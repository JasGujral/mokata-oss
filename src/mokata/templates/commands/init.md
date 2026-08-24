---
name: init
description: mokata · Initialize mokata or switch profile (minimal/standard/full) — human-gated, no pip needed.
argument-hint: "[profile]   # minimal | standard | full (default: ask)"
allowed-tools: Bash, Read
---

# mokata · init (set up the project / choose a profile)

You are setting up mokata in this project, or switching its profile, entirely from inside
Claude Code — no `pip install` required. mokata bundles its whole engine and has no runtime
dependencies, so stock Python runs it. Writing `.mokata/manifest.json` is a durable write,
so this is **human-gated**: preview → explicit approval → apply. Never write without approval.

## 1. Resolve the engine

`${CLAUDE_PLUGIN_ROOT}` is NOT expanded inside command bodies, so discover the bundled
engine instead:

- Read the cached plugin root: `cat ~/.mokata/plugin-root` → `ROOT`.
- If that file is missing/empty, find the plugin directory another way: search the Claude
  Code plugins directory for a `mokata` plugin that contains `src/mokata/__init__.py`, and
  set `ROOT` to it. (If a `mokata` CLI happens to be on PATH, you may use it directly.)
- Build the engine command using the **absolute interpreter** (do not rely on a bare
  `python3` being on PATH):

  ```bash
  PY="$(command -v python3 || command -v python)"
  ENGINE="PYTHONPATH=\"$ROOT/src\" \"$PY\" -m mokata"
  ```

## 2. Resolve the profile

Take the profile from `$ARGUMENTS` (one of `minimal` / `standard` / `full`). If empty, ask
the user which they want, briefly explaining the trade-off:

- **minimal** — engine only; no external capabilities; zero network egress.
- **standard** (default) — engine + codebase graph + memory on lean, local, dependency-free
  defaults (grep + SQLite).
- **full** — wires every known graph and memory provider (each degrades to its floor if
  absent).

## 3. Preview (no write)

Preview **the run you are about to make** — the same command as step 5 with `--preview` added.
Show the user the output **verbatim**:

```bash
eval "$ENGINE init --profile <profile> --yes --preview --path ."
```

⚠ The `--yes` is not a typo and must not be dropped. `--preview` describes the run it is
handed: without `--yes` it previews an init that wires nothing, and step 5 runs `--yes`, which
also writes the Claude Code commands, the Agent Skills, `.mcp.json` and `settings.json`. A
preview missing that flag shows the user a fraction of what they are approving.

It prints the detected tools, the capability chains this profile wires, every file it would
write — `.mokata/` **and** the harness wiring — and writes nothing. If `.mokata/manifest.json`
already exists, tell the user this will **overwrite** it (a profile switch, e.g.
`standard → full`) and that the constitution is preserved.

## 4. Human gate

Ask the user to confirm explicitly. Do **not** proceed without a clear yes. If they decline,
stop — and don't ask again.

## 5. Apply

Only after approval:

```bash
eval "$ENGINE init --profile <profile> --yes --path ."
```

⚠ If a manifest already existed and the user confirmed the overwrite, add `--force` — **to the
step 3 preview as well, then re-show it**. Any flag that reaches this command and not that one
means the user approved a plan for a different run.

## 6. Verify + report

```bash
eval "$ENGINE status --path ."
```

Show the resulting profile and how each capability resolved (present vs. degraded to its
floor). Remind the user to continue/restart Claude Code so the SessionStart briefing
reflects the new stack.
