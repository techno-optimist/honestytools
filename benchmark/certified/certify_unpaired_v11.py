#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""FLOOR v1.1, UNPAIRED mode. Same procedure, floor, filter and statuses as certify_unpaired.py
(FLOOR v1, left unchanged so every v1 certificate still replays), with one stricter gate:

  HELD-OUT (v1.1): heldout_strat.evaluate_unpaired_v11 scores the eight held-out probes twice on
  the same stratified permutation draws: pooled (identical to v1) and within stratum (size-weighted
  per-stratum A*, see heldout_strat.py). The gate fails if EITHER family separates or is borderline.

    python3 benchmark/certified/certify_unpaired_v11.py ITEMS.json --out DIR [--name NAME]
        [--text-key text] [--label-key label] [--stratum-key category]

The filter is untouched (it uses only the in-family probes), so for the same input the kept split
is the same as v1's. Writes certificate.json in format floor/certificate/v1.1.
"""
import json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import certify_unpaired as CU  # noqa: E402
import heldout_strat as HS  # noqa: E402

N, C = CU.N, CU.C


def main(argv):
    import argparse
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
    raw = CU._rb(a.items)
    rows = json.loads(raw)
    tk, lk, sk = a.text_key, a.label_key, a.stratum_key
    name = a.name or os.path.splitext(os.path.basename(a.items))[0]
    n_source = len(rows)
    fl = CU.floor_items(n_source)

    def minc(rs):
        n1 = sum(int(r[lk]) for r in rs)
        return min(n1, len(rs) - n1)
    # --- filter: verbatim certify_unpaired.py ---
    keep = list(range(n_source))
    rounds, stopped_at_floor = [], False
    while True:
        sub = [rows[i] for i in keep]
        q = N.evaluate(sub, tk, lk, sk, n_perm=CU.SCREEN_PERM, seed=C.SCREEN_SEED, jobs=a.jobs)
        rd = {"in": len(keep), "screen_adj_p": q["worst_cell"]["adj_p"]}
        rounds.append(rd)
        print(json.dumps(rd), flush=True)
        if q["worst_cell"]["adj_p"] >= CU.SCREEN_STOP:
            break
        k = max(1, int(round(CU.STEP * len(keep))))
        y = [int(r[lk]) for r in sub]
        n1 = sum(y)
        k1 = int(round(k * n1 / len(y)))
        k0 = k - k1
        if len(keep) - k < fl or min(n1 - k1, len(y) - n1 - k0) < CU.MIN_PER_CLASS:
            stopped_at_floor = True
            rd["stopped"] = "floor"
            break
        rates = CU.cue_rates([str(r[tk]) for r in sub], y)
        drop = set()
        for cls, kk in ((1, k1), (0, k0)):
            js = sorted([j for j in range(len(sub)) if y[j] == cls], key=lambda j: -abs(rates[j] - 0.5))
            drop.update(keep[j] for j in js[:kk])
        rd["dropped"] = len(drop)
        keep = [i for i in keep if i not in drop]
    ks = set(keep)
    kept = [rows[i] for i in keep]
    removed = [rows[i] for i in range(n_source) if i not in ks]
    os.makedirs(a.out, exist_ok=True)
    kb = json.dumps(kept, ensure_ascii=False, indent=0).encode()
    CU._write(os.path.join(a.out, "certified.json"), kb, "wb")
    CU._write(os.path.join(a.out, "removed.json"), json.dumps(removed, ensure_ascii=False, indent=0))
    ur = N.evaluate(kept, tk, lk, sk, n_perm=a.n_perm, seed=CU.CERT_SEED, jobs=a.jobs)
    CU._write(os.path.join(a.out, "unpaired.out.json"), json.dumps(ur, indent=1))
    hk = HS.evaluate_unpaired_v11(kept, CU.HELDOUT_PERM, CU.HELDOUT_SEED, text_key=tk, label_key=lk, stratum_key=sk)
    hs = HS.evaluate_unpaired_v11(rows, CU.HELDOUT_PERM, CU.HELDOUT_SEED, text_key=tk, label_key=lk, stratum_key=sk)
    CU._write(os.path.join(a.out, "heldout.out.json"), json.dumps({"kept": hk, "source": hs}, indent=1))
    hp, hw = hk["pooled"], hk["within"]
    gates = {
        "in_family_unpaired": ur["verdict"] == "PASS" and not ur.get("borderline"),
        "held_out_probes": HS.gate(hk),
        "negation_cue_absent": not hp["negation_check"]["cue_present"],
    }
    status, reasons = CU.decide(gates, n_source, len(kept), minc(rows), minc(kept), stopped_at_floor)

    def cls(rs):
        n1 = sum(int(r[lk]) for r in rs)
        return {"label1": n1, "label0": len(rs) - n1}

    def fam(r, within=False):
        d = {"verdict": r["verdict"], "borderline": r.get("borderline"), "headline": r["headline"],
             "separating": r["separating"],
             "probes": {k: {kk: v[kk] for kk in (("astar", "adj_p", "auroc_by_stratum") if within
                                                 else ("astar", "adj_p", "direction"))}
                        for k, v in r["probes"].items()}}
        if within:
            d.update({kk: r[kk] for kk in ("statistic", "strata_scored", "strata_skipped_one_class")})
        return d
    cert = {
        "format": "floor/certificate/v1.1", "mode": "unpaired", "benchmark": "FLOOR", "dataset": name,
        "issued": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "status": status, "status_reasons": reasons,
        "status_rule": {"floor_items": fl, "min_per_class": CU.MIN_PER_CLASS, "min_kept_share": CU.MIN_KEPT_SHARE,
                        "min_per_class_basis": "certify.py's paired power floor (320) reused per class; conservative, no separate unpaired derivation",
                        "held_out_rule": "v1.1: fails if the pooled OR the within-stratum family separates (adj p < 0.05) or is borderline (< 0.10)",
                        "stopped_at_floor": stopped_at_floor},
        "source": {"file": os.path.basename(a.items), "sha256": CU.sha(raw), "n_items": n_source, "classes": cls(rows)},
        "split": {"file": "certified.json", "sha256": CU.sha(kb), "n_items": len(kept), "classes": cls(kept),
                  "retained_share": round(len(kept) / n_source, 3)},
        "filter": {"method": "item-level adversarial filtering (AFLite-style), class-proportional drops (unchanged from v1)",
                   "probes": CU.FILTER_PROBES, "seeds": CU.FILTER_SEEDS, "step": CU.STEP, "screen_perm": CU.SCREEN_PERM,
                   "screen_seed": C.SCREEN_SEED, "screen_stop": CU.SCREEN_STOP, "rounds": rounds},
        "tests": {
            "unpaired": {"verdict": ur["verdict"], "borderline": ur.get("borderline"), "headline": ur["headline"],
                         "n_perm": ur.get("n_perm"), "seed": CU.CERT_SEED, "stratum": sk},
            "held_out": {"pooled": fam(hp), "within_stratum": fam(hw, True), "worst": HS.worst(hk),
                         "n_perm": CU.HELDOUT_PERM, "seed": CU.HELDOUT_SEED,
                         "null": "labels permuted within stratum; one set of draws shared by both families",
                         "source": {"pooled_headline": hs["pooled"]["headline"],
                                    "pooled_separating": hs["pooled"]["separating"],
                                    "within_headline": hs["within"]["headline"],
                                    "within_separating": hs["within"]["separating"]}},
            "negation_cue": hp["negation_check"], "negation_cue_source": hs["pooled"]["negation_check"]},
        "gates": gates,
        "provenance": {"nulltest_version": N.VERSION,
                       "nulltest_sha256": CU.sha(CU._rb(os.path.join(CU.ROOT, "nulltest.py"))),
                       "certify_sha256": CU.sha(CU._rb(os.path.join(HERE, "certify.py"))),
                       "certify_unpaired_sha256": CU.sha(CU._rb(os.path.join(HERE, "certify_unpaired.py"))),
                       "certify_unpaired_v11_sha256": CU.sha(CU._rb(os.path.abspath(__file__))),
                       "heldout_sha256": CU.sha(CU._rb(os.path.join(HERE, "heldout.py"))),
                       "heldout_np_sha256": CU.sha(CU._rb(os.path.join(HERE, "heldout_np.py"))),
                       "heldout_strat_sha256": CU.sha(CU._rb(os.path.join(HERE, "heldout_strat.py")))},
        "caveat": ("The kept items were selected against the in-family probes, so that gate is circular; the "
                   "held-out probes and the negation test were never used to select. A pass means none of these "
                   "probe families, pooled or within stratum, recovers the labels on the kept items; it is not a "
                   "claim that no surface reader can (no model-based reader is run). Unsigned prototype."),
        "replay": "python3 benchmark/certified/certify_unpaired_v11.py %s --out DIR --n-perm %d%s%s%s"
                  % (os.path.basename(a.items), a.n_perm,
                     (" --text-key %s" % a.text_key) if a.text_key != "text" else "",
                     (" --label-key %s" % a.label_key) if a.label_key != "label" else "",
                     (" --stratum-key %s" % sk) if sk else ""),
    }
    CU._write(os.path.join(a.out, "certificate.json"), json.dumps(cert, indent=1))
    print(json.dumps({k: cert[k] for k in ("status", "status_reasons", "gates")}))
    return 0 if status == "CERTIFIED" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
