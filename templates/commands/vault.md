---
name: vault
description: mokata · Share design artifacts (brainstorm/spec) with the team — push, search, pull, list. Human-gated.
argument-hint: "push <name> <file> | search <query> | pull <name> [dest] | list"
allowed-tools: Bash, Read
---

# mokata · vault (share a brainstorm/spec for the team to review)

The vault is where the team shares durable **design artifacts** — a brainstorm-plan (the
*why*) or a spec (the *what*) — as named, searchable, pullable markdown. The flow: one person
brainstorms a plan and **pushes** it under a name; a teammate **searches**, **pulls**, and
reviews it. The vault lives in the committed/synced `.mokata/vault/` (no service required);
pushing is a durable write, so it is **human-gated** — never write without approval.

## 1. Resolve the engine

`${CLAUDE_PLUGIN_ROOT}` is NOT expanded inside command bodies, so discover the bundled
engine instead:

- Read the cached plugin root: `cat ~/.mokata/plugin-root` → `ROOT`.
- If that file is missing/empty, find the plugin directory another way: search the Claude
  Code plugins directory for a `mokata` plugin that contains `src/mokata/__init__.py`, and
  set `ROOT` to it. (If a `mokata` CLI happens to be on PATH, you may use it directly.)
- Build the engine command using the **absolute interpreter**:

  ```bash
  PY="$(command -v python3 || command -v python)"
  ENGINE="PYTHONPATH=\"$ROOT/src\" \"$PY\" -m mokata"
  ```

## 2. Pick the sub-action from `$ARGUMENTS`

- **`list`** — browse what's in the vault (read-only):

  ```bash
  eval "$ENGINE vault list --path ."
  ```

- **`search <query>`** — find an entry by name/title/body (read-only). Quote a multi-word
  query:

  ```bash
  eval "$ENGINE vault search \"payments redesign\" --path ."
  ```

- **`pull <name> [dest]`** — fetch an artifact to read/review (read-only on the vault):

  ```bash
  eval "$ENGINE vault pull <name> --dest <dest-or-default> --path ."
  ```

  Then **Read** the pulled file and summarize it for review.

- **`push <name> <file>`** — share a brainstorm/spec markdown. This is a **gated write** —
  follow the gate steps below.

## 3. Push (human-gated)

1. Confirm the source file exists and is the brainstorm/spec the user means to share.
2. **Preview first** — show the user what would happen without writing. Run the push in a
   non-interactive *check* by previewing the file and explaining: a new name creates v1; an
   identical re-push is a no-op; a **changed** re-push of an existing name is **refused**
   unless `--force` (which versions it, keeping prior-version metadata — never a silent
   clobber).
3. Ask the user to confirm explicitly. Do **not** proceed without a clear yes.
4. Only after approval, apply:

   ```bash
   eval "$ENGINE vault push <name> <file> --yes --path ."
   # add --force ONLY to version a changed entry, after the user confirms:
   # eval "$ENGINE vault push <name> <file> --yes --force --path ."
   # optionally: --kind brainstorm|spec  --author <name>
   ```

   A secret detected in the artifact is a hard block the approval cannot override — if that
   happens, tell the user to remove it and try again.

## 4. Report

Show the resulting entry (name, kind, version) and remind the user the vault file is
committed/synced, so a teammate can now `mokata vault search`/`pull` it for review.
