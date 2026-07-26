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

The layer resolves `code_graph` through the **router** (`code-review-graph → serena → ast →
ripgrep → grep`) and uses the first present provider:

- A real graph tool (`code-review-graph`/`serena`, or the deprecated external **Neo4j**
  database) → the adopted **graph backend** (B1), which delegates all graph work to the
  external tool via an injected client. No parser, no in-house graph.
- Else, on a repo with Python, the **embedded stdlib-AST floor** answers structurally
  (`degraded=False`, `is_graph=False`) — a floor above grep with real name-resolution, never the
  adopted structural graph.
- Otherwise (a zero-Python repo / non-Python files) the **grep floor** (B3) — a dependency-free
  lexical implementation of the same five queries. Results are marked `degraded=True`
  (approximate but always available).

If the graph backend errors mid-query, the layer degrades to the AST floor (then grep) rather
than failing — a graph rebuild failure answers from the AST floor on current files, never from
stale graph data.

**The hint names the backend that actually answered.** `mokata doctor` / `mokata status` /
`mokata graph status` key their one-line guidance on the **real answering backend**, three
ways — adopted graph, embedded AST floor, or grep floor — and use the same
`floor '<backend>'` vocabulary, so the surfaces cannot disagree. A default Python repo with no
graph tool installed is told the truth: the **AST floor is answering structurally** (real
call/import edges, `degraded=False`, nothing to install) and `mokata query callers` /
`callees` / `blast_radius` work **today** — while still naming the honest next step (adopt a
real graph for cross-language and dynamic edges). It is **not** told it is "running on the
grep floor", because it isn't.

## Language coverage (Stage 65)

mokata's structural paths work across **Python, JS/TS, Go, Rust, and Java** — not just
Python. The **real graph** comes from the adopted tool (whatever languages it supports); the
**grep floor** is language-aware via a central, dependency-free table of *lexical heuristics*
(`mokata/languages.py`) — extension awareness plus per-language patterns for
`function`/`def`/`func`/`fn`, `import`/`require`/`use`, and `class`/`impl`/`interface`. Above
grep sits the **embedded stdlib-AST floor** (`is_graph=False`): real name-resolution on Python,
answering `degraded=False` — a floor above grep, never the adopted structural graph. **No
in-house structural *graph*** — the AST floor and the grep heuristic are floors; the real graph
is always the adopted tool. The grep floor announces itself as lexical (`degraded=True`); a
zero-Python repo gets byte-identical grep behaviour.

| Language | Extensions | Real graph (adopt) | Grep-floor heuristics | Tests recognised |
|---|---|---|---|---|
| Python | `.py` `.pyi` | code-review-graph / serena | `def`/`class`, `import`/`from`, `class X(Base)` | pytest `def test_*` |
| JS / TS | `.js` `.jsx` `.ts` `.tsx` `.mjs` `.cjs` | code-review-graph / serena† | `function`/method, `import`/`require`, `extends`/`implements` | jest/vitest `test(...)`/`it(...)` |
| Go | `.go` | code-review-graph / serena† | `func`, `import`, *(interfaces are structural → no `implements` to match → degrades)* | `func Test*` |
| Rust | `.rs` | code-review-graph / serena† | `fn`, `use`/`mod`, `impl Trait for Type` | `#[test]` attribute |
| Java | `.java` | code-review-graph / serena† | method/`class`, `import`, `extends`/`implements` | JUnit `@Test` |
| *unknown* | *any other* | — | **generic** identifier matching (def/func/fn/class/…) — never crashes | — |

† Whether a given graph tool covers a language is the tool's matter; mokata adopts it and
falls back to the language-aware grep floor where it doesn't. A grep heuristic is approximate
by design — it's the floor, and the real graph adapter is always preferred when wired. An
unknown extension degrades to **generic identifier matching** (no crash); a language without a
given convention (e.g. Go `implements`) simply returns nothing for that query rather than
guessing.

**External graph database (Neo4j) — deprecated (removal in 0.0.17).** `neo4j` is an optional
`code_graph` provider, never a hard dependency: the driver is an optional extra, the URI +
credentials come from **env vars only**, and no driver / no `NEO4J_*` env / an unreachable DB
⇒ the layer degrades cleanly to the floors. It **still works**, and first use prints a
**once-per-repo deprecation warning**; the canonical code graph is the embedded AST floor or
an adopted `code-review-graph`. There is **no migration command** for it and none is needed —
a code graph is **derived data**, so you simply re-index with your current backend. See
[use a codebase graph](../how-to/use-a-codebase-graph.md) for the full install → wire →
`index` → `lat-check` loop. An adopted graph also keys memory's live graph-proximity
retrieval tier (see [memory](memory.md)).

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
