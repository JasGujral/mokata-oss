"""F4 — context compression / output-density mode.

A terse-output + tool-output compressor, ON by default since 0.0.22 D3 (HP.S6), toggled via
settings (`settings.governance.output_density`; set it false to turn it off). Compression is lossless-ish structural
trimming: drop trailing whitespace, collapse runs of blank lines, drop consecutive
duplicate lines, and squeeze internal whitespace — cutting tokens on verbose/tool output
without changing its meaning. Dependency-free.
"""

from __future__ import annotations

import re
from typing import Any

GOVERNANCE_SETTINGS_KEY = "governance"
DENSITY_KEY = "output_density"

_WS = re.compile(r"[ \t]+")


def density_enabled(manifest: Any) -> bool:
    """True unless output-density mode is turned OFF (HP.S6, 0.0.22 D3: the default flipped to on —
    the compression is structural and lossless-ish, so the safe default is the cheaper one)."""
    gov = manifest.setting(GOVERNANCE_SETTINGS_KEY, {}) or {}
    return bool(gov.get(DENSITY_KEY, True))


def compress_output(text: str) -> str:
    out = []
    prev = None
    blanks = 0
    for raw in text.splitlines():
        line = _WS.sub(" ", raw.rstrip())
        if line.strip() == "":
            blanks += 1
            if blanks > 1:
                continue          # collapse runs of blank lines to one
            out.append("")
            continue
        blanks = 0
        if line == prev:
            continue              # drop consecutive duplicate lines
        out.append(line)
        prev = line
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out)


def compress_tool_output(text: str, max_lines: int = 40) -> str:
    """Cap noisy tool output to a head/tail with a count of what was elided."""
    lines = compress_output(text).splitlines()
    if len(lines) <= max_lines:
        return "\n".join(lines)
    head = max_lines // 2
    tail = max_lines - head
    elided = len(lines) - max_lines
    return "\n".join(lines[:head] + [f"… [{elided} line(s) elided] …"] + lines[-tail:])


class OutputDensity:
    """Compress only when enabled; otherwise a no-op passthrough (default behaviour)."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def compress(self, text: str) -> str:
        return compress_output(text) if self.enabled else text

    @classmethod
    def from_manifest(cls, manifest: Any) -> "OutputDensity":
        return cls(density_enabled(manifest))
