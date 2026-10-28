#!/usr/bin/env python3
"""0.0.26 L6 (SA.S1) — the per-PR security lints over what mokata SHIPS.

mokata ships hooks + an MCP server + skills: the maximum-privilege plugin shape. What a user
installs is text a model will read and act on, so it is linted like code. Six lints, each a pure
function over the tree (``--root``), each proven by a planted-bad fixture in
``tests/test_l6_security_lints.py``:

  shell-args         ``$ARGUMENTS`` / positional args spliced UNQUOTED into a shell command in a
                     SKILL.md or command template (fenced ``bash``/``sh``/``shell``/``zsh`` blocks
                     and Claude Code's ``!`cmd``` lines), or handed to ``eval`` / ``sh -c``
                     even quoted (the string is re-parsed as shell).
  injection-markers  prompt-injection markers in shipped skills, templates and the plugin
                     manifest: instruction overrides ("ignore previous instructions"), chat-role
                     tokens, "don't tell the user", pipe-to-shell downloads, hidden characters
                     (zero-width, bidi controls, Unicode tag characters) and HTML comments (text
                     a reviewer never sees rendered but the model reads).
  shipped-secrets    mokata's OWN 4-layer scanner (``govern.secrets``) over every shipped text
                     file (``src/``, ``.claude-plugin/``, the root docs).
  agentskills        agentskills.io conformance of every ``skills/<dir>/SKILL.md``: ``name`` ==
                     the directory, lowercase-hyphen, ≤ 64 chars, no reserved word; a
                     ``description`` ≤ 1024 chars that is a TRIGGER (what it does / when) — no
                     tags, URLs or instructions aimed at the model.
  mcp-pin            every MCP server that runs mokata (``.claude-plugin/plugin.json`` and any
                     shipped ``.mcp.json``) pins the EXACT version in ``pyproject.toml``.
  version-lockstep   ``pyproject.toml`` == ``plugin.json`` == ``marketplace.json`` == the package
                     ``__version__`` == the MCP pin == the published action pins
                     (``packaging.check_release_consistency``, the release gate's own reader).

Exit codes: 0 clean · 1 findings · 2 could not run (a missing file, an unknown lint name).

EXEMPTIONS live HERE, in reviewable code, keyed to the exact text — never in the linted file
(the ``skill_lints`` anti-self-exempt rule). An exemption whose text changed stops applying.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Tuple

LINTS = ("shell-args", "injection-markers", "shipped-secrets", "agentskills", "mcp-pin",
         "version-lockstep")

EXIT_CLEAN, EXIT_FINDINGS, EXIT_CANNOT_RUN = 0, 1, 2


@dataclass(frozen=True)
class Finding:
    lint: str
    path: str
    line: int
    message: str
    excerpt: str = ""

    def render(self) -> str:
        loc = f"{self.path}:{self.line}" if self.line else self.path
        return f"{self.lint}: {loc}: {self.message}"


class CannotRun(Exception):
    """A lint could not read what it must read — exit 2, never a silent pass."""


def _fp(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]


#: (lint, path, fingerprint of the offending line/excerpt) -> WHY it is not a finding.
EXEMPTIONS: Dict[Tuple[str, str, str], str] = {
    # The secret scanner's own source documents the AWS documentation example key (reversed, so the
    # source itself is not the literal) — the scanner must recognise that shape to block it.
    ("shipped-secrets", "src/mokata/govern/secrets.py", "aws-example"):
        "the scanner's own documented AWS example-key shape (reversed in source)",
    ("shipped-secrets", "src/mokata/ide/approve_codes.py", "alphabet"):
        "the one-time approval code's character ALPHABET, not a credential",
    ("shipped-secrets", "src/mokata/session_index.py", "alphabet"):
        "a hex/base-36 digit alphabet, not a credential",
}


def _exempt(f: Finding) -> bool:
    return any(k[0] == f.lint and k[1] == f.path and k[2] in (f.excerpt, _fp(f.excerpt))
               for k in EXEMPTIONS)


# ------------------------------------------------------------------------------ the shipped set

def _tracked(root: str, *paths: str) -> List[str]:
    """The git-tracked files under `paths` (relative, '/'-separated); a non-git tree falls back to
    a walk, so a fixture directory is linted the same way."""
    try:
        # --cached + --others: what is on disk and would ship, tracked or new; a tracked path
        # the working tree has deleted (an uncommitted `rm`) is not on disk, so it is not linted
        # (O16: Jas's dev tree after a patch that deleted a template crashed the lint).
        out = subprocess.run(["git", "-C", root, "ls-files", "-z", "--cached", "--others",
                              "--exclude-standard", "--", *paths],
                             capture_output=True, stdin=subprocess.DEVNULL, timeout=60)
        if out.returncode == 0 and out.stdout:
            return sorted({p for p in out.stdout.decode("utf-8", "replace").split("\0")
                           if p and os.path.isfile(os.path.join(root, p))})
    except (OSError, subprocess.SubprocessError):
        pass
    found: List[str] = []
    for base in paths:
        full = os.path.join(root, base)
        if os.path.isfile(full):
            found.append(base.replace(os.sep, "/"))
            continue
        for dirpath, dirnames, filenames in os.walk(full):
            dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".git")]
            for name in filenames:
                found.append(os.path.relpath(os.path.join(dirpath, name), root).replace(os.sep, "/"))
    return sorted(found)


def _read(root: str, rel: str) -> Optional[str]:
    try:
        with open(os.path.join(root, rel), "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        return None


SKILLS_DIR = "src/mokata/skills"
TEMPLATES_DIR = "src/mokata/templates"
PLUGIN_DIR = ".claude-plugin"


def _markdown_surfaces(root: str) -> List[str]:
    return [p for p in _tracked(root, SKILLS_DIR, TEMPLATES_DIR) if p.endswith(".md")]


# ------------------------------------------------------------------------------ 1 · shell-args

_FENCE = re.compile(r"^\s*(```+|~~~+)\s*([A-Za-z0-9_+-]*)")
_SHELL_LANGS = {"bash", "sh", "shell", "zsh", "console", "shell-session"}
_BANG = re.compile(r"!`([^`]*)`")
_USER_ARG = re.compile(r"\$\{?(ARGUMENTS|[1-9]|@|\*)\b\}?|\$(?:[1-9@*])")
_RESHELL = re.compile(r"\b(eval|(?:ba|z|da)?sh\s+-c|xargs\s+(?:-\S+\s+)*(?:ba)?sh)\b")


def _shell_snippets(text: str) -> Iterable[Tuple[int, str]]:
    """(line number, shell text) for every shell context in a markdown file."""
    in_fence, lang, close = False, "", ""
    for n, line in enumerate(text.splitlines(), 1):
        m = _FENCE.match(line)
        if m and not in_fence:
            in_fence, close, lang = True, m.group(1)[0] * 3, (m.group(2) or "").lower()
            continue
        if in_fence and line.strip().startswith(close):
            in_fence = False
            continue
        if in_fence and lang in _SHELL_LANGS:
            yield n, line
        elif not in_fence:
            for b in _BANG.finditer(line):
                yield n, b.group(1)


#: A `<placeholder>` the model fills with the user's (or the repo's) text.
_PLACEHOLDER = re.compile(r"<[a-z][a-z0-9_.-]*>")


def _unquoted_arg_spans(cmd: str) -> List[str]:
    """User-argument expansions OUTSIDE double quotes (single quotes do not expand at all), and
    `<placeholders>` that are not SINGLE-quoted — the model fills them with user or repo text, and
    a bare or double-quoted slot still expands `$(…)`. A shell comment ends the scan."""
    out, i, quote = [], 0, ""
    while i < len(cmd):
        c = cmd[i]
        if c == "\\" and quote != "'":
            i += 2
            continue
        if quote == "'":
            if c == "'":
                quote = ""
            i += 1
            continue
        if c == "#" and not quote and (i == 0 or cmd[i - 1] in " \t"):
            break                                   # the rest is a shell comment
        if quote == '"' and c == '"':
            quote = ""
            i += 1
            continue
        if not quote and c in "\"'":
            quote = c
            i += 1
            continue
        m = _PLACEHOLDER.match(cmd, i)
        if m:
            out.append(m.group(0))
            i = m.end()
            continue
        m = _USER_ARG.match(cmd, i) if not quote else None
        if m:
            out.append(m.group(0))
            i = m.end()
            continue
        i += 1
    return out


def lint_shell_args(root: str) -> List[Finding]:
    found = []
    for rel in _markdown_surfaces(root):
        text = _read(root, rel)
        if text is None:
            raise CannotRun(f"cannot read {rel}")
        for n, cmd in _shell_snippets(text):
            for arg in _unquoted_arg_spans(cmd):
                fix = ("single-quote the slot ('" + arg + "') — the model fills it with user or "
                       "repo text") if arg.startswith("<") else \
                      "quote it (\"$ARGUMENTS\") so it cannot split, glob or inject"
                found.append(Finding("shell-args", rel, n,
                                     f"user-supplied {arg} reaches a shell command unquoted — {fix}",
                                     cmd.strip()))
            if (_USER_ARG.search(cmd) or _PLACEHOLDER.search(cmd)) and _RESHELL.search(cmd):
                found.append(Finding("shell-args", rel, n,
                                     "user argument handed to eval / sh -c — the string is "
                                     "re-parsed as shell, so quoting does not protect it",
                                     cmd.strip()))
    return found


# ------------------------------------------------------------------------------ 2 · injection-markers

_INJECTION = (
    (re.compile(r"\b(ignore|disregard|forget|override)\s+(all\s+|any\s+|the\s+|your\s+)*"
                r"(previous|prior|above|earlier|preceding|system|original)\s+"
                r"(instructions?|prompts?|messages?|rules|directions)", re.I),
     "an instruction-override phrase"),
    (re.compile(r"<\|im_(start|end)\|>|\[/?INST\]|<<\s*/?SYS\s*>>|^\s*(system|assistant)\s*:\s",
                re.I | re.M),
     "a chat-role / prompt-template token"),
    (re.compile(r"\b(do\s+not|don't|never)\s+(tell|inform|show|reveal\s+to|mention\s+(this\s+)?to)"
                r"\s+the\s+user\b|\bwithout\s+(telling|informing)\s+the\s+user\b", re.I),
     "concealment from the user"),
    (re.compile(r"\byou\s+are\s+now\s+(?:dan|in\s+developer\s+mode|unrestricted|jailbroken)\b",
                re.I),
     "a jailbreak persona switch"),
    (re.compile(r"\b(curl|wget)\b[^\n|]*\|\s*(sudo\s+)?(ba|z)?sh\b|base64\s+(-d|--decode)[^\n]*\|"
                r"\s*(ba)?sh\b", re.I),
     "a download piped straight into a shell"),
)
_HIDDEN = re.compile("[​-‏‪-‮⁠-⁤⁦-⁩﻿]"
                     "|[\U000e0000-\U000e007f]")
_HTML_COMMENT = re.compile(r"<!--(.*?)-->", re.S)
#: mokata's OWN render markers (exact text): replaced at build time, carry no instruction.
KNOWN_MARKERS = frozenset({"<!-- mokata:grounding -->"})


def lint_injection_markers(root: str) -> List[Finding]:
    found = []
    rels = [p for p in _tracked(root, SKILLS_DIR, TEMPLATES_DIR, PLUGIN_DIR)
            if p.endswith((".md", ".json", ".txt", ".yml", ".yaml"))]
    for rel in rels:
        text = _read(root, rel)
        if text is None:
            raise CannotRun(f"cannot read {rel}")
        lines = text.splitlines()
        for n, line in enumerate(lines, 1):
            for rx, what in _INJECTION:
                if rx.search(line):
                    found.append(Finding("injection-markers", rel, n,
                                         f"{what} in shipped model-facing text", line.strip()))
            h = _HIDDEN.search(line if not (n == 1 and line.startswith("﻿"))
                               else line[1:])
            if h:
                found.append(Finding("injection-markers", rel, n,
                                     f"a hidden character U+{ord(h.group(0)):04X} (invisible to a "
                                     "reviewer, read by the model)", line.strip()))
        if rel.endswith(".md"):
            for m in _HTML_COMMENT.finditer(text):
                if m.group(0) in KNOWN_MARKERS:
                    continue
                n = text.count("\n", 0, m.start()) + 1
                found.append(Finding("injection-markers", rel, n,
                                     "an HTML comment — hidden when rendered, read by the model",
                                     m.group(0)[:200]))
    return found


# ------------------------------------------------------------------------------ 3 · shipped-secrets

_SHIPPED = ("src", PLUGIN_DIR, "pyproject.toml", "README.md", "SECURITY.md", "PRIVACY.md",
            "llms.txt", "NOTICE")
_BINARY = (".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".ttf", ".otf", ".pdf",
           ".zip", ".gz", ".pyc", ".db", ".sqlite")


def _secret_excerpt(rel: str, f) -> str:
    """A STABLE handle for an exemption: never the literal (it would put the secret in this file)."""
    if "secrets.py" in rel and f.kind == "known-secret-shape" or "ELPMAXE" in (f.token or ""):
        return "aws-example"
    tok = f.token or ""
    if tok and (tok in "ABCDEFGHJKMNPQRSTUVWXYZ23456789" or tok.startswith("0123456789")
                or "ABCDEFGHJK" in tok):
        return "alphabet"
    return _fp(f"{f.kind}:{f.line}:{tok}")


#: mokata's own source next to this script — the scanner and the release reader come from HERE,
#: so a fixture tree under `--root` is linted by the same code that lints the repo.
_OWN_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")


def _own_mokata() -> None:
    if _OWN_SRC not in sys.path:
        sys.path.insert(0, _OWN_SRC)


def lint_shipped_secrets(root: str) -> List[Finding]:
    _own_mokata()
    try:
        from mokata.govern import secrets as S
    except ImportError as exc:
        raise CannotRun(f"mokata's secret scanner is not importable: {exc}") from exc
    found = []
    for rel in _tracked(root, *_SHIPPED):
        if rel.endswith(_BINARY):
            continue
        text = _read(root, rel)
        if text is None:
            continue                       # a binary asset: no text to scan
        for f in S.scan(text, path=rel):
            found.append(Finding("shipped-secrets", rel, getattr(f, "line", 0) or 0,
                                 f"{f.layer}/{f.kind} — a credential-shaped string in a SHIPPED "
                                 "file", _secret_excerpt(rel, f)))
    return found


# ------------------------------------------------------------------------------ 4 · agentskills

_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_RESERVED = ("anthropic", "claude")
_NOT_A_TRIGGER = re.compile(
    r"<[a-z/!][^>]*>|https?://|`|\b(you\s+must|ignore|disregard|system\s+prompt|always\s+(run|call|"
    r"use|invoke|execute)|never\s+(tell|ask|mention)|before\s+(any|every)\s+(other\s+)?(tool|reply|"
    r"response)|instead\s+of\s+(the\s+user|following))\b", re.I)


def _frontmatter(text: str) -> Optional[Dict[str, str]]:
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end < 0:
        return None
    out: Dict[str, str] = {}
    key = None
    for raw in text[3:end].splitlines():
        if not raw.strip():
            continue
        if raw[:1] in (" ", "\t") and key:
            out[key] = (out[key] + " " + raw.strip()).strip()
            continue
        k, sep, v = raw.partition(":")
        if not sep:
            continue
        key = k.strip()
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[key] = "" if v in (">", "|", ">-", "|-") else v
    return out


def lint_agentskills(root: str) -> List[Finding]:
    found = []
    skill_files = [p for p in _tracked(root, SKILLS_DIR) if p.endswith("/SKILL.md")]
    if not skill_files:
        raise CannotRun(f"no {SKILLS_DIR}/*/SKILL.md found")
    for rel in skill_files:
        parts = rel.split("/")
        if len(parts) != len(SKILLS_DIR.split("/")) + 2:
            continue                        # a nested SKILL.md is not a top-level skill
        dirname = parts[-2]
        fm = _frontmatter(_read(root, rel) or "")

        def bad(msg: str, excerpt: str = "") -> None:
            found.append(Finding("agentskills", rel, 1, msg, excerpt))
        if fm is None:
            bad("no YAML frontmatter")
            continue
        name, desc = fm.get("name", ""), fm.get("description", "")
        if name != dirname:
            bad(f"name {name!r} != its directory {dirname!r}", name)
        if not _NAME.match(name or "-"):
            bad(f"name {name!r} is not lowercase letters/digits/single hyphens", name)
        if len(name) > 64:
            bad(f"name is {len(name)} chars (max 64)", name)
        if any(r in name for r in _RESERVED):
            bad(f"name {name!r} uses a reserved word", name)
        if not desc.strip():
            bad("description is empty")
        if len(desc) > 1024:
            bad(f"description is {len(desc)} chars (max 1024)", desc[:80])
        m = _NOT_A_TRIGGER.search(desc)
        if m:
            bad(f"description is not trigger-only (contains {m.group(0)!r}) — it is shown to the "
                "model before the skill is chosen, so it must say what the skill does / when, never "
                "instruct", desc[:120])
    return found


# ------------------------------------------------------------------------------ 5 · mcp-pin

def _pyproject_version(root: str) -> str:
    text = _read(root, "pyproject.toml")
    m = re.search(r'(?m)^\s*version\s*=\s*"([^"]+)"', text or "")
    if not m:
        raise CannotRun("pyproject.toml has no version")
    return m.group(1)


_PIN = re.compile(r"^mokata(\[[^\]]*\])?==(\S+)$")


def _server_findings(rel: str, servers: dict, version: str) -> List[Finding]:
    out = []
    for name, spec in (servers or {}).items():
        if not isinstance(spec, dict):
            continue
        argv = [str(spec.get("command", ""))] + [str(a) for a in spec.get("args", []) or []]
        if not any("mokata" in a for a in argv):
            continue                        # not a mokata server
        pins = [a for a in argv if a.startswith("mokata") and ("==" in a or "@" in a
                                                                or a.startswith("mokata["))]
        exact = [p for p in pins if _PIN.match(p)]
        if not exact:
            out.append(Finding("mcp-pin", rel, 0,
                               f"MCP server {name!r} runs mokata unpinned ({' '.join(argv)}) — "
                               f"pin the exact version: mokata=={version}", " ".join(argv)))
            continue
        got = _PIN.match(exact[0]).group(2)
        if got != version:
            out.append(Finding("mcp-pin", rel, 0,
                               f"MCP server {name!r} pins mokata=={got}, pyproject.toml is "
                               f"{version}", exact[0]))
    return out


def lint_mcp_pin(root: str) -> List[Finding]:
    version = _pyproject_version(root)
    found = []
    plugin = os.path.join(PLUGIN_DIR, "plugin.json")
    text = _read(root, plugin)
    if text is None:
        raise CannotRun(f"cannot read {plugin}")
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise CannotRun(f"{plugin} is not JSON: {exc}") from exc
    servers = data.get("mcpServers")
    if isinstance(servers, str):            # a path to a separate .mcp.json
        rel = os.path.normpath(os.path.join(PLUGIN_DIR, "..", servers)).replace(os.sep, "/")
        try:
            servers = json.loads(_read(root, rel) or "{}").get("mcpServers", {})
        except ValueError as exc:
            raise CannotRun(f"{rel} is not JSON: {exc}") from exc
    found += _server_findings(plugin, servers if isinstance(servers, dict) else {}, version)
    for rel in [p for p in _tracked(root, ".") if p.endswith(".mcp.json")
                and not p.startswith("tests/")]:
        try:
            data = json.loads(_read(root, rel) or "{}")
        except ValueError as exc:
            raise CannotRun(f"{rel} is not JSON: {exc}") from exc
        found += _server_findings(rel, data.get("mcpServers", {}), version)
    return found


# ------------------------------------------------------------------------------ 6 · version-lockstep

def lint_version_lockstep(root: str) -> List[Finding]:
    version = _pyproject_version(root)
    _own_mokata()
    try:
        from mokata.packaging import check_release_consistency
    except ImportError as exc:
        raise CannotRun(f"mokata.packaging is not importable: {exc}") from exc
    res = check_release_consistency(version, root)
    return [Finding("version-lockstep", m.split(":")[0] if isinstance(m, str) else str(m), 0,
                    f"{m} — every version-bearing field must equal pyproject.toml ({version})")
            for m in res.mismatches]


# ------------------------------------------------------------------------------ the runner

_RUNNERS: Dict[str, Callable[[str], List[Finding]]] = {
    "shell-args": lint_shell_args,
    "injection-markers": lint_injection_markers,
    "shipped-secrets": lint_shipped_secrets,
    "agentskills": lint_agentskills,
    "mcp-pin": lint_mcp_pin,
    "version-lockstep": lint_version_lockstep,
}


def run(root: str, only: Optional[Iterable[str]] = None) -> List[Finding]:
    names = list(only) if only else list(LINTS)
    unknown = [n for n in names if n not in _RUNNERS]
    if unknown:
        raise CannotRun(f"unknown lint(s): {', '.join(unknown)} (known: {', '.join(LINTS)})")
    out: List[Finding] = []
    for name in names:
        out += [f for f in _RUNNERS[name](root) if not _exempt(f)]
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="mokata's per-PR security lints (SA.S1).")
    ap.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--only", default="", help="comma-separated lint names")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        findings = run(args.root, [n for n in args.only.split(",") if n] or None)
    except CannotRun as exc:
        print(f"security-lint: cannot run — {exc}", file=sys.stderr)
        return EXIT_CANNOT_RUN
    if args.json:
        print(json.dumps([f.__dict__ for f in findings], indent=2))
    else:
        for f in findings:
            print(f.render())
        print(f"security-lint: {len(findings)} finding(s) across {len(args.only.split(',')) if args.only else len(LINTS)} lint(s)")
    return EXIT_FINDINGS if findings else EXIT_CLEAN


if __name__ == "__main__":
    raise SystemExit(main())
