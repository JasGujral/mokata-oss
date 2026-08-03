"""P1 — the ONE ISO lexer, pinned directly. The mutation-provable half.

THE BUG. `datetime.fromisoformat` only learned the Zulu designator ('…Z') in Python 3.11. mokata's
declared floor is 3.10, where every `…Z` stamp raises ValueError — and at most consumer sites that
fault was ABSORBED to None by `lifecycle.parse_iso`, degrading a real signal to 0.0 with nothing
logged and nothing failing. The ranking was quietly wrong on the interpreter the package claims to
support, and branch CI (3.12 only) was structurally unable to see it.

WHY THESE PINS ARE ON THE LEXER'S TEXT, not on parsed results. `_normalize_iso` maps string to
string, so its expected output is FIXED TEXT that does not depend on what the running interpreter
can parse. These tests therefore fail on 3.12 exactly as loudly as on 3.10 — which is the entire
point of pinning here. A fix gated behind `sys.version_info` would leave the 3.10 branch green-by-
absence on every CI run between releases; a pin that only bites on the interpreter CI does not run
is not a guard. If P1 ever passes on one interpreter and fails on the other, the fix has been
version-gated and the gate is the bug.

P2 (the seven consumer sites) lives in `test_iso_dialect_consumers.py` — separate file so a
mutation verdict can name which half caught it.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

import _support  # noqa: F401 - puts src/ on the path

from _iso_fixture import NOW, zulu

from mokata.memory import lifecycle


class P1TheLexer(unittest.TestCase):
    """`_normalize_iso` rewrites the Zulu designator and touches NOTHING else."""

    def test_a_trailing_capital_z_becomes_the_explicit_utc_offset(self):
        self.assertEqual(lifecycle._normalize_iso("2026-01-01T00:00:00Z"),
                         "2026-01-01T00:00:00+00:00")

    def test_b_trailing_lowercase_z_becomes_the_explicit_utc_offset(self):
        # RFC 3339 says the designator is case-insensitive; hand-edited docs and some clients emit
        # the lowercase form, so the lexer must not be a `.endswith("Z")` half-measure.
        self.assertEqual(lifecycle._normalize_iso("2026-01-01T00:00:00z"),
                         "2026-01-01T00:00:00+00:00")

    def test_c_an_already_explicit_utc_offset_is_untouched(self):
        stamp = "2026-01-01T00:00:00+00:00"
        self.assertEqual(lifecycle._normalize_iso(stamp), stamp)

    def test_d_a_non_utc_offset_is_untouched(self):
        # Catches a lexer that "normalizes to UTC" rather than rewriting a designator: +05:30 is a
        # DIFFERENT instant and must survive byte-identical.
        stamp = "2026-01-01T00:00:00+05:30"
        self.assertEqual(lifecycle._normalize_iso(stamp), stamp)

    def test_e_a_naive_stamp_is_untouched(self):
        stamp = "2026-01-01T00:00:00"
        self.assertEqual(lifecycle._normalize_iso(stamp), stamp)

    def test_f_degenerate_inputs_do_not_crash_the_lexer(self):
        # The lexer sits AHEAD of the absorber, so anything it raises escapes a path whose contract
        # is "never raises". It must be total over junk, and leave junk as junk for the parser.
        self.assertEqual(lifecycle._normalize_iso("Z"), "Z")     # a bare designator is not a stamp
        self.assertEqual(lifecycle._normalize_iso(""), "")
        self.assertIsNone(lifecycle._normalize_iso(None))

    def test_g_the_normalized_text_parses_to_the_same_instant(self):
        # The lexer's output is TEXT; this is the pin that the text actually parses, on THIS
        # interpreter, to the same aware moment as the explicit dialect.
        self.assertEqual(lifecycle.parse_iso_strict(zulu(NOW)), lifecycle.parse_iso_strict(NOW))
        self.assertEqual(lifecycle.parse_iso_strict(zulu(NOW)),
                         datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc))


class P1TheWrapperContracts(unittest.TestCase):
    """Both wrappers route through the lexer, and their raise/absorb contracts are UNCHANGED."""

    def test_a_strict_routes_zulu_through_the_lexer(self):
        # RED if `parse_iso_strict` calls `fromisoformat` directly again.
        self.assertEqual(lifecycle.parse_iso_strict(zulu(NOW)),
                         datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc))

    def test_b_lenient_routes_zulu_through_the_lexer(self):
        # RED if `parse_iso` stops delegating to `parse_iso_strict`.
        self.assertEqual(lifecycle.parse_iso(zulu(NOW)),
                         datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc))

    def test_c_strict_still_RAISES_on_genuinely_malformed_input(self):
        # The contract split is the whole reason there are two wrappers. If strict stopped raising,
        # `item.add_seconds` would silently compute a TTL off a stamp it could not read.
        for junk in ("not-a-timestamp", "Z", "", None):
            with self.subTest(junk=junk):
                with self.assertRaises((TypeError, ValueError)):
                    lifecycle.parse_iso_strict(junk)

    def test_d_lenient_still_NEVER_raises(self):
        for junk in ("not-a-timestamp", "Z", "2026-13-45T99:99:99Z", "", None, 17, object()):
            with self.subTest(junk=junk):
                self.assertIsNone(lifecycle.parse_iso(junk))

    def test_e_a_naive_stamp_is_read_as_utc_by_both_wrappers(self):
        naive = "2026-07-29T12:00:00"
        self.assertEqual(lifecycle.parse_iso_strict(naive), lifecycle.parse_iso(NOW))
        self.assertEqual(lifecycle.parse_iso(naive), lifecycle.parse_iso(NOW))


class P1TheFixIsNotVersionGated(unittest.TestCase):
    """The normalization is UNCONDITIONAL — no interpreter branch in this path.

    Pinned structurally as well as behaviourally, because the behavioural pins above cannot by
    themselves distinguish "unconditional" from "conditional and this interpreter took the good
    branch". Two lexers is the failure mode: whichever one CI does not run is the one that rots.
    """

    def test_a_the_lexer_source_contains_no_interpreter_branch(self):
        import inspect

        source = inspect.getsource(lifecycle._normalize_iso)
        for gate in ("version_info", "sys.version", "PY3", "platform."):
            self.assertNotIn(gate, source,
                             f"`_normalize_iso` branches on {gate!r} — a version-gated fix ships "
                             f"two lexers and hides one from CI")


if __name__ == "__main__":
    unittest.main()
