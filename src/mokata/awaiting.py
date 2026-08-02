"""MCP-R.D2 — the ONE surface for "mokata is waiting on a human".

The worst UX in the shipped product is a WAIT that is indistinguishable from a HANG (P16). A
gated write returns a proposal and stops; nothing is broken, nothing is stuck — but if the
proposal-id and the two commands that resolve it aren't the LOUDEST thing in the reply, a
correct, healthy wait reads as a wedged server. That was B-AMEND-STUCK: `spec_amend` already
returned everything the user needed, buried under a dozen other keys.

This module owns three read-only views of that one state, so no surface can drift from another:

  * `awaiting_block`  — the uniform dict head every propose-path result leads with (`mcp.consent
                        ._propose` splices it in, so all 19 propose sites inherit it at once).
  * `pending_lines`   — the `doctor` reporter: what is waiting on you and the road back.
  * `signal_wait`     — which channels a given wait actually raises (harness prompt vs mokata's
                        own statusline / result text), so the caller can say so out loud.

Everything here is **pure, deterministic, read-only** and degrade-clean: it formats state the
approval store already holds (`approval.pending`), and never writes, never gates, never raises.
D2 is SURFACING, not re-gating — the gates themselves are untouched (P2).

SECRET-SAFETY: a proposal carries a `summary` and a `preview` of the content it would write.
Those are the human's to read at the terminal via `mokata approve <id>` — they are NEVER rendered
here. Every surface in this module names the proposal_id, the tool, and the commands, and nothing
else. That keeps a DSN, a token, or a spec's contents out of the statusline, out of doctor's
output, and out of a tool result that a transcript may retain.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

# The loud head. One string, one place — a surface that greps for "AWAITING APPROVAL" finds every
# wait, and a reworded copy in some tool's prose can't exist because no tool writes its own.
AWAITING = "AWAITING APPROVAL"

# The two commands that ALWAYS resolve a wait. `approve` redeems it; `--list` is the road back for
# a user who lost the id (the answer to "the call returned nothing — was that a human NO?").
APPROVE_CMD = "mokata approve {proposal_id}"
LIST_CMD = "mokata approve --list"

# The amend path's abort. Named here (not in `tools_spec`) so doctor and the tool result can't
# disagree about what the escape hatch is called.
AMEND_ABORT_CMD = "mokata spec amend --abort"
AMEND_TOOL_NAME = "spec_amend"

# The regressed-state sentence for an in-flight amendment. This is the FACT that makes the wait
# urgent rather than ignorable: development writes are blocked while it stands.
AMEND_REGRESSED_NOTE = ("the run has REGRESSED to the SPEC phase — development writes are BLOCKED "
                        "until this amendment is approved or aborted.")


def awaiting_block(proposal_id: str, tool: str, *, blocked_note: str = "",
                   approved: bool = False,
                   also_pending: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """The uniform head of every propose-path result: what state this is, and the exact commands
    out of it. Returned as an ordered dict the caller splices in FIRST, so a model (or a human
    reading raw JSON) hits the answer before any payload detail.

    `blocked_note` names what this wait is BLOCKING, when it blocks something (the amend path's
    regression). Empty for a wait that blocks nothing — most of them — because inventing a
    consequence where there isn't one is its own kind of lie.

    `approved` flips the head to the redeem instruction: a human has already said yes out-of-band,
    so the next move is the re-call, not another trip to the terminal.

    RE-ENTRY — `also_pending` names the OTHER live proposals this same tool already has waiting on
    the human. D2 fixed a wait that read as a HANG because its data arrived buried; this fixes the
    next turn of the same screw, RE-PROPOSING BLIND. An amendment has REGRESSED the run, so the
    model re-calls — and a reworded `reason` is different content, so it correctly gets a different
    id, and the answer then talks only about the NEW proposal while the OLD one is the thing
    actually holding the run down. The user is told to approve an id that resolves nothing. Naming
    the others turns "here is another wait" into "here is every road out", and it is built HERE so
    all nineteen propose sites inherit it and no tool can word it its own way."""
    approve_cmd = APPROVE_CMD.format(proposal_id=proposal_id)
    others = [p for p in (also_pending or []) if p and p != proposal_id]
    if approved:
        head = (f"{AWAITING}: ALREADY APPROVED — a human has approved this write. Nothing has "
                f"been written yet. Re-call {tool} with proposal_id=\"{proposal_id}\" to commit "
                f"it (once).")
    else:
        head = (f"{AWAITING}: mokata is WAITING ON A HUMAN — this is not an error and the server "
                f"is not stuck. NOTHING was written. Ask the human to run:  {approve_cmd}  — "
                f"then re-call {tool} with proposal_id=\"{proposal_id}\".")
    if others:
        head += (f"  NOTE: {len(others)} earlier {tool} write(s) are ALREADY awaiting this human "
                 f"({', '.join(others)}) — approving one of THOSE may be what you actually want; "
                 f"re-calling with changed arguments stages a new write, it does not replace an "
                 f"old one.")
    block: Dict[str, Any] = {
        "awaiting": head,
        "awaiting_proposal_id": proposal_id,
        "awaiting_approve_command": approve_cmd,
        "awaiting_list_command": LIST_CMD,
    }
    if tool == AMEND_TOOL_NAME:
        block["awaiting_abort_command"] = AMEND_ABORT_CMD
    if blocked_note:
        block["awaiting_blocks"] = blocked_note
    if others:
        block["awaiting_also_pending"] = others
        block["awaiting_also_pending_approve_commands"] = [
            APPROVE_CMD.format(proposal_id=p) for p in others]
    return block


# ======================================================================================
# the doctor reporter — "what is waiting on you", in the shared-reporter style
# ======================================================================================

def _pending(root: str) -> List[Any]:
    """Live proposals for `root`, newest first.

    This deliberately does NOT catch. An unreadable approval store swallowed here would surface as
    an empty list, and an empty list is rendered by `pending_lines` as "nothing is waiting on you
    ✓" — a HEALTH CLAIM mokata did not verify, on the exact surface a stuck user runs to find out
    why everything is blocked. That is the failure mode this whole stage exists to eliminate, so
    it must not be reintroduced by the diagnostic itself. Each caller decides instead: the doctor
    reporter names the cause, the cosmetic statusline stays quiet."""
    from . import approval
    return list(approval.pending(root))


def pending_lines(root: str = ".", *, quiet_when_ok: bool = True) -> List[str]:
    """The shared "waiting on you" report block, in the same style as `parity_lines` /
    `skills_visibility_lines`. Loud-only by default; `doctor` passes `quiet_when_ok=False` to
    also show the nothing-pending line.

    INFORMATIONAL: this reports state, it never judges it. A pending proposal is mokata working
    CORRECTLY — the gate is the point (P2) — so it is never a doctor finding and never touches
    doctor's exit code. Never raises."""
    try:
        proposals = _pending(root)
        if not proposals:
            return [] if quiet_when_ok else [
                "mokata pending: nothing is waiting on you ✓"]

        lines = [f"mokata pending: {len(proposals)} write(s) awaiting YOUR approval "
                 f"— mokata is waiting, not stuck"]
        for p in proposals:
            # NAME the tool + id + the road back. Never `p.summary` / `p.preview` — those carry
            # the proposal's CONTENT (see the module docstring).
            tool = getattr(p, "tool", "?")
            pid = getattr(p, "proposal_id", "?")
            if tool == AMEND_TOOL_NAME:
                lines.append(f"  amendment pending — approve {pid} or --abort")
                lines.append(f"    {AMEND_REGRESSED_NOTE}")
                lines.append(f"    Fix: `{APPROVE_CMD.format(proposal_id=pid)}`  "
                             f"or  `{AMEND_ABORT_CMD}`")
            else:
                lines.append(f"  {tool} pending — approve {pid}")
                lines.append(f"    Fix: `{APPROVE_CMD.format(proposal_id=pid)}`")
        lines.append(f"  (list them any time: `{LIST_CMD}`)")
        return lines
    except Exception as exc:                 # informational path — never raise
        return [f"mokata pending: check skipped ({exc})."]


def liveness_lines() -> List[str]:
    """The doctor liveness line (UX-STUCK): a statement that MCP calls are BOUNDED, quoting D0's
    actual budgets.

    This deliberately measures nothing and probes nothing — D0 already delivered the never-hang
    half (the R1 wall-clock cap on every served call, plus `baseline`'s own named bound), and
    rebuilding it here as a latency probe would be a second authority on a question D0 already
    answers. What was missing was only that a human staring at a slow call had no way to know a
    bound existed at all. So this READS D0's constants rather than restating them: if a budget is
    ever retuned, this line moves with it and cannot drift.

    Informational; never raises, never affects doctor's exit."""
    try:
        from .baseline import BASELINE_MCP_TIMEOUT_SECONDS
        from .mcp.server import MCP_SURFACE_TIMEOUT_SECONDS
        return [
            f"mokata liveness: every MCP call is BOUNDED — "
            f"{MCP_SURFACE_TIMEOUT_SECONDS:g}s interactive budget, "
            f"{BASELINE_MCP_TIMEOUT_SECONDS:g}s for `baseline` (it runs the test suite).",
            "  A call that exceeds its budget returns status `timed_out` naming the operation — "
            "mokata never answers with silence, so silence is the harness, not the server.",
        ]
    except Exception as exc:                 # informational path — never raise
        return [f"mokata liveness: budget check skipped ({exc})."]


def statusline_segment(root: str = ".") -> str:
    """The mokata-OWNED wait signal for a harness that raises none of its own: a statusline
    segment naming the wait and its id. "" when nothing is pending (so a healthy session's
    statusline stays byte-identical). No daemon — the pending set is already on disk, and this
    renders on the statusline tick the harness was going to run anyway. Never raises."""
    try:
        proposals = _pending(root)
        if not proposals:
            return ""
        newest = proposals[0]
        pid = getattr(newest, "proposal_id", "?")
        if len(proposals) > 1:
            return f"⏳ awaiting approval {pid} (+{len(proposals) - 1} more)"
        return f"⏳ awaiting approval {pid}"
    except Exception:
        return ""


# ======================================================================================
# UX-NOTIFY — which channel does THIS wait actually raise?
# ======================================================================================
# Grounded, not assumed. Claude Code has a `Notification` hook event whose `permission_prompt`
# matcher fires exactly when it PROMPTS the human for a tool permission (its `idle_prompt` matcher
# covers the waiting-for-input case). So a wait that raises a permission prompt ALREADY rides a
# harness notification — verified, not assumed. mokata cannot and must not synthesize that event:
# it is not the harness, and a tool that fakes a harness notification lies about who is asking.
#
# The DECLINE half has no such guarantee. Claude Code documents `PermissionDenied` for auto-mode
# classifier denials, but what the model receives when a HUMAN clicks Deny on the prompt is not
# specified — it may be nothing at all. That gap is precisely why D2's answer to a decline is a
# DESCRIPTION contract (tell the model: a silent return is a human NO, go check `mokata approve
# --list`) rather than structural detection of an outcome the harness may never send.
#
# So the channel a wait rides is decided by the GRANT state mokata itself writes at setup
# (`harness_setup._merge_grant`):
#   * `mcp__mokata__approve` is in `permissions.ask` -> calling it PROMPTS -> the harness raises
#     its notification. mokata adds nothing; the channel is already there (AP-MCP, doc 85 §5 D26).
#   * every other write tool rides the `mcp__mokata__*` allow-wildcard -> NO prompt, so NO harness
#     notification. The wait would be silent, and these are mokata's to signal: the statusline
#     segment above + the loud `awaiting` head in the tool result.
# An UNGRANTED repo prompts on everything (the stuck-loop `mokata setup` fixes) — every wait then
# rides the harness, and mokata's own channels still render. Degrade-clean either way: no channel
# is required, none is fatal, and nothing here can block a call.

HARNESS_NOTIFICATION = "harness-notification"
STATUSLINE = "statusline"
TOOL_RESULT = "tool-result"


def signal_wait(root: str = ".", *, tool: str = "", proposal_id: str = "") -> List[str]:
    """The channels this wait-on-human raises, most-authoritative first.

    A pure classification of an already-existing wait — it fires nothing, writes nothing, and
    starts nothing. `TOOL_RESULT` is unconditional: the `awaiting` head always ships in the
    result, which is why a wait is legible even on a harness with no statusline and no
    notification event. Never raises."""
    channels: List[str] = []
    if _rides_harness_prompt(root, tool):
        channels.append(HARNESS_NOTIFICATION)
    if _statusline_wired(root):
        channels.append(STATUSLINE)
    channels.append(TOOL_RESULT)
    return channels


def _rides_harness_prompt(root: str, tool: str) -> bool:
    """True when calling `tool` makes Claude Code PROMPT the human — which is the harness raising
    its OWN notification, the channel mokata must not duplicate. Two ways in: the tool is the
    explicitly-asked `approve` tool, or this repo has no allow-grant at all (so everything
    prompts). Degrade-clean: an unreadable settings file reads as "no harness channel", which
    only ever makes mokata's own signal MORE visible, never less."""
    try:
        from .harness_paths import MCP_APPROVE_TOOL
        from .mcp_admin import grant_status
        if MCP_APPROVE_TOOL.endswith(f"__{tool}"):
            return True
        return not grant_status(root).permitted
    except Exception:
        return False


def _statusline_wired(root: str) -> bool:
    """True when mokata's statusline badge is on for this repo — i.e. `statusline_segment` will
    actually reach a human. Degrade-clean: unknown reads as False."""
    try:
        from .config import Surface
        from .progress import statusline_enabled
        if not Surface.is_initialized(root):
            return False
        return bool(statusline_enabled(Surface.load(root)))
    except Exception:
        return False


__all__ = ["AMEND_ABORT_CMD", "AMEND_REGRESSED_NOTE", "AMEND_TOOL_NAME", "APPROVE_CMD",
           "AWAITING", "HARNESS_NOTIFICATION", "LIST_CMD", "STATUSLINE", "TOOL_RESULT",
           "awaiting_block", "pending_lines", "signal_wait", "statusline_segment"]
