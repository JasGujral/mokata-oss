# How-to: install the Claude Code plugin

<!-- mokata:directory-listing:start -->
> ⏳ **Pending Claude plugin-directory approval.** A one-click Claude Code **plugin** is
> **planned, not yet available** — mokata isn't registered on any Claude Code marketplace.
> The supported way to run mokata inside Claude Code today is the pip-first path:
> `pip install mokata` → `mokata setup claude`
> (see [Getting started](https://mokata.ai/getting-started/)).
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
`/mokata:review`, `/mokata:debug`, `/mokata:optimize`, `/mokata:bug` — and wires all three hooks (declared in
`hooks/hooks.json`):

- **SessionStart** → `hooks/session_start.py` (async/observability) — injects the bootstrap
  briefing.
- **PreToolUse** → `hooks/secret_guard.py` (sync **security**, **exit code 2**, matcher
  `Write|Edit|MultiEdit|Bash`) — blocks a write or shell command carrying a secret. **Never
  overridable:** no approval, and no flag, lifts it.
- **PreToolUse** → `hooks/gate_guard.py` (sync **methodology / run-state**, **exit code 2**,
  matcher `Write|Edit|MultiEdit|NotebookEdit`) — blocks a write that breaks the run's discipline:
  `spec-persisted`, `no-code-without-failing-test`, `spec-scope`. **Overridable** — but only
  explicitly, by a human: `mokata gate override <gate> --reason "<why>"`, re-confirmed
  interactively, scoped to that session, and written to the audit ledger. There is deliberately
  **no env-var kill switch, no MCP tool, and no slash command** for it.

Both are *sync* blocks, and they differ in kind: security is absolute, methodology is
accountable. The gate-guard fires **only inside an active mokata run**, and never on a test
file — you must be able to write the failing test.

Confirm the exact install handle in `.claude-plugin/marketplace.json`. To verify the
install: the `/` commands appear, the SessionStart hook injects the briefing, planting a secret
in a tool input is blocked by `secret_guard` (exit 2), and — mid-run, before a failing test —
an implementation write is blocked by `gate_guard` (exit 2).

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
