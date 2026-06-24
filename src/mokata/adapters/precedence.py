"""H6 — conflict / overlap resolution.

When two tools claim the same role, the manifest's declared precedence (the capability's
fallback order, A2) resolves it. This surfaces overlaps and the precedence; the existing
router already honors it deterministically (it walks the fallback order and picks the
first present provider) — there is no parallel resolution path.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set


def declared_precedence(manifest: Any, need: str) -> List[str]:
    """The precedence order for a capability — i.e. its declared fallback order."""
    return manifest.fallback_order(need)        # raises ManifestError if unknown


def overlapping_capabilities(manifest: Any) -> Dict[str, List[str]]:
    """Capabilities claimed by more than one provider (an overlap to be resolved by
    precedence)."""
    out: Dict[str, List[str]] = {}
    for need in manifest.capabilities:
        order = manifest.fallback_order(need)
        if len(order) > 1:
            out[need] = order
    return out


def resolve_conflict(manifest: Any, need: str,
                     present: Set[str]) -> Optional[str]:
    """The winning provider for a need: the highest-precedence one that is present."""
    for tool in declared_precedence(manifest, need):
        if tool in present:
            return tool
    return None
