#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""FLOOR certified split, prototype: filter a paired right/wrong benchmark to the pairs that
carry no surface cue, then certify the survivors with nulltest (paired AND unpaired) and the
held-out probes, and write a certificate JSON (format floor/certificate/v1).

    python3 benchmark/certified/certify.py PAIRS.json --out DIR [--name NAME]
    python3 benchmark/certified/certify.py SUBSET.json --no-filter --source SOURCE.json --out DIR

--no-filter certifies SUBSET as given (e.g. a negation-balanced selection, Phase 1b option A);
the kept share is then measured against SOURCE.

STATUS (three values):
  CERTIFIED        every gate passes on a kept set at or above the floor.
  NOT_CERTIFIED    the kept set is at or above the floor but a certificate gate fails.
  NOT_CERTIFIABLE  the set cannot be cleaned without collapsing: the source is below the floor,
                   or the filter reached the floor with the screens still firing, or (no-filter)
                   the given subset is below the floor. Gates are still run and reported.
FLOOR (the minimum kept set) = max(ceil(MIN_KEPT_SHARE * source pairs), MIN_KEPT_PAIRS):
  MIN_KEPT_SHARE 0.50: a certificate describes the benchmark. If more than half the pairs must go,
    the cue is a property of how the benchmark was built, not of a few bad items, and the kept
    set is a selected minority whose scores say little about the source.
  MIN_KEPT_PAIRS 320 (= power_floor()): a PASS must be able to rule something out. 320 pairs is
    the smallest n at which an exact two-sided sign test at alpha 0.05/8 (Bonferroni over the
    eight held-out probes, conservative next to Westfall-Young) has >= 80% power against a probe
    that picks the right member in 60% of pairs. Below that, a pass cannot exclude a 60/40 cue.

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
  3. Certify on a DIFFERENT seed than any filter seed, with three gates that must all pass:
     a. IN-FAMILY: paired nulltest (within-pair sign flip, stratified by category) and unpaired
        nulltest on the exploded survivors. These are the probes the filter used, so passing them
        is necessary but circular.
     b. HELD-OUT: heldout.py, eight probe types the filter never saw (negation, hedge and
        function-word counts, punctuation, digits, capitals, mean word length, and a refit
        character 3-5-gram Naive Bayes), paired sign-flip null, Westfall-Young over the eight.
     c. NEGATION CUE: an exact two-sided sign test on which member carries more negation cues.
     A certificate is issued only when every test PASSes, none is borderline, and the negation
     sign test finds no cue (p >= 0.05).
CAVEAT, stated in every certificate: a pass means none of these probe families recovers the
labels on the kept items, not that no surface reader can (model-based readers are not run).
"""
import argparse, hashlib, json, math, os, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
import nulltest as N  # noqa: E402
import nulltest_paired as P  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import heldout as H  # noqa: E402

FILTER_SEEDS = [11, 12, 13, 14, 15]
CERT_SEED = 20260724
FILTER_PROBES = ["lexicality", "words", "length", "shape"]
STEP = 0.05          # share of the current set dropped per round, most-cued first (both tails)
SCREEN_PERM = 200    # cheap screen used only to decide when to stop; never the certificate
SCREEN_SEED = 7
SCREEN_STOP = 0.20   # stop once both screens sit at adj p >= this (well clear of borderline)
MIN_KEPT_SHARE = 0.50
POWER_P1, POWER_ALPHA, POWER_K, POWER_TARGET = 0.60, 0.05, 8, 0.80
STATUSES = ("CERTIFIED", "NOT_CERTIFIED", "NOT_CERTIFIABLE")
HELDOUT_PERM = 2000
HELDOUT_SEED = 20261026


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


def power_floor(p1=POWER_P1, alpha=POWER_ALPHA, k=POWER_K, target=POWER_TARGET):
    """Smallest n where an exact two-sided sign test at alpha/k has power >= target vs rate p1."""
    a = alpha / k
    for n in range(2, 5000):
        pmf = [math.comb(n, i) / 2.0 ** n for i in range(n + 1)]
        c, tail = -1, 0.0
        for i in range(n // 2 + 1):
            if 2 * (tail + pmf[i]) <= a:
                tail += pmf[i]
                c = i
            else:
                break
        if c < 0:
            continue
        pw = sum(math.comb(n, i) * p1 ** i * (1 - p1) ** (n - i) for i in range(n + 1) if i <= c or i >= n - c)
        if pw >= target:
            return n
    raise ValueError("no n below 5000")


MIN_KEPT_PAIRS = power_floor()


def floor_for(n_source):
    return max(int(math.ceil(MIN_KEPT_SHARE * n_source)), MIN_KEPT_PAIRS)


def decide(gates, n_source, n_kept, stopped_at_floor):
    """The status rule, separated so it can be tested without running the probes."""
    fl = floor_for(n_source)
    reasons = []
    if n_source < fl:
        reasons.append("source_below_floor")
    if n_kept < fl:
        reasons.append("kept_below_floor")
    if stopped_at_floor and not all(gates.values()):
        reasons.append("filter_reached_floor_with_cues")
    if reasons:
        return "NOT_CERTIFIABLE", reasons
    return ("CERTIFIED", []) if all(gates.values()) else ("NOT_CERTIFIED", [g for g, v in gates.items() if not v])


def sha(b):
    return hashlib.sha256(b).hexdigest()


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("pairs")
    ap.add_argument("--out", required=True)
    ap.add_argument("--name", default=None)
    ap.add_argument("--n-perm", type=int, default=600)
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--no-filter", action="store_true", help="certify PAIRS as given")
    ap.add_argument("--source", default=None, help="with --no-filter: the full source the subset came from")
    a = ap.parse_args(argv[1:])
    raw = open(a.pairs, "rb").read()
    rows = json.loads(raw)
    name = a.name or os.path.splitext(os.path.basename(a.pairs))[0]
    src_raw = open(a.source, "rb").read() if a.source else raw
    n_source = len(json.loads(src_raw)) if a.source else len(rows)
    fl = floor_for(n_source)
    keep = list(range(len(rows)))
    rounds = []
    stopped_at_floor = False
    while not a.no_filter:
        sub = [rows[i] for i in keep]
        it = explode(sub)
        qp = P.evaluate_paired(it, "text", "label", "_row", None, n_perm=SCREEN_PERM, seed=SCREEN_SEED, jobs=a.jobs)
        qu = N.evaluate(it, "text", "label", None, n_perm=SCREEN_PERM, jobs=a.jobs)
        rd = {"in": len(keep), "screen_paired_adj_p": qp["worst_cell"]["adj_p"],
              "screen_unpaired_adj_p": qu["worst_cell"]["adj_p"]}
        rounds.append(rd)
        if qp["worst_cell"]["adj_p"] >= SCREEN_STOP and qu["worst_cell"]["adj_p"] >= SCREEN_STOP:
            break
        k = max(1, int(round(STEP * len(keep))))
        if len(keep) - k < fl:
            stopped_at_floor = True
            rd["stopped"] = "floor"
            break
        rates = cue_rates(sub)
        order = sorted(range(len(keep)), key=lambda j: -abs(rates[j] - 0.5))
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
    hk = H.evaluate(kept, n_perm=HELDOUT_PERM, seed=HELDOUT_SEED)
    hs = H.evaluate(json.loads(src_raw), n_perm=HELDOUT_PERM, seed=HELDOUT_SEED)   # same probes on the source, for power context
    json.dump({"kept": hk, "source": hs}, open(os.path.join(a.out, "heldout.out.json"), "w"), indent=1)
    gates = {
        "in_family_paired": pr["verdict"] == "PASS" and not pr.get("borderline"),
        "in_family_unpaired": ur["verdict"] == "PASS" and not ur.get("borderline"),
        "held_out_probes": hk["verdict"] == "PASS" and not hk.get("borderline"),
        "negation_cue_absent": not hk["negation_check"]["cue_present"],
    }
    ok = all(gates.values())
    status, reasons = decide(gates, n_source, len(kept), stopped_at_floor)
    def cats(rs):
        c = {}
        for r in rs:
            c[r.get("cat", "_all")] = c.get(r.get("cat", "_all"), 0) + 1
        return c
    cert = {
        "format": "floor/certificate/v1",
        "benchmark": "FLOOR",
        "dataset": name,
        "issued": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "status": status,
        "status_reasons": reasons,
        "status_rule": {"statuses": list(STATUSES), "floor_pairs": fl,
                        "min_kept_share": MIN_KEPT_SHARE, "min_kept_pairs": MIN_KEPT_PAIRS,
                        "min_kept_pairs_basis": {"test": "exact two-sided sign test", "alpha": POWER_ALPHA,
                                                 "bonferroni_k": POWER_K, "alt_rate": POWER_P1, "power": POWER_TARGET},
                        "stopped_at_floor": stopped_at_floor},
        "source": {"file": os.path.basename(a.source or a.pairs), "sha256": sha(src_raw), "n_pairs": n_source},
        "split": {"file": "certified.json", "sha256": sha(kb), "n_pairs": len(kept),
                  "retained_share": round(len(kept) / n_source, 3),
                  "by_category_source": cats(json.loads(src_raw)), "by_category_kept": cats(kept)},
        "filter": ({"method": "none (--no-filter: subset certified as given)", "input": os.path.basename(a.pairs),
                    "input_sha256": sha(raw)} if a.no_filter else
                   {"method": "pair-level adversarial filtering (AFLite-style)",
                    "probes": FILTER_PROBES, "seeds": FILTER_SEEDS, "step": STEP, "screen_perm": SCREEN_PERM, "screen_seed": SCREEN_SEED, "screen_stop": SCREEN_STOP, "floor_pairs": fl, "rounds": rounds}),
        "tests": {
            "paired": {"verdict": pr["verdict"], "borderline": pr.get("borderline"),
                       "headline": pr["headline"], "n_perm": pr.get("n_perm"),
                       "seed": CERT_SEED, "stratum": "cat"},
            "unpaired": {"verdict": ur["verdict"], "borderline": ur.get("borderline"),
                         "headline": ur["headline"], "n_perm": ur.get("n_perm")},
            "held_out": {"verdict": hk["verdict"], "borderline": hk.get("borderline"),
                         "headline": hk["headline"], "separating": hk["separating"],
                         "probes": {k: {"astar_pair": v["astar_pair"], "adj_p": v["adj_p"], "direction": v["direction"]}
                                    for k, v in hk["probes"].items()},
                         "n_perm": HELDOUT_PERM, "seed": HELDOUT_SEED, "stratum": "_all",
                         "source_headline": hs["headline"], "source_separating": hs["separating"]},
            "negation_cue": {k: v for k, v in hk["negation_check"].items() if k != "lexicon"},
            "negation_cue_source": {k: v for k, v in hs["negation_check"].items() if k != "lexicon"}},
        "gates": gates,
        "tool": {"nulltest_version": N.VERSION, "paired_version": P.PAIRED_VERSION,
                 "nulltest_sha256": sha(open(os.path.join(ROOT, "nulltest.py"), "rb").read()),
                 "nulltest_paired_sha256": sha(open(os.path.join(ROOT, "nulltest_paired.py"), "rb").read()),
                 "certify_sha256": sha(open(os.path.abspath(__file__), "rb").read()),
                 "heldout_sha256": sha(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "heldout.py"), "rb").read())},
        "caveat": ("The kept pairs were selected against the in-family probes, so those gates are "
                   "circular; the held-out probes and the negation sign test were never used to "
                   "select. A pass means none of these probe families recovers right-vs-wrong on the "
                   "kept pairs; it is not a claim that no surface reader can (no model-based reader "
                   "is run), and the filter shifts category coverage (see by_category_*). Unsigned "
                   "prototype: signing and `honest check` replay are Phase 5."),
        "replay": ("python3 benchmark/certified/certify.py %s --out DIR --n-perm %d" % (os.path.basename(a.pairs), a.n_perm)
                   + (" --no-filter --source %s" % os.path.basename(a.source) if a.no_filter and a.source else (" --no-filter" if a.no_filter else ""))),
    }
    json.dump(cert, open(os.path.join(a.out, "certificate.json"), "w"), indent=1)
    print(json.dumps({k: cert[k] for k in ("status", "status_reasons", "gates", "tests")}, indent=1)[:4000])
    return 0 if status == "CERTIFIED" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
