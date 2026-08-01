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

from ..degrade import FAILURE_DOC_SCHEMA, note_degraded
from ..errors import MokataError

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
# DB.S5 — the item left the working set because a human APPROVED a budget sweep's archival
# proposal. It is a status, NOT a deletion: the row stays, the value stays, the provenance stays,
# and `valid_to` records when the window closed. Distinct from SUPERSEDED (something replaced it)
# and from STALE (its TTL elapsed and it may be wrong) — an archived item is still TRUE, it is just
# no longer hot enough to carry in the working set. That distinction is what makes un-archiving a
# coherent operation later.
ARCHIVED = "archived"

# Default top-k for by-relevance retrieval (recall_relevant / jit_recall / semantic_search /
# episodic search) — frugal (P11): retrieval returns a small ranked set, never the corpus.
DEFAULT_TOP_K = 5


# ------------------------------------------------------------------- D6 — the memory-DOC version
# The bug this closes: a memory doc carried NO version. `from_dict` below is a key-WHITELIST parse
# (every field is a `d.get(...)`) and `to_dict` re-emits only the keys it knows — so a teammate on
# an older mokata could read a newer doc, drop every field it had no name for, and WRITE IT BACK.
# An approved memory silently lost content, gated by nothing (P2: the fields the human approved
# are the fields that must survive; P21). The drop is invisible: no error, no notice, no ledger.
#
# The fix is a stamp + a compatibility rule, on the DOC axis:
#
#   read   a doc at or below MEMORY_DOC_VERSION  → full parse, mutable (today's behaviour)
#          a doc ABOVE it (or with an unreadable stamp) → parse what is readable, PRESERVE what is
#          not (see `extra`), announce ONCE — and REFUSE every write-back (see `downgrade_refusal`
#          + `to_doc`). SS.S4's bundle rule, applied to the unit that is actually at risk.
#   write  every new/updated doc carries `schema_version`.
#
# Why read-but-never-write, and not SS.S4's refuse-the-artifact-entirely: a bundle is ONE artifact
# you either pull or don't, so refusing it costs a single pull. A memory CORPUS is many docs, and
# refusing them on READ would make one teammate's upgrade blank every newer doc out of every older
# teammate's recall — a read that lies by omission (the CM.S2 bug) or a store that hard-fails until
# the whole team upgrades in lockstep (the hard split D2 exists to prevent). The harm is asymmetric
# and it is entirely on the write side: reading a newer doc destroys nothing, and the fields we
# cannot model ride along on `extra` untouched. Rewriting one destroys an approved memory. So the
# read proceeds (loudly), and every mutation path refuses (loudly).
#
# This is the DOC axis, NOT `teamdb.TEAM_SCHEMA_VERSION` (currently 3), which versions the shared
# DDL — the tables and columns. The two move independently: a doc-shape change needs no DDL (the
# doc rides an existing `doc` JSON column), and a DDL change need not touch the doc shape. Starting
# the doc axis at 3 to "match" the DB axis would assert two doc-shape breaks that never happened.
MEMORY_DOC_VERSION = 1

# FROZEN, forever: the version an UNSTAMPED doc parses as. Every doc written before D6 came from a
# build whose field set is a SUBSET of this one (the shape only ever grew — TM.S6/S7/S9/S11/S11a
# each ADDED keys), so an unstamped doc IS a v1 doc and the floor is exact, not generous. This is
# D2's legacy-parse discipline, and it is NOT "unknown is permission": the stamp is mandatory from
# v1 on, so ABSENCE proves the writer predates D6 — it can never mean "a v2 doc that forgot".
# When MEMORY_DOC_VERSION bumps to 2, this stays 1.
LEGACY_DOC_VERSION = 1

# A `schema_version` present but not readable AS a version (a string, a float, 0, a negative — a
# hand-edited or hostile doc). It declares something we cannot classify, so it is not writable:
# unknown is not permission (D2). Distinct from LEGACY (absent = provably v1) on purpose.
UNREADABLE_DOC_VERSION = 0

DOC_VERSION_KEY = "schema_version"

# Every key `to_dict` emits — i.e. every key this build MODELS. A doc key outside this set is
# "unknown", and D6's whole job is that an unknown key is PRESERVED (`MemoryItem.extra`) rather
# than dropped. Pinned against `to_dict` by a test, so adding a field without adding it here (or
# vice versa) fails loudly instead of quietly turning a modelled field into an "unknown" one.
DOC_KEYS = frozenset({
    "id", "subject", "value", "mtype", "status", "kind", "provenance", "expires_at",
    "supersedes", "depends_on", "scope_level", "scope_id", "pin", "priority", "enforcement",
    "applicability", "review", "about_code", "valid_from", "valid_to",
    # M-1/R9 — the consent chain (see `MemoryItem.approved_by`).
    "approved_by", "approved_at", "approval_ledger_id",
    DOC_VERSION_KEY,
})

# The degrade subsystem key (once-per-session notice) — the `memory-*` family memory/tiered.py
# already uses (`memory-semantic`). One key, so the notice is named + classed exactly once.
DOC_SUBSYSTEM = "memory-schema"

_DOC_FIX = ("Upgrade mokata (`pip install -U mokata`) to write it — this build will not rewrite a "
            "doc it cannot read in full")


class MemoryDocTooNew(MokataError):
    """D6 — a durable write was attempted on a doc this build cannot read in full (its
    `schema_version` is ahead of MEMORY_DOC_VERSION, or is not readable as a version at all).

    This is a HARD refusal, not a degrade: there is no floor to fall back to, because the only
    fallback on offer — "write it anyway, minus the fields you didn't understand" — IS the bug.
    It carries `degrade.FAILURE_DOC_SCHEMA` — the class D6 coined there, in the ONE module that
    owns the failure-class vocabulary, so the exception, the notice and the doctor line all say the
    same word for the same failure (errors.py). Deliberately NOT FAILURE_SCHEMA: that is the
    shared-DB schema, whose remediation (`mokata team init`) would never fix this.

    It is the BACKSTOP, not the user-facing path: every mutation path in `MemoryStore` refuses
    before the gate and returns a message (`downgrade_refusal`), so a caller sees a clean refusal.
    Reaching this exception means a write sink was called directly, and it fires so that "an older
    client cannot serialize a newer doc" is structural rather than a promise.
    """

    failure_class: str = FAILURE_DOC_SCHEMA


def doc_version(d: Dict[str, Any]) -> int:
    """The doc-schema version `d` declares. Absent → LEGACY_DOC_VERSION (provably v1, see above);
    present but not a readable version → UNREADABLE_DOC_VERSION (never writable)."""
    if DOC_VERSION_KEY not in d:
        return LEGACY_DOC_VERSION
    raw = d.get(DOC_VERSION_KEY)
    # `bool` is an `int` in Python — `True` is not a version.
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < LEGACY_DOC_VERSION:
        return UNREADABLE_DOC_VERSION
    return raw


def approval_ledger_id_of(raw: Any) -> Optional[int]:
    """M-1/R9 — the ledger id `raw` names, or None when it names none we can join on.

    Only a real `int` survives. Two exclusions, both deliberate and both copied from
    `team_journal._approval_key`, which answers the same question about the same value on the
    journal side (the two must not drift about what "an approval id" is):

      * `bool` — `True == 1` in Python, so a stray flag would silently read as approval #1;
      * everything else (`None`, `""`, `"floor-recovery"` — the journal's recovery sentinel) — a
        value that cannot index the ledger is not an approval, and coercing it to one would put a
        joinable-looking id on an item whose approval nobody can look up.

    Unknown stays None. This function never invents an id, and there is no path that does.
    """
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    return raw


def can_write_doc(version: int) -> bool:
    """May THIS build durably write a doc at `version`? Only at-or-below what it speaks. There is
    no lower bound to check: v1 is both the floor and the first version, and the shape has only
    ever grown, so no doc can be too OLD to write. The bound that exists is the one D6 is about."""
    return LEGACY_DOC_VERSION <= version <= MEMORY_DOC_VERSION


def downgrade_refusal(item: Any) -> Optional[str]:
    """The refusal message when `item`'s doc must NOT be written back by this build, else None.

    Announces ONCE per session (the CM.S2 loud-degrade shape, class `doc-schema`, fix "upgrade
    mokata") and returns the message the caller surfaces. Duck-typed on `schema_version`, so an
    item from any backend works. Nothing is written and nothing is mutated — the doc is untouched.

    Two arms, because they are two different facts about the world and a notice that blurs them is
    a notice that lies: a doc AHEAD of us was written by a teammate on a NEWER mokata (upgrade and
    it works); a doc with an UNREADABLE stamp was written by nothing we recognise (hand-edited,
    truncated, hostile) and an upgrade may not help it at all. Both refuse; only one is skew.
    """
    version = getattr(item, "schema_version", MEMORY_DOC_VERSION)
    if can_write_doc(version):
        return None
    subject = getattr(item, "subject", "?")
    if version == UNREADABLE_DOC_VERSION:
        cause = (f"declares an UNREADABLE doc schema_version (this build speaks doc "
                 f"v{MEMORY_DOC_VERSION})")
        detail = "a memory doc's schema_version is not readable as a version"
    else:
        cause = (f"was written by a NEWER mokata (doc schema v{version}; this build speaks "
                 f"v{MEMORY_DOC_VERSION})")
        detail = (f"a memory doc declares doc schema v{version}; this build speaks "
                  f"v{MEMORY_DOC_VERSION}")
    note_degraded(
        DOC_SUBSYSTEM, FAILURE_DOC_SCHEMA,
        fallback="read-only: memory items this build cannot read in full can be READ but not written",
        fix=_DOC_FIX, detail=detail)
    return (f"refused: '{subject}' {cause} — rewriting it here would silently drop the fields this "
            f"build cannot read. Nothing was written. {_DOC_FIX}.")


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
    # item JSON. Empty `[]` for every item that names no code, so legacy items round-trip
    # byte-identically.
    #
    # DB.S7a (0.0.16) amended the second half of that sentence, which used to read "NO DDL, NO
    # typed-edge graph (the full graph-native edge model is 0.1.3)". The SUBSTRATE landed early:
    # `memory/edges.py` + `memory_edges` / `mokata_memory_edges` now hold this field, `supersedes`
    # and `depends_on` as explicit typed, bi-temporal rows. What is still 0.1.3 is the FULL model —
    # bounded traversal at recall (DB.S7b), edge-aware healing (DB.S7c), the five declared-but-
    # unwired kinds, and the deprecation of these inline lists.
    #
    # Until then, and this is the contract that matters to anyone reading this dataclass: **these
    # three inline fields remain AUTHORITATIVE.** The edge rows are a DERIVED PROJECTION, rebuilt
    # from exactly these lists by the same gated write that persists them (and re-derivable in full
    # by the migration). One master per edge kind — an edge that disagreed with the list above it
    # would be a bug in the projection, never a second opinion. `to_dict`/`from_dict` are therefore
    # untouched by DB.S7a: the doc is the same bytes it was, and an export, a share and a teammate's
    # read all still carry the relations in these fields.
    about_code: List[str] = field(default_factory=list)
    # DB.S5 — the BI-TEMPORAL VALIDITY WINDOW (doc 62 lifecycle; Graphiti's model). `valid_from` is
    # when this fact STARTED being true, `valid_to` when it STOPPED; an OPEN window (`valid_to`
    # empty) is the live state. This is a different axis from `provenance.created_at` (when we
    # LEARNED it) and from `expires_at` (a one-sided TTL that drives C5 staleness) — all three
    # coexist and none replaces another.
    #
    # It is what "never delete" is made of: an item leaving the working set has its window CLOSED
    # (`lifecycle.close_window`), never its row removed, so the history of what was true when is
    # retained and re-openable. Both default to "" — an item that has never had a window carries
    # none, `valid_from` reads through to `created_at` (`lifecycle.open_window`) and an absent
    # `valid_to` reads as OPEN, so EVERY pre-DB.S5 item is valid on upgrade without a single write.
    valid_from: str = ""
    valid_to: str = ""
    # M-1/R9 — the item's own CONSENT CHAIN (doc 52 M-1: "the item carries its own consent chain").
    # The `author` half of M-1 already ships as `provenance.author` (`store._stamp_author`); this is
    # the approval half, and it answers a different question: `author` is who WROTE it, this is who
    # let it LAND. On a poisoned proposal those are two different people, which is the whole of R9.
    #
    # **THE LEDGER IS AUTHORITATIVE; these three are a PROJECTION.** The consent chain already
    # exists in full in the hash-chained audit ledger — `approval.record_redemption` records
    # proposal-hash → who approved → which write, and the gate's own `write_gate`/`approved` entry
    # is the seq everything joins on. Putting a copy on the item creates NO second source of truth
    # for who approved what: `approval_ledger_id` is a JOIN KEY back to that ledger entry, and
    # `approved_by`/`approved_at` are denormalised so a render (and an export, and a teammate
    # reading a doc with no access to our ledger file) can show the chain without a lookup that may
    # not be available. **On any disagreement the ledger wins** — the item copy is never evidence
    # against it. This is exactly the call DB.S7a already made for edge rows, and the wording there
    # is the contract here too (`team_journal._project_edges_for`): "the audit trail from a relation
    # back to the human decision that created it is a column, not an inference".
    #
    # Written ONLY by the gated write that produced the ledger entry (`store._durable_write`, inside
    # the WriteGate's commit closure, under its ledger hold). There is deliberately NO way to set
    # them through `create()` below: an item cannot be CONSTRUCTED carrying an approval, only
    # STAMPED by the act of being approved. Forging one means forging a ledger entry too.
    #
    # `approved_by` is the run identity (`team_audit.actor()` — the same source `_stamp_author`
    # uses, never a second notion of "who"). It is ADVISORY and labelled as such wherever it is
    # rendered: it is an environment-derived name, so it attributes rather than authenticates (doc
    # 52 M-1: "attribution stays honestly labeled advisory (doc 48 C3), but advisory ≠ absent").
    # `approval_ledger_id` is the element that is NOT advisory — it names a hash-chained entry.
    #
    # Empty/None means UNSET, and unset is the honest reading for every pre-M-1/R9 item: they were
    # approved (P2 was never off), but by a write path that recorded no id ON THE ITEM, and this
    # build will not invent one. Same shape DB.S5's validity window took — emitted always, empty
    # when never set, never a forged claim. `Optional[int]`, and only a real int lands: the journal
    # admits non-int ledger ids (`"floor-recovery"`) and `bool` is an `int` in Python, so both are
    # coerced away rather than stored as an approval that can be joined to nothing.
    approved_by: str = ""
    approved_at: str = ""
    approval_ledger_id: Optional[int] = None
    # D6 — the doc-schema version this item's JSON declares. A NEW item is born at the version this
    # build writes; a parsed one carries what its doc declared (see `doc_version`), which is how a
    # newer-than-us doc is recognised and refused at every write path.
    schema_version: int = MEMORY_DOC_VERSION
    # D6 — every doc key this build does NOT model, carried verbatim so a write-back re-emits it.
    # Two jobs. (1) Forward-compat WITHIN a version: a sibling key added without a shape break
    # survives a same-version round-trip instead of being silently dropped by the whitelist parse.
    # (2) Losslessness of a newer doc that is merely READ (or exported): the fields we cannot model
    # ride along untouched, so nothing is destroyed even before the write refusal fires. Splatted
    # back as SIBLINGS by `to_dict` (it is never itself a doc key), and it can never shadow a known
    # one — `from_dict` only ever puts UNKNOWN keys here.
    extra: Dict[str, Any] = field(default_factory=dict)

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
        """The doc JSON — the LOSSLESS view: every modelled field, the D6 stamp, and every unknown
        sibling key this item was parsed with. Safe on ANY item, including one from a newer build:
        it is how a read/export/render stays lossless. It is NOT the durable-write serializer —
        that is `to_doc`, which refuses a doc this build may not rewrite."""
        doc: Dict[str, Any] = {
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
            # DB.S5 — the bi-temporal window. Emitted always (like every other modelled field), and
            # empty strings for an item that has never had one, so a legacy item's window is
            # "unset" rather than a forged claim about when it started being true.
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
            # M-1/R9 — the consent chain. Emitted always, on the same reasoning as the window
            # above: empty/None says "this item carries no recorded approval", which is the TRUE
            # statement about every pre-M-1/R9 item, where omitting the keys would leave a reader
            # unable to tell "not approved through a recording path" from "this build is too old to
            # have an opinion". Never derived from anything at render time.
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
            "approval_ledger_id": self.approval_ledger_id,
            # D6 — the stamp. A legacy (unstamped) doc parses as v1 and GAINS it here on its first
            # legitimate write; a newer doc keeps the version it declared (it is never re-stamped
            # DOWN to ours — that would forge a claim this build has no right to make).
            DOC_VERSION_KEY: self.schema_version,
        }
        # D6 — unknown siblings, re-emitted verbatim. `setdefault` so a key this build models can
        # never be overwritten by one it doesn't (belt-and-braces: `from_dict` already excludes
        # every known key from `extra`).
        for key, value in self.extra.items():
            doc.setdefault(key, value)
        return doc

    def to_doc(self) -> Dict[str, Any]:
        """D6 — the DURABLE-WRITE serializer: `to_dict`, but only if this build may rewrite the
        doc. Every durable sink (the SQLite/Obsidian/native/Postgres/vector backends, the team
        journal payload, the migrate payload) calls THIS, so "an older client cannot serialize a
        newer doc" is a structural property of the write path, not a promise made by its callers.

        Raises MemoryDocTooNew when the doc is ahead of this build. The store's mutation paths
        refuse before the gate (`downgrade_refusal`), so a user never normally reaches this."""
        if not can_write_doc(self.schema_version):
            raise MemoryDocTooNew(downgrade_refusal(self) or "memory doc too new to write")
        return self.to_dict()

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MemoryItem":
        item = cls(
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
            # DB.S5 — a pre-S5 doc has no validity keys → "" / "" → `lifecycle.is_open` reads it as
            # OPEN and `lifecycle.open_window` reads its `created_at` as the window's start. The
            # whole pre-DB.S5 corpus is therefore valid on upgrade with no migration write; a v4
            # doc round-trips its window verbatim.
            valid_from=str(d.get("valid_from") or ""),
            valid_to=str(d.get("valid_to") or ""),
            # M-1/R9 — a pre-stamping doc has no approval keys → "" / "" / None, which reads as
            # "no recorded approval" everywhere and is never upgraded into one. The id goes through
            # `approval_ledger_id_of`, so a hand-edited doc carrying `true` or `"floor-recovery"`
            # lands as None rather than as a joinable-looking approval that resolves to nothing.
            approved_by=str(d.get("approved_by") or ""),
            approved_at=str(d.get("approved_at") or ""),
            approval_ledger_id=approval_ledger_id_of(d.get("approval_ledger_id")),
            # D6 — what the doc DECLARES (absent → v1, the frozen legacy floor). Never coerced to
            # ours: an item must carry the truth about its own doc, or nothing downstream can
            # refuse to rewrite it.
            schema_version=doc_version(d),
            # D6 — every key above is a WHITELIST `get`, so before this line an unknown sibling was
            # simply dropped on the floor and never re-emitted. That drop, plus a write-back, IS the
            # bug. Keep them.
            extra={k: v for k, v in d.items() if k not in DOC_KEYS},
        )
        if not can_write_doc(item.schema_version):
            # The read PROCEEDS — every modelled field above is parsed and `extra` holds the rest —
            # but it stops being a secret. Once per session (`note_degraded` is once-per-subsystem),
            # so a corpus-wide `all()` over a newer store announces once, not once per row.
            downgrade_refusal(item)
        return item
