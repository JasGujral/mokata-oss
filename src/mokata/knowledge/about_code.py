"""GR.S4 — `about_code` write-time validation against the code graph.

A memory write can carry `about_code` anchors (the code symbols/files a decision or rule
CONCERNS, TM.S11a). At write time mokata validates each anchor resolves in the graph and, when
one does not, attaches a PROPOSAL-LEVEL warning — it NEVER blocks and NEVER auto-writes (P2 is
untouched: the warning is proposal metadata the human sees before approving; nothing is written).

Fail-OPEN by design: an anchor is flagged ONLY when the graph AUTHORITATIVELY says it does not
exist (CRG's `not_found`). No adopted graph, no resolve capability (the AST/grep floor), or any
graph hiccup ⇒ the anchor is treated as resolved — a warning is a claim, and mokata never
manufactures a false "this symbol is gone" from a degraded graph.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional


@dataclass
class AboutCodeCheck:
    """The write-time verdict for a memory item's `about_code` anchors (a read-only diagnostic —
    doc 85 §3 `*Check`-style). `unresolved` is the anchors the graph authoritatively rejected;
    `warning` is the human-facing proposal note (empty ⇒ clean). NEVER a block."""

    unresolved: List[str] = field(default_factory=list)
    warning: str = ""


def check_about_code_anchors(anchors: Optional[List[str]], layer: Any) -> AboutCodeCheck:
    """Validate `about_code` anchors resolve in the graph. PROPOSAL-level: returns a warning,
    never raises, never blocks. Fail-OPEN — only a definite, authoritative non-resolution flags."""
    try:
        if not anchors or layer is None:
            return AboutCodeCheck()
        primary = getattr(layer, "primary", None)
        supports = bool(getattr(primary, "supports_resolve", False))
        resolves = getattr(primary, "resolves", None)
        if not (supports and callable(resolves)):
            return AboutCodeCheck()          # no authoritative resolver ⇒ fail-open (no warning)
        unresolved: List[str] = []
        for a in anchors:
            try:
                if not resolves(a):
                    unresolved.append(a)
            except Exception:                # a graph hiccup on ONE anchor ⇒ skip it (fail-open)
                continue
        if not unresolved:
            return AboutCodeCheck()
        warning = (
            "about_code anchor(s) do not resolve in the code graph: "
            f"{', '.join(unresolved)} — verify the symbol name/path. This is a PROPOSAL warning "
            "only; nothing is blocked or written."
        )
        return AboutCodeCheck(unresolved=unresolved, warning=warning)
    except Exception:                        # validation must never break the write path
        return AboutCodeCheck()
