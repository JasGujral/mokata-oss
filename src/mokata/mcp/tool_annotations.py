"""MCP-R.D1a · tool annotations projected from `ToolSpec.kind` + the grounded open-world set.

Projects each tool's already-declared `kind` (read|write|approve) into MCP tool ANNOTATIONS at
registration (doc 88 §D1), so a client — and the human's allow/ask policy — can reason about a call
BEFORE making it (P16 legibility, P14 no-silent-mutation). Pure PROJECTION: no new per-tool
modeling, no tool-body change, no input-schema change. The `kind` sets the read/destructive/
idempotent axes; a tool's NAME sets the open-world axis via `OPEN_WORLD_TOOLS`.

Pure stdlib — never imports the optional MCP SDK. `mcp/server` turns the returned dict into the SDK
`ToolAnnotations` at `add_tool`; keeping this module SDK-free keeps the projection unit-testable
without the SDK (same discipline as `registry` / `status`).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from typing import Dict

READ = "read"
WRITE = "write"
APPROVE = "approve"

# ======================================================================================
# The open-world axis — a GROUNDED, per-tool constant (drift-guarded by the D1a suite)
# ======================================================================================
#
# RULE (verbatim — do NOT "fix" a propose-only write back to false thinking openWorld means read):
#   openWorldHint:true IFF the tool contacts a subprocess (the CRG code-graph engine), the team
#   Postgres backend, or a remote catalog index on ANY read-or-compute-for-write path — INDEPENDENT
#   of the propose-only write gate. `destructiveHint` is driven by the write gate; the two axes are
#   ORTHOGONAL and never gate each other. A propose-only WRITE whose COMPUTE reaches out (e.g.
#   spec_check shells to CRG, stacks_install resolves a remote manifest) is openWorld:true even
#   though its write is non-destructive. A tool that is pure manifest / spec / run-state / local-file
#   math with NO external contact is false — regardless of its read/write label.
#
# Membership is GROUNDED per-tool by CALL SITE (not by the read/write label, and not by doc 88 §D1's
# ILLUSTRATIVE parenthetical, which this supersedes). Divergences from that parenthetical:
#   - `coverage` DROPPED  — manifest/adapter negotiation only; no CRG/PG/catalog contact.
#   - `index_status` DROPPED — reads config-level backend identity (`primary.name`/`.is_graph`) +
#                     a LOCAL freshness diff; it never issues a graph QUERY, so the lazy CRG
#                     subprocess never spawns.
#   - graph/PG/catalog WRITES + `status`/`govern`/`decompose`/`ci_check`/`remember` ADDED — each
#     verified to reach a sink on a read-or-compute path (citations below).
# A tool added later that reaches out but is NOT listed here fails the D1a drift-guard test.
OPEN_WORLD_TOOLS = frozenset({
    # --- CRG code-graph subprocess — a graph QUERY on the read/compute path -------------------
    "query",           # tools_read.query        -> run_query -> KnowledgeLayer._run (crg_client)
    "decompose",       # tools_read.decompose    -> _decompose(layer=) graph independence-verify
    "ci_check",        # tools_read.ci_check     -> ci_check.run_ci_check -> KnowledgeLayer graph
    "remember",        # tools_memory.remember   -> check_about_code_anchors(layer) on propose path
    "spec_amend",      # tools_spec.spec_amend   -> KnowledgeLayer graph regression (:242)
    "spec_check",      # tools_spec.spec_check   -> KnowledgeLayer graph touch-set (:346)
    # --- team Postgres — memory read / DSN health probe / PG transport / shared log -----------
    # (local/solo mode serves these locally, but the client cannot know that up front; a
    #  set-but-slow DSN is the doc 88 root-cause-2 ~40s blocker.)
    "recall",          # tools_read.recall       -> MemoryStore team backend read
    "status",          # tools_read.status       -> resolve_read_routing team-health probe (RC-2)
    "audit",           # tools_read.audit        -> team_audit_view (shared PG log) when team=true
    "session_list",    # tools_read.session_list -> postgres session transport
    "govern",          # tools_read.govern       -> build_governance_view -> store.peek_active/detect
    "apply_proposal",  # tools_memory.apply_proposal -> store.detect_issues -> backend.all
    "memory_export",   # tools_memory.memory_export -> export_memory(store) reads items
    "memory_import",   # tools_memory.memory_import -> plan_memory_import reads store (already-count)
    "session_push",    # tools_session.session_push -> plan_session_push -> transport.read_bundle
    "session_pull",    # tools_session.session_pull -> plan_session_pull reads the source bundle
    "session_name",    # tools_session.session_name -> plan_session_rename reads transport (collision)
    "audit_share",     # tools_team.audit_share  -> pending_share connects to the shared PG log
    # --- remote catalog — a stack index / manifest resolved from a possibly-remote source -----
    "stacks_list",     # tools_read.stacks_list     -> stacks.load_index(source)
    "stacks_search",   # tools_read.stacks_search   -> stacks.load_index(source)
    "stacks_show",     # tools_read.stacks_show     -> stacks.load_index(source)
    "import_stack",    # tools_config.import_stack  -> resolve_stack_manifest(source) on propose path
    "stacks_install",  # tools_config.stacks_install-> resolve_stack_manifest(source) on propose path
})


def annotations_for(kind: str, name: str) -> Dict[str, bool]:
    """Project a tool's (kind, name) into MCP annotation HINTS (doc 88 §D1). Returns a plain dict
    (SDK-free); `mcp/server` builds the SDK `ToolAnnotations` from it at `add_tool`.

    read           -> readOnlyHint:true. `destructiveHint`/`idempotentHint` are MEANINGLESS under
                      read-only (per the MCP annotations contract), so they are OMITTED; only the
                      openWorld axis is added.
    write / approve -> readOnlyHint:false + destructiveHint:false + idempotentHint:true.
        * destructiveHint:false is DELIBERATE and correct: every mokata write is PROPOSE-ONLY and
          human-gated — it stages a change and destroys nothing; the human approves the apply. A
          reviewer expecting `true` should read this: mokata writes never destroy (P2).
        * idempotentHint:true is the doc 88 default, scoped to the proposal-id re-call path: an
          approval is SINGLE-USE (burned on apply), so re-calling apply with the same proposal_id
          commits AT MOST once — a retry never double-commits. (The initial propose is advisory;
          this coarse hint follows the doc-88 default rather than per-tool grounding.)

    openWorldHint is set from `OPEN_WORLD_TOOLS` and is INDEPENDENT of the axes above — see its rule.

    An unknown `kind` raises: a NEW kind must be classified here, never silently mis-annotated (a
    drift backstop mirroring the OPEN_WORLD_TOOLS drift-guard)."""
    open_world = name in OPEN_WORLD_TOOLS
    if kind == READ:
        return {"readOnlyHint": True, "openWorldHint": open_world}
    if kind in (WRITE, APPROVE):
        return {"readOnlyHint": False, "destructiveHint": False,
                "idempotentHint": True, "openWorldHint": open_world}
    raise ValueError(
        f"MCP annotations: unknown tool kind {kind!r} for {name!r}; classify it in annotations_for")


# ======================================================================================
# MCP-R.D2 · the OUTCOMES contract, appended to every gated-write description
# ======================================================================================
#
# A tool DESCRIPTION is the contract surface the model actually reads — it arrives before any
# call, where a `hint` inside a result arrives only after one. That asymmetry is the whole of
# D2: the outcome a model most needs to understand is the one where NO result comes back.
#
# Three outcomes follow a gated-write call, and conflating any two of them is how a healthy
# WAIT gets misread as a HANG (P16). Only (a) is a mokata result; (b) is the HARNESS answering
# for the human and may be structurally invisible (Claude Code does not specify what a
# user-clicked Deny returns to the model — see `awaiting._rides_harness_prompt`); (c) is a real
# fault, and D0's dispatch wrapper guarantees it arrives as a status rather than as silence.
#
# The CLI fallback is named in every case because it is the one channel that works when the
# harness boundary itself is the thing that failed: `mokata approve --list` answers "is anything
# actually waiting on me?" from the terminal, out-of-band, with no MCP call involved.
_WRITE_OUTCOMES = """

OUTCOMES — three, and they are NOT interchangeable:
  (a) a result with status "proposed" and an `awaiting` head — mokata is WAITING ON A HUMAN.
      Nothing was written and nothing is wrong. Relay the `awaiting` head to the user VERBATIM
      (it carries the proposal id and the exact command), then STOP and wait. Do not retry, and
      do not look for another way to make the write happen — there isn't one, by design.
  (b) NO result, an empty result, or a permission/denial error — the HUMAN DECLINED this call at
      the harness permission prompt. This is a person saying NO; it is NOT a stuck server and NOT
      something to retry. Say so plainly, and confirm from the terminal with `mokata approve
      --list` (out-of-band — it needs no MCP call and works when this boundary does not).
  (c) a result with status "error" / "timed_out" / "degraded" — a genuine fault, in mokata's own
      voice, naming the stuck resource and its fix. Every mokata tool returns one of these rather
      than hanging, so silence is never mokata's answer — silence means (b).

NEVER report a wait as a failure, and never report a decline as a hang. If you cannot tell which
you got, run `mokata approve --list` and say what it shows."""


def description_for(kind: str, name: str, doc: str) -> str:
    """The description `mcp/server` registers: the tool's own docstring, plus — for a GATED WRITE —
    the shared outcomes contract above.

    ONE appended block, projected from `kind`, rather than the same paragraph re-prosed into
    nineteen docstrings: per-tool copies would drift, and the outcomes are identical for every
    gated write BECAUSE they are properties of the consent boundary, not of any one tool.

    `read` tools are untouched (byte-identical): they do not propose, cannot be declined at a
    write gate, and have no third outcome to distinguish. `approve` is included — it is the one
    tool deliberately kept in `permissions.ask`, so it is the MOST likely of all of them to come
    back as outcome (b)."""
    text = (doc or "").strip()
    if kind == READ:
        return text
    return f"{text}{_WRITE_OUTCOMES}"
