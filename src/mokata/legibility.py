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

import os
import re
import sys
from typing import Any, Iterable, List, Optional, Sequence

from .progress import STAGE_BADGE_STAGES

# ================================================================= 0. shared presentation layer
# A SMALL, dependency-free look-and-feel used by every verdict / recap / table surface, so the
# CLI (and later the TUI) read the same way. ANSI escape codes only — NO rich, NO textual. Still
# pure/read-only: these format values the caller already has; they never touch state.
#
# Two independent gates, both degrade-clean:
#   * colour (ANSI)  — on ONLY when _color_enabled(): a real TTY, `NO_COLOR` unset, not ascii_only.
#   * box charset    — Unicode box-drawing, with an ASCII fallback (chosen by the caller's
#                      ascii_only flag). Callers tie ascii_only to `not _color_enabled()` so a
#                      piped / NO_COLOR / ascii_only surface gets a plain-ASCII table, zero escapes.
_ANSI_GREEN, _ANSI_RED, _ANSI_DIM, _ANSI_RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _color_enabled(*, ascii_only: bool = False, stream: Any = None) -> bool:
    """The single colour gate: True only when output is a real TTY AND `NO_COLOR` is unset AND
    not ascii_only. Anything else (piped/redirected, NO_COLOR, ascii, a broken stream) -> False,
    so ANSI never reaches a non-terminal. `stream` defaults to sys.stdout (injectable for tests)."""
    if ascii_only:
        return False
    if os.environ.get("NO_COLOR") is not None:
        return False
    st = sys.stdout if stream is None else stream
    try:
        return bool(st.isatty())
    except Exception:
        return False


def _paint(text: str, code: str, color: bool) -> str:
    """Wrap `text` in an ANSI colour when `color`; otherwise return it untouched (byte-identical)."""
    return f"{code}{text}{_ANSI_RESET}" if color else text


def green(text: str, *, color: bool) -> str:
    """mokata's accent (BRAND.md) for pass/ok — colour-gated by the caller."""
    return _paint(text, _ANSI_GREEN, color)


def red(text: str, *, color: bool) -> str:
    """Red for block/fail — colour-gated by the caller."""
    return _paint(text, _ANSI_RED, color)


def dim(text: str, *, color: bool) -> str:
    """Dim for secondary detail (e.g. the unblock hint) — colour-gated by the caller."""
    return _paint(text, _ANSI_DIM, color)


def _visible_len(s: str) -> int:
    """The display width of `s` with any ANSI colour stripped (so padding stays aligned)."""
    return len(_ANSI_RE.sub("", s))


# Unicode box-drawing set + a pure-ASCII fallback with the same keys.
_BOX = {"tl": "┌", "tr": "┐", "bl": "└", "br": "┘", "h": "─", "v": "│",
        "ml": "├", "mr": "┤", "mt": "┬", "mb": "┴", "x": "┼"}
_BOX_ASCII = {"tl": "+", "tr": "+", "bl": "+", "br": "+", "h": "-", "v": "|",
              "ml": "+", "mr": "+", "mt": "+", "mb": "+", "x": "+"}


def box(lines: Iterable[str], *, title: Optional[str] = None,
        ascii_only: bool = False) -> str:
    """A bordered box around already-formatted `lines`, with an optional title row. Unicode
    box-drawing, ASCII fallback when `ascii_only`. Padding is ANSI-aware (visible width)."""
    b = _BOX_ASCII if ascii_only else _BOX
    rows = [str(x) for x in lines]
    inner = max([_visible_len(r) for r in rows] + ([_visible_len(title)] if title else []) + [0])
    bar = lambda l, r: l + b["h"] * (inner + 2) + r          # noqa: E731
    cell = lambda s: f'{b["v"]} {s}{" " * (inner - _visible_len(s))} {b["v"]}'  # noqa: E731
    out = [bar(b["tl"], b["tr"])]
    if title is not None:
        out.append(cell(title))
        out.append(bar(b["ml"], b["mr"]))
    out.extend(cell(r) for r in rows)
    out.append(bar(b["bl"], b["br"]))
    return "\n".join(out)


def table(headers: Sequence[Any], rows: Iterable[Sequence[Any]], *,
          ascii_only: bool = False) -> str:
    """A simple aligned table with a header rule. Unicode box-drawing, ASCII fallback when
    `ascii_only`. Column widths are ANSI-aware so a coloured cell still lines up. No truncation
    — the full cell text is always shown."""
    b = _BOX_ASCII if ascii_only else _BOX
    head = [str(h) for h in headers]
    body = [[str(c) for c in row] for row in rows]
    cols = len(head)
    widths = [_visible_len(head[i]) for i in range(cols)]
    for row in body:
        for i in range(cols):
            widths[i] = max(widths[i], _visible_len(row[i]))
    rule = lambda l, m, r: l + m.join(b["h"] * (w + 2) for w in widths) + r   # noqa: E731
    line = lambda cells: b["v"] + b["v"].join(                                 # noqa: E731
        f' {c}{" " * (widths[i] - _visible_len(c))} ' for i, c in enumerate(cells)) + b["v"]
    out: List[str] = [rule(b["tl"], b["mt"], b["tr"]), line(head),
                      rule(b["ml"], b["x"], b["mr"])]
    out.extend(line(row) for row in body)
    out.append(rule(b["bl"], b["mb"], b["br"]))
    return "\n".join(out)


# ----------------------------------------------------------------- 1. gate-verdict legibility
# A SHARED one-line verdict for ANY gate decision, so completeness / spec-persisted / deviation
# / write gates all read the same way (a pass gets a one-liner too, not only a block).
_PASS_GLYPH, _BLOCK_GLYPH = "✓", "✗"
_PASS_ASCII, _BLOCK_ASCII = "[PASS]", "[BLOCK]"


def gate_verdict(name: str, passed: bool, reason: str, *, action: Optional[str] = None,
                 ascii_only: bool = False, color: bool = False) -> str:
    """One legible line for a gate decision:
        ✓ <name> passed (<reason>)
        ✗ <name> blocked — <reason>
    A blocked verdict with an `action` adds a second `→ to unblock: …` line (touch 2).

    `color` is opt-in and gated: green ✓ for a pass, red ✗ for a block, dim unblock line. It
    never fires in `ascii_only` mode, so the plain string stays byte-identical to today. Callers
    pass `color=_color_enabled(...)` so ANSI only reaches a real terminal."""
    color = color and not ascii_only
    if passed:
        head = _PASS_ASCII if ascii_only else _PASS_GLYPH
        return f"{green(head, color=color)} {name} passed ({reason})"
    head = _BLOCK_ASCII if ascii_only else _BLOCK_GLYPH
    line = f"{red(head, color=color)} {name} blocked — {reason}"
    if action:
        arrow = "->" if ascii_only else "→"
        line += "\n  " + dim(f"{arrow} to unblock: {action}", color=color)
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


def verdict(result: Any, *, ascii_only: bool = False, color: bool = False) -> str:
    """The shared one-liner for a real gate-result object (read-only). Pulls `.passed`/`.reason`
    /`.gate_id` (duck-typed across the gates) and, on a block, the canonical unblock action.
    `color` is passed through to gate_verdict (opt-in, gate-checked)."""
    passed = _result_passed(result)
    gate_id = _result_gate_id(result)
    reason = str(getattr(result, "reason", "") or "")
    action = None
    if not passed:
        if gate_id == "write" and getattr(result, "findings", None):
            action = unblock_hint("write-secret")   # a secret block — security, not approvable
        else:
            action = unblock_hint(gate_id)
    return gate_verdict(gate_id, passed, reason, action=action, ascii_only=ascii_only,
                        color=color)


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


def stage_recap(stage: str, recap: str, *, ascii_only: bool = False,
                color: bool = False) -> str:
    """`✓ <stage> done — <recap>. Next: \\`/mokata:<next>\\`` (the Next clause is omitted at the
    terminal stage). Read-only; the recap text is supplied by the caller. `color` (opt-in, gated)
    tints the ✓ green — the plain string is unchanged."""
    color = color and not ascii_only
    head = _PASS_ASCII if ascii_only else _PASS_GLYPH
    head = green(head, color=color)
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
