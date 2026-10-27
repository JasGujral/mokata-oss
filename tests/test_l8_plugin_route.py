"""0.0.26 L8 — MP.S1 / G-1: the plugin route works with two in-app commands and no pip step.

A stranger with Claude Code and uv runs `/plugin marketplace add JasGujral/mokata-oss` and
`/plugin install mokata@mostack`. What must be true (doc 86's launch exit):

  * the MCP server, the hooks and the CLI launcher all run the plugin's OWN version — the pinned
    `mokata[full]==<plugin version>` uvx environment (recorded by the MCP server for the hooks);
  * no uv → the first session says how to get it, one line per OS; first start → its cost is said;
  * `/mokata:doctor` reports uv · package · version match · MCP running · hooks firing;
  * `/mokata:review-window` (the IDE) runs from the plugin route through the same launcher;
  * the plugin route reaches the one-line install's end state with ONE pinned command
    (`setup claude --mode full --plugin`) that writes no second copy of the harness wiring.

The real end-to-end (claude CLI install + MCP handshake + hooks + doctor) is the CI job
`.github/workflows/plugin-route.yml` running `scripts/plugin_route_smoke.py`.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import _support  # noqa: F401

import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from mokata import __version__
from mokata import plugin_runtime as PR

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "src" / "mokata" / "hooks"
SHIM = HOOKS / "mokata-hook-launch"
LAUNCHER = HOOKS / "mokata-launch"
SH = shutil.which("sh")
needs_sh = unittest.skipUnless(SH, "POSIX sh not available")
FAKE_SECRET = "AKIAIOSFODNN7EXAMPLE"


def _tmp(test, prefix="mk-l8-"):
    d = tempfile.mkdtemp(prefix=prefix)
    test.addCleanup(shutil.rmtree, d, True)
    return d


def _exe(path, body):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


def _env(**extra):
    """Nothing resolvable: no PATH, no interpreter dirs, no uv dirs, no plugin data."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("PATH", "MOKATA_PYTHON", "MOKATA_HOOK", "PYTHONPATH",
                        "CLAUDE_PLUGIN_DATA", "CLAUDE_CONFIG_DIR")}
    env.update(PATH="", MOKATA_PYTHON_DIRS="", MOKATA_UV_DIRS="")
    env.update(extra)
    return env


def _run(script, args, env):
    return subprocess.run([SH, str(script)] + args, capture_output=True, text=True, env=env,
                          stdin=subprocess.DEVNULL, cwd=str(ROOT))


class TheManifestPinsTheFullEnvironment(unittest.TestCase):
    def test_the_mcp_server_runs_the_plugins_own_version_with_every_full_package(self):
        data = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(data["version"], __version__)
        spec = data["mcpServers"]["mokata"]
        self.assertEqual(["uvx", "--python", PR.UV_PYTHON, "--from", PR.pin_spec(data["version"]), "mokata-mcp"],
                         [spec["command"]] + spec["args"])
        self.assertEqual(PR.pin_spec("1.2.3"), "mokata[full]==1.2.3")

    def test_the_launchers_ship_in_the_wheel(self):
        toml = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('"hooks/*"', toml)
        for name in ("mokata-launch", "mokata-plugin-env.sh", "mokata-hook-launch"):
            self.assertTrue((HOOKS / name).is_file(), name)


class TheMcpServerRecordsItsRuntime(unittest.TestCase):
    def test_on_the_plugin_route_it_records_the_interpreter_and_itself(self):
        data = _tmp(self)
        Path(data, PR.runtime_record_name("0.0.1")).write_text("/old/python\n", encoding="utf-8")
        rec = PR.record_runtime({"CLAUDE_PLUGIN_DATA": data}, version="9.9.9",
                                executable=sys.executable, pid=4242, now=100.0)
        self.assertEqual(rec, os.path.join(data, "runtime-9.9.9.txt"))
        self.assertEqual(Path(rec).read_text(encoding="utf-8").strip(),
                         sys.executable.replace("\\", "/") if os.name == "nt" else sys.executable)
        status = json.loads(Path(data, PR.MCP_STATUS_FILE).read_text(encoding="utf-8"))
        self.assertEqual((status["version"], status["pid"]), ("9.9.9", 4242))
        self.assertFalse(Path(data, "runtime-0.0.1.txt").exists(), "another version's record goes")

    def test_off_the_plugin_route_it_writes_nothing(self):
        self.assertIsNone(PR.record_runtime({}, version="9.9.9"))

    def test_the_server_main_calls_it_before_serving(self):
        from mokata.mcp import server
        calls = []
        fake_state = mock.Mock(usable=True)
        with mock.patch.object(server, "_public_module") as pub, \
                mock.patch.object(server, "_register_this_window"), \
                mock.patch("mokata.plugin_runtime.record_runtime",
                           side_effect=lambda *a, **k: calls.append("recorded")):
            pub.return_value.sdk_state.return_value = fake_state
            pub.return_value.build_server.return_value.run.side_effect = \
                lambda: calls.append("served")
            server.main([])
        self.assertEqual(calls, ["recorded", "served"])


@needs_sh
class TheHookShimRunsThePinnedVersion(unittest.TestCase):
    def test_rung_1_the_recorded_runtime_even_under_a_path_with_spaces(self):
        d = _tmp(self)
        py = _exe(os.path.join(d, "uv cache", "bin", "python"), 'echo "PINNED $*"')
        data = os.path.join(d, "data")
        os.makedirs(data)
        Path(data, PR.runtime_record_name(__version__)).write_text(py + "\n", encoding="utf-8")
        res = _run(SHIM, ["stop", "--via-plugin"], _env(CLAUDE_PLUGIN_DATA=data))
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout.strip(), "PINNED -m mokata_emit stop --via-plugin")

    def test_a_record_of_another_version_is_never_used(self):
        d = _tmp(self)
        py = _exe(os.path.join(d, "python"), 'echo "WRONG VERSION RAN"')
        data = os.path.join(d, "data")
        os.makedirs(data)
        Path(data, PR.runtime_record_name("0.0.1")).write_text(py + "\n", encoding="utf-8")
        res = _run(SHIM, ["secret-guard", "--text", FAKE_SECRET, "--path", "c.py"],
                   _env(CLAUDE_PLUGIN_DATA=data, MOKATA_PYTHON=sys.executable))
        self.assertNotIn("WRONG VERSION", res.stdout)
        self.assertEqual(res.returncode, 2, "the plugin's own source ran the gate: " + res.stderr)

    def test_rung_3_uv_without_any_python(self):
        d = _tmp(self)
        _exe(os.path.join(d, "uvx"), 'echo "UVX $*"')
        res = _run(SHIM, ["stop", "--via-plugin"], _env(MOKATA_UV_DIRS=d))
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout.strip(),
                         f"UVX --python >=3.12 --from mokata[full]=={__version__} python -m mokata_emit "
                         f"stop --via-plugin")

    def test_nothing_resolves_is_loud_and_names_uv(self):
        res = _run(SHIM, ["gate-guard"], _env())
        self.assertEqual(res.returncode, 1)
        self.assertIn("NOT firing", res.stderr)
        self.assertIn("install uv", res.stderr)


@needs_sh
class TheCliLauncherRunsThePinnedVersion(unittest.TestCase):
    def test_pinned_runtime_then_uvx(self):
        d = _tmp(self)
        cfg = os.path.join(d, "claude config")
        data = os.path.join(cfg, "plugins", "data", "mokata-mostack")
        os.makedirs(data)
        py = _exe(os.path.join(d, "py env", "python"), 'echo "PINNED $*"')
        Path(data, PR.runtime_record_name(__version__)).write_text(py + "\n", encoding="utf-8")
        # Run from the Bash tool: no CLAUDE_PLUGIN_DATA — the launcher finds Claude Code's data dir.
        res = _run(LAUNCHER, ["ide", "--no-browser"], _env(CLAUDE_CONFIG_DIR=cfg))
        self.assertEqual(res.stdout.strip(), "PINNED -m mokata ide --no-browser", res.stderr)
        uv = _tmp(self)
        _exe(os.path.join(uv, "uvx"), 'echo "UVX $*"')
        res = _run(LAUNCHER, ["doctor", "--plugin"], _env(MOKATA_UV_DIRS=uv, HOME=d, USERPROFILE=d))
        self.assertEqual(res.stdout.strip(),
                         f"UVX --python >=3.12 --from mokata[full]=={__version__} python -m mokata doctor --plugin")

    def test_no_uv_no_python_names_uv_for_every_os(self):
        res = _run(LAUNCHER, ["doctor"], _env(HOME=_tmp(self), USERPROFILE=_tmp(self)))
        self.assertEqual(res.returncode, 1)
        self.assertIn("astral.sh/uv/install.sh", res.stderr)
        self.assertIn("astral.sh/uv/install.ps1", res.stderr)


class TheFirstSessionSaysWhatThePluginNeeds(unittest.TestCase):
    def test_no_uv_one_line_per_os(self):
        line = PR.first_session_line(env={}, uvx=None)
        self.assertIn("`uv` was not found", line)
        self.assertIn("macOS / Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh", line)
        self.assertIn("Windows: `powershell", line)
        self.assertEqual(len(line.splitlines()), 1)

    def test_first_start_discloses_the_download_then_goes_quiet(self):
        data = _tmp(self)
        env = {"CLAUDE_PLUGIN_DATA": data}
        line = PR.first_session_line(env=env, uvx="/u/uvx")
        self.assertIn("first start", line)
        self.assertIn(PR.pin_spec(), line)
        self.assertIn("400 MB", line)
        PR.record_runtime(env, executable=sys.executable)
        self.assertIsNone(PR.first_session_line(env=env, uvx="/u/uvx"))

    def test_the_plugin_session_start_says_it_and_stamps_the_hooks(self):
        from mokata import hook_cli
        repo, data = _tmp(self), _tmp(self)
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_DATA": data, "HOME": _tmp(self),
                                          "USERPROFILE": _tmp(self)}), \
                mock.patch.object(hook_cli, "_payload_from_stdin",
                                  return_value={"cwd": repo, "session_id": "S"}), \
                mock.patch("mokata.plugin_runtime.find_uvx", return_value=None), \
                redirect_stdout(buf):
            self.assertEqual(hook_cli.main(["session-start", "--via-plugin"]), 0)
        self.assertIn("`uv` was not found", buf.getvalue())
        seen = json.loads(Path(data, PR.HOOK_STATUS_FILE).read_text(encoding="utf-8"))
        self.assertEqual(seen["version"], __version__)

    def test_the_cli_route_session_start_says_nothing_about_the_plugin(self):
        from mokata import hook_cli
        repo, data = _tmp(self), _tmp(self)
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_DATA": data}), \
                mock.patch.object(hook_cli, "_payload_from_stdin", return_value={"cwd": repo}), \
                mock.patch("mokata.plugin_runtime.find_uvx", return_value=None), \
                redirect_stdout(buf):
            hook_cli.main(["session-start"])
        self.assertNotIn("uv` was not found", buf.getvalue())
        self.assertFalse(Path(data, PR.HOOK_STATUS_FILE).exists())


class DoctorReportsThePluginRoute(unittest.TestCase):
    def _plugin(self, pin=None):
        root = _tmp(self)
        os.makedirs(os.path.join(root, ".claude-plugin"))
        Path(root, ".claude-plugin", "plugin.json").write_text(json.dumps({
            "name": "mokata", "version": __version__, "mcpServers": {"mokata": {
                "command": "uvx", "args": ["--from", pin or PR.pin_spec(), "mokata-mcp"]}}}),
            encoding="utf-8")
        return root

    def _healthy_data(self):
        data = _tmp(self)
        env = {"CLAUDE_PLUGIN_DATA": data}
        PR.record_runtime(env, executable=sys.executable, pid=os.getpid())
        PR.record_hook_seen("session-start", env)
        return data, env

    def test_every_check_green_on_a_working_install(self):
        data, env = self._healthy_data()
        rep = PR.plugin_route_report(self._plugin(), env=env, uvx="/u/uvx")
        self.assertTrue(rep.ok, "\n".join(rep.lines()))
        self.assertEqual([c.label for c in rep.checks], [
            "uv found", "package resolved", "version match", "MCP server running",
            "hooks firing"])

    def test_each_failure_is_named_with_its_fix(self):
        data = _tmp(self)
        env = {"CLAUDE_PLUGIN_DATA": data}
        Path(data, PR.MCP_STATUS_FILE).write_text(json.dumps(
            {"version": __version__, "pid": 999999999, "started_at": 0}), encoding="utf-8")
        rep = PR.plugin_route_report(self._plugin(pin="mokata==0.0.1"), env=env, uvx=None)
        bad = {c.label: c for c in rep.checks if not c.ok}
        self.assertEqual(set(bad), set(c.label for c in rep.checks))
        self.assertIn("install uv", bad["uv found"].fix)
        self.assertIn("mokata==0.0.1", bad["version match"].detail)
        self.assertIn("not running now", bad["MCP server running"].detail)
        self.assertFalse(rep.ok)

    def test_the_cli_flag_exits_by_health(self):
        from mokata.cli import main
        data, env = self._healthy_data()
        root = self._plugin()
        buf = io.StringIO()
        with mock.patch.dict(os.environ, env), \
                mock.patch("mokata.plugin_runtime.find_uvx", return_value="/u/uvx"), \
                redirect_stdout(buf):
            rc = main(["doctor", "--plugin", "--plugin-root", root])
        self.assertEqual(rc, 0, buf.getvalue())
        self.assertIn("healthy", buf.getvalue())
        with mock.patch.dict(os.environ, env), \
                mock.patch("mokata.plugin_runtime.find_uvx", return_value=None), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(main(["doctor", "--plugin", "--plugin-root", root]), 1)

    def test_the_slash_command_runs_it_through_the_plugin_launcher(self):
        text = (ROOT / "src" / "mokata" / "templates" / "commands" / "doctor.md").read_text(
            encoding="utf-8")
        self.assertIn('sh "$ROOT/src/mokata/hooks/mokata-launch" doctor --plugin', text)
        self.assertIn("mokata doctor --wiring", text)


class ThePluginRouteReachesTheFullEndState(unittest.TestCase):
    def test_setup_plugin_wires_the_mode_and_writes_no_harness_copy(self):
        from mokata.cli import main
        from mokata import adoption_modes as AM
        repo = _tmp(self)
        seen = {}

        def fake_wire(root, mode, **kw):
            seen.update(root=root, mode=mode, **kw)
            return AM.ModeWiringResult(mode=mode)

        with mock.patch("mokata.cli_commands.setup.wire_mode", side_effect=fake_wire), \
                redirect_stdout(io.StringIO()):
            rc = main(["setup", "claude", "--mode", "full", "--plugin", "--yes", "--path", repo])
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.isfile(os.path.join(repo, ".mokata", "manifest.json")))
        # The plugin IS the wiring: no hooks, commands, skills or MCP copy in the project. The ONE
        # thing in .claude/ is Claude Code's own session-worktree setting (0.0.26 P31, ruling 2:
        # worktrees branch from HEAD) — a project setting a plugin cannot carry.
        self.assertEqual(["settings.json"], sorted(os.listdir(os.path.join(repo, ".claude"))))
        with open(os.path.join(repo, ".claude", "settings.json"), encoding="utf-8") as fh:
            self.assertEqual({"worktree": {"baseRef": "head"}}, json.load(fh),
                             "the plugin IS the wiring")
        self.assertFalse(os.path.exists(os.path.join(repo, ".mcp.json")))
        self.assertEqual(seen["mode"], "full")
        self.assertEqual(seen["rerun"], AM.plugin_setup_command("full"))

    def test_setup_plugin_needs_a_mode(self):
        from mokata.cli import main
        with redirect_stdout(io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
            self.assertEqual(main(["setup", "claude", "--plugin", "--path", _tmp(self)]), 2)

    def test_the_plugin_briefing_never_sends_the_user_to_setup_a_cli_they_lack(self):
        from mokata.skills_visibility import briefing_offer
        repo = _tmp(self)
        home = _tmp(self)
        with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_ROOT": str(ROOT)}):
            self.assertIsNone(briefing_offer(repo, home))
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLAUDE_PLUGIN_ROOT", None)
            self.assertIn("mokata setup claude", briefing_offer(repo, home) or "")


class EveryFixLineNamesTheRoutesOwnCommand(unittest.TestCase):
    def test_on_the_plugin_route_setup_is_the_pinned_command(self):
        from mokata import adoption_modes as AM
        clean = {k: v for k, v in os.environ.items()
                 if k not in ("CLAUDE_PLUGIN_ROOT", "MOKATA_VIA_PLUGIN")}
        with mock.patch.dict(os.environ, clean, clear=True):
            self.assertEqual(AM.setup_command_for("full"), "mokata setup claude --mode full")
        for var in ("CLAUDE_PLUGIN_ROOT", "MOKATA_VIA_PLUGIN"):
            with mock.patch.dict(os.environ, {**clean, var: "1"}, clear=True):
                self.assertEqual(AM.setup_command_for("full"), AM.plugin_setup_command("full"))

    @needs_sh
    def test_the_launcher_marks_its_process_as_the_plugin_route(self):
        d = _tmp(self)
        data = os.path.join(d, "data")
        os.makedirs(data)
        py = _exe(os.path.join(d, "python"), 'echo "VIA=$MOKATA_VIA_PLUGIN"')
        Path(data, PR.runtime_record_name(__version__)).write_text(py + "\n", encoding="utf-8")
        res = _run(LAUNCHER, ["doctor"], _env(CLAUDE_PLUGIN_DATA=data))
        self.assertEqual(res.stdout.strip(), "VIA=1")


class TheIdeRunsFromThePluginRoute(unittest.TestCase):
    def test_a_python_without_the_web_stack_fails_fast(self):
        from mokata.ide import launch
        real = __import__("importlib.util").util.find_spec
        with mock.patch("importlib.util.find_spec",
                        side_effect=lambda n, *a: None if n == "starlette" else real(n, *a)), \
                mock.patch.object(launch, "running", return_value=None), \
                mock.patch.object(launch.subprocess, "Popen") as popen:
            with self.assertRaises(ImportError):
                launch.open_or_focus(_tmp(self), None, browser=False)
        popen.assert_not_called()

    def test_the_plugin_hook_names_the_pinned_launcher(self):
        from mokata import hook_cli
        with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_ROOT": "/p/root dir"}), \
                mock.patch("mokata.ide.launch.open_or_focus", side_effect=ImportError("x")):
            line = hook_cli._ide_for_prompt(_tmp(self), "/mokata:review-window", "S")
        self.assertIn("mokata-launch", line)
        self.assertIn('"/p/root dir', line)

    def test_review_window_falls_back_to_the_launcher(self):
        text = (ROOT / "src" / "mokata" / "templates" / "commands" / "review-window.md").read_text(
            encoding="utf-8")
        self.assertIn("mokata-launch\" ide --no-browser", text)


if __name__ == "__main__":
    unittest.main()
