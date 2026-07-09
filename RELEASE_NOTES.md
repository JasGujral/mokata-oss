
mokata **0.0.12 — "Legible skills + native domain knowledge."** Upgrade with `pip install -U mokata`.
Additive; **no breaking changes**; local stays the zero-config default; **no schema change**. Requires
**Python ≥ 3.10**.

This release makes mokata's pipeline visible and continuous, adds ten clean-room domain-knowledge
skills that attach to the phase where they apply, and ships a new skill that keeps your docs true to
your code.

**Skills are now legible.**

- Every skill carries a `## Contract` — what it CAN do, what it MUST NOT, and which **real gate** backs
  each boundary — plus an active-skill banner, single-sourced so the statusline, in-chat surface, and
  `mokata progress` always agree. Each skill also gains an anti-rationalization table and a
  verification checkbox, and skills **auto-engage** when the moment fits. `mokata skills` now lists
  the **complete** curated catalog — runnable pipeline skills plus the standalone/auto-firing ones
  (`docsync`, `govern`, `session`, `playbook`, `mcp-repair`), with detail and search.

**Ten native domain-knowledge skills.**

- API design, security & hardening, performance, frontend/accessibility, browser testing, CI/CD, git
  workflow, deprecation & migration, documentation/ADRs, and shipping & launch. Each **attaches to the
  pipeline phase where it applies and feeds the gate already running there** — security items are
  hard-enforced rules, an API contract change walks its blast radius, a deprecation records to the
  ledger. Authored **clean-room from primary sources** (OWASP, RFCs, web.dev/Core Web Vitals,
  MDN/WCAG, Google engineering practices) with cited URLs.

**`mokata docsync` — keep docs true to the code.**

- Point it at a doc (`mokata docsync <path>`) or let it find the relevant docs; it **audits** every
  claim against the code (commands, config keys, skill counts, install path, versions) and highlights
  drift with severity, then offers **human-gated** fixes — preview the diff, write only on approval.
  It also **auto-fires** when a change touches a documented symbol.

**Develop shifts problems left.**

- On a non-trivial ambiguity, develop now **stops, asks one question, and amends the spec
  (human-gated)** before continuing — instead of assuming and surfacing the issue at review.
  Brainstorm gains a design pre-mortem and a doc-freshness check.

**Fixes.**

- Hooks resolve reliably under a GUI-launched minimal PATH (the SessionStart briefing + secret-guard
  no longer silently skip). Team-mode memory resolves conflicting scoped items to a single winner. A
  team-Postgres read-through cache keeps retrieval and gates from blocking on the network.

**Hardening & docs.**

- CI dependency installs are **hash-pinned** (`--require-hashes`) for a stronger supply-chain posture,
  and a new developer **"How it works"** documentation section explains the pipeline, gates, knowledge
  graph, memory, governance, and the domain-skills layer.

Local-first, no telemetry, Apache-2.0.
