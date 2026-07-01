---
name: upgrade
description: mokata · Update mokata — human-gated pip upgrade, or the plugin-update steps (never auto-runs).
argument-hint: "[--check]"
allowed-tools: Bash, Read
---

# mokata · upgrade (update mokata, with your approval)

Update mokata to a newer release. This is **human-gated**: it never runs an install or a
network check on its own. `--check` just reports whether a newer release exists (the OPT-IN,
accounted outbound call — it degrades clean offline and never errors).

## How to run

Resolve the bundled engine (read `~/.mokata/plugin-root` → `ROOT`, or a `mokata` CLI on PATH):

```bash
PY="$(command -v python3 || command -v python)"
ENGINE="PYTHONPATH=\"$ROOT/src\" \"$PY\" -m mokata"
eval "$ENGINE upgrade --check --path ."   # just report if a newer release exists (opt-in)
```

To actually update:

- **Plugin install** — mokata can't upgrade the plugin from the CLI. Run `/plugin marketplace
  update mostack` in Claude Code, then reinstall. (`mokata upgrade` prints these steps.)
- **pip install** — `mokata upgrade` proposes a **human-gated** `pip install -U mokata`.
  PREVIEW the proposed command, ask the user to confirm explicitly, and only then run:

  ```bash
  eval "$ENGINE upgrade --yes --path ."   # ONLY after the user approves the pip upgrade
  ```

Never check for updates or upgrade unless the user asks.
