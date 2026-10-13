"""The minimum Claude Code version mokata needs (0.0.22 E1), and the doctor check that says so.

Every enforcement mokata runs inside Claude Code rides a hook event, and those events arrived over
time. The floor below is the OLDEST version on which every one of them exists — derived from the
table, never typed beside it:

    event / field mokata depends on                   since    used by
    Stop, SessionEnd                                   1.0.38   D6 session harvest
    SubagentStop                                       1.0.41   D4 HandbackReport
    PreCompact                                         1.0.48   D1 anchor, D6 harvest
    SessionStart                                       1.0.62   the briefing, D1 re-anchor
    agent_id / agent_transcript_path on SubagentStop   2.0.42   D4
    SubagentStart                                      2.0.43   D4 contract
    PreToolUse/PostToolUse fire INSIDE subagents       2.1.0    D4 read-only enforcement, D5

Sources (checked 2026-10-11): the hook-event history at github.com/shanraisshan/claude-code-hooks;
claudeissues 11726 (agent_id on SubagentStop, 2.0.42). Anthropic's own CHANGELOG.md now keeps only
2.1.286 onwards, so it cannot answer for the older events.

⚠ `last_assistant_message` (Stop / SubagentStop) is NOT in the floor: no source dates it. mokata
treats it as optional — without it the HandbackReport is recorded as the harness's gap (never a
send-back loop) and the session note omits the last word.

Below the floor nothing breaks loudly — the hook that would have fired simply does not — so the
doctor says WHICH features are off, by name, instead of letting them read as working.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from typing import List, Optional, Tuple

Version = Tuple[int, int, int]

#: (feature, since, what mokata loses below it). The floor is derived from this table.
FEATURES: Tuple[Tuple[str, Version, str], ...] = (
    ("Stop / SessionEnd hooks", (1, 0, 38), "session notes are never drafted"),
    ("SubagentStop hook", (1, 0, 41), "subagent handbacks are never read"),
    ("PreCompact hook", (1, 0, 48), "the pre-compaction anchor is never written"),
    ("SessionStart hook", (1, 0, 62), "no briefing and no post-compaction re-anchor"),
    ("agent_id on SubagentStop", (2, 0, 42), "a subagent's contract cannot be matched at stop"),
    ("SubagentStart hook", (2, 0, 43), "subagents get no contract"),
    ("tool hooks inside subagents", (2, 1, 0),
     "a read-only subagent's writes, and its Bash, are not policed"),
)
MIN_VERSION: Version = max(v for _f, v, _l in FEATURES)

_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")
_TIMEOUT_SECS = 3.0


def parse(text: Optional[str]) -> Optional[Version]:
    m = _VERSION_RE.search(text or "")
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def fmt(v: Version) -> str:
    return ".".join(str(x) for x in v)


@lru_cache(maxsize=1)
def installed_version() -> Optional[Version]:
    """`claude --version`, parsed; None when Claude Code is not on PATH or will not answer."""
    exe = shutil.which("claude")
    if not exe:
        return None
    try:
        p = subprocess.run([exe, "--version"], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=_TIMEOUT_SECS, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    return parse(p.stdout) or parse(p.stderr)


@dataclass(frozen=True)
class VersionCheck:
    version: Optional[Version]
    missing: Tuple[Tuple[str, Version, str], ...]

    @property
    def ok(self) -> bool:
        return self.version is None or not self.missing

    def detail(self) -> str:
        lost = "; ".join(f"{f} needs {fmt(v)}: {loss}" for f, v, loss in self.missing)
        return (f"Claude Code {fmt(self.version)} is older than mokata's minimum "
                f"{fmt(MIN_VERSION)} — {lost}. Update Claude Code (`claude update`).")


def check(version: Optional[Version] = None) -> VersionCheck:
    """Which features this Claude Code lacks. No version (not installed) checks nothing: another
    harness is not a Claude Code that is too old."""
    v = version if version is not None else installed_version()
    if v is None:
        return VersionCheck(None, ())
    return VersionCheck(v, tuple(f for f in FEATURES if v < f[1]))


def missing_features(version: Version) -> List[str]:
    return [f for f, since, _l in FEATURES if version < since]


__all__ = ["FEATURES", "MIN_VERSION", "parse", "fmt", "installed_version", "VersionCheck",
           "check", "missing_features"]
