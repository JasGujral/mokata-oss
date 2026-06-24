"""K3 — per-adapter trust dial.

Each wired tool can be set to a trust level: read-only (cannot write at all),
propose-only (writes must be surfaced for explicit human approval — never auto-approved),
or gated-write (the default: human-gated, may stand-in approve in non-interactive runs).
The level is read from the manifest settings and ENFORCED by the WriteGate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict

READ_ONLY = "read-only"
PROPOSE_ONLY = "propose-only"
GATED_WRITE = "gated-write"
TRUST_LEVELS = (READ_ONLY, PROPOSE_ONLY, GATED_WRITE)
DEFAULT_TRUST = GATED_WRITE
TRUST_SETTINGS_KEY = "trust"


@dataclass
class TrustPolicy:
    levels: Dict[str, str] = field(default_factory=dict)
    default: str = DEFAULT_TRUST

    def level(self, tool: Any) -> str:
        if not tool:
            return self.default
        return self.levels.get(tool, self.default)

    def can_write(self, tool: Any) -> bool:
        return self.level(tool) != READ_ONLY

    def requires_human(self, tool: Any) -> bool:
        return self.level(tool) in (PROPOSE_ONLY, GATED_WRITE)

    def allows_auto_approve(self, tool: Any) -> bool:
        # only gated-write may be auto-approved; propose-only always needs a human.
        return self.level(tool) == GATED_WRITE

    @classmethod
    def from_manifest(cls, manifest: Any) -> "TrustPolicy":
        levels = manifest.setting(TRUST_SETTINGS_KEY, {}) or {}
        return cls(levels=dict(levels))


def trust_for(manifest: Any, tool: str) -> str:
    return (manifest.setting(TRUST_SETTINGS_KEY, {}) or {}).get(tool, DEFAULT_TRUST)
