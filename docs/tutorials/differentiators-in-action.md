# Differentiators in action

A runnable showcase of what makes mokata different — the **knowledge graph**, **memory**, and
**governance** are the spine — with a labelled, copy-pasteable demo for **every** differentiator.
Each beat is *scenario → commands → real output → why it matters*. Every command here was run on
a sample repo and the output is exactly what it prints (paths shortened to `<repo>`).

> **Clean-room (D20).** mokata inherits the best practices of spec-driven, test-first agent work
> but **imports and copies nothing** — its methodology, prompts, and engine are its own
> (Apache-2.0 under MoStack). Everything below is mokata's own machinery.

## Set up the sample repo

```bash
mkdir sampleapp && cd sampleapp && git init
cat > payments.py <<'PY'
def process_payment(amount, currency):
    """Charge a payment; idempotent on retry."""
    fee = compute_fee(amount)
    return charge(amount + fee, currency)

def compute_fee(amount):
    return amount * 0.029

def charge(total, currency):
    return {"ok": True, "total": total, "currency": currency}
PY
cat > checkout.py <<'PY'
from payments import process_payment

def checkout(cart):
    total = sum(i["price"] for i in cart)
    return process_payment(total, "USD")
PY
mokata init --profile full --yes
```

> The memory demos below use mokata's **Python API** so they run in a plain terminal. In Claude
> Code the agent drives the *same gated operations* through the `remember` / `recall` /
> `apply_proposal` MCP tools — the API and the tools are one engine.

---

## 1 · Knowledge graph — navigate by structure, not guesses

### D1 · Query the codebase by structure

```bash
mokata query callers process_payment
mokata query blast_radius process_payment --depth 2
```

```text
callers(process_payment) via grep [grep fallback] — 1 result(s)
  checkout.py:6  «checkout»  return process_payment(total, "USD")
  (lexical fallback (no structural graph; results are approximate))
```

`spec`/`develop` ground a change in these queries — *"before changing `process_payment`, here
are its call sites"* — so the agent verifies from the code instead of guessing.

**Keep it fresh — the update loop.** The index is incremental and staleness is *surfaced, never
served silently*:

```bash
mokata index        # build/refresh — only changed files
mokata lat-check     # flag @lat concept-drift anchors
```

```text
index: built 2 file(s)
index: tracking 2 file(s)
index: no code graph wired — refresh runs on the grep floor (`mokata lat-check` still flags concept drift lexically).
lat check: no anchors or lat.md — drift tracking inactive (clean).
```

With no graph tool present mokata runs on the **grep floor** — approximate but always available,
and it tells you how to wire a real one (`mokata status`):

```text
code_graph -> grep (degraded from code-review-graph)
no codebase graph wired — running on the grep floor (safe, but lexical). To enable richer
structural queries, install a graph tool (code-review-graph or serena) and wire it: ...
```

**Why it matters:** a plain agent (and superpowers) reads ad-hoc; mokata navigates by structure
and keeps the index fresh — and never hard-fails when the graph is absent.

### D22 · Wire an external Neo4j graph (degrade-clean)

Point the `code_graph` capability at a team **Neo4j** graph — credentials by env var only:

```bash
mokata config set tools.neo4j '{"provides":"code_graph","kind":"external","enabled":true,"detect":{"type":"python_module","name":"neo4j"},"config":{"uri_env":"NEO4J_URI","user_env":"NEO4J_USERNAME","password_env":"NEO4J_PASSWORD"}}'
mokata config set capabilities.code_graph.fallback '["neo4j","ripgrep","grep"]'
mokata status
```

With no driver / no `NEO4J_*` env / DB down, it **degrades cleanly to the grep floor** and says so:

```text
code_graph -> grep (degraded from neo4j)
```

Install the driver (`pip install "mokata[neo4j]"`), export `NEO4J_URI/_USERNAME/_PASSWORD`, then
`mokata index` → `mokata lat-check` run over the live graph. Full loop:
[use a codebase graph](../how-to/use-a-codebase-graph.md). **Why it matters:** bring your own
graph DB; mokata adopts it under the same contract and never breaks when it's unreachable.

---

## 2 · Memory — keep, update, share (the institutional brain)

### D10 · Every memory write is human-gated

```bash
python3 - <<'PY'
from mokata import mcp_server as M
print(M.remember(path=".", subject="api.style", value="REST")["status"])              # propose
print(M.remember(path=".", subject="api.style", value="REST", approve=True)["status"]) # commit
PY
```

```text
proposed
committed
```

Without `approve` it stages the change and writes **nothing**; the explicit approval is what
commits — and even then a secret in the content is hard-blocked. See the whole brain by category:

```bash
mokata memory
```

```text
memory backend: sqlite · types on: persistent, decision, episodic
memory read/write ratio: 1.00 (1 reads / 1 writes)
active items: 1

decision (1):
  api.style = REST
```

### D2 · Self-healing — a contradiction is surfaced, never silently overwritten

Record a decision, then a **contradicting** one:

```bash
python3 -c "from mokata import mcp_server as M; M.remember(path='.', subject='db.engine', value='postgres', approve=True)"
python3 -c "from mokata import mcp_server as M; M.remember(path='.', subject='db.engine', value='mysql', approve=True)"
mokata memory
```

mokata **surfaces the old→new diff** for your decision (it does not rewrite):

```text
self-healing — 1 item(s) need your decision (nothing changes until you act):
  (contradiction) [decision] db.engine: 'postgres' -> 'mysql'
```

Approve the heal — the old value is superseded (kept in the record), the new one becomes active:

```bash
python3 -c "from mokata import mcp_server as M; print(M.apply_proposal(path='.', subject='db.engine', decision='approve', approve=True)['status'])"
```

```text
committed
```

**Why it matters:** a plain agent has no persistent memory; mokata's is on by default and
*self-heals by surfacing*, so institutional knowledge never silently rots.

### D3 · Share it with the team (one developer's write, seen by another)

Developer A exports their gated decisions; developer B imports them:

```bash
# Developer A
mokata memory export
# Developer B (their own repo)
mokata memory import /path/to/A/.mokata/memory-share.json --yes
mokata memory --kind decision
```

```text
exported 2 memory item(s) (with provenance) to <repo>/.mokata/memory-share.json
memory import: 2 added, 0 skipped (dups), 0 conflict(s) resolved, 0 declined.

decision (2):
  api.style = REST
  db.engine = mysql
```

The import is human-gated, dedups, and routes conflicts through the same old→new heal — and the
imported content is **secret-scanned** before any write. For a live shared store, point mokata at
a **Postgres** DSN (mokata owns the schema — D17) and everyone reads/writes the same memory.

### D17 · Move the live store between backends

```bash
mokata memory migrate --to obsidian --yes
```

```text
migrate: 3 item(s) sqlite -> obsidian (idempotent upsert).
```

(3, not 2 — `migrate` moves the **full store** including the superseded `postgres` record.)
Idempotent (upsert by id), non-destructive (the source stays unless you pass `--drop-source`),
and **degrade-clean** (an unreachable destination writes nothing). sqlite ↔ obsidian ↔ postgres.

### D4 · Guided capture → referenced *just-in-time* in a later spec

`/mokata:onboard` (or `mokata onboard`) guides you through your project's rules / guardrails /
conventions / domain facts and **LLM-processes** them into typed memory. Here we capture a
domain formula and three context facts, then a spec that *touches pricing* pulls in **only** the
relevant one:

```bash
python3 - <<'PY'
from mokata.config import Surface
from mokata.memory import MemoryStore, MemoryItem, PERSISTENT, CONTEXT, jit_recall
s = MemoryStore.from_surface(Surface.load("."))
for subj, val in [("pricing.formula", "price = base * 1.2 (20% margin)"),
                  ("logging.format", "structured JSON logs"),
                  ("retry.policy", "retry 3x on a 500")]:
    s.remember(MemoryItem.create(subj, val, mtype=PERSISTENT, kind=CONTEXT), assume_yes=True)
for h in jit_recall(s, "how is the pricing margin computed", top_k=2):
    print(f"  -> [{h.effective_kind}] {h.subject}: {h.value}")
PY
```

```text
  -> [context] pricing.formula: price = base * 1.2 (20% margin)
```

Only the pricing formula surfaces — the logging and retry facts are **not** loaded.

**Why it matters (frugality, D12):** the project brain can grow large without bloating any run —
mokata retrieves *only what the task touches*, never the whole corpus.

### D21 · Tiered semantic retrieval — find by meaning, not just words

The embedder is a **pluggable seam** (wire pgvector + a real model in production). Here a tiny
synonym embedder shows the semantic tier surfacing a memory that shares **no words** with the query:

```bash
python3 - <<'PY'
import tempfile, os, re
from mokata.memory import MemoryStore, SQLiteBackend, MemoryItem, DECISION
class SynonymEmbedder:
    GROUPS = {0: {"postgres","postgresql","pg","database","db","datastore"}}
    DIM = 2
    def __call__(self, text):
        toks = set(re.findall(r"[a-z0-9]+", (text or "").lower()))
        v = [1.0 if (toks & self.GROUPS[0]) else 0.0, 0.0]
        if toks and not any(v): v[1] = 1.0
        n = sum(x*x for x in v) ** 0.5
        return [x/n for x in v] if n else v
with tempfile.TemporaryDirectory() as d:
    s = MemoryStore(SQLiteBackend(os.path.join(d, "m.db")), embedder=SynonymEmbedder())
    s.remember(MemoryItem.create("db.engine", "we chose postgresql", mtype=DECISION), assume_yes=True)
    s.remember(MemoryItem.create("ui.theme", "dark mode default", mtype=DECISION), assume_yes=True)
    for h in s.recall_relevant("which datastore did we pick", top_k=1):
        print(f"  -> {h.item.subject}: {h.item.value}   (semantic={h.semantic:.2f}, lexical={h.lexical:.2f})")
PY
```

```text
  -> db.engine: we chose postgresql   (semantic=1.00, lexical=0.11)
```

"datastore" never appears in the stored decision, yet the semantic tier ranks it first. It's
opt-in, degrade-clean (no embedder ⇒ the lexical floor still works), and frugal (top-k only).

### D23 · Team design & spec vault — push → search → pull → review

Memory carries the *decisions*; the **vault** carries the *artifacts* — a brainstorm-plan or a
spec — so a teammate can find and review them:

```bash
printf '# Payments redesign\n\nWe weighed 3 options and chose the idempotent-ledger approach for exactly-once capture.\n' > plan.md
mokata vault push payments-redesign plan.md --yes --author alice
mokata vault search "idempotent ledger"
mokata vault pull payments-redesign --dest review.md
```

```text
vault: pushed 'payments-redesign' [brainstorm v1] — new entry 'payments-redesign' [brainstorm]
vault: 1 match(es) for 'idempotent ledger'
  [0.12] payments-redesign  [brainstorm v1]  Payments redesign  — alice · 2026-06-27
pulled 'payments-redesign' [brainstorm v1] → <repo>/review.md  (by alice · 2026-06-27)
```

Gated, secret-scanned, versioned (a changed re-push needs `--force`), committed to the synced
`.mokata/vault/`. **Why it matters:** the design record is named, searchable, and reviewable —
not lost in chat.

---

## 3 · Spec-driven correctness

### D5 · Provable completeness gate · D6 · No code without a saved spec

Run the whole story end-to-end and watch the gates fire live:

```bash
mokata playbook
```

```text
mokata v1 playbook — profile 'full', mode 'sequential'
  [PASS] brainstorm_approved
  [PASS] knowledge_layer_on
  [PASS] gate_blocked_initially
  [PASS] approach_in_gate
  [PASS] gate_passed_after_tests
  [PASS] red_before_green
  [PASS] review_passed
  ...
  RESULT: PASS
```

`gate_blocked_initially → gate_passed_after_tests` is the **completeness gate** (D5): emit is
blocked until every acceptance criterion maps to a test; `red_before_green` proves the test
failed first. And jumping straight to implementation without a saved spec is **blocked** (D6):

```bash
mokata run develop
```

```text
[BLOCKED] spec-persisted — no saved spec — draft and emit it first (/mokata:spec); the completeness gate must pass before implementation.
```

### D7 · Ground in code, never assume

```bash
mokata run spec
```

The spec protocol requires inspecting the real code first and emitting an auditable
*"Verified from code:"* list:

```text
BEFORE drafting or emitting ANY acceptance criterion, inspect the REAL code the change touches:
... Emit a short "Verified from code:" list naming the symbols / signatures / edges you checked ...
Decide from the code, not from assumption. ... never silently assume. Cite what you verified.
```

### D8 · Spec-awareness regression guard · D9 · Deviation gate

Save a spec, then a change that **touches** it is raised and routed through the deviation gate:

```bash
python3 -c "from mokata import mcp_server as M; print(M.spec_check(path='.', symbols='process_payment')['status'])"
python3 -c "from mokata import mcp_server as M; print(M.spec_check(path='.', symbols='process_payment', approve=True)['status'])"
python3 -c "from mokata import mcp_server as M; print(M.spec_check(path='.', symbols='render_sidebar')['status'])"
```

```text
blocked       # touches the saved 'Payments' spec — STOP until confirmed
confirmed      # human confirms (amend/supersede) — routed through the deviation gate, logged
ok             # an unrelated change → no false alarm
```

The same **deviation gate** (D9) guards every plan change: mokata *never silently deviates* — it
stops, surfaces *what · why · options*, and logs your decision. **Why it matters:** a plain
agent (and superpowers, which optimises for autonomous non-deviating runs) can silently break a
previously-shipped spec; mokata asks first.

---

## 4 · Governance you can trust — review every decision

### D11 · The audit ledger reconstructs the whole run

```bash
mokata audit
```

A representative excerpt (your ledger reflects exactly the commands *you* ran):

```text
  #1   gate        gate=spec-persisted phase=develop decision=blocked reason=no saved spec ...
  #2   playbook    step=brainstorm approved=True
  #3   playbook    step=gate_block passed=False unmapped=['AC-1', 'AC-2']
  #6   playbook    step=gate_pass passed=True
  #7   tdd         event=blocked test=test_never_written gate=no-code-without-failing-test
  #9   exec_estimate mode=sequential tasks=2 est_in=22 est_out=44 est_cost=0.000726
  #12  playbook    step=done profile=full mode=sequential degraded=False
```

Every gate decision, tool call, and durable write is on one append-only ledger.

### D13 · Local-first, zero telemetry

```bash
mokata init --profile minimal --preview
```

```text
mokata init — profile 'minimal'
Capabilities: none (engine-only profile).
```

The `minimal` profile wires **no external capabilities** — zero network egress. Nothing leaves
the machine unless you wire it; mokata ships **no telemetry** (superpowers ships optional telemetry).

### D14 · Reversible & resumable

```bash
mokata reset --keep-config        # previews what it would remove; deletes nothing without your yes
mokata enter analysis             # re-enter the pipeline at any phase (resume)
```

```text
reset will remove:
  <repo>/.mokata/temp_local
# mokata · pipeline entry: analysis
Phases to run (each applies its own gate):
```

### D16 · Adopt freely, trust nothing

Every MCP write tool is **propose-only by default** — adopt an external tool under mokata's gates
without granting it autonomy:

```bash
python3 -c "from mokata import mcp_server as M; print(M.reset(path='.')['status'])"
```

```text
proposed
```

Trust dials (`config set tools.<t>.trust …`) let you keep a tool read-only or propose-only.

---

## 5 · Composability & control

### D15 · Run any capability standalone; enter at any phase

```bash
mokata skills                     # the catalog (cheap; add a name for the full prompt)
mokata run review                 # run one skill on its own — no pipeline required
mokata enter analysis             # start mid-pipeline
```

```text
mokata skills (run `mokata skills <name>` for detail):
  /brainstorm mokata · Explore approaches with the user; HARD-GATE the spec behind approval.
  /onboard    mokata · Guided capture of the project's rules, guardrails, conventions, ...
  /spec       mokata · Turn the problem into testable acceptance criteria; map each to a test.
  ...
# mokata · /review (standalone)
```

Profiles, per-layer/tool toggles, and trust dials make the stack configurable and reproducible.

### D18 · Verified `ship` — never auto-merge

```bash
mokata skills ship
```

```text
  gate: finish-is-human-landed (human) — Shipping verifies done (green tests + met ACs + passed review) and the human chooses how to land it; mokata never merges/PRs/deletes without explicit confirmation.
```

`/mokata:ship` blocks until the work is *actually* done (green tests + every AC met + review
passed), then lets **you** choose how to land it — merge, PR, keep, or discard.

---

## 6 · Observability — see the governance happen

### D19 · Run-progress tracker · D24 · Parallel lanes + clickable dashboard

When mokata runs subagents in parallel, the progress view is **parallel-aware**. To see it
without wiring a subagent harness, simulate a parallel run's recorded state (this is exactly
what the orchestrator persists), then read it back:

```bash
python3 - <<'PY'
from mokata.config import Surface
from mokata.govern import AuditLedger
from mokata.govern.resume import CHECKPOINT_PREFIX
s = Surface.load("."); s.state.write(CHECKPOINT_PREFIX + "demo", {"run_id": "demo", "passed": ["brainstorm", "analysis"]})
l = AuditLedger.from_mokata_dir(s.mokata_dir)
l.record("exec_estimate", mode="parallel", tasks=3)
l.record("subagent", task="auth", ok=True, isolated=True, review_passed=True)
l.record("subagent", task="billing", ok=True, isolated=True, review_passed=True)
l.record("subagent", task="search", ok=True, isolated=True, review_passed=False)
PY
mokata progress --lanes --run demo
```

(`mokata progress` without `--lanes` shows the linear 7-phase tracker — done/current/pending.)

```text
mokata · run [2/7 done] · strawman
  lanes (3 concurrent):
  ✓ auth                done  (isolated)
  ✓ billing             done  (isolated)
  ✗ search              blocked  (review failed)
```

For a richer view, opt into the **clickable local HTML dashboard** (self-contained, no network,
no server) — choose your tier and write it:

```bash
mokata config set settings.ux.progress dashboard
mokata watch --once --open --run demo
```

```text
mokata watch: wrote <repo>/.mokata/temp_local/watch.html
```

The dashboard shows the parallel lanes (click a lane to drill into its ledger rows), the 7-phase
pipeline, and a bounded gate/decision feed. It's **read-only** (never writes durable state, never
gates), **frugal** (only the active run + a bounded ledger tail), and **local-first** (gitignored,
never committed). See [watch a run](../how-to/watch-a-run.md).

---

## 7 · Frugal by design

### D12 · Active token & cost governance

Frugality is a first-class design rule, not an afterthought. Three things you saw above are it
in action: **JIT retrieval** (§2 D4 — only the pricing formula loaded, never the corpus),
**top-k semantic recall** (§2 D21), and the **bounded** dashboard feed (§6 — only a tail of the
ledger). The token tracker also surfaces parallel-run savings:

```bash
mokata budget
```

```text
budget: no savings recorded yet.
```

(After a parallel run with capped hand-backs, `budget` reports the input/output tokens saved.)
Graph and memory retrieval are **just-in-time and budgeted**, output is kept dense, and the
SessionStart briefing stays under a hard ~2k-token ceiling with cache-stable prefixes — so a big
project brain costs you *more knowledge, not more tokens per run*. **Why it matters:** a plain
agent re-reads and re-explains; mokata loads only what the task touches and accounts for it.

---

## Why this beats a plain agent (and superpowers)

mokata's spine — a **codebase graph**, **persistent self-healing shareable memory**, and
**human-gated, audited governance** — is exactly what an ad-hoc agent lacks. Superpowers brings
process discipline but has **no graph, no persistent or shared memory, no audit ledger**, and
optimises for autonomous non-deviating runs; mokata optimises for *you reviewing every decision*,
local-first, with nothing silent. Everything above is **runnable** — see it for yourself.
