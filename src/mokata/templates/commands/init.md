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
  mokata() { PYTHONPATH="$ROOT/src" "$PY" -m mokata "$@"; }   # skip when a `mokata` CLI is on PATH
  ```

## 2. Resolve the profile

Take the profile from `$ARGUMENTS` (one of `minimal` / `standard` / `full`). If `$ARGUMENTS` is
empty, use the profile the user already named in the conversation; if they named none, ask
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
mokata init --profile '<profile>' --yes --preview --path .
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
mokata init --profile '<profile>' --yes --path .
```

⚠ If a manifest already existed and the user confirmed the overwrite, add `--force` — **to the
step 3 preview as well, then re-show it**. Any flag that reaches this command and not that one
means the user approved a plan for a different run.

### 5b. Plugin route, `full` profile: finish the mode

If `ROOT` came from `~/.mokata/plugin-root` (the plugin route) and the profile is `full`, finish
full mode with the plugin's own pinned environment (`mokata[full]` at the plugin's version — the
semantic model, code-review-graph and the TypeScript parser are already in it; this wires them and
warms the model). It writes only this repo's mokata settings, which the user approved in step 4:

```bash
sh "$ROOT/src/mokata/hooks/mokata-launch" setup claude --mode full --plugin --yes --path .
```

Show its table verbatim; a ✗ row names its cause and fix.

## 6. Verify + report

```bash
mokata status --path .
```

Show the resulting profile and how each capability resolved (present vs. degraded to its
floor). Remind the user to continue/restart Claude Code so the SessionStart briefing
reflects the new stack.
