"""SIMP.S2 — shim parity + MANIFEST PARITY (Jas 2026-07-15) + Neo4j WARN.

The deprecated channels keep working EXACTLY as today; the ONLY new behaviour is a once-per-repo
warn. A committed manifest that lists `native-memory`/`obsidian` must still RESOLVE (never
silently vanish) and must WARN (never silently swap to a different backend without naming the
swap-to-come). A canonical-only repo (sqlite/postgres) sees ZERO new output — byte-identical.
"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest import mock

import _support  # noqa: F401

from mokata import MOKATA_DIR, TEMP_LOCAL_DIRNAME
from mokata.config import Surface
from mokata.init import init_repo
from mokata.memory import MemoryItem, MemoryStore
from mokata import profiles


def _silent(_):
    pass


def _repo(d, profile="standard"):
    init_repo(root=d, profile=profile, assume_yes=True, out=_silent)
    return Surface.load(d)


def _manifest_path(d):
    return os.path.join(d, MOKATA_DIR, "manifest.json")


def _set_memory_chain(d, chain, tools=None):
    """Rewrite the committed memory_store fallback chain (+ ensure its tools exist)."""
    p = _manifest_path(d)
    with open(p, encoding="utf-8") as fh:
        data = json.load(fh)
    data["capabilities"]["memory_store"]["fallback"] = list(chain)
    for tid in chain:
        data.setdefault("tools", {}).setdefault(
            tid, dict(profiles.TOOL_CATALOG[tid], enabled=True))
    if tools:
        data["tools"].update(tools)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
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

    def test_committed_obsidian_chain_warns_once_and_reads_still_work(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            surface = _set_memory_chain(d, ["obsidian", "sqlite"])
            # Shim parity: a write→read round-trips through the deprecated chain unperturbed —
            # whichever backend resolves, the store keeps working EXACTLY as today (the only new
            # thing is the warn). The warn fires (once) on the first store build here.
            st = MemoryStore.from_surface(surface)
            st.remember(MemoryItem.create("k", "v", source="t", author="t"), assume_yes=True)
            st.close()

            items, err = self._read_with_stderr(Surface.load(d))
            self.assertTrue(any(i.subject == "k" for i in items))   # reads unperturbed (parity)
            self.assertEqual(err, "")                               # already warned once — silent

            # …and the very first build DID warn, naming the migration.
            with tempfile.TemporaryDirectory() as d2:
                _repo(d2)
                s2 = _set_memory_chain(d2, ["obsidian", "sqlite"])
                _i, err2 = self._read_with_stderr(s2)
                self.assertIn("0.0.17", err2)
                self.assertIn("mokata migrate obsidian", err2)

    def test_committed_native_memory_chain_warns(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            surface = _set_memory_chain(d, ["native-memory", "sqlite"])
            _items, err = self._read_with_stderr(surface)
            self.assertIn("native-memory", err)
            self.assertIn("0.0.17", err)

    def test_canonical_only_repo_is_byte_identical_no_warn(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)          # standard profile — memory_store chain is ["sqlite"]
            _items, err = self._read_with_stderr(surface)
            self.assertEqual(err, "", "a canonical-only repo must emit ZERO new output")
            self.assertFalse(os.path.isdir(_marker_dir(d)),
                             "a canonical-only repo must write NO deprecation marker")


class TestManifestParity(unittest.TestCase):
    def test_catalog_marks_deprecated_providers(self):
        for tid in ("native-memory", "obsidian", "neo4j"):
            self.assertEqual(profiles.TOOL_CATALOG[tid].get("deprecated"), "0.0.17",
                             f"{tid} must be marked deprecated in the catalog")

    def test_canonical_providers_not_marked_deprecated(self):
        for tid in ("sqlite", "postgres", "ast", "grep", "ripgrep"):
            self.assertNotIn("deprecated", profiles.TOOL_CATALOG[tid])

    def test_fallback_chain_still_lists_deprecated_providers(self):
        # MANIFEST PARITY: they must NOT silently vanish from resolution.
        chain = profiles.CAPABILITY_FALLBACKS["memory_store"]["fallback"]
        self.assertIn("native-memory", chain)
        self.assertIn("obsidian", chain)
        self.assertIn("sqlite", chain)

    def test_full_and_custom_profiles_still_wire_deprecated_providers(self):
        for prof in ("full", "custom"):
            chain = profiles.PROFILES[prof]["capabilities"]["memory_store"]
            self.assertIn("native-memory", chain)
            self.assertIn("obsidian", chain)

    def test_built_manifest_lists_and_marks_deprecated(self):
        data = profiles.build_manifest_data("full", "0.0.15")
        chain = data["capabilities"]["memory_store"]["fallback"]
        self.assertIn("native-memory", chain)
        self.assertIn("obsidian", chain)
        self.assertEqual(data["tools"]["obsidian"].get("deprecated"), "0.0.17")
        self.assertEqual(data["tools"]["native-memory"].get("deprecated"), "0.0.17")


class TestNeo4jWarn(unittest.TestCase):
    def test_selecting_neo4j_warns_once_and_still_degrades_to_floor(self):
        from mokata.knowledge.layer import select_backends, GrepBackend
        # a fake router that resolves code_graph to neo4j (test_stage35f pattern)
        from test_stage35f_graph_adapter import _Router  # noqa: F401  (bare: unittest -t tests)
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, MOKATA_DIR))
            with mock.patch.dict(os.environ, {}, clear=True):
                buf = io.StringIO()
                with redirect_stderr(buf):
                    primary, _fb = select_backends(_Router("neo4j"), root=d)
                self.assertIsInstance(primary, GrepBackend)     # behaviour unchanged (grep floor)
                self.assertIn("Neo4j", buf.getvalue())
                self.assertIn("0.0.17", buf.getvalue())
                # once per repo
                buf2 = io.StringIO()
                with redirect_stderr(buf2):
                    select_backends(_Router("neo4j"), root=d)
                self.assertEqual(buf2.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
