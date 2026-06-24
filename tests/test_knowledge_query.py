"""B2/B3 — the grep floor implements the typed structural query API correctly on a
small sample repo (the documented fallback when no graph backend is present)."""

import dataclasses
import tempfile
import unittest

from _support import write_sample_repo

from mokata.knowledge import GrepBackend, QueryResult, Reference


class TestGrepStructuralQueries(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = write_sample_repo(self.tmp.name)
        self.backend = GrepBackend(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def _symbols(self, result):
        return {r.symbol for r in result.references}

    def _files(self, result):
        return {r.path for r in result.references}

    def test_callers(self):
        r = self.backend.query("callers", "compute")
        # called from Impl.run (mod_a) and main (mod_b); the def line is excluded
        self.assertEqual(self._files(r), {"mod_a.py", "mod_b.py"})
        self.assertEqual(self._symbols(r), {"run", "main"})

    def test_callees(self):
        r = self.backend.query("callees", "compute")
        self.assertIn("helper", self._symbols(r))

    def test_implementers(self):
        r = self.backend.query("implementers", "Base")
        self.assertEqual(self._symbols(r), {"Impl", "OtherImpl"})

    def test_imports(self):
        r = self.backend.query("imports", "mod_a")
        self.assertEqual(self._files(r), {"mod_b.py"})

    def test_blast_radius_depth_1_is_direct_callsites(self):
        r = self.backend.query("blast_radius", "helper", depth=1)
        # helper is only called inside compute (mod_a)
        self.assertEqual(self._files(r), {"mod_a.py"})

    def test_blast_radius_depth_2_reaches_transitive_callers(self):
        r = self.backend.query("blast_radius", "helper", depth=2)
        # depth 2: callers of compute too -> reaches mod_b (main)
        self.assertIn("mod_b.py", self._files(r))

    def test_unknown_kind_raises(self):
        with self.assertRaises(ValueError):
            self.backend.query("teleport", "compute")

    def test_result_is_the_typed_shape(self):
        r = self.backend.query("callers", "compute")
        self.assertIsInstance(r, QueryResult)
        self.assertEqual(r.kind, "callers")
        self.assertEqual(r.target, "compute")
        self.assertTrue(all(isinstance(x, Reference) for x in r.references))
        self.assertFalse(self.backend.is_graph)
        self.assertTrue(r.degraded)   # grep floor is always a degraded answer
        self.assertEqual(r.count, len(r.references))
        # the shape is a fixed set of fields
        self.assertEqual(
            {f.name for f in dataclasses.fields(QueryResult)},
            {"kind", "target", "references", "backend", "degraded", "note"},
        )


if __name__ == "__main__":
    unittest.main()
