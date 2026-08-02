"""D5 — the MokataError taxonomy: ONE vocabulary for every failure mokata can produce.

The bug this closes (doc 74's "168 `except Exception`" row, re-counted at 210): a failure vanished
into `except Exception: pass`. The user's feature silently didn't work, doctor saw nothing, the
ledger recorded nothing. D1+D2's "loud but lying" schema degrade — a healthy connection told to run
`mokata sync`, forever — was ONE instance of a whole class.

Before this module, mokata had 42 exception classes and no way to say anything true about them
collectively. `except Exception` was the only handler that caught them all, which is precisely why
it was reached for 210 times — and `except Exception` also catches `AttributeError` from a typo,
so every one of those sites was silently a bug-swallower too.

The taxonomy is DESCRIPTIVE, not aspirational. It was derived by sweeping what the codebase
actually raises; no class here exists without a raiser. Two axes:

  MokataError            every error mokata itself raises. `except MokataError` now means "a
                         failure mokata DEFINED", as distinct from a Python bug (AttributeError,
                         TypeError) that a broad catch would have swallowed identically.

  DegradedCapability     the fail-open family — a capability could not be built, and the caller
                         degrades to a documented floor (Postgres → the SQLite floor; Neo4j → the
                         grep floor; a subagent → sequential). These are NOT bugs; they are the
                         designed degrade paths, and every one carries the `failure_class` that
                         tells a surface WHICH remediation is the true one.

`failure_class` is drawn from `degrade.py`'s vocabulary — deliberately NOT a second enum. A
degrade notice, the exception that caused it, and the doctor line that reports it must all say the
same word for the same failure, or the surfaces start inventing their own words for "degraded" and
we are back where CM.S2 started.

A hard error (a `ManifestError`, a `PhaseError`) carries `failure_class = ""`: it is not a degrade,
it propagates, and there is nothing to fall back TO. The empty string is honest — inventing a class
for it would be a word no failure ever earns.

WHAT THIS MODULE DOES NOT DO: change a single raising site. Every class keeps its name, its
message, its `__init__` and its secondary base (`LockTimeout` is still a `TimeoutError`;
`SkillNotFound` is still a `KeyError`), so every existing `except` in the codebase and in user code
keeps catching exactly what it caught before. This is a re-parenting, not a rewrite.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from .degrade import FAILURE_UNREACHABLE

__all__ = ["ControlSignal", "MokataError", "DegradedCapability", "failure_class_of"]


class ControlSignal(Exception):
    """A private CONTROL-FLOW signal — deliberately NOT a `MokataError`, and the sibling base that
    makes "every exception mokata defines is a MokataError" survive contact with a codebase that
    also needs non-local exits.

    The distinction is not taxonomic tidiness. A `MokataError` is a FAILURE mokata is reporting:
    something went wrong, and `except MokataError` exists so a caller can catch exactly that and
    nothing else. A control signal reports no failure at all — it is a `raise` used to leave a
    block, caught within a handful of lines by the frame that armed it. Filing one under the error
    base makes a legitimate `except MokataError` swallow it, which converts a designed non-local
    exit into a silently skipped one — and the whole point of D5 was to stop broad handlers
    swallowing things they were never meant to see.

    So the rule is: a signal that means "unwind to there" gets this base; anything that means "this
    failed" gets `MokataError`. Both are swept by the D5 taxonomy test, so a new class still cannot
    appear without someone saying which it is — the escape hatch is NAMED, not open.

    It subclasses `Exception` (not `BaseException`): a signal must still be caught by the
    last-resort `except Exception` handlers that guard mokata's never-raise boundaries, because a
    signal that escapes its frame is a bug and must degrade like one rather than kill the process.
    """


class MokataError(Exception):
    """The base of every error mokata raises.

    `failure_class` is a CLASS attribute, not a constructor argument, because it describes the
    KIND of failure, not this instance of it — and because making it an argument would force all
    42 subclasses to change their `__init__`, which is exactly the behavioural churn D5 forbids.
    A subclass overrides it; an instance may override it too where the same class genuinely covers
    two classes of failure (`teamdb` stamps `failure_class` per-instance to tell a denied CREATE
    apart from an unreachable host — D1).

    "" means: a hard error. It propagates, nothing falls back, and no remediation is implied.
    """

    failure_class: str = ""


class DegradedCapability(MokataError):
    """A capability could not be built, and the caller degrades to a documented floor.

    The shared contract of the `*Unavailable` family — the one every member's docstring already
    stated in its own words before this class existed to say it once ("the caller catches this and
    degrades to the SQLite floor (never a hard failure: 'degrade, never break')").

    Raising one of these is NOT a failure of correctness. SWALLOWING one without a notice is: that
    is the D5 bug, and it is why every catch of a `DegradedCapability` should be paired with a
    `degrade.note_degraded()` — the fallback keeps falling back, it just stops being a secret.

    UNREACHABLE is the default because that is what "can't build it" almost always means; the two
    members that mean something else (`DsnUnset` — nothing was ever configured; a schema mismatch —
    the connection is perfectly healthy) override it, and those two overrides are the entire point:
    D1's bug was telling a schema failure to `mokata sync`, which would never have fixed it.
    """

    failure_class: str = FAILURE_UNREACHABLE


def failure_class_of(exc: BaseException) -> str:
    """The failure class an exception carries — "" for anything that is not a classed mokata error.

    The ONE accessor every degrade site uses, so a site never has to know whether the class came
    from the type or was stamped on the instance (D1 does the latter). A plain `AttributeError`
    from a typo returns "" and is thereby distinguishable, in code, from a designed degrade — the
    distinction 210 `except Exception` handlers could not make."""
    return getattr(exc, "failure_class", "") or ""
