#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""aperture transparency log — an append-only Merkle witness log for signed receipts.

Move 1 of the plan, the real trust-ladder rung: self-signing a receipt proves *we* say it's authentic;
publishing its leaf into an append-only, externally-witnessable Merkle log proves we cannot later
*suppress* it or *swap* it without detection — by anyone, not just us. This is the RFC 6962
(Certificate Transparency) tree structure, self-contained and dependency-light:

  - the leaf for a receipt is `aperture_verify.leaf_hash(receipt)` (a stable content hash of the signed body)
  - the log keeps an append-only file of leaves and publishes a Signed Tree Head (STH): {size, root, ...}
  - an INCLUSION proof shows a given receipt's leaf is in the tree at index i under a published root
  - a CONSISTENCY proof shows the size-n tree is a prefix of the size-m tree — i.e. the log only ever
    APPENDED; nothing was rewritten between two STHs a client saw

The STH root is signed with the log's own ed25519 key here; the genuinely-external rung is to have a
third-party WITNESS co-sign the same root (or mirror the leaves to a public Sigstore/Rekor instance) so
neither the log operator alone nor Aperture alone can present two divergent histories. That co-signature
is a named-partner milestone; this module is the primitive it hangs on.

CLI:
    python3 transparency_log.py add receipt.json            # append a receipt's leaf, print index + STH
    python3 transparency_log.py sth                         # current signed tree head
    python3 transparency_log.py prove <index>              # inclusion proof for a leaf
    python3 transparency_log.py check receipt.json          # verify a receipt IS in the current log
    python3 transparency_log.py session-root r1.json r2.json ...          # the head of an ordered session trail
    python3 transparency_log.py verify-session <root> r1.json r2.json ... # verify a whole agent-run trail
    python3 transparency_log.py verify-checkpoint log/checkpoints/23 [log/leaves.jsonl]  # C2SP checkpoint under the pinned log key
    python3 transparency_log.py verify-anchor log/checkpoints/22 log/rekor-entry-0.json  # that checkpoint is logged in Sigstore Rekor v2
    python3 transparency_log.py verify-anchor log/checkpoints/23 log/rekor-entry-1.json
    python3 transparency_log.py consistency log/checkpoints/22 log/checkpoints/23 log/proofs/consistency-22-23.json
                                                            # RFC 6962/9162: the size-23 tree only appended to the size-22 tree
    python3 transparency_log.py consistency make log/checkpoints/22 log/checkpoints/23 log/leaves.jsonl [out.json]
                                                            # (log operator) generate + self-verify that proof from the leaves

LOG LAYOUT: log/checkpoints/<size> holds every signed checkpoint, byte-identical, forever; log/checkpoint is a
byte-identical copy of the newest one (the head); log/rekor-entry-<k>.json is the k-th Rekor anchor (entry 0
anchors checkpoints/22, entry 1 anchors checkpoints/23); log/proofs/consistency-<m>-<n>.json links consecutive
checkpoints; log/leaves.jsonl holds the leaves of the head (older checkpoints verify against its prefix).
    python3 transparency_log.py keys                        # the pinned receipt-log key and who holds it
    python3 transparency_log.py selftest                    # RFC6962 inclusion + consistency + session + tamper

SESSION TRAILS (the C9 "flight recorder"): a session is an ordered list of Aperture receipts from one agent
run. Its tamper-evident head is the Merkle root over the receipt leaves. `verify-session` confirms every
receipt's Aperture signature AND that the recomputed root matches the recorded head — so altering, reordering,
dropping, or inserting a receipt in a 12-hour run is caught. You cannot watch a long-running agent; you can
verify its trail.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

# ── RFC 6962 Merkle hashing over a list of leaf-DATA (each 32-byte content hash) ─────────────────────
_LEAF = b"\x00"
_NODE = b"\x01"


def _h(*parts: bytes) -> bytes:
    m = hashlib.sha256()
    for p in parts:
        m.update(p)
    return m.digest()


def _leaf_hash(data: bytes) -> bytes:
    return _h(_LEAF, data)


def _split(n: int) -> int:
    """Largest power of two STRICTLY less than n (RFC 6962 k)."""
    return 1 << ((n - 1).bit_length() - 1)


def merkle_root(leaves: list[bytes]) -> bytes:
    """MTH — Merkle Tree Hash of the leaf-data list."""
    n = len(leaves)
    if n == 0:
        return hashlib.sha256(b"").digest()
    if n == 1:
        return _leaf_hash(leaves[0])
    k = _split(n)
    return _h(_NODE, merkle_root(leaves[:k]), merkle_root(leaves[k:]))


def inclusion_proof(m: int, leaves: list[bytes]) -> list[bytes]:
    """Audit path for the m-th leaf in a tree of len(leaves) leaves (RFC 6962 PATH)."""
    n = len(leaves)
    if n == 1:
        return []
    k = _split(n)
    if m < k:
        return inclusion_proof(m, leaves[:k]) + [merkle_root(leaves[k:])]
    return inclusion_proof(m - k, leaves[k:]) + [merkle_root(leaves[:k])]


def verify_inclusion(leaf_data: bytes, m: int, n: int, proof: list[bytes], root: bytes) -> bool:
    """Recompute the root from a leaf + its audit path and compare (RFC 6962 verification)."""
    if m < 0 or n <= 0 or m >= n or len(root) != 32 or len(proof) > 63 or any(len(p) != 32 for p in proof):
        return False
    node = _leaf_hash(leaf_data)
    fn, sn = m, n - 1
    for sibling in proof:
        if fn % 2 == 1 or fn == sn:
            node = _h(_NODE, sibling, node)
            while not (fn % 2 == 1 or fn == 0):
                fn >>= 1
                sn >>= 1
        else:
            node = _h(_NODE, node, sibling)
        fn >>= 1
        sn >>= 1
    return fn == 0 and node == root


def consistency_proof(m: int, leaves: list[bytes]) -> list[bytes]:
    """Prove the size-m tree is a prefix of the size-len(leaves) tree (RFC 6962 PROOF)."""
    n = len(leaves)
    if m == n:
        return []
    return _subproof(m, leaves, True)


def _subproof(m: int, leaves: list[bytes], b: bool) -> list[bytes]:
    n = len(leaves)
    if m == n:
        return [] if b else [merkle_root(leaves)]
    k = _split(n)
    if m <= k:
        return _subproof(m, leaves[:k], b) + [merkle_root(leaves[k:])]
    return _subproof(m - k, leaves[k:], False) + [merkle_root(leaves[:k])]


def verify_consistency(m: int, n: int, proof: list[bytes], first_root: bytes, second_root: bytes) -> bool:
    """Verify a consistency proof between the size-m and size-n trees (RFC 6962)."""
    if len(first_root) != 32 or len(second_root) != 32 or len(proof) > 63 or any(len(p) != 32 for p in proof):
        return False
    if m < 0 or n < 0:
        return False
    if m == n:
        return not proof and first_root == second_root and (m > 0 or first_root == hashlib.sha256(b"").digest())
    if m == 0 or m > n:
        return False
    if m & (m - 1) == 0:                       # m is a power of two: first node is the old root, implied
        proof = [first_root] + proof
    if not proof:
        return False
    fn, sn = m - 1, n - 1
    while fn % 2 == 1:
        fn >>= 1
        sn >>= 1
    fr = sr = proof[0]
    for c in proof[1:]:
        if sn == 0:
            return False
        if fn % 2 == 1 or fn == sn:
            fr = _h(_NODE, c, fr)
            sr = _h(_NODE, c, sr)
            while not (fn % 2 == 1 or fn == 0):
                fn >>= 1
                sn >>= 1
        else:
            sr = _h(_NODE, sr, c)
        fn >>= 1
        sn >>= 1
    return fn == 0 and sn == 0 and fr == first_root and sr == second_root


# ── the persistent append-only log ──────────────────────────────────────────────────────────────────
class TransparencyLog:
    """Append-only leaf store. The leaves file is the source of truth; the STH is derived. In production
    the STH would additionally be ed25519-signed by the log AND co-signed by an external witness."""

    def __init__(self, path: str):
        self.path = path
        self.leaves: list[bytes] = []
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        self.leaves.append(bytes.fromhex(json.loads(line)["leaf"]))

    def append(self, leaf_hex: str) -> int:
        data = bytes.fromhex(leaf_hex)
        idx = len(self.leaves)
        self.leaves.append(data)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"index": idx, "leaf": leaf_hex}) + "\n")
        return idx

    def size(self) -> int:
        return len(self.leaves)

    def root_hex(self) -> str:
        return merkle_root(self.leaves).hex()

    def sth(self) -> dict:
        return {"log": "aperture.transparency/v1", "tree_size": self.size(), "root_sha256": self.root_hex(),
                "note": "root is co-signable by an external witness; suppression/rewrite is then third-party detectable"}

    def index_of(self, leaf_hex: str) -> int:
        data = bytes.fromhex(leaf_hex)
        for i, d in enumerate(self.leaves):
            if d == data:
                return i
        return -1

    def inclusion(self, m: int) -> dict:
        proof = inclusion_proof(m, self.leaves)
        return {"leaf_index": m, "tree_size": self.size(), "root_sha256": self.root_hex(),
                "audit_path": [p.hex() for p in proof]}


# ── C2SP signed checkpoints + the pinned receipt-log key + Rekor v2 anchors (ANCHORING.md) ───────────
# A checkpoint is a C2SP signed note (c2sp.org/tlog-checkpoint): "<origin>\n<size>\n<b64 root>\n", then a
# blank line and "— <key name> <b64(4-byte key id || ed25519 sig)>". The key is the dedicated receipt-LOG key
# (not the registry receipt key): held by Kevin Russell, Lead Researcher, Honesty.tools, generated and kept
# only on his DGX. Anchor: the same checkpoint body, Ed25519ph-signed by the same key, logged as a Rekor v2
# hashedrekord (SHA2_512 digest). Verification is offline: our note sig, Rekor's body digest + key binding,
# the Merkle inclusion proof, and Rekor's own checkpoint signature under its pinned key.
LOG_ORIGIN = "www.honesty.tools/receipts"
PINNED_LOG_VKEYS = {
    "www.honesty.tools/receipts+fcd79bdd+AUF/J2nQKn90Ba52NjK0jqgIYayocRQGAYsK7PUdD7+C":
        "receipt-log key (ed25519, sha256(pub)=d1f9d9386e4173ba6e5e80ad3bb624cf18ee389fe9ba8ed6669be195f4a76342) — "
        "held by Kevin Russell, Lead Researcher, Honesty.tools; generated 2026-10-27T05:54:21Z and kept only on his DGX",
}
# Rekor v2 shard keys, copied from Sigstore's TUF trusted_root.json (sigstore/root-signing, targets/).
PINNED_REKOR_LOGS = {"log2025-1.rekor.sigstore.dev": "t8rlp1knGwjfbcXAYPYAkn0XiLz1x8O4t0YkEhie244="}
_SPKI_ED25519 = bytes.fromhex("302a300506032b6570032100")


def decode_b64_strict(s: str, *, expected_len: int | None = None, label: str = "base64") -> bytes:
    """Decode strict canonical ASCII standard base64 (no stray chars, whitespace, or non-zero padding bits)."""
    import base64, binascii
    if not isinstance(s, str) or not s or not s.isascii() or any(c.isspace() for c in s):
        raise ValueError("NON-CANONICAL ENCODING (%s): invalid characters or whitespace" % label)
    try:
        raw = base64.b64decode(s.encode("ascii"), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("NON-CANONICAL ENCODING (%s): %s" % (label, exc))
    if base64.b64encode(raw).decode("ascii") != s:
        raise ValueError("NON-CANONICAL ENCODING (%s): non-canonical base64 padding or trailing bits" % label)
    if expected_len is not None and len(raw) != expected_len:
        raise ValueError("NON-CANONICAL ENCODING (%s): expected %d bytes, got %d" % (label, expected_len, len(raw)))
    return raw


def _parse_canonical_uint(s: str, label: str = "size") -> int:
    if not isinstance(s, str) or not s or not s.isascii() or not s.isdigit() or (len(s) > 1 and s.startswith("0")):
        raise ValueError("malformed %s %r" % (label, s))
    return int(s)


def parse_vkey(vkey: str):
    """C2SP signed-note verifier key '<name>+<hex key id>+<b64(0x01||pub)>' -> (name, key_id, raw pub)."""
    parts = vkey.strip().split("+", 2)
    if len(parts) != 3:
        raise ValueError("malformed vkey")
    name, kid_hex, b = parts
    if not name or len(kid_hex) != 8 or kid_hex != kid_hex.lower():
        raise ValueError("malformed vkey header")
    try:
        bytes.fromhex(kid_hex)
    except ValueError:
        raise ValueError("malformed vkey key id")
    blob = decode_b64_strict(b, expected_len=33, label="vkey %s" % name)
    if blob[0] != 1:
        raise ValueError("not an ed25519 (type 0x01) vkey")
    pub = blob[1:]
    kid = _h(name.encode(), b"\n\x01", pub)[:4]
    if kid.hex() != kid_hex:
        raise ValueError("vkey key id does not match its name+key")
    return name, kid, pub


def checkpoint_body(origin: str, size: int, root: bytes) -> bytes:
    import base64
    return ("%s\n%d\n%s\n" % (origin, size, base64.b64encode(root).decode())).encode()


def _split_note(note: str):
    if "\n\n" not in note:
        raise ValueError("not a signed note (no blank line before signatures)")
    text, sigs = note.split("\n\n", 1)
    if not sigs:
        raise ValueError("not a signed note (empty signature block)")
    raw_lines = sigs.splitlines()
    if not raw_lines:
        raise ValueError("not a signed note (no signature lines)")
    parsed = []
    for l in raw_lines:
        if not l:
            raise ValueError("NON-CANONICAL ENCODING (signature block): blank line in signature section")
        parts = l.split(" ")
        if len(parts) != 3 or parts[0] != "\u2014" or not parts[1]:
            raise ValueError("NON-CANONICAL ENCODING (signature line): malformed signature line %r" % l)
        blob = decode_b64_strict(parts[2], label="signature %s" % parts[1])
        if len(blob) not in (68, 76):
            raise ValueError("NON-CANONICAL ENCODING (signature %s): wrong signature length %d (expected 68 or 76)"
                             % (parts[1], len(blob)))
        parsed.append((l, parts[1], blob))
    return text + "\n", [l for l, _, _ in parsed]


def verify_checkpoint(note: str, vkeys: dict | None = None) -> dict:
    """Verify a C2SP checkpoint under a PINNED log vkey. Signatures by unknown keys are ignored (C2SP)
    once every signature line is strictly parsed; at least one pinned signature must verify. Returns
    {origin, tree_size, root, key}."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.exceptions import InvalidSignature
    vkeys = PINNED_LOG_VKEYS if vkeys is None else vkeys
    text, sigs = _split_note(note)
    lines = text.split("\n")[:-1]
    if len(lines) < 3:
        raise ValueError("checkpoint body needs origin, size, root")
    origin = lines[0]
    if not origin or any(c.isspace() for c in origin):
        raise ValueError("malformed checkpoint origin")
    size = _parse_canonical_uint(lines[1], "checkpoint size")
    root = decode_b64_strict(lines[2], expected_len=32, label="checkpoint root")
    if size == 0 and root != hashlib.sha256(b"").digest():
        raise ValueError("size-0 checkpoint root must equal SHA-256 of empty input")
    for vk in vkeys:
        name, kid, pub = parse_vkey(vk)
        if name != origin:
            continue
        for l in sigs:
            _, who, b64 = l.split(" ")
            if who != name:
                continue
            blob = decode_b64_strict(b64, label="signature %s" % who)
            if len(blob) != 68:
                raise ValueError("NON-CANONICAL ENCODING (signature %s): expected 68 bytes for log key, got %d"
                                 % (who, len(blob)))
            if blob[:4] != kid:
                continue
            try:
                Ed25519PublicKey.from_public_bytes(pub).verify(blob[4:], text.encode())
            except InvalidSignature:
                raise ValueError("checkpoint signature by the pinned log key does NOT verify")
            return {"origin": origin, "tree_size": size, "root": root.hex(), "key": vk, "body": text}
    raise ValueError("no signature from a pinned log key on this checkpoint")


def _verify_rekor_checkpoint(env: str, logs: dict) -> dict:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    text, sigs = _split_note(env)
    lines = text.split("\n")[:-1]
    if len(lines) < 3:
        raise ValueError("Rekor checkpoint body needs origin, size, root")
    origin = lines[0]
    if origin not in logs:
        raise ValueError("Rekor checkpoint origin %r is not a pinned Rekor shard" % origin)
    size = _parse_canonical_uint(lines[1], "Rekor tree size")
    root = decode_b64_strict(lines[2], expected_len=32, label="Rekor root")
    pub = decode_b64_strict(logs[origin], expected_len=32, label="Rekor vkey")
    kid = _h(origin.encode(), b"\n\x01", pub)[:4]
    for l in sigs:
        _, who, b64 = l.split(" ")
        if who != origin:
            continue
        blob = decode_b64_strict(b64, expected_len=68, label="Rekor signature")
        if blob[:4] == kid:
            Ed25519PublicKey.from_public_bytes(pub).verify(blob[4:], text.encode())
            others = sorted({x.split(" ")[1] for x in sigs if x.split(" ")[1] != origin})
            return {"origin": origin, "tree_size": size, "root": root,
                    "other_signers_unverified": others}
    raise ValueError("no signature by the pinned Rekor key on its checkpoint")


def verify_rekor_anchor(note: str, entry: dict, vkeys: dict | None = None, rekor_logs: dict | None = None) -> dict:
    """ANCHORED check: the Rekor v2 TransparencyLogEntry `entry` logs THIS checkpoint, under THIS log key, and
    is included in a Rekor checkpoint signed by a pinned Rekor shard key. Offline; raises ValueError on failure."""
    cp = verify_checkpoint(note, vkeys)
    rekor_logs = PINNED_REKOR_LOGS if rekor_logs is None else rekor_logs
    body = decode_b64_strict(entry["canonicalizedBody"], label="canonicalizedBody")
    rec = json.loads(body)
    if rec.get("kind") != "hashedrekord" or rec.get("apiVersion") != "0.0.2":
        raise ValueError("Rekor entry is not hashedrekord 0.0.2")
    spec = rec["spec"]["hashedRekordV002"]
    if spec["data"]["algorithm"] != "SHA2_512" or \
            decode_b64_strict(spec["data"]["digest"], expected_len=64, label="Rekor digest") != hashlib.sha512(cp["body"].encode()).digest():
        raise ValueError("Rekor entry digest is not SHA-512 of this checkpoint body")
    ver = spec["signature"]["verifier"]
    _, _, pub = parse_vkey(cp["key"])
    if ver.get("keyDetails") != "PKIX_ED25519_PH" or \
            decode_b64_strict(ver["publicKey"]["rawBytes"], expected_len=len(_SPKI_ED25519) + 32, label="Rekor pubkey") != _SPKI_ED25519 + pub:
        raise ValueError("Rekor entry was not made with the pinned receipt-log key")
    ph = "not re-checked offline (needs pycryptodome; Rekor verified it on admission)"
    try:
        from Crypto.PublicKey import ECC
        from Crypto.Signature import eddsa
        from Crypto.Hash import SHA512
        eddsa.new(ECC.import_key(_SPKI_ED25519 + pub), "rfc8032").verify(
            SHA512.new(cp["body"].encode()),
            decode_b64_strict(spec["signature"]["content"], expected_len=64, label="Rekor Ed25519ph sig"))
        ph = "verified"
    except ImportError:
        pass
    except ValueError:
        raise ValueError("Ed25519ph signature in the Rekor entry does NOT verify")
    ip = entry["inclusionProof"]
    rk = _verify_rekor_checkpoint(ip["checkpoint"]["envelope"], rekor_logs)
    idx = int(entry["logIndex"])
    if not verify_inclusion(body, idx, rk["tree_size"],
                              [decode_b64_strict(x, expected_len=32, label="Rekor inclusion hash") for x in ip["hashes"]],
                              rk["root"]):
        raise ValueError("Rekor inclusion proof does not verify against Rekor's signed checkpoint")
    return {"ok": True, "checkpoint": {k: cp[k] for k in ("origin", "tree_size", "root")},
            "rekor": {"log": rk["origin"], "log_index": idx, "tree_size": rk["tree_size"],
                      "ed25519ph_sig": ph, "cosigners_listed_not_verified": rk["other_signers_unverified"]}}

# ── consistency between two published checkpoints (RFC 6962 §2.1.2 / RFC 9162 §2.1.4) ─────────────────
# A proof file links two checkpoints of the same log: the older tree is a prefix of the newer one, so nothing
# logged before the older head was rewritten or dropped. Both checkpoints must carry a pinned-key signature,
# and the sizes and roots inside the proof file must equal the ones in the checkpoints.
PROOF_TYPE = "rfc9162-consistency-proof/sha256"


def make_consistency_proof(old_note: str, new_note: str, leaves: list[bytes], vkeys: dict | None = None) -> dict:
    """Build the proof file for old->new from the leaf list (len >= new size); refuses if either checkpoint's
    root is not reproduced by the leaves. Self-verifies before returning."""
    a, b = verify_checkpoint(old_note, vkeys), verify_checkpoint(new_note, vkeys)
    if a["origin"] != b["origin"]:
        raise ValueError("checkpoints are from different logs (%r vs %r)" % (a["origin"], b["origin"]))
    m, n = a["tree_size"], b["tree_size"]
    if not 0 < m <= n:
        raise ValueError("need 0 < old size <= new size (got %d, %d)" % (m, n))
    if len(leaves) < n:
        raise ValueError("leaves file has %d leaves, new checkpoint needs %d" % (len(leaves), n))
    if merkle_root(leaves[:m]).hex() != a["root"]:
        raise ValueError("first %d leaves do not reproduce the old checkpoint root" % m)
    if merkle_root(leaves[:n]).hex() != b["root"]:
        raise ValueError("first %d leaves do not reproduce the new checkpoint root" % n)
    proof = consistency_proof(m, leaves[:n])
    obj = {"type": PROOF_TYPE, "origin": a["origin"], "old_size": m, "old_root": a["root"],
           "new_size": n, "new_root": b["root"], "proof": [p.hex() for p in proof]}
    verify_consistency_file(old_note, new_note, obj, vkeys)
    return obj


def verify_consistency_file(old_note: str, new_note: str, obj: dict, vkeys: dict | None = None) -> dict:
    """CONSISTENT check, offline: both checkpoints verify under a pinned log key, same origin, the proof file
    names exactly their sizes and roots, and the RFC 9162 proof recomputes both roots. Raises ValueError."""
    a, b = verify_checkpoint(old_note, vkeys), verify_checkpoint(new_note, vkeys)
    if a["origin"] != b["origin"]:
        raise ValueError("checkpoints are from different logs (%r vs %r)" % (a["origin"], b["origin"]))
    if obj.get("type") != PROOF_TYPE:
        raise ValueError("proof file type is %r, expected %r" % (obj.get("type"), PROOF_TYPE))
    if obj.get("origin") != a["origin"]:
        raise ValueError("proof file origin %r does not match the checkpoints" % obj.get("origin"))
    if (obj.get("old_size"), obj.get("old_root")) != (a["tree_size"], a["root"]):
        raise ValueError("proof file old size/root do not match the old checkpoint")
    if (obj.get("new_size"), obj.get("new_root")) != (b["tree_size"], b["root"]):
        raise ValueError("proof file new size/root do not match the new checkpoint")
    try:
        proof = [bytes.fromhex(x) for x in obj.get("proof", [])]
    except (TypeError, ValueError):
        raise ValueError("proof hashes are not hex")
    if any(len(x) != 32 for x in proof):
        raise ValueError("proof hashes must be 32 bytes")
    m, n = a["tree_size"], b["tree_size"]
    if not verify_consistency(m, n, proof, bytes.fromhex(a["root"]), bytes.fromhex(b["root"])):
        raise ValueError("consistency proof does NOT verify: size %d is not shown to be a prefix of size %d" % (m, n))
    return {"ok": True, "origin": a["origin"], "old_size": m, "old_root": a["root"],
            "new_size": n, "new_root": b["root"], "proof_len": len(proof)}


# ── session trails: the C9 "flight recorder" — verify a whole agent run, not one receipt ──────────────
# A session is an ORDERED list of Aperture receipts from one agent run. Its tamper-evident head is the
# Merkle root over the receipt leaves. Verifying a session = (1) every receipt's Aperture signature is valid,
# AND (2) the recomputed root matches the head the customer recorded (and, ideally, witnessed in a public
# log). Altering, reordering, dropping, or inserting a receipt breaks (1) or (2). No extra signing key is
# needed on the leaves — they are already bound to Aperture-signed receipts; the root binds their SEQUENCE.
def session_root(receipts: list[dict]) -> str:
    import aperture_verify
    leaves = [bytes.fromhex(aperture_verify.leaf_hash(r)) for r in receipts]
    return merkle_root(leaves).hex()


def verify_session(receipts: list[dict], claimed_root_hex: str, pinned: dict | None = None) -> dict:
    """Verify an ordered session trail against its recorded root. Returns a report; never raises on a bad
    receipt (it records which failed) so a partial trail is diagnosable."""
    import aperture_verify
    bad_sig = []
    for i, r in enumerate(receipts):
        try:
            aperture_verify.verify_receipt(r, pinned=pinned)
        except aperture_verify.VerifyError:
            bad_sig.append(i)
    root = session_root(receipts)
    root_ok = (root == (claimed_root_hex or "").strip().lower())
    return {"ok": (not bad_sig) and root_ok, "n": len(receipts), "n_bad_sig": len(bad_sig),
            "bad_sig_indices": bad_sig, "root": root, "root_matches": root_ok,
            "claimed_root": (claimed_root_hex or "").strip().lower()}


# ── CLI ─────────────────────────────────────────────────────────────────────────────────────────────
def _load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        obj = json.load(f)
    for wrap in ("receipt", "cert"):
        if isinstance(obj, dict) and wrap in obj and isinstance(obj[wrap], dict):
            return obj[wrap]
    return obj


def _default_log_path() -> str:
    return os.environ.get("APERTURE_TLOG", os.path.join(os.path.dirname(os.path.abspath(__file__)), "aperture_tlog.jsonl"))


def _leaf_of(path: str) -> str:
    import aperture_verify
    with open(path, "r", encoding="utf-8") as f:
        obj = json.load(f)
    for wrap in ("receipt", "cert"):
        if isinstance(obj, dict) and wrap in obj and isinstance(obj[wrap], dict):
            obj = obj[wrap]
            break
    return aperture_verify.leaf_hash(obj)


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd = argv[1]
    log = TransparencyLog(_default_log_path())
    if cmd == "add":
        leaf = _leaf_of(argv[2])
        idx = log.append(leaf)
        print("appended leaf %s at index %d" % (leaf[:16] + "…", idx))
        print(json.dumps(log.sth(), indent=2))
        return 0
    if cmd == "sth":
        print(json.dumps(log.sth(), indent=2))
        return 0
    if cmd == "prove":
        print(json.dumps(log.inclusion(int(argv[2])), indent=2))
        return 0
    if cmd == "check":
        leaf = _leaf_of(argv[2])
        idx = log.index_of(leaf)
        if idx < 0:
            print("  ✗ receipt leaf %s… is NOT in the log — unwitnessed" % leaf[:16])
            return 1
        inc = log.inclusion(idx)
        ok = verify_inclusion(bytes.fromhex(leaf), idx, log.size(),
                              [bytes.fromhex(p) for p in inc["audit_path"]], bytes.fromhex(log.root_hex()))
        print("  %s leaf %s… included at index %d/%d under root %s…"
              % ("✓" if ok else "✗", leaf[:16], idx, log.size(), log.root_hex()[:16]))
        return 0 if ok else 1
    if cmd == "session-root":                              # compute a session head over ordered receipts
        receipts = [_load_json(p) for p in argv[2:]]
        print(session_root(receipts))
        return 0
    if cmd == "verify-session":                            # verify-session <root> <receipt1> <receipt2> ...
        root = argv[2]
        receipts = [_load_json(p) for p in argv[3:]]
        rep = verify_session(receipts, root)
        if rep["ok"]:
            print("  ✓ SESSION VERIFIED — %d receipts, all signatures valid, root matches %s…"
                  % (rep["n"], rep["root"][:16]))
        else:
            why = []
            if rep["n_bad_sig"]:
                why.append("%d receipt(s) failed signature (indices %s)" % (rep["n_bad_sig"], rep["bad_sig_indices"]))
            if not rep["root_matches"]:
                why.append("root mismatch: recomputed %s… vs recorded %s…" % (rep["root"][:16], rep["claimed_root"][:16]))
            print("  ✗ SESSION NOT VERIFIED — %s" % "; ".join(why))
        return 0 if rep["ok"] else 1
    if cmd == "verify-checkpoint":                        # verify-checkpoint <checkpoint> [leaves.jsonl]
        try:
            cp = verify_checkpoint(open(argv[2], encoding="utf-8").read())
        except Exception as e:
            print("  \u2717 CHECKPOINT NOT VERIFIED \u2014 %s" % e)
            return 1
        print("  \u2713 checkpoint signed by the pinned receipt-log key: %s size %d root %s\u2026"
              % (cp["origin"], cp["tree_size"], cp["root"][:16]))
        if len(argv) > 3:
            lg = TransparencyLog(argv[3])
            n = cp["tree_size"]
            got = merkle_root(lg.leaves[:n]).hex() if lg.size() >= n else lg.root_hex()
            if lg.size() < n or got != cp["root"]:
                print("  \u2717 leaves file does NOT reproduce this checkpoint (%d leaves, root of first %d: %s\u2026)"
                      % (lg.size(), min(n, lg.size()), got[:16]))
                return 1
            if lg.size() == n:
                print("  \u2713 leaves file reproduces the checkpoint root")
            else:
                print("  \u2713 the first %d of the %d leaves in the leaves file reproduce the checkpoint root" % (n, lg.size()))
        return 0
    if cmd == "verify-anchor":                            # verify-anchor <checkpoint> <rekor_entry.json>
        try:
            rep = verify_rekor_anchor(open(argv[2], encoding="utf-8").read(), json.load(open(argv[3], encoding="utf-8")))
        except Exception as e:
            print("  \u2717 NOT ANCHORED \u2014 %s" % e)
            return 1
        print("  \u2713 ANCHORED \u2014 checkpoint %s size %d is Rekor v2 entry %d in %s (Rekor checkpoint size %d); Ed25519ph sig %s"
              % (rep["checkpoint"]["origin"], rep["checkpoint"]["tree_size"], rep["rekor"]["log_index"], rep["rekor"]["log"],
                 rep["rekor"]["tree_size"], rep["rekor"]["ed25519ph_sig"]))
        print("    proves: this log head existed and was published by the log key no later than Rekor logged it."
              " Does NOT prove any receipt's content is true.")
        return 0
    if cmd == "consistency":
        # consistency <old_checkpoint> <new_checkpoint> <proof.json>                    verify
        # consistency make <old_checkpoint> <new_checkpoint> <leaves.jsonl> [out.json]   generate (+ self-verify)
        if len(argv) >= 6 and argv[2] == "make":
            try:
                obj = make_consistency_proof(open(argv[3], encoding="utf-8").read(), open(argv[4], encoding="utf-8").read(),
                                             TransparencyLog(argv[5]).leaves)
            except Exception as e:
                print("  \u2717 NO PROOF \u2014 %s" % e)
                return 1
            txt = json.dumps(obj, indent=2) + "\n"
            if len(argv) > 6:
                with open(argv[6], "w", encoding="utf-8") as f:
                    f.write(txt)
                print("  \u2713 wrote %s: %d -> %d, %d hashes, self-verified" % (argv[6], obj["old_size"], obj["new_size"], len(obj["proof"])))
            else:
                sys.stdout.write(txt)
            return 0
        if len(argv) != 5:
            sys.stderr.write("usage: consistency <old_checkpoint> <new_checkpoint> <proof.json>\n"
                             "       consistency make <old_checkpoint> <new_checkpoint> <leaves.jsonl> [out.json]\n")
            return 2
        try:
            rep = verify_consistency_file(open(argv[2], encoding="utf-8").read(), open(argv[3], encoding="utf-8").read(),
                                          json.load(open(argv[4], encoding="utf-8")))
        except Exception as e:
            print("  \u2717 NOT CONSISTENT \u2014 %s" % e)
            return 1
        print("  \u2713 CONSISTENT \u2014 %s size %d root %s\u2026 is a prefix of size %d root %s\u2026 (%d-hash RFC 9162 proof; both checkpoints signed by the pinned receipt-log key)"
              % (rep["origin"], rep["old_size"], rep["old_root"][:16], rep["new_size"], rep["new_root"][:16], rep["proof_len"]))
        print("    proves: the log only appended between these two heads; no entry under the older head was changed or dropped.")
        return 0
    if cmd == "keys":
        for k, role in PINNED_LOG_VKEYS.items():
            print("  %s\n    %s" % (k, role))
        return 0
    if cmd == "selftest":
        return _selftest()
    sys.stderr.write("unknown command %r\n" % cmd)
    return 2


def _selftest() -> int:
    ok = 0
    total = 0
    leaves = [hashlib.sha256(b"receipt-%d" % i).digest() for i in range(11)]   # odd, non-power-of-two size
    root = merkle_root(leaves)
    # inclusion for every leaf
    inc_ok = all(verify_inclusion(leaves[i], i, len(leaves), inclusion_proof(i, leaves), root) for i in range(len(leaves)))
    total += 1; ok += inc_ok; print("  %s inclusion proofs verify for all 11 leaves" % ("✓" if inc_ok else "✗"))
    # tampered leaf fails
    bad = verify_inclusion(hashlib.sha256(b"forged").digest(), 3, len(leaves), inclusion_proof(3, leaves), root)
    total += 1; ok += (not bad); print("  %s a forged leaf fails its own proof" % ("✓" if not bad else "✗"))
    # consistency between every prefix and the full tree
    cons_ok = True
    for m in range(1, len(leaves)):
        first = merkle_root(leaves[:m])
        if not verify_consistency(m, len(leaves), consistency_proof(m, leaves), first, root):
            cons_ok = False; break
    total += 1; ok += cons_ok; print("  %s consistency proofs verify for every prefix (append-only)" % ("✓" if cons_ok else "✗"))
    # a rewritten history breaks consistency
    rewritten = leaves[:5] + [hashlib.sha256(b"swapped").digest()] + leaves[6:]
    broke = verify_consistency(5, len(leaves), consistency_proof(5, leaves), merkle_root(leaves[:5]), merkle_root(rewritten))
    total += 1; ok += (not broke); print("  %s a rewritten leaf breaks consistency vs the old root" % ("✓" if not broke else "✗"))
    # session trails: build 3 self-signed receipts, verify the trail against its root, then break it 3 ways
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    import base64
    sk = Ed25519PrivateKey.generate()
    pub = base64.b64encode(sk.public_key().public_bytes_raw()).decode()
    pin = {pub: "selftest"}
    def _mk(q):
        r = {"kind": "read", "query": q, "certificate": "GROUNDED"}
        canon = json.dumps(r, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        r["signature"] = {"alg": "ed25519", "pub": pub, "sig": base64.b64encode(sk.sign(canon)).decode()}
        return r
    sess = [_mk("q1"), _mk("q2"), _mk("q3")]
    sroot = session_root(sess)
    total += 1; hit = verify_session(sess, sroot, pinned=pin)["ok"]; ok += hit
    print("  %s a 3-receipt session verifies against its root" % ("✓" if hit else "✗"))
    tampered = json.loads(json.dumps(sess)); tampered[1]["certificate"] = "OFF_MAP"
    total += 1; hit = not verify_session(tampered, sroot, pinned=pin)["ok"]; ok += hit
    print("  %s tampering a receipt in the trail breaks the session" % ("✓" if hit else "✗"))
    total += 1; hit = not verify_session([sess[0], sess[2], sess[1]], sroot, pinned=pin)["root_matches"]; ok += hit
    print("  %s reordering the trail breaks the session root" % ("✓" if hit else "✗"))
    total += 1; hit = not verify_session([sess[0], sess[2]], sroot, pinned=pin)["root_matches"]; ok += hit
    print("  %s dropping a receipt breaks the session root" % ("✓" if hit else "✗"))
    # C2SP checkpoint: sign with an ephemeral key, verify; tampered size and unpinned key must fail
    import base64 as _b
    lsk = Ed25519PrivateKey.generate()
    lpub = lsk.public_key().public_bytes_raw()
    nm = "example.test/log"
    kid = _h(nm.encode(), b"\n\x01", lpub)[:4]
    vk = {"%s+%s+%s" % (nm, kid.hex(), _b.b64encode(b"\x01" + lpub).decode()): "selftest"}
    body = checkpoint_body(nm, len(leaves), root)
    note = (body + b"\n" + ("\u2014 %s %s\n" % (nm, _b.b64encode(kid + lsk.sign(body)).decode())).encode()).decode()
    total += 1; hit = verify_checkpoint(note, vk)["root"] == root.hex(); ok += hit
    print("  %s a C2SP checkpoint signed by a pinned log key verifies" % ("\u2713" if hit else "\u2717"))
    def _fails(n, v):
        try:
            verify_checkpoint(n, v); return False
        except ValueError:
            return True
    total += 1; hit = _fails(note.replace("\n%d\n" % len(leaves), "\n%d\n" % (len(leaves) - 1), 1), vk); ok += hit
    print("  %s a checkpoint with an altered tree size fails" % ("\u2713" if hit else "\u2717"))
    total += 1; hit = _fails(note, PINNED_LOG_VKEYS); ok += hit
    print("  %s a checkpoint signed by an unpinned key is rejected" % ("\u2713" if hit else "\u2717"))
    # consistency proof files: a valid one verifies, a tampered hash / swapped size fails
    vleaves = leaves + [hashlib.sha256(b"receipt-11").digest()]
    def _note(sz, rt):
        bd = checkpoint_body(nm, sz, rt)
        return (bd + b"\n" + ("\u2014 %s %s\n" % (nm, _b.b64encode(kid + lsk.sign(bd)).decode())).encode()).decode()
    n_old, n_new = _note(len(leaves), root), _note(len(vleaves), merkle_root(vleaves))
    pf = make_consistency_proof(n_old, n_new, vleaves, vk)
    def _cfails(o):
        try:
            verify_consistency_file(n_old, n_new, o, vk); return False
        except ValueError:
            return True
    bad = json.loads(json.dumps(pf)); bad["proof"][0] = ("%064x" % (int(bad["proof"][0], 16) ^ 1))
    total += 1; hit = (not _cfails(pf)) and _cfails(bad); ok += hit
    print("  %s a consistency proof file verifies; a one-bit-tampered proof fails" % ("\u2713" if hit else "\u2717"))
    # the published tree: every Rekor anchor, every consistency proof, and the head copy
    here = os.path.dirname(os.path.abspath(__file__))
    lg_dir = os.path.join(here, "log")
    cdir = os.path.join(lg_dir, "checkpoints")
    cps = {}
    if os.path.isdir(cdir):
        for f in os.listdir(cdir):
            if f.isdigit():
                cps[int(f)] = open(os.path.join(cdir, f), encoding="utf-8").read()
    elif os.path.exists(os.path.join(lg_dir, "checkpoint")):        # pre-per-size layout
        cps[-1] = open(os.path.join(lg_dir, "checkpoint"), encoding="utf-8").read()
    for f in sorted(os.listdir(lg_dir)) if os.path.isdir(lg_dir) else []:
        if not (f.startswith("rekor-entry-") and f.endswith(".json")):
            continue
        ent = json.load(open(os.path.join(lg_dir, f), encoding="utf-8"))
        hit, which = False, None
        for sz, note in sorted(cps.items()):
            try:
                hit = verify_rekor_anchor(note, ent)["ok"]; which = sz; break
            except (ValueError, KeyError):
                continue
        total += 1; ok += hit
        print("  %s %s anchors published checkpoint %s in Rekor v2" % ("\u2713" if hit else "\u2717", f, which if hit else "(none)"))
    pdir = os.path.join(lg_dir, "proofs")
    for f in sorted(os.listdir(pdir)) if os.path.isdir(pdir) else []:
        if not (f.startswith("consistency-") and f.endswith(".json")):
            continue
        try:
            m_, n_ = (int(x) for x in f[len("consistency-"):-len(".json")].split("-"))
            hit = verify_consistency_file(cps[m_], cps[n_], json.load(open(os.path.join(pdir, f), encoding="utf-8")))["ok"]
        except (ValueError, KeyError):
            hit = False
        total += 1; ok += hit
        print("  %s proofs/%s verifies against checkpoints/%s" % ("\u2713" if hit else "\u2717", f, f[len("consistency-"):-len(".json")].replace("-", " and ")))
    head = os.path.join(lg_dir, "checkpoint")
    if os.path.isdir(cdir) and cps and os.path.exists(head):
        hit = open(head, "rb").read() == open(os.path.join(cdir, str(max(cps))), "rb").read()
        total += 1; ok += hit
        print("  %s log/checkpoint is byte-identical to the newest checkpoints/%d" % ("\u2713" if hit else "\u2717", max(cps)))
    print("  transparency-log self-test: %d/%d passed" % (ok, total))
    return 0 if ok == total else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
