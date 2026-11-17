#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Kevin Russell (Aperture / Project Forty Two)
"""render_audit --check break-tests — plant a defect, run the real script, require it to fire.

WHY THIS EXISTS. Until 2026-11-16, render_audit --check bound the page to nulltest_audit_results.json
and not to the bytes those rows describe. Flipping 40 labels in calibration-battery-v1 moved the
worst cell (lexicality/book 0.0107 -> length/firm 0.0136) but left the verdict unchanged, and
--check still printed "current" and exited 0. Editing nulltest.py or adding an orphaned correction
note also exited 0. These tests copy the script into a throwaway tree and plant each of those
defects. The clean tree must pass, so a gate that fires on everything fails too.

    python3 -m unittest tests.test_render_audit -v
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

SKELETON = """<!doctype html>
<html><body>
<p>the null-floor audit &middot; nulltest 0 &middot; 0 rows generated from
nulltest_audit_results.json by render_audit.py</p>
<table>
<thead><tr><th>benchmark</th></tr></thead>
<tbody></tbody>
</table>
</body></html>
"""


def _script():
    return os.path.join(REPO, "render_audit.py")


class RenderAuditBreakTests(unittest.TestCase):

    def setUp(self):
        self.tree = tempfile.mkdtemp(prefix="render_audit_")
        self.addCleanup(shutil.rmtree, self.tree, ignore_errors=True)
        # Site layout: audit.html sits one level above the script (HERE/../audit.html).
        self.here = os.path.join(self.tree, "verifier")
        os.makedirs(os.path.join(self.here, "batteries"))
        self._build_clean_tree()

    def _build_clean_tree(self):
        # Slim results: two of our own rows, hashes taken from the committed files so a clean
        # copy of those files (and of nulltest.py) must pass.
        with open(os.path.join(REPO, "nulltest_audit_results.json")) as f:
            src = json.load(f)
        keep = {"aperture/calibration-battery-v1", "aperture/photon-entities"}
        rows = [r for r in src["rows"] if r["name"] in keep]
        self.assertEqual(len(rows), 2, "expected both slim-fixture rows in the results file")
        doc = {k: src[k] for k in src if k != "rows"}
        doc["rows"] = rows
        doc["partial_run"] = True
        with open(os.path.join(self.here, "nulltest_audit_results.json"), "w") as f:
            json.dump(doc, f, indent=1)

        notes = {"_about": "test fixture — only keys that have a row"}
        with open(os.path.join(self.here, "audit_notes.json"), "w") as f:
            json.dump(notes, f, indent=1)

        for slug in ("calibration-battery-v1", "photon-entities"):
            shutil.copy(os.path.join(REPO, "batteries", slug + ".json"),
                        os.path.join(self.here, "batteries", slug + ".json"))
        shutil.copy(os.path.join(REPO, "nulltest.py"), os.path.join(self.here, "nulltest.py"))
        shutil.copy(_script(), os.path.join(self.here, "render_audit.py"))
        with open(os.path.join(self.tree, "audit.html"), "w", encoding="utf-8") as f:
            f.write(SKELETON)

        # Generate the page from this tree so --check starts from a current page.
        wrote = self._run([], check=False)
        self.assertEqual(wrote.returncode, 0, wrote.stdout + wrote.stderr)
        self.assertIn("wrote", wrote.stdout)

    def _run(self, args, check=True):
        cmd = [sys.executable, os.path.join(self.here, "render_audit.py")]
        if check:
            cmd.append("--check")
        cmd.extend(args)
        return subprocess.run(cmd, capture_output=True, text=True, cwd=self.here)

    def test_clean_tree_passes(self):
        """A tree whose page, battery bytes and nulltest.py match the results file exits 0."""
        r = self._run([])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("current", r.stdout)
        self.assertNotIn("ROW DESCRIBES DIFFERENT BYTES", r.stdout)
        self.assertNotIn("orphan note", r.stdout)

    def test_flipped_labels_fail(self):
        """Changing battery bytes (40 flipped labels) must fail even if the verdict would not."""
        path = os.path.join(self.here, "batteries", "calibration-battery-v1.json")
        with open(path) as f:
            data = json.load(f)
        items = data["battery"]
        self.assertGreaterEqual(len(items), 40)
        for item in items[:40]:
            item["label"] = 1 - int(item["label"])
        with open(path, "w") as f:
            json.dump(data, f)
        r = self._run([])
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ROW DESCRIBES DIFFERENT BYTES", r.stdout)
        self.assertIn("calibration-battery-v1", r.stdout)

    def test_edited_nulltest_fails(self):
        """An edited nulltest.py must fail against the results file's tool_sha256."""
        path = os.path.join(self.here, "nulltest.py")
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n# planted edit — must make --check fail\n")
        r = self._run([])
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ROW DESCRIBES DIFFERENT BYTES", r.stdout)
        self.assertIn("nulltest.py", r.stdout)

    def test_orphaned_note_fails(self):
        """A correction note with no row in the table must fail the check, not only print."""
        notes_path = os.path.join(self.here, "audit_notes.json")
        with open(notes_path) as f:
            notes = json.load(f)
        notes["planted-orphan"] = "a correction that is no longer displayed"
        with open(notes_path, "w") as f:
            json.dump(notes, f, indent=1)
        r = self._run([])
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("orphan note", r.stdout)
        self.assertIn("planted-orphan", r.stdout)

    def test_displayed_mismatches_fire_when_worst_cell_moves(self):
        """CI must reject a row whose verdict is unchanged but whose worst cell moved."""
        sys.path.insert(0, REPO)
        import render_audit
        row = {
            "name": "aperture/calibration-battery-v1",
            "n": 168,
            "cells": 34,
            "verdict": "DISQUALIFIED",
            "headline": "worst cell A* = 0.0107 in lexicality/book (n=168), family-adjusted p = 0.0107 over 34 cells",
            "worst_cell": {"astar": 0.0107, "adj_p": 0.0107, "null": "lexicality", "stratum": "book", "n": 168},
        }
        # Same verdict, n and family size; worst cell moved the way the 2026-11-16 plant did.
        report = {
            "n": 168,
            "family_size": 34,
            "verdict": "DISQUALIFIED",
            "headline": "worst cell A* = 0.0136 in length/firm (n=168), family-adjusted p = 0.0136 over 34 cells",
            "worst_cell": {"astar": 0.0136, "adj_p": 0.0136, "null": "length", "stratum": "firm", "n": 168},
        }
        diffs = render_audit.displayed_mismatches(report, row)
        self.assertTrue(any("worst_cell" in d for d in diffs), diffs)
        self.assertTrue(any("headline" in d for d in diffs), diffs)
        self.assertFalse(any("verdict:" in d for d in diffs), diffs)
        # An identical row must be clean, or the check will false-alarm.
        same = {
            "n": 168,
            "family_size": 34,
            "verdict": "DISQUALIFIED",
            "headline": row["headline"],
            "worst_cell": dict(row["worst_cell"]),
        }
        self.assertEqual(render_audit.displayed_mismatches(same, row), [])



class PublishedRowRepro(unittest.TestCase):
    """CI compares the fields the /audit page shows, not only the verdict.

    This lives in the unit-test job so it runs without a workflow-file change (GitHub
    rejects workflow updates from a token without the `workflow` scope). The batteries
    job still re-scores too; this test is what currently binds CI to n, worst cell,
    A*, adjusted p, family size and the headline.
    """

    def test_all_nine_published_rows_match_displayed_fields(self):
        sys.path.insert(0, REPO)
        import render_audit
        with open(os.path.join(REPO, "nulltest_audit_results.json")) as f:
            rows = {r["name"].split("/", 1)[-1]: r
                    for r in json.load(f)["rows"]
                    if r["name"].startswith("aperture/")}
        files = sorted(f[:-5] for f in os.listdir(os.path.join(REPO, "batteries"))
                       if f.endswith(".json"))
        self.assertTrue(files, "no batteries found — refusing to report success")
        missing = [f for f in files if f not in rows]
        self.assertEqual(missing, [], "battery on disk but absent from the published table: %s"
                         % ", ".join(missing))
        fail = []
        for f in files:
            out = subprocess.run(
                [sys.executable, os.path.join(REPO, "nulltest.py"),
                 os.path.join(REPO, "batteries", f + ".json"), "--json", "--n-perm", "600"],
                capture_output=True, text=True, timeout=900)
            self.assertTrue(out.stdout.strip(),
                            "%s: nulltest produced no JSON (exit %s): %s"
                            % (f, out.returncode, out.stderr[-400:]))
            report = json.loads(out.stdout)
            diffs = render_audit.displayed_mismatches(report, rows[f])
            fail.extend(diffs)
        self.assertEqual(fail, [], "\n".join(fail))


if __name__ == "__main__":
    unittest.main()
