# Which setup command do I need?

`mokata init`, `mokata setup claude`, and installing the plugin are **three different things**,
and the most common mokata support question comes from treating them as one. They answer three
separate questions, and you may need one, two, or all three.

## The decision table

| You want… | Run | What it touches | What it does NOT do |
|---|---|---|---|
| **A governed config in this repo** — profile, capability chains, the constitution, the audit ledger | `mokata init` (interactive) | `.mokata/` in the current directory | Does **not** touch Claude Code. No slash commands, no Agent Skills, no MCP server, no hooks. Your agent is unchanged. ⚠ **`mokata init --yes` is the exception** — see below. |
| **mokata inside Claude Code** — slash commands, Agent Skills, the MCP server, the gate hooks, the status line | `mokata setup claude` | `.claude/` (commands, skills, `settings.json`) + the MCP registration. Runs `init` for you if `.mokata/` is missing | Does **not** install the mokata package (pipx / uv tool / brew / pip already did that), and does **not** replace the upgrade when a new version ships. |
| **The Claude Code plugin** (mokata's own marketplace) | `/plugin marketplace add JasGujral/mokata-oss` then `/plugin install mokata@mostack` | Claude Code's own plugin cache — a self-contained copy with its own hooks and MCP registration (the server starts through `uvx`, so `uv` must be on PATH) | Does **not** put the `mokata` CLI on your `PATH`, and is **not** rewired by `mokata setup claude`. Use it *or* `setup`, not both — both wire the same hooks. *(A community-marketplace listing is pending submission.)* |

### The one-line version

- **`init` = your repo.** It writes `.mokata/`.
- **`setup` = your agent.** It writes `.claude/`.
- **plugin = an alternative to `setup`,** not an alternative to `init`.

### Do I need both `init` and `setup`?

Run **`mokata setup claude`** and you are done — it initializes the repo as part of wiring the
harness. Run `mokata init` on its own only when you want mokata's engine from the terminal, from
CI, or from a non-Claude agent, with your Claude Code setup left alone.

`mokata init --mode seatbelt|memory|full` picks *how much of the engine* to configure. It is a
profile choice, not a wiring choice — no *mode* wires Claude Code.

### 🔴 `--yes` wires the harness (changed in 0.0.19)

The sentence above used to end *"after any `init`, the harness is wired by `mokata setup
claude` or not at all."* That is no longer true, and the difference matters most to the
people least likely to be watching a terminal.

**`mokata init --yes` now runs `setup claude` at project scope as part of the init.** `--yes`
is consent to the whole plan, and wiring the run-state gate is part of that plan, so the
consent is already given rather than assumed. Concretely, a non-interactive init writes:

- `.claude/commands/` (the slash commands) and `.claude/skills/` (the Agent Skills);
- `.claude/settings.json` — the SessionStart briefing, `secret-guard`, `gate-guard`, the MCP
  permission grant, and the status line (existing JSON is merged, never overwritten);
- `.mcp.json` — the `mokata` MCP server registration;

and it **spawns a short-lived `mokata-mcp` subprocess** to check the server answers and serves
the same version as the CLI. **CI and scripted callers that timed or sandboxed `mokata init`
should expect those writes and that subprocess.** Reverse it with
`mokata unsetup claude --scope project`.

This applies to `--mode` too: `mokata init --mode memory --yes` wires; `mokata init --mode
memory` on a terminal does not.

An init that wires *nothing* is not silent either — it prints whether the run-state gate is
enforcing here, in three states: enforcing, not wired, or *could not be verified*.

`mokata init --preview` previews the whole write. On its own it shows the `.mokata/` plan and
states that harness wiring is not part of that run, naming `--yes`, the wizard's offer and
`mokata setup claude`. With `--yes` it also renders the harness plan — through the same
renderer the wiring uses, so the preview cannot drift from what lands.

## Upgrading mokata

Installing the new package is **not** the whole upgrade. Upgrading the package replaces the
code; it does not touch `.claude/settings.json`, which still carries the wiring the *previous*
version wrote. A hook added — or a tool matcher widened — since your last `mokata setup claude`
is simply not there. Nothing errors. The gate just never fires.

### Upgrade the package, then the same setup

```bash
uv tool upgrade mokata             # or: pipx upgrade mokata — both keep the [full] extra
mokata setup claude --mode full    # refresh the wiring (previews, asks first); keeps config + memory
# restart Claude Code, then:
mokata doctor
```

⚠ Not `uv tool install --upgrade mokata`: naming mokata WITHOUT `[full]` rebuilds the tool env
without the extra. The setup is the same one you set up with: `mokata setup claude` runs `mokata init` only on a
repo with no `.mokata/`, so on an existing repo it refreshes the wiring and nothing else — new
commands appear and stale mokata commands are removed. Never use `mokata init --force` to
update; it resets the repo's config to defaults.

### `mokata update` — one action, any install

```text
$ mokata update
mokata update

  installed  0.0.25  (uv-tool; keeps mokata[full])
  latest     0.0.26

What changed 0.0.25 → 0.0.26:
  0.0.26
    ...the release notes...

Will run:
  uv tool upgrade mokata

Then, automatically (`mokata update --finish` with the new mokata):
  mokata setup claude

Update now? [Y/n]
```

- The **command** is the installer's own and keeps your extras: `uv tool upgrade mokata`,
  `pipx upgrade mokata` (`--include-injected` when you injected packages), or
  `python -m pip install --upgrade "mokata[full]"` with the extras your environment has. A plain
  `pip install -U mokata` would never install a package a newer `[full]` adds.
- After it, the **new** mokata refreshes the vendored commands/skills and re-wires only if the
  wiring changed — you end up current with one yes.
- **Windows:** mokata's files are locked while it runs, so the update starts the moment the
  command exits; your next `mokata` command says how it went. Close Claude Code sessions using
  mokata first (their MCP server holds mokata's files).
- `mokata update --check` prints all of this and runs nothing. Offline, it says so and prints the
  command.
- A source checkout gets `git pull`; the plugin gets Claude Code's `/plugin` steps.

### Commands and skills only: `mokata setup claude --skills-only`

`mokata setup claude` copies mokata's slash commands and Agent Skills into `.claude/commands`
and `.claude/skills` — committed with the repo, they give a teammate the methodology on clone
with nothing installed. When those files differ from the installed mokata (you upgraded and
did not re-run setup, or someone edited them), `mokata update` says so and offers
`mokata setup claude --skills-only`: it rewrites only the commands and skills (and removes the
ones mokata no longer ships). No init, no MCP, no hooks, no `settings.json`, no dependency.

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
