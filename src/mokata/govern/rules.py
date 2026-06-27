"""G1 — 4-tier rules + constitution under the unified config.

Tiers (most-binding to most-prose):
  1. always_on   — the reflex rules injected every session (HARD cap: 60 lines)
  2. agent_memory — per-agent MEMORY.md (cap: 200 lines)
  3. steering    — optional steering notes (.mokata/steering.md)
  4. articles    — the constitution's governing articles

G2 — the rules-vs-gates-vs-hooks taxonomy: a rule is advisory (stays prose), blocking
(make it a gate), or event-driven (make it a hook). "Checkable → gate or hook, not prose."
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .. import MOKATA_DIR

RULE_TIERS = ("always_on", "agent_memory", "steering", "articles")
CAPS: Dict[str, Optional[int]] = {
    "always_on": 60,
    "agent_memory": 200,
    "steering": None,
    "articles": None,
}

# Tier 1 — the always-on reflex rules. Kept terse and well under the 60-line cap.
ALWAYS_ON_RULES = """\
# mokata — always-on rules (Tier 1; <= 60 lines)
# Inviolable
- Human-gate every durable write (code, memory, config). Nothing silent or autonomous.
- Local-first: nothing leaves the machine unless a human wires it. No telemetry.
# Spec & correctness
- Brainstorm first: explore approaches, get one explicitly approved (HARD-GATE) before a spec.
- No implementation before a spec whose every acceptance criterion maps to a test.
- RED before GREEN: a test must fail before its implementation is written.
# Degradation & tools
- Degrade, never break: a missing optional tool falls back to a documented alternative.
- Prefer the codebase graph over grep; fall back to grep when the graph is absent.
# Safety
- Sync hooks block (exit 2) only for security; async hooks observe and never block.
- Catch secrets before they are written, committed, or sent.
- Record every gate decision, tool call, and durable write in the audit ledger.
# Governance
- Checkable rule -> make it a gate or a hook, not prose; keep advisory rules short.
# Memory
- Memory is on by default and heals by surfacing old->new changes for your approval.
"""


@dataclass
class RuleSet:
    tier: str
    lines: List[str] = field(default_factory=list)
    cap: Optional[int] = None

    @property
    def line_count(self) -> int:
        return len(self.lines)

    @property
    def within_cap(self) -> bool:
        return self.cap is None or self.line_count <= self.cap


def _text_to_lines(text: str) -> List[str]:
    return text.splitlines()


def always_on_rules() -> RuleSet:
    return RuleSet("always_on", _text_to_lines(ALWAYS_ON_RULES), CAPS["always_on"])


# Stage 36 — the captured project rules/guardrails appended to the always-on tier. The header
# costs one line; both header and the project lines fit INSIDE the same hard cap (never blown).
_PROJECT_RULES_HEADER = "# Project rules & guardrails (captured via /mokata:onboard)"


def _project_always_on_lines(surface, budget: int) -> List[str]:
    """Pull the captured rule/guardrail entries as terse lines, fitting INSIDE `budget` lines
    (header included). Degrade-clean: memory off / uninitialized / any error -> no lines."""
    if budget <= 1:
        return []
    try:
        from ..memory import MemoryStore, always_on_lines
        store = MemoryStore.from_surface(surface)
        lines, _overflow = always_on_lines(store, budget - 1)   # -1 reserves the header
    except Exception:
        return []
    return [_PROJECT_RULES_HEADER, *lines] if lines else []


def always_on_rules_for(surface) -> RuleSet:
    """The always-on tier WITH the project's captured rules/guardrails merged in, kept within
    the hard line cap (P11 — the budget is never exceeded; overflow is flagged, not dropped
    silently)."""
    base = _text_to_lines(ALWAYS_ON_RULES)
    cap = CAPS["always_on"] or 0
    remaining = max(cap - len(base), 0)
    return RuleSet("always_on", base + _project_always_on_lines(surface, remaining),
                   CAPS["always_on"])


def _read_optional(path: str) -> List[str]:
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return fh.read().splitlines()
    return []


def load_rules(surface) -> Dict[str, RuleSet]:
    """Load all four tiers from the unified config surface."""
    mdir = os.path.join(surface.root, MOKATA_DIR)
    articles_lines: List[str] = []
    const = getattr(surface, "constitution", None)
    if const is not None and const.text:
        articles_lines = const.text.splitlines()
    return {
        "always_on": always_on_rules_for(surface),
        "agent_memory": RuleSet("agent_memory",
                                _read_optional(os.path.join(mdir, "MEMORY.md")),
                                CAPS["agent_memory"]),
        "steering": RuleSet("steering",
                            _read_optional(os.path.join(mdir, "steering.md")),
                            CAPS["steering"]),
        "articles": RuleSet("articles", articles_lines, CAPS["articles"]),
    }


def validate_caps(rules: Dict[str, RuleSet]) -> List[str]:
    """Return human-readable cap violations (empty == all within budget)."""
    errors: List[str] = []
    for tier, rs in rules.items():
        if not rs.within_cap:
            errors.append(
                f"{tier} exceeds its {rs.cap}-line cap ({rs.line_count} lines)")
    return errors


# --- G2 taxonomy ---------------------------------------------------------------
ADVISORY = "advisory"
BLOCKING = "blocking"
EVENT = "event"

_MECHANISM = {ADVISORY: "rule", BLOCKING: "gate", EVENT: "hook"}

RULE_TAXONOMY = {
    ADVISORY: "Not mechanically checkable -> keep it short prose (a rule).",
    BLOCKING: "Checkable and must stop progress -> make it a gate.",
    EVENT: "Triggered by an event -> make it a hook.",
}


def classify(blocking: bool = False, on_event: bool = False) -> str:
    if on_event:
        return EVENT
    if blocking:
        return BLOCKING
    return ADVISORY


def mechanism_for(kind: str) -> str:
    return _MECHANISM[kind]
