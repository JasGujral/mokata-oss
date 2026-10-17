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

NO_COMMAND_MESSAGE = (
    "baseline: no test command known — set `settings.baseline.test_command` (or pass one) "
    "so mokata can confirm a green baseline. Skipping (not a hard failure)."
)


@dataclass
class BaselineResult:
    state: str                 # green | red | unknown
    command: Optional[str] = None
    detail: str = ""
    returncode: Optional[int] = None
    # 0.0.23 B2 — WHICH Python the project's tests ran in (`project_env.describe`), named in the
    # report so a RED caused by the wrong interpreter is visible as that, not as a failing suite.
    env_line: str = ""

    @property
    def ok(self) -> bool:
        # green is good; unknown degrades clean (not a hard failure); only red is "not ok".
        return self.state != RED

    def render(self) -> str:
        where = f"\n  tests ran in: {self.env_line}" if self.env_line else ""
        if self.state == GREEN:
            return f"baseline: GREEN — `{self.command}` passed. New failures are yours." + where
        if self.state == RED:
            return (f"baseline: RED — `{self.command}` is already failing (rc "
                    f"{self.returncode}). Fix or acknowledge before starting, so new "
                    "failures are attributable to your change." + where)
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
    """Run the test command and report green/red; UNKNOWN (degrade-clean) when none given.
    Never raises — a command that can't run reports red with the reason, not an exception.

    0.0.23 B2: the command runs in the PROJECT's Python env (`project_env.resolve`), not in
    whatever environment mokata's own process happened to inherit."""
    if not command:
        return BaselineResult(state=UNKNOWN, detail=NO_COMMAND_MESSAGE)
    from . import project_env
    base_env = os.environ if environ is None else environ
    penv = project_env.resolve(cwd or os.getcwd(), settings_python, base_env)
    env_line = project_env.describe(penv)
    run_env = project_env.activated(base_env, penv)
    try:
        # Justification for the B602 suppression: `command` is the USER's OWN test command (their
        # `settings.baseline` config / CLI arg), run in their own shell exactly as they'd run it;
        # not attacker-controlled input. A shell is required so a normal test one-liner
        # (`pytest -q && ruff .`, pipes, globs) works. Bounded by `timeout`; degrade-clean.
        # `stdin=DEVNULL`, not inherited. A test command is a BATCH command: if it asks a
        # question it must get EOF and decide, never sit on a fd nobody is going to write to.
        # Inheriting mokata's stdin makes the bound above a lie — `timeout` bounds the wait, but
        # only after the caller has already lost `timeout` seconds to a prompt no one can see.
        # (0.0.18 round 2: an inherited stdin on a shell subprocess is what wedged the Windows
        # unit leg for 54 minutes; the class is swept in `tests/_windows_portability.py`.)
        proc = subprocess.run(command, shell=True, cwd=cwd, capture_output=True,  # nosec B602
                              stdin=subprocess.DEVNULL, text=True, timeout=timeout, env=run_env)
    except Exception as exc:  # missing binary, timeout, etc. — report, don't crash
        return BaselineResult(state=RED, command=command,
                              detail=f"could not run test command: {exc}", env_line=env_line)
    state = GREEN if proc.returncode == 0 else RED
    return BaselineResult(state=state, command=command, returncode=proc.returncode,
                          detail=(proc.stderr or proc.stdout or "").strip()[-500:],
                          env_line=env_line)
