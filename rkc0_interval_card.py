#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Kevin Russell (Aperture / Project Forty Two)
"""rkc0_interval_card — P94 interval, rerun-floor and selection checks for every RKC0 verdict.

An RKC0 verdict is incomplete until it carries this card. The card does not
run a model. It checks that a result JSON (or a published HTML card) names
its interval method, judges any change against a replicated no-change floor,
keeps measurement noise separate from run-to-run variance, and tests any
selection rule against shuffled labels and on held-out runs.

WHAT THIS CHECKS

  (a) INTERVAL METHOD. Every interval names its method. For bounded
      few-valued scores (pass/fail, rubric) the certifying interval is exact
      (Clopper-Pearson or Gaffke). Known-answer worlds include small-n and
      near-boundary cases, and report the realized coverage of the method
      used (Wald may be shown for contrast).

  (b) REPLICATED NO-CHANGE FLOOR. Any 'change is harmless / change helps'
      certification is judged against a no-change floor built from at least
      3 replicas (restart and reorder), with a mandatory unchanged world the
      rule must not certify as changed. One replica pair is never a floor.

  (c) NOISE SPLIT. Method-level verdicts separate measurement noise
      (sigma^2: repeated evaluation of one artifact) from run-to-run
      variance (tau^2: independent runs of the method) and need independent
      runs per method.

  (d) SELECTION / GATING. Any selection or gating rule is run in a
      label-shuffled zero-effect world and on held-out runs. Its in-sample
      gain under the shuffle is reported as its noise gain, and the share of
      cells that cannot show a decrease (an arithmetic floor) is counted
      before any harm count.

    python3 -B rkc0_interval_card.py CARD.json
    python3 -B rkc0_interval_card.py --from-pilot PILOT.json
    python3 -B rkc0_interval_card.py --check-html research.html --card CARD.json

Stdlib only. Exit 0 if the card is complete and any credit it claims is
justified. Exit 1 if required fields are missing or a claim is unjustified.
"""
from __future__ import annotations

import argparse
import json
import re
import sys

SCHEMA = "honestytools.rkc0_interval_card.v1"
MIN_REPLICAS = 3
EXACT_METHODS = {
    "gaffke",
    "gaffke_interval",
    "clopper-pearson",
    "clopper_pearson",
    "clopper pearson",
    "exact",
    "exact_binomial",
    "exact-binomial",
}
APPROX_METHODS = {
    "wald",
    "student",
    "student_t",
    "student-t",
    "bootstrap",
    "percentile_bootstrap",
    "normal",
}
BOUNDED_SCORES = {
    "pass_fail",
    "pass/fail",
    "binary",
    "bernoulli",
    "rubric",
    "few_valued",
    "few-valued",
    "bounded",
    "bounded_few_valued",
}
REQUIRED_HTML_PHRASES = (
    "controls card",
    "interval",
    "gaffke",
    "replica",
    "shuffl",
    "held-out",
)


class CardError(ValueError):
    pass


def _norm(value):
    return re.sub(r"[\s-]+", "_", str(value or "").strip().lower())


def _num(value, field):
    if value is None:
        raise CardError("missing number: %s" % field)
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise CardError("not a number: %s (%r)" % (field, value)) from exc


def _is_exact(method):
    return _norm(method) in {_norm(m) for m in EXACT_METHODS}


def _is_approx(method):
    return _norm(method) in {_norm(m) for m in APPROX_METHODS}


def _is_bounded(score_type):
    return _norm(score_type) in {_norm(s) for s in BOUNDED_SCORES}


def evaluate_interval(doc, issues):
    block = doc.get("interval") or doc.get("interval_method") or {}
    if not isinstance(block, dict):
        issues.append("interval block is missing")
        return None
    method = block.get("certifying_method") or block.get("method") or doc.get("interval_method")
    if isinstance(method, dict):
        method = method.get("name") or method.get("method")
    if not method:
        issues.append("interval method is not named")
        return None
    score_type = block.get("score_type") or doc.get("score_type") or "pass_fail"
    if _is_bounded(score_type) and not _is_exact(method):
        issues.append(
            "certifying interval for a bounded few-valued score is %r; P94 requires an exact method (Clopper-Pearson or Gaffke)"
            % method
        )
    worlds = block.get("coverage_worlds") or block.get("worlds") or doc.get("coverage_worlds") or {}
    if not worlds:
        issues.append("known-answer coverage worlds are missing")
    return {
        "method": str(method),
        "score_type": score_type,
        "exact": _is_exact(method),
        "n_worlds": len(worlds) if isinstance(worlds, dict) else (len(worlds) if worlds else 0),
    }


def evaluate_change_floor(doc, issues):
    block = doc.get("change_floor") or doc.get("rerun_floor") or doc.get("change_gate") or {}
    if not block:
        issues.append("replicated no-change floor is missing")
        return None
    n = block.get("n_replicas") or block.get("replicas") or block.get("n")
    try:
        n = int(n) if n is not None else None
    except (TypeError, ValueError):
        issues.append("n_replicas is not an integer")
        n = None
    if n is None:
        issues.append("change floor does not state the number of replicas")
    elif n < MIN_REPLICAS:
        issues.append("change floor uses n=%s replicas; P94 requires at least %d" % (n, MIN_REPLICAS))
    worlds = block.get("worlds") or {}
    unchanged = worlds.get("unchanged") if isinstance(worlds, dict) else block.get("unchanged")
    if not isinstance(unchanged, dict):
        issues.append("mandatory unchanged world is missing from the change floor")
        unchanged = {}
    if block.get("floor_is_single_pair") is True or block.get("n_replicas") == 1:
        issues.append("one replica pair is never a floor")
    # If the card certifies a change using only a single-pair rate, fail.
    certified_on = block.get("certified_against") or block.get("floor_used")
    if certified_on is not None and _norm(certified_on) in {"single_pair", "one_pair", "one_replica_pair", "n_1"}:
        issues.append("a change was certified against a single replica pair; P94 requires at least 3 replicas")
    return {
        "n_replicas": n,
        "unchanged": unchanged,
        "changed": (worlds.get("changed") if isinstance(worlds, dict) else block.get("changed")),
    }


def evaluate_noise_split(doc, issues):
    block = doc.get("noise_split") or doc.get("method_verdicts") or doc.get("variance_split")
    # Optional unless a method-level verdict is claimed.
    claims_method = bool(doc.get("method_level") or doc.get("method_comparison") or (isinstance(block, dict) and block.get("claimed")))
    if not block and not claims_method:
        return None
    if not isinstance(block, dict):
        issues.append("method-level verdict is missing a measurement-noise (sigma^2) vs run-to-run (tau^2) split")
        return None
    sigma = block.get("sigma2")
    if sigma is None:
        sigma = block.get("eval_noise") or block.get("measurement_noise")
    tau = block.get("tau2")
    if tau is None:
        tau = block.get("run_to_run") or block.get("run_to_run_variance")
    if sigma is None or tau is None:
        issues.append("method-level verdict must separate measurement noise (sigma^2) from run-to-run variance (tau^2)")
    runs = block.get("independent_runs_per_method") or block.get("n_runs")
    if runs is None or (isinstance(runs, (int, float)) and int(runs) < 2):
        issues.append("method-level verdict needs independent runs per method")
    return {"sigma2": sigma, "tau2": tau, "independent_runs_per_method": runs}


def evaluate_selection(doc, issues):
    block = doc.get("selection") or doc.get("gating") or doc.get("selection_rule")
    if not block:
        return None
    if not isinstance(block, dict):
        issues.append("selection rule block is not an object")
        return None
    shuffle = block.get("shuffle_null") or block.get("label_shuffle") or block.get("zero_effect")
    if not isinstance(shuffle, dict):
        issues.append("selection rule is missing a label-shuffled zero-effect world")
        shuffle = {}
    in_sample = shuffle.get("in_sample_gain_pp")
    if in_sample is None:
        in_sample = shuffle.get("in_sample_gain_pp_mean") or shuffle.get("noise_gain_pp")
    if in_sample is None:
        issues.append("selection rule does not report its in-sample noise gain under the shuffle")
    held = block.get("held_out") or shuffle.get("held_out")
    if not isinstance(held, dict) and block.get("held_out_gain_pp_mean") is None:
        # also accept flat keys on the block
        if block.get("held_out_gain_pp") is None and shuffle.get("held_out_gain_pp_mean") is None:
            issues.append("selection rule is missing a held-out check")
            held = {}
        else:
            held = {
                "gain_pp": block.get("held_out_gain_pp_mean") or block.get("held_out_gain_pp") or shuffle.get("held_out_gain_pp_mean")
            }
    elif isinstance(held, dict) and held.get("gain_pp") is None and held.get("gain_pp_mean") is None and block.get("held_out_gain_pp_mean") is None:
        if "held_out_gain_pp_mean" not in (block or {}) and "held_out_gain_pp_mean" not in (shuffle or {}):
            # held dict might already use the pilot key at the parent
            pass
    # Arithmetic floor before any harm count.
    cannot = (
        block.get("share_cannot_show_decrease")
        or shuffle.get("share_tasks_baseline_0_of_3_cannot_show_decrease")
        or block.get("share_tasks_baseline_0_of_3_cannot_show_decrease")
        or block.get("arithmetic_floor_share")
    )
    if block.get("harm_count") is not None and cannot is None:
        issues.append("a harm count is present, but the share of cells that cannot show a decrease (arithmetic floor) was not counted first")
    return {
        "in_sample_gain_pp": in_sample if in_sample is None else float(in_sample) if not isinstance(in_sample, (list, dict)) else in_sample,
        "held_out": held,
        "arithmetic_floor_share": cannot,
    }


def evaluate_card(doc):
    issues = []
    notes = []
    if not isinstance(doc, dict):
        raise CardError("card is not a JSON object")
    schema = doc.get("schema")
    if schema and schema != SCHEMA:
        issues.append("unexpected schema %r (expected %s)" % (schema, SCHEMA))

    interval = evaluate_interval(doc, issues)
    change = evaluate_change_floor(doc, issues)
    noise = evaluate_noise_split(doc, issues)
    if noise is None:
        notes.append("this card has no method-level verdict, so the sigma^2 / tau^2 split is not required here")
    selection = evaluate_selection(doc, issues)

    complete = not issues
    return {
        "complete": complete,
        "issues": issues,
        "notes": notes,
        "interval": interval,
        "change_floor": change,
        "noise_split": noise,
        "selection": selection,
    }


def cards_from_pilot(pilot):
    """Map the 2026-11-26 RKC0 interval-floor box-pilot JSON into a v1 card."""
    if not isinstance(pilot, dict) or "A_interval_coverage_nominal_0.95" not in pilot:
        raise CardError("not a tool-17 P94 pilot JSON")
    coverage = pilot["A_interval_coverage_nominal_0.95"]
    gate = pilot["B_change_gate_reject_rate"]
    select = pilot["C_selection_on_noise"]
    worlds = {}
    for name, methods in coverage.items():
        worlds[name] = {
            method: {"coverage": float(vals["coverage"]), "mean_width": float(vals["mean_width"])}
            for method, vals in methods.items()
        }
    # Headline cells used on the public card: 98% pass rate at n = 10, 20, 30.
    headline = {
        str(n): {
            method: worlds["binary_p0.98_n%d" % n][method]["coverage"]
            for method in ("wald", "student_t", "bootstrap", "gaffke")
        }
        for n in (10, 20, 30)
    }
    gaffke_coverages = [methods["gaffke"]["coverage"] for methods in coverage.values()]
    return {
        "schema": SCHEMA,
        "title": "RKC0 interval and rerun controls card",
        "proposal": "P94",
        "score_type": "pass_fail",
        "interval": {
            "certifying_method": "gaffke",
            "score_type": "pass_fail",
            "nominal_coverage": 0.95,
            "contrast_methods": ["wald", "student_t", "bootstrap"],
            "coverage_worlds": worlds,
            "headline_binary_p0.98": headline,
            "gaffke_coverage_range": [min(gaffke_coverages), max(gaffke_coverages)],
        },
        "change_floor": {
            "n_replicas": 3,
            "also_reported": [5],
            "floor_used": "floor3_p95_binomial",
            "construction": "restart and reorder",
            "worlds": {
                "unchanged": dict(gate["unchanged"]),
                "changed": dict(gate["changed_+1.5pct_items_flip"]),
            },
            "changed_world": "+1.5% item flips",
        },
        "selection": {
            "rule": "per-task gate",
            "tasks": select["tasks"],
            "runs_per_condition": select["runs_per_condition"],
            "true_effect_pp": select["true_effect_pp"],
            "shuffle_null": {
                "in_sample_gain_pp_mean": select["in_sample_gain_pp_mean"],
                "in_sample_gain_pp_p5_p95": list(select["in_sample_gain_pp_p5_p95"]),
                "held_out_gain_pp_mean": select["held_out_gain_pp_mean"],
                "held_out_gain_pp_p5_p95": list(select["held_out_gain_pp_p5_p95"]),
                "share_tasks_baseline_0_of_3_cannot_show_decrease": select[
                    "share_tasks_baseline_0_of_3_cannot_show_decrease"
                ],
            },
            "held_out": {"gain_pp_mean": select["held_out_gain_pp_mean"]},
            "arithmetic_floor_share": select["share_tasks_baseline_0_of_3_cannot_show_decrease"],
        },
        "method_level": False,
        "pilot": {
            "script": "tool17_p94_interval_floor_pilot_20261126.py",
            "runtime_s": pilot.get("seconds"),
            "notes": (
                "Small known-answer simulation, seeded, about 7 seconds. Our "
                "numbers. This is not a reproduction of Abraham (arXiv:2611.29512) "
                "or Rane (arXiv:2611.29663)."
            ),
        },
    }


def check_html(html, card=None):
    issues = []
    if 'id="rkc0-interval-controls"' not in html and "id='rkc0-interval-controls'" not in html:
        issues.append("HTML is missing id=\"rkc0-interval-controls\"")
    start = html.find('id="rkc0-interval-controls"')
    if start < 0:
        start = html.find("id='rkc0-interval-controls'")
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
            if phrase == "replica" and ("rerun" in text_l or "re-run" in text_l):
                continue
            if phrase == "held-out" and "held out" in text_l:
                continue
            if phrase == "gaffke" and "exact" in text_l and "clopper" in text_l:
                continue
            issues.append("HTML is missing the phrase %r" % phrase)
    banned = re.search(r"\b(solved|proved|caught)\b|the AI honesty layer", text, re.I)
    if banned:
        issues.append("HTML card uses the wording %r" % banned.group(0))
    if card:
        blob = text.replace("\u2212", "-")
        # Headline figures from the 98% pass-rate worlds and the change floor.
        if "18" not in blob and "0.18" not in blob:
            issues.append("HTML is missing the Wald coverage 18% at n=10")
        if "48.6" not in blob and "49%" not in blob and "half" not in text_l:
            issues.append("HTML is missing the single-pair unchanged reject rate 48.6%")
        if "9.46" not in blob:
            issues.append("HTML is missing the in-sample shuffle gain 9.46 pp")
        if "0.02" not in blob:
            issues.append("HTML is missing the held-out shuffle gain 0.02 pp")
        interval = (card.get("interval") or {})
        if interval.get("certifying_method") and str(interval["certifying_method"]).lower() not in text_l:
            if "gaffke" not in text_l:
                issues.append("HTML does not name the certifying interval method")
    return issues


def _load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _dump(doc):
    return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("path", nargs="?", help="a v1 controls card JSON, or a pilot JSON with --from-pilot")
    p.add_argument("--from-pilot", action="store_true", help="read a P94 box-pilot JSON and emit a v1 card")
    p.add_argument("-o", "--output", help="write the card JSON here (with --from-pilot)")
    p.add_argument("--check-html", metavar="HTML", help="check a published page for the required card copy")
    p.add_argument("--card", help="v1 card JSON to check the HTML against")
    args = p.parse_args(argv)

    if args.from_pilot:
        if not args.path:
            print("rkc0_interval_card: --from-pilot needs a pilot JSON path", file=sys.stderr)
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
                print("rkc0_interval_card: %s" % issue, file=sys.stderr)
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
                    for k in ("complete", "issues", "notes", "interval", "change_floor", "noise_split", "selection")
                },
                indent=2,
                default=str,
            )
        )
        if not report["complete"]:
            for issue in report["issues"]:
                print("rkc0_interval_card: %s" % issue, file=sys.stderr)
    if args.check_html:
        if args.card and card is None:
            card = _load_json(args.card)
        html = open(args.check_html, encoding="utf-8").read()
        html_issues = check_html(html, card)
        for issue in html_issues:
            print("rkc0_interval_card: %s" % issue, file=sys.stderr)
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
