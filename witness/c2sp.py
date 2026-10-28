# SPDX-License-Identifier: Apache-2.0
"""Small C2SP helpers shared by witness_client.py and log_consistency.py.

Reuses the RFC 6962 tree code and the pinned-key checkpoint verifier in ../verifier/transparency_log.py
(that file is a byte-for-byte mirror of what www.honesty.tools serves, so it is imported, never edited).

Specs (read 2026-10-28):
  signed-note        https://c2sp.org/signed-note        (vkeys, key IDs, signature types 0x01 / 0x04)
  tlog-checkpoint    https://c2sp.org/tlog-checkpoint     (origin, size, base64 root)
  tlog-cosignature   https://c2sp.org/tlog-cosignature    (cosignature/v1 message, 8-byte timestamp prefix)
  tlog-witness       https://c2sp.org/tlog-witness        (POST add-checkpoint, 409 text/x.tlog.size, 422 ...)
"""
import base64
import hashlib
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
VDIR = os.path.join(os.path.dirname(HERE), "verifier")
if VDIR not in sys.path:
    sys.path.insert(0, VDIR)
import transparency_log as tl  # noqa: E402

PRODUCTION_ORIGIN = "www.honesty.tools/receipts"
PRODUCTION_VKEY = "www.honesty.tools/receipts+fcd79bdd+AUF/J2nQKn90Ba52NjK0jqgIYayocRQGAYsK7PUdD7+C"
# Where the log's checkpoint and tree data are actually published. NOTE: the origin line
# www.honesty.tools/receipts is a name, not a URL; https://www.honesty.tools/receipts/checkpoint is a 404.
PUBLISHED_BASE = "https://www.honesty.tools/verifier/log/"
PUBLISHED_CHECKPOINT_URL = PUBLISHED_BASE + "checkpoint"
PUBLISHED_LEAVES_URL = PUBLISHED_BASE + "leaves.jsonl"

SIG_ED25519 = 0x01
SIG_COSIGNATURE_V1 = 0x04
SIG_MLDSA44_COSIG = 0x06
EM_DASH = "\u2014"
USER_AGENT = "honestytools-witness-client/0.1 (+https://www.honesty.tools/about; hello@honesty.tools)"


def b64dec(s: str) -> bytes:
    """Strict, canonical base64 (tlog-witness: decoders MUST reject non-canonical encodings)."""
    raw = base64.b64decode(s, validate=True)
    if base64.b64encode(raw).decode() != s:
        raise ValueError("non-canonical base64: %r" % s[:24])
    return raw


def b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def key_id(name: str, sig_type: int, pub: bytes) -> bytes:
    return hashlib.sha256(name.encode() + b"\n" + bytes([sig_type]) + pub).digest()[:4]


def parse_vkey(vkey: str):
    """'<name>+<hex id>+<b64(type||pub)>' -> (name, sig_type, key_id bytes, pub). Checks the key ID."""
    name, kid_hex, blob_b64 = vkey.strip().split("+", 2)
    blob = b64dec(blob_b64)
    sig_type, pub = blob[0], blob[1:]
    if sig_type in (SIG_ED25519, SIG_COSIGNATURE_V1) and len(pub) != 32:
        raise ValueError("ed25519 vkey must carry a 32-byte key")
    kid = key_id(name, sig_type, pub)
    if kid.hex() != kid_hex.lower():
        raise ValueError("vkey key ID %s does not match SHA-256(name||0x0A||type||key)[:4]=%s" % (kid_hex, kid.hex()))
    return name, sig_type, kid, pub


def split_note(note: str):
    """Signed note -> (body text incl. final newline, [signature lines])."""
    if not note.endswith("\n"):
        raise ValueError("signed note must end in a newline")
    if "\n\n" not in note:
        raise ValueError("not a signed note (no blank line before signatures)")
    text, sigs = note.split("\n\n", 1)
    return text + "\n", [l for l in sigs.split("\n") if l]


def parse_checkpoint_body(text: str):
    lines = text.split("\n")[:-1]
    if len(lines) < 3:
        raise ValueError("checkpoint body needs origin, size, root")
    origin, size_s, root_b64 = lines[0], lines[1], lines[2]
    size = int(size_s)
    if str(size) != size_s or size < 0:
        raise ValueError("tree size is not a canonical decimal")
    root = b64dec(root_b64)
    if len(root) != 32:
        raise ValueError("root hash must be 32 bytes")
    return origin, size, root


def verify_log_checkpoint(note: str, log_vkey: str) -> dict:
    """Verify the log's own signature (type 0x01) under ONE pinned vkey; unknown signatures ignored."""
    cp = tl.verify_checkpoint(note, {log_vkey: "pinned"})
    origin, size, root = parse_checkpoint_body(cp["body"])
    return {"origin": origin, "size": size, "root": root, "body": cp["body"], "note": note}


def cosignature_message(origin: str, size: int, root: bytes, timestamp: int) -> bytes:
    return ("cosignature/v1\ntime %d\n%s\n%d\n%s\n" % (timestamp, origin, size, b64(root))).encode()


def verify_cosignature_lines(lines, witness_vkey: str, origin: str, size: int, root: bytes):
    """Return [(line, timestamp)] for every line that is a VALID cosignature/v1 by witness_vkey over
    (origin, size, root). Lines from unknown keys are ignored (tlog-witness: client MUST ignore them).
    A line whose name+key ID match but whose signature fails raises (that is a malformed note)."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.exceptions import InvalidSignature
    name, sig_type, kid, pub = parse_vkey(witness_vkey)
    if sig_type != SIG_COSIGNATURE_V1:
        raise ValueError("witness vkey type 0x%02x is not cosignature/v1 (0x04); ML-DSA-44 (0x06) is not "
                         "supported by this client yet" % sig_type)
    good = []
    for line in lines:
        if not line.strip():
            continue
        parts = line.split(" ")
        if len(parts) != 3 or parts[0] != EM_DASH:
            raise ValueError("malformed signature line: %r" % line[:60])
        if parts[1] != name:
            continue
        blob = b64dec(parts[2])
        if blob[:4] != kid:
            continue
        if len(blob) != 4 + 8 + 64:
            raise ValueError("cosignature/v1 must be key ID + 8-byte timestamp + 64-byte signature")
        ts = int.from_bytes(blob[4:12], "big")
        try:
            Ed25519PublicKey.from_public_bytes(pub).verify(blob[12:], cosignature_message(origin, size, root, ts))
        except InvalidSignature:
            raise ValueError("cosignature from %s does NOT verify over %s size %d" % (name, origin, size))
        good.append((line, ts))
    return good


def fetch(url: str, timeout: int = 30) -> bytes:
    if url.startswith("file://"):
        with open(url[len("file://"):], "rb") as f:
            return f.read()
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # follows the honesty.tools -> www 301
        return r.read()


def parse_leaves_jsonl(data: bytes):
    leaves = []
    for i, line in enumerate(l for l in data.decode().split("\n") if l.strip()):
        obj = json.loads(line)
        if obj.get("index") != i:
            raise ValueError("leaves file index %r at position %d (must be contiguous from 0)" % (obj.get("index"), i))
        leaf = bytes.fromhex(obj["leaf"])
        if len(leaf) != 32:
            raise ValueError("leaf %d is not 32 bytes" % i)
        leaves.append(leaf)
    return leaves


def merkle_root(leaves):
    return tl.merkle_root(leaves) if leaves else hashlib.sha256(b"").digest()


def consistency_proof(m: int, leaves):
    """RFC 6962 PROOF(m, D[n]) with n = len(leaves); empty for m == 0 or m == n."""
    if m == 0:
        return []
    return tl.consistency_proof(m, leaves)


def verify_consistency(m: int, n: int, proof, old_root: bytes, new_root: bytes) -> bool:
    if m == 0:
        return not proof
    return tl.verify_consistency(m, n, proof, old_root, new_root)
