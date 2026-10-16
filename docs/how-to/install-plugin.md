# How-to: install the Claude Code plugin

<!-- mokata:directory-listing:start -->
> **Installs today as a Claude Code plugin** from mokata's own marketplace — inside Claude
> Code run `/plugin marketplace add JasGujral/mokata-oss`, then `/plugin install mokata@mostack`
> (the plugin starts its MCP server through `uvx`, so [`uv`](https://docs.astral.sh/uv/) must be
> on PATH).
> ⏳ **Pending: the community-marketplace listing** (pending submission) — mokata is not yet
> listed in Claude's community plugin marketplace; until it is, use the two commands above.
> _(This notice auto-flips once the listing exists — single source:
> `scripts/directory_listing.py`.)_
<!-- mokata:directory-listing:end -->

## Install (two commands, inside Claude Code)

```text
/plugin marketplace add JasGujral/mokata-oss
/plugin install mokata@mostack
```

`/plugin marketplace add` reads `.claude-plugin/marketplace.json` from the GitHub repo and
registers it as a marketplace named `mostack` — a self-hosted marketplace needs no approval — and
`@mostack` is that name. Nothing is installed into your project's Python environment: the
plugin's hooks run from the plugin's own checkout, and its MCP server is launched with
`uvx --from mokata==<plugin version> mokata-mcp`, pinned to the plugin's version.

**Requirement: [`uv`](https://docs.astral.sh/uv/) on your PATH** (it provides `uvx`). Without
it the commands, skills and hooks still load but the MCP server cannot start; `mokata doctor`
names it.

From a local clone (contributors), the same route works with a path:
`/plugin marketplace add ~/path/to/mokata-oss`. Claude Code caches one copy per plugin version,
so an edited copy under an unchanged version is served stale — use `claude --plugin-dir` while
developing.

> **Command form ↔ install route.** The namespaced `/mokata:<name>` form below is the **plugin**
> render. Via the `mokata setup claude` route the same commands appear **bare** in your `/` menu
> — `/<name>` (drop the `mokata:` prefix). Use one route per repo, not both: both wire the same
> hooks.

Either route makes the slash commands available — `/mokata:brainstorm`, `/mokata:spec`, `/mokata:test`, `/mokata:develop`,
`/mokata:review`, `/mokata:debug`, `/mokata:optimize`, `/mokata:bug` — and wires its hooks (declared in
`hooks/hooks.json`):

- **SessionStart** → `hooks/session_start.py` (async/observability) — injects the bootstrap
  briefing.
- **PreToolUse** → `hooks/secret_guard.py` (sync **security**, **exit code 2**, matcher
  `Write|Edit|MultiEdit|Bash`) — blocks a write or shell command carrying a secret. **Never
  overridable:** no approval, and no flag, lifts it.
- **PreToolUse** → `hooks/gate_guard.py` (sync **methodology / run-state**, **exit code 2**,
  matcher `Write|Edit|MultiEdit|NotebookEdit`) — blocks a write that breaks the run's discipline:
  `approach-approval`, `spec-persisted`, `no-code-without-failing-test`, `spec-scope`.
  **Overridable** — but only
  explicitly, by a human: `mokata gate override <gate> --reason "<why>"`, re-confirmed
  interactively, scoped to that session, and written to the audit ledger. There is deliberately
  **no env-var kill switch, no MCP tool, and no slash command** for it.
- **PostToolUse** → `hooks/dirty_track.py` (async/bookkeeping, matcher
  `Write|Edit|MultiEdit|NotebookEdit`) — records touched paths into the session graph dirty-set
  so the code graph is reconciled before the next query answers. It never blocks (always exits 0).

The two `PreToolUse` guards are *sync* blocks, and they differ in kind: security is absolute, methodology is
accountable. The gate-guard fires **only inside an active mokata run**, and never on a test
file — you must be able to write the failing test.

Confirm the exact install handle in `.claude-plugin/marketplace.json`. To verify the
install: the `/` commands appear, the SessionStart hook injects the briefing, planting a secret
in a tool input is blocked by `secret_guard` (exit 2), and — mid-run, before a failing test —
an implementation write is blocked by `gate_guard` (exit 2).

Want the terminal CLI too (`mokata approve`, `mokata doctor`, CI)? Install it **isolated**, in
its own environment:

```bash
pipx install mokata            # or: uv tool install mokata
# or, on macOS/Linux:
brew install JasGujral/mokata/mokata
```

Plain `pip install mokata` works too, last — inside a project venv this mixes mokata's deps into
yours — prefer the above.

> **Heads up:** the CLI alone gives you the `mokata` command **in your terminal only** — it does
> **not** put mokata inside Claude Code (no slash commands, no hooks). The plugin above does that;
> so does **`mokata setup claude`** (see [Getting started](../getting-started.md)). Why two ways:
> [How mokata uses an LLM: harness vs CLI](../concepts/execution-model.md).

See the [CLI reference](../reference/cli.md). To get the full workflow (slash commands,
tools, hooks) inside Claude Code **without** installing the plugin — or to wire mokata into
another harness — see [Use mokata without the plugin](use-without-plugin.md).
