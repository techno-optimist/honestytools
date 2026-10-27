#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build knowledge-prominence-v3: well-known vs little-known real entities, matched on surface form.

    python3 build_kp_v3.py fetch            # category members + pageviews -> candidates.json.gz (network)
    python3 build_kp_v3.py build            # candidates.json.gz -> battery.json + items.json (offline, deterministic)

Both classes are REAL Wikipedia article subjects. label 0 = high en.wikipedia pageviews (a model
plausibly learned it), label 1 = low pageviews from the SAME seed category (it plausibly did not).
Every rule below is recorded verbatim in battery.json["provenance"].

Built against the surface cues that sank our earlier sets: only ASCII letters and spaces (no digits,
punctuation, diacritics or disambiguators), and each pair is matched exactly on word count,
per-word capitalisation shape, the function words and their positions, first letter, last two
letters, and total length within one character."""
import argparse, gzip, urllib.error, hashlib, json, os, random, re, sys, time, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
UA = {"User-Agent": "honesty-tools-kp-v3/1.0 (https://www.honesty.tools; hello@honesty.tools)"}
API = "https://en.wikipedia.org/w/api.php"

# Seed categories: DIRECT article members only (namespace 0). No subcategory traversal, which is
# what made v1's book stratum 64% fictional characters.
SEEDS = {
    "person": ["American film actresses", "American male film actors"],
    "book": ["American science fiction novels", "American fantasy novels", "American mystery novels"],
    "firm": ["Companies listed on the New York Stock Exchange", "Companies listed on the Nasdaq"],
    "place": ["Cities in Texas", "Cities in Ohio", "Towns in Massachusetts", "Mountains of Switzerland",
              "Towns in Bavaria"],
    "film": ["American drama films"],
}
# US place titles carry the state ("Austin, Texas"); that suffix is removed before any filter.
STRIP_SUFFIX = {"Cities in Texas": ", Texas", "Cities in Ohio": ", Ohio",
                "Towns in Massachusetts": ", Massachusetts"}
PV_DAYS = 60                    # prop=pageviews returns the last 60 days; the fixed WINDOW below is used
WINDOW = ("2026-08-28", "2026-10-25")    # 59 days, fixed; fetched 2026-10-27
MAX_PER_SEED = 6000
HIGH = 50.0                     # label 0: mean daily user pageviews >= HIGH
LOW = 10.0                      # label 1: mean daily user pageviews <= LOW
FORM = re.compile(r"^[A-Za-z]+( [A-Za-z]+){0,4}$")
MIN_LEN, MAX_LEN = 4, 30
FUNCTION = {"a", "an", "the", "of", "and", "or", "in", "on", "at", "to", "for", "from", "by", "with",
            "no", "not", "nor", "is", "are", "was", "be", "as", "into", "over", "under", "my", "your",
            "his", "her", "its", "our", "their", "this", "that", "who", "what", "where", "when"}
SEED = 20261027
MAX_PAIRS_PER_DOMAIN = 10000   # effectively uncapped: persons dominate (see README)


def api(params, tries=10):
    params = dict(params, format="json", formatversion="2", maxlag="5")
    url = API + "?" + urllib.parse.urlencode(params)
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=90) as r:
                d = json.load(r)
            if "error" in d and d["error"].get("code") == "maxlag":
                time.sleep(5); continue
            return d
        except Exception as e:  # noqa: BLE001
            if k == tries - 1:
                raise
            print("api retry %r" % (e,), file=sys.stderr)
            time.sleep(min(60, 3 * 2 ** k))


def list_members(cat):
    out, cont = [], {}
    while True:
        d = api(dict({"action": "query", "list": "categorymembers", "cmtitle": "Category:" + cat,
                      "cmnamespace": "0", "cmtype": "page", "cmlimit": "500"}, **cont))
        out += [m["title"] for m in d["query"]["categorymembers"]]
        if "continue" not in d:
            return out
        cont = d["continue"]


def pageviews_batch(titles):
    """MediaWiki prop=pageviews (per-article daily user views, as served by the Wikimedia
    pageviews service) for up to 50 titles; follows pvipcontinue until every title is covered."""
    got, cont = {}, {}
    while True:
        d = api(dict({"action": "query", "titles": "|".join(titles), "prop": "pageviews",
                      "pvipdays": str(PV_DAYS)}, **cont))
        for p in d["query"]["pages"]:
            if "pageviews" in p:
                got[p["title"]] = p["pageviews"]
        if "continue" not in d:
            return got
        cont = d["continue"]


def eligible(seed, title):
    name = title
    suf = STRIP_SUFFIX.get(seed)
    if suf and name.endswith(suf):
        name = name[: -len(suf)]
    ok = bool(FORM.match(name)) and MIN_LEN <= len(name) <= MAX_LEN and not name.lower().startswith("list of")
    return ok, name


def fetch(out, window):
    rng = random.Random(SEED)
    jobs, counts = [], {}
    for dom, cats in SEEDS.items():
        for cat in cats:
            mem = sorted(set(list_members(cat)))
            ok = [t for t in mem if eligible(cat, t)[0]]
            take = sorted(rng.sample(ok, min(len(ok), MAX_PER_SEED)))
            counts[cat] = {"members": len(mem), "form_eligible": len(ok), "sampled": len(take)}
            print("%-8s %-50s %s" % (dom, cat, counts[cat]), file=sys.stderr)
            jobs += [(dom, cat, t) for t in take]
    titles = sorted({t for _, _, t in jobs})
    pv = {}
    for i in range(0, len(titles), 50):
        pv.update(pageviews_batch(titles[i:i + 50]))
        if i % 1000 == 0:
            print("pageviews %d/%d" % (i, len(titles)), file=sys.stderr)
    rows = [{"domain": d, "seed": c, "title": t, "pv": pv.get(t, {})} for d, c, t in jobs]
    meta = {"retrieved": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "members_api": API + " list=categorymembers cmnamespace=0 cmtype=page",
            "pageviews_api": API + " prop=pageviews pvipdays=%d (daily user views, all access)" % PV_DAYS, "window": [window[0], window[-1]], "per_seed": counts,
            "sampling": "form-eligible titles per seed category; seeded random sample of at most %d (random.Random(%d), seeds in SEEDS order)" % (MAX_PER_SEED, SEED)}
    with gzip.open(out, "wt") as f:
        json.dump({"meta": meta, "rows": rows}, f)
    print("wrote %s (%d rows)" % (out, len(rows)), file=sys.stderr)


def shape(w):
    if w.isupper() and len(w) > 1: return "U"
    if w[0].isupper() and (len(w) == 1 or w[1:].islower()): return "A"
    if w.islower(): return "a"
    return "M"


def form_key(name):
    ws = name.split(" ")
    return (len(ws), tuple(shape(w) for w in ws),
            tuple((i, w.lower()) for i, w in enumerate(ws) if w.lower() in FUNCTION),
            name[0].upper(), name[-2:].lower())


def build(cand, out, window):
    C = json.load(gzip.open(cand, "rt"))
    rng = random.Random(SEED)
    items, excluded = [], {"form": 0, "window": 0, "middle": 0, "dupe_name": 0}
    seen = {}
    for r in C["rows"]:
        ok, name = eligible(r["seed"], r["title"])
        if not ok:
            excluded["form"] += 1; continue
        if not all(d in r["pv"] for d in window):
            excluded["window"] += 1; continue
        views = sum((r["pv"][d] or 0) for d in window) / len(window)
        if LOW < views < HIGH:
            excluded["middle"] += 1; continue
        seen.setdefault(name, []).append(len(items))
        items.append({"entity": name, "label": 0 if views >= HIGH else 1, "category": r["domain"],
                      "views": round(views, 2), "seed_category": r["seed"], "title": r["title"]})
    dup = {n for n, ix in seen.items() if len(ix) > 1}       # a name in two seeds is ambiguous
    excluded["dupe_name"] = sum(len(seen[n]) for n in dup)
    items = [it for it in items if it["entity"] not in dup]
    battery, per = [], {}
    for dom in SEEDS:
        pairs = []
        for cat in SEEDS[dom]:
            hi = sorted([i for i in items if i["seed_category"] == cat and i["label"] == 0], key=lambda i: i["entity"])
            lo = sorted([i for i in items if i["seed_category"] == cat and i["label"] == 1], key=lambda i: i["entity"])
            rng.shuffle(hi); rng.shuffle(lo)
            pool = {}
            for i in lo:
                pool.setdefault(form_key(i["entity"]), []).append(i)
            for h in hi:
                k = form_key(h["entity"])
                cands = [c for c in pool.get(k, []) if abs(len(c["entity"]) - len(h["entity"])) <= 1]
                if not cands:
                    continue
                c = cands[0]
                pool[k].remove(c)
                pairs.append((h, c))
        rng.shuffle(pairs)
        pairs = pairs[:MAX_PAIRS_PER_DOMAIN]
        pairs.sort(key=lambda p: (p[0]["seed_category"], p[0]["entity"]))
        per[dom] = len(pairs)
        for pid, (h, c) in enumerate(pairs):
            for it in (h, c):
                battery.append(dict(it, pair="%s-%03d" % (dom, pid)))
    sha = hashlib.sha256(open(cand, "rb").read()).hexdigest()
    doc = {
        "name": "knowledge-prominence-v3", "built": time.strftime("%Y-%m-%d"), "n": len(battery),
        "n_pairs": len(battery) // 2, "pairs_per_domain": per, "supersedes": "knowledge-prominence-v2 (148 pairs, below the FLOOR floor; its builder was not recorded)",
        "design": "Both classes REAL. label 0 = mean daily en.wikipedia user pageviews >= HIGH; label 1 = <= LOW, from the SAME seed category. Pairs matched exactly on surface form (see provenance.pairing).",
        "label_source": "en.wikipedia pageviews (agent=user), external to any model",
        "provenance": {
            "builder": "build_kp_v3.py", "builder_sha256": hashlib.sha256(open(os.path.abspath(__file__), "rb").read()).hexdigest(),
            "candidates_file": os.path.basename(cand), "candidates_sha256": sha, "fetch": C["meta"],
            "seed_categories": SEEDS, "membership": "direct article members only (namespace 0, gcmtype=page); no subcategory traversal",
            "title_transform": {"strip_suffix": STRIP_SUFFIX},
            "pageview_window": {"first": window[0], "last": window[-1], "days": len(window),
                                "statistic": "mean daily user views over every day of the window (MediaWiki prop=pageviews); a null day counts as 0; a title whose response lacks any window date is excluded"},
            "thresholds": {"HIGH_label0_min_mean_daily": HIGH, "LOW_label1_max_mean_daily": LOW, "between_excluded": True},
            "form_filter": {"regex": FORM.pattern, "min_len": MIN_LEN, "max_len": MAX_LEN, "excluded": "titles starting 'List of'; names appearing under two seed categories"},
            "pairing": {"within": "same seed category", "exact": ["word count", "per-word capitalisation shape (A=Initial, a=lower, U=ALLCAPS, M=mixed)",
                        "function words and their positions (list below)", "first letter", "last two letters (case-folded)"],
                        "length": "total characters within 1", "function_words": sorted(FUNCTION),
                        "order": "seeded shuffle of both pools, then each high item takes the first unused matching low item",
                        "cap": "at most %d pairs per domain, a seeded random subset" % MAX_PAIRS_PER_DOMAIN},
            "seed": SEED, "exclusions": excluded,
        },
        "battery": battery,
    }
    json.dump(doc, open(out, "w"), indent=1, ensure_ascii=True)
    json.dump(battery, open(os.path.join(os.path.dirname(out), "items.json"), "w"), indent=1, ensure_ascii=True)
    print("pairs per domain", per, "total pairs", len(battery) // 2, "excluded", excluded, file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["fetch", "build"])
    ap.add_argument("--candidates", default=os.path.join(HERE, "candidates.json.gz"))
    ap.add_argument("--out", default=os.path.join(HERE, "battery.json"))
    a = ap.parse_args()
    import datetime as dt
    d0, d1 = dt.date.fromisoformat(WINDOW[0]), dt.date.fromisoformat(WINDOW[1])
    window = [(d0 + dt.timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]
    if a.cmd == "fetch":
        fetch(a.candidates, window)
    else:
        build(a.candidates, a.out, window)


if __name__ == "__main__":
    main()
