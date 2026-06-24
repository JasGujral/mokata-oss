"""H4 — MCP registry + discovery: enumerate MCP servers and map them to stack roles;
degrade cleanly when none are present."""

import json
import os
import tempfile
import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.adapters import MCPRegistry, MCPServer, discover_mcp_servers


CONFIG = [
    {"name": "code-review-graph", "provides": ["code_graph"], "command": "crg"},
    {"name": "memory-mcp", "provides": ["memory_store"], "command": "mem"},
]


class TestDiscovery(unittest.TestCase):
    def test_enumerates_from_config(self):
        servers = discover_mcp_servers(config=CONFIG)
        self.assertEqual({s.name for s in servers},
                         {"code-review-graph", "memory-mcp"})

    def test_enumerates_from_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "mcp.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(CONFIG, fh)
            servers = discover_mcp_servers(path=path)
            self.assertEqual(len(servers), 2)

    def test_degrades_cleanly_when_none_present(self):
        self.assertEqual(discover_mcp_servers(), [])           # nothing -> empty
        reg = MCPRegistry.discover()
        self.assertEqual(reg.map_to_roles(), {})               # no error


class TestRegistry(unittest.TestCase):
    def test_maps_servers_to_roles(self):
        reg = MCPRegistry.discover(config=CONFIG)
        roles = reg.map_to_roles()
        self.assertEqual(roles["code_graph"], ["code-review-graph"])
        self.assertEqual(roles["memory_store"], ["memory-mcp"])

    def test_servers_for_capability(self):
        reg = MCPRegistry.discover(config=CONFIG)
        self.assertEqual([s.name for s in reg.servers_for("code_graph")],
                         ["code-review-graph"])

    def test_servers_are_adapters(self):
        reg = MCPRegistry.discover(config=CONFIG)
        adapters = reg.adapters()
        self.assertTrue(all(a.kind == "mcp" for a in adapters))
        self.assertIn("code_graph", adapters[0].provides + adapters[1].provides)


if __name__ == "__main__":
    unittest.main()
