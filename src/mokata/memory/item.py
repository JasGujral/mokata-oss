"""C1/C5 — the memory item.

A single durable memory fact, decision, or convention. Carries the metadata the
self-healing layer needs: provenance (where/who/when), a TTL via `expires_at`/`valid_for`
(staleness), and `supersedes`/`depends_on` edges (contradiction resolution + lineage).

This is mokata's own data model; storage backends only serialize it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

# TM.S6 — the scope hierarchy this item is written at (doc 62 §2 axis A). `PERSONAL` is the
# default so every legacy / zero-config item is personal, and the personal-only (local) path
# returns all of them — byte-identical local recall. The full model + union read live in scope.py.
from .scope import DEFAULT_SCOPE_LEVEL

# The memory triad — each individually toggleable (C9). This is the STORAGE type that gates
# enablement; the finer `kind` (below) is the institutional-knowledge taxonomy on top of it.
PERSISTENT = "persistent"   # project facts / conventions (C1)
DECISION = "decision"       # project decisions (C2)
EPISODIC = "episodic"       # past conversation turns (C3)
MEMORY_TYPES = (PERSISTENT, DECISION, EPISODIC)

# Stage 36 — the typed-memory "parts": a first-class `kind` on each item (D1), so the project
# brain is surfaced + retrieved by category, not as a flat dump. The new parts are all stored
# as PERSISTENT (so they inherit the persistent toggle) and distinguished by `kind`.
RULE = "rule"                   # hard project rule (always-on; counts to the rules budget)
GUARDRAIL = "guardrail"         # safety/quality constraint (always-on)
BEST_PRACTICE = "best-practice" # recommended pattern/convention (JIT)
CONTEXT = "context"             # domain fact/formula/constraint (JIT)
REFERENCE = "reference"         # distilled key points from a document, + a source pointer (JIT)

# The full taxonomy (parts + the existing decision/episodic), in display order.
MEMORY_KINDS = (RULE, GUARDRAIL, BEST_PRACTICE, CONTEXT, REFERENCE, DECISION, EPISODIC)

# The "parts" captured by /mokata:onboard — all persisted as PERSISTENT, keyed by `kind`.
PART_KINDS = (RULE, GUARDRAIL, BEST_PRACTICE, CONTEXT, REFERENCE)

# ---------------------------------------------------------- TM.S7 — governance KIND × ENFORCEMENT
# doc 62 §2 (axis B) collapses to THREE governance kinds (grooming decision): `fact`, `rule`,
# `formula`. This is a NORMALIZED view over the richer Stage-36 `kind` field, not a rewrite of it:
#   * the Stage-36 RULE ("rule") and GUARDRAIL ("guardrail") kinds → governance `rule`;
#   * FORMULA is RESERVED here (authoring rides TM.S9) — no formula is created in this stage;
#   * every other kind (facts / best-practice / context / reference / decision / persistent) → `fact`.
FACT = "fact"
FORMULA = "formula"                 # reserved (TM.S9 authors it); mapped-through only for now
GOVERNANCE_KINDS = (FACT, RULE, FORMULA)   # RULE == "rule" is shared with the Stage-36 constant

# doc 62 §4 — ENFORCEMENT is a BINDING on a rule, stored SEPARATELY from its condition (the
# Sentinel model): advisory (warn + proceed — the default on-ramp), soft (block w/ a logged
# override), hard (block, no runtime override). It is meaningful ONLY for a governance `rule`;
# facts/formulae carry no enforcement. NEW rules are BORN ADVISORY; PROMOTION is the gated moment
# (TM.S7). The in-run block itself is TM.S8 — this stage only carries + promotes the binding.
ADVISORY = "advisory"
SOFT = "soft"
HARD = "hard"
ENFORCEMENT_LEVELS = (ADVISORY, SOFT, HARD)   # ordered advisory → soft → hard (broadening force)
_ENFORCEMENT_RANK = {level: i for i, level in enumerate(ENFORCEMENT_LEVELS)}


def governance_kind(kind: str) -> str:
    """Normalize a Stage-36 `kind` (or governance kind) to one of GOVERNANCE_KINDS. RULE +
    GUARDRAIL → `rule`; FORMULA → `formula`; everything else → `fact`."""
    if kind in (RULE, GUARDRAIL):
        return RULE
    if kind == FORMULA:
        return FORMULA
    return FACT


def default_enforcement(kind: str) -> str:
    """The enforcement a rule is BORN with when none is set: a mapped GUARDRAIL is `hard`
    (it was an always-on safety constraint), every other rule is `advisory` (low-friction
    on-ramp — doc 62 §4). Non-rule kinds return `advisory` but it is not applicable to them."""
    return HARD if kind == GUARDRAIL else ADVISORY


def is_valid_enforcement(value: Any) -> bool:
    return value in _ENFORCEMENT_RANK


def enforcement_rank(value: str) -> int:
    """advisory=0 < soft=1 < hard=2 — the direction of a promotion (higher) vs demotion (lower)."""
    return _ENFORCEMENT_RANK.get(value, 0)


def effective_enforcement(item: Any) -> str:
    """The enforcement in force for `item`: its explicit `enforcement`, or the kind-derived
    default when unset (so a legacy GUARDRAIL reads as `hard`, a bare rule as `advisory`).
    Duck-typed — works on any object exposing `enforcement` / `kind`."""
    enf = getattr(item, "enforcement", "") or ""
    return enf if enf in _ENFORCEMENT_RANK else default_enforcement(getattr(item, "kind", ""))

# Always-on: injected into the SessionStart briefing / rules surface every run (capped, P11).
ALWAYS_ON_KINDS = (RULE, GUARDRAIL)
# JIT: pulled into a skill ONLY when relevant to the task at hand — never a corpus dump (P11).
JIT_KINDS = (BEST_PRACTICE, CONTEXT, REFERENCE)

# Item lifecycle statuses.
ACTIVE = "active"
SUPERSEDED = "superseded"
STALE = "stale"
# TM.S11 — the review-workflow statuses (doc 62 §6). A proposal in the Draft/In-Review/Approved
# states carries `PROPOSED` — it is NOT live, so it is excluded from every ACTIVE recall until it
# is PUBLISHED (at which point its status flips to ACTIVE and it supersedes the item it changes,
# giving the rollback lineage). A rejected proposal is terminal `REJECTED`. The review STATE itself
# (draft|in-review|approved|published|rejected) lives in `item.review` (memory/review.py); these
# two statuses are only the storage-lifecycle mirror that keeps un-published drafts out of recall.
PROPOSED = "proposed"
REJECTED = "rejected"

# Default top-k for by-relevance retrieval (recall_relevant / jit_recall / semantic_search /
# episodic search) — frugal (P11): retrieval returns a small ranked set, never the corpus.
DEFAULT_TOP_K = 5


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def add_seconds(iso: str, seconds: int) -> str:
    dt = datetime.fromisoformat(iso)
    return (dt + timedelta(seconds=seconds)).isoformat()


@dataclass
class MemoryItem:
    subject: str
    value: str
    mtype: str = PERSISTENT
    id: str = ""
    status: str = ACTIVE
    kind: str = ""               # Stage 36 — the typed-memory part (see MEMORY_KINDS)
    provenance: Dict[str, Any] = field(default_factory=dict)
    expires_at: Optional[str] = None
    supersedes: List[str] = field(default_factory=list)
    depends_on: List[str] = field(default_factory=list)
    # TM.S6 — scope (doc 62 §2) + precedence hints (doc 62 §3). `scope_level` names the home
    # level in the broad→narrow chain (default personal); `scope_id` is the team/project/category/
    # user id at that level. `pin` marks a broader-scope un-overridable floor (preference class);
    # `priority` is the explicit integer tiebreak (Drools salience). The enforcement-level binding
    # + typed authoring ride TM.S7 — these fields are the storage + precedence inputs only.
    scope_level: str = DEFAULT_SCOPE_LEVEL
    scope_id: str = ""
    pin: bool = False
    priority: int = 0
    # TM.S7 — the enforcement BINDING (doc 62 §4), separate from the condition/value. "" means
    # "unset → derive from kind" (advisory for a rule, hard for a mapped guardrail). Only a
    # governance `rule` uses it; facts/formulae ignore it. Changed ONLY through the gated
    # promotion path (store.promote), never by a plain edit — the binding moves without a rewrite.
    enforcement: str = ""
    # TM.S9 — a FORMULA's trigger/APPLICABILITY metadata (doc 62 §2 axis B, §8), stored in the
    # item JSON (no DDL). For a `kind=formula` item the `value` holds the parameterized template
    # string and this dict holds `{triggers: [...], topic: "...", params: [...]}` — the "when it
    # applies" + named params matched at recall. Empty for every non-formula / legacy item, so
    # they round-trip byte-identically. The formula logic lives in memory/formula.py.
    applicability: Dict[str, Any] = field(default_factory=dict)
    # TM.S11 — the review-workflow metadata (doc 62 §6), stored in the item JSON (no DDL — workflow
    # state on the item, not a new table). A proposal carries
    # `{state, proposer, approver, base_id, change}`: `state` is the Draft→…→Published position,
    # `proposer`/`approver` enforce separation of duties, `base_id` is the published item this
    # change supersedes (rollback lineage), `change` is the origin label (edit|enforce|formula|new).
    # Empty `{}` for every non-proposal / legacy item, so they round-trip byte-identically.
    review: Dict[str, Any] = field(default_factory=dict)
    # TM.S11a — the LIGHTWEIGHT `about_code` link (doc 55 K3 minimal): the code symbols/files this
    # decision or rule CONCERNS. It lets brainstorm's blast-radius lens union code impact with the
    # team DECISIONS an approach touches ("affected team decisions"). A plain list of strings in the
    # item JSON — NO DDL, NO typed-edge graph (the full graph-native edge model is 0.1.3). Empty
    # `[]` for every item that names no code, so legacy items round-trip byte-identically.
    about_code: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.id:
            self.id = uuid4().hex

    @classmethod
    def create(
        cls,
        subject: str,
        value: str,
        mtype: str = PERSISTENT,
        author: str = "user",
        source: str = "manual",
        created_at: Optional[str] = None,
        expires_at: Optional[str] = None,
        valid_for: Optional[int] = None,
        id: Optional[str] = None,
        kind: str = "",
        supersedes: Optional[List[str]] = None,
        depends_on: Optional[List[str]] = None,
        scope_level: str = DEFAULT_SCOPE_LEVEL,
        scope_id: str = "",
        pin: bool = False,
        priority: int = 0,
        enforcement: str = "",
        applicability: Optional[Dict[str, Any]] = None,
        review: Optional[Dict[str, Any]] = None,
        about_code: Optional[List[str]] = None,
    ) -> "MemoryItem":
        created = created_at or now_iso()
        if expires_at is None and valid_for is not None:
            expires_at = add_seconds(created, valid_for)
        return cls(
            subject=subject,
            value=value,
            mtype=mtype,
            id=id or uuid4().hex,
            kind=kind,
            provenance={"source": source, "author": author, "created_at": created},
            expires_at=expires_at,
            supersedes=list(supersedes or []),
            depends_on=list(depends_on or []),
            scope_level=scope_level or DEFAULT_SCOPE_LEVEL,
            scope_id=scope_id or "",
            pin=bool(pin),
            priority=int(priority or 0),
            # TM.S7 — born unset ("") unless caller pins a level; effective_enforcement derives
            # advisory (a new rule is BORN ADVISORY) / hard (a mapped guardrail).
            enforcement=enforcement or "",
            # TM.S9 — formula applicability metadata; empty for every non-formula item.
            applicability=dict(applicability or {}),
            # TM.S11 — review-workflow metadata; empty for every non-proposal item.
            review=dict(review or {}),
            # TM.S11a — about_code link; empty for every item that names no code symbols.
            about_code=[str(s) for s in (about_code or [])],
        )

    @property
    def created_at(self) -> str:
        return self.provenance.get("created_at", "")

    @property
    def effective_kind(self) -> str:
        """The taxonomy bucket for surfacing/grouping: the explicit `kind`, or the storage
        `mtype` when none was set (so legacy decision/episodic items still group sensibly)."""
        return self.kind or self.mtype

    @property
    def governance_kind(self) -> str:
        """TM.S7 — the normalized governance kind (fact | rule | formula) for this item."""
        return governance_kind(self.kind)

    @property
    def effective_enforcement(self) -> str:
        """TM.S7 — the enforcement level in force (explicit, or the kind-derived default)."""
        return effective_enforcement(self)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "subject": self.subject,
            "value": self.value,
            "mtype": self.mtype,
            "status": self.status,
            "kind": self.kind,
            "provenance": dict(self.provenance),
            "expires_at": self.expires_at,
            "supersedes": list(self.supersedes),
            "depends_on": list(self.depends_on),
            "scope_level": self.scope_level,
            "scope_id": self.scope_id,
            "pin": self.pin,
            "priority": self.priority,
            "enforcement": self.enforcement,
            "applicability": dict(self.applicability),
            "review": dict(self.review),
            "about_code": list(self.about_code),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MemoryItem":
        return cls(
            subject=d["subject"],
            value=d["value"],
            mtype=d.get("mtype", PERSISTENT),
            id=d.get("id", ""),
            status=d.get("status", ACTIVE),
            kind=d.get("kind", ""),
            provenance=dict(d.get("provenance", {})),
            expires_at=d.get("expires_at"),
            supersedes=list(d.get("supersedes", [])),
            depends_on=list(d.get("depends_on", [])),
            # TM.S6 — legacy docs (pre-scope) default to personal / unpinned / priority 0, so a
            # pre-TM.S6 item round-trips unchanged and matches the personal-only path.
            scope_level=d.get("scope_level") or DEFAULT_SCOPE_LEVEL,
            scope_id=d.get("scope_id", "") or "",
            pin=bool(d.get("pin", False)),
            priority=int(d.get("priority", 0) or 0),
            # TM.S7 — a pre-S7 doc has no `enforcement` key → "" → effective_enforcement derives
            # advisory (a plain rule) or hard (a mapped GUARDRAIL) without a migration write.
            enforcement=d.get("enforcement", "") or "",
            # TM.S9 — a pre-S9 doc has no `applicability` key → {} (no formula metadata); a
            # formula doc round-trips its triggers/topic/params dict verbatim.
            applicability=dict(d.get("applicability", {}) or {}),
            # TM.S11 — a pre-S11 doc has no `review` key → {} (not a proposal); a proposal doc
            # round-trips its {state, proposer, approver, base_id, change} dict verbatim.
            review=dict(d.get("review", {}) or {}),
            # TM.S11a — a pre-S11a doc has no `about_code` key → [] (names no code); a linked item
            # round-trips its symbol list verbatim.
            about_code=[str(s) for s in (d.get("about_code", []) or [])],
        )
