"""AP-MCP — the in-chat approval tool: `approve(proposal_id)`, DEFAULT-OFF, opt-in (0.0.14).

A DELIBERATE, Jas-ledgered override of the SI.3 "no in-harness approve surface" settled decision
(doc 85 §5 D26 amendment; doc 84 AP-MCP). SI.3 gave the approval NO MCP tool for a precise reason:
an in-harness approve surface lets the MODEL approve its own writes — the proposal id is
model-visible, so `approve(id)` is `approve=true` wearing an id costume. That reasoning stays TRUE.
AP-MCP does not refute it; it BOUNDS it and makes it an explicit opt-in, for teams that accept the
named residual risk in exchange for the in-flow UX.

What this tool is, exactly
--------------------------
It performs the SAME act as `mokata approve <id>` — nothing more. It approves ONE existing,
human-shown proposal. It NEVER creates, modifies, redeems, or auto-approves a proposal, and it has
NO way to approve more than one: `proposal_id` is the only input — no boolean, no batch, no list,
no env var, no `approve=true`. Every SI.3 invariant is untouched because the tool reuses
`approval.approve` verbatim: single-use, content-hash-bound, session-scoped, clock-expiry, no
approve-all, hash-chain ledgered. The one thing it changes is WHERE the flip is typed — and it
records that honestly as `actor="chat-relayed"`, distinguishable in the audit trail from a TTY
approval's `actor="human"`.

The four binding bounds (all enforced here + in setup)
------------------------------------------------------
  1. DEFAULT-OFF. `settings.approvals.in_chat` defaults false; while off the tool REFUSES and names
     the enable path. Enabling is itself a human-gated TTY config write (`mokata config set` →
     `mokata approve <that write>`), ledgered — the model cannot flip its own opt-in on.
  2. NEVER rides the auto-grant. `setup`/`mcp install` write an explicit `permissions.ask` entry
     for `mcp__mokata__approve`, so Claude Code PROMPTS the human on every call even though the
     `mcp__mokata__*` allow-wildcard already matches it (harness_setup._merge_grant).
  3. The result echoes `approval.render(p)` IN FULL + the ledger entry id, so the transcript
     carries mokata's OWN ground-truth content next to the approval.
  4. Registered, not hidden: this tool's `kind` is its own ("approve") — NOT "write" — so the SI.3
     write-tool sweeps (which prove no propose-only tool commits on a model-typed param) never
     touch it. It is the approval act, not a gated write.

Why "always registered, refuse when off" and not "don't register"
-----------------------------------------------------------------
The `TOOLS` registry is static (populated at import) and `build_server` registers every tool
unconditionally, with the repo path arriving PER CALL — so "not registered when off" is not
cleanly expressible in this lifecycle. The clean, honest posture is the same one `audit_share`
uses for `settings.audit.shared`: the tool is present but refuses at call time with a message
naming the enable path. A refusal the human can act on beats a tool that silently vanishes.

Stdlib-only; SDK-free. Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .. import approval
from ..govern import AuditLedger
from .registry import _mokata_dir, _tool

__all__ = ["approve"]

# The chat-relayed actor recorded on the ledger + the proposal — the honest label of WHERE the
# flip was typed. A TTY approval records "human" (cli_commands/approve.py --actor default); this
# one is `chat-relayed`, so the audit trail can always tell an in-chat approval apart.
CHAT_ACTOR = "chat-relayed"

# The dotted manifest key that gates this tool (default: false).
IN_CHAT_SETTING = "settings.approvals.in_chat"


def _in_chat_enabled(path: str) -> bool:
    """True only when the team has EXPLICITLY opted in (`settings.approvals.in_chat`). Default
    false, and degrade-clean: an uninitialized/unreadable manifest reads as OFF (fail-closed —
    the model cannot get an in-chat approve surface out of a broken config)."""
    from .. import config_cmd
    try:
        found, value = config_cmd.config_get(path, IN_CHAT_SETTING)
    except Exception:  # noqa: BLE001 - a broken/absent manifest must read as OFF, never crash the
        return False   #                tool call; D5-classed DEGRADE_CLEAN (fail-closed default).
    return bool(found and value)


def _disabled(path: str) -> Dict[str, Any]:
    """The default posture: in-chat approval is off. Name the human-gated TTY enable path — never
    approve, never propose, never touch the proposal."""
    return {
        "status": "disabled", "committed": False, "approved": False,
        "message": (
            "in-chat approval is OFF (the default). This tool cannot approve anything until a "
            f"human enables it with a gated config write:  mokata config set {IN_CHAT_SETTING} "
            "true  (then approve THAT write with `mokata approve <id>`). Until then, approve at "
            "the terminal with `mokata approve <id>` — the model may reference a proposal, never "
            "mint its own consent."),
    }


def _refused(code: str, p: Optional[approval.Proposal] = None) -> Dict[str, Any]:
    """A named refusal, mirroring approve.py's message class — the tool approved nothing."""
    out: Dict[str, Any] = {
        "status": "refused", "committed": False, "approved": False,
        "reason_code": code,
        "reason": approval._REFUSAL_TEXT.get(code, "refused"),
        "hint": ("this tool approves ONE existing, human-shown proposal by id — it never creates "
                 "or redeems one. Ask the model to re-run the write for a fresh proposal id."),
    }
    if p is not None:
        out["render"] = approval.render(p)
    return out


@_tool("approve", "approve")
def approve(path: str = ".", proposal_id: str = "") -> Dict[str, Any]:
    """Approve ONE existing, human-shown durable-write proposal in-flow — the AP-MCP opt-in
    (doc 85 §5 D26 amendment). DEFAULT-OFF: until a human enables `settings.approvals.in_chat`
    (itself a gated config write), this tool REFUSES and names the terminal path. This is NOT a
    boolean gate: `proposal_id` is the ONLY input — there is no `approve=true`, no batch, no
    approve-all, no env auto-approve. It performs the SAME act as `mokata approve <id>`
    (actor=`chat-relayed`, distinguishable in the audit ledger), reuses every SI.3 invariant
    verbatim (single-use, content-hash-bound, session-scoped, clock-expiry), and NEVER creates,
    modifies, or redeems a proposal. The result echoes mokata's OWN full render of the proposal
    plus the ledger entry id, so the transcript carries what was actually approved. Even with the
    `mcp__mokata__*` grant present, `setup` wires an explicit prompt on this tool — the human is
    asked on every call."""
    if not _in_chat_enabled(path):
        return _disabled(path)

    if not proposal_id:
        return _refused(approval.REFUSED_UNKNOWN)

    # Screen the plain refusals FIRST (unknown / used / expired), so a refusal never writes and the
    # message class matches `mokata approve`. The approve call below re-checks under the store's
    # lock — this is the friendly pre-check, not the authority.
    p = approval.load(path, proposal_id)
    if p is None:
        return _refused(approval.REFUSED_UNKNOWN)
    if p.status == approval.STATUS_USED:
        return _refused(approval.REFUSED_USED, p)
    if p.expired():
        return _refused(approval.REFUSED_EXPIRED, p)

    mokata_dir = _mokata_dir(path)
    ledger = AuditLedger.from_mokata_dir(mokata_dir)
    res = approval.approve(path, proposal_id, actor=CHAT_ACTOR, ledger=ledger)
    if not res.ok:
        # A race lost the pre-check (expired/used between load and approve) — report it by name.
        code = res.code if res.code in approval._REFUSAL_TEXT else approval.REFUSED_UNKNOWN
        return _refused(code, res.proposal)

    approved = res.proposal
    out: Dict[str, Any] = {
        "status": "approved", "committed": False, "approved": True,
        "proposal_id": approved.proposal_id, "tool": approved.tool,
        "actor": CHAT_ACTOR,
        "render": approval.render(approved),
        "message": (f"approved (in-chat) — {approved.tool} may now commit this ONE write by "
                    f"re-calling with proposal_id=\"{approved.proposal_id}\". Single-use; it "
                    "burns on redeem and expires with this session."),
    }
    seq = _approval_seq(ledger, approved.proposal_id)
    if seq is not None:
        out["ledger_seq"] = seq
    return out


def _approval_seq(ledger: Any, proposal_id: str) -> Optional[int]:
    """The seq of the `approved` ledger entry this call just wrote — the audit id echoed back so
    the transcript can point at exactly the record. Best-effort: a read failure omits the field
    (the approval already committed; the id is a convenience, not the authority)."""
    try:
        for e in reversed(ledger.entries()):
            if (e.get("kind") == approval.LEDGER_KIND and e.get("decision") == "approved"
                    and e.get("proposal") == proposal_id):
                return e.get("seq")
    except Exception:  # noqa: BLE001 - the echo is cosmetic; never fail the approval over it.
        return None
    return None
