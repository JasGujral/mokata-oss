# Developer guide

mokata is a pure-Python package under `src/mokata/`, with one capability model. Supported
Python: **3.10–3.13**.

**Dependencies.** The one required runtime dependency is the **MCP SDK** (`mcp>=1.2`) — it ships
by default so the bundled `mokata-mcp` server works straight out of `pip install mokata`.
Everything else is optional and **degraded over when absent**, never fatal:

| Extra | Pulls | Absent ⇒ |
|---|---|---|
| `schema` | `jsonschema>=4.0` | the built-in structural validator still validates the manifest |
| `postgres` | `psycopg>=3.1` | memory degrades to the SQLite floor |
| `neo4j` | the Neo4j driver | `code_graph` degrades to the ast → ripgrep → grep floor |
| `mcp` | *(no-op alias of the default dep — kept so `mokata[mcp]` still resolves)* | — |

Every optional import is **lazy**, so the core, the CLI and every default profile run with all of
them missing.

## Architecture by package (Parts A–L)

The spine is the conductor; every other layer plugs into it through the manifest, the
capability router, and the unified `Surface`.

### Part A — Spine
- `manifest.py` — load/validate the stack manifest (`Manifest`), accessors for layers,
  capabilities, tools, settings; `layer_enabled`, `tool_enabled`, `capability_enabled`.
- `schema.py` — structural validator (authoritative, dependency-free) + an optional
  `jsonschema` pass that degrades on any failure.
- `detect.py` — `Detector`: is a tool present? (`command`/`python_module`/`path`/`always`),
  with overrides + caching. Absence is a value, never an error.
- `router.py` — `Router.resolve(need)` walks a capability's declared fallback order and
  returns the first present provider, recording the attempted chain (`Resolution`).
- `config.py` — `Surface`: the single governed read surface over `.mokata/` (manifest +
  constitution + router + state store).
- `bootstrap.py` — the SessionStart briefing, capped at a 2,000-token budget.
- `init.py` / `profiles.py` / `cli.py` — `mokata init`, the tool catalog + profiles, the CLI.
- `adapters/` — A6/H4–H6: `AdapterContract` + `negotiate` (coverage/gaps), `MCPRegistry`
  (discovery), `overlapping_capabilities`/`resolve_conflict` (precedence).

### Part B — Knowledge (`knowledge/`)
`query.py` (typed `QueryResult`/`Reference`, 5 query kinds), `grep_backend.py` (the
lexical floor), `ast_backend.py` (the embedded stdlib-AST floor — non-degraded structural
queries on Python, a floor above grep), `graph_backend.py` (the adopted code-review-graph
adapter via an injected client), `layer.py` (`KnowledgeLayer` — backend chosen through the router, story bridge),
`index.py` (incremental fingerprint index + staleness surfacing), `anchors.py` (`@lat`
drift anchors + `lat_check`).

### Part C — Memory (`memory/`)
`item.py` (`MemoryItem` + the three types), `backends.py` (`SQLiteBackend` default,
`ObsidianBackend`, `NativeMemoryBackend`), `store.py` (the logic: gated writes, toggles,
instrumentation, consolidation), `healing.py` (surfacing detection), `episodic.py`
(searchable turns, lexical fallback), `consolidation.py` (proposal-only).

### Part D — Engine (`engine/`)
`spec.py`, `acmapper.py` (AC → test traceability), `completeness.py` (the blocking gate),
`premortem.py` (risk probes), `phases.py` (analysis/strawman + `run_pipeline`),
`compliance.py` (spec-compliance review), `preview.py` (zero-side-effect dry-run).

### Part E — Execution (`execmode/`, `modes/`)
`selector.py` (per-run mode choice), `tasks.py`, `orchestrator.py` (isolation, fan-out,
handback cap, degrade), `review.py` (two-stage), `routing.py` (cheapest-capable model +
escalation); `modes/bug.py`, `modes/debug.py`, `modes/optimize.py`.

### Parts F/G/I — Governance (`govern/`)
`tokens.py`, `retrieval.py`, `compaction.py`, `compress.py`, `budget.py`, `cache.py` (F);
`rules.py`, `karpathy.py`, `learning.py`, `authoring.py`, `hooks.py`, `enforce.py` (G);
`secrets.py`, `gate.py` (WriteGate + trust enforcement), `ledger.py` (hash-chained),
`trifecta.py`, `deviation.py`, `outbound.py`, `revert.py`, `resume.py`, `tdd.py` (I);
`trust.py`, `doctor.py`, `lifecycle.py` (K).

### The seatbelt — enforcement outside our own tools
The gates above all fire *inside* mokata's tools; these modules are what stop the model simply
reaching past them. Read `gate_hook.py`'s module docstring first — it is the design record.

- `approval.py` — the **human-minted approval**: `propose` / `redeem`, content-hashed and
  session-scoped proposal ids. `approve=true` on a tool call is inert by construction; only
  `mokata approve <id>` (a human, out-of-band) mints one, and it licenses exactly one commit.
- `gate_hook.py` — the **decision** for the four run-state gates (`approach-approval`,
  `spec-persisted`, `no-code-without-failing-test`, `spec-scope`) on a native `Write`/`Edit`.
  Pure, total, never raises; every uncertainty (no run, ambiguous run, unreadable state,
  undeclared scope) resolves to **ALLOW**.
- `hook_cli.py` — the I/O for all three shipped hooks (`session-start`, `secret-guard`,
  `gate-guard`), launched via the `mokata-hook` console entry point. Blocks with exit code 2.
- `spec_scope.py` — a spec's authorized surface + its **deferred** items (paths and literal
  markers), and `classify()`, the pure verdict the scope gate reads. Plus the amend record.
- `tdd_state.py` / `session_state.py` / `session.py` — the persisted RED/GREEN record, the
  run-scoped (`__<run_id>`) state keys, and the minted per-process session identity that makes
  two windows on one repo distinguishable.
- `degrade.py` — the degrade registry: a capability that falls back to a floor is *remembered*,
  so `doctor` can say so instead of letting a silent fallback pass for the real thing.

### Parts J/K/L — Distribution & composability
`harness.py` (thin cross-harness boundary), `harness_setup.py` (`mokata setup <harness>` —
commands + MCP + the three hooks), `share.py` (export/import stacks), `compose.py` (chaining +
suggestions), `playbook.py` (the end-to-end integration runner), `packaging.py`
(plugin/marketplace validators), `team*.py` (the opt-in shared Postgres store).

## Dev setup

```bash
git clone https://github.com/JasGujral/mokata-oss && cd mokata-oss
pip install -e ".[schema]"     # editable install + jsonschema; the MCP SDK is a default dep
```

*(End users never clone: `pip install mokata` → `mokata setup claude`.)*

## Running the tests (BOTH jsonschema states)

A hard invariant: the suite must pass with `jsonschema` absent **and** present.

```bash
# absent
pip uninstall -y jsonschema
python -m unittest discover -s tests -t tests

# present
pip install "jsonschema>=4.0"
python -m unittest discover -s tests -t tests
```

CI runs both states across Python 3.10–3.13 plus a `mokata playbook` smoke run. Tests are
written RED-before-GREEN.

## Contributing

See [`CONTRIBUTING.md`](https://github.com/JasGujral/mokata-oss/blob/main/CONTRIBUTING.md)
for the full flow. The non-negotiables: TDD (RED-before-GREEN), **clean-room** (no import
of or text from any other framework), **human-gate every durable write**, local-first, and
Apache-2.0 / MoStack with no vendor-prefixed names. To add a skill/command, register a
`Skill` in `skills.py` and regenerate its template; to add a tool, declare an
`AdapterContract` and wire it through the router.
