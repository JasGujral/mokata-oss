# Concept: memory (default-on, self-healing)

Memory is a native part of mokata — **on by default**, not an add-on. There are three
individually-toggleable types and every durable write is human-gated.

## The memory triad (C1/C2/C3)

| Type | What it holds |
|---|---|
| `persistent` | project facts / conventions |
| `decision` | project decisions ("why we did X") |
| `episodic` | past conversation turns (searchable) |

Each item carries provenance, a TTL (`expires_at` / `valid_for`), and
`supersedes`/`depends_on` edges; status is `active` / `superseded` / `stale`.

## Pluggable backends (C4) — storage only

Chosen through the router (`memory_store`): **SQLite** (default, stdlib, the guaranteed
floor), **Obsidian** (a markdown vault), or **native-memory** (an adapter delegating to an
injected client). Storage only — the memory *logic* is mokata's own. When native-memory
has no client wired, selection degrades to the SQLite floor.

## Human-gated writes (C6)

Nothing reaches a backend without approval. The write API takes a `confirm` callback or an
`assume_yes` flag; a declined write commits nothing.

## Self-healing by surfacing (C5)

mokata *detects* contradictions (two active items, same subject, different value) and
staleness (elapsed TTL) and **surfaces** each as an old → new diff for you to **approve /
edit / reject**. It **never silently rewrites**; the default is no change. Detection writes
nothing — only an explicit, gated `apply` changes anything.

```bash
mokata memory          # read-only: active items, read/write ratio, pending heal proposals
```

## Consolidation (C7) — proposal-only

A pass that **proposes** merges (duplicates), summaries (episodic clusters), and prunes
(already-stale items) — and **never auto-applies**. Each proposal is an old → new diff,
human-gated like C5, logged to the audit ledger.

## Per-type toggles (C9) & instrumentation (C8)

`settings.memory.{persistent,decision,episodic}` toggle types independently (default on);
disabling the whole `memory` layer turns them all off. A disabled type is refused on write
and never surfaced on read. mokata logs the read/write ratio — if writes ≫ reads, the
feature is failing.

See [how-to: use & heal memory](../how-to/use-memory.md) and the
[configuration reference](../reference/manifest.md).
