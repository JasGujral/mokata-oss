"""B-BADGE — session-scoped run resolution (the statusline BADGE, and REVIEW-FIX.R1's verdict key).

The statusline badge was session-BLIND: it derived its stage strip from `progress.find_active_run`,
which resolves ANY persisted `pipeline_run__*` checkpoint with no session binding. Run state
deliberately survives `/clear` (P17 resume — correct), so a cleared session kept wearing a dead
run's stage strip, and a fresh `/brainstorm`'s newly registered run could lose resolution to an
older one (live report 2026-07-15, 0.0.13).

This module is a NEW resolver that sits BESIDE `find_active_run` — it does not change it, so every
other consumer (progress CLI, resume, lanes) keeps today's behaviour exactly. The badge, and only
the badge, resolves the run for a Claude Code session in this order:

    (i)  a run explicitly BOUND to THIS Claude Code `session_id` (and still on record) -> that run;
    (ii) else exactly ONE live run — the R-MCP narrowing (`gate_hook._live_runs`: pid_alive + repo
         match over the runs with on-disk state) -> that run;
    (iii) else None -> the plain `mokata` no-run badge.

The binding (i) is `session_run_binding__<claude_session_id>` — a transient RUN-STATE file under
`temp_local/state`, keyed by Claude Code's `session_id` (which hooks receive on stdin). It is
UNGATED (P2 is about durable writes; this is transient run-tracking, like the checkpoints and the
registry) and secret-safe by construction (it holds one field: `run_id`).

Grounding divergence (harness gap #25642): the MCP process that REGISTERS a run cannot learn Claude
Code's `session_id` (no env var, no `_meta`, no `initialize` params), so the spec's step-3
"RUN-REG binds the run to the session" is impossible today. The binding is instead written by the
SessionStart hook (`maybe_bind_on_session_start`), source-aware, and the fresh `/brainstorm` flip
rides live-narrowing (ii) until the next SessionStart. See doc 84 B-BADGE-FU — move the binding
write to RUN-REG when the harness exposes session identity to MCP.

RE-ENTRY adds a THIRD consumer, `resolve_run_for_evidence` — the run an APPROVAL belongs to. It
reuses tiers (i)/(iv) verbatim and inserts ONE new tier of its own (the single run holding pipeline
evidence), because a re-entered brainstorm REGISTERS a bare checkpoint that the checkpoint-counting
tiers cannot tell apart from the pipeline it just re-entered; see its docstring.

REVIEW-FIX.R1 adds a SECOND consumer of the same session-aware tiers — `resolve_verdict_run`, the
key the review verdict is recorded under and read back by (`progress_events`). It reuses tier (i)
verbatim and refuses on ambiguity instead of falling back to a global scan; see its docstring for
the two deliberate differences from the badge resolver. `find_active_run` is still untouched.

Never raises: this feeds the statusline, a pure read-only surface that must always exit 0. Reuses
`gate_hook`'s R-MCP read helpers (`_run_ids` / `_live_runs`) so (ii) is byte-identical to the gate's
narrowing — a read, never a write; enforcement is untouched.

Stdlib-only. Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
from typing import Optional

from .govern.resume import CHECKPOINT_PREFIX
from .state import StateStore
from .tdd_state import state_dir

# The per-session binding key: `session_run_binding__<claude_session_id>.json`. Keyed by Claude
# Code's session_id (NOT mokata's run_id), so it is a plain pass-through key — never session-scoped
# by mokata's own id. Holds exactly one field, `run_id` (secret-safe by construction).
BINDING_PREFIX = "session_run_binding__"

# The SessionStart `source` values that may establish a binding — a session that is STARTING or
# RESUMING adopts the single live run. `clear` (and anything else) never binds: the clean badge is
# the whole point of `/clear`.
_BINDING_SOURCES = frozenset({"startup", "resume"})


def _binding_key(claude_session_id: str) -> str:
    return BINDING_PREFIX + claude_session_id


def bind_session_run(root: str, claude_session_id: str, run_id: str) -> None:
    """Bind `claude_session_id` -> `run_id` for the badge (transient run-state; ungated). No-op on a
    missing id/run or any store error — a binding is best-effort surfacing, never worth raising."""
    if not claude_session_id or not run_id:
        return
    try:
        StateStore(state_dir(root)).write(_binding_key(claude_session_id), {"run_id": run_id})
    except Exception:
        # Degrade-clean: the binding is a badge convenience. If it can't be written, resolution
        # falls back to live-narrowing (ii) exactly as if no binding existed. Never raises.
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


def _single_live_run(root: str) -> Optional[str]:
    """The one live run in this repo (R-MCP narrowing), or None when zero or 2+ are live — the same
    pid_alive + repo-match read the gate hook uses. Reused so the badge's (ii) can never diverge
    from the gate's narrowing. A read, never a write; never raises."""
    try:
        from .gate_hook import _live_runs, _run_ids
        candidates = _run_ids(state_dir(root))
        if not candidates:
            return None
        survivors = _live_runs(root, candidates)
        return survivors[0] if len(survivors) == 1 else None
    except Exception:
        return None


def _run_is_shipped(root: str, run_id: str) -> bool:
    """Whether `run_id` reached its terminal END-OF-RUN signal (`stage_enter: ship` in the progress-
    event log — the strongest end-of-run evidence, since `STAGE_PASS`/a ship-completion event are
    never written). B-LIFE keys retirement on SHIP, NOT the pipeline checkpoint: a complete checkpoint
    means the spec emitted and the user is AT develop (active), which must keep its badge. Degrade-
    clean: any read problem reads as NOT shipped, so an unreadable run is shown (never wrongly
    retired).

    REVIEW-FIX.R2 — asks the RUN-FILTERED question (`_shipped_run_ids(store, run_id)`), so the log
    scan runs backward and stops at this run's own latest stage: retirement can no longer be
    truncated out of the read window, and the badge reads only as far as it must."""
    try:
        from .progress import _shipped_run_ids
        return run_id in _shipped_run_ids(StateStore(state_dir(root)), run_id)
    except Exception:
        return False


def _bound_run(root: str, claude_session_id: Optional[str]) -> Optional[str]:
    """Tier (i) — the run explicitly BOUND to this Claude Code session, but only if it is STILL on
    record (a bound run whose checkpoint is gone falls through, so a stale binding never resolves to
    a ghost). Shared verbatim by `resolve_badge_run` and `resolve_verdict_run` so the two can never
    diverge on what "this session's run" means. Never raises."""
    bound = read_binding(root, claude_session_id)
    if bound is None:
        return None
    try:
        return bound if StateStore(state_dir(root)).exists(CHECKPOINT_PREFIX + bound) else None
    except Exception:
        return None


def resolve_verdict_run(root: str, claude_session_id: Optional[str] = None) -> Optional[str]:
    """REVIEW-FIX.R1 — the run a REVIEW VERDICT is keyed to, for BOTH the record and the read.

    The ship gate is only worth anything if the verdict it reads belongs to THIS run (P14): the
    record-key and the read-key must be the same run, and an unresolvable run must satisfy nothing.
    Resolution order:

        (i)   the run BOUND to this Claude Code session (`_bound_run` — the badge's tier (i));
        (ii)  the run the GATE HOOK would enforce (`gate_hook.resolve_run`: an explicit
              `MOKATA_SESSION_ID` pin -> the single run with state in this repo -> the single LIVE
              run when 2+ have state -> AMBIGUOUS, which resolves to None);
        (iii) None — the caller must FAIL CLOSED (block / record run-less), never fall back to a
              global scan of every run's verdicts.

    Two deliberate differences from `resolve_badge_run`, both load-bearing:

    * B-LIFE ship retirement is NOT applied. Retirement is DISPLAY-only (a finished run is not the
      current state to SHOW); the verdict key must survive `mokata progress mark ship`, or ship's
      own entry mark would re-key the read away from the run whose verdict was recorded. Because it
      is skipped here, `review-status` reads the SAME verdict before and after the ship mark — the
      ordering inside ship.md cannot affect the gate.
    * tier (ii) accepts the single run with state (`resolve_run`'s own short-circuit) where the
      badge demands a live PROCESS. A repo with exactly ONE run has no foreign run to borrow
      evidence from, and the CLI process that runs `mokata progress review-status` is a separate,
      unregistered process — demanding a live registry entry there would fail closed on every
      ordinary single-run ship. AMBIGUITY (2+ runs the live-session registry cannot narrow) is what
      produces the leak, and that still resolves to None: mokata refuses rather than guesses, the
      same discipline `cli_commands/spec.py:_run_scoped_store` applies to a spec emit.

    Never raises: any error at any step resolves to None, which is the strict outcome."""
    try:
        bound = _bound_run(root, claude_session_id)
        if bound is not None:
            return bound
        from .gate_hook import resolve_run
        run = resolve_run(root)
        return None if run.ambiguous else run.run_id
    except Exception:
        return None


def _evidence_run(root: str) -> Optional[str]:
    """The single run in this repo that holds PIPELINE EVIDENCE — an `approved_approach__<run>` or
    an `emitted_spec__<run>` — or None when zero or 2+ do.

    Why evidence and not "run state": `gate_hook._run_ids` counts FOUR prefixes, and one of them is
    the bare `pipeline_run__<run>` checkpoint that RUN-REG writes at protocol start. So the moment a
    user goes back to `/brainstorm` in a second window, that window REGISTERS a checkpoint and
    becomes a candidate run indistinguishable from the pipeline it just re-entered — the resolver
    then either goes ambiguous or (via live-narrowing) picks the brand-new EMPTY run, which is the
    opposite of the answer. A bare checkpoint says "a process is here"; the approach and the spec
    say "the pipeline is here", and it is the pipeline an approval belongs to.

    Read-only and never raises: any error resolves to None, which makes the caller fall through to
    the next tier rather than act on a half-read directory."""
    from .gate_hook import APPROACH_PREFIX, SPEC_PREFIX
    found = set()
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
        return None
    found.discard("")
    return next(iter(found)) if len(found) == 1 else None


def resolve_run_for_evidence(root: str, claude_session_id: Optional[str] = None) -> Optional[str]:
    """RE-ENTRY — the run a piece of RUN-SCOPED EVIDENCE belongs to, for BOTH the record and the
    read. The third consumer of these session-aware tiers, after the badge and R1's verdict key.

    The bug it exists for: `approval.proposal_id_for` keys a proposal to `current_run_id()`, which
    IS the process's `session_id` (`session.py`). So the id under which a human's approval is
    recorded was a function of which MCP process happened to be alive — restart the server, or
    re-enter the pipeline from a second window, and the SAME human approving the SAME content in
    the SAME repo produced a DIFFERENT id, the genuine approval came back `other-session`, and the
    pending store filled with one proposal per restart. That is REVIEW-FIX.R1's residual class, in
    the approval chain: record-key != read-key.

    Resolution order — most explicit first:

        (i)   an explicit `MOKATA_SESSION_ID` pin -> that run. HOISTED above the evidence tier
              (R1 leaves the pin inside `resolve_run`, tier ii) because the pin is the documented
              "I am telling you which run" control; an inferred evidence run must never outrank a
              human who named one.
        (ii)  the run BOUND to this Claude Code session (`_bound_run` — the badge's tier (i),
              shared verbatim so the three resolvers cannot diverge on "this session's run").
        (iii) the single run holding pipeline EVIDENCE (`_evidence_run`) — the tier that survives
              re-entry, and the one deliberate addition over `resolve_verdict_run`.
        (iv)  the run the GATE HOOK would enforce (`gate_hook.resolve_run`), unchanged.
        (v)   None — the caller must NOT guess.

    Never raises: any error at any step resolves to None, which is the strict outcome."""
    try:
        pinned = os.environ.get("MOKATA_SESSION_ID", "").strip()
        if pinned:
            return pinned
        bound = _bound_run(root, claude_session_id)
        if bound is not None:
            return bound
        evidence = _evidence_run(root)
        if evidence is not None:
            return evidence
        from .gate_hook import resolve_run
        run = resolve_run(root)
        return None if run.ambiguous else run.run_id
    except Exception:
        return None


def resolve_badge_run(root: str, claude_session_id: Optional[str]) -> Optional[str]:
    """The run the BADGE should show for a Claude Code session — (i) bound run, (ii) single live
    run, (iii) None. Read-only and degrade-clean: any error at any step resolves to None (the clean
    badge), never a raise.

    B-LIFE — a resolved run that has SHIPPED (reached its terminal `ship` stage) is retired from the
    badge (resolves as None ⇒ the clean `local · mokata` strip): a finished run is not the current
    state. A spec-emitted run that is only AT develop/review is NOT retired — it stays badged, since
    develop/review/ship is the healthy active arc. DISPLAY-only: the binding record and the
    checkpoint stay on disk untouched (the run is still resumable / viewable by explicit id)."""
    try:
        # (i) a run explicitly bound to THIS session — but only if it is still on record (a bound
        # run whose checkpoint is gone falls through, so a stale binding never shows a ghost).
        run_id: Optional[str] = _bound_run(root, claude_session_id)
        # (ii) exactly one live run in this repo.
        if run_id is None:
            run_id = _single_live_run(root)
        # (iii) B-LIFE — a SHIPPED run is retired -> clean badge (display only).
        if run_id is not None and _run_is_shipped(root, run_id):
            return None
        return run_id
    except Exception:
        return None


def maybe_bind_on_session_start(root: str, claude_session_id: Optional[str],
                                source: Optional[str]) -> Optional[str]:
    """SessionStart writer for the badge binding (the only in-scope process that sees Claude Code's
    `session_id` at a session boundary — the MCP registrar cannot, harness gap #25642).

    Binds `claude_session_id` -> the run ONLY when BOTH hold: `source` is `startup`/`resume` (never
    `clear` — the clean badge is the point of `/clear`), AND exactly ONE live run exists (the R-MCP
    narrowing). Ambiguous (2+ live) or zero live ⇒ NO binding, so the badge falls open to (ii)/(iii)
    — the narrowing can only ever REMOVE ambiguity, never manufacture a wrong pick. Returns the
    bound run_id (or None). Never raises: SessionStart is async/observability and must never block."""
    try:
        if not claude_session_id or source not in _BINDING_SOURCES:
            return None
        run_id = _single_live_run(root)
        if run_id is None:
            return None
        bind_session_run(root, claude_session_id, run_id)
        return run_id
    except Exception:
        return None
