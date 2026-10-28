#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""testlog.py: a throwaway C2SP log for tests (ephemeral Ed25519 key, random leaves). Never the real key.

  python3 witness/testlog.py OUTDIR --origin test.invalid/e2e --sizes 5,22,37 [--fork-at 3 --fork-size 40]

Writes OUTDIR/{vkey, leaves.jsonl, checkpoint-<N> for each size} and, with --fork-at, a forked history
OUTDIR/fork/{leaves.jsonl, checkpoint-<fork-size>} signed by the same key (leaf <fork-at> rewritten).
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import c2sp  # noqa: E402


def make_log(outdir, origin, sizes, fork_at=None, fork_size=None, seed=None):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    sk = Ed25519PrivateKey.generate()
    pub = sk.public_key().public_bytes_raw()
    kid = c2sp.key_id(origin, c2sp.SIG_ED25519, pub)
    vkey = "%s+%s+%s" % (origin, kid.hex(), c2sp.b64(b"\x01" + pub))
    n = max(sizes + ([fork_size] if fork_size else []))
    leaves = [os.urandom(32) for _ in range(n)]

    def sign(lv, size):
        body = c2sp.tl.checkpoint_body(origin, size, c2sp.merkle_root(lv[:size])).decode()
        return body + "\n" + "%s %s %s\n" % (c2sp.EM_DASH, origin, c2sp.b64(kid + sk.sign(body.encode())))

    def write(d, lv, szs):
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "leaves.jsonl"), "w") as f:
            for i, l in enumerate(lv):
                f.write(json.dumps({"index": i, "leaf": l.hex()}) + "\n")
        for s in szs:
            with open(os.path.join(d, "checkpoint-%d" % s), "w") as f:
                f.write(sign(lv, s))

    write(outdir, leaves[:max(sizes)], sizes)
    with open(os.path.join(outdir, "vkey"), "w") as f:
        f.write(vkey + "\n")
    if fork_at is not None:
        forked = list(leaves[:fork_size])
        forked[fork_at] = os.urandom(32)
        write(os.path.join(outdir, "fork"), forked, [fork_size])
    return vkey


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("outdir"); p.add_argument("--origin", required=True)
    p.add_argument("--sizes", default="5,22,37")
    p.add_argument("--fork-at", type=int); p.add_argument("--fork-size", type=int)
    a = p.parse_args()
    print(make_log(a.outdir, a.origin, [int(x) for x in a.sizes.split(",")], a.fork_at, a.fork_size))
