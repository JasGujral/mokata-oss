"""TM.S4 — the single canonical "Team mode: setup & operations" docs pointer.

Every team-mode error — the preflight (`run_mode.py`), the DB probe (`teamdb.py`), and the
`team`/`mode` surfaces (`team.py`, `cli_commands/mode.py`) — links the SAME page, so a stuck
user always has one place to go. Keeping the URL (and its SECURITY anchor) in one leaf module
means the canonical link never drifts across surfaces.

The page itself is PUBLIC product docs (`docs/how-to/team-setup.md`) — never an internal file.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

# The published docs site (mkdocs `site_url`) + the ops page's slug. `team-setup.md` renders at
# `/how-to/team-setup/`; its `## Security` heading anchors at `#security`.
DOCS_SITE = "https://mokata.ai"
TEAM_DOCS_URL = f"{DOCS_SITE}/how-to/team-setup/"
TEAM_DOCS_SECURITY_URL = f"{TEAM_DOCS_URL}#security"


def team_docs_hint() -> str:
    """The one canonical "here's the page" fragment appended to every team-mode error, so a
    stuck user always has the setup & operations guide."""
    return f"team-mode setup & operations: {TEAM_DOCS_URL}"


def with_docs(message: str) -> str:
    """Append the canonical docs pointer to a team-mode error `message` (idempotent — never
    doubles the link if it's already present)."""
    if TEAM_DOCS_URL in message:
        return message
    sep = " " if message and not message.endswith((" ", "\n")) else ""
    return f"{message}{sep}({team_docs_hint()})"


__all__ = ["DOCS_SITE", "TEAM_DOCS_URL", "TEAM_DOCS_SECURITY_URL", "team_docs_hint", "with_docs"]
