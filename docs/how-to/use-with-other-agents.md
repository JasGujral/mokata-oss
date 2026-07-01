# Use mokata with other AI agents

mokata is not Claude-Code-only. It runs behind a thin **harness boundary**, so the same
engine — the spec-driven pipeline, the completeness gate, the knowledge graph, self-healing
memory, governance — works under the AI coding agent you already use. mokata wires the
`/mokata:` command set into each agent's **native** command surface and degrades **clearly**
where an agent lacks a capability (it never pretends).

> One command per agent: `mokata setup <agent>` (human-gated, idempotent, reversible with
> `mokata unsetup <agent>`). See the live matrix any time with `mokata harness`.

## What's wired, what degrades (per agent)

| Agent | Commands → native surface | Context | PreToolUse hook | Subagents | MCP server |
|---|---|---|---|---|---|
| **Claude Code** (`claude`) | ✅ `.claude/commands/*.md` | ✅ | ✅ secret-guard + SessionStart briefing | ✅ native fan-out | ✅ auto (`.mcp.json`) |
| **Codex** (`codex`) | ✅ `.codex/prompts/*.md` | ✅ | ❌ *degrades* | ❌ *degrades* | manual (TOML config) |
| **Cursor** (`cursor`) | ✅ `.cursor/commands/*.md` | ✅ `.cursor/rules` | ❌ *degrades* | ❌ *degrades* | ✅ auto (`.cursor/mcp.json`) |
| **GitHub Copilot** (`copilot`) | ✅ `.github/prompts/*.prompt.md` | ✅ `copilot-instructions.md` | ❌ *degrades* | ❌ *degrades* | manual (VS Code `mcp.json`) |
| **Windsurf** (`windsurf`) | ✅ `.windsurf/workflows/*.md` | ✅ `.windsurf/rules` | ❌ *degrades* | ❌ *degrades* | manual (`~/.codeium/windsurf/`) |
| **Gemini CLI** (`gemini`) | ✅ `.gemini/commands/*.toml` | ✅ `GEMINI.md` | ❌ *degrades* | ❌ *degrades* | ✅ auto (`.gemini/settings.json`) |
| **Aider** (`aider`) | ❌ reference prompts only¹ | ✅ conventions / `--read` | ❌ *degrades* | ❌ *degrades* | manual / none |

¹ Aider has **no user-authored slash-command file system** (its `/commands` are built-in), so
mokata declares `commands` **absent** for it and ships the `/mokata:` prompts as **reference**
files (`.aider/mokata-commands/`) you `/read` or paste — never pretended to be native commands.

### What "degrades clearly" means

mokata declares a capability **absent** unless it can verify the agent really supports it
(the Stage-52 inviolable: *when unsure, declare absent*). When the engine needs an absent
capability the harness boundary returns a **clear** result that **names** the missing
capability — never a silent no-op of a gate:

- **No PreToolUse hook** (every agent except Claude Code): the agent won't run mokata's
  secret-guard on each write. Durable-write protection still holds — mokata's **own** gated
  CLI/MCP `WriteGate` scans for secrets, human-gates, and audits **every** durable write
  regardless of the hook. Run writes through `mokata` (or its MCP tools), not raw.
- **No native subagents**: parallel fan-out falls back to mokata's sequential gated flow
  (the cost estimate + two-stage review still apply); nothing is skipped.
- **MCP is a manual step**: register the `mokata-mcp` server with the agent per its docs
  (mokata states this in the setup plan rather than half-wiring a schema it can't be sure of).

## Quickstart

```bash
# wire mokata into your agent (shows exactly what it will write, then asks)
mokata setup cursor          # or: copilot · windsurf · gemini · aider · codex · claude
# … point the agent at the generated commands; register mokata-mcp if the table says "manual"
mokata harness               # see the full capability matrix any time
mokata unsetup cursor        # reverse it cleanly — no residue
```

Setup is **human-gated** (it previews every file it will write/merge, then waits for your
`yes`), **idempotent** (re-running converges), and **merge-safe** (an existing `mcp.json` /
`settings.json` keeps its other entries). `unsetup` removes exactly what setup wrote and
leaves your `.mokata/` config untouched.

## See also
- [Use mokata without the plugin](use-without-plugin.md)
- [Integrate with other AI tools](integrate-other-ai-tools.md)
- [How mokata uses an LLM (harness vs CLI)](../concepts/execution-model.md)
