# Concept: the knowledge layer

mokata orchestrates a codebase graph — it **never builds a parser**. Structural queries
return one typed shape regardless of which backend answers, the backend is chosen through
the capability router, and stale results are surfaced rather than served silently.

> **Wire one →** [Use a codebase graph](../how-to/use-a-codebase-graph.md) — detect, install,
> and point the project at a graph tool, and the structural queries it unlocks (with grep as
> the always-safe floor). `mokata status`/`doctor` tell you which you're on.

## Grounding discipline — verify, never assume

The graph (and memory) aren't just *available* — every critical skill is instructed to
**decide from the code, not from assumption**. Before asserting anything about types,
signatures, behaviour, conventions, or layout, a skill verifies it against the actual code
(read the source, run the structural queries, check memory). The graph + memory are the
source of truth; where they're absent it reads/greps and **says what it read**. If a fact
can't be determined from the code, mokata **states the assumption and asks** — it never
silently assumes, and it **cites what it verified**. `spec` makes this auditable: before any
acceptance criterion it inspects the real code and emits a **"Verified from code:"** list, so
the spec rests on the codebase, not a guessed interface.

And it's **continuous**: if a decision turns out to rest on an assumption, or the code
contradicts something assumed mid-flight, mokata **stops, surfaces it, confirms with you, and
re-plans** (through the [deviation gate](governance.md), amending the spec so ACs stay
provable) — there is no "assumed and continued" path.

## Typed query API (B2)

Five query kinds, each returning a `QueryResult` (`kind`, `target`, `references[]`,
`backend`, `degraded`, `note`) where each `Reference` is `(path, line, snippet, symbol)`:

| Kind | Question |
|---|---|
| `callers` | who calls this symbol? |
| `callees` | what does this symbol call? |
| `implementers` | which classes subclass/implement this? |
| `imports` | where is this module/symbol imported? |
| `blast_radius` | transitive callers up to `--depth` hops (impact surface) |

```bash
mokata query callers compute
mokata query blast_radius helper --depth 3
```

## Backend selection (B1/B3) — one detection path

The layer resolves `code_graph` through the **router** (`code-review-graph → serena →
ripgrep → grep`) and uses the first present provider:

- A real graph tool (`code-review-graph`/`serena`, or an external **Neo4j** database) → the
  adopted **graph backend** (B1), which delegates all graph work to the external tool via an
  injected client. No parser, no in-house graph.
- Otherwise the **grep floor** (B3) — a dependency-free lexical implementation of the same
  five queries. Results are marked `degraded=True` (approximate but always available).

If the graph backend errors mid-query, the layer degrades to grep rather than failing.

**External graph database (Neo4j).** `neo4j` is an optional `code_graph` provider, never a
hard dependency: the driver is an optional extra, the URI + credentials come from **env vars
only**, and no driver / no `NEO4J_*` env / an unreachable DB ⇒ the layer degrades cleanly to
the grep floor. See [use a codebase graph](../how-to/use-a-codebase-graph.md) for the full
install → wire → `index` → `lat-check` loop. The same wired backend keys memory's live
graph-proximity retrieval tier (see [memory](memory.md)).

## Incremental index + staleness (B4)

`mokata index` builds a per-file fingerprint index (content hash + mtime + size) and
re-indexes **only what changed**. When a query touches a file that changed since indexing,
the result's `note` is annotated with a `STALE: …` warning — staleness is **surfaced,
never served silently**.

```bash
mokata index          # build/refresh; reports added/changed/removed + stale files
```

## Drift anchors / `lat check` (B5)

Optional `@lat: <concept>` comments tie code to concepts registered in a `lat.md`.
`mokata lat-check` flags drift — anchors to unknown concepts (orphans) and registered
concepts with no anchor — and **degrades cleanly** (inactive, exit 0) when there are no
anchors and no registry. It exits 1 on drift, so it's usable as a review gate.

## Per-story bridge (B6)

A story's queries are recorded and can be persisted (via the state surface) so analysis
enriches a durable layer instead of being recomputed each run.
