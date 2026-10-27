#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""FLOOR v1, UNPAIRED mode: the certify.py procedure for a labelled set with no matched pairs
(true vs false statements, real vs fabricated entities, faithful vs hallucinated responses).

    python3 benchmark/certified/certify_unpaired.py ITEMS.json --out DIR [--name NAME]
        [--text-key text] [--label-key label] [--stratum-key category]

ITEMS.json: list of {"<text-key>": str, "<label-key>": 0|1, "<stratum-key>": str (optional)}.

Same three statuses and the same shape of rule as certify.py (see its docstring):
  CERTIFIED / NOT_CERTIFIED / NOT_CERTIFIABLE.
FLOOR (unpaired): the kept set must hold at least max(ceil(0.50 * source items), 2 * 320) items
  AND at least 320 of each class. 320 is certify.py's paired power floor reused per class. We did
  not derive a separate unpaired power floor; reusing 320 per class is the conservative choice
  (an unpaired AUROC test needs fewer items per class than a sign test needs pairs for the same
  60/40 alternative), so a dataset that is NOT_CERTIFIABLE here for size could in principle pass a
  looser, separately justified floor. The 0.50 share rule is unchanged.

PROCEDURE:
  1. Item-level adversarial filter (AFLite-style). For FILTER_SEEDS fold splits, every item is
     scored out-of-fold by the in-family probes (nulltest's lexicality, words, shape refit per
     seed; length unfitted). A probe 'calls' an item when the item sits on its own class's side
     of the probe's median, in the direction the probe favours over the whole set. An item's cue
     rate is its share of correct calls. Each round drops STEP of the current set, the items whose
     rate is furthest from 1/2 (both tails), taken from each class in proportion to its share so
     the class balance does not drift. Re-screen with nulltest (SCREEN_PERM, stratified); stop at
     adj p >= SCREEN_STOP, or at the floor.
  2. Certify on a different seed, three gates, all must pass:
     a. IN-FAMILY: nulltest (labels permuted within stratum, every probe refit). Circular, since
        the filter used these probes.
     b. HELD-OUT: heldout_np.evaluate_unpaired, the eight heldout.py probe types scored as AUROC,
        labels permuted within stratum, char n-gram refit, Westfall-Young.
     c. NEGATION CUE: two-sided permutation test on the class difference in mean negation count.
CAVEAT: as certify.py. A pass means none of these probe families recovers the labels on the kept
items; it is not a claim that no surface reader can.
"""
import argparse, hashlib, json, math, os, random, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import nulltest as N  # noqa: E402
import certify as C  # noqa: E402

FILTER_SEEDS = C.FILTER_SEEDS
FILTER_PROBES = C.FILTER_PROBES
STEP, SCREEN_PERM, SCREEN_STOP = C.STEP, C.SCREEN_PERM, C.SCREEN_STOP
CERT_SEED, HELDOUT_PERM, HELDOUT_SEED = C.CERT_SEED, C.HELDOUT_PERM, C.HELDOUT_SEED
MIN_KEPT_SHARE = C.MIN_KEPT_SHARE
MIN_PER_CLASS = C.MIN_KEPT_PAIRS


def floor_items(n_source):
    return max(int(math.ceil(MIN_KEPT_SHARE * n_source)), 2 * MIN_PER_CLASS)


def decide(gates, n_source, n_kept, min_class_source, min_class_kept, stopped_at_floor):
    fl = floor_items(n_source)
    reasons = []
    if n_source < fl or min_class_source < MIN_PER_CLASS:
        reasons.append("source_below_floor")
    if n_kept < fl or min_class_kept < MIN_PER_CLASS:
        reasons.append("kept_below_floor")
    if stopped_at_floor and not all(gates.values()):
        reasons.append("filter_reached_floor_with_cues")
    if reasons:
        return "NOT_CERTIFIABLE", reasons
    return ("CERTIFIED", []) if all(gates.values()) else ("NOT_CERTIFIED", [g for g, v in gates.items() if not v])


def _median(xs):
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def cue_rates(texts, y):
    calls = [[] for _ in texts]
    for seed in FILTER_SEEDS:
        for name in FILTER_PROBES:
            kind, fn = N.NULLS[name]
            s = N._NB(texts, fn, seed=seed).score(y) if kind == "nb" else [float(fn(t)) for t in texts]
            a = N.auroc(y, s)
            sign = 1 if a >= 0.5 else -1
            m = _median(s)
            for i, (v, yy) in enumerate(zip(s, y)):
                calls[i].append(1 if (v - m) * sign * (1 if yy == 1 else -1) > 0 else 0)
    return [sum(c) / len(c) for c in calls]


def _rb(path):
    with open(path, "rb") as f:
        return f.read()


def _write(path, data, mode="w"):
    with open(path, mode) as f:
        f.write(data)


def sha(b):
    return hashlib.sha256(b).hexdigest()


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("items")
    ap.add_argument("--out", required=True)
    ap.add_argument("--name", default=None)
    ap.add_argument("--text-key", default="text")
    ap.add_argument("--label-key", default="label")
    ap.add_argument("--stratum-key", default=None)
    ap.add_argument("--n-perm", type=int, default=600)
    ap.add_argument("--jobs", type=int, default=8)
    a = ap.parse_args(argv[1:])
    import heldout_np as HN
    raw = _rb(a.items)
    rows = json.loads(raw)
    tk, lk, sk = a.text_key, a.label_key, a.stratum_key
    name = a.name or os.path.splitext(os.path.basename(a.items))[0]
    n_source = len(rows)
    fl = floor_items(n_source)

    def minc(rs):
        n1 = sum(int(r[lk]) for r in rs)
        return min(n1, len(rs) - n1)
    keep = list(range(n_source))
    rounds, stopped_at_floor = [], False
    while True:
        sub = [rows[i] for i in keep]
        q = N.evaluate(sub, tk, lk, sk, n_perm=SCREEN_PERM, seed=C.SCREEN_SEED, jobs=a.jobs)
        rd = {"in": len(keep), "screen_adj_p": q["worst_cell"]["adj_p"]}
        rounds.append(rd)
        print(json.dumps(rd), flush=True)
        if q["worst_cell"]["adj_p"] >= SCREEN_STOP:
            break
        k = max(1, int(round(STEP * len(keep))))
        y = [int(r[lk]) for r in sub]
        n1 = sum(y)
        k1 = int(round(k * n1 / len(y)))
        k0 = k - k1
        if len(keep) - k < fl or min(n1 - k1, len(y) - n1 - k0) < MIN_PER_CLASS:
            stopped_at_floor = True
            rd["stopped"] = "floor"
            break
        rates = cue_rates([str(r[tk]) for r in sub], y)
        drop = set()
        for cls, kk in ((1, k1), (0, k0)):
            js = sorted([j for j in range(len(sub)) if y[j] == cls], key=lambda j: -abs(rates[j] - 0.5))
            drop.update(keep[j] for j in js[:kk])
        rd["dropped"] = len(drop)
        keep = [i for i in keep if i not in drop]
    kept = [rows[i] for i in keep]
    removed = [rows[i] for i in range(n_source) if i not in set(keep)]
    os.makedirs(a.out, exist_ok=True)
    kb = json.dumps(kept, ensure_ascii=False, indent=0).encode()
    _write(os.path.join(a.out, "certified.json"), kb, "wb")
    _write(os.path.join(a.out, "removed.json"), json.dumps(removed, ensure_ascii=False, indent=0))
    ur = N.evaluate(kept, tk, lk, sk, n_perm=a.n_perm, seed=CERT_SEED, jobs=a.jobs)
    _write(os.path.join(a.out, "unpaired.out.json"), json.dumps(ur, indent=1))
    hk = HN.evaluate_unpaired(kept, HELDOUT_PERM, HELDOUT_SEED, text_key=tk, label_key=lk, stratum_key=sk)
    hs = HN.evaluate_unpaired(rows, HELDOUT_PERM, HELDOUT_SEED, text_key=tk, label_key=lk, stratum_key=sk)
    _write(os.path.join(a.out, "heldout.out.json"), json.dumps({"kept": hk, "source": hs}, indent=1))
    gates = {
        "in_family_unpaired": ur["verdict"] == "PASS" and not ur.get("borderline"),
        "held_out_probes": hk["verdict"] == "PASS" and not hk.get("borderline"),
        "negation_cue_absent": not hk["negation_check"]["cue_present"],
    }
    status, reasons = decide(gates, n_source, len(kept), minc(rows), minc(kept), stopped_at_floor)

    def cls(rs):
        n1 = sum(int(r[lk]) for r in rs)
        return {"label1": n1, "label0": len(rs) - n1}
    cert = {
        "format": "floor/certificate/v1", "mode": "unpaired", "benchmark": "FLOOR", "dataset": name,
        "issued": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "status": status, "status_reasons": reasons,
        "status_rule": {"floor_items": fl, "min_per_class": MIN_PER_CLASS, "min_kept_share": MIN_KEPT_SHARE,
                        "min_per_class_basis": "certify.py's paired power floor (320) reused per class; conservative, no separate unpaired derivation",
                        "stopped_at_floor": stopped_at_floor},
        "source": {"file": os.path.basename(a.items), "sha256": sha(raw), "n_items": n_source, "classes": cls(rows)},
        "split": {"file": "certified.json", "sha256": sha(kb), "n_items": len(kept), "classes": cls(kept),
                  "retained_share": round(len(kept) / n_source, 3)},
        "filter": {"method": "item-level adversarial filtering (AFLite-style), class-proportional drops",
                   "probes": FILTER_PROBES, "seeds": FILTER_SEEDS, "step": STEP, "screen_perm": SCREEN_PERM,
                   "screen_seed": C.SCREEN_SEED, "screen_stop": SCREEN_STOP, "rounds": rounds},
        "tests": {
            "unpaired": {"verdict": ur["verdict"], "borderline": ur.get("borderline"), "headline": ur["headline"],
                         "n_perm": ur.get("n_perm"), "seed": CERT_SEED, "stratum": sk},
            "held_out": {"verdict": hk["verdict"], "borderline": hk.get("borderline"), "headline": hk["headline"],
                         "separating": hk["separating"],
                         "probes": {k: {"astar": v["astar"], "adj_p": v["adj_p"], "direction": v["direction"]}
                                    for k, v in hk["probes"].items()},
                         "n_perm": HELDOUT_PERM, "seed": HELDOUT_SEED, "source_headline": hs["headline"],
                         "source_separating": hs["separating"]},
            "negation_cue": hk["negation_check"], "negation_cue_source": hs["negation_check"]},
        "gates": gates,
        "provenance": {"nulltest_version": N.VERSION,
                       "nulltest_sha256": sha(_rb(os.path.join(ROOT, "nulltest.py"))),
                       "certify_sha256": sha(_rb(os.path.join(HERE, "certify.py"))),
                       "certify_unpaired_sha256": sha(_rb(os.path.abspath(__file__))),
                       "heldout_sha256": sha(_rb(os.path.join(HERE, "heldout.py"))),
                       "heldout_np_sha256": sha(_rb(os.path.join(HERE, "heldout_np.py")))},
        "caveat": ("The kept items were selected against the in-family probes, so that gate is circular; the "
                   "held-out probes and the negation test were never used to select. A pass means none of these "
                   "probe families recovers the labels on the kept items; it is not a claim that no surface "
                   "reader can (no model-based reader is run). Unsigned prototype."),
        "replay": "python3 benchmark/certified/certify_unpaired.py %s --out DIR --n-perm %d%s%s%s"
                  % (os.path.basename(a.items), a.n_perm,
                     (" --text-key %s" % a.text_key) if a.text_key != "text" else "",
                     (" --label-key %s" % a.label_key) if a.label_key != "label" else "",
                     (" --stratum-key %s" % sk) if sk else ""),
    }
    _write(os.path.join(a.out, "certificate.json"), json.dumps(cert, indent=1))
    print(json.dumps({k: cert[k] for k in ("status", "status_reasons", "gates")}))
    return 0 if status == "CERTIFIED" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
