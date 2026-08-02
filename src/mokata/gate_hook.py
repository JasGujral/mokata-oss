"""SI.1 — the run-state gates, decided from PERSISTED state alone (0.0.13 seatbelt cluster, 2).

Every mokata gate fired INSIDE mokata's MCP write tools. The model could simply use its NATIVE
`Write`/`Edit` and no gate ever ran — "the whole seatbelt has a door with no lock" (doc 74). SI.2
made the TDD phase survive the process; THIS module is the lock: a `PreToolUse` hook (see
`hook_cli.gate_guard_main`) that runs on the native file-mutation tools, decides from state on disk,
and exits 2 on a violation. The gate stops being advice the model may ignore and becomes an exit
code it cannot — enforcement moves from *inside our tools* to *the harness itself*.

This module is the DECISION; the hook is only its I/O. It is pure and side-effect-free (except the
once-per-session notice marker), so the whole table below is unit-testable without a subprocess.

The enforced gates — and their persisted signals
-------------------------------------------------
A gate is enforced only where a signal on disk can DECIDE it. Both gates are **positively
triggered**: they fire only when this run's own state proves a mokata pipeline run is under way.
Absence of state means "no run is driving this edit", NOT "a violation" — so a mokata repo being
hand-edited outside a run is never policed. That floor is not a compromise, it is the point: a gate
that makes the editor unusable would be uninstalled, and an uninstalled gate enforces nothing.

    run-scoped signal (key)                    written by
    ---------------------------------------    ---------------------------------------------
    approved_approach__<run_id>                brainstorm.persist_approach (approach APPROVED)
    emitted_spec__<run_id>                     engine/phases.py (the `emit` phase)
    tdd_phase__<run_id>                        govern/tdd.TddGuard.record_red / record_green (SI.2)

The decision for a native write to an IMPLEMENTATION file (a test file is ALWAYS allowed — you must
be able to write the failing test, and a gate that blocks the test blocks the fix):

    approach absent AND spec absent    -> ALLOW   no active mokata run; not our business
    approach present, spec absent      -> BLOCK   `spec-persisted`  (coding before the spec)
    spec present, red-set EMPTY        -> BLOCK   `no-code-without-failing-test`
    spec present, red-set NON-EMPTY    -> ALLOW   a failing test is on record

The direction of the TDD gate — read this before "fixing" it
-------------------------------------------------------------
RED is the PERMISSION to implement, not the prohibition. `TddGuard.allow_implementation(test_id)` is
`test_id in self._red`: implementation is refused until a test for it has been recorded FAILING.
So the violation this hook blocks is an implementation write with **no failing test on record at
all** (`phase == unset` / an empty red-set) — never "a write while RED", which is exactly the state
`/mokata:develop` is FOR. Inverting it would block the model precisely when mokata tells it to write
the code, and wave it through when the backed gate says stop.

The red-set is a HIGH-WATER MARK, not a level: `phase_of` returns `green` when every RED test has
since passed, but the red-set still CONTAINS those ids — matching `allow_implementation`'s
red-membership test, under which a greened test still licenses implementation for the rest of the
run (a refactor after green is not a gate violation). So this hook keys on `red-set empty` (nothing
was ever owed), NOT on `phase != red`. A refinement cycle re-records RED for its new behaviour.

Window identity — why this hook refuses to guess
-------------------------------------------------
A hook is a SEPARATE, short-lived process spawned by Claude Code per tool call. It does not share
memory, env, or identity with the MCP process that owns the run: mokata's `run_id` is a `uuid4`
minted INSIDE the MCP server (`session.py`), and Claude Code's own `session_id` (which the hook DOES
receive on stdin) is a different namespace. Neither process can see the other's id. So the run is
resolved WITHOUT guessing (SI.2's contract, extended to all three run-scoped keys):

    1. the `MOKATA_SESSION_ID` pin, if the hook's env carries one   (exact)
    2. else, if exactly ONE run id has run-scoped state in this repo, that run  (unambiguous)
    3. else, with two or more candidate runs, NARROW on the MS.S2 live-session registry (R-MCP):
       the candidates whose MCP process is ALIVE and rooted at this repo. Exactly ONE survivor ->
       that run (its window is the real driver). Zero or 2+ -> stay ambiguous.
    4. else AMBIGUOUS -> fail OPEN + a once-per-session notice. Never a block.

Rules 3–4 are what make wrong-window blocking STRUCTURALLY impossible: the registry can only ever
REMOVE ambiguity (name the single live driver), never manufacture a pick — with two live windows on
one repo the narrowing yields two survivors and the hook still declines to choose, so it can never
enforce window A's RED against window B's editor no matter which of them is red. Enforcement lands
where the repo has one run's state, where the pin is set, or where the registry resolves the repo to
a single live run; everywhere else the honest answer is "I cannot tell", and the honest behaviour is
to get out of the way.

Now USED for disambiguation (this is R-MCP's change): the MS.S2 live-session registry. It became a
SOUND narrower once the MCP server started self-registering its run on the FIRST tool call
(`mcp/server._with_registration`) — registration is now STRUCTURAL, not user-dependent, so it no
longer matters whether anyone ran `mokata windows`. `_live_runs` reads it (never prunes — a read,
not a write) and keeps only pid-alive entries rooted here; an absent/unreadable registry simply
narrows nothing and the hook falls open exactly as rule 4. (The SessionStart hook's own transient
registration is unrelated — its pid dies instantly and is pruned; only the live MCP process survives
the pid-alive filter.)

Cheap by construction: stdlib + `state` + `tdd_state` (SI.2's surface — no engine, no govern, no
config/manifest/router). At most four small JSON reads, plus — on the AMBIGUOUS path only — ONE more
for the registry narrowing (`_live_runs`); the happy paths (pin, single candidate) read nothing new.
No lock taken (every write lands by `os.replace`, so a lock-free read can never see a torn file).
Never raises: any error, anywhere, degrades to ALLOW.

Stdlib-only; clean-room. Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Sequence, Set, Tuple

from . import MOKATA_DIR, TEMP_LOCAL_DIRNAME
from .spec_scope import SCOPE_KEY, amend_from_state, amend_key, classify, scope_from_dict
from .state import StateStore
from .tdd_state import PHASE_UNSET, TDD_STATE_PREFIX, from_state, state_dir

# The PreToolUse block code. A non-zero exit blocks the tool call; 2 is mokata's reserved
# gate/security-block code (pinned to `hook_cli.BLOCK_EXIT` by test_si_1_*).
BLOCK_EXIT = 2

# The enforced gates. The first two ids are the SAME strings the in-tool gates already use
# (`govern/tdd.py:GATE_ID`, `engine/spec_gate.py:SPEC_PERSISTED_GATE_ID`), spelled as literals here
# to keep the hook's import surface cheap — pinned to their owners by test_si_1_*. This hook is a
# NET UNDER those gates, not a second opinion: same id, same meaning, new enforcement point.
#
# SI-DEV adds the third: a write OUTSIDE what the spec authorized — or spelling something the spec
# explicitly DEFERRED — is a scope change, and scope enters through the gate or not at all (P4). It
# is one gate id, not two, because the amend-in-progress block is the SAME gate in its regressed
# phase: a human who overrides `spec-scope` is saying "I am taking responsibility for scope", and
# that one decision should not have to be made twice.
GATE_TDD = "no-code-without-failing-test"
GATE_SPEC = "spec-persisted"
GATE_SCOPE = "spec-scope"

# PH-GATE.S0 — the PHASE-write gate. Its id IS the pipeline's brainstorm boundary
# (`pipeline.PHASE_GATES["brainstorm"].id`), so the hook is a NET UNDER that boundary, not a second
# opinion — the same "same id, new enforcement point" discipline as the three above. Enforcing it
# here is what turns the `approach-approval` boundary from advisory into a BACKED gate (doc 76 FU-1;
# pinned to the pipeline id by test_ph_gate_s0). It fires only inside a REGISTERED run (a
# `pipeline_run__<run_id>` checkpoint on disk) whose phase is still brainstorm — no approach
# approved, no spec emitted — so a native implementation write before an approach exists is blocked.
GATE_PHASE = "approach-approval"
GATES: Tuple[str, ...] = (GATE_SPEC, GATE_TDD, GATE_SCOPE, GATE_PHASE)

# The run-scoped state keys this hook reads. Literals for the same reason (owners:
# `brainstorm.APPROACH_STATE_KEY`, `engine.spec_gate.SPEC_STATE_KEY`, `tdd_state.TDD_STATE_PREFIX`).
# The `__<run_id>` suffix is `session_state.SessionScopedStore._phys`'s physical naming.
APPROACH_PREFIX = "approved_approach__"
SPEC_PREFIX = "emitted_spec__"

# The pipeline-run checkpoint (owner: `govern.resume.CHECKPOINT_PREFIX`). A literal here for the
# same cheap-import reason as the gate-id literals — pinned to its owner by test_ph_gate_s0. Its
# mere EXISTENCE is what PH-GATE.S0 binds on: a checkpoint means a mokata pipeline run is REGISTERED
# and under way (RUN-REG made that structural — protocol-start writes it), which the run resolver
# and the phase gate both now read. Keyed by run_id (== session_id), so it is read as a plain
# pass-through key, exactly like the three above.
CHECKPOINT_PREFIX = "pipeline_run__"

# The P14 session-scoped override (written by `mokata gate override`, read here). Keyed by run_id,
# so it EXPIRES with the run: a new session mints a new run_id, which has no override file, and
# enforcement is back. There is deliberately NO env-var kill switch — an env var is a side door any
# process can open silently, not an explicit, re-confirmed, ledgered human decision (P14).
OVERRIDE_PREFIX = "gate_override__"

# The once-per-session ambiguity notice marker. Keyed by Claude Code's OWN session id (the one
# identity the hook reliably has, straight off stdin) so the notice is shown once per WINDOW.
# Dot-prefixed and NOT `.json`, so state-dir scans (progress.list_runs, and this module's own
# candidate scan) never pick it up — the same convention as StateStore's lock sidecars.
NOTICE_PREFIX = ".gate_notice__"

# Source files whose creation/edit IS "writing implementation". Anything else (markdown, JSON, YAML,
# TOML, config, assets) is never blocked: the gates are about code-before-test/spec, and blocking a
# README edit mid-run would be exactly the "house arrest" the positive-trigger rule exists to avoid.
_SOURCE_EXTS = frozenset({
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue", ".svelte",
    ".go", ".rs", ".java", ".kt", ".kts", ".rb", ".php", ".cs", ".swift", ".scala",
    ".c", ".h", ".cc", ".cpp", ".hpp", ".cxx", ".m", ".mm", ".sh", ".bash", ".sql",
})

# Directory names that mark a test tree, and the basename shapes that mark a test file.
_TEST_DIRS = frozenset({"tests", "test", "__tests__", "spec", "specs", "testing"})
_TEST_STEM_PREFIXES = ("test_", "test-")
_TEST_STEM_SUFFIXES = ("_test", "-test", ".test", ".spec", "_spec")

# The tool_input keys naming the target path, per native tool (Write/Edit/MultiEdit/NotebookEdit).
_PATH_KEYS = ("file_path", "notebook_path", "path", "target_file")

# The tool_input keys carrying the CONTENT about to be written — `content` (Write), `new_string`
# (Edit), `new_source` (NotebookEdit), and MultiEdit's per-edit `new_string`s. SI-DEV needs these:
# a deferred feature is usually added to a file that is otherwise perfectly authorized (that is how
# the incident happened — the batch endpoint went into the same module as the single-item ones), so
# path alone cannot see it. The text can. The hook only ever MATCHES LITERAL MARKERS in it — it does
# not interpret the code, and it is never sent anywhere.
_CONTENT_KEYS = ("content", "new_string", "new_source")
_EDITS_KEY = "edits"

# The most content the marker scan will read. A marker is a short literal token; scanning an
# unbounded blob to find one would put a file-size-dependent cost on every native write, which is
# exactly the kind of latency a hook must not have. 256 KiB covers any source file a model writes.
MAX_SCAN_BYTES = 256 * 1024


# ======================================================================================
# verdict
# ======================================================================================

@dataclass(frozen=True)
class GateOutcome:
    """A gate verdict for ONE native write (doc 85 §3: `*Outcome` = gate verdict)."""

    allowed: bool
    reason: str
    gate: Optional[str] = None          # the gate that decided (None when nothing applied)
    overridden: bool = False            # allowed only because of a ledgered P14 override
    notice: Optional[str] = None        # a once-per-session note (never a block)

    @property
    def exit_code(self) -> int:
        return 0 if self.allowed else BLOCK_EXIT


@dataclass(frozen=True)
class RunResolution:
    """Which run this hook is deciding for — or that it honestly cannot tell."""

    run_id: Optional[str]
    ambiguous: bool = False
    candidates: Tuple[str, ...] = ()


ALLOW_NO_ROOT = GateOutcome(True, "not a mokata project")


# ======================================================================================
# paths
# ======================================================================================

def find_mokata_root(start: str = ".") -> Optional[str]:
    """The nearest ancestor of `start` holding an initialized `.mokata/manifest.json`, else None.

    The cheap twin of `config.find_project_root` (which would drag in detect/manifest/router). It
    answers only the question the hook asks — "is this an initialized mokata repo, and where is its
    root" — so a NON-mokata repo costs one `os.path.exists` per ancestor and an instant exit 0.
    Pinned to `config.find_project_root` for initialized repos by test_si_1_*."""
    try:
        cur = os.path.abspath(start)
    except OSError:
        return None
    while True:
        if os.path.exists(os.path.join(cur, MOKATA_DIR, "manifest.json")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def is_test_path(path: str) -> bool:
    """True when `path` names a TEST file — by its directory (a `tests/` tree) or its basename
    (`test_x.py`, `x_test.go`, `x.test.ts`, `x.spec.ts`). Test writes are ALWAYS allowed."""
    if not path:
        return False
    norm = path.replace("\\", "/")
    parts = [p for p in norm.split("/") if p]
    if any(p.lower() in _TEST_DIRS for p in parts[:-1]):
        return True
    stem = os.path.splitext(parts[-1])[0].lower() if parts else ""
    if stem.startswith(_TEST_STEM_PREFIXES):
        return True
    return stem.endswith(_TEST_STEM_SUFFIXES)


def is_implementation_path(path: str) -> bool:
    """True when a write to `path` is a write of IMPLEMENTATION code — a source file that is not a
    test file. Everything else (docs, config, data, assets, unknown extensions) is out of scope and
    is never blocked."""
    if not path:
        return False
    ext = os.path.splitext(path)[1].lower()
    if ext not in _SOURCE_EXTS:
        return False
    return not is_test_path(path)


def target_path(tool_input: object) -> Optional[str]:
    """The file a native tool is about to mutate, from its `tool_input`."""
    if not isinstance(tool_input, dict):
        return None
    for key in _PATH_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def target_content(tool_input: object) -> str:
    """The TEXT a native tool is about to write, from its `tool_input` — "" when there is none.

    Handles every native shape: `content` (Write), `new_string` (Edit), `new_source`
    (NotebookEdit), and MultiEdit's `edits: [{new_string: …}, …]` (all of them concatenated — a
    deferred marker in edit #3 counts exactly as much as one in edit #1).

    Bounded by `MAX_SCAN_BYTES`, and never raises: content the hook cannot read is content it
    cannot judge, which means ALLOW."""
    if not isinstance(tool_input, dict):
        return ""
    parts = []
    for key in _CONTENT_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            parts.append(value)
    edits = tool_input.get(_EDITS_KEY)
    if isinstance(edits, list):
        for edit in edits:
            if not isinstance(edit, dict):
                continue
            for key in _CONTENT_KEYS:
                value = edit.get(key)
                if isinstance(value, str) and value:
                    parts.append(value)
    return "\n".join(parts)[:MAX_SCAN_BYTES]


def bash_command(tool_input: object) -> str:
    """The shell command a `Bash` tool call is about to run — "" when there is none.

    SELF-PROTECT (0.0.16 stage 3) parses this for write destinations (`sed -i`, `tee`, redirects,
    `cp` destinations). The RUN-STATE gates deliberately do NOT read it and are unchanged: they
    decide from a target path, and a shell command's real target is a heuristic, not a fact — which
    is fine for an absolute path block and not fine for a methodology gate. Bounded by
    `MAX_SCAN_BYTES` for the same reason `target_content` is; never raises."""
    if not isinstance(tool_input, dict):
        return ""
    value = tool_input.get("command")
    return value[:MAX_SCAN_BYTES] if isinstance(value, str) else ""


# ======================================================================================
# run resolution (the window-identity obligation)
# ======================================================================================

def _run_ids(directory: str) -> Set[str]:
    """Every run id with run-scoped pipeline state in `directory` (one `scandir`, no file read)."""
    found: Set[str] = set()
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                name = entry.name
                if not name.endswith(".json"):
                    continue
                for prefix in (TDD_STATE_PREFIX, APPROACH_PREFIX, SPEC_PREFIX, CHECKPOINT_PREFIX):
                    if name.startswith(prefix):
                        found.add(name[len(prefix):-len(".json")])
                        break
    except OSError:
        return set()
    found.discard("")
    return found


def _live_runs(root: str, candidates: Set[str]) -> list:
    """The candidate run ids whose MCP process is registered ALIVE and rooted at this repo (R-MCP).

    ONE registry read, taken only on the ambiguous path (`resolve_run`, 2+ candidates). A candidate
    survives iff the MS.S2 live-session registry (`session_registry.SESSION_REGISTRY_KEY`, the
    shared pass-through key in this repo's `state_dir`) holds an entry for it whose `pid` is alive
    and whose `repo_root` resolves to this repo. This is only sound because the MCP server now
    self-registers every run on its first tool call (`mcp/server._with_registration`) — the whole
    point of this stage.

    A read, never a write: it uses `StateStore.read`, NOT `list_sessions` (which prunes), so the
    hook keeps its never-writes contract even against a stale registry. Degrade-clean: an
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


def resolve_run(root: str, run_id: Optional[str] = None) -> RunResolution:
    """Which run's gates apply to a write in `root` — see the module docstring's resolution order.

    Never guesses: two or more candidate runs that the live-session registry cannot narrow to
    exactly one is `ambiguous`, which the caller MUST fail open on. That is the whole guarantee
    against wrong-window blocking."""
    if run_id:
        return RunResolution(run_id)
    pinned = os.environ.get("MOKATA_SESSION_ID", "").strip()
    if pinned:
        return RunResolution(pinned)
    candidates = _run_ids(state_dir(root))
    if not candidates:
        return RunResolution(None)                       # no run has state — nothing to enforce
    if len(candidates) == 1:
        return RunResolution(next(iter(candidates)))
    # AMBIGUOUS on disk. R-MCP: the MS.S2 registry now records every MCP process's run structurally
    # (its first tool call self-registers), so it can soundly narrow — a candidate whose process is
    # ALIVE and rooted here is the real driver. Exactly one such run -> enforce against it; zero or
    # 2+ survivors -> still ambiguous, fail open unchanged (two live windows on one repo stay
    # un-decidable, which is what keeps wrong-window blocking structurally impossible).
    survivors = _live_runs(root, candidates)
    if len(survivors) == 1:
        return RunResolution(survivors[0])
    return RunResolution(None, ambiguous=True, candidates=tuple(sorted(candidates)))


# ======================================================================================
# the P14 override (read side)
# ======================================================================================

def override_key(run_id: str) -> str:
    return OVERRIDE_PREFIX + run_id


def read_override(root: str, run_id: str) -> frozenset:
    """The gate ids this run has an explicit, ledgered override for (empty when none).

    Session-scoped by construction: the key carries the run_id, so the override dies with the run.
    Degrade-clean — an absent/corrupt file is simply no override (fail CLOSED on the override, which
    means the gate still enforces; a broken override must never silently disable a gate)."""
    try:
        data = StateStore(state_dir(root)).read(override_key(run_id))
    except OSError:
        return frozenset()
    if not isinstance(data, dict):
        return frozenset()
    scopes = data.get("scopes")
    if not isinstance(scopes, list):
        return frozenset()
    return frozenset(s for s in scopes if isinstance(s, str) and s in GATES)


# ======================================================================================
# the decision
# ======================================================================================

def _exists(store: StateStore, prefix: str, run_id: str) -> bool:
    try:
        return store.exists(prefix + run_id)
    except OSError:
        return False


def _run_registered(store: StateStore, run_id: str) -> bool:
    """Is a mokata pipeline run REGISTERED for `run_id` — i.e. a `pipeline_run__<run_id>` checkpoint
    is on disk? PH-GATE.S0's phase gate binds on this: a registered run with no approach and no spec
    is a run still in its brainstorm phase. Degrade-clean: an unreadable checkpoint reads as
    NOT-registered, so the phase gate falls OPEN on a state it cannot read (a broken checkpoint must
    never manufacture a block — the same fail-open discipline as every other read here)."""
    return _exists(store, CHECKPOINT_PREFIX, run_id)


def _red_set(store: StateStore, run_id: str) -> Optional[Set[str]]:
    """This run's RED test ids — or **None when the state cannot be trusted**, and None must ALLOW.

    The tri-state is the point. `StateStore.read` degrades a CORRUPT file to `None`, which
    `from_state` then renders as an empty red-set — indistinguishable from "no test was ever
    recorded". Collapsing the two would make a truncated write BLOCK the user's editor, which is
    exactly the fail-open violation this hook must not commit. So "absent" (a real, decidable empty
    red-set) and "present but unreadable" (uncertainty) are answered separately."""
    key = TDD_STATE_PREFIX + run_id
    try:
        if not store.exists(key):
            return set()                                 # no TDD state: nothing was ever RED
        data = store.read(key)
    except OSError:
        return None                                      # unreadable -> uncertain -> ALLOW
    if not isinstance(data, dict) or not isinstance(data.get("red"), list):
        return None                                      # present but corrupt/wrong-shape -> ALLOW
    red, _green = from_state(data)
    return red


def _read(store: StateStore, key: str) -> Optional[dict]:
    """One JSON read that never raises — an unreadable key is simply absent (fail open)."""
    try:
        data = store.read(key)
    except OSError:
        return None
    return data if isinstance(data, dict) else None


def _red_set_empty(store: StateStore, run_id: str) -> Optional[bool]:
    """Has this run recorded NO failing test at all? True (empty) / False (something is on record) /
    **None when the state cannot be trusted** — and None must ALLOW.

    The tri-state is the point. `StateStore.read` degrades a CORRUPT file to `None`, which
    `from_state` then renders as an empty red-set — indistinguishable from "no test was ever
    recorded". Collapsing the two would make a truncated write BLOCK the user's editor, which is
    exactly the fail-open violation this hook must not commit. So "absent" (a real, decidable empty
    red-set) and "present but unreadable" (uncertainty) are answered separately.

    Reads the persisted red-set rather than the derived phase, because `green` still licenses
    implementation (the red-set is a high-water mark — see the module docstring)."""
    red = _red_set(store, run_id)
    return None if red is None else not red


def check_write(root: str, path: str, run_id: Optional[str] = None,
                content: str = "") -> GateOutcome:
    """THE decision: may this native write to `path` proceed? See the module docstring's table.

    Pure and total — it never raises and never writes. Every uncertainty (no root, no run, ambiguous
    run, unreadable state, an undeclared scope) resolves to ALLOW.

    `content` is the TEXT about to be written (`target_content` pulls it off the envelope). It is
    used for ONE thing: matching the literal markers a spec declared as DEFERRED. A caller that
    supplies none still gets every path-level check — the marker check simply has nothing to look
    at, which is an allow, not a guess."""
    if not path:
        return GateOutcome(True, "no target path")
    if is_test_path(path):
        return GateOutcome(True, "test file — always writable (you must be able to write the "
                                 "failing test)")
    if not is_implementation_path(path):
        return GateOutcome(True, "not an implementation file")

    run = resolve_run(root, run_id)
    if run.ambiguous:
        return GateOutcome(
            True, "two or more mokata runs have state here and no run is pinned — refusing to "
                  "guess which window this edit belongs to",
            notice=(
                "mokata: run-state gates are OFF for this window — this repo holds state for "
                f"{len(run.candidates)} runs and none is pinned, so mokata cannot tell which run "
                "your edits belong to and will not guess (it would risk blocking on another "
                "window's state). Pin one with MOKATA_SESSION_ID to re-enable enforcement."),
        )
    if run.run_id is None:
        return GateOutcome(True, "no mokata run has state in this repo")

    store = StateStore(state_dir(root))
    has_approach = _exists(store, APPROACH_PREFIX, run.run_id)
    spec_data = _read(store, SPEC_PREFIX + run.run_id)
    has_spec = spec_data is not None or _exists(store, SPEC_PREFIX, run.run_id)

    # The positive trigger. No approach, no spec -> nothing past the brainstorm phase is on record.
    # SPLIT by whether a run is REGISTERED (a checkpoint on disk):
    #   * PH-GATE.S0 — a run IS registered ⇒ its persisted phase is still brainstorm (an approach
    #     is not yet approved and no spec is emitted), so an implementation write is premature. This
    #     is the idea→code jump doc 76 FU-1 names; it now exits 2, escapable only by the P14 override.
    #   * NO registered run ⇒ ordinary hand-editing in a mokata repo. Not policed — the SI.1
    #     fail-open floor, byte-identical (and it stays cheap: the override read is skipped here).
    if not has_approach and not has_spec:
        if not _run_registered(store, run.run_id):
            return GateOutcome(True, "no active mokata run — not policed")
        if GATE_PHASE in read_override(root, run.run_id):
            return GateOutcome(True, f"{GATE_PHASE}: overridden for this session",
                               gate=GATE_PHASE, overridden=True)
        return GateOutcome(
            False,
            f"{GATE_PHASE}: brainstorm in progress — approve an approach first. "
            f"{os.path.basename(path)} is implementation, but no approach is approved for this run "
            f"yet. Explore and approve one approach (/mokata:brainstorm), or override: "
            f"mokata gate override {GATE_PHASE} --reason \"<why>\"",
            gate=GATE_PHASE,
        )

    overrides = read_override(root, run.run_id)

    # Gate 1 — spec-persisted. Fires AHEAD of the TDD gate, matching `engine/spec_gate.py`'s
    # documented order ("fired AHEAD of no-code-without-failing-test").
    if not has_spec:
        if GATE_SPEC in overrides:
            return GateOutcome(True, f"{GATE_SPEC}: overridden for this session",
                               gate=GATE_SPEC, overridden=True)
        return GateOutcome(
            False,
            f"{GATE_SPEC}: an approach is approved for this run but no spec is emitted — "
            f"{os.path.basename(path)} is implementation. Emit the spec first (/mokata:spec), "
            f"or override: mokata gate override {GATE_SPEC} --reason \"<why>\"",
            gate=GATE_SPEC,
        )

    # SI-DEV — the FORCED REGRESSION. An amendment is in flight: this run has left `develop` and is
    # back at SPEC, so development writes are blocked until the new scope is re-gated. Checked ahead
    # of the TDD gate because it outranks it: while the spec is being rewritten there is no approved
    # spec to be writing code against at all.
    amend = amend_from_state(_read(store, amend_key(run.run_id)))
    if amend.is_open:
        if GATE_SCOPE in overrides:
            return GateOutcome(True, f"{GATE_SCOPE}: overridden for this session",
                               gate=GATE_SCOPE, overridden=True)
        return GateOutcome(
            False,
            f"{GATE_SCOPE}: a spec amend is IN PROGRESS (v{amend.from_version} -> "
            f"v{amend.to_version}"
            f"{': ' + amend.item if amend.item else ''}) — this run has regressed to the SPEC "
            f"phase and development writes are blocked until the amendment is approved. Finish it "
            f"(mokata approve <id>, then re-run `mokata spec amend`), abandon it "
            f"(mokata spec amend --abort), or override: mokata gate override {GATE_SCOPE} "
            f"--reason \"<why>\"",
            gate=GATE_SCOPE,
        )

    # Gate 2 — no-code-without-failing-test. The spec is emitted; implementation now requires a
    # failing test ON RECORD for this run. RED is the licence, not the violation.
    red = _red_set(store, run.run_id)
    if red is None:
        return GateOutcome(True, f"{GATE_TDD}: this run's TDD state is unreadable — failing open "
                                 f"rather than blocking on a state mokata cannot read",
                           gate=GATE_TDD)
    if not red:
        if GATE_TDD in overrides:
            return GateOutcome(True, f"{GATE_TDD}: overridden for this session",
                               gate=GATE_TDD, overridden=True)
        return GateOutcome(
            False,
            f"{GATE_TDD}: no failing test is on record for this run — {os.path.basename(path)} is "
            f"implementation. Write the failing test first and watch it fail (/mokata:test), "
            f"or override: mokata gate override {GATE_TDD} --reason \"<why>\"",
            gate=GATE_TDD,
        )

    # SI-DEV — the RED an AMENDMENT owes. The red-set is a HIGH-WATER MARK, so mid-develop it is
    # already non-empty and the gate above waves everything through. A spec that just GREW would
    # therefore license implementation of the new behaviour with no failing test for it at all —
    # the exact hole the amendment was supposed to close. The amend record carries the tests its new
    # criteria owe (mapped by the completeness gate itself); they must be RED before the new work is.
    owed = amend.owed(red)
    if owed:
        if GATE_TDD in overrides:
            return GateOutcome(True, f"{GATE_TDD}: overridden for this session",
                               gate=GATE_TDD, overridden=True)
        return GateOutcome(
            False,
            f"{GATE_TDD}: the spec was amended to v{amend.to_version} and its new acceptance "
            f"criteria still owe a failing test — {', '.join(owed)} "
            f"{'is' if len(owed) == 1 else 'are'} not RED yet. Write "
            f"{'it' if len(owed) == 1 else 'them'} and watch "
            f"{'it' if len(owed) == 1 else 'them'} fail (/mokata:test), or override: "
            f"mokata gate override {GATE_TDD} --reason \"<why>\"",
            gate=GATE_TDD,
        )

    # SI-DEV — the SCOPE. The spec is emitted, a failing test is on record, and nothing is being
    # amended: the only question left is whether this write is inside what the human APPROVED. It is
    # judged against the spec's declared scope and nothing else — and every undeclared case (no
    # scope section, an unreadable one, a spec that predates SI-DEV, an authorized list that draws
    # no map) fails OPEN. See `spec_scope.classify`.
    verdict = classify(scope_from_dict((spec_data or {}).get(SCOPE_KEY)), path, content, root=root)
    if not verdict.allowed:
        if GATE_SCOPE in overrides:
            return GateOutcome(True, f"{GATE_SCOPE}: overridden for this session",
                               gate=GATE_SCOPE, overridden=True)
        deferred = f" (deferred: {verdict.item})" if verdict.item else ""
        return GateOutcome(
            False,
            f"{GATE_SCOPE}: scope change — this write is outside spec v{_version_of(spec_data)}"
            f"{deferred}. {verdict.reason}. A user's instruction is authorization to ASK, not to "
            f"build: run `mokata spec amend` (gated — the new scope is re-approved and re-tested), "
            f"or override: mokata gate override {GATE_SCOPE} --reason \"<why>\"",
            gate=GATE_SCOPE,
        )

    return GateOutcome(True, f"{GATE_TDD}: a failing test is on record for this run")


def _version_of(spec_data: Optional[dict]) -> int:
    try:
        return int((spec_data or {}).get("version", 1) or 1)
    except (TypeError, ValueError):
        return 1


# ======================================================================================
# the once-per-session notice
# ======================================================================================

def notice_once(root: str, session_id: str, message: str) -> Optional[str]:
    """`message`, but only the FIRST time it is asked for in `session_id` — else None.

    The marker is created with `O_EXCL`, so the "first" is decided by the filesystem and two
    concurrent hooks cannot both emit. Transient state under `temp_local/` (ungated — P2 governs
    DURABLE writes). Never raises: if the marker cannot be written we simply say it once more,
    which is a repeated notice, never a block."""
    if not session_id:
        return message
    marker = os.path.join(state_dir(root), NOTICE_PREFIX + session_id)
    try:
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return None                                      # already said, this window
    except OSError:
        return message
    os.close(fd)
    return message


__all__ = [
    "BLOCK_EXIT", "GATES", "GATE_PHASE", "GATE_SCOPE", "GATE_SPEC", "GATE_TDD", "GateOutcome",
    "RunResolution", "APPROACH_PREFIX", "SPEC_PREFIX", "CHECKPOINT_PREFIX", "OVERRIDE_PREFIX",
    "MAX_SCAN_BYTES",
    "bash_command", "check_write", "find_mokata_root", "is_implementation_path", "is_test_path",
    "notice_once", "override_key", "read_override", "resolve_run", "target_content", "target_path",
]
