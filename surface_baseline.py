#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Kevin Russell (Aperture / Project Forty Two)
"""surface_baseline.py — the number a detector has to beat.

    python3 surface_baseline.py battery.json                      # one corpus, keys sniffed as nulltest does
    python3 surface_baseline.py data.jsonl --text-key response --label-key labels --stratum-key task_type
    python3 surface_baseline.py --audit [--only NAME] [--out surface_baseline_results.json]
    python3 surface_baseline.py --selftest [--out surface_baseline_null.json]   # what it says on noise

WHAT IT MEASURES. `nulltest` asks whether any ONE surface probe separates the two classes of a
benchmark (a yes/no verdict, permutation-calibrated). This asks the next question a reader of a
published score needs answered: HOW MUCH accuracy do all six probes together earn on held-out
items, with no model and without reading the question, passage or dialogue the item answers?
That number is the floor a detector or judge has to clear before its score says anything about
hallucination, truth or familiarity. A score below it is worse than a regular expression.

HOW. Five-fold cross-validation with nulltest's own fold assignment. Inside each training fold the
six probe scores are produced out-of-fold again (nested), so the combiner never sees a score that
was fitted on the item it is scoring; the held-out fold is then scored against the whole training
fold's counts. The combiner is a ridge-regularised logistic regression on the six probe scores.
Reported: held-out accuracy with a 95% Clopper–Pearson interval, balanced accuracy, AUROC, the
majority-class accuracy, the in-sample accuracy of knowing each stratum's label rate (the prior a
model gets by recognising the task; descriptive, not held out), and each probe's held-out AUROC
alone. Everything is seeded and deterministic.

WHY NO STRATUM FEATURE. A first version fed the combiner the training fold's label rate for the
item's stratum. On a small battery balanced within each stratum that feature is anti-correlated
with the held-out label (remove one label-1 item and its stratum's training rate drops below a
half), and for training items it carried their own label, so the combiner over-weighted it and
scored our grounding battery at 40% accuracy, below chance. A held-out number that can sit below
chance on noise is not a floor. The stratum prior is therefore reported beside the baseline, not
inside it; the text probes already read the task where the task is written on the surface.

WHAT IT IS NOT. Not a verdict (nulltest gives that), not an attack (a real adversary would do
better than six features and a linear combiner, so this is a LOWER bound on what the surface
yields), and not a statement about any model. Standard library only; no browser port.
"""
import argparse
import datetime
import hashlib
import importlib.util
import json
import math
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
VERSION = "0.1"
SEED = 20260724
K = 5
RIDGE = 1e-2


def _nt():
    spec = importlib.util.spec_from_file_location("nulltest", os.path.join(HERE, "nulltest.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


N = _nt()


# ---------------------------------------------------------------------------------------------
# naive-Bayes log-odds for held-out items from training counts: the same arithmetic as nulltest._NB
# (hapax dropped, +0.5 / +1 smoothing, quantised), written out for an explicit train/test split.
# ---------------------------------------------------------------------------------------------
def _nb_train_test(feats_tr, y_tr, feats_te):
    total, pos = {}, {}
    npos = 0
    for fs, yy in zip(feats_tr, y_tr):
        if yy == 1:
            npos += 1
        for g in fs:
            total[g] = total.get(g, 0) + 1
            if yy == 1:
                pos[g] = pos.get(g, 0) + 1
    nneg = len(feats_tr) - npos
    out = []
    for fs in feats_te:
        sc = 0.0
        for g in fs:
            c = total.get(g, 0)
            if c < 2:
                continue
            p = pos.get(g, 0)
            sc += math.log((p + 0.5) / (npos + 1.0)) - math.log((c - p + 0.5) / (nneg + 1.0))
        out.append(math.floor(sc * 1e9 + 0.5) / 1e9)
    return out


def _inner_oof(feats, y, k=K, seed=SEED):
    """Out-of-fold NB scores inside a training set, so the combiner is fitted on scores produced
    without each item's own label."""
    n = len(feats)
    idx = list(range(n))
    random.Random(seed).shuffle(idx)
    scores = [0.0] * n
    for f in range(k):
        te = idx[f::k]
        te_set = set(te)
        tr = [i for i in idx if i not in te_set]
        if not tr or not te:
            continue
        s = _nb_train_test([feats[i] for i in tr], [y[i] for i in tr], [feats[i] for i in te])
        for i, v in zip(te, s):
            scores[i] = v
    return scores


# ---------------------------------------------------------------------------------------------
# ridge logistic regression by Newton's method, a handful of features, stdlib only
# ---------------------------------------------------------------------------------------------
def _standardise(X):
    d = len(X[0])
    mu = [sum(row[j] for row in X) / len(X) for j in range(d)]
    sd = [math.sqrt(sum((row[j] - mu[j]) ** 2 for row in X) / max(1, len(X) - 1)) or 1.0 for j in range(d)]
    return mu, sd


def _apply(X, mu, sd):
    return [[(row[j] - mu[j]) / sd[j] for j in range(len(mu))] + [1.0] for row in X]


def _solve(A, b):
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(M[r][c]))
        M[c], M[piv] = M[piv], M[c]
        if abs(M[c][c]) < 1e-12:
            M[c][c] = 1e-12
        for r in range(n):
            if r != c:
                f = M[r][c] / M[c][c]
                if f:
                    for k in range(c, n + 1):
                        M[r][k] -= f * M[c][k]
    return [M[i][n] / M[i][i] for i in range(n)]


def _sigmoid(z):
    if z >= 0:
        e = math.exp(-z)
        return 1.0 / (1.0 + e)
    e = math.exp(z)
    return e / (1.0 + e)


def _fit_logistic(Xs, y, ridge=RIDGE, iters=25):
    d = len(Xs[0])
    w = [0.0] * d
    for _ in range(iters):
        g = [0.0] * d
        H = [[0.0] * d for _ in range(d)]
        for row, yy in zip(Xs, y):
            p = _sigmoid(sum(wi * xi for wi, xi in zip(w, row)))
            r = p - yy
            q = p * (1 - p)
            for j in range(d):
                g[j] += r * row[j]
                hj = H[j]
                qx = q * row[j]
                for kk in range(j, d):
                    hj[kk] += qx * row[kk]
        for j in range(d):
            for kk in range(j):
                H[j][kk] = H[kk][j]
            if j < d - 1:                       # no penalty on the intercept
                g[j] += ridge * len(Xs) * w[j]
                H[j][j] += ridge * len(Xs)
        step = _solve(H, g)
        w = [wi - si for wi, si in zip(w, step)]
        if max(abs(s) for s in step) < 1e-8:
            break
    return w


def _predict(Xs, w):
    return [_sigmoid(sum(wi * xi for wi, xi in zip(w, row))) for row in Xs]


# ---------------------------------------------------------------------------------------------
# the baseline
# ---------------------------------------------------------------------------------------------
PROBES = list(N.NULLS.keys())


def baseline(items, text_key, label_key, stratum_key=None, k=K, seed=SEED):
    texts = [N.item_text(it, text_key) for it in items]
    y = [int(it[label_key]) for it in items]
    strata = [str(it.get(stratum_key)) for it in items] if stratum_key else None
    if strata is not None and len(set(strata)) < 2:
        strata = None
    n = len(texts)
    if n < 30 or len(set(y)) < 2:
        raise ValueError("%d items, %d distinct labels — too small or single-class" % (n, len(set(y))))

    feats = {}
    fixed = {}
    for name, (kind, fn) in N.NULLS.items():
        if kind == "nb":
            feats[name] = [fn(t) for t in texts]
        else:
            fixed[name] = [float(fn(t)) for t in texts]

    folds = N._fold_of(n, k=k, seed=seed)
    prob = [0.0] * n
    probe_oof = {name: [0.0] * n for name in PROBES}
    strat_major = [0] * n
    for f in range(k):
        tr = [i for i in range(n) if folds[i] != f]
        te = [i for i in range(n) if folds[i] == f]
        if not tr or not te:
            continue
        y_tr = [y[i] for i in tr]
        if len(set(y_tr)) < 2:
            continue
        cols_tr, cols_te = [], []
        for name in PROBES:
            if name in feats:
                ftr = [feats[name][i] for i in tr]
                inner = _inner_oof(ftr, y_tr, k=k, seed=seed)
                outer = _nb_train_test(ftr, y_tr, [feats[name][i] for i in te])
            else:
                inner = [fixed[name][i] for i in tr]
                outer = [fixed[name][i] for i in te]
            cols_tr.append(inner)
            cols_te.append(outer)
            for i, v in zip(te, outer):
                probe_oof[name][i] = v
        maj = 1 if sum(y_tr) * 2 >= len(y_tr) else 0
        for i in te:
            strat_major[i] = maj
        X_tr = [list(col) for col in zip(*cols_tr)]
        X_te = [list(col) for col in zip(*cols_te)]
        mu, sd = _standardise(X_tr)
        w = _fit_logistic(_apply(X_tr, mu, sd), y_tr)
        for i, p in zip(te, _predict(_apply(X_te, mu, sd), w)):
            prob[i] = p

    pred = [1 if p >= 0.5 else 0 for p in prob]
    correct = sum(1 for a, b in zip(pred, y) if a == b)
    npos = sum(y)
    tpr = sum(1 for a, b in zip(pred, y) if a == 1 and b == 1) / npos
    tnr = sum(1 for a, b in zip(pred, y) if a == 0 and b == 0) / (n - npos)
    lo, hi = N.clopper_pearson(correct, n)
    majority = max(npos, n - npos) / n
    # the in-sample accuracy of knowing each stratum's label rate and nothing else: descriptive, like
    # nulltest's label_rate_by_stratum, not a held-out classifier (see WHY NO STRATUM FEATURE)
    if strata is not None:
        by = {}
        for s_, yy in zip(strata, y):
            by.setdefault(s_, [0, 0])[yy] += 1
        strat_acc = sum(max(c) for c in by.values()) / n
    else:
        strat_acc = majority
    per_probe = {}
    for name in PROBES:
        a = N.auroc(y, probe_oof[name])
        per_probe[name] = None if a is None else round(max(a, 1 - a), 3)
    rep = {
        "tool": "surface_baseline", "version": VERSION, "nulltest_version": N.VERSION,
        "n": n, "n_label1": npos, "n_label0": n - npos, "folds": k, "seed": seed,
        "features": PROBES,
        "n_strata": len(set(strata)) if strata is not None else 1,
        "accuracy": round(correct / n, 4), "accuracy_ci95": [round(lo, 4), round(hi, 4)],
        "balanced_accuracy": round((tpr + tnr) / 2, 4),
        "auroc": round(N.auroc(y, prob), 4),
        "majority_accuracy": round(majority, 4),
        "stratum_prior_accuracy": round(strat_acc, 4),
        "probe_auroc_held_out": per_probe,
    }
    rep["headline"] = headline(rep)
    return rep


def headline(r):
    gain = r["accuracy"] - r["majority_accuracy"]
    s = ("surface baseline: %.1f%% held-out accuracy [%.1f%%, %.1f%%] from six probes, against %.1f%% for "
         "the majority class%s; balanced accuracy %.1f%%, AUROC %.3f."
         % (100 * r["accuracy"], 100 * r["accuracy_ci95"][0], 100 * r["accuracy_ci95"][1],
            100 * r["majority_accuracy"],
            (" and %.1f%% for knowing each stratum's label rate" % (100 * r["stratum_prior_accuracy"])) if r["n_strata"] > 1 else "",
            100 * r["balanced_accuracy"], r["auroc"]))
    if gain <= 0.01:
        s += " The surface earns nothing beyond the class prior here."
    return s


def _sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


# ---------------------------------------------------------------------------------------------
# --audit: every manifest target, through the same load_items pipeline as the table
# ---------------------------------------------------------------------------------------------
def run_audit(only=None, out=None, verbose=True):
    spec = importlib.util.spec_from_file_location("nulltest_audit", os.path.join(HERE, "nulltest_audit.py"))
    A = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(A)
    man = json.load(open(A.MANIFEST))
    rows = []
    for t in man["targets"]:
        if only and t["name"] != only:
            continue
        if not t.get("url"):
            continue
        row = {"name": t["name"], "label_kind": t.get("label_kind")}
        try:
            items, info = A.load_items(t)
            has_stratum = any("category" in i for i in items)
            rep = baseline(items, "entity", "label", "category" if has_stratum else None)
        except Exception as e:
            row["status"] = "failed"
            row["note"] = str(e)[:160]
            rows.append(row)
            if verbose:
                print("  %-32s FAILED %s" % (t["name"][:32], row["note"]), flush=True)
            continue
        row.update({"status": "scored", "sha256": info["sha256"], "items_total": info["items_total"]})
        row.update(rep)
        rows.append(row)
        if verbose:
            print("  %-32s acc %.3f [%.3f, %.3f]  majority %.3f  stratum-prior %.3f  bal %.3f  auroc %.3f"
                  % (t["name"][:32], rep["accuracy"], rep["accuracy_ci95"][0], rep["accuracy_ci95"][1],
                     rep["majority_accuracy"], rep["stratum_prior_accuracy"], rep["balanced_accuracy"],
                     rep["auroc"]), flush=True)
    cert = {"tool": "surface_baseline", "version": VERSION, "tool_sha256": _sha(os.path.join(HERE, "surface_baseline.py")),
            "nulltest_sha256": _sha(os.path.join(HERE, "nulltest.py")), "nulltest_version": N.VERSION,
            "generated": datetime.date.today().isoformat(), "folds": K, "seed": SEED, "rows": rows}
    if out:
        if only and os.path.exists(out):       # --only replaces one row in an existing certificate
            old = json.load(open(out))
            if old.get("tool_sha256") != cert["tool_sha256"] or old.get("nulltest_sha256") != cert["nulltest_sha256"]:
                raise SystemExit("surface_baseline: the existing certificate was made by a different tool; "
                                 "regenerate all rows rather than mixing them")
            keep = [r for r in old["rows"] if r["name"] != only]
            cert["rows"] = keep + rows
            cert["generated"] = old.get("generated", cert["generated"])
        json.dump(cert, open(out, "w"), indent=1)
        if verbose:
            print("wrote %s (%d rows)" % (out, len(cert["rows"])))
    return cert


# ---------------------------------------------------------------------------------------------
# --selftest: what this baseline reports on batteries with NO signal, by size. Cross-validated
# accuracy on null data sits slightly BELOW the majority rate at small n (a combiner fitted to noise
# generalises a little worse than chance; the effect shrinks with n), so the floor this tool
# reports is conservative, and a gain inside the null band below is not evidence of a surface.
# ---------------------------------------------------------------------------------------------
NULL_SIZES = (162, 324, 702, 1496)
NULL_SEEDS = (30, 20, 12, 8)


def selftest(out=None, verbose=True):
    rows = []
    for n, seeds in zip(NULL_SIZES, NULL_SEEDS):
        gains, aurocs = [], []
        for s in range(seeds):
            items = N._noise_battery(7000 + s, 6, "coinword", n=n)
            r = baseline(items, "entity", "label", "category")
            gains.append(r["accuracy"] - r["majority_accuracy"])
            aurocs.append(r["auroc"])
        gains.sort()
        row = {"n": n, "seeds": seeds, "gain_mean": round(sum(gains) / seeds, 4), "gain_min": round(gains[0], 4),
               "gain_max": round(gains[-1], 4), "gain_p975": round(gains[min(seeds - 1, int(0.975 * seeds))], 4),
               "auroc_mean": round(sum(aurocs) / seeds, 4), "auroc_min": round(min(aurocs), 4)}
        rows.append(row)
        if verbose:
            print("  n=%-5d %2d null batteries: gain over majority mean %+.3f [%+.3f, %+.3f], AUROC mean %.3f (min %.3f)"
                  % (n, seeds, row["gain_mean"], row["gain_min"], row["gain_max"], row["auroc_mean"], row["auroc_min"]), flush=True)
    cert = {"tool": "surface_baseline", "version": VERSION, "tool_sha256": _sha(os.path.join(HERE, "surface_baseline.py")),
            "nulltest_sha256": _sha(os.path.join(HERE, "nulltest.py")), "generated": datetime.date.today().isoformat(),
            "construction": "nulltest._noise_battery(seed, 6 strata, 'coinword'): balanced labels, random tokens, no signal",
            "reading": "a reported gain over the majority rate that lies inside [gain_min, gain_max] for the nearest n is "
                       "within what this protocol reports on noise; below gain_min is the protocol's own pessimism, not a defect",
            "rows": rows}
    if out:
        json.dump(cert, open(out, "w"), indent=1)
        if verbose:
            print("wrote %s" % out)
    return cert


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("path", nargs="?", help="battery JSON/JSONL/CSV, as nulltest reads it")
    ap.add_argument("--text-key"); ap.add_argument("--label-key"); ap.add_argument("--stratum-key")
    ap.add_argument("--positive-label")
    ap.add_argument("--audit", action="store_true", help="score every target in nulltest_audit_manifest.json")
    ap.add_argument("--only", help="with --audit: one target by name")
    ap.add_argument("--out", help="with --audit: write the certificate here")
    ap.add_argument("--json", action="store_true", help="print the full report as JSON")
    ap.add_argument("--selftest", action="store_true", help="report what the baseline says on no-signal batteries, by size")
    a = ap.parse_args(argv[1:])
    if a.selftest:
        selftest(out=a.out)
        return 0
    if a.audit:
        run_audit(only=a.only, out=a.out)
        return 0
    if not a.path:
        ap.error("give a battery path or --audit")
    items = N.read_items(a.path)
    tk, lk, sk = N.sniff_keys(items, a.text_key, a.label_key, a.stratum_key)
    items, _pos = N.coerce_labels(items, lk, a.positive_label)
    rep = baseline(items, tk, lk, sk)
    if a.json:
        print(json.dumps(rep, indent=1))
    else:
        print(rep["headline"])
        print("per-probe held-out A*: " + ", ".join("%s %s" % (k, v) for k, v in rep["probe_auroc_held_out"].items()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
