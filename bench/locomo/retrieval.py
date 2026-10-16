"""The retrieval half: mokata's REAL memory store, one fresh store per conversation, per arm.

Ingestion goes through `MemoryStore.remember` — the real gated write (secret-scan, WriteGate,
embedding stamped on write) — with `assume_yes=True`: the declared BENCHMARK AUTO-APPROVE posture.
Every auto-approval (and every secret-scan block) is LEDGERED by the harness, because in the
product each of these writes would have waited for a human (P2).

Retrieval is `tiered.tiered_recall` with the arm's tiers switched on, mirroring
`tests/_quality_harness.run_arm` (the DB.S8d ladder): A jaccard (FTS off) · B fts · C +vector ·
D +expansion. FULL is the control: no retrieval, the whole conversation is the context.

mokata performs NO LLM fact extraction: a dialogue turn is stored as-is.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Optional, Tuple

from .dataset import Conversation

ARM_A, ARM_B, ARM_C, ARM_D, ARM_FULL = "A:jaccard", "B:fts", "C:+vector", "D:+expansion", "FULL:context"
ARMS = (ARM_A, ARM_B, ARM_C, ARM_D, ARM_FULL)
EMBEDDER_ARMS = (ARM_C, ARM_D)


def estimate_tokens(text: str) -> int:
    from mokata.bootstrap import estimate_tokens as est      # mokata's own (~4 chars/token)
    return est(text)


def render_turn(t) -> str:
    return f"[{t.date}] {t.speaker}: {t.text}"


class MokataRetriever:
    def __init__(self, arm: str, workdir: str, embedder: Any = None):
        if arm not in ARMS or arm == ARM_FULL:
            raise ValueError(f"MokataRetriever serves retrieval arms, not {arm!r}")
        if arm in EMBEDDER_ARMS and embedder is None:
            raise ValueError(f"arm {arm} needs an embedder")
        self.arm, self.workdir = arm, workdir
        self.embedder = embedder if arm in EMBEDDER_ARMS else None
        self.store = None
        self.backend = None
        self.ctx = None
        self.ledger: List[Dict[str, Any]] = []

    @property
    def embedder_id(self) -> Optional[str]:
        return getattr(self.embedder, "embedder_id", None) if self.embedder is not None else None

    def ingest(self, conv: Conversation) -> None:
        from mokata.memory import scope as sc
        from mokata.memory.backends import SQLiteBackend
        from mokata.memory.item import CONTEXT, PERSISTENT, MemoryItem
        from mokata.memory.store import MemoryStore
        os.makedirs(self.workdir, exist_ok=True)
        self.backend = SQLiteBackend(os.path.join(self.workdir, "memory.db"))
        self.ctx = sc.ScopeContext(project=f"locomo-{conv.id}")
        self.store = MemoryStore(self.backend, scope_context=self.ctx, embedder=self.embedder)
        for t in conv.turns:
            item = MemoryItem(id=t.dia_id, subject=f"{t.speaker} {t.date}", value=t.text,
                              mtype=PERSISTENT, kind=CONTEXT, scope_level=sc.PROJECT,
                              scope_id=f"locomo-{conv.id}")
            res = self.store.remember(item, assume_yes=True)
            self.ledger.append({"item": t.dia_id, "posture": "benchmark-auto-approve",
                                "committed": bool(res.committed),
                                "blocked": bool(getattr(res, "blocked", False)),
                                "message": res.message})

    def retrieve(self, query: str, k: int) -> List[Tuple[str, str]]:
        from mokata.memory import tiered
        from mokata.memory.store import MemoryStore
        saved = self.backend._fts
        # Arm A is the Jaccard floor: FTS off for the duration of the read (as _quality_harness).
        self.backend._fts = saved and self.arm != ARM_A
        try:
            store = MemoryStore(self.backend, scope_context=self.ctx, embedder=self.embedder)
            expander = store._edge_expander() if self.arm == ARM_D else None
            hits = tiered.tiered_recall(store, query, embedder=self.embedder, top_k=k,
                                        expander=expander, degrade_out=lambda _m: None,
                                        count_read=False)
        finally:
            self.backend._fts = saved
        return [(h.item.id, f"{h.item.subject}: {h.item.value}") for h in hits]


def resolve_embedder(name: Optional[str]) -> Any:
    """`model2vec` (the blessed extra) for a publishable entry; `hashing` runs but can never be
    published (pin 1)."""
    if not name:
        return None
    from mokata.memory.embed import make_embedder
    return make_embedder(name, degrade_out=lambda _m: None)


RetrieverFactory = Callable[[str, str], Any]
