
mokata **0.0.11 — "Team mode."** Upgrade with `pip install -U mokata`. Additive; no breaking
changes; **local stays the zero-config default**. Requires **Python ≥ 3.10**.

A shared, governed team brain — on your team's **own** Postgres, with nothing ever phoned home.

**Run mode, first-class and visible.**

- **`mokata mode` / `mokata mode set local|team`:** the run mode is now an explicit property of every
  session. `mokata mode` shows it plus a **team-readiness preflight**; `set local` is a zero-config
  no-op that writes nothing on an already-local repo; `set team` runs a **fail-closed** preflight — a
  usable run identity, `$MOKATA_PG_DSN` present, the DB reachable within a ≤500ms probe, and a
  compatible schema — and only then activates (never half-activated). The mode is surfaced in the
  status badge, the SessionStart briefing, and `mokata doctor`.

**Team setup on your own backend.**

- **`mokata team init`** is first-time setup and the **sole owner of DDL:** it guides a backend pick
  (`--backend managed|compose|local`), fails closed with a named fix when `$MOKATA_PG_DSN` is unset,
  provisions the shared tables idempotently on **vanilla Postgres ≥14 (no extensions)**, pins the
  team project identity, and runs a live CONNECTED test.
- **`mokata team join <source>`** is the new-member onboarding path (a joiner never runs DDL): it
  chains **adopt → connect → activate → vault pull → onboard → consent → doctor**, each a confirmable
  step, inheriting the pinned team project id. The steps ship individually too: `status`, `adopt`,
  `connect --dsn-env <ENV>`, `disconnect`. The DSN **value is never stored** (only the env-var name),
  and **mokata hosts nothing**.

**Shared memory over Postgres.**

- A team-shared store with a **scope hierarchy** (personal → project → team → global), **typed items**
  (rule / guardrail / best-practice / context / reference / decision) each carrying an **enforcement
  binding** (advisory / soft / hard), **in-run hard-rule enforcement**, and shared **formulas**.
- **`mokata memory promote`** moves a rule's enforcement binding (human-gated); **`mokata memory
  review`** runs the proposal workflow (Draft → In-Review → Approved, proposer ≠ approver).

**Journal-first team writes, conflict-safe.**

- Every durable team write lands in a **crash-safe local journal first**, so **offline never blocks**
  and nothing is lost. **`mokata sync`** flushes + reconciles: each write **inherits the ledger id of
  its original human approval** (never a governance bypass) and is re-secret-scanned, and each memory
  write is **compare-and-set** on a revision column so a concurrent-writer conflict **surfaces through
  the human gate** — never a silent last-writer-wins.

**Governance & release safety.**

- **Scoped-consent access** to the shared brain; **`mokata audit --consent show|grant|revoke`**
  manages a revocable standing consent for the batched audit-publish (per-publish secret-scan still
  hard-blocks).
- **`mokata branch-protection-check`** verifies the public default branch is protected — no
  force-push, no deletion, required checks — **fail-closed** (exit 1 if unprotected or unverifiable).
- A **team ops kit:** `docker-compose.team.yml` + `.env.example` for self-hosting the shared Postgres,
  and an `llms.txt` at the docs root.

**Also.**

- The in-Claude-Code MCP repair skill is renamed to **`/mokata:mcp-repair`**.
- `mokata setup claude` surfaces an explicit **permission-grant** step when wiring the MCP server.

Local-first, no telemetry, Apache-2.0.
