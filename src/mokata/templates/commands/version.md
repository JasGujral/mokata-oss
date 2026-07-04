---
name: version
description: mokata · Show the installed version + how to update (offline; opt-in check).
---

# mokata · /version

Tell the user which mokata version is installed and how to update it. Run `mokata version` — it prints the version, profile, install method, and Python OFFLINE (no network). If they want to know whether a newer release exists, run `mokata version --check` (or `mokata upgrade --check`): this is OPT-IN and the ONLY outbound call — it is accounted and degrades clean offline (a blocked or failed check just says it couldn't check; it never errors). To update: if mokata is installed as a Claude Code plugin, run `/plugin marketplace update mostack` then reinstall it; if it's a pip install, `mokata upgrade` proposes a HUMAN-GATED `pip install -U mokata` (it never runs the upgrade or a network check on its own). Never check for updates or upgrade unless the user asks.

## Gate (check)
Read-only version display; the update check is opt-in and the upgrade is human-gated — nothing leaves the machine or changes without asking.

## Standalone
This command runs on its own — no upstream pipeline phase is required. It applies only its own gate above, and never silently skips a gate of a phase you did run.
