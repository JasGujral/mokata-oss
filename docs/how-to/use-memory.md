# How-to: use & heal memory

Memory is on by default on `standard`/`full`. It is **human-gated** and **self-healing by
surfacing** — it never silently rewrites.

## Inspect (read-only)

```bash
mokata memory     # active items, read/write ratio, and pending self-healing proposals
```

## Record facts/decisions (gated)

Programmatically, every write goes through the gate:

```python
from mokata.config import Surface
from mokata.memory import MemoryStore, MemoryItem, DECISION

store = MemoryStore.from_surface(Surface.load("."))
store.remember(MemoryItem.create("db.engine", "postgres"), assume_yes=True)
store.remember_decision("api.style", "REST", assume_yes=True)
```

## Self-healing (C5) — surface, then approve/edit/reject

```python
for p in store.detect_issues():          # read-only: detects, writes nothing
    print(store.render_proposal(p))      # old → new diff
    store.apply_proposal(p, "approve", assume_yes=True)   # or "edit" / "reject"
```

`detect_issues()` finds contradictions and stale facts; nothing changes until you apply,
and the default is no change.

## Consolidation (C7) — proposal-only

```python
for p in store.propose_consolidations():   # merge dupes / summarize / prune
    store.apply_consolidation(p, "approve", assume_yes=True)
```

It **never auto-applies**; both proposals and decisions are logged to the audit ledger.

## Episodic search (C3)

```python
from mokata.memory import EpisodicMemory
epi = EpisodicMemory(store)
epi.record("session-1", "we chose postgres as the database engine", assume_yes=True)
epi.search("which database did we choose")   # embeddings optional; lexical fallback
```

## Toggle a type off

Set `settings.memory.episodic: false` (etc.) in the manifest — disabling a type refuses
its writes and never surfaces it on read. See [memory concepts](../concepts/memory.md).

## Change where memory is stored

Point the backend at a custom SQLite path, an external Obsidian vault, or a hosted Postgres
database — see [configure storage backends & paths](configure-storage-backends.md).
