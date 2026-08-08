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

SPEC-REEMIT-CLOBBER — the run's spec is superseded, never overwritten
--------------------------------------------------------------------
The paragraph above was true of the CORPUS and quietly false of the RUN's spec. `spec_commit`
archived the prior spec only when a caller PASSED `archive_key` — which `amend.py:finish_amend`
does and no emit surface ever did — so a second emit overwrote `emitted_spec` outright: no bump, no
archive, the prior spec gone. Its `version=1` DEFAULT made it worse than a no-op, rewinding a v3
spec's counter to 1 so the next amendment archived on top of a key it had already used.

So the supersede now DERIVES here, in the one committer, whenever a caller says nothing
(`reemit_verdict`): vN is copied to `spec_archive__<run>__v<N>` and the new spec lands as vN+1, on
every surface at once. A surface that forgets cannot lose a spec — the committer will not let it.

The CONSENT question that rides on top of it is deliberately NOT the committer's (see
`reemit_verdict`): whether a given replacement may happen at all is a gate decision, and it is
answered before a human is ever shown a proposal.

Clean-room. Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any, Callable, Dict, List, Optional

from ..govern import WriteGate, WriteRequest
from ..govern.trust import CLI_SURFACE
from ..spec_scope import SCOPE_KEY, DeferredItem, SpecScope, scope_from_dict
from .completeness import run_completeness_gate, standalone_note
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
    version: int = 1                     # the version this emit landed as (SPEC-REEMIT-CLOBBER)
    superseded: str = ""                 # where the spec it REPLACED is archived ("" on a first emit)
    # SPEC-STANDALONE-SILENT — the completeness gate's standalone note, or "" when an approved
    # direction IS on record. The gate has always KNOWN this (`approach_present` /
    # `refinements_present`); the `GateResult` was simply discarded on the PASS path, so the CLI had
    # nothing to print and a supported path emitted in total silence. Carried as the note itself
    # rather than a bool so there is one truth and no surface re-words it.
    standalone_note: str = ""

    @property
    def standalone(self) -> bool:
        """No approved brainstorm approach or refinement set stood behind this spec."""
        return bool(self.standalone_note)

    @property
    def blocked_by_completeness(self) -> bool:
        return not self.committed and self.gate == "completeness"

    @property
    def blocked_by_reemit(self) -> bool:
        """Refused because the run has work in flight — the answer is `spec_amend`, not approval."""
        return not self.committed and self.gate == REEMIT_GATE


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


# ======================================================================================
# SPEC-REEMIT-CLOBBER — a second emit onto a run that already has a spec
# ======================================================================================

# The gate identity a refused re-emit reports (sibling of "completeness" / "graph-required" /
# "prior-art"). It is a REFUSAL, not a proposal: there is nothing here a human could approve,
# because the answer is a different tool.
REEMIT_GATE = "re-emit"


@dataclass(frozen=True)
class ReemitVerdict:
    """What a NEW emit onto this run must do about the spec already on it (doc 85 §3: a `*Verdict`
    is a gate answer). Two separable questions, deliberately answered together here so no surface
    can get one right and the other wrong:

      THE MECHANICS — `version` + `archive`: where the new spec lands and where the old one is
      kept. Always non-destructive, for every `kind`, INCLUDING `amend-required`. If a surface ever
      commits despite the refusal, the committer still preserves history; the refusal decides
      whether the write happens, never whether the prior spec survives it.

      THE CONSENT — `kind`. See `reemit_verdict` for where the line is drawn and why."""

    kind: str                       # "first" | "replace" | "amend-required"
    from_version: int = 0           # the version on record now (0 == none)
    version: int = 1                # the version this emit would land as
    archive: str = ""               # where `from_version` is kept ("" on a first emit)
    red: tuple = ()                 # tests already recorded RED for this run — the (b) evidence
    amend_open: bool = False        # an amendment already in flight — also (b)
    reason: str = ""                # why amend is required (empty unless refused)

    @property
    def refused(self) -> bool:
        return self.kind == "amend-required"

    @property
    def replaces(self) -> bool:
        return self.kind == "replace"


def _reemit_run(store: Any, run_id: str) -> str:
    """The run identity the archive key is built from.

    NOT a resolution path (doc 85 — there is exactly one, `run_resolver.resolve_run`, and
    both MCP callers reach it through `consent._evidence_store`). Every caller that HAS a resolved
    run passes it. The fallback covers only the callers that structurally have none — the engine
    pipeline's `phases.py:_emit`, and the CLI's standalone-repo branch where `_run_scoped_store`
    returns `(surface.state, None, None)` — and in BOTH of those the store is scoped to this
    process's own session, so `current_run_id()` is not a guess about which pipeline this is: it is
    the same identity the store in hand is already addressed by. Archiving under any other name
    would file the history somewhere its own spec is not.

    DELIBERATELY UNGUARDED (D5). The obvious `try/except Exception -> ""` here is worse than no
    handler: `session.current_session_id` is a total in-process accessor (an env read, a uuid4 and a
    lock — it does not touch disk and does not raise), so the handler could only ever catch a fault
    that means mokata is already broken — and its "" fallback is precisely the state that DISABLES
    the archive and lets the clobber this stage exists to fix happen again, silently. A fault loud
    enough to fail the emit is the honest outcome; a spec quietly destroyed is not."""
    if not run_id:
        from ..session import current_run_id
        return current_run_id()
    return run_id


def reemit_verdict(store: Any, run_id: str = "") -> ReemitVerdict:
    """May this emit REPLACE the spec already on the run — and if so, as which version?

    WHY THIS IS A GATE AND NOT A CONVENIENCE
    ----------------------------------------
    `spec_amend` exists to make a scope change EXPENSIVE in the right way: it regresses the run
    (development writes blocked while it stands), re-runs completeness over old AND new criteria,
    re-computes the blast radius when scope WIDENS, surfaces the change through
    `DeviationGate(SCOPE)`, ledgers the diff, and makes every NEW criterion owe a RED. `spec_emit`
    is a different tool with a different trust dial on purpose (`amend.AMEND_TOOL`: "a team may
    well want to let a tool emit the FIRST spec and still hold a scope CHANGE to a stricter dial").
    If a second emit could simply replace the spec, an agent blocked by the scope hook could re-emit
    with a wider `authorized` list and land the identical change under the weaker dial with none of
    those rungs. That is not a hypothetical shape — it is the shortest path around the gate.

    WHERE THE LINE IS: WORK IN FLIGHT
    ---------------------------------
    Refusing EVERY re-emit would be wrong too. Jas's case is real — "the same brainstorm might have
    multiple specs as we review and refine code" — and routing a pre-implementation refinement
    through amend would open a REGRESSION on a run that never entered develop, blocking writes that
    were never happening and demanding a RED for criteria nothing has been built against. Consent
    theatre, and P16 fatigue.

    So the line is drawn where amend's rungs start to MEAN something — where there is work in
    flight for them to protect:

      (a) REPLACE, PREVIEWED — no test has ever been recorded RED for this run and no amendment is
          open. The spec is not yet enforcing anything against code, so replacing it costs only
          history, and the archive keeps that. Still one human-gated commit, but the proposal NAMES
          what it replaces (version, title, AC delta, scope delta) so the human approves a
          REPLACEMENT knowingly rather than approving "an emit" (P2 — consent is only consent if
          you can see what you are consenting to).
      (b) REFUSE, and route to `spec_amend` — this run has produced a RED, or an amendment is
          already open. A red test IS work in flight against vN: the spec's boundary is live, the
          TDD gate is holding an obligation against it, and changing it now is exactly the act
          amend's ladder is built for. `red` is SI.2's own high-water mark (the same set
          `amend.red_owed_for` reasons about), so the two tools agree on what "in flight" means
          rather than each inventing a definition.

    The refusal is a RE-ROUTE, not a wedge: `spec_amend` accepts the identical payload plus a
    `reason`, and `test_spec_reemit` pins that it lands where the re-emit refused.

    Run-scoped by construction. `store` is the resolved evidence store (STATE-SCOPE) and `run_id`
    the run it is scoped to, so the spec being superseded, the RED being read and the archive being
    written are all the PIPELINE's — not the writing process's. Read through a process's own scope
    this gate would go SILENT cross-session, which is the one outcome worse than blocking.

    Degrade-clean on every read: an unreadable store answers "first" (there is no spec to protect),
    which is exactly today's behaviour and cannot destroy anything."""
    from ..spec_scope import amend_from_state, amend_key, archive_key

    try:
        previous = store.read(SPEC_STATE_KEY)
    except (OSError, AttributeError):
        return ReemitVerdict("first")
    if not isinstance(previous, dict) or not previous:
        return ReemitVerdict("first")

    # A spec IS on record. `spec_version` reads 1 for every spec written before SI-DEV (and for one
    # that is present but unparseable) — which is the right answer for both: unversioned means v1,
    # and a spec we cannot READ is still a spec we must not DESTROY.
    frm = spec_version(store) or 1
    run = _reemit_run(store, run_id)
    plan = {"from_version": frm, "version": frm + 1,
            "archive": archive_key(run, frm) if run else ""}

    red: tuple = ()
    amend_open = False
    if run:
        try:
            from ..tdd_state import load as load_tdd
            red_set, _green = load_tdd(store, run)
            red = tuple(sorted(red_set))
            amend_open = amend_from_state(store.read(amend_key(run))).is_open
        except (OSError, AttributeError, TypeError, ValueError):
            # The consent EVIDENCE could not be read. Fail to (a): the mechanics above already keep
            # the prior spec, and refusing on an unreadable side-channel would wedge a legitimate
            # pre-implementation refinement behind a fault it cannot see or fix. Nothing is lost
            # either way — this decides which TOOL, not whether history survives.
            red, amend_open = (), False

    if amend_open:
        return ReemitVerdict(
            "amend-required", red=red, amend_open=True, reason=(
                f"an amendment of this spec is already OPEN (v{frm} -> v{frm + 1}) and the run is "
                f"REGRESSED — development writes are blocked while it stands. A re-emit now would "
                f"land the very change that amendment is being judged for, around the judgement, "
                f"and leave the regression pointing at a spec that is no longer there. Finish the "
                f"amendment (`mokata approve <id>`) or abandon it (`mokata spec amend --abort`), "
                f"then emit. Nothing was written; v{frm} is untouched."), **plan)

    if red:
        return ReemitVerdict(
            "amend-required", red=red, reason=(
                f"a spec is already on record for this run (v{frm}) and this run has WORK IN "
                f"FLIGHT against it — {', '.join(red)} {'has' if len(red) == 1 else 'have'} been "
                f"recorded RED. Replacing the spec now is a scope change against a boundary that "
                f"is already being enforced, which is what `spec_amend` is for: it regresses the "
                f"run, re-computes the blast radius if scope widens, ledgers the diff, and makes "
                f"each new criterion OWE a failing test. A re-emit would land the same change with "
                f"none of that. Send this spec to `spec_amend` with a `reason` instead. Nothing "
                f"was written; v{frm} is untouched."), **plan)

    return ReemitVerdict("replace", **plan)


def replacement_preview(store: Any, spec: Spec, verdict: ReemitVerdict) -> str:
    """What this re-emit REPLACES, in the words a human needs to approve it knowingly.

    Reuses `amend.diff_specs` rather than re-deriving a second notion of "what changed", so the
    delta a human reads before a REPLACE is the same vocabulary they read before an AMEND. Imported
    inside the function because `amend` imports this module at module scope.

    Empty string when there is nothing being replaced (a first emit) — callers splice it in, so a
    first emit's preview is byte-identical to before this stage."""
    if not verdict.replaces:
        return ""
    from .spec_gate import load_emitted_spec
    previous = load_emitted_spec(store)
    head = (f"REPLACES v{verdict.from_version} -> v{verdict.version} "
            f"(this run already has a spec; the old one is NOT edited, it is superseded)")
    if previous is None:
        # A spec is present but unreadable (D5's malformed case). Say so — do not pretend to diff
        # it, and do not pretend there is nothing there.
        return (f"{head}\n  v{verdict.from_version} is present but UNREADABLE — it is archived "
                f"as-is at {verdict.archive} and cannot be diffed here.")
    from .amend import diff_specs
    lines = [head,
             f"  v{verdict.from_version}: '{previous.title}' ({len(previous.criteria)} AC)",
             f"  v{verdict.version}: '{spec.title}' ({len(spec.criteria)} AC)"]
    if previous.title != spec.title:
        lines.append(f"  TITLE CHANGES: '{previous.title}' -> '{spec.title}'")
    lines.append(diff_specs(previous, spec).render())
    lines.append(f"  v{verdict.from_version} is archived at {verdict.archive} — nothing is lost.")
    return "\n".join(lines)


def note_supersede(ledger: Any, run_id: str, verdict: ReemitVerdict, title: str) -> None:
    """Record the supersede on the audit ledger — the ONE wording both emit surfaces share.

    This is how a superseded spec stays REACHABLE. No archive-read tool ships in this stage (the
    registry stays at 58 tools), so `mokata audit` is what points at the key: what was replaced,
    by what, and where the old version lives. `spec_amend` already does exactly this with its own
    `superseded=` field; a re-emit that quietly replaced a spec with no such entry would be the one
    version transition in the system with no trail. Degrade-clean — a ledger fault never fails a
    commit that has already landed."""
    if ledger is None or not verdict.replaces:
        return
    try:
        ledger.record("spec_reemit", run=run_id, title=title,
                      from_version=verdict.from_version, to_version=verdict.version,
                      superseded=verdict.archive)
    except Exception:                # noqa: BLE001 — the spec is committed; bookkeeping cannot undo it
        from ..session_flow import note_persist_failure
        note_persist_failure("ledger:spec_reemit")


def spec_commit(store: Any, spec: Spec, *, version: int = 0,
                archive_key: str = "", run_id: str = "") -> int:
    """THE durable spec write. Returns the corpus size after it.

    Two keys, one commit: this run's `emitted_spec` (session-scoped — the key the SI.1 hook reads)
    and the shared `spec_corpus` (cross-run — the corpus `spec-check` reads). Called ONLY from
    inside a WriteGate `commit=` closure; it does not gate itself (that would double-gate the
    engine pipeline, which brings its own gate).

    SI-DEV: `archive_key` supersedes the CURRENT spec before the new one lands — vN is copied to
    `spec_archive__<run>__v<N>` and left there forever. A superseded spec is never deleted: the
    ledger records that scope changed, and the archive is what that record points AT. `version` is
    written alongside the spec (not into `Spec` itself), so a scope-less spec's own bytes are
    untouched and `Spec.from_dict` simply ignores it.

    SPEC-REEMIT-CLOBBER — those two are now DERIVED (`reemit_verdict(store, run_id)`) when the
    caller passes NEITHER. That inversion is the fix: `archive_key=""` used to mean "overwrite and
    lose it", which is not a thing any caller ever wants, and the one caller that DID want history
    (`finish_amend`) had to remember to ask. Now the safe behaviour is what you get by saying
    nothing, and a caller can still pin both explicitly — which `finish_amend` does, so amend's
    ladder is byte-for-byte unchanged and keeps owning its own version arithmetic.

    `version=0` is that "say nothing" sentinel. It replaced a `1` default that did not merely fail
    to bump — it REWOUND, writing "v1" over a v3 spec and poisoning the next amendment's archive
    key."""
    if not version and not archive_key:
        plan = reemit_verdict(store, run_id)
        version, archive_key = plan.version, plan.archive
    version = version or 1
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
                surface: str = CLI_SURFACE,
                run_id: str = "") -> "tuple[bool, str, int]":
    """Run `spec_commit` through the human WriteGate. `(committed, reason, corpus_size)`.

    The caller owns the gate (so the engine pipeline keeps its own, and the MCP path can hand in
    one carrying a verified SI.3 approval). This function owns only the fact that a spec emit is a
    durable, previewable, ledgered write.

    `run_id` is the run the supersede is keyed to (SPEC-REEMIT-CLOBBER). A caller that has one
    passes it; `phases.py:_emit` has none and falls back to its own session, which is the identity
    the store it hands in is already scoped to (see `_reemit_run`)."""
    box: Dict[str, int] = {}
    out = gate.submit(
        WriteRequest(EMIT_KIND, EMIT_TARGET, content=preview_content(store, spec),
                     tool=EMIT_TOOL, surface=surface),
        commit=lambda: box.update(size=spec_commit(store, spec, run_id=run_id)),
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
    # SPEC-STANDALONE-SILENT — carried on EVERY outcome from here down, so the surface decides what
    # to SHOW and never has to re-derive WHETHER. Informational only: it is read off a verdict that
    # is already decided and changes none of the branches below.
    alone = standalone_note(gr)
    if not gr.passed:
        return EmitOutcome(False, gr.reason, gate="completeness",
                           ac_count=len(spec.criteria), unmapped=tuple(gr.unmapped_ids),
                           standalone_note=alone)

    # SPEC-REEMIT-CLOBBER — may this REPLACE the spec already on the run? Refused when there is work
    # in flight (a RED on record, or an amendment open): that is `spec_amend`'s ladder, and a
    # re-emit must not be the way around it. Placed with the other pre-consent gates — a refusal
    # here writes nothing and proposes nothing, because no approval could make it the right tool.
    verdict = reemit_verdict(store, run_id)
    if verdict.refused:
        return EmitOutcome(False, verdict.reason, gate=REEMIT_GATE, ac_count=len(spec.criteria),
                           standalone_note=alone)

    committed, reason, size = commit_spec(
        store, spec, gate=WriteGate(ledger=ledger),
        assume_yes=assume_yes, confirm=confirm, run_id=run_id)
    if committed and run_id:
        mark_emitted(store, run_id, ledger=ledger)      # the two gates this emit just earned (P17)
    if committed:
        note_supersede(ledger, run_id, verdict, spec.title)
    return EmitOutcome(committed, reason, gate="" if committed else "write-gate",
                       ac_count=len(spec.criteria), corpus_size=size,
                       version=verdict.version, superseded=verdict.archive if verdict.replaces
                       else "", standalone_note=alone)


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
    "EMIT_KIND", "EMIT_TARGET", "EMIT_TOOL", "MAX_CORPUS", "REEMIT_GATE",
    "EmitOutcome", "ReemitVerdict", "commit_spec", "derive_scope", "emit_spec", "note_supersede",
    "preview_content", "reemit_verdict", "replacement_preview", "spec_commit",
    "spec_from_payload", "spec_version",
]
