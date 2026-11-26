#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Kevin Russell (Aperture / Project Forty Two)
"""P90 rotated-lens controls card — plant a defect, require the checker to fire.

Each defect test has a matching complete card, so a checker that rejects
everything fails too. The fixture is the 2026-11-26 SmolLM2-135M box pilot.

    python3 -B -m unittest -v tests.test_rotated_lens_card
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import rotated_lens_card as C  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "rotated_lens_card_smollm2_p90.json")
SCRIPT = os.path.join(ROOT, "rotated_lens_card.py")
PILOT = os.path.join(HERE, "fixtures", "tool05_p90_scale_controls_layer_pilot_20261126.json")


def load_card():
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


def run_cli(args, stdin=None):
    return subprocess.run(
        [sys.executable, "-B", SCRIPT, *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


class TestPercentiles(unittest.TestCase):
    def test_linear_p95_matches_pilot_scores(self):
        card = load_card()
        for setting in card["settings"]:
            scores = setting["random_rotations"]["scores"]
            p95 = C.percentile_linear(scores, 95)
            self.assertAlmostEqual(p95, setting["random_rotations"]["auroc_p95"], places=3)
            pct = C.percentile_rank(setting["fitted"]["auroc"], scores)
            self.assertAlmostEqual(pct, setting["random_rotations"]["fitted_percentile"], places=1)


class TestCompleteFixture(unittest.TestCase):
    def test_fixture_is_complete_and_does_not_credit_full_width_ranking(self):
        report = C.evaluate_card(load_card())
        self.assertTrue(report["complete"], report["issues"])
        self.assertTrue(report["layer_method_ok"])
        self.assertTrue(report["validation_ceiling"])
        self.assertTrue(report["chain_ok"])
        self.assertFalse(report["cross_model"])
        by_k = {s["k"]: s for s in report["settings"]}
        self.assertTrue(by_k[64]["credit_ranking"])
        self.assertTrue(by_k[64]["above_controls"])
        self.assertEqual(by_k[64]["random_percentile"], 98.0)
        self.assertEqual(by_k[64]["shuffled_percentile"], 96.0)
        self.assertFalse(by_k[576]["credit_ranking"])
        self.assertTrue(by_k[576]["credit_accuracy"])
        self.assertEqual(by_k[576]["identity_used"], "ambient_identity")
        self.assertAlmostEqual(by_k[576]["ranking_gain"], 0.001, places=3)
        self.assertTrue(any("CEILING" in n for n in report["notes"]))
        self.assertTrue(any("no ranking gain" in n for n in report["notes"]))

    def test_cli_exits_0_on_the_fixture(self):
        result = run_cli([FIXTURE])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        doc = json.loads(result.stdout)
        self.assertTrue(doc["complete"])


class TestPlantedDefects(unittest.TestCase):
    def _fail(self, mutate, needle):
        card = load_card()
        mutate(card)
        report = C.evaluate_card(card)
        self.assertFalse(report["complete"], "planted defect was not reported")
        blob = " ".join(report["issues"])
        self.assertIn(needle, blob)

    def test_too_few_random_maps_fails(self):
        def mutate(card):
            rnd = card["settings"][0]["random_rotations"]
            rnd["n"] = 49
            rnd["scores"] = rnd["scores"][:49]
        self._fail(mutate, "at least 50")

    def test_missing_shuffled_block_fails(self):
        def mutate(card):
            del card["settings"][0]["shuffled_pair_rotations"]
        self._fail(mutate, "shuffled-pair")

    def test_missing_identity_fails(self):
        def mutate(card):
            for s in card["settings"]:
                s.pop("no_rotation_principal", None)
                s.pop("ambient_identity", None)
                s.pop("no_rotation", None)
        self._fail(mutate, "identity")

    def test_missing_intercept_fails(self):
        def mutate(card):
            del card["intercept"]
        self._fail(mutate, "intercept")

    def test_missing_scale_fails(self):
        def mutate(card):
            del card["settings"][0]["scale_s"]
        self._fail(mutate, "scale")

    def test_cka_layer_selection_fails(self):
        def mutate(card):
            card["layer_selection"]["method"] = "linear_cka"
        self._fail(mutate, "geometric similarity")

    def test_unjustified_ranking_credit_at_full_width_fails(self):
        def mutate(card):
            card["settings"][1]["credit"] = {"ranking": True}
        self._fail(mutate, "credits ranking")

    def test_scale_credit_with_near_zero_intercept_fails(self):
        def mutate(card):
            card["settings"][0]["credit"] = {"scale": True}
        self._fail(mutate, "near zero")

    def test_cross_model_without_chain_check_fails(self):
        def mutate(card):
            card["cross_model"] = True
            card["chain_check"] = None
        self._fail(mutate, "chain check")

    def test_stated_percentile_disagreeing_with_scores_fails(self):
        def mutate(card):
            card["settings"][0]["random_rotations"]["fitted_percentile"] = 10.0
        self._fail(mutate, "percentile")

    def test_complete_card_is_not_rejected(self):
        # Guard against a checker that fails everything.
        report = C.evaluate_card(load_card())
        self.assertTrue(report["complete"], report["issues"])


class TestFromPilot(unittest.TestCase):
    @unittest.skipUnless(os.path.exists(PILOT), "re-run pilot JSON is not on this machine")
    def test_from_pilot_matches_the_fixture(self):
        with open(PILOT, encoding="utf-8") as f:
            emitted = C.cards_from_pilot(json.load(f))
        fixture = load_card()
        # runtime_s can differ by a second across runs; everything else must match
        emitted.get("pilot", {}).pop("runtime_s", None)
        fixture.get("pilot", {}).pop("runtime_s", None)
        self.assertEqual(emitted, fixture)

    def test_cli_from_pilot_roundtrip(self):
        if not os.path.exists(PILOT):
            self.skipTest("re-run pilot JSON is not on this machine")
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "card.json")
            result = run_cli(["--from-pilot", PILOT, "-o", out])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(os.path.exists(out))
            with open(out, encoding="utf-8") as f:
                report = C.evaluate_card(json.load(f))
            self.assertTrue(report["complete"], report["issues"])


class TestHtmlCheck(unittest.TestCase):
    def test_missing_card_anchor_fails(self):
        issues = C.check_html("<html><p>no card here</p></html>", load_card())
        self.assertTrue(any("rotated-lens-controls" in i for i in issues))

    def test_complete_snippet_passes(self):
        card = load_card()
        html = """
        <section id="rotated-lens-controls">
          <h4>Controls card</h4>
          <p>Fitted rotation vs random and shuffled-pair rotations (percentile and p95),
          no rotation, layers chosen by held-out transfer, not by CKA, scale 3.036 and
          2.888, intercept -0.0028. CEILING on validation. k=64 fitted AUROC 0.782 at
          the 98th percentile of random rotations; k=576 fitted AUROC 0.999 at the
          100th percentile.</p>
        </section>
        """
        issues = C.check_html(html, card)
        self.assertEqual(issues, [], issues)

    def test_cli_check_html_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, "bad.html")
            with open(bad, "w", encoding="utf-8") as f:
                f.write("<html></html>")
            result = run_cli(["--check-html", bad, "--card", FIXTURE])
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            good = os.path.join(tmp, "good.html")
            with open(good, "w", encoding="utf-8") as f:
                f.write(
                    '<section id="rotated-lens-controls"><p>Controls card: random and '
                    "shuffled-pair rotations, percentile, no rotation, layers chosen by "
                    "held-out transfer, not by CKA, scale and intercept -0.0028. "
                    "CEILING. 0.782 98th, 0.999 100th.</p></section>"
                )
            result = run_cli(["--check-html", good, "--card", FIXTURE])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
