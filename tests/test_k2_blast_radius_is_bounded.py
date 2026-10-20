"""0.0.25 K2 — a blast radius on a hub function FINISHES, and says when it stopped early.

The IDE's Review page asks `nav.impact` — a depth-2 blast radius — for every changed file, by
itself. On a hub with ~85k call sites the AST floor's walk never finished (measured: >150 s, the
page's server thread pinned). The walk is now bounded by a reference cap and a time budget; a
bounded answer is NEVER a silent partial: `truncated` is set, the classed `truncated —` note says
which bound bit, and both survive the knowledge layer and the wire form (`to_dict`).

Mutations this module was checked against (each went RED, then was restored):
  M1 the cap check removed from `_blast_radius_bounded`     -> test_a_hub_stops_at_the_cap_and_says_so
  M2 `query` ignores the cut (returns the prefix as complete) -> test_a_hub_stops_at_the_cap_and_says_so,
                                                               test_the_time_budget_bites_and_says_so
"""

import _support  # noqa: F401

import os
import tempfile
import unittest
from unittest import mock

from mokata.knowledge import KnowledgeLayer
from mokata.knowledge import ast_backend as ast_mod
from mokata.knowledge.ast_backend import AstBackend
from mokata.knowledge.grep_backend import GrepBackend
from mokata.knowledge.query import TRUNCATED_NOTE_PREFIX

HUB_CALLERS = 40


def write_hub_repo(root):
    os.makedirs(os.path.join(root, "pkg"), exist_ok=True)
    with open(os.path.join(root, "pkg", "hub.py"), "w", encoding="utf-8") as fh:
        fh.write("def hub(x):\n    return x\n\n\ndef small(x):\n    return x\n")
    for i in range(HUB_CALLERS):
        with open(os.path.join(root, "pkg", f"m{i:03d}.py"), "w", encoding="utf-8") as fh:
            fh.write("from pkg.hub import hub, small\n\n\n"
                     f"def f{i}(v):\n    return hub(v)\n\n\n"
                     f"def g{i}(v):\n    return f{i}(v) + f{i}(v + 1)\n")
    with open(os.path.join(root, "pkg", "one.py"), "w", encoding="utf-8") as fh:
        fh.write("from pkg.hub import small\n\n\ndef only(v):\n    return small(v)\n")


class TestTheBlastRadiusIsBounded(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="k2-")
        write_hub_repo(self.root)
        self.b = AstBackend(root=self.root, grep=GrepBackend(root=self.root),
                            cache_dir=tempfile.mkdtemp(prefix="k2-cache-"))

    def test_under_the_bounds_the_answer_is_complete_and_unchanged(self):
        r = self.b.query("blast_radius", "hub", depth=2)
        self.assertFalse(r.truncated)
        self.assertIsNone(r.total)
        self.assertEqual(r.basis, "structural")
        self.assertNotIn(TRUNCATED_NOTE_PREFIX, r.note)
        self.assertEqual(r.count, HUB_CALLERS * 2)          # 40 hub sites + 40 f-call LINES

    def test_a_hub_stops_at_the_cap_and_says_so(self):
        with mock.patch.object(ast_mod, "BLAST_RADIUS_MAX_REFS", 25):
            r = self.b.query("blast_radius", "hub", depth=2)
        self.assertTrue(r.truncated, "a capped answer must say it is partial")
        self.assertEqual(r.count, 25)
        self.assertEqual(r.basis, "structural", "a structural prefix is still structural")
        self.assertIn(TRUNCATED_NOTE_PREFIX, r.note)
        self.assertIn("25-reference cap", r.note)
        wire = r.to_dict()
        self.assertTrue(wire["truncated"])
        # the prefix is the unbounded answer's prefix, in order
        full = self.b.query("blast_radius", "hub", depth=2)
        self.assertEqual([x.to_dict() for x in r.references],
                         [x.to_dict() for x in full.references[:25]])

    def test_the_time_budget_bites_and_says_so(self):
        with mock.patch.object(ast_mod, "BLAST_RADIUS_TIME_BUDGET_S", -1.0):
            r = self.b.query("blast_radius", "hub", depth=2)
        self.assertTrue(r.truncated)
        self.assertIn("time budget", r.note)

    def test_the_bound_survives_the_layer(self):
        layer = KnowledgeLayer(self.b)
        with mock.patch.object(ast_mod, "BLAST_RADIUS_MAX_REFS", 10):
            r = layer.blast_radius("hub", depth=2)
        self.assertTrue(r.truncated)
        self.assertTrue(r.to_dict()["truncated"])
        self.assertTrue(layer.history[-1].truncated)

    def test_a_leaf_is_still_a_verified_zero(self):
        r = self.b.query("blast_radius", "g0", depth=2)
        self.assertEqual(r.basis, "verified_empty")
        self.assertFalse(r.truncated)

    def test_a_large_hub_finishes_quickly(self):
        """A business bound, not a micro-benchmark: 40 callers x depth 6 must not take seconds."""
        import time
        t = time.perf_counter()
        self.b.query("blast_radius", "hub", depth=6)
        self.assertLess(time.perf_counter() - t, 2.0)


if __name__ == "__main__":
    unittest.main()
