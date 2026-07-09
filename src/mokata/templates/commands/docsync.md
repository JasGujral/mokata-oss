---
name: docsync
description: mokata · Keep the docs TRUE to the code — audit a doc (or sweep + drift-detect the docs) against the code, then reconcile the drift behind a human-gated, previewed write.
when_to_use: Engage when the user asks whether the docs still match the code, when a change renamed a command / bumped a count / moved an install path and the docs may have gone stale, when a doc references a symbol the change touched (drift), or before a release when the docs must be verified against what actually ships. Do NOT engage to write NEW documentation from scratch (that is authoring, not reconciliation), or to edit code to match a doc — docsync brings the DOC in line with the code, never the reverse.
argument-hint: "[path]   # a doc to audit/reconcile; omit to sweep the whole doc tree"
allowed-tools: Bash, Read
---

# mokata · docsync (docs ↔ code reconciliation)

Documentation drifts from code silently — a renamed command, a bumped skill count, a dead install
path, a signature that changed under a doc's feet. docsync keeps the docs **true to the code**: it
cross-references every claim a doc makes against what actually ships, and — only on your approval —
reconciles the drift. It operationalizes mokata's pre-release ground-up doc check into a reusable,
auto-firing capability, so the docs are verified by construction, not by hand each release.

## When mokata engages this on its own

This skill is **model-invocable**: beyond `/mokata:docsync`, Claude Code may auto-activate it when
the docs may have gone stale (per `when_to_use` above), and mokata **auto-fires it on drift** — when
a change touches a symbol a doc references, docsync engages with its activation banner and audits it.
When it engages on its own it announces the banner and **holds its boundary**: the audit is
READ-ONLY (it writes nothing) and any doc edit is previewed as a diff and written ONLY through the
human gate — an auto-fire never becomes a silent write.

## Two targeting modes — point at a doc, or let mokata find them

1. **You point at a doc** — audit or reconcile exactly that file:
   ```bash
   mokata docsync docs/getting-started.md            # audit ONE doc (read-only)
   mokata docsync docs/getting-started.md --reconcile # propose fixes → preview → human-gated write
   ```
2. **mokata finds the docs** — sweep the whole public doc tree and drift-detect, so you need not
   know which doc went stale:
   ```bash
   mokata docsync                                     # sweep + audit every public doc
   ```
   On drift, the code graph shows which docs reference a changed symbol; docsync narrows to those.

## Two output modes — audit (read-only) then reconcile (human-gated)

- **AUDIT (default, read-only).** Cross-reference every claim against the code via the graph +
  memory: **skill counts**, **command names** (`mokata <cmd>` / `/mokata:<name>`), **config keys**,
  **install / getting-started path**, **version examples**, and **symbols / signatures**. Report each
  discrepancy with a **severity — Blocking / Minor / Info** — and **HIGHLIGHT the stale section** it
  sits in. It writes nothing. A Blocking discrepancy exits non-zero so a doc gate can act on it.
- **RECONCILE (`--reconcile`, human-gated write — P2).** Propose the edits that bring the doc back in
  line with the code, **PREVIEW the unified diff**, and write ONLY on explicit approval through the
  universal write gate (secret-scan → human approval → audit). Decline and nothing is written; there
  is no silent-write path. docsync fixes the **doc**, never the code.

## How to run

Resolve the bundled engine (read `~/.mokata/plugin-root` → `ROOT`, or a `mokata` CLI on PATH), then:

```bash
PY="$(command -v python3 || command -v python)"
ENGINE="PYTHONPATH=\"$ROOT/src\" \"$PY\" -m mokata"
eval "$ENGINE docsync --path ."                        # sweep + audit the doc tree (read-only)
eval "$ENGINE docsync <doc> --path ."                  # audit one doc
eval "$ENGINE docsync <doc> --reconcile --path ."      # reconcile one doc (preview → approve)
```

Read the audit, name the Blocking discrepancies and their stale sections first, and — where the fix
is unambiguous — OFFER the human-gated `--reconcile`. Where a discrepancy names a decision (not a
typo), pair it with the docs/ADR discipline so the fix is recorded, not just patched.

<!-- mokata:grounding -->
