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

import inspect

from typing import Any, Dict

READ = "read"

# ======================================================================================
# 0.0.28 I2 — parameter schema metadata (what `tools/list` tells a model about each argument)
# ======================================================================================
# Every tool parameter is `Annotated[T, Field(description=…)]`, closed sets are `Literal[…]` (which
# FastMCP emits as `enum`), and array items are `TypedDict`s — so a first call made from
# `tools/list` alone is right. `Field` is pydantic's (pydantic ships with the MCP SDK); in a stripped
# environment without it the tool functions must still import and run (they are pure and SDK-free),
# so `Field` then degrades to inert metadata that nothing reads. `TypedDict` and `NotRequired`
# are the stdlib's (the floor is 3.12, where pydantic accepts `typing.TypedDict`).
try:
    from pydantic import Field as Field  # noqa: F401  (re-exported for the tool modules)
except ImportError:                       # pragma: no cover - a stripped env: no schema is built
    def Field(*_a: Any, description: str = "", **_k: Any) -> Any:  # type: ignore[misc]
        return description
from typing import NotRequired, TypedDict  # noqa: F401,E402  (re-exported for the tool modules)

#: Tools of kind `read` that nonetheless APPEND evidence (the progress/review event log, the session
#: snapshot). They are not gated writes — nothing is proposed and nothing is destroyed — but they are
#: not read-only either, so a client is told `readOnlyHint:false, destructiveHint:false`.
APPEND_ONLY_TOOLS = frozenset({"review_record", "progress_mark", "run_finish", "session_save"})
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
#   - `spec_show` (HANDOFF.G1) DELIBERATELY EXCLUDED — grounded, not overlooked. Its whole body is
#     `_run_scoped_store` -> `gate_hook.resolve_run` (local `state/` filenames + the local session
#     registry) then `read_emitted_spec` (one local JSON read). It builds NO `KnowledgeLayer`, so —
#     unlike its neighbour `decompose`, which shares the spec load but then graph-verifies subtask
#     independence — the lazy CRG subprocess never spawns; it touches no MemoryStore, so no team
#     Postgres; it resolves no catalog. openWorldHint:false.
#   - `review_status` / `review_record` (REVIEW-FIX.R3) DELIBERATELY EXCLUDED — grounded per tool.
#     Both bodies are `Surface.load` + `progress_events._resolve_verdict_run` (which reaches
#     `run_resolver.resolve_run` -> the local session-binding file + `gate_hook.resolve_run`
#     over local `state/` filenames) + one local append/backward-scan of `state/
#     progress-events.jsonl`. No `KnowledgeLayer` is built, so the lazy CRG subprocess never
#     spawns; no `MemoryStore`, so no team Postgres; no catalog is resolved. openWorldHint:false.
#     NOTE the read/write label is NOT what decides this: `review_record` APPENDS an event and is
#     still openWorld:false, exactly as the rule above says.
# A tool added later that reaches out but is NOT listed here fails the D1a drift-guard test.
OPEN_WORLD_TOOLS = frozenset({
    # --- CRG code-graph subprocess — a graph QUERY on the read/compute path -------------------
    "query",           # tools_read.query        -> run_query -> KnowledgeLayer._run (crg_client)
    "decompose",       # tools_read.decompose    -> _decompose(layer=) graph independence-verify
    "ci_check",        # tools_read.ci_check     -> ci_check.run_ci_check -> KnowledgeLayer graph
    "remember",        # tools_memory.remember   -> grounding_report(layer) symbol resolve on propose
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
    "events_share",    # tools_team.events_share -> pending_publish connects to the shared PG store
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
    if kind == READ and name in APPEND_ONLY_TOOLS:
        return {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": open_world}
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

Outcomes: "proposed" is WAITING ON A HUMAN — nothing written; relay the `awaiting` head verbatim, \
stop, and re-call with `proposal_id` after they approve. No result or a permission denial is HUMAN \
DECLINED — do not retry (`mokata approve --list` confirms). "error"/"degraded" — a real fault, with \
its fix. "running" — re-call with the same arguments to join it."""


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
    # 0.0.28 I2: dedented (`inspect.cleandoc`) — a docstring's source indentation is bytes a
    # model reads as nothing, and some tool docstrings are indented while others are not.
    text = inspect.cleandoc(doc or "")
    if kind == READ:
        return text
    return f"{text}{_WRITE_OUTCOMES}"
