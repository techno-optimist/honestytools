"""benchmark/certified: the FLOOR status rule (NOT_CERTIFIABLE floor) and option-A negation balancing."""
import os, random, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "benchmark", "certified"))
import certify as C  # noqa: E402
import balance_negation as B  # noqa: E402

OK = {"in_family_paired": True, "in_family_unpaired": True, "held_out_probes": True, "negation_cue_absent": True}


class Floor(unittest.TestCase):
    def test_power_floor_is_320(self):
        self.assertEqual(C.MIN_KEPT_PAIRS, 320)
        self.assertEqual(C.power_floor(k=1), 199)
        self.assertEqual(C.power_floor(p1=0.65), 142)

    def test_floor_for(self):
        self.assertEqual(C.floor_for(790), 395)     # share binds
        self.assertEqual(C.floor_for(400), 320)     # power binds
        self.assertEqual(C.floor_for(641), 321)

    def test_certified(self):
        self.assertEqual(C.decide(OK, 790, 500, False), ("CERTIFIED", []))

    def test_not_certified_lists_failed_gates(self):
        g = dict(OK, held_out_probes=False)
        self.assertEqual(C.decide(g, 790, 491, False), ("NOT_CERTIFIED", ["held_out_probes"]))

    def test_not_certifiable(self):
        g = dict(OK, in_family_paired=False)
        self.assertEqual(C.decide(g, 790, 405, True), ("NOT_CERTIFIABLE", ["filter_reached_floor_with_cues"]))
        self.assertEqual(C.decide(OK, 790, 394, False)[0], "NOT_CERTIFIABLE")
        self.assertIn("source_below_floor", C.decide(OK, 200, 200, False)[1])

    def test_clean_at_floor_is_still_certified(self):
        # reaching the floor only matters if cues are still there
        self.assertEqual(C.decide(OK, 790, 395, True)[0], "CERTIFIED")


class Balance(unittest.TestCase):
    def rows(self, seed=0):
        rng = random.Random(seed)
        out = []
        for i in range(300):
            cat = "c%d" % (i % 3)
            k = rng.random()
            r, w = "the sky is blue", "the sea is green"
            if k < 0.5:
                r = "it is not true that " + r
            elif k < 0.6:
                w = "it is not true that " + w
            out.append({"right": r, "wrong": w, "cat": cat})
        return out

    def test_balanced_within_category_and_deterministic(self):
        rows = self.rows()
        kept, rep = B.balance(rows)
        for cat, v in rep.items():
            sub = [r for r in kept if r["cat"] == cat]
            d = [(B.neg(r["right"]) > B.neg(r["wrong"])) - (B.neg(r["right"]) < B.neg(r["wrong"])) for r in sub]
            self.assertEqual(d.count(1), d.count(-1))
            self.assertEqual(d.count(1), v["kept_each_side"])
            self.assertEqual(d.count(0), v["ties"])
        self.assertEqual(B.balance(rows)[0], kept)
        self.assertTrue(all(r in rows for r in kept))   # selection only, no rewriting


if __name__ == "__main__":
    unittest.main()
