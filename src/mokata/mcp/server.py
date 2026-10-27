"""MCP wiring — lazily imports the SDK; never imported by the core/CLI.

The MCP SDK is an unconditional dependency of mokata (Python 3.12+ floor), so a plain
`pip install mokata` always provides it. It is still imported LAZILY inside `build_server`, so
the core package and CLI import and run even in a stripped/broken env where the SDK is absent —
`mokata-mcp` then fails LOUD with a fix rather than crashing at import. Note: `mokata.mcp` (this
namespaced package) does NOT shadow the SDK's top-level `mcp` — the absolute
`from mcp.server.fastmcp import FastMCP` below resolves to the installed SDK, and
`mcp_available()`'s import looks up the same top-level SDK, never this package.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import functools
import re
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from ..axi_out import with_remedy
from . import status as _status
from .registry import SERVER_NAME, TOOLS
from .validation import ValidationError, refusal, validate_surface_params
# tools_read / tools_write / tools_approve populate the shared TOOLS registry as an import side
# effect (tools_approve = AP-MCP's default-off in-chat `approve` tool).
from . import tools_read, tools_write, tools_approve  # noqa: F401


# ======================================================================================
# R-MCP — the run self-registration seam
# ======================================================================================
#
# SI.1's gate hook narrows an AMBIGUOUS multi-run repo by consulting the MS.S2 live-session
# registry (`run_resolver.live_runs`) — but that is only SOUND if every MCP process is actually IN
# the registry. Before this stage the MCP process self-registered ONLY when the user called the
# `session_windows` tool, so the hook could not rely on it and had to fail open. This seam closes
# that: the server registers EAGERLY at process start (`main`, RUN-ID-DRIFT) and refreshes on every
# tool call it serves, making registry liveness a STRUCTURAL fact, not a user-dependent one.
#
# It said that before RUN-ID-DRIFT too, while registering on the first tool call — which made the
# claim false for exactly the window that had not yet used mokata. See `main` for the measured
# consequence.

def _call_path(args: tuple, kwargs: dict) -> str:
    """The repo path a served tool call targets. Every mokata MCP tool takes `path` (default ".")
    as its first parameter, so the value is either the `path` keyword or the first positional."""
    p = kwargs.get("path")
    if isinstance(p, str) and p:
        return p
    if args and isinstance(args[0], str) and args[0]:
        return args[0]
    return "."


def _register_this_window(path: str) -> None:
    """Self-register / refresh THIS MCP process in the MS.S2 live-session registry (R-MCP).

    `session_registry.touch` is an idempotent UPSERT-SELF, so the FIRST tool call registers this
    run (session_id/run_id + pid + repo_root + last_seen — the existing MS.S2 `_ENTRY_FIELDS`, no
    new field) and every subsequent call refreshes it. Lazy by construction: this only ever runs
    from `_with_registration`, i.e. when a tool is actually SERVED — never at import or startup.

    Degrade-clean and D5-classed: a registry failure is `note_degraded` ONCE, loudly (the hook then
    stays fail-open on ambiguity, exactly as before this stage), then swallowed — self-registering
    this window must NEVER break the tool call the user asked for. The class is broad on purpose:
    `touch` spans identity minting, PID/OS probing, and cross-process-locked transient-file IO, and
    none of that is worth failing a user's tool call over."""
    try:
        from ..config import Surface
        from .. import session_registry as SR
        SR.touch(Surface.load(path))
    except Exception as exc:  # noqa: BLE001 - the contract IS broad (see docstring); it SPEAKS.
        from ..degrade import FAILURE_LOCAL_IO, note_degraded
        note_degraded(
            "session-registry", FAILURE_LOCAL_IO,
            fallback="this window is not recorded in the live-session registry; the run-state gate "
                     "stays fail-open when this repo has ambiguous run state",
            fix="check permissions/disk under `.mokata/temp_local/`, then run `mokata doctor`",
            detail=str(exc))


# ======================================================================================
# MCP-R.D0 — `_serve`: the ONE systemic dispatch wrapper (doc 88 §D0)
# ======================================================================================
#
# `_with_registration` only fired a self-registration side effect then `return fn(...)`. It is
# expanded here into `_serve`, so EVERY served tool call is guaranteed a bounded, machine-legible
# outcome instead of a client-side timeout into silence (P16 legibility, P14 no-unbounded-hang):
#
#   R5  the self-registration pre-hook runs CONCURRENTLY (a daemon thread we never join on the
#       served path), so its up-to-10s cross-process lock wait (root cause 4) can NEVER delay or
#       stall the tool the user asked for. Its failure stays best-effort + D5-classed, as before.
#   R1  the tool BODY runs in a worker thread under a wall-clock MCP-SURFACE reply budget; on
#       expiry we answer `status:"running"` (O14 — it was `timed_out`, whose hint claimed nothing
#       was half-committed while the orphaned worker went on to commit) and the work continues;
#       a re-call with the same arguments joins it and returns its result.
#   R2  one try/except turns ANY uncaught exception into mokata's OWN `status:"error"` shape
#       (isError + reason + hint), reclaiming the voice from FastMCP's generic handler.
#   R3  a None / non-dict return is coerced to the same structured `error`.
#   R6  every status the wrapper emits comes from the single-source vocab (`mcp.status`).
#
# R4 (progress/heartbeat) is a documented degrade-clean NO-OP: `Context.report_progress` is a
# coroutine that must be injected as a tool parameter, which the schema-transparency constraint
# forbids. Liveness is delivered instead by O14's `running` answer (below): a call still working at
# the reply budget SAYS so, and a re-call with the same arguments joins the same work.
R4_PROGRESS_NOOP = True

# The interactive MCP-surface REPLY budget (R1, O14). After this long a call that is still working
# answers `status:"running"` — the work is NOT cut: it keeps running, and a re-call with the same
# arguments joins it and receives its result (Jas ruling: no deadline cuts real work; long work is
# disclosed). Distinct from any tool's internal subprocess/DB bound.
MCP_SURFACE_TIMEOUT_SECONDS = 60.0

# O14 — how long a finished call's result is kept for the re-call that a `running` answer promised.
# Only results whose caller was told `running` are kept; nothing else is cached.
_RESULT_KEEP_SECONDS = 1800.0

# R5 — fire-and-forget self-registration threads, tracked so tests/diagnostics can drain them. The
# SERVED path never joins these; draining is a test/diagnostic affordance only.
_REG_LOCK = threading.Lock()
_REG_THREADS: "List[threading.Thread]" = []


def _spawn_registration(path: str) -> "threading.Thread":
    """R5 — self-register THIS window off the served path, in a daemon thread we do not join. A
    stalled registry (a contended cross-process lock) therefore cannot delay the tool call."""
    # O14 — COALESCED: a burst of parallel calls (Claude Code fans tool calls out) used to start one
    # registry write per call, every one contending for the same cross-process registry lock with
    # the hooks. One in flight per path is a refresh; a second adds only contention.
    with _REG_LOCK:
        _REG_THREADS[:] = [x for x in _REG_THREADS if x.is_alive()]   # prune finished
        for x in _REG_THREADS:
            if getattr(x, "_mokata_path", None) == path:
                return x
        t = threading.Thread(target=_register_this_window, args=(path,),
                             name="mokata-mcp-register", daemon=True)
        t._mokata_path = path                                   # type: ignore[attr-defined]
        _REG_THREADS.append(t)
    t.start()
    return t


def _await_registrations(timeout: float = 5.0) -> None:
    """Test/diagnostic seam: block until the spawned self-registrations finish (bounded). NOT on the
    served path — the server never calls this. Registration is fire-and-forget by design (R5); a
    test that asserts the registry side effect calls this to remove the inherent race."""
    with _REG_LOCK:
        threads = list(_REG_THREADS)
    for t in threads:
        t.join(timeout)


def _mcp_timeout_for(op: str, path: str = ".") -> float:
    """The wall-clock MCP-surface budget for `op` (R1). `baseline` — a READ tool that runs the whole
    test suite — gets its own named cap far below its 600s terminal bound (R7); everything else gets
    the interactive default. Read at CALL time so the budget is patchable/tunable."""
    if op == "baseline":
        # 0.0.24 (OSS #82) — the repo's `settings.mcp.test_timeout_seconds` raises it (clamped to
        # the 600s terminal bound); the subprocess in `tools_read.baseline` reads the same value.
        from ..baseline import mcp_test_timeout
        return mcp_test_timeout(_manifest_or_none(path))
    return MCP_SURFACE_TIMEOUT_SECONDS


def _manifest_or_none(path: str):
    """The repo's manifest for a budget read, or None — never raises (an unreadable repo gets the
    default budget, and the tool body reports the real problem)."""
    try:
        from ..config import Surface
        if Surface.is_initialized(path):
            return Surface.load(path).manifest
    except Exception:  # noqa: BLE001
        return None
    return None


def _still_running(op: str, budget: float, elapsed: float, call_id: str) -> Dict[str, Any]:
    """O14 — the reply-budget verdict. The work was NOT cut: it is still running in this server and
    a re-call with the SAME arguments joins it (it never runs twice) and returns its result. Names
    the OPERATION and a short call id; NEVER the args (a slow call must not echo a DSN/path/arg)."""
    return {
        "status": _status.RUNNING, "isError": False, "operation": op, "call_id": call_id,
        "elapsed_s": round(elapsed, 1),
        "reason": (f"the '{op}' operation is still working after {elapsed:.0f}s (mokata answers "
                   f"within {int(budget)}s and keeps the work going)"),
        "hint": (f"re-call '{op}' with exactly the same arguments to receive the result — it joins "
                 f"the work already in flight and is never started twice. Do not switch to the "
                 f"CLI for the same write: that WOULD run it twice."
                 + (" A suite that needs longer per reply: `mokata config set "
                    "settings.mcp.test_timeout_seconds <seconds>` (up to 600)."
                    if op == "baseline" else "")),
    }


def _as_error(op: str, exc: BaseException) -> Dict[str, Any]:
    """R2 — reclaim the voice from FastMCP's generic handler. Only the exception TYPE name surfaces:
    `str(exc)` can carry a DSN/path/arg and must not leak into the render.

    O14 — a bounded lock wait that expired (`oslock.LockTimeout`) is its own, RETRYABLE answer:
    another mokata process (a hook, the CLI, another window) held the same state for longer than
    the bounded wait. Nothing is waited on indefinitely, and the agent can tell "busy, retry" from
    "the server broke" by `retryable` alone."""
    from ..oslock import LockTimeout
    if isinstance(exc, LockTimeout):
        return {
            "status": _status.ERROR, "isError": True, "operation": op, "committed": False,
            "retryable": True, "reason_code": "state-busy",
            "reason": (f"the '{op}' operation could not take mokata's state lock — another mokata "
                       f"process held it past the bounded wait"),
            "hint": ("retry the same call in a few seconds. A write that was refused here did not "
                     "land; a re-call of a write whose approval was already used reports whether "
                     "it landed."),
        }
    return {
        "status": _status.ERROR, "isError": True, "operation": op, "committed": False,
        "reason": f"the '{op}' operation raised {type(exc).__name__}",
        "hint": ("this is a mokata-side error, not a refusal or a missing approval; retry, and if "
                 "it persists run `mokata doctor`."),
    }


def _coerce(op: str, result: Any) -> Dict[str, Any]:
    """R3 — a tool must return a structured dict; a None / non-dict is coerced to `error` rather than
    handed to the client as ambiguous nothing. A dict passes through BYTE-IDENTICAL (same object)."""
    if isinstance(result, dict):
        return result
    return {
        "status": _status.ERROR, "isError": True, "operation": op, "committed": False,
        "reason": f"the '{op}' operation returned no structured result",
        "hint": "this is a mokata-side defect; retry, and if it persists run `mokata doctor`.",
    }


def _emit_tool_call(path: str, op: str, kind: Optional[str], result: Any,
                    duration_ms: int) -> None:
    """R1.S1b — one `ToolCall` event per served MCP dispatch, with its DURATION.

    ⭐ **THIS IS THE OTHER HALF OF R1.S1b, AND IT CANNOT COME FROM THE LEDGER.** The ledger
    projection covers every gate decision and approval outcome because those are recorded there;
    an MCP tool call is not, and doc 42 names tool dispatch explicitly. So the event is emitted
    where the fact exists — here, in the ONE dispatch wrapper every tool rides, which is the
    same §7i reasoning that put the ledger projection in `AuditLedger.record` instead of at 90
    call sites.

    ⚠ **AND THIS IS WHERE `duration_ms` IS REAL RATHER THAN DERIVED.** `_serve` already runs
    the body on a worker and joins it under a budget, so the wall clock around that join is the
    tool's actual elapsed time. Doc 99's events exit criterion asks for duration on governance
    events; measured at the head of this stage, **zero of the ledger's 49 kinds carried one**,
    and the ledger cannot retrofit it — an append knows when it happened, not how long the thing
    it describes took. A dispatch wrapper knows both.

    `ok` is read from the structured status the wrapper returns, not from "did it raise": a
    `refused`, an `error` and a `running` are all non-exceptional returns here, and treating
    them as successes would make the event say the opposite of what the caller was told."""
    try:
        status = result.get("status") if isinstance(result, dict) else None
        ok = status not in ("error", "running", "refused")
        from ..events import ToolCall, emit as emit_event
        emit_event(path or ".", ToolCall(tool=op, surface=kind or "mcp", ok=ok),
                   duration_ms=duration_ms)
    except Exception:  # noqa: BLE001 — the observability lane never fails a tool call
        return


# ======================================================================================
# P41 MCP-FOLLOWS-WORKTREE — a code tool answers for the session's CURRENT tree
# ======================================================================================
# Claude Code starts this server once, in the main checkout, and `EnterWorktree` never moves it —
# so every code answer used to describe the main checkout's files while the session edited its
# worktree. A tool whose answer is about CODE (it builds the knowledge layer or reads the tree's
# files) now resolves, per call, which tree it serves (`tree_scope.scope_for`: the session this
# process serves, by process ancestry, and that session's P31 worktree binding) and runs its body
# under that scope. The answer carries the scope (`tree`) whenever it is a worktree's — or the main
# checkout's for a stated reason (the session could not be identified, or its worktree is gone).
# Every other tool is untouched: shared state stays the main checkout's `.mokata/` (P31).
TREE_TOOLS = frozenset({
    "query", "decompose", "ci_check", "lat_check", "recall", "remember",
    "spec_emit", "spec_amend", "spec_check",
})


def _tree_scope_for(op: str, path: str) -> Any:
    if op not in TREE_TOOLS:
        return None
    try:
        from .. import tree_scope
        return tree_scope.scope_for(path)
    except Exception:  # noqa: BLE001 — no scope is the main checkout, exactly as before
        return None


def _with_tree(out: Dict[str, Any], scope: Any) -> Dict[str, Any]:
    if scope is None or not getattr(scope, "reportable", False) or not isinstance(out, dict):
        return out
    out.setdefault("tree", scope.to_dict())
    if scope.note and isinstance(out.get("note"), str):
        out["note"] = f"{out['note']} | {scope.note}" if out["note"] else scope.note
    elif scope.note and "note" not in out:
        out["note"] = scope.note
    return out


# ======================================================================================
# O14 MCP-STABILITY — one piece of work per call, joined by its retries
# ======================================================================================
# Measured 2026-10-22 (mcp 1.30, stdio, the harness in tests/test_o14_mcp_stability.py): FastMCP runs
# a SYNC tool function directly ON THE EVENT LOOP, and `_serve` is sync — so a tool body blocked the
# whole server for its duration. Eleven parallel read calls on the K bed all answered at 18.0s (the
# slowest, `lat_check`, set everyone's latency) and an MCP `ping` sent meanwhile took 18.0s; a
# `spec_emit` redeem issued next to a `query` on django waited out the query first. That is the
# "MCP gets stuck while the CLI is fast": the CLI runs one command, the server ran a queue. The
# served handler is now async and runs `_serve` on a worker thread (`_offloop`), so the loop stays
# free and parallel calls run in parallel.
#
# `_serve` itself keeps one piece of WORK per distinct call: a call (op + its exact arguments) that
# is already in flight is JOINED, never started a second time. That is what makes the `running`
# answer honest and a client retry safe — a retried `spec_amend` redeem lands once — and it is why
# the reply budget is not a deadline: nothing is cut, the work finishes and the re-call collects it.
# Durable writes (`kind` write/approve) on one repo also run one at a time in this process, so two
# parallel commits can never interleave their multi-key writes; the wait happens inside the work,
# so a queued write still answers `running` at the budget rather than hanging the caller.

class _Call:
    __slots__ = ("key", "call_id", "started", "done", "out", "told_running", "finished_at",
                 "elapsed_ms")

    def __init__(self, key: str) -> None:
        import hashlib
        self.key = key
        self.call_id = hashlib.sha256(key.encode("utf-8", "replace")).hexdigest()[:12]
        self.started = time.perf_counter()
        self.done = threading.Event()
        self.out: Dict[str, Any] = {}
        self.told_running = False
        self.finished_at = 0.0
        self.elapsed_ms = 0


_INFLIGHT_LOCK = threading.Lock()
_INFLIGHT: Dict[str, _Call] = {}
_WRITE_LOCKS: Dict[str, threading.Lock] = {}


def _call_key(op: str, args: tuple, kwargs: dict) -> str:
    import json
    try:
        blob = json.dumps([list(args), kwargs], sort_keys=True, default=repr)
    except (TypeError, ValueError):
        blob = repr((args, sorted(kwargs.items())))
    return f"{op}\x00{blob}"


def _write_lock(path: str) -> threading.Lock:
    import os
    key = os.path.realpath(path or ".")
    with _INFLIGHT_LOCK:
        lock = _WRITE_LOCKS.get(key)
        if lock is None:
            lock = _WRITE_LOCKS[key] = threading.Lock()
        return lock


def _prune_kept(now: float) -> None:
    """Drop finished calls nobody came back for (caller holds `_INFLIGHT_LOCK`)."""
    for k in [k for k, c in _INFLIGHT.items()
              if c.done.is_set() and now - c.finished_at > _RESULT_KEEP_SECONDS]:
        del _INFLIGHT[k]


def _join_or_start(op: str, kind: Optional[str], fn: Callable[..., Any], args: tuple,
                   kwargs: dict, call_path: str) -> "tuple":
    """The in-flight call for exactly this (op, args), joined — or a new one, started. Returns
    `(call, joined)`."""
    key = _call_key(op, args, kwargs)
    now = time.perf_counter()
    with _INFLIGHT_LOCK:
        _prune_kept(now)
        live = _INFLIGHT.get(key)
        if live is not None and (not live.done.is_set() or live.told_running):
            return live, True
        call = _INFLIGHT[key] = _Call(key)
    scope = _tree_scope_for(op, call_path)                # P41 — which tree a code answer is for

    def _run() -> None:
        call.out = _coerce(op, None)       # what the caller gets if even the shaping below dies
        try:
            call.out = _work(op, kind, fn, args, kwargs, call_path, scope)
        except Exception as exc:                          # noqa: BLE001 - never leave a caller waiting
            call.out = _as_error(op, exc)
        finally:
            # ⚠ `perf_counter`, NOT `monotonic` (0.0.21 cut): on Windows `monotonic` ticks ~15.6 ms.
            call.elapsed_ms = int((time.perf_counter() - call.started) * 1000)
            call.finished_at = time.perf_counter()
            call.done.set()

    threading.Thread(target=_run, name=f"mokata-mcp-{op}", daemon=True).start()
    return call, False


def _work(op: str, kind: Optional[str], fn: Callable[..., Any], args: tuple, kwargs: dict,
          call_path: str, scope: Any) -> Dict[str, Any]:
    """Run one tool body and shape its outcome (R2/R3/D1d/P41/AXI-OUT) — on the call's worker."""
    from ..tree_scope import serving
    gate = _write_lock(call_path) if kind in ("write", "approve") else None
    try:
        if gate is not None:
            gate.acquire()
        try:
            with serving(scope):
                result = fn(*args, **kwargs)
        finally:
            if gate is not None:
                gate.release()
    except ValidationError as bad:
        # D1d — a tool-local validator raises from inside the body. It is still a caller fault, so
        # it converts through the SAME single site as the pre-step's, ahead of R2.
        return with_remedy(refusal(bad, op))
    except Exception as exc:                              # noqa: BLE001 - R2: reclaim ANY failure
        return with_remedy(_as_error(op, exc))
    # AXI-OUT (10) — a non-success result carries the exact fixing command as `remedyCommand`.
    return with_remedy(_with_tree(_coerce(op, result), scope))   # R3 + P41


def _finish(call: _Call) -> None:
    """The caller has the result: forget the call (a later identical call is new work)."""
    with _INFLIGHT_LOCK:
        if _INFLIGHT.get(call.key) is call:
            del _INFLIGHT[call.key]


def _offloop(served: Callable[..., Any]) -> Callable[..., Any]:
    """O14 — the handler FastMCP registers: async, and it runs `_serve` on a worker thread, so the
    event loop (pings, cancellations, every other parallel call) never waits on a tool body.
    Signature-transparent: `functools.wraps` carries `__wrapped__` down to the tool function, so the
    tool schema FastMCP builds is the unwrapped function's, byte for byte."""
    @functools.wraps(served)
    async def handler(*args: Any, **kwargs: Any) -> Any:
        import anyio
        return await anyio.to_thread.run_sync(functools.partial(served, *args, **kwargs),
                                              abandon_on_cancel=True)
    return handler


def _serve(fn: Callable[..., Any], name: Optional[str] = None,
           kind: Optional[str] = None) -> Callable[..., Any]:
    """The ONE systemic dispatch wrapper (MCP-R.D0). Every served tool call self-registers this
    window (R5, off the served path), answers within the MCP-surface reply budget (R1), and returns
    a structured, single-vocab `status` — `running` while the work continues past the budget (O14:
    a re-call with the same arguments joins it), `error` on any uncaught exception (R2) or
    None/non-dict return (R3) — reclaiming the outcome from silence.

    R4 progress/heartbeat stays a documented no-op (see the module note); `running` carries
    liveness in its place.

    Signature-transparent: `functools.wraps` sets `__wrapped__`, which `inspect.signature` follows,
    so the FastMCP tool schema built from the wrapper is byte-identical to the unwrapped fn (D1a's
    annotations stage depends on this) — no tool's inputs/outputs change. `name`/`kind` label the
    operation for the timeout/error render + the per-tool budget; `name` defaults to the fn name."""
    op = name or getattr(fn, "__name__", "operation")

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        # MCP-R.D1d — the shared input pre-step, FIRST. It runs ahead of the R5 registration (which
        # itself calls `Surface.load(path)`) and ahead of the body thread, so a traversing `path`
        # causes no filesystem read whatsoever. A caller-side argument fault is `refused`, NOT the
        # `error` a mokata-side fault gets (R6) — the agent must be able to tell "fix your call"
        # from "the server broke" by branching on `status` alone.
        try:
            validate_surface_params(args, kwargs)
        except ValidationError as bad:
            return refusal(bad, op)

        call_path = _call_path(args, kwargs)
        _spawn_registration(call_path)                    # R5 — concurrent, never gates the body
        budget = _mcp_timeout_for(op, call_path)          # R1/R7 — per-tool reply budget
        call, joined = _join_or_start(op, kind, fn, args, kwargs, call_path)
        call.done.wait(budget)

        if not call.done.is_set():                        # O14 — still working: SAY so, keep going
            call.told_running = True
            elapsed = time.perf_counter() - call.started
            out = with_remedy(_still_running(op, budget, elapsed, call.call_id))
            _emit_tool_call(call_path, op, kind, out, int(elapsed * 1000))
            return out
        _finish(call)
        # R1.S1b — the event's real duration, measured by the call itself (a joined re-call reports
        # the work's duration, not the re-call's wait). ONE event per piece of work: a re-call that
        # joined does not event it a second time.
        out = call.out
        if not joined:
            _emit_tool_call(call_path, op, kind, out, call.elapsed_ms)
        return out

    return wrapper


# Back-compat alias — the historical seam name (R-MCP wired `build_server` and its tests through
# `_with_registration`). It now IS `_serve`, so every call inherits the D0 robustness unchanged.
_with_registration = _serve


# ======================================================================================
# MCP-SDK-2-BREAKS-THE-SERVER — the SDK's disposition, in FOUR representations (doc 85 §7g)
# ======================================================================================
# `mcp_available()` used to return a bare bool built from `except ImportError: return False`, so
# three different facts collapsed into one `False`: the SDK is not installed, the SDK is installed
# but at a major mokata cannot use, and the SDK is installed at a usable major but its own
# dependency is missing. No caller could tell them apart, so every caller picked the safe-looking
# meaning — "not installed" — and every message, skip and remedy downstream was written for it.
#
# That is not hypothetical. `mcp` 2.0.0 (2026-07-28) removed `mcp.server.fastmcp` outright; with no
# upper bound in `pyproject.toml` a fresh resolve took it, `mcp_available()` said False, and both
# the error message ("not installed") and its remedy ("`pip install -U mokata`", which re-resolves
# to the same 2.0.0) were wrong. v0.0.17 shipped eleven days into that break and CI stayed green,
# because nine `skipUnless(mcp_available())` decorators read the same False as "nothing to grade".
#
# The fix is §7g's: SPLIT THE REPRESENTATION, and make each answer carry its own provenance —
# WHICH version was found and WHAT the import actually said.

#: The `>=1.2` half of pyproject's `mcp>=1.2,<2`.
SDK_MIN_VERSION = (1, 2)
#: The `<2` half. 2.0.0 removed `mcp.server.fastmcp` entirely, so supporting 2.x is a PORT, not a
#: version bump. `test_mcp_sdk_2_breaks_the_server` asserts both constants against pyproject, so
#: the guard is pinned to the bound pip actually enforces rather than to a string typed twice.
SDK_MAJOR_CEILING = 2

SDK_SUPPORTED = "supported"          # importable, and within the declared bound
SDK_ABSENT = "absent"                # no `mcp` distribution at all (a stripped env)
SDK_INCOMPATIBLE = "incompatible"    # installed at a major/version mokata cannot use — THE DEFECT
SDK_BROKEN = "broken"                # a supported version, but its own import fails

#: How mokata names the requirement to a human. Derived from the two constants above so the
#: message cannot drift from the check that produced it.
SDK_REQUIREMENT = f"mcp>={'.'.join(str(p) for p in SDK_MIN_VERSION)},<{SDK_MAJOR_CEILING}"


class SdkDisposition:
    """What the MCP SDK is doing in THIS interpreter, plus the evidence for that answer.

    `state` is one of the four `SDK_*` constants; `version` is the version found (None when there
    is no distribution metadata); `detail` is the underlying import error text, kept rather than
    swallowed so a `BROKEN` answer can name the dependency that is actually missing.
    """

    __slots__ = ("state", "version", "detail")

    def __init__(self, state: str, version: Optional[str] = None, detail: str = "") -> None:
        self.state = state
        self.version = version
        self.detail = detail

    def __repr__(self) -> str:                                   # pragma: no cover - debug aid
        return f"SdkDisposition({self.state!r}, version={self.version!r})"

    def __eq__(self, other: Any) -> bool:
        return (isinstance(other, SdkDisposition) and self.state == other.state
                and self.version == other.version and self.detail == other.detail)

    @property
    def usable(self) -> bool:
        return self.state == SDK_SUPPORTED

    def diagnosis(self) -> str:
        """The cause and a remedy THAT WORKS — one message per state, never shared.

        doc 85 §7g's corollary: a refusal pointing at a remedy nobody can act on is the defect in
        a new costume. The shipped message told a user whose SDK was installed that it was not,
        and told them to reinstall mokata — which re-resolves to the same incompatible SDK and
        reproduces the break. Each branch below is checked by
        `test_mcp_sdk_2_breaks_the_server.TestTheRemedyMustActuallyWork`.
        """
        if self.state == SDK_SUPPORTED:
            return f"the MCP SDK ({self.version or 'unknown version'}) is usable."
        if self.state == SDK_INCOMPATIBLE:
            message = (
                f"the installed MCP SDK is `mcp {self.version}`, which mokata cannot use "
                f"(mokata needs `{SDK_REQUIREMENT}`).\n"
                f"  Cause: this SDK does not provide `mcp.server.fastmcp`, the module the mokata "
                f"MCP server is built on — `mcp` 2.0.0 removed it. Supporting 2.x is a port, not "
                f"a version bump.\n"
                f"  Fix:   pin the SDK below 2 — `pip install 'mcp<2'` — then start "
                f"`mokata-mcp` again.\n"
                f"  ⚠ `pip install -U mokata` does NOT help: it re-resolves the same "
                f"incompatible SDK and reproduces this exact failure.")
            return f"{message}\n  Detail: {self.detail}" if self.detail else message
        if self.state == SDK_BROKEN:
            return (
                f"the MCP SDK is installed (`mcp {self.version or 'unknown version'}`) but cannot "
                f"be imported — one of ITS OWN dependencies is missing.\n"
                f"  Fix:   repair the SDK's dependencies — "
                f"`pip install --force-reinstall '{SDK_REQUIREMENT}'`.\n"
                f"  Detail: {self.detail}")
        return (
            "the MCP SDK (`mcp`) is not installed, so the mokata MCP server cannot start.\n"
            "  Cause: your mokata install is missing the `mcp` package (a required dependency).\n"
            "  Fix:   reinstall mokata — `pip install -U mokata` — to restore the SDK. The "
            "mokata CLI works fully without the MCP server.")


def _parse_version(version: Optional[str]) -> Optional[tuple]:
    """The leading numeric release of a PEP 440 version, as a tuple. None when unparseable.

    Deliberately tiny: mokata needs only "which major/minor is this", and pulling `packaging` in
    for it would make a version check a new runtime dependency of the check that exists to keep
    runtime dependencies honest.
    """
    if not version:
        return None
    head = re.match(r"\d+(?:\.\d+)*", version.strip())
    if head is None:
        return None
    return tuple(int(part) for part in head.group(0).split("."))


def _within_declared_bound(release: tuple) -> bool:
    """`SDK_MIN_VERSION <= release` and the major is below `SDK_MAJOR_CEILING` — i.e. exactly
    what `SDK_REQUIREMENT` tells pip, evaluated in-process."""
    return release >= SDK_MIN_VERSION and release[0] < SDK_MAJOR_CEILING


def classify_sdk(version: Optional[str], import_error: Optional[BaseException]) -> SdkDisposition:
    """Turn (version found, import outcome) into one of the four dispositions.

    Pure, so the four states can be graded without four machines — `classify_sdk` is the whole
    decision and `sdk_state()` below is only the two measurements that feed it.
    """
    release = _parse_version(version)
    if release is not None and not _within_declared_bound(release):
        # Checked BEFORE the import outcome on purpose: under `mcp` 2.0.0 the import fails with
        # ModuleNotFoundError, which is precisely the evidence the old code read as "absent".
        return SdkDisposition(SDK_INCOMPATIBLE, version,
                              str(import_error) if import_error else "")
    if import_error is None:
        return SdkDisposition(SDK_SUPPORTED, version)

    # WHICH module was missing decides the rest, and the version cannot answer it.
    #
    # An SDK that does not carry `mcp.server.fastmcp` is unusable no matter what version it
    # claims — that is what *incompatible* means — whereas a missing `jsonschema` is a broken
    # machine mokata can tell the user to repair. Discriminating on the missing module's NAME
    # rather than on a version range is what makes this catch a break NOBODY PREDICTED: it does
    # not depend on the ceiling having been set correctly in advance.
    #
    # ⚠ This branch exists because the fix was probed against the world of 2026-07-28 — bound
    # widened, `mcp` 2.0.0 installed — and `classify_sdk` answered BROKEN, which is skippable.
    # The guard reported `OK (skipped=2)`: the very false green this function exists to end,
    # rebuilt inside the fix for it. `No module named 'mcp'` (the genuinely absent case) names
    # the namespace WITHOUT a dot, so the `mcp.` prefix separates the two cleanly.
    missing = getattr(import_error, "name", None) or ""
    if missing.startswith("mcp."):
        return SdkDisposition(SDK_INCOMPATIBLE, version, str(import_error))
    if version is None:
        # No distribution metadata AND the import failed — there is nothing installed.
        return SdkDisposition(SDK_ABSENT, None, str(import_error))
    # A version inside the bound that will not import for a reason outside the `mcp` namespace:
    # the SDK's own dependency is missing (`mcp` hard-requires `jsonschema>=4.20.0`, which CI's
    # `jsonschema: absent` leg removes).
    return SdkDisposition(SDK_BROKEN, version, str(import_error))


def sdk_state() -> SdkDisposition:
    """Measure the installed MCP SDK: its declared version, and whether it actually imports.

    The version comes from distribution METADATA, which is readable even when the package cannot
    be imported — that is what makes `incompatible` distinguishable from `absent` at all. The SDK
    itself stays LAZILY imported (the import is inside this function, keyed by string — there is
    still no top-level `import mcp`), so the core and CLI keep importing in a stripped env.
    """
    import importlib
    import importlib.metadata as metadata

    try:
        version: Optional[str] = metadata.version("mcp")
    except metadata.PackageNotFoundError:
        # NARROW on purpose (D5): "there is no `mcp` distribution metadata" is the only condition
        # this fallback is honest about, and a vendored SDK on sys.path is exactly that — no
        # metadata, but it imports fine, so the import outcome below decides. Any OTHER metadata
        # failure must propagate rather than be silently read as "no version known": that path
        # would classify an INCOMPATIBLE SDK as ABSENT and hand the user the wrong remedy, which
        # is the defect this function exists to end.
        version = None

    import_error: Optional[BaseException] = None
    try:
        importlib.import_module("mcp.server.fastmcp")
    except ImportError as exc:
        import_error = exc
    return classify_sdk(version, import_error)


def mcp_available(state: Optional[SdkDisposition] = None) -> bool:
    """True only when the SDK is importable AND within the declared bound.

    Kept as the one-line predicate for callers that genuinely only need "can the server start"
    (`main`, and the historical `mokata.mcp_server.mcp_available` patch point). Anything that
    reports a FAILURE to a human must use `sdk_state()` instead — a bool cannot say which of the
    three failures it is, and picking the wrong one is what shipped a remedy that loops.
    """
    return (sdk_state() if state is None else state).usable


def _public_module() -> Any:
    """The compat surface callers monkeypatch — the `mokata.mcp_server` shim, falling back to
    this module. `main` resolves `mcp_available` through it so the historical patch point
    (`mokata.mcp_server.mcp_available = ...`) keeps working unchanged after the package split
    (zero behavior change)."""
    return sys.modules.get("mokata.mcp_server", sys.modules[__name__])


def build_server() -> Any:
    """Construct the FastMCP server with every tool registered.

    The `mcp` SDK is an unconditional dependency, but that is NOT a guarantee this succeeds:
    `mcp>=1.2` carried no upper bound until 0.0.18, so between 2026-07-28 (when `mcp` 2.0.0
    removed `mcp.server.fastmcp`) and this fix, a healthy `pip install mokata` was exactly what
    produced an SDK the server cannot build on. The docstring here previously said this "succeeds
    on any healthy `pip install mokata`" — prose that justified the behaviour rather than
    describing it, and that had been false for fifteen days (doc 85 §7h).

    So the failure is DIAGNOSED rather than assumed: `sdk_state()` says which of absent /
    incompatible / broken it is, and the RuntimeError carries that state's own remedy.
    """
    # Resolved through `_public_module()`, the SAME seam `main` uses. When these two read the SDK
    # through different names, an import-order accident decides which one a caller (or a test)
    # actually patched — `main` would clear its own gate and then `build_server` would raise from
    # the other module. One seam, one answer.
    state = _public_module().sdk_state()
    if not state.usable:
        raise RuntimeError(f"the mokata MCP server cannot be built: {state.diagnosis()}")
    try:
        from mcp.server.fastmcp import FastMCP
        from mcp.types import ToolAnnotations
    except ImportError as exc:  # pragma: no cover - the state flipped between check and import
        raise RuntimeError(
            f"the mokata MCP server cannot be built: {sdk_state().diagnosis()}") from exc

    from .tool_annotations import annotations_for, description_for

    server = FastMCP(SERVER_NAME)
    for spec in TOOLS:
        # MCP-R.D0: every served tool call rides `_serve` — self-registers this window (R5), runs
        # under the MCP-surface budget (R1), and returns a single-vocab structured status (R2/R3/R6).
        # MCP-R.D1a: project the tool's `kind` (+ the grounded open-world set) into MCP annotations
        # so the client can reason read-vs-write / reaches-out BEFORE calling — metadata only, it
        # leaves the inputSchema (D0's parity guarantee) and every tool result byte-identical.
        # MCP-R.D2: a gated write's description carries the shared OUTCOMES contract (proposal /
        # human-declined / server-error + the `mokata approve --list` fallback), appended once from
        # `kind` rather than re-prosed per tool. Read tools stay byte-identical. Metadata only —
        # like D1a's annotations it changes no input schema and no tool result.
        server.add_tool(_offloop(_serve(spec.fn, name=spec.name, kind=spec.kind)), name=spec.name,
                        description=description_for(spec.kind, spec.name, spec.fn.__doc__ or ""),
                        annotations=ToolAnnotations(**annotations_for(spec.kind, spec.name)))
    return server


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        prog="mokata-mcp",
        description="mokata MCP server (stdio) — mokata operations as native MCP tools.")
    parser.add_argument("--path", default=".",
                        help="repo root the tools operate on (default: current dir)")
    # B-VER: `--version` prints mokata's version and exits 0 BEFORE any SDK import — it is the
    # target of the version-parity probe (`mcp_admin.version_parity`), which launches this exact
    # registered command to learn which mokata the server actually serves. argparse's `version`
    # action fires during `parse_args`, ahead of the `mcp_available()` SDK check below, so the
    # probe target never hangs and never needs MCP deps (works in a stripped env). A server that
    # PREDATES this flag rejects it with exit 2 — and that failure is itself the staleness signal.
    from .. import __version__
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)

    # Fail LOUD, not dead (Stage 3b.1) — and, since MCP-SDK-2-BREAKS-THE-SERVER, fail loud about
    # the RIGHT THING. Rather than let build_server() raise an uncaught traceback — which Claude
    # Code surfaces only as a failed/absent server the user can't diagnose — name the cause and a
    # remedy on stderr and exit non-zero.
    #
    # ⚠ This branch used to hardcode ONE message: "the MCP SDK is not installed … reinstall
    # mokata". Under `mcp` 2.0.0 every clause of it was false — the SDK was installed, and the
    # reinstall re-resolved the same broken version, so the remedy was a loop. The message is now
    # the STATE's own (`diagnosis()`), which is why absent, incompatible and broken can no longer
    # be told the same thing. Gated on the disposition, not on a bool, so the text and the refusal
    # can never come from different reads (doc 85 §7g).
    state = _public_module().sdk_state()
    if not state.usable:
        sys.stderr.write(f"mokata-mcp: {state.diagnosis()}\n")
        return 1

    # RUN-ID-DRIFT — register this window EAGERLY, at process start, BEFORE serving anything.
    #
    # The comment above `_register_this_window` has always claimed registry liveness is "a STRUCTURAL
    # fact, not a user-dependent one". It was not: registration fired from `_serve`, i.e. on the FIRST
    # TOOL CALL the server happened to serve. A window the user had opened but not yet asked mokata
    # anything therefore had only the row its SessionStart hook wrote — and that hook process exits
    # within a second, so a sibling window read the row's pid as dead, PRUNED it, and `live_runs`
    # returned [] with no live sibling to find. Every narrowing that depends on the registry
    # (`resolve_run`'s LIVE rung, the gate hook's ambiguity narrowing, the badge's display filter)
    # silently lost the one signal that could have disambiguated the repo. Registering here makes
    # the claim true: a window is in the registry from the moment its server exists.
    #
    # Same call, same degrade posture (`_register_this_window` is D5-classed and swallows), and it
    # runs BEFORE `.run()` blocks on stdio, so a slow registry delays startup rather than a user's
    # tool call — the reverse of R5's tradeoff, and the right way round at startup. `_serve` still
    # re-registers per call, which is now a REFRESH (`last_seen`/phase) rather than the first write.
    _register_this_window(getattr(args, "path", ".") or ".")
    # 0.0.26 L8 (MP.S1) — on the plugin route (`CLAUDE_PLUGIN_DATA` is exported only to a plugin's
    # servers and hooks) this process IS the pinned `uvx --from "mokata[full]==<v>"` environment:
    # record its interpreter so the plugin's hooks and CLI launcher run the same version, fast.
    # A no-op on every other route; never raises.
    from ..plugin_runtime import record_runtime
    record_runtime()
    # O14b — this process lives for the whole session: share one code-graph `serve` per repo across
    # calls instead of starting (and leaking) one per call.
    from ..knowledge.crg_client import enable_session_pool
    enable_session_pool()
    _run_stdio(_public_module().build_server())   # stdio (plugin-launched); each tool takes `path`
    return 0


def _run_stdio(server: Any) -> None:
    """O14 — serve stdio with the JSON-RPC stream on a PRIVATE descriptor.

    The protocol owns stdout, and before this anything else that reached file descriptor 1 went
    into the client's JSON-RPC stream: a stray `print`, a library warning written to stdout, and —
    the case no code review catches — any child process (git, a graph CLI, a test runner) started
    without its stdout captured, because a child inherits fd 1. One such line and the client
    drops the connection or waits for a reply that is now unparseable: a "stuck" server. So the
    real stdout is duplicated to a private fd that only the transport writes, and fd 1 (plus
    `sys.stdout`) is pointed at stderr, where a stray write is harmless log noise. Works the same
    on Windows (`os.dup`/`os.dup2` are CRT calls there). A non-FastMCP server (a test double) is
    run as-is."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:                                   # pragma: no cover - checked in main
        FastMCP = None                                    # type: ignore[assignment]
    if FastMCP is None or not isinstance(server, FastMCP):
        server.run()
        return
    import os
    from io import TextIOWrapper
    import anyio
    from mcp.server.stdio import stdio_server

    try:
        sys.stdout.flush()
        rpc_fd = os.dup(sys.stdout.fileno())
        os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    except (AttributeError, OSError, ValueError):
        # No usable stderr descriptor to park fd 1 on (a windowless launcher): serve as the SDK
        # does. Nothing is lost that this host could have kept.
        server.run()
        return
    sys.stdout = sys.stderr
    rpc = anyio.wrap_file(TextIOWrapper(os.fdopen(rpc_fd, "wb"), encoding="utf-8"))
    low = server._mcp_server                              # FastMCP's own lowlevel server (mcp<2)

    async def _serve_forever() -> None:
        async with stdio_server(stdout=rpc) as (read_stream, write_stream):
            await low.run(read_stream, write_stream, low.create_initialization_options())

    anyio.run(_serve_forever)
