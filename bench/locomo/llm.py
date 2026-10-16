"""Answerer and judge for LOCOMO — DECLARED and SEPARATE (pin 4). Both are `complete(prompt) -> str`.

  claude-cli     a headless Claude Code call on the operator's own login (no API key); prompt on
                 stdin, empty temp dir, no MCP servers, one turn, API-key env scrubbed.
  anthropic-api  opt-in; reuses G2's judge client, which reads ONLY `MOKATA_G2_JUDGE_API_KEY` and
                 refuses loudly when it is absent.
  fake           tests only.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from typing import Callable, Optional

from bench.g2.agent import scrubbed_env
from bench.g2.judge import AnthropicApiJudge, JudgeUnavailable

REAL_IDS = ("claude-cli", "anthropic-api")


class ClaudeCliCompleter:
    id = "claude-cli"

    def __init__(self, model: Optional[str] = None, claude_bin: str = "claude", timeout_s: int = 600):
        self.model, self.claude_bin, self.timeout_s = model or "", claude_bin, timeout_s

    def complete(self, prompt: str) -> str:
        argv = [self.claude_bin, "-p", "--output-format", "json", "--max-turns", "1",
                "--strict-mcp-config", "--setting-sources", "local"]
        if self.model:
            argv += ["--model", self.model]
        with tempfile.TemporaryDirectory(prefix="locomo-llm-") as empty:
            proc = subprocess.run(argv, input=prompt, cwd=empty, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=self.timeout_s,
                                  env=scrubbed_env())
        try:
            return str(json.loads(proc.stdout).get("result", ""))
        except ValueError:
            return proc.stdout


class ApiCompleter:
    id = "anthropic-api"

    def __init__(self, model: Optional[str]):
        self._client = AnthropicApiJudge.from_env(model)
        self.model = self._client.model

    def complete(self, prompt: str) -> str:
        return self._client.complete(prompt)


class FakeCompleter:
    id = "fake"
    model = "fake"

    def __init__(self, fn: Callable[[str], str]):
        self.fn = fn

    def complete(self, prompt: str) -> str:
        return self.fn(prompt)


def make_completer(kind: str, model: Optional[str]):
    if kind == "claude-cli":
        return ClaudeCliCompleter(model)
    if kind == "anthropic-api":
        return ApiCompleter(model)
    raise JudgeUnavailable(f"unknown completer {kind!r} (claude-cli | anthropic-api)")


def answer_prompt(question: str, context: str) -> str:
    return ("Answer the question using ONLY the conversation memories below. Reply with a short "
            "answer (a few words), or 'unknown' if they do not contain it.\n\n"
            f"## Memories\n{context}\n\n## Question\n{question}\n")


def judge_prompt(question: str, gold: str, answer: str) -> str:
    return ("You are grading an answer to a question about a long conversation. Label it CORRECT "
            "if it conveys the same fact as the gold answer (be generous with wording and date "
            "formats, strict with the fact), else WRONG. Reply with ONE JSON object: "
            '{"label": "CORRECT"|"WRONG"}\n\n'
            f"Question: {question}\nGold answer: {gold}\nGenerated answer: {answer}\n")


def parse_label(raw: str) -> Optional[bool]:
    m = re.search(r"\{.*?\}", raw or "", re.S)
    try:
        label = json.loads(m.group(0))["label"] if m else None
    except (ValueError, KeyError, TypeError):
        label = None
    if label in ("CORRECT", "WRONG"):
        return label == "CORRECT"
    return None                                   # ungradable — counted, never scored
