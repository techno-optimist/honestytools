#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Rebuild the FLOOR v1 input files from nulltest_audit_manifest.json (the same loaders as /audit).

    python3 benchmark/certified/floor-v1/make_inputs.py OUTDIR [--cache DIR]

Unpaired sets: [{entity, label, category}] exactly as nulltest_audit.load_items returns them.
HaluEval qa/dialogue/summarization: [{right, wrong, cat, _src}], then a seeded 2,000-pair sample
(random.Random(20260724).sample over the loader order, sorted). ragtruth: a seeded 4,000-item sample of
the audit's 8,000, stratified by category (random.Random(20260724), proportional, categories sorted).
TruthfulQA is not rebuilt here (see ../truthfulqa-binary/source_790.json)."""
import argparse, json, os, random, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)
import nulltest_audit as A  # noqa: E402

SEED = 20260724


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--cache", default=os.path.expanduser("~/.cache/honestytools-floor-v1"))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True); os.makedirs(a.cache, exist_ok=True)
    with open(os.path.join(ROOT, "nulltest_audit_manifest.json")) as f:
        M = json.load(f)
    for spec in M["targets"]:
        name = spec["name"]
        if spec.get("explode") and not name.startswith("halueval"):
            continue
        if spec.get("explode"):
            raw, _ = A.fetch(spec["url"], os.path.join(a.cache, spec["cache_as"]))
            rows = A.parse_items(raw, spec)
            (rf, _), (wf, _) = sorted(spec["explode"].items(), key=lambda kv: kv[1])
            data = [{"right": str(r[rf]), "wrong": str(r[wf]), "cat": "_all", "_src": i}
                    for i, r in enumerate(rows) if r.get(rf) and r.get(wf)]
            data = [data[i] for i in sorted(random.Random(SEED).sample(range(len(data)), 2000))]
        else:
            data, _ = A.load_items(spec, a.cache)
            if name == "ragtruth/responses":
                by = {}
                for i, x in enumerate(data):
                    by.setdefault(x["category"], []).append(i)
                rng, sel = random.Random(SEED), []
                for c in sorted(by):
                    sel += rng.sample(by[c], round(len(by[c]) * 4000 / len(data)))
                data = [data[i] for i in sorted(sel)]
                name = "ragtruth/responses-4000"
        fn = os.path.join(a.out, name.replace("/", "__") + ".json")
        with open(fn, "w") as f:
            json.dump(data, f, ensure_ascii=False)
        print(name, len(data), flush=True)


if __name__ == "__main__":
    main()
