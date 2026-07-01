---
name: session
description: mokata · Portable / shareable tagged sessions — package this session and resume it on another machine or hand it to a teammate. Human-gated.
argument-hint: "push <tag> [--to local|vault|postgres] | pull <tag> [--from …] [--into <repo>] | list | name <tag> <new>"
allowed-tools: Bash, Read
---

# mokata · session (carry your work to another machine — or a teammate)

Your session state (the resumable run checkpoint(s), the approved approach, the emitted spec,
and any in-progress brainstorm) lives under `.mokata/temp_local/` — local and gitignored, so it
doesn't travel. `session` packages it into a **machine-path-free, versioned, content-hashed,
provenance-stamped** bundle and shares it under a tag, so another machine (or a teammate) can
**pull** it and `mokata resume` continues exactly where you left off.

**Transports (where the bundle travels) — `--to` on push, `--from` on pull:**

- **`local`** (default) — a file under `.mokata/session-bundles/` on this machine.
- **`vault`** — the committed/synced store `.mokata/vault/sessions/` — the bundle travels **with
  the repo**, so a teammate who pulls/clones the repo can `session pull --from vault`.
- **`postgres`** — a **shared DB table** reached by a DSN env var (`MOKATA_SESSION_PG_DSN`, or the
  shared `MOKATA_PG_DSN`) so a whole team pushes/pulls one store. **Opt-in & local-first:** with
  no `psycopg`/DSN it **degrades clean** (a clear message, no crash) and **never** silently falls
  back to a less-secure store.

Both **push** and **pull** are **human-gated** and **secret-scanned on EVERY transport** (a secret
in the session is a hard block approval can't override). On **pull** the bundle is untrusted: the
content-hash is verified (corruption is caught from any source), it's re-scanned + re-gated, and a
**cross-codebase mismatch** (the bundle came from a different repo) is surfaced — never silently
applied. The HARD-GATE survives the trip: a not-yet-approved brainstorm stays **not** approved
after pull.

## 1. Resolve the engine

`${CLAUDE_PLUGIN_ROOT}` is NOT expanded inside command bodies, so discover the bundled engine:

- Read the cached plugin root: `cat ~/.mokata/plugin-root` → `ROOT`.
- If that file is missing/empty, find the plugin directory another way: search the Claude Code
  plugins directory for a `mokata` plugin that contains `src/mokata/__init__.py`, and set `ROOT`
  to it. (If a `mokata` CLI happens to be on PATH, you may use it directly.)
- Build the engine command using the **absolute interpreter**:

  ```bash
  PY="$(command -v python3 || command -v python)"
  ENGINE="PYTHONPATH=\"$ROOT/src\" \"$PY\" -m mokata"
  ```

## 2. Pick the sub-action from `$ARGUMENTS`

- **`list`** — show the tagged bundles + their resume point, **spanning local + the committed
  vault (+ shared Postgres when a DSN is set)**, read-only:

  ```bash
  eval "$ENGINE session list --path ."
  ```

- **`push <tag> [--to local|vault|postgres]`** — package THIS session under a tag on the chosen
  transport. A **gated write** — follow §3.

- **`pull <tag> [--from …] [--into <repo>]`** — re-hydrate a bundle so `mokata resume` continues.
  A **gated write** on the target — follow §3.

- **`name <tag> <new>`** — give a tagged session a human-friendly name (rename). A **gated write**
  — follow §3. Idempotent; a name collision is **refused unless `--force`** (never a silent
  clobber); provenance is preserved and the content-hash is untouched. The name is what `push` /
  `pull` / `resume` and the status badge read, so you refer to work as `auth-refactor`, not a hash.

## 3. Push / pull / name (human-gated)

1. **Preview first.** Show the user what would happen without writing — for a push, the tag,
   transport, and resume point; for a pull, the target repo and whether the codebase fingerprints
   match; for a name, the old → new tags. A **changed** re-push is refused unless `--force`; a
   **fingerprint mismatch** on pull is refused unless `--force`; a **name collision** is refused
   unless `--force` (each an explicit, never-silent override).
2. Ask the user to confirm explicitly. Do **not** proceed without a clear yes.
3. Only after approval, apply (add `--to` / `--from` to choose the transport — default `local`):

   ```bash
   # push the current session (vault travels with the repo; postgres is the shared team store):
   eval "$ENGINE session push <tag> --to vault --yes --path ."
   # add --force ONLY to overwrite a changed bundle; --author <name> for provenance.

   # pull + re-hydrate (default target = this repo; --into to target another):
   eval "$ENGINE session pull <tag> --from vault --yes --path ."
   # add --force ONLY to apply across a cross-codebase mismatch, after the user confirms.

   # rename a tagged session:
   eval "$ENGINE session name <tag> <new> --yes --path ."
   # add --force ONLY to overwrite a colliding name, after the user confirms.
   ```

   A secret detected in the session is a hard block the approval cannot override **on every
   transport** — if that happens, tell the user to remove it and try again. The Postgres transport
   needs a DSN in `$MOKATA_SESSION_PG_DSN` (or `$MOKATA_PG_DSN`); without it the command degrades
   clean — it does **not** silently fall back to local.

## 4. Report + continue

After a pull, tell the user that `mokata resume` (or `/mokata:resume`) now continues from the
bundle's resume point. After a push, remind them where the bundle lives for the other side to pull:
`local` → `.mokata/session-bundles/<tag>.json`; `vault` → `.mokata/vault/sessions/<tag>.json`
(commit/sync it); `postgres` → the shared DB (the teammate runs `session pull <tag> --from
postgres`).
