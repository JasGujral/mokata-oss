# mokata catches a bad change (60 seconds)

The one-glance demo. An AI coding agent, mid-task, tries two bad changes — ship code with no
spec, and stash a secret. **mokata stops both, and puts both on the audit ledger.** Every command
below was run on a fresh sample repo; the output is exactly what it prints (absolute paths
shortened to `.`).

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

### Bad change #2 — a secret in the change

Now it tries to stash an AWS key. A secret is a **hard block** — approval can't override it
(here it's even called *with* `approve=True`, and still refused):

```bash
python3 - <<'PY'
from mokata import mcp_server as M
key = "AKIA" + "IOSFODNN7" + "EXAMPLE"        # a real-looking AWS key
r = M.remember(path=".", subject="aws.key", value=key, approve=True)
print(f'status: {r["status"]}   findings: {r["findings"]}')
PY
```

```text
status: blocked   findings: ['aws-access-key', 'high-entropy-token', 'sensitive-location']
```

### The punchline — every block is on the ledger

```bash
mokata audit
```

```text
audit ledger — 2 entries:
  #1   gate        gate=spec-persisted phase=develop decision=blocked reason=no saved spec — draft and emit it first (/mokata:spec); the completeness gate must pass before implementation. ac_count=0
  #2   write_gate  write_kind=memory target=memory:aws.key actor=mcp decision=blocked reason=secret detected
```

Two bad changes, both caught, both auditable — **local-first, human-gated, nothing silent.**
That's mokata: it remembers your project and stops the agent shipping the wrong thing.

**Next:** see *every* differentiator run (graph, memory, governance) in
[differentiators in action](differentiators-in-action.md), or
[get started](../quickstart.md) in your own repo. Inside Claude Code the agent drives the *same
gates* through the `/mokata:` commands and MCP tools — one engine.
