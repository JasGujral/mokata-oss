# Changelog

The full, versioned changelog lives in the repository's
[`CHANGELOG.md`](https://github.com/JasGujral/mokata-oss/blob/main/CHANGELOG.md)
(Keep a Changelog format).

> **Re-baselined at 0.0.1** — mokata's inaugural public release. Earlier internal iterations were
> a stabilizing phase and are collapsed into this entry; 0.0.1 is the honest starting point for an
> early, fast-moving project.

## 0.0.6

**Windows portability fix. No breaking changes; Linux/macOS behavior unchanged.** The Windows
CI matrix first ran on the 0.0.5 re-cut and surfaced two real Windows-only bugs (prior green runs
were Linux-only). Fixed: (1) the SQLite memory backend held a persistent file handle across
operations, so a tempdir teardown failed on Windows with `PermissionError [WinError 32]` — the
file-backed backend now opens a short-lived connection per operation (an in-memory `:memory:` DB
keeps its connection, having no file to leak); (2) text files written without an explicit encoding
landed as cp1252 on Windows (em-dash → `0x97`) and broke the utf-8 read — every text-mode file
I/O now passes `encoding="utf-8"`. Guarded by a lint test (encoding on all text I/O) and a
portability test (no lingering handle) that run on every OS.

## 0.0.5

**Portable sessions, in-Claude-Code UX, every-agent reach, team sharing & supply-chain trust.
No breaking changes.**
Fixed: hook invocation now uses a PATH-resolved `mokata-hook` console entry (the `python3: command
not found` pre-hook error on Windows / GUI macOS / exotic PATHs is gone). New: portable/shareable
**sessions** (`session push`/`pull`/`list`/`name` — machine-path-free, secret-scanned,
human-gated); an always-on **stage badge** + flow legibility + parallel-agent **lanes** + full
Claude-Code **command parity** (CI-enforced) + task decomposition + brainstorm anti-drift anchor +
native **to-do widget** (one `RunProgress`, many renderers); a **first-run/`reconfigure` wizard**;
**memory intelligence** (explainable retrieval, health nudges, proposed guardrails); a **CI/PR
check** GitHub Action; reach under **Cursor/Copilot/Windsurf/Codex/Gemini/Aider**, a **VS Code
extension** + **Copilot Chat `@mokata`**, language + OS matrix; **team join/sync/audit** +
community **stacks** + **project-scoped shared backends** (one DB, many projects, no bleed — all
no-telemetry). Hardened: supply-chain (**SBOM + Sigstore provenance**, least-privilege SHA-pinned
CI); reliability fuzz pass + a measured **performance budget**; release-process version-at-tag
verification.

## 0.0.4

**Governance transparency, session lifecycle, portability & hardening. No breaking changes.**
New: `mokata govern` (clickable governed-state dashboard — rules/guardrails + memory-by-kind +
proposals, read-only); `mokata audit --why` (what + decision + why timeline; decisions now record
rationale); `mokata sessions`/`resume` + a mid-brainstorm checkpoint (leave a brainstorm and come
back, HARD-GATE intact); opt-in git-worktree isolation for parallel/paused work; cross-harness
portability (claude/codex/cowork adapters + `mokata harness` matrix, degrade-clear); `mokata
version`/`upgrade` (offline info, opt-in update check, human-gated upgrade) + `/mokata:version`.
Hardened: secret guard broadened to 18 formats + fuzz invariant (paths/URLs/UUIDs/hex digests no
longer false-positive); Dependabot/CodeQL/Scorecard/CODEOWNERS; live-DB CI (Postgres+pgvector+
Neo4j containers); README + CLI-reference audit with a docs-vs-code drift guard.

## 0.0.3

**Wires up governance/token features that previously had no runtime path, plus a second
secret-guard precision fix. No breaking changes.** New/reachable: `mokata memory consolidate`
(proposal-only), `mokata skill author` (RED-GREEN-for-docs, human-gated), `mokata playbook
--dense` (output-density compression). Karpathy gates now run per pipeline phase, lethal-trifecta
gating guards a private outbound `vault push`, rules-learning surfaces proposal-only promotions in
`mokata rules`, per-task model routing is opt-in, and the briefing emits a cache-stable prefix —
all off-by-default / degrade-clean / human-gated where they write. **Fixed:** the secret-guard
entropy layer no longer flags long file paths / URLs / UUIDs as secrets (real-secret detection
unchanged; complements the 0.0.2 envelope fix).

## 0.0.2

**Critical fix.** The PreToolUse secret-guard hook scanned the whole hook payload — including
Claude Code's high-entropy `session_id` and `transcript_path` — which tripped the secret detector
and **blocked every Write/Edit/Bash call** for installed plugin users. It now scans **only the
tool's content and target path**, never the envelope metadata. Real-secret detection is unchanged
(secrets in a command, file content, or a `.env`/`.pem` path still hard-block). No feature changes.

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
