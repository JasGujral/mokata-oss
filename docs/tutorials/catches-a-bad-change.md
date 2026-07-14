# mokata catches a bad change (60 seconds)

The one-glance demo. An AI coding agent, mid-task, tries three bad changes — ship code with no
spec, edit a source file straight past the gates, and stash a secret. **mokata stops all three, and
every write it governs lands on the audit ledger.** Every command below was run end-to-end on a
fresh sample repo; the output is exactly what it prints (absolute paths shortened to `.`).

> **mokata is the memory + seatbelt for your AI coding agent.** This page is the seatbelt in
> action; copy-paste it and watch it catch a bad change in your own terminal.

## Run it (copy-paste)

```bash
mkdir demo && cd demo && git init -q
cat > checkout.py <<'PY'
def checkout(cart):
    return sum(i["price"] for i in cart)
PY
mokata init --profile standard --yes
export MOKATA_SESSION_ID=demo     # see the note at the bottom
```

```text
wrote ./.mokata/manifest.json
wrote ./.mokata/constitution.md
wrote ./.mokata/.gitignore
mokata initialized with profile 'standard'.
```

### Bad change #1 — code with no spec, no tests

The agent jumps straight to implementation. mokata **blocks** it — no code ships without a saved
spec whose acceptance criteria each map to a test:

```bash
mokata run develop
```

```text
[BLOCKED] spec-persisted — no saved spec — draft and emit it first (/mokata:spec); the completeness gate must pass before implementation.
```

### Bad change #2 — going around the gates with a plain file edit

So the agent does the honest thing and emits a spec — every acceptance criterion mapped to a test,
or the completeness gate refuses it:

```bash
cat > spec.json <<'JSON'
{"title": "checkout rejects an empty cart",
 "criteria": [{"id": "AC1", "text": "checkout([]) raises ValueError"}],
 "tests": [{"name": "test_empty_cart_raises", "ac_ids": ["AC1"]}]}
JSON
mokata spec emit --file spec.json --yes
```

```text
spec emitted: 'checkout rejects an empty cart' — 1 acceptance criteria, all mapped to tests.
  saved as this run's spec (run demo), and recorded in the shared spec corpus (1 spec(s)).
  implementation is unblocked once a failing test is on record (/mokata:test).
```

Now the obvious dodge: there's a spec but still no failing test, so instead of mokata's tools the
agent reaches for the editor's own `Write`. **That door has a lock.** `mokata setup claude` installs
a **`PreToolUse` gate-guard hook**, so the run-state gates fire on the agent's *native* `Write`/`Edit`
too:

```bash
# simulate what Claude Code sends the hook on a native Write
python3 -c "
import json, os
print(json.dumps({'tool_name': 'Write', 'cwd': os.getcwd(), 'session_id': 'w1',
  'tool_input': {'file_path': os.getcwd() + '/checkout.py',
                 'content': 'def checkout(c): return 0'}}))" | mokata-hook gate-guard
```

```text
BLOCKED [no-code-without-failing-test] no-code-without-failing-test: no failing test is on record for this run — checkout.py is implementation. Write the failing test first and watch it fail (/mokata:test), or override: mokata gate override no-code-without-failing-test --reason "<why>"
```

Exit code **2** — Claude Code refuses the tool call and the file is never touched.

Now send the **same** write to a **test** file and watch it sail through:

```bash
python3 -c "
import json, os
print(json.dumps({'tool_name': 'Write', 'cwd': os.getcwd(), 'session_id': 'w1',
  'tool_input': {'file_path': os.getcwd() + '/test_checkout.py',
                 'content': 'def test_empty_cart_raises(): ...'}}))" | mokata-hook gate-guard
echo "exit: $?"
```

```text
exit: 0
```

That's the whole idea in two commands: **RED is the *permission* to implement, not the
prohibition.** A test file is always writable — you have to be able to write the failing test.
Three run-state gates guard native writes — `spec-persisted`, `no-code-without-failing-test`, and
`spec-scope` (a write outside the surface the spec authorized, or a feature you agreed *not* to
build) — and they fire **only inside an active run**: hand-editing a repo mokata isn't running is
never policed. It is a *methodology* block, not a security one, so a human can lift it, on the
record: `mokata gate override <gate> --reason "<why>"`.

### Bad change #3 — a secret in the change

Now it tries to stash an AWS key. First it calls the `remember` tool with `approve=True`, the way
a model used to wave its own writes through. **It doesn't commit anything — it can't:**

```bash
python3 -c "
from mokata import mcp_server as M
key = 'AKIA' + 'IOSFODNN7' + 'EXAMPLE'        # a real-looking AWS key
r = M.remember(path='.', subject='aws.key', value=key, approve=True)
print('status:', r['status'], ' committed:', r['committed'])
print(r['note'])
print(r['hint'])"
```

```text
status: proposed  committed: False
`approve`/`confirm` no longer commit: an approval is MINTED BY A HUMAN out-of-band (`mokata approve <id>`) and can only be REFERENCED here by id. Typing approve=true is not consent — it never was.
NOTHING was written. Ask the human to run:  mokata approve p-d6af1f0f5d5b  — then re-call remember with proposal_id="p-d6af1f0f5d5b".
```

The model cannot approve its own write. It can only *propose* one and hand you an id. So play the
careless human and approve it — in **your** terminal, in a process the model isn't driving:

```bash
mokata approve p-d6af1f0f5d5b --yes
```

```text
approved: p-d6af1f0f5d5b — recorded to the audit ledger.
The model may now commit this ONE write by re-calling remember with proposal_id=p-d6af1f0f5d5b.
It is single-use and expires with this session — nothing to remember to turn off.
```

Now the model redeems the approval — and **the secret scan still refuses it.** Approval is a
methodology gate; it was never a security override:

```bash
python3 -c "
from mokata import mcp_server as M
key = 'AKIA' + 'IOSFODNN7' + 'EXAMPLE'
r = M.remember(path='.', subject='aws.key', value=key, proposal_id='p-d6af1f0f5d5b')
print('status:', r['status'], ' findings:', r['findings'])"
```

```text
status: blocked  findings: ['aws-access-key', 'high-entropy-token', 'sensitive-location']
```

### The punchline — every decision is on the ledger

```bash
mokata audit
```

```text
audit ledger — 7 entries:
  #1   gate        gate=spec-persisted phase=develop decision=blocked reason=no saved spec — draft and emit it first (/mokata:spec); the completeness gate must pass before implementation. ac_count=0
  #2   write_gate  write_kind=config target=spec:emit actor=agent decision=approved reason=committed
  #3   checkpoint  run=demo phase=completeness_gate
  #4   checkpoint  run=demo phase=emit
  #5   write_approval decision=approved proposal=p-d6af1f0f5d5b tool=remember target=memory:aws.key actor=human run=demo scope=session
  #6   write_gate  write_kind=memory target=memory:aws.key actor=mcp decision=blocked reason=secret detected
  #7   write_approval decision=redeemed proposal=p-d6af1f0f5d5b tool=remember target=memory:aws.key approved_by=human run=demo committed=False
```

*(Each row also carries `prev_hash`/`entry_hash` — the ledger is hash-chained. Elided here for
width.)* Even the *approved* write is recorded as `committed=False`: you can see exactly what you
approved, and exactly why it still didn't land.

One honest gap worth knowing: the **gate-guard's block (#2) is not on the ledger.** The hook runs
as its own short-lived process at the harness's tool boundary — it stops the write with an exit
code, and it is the *override* (`mokata gate override`) that gets recorded, permanently, along with
who lifted which gate and why. What mokata *governs*, it logs.

Three bad changes, all caught — **local-first, human-gated, nothing silent.**
That's mokata: it remembers your project and stops the agent shipping the wrong thing.

> **Why `MOKATA_SESSION_ID=demo`?** An approval is bound to one session, and each `python3 -c` in
> a shell is a fresh process. The pin makes this whole terminal one session so the demo can run
> end to end. Inside Claude Code you never set it — the MCP server *is* one long-lived session,
> and you just run `mokata approve <id>` in your own terminal.

**Next:** see *every* differentiator run (graph, memory, governance) in
[differentiators in action](differentiators-in-action.md), or
[get started](../quickstart.md) in your own repo. Inside Claude Code the agent drives the *same
gates* through the `/mokata:` commands and MCP tools — one engine.
