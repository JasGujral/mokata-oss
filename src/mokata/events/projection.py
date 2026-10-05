"""R1.S1b — project the canonical ledger into typed events.

doc 42: *"events emitted from the ledger write path (ledger stays canonical, events derive from
it) for every gate evaluation, approval outcome, MCP tool dispatch."* This module is the
derivation, and the projection point is `AuditLedger.record` itself rather than the 90 call
sites that reach it. ⭐ **That choice is §7i applied before the fact:** a guard wired at the
call sites it can see protects the call sites it can see, and the 91st would have been written
next week by someone who had never read this module.

⛔ **BUT A MAP FROM 65 KINDS IS A LIST, AND A LIST GOES STALE — so it is graded against a
sweep.** `tests/_ledger_kinds.py` derives every kind the tree can write (literals, module
constants, and the three forwarders that pass a `kind` through), and `test_a39` asserts the
register below is TOTAL over it. A new `record("something_new", …)` reds until someone says
what it is. This is the SI.6 zero-bypass / D5 sweep-register pattern, third use in this tree,
and it is here for the same reason it was there: *a classification nobody can be forced to
make is one somebody will skip.*

⚠ **UNMAPPED IS A REAL ANSWER, NOT A GAP.** Most of the ledger is not one of doc 42's seven
governance event types: a blast-radius measurement, a CAS conflict the machine resolved, a
proposal that is not yet a decision, a dev-mode step log. Each one says so in one line. ⭐ The
distinction that matters is between *"this is not a governance event"* and *"nobody looked"* —
and only a total register can tell them apart (§7g).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from .schema import (
    APPROVAL_DECISION,
    GATE_DECISION,
    MEMORY_OP,
    PHASE_TRANSITION,
    TOKEN_SPEND,
    TOOL_CALL,
    ApprovalDecision,
    GateDecision,
    MemoryOp,
    Payload,
    PhaseTransition,
    TokenSpend,
    ToolCall,
)

UNMAPPED = None

# ------------------------------------------------------------------- the register (ledger kind)
#
# Each entry is `kind: (event type, why)`. An UNMAPPED kind is `(None, why)` and the `why` is
# the part that earns its place: it is the sentence a future reader needs in order to disagree.

KIND_REGISTER: Dict[str, Tuple[Optional[str], str]] = {
    # ---- a gate evaluated something and reached a verdict -----------------------------------
    "write_gate": (GATE_DECISION, "the universal write gate — the one every durable write passes"),
    "gate": (GATE_DECISION, "the completeness gate's AC verdict"),
    "karpathy_gate": (GATE_DECISION, "the Karpathy discipline gate"),
    "tdd": (GATE_DECISION, "the TddGuard's RED/GREEN verdict"),
    "deviation": (GATE_DECISION, "the spec-deviation gate"),
    "outbound": (GATE_DECISION, "the outbound/trifecta gate on a publish"),
    "pooler_trap": (GATE_DECISION, "the DSN pooler refusal — a gate on a connection string"),
    "decompose_fanout_guard": (GATE_DECISION, "the fan-out ceiling refusing a decomposition"),
    "spec_conflict": (GATE_DECISION, "spec-awareness refusing or flagging a conflicting touch set"),
    "rule_block": (GATE_DECISION, "the Sentinel enforcement gate blocking an action"),
    "hook": (GATE_DECISION,
             "a PreToolUse hook's verdict — it carries `blocked` and an exit code, which is a "
             "gate decision reached in a different process"),

    # ---- a HUMAN said yes or no -------------------------------------------------------------
    "write_approval": (APPROVAL_DECISION, "the minted approval itself"),
    "audit_consent": (APPROVAL_DECISION, "consent to an audit scope"),
    "graph_offer": (APPROVAL_DECISION, "the code-graph adoption offer, accepted or declined"),
    "extra_offer": (APPROVAL_DECISION, "an optional-extra install offer"),
    "consolidation_decision": (APPROVAL_DECISION, "a human accepting or rejecting a consolidation"),
    "healing_decision": (APPROVAL_DECISION, "a human accepting or rejecting a healing proposal"),
    "rule_promotion_decision": (APPROVAL_DECISION, "a human promoting a pattern to a rule"),
    "enforcement_change": (APPROVAL_DECISION, "a human changing a rule's enforcement level"),
    "scope_promotion": (APPROVAL_DECISION, "a human moving an item between scopes"),
    "decompose_confirm": (APPROVAL_DECISION, "a human confirming a decomposition"),
    "finish": (APPROVAL_DECISION, "the end-of-run choice a human makes"),
    # ⚠ An OVERRIDE is filed here and not under GateDecision, deliberately. The gate's own
    # verdict is already its own event; an override is a PERSON deciding to proceed past one,
    # which is the fact an auditor is looking for and the one a gate-shaped event would bury.
    "gate_override": (APPROVAL_DECISION, "a human overriding a soft gate — see the note above"),
    "rule_override": (APPROVAL_DECISION, "a human overriding a Sentinel soft block"),
    "graph_degraded_override": (APPROVAL_DECISION, "a human proceeding on a degraded graph"),

    # ---- tokens -----------------------------------------------------------------------------
    "exec_estimate": (TOKEN_SPEND, "an execution-mode ESTIMATE — `estimated=True` carries that"),
    "savings": (TOKEN_SPEND, "a JIT-retrieval saving: baseline vs actual tokens"),
    "token_calibration": (TOKEN_SPEND,
                          "R11's estimate-vs-actual row. Since the transcript adapter landed it "
                          "can carry a REAL `actual`, which is the only place in this register "
                          "a MEASURED token count comes from — everything else here is chars/4"),

    # ---- memory -----------------------------------------------------------------------------
    "memory_reembed": (MEMORY_OP, "a bulk re-embed over memory items"),
    "import_batch": (MEMORY_OP, "a memory import batch"),
    "migrate_batch": (MEMORY_OP, "a memory migration batch"),
    "migrate_drop_source": (MEMORY_OP, "the destructive half of a migration"),
    "review_transition": (MEMORY_OP,
                          "a memory item's lifecycle transition. ⚠ The ledger entry carries a "
                          "rendered DIFF; `MemoryOp` carries the id and type and NOTHING else, "
                          "which is the projection refusing content the canonical record keeps"),
    "review_rollback": (MEMORY_OP, "a memory item rolled back to a prior version — same refusal"),
    "consolidation_proposal": (MEMORY_OP, "a proposed merge/prune over memory items"),

    # ---- the pipeline -----------------------------------------------------------------------
    "phase": (PHASE_TRANSITION, "a pipeline phase's outcome"),
    "checkpoint": (PHASE_TRANSITION, "a passed-gate checkpoint"),
    "playbook": (PHASE_TRANSITION, "a playbook step"),
    "sequential": (PHASE_TRANSITION, "one sequential task in an execution batch"),
    "subagent": (PHASE_TRANSITION, "one parallel subagent task"),
    "exec_mode": (PHASE_TRANSITION, "the execution mode chosen for a batch"),
    "exec_degrade": (PHASE_TRANSITION, "a parallel batch degrading to the sequential floor"),

    # ---- a tool or command dispatched -------------------------------------------------------
    "model_route": (TOOL_CALL, "a routed model attempt"),
    "graph_adopt": (TOOL_CALL, "a code-graph tool being adopted"),
    "graph_first_use": (TOOL_CALL, "a code-graph tool's first use in a repo"),
    "setup": (TOOL_CALL, "a harness setup/unsetup run"),
    "update_check": (TOOL_CALL, "an update check reaching outbound"),
    "worktree_create": (TOOL_CALL, "an isolated worktree created"),
    "worktree_remove": (TOOL_CALL, "an isolated worktree removed"),
    "team_flush": (TOOL_CALL, "a team-journal flush against the shared store"),
    "vault_integrity": (TOOL_CALL, "a vault artifact's integrity verdict on pull"),

    # ---- NOT a governance event, and why ----------------------------------------------------
    "impact": (UNMAPPED, "a blast-radius MEASUREMENT taken during a spec amend. A number, not a "
                         "decision — nothing was gated, approved or spent"),
    "team_sync_conflict": (UNMAPPED, "resolved by compare-and-set, by the machine. Filing it as "
                                     "an ApprovalDecision would put an automatic outcome in the "
                                     "register of things a person chose"),
    "rule_promotion_proposed": (UNMAPPED, "a PROPOSAL. Its decision counterpart "
                                          "`rule_promotion_decision` is mapped; a proposal that "
                                          "may never be accepted is not an approval outcome"),
    "spec_amend": (UNMAPPED, "a spec document edit. It passes a gate, and THAT gate's own entry "
                             "is the governance event; this row is the artifact change"),
    "spec_reemit": (UNMAPPED, "the same, for a re-emitted spec version"),
    "reversible_write": (UNMAPPED, "the undo-buffer's bookkeeping for a write whose gate entry "
                                   "is already a GateDecision — mapping it would double-count "
                                   "one decision"),
    "revert": (UNMAPPED, "the same, for the undo itself"),
    "secret_ignore": (UNMAPPED, "registering a scanner exception. It is gated, and its gate "
                                "entry is the event; this row records the resulting store"),
    "domain": (UNMAPPED, "a domain-pack bookkeeping row"),
    "deprecation_notice": (UNMAPPED, "a user-facing deprecation notice was shown"),
    "removal_notice": (UNMAPPED, "a user-facing removal notice was shown"),
    "optimize": (UNMAPPED, "a dev-mode optimise step log"),
    "debug": (UNMAPPED, "a dev-mode debug step log"),
    "bug": (UNMAPPED, "a dev-mode bug-mode step log"),
}


def _first(entry: Dict[str, Any], *names: str, default: Any = "") -> Any:
    for n in names:
        v = entry.get(n)
        if v is not None and v != "":
            return v
    return default


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def project(kind: str, entry: Dict[str, Any]) -> Optional[Payload]:
    """The typed payload for one ledger entry, or None when its kind is not a governance event.

    The field mapping is per EVENT TYPE, not per kind: the ledger's 65 kinds already agree on a
    handful of field names (`decision`, `reason`, `target`/`subject`, `actor`), so a per-kind
    field table would be 65 near-identical rows and 65 chances to mistype one. A kind whose
    fields do not match its type's reader degrades to the type's defaults rather than raising —
    this runs inside the ledger's append path."""
    mapped = KIND_REGISTER.get(kind, (UNMAPPED, "unregistered"))[0]
    if mapped is None:
        return None
    if mapped == GATE_DECISION:
        # ⚠ The ledger's `reason` is deliberately NOT copied — see `GateDecision`'s docstring.
        return GateDecision(gate=str(_first(entry, "gate", "hook", default=kind)),
                            decision=str(_first(entry, "decision", default="recorded")),
                            subject=str(_first(entry, "target", "subject", "item_id")))
    if mapped == APPROVAL_DECISION:
        seq = entry.get("approval_seq")
        return ApprovalDecision(decision=str(_first(entry, "decision", default="recorded")),
                                subject=str(_first(entry, "subject", "target", "proposal",
                                                   "item_id", "scope", "extra", default=kind)),
                                actor=str(_first(entry, "actor")),
                                approval_seq=seq if isinstance(seq, int) else None)
    if mapped == TOKEN_SPEND:
        # `estimated` is the honesty bit: only `token_calibration` with a real `actual` is a
        # measurement. Everything else on this path is a chars/4 figure.
        real = kind == "token_calibration" and isinstance(entry.get("actual"), int)
        # ⚠ THE LABEL IS KIND-QUALIFIED WHEN IT COMES FROM `mode`. `exec_estimate` carries
        # `mode="sequential"`, which would render a TokenSpend labelled exactly `sequential` —
        # indistinguishable at a glance from the REAL `exec:sequential` spend the orchestrator
        # emits afterwards, and the two differ only in a boolean. An estimate and a measurement
        # that read alike is the whole failure this type's `estimated` flag exists to prevent.
        label = _first(entry, "label", "context")
        if not label:
            mode = _first(entry, "mode")
            label = f"{kind}:{mode}" if mode else kind
        return TokenSpend(label=str(label),
                          input_tokens=_as_int(_first(entry, "est_in", "baseline", "estimate",
                                                      default=0)),
                          output_tokens=_as_int(_first(entry, "est_out", "actual", default=0)),
                          model=str(_first(entry, "model", "final_model")),
                          estimated=not real)
    if mapped == MEMORY_OP:
        return MemoryOp(op=kind,
                        item_id=str(_first(entry, "item_id", "batch_digest")),
                        mtype=str(_first(entry, "mtype", "op")),
                        count=_as_int(_first(entry, "items", "count", default=1)) or 1)
    if mapped == PHASE_TRANSITION:
        return PhaseTransition(phase=str(_first(entry, "phase", "step", "mode", "task",
                                                default=kind)),
                               outcome=str(_first(entry, "decision", "outcome", default=kind)))
    if mapped == TOOL_CALL:
        ok = entry.get("ok")
        return ToolCall(tool=str(_first(entry, "tool", "action", "target", default=kind)),
                        surface=str(_first(entry, "surface", "scope", "source")),
                        ok=bool(ok) if isinstance(ok, bool) else True)
    return None


def registered_kinds() -> "frozenset[str]":
    return frozenset(KIND_REGISTER)


def mapped_kinds() -> "frozenset[str]":
    return frozenset(k for k, (t, _why) in KIND_REGISTER.items() if t is not None)
