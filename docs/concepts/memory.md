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

## Typed memory — the project "brain" (Stage 36)

On top of the storage type, each item carries a first-class **`kind`** — the institutional
knowledge a team wants mokata to honour, captured via the guided **`/mokata:onboard`**
([how-to](../how-to/capture-project-rules-and-context.md)) and stored **structured, not
verbatim** (the LLM distils, types, normalises, and dedups the input):

| kind | holds | surfaced |
|---|---|---|
| `rule` | hard rules | **always-on** (briefing every run) |
| `guardrail` | safety/quality constraints | **always-on** |
| `best-practice` | recommended patterns | **JIT** (when relevant) |
| `context` | domain facts / formulas | **JIT** |
| `reference` | distilled doc key-points + source | **JIT** |

See the whole brain grouped by kind with `mokata memory [--kind <k>]`, and update any entry —
human-gated, routed through self-healing — with `mokata memory edit <subject>`.

**Frugal surfacing (P11) is the point.** Only `rule`/`guardrail` go **always-on**, injected
into the SessionStart briefing within the rules **line budget** — if more are captured than
fit, the most relevant are shown and the rest flagged (`mokata memory --kind rule`); the budget
is **never** blown. `context`/`reference`/`best-practice` are retrieved **just-in-time** — only
the entries relevant to the task in play, never the whole corpus. More captured knowledge, not
more tokens per run.

## Pluggable backends (C4) — storage only

Chosen through the router (`memory_store`): **SQLite** (default, stdlib, the guaranteed
floor), **Obsidian** (a markdown vault), **native-memory** (an adapter delegating to an
injected client), or **Postgres** (a shared database — below). Storage only — the memory
*logic* is mokata's own. When a richer backend isn't reachable, selection degrades to the
SQLite floor.

### Shared team memory — Postgres (mokata owns the schema)

Point a whole team's mokata at one **Postgres** DSN and everyone reads/writes the **same
memory store, live**: a new teammate inherits the team's decisions and conventions
immediately, and one person's update is seen by all. mokata **owns the schema** (it creates
its own namespaced **`mokata_memory`** table and implements full CRUD), so this is a real
managed store, not a pass-through — and the dedicated name means mokata can share a database
with an app that has its own `memory` table without colliding.

> **Migration note:** before 0.0.1 the table was the generic `memory`; it is now `mokata_memory`.
> If you ran an early build against a shared Postgres, rename it once —
> `ALTER TABLE memory RENAME TO mokata_memory;` — or let mokata create the new (empty) table and
> `mokata memory migrate` your items into it. The team guarantees still hold against the shared store: writes are
**human-gated** and **provenance-carrying** (who/when/source), and two clients disagreeing
surface a **contradiction** (the self-healing old→new diff) rather than silently merging.

It's **opt-in and local-first** (P8): nothing connects unless you wire it, the DSN comes from
an **environment variable** (`tools.postgres.config.dsn_env` — never inline in the committed
manifest), the adapter is trust-dialed, and if `psycopg` (the optional `mokata[postgres]`
extra) is absent or the DB is unreachable it **degrades to the SQLite floor** — never a hard
failure. The same `MemoryBackend` contract generalizes to any database. See
[configure storage backends & paths](../how-to/configure-storage-backends.md).

### Share by file — `memory export` / `memory import`

For teams who don't run a shared DB, `mokata memory export` writes a **committable** share
artifact (`.mokata/memory-share.json`, at the root — not `temp_local/`) carrying the active
items **with provenance**; it's read-only on the source. A teammate runs `mokata memory
import <file>` — a **human-gated** merge that dedups, gate-adds new items, and routes a
conflicting fact through the self-healing old→new surface (**never a silent overwrite**),
preserving provenance. It's opt-in and local-first: nothing egresses — sharing rides the
file/VCS the team already uses. (MCP: `memory_export` / `memory_import`, propose-only.)

### Move the store — `memory migrate`

`mokata memory migrate --to <backend>` ports the **live store** between `sqlite` / `obsidian` /
`postgres` — e.g. your local SQLite onto the team's shared Postgres (the on-ramp to the live
store above), or into the Obsidian vault, and back. It's **human-gated**, **idempotent**
(re-run upserts by id), and **non-destructive** (the source stays unless you add the separately
gated `--drop-source`); if the destination can't be built it reports and writes nothing — your
data is never lost. Where `export/import` shares content as a *file*, `migrate` moves the
*store* between databases. See [the CLI reference](../reference/cli.md).

## Tiered retrieval — lexical → graph → semantic

`recall_relevant(query)` pulls the memory *relevant to the task*, not the whole corpus (P11),
by fusing up to three tiers into one ranked, top-k result with a deterministic ordering:

1. **lexical** — keyword overlap; the always-present **floor** (zero deps).
2. **graph-proximity** — a code-graph-keyed boost that is **live by default**: when a memory
   store is built from a repo, it auto-wires the [knowledge layer](knowledge.md), so an item
   referencing a symbol the **code graph confirms** is real and related to the query is lifted.
   It degrades clean — on the grep floor (no real graph) the tier silently contributes nothing
   and lexical + semantic hold. With an external graph (e.g. [Neo4j](../how-to/use-a-codebase-graph.md))
   wired, the boost is keyed on that graph.
3. **semantic** — embedding similarity (the top tier), via a **vector backend** (pgvector —
   mokata owns the `mokata_memory_vectors` schema and queries the index, no full-store scan)
   or, for any other backend, the embedding **stamped on each item at write time**.

The **embedder is a pluggable seam**: the default `HashingEmbedder` is deterministic, local,
and dependency-free, so semantic recall works with **zero deps and no network**; real
providers are wired by `settings.memory.embedder`. It's **opt-in** — with **no embedder
configured the semantic tier is simply OFF** and lexical (+ graph) still work. Degrade-clean
end to end: no `psycopg` / no `pgvector` / no embedder ⇒ semantic silently absent, nothing
crashes, no partial writes. Frugal: embeddings are computed **once, on the gated write**;
recall embeds only the query and returns only the top-k.

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
