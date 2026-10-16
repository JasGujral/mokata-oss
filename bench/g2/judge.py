"""The SEPARATE judge: grades the FULL trajectory against the task's rubric.

Separation is structural: the judge is a different process and session from the agent, sees the
whole transcript (nothing truncated — a judge grading a summary is grading the summarizer), is NOT
told the condition's name, and its raw output is archived beside the verdict so any verdict can be
re-audited or re-graded.

Two real judges, both declared in the run manifest (model + id), both opt-in:
  * `claude-cli`    — a separate headless Claude Code session on the user's own login; no API key.
  * `anthropic-api` — the Messages API. ⚠ The ONLY API-key read in the repository, and it lives
                      here, outside the product: `MOKATA_G2_JUDGE_API_KEY`, read from the
                      environment only when this judge is asked for, refusing LOUDLY when absent.
                      A dedicated name, not `ANTHROPIC_API_KEY`, so a key exported for something
                      else is never picked up by accident.

An answer the judge returns that cannot be parsed is UNGRADABLE — counted, disclosed, never
silently scored as a fail (or a pass).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .agent import scrubbed_env
from .tasks import Task

JUDGE_KEY_ENV = "MOKATA_G2_JUDGE_API_KEY"
API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"


class JudgeUnavailable(RuntimeError):
    """Raised loudly when a requested judge cannot run (no key, no model, no binary)."""


@dataclass
class Verdict:
    status: str                                   # "graded" | "ungradable"
    criteria: Dict[str, bool] = field(default_factory=dict)
    reasons: Dict[str, str] = field(default_factory=dict)
    overall_pass: Optional[bool] = None
    judge_id: str = ""
    judge_model: str = ""
    error: str = ""

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)


JUDGE_INSTRUCTIONS = """You are an impartial evaluator grading an AI coding agent's FULL trajectory.
Grade ONLY against the rubric. Judge what the trajectory shows was done, not what the agent claims.
Reply with ONE JSON object and nothing else:
{"criteria": {"<criterion id>": {"pass": true|false, "why": "<one sentence>"}, ...},
 "overall_pass": true|false}
overall_pass is true only if every "outcome" criterion passes."""


def build_prompt(task: Task, transcript: str) -> str:
    rubric = "\n".join(f"- {c.id} ({c.kind}): {c.text}" for c in task.rubric)
    return (f"{JUDGE_INSTRUCTIONS}\n\n## Task given to the agent\n{task.prompt}\n\n"
            f"## Rubric\n{rubric}\n\n## Full trajectory\n{transcript}\n")


def parse_verdict(task: Task, raw: str, judge_id: str, model: str) -> Verdict:
    match = re.search(r"\{.*\}", raw or "", re.S)
    try:
        data = json.loads(match.group(0)) if match else None
        crit = data["criteria"]
        got: Dict[str, bool] = {}
        why: Dict[str, str] = {}
        for c in task.rubric:
            entry = crit[c.id]
            if not isinstance(entry.get("pass"), bool):
                raise ValueError(f"criterion {c.id}: pass is not a boolean")
            got[c.id] = entry["pass"]
            why[c.id] = str(entry.get("why", ""))
        outcome_ids = [c.id for c in task.rubric if c.kind == "outcome"]
        overall = all(got[i] for i in outcome_ids) if outcome_ids else bool(data.get("overall_pass"))
        return Verdict(status="graded", criteria=got, reasons=why, overall_pass=overall,
                       judge_id=judge_id, judge_model=model)
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        return Verdict(status="ungradable", judge_id=judge_id, judge_model=model,
                       error=f"{type(exc).__name__}: {exc}")


class AnthropicApiJudge:
    id = "anthropic-api"

    def __init__(self, model: str, api_key: str, max_tokens: int = 2048, timeout_s: int = 600):
        self.model, self._key, self.max_tokens, self.timeout_s = model, api_key, max_tokens, timeout_s

    @classmethod
    def from_env(cls, model: Optional[str], env: Optional[Dict[str, str]] = None) -> "AnthropicApiJudge":
        env = os.environ if env is None else env
        if not model:
            raise JudgeUnavailable("--judge anthropic-api needs --judge-model (the judge is DECLARED, "
                                   "never defaulted)")
        key = env.get(JUDGE_KEY_ENV, "")
        if not key.strip():
            raise JudgeUnavailable(
                f"REFUSING: --judge anthropic-api reads its key from {JUDGE_KEY_ENV}, which is not "
                f"set. This is the eval harness's opt-in judge — mokata itself never needs a key. "
                f"Set {JUDGE_KEY_ENV}, or use --judge claude-cli (your own Claude login).")
        return cls(model=model, api_key=key.strip())

    def complete(self, prompt: str) -> str:
        body = json.dumps({"model": self.model, "max_tokens": self.max_tokens,
                           "messages": [{"role": "user", "content": prompt}]}).encode("utf-8")
        req = urllib.request.Request(API_URL, data=body, method="POST", headers={
            "x-api-key": self._key, "anthropic-version": API_VERSION,
            "content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:   # noqa: S310 (fixed https URL)
            data = json.loads(resp.read().decode("utf-8"))
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")

    def grade(self, task: Task, transcript: str) -> "tuple[Verdict, str]":
        try:
            raw = self.complete(build_prompt(task, transcript))
        except Exception as exc:  # network/HTTP: ungradable, disclosed — never a silent fail
            return Verdict(status="ungradable", judge_id=self.id, judge_model=self.model,
                           error=f"{type(exc).__name__}: {exc}"), ""
        return parse_verdict(task, raw, self.id, self.model), raw


class ClaudeCliJudge:
    """A SEPARATE headless Claude Code session on the user's login, run in an empty temp dir with
    no MCP servers and one turn. The prompt goes over STDIN (a full transcript exceeds Windows'
    32k argv limit)."""
    id = "claude-cli"

    def __init__(self, model: Optional[str] = None, claude_bin: str = "claude", timeout_s: int = 900):
        self.model, self.claude_bin, self.timeout_s = model or "", claude_bin, timeout_s

    def grade(self, task: Task, transcript: str) -> "tuple[Verdict, str]":
        argv = [self.claude_bin, "-p", "--output-format", "json", "--max-turns", "1",
                "--strict-mcp-config", "--setting-sources", "local"]
        if self.model:
            argv += ["--model", self.model]
        with tempfile.TemporaryDirectory(prefix="g2-judge-") as empty:
            try:
                proc = subprocess.run(argv, input=build_prompt(task, transcript), cwd=empty,
                                      capture_output=True, text=True, encoding="utf-8",
                                      errors="replace", timeout=self.timeout_s, env=scrubbed_env())
            except (OSError, subprocess.SubprocessError) as exc:
                return Verdict(status="ungradable", judge_id=self.id, judge_model=self.model,
                               error=f"{type(exc).__name__}: {exc}"), ""
        raw = proc.stdout
        try:
            text = json.loads(raw).get("result", "")
        except ValueError:
            text = raw
        return parse_verdict(task, text, self.id, self.model or "claude-cli-default"), raw


class FakeJudge:
    """Test double: `decide(task, transcript) -> raw judge text`."""
    id = "fake-judge"
    model = "fake"

    def __init__(self, decide: Callable[[Task, str], str]):
        self.decide = decide
        self.seen: List[str] = []

    def grade(self, task: Task, transcript: str) -> "tuple[Verdict, str]":
        self.seen.append(transcript)
        raw = self.decide(task, transcript)
        return parse_verdict(task, raw, self.id, self.model), raw


def make_judge(kind: str, model: Optional[str], env: Optional[Dict[str, str]] = None):
    if kind == "anthropic-api":
        return AnthropicApiJudge.from_env(model, env)
    if kind == "claude-cli":
        return ClaudeCliJudge(model=model)
    raise JudgeUnavailable(f"unknown judge {kind!r} (claude-cli | anthropic-api)")


REAL_JUDGES = (AnthropicApiJudge.id, ClaudeCliJudge.id)
