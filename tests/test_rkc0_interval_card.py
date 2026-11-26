#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Kevin Russell (Aperture / Project Forty Two)
"""P94 RKC0 interval and rerun controls card — plant a defect, require the checker to fire.

Each defect test has a matching complete card, so a checker that rejects
everything fails too. The fixture is the 2026-11-26 known-answer box pilot.

    python3 -B -m unittest -v tests.test_rkc0_interval_card
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

import rkc0_interval_card as C  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "rkc0_interval_card_p94.json")
SCRIPT = os.path.join(ROOT, "rkc0_interval_card.py")
PILOT = os.path.join(HERE, "fixtures", "tool17_p94_interval_floor_pilot_20261126.json")


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
    def test_fixture_is_complete_and_reports_the_pilot_figures(self):
        report = C.evaluate_card(load_card())
        self.assertTrue(report["complete"], report["issues"])
        self.assertEqual(report["interval"]["method"], "gaffke")
        self.assertTrue(report["interval"]["exact"])
        self.assertEqual(report["interval"]["n_worlds"], 12)
        self.assertEqual(report["change_floor"]["n_replicas"], 3)
        self.assertAlmostEqual(report["change_floor"]["unchanged"]["single_pair"], 0.486)
        self.assertAlmostEqual(report["change_floor"]["unchanged"]["floor3_p95_binomial"], 0.004)
        self.assertAlmostEqual(report["selection"]["in_sample_gain_pp"], 9.46)
        self.assertIsNone(report["noise_split"])
        card = load_card()
        headline = card["interval"]["headline_binary_p0.98"]
        self.assertEqual(headline["10"]["wald"], 0.18)
        self.assertEqual(headline["20"]["student_t"], 0.333)
        self.assertEqual(headline["30"]["gaffke"], 0.977)

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
        self.assertFalse(report["complete"], "planted defect was not reported: %s" % report["issues"])
        blob = " ".join(report["issues"])
        self.assertIn(needle, blob)

    def test_missing_interval_method_fails(self):
        def mutate(card):
            del card["interval"]["certifying_method"]
        self._fail(mutate, "interval method")

    def test_wald_as_certifying_interval_for_pass_fail_fails(self):
        def mutate(card):
            card["interval"]["certifying_method"] = "wald"
        self._fail(mutate, "exact method")

    def test_missing_coverage_worlds_fails(self):
        def mutate(card):
            del card["interval"]["coverage_worlds"]
        self._fail(mutate, "coverage worlds")

    def test_too_few_replicas_fails(self):
        def mutate(card):
            card["change_floor"]["n_replicas"] = 2
        self._fail(mutate, "at least 3")

    def test_single_pair_as_floor_fails(self):
        def mutate(card):
            card["change_floor"]["certified_against"] = "single_pair"
        self._fail(mutate, "single replica pair")

    def test_missing_unchanged_world_fails(self):
        def mutate(card):
            del card["change_floor"]["worlds"]["unchanged"]
        self._fail(mutate, "unchanged world")

    def test_method_verdict_without_noise_split_fails(self):
        def mutate(card):
            card["method_level"] = True
        self._fail(mutate, "sigma^2")

    def test_method_verdict_without_independent_runs_fails(self):
        def mutate(card):
            card["method_level"] = True
            card["noise_split"] = {"sigma2": 0.1, "tau2": 0.2, "n_runs": 1}
        self._fail(mutate, "independent runs")

    def test_selection_without_shuffle_null_fails(self):
        def mutate(card):
            del card["selection"]["shuffle_null"]
        self._fail(mutate, "shuffled")

    def test_selection_without_held_out_fails(self):
        def mutate(card):
            del card["selection"]["held_out"]
            del card["selection"]["shuffle_null"]["held_out_gain_pp_mean"]
            del card["selection"]["shuffle_null"]["held_out_gain_pp_p5_p95"]
        self._fail(mutate, "held-out")

    def test_harm_count_without_arithmetic_floor_fails(self):
        def mutate(card):
            card["selection"]["harm_count"] = 12
            card["selection"]["arithmetic_floor_share"] = None
            del card["selection"]["shuffle_null"]["share_tasks_baseline_0_of_3_cannot_show_decrease"]
        self._fail(mutate, "cannot show a decrease")

    def test_complete_card_is_not_rejected(self):
        report = C.evaluate_card(load_card())
        self.assertTrue(report["complete"], report["issues"])


class TestFromPilot(unittest.TestCase):
    @unittest.skipUnless(os.path.exists(PILOT), "re-run pilot JSON is not on this machine")
    def test_from_pilot_matches_the_fixture(self):
        with open(PILOT, encoding="utf-8") as f:
            emitted = C.cards_from_pilot(json.load(f))
        self.assertEqual(emitted, load_card())

    def test_cli_from_pilot_roundtrip(self):
        if not os.path.exists(PILOT):
            self.skipTest("re-run pilot JSON is not on this machine")
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "card.json")
            result = run_cli(["--from-pilot", PILOT, "-o", out])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            with open(out, encoding="utf-8") as f:
                report = C.evaluate_card(json.load(f))
            self.assertTrue(report["complete"], report["issues"])


class TestHtmlCheck(unittest.TestCase):
    def test_missing_card_anchor_fails(self):
        issues = C.check_html("<html><p>no card here</p></html>", load_card())
        self.assertTrue(any("rkc0-interval-controls" in i for i in issues))

    def test_complete_snippet_passes(self):
        html = """
        <section id="rkc0-interval-controls">
          <h4>Controls card</h4>
          <p>Every RKC0 verdict names its interval method. Gaffke is the
          exact certifying interval. A change is judged against at least
          3 replicas. A per-task gate is tested in a shuffled zero-effect
          world and on held-out runs. Wald coverage 18% at n=10; one
          replica pair rejected an unchanged setup 48.6% of the time;
          in-sample gain 9.46 pp, held-out 0.02 pp.</p>
        </section>
        """
        issues = C.check_html(html, load_card())
        self.assertEqual(issues, [], issues)

    def test_banned_word_fails(self):
        html = """
        <section id="rkc0-interval-controls">
          <p>Controls card, Gaffke interval, 3 replicas, shuffled labels,
          held-out runs, 18%, 48.6%, 9.46, 0.02. A +1.5% change was caught.</p>
        </section>
        """
        issues = C.check_html(html, load_card())
        self.assertTrue(any("caught" in i for i in issues))

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
                    '<section id="rkc0-interval-controls"><p>Controls card: '
                    "Gaffke interval, exact for pass/fail, 3 replicas, "
                    "shuffled labels, held-out check. Wald 18% at n=10, "
                    "single-pair 48.6%, in-sample 9.46 pp, held-out "
                    "0.02 pp.</p></section>"
                )
            result = run_cli(["--check-html", good, "--card", FIXTURE])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
