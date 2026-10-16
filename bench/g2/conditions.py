"""G2 conditions — the treatment vector, and the mechanical check that it was actually applied.

Methodology bound (doc 84 G2 row): the condition-specific CLAUDE.md is the treatment vector, plus —
for mokata-on — mokata's own project wiring (`mokata setup claude --scope project`: MCP + hooks).

⛔ ISOLATION FROM THE OWNER'S MACHINE. A "bare" run on a laptop where mokata is installed as a
user-scope plugin would silently be a mokata run. Two layers stop that:
  1. every condition runs with `--setting-sources project,local` (no user-scope hooks/plugins) and
     `--strict-mcp-config` with a condition-specific `--mcp-config`;
  2. `check()` reads the stream's own `init` event and marks the run CONTAMINATED when the MCP
     servers / tools it reports disagree with the condition. The archive validator refuses a run
     set containing a contaminated run — the flags are belt, this is braces, and only this one is
     evidence.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

from .streamparse import Trajectory

MOKATA_SERVER = "mokata"
MOKATA_TOOL_PREFIX = "mcp__mokata__"

#: The CLAUDE.md text every condition starts from. Identical across conditions so the only
#: difference a condition introduces is its own block below it.
BASELINE_CLAUDE_MD = (
    "# Working agreement (G2 eval)\n\n"
    "You are working in a fresh clone of this repository with no human available to answer "
    "questions. Complete the task you are given end to end, run the tests, and stop when done.\n"
)

MOKATA_CLAUDE_MD = (
    "\n## mokata\n\n"
    "mokata is installed in this repository. Use its brainstorm → spec → develop → review flow, "
    "its code graph for navigation and impact, and its memory for decisions.\n"
)

Runner = Callable[[Sequence[str], str], int]


def default_runner(argv: Sequence[str], cwd: str) -> int:
    return subprocess.run(list(argv), cwd=cwd, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, timeout=600).returncode


@dataclass
class Condition:
    name: str
    description: str
    expect_mokata: bool
    claude_md: str
    setup: List[List[str]] = field(default_factory=list)   # argv; "{workdir}" / "{python}" expand
    disallowed_tools: List[str] = field(default_factory=list)
    #: Declared surface for AXI-LEAN reporting: "none" | "full" | "lean".
    surface: str = "none"

    def fingerprint(self) -> str:
        """Hash of everything that defines the treatment — archived so a STUDY.md reader can tell
        whether two runs ran the same condition."""
        blob = json.dumps({"md": self.claude_md, "setup": self.setup,
                           "disallowed": sorted(self.disallowed_tools), "surface": self.surface},
                          sort_keys=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

    def prepare(self, workdir: str, run_dir: str, runner: Optional[Runner] = None) -> List[str]:
        """Apply the treatment to a fresh clone; return the extra `claude` CLI args."""
        runner = runner or default_runner
        for argv in self.setup:
            cmd = [a.replace("{workdir}", workdir).replace("{python}", sys.executable) for a in argv]
            code = runner(cmd, workdir)
            if code != 0:
                raise RuntimeError(f"condition {self.name}: setup {cmd!r} exited {code}")
        md_path = os.path.join(workdir, "CLAUDE.md")
        existing = ""
        if os.path.exists(md_path):
            with open(md_path, encoding="utf-8") as fh:
                existing = fh.read()
        with open(md_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(self.claude_md + ("\n" + existing if existing else ""))
        project_mcp = os.path.join(workdir, ".mcp.json")
        if self.expect_mokata and os.path.exists(project_mcp):
            mcp_config = project_mcp
        else:
            mcp_config = os.path.join(run_dir, "mcp-config.json")
            with open(mcp_config, "w", encoding="utf-8") as fh:
                json.dump({"mcpServers": {}}, fh)
        args = ["--setting-sources", "project,local", "--strict-mcp-config",
                "--mcp-config", mcp_config]
        if self.disallowed_tools:
            args += ["--disallowedTools", ",".join(self.disallowed_tools)]
        return args

    def check(self, tr: Trajectory) -> List[str]:
        """Contamination: what the run's own `init` event says vs what the condition requires."""
        problems = []
        if not tr.init:
            return ["no init event in the stream — the condition cannot be verified"]
        names = {str(s.get("name")) for s in tr.mcp_servers}
        mokata_tools = [t for t in tr.tools_offered if t.startswith(MOKATA_TOOL_PREFIX)]
        if self.expect_mokata:
            status = {str(s.get("name")): s.get("status") for s in tr.mcp_servers}
            if MOKATA_SERVER not in names:
                problems.append("mokata condition ran WITHOUT the mokata MCP server")
            elif status.get(MOKATA_SERVER) not in (None, "connected"):
                problems.append(f"mokata MCP server status {status.get(MOKATA_SERVER)!r}")
            for tool in self.disallowed_tools:
                if tool in tr.tools_offered:
                    problems.append(f"disallowed tool still offered: {tool}")
            if tr.tools_offered and not mokata_tools:
                problems.append("mokata condition offered NO mcp__mokata__ tools")
        else:
            if MOKATA_SERVER in names or mokata_tools:
                problems.append("bare condition ran WITH mokata present (user-scope leak?)")
        return problems


def bare() -> Condition:
    return Condition(name="bare", description="Bare Claude Code: no mokata, no MCP servers.",
                     expect_mokata=False, claude_md=BASELINE_CLAUDE_MD, surface="none")


def mokata_on() -> Condition:
    return Condition(
        name="mokata", description="mokata-on: `mokata setup claude --scope project` (MCP + hooks).",
        expect_mokata=True, claude_md=BASELINE_CLAUDE_MD + MOKATA_CLAUDE_MD,
        setup=[["{python}", "-m", "mokata.cli", "setup", "claude", "--path", "{workdir}",
                "--scope", "project", "--yes"]],
        surface="full")


def _mokata_lean() -> Condition:
    from .lean import mokata_lean            # imports mokata's registry; only when asked for
    return mokata_lean()


BUILTIN: Dict[str, Callable[[], Condition]] = {"bare": bare, "mokata": mokata_on,
                                               "mokata-lean": _mokata_lean}


def resolve(names: Sequence[str]) -> List[Condition]:
    out = []
    for n in names:
        if n not in BUILTIN:
            raise ValueError(f"unknown condition {n!r}; known: {sorted(BUILTIN)}")
        out.append(BUILTIN[n]())
    if "bare" not in [c.name for c in out]:
        raise ValueError("G2 compares against bare Claude Code: the 'bare' condition is required")
    return out
