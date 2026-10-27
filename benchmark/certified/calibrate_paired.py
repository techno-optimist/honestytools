#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""False-positive rate of paired mode on REAL pairs with labels coin-flipped within each pair
(so the null is true by construction). Usage: calibrate_paired.py PAIRS.json SEEDS NPERM"""
import json, os, random, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
import nulltest_paired as P
rows = json.load(open(sys.argv[1])); S = int(sys.argv[2]); B = int(sys.argv[3])
rej = 0; ps = []
for seed in range(S):
    r = random.Random(1000 + seed); it = []
    for i, x in enumerate(rows):
        a, b = (x["right"], x["wrong"]) if r.random() < .5 else (x["wrong"], x["right"])
        it += [{"text": a, "label": 0, "_row": i}, {"text": b, "label": 1, "_row": i}]
    rep = P.evaluate_paired(it, "text", "label", "_row", None, n_perm=B, seed=seed, jobs=8)
    ps.append(rep["worst_cell"]["adj_p"]); rej += rep["verdict"] == "DISQUALIFIED"
print(json.dumps({"pairs_file": os.path.basename(sys.argv[1]), "n_pairs": len(rows), "seeds": S,
                  "n_perm": B, "rejections": rej, "rate": rej / S, "adj_p": ps}))
