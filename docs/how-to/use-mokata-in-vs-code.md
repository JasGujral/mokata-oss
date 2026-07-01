# Use mokata in VS Code

mokata's governance and memory can show up **where you already work** — inside VS Code. The
extension is a **thin, read-only client** over the mokata CLI: it renders what mokata already
knows, and it **never performs a durable write** (every change stays human-gated in the CLI).

> **Honest scope:** the editor extension is **VS Code only** today. JetBrains and Neovim are on
> the [roadmap](#roadmap-jetbrains--neovim) — not built yet, and intentionally not stubbed.

## Install

The extension lives in the mokata repo under [`editors/vscode/`](https://github.com/mostack/mokata/tree/main/editors/vscode).
Until it's published to the Marketplace, build it from source:

```bash
cd editors/vscode
npm install
npm run compile          # tsc -> out/
npx vsce package         # produces mokata-vscode-*.vsix
```

Then in VS Code: **Extensions ▸ … ▸ Install from VSIX…** and pick the `.vsix`. (For
development, open `editors/vscode` in VS Code and press **F5** for an Extension Development
Host.) You need the mokata CLI installed too — `pipx install mokata` (or `pip install mokata`).

## What it shows

- A **status-bar badge** — the mokata stage badge / one-line `mokata status`, refreshed on a
  timer and whenever anything under `.mokata/` changes.
- A **“mokata: Governance & Memory” panel** in the activity bar, with four read-only sections,
  each rendered straight from the CLI:
  - **Run progress & lanes** — `mokata progress --lanes`
  - **Governance & gate verdicts** — `mokata govern`
  - **Memory & health** — `mokata memory` (incl. the memory-health nudge)
  - **Status** — `mokata status`
- **Command Palette** commands (all prefixed `mokata:`) for each view, a **Refresh**, and
  **“Run a /mokata command in the terminal…”**.

## Read-only, with writes deferred to you

The extension only ever runs mokata's **read-only** subcommands. Anything that changes
state — `init`, `spec`, `develop`, `ship`, `remember`, … — is **deferred to a terminal**: the
“Run a /mokata command…” action *types* the command into a VS Code terminal for **you** to
review and run. The extension stages it; you press Enter; **mokata's own human gates fire as
normal**. There is no code path in the extension that writes.

## Use mokata in GitHub Copilot Chat

The same extension goes deeper than the Stage-63 Copilot prompt-files: it makes mokata reachable
**inside Copilot Chat** (which lives in VS Code) — read-only, with writes still human-gated.

### The `@mokata` chat participant

Type **`@mokata`** in Copilot Chat to pull up mokata's state without leaving the chat:

- `@mokata /status` — the stack status
- `@mokata /progress` — run progress & parallel lanes
- `@mokata /memory` — memory + the health nudge
- `@mokata /why` — governance & gate verdicts (why a gate blocked)

These render the output of the **same read-only CLI commands** the panel uses (via the Stage-64
thin client's `READ_COMMANDS` whitelist). For anything that would **change** your project
(`spec`, `develop`, `ship`, `remember`, …), `@mokata` **proposes** the exact `/mokata:` /
`mokata …` command and offers a button to **stage** it in a terminal — you press Enter and
mokata's human gates apply. **The participant never writes.** It needs VS Code 1.90+ with
Copilot Chat; where the Chat API is absent it degrades cleanly (the badge and panel still work).

### Wire `mokata-mcp` into Copilot Chat (MCP)

So Copilot can call mokata's **governed MCP tools** directly (query / recall / govern …),
register the bundled `mokata-mcp` server. Run **“mokata: Register mokata-mcp with Copilot Chat
(MCP)…”** from the Command Palette — it merges (never clobbers) this into your workspace
`.vscode/mcp.json`:

```json
{
  "servers": {
    "mokata": { "command": "mokata-mcp", "args": [], "type": "stdio" }
  }
}
```

(The canonical snippet ships at `editors/vscode/mcp/mokata.mcp.json`.) **Honest about the user
step:** VS Code then shows a **Start / Trust** prompt for the server — that step is *yours*; the
extension can't auto-trust an MCP server. Reads through the server are safe; mokata's MCP
**write** tools stay human-gated by the WriteGate **inside the server**, so even a Copilot tool
call that would write still hits mokata's human gate.

## Opt-in & degrade-clean

- **Opt-in** — installing the extension is the opt-in; you can also flip `mokata.enable` off to
  hide it in a given editor. The `@mokata` participant and MCP wiring are likewise only active
  when you have Copilot Chat / start the server.
- **Degrade-clean** — if mokata isn't installed, or the folder isn't a mokata project, you get
  a short, friendly message (“mokata is not installed…”, “mokata is not initialized…”), never
  an error spew.

### Settings

| Setting | Default | Meaning |
|---|---|---|
| `mokata.enable` | `true` | Show the badge + panel (opt-out toggle). |
| `mokata.cliPath` | `mokata` | Path to the CLI if it isn't on your `PATH`. |
| `mokata.refreshIntervalSeconds` | `30` | Badge refresh cadence; `0` disables the timer. |

## Verifying it works (manual-verification leg)

A VS Code extension can't run inside mokata's Python test suite, so the **real editor run is a
manual-verification leg** (like the live-DB integration leg). What *is* auto-tested in CI: the
extension scaffold (`package.json` parses + declares its commands/contributions, `tsconfig`
present), that it's **read-only by construction** (only the read-only whitelist is ever spawned;
no durable-write subcommand is wired), and that the degrade-clean copy is present. To verify the
live experience: open a mokata-initialized project, confirm the badge appears, expand the panel
sections, and run **“mokata: Run a /mokata command in the terminal…”** — it should open a
terminal with the command staged but **not executed**.

For the **Copilot Chat** pieces (Stage 64b), the same applies: auto-tested in CI are the
`chatParticipants` contribution, that the participant is **read-only by construction** (its
intent map targets only the `READ_COMMANDS` whitelist; write verbs resolve to a *propose* intent,
never a spawn), and that the `mokata-mcp` MCP snippet is valid JSON naming `mokata-mcp`. The
**live Copilot run** is the manual-verification leg: in a Copilot-enabled VS Code, type
`@mokata /status` (and `/why`, `/progress`, `/memory`), confirm a write ask like “@mokata ship
it” *proposes* the command with a **Stage in a terminal** button (not run), then run the
**Register mokata-mcp…** command and use VS Code's Start/Trust prompt to enable the server.

## Roadmap: JetBrains & Neovim

Editor presence beyond VS Code is planned but **not yet built**:

- **JetBrains** (IntelliJ/PyCharm) — a read-only tool window mirroring the VS Code panel.
- **Neovim** — a read-only statusline/sidebar integration.

Both will follow the same rule as the VS Code client: a **thin, read-only** surface that defers
every durable write to the human-gated CLI. They are deliberately **not stubbed** until they
actually work.
