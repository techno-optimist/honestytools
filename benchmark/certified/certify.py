#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Certified suite, prototype: filter a paired right/wrong benchmark to the pairs that carry no
surface cue, then certify the survivors with nulltest (paired AND unpaired) and write a
certificate JSON.

    python3 benchmark/certified/certify.py PAIRS.json --out DIR [--name NAME]

PAIRS.json: list of {"right": str, "wrong": str, "cat": str, ...}; one row is one pair.

PROCEDURE (pair-level adversarial filtering, in the spirit of AFLite, Le Bras et al. 2020,
arXiv 2002.04108):
  1. Score every pair out-of-fold with the fitted and unfitted surface probes, over FILTER_SEEDS
     different pair-grouped fold splits. A probe 'calls' a pair when it ranks the members in the
     direction that probe favours across the whole set.
  2. A pair's cue rate = share of (probe, seed) calls that are correct. Each round drops the
     STEP share of pairs whose cue rate is furthest from 1/2 (BOTH tails), then re-screens with
     cheap paired + unpaired nulltest runs (SCREEN_PERM, a separate seed) and stops once both
     sit at adj p >= SCREEN_STOP. A fixed threshold on the rate was tried and collapsed 790 pairs
     to 25, because the per-pair rate is noisy across fold seeds.
     Dropping only the correct-call tail was tried first and is wrong: it keeps the pairs where
     the cue points the OTHER way, the refit learns the reversed cue, and the two-sided test
     fires harder than on the source (TruthfulQA 790: unpaired A* 0.67 -> 0.84, 2026-10-26).
  3. Certify on a DIFFERENT seed than any filter seed: paired nulltest (within-pair sign flip,
     stratified by category) and unpaired nulltest on the exploded survivors. A certificate is
     issued only when both PASS and neither is borderline.
CAVEAT, stated in every certificate: the survivors were SELECTED against these probe families,
so a pass says these six probes cannot recover the labels on the kept items, not that no
surface reader can. Held-out probe families are Phase 2 work (BENCHMARK.md).
"""
import argparse, hashlib, json, os, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
import nulltest as N  # noqa: E402
import nulltest_paired as P  # noqa: E402

FILTER_SEEDS = [11, 12, 13, 14, 15]
CERT_SEED = 20260724
FILTER_PROBES = ["lexicality", "words", "length", "shape"]
STEP = 0.05          # share of the current set dropped per round, most-cued first (both tails)
SCREEN_PERM = 200    # cheap screen used only to decide when to stop; never the certificate
SCREEN_SEED = 7
SCREEN_STOP = 0.20   # stop once both screens sit at adj p >= this (well clear of borderline)
MIN_PAIRS = 50


def explode(rows):
    out = []
    for i, r in enumerate(rows):
        out.append({"text": r["right"], "label": 0, "_row": i, "cat": r.get("cat", "_all")})
        out.append({"text": r["wrong"], "label": 1, "_row": i, "cat": r.get("cat", "_all")})
    return out


def cue_rates(rows):
    items = explode(rows)
    calls = {i: [] for i in range(len(rows))}
    for seed in FILTER_SEEDS:
        m = P.pair_margins(items, "text", "label", "_row", seed=seed)
        for probe in FILTER_PROBES:
            tot = sum(m[str(i)][probe] for i in range(len(rows)))
            sign = 1 if tot >= 0 else -1
            for i in range(len(rows)):
                calls[i].append(1 if m[str(i)][probe] * sign > 0 else 0)
    return [sum(c) / len(c) for i, c in sorted(calls.items())]


def sha(b):
    return hashlib.sha256(b).hexdigest()


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("pairs")
    ap.add_argument("--out", required=True)
    ap.add_argument("--name", default=None)
    ap.add_argument("--n-perm", type=int, default=600)
    ap.add_argument("--jobs", type=int, default=8)
    a = ap.parse_args(argv[1:])
    raw = open(a.pairs, "rb").read()
    rows = json.loads(raw)
    name = a.name or os.path.splitext(os.path.basename(a.pairs))[0]
    keep = list(range(len(rows)))
    rounds = []
    while len(keep) >= MIN_PAIRS:
        sub = [rows[i] for i in keep]
        it = explode(sub)
        qp = P.evaluate_paired(it, "text", "label", "_row", None, n_perm=SCREEN_PERM, seed=SCREEN_SEED, jobs=a.jobs)
        qu = N.evaluate(it, "text", "label", None, n_perm=SCREEN_PERM, jobs=a.jobs)
        rd = {"in": len(keep), "screen_paired_adj_p": qp["worst_cell"]["adj_p"],
              "screen_unpaired_adj_p": qu["worst_cell"]["adj_p"]}
        rounds.append(rd)
        if qp["worst_cell"]["adj_p"] >= SCREEN_STOP and qu["worst_cell"]["adj_p"] >= SCREEN_STOP:
            break
        rates = cue_rates(sub)
        order = sorted(range(len(keep)), key=lambda j: -abs(rates[j] - 0.5))
        k = max(1, int(round(STEP * len(keep))))
        ds = {keep[j] for j in order[:k]}
        rd["dropped"] = len(ds)
        keep = [i for i in keep if i not in ds]
    kept = [rows[i] for i in keep]
    removed = [rows[i] for i in range(len(rows)) if i not in set(keep)]
    os.makedirs(a.out, exist_ok=True)
    kb = json.dumps(kept, ensure_ascii=False, indent=0).encode()
    open(os.path.join(a.out, "certified.json"), "wb").write(kb)
    open(os.path.join(a.out, "removed.json"), "w").write(json.dumps(removed, ensure_ascii=False, indent=0))
    items = explode(kept)
    pr = P.evaluate_paired(items, "text", "label", "_row", "cat", n_perm=a.n_perm, seed=CERT_SEED, jobs=a.jobs)
    ur = N.evaluate(items, "text", "label", None, n_perm=a.n_perm, jobs=a.jobs)
    json.dump(pr, open(os.path.join(a.out, "paired.out.json"), "w"), indent=1)
    json.dump(ur, open(os.path.join(a.out, "unpaired.out.json"), "w"), indent=1)
    ok = (pr["verdict"] == "PASS" and not pr.get("borderline")
          and ur["verdict"] == "PASS" and not ur.get("borderline"))
    def cats(rs):
        c = {}
        for r in rs:
            c[r.get("cat", "_all")] = c.get(r.get("cat", "_all"), 0) + 1
        return c
    cert = {
        "format": "honesty.tools/certificate/v0",
        "dataset": name,
        "issued": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "status": "CERTIFIED" if ok else "NOT_CERTIFIED",
        "source": {"file": os.path.basename(a.pairs), "sha256": sha(raw), "n_pairs": len(rows)},
        "split": {"file": "certified.json", "sha256": sha(kb), "n_pairs": len(kept),
                  "retained_share": round(len(kept) / len(rows), 3),
                  "by_category_source": cats(rows), "by_category_kept": cats(kept)},
        "filter": {"method": "pair-level adversarial filtering (AFLite-style)",
                   "probes": FILTER_PROBES, "seeds": FILTER_SEEDS, "step": STEP, "screen_perm": SCREEN_PERM, "screen_seed": SCREEN_SEED, "screen_stop": SCREEN_STOP, "min_pairs": MIN_PAIRS, "rounds": rounds},
        "tests": {
            "paired": {"verdict": pr["verdict"], "borderline": pr.get("borderline"),
                       "headline": pr["headline"], "n_perm": pr.get("n_perm"),
                       "seed": CERT_SEED, "stratum": "cat"},
            "unpaired": {"verdict": ur["verdict"], "borderline": ur.get("borderline"),
                         "headline": ur["headline"], "n_perm": ur.get("n_perm")}},
        "tool": {"nulltest_version": N.VERSION, "paired_version": P.PAIRED_VERSION,
                 "nulltest_sha256": sha(open(os.path.join(ROOT, "nulltest.py"), "rb").read()),
                 "nulltest_paired_sha256": sha(open(os.path.join(ROOT, "nulltest_paired.py"), "rb").read()),
                 "certify_sha256": sha(open(os.path.abspath(__file__), "rb").read())},
        "caveat": ("The kept pairs were selected against these probe families. A pass means these "
                   "six surface probes cannot recover right-vs-wrong on the kept pairs; it is not a "
                   "claim that no surface reader can, and the filter shifts category coverage "
                   "(see by_category_*). Unsigned prototype: signing and `honest check` replay are Phase 5."),
        "replay": "python3 benchmark/certified/certify.py %s --out DIR --n-perm %d" % (os.path.basename(a.pairs), a.n_perm),
    }
    json.dump(cert, open(os.path.join(a.out, "certificate.json"), "w"), indent=1)
    print(json.dumps({k: cert[k] for k in ("status", "split", "filter", "tests")}, indent=1)[:3000])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
