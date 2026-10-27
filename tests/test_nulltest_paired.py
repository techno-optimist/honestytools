#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Tests for nulltest paired mode (--pair-key). Each states the defect it would catch."""
import contextlib, io, json, os, random, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import nulltest as N  # noqa: E402
import nulltest_paired as P  # noqa: E402

PERM = 120


def _word(r, k=6):
    return "".join(r.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(k))


def pairs_battery(n, seed, leak=None):
    """n pairs sharing a random topic word; `leak` plants a cue on the label-1 member."""
    r = random.Random(seed)
    out = []
    for i in range(n):
        topic = _word(r)
        a = "%s %s %s" % (topic, _word(r), _word(r))
        b = "%s %s %s" % (topic, _word(r), _word(r))
        if leak == "length":
            b = b + " " + _word(r, 9)
        elif leak == "word":
            b = b + " never"
        out.append({"text": a, "label": 0, "q": i})
        out.append({"text": b, "label": 1, "q": i})
    return out


class Paired(unittest.TestCase):
    def test_null_battery_passes(self):
        """a sign-flip null that is miscalibrated would DISQUALIFY pure noise"""
        rep = P.evaluate_paired(pairs_battery(80, 1), "text", "label", "q", n_perm=PERM)
        self.assertEqual(rep["verdict"], "PASS")

    def test_length_leak_fires(self):
        """paired mode must catch a within-pair length cue"""
        rep = P.evaluate_paired(pairs_battery(60, 2, "length"), "text", "label", "q", n_perm=PERM)
        self.assertEqual(rep["verdict"], "DISQUALIFIED")
        self.assertEqual(rep["nulls"]["length"]["per_stratum"]["_all"]["astar_pair"], 1.0)

    def test_word_leak_fires_on_fitted_probe(self):
        """a refit probe with pair-grouped folds must still learn a planted word"""
        rep = P.evaluate_paired(pairs_battery(60, 3, "word"), "text", "label", "q", n_perm=PERM)
        self.assertTrue(rep["nulls"]["words"]["per_stratum"]["_all"]["separates"])

    def test_folds_keep_pairs_together(self):
        """a pair split across folds lets the probe see the partner"""
        items = pairs_battery(50, 4)
        pairs, _, _ = P.make_pairs(items, "label", "q")
        f = P._pair_folds(len(items), pairs)
        for _, a, b in pairs:
            self.assertEqual(f[a], f[b])
        self.assertEqual(len(set(f)), N.N_FOLDS)

    def test_bad_pairs_dropped_and_counted(self):
        """a triple or a same-label pair must be dropped, never silently scored"""
        items = pairs_battery(30, 5)
        items.append({"text": "x y z", "label": 1, "q": 0})          # q=0 becomes a triple
        items += [{"text": "p q", "label": 0, "q": "s"}, {"text": "r s", "label": 0, "q": "s"}]
        pairs, dropped, _ = P.make_pairs(items, "label", "q")
        self.assertEqual(len(pairs), 29)
        self.assertEqual(dropped, 2)

    def test_shared_topic_not_a_leak(self):
        """pooled (unpaired) scoring can be fooled by shared-topic structure; paired must not
        treat a topic word both members share as signal"""
        rep = P.evaluate_paired(pairs_battery(80, 6), "text", "label", "q", n_perm=PERM)
        self.assertFalse(rep["nulls"]["lexicality"]["per_stratum"]["_all"]["separates"])

    def test_deterministic(self):
        """same seed, same report — a replayable receipt needs this"""
        it = pairs_battery(40, 7, "length")
        a = P.evaluate_paired(it, "text", "label", "q", n_perm=PERM)
        b = P.evaluate_paired(it, "text", "label", "q", n_perm=PERM)
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))

    def test_cli_explode_row(self):
        """--explode + --pair-key _row is the TruthfulQA path"""
        r = random.Random(8)
        rows = [{"right": "%s %s" % (_word(r), _word(r)), "wrong": "%s %s %s" % (_word(r), _word(r), _word(r, 9))}
                for _ in range(40)]
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(rows, fh)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = P.main(["nulltest_paired", fh.name, "--explode", "right=0,wrong=1", "--pair-key", "_row",
                           "--n-perm", str(PERM), "--json"])
        os.unlink(fh.name)
        rep = json.loads(out.getvalue())
        self.assertEqual(rep["mode"], "paired")
        self.assertEqual(rep["n_pairs"], 40)
        self.assertEqual(rep["verdict"], "DISQUALIFIED")
        self.assertEqual(code, 1)



class CoreUntouched(unittest.TestCase):
    def test_nulltest_has_no_pair_flag(self):
        """paired mode must live beside nulltest.py, not in it: the committed certificates are
        stamped with nulltest.py's sha256, and CI fails if that file changes"""
        src = open(os.path.join(os.path.dirname(HERE), "nulltest.py")).read()
        self.assertNotIn("pair-key", src)


if __name__ == "__main__":
    unittest.main()
