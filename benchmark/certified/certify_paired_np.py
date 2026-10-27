#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""certify.py with the vectorised held-out backend (heldout_np.evaluate_paired), for paired sets
too large for heldout.py's pure-Python refit. Same arguments, same procedure, same certificate,
plus tool.heldout_backend. heldout_np reproduces heldout.evaluate's permutation draws and
arithmetic (checked in tests/test_floor_v1.py)."""
import json, hashlib, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import certify as C  # noqa: E402
import heldout_np as HN  # noqa: E402


def stamp(out):
    """Mark a certify.py certificate in OUT as produced with the numpy held-out backend."""
    p = os.path.join(out, "certificate.json")
    with open(p) as f:
        cert = json.load(f)
    cert["mode"] = "paired"
    tool = cert.setdefault("tool", {})
    tool["heldout_backend"] = "heldout_np.evaluate_paired"
    with open(os.path.join(HERE, "heldout_np.py"), "rb") as f:
        tool["heldout_np_sha256"] = hashlib.sha256(f.read()).hexdigest()
    with open(os.path.abspath(__file__), "rb") as f:
        tool["certify_paired_np_sha256"] = hashlib.sha256(f.read()).hexdigest()
    cert["replay"] = cert["replay"].replace("certify.py", "certify_paired_np.py")
    with open(p, "w") as f:
        json.dump(cert, f, indent=1)


def main(argv):
    C.H.evaluate = lambda rows, n_perm=2000, seed=20261026: HN.evaluate_paired(rows, n_perm=n_perm, seed=seed)
    rc = C.main(argv)
    stamp(argv[argv.index("--out") + 1])
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
