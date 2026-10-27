---
name: update
description: mokata · Update mokata in one go (upgrade to the latest release) — shows the plan (installer, latest + release notes, the command that keeps your extras), asks once, runs it, refreshes skills and wiring.
argument-hint: "[--check]"
allowed-tools: Bash, Read
---

# mokata · update

`mokata update` shows how mokata was installed (uv tool / pipx / pip / uv pip / the plugin), the
installed and latest versions (one call to the release feed), the release notes in between, and
the ONE command that upgrades mokata **and keeps its extras** (e.g. `uv tool upgrade mokata`
keeps `[full]`). It then asks once — `Update now? [Y/n]` — runs that command, and finishes with
the new mokata: it refreshes the commands and skills vendored into `.claude/` and re-wires only
if the wiring changed. `--check` prints the plan and runs nothing.

## How to run

Resolve the bundled engine (read `~/.mokata/plugin-root` → `ROOT`, or a `mokata` CLI on PATH):

```bash
PY="$(command -v python3 || command -v python)"
mokata() { PYTHONPATH="$ROOT/src" "$PY" -m mokata "$@"; }   # skip when a `mokata` CLI is on PATH
mokata update --check --path .   # the plan: what is newer, the release notes, the command
```

Show the user the plan. **Only if the user says to update**, run it with their consent:

```bash
mokata update --yes --path .     # ONLY after the user approved the update
```

On Windows the update runs after mokata exits (Windows locks its files); the next `mokata`
command reports the result. On a plugin install the steps are Claude Code's
`/plugin marketplace update mostack` then `/plugin install mokata@mostack`.

Never check for updates unless the user asks.
