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

import os
import subprocess
from dataclasses import dataclass
from typing import Any, Dict, Optional

# settings.baseline.test_command — the project's test command (mokata never guesses one).
BASELINE_SETTINGS_KEY = "baseline"
# How long the baseline test command may run before we report (not crash) a timeout. This is the
# TERMINAL / `mokata baseline` (CLI) bound: a human at a terminal can afford a full 10-minute suite.
BASELINE_TIMEOUT_SECONDS = 600
# MCP-R.D0 · R7 — the MCP-SURFACE bound (the two-bound split). `baseline` is a READ tool, but on the
# MCP surface it must NEVER block stdio for 10 minutes of silence: the MCP `baseline` tool caps its
# subprocess far below 600s so a client sees a bounded report, not dead air. Only the MCP surface is
# capped — the CLI/terminal path above keeps its longer bound.
BASELINE_MCP_TIMEOUT_SECONDS = 120
#: 0.0.24 (OSS #82) — a repo whose suite takes longer than the default can raise the MCP budget:
#: `mokata config set settings.mcp.test_timeout_seconds 300`. Clamped to the terminal bound, so the
#: MCP surface can never wait longer than `mokata baseline` itself would.
MCP_TEST_TIMEOUT_SETTING = "mcp.test_timeout_seconds"


def mcp_test_timeout(manifest=None) -> float:
    """The MCP-surface budget for a tool that runs the test suite: the repo's
    `settings.mcp.test_timeout_seconds` when it is a positive number, else the default — and never
    above the terminal bound (`BASELINE_TIMEOUT_SECONDS`). A bad value is ignored, never fatal."""
    value = None
    try:
        if manifest is not None:
            # `mokata config set settings.mcp.test_timeout_seconds N` stores it NESTED
            # ({"mcp": {"test_timeout_seconds": N}}); a hand-edited flat key is honoured too.
            value = manifest.setting(MCP_TEST_TIMEOUT_SETTING)
            if value is None:
                group, _, leaf = MCP_TEST_TIMEOUT_SETTING.partition(".")
                nested = manifest.setting(group)
                value = nested.get(leaf) if isinstance(nested, dict) else None
    except Exception:  # noqa: BLE001 — a settings read must never take the tool down
        value = None
    try:
        seconds = float(value) if value is not None and not isinstance(value, bool) else None
    except (TypeError, ValueError):
        seconds = None
    if not seconds or seconds <= 0:
        return float(BASELINE_MCP_TIMEOUT_SECONDS)
    return float(min(seconds, BASELINE_TIMEOUT_SECONDS))

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


def baseline_status(command: Optional[str], cwd: Optional[str] = None,
                    timeout: int = BASELINE_TIMEOUT_SECONDS, *,
                    settings_python: Optional[str] = None,
                    environ: Optional[Dict[str, str]] = None) -> BaselineResult:
    """Run the test command and report green/red; UNKNOWN (degrade-clean) when none given;
    NOT_RUN (not ok) when it could not run or did not finish. Never raises.

    0.0.23 B2: the command runs in the PROJECT's Python env (`project_env.resolve`), not in
    whatever environment mokata's own process happened to inherit.
    0.0.28 lane B: it runs through the process door (`quiet_spawn.run_jobbed`) — its output goes to
    temp files and on a timeout its whole tree is ended, so a grandchild that holds the output
    cannot hang `mokata baseline` or the MCP `baseline` tool past the bound."""
    if not command:
        return BaselineResult(state=UNKNOWN, detail=NO_COMMAND_MESSAGE)
    from . import project_env
    from .quiet_spawn import run_jobbed
    base_env = os.environ if environ is None else environ
    penv = project_env.resolve(cwd or os.getcwd(), settings_python, base_env)
    env_line = project_env.describe(penv)
    run_env = project_env.activated(base_env, penv)
    try:
        # The baseline one-liner from the repo's committed manifest (or the user's CLI arg),
        # still run by a SHELL (Jas B-Q2): shell syntax (`pytest -q && ruff .`, pipes, globs) is
        # its documented use. The door's `shell_line` is Python's own `shell=True` — `/bin/sh`
        # hard-coded on POSIX, never a `sh` looked up on the project-activated PATH; on Windows
        # the parent's `ComSpec /c "…"`, which (cmd.exe's rule) also searches the cwd for the
        # first word, so a repo can plant `pytest.cmd` there (disclosed, 0.0.28 Low L5). stdin is
        # an empty file, never mokata's: a batch command that asks a question gets EOF (0.0.18
        # round 2: an inherited stdin wedged the Windows leg for 54 minutes).
        rc, out, err = run_jobbed(shell_line=command, cwd=cwd, timeout=timeout,  # nosec B602
                                  env=dict(run_env))
    except subprocess.TimeoutExpired:
        return BaselineResult(state=NOT_RUN, command=command,
                              detail=f"timed out after {timeout:g} s — the suite did not finish; "
                                     "nothing about its result is known", env_line=env_line)
    except OSError as exc:               # could not start, or the door refused it (NO_JOB)
        return BaselineResult(state=NOT_RUN, command=command,
                              detail=f"could not run the test command: {exc}", env_line=env_line)
    state = GREEN if rc == 0 else RED
    return BaselineResult(state=state, command=command, returncode=rc,
                          detail=(err or out or "").strip()[-500:], env_line=env_line)
