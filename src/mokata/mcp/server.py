"""MCP wiring — lazily imports the SDK; never imported by the core/CLI.

The MCP SDK is an unconditional dependency of mokata (Python 3.10+ floor), so a plain
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
import sys
import threading
from typing import Any, Callable, Dict, List, Optional

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
#   R1  the tool BODY runs in a worker thread under a wall-clock MCP-SURFACE budget; on expiry we
#       return a structured `status:"timed_out"` naming the operation + the CLI fallback, and the
#       orphaned worker (a daemon, backstopped by the tool's own inner bound) dies on its own —
#       the teamdb.probe daemon-join pattern, applied to the front door.
#   R2  one try/except turns ANY uncaught exception into mokata's OWN `status:"error"` shape
#       (isError + reason + hint), reclaiming the voice from FastMCP's generic handler.
#   R3  a None / non-dict return is coerced to the same structured `error`.
#   R6  every status the wrapper emits comes from the single-source vocab (`mcp.status`).
#
# R4 (progress/heartbeat) is a documented degrade-clean NO-OP this stage: `Context.report_progress`
# is a coroutine that must be injected as a tool parameter and awaited on the event loop — a
# synchronous, SDK-free tool body running in a worker thread cannot reach it without changing the
# tool schema, which the schema-transparency constraint forbids this stage. Liveness is delivered
# by the bounded budget + `timed_out`/`running` statuses instead; a real progress channel is D1's.
R4_PROGRESS_NOOP = True

# The interactive MCP-surface wall-clock budget (R1). NAMED and distinct from any tool's internal
# subprocess/DB bound — this is the front-door cap, not a tool's own timeout.
MCP_SURFACE_TIMEOUT_SECONDS = 60.0

# R5 — fire-and-forget self-registration threads, tracked so tests/diagnostics can drain them. The
# SERVED path never joins these; draining is a test/diagnostic affordance only.
_REG_LOCK = threading.Lock()
_REG_THREADS: "List[threading.Thread]" = []


def _spawn_registration(path: str) -> "threading.Thread":
    """R5 — self-register THIS window off the served path, in a daemon thread we do not join. A
    stalled registry (a contended cross-process lock) therefore cannot delay the tool call."""
    t = threading.Thread(target=_register_this_window, args=(path,),
                         name="mokata-mcp-register", daemon=True)
    with _REG_LOCK:
        _REG_THREADS[:] = [x for x in _REG_THREADS if x.is_alive()]   # prune finished
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


def _mcp_timeout_for(op: str) -> float:
    """The wall-clock MCP-surface budget for `op` (R1). `baseline` — a READ tool that runs the whole
    test suite — gets its own named cap far below its 600s terminal bound (R7); everything else gets
    the interactive default. Read at CALL time so the budget is patchable/tunable."""
    if op == "baseline":
        from ..baseline import BASELINE_MCP_TIMEOUT_SECONDS
        return BASELINE_MCP_TIMEOUT_SECONDS
    return MCP_SURFACE_TIMEOUT_SECONDS


def _timed_out(op: str, budget: float) -> Dict[str, Any]:
    """R1 — the bounded-budget verdict. Names the OPERATION and the CLI fallback; NEVER the args (a
    timed-out call must not echo a DSN/path/arg into the render)."""
    return {
        "status": _status.TIMED_OUT, "isError": True, "operation": op, "committed": False,
        "reason": (f"the '{op}' operation did not finish within mokata's {int(budget)}s MCP "
                   f"surface budget"),
        "hint": (f"nothing was half-committed by this; run it from your terminal with "
                 f"`mokata {op}` (no MCP time budget) or retry."),
    }


def _as_error(op: str, exc: BaseException) -> Dict[str, Any]:
    """R2 — reclaim the voice from FastMCP's generic handler. Only the exception TYPE name surfaces:
    `str(exc)` can carry a DSN/path/arg and must not leak into the render."""
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


def _serve(fn: Callable[..., Any], name: Optional[str] = None,
           kind: Optional[str] = None) -> Callable[..., Any]:
    """The ONE systemic dispatch wrapper (MCP-R.D0). Every served tool call self-registers this
    window (R5, off the served path), runs under a wall-clock MCP-surface budget (R1), and returns
    a structured, single-vocab `status` — `timed_out` on expiry (R1), `error` on any uncaught
    exception (R2) or None/non-dict return (R3) — reclaiming the outcome from silence.

    R4 progress/heartbeat is a documented degrade-clean NO-OP this stage (see the module note): the
    bounded budget + `timed_out` status carry liveness in its place.

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

        _spawn_registration(_call_path(args, kwargs))     # R5 — concurrent, never gates the body
        budget = _mcp_timeout_for(op)                     # R1/R7 — per-tool wall-clock cap
        box: Dict[str, Any] = {}

        def _run() -> None:
            try:
                box["result"] = fn(*args, **kwargs)
            except Exception as exc:                      # noqa: BLE001 - R2: reclaim ANY failure
                box["exc"] = exc

        worker = threading.Thread(target=_run, name=f"mokata-mcp-{op}", daemon=True)
        worker.start()
        worker.join(budget)

        if worker.is_alive():                             # R1 — over budget, still running
            return _timed_out(op, budget)
        if "exc" in box:
            # D1d — a tool-local validator (an enum / comma-list whose vocabulary is the TOOL's, not
            # the surface's) raises from inside the body. It is still a caller fault, so it converts
            # through the SAME single site as the pre-step's, ahead of R2's server-fault reclaim.
            if isinstance(box["exc"], ValidationError):
                return refusal(box["exc"], op)
            return _as_error(op, box["exc"])              # R2 — reclaim the exception
        return _coerce(op, box.get("result"))             # R3 — never None / non-dict

    return wrapper


# Back-compat alias — the historical seam name (R-MCP wired `build_server` and its tests through
# `_with_registration`). It now IS `_serve`, so every call inherits the D0 robustness unchanged.
_with_registration = _serve


def mcp_available() -> bool:
    """True only when the MCP SDK is actually IMPORTABLE — not merely present on disk.
    A `find_spec("mcp")` check reports True for a present-but-broken SDK (e.g. the SDK imports
    jsonschema, but jsonschema is absent), which then explodes inside build_server()'s
    `from mcp...`. So mirror that import here and treat any ImportError as unavailable: `main`
    fails loud with guidance (Stage 3b.1 guarantee #2), and the SDK stays lazily imported (the
    import is inside this function, keyed by string — no top-level `import mcp`)."""
    import importlib
    try:
        importlib.import_module("mcp.server.fastmcp")
        return True
    except ImportError:
        return False


def _public_module() -> Any:
    """The compat surface callers monkeypatch — the `mokata.mcp_server` shim, falling back to
    this module. `main` resolves `mcp_available` through it so the historical patch point
    (`mokata.mcp_server.mcp_available = ...`) keeps working unchanged after the package split
    (zero behavior change)."""
    return sys.modules.get("mokata.mcp_server", sys.modules[__name__])


def build_server() -> Any:
    """Construct the FastMCP server with every tool registered. The `mcp` SDK is an
    unconditional dependency, so this succeeds on any healthy `pip install mokata`."""
    try:
        from mcp.server.fastmcp import FastMCP
        from mcp.types import ToolAnnotations
    except ImportError as exc:  # pragma: no cover - exercised only in a stripped/broken env
        raise RuntimeError(
            "the MCP SDK is not installed; reinstall mokata (`pip install -U mokata`) to "
            "restore the mokata MCP server"
        ) from exc

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
        server.add_tool(_serve(spec.fn, name=spec.name, kind=spec.kind), name=spec.name,
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

    # Fail LOUD, not dead (Stage 3b.1). The MCP SDK is an unconditional dependency, so a healthy
    # `pip install mokata` always has it — but a stripped or broken environment can still be
    # missing it. Rather than let build_server() raise an uncaught ImportError/RuntimeError
    # traceback — which Claude Code surfaces only as a failed/absent server the user can't
    # diagnose — name the cause and the fix on stderr and exit non-zero, so the failure is legible.
    if not _public_module().mcp_available():
        sys.stderr.write(
            "mokata-mcp: the MCP SDK is not installed, so the mokata MCP server cannot start.\n"
            "  Cause: your mokata install is missing the `mcp` package (a required dependency).\n"
            "  Fix:   reinstall mokata — `pip install -U mokata` — to restore the SDK. The "
            "mokata CLI works fully without the MCP server.\n")
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
    _public_module().build_server().run()   # stdio (plugin-launched); each tool takes its `path`
    return 0
