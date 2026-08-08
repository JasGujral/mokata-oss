"""SI.2 — the PERSISTED TDD RED/GREEN phase (0.0.13 seatbelt cluster, stage 1).

`govern/tdd.py:TddGuard` is the executable form of the `no-code-without-failing-test` gate (doc 85
§4 — one of the 8 backed gates): implementation of a behaviour is refused until a test for it has
been recorded FAILING. But that RED/GREEN record lived in TWO in-memory `set`s on the guard
INSTANCE, so the discipline was only as durable as the process holding it: exit the CLI, crash the
window, or simply construct a second guard, and every owed test evaporated — post-restart writes
proceeded as if the methodology had never asked for a failing test. The gate was real only while
the process lived.

This module is the durable truth behind that gate. The phase is persisted per `run_id`, through the
MS.S1 `StateStore` (atomic temp-file → fsync → `os.replace`, under the MS.S6/oslock cross-process
lock held across the whole read-modify-write), so a `kill -9` mid-RED leaves either the whole old
phase or the whole new one — never a fragment — and a fresh process on the same run resumes exactly
what it owed. `TddGuard` keeps its two sets as a CACHE of this file; the file is the truth.

The persisted key — granularity
--------------------------------
    tdd_phase__<run_id>          (one JSON file per run, in the state dir)

Per RUN, holding a set of test ids — because that is the granularity the guard already had. RED and
GREEN are recorded against a `test_id` (`TddGuard.record_red("test_login")`, and `BugFlow` uses
`bug:<id>`), and every enforcement question is asked about a test id (`allow_implementation(test)`).
There is no per-spec or per-AC dimension in the guard to persist. The RUN is the scope that owns
those ids: `run_id == session_id` (see `session.py`), so a per-run key is session-scoped for free —
window A's RED cannot leak into window B's run — exactly like the `pipeline_run__<run_id>`
checkpoints. The key is therefore NOT in `session_state.SESSION_SCOPED_KEYS`: it already carries the
session dimension in its name and passes through a `SessionScopedStore` verbatim.

Value shape (stable; no schema change to the state format — this is a new key, not a new field):

    {"run_id": "<rid>", "red": ["test_a", "test_b"], "green": ["test_a"]}

`red` = every test seen FAILING in this run. `green` = every test that has since PASSED. Both are
sorted, deduped id lists (deterministic bytes; a re-record is idempotent). The derived phase:

    unset  — no test has been recorded RED in this run (a fresh run; nothing owed)
    red    — at least one RED test has not yet been recorded GREEN  (`owed` names them)
    green  — every RED test in this run has since been recorded GREEN

The cheap read — SI.1's contract
---------------------------------
`read_tdd_phase(root, run_id=...)` is THE read path for a hook (SI.1's `PreToolUse` is a SEPARATE,
short-lived process spawned per tool call, with a latency budget measured in milliseconds). Its
contract, which SI.1 builds on:

  * **One content read.** It `open()`s exactly one file — `<root>/.mokata/temp_local/state/
    tdd_phase__<run_id>.json` — with no lock taken (a lock-free read can never observe a torn file:
    every write lands by `os.replace`).
  * **Cheap import surface.** This module imports stdlib + `mokata.state` (→ `atomicfile`, `oslock`)
    only. It pulls in NO engine, NO govern package, NO config/manifest/router, NO memory layer — so
    a hook pays a plain-JSON import cost, not a framework boot. `test_si_2_*` asserts that surface.
  * **Never raises, never blocks.** Absent / corrupt / unreadable state degrades to
    `phase == "unset"` (P11 degrade-clean). A hook must fail OPEN, never wedge a session.
  * **run_id resolution** (in order): the explicit `run_id` argument · the `MOKATA_SESSION_ID` pin
    (`session.SESSION_ID_ENV`; `run_id == session_id`) · else, if the state dir holds exactly ONE
    `tdd_phase__*.json`, that run (the unambiguous single-window case). If several runs have TDD
    state and the caller named none, the answer is `unset` with `run_id=None` — we refuse to GUESS
    which window a hook belongs to rather than leak another window's RED into it. **SI.1 must
    therefore pass `run_id`, or launch its hook with `MOKATA_SESSION_ID` pinned**, for enforcement
    to hold with two windows open. The optional directory listing happens only when the caller
    supplies neither, and is a `scandir` (no file contents read).

Stdlib-only; clean-room. Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Set, Tuple

from . import MOKATA_DIR, TEMP_LOCAL_DIRNAME
from .state import StateStore

# The per-run state key. Mirrors the `pipeline_run__<run_id>` convention (govern/resume.py) — the
# run id is IN the name, so the key is session-scoped for free and passes through a
# SessionScopedStore unchanged.
TDD_STATE_PREFIX = "tdd_phase__"

# The state subdirectory, spelled as a literal to keep this module's import surface cheap for hooks:
# the owner is `config.STATE_DIRNAME`, but importing `config` would drag in detect/manifest/router.
# Same technique as `session_state.py`'s scoped-key literals; pinned to config by test_si_2_*.
STATE_DIRNAME = "state"

# The three phases of a run's TDD cycle.
PHASE_UNSET = "unset"
PHASE_RED = "red"
PHASE_GREEN = "green"
PHASES = (PHASE_UNSET, PHASE_RED, PHASE_GREEN)


@dataclass(frozen=True)
class TddPhase:
    """A run's persisted TDD phase, as read from disk. A read-only view — it enforces nothing (that
    is SI.1's job, next stage); it only says what the run owes."""

    run_id: Optional[str]
    phase: str                    # one of PHASES
    owed: Tuple[str, ...]         # RED tests not yet recorded GREEN (empty unless phase == "red")
    red: Tuple[str, ...]          # every test seen FAILING in this run
    green: Tuple[str, ...]        # every test that has since PASSED

    @property
    def is_red(self) -> bool:
        return self.phase == PHASE_RED


UNSET_PHASE = TddPhase(run_id=None, phase=PHASE_UNSET, owed=(), red=(), green=())


def state_key(run_id: str) -> str:
    """The StateStore key holding `run_id`'s TDD phase."""
    return TDD_STATE_PREFIX + run_id


def state_dir(root: str) -> str:
    """The state directory for a repo root — `<root>/.mokata/temp_local/state/` (config's layout,
    resolved without importing config; see STATE_DIRNAME)."""
    return os.path.join(root, MOKATA_DIR, TEMP_LOCAL_DIRNAME, STATE_DIRNAME)


def root_of_state_dir(directory: str) -> Optional[str]:
    """The INVERSE of `state_dir` — the repo root a state directory belongs to, or None when
    `directory` is not one (`state_dir(root_of_state_dir(d)) == d` for every real state dir).

    RUN-ID-DRIFT needs it because run resolution is rooted at the REPO (the session registry, the
    session binding and the candidate scan all live under one root) while the read surfaces
    — `build_progress`, `build_run_lanes`, the badge — are handed a `StateStore`. Every one of them
    has the repo in hand indirectly; this makes that explicit and single-sourced instead of each
    caller doing its own `dirname(dirname(dirname(...)))`, which is the kind of duplicated path math
    that goes wrong silently when the layout moves.

    Declared HERE, next to `state_dir`, so the pair moves together: a change to the layout that
    forgets this function fails `test_run_id_drift`'s round-trip pin rather than degrading a surface
    to "no run found". Returns None (never guesses) when the tail does not match the layout — a
    synthetic store in a test, or any directory that is simply not a mokata state dir."""
    parts = os.path.normpath(directory).split(os.sep)
    tail = (MOKATA_DIR, TEMP_LOCAL_DIRNAME, STATE_DIRNAME)
    if len(parts) <= len(tail) or tuple(parts[-len(tail):]) != tail:
        return None
    return os.sep.join(parts[:-len(tail)]) or os.sep


def phase_of(red: Iterable[str], green: Iterable[str]) -> str:
    """The phase implied by the recorded sets (see the module docstring's table)."""
    red_set, green_set = set(red), set(green)
    if not red_set:
        return PHASE_UNSET
    return PHASE_GREEN if red_set <= green_set else PHASE_RED


def to_state(run_id: str, red: Iterable[str], green: Iterable[str]) -> Dict[str, Any]:
    """The persisted value for `run_id` — sorted, deduped id lists (deterministic bytes)."""
    return {"run_id": run_id, "red": sorted(set(red)), "green": sorted(set(green))}


def from_state(data: Any) -> Tuple[Set[str], Set[str]]:
    """`(red, green)` parsed out of a persisted value. Degrade-clean: a corrupt / wrong-shape value
    (not a mapping, a `red` that isn't a list, a non-string id) reads as EMPTY rather than raising
    into a gate or a hook — the same floor `PipelineCheckpoint` takes."""
    if not isinstance(data, dict):
        return set(), set()

    def _ids(value: Any) -> Set[str]:
        if not isinstance(value, list):
            return set()
        return {item for item in value if isinstance(item, str)}

    return _ids(data.get("red")), _ids(data.get("green"))


def as_phase(run_id: Optional[str], data: Any) -> TddPhase:
    """Render a persisted value (or None) as a `TddPhase`."""
    red, green = from_state(data)
    owed = sorted(red - green)
    return TddPhase(
        run_id=run_id,
        phase=phase_of(red, green),
        owed=tuple(owed),
        red=tuple(sorted(red)),
        green=tuple(sorted(green)),
    )


def load(store: Any, run_id: str) -> Tuple[Set[str], Set[str]]:
    """`(red, green)` for `run_id` from `store` — the persisted truth a guard rehydrates from."""
    return from_state(store.read(state_key(run_id)))


def record(store: Any, run_id: str, *, red: Iterable[str] = (),
           green: Iterable[str] = ()) -> Tuple[Set[str], Set[str]]:
    """Persist a transition and return the new `(red, green)`.

    A locked read-modify-write (`StateStore.update`) that UNIONS the given ids into what is already
    on disk, so a transition recorded by another process on the same run is never lost and a
    re-record is idempotent. The write is atomic (temp file → fsync → `os.replace`), so a `kill -9`
    at any instant leaves the whole previous phase or the whole new one — never a torn file."""
    add_red, add_green = set(red), set(green)

    def _mutator(current: Any) -> Dict[str, Any]:
        cur_red, cur_green = from_state(current)
        return to_state(run_id, cur_red | add_red, cur_green | add_green)

    new = store.update(state_key(run_id), _mutator, default=None)
    return from_state(new)


def _sole_run_id(directory: str) -> Optional[str]:
    """The run id of the ONLY `tdd_phase__*.json` in `directory`, or None when there is no such file
    or more than one (ambiguous — see the module docstring: we never guess between two windows)."""
    found: Optional[str] = None
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                name = entry.name
                if not name.startswith(TDD_STATE_PREFIX) or not name.endswith(".json"):
                    continue
                if found is not None:
                    return None                      # ambiguous
                found = name[len(TDD_STATE_PREFIX):-len(".json")]
    except OSError:
        return None
    return found or None


def resolve_run_id(root: str, run_id: Optional[str] = None) -> Optional[str]:
    """The run whose TDD phase a caller (a hook) is asking about — see the module docstring's
    resolution order. Returns None when it cannot be established WITHOUT guessing."""
    if run_id:
        return run_id
    from .session import SESSION_ID_ENV       # a constant; `session` imports only stdlib
    pinned = os.environ.get(SESSION_ID_ENV, "").strip()
    if pinned:
        return pinned
    return _sole_run_id(state_dir(root))


def read_tdd_phase(root: str = ".", run_id: Optional[str] = None) -> TddPhase:
    """THE cheap read: this repo/run's current TDD phase, from disk, in ONE content read.

    The function SI.1's `PreToolUse` hook calls. It takes no lock, imports no engine, and never
    raises: an absent / corrupt / unresolvable state reads as `unset` (fail open — a hook must never
    wedge a session). See the module docstring for the full contract, including why a caller with
    two windows open MUST pass `run_id` (or pin `MOKATA_SESSION_ID`)."""
    rid = resolve_run_id(root, run_id)
    if not rid:
        return UNSET_PHASE
    data = StateStore(state_dir(root)).read(state_key(rid))   # one open(); degrades to None
    if data is None:
        return TddPhase(run_id=rid, phase=PHASE_UNSET, owed=(), red=(), green=())
    return as_phase(rid, data)
