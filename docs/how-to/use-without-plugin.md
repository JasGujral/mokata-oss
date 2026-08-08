# How-to: use mokata without the plugin

The pip-first `mokata setup claude` path (below) is the supported way to run mokata inside
Claude Code today — a one-click Claude Code plugin is planned but not yet available. Either
way, what gets wired is just a **bundle** of three portable artifacts that mokata already
ships:

1. **Prompt templates** — the pipeline commands in `templates/commands/*.md`
   (`brainstorm`, `spec`, `test`, `develop`, `review`, `debug`, `optimize`, `bug`).
2. **Tools** — the `mokata` CLI and the bundled `mokata-mcp` MCP server (knowledge graph,
   memory, audit, governance).
3. **Enforcement** — the hook scripts in `hooks/` (SessionStart briefing + the two PreToolUse
   blocks: the **secret-guard** and the run-state **gate-guard**).

Because these are plain files plus a CLI, **any agent harness** that supports custom
commands, MCP, or shell tools can consume them directly — no marketplace install required.
mokata supplies the structure and the tools; the **harness supplies the LLM** (the "brain").
This runs entirely on your machine using your existing Claude Code sign-in — **no API key,
nothing leaves your computer.**

> **The supported route today is `mokata setup claude`** (this page). A one-click Claude Code
> plugin is planned but not yet registered on any marketplace; the manual `/plugin marketplace
> add` route from a checkout is an experimental/advanced alternative (see
> [Install the plugin](install-plugin.md)).

> **`pip install` alone is not enough to use mokata *in* Claude Code.** The `mokata` CLI is
> terminal-only (the engine without a brain). The **`mokata setup claude`** command on this
> page is what wires the slash commands, MCP tools, and hooks into Claude Code so Claude drives
> them. See [How mokata uses an LLM: harness vs CLI](../concepts/execution-model.md).

## The one-command way (recommended)

After installing the CLI, a single command wires all three pieces into Claude Code:

```bash
# 1. install the CLI once (puts `mokata` + `mokata-mcp` on PATH)
pip install mokata               # on Python ≥ 3.10 the MCP SDK comes with it (default dep)

# 2. in the project you want to use mokata on:
cd /path/to/your/project
mokata setup claude
```

(New here? Start from [Getting started](../getting-started.md) for the full pip-first path.)

`mokata setup claude` is **human-gated** — it shows exactly what it will create and merge,
then waits for your confirmation. It:

- runs `mokata init` (if the project isn't set up yet),
- copies the slash commands into `.claude/commands/`,
- writes mokata's Agent Skills into `.claude/skills/` (and prunes stale mokata ones on re-run),
- registers the `mokata-mcp` server in `.mcp.json`,
- wires **three** hooks into `.claude/settings.json` — the SessionStart briefing, the PreToolUse
  **secret-guard**, and the PreToolUse run-state **gate-guard**,
- wires the always-on **pipeline-stage badge** as a Claude Code `statusLine` (default-on;
  composes over any statusLine you already have — see the
  [stage badge](../concepts/pipeline.md#the-always-on-stage-badge-stage-54b)). Opt out with
  `mokata config set settings.ux.statusline false`, or `--no-hooks` to skip the
  settings.json wiring entirely.

Then **restart Claude Code** in that project. You'll have `/brainstorm`, `/spec`, `/test`,
`/develop`, `/review`, `/debug`, `/optimize`, `/bug`, the bootstrap briefing, the
secret-guard, the run-state gate-guard, and the mokata MCP tools — the same experience as the plugin.

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

Every MCP write tool is **propose-first**: the call returns a `proposal_id` and writes nothing.
A **human** mints the approval out-of-band in their own terminal — `mokata approve <id>` — and
only then does a re-call with that `proposal_id` commit, once. Secrets remain a hard block that
no approval lifts.

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
        ] },
      { "matcher": "Write|Edit|MultiEdit|NotebookEdit",
        "hooks": [
          { "type": "command",
            "command": "mokata-hook gate-guard" }
        ] }
    ]
  }
}
```

**Wire both `PreToolUse` entries.** If you wire only the secret-guard you get *no run-state
gates at all* — your spec/TDD/scope enforcement silently never fires, while everything looks
installed. The two blocks are different in kind:

| hook | matcher | what it blocks | overridable? |
|---|---|---|---|
| `mokata-hook secret-guard` | `Write\|Edit\|MultiEdit\|Bash` | a write **or shell command** carrying a secret — a **security** block | **Never.** No approval lifts it. |
| `mokata-hook gate-guard` | `Write\|Edit\|MultiEdit\|NotebookEdit` | a write that breaks the run's **methodology** — the four run-state gates | **Yes**, explicitly: `mokata gate override <gate> --reason "<why>"` |

Both refuse with **exit code 2** and one stderr line, `BLOCKED [<gate>] <reason>`. The
gate-guard's four gates are `approach-approval` (the run is still in brainstorm — no approach
approved yet), `spec-persisted` (an approach is approved but no spec is emitted),
`no-code-without-failing-test` (a spec exists but no failing test is on record), and `spec-scope`
(the write is outside the spec's authorized surface, spells a **deferred** marker, or a spec
amend is in progress). They fire **only inside an active mokata run** — hand-editing a repo
outside a run is never policed — and **test files are always writable**, because you must be
able to write the failing test. Note the matchers **differ**: the gate-guard does not match
`Bash`, so a `sed -i` through the shell is not policed (a known, documented hole; the
*secret*-guard does match `Bash`).

`SessionStart` injects the bootstrap briefing (the `--plugin-root` lets `/init` locate
the bundled engine — manual setup has no `${CLAUDE_PLUGIN_ROOT}`). Just let `mokata setup
claude` write the whole block for you to get the absolute-path form automatically.

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
non-blocking `python3: command not found` line and the SessionStart briefing / secret-guard /
gate-guard silently not running.

mokata now launches the hooks through the **`mokata-hook` console entry point** (the
`session-start`, `secret-guard`, and `gate-guard` subcommands). When you `pip install` mokata, `mokata-hook`
lands on PATH exactly like the `mokata` CLI and the `mokata-mcp` server — so if the MCP server
resolves for you (it must, for its tools to work), the hooks resolve identically. No bare
`python3`, no `sh`, no PATH guessing. `mokata setup` additionally pins it to its absolute path and
wires it in **exec form** — `{"command": "<abs>/mokata-hook", "args": ["secret-guard"]}` — which
Claude Code spawns directly, with no shell involved on any platform.

**The plugin route (no `pip install`, or a GUI-launched app).** A plugin's `hooks.json` is
static — it can't carry the absolute path `mokata setup` writes — so it invokes a
**self-resolving shim**, `hooks/mokata-hook-launch`, under `${CLAUDE_PLUGIN_ROOT}`. The shim
runs the same ladder at hook time: `$MOKATA_HOOK` → `mokata-hook` on PATH → the `mokata-hook`
sitting beside a resolved Python 3 → that interpreter running the packaged module directly
(mokata's core is dependency-free, so this works with no install at all). Set `MOKATA_PYTHON`
to your interpreter's absolute path if it still can't find one.

If **nothing** resolves, the shim prints one line naming the fix and **exits 1** — a
misconfiguration, never a silent success, because a security gate that quietly doesn't run is
worse than no gate. Exit 1 does not block your tool call (only exit 2 does, and that stays
reserved for a real secret). The fix it names is `mokata setup claude`, which rewires the hooks
to absolute paths. `mokata doctor` reports the same thing before you hit it: a wired hook whose
command doesn't resolve is an error finding — *"gates are NOT firing"* — naming the exact
command it tried.

On **Windows**, `hooks.json` pins **`"shell": "bash"`** on every hook, so the shim runs under the
Git Bash from [Git for Windows](https://git-scm.com/downloads/win). If Git Bash isn't installed,
Claude Code fails with a named error rather than skipping the hook.

`mokata-hook-launch.cmd` ships beside the POSIX shim and carries the same ladder and the same exit
codes, but it is **not on the hook path**: cmd.exe is never a hook shell, so **no** `PATHEXT`
completion of the extension-less path in `hooks.json` ever happens. It is there for invoking the
launcher directly from a `cmd.exe` prompt.
