# Developer guide

mokata is a pure-Python package under `src/mokata/`, with one capability model and **no
required runtime dependencies**. `jsonschema` is the only optional dependency and is
degraded over when absent. Supported Python: **3.9–3.12**.

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
lexical floor), `graph_backend.py` (the adopted code-review-graph adapter via an injected
client), `layer.py` (`KnowledgeLayer` — backend chosen through the router, story bridge),
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
`rules.py`, `karpathy.py`, `learning.py`, `authoring.py`, `hooks.py` (G);
`secrets.py`, `gate.py` (WriteGate + trust enforcement), `ledger.py`, `trifecta.py`,
`revert.py`, `resume.py` (I); `trust.py`, `doctor.py`, `lifecycle.py` (K).

### Parts J/K/L — Distribution & composability
`harness.py` (thin cross-harness boundary), `share.py` (export/import stacks),
`compose.py` (chaining + suggestions), `playbook.py` (the end-to-end integration runner),
`packaging.py` (plugin/marketplace validators).

## Dev setup

```bash
git clone https://github.com/JasGujral/mokata-oss && cd mokata-oss
pip install -e ".[mcp,schema]" # editable install + both extras (MCP server + jsonschema), for dev
```

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

CI runs both states across Python 3.9–3.12 plus a `mokata playbook` smoke run. Tests are
written RED-before-GREEN.

## Contributing

See [`CONTRIBUTING.md`](https://github.com/JasGujral/mokata-oss/blob/master/CONTRIBUTING.md)
for the full flow. The non-negotiables: TDD (RED-before-GREEN), **clean-room** (no import
of or text from any other framework), **human-gate every durable write**, local-first, and
Apache-2.0 / MoStack with no vendor-prefixed names. To add a skill/command, register a
`Skill` in `skills.py` and regenerate its template; to add a tool, declare an
`AdapterContract` and wire it through the router.
