"""PRE-SIMP (0.0.15) — the re-export shims: every old import path resolves to the SAME object.

The stage extracts `memory/selection.py` from `memory/store.py` and splits `mcp/tools_write.py` into
per-domain modules. The contract is import-compat: every existing `from .store import X` /
`from ..memory.store import X` / `from mokata.mcp.tools_write import <tool>` caller keeps working
unchanged, because the origin modules re-export the moved objects. This pins that — a regression
that breaks a caller's import path fails HERE, loudly, not deep in an unrelated test.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import unittest

import _support  # noqa: F401 - puts src/ on the path


class TestStoreSelectionShim(unittest.TestCase):
    def test_selection_symbols_reexported_from_store_are_the_same_objects(self):
        from mokata.memory import selection, store
        for name in ("build_backend", "select_memory_backend", "_select_raw_backend",
                     "_overlay_for_team", "_scope_context_for", "_identity_and_access_for"):
            self.assertIs(getattr(store, name), getattr(selection, name),
                          f"store.{name} is not the same object as selection.{name}")

    def test_memory_dirname_constant_reexported(self):
        from mokata.memory import selection, store
        self.assertEqual(store.MEMORY_DIRNAME, "memory")
        self.assertEqual(store.MEMORY_DIRNAME, selection.MEMORY_DIRNAME)

    def test_memory_package_reexports_still_resolve(self):
        # `from mokata.memory import build_backend, select_memory_backend` (the memory __init__ API)
        from mokata.memory import build_backend, select_memory_backend
        from mokata.memory import selection
        self.assertIs(build_backend, selection.build_backend)
        self.assertIs(select_memory_backend, selection.select_memory_backend)

    def test_direct_store_import_paths_still_work(self):
        from mokata.memory.store import build_backend, select_memory_backend  # noqa: F401
        from mokata.memory.store import _identity_and_access_for  # noqa: F401
        from mokata.memory.store import MEMORY_DIRNAME  # noqa: F401


class TestToolsWriteAggregatorShim(unittest.TestCase):
    # tool -> the domain module it now lives in
    _MAP = {
        "remember": "tools_memory", "apply_proposal": "tools_memory",
        # 35b — the memory BACKUP surface re-homed from tools_share → tools_memory (it survives
        # SIMP.S3; only vault_push stays in the deletion set).
        "memory_export": "tools_memory", "memory_import": "tools_memory",
        "vault_push": "tools_share",
        "session_push": "tools_session", "session_pull": "tools_session",
        "session_name": "tools_session",
        "audit_share": "tools_team",
        "spec_emit": "tools_spec", "spec_amend": "tools_spec", "spec_check": "tools_spec",
        "import_stack": "tools_config", "reset": "tools_config", "init": "tools_config",
        "reconfigure": "tools_config", "config_set": "tools_config",
        "export_stack": "tools_config", "stacks_install": "tools_config",
    }

    def test_every_tool_reexported_from_tools_write_is_the_same_object(self):
        import importlib
        from mokata.mcp import tools_write as TW
        for tool, modname in self._MAP.items():
            mod = importlib.import_module(f"mokata.mcp.{modname}")
            self.assertIs(getattr(TW, tool), getattr(mod, tool),
                          f"tools_write.{tool} is not {modname}.{tool}")

    def test_all_is_byte_identical(self):
        from mokata.mcp import tools_write as TW
        self.assertEqual(TW.__all__, [
            "remember", "import_stack", "reset", "apply_proposal", "memory_export",
            "memory_import", "vault_push", "session_push", "session_pull", "session_name",
            "audit_share", "spec_check", "init", "reconfigure", "config_set", "export_stack",
            "stacks_install",
        ])

    def test_spec_emit_amend_are_attributes_even_though_not_in_all(self):
        # They were never in __all__ (so `import *` never exported them) but were reachable as
        # `tools_write.spec_emit`. That attribute access must still work post-split.
        from mokata.mcp import tools_spec
        from mokata.mcp import tools_write as TW
        self.assertNotIn("spec_emit", TW.__all__)
        self.assertIs(TW.spec_emit, tools_spec.spec_emit)
        self.assertIs(TW.spec_amend, tools_spec.spec_amend)

    def test_mcp_server_star_reexport_unchanged(self):
        # mcp_server does `from .mcp.tools_write import *` (uses __all__). remember is in __all__;
        # spec_emit is not — byte-identical to pre-split.
        import mokata.mcp_server as MS
        self.assertTrue(hasattr(MS, "remember"))
        self.assertFalse(hasattr(MS, "spec_emit"))


if __name__ == "__main__":
    unittest.main()
