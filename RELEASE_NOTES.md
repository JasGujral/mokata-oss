
mokata **0.0.10 — "Inside Claude Code."** Upgrade with `pip install -U mokata`. Additive; no
breaking changes; no new dependencies.

**Richer in-terminal UX.**

- **Command palette — `/mokata:menu`:** `mokata menu` lists every mokata command and skill on one
  screen with gate markers, derived from the shipped command/skill files (single source — no drift).
- **Docs at your fingertips — `/mokata:docs [topic]`:** lists topics with their published-docs URLs
  and resolves a topic to its page. Docs are read online — not bundled in the wheel, and the command
  never fetches at runtime (local-first).
- **Gated settings wizard — `mokata config wizard`:** walks you through mokata's settings
  interactively, routing every change through the same human-gated write path (secret-scan + schema
  validation + write gate + audit ledger), and failing closed when run non-interactively. It's a
  front-end, never a second write path.
- **Consistent output + `mokata doctor --matrix`:** verdicts, progress, and doctor tables now share
  one look — colour on a TTY, clean ASCII when piped or under `NO_COLOR`. `mokata doctor` gains an
  opt-in capability **coverage matrix**: pass / degraded / fail for every capability, single-sourced
  from the resolver.

**Fixes.**

- **Hooks never hang** *(fixes the 0.0.9 known issue):* `mokata-hook statusline` / `session-start` no
  longer block when stdin is an open pipe with no writer — a bounded read falls back to defaults (the
  "hooks never block a session" contract).
- **Mis-wired hooks are visible:** `mokata-hook` with a missing or unknown subcommand now exits
  non-zero (exit 1) instead of looking successful — and never uses the reserved security-block code.
- **Clean uninstall:** `mokata unsetup claude` removes config files it created once they become empty
  instead of leaving `{}` husks; files that still hold your own content are preserved.

**Under the hood.** The tokenizer-free chars÷4 briefing estimate now logs estimate-vs-actual to the
ledger so the ~2k budget's safety margin is measured, not merely asserted. Local-first, no telemetry,
Apache-2.0.
