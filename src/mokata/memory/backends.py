"""C4 — pluggable memory storage backends (STORAGE ONLY).

All three live behind one `MemoryBackend` contract; the memory *logic* (gating, healing,
toggles, instrumentation) is mokata's own and lives in `store.py`. SQLite is the
guaranteed default floor (stdlib, no dependency). Obsidian (markdown vault) is a real
local adapter. native-memory is an optional adapter delegating to an injected client —
when no client is wired, selection degrades to the SQLite floor (never a hard failure).
"""

from __future__ import annotations

import json
import os
import sqlite3
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Protocol, Tuple

from .item import ACTIVE, MemoryItem


class MemoryBackend(ABC):
    name: str = ""

    @abstractmethod
    def put(self, item: MemoryItem) -> None: ...

    @abstractmethod
    def get(self, item_id: str) -> Optional[MemoryItem]: ...

    @abstractmethod
    def all(self, mtype: Optional[str] = None,
            statuses: Optional[Tuple[str, ...]] = None) -> List[MemoryItem]: ...

    @abstractmethod
    def delete(self, item_id: str) -> bool: ...

    def update(self, item: MemoryItem) -> None:
        """Upsert (storage is keyed by id)."""
        self.put(item)

    def close(self) -> None:
        pass


# --------------------------------------------------------------------------- sqlite
class SQLiteBackend(MemoryBackend):
    name = "sqlite"

    def __init__(self, path: str, name: str = "sqlite") -> None:
        self.path = path
        self.name = name
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS memory (
                   seq INTEGER PRIMARY KEY AUTOINCREMENT,
                   id TEXT UNIQUE,
                   mtype TEXT,
                   subject TEXT,
                   status TEXT,
                   doc TEXT
               )"""
        )
        self._conn.commit()

    def put(self, item: MemoryItem) -> None:
        doc = json.dumps(item.to_dict())
        self._conn.execute(
            """INSERT INTO memory (id, mtype, subject, status, doc)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   mtype=excluded.mtype, subject=excluded.subject,
                   status=excluded.status, doc=excluded.doc""",
            (item.id, item.mtype, item.subject, item.status, doc),
        )
        self._conn.commit()

    def get(self, item_id: str) -> Optional[MemoryItem]:
        row = self._conn.execute(
            "SELECT doc FROM memory WHERE id=?", (item_id,)
        ).fetchone()
        return MemoryItem.from_dict(json.loads(row[0])) if row else None

    def all(self, mtype: Optional[str] = None,
            statuses: Optional[Tuple[str, ...]] = None) -> List[MemoryItem]:
        rows = self._conn.execute(
            "SELECT doc FROM memory ORDER BY seq"
        ).fetchall()
        items = [MemoryItem.from_dict(json.loads(r[0])) for r in rows]
        if mtype is not None:
            items = [i for i in items if i.mtype == mtype]
        if statuses is not None:
            items = [i for i in items if i.status in statuses]
        return items

    def delete(self, item_id: str) -> bool:
        cur = self._conn.execute("DELETE FROM memory WHERE id=?", (item_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def close(self) -> None:
        self._conn.close()


# ------------------------------------------------------------------------- obsidian
_FENCE = "```"


class ObsidianBackend(MemoryBackend):
    """Stores each item as a human-readable markdown note in a vault directory; the
    authoritative item dict lives in a fenced JSON block so edges round-trip exactly."""

    name = "obsidian"

    def __init__(self, vault: str, name: str = "obsidian") -> None:
        self.vault = vault
        self.name = name
        os.makedirs(vault, exist_ok=True)

    def _path(self, item_id: str) -> str:
        return os.path.join(self.vault, f"{item_id}.md")

    def put(self, item: MemoryItem) -> None:
        body = (
            f"# memory: {item.subject}  ({item.mtype})\n\n"
            f"{item.value}\n\n"
            f"{_FENCE}json\n{json.dumps(item.to_dict(), indent=2)}\n{_FENCE}\n"
        )
        with open(self._path(item.id), "w", encoding="utf-8") as fh:
            fh.write(body)

    @staticmethod
    def _parse(text: str) -> Optional[MemoryItem]:
        start = text.find(_FENCE + "json")
        if start == -1:
            return None
        start = text.find("\n", start) + 1
        end = text.find(_FENCE, start)
        if end == -1:
            return None
        return MemoryItem.from_dict(json.loads(text[start:end]))

    def get(self, item_id: str) -> Optional[MemoryItem]:
        path = self._path(item_id)
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as fh:
            return self._parse(fh.read())

    def all(self, mtype: Optional[str] = None,
            statuses: Optional[Tuple[str, ...]] = None) -> List[MemoryItem]:
        items: List[MemoryItem] = []
        for fn in sorted(os.listdir(self.vault)):
            if not fn.endswith(".md"):
                continue
            with open(os.path.join(self.vault, fn), encoding="utf-8") as fh:
                it = self._parse(fh.read())
            if it is not None:
                items.append(it)
        # stable order by creation time, then id
        items.sort(key=lambda i: (i.created_at, i.id))
        if mtype is not None:
            items = [i for i in items if i.mtype == mtype]
        if statuses is not None:
            items = [i for i in items if i.status in statuses]
        return items

    def delete(self, item_id: str) -> bool:
        path = self._path(item_id)
        if os.path.exists(path):
            os.remove(path)
            return True
        return False


# -------------------------------------------------------------------- native-memory
class MemoryClient(Protocol):
    """Contract for the Anthropic native memory tool (or any external store). The
    adapter delegates storage entirely to this; mokata's logic stays in the store."""

    def put(self, doc: Dict[str, Any]) -> None: ...
    def get(self, item_id: str) -> Optional[Dict[str, Any]]: ...
    def all(self) -> List[Dict[str, Any]]: ...
    def delete(self, item_id: str) -> bool: ...


class NativeMemoryBackend(MemoryBackend):
    name = "native-memory"

    def __init__(self, client: MemoryClient, name: str = "native-memory") -> None:
        self.client = client
        self.name = name

    def put(self, item: MemoryItem) -> None:
        self.client.put(item.to_dict())

    def get(self, item_id: str) -> Optional[MemoryItem]:
        doc = self.client.get(item_id)
        return MemoryItem.from_dict(doc) if doc else None

    def all(self, mtype: Optional[str] = None,
            statuses: Optional[Tuple[str, ...]] = None) -> List[MemoryItem]:
        items = [MemoryItem.from_dict(d) for d in self.client.all()]
        items.sort(key=lambda i: (i.created_at, i.id))
        if mtype is not None:
            items = [i for i in items if i.mtype == mtype]
        if statuses is not None:
            items = [i for i in items if i.status in statuses]
        return items

    def delete(self, item_id: str) -> bool:
        return self.client.delete(item_id)
