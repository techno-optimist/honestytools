#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Kevin Russell (Aperture / Project Forty Two)
"""nulltest_dossier.py — AUDIT_DOSSIER.md: what carries the label in every audited benchmark.

    python3 nulltest_dossier.py            # score every manifest target with --explain, then render

The audit table says WHICH benchmarks fail the surface floor. This says WHAT carries the label in
each one: the full nulltest report plus the --explain feature tables, with counts and example
items, one section per row. Every number in AUDIT_DOSSIER.md is copied from those reports; the
file is rendered, never edited by hand, so a regenerated table cannot disagree with its dossier.

Reports are cached per row in dossier_cache/ (gitignored, keyed by corpus sha256 and tool), so a
re-run after one manifest change scores only the new row. The full run from cold takes about an
hour on four cores, most of it TruthfulQA (8,900 permutations), HaluEval summarization and
RAGTruth.
"""
import contextlib, datetime, io, json, os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__)); S = HERE
CACHE_DIR = os.path.join(HERE, "dossier_cache"); os.makedirs(CACHE_DIR, exist_ok=True)
sys.path.insert(0, HERE)
import nulltest as N, nulltest_audit as A

man_path = A.MANIFEST
cache_path = os.path.join(CACHE_DIR, "dossier_cache.json")
cache = json.load(open(cache_path)) if os.path.exists(cache_path) else {}
man = json.load(open(man_path))
def log(m): print(time.strftime("%H:%M:%S"), m, flush=True)
for spec in man["targets"]:
    name = spec["name"]
    if name in cache or not spec.get("url"):
        continue
    t0 = time.time()
    try:
        items, info = A.load_items(spec)
        sha = info["sha256"]
    except Exception as e:
        cache[name] = {"error": str(e)[:200]}; json.dump(cache, open(cache_path, "w"), indent=1); log("%s: %s" % (name, e)); continue
    has_stratum = any("category" in i for i in items)
    sk = "category" if has_stratum else None
    with contextlib.redirect_stdout(io.StringIO()):
        rep = N.evaluate(items, "entity", "label", sk, n_perm=600, jobs=4)
        rep["explain"] = N.explain(items, "entity", "label", sk, rep, max_cells=12)
    rep["sha256"] = sha; rep["n_items"] = len(items); rep["secs"] = round(time.time() - t0)
    rep["label_meaning"] = spec.get("label_kind")
    cache[name] = rep
    json.dump(cache, open(cache_path, "w"), indent=1)
    log("%s: %s %s/%s A* %s adj %s (%ds)" % (name, rep["verdict"], rep["worst_cell"]["null"], rep["worst_cell"]["stratum"], rep["worst_cell"]["astar"], rep["worst_cell"]["adj_p"], rep["secs"]))
log("scored")


# ---- render
cache = json.load(open(os.path.join(CACHE_DIR, "dossier_cache.json")))
man = json.load(open(os.path.join(HERE, "nulltest_audit_manifest.json")))
version = None
MEAN = {
 "length": "one class is systematically longer; a scorer that rewards or penalises length earns part of its score without reading",
 "words": "particular words or phrases mark one class; the two classes were written by different processes or from different templates",
 "lexicality": "the two classes are spelled or formatted differently at the character level (register, diacritics, punctuation, name shape)",
 "shape": "digits, capitals or punctuation mark one class; usually an ID-shaped class against a word-shaped one",
 "alpha_first": "the alphabet position of the first initial separates the classes; for entity lists this means one class was sampled from the top of a sorted list, for sentences it means the classes tend to open with different words (a template)",
 "alpha_last": "the alphabet position of the last initial separates the classes; for entity lists one class was sampled from a list sorted by surname, for sentences the classes tend to end with different words",
}
def pct(x): return "%.1f%%" % (100 * x)
out = []
out.append("# Audit dossier: what carries the label in each benchmark\n")
out.append("Generated %s from `nulltest_audit_manifest.json` and a full `nulltest` report with `--explain` for every "
           "row, by `nulltest_dossier.py`; every number below is copied from those reports. The verdicts match "
           "`nulltest_audit_results.json`. A DISQUALIFIED verdict is a measurement about a corpus, not a finding of error "
           "or misconduct by its authors; the feature tables are in-sample descriptions for finding the artifact, and the "
           "verdict is held-out and permutation-calibrated. Read each row under its own n: A* is not comparable across "
           "rows of different size.\n" % datetime.date.today().isoformat())
out.append("Reproduce any row with `python3 nulltest_audit.py --only NAME`, or run the one-line command under it.\n")
# summary table
out.append("| benchmark | items | cells | verdict | worst cell | A* | adj p | what carries it |\n|---|---|---|---|---|---|---|---|")
rows = []
for t in man["targets"]:
    r = cache.get(t["name"])
    if not r or "error" in r: continue
    version = version or r.get("version")
    w = r["worst_cell"]
    carry = "—" if r["verdict"] != "DISQUALIFIED" else ", ".join(sorted({c["probe"] for c in r["explain"]["cells"]}))
    out.append("| %s | %d | %d | %s%s | %s/%s | %.3f | %.4f | %s |" % (
        t["name"], r["n"], r["family_size"], r["verdict"], " (borderline)" if r.get("borderline") else "",
        w["null"], w["stratum"], w["astar"], w["adj_p"], carry))
    rows.append((t, r))
out.append("")
ours = [t for t, r in rows if t["name"].startswith("aperture/")]
dq = [t for t, r in rows if r["verdict"] == "DISQUALIFIED"]
out.append("%d rows scored with nulltest %s; %d DISQUALIFIED, %d of them ours (of %d of ours in the table).\n"
           % (len(rows), version, len(dq), len([t for t in dq if t["name"].startswith("aperture/")]), len(ours)))
for t, r in rows:
    w = r["worst_cell"]
    out.append("## %s\n" % t["name"])
    out.append("**What it is.** %s %s\n" % (t.get("paper", ""), t.get("note", "")))
    out.append("**Scored.** %d items (%d label-0, %d label-1; label-1 = %s), %d cells over %d probes%s, %d permutations%s. "
               "Source `%s`, sha256 `%s`.\n" % (
        r["n"], r["n_real"], r["n_fake"], {"real_vs_fabricated_entity": "fabricated", "true_vs_false_statement": "false",
                                           "right_vs_wrong_answer": "the wrong/hallucinated answer",
                                           "hallucinated_vs_faithful_response": "hallucinated",
                                           "honest_vs_deceptive_response": "deceptive"}.get(t.get("label_kind"), "positive class"),
        r["family_size"], len(r["probes"]), (" and %d strata" % len(r["strata"])) if len(r["strata"]) > 1 else "",
        r["n_perm"], (" (raised from %d for the family size)" % r["n_perm_requested"]) if r["n_perm"] != r["n_perm_requested"] else "",
        t["url"], r["sha256"]))
    rates = r.get("label_rate_by_stratum") or {}
    if len(rates) > 1:
        vals = [v["label1_rate"] for v in rates.values()]
        if max(vals) - min(vals) >= 0.1:
            out.append("**Label rate by stratum** ranges from %s to %s; the stratified null conditions on this, so it is "
                       "described, not tested. A model can exploit it even though this test does not flag it.\n" % (pct(min(vals)), pct(max(vals))))
    out.append("**Verdict: %s%s.** %s %d of %d cells separate after correction. %s\n" % (
        r["verdict"], " (BORDERLINE)" if r.get("borderline") else "", r["headline"], r["n_separating_cells"], r["family_size"],
        r["power"].get("reading", "")))
    if r["verdict"] == "DISQUALIFIED":
        out.append("**What carries the label.**\n")
        for c in r["explain"]["cells"][:4]:
            line = "- `%s`/%s (n=%d, A* %.3f, adj p %.4f): %s" % (c["probe"], c["stratum"], c["n"], c["astar"], c["adj_p"], c.get("reading", ""))
            f1 = c.get("favours_label1") or []; f0 = c.get("favours_label0") or []
            if f1 or f0:
                def fmt(fs): return ", ".join("`%s` (%d vs %d)" % (f["feature"].replace("`", "'"), f["in_label1"], f["in_label0"]) for f in fs[:5])
                if f1: line += "\n  - marks label-1: %s" % fmt(f1)
                if f0: line += "\n  - marks label-0: %s" % fmt(f0)
            ex = c.get("examples") or {}
            if ex.get("label1") or ex.get("label0"):
                line += "\n  - e.g. label-1 %s; label-0 %s" % (" / ".join("“%s”" % e[:80] for e in ex.get("label1", [])[:2]), " / ".join("“%s”" % e[:80] for e in ex.get("label0", [])[:2]))
            out.append(line)
        probes = sorted({c["probe"] for c in r["explain"]["cells"]})
        out.append("\n**What it means for a score on this benchmark.** " + " ".join("%s: %s." % (p, MEAN.get(p, "")) for p in probes) +
                   " A model-based score on this corpus needs a surface control before the number is read as %s.\n" %
                   {"right_vs_wrong_answer": "knowledge of the answer", "hallucinated_vs_faithful_response": "hallucination detection",
                    "true_vs_false_statement": "truth discrimination",
                    "honest_vs_deceptive_response": "deception detection"}.get(t.get("label_kind"), "entity familiarity"))
    else:
        c = (r["explain"]["cells"] or [None])[0]
        if c:
            out.append("**Closest cell.** `%s`/%s A* %.3f, adj p %.4f: %s\n" % (c["probe"], c["stratum"], c["astar"], c["adj_p"], c.get("reading", "")))
        out.append("A PASS is the absence of a detected surface artifact at this size, not a certificate of quality; see the POWER line above for what could be missed.\n")
    if t.get("explode"):
        cmd = "python3 nulltest.py %s --explode \"%s\"%s%s --explain" % (
            t.get("cache_as", "FILE"), ",".join("%s=%s" % kv for kv in t["explode"].items()),
            " --split \"%s\"" % t["split"] if t.get("split") else "", " --stratum-key %s" % t["stratum_field"] if t.get("stratum_field") else "")
    else:
        cmd = "python3 nulltest_audit.py --only %s" % t["name"]
    out.append("Reproduce: `%s`\n" % cmd)
open(os.path.join(HERE, "AUDIT_DOSSIER.md"), "w").write("\n".join(out))
print("wrote AUDIT_DOSSIER.md: %d rows" % len(rows))


# ---- machine-readable summary, so the audit table can carry a "what carries it" column that is
# copied from the same reports as the dossier, with a link into the matching dossier section.
def anchor(name):
    """GitHub's heading slug: lower-case, punctuation other than hyphens dropped, spaces to hyphens."""
    import re
    return re.sub(r"[^a-z0-9 _-]", "", name.lower()).replace(" ", "-")

summary = {"generated": datetime.date.today().isoformat(), "version": version, "source": "nulltest_dossier.py",
           "dossier": "AUDIT_DOSSIER.md", "rows": []}
for t, r in rows:
    cells = r["explain"]["cells"]
    c = cells[0] if cells else None
    entry = {"name": t["name"], "sha256": r["sha256"], "verdict": r["verdict"], "borderline": bool(r.get("borderline")),
             "anchor": anchor(t["name"]), "probes": [], "phrase": ""}
    if r["verdict"] == "DISQUALIFIED" and c:
        entry["probes"] = sorted({x["probe"] for x in cells})
        entry["worst_explain_cell"] = {k: c[k] for k in ("probe", "stratum", "n", "astar", "adj_p")}
        phrase = c.get("reading", "")
        if c["stratum"] != "_all":
            phrase = "%s: %s" % (c["stratum"], phrase)
        entry["phrase"] = phrase
    summary["rows"].append(entry)
json.dump(summary, open(os.path.join(HERE, "nulltest_dossier_summary.json"), "w"), indent=1, ensure_ascii=False)
print("wrote nulltest_dossier_summary.json: %d rows" % len(summary["rows"]))
