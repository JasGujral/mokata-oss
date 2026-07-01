# mokata for VS Code

Bring mokata's governance and memory **into the editor** — a read-only view of what mokata
already knows, surfaced inline so you don't have to switch to the terminal to see it.

This extension is a **thin, read-only client** over the mokata CLI. It runs no engine logic of
its own and it **never performs a durable write** — every change stays human-gated in the CLI.

## What it shows

- **Status-bar badge** — the mokata stage badge / one-line status (`mokata status`), refreshed
  on a timer and whenever anything under `.mokata/` changes.
- **A "mokata: Governance & Memory" panel** (activity bar) with four read-only sections, each
  rendered straight from the CLI:
  - **Run progress & lanes** — `mokata progress --lanes`
  - **Governance & gate verdicts** — `mokata govern`
  - **Memory & health** — `mokata memory` (including the Stage 59 health nudge)
  - **Status** — `mokata status`
- **Commands** (Command Palette, all prefixed `mokata:`) for each view, a **Refresh**, and
  **“Run a /mokata command in the terminal…”** — which *types* a command into a terminal for
  **you** to run. The extension stages it; you press Enter; mokata's own gates then apply.

## Read-only & human-gated (by design)

- The extension only ever runs mokata's **read-only** subcommands (`status`, `progress`,
  `govern`, `memory`). There is no code path that writes.
- Anything that changes state (init, spec, develop, ship, remember, …) is **deferred to the
  terminal**, where you review and run it and mokata's human gates fire as normal.

## Opt-in & degrade-clean

- **Opt-in:** installing the extension is the opt-in; you can also toggle `mokata.enable`.
- **Degrade-clean:** if mokata isn't installed, or this folder isn't a mokata project, you get
  a short, friendly message (“mokata is not installed…”, “mokata is not initialized…”) — never
  an error spew.

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `mokata.enable` | `true` | Show the badge + panel in this editor (opt-out toggle). |
| `mokata.cliPath` | `mokata` | Path to the mokata CLI if it isn't on your `PATH`. |
| `mokata.refreshIntervalSeconds` | `30` | Badge refresh cadence; `0` disables the timer (the `.mokata/` file-watch still refreshes). |

## Build / test (for contributors)

```bash
cd editors/vscode
npm install
npm run compile     # tsc -> out/
npm test            # node --test ./out/test  (pure-helper unit tests)
```

Then press **F5** in VS Code to launch an Extension Development Host and try it on a mokata
project. (Packaging: `npx vsce package`.)

## Use mokata in GitHub Copilot Chat

Two deeper integrations let mokata's governed brain show up **inside Copilot Chat** (which runs
in VS Code) — both read-only, with writes still human-gated.

### 1. The `@mokata` chat participant

Type **`@mokata`** in Copilot Chat to pull up mokata's state, read-only:

- `@mokata /status` — the stack status
- `@mokata /progress` — run progress & parallel lanes
- `@mokata /memory` — memory + the health nudge
- `@mokata /why` — governance & gate verdicts (why a gate blocked)

It renders the output of the **same read-only CLI commands** the panel uses. For anything that
would **change** your project (spec, develop, ship, remember, …), `@mokata` **proposes** the
exact `/mokata:` / `mokata …` command and offers a button to **stage** it in a terminal — you
press Enter, and mokata's human gates apply. The participant **never writes**.

(Needs VS Code 1.90+ with Copilot Chat. Where the Chat API isn't available it degrades
cleanly — the badge and panel still work.)

### 2. Wire `mokata-mcp` into Copilot Chat (MCP)

So Copilot can call mokata's **governed MCP tools** directly, register the bundled `mokata-mcp`
server. Run **“mokata: Register mokata-mcp with Copilot Chat (MCP)…”** from the Command Palette
— it merges (never clobbers) this into your workspace `.vscode/mcp.json`:

```json
{
  "servers": {
    "mokata": { "command": "mokata-mcp", "args": [], "type": "stdio" }
  }
}
```

(Same content as [`mcp/mokata.mcp.json`](./mcp/mokata.mcp.json).) **Honest about the user
step:** VS Code then shows a **Start / Trust** prompt for the server — that step is yours; the
extension can't auto-trust it. Reads through the server are safe; mokata's MCP **write** tools
stay human-gated via the WriteGate **inside the server**, so a Copilot tool call that would
write still hits mokata's gate.

## Roadmap — other editors (honest scope)

This extension is **VS Code only** today. **JetBrains** and **Neovim** support are on the
**roadmap** but are **not built yet** — they are intentionally *not* stubbed here. When they
land they'll follow the same rule: a thin, read-only client that defers every write to the
human-gated CLI.

---

Apache-2.0. Part of [mokata](https://github.com/mostack/mokata).
