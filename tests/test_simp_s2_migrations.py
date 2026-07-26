"""SIMP.S2 — one-time GATED migrations of the deprecated channels into the canonical store.

`mokata migrate <channel>` reuses the EXISTING gated write path (never a second one): the memory
backends (obsidian / native-memory) go through `migrate_memory`; the `memory-share.json` file goes
through `import_memory`. Every item lands via the WriteGate with provenance; secrets are hard-
blocked on ingest; the source is left in place (NON-destructive); a re-run is a no-op ("already
migrated"). Declining the human gate writes NOTHING.
"""

import json
import os
import tempfile
import unittest

import _support  # noqa: F401

from mokata import MOKATA_DIR
from mokata.config import Surface
from mokata.init import init_repo
from mokata.memory import MemoryItem, MemoryStore
from mokata.memory.backends import ObsidianBackend
from mokata.memory.share import SHARE_KIND, SHARE_SCHEMA_VERSION, MEMORY_SHARE_FILENAME
from mokata import migrate_channels as MC


def _silent(_):
    pass


def _repo(d):
    init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
    return Surface.load(d)


def _manifest_path(d):
    return os.path.join(d, MOKATA_DIR, "manifest.json")


def _wire_obsidian(d, vault_path):
    """Make `obsidian` a resolvable source pointing at `vault_path` (does NOT change the resolved
    store — that stays the canonical sqlite floor, so the migration has a real destination)."""
    p = _manifest_path(d)
    with open(p, encoding="utf-8") as fh:
        data = json.load(fh)
    data.setdefault("tools", {})["obsidian"] = {
        "provides": "memory_store", "kind": "external", "enabled": True,
        "detect": {"type": "obsidian"}, "config": {"vault": vault_path}, "deprecated": "0.0.17"}
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    return Surface.load(d)


def _canonical_items(surface):
    store = MemoryStore.from_surface(surface)
    items = store.all_active()
    store.close()
    return {i.subject: i for i in items}


class _FakeNativeClient:
    """A minimal native-memory client (the `MemoryClient` protocol) over an in-memory dict."""

    def __init__(self):
        self._docs = {}

    def put(self, doc):
        self._docs[doc["id"]] = doc

    def get(self, item_id):
        return self._docs.get(item_id)

    def all(self):
        return list(self._docs.values())

    def delete(self, item_id):
        return self._docs.pop(item_id, None) is not None


class TestObsidianMigration(unittest.TestCase):
    def test_round_trip_preview_approve_provenance_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            vault = os.path.join(d, "obs-vault")
            be = ObsidianBackend(vault)
            be.put(MemoryItem.create("api-timeout", "30s", source="obsidian-note", author="alice"))
            be.put(MemoryItem.create("retries", "3", source="obsidian-note", author="alice"))
            surface = _wire_obsidian(d, vault)

            # preview — counts + a sample, NO writes
            plan = MC.plan_channel_migration(surface, "obsidian")
            self.assertEqual(plan.count, 2)
            self.assertIn("api-timeout", plan.sample)
            self.assertEqual(_canonical_items(surface), {})       # nothing written by the preview

            # approve → items land in the canonical store WITH provenance
            res = MC.run_channel_migration(surface, "obsidian", assume_yes=True, out=_silent)
            self.assertFalse(res.aborted)
            self.assertEqual(res.migrated, 2)
            canon = _canonical_items(Surface.load(d))
            self.assertEqual(canon["api-timeout"].value, "30s")
            self.assertEqual(canon["api-timeout"].provenance.get("author"), "alice")

            # source left intact (NON-destructive)
            self.assertEqual(len(ObsidianBackend(vault).all()), 2)

            # idempotent re-run — "already migrated, nothing to do"
            res2 = MC.run_channel_migration(surface, "obsidian", assume_yes=True, out=_silent)
            self.assertTrue(res2.already_migrated)
            self.assertEqual(res2.migrated, 0)

    def test_decline_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            vault = os.path.join(d, "obs-vault")
            ObsidianBackend(vault).put(
                MemoryItem.create("k", "v", source="obsidian-note", author="bob"))
            surface = _wire_obsidian(d, vault)

            res = MC.run_channel_migration(surface, "obsidian",
                                           confirm=lambda _t: False, out=_silent)
            self.assertTrue(res.aborted)
            self.assertEqual(res.migrated, 0)
            self.assertEqual(_canonical_items(Surface.load(d)), {})   # zero writes


class TestNativeMemoryMigration(unittest.TestCase):
    def test_round_trip_from_injected_client(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            client = _FakeNativeClient()
            client.put(MemoryItem.create("db-pool", "10", source="native", author="carol").to_doc())
            res = MC.run_channel_migration(surface, "native-memory", assume_yes=True,
                                           client=client, out=_silent)
            self.assertEqual(res.migrated, 1)
            canon = _canonical_items(Surface.load(d))
            self.assertEqual(canon["db-pool"].value, "10")

    def test_missing_client_refuses_never_reads_floor(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            res = MC.run_channel_migration(surface, "native-memory", assume_yes=True, out=_silent)
            self.assertTrue(res.aborted)               # no client → refuse, never silently a floor
            self.assertEqual(res.migrated, 0)


class TestMemoryShareMigration(unittest.TestCase):
    def _write_share(self, path, items):
        data = {"schema_version": SHARE_SCHEMA_VERSION, "kind": SHARE_KIND,
                "items": [MemoryItem.create(s, v, source="share", author="dave").to_dict()
                          for s, v in items]}
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    def test_round_trip_and_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            share = os.path.join(d, MOKATA_DIR, MEMORY_SHARE_FILENAME)
            self._write_share(share, [("region", "eu-west"), ("tier", "gold")])

            plan = MC.plan_channel_migration(surface, "memory-share")
            self.assertEqual(plan.count, 2)

            res = MC.run_channel_migration(surface, "memory-share", assume_yes=True, out=_silent)
            self.assertEqual(res.migrated, 2)
            canon = _canonical_items(Surface.load(d))
            self.assertEqual(canon["region"].value, "eu-west")
            # share file left in place (NON-destructive)
            self.assertTrue(os.path.exists(share))

            res2 = MC.run_channel_migration(surface, "memory-share", assume_yes=True, out=_silent)
            self.assertTrue(res2.already_migrated)

    def test_decline_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            share = os.path.join(d, MOKATA_DIR, MEMORY_SHARE_FILENAME)
            self._write_share(share, [("region", "eu-west")])
            res = MC.run_channel_migration(surface, "memory-share",
                                           confirm=lambda _t: False, out=_silent)
            self.assertTrue(res.aborted)
            self.assertEqual(_canonical_items(Surface.load(d)), {})


class TestMigrationSecretSafety(unittest.TestCase):
    def test_preview_shows_keys_not_values_and_no_dsn(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            vault = os.path.join(d, "obs-vault")
            ObsidianBackend(vault).put(MemoryItem.create(
                "conn", "postgres://user:pw@host/db", source="obsidian-note", author="e"))
            surface = _wire_obsidian(d, vault)
            plan = MC.plan_channel_migration(surface, "obsidian")
            blob = plan.render()
            self.assertIn("conn", blob)                 # the KEY is fine to show
            self.assertNotIn("://", blob)               # never the value / a DSN
            self.assertNotIn("pw@host", blob)


if __name__ == "__main__":
    unittest.main()
