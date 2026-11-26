#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Kevin Russell (Aperture / Project Forty Two)
"""census_cache_card — P93 cache-storage custody and resampling floor for every census reading.

A census reading is incomplete until it carries this card. The card does not
run a model. It checks that a result JSON (or a published HTML card) records
how the cache was stored and that any claim it makes is justified by the
resampling floors and the codec shifts beside it.

WHAT THIS CHECKS

  (a) CUSTODY. Every census cache records forward dtype, storage codec (bit
      depth, per-token vs per-channel scaling, any standardization or
      rotation), and, for a retained cache, a cache hash. A census reading
      names the cache it was read from.

  (b) TWO FLOORS. Every statistic is reported beside a document half-split
      floor (p95). Any population difference is reported beside a
      label-permutation floor (p95), and is called real only when it sits
      strictly above that permutation p95.

  (c) CODEC CHECK PER STATISTIC. On a calibration subset, each statistic's
      shift from an fp32 reference under the codec actually used is reported.
      The statistic is codec-stable only when the absolute shift is strictly
      below the half-split p95; otherwise it must be flagged or recomputed
      from fp32. Dictionary-based shares may use the identical-bytes ceiling
      and reseed floor of arXiv:2611.08551; that calibration is optional when
      no dictionary share is claimed.

  (d) CONTENT STATISTICS take PC1 from the full covariance, not a subsample.

    python3 -B census_cache_card.py CARD.json
    python3 -B census_cache_card.py --from-pilot PILOT.json
    python3 -B census_cache_card.py --check-html research.html --card CARD.json

Stdlib only. Exit 0 if the card is complete and any credit it claims is
justified. Exit 1 if required fields are missing or a claim is unjustified.
"""
from __future__ import annotations

import argparse
import json
import re
import sys

SCHEMA = "honestytools.census_cache_card.v1"
STAT_KEYS = ("w1", "erank", "pr", "pr_c", "w1c", "pr_m", "nmass", "nspk")
CONTENT_STATS = {"pr_c", "w1c"}
FLOOR_TOL = 0.006
SHIFT_TOL = 0.006
REQUIRED_HTML_PHRASES = (
    "controls card",
    "cache",
    "resampl",
    "floor",
    "4-bit",
    "8-bit",
    "16-bit",
)

CODEC_META = {
    "fp32": {
        "label": "fp32 reference",
        "bit_depth": 32,
        "forward_dtype": "fp32",
        "scaling": None,
        "standardization": None,
        "rotation": None,
    },
    "fp16": {
        "label": "fp16 storage",
        "bit_depth": 16,
        "forward_dtype": "fp32",
        "scaling": None,
        "standardization": None,
        "rotation": None,
    },
    "bf16": {
        "label": "bf16 storage",
        "bit_depth": 16,
        "forward_dtype": "fp32",
        "scaling": None,
        "standardization": None,
        "rotation": None,
    },
    "int8_tok": {
        "label": "int8 per-token",
        "bit_depth": 8,
        "forward_dtype": "fp32",
        "scaling": "per-token-absmax",
        "standardization": None,
        "rotation": None,
    },
    "int4_tok": {
        "label": "int4 per-token",
        "bit_depth": 4,
        "forward_dtype": "fp32",
        "scaling": "per-token-absmax",
        "standardization": None,
        "rotation": None,
    },
    "int4_std": {
        "label": "int4 per-channel standardized",
        "bit_depth": 4,
        "forward_dtype": "fp32",
        "scaling": "per-channel",
        "standardization": "per-channel",
        "rotation": None,
    },
    "ulp_1e-6": {
        "label": "one-in-a-million ulp change",
        "bit_depth": 32,
        "forward_dtype": "fp32",
        "scaling": None,
        "standardization": None,
        "rotation": None,
    },
    "bf16fwd": {
        "label": "bf16 forward pass",
        "bit_depth": 16,
        "forward_dtype": "bf16",
        "scaling": None,
        "standardization": None,
        "rotation": None,
    },
}

STAT_LABELS = {
    "pr_c": "content participation ratio",
    "nspk": "spike count",
    "pr_m": "massive-rows-dropped participation ratio",
    "w1": "top-eigenvalue share",
    "erank": "effective rank",
    "pr": "raw participation ratio",
    "w1c": "content top-eigenvalue share",
    "nmass": "massive-row count",
}


class CardError(ValueError):
    pass


def _num(value, field):
    if value is None:
        raise CardError("missing number: %s" % field)
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise CardError("not a number: %s (%r)" % (field, value)) from exc


def _abs(value):
    return abs(float(value))


def evaluate_cache(cache, issues):
    label = str(cache.get("id") or cache.get("name") or cache.get("codec") or "cache")
    bit_depth = cache.get("bit_depth")
    if bit_depth is None and isinstance(cache.get("codec"), dict):
        bit_depth = cache["codec"].get("bit_depth")
    if bit_depth is None:
        issues.append("%s: bit depth is missing" % label)
    else:
        try:
            bit_depth = int(bit_depth)
        except (TypeError, ValueError):
            issues.append("%s: bit depth is not an integer" % label)
            bit_depth = None
    forward = cache.get("forward_dtype")
    if not forward:
        issues.append("%s: forward dtype is missing" % label)
    retained = bool(cache.get("retained"))
    cache_hash = cache.get("cache_hash") or cache.get("hash")
    if retained and not cache_hash:
        issues.append("%s: retained cache is missing a cache hash" % label)
    return {
        "id": label,
        "bit_depth": bit_depth,
        "forward_dtype": forward,
        "scaling": cache.get("scaling"),
        "standardization": cache.get("standardization"),
        "retained": retained,
        "cache_hash": cache_hash,
    }


def evaluate_reading(reading, caches_by_id, issues):
    label = str(reading.get("id") or reading.get("name") or "reading")
    cache_id = reading.get("cache_id") or reading.get("cache")
    if isinstance(cache_id, dict):
        cache_id = cache_id.get("id") or cache_id.get("name")
    if not cache_id:
        issues.append("%s: does not name the cache it was read from" % label)
        return None
    cache_id = str(cache_id)
    if caches_by_id and cache_id not in caches_by_id:
        issues.append("%s: names cache %r, which is not on the card" % (label, cache_id))
    return {"id": label, "cache_id": cache_id}


def _floor_p95(floors, key, kind, issues):
    if not isinstance(floors, dict):
        return None
    block = floors.get(key)
    if block is None:
        return None
    if isinstance(block, dict):
        raw = block.get("p95")
    else:
        raw = block
    if raw is None:
        issues.append("%s floor for %s is missing p95" % (kind, key))
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        issues.append("%s floor p95 for %s is not a number" % (kind, key))
        return None


def evaluate_codec(codec, floors_half, issues, notes):
    name = str(codec.get("id") or codec.get("name") or codec.get("codec") or "codec")
    label = codec.get("label") or name
    shifts = codec.get("shift_vs_fp32") or codec.get("shift_vs_fp32_sci") or {}
    claimed = codec.get("codec_stable") or codec.get("stable") or {}
    report_shifts = {}
    unstable = []
    if not isinstance(shifts, dict) or not shifts:
        issues.append("%s: codec shifts from an fp32 reference are missing" % name)
        shifts = {}
    for key, raw in shifts.items():
        try:
            shift = float(raw)
        except (TypeError, ValueError):
            issues.append("%s: shift for %s is not a number" % (name, key))
            continue
        report_shifts[key] = shift
        p95 = _floor_p95(floors_half, key, "half-split", issues)
        stable = p95 is not None and abs(shift) < p95
        if not stable:
            unstable.append(key)
        claimed_flag = None
        if isinstance(claimed, dict):
            claimed_flag = claimed.get(key)
        elif isinstance(claimed, bool) and len(shifts) == 1:
            claimed_flag = claimed
        if claimed_flag is True and not stable:
            issues.append(
                "%s: card marks %s codec-stable, but the shift (%.3f) is not below the half-split p95 (%s)"
                % (name, key, shift, p95)
            )
        if claimed_flag is False and stable:
            issues.append(
                "%s: card flags %s as unstable, but the shift sits below the half-split p95"
                % (name, key)
            )
    return {
        "id": name,
        "label": label,
        "bit_depth": codec.get("bit_depth"),
        "shifts": report_shifts,
        "unstable": unstable,
    }


def evaluate_population_diffs(diffs, floors_perm, issues):
    if not diffs:
        return []
    reports = []
    if not isinstance(floors_perm, dict) or not floors_perm:
        issues.append("population differences are present, but the label-permutation floor is missing")
        floors_perm = {}
    for item in diffs:
        if not isinstance(item, dict):
            continue
        key = item.get("statistic") or item.get("key")
        if not key:
            issues.append("a population difference is missing its statistic name")
            continue
        try:
            value = float(item.get("value") if item.get("value") is not None else item.get("diff"))
        except (TypeError, ValueError):
            issues.append("%s: population difference is not a number" % key)
            continue
        p95 = _floor_p95(floors_perm, key, "label-permutation", issues)
        real = p95 is not None and abs(value) > p95
        if item.get("real") is True and not real:
            issues.append(
                "%s: card calls the population difference real, but |%.3f| is not above the permutation p95 (%s)"
                % (key, value, p95)
            )
        reports.append({"statistic": key, "value": value, "permutation_p95": p95, "real": real})
    return reports


def evaluate_card(doc):
    issues = []
    notes = []
    if not isinstance(doc, dict):
        raise CardError("card is not a JSON object")
    schema = doc.get("schema")
    if schema and schema != SCHEMA:
        issues.append("unexpected schema %r (expected %s)" % (schema, SCHEMA))

    caches = doc.get("caches") or []
    cache_reports = [evaluate_cache(c, issues) for c in caches] if isinstance(caches, list) else []
    if not cache_reports:
        issues.append("caches list is missing")
    caches_by_id = {c["id"]: c for c in cache_reports if c}

    readings = doc.get("readings") or []
    reading_reports = []
    if isinstance(readings, list):
        for item in readings:
            report = evaluate_reading(item, caches_by_id, issues)
            if report:
                reading_reports.append(report)

    floors_half = doc.get("floors", {}).get("half_split") if isinstance(doc.get("floors"), dict) else doc.get("floor_halfsplit")
    floors_perm = doc.get("floors", {}).get("label_permutation") if isinstance(doc.get("floors"), dict) else doc.get("floor_labelperm")
    # also accept the pilot key names via evaluate later
    if not floors_half:
        issues.append("document half-split floor is missing")
        floors_half = {}
    else:
        # content and spike floors are the ones the card must carry
        for key in ("pr_c", "nspk"):
            if _floor_p95(floors_half, key, "half-split", issues) is None and key not in (floors_half or {}):
                issues.append("half-split floor for %s is missing" % key)

    codecs = doc.get("codecs") or []
    codec_reports = []
    if not codecs:
        issues.append("codec calibration list is missing")
    else:
        for codec in codecs:
            codec_reports.append(evaluate_codec(codec, floors_half, issues, notes))

    diffs = doc.get("population_differences") or doc.get("sci_minus_rec") or []
    if isinstance(diffs, dict):
        # map of statistic -> value under one codec (usually fp32)
        diffs = [{"statistic": k, "value": v, "real": None} for k, v in diffs.items()]
    diff_reports = evaluate_population_diffs(diffs, floors_perm, issues) if diffs else []
    if diffs and not floors_perm:
        pass  # already flagged

    pc1 = doc.get("content_pc1_from") or doc.get("content_pc1_source")
    if not pc1:
        issues.append("content statistics do not say whether PC1 came from the full covariance")
    else:
        norm = re.sub(r"[\s-]+", "_", str(pc1).strip().lower())
        if norm in {"subsample", "subsampled", "subset", "4000_row_subsample"}:
            issues.append("content statistics took PC1 from a subsample; P93 requires the full covariance")
        elif norm not in {"full_covariance", "full", "exact_covariance", "full_cov"}:
            issues.append("content_pc1_from %r is not the full covariance" % pc1)

    # Unexplained non-monotone spike-count shift: 8-bit vs 4-bit.
    by_depth = {}
    for codec in codec_reports:
        depth = codec.get("bit_depth")
        nspk = (codec.get("shifts") or {}).get("nspk")
        if depth in (4, 8) and nspk is not None:
            # prefer per-token (no extra standardization) when several 4-bit codecs exist
            prev = by_depth.get(depth)
            name = str(codec.get("id") or "")
            if prev is None or ("std" not in name and "std" in str(prev.get("id") or "")):
                by_depth[depth] = codec
    if 4 in by_depth and 8 in by_depth:
        s4 = abs(by_depth[4]["shifts"]["nspk"])
        s8 = abs(by_depth[8]["shifts"]["nspk"])
        if s8 > s4 + 1e-12:
            noted = bool(doc.get("spike_count_not_monotone_in_bit_depth")) or any(
                "not monotone" in str(n).lower() or "8-bit" in str(n).lower() and "4-bit" in str(n).lower()
                for n in (doc.get("notes") or [])
            )
            # A short dedicated field or a notes entry is enough on the JSON card.
            if not noted:
                # Also accept a prose note field.
                blob = " ".join(str(x) for x in (doc.get("notes") or [])) + " " + str(doc.get("unexplained") or "")
                noted = "8-bit" in blob and "4-bit" in blob
            if not noted:
                issues.append(
                    "8-bit moved the spike count more than 4-bit did (%.0f vs %.0f); the card must state this"
                    % (s8, s4)
                )
            else:
                notes.append("8-bit moved the spike count more than 4-bit did")

    complete = not issues
    return {
        "complete": complete,
        "issues": issues,
        "notes": notes,
        "caches": cache_reports,
        "readings": reading_reports,
        "codecs": codec_reports,
        "population_differences": diff_reports,
        "content_pc1_from": pc1,
    }


def _floor_map(block):
    out = {}
    for key, val in (block or {}).items():
        if isinstance(val, dict):
            out[key] = {"median": val.get("median"), "p95": val.get("p95")}
        else:
            out[key] = {"p95": val}
    return out


def cards_from_pilot(pilot):
    """Map the 2026-11-26 SmolLM2 census-codec box-pilot JSON into a v1 card."""
    if not isinstance(pilot, dict) or "codecs" not in pilot or "config" not in pilot:
        raise CardError("not a tool-14 P93 pilot JSON")
    floors_half = _floor_map(pilot.get("floor_halfsplit_absdiff"))
    floors_perm = _floor_map(pilot.get("floor_labelperm_absdiff"))
    caches = []
    codecs = []
    for name, block in (pilot.get("codecs") or {}).items():
        meta = dict(CODEC_META.get(name) or {"label": name, "bit_depth": None, "forward_dtype": "fp32"})
        cache = {
            "id": "pilot-%s" % name,
            "codec": name,
            "label": meta.get("label") or name,
            "bit_depth": meta.get("bit_depth"),
            "forward_dtype": meta.get("forward_dtype"),
            "scaling": meta.get("scaling"),
            "standardization": meta.get("standardization"),
            "rotation": meta.get("rotation"),
            "retained": False,
            "cache_hash": None,
        }
        caches.append(cache)
        shifts = block.get("shift_vs_fp32_sci") or {}
        stable = {}
        for key, raw in shifts.items():
            p95 = (floors_half.get(key) or {}).get("p95")
            if p95 is not None:
                stable[key] = abs(float(raw)) < float(p95)
        codecs.append(
            {
                "id": name,
                "label": cache["label"],
                "bit_depth": cache["bit_depth"],
                "cache_id": cache["id"],
                "shift_vs_fp32": {k: float(v) for k, v in shifts.items()},
                "codec_stable": stable,
                "sci": block.get("sci"),
                "rec": block.get("rec"),
                "sci_minus_rec": block.get("sci_minus_rec"),
            }
        )
    fp32 = (pilot.get("codecs") or {}).get("fp32") or {}
    diffs = []
    for key, value in (fp32.get("sci_minus_rec") or {}).items():
        p95 = (floors_perm.get(key) or {}).get("p95")
        diffs.append(
            {
                "statistic": key,
                "populations": "sci-rec",
                "value": float(value),
                "real": None if p95 is None else abs(float(value)) > float(p95),
            }
        )
    s8 = float(((pilot.get("codecs") or {}).get("int8_tok") or {}).get("shift_vs_fp32_sci", {}).get("nspk") or 0)
    s4 = float(((pilot.get("codecs") or {}).get("int4_tok") or {}).get("shift_vs_fp32_sci", {}).get("nspk") or 0)
    unexplained = (
        "8-bit moved the spike count more than 4-bit did (+%.0f vs +%.0f). We do not yet have an explanation."
        % (s8, s4)
    )
    return {
        "schema": SCHEMA,
        "title": "Census cache-storage controls card",
        "proposal": "P93",
        "model": (pilot.get("config") or {}).get("model"),
        "layer": (pilot.get("config") or {}).get("layer"),
        "tokens_per_doc": (pilot.get("config") or {}).get("tokens_per_doc"),
        "docs_per_pop": (pilot.get("config") or {}).get("docs_per_pop"),
        "d": (pilot.get("config") or {}).get("d"),
        "populations": ["sci", "rec"],
        "floors": {"half_split": floors_half, "label_permutation": floors_perm},
        "content_pc1_from": "full_covariance",
        "spike_count_not_monotone_in_bit_depth": bool(abs(s8) > abs(s4)),
        "unexplained": unexplained,
        "notes": [unexplained],
        "caches": caches,
        "codecs": codecs,
        "population_differences": diffs,
        "readings": [
            {
                "id": "example-sci-fp32-content-pr",
                "cache_id": "pilot-fp32",
                "statistic": "pr_c",
                "value": (fp32.get("sci") or {}).get("pr_c"),
            }
        ],
        "pilot": {
            "script": "tool14_p93_cache_codec_floor_pilot_20261126.py",
            "runtime_s": pilot.get("elapsed_s"),
            "notes": (
                "Small box pilot on SmolLM2-135M, layer 15, 160 sci and 160 rec "
                "20 Newsgroups documents, 128 tokens each. Our numbers. This is "
                "not a reproduction of Yeung (arXiv:2611.08551)."
            ),
        },
    }


def fmt_shift(value):
    v = float(value)
    if abs(v - round(v)) < 1e-9:
        return "%+d" % int(round(v))
    # one or two decimals as needed
    text = "%.2f" % v
    if text.endswith("0"):
        text = "%.1f" % v
    if not text.startswith("-") and v != 0:
        text = "+" + text
    return text


def check_html(html, card=None):
    issues = []
    if 'id="census-cache-controls"' not in html and "id='census-cache-controls'" not in html:
        issues.append("HTML is missing id=\"census-cache-controls\"")
    start = html.find('id="census-cache-controls"')
    if start < 0:
        start = html.find("id='census-cache-controls'")
    snippet = html
    if start >= 0:
        sec = html.rfind("<section", 0, start)
        end = html.find("</section>", start)
        if sec >= 0 and end > sec:
            snippet = html[sec : end + len("</section>")]
    text = re.sub(r"<[^>]+>", " ", snippet)
    text_l = text.lower()
    for phrase in REQUIRED_HTML_PHRASES:
        if phrase not in text_l:
            if phrase == "resampl" and "half-split" in text_l and "permut" in text_l:
                continue
            if phrase == "4-bit" and ("int4" in text_l or "4 bit" in text_l):
                continue
            if phrase == "8-bit" and ("int8" in text_l or "8 bit" in text_l):
                continue
            if phrase == "16-bit" and ("fp16" in text_l or "16 bit" in text_l):
                continue
            issues.append("HTML is missing the phrase %r" % phrase)
    banned = re.search(r"\b(solved|proved|caught)\b|the AI honesty layer", text, re.I)
    if banned:
        issues.append("HTML card uses the wording %r" % banned.group(0))
    if card:
        blob = text.replace("\u2212", "-")
        # Unexplained result must be stated plainly.
        if "8-bit" in text_l and "4-bit" in text_l:
            pass
        else:
            issues.append("HTML does not state the 8-bit vs 4-bit spike-count result")
        if "27" not in blob:
            issues.append("HTML is missing the 8-bit spike-count shift 27")
        # 4-bit content PR ~6.04 and 4-bit spike +1
        if "6.04" not in blob and "6.0" not in blob:
            issues.append("HTML is missing the 4-bit content participation-ratio shift 6.04")
        for codec in card.get("codecs") or []:
            if codec.get("id") == "int8_tok":
                nspk = (codec.get("shift_vs_fp32") or {}).get("nspk")
                if nspk is not None and fmt_shift(nspk).lstrip("+") not in blob and "27" not in blob:
                    issues.append("HTML is missing the 8-bit spike-count shift")
    return issues


def _load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _dump(doc):
    return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("path", nargs="?", help="a v1 controls card JSON, or a pilot JSON with --from-pilot")
    p.add_argument("--from-pilot", action="store_true", help="read a P93 box-pilot JSON and emit a v1 card")
    p.add_argument("-o", "--output", help="write the card JSON here (with --from-pilot)")
    p.add_argument("--check-html", metavar="HTML", help="check a published page for the required card copy")
    p.add_argument("--card", help="v1 card JSON to check the HTML against")
    args = p.parse_args(argv)

    if args.from_pilot:
        if not args.path:
            print("census_cache_card: --from-pilot needs a pilot JSON path", file=sys.stderr)
            return 1
        card = cards_from_pilot(_load_json(args.path))
        report = evaluate_card(card)
        text = _dump(card)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(text)
        else:
            sys.stdout.write(text)
        for note in report["notes"]:
            print("note: %s" % note, file=sys.stderr)
        if not report["complete"]:
            for issue in report["issues"]:
                print("census_cache_card: %s" % issue, file=sys.stderr)
            return 1
        return 0

    html_issues = []
    card = None
    report = None
    if args.path:
        card = _load_json(args.path)
        report = evaluate_card(card)
        print(
            json.dumps(
                {
                    k: report[k]
                    for k in (
                        "complete",
                        "issues",
                        "notes",
                        "codecs",
                        "population_differences",
                        "content_pc1_from",
                    )
                },
                indent=2,
                default=str,
            )
        )
        if not report["complete"]:
            for issue in report["issues"]:
                print("census_cache_card: %s" % issue, file=sys.stderr)
    if args.check_html:
        if args.card and card is None:
            card = _load_json(args.card)
        html = open(args.check_html, encoding="utf-8").read()
        html_issues = check_html(html, card)
        for issue in html_issues:
            print("census_cache_card: %s" % issue, file=sys.stderr)
        if not html_issues:
            print("html card copy ok", file=sys.stderr)
    if card is None and not args.check_html:
        p.print_help()
        return 2
    if (report and not report["complete"]) or html_issues:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
