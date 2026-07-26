# How-to: your first run (zero to wired in minutes)

mokata is the **memory + seatbelt for your AI coding agent**. This is the fastest path from
nothing to a wired, personalized setup — and a 60-second demo if you'd rather look before you
leap.

> Already have mokata installed? If not, start with [Getting started](../getting-started.md)
> (`pip install mokata` → `mokata setup claude`), then come back here.

## See it work first — `mokata tour` (read-only)

```bash
mokata tour            # or /tour inside Claude Code
```

A 60-second, **read-only** walk through three things on a tiny sample — it writes **nothing** to
your repo:

1. **Graph query** — ask the codebase a structural question (`mokata query callers <symbol>`)
   instead of grepping.
2. **Memory recall** — mokata remembers your project's decisions so the agent stops re-asking.
3. **Gate catch** — every durable write is scanned; a secret is a **hard block** approval can't
   override. Nothing is committed.

## The guided first run — the wizard

Run `mokata init` interactively (or `/setup` inside Claude Code) and you get a guided
**Q&A wizard** instead of flag-wrangling:

1. **Pick a profile** — `minimal` (engine only), `standard` (engine + graph + memory on lean
   local defaults), or `full` (every known provider).
2. **mokata detects your integrations** — graph backends, memory backends, Postgres / Obsidian /
   vector — and shows you exactly what's installed.
3. **You choose what to wire.** For something detected-but-not-installed, mokata **recommends**
   the install command (e.g. `pip install 'mokata[postgres]'`) — it **never installs a
   third-party tool for you**. Detect → recommend → run **with your approval**.
4. **It wires what you approved** — scaffolds the config, wires the chosen integrations, and
   (optionally) wires mokata into your harness (slash commands + MCP server + hooks + status
   badge) — every durable step **human-gated**. Decline and **nothing** is written.
5. **A 30-second recap** — "here's what I just did": what was detected, what got wired, the graph
   and memory now standing, the 5 starter guardrails (your constitution), and the **one next
   step**.

Everything is **local-first** and **reviewable** — the config is committed plain JSON, and every
write went through a gate you approved.

### Non-interactive (CI / scripts)

The flag path is unchanged and never prompts:

```bash
mokata init --profile standard --yes      # scaffold, no wizard, no prompts
mokata init --mode seatbelt --yes         # or name an on-ramp instead of a profile
mokata setup claude --yes                 # wire the harness non-interactively
```

`--mode {seatbelt,memory,full}` is the graduated on-ramp (mutually exclusive with `--profile`):
`seatbelt` = the gates + the AST code graph they need, `memory` = that plus typed persistent
memory, `full` = everything the spine can wire. It resolves to a profile and only the profile is
persisted. Under `--yes` the interactive extras offers never fire, so a CI init can never reach
`pip`. Full detail: [Getting started](../getting-started.md).

## When you mistype a command

mokata helps instead of just erroring:

```text
$ mokata statuss
mokata: 'statuss' is not a mokata command.
Did you mean 'status'?  (try `mokata status --help`)
Next: run `mokata init` (or `/setup` inside Claude Code) to set up this repo …
```

It suggests the closest real command (a `difflib` match over the command set) and the single most
useful next step for where you are.

## Change your setup later — `mokata reconfigure`

You're never locked into your first-run choices. Re-run the **same guided Q&A** any time on an
already-set-up repo to **change what's wired** — it re-detects your tools, shows a
current→proposed diff, and applies behind one gate:

```bash
mokata reconfigure                                   # interactive — or /reconfigure
mokata reconfigure --add postgres --yes              # wire a now-installed integration
mokata reconfigure --remove obsidian --yes           # cleanly unwire one (no residue)
mokata reconfigure --profile full --yes              # switch the profile
mokata reconfigure --set tools.sqlite.config.path=mem/custom.db --yes   # switch a backend
```

It's **idempotent** (no changes → a no-op, nothing written), **human-gated** (decline → nothing
changes), and **reversible** (`--remove` leaves no residue — gone from the capability chain *and*
the tools table). Like first-run, it **detects → recommends → runs with approval** — an absent
`--add` tool is recommended, never installed. Integrations grow with your project instead of
requiring a manual teardown.

## Your brainstorm design, saved as a plan file

When you approve a brainstorm approach — **before** any spec — mokata saves the design write-up
as an internal **plan file** under `.mokata/temp_local/plans/<slug>.md`, so the reasoning behind
the approach isn't lost. Browse and share it with `mokata plan`:

```bash
mokata plan list                       # the saved plans
mokata plan show [<slug>]              # print one (defaults to the sole plan)
mokata plan export [<slug>]           # copy it to a committable plans/<slug>.md
mokata plan export <slug> --to docs   # choose the destination dir
```

`export` is user-initiated and **never silently overwrites** an existing copy — pass `--force`
to replace one. (Full flags: the [CLI reference](../reference/cli.md).)

## Next

Once you're wired, start your first governed change with `/brainstorm`, or read
[the pipeline & gates](../concepts/pipeline.md). To change what's wired later, use
`mokata reconfigure` (above); to remove mokata entirely, `mokata unsetup` / `mokata reset`.
