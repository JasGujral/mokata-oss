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

> **`pip install` alone is not enough to use mokata *in* Claude Code.** The `mokata` CLI is
> terminal-only (the engine without a brain). The **`mokata setup claude`** command on this
> page is what wires the slash commands, MCP tools, and hooks into Claude Code so Claude drives
> them. See [How mokata uses an LLM: harness vs CLI](../concepts/execution-model.md).

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
- wires the SessionStart + secret-guard hooks into `.claude/settings.json`,
- wires the always-on **pipeline-stage badge** as a Claude Code `statusLine` (default-on;
  composes over any statusLine you already have — see the
  [stage badge](../concepts/pipeline.md#the-always-on-stage-badge-stage-54b)). Opt out with
  `mokata config set settings.ux.statusline false`, or `--no-hooks` to skip the
  settings.json wiring entirely.

Then **restart Claude Code** in that project. You'll have `/mokata:brainstorm`, `/mokata:spec`, `/mokata:test`,
`/mokata:develop`, `/mokata:review`, `/mokata:debug`, `/mokata:optimize`, `/mokata:bug`, the bootstrap briefing, the
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

Add to `.claude/settings.json`. The hooks are launched through the **`mokata-hook` console
entry point** — the same PATH-resolved mechanism the bundled `mokata-mcp` server uses (both
land on PATH when you `pip install` mokata), so there is no bare `python3` / `sh` / `launch.sh`
resolution to fail on. `mokata setup` writes exactly this block, resolving `mokata-hook` to its
absolute path; by hand the bare name works just as well:

```json
{
  "hooks": {
    "SessionStart": [
      { "hooks": [
        { "type": "command",
          "command": "mokata-hook session-start --plugin-root \"/ABSOLUTE/PATH/TO/mokata-oss\"" }
      ] }
    ],
    "PreToolUse": [
      { "matcher": "Write|Edit|MultiEdit|Bash",
        "hooks": [
          { "type": "command",
            "command": "mokata-hook secret-guard" }
        ] }
    ]
  }
}
```

`SessionStart` injects the bootstrap briefing (the `--plugin-root` lets `/mokata:init` locate
the bundled engine — manual setup has no `${CLAUDE_PLUGIN_ROOT}`); `PreToolUse` blocks a
secret-bearing write or command with **exit code 2**. Just let `mokata setup claude` write the
block for you to get the absolute-path form automatically.

### Plugin vs. manual vs. `mokata setup`

All three are functionally identical. All launch the hooks via the `mokata-hook` entry point;
the plugin additionally forwards `${CLAUDE_PLUGIN_ROOT}` to it, while `mokata setup` and the
manual steps forward your checkout path instead. If you later install the plugin, run
`mokata unsetup claude` first to avoid duplication.

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

## Cross-platform hooks (no `python3: command not found`)

Earlier builds launched the hooks with a bare `python3` (via `sh launch.sh`), which failed to
resolve in a few common setups — **Windows** names the interpreter `python` or `py -3`; a
**GUI-launched Claude Code on macOS** runs hooks with a minimal `PATH` that often omits
Homebrew (`/opt/homebrew/bin`), pyenv shims, or `/usr/local/bin`. The symptom was a
non-blocking `python3: command not found` line and the SessionStart briefing / secret-guard
silently not running.

mokata now launches the hooks through the **`mokata-hook` console entry point** (the
`session-start` / `secret-guard` subcommands). When you `pip install` mokata, `mokata-hook`
lands on PATH exactly like the `mokata` CLI and the `mokata-mcp` server — so if the MCP server
resolves for you (it must, for its tools to work), the hooks resolve identically. No bare
`python3`, no `sh`, no PATH guessing. `mokata setup` additionally pins it to its absolute path.

**Last-resort fallback (pure plugin, no `pip install`).** If you ran the plugin *without*
installing the package, `mokata-hook` isn't on PATH. The plugin then materializes
`hooks/launch.sh` at init with an absolute interpreter, and as a final safety net that launcher
resolves a Python 3 at run time (`python3` → `python` → `py -3`, then common install
locations); set `MOKATA_PYTHON` to your interpreter's absolute path if it still can't find one.
Either way a missing interpreter prints one clear line and **exits 0 — it never blocks your
session.** On **Windows**, install [Git for Windows](https://git-scm.com/downloads/win) — Claude
Code already recommends it, and the fallback launcher runs under its bundled Git Bash.
