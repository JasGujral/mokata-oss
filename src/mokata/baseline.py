"""Stage 34 Part B — clean-test-baseline check.

Before an implementation run starts, it's worth knowing the test suite is GREEN at baseline,
so any new failure is attributable to the change (TDD hygiene). This runs the project's
configured test command and reports green/red. It **degrades cleanly**: when no test command
is known, it says so and does NOT hard-block (mokata never guesses a test framework — that
would be an assumption; the user states the command).

The command comes from `settings.baseline.test_command` in the manifest, or an explicit
override. Running it is a read-only diagnostic the user invokes; it makes no durable write and
no network call.
"""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from .errors import ControlSignal

# settings.baseline.test_command — the project's test command (mokata never guesses one).
BASELINE_SETTINGS_KEY = "baseline"
#: 0.0.29 lane G (#82-1b, Jas D4) — NO wall clock ends a suite. It ends on exit, on SILENCE (no output
#: growth) for `settings.baseline.silence_seconds` (this default; `0` = never), on a disk bound derived
#: from free space, on a cancel (lane B's lever: the MCP request attached to it, or the server's exit),
#: or on Ctrl-C at the CLI. Every ending that is not a result says which and the way out.
BASELINE_SILENCE_SECONDS = 600
#: The manifest leaf under `settings.baseline` that moves the silence bound.
SILENCE_SETTING = "baseline.silence_seconds"
#: The spool bound's ceiling. The bound itself is DERIVED, not typed: a quarter of the free space the
#: temp volume had when the run started, never above this (a disk-safety bound, not a work cap).
SPOOL_CEILING_BYTES = 16 * 1024 ** 3
#: When the temp volume cannot say how much space it has, the spool bound falls back to this.
SPOOL_FALLBACK_BYTES = 4 * 1024 ** 3
#: The report renders the last 500 characters; the door returns only this much of each stream, so a
#: suite that wrote gigabytes is never read into memory to show a 500-character tail.
_TAIL_BYTES = 4096
#: The road a person takes when the suite cannot be run to a result here: run it and record it.
#: Equal to `awaiting.TEST_RUN_RED_CMD` (pinned) — spelled there, read here lazily.
_RECORD_ROAD = ("run it yourself and record the result: `{red}` (`--passed` for a green run)")


class Silent(ControlSignal):
    """The suite wrote nothing for the silence bound: ended as stalled (raised from the door's
    progress hook, which ends the tree and propagates it unchanged; caught in `baseline_status`)."""

    def __init__(self, ran_s: float, quiet_s: float) -> None:
        super().__init__("silent")
        self.ran_s = ran_s
        self.quiet_s = quiet_s


class Spooled(ControlSignal):
    """The suite's output on disk passed the derived spool bound: ended as a runaway (control
    flow, caught in `baseline_status`)."""

    def __init__(self, total: int, bound: int) -> None:
        super().__init__("spooled")
        self.total = total
        self.bound = bound


def silence_seconds(manifest: Any = None) -> float:
    """`settings.baseline.silence_seconds`: a finite number >= 0 (`0` = never end on silence); a
    missing, bad, negative, NaN/inf or boolean value is the default — never fatal."""
    value = None
    try:
        if manifest is not None:
            s = manifest.setting(BASELINE_SETTINGS_KEY, {}) or {}
            value = s.get("silence_seconds") if isinstance(s, dict) else None
    except Exception:  # noqa: BLE001 — a settings read must never take the baseline down
        value = None
    if value is None or isinstance(value, bool):
        return float(BASELINE_SILENCE_SECONDS)
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return float(BASELINE_SILENCE_SECONDS)
    if math.isnan(seconds) or math.isinf(seconds) or seconds < 0:
        return float(BASELINE_SILENCE_SECONDS)
    return seconds


def spool_bound(tmpdir: Optional[str] = None, disk_usage: Any = None) -> int:
    """`min(16 GiB, free bytes of the temp volume // 4)` — the run can never take more than a quarter
    of the disk it found free. Never raises (an unreadable volume gets the fallback)."""
    import shutil
    import tempfile
    try:
        free = (disk_usage or shutil.disk_usage)(tmpdir or tempfile.gettempdir()).free
        return int(min(SPOOL_CEILING_BYTES, max(0, int(free)) // 4))
    except (OSError, AttributeError, TypeError, ValueError):
        return SPOOL_FALLBACK_BYTES


def _bytes(n: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GiB"


GREEN = "green"
RED = "red"
UNKNOWN = "unknown"
#: 0.0.28 lane B (refute H1): the command exists but did not run to a result — the door refused it,
#: it could not start, or it timed out. NOT ok: a run that did not happen never reads as a pass.
#: (`UNKNOWN` keeps exactly one meaning: no command configured, degrade clean.)
NOT_RUN = "not-run"
#: The states that are ok — an allow-list, so a future state is not-ok until someone says it is.
OK_STATES = (GREEN, UNKNOWN)

NO_COMMAND_MESSAGE = (
    "baseline: no test command known — set `settings.baseline.test_command` (or pass one) "
    "so mokata can confirm a green baseline. Skipping (not a hard failure)."
)


@dataclass
class BaselineResult:
    state: str                 # green | red | unknown | not-run
    command: Optional[str] = None
    detail: str = ""
    returncode: Optional[int] = None
    # 0.0.23 B2 — WHICH Python the project's tests ran in (`project_env.describe`), named in the
    # report so a RED caused by the wrong interpreter is visible as that, not as a failing suite.
    env_line: str = ""
    #: 0.0.29 lane G — WHICH ending a NOT_RUN was: "silent" | "spooled" | "" (could not run).
    ending: str = ""

    @property
    def ok(self) -> bool:
        # green is good; unknown (no command) degrades clean; red and not-run are not ok.
        return self.state in OK_STATES

    def render(self) -> str:
        where = f"\n  tests ran in: {self.env_line}" if self.env_line else ""
        if self.state == GREEN:
            return f"baseline: GREEN — `{self.command}` passed. New failures are yours." + where
        if self.state == RED:
            return (f"baseline: RED — `{self.command}` is already failing (rc "
                    f"{self.returncode}). Fix or acknowledge before starting, so new "
                    "failures are attributable to your change." + where)
        if self.state == NOT_RUN and self.ending:
            # 0.0.29 lane G — an ending that is not a result names itself and its way out (detail).
            return f"baseline: NOT RUN — {self.detail}" + where
        if self.state == NOT_RUN:
            return (f"baseline: NOT RUN — `{self.command}` did not run to a result: {self.detail}."
                    " Nothing about the suite is known; fix that and run `mokata baseline` "
                    "again." + where)
        return NO_COMMAND_MESSAGE


def baseline_command(manifest: Any = None, override: Optional[str] = None) -> Optional[str]:
    """Resolve the test command: an explicit override, else settings.baseline.test_command."""
    if override:
        return override
    if manifest is None:
        return None
    try:
        s = manifest.setting(BASELINE_SETTINGS_KEY, {}) or {}
    except AttributeError:
        return None
    cmd = s.get("test_command") if isinstance(s, dict) else None
    return cmd or None


def _record_road() -> str:
    from .awaiting import TEST_RUN_RED_CMD
    return _RECORD_ROAD.format(red=TEST_RUN_RED_CMD)


def _silent_text(command: str, exc: "Silent", silence: float) -> str:
    return (f"`{command}` wrote nothing for {exc.quiet_s:.0f} s (`settings.baseline.silence_seconds`, "
            f"now {silence:g}) and was stopped as stalled after {exc.ran_s:.0f} s. Nothing about the "
            "suite is known. A suite that is quiet by design (output piped through `tail`/`grep`, "
            "progress written only to a file, a runner that buffers its output, one long silent test) "
            "needs a larger value: `mokata config set settings.baseline.silence_seconds <seconds>` "
            "(0 = never stop on silence). Or " + _record_road() + ".")


def _spooled_text(command: str, exc: "Spooled") -> str:
    return (f"`{command}` wrote over {_bytes(exc.bound)} of output (a quarter of the free space on "
            "the temp volume when it started) and was stopped. Nothing about the suite is known — "
            "check it for a runaway loop, or " + _record_road() + ".")


def _run_env(run_env: Dict[str, str]) -> Dict[str, str]:
    """The child's env: `PYTHONUNBUFFERED=1` added only when absent or empty (a user's value is kept),
    so a Python suite's progress reaches the output file and the silence bound sees it. A COPY — the
    caller's environment is never changed."""
    env = dict(run_env)
    if not env.get("PYTHONUNBUFFERED"):
        env["PYTHONUNBUFFERED"] = "1"
    return env


def baseline_status(command: Optional[str], cwd: Optional[str] = None, *,
                    silence: float = BASELINE_SILENCE_SECONDS,
                    settings_python: Optional[str] = None,
                    environ: Optional[Dict[str, str]] = None,
                    clock: Optional[Callable[[], float]] = None,
                    disk_usage: Any = None) -> BaselineResult:
    """Run the test command and report green/red; UNKNOWN (degrade-clean) when none given;
    NOT_RUN (not ok) when it could not run, was stopped as stalled (`silence` seconds with no output
    growth, `0` = never) or wrote past the derived spool bound. Raises nothing of its own; a
    `knowledge.cancel.Cancelled` (lane B's lever: the MCP request attached to the run was cancelled,
    or the server is exiting) PROPAGATES, so the MCP lifecycle treats the run as a cancelled read and
    the next identical call is a new run, never a kept "stopped" answer.

    0.0.23 B2: the command runs in the PROJECT's Python env (`project_env.resolve`), not in
    whatever environment mokata's own process happened to inherit.
    0.0.29 lane G: it runs through the process door (`quiet_spawn.run_jobbed`) with NO clock
    (`timeout=None`); the door calls `watch` between wait slices with the bytes written so far, and
    the policy below ends the tree by raising. `clock`/`disk_usage` are test seams."""
    if not command:
        return BaselineResult(state=UNKNOWN, detail=NO_COMMAND_MESSAGE)
    from . import project_env
    from .knowledge import cancel as C
    from .quiet_spawn import run_jobbed
    base_env = os.environ if environ is None else environ
    penv = project_env.resolve(cwd or os.getcwd(), settings_python, base_env)
    env_line = project_env.describe(penv)
    run_env = _run_env(project_env.activated(base_env, penv))
    now = clock or time.perf_counter      # ⚠ perf_counter, not monotonic (0.0.21: Windows ticks)
    bound = spool_bound(disk_usage=disk_usage)
    start = now()
    seen = {"last": 0, "quiet_since": start}

    def watch(total: int) -> None:
        t = now()
        if total > seen["last"]:
            seen["last"] = total
            seen["quiet_since"] = t
        quiet = t - seen["quiet_since"]
        C.report(done=total, unit="bytes of test output written", quiet_s=round(quiet, 1))
        if total > bound:
            raise Spooled(total, bound)
        if silence > 0 and quiet >= silence:
            raise Silent(t - start, quiet)

    try:
        # The baseline one-liner from the repo's committed manifest (or the user's CLI arg),
        # still run by a SHELL (Jas B-Q2): shell syntax (`pytest -q && ruff .`, pipes, globs) is
        # its documented use. The door's `shell_line` is Python's own `shell=True` — `/bin/sh`
        # hard-coded on POSIX, never a `sh` looked up on the project-activated PATH; on Windows
        # the parent's `ComSpec /c "…"`, run with `NoDefaultCurrentDirectoryInExePath` set (lane B,
        # 0.0.29), so cmd.exe does not search the cwd for the first word and a repo cannot plant a
        # `pytest.cmd` there. stdin is an empty file, never mokata's: a batch command that asks a
        # question gets EOF (0.0.18 round 2: an inherited stdin wedged the Windows leg for 54 min).
        rc, out, err = run_jobbed(shell_line=command, cwd=cwd, timeout=None,  # nosec B602
                                  env=run_env, progress=watch, tail_bytes=_TAIL_BYTES)
    except Silent as exc:
        return BaselineResult(state=NOT_RUN, command=command, ending="silent",
                              detail=_silent_text(command, exc, silence), env_line=env_line)
    except Spooled as exc:
        return BaselineResult(state=NOT_RUN, command=command, ending="spooled",
                              detail=_spooled_text(command, exc), env_line=env_line)
    except OSError as exc:               # could not start, or the door refused it (NO_JOB)
        return BaselineResult(state=NOT_RUN, command=command,
                              detail=f"could not run the test command: {exc}", env_line=env_line)
    state = GREEN if rc == 0 else RED
    return BaselineResult(state=state, command=command, returncode=rc,
                          detail=(err or out or "").strip()[-500:], env_line=env_line)
