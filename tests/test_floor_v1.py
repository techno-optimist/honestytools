"""benchmark/certified FLOOR v1: unpaired status rule, and the numpy held-out backend (skipped without numpy)."""
import json, os, random, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
CERT = os.path.join(os.path.dirname(HERE), "benchmark", "certified")
sys.path.insert(0, CERT)
import certify_unpaired as U  # noqa: E402

try:
    import numpy  # noqa: F401
    HAVE_NP = True
except ImportError:
    HAVE_NP = False

OK = {"in_family_unpaired": True, "held_out_probes": True, "negation_cue_absent": True}


class UnpairedFloor(unittest.TestCase):
    def test_floor_items(self):
        self.assertEqual(U.floor_items(1000), 640)   # 2 x 320 per class binds
        self.assertEqual(U.floor_items(4507), 2254)  # 0.50 share binds

    def test_certified(self):
        self.assertEqual(U.decide(OK, 2000, 1200, 900, 500, False), ("CERTIFIED", []))

    def test_not_certified(self):
        g = dict(OK, held_out_probes=False)
        self.assertEqual(U.decide(g, 2000, 1200, 900, 500, False), ("NOT_CERTIFIED", ["held_out_probes"]))

    def test_class_floor(self):
        st, why = U.decide(OK, 2000, 1200, 300, 300, False)
        self.assertEqual(st, "NOT_CERTIFIABLE")
        self.assertEqual(why, ["source_below_floor", "kept_below_floor"])

    def test_reached_floor_with_cues(self):
        g = dict(OK, in_family_unpaired=False)
        self.assertEqual(U.decide(g, 2000, 1000, 900, 400, True),
                         ("NOT_CERTIFIABLE", ["filter_reached_floor_with_cues"]))


@unittest.skipUnless(HAVE_NP, "numpy is a benchmark-only dependency")
class NumpyHeldout(unittest.TestCase):
    def test_paired_matches_pure_python(self):
        import heldout as H, heldout_np as HN
        with open(os.path.join(CERT, "truthfulqa-binary", "certified.json")) as f:
            rows = json.load(f)[:80]
        a, b = H.evaluate(rows, n_perm=60), HN.evaluate_paired(rows, n_perm=60)
        for p in H.PROBES:
            self.assertAlmostEqual(a["probes"][p]["raw_p"], b["probes"][p]["raw_p"], places=6, msg=p)
            self.assertAlmostEqual(a["probes"][p]["adj_p"], b["probes"][p]["adj_p"], places=6, msg=p)
        self.assertEqual(a["negation_check"]["sign_test_p_two_sided"], b["negation_check"]["sign_test_p_two_sided"])

    def _items(self, plant):
        rng = random.Random(3)
        words = ["alpha", "bravo", "delta", "omega", "sigma", "kappa", "theta", "gamma"]
        out = []
        for i in range(200):
            y = i % 2
            t = " ".join(rng.choice(words) for _ in range(6))
            if plant and y:
                t += " zqx"
            out.append({"text": t, "label": y})
        return out

    def test_planted_cue_found(self):
        import heldout_np as HN
        r = HN.evaluate_unpaired(self._items(True), n_perm=100)
        self.assertTrue(r["probes"]["char_ngram"]["separates"])

    def test_no_cue_not_found(self):
        import heldout_np as HN
        r = HN.evaluate_unpaired(self._items(False), n_perm=100)
        self.assertFalse(r["probes"]["char_ngram"]["separates"])


if __name__ == "__main__":
    unittest.main()
