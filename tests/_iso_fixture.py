"""Shared fixtures for the ISO-dialect pins: one instant, written two ways.

Split out of the pin files themselves so P1 (the lexer) and P2 (the seven consumers) can live in
SEPARATE modules. That separation is not cosmetic — `scripts/mutate.sh` selects tests by FILENAME,
so two files is what lets a mutation verdict say "P1 caught it" or "only P2 caught it" instead of
one undifferentiated red.

Every fixture is written in the EXPLICIT `+00:00` dialect and converted by `zulu()`, so a test can
never accidentally compare two different moments.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

NOW = "2026-07-29T12:00:00+00:00"
ONE_HALF_LIFE_AGO = "2026-06-29T12:00:00+00:00"        # NOW - 30 days == RECENCY_HALF_LIFE_DAYS
EXPIRED = "2026-07-28T12:00:00+00:00"                  # NOW - 1 day: a TTL that has elapsed
UNEXPIRED = "2026-07-30T12:00:00+00:00"                # NOW + 1 day: a TTL that has NOT elapsed


def explicit(iso: str) -> str:
    """The explicit-offset dialect: the fixture as written."""
    return iso


def zulu(iso: str) -> str:
    """The SAME instant in the Zulu dialect."""
    assert iso.endswith("+00:00"), f"fixture must be written in the explicit dialect: {iso!r}"
    return iso[: -len("+00:00")] + "Z"


DIALECTS = (("explicit", explicit), ("zulu", zulu))
