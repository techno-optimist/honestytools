# SPDX-License-Identifier: Apache-2.0
"""FLOOR v1.1 held-out probes for UNPAIRED sets: pooled AND within-stratum.

Why: in v1 (heldout_np.evaluate_unpaired) each probe is one AUROC pooled over all items, with
labels permuted within stratum. When the label base rate differs across strata and a probe can
identify the stratum (a refit char n-gram can), the pooled score mostly encodes the stratum, and
so does every permuted null; the test then has little power against cues INSIDE a stratum. A cue
whose direction flips between strata can also cancel in the pooled AUROC. The FLOOR paper's
RAGTruth section works through a real case (pooled char n-gram A* 0.679, raw p 0.367; within
task AUROC 0.695 and 0.382, raw p 0.0005 each).

What v1.1 adds, computed from the SAME permutation draws, folds and out-of-fold char n-gram
scores as v1 (the pooled half reproduces heldout_np.evaluate_unpaired exactly):
  within-stratum A*_w = sum_s n_s * max(AUROC_s, 1 - AUROC_s) / sum_s n_s
over every stratum s that holds both classes (labels are permuted within stratum, so that set of
strata is the same in every permutation). A* is taken per stratum BEFORE averaging, so opposite
directions in different strata do not cancel. Null: the same stratified permutations (char n-gram
refit in each). Westfall-Young min-p over the eight probes, separately for the within family.
The v1.1 held-out gate fails if EITHER family fires (adj p < alpha) or is borderline (< 2 alpha).
Running two families each at alpha makes the gate stricter than either alone, never looser.
With a single stratum the within family equals the pooled one.
"""
import random
import numpy as np
from scipy.stats import rankdata
import heldout as H
import heldout_np as HN

PROBES = HN.PROBES
FIXED = HN.FIXED


def _auc_rows(s, Y):
    """AUROC per column; s (m,) or (m,B), Y (m,B) 0/1; ties 1/2"""
    r = rankdata(s)[:, None] if s.ndim == 1 else rankdata(s, axis=0)
    n1 = Y.sum(0)
    n0 = Y.shape[0] - n1
    return ((r * Y).sum(0) - n1 * (n1 + 1) / 2) / (n1 * n0)


def evaluate_unpaired_v11(items, n_perm=2000, seed=20261026, alpha=0.05, folds=5, text_key="text",
                          label_key="label", stratum_key=None):
    texts = [str(it[text_key]) for it in items]
    y = np.array([int(it[label_key]) for it in items])
    n = len(texts)
    strata = [str(it.get(stratum_key, "_all")) if stratum_key else "_all" for it in items]
    # --- identical construction to heldout_np.evaluate_unpaired ---
    rng = random.Random(seed + 1)
    by = {}
    for i, s in enumerate(strata):
        by.setdefault(s, []).append(i)
    Y = np.zeros((n, n_perm + 1))
    Y[:, 0] = y
    for b in range(1, n_perm + 1):
        yy = y.copy()
        for s, idx in by.items():
            vals = [int(y[i]) for i in idx]
            rng.shuffle(vals)
            yy[idx] = vals
        Y[:, b] = yy
    idx = list(range(n))
    random.Random(seed).shuffle(idx)
    fold = [0] * n
    for pos, i in enumerate(idx):
        fold[i] = pos % folds
    fx = [H.fixed_scores(t) for t in texts]
    SC = {p: np.array([f[p] for f in fx], float) for p in FIXED}
    SC["char_ngram"] = HN._NB(texts, fold, folds).score(Y)
    # pooled (v1)
    AUC = {p: HN._auc_cols(SC[p], Y) for p in PROBES}
    A = {p: np.maximum(AUC[p], 1 - AUC[p]) for p in PROBES}
    obs = {p: float(A[p][0]) for p in PROBES}
    raw, adj = HN._wy(obs, {p: A[p][1:] for p in PROBES})
    neg = SC["negation"]
    n1 = Y.sum(0)
    diff = (neg @ Y) / n1 - (neg @ (1 - Y)) / (n - n1)
    p_neg = (1 + int((np.abs(diff[1:]) >= abs(diff[0]) - 1e-12).sum())) / (n_perm + 1)
    negc = {"items": n, "mean_negation_label1": round(float(neg[y == 1].mean()), 4),
            "mean_negation_label0": round(float(neg[y == 0].mean()), 4),
            "items_with_negation": int((neg > 0).sum()),
            "test": "two-sided permutation test (labels permuted within stratum) on the difference in mean negation-cue count",
            "p_two_sided": float("%.3g" % p_neg), "cue_present": p_neg < alpha}
    pooled = HN._report(obs, {p: float(AUC[p][0]) for p in PROBES}, raw, adj, alpha, n_perm, seed, "n_items", n,
                        {"strata": sorted(by), "null": "labels permuted within stratum, char_ngram refit",
                         "negation_check": negc})
    # within-stratum (v1.1)
    elig = {s: np.array(ix) for s, ix in by.items() if 0 < int(y[ix].sum()) < len(ix)}
    skipped = sorted(set(by) - set(elig))
    W = sum(len(ix) for ix in elig.values())
    AW, per = {}, {}
    for p in PROBES:
        acc = np.zeros(n_perm + 1)
        per[p] = {}
        for s, ix in sorted(elig.items()):
            sub = SC[p][ix]
            a = _auc_rows(sub, Y[ix])
            acc += len(ix) * np.maximum(a, 1 - a)
            per[p][s] = round(float(a[0]), 3)
        AW[p] = acc / W
    obs_w = {p: float(AW[p][0]) for p in PROBES}
    raw_w, adj_w = HN._wy(obs_w, {p: AW[p][1:] for p in PROBES})
    within = HN._report(obs_w, {p: 0.5 for p in PROBES}, raw_w, adj_w, alpha, n_perm, seed, "n_items", W,
                        {"statistic": "size-weighted mean over strata of max(AUROC_s, 1 - AUROC_s)",
                         "strata_scored": sorted(elig), "strata_skipped_one_class": skipped,
                         "null": "labels permuted within stratum (same draws as pooled), char_ngram refit"})
    within["headline"] = within["headline"].replace("worst held-out probe", "worst within-stratum probe")
    for p in PROBES:
        within["probes"][p].pop("direction", None)
        within["probes"][p]["auroc_by_stratum"] = per[p]
    return {"pooled": pooled, "within": within}


def gate(res):
    """v1.1 held-out gate: both families PASS and neither borderline"""
    return all(res[f]["verdict"] == "PASS" and not res[f].get("borderline") for f in ("pooled", "within"))


def worst(res):
    """the probe with the smallest adjusted p over both families. Ties (common at the permutation
    floor) go to the pooled family, so the v1 worst probe is kept unless the within family is
    strictly stronger; within a family, ties go to the larger A* as in v1."""
    c = [(res[f]["probes"][p]["adj_p"], f != "pooled", -res[f]["probes"][p]["astar"], f, p)
         for f in ("pooled", "within") for p in PROBES]
    ap, _, na, f, p = min(c)
    return {"family": f, "probe": p, "astar": -na, "adj_p": ap}
