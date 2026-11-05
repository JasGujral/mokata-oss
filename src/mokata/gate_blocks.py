"""The ONE rule for "is this run blocked at a gate right now?" and "is this event this run's?"
(0.0.27 P14, extracted from `ide/session_views.browser`, where P12's needs-you queue used it inline).

Two readers ask these questions and must give the same answer: the IDE's needs-you queue
(`ide/session_views.py`) and the next-step hints (`mokata/hints.py`, which the CLI and the MCP
server also serve, without the `[ide]` extra). So the rule lives here — stdlib only, no `mokata.ide`
import — and both import it. Two copies of a rule are two rules the day one of them is edited.

  * `in_chain(run, chain, ev_run, ev_session)` — an event belongs to a session row when it carries
    the row's run, or one of the sessions of the row's resumed chain (O10). A session-scoped
    proposal mints `run_id == session_id`, which the chain half catches.
  * `pending_block(gates, last_activity)` — the newest BLOCKING gate decision, unless a LATER
    non-blocking decision OF THE SAME GATE (that gate let something through since — another gate's
    pass says nothing about this one, so a secret block is not hidden by the next unrelated
    allowed write) or later recorded activity (the
    session moved on: it wrote after the block) superseded it. `gates` is oldest first.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Optional, Sequence

#: Gate decisions that leave a session blocked until something changes. "declined" is a human
#: saying NO — an answer, not a wait — so it is not here.
BLOCKING = frozenset({"block", "blocked", "deny", "denied", "refused", "fail", "failed",
                      "would-block"})

#: Activity this long after a block counts as "moved on" (the event store's `ts` has one-second
#: resolution, so anything closer is the same moment).
SAME_MOMENT_SECS = 1.0


def epoch_of(ts: Any) -> float:
    """An event-store `ts` (ISO-8601 UTC, second resolution, `Z`) as epoch seconds; 0 when not."""
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def is_blocking(decision: Any) -> bool:
    return str(decision or "").lower() in BLOCKING


def in_chain(run: str, chain: Iterable[str], ev_run: str, ev_session: str) -> bool:
    """Whether an event (`ev_run`, `ev_session`) belongs to the row (`run`, its session `chain`)."""
    return (bool(run) and ev_run == run) or (bool(ev_session) and ev_session in set(chain))


def pending_block(gates: Sequence[Any], last_activity: float) -> Optional[Any]:
    """The block still standing, or None. `gates`: `gate_decision` events (with `.seq`, `.ts`,
    `.data`), OLDEST FIRST; `last_activity`: epoch seconds of the row's latest recorded activity."""
    blocked = [g for g in gates if is_blocking((g.data or {}).get("decision"))]
    if not blocked:
        return None
    b = blocked[-1]
    at = epoch_of(b.ts)
    if float(last_activity or 0.0) > at + SAME_MOMENT_SECS:
        return None
    gate = (b.data or {}).get("gate")
    if any(g.seq > b.seq and (g.data or {}).get("gate") == gate
           for g in gates if not is_blocking((g.data or {}).get("decision"))):
        return None                       # the SAME gate let something through since
    return b


__all__ = ["BLOCKING", "SAME_MOMENT_SECS", "epoch_of", "is_blocking", "in_chain",
           "pending_block"]
