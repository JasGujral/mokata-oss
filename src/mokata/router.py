"""A2 — Capability router (with A3 graceful degradation).

The router answers: *given a declared need, which tool should we actually use right
now?* It walks the manifest's declared fallback order for that need and returns the
first tool the `Detector` reports present. Absence is never an error — when the
preferred tool is missing it degrades to the next, and the result records the whole
attempted chain so the choice is auditable (P7: review every decision).

Resolution outcomes:
  - tool present at position 0      -> available, not degraded (preferred choice)
  - tool present at a later position -> available, degraded   (fell back)
  - no tool in the chain present    -> unavailable, degraded  (no provider)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .detect import Detector
from .manifest import Manifest, ManifestError


@dataclass
class Resolution:
    need: str
    tool: Optional[str]                 # chosen tool id, or None if none present
    available: bool                     # a provider was found
    degraded: bool                      # not the preferred provider (or none)
    preferred: Optional[str]            # the declared first choice for this need
    # attempted: ordered (tool_id, present?) pairs the router walked.
    attempted: List[Tuple[str, bool]] = field(default_factory=list)
    reason: str = ""

    def summary(self) -> str:
        if self.available and not self.degraded:
            return f"{self.need} -> {self.tool}"
        if self.available and self.degraded:
            return f"{self.need} -> {self.tool} (degraded from {self.preferred})"
        return f"{self.need} -> UNAVAILABLE (no provider present)"


class Router:
    def __init__(self, manifest: Manifest, detector: Optional[Detector] = None) -> None:
        self.manifest = manifest
        self.detector = detector or Detector()

    def resolve(self, need: str) -> Resolution:
        order = self.manifest.fallback_order(need)  # raises ManifestError if unknown
        preferred = order[0] if order else None
        attempted: List[Tuple[str, bool]] = []

        # K1: a capability whose owning layer is disabled is not routable at all.
        if not self.manifest.capability_enabled(need):
            layer = self.manifest.capability_layer(need)
            return Resolution(
                need=need,
                tool=None,
                available=False,
                degraded=True,
                preferred=preferred,
                attempted=attempted,
                reason=f"capability's layer '{layer}' is disabled",
            )

        for index, tool_id in enumerate(order):
            tool_def = self.manifest.tools.get(tool_id, {})
            # K1: a disabled tool is treated as absent so the router degrades past it.
            present = self.manifest.tool_enabled(tool_id) and self.detector.is_present(
                tool_id, tool_def
            )
            attempted.append((tool_id, present))
            if present:
                degraded = index > 0
                reason = (
                    "preferred provider available"
                    if not degraded
                    else f"preferred '{preferred}' absent; using fallback '{tool_id}'"
                )
                return Resolution(
                    need=need,
                    tool=tool_id,
                    available=True,
                    degraded=degraded,
                    preferred=preferred,
                    attempted=attempted,
                    reason=reason,
                )

        return Resolution(
            need=need,
            tool=None,
            available=False,
            degraded=True,
            preferred=preferred,
            attempted=attempted,
            reason="no declared provider is present on this machine",
        )

    def resolve_all(self) -> List[Resolution]:
        """Resolve every *enabled* capability (stable, declaration order). Capabilities
        whose owning layer is disabled are dropped entirely (K1), not surfaced as
        unavailable."""
        return [self.resolve(need) for need in self.manifest.enabled_capabilities()]

    def has(self, need: str) -> bool:
        try:
            return self.resolve(need).available
        except ManifestError:
            return False
