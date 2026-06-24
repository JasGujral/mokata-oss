# How-to: install the Claude Code plugin

mokata ships as a Claude Code plugin under the **MoStack** marketplace.

```text
/plugin marketplace add JasGujral/mokata-oss
/plugin install mokata@mostack
```

This makes the slash commands available — `/brainstorm`, `/spec`, `/test`, `/develop`,
`/review`, `/debug`, `/optimize`, `/bug` — and wires both hooks (declared in
`hooks/hooks.json`):

- **SessionStart** → `hooks/session_start.py` (async/observability) — injects the bootstrap
  briefing.
- **PreToolUse** → `hooks/secret_guard.py` (sync **security**, **exit code 2**) — blocks a
  write/command carrying a secret.

Confirm the exact install handle in `.claude-plugin/marketplace.json`. To verify the
install: the `/` commands appear, the SessionStart hook injects the briefing, and planting
a secret in a tool input is blocked by `secret_guard` (exit 2).

Prefer the CLI without the plugin? Clone the repo from GitHub and install it:

```bash
git clone https://github.com/JasGujral/mokata-oss.git
cd mokata-oss
pip install -e .
```

See the [CLI reference](../reference/cli.md). To get the full workflow (slash commands,
tools, hooks) inside Claude Code **without** installing the plugin — or to wire mokata into
another harness — see [Use mokata without the plugin](use-without-plugin.md).
