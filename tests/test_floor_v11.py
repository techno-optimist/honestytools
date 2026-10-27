"""benchmark/certified FLOOR v1.1: within-stratum held-out scoring (skipped without numpy/scipy)."""
import os, random, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
CERT = os.path.join(os.path.dirname(HERE), "benchmark", "certified")
sys.path.insert(0, CERT)

try:
    import numpy  # noqa: F401
    import scipy  # noqa: F401
    HAVE_NP = True
except ImportError:
    HAVE_NP = False

VOCAB = {"a": ["river", "stone", "maple", "cedar", "harbor", "meadow", "willow", "canyon"],
         "b": ["vector", "kernel", "matrix", "tensor", "cipher", "socket", "buffer", "router"]}


def _items(cue, n_per=200, rates=(0.7, 0.3), seed=11):
    """Two strata with uneven base rates (70% vs 30% label 1) and stratum-specific vocabulary, so the
    pooled char n-gram mostly reads the stratum (the RAGTruth situation). With cue=True a capitalised
    word is planted in 60% vs 20% of the classes, label 1 in stratum a and label 0 in stratum b: the
    counts are exact, so pooled over both strata the cue is equally common in each class and cancels."""
    rng = random.Random(seed)
    out = []
    for s, r in zip(("a", "b"), rates):
        n1 = int(round(r * n_per))
        labs = [1] * n1 + [0] * (n_per - n1)
        hi_cls = 1 if s == "a" else 0
        for cls in (1, 0):
            idx = [i for i, l in enumerate(labs) if l == cls]
            share = 0.6 if cls == hi_cls else 0.2
            k = int(round(share * len(idx)))
            for j, _ in enumerate(idx):
                w = [rng.choice(VOCAB[s]) for _ in range(6)]
                if cue and j < k:
                    w.insert(3, "Zulu")
                out.append({"text": " ".join(w), "label": cls, "category": s})
    rng.shuffle(out)
    return out


@unittest.skipUnless(HAVE_NP, "numpy/scipy are benchmark-only dependencies")
class WithinStratum(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import heldout_strat as HS
        cls.HS = HS
        cls.cued = HS.evaluate_unpaired_v11(_items(True), n_perm=300, stratum_key="category")
        cls.clean = HS.evaluate_unpaired_v11(_items(False), n_perm=300, stratum_key="category")

    def test_cue_cancels_in_pooled_count(self):
        it = _items(True)
        for lab in (0, 1):
            self.assertEqual(sum(1 for r in it if r["label"] == lab and "Zulu" in r["text"]), 96)

    def test_pooled_misses_planted_cue(self):
        p = self.cued["pooled"]
        self.assertEqual(p["verdict"], "PASS", p["headline"])
        self.assertFalse(p["borderline"], p["headline"])

    def test_within_catches_planted_cue(self):
        w = self.cued["within"]
        self.assertEqual(w["verdict"], "DISQUALIFIED", w["headline"])
        self.assertIn("caps", w["separating"])
        a = w["probes"]["caps"]["auroc_by_stratum"]
        self.assertGreater(a["a"], 0.6)   # opposite directions, both caught: A* is taken per stratum
        self.assertLess(a["b"], 0.4)
        self.assertFalse(self.HS.gate(self.cued))
        self.assertEqual(self.HS.worst(self.cued)["family"], "within")

    def test_no_cue_passes_both(self):
        for f in ("pooled", "within"):
            self.assertEqual(self.clean[f]["verdict"], "PASS", self.clean[f]["headline"])
        self.assertTrue(self.HS.gate(self.clean))

    def test_pooled_half_is_v1(self):
        import heldout_np as HN
        it = _items(True)
        v1 = HN.evaluate_unpaired(it, n_perm=300, stratum_key="category")
        self.assertEqual(v1, self.cued["pooled"])

    def test_single_stratum_within_equals_pooled(self):
        it = [dict(r, category="one") for r in _items(True)]
        r = self.HS.evaluate_unpaired_v11(it, n_perm=100, stratum_key="category")
        for p in self.HS.PROBES:
            self.assertEqual(r["within"]["probes"][p]["astar"], r["pooled"]["probes"][p]["astar"], p)
            self.assertEqual(r["within"]["probes"][p]["adj_p"], r["pooled"]["probes"][p]["adj_p"], p)

    def test_one_class_stratum_is_skipped(self):
        it = _items(False) + [{"text": "lone item %d" % i, "label": 1, "category": "c"} for i in range(5)]
        r = self.HS.evaluate_unpaired_v11(it, n_perm=50, stratum_key="category")
        self.assertEqual(r["within"]["strata_skipped_one_class"], ["c"])
        self.assertEqual(r["within"]["n_items"], 400)


if __name__ == "__main__":
    unittest.main()
