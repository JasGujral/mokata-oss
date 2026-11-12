"""0.0.28 lane I1 (H8, `PLUGIN-PIN-UNRESOLVED`) — the release refuses to finish while the plugin's
pinned server does not resolve.

The plugin starts its server as `uvx … --from mokata[full]==X mokata-mcp`. When X had not reached
PyPI, every Claude Code user who installed or updated the plugin got a server that could not start,
and nothing in the release noticed. `release.sh` step 7b now runs `verify_plugin_pin_resolves`: the
plugin.json command itself, with `--refresh` and `--version`, must print exactly X.

The function is extracted from `scripts/release.sh` and run in bash against a FAKE `uvx` on PATH (no
network, no PyPI). `release.sh` is internal-only, so this module skips on the public mirror.
FIRING CONTROL (mutant): make the function `return 0` first — the unresolved pin passes.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RELEASE_SH = os.path.join(ROOT, "scripts", "release.sh")
BASH = shutil.which("bash")
VER = "9.9.9"


def _functions() -> str:
    """`say` and `verify_plugin_pin_resolves`, verbatim from release.sh."""
    text = open(RELEASE_SH, encoding="utf-8").read()
    say = re.search(r"^say\(\) \{.*?\}$", text, re.M | re.S).group(0)
    gate = re.search(r"^verify_plugin_pin_resolves\(\) \{.*?^\}$", text, re.M | re.S).group(0)
    return f"{say}\n{gate}\n"


@unittest.skipUnless(os.path.exists(RELEASE_SH), "release.sh is internal-only; absent on the mirror")
@unittest.skipUnless(BASH and os.name != "nt", "needs a POSIX bash")
class TestI1_PluginPinGate(unittest.TestCase):

    def _run(self, uvx_body: str, pin: str = f"mokata[full]=={VER}"):
        d = tempfile.mkdtemp(prefix="i1-pin-")
        self.addCleanup(shutil.rmtree, d, True)
        os.makedirs(os.path.join(d, ".claude-plugin"))
        os.makedirs(os.path.join(d, "bin"))
        with open(os.path.join(d, ".claude-plugin", "plugin.json"), "w", encoding="utf-8") as fh:
            json.dump({"mcpServers": {"mokata": {
                "command": "uvx", "args": ["--python", ">=3.12", "--from", pin, "mokata-mcp"]}}}, fh)
        uvx = os.path.join(d, "bin", "uvx")
        with open(uvx, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\necho \"$@\" > \"$(dirname \"$0\")/argv\"\n" + uvx_body + "\n")
        os.chmod(uvx, 0o755)
        script = _functions() + f'verify_plugin_pin_resolves "{d}" "{VER}"\n'
        env = dict(os.environ, PATH=os.path.join(d, "bin") + os.pathsep + os.environ["PATH"],
                   PYTHON=sys.executable)
        proc = subprocess.run([BASH, "-c", script], env=env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL)
        argv_file = os.path.join(d, "bin", "argv")
        argv = open(argv_file, encoding="utf-8").read().split() if os.path.exists(argv_file) else None
        return proc, argv

    def test_I1_a_pin_that_resolves_passes_and_ran_the_plugin_command(self):
        proc, argv = self._run(f"echo {VER}")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("plugin pin OK", proc.stdout)
        self.assertEqual(argv[0], "--refresh")
        self.assertIn(f"mokata[full]=={VER}", argv)
        self.assertEqual(argv[-1], "--version")

    def test_I1_a_pin_not_on_pypi_refuses(self):
        proc, _ = self._run("echo 'No solution found: mokata[full]==9.9.9' >&2; exit 1")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("REFUSING", proc.stderr)
        self.assertIn("No solution found", proc.stderr)

    def test_I1_a_resolver_serving_another_version_refuses(self):
        proc, _ = self._run("echo 0.0.27")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("does not resolve to 9.9.9", proc.stderr)

    def test_I1_a_plugin_json_pinning_another_version_refuses_without_running_it(self):
        proc, argv = self._run(f"echo {VER}", pin="mokata[full]==0.0.27")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("does not pin mokata[full]==9.9.9", proc.stderr)
        self.assertIsNone(argv, "the resolver ran although the pin was wrong")

    def test_I1_release_sh_calls_the_gate_and_stops_on_it(self):
        text = open(RELEASE_SH, encoding="utf-8").read()
        self.assertRegex(text, r'verify_plugin_pin_resolves "\$REPO_ROOT" "\$VER" \|\| exit 1')


@unittest.skipUnless(os.name != "nt", "the fake uvx is a POSIX shell script")
class TestI1_DoctorNamesAnUnresolvedPin(unittest.TestCase):
    """`mokata doctor --plugin` names the cause when the pinned release is not on the index."""

    def _line(self, uvx_body, resolved_ok=False):
        from unittest import mock
        from mokata.cli_commands import diagnostics as D
        d = tempfile.mkdtemp(prefix="i1-doc-")
        self.addCleanup(shutil.rmtree, d, True)
        uvx = os.path.join(d, "uvx")
        with open(uvx, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\n" + uvx_body + "\n")
        os.chmod(uvx, 0o755)
        check = mock.Mock(label="package resolved", ok=resolved_ok)
        rep = mock.Mock(checks=[check])
        with mock.patch("mokata.plugin_runtime.find_uvx", return_value=uvx), \
                mock.patch("mokata.plugin_runtime._plugin_manifest", return_value={}), \
                mock.patch("mokata.plugin_runtime._manifest_pin",
                           return_value=f"mokata[full]=={VER}"):
            return D._pin_does_not_resolve(d, rep)

    def test_I1_an_unpublished_pin_is_named_with_its_fix(self):
        line = self._line("echo 'No solution found when resolving: no version of "
                          "mokata[full]==9.9.9' >&2; exit 1")
        self.assertIn(f"pinned `mokata[full]=={VER}` does not resolve", line)
        self.assertIn("wait for the release to reach PyPI", line)

    def test_I1_a_healthy_route_does_no_probe(self):
        self.assertIsNone(self._line("exit 99", resolved_ok=True))

    def test_I1_another_failure_is_left_to_its_own_message(self):
        self.assertIsNone(self._line("echo 'network unreachable' >&2; exit 2"))


if __name__ == "__main__":
    unittest.main()
