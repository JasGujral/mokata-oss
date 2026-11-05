"""0.0.27 P5 — the intra-line diff's quadratic matcher is BOUNDED (ID-DIFF-INTRALINE).

Kept apart from test_p5_ide_diff_split_intraline on purpose: if the bound is ever lost, the big-line
tests there do not fail, they run (quadratically) for hours. This file has only small inputs, so a
lost bound is a red here in well under a second rather than a hang there.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import random
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata.ide import diffview as DV


class TheQuadraticMatcherIsBounded(unittest.TestCase):

    def test_a_gap_over_the_leaf_bound_with_no_anchor_is_one_said_span(self):
        """The quadratic matcher only ever sees a gap of <= LEAF_CELLS cells. 400 x 400 tokens
        from a 6-word vocabulary has no token unique on both sides (no anchor) and is over the
        bound: it is marked as ONE span and the answer says so — it is not run quadratically."""
        rnd = random.Random(5)
        words = ["aa", "bb", "cc", "dd", "ee", "ff"]
        a = " ".join(rnd.choice(words) for _ in range(200))
        b = " ".join(rnd.choice(words) for _ in range(200))
        self.assertGreater(DV.middle_tokens(a, b) ** 2 // 4, DV.LEAF_CELLS)
        r = DV.intraline(a, b)
        self.assertEqual("span", r["basis"])
        self.assertEqual(1, len(r["old"]))
        self.assertEqual(1, len(r["new"]))


if __name__ == "__main__":
    unittest.main()
