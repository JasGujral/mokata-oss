# Which setup command do I need?

`mokata init`, `mokata setup claude`, and installing the plugin are **three different things**,
and the most common mokata support question comes from treating them as one. They answer three
separate questions, and you may need one, two, or all three.

## The decision table

| You want… | Run | What it touches | What it does NOT do |
|---|---|---|---|
| **A governed config in this repo** — profile, capability chains, the constitution, the audit ledger | `mokata init` | `.mokata/` in the current directory | Does **not** touch Claude Code. No slash commands, no Agent Skills, no MCP server, no hooks. Your agent is unchanged. |
| **mokata inside Claude Code** — slash commands, Agent Skills, the MCP server, the gate hooks, the status line | `mokata setup claude` | `.claude/` (commands, skills, `settings.json`) + the MCP registration. Runs `init` for you if `.mokata/` is missing | Does **not** install the mokata package (`pip` already did that), and does **not** replace `pip install -U` when a new version ships. |
| **One-click install from Claude Code's plugin directory** | `/plugin install mokata@mostack` | Claude Code's own plugin cache — a self-contained copy with its own hooks and MCP registration | Does **not** put the `mokata` CLI on your `PATH`, and is **not** rewired by `mokata setup claude`. *(Planned — mokata is not yet registered on a marketplace. Use the pip path today.)* |

### The one-line version

- **`init` = your repo.** It writes `.mokata/`.
- **`setup` = your agent.** It writes `.claude/`.
- **plugin = an alternative to `setup`,** not an alternative to `init`.

### Do I need both `init` and `setup`?

Run **`mokata setup claude`** and you are done — it initializes the repo as part of wiring the
harness. Run `mokata init` on its own only when you want mokata's engine from the terminal, from
CI, or from a non-Claude agent, with your Claude Code setup left alone.

`mokata init --mode seatbelt|memory|full` picks *how much of the engine* to configure. It is a
profile choice, not a wiring choice — **no mode wires Claude Code.** After any `init`, the
harness is wired by `mokata setup claude` or not at all.

## Upgrading mokata

Installing the new package is **not** the whole upgrade. `pip install -U mokata` replaces the
code; it does not touch `.claude/settings.json`, which still carries the wiring the *previous*
version wrote. A hook added — or a tool matcher widened — since your last `mokata setup claude`
is simply not there. Nothing errors. The gate just never fires.

### The one command

```bash
mokata upgrade
```

`mokata upgrade` finishes the job:

1. it proposes `pip install -U mokata` and **asks** before running it;
2. it then re-runs `mokata setup claude`, which **previews the change and asks again** before
   writing `settings.json`;
3. it runs `mokata doctor --wiring` and reports.

Both writes are human-gated: decline either and nothing is written. `mokata upgrade --yes`
approves both non-interactively (the plan is still printed), and `--no-refresh` skips the
re-wiring if you want to do it yourself.

Restart Claude Code afterwards so it reloads `settings.json`.

### Upgrading by hand

If you run `pip` yourself, run the other two steps yourself too:

```bash
pip install -U mokata
mokata setup claude          # refresh the harness wiring (previews, asks first)
mokata doctor --wiring       # confirm the gates resolve and the wiring is current
```

From a source checkout, the first step is `git pull && pip install -e .`; the last two are the
same. On the plugin route, update through Claude Code (`/plugin marketplace update mostack`,
then `/plugin install mokata@mostack`) and verify with `mokata doctor --wiring` — `mokata setup
claude` is not the plugin's remedy.

### How you find out the wiring went stale

You do not have to remember to check. The same verdict reaches you three ways:

- the **SessionStart briefing** carries one line when — and only when — your wiring is out of
  date;
- the **`status` MCP tool** reports it, which is what covers the case where the hooks are dead
  and therefore cannot tell you anything themselves;
- **`mokata doctor --wiring`** answers on demand and exits non-zero when the wiring is not both
  launchable and current.

If the wiring is current, none of them say anything.

## Related

- [`mokata-hook: command not found`](fix-mokata-hook-command-not-found.md) — the gates are wired
  but not launchable.
- [Use mokata without the plugin](use-without-plugin.md) — everything `mokata setup claude`
  wires, in detail.
- [Install mokata](install-mokata.md) — pip / pipx / uv / brew.
- [How mokata uses an LLM: harness vs CLI](../concepts/execution-model.md) — why the CLI and the
  in-Claude experience are two different surfaces.
