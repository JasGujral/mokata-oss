"""GR.S2 (m) — REVIEW's graph-backed VERIFICATION slice (the thin gate, doc-67).

Review is a `uses_graph` CONSUMER, but a THIN one: bounded verification only, never discovery
(discovery stays in develop → DEV-CC 0.0.15). Two halves:

  * pass 2 (live from GR.S2 day one): callers-of-changed-symbols + guards on touched paths.
    A changed symbol whose CALLER lives outside the diff is a silently-breakable caller (P22:
    the review verifies the diff's actual reach against what the human approved). Top-N +
    hop-bounded, sharing the EXISTING review budget — no second channel.

  * pass 1 (DORMANT until AP-SD ships): the diff's ACTUAL blast radius vs the approach's
    DECLARED `about_code` anchors (AP-SD `decisions[]`). Undeclared reach = a divergence
    finding. AP-SD builds AFTER GR.S2, so this half is behind a capability check: it activates
    ONLY when `decisions` is supplied. NAMED HOOK — when AP-SD's `persist_approach` writes
    `decisions[].about_code`, pass `decisions=load_decisions(...)` here and pass 1 lights up.

Graph-absent => `degraded=True` + ZERO findings, so review is byte-identical to today.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional

# Defaults for the bounded verification (shared review budget, not a second channel).
DEFAULT_TOP_N = 25
DEFAULT_MAX_DEPTH = 2


@dataclass
class GraphFinding:
    kind: str                     # "caller-at-risk" | "path-guard" | "undeclared-reach"
    symbol: str
    message: str
    pass_no: int                  # 1 (spec-compliance) or 2 (code-quality)
    detail_path: str = ""


@dataclass
class GraphVerifyResult:
    findings: List[GraphFinding] = field(default_factory=list)
    degraded: bool = False        # True when no real graph answered (loud, not silent)
    checked_symbols: int = 0
    max_depth: int = DEFAULT_MAX_DEPTH

    @property
    def divergences(self) -> List[GraphFinding]:
        return [f for f in self.findings if f.kind == "undeclared-reach"]


def _touched(change: Any) -> set:
    return set(getattr(change, "files", None) or [])


def _symbols(change: Any) -> List[str]:
    return list(getattr(change, "symbols", None) or [])


def graph_verify(
    layer: Any,
    change: Any,
    *,
    top_n: int = DEFAULT_TOP_N,
    max_depth: int = DEFAULT_MAX_DEPTH,
    budget: Optional[int] = None,
    decisions: Optional[List[Any]] = None,
) -> GraphVerifyResult:
    """Verify a change's ACTUAL graph reach. Bounded (top-N symbols, hop-bounded depth, shared
    budget). Returns findings + a loud `degraded` flag when no real graph is wired."""
    if layer is None or not getattr(layer, "uses_graph", False):
        # No real graph -> the thin gate adds nothing; review stays exactly as today (loud).
        return GraphVerifyResult(findings=[], degraded=True, checked_symbols=0,
                                 max_depth=max_depth)

    touched = _touched(change)
    symbols = _symbols(change)[:max(0, top_n)]
    findings: List[GraphFinding] = []
    spent = 0

    def _over_budget() -> bool:
        return budget is not None and spent >= budget

    # --- pass 2: callers-of-changed-symbols + guards on touched paths --------------------
    checked = 0
    for sym in symbols:
        if _over_budget():
            break
        spent += 1
        checked += 1
        try:
            res = layer.callers(sym)
        except Exception:
            continue
        if res is None or getattr(res, "degraded", False):
            continue
        for ref in getattr(res, "references", []) or []:
            path = getattr(ref, "path", "") or ""
            is_test = bool((getattr(ref, "metadata", None) or {}).get("is_test"))
            if path and path not in touched:
                # a caller OUTSIDE the diff that depends on a changed symbol.
                findings.append(GraphFinding(
                    kind="path-guard" if is_test else "caller-at-risk",
                    symbol=sym, detail_path=path,
                    message=(f"'{sym}' is exercised by test {path} (a guard) — verify it still "
                             f"passes" if is_test else
                             f"'{sym}' has a caller in {path} outside the diff — verify it is "
                             f"not silently broken"),
                    pass_no=2))

    # --- pass 1: DORMANT until AP-SD `decisions[]` exists --------------------------------
    if decisions is not None:
        declared: set = set()
        for d in decisions:
            anchors = (d.get("about_code") if isinstance(d, dict)
                       else getattr(d, "about_code", None)) or []
            declared.update(anchors)
        for sym in symbols:
            if _over_budget():
                break
            spent += 1
            try:
                res = layer.blast_radius(sym, depth=max_depth)
            except Exception:
                continue
            if res is None or getattr(res, "degraded", False):
                continue
            for ref in getattr(res, "references", []) or []:
                reached = getattr(ref, "symbol", None) or getattr(ref, "path", "")
                if reached and reached not in declared:
                    findings.append(GraphFinding(
                        kind="undeclared-reach", symbol=sym, detail_path=getattr(ref, "path", ""),
                        message=(f"the diff reaches '{reached}' via '{sym}', which the approved "
                                 f"approach did not declare in about_code"),
                        pass_no=1))

    return GraphVerifyResult(findings=findings, degraded=False, checked_symbols=checked,
                             max_depth=max_depth)
