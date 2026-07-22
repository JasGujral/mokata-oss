# How-to: use & heal memory

Memory is on by default on `standard`/`full`. It is **human-gated** and **self-healing by
surfacing** — it never silently rewrites.

## Inspect (read-only)

```bash
mokata memory     # active items, read/write ratio, pending proposals + the health nudge
```

When the store needs attention `mokata memory` (and the `mokata govern` view) print a
one-line **health nudge** — `N stale · M contradictory · K unused — review with mokata memory
/ mokata govern` — pointing at the gated review path. It is read-only and **proposal-only**:
it never edits or prunes memory, and it's silent when the store is healthy.

## Back it up & restore it — `memory export` / `memory import`

Memory is a durable asset, so it has a **backup** surface. `export` writes a committable,
human-readable JSON file **you own**; `import` restores one through the gate.

```bash
mokata memory export                       # → .mokata/backups/memory-<UTC>.json
mokata memory export ./team-brain.json     # or name the destination
mokata memory import ./team-brain.json     # human-gated restore (--yes to skip the prompt)
```

The default destination is `.mokata/backups/memory-<UTC>.json` — **not** under `temp_local/`, so
it's committable, and it is UTC-stamped to the microsecond so successive backups **never clobber**
each other. Export is read-only on the source and carries provenance with each item.

`import` is a **restore**, not a merge-and-hope: it previews (counts + a keys-only sample), dedups,
surfaces each new item for approval, and routes a **conflict** (same subject, different value)
through the self-healing old→new surface — never a silent overwrite. Every restored item lands
through the one WriteGate, secret-scanned and stamped with import provenance, so a round trip is
content-identical.

!!! note "Backup ≠ sharing"
    This is a **backup** surface. Cross-repo/team sharing is the team Postgres store (see
    [team setup](team-setup.md)). The old `memory-share.json` channel still works as a destination
    but is **deprecated** (removal: 0.0.17) and warns once — fold it into the canonical store with
    the one-time, human-gated `mokata migrate memory-share`.

## How recall actually ranks — the retrieval tiers

A recall fuses up to three tiers, and mokata tells you **which engines are really ranking your
results** rather than letting two installs both say "memory: ok":

| Tier | What runs | Notes |
|---|---|---|
| **lexical** (always on) | `fts5` (SQLite FTS5 + bm25) or `tsvector` (Postgres tsvector + ts_rank) — ranked **in the database** | degrades honestly to `jaccard`, a Python keyword-overlap floor, when FTS5 is absent |
| **graph-proximity** (optional) | a code-graph-keyed boost | off unless a graph is wired |
| **semantic** (opt-in) | embedding cosine over the vector index | `off` by default; `hashing` is the zero-dep floor and is **honestly labelled "token-hash overlap, NOT meaning"** |

`mokata doctor` prints the live retrieval-stack line, so you never have to guess. It is
**informational** — `hashing` + `jaccard` is a legitimate, working zero-dependency install, not a
failure, and it never affects doctor's exit code.

### Turning on real semantics (consented, not default-on)

The embeddings tier is **opt-in**. mokata **asks before installing anything** — an extra is a real
install that runs `pip` and may touch the network, so it goes through the same consent discipline
as any durable change:

```bash
pip install 'mokata[embeddings]'      # or accept the offer when mokata asks
```

`mokata init --mode memory` and `--mode full` **offer** the local embeddings model when run
interactively; `--mode seatbelt` structurally never does. The prompt **fails closed off a TTY** (an
unanswered prompt is not consent), the install is one **bounded** subprocess, and success is decided
by whether the module actually **imports** — not by pip's exit code. A **decline is remembered**
(user-scoped, so it survives a re-clone) and you are not asked again.

Changed embedder? Vectors must never silently mix, so the runtime **refuses** a mismatched index
(the semantic tier goes off and recall falls to lexical). The way out is the gated migration:

```bash
mokata memory reembed        # previewed (the item count + old→new embedder), then human-gated
```

## Explainable recall — "why did this surface?"

A by-relevance recall names *why* each hit surfaced (matched token / graph anchor / semantic
neighbour / kind):

```python
from mokata.memory import explain_recall
hits = store.recall_relevant("auth token rotation")   # or jit_recall(store, query)
for e in explain_recall("auth token rotation", hits):
    print(e.line())     # - auth.policy: rotate tokens daily  ↳ [context] matched "auth"
```

Inside Claude Code, `recall(query="…")` returns each hit with its `why`. The explanation is
deterministic and read-only; one short phrase per hit, so the top-k frugality bound holds.

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
