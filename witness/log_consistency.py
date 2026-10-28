#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""log_consistency.py: keep the size-22 genesis snapshot and prove later checkpoints only append to it.

  archive  [--out witness/archive]                       snapshot the PUBLISHED checkpoint + leaves (verified)
  prove    NEW_CHECKPOINT NEW_LEAVES [--old CHECKPOINT --old-leaves LEAVES] [--proof-out FILE]
           both checkpoints must verify under the pinned log key; the old leaves must be an exact prefix of
           the new leaves; computes the RFC 6962 consistency proof old->new, verifies it, prints it
  verify   NEW_CHECKPOINT PROOF_FILE [--old CHECKPOINT]
           for monitors without the leaves: checks the signed roots and the proof only

Defaults: --old witness/archive/size-22/checkpoint, --old-leaves witness/archive/size-22/leaves.jsonl.
Paths may be local files, https:// or file:// URLs. Exit 0 = APPEND-ONLY, 1 = NOT append-only / bad.

A passing check proves the newer log head contains the archived 22 leaves unchanged, in order, at the
same positions. It says nothing about whether any receipt's content is true.
"""
import argparse
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import c2sp  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ARCHIVE = os.path.join(HERE, "archive")
GENESIS = os.path.join(ARCHIVE, "size-22")


def _read(path):
    if "://" in path:
        return c2sp.fetch(path)
    with open(path, "rb") as f:
        return f.read()


def _cp(path, vkey):
    return c2sp.verify_log_checkpoint(_read(path).decode("utf-8"), vkey)


def cmd_archive(a):
    note = c2sp.fetch(a.checkpoint_url)
    leaves_raw = c2sp.fetch(a.leaves_url)
    cp = c2sp.verify_log_checkpoint(note.decode(), a.log_vkey)
    leaves = c2sp.parse_leaves_jsonl(leaves_raw)
    if len(leaves) != cp["size"] or c2sp.merkle_root(leaves) != cp["root"]:
        print("  ✗ published leaves do not reproduce the published checkpoint; not archiving")
        return 1
    d = os.path.join(a.out, "size-%d" % cp["size"])
    os.makedirs(d, exist_ok=True)
    files = {"checkpoint": note, "leaves.jsonl": leaves_raw}
    for extra in ("leaves_manifest.json", "rekor-entry-0.json", "receipt-log.vkey"):
        try:
            files[extra] = c2sp.fetch(a.checkpoint_url.rsplit("/", 1)[0] + "/" + extra)
        except Exception as e:  # optional companions
            print("  (skipped %s: %s)" % (extra, e))
    for name, data in files.items():
        p = os.path.join(d, name)
        if os.path.exists(p) and open(p, "rb").read() != data:
            print("  ✗ %s already archived with DIFFERENT bytes; an archive is never overwritten" % p)
            return 1
        with open(p, "wb") as f:
            f.write(data)
    with open(os.path.join(d, "SHA256SUMS"), "w") as f:
        for name in sorted(files):
            f.write("%s  %s\n" % (hashlib.sha256(files[name]).hexdigest(), name))
    print("  ✓ archived %s size %d root %s in %s" % (cp["origin"], cp["size"], c2sp.b64(cp["root"]), d))
    return 0


def cmd_prove(a):
    old = _cp(a.old, a.log_vkey)
    new = _cp(a.new_checkpoint, a.log_vkey)
    if old["origin"] != new["origin"]:
        print("  ✗ different origins: %s vs %s" % (old["origin"], new["origin"])); return 1
    m, n = old["size"], new["size"]
    if m > n:
        print("  ✗ old checkpoint (%d) is larger than the new one (%d)" % (m, n)); return 1
    new_leaves = c2sp.parse_leaves_jsonl(_read(a.new_leaves))
    if len(new_leaves) < n:
        print("  ✗ new leaves file has %d leaves, checkpoint says %d" % (len(new_leaves), n)); return 1
    new_leaves = new_leaves[:n]
    if c2sp.merkle_root(new_leaves) != new["root"]:
        print("  ✗ new leaves do NOT reproduce the new checkpoint root"); return 1
    if a.old_leaves:
        old_leaves = c2sp.parse_leaves_jsonl(_read(a.old_leaves))
        if old_leaves != new_leaves[:m]:
            bad = next(i for i in range(min(m, len(old_leaves))) if old_leaves[i] != new_leaves[i]) \
                if any(old_leaves[i] != new_leaves[i] for i in range(min(m, len(old_leaves)))) else min(m, len(old_leaves))
            print("  ✗ NOT APPEND-ONLY: archived leaf %d differs from (or is missing in) the new log" % bad); return 1
    if c2sp.merkle_root(new_leaves[:m]) != old["root"]:
        print("  ✗ NOT APPEND-ONLY: the first %d new leaves do not hash to the archived root" % m); return 1
    proof = c2sp.consistency_proof(m, new_leaves)
    if not c2sp.verify_consistency(m, n, proof, old["root"], new["root"]):
        print("  ✗ consistency proof %d->%d does not verify" % (m, n)); return 1
    out = {"origin": new["origin"], "old_size": m, "old_root": c2sp.b64(old["root"]),
           "new_size": n, "new_root": c2sp.b64(new["root"]), "proof": [c2sp.b64(h) for h in proof]}
    if a.proof_out:
        with open(a.proof_out, "w") as f:
            json.dump(out, f, indent=1)
            f.write("\n")
    print("  ✓ APPEND-ONLY: %s size %d -> %d; both heads signed by the pinned log key; RFC 6962 proof (%d hashes) verifies"
          % (new["origin"], m, n, len(proof)))
    for h in out["proof"]:
        print("    " + h)
    return 0


def cmd_verify(a):
    old = _cp(a.old, a.log_vkey)
    new = _cp(a.new_checkpoint, a.log_vkey)
    pf = json.loads(_read(a.proof_file))
    if (pf["old_size"], pf["new_size"], pf["origin"]) != (old["size"], new["size"], new["origin"]):
        print("  ✗ proof file is for %s %d->%d, not %s %d->%d" % (pf["origin"], pf["old_size"], pf["new_size"],
              new["origin"], old["size"], new["size"])); return 1
    proof = [c2sp.b64dec(h) for h in pf["proof"]]
    if old["origin"] == new["origin"] and c2sp.verify_consistency(old["size"], new["size"], proof, old["root"], new["root"]):
        print("  ✓ APPEND-ONLY: %s %d -> %d (signed heads + proof, no leaves needed)" % (new["origin"], old["size"], new["size"]))
        return 0
    print("  ✗ NOT VERIFIED: proof does not link the two signed roots"); return 1


def main(argv=None):
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--log-vkey", default=c2sp.PRODUCTION_VKEY)
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("archive", parents=[common])
    s.add_argument("--checkpoint-url", default=c2sp.PUBLISHED_CHECKPOINT_URL)
    s.add_argument("--leaves-url", default=c2sp.PUBLISHED_LEAVES_URL)
    s.add_argument("--out", default=ARCHIVE)
    s = sub.add_parser("prove", parents=[common])
    s.add_argument("new_checkpoint"); s.add_argument("new_leaves")
    s.add_argument("--old", default=os.path.join(GENESIS, "checkpoint"))
    s.add_argument("--old-leaves", default=os.path.join(GENESIS, "leaves.jsonl"))
    s.add_argument("--proof-out")
    s = sub.add_parser("verify", parents=[common])
    s.add_argument("new_checkpoint"); s.add_argument("proof_file")
    s.add_argument("--old", default=os.path.join(GENESIS, "checkpoint"))
    a = p.parse_args(argv)
    try:
        return {"archive": cmd_archive, "prove": cmd_prove, "verify": cmd_verify}[a.cmd](a)
    except ValueError as e:
        print("  ✗ %s" % e); return 1


if __name__ == "__main__":
    raise SystemExit(main())
