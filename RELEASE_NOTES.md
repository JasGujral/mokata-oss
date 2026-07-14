
mokata **0.0.13 — "Correctness & Trust."** Upgrade with `pip install -U mokata`. Additive;
**no breaking changes**; local stays the zero-config default; **no schema change**. Requires
**Python ≥ 3.10**.

This is a correctness release. Nothing here was built to be new — every change fixes silent data
loss, a race, or a gate that could be walked around. The headline is that **mokata's seatbelt is now
enforced rather than advertised**: the gates bind your agent's *native* edits, and the model can no
longer type its own approval.

**Live bugs, fixed.**

- **Team writes never flushed on a custom DSN.** Teams on `team connect --dsn-env CUSTOM` read fine
  but **never flushed a single write** — health, flush and sync were hardwired to `MOKATA_PG_DSN`
  while reads honoured the configured env. Writes journalled forever and nobody was warned. One DSN
  resolver now serves every subsystem.
- **The spec gate could brick every implementation write.** The spec-emit path had no writer reachable
  from any surface, so `spec-check` always said "no saved specs — skipped" — while the gate, which
  *is* wired, blocked **every** implementation write after a real approval, pointing at a surface that
  did not exist. New `mokata spec emit` / `spec show` and a gated `spec_emit` MCP tool close it.
- A **WAL-switch race** (surfaced by the new stress test) that left a losing process on the rollback
  journal behind a false "permanent degrade" notice; **approval misattribution** under a two-process
  race (an approval could name the wrong ledger entry); team access control that **failed open**;
  and a sequential task floor that **fabricated ledger rows** — inventing `ok=true` output for work
  nothing ran, then hash-chaining the lie into the audit trail.
- **30 silent degrades** swept out. The worst: a tampered ledger line made the tamper check report
  *intact*; a scope-widening spec amendment skipped the blast-radius gate; and the secret-guard hook
  swallowed an import error, leaving every Write/Edit/Bash **unscanned for secrets**, silently.

**The seatbelt.**

- **Hook-enforced gates.** A `PreToolUse` hook enforces run-state gates on **native Write/Edit**
  (exit 2) — not just on mokata's own tools, which was the hole. No code before a persisted spec; no
  code without a failing test (**RED is the permission to implement**). Overrides are named,
  reasoned, session-scoped, ledgered — and have no MCP surface, by design.
- **Human-minted approval.** `approve=true` was a model-typed boolean standing in for a human. It is
  dead: it returns a proposal and commits nothing. A commit now needs an approval **minted by a human**
  via `mokata approve <id>` in a separate terminal — content-bound (approving X then committing Y is
  arithmetically impossible), single-use, session-scoped, expiring, fail-closed off a TTY, ledgered.
  There is deliberately no `approve` MCP tool: a model-invocable approve *is* the hole.
- **The trust dial actually works.** `settings.trust` was 100% dead code — `read-only` did nothing
  while `doctor` linted it and the docs described it. It is now threaded into every gate that writes.
- **The zero-bypass audit.** Every durable-write site in the codebase is swept and classified; an
  unregistered writer fails CI. It closed three real side doors — consolidation writing outside the
  gate, export being gated but never *scanned* (the two compose: one plants a secret, the other
  exfiltrates it), and a migrate that corrupted a teammate's row.
- **Scope binding**, born from a real incident where an agent built a feature the spec had explicitly
  **deferred**, treating a user's instruction as authorization. The spec now carries a
  machine-checkable scope, and an out-of-scope write is an exit-2. The only road back is `spec amend`
  — a **forced phase regression**: writes blocked, gates re-run, a fresh human approval, RED owed for
  the new criteria, then a resume. **An instruction is authorization to ASK, not to build.**

**Multi-session safety.** Every editor window is its own process, and every lock was per-process.
State writes are now atomic (a torn write used to silently *erase* state), sessions have real
identities and scoped keys (they were singletons that clobbered each other), worktrees are detected
and share one team identity, the ledger is hash-chained with a locked counter, and a **two-process
stress test** — 2×2000 mixed operations, 16 named invariants, seed-replay on failure — runs in CI.

**Session save & share.** The save path *did not exist in production*: the resume stack read state
that nothing ever wrote, so an interrupted brainstorm was unrecoverable. Now `session save` survives
`kill -9`, a per-turn autosave bounds a crash to **at most one lost brainstorm turn** (proven
numerically), and bundles are versioned with a hash that catches forged flags and a secret-scanned
transcript. One real hole closed: **approval no longer crosses machines** — hydrating a bundle used
to import the approved approach verbatim; the receiver's own gate now owns approval, on every
transport.

**Honest boundaries** — real, registered, not excused.

- **"Zero writes bypass the gate" is not true repo-wide.** It is true, and proven, of the memory /
  export / migrate funnel. **Six CLI setup one-shots** (init, harness setup, skill write/prune,
  lifecycle remove) still write outside the gate; they sit in a frozen register CI enforces, and are
  filed for 0.0.14.
- The gates bind Write/Edit and mokata's own tools. **An agent with arbitrary shell access is out of
  scope** — Bash is a side door the hook does not gate.
- The **trust dial is not yet enforced on the CLI**, and propose-only adds no teeth beyond the
  human-approval floor on MCP.
- The **Windows** leg of the stress test is wired on both operating systems, but its proof (with the
  live-database leg) lands on the **public mirror's CI** at the cut.

Local-first, no telemetry, Apache-2.0.
