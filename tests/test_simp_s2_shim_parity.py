"""SIMP.S2 — shim parity + MANIFEST PARITY (Jas 2026-07-15).

The STILL-deprecated channels keep working EXACTLY as today; the ONLY new behaviour is a
once-per-repo warn. A canonical-only repo (sqlite/postgres) sees ZERO new output — byte-identical.

⚠ REVERSED AT 0.0.18 STAGE 10 (lane D slice 1), and reversed rather than deleted. Four pins here
asserted that a committed manifest naming `native-memory`/`obsidian` must still RESOLVE and that
`TOOL_CATALOG` must still carry them. Those were TRUE while the backends existed and are FALSE
now — deleting them would have left the removal ungraded on the surfaces that matter most (the
catalog `init_repo` writes to a user's disk, and the resolution path a committed chain takes), and
BUMPING them would have been the stage-9 mistake one release later. Each is now the opposite
assertion about the same surface, so the file still grades the thing it always graded.

⚠ AND REVERSED AGAIN AT 0.0.18 STAGE 14, for the last provider. `TestNeo4jWarn` graded the
once-per-repo DEPRECATION warn on `select_backends`; there is no deprecated provider left to warn
about, and the behaviour that replaced it — a committed `code_graph` chain that still names a
REMOVED provider is announced once and answered from the AST floor, never from the lexical floor
under the removed name — is graded in `test_stage14_neo4j_removal.py`, where the whole refusal
lives. The manifest-parity pins below became DERIVED in the same edit: they ranged over
`memory_store` chains only, which was exactly right while every removed channel was a memory
backend and silently vacuous for `neo4j` the moment one was not.
"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest import mock

import _support  # noqa: F401
from _removed_channel_fixture import plant_removed_chain

from mokata import MOKATA_DIR, TEMP_LOCAL_DIRNAME
from mokata.config import Surface
from mokata.init import init_repo
from mokata.memory import MemoryItem, MemoryStore
from mokata import profiles
from mokata.deprecation import REMOVAL_RELEASE, REMOVED


def _silent(_):
    pass


def _repo(d, profile="standard"):
    init_repo(root=d, profile=profile, assume_yes=True, out=_silent)
    return Surface.load(d)


def _manifest_path(d):
    return os.path.join(d, MOKATA_DIR, "manifest.json")


def _set_memory_chain(d, chain, tools=None):
    """Rewrite the committed memory_store fallback chain (+ ensure its tools exist).

    A chain naming a REMOVED provider cannot be built from the live catalog any more, so the
    legacy blocks are PLANTED (§7i) — see `_removed_channel_fixture`."""
    plant_removed_chain(d, chain, tool_blocks=tools)
    return Surface.load(d)


def _marker_dir(d):
    return os.path.join(d, MOKATA_DIR, TEMP_LOCAL_DIRNAME, "deprecations")


class TestMemoryShimWarns(unittest.TestCase):
    def _read_with_stderr(self, surface):
        buf = io.StringIO()
        with redirect_stderr(buf):
            store = MemoryStore.from_surface(surface)
            items = store.all_active()
            store.close()
        return items, buf.getvalue()

    def test_committed_obsidian_chain_with_NO_data_is_loud_and_reads_still_work(self):
        # REVERSED (stage 10): this asserted the deprecated chain resolved and WARNED. The backend
        # is gone, so the chain now resolves PAST it — and the whole point of the slice is that it
        # does not do so in silence. With nothing behind it that is a stale config line, not data
        # loss, so it is a LOUD notice and the read still works. (The data case RAISES; that is
        # `test_stage10_removed_backends.py`, which is where the fixture with notes in it lives.)
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            surface = _set_memory_chain(d, ["obsidian", "sqlite"])
            buf = io.StringIO()
            with redirect_stderr(buf):
                st = MemoryStore.from_surface(surface)
                st.remember(MemoryItem.create("k", "v", source="t", author="t"), assume_yes=True)
                st.close()
            err = buf.getvalue()
            self.assertIn("obsidian", err)
            self.assertIn("REMOVED", err)
            self.assertIn(REMOVAL_RELEASE, err)

            # …ONCE (the second read is silent, exactly as the deprecation warn was), and the
            # write→read round-trip is unperturbed — the SQLite floor really did serve it.
            items, err2 = self._read_with_stderr(Surface.load(d))
            self.assertTrue(any(i.subject == "k" for i in items))
            self.assertEqual(err2, "")

    def test_canonical_only_repo_is_byte_identical_no_warn(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)          # standard profile — memory_store chain is ["sqlite"]
            _items, err = self._read_with_stderr(surface)
            self.assertEqual(err, "", "a canonical-only repo must emit ZERO new output")
            self.assertFalse(os.path.isdir(_marker_dir(d)),
                             "a canonical-only repo must write NO deprecation marker")


class TestManifestParity(unittest.TestCase):
    """REVERSED at stage 10. Every assertion here used to say "the deprecated providers must NOT
    vanish"; each now says its subject is gone, on the same surface, so the removal is graded
    exactly where the shim was."""

    def test_catalog_marks_nothing_deprecated_because_nothing_is(self):
        # REVERSED at stage 14 (it asserted `TOOL_CATALOG["neo4j"]["deprecated"]`). `neo4j` was the
        # only marked entry and it is gone, so the honest assertion is that NO entry carries the
        # marker — which is a real statement about the dict `init_repo` writes to a user's disk,
        # where "assert the one marked entry" would now be an assertion about nothing.
        marked = {tid for tid, tool in profiles.TOOL_CATALOG.items() if "deprecated" in tool}
        self.assertEqual(marked, set(),
                         "a catalog entry is marked deprecated but `deprecation.CHANNELS` is "
                         "empty — the two must agree or the manifest on disk says more than the "
                         "registry does")
        from mokata import deprecation as _dep
        self.assertEqual(_dep.CHANNELS, {})

    def test_catalog_no_longer_carries_the_REMOVED_providers(self):
        # ★ THE DISK SURFACE. `init_repo` copies TOOL_CATALOG into `.mokata/manifest.json`, so an
        # entry left here would advertise a removed backend inside every repo created from now on.
        for tid in REMOVED:
            self.assertNotIn(tid, profiles.TOOL_CATALOG,
                             f"{tid} is REMOVED but the catalog still writes it to disk")

    def test_canonical_providers_not_marked_deprecated(self):
        for tid in ("sqlite", "postgres", "ast", "grep", "ripgrep"):
            self.assertNotIn("deprecated", profiles.TOOL_CATALOG[tid])

    def test_fallback_chain_no_longer_lists_the_removed_providers(self):
        # ⚠ EVERY CAPABILITY, NOT `memory_store`. This read one chain while every removed channel
        # was a memory backend; `neo4j` is a `code_graph` channel, so at stage 14 the loop would
        # have gone on passing while saying nothing about the chain the removal is actually in.
        for need, spec in profiles.CAPABILITY_FALLBACKS.items():
            for tid in REMOVED:
                self.assertNotIn(tid, spec["fallback"], f"{need} still declares {tid}")
        self.assertIn("sqlite", profiles.CAPABILITY_FALLBACKS["memory_store"]["fallback"])
        self.assertIn("grep", profiles.CAPABILITY_FALLBACKS["code_graph"]["fallback"])
        self.assertIn("ast", profiles.CAPABILITY_FALLBACKS["code_graph"]["fallback"])

    def test_no_profile_wires_a_removed_provider(self):
        for prof, spec in profiles.PROFILES.items():
            for need, chain in spec["capabilities"].items():
                for tid in REMOVED:
                    self.assertNotIn(tid, chain, f"profile {prof} still wires {tid} for {need}")

    def test_built_manifest_neither_lists_nor_marks_a_removed_provider(self):
        data = profiles.build_manifest_data("full", "0.0.15")
        for need, cap in data["capabilities"].items():
            for tid in REMOVED:
                self.assertNotIn(tid, cap["fallback"], f"{need} chain still names {tid}")
        for tid in REMOVED:
            self.assertNotIn(tid, data["tools"])
        self.assertIn("sqlite", data["capabilities"]["memory_store"]["fallback"])
        self.assertIn("ast", data["capabilities"]["code_graph"]["fallback"])


if __name__ == "__main__":
    unittest.main()
