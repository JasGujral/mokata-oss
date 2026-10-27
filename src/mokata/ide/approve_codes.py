"""0.0.26 P38 (APPROVE-IN-IDE) — the one-time code that makes an IDE Approve/Reject a HUMAN act.

THE PROBLEM. The IDE's pairing token is written to `.mokata/temp_local/ide/server.json` and is
accepted as an `X-Mokata-Token` header (`ide/server.py`), so any process the agent drives (its Bash
tool) can read it and POST with it — and `Origin` is just a header curl can set. A route that
approved on the token alone would be an always-on approve door for a shell-capable agent, weaker
than `mokata approve`, which FAILS CLOSED off a TTY (SI.3, `cli_commands/approve.py`).

THE DECISION (coordinator, 2026-10-22; SI.3 stays intact):
  * D, always: the card shows the proposal in full and the exact `mokata approve <id>` command
    (Copy), and updates live when the approval lands from the terminal.
  * A, only when `mokata ide --foreground` holds an interactive terminal (stdin AND stdout are TTYs,
    not inside a Claude Code session — the bar `mokata approve` and the embedded terminal use): the
    card's Approve/Reject asks for a code the server prints to THAT terminal only.

THE CODE (this module):
  * minted per (proposal, decision) — a code minted to approve cannot reject, and the reverse;
  * 8 characters from a 31-letter alphabet (~40 bits), from `secrets`; shown as ABCD-EFGH;
  * printed to the server's own TTY and NOWHERE else: never written to a file (not server.json,
    not a log), never in any route response, never on the event stream;
  * single-use; expires after CODE_TTL seconds; at most MAX_TRIES wrong answers burn it;
  * minting is rate-limited (MIN_GAP between mints, MAX_MINTS per minute) so a guessing loop cannot
    churn codes; with 3 tries per ~40-bit code that is ~1e-11 per attempt.
The threat model this does NOT cover is the one SI.3 already names: an agent that can type into
the human's own terminal (or read its screen) — it could equally run `mokata approve --yes`.

Pure and in-memory; stdlib only. Copyright 2026 MoStack. Licensed under the Apache License, 2.0.
"""

from __future__ import annotations

import hmac
import os
import secrets
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"     # no 0/O, 1/I/L — read off a terminal, typed back
CODE_LEN = 8
CODE_TTL = 120.0
MAX_TRIES = 3
MIN_GAP = 3.0           # seconds between two mints (any proposal)
MAX_MINTS = 6           # per rolling minute
DECISIONS = ("approve", "reject")

NO_TTY = ("Approve here needs `mokata ide --foreground` running in your own terminal — the window "
          "prints a one-time code there (a window an agent opened has no terminal, so it can only "
          "show you the command). Or approve in any terminal:")
IN_SESSION = ("this window was started inside a Claude Code session (CLAUDECODE is set) — an agent's "
              "window never approves; use the command in your own terminal:")
SAMPLE = "SAMPLE data (`mokata ide --demo`) — there is nothing to approve"


def normalize(code: Any) -> str:
    """What the human typed, compared: upper-case, separators and spaces dropped."""
    return "".join(ch for ch in str(code or "").upper() if ch.isalnum())


def human_terminal(environ: Optional[Dict[str, str]] = None, stdin: Any = None,
                   stdout: Any = None) -> Tuple[bool, str]:
    """(available, why not) — the SAME bar as `mokata approve` (stdin is a TTY), plus stdout (the code
    is printed there) and not inside a Claude Code session. Fails closed: anything unreadable is no."""
    env = os.environ if environ is None else environ
    if env.get("CLAUDECODE"):
        return False, IN_SESSION
    for stream in (sys.stdin if stdin is None else stdin, sys.stdout if stdout is None else stdout):
        try:
            if stream is None or not stream.isatty():
                return False, NO_TTY
        except (AttributeError, OSError, ValueError):
            return False, NO_TTY
    return True, ""


class CodeBook:
    """The live codes, in memory only. One code per proposal at a time (a new mint replaces it)."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._codes: Dict[str, Dict[str, Any]] = {}
        self._mints: List[float] = []

    def mint(self, proposal_id: str, decision: str) -> Tuple[Optional[str], str, float]:
        """`(code, refusal, retry_in)` — a fresh code, or None with why (and when to try again)."""
        if decision not in DECISIONS:
            return None, f"decision must be one of {DECISIONS}", 0.0
        now = self._clock()
        self._mints = [t for t in self._mints if now - t < 60.0]
        if self._mints and now - self._mints[-1] < MIN_GAP:
            return None, "a code was just printed — wait a moment", MIN_GAP - (now - self._mints[-1])
        if len(self._mints) >= MAX_MINTS:
            return None, "too many codes this minute", 60.0 - (now - self._mints[0])
        code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))
        self._codes[proposal_id] = {"code": code, "decision": decision, "at": now, "tries": 0}
        self._mints.append(now)
        return code, "", 0.0

    def check(self, proposal_id: str, decision: str, typed: Any) -> Tuple[bool, str]:
        """Spend the code on success; count a wrong answer (MAX_TRIES burn it). Constant-time."""
        rec = self._codes.get(proposal_id)
        if rec is None:
            return False, "no code is waiting for this proposal — ask for one (it is printed in the terminal)"
        if self._clock() - rec["at"] > CODE_TTL:
            del self._codes[proposal_id]
            return False, "that code has expired — ask for a new one"
        ok = hmac.compare_digest(normalize(typed).encode(), rec["code"].encode())
        if ok and rec["decision"] == decision:
            del self._codes[proposal_id]
            return True, ""
        rec["tries"] += 1
        if rec["tries"] >= MAX_TRIES:
            del self._codes[proposal_id]
            return False, "wrong code — it is now void; ask for a new one"
        if ok:
            return False, f"that code was printed to {rec['decision']}, not to {decision}"
        return False, f"wrong code ({MAX_TRIES - rec['tries']} tr{'y' if MAX_TRIES - rec['tries'] == 1 else 'ies'} left)"

    def void(self, proposal_id: str) -> None:
        self._codes.pop(proposal_id, None)

    def live(self) -> int:
        return len(self._codes)


def say(text: str, stream: Any = None) -> bool:
    """Print to the server's terminal — and only if it IS a terminal (re-checked at the moment of
    printing, fail closed). Returns whether it was printed."""
    out = sys.stdout if stream is None else stream
    try:
        if not out.isatty():
            return False
        out.write(text)
        out.flush()
        return True
    except (AttributeError, OSError, ValueError):
        return False


__all__ = ["ALPHABET", "CODE_LEN", "CODE_TTL", "MAX_TRIES", "MIN_GAP", "MAX_MINTS", "DECISIONS",
           "NO_TTY", "IN_SESSION", "SAMPLE", "CodeBook", "human_terminal", "normalize", "say"]
