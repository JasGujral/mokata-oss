# How-to: install the Claude Code plugin

<!-- mokata:directory-listing:start -->
> ⏳ **Pending Claude plugin-directory approval.** A one-click Claude Code **plugin** is
> **planned, not yet available** — mokata isn't registered on any Claude Code marketplace.
> The supported way to run mokata inside Claude Code today is the pip-first path:
> `pip install mokata` → `mokata setup claude`
> (see [Getting started](https://jasgujral.github.io/mokata-oss/getting-started/)).
> _(This notice auto-flips once the listing is approved — single source:
> `scripts/directory_listing.py`.)_
<!-- mokata:directory-listing:end -->

The rest of this page is an **experimental/advanced** note for the manual `/plugin marketplace
add` route from a local checkout — see also [Use mokata without the plugin](use-without-plugin.md).

The plugin is just a convenient bundle of the same artifacts `mokata setup claude` writes. If
you want to try the manual marketplace route from a local clone (advanced), `/plugin
marketplace add <path>` reads the `.claude-plugin/marketplace.json` in that directory and
registers it as a local marketplace named `mostack`:

```text
# experimental / advanced — from a local clone:
/plugin marketplace add ~/path/to/mokata-oss
/plugin install mokata@mostack
```

The `@mostack` handle is the local marketplace name. (A public marketplace submission is a
separate, later step for discoverability — it is **not** live yet.)

Either the supported setup path or the experimental route makes the slash commands available — `/mokata:brainstorm`, `/mokata:spec`, `/mokata:test`, `/mokata:develop`,
`/mokata:review`, `/mokata:debug`, `/mokata:optimize`, `/mokata:bug` — and wires both hooks (declared in
`hooks/hooks.json`):

- **SessionStart** → `hooks/session_start.py` (async/observability) — injects the bootstrap
  briefing.
- **PreToolUse** → `hooks/secret_guard.py` (sync **security**, **exit code 2**) — blocks a
  write/command carrying a secret.

Confirm the exact install handle in `.claude-plugin/marketplace.json`. To verify the
install: the `/` commands appear, the SessionStart hook injects the briefing, and planting
a secret in a tool input is blocked by `secret_guard` (exit 2).

Want just the terminal CLI? `pip install mokata` puts the `mokata` command on your PATH:

```bash
pip install mokata
```

> **Heads up:** `pip install` alone gives you the `mokata` command **in your terminal only** —
> it does **not** put mokata inside Claude Code (no slash commands, no hooks). For the in-Claude
> workflow, run **`mokata setup claude`** (the supported path — see
> [Getting started](../getting-started.md)). Why two ways:
> [How mokata uses an LLM: harness vs CLI](../concepts/execution-model.md).

See the [CLI reference](../reference/cli.md). To get the full workflow (slash commands,
tools, hooks) inside Claude Code **without** installing the plugin — or to wire mokata into
another harness — see [Use mokata without the plugin](use-without-plugin.md).
