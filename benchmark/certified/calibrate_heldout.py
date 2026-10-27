#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Calibrate the held-out family: real pair texts, labels coin-flipped inside every pair (so the
null is true by construction). Reports the share of seeds the family rejects at alpha 0.05.

    python3 benchmark/certified/calibrate_heldout.py PAIRS.json --seeds 100 --n-perm 200 --out FILE
"""
import argparse, json, os, random, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import heldout as H  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("pairs"); ap.add_argument("--seeds", type=int, default=100)
ap.add_argument("--n-perm", type=int, default=200); ap.add_argument("--out", required=True)
a = ap.parse_args()
rows = json.load(open(a.pairs))
rej, negrej, adj = 0, 0, []
for s in range(a.seeds):
    rng = random.Random(1000 + s)
    sw = [dict(r, right=r["wrong"], wrong=r["right"]) if rng.random() < 0.5 else r for r in rows]
    o = H.evaluate(sw, n_perm=a.n_perm, seed=5000 + s)
    rej += o["verdict"] == "DISQUALIFIED"
    negrej += o["negation_check"]["cue_present"]
    adj.append(o["probes"][o["worst"]]["adj_p"])
out = {"pairs_file": os.path.basename(a.pairs), "n_pairs": len(rows), "seeds": a.seeds, "n_perm": a.n_perm,
       "family_rejections": rej, "family_rate": rej / a.seeds,
       "negation_rejections": negrej, "negation_rate": negrej / a.seeds, "worst_adj_p": adj}
json.dump(out, open(a.out, "w"), indent=0)
print({k: v for k, v in out.items() if k != "worst_adj_p"})
