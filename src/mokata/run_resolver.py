"""RUN-ID-DRIFT (OSS #44) — THE run resolver. There is exactly ONE, and this is it.

The defect this module now exists to close
------------------------------------------
A run id IS a session id (`session.py`: "run_id == session_id"), minted per PROCESS. So every
process that registers can mint its own run, and a repo accumulates runs the way a repo accumulates
windows. That is fine — what was NOT fine is that mokata then had TWO resolvers answering "which run
is this?", and they answered differently:

  * `progress.find_active_run` SCANNED and PICKED — "the first incomplete-checkpoint run, else the
    most-recent unshipped". Over `uuid4().hex` ids the first is an ARBITRARY run. It never asked
    whose run it was; it could not, since it only ever saw a state store.
  * this module (then named `badge_run`) resolved SESSION-AWARELY — bound run, then single-live
    narrowing — but its own docstring said it "sits BESIDE `find_active_run` — it does not change
    it". The name was part of the problem: a module called `badge_run` reads as the BADGE's private
    concern, so the next surface needing a run wrote its own answer instead of reading this one.

Beside-ness was the bug. Live report OSS #44: five tracked runs drifted apart inside one session,
one surface reading `[2/7] complete` while calls stamped into a different, UNSTARTED run and review
verdicts recorded against a third. Each surface was correct by its own logic; there were three
logics. `find_active_run` is deleted, this resolver is the only answer, and every surface —
`progress`, `progress mark`, `review-status`, `spec`, the session bundle, the badge, the gate hook,
the MCP stamp — reads it.

The Class-1 obligation: an absent answer is not an answer
--------------------------------------------------------
Rung (vii) below (single-LIVE narrowing) DEGENERATES the moment several runs are live, which is
precisely OSS #44's shape. The old resolvers all returned `Optional[str]`, so "here is your run"
and "I cannot tell" and "there is no run" shared one representation (`None`) — and the surfaces
that could not live with `None` filled it in by picking. `RunResolution` gives the three outcomes
three representations:

    resolved      run_id is not None, `basis` names the rung that answered
    AMBIGUOUS     run_id is None, `candidates` lists the runs it could not choose between
    NONE          run_id is None, no candidates — this repo has no run state at all

mokata never picks between candidates. A surface that needs a run and gets AMBIGUOUS says so and
names the remedy (`--run <id>`, `mokata sessions`); it does not guess, because a guess is what
stamped a call into an unstarted run.

The ladder — facts about the caller first, inferences after
-----------------------------------------------------------
    (i)    EXPLICIT   the caller named a run outright.
    (ii)   PINNED     `MOKATA_SESSION_ID` — the documented "I am telling you which run" control.
    (iii)  BOUND      the run bound to this Claude Code `session_id` (SessionStart writes it; see
                      `maybe_bind_on_session_start` and harness gap #25642), still on record.
    (iv)   EVIDENCE   the single run holding PIPELINE evidence (an approved approach / emitted
                      spec). Survives re-entry, where a bare new checkpoint is indistinguishable
                      from the pipeline it just re-entered (RE-ENTRY's tier, kept verbatim).
    (v)    OWN        THIS PROCESS's own run (`current_run_id()`) has run state on disk. This is the
                      rung `find_active_run` structurally could not have: an MCP process that
                      registered a run IS that run's driver, so amid five bare live runs it resolves
                      to its own rather than scanning for someone else's. A CLI process that never
                      registered mints an id that names nothing on disk, so this rung stays silent
                      for it and costs nothing.
    (vi)   SINGLE     exactly ONE run with state — no foreign run exists to confuse it with.
    (vii)  LIVE       exactly ONE run whose process is alive and rooted here (the R-MCP narrowing).
    (viii) UNSHIPPED  exactly ONE candidate has not reached its terminal `ship` stage — what
                      soundly survives of `find_active_run` (its RETIREMENT half, which narrows;
                      its "first incomplete checkpoint" half, which PICKED, is the defect and is
                      gone).
    (ix)   AMBIGUOUS / NONE.

Ordering notes that are load-bearing, not taste. EVIDENCE outranks OWN — deliberately, and it is
the one place a FACT about the caller yields to an inference about the repo. RE-ENTRY measured why:
going back to `/brainstorm` REGISTERS a bare `pipeline_run__<new>` checkpoint under the re-entering
process's own id, so "my own run" is precisely the empty run, and the approach/spec being amended
belongs to the pipeline it just re-entered. Evidence is a fact about which run the WORK is in; own
is a fact about which run the PROCESS is. Where they differ, the work wins. Where no evidence
exists — five bare live runs, OSS #44's shape — OWN answers and nothing else can.
EVIDENCE outranks LIVE because RE-ENTRY measured the alternative: live-narrowing picks the
brand-new EMPTY run, "which is the opposite of the answer". SINGLE outranks LIVE so that the
one-candidate case costs NO registry read: R-MCP's guarantee that the gate hook — which runs on
every native write — reads nothing new on its happy paths (pin, single candidate) is preserved
exactly. The badge does not lose by this: its display filter asks `_is_live` about the one run it
was given, so a single LIVE run is still badged and a single DEAD one still is not.

Ship retirement (B-LIFE) enters the ladder ONLY as rung (viii)'s NARROWING, never as a filter over
the answer. The distinction is REVIEW-FIX.R1's and it is load-bearing: the verdict key must SURVIVE
`mokata progress mark ship`, or ship's own entry mark would re-key the read away from the run whose
verdict was recorded. Because a one-run repo resolves at SINGLE (rung vi, above the narrowing),
marking that run shipped changes nothing about what resolves. RETIRING a resolved run — refusing to
show a finished run as the current one — stays DISPLAY policy, applied by `resolve_badge_run` and
`progress.build_progress` after resolution, where it cannot reach a gate's evidence key.

Why the badge still differs — and why that is not a second resolver
-------------------------------------------------------------------
`resolve_badge_run` applies a display FILTER on top of the one answer: it shows a run only when
this session is ATTACHED to it (explicit/pinned/own/bound) or a live process is driving it. That
preserves B-BADGE's whole point — after `/clear` no binding is written, so a dead run with evidence
on disk must render the CLEAN badge, not the ghost of last week's pipeline. The filter can only
ever turn an answer into NO answer; it can never turn it into a DIFFERENT one. That is the
invariant #44 needs, and a filter guarantees it where a second ladder did not:

    for any repo state and any caller, every surface either names the SAME run or abstains —
    no two surfaces ever name different run ids.

Never raises: this feeds the statusline, a read-only surface that must always exit 0, and the gate
hook, which must never block a write because a resolver threw. Read-only throughout — it owns the
run-identification primitives (`run_ids`, `live_runs`) the gate hook narrows with, so the gate's
narrowing and the resolver's can never diverge.

Stdlib-only. Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional, Set, Tuple

from .state import StateStore
from .tdd_state import state_dir

# The pipeline-run checkpoint (owner: `govern.resume.CHECKPOINT_PREFIX`). A LITERAL here, not an
# import, and the reason is a latency contract rather than taste: `gate_hook` imports this module at
# module level, the gate hook runs on EVERY native write, and SI.2 pins that its import surface stays
# plain-JSON-cheap — `from .govern.resume import ...` drags in govern → memory → router and fails
# `test_si_1_hook_gates.TestLatency`. `gate_hook` carried the same literal for the same reason before
# RUN-ID-DRIFT moved resolution here; `test_ph_gate_s0` pins it to its owner.
CHECKPOINT_PREFIX = "pipeline_run__"

# The per-session binding key: `session_run_binding__<claude_session_id>.json`. Keyed by Claude
# Code's session_id (NOT mokata's run_id), so it is a plain pass-through key — never session-scoped
# by mokata's own id. Holds exactly one field, `run_id` (secret-safe by construction).
BINDING_PREFIX = "session_run_binding__"

# The run-scoped state prefixes that make a run id a CANDIDATE. Declared here, with the resolver,
# because "what counts as a run" is a resolution question; `gate_hook` imports them from here so
# enforcement and resolution can never disagree about the candidate set.
TDD_STATE_PREFIX = "tdd_state__"
APPROACH_PREFIX = "approved_approach__"
SPEC_PREFIX = "emitted_spec__"
RUN_STATE_PREFIXES = (TDD_STATE_PREFIX, APPROACH_PREFIX, SPEC_PREFIX, CHECKPOINT_PREFIX)

# A parent may PIN the run (a wrapper making several processes share one session; deterministic
# tests). Mirrors `session.SESSION_ID_ENV` — the same variable, read here as a run pin.
PIN_ENV = "MOKATA_SESSION_ID"

# The SessionStart `source` values that may establish a binding — a session that is STARTING or
# RESUMING adopts the single live run. `clear` (and anything else) never binds: the clean badge is
# the whole point of `/clear`.
_BINDING_SOURCES = frozenset({"startup", "resume"})

# ---------------------------------------------------------- the bases (which rung answered)
BASIS_EXPLICIT = "explicit"
BASIS_PINNED = "pinned"
BASIS_OWN = "own"
BASIS_BOUND = "bound"
BASIS_EVIDENCE = "evidence"
BASIS_LIVE = "live"
BASIS_SINGLE = "single"
BASIS_UNSHIPPED = "unshipped"
BASIS_AMBIGUOUS = "ambiguous"
BASIS_NONE = "none"

# The bases that mean "this session is ATTACHED to the run" — the caller named it, pinned it, owns
# the process that registered it, or is bound to it. `resolve_badge_run`'s display filter admits
# these plus a run with a LIVE process; see the module docstring.
ATTACHED_BASES = frozenset({BASIS_EXPLICIT, BASIS_PINNED, BASIS_OWN, BASIS_BOUND})


@dataclass(frozen=True)
class RunResolution:
    """Which run a caller is in — or WHY that could not be determined (Class 1: a real answer and an
    absent answer do not share a representation).

    `run_id is not None` iff resolved, and `basis` then names the rung that answered. Unresolved
    splits in two, and the split is the whole point: `ambiguous` means the repo holds `candidates`
    the resolver refused to choose between (say so, offer `--run`), while a plain empty resolution
    means this repo has no run state at all (offer to start one). No caller may collapse them."""

    run_id: Optional[str] = None
    basis: str = BASIS_NONE
    candidates: Tuple[str, ...] = field(default=())

    @property
    def resolved(self) -> bool:
        return self.run_id is not None

    @property
    def ambiguous(self) -> bool:
        """2+ candidate runs that no rung could narrow — the caller MUST NOT guess."""
        return self.basis == BASIS_AMBIGUOUS

    @property
    def attached(self) -> bool:
        """Whether the answer came from a rung that ties the run to THIS caller (as opposed to an
        inference off repo state). The badge's display filter is written in terms of this."""
        return self.basis in ATTACHED_BASES


# ======================================================================================
# run-identification primitives (owned here; the gate hook narrows with the SAME reads)
# ======================================================================================

def run_ids(directory: str) -> Set[str]:
    """Every run id with run-scoped pipeline state in `directory` (one `scandir`, no file read)."""
    found: Set[str] = set()
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                name = entry.name
                if not name.endswith(".json"):
                    continue
                for prefix in RUN_STATE_PREFIXES:
                    if name.startswith(prefix):
                        found.add(name[len(prefix):-len(".json")])
                        break
    except OSError:
        return set()
    found.discard("")
    return found


def _same_repo(entry_root: str, here: str) -> bool:
    """True when a registry entry's `repo_root` names THIS repo — realpath-compared so a
    symlinked/`/tmp` vs `/private/tmp` root still matches. Degrade-clean: unresolvable -> not-same
    (so a broken path narrows nothing rather than mis-enforcing)."""
    if not here:
        return False
    try:
        return os.path.realpath(entry_root) == here
    except OSError:
        return False


def live_runs(root: str, candidates: Set[str]) -> list:
    """The candidate run ids whose MCP process is registered ALIVE and rooted at this repo (R-MCP).

    A candidate survives iff the MS.S2 live-session registry (`session_registry.SESSION_REGISTRY_KEY`
    in this repo's `state_dir`) holds an entry for it whose `pid` is alive and whose `repo_root`
    resolves to this repo. This is only sound because the MCP server registers EAGERLY at process
    start (`mcp/server.main`) — before RUN-ID-DRIFT it registered on the first tool call it served,
    so a window that had not yet invoked a mokata tool carried only its SessionStart hook's row,
    written by a process that exits within a second: the next window read that row as dead, pruned
    it, and narrowing returned [] with no live sibling to find.

    A read, never a write: it uses `StateStore.read`, NOT `list_sessions` (which prunes), so the
    gate hook keeps its never-writes contract even against a stale registry. Degrade-clean: an
    absent/unreadable/torn registry, or a wrong-shape entry, narrows NOTHING (returns []), so the
    caller falls open exactly as before — the registry can only ever REMOVE ambiguity, never add a
    wrong-window block."""
    from .session_registry import SESSION_REGISTRY_KEY, pid_alive
    try:
        data = StateStore(state_dir(root)).read(SESSION_REGISTRY_KEY)
    except OSError:
        return []
    if not isinstance(data, dict):
        return []
    sessions = data.get("sessions")
    if not isinstance(sessions, dict):
        return []
    try:
        here = os.path.realpath(root)
    except OSError:
        here = ""
    survivors = []
    for run_id in candidates:
        entry = sessions.get(run_id)
        if not isinstance(entry, dict) or not pid_alive(entry.get("pid")):
            continue
        entry_root = entry.get("repo_root")
        if not isinstance(entry_root, str) or not entry_root:
            continue
        if _same_repo(entry_root, here):
            survivors.append(run_id)
    return survivors


def _is_live(root: str, run_id: str) -> bool:
    """Whether ONE named run has a live process rooted here (the badge's display filter)."""
    try:
        return bool(live_runs(root, {run_id}))
    except Exception:
        return False


# ======================================================================================
# the binding (rung iv) — written by the SessionStart hook
# ======================================================================================

def _binding_key(claude_session_id: str) -> str:
    return BINDING_PREFIX + claude_session_id


def bind_session_run(root: str, claude_session_id: str, run_id: str) -> None:
    """Bind `claude_session_id` -> `run_id` (transient run-state; ungated). No-op on a missing
    id/run or any store error — a binding is best-effort surfacing, never worth raising."""
    if not claude_session_id or not run_id:
        return
    try:
        StateStore(state_dir(root)).write(_binding_key(claude_session_id), {"run_id": run_id})
    except Exception:
        # Degrade-clean: the binding is a convenience. If it can't be written, resolution falls
        # through to the rungs below it exactly as if no binding existed. Never raises.
        pass


def read_binding(root: str, claude_session_id: Optional[str]) -> Optional[str]:
    """The `run_id` bound to `claude_session_id`, or None (no binding / no id / unreadable)."""
    if not claude_session_id:
        return None
    try:
        data = StateStore(state_dir(root)).read(_binding_key(claude_session_id))
    except Exception:
        return None
    if isinstance(data, dict):
        run_id = data.get("run_id")
        if isinstance(run_id, str) and run_id:
            return run_id
    return None


def _bound_run(root: str, claude_session_id: Optional[str]) -> Optional[str]:
    """Rung (iv) — the run explicitly BOUND to this Claude Code session, but only if it is STILL on
    record (a bound run whose checkpoint is gone falls through, so a stale binding never resolves to
    a ghost). Never raises."""
    bound = read_binding(root, claude_session_id)
    if bound is None:
        return None
    try:
        return bound if StateStore(state_dir(root)).exists(CHECKPOINT_PREFIX + bound) else None
    except Exception:
        return None


# ======================================================================================
# the remaining rungs
# ======================================================================================

def _own_run(candidates: Set[str]) -> Optional[str]:
    """Rung (iii) — THIS PROCESS's own run, when it has run state on disk.

    `session.current_run_id()` is stable within a process and distinct across processes, so for any
    process that REGISTERED a run (every MCP window does, at RUN-REG) it names that window's own
    run with no inference at all. For a process that registered nothing — a bare `mokata progress`
    CLI invocation — it mints an id that appears in no candidate set, so this rung is silent and
    costs the CLI exactly one in-memory uuid.

    This is the rung the old scanner structurally could not have: `find_active_run` saw a state
    store and nothing about its caller, so amid five live runs it scanned for someone else's."""
    try:
        from .session import current_run_id
        rid = current_run_id()
    except Exception:
        return None
    return rid if rid in candidates else None


def _unshipped(root: str, candidates: Set[str]) -> list:
    """Rung (viii) — the candidates that have NOT reached their terminal `ship` stage.

    This is what SOUNDLY survives of `find_active_run`. That function did two things: it RETIRED
    shipped runs (real, B-LIFE) and then it PICKED "the first incomplete checkpoint" among what was
    left (arbitrary over `uuid4` ids — OSS #44). The pick is deleted; the retirement is kept, and
    kept in the one shape that cannot pick: it NARROWS. A repo carrying last week's finished runs
    plus today's live one resolves to today's, because the finished ones are excluded — and two
    unfinished runs still resolve to nothing, because excluding cannot choose.

    Deliberately the LAST narrowing rung, below live-narrowing, for two reasons. It costs a bounded
    progress-log read, which the earlier rungs do not, so it is only paid on a genuinely ambiguous
    repo. And REVIEW-FIX.R1's guarantee — the verdict key must SURVIVE `mokata progress mark ship` —
    is unaffected by it: a repo with one run resolves at SINGLE, above this, so marking that run
    shipped never re-keys the read away from it.

    Degrade-clean: an unreadable log retires NOTHING, so every candidate stays a candidate and the
    resolution goes ambiguous rather than narrowing on evidence it could not read."""
    try:
        from .progress import _shipped_run_ids
        shipped = _shipped_run_ids(StateStore(state_dir(root)))
    except Exception:
        return []
    return sorted(candidates - set(shipped))


def _evidence_runs(root: str) -> Set[str]:
    """The runs in this repo holding PIPELINE EVIDENCE — an `approved_approach__<run>` or an
    `emitted_spec__<run>`. Exactly one is rung (iv)'s answer; the SET is returned rather than that
    one answer because the resolver needs to tell "no pipeline here" from "two pipelines here", and
    those two cases take different branches (see `resolve_run`).

    Why evidence and not "run state": the candidate set counts FOUR prefixes, one of them the bare
    `pipeline_run__<run>` checkpoint that RUN-REG writes at protocol start. So the moment a user
    goes back to `/brainstorm` in a second window, that window REGISTERS a checkpoint and becomes a
    candidate indistinguishable from the pipeline it just re-entered — the resolver then either goes
    ambiguous or (via live-narrowing) picks the brand-new EMPTY run, which is the opposite of the
    answer. A bare checkpoint says "a process is here"; the approach and the spec say "the pipeline
    is here", and it is the pipeline a caller means.

    Read-only and never raises: an unreadable directory reads as NO evidence, which is safe here
    because the SAME directory feeds `run_ids` one rung earlier — if it cannot be scanned there are
    no candidates either, and the ladder has already returned an empty resolution."""
    found: Set[str] = set()
    try:
        with os.scandir(state_dir(root)) as entries:
            for entry in entries:
                name = entry.name
                if not name.endswith(".json"):
                    continue
                for prefix in (APPROACH_PREFIX, SPEC_PREFIX):
                    if name.startswith(prefix):
                        found.add(name[len(prefix):-len(".json")])
                        break
    except OSError:
        return set()
    found.discard("")
    return found


# ======================================================================================
# THE resolver
# ======================================================================================

def resolve_run(root: str, *, session_id: Optional[str] = None,
                run_id: Optional[str] = None) -> RunResolution:
    """Which run the caller is in — the ONE answer every surface reads. See the module docstring for
    the ladder and for why each ordering choice is load-bearing.

    `session_id` is CLAUDE CODE's session id (hooks receive it on stdin; the MCP process cannot see
    it — harness gap #25642), used only by the BOUND rung. Omitting it simply skips that rung.

    Never raises and never writes: any failure at any rung resolves to an empty (unresolved)
    result, which is the strict outcome — callers must handle it, and none of them may guess."""
    try:
        if run_id:
            return RunResolution(run_id, BASIS_EXPLICIT)

        pinned = os.environ.get(PIN_ENV, "").strip()
        if pinned:
            return RunResolution(pinned, BASIS_PINNED)

        candidates = run_ids(state_dir(root))
        if not candidates:
            return RunResolution(None, BASIS_NONE)

        bound = _bound_run(root, session_id)
        if bound is not None:
            return RunResolution(bound, BASIS_BOUND)

        evidence = _evidence_runs(root)
        if len(evidence) == 1:
            return RunResolution(next(iter(evidence)), BASIS_EVIDENCE)

        # 2+ EVIDENCE runs means two GENUINE pipelines share this repo, and REVIEW-FIX.R1 settled
        # what happens there: mokata refuses rather than picks a window — including when one of them
        # is this process's own. So OWN is skipped on this branch. It is not an oversight that a
        # fact about the caller loses here: with two real pipelines, "mine" is a preference between
        # two valid answers, which is the window-picking R1 forbade. Only the registry's LIVE
        # narrowing may still speak, exactly as before this stage.
        if not evidence:
            own = _own_run(candidates)
            if own is not None:
                return RunResolution(own, BASIS_OWN)

        if len(candidates) == 1:
            return RunResolution(next(iter(candidates)), BASIS_SINGLE)

        survivors = live_runs(root, candidates)
        if len(survivors) == 1:
            return RunResolution(survivors[0], BASIS_LIVE)

        unshipped = _unshipped(root, candidates)
        if len(unshipped) == 1:
            return RunResolution(unshipped[0], BASIS_UNSHIPPED)

        # Narrowing could not narrow. mokata does NOT pick: it names the candidates and lets the
        # caller ask the human which run they meant. This is OSS #44's shape, and the arbitrary
        # pick that used to happen here is the whole defect.
        return RunResolution(None, BASIS_AMBIGUOUS, tuple(sorted(candidates)))
    except Exception:
        return RunResolution(None, BASIS_NONE)


def resolve_run_id(root: str, *, session_id: Optional[str] = None,
                   run_id: Optional[str] = None) -> Optional[str]:
    """The resolved run id, or None. The convenience form for surfaces that only need the id and
    treat both unresolved cases the same way — they must still not GUESS on None."""
    return resolve_run(root, session_id=session_id, run_id=run_id).run_id


def unresolved_reason(res: RunResolution, *, flag: str = "--run") -> str:
    """The one sentence a surface says when it has no run — DIFFERENT for the two unresolved cases,
    because they need different things from the human (Class 1 on the user-facing surface too).

    Ambiguous ⇒ the runs exist and one of them is theirs: name it. Empty ⇒ there is nothing to name:
    start a tracked run. Shared by every surface so the remedy is worded once."""
    if res.ambiguous:
        n = len(res.candidates)
        shown = ", ".join(r[:8] for r in res.candidates[:5])
        more = f" (+{n - 5} more)" if n > 5 else ""
        return (f"{n} runs have state in this repo and none of them is identifiably yours, so "
                f"mokata will not guess which one you mean: {shown}{more}. "
                f"-> to unblock: name it — `{flag} <run id>` (`mokata sessions` lists them).")
    return ("no tracked run in this repo. -> to unblock: start one with /mokata:brainstorm, "
            "or name an existing run explicitly.")


# ======================================================================================
# the badge (the one surface with a display policy on top of the one answer)
# ======================================================================================

def _run_is_shipped(root: str, run_id: str) -> bool:
    """Whether `run_id` reached its terminal END-OF-RUN signal (`stage_enter: ship` in the progress-
    event log — the strongest end-of-run evidence, since `STAGE_PASS`/a ship-completion event are
    never written). B-LIFE keys retirement on SHIP, NOT the pipeline checkpoint: a complete
    checkpoint means the spec emitted and the user is AT develop (active), which must keep its
    badge. Degrade-clean: any read problem reads as NOT shipped, so an unreadable run is shown
    (never wrongly retired).

    REVIEW-FIX.R2 — asks the RUN-FILTERED question (`_shipped_run_ids(store, run_id)`), so the log
    scan runs backward and stops at this run's own latest stage: retirement can no longer be
    truncated out of the read window, and the badge reads only as far as it must."""
    try:
        from .progress import _shipped_run_ids
        return run_id in _shipped_run_ids(StateStore(state_dir(root)), run_id)
    except Exception:
        return False


def resolve_badge_run(root: str, claude_session_id: Optional[str]) -> Optional[str]:
    """The run the BADGE should show for a Claude Code session — the ONE resolution, plus two
    DISPLAY policies that can only ever narrow it to "show nothing". Read-only and degrade-clean:
    any error resolves to None (the clean badge), never a raise.

    ATTACHED-OR-LIVE (B-BADGE) — a run is badged only when this session is attached to it
    (explicit/pinned/own/bound) or a live process is driving it. Run state deliberately survives
    `/clear` (P17 resume — correct) and `/clear` deliberately writes no binding, so without this
    filter a cleared session would wear the strip of a dead run still on disk: the exact live report
    B-BADGE was built for. The filter never selects a DIFFERENT run than the resolver's — that is
    what keeps this a display policy rather than a second resolver (see the module docstring).

    B-LIFE — a resolved run that has SHIPPED is retired from the badge (=> the clean strip): a
    finished run is not the current state. A spec-emitted run only AT develop/review is NOT retired
    — develop/review/ship is the healthy active arc. DISPLAY-only: the binding record and the
    checkpoint stay on disk untouched (the run is still resumable / viewable by explicit id), and
    retirement is deliberately absent from the ladder itself, since REVIEW-FIX.R1 needs the verdict
    key to survive `mokata progress mark ship`."""
    try:
        res = resolve_run(root, session_id=claude_session_id)
        if not res.resolved:
            return None
        if not res.attached and not _is_live(root, res.run_id):
            return None
        if _run_is_shipped(root, res.run_id):
            return None
        return res.run_id
    except Exception:
        return None


def maybe_bind_on_session_start(root: str, claude_session_id: Optional[str],
                                source: Optional[str]) -> Optional[str]:
    """SessionStart writer for the session binding (the only in-scope process that sees Claude
    Code's `session_id` at a session boundary — the MCP registrar cannot, harness gap #25642).

    Binds `claude_session_id` -> the run ONLY when BOTH hold: `source` is `startup`/`resume` (never
    `clear` — the clean badge is the point of `/clear`), AND exactly ONE live run exists (the R-MCP
    narrowing). Ambiguous (2+ live) or zero live ⇒ NO binding, so resolution falls through to the
    rungs below — the narrowing can only ever REMOVE ambiguity, never manufacture a wrong pick.
    Returns the bound run_id (or None). Never raises: SessionStart is async/observability and must
    never block."""
    try:
        if not claude_session_id or source not in _BINDING_SOURCES:
            return None
        candidates = run_ids(state_dir(root))
        survivors = live_runs(root, candidates) if candidates else []
        if len(survivors) != 1:
            return None
        bind_session_run(root, claude_session_id, survivors[0])
        return survivors[0]
    except Exception:
        return None


__all__ = [
    "RunResolution", "resolve_run", "resolve_run_id", "unresolved_reason",
    "resolve_badge_run", "maybe_bind_on_session_start",
    "bind_session_run", "read_binding",
    "run_ids", "live_runs",
    "BASIS_EXPLICIT", "BASIS_PINNED", "BASIS_OWN", "BASIS_BOUND", "BASIS_EVIDENCE",
    "BASIS_LIVE", "BASIS_SINGLE", "BASIS_UNSHIPPED", "BASIS_AMBIGUOUS", "BASIS_NONE",
    "ATTACHED_BASES",
    "APPROACH_PREFIX", "SPEC_PREFIX", "TDD_STATE_PREFIX", "RUN_STATE_PREFIXES", "BINDING_PREFIX",
]
