# SPDX-License-Identifier: Apache-2.0
"""Held-out certification probes for the FLOOR benchmark, plus an explicit negation-cue check.

WHY. certify.py filters pairs against nulltest's own probes (lexicality, words, length, shape),
so certifying with those same probes is circular: the survivors were chosen because those probes
cannot read them. The probes here never take part in filtering. They are different families:

  fixed, no fitting (scored once per text):
    negation      count of negation cues (not, no, never, nothing, none, nobody, cannot, n't, ...)
    hedge         count of hedge / scope words (may, might, some, usually, often, depends, ...)
    function      share of tokens that are English function words
    punct         count of punctuation characters
    digits        count of digits
    caps          count of capitalised tokens after the first
    word_len      mean characters per token
  fitted (out-of-fold, folds grouped by pair, refit inside every permutation):
    char_ngram    multinomial Naive Bayes over character 3- to 5-grams (within word boundaries)

TEST. Same paired statistic as nulltest_paired: per probe, w = share of pairs where the label-1
(wrong) member scores higher, ties 1/2; A*_pair = max(w, 1-w). Null: each pair's labels swapped
with probability 1/2; fixed probes just swap their two scores, char_ngram is refit. Westfall-Young
min-p over the 8 probes, one stratum (_all), because a 49-pair split cannot support per-category
cells.

NEGATION CHECK. Separately, an exact two-sided sign test on the pairs whose two members carry a
different number of negation cues: under the null (no negation cue) the right answer is the more
negated member with probability 1/2. This is the cue the 2026-10-26 run found was not removed by
filtering ("not", "No, there is no ...").
"""
import math, random, re
from collections import Counter

NEG = {"not", "no", "never", "nothing", "none", "nobody", "nowhere", "neither", "nor", "cannot",
       "without", "false", "impossible", "myth", "untrue", "doesn't", "don't", "isn't", "aren't",
       "wasn't", "weren't", "won't", "can't", "didn't", "hasn't", "haven't", "shouldn't",
       "wouldn't", "couldn't", "mustn't"}
HEDGE = {"may", "might", "could", "some", "sometimes", "usually", "often", "depends", "likely",
         "unlikely", "probably", "possibly", "many", "most", "few", "generally", "typically",
         "unknown", "uncertain", "varies", "particular", "specific", "necessarily"}
FUNC = set(("a an the and or but if of in on at to for from by with as is are was were be been "
            "being it its this that these those there here which who whom whose what when where why "
            "how do does did have has had i you he she we they them his her their our your my me "
            "us him so than then too very can will would should shall must").split())
TOK = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?|\d+")


def toks(t):
    return TOK.findall(t)


def fixed_scores(t):
    w = toks(t)
    lw = [x.lower() for x in w]
    n = max(1, len(w))
    return {
        "negation": sum(1 for x in lw if x in NEG or x.endswith("n't")),
        "hedge": sum(1 for x in lw if x in HEDGE),
        "function": sum(1 for x in lw if x in FUNC) / n,
        "punct": sum(1 for c in t if not c.isalnum() and not c.isspace()),
        "digits": sum(1 for c in t if c.isdigit()),
        "caps": sum(1 for x in w[1:] if x[:1].isupper()),
        "word_len": sum(len(x) for x in w) / n,
    }


FIXED = ["negation", "hedge", "function", "punct", "digits", "caps", "word_len"]
PROBES = FIXED + ["char_ngram"]


def char_grams(t):
    out = []
    for w in toks(t.lower()):
        s = "<%s>" % w
        for k in (3, 4, 5):
            out.extend(s[i:i + k] for i in range(len(s) - k + 1))
    return Counter(out)


def _nb_scores(grams, y, fold, k):
    """out-of-fold log-odds(label 1) for every item, multinomial NB, Laplace smoothing"""
    s = [0.0] * len(grams)
    for f in range(k):
        c = [Counter(), Counter()]
        for g, yy, ff in zip(grams, y, fold):
            if ff != f:
                c[yy].update(g)
        vocab = set(c[0]) | set(c[1])
        V = len(vocab) or 1
        t0, t1 = sum(c[0].values()), sum(c[1].values())
        for i, (g, ff) in enumerate(zip(grams, fold)):
            if ff == f:
                s[i] = sum(n * (math.log((c[1][x] + 1) / (t1 + V)) - math.log((c[0][x] + 1) / (t0 + V)))
                           for x, n in g.items())
    return s


def _w(s, y, pairs):
    tot = 0.0
    for a, b in pairs:
        i1, i0 = (a, b) if y[a] == 1 else (b, a)
        tot += 1.0 if s[i1] > s[i0] else (0.5 if s[i1] == s[i0] else 0.0)
    return tot / len(pairs)


def evaluate(rows, n_perm=2000, seed=20261026, alpha=0.05, folds=5):
    """rows: list of {right, wrong}. Returns the held-out family result + negation sign test."""
    texts, y, pairs = [], [], []
    for r in rows:
        a = len(texts)
        texts += [r["right"], r["wrong"]]
        y += [0, 1]
        pairs.append((a, a + 1))
    fx = [fixed_scores(t) for t in texts]
    grams = [char_grams(t) for t in texts]
    order = list(range(len(pairs)))
    random.Random(seed).shuffle(order)
    fold = [0] * len(texts)
    for pos, p in enumerate(order):
        a, b = pairs[p]
        fold[a] = fold[b] = pos % folds

    def stats(yy):
        out = {}
        for p in FIXED:
            out[p] = _w([f[p] for f in fx], yy, pairs)
        out["char_ngram"] = _w(_nb_scores(grams, yy, fold, folds), yy, pairs)
        return {p: max(w, 1 - w) for p, w in out.items()}, out

    obs, obs_w = stats(y)
    rng = random.Random(seed + 1)
    perm = []
    for _ in range(n_perm):
        yy = list(y)
        for a, b in pairs:
            if rng.random() < 0.5:
                yy[a], yy[b] = yy[b], yy[a]
        perm.append(stats(yy)[0])
    raw = {p: (1 + sum(1 for q in perm if q[p] >= obs[p] - 1e-12)) / (n_perm + 1) for p in PROBES}
    # Westfall-Young min-p: p-value every permuted statistic against the same permutation pool
    pool = {p: sorted(q[p] for q in perm) for p in PROBES}

    def pv(p, v):
        import bisect
        ge = len(pool[p]) - bisect.bisect_left(pool[p], v - 1e-12)
        return (1 + ge) / (n_perm + 1)
    minp = [min(pv(p, q[p]) for p in PROBES) for q in perm]
    adj = {p: (1 + sum(1 for m in minp if m <= raw[p] + 1e-12)) / (n_perm + 1) for p in PROBES}
    worst = min(PROBES, key=lambda p: (adj[p], -obs[p]))
    probes = {p: {"astar_pair": round(obs[p], 3),
                  "direction": "wrong scores higher" if obs_w[p] > 0.5 else ("right scores higher" if obs_w[p] < 0.5 else "none"),
                  "raw_p": float("%.3g" % raw[p]), "adj_p": float("%.3g" % adj[p]), "separates": adj[p] < alpha}
              for p in PROBES}
    # explicit negation-cue check: exact two-sided sign test
    more_r = more_w = 0
    for a, b in pairs:
        nr, nw = fx[a]["negation"], fx[b]["negation"]
        more_r += nr > nw
        more_w += nw > nr
    m = more_r + more_w
    k = min(more_r, more_w)
    p_sign = min(1.0, 2 * sum(math.comb(m, i) for i in range(k + 1)) / 2 ** m) if m else 1.0
    neg = {"pairs": len(pairs), "pairs_differing": m, "right_more_negated": more_r,
           "wrong_more_negated": more_w,
           "share_right_more_negated_of_differing": round(more_r / m, 3) if m else None,
           "sign_test_p_two_sided": float("%.3g" % p_sign), "cue_present": p_sign < alpha,
           "lexicon": sorted(NEG) + ["*n't"]}
    sep = [p for p in PROBES if probes[p]["separates"]]
    return {"family": "held-out (never used by the filter)", "probes": probes, "n_pairs": len(pairs),
            "n_perm": n_perm, "seed": seed, "correction": "westfall_young_min_p over %d probes" % len(PROBES),
            "stratum": "_all", "worst": worst, "separating": sep,
            "verdict": "DISQUALIFIED" if sep else "PASS",
            "borderline": (not sep) and adj[worst] < 2 * alpha,
            "headline": "worst held-out probe %s: A*_pair %.3f, adjusted p %.4g over %d probes"
                        % (worst, obs[worst], adj[worst], len(PROBES)),
            "negation_check": neg}
