"""B5 — concept-graph / drift anchors (lat.md-style).

Optional `@lat: <concept>` anchors in code (e.g. `// @lat: caching`, `# @lat: caching`)
tie code to concepts registered in a `lat.md`. `lat_check` flags drift — anchors to
unknown concepts (orphans) and registered concepts with no anchor — and degrades cleanly
when there are no anchors and no registry. Optional; no new required dependency.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import List, Optional

ANCHOR_RE = re.compile(r"(?://|#)\s*@lat:\s*([^\s]+)")
_SCAN_EXTENSIONS = (".py", ".js", ".ts", ".go", ".rb", ".java", ".rs", ".c", ".h")
_CONCEPT_LINE = re.compile(r"^\s*(?:-\s+|#{1,6}\s+)([^\s#].*?)\s*$")


@dataclass
class Anchor:
    path: str
    line: int
    concept: str
    text: str


@dataclass
class DriftFinding:
    kind: str            # "orphan-anchor" | "unanchored-concept"
    concept: str
    detail: str


@dataclass
class LatReport:
    available: bool
    anchors: List[Anchor] = field(default_factory=list)
    drift: List[DriftFinding] = field(default_factory=list)

    @property
    def has_drift(self) -> bool:
        return bool(self.drift)

    @property
    def clean(self) -> bool:
        return not self.drift

    def render(self) -> str:
        if not self.available:
            return "lat check: no anchors or lat.md — drift tracking inactive (clean)."
        if not self.drift:
            return f"lat check: {len(self.anchors)} anchor(s), no drift."
        lines = [f"lat check: {len(self.drift)} drift finding(s):"]
        for f in self.drift:
            lines.append(f"  [{f.kind}] {f.concept} — {f.detail}")
        return "\n".join(lines)


def scan_anchors(root: str) -> List[Anchor]:
    out: List[Anchor] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in filenames:
            if not fn.endswith(_SCAN_EXTENSIONS):
                continue
            path = os.path.join(dirpath, fn)
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    lines = fh.read().splitlines()
            except OSError:
                continue
            for i, line in enumerate(lines, start=1):
                m = ANCHOR_RE.search(line)
                if m:
                    out.append(Anchor(os.path.relpath(path, root), i, m.group(1),
                                      line.strip()))
    return out


def load_concepts(root: str, filename: str = "lat.md") -> List[str]:
    path = os.path.join(root, filename)
    if not os.path.exists(path):
        return []
    concepts: List[str] = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh.read().splitlines():
            m = _CONCEPT_LINE.match(line)
            if m and not line.lstrip().startswith("# concepts"):
                token = m.group(1).split()[0]
                if token.lower() != "concepts":
                    concepts.append(token)
    return concepts


def lat_check(root: str, concepts: Optional[List[str]] = None) -> LatReport:
    anchors = scan_anchors(root)
    registry = concepts if concepts is not None else load_concepts(root)
    available = bool(anchors) or bool(registry)
    if not available:
        return LatReport(available=False)        # degrade cleanly

    reg = set(registry)
    anchored = {a.concept for a in anchors}
    drift: List[DriftFinding] = []
    for a in anchors:
        if reg and a.concept not in reg:
            drift.append(DriftFinding("orphan-anchor", a.concept,
                                      f"{a.path}:{a.line} anchors an unknown concept"))
    for c in reg:
        if c not in anchored:
            drift.append(DriftFinding("unanchored-concept", c,
                                      f"concept '{c}' has no @lat anchor in code"))
    return LatReport(available=True, anchors=anchors, drift=drift)
