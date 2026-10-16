"""0.0.23 D8 — EV-GATE (doc 84), scope (a)–(c): gate outcomes carry TYPED EVIDENCE, not just verdicts.

  (a) `artifacts[]` — typed `path` | `url` | `content` items per gate outcome, persisted under the
      managed `evidence/<run_id>/` directory;
  (b) `tested[]` — the explicit inventory of checks the gate ACTUALLY exercised, each with a status
      and the acceptance criteria it speaks to (audits gate coverage — a gate that says "pass" while
      exercising nothing is now visible as exactly that);
  (c) "evidence covers the stated ACs" is itself a FINDING (`evidence_covers_acs`): an AC no
      exercised check names is UNCOVERED; one whose checks were exercised but none passed is UNPROVEN.

Out of scope here, deliberately: (d) rendering evidence into a PR body and (e)/(f) CI signatures —
those ride G3 → 0.0.25.

Trust tier: OBSERVABILITY. Like the audit ledger and `progress_events`, the evidence log is
append-only and UNGATED — it records what a gate saw; it is not a durable code/memory/config write,
so it never routes through the WriteGate. It lives under `.mokata/temp_local/evidence/<run_id>/`
(beside the state store, gitignored with the rest of temp_local). Degrade-clean: recording never
raises (a failure returns None) and an absent/corrupt log reads as empty.

Windows-safe: the run id becomes a directory name only after it is reduced to `[A-Za-z0-9._-]`;
anything else is replaced, so a run id can never name a path outside the evidence root.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# ----------------------------------------------------------------------------------- vocabulary
PATH = "path"
URL = "url"
CONTENT = "content"
ARTIFACT_TYPES = (PATH, URL, CONTENT)

PASS = "pass"
FAIL = "fail"
SKIP = "skip"
ERROR = "error"
TESTED_STATUSES = (PASS, FAIL, SKIP, ERROR)
#: A check was EXERCISED when it produced a verdict. A skip ran nothing; an error produced none.
EXERCISED_STATUSES = (PASS, FAIL)

#: The finding id for (c), so a surface can name it the way it names a gate.
COVERAGE_FINDING_ID = "evidence-covers-acs"

EVIDENCE_DIRNAME = "evidence"
EVIDENCE_FILENAME = "evidence.jsonl"
EVIDENCE_SCHEMA_VERSION = 1
#: `content` artifacts are capped so one gate outcome cannot bloat the log (P11 frugality).
CONTENT_CAP = 4000

_SAFE = re.compile(r"[^A-Za-z0-9._-]")


# ----------------------------------------------------------------------------------- the types
@dataclass(frozen=True)
class Artifact:
    """One typed artifact. `ref` is a filesystem path (`path`), a URL (`url`), or inline text
    (`content`, capped at CONTENT_CAP with the truncation stated)."""

    type: str
    ref: str
    label: str = ""

    def __post_init__(self) -> None:
        if self.type not in ARTIFACT_TYPES:
            raise ValueError(f"artifact type {self.type!r} is not one of {ARTIFACT_TYPES}")
        if not isinstance(self.ref, str) or not self.ref:
            raise ValueError("an artifact needs a non-empty ref")
        if self.type == URL and not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", self.ref):
            raise ValueError(f"a url artifact needs a scheme://, got {self.ref!r}")
        if self.type == CONTENT and len(self.ref) > CONTENT_CAP:
            object.__setattr__(self, "ref", self.ref[:CONTENT_CAP] + "\n…[truncated]")

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.type, "ref": self.ref, "label": self.label}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Artifact":
        return cls(str(d.get("type", "")), str(d.get("ref", "")), str(d.get("label", "") or ""))


@dataclass(frozen=True)
class TestedCheck:
    """One check the gate exercised (or explicitly did not: `skip`/`error`), and the ACs it is about."""

    __test__ = False   # not a unittest/pytest class despite the name

    check_id: str
    status: str
    ac_ids: Tuple[str, ...] = ()
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.check_id:
            raise ValueError("a tested check needs a check_id")
        if self.status not in TESTED_STATUSES:
            raise ValueError(f"check status {self.status!r} is not one of {TESTED_STATUSES}")
        object.__setattr__(self, "ac_ids", tuple(str(a) for a in self.ac_ids))

    @property
    def exercised(self) -> bool:
        return self.status in EXERCISED_STATUSES

    def to_dict(self) -> Dict[str, Any]:
        return {"check_id": self.check_id, "status": self.status, "ac_ids": list(self.ac_ids),
                "detail": self.detail}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TestedCheck":
        return cls(str(d.get("check_id", "")), str(d.get("status", "")),
                   tuple(d.get("ac_ids") or ()), str(d.get("detail", "") or ""))


@dataclass
class GateEvidence:
    """A gate outcome's evidence: the verdict it reached, at what level, the artifacts it saw and the
    checks it exercised."""

    gate_id: str
    verdict: str                       # pass | block | would-block | override
    enforcement: str = ""
    artifacts: List[Artifact] = field(default_factory=list)
    tested: List[TestedCheck] = field(default_factory=list)
    run_id: str = ""
    at: str = ""

    @property
    def exercised(self) -> List[TestedCheck]:
        return [t for t in self.tested if t.exercised]

    def to_dict(self) -> Dict[str, Any]:
        return {"schema_version": EVIDENCE_SCHEMA_VERSION, "gate_id": self.gate_id,
                "verdict": self.verdict, "enforcement": self.enforcement, "run_id": self.run_id,
                "at": self.at, "artifacts": [a.to_dict() for a in self.artifacts],
                "tested": [t.to_dict() for t in self.tested]}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "GateEvidence":
        return cls(gate_id=str(d.get("gate_id", "")), verdict=str(d.get("verdict", "")),
                   enforcement=str(d.get("enforcement", "") or ""),
                   artifacts=[Artifact.from_dict(a) for a in d.get("artifacts") or []],
                   tested=[TestedCheck.from_dict(t) for t in d.get("tested") or []],
                   run_id=str(d.get("run_id", "") or ""), at=str(d.get("at", "") or ""))


def verdict_of(decision: Any) -> str:
    """The evidence verdict word for an `enforce.GateDecision` (duck-typed)."""
    if getattr(decision, "would_block", False):
        return "would-block"
    if getattr(decision, "overridden", False):
        return "override"
    return PASS if getattr(decision, "allowed", False) else "block"


# ----------------------------------------------------------------------------------- (c) finding
@dataclass
class EvidenceFinding:
    """(c) — does this gate outcome's evidence cover the stated ACs? `uncovered`: no exercised check
    names the AC. `unproven`: checks naming it were exercised, none passed. Clean only when both are
    empty. A FINDING, not a block — it is reported beside the verdict, never flips it."""

    finding_id: str
    gate_id: str
    ok: bool
    uncovered: List[str] = field(default_factory=list)
    unproven: List[str] = field(default_factory=list)
    message: str = ""


def evidence_covers_acs(evidence: GateEvidence, ac_ids: Sequence[str]) -> EvidenceFinding:
    proved: set = set()
    touched: set = set()
    for t in evidence.exercised:
        touched.update(t.ac_ids)
        if t.status == PASS:
            proved.update(t.ac_ids)
    uncovered = [a for a in ac_ids if a not in touched]
    unproven = [a for a in ac_ids if a in touched and a not in proved]
    ok = not uncovered and not unproven
    if ok:
        msg = f"evidence covers all {len(ac_ids)} stated AC(s)"
    else:
        parts = []
        if uncovered:
            parts.append("no exercised check covers " + ", ".join(uncovered))
        if unproven:
            parts.append("exercised but unproven: " + ", ".join(unproven))
        msg = "; ".join(parts)
    return EvidenceFinding(COVERAGE_FINDING_ID, evidence.gate_id, ok, uncovered, unproven, msg)


# ----------------------------------------------------------------------------------- persistence
def safe_run_dirname(run_id: str) -> str:
    """A run id reduced to a portable directory name (never empty, never a traversal)."""
    name = _SAFE.sub("_", run_id or "")
    if not name or set(name) <= {"."}:
        name = "_run"
    return name[:120]


def evidence_root_for_state(store: Any) -> Optional[str]:
    """`.mokata/temp_local/evidence` for a StateStore whose root is `.mokata/temp_local/state`."""
    root = getattr(store, "root", None)
    if not root or not isinstance(root, str):
        return None
    return os.path.join(os.path.dirname(os.path.abspath(root)), EVIDENCE_DIRNAME)


def evidence_path(evidence_root: str, run_id: str) -> str:
    return os.path.join(evidence_root, safe_run_dirname(run_id), EVIDENCE_FILENAME)


def record_evidence(evidence_root: Optional[str], run_id: str,
                    evidence: GateEvidence) -> Optional[str]:
    """Append `evidence` to `evidence/<run_id>/evidence.jsonl`; returns the path, or None when there
    was nowhere to write or the write failed (observability never raises)."""
    if not evidence_root or not run_id:
        return None
    try:
        if not evidence.run_id:
            evidence.run_id = run_id
        if not evidence.at:
            evidence.at = datetime.now(timezone.utc).isoformat()
        path = evidence_path(evidence_root, run_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(evidence.to_dict(), ensure_ascii=False) + "\n")
        return path
    except (OSError, TypeError, ValueError):
        return None


def load_evidence(evidence_root: Optional[str], run_id: str) -> List[GateEvidence]:
    """Every evidence record for `run_id`, oldest first. Absent/corrupt lines read as nothing."""
    if not evidence_root or not run_id:
        return []
    path = evidence_path(evidence_root, run_id)
    out: List[GateEvidence] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(GateEvidence.from_dict(json.loads(line)))
                except (ValueError, TypeError, AttributeError):
                    continue
    except OSError:
        return []
    return out


def tested_from(items: Iterable[Any]) -> List[TestedCheck]:
    """Normalize dicts / TestedCheck into TestedCheck (raises on a malformed one — a caller handing
    the gate a check it cannot type is a bug, not a degraded read)."""
    return [t if isinstance(t, TestedCheck) else TestedCheck.from_dict(t) for t in items]
