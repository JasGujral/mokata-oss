"""P2 — dialect EQUIVALENCE at all seven consumer sites of the ISO lexer.

The property that actually matters: the dialect a timestamp is WRITTEN in must never change a
decision mokata makes. Seven rows for the seven call sites the lexer feeds, table-driven so that
adding a consumer without adding a row is the visible drift this file exists to catch.

WHY EVERY ROW CARRIES A NON-DEGENERATE `expected`. `assertEqual(zulu, explicit)` on its own is
satisfied by BOTH dialects returning 0.0 / None / () — i.e. by the very degradation being pinned
against. On 3.10 before the fix, several of these sites returned the same wrong answer in both
dialects and a bare equivalence check would have been GREEN. So each row also asserts the site did
the work: a recency term of exactly 0.5 at one half-life, a TTL that actually FIRES, a timestamp
that actually advanced. A pin that cannot distinguish "looked and found nothing" from "never
looked" is not evidence.

P1 (the lexer itself) lives in `test_iso_dialect_split.py`.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

import _support  # noqa: F401 - puts src/ on the path

from _iso_fixture import (
    DIALECTS,
    EXPIRED,
    NOW,
    ONE_HALF_LIFE_AGO,
    UNEXPIRED,
    explicit,
    zulu,
)

from mokata import progress_events
from mokata.memory import healing, lifecycle
from mokata.memory.item import MemoryItem, add_seconds

Dialect = Callable[[str], str]


# ===================================================================== the sites, exercised
def _recency(recall_stamp: Optional[str], created_at: str, now: str) -> float:
    return lifecycle.recency_score(
        lifecycle.UsageSignal(hits=3, last_recalled_at=recall_stamp),
        created_at=created_at, now=now)


def _stale_proposals(expires_at: str, now: str) -> tuple:
    item = MemoryItem.create(subject="ttl probe", value="v",
                             created_at=ONE_HALF_LIFE_AGO, expires_at=expires_at)
    return tuple(sorted((p.kind, p.subject) for p in healing.detect_issues([item], now=now)))


def _event_age(ts: str) -> Optional[float]:
    return progress_events._event_age_hours({"ts": ts})


# The progress-events site reads `datetime.now()` INSIDE the call, so its two dialect runs are
# milliseconds apart in real time. Its row is built off the wall clock and compared with a
# tolerance; every other row is deterministic and compared exactly.
_AGED_HOURS = 48.0


def _aged_stamp() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=_AGED_HOURS)).isoformat()


@dataclass(frozen=True)
class Site:
    """One consumer of the lexer, runnable in either dialect."""

    label: str
    where: str
    run: Callable[[Dialect], Any]
    expected: Any
    note: str
    delta: Optional[float] = None


SITES = (
    Site(
        label="recency_score / last_recalled_at",
        where="lifecycle.py:139 (primary reference)",
        run=lambda d: _recency(d(ONE_HALF_LIFE_AGO), created_at="", now=NOW),
        expected=0.5,
        note="exactly one half-life old, so the honest answer is 0.5; the absorbed-fault "
             "answer is 0.0 and outranks nothing",
    ),
    Site(
        label="recency_score / created_at fallback",
        where="lifecycle.py:139 (fallback reference)",
        run=lambda d: _recency(None, created_at=d(ONE_HALF_LIFE_AGO), now=NOW),
        expected=0.5,
        note="the fallback arm of the same line — reached only when an item has hits but no "
             "recall stamp, which is exactly the half-landed-telemetry case",
    ),
    Site(
        label="recency_score / now",
        where="lifecycle.py:140 (the clock)",
        run=lambda d: _recency(ONE_HALF_LIFE_AGO, created_at="", now=d(NOW)),
        expected=0.5,
        note="an unreadable `now` falls back to the REAL clock, so this site silently re-dates "
             "the whole corpus rather than zeroing one item",
    ),
    Site(
        label="healing.detect_issues / now",
        where="healing.py:309",
        run=lambda d: _stale_proposals(expires_at=EXPIRED, now=d(NOW)),
        expected=((healing.STALE, "ttl probe"),),
        note="an unreadable `now` yields ref=None, and then NO item in the store can ever be "
             "proposed stale — the failure is corpus-wide, not per-item",
    ),
    Site(
        label="healing.detect_issues / expires_at",
        where="healing.py:313",
        run=lambda d: _stale_proposals(expires_at=d(EXPIRED), now=NOW),
        expected=((healing.STALE, "ttl probe"),),
        note="the TTL must actually FIRE; the degraded answer is an empty proposal list, i.e. an "
             "expired fact reads as live",
    ),
    Site(
        label="item.add_seconds",
        where="item.py:294 (raising contract)",
        run=lambda d: add_seconds(d(NOW), 3600),
        expected="2026-07-29T13:00:00+00:00",
        note="the one site that RAISES rather than absorbing, so its failure is loud — but it "
             "must still LEX the Zulu dialect rather than rejecting it",
    ),
    Site(
        label="progress_events verdict age",
        where="progress_events.py:485 (ship gate)",
        run=lambda d: _event_age(d(_aged_stamp())),
        expected=_AGED_HOURS,
        delta=0.05,
        note="None reads as STALE evidence at the ship gate, blocking a legitimate ship",
    ),
)


class P2DialectEquivalenceAtEveryConsumer(unittest.TestCase):
    def test_a_all_seven_call_sites_are_covered(self):
        # The count is the claim; this guards the table against silently shrinking.
        self.assertEqual(len(SITES), 7)
        self.assertEqual(len({s.where for s in SITES}), 7, "two rows name the same call site")

    def test_b_each_site_answers_identically_in_both_dialects(self):
        for site in SITES:
            with self.subTest(site=site.label):
                z, e = site.run(zulu), site.run(explicit)
                if site.delta is not None:
                    self.assertAlmostEqual(z, e, delta=site.delta,
                                           msg=f"{site.where}: dialect changed the answer")
                else:
                    self.assertEqual(z, e, f"{site.where}: dialect changed the answer")

    def test_c_each_site_actually_did_the_work_in_both_dialects(self):
        # The non-degeneracy half — without it, test_b is satisfied by both dialects failing
        # identically, which is precisely the bug's signature on 3.10.
        for site in SITES:
            for name, dialect in DIALECTS:
                with self.subTest(site=site.label, dialect=name):
                    got = site.run(dialect)
                    if site.delta is not None:
                        self.assertAlmostEqual(got, site.expected, delta=site.delta,
                                               msg=f"{site.where}: {site.note}")
                    else:
                        self.assertEqual(got, site.expected, f"{site.where}: {site.note}")


class P2TheHealingRowsHaveANegativeControl(unittest.TestCase):
    """A LIVE TTL must NOT propose stale — otherwise the two healing rows above are also
    satisfied by a detector that proposes STALE for everything it is handed."""

    def test_a_an_unexpired_zulu_ttl_produces_no_stale_proposal(self):
        self.assertEqual(_stale_proposals(expires_at=zulu(UNEXPIRED), now=NOW), ())
        self.assertEqual(_stale_proposals(expires_at=UNEXPIRED, now=zulu(NOW)), ())

    def test_b_expired_and_live_differ_within_the_zulu_dialect(self):
        expired = _stale_proposals(expires_at=zulu(EXPIRED), now=zulu(NOW))
        live = _stale_proposals(expires_at=zulu(UNEXPIRED), now=zulu(NOW))
        self.assertNotEqual(expired, live)
        self.assertEqual(expired, ((healing.STALE, "ttl probe"),))


class P2TheRecencyRowsHaveANegativeControl(unittest.TestCase):
    """0.5 must be EARNED by the age, not returned for any input — otherwise the three recency
    rows would pass against a function that ignores its timestamps."""

    def test_a_a_different_age_scores_differently_in_the_zulu_dialect(self):
        two_half_lives = "2026-05-30T12:00:00+00:00"        # NOW - 60 days
        self.assertAlmostEqual(
            _recency(zulu(two_half_lives), created_at="", now=zulu(NOW)), 0.25, places=6)

    def test_b_zero_hits_still_scores_zero_in_either_dialect(self):
        # The DB.S5 back-compat floor, re-pinned here: the fix must not switch the recency term on
        # for a never-recalled item just because its stamp is now readable.
        for name, dialect in DIALECTS:
            with self.subTest(dialect=name):
                signal = lifecycle.UsageSignal(hits=0, last_recalled_at=dialect(ONE_HALF_LIFE_AGO))
                self.assertEqual(
                    lifecycle.recency_score(signal, created_at="", now=dialect(NOW)), 0.0)


if __name__ == "__main__":
    unittest.main()
