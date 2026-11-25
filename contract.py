#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Kevin Russell (Aperture / Project Forty Two)
"""The tool contract — every honesty tool implements this, or it does not ship.

This is the spine of the toolbox (see CHARTER.md). A lone script is copyable; a contract that
forces every member to expose its own vacuity mode is a standard others build inside. Four methods,
frozen:

    run(...)        -> Verdict     the structured verdict, with the numbers that back it
    selftest()      -> bool        the DELIBERATELY BROKEN input the tool must catch. CI runs this.
    false_positive_rate() -> dict  a CALIBRATED, shipped measurement — a rate with intervals, not a
                                   promise. None is a legal answer and means "not yet measured".
    replay(receipt) -> Verdict     re-derive the verdict offline, stdlib only, no call back to us

THE STABLE/EXPERIMENTAL WALL IS MECHANICAL, NOT EDITORIAL. A tool registered `experimental` — one
that has not shown its selftest catches a broken input, or that fails its own kill condition, like
observertest today — physically cannot emit a stable-green verdict. The dispatcher stamps its tier
onto every verdict it returns, and Verdict.is_stable_green() reads that stamp. This is the
packaging-layer form of the discipline we nearly lost: it makes it impossible to launder a failing
tool under a passing tool's credibility.
"""
from __future__ import annotations

import abc
import json
from dataclasses import asdict, dataclass, field

STABLE = "stable"
EXPERIMENTAL = "experimental"

# Verdict strings a tool may return. EXPERIMENTAL_NO_STABLE_VERDICT is the wall speaking: an
# experimental tool's finding is real information but is never a green a stranger may rely on.
PASS = "PASS"
DISQUALIFIED = "DISQUALIFIED"
INDISTINGUISHABLE = "INDISTINGUISHABLE"
NOT_SCORABLE = "NOT_SCORABLE"
EXPERIMENTAL_NO_STABLE_VERDICT = "EXPERIMENTAL_NO_STABLE_VERDICT"


@dataclass
class Verdict:
    tool: str
    tier: str                      # STABLE | EXPERIMENTAL — stamped by the dispatcher, not the tool
    verdict: str
    headline: str
    evidence: dict = field(default_factory=dict)
    fpr: dict | None = None        # the tool's calibrated false-positive rate, or None if unmeasured
    replayable: bool = True        # can a stranger re-derive this offline with stdlib and no callback?
    power: dict | None = None      # measured power / MDE for the tested sample size, or None

    def has_measured_power(self) -> bool:
        pw = self.power if self.power is not None else (self.evidence or {}).get("power")
        return isinstance(pw, dict) and pw.get("measured_mde") is not None

    def is_stable_green(self) -> bool:
        """The one question a downstream pipeline actually asks: may I rely on this PASS?

        Only a STABLE tool returning PASS with both a measured false-positive rate and measured
        power (n >= 156, where MDE is established) counts. An experimental tool's PASS is not
        green, a DISQUALIFIED is not green, a PASS with no measured FPR is not green, and an
        underpowered PASS below the smallest measured size is not green.
        """
        return (self.tier == STABLE
                and self.verdict == PASS
                and self.fpr is not None
                and self.has_measured_power())

    def state(self) -> str:
        if self.tier == STABLE and self.verdict == PASS and not self.has_measured_power():
            return "underpowered"
        if self.is_stable_green():
            return "stable_green"
        return self.verdict.lower()

    def to_json(self) -> str:
        d = asdict(self)
        d["stable_green"] = self.is_stable_green()
        d["state"] = self.state()
        return json.dumps(d, indent=1)


class Tool(abc.ABC):
    """Implement all four methods. The registry refuses a tool that only implements run()."""

    name: str = "unnamed"
    summary: str = ""

    @abc.abstractmethod
    def run(self, *args, **kwargs) -> Verdict:
        ...

    @abc.abstractmethod
    def selftest(self) -> bool:
        """Return True iff the tool caught its own deliberately-broken input. Nothing enters
        `stable` until this passes in CI — the single discipline that produced our whole failure
        taxonomy, made a merge gate."""
        ...

    @abc.abstractmethod
    def false_positive_rate(self) -> dict | None:
        """A calibrated measurement with intervals, or None if honestly not yet measured. A number
        with no interval is not an answer; a promise is not a measurement."""
        ...

    @abc.abstractmethod
    def replay(self, receipt) -> Verdict:
        """Re-derive a verdict from a receipt, stdlib only, with no network. If a stranger cannot
        run this offline, the verdict was never independent."""
        ...


# ── the registry, and the wall ────────────────────────────────────────────────────────────
_REGISTRY: dict[str, tuple[str, Tool]] = {}


def register(tool: Tool, tier: str) -> None:
    if tier not in (STABLE, EXPERIMENTAL):
        raise ValueError("tier must be %r or %r" % (STABLE, EXPERIMENTAL))
    # A tool cannot be registered `stable` unless it actually implements the contract. This is a
    # shallow check (the methods exist and are not the ABC stubs); CI runs the real selftest.
    for m in ("run", "selftest", "false_positive_rate", "replay"):
        if getattr(type(tool), m) is getattr(Tool, m):
            raise TypeError("%s does not implement %s() — cannot register" % (tool.name, m))
    _REGISTRY[tool.name] = (tier, tool)


def tier_of(name: str) -> str:
    return _REGISTRY[name][0]


def registered() -> list[tuple[str, str, str]]:
    """(name, tier, summary) for everything registered, stable first."""
    rows = [(n, t, tool.summary) for n, (t, tool) in _REGISTRY.items()]
    return sorted(rows, key=lambda r: (r[1] != STABLE, r[0]))


def run(name: str, *args, **kwargs) -> Verdict:
    """Dispatch to a tool AND STAMP ITS TIER ONTO THE VERDICT. The tool cannot lie about its own
    tier because it does not get to set it — this function does, from the registry. An experimental
    tool's verdict is forced to EXPERIMENTAL_NO_STABLE_VERDICT so it can never read as stable green,
    with its real finding preserved in the headline and evidence."""
    if name not in _REGISTRY:
        raise KeyError("no tool named %r (have: %s)"
                       % (name, ", ".join(sorted(_REGISTRY)) or "none"))
    tier, tool = _REGISTRY[name]
    v = tool.run(*args, **kwargs)
    v.tool = tool.name
    v.tier = tier
    if tier == EXPERIMENTAL:
        v.headline = "[experimental — not a stable verdict] " + v.headline
        v.evidence = {"raw_verdict": v.verdict, **(v.evidence or {})}
        v.verdict = EXPERIMENTAL_NO_STABLE_VERDICT
    elif v.verdict == PASS and not v.has_measured_power():
        pw = v.power if v.power is not None else (v.evidence or {}).get("power")
        n = pw.get("n") if isinstance(pw, dict) else None
        size_tag = f"n={n} < 156" if n is not None else "no measured power"
        v.headline = f"[underpowered — {size_tag}, below smallest measured size (156); PASS is not green] " + v.headline
    return v
