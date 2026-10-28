---
name: version
description: mokata · Show the installed version + how to update (offline; opt-in check).
---

# mokata · /version

Tell the user which mokata version is installed and how to update it. Run `mokata version` — it prints the version, profile, install method, and Python OFFLINE (no network). If they want to know whether a newer release exists, run `mokata version --check`: this is OPT-IN and the ONLY outbound call — it is accounted and degrades clean offline (a blocked or failed check just says it couldn't check; it never errors). To update, run `mokata update --check` and show the plan (installer, latest version, release notes, the ONE command that keeps the extras); only when the user says to update, run `mokata update --yes` — it runs that command and then refreshes the vendored commands/skills and the wiring. Never check for updates or update unless the user asks.

## Gate (check)
Read-only version display; the update check is opt-in and `mokata update` asks before it runs — nothing leaves the machine or changes without asking.

## Standalone
This command runs on its own — no upstream pipeline phase is required. It applies only its own gate above, and never silently skips a gate of a phase you did run.
