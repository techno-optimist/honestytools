#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""aperture-witness — the offline verifier for `aperture.witness/v1` deterministic witnesses.

Sits beside `aperture_verify.py` and reuses it (and `transparency_log.py`) verbatim. A witnessed
receipt carries, inside its ed25519-signed body, a per-answer WITNESS whose fixed rule a stranger
re-fires offline: a registry Merkle-inclusion proof (`grounded_registry`), a re-executed program
under attested isolation (`code_verified`), or a certificate-shaped math re-check (`math_verified`).

    python3 aperture_witness.py verify receipt.json [witness.json]   # gates + cell check -> verdict
    python3 aperture_witness.py hash   witness.json                  # domain-separated FULL-envelope hash
    python3 aperture_witness.py selftest                             # the §F conformance vector suite

    # the shipped demo pack is signed by PUBLISHED (forgeable) demo keys, so it needs an explicit opt-in:
    python3 aperture_witness.py verify --demo \
        demo_witnesses/math_verified_receipt.json demo_witnesses/math_verified_witness.json

`verify` accepts, all OPT-IN — with no flags the released pins are unchanged and an unknown key FAILs:
  --demo · --pin B64[=role] · --registry-pin B64[=role] · --checkpoint FILE
Any verdict earned under a non-production key is stamped `trust_basis` / `production_pinned:false` in the
report and banner-warned on stderr — green under a demo key says so, in the verdict.

WHAT A GREEN PER-CELL PASS MEANS (spec §E) — and what it never means:
  ✓ provenance: the receipt was signed by a pinned Aperture key and not altered by one byte, and the
    presented witness envelope (claim included) is byte-identical to the one signed into it.
  ✓ rule-fired: the fixed rule of the answer's cell was re-derived on THIS machine, under the cell's
    stated semantics and enforcement.
  ✗ it never proves the answer is true. The verdict TOKENS are per-cell on purpose — there is no bare
    `WITNESS_OK`. `REGISTRY_MEMBERSHIP_OK` is the weakest (curation, not truth); `MATH_TIER_S_OK`
    rests on SymPy's simplifier in both directions. See each report's `residual_trust`.

Hard deps: `cryptography` (via aperture_verify). Optional: `sympy` (math cell only; absent ⇒
UNCHECKABLE_HERE, never a false PASS). Code cell needs `witness_sandbox` (pinned interface); absent
or unable to attest isolation ⇒ UNCHECKABLE_HERE (`no_attestable_sandbox`) — never a PASS unsandboxed.
Python ≥ 3.9. No network: the only outbound bytes a conformant verifier produces are none.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import re
import sys

import aperture_verify
from aperture_verify import VerifyError
import transparency_log as tlog

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.exceptions import InvalidSignature
except Exception:                                            # pragma: no cover
    sys.stderr.write("aperture-witness needs `cryptography`:  pip install cryptography\n")
    raise SystemExit(2)

# ── constants ─────────────────────────────────────────────────────────────────────────────────────
WITNESS_SCHEMA = "aperture.witness/v1"
SRR_SCHEMA = "aperture.registry.root/v1"
WITNESS_DOMAIN = b"aperture.witness/v1\x00"
REGISTRY_LEAF_DOMAIN = b"aperture.registry.leaf/v1\x00"
TIMEOUT_CEILING_S = 60
VERSION = "1.0.0"

# Verbatim from spec §E — the mandatory footer, present in EVERY report.
FOOTER = ("provenance + rule, not truth — this proves what was served and that its cell's rule "
          "re-derives offline, not that the answer is correct.")

# Per-cell texts (spec §D.2/§E). `establishes` is scanned for affirmative claims (must not assert
# true/correct/accurate as an established property); `residual_trust` and the footer are exempt (the
# footer's negation is REQUIRED, and residuals speak honestly about limits).
_ESTABLISHES = {
    "grounded_registry": ("cited spans equal a registry fact's field bytes; fact is in the operator's "
                          "published registry at the signed epoch"),
    "code_verified": ("the shipped program, run under attested OS isolation, reproduces the expected "
                      "output byte-exactly; asserted spans equal that output"),
    "math_verified_D": ("the stated identity holds under decision procedures on the verifier's machine"),
    "math_verified_S": ("the stated identity holds under SymPy's simplifier (semi-decision, opt-in)"),
}
_RESIDUAL = {
    "grounded_registry": ("the operator's self-signed registry root — curation is trusted, not proven; "
                          "byte-substring equality survives negation/modality; unwitnessed bytes are unverified"),
    "code_verified": ("a faithful execution of the WRONG program is a valid witness — nothing here proves "
                      "the code formalizes the user's question; on unattestable platforms there is no PASS"),
    "math_verified_D": ("only the formalization gap remains — nothing proves the checked statement is the "
                        "one the user needed"),
    "math_verified_S": ("rests on SymPy's simplifier in BOTH directions — its FAIL is not a disproof and its "
                        "PASS can be false on branch-cut / assumption-dependent identities; caps any composite"),
}

# Verdict tokens and their composition rank (spec §D.1: 0 FAIL < 1 UNCHECKABLE_HERE <
# 2 PROVENANCE_ONLY < 3 CELL_PASS, with MATH_TIER_S_OK capped at a sub-tier below the other PASS tokens).
FAIL = "FAIL"
UNCHECKABLE_HERE = "UNCHECKABLE_HERE"
PROVENANCE_ONLY = "PROVENANCE_ONLY"
REGISTRY_MEMBERSHIP_OK = "REGISTRY_MEMBERSHIP_OK"
CODE_EXEC_OK = "CODE_EXEC_OK"
MATH_TIER_D_OK = "MATH_TIER_D_OK"
MATH_TIER_S_OK = "MATH_TIER_S_OK"
_RANK = {FAIL: 0, UNCHECKABLE_HERE: 1, PROVENANCE_ONLY: 2, MATH_TIER_S_OK: 2.5,
         REGISTRY_MEMBERSHIP_OK: 3, CODE_EXEC_OK: 3, MATH_TIER_D_OK: 3}

# NAME tokens the math token-gate admits, besides single-letter symbols (spec §A.3).
_MATH_CALLABLES = {"Integer", "Rational", "Symbol", "Add", "Mul", "Pow", "binomial", "factorial",
                   "gcd", "isprime", "expand", "together", "diff"}

# Static AST gate for the code cell (spec §A.2) — a conformance filter on witness SHAPE, honestly
# labeled NOT a security boundary (the OS sandbox is). Import allowlist / banned-name list:
_CODE_IMPORT_ALLOW = {"math", "fractions", "decimal", "itertools", "functools", "collections",
                      "heapq", "bisect", "sys", "re", "json"}
_CODE_BANNED_NAMES = {"open", "__import__", "eval", "exec", "compile", "socket", "os", "subprocess",
                      "time", "random", "ctypes", "getattr", "setattr", "delattr",
                      "globals", "locals", "vars", "breakpoint"}


# ── module-level default pins (spec §C, §D.3) ──────────────────────────────────────────────────────
class Pins:
    """Two DISJOINT pin sets (spec §C key-role separation): one for receipt-signing keys, one for the
    registry-root key. A key may never live in both — an SRR signed by a receipt key is a forgery, and
    vice versa. `receipt`/`registry` map base64 pubkey -> role string."""

    def __init__(self, receipt=None, registry=None):
        receipt = dict(receipt or {})
        registry = dict(registry or {})
        overlap = set(receipt) & set(registry)
        if overlap:
            raise ValueError("receipt and registry key sets MUST be disjoint (crossover: %s)"
                             % ", ".join(sorted(overlap)))
        self.receipt = receipt
        self.registry = registry


# Released-verifier default: receipt keys pinned; NO registry-root key pinned. Per spec §D.3 release
# gate (4), `grounded_registry` MUST NOT be pinned into a released verifier until a distinct
# registry-root key AND an external SRR co-signature exist — so by default any grounded_registry
# witness FAILs (its SRR key is unpinned). selftest supplies its own throwaway registry key.
DEFAULT_PINS = Pins(receipt=aperture_verify.PINNED_KEYS, registry={})

# ── DEMO pins (opt-in ONLY, via `verify --demo`) ────────────────────────────────────────────────────
# The pack in demo_witnesses/ is signed by keys whose PRIVATE SEEDS ARE PUBLISHED IN
# `witness_emitter.py` (DEMO_*_SEED_LABEL). Anyone can mint a receipt under them, which is precisely
# why they are safe to ship and useless as evidence: they demonstrate that the MECHANISM re-derives
# offline, and establish nothing about who served the answer.
#
# These keys are NOT in DEFAULT_PINS and NOT in aperture_verify.PINNED_KEYS. A stranger must ask for
# them by name (`--demo`); the default stays fail-closed, and every report produced under them is
# stamped `trust_basis: DEMO ...` so a green verdict says out loud that it is a demo key.
DEMO_RECEIPT_KEY = "t7HXH8vN4YXPL5ZMtBVU62PSup2b3sY54hZcSTSUoiQ="
DEMO_REGISTRY_KEY = "kV1vJ9LRSpppJeUjwjDenhv9Cha43WPQUNAjNOs5IcI="
DEMO_RECEIPT_ROLE = "DEMO receipt key — PUBLISHED private seed, forgeable by anyone; NOT production"
DEMO_REGISTRY_ROLE = "DEMO registry-root key — PUBLISHED private seed, forgeable by anyone; NOT production"
DEMO_PINS = Pins(receipt={DEMO_RECEIPT_KEY: DEMO_RECEIPT_ROLE},
                 registry={DEMO_REGISTRY_KEY: DEMO_REGISTRY_ROLE})
# The checkpoint the demo registry epoch extends (spec §A.1.3) — over mock_registry.json, prefix
# size n-2. Pinned here exactly as REGISTRY_BUILD_SPEC.md §5 requires of a real epoch; `emit-demo`
# hard-stops if it drifts from what the emitter builds.
DEMO_CHECKPOINT = {"epoch": "2026-07-11T00:00:00Z", "tree_size": 10,
                   "root_sha256": "4c92678d6fdd572bfe6ced1f59ec62fb224dcc09126c343f23a17faf1a809a87"}

# A pin role naming a non-production key marks its whole report. Roles are author-written strings, so
# match on the shipped constants and on the explicit `--pin` marker rather than sniffing for a prefix.
_NONPROD_TRUST = {
    DEMO_RECEIPT_ROLE: ("DEMO — the signing key's private seed is published in witness_emitter.py; "
                        "anyone can mint this receipt. The cell rule re-derived here, but provenance "
                        "is NOT established."),
    DEMO_REGISTRY_ROLE: ("DEMO — the registry-root key's private seed is published in "
                         "witness_emitter.py; the registry head is self-signed by a forgeable key."),
}
_CLI_PIN_ROLE = "operator-supplied via --pin (not a key shipped with this verifier)"
_CLI_REGISTRY_PIN_ROLE = "operator-supplied via --registry-pin (not a key shipped with this verifier)"
_PRODUCTION_TRUST = "pinned Aperture registry key (aperture_verify.PINNED_KEYS)"


# ── canonical bytes + witness hash (spec §A.0) ─────────────────────────────────────────────────────
def _canon(obj) -> bytes:
    """Canonical JSON, identical recipe to aperture_verify.canonical_body (no field stripping here)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def witness_hash(envelope) -> str:
    """Domain-separated sha256 over the ENTIRE witness envelope (schema, cell, answer_sha256,
    emitted_at, emitted_with, claim AND witness) — the value bound into the receipt (spec §A.0).
    Hashing the full envelope signs `claim.asserted_values`, closing the rev-B soundness hole."""
    return hashlib.sha256(WITNESS_DOMAIN + _canon(envelope)).hexdigest()


def _module_sha256() -> str:
    try:
        with open(os.path.abspath(__file__), "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except Exception:                                        # pragma: no cover
        return "unavailable"


def _sympy_version():
    try:
        import sympy
        return sympy.__version__
    except Exception:
        return None


# ── span helpers (spec §A.0 span semantics: utf-8 BYTE offsets into raw pre-normalization bytes) ────
class _SpanError(Exception):
    pass


def _check_span(data: bytes, span) -> bytes:
    """Validate [a,b] as an in-range, byte-boundary utf-8 slice of `data`; return the slice bytes.
    Out-of-range, a>=b, or a non-utf-8-boundary span raises _SpanError (⇒ FAIL upstream)."""
    if (not isinstance(span, (list, tuple)) or len(span) != 2
            or not all(isinstance(x, int) for x in span)):
        raise _SpanError("span must be [int,int]")
    a, b = span
    if not (0 <= a < b <= len(data)):
        raise _SpanError("span %r out of range for %d bytes" % (list(span), len(data)))
    sl = data[a:b]
    try:
        sl.decode("utf-8")                                  # enforce byte-boundary
    except UnicodeDecodeError:
        raise _SpanError("span %r is not on a utf-8 char boundary" % list(span))
    return sl


def _no_overlap(spans) -> bool:
    ordered = sorted(spans, key=lambda s: s[0])
    for i in range(1, len(ordered)):
        if ordered[i][0] < ordered[i - 1][1]:
            return False
    return True


def _complement(total: int, spans):
    """The unwitnessed byte ranges: [0,total] minus the union of `spans` (spec §A.1.2)."""
    if not spans:
        return [[0, total]] if total > 0 else []
    merged = []
    for a, b in sorted(spans):
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    out = []
    cur = 0
    for a, b in merged:
        if a > cur:
            out.append([cur, a])
        cur = max(cur, b)
    if cur < total:
        out.append([cur, total])
    return out


def _norm_str(s: str, norm: str):
    if norm == "casefold":
        import unicodedata
        return unicodedata.normalize("NFC", s).casefold()
    return s


# ── cellresult constructor ─────────────────────────────────────────────────────────────────────────
def _cell(status, reasons=None, **extras):
    r = {"status": status, "reasons": list(reasons or [])}
    r.update(extras)
    return r


# ── grounded_registry (spec §A.1) ──────────────────────────────────────────────────────────────────
def _verify_srr_signature(srr, registry_pins):
    """Verify the Signed Registry Root under a pinned REGISTRY-ROOT key (disjoint from receipt keys).
    Returns (ok: bool, reason: str|None)."""
    if not isinstance(srr, dict):
        return False, "srr_not_object"
    if srr.get("schema") != SRR_SCHEMA:
        return False, "srr_bad_schema"
    if srr.get("trust") != "operator_self_signed":
        return False, "srr_missing_trust_marker"
    sig = srr.get("signature")
    if not isinstance(sig, dict) or "sig" not in sig or "pub" not in sig:
        return False, "srr_no_signature"
    if sig.get("alg", "ed25519") != "ed25519":
        return False, "srr_bad_alg"
    pub_b64 = sig["pub"]
    if pub_b64 not in registry_pins:
        # includes the key-role-crossover case: a receipt key is not in the registry pin set.
        return False, "registry_key_not_pinned"
    body = {k: v for k, v in srr.items() if k != "signature"}
    canon = _canon(body)
    try:
        pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(pub_b64))
        pub.verify(base64.b64decode(sig["sig"]), canon)
    except InvalidSignature:
        return False, "srr_signature_invalid"
    except Exception as e:
        return False, "srr_malformed:%s" % e
    return True, None


def check_registry(witness, answer_bytes, pinned, checkpoint):
    """§A.1: SRR signature (disjoint registry key) + pinned-checkpoint consistency + Merkle inclusion
    + token trace (byte spans, exact/casefold/numeric norms, unit rules) + unwitnessed-byte accounting.
    Establishes membership at the signed epoch — NOT truth. Returns a cellresult."""
    reasons = []
    wit = witness.get("witness") or {}
    claim = witness.get("claim") or {}

    srr = wit.get("registry_root")
    ok, why = _verify_srr_signature(srr, pinned.registry if isinstance(pinned, Pins) else (pinned or {}))
    if not ok:
        return _cell(FAIL, [why])

    # (b) pinned checkpoint + consistency proof — anti-equivocation / anti-rollback (§A.1.3).
    if not checkpoint:
        # Release gate §D.3(4): registry cell requires a pinned checkpoint; without one it is not
        # verifiable here. Fail closed with the named reason.
        return _cell(FAIL, ["registry_equivocation_or_rollback", "no_pinned_checkpoint"])
    try:
        srr_size = int(srr["tree_size"])
        srr_root = bytes.fromhex(srr["root_sha256"])
        cp_size = int(checkpoint["tree_size"])
        cp_root = bytes.fromhex(checkpoint["root_sha256"])
    except Exception as e:
        return _cell(FAIL, ["registry_equivocation_or_rollback", "malformed_root_or_checkpoint:%s" % e])
    if srr_size < cp_size:
        return _cell(FAIL, ["registry_equivocation_or_rollback", "rollback: srr_size < checkpoint_size"])
    if srr_size == cp_size:
        if srr_root != cp_root:
            return _cell(FAIL, ["registry_equivocation_or_rollback", "equal_size_root_divergence"])
    else:
        try:
            cpath = [bytes.fromhex(h) for h in wit.get("consistency_path", [])]
            if not tlog.verify_consistency(cp_size, srr_size, cpath, cp_root, srr_root):
                return _cell(FAIL, ["registry_equivocation_or_rollback", "consistency_proof_failed"])
        except Exception as e:
            return _cell(FAIL, ["registry_equivocation_or_rollback", "consistency_error:%s" % e])

    # (c) leaf hash + inclusion under the SRR root/size.
    fact = wit.get("fact")
    if not isinstance(fact, dict):
        return _cell(FAIL, ["fact_not_object"])
    try:
        leaf_index = int(wit["leaf_index"])
        wit_tree_size = int(wit["tree_size"])
    except Exception as e:
        return _cell(FAIL, ["malformed_leaf_fields:%s" % e])
    if wit_tree_size != srr_size:
        return _cell(FAIL, ["tree_size_mismatch: witness %d vs srr %d" % (wit_tree_size, srr_size)])
    leaf_data = hashlib.sha256(REGISTRY_LEAF_DOMAIN + _canon(fact)).digest()
    try:
        audit_path = [bytes.fromhex(h) for h in wit.get("audit_path", [])]
    except Exception as e:
        return _cell(FAIL, ["malformed_audit_path:%s" % e])
    if not tlog.verify_inclusion(leaf_data, leaf_index, srr_size, audit_path, srr_root):
        return _cell(FAIL, ["fact_not_in_registry"])

    # (d) token trace: answer -> fact, byte spans + norms + unit rules.
    trace = wit.get("token_trace", [])
    asserted = claim.get("asserted_values", [])
    if not isinstance(trace, list) or not isinstance(asserted, list):
        return _cell(FAIL, ["malformed_trace_or_assertions"])
    fact_unit = fact.get("unit", None)
    answer_spans = []
    unit_bound = False
    numeric_used_on_value = False
    for entry in trace:
        try:
            aspan = entry["answer_span"]
            field = entry["fact_field"]
            fspan = entry["fact_span"]
            norm = entry["norm"]
        except Exception:
            return _cell(FAIL, ["trace_entry_missing_fields"])
        if norm not in ("exact", "casefold", "numeric"):
            return _cell(FAIL, ["unknown_norm:%r" % norm])
        if field not in fact:
            return _cell(FAIL, ["trace_field_absent:%r" % field])
        fval = fact[field]
        if not isinstance(fval, str):
            # Traceable fields MUST be JSON strings (§A.0) — slicing a JSON number is undefined.
            return _cell(FAIL, ["trace_field_not_string:%r" % field])
        try:
            aslice = _check_span(answer_bytes, aspan)
            fslice = _check_span(fval.encode("utf-8"), fspan)
        except _SpanError as e:
            return _cell(FAIL, ["bad_span:%s" % e])
        answer_spans.append([aspan[0], aspan[1]])
        if norm == "exact":
            if aslice != fslice:
                return _cell(FAIL, ["exact_mismatch on field %r" % field])
        elif norm == "casefold":
            if _norm_str(aslice.decode("utf-8"), "casefold") != _norm_str(fslice.decode("utf-8"), "casefold"):
                return _cell(FAIL, ["casefold_mismatch on field %r" % field])
            if field == "unit":
                unit_bound = True
        elif norm == "numeric":
            # numeric admissible only against unit-free fields, OR paired with a unit binding (checked below).
            import decimal
            try:
                if decimal.Decimal(aslice.decode("utf-8")) != decimal.Decimal(fslice.decode("utf-8")):
                    return _cell(FAIL, ["numeric_mismatch on field %r" % field])
            except (decimal.InvalidOperation, ValueError):
                return _cell(FAIL, ["numeric_unparseable on field %r" % field])
            if field != "unit":
                numeric_used_on_value = True

    # Units (§A.1.2): a numeric trace on the VALUE of a unit-bearing fact MUST be accompanied by a
    # casefold trace binding the answer's unit token to fact["unit"]. Numeric-only ⇒ FAIL.
    if numeric_used_on_value and fact_unit not in (None, ""):
        if not unit_bound:
            return _cell(FAIL, ["unit_binding_required",
                                "numeric trace on unit-bearing fact without unit binding"])

    # Every asserted value MUST have a matching trace entry (same answer_span + field).
    trace_keys = {(tuple(e["answer_span"]), e["fact_field"]) for e in trace}
    for av in asserted:
        key = (tuple(av.get("answer_span", [])), av.get("fact_field"))
        if key not in trace_keys:
            return _cell(FAIL, ["asserted_value_without_trace: %r" % list(key)])

    if not _no_overlap(answer_spans):
        return _cell(FAIL, ["overlapping_answer_spans"])

    unwitnessed = _complement(len(answer_bytes), answer_spans)
    n_unwitnessed = sum(b - a for a, b in unwitnessed)
    return _cell(REGISTRY_MEMBERSHIP_OK, [],
                 unwitnessed_byte_ranges=unwitnessed, n_unwitnessed_bytes=n_unwitnessed,
                 registry_trust="operator_self_signed")


# ── code_verified (spec §A.2) ──────────────────────────────────────────────────────────────────────
def _static_gate(code: str):
    """AST pass: reject imports outside the allowlist and banned names/calls. Returns (ok, reason).
    Honestly labeled: a conformance filter on witness shape, NOT the security boundary."""
    import ast
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as e:
        return False, "syntax_error:%s" % e
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in _CODE_IMPORT_ALLOW:
                    return False, "import_not_allowed:%s" % alias.name
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root not in _CODE_IMPORT_ALLOW:
                return False, "import_not_allowed:%s" % node.module
        elif isinstance(node, ast.Name):
            if node.id in _CODE_BANNED_NAMES:
                return False, "banned_name:%s" % node.id
            if node.id.startswith("__") and node.id.endswith("__"):
                return False, "dunder_name:%s" % node.id   # e.g. __builtins__ reflection bypass
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__") and node.attr.endswith("__"):
                return False, "dunder_attribute:%s" % node.attr
    return True, None


def _canon_stdout(b: bytes) -> bytes:
    """Line endings -> \\n, strip ONE trailing newline (spec §A.2 output canon). No float tolerance."""
    b = b.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    if b.endswith(b"\n"):
        b = b[:-1]
    return b


def check_code(witness, answer_bytes):
    """§A.2: timeout ceiling, static AST gate, then re-execute under OS-attested isolation via the
    pinned `witness_sandbox.run` interface (lazy import). No attestable sandbox ⇒ UNCHECKABLE_HERE
    (`no_attestable_sandbox`) — NEVER a PASS from an unsandboxed run. Fail safe, not fail open."""
    wit = witness.get("witness") or {}
    claim = witness.get("claim") or {}

    lang = wit.get("language", "python")
    if lang != "python":
        return _cell(UNCHECKABLE_HERE, ["unsupported_language:%r" % lang])

    try:
        timeout_s = int(wit["timeout_s"])
    except Exception:
        return _cell(FAIL, ["missing_timeout_s"])
    if timeout_s > TIMEOUT_CEILING_S:
        return _cell(FAIL, ["timeout_exceeds_ceiling: %d > %d" % (timeout_s, TIMEOUT_CEILING_S)])
    if timeout_s <= 0:
        return _cell(FAIL, ["nonpositive_timeout"])

    code = wit.get("code")
    if not isinstance(code, str):
        return _cell(FAIL, ["missing_code"])

    ok, why = _static_gate(code)
    if not ok:
        # Defense-in-depth gate fires first with a named reason (network-fetch / nondeterminism vectors).
        return _cell(FAIL, ["static_gate:%s" % why])

    # Lazy import of the pinned sandbox interface. Absent module OR SandboxUnavailable ⇒ no attestable
    # isolation ⇒ UNCHECKABLE_HERE. We MUST NOT run witness code without attested isolation.
    try:
        import witness_sandbox
    except Exception:
        return _cell(UNCHECKABLE_HERE, ["no_attestable_sandbox", "witness_sandbox module not importable"],
                     sandbox=None)

    stdin = wit.get("stdin", "")
    try:
        result = witness_sandbox.run(code, stdin, timeout_s)
    except getattr(witness_sandbox, "SandboxUnavailable", Exception) as e:
        # Distinguish SandboxUnavailable (⇒ UNCHECKABLE_HERE) from SandboxTimeout (⇒ FAIL) below.
        if type(e).__name__ == "SandboxTimeout":
            return _cell(FAIL, ["timeout"])
        return _cell(UNCHECKABLE_HERE, ["no_attestable_sandbox", str(e)], sandbox=None)
    except Exception as e:
        if type(e).__name__ == "SandboxTimeout":
            return _cell(FAIL, ["timeout"])
        if type(e).__name__ == "SandboxUnavailable":
            return _cell(UNCHECKABLE_HERE, ["no_attestable_sandbox", str(e)], sandbox=None)
        return _cell(FAIL, ["sandbox_error:%s" % e])

    sandbox_mode = result.get("sandbox")
    if sandbox_mode not in ("linux-netns", "darwin-sandbox-exec", "docker"):
        # An un-attested run must never mint a PASS.
        return _cell(UNCHECKABLE_HERE, ["no_attestable_sandbox", "unrecognized sandbox attestation %r" % sandbox_mode],
                     sandbox=sandbox_mode)

    got_stdout = _canon_stdout(result.get("stdout", b""))
    exp_stdout = _canon_stdout(wit.get("expected_stdout", "").encode("utf-8")
                               if isinstance(wit.get("expected_stdout"), str) else wit.get("expected_stdout", b""))
    got_exit = int(result.get("exit_code", -1))
    exp_exit = int(wit.get("expected_exit", 0))

    if got_exit != exp_exit:
        return _cell(FAIL, ["exit_code_mismatch: got %d expected %d" % (got_exit, exp_exit)], sandbox=sandbox_mode)
    if got_stdout != exp_stdout:
        # Cross-version divergence is an availability risk (fail closed) — name both interpreters.
        emitter_interp = (witness.get("emitted_with") or {}).get("interpreter", "unknown")
        verifier_interp = "cpython-%s" % platform.python_version()
        return _cell(FAIL, ["stdout_mismatch",
                            "emitter=%s verifier=%s" % (emitter_interp, verifier_interp)], sandbox=sandbox_mode)

    # Span bindings: answer[a:b] must equal canon stdout[c:d].
    for av in claim.get("asserted_values", []):
        if av.get("binds_to") != "stdout_span":
            return _cell(FAIL, ["unknown_binding:%r" % av.get("binds_to")], sandbox=sandbox_mode)
        try:
            aslice = _check_span(answer_bytes, av["answer_span"])
            sslice = _check_span(got_stdout, av["span"])
        except _SpanError as e:
            return _cell(FAIL, ["bad_span:%s" % e], sandbox=sandbox_mode)
        if aslice != sslice:
            return _cell(FAIL, ["stdout_span_mismatch"], sandbox=sandbox_mode)

    return _cell(CODE_EXEC_OK, [], sandbox=sandbox_mode)


# ── math_verified (spec §A.3) ──────────────────────────────────────────────────────────────────────
class _MathTokenError(Exception):
    pass


def _math_token_gate(s: str):
    """Pre-parse token gate on the RAW expression string (spec §A.3) — the boundary is HERE, before
    parse_expr (which is eval-based internally). Charset + NAME whitelist + length cap + no attribute
    access / dunders. Raises _MathTokenError (⇒ FAIL) on any violation."""
    if not isinstance(s, str):
        raise _MathTokenError("not_a_string")
    if len(s) > 4096:
        raise _MathTokenError("length_cap")
    if "__" in s:
        raise _MathTokenError("dunder")
    if not re.fullmatch(r"[0-9A-Za-z_+\-*/^(),. ]*", s or ""):
        raise _MathTokenError("charset")
    # attribute access: a letter/underscore adjacent to '.' (a decimal point sits between digits only).
    if re.search(r"[A-Za-z_]\s*\.", s) or re.search(r"\.\s*[A-Za-z_]", s):
        raise _MathTokenError("attribute_access")
    for m in re.finditer(r"[A-Za-z_][A-Za-z_0-9]*", s):
        name = m.group(0)
        if len(name) == 1 and name.isalpha():
            continue
        if name in _MATH_CALLABLES:
            continue
        raise _MathTokenError("name_not_whitelisted:%s" % name)


def _math_parse(s: str):
    """Token-gate THEN parse. The gate, not parse_expr, is the boundary."""
    _math_token_gate(s)                                     # fires before parse_expr — asserted in §F
    import sympy
    from sympy.parsing.sympy_parser import (parse_expr, standard_transformations, convert_xor)
    transformations = standard_transformations + (convert_xor,)
    local = {name: getattr(sympy, name) for name in _MATH_CALLABLES}
    return parse_expr(s, local_dict=local, global_dict={}, transformations=transformations, evaluate=True)


def _is_zero_exact(expr) -> bool:
    """Decision-procedure zero test for Tier D: expand then compare to the literal zero object. No
    simplify() heuristics — conservative by design (a symbolic residue that only simplify() kills is
    NOT accepted here)."""
    import sympy
    e = sympy.expand(expr)
    try:
        return bool(e == 0)
    except Exception:
        return False


def check_math(witness, answer_bytes):
    """§A.3: token-gate every parsed expression, execute the certificate steps in a restricted
    vocabulary. Tier D = genuine decision procedures (rational arithmetic, polynomial identity via
    expand, root/factor checks, verify_antiderivative, verify_sum_closed_form). Tier S = simplify/doit
    (semi-decision, honestly weaker; distinct token, caps composites). SymPy absent ⇒ UNCHECKABLE_HERE."""
    try:
        import sympy  # noqa: F401
    except Exception:
        return _cell(UNCHECKABLE_HERE, ["sympy_absent"])

    wit = witness.get("witness") or {}
    claim = witness.get("claim") or {}
    steps = wit.get("steps", [])
    if not isinstance(steps, list) or not steps:
        return _cell(FAIL, ["no_steps"])

    tier = "D"
    try:
        for step in steps:
            op = step.get("op")
            if op == "rational_arith":
                lhs = _math_parse(step["expr"])
                rhs = _math_parse(step["expect"])
                if not _is_zero_exact(lhs - rhs):
                    return _cell(FAIL, ["rational_arith_failed"])
            elif op == "expand_identity":
                lhs = _math_parse(step["lhs"])
                rhs = _math_parse(step["rhs"])
                diff = lhs - rhs
                if step.get("rational"):
                    diff = sympy.together(diff)
                    num = sympy.fraction(diff)[0]
                    if not _is_zero_exact(num):
                        return _cell(FAIL, ["expand_identity_failed(rational)"])
                elif not _is_zero_exact(diff):
                    return _cell(FAIL, ["expand_identity_failed"])
            elif op == "substitution_root":
                expr = _math_parse(step["expr"])
                var = sympy.Symbol(step["var"])
                val = _math_parse(step["value"])
                expect = _math_parse(step.get("expect", "0"))
                if not _is_zero_exact(expr.subs(var, val) - expect):
                    return _cell(FAIL, ["substitution_root_failed"])
            elif op == "factor_check":
                prod = sympy.Integer(1)
                for f in step["factors"]:
                    prod = prod * _math_parse(f)
                product = _math_parse(step["product"])
                if not _is_zero_exact(prod - product):
                    return _cell(FAIL, ["factor_check_failed"])
            elif op == "verify_antiderivative":
                F = _math_parse(step["F"])
                integrand = _math_parse(step["integrand"])
                var = sympy.Symbol(step["var"])
                # mechanical differentiation check (algorithmic) — the emitter did the search
                if not _is_zero_exact(sympy.diff(F, var) - integrand):
                    return _cell(FAIL, ["antiderivative_diff_mismatch"])
                a = _math_parse(step["bounds"][0])
                b = _math_parse(step["bounds"][1])
                expect = _math_parse(step["expect"])
                if not _is_zero_exact((F.subs(var, b) - F.subs(var, a)) - expect):
                    return _cell(FAIL, ["antiderivative_eval_mismatch"])
            elif op == "verify_sum_closed_form":
                S = _math_parse(step["S"])
                term = _math_parse(step["term"])
                n = sympy.Symbol(step["var"])
                if not _is_zero_exact(sympy.expand(S - S.subs(n, n - 1) - term)):
                    return _cell(FAIL, ["sum_telescoping_failed"])
                base = step.get("base")
                if base:
                    n0 = _math_parse(base["at"])
                    val = _math_parse(base["value"])
                    if not _is_zero_exact(S.subs(n, n0) - val):
                        return _cell(FAIL, ["sum_base_case_failed"])
            # ── Tier S (opt-in, honestly weaker) ──
            elif op == "simplify_zero":
                tier = "S"
                lhs = _math_parse(step["lhs"])
                rhs = _math_parse(step["rhs"])
                if sympy.simplify(lhs - rhs) != 0:
                    return _cell(FAIL, ["simplify_zero_failed"])
            elif op == "doit":
                tier = "S"
                expr = _math_parse(step["expr"])
                expect = _math_parse(step["expect"])
                if sympy.simplify(expr.doit() - expect) != 0:
                    return _cell(FAIL, ["doit_failed"])
            else:
                return _cell(FAIL, ["unknown_op:%r" % op])
    except _MathTokenError as e:
        return _cell(FAIL, ["token_gate:%s" % e])
    except (KeyError, IndexError) as e:
        return _cell(FAIL, ["malformed_step:%s" % e])
    except Exception as e:
        return _cell(FAIL, ["math_error:%s" % e])

    # asserted_values: bind the answer text to the verified value (Tier-D exact equality).
    for av in claim.get("asserted_values", []):
        try:
            aslice = _check_span(answer_bytes, av["answer_span"]).decode("utf-8")
            aexpr = _math_parse(aslice)
            target = _math_parse(av["expr"])
        except _SpanError as e:
            return _cell(FAIL, ["bad_span:%s" % e])
        except _MathTokenError as e:
            return _cell(FAIL, ["token_gate:%s" % e])
        except Exception as e:
            return _cell(FAIL, ["assertion_parse_error:%s" % e])
        if not _is_zero_exact(aexpr - target):
            return _cell(FAIL, ["asserted_value_mismatch"])

    token = MATH_TIER_D_OK if tier == "D" else MATH_TIER_S_OK
    return _cell(token, [], tier_label=tier)


# ── the top-level verifier (spec §B.2) ─────────────────────────────────────────────────────────────
def _report(verdict, cell, cellres=None, reasons=None, sympy_ver=None):
    cellres = cellres or {}
    reasons = list(reasons or [])
    reasons += cellres.get("reasons", [])
    # establishes / residual_trust keying (math splits D vs S).
    if cell == "math_verified":
        key = "math_verified_D" if verdict == MATH_TIER_D_OK else "math_verified_S"
    else:
        key = cell
    establishes = _ESTABLISHES.get(key) if verdict in (
        REGISTRY_MEMBERSHIP_OK, CODE_EXEC_OK, MATH_TIER_D_OK, MATH_TIER_S_OK) else None
    residual = _RESIDUAL.get(key) if verdict in (
        REGISTRY_MEMBERSHIP_OK, CODE_EXEC_OK, MATH_TIER_D_OK, MATH_TIER_S_OK) else (
        "provenance held; the cell rule was NOT re-derived here" if verdict == PROVENANCE_ONLY else
        "no property established")
    checker_id = "aperture_witness.check_%s" % (
        "registry" if cell == "grounded_registry" else "code" if cell == "code_verified" else "math"
        if cell == "math_verified" else "gate")
    rep = {
        "ok": verdict in (REGISTRY_MEMBERSHIP_OK, CODE_EXEC_OK, MATH_TIER_D_OK, MATH_TIER_S_OK),
        "verdict": verdict,
        "tier": {FAIL: 0, UNCHECKABLE_HERE: 1, PROVENANCE_ONLY: 2}.get(verdict, 3),
        "cell": cell,
        "establishes": establishes,
        "residual_trust": residual,
        "unwitnessed_byte_ranges": cellres.get("unwitnessed_byte_ranges"),
        "n_unwitnessed_bytes": cellres.get("n_unwitnessed_bytes"),
        "reasons": reasons,
        "witness_sha256": None,
        "sandbox": cellres.get("sandbox"),
        "versions": {"python": platform.python_version(), "sympy": sympy_ver},
        "checker": {"id": checker_id, "version": VERSION, "selftest_sha256": _module_sha256()},
        "footer": FOOTER,
    }
    if verdict == MATH_TIER_S_OK:
        rep["tier_s"] = True
    return rep


def _stamp_trust(rep, sigrep, pinned):
    """Record WHICH pin basis accepted the receipt key, so a green verdict can never hide that it was
    earned under a demo or operator-supplied pin rather than a shipped production key. Sets
    `trust_basis`; for anything non-production also sets `production_pinned: False` and prepends a
    reason token — the fields the CLI banner and any integrator read."""
    role = (sigrep or {}).get("key_role") or ""
    key = (sigrep or {}).get("key")
    rep["signing_key"] = key
    rep["signing_key_role"] = role or None
    if key in aperture_verify.PINNED_KEYS:
        rep["trust_basis"] = _PRODUCTION_TRUST
        rep["production_pinned"] = True
        return rep
    rep["production_pinned"] = False
    rep["trust_basis"] = _NONPROD_TRUST.get(role) or (
        "NOT a production pin — this key was accepted only because it was supplied to this run "
        "(role: %s). The cell rule re-derived here; provenance is NOT established." % (role or "unnamed"))
    # On a GREEN verdict the caveat also rides in `reasons`, where a reviewer actually looks — appended,
    # not prepended, so the FAIL-path `reasons[0]` cause vocabulary keeps its meaning.
    if rep.get("ok"):
        marker = "demo_key" if role in _NONPROD_TRUST else "non_production_key"
        rep["reasons"] = list(rep.get("reasons") or []) + ["%s:%s" % (marker, rep["trust_basis"])]
    # A registry cell can also be riding a non-production SRR key; name it separately so the two
    # trust holes are not collapsed into one line.
    reg_roles = [r for r in (pinned.registry if isinstance(pinned, Pins) else {}).values()
                 if r in _NONPROD_TRUST or r == _CLI_REGISTRY_PIN_ROLE]
    if reg_roles and rep.get("cell") == "grounded_registry":
        rep["registry_key_basis"] = _NONPROD_TRUST.get(reg_roles[0], reg_roles[0])
    return rep


def verify_witness(receipt, witness=None, *, pinned=None, checkpoint=None):
    """Verify a witnessed receipt end-to-end (spec §B.2). Steps 1-2 are boolean GATES (fail ⇒ hard
    FAIL); step 3 is the per-cell check. Pure and offline. Returns a report dict (§D.2).

    `pinned` is a Pins (receipt + disjoint registry key sets); a plain dict is treated as receipt keys.
    `checkpoint` is the pinned SRR checkpoint {epoch, tree_size, root_sha256} for the registry cell."""
    if pinned is None:
        pinned = DEFAULT_PINS
    elif isinstance(pinned, dict):
        pinned = Pins(receipt=pinned, registry={})
    sympy_ver = _sympy_version()

    if not isinstance(receipt, dict):
        return _report(FAIL, "unknown", reasons=["receipt_not_object"], sympy_ver=sympy_ver)

    wblock = receipt.get("witness")
    if not isinstance(wblock, dict) and witness is None:
        # No witness block: plain aperture_verify outcome — this spec adds nothing.
        try:
            sigrep = aperture_verify.verify_receipt(receipt, pinned=pinned.receipt)
            rep = _report(PROVENANCE_ONLY, "none", reasons=["no_witness_block"], sympy_ver=sympy_ver)
            rep["note"] = "unwitnessed receipt: signature verified, no cell rule to re-derive"
            return _stamp_trust(rep, sigrep, pinned)
        except VerifyError as e:
            return _report(FAIL, "none", reasons=["receipt_signature:%s" % e], sympy_ver=sympy_ver)

    # ── Step 1: receipt provenance gate ──
    try:
        sigrep = aperture_verify.verify_receipt(receipt, pinned=pinned.receipt)
    except VerifyError as e:
        return _report(FAIL, "unknown", reasons=["receipt_signature:%s" % e], sympy_ver=sympy_ver)

    # From here on the receipt key was ACCEPTED, so every exit stamps which key basis accepted it.
    def fin(rep):
        return _stamp_trust(rep, sigrep, pinned)

    # Resolve the witness envelope (inline or supplied) and the cell.
    env = witness if witness is not None else (wblock or {}).get("inline")
    if not isinstance(env, dict):
        return fin(_report(FAIL, "unknown", reasons=["no_witness_envelope"], sympy_ver=sympy_ver))
    cell = env.get("cell")

    # ── Step 2: binding gate ──
    signed_wsha = (wblock or {}).get("witness_sha256")
    presented_wsha = witness_hash(env)
    if signed_wsha != presented_wsha:
        # Kills mix-and-match / lying-mirror / any one-byte tamper in the envelope (claim included).
        rep = _report(FAIL, cell or "unknown", reasons=["witness_binding_mismatch"], sympy_ver=sympy_ver)
        rep["witness_sha256"] = presented_wsha
        return fin(rep)
    if "answer" not in receipt:
        rep = _report(FAIL, cell or "unknown", reasons=["missing_answer_field"], sympy_ver=sympy_ver)
        rep["witness_sha256"] = presented_wsha
        return fin(rep)
    answer_bytes = receipt["answer"].encode("utf-8")
    if hashlib.sha256(answer_bytes).hexdigest() != env.get("answer_sha256"):
        rep = _report(FAIL, cell or "unknown", reasons=["answer_hash_mismatch"], sympy_ver=sympy_ver)
        rep["witness_sha256"] = presented_wsha
        return fin(rep)

    # ── Step 3: cell check ──
    if cell == "grounded_registry":
        cellres = check_registry(env, answer_bytes, pinned, checkpoint)
    elif cell == "code_verified":
        cellres = check_code(env, answer_bytes)
    elif cell == "math_verified":
        cellres = check_math(env, answer_bytes)
    else:
        rep = _report(FAIL, cell or "unknown", reasons=["unknown_cell:%r" % cell], sympy_ver=sympy_ver)
        rep["witness_sha256"] = presented_wsha
        return fin(rep)

    status = cellres["status"]
    if status in (REGISTRY_MEMBERSHIP_OK, CODE_EXEC_OK, MATH_TIER_D_OK, MATH_TIER_S_OK):
        verdict = status
    elif status == UNCHECKABLE_HERE:
        verdict = PROVENANCE_ONLY
    else:
        verdict = FAIL
    rep = _report(verdict, cell, cellres=cellres, sympy_ver=sympy_ver)
    rep["witness_sha256"] = presented_wsha
    if "registry_trust" in cellres:
        rep["registry_trust"] = cellres["registry_trust"]   # surface the operator_self_signed marker (§F)
    if status == UNCHECKABLE_HERE:
        rep["cell_status"] = UNCHECKABLE_HERE           # surfaced so composition can propagate it
    return fin(rep)


# ── composition (spec §D.1) ────────────────────────────────────────────────────────────────────────
def compose_reports(reports):
    """Composite over a multi-receipt trail / multi-cell composite. Headline = MEET (minimum tier);
    the full list of per-component tokens is preserved — a composite MUST NOT collapse distinct
    `establishes` properties into one token. Tier-S components cap the headline at the S sub-tier."""
    if not reports:
        return {"ok": False, "verdict": FAIL, "component_tokens": [], "footer": FOOTER}
    ranked = []
    for r in reports:
        v = r.get("verdict")
        # An UNCHECKABLE cell surfaces as PROVENANCE_ONLY at the receipt level; in a trail it is
        # UNCHECKABLE_HERE (an unrun link).
        if r.get("cell_status") == UNCHECKABLE_HERE:
            v = UNCHECKABLE_HERE
        ranked.append((_RANK.get(v, 0), v, r))
    ranked.sort(key=lambda t: t[0])
    headline = ranked[0][1]
    return {
        "ok": headline in (REGISTRY_MEMBERSHIP_OK, CODE_EXEC_OK, MATH_TIER_D_OK, MATH_TIER_S_OK),
        "verdict": headline,
        "tier_rank": ranked[0][0],
        "component_tokens": [v for _, v, _ in sorted(
            [(_RANK.get(r.get("verdict"), 0), r.get("verdict"), r) for r in reports])],
        "components": reports,
        "footer": FOOTER,
    }


# ── CLI ─────────────────────────────────────────────────────────────────────────────────────────────
def _load(path):
    with open(path, "r", encoding="utf-8") as f:
        obj = json.load(f)
    for wrap in ("receipt", "cert", "read"):
        if isinstance(obj, dict) and wrap in obj and isinstance(obj[wrap], dict) and "signature" in obj[wrap]:
            return obj[wrap]
    return obj


def _is_ed25519_pub_b64(s):
    # Require the canonical encoding: Python <3.11 b64decode(validate=True) tolerates surplus '='
    # padding, which would let _split_pin keep the role separator as part of the key.
    try:
        raw = base64.b64decode(s, validate=True)
    except Exception:
        return False
    return len(raw) == 32 and base64.b64encode(raw).decode("ascii") == s


def _split_pin(spec):
    """Split a `--pin` argument `B64[=role]`. Base64 padding is ITSELF '=', so a naive split on the
    first (or last) '=' silently truncates the key and every receipt then reads as forged. The key is
    the longest prefix that is a valid 32-byte ed25519 public key; the rest, minus one separator, is
    the role."""
    for cut in range(len(spec), 0, -1):
        if _is_ed25519_pub_b64(spec[:cut]):
            role = spec[cut:]
            return spec[:cut], (role[1:] if role.startswith("=") else role)
    raise ValueError("%r does not start with a base64 32-byte ed25519 public key" % spec)


def _parse_verify_args(rest):
    """Split `verify`'s argv tail into (positional files, Pins, checkpoint).

    Flags (all OPT-IN; with none of them the released fail-closed default is unchanged):
      --demo                     also accept the published DEMO keys + demo checkpoint (demo_witnesses/)
      --pin B64[=role]           accept an extra RECEIPT key (repeatable)
      --registry-pin B64[=role]  accept an extra REGISTRY-ROOT key (repeatable)
      --checkpoint FILE          pin the registry checkpoint {epoch,tree_size,root_sha256} from FILE
    """
    files, receipt_pins, registry_pins, checkpoint, demo = [], {}, {}, None, False
    i = 0
    while i < len(rest):
        a = rest[i]
        if a == "--demo":
            demo = True
        elif a in ("--pin", "--registry-pin"):
            if i + 1 >= len(rest):
                raise ValueError("%s needs a base64 ed25519 public key" % a)
            key, role = _split_pin(rest[i + 1])
            i += 1
            target = receipt_pins if a == "--pin" else registry_pins
            target[key] = role or (_CLI_PIN_ROLE if a == "--pin" else _CLI_REGISTRY_PIN_ROLE)
        elif a == "--checkpoint":
            if i + 1 >= len(rest):
                raise ValueError("--checkpoint needs a JSON file")
            with open(rest[i + 1], "r", encoding="utf-8") as f:
                checkpoint = json.load(f)
            i += 1
        elif a.startswith("-"):
            raise ValueError("unknown flag %r" % a)
        else:
            files.append(a)
        i += 1
    receipt = dict(DEFAULT_PINS.receipt)
    registry = dict(DEFAULT_PINS.registry)
    if demo:
        receipt.update(DEMO_PINS.receipt)
        registry.update(DEMO_PINS.registry)
        if checkpoint is None:
            checkpoint = dict(DEMO_CHECKPOINT)
    receipt.update(receipt_pins)
    registry.update(registry_pins)
    return files, Pins(receipt=receipt, registry=registry), checkpoint


def main(argv):
    if len(argv) < 2 or argv[1] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd = argv[1]
    if cmd == "hash":
        env = _load(argv[2])
        print(witness_hash(env))
        return 0
    if cmd == "verify":
        try:
            files, pins, checkpoint = _parse_verify_args(argv[2:])
        except (ValueError, OSError) as e:
            sys.stderr.write("bad arguments: %s\n" % e)
            return 2
        if not files:
            sys.stderr.write("verify needs a receipt file\n")
            return 2
        receipt = _load(files[0])
        witness = _load(files[1]) if len(files) > 1 else None
        rep = verify_witness(receipt, witness, pinned=pins, checkpoint=checkpoint)
        print(json.dumps(rep, indent=2, ensure_ascii=False))
        # The banner goes to stderr so the JSON on stdout stays pipeable — a green verdict earned
        # under a non-production key must never scroll past unlabeled.
        if rep.get("ok") and rep.get("production_pinned") is False:
            sys.stderr.write("\n  \033[33m⚠ %s verdict under a NON-PRODUCTION key\033[0m\n    %s\n"
                             % (rep["verdict"], rep.get("trust_basis", "")))
            if rep.get("registry_key_basis"):
                sys.stderr.write("    registry root: %s\n" % rep["registry_key_basis"])
        return 0 if rep["ok"] else 1
    if cmd == "selftest":
        return _selftest()
    sys.stderr.write("unknown command %r — try: verify | hash | selftest\n" % cmd)
    return 2


# ── §F conformance selftest suite ──────────────────────────────────────────────────────────────────
def _selftest():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    ok = 0
    total = 0

    def check(label, cond):
        nonlocal ok, total
        total += 1
        ok += 1 if cond else 0
        print("  %s %s" % ("✓" if cond else "✗", label))
        return cond

    # Throwaway ed25519 receipt key + a DISJOINT throwaway registry-root key (no network).
    rsk = Ed25519PrivateKey.generate()
    rpub = base64.b64encode(rsk.public_key().public_bytes_raw()).decode()
    gsk = Ed25519PrivateKey.generate()
    gpub = base64.b64encode(gsk.public_key().public_bytes_raw()).decode()
    pins = Pins(receipt={rpub: "selftest-receipt"}, registry={gpub: "selftest-registry-root"})

    def sign_receipt(rec, sk=rsk, pub=rpub):
        canon = aperture_verify.canonical_body(rec)
        rec = dict(rec)
        rec["signature"] = {"alg": "ed25519", "pub": pub, "sig": base64.b64encode(sk.sign(canon)).decode()}
        return rec

    def make_receipt(cell, answer, claim, wit_body):
        env = {"schema": WITNESS_SCHEMA, "cell": cell,
               "answer_sha256": hashlib.sha256(answer.encode("utf-8")).hexdigest(),
               "emitted_at": "2026-07-18T00:00:00Z",
               "emitted_with": {"interpreter": "cpython-%s" % platform.python_version()},
               "claim": claim, "witness": wit_body}
        wsha = witness_hash(env)
        rec = {"kind": "read", "query": "selftest", "answer": answer,
               "witness": {"schema": WITNESS_SCHEMA, "cell": cell, "witness_sha256": wsha, "inline": env}}
        return sign_receipt(rec), env

    def sign_srr(size, root_hex, epoch="2026-07-18T00:00:00Z",
                 prev_epoch="2026-07-11T00:00:00Z", prev_size=0):
        srr = {"schema": SRR_SCHEMA, "epoch": epoch, "tree_size": size, "root_sha256": root_hex,
               "leaf_order": "fact_id_ascending", "prev_epoch": prev_epoch, "prev_tree_size": prev_size,
               "trust": "operator_self_signed"}
        canon = _canon(srr)
        srr["signature"] = {"alg": "ed25519", "pub": gpub, "sig": base64.b64encode(gsk.sign(canon)).decode()}
        return srr

    def reg_leaf(fact):
        return hashlib.sha256(REGISTRY_LEAF_DOMAIN + _canon(fact)).digest()

    # ─────────────────────────── grounded_registry vectors ───────────────────────────
    print("\n grounded_registry")
    # A 5-fact registry (non-power-of-two). Checkpoint pinned at size 3; SRR at size 5.
    facts = [
        {"fact_id": "reg:a", "subject": "s0", "predicate": "p", "object": "alpha", "unit": None,
         "source": "mock", "as_of": "2026-07-18"},
        {"fact_id": "reg:b", "subject": "s1", "predicate": "p", "object": "0.5", "unit": None,
         "source": "mock", "as_of": "2026-07-18"},
        {"fact_id": "reg:c", "subject": "s2", "predicate": "p", "object": "beta", "unit": None,
         "source": "mock", "as_of": "2026-07-18"},
        {"fact_id": "reg:planck", "subject": "planck_constant", "predicate": "value",
         "object": "6.62607015e-34", "unit": "J·s", "source": "mock", "as_of": "2026-07-18"},
        {"fact_id": "reg:pi", "subject": "pi", "predicate": "value", "object": "3.14159",
         "unit": None, "source": "mock", "as_of": "2026-07-18"},
    ]
    leaves = [reg_leaf(f) for f in facts]
    srr_root = tlog.merkle_root(leaves)
    srr = sign_srr(5, srr_root.hex(), prev_size=3)
    cp_root = tlog.merkle_root(leaves[:3])
    checkpoint = {"epoch": "2026-07-11T00:00:00Z", "tree_size": 3, "root_sha256": cp_root.hex()}
    cons_path = [h.hex() for h in tlog.consistency_proof(3, leaves)]

    def reg_witness(fact_idx, token_trace, asserted, tree_size=5, srr_obj=None, cons=None):
        f = facts[fact_idx]
        srr_use = srr_obj if srr_obj is not None else srr
        return {"fact": f, "leaf_index": fact_idx, "tree_size": tree_size,
                "audit_path": [h.hex() for h in tlog.inclusion_proof(fact_idx, leaves)],
                "registry_root": srr_use,
                "consistency_path": cons if cons is not None else cons_path,
                "token_trace": token_trace,
                "unwitnessed_byte_ranges": []}

    # Genuine: unit-free exact string. answer embeds "alpha".
    ans = "The value is alpha here."
    a0 = ans.encode().index(b"alpha")
    trace = [{"answer_span": [a0, a0 + 5], "fact_field": "object", "fact_span": [0, 5], "norm": "exact"}]
    asserted = [{"answer_span": [a0, a0 + 5], "fact_field": "object", "norm": "exact"}]
    rec, env = make_receipt("grounded_registry", ans, {"kind": "fact_grounding", "asserted_values": asserted},
                            reg_witness(0, trace, asserted))
    rep = verify_witness(rec, pinned=pins, checkpoint=checkpoint)
    check("genuine registry fact verifies -> REGISTRY_MEMBERSHIP_OK (non-pow2 tree)",
          rep["verdict"] == REGISTRY_MEMBERSHIP_OK)
    check("registry report carries unwitnessed_byte_ranges + operator_self_signed marker",
          rep["unwitnessed_byte_ranges"] is not None and rep.get("registry_trust") == "operator_self_signed"
          and "self-signed" in (rep["residual_trust"] or ""))

    # numeric norm: 0.5 vs 0.50 equal (unit-free fact).
    ans2 = "pi-ish 0.50 done"
    n0 = ans2.encode().index(b"0.50")
    tr2 = [{"answer_span": [n0, n0 + 4], "fact_field": "object", "fact_span": [0, 3], "norm": "numeric"}]
    as2 = [{"answer_span": [n0, n0 + 4], "fact_field": "object", "norm": "numeric"}]
    rec2, _ = make_receipt("grounded_registry", ans2, {"kind": "fact_grounding", "asserted_values": as2},
                           reg_witness(1, tr2, as2))
    rep2 = verify_witness(rec2, pinned=pins, checkpoint=checkpoint)
    check("numeric norm equates 0.50 with 0.5", rep2["verdict"] == REGISTRY_MEMBERSHIP_OK)

    # numeric rejects 0.5 vs 0.499
    ans3 = "value 0.499 x"
    m0 = ans3.encode().index(b"0.499")
    tr3 = [{"answer_span": [m0, m0 + 5], "fact_field": "object", "fact_span": [0, 3], "norm": "numeric"}]
    as3 = [{"answer_span": [m0, m0 + 5], "fact_field": "object", "norm": "numeric"}]
    rec3, _ = make_receipt("grounded_registry", ans3, {"kind": "fact_grounding", "asserted_values": as3},
                           reg_witness(1, tr3, as3))
    check("numeric norm rejects 0.5 vs 0.499",
          verify_witness(rec3, pinned=pins, checkpoint=checkpoint)["verdict"] == FAIL)

    # multibyte byte-offset vector: leading 'π' (2 bytes) before "3.14159".
    ansm = "π = 3.14159 exactly"
    ansm_b = ansm.encode("utf-8")
    p0 = ansm_b.index(b"3.14159")
    trm = [{"answer_span": [p0, p0 + 7], "fact_field": "object", "fact_span": [0, 7], "norm": "exact"}]
    asm = [{"answer_span": [p0, p0 + 7], "fact_field": "object", "norm": "exact"}]
    recm, _ = make_receipt("grounded_registry", ansm, {"kind": "fact_grounding", "asserted_values": asm},
                           reg_witness(4, trm, asm))
    # 'π'(2 bytes) + ' = '(3 bytes) -> '3' at BYTE index 5, but CODEPOINT index 4. Byte-slicing must
    # use 5; a codepoint-based slice (4) would mis-slice. Assert the verifier used byte offsets.
    check("multibyte answer round-trips byte-offset spans (byte p0=%d != codepoint 4)" % p0,
          verify_witness(recm, pinned=pins, checkpoint=checkpoint)["verdict"] == REGISTRY_MEMBERSHIP_OK
          and p0 == 5 and ansm[4] == "3")

    # unit-bearing genuine: numeric value + casefold unit binding.
    ansu = "Planck: 6.62607015e-34 J·s"
    ansu_b = ansu.encode("utf-8")
    v0 = ansu_b.index("6.62607015e-34".encode())
    u0 = ansu_b.index("J·s".encode())
    tru = [{"answer_span": [v0, v0 + len("6.62607015e-34")], "fact_field": "object",
            "fact_span": [0, len("6.62607015e-34")], "norm": "numeric"},
           {"answer_span": [u0, u0 + len("J·s".encode())], "fact_field": "unit",
            "fact_span": [0, len("J·s".encode())], "norm": "casefold"}]
    asu = [{"answer_span": [v0, v0 + len("6.62607015e-34")], "fact_field": "object", "norm": "numeric"},
           {"answer_span": [u0, u0 + len("J·s".encode())], "fact_field": "unit", "norm": "casefold"}]
    recu, _ = make_receipt("grounded_registry", ansu, {"kind": "fact_grounding", "asserted_values": asu},
                           reg_witness(3, tru, asu))
    check("unit-bearing fact verifies with value(numeric)+unit(casefold) binding",
          verify_witness(recu, pinned=pins, checkpoint=checkpoint)["verdict"] == REGISTRY_MEMBERSHIP_OK)

    # unit-mismatch vector: numeric value on unit-bearing fact WITHOUT unit binding ("6.626e-34 kg").
    ansk = "6.62607015e-34 kg"
    ansk_b = ansk.encode("utf-8")
    trk = [{"answer_span": [0, len("6.62607015e-34")], "fact_field": "object",
            "fact_span": [0, len("6.62607015e-34")], "norm": "numeric"}]
    ask = [{"answer_span": [0, len("6.62607015e-34")], "fact_field": "object", "norm": "numeric"}]
    reck, _ = make_receipt("grounded_registry", ansk, {"kind": "fact_grounding", "asserted_values": ask},
                           reg_witness(3, trk, ask))
    repk = verify_witness(reck, pinned=pins, checkpoint=checkpoint)
    check("numeric-only trace on unit-bearing fact rejected (unit-mismatch vector)",
          repk["verdict"] == FAIL and "unit_binding_required" in repk["reasons"])

    # forged fact: tamper the fact object after building the proof (leaf hash changes -> inclusion fails).
    wf = reg_witness(0, trace, asserted)
    wf["fact"] = dict(wf["fact"]); wf["fact"]["object"] = "FORGED"
    recf, envf = make_receipt("grounded_registry", ans, {"kind": "fact_grounding", "asserted_values": asserted}, wf)
    check("forged fact fails inclusion", verify_witness(recf, pinned=pins, checkpoint=checkpoint)["verdict"] == FAIL)

    # truncated audit path
    wt = reg_witness(0, trace, asserted); wt["audit_path"] = wt["audit_path"][:-1]
    rect, _ = make_receipt("grounded_registry", ans, {"kind": "fact_grounding", "asserted_values": asserted}, wt)
    check("truncated audit path fails", verify_witness(rect, pinned=pins, checkpoint=checkpoint)["verdict"] == FAIL)

    # wrong leaf_index
    wi = reg_witness(0, trace, asserted); wi["leaf_index"] = 2
    reci, _ = make_receipt("grounded_registry", ans, {"kind": "fact_grounding", "asserted_values": asserted}, wi)
    check("wrong leaf_index fails", verify_witness(reci, pinned=pins, checkpoint=checkpoint)["verdict"] == FAIL)

    # root swapped for a different epoch's root (validly signed, but wrong tree) -> inclusion fails
    other_leaves = leaves[:4] + [reg_leaf({"fact_id": "reg:z", "subject": "z", "predicate": "p",
                                           "object": "zeta", "unit": None, "source": "mock", "as_of": "x"})]
    other_srr = sign_srr(5, tlog.merkle_root(other_leaves).hex(), prev_size=3)
    ws = reg_witness(0, trace, asserted, srr_obj=other_srr)
    recs, _ = make_receipt("grounded_registry", ans, {"kind": "fact_grounding", "asserted_values": asserted}, ws)
    check("SRR root from a different epoch fails inclusion",
          verify_witness(recs, pinned=pins, checkpoint=checkpoint)["verdict"] == FAIL)

    # rollback/equivocation: a validly-signed SRR whose consistency vs the pinned checkpoint fails.
    # Build a divergent 5-leaf history that does NOT extend the checkpoint's first 3 leaves.
    rogue_leaves = [reg_leaf({"fact_id": "reg:r%d" % i, "subject": "r", "predicate": "p",
                              "object": "v%d" % i, "unit": None, "source": "m", "as_of": "x"}) for i in range(5)]
    rogue_srr = sign_srr(5, tlog.merkle_root(rogue_leaves).hex(), prev_size=3)
    rogue_cons = [h.hex() for h in tlog.consistency_proof(3, rogue_leaves)]
    wr = {"fact": facts[0], "leaf_index": 0, "tree_size": 5,
          "audit_path": [h.hex() for h in tlog.inclusion_proof(0, rogue_leaves)],
          "registry_root": rogue_srr, "consistency_path": rogue_cons, "token_trace": trace}
    recr, _ = make_receipt("grounded_registry", ans, {"kind": "fact_grounding", "asserted_values": asserted}, wr)
    repr_ = verify_witness(recr, pinned=pins, checkpoint=checkpoint)
    check("validly-signed rogue SRR fails consistency vs pinned checkpoint (rollback/equivocation)",
          repr_["verdict"] == FAIL and "registry_equivocation_or_rollback" in repr_["reasons"])

    # SRR signed by a valid-but-unpinned key
    usk = Ed25519PrivateKey.generate()
    upub = base64.b64encode(usk.public_key().public_bytes_raw()).decode()
    unpinned_srr = {"schema": SRR_SCHEMA, "epoch": "2026-07-18T00:00:00Z", "tree_size": 5,
                    "root_sha256": srr_root.hex(), "leaf_order": "fact_id_ascending",
                    "prev_epoch": "x", "prev_tree_size": 3, "trust": "operator_self_signed"}
    unpinned_srr["signature"] = {"alg": "ed25519", "pub": upub,
                                 "sig": base64.b64encode(usk.sign(_canon(unpinned_srr))).decode()}
    wu = reg_witness(0, trace, asserted, srr_obj=unpinned_srr)
    recun, _ = make_receipt("grounded_registry", ans, {"kind": "fact_grounding", "asserted_values": asserted}, wu)
    check("SRR under a valid-but-unpinned key rejected as forgery",
          verify_witness(recun, pinned=pins, checkpoint=checkpoint)["verdict"] == FAIL)

    # key-role crossover: SRR signed by the RECEIPT key
    cross_srr = {"schema": SRR_SCHEMA, "epoch": "2026-07-18T00:00:00Z", "tree_size": 5,
                 "root_sha256": srr_root.hex(), "leaf_order": "fact_id_ascending",
                 "prev_epoch": "x", "prev_tree_size": 3, "trust": "operator_self_signed"}
    cross_srr["signature"] = {"alg": "ed25519", "pub": rpub,
                              "sig": base64.b64encode(rsk.sign(_canon(cross_srr))).decode()}
    wc = reg_witness(0, trace, asserted, srr_obj=cross_srr)
    recc, _ = make_receipt("grounded_registry", ans, {"kind": "fact_grounding", "asserted_values": asserted}, wc)
    check("SRR signed by a RECEIPT key rejected (key-role crossover)",
          verify_witness(recc, pinned=pins, checkpoint=checkpoint)["verdict"] == FAIL)

    # token trace claiming a span the fact does not contain
    bad_trace = [{"answer_span": [a0, a0 + 5], "fact_field": "object", "fact_span": [0, 5], "norm": "exact"}]
    ans_bad = "The value is gamma here."
    ab0 = ans_bad.encode().index(b"gamma")
    bad_trace = [{"answer_span": [ab0, ab0 + 5], "fact_field": "object", "fact_span": [0, 5], "norm": "exact"}]
    bad_as = [{"answer_span": [ab0, ab0 + 5], "fact_field": "object", "norm": "exact"}]
    recb, _ = make_receipt("grounded_registry", ans_bad, {"kind": "fact_grounding", "asserted_values": bad_as},
                           reg_witness(0, bad_trace, bad_as))
    check("token trace with wrong content fails (gamma != alpha)",
          verify_witness(recb, pinned=pins, checkpoint=checkpoint)["verdict"] == FAIL)

    # unknown norm
    un_trace = [{"answer_span": [a0, a0 + 5], "fact_field": "object", "fact_span": [0, 5], "norm": "fuzzy"}]
    recn, _ = make_receipt("grounded_registry", ans, {"kind": "fact_grounding", "asserted_values": asserted},
                           reg_witness(0, un_trace, asserted))
    check("unknown norm rejected",
          verify_witness(recn, pinned=pins, checkpoint=checkpoint)["verdict"] == FAIL)

    # out-of-range span
    oor_trace = [{"answer_span": [a0, a0 + 9999], "fact_field": "object", "fact_span": [0, 5], "norm": "exact"}]
    reco, _ = make_receipt("grounded_registry", ans, {"kind": "fact_grounding", "asserted_values": asserted},
                           reg_witness(0, oor_trace, asserted))
    check("out-of-range span rejected",
          verify_witness(reco, pinned=pins, checkpoint=checkpoint)["verdict"] == FAIL)

    # asserted value with NO matching trace
    recav, _ = make_receipt("grounded_registry", ans,
                            {"kind": "fact_grounding",
                             "asserted_values": [{"answer_span": [0, 3], "fact_field": "object", "norm": "exact"}]},
                            reg_witness(0, trace, asserted))
    check("asserted value without a trace entry rejected",
          verify_witness(recav, pinned=pins, checkpoint=checkpoint)["verdict"] == FAIL)

    # default pins (no registry key pinned) -> registry cell FAILs (release gate D.3(4))
    check("with no registry key pinned (released default) registry witness FAILs",
          verify_witness(rec, pinned=Pins(receipt={rpub: "r"}, registry={}), checkpoint=checkpoint)["verdict"] == FAIL)

    # ─────────────────────────── common / binding vectors ───────────────────────────
    print("\n common / binding")
    # reuse the genuine registry receipt `rec`/`env`
    check("one-byte tamper in receipt body (answer) rejected",
          verify_witness({**rec, "answer": rec["answer"] + "!"}, pinned=pins, checkpoint=checkpoint)["verdict"] == FAIL)

    tamp_env = json.loads(json.dumps(env))
    tamp_env["witness"]["leaf_index"] = 99
    check("tamper in witness object rejected (binding mismatch)",
          verify_witness(rec, tamp_env, pinned=pins, checkpoint=checkpoint)["reasons"][0] == "witness_binding_mismatch")

    claim_env = json.loads(json.dumps(env))
    claim_env["claim"]["asserted_values"] = []
    check("tamper in claim.asserted_values rejected (rev-B: claim is bound)",
          verify_witness(rec, claim_env, pinned=pins, checkpoint=checkpoint)["reasons"][0] == "witness_binding_mismatch")

    as_env = json.loads(json.dumps(env)); as_env["answer_sha256"] = "0" * 64
    check("tamper in answer_sha256 rejected", verify_witness(rec, as_env, pinned=pins, checkpoint=checkpoint)["verdict"] == FAIL)

    ea_env = json.loads(json.dumps(env)); ea_env["emitted_at"] = "1999-01-01T00:00:00Z"
    check("tamper in emitted_at rejected", verify_witness(rec, ea_env, pinned=pins, checkpoint=checkpoint)["verdict"] == FAIL)

    # valid witness + a DIFFERENT valid receipt (answer-hash mismatch)
    rec_other, _ = make_receipt("grounded_registry", "a totally different answer",
                                {"kind": "fact_grounding", "asserted_values": asserted}, reg_witness(0, trace, asserted))
    # present env from `rec` against rec_other's witness block: binding first mismatches
    check("valid witness with a different receipt rejected",
          verify_witness(rec_other, env, pinned=pins, checkpoint=checkpoint)["verdict"] == FAIL)

    # missing answer field
    rec_noans = json.loads(json.dumps(rec)); del rec_noans["answer"]; rec_noans = sign_receipt(
        {k: v for k, v in rec_noans.items() if k != "signature"})
    repna = verify_witness(rec_noans, pinned=pins, checkpoint=checkpoint)
    check("witnessed receipt missing signed `answer` field rejected (missing_answer_field)",
          "missing_answer_field" in repna["reasons"])

    # lying mirror: fetched witness differs from its signed hash
    lie_env = json.loads(json.dumps(env)); lie_env["witness"]["fact"]["object"] = "swapped"
    check("lying-mirror witness (differs from signed hash) rejected",
          verify_witness(rec, lie_env, pinned=pins, checkpoint=checkpoint)["reasons"][0] == "witness_binding_mismatch")

    # ─────────────────────────── code_verified vectors ───────────────────────────
    print("\n code_verified")
    good_code = "print(6*7)\n"
    code_answer = "The answer is 42."
    ca0 = code_answer.encode().index(b"42")
    code_claim = {"kind": "code_execution",
                  "asserted_values": [{"answer_span": [ca0, ca0 + 2], "binds_to": "stdout_span", "span": [0, 2]}]}
    code_wit = {"language": "python", "interpreter": "cpython>=3.9,<4", "code": good_code, "stdin": "",
                "expected_stdout": "42\n", "expected_exit": 0, "timeout_s": 30}
    rec_code, _ = make_receipt("code_verified", code_answer, code_claim, code_wit)
    rep_code = verify_witness(rec_code, pinned=pins)
    try:
        import witness_sandbox  # noqa: F401
        sandbox_present = True
    except Exception:
        sandbox_present = False
    if sandbox_present and rep_code["verdict"] == CODE_EXEC_OK:
        code_path = "SANDBOXED (%s)" % rep_code.get("sandbox")
        check("genuine code re-executes -> CODE_EXEC_OK [%s]" % code_path, rep_code["verdict"] == CODE_EXEC_OK)
        # tamper expected_stdout
        bad_wit = dict(code_wit); bad_wit["expected_stdout"] = "43\n"
        rec_bc, _ = make_receipt("code_verified", code_answer, code_claim, bad_wit)
        check("code stdout mismatch -> FAIL", verify_witness(rec_bc, pinned=pins)["verdict"] == FAIL)
        # exit-code mismatch
        ec_wit = dict(code_wit); ec_wit["expected_exit"] = 3
        rec_ec, _ = make_receipt("code_verified", code_answer, code_claim, ec_wit)
        check("code exit-code mismatch -> FAIL", verify_witness(rec_ec, pinned=pins)["verdict"] == FAIL)
    else:
        code_path = "UNCHECKABLE_HERE (no witness_sandbox / no attestable isolation)"
        check("genuine code with no attestable sandbox -> PROVENANCE_ONLY, NO PASS [%s]" % code_path,
              rep_code["verdict"] == PROVENANCE_ONLY and rep_code.get("cell_status") == UNCHECKABLE_HERE)
        check("NO unsandboxed PASS is ever produced", rep_code["verdict"] != CODE_EXEC_OK)

    # timeout ceiling (checked before any execution -> deterministic regardless of sandbox)
    to_wit = dict(code_wit); to_wit["timeout_s"] = 120
    rec_to, _ = make_receipt("code_verified", code_answer, code_claim, to_wit)
    rt = verify_witness(rec_to, pinned=pins)
    check("timeout_s > 60 -> FAIL (timeout_exceeds_ceiling)",
          rt["verdict"] == FAIL and any("timeout_exceeds_ceiling" in r for r in rt["reasons"]))

    # network-fetch vector: urllib import -> static gate FAIL (before sandbox)
    net_wit = dict(code_wit); net_wit["code"] = "import urllib.request\nprint(42)\n"
    rec_net, _ = make_receipt("code_verified", code_answer, code_claim, net_wit)
    rn = verify_witness(rec_net, pinned=pins)
    check("network-fetch (urllib) rejected by static gate -> FAIL",
          rn["verdict"] == FAIL and any("static_gate" in r for r in rn["reasons"]))

    sock_wit = dict(code_wit); sock_wit["code"] = "import socket\nprint(42)\n"
    rec_sock, _ = make_receipt("code_verified", code_answer, code_claim, sock_wit)
    check("socket import rejected by static gate -> FAIL", verify_witness(rec_sock, pinned=pins)["verdict"] == FAIL)

    # nondeterminism vector: time.time() -> static gate FAIL
    nd_wit = dict(code_wit); nd_wit["code"] = "import time\nprint(int(time.time()))\n"
    rec_nd, _ = make_receipt("code_verified", code_answer, code_claim, nd_wit)
    rd = verify_witness(rec_nd, pinned=pins)
    check("nondeterminism (time.time) rejected by static gate -> FAIL",
          rd["verdict"] == FAIL and any("static_gate" in r for r in rd["reasons"]))

    # ─────────────────────────── math_verified vectors ───────────────────────────
    print("\n math_verified")
    have_sympy = _sympy_version() is not None
    if have_sympy:
        # Tier D: verify_antiderivative of x^2 on [0,1] == 1/3, and the answer "1/3" binds.
        math_answer = "The integral equals 1/3."
        mi = math_answer.encode().index(b"1/3")
        math_claim = {"kind": "math_identity", "statement": "Integral(x**2,(x,0,1)) == Rational(1,3)",
                      "asserted_values": [{"answer_span": [mi, mi + 3], "expr": "Rational(1,3)", "norm": "sympy_equal"}]}
        math_wit = {"sympy": ">=1.12", "parser": "restricted",
                    "steps": [{"op": "verify_antiderivative", "F": "x**3/3", "integrand": "x**2",
                               "var": "x", "bounds": ["0", "1"], "expect": "Rational(1,3)"}]}
        rec_math, _ = make_receipt("math_verified", math_answer, math_claim, math_wit)
        rep_math = verify_witness(rec_math, pinned=pins)
        check("Tier-D verify_antiderivative -> MATH_TIER_D_OK", rep_math["verdict"] == MATH_TIER_D_OK)

        # polynomial identity
        poly_wit = {"sympy": ">=1.12", "parser": "restricted",
                    "steps": [{"op": "expand_identity", "lhs": "(x+1)*(x-1)", "rhs": "x**2-1"}]}
        rec_poly, _ = make_receipt("math_verified", "identity holds",
                                   {"kind": "math_identity", "statement": "poly", "asserted_values": []}, poly_wit)
        check("Tier-D polynomial identity -> MATH_TIER_D_OK",
              verify_witness(rec_poly, pinned=pins)["verdict"] == MATH_TIER_D_OK)

        # Tier S distinct token
        s_wit = {"sympy": ">=1.12", "parser": "restricted",
                 "steps": [{"op": "simplify_zero", "lhs": "sin(x)**2+cos(x)**2", "rhs": "1"}]}
        # 'sin'/'cos' are not whitelisted NAME tokens -> token gate would fire. Use a Tier-S that
        # stays within the whitelist: simplify of an expand-identity that expand already proves.
        s_wit = {"sympy": ">=1.12", "parser": "restricted",
                 "steps": [{"op": "simplify_zero", "lhs": "(x+1)**2", "rhs": "x**2+2*x+1"}]}
        rec_s, _ = make_receipt("math_verified", "s",
                                {"kind": "math_identity", "statement": "s", "asserted_values": []}, s_wit)
        rep_s = verify_witness(rec_s, pinned=pins)
        check("Tier-S simplify -> MATH_TIER_S_OK (distinct token)", rep_s["verdict"] == MATH_TIER_S_OK)

        # false identity fails
        false_wit = {"sympy": ">=1.12", "parser": "restricted",
                     "steps": [{"op": "expand_identity", "lhs": "(x+1)**2", "rhs": "x**2+1"}]}
        rec_false, _ = make_receipt("math_verified", "f",
                                    {"kind": "math_identity", "statement": "f", "asserted_values": []}, false_wit)
        check("false identity -> FAIL", verify_witness(rec_false, pinned=pins)["verdict"] == FAIL)

        # wrong antiderivative caught by the diff-check
        wrong_ad = {"sympy": ">=1.12", "parser": "restricted",
                    "steps": [{"op": "verify_antiderivative", "F": "x**2/2", "integrand": "x**2",
                               "var": "x", "bounds": ["0", "1"], "expect": "Rational(1,3)"}]}
        rec_wad, _ = make_receipt("math_verified", "w",
                                  {"kind": "math_identity", "statement": "w", "asserted_values": []}, wrong_ad)
        rwad = verify_witness(rec_wad, pinned=pins)
        check("wrong antiderivative caught by diff-check -> FAIL",
              rwad["verdict"] == FAIL and "antiderivative_diff_mismatch" in rwad["reasons"])

        # token gate fires BEFORE parse: non-whitelisted NAME
        tok_wit = {"sympy": ">=1.12", "parser": "restricted",
                   "steps": [{"op": "expand_identity", "lhs": "os", "rhs": "0"}]}
        rec_tok, _ = make_receipt("math_verified", "t",
                                  {"kind": "math_identity", "statement": "t", "asserted_values": []}, tok_wit)
        rtok = verify_witness(rec_tok, pinned=pins)
        check("non-whitelisted NAME token rejected by pre-parse gate",
              rtok["verdict"] == FAIL and any("token_gate" in r for r in rtok["reasons"]))

        # attribute access rejected by gate before parse
        attr_wit = {"sympy": ">=1.12", "parser": "restricted",
                    "steps": [{"op": "expand_identity", "lhs": "x.p", "rhs": "0"}]}
        rec_attr, _ = make_receipt("math_verified", "a",
                                   {"kind": "math_identity", "statement": "a", "asserted_values": []}, attr_wit)
        rattr = verify_witness(rec_attr, pinned=pins)
        check("attribute access rejected by pre-parse gate",
              rattr["verdict"] == FAIL and any("token_gate" in r for r in rattr["reasons"]))

        # dunder rejected
        dun_wit = {"sympy": ">=1.12", "parser": "restricted",
                   "steps": [{"op": "expand_identity", "lhs": "x__class__", "rhs": "0"}]}
        rec_dun, _ = make_receipt("math_verified", "d",
                                  {"kind": "math_identity", "statement": "d", "asserted_values": []}, dun_wit)
        check("dunder rejected by pre-parse gate", verify_witness(rec_dun, pinned=pins)["verdict"] == FAIL)

        # assert the gate fires strictly BEFORE parse_expr (raise a sentinel from a patched parse_expr)
        import sympy.parsing.sympy_parser as spp
        orig = spp.parse_expr
        parse_called = {"n": 0}
        def _spy(*a, **k):
            parse_called["n"] += 1
            return orig(*a, **k)
        spp.parse_expr = _spy
        try:
            _math_parse("os")  # should raise _MathTokenError before parse
            gate_first = False
        except _MathTokenError:
            gate_first = (parse_called["n"] == 0)
        finally:
            spp.parse_expr = orig
        check("token gate fires BEFORE parse_expr is ever called", gate_first)
    else:
        # SymPy absent -> UNCHECKABLE_HERE, never PASS
        math_wit = {"sympy": ">=1.12", "parser": "restricted",
                    "steps": [{"op": "rational_arith", "expr": "Rational(1,2)", "expect": "Rational(1,2)"}]}
        rec_math, _ = make_receipt("math_verified", "x",
                                   {"kind": "math_identity", "statement": "x", "asserted_values": []}, math_wit)
        rep_math = verify_witness(rec_math, pinned=pins)
        check("SymPy absent -> PROVENANCE_ONLY (UNCHECKABLE_HERE), never PASS",
              rep_math["verdict"] == PROVENANCE_ONLY and rep_math.get("cell_status") == UNCHECKABLE_HERE)

    # ─────────────────────────── verdict structure / boundary ───────────────────────────
    print("\n verdict structure / boundary")
    # gates-fail -> FAIL regardless of cell (wrong receipt key)
    wrong_pins = Pins(receipt={upub: "not-the-signer"}, registry={gpub: "r"})
    check("gates fail (unpinned receipt key) -> FAIL",
          verify_witness(rec, pinned=wrong_pins, checkpoint=checkpoint)["verdict"] == FAIL)

    # composite {PASS, PASS, UNCHECKABLE} -> headline UNCHECKABLE_HERE tier, all tokens listed
    passA = {"verdict": REGISTRY_MEMBERSHIP_OK}
    passB = {"verdict": MATH_TIER_D_OK}
    unchk = {"verdict": PROVENANCE_ONLY, "cell_status": UNCHECKABLE_HERE}
    comp1 = compose_reports([passA, passB, unchk])
    check("composite {PASS,PASS,UNCHECKABLE} headlines UNCHECKABLE_HERE",
          comp1["verdict"] == UNCHECKABLE_HERE and len(comp1["component_tokens"]) == 3)

    # composite {PASS, FAIL, PASS} -> FAIL
    comp2 = compose_reports([passA, {"verdict": FAIL}, passB])
    check("composite {PASS,FAIL,PASS} headlines FAIL", comp2["verdict"] == FAIL)

    # composite containing MATH_TIER_S_OK never headlines above S sub-tier
    comp3 = compose_reports([passA, passB, {"verdict": MATH_TIER_S_OK}])
    check("composite with MATH_TIER_S_OK capped at Tier-S sub-tier",
          comp3["verdict"] == MATH_TIER_S_OK and comp3["tier_rank"] == 2.5)

    # every report carries footer verbatim + establishes + residual + checker
    check("report footer verbatim present", rep["footer"] == FOOTER)
    check("report carries establishes + residual_trust + checker attestation",
          rep["establishes"] and rep["residual_trust"] and rep["checker"]["selftest_sha256"])

    # affirmative-claim hygiene: `establishes` must not assert true/correct/accurate as established
    def affirmative_clean(r):
        est = (r.get("establishes") or "").lower()
        return not any(w in est for w in ("true", "correct", "accurate"))
    check("no report `establishes` string asserts true/correct/accurate (footer negation exempt)",
          affirmative_clean(rep) and affirmative_clean(rep2) and affirmative_clean(rep_code))
    check("footer negation '...not that the answer is correct' is PRESENT (required, not banned)",
          "not that the answer is correct" in rep["footer"])

    # ─────────────────── the SHIPPED demo pack (demo_witnesses/) ───────────────────
    # This is the artifact handed to external reviewers: it MUST verify green under the documented
    # demo procedure and MUST still FAIL under the released default. Both halves are asserted here so
    # neither can regress silently — a demo pack that no longer verifies is a demo-day landmine, and a
    # demo key that leaks into the default pins is a hole in the fail-closed floor.
    print("\n shipped demo pack (demo_witnesses/)")
    demo_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo_witnesses")
    expect = {"grounded_registry": REGISTRY_MEMBERSHIP_OK, "code_verified": CODE_EXEC_OK,
              "math_verified": MATH_TIER_D_OK}
    check("demo_witnesses/ ships beside the verifier", os.path.isdir(demo_dir))
    for cellname, want in sorted(expect.items()):
        rpath = os.path.join(demo_dir, "%s_receipt.json" % cellname)
        wpath = os.path.join(demo_dir, "%s_witness.json" % cellname)
        if not (os.path.exists(rpath) and os.path.exists(wpath)):
            check("demo pack %s: receipt+witness present" % cellname, False)
            continue
        with open(rpath, "r", encoding="utf-8") as f:
            drec = json.load(f)
        with open(wpath, "r", encoding="utf-8") as f:
            dwit = json.load(f)
        # (1) the documented demo procedure — exactly what README/`--demo` wires up.
        dfiles, dpins, dcp = _parse_verify_args(["--demo", rpath, wpath])
        drep = verify_witness(drec, dwit, pinned=dpins, checkpoint=dcp)
        if drep["verdict"] == want:
            check("demo pack %s verifies %s under --demo" % (cellname, want), True)
        elif (cellname == "code_verified" and drep["verdict"] == PROVENANCE_ONLY
              and any("sandbox" in r for r in drep["reasons"])):
            # Honest platform limit, not a pack regression: no attestable sandbox here ⇒ never a PASS.
            check("demo pack code_verified: no attestable sandbox on this platform (%s) — "
                  "PROVENANCE_ONLY, not a false PASS" % "; ".join(drep["reasons"]), True)
        else:
            check("demo pack %s verifies %s under --demo (got %s: %s)"
                  % (cellname, want, drep["verdict"], "; ".join(drep["reasons"])), False)
        # (2) a green demo verdict SAYS it is a demo key — in the report and in `reasons`.
        if drep["ok"]:
            check("demo pack %s: green verdict is stamped demo (trust_basis + production_pinned:false"
                  " + reasons)" % cellname,
                  drep.get("production_pinned") is False
                  and "DEMO" in (drep.get("trust_basis") or "")
                  and any(r.startswith("demo_key:") for r in drep["reasons"]))
        # (3) fail-closed floor intact: the SAME bytes must FAIL with no flags.
        drep_default = verify_witness(drec, dwit)
        check("demo pack %s FAILs under the released default pins (fail-closed intact)" % cellname,
              drep_default["verdict"] == FAIL
              and any("UNKNOWN key" in r for r in drep_default["reasons"]))

    # the demo keys must never have leaked into the production pin set
    check("DEMO keys are absent from aperture_verify.PINNED_KEYS (production untouched)",
          DEMO_RECEIPT_KEY not in aperture_verify.PINNED_KEYS
          and DEMO_REGISTRY_KEY not in aperture_verify.PINNED_KEYS)
    check("DEMO keys are absent from DEFAULT_PINS (default stays fail-closed)",
          DEMO_RECEIPT_KEY not in DEFAULT_PINS.receipt and DEMO_REGISTRY_KEY not in DEFAULT_PINS.registry)
    check("--demo keeps receipt/registry key roles disjoint (spec §C)",
          set(DEMO_PINS.receipt) & set(DEMO_PINS.registry) == set())
    # --pin is an explicit, per-run act: it must not mint a production-looking verdict
    ppins = _parse_verify_args(["--pin", "%s=demo-by-hand" % DEMO_RECEIPT_KEY])[1]
    prep = verify_witness(json.load(open(os.path.join(demo_dir, "math_verified_receipt.json"),
                                         encoding="utf-8")),
                          json.load(open(os.path.join(demo_dir, "math_verified_witness.json"),
                                         encoding="utf-8")),
                          pinned=ppins) if os.path.isdir(demo_dir) else {}
    check("--pin verdict is marked non-production, never production_pinned",
          prep.get("production_pinned") is False
          and any(r.startswith("non_production_key:") for r in prep.get("reasons", [])))
    # `--pin B64` with no role: the '=' padding must survive the split, or every receipt reads forged
    check("--pin parses a padded base64 key with no role (padding not eaten)",
          _split_pin(DEMO_RECEIPT_KEY) == (DEMO_RECEIPT_KEY, "")
          and _split_pin("%s=hand-pinned" % DEMO_RECEIPT_KEY) == (DEMO_RECEIPT_KEY, "hand-pinned"))

    # --demo widens the pin set by exactly two named keys — it is not a skeleton key and it does not
    # switch off a single gate. Both halves are asserted against the demo pins themselves.
    _, demo_pins, demo_cp = _parse_verify_args(["--demo"])
    check("--demo does NOT accept some other unknown key (still a forgery)",
          verify_witness(sign_receipt(rec, usk, upub), env,
                         pinned=demo_pins, checkpoint=demo_cp)["verdict"] == FAIL)
    if os.path.isdir(demo_dir):
        with open(os.path.join(demo_dir, "math_verified_receipt.json"), encoding="utf-8") as f:
            trec = json.load(f)
        with open(os.path.join(demo_dir, "math_verified_witness.json"), encoding="utf-8") as f:
            twit = json.load(f)
        tam = json.loads(json.dumps(twit))
        tam["claim"]["statement"] = (tam["claim"].get("statement") or "") + " "
        check("--demo still FAILs a tampered demo witness (binding gate untouched)",
              verify_witness(trec, tam, pinned=demo_pins, checkpoint=demo_cp)["verdict"] == FAIL)
        tam2 = json.loads(json.dumps(trec))
        tam2["answer"] = tam2["answer"] + "!"
        check("--demo still FAILs a demo receipt whose answer was edited after signing",
              verify_witness(tam2, twit, pinned=demo_pins, checkpoint=demo_cp)["verdict"] == FAIL)

    print("\n  aperture-witness self-test: %d/%d passed" % (ok, total))
    return 0 if ok == total else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
