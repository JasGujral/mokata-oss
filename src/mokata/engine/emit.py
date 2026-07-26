"""SI-DEV.0 — THE spec-emit path: one committer, re-invoked by every surface.

Before this module, `emitted_spec` had exactly one writer — a `store.write` buried in
`phases.py:_emit` — reachable only through `run_pipeline()`, which nothing in `src/` called. The
spec that the whole methodology turns on (the `spec-persisted` gate, the completeness gate, the
`spec-check` regression guard) could not be produced by any command a human or a model could run.
The gate that demanded it therefore blocked forever, and the guard that read it always found
nothing. This module is that missing writer, and it is deliberately the ONLY one:

    spec_commit(store, spec)      the durable write itself — emitted_spec + spec_corpus
    emit_spec(surface, ...)       the use-case: the REAL completeness gate, then a gated commit

`phases.py:_emit` (the engine pipeline), `cli_commands/spec.py` (`mokata spec emit`) and
`mcp/tools_write.py:spec_emit` all land here. One committer means one place where the spec's shape
is decided, one place the corpus is kept in step, and one place a later stage (SI-DEV's scope
section) has to touch.

The two writes are ONE commit
-----------------------------
`emitted_spec` is THIS RUN's spec — session-scoped by `SessionScopedStore`, so it lands at the
physical `emitted_spec__<run_id>` the SI.1 hook resolves. `spec_corpus` is the SHARED, cross-run
archive that `spec-check` (`engine/spec_awareness.load_spec_corpus`) reads — deliberately NOT
session-scoped, because a regression guard that could only see the current window's spec would
miss exactly the specs it exists to protect. Both are written inside the same `commit=` closure
the WriteGate runs, so a spec is never on record without the corpus knowing about it (which was
the state the whole repo was in until now).

Supersede-by-title, not append-blindly
--------------------------------------
Re-emitting a spec (a refined AC, a second pass) must not leave two contradictory copies in the
corpus for the guard to trip over. An entry with the same title is REPLACED; anything else is
appended. The corpus is capped (`MAX_CORPUS`, oldest dropped) so a long-lived repo cannot grow an
unbounded state file. Both are bounded, honest choices, not deep semantics — real spec versioning
(vN superseded-not-deleted, diffs ledgered) is SI-DEV's job, and this module leaves room for it.

Clean-room. Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any, Callable, Dict, List, Optional

from ..govern import WriteGate, WriteRequest
from ..govern.trust import CLI_SURFACE
from ..spec_scope import SCOPE_KEY, DeferredItem, SpecScope, scope_from_dict
from .completeness import run_completeness_gate
from .spec import Spec, TestRef
from .spec_awareness import SPEC_CORPUS_KEY
from .spec_gate import SPEC_STATE_KEY

# The WriteGate identity of a spec emit — the `tool` the trust dial (K3/SI.4) and the audit ledger
# see. Already the name `phases.py:_emit` used; now it is a real, registered surface too.
EMIT_TOOL = "spec_emit"

# The gate's write kind + target (what a human sees in the approval preview).
EMIT_KIND = "config"
EMIT_TARGET = "spec:emit"

# The most specs kept in the shared corpus. Generous — a repo would need this many DISTINCT spec
# titles before the oldest is dropped — and bounded, so `spec_corpus.json` cannot grow forever.
MAX_CORPUS = 64


@dataclass(frozen=True)
class EmitOutcome:
    """What an emit did (doc 85 §3: a `*Outcome` is a gate verdict)."""

    committed: bool
    reason: str
    gate: str = ""                       # the gate that decided, when one refused
    ac_count: int = 0
    unmapped: tuple = ()                 # ACs with no test — why completeness refused
    corpus_size: int = 0

    @property
    def blocked_by_completeness(self) -> bool:
        return not self.committed and self.gate == "completeness"


# ======================================================================================
# AP-SD — ONE truth: the spec's deferred scope DERIVES from the approved approach's decisions[]
# ======================================================================================

def derive_scope(scope: Optional[SpecScope], decisions: Any) -> Optional[SpecScope]:
    """The spec's DEFERRED scope, derived at emit from the approved approach's `decisions[].deferred`
    — never hand-written a second time. The AUTHORIZED surface stays the spec payload's (the approach
    declares no authorized set); the DEFERRED list is the decisions' deferrals, UNIONED with any the
    spec payload still declared whose id the decisions did not already cover (dedup by id — the
    decision is the source of truth).

    Pure and total. Byte-identical when the decisions carry NO deferrals: returns `scope` unchanged
    (the same object), so a pre-AP-SD approach leaves SI-DEV's scope semantics exactly as today."""
    derived: List[DeferredItem] = []
    for d in decisions or []:
        for df in (getattr(d, "deferred", None) or []):
            derived.append(DeferredItem(
                id=getattr(df, "id", "") or "", item=getattr(df, "item", "") or "",
                paths=tuple(getattr(df, "paths", ()) or ()),
                markers=tuple(getattr(df, "markers", ()) or ())))
    if not derived:
        return scope
    seen = {i.id for i in derived}
    authorized = scope.authorized if scope is not None else ()
    kept = tuple(i for i in (scope.deferred if scope is not None else ()) if i.id not in seen)
    return SpecScope(authorized=authorized, deferred=tuple(derived) + kept)


# ======================================================================================
# the durable write
# ======================================================================================

def preview_content(store: Any, spec: Spec) -> str:
    """The JSON a human PREVIEWS for a spec emit — with the scope DERIVED exactly as
    `spec_commit` will write it (AP-SD-FU). The derivation lives in the committer, so the raw
    payload preview understated the deferred items the approved approach's `decisions[].deferred`
    adds; this projects them into the preview so the human sees precisely what lands. Pure display:
    the durable write is still `spec_commit` (which derives), so what is WRITTEN is unchanged. Reads
    decisions from the SAME `store` the paired `spec_commit` uses, so preview and write never diverge;
    byte-identical to `json.dumps(spec.to_dict())` when the approach records no deferrals."""
    from ..brainstorm import load_decisions
    derived = derive_scope(spec.scope, load_decisions(store))
    shown = spec if derived is spec.scope else replace(spec, scope=derived)
    return json.dumps(shown.to_dict())


def _corpus_after(current: Any, spec: Spec) -> List[Dict[str, Any]]:
    """The corpus with `spec` recorded — replacing a same-titled entry, else appended, capped at
    `MAX_CORPUS` (oldest first out). Degrade-clean: an absent/corrupt corpus starts fresh rather
    than raising into a commit closure."""
    entries: List[Dict[str, Any]] = []
    if isinstance(current, list):
        entries = [e for e in current if isinstance(e, dict)]
    kept = [e for e in entries if e.get("title") != spec.title]
    kept.append(spec.to_dict())
    return kept[-MAX_CORPUS:]


def mark_emitted(store: Any, run_id: str, ledger: Any = None) -> None:
    """Checkpoint the two gates an emit just earned — `completeness_gate` and `emit`.

    `run_pipeline` already did this for its own phases (SS.S1's double-wire); the emit SURFACES did
    not, because before SI-DEV nothing read it back. SI-DEV does: `spec amend` regresses the run by
    dropping exactly these two from the passed set, and "resume from the last PASSED gate" (P17) is
    only meaningful if they were ever marked. Degrade-clean — a checkpoint failure never fails the
    emit; the spec is already committed and safe.

    D5 — but it is no longer SILENT. A lost checkpoint is a lost RESUME: the two gates this emit
    just earned are not on record, so an interrupted run restarts from a phase it already passed and
    the human re-approves a spec they already approved. `phases.py:_mark_gate_passed` handles the
    IDENTICAL failure through `session_flow.note_persist_failure` — the same channel, the same
    once-per-moment key shape, is used here rather than a second vocabulary for one failure."""
    try:
        from ..govern.resume import PipelineCheckpoint
        cp = PipelineCheckpoint(store, run_id, ledger=ledger)
        for phase in ("completeness_gate", "emit"):
            cp.mark_passed(phase)
    except Exception:
        # BROAD ON PURPOSE (and matching `phases.py:_mark_gate_passed` exactly): the checkpoint
        # write goes through a caller-supplied store, whose failure modes are not knowable here.
        # The emit itself already succeeded and MUST NOT be failed by its own bookkeeping — but the
        # human is told the checkpoint is gone, so a lost resume is a fact, not a surprise.
        from ..session_flow import note_persist_failure
        note_persist_failure("gate:emit")


def spec_version(store: Any) -> int:
    """The persisted spec's version (1 when it carries none — every spec written before SI-DEV)."""
    try:
        data = store.read(SPEC_STATE_KEY)
    except (OSError, AttributeError):
        return 0
    if not isinstance(data, dict):
        return 0
    try:
        return int(data.get("version", 1) or 1)
    except (TypeError, ValueError):
        return 1


def spec_commit(store: Any, spec: Spec, *, version: int = 1,
                archive_key: str = "") -> int:
    """THE durable spec write. Returns the corpus size after it.

    Two keys, one commit: this run's `emitted_spec` (session-scoped — the key the SI.1 hook reads)
    and the shared `spec_corpus` (cross-run — the corpus `spec-check` reads). Called ONLY from
    inside a WriteGate `commit=` closure; it does not gate itself (that would double-gate the
    engine pipeline, which brings its own gate).

    SI-DEV: `archive_key` supersedes the CURRENT spec before the new one lands — vN is copied to
    `spec_archive__<run>__v<N>` and left there forever. A superseded spec is never deleted: the
    ledger records that scope changed, and the archive is what that record points AT. `version` is
    written alongside the spec (not into `Spec` itself), so a scope-less spec's own bytes are
    untouched and `Spec.from_dict` simply ignores it."""
    if archive_key:
        previous = store.read(SPEC_STATE_KEY)
        if isinstance(previous, dict):
            store.write(archive_key, previous)          # vN superseded — NEVER deleted

    # AP-SD ONE-truth — the deferred scope derives from the approved approach's decisions[] at emit,
    # so it is never hand-written twice. Every emit surface (CLI/pipeline/MCP) lands here, so one
    # wiring binds them all. A no-op (byte-identical) when the approach records no deferrals.
    from ..brainstorm import load_decisions
    spec.scope = derive_scope(spec.scope, load_decisions(store))

    data = dict(spec.to_dict())
    data["version"] = version
    store.write(SPEC_STATE_KEY, data)
    corpus = _corpus_after(store.read(SPEC_CORPUS_KEY), spec)
    store.write(SPEC_CORPUS_KEY, corpus)
    return len(corpus)


def commit_spec(store: Any, spec: Spec, *, gate: WriteGate,
                assume_yes: bool = False,
                confirm: Optional[Callable[[str], bool]] = None,
                policy: Any = None,
                human_approved: bool = False,
                surface: str = CLI_SURFACE) -> "tuple[bool, str, int]":
    """Run `spec_commit` through the human WriteGate. `(committed, reason, corpus_size)`.

    The caller owns the gate (so the engine pipeline keeps its own, and the MCP path can hand in
    one carrying a verified SI.3 approval). This function owns only the fact that a spec emit is a
    durable, previewable, ledgered write."""
    box: Dict[str, int] = {}
    out = gate.submit(
        WriteRequest(EMIT_KIND, EMIT_TARGET, content=preview_content(store, spec),
                     tool=EMIT_TOOL, surface=surface),
        commit=lambda: box.update(size=spec_commit(store, spec)),
        assume_yes=assume_yes, confirm=confirm, human_approved=human_approved)
    return bool(out.committed), out.reason, box.get("size", 0)


# ======================================================================================
# the use-case — the REAL gates, re-invoked
# ======================================================================================

def emit_spec(surface: Any, spec: Spec, tests: List[TestRef], *,
              handoff: Any = None, ledger: Any = None, store: Any = None,
              run_id: str = "", assume_yes: bool = False,
              confirm: Optional[Callable[[str], bool]] = None) -> EmitOutcome:
    """Emit `spec` — the whole path, as one call, for the CLI and any other in-process caller.

    The completeness gate runs FIRST and is the SAME `run_completeness_gate` the engine pipeline
    runs: an acceptance criterion with no test refuses the emit and writes NOTHING. Only a passing
    gate reaches the human WriteGate. (The MCP tool does not call this — it needs to interleave the
    SI.3 propose/redeem round-trip between the two gates — but it calls the same two pieces, in the
    same order, through `spec_commit`. `test_si_dev_0_*` pins both paths.)

    `store` overrides `surface.state` for callers that must write onto a run OTHER than their own
    process's session — which is every CLI caller: `mokata spec emit` runs in the human's shell, not
    the agent's session, so it resolves the run being gated and hands the matching store in (see
    `cli_commands/spec.py:_run_scoped_store`). Defaulting to `surface.state` keeps every in-session
    caller unchanged."""
    store = surface.state if store is None else store
    gr = run_completeness_gate(spec, tests, handoff=handoff, store=store)
    if not gr.passed:
        return EmitOutcome(False, gr.reason, gate="completeness",
                           ac_count=len(spec.criteria), unmapped=tuple(gr.unmapped_ids))

    committed, reason, size = commit_spec(
        store, spec, gate=WriteGate(ledger=ledger),
        assume_yes=assume_yes, confirm=confirm)
    if committed and run_id:
        mark_emitted(store, run_id, ledger=ledger)      # the two gates this emit just earned (P17)
    return EmitOutcome(committed, reason, gate="" if committed else "write-gate",
                       ac_count=len(spec.criteria), corpus_size=size)


# ======================================================================================
# parsing the wire shape (shared by the CLI's --file and the MCP tool's args)
# ======================================================================================

def spec_from_payload(payload: Dict[str, Any]) -> "tuple[Spec, List[TestRef]]":
    """`(spec, tests)` out of the JSON shape both surfaces accept:

        {"title": str, "approach": str?, "domains": [str]?,
         "criteria": [{"id": str, "text": str}],
         "tests":    [{"name": str, "ac_ids": [str]}]}

    Raises `ValueError` with an actionable message on a shape the gates could not judge — an
    unparseable spec must fail LOUDLY at the surface, never reach a gate as an empty one (an
    empty spec would be refused by completeness for the wrong reason, and the human would be told
    "no acceptance criteria" when the truth is "your JSON was wrong")."""
    from .spec import AcceptanceCriterion

    if not isinstance(payload, dict):
        raise ValueError("a spec must be a JSON object")
    title = payload.get("title") or ""
    if not isinstance(title, str) or not title.strip():
        raise ValueError("a spec needs a title")

    raw_criteria = payload.get("criteria")
    if not isinstance(raw_criteria, list) or not raw_criteria:
        raise ValueError("a spec needs at least one acceptance criterion "
                         "(criteria: [{id, text}])")
    criteria = []
    for c in raw_criteria:
        if not isinstance(c, dict) or not c.get("id"):
            raise ValueError("each acceptance criterion needs an `id` (and should carry `text`)")
        criteria.append(AcceptanceCriterion(id=str(c["id"]), text=str(c.get("text", ""))))

    tests: List[TestRef] = []
    for t in payload.get("tests") or []:
        if not isinstance(t, dict) or not t.get("name"):
            raise ValueError("each test needs a `name` (and the `ac_ids` it covers)")
        ac_ids = t.get("ac_ids") or []
        if not isinstance(ac_ids, list):
            raise ValueError(f"test {t['name']}: `ac_ids` must be a list of criterion ids")
        tests.append(TestRef(name=str(t["name"]), ac_ids=[str(a) for a in ac_ids]))

    domains = payload.get("domains") or []
    spec = Spec(
        title=title,
        criteria=criteria,
        approach=payload.get("approach") or None,
        domains=[str(d) for d in domains] if isinstance(domains, list) else [],
        # SI-DEV — the optional, additive scope section. Absent => the spec declares no scope and
        # the hook will not police it (the honest boundary; every pre-SI-DEV spec is in this case).
        scope=scope_from_dict(payload.get(SCOPE_KEY)),
    )
    return spec, tests


__all__ = [
    "EMIT_KIND", "EMIT_TARGET", "EMIT_TOOL", "MAX_CORPUS",
    "EmitOutcome", "commit_spec", "derive_scope", "emit_spec", "preview_content",
    "spec_commit", "spec_from_payload",
]
