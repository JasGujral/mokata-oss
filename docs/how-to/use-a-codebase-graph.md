# How-to: use a codebase graph

mokata can answer **structural** questions about your code — who calls a function, who
implements an interface, what the blast radius of a change is — by orchestrating a codebase
graph tool. There are three tiers, tried in order:

1. an **adopted graph** (`code-review-graph` / `serena`) — full, cross-language structural
   precision;
2. the **embedded stdlib-AST floor** — ships with mokata, zero-dependency, answers structural
   queries **cleanly (`degraded=False`)** on Python out of the box;
3. the **grep floor** — the universal lexical emergency floor beneath everything, marked
   `degraded=True`.

**Graph is mandatory-by-default.** `settings.graph.required` defaults to **true**, so
mokata will *refuse to present a degraded (grep-floor) blast radius as decision input* — you
either adopt a real graph or explicitly accept the degraded evidence for the session (see
[Graph is mandatory-by-default](#graph-is-mandatory-by-default) below). This guide shows how to
tell which tier you're on and how to wire a real graph.

## Which am I on?

`mokata status` and `mokata doctor` both surface an actionable hint:

```text
# graph wired:
code graph active (code-review-graph) — use `mokata query callers <sym>` / `callees <sym>` /
`blast_radius <sym>` for structural queries.

# the embedded AST floor (the default on a Python repo with no graph adopted):
code graph: floor 'ast' — the embedded AST floor is answering structurally (real Python
call/import edges, no install needed) and you can run `mokata query callers <sym>` /
`callees <sym>` / `blast_radius <sym>` today. It is not a full graph: adopt one for
cross-language and dynamic edges (plus semantic search) — `mokata graph adopt`, or
`mokata init --profile full`.

# only the grep floor:
code graph: floor 'grep' — no codebase graph wired, and no AST floor here (the embedded AST
floor answers Python repos), so answers are lexical (safe, but approximate). To enable richer
structural queries, install a graph tool (code-review-graph or serena) and wire it:
`mokata init --profile full`, or add it via `mokata config set tools.<graph>...` / the manifest.
```

> The hint names the backend that **actually answers** — the same vocabulary `mokata graph
> status` uses — so the two surfaces can't disagree.

## Graph is mandatory-by-default

`settings.graph.required` defaults to **true**. When a decision input — a Lens-1 blast radius, a
`spec-check` touch-set, a domain classification — resolves only to the **grep floor** (degraded),
mokata **refuses it** rather than letting a lexical guess drive a decision. You get two roads out:

- **Adopt a real graph** — `mokata graph adopt [code-review-graph|serena]` (default
  `code-review-graph`) pins it into the manifest through the human gate. The embedded AST floor
  stays the fallback, so adoption is *recommended, never required*.
- **Accept the degraded evidence for this session** — pass `--allow-degraded` (e.g. on
  `mokata spec-check`). It is **TTY-reconfirmed** (a model cannot type it), **session-scoped**,
  and **ledgered**, and the result stays marked degraded — with `--reason "<why>"` recorded.

Inspect the live state any time:

```bash
mokata graph status    # which backend actually answers today (graph / AST floor / grep), degrade-clean
```

The AST floor answering `degraded=False` on Python is **not** a degraded state — it is only the
*grep* floor that trips the `graph.required` refusal.

## Wire a graph

The recommended path is `code-review-graph` **with its embeddings extra** — that enables
semantic (hybrid FTS + local-embedding) symbol search on top of the structural queries:

1. **Install** it (an external tool mokata orchestrates — not a mokata dependency):

   ```bash
   pip install "code-review-graph[embeddings]"
   ```

   The `[embeddings]` extra uses CRG's **bundled local model** (`all-MiniLM-L6-v2`) — no API
   key, no network egress at query time. Skip the extra and structural queries still work;
   mokata prints the install hint for the semantic tier and degrades cleanly.

2. **Adopt it** (gated — pins the tool into the committed `code_graph` chain):

   ```bash
   mokata graph adopt code-review-graph
   ```

   If the tool isn't installed yet, `graph adopt` offers an assisted install first; on adopt it
   also provisions the semantic index when the extra is present. `serena` is the supported
   alternative: `mokata graph adopt serena`.

   Wiring alternatives: on a **fresh** repo, `mokata init --profile full` wires the whole chain
   (`code-review-graph → serena → ast → ripgrep → grep`); on an **already-initialized** repo use
   `mokata reconfigure --profile full` (see
   [configure a profile](configure-a-profile.md#switch-an-existing-repos-profile-eg-up-to-full)),
   or point the manifest at a custom endpoint (Stage 24A config):

   ```bash
   mokata config set tools.code-review-graph.config.endpoint http://localhost:7000
   ```

   A configured path/endpoint is reflected back in the `status`/`doctor` hint so you can
   confirm what's live.

3. **Confirm** with `mokata graph status` — the precise report of which backend answers
   (adopted graph / AST floor / grep) — or `mokata status` for the one-line hint.

## Wire an external graph database (Neo4j) — REMOVED in 0.0.18

!!! warning "The Neo4j backend was removed in mokata 0.0.18"
    mokata briefly supported an external **Neo4j** graph as an optional `code_graph` provider. It
    was deprecated at 0.0.15 — a third database contradicts mokata's two-stores shape — and
    **removed at 0.0.18**. This section documented how to wire it; the instructions are gone
    because following them no longer works.

    **Nothing of yours was touched.** mokata only ever *queried* a graph your team populated; it
    never built or stored one, so your Neo4j server and its contents are exactly as you left them.
    There was never a `mokata migrate neo4j` and there deliberately never needed to be: a code
    graph is **derived** data, and the canonical one already answers here — the embedded AST floor
    on Python out of the box, or an adopted `code-review-graph` / `serena` for cross-language
    depth (wired above).

    **If a repo's committed chain still names `neo4j`**, mokata says so once, on stderr, then
    answers from the AST floor rather than silently pretending the graph is live. Clear the dead
    entry with:

    ```bash
    mokata reconfigure --remove neo4j     # reversible, human-gated, no residue
    mokata index                          # refresh against what answers now
    ```

### 3 & 4. Keep it fresh — `mokata index`, then `mokata lat-check`

```bash
mokata index        # refresh the freshness index; names the active graph backend
mokata lat-check    # flag concept↔code drift against the wired backend
```

`mokata index` prints which backend the refresh runs against, so the loop is explicit:

```text
# graph wired:
index: code graph 'code-review-graph' wired — `mokata lat-check` flags drift against it.

# only the grep floor:
index: no code graph wired — refresh runs on the grep floor (`mokata lat-check` still flags
concept drift lexically).
```

Run these whenever the code or the graph changes. Both operate over the **wired adapter when
present and the grep floor when not** — same commands either way.

## The structural queries it unlocks

```bash
mokata query defs <symbol>           # where it's defined
mokata query refs <symbol>           # everywhere it's referenced
mokata query callers <symbol>        # who calls it
mokata query callees <symbol>        # what it calls
mokata query implementers <name>     # who implements/subclasses it
mokata query imports <module>        # who imports it
mokata query blast_radius <symbol>   # transitive impact of a change
```

The first six are **navigation** — they replace opening a file or grepping for a name. mokata's
skills are instructed to ask the graph first and to fall back to Read/grep only afterwards,
marking the answer degraded when they do; a lexical navigation answer carries
`grep floor — install code-review-graph for full navigation`.

One honest gap: `code-review-graph` exposes no definition-site query, so `defs` is answered by
the embedded AST floor (exact on Python) even when the graph is adopted. The answer names the
backend that produced it, so you always know which one you got.

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

## Degrade is honest, not silent

If a real graph is absent or errors mid-query, mokata degrades **loudly and in order** — never
to stale data. On a Python repo the **embedded AST floor** carries the structural queries cleanly
(`degraded=False`); only when even that can't answer (a zero-Python repo, or non-Python files)
does it fall to the **grep floor**, marked degraded. A graph rebuild failure answers from the AST
floor on *current* files, never from a stale graph. This holds for an adopted graph too: an
absent binary, a version skew, or an unreachable server ⇒ mokata drops to the next tier and your
`mokata query …` / `index` / `lat-check` commands all still work.

The one place degrade is **not** waved through: a *grep-floor* result offered as **decision
input** (a blast radius, a `spec-check` touch-set, a domain classification). With
`graph.required` on (the default) that is **refused** until you adopt a graph or accept it for
the session with `--allow-degraded` — see
[Graph is mandatory-by-default](#graph-is-mandatory-by-default). You never lose the ability to
*ask*; you only can't let a lexical guess silently drive a decision. See
[the knowledge layer](../concepts/knowledge.md) and
[configure a profile](configure-a-profile.md).
