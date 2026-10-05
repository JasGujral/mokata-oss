"""R1.S1a — the typed, schema-versioned event vocabulary.

doc 42 specifies seven event types and one envelope, and this module is both. It exists
because the thing mokata already has is the opposite of typed: `AuditLedger.record(kind,
**fields)` takes any string and any keywords, and the tree has grown **49 distinct kinds across
90 call sites** with no declared field set between them. That is a log, and a log is queryable
only by someone who already knows what was written. ⭐ **A typed vocabulary is what makes
"every governance decision emits an event carrying duration and token usage" a checkable
sentence instead of a hope** — doc 99's events exit criterion, measured at the head of 0.0.21
stage 10 as **zero of 49 kinds carrying a duration**.

⛔ **THE EVENT STORE IS A PROJECTION, NOT A SECOND SOURCE OF TRUTH.** doc 42's R1.S1b says it
outright — *"ledger stays canonical, events derive from it"* — and that one sentence settles
what would otherwise be a §7f problem: two append-only stores, each claiming integrity, neither
gradable against the other. An event that derives from a ledger entry carries that entry's
`ledger_seq`, and its integrity claim IS the ledger's hash chain. An event that does not carries
`ledger_seq=None` and makes **no** integrity claim at all. ⚠ The two must never share a
representation (§7g), which is why the field is nullable rather than defaulted to 0.

⛔ **AND NO EVENT CARRIES CONTENT.** A `MemoryOp` names an item's id and type, never its value;
a `SecretScanHit` names a finding's kind and count, never the matched text; a `GateDecision`
names the subject, never the payload under review. This store is queried, exported and read by
whoever has the repo, and the ledger learned that lesson first (`run_reembed`'s rule). It is a
dataclass-level property, so `test_a39` asserts it over `dataclasses.fields` rather than over a
list of types somebody has to remember to extend.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from typing import Any, Dict, Optional

# Bump ONLY on an incompatible envelope change. `progress_events.PROGRESS_SCHEMA_VERSION` is the
# older, three-type log this vocabulary is a superset of; the two are deliberately separate
# numbers, because they version different things.
EVENT_SCHEMA_VERSION = 1

# The seven types doc 42 names. The tuple IS the vocabulary — `emit` refuses a type that is not
# in it, so a new event type cannot be introduced by typing a new string at a call site.
GATE_DECISION = "gate_decision"
TOOL_CALL = "tool_call"
TOKEN_SPEND = "token_spend"
MEMORY_OP = "memory_op"
PHASE_TRANSITION = "phase_transition"
APPROVAL_DECISION = "approval_decision"
SECRET_SCAN_HIT = "secret_scan_hit"

EVENT_TYPES = (GATE_DECISION, TOOL_CALL, TOKEN_SPEND, MEMORY_OP,
               PHASE_TRANSITION, APPROVAL_DECISION, SECRET_SCAN_HIT)

# The envelope keys every stored event carries, in the order the store writes them.
ENVELOPE_KEYS = ("event_id", "ts", "schema_version", "type", "session_id", "run_id",
                 "actor", "duration_ms", "ledger_seq")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_event_id() -> str:
    return uuid.uuid4().hex


@dataclass
class Envelope:
    """What every event carries regardless of type.

    `duration_ms` and `ledger_seq` are Optional ON PURPOSE and are the two fields this schema
    would be dishonest without. A decision nobody timed has no duration, and `0` would say it
    took no time; an event that derives from no ledger entry has no `ledger_seq`, and `0` is a
    real seq. Both are the §7g distinction the whole stage turns on.

    ⛔ **AND NEITHER HAS A DEFAULT, BECAUSE THE FIRST DRAFT GAVE BOTH `= None` AND THE MUTANTS
    THAT CHANGED THEM TO `= 0` SURVIVED.** Every construction of an Envelope passes both
    explicitly, so those defaults were dead text that read like the §7g guarantee while
    guaranteeing nothing (§7f). An envelope cannot FORGET to say whether it was timed or
    ledger-backed: the absence has to be stated. The live default — what a *caller* who omits
    the argument gets — lives in `EventStore.emit`'s signature, in one place, where the tests
    reach it."""
    type: str
    session_id: str
    duration_ms: Optional[int]
    ledger_seq: Optional[int]
    actor: str = ""
    event_id: str = field(default_factory=new_event_id)
    ts: str = field(default_factory=now_iso)
    schema_version: int = EVENT_SCHEMA_VERSION
    run_id: str = ""

    def to_row(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in ENVELOPE_KEYS}


class Payload:
    """Base for the seven typed payloads. Subclasses are plain dataclasses of SCALARS."""

    TYPE: str = ""

    def to_data(self) -> Dict[str, Any]:
        return asdict(self)                      # type: ignore[arg-type]

    @classmethod
    def field_names(cls) -> "tuple[str, ...]":
        return tuple(f.name for f in fields(cls))   # type: ignore[arg-type]


@dataclass
class GateDecision(Payload):
    """One gate evaluation: WHICH gate, WHAT it decided, and the subject as a path or an id.

    ⛔ **THERE IS NO `reason` FIELD, AND ITS ABSENCE WAS FOUND BY A TEST RATHER THAN BY
    FORESIGHT.** The first version copied the ledger entry's `reason` straight through, and the
    content-marker probe in `test_a39` walked a planted string from a gate record into this
    store on its first run. A `reason` is FREE TEXT: `render_block`'s refusals name paths, and
    `DEGRADE-DETAIL-IS-COMPUTED-BY-38-SITES-AND-SHOWN-BY-NONE` is a row in this backlog about
    ~20 sites interpolating raw exception text — one of them a psycopg error carrying host,
    port, database and user. ⭐ **The reason is not LOST: the ledger keeps it, in full, where it
    is already covered by the hash chain.** What this store holds is the typed, queryable,
    content-free view, and a free-text channel has no place in it."""
    TYPE = GATE_DECISION
    gate: str
    decision: str
    subject: str = ""


@dataclass
class ToolCall(Payload):
    """One MCP tool or CLI command dispatch."""
    TYPE = TOOL_CALL
    tool: str
    surface: str = ""
    ok: bool = True


@dataclass
class TokenSpend(Payload):
    """Token usage for one labelled unit of work.

    ⚠ `estimated` is not a convenience flag, it is the whole honesty of this type. Until 0.0.21
    stage 09 every token figure in this tree was a `chars/4` ESTIMATE, and the transcript
    adapter now also produces REAL counts. A consumer that cannot tell the two apart will
    average them, and the average of a measurement and a guess is a guess (§7g)."""
    TYPE = TOKEN_SPEND
    label: str
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    estimated: bool = True


@dataclass
class MemoryOp(Payload):
    """One memory read/write/heal. The item's ID and TYPE, never its subject or value."""
    TYPE = MEMORY_OP
    op: str
    item_id: str = ""
    mtype: str = ""
    count: int = 1


@dataclass
class PhaseTransition(Payload):
    """A pipeline phase entered or passed — the `progress_events` vocabulary, typed."""
    TYPE = PHASE_TRANSITION
    phase: str
    outcome: str = "enter"


@dataclass
class ApprovalDecision(Payload):
    """A human-minted approval, or its refusal. `approval_seq` links to the ledger entry."""
    TYPE = APPROVAL_DECISION
    decision: str
    subject: str = ""
    actor: str = ""
    approval_seq: Optional[int] = None


@dataclass
class SecretScanHit(Payload):
    """A secret-scan finding: its KIND and WHERE, never the matched text and never a count of
    characters that could narrow it."""
    TYPE = SECRET_SCAN_HIT
    kind: str
    where: str = ""
    count: int = 1


PAYLOAD_TYPES = (GateDecision, ToolCall, TokenSpend, MemoryOp,
                 PhaseTransition, ApprovalDecision, SecretScanHit)

PAYLOAD_BY_TYPE = {p.TYPE: p for p in PAYLOAD_TYPES}


def payload_for(event_type: str):
    """The payload class for a type name, or None. The lookup is DERIVED from `PAYLOAD_TYPES`,
    so a type declared in `EVENT_TYPES` with no payload class is a hole a test can find rather
    than a `KeyError` at a call site."""
    return PAYLOAD_BY_TYPE.get(event_type)
