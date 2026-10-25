#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for nulltest.py — stdlib unittest, no fixtures outside this file.

    python3 -m unittest discover -s tests -v

Each test states the defect it would catch. A test that cannot fail on a plausible regression is
decoration, so every one of these was watched failing against a deliberately broken version first.
"""
import contextlib
import io
import json
import math
import os
import random
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import nulltest as N  # noqa: E402

PERM = 120   # enough to resolve alpha=0.05 (floor 1/121) and keep the suite under a minute


def _rng(seed):
    return random.Random(seed)


def _ids(r, n):
    return ["NCT%08d" % r.randrange(10 ** 7) for _ in range(n)]


def _cvcv(r, n, syll=4):
    return ["".join(r.choice("bcdfg") + r.choice("aeiou") for _ in range(syll)) for _ in range(n)]


def battery(texts0, texts1, cat="c"):
    return ([{"entity": t, "label": 0, "category": cat} for t in texts0]
            + [{"entity": t, "label": 1, "category": cat} for t in texts1])


class Statistics(unittest.TestCase):
    def test_auroc_perfect_and_inverted(self):
        y = [0, 0, 1, 1]
        self.assertEqual(N.auroc(y, [1, 2, 3, 4]), 1.0)
        self.assertEqual(N.auroc(y, [4, 3, 2, 1]), 0.0)
        # RULE 1: an inverted classifier is as bad as a correct one
        self.assertEqual(N.astar(y, [4, 3, 2, 1]), 1.0)

    def test_auroc_ties_are_half_ranked(self):
        self.assertAlmostEqual(N.auroc([0, 1], [1, 1]), 0.5)

    def test_auroc_single_class_is_none(self):
        self.assertIsNone(N.auroc([1, 1, 1], [1, 2, 3]))

    def test_clopper_pearson_brackets_the_rate(self):
        lo, hi = N.clopper_pearson(5, 100)
        self.assertLess(lo, 0.05)
        self.assertGreater(hi, 0.05)
        self.assertAlmostEqual(lo, 0.0164, places=3)
        self.assertAlmostEqual(hi, 0.1128, places=3)

    def test_pval_never_zero(self):
        self.assertGreater(N._pval([0.5] * 100, 1.0), 0.0)


class Probes(unittest.TestCase):
    """Each probe must recover the artifact it was written for, on held-out folds."""

    def test_lexicality_catches_spelling(self):
        r = _rng(1)
        b = battery(_ids(r, 60), _cvcv(r, 60))
        y = [i["label"] for i in b]
        s = N.null_lexicality([i["entity"] for i in b], y, None)
        self.assertGreater(N.astar(y, s), 0.95)

    def test_words_catches_a_template(self):
        r = _rng(2)
        t0 = ["The city of %s is in %s." % (w, v) for w, v in zip(_cvcv(r, 60), _cvcv(r, 60))]
        t1 = ["%s is not located in %s." % (w, v) for w, v in zip(_cvcv(r, 60), _cvcv(r, 60))]
        y = [0] * 60 + [1] * 60
        s = N.null_words(t0 + t1, y, None)
        self.assertGreater(N.astar(y, s), 0.95)

    def test_alpha_last_catches_surname_initial_sampling(self):
        """The prominence-people-v1 defect: one class drawn from the top of a surname-sorted list."""
        r = _rng(3)
        first = _cvcv(r, 200, 3)
        sur0 = [r.choice("abcdefghijklmnopqrstuvwxyz") + w for w in _cvcv(r, 100, 3)]
        sur1 = [r.choice("abc") + w for w in _cvcv(r, 100, 3)]            # alphabetically-first
        t0 = ["%s %s" % (f.title(), s.title()) for f, s in zip(first[:100], sur0)]
        t1 = ["%s %s" % (f.title(), s.title()) for f, s in zip(first[100:], sur1)]
        y = [0] * 100 + [1] * 100
        self.assertGreater(N.astar(y, N.null_alpha_last(t0 + t1, y, None)), 0.80)
        self.assertLess(N.astar(y, N.null_alpha_first(t0 + t1, y, None)), 0.65)

    def test_alpha_probe_is_ascii_only_and_safe_on_empty(self):
        self.assertEqual(N._alpha_first(""), 0)
        self.assertEqual(N._alpha_first("Émile Zola"), 0)
        self.assertEqual(N._alpha_last("Émile Zola"), 26)
        self.assertEqual(N._alpha_first("  bravo  "), 2)

    def test_shape_catches_ids_versus_words(self):
        r = _rng(4)
        b = battery(_ids(r, 60), _cvcv(r, 60))
        y = [i["label"] for i in b]
        self.assertGreater(N.astar(y, N.null_shape([i["entity"] for i in b], y, None)), 0.95)

    def test_nb_is_held_out(self):
        """LOO leaked labels and scored pure noise at A* = 1.000. K-fold must stay near chance."""
        r = _rng(5)
        texts = _ids(r, 200)
        y = [r.randrange(2) for _ in texts]
        self.assertLess(N.astar(y, N.null_lexicality(texts, y, None)), 0.65)

    def test_prepared_nb_matches_one_shot(self):
        r = _rng(6)
        texts = _cvcv(r, 80)
        nb = N._NB(texts, N._ngrams)
        for _ in range(3):
            y = [r.randrange(2) for _ in texts]
            self.assertEqual(nb.score(y), N._nb_scores(texts, y, N._ngrams))


class Verdicts(unittest.TestCase):
    def test_planted_separable_is_disqualified(self):
        r = _rng(7)
        rep = N.evaluate(battery(_ids(r, 60), _cvcv(r, 60)), "entity", "label", "category", n_perm=PERM)
        self.assertEqual(rep["verdict"], "DISQUALIFIED")
        self.assertTrue(rep["saturated"])

    def test_noise_passes(self):
        rep = N.evaluate(N._noise_battery(9001, 1, "coin"), "entity", "label", "category", n_perm=PERM)
        self.assertEqual(rep["verdict"], "PASS")

    def test_parallel_equals_serial(self):
        """--jobs changes the wall clock only. The permutations are drawn before scoring."""
        b = N._noise_battery(9002, 2, "coin")
        a = N.evaluate(b, "entity", "label", "category", n_perm=PERM, jobs=1)
        c = N.evaluate(b, "entity", "label", "category", n_perm=PERM, jobs=3)
        self.assertEqual(a["nulls"], c["nulls"])
        self.assertEqual(a["headline"], c["headline"])

    def test_not_scorable_is_not_pass(self):
        """A corpus with no scorable cell used to come back PASS."""
        items = [{"entity": "same", "label": i % 2, "category": "c"} for i in range(40)]
        rep = N.evaluate(items, "entity", "label", "category", n_perm=PERM)
        self.assertEqual(rep["verdict"], "NOT_SCORABLE")

    def test_single_class_cell_is_skipped_not_crashed(self):
        r = _rng(8)
        items = battery(_ids(r, 30), _cvcv(r, 30)) + [{"entity": t, "label": 0, "category": "only0"}
                                                      for t in _ids(r, 20)]
        rep = N.evaluate(items, "entity", "label", "category", n_perm=PERM)
        self.assertNotIn("only0", rep["nulls"]["lexicality"]["per_stratum"])

    def test_worst_cell_tiebreak_prefers_largest_effect(self):
        r = _rng(9)
        rep = N.evaluate(battery(_ids(r, 60), _cvcv(r, 60)), "entity", "label", "category", n_perm=PERM)
        tied = [c["astar"] for e in rep["nulls"].values() for c in e["per_stratum"].values()
                if c["adj_p"] == rep["worst_cell"]["adj_p"]]
        self.assertEqual(rep["worst_cell"]["astar"], max(tied))

    def test_multi_field_text(self):
        r = _rng(10)
        items = [{"q": "What is %s?" % w, "a": a, "label": 0} for w, a in zip(_cvcv(r, 40), _ids(r, 40))]
        items += [{"q": "What is %s?" % w, "a": a, "label": 1} for w, a in zip(_cvcv(r, 40), _cvcv(r, 40))]
        rep = N.evaluate(items, ["q", "a"], "label", None, n_perm=PERM)
        self.assertEqual(rep["verdict"], "DISQUALIFIED")
        self.assertEqual(N.item_text(items[0], ["q", "a"]), items[0]["q"] + "\n" + items[0]["a"])

    def test_report_carries_version_and_probes(self):
        rep = N.evaluate(N._noise_battery(9003, 1, "alt"), "entity", "label", "category", n_perm=PERM)
        self.assertEqual(rep["version"], N.VERSION)
        self.assertEqual(rep["probes"], list(N.NULLS))


class Loading(unittest.TestCase):
    def _write(self, name, text):
        d = tempfile.mkdtemp()
        p = os.path.join(d, name)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        return p

    def test_csv_and_tsv(self):
        p = self._write("b.csv", "statement,label\nThe sky is blue.,1\nThe sky is green.,0\n")
        items = N.read_items(p)
        self.assertEqual(items[0]["statement"], "The sky is blue.")
        tk, lk, sk = N.sniff_keys(items)
        self.assertEqual((tk, lk, sk), ("statement", "label", None))
        p = self._write("b.tsv", "entity\tlabel\tdomain\nA\t0\tx\nB\t1\tx\n")
        self.assertEqual(N.sniff_keys(N.read_items(p)), ("entity", "label", "domain"))

    def test_jsonl_and_wrapped_json(self):
        p = self._write("b.jsonl", '{"text":"a","y":0}\n{"text":"b","y":1}\n')
        self.assertEqual(len(N.read_items(p)), 2)
        p = self._write("b.json", json.dumps({"battery": [{"entity": "a", "label": 0}]}))
        self.assertEqual(N.read_items(p)[0]["entity"], "a")

    def test_labels_known_words_are_coerced(self):
        items = [{"t": "a", "k": "fabricated"}, {"t": "b", "k": "real"}]
        out, pos = N.coerce_labels(items, "k")
        self.assertEqual(pos, "fabricated")
        self.assertEqual([i["k"] for i in out], [1, 0])
        out, _ = N.coerce_labels([{"k": True}, {"k": False}], "k")
        self.assertEqual([i["k"] for i in out], [1, 0])

    def test_unknown_label_words_refuse_to_guess(self):
        """v1.2 mapped 'yes'/'no', 'A'/'B', anything, to 0 silently."""
        with self.assertRaises(N.LabelError):
            N.coerce_labels([{"k": "alpha"}, {"k": "beta"}], "k")
        out, pos = N.coerce_labels([{"k": "alpha"}, {"k": "beta"}], "k", positive="beta")
        self.assertEqual([i["k"] for i in out], [0, 1])

    def test_three_label_values_refuse(self):
        with self.assertRaises(N.LabelError):
            N.coerce_labels([{"k": 0}, {"k": 1}, {"k": 2}], "k")

    def test_positive_label_must_exist(self):
        with self.assertRaises(N.LabelError):
            N.coerce_labels([{"k": 0}, {"k": 1}], "k", positive="7")

    def test_load_splits_comma_text_keys(self):
        p = self._write("qa.json", json.dumps([{"q": "x", "a": "y", "label": 0}, {"q": "u", "a": "v", "label": 1}]))
        items = N.load(p, "q,a", "label")
        self.assertEqual(len(items), 2)


class Novelty(unittest.TestCase):
    def test_copy_of_a_real_has_zero_novelty(self):
        items = [{"entity": "alpha beta", "label": 0}, {"entity": "alpha beta", "label": 1}]
        self.assertEqual(N.novelty(items), 0.0)

    def test_disjoint_alphabet_is_fully_novel(self):
        items = [{"entity": "aaaa", "label": 0}, {"entity": "zzzz", "label": 1}]
        self.assertEqual(N.novelty(items), 1.0)


class Explode(unittest.TestCase):
    """--explode turns question-answer rows into one item per answer, without a flattening script."""
    ROWS = [{"q": "Where?", "right": "Paris", "wrong": "Berlin; Rome", "cat": "Places"},
            {"q": "Who?", "right": "Ada", "wrong": "", "cat": "People"},
            {"q": "When?", "right": None, "wrong": "1999", "cat": "Dates"}]

    def test_parse_explode_pairs(self):
        self.assertEqual(N.parse_explode("right=0, wrong=1"), [("right", "0"), ("wrong", "1")])
        with self.assertRaises(N.LabelError):
            N.parse_explode("right")
        with self.assertRaises(N.LabelError):
            N.parse_explode("")

    def test_explode_one_item_per_answer_with_split(self):
        out = N.explode_items(self.ROWS, [("right", 0), ("wrong", 1)], split=";")
        texts = [(o["text"], o["label"]) for o in out]
        self.assertEqual(texts, [("Paris", 0), ("Berlin", 1), ("Rome", 1), ("Ada", 0), ("1999", 1)])
        # the row's other fields survive, so the category can be a stratum; the answer fields do not
        self.assertEqual(out[1]["cat"], "Places")
        self.assertEqual(out[1]["_row"], 0)
        self.assertEqual(out[1]["_field"], "wrong")
        self.assertNotIn("right", out[0])
        self.assertNotIn("wrong", out[0])

    def test_explode_without_split_keeps_cell_whole(self):
        out = N.explode_items(self.ROWS[:1], [("wrong", 1)])
        self.assertEqual([o["text"] for o in out], ["Berlin; Rome"])

    def test_explode_list_valued_field(self):
        out = N.explode_items([{"a": ["x", "y"], "k": 1}], [("a", 1)])
        self.assertEqual([o["text"] for o in out], ["x", "y"])

    def test_explode_with_no_matching_field_refuses(self):
        with self.assertRaises(N.LabelError):
            N.explode_items(self.ROWS, [("nope", 1)])

    def test_load_explode_end_to_end(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "qa.json")
            with open(p, "w") as fh:
                json.dump(self.ROWS, fh)
            items = N.load(p, "text", "label", explode=[("right", "0"), ("wrong", "1")], split=";")
        self.assertEqual(sum(i["label"] for i in items), 3)
        self.assertEqual(len(items), 5)


class Explain(unittest.TestCase):
    """A DISQUALIFIED verdict must say which strings carry the label, or the author cannot fix it."""

    def test_nb_cell_names_the_leaking_feature(self):
        texts = ["the cat sat %d" % i for i in range(30)] + ["a dog ran %d" % i for i in range(30)]
        y = [0] * 30 + [1] * 30
        d = N.explain_cell("words", texts, y)
        up = [r["feature"] for r in d["favours_label1"]]
        down = [r["feature"] for r in d["favours_label0"]]
        self.assertIn("dog", up)
        self.assertIn("cat", down)
        self.assertTrue(all(r["in_label0"] == 0 for r in d["favours_label1"]))
        self.assertIn("dog", d["reading"])

    def test_score_cell_reports_medians(self):
        texts = ["ab"] * 20 + ["abcdefgh"] * 20
        y = [0] * 20 + [1] * 20
        d = N.explain_cell("length", texts, y)
        self.assertLess(d["median_label0"], d["median_label1"])
        self.assertIn("longer", d["reading"])

    def test_alpha_cell_reads_letters(self):
        texts = ["adam x"] * 15 + ["zed x"] * 15
        d = N.explain_cell("alpha_first", texts, [0] * 15 + [1] * 15)
        self.assertIn("'z'", d["reading"])
        self.assertIn("'a'", d["reading"])

    def test_explain_follows_the_separating_cells(self):
        r = _rng(3)
        items = ([{"entity": "NCT%08d" % r.randrange(10 ** 7), "label": 0, "category": "t"}
                  for _ in range(60)]
                 + [{"entity": "".join(r.choice("bcdfg") + r.choice("aeiou") for _ in range(4)),
                     "label": 1, "category": "t"} for _ in range(60)])
        rep = N.evaluate(items, "entity", "label", "category", n_perm=PERM)
        self.assertEqual(rep["verdict"], "DISQUALIFIED")
        ex = N.explain(items, "entity", "label", "category", rep)
        self.assertTrue(ex["cells"])
        self.assertEqual(ex["cells"][0]["stratum"], "t")
        self.assertIn("held-out", ex["note"])
        # every explained cell is one the report marked as separating
        for c in ex["cells"]:
            self.assertTrue(rep["nulls"][c["probe"]]["per_stratum"][c["stratum"]]["separates"])

    def test_explain_on_a_pass_describes_the_worst_cell_only(self):
        items = N._noise_battery(5, 1, "coin")
        rep = N.evaluate(items, "entity", "label", "category", n_perm=PERM)
        if rep["verdict"] == "DISQUALIFIED":
            self.skipTest("this seed happened to false-fire")
        ex = N.explain(items, "entity", "label", "category", rep)
        self.assertEqual(len(ex["cells"]), 1)
        self.assertEqual(ex["cells"][0]["probe"], rep["worst_cell"]["null"])


class Receipt(unittest.TestCase):
    """A published --json report must be replayable against the bytes it names, and nothing else."""

    def _battery(self, d):
        r = _rng(11)
        items = ([{"entity": "NCT%08d" % r.randrange(10 ** 7), "label": 0, "category": "t"} for _ in range(40)]
                 + [{"entity": "".join(r.choice("bcdfg") + r.choice("aeiou") for _ in range(4)),
                     "label": 1, "category": "t"} for _ in range(40)])
        p = os.path.join(d, "b.json")
        with open(p, "w") as fh:
            json.dump(items, fh)
        return p

    def test_receipt_replays_and_refuses_other_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._battery(d)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                N.main(["nulltest", p, "--json", "--n-perm", str(PERM)])
            rec = json.loads(out.getvalue())
            self.assertEqual(len(rec["battery_sha256"]), 64)
            self.assertEqual(len(rec["tool_sha256"]), 64)
            rp = os.path.join(d, "r.json")
            with open(rp, "w") as fh:
                json.dump(rec, fh)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(N.check_receipt(rp, p), 0)
            # one byte changed in the battery: the receipt no longer describes it
            with open(p, "a") as fh:
                fh.write("\n")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(N.check_receipt(rp, p), 2)

    def test_tampered_receipt_diverges(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._battery(d)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                N.main(["nulltest", p, "--json", "--n-perm", str(PERM)])
            rec = json.loads(out.getvalue())
            rec["verdict"] = "PASS"          # the lie a receipt could tell
            rp = os.path.join(d, "r.json")
            with open(rp, "w") as fh:
                json.dump(rec, fh)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(N.check_receipt(rp, p), 1)


class V14(unittest.TestCase):
    """The v1.4 corrections: a stratified null, an honest permutation count, and the loader
    saying what it did instead of doing it quietly."""

    def test_stratified_null_holds_on_unequal_label_rates(self):
        # two strata at 85% / 15% label-1 and no surface signal: v1.3's unstratified null fired on
        # 44% of these. The stratified null must treat it as noise (seed fixed; deterministic).
        items = N._noise_battery(9001, 2, "coinword_imb")
        rep = N.evaluate(items, "entity", "label", "category", n_perm=PERM)
        self.assertNotEqual(rep["verdict"], "DISQUALIFIED")
        self.assertNotIn("stratum", rep["nulls"])
        rates = rep["label_rate_by_stratum"]
        self.assertEqual(len(rates), 2)
        self.assertGreater(max(r["label1_rate"] for r in rates.values()), 0.7)
        self.assertIn("within stratum", rep["null"])

    def test_permutation_count_is_raised_to_resolve_alpha(self):
        items = battery(_ids(_rng(3), 30), _ids(_rng(4), 30))
        rep = N.evaluate(items, "entity", "label", "category", n_perm=50)
        self.assertEqual(rep["n_perm_requested"], 50)
        self.assertGreaterEqual(rep["n_perm"], 2 * (rep["family_size"] + 1) / rep["alpha"])
        self.assertIn("n_perm_note", rep)

    def test_label_sniffing_prefers_truth_fields_over_type(self):
        items = [{"statement": "a", "type": "claim", "is_true": True}]
        self.assertEqual(N.sniff_keys(items)[1], "is_true")

    def test_stratum_key_none_switches_the_sniffed_stratum_off(self):
        items = [{"text": "a", "label": 0, "category": "x"}]
        self.assertEqual(N.sniff_keys(items)[2], "category")
        self.assertIsNone(N.sniff_keys(items, stratum_key="none")[2])
        self.assertIsNone(N.sniff_keys(items, stratum_key="")[2])

    def test_explode_names_the_field_nobody_has(self):
        items = [{"q": "x", "right": "a", "wrong": "b"}]
        with self.assertRaises(N.LabelError) as cm:
            N.explode_items(items, [("nosuch", "0"), ("wrong", "1")])
        self.assertIn("nosuch", str(cm.exception))
        self.assertIn("right", str(cm.exception))

    def test_read_errors_are_sentences_not_tracebacks(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(N.LabelError) as cm:
                N.read_items(os.path.join(d, "nope.json"))
            self.assertIn("no such file", str(cm.exception))
            p = os.path.join(d, "bad.json")
            with open(p, "w") as fh:
                fh.write('{"a": [1, 2')
            with self.assertRaises(N.LabelError) as cm:
                N.read_items(p)
            self.assertIn("not valid JSON", str(cm.exception))
            p = os.path.join(d, "empty.json")
            open(p, "w").close()
            with self.assertRaises(N.LabelError) as cm:
                N.read_items(p)
            self.assertIn("empty", str(cm.exception))

    def test_positive_label_matches_case_insensitively(self):
        items = [{"t": "a", "label": "True"}, {"t": "b", "label": "FALSE"}]
        out, pos = N.coerce_labels(items, "label", positive="true")
        self.assertEqual(pos, "True")
        self.assertEqual([it["label"] for it in out], [1, 0])

    def test_loader_reports_dropped_rows_and_the_label_mapping(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "b.json")
            with open(p, "w") as fh:
                json.dump([{"text": "a", "label": "fake"}, {"text": "b", "label": "real"},
                           {"text": "c"}, {"text": 7, "label": "real"}], fh)
            info = {}
            out = N.load(p, "text", "label", info=info)
            self.assertEqual(len(out), 3)
            self.assertEqual(info["rows_dropped_missing_field"], 1)
            self.assertEqual(info["non_string_texts"], 1)
            self.assertEqual((info["label1_raw_value"], info["label0_raw_value"]), ("fake", "real"))

    def test_check_accepts_the_toolbox_verdict_shape(self):
        # `honest null` writes the contract Verdict with the receipt fields under "evidence";
        # `honest check` must accept its own output.
        with tempfile.TemporaryDirectory() as d:
            p = Receipt._battery(Receipt(), d)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                N.main(["nulltest", p, "--json", "--n-perm", str(PERM)])
            rec = json.loads(out.getvalue())
            verdict = {"tool": "null", "verdict": rec.pop("verdict"), "evidence": rec}
            rp = os.path.join(d, "v.json")
            with open(rp, "w") as fh:
                json.dump(verdict, fh)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(N.check_receipt(rp, p), 0)
                # swapped arguments are named, not a traceback
                self.assertEqual(N.check_receipt(p, rp), 2)


if __name__ == "__main__":
    unittest.main()


class AuditJoin(unittest.TestCase):
    """nulltest_audit.load_items: fetch -> parse -> join -> normalise -> subsample. The join copies
    named fields from a companion file keyed on a shared id, so a corpus whose stratum lives in a
    second file (RAGTruth's task type) can be stratified without hand-editing the corpus."""

    def _harness(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("nulltest_audit", os.path.join(HERE, "..", "nulltest_audit.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m

    def _files(self, d):
        resp = [{"id": i, "source_id": str(i % 3), "response": "text %d" % i, "labels": [1] if i % 2 else []}
                for i in range(12)]
        resp.append({"id": 99, "source_id": "orphan", "response": "no companion", "labels": []})
        src = [{"source_id": str(k), "task_type": ["QA", "Summary", "Data2txt"][k], "source_info": "..."} for k in range(3)]
        with open(os.path.join(d, "resp.jsonl"), "w") as f:
            f.write("\n".join(json.dumps(r) for r in resp))
        with open(os.path.join(d, "src.jsonl"), "w") as f:
            f.write("\n".join(json.dumps(r) for r in src))
        return {"name": "t/join", "url": "https://example.invalid/never-fetched", "cache_as": "resp.jsonl",
                "format": "jsonl", "text_field": "response", "label_field": "labels", "label_nonempty": True,
                "stratum_field": "task_type",
                "join": {"url": "https://example.invalid/never-fetched-either", "cache_as": "src.jsonl",
                         "format": "jsonl", "key": "source_id", "fields": ["task_type"]}}

    def test_join_attaches_stratum_and_counts_unmatched(self):
        A = self._harness()
        with tempfile.TemporaryDirectory() as d:
            spec = self._files(d)
            items, info = A.load_items(spec, cache_dir=d)
        self.assertEqual(info["items_total"], 13)
        self.assertEqual(info["join_unmatched"], 1)
        cats = sorted({i.get("category") for i in items if "category" in i})
        self.assertEqual(cats, ["Data2txt", "QA", "Summary"])
        orphan = [i for i in items if i["entity"] == "no companion"]
        self.assertEqual(len(orphan), 1)
        self.assertNotIn("category", orphan[0])      # unmatched rows are kept, unstratified, and counted
        self.assertEqual(sum(i["label"] for i in items), 6)   # label_nonempty: a non-empty span list is label-1

    def test_without_join_nothing_changes(self):
        A = self._harness()
        with tempfile.TemporaryDirectory() as d:
            spec = self._files(d)
            del spec["join"]
            items, info = A.load_items(spec, cache_dir=d)
        self.assertEqual(info["join_unmatched"], 0)
        self.assertFalse(any("category" in i for i in items))   # task_type is not on the rows without the join


class AuditZipAndLabelMap(unittest.TestCase):
    """Deception corpora (Apollo's rollouts) keep several sampled responses and their grader
    verdicts as parallel lists, nest fields under `metadata`, and grade on scales whose middle is
    dropped. `zip`, dotted fields and `label_map` read them as published, with no hand-made copy."""

    def _harness(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("nulltest_audit", os.path.join(HERE, "..", "nulltest_audit.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m

    def test_zip_and_label_map_drop_the_middle(self):
        A = self._harness()
        rows = {"rollouts": [
            {"output_strs": ["a1", "a2", "a3"], "graded_deception": [1, 7, 4], "pool": "x"},
            {"output_strs": ["b1", "b2"], "graded_deception": ["2", "6"], "pool": "y"},
            {"output_strs": ["c1", "c2"], "graded_deception": [7], "pool": "y"},     # malformed: skipped
        ]}
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "r.json"), "w") as f:
                json.dump(rows, f)
            spec = {"name": "t/zip", "url": "https://example.invalid/x", "cache_as": "r.json", "format": "json",
                    "root": "rollouts", "zip": ["output_strs", "graded_deception"], "text_field": "output_strs",
                    "label_field": "graded_deception", "label_map": {"1": 0, "2": 0, "6": 1, "7": 1},
                    "stratum_field": "pool"}
            items, info = A.load_items(spec, cache_dir=d)
        self.assertEqual([(i["entity"], i["label"], i["category"]) for i in items],
                         [("a1", 0, "x"), ("a2", 1, "x"), ("b1", 0, "y"), ("b2", 1, "y")])
        self.assertEqual(info["dropped"], 1)          # grade 4 is ambiguous; the malformed row never became items

    def test_dotted_fields(self):
        A = self._harness()
        rows = [{"metadata": {"report_message": "m%d" % i, "report_label": lab}}
                for i, lab in enumerate(["explicit", "concealed", "implied", "invalid", "concealed"])]
        rows.append({"metadata": {"trade_message": "no report"}})
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "g.json"), "w") as f:
                json.dump(rows, f)
            spec = {"name": "t/dot", "url": "https://example.invalid/x", "cache_as": "g.json", "format": "json",
                    "text_field": "metadata.report_message", "label_field": "metadata.report_label",
                    "label_map": {"explicit": 0, "concealed": 1}}
            items, info = A.load_items(spec, cache_dir=d)
        self.assertEqual([(i["entity"], i["label"]) for i in items], [("m0", 0), ("m1", 1), ("m4", 1)])
        self.assertEqual(info["dropped"], 3)


class SurfaceBaseline(unittest.TestCase):
    """surface_baseline.py: how much held-out accuracy the six probes earn together. It must sit at
    the class prior on noise, beat it on a battery with a surface artifact, and never let an item's
    own label into the scores that classify it."""

    def _sb(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("surface_baseline", os.path.join(HERE, "..", "surface_baseline.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m

    def test_noise_sits_at_the_prior(self):
        S = self._sb()
        # balanced noise: accuracy's interval must cover the majority rate, AUROC near one half
        items = N._noise_battery(9002, 3, "coinword", n=300)
        r = S.baseline(items, "entity", "label", "category")
        lo, hi = r["accuracy_ci95"]
        self.assertLessEqual(lo, r["majority_accuracy"])
        self.assertLess(abs(r["auroc"] - 0.5), 0.1)

    def test_balanced_strata_cannot_be_read_from_the_design(self):
        S = self._sb()
        # exactly 20/20 per stratum, no signal: a held-out number below chance here would mean the
        # protocol, not the corpus, was being scored
        rng = random.Random(3)
        items = [{"entity": "w%d w%d" % (rng.randrange(500), rng.randrange(500)), "label": i % 2,
                  "category": "s%d" % (i // 40)} for i in range(240)]
        r = S.baseline(items, "entity", "label", "category")
        self.assertGreaterEqual(r["accuracy_ci95"][1], 0.5)
        self.assertGreater(r["auroc"], 0.4)

    def test_imbalanced_noise_earns_nothing(self):
        S = self._sb()
        # 85/15 per stratum, no signal in the text: the baseline sits at the prior and the stratum
        # label rate is reported beside it as a description, never fed to the combiner
        items = N._noise_battery(9001, 3, "coinword_imb", n=300)
        r = S.baseline(items, "entity", "label", "category")
        lo, hi = r["accuracy_ci95"]
        self.assertLessEqual(lo, r["majority_accuracy"])
        self.assertGreaterEqual(r["stratum_prior_accuracy"], 0.8)     # the prior is reported, not used
        self.assertNotIn("stratum_rate", r["features"])

    def test_artifact_is_detected_and_quantified(self):
        S = self._sb()
        rng = random.Random(7)
        items = []
        for i in range(240):
            y = i % 2
            # label-1 items are long and carry a marker token: a surface artifact a regex would find
            text = ("x%d " % rng.randrange(1000)) * (6 if y else 2) + ("zz" if y else "")
            items.append({"entity": text, "label": y, "category": "a" if i % 3 else "b"})
        r = S.baseline(items, "entity", "label", "category")
        self.assertGreater(r["accuracy"], 0.9)
        self.assertGreater(r["probe_auroc_held_out"]["length"], 0.9)
        self.assertGreater(r["auroc"], 0.95)

    def test_held_out_nb_matches_nulltest_arithmetic(self):
        S = self._sb()
        feats_tr = [["a", "b"], ["a", "c"], ["b", "c"], ["c", "d"]]
        y_tr = [1, 1, 0, 0]
        s = S._nb_train_test(feats_tr, y_tr, [["a"], ["c"], ["zzz"], ["d"]])
        # 'a': seen twice, both label-1: log(2.5/3) - log(0.5/3) = log 5
        self.assertAlmostEqual(s[0], math.log(5), places=8)
        # 'zzz' unseen and 'd' hapax: no evidence
        self.assertEqual(s[2], 0.0)
        self.assertEqual(s[3], 0.0)
        # 'c' three times, one label-1: log(1.5/3) - log(2.5/3)
        self.assertAlmostEqual(s[1], math.log(1.5 / 3) - math.log(2.5 / 3), places=8)
