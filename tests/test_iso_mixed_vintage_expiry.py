"""P3 — MIXED VINTAGE: a store holding both shapes of `expires_at` decides identically.

`item.add_seconds` changed shape in 0.0.16. It routes through `lifecycle.parse_iso_strict`, which
reads a NAIVE stamp AS UTC and hands back an aware datetime — so `.isoformat()` now emits an
explicit `+00:00` where 0.0.15's bare `datetime.fromisoformat` emitted a naive stamp:

    add_seconds("2026-07-01T06:00:00", 3600)   0.0.15 -> "2026-07-01T07:00:00"
                                               0.0.16 -> "2026-07-01T07:00:00+00:00"

Only the NAIVE input dialect moved. `+00:00` and other explicit offsets were byte-identical before
and after; `…Z` RAISED on the declared 3.10 floor, so it wrote no row at all to be mixed with.

That means a store written by 0.0.15 and reopened by 0.0.16 holds BOTH shapes at once, naming the
SAME instant, for the rest of its life — there is no migration and deliberately none, because the
audit found nothing that can observe the difference: `expires_at` is not a COLUMN in either backend
(it lives inside the `doc` JSON, so neither Postgres TIMESTAMP truncation nor SQLite's TEXT
lexicographic compare can reach it), and no read path compares, sorts or keys it as a raw string.
`migrate.item_digest` hashes only id/mtype/subject/value/status, so the batch-derivation guard does
not see it either.

"Nothing can observe it" is a claim about the whole read surface, and this file is what makes it
FALSIFIABLE rather than merely asserted. Three decisions, each run against both vintages of the one
instant, each required to come out the same:

  V1  TTL EXPIRY   — `detect_issues` fires STALE for both, or neither, on each side of the instant.
  V2  ORDERING     — parsed instants compare EQUAL, and a later item sorts after both regardless of
                     vintage. This is the one with teeth: as TEXT, "…T07:00:00" is a PREFIX of
                     "…T07:00:00+00:00" and therefore sorts FIRST, so a raw-string sort separates
                     two stamps that name the same moment.
  V3  LOOKUP       — a real SQLite store round-trips both, `get`/`all` return them intact, and the
                     doc key set is identical, so neither vintage is a differently-shaped doc.

The naive row is written through the STORE with a hand-written stamp (`MemoryItem(...)` direct,
which is what a 0.0.15 write left on disk), never through `create`, so the fixture cannot silently
start testing 0.0.16 against itself if `add_seconds` changes again.

P1 (the lexer) is `test_iso_dialect_split.py`; P2 (consumer equivalence) is
`test_iso_dialect_consumers.py`.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

import _support  # noqa: F401 - puts src/ on the path

from mokata.memory.backends import SQLiteBackend
from mokata.memory.healing import STALE, detect_issues
from mokata.memory.item import ACTIVE, PERSISTENT, MemoryItem, add_seconds
from mokata.memory.lifecycle import parse_iso, parse_iso_strict

# ONE instant, in the two vintages. `_CREATED` + one hour is `_INSTANT`, which is what makes the
# 0.0.16 row genuinely a product of the new write path rather than a hand-typed twin of it.
_CREATED = "2026-07-01T06:00:00"                  # naive, as a 0.0.15 caller supplied it
_INSTANT_NAIVE = "2026-07-01T07:00:00"            # what 0.0.15's add_seconds stored
_INSTANT_AWARE = "2026-07-01T07:00:00+00:00"      # what 0.0.16's add_seconds stores

_BEFORE = "2026-07-01T06:30:00+00:00"             # both rows still LIVE here
_AFTER = "2026-07-01T07:30:00+00:00"              # both rows EXPIRED here

# THE BOUNDARY, and the only `now` at which a raw-string compare actually SPLITS the two vintages.
# It has to be the instant itself: away from it the hour digits dominate the compare and text and
# instants happen to agree, which is why an off-boundary pin lets a string-compare regression
# through (this file's first draft did, and the mutant survived). Here:
#
#   "2026-07-01T07:00:00"        >= "2026-07-01T07:00:00+00:00"  -> False as TEXT (a prefix is
#                                                                   LESS) -> legacy read EXPIRED
#   "2026-07-01T07:00:00+00:00"  >= "2026-07-01T07:00:00+00:00"  -> True  -> current read LIVE
#
# As instants both are `>= now`, so the correct answer is NEITHER stale — the same answer for both.
_AT = _INSTANT_AWARE


def _legacy_item(item_id: str) -> MemoryItem:
    """A row exactly as 0.0.15 left it: a naive `expires_at`, built without `create`."""
    return MemoryItem(subject=f"host {item_id}", value="10.0.0.1", id=item_id,
                      mtype=PERSISTENT, status=ACTIVE, expires_at=_INSTANT_NAIVE,
                      provenance={"source": "test", "author": "t", "created_at": _CREATED})


def _current_item(item_id: str) -> MemoryItem:
    """A row written by THIS build's path — `create` -> `add_seconds` -> aware stamp."""
    return MemoryItem.create(subject=f"host {item_id}", value="10.0.0.1", id=item_id,
                             source="test", author="t", created_at=_CREATED, valid_for=3600)


class TestFixtureIsHonest(unittest.TestCase):
    """The fixture's own preconditions. If these drift, every assertion below is vacuous."""

    def test_the_two_vintages_are_textually_different(self):
        self.assertNotEqual(_INSTANT_NAIVE, _INSTANT_AWARE,
                            "mixed vintage is only a hazard if the two stamps differ as text")

    def test_the_two_vintages_name_the_same_instant(self):
        self.assertEqual(parse_iso_strict(_INSTANT_NAIVE), parse_iso_strict(_INSTANT_AWARE),
                         "the whole premise: one moment, written two ways")

    def test_this_build_writes_the_aware_vintage(self):
        self.assertEqual(add_seconds(_CREATED, 3600), _INSTANT_AWARE)
        self.assertEqual(_current_item("c").expires_at, _INSTANT_AWARE,
                         "if create() stops emitting the aware shape this file is comparing "
                         "0.0.15 against itself and proves nothing")

    def test_the_legacy_row_is_not_built_through_the_new_path(self):
        self.assertEqual(_legacy_item("l").expires_at, _INSTANT_NAIVE)


# --------------------------------------------------------------------------- V1  TTL expiry
class TestV1TtlExpiry(unittest.TestCase):
    """Both vintages must fire STALE together and stay live together."""

    def _kinds(self, item, now):
        return [p.kind for p in detect_issues([item], now=now)]

    def test_both_vintages_are_live_before_the_instant(self):
        self.assertNotIn(STALE, self._kinds(_legacy_item("l"), _BEFORE))
        self.assertNotIn(STALE, self._kinds(_current_item("c"), _BEFORE))

    def test_both_vintages_are_stale_after_the_instant(self):
        self.assertIn(STALE, self._kinds(_legacy_item("l"), _AFTER),
                      "a 0.0.15 naive TTL must still expire — read as UTC, not dropped")
        self.assertIn(STALE, self._kinds(_current_item("c"), _AFTER))

    def test_at_the_boundary_neither_vintage_is_stale(self):
        """The load-bearing case. A raw-string compare calls the LEGACY row expired here and the
        current one live — the same instant, two verdicts, decided by which build wrote it."""
        self.assertNotIn(STALE, self._kinds(_legacy_item("l"), _AT),
                         "a naive stamp equal to `now` must not read as already expired")
        self.assertNotIn(STALE, self._kinds(_current_item("c"), _AT))

    def test_the_two_vintages_agree_row_for_row_in_one_pass(self):
        """Both in ONE detection pass, which is how a real mixed store is actually read."""
        for now, expect_stale in ((_BEFORE, False), (_AT, False), (_AFTER, True)):
            with self.subTest(now=now):
                stale = {p.subject for p in detect_issues(
                    [_legacy_item("l"), _current_item("c")], now=now) if p.kind == STALE}
                self.assertEqual(stale, {"host l", "host c"} if expect_stale else set(),
                                 "one vintage expired while the other did not")


# --------------------------------------------------------------------------- V2  ordering
class TestV2Ordering(unittest.TestCase):
    """Ordering by expiry must be by INSTANT. The raw-string sort is the failure being pinned."""

    def test_the_raw_string_sort_disagrees_with_the_instant_sort(self):
        """The negative control. Without this, V2 could pass because sorting is a no-op here."""
        self.assertLess(_INSTANT_NAIVE, _INSTANT_AWARE,
                        "as TEXT the naive stamp is a PREFIX and sorts first — that is the bug "
                        "this class exists to keep out")

    def test_the_two_vintages_compare_equal_as_instants(self):
        self.assertEqual(parse_iso(_legacy_item("l").expires_at),
                         parse_iso(_current_item("c").expires_at))

    def test_a_later_row_sorts_after_both_vintages(self):
        later = MemoryItem(subject="host z", value="v", id="z", mtype=PERSISTENT, status=ACTIVE,
                           expires_at="2026-07-01T08:00:00+00:00",
                           provenance={"source": "test", "author": "t", "created_at": _CREATED})
        for first in (_legacy_item("l"), _current_item("c")):
            with self.subTest(vintage=first.expires_at):
                ordered = sorted([later, first], key=lambda i: parse_iso(i.expires_at))
                self.assertEqual([i.id for i in ordered], [first.id, "z"],
                                 "expiry order must not depend on which vintage wrote the stamp")


# --------------------------------------------------------------------------- V3  lookup
class TestV3StoreRoundTrip(unittest.TestCase):
    """A REAL store holding both vintages at once — the mixed store the audit was about."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.store = SQLiteBackend(os.path.join(self.root, "memory.db"))
        self.addCleanup(self.store.close)
        self.store.put(_legacy_item("legacy"))
        self.store.put(_current_item("current"))

    def test_each_vintage_survives_get_byte_for_byte(self):
        self.assertEqual(self.store.get("legacy").expires_at, _INSTANT_NAIVE,
                         "the store must not silently rewrite a legacy stamp")
        self.assertEqual(self.store.get("current").expires_at, _INSTANT_AWARE)

    def test_both_vintages_come_back_from_all(self):
        got = {i.id: i.expires_at for i in self.store.all()}
        self.assertEqual(got, {"legacy": _INSTANT_NAIVE, "current": _INSTANT_AWARE})

    def test_the_two_vintages_are_the_same_doc_SHAPE(self):
        """Same keys, differing only in the stamp — neither vintage is a differently-shaped doc."""
        legacy, current = self.store.get("legacy").to_dict(), self.store.get("current").to_dict()
        self.assertEqual(set(legacy), set(current))
        self.assertEqual({k: v for k, v in legacy.items() if k not in ("id", "subject",
                                                                       "expires_at")},
                         {k: v for k, v in current.items() if k not in ("id", "subject",
                                                                        "expires_at")})

    def test_a_mixed_store_expires_both_rows_together(self):
        """The end-to-end shape: read the mixed store back, then decide TTL over what came out."""
        rows = sorted(self.store.all(), key=lambda i: i.id)
        for now, expect in ((_BEFORE, set()), (_AT, set()),
                            (_AFTER, {"host current", "host legacy"})):
            with self.subTest(now=now):
                stale = {p.subject for p in detect_issues(rows, now=now) if p.kind == STALE}
                self.assertEqual(stale, expect,
                                 "a mixed-vintage store must not expire one row and not the other")


if __name__ == "__main__":                        # pragma: no cover
    unittest.main()
