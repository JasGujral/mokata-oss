# How-to: install the Claude Code plugin

mokata ships as a Claude Code plugin under the **MoStack** marketplace. You can install it
from the public GitHub repo, or straight from a local clone — **both are the same plugin,
and neither needs the community marketplace registration.**

**From the public repo:**

```text
/plugin marketplace add JasGujral/mokata-oss
/plugin install mokata@mostack
```

**From a local clone** (no registration needed — great for the freshest copy or for testing
before a release is public):

```text
/plugin marketplace add ~/Documents/Development/claude/cowork/mokata
/plugin install mokata@mostack
```

`/plugin marketplace add <path>` reads the `.claude-plugin/marketplace.json` in that
directory and registers it as a local marketplace named `mostack`; the `@mostack` handle is
the same either way. (The community marketplace submission is a separate, later step purely
for public discoverability — it is **not** required to use the plugin.)

Either route makes the slash commands available — `/mokata:brainstorm`, `/mokata:spec`, `/mokata:test`, `/mokata:develop`,
`/mokata:review`, `/mokata:debug`, `/mokata:optimize`, `/mokata:bug` — and wires both hooks (declared in
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

> **Heads up:** `pip install` gives you the `mokata` command **in your terminal only** — it
> does **not** put mokata inside Claude Code (no slash commands, no hooks). For the in-Claude
> workflow without this plugin, run **`mokata setup claude`** (see below). Why two ways:
> [How mokata uses an LLM: harness vs CLI](../concepts/execution-model.md).

See the [CLI reference](../reference/cli.md). To get the full workflow (slash commands,
tools, hooks) inside Claude Code **without** installing the plugin — or to wire mokata into
another harness — see [Use mokata without the plugin](use-without-plugin.md).
