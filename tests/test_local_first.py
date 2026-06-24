"""K4 — local-first / no-telemetry: enforced and explicitly stated.

The minimal profile must perform zero network egress. We prove it by running the
whole minimal-profile spine path inside a guard that turns ANY outbound socket into
a loud error, and by asserting minimal wires no network-capable tool at all.
"""

import os
import socket
import tempfile
import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.config import Surface
from mokata.bootstrap import build_bootstrap
from mokata.detect import Detector
from mokata.init import init_repo
from mokata.manifest import Manifest
from mokata.netguard import NetworkEgressBlocked, network_capable_tools, no_network
from mokata.profiles import build_manifest_data
from mokata.router import Router


def silent(_):
    pass


class TestNoNetworkGuard(unittest.TestCase):
    """The guard itself must actually block — otherwise the egress proof is vacuous."""

    def test_guard_blocks_create_connection(self):
        with no_network():
            with self.assertRaises(NetworkEgressBlocked):
                socket.create_connection(("198.51.100.1", 80), timeout=0.1)

    def test_guard_blocks_socket_connect(self):
        with no_network():
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                with self.assertRaises(NetworkEgressBlocked):
                    s.connect(("198.51.100.1", 80))
            finally:
                s.close()

    def test_guard_is_restored_afterwards(self):
        before = socket.socket.connect
        with no_network():
            pass
        self.assertIs(socket.socket.connect, before)


class TestMinimalZeroEgress(unittest.TestCase):
    def test_minimal_profile_full_path_makes_no_connection(self):
        with tempfile.TemporaryDirectory() as d:
            with no_network():
                # init -> load -> route -> bootstrap, all under the egress guard.
                init_repo(root=d, profile="minimal", assume_yes=True, out=silent)
                surface = Surface.load(d)
                resolutions = surface.router.resolve_all()
                briefing = build_bootstrap(surface)
            # Minimal wires no capabilities, so nothing to resolve.
            self.assertEqual(resolutions, [])
            self.assertTrue(briefing.within_budget)

    def test_minimal_wires_no_network_capable_tool(self):
        m = Manifest.from_dict(build_manifest_data("minimal", "0.1.0"))
        self.assertEqual(network_capable_tools(m), [])


class TestNetworkCapableToolsAreExplicit(unittest.TestCase):
    def test_standard_is_local_only_by_default(self):
        # Standard's lean defaults (grep/ripgrep + sqlite) never leave the machine.
        m = Manifest.from_dict(build_manifest_data("standard", "0.1.0"))
        self.assertEqual(network_capable_tools(m), [])

    def test_full_surfaces_its_network_capable_tools_explicitly(self):
        # Egress is possible only because the user opted into full's external/mcp tools.
        m = Manifest.from_dict(build_manifest_data("full", "0.1.0"))
        net = set(network_capable_tools(m))
        self.assertIn("code-review-graph", net)   # mcp
        self.assertIn("native-memory", net)       # external
        self.assertNotIn("grep", net)             # builtin stays local
        self.assertNotIn("sqlite", net)           # library stays local

    def test_disabled_network_tool_is_not_counted(self):
        data = build_manifest_data("full", "0.1.0")
        data["tools"]["code-review-graph"]["enabled"] = False
        m = Manifest.from_dict(data)
        self.assertNotIn("code-review-graph", network_capable_tools(m))


if __name__ == "__main__":
    unittest.main()
