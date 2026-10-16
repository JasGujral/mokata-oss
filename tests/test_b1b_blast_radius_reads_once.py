"""0.0.23 B1b — `BLAST-RADIUS-WALKS-THE-CORPUS-PER-SYMBOL` (Jas 2026-10-15: *"blast radius timeout is
happening frequently … so no amend working"*).

The grep floor answered a depth-2 blast radius by calling `_callers` once per symbol found at hop 1,
and every call re-walked and re-read the whole tree. On a Python repo whose `venv/` was walked as
source (`VENV-NOT-PRUNED`, B1) that is tens of thousands of files read dozens of times, and
`spec amend`'s 20 s impact lens refused every widening as `blast-radius-timeout`. Measured on a
300-file repo with a 15,000-file un-dotted venv: v0.0.22 did not answer in 300 s; this tree answers
in 0.1 s.

Pinned here: the corpus is read ONCE per query, the answer is IDENTICAL to the per-symbol walk, and
a sentence (an acceptance criterion handed to the lens) costs no walk at all.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import shutil
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata.knowledge.grep_backend import GrepBackend


def _repo():
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "src"))
    files = {
        "src/a.py": "def leaf(x):\n    return x\n",
        "src/b.py": "def mid(x):\n    return leaf(x)\n\ndef mid2(x):\n    return leaf(x) + leafy(x)\n",
        "src/c.py": "def top(x):\n    return mid(x) + mid2(x)\n",
        "src/d.py": "def other(x):\n    return top(x)\n",
        "src/e.py": "def leafy(x):\n    return 1\n",
    }
    for rel, body in files.items():
        with open(os.path.join(d, *rel.split("/")), "wb") as fh:
            fh.write(body.encode("utf-8"))
    return d, len(files)


def _per_symbol_reference(backend, target, depth):
    """The pre-0.0.23 algorithm, verbatim, as the oracle."""
    seen_syms, seen_refs, out, frontier = {target}, set(), [], [target]
    for _ in range(max(1, depth)):
        nxt = []
        for sym in frontier:
            for r in backend._callers(sym):
                if (r.path, r.line) not in seen_refs:
                    seen_refs.add((r.path, r.line))
                    out.append(r)
                if r.symbol and r.symbol not in seen_syms:
                    seen_syms.add(r.symbol)
                    nxt.append(r.symbol)
        frontier = nxt
        if not frontier:
            break
    return out


class TheCorpusIsReadOnce(unittest.TestCase):

    def setUp(self):
        self.root, self.n = _repo()
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_a_depth_3_blast_radius_reads_each_file_once(self):
        b = GrepBackend(self.root)
        real = GrepBackend._read_file
        with mock.patch.object(GrepBackend, "_read_file", side_effect=real) as spy:
            b.query("blast_radius", "leaf", depth=3)
        self.assertEqual(spy.call_count, self.n)

    def test_the_answer_is_identical_to_the_per_symbol_walk(self):
        b = GrepBackend(self.root)
        new = b.query("blast_radius", "leaf", depth=3).references
        old = _per_symbol_reference(GrepBackend(self.root), "leaf", 3)
        self.assertEqual([(r.path, r.line, r.symbol) for r in new],
                         [(r.path, r.line, r.symbol) for r in old])
        self.assertTrue(new, "the fixture must exercise a real chain")

    def test_overlapping_names_answer_like_the_single_symbol_scan(self):
        """`leaf` and `leafy` share a prefix — each must get exactly its own call sites."""
        b = GrepBackend(self.root)
        many = b._callers_of_many(["leaf", "leafy"])
        self.assertEqual([r.line for r in many["leaf"]], [r.line for r in b._callers("leaf")])
        self.assertEqual([r.line for r in many["leafy"]], [r.line for r in b._callers("leafy")])

    def test_the_cache_never_outlives_a_query(self):
        """An edit between two queries is always seen."""
        b = GrepBackend(self.root)
        self.assertEqual(len(b.query("callers", "other").references), 0)
        with open(os.path.join(self.root, "src", "f.py"), "wb") as fh:
            fh.write(b"def z():\n    return other(1)\n")
        self.assertEqual(len(b.query("callers", "other").references), 1)


class ASentenceCostsNoWalk(unittest.TestCase):

    def test_an_acceptance_criterion_is_not_walked(self):
        root, _ = _repo()
        self.addCleanup(shutil.rmtree, root, True)
        b = GrepBackend(root)
        with mock.patch.object(GrepBackend, "_read_file") as spy:
            refs = b.query("blast_radius", "the login form rejects an empty email", depth=2)
        self.assertEqual(refs.references, [])
        self.assertEqual(spy.call_count, 0)


if __name__ == "__main__":
    unittest.main()
