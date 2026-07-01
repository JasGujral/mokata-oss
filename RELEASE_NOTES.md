# mokata 0.0.6

**Spec-driven, test-first development for Claude Code — with a real codebase knowledge graph,
persistent self-healing memory, and human-gated, audited governance.** Clean-room, local-first,
Apache-2.0 (under MoStack).

> **What's new in 0.0.6 — Windows portability fix (no breaking changes; Linux/macOS unchanged).**
> The Windows CI matrix ran for the first time on the 0.0.5 re-cut and caught two real Windows-only
> bugs (prior green runs were Linux-only). **Fixed:** (1) the SQLite memory backend held a file
> handle across operations, so a temp-dir teardown failed on Windows with
> `PermissionError [WinError 32]` — it now opens a short-lived connection per operation (also a
> genuine resource-leak fix; an in-memory `:memory:` DB keeps its connection, having no file to
> leak); (2) text files written without an explicit encoding landed as cp1252 on Windows (em-dash
> `—` → `0x97`) and broke the utf-8 read — every text-mode file I/O now declares
> `encoding="utf-8"`. **Guarded** on every OS by a lint test (encoding on all text I/O) and a
> portability test (a memory op leaves no lingering file handle). Upgrading is recommended for
> anyone on Windows.
>
> **What's new in 0.0.5 — portable sessions, in-Claude-Code UX, every-agent reach, team sharing &
> supply-chain trust.**
> **Fixed first:** hooks now resolve via a PATH-based `mokata-hook` console entry — the
> `python3: command not found` pre-hook error (Windows / GUI-launched macOS / exotic PATHs) is
> gone. **Portable sessions (the headline):** `mokata session push <tag>` / `pull` / `list` /
> `name` — package your run (checkpoints + approach + in-progress brainstorm + memory) into a
> machine-path-free, secret-scanned, human-gated bundle; start on one machine, resume on another,
> or hand it to a teammate. **Inside Claude Code:** an always-on **stage badge**, gate-verdict
> legibility (why-blocked + how-to-unblock), the parallel-agent **lanes** view + `/mokata:progress`
> / `watch` / `govern`, **full command parity** (every command reachable in-harness, CI-enforced),
> assisted task decomposition, a brainstorm anti-drift anchor, and the native **to-do widget** —
> all one `RunProgress`, many renderers. **Zero-to-wired:** an interactive `/mokata:setup` wizard +
> a re-runnable `reconfigure`. **Smarter memory:** explainable retrieval, health nudges, and
> proposed guardrails (proposal-only). **Every agent, in your editor:** in-harness surfaces for
> Cursor / Copilot / Windsurf / Codex / Gemini CLI / Aider, a **VS Code extension**, a Copilot
> Chat **`@mokata`** participant, and a language + Windows/macOS/Linux matrix. **Teams:** one
> guided `mokata team join`, community **stacks**, shared-or-local **audit logs**, and
> **project-scoped shared backends** (one DB, many projects, no bleed) — all **no-telemetry**. A
> **CI/PR check** GitHub Action brings the completeness gate to every pull request. **Hardened:**
> supply-chain (**SBOM + Sigstore provenance**, least-privilege SHA-pinned CI), a reliability fuzz
> pass + a measured **performance budget**, and release-process version-at-tag verification. No
> breaking changes — upgrading is recommended.

> **Early & stabilizing.** mokata is early and fast-moving; the pre-1.x history was a stabilizing
> phase, re-baselined into the 0.0.x line. Expect rapid iteration; pin the version if you need
> stability.

## Install

**1 — Claude Code plugin (recommended):** add the marketplace and install the `mokata` plugin,
then restart Claude Code. You get the `/mokata:*` commands, the bundled MCP tools, and the
SessionStart briefing + secret-guard hooks.

**2 — Claude Code without the public marketplace (no registration):** install the plugin from a
local clone, **or** run `pip install mokata && mokata setup claude` (copies the commands,
registers `mokata-mcp`, wires the hooks — all human-gated, idempotent, reversible with
`mokata unsetup claude`). Runs on your existing Claude Code sign-in — no API key.

**3 — CLI, with any AI tool / scripting / CI:** `pip install mokata` and use the `mokata`
command (the engine's mechanics; no LLM). Optional extras: `mokata[mcp]`, `mokata[postgres]`,
`mokata[neo4j]`.

No required runtime dependencies; `jsonschema`/`mcp`/`postgres`/`neo4j` are optional and degraded
over. Requires Python ≥ 3.9.

## What makes mokata different

**Knowledge graph & correctness**
- **D1 Codebase knowledge graph** — navigate by structure (callers/callees/imports/blast-radius); grep floor when absent.
- **D22 External Neo4j adapter** — wire a team graph as the `code_graph` provider; degrade-clean to grep.
- **D5 Provable completeness gate** — no code until every acceptance criterion maps to a test (RED before GREEN).
- **D6 Spec persisted + precondition** — no implementation without a saved, reviewable spec.
- **D7 Ground-in-code** — verify from the code, cite "Verified from code", never assume.
- **D8 Spec-awareness regression guard** — a change checked against saved specs + decisions before it can break them.
- **D9 Plan-adherence deviation gate** — never silently deviates; asks first, logs the decision.
- **D18 Verified `ship`** — green + ACs met + review passed, then a human-chosen landing (never auto-merge).

**Memory — the institutional brain**
- **D2 Persistent, self-healing memory** — on by default; surfaces contradictions (old→new), never silently rots.
- **D3 / D17 Shared / team memory** — export/import, migrate (sqlite↔obsidian↔postgres), and a team Postgres store mokata owns.
- **D4 Guided capture** — `/mokata:onboard` LLM-processes rules/guardrails/docs/context into typed memory, referenced just-in-time.
- **D21 Tiered semantic retrieval** — lexical + graph-proximity + semantic (pluggable embedder / pgvector), fused, frugal top-k.
- **D23 Team design & spec vault** — push a named brainstorm/spec → teammates search → pull → review (versioned, gated, secret-scanned).

**Governance, safety & UX**
- **D10 Universal human-gated writes** · **D11 Audit ledger** (review every decision) · **D13 Local-first, zero telemetry**.
- **D12 Active token & cost governance** — JIT retrieval, budget, cache-stable prefixes; frugal by design.
- **D14 Reversible & resumable** · **D15 Configurable & composable** · **D16 Adopt freely, trust nothing** (trust dials).
- **D19 Run-progress UX** + **D24 Clickable run-observability** — parallel-aware lanes and an opt-in self-contained local HTML dashboard (`mokata watch`).
- **D20 Clean-room methodology** — inherits best practices, imports/copies nothing.

See the runnable showcase: **Differentiators in action** in the docs.

## Notes
- The suite passes with `jsonschema` both absent and present; clean-room throughout.
- Storage footprint stays under `.mokata/` (committed config at the root; transient state under
  the gitignored `.mokata/temp_local/`).
