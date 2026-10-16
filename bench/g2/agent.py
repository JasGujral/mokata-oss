"""The AGENT under test: Claude Code headless (`claude -p`) on the user's OWN login.

⛔ The child environment is SCRUBBED of every variable that would move Claude Code off the user's
login onto metered API/cloud billing (`ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, Bedrock/Vertex
switches) and of the judge's key. An owner with `ANTHROPIC_API_KEY` exported for something else
would otherwise run — and pay for — the whole matrix on the API without being told.

`FakeAgent` is the test double: it replays scripted stream-json events, so the whole harness is
exercised with no network and no `claude` binary.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

SCRUBBED_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY",
                "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "MOKATA_G2_JUDGE_API_KEY")


def scrubbed_env(base: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    env = dict(os.environ if base is None else base)
    for key in SCRUBBED_ENV:
        env.pop(key, None)
    return env


@dataclass
class AgentResult:
    lines: List[str]
    exit_code: int
    argv: List[str] = field(default_factory=list)
    stderr: str = ""
    wall_s: float = 0.0


class ClaudeCodeAgent:
    id = "claude-code"

    def __init__(self, claude_bin: str = "claude", permission_mode: str = "acceptEdits",
                 timeout_s: int = 3600):
        self.claude_bin = claude_bin
        self.permission_mode = permission_mode
        self.timeout_s = timeout_s

    def available(self) -> bool:
        return shutil.which(self.claude_bin) is not None

    def version(self) -> str:
        try:
            out = subprocess.run([self.claude_bin, "--version"], capture_output=True, text=True,
                                 timeout=60, env=scrubbed_env())
            return out.stdout.strip() or out.stderr.strip()
        except (OSError, subprocess.SubprocessError) as exc:
            return f"unknown ({exc})"

    def argv(self, prompt: str, max_turns: int, extra: Sequence[str]) -> List[str]:
        return [self.claude_bin, "-p", prompt, "--output-format", "stream-json", "--verbose",
                "--max-turns", str(max_turns), "--permission-mode", self.permission_mode,
                *extra]

    def run(self, workdir: str, prompt: str, *, max_turns: int, extra: Sequence[str],
            condition: str, repeat: int) -> AgentResult:
        argv = self.argv(prompt, max_turns, extra)
        start = time.monotonic()
        try:
            proc = subprocess.run(argv, cwd=workdir, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=self.timeout_s,
                                  env=scrubbed_env(), stdin=subprocess.DEVNULL)
            code, out, err = proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode("utf-8", "replace")
            code, err = -1, f"timeout after {self.timeout_s}s"
        return AgentResult(lines=out.splitlines(), exit_code=code, argv=argv, stderr=err,
                           wall_s=round(time.monotonic() - start, 3))


Script = Callable[[str, str, str, int], List[Dict[str, Any]]]


class FakeAgent:
    """Replays `script(workdir, prompt, condition, repeat) -> [event dicts]` as a stream."""
    id = "fake-agent"

    def __init__(self, script: Script):
        self.script = script
        self.calls: List[Dict[str, Any]] = []

    def available(self) -> bool:
        return True

    def version(self) -> str:
        return "fake"

    def run(self, workdir: str, prompt: str, *, max_turns: int, extra: Sequence[str],
            condition: str, repeat: int) -> AgentResult:
        self.calls.append({"workdir": workdir, "condition": condition, "repeat": repeat,
                           "extra": list(extra)})
        events = self.script(workdir, prompt, condition, repeat)
        return AgentResult(lines=[json.dumps(e) for e in events], exit_code=0,
                           argv=["fake-agent", condition, str(repeat)])
