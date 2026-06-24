"""C1/C2/C4/C6/C8/C9 — the memory store: default-on, human-gated writes, persistence,
per-type toggles, decision memory, backend selection via the router, instrumentation."""

import os
import tempfile
import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.config import Surface
from mokata.detect import Detector
from mokata.init import init_repo
from mokata.manifest import Manifest
from mokata.memory import (
    DECISION,
    PERSISTENT,
    MemoryDisabledError,
    MemoryItem,
    MemoryStore,
    ObsidianBackend,
    SQLiteBackend,
    enabled_memory_types,
)
from mokata.profiles import build_manifest_data
from mokata.router import Router


def silent(_):
    pass


def store_on(tmp, name="memory.db"):
    return MemoryStore(SQLiteBackend(os.path.join(tmp, name)))


def full_router(overrides):
    return Router(Manifest.from_dict(build_manifest_data("full", "0.1.0")),
                 Detector(overrides=overrides))


class TestHumanGatedWrites(unittest.TestCase):
    def test_no_write_commits_without_approval(self):
        with tempfile.TemporaryDirectory() as d:
            store = store_on(d)
            res = store.remember(MemoryItem.create("db.engine", "postgres"),
                                 confirm=lambda _text: False)   # user declines
            self.assertFalse(res.committed)
            self.assertTrue(res.aborted)
            self.assertEqual(store.all_active(), [])             # nothing stored

    def test_write_commits_after_approval(self):
        with tempfile.TemporaryDirectory() as d:
            store = store_on(d)
            res = store.remember(MemoryItem.create("db.engine", "postgres"),
                                 confirm=lambda _text: True)
            self.assertTrue(res.committed)
            self.assertEqual([i.value for i in store.all_active()], ["postgres"])


class TestPersistenceAcrossSessions(unittest.TestCase):
    def test_fact_written_now_is_readable_in_a_new_session(self):
        with tempfile.TemporaryDirectory() as d:
            s1 = store_on(d)
            s1.remember(MemoryItem.create("db.engine", "postgres"), assume_yes=True)
            s1.close()
            # a brand new store over the same backing file = a new session
            s2 = store_on(d)
            hits = s2.recall("db.engine")
            self.assertEqual([i.value for i in hits], ["postgres"])


class TestDefaultOn(unittest.TestCase):
    def test_memory_is_active_by_default_with_no_opt_in(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=silent)
            surface = Surface.load(d)
            store = MemoryStore.from_surface(surface)
            # no opt-in flag anywhere: both v1 types are live out of the box
            self.assertTrue(store.type_enabled(PERSISTENT))
            self.assertTrue(store.type_enabled(DECISION))
            store.remember(MemoryItem.create("k", "v"), assume_yes=True)
            self.assertEqual(len(store.all_active()), 1)

    def test_enabled_types_helper_defaults_all_on(self):
        from mokata.memory import EPISODIC
        m = Manifest.from_dict(build_manifest_data("standard", "0.1.0"))
        self.assertEqual(set(enabled_memory_types(m)),
                         {PERSISTENT, DECISION, EPISODIC})


class TestPerTypeToggles(unittest.TestCase):
    def test_disabling_a_type_removes_it_cleanly(self):
        m_data = build_manifest_data("full", "0.1.0")
        m_data["settings"]["memory"] = {"decision": False, "episodic": False}
        m = Manifest.from_dict(m_data)
        self.assertEqual(set(enabled_memory_types(m)), {PERSISTENT})

        with tempfile.TemporaryDirectory() as d:
            store = MemoryStore(SQLiteBackend(os.path.join(d, "m.db")),
                                enabled_types=enabled_memory_types(m))
            # writing a disabled type is refused
            with self.assertRaises(MemoryDisabledError):
                store.remember(MemoryItem.create("api", "REST", mtype=DECISION),
                               assume_yes=True)
            # a persistent write still works
            store.remember(MemoryItem.create("k", "v"), assume_yes=True)
            # reads never surface a disabled type, even if rows exist
            store.backend.put(MemoryItem.create("api", "REST", mtype=DECISION))
            self.assertEqual([i.mtype for i in store.all_active()], [PERSISTENT])

    def test_memory_layer_off_disables_all_types(self):
        m = Manifest.from_dict(build_manifest_data("minimal", "0.1.0"))
        self.assertEqual(enabled_memory_types(m), ())


class TestDecisionMemoryWired(unittest.TestCase):
    def test_decision_type_is_first_class(self):
        with tempfile.TemporaryDirectory() as d:
            store = store_on(d)
            store.remember(
                MemoryItem.create("api.style", "REST", mtype=DECISION),
                assume_yes=True)
            decisions = store.all_active(mtype=DECISION)
            self.assertEqual([i.value for i in decisions], ["REST"])


class TestBackendSelectionViaRouter(unittest.TestCase):
    def test_defaults_to_sqlite_floor(self):
        with tempfile.TemporaryDirectory() as d:
            router = full_router({"native-memory": False, "obsidian": False})
            store = MemoryStore.from_router(router, root=d)
            self.assertEqual(store.backend.name, "sqlite")

    def test_selects_obsidian_when_router_resolves_it(self):
        with tempfile.TemporaryDirectory() as d:
            router = full_router({"native-memory": False, "obsidian": True})
            store = MemoryStore.from_router(router, root=d)
            self.assertEqual(store.backend.name, "obsidian")


class TestBackendSwap(unittest.TestCase):
    def test_same_behavior_across_backends(self):
        with tempfile.TemporaryDirectory() as d:
            results = {}
            for label, backend in (
                ("sqlite", SQLiteBackend(os.path.join(d, "m.db"))),
                ("obsidian", ObsidianBackend(os.path.join(d, "vault"))),
            ):
                store = MemoryStore(backend)
                store.remember(MemoryItem.create("db.engine", "postgres"),
                               assume_yes=True)
                results[label] = [i.value for i in store.recall("db.engine")]
                store.close()
            self.assertEqual(results["sqlite"], results["obsidian"])
            self.assertEqual(results["sqlite"], ["postgres"])


class TestInstrumentation(unittest.TestCase):
    def test_read_write_ratio_is_logged(self):
        with tempfile.TemporaryDirectory() as d:
            store = store_on(d)
            store.remember(MemoryItem.create("a", "1"), assume_yes=True)
            store.recall("a")
            store.recall("a")
            stats = store.stats
            self.assertEqual(stats.writes, 1)
            self.assertEqual(stats.reads, 2)
            self.assertEqual(stats.ratio, 2.0)
            self.assertIn("read/write", stats.log_line().lower())

    def test_stats_persist_across_sessions_via_state(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="full", assume_yes=True, out=silent)
            s1 = MemoryStore.from_surface(Surface.load(d))
            s1.remember(MemoryItem.create("a", "1"), assume_yes=True)
            s1.recall("a")
            s1.close()
            s2 = MemoryStore.from_surface(Surface.load(d))
            self.assertEqual(s2.stats.writes, 1)
            self.assertEqual(s2.stats.reads, 1)


if __name__ == "__main__":
    unittest.main()
