"""I3 — full audit ledger.

An append-only JSONL record spanning every gate decision, tool call, and durable write.
Append-only by construction (entries are only ever added, each with a monotonic seq), so
a human can reconstruct and walk back any choice the system made (P7).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

AUDIT_DIRNAME = "audit"
LEDGER_FILENAME = "ledger.jsonl"


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
        return cls(os.path.join(mokata_dir, AUDIT_DIRNAME, LEDGER_FILENAME))

    def record(self, kind: str, **fields: Any) -> Dict[str, Any]:
        """Append one entry and return it. Never rewrites existing entries."""
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
