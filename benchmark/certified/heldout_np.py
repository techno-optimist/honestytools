# SPDX-License-Identifier: Apache-2.0
"""Vectorised held-out probes for FLOOR v1 (numpy + scipy), paired and unpaired.

PAIRED: evaluate_paired(rows, n_perm, seed) reproduces heldout.evaluate exactly in design: the
same eight probes, the same pair-grouped folds, the SAME permutation draws (random.Random(seed+1),
one rng.random() < 0.5 per pair per permutation, in order), the same multinomial Naive Bayes with
Laplace smoothing, and the same Westfall-Young min-p. Only the arithmetic is vectorised, so
scores can differ in the last float bits; tests/test_floor_v1.py checks the outputs agree with
heldout.evaluate on real pairs.

UNPAIRED: evaluate_unpaired(items, n_perm, seed, strata) runs the same eight probes on a set of
items with no pairing. Statistic per probe: A* = max(AUROC, 1 - AUROC) of the probe score for
label 1 vs label 0 (ties count 1/2); char_ngram is scored out-of-fold (5 folds, items shuffled by
seed) and refit inside every permutation. Null: labels permuted within stratum. Westfall-Young
min-p over the eight probes. The negation check is a two-sided permutation test (same draws) on
the difference in mean negation-cue count between the classes.
"""
import bisect, math, random
import numpy as np
from scipy import sparse
from scipy.stats import rankdata
import heldout as H

PROBES = H.PROBES
FIXED = H.FIXED


def _matrix(texts):
    vocab, rows, cols, vals = {}, [], [], []
    for i, t in enumerate(texts):
        for g, n in H.char_grams(t).items():
            j = vocab.setdefault(g, len(vocab))
            rows.append(i); cols.append(j); vals.append(n)
    return sparse.csr_matrix((np.array(vals, float), (rows, cols)), shape=(len(texts), max(1, len(vocab))))


class _NB:
    """multinomial NB, Laplace, out-of-fold; label-independent parts precomputed"""

    def __init__(self, texts, fold, k):
        X = _matrix(texts)
        self.k = k
        self.parts = []
        fold = np.asarray(fold)
        for f in range(k):
            tr, te = np.where(fold != f)[0], np.where(fold == f)[0]
            if len(tr) == 0 or len(te) == 0:
                continue
            Xtr = X[tr]
            V = max(1, int((np.asarray(Xtr.sum(0)).ravel() > 0).sum()))
            self.parts.append((tr, te, Xtr.T.tocsr(), X[te], V))
        self.n = len(texts)

    def score(self, y, chunk=64):
        """y: (n,) or (n, B) 0/1 -> out-of-fold log-odds, same shape (columns scored in chunks)"""
        y = np.asarray(y, float)
        one = y.ndim == 1
        Y = y[:, None] if one else y
        if Y.shape[1] > chunk:
            S = np.hstack([self.score(Y[:, c:c + chunk], chunk) for c in range(0, Y.shape[1], chunk)])
            return S
        S = np.zeros_like(Y)
        for tr, te, XtrT, Xte, V in self.parts:
            Ytr = Y[tr]
            c1 = XtrT @ Ytr                      # (vocab, B)
            ctot = np.asarray(XtrT.sum(1))        # (vocab, 1)
            c0 = ctot - c1
            t1 = c1.sum(0)
            t0 = c0.sum(0)
            w = np.log((c1 + 1) / (t1 + V)) - np.log((c0 + 1) / (t0 + V))
            S[te] = Xte @ w
        return S[:, 0] if one else S


def _wy(obs, perm):
    """obs: {probe: A*}, perm: {probe: array(B)} -> raw, adj"""
    B = len(next(iter(perm.values())))
    raw = {p: (1 + int((perm[p] >= obs[p] - 1e-12).sum())) / (B + 1) for p in PROBES}
    pool = {p: np.sort(perm[p]) for p in PROBES}
    def pv(p, v):
        ge = len(pool[p]) - np.searchsorted(pool[p], v - 1e-12, side="left")
        return (1 + ge) / (B + 1)
    minp = np.min(np.vstack([pv(p, perm[p]) for p in PROBES]), axis=0)
    adj = {p: (1 + int((minp <= raw[p] + 1e-12).sum())) / (B + 1) for p in PROBES}
    return raw, adj


def _report(obs, wdir, raw, adj, alpha, n_perm, seed, n_label, n, extra):
    worst = min(PROBES, key=lambda p: (adj[p], -obs[p]))
    probes = {p: {"astar": round(obs[p], 3),
                  "direction": "label-1 scores higher" if wdir[p] > 0.5 else ("label-0 scores higher" if wdir[p] < 0.5 else "none"),
                  "raw_p": float("%.3g" % raw[p]), "adj_p": float("%.3g" % adj[p]), "separates": adj[p] < alpha}
              for p in PROBES}
    sep = [p for p in PROBES if probes[p]["separates"]]
    out = {"family": "held-out (never used by the filter)", "backend": "heldout_np", "probes": probes,
           n_label: n, "n_perm": n_perm, "seed": seed,
           "correction": "westfall_young_min_p over %d probes" % len(PROBES), "worst": worst,
           "separating": sep, "verdict": "DISQUALIFIED" if sep else "PASS",
           "borderline": (not sep) and adj[worst] < 2 * alpha,
           "headline": "worst held-out probe %s: A* %.3f, adjusted p %.4g over %d probes"
                       % (worst, obs[worst], adj[worst], len(PROBES))}
    out.update(extra)
    return out


def evaluate_paired(rows, n_perm=2000, seed=20261026, alpha=0.05, folds=5):
    texts = []
    for r in rows:
        texts += [r["right"], r["wrong"]]
    P = len(rows)
    fx = [H.fixed_scores(t) for t in texts]
    order = list(range(P))
    random.Random(seed).shuffle(order)
    fold = [0] * len(texts)
    for pos, p in enumerate(order):
        fold[2 * p] = fold[2 * p + 1] = pos % folds
    rng = random.Random(seed + 1)
    swaps = np.zeros((n_perm, P), bool)
    for b in range(n_perm):
        for p in range(P):
            swaps[b, p] = rng.random() < 0.5
    # label matrix: column 0 observed, then permutations. y[2p]=0 (right), y[2p+1]=1 (wrong), swapped where swaps
    Bt = n_perm + 1
    sw = np.vstack([np.zeros((1, P), bool), swaps])     # (Bt, P)
    Y = np.zeros((2 * P, Bt))
    Y[1::2] = (~sw).T
    Y[0::2] = sw.T

    def wstat(s):
        # s: (2P,) or (2P, Bt). share of pairs where the label-1 member scores higher (ties 1/2)
        if s.ndim == 1:
            d = s[1::2] - s[0::2]                 # wrong-as-shipped minus right
            d = np.where(sw.T, -d[:, None], d[:, None])
        else:
            one = np.where(sw.T, s[0::2], s[1::2])
            zero = np.where(sw.T, s[1::2], s[0::2])
            d = one - zero
        return ((d > 0) + 0.5 * (d == 0)).mean(0)   # (Bt,)
    W = {p: wstat(np.array([f[p] for f in fx], float)) for p in FIXED}
    nb = _NB(texts, fold, folds)
    W["char_ngram"] = wstat(nb.score(Y))
    A = {p: np.maximum(W[p], 1 - W[p]) for p in PROBES}
    obs = {p: float(A[p][0]) for p in PROBES}
    raw, adj = _wy(obs, {p: A[p][1:] for p in PROBES})
    more_r = sum(1 for p in range(P) if fx[2 * p]["negation"] > fx[2 * p + 1]["negation"])
    more_w = sum(1 for p in range(P) if fx[2 * p + 1]["negation"] > fx[2 * p]["negation"])
    m = more_r + more_w
    k = min(more_r, more_w)
    p_sign = min(1.0, 2 * sum(math.comb(m, i) for i in range(k + 1)) / 2 ** m) if m else 1.0
    neg = {"pairs": P, "pairs_differing": m, "right_more_negated": more_r, "wrong_more_negated": more_w,
           "share_right_more_negated_of_differing": round(more_r / m, 3) if m else None,
           "sign_test_p_two_sided": float("%.3g" % p_sign), "cue_present": p_sign < alpha}
    rep = _report(obs, {p: float(W[p][0]) for p in PROBES}, raw, adj, alpha, n_perm, seed, "n_pairs", P,
                  {"stratum": "_all", "negation_check": neg})
    for p in PROBES:   # same key as heldout.py
        rep["probes"][p]["astar_pair"] = rep["probes"][p].pop("astar")
        d = rep["probes"][p]["direction"]
        rep["probes"][p]["direction"] = {"label-1 scores higher": "wrong scores higher",
                                         "label-0 scores higher": "right scores higher"}.get(d, d)
    return rep


def _auc_cols(s, Y):
    """AUROC of score s (n,) or (n,B) for label 1 over columns of Y (n,B), ties 1/2"""
    if s.ndim == 1:
        r = rankdata(s)[:, None]
    else:
        r = np.apply_along_axis(rankdata, 0, s)
    n1 = Y.sum(0)
    n0 = Y.shape[0] - n1
    return ((r * Y).sum(0) - n1 * (n1 + 1) / 2) / (n1 * n0)


def evaluate_unpaired(items, n_perm=2000, seed=20261026, alpha=0.05, folds=5, text_key="text",
                      label_key="label", stratum_key=None):
    texts = [str(it[text_key]) for it in items]
    y = np.array([int(it[label_key]) for it in items])
    n = len(texts)
    strata = [str(it.get(stratum_key, "_all")) if stratum_key else "_all" for it in items]
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
    AUC = {p: _auc_cols(np.array([f[p] for f in fx], float), Y) for p in FIXED}
    nb = _NB(texts, fold, folds)
    S = nb.score(Y)
    AUC["char_ngram"] = _auc_cols(S, Y)
    A = {p: np.maximum(AUC[p], 1 - AUC[p]) for p in PROBES}
    obs = {p: float(A[p][0]) for p in PROBES}
    raw, adj = _wy(obs, {p: A[p][1:] for p in PROBES})
    neg = np.array([f["negation"] for f in fx], float)
    n1 = Y.sum(0)
    diff = (neg @ Y) / n1 - (neg @ (1 - Y)) / (n - n1)
    p_neg = (1 + int((np.abs(diff[1:]) >= abs(diff[0]) - 1e-12).sum())) / (n_perm + 1)
    negc = {"items": n, "mean_negation_label1": round(float(neg[y == 1].mean()), 4),
            "mean_negation_label0": round(float(neg[y == 0].mean()), 4),
            "items_with_negation": int((neg > 0).sum()),
            "test": "two-sided permutation test (labels permuted within stratum) on the difference in mean negation-cue count",
            "p_two_sided": float("%.3g" % p_neg), "cue_present": p_neg < alpha}
    return _report(obs, {p: float(AUC[p][0]) for p in PROBES}, raw, adj, alpha, n_perm, seed, "n_items", n,
                   {"strata": sorted(by), "null": "labels permuted within stratum, char_ngram refit",
                    "negation_check": negc})
