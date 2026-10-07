"""R1.S1a–d — mokata's typed event stream.

Seven schema-versioned event types over one envelope, an append-only SQLite store, and the
instrumentation that fills it. ⛔ **The ledger stays canonical and events derive from it**
(doc 42 R1.S1b): an event carrying a `ledger_seq` is backed by the ledger's hash chain, one
without carries no integrity claim, and nothing here is a second source of truth.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from .schema import (
    APPROVAL_DECISION,
    EVENT_SCHEMA_VERSION,
    EVENT_TYPES,
    GATE_DECISION,
    MEMORY_OP,
    PHASE_TRANSITION,
    SECRET_SCAN_HIT,
    TOKEN_SPEND,
    TOOL_CALL,
    ApprovalDecision,
    Envelope,
    GateDecision,
    MemoryOp,
    Payload,
    PAYLOAD_TYPES,
    PhaseTransition,
    SecretScanHit,
    TokenSpend,
    ToolCall,
    payload_for,
)
from .store import (
    DEFAULT_LIMIT,
    EVENTS_SETTINGS_KEY,
    EventStore,
    StoredEvent,
    emit,
    enabled,
    events_dir,
    events_path,
    query,
    store_for_root,
)

__all__ = [
    # schema
    "EVENT_SCHEMA_VERSION", "EVENT_TYPES", "Envelope", "Payload", "PAYLOAD_TYPES", "payload_for",
    "GATE_DECISION", "TOOL_CALL", "TOKEN_SPEND", "MEMORY_OP", "PHASE_TRANSITION",
    "APPROVAL_DECISION", "SECRET_SCAN_HIT",
    "GateDecision", "ToolCall", "TokenSpend", "MemoryOp", "PhaseTransition",
    "ApprovalDecision", "SecretScanHit",
    # store
    "EventStore", "StoredEvent", "EVENTS_SETTINGS_KEY", "DEFAULT_LIMIT",
    "emit", "query", "enabled", "events_dir", "events_path", "store_for_root",
]
