#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Kevin Russell (Aperture / Project Forty Two)
"""claim_gate break-tests — plant a defect, run the real gate script, require it to fire.

WHY THIS EXISTS. claim_gate matched elements with a non-overlapping regex and then kept only the
"innermost" ones. When a <div> was matched first, a <b> inside it was never emitted as its own
element, so the div was dropped for having a figure-bearing child AND the child was never checked.
On the live /evidence page "17 of 18", "0.967" and "0.742" sat inside such <b> tags, and the gate
reported "0 failures" without having looked at them. Rule 2 had the same hole: a retracted claim
inside a <b> could render without its withdrawal and nothing fired.

Every test here runs the gate as a subprocess against a throwaway tree, the same way it runs on the
site, and asserts on what it prints. Each defect test has a matching annotated case, so a gate that
fires on everything fails too.

    python3 -m unittest test_claim_gate        # or: pytest test_claim_gate.py
    CLAIM_GATE_SCRIPT=/path/to/claim_gate.py   # to test a copy elsewhere
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def _gate_script():
    env = os.environ.get("CLAIM_GATE_SCRIPT")
    if env:
        return env
    for cand in (os.path.join(HERE, "claim_gate.py"),                               # honesttools
                 os.path.join(HERE, "..", "web", "static", "verifier", "claim_gate.py")):  # observatory
        if os.path.exists(cand):
            return os.path.abspath(cand)
    raise SystemExit("test_claim_gate: cannot find claim_gate.py — set CLAIM_GATE_SCRIPT")


REGISTRY = {"entries": [
    {"id": "live-claim", "status": "grounded-empirical"},
    {"id": "dead-claim", "status": "falsified-negative"},
    {"id": "probe-transfer-universal-axes", "status": "grounded-empirical"},
    {"id": "c7-affine-portability-decisive-negative", "status": "grounded-empirical"},
    {"id": "prominence-people-v2-surface-blind-battery", "status": "grounded-empirical"},
]}

HEAD = "<!doctype html>\n<html>\n<head>\n<title>t</title>\n</head>\n<body>\n"
TAIL = "\n</body>\n</html>\n"

# One page per case. The gate refuses to run on fewer than five pages, so the set is also the
# denominator: every page below must be read, or a test fails.
PAGES = {
    # the defect as it sat on /evidence: a figure in <b> inside a <div>, unannotated
    "nested_unannotated": '<div class="bl-txt">The battery separated on <b>17 of 18 cells</b>.</div>',
    # the same shape, annotated on the element that carries the figure
    "nested_annotated": '<div class="bl-txt">The battery separated on <b data-claim="unregistered">16 of 18 cells</b>.</div>',
    # rule 2 through the same hole: a dead claim rendered without its withdrawal
    "nested_retracted": '<div class="bl-txt">Headline: <b data-claim="dead-claim">0 of 36 fabricated</b>.</div>',
    # ...and with its withdrawal shown, which must pass
    "nested_retracted_disclosed": '<div class="bl-txt">Headline: <b data-claim="dead-claim"><s>0 of 36</s> withdrawn</b>.</div>',
    # a wrapper's id does not silently cover a child figure: the child is WRAPPER-CLAIM,
    # not annotated, and the warning names the wrapper line
    "wrapper": '<div class="card" data-claim="live-claim">Summary <b>AUROC 0.951</b></div>',
    # P56 (d): a dead id on the wrapper must fail RETRACTED-NOT-DISCLOSED for the child figure
    "wrapper_retracted": '<div class="card" data-claim="dead-claim">Summary <b>AUROC 0.947</b></div>',
    # disclosure is judged on the ancestor's inner HTML, not only the child's
    "wrapper_retracted_disclosed": '<div class="card" data-claim="dead-claim">Summary <b>AUROC 0.947</b> withdrawn</div>',
    # a child with its own id must not inherit the wrapper's (even a dead one)
    "child_own_id": '<div class="card" data-claim="dead-claim">Summary <b data-claim="live-claim">AUROC 0.947</b></div>',
    # one wrapper id over sibling figures still warns each child (does not silently annotate both)
    "sibling_wrapper": '<div class="nums" data-claim="live-claim"><div class="v">0.996</div><div class="v">0.515</div></div>',
    # a parent's own figure must still be checked when a child also carries one
    "parent_own_figure": '<p>Rate 4.64% and <b data-claim="unregistered">116 of 2,500</b> runs.</p>',
    "parent_own_figure_annotated": '<p data-claim="live-claim">Rate 4.64% and <b data-claim="unregistered">116 of 2,500</b> runs.</p>',
    # positive control on the flat shape the old gate already handled
    "flat": '<p>A flat figure 0.967 here.</p>\n<p data-claim="live-claim">An annotated 0.912.</p>',
    # markup inside a comment is not published and must not be read as a claim
    "commented": '<!-- <p>an old draft said 0.999</p> -->\n<p>No figures here.</p>',
}

FAIL_LINE = re.compile(r"^FAIL\s+(\S+)\s+(\S+?):(\d+)\s+(.*)$")


class ClaimGateBreakTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="claim_gate_test_")
        static = os.path.join(cls.tmp, "static")
        os.makedirs(os.path.join(static, "verifier"))
        shutil.copy(_gate_script(), os.path.join(static, "verifier", "claim_gate.py"))
        cls.lines = {}
        for name, body in PAGES.items():
            src = HEAD + body + TAIL
            with open(os.path.join(static, name + ".html"), "w", encoding="utf-8") as fh:
                fh.write(src)
            cls.lines[name] = src
        reg = os.path.join(cls.tmp, "registry.json")
        with open(reg, "w") as fh:
            json.dump(REGISTRY, fh)
        env = dict(os.environ, CLAIM_GATE_REGISTRY=reg)
        out = subprocess.run([sys.executable, os.path.join(static, "verifier", "claim_gate.py"),
                              "--strict"], capture_output=True, text=True, env=env, timeout=120)
        cls.stdout = out.stdout + out.stderr
        cls.code = out.returncode
        cls.fails = [FAIL_LINE.match(l).groups() for l in out.stdout.splitlines() if FAIL_LINE.match(l)]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _fails(self, page, kind=None):
        return [f for f in self.fails if f[1] == page + ".html" and (kind is None or f[0] == kind)]

    def _line_of(self, page, needle):
        src = self.lines[page]
        return src[:src.index(needle)].count("\n") + 1

    # -- the gate ran at all ------------------------------------------------------------------

    def test_gate_ran_and_failed_overall(self):
        self.assertNotIn("refusing to run", self.stdout, self.stdout)
        self.assertEqual(self.code, 1, "planted defects must make --strict exit 1\n" + self.stdout)

    # -- the defect: a figure nested in <b> inside a <div> --------------------------------------

    def test_nested_unannotated_figure_is_reported(self):
        hits = self._fails("nested_unannotated", "UNANNOTATED")
        self.assertEqual(len(hits), 1, self.stdout)
        self.assertIn("17 of 18", hits[0][3])

    def test_nested_annotated_figure_passes(self):
        self.assertEqual(self._fails("nested_annotated"), [], self.stdout)

    def test_nested_retracted_claim_must_render_its_withdrawal(self):
        hits = self._fails("nested_retracted", "RETRACTED-NOT-DISCLOSED")
        self.assertEqual(len(hits), 1, self.stdout)
        self.assertEqual(self._fails("nested_retracted_disclosed"), [], self.stdout)

    # -- no wrapper double-count, no parent blind spot ------------------------------------------

    def test_wrapper_id_does_not_cover_child_figure(self):
        hits = self._fails("wrapper", "WRAPPER-CLAIM")
        self.assertEqual(len(hits), 1, self.stdout)
        self.assertIn("0.951", hits[0][3])
        self.assertIn("wrapper", hits[0][3])
        self.assertEqual(self._fails("wrapper", "UNANNOTATED"), [], self.stdout)

    def test_dead_wrapper_id_fails_retracted_not_disclosed(self):
        hits = self._fails("wrapper_retracted", "RETRACTED-NOT-DISCLOSED")
        self.assertEqual(len(hits), 1, self.stdout)
        self.assertIn("dead-claim", hits[0][3])
        self.assertEqual(self._fails("wrapper_retracted_disclosed", "RETRACTED-NOT-DISCLOSED"), [],
                         self.stdout)

    def test_child_with_own_id_does_not_inherit_wrapper(self):
        self.assertEqual(self._fails("child_own_id", "RETRACTED-NOT-DISCLOSED"), [], self.stdout)
        self.assertEqual(self._fails("child_own_id", "WRAPPER-CLAIM"), [], self.stdout)

    def test_sibling_wrapper_still_warns_each_child(self):
        hits = self._fails("sibling_wrapper", "WRAPPER-CLAIM")
        self.assertEqual(len(hits), 2, self.stdout)
        detail = " ".join(h[3] for h in hits)
        self.assertIn("0.996", detail)
        self.assertIn("0.515", detail)
        self.assertEqual(self._fails("sibling_wrapper", "UNANNOTATED"), [], self.stdout)

    def test_parent_own_figure_is_checked(self):
        hits = self._fails("parent_own_figure", "UNANNOTATED")
        self.assertEqual(len(hits), 1, self.stdout)
        self.assertIn("4.64%", hits[0][3])
        self.assertNotIn("116 of 2,500", hits[0][3], "the child's annotated figure is not the parent's")
        self.assertEqual(self._fails("parent_own_figure_annotated"), [], self.stdout)

    # -- controls ---------------------------------------------------------------------------------

    def test_flat_control_fires_and_annotated_flat_passes(self):
        hits = self._fails("flat", "UNANNOTATED")
        self.assertEqual(len(hits), 1, self.stdout)
        self.assertIn("0.967", hits[0][3])

    def test_commented_out_markup_is_not_a_claim(self):
        self.assertEqual(self._fails("commented"), [], self.stdout)

    def test_reported_line_is_the_source_line(self):
        hits = self._fails("nested_unannotated", "UNANNOTATED")
        self.assertEqual(len(hits), 1, self.stdout)
        self.assertEqual(int(hits[0][2]), self._line_of("nested_unannotated", "<b>17 of 18"), self.stdout)



# Live /certificate and /evidence markup as it stood before the P56 attribute edits:
# the registry id sits on the wrapper, not on the value element that owns the numeral.
HEADLINE_PAGE = """
<div class="nums" data-claim="probe-transfer-universal-axes">
  <div class="num"><div class="v">0.996<small>vs 0.993 native</small></div><div class="c">ported fidelity. <b>G1 clears.</b></div></div>
  <div class="num" data-claim="c7-affine-portability-decisive-negative"><div class="v">0.515</div><div class="c" data-claim="c7-affine-portability-decisive-negative">the random-orthogonal null, dead.</div></div>
  <div class="num" data-claim="c7-affine-portability-decisive-negative"><div class="v">0.947<small>vs 0.911 native</small></div><div class="c" data-claim="c7-affine-portability-decisive-negative">a reading direction, rotated.</div></div>
</div>
<div class="bl-row" data-claim="prominence-people-v2-surface-blind-battery">
  <div class="bl-what">an AUROC of 1.000<br>WAS 0.9997</div>
  <div class="bl-txt">A script printed the rounded figure.</div>
</div>
"""

FILLER = "<p>No figures on this page.</p>"


def _write_tree(root, pages, registry):
    static = os.path.join(root, "static")
    os.makedirs(os.path.join(static, "verifier"))
    shutil.copy(_gate_script(), os.path.join(static, "verifier", "claim_gate.py"))
    for name, body in pages.items():
        with open(os.path.join(static, name + ".html"), "w", encoding="utf-8") as fh:
            fh.write(HEAD + body + TAIL)
    reg = os.path.join(root, "registry.json")
    with open(reg, "w") as fh:
        json.dump(registry, fh)
    return static, reg


def _run_gate(static, registry, args):
    env = dict(os.environ, CLAIM_GATE_REGISTRY=registry)
    return subprocess.run([sys.executable, os.path.join(static, "verifier", "claim_gate.py"), *args],
                          capture_output=True, text=True, env=env, timeout=120)


class ClaimGateRetractAndHeadlineTests(unittest.TestCase):
    """P56: --retract lists child numerals and WRAPPER-CLAIM figures; the four headline
    numbers on a /certificate+/evidence-shaped page are reported against their registry ids.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="claim_gate_retract_")
        pages = {
            "wrapped": '<div class="card" data-claim="live-claim">Summary <b>AUROC 0.951</b></div>',
            "headlines": HEADLINE_PAGE,
            "plain": FILLER,
            "plain2": FILLER,
            "plain3": FILLER,
        }
        cls.static, cls.reg = _write_tree(cls.tmp, pages, REGISTRY)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_retract_lists_child_numerals_and_wrapper_claim(self):
        out = _run_gate(self.static, self.reg, ["--retract", "live-claim"])
        text = out.stdout + out.stderr
        self.assertEqual(out.returncode, 0, text)
        self.assertIn("0.951", text)
        self.assertIn("WRAPPER-CLAIM", text)

    def test_four_headline_numbers_are_reported(self):
        # The four number-level claims: 0.996, 0.515, 0.947, and the evidence 0.9997
        # (FIGURE does not match a bare 1.000; the element is found via 0.9997).
        cases = [
            ("probe-transfer-universal-axes", ["0.996"]),
            ("c7-affine-portability-decisive-negative", ["0.515", "0.947"]),
            ("prominence-people-v2-surface-blind-battery", ["0.9997"]),
        ]
        missing = []
        for cid, nums in cases:
            out = _run_gate(self.static, self.reg, ["--retract", cid])
            text = out.stdout + out.stderr
            self.assertEqual(out.returncode, 0, text)
            for n in nums:
                if n not in text:
                    missing.append((cid, n, text))
        self.assertEqual(missing, [], "claim_gate --retract did not report the headline numerals:\n" +
                         "\n".join("%s missing %s\n%s" % (c, n, t) for c, n, t in missing))



def _filler_pages(n=5, prefix="plain"):
    return {"%s%d" % (prefix, i): FILLER for i in range(n)}


def _write_p82_tree(root, pages, registry, batteries=None, notes=None, nulltest_payload=None):
    """A throwaway tree with optional batteries, audit_notes, and a stub nulltest."""
    static, reg = _write_tree(root, pages, registry)
    verifier = os.path.join(static, "verifier")
    if batteries:
        bdir = os.path.join(verifier, "batteries")
        os.makedirs(bdir, exist_ok=True)
        for name, body in batteries.items():
            with open(os.path.join(bdir, name), "w", encoding="utf-8") as fh:
                json.dump(body, fh)
    if notes is not None:
        with open(os.path.join(verifier, "audit_notes.json"), "w", encoding="utf-8") as fh:
            json.dump(notes, fh)
    if nulltest_payload is not None:
        stub = os.path.join(verifier, "nulltest.py")
        with open(stub, "w", encoding="utf-8") as fh:
            fh.write("#!/usr/bin/env python3\nimport json, sys\n"
                     "print(json.dumps(%s))\n" % json.dumps(nulltest_payload))
        os.chmod(stub, 0o755)
    return static, reg


def _p82_pages(body, extras=None):
    pages = _filler_pages()
    pages["p0"] = body
    if extras:
        pages.update(extras)
    return pages


class ClaimGateP82Tests(unittest.TestCase):
    """P82: closed status vocabulary, provisional labels, disclosed stale self-reports."""

    def _run(self, pages, registry, args=None, batteries=None, notes=None, nulltest_payload=None):
        tmp = tempfile.mkdtemp(prefix="claim_gate_p82_")
        self.addCleanup(shutil.rmtree, tmp, True)
        static, reg = _write_p82_tree(tmp, pages, registry, batteries, notes, nulltest_payload)
        out = _run_gate(static, reg, args or [])
        text = out.stdout + out.stderr
        fails = [FAIL_LINE.match(l).groups() for l in out.stdout.splitlines() if FAIL_LINE.match(l)]
        return out.returncode, text, fails

    def _reg(self, status, cid="planted-id"):
        entry = {"id": cid}
        if status is not None:
            entry["status"] = status
        return {"entries": [entry, {"id": "live-claim", "status": "grounded-empirical"}]}

    def _figure(self, cid="planted-id", qualifier=""):
        extra = (" " + qualifier) if qualifier else ""
        return '<div data-claim="%s">transfer AUROC 0.947 across providers%s</div>' % (cid, extra)

    def test_retracted_title_case_fails_when_undisclosed(self):
        code, text, fails = self._run(_p82_pages(self._figure()), self._reg("Retracted"))
        self.assertEqual(code, 1, text)
        self.assertEqual([f[0] for f in fails], ["RETRACTED-NOT-DISCLOSED"], text)

    def test_retracted_trailing_space_fails_when_undisclosed(self):
        code, text, fails = self._run(_p82_pages(self._figure()), self._reg("retracted "))
        self.assertEqual(code, 1, text)
        self.assertEqual([f[0] for f in fails], ["RETRACTED-NOT-DISCLOSED"], text)

    def test_falsified_negative_space_separator_fails_when_undisclosed(self):
        code, text, fails = self._run(_p82_pages(self._figure()), self._reg("falsified negative"))
        self.assertEqual(code, 1, text)
        self.assertEqual([f[0] for f in fails], ["RETRACTED-NOT-DISCLOSED"], text)

    def test_missing_status_is_unknown_status(self):
        code, text, fails = self._run(_p82_pages(self._figure()), self._reg(None))
        self.assertEqual(code, 1, text)
        self.assertEqual([f[0] for f in fails], ["UNKNOWN-STATUS"], text)
        self.assertIn("missing", fails[0][3], text)

    def test_empty_status_is_unknown_status(self):
        code, text, fails = self._run(_p82_pages(self._figure()), self._reg(""))
        self.assertEqual(code, 1, text)
        self.assertEqual([f[0] for f in fails], ["UNKNOWN-STATUS"], text)

    def test_superseded_undisclosed_fails_and_does_not_pass_silently(self):
        code, text, fails = self._run(_p82_pages(self._figure()), self._reg("superseded"))
        self.assertEqual(code, 1, text)
        self.assertEqual([f[0] for f in fails], ["RETRACTED-NOT-DISCLOSED"], text)

    def test_superseded_disclosed_passes(self):
        body = '<div data-claim="planted-id"><s>AUROC 0.947</s> superseded</div>'
        code, text, fails = self._run(_p82_pages(body), self._reg("superseded"))
        self.assertEqual(fails, [], text)
        self.assertEqual(code, 0, text)
        self.assertIn("dead", text)
        self.assertIn("superseded", text)

    def test_unknown_status_corrected_fails_closed(self):
        code, text, fails = self._run(_p82_pages(self._figure()), self._reg("corrected"))
        self.assertEqual(code, 1, text)
        self.assertEqual([f[0] for f in fails], ["UNKNOWN-STATUS"], text)
        self.assertIn("corrected", fails[0][3], text)

    def test_candidate_without_qualifier_warns_and_fails_under_strict(self):
        pages, registry = _p82_pages(self._figure()), self._reg("candidate")
        code, text, fails = self._run(pages, registry)
        self.assertEqual(code, 0, text)
        self.assertEqual(fails, [], text)
        self.assertIn("PROVISIONAL-NOT-LABELLED", text)
        self.assertIn("provisional figures not labelled", text)
        code_s, text_s, fails_s = self._run(pages, registry, args=["--strict"])
        self.assertEqual(code_s, 1, text_s)
        self.assertEqual([f[0] for f in fails_s], ["PROVISIONAL-NOT-LABELLED"], text_s)

    def test_candidate_with_qualifier_is_labelled_in_output(self):
        code, text, fails = self._run(
            _p82_pages(self._figure(qualifier="(candidate)")), self._reg("candidate"))
        self.assertEqual(code, 0, text)
        self.assertEqual(fails, [], text)
        self.assertNotIn("PROVISIONAL-NOT-LABELLED", text)
        self.assertIn("provisional figures (labelled)", text)
        self.assertIn("planted-id", text)
        self.assertIn("candidate", text)

    def test_stale_self_report_with_matching_audit_note_is_disclosed(self):
        batteries = {"calibration-battery-v1.json": {
            "items": [],
            "surface_floor": {"cells": 18, "cells_separating": 16},
        }}
        notes = {"aperture/calibration-battery-v1":
                 "RE-SCORED under v1.4: 15 of 34 cells separate (16 of 18 under v1.2)."}
        payload = {"version": "1.4", "n_separating_cells": 15, "verdict": "DISQUALIFIED"}
        code, text, fails = self._run(
            _filler_pages(), self._reg("grounded-empirical", "live-claim"),
            batteries=batteries, notes=notes, nulltest_payload=payload)
        self.assertEqual(fails, [], text)
        self.assertEqual(code, 0, text)
        self.assertIn("ARTIFACT-SUPERSEDED-DISCLOSED", text)
        self.assertIn("16", text)
        self.assertNotIn("FAIL  ARTIFACT-SELF-REPORT", text)

    def test_stale_self_report_without_note_still_fails(self):
        batteries = {"calibration-battery-v1.json": {
            "items": [],
            "surface_floor": {"cells": 18, "cells_separating": 16},
        }}
        payload = {"version": "1.4", "n_separating_cells": 15, "verdict": "DISQUALIFIED"}
        code, text, fails = self._run(
            _filler_pages(), self._reg("grounded-empirical", "live-claim"),
            batteries=batteries, notes={}, nulltest_payload=payload)
        self.assertEqual(code, 1, text)
        self.assertEqual([f[0] for f in fails], ["ARTIFACT-SELF-REPORT"], text)
        self.assertNotIn("ARTIFACT-SUPERSEDED-DISCLOSED", text)

    def test_current_version_mismatch_fails_even_with_a_note(self):
        batteries = {"calibration-battery-v1.json": {
            "items": [],
            "surface_floor": {"cells": 18, "cells_separating": 16, "nulltest_version": "1.4"},
        }}
        notes = {"aperture/calibration-battery-v1": "16 of 18 under v1.4, but the tool now prints 15."}
        payload = {"version": "1.4", "n_separating_cells": 15, "verdict": "DISQUALIFIED"}
        code, text, fails = self._run(
            _filler_pages(), self._reg("grounded-empirical", "live-claim"),
            batteries=batteries, notes=notes, nulltest_payload=payload)
        self.assertEqual(code, 1, text)
        self.assertEqual([f[0] for f in fails], ["ARTIFACT-SELF-REPORT"], text)



if __name__ == "__main__":
    unittest.main()
