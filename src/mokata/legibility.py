"""Stage 54c — pipeline legibility & flow UX (read-only/derived surfacing).

A small bundle of cohesive helpers that make a run read like a guided flow — the user always
knows what just happened, why a gate fired, the one thing to do next, and how far along they
are. Everything here is **pure, deterministic, read-only**: it formats verdicts/recaps from
values the gates and run-state already produce — it never re-derives a verdict, bumps a
counter, or writes durable state (the Stage 48/49 lesson). Degrade-clean, frugal, clean-room.

The one-key approve/edit/reject human-gate response lives in `prompt.py` (it reads input);
this module is the read-only formatting layer.
"""

from __future__ import annotations

from typing import Any, Optional

from .progress import STAGE_BADGE_STAGES

# ----------------------------------------------------------------- 1. gate-verdict legibility
# A SHARED one-line verdict for ANY gate decision, so completeness / spec-persisted / deviation
# / write gates all read the same way (a pass gets a one-liner too, not only a block).
_PASS_GLYPH, _BLOCK_GLYPH = "✓", "✗"
_PASS_ASCII, _BLOCK_ASCII = "[PASS]", "[BLOCK]"


def gate_verdict(name: str, passed: bool, reason: str, *, action: Optional[str] = None,
                 ascii_only: bool = False) -> str:
    """One legible line for a gate decision:
        ✓ <name> passed (<reason>)
        ✗ <name> blocked — <reason>
    A blocked verdict with an `action` adds a second `→ to unblock: …` line (touch 2)."""
    if passed:
        head = _PASS_ASCII if ascii_only else _PASS_GLYPH
        return f"{head} {name} passed ({reason})"
    head = _BLOCK_ASCII if ascii_only else _BLOCK_GLYPH
    line = f"{head} {name} blocked — {reason}"
    if action:
        arrow = "->" if ascii_only else "→"
        line += f"\n  {arrow} to unblock: {action}"
    return line


# ----------------------------------------------------------------- 2. why-blocked / unblock
# The SINGLE next action that clears each gate — generalizing spec_gate's actionable pattern to
# every gate. Read-only lookup; an unknown gate degrades to None (no invented advice).
GATE_UNBLOCK = {
    "completeness": "write a test for each unmapped acceptance criterion "
                    "(`/mokata:test`), then re-run the gate",
    "spec-persisted": "draft and emit the spec first (`/mokata:spec`)",
    "approach-approval": "approve exactly one approach to continue (`/mokata:brainstorm`)",
    "refinement-approval": "approve a scoped refinement set (`/mokata:refine`)",
    "emit-approval": "review the output, then approve the emit",
    "red-before-green": "show the tests FAILING (RED) before writing any implementation",
    "deviation": "approve the plan change (re-enter the approval surface) or decline and "
                 "implement strictly to the approved plan",
    # security: a secret is a HARD block — the action is to remove it, never to approve it away.
    "write-secret": "remove the flagged secret(s) before writing — a security block "
                    "cannot be approved away",
}


def unblock_hint(gate_id: Optional[str]) -> Optional[str]:
    """The single next action that clears `gate_id`, or None when there's no canonical one."""
    return GATE_UNBLOCK.get(gate_id or "")


# Map a result object to (gate_id, passed). Duck-typed so one renderer covers the completeness
# GateResult, the SpecGateResult, a WriteOutcome, and a DeviationOutcome without re-deriving
# any verdict — each already carries its own decision.
def _result_passed(result: Any) -> bool:
    if hasattr(result, "passed"):
        return bool(result.passed)
    if hasattr(result, "committed"):           # WriteOutcome
        return bool(result.committed)
    if hasattr(result, "approved"):            # DeviationOutcome
        return bool(result.approved)
    return False


def _result_gate_id(result: Any) -> str:
    gid = getattr(result, "gate_id", None)
    if gid:
        return str(gid)
    if hasattr(result, "committed"):
        return "write"
    if hasattr(result, "approved"):
        return "deviation"
    return "gate"


def verdict(result: Any, *, ascii_only: bool = False) -> str:
    """The shared one-liner for a real gate-result object (read-only). Pulls `.passed`/`.reason`
    /`.gate_id` (duck-typed across the gates) and, on a block, the canonical unblock action."""
    passed = _result_passed(result)
    gate_id = _result_gate_id(result)
    reason = str(getattr(result, "reason", "") or "")
    action = None
    if not passed:
        if gate_id == "write" and getattr(result, "findings", None):
            action = unblock_hint("write-secret")   # a secret block — security, not approvable
        else:
            action = unblock_hint(gate_id)
    return gate_verdict(gate_id, passed, reason, action=action, ascii_only=ascii_only)


# ----------------------------------------------------------------- 3. stage recap + next-step
# The user-facing arc is the same five stages the 54b badge highlights (single source so the
# nudge can't drift from the badge). After a stage, name the ONE next command.
#
# HONEST MECHANISM ONLY: Claude Code cannot pre-fill the prompt box or rebind Tab (no plugin
# API for it), so the nudge just NAMES the next `/mokata:` command — it then reaches the user
# via the `/` autocomplete (click-to-fill) and model-invoked continuation. We never claim a
# pre-fill or a key rebind.
_NEXT = {STAGE_BADGE_STAGES[i]: STAGE_BADGE_STAGES[i + 1]
         for i in range(len(STAGE_BADGE_STAGES) - 1)}


def next_command(stage: str) -> Optional[str]:
    """The `/mokata:` command that follows `stage` on the user arc, or None at the end / for an
    unknown stage."""
    nxt = _NEXT.get(stage)
    return f"/mokata:{nxt}" if nxt else None


def stage_recap(stage: str, recap: str, *, ascii_only: bool = False) -> str:
    """`✓ <stage> done — <recap>. Next: \\`/mokata:<next>\\`` (the Next clause is omitted at the
    terminal stage). Read-only; the recap text is supplied by the caller."""
    head = _PASS_ASCII if ascii_only else _PASS_GLYPH
    line = f"{head} {stage} done — {recap}."
    nxt = next_command(stage)
    if nxt:
        line += f" Next: `{nxt}`"
    return line


# ----------------------------------------------------------------- 5. in-stage progress counters
def counter(done: int, total: int, unit: str = "") -> str:
    """A compact `[done/total]` (or `[done/total unit]`, e.g. `[3/7 ACs]`)."""
    return f"[{done}/{total}{(' ' + unit) if unit else ''}]"


def stage_counter(prog: Any) -> str:
    """`[done/total]` for an active run (from `progress.build_progress`), `""` with no run.
    Read-only — it only formats numbers already on the progress view."""
    if prog is None or not getattr(prog, "active", False):
        return ""
    return counter(prog.done, prog.total)
