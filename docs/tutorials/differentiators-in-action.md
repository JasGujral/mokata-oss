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
export MOKATA_SESSION_ID=sample     # see the note below
```

> The memory demos below reach mokata two ways from a plain terminal: through the **MCP tools**
> (`mcp_server`) exactly as the agent does — those are the ones that must earn a human-minted
> approval — and through the **Python API** (`MemoryStore`), which is the *human's* own surface, the
> one the CLI itself uses. Same engine, different side of the gate.
>
> **Why the `MOKATA_SESSION_ID` pin?** A human-minted approval (below) is bound to one session,
> and every `python3 -c` in a shell is a fresh process. The pin makes this terminal one session so
> the propose → approve → commit loop can run end to end. Inside Claude Code you never set it —
> the MCP server *is* one long-lived session.

---

## 1 · Knowledge graph — navigate by structure, not guesses

### D1 · Query the codebase by structure

```bash
mokata query callers process_payment
mokata query blast_radius process_payment --depth 2
```

```text
callers(process_payment) via ast [graph] — 1 result(s)
  checkout.py:5  «checkout»
  (answered by the embedded AST floor (name-resolution, not type inference); dynamic dispatch is not resolved)
```

No graph tool is installed here, yet the answer is **structural, not lexical** — with no
`code-review-graph`/`serena` adopted, the router binds mokata's **embedded stdlib-AST floor**,
which resolves names on Python and answers `degraded=False`. (grep is only the emergency floor
beneath it, for a zero-Python repo.)

`spec`/`develop` ground a change in these queries — *"before changing `process_payment`, here
are its call sites"* — so the agent verifies from the code instead of guessing.

**Keep it fresh — the update loop.** The index is incremental and staleness is *surfaced, never
served silently* (0.0.14 adds a freshness-before-answer contract — a known-stale graph rebuilds
*before* it answers):

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

`mokata graph status` is the precise report of which backend answers today:

```text
code graph: floor 'ast' — no adopted graph. Adopt one with `mokata graph adopt`.
```

**Graph is mandatory-by-default (0.0.14).** `graph.required` is on by default, so a *degraded*
(grep-floor) blast radius is **refused** as a decision input — you adopt a real graph
(`mokata graph adopt`, human-gated; the AST floor stays the fallback) or explicitly accept the
degraded evidence for the session with `--allow-degraded` (TTY-reconfirmed, ledgered). The AST
floor answering `degraded=False` is not a degraded state; only the *grep* floor trips the refusal.

**Why it matters:** a plain agent (and superpowers) reads ad-hoc; mokata navigates by structure —
answering structurally out of the box via the AST floor — and will not let a lexical guess
silently drive a decision.

### D22 · Adopt an external graph (degrade-clean)

The `code_graph` capability is an adoption contract, not a built-in parser: point it at a real
graph tool and mokata routes structural queries there, under the same gates.

```bash
mokata graph adopt code-review-graph   # human-gated; `serena` is the other adopted backend
mokata graph status                    # which backend answers today
```

Whatever the adopted tool does, the **floor is the guarantee**: if the backend is absent or fails
mid-query, mokata degrades to the embedded AST floor on current files (grep beneath on a
zero-Python repo) and says so rather than serving stale or fabricated structure. Full loop:
[use a codebase graph](../how-to/use-a-codebase-graph.md).

> **Neo4j is deprecated.** The external Neo4j code-graph backend still resolves and still works,
> but selecting it now prints a deprecation warning and it is **scheduled for removal in
> 0.0.17** (`deprecation.py`; a third database contradicts mokata's two-modes-one-shape store).
> There is no migration to run — a code graph is *derived* data, so the fix is to adopt
> `code-review-graph` or `serena` and re-index. Don't wire a new project onto it.

---

## 1b · Graph mandatory + trust (new in 0.0.14)

### D29 · Freshness-before-answer — never structure from stale code

Every graph query front-runs a freshness check: a known-stale graph rebuilds **before** it
answers. Add a second caller and re-ask — no manual reindex, and the new call site is already
there:

```bash
# add refund.py (a new caller of process_payment), then immediately:
mokata query callers process_payment
```

```text
callers(process_payment) via ast [graph] — 2 result(s)
  checkout.py:5  «checkout»
  refund.py:5  «refund»
  (answered by the embedded AST floor (name-resolution, not type inference); dynamic dispatch is not resolved)
```

A CRG rebuild failure degrades *loud* to the AST floor on **current** files — never stale graph
data. **Why it matters:** structural answers are only trustworthy if they track the code you have
right now; no surveyed framework contracts freshness before answering.

### D32 · The idea→code jump is physically blocked (the 9th backed gate)

`approach-approval` is a **backed** gate, not advice: with a run registered but no approach
approved, a *native* `Write`/`Edit` to an implementation file is refused with exit code 2 by the
`gate-guard` hook — the model can't skip brainstorm by reaching past mokata's tools.

```text
approach-approval: brainstorm in progress — approve an approach first. payments.py is
implementation, but no approach is approved for this run yet. Explore and approve one approach
(/brainstorm), or override: mokata gate override approach-approval --reason "<why>"
```

It brings mokata to **9 backed gates** — gates with a real enforcement mechanism behind them, not
prose — of which **4 are run-state gates** enforced on the agent's native `Write`/`Edit` by the
`gate-guard` hook (see §4). Like the other three, it is a *methodology* block a human can lift on
the record.

### D30 · Prior-art bound step · D31 · Typed approach `decisions[]`

Two more trust contracts land inside **brainstorm** (§3): approach approval is **refused unless
the prior-art step actually ran** (a step-ran check — grounding new work in what already exists,
never "found something"), and the approved approach carries typed **`decisions[]`** (statement ·
rationale · `about_code` anchors · deferred). At spec emit the deferred scope **derives** from
`decisions[].deferred` — one truth, never hand-written twice — and review's first pass compares
the diff's actual reach against the declared `about_code` anchors, so undeclared blast radius is a
divergence finding.

---

## 2 · Memory — keep, update, share (the institutional brain)

### D10 · Every memory write needs an approval the model cannot mint

The model **cannot approve its own write.** It proposes; a *human* mints the approval out-of-band,
in their own terminal; the model may then reference it by id — once.

```bash
python3 -c "
from mokata import mcp_server as M
r = M.remember(path='.', subject='api.style', value='REST')
print(r['status'], r['proposal_id'])
print(r['hint'])"
```

```text
proposed p-6d9f0e8bb43a
NOTHING was written. Ask the human to run:  mokata approve p-6d9f0e8bb43a  — then re-call remember with proposal_id="p-6d9f0e8bb43a".
```

Nothing is stored. Now **you** approve it — a separate process the model is not driving. (Run bare
`mokata approve` to list everything waiting.)

```bash
mokata approve p-6d9f0e8bb43a
```

```text
mokata · a durable write is waiting for your approval:

  proposal : p-6d9f0e8bb43a
  tool     : remember
  target   : memory:api.style
  summary  : remember 'api.style' = REST
  expires  : in 14m 59s
  would write:
    mokata · propose to remember [decision] api.style = 'REST'
    Nothing is stored unless you approve.

approved: p-6d9f0e8bb43a — recorded to the audit ledger.
The model may now commit this ONE write by re-calling remember with proposal_id=p-6d9f0e8bb43a.
It is single-use and expires with this session — nothing to remember to turn off.
```

```bash
python3 -c "
from mokata import mcp_server as M
print(M.remember(path='.', subject='api.style', value='REST', proposal_id='p-6d9f0e8bb43a')['status'])"
```

```text
committed
```

**The approval is single-use and content-hashed** — so "approve X, commit Y" is arithmetically
impossible, and a replay is refused:

```bash
python3 -c "
from mokata import mcp_server as M
# same id, TAMPERED value
print(M.remember(path='.', subject='api.style', value='GraphQL', proposal_id='p-6d9f0e8bb43a')['reason'])
# same id, second use
print(M.remember(path='.', subject='api.style', value='REST', proposal_id='p-6d9f0e8bb43a')['reason'])"
```

```text
this is NOT the write that was approved — the arguments changed since the human saw them (the approval is bound to its content hash)
that approval was already used — an approval licenses exactly one write
```

`approve=true` / `confirm=true` are still *accepted* on all 19 write tools (nothing breaks), but
they **commit nothing** — the tool answers with a proposal and this note:

```text
`approve`/`confirm` no longer commit: an approval is MINTED BY A HUMAN out-of-band
(`mokata approve <id>`) and can only be REFERENCED here by id. Typing approve=true is not
consent — it never was.
```

Approve is a **terminal command by default**. An in-chat MCP approve tool (`mcp__mokata__approve`)
exists but is **opt-in and default-OFF** (`settings.approvals.in_chat`); even when enabled, setup
writes a `permissions.ask` entry so the harness re-prompts the human on every call — the model
still cannot mint its own consent. (There is no approve slash command.) And even an approved write
is hard-blocked if it carries a secret.

**Why this is the memory-poisoning defense.** A memory store an agent can write to on its own is a
persistence layer for whatever it was told — or tricked into believing — one session, and every
later session inherits it as fact. mokata has **no auto-writes**: nothing reaches the store without
a human minting an approval out-of-band, bound to that exact content, usable once. Poisoning the
project brain therefore requires a human to read the diff and say yes to it — which is the whole
point of putting the gate on the *write*, not on the retrieval.

See the whole brain by category:

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

Record a decision, then a **contradicting** one. Each write runs the same
propose → `mokata approve <id>` → re-call-with-the-id loop from D10, so here it is as a shell
helper (the agent does this over MCP; the loop is identical):

```bash
commit() {  # propose -> you approve -> commit
  ID=$(python3 -c "
from mokata import mcp_server as M
print(M.remember(path='.', subject='$1', value='$2')['proposal_id'])")
  mokata approve $ID --yes >/dev/null
  python3 -c "
from mokata import mcp_server as M
print(M.remember(path='.', subject='$1', value='$2', proposal_id='$ID')['status'])"
}
commit db.engine postgres
commit db.engine mysql
mokata memory
```

mokata **surfaces the old→new diff** for your decision (it does not rewrite):

```text
self-healing — 1 item(s) need your decision (nothing changes until you act):
  (contradiction) [decision] db.engine: 'postgres' -> 'mysql'

mokata · memory health: 0 stale · 1 contradictory · 1 unused — review with `mokata memory` (gated) / `mokata govern`; nothing changes until you approve.
```

Approve the heal — the old value is superseded (kept in the record), the new one becomes active.
The heal is a durable write too, so it earns its own human-minted approval:

```bash
ID=$(python3 -c "
from mokata import mcp_server as M
print(M.apply_proposal(path='.', subject='db.engine', decision='approve')['proposal_id'])")
mokata approve $ID --yes >/dev/null
python3 -c "
from mokata import mcp_server as M
print(M.apply_proposal(path='.', subject='db.engine', decision='approve', proposal_id='$ID')['status'])"
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
mokata memory import /path/to/A/.mokata/backups/memory-<UTC>.json --yes
mokata memory --kind decision
```

```text
backed up 2 memory item(s) (with provenance) to ./.mokata/backups/memory-20260722T080840_369877Z.json
memory import: 2 added, 0 skipped (dups), 0 conflict(s) resolved, 0 declined.

decision (2):
  api.style = REST
  db.engine = mysql
```

`export`/`import` are the **backup** surface: the default destination is a timestamped, committable
`.mokata/backups/memory-<UTC>.json` that never clobbers a previous backup. The import is
human-gated, dedups, and routes conflicts through the same old→new heal — and the imported content
is **secret-scanned** before any write. For a live shared store, point mokata at a **Postgres**
DSN (mokata owns the schema — D17) and everyone reads/writes the same memory.

### D17 · Move the live store between backends

```bash
mokata memory migrate --to obsidian --yes
```

```text
migrate: 3 item(s) sqlite -> obsidian
migrate: 3 item(s) sqlite -> obsidian (idempotent upsert).
```

(3, not 2 — `migrate` moves the **full store** including the superseded `postgres` record.)
Idempotent (upsert by id), non-destructive (the source stays unless you pass `--drop-source`),
and **degrade-clean** (an unreachable destination writes nothing).

The two canonical backends are **sqlite** (the local, zero-config default) and **postgres** (team
mode, your own DSN). The `obsidian` and `native-memory` backends still work but are
**deprecated** — selecting one prints a warning, and they are scheduled for removal in 0.0.17. Move
off them with the one-time gated `mokata migrate obsidian` / `mokata migrate native-memory`, which
folds the channel into the canonical store.

### D4 · Guided capture → referenced *just-in-time* in a later spec

`/onboard` (or `mokata onboard`) guides you through your project's rules / guardrails /
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

Retrieval fuses up to three tiers: a **lexical floor** that runs SQL-side (SQLite FTS5/bm25,
Postgres `ts_rank`, with an honest degrade to Jaccard when FTS5 is absent), an optional
**graph-proximity** tier, and a **semantic** tier on top.

The semantic tier is **real and shipping, not a stub**: `pip install "mokata[embeddings]"` wires a
local static-embedding model (model2vec, numpy-only — no torch, no network at query time), which
mokata auto-detects. It is **consented and opt-in**, never default-on: `mokata init --mode memory`
and `--mode full` *offer* it once, interactively, and `--mode seatbelt` structurally never asks.
With no embedder configured the semantic tier is simply **off** and the lexical floor still
answers. The embedder also stays a **pluggable seam** — any `text -> list[float]` callable works,
and every vector is stamped with its embedder's id so two embedders' vectors can never be compared
(`mokata memory reembed` re-indexes when you switch).

Here a tiny synonym embedder stands in for the model, to show the semantic tier ranking a memory
by meaning:

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
  -> db.engine: we chose postgresql   (semantic=1.00, lexical=1.00)
```

"datastore" never appears in the stored decision, yet it comes back first — the semantic tier
scores it 1.00 on meaning alone. (The lexical score is also 1.00 here only because bm25 normalises
against a two-item store; the tiers are reported separately precisely so you can see which one
earned the hit.) Degrade-clean and frugal: top-k only, and the query is the only thing embedded at
read time — item vectors are computed once, on the gated write.

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
  [info] review_passed = simulated
  [PASS] memory_written
  ...
  RESULT: PASS
```

(`review_passed = simulated` is deliberate honesty: on the bare CLI there is no LLM to run the
two-stage review, so the playbook **labels the step simulated and never counts it as a pass**. A
green checkmark for work nothing actually did is the bug, not the feature. Inside Claude Code the
review really runs.)

`gate_blocked_initially → gate_passed_after_tests` is the **completeness gate** (D5): emit is
blocked until every acceptance criterion maps to a test; `red_before_green` proves the test
failed first. And jumping straight to implementation without a saved spec is **blocked** (D6):

```bash
mokata run develop
```

```text
[BLOCKED] spec-persisted — no saved spec — draft and emit it first (/spec); the completeness gate must pass before implementation.
```

**…and the same gates fire on the agent's *native* file writes.** A gate that only lives inside
mokata's own tools is a door with no lock — the model could just use its editor. `mokata setup
claude` installs a **`PreToolUse` gate-guard hook**, so `Write`/`Edit`/`MultiEdit`/`NotebookEdit`
are decided from the run's persisted state and refused with exit code 2. Four gates:

| gate | blocks an implementation write when… |
|---|---|
| `approach-approval` | a run is registered (brainstorm in progress) but no approach is approved yet — the idea→code jump |
| `spec-persisted` | an approach is approved for this run but no spec is emitted |
| `no-code-without-failing-test` | the spec is emitted but no failing test is on record |
| `spec-scope` | the write is outside the spec's authorized surface, spells something the spec **deferred**, or a `spec amend` is in flight |

A spec carries a **scope**: the surface it authorizes, and the things you agreed *not* to build
(each with the literal marker it would spell in code). The model writes that section when it emits
the spec through `/spec` — you never hand-author it. To make this beat runnable in a plain
terminal, emit one with the scripted escape hatch, and put a failing test on record:

```bash
cat > spec.json <<'JSON'
{"title": "Payments",
 "criteria": [{"id": "AC-1", "text": "process_payment is idempotent on retry"}],
 "tests":    [{"name": "test_idempotent", "ac_ids": ["AC-1"]}],
 "scope": {"authorized": ["payments.py"],
           "deferred":   [{"id": "D1", "item": "batch payments", "markers": ["batch_update"]}]}}
JSON
mokata spec emit --file spec.json --yes
```

```text
spec emitted: 'Payments' — 1 acceptance criteria, all mapped to tests.
  saved as this run's spec (run sample), and recorded in the shared spec corpus (1 spec(s)).
  implementation is unblocked once a failing test is on record (/test).
```

Implementation stays blocked until a failing test is on record, so put one there — this is the
one step `/test` normally does for you:

```bash
python3 -c "
from mokata.state import StateStore
from mokata import tdd_state
tdd_state.record(StateStore(tdd_state.state_dir('.')), 'sample', red=['test_idempotent'])
print('phase:', tdd_state.read_tdd_phase('.', 'sample').phase)"
```

```text
phase: red
```

RED is the *permission* to implement. Now watch `spec-scope` catch scope creep **inside an
otherwise-authorized file**:

```bash
# build the PreToolUse envelope Claude Code sends the hook on a native Write
write() { python3 -c "
import json, os, sys
print(json.dumps({'tool_name': 'Write', 'cwd': os.getcwd(), 'session_id': 'w1',
  'tool_input': {'file_path': os.getcwd() + '/' + sys.argv[1], 'content': sys.argv[2]}}))" "$1" "$2"; }

write payments.py 'def process_payment(a, c): return 1' | mokata-hook gate-guard   # authorized
write payments.py 'def batch_update(items): pass'       | mokata-hook gate-guard   # DEFERRED
write checkout.py 'def checkout(c): pass'               | mokata-hook gate-guard   # not in scope
```

```text
BLOCKED [spec-scope] spec-scope: scope change — this write is outside spec v1 (deferred: batch payments). this write spells 'batch_update', and the spec DEFERRED 'batch payments'. A user's instruction is authorization to ASK, not to build: run `mokata spec amend` (gated — the new scope is re-approved and re-tested), or override: mokata gate override spec-scope --reason "<why>"
BLOCKED [spec-scope] spec-scope: scope change — this write is outside spec v1. checkout.py is outside the surface this spec authorized (payments.py). A user's instruction is authorization to ASK, not to build: run `mokata spec amend` (gated — the new scope is re-approved and re-tested), or override: mokata gate override spec-scope --reason "<why>"
```

(The first write — an authorized file, ordinary implementation — passes silently, exit 0.) The
honest fine print: **test files are always writable** (you must be able to write the failing
test), the gates fire **only inside an active mokata run** (hand-editing is never policed), and
this is a *methodology* block, not a security one — a human can lift one, explicitly and on the
ledger:

```bash
mokata gate override spec-scope --reason "hotfix: prod is down"
mokata gate status
mokata gate clear          # …and enforce again
```

```text
'spec-scope' overridden for this session (run sample) — recorded to the audit ledger.
It expires with this session. Clear it now with: mokata gate clear

mokata run-state gates (enforced on native Write/Edit by the gate-guard hook):
  run: sample
  spec-persisted                   enforced
  no-code-without-failing-test     enforced
  spec-scope                       OVERRIDDEN (this session)
  approach-approval                enforced

  The override expires with this session — a new session enforces again.

cleared: spec-scope — the gates are enforcing again.
```

The override lands on the ledger with its reason:

```text
  #5   gate_override gate=spec-scope run=sample actor=human decision=override scope=session reason=hotfix: prod is down
```

**Why it matters:** every other methodology tool asks the model nicely. mokata's gates are an exit
code the model cannot argue with — and there is **no env-var kill switch and no MCP tool** to turn
them off, because a model-invocable override is not an override at all.

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

With the `Payments` spec saved (above), a change that **touches** it is raised and routed through
the deviation gate — and the confirmation is one the model cannot mint for itself:

```bash
python3 -c "
from mokata import mcp_server as M
r = M.spec_check(path='.', symbols='process_payment')      # touches the saved spec
print(r['status'], r['proposal_id'])
print('unrelated:', M.spec_check(path='.', symbols='render_sidebar')['status'])"
```

```text
blocked p-76acbec00cdf        # touches the saved 'Payments' spec — STOP until a human confirms
unrelated: ok                 # an unrelated change → no false alarm
```

`mokata approve p-76acbec00cdf`, then re-calling `spec_check` with that `proposal_id`, records the
amend/supersede through the deviation gate and logs it. With **no** saved spec corpus at all the
answer is `skipped` — mokata says it doesn't know, rather than clearing you.

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

All **19** MCP write tools are propose-only — even the destructive ones. Nothing an adopted tool
does can commit without an approval a human minted:

```bash
python3 -c "
from mokata import mcp_server as M
r = M.reset(path='.')
print(r['status'], r['proposal_id'])
print(r['hint'])"
```

```text
proposed p-5da515d63e87
NOTHING was written. Ask the human to run:  mokata approve p-5da515d63e87  — then re-call reset with proposal_id="p-5da515d63e87".
```

On top of that, the **trust dial** (`settings.trust`, keyed by surface or by tool) can pin a tool
or the whole MCP surface to `read-only` — a *configuration* bound that no proposal and no human
approval can lift. Its honest ladder on the MCP surface is `read-only` ▸ write-allowed:
`propose-only` and the default `gated-write` are the same thing there, because every MCP write
already needs a human-minted approval.

---

## 5 · Composability & control

### D15 · Run any capability standalone; enter at any phase

```bash
mokata skills                     # the catalog (cheap; add a name for the full prompt)
mokata run review                 # run one skill on its own — no pipeline required
mokata enter analysis             # start mid-pipeline
```

```text
mokata skills — the curated catalog (16 skills; run `mokata skills <name>` for detail):

Runnable pipeline skills (run `mokata run <name>` or `/<name>`):
  /brainstorm  mokata · Explore approaches with the user; HARD-GATE the spec behind approval.
  /spec        mokata · Turn the problem into testable acceptance criteria; map each to a test.
  /test        mokata · Write failing tests first (RED); no implementation.
  /develop     mokata · Implement the minimum to turn a failing test green.
  ...

Standalone / auto-firing skills (their own command or fire on their own — not `mokata run`):
  /govern      mokata · See the governed state — rules, memory-by-kind, read/write ratio, ...
  ...
# mokata · /review (standalone)
```

(16 curated skills, plus 10 auto-engaging domain skills — `api`, `security`, `performance`,
`frontend-a11y`, `browser-testing`, `ci-cd`, `git`, `deprecation`, `docs-adr`, `shipping` — for
26 shipped in all.)

Profiles, per-layer/tool toggles, and trust dials make the stack configurable and reproducible.

### D18 · Verified `ship` — never auto-merge

```bash
mokata skills ship
```

```text
  gate: finish-is-human-landed (human) — Shipping verifies done (green tests + met ACs + passed review) and the human chooses how to land it; mokata never merges/PRs/deletes without explicit confirmation.
```

`/ship` blocks until the work is *actually* done (green tests + every AC met + review
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
