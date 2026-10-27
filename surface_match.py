#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Kevin Russell (Aperture / Project Forty Two)
"""surface_match.py — repair a benchmark the surface can score: keep a matched subset, verified held-out.

    python3 surface_match.py battery.json --out matched.json
    python3 surface_match.py data.jsonl --text-key response --label-key labels --stratum-key task_type --out matched.json
    python3 surface_match.py --audit [--only NAME] [--out-dir matched/]      # every DISQUALIFIED manifest row

WHAT IT DOES. `nulltest` tells a benchmark author that a regular expression could score their set.
This gives them a set it cannot. It keeps the items whose two classes look alike on the surface and
drops the rest, by propensity matching on the six probes: fit the surface classifier
(`surface_baseline`'s combiner) out-of-fold, then pair every item of the smaller class with its nearest unmatched item of the
other class on the surface logit (inside its stratum, within a caliper of a fifth of a standard
deviation) and drop whatever finds no twin. Repeat until the combiner can no longer separate the
classes. The output file holds the kept items unchanged plus a receipt.

WHY THE RECEIPT HAS TWO NUMBERS. Testing a subset on the same items you selected it with flatters
the subset: you removed the items that made the probes fire, so of course they fire less. So the
matching RULE (the fitted models and calipers, not the item choices) is learned on one half of
the data and applied to the other, both ways, and `nulltest` and the baseline are run on those
held-out halves. That is the honest estimate of what the procedure achieves on items it never
selected on. The full-data subset is what you ship; its own in-sample verdict is printed beside the
held-out one and labelled optimistic. If the held-out halves still DISQUALIFY, the procedure did
not fix the set and the receipt says so.

WHAT IT CANNOT DO. It cannot add items, so a set that is all surface ends up empty or tiny; it
cannot fix an artifact the six probes do not see; and matching on the surface changes what the
set measures (the hard, surface-ambiguous items remain), which is the point, and must be said when
the matched set is cited. Standard library only. Deterministic: seed 20260724.
"""
import argparse
import datetime
import hashlib
import importlib.util
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
VERSION = "0.1"
SEED = 20260724
MAX_ROUNDS = 4


def stop_auroc(n):
    """Stop matching when the out-of-fold surface AUROC on the kept items is within sampling noise of
    one half: 1.5/sqrt(n) above it, never less than 0.02. Matching on noise only shrinks the set."""
    return 0.5 + max(0.02, 1.5 / (n ** 0.5))


def _load(mod, fname):
    # Reuse a module the caller already imported (the tests import nulltest as a package module);
    # registering a second copy under the same name breaks pickling into multiprocessing workers.
    if mod in sys.modules:
        return sys.modules[mod]
    spec = importlib.util.spec_from_file_location(mod, os.path.join(HERE, fname))
    m = importlib.util.module_from_spec(spec)
    sys.modules[mod] = m
    spec.loader.exec_module(m)
    return m


N = _load("nulltest", "nulltest.py")
S = _load("surface_baseline", "surface_baseline.py")


# ---------------------------------------------------------------------------------------------
# surface propensity: fit on one index set, score another (or itself, out-of-fold)
# ---------------------------------------------------------------------------------------------
class _Surface:
    """Everything label-free about the items, computed once."""

    def __init__(self, items, text_key, label_key, stratum_key):
        self.texts = [N.item_text(it, text_key) for it in items]
        self.y = [int(it[label_key]) for it in items]
        self.strata = [str(it.get(stratum_key, "_all")) for it in items] if stratum_key else ["_all"] * len(items)
        if len(set(self.strata)) < 2:
            self.strata = ["_all"] * len(items)
        self.feats, self.fixed = {}, {}
        for name, (kind, fn) in N.NULLS.items():
            if kind == "nb":
                self.feats[name] = [fn(t) for t in self.texts]
            else:
                self.fixed[name] = [float(fn(t)) for t in self.texts]


def _columns(surf, fit_idx, apply_idx, nested):
    """Probe-score columns for apply_idx, with NB probes trained on fit_idx. nested=True scores
    fit_idx itself out-of-fold (apply_idx must then equal fit_idx)."""
    y_fit = [surf.y[i] for i in fit_idx]
    cols = []
    for name in S.PROBES:
        if name in surf.feats:
            ftr = [surf.feats[name][i] for i in fit_idx]
            if nested:
                cols.append(S._inner_oof(ftr, y_fit))
            else:
                cols.append(S._nb_train_test(ftr, y_fit, [surf.feats[name][i] for i in apply_idx]))
        else:
            cols.append([surf.fixed[name][i] for i in apply_idx])
    return [list(r) for r in zip(*cols)]


def fit_round(surf, idx):
    """Learn one round's rule on idx: combiner weights and caliper from out-of-fold probabilities.
    Returns (rule, oof_probabilities for idx)."""
    y = [surf.y[i] for i in idx]
    X = _columns(surf, idx, idx, nested=True)
    mu, sd = S._standardise(X)
    w = S._fit_logistic(S._apply(X, mu, sd), y)
    p = S._predict(S._apply(X, mu, sd), w)
    # caliper: a fifth of the standard deviation of the surface logit, the usual propensity rule
    zs = [_logit(v) for v in p]
    mz = sum(zs) / len(zs)
    sdz = (sum((v - mz) ** 2 for v in zs) / max(1, len(zs) - 1)) ** 0.5
    rule = {"fit_idx": list(idx), "mu": mu, "sd": sd, "w": w, "caliper": max(0.2 * sdz, 1e-3)}
    return rule, p


def apply_round(surf, rule, idx):
    """Score idx with a rule learned elsewhere: NB probes trained on the rule's fit set."""
    X = _columns(surf, rule["fit_idx"], idx, nested=False)
    return S._predict(S._apply(X, rule["mu"], rule["sd"]), rule["w"])


def _logit(p):
    import math
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def match(surf, idx, p, caliper):
    """One-to-one nearest-neighbour matching on the surface logit, inside each stratum: every item
    of the smaller class is paired with the closest unmatched item of the other class whose logit
    lies within `caliper`; unpaired items are dropped. The most extreme items are matched first, so
    the items with no surface twin (the ones carrying the artifact) are the ones that go.
    Deterministic; items are returned in their original order."""
    import bisect
    z = {i: _logit(pi) for i, pi in zip(idx, p)}
    keep = []
    for st in sorted(set(surf.strata[i] for i in idx)):
        ones = [i for i in idx if surf.strata[i] == st and surf.y[i] == 1]
        zeros = [i for i in idx if surf.strata[i] == st and surf.y[i] == 0]
        if not ones or not zeros:
            continue
        small, big = (ones, zeros) if len(ones) <= len(zeros) else (zeros, ones)
        big = sorted(big, key=lambda i: (z[i], i))
        bz = [z[i] for i in big]
        used = [False] * len(big)
        for i in sorted(small, key=lambda i: (-abs(z[i]), i)):
            k = bisect.bisect_left(bz, z[i])
            best, bestd = None, caliper
            lo, hi = k - 1, k
            while lo >= 0 or hi < len(big):
                if hi < len(big) and not used[hi] and bz[hi] - z[i] <= bestd:
                    best, bestd = hi, bz[hi] - z[i]
                if lo >= 0 and not used[lo] and z[i] - bz[lo] <= bestd:
                    best, bestd = lo, z[i] - bz[lo]
                if (hi >= len(big) or bz[hi] - z[i] > bestd) and (lo < 0 or z[i] - bz[lo] > bestd):
                    break
                lo -= 1
                hi += 1
            if best is not None:
                used[best] = True
                keep.append(i)
                keep.append(big[best])
    return sorted(keep)


def learn(surf, idx, rng, max_rounds=MAX_ROUNDS, log=None):
    """Learn the full rule (a list of rounds) on idx, returning (rounds, kept idx, trace)."""
    rounds, trace = [], []
    cur = list(idx)
    for r in range(max_rounds):
        y = [surf.y[i] for i in cur]
        if len(cur) < 40 or len(set(y)) < 2:
            break
        rule, p = fit_round(surf, cur)
        # SIGNED, not rectified: the combiner predicts label-1, so only an AUROC above one half is
        # surface signal. A combiner fitted to noise generalises a little BELOW one half (see
        # surface_baseline_null.json); rectifying that would read it as signal and keep matching.
        a = N.auroc(y, p)
        trace.append({"round": r + 1, "n": len(cur), "surface_auroc_oof": round(a, 3) if a is not None else None})
        if log:
            log("    round %d: n=%d, out-of-fold surface AUROC %.3f" % (r + 1, len(cur), a if a is not None else float("nan")))
        if a is None or a < stop_auroc(len(cur)):
            break
        rounds.append(rule)
        cur = match(surf, cur, p, rule["caliper"])
    return rounds, cur, trace


def apply(surf, rounds, idx, rng):
    cur = list(idx)
    for rule in rounds:
        if len(cur) < 2:
            break
        p = apply_round(surf, rule, cur)
        cur = match(surf, cur, p, rule["caliper"])
    return cur


# ---------------------------------------------------------------------------------------------
# verdicts on a subset
# ---------------------------------------------------------------------------------------------
def _verdict(items, text_key, label_key, stratum_key, n_perm, jobs):
    if len(items) < 30 or len({int(it[label_key]) for it in items}) < 2:
        return {"verdict": "NOT_SCORABLE", "n": len(items), "note": "fewer than 30 items or one class"}
    import contextlib, io
    with contextlib.redirect_stdout(io.StringIO()):
        rep = N.evaluate(items, text_key, label_key, stratum_key, n_perm=n_perm, jobs=jobs)
    out = {"verdict": rep["verdict"], "borderline": bool(rep.get("borderline")), "n": rep["n"],
           "cells": rep["family_size"], "worst_cell": rep["worst_cell"], "n_perm": rep["n_perm"],
           "separating_cells": rep.get("n_separating_cells", 0)}
    try:
        b = S.baseline(items, text_key, label_key, stratum_key)
        out["baseline"] = {k: b[k] for k in ("accuracy", "accuracy_ci95", "majority_accuracy", "balanced_accuracy", "auroc")}
    except ValueError as e:
        out["baseline"] = {"note": str(e)}
    return out


def _sha_bytes(b):
    return hashlib.sha256(b).hexdigest()


def _sha_file(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def repair(items, text_key, label_key, stratum_key=None, n_perm=600, jobs=1, seed=SEED, log=print):
    """The whole procedure. Returns (kept_items, receipt)."""
    surf = _Surface(items, text_key, label_key, stratum_key)
    n = len(items)
    idx = list(range(n))
    log("before: scoring the full set")
    before = _verdict(items, text_key, label_key, stratum_key, n_perm, jobs)
    if before["verdict"] != "DISQUALIFIED":
        # nothing to remove: matching a clean set only shrinks it
        log("    %s: nothing to repair, all items kept" % before["verdict"])
        return list(items), _receipt(surf, items, idx, [], [], before, [], before, "not_needed", n_perm, seed, text_key,
                                     label_key, stratum_key)

    # two halves, stratified by (stratum, label), seeded
    rng = random.Random(seed)
    halves = [[], []]
    groups = {}
    for i in idx:
        groups.setdefault((surf.strata[i], surf.y[i]), []).append(i)
    for g in sorted(groups):
        lst = groups[g][:]
        rng.shuffle(lst)
        for k, i in enumerate(lst):
            halves[k % 2].append(i)
    halves = [sorted(h) for h in halves]

    held_out = []
    for a, b in ((0, 1), (1, 0)):
        log("held-out check: learn the rule on half %d, apply it to half %d" % (a + 1, b + 1))
        rounds, _kept_a, trace = learn(surf, halves[a], random.Random(seed + 1 + a), log=log)
        kept_b = apply(surf, rounds, halves[b], random.Random(seed + 11 + a))
        sub = [items[i] for i in kept_b]
        v = _verdict(sub, text_key, label_key, stratum_key, n_perm, jobs)
        held_out.append({"learned_on_half": a + 1, "applied_to_half": b + 1, "rounds": len(rounds), "trace": trace,
                         "n_half": len(halves[b]), "n_kept": len(kept_b), "after": v})
        log("    kept %d of %d -> %s%s" % (len(kept_b), len(halves[b]), v["verdict"], " (borderline)" if v.get("borderline") else ""))

    log("final: learn the rule on everything, keep the matched subset")
    rounds, kept, trace = learn(surf, idx, random.Random(seed + 99), log=log)
    kept_items = [items[i] for i in kept]
    after = _verdict(kept_items, text_key, label_key, stratum_key, n_perm, jobs)
    log("    kept %d of %d -> %s (in-sample, optimistic)" % (len(kept), n, after["verdict"]))

    ho_verdicts = [h["after"]["verdict"] for h in held_out]
    if all(v == "PASS" for v in ho_verdicts) and after["verdict"] == "PASS":
        status = "repaired"
    elif any(v == "DISQUALIFIED" for v in ho_verdicts) or after["verdict"] == "DISQUALIFIED":
        status = "not_repaired"
    else:
        status = "too_small"
    return kept_items, _receipt(surf, items, kept, rounds, trace, before, held_out, after, status, n_perm, seed,
                                text_key, label_key, stratum_key)


def _receipt(surf, items, kept, rounds, trace, before, held_out, after, status, n_perm, seed, text_key, label_key,
             stratum_key):
    n = len(items)
    by_stratum = {}
    for i in kept:
        d = by_stratum.setdefault(surf.strata[i], {"n": 0, "label1": 0})
        d["n"] += 1
        d["label1"] += surf.y[i]
    return {
        "tool": "surface_match", "version": VERSION, "nulltest_version": N.VERSION,
        "tool_sha256": _sha_file(os.path.join(HERE, "surface_match.py")),
        "nulltest_sha256": _sha_file(os.path.join(HERE, "nulltest.py")),
        "surface_baseline_sha256": _sha_file(os.path.join(HERE, "surface_baseline.py")),
        "generated": datetime.date.today().isoformat(), "seed": seed, "n_perm_requested": n_perm,
        "text_key": text_key, "label_key": label_key, "stratum_key": stratum_key if len(set(surf.strata)) > 1 else None,
        "n_before": n, "n_kept": len(kept), "kept_fraction": round(len(kept) / n, 3),
        "kept_by_stratum": by_stratum, "rounds": len(rounds), "trace": trace,
        "before": before,
        "held_out": held_out,
        "after_in_sample": after,
        "repaired": status == "repaired", "status": status,
        "reading": {
            "repaired": "REPAIRED: the rule learned on each half leaves the other half PASS, and the full matched set "
                        "passes. Cite the matched set as surface-matched: it keeps the items whose classes look alike "
                        "on the surface, and measures something narrower than the original.",
            "not_repaired": "NOT REPAIRED: a held-out half (or the full matched set) still DISQUALIFIES. The surface in "
                            "this set is not removable by matching on six probes at this size; the kept subset is "
                            "smaller but not clean.",
            "too_small": "TOO SMALL TO VERIFY: matching left fewer than 30 items (or one class) in a held-out half, so "
                         "nothing can be said about the matched set. Most of this set IS its surface; it needs new "
                         "items, not a subset.",
            "not_needed": "NOTHING TO REPAIR: the full set is not DISQUALIFIED, so there is no surface to match away; "
                          "all items kept.",
        }[status],
    }


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("path", nargs="?")
    ap.add_argument("--text-key"); ap.add_argument("--label-key"); ap.add_argument("--stratum-key")
    ap.add_argument("--positive-label")
    ap.add_argument("--explode"); ap.add_argument("--split")
    ap.add_argument("--n-perm", type=int, default=600)
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--out", help="write the matched items plus receipt here (JSON: {items, surface_match})")
    ap.add_argument("--audit", action="store_true", help="repair every DISQUALIFIED row of the audit manifest")
    ap.add_argument("--only")
    ap.add_argument("--out-dir", default=os.path.join(HERE, "matched"))
    a = ap.parse_args(argv[1:])
    if a.audit:
        return run_audit(a.only, a.out_dir, a.n_perm, a.jobs)
    if not a.path:
        ap.error("give a battery path or --audit")
    items = N.read_items(a.path)
    raw_sha = _sha_file(a.path)
    if a.explode:
        items = N.explode_items(items, N.parse_explode(a.explode), split=a.split)
        tk, lk, sk = "text", "label", N.sniff_keys(items, "text", "label", a.stratum_key)[2]
    else:
        tk, lk, sk = N.sniff_keys(items, a.text_key, a.label_key, a.stratum_key)
    items, _pos = N.coerce_labels(items, lk, a.positive_label)
    kept, rec = repair(items, tk, lk, sk, n_perm=a.n_perm, jobs=a.jobs)
    rec["input"] = {"path": os.path.basename(a.path), "sha256": raw_sha}
    print()
    print(rec["reading"])
    print("before: %s, surface baseline %s; kept %d of %d" % (
        rec["before"]["verdict"], (rec["before"].get("baseline") or {}).get("accuracy"), rec["n_kept"], rec["n_before"]))
    for h in rec["held_out"]:
        print("held-out half %d: %s, surface baseline %s (n=%d)" % (
            h["applied_to_half"], h["after"]["verdict"], (h["after"].get("baseline") or {}).get("accuracy"), h["n_kept"]))
    if a.out:
        with open(a.out, "w") as fh:
            json.dump({"items": kept, "surface_match": rec}, fh, indent=1, ensure_ascii=False)
        print("wrote %s" % a.out)
    return 0 if rec["repaired"] else 1


def run_audit(only, out_dir, n_perm, jobs):
    A = _load("nulltest_audit", "nulltest_audit.py")
    with open(A.MANIFEST) as fh:
        man = json.load(fh)
    with open(os.path.join(HERE, "nulltest_audit_results.json")) as fh:
        res = {r["name"]: r for r in json.load(fh)["rows"]}
    os.makedirs(out_dir, exist_ok=True)
    summary = []
    for t in man["targets"]:
        if only and t["name"] != only:
            continue
        r = res.get(t["name"])
        if not r or r.get("verdict") != "DISQUALIFIED":
            continue
        print("== %s" % t["name"], flush=True)
        items, info = A.load_items(t)
        has_stratum = any("category" in i for i in items)
        kept, rec = repair(items, "entity", "label", "category" if has_stratum else None, n_perm=n_perm, jobs=jobs,
                           log=lambda m: print("  " + m, flush=True))
        rec["input"] = {"name": t["name"], "url": t.get("url"), "sha256": info["sha256"], "items_total": info["items_total"]}
        fn = os.path.join(out_dir, t["name"].replace("/", "_") + ".json")
        with open(fn, "w") as fh:
            json.dump({"items": kept, "surface_match": rec}, fh, indent=1, ensure_ascii=False)
        summary.append({"name": t["name"], "sha256": info["sha256"], "n_before": rec["n_before"], "n_kept": rec["n_kept"],
                        "kept_fraction": rec["kept_fraction"], "repaired": rec["repaired"], "status": rec["status"],
                        "before": rec["before"]["verdict"],
                        "held_out": [h["after"]["verdict"] for h in rec["held_out"]],
                        "held_out_baseline": [(h["after"].get("baseline") or {}).get("accuracy") for h in rec["held_out"]],
                        "held_out_majority": [(h["after"].get("baseline") or {}).get("majority_accuracy") for h in rec["held_out"]],
                        "after_in_sample": rec["after_in_sample"]["verdict"], "file": os.path.basename(fn)})
        print("  %s" % rec["status"].upper(), flush=True)
    cert_path = os.path.join(out_dir, "surface_match_results.json")
    cert = {"tool": "surface_match", "version": VERSION,
            "tool_sha256": _sha_file(os.path.join(HERE, "surface_match.py")),
            "nulltest_sha256": _sha_file(os.path.join(HERE, "nulltest.py")),
            "surface_baseline_sha256": _sha_file(os.path.join(HERE, "surface_baseline.py")),
            "generated": datetime.date.today().isoformat(), "rows": summary}
    if only and os.path.exists(cert_path):
        with open(cert_path) as fh:
            old = json.load(fh)
        if old.get("tool_sha256") == cert["tool_sha256"]:
            cert["rows"] = [r for r in old["rows"] if r["name"] != only] + summary
    with open(cert_path, "w") as fh:
        json.dump(cert, fh, indent=1)
    print("wrote %s (%d rows)" % (cert_path, len(cert["rows"])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
