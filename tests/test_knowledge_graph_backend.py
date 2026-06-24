"""B1 — the code-review-graph adapter: translates typed queries to the adopted backend
(via an injected client) and the typed results back. No parser, no in-house graph."""

import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.knowledge import BackendError, CodeReviewGraphBackend, QueryResult


class FakeGraphClient:
    """Stands in for the real code-review-graph process in tests."""

    def __init__(self, rows_by_kind):
        self.rows = rows_by_kind
        self.calls = []

    def query(self, kind, target, root, depth=1):
        self.calls.append((kind, target, depth))
        return list(self.rows.get(kind, []))


class FailingGraphClient:
    def query(self, kind, target, root, depth=1):
        raise RuntimeError("graph process exploded")


class TestCodeReviewGraphAdapter(unittest.TestCase):
    def test_adapter_returns_typed_shape_from_backend_rows(self):
        client = FakeGraphClient({
            "callers": [
                {"path": "a.py", "line": 12, "snippet": "foo()", "symbol": "bar"},
                {"path": "b.py", "line": 3, "snippet": "foo()", "symbol": "baz"},
            ]
        })
        backend = CodeReviewGraphBackend(name="code-review-graph", root=".",
                                         client=client)
        r = backend.query("callers", "foo")
        self.assertIsInstance(r, QueryResult)
        self.assertEqual(r.backend, "code-review-graph")
        self.assertTrue(backend.is_graph)
        self.assertFalse(r.degraded)              # a real graph answer
        self.assertEqual(r.count, 2)
        self.assertEqual(r.references[0].path, "a.py")
        self.assertEqual(r.references[0].symbol, "bar")
        self.assertEqual(client.calls[0][0], "callers")

    def test_depth_is_passed_through(self):
        client = FakeGraphClient({"blast_radius": []})
        backend = CodeReviewGraphBackend(name="code-review-graph", root=".",
                                         client=client)
        backend.query("blast_radius", "foo", depth=3)
        self.assertEqual(client.calls[0], ("blast_radius", "foo", 3))

    def test_client_failure_raises_backend_error(self):
        backend = CodeReviewGraphBackend(name="code-review-graph", root=".",
                                         client=FailingGraphClient())
        with self.assertRaises(BackendError):
            backend.query("callers", "foo")

    def test_unknown_kind_rejected(self):
        backend = CodeReviewGraphBackend(name="code-review-graph", root=".",
                                         client=FakeGraphClient({}))
        with self.assertRaises(ValueError):
            backend.query("teleport", "foo")


if __name__ == "__main__":
    unittest.main()
