#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Kevin Russell (Aperture / Project Forty Two)
"""empirical_lens_card — P91 familiar-to-what receipt and fact-vs-surface card.

A familiar empirical-lens result is incomplete until it carries this card. The
card does not run a model. It checks that a result JSON (or a published HTML
card) states the required receipt and controls, and that any rate it states is
consistent with the figures beside it.

WHAT THIS CHECKS

  (a) FAMILIAR-TO-WHAT. Every familiar verdict prints its nearest reference
      items (ids and cosines) and, where the atlas has source ids, whether the
      nearest item is the same source. Word it 'close in this embedding to
      <items>', never 'seen' or 'known'.

  (b) FACT-VS-SURFACE CONTROLS. Every frame publishes a control card from a
      paired benchmark (FOF-Bench style): the share of fact-altered near-copies
      read familiar, the share of faithful rewrites read uncharted, and the
      own-item retrieval rate, beside the in-set false-alarm rate.

  (c) LENGTH-MATCHED RE-SCORE. When query and atlas lengths differ, the verdict
      is reported again at matched length, and the card says whether it flips.

  (d) OPTIONAL. A human-anchored spot check of what 'similar' means for the
      frame may be present; it is not required for a complete card.

    python3 -B empirical_lens_card.py CARD.json
    python3 -B empirical_lens_card.py --from-pilot PILOT.json
    python3 -B empirical_lens_card.py --check-html research.html --card CARD.json

Stdlib only. Exit 0 if the card is complete and any figures it states are
consistent. Exit 1 if required fields are missing or a figure is inconsistent.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any

SCHEMA = "honestytools.empirical_lens_card.v1"
RATE_TOL = 0.0006
BANNED_WORDING = re.compile(r"\b(seen|known)\b", re.IGNORECASE)
CLOSE_PHRASE = "close in this embedding"
REQUIRED_HTML_PHRASES = (
    "controls card",
    "close in this embedding",
    "nearest",
    "uncharted",
    "faithful",
    "false-alarm",
    "length",
)


class CardError(ValueError):
    pass


def _get(d, *keys, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def _num(value, field):
    if value is None:
        raise CardError("missing number: %s" % field)
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise CardError("not a number: %s (%r)" % (field, value)) from exc


def _rate(block, field, issues, alt_fields=()):
    """Return a 0..1 rate from `field` or one of alt_fields, or None if absent."""
    if not isinstance(block, dict):
        return None
    raw = block.get(field)
    if raw is None:
        for alt in alt_fields:
            raw = block.get(alt)
            if raw is not None:
                break
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        issues.append("%s is not a number (%r)" % (field, raw))
        return None
    if value > 1.0 + 1e-12 and value <= 100.0:
        value = value / 100.0
    if value < -1e-12 or value > 1.0 + 1e-12:
        issues.append("%s=%s is not a rate in 0..1" % (field, raw))
        return None
    return value


def _consistent_pair(a, b, label, issues, target=1.0):
    if a is None or b is None:
        return
    if abs((a + b) - target) > RATE_TOL:
        issues.append("%s: familiar (%.4f) and uncharted (%.4f) do not add to 1" % (label, a, b))


def _count_consistent(rate, n, count, label, issues):
    if rate is None or n is None or count is None:
        return
    try:
        n = int(n)
        count = int(count)
    except (TypeError, ValueError):
        issues.append("%s: n or count is not an integer" % label)
        return
    if n <= 0:
        issues.append("%s: n must be positive" % label)
        return
    expected = count / float(n)
    if abs(expected - rate) > RATE_TOL:
        issues.append(
            "%s: stated rate %.4f does not match count %d / n %d (%.4f)"
            % (label, rate, count, n, expected)
        )


def fmt_pct(rate):
    """Half-up to one decimal percent, e.g. 0.0525 -> '5.3%'."""
    x = 100.0 * float(rate)
    tenths = int(x * 10 + (0.5 if x >= 0 else -0.5))
    whole, frac = divmod(abs(tenths), 10)
    sign = "-" if tenths < 0 else ""
    return "%s%d.%d%%" % (sign, whole, frac)


def _banned_in(text, field, issues):
    if not text:
        return
    match = BANNED_WORDING.search(str(text))
    if match:
        issues.append(
            "%s uses %r; word it 'close in this embedding to <items>', never 'seen' or 'known'"
            % (field, match.group(0))
        )


def evaluate_nearest(items, label, require_source, issues):
    if not isinstance(items, list) or not items:
        issues.append("%s: nearest reference items are missing" % label)
        return []
    reports = []
    for i, item in enumerate(items):
        tag = "%s nearest[%d]" % (label, i)
        if not isinstance(item, dict):
            issues.append("%s is not an object" % tag)
            continue
        ident = item.get("id")
        if ident is None or str(ident).strip() == "":
            issues.append("%s: id is missing" % tag)
        cosine = item.get("cosine")
        if cosine is None:
            cosine = item.get("cos")
        if cosine is None:
            issues.append("%s: cosine is missing" % tag)
            cosine_v = None
        else:
            try:
                cosine_v = float(cosine)
            except (TypeError, ValueError):
                issues.append("%s: cosine is not a number" % tag)
                cosine_v = None
        same = item.get("same_source")
        if require_source and same is None:
            issues.append("%s: same_source is missing (the atlas has source ids)" % tag)
        reports.append(
            {
                "id": None if ident is None else str(ident),
                "cosine": cosine_v,
                "same_source": same,
            }
        )
    return reports


def evaluate_verdict(doc, issues, atlas_has_source_ids=None):
    """Check one familiar (or uncharted) verdict for the P91 receipt."""
    if not isinstance(doc, dict):
        issues.append("verdict is not a JSON object")
        return None
    verdict = str(doc.get("verdict") or doc.get("label") or "").strip().lower()
    label = doc.get("id") or doc.get("query_id") or "verdict"
    wording = doc.get("wording") or doc.get("text") or doc.get("receipt") or ""
    _banned_in(wording, "%s wording" % label, issues)
    require_source = doc.get("atlas_has_source_ids")
    if require_source is None:
        require_source = atlas_has_source_ids
    require_source = bool(require_source)
    nearest = doc.get("nearest") or doc.get("nearest_items") or doc.get("matches")
    if verdict == "familiar":
        if CLOSE_PHRASE not in str(wording).lower():
            issues.append(
                "%s: familiar wording must say 'close in this embedding to <items>'" % label
            )
        reports = evaluate_nearest(nearest, str(label), require_source, issues)
        return {
            "id": label,
            "verdict": "familiar",
            "nearest": reports,
            "wording": wording,
        }
    # Uncharted (or other) verdicts do not need nearest items, but must still
    # avoid 'seen' / 'known' if they carry wording.
    if nearest:
        evaluate_nearest(nearest, str(label), require_source, issues)
    return {"id": label, "verdict": verdict or None, "wording": wording}


def _arm(block, name, issues, need_retrieval=True):
    if not isinstance(block, dict):
        issues.append("missing %s block" % name)
        return None
    uncharted = _rate(block, "uncharted", issues)
    familiar = _rate(block, "familiar", issues)
    if familiar is None and uncharted is not None:
        familiar = 1.0 - uncharted
    if uncharted is None and familiar is not None:
        uncharted = 1.0 - familiar
    if familiar is None and uncharted is None:
        issues.append("%s: familiar or uncharted rate is missing" % name)
    _consistent_pair(familiar, uncharted, name, issues)
    n = block.get("n")
    for key, rate in (("familiar_count", familiar), ("uncharted_count", uncharted)):
        if block.get(key) is not None:
            _count_consistent(rate, n, block.get(key), "%s %s" % (name, key), issues)
    retrieval = _rate(
        block,
        "own_item_retrieval",
        issues,
        alt_fields=("nn_is_own_anchor", "own_item_retrieval_rate"),
    )
    if need_retrieval and retrieval is None:
        issues.append("%s: own-item retrieval rate is missing" % name)
    return {
        "familiar": familiar,
        "uncharted": uncharted,
        "own_item_retrieval": retrieval,
        "median_top1": block.get("median_top1"),
        "n": n,
    }


def evaluate_frame(frame, default_n, issues, notes):
    name = str(frame.get("name") or frame.get("frame") or "frame")
    label = name
    fact = _arm(
        frame.get("fact_altered_near_copies") or frame.get("neg") or frame.get("fact_altered"),
        "%s fact-altered near-copies" % label,
        issues,
        need_retrieval=True,
    )
    rewrite = _arm(
        frame.get("faithful_rewrites") or frame.get("pos") or frame.get("faithful"),
        "%s faithful rewrites" % label,
        issues,
        need_retrieval=True,
    )
    matched_block = (
        frame.get("faithful_rewrites_length_matched")
        or frame.get("pos_trunc")
        or frame.get("length_matched")
    )
    lengths_differ = frame.get("lengths_differ")
    if lengths_differ is None:
        # Infer from median word counts when present.
        med_q = frame.get("median_query_words")
        med_a = frame.get("median_atlas_words")
        if med_q is not None and med_a is not None:
            try:
                lengths_differ = abs(float(med_q) - float(med_a)) >= 1.0 and (
                    min(float(med_q), float(med_a)) <= 0
                    or max(float(med_q), float(med_a)) / max(min(float(med_q), float(med_a)), 1e-9)
                    >= 1.25
                )
            except (TypeError, ValueError):
                lengths_differ = None
    matched = None
    flip = frame.get("length_matched_flips")
    if flip is None:
        flip = frame.get("flips")
    if lengths_differ:
        if not isinstance(matched_block, dict):
            issues.append(
                "%s: query and atlas lengths differ, but a length-matched re-score is missing"
                % label
            )
        else:
            matched = _arm(matched_block, "%s length-matched faithful rewrites" % label, issues, need_retrieval=False)
            if flip is None:
                issues.append(
                    "%s: length-matched re-score is present, but the card does not say whether it flips"
                    % label
                )
            elif matched and rewrite and matched["uncharted"] is not None and rewrite["uncharted"] is not None:
                shares_differ = abs(matched["uncharted"] - rewrite["uncharted"]) > 0.01
                if (not bool(flip)) and shares_differ:
                    issues.append(
                        "%s: length-matched uncharted share changed (%.3f vs %.3f) but flips is false"
                        % (label, rewrite["uncharted"], matched["uncharted"])
                    )
                if bool(flip) and not shares_differ:
                    issues.append(
                        "%s: flips is true, but length-matched and original uncharted shares agree"
                        % label
                    )
    elif isinstance(matched_block, dict):
        matched = _arm(matched_block, "%s length-matched faithful rewrites" % label, issues, need_retrieval=False)

    fa = _rate(
        frame,
        "in_set_false_alarm",
        issues,
        alt_fields=("in_set_false_alarm_rate", "false_alarm_in_set"),
    )
    if fa is None and isinstance(frame.get("in_set_false_alarm"), dict):
        fa = _rate(frame["in_set_false_alarm"], "rate", issues, alt_fields=("uncharted",))
    baseline = frame.get("baseline") or {}
    if fa is None and isinstance(baseline, dict):
        fa = _rate(baseline, "alpha", issues, alt_fields=("in_set_false_alarm",))
    if fa is None:
        issues.append("%s: in-set false-alarm rate is missing" % label)

    held = frame.get("held_out_anchors") or frame.get("hold") or {}
    held_uncharted = None
    if isinstance(held, dict) and held:
        held_uncharted = _rate(held, "uncharted", issues)

    return {
        "name": name,
        "fact_altered_familiar": None if not fact else fact["familiar"],
        "fact_altered_uncharted": None if not fact else fact["uncharted"],
        "fact_altered_retrieval": None if not fact else fact["own_item_retrieval"],
        "faithful_uncharted": None if not rewrite else rewrite["uncharted"],
        "faithful_retrieval": None if not rewrite else rewrite["own_item_retrieval"],
        "length_matched_uncharted": None if not matched else matched["uncharted"],
        "length_matched_flips": None if flip is None else bool(flip),
        "in_set_false_alarm": fa,
        "held_out_uncharted": held_uncharted,
        "lengths_differ": bool(lengths_differ),
    }


def evaluate_card(doc):
    issues = []
    notes = []
    if not isinstance(doc, dict):
        raise CardError("card is not a JSON object")
    schema = doc.get("schema")
    if schema and schema != SCHEMA:
        issues.append("unexpected schema %r (expected %s)" % (schema, SCHEMA))

    atlas_has_source_ids = bool(doc.get("atlas_has_source_ids"))
    frames = doc.get("frames") or doc.get("settings")
    if not frames:
        issues.append("frames list is missing")
        frame_reports = []
    else:
        default_n = doc.get("n_atlas")
        frame_reports = [evaluate_frame(f, default_n, issues, notes) for f in frames]

    verdicts = doc.get("familiar_verdicts") or doc.get("results") or []
    verdict_reports = []
    if isinstance(verdicts, list):
        for item in verdicts:
            verdict_reports.append(evaluate_verdict(item, issues, atlas_has_source_ids))
    elif verdicts:
        issues.append("familiar_verdicts must be a list")

    # Optional human-anchored spot check: if present, it must name a frame.
    spot = doc.get("human_spot_check") or doc.get("spot_check")
    if spot is not None:
        if not isinstance(spot, dict) or not (spot.get("frame") or spot.get("name")):
            issues.append("human-anchored spot check is present but does not name a frame")
        else:
            notes.append("optional human-anchored spot check is present")

    _banned_in(doc.get("wording") or "", "card wording", issues)
    _banned_in(doc.get("title") or "", "card title", issues)

    complete = not issues
    return {
        "complete": complete,
        "issues": issues,
        "notes": notes,
        "frames": frame_reports,
        "verdicts": [v for v in verdict_reports if v],
        "atlas_has_source_ids": atlas_has_source_ids,
    }


def cards_from_pilot(pilot):
    """Map the 2026-11-26 FOF-Bench box-pilot JSON into a v1 controls card."""
    if not isinstance(pilot, dict) or "frames" not in pilot or "n_atlas" not in pilot:
        raise CardError("not a tool-07 P91 pilot JSON")
    frames = []
    med = pilot.get("median_words") or {}
    atlas_words = med.get("atlas")
    pos_words = med.get("pos")
    lengths_differ = False
    if atlas_words is not None and pos_words is not None:
        lengths_differ = abs(float(pos_words) - float(atlas_words)) >= 1.0 and (
            max(float(pos_words), float(atlas_words)) / max(min(float(pos_words), float(atlas_words)), 1e-9)
            >= 1.25
        )
    for name, block in (pilot.get("frames") or {}).items():
        neg = block["neg"]
        pos = block["pos"]
        trunc = block["pos_trunc"]
        hold = block["hold"]
        flip = abs(float(pos["uncharted"]) - float(trunc["uncharted"])) > 0.01
        frames.append(
            {
                "name": name,
                "threshold_cos": block["thr_cos"],
                "median_atlas_words": atlas_words,
                "median_query_words": pos_words,
                "lengths_differ": lengths_differ,
                "in_set_false_alarm": 0.05,
                "baseline": {
                    "method": "reference-to-reference leave-one-out",
                    "alpha": 0.05,
                },
                "fact_altered_near_copies": {
                    "n": pilot["n_atlas"],
                    "uncharted": neg["uncharted"],
                    "familiar": 1.0 - float(neg["uncharted"]),
                    "own_item_retrieval": neg["nn_is_own_anchor"],
                    "median_top1": neg["median_top1"],
                },
                "faithful_rewrites": {
                    "n": pilot["n_atlas"],
                    "uncharted": pos["uncharted"],
                    "familiar": 1.0 - float(pos["uncharted"]),
                    "own_item_retrieval": pos["nn_is_own_anchor"],
                    "median_top1": pos["median_top1"],
                },
                "faithful_rewrites_length_matched": {
                    "n": pilot["n_atlas"],
                    "uncharted": trunc["uncharted"],
                    "familiar": 1.0 - float(trunc["uncharted"]),
                    "own_item_retrieval": trunc["nn_is_own_anchor"],
                    "median_top1": trunc["median_top1"],
                },
                "length_matched_flips": flip,
                "held_out_anchors": {
                    "n": pilot["n_hold"],
                    "uncharted": hold["uncharted"],
                    "median_top1": hold["median_top1"],
                },
            }
        )
    # Example receipt, so the checker can plant a defect against a complete one.
    # Synthetic ids; the pilot stored rates, not per-item neighbours.
    example = [
        {
            "id": "example-neg-000",
            "verdict": "familiar",
            "frame": "MiniLM-L6 mean raw",
            "wording": "close in this embedding to atlas-000 (cosine 0.998)",
            "atlas_has_source_ids": True,
            "nearest": [
                {"id": "atlas-000", "cosine": 0.9983595609664917, "same_source": True}
            ],
        }
    ]
    return {
        "schema": SCHEMA,
        "title": "Empirical-lens controls card",
        "proposal": "P91",
        "benchmark": "FOF-Bench test split (MaxDevv/FOF-Bench, Adeola and Maximus, arXiv:2611.33250, CC-BY-NC-SA-4.0)",
        "n_atlas": pilot["n_atlas"],
        "n_hold": pilot["n_hold"],
        "n_ok": pilot.get("n_ok"),
        "bad_rows": pilot.get("bad_rows"),
        "maxtok": pilot.get("maxtok"),
        "median_words": med,
        "baseline": {
            "method": "reference-to-reference leave-one-out",
            "alpha": 0.05,
        },
        "atlas_has_source_ids": True,
        "wording_rule": "close in this embedding to <items>; never 'seen' or 'known'",
        "frames": frames,
        "familiar_verdicts": example,
        "pilot": {
            "script": "tool07_p91_fact_vs_surface_pilot_20261126.py",
            "notes": (
                "Small box pilot on the FOF-Bench test set: 1,200-anchor atlas, "
                "400 held-out anchors, MiniLM mean and SmolLM2-135M layer-20 mean "
                "(raw and centred). Our numbers. This is not a reproduction of "
                "Adeola and Maximus (arXiv:2611.33250)."
            ),
        },
    }


def check_html(html, card=None):
    issues = []
    if 'id="empirical-lens-controls"' not in html and "id='empirical-lens-controls'" not in html:
        issues.append("HTML is missing id=\"empirical-lens-controls\"")
    start = html.find('id="empirical-lens-controls"')
    if start < 0:
        start = html.find("id='empirical-lens-controls'")
    snippet = html
    if start >= 0:
        # Use the section that contains the id, falling back to the rest of the file.
        sec = html.rfind("<section", 0, start)
        end = html.find("</section>", start)
        if sec >= 0 and end > sec:
            snippet = html[sec : end + len("</section>")]
    text = re.sub(r"<[^>]+>", " ", snippet)
    text_l = text.lower()
    for phrase in REQUIRED_HTML_PHRASES:
        if phrase not in text_l:
            # false-alarm may be written "false alarm"
            if phrase == "false-alarm" and "false alarm" in text_l:
                continue
            issues.append("HTML is missing the phrase %r" % phrase)
    if "fact-altered" not in text_l and "changed fact" not in text_l and "one fact changed" not in text_l:
        issues.append("HTML does not mention fact-altered near-copies / a changed fact")
    if "retrieval" not in text_l and "own-item" not in text_l and "own item" not in text_l:
        issues.append("HTML does not mention own-item retrieval")
    banned = BANNED_WORDING.search(text)
    if banned:
        issues.append(
            "HTML card uses %r; word it 'close in this embedding to <items>', never 'seen' or 'known'"
            % banned.group(0)
        )
    if card:
        # Headline figure from the proposal / pilot: 99.6%+ familiar on fact-altered copies.
        blob = text.replace("\u2212", "-")
        if "99.6" not in blob and "99.7" not in blob and "100.0" not in blob and "100%" not in blob:
            issues.append("HTML does not state the fact-altered familiar share (99.6% or above)")
        for frame in card.get("frames") or []:
            name = frame.get("name") or ""
            rewrite = frame.get("faithful_rewrites") or {}
            uncharted = rewrite.get("uncharted")
            if uncharted is not None and uncharted >= 0.05:
                token = fmt_pct(uncharted)
                short = token.replace("%", "")
                if short not in blob and token not in blob:
                    issues.append(
                        "HTML is missing the faithful-rewrite uncharted share %s for %s"
                        % (token, name)
                    )
            matched = frame.get("faithful_rewrites_length_matched") or {}
            if frame.get("length_matched_flips") and matched.get("uncharted") is not None:
                token = fmt_pct(matched["uncharted"])
                short = token.replace("%", "")
                if short not in blob and token not in blob:
                    issues.append(
                        "HTML is missing the length-matched uncharted share %s for %s"
                        % (token, name)
                    )
            retrieval = (frame.get("faithful_rewrites") or {}).get("own_item_retrieval")
            if retrieval is not None and retrieval <= 0.2:
                token = fmt_pct(retrieval)
                short = token.replace("%", "")
                if short not in blob and token not in blob:
                    issues.append(
                        "HTML is missing the own-item retrieval rate %s for %s" % (token, name)
                    )
    return issues


def _load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _dump(doc):
    return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("path", nargs="?", help="a v1 controls card JSON, or a pilot JSON with --from-pilot")
    p.add_argument("--from-pilot", action="store_true", help="read a P91 box-pilot JSON and emit a v1 card")
    p.add_argument("-o", "--output", help="write the card JSON here (with --from-pilot)")
    p.add_argument("--check-html", metavar="HTML", help="check a published page for the required card copy")
    p.add_argument("--card", help="v1 card JSON to check the HTML against")
    args = p.parse_args(argv)

    if args.from_pilot:
        if not args.path:
            print("empirical_lens_card: --from-pilot needs a pilot JSON path", file=sys.stderr)
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
                print("empirical_lens_card: %s" % issue, file=sys.stderr)
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
                    for k in ("complete", "issues", "notes", "frames", "verdicts", "atlas_has_source_ids")
                },
                indent=2,
            )
        )
        if not report["complete"]:
            for issue in report["issues"]:
                print("empirical_lens_card: %s" % issue, file=sys.stderr)
    if args.check_html:
        if args.card and card is None:
            card = _load_json(args.card)
        html = open(args.check_html, encoding="utf-8").read()
        html_issues = check_html(html, card)
        for issue in html_issues:
            print("empirical_lens_card: %s" % issue, file=sys.stderr)
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
