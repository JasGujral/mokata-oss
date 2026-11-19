"""0.0.28 lane I1 (H8, `PLUGIN-PIN-UNRESOLVED`) — the release refuses to finish while the plugin's
pinned server does not resolve.

The plugin starts its server as `uvx … --from mokata[full]==X mokata-mcp`. When X had not reached
PyPI, every Claude Code user who installed or updated the plugin got a server that could not start,
and nothing in the release noticed. `release.sh` step 7b now runs `verify_plugin_pin_resolves`: the
plugin.json command itself, with `--refresh` and `--version`, must print exactly X.

0.0.29 D2 (`PLUGIN-PIN-CHECK-NO-WAIT`): 7b WAITS for it, in a stated window — see `ThePinWaits`.

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


def _fn(text, name):
    return re.search(r"^%s\(\) \{.*?^\}$" % re.escape(name), text, re.M | re.S).group(0)


def _functions() -> str:
    """`say`, `plugin_pin_decision` and `verify_plugin_pin_resolves`, verbatim from release.sh —
    then two STUBS the test controls: `sleep` (records the poll, takes no time: the 1800 s window is
    walked on a fake clock, never shortened) and `read_release_run` (answers from a file)."""
    text = open(RELEASE_SH, encoding="utf-8").read()
    say = re.search(r"^say\(\) \{.*?\}$", text, re.M | re.S).group(0)
    stubs = ('sleep() { printf "%s\\n" "$1" >> "$I1_DIR/sleeps"; }\n'
             'read_release_run() { cat "$I1_DIR/run" 2>/dev/null || printf "UNREADABLE\\t\\t\\n"; }\n')
    return "\n".join((say, _fn(text, "plugin_pin_decision"),
                      _fn(text, "verify_plugin_pin_resolves"), stubs))


def _run_pin(tc, uvx_body: str, pin: str = f"mokata[full]=={VER}", run=None):
    """Runs the extracted 7b gate against a fake `uvx`, a fake clock and a fake run state.
    A module function, not a mixin: only the two guarded classes below call it, so the read of
    release.sh sits under their `skipUnless(os.path.exists(RELEASE_SH))` (s28 / b1 census)."""
    d = tempfile.mkdtemp(prefix="i1-pin-")
    tc.addCleanup(shutil.rmtree, d, True)
    os.makedirs(os.path.join(d, ".claude-plugin"))
    os.makedirs(os.path.join(d, "bin"))
    with open(os.path.join(d, ".claude-plugin", "plugin.json"), "w", encoding="utf-8") as fh:
        json.dump({"mcpServers": {"mokata": {
            "command": "uvx", "args": ["--python", ">=3.12", "--from", pin, "mokata-mcp"]}}}, fh)
    if run is not None:
        with open(os.path.join(d, "run"), "w", encoding="utf-8") as fh:
            fh.write("\t".join(run) + "\n")
    uvx = os.path.join(d, "bin", "uvx")
    with open(uvx, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\nhere=\"$(dirname \"$0\")\"\necho \"$@\" > \"$here/argv\"\n"
                 "n=$(cat \"$here/n\" 2>/dev/null || echo 0); n=$((n + 1)); echo $n > \"$here/n\"\n"
                 + uvx_body + "\n")
    os.chmod(uvx, 0o755)
    script = _functions() + f'verify_plugin_pin_resolves "{d}" "{VER}" "o/mirror" "v{VER}"\n'
    env = dict(os.environ, PATH=os.path.join(d, "bin") + os.pathsep + os.environ["PATH"],
               PYTHON=sys.executable, I1_DIR=d)
    proc = subprocess.run([BASH, "-c", script], env=env, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL)

    def _read(path):
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    argv = _read(os.path.join(d, "bin", "argv"))
    tc.tries = int(_read(os.path.join(d, "bin", "n")) or 0)
    tc.sleeps = (_read(os.path.join(d, "sleeps")) or "").split()
    return proc, (argv.split() if argv is not None else None)


@unittest.skipUnless(os.path.exists(RELEASE_SH), "release.sh is internal-only; absent on the mirror")
@unittest.skipUnless(BASH and os.name != "nt", "needs a POSIX bash")
class TestI1_PluginPinGate(unittest.TestCase):
    def _run(self, *args, **kw):
        return _run_pin(self, *args, **kw)

    def test_I1_a_pin_that_resolves_passes_and_ran_the_plugin_command(self):
        proc, argv = self._run(f"echo {VER}")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("plugin pin OK", proc.stdout)
        self.assertEqual(argv[0], "--refresh")
        self.assertIn(f"mokata[full]=={VER}", argv)
        self.assertEqual(argv[-1], "--version")
        self.assertEqual(self.sleeps, [])

    def test_I1_a_pin_not_on_pypi_refuses_at_the_window(self):
        proc, _ = self._run("echo 'No solution found: mokata[full]==9.9.9' >&2; exit 1",
                            run=("SUCCEEDED", "77", "success"))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("REFUSING", proc.stderr)
        self.assertIn("No solution found", proc.stderr)

    def test_I1_a_resolver_serving_another_version_refuses(self):
        proc, _ = self._run("echo 0.0.27", run=("SUCCEEDED", "77", "success"))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("did not resolve to 9.9.9 within the 1800s window", proc.stderr)

    def test_I1_a_plugin_json_pinning_another_version_refuses_without_running_it(self):
        proc, argv = self._run(f"echo {VER}", pin="mokata[full]==0.0.27")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("does not pin mokata[full]==9.9.9", proc.stderr)
        self.assertIsNone(argv, "the resolver ran although the pin was wrong")
        self.assertEqual(self.sleeps, [], "a wrong pin is a refusal, never a 30-minute wait")

    def test_I1_release_sh_calls_the_gate_and_stops_on_it(self):
        text = open(RELEASE_SH, encoding="utf-8").read()
        self.assertRegex(text, r'verify_plugin_pin_resolves "\$REPO_ROOT" "\$VER" "\$PUB_REPO" '
                               r'"\$TAG" \|\| exit 1')


@unittest.skipUnless(os.path.exists(RELEASE_SH), "release.sh is internal-only; absent on the mirror")
@unittest.skipUnless(BASH and os.name != "nt", "needs a POSIX bash")
class ThePinWaits(unittest.TestCase):
    """0.0.29 D2 `PLUGIN-PIN-CHECK-NO-WAIT`: step 7b WAITS, in a stated window (1800 s, a try every
    20 s), for the pin to resolve; a stale index is a wait, never a refusal. A FAILED release.yml
    refuses at once; PENDING-APPROVAL / RUNNING at the window's end exit 3 (re-run the same command).
    FIRING CONTROLS: `plugin_pin_decision` refusing before the window; the FAILED arm removed."""

    def _run(self, *args, **kw):
        return _run_pin(self, *args, **kw)

    WINDOW_POLLS = 1800 // 20

    def test_a_pin_that_resolves_on_the_fourth_try_returns_0_after_three_polls(self):
        proc, _ = self._run(f'if [ "$n" -ge 4 ]; then echo {VER}; else echo stale >&2; exit 1; fi',
                            run=("RUNNING", "77", ""))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.tries, 4)
        self.assertEqual(self.sleeps, ["20", "20", "20"])
        self.assertIn("try 4, after 60s", proc.stdout)
        self.assertIn("waiting up to 1800s (a try every 20s)", proc.stdout)

    def test_never_resolving_refuses_only_when_the_window_ends_naming_the_run_state(self):
        proc, _ = self._run("echo stale >&2; exit 1", run=("SUCCEEDED", "77", "success"))
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(len(self.sleeps), self.WINDOW_POLLS)
        self.assertEqual(self.tries, self.WINDOW_POLLS + 1)
        self.assertIn("1800s window", proc.stderr)
        self.assertIn("was SUCCEEDED (run 77)", proc.stderr)
        self.assertIn("PUBLISHED, yet the index still does not serve the pin", proc.stderr)
        self.assertEqual(proc.stderr.count("stale"), 1, "the stderr tail is printed once, at the end")

    def test_an_unreadable_run_is_not_called_a_failed_publish(self):
        proc, _ = self._run("exit 1")                          # no run file -> UNREADABLE
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(len(self.sleeps), self.WINDOW_POLLS)
        self.assertIn("was UNREADABLE", proc.stderr)
        self.assertIn("NOT a statement that the publish failed", proc.stderr)

    def test_a_failed_release_run_refuses_at_once_with_the_run_id(self):
        proc, _ = self._run("exit 1", run=("FAILED", "4242", "failure"))
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(self.sleeps, [], "a failed publish was waited out")
        self.assertIn("release.yml run 4242", proc.stderr)
        self.assertIn("concluded 'failure'", proc.stderr)
        self.assertIn("--log-failed", proc.stderr)

    def test_pending_approval_waits_says_so_once_then_exits_3(self):
        proc, _ = self._run("exit 1", run=("PENDING-APPROVAL", "88", ""))
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertEqual(len(self.sleeps), self.WINDOW_POLLS)
        self.assertEqual(proc.stdout.count("WAITING for a human to approve the 'pypi'"), 1)
        self.assertIn("Approve it:", proc.stderr)
        self.assertIn("Re-run the SAME command", proc.stderr)

    def test_a_still_running_publish_exits_3_not_1(self):
        proc, _ = self._run("exit 1", run=("RUNNING", "89", ""))
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIn("still RUNNING", proc.stderr)

    def test_the_decision_is_pure_and_total(self):
        decision = _fn(open(RELEASE_SH, encoding="utf-8").read(), "plugin_pin_decision")
        rows = (("0", VER, "SUCCEEDED", "0", "OK"), ("0", VER, "FAILED", "0", "OK"),
                ("1", "", "FAILED", "0", "REFUSE"), ("0", "0.0.1", "FAILED", "40", "REFUSE"),
                ("1", "", "RUNNING", "0", "RETRY"), ("1", "", "UNREADABLE", "1780", "RETRY"),
                ("1", "", "PENDING-APPROVAL", "1800", "PENDING"),
                ("1", "", "RUNNING", "1800", "PENDING"),
                ("1", "", "SUCCEEDED", "1800", "REFUSE"), ("1", "", "UNREADABLE", "1800", "REFUSE"),
                ("1", "", "NO-RUN", "1800", "REFUSE"), ("1", "", "UNREAD", "1820", "REFUSE"))
        for rc, printed, state, waited, want in rows:
            with self.subTest(state=state, waited=waited, rc=rc):
                out = subprocess.run(
                    [BASH, "-c", f"{decision}\nplugin_pin_decision '{rc}' '{printed}' '{VER}' "
                                 f"'{state}' '{waited}' 1800"],
                    capture_output=True, text=True, stdin=subprocess.DEVNULL)
                self.assertEqual(out.stdout.strip(), want, out.stderr)


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
