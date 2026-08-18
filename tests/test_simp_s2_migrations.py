"""SIMP.S2 — what is LEFT of the one-time gated channel migrations: one property that outlived
all of them.

⚠ 0.0.18 lane D slice 4: THERE ARE NO MIGRATABLE CHANNELS. `vault` was the last, `migrate_channels`
is deleted, and `TestTheRemovedChannelsAreNoLongerMigratable` went with it — every assertion it
made was about a module that no longer exists. What that class MEANT ("a removed channel is refused
by name, never silently previewed as empty") is not lost: it moved to the two surfaces that now
answer for a removed channel, `mokata migrate` and `session_transport.make_transport`, and is
graded in `test_stage13_vault_slice.py`.

WARNING - 0.0.18 stage 10 (lane D slice 1): `obsidian` and `native-memory` are REMOVED, so they
are no longer migratable — the backends a migration would READ are gone, and a channel you cannot
read is not a channel you can move. Their round-trip tests went with them; what did NOT go is the
SECRET-SAFETY property they carried, which is re-pointed at a surviving channel below, and a pin
that the removed names are now refused by the channel list rather than silently accepted.

WARNING - 0.0.18 stage 11 (lane D slice 2): `memory-share` is REMOVED, for a DIFFERENT reason —
its backend was never missing. Its file is a 35b backup and `mokata memory import` reads it
through the identical `import_memory` call this module wrapped, so the migrator was a second route
to one destination and the surviving route is the one a user can find. `TestMemoryShareMigration`
went with it; what a repo that still asks for the channel is TOLD is graded in
`test_stage11_memory_share_channel.py`, which owns that surface.

⚠ AND THE SECRET-SAFETY PROPERTY MOVED A SECOND TIME. Stage 10 re-pointed it OFF obsidian and ONTO
memory-share; that host has now gone too. Re-pointing it again rather than letting it die with its
second host is the whole point of the exercise — see `TestMigrationSecretSafety` below for where
it lives now and why that is its real home rather than its third rental.
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
from mokata.memory import plan_memory_import
from mokata.memory.share import SHARE_KIND, SHARE_SCHEMA_VERSION


def _silent(_):
    pass


def _repo(d):
    init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
    return Surface.load(d)


def _manifest_path(d):
    return os.path.join(d, MOKATA_DIR, "manifest.json")


def _canonical_items(surface):
    store = MemoryStore.from_surface(surface)
    items = store.all_active()
    store.close()
    return {i.subject: i for i in items}


class TestMigrationSecretSafety(unittest.TestCase):
    """RE-POINTED TWICE, AND THIS TIME ONTO ITS ACTUAL OWNER.

    The property — a preview shows KEYS and never VALUES, so a credential sitting in an incoming
    item cannot reach a terminal — was written against the obsidian channel, re-pointed at stage 10
    onto memory-share, and would now die with THAT host if it were left where it was. It never
    belonged to any channel: it belongs to the PREVIEW of an untrusted incoming item set, and the
    one surviving preview of exactly that is `plan_memory_import` — the same data, read from the
    same file, previewed before the same gated restore that `_migrate_memory_share` used to wrap.

    ⚠ A property that has to move every time its host is deleted is a property that was pinned to
    the wrong thing twice. It is pinned to the mechanism now.

    The credential is ASSEMBLED from parts rather than written out: a literal one in the source
    trips mokata's own secret-guard hook, which is the feature working on its own test corpus."""

    def test_preview_shows_keys_not_values_and_no_credential(self):
        parts = ["postgres", "://", "user", ":", "pw", "@host/db"]
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            backup = os.path.join(d, MOKATA_DIR, "memory-share.json")
            data = {"schema_version": SHARE_SCHEMA_VERSION, "kind": SHARE_KIND,
                    "items": [MemoryItem.create("conn", "".join(parts),
                                                source="share", author="e").to_dict()]}
            with open(backup, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            store = MemoryStore.from_surface(surface)
            try:
                blob = plan_memory_import(store, data, source=backup).render()
            finally:
                store.close()
            self.assertIn("conn", blob)                  # the KEY is fine to show
            self.assertNotIn("://", blob)                # never the value
            self.assertNotIn("@host/db", blob)


if __name__ == "__main__":
    unittest.main()
