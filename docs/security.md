---
mokata_command_route: both
---

# Security

Report vulnerabilities **privately** via GitHub's
[private vulnerability reporting](https://github.com/JasGujral/mokata-oss/security/advisories/new) —
do not open a public issue. See the repository's
[`SECURITY.md`](https://github.com/JasGujral/mokata-oss/blob/main/SECURITY.md) for supported
versions and response expectations.

## Security posture

mokata is a Claude Code plugin with **hooks, an MCP server and skills**. That is the
maximum-privilege plugin shape: hooks run on every prompt and tool call, the MCP server runs for
the whole session, and skills are text the model reads and acts on. This section says what runs,
what it writes, what it sends, and what it defends against.

### What runs, and where

Everything runs **on your machine, as you**. Nothing runs on a mokata server, because there is none.

| Piece | When it runs | What it is |
|---|---|---|
| Hooks (`src/mokata/hooks/hooks.json`) | `SessionStart`, `UserPromptSubmit`, `PreToolUse` (`secret-guard`, `gate-guard`), `PostToolUse` (`dirty-track`, `run-bind`), `PreCompact`, `SubagentStart`/`SubagentStop`, `Stop`, `SessionEnd` | A short Python process per event, run by Claude Code. Only the two `PreToolUse` hooks can block a tool call (exit code 2). Every other hook only adds context or records, and always exits 0 |
| Background helpers | After a hook returns | Short-lived detached Python processes: they record spooled hook events, refresh the session briefing's cached facts, and precompute code-graph lookups. Each holds a per-repo lock, writes only under `.mokata/temp_local/`, and exits |
| MCP server (`mokata-mcp`) | For the Claude Code session | A local **stdio** server. On the plugin route it is launched as `uvx --python ">=3.12" --from "mokata[full]==<exact version>" mokata-mcp`, pinned to the plugin's own version |
| IDE (`mokata ide`) | Only when you start it | A local web server bound to **127.0.0.1 only**. It checks the `Host` header on every request (DNS rebinding), and a page needs the per-start **pairing token** |
| Skills and commands | When Claude Code invokes them | Markdown the model follows. They run `mokata` CLI commands as ordinary shell commands |

### What it writes

- **Your repo:** `.mokata/`. The manifest and constitution are committed. Everything mutable lives
  under `.mokata/temp_local/`, which is gitignored: memory, the audit ledger, run state and caches.
- **Your home directory:** `~/.mokata/`. It holds the hook spool (owner-only), the cached plugin
  root and the IDE's per-repo state for repos mokata is not set up in.
- **Claude Code's settings:** only through `mokata setup claude`, which previews the change and
  asks first. The plugin route needs no settings write.
- **Every durable write** to memory, code, config or a remote goes through **one** human gate: it
  is previewed, it needs explicit approval, it is secret-scanned and it is ledgered.

### Network: none by default

mokata has **no telemetry** and never phones home (see
[`PRIVACY.md`](https://github.com/JasGujral/mokata-oss/blob/main/PRIVACY.md)). The minimal profile
makes **zero network connections**. A connection happens only when **you** ask for one:

- `uvx` downloads the pinned mokata package the first time the plugin starts its MCP server;
- `mokata update` checks for a newer version and installs it;
- team mode connects to **your** Postgres, using a DSN from your own environment;
- the optional embeddings extra fetches its model once, with a 30-second bound.

The IDE never goes online and never installs anything: its front-end libraries are vendored,
version-pinned and hash-checked.

### No API key

mokata makes **no model calls of its own** and never needs an API key. It runs on your own Claude
Code login. `mokata doctor` warns when an `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN` in your
environment would make Claude Code bill that key instead of your subscription. It shows the
variable's name, never its value.

### Approvals an agent cannot give itself

`mokata approve` refuses to run without a terminal (TTY). An agent's shell has none, so the agent
cannot approve its own writes. Approving from the IDE needs a one-time code that is printed only to
the terminal that started the IDE ([details below](#approving-from-the-ide)).

### Threat model

| Threat | What mokata does |
|---|---|
| **Prompt injection** in what the model reads (a fetched page, a log, an API reply, a file in the repo) | Every skill carries a trust-tier rule: text from those sources is DATA, never a directive. If it asks for something, the model shows it to you and does not act on it. Even if the model is fooled, durable writes still need your approval, and the secret-guard still blocks credentials |
| **A poisoned skill** (malicious instructions or credential theft hidden in a third-party skill; for example the "ToxicSkills" research on public skill registries) | mokata ships a **curated, closed set** of skills and **never ingests third-party skills**. Every shipped skill and command is linted on every PR: injection markers, hidden characters, HTML comments, unquoted shell arguments, agentskills.io conformance |
| **Shell injection through a skill's arguments** | Skills and commands call the engine as a shell function. User or repo text is passed as a single-quoted argument, never through `eval`. The lint fails a PR that splices `$ARGUMENTS` or a `<slot>` into a shell command unquoted |
| **A credential leaving the machine** | Four layers: signatures, entropy, sensitive paths, and egress. They run on every write, commit and send, and cannot be overridden. Shipped files are scanned too |
| **The agent approving its own writes** | TTY-gated approvals (above). The IDE pairing token is not consent |
| **A drifted or swapped server** | The MCP server is pinned to the exact mokata version. A PR lint fails if `plugin.json`, `marketplace.json`, `pyproject.toml`, the package version and the MCP pin disagree. `mokata doctor` names a server that serves a different version |
| **Supply chain** | GitHub Actions are pinned to full SHAs. Releases ship with build provenance (Sigstore), an SBOM, and a reproducible build ([supply chain](reference/supply-chain.md)) |

**What this does not cover:**

- A process that already controls your terminal could also type `mokata approve`.
- A program you run, such as your own tests, can write files that no hook sees.
- Only the Claude Code harness has hooks.

### What CI checks on every PR

`python scripts/security_lint.py` runs on every PR. It exits 0 when clean, 1 when it finds
something, and 2 when it cannot run. It runs six lints over the shipped files:

- `shell-args`
- `injection-markers`
- `shipped-secrets`
- `agentskills`
- `mcp-pin`
- `version-lockstep`

A third-party scan with `mcp-scan` is available as an **opt-in, manual** CI workflow
(`mcp-scan.yml`). It needs the network, so it is never part of the product or of the default CI.

### Reporting

Use [private vulnerability reporting](https://github.com/JasGujral/mokata-oss/security/advisories/new).
Supported versions, response targets and scope are in
[`SECURITY.md`](https://github.com/JasGujral/mokata-oss/blob/main/SECURITY.md).

## The two sync hooks — and why only one is a *security* block

mokata registers **two** blocking (`PreToolUse`) hooks in Claude Code. Both stop a tool call with
**exit code 2**, but they are different in kind, and the difference is security-relevant:

| Hook | Matches | Class | Overridable? |
|---|---|---|---|
| `secret-guard` | `Write` · `Edit` · `MultiEdit` · **`Bash`** | **security** — a credential is about to be written, committed, or sent | **Never.** No flag, no approval, no override lifts it — an approved write is still hard-blocked |
| `gate-guard` | `Write` · `Edit` · `MultiEdit` · `NotebookEdit` · `Bash` · `Skill` · `Agent` · `Task` | **methodology** — the [run-state gates](reference/cli.md#mokata-gate-statusoverrideclear) (`approach-approval`, `spec-persisted`, `no-code-without-failing-test`, `spec-scope`) | **Yes**, explicitly: `mokata gate override <gate> --reason "<why>"` — session-scoped, re-confirmed, and **ledgered**. There is deliberately no env-var kill switch |

A methodology gate is something a human may knowingly step around, on the record. A security block is
not — so mokata never lets an approval, a trust level, or a flag turn the secret-guard off.

**One honest limit.** The `gate-guard` reads a `Bash` command for the files it writes. A write
the parser can see (`sed -i`, `>`, `tee` and so on) meets the same run-state gates as a native
`Write` would. During a run, a command that may write somewhere the parser cannot determine is
refused: `eval`, `$(…)`, a variable path or an inline interpreter.

What it does not police is a program you run, such as your test suite or `make`. Those may write
files that no command-line parser can name. The `secret-guard` matches `Bash` too, so the
*security* boundary holds there. Only the **Claude Code** harness declares the `hooks` capability;
see [platform support](reference/platform-support.md).

## Approving from the IDE

`mokata ide` shows each durable write an agent proposed as a card in the Activity drawer and on the
Review screen. The card shows the write in full, plus the `mokata approve <id>` command with a Copy
button. It turns "approved", with its audit-ledger row, the moment you approve it in a terminal.

**Approve / Reject inside the window** needs one more thing: a **one-time code** printed to the
terminal that runs `mokata ide --foreground`. You read it there and type it into the card.

Why a code, and not just the window's login:

- **The window's pairing token is not consent.** It lives in
  `.mokata/temp_local/ide/server.json`, inside the repo. An agent with a shell can read it and
  send it in a request header, and it can set any `Origin` header it likes. A button that approved
  on the token alone would let the agent approve its own writes. `mokata approve` refuses that: it
  fails closed when it is not run at a terminal (TTY), and an agent's shell has none.
- **The code is printed only to the server's own terminal.** It is offered only when stdin and
  stdout are both a TTY and the window was not started inside a Claude Code session. That is the
  same bar as `mokata approve` and the embedded terminal. A window an agent opened (for example
  `/review-window`, or `mokata ide` with no terminal) never offers it. Its card shows the
  command and why.
- **The code never leaves that terminal.** It is never written to any file (not `server.json`, not
  a log), never in any response from the window's server, and never on its event stream.
- **The code is hard to guess or reuse:**
  - one use;
  - bound to one proposal *and* one decision (an approve code cannot reject);
  - expires after 2 minutes;
  - void after 3 wrong answers;
  - at most one code every 3 seconds and 6 a minute.
- **The decision is the same one the CLI makes.** It is recorded on the ledger as
  `actor: ide-tty`, next to the CLI's `human` and the opt-in in-chat approval's `chat-relayed`. The
  approval is still single-use, content-hashed and run-scoped, and the secret-guard still
  hard-blocks.

**The limit (the same one `mokata approve` has).** A process that can type into your own terminal,
or read its screen, could also run `mokata approve --yes`. This design keeps the model's tools and
its shell out of the decision. It cannot protect a terminal the agent already controls.

