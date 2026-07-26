"""PRE-SIMP (0.0.15) — the MCP tool registry is byte-identical after the tools_write split.

`mcp/tools_write.py` was split into per-domain modules; registration stays registry-driven and the
aggregator registers each tool into the shared `TOOLS` list in the EXACT historical order. This pins
the recorded PRE-STAGE snapshot: the server's registered tool set + ORDER (which `build_server`
iterates verbatim) is unchanged, with no duplicates and no drops.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import unittest

import _support  # noqa: F401 - puts src/ on the path

# The recorded PRE-STAGE snapshot (captured from the single-file tools_write.py before the split):
# the full ordered tool set the server registers (read tools, then the 19 write tools, then approve).
SNAPSHOT_FULL = [
    'query', 'recall', 'doctor', 'coverage', 'budget', 'audit', 'status', 'preview', 'progress',
    'lanes', 'watch', 'govern', 'rules', 'skills', 'suggest', 'lat_check', 'index_status', 'tour',
    'ci_check', 'baseline', 'sessions', 'session_windows', 'session_save', 'plan_list', 'plan_show',
    'session_list', 'config_get', 'export_preview', 'decompose', 'vault_list', 'vault_search',
    'vault_pull', 'stacks_list', 'stacks_search', 'stacks_show',
    'remember', 'import_stack', 'reset', 'apply_proposal', 'memory_export', 'memory_import',
    'vault_push', 'session_push', 'session_pull', 'session_name', 'audit_share', 'spec_emit',
    'spec_amend', 'spec_check', 'init', 'reconfigure', 'config_set', 'export_stack',
    'stacks_install', 'approve',
]
SNAPSHOT_WRITE = [
    'remember', 'import_stack', 'reset', 'apply_proposal', 'memory_export', 'memory_import',
    'vault_push', 'session_push', 'session_pull', 'session_name', 'audit_share', 'spec_emit',
    'spec_amend', 'spec_check', 'init', 'reconfigure', 'config_set', 'export_stack',
    'stacks_install',
]


class TestToolRegistryParity(unittest.TestCase):
    def test_full_registered_order_matches_snapshot(self):
        from mokata.mcp.registry import tool_names
        self.assertEqual(tool_names(), SNAPSHOT_FULL)

    def test_write_registered_order_matches_snapshot(self):
        from mokata.mcp.registry import write_tool_names
        self.assertEqual(write_tool_names(), SNAPSHOT_WRITE)

    def test_no_duplicate_registrations(self):
        from mokata.mcp.registry import tool_names
        names = tool_names()
        self.assertEqual(len(names), len(set(names)),
                         "a tool was registered twice by the aggregator")

    def test_build_server_registers_the_same_ordered_set(self):
        # `build_server` iterates TOOLS in order; the registered spec names are exactly the snapshot.
        from mokata.mcp.registry import TOOLS
        self.assertEqual([t.name for t in TOOLS], SNAPSHOT_FULL)


if __name__ == "__main__":
    unittest.main()
