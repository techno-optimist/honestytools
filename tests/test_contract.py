#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""The stable/experimental wall in contract.py, enforced. Each test names the defect it would catch."""
import contextlib, io, json, os, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import nulltest as _N  # noqa: E402,F401  (honest._load replaces these; restored in tearDownModule)
import nulltest_paired as _P  # noqa: E402,F401
_SAVED = {k: sys.modules[k] for k in ("nulltest", "nulltest_paired")}
import contract as C  # noqa: E402
import honest as H  # noqa: E402


def tearDownModule():
    # honest.py loads its own copies of nulltest; put the originals back so other test modules'
    # multiprocessing pickles still resolve to the module they imported.
    sys.modules.update(_SAVED)

# Promotion to stable is a deliberate act: changing this set must be a reviewed edit of this test.
STABLE_ALLOWLIST = {"null"}


class _Fake(C.Tool):
    summary = "test double"

    def __init__(self, name, verdict, claimed_tier, fpr, power=None):
        self.name, self._v, self._t, self._f, self._p = name, verdict, claimed_tier, fpr, power

    def run(self, *a, **k):
        return C.Verdict(self.name, self._t, self._v, "fake", evidence={"x": 1}, fpr=self._f, power=self._p)

    def selftest(self):
        return True

    def false_positive_rate(self):
        return self._f

    def replay(self, receipt):
        return self.run()


class Wall(unittest.TestCase):
    def setUp(self):
        self._saved = dict(C._REGISTRY)

    def tearDown(self):
        C._REGISTRY.clear(); C._REGISTRY.update(self._saved)

    def test_experimental_pass_is_never_green(self):
        """an experimental tool's PASS must not read as a stable green, even when it claims STABLE"""
        C.register(_Fake("fx", C.PASS, C.STABLE, {"rate": 0.01}), C.EXPERIMENTAL)
        v = C.run("fx")
        self.assertEqual(v.tier, C.EXPERIMENTAL)
        self.assertEqual(v.verdict, C.EXPERIMENTAL_NO_STABLE_VERDICT)
        self.assertEqual(v.evidence["raw_verdict"], C.PASS)
        self.assertTrue(v.headline.startswith("[experimental"))
        self.assertFalse(v.is_stable_green())

    def test_tool_cannot_claim_stable_tier(self):
        """the dispatcher, not the tool, sets the tier"""
        C.register(_Fake("fx2", C.DISQUALIFIED, C.STABLE, None), C.EXPERIMENTAL)
        self.assertEqual(C.run("fx2").tier, C.EXPERIMENTAL)

    def test_stable_pass_needs_measured_fpr(self):
        """a stable PASS with no measured FPR is not green; with one (and power) it is"""
        pw = {"n": 156, "measured_mde": 0.6}
        C.register(_Fake("s0", C.PASS, C.EXPERIMENTAL, None, power=pw), C.STABLE)
        C.register(_Fake("s1", C.PASS, C.EXPERIMENTAL, {"rate": 0.05}, power=pw), C.STABLE)
        self.assertFalse(C.run("s0").is_stable_green())
        self.assertTrue(C.run("s1").is_stable_green())
        self.assertEqual(C.run("s1").tier, C.STABLE)

    def test_stable_pass_needs_measured_power(self):
        """a stable PASS with no measured power (n < 156 or None) is underpowered and never green"""
        C.register(_Fake("p_none", C.PASS, C.STABLE, {"rate": 0.05}, power=None), C.STABLE)
        C.register(_Fake("p_small", C.PASS, C.STABLE, {"rate": 0.05},
                         power={"n": 6, "measured_mde": None, "reading": "below 156"}), C.STABLE)
        C.register(_Fake("p_ok", C.PASS, C.STABLE, {"rate": 0.05},
                         power={"n": 156, "measured_mde": 0.6}), C.STABLE)
        for name in ("p_none", "p_small"):
            v = C.run(name)
            self.assertFalse(v.is_stable_green(), name)
            self.assertEqual(v.state(), "underpowered", name)
            self.assertTrue(v.headline.startswith("[underpowered"), v.headline)
            payload = json.loads(v.to_json())
            self.assertFalse(payload["stable_green"])
            self.assertEqual(payload["state"], "underpowered")
        v_ok = C.run("p_ok")
        self.assertTrue(v_ok.is_stable_green())
        self.assertEqual(v_ok.state(), "stable_green")
        self.assertTrue(json.loads(v_ok.to_json())["stable_green"])

    def test_stable_disqualified_is_not_green(self):
        C.register(_Fake("s2", C.DISQUALIFIED, C.STABLE, {"rate": 0.05}), C.STABLE)
        self.assertFalse(C.run("s2").is_stable_green())

    def test_bad_tier_refused(self):
        with self.assertRaises(ValueError):
            C.register(_Fake("bt", C.PASS, C.STABLE, None), "beta")

    def test_unimplemented_method_refused(self):
        """register() refuses a tool whose method is still the ABC stub"""
        class Half(_Fake):
            pass
        Half.replay = C.Tool.replay
        Half.__abstractmethods__ = frozenset()
        with self.assertRaises(TypeError):
            C.register(Half("half", C.PASS, C.STABLE, None), C.STABLE)


class Registry(unittest.TestCase):
    def test_stable_set_is_the_allowlist(self):
        """nothing enters stable without a reviewed edit of STABLE_ALLOWLIST"""
        stable = {n for n, t, _ in C.registered() if t == C.STABLE}
        self.assertEqual(stable, STABLE_ALLOWLIST)

    def test_paired_and_observertest_are_experimental(self):
        self.assertEqual(C.tier_of("paired"), C.EXPERIMENTAL)
        self.assertEqual(C.tier_of("observertest"), C.EXPERIMENTAL)

    def test_every_stable_tool_passes_its_selftest_and_has_an_fpr(self):
        """'Nothing enters stable until this passes in CI' -- made a unit test"""
        for n, t, _ in C.registered():
            if t != C.STABLE:
                continue
            _, tool = C._REGISTRY[n]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(tool.selftest(), n)
            self.assertIsNotNone(tool.false_positive_rate(), n)

    def test_observertest_cannot_be_promoted(self):
        """its selftest fails; registering it stable would contradict the allowlist test"""
        _, tool = C._REGISTRY["observertest"]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(tool.selftest())

    def test_list_shows_paired_as_experimental(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(H.main(["honest", "list"]), 0)
        line = [l for l in out.getvalue().splitlines() if " paired " in l][0]
        self.assertIn("[experimental]", line)
        self.assertTrue(line.lstrip().startswith("*"))


class PairedCLI(unittest.TestCase):
    def _battery(self, d, leak):
        # seeds fixed: seed 5 leaks, seed 1 is a clean draw (a clean draw can false-fire at ~alpha)
        rows = H.PairedTool._pairs(40, 5 if leak else 1, leak)
        p = os.path.join(d, "pairs.json")
        with open(p, "w") as fh:
            json.dump(rows, fh)
        return p

    def test_honest_paired_is_stamped_experimental(self):
        """`honest paired` must go through the contract, not around it"""
        with tempfile.TemporaryDirectory() as d:
            p = self._battery(d, True)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = H.main(["honest", "paired", p, "--pair-key", "q", "--n-perm", "120"])
            v = json.loads(out.getvalue())
            self.assertEqual(v["tool"], "paired")
            self.assertEqual(v["tier"], C.EXPERIMENTAL)
            self.assertEqual(v["verdict"], C.EXPERIMENTAL_NO_STABLE_VERDICT)
            self.assertEqual(v["evidence"]["raw_verdict"], "DISQUALIFIED")
            self.assertEqual(rc, 0)
            self.assertEqual(len(v["evidence"]["paired_tool_sha256"]), 64)

    def test_honest_paired_pass_exits_nonzero(self):
        """an experimental PASS is never a green a pipeline may rely on"""
        with tempfile.TemporaryDirectory() as d:
            p = self._battery(d, False)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = H.main(["honest", "paired", p, "--pair-key", "q", "--n-perm", "120"])
            v = json.loads(out.getvalue())
            self.assertEqual(v["evidence"]["raw_verdict"], "PASS")
            self.assertEqual(rc, 1)

    def test_paired_replay_reproduces(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._battery(d, True)
            v = C.run("paired", p, "q", n_perm=120)
            _, tool = C._REGISTRY["paired"]
            r = tool.replay({"battery_path": p, "evidence": v.evidence})
            self.assertEqual(r.verdict, v.evidence["raw_verdict"])
            self.assertEqual(r.evidence["worst_cell"], v.evidence["worst_cell"])


class FprInterval(unittest.TestCase):
    def test_null_fpr_carries_the_grid_interval(self):
        """contract: 'a number with no interval is not an answer'"""
        _, tool = C._REGISTRY["null"]
        f = tool.false_positive_rate()
        if f is None:
            self.skipTest("no grid matching this nulltest.py beside the code")
        with open(os.path.join(os.path.dirname(HERE), "nulltest_grid.json")) as fh:
            g = json.load(fh)
        lo, hi = f["worst_ci95"]
        self.assertLessEqual(lo, f["worst_rate"]); self.assertLessEqual(f["worst_rate"], hi)
        self.assertEqual(f["max_ci95_upper"], max(r["ci95"][1] for r in g["grid"]))
        self.assertEqual(len(f["grid"]), len(g["grid"]))

    def test_paired_fpr_declines(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(H.main(["honest", "fpr", "paired"]), 1)
        self.assertIn("not yet measured", out.getvalue())



class NullPowerGate(unittest.TestCase):
    def test_honest_null_6_item_length_separable_is_underpowered_not_green(self):
        """6-item length-guessable battery returns PASS from nulltest at n=6, but honest null marks it underpowered and exits 1."""
        rows = [
            {"text": "a", "label": 0, "category": "s"},
            {"text": "b", "label": 0, "category": "s"},
            {"text": "c", "label": 0, "category": "s"},
            {"text": "alpha bravo charlie delta echo foxtrot", "label": 1, "category": "s"},
            {"text": "golf hotel india juliet kilo lima", "label": 1, "category": "s"},
            {"text": "mike november oscar papa quebec romeo", "label": 1, "category": "s"},
        ]
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "tiny6.json")
            with open(p, "w", encoding="utf-8") as fh:
                json.dump(rows, fh)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = H.main(["honest", "null", p, "--n-perm", "60"])
            self.assertEqual(rc, 1)
            v = json.loads(out.getvalue())
            self.assertEqual(v["verdict"], C.PASS)
            self.assertFalse(v["stable_green"])
            self.assertEqual(v["state"], "underpowered")
            self.assertEqual(v["power"]["n"], 6)
            self.assertIsNone(v["power"]["measured_mde"])
            self.assertIn("156", v["power"]["reading"])
            self.assertIn("underpowered", v["headline"])
            self.assertEqual(v["evidence"]["power"]["n"], 6)
            rp = os.path.join(d, "receipt.json")
            with open(rp, "w", encoding="utf-8") as fh:
                fh.write(out.getvalue())
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(H.main(["honest", "check", rp, p]), 0)

    def test_honest_null_156_plus_clean_is_stable_green(self):
        """A PASS at n >= 156 (smallest measured size) remains stable-green and exits 0."""
        p = os.path.join(os.path.dirname(HERE), "batteries", "prominence-people-v2.json")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = H.main(["honest", "null", p, "--n-perm", "60"])
        self.assertEqual(rc, 0)
        v = json.loads(out.getvalue())
        self.assertEqual(v["verdict"], C.PASS)
        self.assertTrue(v["stable_green"])
        self.assertEqual(v["state"], "stable_green")
        self.assertEqual(v["power"]["n"], 156)
        self.assertEqual(v["power"]["measured_mde"], 0.6)

    def test_honest_null_small_disqualified_still_exits_zero(self):
        """Low power limits detections, not disqualifications; small-n DISQUALIFIED still exits 0."""
        p = os.path.join(os.path.dirname(HERE), "batteries", "photon-entities.json")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = H.main(["honest", "null", p, "--n-perm", "60"])
        self.assertEqual(rc, 0)
        v = json.loads(out.getvalue())
        self.assertEqual(v["verdict"], C.DISQUALIFIED)
        self.assertFalse(v["stable_green"])
        self.assertEqual(v["state"], "disqualified")
        self.assertIsNone(v["power"]["measured_mde"])

if __name__ == "__main__":
    unittest.main()
