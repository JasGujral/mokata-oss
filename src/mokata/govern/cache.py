"""F6 — prompt-cache awareness.

Prompt caches hit only on byte-stable prefixes. This builds a deterministic prefix from
slow-changing, stable sources (manifest identity, the always-on rules, the constitution)
and exposes a check that a prefix is cache-stable across runs (identical fingerprint).
Keep volatile, per-run content OUT of the prefix so the cache keeps hitting.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, List

_SEP = "\n---\n"


def build_stable_prefix(parts: List[str]) -> str:
    """Join stable segments deterministically into a single cache prefix."""
    return _SEP.join(parts)


def prefix_fingerprint(prefix: str) -> str:
    return hashlib.sha256(prefix.encode("utf-8")).hexdigest()


def is_cache_stable(a: str, b: str) -> bool:
    """Two prefixes are cache-stable iff byte-identical (same fingerprint)."""
    return prefix_fingerprint(a) == prefix_fingerprint(b)


@dataclass
class CachePrefix:
    segments: List[str]

    def text(self) -> str:
        return build_stable_prefix(self.segments)

    def fingerprint(self) -> str:
        return prefix_fingerprint(self.text())


def stable_prefix_for(surface: Any) -> CachePrefix:
    """Assemble the cache-stable prefix for a project: manifest identity + the always-on
    rules + the constitution. All slow-changing; no live routing/state included."""
    from .rules import always_on_rules
    manifest = surface.manifest
    segments = [
        f"mokata {manifest.mokata_version} · profile {manifest.profile}",
        "\n".join(always_on_rules().lines),
        surface.constitution.text if getattr(surface, "constitution", None) else "",
    ]
    return CachePrefix(segments=segments)
