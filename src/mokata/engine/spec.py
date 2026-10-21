"""D2/D3 — the spec model: acceptance criteria and the tests that trace to them.

A spec is a title plus concrete, testable acceptance criteria (ACs). The AC-mapper and
the completeness gate are built on this; a `TestRef` records which ACs a test covers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..spec_scope import SCOPE_KEY, SpecScope, scope_from_dict


# SPEC-ANCHOR (0.0.23 D4) — WHERE an acceptance criterion lives in the code, so the test phase places
# its test without re-exploring the tree. ADVISORY LOCATIONS, never authoritative over live code
# (Jas 2026-07-18): each anchor names the evidence it came from (`source`) and how sure that evidence
# is (`confidence`), and develop RE-VERIFIES it against live code (LIVE-EVIDENCE) — a stale anchor is
# a finding, not a directive.
ANCHOR_IMPL = "impl"                    # the code the AC changes
ANCHOR_TEST_TARGET = "test-target"      # where the AC's test belongs
ANCHOR_REFERENCE = "reference"          # a coarse pointer (a scope glob), not a location
ANCHOR_KINDS = (ANCHOR_IMPL, ANCHOR_TEST_TARGET, ANCHOR_REFERENCE)
#: approach = AP-SD `decisions[].about_code` · blast-radius = brainstorm Lens-1 impact · graph = a
#: graph query · scope = `scope.authorized` fallback · spec = written by the spec's author.
ANCHOR_SOURCES = ("approach", "blast-radius", "graph", "scope", "spec")
ANCHOR_CONFIDENCE = ("high", "medium", "low")


@dataclass(frozen=True)
class CodeAnchor:
    file: str
    symbol: str = ""
    kind: str = ANCHOR_IMPL
    source: str = "spec"
    confidence: str = "medium"

    @property
    def key(self) -> "tuple":
        return (self.file, self.symbol, self.kind)

    def label(self) -> str:
        return f"{self.file}:{self.symbol}" if self.symbol else self.file

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"file": self.file}
        if self.symbol:
            out["symbol"] = self.symbol
        out.update({"kind": self.kind, "source": self.source, "confidence": self.confidence})
        return out

    @classmethod
    def from_dict(cls, d: Any) -> "CodeAnchor":
        """STRICT. An anchor that is not the shape above raises `ValueError`, so a persisted spec
        carrying one reads as MALFORMED (`spec_gate.read_emitted_spec`) — refused loudly, never
        silently dropped (pre-1.0: no shims, and never a quiet loss of what a user wrote)."""
        if not isinstance(d, dict):
            raise ValueError("a code anchor must be an object {file, symbol?, kind, source, "
                             "confidence}")
        f = d.get("file")
        if not isinstance(f, str) or not f.strip():
            raise ValueError("a code anchor needs a non-empty `file`")
        sym = d.get("symbol", "") or ""
        kind = d.get("kind", ANCHOR_IMPL)
        source = d.get("source", "spec")
        conf = d.get("confidence", "medium")
        if not isinstance(sym, str):
            raise ValueError(f"anchor {f}: `symbol` must be a string")
        if kind not in ANCHOR_KINDS:
            raise ValueError(f"anchor {f}: kind {kind!r} is not one of {', '.join(ANCHOR_KINDS)}")
        if source not in ANCHOR_SOURCES:
            raise ValueError(f"anchor {f}: source {source!r} is not one of "
                             f"{', '.join(ANCHOR_SOURCES)}")
        if conf not in ANCHOR_CONFIDENCE:
            raise ValueError(f"anchor {f}: confidence {conf!r} is not one of "
                             f"{', '.join(ANCHOR_CONFIDENCE)}")
        return cls(file=f.strip(), symbol=sym.strip(), kind=kind, source=source, confidence=conf)


# 0.0.25 Lane D — the UI acceptance-criterion FORM (research 2026-10-16, item 6). A UI criterion
# states its BEHAVIOUR (Given/When/Then, in `text`) AND what the user sees in every state the UI can
# be in. A spec that says only the happy path ships a component whose loading, empty, error and
# disabled states nobody decided — so the completeness gate refuses a UI criterion without the full
# matrix. A state that genuinely cannot occur is still written down: `"n/a — <why>"`.
AC_KIND_BEHAVIOUR = "behaviour"
AC_KIND_UI = "ui"
AC_KINDS = (AC_KIND_BEHAVIOUR, AC_KIND_UI)
UI_STATES = ("default", "loading", "empty", "error", "disabled")


@dataclass
class AcceptanceCriterion:
    id: str
    text: str = ""
    # SPEC-ANCHOR — additive, serialized ONLY when set, so an anchorless AC's bytes are unchanged.
    anchors: List[CodeAnchor] = field(default_factory=list)
    # 0.0.25 Lane D — `ui` criteria carry a state matrix {state: what the user sees}. Serialized only
    # for a UI criterion; a behaviour criterion has no `kind`/`states` keys on disk.
    kind: str = AC_KIND_BEHAVIOUR
    states: Dict[str, str] = field(default_factory=dict)

    def missing_states(self) -> List[str]:
        """For a UI criterion, the states (in :data:`UI_STATES` order) with no stated expectation;
        always ``[]`` for a behaviour criterion."""
        if self.kind != AC_KIND_UI:
            return []
        return [st for st in UI_STATES if not str(self.states.get(st, "")).strip()]

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"id": self.id, "text": self.text}
        if self.anchors:
            out["anchors"] = [a.to_dict() for a in self.anchors]
        if self.kind == AC_KIND_UI:
            out["kind"] = AC_KIND_UI
            out["states"] = {st: self.states[st] for st in UI_STATES if st in self.states}
        return out

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "AcceptanceCriterion":
        """STRICT, like the anchors: an unknown `kind`, a non-object `states`, a state outside
        :data:`UI_STATES`, or a state matrix on a behaviour criterion raises `ValueError`, so a
        malformed spec is refused loudly at the surface rather than judged as something else."""
        raw = d.get("anchors", [])
        if not isinstance(raw, list):
            raise ValueError(f"criterion {d.get('id')}: `anchors` must be a list")
        kind = d.get("kind", AC_KIND_BEHAVIOUR) or AC_KIND_BEHAVIOUR
        if kind not in AC_KINDS:
            raise ValueError(f"criterion {d.get('id')}: kind {kind!r} is not one of "
                             f"{', '.join(AC_KINDS)}")
        states = d.get("states", {}) or {}
        if not isinstance(states, dict):
            raise ValueError(f"criterion {d.get('id')}: `states` must be an object "
                             f"{{{', '.join(UI_STATES)}}}")
        unknown = sorted(str(k) for k in states if k not in UI_STATES)
        if unknown:
            raise ValueError(f"criterion {d.get('id')}: unknown UI state(s) {', '.join(unknown)} "
                             f"— the states are {', '.join(UI_STATES)}")
        if states and kind != AC_KIND_UI:
            raise ValueError(f"criterion {d.get('id')}: `states` belong to a UI criterion — set "
                             f"\"kind\": \"ui\"")
        for st, val in states.items():
            if not isinstance(val, str):
                raise ValueError(f"criterion {d.get('id')}: state {st!r} must be text")
        return cls(id=d["id"], text=d.get("text", ""),
                   anchors=[CodeAnchor.from_dict(a) for a in raw],
                   kind=kind, states=dict(states))


@dataclass
class TestRef:
    name: str
    ac_ids: List[str] = field(default_factory=list)
    path: str = ""
    line: int = 0


@dataclass
class Spec:
    title: str
    criteria: List[AcceptanceCriterion] = field(default_factory=list)
    approach: Optional[str] = None       # the approved-approach name, if known
    source: str = ""
    # DK.S0 — the domains-in-play, classified at brainstorm from the approach's graph surface and
    # persisted here as a FIRST-CLASS constraint beside the ACs + approach (human-approved +
    # legible). develop/review engage EXACTLY these; a domain reached only later amends the spec.
    # A JSON field only — the store SCHEMA is untouched; a legacy spec with no key reads as [].
    domains: List[str] = field(default_factory=list)
    # SI-DEV — the MACHINE-CHECKABLE scope: the paths this spec authorized, and the items it
    # explicitly DEFERRED (each carrying the paths/markers a hook can recognise it by). The gate
    # hook enforces exactly this and nothing more; a spec with no scope (every spec written before
    # SI-DEV) reads as None and is NOT policed. Additive, like `domains` above: a JSON field only,
    # serialized ONLY when set, so a scope-less spec's persisted bytes are unchanged.
    scope: Optional["SpecScope"] = None

    @property
    def ac_ids(self) -> List[str]:
        return [c.id for c in self.criteria]

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "title": self.title,
            "criteria": [c.to_dict() for c in self.criteria],
            "approach": self.approach,
            "domains": list(self.domains),
        }
        if self.scope is not None:
            out[SCOPE_KEY] = self.scope.to_dict()
        return out

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Spec":
        return cls(
            title=d.get("title", ""),
            criteria=[AcceptanceCriterion.from_dict(c) for c in d.get("criteria", [])],
            approach=d.get("approach"),
            source=d.get("source", ""),
            domains=list(d.get("domains", [])),
            scope=scope_from_dict(d.get(SCOPE_KEY)),
        )
