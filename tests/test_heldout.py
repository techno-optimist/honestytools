"""benchmark/certified/heldout.py: the held-out certification probes and the negation-cue check."""
import os, random, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "benchmark", "certified"))
import heldout as H  # noqa: E402

WORDS = "apple river stone cloud lamp tiger paper glass music field".split()


def pairs(n, leak=None, seed=0):
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        a = " ".join(rng.choice(WORDS) for _ in range(6))
        b = " ".join(rng.choice(WORDS) for _ in range(6))
        if leak == "negation":
            a = "No, " + a + " is not true"
        out.append({"right": a, "wrong": b})
    return out


class HeldOut(unittest.TestCase):
    def test_null_passes(self):
        o = H.evaluate(pairs(60), n_perm=200)
        self.assertEqual(o["verdict"], "PASS")
        self.assertFalse(o["negation_check"]["cue_present"])

    def test_negation_cue_fires(self):
        o = H.evaluate(pairs(40, leak="negation"), n_perm=200)
        self.assertTrue(o["negation_check"]["cue_present"])
        self.assertEqual(o["negation_check"]["right_more_negated"], 40)
        self.assertIn("negation", o["separating"])

    def test_deterministic(self):
        r = pairs(30, seed=3)
        self.assertEqual(H.evaluate(r, n_perm=100), H.evaluate(r, n_perm=100))


if __name__ == "__main__":
    unittest.main()
