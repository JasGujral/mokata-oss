"""The CONSENT BOUNDARY for the human-gated MCP write tools — the whole of SI.3 in one place.

Extracted from `mcp/tools_write.py` (PRE-SIMP, release 0.0.15) so the write tools regroup into domain
modules while the consent machinery lives ONCE, here, and every domain tool routes through it. These
five helpers plus `_gated_write` are the ONLY way a durable MCP write reaches the WriteGate under a
verified, human-minted approval: there is deliberately no argument a model could pass to reach
GRANTED — the only way in is a record a human wrote, from a terminal, in another process.

    1. the model calls the tool                  -> status "proposed" + a proposal_id. Nothing writes.
    2. the HUMAN runs `mokata approve <id>`      -> a separate process, a TTY re-confirm, ledgered.
    3. the model re-calls with proposal_id=<id>  -> verified, committed once, approval BURNED.

`approve`/`confirm` are still ACCEPTED (schema stability — no caller breaks) but DEMOTED: they commit
nothing. The consent lives in `approval.py`: content-hashed (what was approved is what commits),
single-use, session-scoped, TTL-bounded, and recorded on the hash-chained audit ledger, which links
proposal -> human -> the commit it licensed.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional

from .. import approval
from ..govern import AuditLedger, WriteGate, WriteRequest
from ..govern.trust import (MCP_SURFACE, REFUSED_READ_ONLY, TrustPolicy,
                            WritePolicy)
from .registry import _mokata_dir, _surface

if TYPE_CHECKING:                                  # type-only; no import cost at runtime
    from ..config import Surface


# ======================================================================================
# the consent boundary — the whole of SI.3 lives in these five helpers
# ======================================================================================

def _approval_run(path: str) -> str:
    """RE-ENTRY — the run an approval belongs to. THE seam this whole stage turns on, and the one
    place both halves of the chain (`_propose` records under it, `_consent` reads under it) get
    their answer, so the record-key IS the read-key by construction.

    It was `current_run_id()`, which IS the process's `session_id` (`session.py`: "equals the
    session_id" by design). That made a human's consent an artifact of process lifetime: restart the
    MCP server, or go back to `/brainstorm` in a second window, and the SAME human approving the
    SAME content in the SAME repo minted a DIFFERENT proposal id — the genuine approval came back
    `other-session`, re-proposing filled the store with one pending record per restart, and the user
    was walked to a terminal again on every single call. That is the live 2026-07-27 wedge, and it
    is REVIEW-FIX.R1's filed residual class (record-key != read-key) landing in the approval chain.

    WHY A STABLE ID IS NOT A WEAKENING OF CONSENT
    ---------------------------------------------
    Every property SI.3 actually rests on is untouched, because none of them lives in the run:

      * CONTENT-BOUND — the id's pre-image still carries the full `content_hash` over tool + args.
        Change an argument and the id changes; "approve X, then commit Y" stays arithmetically
        impossible, not merely discouraged.
      * MODEL-CANNOT-MINT — redemption still requires an on-disk record a HUMAN wrote from a
        terminal in a process the model is not driving. There is still no argument that reaches
        GRANTED. That is the stage's whole claim and this changes nothing about it.
      * ONE-APPROVAL-ONE-WRITE — the burn (`REFUSED_USED`) is enforced by the store lock at
        redemption, not by the key.
      * EXPIRY — the TTL is on the record.
      * FOREIGN-RUN — `REFUSED_OTHER_SESSION` still fires; it now asks the RIGHT question ("does
        this approval belong to the pipeline I am writing to?") instead of a question about which
        OS process happened to be alive.

    What changes is only the SCOPE of "the same context": from *this process* to *this pipeline run
    in this repo*. A human at a terminal approved this exact content for this exact pipeline; an MCP
    restart is not a change of human, of repo, or of content. Binding consent to a process lifetime
    was never a security property — it was a side effect of `run_id == session_id`, and it made a
    genuine approval unredeemable, which taught users to re-approve reflexively. Consent fatigue is
    a weakening of the gate, not a hardening of it.

    RUN-ID-DRIFT — resolution is THE resolver (`run_resolver.resolve_run`), and it now carries an
    OWN rung that matters here: an MCP process that registered its own run resolves to that run
    directly, so a repo with five live windows no longer drops every one of them to the fallback
    below. What remains unfixed is the RESTART case with no evidence yet on disk (approve an
    approach, restart the server before it persists): the new process's own run is a new id, and no
    rung can tie it to the old one until the harness exposes Claude's session id to MCP (#25642).

    FAIL-CLOSED, unchanged. When the ladder cannot tell (2+ pipelines, no pin, no own run) mokata
    does NOT guess — it falls back to this process's own session id, the NARROWEST key available.
    So an unresolvable repo can only ever be stricter than a resolved one: the fallback can refuse
    a redemption that a guess would have granted, never the reverse. Nothing on this path can let a
    write through without a human-minted record; the key decides only WHICH id a content maps to,
    never whether it commits.
    """
    from ..session import current_run_id
    try:
        from ..run_resolver import resolve_run_id
        return resolve_run_id(path) or current_run_id()
    except Exception:                    # noqa: BLE001 — resolution is never worth failing a call
        return current_run_id()


def _evidence_store(surface: "Surface", path: str):
    """STATE-SCOPE — the state store scoped to the run `_approval_run` keys to, plus that run id.

    The MCP twin of `cli_commands/spec.py:_run_scoped_store`, which the CLI has had since
    HANDOFF.G1. `Surface.state` is scoped to `session.current_session_id()` (MS.S2), so the MCP
    surface could RESOLVE the pipeline's run and then read a different one's state:

        load_approved_approach(surface.state)  -> None      # the Handoff is filed under sessA
        spec_amend from sessB                  -> blocked at `spec-persisted`, never at consent

    and — measured at the RE-ENTRY close-out — BOTH `spec_emit` refusal gates went SILENT across a
    session boundary. Neither mis-fired; both simply stopped seeing the evidence they gate on. A
    gate that quietly isn't there is worse than one that blocks.

    IT IS A READ/WRITE PAIR, AND THAT IS NOT A CONVENIENCE
    -----------------------------------------------------
    Fixing only the READS would leave RE-ENTRY's keying fix SINGLE-SHOT. The proposal resolves to
    the pipeline's run, the human approves it, and the commit then lands under the WRITING
    session's scope — creating a SECOND `emitted_spec__<run>`, i.e. a second EVIDENCE run. From the
    next call on, `run_resolver._evidence_run` sees 2+ and correctly declines to choose, resolution
    degrades back to per-process keying, and the human is walked to a terminal again. One
    cross-session commit would undo the fix. So the same resolved run scopes the write.

    ONE SEAM, NOT A FOURTH RESOLUTION PATH. The run comes from `_approval_run` — i.e. from
    `run_resolver.resolve_run`, the same tiers the CLI emit path and the approval chain
    already use. That makes the invariant structural rather than remembered: **the run a proposal
    is filed under IS the run the state is scoped to.** If those two could disagree, a gate would
    read one pipeline's evidence and license another pipeline's write.

    FAIL-CLOSED, unchanged. When the run is unresolvable (2+ pipelines and no pin) `_approval_run`
    does NOT guess — it falls back to this process's own id, the narrowest scope there is and
    byte-for-byte today's behaviour. Both halves of the pair degrade together, so they cannot drift
    apart on ambiguity either. A standalone repo (no run state at all) resolves the same way and
    therefore addresses exactly the files `surface.state` addresses today.

    Never raises on resolution: `_approval_run` absorbs any fault into that narrowest fallback."""
    from ..session_state import scoped_store
    from ..state import StateStore
    from ..tdd_state import state_dir
    run_id = _approval_run(path)
    return scoped_store(StateStore(state_dir(surface.root)), run_id), run_id


def _trust(path: str, surface: Optional["Surface"] = None) -> TrustPolicy:
    """This repo's trust dial. Degrade-clean: an UNINITIALIZED repo has no manifest and therefore no
    dial, which must read as the gated-write default — otherwise `init` itself, the one tool whose
    job is to create the manifest, could never run.

    MCP-SURF — `surface` is an OPTIONAL already-resolved `Surface`. A write tool that has ALREADY
    paid for `Surface.load` at its top threads it in here so the dial is read off that same object
    instead of re-parsing the manifest and re-wiring Router+Detector a second (and, via `_policy`, a
    third) time in one tool call. Omitted, this loads from `path` exactly as before — every existing
    caller is untouched. Both modes degrade identically: the try/except still covers the
    surface-passed path, so an uninitialized repo reaching either branch reads as the gated
    default."""
    from ..config import ConfigError
    from ..manifest import ManifestError
    try:
        return TrustPolicy.from_surface(surface if surface is not None else _surface(path))
    except (ConfigError, ManifestError, OSError):
        return TrustPolicy()


def _policy(path: str, tool: str, *, human_approved: bool = False,
            surface: Optional["Surface"] = None) -> WritePolicy:
    """The ONE seam (SI.4), resolved once at the MCP boundary and threaded down to whichever
    WriteGate actually performs the write — `_gated_write`'s, or one buried in config_cmd /
    session_bundle / memory / team, which is where several of these tools really commit.

    MCP-SURF — `surface` is passed straight through to `_trust` (see there); omitted, unchanged."""
    return WritePolicy(trust=_trust(path, surface), tool=tool, surface=MCP_SURFACE,
                       human_approved=human_approved)


def _consent(path: str, tool: str, args: Dict[str, Any],
             proposal_id: str, *, surface: Optional["Surface"] = None) -> approval.Consent:
    """THE gate. Two questions, in order.

    1. MAY this tool write at all? (K3/SI.4) A `read-only` tool is refused HERE, before it proposes
       — a proposal a human could never redeem is worse than useless: it would walk them to a
       terminal to approve a write that is structurally incapable of committing. The refusal is
       recorded on the audit ledger, because a blocked write is a decision like any other (I3).
    2. Is there a human-minted approval for this EXACT call? `proposal_id` names one, and it is
       verified (content hash + freshness + single-use + session). No id -> NEEDED, and the tool
       proposes.

    There is deliberately no argument here a model could pass to reach GRANTED — the only way in is
    a record a human wrote, from a terminal, in another process.

    MCP-SURF — `surface` is an OPTIONAL already-resolved `Surface`, forwarded to `_trust` so a tool
    that resolved one at its top does not pay for a second `Surface.load` here. It feeds the trust
    READ only; it cannot reach the consent decision, which still comes solely from
    `approval.consent` over a record a human minted out-of-band."""
    if not _trust(path, surface).can_write(tool, MCP_SURFACE):
        AuditLedger.from_mokata_dir(_mokata_dir(path)).record(
            "write_gate", write_kind="mcp", target=f"tool:{tool}", actor="mcp",
            decision="blocked", reason="read-only trust")
        return approval.Consent(
            approval.REFUSED, code=REFUSED_READ_ONLY,
            reason=(f"'{tool}' is read-only on the mcp surface — writes are not permitted "
                    f"(settings.trust)"))
    return approval.consent(path, tool=tool, args=args, run_id=_approval_run(path),
                            proposal_id=proposal_id)


def _require(gate: approval.Consent) -> approval.Proposal:
    """The structural tripwire under every commit path in this module.

    Reaching a durable write without a GRANTED consent is a BUG, not a user input: no combination of
    tool parameters can produce it (that is the stage's whole claim, and `test_si_3_*` sweeps every
    tool × every consent-ish param to prove it). If it ever happens, refuse loudly rather than
    write."""
    if gate is None or not gate.granted or gate.proposal is None:
        raise RuntimeError(
            "mokata internal: a durable write reached the commit path without a human-minted "
            "approval (SI.3). Refusing to write.")
    return gate.proposal


def _refused(gate: approval.Consent) -> Dict[str, Any]:
    """This write is refused. Say WHICH way — a read-only dial and a bad approval are different
    problems, and only one of them has a next step the model can take."""
    if gate.code == REFUSED_READ_ONLY:
        return {
            "status": "refused", "committed": False,
            "reason_code": gate.code, "reason": gate.reason,
            "hint": ("this is a CONFIGURATION bound, not a missing approval — no proposal, and no "
                     "human approval, can lift it. A human must change `settings.trust` in the "
                     "manifest to allow this tool to write."),
        }
    return {
        "status": "refused", "committed": False,
        "reason_code": gate.code, "reason": gate.reason,
        "hint": ("re-call with no proposal_id to get a fresh proposal, then ask the human to run "
                 "`mokata approve <id>`. An approval is bound to one write, one session, one use."),
    }


def _other_pending(path: str, tool: str, proposal_id: str) -> list:
    """The OTHER live proposals this tool already has awaiting the human, newest first.

    Feeds the shared `awaiting_block` head so a re-call never re-proposes BLIND — see that
    builder's docstring for why silence here is what turns a regressed amendment into a loop. Ids
    ONLY: a proposal carries `summary`/`preview`, which are the human's to read at their own
    terminal, and this string lands in a tool result a transcript may retain (the `awaiting`
    module's secret-safety rule). Degrade-clean — an unreadable store names no others rather than
    failing the propose."""
    try:
        return [p.proposal_id for p in approval.pending(path)
                if p.tool == tool and p.proposal_id != proposal_id]
    except Exception:                    # noqa: BLE001 — a surfacing read never breaks a propose
        return []


def _propose(path: str, tool: str, args: Dict[str, Any], payload: Dict[str, Any], *,
             target: str = "", summary: str = "", preview: str = "",
             blocked_note: str = "",
             approve: bool = False, confirm: Optional[bool] = None) -> Dict[str, Any]:
    """Stage the write, return what WOULD change, and tell the model how a human approves it.

    MCP-R.D2 (B-AMEND-STUCK) — the result LEADS with the shared `awaiting_block`: the proposal-id,
    what this wait blocks, and the exact commands out of it, spliced in BEFORE `status` and before
    any payload detail. This is the ONE place that head is built, so all nineteen propose sites
    inherit the identical shape and no tool can write its own wording. The bug it fixes was never
    missing data — `spec_amend` already returned every field a stuck user needed — it was that the
    data arrived buried, so a healthy WAIT read as a HANG (P16).

    `blocked_note` names what this wait is blocking, for the paths where it blocks something (the
    amend regression). Everything else is unchanged: same proposal, same gate, same commit path —
    D2 surfaces state, it never re-gates it (P2)."""
    from ..awaiting import awaiting_block
    p = approval.propose(path, tool=tool, args=args, run_id=_approval_run(path),
                         target=target, summary=summary, preview=preview)
    # The loud head FIRST (dicts preserve insertion order — this is what makes it the first thing
    # a model or a human reading raw JSON hits), then the unchanged fields, then the payload.
    out: Dict[str, Any] = awaiting_block(p.proposal_id, tool, blocked_note=blocked_note,
                                         approved=p.approved,
                                         also_pending=_other_pending(path, tool, p.proposal_id))
    out.update({"status": "proposed", "action": tool, "committed": False,
                "proposal_id": p.proposal_id, "expires_in": p.expires_in(),
                "approved": p.approved})
    out.update(payload)
    if p.approved:
        out["hint"] = (f"a human has ALREADY approved this write — re-call {tool} with "
                       f"proposal_id=\"{p.proposal_id}\" to commit it (once).")
    else:
        out["hint"] = (f"NOTHING was written. Ask the human to run:  mokata approve {p.proposal_id}"
                       f"  — then re-call {tool} with proposal_id=\"{p.proposal_id}\".")
    if approve or confirm:
        out["note"] = ("`approve`/`confirm` no longer commit: an approval is MINTED BY A HUMAN "
                       "out-of-band (`mokata approve <id>`) and can only be REFERENCED here by id. "
                       "Typing approve=true is not consent — it never was.")
    return out


def _record(mokata_dir: str, gate: approval.Consent, *, write_seq: Any = None,
            committed: bool = True) -> None:
    """Close the audit chain for a commit that did NOT go through `_gated_write` (the tools that own
    their own gated commit): proposal-hash → who approved → what landed."""
    approval.record_redemption(AuditLedger.from_mokata_dir(mokata_dir), _require(gate),
                               write_seq=write_seq, committed=committed)


def _gated_write(mokata_dir: str, kind: str, target: str, content: str,
                 commit_fn: Callable[[], Any], gate: approval.Consent,
                 policy: Optional[WritePolicy] = None) -> Dict[str, Any]:
    """Run a durable write through the universal WriteGate, under a verified human approval.

    Secrets stay a HARD BLOCK that no approval can override (P2/I1 — a security block is not a
    methodology gate). The decision goes to the audit ledger, and the redemption record links the
    human's approval to the `write_gate` entry of the commit it licensed.

    The `policy` carries the K3 trust dial and the tool's identity down to the gate (SI.4), and marks
    the write `human_approved` — which is a STATEMENT OF FACT, not a bypass: `_require` above has
    already verified a human minted this approval out-of-band and burned it. That is what lets a
    `propose-only` tool commit here despite the MCP server having no terminal to prompt at."""
    p = _require(gate)                  # no human approval on disk -> we do not get here at all
    ledger = AuditLedger.from_mokata_dir(mokata_dir)
    wgate = WriteGate(ledger=ledger, trust=(policy.trust if policy else None))
    box: Dict[str, Any] = {}
    outcome = wgate.submit(
        WriteRequest(kind, target, content=content, actor="mcp",
                     tool=(policy.tool if policy else None), surface=MCP_SURFACE),
        commit=lambda: box.update(result=commit_fn()),
        human_approved=True,            # the ALREADY-VERIFIED human decision (SI.3), never a tool
                                        # parameter — see `_require` above
        # SELF-PROTECT rule (c), 0.0.16: THIS is the lane where a containment root is a fact rather
        # than a guess — every MCP write tool takes a repo `path` (guard_path'd) and hands its
        # `.mokata` dir down here, so the repo root is one dirname away. The CLI lane deliberately
        # passes none: a human typing `mokata stack share --out ~/x.json` named that destination
        # themselves, and rules (a)/(b) (which need no root) still apply there.
        workspace_root=os.path.dirname(os.path.abspath(mokata_dir)))
    approval.record_redemption(ledger, p, write_seq=outcome.approval_seq,
                               committed=outcome.committed)
    return {"status": "committed" if outcome.committed else "blocked",
            "committed": outcome.committed,
            "reason": outcome.reason,
            "findings": [f.kind for f in outcome.findings],
            "result": box.get("result")}
