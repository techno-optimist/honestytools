#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Kevin Russell (Aperture / Project Forty Two)
"""claim_gate — no numeral ships on a public page without something behind it.

WHY THIS EXISTS. On 2026-07-25 we retracted a headline and corrected it on two pages. It stayed
live on a third for two days. A census then found 58 surviving problems across eight pages, and the
cause was one thing, not fifty:

    we retract CLAIMS, but the site publishes NUMBERS, and nothing binds the two.

The same measurement travelled as "cross-provider transfer", "language-invariant off-distribution
detection", and "familiarity" — so grepping the retracted claim found nothing, and only grepping
the DIGITS found all four copies. "1.000" appeared four times on one page meaning three different
things. A fix landed on two copies of a field and missed a third, which kept rendering
"undefined real / undefined fabricated" on a live console.

Retraction was treated as an edit to prose. The unit that actually propagates is the numeral.

WHAT THIS CHECKS

  1. UNANNOTATED FIGURES.  Any element whose text carries a benchmark-shaped numeral must declare
     data-claim="<registry-id>". Under --strict this rule fails CLOSED — a new unannotated number
     is a failure, so coverage cannot silently rot. Without --strict (the default) it only WARNS and
     prints the count, so a default run exiting 0 does not mean every figure is annotated. It is the
     load-bearing rule; the rest only work on what it forces to be labelled.

  2. RETRACTED SOURCES.  Registry statuses are normalised (lowercase, strip, '_'/' ' -> '-') and
     classified against a closed allowlist: live (grounded-empirical, DOI, proven-no-doi,
     machine-verified-nogo), provisional (candidate, conjecture-open), and dead
     (falsified-negative, retracted, withdrawn, superseded, deprecated). superseded and
     deprecated are treated as dead so a replaced result cannot look live; a page may also
     declare itself superseded with name="claim-gate" content="superseded" and is then skipped
     with a visible note. Any other or missing status on a cited id is UNKNOWN-STATUS and fails
     in both modes. A dead entry must itself render the withdrawal — a <s> strike or the word
     "withdrawn", "retracted", "superseded" or "deprecated" — so a dead number cannot appear as
     a live one. A provisional entry must render a qualifier (candidate, preliminary, open,
     conjecture, or not established) in the element or its citing wrapper; that is a warning
     by default and a failure under --strict. The gate prints cited-id status counts and
     labels provisional figures in its output.

  3. DISQUALIFIED BATTERIES.  If data-battery names a battery that nulltest DISQUALIFIES, the
     element must say DISQUALIFIED. A score measured on a battery that fails the floor is not
     wrong, it is uninterpretable, and the page has to say so.

  4. SELF-REPORTING ARTIFACTS.  A battery JSON's surface_floor block must match what nulltest
     computes on that same file. calibration-battery-v1.json hand-carried cells_separating: 17
     while the tool printed 16 — and our own retraction quoted the artifact. An artifact must never
     be able to assert its own verdict. A mismatch whose block has no nulltest_version, or an
     older one, is ARTIFACT-SUPERSEDED-DISCLOSED (informational, exit unaffected) when
     audit_notes.json for that battery states the block's count (for example "16 of 18 under
     v1.2"); otherwise it is still ARTIFACT-SELF-REPORT. New self-report blocks must carry
     nulltest_version.

Rule 4 alone would have caught the 17-vs-16. Rule 1 would have caught all four pages.

    An unannotated figure whose nearest tracked ancestor with a registry id is reported as
    WRAPPER-CLAIM, not plain UNANNOTATED. Rules 2-3 then run on it with that ancestor's id
    (disclosure is judged on the ancestor's inner HTML). A child with its own id does not
    inherit, so one wrapper id cannot silently cover unrelated sibling numbers. --retract
    collects numerals from the citing element's own text and from WRAPPER-CLAIM figures bound
    to that id, and lists those figures.

    python3 claim_gate.py                 # check the served tree (rule 1 warns only)
    python3 claim_gate.py --strict        # same, but unannotated, WRAPPER-CLAIM, and unlabelled
                                          # provisional figures fail
    python3 claim_gate.py --retract <id>  # every file:line citing a registry id, plus every
                                          # distinct numeral in those elements and in WRAPPER-CLAIM
                                          # figures bound to the id, then every unannotated
                                          # occurrence of those numerals tree-wide
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from html.parser import HTMLParser

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.dirname(HERE)
def _find_registry():
    """Walk up for results_registry/results.json, and allow CLAIM_GATE_REGISTRY to override.

    A fixed number of dirname() calls assumed one repo layout. The deploy tree has no
    results_registry at all, so the gate refused to run there — correct behaviour from the guard,
    but useless as a pre-deploy check, which is exactly where it needs to run.
    """
    env = os.environ.get("CLAIM_GATE_REGISTRY")
    if env:
        return env
    d = STATIC
    for _ in range(8):
        cand = os.path.join(d, "results_registry", "results.json")
        if os.path.exists(cand):
            return cand
        nd = os.path.dirname(d)
        if nd == d:
            break
        d = nd
    # the deploy tree is a sibling checkout of the same repo; fall back to the canonical one
    home = os.path.expanduser("~/Desktop/cultural-soliton-observatory/results_registry/results.json")
    return home if os.path.exists(home) else cand


REGISTRY = _find_registry()
BATTERIES = os.path.join(HERE, "batteries")

# A benchmark-shaped numeral. Deliberately narrow: version strings, years, byte counts and prices
# are not claims about measurement, and sweeping them in would train people to ignore this gate.
FIGURE = re.compile(
    r"\b0\.\d{2,4}\b"                 # 0.955, 0.9997
    r"|\bAUROC\s*[\d.]+"              # AUROC 0.83
    r"|\b\d{1,3}\.\d%|\b\d{1,3}%"     # 96.7%, 18%
    r"|\b\d+\s*(?:of|/)\s*\d+\b"      # 0 of 36, 16 of 18
    r"|\bA\*\s*[\d.]+")               # A* 0.967

# a bare running counter, not a published measurement
PLACEHOLDER = re.compile(r"^\W*\d[\d,]*\s*/\s*\d[\d,]*\W*$|^\W*0\s*/\s*0\W*$")

# Closed status vocabulary. Statuses are normalised (lowercase, strip, '_'/' ' -> '-')
# before classification. Anything else, including a missing status, is UNKNOWN-STATUS
# and fails in both default and --strict modes, so a typo cannot look live.
LIVE = {"grounded-empirical", "doi", "proven-no-doi", "machine-verified-nogo"}
PROVISIONAL = {"candidate", "conjecture-open"}
# superseded and deprecated are treated as dead so a replaced result cannot appear as a
# live number. The registry did not use those two statuses as of 2026-11-25; they are in
# the allowlist so they fail closed if adopted. A page may also declare itself superseded
# with name="claim-gate" content="superseded" (skipped, with a visible note).
DEAD = {"falsified-negative", "retracted", "withdrawn", "superseded", "deprecated"}
ALLOWED_STATUS = LIVE | PROVISIONAL | DEAD

# Three annotations, because pretending every published numeral maps to a registry entry would be
# its own dishonesty. Most do not — the registry holds results, the site holds prose about them.
#   <registry-id>      the figure traces to a registered result
#   "unregistered"     a real measurement with NO registry entry. This is DEBT, counted and printed
#                      on every run so it has to trend down. It is not a pass.
#   "not-a-measurement" file sizes, item counts, latencies, prices — nothing a floor applies to.
SPECIAL = {"unregistered", "not-a-measurement"}
DISCLOSED = re.compile(
    r"<s>|withdrawn|WITHDRAWN|retracted|RETRACTED|DISQUALIFIED|superseded|deprecated", re.I)
PROVISIONAL_MARK = re.compile(
    r"candidate|preliminary|\bopen\b|conjecture|not established", re.I)

PUBLIC = [f for f in os.listdir(STATIC) if f.endswith(".html")] if os.path.isdir(STATIC) else []


def _registry():
    """Load the registry, or FAIL LOUDLY.

    This returned {} on any error, which made rule 2 pass everything silently whenever the registry
    could not be found — a check reporting success without having checked, inside the gate written
    to stop exactly that. Caught by deliberately planting a retracted claim and watching nothing
    fire. An empty registry is now an error, not a quiet pass.
    """
    if not os.path.exists(REGISTRY):
        raise SystemExit("claim_gate: registry not found at %s — refusing to run, because with no "
                         "registry every retracted-claim check would pass vacuously." % REGISTRY)
    with open(REGISTRY) as fh:
        entries = json.load(fh).get("entries") or []
    if not entries:
        raise SystemExit("claim_gate: registry has no entries — refusing to run.")
    return {e["id"]: e for e in entries}


def _floor(name):
    """Ask nulltest, never the artifact."""
    p = os.path.join(BATTERIES, name if name.endswith(".json") else name + ".json")
    if not os.path.exists(p):
        return None
    try:
        out = subprocess.run([sys.executable, os.path.join(HERE, "nulltest.py"), p, "--json"],
                             capture_output=True, text=True, timeout=900)
        return json.loads(out.stdout)
    except Exception:
        return None


TRACKED = {"span", "div", "li", "p", "td", "b", "h1", "h2", "h3", "h4", "h5", "h6"}
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param",
        "source", "track", "wbr"}


class _Owners(HTMLParser):
    """Give every piece of visible text to exactly ONE owner: its nearest enclosing tracked element.

    This replaced a regex that matched elements without overlap and then kept only the "innermost"
    ones. A <div> matched first swallowed the <b> inside it, so the <b> was never emitted on its
    own, and the <div> was then dropped for having a figure-bearing child: the figure was checked by
    nobody. On /evidence that hid "17 of 18", "0.967" and "0.742", and the gate printed "0 failures".
    The same filter also dropped a parent's own figure whenever a child carried one.

    Ownership keeps what INNERMOST ONLY was for — a wrapper's data-claim never covers numbers that
    live in its child elements, so one id cannot be attached to several unrelated numbers — without
    the blind spot: an element owns only the text that is not inside another tracked element.
    Untracked inline tags (<s>, <a>, <code>, <i>…) belong to the nearest tracked element around them.
    """

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.src = html
        self.line_starts = [0] + [m.end() for m in re.finditer("\n", html)]
        self.stack, self.out = [], []

    def _offset(self):
        line, col = self.getpos()
        return self.line_starts[line - 1] + col

    def handle_starttag(self, tag, attrs):
        if tag in VOID:
            return
        raw = self.get_starttag_text() or ""
        m = re.match(r"<[^\s/>]+(.*?)/?>$", raw, re.S)
        start = self._offset()
        self.stack.append({"tag": tag, "line": self.getpos()[0], "start": start,
                           "attrs": m.group(1) if m else "", "inner": start + len(raw), "text": []})

    def handle_endtag(self, tag):
        # Close back to the nearest open element of this name; children left open end here too.
        # A stray end tag with nothing to close is ignored, as a browser would.
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i]["tag"] == tag:
                end = self._offset()
                for el in self.stack[i:]:
                    self._emit(el, end)
                del self.stack[i:]
                return

    def handle_data(self, data):
        if any(el["tag"] in ("script", "style") for el in self.stack):
            return
        for el in reversed(self.stack):
            if el["tag"] in TRACKED:
                el["text"].append(data)
                return

    def _emit(self, el, end):
        if el["tag"] in TRACKED:
            # The inner HTML is the markup WITHOUT the opening tag's attributes. Disclosure must be
            # judged on that, never on the whole element: the registry id
            # "verifydomain-147-headline-retracted" contains the word "retracted", so searching the
            # whole element matched the gate's OWN annotation and passed every retracted claim
            # silently. Disclosure may sit in a child (<s>, "withdrawn"), so it is the full inner
            # HTML; the figures are only the element's own text.
            self.out.append((el["start"], end, el["line"], self.src[el["inner"]:end], el["attrs"],
                             " ".join(el["text"])))

    def close(self):
        super().close()
        for el in self.stack:
            self._emit(el, len(self.src))
        self.stack = []


def _snippet(text, width=90):
    """The text around the first figure. The first 90 characters often missed it, so a warning
    could print "POST /api/drift/check" for a number further down the element."""
    text = re.sub(r"\s+", " ", text).strip()
    m = FIGURE.search(text)
    start = max(0, m.start() - 30) if m else 0
    return ("…" if start else "") + text[start:start + width]


def _parse(html):
    """Tracked elements as dicts (start, end, line, inner, attrs, text), in document order.

    text is the element's own text — not the text of tracked elements nested in it. Comments are
    not published and are not read. Lines are 1-based lines of `html` as passed in. start/end are
    offsets into `html` (end is the start of the end tag).
    """
    p = _Owners(html)
    p.feed(html)
    p.close()
    rows = []
    for start, end, line, inner, attrs, text in sorted(p.out):
        rows.append({"start": start, "end": end, "line": line, "inner": inner,
                     "attrs": attrs, "text": text})
    return rows


def _elements(html):
    """(line, inner_html, attrs, own_text) for every tracked element, in document order."""
    return [(el["line"], el["inner"], el["attrs"], el["text"]) for el in _parse(html)]


def _claim_id(attrs):
    m = re.search(r'data-claim="([^"]+)"', attrs)
    return m.group(1) if m else None


def _battery_id(attrs):
    m = re.search(r'data-battery="([^"]+)"', attrs)
    return m.group(1) if m else None


def _nearest_registry_wrapper(el, elements, reg):
    """Nearest tracked ancestor whose data-claim is a registry entry, or None."""
    best = None
    for anc in elements:
        if anc["start"] < el["start"] and anc["end"] >= el["end"]:
            cid = _claim_id(anc["attrs"])
            if cid and cid in reg:
                if best is None or anc["start"] > best["start"]:
                    best = anc
    return best


def _wrapper_bound_figures(elements, cid, reg):
    """Unannotated figure-bearing elements whose nearest registry-id ancestor is cid."""
    out = []
    for el in elements:
        if _claim_id(el["attrs"]):
            continue
        if not FIGURE.search(el["text"]) or PLACEHOLDER.search(el["text"].strip()):
            continue
        anc = _nearest_registry_wrapper(el, elements, reg)
        if anc and _claim_id(anc["attrs"]) == cid:
            out.append((el, anc))
    return out


def _normalize_status(status):
    """Lowercase, strip, collapse space, underscore and hyphen to a single '-'."""
    if status is None:
        return None
    s = str(status).strip().lower().replace("_", " ").replace("-", " ")
    s = "-".join(s.split())
    return s or None


def _classify_status(status):
    """Return (class, normalised) where class is live, provisional, dead, or unknown."""
    norm = _normalize_status(status)
    if norm in LIVE:
        return "live", norm
    if norm in PROVISIONAL:
        return "provisional", norm
    if norm in DEAD:
        return "dead", norm
    return "unknown", norm


def _audit_notes_path():
    env = os.environ.get("CLAIM_GATE_AUDIT_NOTES")
    if env:
        return env
    return os.path.join(HERE, "audit_notes.json")


def _audit_notes():
    path = _audit_notes_path()
    if not os.path.exists(path):
        return {}
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _battery_audit_note(notes, bn):
    """Match a battery filename to an audit_notes.json entry.

    Notes are keyed like aperture/calibration-battery-v1; files are
    calibration-battery-v1.json. A leading _ key is documentation, not a battery.
    """
    stem = bn[:-5] if bn.endswith(".json") else bn
    candidates = [stem, "aperture/" + stem]
    if stem.startswith("aperture/"):
        candidates.append(stem[len("aperture/"):])
    for key in candidates:
        if key in notes and not str(key).startswith("_"):
            return notes[key], key
    for key, val in notes.items():
        if str(key).startswith("_"):
            continue
        if str(key).rsplit("/", 1)[-1] == stem:
            return val, key
    return None, None


def _version_tuple(value):
    if value is None or value == "":
        return None
    parts = [int(p) for p in re.findall(r"\d+", str(value))]
    return tuple(parts) if parts else None


def _note_states_count(note, claimed, cells):
    """True when the audit note records the block's count, e.g. '16 of 18 under v1.2'."""
    if not note or claimed is None:
        return False
    text = str(note)
    if cells is not None:
        pat = r"\b%s\s+of\s+%s\b" % (re.escape(str(claimed)), re.escape(str(cells)))
        return re.search(pat, text) is not None
    return re.search(r"\b%s\b" % re.escape(str(claimed)), text) is not None


def _status_findings(entry, html, fn, line, key, strict):
    """Classify a cited entry. Returns (fails, warns, labelled, (key, class, norm, raw))."""
    raw = None if entry is None else entry.get("status")
    klass, norm = _classify_status(raw)
    cited = (key, klass, norm, raw)
    fails, warns, labelled = [], [], []
    if klass == "unknown":
        if raw is None or str(raw).strip() == "":
            shown = "missing"
        else:
            shown = repr(raw)
        fails.append(("UNKNOWN-STATUS", fn, line,
                      "%s  status %s is not in the allowlist (live: %s; provisional: %s; dead: %s)"
                      % (key, shown, ", ".join(sorted(LIVE)),
                         ", ".join(sorted(PROVISIONAL)), ", ".join(sorted(DEAD)))))
    elif klass == "dead":
        if not DISCLOSED.search(html or ""):
            fails.append(("RETRACTED-NOT-DISCLOSED", fn, line, key))
    elif klass == "provisional":
        rec = ("PROVISIONAL-NOT-LABELLED", fn, line, "%s  status %s" % (key, norm))
        if not PROVISIONAL_MARK.search(html or ""):
            (fails if strict else warns).append(rec)
        else:
            labelled.append((fn, line, key, norm))
    return fails, warns, labelled, cited


def check(strict_unannotated=False):
    # A DENOMINATOR, mirroring the registry guard. With PUBLIC empty — a wrong cwd, a moved tree, a
    # glob that matched nothing — this walked zero pages and reported "0 failures", which reads
    # identical to a clean run. The registry arm already refused to run vacuously; the pages arm
    # did not. Same hole, one function apart, in the gate written to stop exactly this.
    if len(PUBLIC) < 5:
        raise SystemExit("claim_gate: found only %d public pages under %s — refusing to run, because "
                         "a near-empty page set reports '0 failures' and looks like a clean pass."
                         % (len(PUBLIC), STATIC))
    reg = _registry()
    fails, warns, debt, skipped = [], [], [], []
    cited_ids = {}
    provisional_labelled = []
    artifact_disclosed = []
    notes = _audit_notes()
    for fn in sorted(PUBLIC):
        path = os.path.join(STATIC, fn)
        html = open(path, encoding="utf-8", errors="replace").read()
        # A page can declare itself superseded. It is still served (we do not delete history), but
        # its figures are not live coverage debt. The declaration is machine-readable so a page
        # cannot quietly opt out of the gate in prose.
        if 'name="claim-gate" content="superseded"' in html:
            skipped.append(fn)
            continue
        # Blank the head instead of slicing it off, so reported lines are lines of the file. Slicing
        # numbered from <body> ("evidence.html:16" meant line 249), and a page without "<body"
        # sliced to its LAST CHARACTER, so the whole page went unchecked.
        b = html.find("<body")
        body = ("\n" * html[:b].count("\n") + html[b:]) if b >= 0 else html
        # Strip style/script before matching. rgba(0,0,0,0.24) is not a claim about measurement,
        # and a gate that flags CSS teaches people to skim its output. Replaced with newlines so
        # line numbers still point at the real source.
        body = re.sub(r"<(style|script)\b.*?</\1>",
                      lambda m: "\n" * m.group(0).count("\n"), body, flags=re.S)
        elements = _parse(body)
        for el in elements:
            line, whole, attrs, inner = el["line"], el["inner"], el["attrs"], el["text"]
            if not FIGURE.search(inner):
                continue
            # "0 / 0", "0 / 1,000" and friends are live counters rendered by JS, not measurements.
            if PLACEHOLDER.search(inner.strip()):
                continue
            cid = _claim_id(attrs)
            bat = _battery_id(attrs)
            if not cid:
                wrapper = _nearest_registry_wrapper(el, elements, reg)
                if wrapper:
                    wcid = _claim_id(wrapper["attrs"])
                    (fails if strict_unannotated else warns).append(
                        ("WRAPPER-CLAIM", fn, line,
                         "%s  (wrapper %s:%s %s)" % (_snippet(inner), fn, wrapper["line"], wcid)))
                    # rules 2-3 with the ancestor's id; disclosure on the ancestor's inner HTML
                    entry = reg.get(wcid)
                    if entry:
                        sf, sw, sl, cited = _status_findings(
                            entry, wrapper["inner"], fn, line, wcid, strict_unannotated)
                        fails.extend(sf)
                        warns.extend(sw)
                        provisional_labelled.extend(sl)
                        cited_ids[cited[0]] = cited[1:]
                    wbat = bat or _battery_id(wrapper["attrs"])
                    if wbat:
                        whtml = whole if bat else wrapper["inner"]
                        rep = _floor(wbat)
                        if rep and rep["verdict"] == "DISQUALIFIED" and "DISQUALIFIED" not in whtml:
                            fails.append(("DISQUALIFIED-NOT-LABELLED", fn, line, wbat))
                else:
                    (fails if strict_unannotated else warns).append(
                        ("UNANNOTATED", fn, line, _snippet(inner)))
                continue
            key = cid
            if key in SPECIAL:
                debt.append((key, fn, line, inner.strip()[:70]))
                continue
            entry = reg.get(key)
            if entry is None:
                fails.append(("CLAIM-ID-NOT-IN-REGISTRY", fn, line, key))
                continue
            sf, sw, sl, cited = _status_findings(
                entry, whole, fn, line, key, strict_unannotated)
            fails.extend(sf)
            warns.extend(sw)
            provisional_labelled.extend(sl)
            cited_ids[cited[0]] = cited[1:]
            if bat:
                rep = _floor(bat)
                if rep and rep["verdict"] == "DISQUALIFIED" and "DISQUALIFIED" not in whole:
                    fails.append(("DISQUALIFIED-NOT-LABELLED", fn, line, bat))

    # rule 4 — artifacts must not assert their own verdict
    for bn in sorted(os.listdir(BATTERIES)) if os.path.isdir(BATTERIES) else []:
        if not bn.endswith(".json"):
            continue
        try:
            d = json.load(open(os.path.join(BATTERIES, bn)))
        except Exception:
            continue
        if not isinstance(d, dict):
            continue          # a bare-array battery carries no self-reported verdict to contradict
        sf = d.get("surface_floor") or {}
        claimed = sf.get("cells_separating")
        if claimed is None:
            continue
        rep = _floor(bn)
        if not rep:
            continue
        got = rep.get("n_separating_cells")
        if got == claimed:
            continue
        block_t = _version_tuple(sf.get("nulltest_version"))
        cur_ver = rep.get("version")
        cur_t = _version_tuple(cur_ver)
        stale = block_t is None or (cur_t is not None and block_t < cur_t)
        note, note_key = _battery_audit_note(notes, bn)
        if stale and _note_states_count(note, claimed, sf.get("cells")):
            artifact_disclosed.append(
                ("ARTIFACT-SUPERSEDED-DISCLOSED", bn, 0,
                 "says %s, nulltest %s says %s; count recorded in audit_notes.json (%s)"
                 % (claimed, cur_ver or "current", got, note_key or "entry")))
        else:
            fails.append(("ARTIFACT-SELF-REPORT", bn, 0,
                          "says %s, nulltest says %s" % (claimed, got)))
    info = {
        "cited": cited_ids,
        "provisional_labelled": provisional_labelled,
        "artifact_disclosed": artifact_disclosed,
    }
    return fails, warns, debt, skipped, info


def retract(cid):
    """Everything that must change when a claim dies."""
    reg = _registry()
    print("retracting: %s" % cid)
    if cid in reg:
        print("  registry status: %s" % reg[cid].get("status"))
    hits, numerals, wrappers = [], set(), []
    for fn in sorted(PUBLIC):
        html = open(os.path.join(STATIC, fn), encoding="utf-8", errors="replace").read()
        elements = _parse(html)
        for el in elements:
            if _claim_id(el["attrs"]) == cid:
                hits.append((fn, el["line"], el["text"].strip()[:80]))
                numerals.update(FIGURE.findall(el["text"]))
        for el, anc in _wrapper_bound_figures(elements, cid, reg):
            wrappers.append((fn, el["line"], _snippet(el["text"]), anc["line"]))
            numerals.update(FIGURE.findall(el["text"]))
    print("\n  elements citing it (%d):" % len(hits))
    for fn, ln, txt in hits:
        print("    %s:%d  %s" % (fn, ln, txt))
    print("\n  WRAPPER-CLAIM figures (%d):" % len(wrappers))
    if wrappers:
        for fn, ln, txt, wln in wrappers:
            print("    %s:%d  %s  (wrapper %s:%d)" % (fn, ln, txt, fn, wln))
    else:
        print("    none")
    print("\n  numerals inside them (%d): %s" % (len(numerals), ", ".join(sorted(numerals)) or "none"))
    print("\n  UNANNOTATED occurrences of those numerals elsewhere — these are what gets missed:")
    found = 0
    for num in sorted(numerals):
        for fn in sorted(PUBLIC):
            for i, ln in enumerate(open(os.path.join(STATIC, fn), encoding="utf-8",
                                        errors="replace").read().split("\n"), 1):
                if num in ln and 'data-claim="%s"' % cid not in ln:
                    print("    %s:%d  %s" % (fn, i, num)); found += 1
    if not found:
        print("    none")
    return 0


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--retract", metavar="REGISTRY_ID")
    ap.add_argument("--strict", action="store_true",
                    help="treat unannotated, WRAPPER-CLAIM, and unlabelled provisional figures as failures")
    a = ap.parse_args(argv[1:])
    if a.retract:
        return retract(a.retract)

    fails, warns, debt, skipped, info = check(strict_unannotated=a.strict)
    for kind, fn, line, detail in fails:
        print("FAIL  %-26s %s:%s  %s" % (kind, fn, line, detail))
    unann = [w for w in warns if w[0] in ("UNANNOTATED", "WRAPPER-CLAIM")]
    prov_warns = [w for w in warns if w[0] == "PROVISIONAL-NOT-LABELLED"]
    other_warns = [w for w in warns if w not in unann and w not in prov_warns]
    if unann:
        print("\n%d unannotated figures (warnings until the annotation pass lands; --strict fails on them)"
              % len(unann))
        for kind, fn, line, detail in unann[:12]:
            print("  warn  %s:%s  %s" % (fn, line, detail))
        if len(unann) > 12:
            print("  ... and %d more" % (len(unann) - 12))
    if prov_warns:
        print("\n%d provisional figures not labelled (warning; --strict fails on them)"
              % len(prov_warns))
        for kind, fn, line, detail in prov_warns:
            print("  warn  %-26s %s:%s  %s" % (kind, fn, line, detail))
    for kind, fn, line, detail in other_warns:
        print("  warn  %-26s %s:%s  %s" % (kind, fn, line, detail))
    labelled = info.get("provisional_labelled") or []
    if labelled:
        print("\n%d provisional figures (labelled):" % len(labelled))
        for fn, line, key, norm in labelled:
            print("  %s:%s  %s  %s" % (fn, line, key, norm))
    for kind, fn, line, detail in info.get("artifact_disclosed") or []:
        print("INFO  %-26s %s:%s  %s" % (kind, fn, line, detail))
    cited = info.get("cited") or {}
    if cited:
        classes, statuses = {}, {}
        for key, (klass, norm, raw) in cited.items():
            classes[klass] = classes.get(klass, 0) + 1
            label = norm or "missing"
            statuses.setdefault(klass, {})
            statuses[klass][label] = statuses[klass].get(label, 0) + 1
        print("\ncited registry statuses (%d unique ids):" % len(cited))
        for klass in ("live", "provisional", "dead", "unknown"):
            n = classes.get(klass, 0)
            breakdown = statuses.get(klass) or {}
            extra = ", ".join("%s %d" % (s, c) for s, c in sorted(breakdown.items()))
            print("  %-12s %d%s" % (klass, n, (": " + extra) if extra else ""))
    if debt:
        print("\n%d figures annotated but UNREGISTERED or not-a-measurement — standing debt, must trend down:"
              % len(debt))
        seen = {}
        for kind, fn, line, txt in debt:
            seen.setdefault((fn, kind), 0)
            seen[(fn, kind)] += 1
        for (fn, kind), n in sorted(seen.items()):
            print("  %-22s %-18s %d" % (fn, kind, n))
    if skipped:
        print("\nskipped as superseded (still served, not live claims): %s" % ", ".join(skipped))
    print("\n%d failures, %d unannotated, %d annotated-debt" % (len(fails), len(unann), len(debt)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
