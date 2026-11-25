#!/usr/bin/env python3
"""Check witness co-signatures on a honesty.tools receipt-log checkpoint, offline.

    python3 verify_cosignatures.py log/cosigned/23

Checks, with no network call:
  1. the checkpoint is signed by the pinned receipt-log key (C2SP signed-note, Ed25519, type 0x01);
  2. each witness line verifies under that witness's key in log/witnesses.txt
     (C2SP tlog-cosignature, cosignature/v1, type 0x04);
  3. if log/checkpoints/<size> exists, the co-signed body is byte-identical to it;
  4. every header, vkey, root and signature line uses strict canonical ASCII base64 and exact lengths.

A co-signature means: that witness saw this log head at that time, and it was consistent with every
earlier head that witness had signed for this log. It says nothing about whether any entry is true.
Needs Python 3.9+ and the `cryptography` package.
"""
import hashlib, os, struct, sys
from datetime import datetime, timezone
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from transparency_log import _parse_canonical_uint, _split_note, decode_b64_strict  # noqa: E402

LOG_VKEY = "www.honesty.tools/receipts+fcd79bdd+AUF/J2nQKn90Ba52NjK0jqgIYayocRQGAYsK7PUdD7+C"  # = log/receipt-log.vkey


def parse_vkey(vkey, kind):
    parts = vkey.strip().split("+", 2)
    if len(parts) != 3:
        raise ValueError(f"bad vkey {vkey!r}")
    name, kid, b64 = parts
    if not name or len(kid) != 8 or kid != kid.lower():
        raise ValueError(f"bad vkey {vkey!r}")
    try:
        kid_bytes = bytes.fromhex(kid)
    except ValueError as exc:
        raise ValueError(f"bad vkey {vkey!r}") from exc
    raw = decode_b64_strict(b64, expected_len=33, label=f"vkey {name}")
    if raw[0] != kind or hashlib.sha256(name.encode() + b"\n" + raw).digest()[:4] != kid_bytes:
        raise ValueError(f"bad vkey {vkey!r}")
    return name, kid_bytes, Ed25519PublicKey.from_public_bytes(raw[1:])


def main(path):
    here = os.path.dirname(os.path.abspath(path))
    try:
        note = open(path, encoding="utf-8").read()
        body_str, lines = _split_note(note)
        body_lines = body_str.split("\n")[:-1]
        if len(body_lines) < 3:
            raise ValueError("checkpoint body needs origin, size, root")
        origin = body_lines[0]
        if not origin or any(c.isspace() for c in origin):
            raise ValueError("malformed checkpoint origin")
        size_num = _parse_canonical_uint(body_lines[1], "checkpoint size")
        size = body_lines[1]
        root = decode_b64_strict(body_lines[2], expected_len=32, label="checkpoint root")
        if size_num == 0 and root != hashlib.sha256(b"").digest():
            raise ValueError("size-0 checkpoint root must equal SHA-256 of empty input")
    except ValueError as exc:
        print(f"  \u2717 {exc}")
        return 1

    body = body_str.encode()
    ok = True
    try:
        name, kid, key = parse_vkey(LOG_VKEY, 0x01)
    except ValueError as exc:
        print(f"  \u2717 {exc}")
        return 1
    good = False
    bad_log_sig = False
    for l in lines:
        _, who, sig = l.split(" ")
        if who != name:
            continue
        blob = decode_b64_strict(sig, label=f"signature {who}")
        if len(blob) != 68:
            print(f"  \u2717 NON-CANONICAL ENCODING (signature {who}): expected 68 bytes for log key, got {len(blob)}")
            bad_log_sig = True
            continue
        if blob[:4] == kid:
            try:
                key.verify(blob[4:], body)
                good = True
            except Exception:
                bad_log_sig = True
    good = good and not bad_log_sig
    print(("  \u2713" if good else "  \u2717") + f" log signature ({name}) on {origin} size {size}")
    ok &= good
    wfile = os.path.join(here, "..", "witnesses.txt") if os.path.basename(here) == "cosigned" else os.path.join(here, "witnesses.txt")
    for vkey in open(wfile, encoding="utf-8"):
        if not vkey.strip() or vkey.startswith("#"):
            continue
        try:
            name, kid, key = parse_vkey(vkey, 0x04)
        except ValueError as exc:
            print(f"  \u2717 {exc}")
            ok = False
            continue
        when = None
        witness_bad = False
        for l in lines:
            _, who, sig = l.split(" ")
            if who != name:
                continue
            blob = decode_b64_strict(sig, label=f"signature {who}")
            if len(blob) != 76:
                print(f"  \u2717 NON-CANONICAL ENCODING (signature {who}): expected 76 bytes for witness key, got {len(blob)}")
                witness_bad = True
                continue
            if blob[:4] != kid:
                continue
            t = struct.unpack(">Q", blob[4:12])[0]
            try:
                key.verify(blob[12:], f"cosignature/v1\ntime {t}\n".encode() + body)
                when = t
            except Exception:
                witness_bad = True
        if witness_bad:
            print(f"  \u2717 {name}: invalid co-signature on this checkpoint")
            ok = False
        elif when is None:
            print(f"  - {name}: no valid co-signature on this checkpoint")
        else:
            print(f"  \u2713 {name} co-signed at {datetime.fromtimestamp(when, timezone.utc):%Y-%m-%d %H:%M:%S} UTC")
    pinned = os.path.join(here, "..", "checkpoints", size) if os.path.basename(here) == "cosigned" else None
    if pinned and os.path.exists(pinned):
        same = open(pinned, "rb").read().split(b"\n\n")[0] + b"\n" == body
        print(("  \u2713" if same else "  \u2717") + f" body matches log/checkpoints/{size}")
        ok &= same
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1]))
