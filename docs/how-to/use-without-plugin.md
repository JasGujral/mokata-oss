# How-to: use mokata without the plugin

The Claude Code plugin is the easiest way to run mokata, but it's not the only way. A
plugin is just a convenient **bundle** of three portable artifacts that mokata already
ships:

1. **Prompt templates** — the pipeline commands in `templates/commands/*.md`
   (`brainstorm`, `spec`, `test`, `develop`, `review`, `debug`, `optimize`, `bug`).
2. **Tools** — the `mokata` CLI and the bundled `mokata-mcp` MCP server (knowledge graph,
   memory, audit, governance).
3. **Enforcement** — the hook scripts in `hooks/` (SessionStart briefing + secret-guard).

Because these are plain files plus a CLI, **any agent harness** that supports custom
commands, MCP, or shell tools can consume them directly — no marketplace install required.
mokata supplies the structure and the tools; the **harness supplies the LLM** (the "brain").
This runs entirely on your machine using your existing Claude Code sign-in — **no API key,
nothing leaves your computer.**

> **Two no-marketplace routes.** There are two ways to run mokata in Claude Code without the
> public marketplace (both need no registration): install the **plugin from a local clone**
> (`/plugin marketplace add ~/path/to/mokata-oss` — see [Install the plugin](install-plugin.md)),
> or use **`mokata setup claude`** below. This page covers the latter.

## The one-command way (recommended)

After installing the CLI, a single command wires all three pieces into Claude Code:

```bash
# 1. install the CLI once (puts `mokata` + `mokata-mcp` on PATH)
git clone https://github.com/JasGujral/mokata-oss.git
cd mokata-oss
pip install -e ".[mcp]"

# 2. in the project you want to use mokata on:
cd /path/to/your/project
mokata setup claude
```

`mokata setup claude` is **human-gated** — it shows exactly what it will create and merge,
then waits for your confirmation. It:

- runs `mokata init` (if the project isn't set up yet),
- copies the slash commands into `.claude/commands/`,
- registers the `mokata-mcp` server in `.mcp.json`,
- wires the SessionStart + secret-guard hooks into `.claude/settings.json`.

Then **restart Claude Code** in that project. You'll have `/brainstorm`, `/spec`, `/test`,
`/develop`, `/review`, `/debug`, `/optimize`, `/bug`, the bootstrap briefing, the
secret-guard, and the mokata MCP tools — the same experience as the plugin.

### Options

```bash
mokata setup claude --profile full     # choose the profile (minimal | standard | full)
mokata setup claude --scope user       # install to ~/.claude (every project) instead of one repo
mokata setup claude --no-hooks         # skip the hooks; wire only commands + MCP
mokata setup claude --yes              # non-interactive (CI / scripted)
```

Existing `.mcp.json` / `settings.json` entries are **merged, not overwritten**, and the
command is idempotent (re-running converges). To reverse everything:

```bash
mokata unsetup claude                  # removes the commands, MCP entry, and hooks
```

`unsetup` leaves your `.mokata/` config intact (use `mokata reset` for that).

## What it does under the hood (manual wiring)

`mokata setup` automates the steps below — useful if you want to do it by hand, adapt it,
or understand exactly what's written.

### 1. Slash commands

Claude Code auto-discovers `.claude/commands/*.md` and derives the command name from the
filename:

```bash
MOKATA_HOME=~/code/mokata-oss
mkdir -p .claude/commands
cp "$MOKATA_HOME"/templates/commands/*.md .claude/commands/
```

Use `~/.claude/commands/` for every project (the `--scope user` equivalent).

### 2. Tools — the MCP server

```bash
claude mcp add --transport stdio --scope project mokata -- mokata-mcp
```

…or write `.mcp.json` at the project root by hand:

```json
{
  "mcpServers": {
    "mokata": { "command": "mokata-mcp", "args": [] }
  }
}
```

Write tools are propose-only unless explicitly confirmed; secrets are a hard block.

### 3. Enforcement — the hooks

Add to `.claude/settings.json`, pointing at the scripts in your clone (manual setup has no
`${CLAUDE_PLUGIN_ROOT}`, so use an absolute path):

```json
{
  "hooks": {
    "SessionStart": [
      { "hooks": [
        { "type": "command",
          "command": "python3 \"/ABSOLUTE/PATH/TO/mokata-oss/hooks/session_start.py\"" }
      ] }
    ],
    "PreToolUse": [
      { "matcher": "Write|Edit|MultiEdit|Bash",
        "hooks": [
          { "type": "command",
            "command": "python3 \"/ABSOLUTE/PATH/TO/mokata-oss/hooks/secret_guard.py\"" }
        ] }
    ]
  }
}
```

`SessionStart` injects the bootstrap briefing; `PreToolUse` blocks a secret-bearing write or
command with **exit code 2**.

### Plugin vs. manual vs. `mokata setup`

All three are functionally identical. The plugin bundles everything and resolves paths via
`${CLAUDE_PLUGIN_ROOT}`; `mokata setup` and the manual steps point at your checkout instead.
If you later install the plugin, run `mokata unsetup claude` first to avoid duplication.

## Other harnesses

The artifacts are harness-agnostic; only the glue differs:

- **Prompts** — `templates/commands/*.md` are plain Markdown. Point any harness's
  custom-command mechanism at them.
- **Tools** — `mokata-mcp` is a standard stdio MCP server, so any MCP-capable harness can
  load it; and the `mokata` CLI works from any shell-tool-capable agent
  (`mokata query callers foo`, `mokata doctor`, `mokata preview`).

`mokata setup` currently targets `claude`. Worked examples for **Gemini CLI** and **Codex**
are on the roadmap (the same three steps, mapped to each harness's conventions). See also
[Integrate with other AI tools](integrate-other-ai-tools.md).
