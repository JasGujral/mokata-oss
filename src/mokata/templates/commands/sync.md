---
name: sync
description: mokata · Flush the crash-safe team write journal to the shared DB and reconcile any compare-and-set conflicts through the human gate. Team mode only; each flush inherits its original approval.
argument-hint: "[--yes]"
allowed-tools: Bash, Read
---

# mokata · sync (flush + reconcile the team journal)

Reconcile team mode's write journal with the shared database. Every durable team write lands in a
**crash-safe local journal first** (so offline never blocks and nothing is lost); `mokata sync`
is the **manual flush + reconcile**:

- **Flush** the journaled writes to Postgres in one batch. Each write carries the **ledger id of
  its original human approval** — the flush *inherits* that approval (never a governance bypass),
  and a **per-publish secret-scan** still applies.
- **Reconcile** any **compare-and-set conflict** (a teammate changed the same memory row) through
  the **human gate**: keep your local version (overwrite remote) or keep remote — **never a silent
  last-writer-wins**. A conflict you don't decide stays deferred for a later interactive run.
- **Recover** any rows the old fallback stranded in the local floor, flushing them through the
  same gated path.

Team mode only — in **local** mode `sync` is a no-op (zero-config is untouched). When the
connection isn't healthy the flush is **skipped** (work-locally; nothing lost) and the state is
reported.

## 1. Resolve the engine

`${CLAUDE_PLUGIN_ROOT}` is NOT expanded inside command bodies, so discover the bundled engine:

- Read the cached plugin root: `cat ~/.mokata/plugin-root` → `ROOT`. If missing/empty, search the
  Claude Code plugins directory for a `mokata` plugin containing `src/mokata/__init__.py`. (If a
  `mokata` CLI is on PATH, use it directly.)
- Build the engine command with the **absolute interpreter**:

  ```bash
  PY="$(command -v python3 || command -v python)"
  ENGINE="PYTHONPATH=\"$ROOT/src\" \"$PY\" -m mokata"
  ```

## 2. Flush + reconcile

```bash
# interactive: prompts per conflict (keep local / keep remote)
# --yes: non-interactive flush; conflicts are DEFERRED (never silently overwritten)
eval "$ENGINE sync ${ARGUMENTS}"
```

Show the output **verbatim** — it leads with the connection **health** verdict (the SAME one the
badge / `mokata mode` / `mokata doctor` show), then the flush + reconcile summary. If a conflict is
**deferred**, tell the user to re-run `mokata sync` interactively to decide it — do **not**
hand-edit the shared database to force a winner.

## Notes

- **Offline never blocks:** writes are journaled locally and reconciled later; nothing diverges
  silently and nothing is lost.
- **Human-gated (P2):** conflict resolution is a real decision, audit-ledgered — never an
  automatic last-writer-wins.
- **Health everywhere:** a broken connection is highlighted in the statusline badge (⚠), `mokata
  mode`, `mokata doctor`, and the SessionStart briefing — all from the same probe.
