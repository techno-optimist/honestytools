#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""aperture-verify — the open, offline verifier for Aperture signed receipts.

Verify any honesty.tools **read receipt** or **calibration certificate** against the pinned
registry ed25519 key — no call back to us, `cryptography` + stdlib only, Python >= 3.9.

    # verify a receipt you already hold (offline, nothing leaves your machine):
    python3 aperture_verify.py verify receipt.json

    # fetch a receipt/cert by id or URL and verify it:
    python3 aperture_verify.py fetch db8f162c66c3        # read receipt or cert id (read is tried first)
    python3 aperture_verify.py fetch https://www.honesty.tools/api/cert/ad55072b6a30

    # print the transparency-log leaf (the content hash a witness log records):
    python3 aperture_verify.py leaf receipt.json

    # show the pinned keys / run the self-test:
    python3 aperture_verify.py keys
    python3 aperture_verify.py selftest

WHAT A GREEN CHECK MEANS (and what it does NOT):
  ✓ the receipt was signed by the Aperture registry key and not altered by one byte since — provenance.
  ✗ it does NOT mean the answer is *true*. A receipt binds provenance, not truth: it proves what the lens
    read / what was served, signed so no one — including us — can quietly rewrite it. "Verified" as a
    verdict is earned elsewhere (registry grounding or cross-model agreement), never by this signature.

THE CANONICAL RECIPE (aperture.receipt.verify/v1), replicated from cert_store._sign_payload:
  1. take the receipt/cert object R
  2. remove the fields that are not part of the signed body: `signature`, and `id` (a content hash
     minted AFTER signing)
  3. canon = json.dumps(R_stripped, sort_keys=True, separators=(",",":"), ensure_ascii=False).encode()
  4. ed25519-verify base64(signature.sig) over `canon` with base64(signature.pub)
  5. REQUIRE signature.pub to be a PINNED registry key — a valid signature by an unknown key is a forgery
"""
from __future__ import annotations

import base64
import hashlib
import json
import sys
import urllib.error
import urllib.request

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.exceptions import InvalidSignature
except Exception:                                            # pragma: no cover
    sys.stderr.write("aperture-verify needs `cryptography`:  pip install cryptography\n")
    raise SystemExit(2)

SPEC_VERSION = "aperture.receipt.verify/v1"
SITE = "https://www.honesty.tools"

# ── PINNED registry keys (base64 ed25519 public keys). A signature is only trusted if its `pub` is one of
#    these — a correct signature under any other key is a forgery. Cross-check against SITE/api/cert/pubkey;
#    they should match, but the pin is what makes the verifier safe when you DON'T trust the network. ──
PINNED_KEYS = {
    "U9pHq3UwrFT5oyLwOnSgt+J6YpCkG1zUEcOO4Sxm7hA=": "registry (2f48ea75c592) — /api/read receipts + verify/photon primary certs",
    "h9ee/lLc0OXghSwtB1CtYQEC8mblSTzFoV5lOIJ8bSw=": "lucidia axis-cert (fe8416f4bc70) — harness sidecar honesty_receipt",
}
# fields present in a receipt/cert that are NOT part of the signed body (id is a content hash minted after)
_UNSIGNED_FIELDS = ("signature", "id")


class VerifyError(Exception):
    """Raised when a receipt fails verification — the message states exactly why."""


def canonical_body(obj: dict) -> bytes:
    """The exact bytes that were signed: the object minus the unsigned fields, canonicalized."""
    body = {k: v for k, v in obj.items() if k not in _UNSIGNED_FIELDS}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def leaf_hash(obj: dict) -> str:
    """The transparency-log leaf for a receipt: sha256 over the canonical *signed* object (body + signature,
    without the mutable id). Stable across who fetches it — the value a witness log records so that later
    suppression or a swapped signature is third-party detectable. Domain-separated so a receipt leaf can
    never collide with a raw file hash."""
    signed = {k: v for k, v in obj.items() if k != "id"}
    blob = json.dumps(signed, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(b"aperture.receipt.leaf/v1\x00" + blob).hexdigest()


def verify_receipt(obj: dict, *, pinned: dict | None = None) -> dict:
    """Verify one receipt/cert dict. Returns a report dict on success; raises VerifyError on any failure.
    Pure and offline — never touches the network."""
    pinned = PINNED_KEYS if pinned is None else pinned
    if not isinstance(obj, dict):
        raise VerifyError("not a JSON object")
    sig = obj.get("signature")
    if not isinstance(sig, dict) or "sig" not in sig or "pub" not in sig:
        raise VerifyError("no ed25519 signature block ({alg,pub,sig}) — an unsigned receipt is theater")
    if sig.get("alg", "ed25519") != "ed25519":
        raise VerifyError("unsupported signature alg %r (expected ed25519)" % sig.get("alg"))
    pub_b64 = sig["pub"]
    key_role = pinned.get(pub_b64)
    if key_role is None:
        raise VerifyError("signed by an UNKNOWN key %s… — not a pinned Aperture registry key (forgery)"
                          % pub_b64[:16])
    try:
        pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(pub_b64))
        raw_sig = base64.b64decode(sig["sig"])
    except Exception as e:
        raise VerifyError("malformed key or signature base64: %s" % e)
    canon = canonical_body(obj)
    try:
        pub.verify(raw_sig, canon)
    except InvalidSignature:
        raise VerifyError("SIGNATURE INVALID — the receipt was altered, or the signature does not match")
    # id-binding (best effort): a read-receipt id is sha256(id_blob + 'aperture-read')[:12], where id_blob is
    # the signed object (incl. signature) canonicalized with sort_keys but DEFAULT separators — matching
    # cert_store.mint_read (which does NOT compact the blob, unlike the signature body). Certs use a different
    # salt, so this is informational only; the signature above is the load-bearing check.
    id_ok = None
    rid = obj.get("id")
    if isinstance(rid, str):
        id_blob = json.dumps({k: v for k, v in obj.items() if k != "id"},
                             sort_keys=True, ensure_ascii=False).encode("utf-8")
        cand = hashlib.sha256(id_blob + b"aperture-read").hexdigest()[:12]
        id_ok = (cand == rid.lower())
    return {"ok": True, "spec": SPEC_VERSION, "key": pub_b64, "key_role": key_role,
            "id": rid, "id_binding": ("read-id ok" if id_ok else ("read-id n/a" if id_ok is None else "not a read-id salt")),
            "leaf": leaf_hash(obj),
            "kind": obj.get("kind") or ("certificate" if "grade" in obj else "receipt"),
            "bytes_signed": len(canon)}


# ── CLI ─────────────────────────────────────────────────────────────────────────────────────────────
def _load(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        obj = json.load(f)
    # accept a raw receipt, or an API envelope {"receipt": {...}} / {"cert": {...}}
    for wrap in ("receipt", "cert", "read"):
        if isinstance(obj, dict) and wrap in obj and isinstance(obj[wrap], dict) and "signature" in obj[wrap]:
            return obj[wrap]
    return obj


def _fetch(idc: str) -> dict:
    if idc.startswith("http://") or idc.startswith("https://"):
        url = idc
    elif len(idc) == 12 and all(c in "0123456789abcdef" for c in idc.lower()):
        # read receipts and certificates both use 12-hex ids: try the read receipt first, then the cert
        try:
            with urllib.request.urlopen("%s/api/read/%s" % (SITE, idc), timeout=20) as r:
                env = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
            with urllib.request.urlopen("%s/api/cert/%s" % (SITE, idc), timeout=20) as r:
                env = json.loads(r.read().decode("utf-8"))
        url = None
    else:
        url = "%s/api/cert/%s" % (SITE, idc)
    if url is not None:
        with urllib.request.urlopen(url, timeout=20) as r:
            env = json.loads(r.read().decode("utf-8"))
    for wrap in ("receipt", "cert"):
        if isinstance(env, dict) and wrap in env and isinstance(env[wrap], dict):
            return env[wrap]
    return env


def _report(rep: dict) -> None:
    print("  \033[32m✓ VERIFIED\033[0m — signed by the Aperture registry, unaltered.")
    print("    key      %s\n             %s" % (rep["key"], rep["key_role"]))
    print("    id       %s   (%s)" % (rep["id"], rep["id_binding"]))
    print("    kind     %s · %d bytes signed" % (rep["kind"], rep["bytes_signed"]))
    print("    leaf     %s" % rep["leaf"])
    print("    note     provenance, not truth — this proves what was signed, not that the answer is correct.")


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd = argv[1]
    try:
        if cmd == "verify":
            _report(verify_receipt(_load(argv[2])))
            return 0
        if cmd == "fetch":
            obj = _fetch(argv[2])
            _report(verify_receipt(obj))
            return 0
        if cmd == "leaf":
            print(leaf_hash(_load(argv[2])))
            return 0
        if cmd == "keys":
            print("Pinned Aperture registry keys (%s):" % SPEC_VERSION)
            for k, role in PINNED_KEYS.items():
                print("  %s  %s" % (k, role))
            print("Cross-check: %s/api/cert/pubkey" % SITE)
            return 0
        if cmd == "selftest":
            return _selftest()
        sys.stderr.write("unknown command %r — try: verify | fetch | leaf | keys | selftest\n" % cmd)
        return 2
    except VerifyError as e:
        print("  \033[31m✗ NOT VERIFIED\033[0m — %s" % e)
        return 1
    except FileNotFoundError as e:
        sys.stderr.write("file not found: %s\n" % e.filename)
        return 2
    except urllib.error.URLError as e:
        sys.stderr.write("could not fetch (%s).\nThe offline path is the point — download the receipt yourself and run:\n"
                         "  curl -s %s/api/read/<id> | python3 -c 'import sys,json;json.dump(json.load(sys.stdin)[\"receipt\"],open(\"r.json\",\"w\"))'\n"
                         "  python3 aperture_verify.py verify r.json\n" % (e.reason, SITE))
        return 2


def _selftest() -> int:
    """Round-trip: sign a payload with a throwaway key that we temporarily pin, verify it, then prove a
    one-byte tamper and a wrong-key signature are both rejected. No network."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    sk = Ed25519PrivateKey.generate()
    pub_b64 = base64.b64encode(sk.public_key().public_bytes_raw()).decode()
    rec = {"kind": "read", "query": "self-test", "certificate": "GROUNDED", "minted_at": "2026-07-16T00:00:00Z"}
    canon = json.dumps(rec, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    rec["signature"] = {"alg": "ed25519", "pub": pub_b64, "sig": base64.b64encode(sk.sign(canon)).decode()}
    pin = {pub_b64: "selftest key"}
    ok = 0
    try:
        verify_receipt(rec, pinned=pin); print("  ✓ valid receipt verifies"); ok += 1
    except VerifyError as e:
        print("  ✗ FAIL valid receipt: %s" % e)
    tampered = json.loads(json.dumps(rec)); tampered["certificate"] = "OFF_MAP"
    try:
        verify_receipt(tampered, pinned=pin); print("  ✗ FAIL tamper not caught")
    except VerifyError:
        print("  ✓ one-byte tamper rejected"); ok += 1
    try:
        verify_receipt(rec, pinned={})     # empty pin = key not trusted
        print("  ✗ FAIL unknown key accepted")
    except VerifyError:
        print("  ✓ unknown/unpinned key rejected"); ok += 1
    print("  self-test: %d/3 passed" % ok)
    return 0 if ok == 3 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
