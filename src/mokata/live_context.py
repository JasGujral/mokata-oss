"""HP.S1 (0.0.22 D2) — live repo context in the briefing: where the working tree is NOW.

The briefing told a session which gates and rules apply; it did not tell it the state of the repo it
had just opened. HP.S1's four facts, each one line, ≤ `MAX_LINES` in all:

  * GIT — branch, upstream ahead/behind, and how many files are modified / untracked. One bounded
    `git status --porcelain=v1 --branch` call (SessionStart must stay fast); absent when this is
    not a git checkout or git cannot answer in time.
  * TESTS — the project's test command, from `settings.baseline.test_command` (mokata never guesses
    one, so an unset command SAYS it is unset rather than vanishing).
  * INSTRUCTIONS — pointers to the instruction files actually present (CLAUDE.md, AGENTS.md,
    `.claude/rules/`), never their contents.
  * STRUCTURE — the GR.S4 graph-derived line, unchanged, now inside this block.

⚠ "DROPS FIRST UNDER BUDGET" (the row's own words): these lines are the most recoverable thing in
the briefing — any of them is one command away — so when the briefing is over budget the whole
block is dropped BEFORE the defensive truncation cuts the gates or the rules, and the drop is said.

Never raises; every probe that fails is omitted, and the block is omitted when nothing is known.
"""

from __future__ import annotations

import os
from typing import Any, List, Optional

#: The row's cap.
MAX_LINES = 6
#: Bound on the one git call. SessionStart is async, but the first response waits for it.
GIT_TIMEOUT_SECS = 1.5
HEADER = "Repo now (live):"
DROPPED_NOTICE = "[live repo context dropped to fit the token budget — run `git status`]"

_INSTRUCTION_FILES = ("CLAUDE.md", "AGENTS.md", os.path.join(".claude", "CLAUDE.md"))


def git_line(root: str, timeout: float = GIT_TIMEOUT_SECS) -> Optional[str]:
    """`git: <branch> (ahead N, behind M) · K modified · U untracked`, or None."""
    import subprocess          # O12b: only the background refresher / `mokata bootstrap` runs git
    try:
        p = subprocess.run(["git", "-C", root, "status", "--porcelain=v1", "--branch"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode != 0 or not p.stdout:
        return None
    lines = p.stdout.splitlines()
    head = lines[0][3:] if lines and lines[0].startswith("## ") else ""
    branch, _, track = head.partition("...")
    branch = branch.strip() or "(detached)"
    if branch.startswith("No commits yet on "):
        branch = branch[len("No commits yet on "):] + " (no commits yet)"
    extra = ""
    if "[" in track and "]" in track:
        extra = " (" + track[track.index("[") + 1:track.index("]")] + ")"
    body = lines[1:] if head else lines
    untracked = sum(1 for ln in body if ln.startswith("??"))
    modified = sum(1 for ln in body if ln and not ln.startswith("??"))
    state = "clean" if not body else f"{modified} modified · {untracked} untracked"
    return f"- git: {branch}{extra} · {state}"


def test_line(manifest) -> str:
    from .baseline import baseline_command
    cmd = baseline_command(manifest)
    if cmd:
        return f"- tests: `{cmd}`"
    return "- tests: no test command set (`settings.baseline.test_command`) — mokata will not guess"


def instruction_line(root: str) -> Optional[str]:
    found = [f for f in _INSTRUCTION_FILES if os.path.isfile(os.path.join(root, f))]
    rules = os.path.join(root, ".claude", "rules")
    if os.path.isdir(rules):
        found.append(os.path.join(".claude", "rules") + os.sep)
    if not found:
        return None
    return "- instructions: " + ", ".join(found) + " (read before non-trivial work)"


_ASK_GIT = object()


def live_context_lines(surface, structure_line: Optional[str] = None,
                       max_lines: int = MAX_LINES, git: Any = _ASK_GIT) -> List[str]:
    """The block's lines (header first), or [] when nothing is known. ≤ `max_lines` incl. header
    (`settings.context.live_lines`; 0 turns the block off). `git` is the git line already in hand
    (0.0.26 O12b: the SessionStart hook passes the background-refreshed one, so it runs no `git`)."""
    if max_lines <= 1:
        return []
    body: List[str] = []
    try:
        for line in (git_line(surface.root) if git is _ASK_GIT else git,
                     test_line(surface.manifest),
                     instruction_line(surface.root)):
            if line:
                body.append(line)
    except Exception:  # noqa: BLE001 — a probe that fails is omitted, never a broken briefing
        pass
    if structure_line:
        body.append("- " + structure_line.lstrip("- "))
    if not body:
        return []
    return [HEADER] + body[:max_lines - 1]


__all__ = ["MAX_LINES", "HEADER", "DROPPED_NOTICE", "git_line", "test_line", "instruction_line",
           "live_context_lines"]
