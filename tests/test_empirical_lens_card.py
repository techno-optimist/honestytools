#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Kevin Russell (Aperture / Project Forty Two)
"""P91 empirical-lens controls card — plant a defect, require the checker to fire.

Each defect test has a matching complete card, so a checker that rejects
everything fails too. The fixture is the 2026-11-26 FOF-Bench box pilot.

    python3 -B -m unittest -v tests.test_empirical_lens_card
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import empirical_lens_card as C  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "empirical_lens_card_fofbench_p91.json")
SCRIPT = os.path.join(ROOT, "empirical_lens_card.py")
PILOT = os.path.join(HERE, "fixtures", "tool07_p91_fact_vs_surface_pilot_20261126.json")


def load_card():
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


def run_cli(args):
    return subprocess.run(
        [sys.executable, "-B", SCRIPT, *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


class TestCompleteFixture(unittest.TestCase):
    def test_fixture_is_complete_and_reports_the_pilot_rates(self):
        report = C.evaluate_card(load_card())
        self.assertTrue(report["complete"], report["issues"])
        by_name = {s["name"]: s for s in report["frames"]}
        mini = by_name["MiniLM-L6 mean raw"]
        self.assertEqual(mini["fact_altered_familiar"], 1.0)
        self.assertEqual(mini["faithful_uncharted"], 0.0)
        self.assertFalse(mini["length_matched_flips"])
        raw = by_name["SmolLM2-135M L20 mean raw"]
        self.assertAlmostEqual(raw["fact_altered_familiar"], 0.9991666666666666)
        self.assertAlmostEqual(raw["faithful_uncharted"], 0.8541666666666666)
        self.assertAlmostEqual(raw["faithful_retrieval"], 0.06333333333333334)
        self.assertTrue(raw["length_matched_flips"])
        self.assertAlmostEqual(raw["length_matched_uncharted"], 0.07666666666666666)
        centred = by_name["SmolLM2-135M L20 mean centred"]
        self.assertAlmostEqual(centred["fact_altered_familiar"], 0.9966666666666667)
        self.assertAlmostEqual(centred["faithful_uncharted"], 0.0033333333333333335)
        self.assertAlmostEqual(centred["faithful_retrieval"], 0.05416666666666667)
        self.assertTrue(centred["length_matched_flips"])
        self.assertEqual(len(report["verdicts"]), 1)
        self.assertEqual(report["verdicts"][0]["verdict"], "familiar")

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

    def test_missing_nearest_items_on_a_familiar_verdict_fails(self):
        def mutate(card):
            card["familiar_verdicts"][0]["nearest"] = []
        self._fail(mutate, "nearest reference items")

    def test_familiar_wording_seen_fails(self):
        def mutate(card):
            card["familiar_verdicts"][0]["wording"] = "this text was seen in the atlas"
        self._fail(mutate, "seen")

    def test_familiar_wording_known_fails(self):
        def mutate(card):
            card["familiar_verdicts"][0]["wording"] = "known document in the atlas"
        self._fail(mutate, "known")

    def test_familiar_wording_without_close_phrase_fails(self):
        def mutate(card):
            card["familiar_verdicts"][0]["wording"] = "matches atlas-000 (cosine 0.998)"
        self._fail(mutate, "close in this embedding")

    def test_missing_same_source_fails_when_atlas_has_source_ids(self):
        def mutate(card):
            del card["familiar_verdicts"][0]["nearest"][0]["same_source"]
        self._fail(mutate, "same_source")

    def test_missing_fact_altered_block_fails(self):
        def mutate(card):
            del card["frames"][0]["fact_altered_near_copies"]
        self._fail(mutate, "fact-altered")

    def test_missing_faithful_rewrites_fails(self):
        def mutate(card):
            del card["frames"][0]["faithful_rewrites"]
        self._fail(mutate, "faithful rewrites")

    def test_missing_own_item_retrieval_fails(self):
        def mutate(card):
            del card["frames"][0]["faithful_rewrites"]["own_item_retrieval"]
        self._fail(mutate, "own-item retrieval")

    def test_missing_in_set_false_alarm_fails(self):
        def mutate(card):
            del card["frames"][0]["in_set_false_alarm"]
            if "baseline" in card["frames"][0]:
                del card["frames"][0]["baseline"]
        self._fail(mutate, "false-alarm")

    def test_missing_length_matched_rescore_when_lengths_differ_fails(self):
        def mutate(card):
            del card["frames"][1]["faithful_rewrites_length_matched"]
            card["frames"][1].pop("length_matched_flips", None)
        self._fail(mutate, "length-matched")

    def test_flip_flag_disagreeing_with_shares_fails(self):
        def mutate(card):
            card["frames"][1]["length_matched_flips"] = False
        self._fail(mutate, "flips is false")

    def test_familiar_and_uncharted_not_adding_to_one_fails(self):
        def mutate(card):
            card["frames"][0]["fact_altered_near_copies"]["familiar"] = 0.5
            card["frames"][0]["fact_altered_near_copies"]["uncharted"] = 0.1
        self._fail(mutate, "do not add to 1")

    def test_complete_card_is_not_rejected(self):
        report = C.evaluate_card(load_card())
        self.assertTrue(report["complete"], report["issues"])


class TestFromPilot(unittest.TestCase):
    @unittest.skipUnless(os.path.exists(PILOT), "re-run pilot JSON is not on this machine")
    def test_from_pilot_matches_the_fixture(self):
        with open(PILOT, encoding="utf-8") as f:
            emitted = C.cards_from_pilot(json.load(f))
        fixture = load_card()
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
        self.assertTrue(any("empirical-lens-controls" in i for i in issues))

    def test_complete_snippet_passes(self):
        card = load_card()
        html = """
        <section id="empirical-lens-controls">
          <h4>Controls card</h4>
          <p>Every familiar result shows the nearest reference items, worded
          close in this embedding to those items, never those other words.
          Fact-altered near-copies still read familiar 99.6% or more.
          Faithful rewrites read uncharted 0.0%, 85.4%, and 0.3%.
          Own-item retrieval on faithful rewrites was 99.3%, 6.3%, and 5.4%.
          In-set false-alarm rate 5.0%. Length-matched re-score flips on
          SmolLM2 raw (7.7%) and centred (10.9%).</p>
        </section>
        """
        issues = C.check_html(html, card)
        self.assertEqual(issues, [], issues)

    def test_banned_word_in_card_fails(self):
        html = """
        <section id="empirical-lens-controls">
          <h4>Controls card</h4>
          <p>close in this embedding, nearest items, fact-altered, faithful
          rewrites uncharted, own-item retrieval, false-alarm, length, 99.6%,
          this was seen in training.</p>
        </section>
        """
        issues = C.check_html(html, load_card())
        self.assertTrue(any("seen" in i for i in issues))

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
                    '<section id="empirical-lens-controls"><p>Controls card: '
                    "nearest items, close in this embedding to them, "
                    "fact-altered near-copies 99.6%, faithful rewrites "
                    "uncharted 85.4%, own-item retrieval 6.3% and 5.4%, "
                    "false-alarm 5.0%, length-matched 7.7% and 10.9%.</p>"
                    "</section>"
                )
            result = run_cli(["--check-html", good, "--card", FIXTURE])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
