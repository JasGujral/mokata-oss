# How-to: your first run (zero to wired in minutes)

mokata is the **memory + seatbelt for your AI coding agent**. This is the fastest path from
nothing to a wired, personalized setup — and a 60-second demo if you'd rather look before you
leap.

## See it work first — `mokata tour` (read-only)

```bash
mokata tour            # or /mokata:tour inside Claude Code
```

A 60-second, **read-only** walk through three things on a tiny sample — it writes **nothing** to
your repo:

1. **Graph query** — ask the codebase a structural question (`mokata query callers <symbol>`)
   instead of grepping.
2. **Memory recall** — mokata remembers your project's decisions so the agent stops re-asking.
3. **Gate catch** — every durable write is scanned; a secret is a **hard block** approval can't
   override. Nothing is committed.

## The guided first run — the wizard

Run `mokata init` interactively (or `/mokata:setup` inside Claude Code) and you get a guided
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
mokata setup claude --yes                 # wire the harness non-interactively
```

## When you mistype a command

mokata helps instead of just erroring:

```text
$ mokata statuss
mokata: 'statuss' is not a mokata command.
Did you mean 'status'?  (try `mokata status --help`)
Next: run `mokata init` (or `/mokata:setup` inside Claude Code) to set up this repo …
```

It suggests the closest real command (a `difflib` match over the command set) and the single most
useful next step for where you are.

## Change your setup later — `mokata reconfigure`

You're never locked into your first-run choices. Re-run the **same guided Q&A** any time on an
already-set-up repo to **change what's wired** — it re-detects your tools, shows a
current→proposed diff, and applies behind one gate:

```bash
mokata reconfigure                                   # interactive — or /mokata:reconfigure
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

## Next

Once you're wired, start your first governed change with `/mokata:brainstorm`, or read
[the pipeline & gates](../concepts/pipeline.md). To change what's wired later, use
`mokata reconfigure` (above); to remove mokata entirely, `mokata unsetup` / `mokata reset`.
