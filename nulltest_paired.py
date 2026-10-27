#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""nulltest PAIRED MODE (--pair-key) — within-pair sign-flip permutation.

The unpaired nulltest asks: can a model-free reader tell label-1 items from label-0 items by
their wording? On a corpus of MATCHED PAIRS (a right and a wrong answer to the same question, a
true and a false version of the same claim) that question is the wrong one: the two classes
share a question, a topic, a template, and pooling them throws that structure away. The paired
question is sharper: inside each pair, does the surface probe score the label-1 member above the
label-0 member more often than chance?

  statistic   per probe and stratum, w = share of pairs where s(label1) > s(label0), ties 1/2;
              A*_pair = max(w, 1 - w) (two-sided, as in the unpaired tool)
  folds       BY PAIR: both members of a pair always sit in the same held-out fold, so a fitted
              probe never sees the partner of the item it is scoring
  null        within-pair SIGN FLIP: each pair's two labels are swapped with probability 1/2,
              independently, and every fitted probe is refit. Under "no surface signal inside
              pairs" the labels within a pair are exchangeable, so this is an exact permutation
              null that holds question, topic and stratum fixed
  correction  Westfall-Young min-p over the (probe, stratum) family, same pool rule as v1.4

The core unpaired path in nulltest.py is untouched (and stays bit-identical to the JS port);
this module only reuses its probes. Pairs that do not have exactly one label-0 and one label-1
member are dropped and counted, never silently repaired.
"""
import bisect
import math
import random

import nulltest as N

PAIRED_VERSION = "0.1"


def make_pairs(items, label_key, pair_key):
    groups, order = {}, []
    for i, it in enumerate(items):
        k = it.get(pair_key)
        if k is None:
            continue
        k = str(k)
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(i)
    pairs, dropped = [], 0
    for k in order:
        idx = groups[k]
        labs = [int(items[i][label_key]) for i in idx]
        if len(idx) == 2 and sorted(labs) == [0, 1]:
            pairs.append((k, idx[0], idx[1]))
        else:
            dropped += 1
    n_unkeyed = sum(1 for it in items if it.get(pair_key) is None)
    return pairs, dropped, n_unkeyed


class _PairNB(N._NB):
    """_NB with an externally supplied fold assignment (pair-grouped)."""

    def __init__(self, texts, featfn, fold):
        feats = [featfn(t) for t in texts]
        vocab = sorted({g for f in feats for g in f})
        idx_of = {g: i for i, g in enumerate(vocab)}
        self.items = [[idx_of[g] for g in f] for f in feats]
        k = max(fold) + 1 if fold else 0
        self.folds = []
        for f in range(k):
            f_te = [i for i in range(len(texts)) if fold[i] == f]
            te = set(f_te)
            tr = [i for i in range(len(texts)) if i not in te]
            if not tr or not f_te:
                continue
            total = [0] * len(vocab)
            for i in tr:
                for g in self.items[i]:
                    total[g] += 1
            te_feats = [(i, [g for g in self.items[i] if total[g] >= 2]) for i in f_te]
            self.folds.append((tr, te_feats, total))


def _pair_folds(n_items, pairs, k=N.N_FOLDS, seed=20260724):
    order = list(range(len(pairs)))
    random.Random(seed).shuffle(order)
    fold = [0] * n_items
    for pos, p in enumerate(order):
        _, a, b = pairs[p]
        fold[a] = fold[b] = pos % k
    return fold


def _win(s, y, plist):
    """share of pairs where label-1 member outscores label-0 member (ties 1/2), and #non-ties"""
    tot, nt = 0.0, 0
    for _, a, b in plist:
        one, zero = (a, b) if y[a] == 1 else (b, a)
        d = s[one] - s[zero]
        if d > 0:
            tot += 1.0; nt += 1
        elif d < 0:
            nt += 1
        else:
            tot += 0.5
    return tot / len(plist), nt


class _Prep:
    def __init__(self, texts, pairs, fold):
        self.fixed, self.nb = {}, {}
        for name, (kind, fn) in N.NULLS.items():
            if kind == "score":
                self.fixed[name] = [float(fn(t)) for t in texts]
            else:
                self.nb[name] = _PairNB(texts, fn, fold)

    def scores(self, name, y):
        if name in self.fixed:
            return self.fixed[name]
        return self.nb[name].score(y)


def _score_cells(prep, y, cells):
    out = {}
    for name in N.NULLS:
        s = prep.scores(name, y)
        for st, plist in cells:
            w, nt = _win(s, y, plist)
            out[(name, st)] = None if nt == 0 else max(w, 1.0 - w)
    return out


_PAR = {}


def _worker(args):
    b, ys = args
    return b, _score_cells(_PAR["prep"], ys, _PAR["cells"])


def evaluate_paired(items, text_key, label_key, pair_key, stratum_key=None, n_perm=600,
                    seed=20260724, alpha=0.05, min_cell=12, jobs=1):
    texts = [N.item_text(it, text_key) for it in items]
    y = [int(it[label_key]) for it in items]
    pairs, dropped, unkeyed = make_pairs(items, label_key, pair_key)
    if stratum_key:
        pst = [str(items[a].get(stratum_key, "_all")) for _, a, _ in pairs]
    else:
        pst = ["_all"] * len(pairs)
    cells = []
    for st in sorted(set(pst)):
        cells.append((st, [p for p, s in zip(pairs, pst) if s == st]))
    if len(cells) > 1:
        cells.insert(0, ("_all", list(pairs)))
    fold = _pair_folds(len(items), pairs)
    prep = _Prep(texts, pairs, fold)
    obs = _score_cells(prep, y, cells)
    keys = sorted(k for k, v in obs.items() if v is not None)
    rep = {"tool": "nulltest", "version": N.VERSION, "mode": "paired",
           "paired_version": PAIRED_VERSION, "probes": list(N.NULLS),
           "n_items": len(items), "n_pairs": len(pairs), "pairs_dropped_not_1v1": dropped,
           "items_without_pair_key": unkeyed, "pair_key": pair_key,
           "strata": [st for st, _ in cells], "alpha": alpha, "family_size": len(keys),
           "correction": "westfall_young_min_p",
           "null": "labels swapped within each pair with probability 1/2 (sign flip), every probe refit",
           "statistic": "A*_pair = max(w, 1-w), w = share of pairs where label-1 outscores label-0 (ties 1/2)",
           "folds": "grouped by pair (both members held out together)",
           "nulls": {}}
    if not keys:
        rep.update(verdict="NOT_SCORABLE", worst_cell=None,
                   headline="no scorable cell — nothing was tested; this is NOT a pass.")
        return rep
    need = int(math.ceil(2.0 * (len(keys) + 1) / alpha / 100.0)) * 100
    rep["n_perm_requested"] = n_perm
    n_perm = max(n_perm, need)
    rep["n_perm"] = n_perm
    rng = random.Random(seed)
    perms = []
    for _ in range(n_perm):
        ys = list(y)
        for _, a, b in pairs:
            if rng.random() < 0.5:
                ys[a], ys[b] = y[b], y[a]
        perms.append(ys)
    results = None
    if jobs and jobs > 1:
        try:
            import multiprocessing as mp
            ctx = mp.get_context("fork")
            _PAR.update(prep=prep, cells=cells)
            results = [None] * n_perm
            with ctx.Pool(jobs) as pool:
                for b, sc in pool.imap_unordered(_worker, list(enumerate(perms)), chunksize=4):
                    results[b] = sc
            _PAR.clear()
        except (ImportError, ValueError):
            results = None
    if results is None:
        results = [_score_cells(prep, ys, cells) for ys in perms]
    draws = {k: [r.get(k) for r in results] for k in keys}
    cols = {k: sorted(v for v in draws[k] if v is not None) for k in keys}
    raw = {k: N._pval(cols[k], obs[k]) for k in keys}
    q = []
    for b in range(n_perm):
        best = 1.0
        for k in keys:
            t = draws[k][b]
            if t is None:
                continue
            col = cols[k]
            p = (len(col) - bisect.bisect_left(col, t) + (1.0 if obs[k] >= t else 0.0)) / (len(col) + 1.0)
            best = min(best, p)
        q.append(best)
    q.sort()
    adj = {k: (bisect.bisect_right(q, raw[k]) + 1.0) / (n_perm + 1.0) for k in keys}
    worst, wkey = None, None
    for k in keys:
        n_p = len(dict(cells)[k[1]])
        eff = (obs[k] - 0.5) * math.sqrt(n_p)
        cand = (adj[k], -eff)
        if worst is None or cand < worst:
            worst, wkey = cand, k
    for name in N.NULLS:
        e = {}
        for st, plist in cells:
            k = (name, st)
            if k not in keys:
                continue
            col = cols[k]
            thr = col[min(len(col) - 1, int(round(0.95 * (len(col) - 1))))] if col else None
            e[st] = {"n_pairs": len(plist), "astar_pair": round(obs[k], 3),
                     "threshold": None if thr is None else round(thr, 3),
                     "raw_p": round(raw[k], 4), "adj_p": round(adj[k], 4),
                     "separates": bool(adj[k] < alpha), "underpowered": len(plist) < min_cell}
        if e:
            rep["nulls"][name] = {"per_stratum": e}
    wn, ws = wkey
    wc = rep["nulls"][wn]["per_stratum"][ws]
    rep["worst_cell"] = dict(wc, null=wn, stratum=ws)
    rep["n_separating_cells"] = sum(1 for k in keys if adj[k] < alpha)
    rep["verdict"] = "DISQUALIFIED" if adj[wkey] < alpha else "PASS"
    rep["borderline"] = bool(alpha / 2 <= wc["adj_p"] < 2 * alpha)
    rep["p_floor"] = round(1.0 / (n_perm + 1), 6)
    rep["saturated"] = bool(wc["adj_p"] <= round(1.0 / (n_perm + 1), 4) + 1e-12)
    rep["headline"] = ("worst cell A*_pair = %s in %s/%s (%d pairs), family-adjusted p = %s over %d cells"
                       % (wc["astar_pair"], wn, ws, wc["n_pairs"], wc["adj_p"], len(keys)))
    rep["scope"] = N.SCOPE
    return rep


def pair_margins(items, text_key, label_key, pair_key, seed=20260724):
    """Out-of-fold, per-pair surface margins on the OBSERVED labels: for each probe, +1 if the
    label-1 member outscores the label-0 member, -1 if the reverse, 0 on a tie. Used by the
    certified-suite filter; it is a description, not a test."""
    texts = [N.item_text(it, text_key) for it in items]
    y = [int(it[label_key]) for it in items]
    pairs, _, _ = make_pairs(items, label_key, pair_key)
    fold = _pair_folds(len(items), pairs, seed=seed)
    prep = _Prep(texts, pairs, fold)
    out = {k: {} for k, _, _ in pairs}
    for name in N.NULLS:
        s = prep.scores(name, y)
        for k, a, b in pairs:
            one, zero = (a, b) if y[a] == 1 else (b, a)
            d = s[one] - s[zero]
            out[k][name] = (d > 0) - (d < 0)
    return out
