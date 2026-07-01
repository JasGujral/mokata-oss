# How-to: use a codebase graph

mokata can answer **structural** questions about your code — who calls a function, who
implements an interface, what the blast radius of a change is — by orchestrating a codebase
graph tool. When no graph is wired it runs on a **grep floor** (lexical, dependency-free):
safe and always available, just not structural. This guide shows how to tell which you're
on and how to wire a real graph.

## Which am I on?

`mokata status` and `mokata doctor` both surface an actionable hint:

```text
# graph wired:
code graph active (code-review-graph) — use `mokata query callers <sym>` / `callees <sym>` /
`blast_radius <sym>` for structural queries.

# only the grep floor:
no codebase graph wired — running on the grep floor (safe, but lexical). To enable richer
structural queries, install a graph tool (code-review-graph or serena) and wire it:
`mokata init --profile full`, or add it via `mokata config set tools.<graph>...` / the manifest.
```

## Wire a graph

1. **Install** a supported graph tool — `code-review-graph` or `serena` (each is an external
   tool mokata orchestrates; neither is a mokata dependency).
2. **Add it to the project** — either start from a profile that wires the full chain:

   ```bash
   mokata init --profile full     # wires code-review-graph → serena → ripgrep → grep
   ```

   or point the manifest at it / set a custom endpoint (Stage 24A config):

   ```bash
   mokata config set tools.code-review-graph.config.endpoint http://localhost:7000
   ```

   A configured path/endpoint is reflected back in the `status`/`doctor` hint so you can
   confirm what's live.
3. **Confirm** with `mokata status` — you should see *code graph active (...)*.

## Wire an external graph database (Neo4j)

If your team already populates a **Neo4j** graph of the codebase, mokata can query it directly
— it becomes an optional provider for the `code_graph` capability, sitting in front of the grep
floor. mokata never builds the graph; it adopts the one you populated. The whole loop is four
steps: **install → wire → `mokata index` → `mokata lat-check`**.

### 1. Install the driver and have a reachable DB

```bash
pip install "mokata[neo4j]"     # or: pip install neo4j   (the driver is an optional extra)
```

mokata queries a **conventional schema** — populate it with whatever indexer you use:

- nodes `(:Symbol {name, path, line})`
- relationships `[:CALLS]`, `[:IMPLEMENTS]`, `[:IMPORTS]`

### 2. Wire it — credentials via environment variables only

Point a few env vars at your DB (mokata **never** stores a URI or password in the committed
manifest — only the *names* of the env vars it should read):

```bash
export NEO4J_URI="bolt://localhost:7687"
export NEO4J_USERNAME="neo4j"
export NEO4J_PASSWORD="…"          # from your secret manager, not committed
```

Then add `neo4j` to the front of the `code_graph` chain (human-gated, previewed before write):

```bash
# register the tool (env-var names only; defaults are NEO4J_URI/NEO4J_USERNAME/NEO4J_PASSWORD)
mokata config set tools.neo4j '{"provides":"code_graph","kind":"external","enabled":true,"detect":{"type":"python_module","name":"neo4j"},"config":{"uri_env":"NEO4J_URI","user_env":"NEO4J_USERNAME","password_env":"NEO4J_PASSWORD"}}'

# put it first in the fallback chain, ahead of the grep floor
mokata config set capabilities.code_graph.fallback '["neo4j","ripgrep","grep"]'
```

Confirm with `mokata status` — you should see *code graph active (neo4j)*. If the driver is
missing, the env vars are unset, or the DB is unreachable, mokata silently stays on the grep
floor instead (see [degrade-to-grep](#degrade-to-grep-is-safe) below) — wiring is never a hard
failure.

### 3 & 4. Keep it fresh — `mokata index`, then `mokata lat-check`

```bash
mokata index        # refresh the freshness index; names the active graph backend
mokata lat-check    # flag concept↔code drift against the wired backend
```

`mokata index` prints which backend the refresh runs against, so the loop is explicit:

```text
# graph wired:
index: code graph 'neo4j' wired — `mokata lat-check` flags drift against it.

# only the grep floor:
index: no code graph wired — refresh runs on the grep floor (`mokata lat-check` still flags
concept drift lexically).
```

Run these whenever the code or the graph changes. Both operate over the **wired adapter when
present and the grep floor when not** — same commands either way.

## The structural queries it unlocks

```bash
mokata query callers <symbol>        # who calls it
mokata query callees <symbol>        # what it calls
mokata query implementers <name>     # who implements/subclasses it
mokata query imports <module>        # who imports it
mokata query blast_radius <symbol>   # transitive impact of a change
```

## Which languages work

The queries above work across **Python, JS/TS, Go, Rust, and Java**. The *real* graph covers
whatever languages the adopted tool supports; the **grep floor** is language-aware on its own
(extension awareness + per-language lexical patterns — `function`/`def`/`func`/`fn`,
`import`/`require`/`use`, `class`/`impl`/`interface`), with **no parser** — it's the
heuristic floor and says so (`degraded`).

| Language | Files | Grep-floor structural queries | AC-tagged tests it finds |
|---|---|---|---|
| Python | `.py` `.pyi` | callers / callees / imports / implementers | pytest `def test_*` |
| JS / TS | `.js` `.jsx` `.ts` `.tsx` | callers / callees / imports / implementers (`extends`/`implements`) | jest/vitest `test(...)` / `it(...)` |
| Go | `.go` | callers / callees / imports *(interfaces are structural — `implementers` degrades)* | `func Test*` |
| Rust | `.rs` | callers / callees / imports / implementers (`impl Trait for Type`) | `#[test]` |
| Java | `.java` | callers / callees / imports / implementers | JUnit `@Test` |
| anything else | any extension | **generic** identifier matching — never crashes | — |

Wire a real graph tool (above) for precise, cross-language structural answers; the floor is
always there underneath so the queries never hard-fail on a stack the graph tool doesn't cover.
An unknown language falls back to **generic identifier matching** (degrade-clean, no crash).

## Degrade-to-grep is safe

If the graph tool is absent or errors mid-query, mokata **falls back to the grep floor**
rather than failing — the same query shape, answered lexically, marked degraded. This holds
for an external DB too: no `neo4j` driver, no `NEO4J_*` env, or an unreachable Neo4j ⇒ mokata
quietly runs on the grep floor and your `mokata query …` / `index` / `lat-check` commands all
still work. You never lose the ability to ask; you only lose structural precision until a graph
is wired. See
[the knowledge layer](../concepts/knowledge.md) and
[configure a profile](configure-a-profile.md).
