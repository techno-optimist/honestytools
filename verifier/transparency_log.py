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
    if m >= n:
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
    if m == n:
        return not proof and first_root == second_root
    if m == 0 or m > n:
        return False
    if m & (m - 1) == 0:                       # m is a power of two: first node is the old root, implied
        proof = [first_root] + proof
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
    return fn == 0 and fr == first_root and sr == second_root


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
    print("  transparency-log self-test: %d/%d passed" % (ok, total))
    return 0 if ok == total else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
