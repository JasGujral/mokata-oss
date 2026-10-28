"""NO-API-KEY-GUARD, runtime half (0.0.26 L4): say so when the environment would move Claude Code
off the user's Claude login.

mokata never needs or reads an API key. But the `claude` it launches (the IDE's embedded terminal)
inherits the user's environment, and Claude Code's own precedence puts `ANTHROPIC_AUTH_TOKEN` and
`ANTHROPIC_API_KEY` ABOVE the subscription login (code.claude.com/docs/en/iam, "Authentication
precedence", checked 2026-10-21). For `ANTHROPIC_API_KEY`, an interactive Claude Code asks ONCE to
approve the key and then remembers the answer; a print-mode (headless) run uses it unasked.
`ANTHROPIC_AUTH_TOKEN` (an LLM gateway's bearer token) is used whenever it is set. A key exported
for some other tool can therefore move every later session onto metered billing with no further
prompt.

THE DECISION (evidence above): WARN, do not strip.
  * mokata spawns no print-mode claude (the static guard's R4), so every claude it launches is
    interactive, where Claude Code itself asks before an API key is first used and `/status`
    names the active credential. The switch is the user's own recorded choice (a gateway token is
    a deliberate configuration), not one mokata makes.
  * Stripping the key would override that choice and break the users who bill a key on purpose
    (Console-only users, LLM gateways via `ANTHROPIC_AUTH_TOKEN`) — inside a terminal that is
    otherwise a dumb pipe of the user's own environment.
  * So mokata makes it VISIBLE where it launches claude: a notice at the top of the embedded
    `claude` terminal, and a `mokata doctor` warning. (bench/g2 SCRUBS the same names because it
    runs claude in print mode, where nothing would ask — a different case, decided differently.)

PRESENCE ONLY: this module answers "is the variable set?" and names the variable. It never returns,
logs or renders the value (pinned by `tests/test_l4_billing_env.py` with a sentinel value). These
two names are the ONLY key names in `src/mokata` — the static guard's allow-list names this file
and nothing else.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
from typing import List, Mapping, Optional

#: Claude Code's own precedence order, highest first (both outrank the subscription login).
BILLING_ENV = ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")


def billing_overrides(environ: Optional[Mapping[str, str]] = None) -> List[str]:
    """The NAMES of the set, non-empty variables that would move Claude Code off the login."""
    env = os.environ if environ is None else environ
    return [name for name in BILLING_ENV if bool((env.get(name) or "").strip())]


def notice(names: List[str]) -> str:
    """One plain sentence naming the variables (never their values) and the way back."""
    which = " and ".join(names)
    return (f"{which} {'is' if len(names) == 1 else 'are'} set in this environment: Claude Code "
            "ranks it above your Claude login and bills it instead (an API key after a one-time "
            "approval prompt; print mode and a gateway token without asking). mokata never needs "
            f"a key. To stay on your login, unset {which} before starting Claude Code; `/status` "
            "shows which credential is active.")


def terminal_preface(program: str, environ: Optional[Mapping[str, str]] = None) -> bytes:
    """The line written at the top of an embedded `claude` terminal when a key is set; b"" otherwise
    (and always for the user's shell, which is theirs to run as they like)."""
    if program != "claude":
        return b""
    names = billing_overrides(environ)
    if not names:
        return b""
    return ("\x1b[33mmokata: " + notice(names) + "\x1b[0m\r\n").encode("utf-8")


__all__ = ["BILLING_ENV", "billing_overrides", "notice", "terminal_preface"]
