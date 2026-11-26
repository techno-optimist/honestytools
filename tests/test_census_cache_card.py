#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Kevin Russell (Aperture / Project Forty Two)
"""P93 census cache-storage controls card — plant a defect, require the checker to fire.

Each defect test has a matching complete card, so a checker that rejects
everything fails too. The fixture is the 2026-11-26 SmolLM2-135M box pilot.

    python3 -B -m unittest -v tests.test_census_cache_card
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

import census_cache_card as C  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "census_cache_card_smollm2_p93.json")
SCRIPT = os.path.join(ROOT, "census_cache_card.py")
PILOT = os.path.join(HERE, "fixtures", "tool14_p93_cache_codec_floor_pilot_20261126.json")


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
    def test_fixture_is_complete_and_reports_the_pilot_shifts(self):
        report = C.evaluate_card(load_card())
        self.assertTrue(report["complete"], report["issues"])
        by_id = {c["id"]: c for c in report["codecs"]}
        self.assertEqual(by_id["fp16"]["unstable"], [])
        self.assertEqual(by_id["int8_tok"]["unstable"], ["nspk"])
        self.assertAlmostEqual(by_id["int8_tok"]["shifts"]["nspk"], 27)
        self.assertAlmostEqual(by_id["int4_tok"]["shifts"]["pr_c"], 6.044905339976552)
        self.assertAlmostEqual(by_id["int4_tok"]["shifts"]["nspk"], 1)
        self.assertIn("pr_c", by_id["int4_tok"]["unstable"])
        self.assertIn("pr_m", by_id["int4_tok"]["unstable"])
        self.assertNotIn("nspk", by_id["int4_tok"]["unstable"])
        diffs = {d["statistic"]: d for d in report["population_differences"]}
        self.assertTrue(diffs["pr_c"]["real"])
        self.assertFalse(diffs["nspk"]["real"])
        self.assertEqual(report["content_pc1_from"], "full_covariance")
        self.assertTrue(any("8-bit" in n for n in report["notes"]))

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

    def test_missing_bit_depth_fails(self):
        def mutate(card):
            card["caches"][0]["bit_depth"] = None
        self._fail(mutate, "bit depth")

    def test_missing_forward_dtype_fails(self):
        def mutate(card):
            card["caches"][0]["forward_dtype"] = None
        self._fail(mutate, "forward dtype")

    def test_retained_cache_without_hash_fails(self):
        def mutate(card):
            card["caches"][0]["retained"] = True
            card["caches"][0]["cache_hash"] = None
        self._fail(mutate, "cache hash")

    def test_reading_without_cache_id_fails(self):
        def mutate(card):
            card["readings"][0]["cache_id"] = None
        self._fail(mutate, "does not name the cache")

    def test_missing_half_split_floor_fails(self):
        def mutate(card):
            del card["floors"]["half_split"]
        self._fail(mutate, "half-split")

    def test_population_difference_without_permutation_floor_fails(self):
        def mutate(card):
            del card["floors"]["label_permutation"]
        self._fail(mutate, "permutation")

    def test_unjustified_real_difference_fails(self):
        def mutate(card):
            for item in card["population_differences"]:
                if item["statistic"] == "nspk":
                    item["real"] = True
        self._fail(mutate, "calls the population difference real")

    def test_missing_codec_shifts_fails(self):
        def mutate(card):
            del card["codecs"][1]["shift_vs_fp32"]
        self._fail(mutate, "shifts from an fp32")

    def test_unjustified_codec_stable_flag_fails(self):
        def mutate(card):
            for codec in card["codecs"]:
                if codec["id"] == "int8_tok":
                    codec["codec_stable"]["nspk"] = True
        self._fail(mutate, "codec-stable")

    def test_content_pc1_from_subsample_fails(self):
        def mutate(card):
            card["content_pc1_from"] = "subsample"
        self._fail(mutate, "subsample")

    def test_missing_unexplained_8bit_vs_4bit_note_fails(self):
        def mutate(card):
            card["spike_count_not_monotone_in_bit_depth"] = False
            card["notes"] = []
            card["unexplained"] = ""
        self._fail(mutate, "8-bit moved the spike count")

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
            with open(out, encoding="utf-8") as f:
                report = C.evaluate_card(json.load(f))
            self.assertTrue(report["complete"], report["issues"])


class TestHtmlCheck(unittest.TestCase):
    def test_missing_card_anchor_fails(self):
        issues = C.check_html("<html><p>no card here</p></html>", load_card())
        self.assertTrue(any("census-cache-controls" in i for i in issues))

    def test_complete_snippet_passes(self):
        html = """
        <section id="census-cache-controls">
          <h4>Controls card</h4>
          <p>Every census reading records how its cache was stored and is
          checked against a resampling baseline (half-split and permutation
          floors). 16-bit storage stayed below the floor. 4-bit moved the
          content participation ratio +6.04. 8-bit moved the spike count more
          than 4-bit did (+27 vs +1).</p>
        </section>
        """
        issues = C.check_html(html, load_card())
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
                    '<section id="census-cache-controls"><p>Controls card: '
                    "cache storage and a resampling floor. 16-bit stayed below "
                    "the floor. 4-bit content participation ratio +6.04. "
                    "8-bit moved the spike count more than 4-bit did "
                    "(+27 vs +1).</p></section>"
                )
            result = run_cli(["--check-html", good, "--card", FIXTURE])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
