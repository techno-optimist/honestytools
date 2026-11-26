#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Kevin Russell (Aperture / Project Forty Two)
"""rotated_lens_card — P90 controls card for every rotated-lens result.

A rotated-lens result is incomplete until it carries this card. The card does not
run a model. It checks that a result JSON (or a published HTML card) states the
required controls and that any credit it claims is justified by those numbers.

WHAT THIS CHECKS

  (a) CONTROLS. The fitted rotation is reported beside at least 50 Haar-random
      orthogonal maps and 50 shuffled-pair Procrustes fits (p95 of each, and the
      percentile of the fitted score among them), a no-rotation / identity map,
      and, within one model, the source-probe reading on the target layer
      (ambient identity) when the width is full. Credit the rotation for a
      quantity only if the fitted score is strictly above the larger p95 and
      only with a gain over the identity map used for that quantity.

  (b) LAYER SELECTION. Layers must be chosen on a held-out validation split by
      the functional quantity claimed (transfer AUROC, accuracy, or swap
      success), never by a geometric similarity score (CKA, mKNN). The
      similarity score is reported beside it. Flag CEILING when validation
      saturates, because selection is then uninformative.

  (c) SCALE AND INTERCEPT. The global scale s and the carried intercept must be
      stated. Credit a scale fix for decision accuracy only if a change is
      shown; an intercept near zero cannot move a carried decision.

  (d) CROSS-MODEL. Cross-model claims must include a chain check (A->B->C vs
      A->C).

The ranking/threshold split (P64) is kept: AUROC and carried-threshold accuracy
are credited separately. A 0.001 AUROC difference is not treated as a ranking
gain (see MIN_RANKING_GAIN).

    python3 -B rotated_lens_card.py CARD.json
    python3 -B rotated_lens_card.py --from-pilot PILOT.json
    python3 -B rotated_lens_card.py --check-html research.html --card CARD.json

Stdlib only. Exit 0 if the card is complete and any credit it claims is
justified. Exit 1 if required fields are missing or a credit is unjustified.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from typing import Any

SCHEMA = "honestytools.rotated_lens_card.v1"
MIN_CONTROLS = 50
MIN_RANKING_GAIN = 0.005
MIN_ACCURACY_GAIN = 0.01
INTERCEPT_NEAR_ZERO = 0.01
CEILING_AUROC = 0.999
TRANSFER_METHODS = {
    "heldout_transfer",
    "heldout_transfer_auroc",
    "heldout_transfer_accuracy",
    "heldout_swap_success",
    "held-out transfer",
    "held-out transfer auroc",
    "held-out transfer accuracy",
    "held-out swap success",
}
SIMILARITY_METHODS = {
    "cka",
    "linear_cka",
    "linear-cka",
    "mknn",
    "mknn10",
    "mutual_nearest_neighbours",
    "mutual nearest neighbours",
    "similarity",
    "geometric_similarity",
}


class CardError(ValueError):
    pass


def percentile_linear(values, q):
    """Numpy default method='linear' percentile; q is 0..100."""
    xs = sorted(float(x) for x in values)
    n = len(xs)
    if n == 0:
        raise CardError("percentile of an empty list")
    if n == 1:
        return xs[0]
    pos = (float(q) / 100.0) * (n - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return xs[lo]
    w = pos - lo
    return xs[lo] * (1.0 - w) + xs[hi] * w


def percentile_rank(score, values):
    """Percent of control scores strictly below `score`, 0..100."""
    xs = [float(x) for x in values]
    if not xs:
        raise CardError("percentile rank of an empty list")
    below = sum(1 for x in xs if x < float(score))
    return 100.0 * below / len(xs)


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


def _int(value, field):
    if value is None:
        raise CardError("missing integer: %s" % field)
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise CardError("not an integer: %s (%r)" % (field, value)) from exc


def _norm_method(value):
    return re.sub(r"[\s-]+", "_", str(value or "").strip().lower())


def _control_block(block, name, fitted_auroc, issues, notes):
    if not isinstance(block, dict):
        issues.append("missing %s control block" % name)
        return None
    n = block.get("n")
    if n is None:
        scores = block.get("scores") or block.get("auroc_scores")
        n = len(scores) if scores else None
    try:
        n = int(n)
    except (TypeError, ValueError):
        issues.append("%s: n is not an integer" % name)
        return None
    if n < MIN_CONTROLS:
        issues.append("%s: n=%s, need at least %d" % (name, n, MIN_CONTROLS))
    p95 = block.get("auroc_p95")
    percentile = block.get("fitted_percentile")
    scores = block.get("scores") or block.get("auroc_scores")
    if scores is not None:
        if len(scores) != n:
            issues.append("%s: %d scores but n=%d" % (name, len(scores), n))
        computed_p95 = percentile_linear(scores, 95)
        computed_pct = percentile_rank(fitted_auroc, scores)
        if p95 is None:
            p95 = computed_p95
        elif abs(float(p95) - computed_p95) > 0.006:
            issues.append(
                "%s: stated p95 %s does not match scores (%.3f)"
                % (name, p95, computed_p95)
            )
        if percentile is None:
            percentile = computed_pct
        elif abs(float(percentile) - computed_pct) > 0.6:
            issues.append(
                "%s: stated percentile %s does not match scores (%.1f)"
                % (name, percentile, computed_pct)
            )
    if p95 is None:
        issues.append("%s: auroc_p95 is missing" % name)
        return None
    if percentile is None:
        issues.append("%s: fitted_percentile is missing (and no scores to recompute it)" % name)
        return None
    return {
        "n": n,
        "auroc_median": block.get("auroc_median"),
        "auroc_p95": float(p95),
        "fitted_percentile": float(percentile),
    }


def _identity_map(setting):
    """Identity used for credit: ambient (source probe on the target layer) when
    present, otherwise no-rotation in the same principal bases."""
    ambient = setting.get("ambient_identity")
    principal = setting.get("no_rotation_principal") or setting.get("no_rotation")
    if isinstance(ambient, dict) and ambient.get("auroc") is not None:
        return "ambient_identity", ambient
    if isinstance(principal, dict) and principal.get("auroc") is not None:
        return "no_rotation_principal", principal
    return None, None


def evaluate_setting(setting, intercept, issues, notes):
    label = "k=%s" % setting.get("k", "?")
    fitted = setting.get("fitted") or {}
    if fitted.get("auroc") is None:
        issues.append("%s: fitted AUROC is missing" % label)
        return None
    fitted_auroc = _num(fitted.get("auroc"), "%s fitted.auroc" % label)
    fitted_acc = fitted.get("accuracy_carried")
    if fitted_acc is not None:
        fitted_acc = _num(fitted_acc, "%s fitted.accuracy_carried" % label)

    if setting.get("scale_s") is None:
        issues.append("%s: scale s is missing" % label)
        scale_s = None
    else:
        scale_s = _num(setting.get("scale_s"), "%s scale_s" % label)

    random = _control_block(
        setting.get("random_rotations") or setting.get("random"),
        "%s random rotations" % label,
        fitted_auroc,
        issues,
        notes,
    )
    shuffled = _control_block(
        setting.get("shuffled_pair_rotations") or setting.get("shuffled_pair"),
        "%s shuffled-pair rotations" % label,
        fitted_auroc,
        issues,
        notes,
    )
    ident_name, ident = _identity_map(setting)
    if ident is None:
        issues.append("%s: no-rotation / identity map is missing" % label)

    larger_p95 = None
    above_controls = False
    if random and shuffled:
        larger_p95 = max(random["auroc_p95"], shuffled["auroc_p95"])
        above_controls = fitted_auroc > larger_p95
    elif random or shuffled:
        issues.append("%s: both random and shuffled-pair controls are required" % label)

    ranking_gain = None
    accuracy_gain = None
    credit_ranking = False
    credit_accuracy = False
    if ident is not None:
        ident_auroc = _num(ident.get("auroc"), "%s %s.auroc" % (label, ident_name))
        ranking_gain = fitted_auroc - ident_auroc
        if above_controls and ranking_gain >= MIN_RANKING_GAIN:
            credit_ranking = True
        elif above_controls and ranking_gain is not None and ranking_gain <= 0.001 + 1e-12:
            notes.append(
                "%s: fitted AUROC is above the control p95 but has no ranking gain "
                "over %s (%.3f vs %.3f)"
                % (label, ident_name, fitted_auroc, ident_auroc)
            )
        if fitted_acc is not None and ident.get("accuracy_carried") is not None:
            ident_acc = _num(ident.get("accuracy_carried"), "%s %s.accuracy_carried" % (label, ident_name))
            accuracy_gain = fitted_acc - ident_acc
            if above_controls and accuracy_gain >= MIN_ACCURACY_GAIN:
                credit_accuracy = True

    # Claimed credits in the JSON, if any, must be justified.
    claimed = setting.get("credit") or {}
    if claimed.get("ranking") and not credit_ranking:
        issues.append("%s: card credits ranking, but the numbers do not justify it" % label)
    if claimed.get("accuracy") and not credit_accuracy:
        issues.append("%s: card credits carried accuracy, but the numbers do not justify it" % label)
    if claimed.get("scale"):
        scale_ok, scale_reason = _scale_credit_ok(setting, intercept)
        if not scale_ok:
            issues.append("%s: card credits a scale fix, but %s" % (label, scale_reason))

    scale_changes = setting.get("scale_changes_decisions")
    if scale_changes is True:
        scale_ok, scale_reason = _scale_credit_ok(setting, intercept)
        if not scale_ok:
            issues.append("%s: scale_changes_decisions is true, but %s" % (label, scale_reason))

    return {
        "k": setting.get("k"),
        "fitted_auroc": fitted_auroc,
        "fitted_accuracy_carried": fitted_acc,
        "scale_s": scale_s,
        "identity_used": ident_name,
        "larger_p95": larger_p95,
        "random_percentile": None if not random else random["fitted_percentile"],
        "shuffled_percentile": None if not shuffled else shuffled["fitted_percentile"],
        "above_controls": above_controls,
        "ranking_gain": ranking_gain,
        "accuracy_gain": accuracy_gain,
        "credit_ranking": credit_ranking,
        "credit_accuracy": credit_accuracy,
    }


def _scale_credit_ok(setting, intercept):
    if intercept is None:
        return False, "the intercept is not stated"
    if abs(float(intercept)) <= INTERCEPT_NEAR_ZERO:
        return False, "the intercept is near zero (%.4f), so a global scale cannot move a carried decision" % float(intercept)
    rot = (setting.get("fitted_rotation_only") or setting.get("rotation_only") or {})
    scaled = (setting.get("fitted_rotation_plus_scale") or setting.get("rotation_plus_scale") or {})
    if rot.get("accuracy_carried") is not None and scaled.get("accuracy_carried") is not None:
        if abs(float(scaled["accuracy_carried"]) - float(rot["accuracy_carried"])) < 1e-9:
            return False, "rotation-only and rotation-plus-scale accuracy are the same"
        return True, "accuracy changed"
    if setting.get("scale_changes_decisions") is True and setting.get("credit", {}).get("scale"):
        # already handled; without a shown pair we cannot credit
        return False, "no rotation-only vs rotation-plus-scale accuracy is shown"
    return False, "no rotation-only vs rotation-plus-scale accuracy is shown"


def evaluate_card(doc):
    issues = []
    notes = []
    if not isinstance(doc, dict):
        raise CardError("card is not a JSON object")
    schema = doc.get("schema")
    if schema and schema != SCHEMA:
        issues.append("unexpected schema %r (expected %s)" % (schema, SCHEMA))

    intercept = doc.get("intercept")
    if intercept is None:
        issues.append("carried intercept is missing")
    else:
        intercept = _num(intercept, "intercept")

    selection = doc.get("layer_selection") or {}
    method_raw = selection.get("method")
    if not method_raw:
        issues.append("layer_selection.method is missing")
        method_ok = False
        method_norm = ""
    else:
        method_norm = _norm_method(method_raw)
        if method_norm in {_norm_method(m) for m in SIMILARITY_METHODS}:
            issues.append("layers were chosen by a geometric similarity score (%s); P90 requires held-out transfer" % method_raw)
            method_ok = False
        elif method_norm in {_norm_method(m) for m in TRANSFER_METHODS}:
            method_ok = True
        else:
            issues.append("layer_selection.method %r is not a held-out transfer method" % method_raw)
            method_ok = False

    ceiling = bool(selection.get("validation_ceiling"))
    if ceiling:
        notes.append("CEILING: validation transfer saturated, so layer selection is uninformative")

    settings = doc.get("settings")
    if not settings:
        issues.append("settings list is missing")
        setting_reports = []
    else:
        setting_reports = [evaluate_setting(s, intercept, issues, notes) for s in settings]
        setting_reports = [s for s in setting_reports if s]

    cross_model = bool(doc.get("cross_model"))
    if not cross_model:
        models = {doc.get("source_model"), doc.get("target_model")} - {None}
        if len(models) > 1:
            cross_model = True
    chain = doc.get("chain_check")
    if cross_model:
        if not isinstance(chain, dict) or chain.get("a_b_c") is None or chain.get("a_c") is None:
            issues.append("cross-model claim is missing a chain check (A->B->C vs A->C)")
            chain_ok = False
        else:
            chain_ok = True
    else:
        chain_ok = True

    complete = not issues
    return {
        "complete": complete,
        "issues": issues,
        "notes": notes,
        "intercept": intercept,
        "layer_method_ok": method_ok,
        "validation_ceiling": ceiling,
        "cross_model": cross_model,
        "chain_ok": chain_ok,
        "settings": setting_reports,
    }


def cards_from_pilot(pilot):
    """Map the 2026-11-26 SmolLM2 box-pilot JSON into a v1 controls card."""
    if not isinstance(pilot, dict) or "B20_k64" not in pilot:
        raise CardError("not a tool-05 P90 pilot JSON")
    intercept = pilot["carried_intercept_bA"]
    sweep = {int(k): v for k, v in (pilot.get("sweep_k576_rotation_plus_scale") or {}).items()}
    ceiling_layers = [b for b, v in sweep.items() if v.get("val_auc", 0) >= CEILING_AUROC]
    if not ceiling_layers and _get(pilot, "layer_selection_ceiling", "validation_auc_ceiling_layers"):
        ceiling_layers = list(pilot["layer_selection_ceiling"]["validation_auc_ceiling_layers"])
    selected = _get(pilot, "selected_layers", "by_val_transfer_auc", "layer")
    settings = []
    ambient = pilot.get("B20_identity_map")
    for k in (64, 576):
        block = pilot["B20_k%d" % k]
        rot = block["rotation_only"]
        scaled = block["rotation_plus_scale"]
        setting = {
            "k": k,
            "fitted": {"auroc": rot["auc"], "accuracy_carried": rot["acc_carried"]},
            "rotation_only": {"auroc": rot["auc"], "accuracy_carried": rot["acc_carried"]},
            "rotation_plus_scale": {"auroc": scaled["auc"], "accuracy_carried": scaled["acc_carried"]},
            "scale_s": block["global_scale_s"],
            "scale_changes_decisions": rot["acc_carried"] != scaled["acc_carried"],
            "no_rotation_principal": {
                "auroc": block["no_rotation"]["auc"],
                "accuracy_carried": block["no_rotation"]["acc_carried"],
            },
            "random_rotations": {
                "n": int(pilot.get("n_controls") or len(block["random_rotation_aucs"])),
                "auroc_median": block["random_rotation_auc_median_p95"][0],
                "auroc_p95": block["random_rotation_auc_median_p95"][1],
                "fitted_percentile": block["random_rotation_fitted_percentile"],
                "scores": block["random_rotation_aucs"],
            },
            "shuffled_pair_rotations": {
                "n": int(pilot.get("n_controls") or len(block["shuffled_pair_rotation_aucs"])),
                "auroc_median": block["shuffled_pair_rotation_auc_median_p95"][0],
                "auroc_p95": block["shuffled_pair_rotation_auc_median_p95"][1],
                "fitted_percentile": block["shuffled_pair_rotation_fitted_percentile"],
                "scores": block["shuffled_pair_rotation_aucs"],
            },
        }
        if k == 576 and ambient:
            setting["ambient_identity"] = {
                "auroc": ambient["auc"],
                "accuracy_carried": ambient["acc_carried"],
            }
        settings.append(setting)
    cka_range = [sweep[b]["linear_cka"] for b in range(11, 21) if b in sweep]
    transfer_range = [sweep[b]["test_auc"] for b in range(11, 21) if b in sweep]
    return {
        "schema": SCHEMA,
        "title": "Rotated-lens controls card",
        "proposal": "P90",
        "model": pilot["model"],
        "within_model": True,
        "source_layer": pilot["source_layer"],
        "target_layer": 20,
        "n_neutral": pilot.get("n_neutral"),
        "n_train": pilot.get("n_train"),
        "n_val": pilot.get("n_val"),
        "n_test": pilot["n_test"],
        "n_controls": int(pilot.get("n_controls") or 50),
        "source_probe_test": {
            "auroc": pilot["source_probe_test"]["auc"],
            "accuracy": pilot["source_probe_test"]["acc"],
        },
        "intercept": intercept,
        "layer_selection": {
            "method": "heldout_transfer_auroc",
            "selected_layer": selected,
            "validation_ceiling": bool(ceiling_layers),
            "validation_ceiling_layers": ceiling_layers,
            "also_selected_by": {
                "mknn": _get(pilot, "selected_layers", "by_mknn", "layer"),
                "linear_cka": _get(pilot, "selected_layers", "by_linear_cka", "layer"),
            },
            "linear_cka_layers_11_20": cka_range,
            "transported_auroc_layers_11_20": transfer_range,
        },
        "settings": settings,
        "cross_model": False,
        "chain_check": None,
        "pilot": {
            "script": "tool05_p90_scale_controls_layer_pilot_20261126.py",
            "runtime_s": pilot.get("runtime_s"),
            "notes": (
                "Tiny single-model box pilot on SmolLM2-135M-Instruct. Our numbers. "
                "Not a reproduction of Plato's Key."
            ),
        },
    }


REQUIRED_HTML_PHRASES = (
    "controls card",
    "random",
    "shuffled",
    "percentile",
    "held-out",
    "not by",  # "not by CKA" / "not by a geometric similarity score"
    "scale",
    "intercept",
)


def check_html(html, card=None):
    issues = []
    text = re.sub(r"<[^>]+>", " ", html)
    text_l = text.lower()
    if 'id="rotated-lens-controls"' not in html and "id='rotated-lens-controls'" not in html:
        issues.append("HTML is missing id=\"rotated-lens-controls\"")
    for phrase in REQUIRED_HTML_PHRASES:
        if phrase not in text_l:
            issues.append("HTML is missing the phrase %r" % phrase)
    if "cka" not in text_l:
        issues.append("HTML does not mention CKA (required: layers were not chosen by CKA)")
    if card:
        if card.get("intercept") is not None and ("0.003" not in text and "-0.0028" not in text and "\u22120.003" not in text and "-0.003" not in text):
            # allow a rounded intercept
            issues.append("HTML does not state the carried intercept")
        for setting in card.get("settings") or []:
            k = setting.get("k")
            fitted = (setting.get("fitted") or {}).get("auroc")
            if fitted is not None and ("%.3f" % fitted) not in text and ("%.3g" % fitted) not in text:
                issues.append("HTML is missing fitted AUROC %.3f for k=%s" % (fitted, k))
            rnd = setting.get("random_rotations") or {}
            if rnd.get("fitted_percentile") is not None:
                pct = rnd["fitted_percentile"]
                # 98.0 may appear as 98th
                token = str(int(pct)) if float(pct) == int(pct) else str(pct)
                if token not in text:
                    issues.append("HTML is missing the random-rotation percentile %s for k=%s" % (token, k))
        selection = card.get("layer_selection") or {}
        if selection.get("validation_ceiling") and "ceiling" not in text_l:
            issues.append("HTML does not flag CEILING, but the card's validation transfer saturated")
    # tag balance on the snippet if we can find it
    start = html.find('id="rotated-lens-controls"')
    if start < 0:
        start = html.find("id='rotated-lens-controls'")
    if start >= 0:
        # balance on the whole page instead, later; here just the file-level check is in the site test
        pass
    return issues


def _load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _dump(doc):
    return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("path", nargs="?", help="a v1 controls card JSON, or a pilot JSON with --from-pilot")
    p.add_argument("--from-pilot", action="store_true", help="read a P90 box-pilot JSON and emit a v1 card")
    p.add_argument("-o", "--output", help="write the card JSON here (with --from-pilot)")
    p.add_argument("--check-html", metavar="HTML", help="check a published page for the required card copy")
    p.add_argument("--card", help="v1 card JSON to check the HTML against")
    args = p.parse_args(argv)

    if args.from_pilot:
        if not args.path:
            print("rotated_lens_card: --from-pilot needs a pilot JSON path", file=sys.stderr)
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
                print("rotated_lens_card: %s" % issue, file=sys.stderr)
            return 1
        return 0

    html_issues = []
    card = None
    report = None
    if args.path:
        card = _load_json(args.path)
        report = evaluate_card(card)
        print(json.dumps({k: report[k] for k in ("complete", "issues", "notes", "layer_method_ok", "validation_ceiling", "cross_model", "chain_ok", "settings")}, indent=2))
        if not report["complete"]:
            for issue in report["issues"]:
                print("rotated_lens_card: %s" % issue, file=sys.stderr)
    if args.check_html:
        if args.card and card is None:
            card = _load_json(args.card)
        html = open(args.check_html, encoding="utf-8").read()
        html_issues = check_html(html, card)
        for issue in html_issues:
            print("rotated_lens_card: %s" % issue, file=sys.stderr)
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
