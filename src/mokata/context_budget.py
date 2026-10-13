"""H-8 + HP.S6 (0.0.22 D3) — ONE context budget, read from `settings.context.*`.

Doc 110 criterion 5, clause 2: *one context budget, with no second channel.* Everything mokata adds
to the model's context automatically — the SessionStart briefing (with its live repo block and its
post-compaction re-anchor), the per-turn recall pack — is sized from this one object, and every byte
of it leaves through the same hook `additionalContext` channel that the budget measures.

Before this stage the numbers were module constants (`bootstrap.py`), so a team could not tune them,
and two additions (the re-anchor block and the MCP reachability warning) were appended AFTER the
briefing's budget check — a second, unmetered channel. Both now sit inside it.

`settings.context` keys (all optional; defaults are the values that shipped):

    briefing_tokens        SessionStart briefing ceiling          2000   (200..8000)
    turn_tokens            per-turn recall pack ceiling            300   (0..2000; 0 = off)
    briefing_rules_lines   rule/guardrail lines in the briefing     12   (0..40)
    turn_rules_lines       reserved rule lines per turn              4   (0..12)
    turn_top_k             recall candidates ranked per turn         5   (1..50)
    live_lines             live repo block lines (incl. header)      6   (0..10; 0 = off)

⚠ A bad value is REFUSED, not clamped silently and not raised: the default is used and the briefing
SAYS which key was ignored and why (`ContextBudget.problems`), so a typo cannot quietly turn a
budget off — or up.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any, Dict, List, Tuple

CONTEXT_SETTINGS_KEY = "context"

#: (default, minimum, maximum) per key — the defaults are the shipped constants.
LIMITS: Dict[str, Tuple[int, int, int]] = {
    "briefing_tokens": (2000, 200, 8000),
    "turn_tokens": (300, 0, 2000),
    "briefing_rules_lines": (12, 0, 40),
    "turn_rules_lines": (4, 0, 12),
    "turn_top_k": (5, 1, 50),
    "live_lines": (6, 0, 10),
}


@dataclass(frozen=True)
class ContextBudget:
    briefing_tokens: int = LIMITS["briefing_tokens"][0]
    turn_tokens: int = LIMITS["turn_tokens"][0]
    briefing_rules_lines: int = LIMITS["briefing_rules_lines"][0]
    turn_rules_lines: int = LIMITS["turn_rules_lines"][0]
    turn_top_k: int = LIMITS["turn_top_k"][0]
    live_lines: int = LIMITS["live_lines"][0]
    problems: Tuple[str, ...] = field(default_factory=tuple)

    def problem_line(self) -> str:
        return ("mokata: settings.context ignored — " + "; ".join(self.problems)
                + " (defaults used; fix with `mokata config set settings.context.<key> <n>`)")


def context_budget(manifest: Any) -> ContextBudget:
    """The budget from `settings.context`, every bad value refused with its reason."""
    try:
        raw = manifest.setting(CONTEXT_SETTINGS_KEY, {}) if manifest is not None else {}
    except AttributeError:
        raw = {}
    if not isinstance(raw, dict):
        return ContextBudget(problems=("settings.context is not a table",))
    values: Dict[str, int] = {}
    problems: List[str] = []
    for key, (default, lo, hi) in LIMITS.items():
        if key not in raw:
            continue
        val = raw[key]
        if isinstance(val, bool) or not isinstance(val, int):
            problems.append(f"{key}={val!r} is not a whole number")
        elif not lo <= val <= hi:
            problems.append(f"{key}={val} is outside {lo}..{hi}")
        else:
            values[key] = val
    unknown = sorted(set(raw) - set(LIMITS))
    if unknown:
        problems.append("unknown key(s): " + ", ".join(unknown))
    return ContextBudget(**values, problems=tuple(problems))


def budget_keys() -> List[str]:
    return [f.name for f in fields(ContextBudget) if f.name != "problems"]


__all__ = ["CONTEXT_SETTINGS_KEY", "LIMITS", "ContextBudget", "context_budget", "budget_keys"]
