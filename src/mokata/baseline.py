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

import subprocess
from dataclasses import dataclass
from typing import Any, Optional

# settings.baseline.test_command — the project's test command (mokata never guesses one).
BASELINE_SETTINGS_KEY = "baseline"
# How long the baseline test command may run before we report (not crash) a timeout.
BASELINE_TIMEOUT_SECONDS = 600

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

    @property
    def ok(self) -> bool:
        # green is good; unknown degrades clean (not a hard failure); only red is "not ok".
        return self.state != RED

    def render(self) -> str:
        if self.state == GREEN:
            return f"baseline: GREEN — `{self.command}` passed. New failures are yours."
        if self.state == RED:
            return (f"baseline: RED — `{self.command}` is already failing (rc "
                    f"{self.returncode}). Fix or acknowledge before starting, so new "
                    "failures are attributable to your change.")
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
                    timeout: int = BASELINE_TIMEOUT_SECONDS) -> BaselineResult:
    """Run the test command and report green/red; UNKNOWN (degrade-clean) when none given.
    Never raises — a command that can't run reports red with the reason, not an exception."""
    if not command:
        return BaselineResult(state=UNKNOWN, detail=NO_COMMAND_MESSAGE)
    try:
        # Justification for the B602 suppression: `command` is the USER's OWN test command (their
        # `settings.baseline` config / CLI arg), run in their own shell exactly as they'd run it;
        # not attacker-controlled input. A shell is required so a normal test one-liner
        # (`pytest -q && ruff .`, pipes, globs) works. Bounded by `timeout`; degrade-clean.
        proc = subprocess.run(command, shell=True, cwd=cwd, capture_output=True,  # nosec B602
                              text=True, timeout=timeout)
    except Exception as exc:  # missing binary, timeout, etc. — report, don't crash
        return BaselineResult(state=RED, command=command,
                              detail=f"could not run test command: {exc}")
    state = GREEN if proc.returncode == 0 else RED
    return BaselineResult(state=state, command=command, returncode=proc.returncode,
                          detail=(proc.stderr or proc.stdout or "").strip()[-500:])
