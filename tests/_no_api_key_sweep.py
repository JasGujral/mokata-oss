"""NO-API-KEY-GUARD — the static sweep behind `test_no_api_key_guard.py` (0.0.23 D7, doc 84:1238).

THE RULE (Jas, hard): mokata's PRODUCT — everything under `src/mokata/` (CLI, hooks, MCP server,
IDE, skills) — never needs, reads or spawns anything that needs an LLM API key. It runs on the
user's own Claude login. The G2 eval harness (`bench/`) is the ONE place an API key may be read,
and only for its opt-in judge; it lives outside the wheel, and this sweep is what keeps the
boundary from eroding one convenient import at a time.

WHAT IS SWEPT, AND HOW
  * every `*.py` under `src/mokata/` is PARSED (AST, never regex over source), so a mention in a
    comment or a prose docstring is not a finding and a real read cannot hide behind formatting;
  * every non-Python TEXT file under `src/mokata/` (hooks' shell launchers, the IDE's js/html,
    shipped skill prompts, json) gets a word-boundary text sweep, because a SKILL.md that tells the
    agent to `export ANTHROPIC_API_KEY` is the product needing a key just as surely as an import.

THE RULES (each has a positive control in the test file — a rule nothing can trip is not a rule)
  R1 SDK import        — `import anthropic` / `openai` / `claude_agent_sdk` / `claude_code_sdk`,
                         static or via `importlib.import_module("…")` / `__import__("…")`.
  R2 key env var       — ANY string constant that IS an env-var name ending in `_API_KEY`,
                         `_AUTH_TOKEN` or `_OAUTH_TOKEN`. Deliberately not limited to
                         `os.environ[...]`/`getenv(...)` call shapes: a name held in a constant and
                         read three calls later is the same read, and the AST cannot follow it.
  R3 credential store  — `.credentials.json`, the macOS keychain service `Claude Code-credentials`,
                         or `find-generic-password` (the keychain read).
  R4 headless spawn    — an argv literal `["claude", …, "-p"|"--print"|"--bare", …]` or a command
                         string `claude -p` / `--print` / `--bare`: a `-p` spawn is the Agent-SDK
                         path (it uses an API key UNASKED when one is set), and `--bare` skips the
                         login entirely (`claude --help`, 2.1.308: "Anthropic auth is strictly
                         ANTHROPIC_API_KEY or apiKeyHelper ... OAuth and keychain are never read"). Baseline: none in src/mokata.
  R5 API endpoint      — a constant naming `api.anthropic.com` / `api.openai.com`.
  R6 eval import       — `src/mokata` importing `bench` (the harness may import the product;
                         the product must never import the harness).

THE KNOWN LIMIT, stated: a key name assembled at runtime (`"ANTHROPIC" + "_API_KEY"`) or read
through a fully dynamic `os.environ[name]` whose `name` comes from user config is not visible to
a static sweep. `dsn.py`'s `team.dsn_env` is that shape (a DATABASE DSN, not an LLM key) and is
why R2 keys on the constant, not the call.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

SDK_MODULES = frozenset({"anthropic", "openai", "claude_agent_sdk", "claude_code_sdk"})
KEY_NAME = re.compile(r"[A-Z][A-Z0-9_]*_(?:API_KEY|AUTH_TOKEN|OAUTH_TOKEN)")
KEY_NAME_IN_TEXT = re.compile(r"\b[A-Z][A-Z0-9_]*_(?:API_KEY|AUTH_TOKEN|OAUTH_TOKEN)\b")
CREDENTIAL_MARKERS = (".credentials.json", "claude code-credentials", "find-generic-password")
ENDPOINTS = ("api.anthropic.com", "api.openai.com")
HEADLESS_CMD = re.compile(r"\bclaude\s+(?:-p|--print|--bare)\b")
PRINT_FLAGS = frozenset({"-p", "--print", "--bare"})
EVAL_PACKAGES = frozenset({"bench"})

#: The non-Python file types the text sweep reads. Binary/compiled files are skipped.
TEXT_SUFFIXES = (".sh", ".cmd", ".bat", ".ps1", ".js", ".html", ".css", ".json", ".md", ".txt",
                 ".toml", ".yaml", ".yml", "")


@dataclass(frozen=True)
class Finding:
    rule: str
    path: str      # repo-relative, always `/`-separated (Windows-safe comparison)
    line: int
    detail: str

    def key(self) -> Tuple[str, str, str]:
        return (self.rule, self.path, self.detail)


#: THE ALLOW-LIST. Each entry is (rule, path, detail) -> the REASON it is not a violation.
#: ⛔ Adding an entry is an owner decision (Jas's hard rule), not a builder's convenience; the test
#: also fails on a STALE entry, so an allowance cannot outlive the code it excused.
#: 0.0.26 L4 (pending Jas's ruling): ONE file, the runtime half of this guard. `billing_env.py`
#: names the two variables that would move Claude Code OFF the user's login, to WARN that they are
#: set — presence only, never the value (pinned by `test_l4_billing_env.py` with a sentinel). A
#: key READ anywhere else, or any other key name, is still a violation.
_BILLING_WARNING = ("presence-only: warns that this variable would move Claude Code off the "
                    "user's login (doctor + IDE terminal); never reads out, logs or sends the value")
ALLOW: Dict[Tuple[str, str, str], str] = {
    ("R2", "src/mokata/billing_env.py", "ANTHROPIC_API_KEY"): _BILLING_WARNING,
    ("R2", "src/mokata/billing_env.py", "ANTHROPIC_AUTH_TOKEN"): _BILLING_WARNING,
}


def _const_str(node: ast.AST) -> Optional[str]:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def scan_python(source: str, rel: str) -> List[Finding]:
    """Every finding in one Python source. Raises SyntaxError on unparsable source (the caller
    reports it — an unparsable file is an un-swept file, never a clean one)."""
    tree = ast.parse(source)
    out: List[Finding] = []

    def add(rule: str, node: ast.AST, detail: str) -> None:
        out.append(Finding(rule, rel, getattr(node, "lineno", 0), detail))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in SDK_MODULES:
                    add("R1", node, f"import {alias.name}")
                if root in EVAL_PACKAGES:
                    add("R6", node, f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            root = node.module.split(".")[0]
            if root in SDK_MODULES:
                add("R1", node, f"from {node.module} import")
            if root in EVAL_PACKAGES:
                add("R6", node, f"from {node.module} import")
        elif isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name in ("import_module", "__import__") and node.args:
                mod = _const_str(node.args[0])
                if mod and mod.split(".")[0] in SDK_MODULES:
                    add("R1", node, f"dynamic import {mod}")
                if mod and mod.split(".")[0] in EVAL_PACKAGES:
                    add("R6", node, f"dynamic import {mod}")
        elif isinstance(node, (ast.List, ast.Tuple)):
            elts = [_const_str(e) for e in node.elts]
            if elts and elts[0] and os.path.basename(elts[0]).split(".")[0] == "claude" \
                    and any(e in PRINT_FLAGS for e in elts[1:] if e):
                add("R4", node, "argv claude -p")
        value = _const_str(node)
        if value is not None:
            if KEY_NAME.fullmatch(value.strip()):
                add("R2", node, value.strip())
            low = value.lower()
            for marker in CREDENTIAL_MARKERS:
                if marker in low:
                    add("R3", node, marker)
            if HEADLESS_CMD.search(value):
                add("R4", node, "command claude -p")
            for host in ENDPOINTS:
                if host in low:
                    add("R5", node, host)
    return out


def scan_text(text: str, rel: str) -> List[Finding]:
    out: List[Finding] = []
    for n, line in enumerate(text.splitlines(), 1):
        for m in KEY_NAME_IN_TEXT.finditer(line):
            out.append(Finding("R2", rel, n, m.group(0)))
        low = line.lower()
        for marker in CREDENTIAL_MARKERS:
            if marker in low:
                out.append(Finding("R3", rel, n, marker))
        if HEADLESS_CMD.search(line):
            out.append(Finding("R4", rel, n, "command claude -p"))
        for host in ENDPOINTS:
            if host in low:
                out.append(Finding("R5", rel, n, host))
    return out


def iter_files(root: str) -> Iterable[Tuple[str, str]]:
    """(absolute path, repo-relative `/` path) for every file under `root`, skipping caches."""
    base = os.path.dirname(os.path.dirname(root))   # the repo root (root is <repo>/src/mokata)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            yield full, os.path.relpath(full, base).replace(os.sep, "/")


def sweep(root: str) -> Tuple[List[Finding], Dict[str, int], List[str]]:
    """Sweep a package tree. Returns (findings, counts-by-kind, unreadable-or-unparsable paths)."""
    findings: List[Finding] = []
    counts = {"python": 0, "text": 0, "skipped": 0}
    broken: List[str] = []
    for full, rel in iter_files(root):
        suffix = os.path.splitext(full)[1].lower()
        if suffix == ".py":
            try:
                with open(full, encoding="utf-8") as fh:
                    findings.extend(scan_python(fh.read(), rel))
                counts["python"] += 1
            except (OSError, UnicodeDecodeError, SyntaxError) as exc:
                broken.append(f"{rel}: {exc}")
        elif suffix in TEXT_SUFFIXES:
            try:
                with open(full, encoding="utf-8") as fh:
                    findings.extend(scan_text(fh.read(), rel))
                counts["text"] += 1
            except UnicodeDecodeError:
                counts["skipped"] += 1      # binary with a texty suffix — not a source of reads
            except OSError as exc:
                broken.append(f"{rel}: {exc}")
        else:
            counts["skipped"] += 1
    return findings, counts, broken


def violations(findings: Iterable[Finding]) -> List[Finding]:
    return [f for f in findings if f.key() not in ALLOW]
