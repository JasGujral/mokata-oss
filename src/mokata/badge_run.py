"""B-BADGE — session-scoped run resolution for the statusline BADGE only.

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

Never raises: this feeds the statusline, a pure read-only surface that must always exit 0. Reuses
`gate_hook`'s R-MCP read helpers (`_run_ids` / `_live_runs`) so (ii) is byte-identical to the gate's
narrowing — a read, never a write; enforcement is untouched.

Stdlib-only. Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

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
    retired)."""
    try:
        from .progress import _shipped_run_ids
        return run_id in _shipped_run_ids(StateStore(state_dir(root)))
    except Exception:
        return False


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
        run_id: Optional[str] = None
        bound = read_binding(root, claude_session_id)
        if bound is not None:
            try:
                if StateStore(state_dir(root)).exists(CHECKPOINT_PREFIX + bound):
                    run_id = bound
            except Exception:
                pass
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
