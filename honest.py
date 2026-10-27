#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Kevin Russell (Aperture / Project Forty Two)
"""honest — the toolbox entry point. One command, many tools, one contract.

    honest list                     what is in the box, and at which tier
    honest null <battery> [flags]   run the model-free surface floor; same flags as nulltest.py
    honest paired <battery> --pair-key F [flags]   paired surface floor (within-pair sign flip;
                                    EXPERIMENTAL tier: verdict stamped, never stable-green)
                                    (--text-key --label-key --stratum-key --explode --split
                                     --positive-label --n-perm --jobs --explain)
    honest check <receipt> <file>   replay a published verdict against the bytes in front of you
    honest baseline <battery> [...] the number to beat: held-out accuracy of the six probes together
    honest match <battery> --out F   repair: keep a surface-matched subset, verified on held-out halves
    honest audit [--only N] [...]   re-score the published audit table (repository checkout only)
    honest selftest [tool]          run a tool's break-test (all tools if omitted)
    honest fpr <tool>               print a tool's calibrated false-positive rate

Phase 0 of the charter: the contract is exercised on the STRONGEST tool first (`null`), reversibly,
before the weaker three are wrapped. `observertest` is registered `experimental` on purpose — to
demonstrate that the stable/experimental wall actually refuses to let it emit a stable verdict, not
to run it here. See CHARTER.md.
"""
from __future__ import annotations

import importlib.util
import os
import sys

import contract
from contract import (EXPERIMENTAL, PASS, STABLE, Tool, Verdict)

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(mod, path):
    spec = importlib.util.spec_from_file_location(mod, os.path.join(HERE, path))
    m = importlib.util.module_from_spec(spec)
    sys.modules[mod] = m          # so multiprocessing can pickle references into it
    spec.loader.exec_module(m)
    return m


# ── null: wrap the shipped surface floor in the contract ──────────────────────────────────
class NullTool(Tool):
    name = "null"
    summary = "model-free surface floor — does a benchmark measure honesty, or string shape?"

    def _nt(self):
        return _load("nulltest", "nulltest.py")

    def run(self, battery_path, n_perm=600, text_key=None, label_key=None, stratum_key=None,
            explode=None, split=None, positive_label=None, jobs=1, explain=False) -> Verdict:
        nt = self._nt()
        # Same reader, the same key sniffing and the same flags as `python3 nulltest.py`, so
        # `honest null` and the bare tool cannot disagree about what a file contains or says.
        raw = nt.read_items(battery_path)
        mapping = None
        if explode:
            mapping = nt.parse_explode(explode)
            raw = nt.explode_items(raw, mapping, split=split)
            text_key, label_key = "text", "label"
        tk, lk, sk = nt.sniff_keys(raw, text_key, label_key, stratum_key)
        if tk is None or lk is None:
            raise SystemExit("could not find a text and a label field in %s; name them with "
                             "--text-key and --label-key" % battery_path)
        items = nt.load(battery_path, tk, lk, positive_label, explode=mapping, split=split)
        tkeys = tk.split(",") if "," in tk else tk
        rep = nt.evaluate(items, tkeys, lk, sk, n_perm=n_perm, jobs=jobs)
        w = rep.get("worst_cell") or {}
        import hashlib
        with open(battery_path, "rb") as fh:
            battery_sha = hashlib.sha256(fh.read()).hexdigest()
        evidence = {"worst_cell": w, "n": rep.get("n"), "family_size": rep.get("family_size"),
                    "n_separating_cells": rep.get("n_separating_cells"),
                    "borderline": bool(rep.get("borderline")),
                    "battery": os.path.basename(battery_path), "battery_sha256": battery_sha,
                    "tool_sha256": nt._tool_sha256(), "nulltest_version": nt.VERSION,
                    "keys": {"text": tk, "label": lk, "stratum": sk}, "n_perm": rep.get("n_perm", n_perm),
                    "alpha": rep.get("alpha", 0.05), "positive_label": positive_label,
                    "explode": ({"fields": dict(mapping), "split": split} if mapping else None)}
        if explain or rep["verdict"] == "DISQUALIFIED":
            evidence["explain"] = nt.explain(items, tkeys, lk, sk, rep)
        return Verdict(
            tool=self.name, tier=STABLE, verdict=rep["verdict"], headline=rep["headline"],
            evidence=evidence, fpr=self.false_positive_rate(), replayable=True)

    def selftest(self) -> bool:
        """null's break-test: a battery separable by string shape ALONE must come back DISQUALIFIED,
        and pure noise must NOT. This is the discipline that caught every defect in the taxonomy."""
        nt = self._nt()
        import random
        r = random.Random(7)
        dirty = ([{"entity": "NCT%08d" % r.randrange(10 ** 7), "label": 0, "category": "t"}
                  for _ in range(60)]
                 + [{"entity": "".join(r.choice("bcdfg") + r.choice("aeiou") for _ in range(4)),
                     "label": 1, "category": "t"} for _ in range(60)])
        must_fire = nt.evaluate(dirty, "entity", "label", "category", n_perm=200)["verdict"]
        clean = nt.evaluate(nt._noise_battery(9001, 1, "coin"), "entity", "label", "category",
                            n_perm=200)["verdict"]
        ok = (must_fire == "DISQUALIFIED" and clean != "DISQUALIFIED")
        print("  null.selftest: planted-separable -> %s (want DISQUALIFIED), "
              "pure-noise -> %s (want not-DISQUALIFIED): %s"
              % (must_fire, clean, "PASS" if ok else "FAIL"))
        return ok

    def false_positive_rate(self) -> dict | None:
        """The calibrated grid, if a certificate that matches THIS code is present. A certificate
        that describes different code is not this tool's FPR, so it is not reported as one."""
        import hashlib
        import json
        p = os.path.join(HERE, "nulltest_grid.json")
        if not os.path.exists(p):
            return None
        try:
            with open(p) as fh:
                d = json.load(fh)
        except Exception:
            return None
        stamped = (d.get("provenance") or {}).get("tool_sha256") or d.get("tool_sha256")
        with open(os.path.join(HERE, "nulltest.py"), "rb") as fh:
            actual = hashlib.sha256(fh.read()).hexdigest()
        if stamped != actual:
            return None      # the certificate is not about this code; refuse to quote it
        rows = [r for r in (d.get("grid") or []) if isinstance(r, dict)]
        worst = max(rows, key=lambda r: (r.get("rate") or 0), default=None)
        his = [r["ci95"][1] for r in rows if isinstance(r.get("ci95"), list) and len(r["ci95"]) == 2]
        return {"worst_rate": d.get("worst_observed_rate") or d.get("worst_rate"),
                # contract: "a number with no interval is not an answer" -- the grid's own 95%
                # interval for the worst row, and the highest upper bound over every row
                "worst_row": (worst or {}).get("construction"),
                "worst_ci95": (worst or {}).get("ci95"),
                "max_ci95_upper": max(his) if his else None,
                "ceiling": d.get("ceiling"), "seeds": d.get("seeds"), "state": d.get("state"),
                "grid": [{"construction": r.get("construction"), "false_disqualified": r.get("false_disqualified"),
                          "seeds": r.get("seeds"), "rate": r.get("rate"), "ci95": r.get("ci95")} for r in rows],
                "source": "nulltest_grid.json (provenance matches this tool)"}

    def replay(self, receipt) -> Verdict:
        """null needs no signed receipt: the battery IS the receipt. Re-running run() on the same
        bytes reproduces the verdict, offline, with stdlib — which is what replay means here."""
        return self.run(receipt)


# ── paired: the within-pair sign-flip floor, registered experimental ────────────────────────
class PairedTool(Tool):
    name = "paired"
    summary = ("paired surface floor — inside a matched pair, can wording tell the labels apart? "
               "(EXPERIMENTAL — no calibrated FPR for this code yet)")

    def _np(self):
        _load("nulltest", "nulltest.py")
        return _load("nulltest_paired", "nulltest_paired.py")

    def run(self, battery_path, pair_key, n_perm=600, text_key=None, label_key=None,
            stratum_key=None, explode=None, split=None, positive_label=None, jobs=1,
            alpha=0.05) -> Verdict:
        import hashlib
        P = self._np(); nt = P.N
        # same reader and flags as `python3 nulltest_paired.py` (and nulltest.py)
        raw = nt.read_items(battery_path)
        mapping = None
        if explode:
            mapping = nt.parse_explode(explode)
            raw = nt.explode_items(raw, mapping, split=split)
            text_key, label_key = "text", "label"
        tk, lk, sk = nt.sniff_keys(raw, text_key, label_key, stratum_key)
        if tk is None or lk is None:
            raise SystemExit("could not find a text and a label field in %s; name them with "
                             "--text-key and --label-key" % battery_path)
        items = nt.load(battery_path, tk, lk, positive_label, explode=mapping, split=split)
        if not any(pair_key in it for it in items):
            raise SystemExit("no item carries the pair field %r (under --explode, use --pair-key _row)"
                             % pair_key)
        tkeys = tk.split(",") if "," in tk else tk
        rep = P.evaluate_paired(items, tkeys, lk, pair_key, sk, n_perm=n_perm, alpha=alpha, jobs=jobs)
        with open(battery_path, "rb") as fh:
            battery_sha = hashlib.sha256(fh.read()).hexdigest()
        with open(os.path.join(HERE, "nulltest_paired.py"), "rb") as fh:
            paired_sha = hashlib.sha256(fh.read()).hexdigest()
        evidence = {"worst_cell": rep.get("worst_cell"), "n_pairs": rep.get("n_pairs"),
                    "pairs_dropped_not_1v1": rep.get("pairs_dropped_not_1v1"),
                    "family_size": rep.get("family_size"), "n_perm": rep.get("n_perm", n_perm),
                    "alpha": alpha, "borderline": bool(rep.get("borderline")),
                    "battery": os.path.basename(battery_path), "battery_sha256": battery_sha,
                    "tool_sha256": nt._tool_sha256(), "paired_tool_sha256": paired_sha,
                    "nulltest_version": nt.VERSION, "paired_version": P.PAIRED_VERSION,
                    "keys": {"text": tk, "label": lk, "stratum": sk, "pair": pair_key},
                    "explode": ({"fields": dict(mapping), "split": split} if mapping else None),
                    "report": rep}
        return Verdict(tool=self.name, tier=EXPERIMENTAL, verdict=rep["verdict"],
                       headline=rep.get("headline", ""), evidence=evidence,
                       fpr=self.false_positive_rate(), replayable=True)

    @staticmethod
    def _pairs(n, seed, leak):
        import random
        r = random.Random(seed)
        w = lambda k=6: "".join(r.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(k))
        out = []
        for i in range(n):
            t = w()
            a, b = "%s %s %s" % (t, w(), w()), "%s %s %s" % (t, w(), w())
            if leak:
                b += " " + w(9)
            out += [{"text": a, "label": 0, "q": i}, {"text": b, "label": 1, "q": i}]
        return out

    def selftest(self) -> bool:
        """paired's break-test: pairs whose label-1 member is always longer must be DISQUALIFIED;
        pairs that only share a topic word (no within-pair cue) must not be."""
        P = self._np()
        must_fire = P.evaluate_paired(self._pairs(60, 2, True), "text", "label", "q", n_perm=200)["verdict"]
        clean = P.evaluate_paired(self._pairs(80, 1, False), "text", "label", "q", n_perm=200)["verdict"]
        ok = (must_fire == "DISQUALIFIED" and clean != "DISQUALIFIED")
        print("  paired.selftest: planted within-pair length cue -> %s (want DISQUALIFIED), "
              "shared-topic pairs -> %s (want not-DISQUALIFIED): %s"
              % (must_fire, clean, "PASS" if ok else "FAIL"))
        return ok

    def false_positive_rate(self) -> dict | None:
        """Not yet measured for THIS code. The 5/100 label-scrambled calibration in the benchmark
        branch ran on an earlier build (paired code inside nulltest.py), so it is not quoted here."""
        return None

    def replay(self, receipt) -> Verdict:
        """Re-run on the same bytes with the keys the receipt recorded. `receipt` is a dict with
        `battery_path` and the verdict's `evidence` (keys, explode, n_perm, alpha)."""
        ev = receipt.get("evidence") or {}
        keys = ev.get("keys") or {}
        ex = ev.get("explode") or None
        explode = ",".join("%s=%s" % kv for kv in ex["fields"].items()) if ex else None
        return self.run(receipt["battery_path"], keys.get("pair"), n_perm=ev.get("n_perm", 600),
                        text_key=None if ex else keys.get("text"),
                        label_key=None if ex else keys.get("label"),
                        stratum_key=keys.get("stratum"), explode=explode,
                        split=(ex or {}).get("split"), alpha=ev.get("alpha", 0.05))


# ── observertest: registered experimental ON PURPOSE, to exercise the wall ─────────────────
class ObservertestStub(Tool):
    name = "observertest"
    summary = "concealment/sandbagging detection (EXPERIMENTAL — fails its own kill condition today)"

    def run(self, *a, **k) -> Verdict:
        # It would return a real finding when run against a served endpoint. Here it demonstrates
        # that the wall forces even a PASS-looking result to a non-stable verdict.
        return Verdict(self.name, EXPERIMENTAL, PASS,
                       "would report register-exchangeability + domain-selectivity from output bytes",
                       evidence={"note": "fails its pre-registered kill condition (Stage B 4/5); "
                                         "see Aperture/research/tools_deep/obs/FALSE_POSITIVE_RATE.md"})

    def selftest(self) -> bool:
        print("  observertest.selftest: fails its kill condition (Stage B fired 4/5 vs <=1/5) — "
              "correctly cannot enter stable")
        return False

    def false_positive_rate(self) -> dict | None:
        return {"measured": "4 of 5 fresh-dressing runs false-fired Stage B (kill condition <=1/5)",
                "verdict": "FAILS — not shippable as written", "paths": ["many dressings", "empirical null"]}

    def replay(self, receipt) -> Verdict:
        return self.run()


contract.register(NullTool(), STABLE)
contract.register(PairedTool(), EXPERIMENTAL)
contract.register(ObservertestStub(), EXPERIMENTAL)


# ── CLI ───────────────────────────────────────────────────────────────────────────────────
def _cmd_list():
    print("honesttools — the honesty toolbox\n")
    for name, tier, summary in contract.registered():
        tag = "  " if tier == STABLE else "* "
        print("  %s%-14s [%s]  %s" % (tag, name, tier, summary))
    print("\n  * experimental: real finding, but the wall forbids it a stable-green verdict.")
    return 0


def _cmd_selftest(which):
    names = [which] if which else [n for n, _, _ in contract.registered()]
    all_ok = True
    for n in names:
        tier = contract.tier_of(n)
        _, tool = contract._REGISTRY[n]
        ok = tool.selftest()
        # An experimental tool is ALLOWED to fail its selftest — that is why it is experimental.
        # A stable tool that fails its selftest is a release-blocking failure.
        if tier == STABLE and not ok:
            all_ok = False
        elif tier == EXPERIMENTAL and not ok:
            print("      (experimental: a failing selftest is expected and keeps it out of stable)")
    return 0 if all_ok else 1


def _cmd_fpr(name):
    _, tool = contract._REGISTRY[name]
    fpr = tool.false_positive_rate()
    if fpr is None:
        # On-ethos: refuse to quote a rate we cannot verify against the running code. When the
        # calibrated grid is not installed beside the tool (a pip wheel does not bundle it yet),
        # point to the published measurement and to how a stranger reproduces it, rather than
        # printing a number that might not describe this build.
        if name != "null":
            print("  %s: no calibrated false-positive rate has been measured for this code, so none is" % name)
            print("  quoted. (contract: None means not yet measured.)")
            return 1
        print("  %s: no calibrated grid is installed beside this build, so there is no rate this" % name)
        print("  tool can verify against the code you are running — and it will not quote one it")
        print("  cannot verify. The calibrated false-positive rate for the published version is at")
        print("  https://www.honesty.tools/audit ; reproduce it yourself with:")
        print("      python3 -m nulltest --calibrate --jobs 4 --emit-grid ./grid.json")
        print("  (10 grid rows x 500 seeds at the shipped permutation count; about 26 minutes on 4 cores)")
        return 1
    import json
    print(json.dumps(fpr, indent=1))
    return 0


def main(argv=None):
    # argv=None is the console-script entry point (`honest ...`); the list form is used by tests
    # and by `python3 honest.py ...`.
    if argv is None:
        argv = sys.argv
    if len(argv) < 2 or argv[1] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd = argv[1]
    if cmd == "list":
        return _cmd_list()
    if cmd == "selftest":
        return _cmd_selftest(argv[2] if len(argv) > 2 else None)
    if cmd == "fpr":
        if len(argv) < 3:
            print("usage: honest fpr <tool>")
            return 2
        return _cmd_fpr(argv[2])
    if cmd == "check":
        if len(argv) < 4:
            print("usage: honest check <receipt.json> <battery>")
            return 2
        return _load("nulltest", "nulltest.py").check_receipt(argv[2], argv[3])
    if cmd == "paired":
        # paired surface floor (nulltest_paired.py), dispatched THROUGH THE CONTRACT so the verdict
        # carries the experimental stamp. The bare `python3 nulltest_paired.py` stays as it was.
        import argparse
        ap = argparse.ArgumentParser(prog="honest paired")
        ap.add_argument("battery")
        ap.add_argument("--pair-key", required=True)
        for k in ("--text-key", "--label-key", "--stratum-key", "--explode", "--split", "--positive-label"):
            ap.add_argument(k, default=None)
        ap.add_argument("--n-perm", type=int, default=600)
        ap.add_argument("--jobs", type=int, default=1)
        ap.add_argument("--alpha", type=float, default=0.05)
        a = ap.parse_args(argv[2:])
        v = contract.run("paired", a.battery, a.pair_key, n_perm=a.n_perm, text_key=a.text_key,
                         label_key=a.label_key, stratum_key=a.stratum_key, explode=a.explode,
                         split=a.split, positive_label=a.positive_label, jobs=a.jobs, alpha=a.alpha)
        print(v.to_json())
        # experimental: never green. A disqualification is still a real "no" (exit 0, as for
        # `honest null`); any other raw verdict exits 1.
        return 0 if v.evidence.get("raw_verdict") == "DISQUALIFIED" else 1
    if cmd == "match":
        # repair: keep a surface-matched subset, verified on held-out halves (surface_match.py)
        return _load("surface_match", "surface_match.py").main(["surface_match"] + argv[2:]) or 0
    if cmd == "baseline":
        # the number to beat: held-out accuracy of the six probes together (surface_baseline.py)
        return _load("surface_baseline", "surface_baseline.py").main(["surface_baseline"] + argv[2:]) or 0
    if cmd == "audit":
        p = os.path.join(HERE, "nulltest_audit.py")
        if not os.path.exists(p):
            print("  the audit harness ships in the repository checkout, not in the wheel:")
            print("  git clone https://github.com/techno-optimist/honestytools && python3 nulltest_audit.py")
            return 2
        return _load("nulltest_audit", "nulltest_audit.py").main(["nulltest_audit"] + argv[2:]) or 0
    if cmd in contract._REGISTRY:
        if len(argv) < 3:
            print("usage: honest %s <battery> [--text-key K] [--label-key K] [--stratum-key K] "
                  "[--explode F=L,...] [--split SEP] [--positive-label V] [--n-perm N] [--jobs N] "
                  "[--explain]" % cmd)
            return 2
        import argparse
        ap = argparse.ArgumentParser(prog="honest %s" % cmd)
        ap.add_argument("battery")
        ap.add_argument("--text-key", default=None)
        ap.add_argument("--label-key", default=None)
        ap.add_argument("--stratum-key", default=None)
        ap.add_argument("--explode", default=None)
        ap.add_argument("--split", default=None)
        ap.add_argument("--positive-label", default=None)
        ap.add_argument("--n-perm", type=int, default=600)
        ap.add_argument("--jobs", type=int, default=1)
        ap.add_argument("--explain", action="store_true")
        a = ap.parse_args(argv[2:])
        v = contract.run(cmd, a.battery, n_perm=a.n_perm, text_key=a.text_key, label_key=a.label_key,
                         stratum_key=a.stratum_key, explode=a.explode, split=a.split,
                         positive_label=a.positive_label, jobs=a.jobs, explain=a.explain)
        print(v.to_json())
        # exit 0 only on a verdict a downstream pipeline may actually rely on
        return 0 if v.is_stable_green() or v.verdict == "DISQUALIFIED" else 1
    print("unknown command %r — try: honest list" % cmd)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
