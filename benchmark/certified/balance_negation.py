#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""FLOOR Phase 1b option A: select existing pairs so the negation cue is balanced within each
category. No text is rewritten.

    python3 benchmark/certified/balance_negation.py SOURCE.json OUT.json [--seed N]

For each pair, d = sign(negation count of the right answer - negation count of the wrong one),
using heldout.py's negation lexicon. Within every category, all d = 0 pairs are kept, and the
larger of the d = +1 / d = -1 groups is subsampled (seeded) down to the size of the smaller.
The result has exactly as many 'right more negated' as 'wrong more negated' pairs per category,
so the negation sign test on it is balanced BY CONSTRUCTION (that gate becomes circular for this
split); the other held-out probes and the in-family tests were not used to select.
"""
import json, os, random, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import heldout as H  # noqa: E402

SEED = 20261027


def neg(t):
    return H.fixed_scores(t)["negation"]


def balance(rows, seed=SEED):
    rng = random.Random(seed)
    by = {}
    for i, r in enumerate(rows):
        d = neg(r["right"]) - neg(r["wrong"])
        by.setdefault(r.get("cat", "_all"), {1: [], -1: [], 0: []})[(d > 0) - (d < 0)].append(i)
    keep, report = [], {}
    for cat in sorted(by):
        g = by[cat]
        m = min(len(g[1]), len(g[-1]))
        pos = sorted(rng.sample(g[1], m))
        negs = sorted(rng.sample(g[-1], m))
        keep += g[0] + pos + negs
        report[cat] = {"ties": len(g[0]), "right_more_neg": len(g[1]), "wrong_more_neg": len(g[-1]), "kept_each_side": m}
    keep.sort()
    return [rows[i] for i in keep], report


def main(argv):
    seed = SEED
    if "--seed" in argv:
        seed = int(argv[argv.index("--seed") + 1])
    rows = json.load(open(argv[1]))
    kept, report = balance(rows, seed)
    json.dump(kept, open(argv[2], "w"), ensure_ascii=False, indent=0)
    tot = {k: sum(v[k] for v in report.values()) for k in ("ties", "right_more_neg", "wrong_more_neg", "kept_each_side")}
    json.dump({"seed": seed, "n_source": len(rows), "n_kept": len(kept), "totals": tot, "by_category": report},
              open(os.path.splitext(argv[2])[0] + ".selection.json", "w"), indent=1)
    print(json.dumps({"n_source": len(rows), "n_kept": len(kept), "totals": tot}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
