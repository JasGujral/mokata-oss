"""approve — the SI.3 out-of-band human approval for a durable MCP write.

This command is the DEFAULT act the model cannot perform. Every mokata write tool returns a
*proposal* and commits nothing; only an approval minted HERE — in a separate process, through a
terminal the model does not own — lets that write land, exactly once.

It is the same discipline SI.1 put on the gate override (`cli_commands/gate.py`), for the same
reason. Historically it had NO MCP tool at all; AP-MCP (doc 85 §5 D26 amendment) adds ONE bounded
exception — an in-chat `mcp__mokata__approve` tool that does the SAME act as this command — but
DEFAULT-OFF and opt-in (`settings.approvals.in_chat`), never riding the `mcp__mokata__*` auto-grant
(setup writes an explicit `permissions.ask` prompt), and reusing every invariant below verbatim
(actor=`chat-relayed` in the ledger). The reasoning that kept an approve surface out by default is
intact and remains the out-of-the-box posture: an in-harness approve surface would let the model
approve its own writes, which is precisely the hole this command closes — the opt-in only lets a
team that accepts the named residual risk redeem an approval in-flow. See `parity.py` + AP-MCP.

  * **explicit**       — you name the proposal id the model showed you. There is no "approve all",
                         and deliberately NO env-var auto-approve: an env var is a side door any
                         process can open silently, which is the opposite of a human decision.
  * **shown in full**  — the tool, the target, and what would be written are printed BEFORE the
                         question, so the thing you approve is the thing you saw.
  * **re-confirmed**   — an interactive y/N. Off a TTY it FAILS CLOSED (P2): a non-interactive shell
                         cannot approve by accident. A genuinely non-interactive HUMAN flow (CI, a
                         script) passes `--yes`, which is the human's own environment saying it —
                         never a tool parameter the model can type.
  * **single-use**     — the approval licenses exactly ONE commit of exactly the content you saw
                         (content-hashed), then it is burned.
  * **session-scoped** — it dies with the run that proposed it, and expires on a clock.
  * **ledgered**       — who / when / which write / which hash, on the hash-chained audit ledger.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""
from __future__ import annotations

import argparse
import os

from .. import MOKATA_DIR
from ._common import AuditLedger


def _root(path: str) -> str:
    """The repo whose proposals we are approving.

    Deliberately does NOT go through `_load_surface`: that exits(1) on a repo with no manifest, and
    the one write that is proposed against an UNINITIALIZED repo is `init` itself — refusing to
    approve it would make init the single tool the human could not gate. An initialized repo still
    gets the normal root-walk (so `mokata approve` works from a subdirectory); an uninitialized one
    falls back to the path as given, which is exactly where `init` staged its proposal."""
    from ..config import ConfigError, Surface
    from ..manifest import ManifestError
    try:
        return Surface.load(path).root
    except (ConfigError, ManifestError, OSError):
        return path


def _ledger(root: str):
    return AuditLedger.from_mokata_dir(os.path.join(root, MOKATA_DIR))


def cmd_approve(args: argparse.Namespace) -> int:
    """Approve ONE proposed durable write — or, with no id, list what is waiting on you."""
    from .. import approval
    from ..prompt import read_yes_no

    root = _root(args.path)

    if not args.proposal_id:
        return _list(root)

    p = approval.load(root, args.proposal_id)
    if p is None:
        print(f"error: no such proposal '{args.proposal_id}' — it never existed, expired, or was "
              f"pruned.")
        print("       Ask the model to re-run the write; it will show you a fresh proposal id.")
        return 1
    if p.status == approval.STATUS_USED:
        print(f"error: proposal '{p.proposal_id}' was already used — an approval licenses exactly "
              f"one write.")
        return 1
    if p.expired():
        print(f"error: proposal '{p.proposal_id}' has expired. Ask the model to re-run the write.")
        return 1

    # Show the write IN FULL before asking. The thing you approve is the thing you saw.
    print("mokata · a durable write is waiting for your approval:\n")
    print(approval.render(p))
    print()

    if p.status == approval.STATUS_APPROVED:
        print(f"already approved (by {p.approved_by or 'you'}) — the model can redeem it once.")
        return 0

    question = f"Approve this {p.tool} write? It commits ONCE, then the approval is burned."
    if not (args.yes or read_yes_no("", question)):
        print("aborted — nothing was approved, and nothing will be written.")
        return 1

    res = approval.approve(root, p.proposal_id, actor=args.actor, ledger=_ledger(root))
    if not res.ok:
        print(f"error: {res.message}")
        return 1

    print(f"approved: {p.proposal_id} — recorded to the audit ledger.")
    print(f"The model may now commit this ONE write by re-calling {p.tool} with "
          f"proposal_id={p.proposal_id}.")
    print("It is single-use and expires with this session — nothing to remember to turn off.")
    return 0


def _list(root: str) -> int:
    """What is waiting on the human (read-only)."""
    from .. import approval

    items = approval.pending(root)
    if not items:
        print("no durable writes are waiting for your approval.")
        return 0
    print(f"mokata · {len(items)} durable write(s) waiting for your approval:\n")
    for p in items:
        mark = "APPROVED (redeemable once)" if p.approved else "awaiting your approval"
        print(f"  {p.proposal_id}  {p.tool:16} {mark}")
        print(f"    {p.summary or p.target}")
        print(f"    expires in {p.expires_in() // 60}m — approve with: "
              f"mokata approve {p.proposal_id}")
        print()
    return 0


def register(sub, common):
    p_approve = sub.add_parser(
        "approve", parents=[common],
        help="approve ONE proposed durable write (the human act an MCP tool cannot perform)")
    p_approve.add_argument("proposal_id", nargs="?", default="",
                           help="the proposal id the model showed you (omit to list pending)")
    p_approve.add_argument("--actor", default="human",
                           help="who is approving (default: human; recorded to the ledger)")
    p_approve.add_argument("--yes", action="store_true",
                           help="skip the interactive re-confirmation (a HUMAN's own "
                                "non-interactive flow: CI, a script). Never a tool parameter.")
    p_approve.set_defaults(func=cmd_approve)
