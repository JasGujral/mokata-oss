"""I3 — full audit ledger.

An append-only JSONL record spanning every gate decision, tool call, and durable write.
Append-only by construction (entries are only ever added, each with a monotonic seq), so
a human can reconstruct and walk back any choice the system made (P7).
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List

from .. import TEMP_LOCAL_DIRNAME

AUDIT_DIRNAME = "audit"
LEDGER_FILENAME = "ledger.jsonl"

# Serialize the read-seq-then-append across threads: parallel fanout (Stage 8) shares one
# ledger, and a plain text-mode append is atomic on POSIX (O_APPEND) but NOT on Windows, where
# concurrent appends can clobber each other and drop an entry. One process-wide lock keeps the
# append-only ledger correct on every OS; single-threaded behavior is unchanged.
_RECORD_LOCK = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AuditLedger:
    def __init__(self, path: str) -> None:
        self.path = path
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)

    @classmethod
    def from_mokata_dir(cls, mokata_dir: str) -> "AuditLedger":
        # The ledger is transient runtime data (Stage 24D): under .mokata/temp_local/.
        return cls(os.path.join(mokata_dir, TEMP_LOCAL_DIRNAME, AUDIT_DIRNAME,
                                LEDGER_FILENAME))

    def record(self, kind: str, **fields: Any) -> Dict[str, Any]:
        """Append one entry and return it. Never rewrites existing entries. The
        seq-then-append is locked so concurrent fanout writers never drop an entry."""
        with _RECORD_LOCK:
            entry: Dict[str, Any] = {"seq": len(self) + 1, "kind": kind,
                                     "at": _now_iso()}
            entry.update(fields)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
            return entry

    def entries(self) -> List[Dict[str, Any]]:
        if not os.path.exists(self.path):
            return []
        out: List[Dict[str, Any]] = []
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    def __len__(self) -> int:
        return len(self.entries())


# --------------------------------------------------------------------------- Stage 49 —
# decision observability: a read-only, bounded "what + decision + WHY" timeline derived from
# the ledger. Pure (no I/O beyond the caller's entries), frugal (a tail, not the whole
# history), local-first. Surfaces whatever rationale each entry carries.

WHY_TIMELINE_TAIL = 50

# A human label per ledger kind (falls back to the raw kind for anything unmapped).
_WHY_WHAT = {
    "deviation": "deviation gate", "spec_conflict": "spec-awareness",
    "write_gate": "write gate", "karpathy_gate": "karpathy gate",
    "healing_decision": "self-healing", "consolidation_proposal": "consolidation (proposed)",
    "consolidation_decision": "consolidation", "rule_promotion_proposed": "rule learning",
    "rule_promotion_decision": "rule promotion", "outbound": "outbound gate",
    "model_route": "model routing", "phase": "pipeline phase", "subagent": "subagent",
    "sequential": "task", "finish": "ship", "spec_check": "spec-awareness",
    "revert": "revert", "reversible_write": "write",
}
# Where the human-readable rationale lives, in priority order (different layers named it
# differently before this stage; we read them all so the WHY always surfaces if present).
_WHY_REASON_KEYS = ("reason", "why", "rationale", "detail", "message")
_WHY_SUBJECT_KEYS = ("target", "subject", "gate", "pattern", "action", "task", "phase")
_WHY_DECISION_KEYS = ("decision", "passed", "allowed", "changed", "added", "ok")


def _why_pick(entry: Dict[str, Any], keys) -> str:
    for k in keys:
        if k in entry and entry[k] not in (None, ""):
            return str(entry[k])
    return ""


def why_timeline(entries: List[Dict[str, Any]],
                 tail: int = WHY_TIMELINE_TAIL) -> List[str]:
    """A readable what+decision+why line per entry, bounded to the last `tail` (frugal, P11).
    Read-only: derives from the given entries and returns strings; writes nothing."""
    rows = list(entries)[-tail:] if tail and tail > 0 else list(entries)
    out: List[str] = []
    for e in rows:
        kind = e.get("kind", "")
        what = _WHY_WHAT.get(kind, kind or "event")
        subject = _why_pick(e, _WHY_SUBJECT_KEYS)
        decision = _why_pick(e, _WHY_DECISION_KEYS)
        why = _why_pick(e, _WHY_REASON_KEYS)
        line = f"#{e.get('seq', '?'):<4} {what}"
        if subject:
            line += f" · {subject}"
        if decision:
            line += f" — {decision}"
        if why:
            line += f"  (why: {why})"
        out.append(line)
    return out
