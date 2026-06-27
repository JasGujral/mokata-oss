# Changelog

The full, versioned changelog lives in the repository's
[`CHANGELOG.md`](https://github.com/JasGujral/mokata-oss/blob/main/CHANGELOG.md)
(Keep a Changelog format).

> **Re-baselined at 0.0.1** — mokata's inaugural public release. Earlier internal iterations were
> a stabilizing phase and are collapsed into this entry; 0.0.1 is the honest starting point for an
> early, fast-moving project.

## 0.0.1

The inaugural public release — clean-room, local-first, Apache-2.0. A spec-driven, test-first
framework for Claude Code whose spine is a real codebase **knowledge graph**, persistent
**self-healing, shareable memory**, and **human-gated, audited governance**.

- **Knowledge graph** — typed structural queries (callers/callees/blast-radius) over an adopted
  graph with a grep floor; incremental index + `lat-check` drift; an external **Neo4j** adapter
  (degrade-clean to grep).
- **Memory** — persistent + decision + **typed** parts (rule/guardrail/best-practice/context/
  reference), on by default, self-healing; **tiered retrieval** (lexical → graph → semantic, with
  a pluggable embedder / pgvector); **sharing** via export/import, migrate, and a team Postgres
  store mokata owns; **`/mokata:onboard`** guided typed capture; a **team design vault**.
- **Engine & correctness** — the 7-phase pipeline, a provable completeness gate (RED before
  GREEN), spec-persisted precondition, ground-in-code discipline, **spec-awareness regression
  guard**, and a verified `ship` step (never auto-merge).
- **Governance & UX** — universal human-gated writes (secret-scan → approval → audit ledger),
  deviation gate, reversible/resumable, local-first with **zero telemetry**; trust dials;
  parallel-aware progress lanes + an opt-in clickable local **dashboard** (`mokata watch`);
  profiles, per-layer/tool toggles, standalone skills.

**Early & stabilizing:** expect rapid iteration; pin the version if you need stability. No
required runtime dependencies (`jsonschema`/`mcp`/`postgres`/`neo4j` are optional extras); the
suite passes with `jsonschema` both absent and present.
