"""0.0.26 L2 — `MOK-UPDATE`: `mokata update` updates mokata in one action, installer-aware, and the
vendored commands/skills tier can be refreshed on its own (Jas 2026-10-21: "whatever makes UX easy").

Business rules:
  * per installer, the ONE command upgrades mokata AND keeps its extras;
  * one network call when the human runs it; `--offline` makes none; offline says so, prints the
    command and runs nothing;
  * the release notes between installed and latest are shown;
  * ONE "Update now? [Y/n]" (or `--yes`, or the IDE click) is the consent; on yes EXACTLY the
    planned command runs, then `mokata update --finish` with the new mokata (refresh vendored
    commands/skills; re-wire only when the wiring changed);
  * Windows: never in-process — a detached updater waits for mokata to exit, runs the command,
    writes a result the next start reports;
  * `--check` / `--offline` NEVER spawn a process;
  * `mokata upgrade` is gone.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import install_route as R
from mokata import update as U
from mokata.cli import main
from mokata.netguard import no_network

ON_PATH = {"uv": "/usr/bin/uv", "pipx": "/usr/bin/pipx"}.get

# mokata's Requires-Dist, as the installed metadata states it (shape of pyproject's extras).
REQUIRES = [
    "mcp<2,>=1.2",
    'jsonschema>=4.0; extra == "schema"',
    'starlette>=0.27; extra == "ide"', 'uvicorn>=0.23.1; extra == "ide"',
    'sse-starlette>=1.6.1; extra == "ide"', 'pywinpty>=2.0; sys_platform == "win32" and extra == "ide"',
    'mcp<2,>=1.2; extra == "mcp"',
    'psycopg>=3.1; extra == "postgres"',
    'model2vec>=0.3; extra == "embeddings"', 'numpy>=1.24; extra == "embeddings"',
    'tree-sitter>=0.23; extra == "graph-ts"', 'tree-sitter-typescript>=0.23; extra == "graph-ts"',
    'model2vec>=0.3; extra == "full"', 'numpy>=1.24; extra == "full"',
    'tree-sitter>=0.23; extra == "full"', 'tree-sitter-typescript>=0.23; extra == "full"',
    'code-review-graph; extra == "full"', 'starlette>=0.27; extra == "full"',
    'uvicorn>=0.23.1; extra == "full"', 'sse-starlette>=1.6.1; extra == "full"',
    'pywinpty>=2.0; sys_platform == "win32" and extra == "full"',
]
FULL_PKGS = {"mcp", "model2vec", "numpy", "tree-sitter", "tree-sitter-typescript",
             "code-review-graph", "starlette", "uvicorn", "sse-starlette"}


def has(*names):
    s = {n.lower() for n in names}
    return lambda n: n.lower() in s


def run_cli(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(argv)
    return rc, buf.getvalue()


@contextmanager
def no_spawn():
    """Any process spawn is a failure: `update` runs no installer and nothing else."""
    boom = AssertionError("mokata update spawned a process")
    with mock.patch.object(subprocess, "Popen", side_effect=boom), \
            mock.patch.object(subprocess, "run", side_effect=boom), \
            mock.patch.object(subprocess, "call", side_effect=boom), \
            mock.patch.object(subprocess, "check_call", side_effect=boom), \
            mock.patch.object(subprocess, "check_output", side_effect=boom), \
            mock.patch.object(os, "system", side_effect=boom):
        yield


def _uv_env(d, req='{ name = "mokata", extras = ["full"] }', python=None):
    lines = ["[tool]", f"requirements = [{req}]"]
    if python:
        lines.append(f'python = "{python}"')
    Path(d, "uv-receipt.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return d


def _pipx_env(d, package_or_url="mokata[full]", injected=None):
    Path(d, "pipx_metadata.json").write_text(json.dumps({
        "main_package": {"package_or_url": package_or_url},
        "injected_packages": injected or {}}), encoding="utf-8")
    return d


# ⚠ Every `plan_update` call here pins `home=` to its temp dir (O16). Without it
# `update._install_line` asks `detect_install_method()` about the REAL `~/.claude`, so on a
# machine that happens to have the mokata plugin installed — Jas's Mac — the plugin route won
# over the env this test built, and the test asserted a `uv tool upgrade` line that the product
# was right not to print. An injected environment is only injected if nothing ambient outranks it.
def _route(d, **kw):
    kw.setdefault("which", ON_PATH)
    kw.setdefault("platform", "linux")
    kw.setdefault("editable", None)
    kw.setdefault("requires", REQUIRES)
    kw.setdefault("have", has(*FULL_PKGS))
    return dict(prefix=d, executable="/env/bin/python", **kw)


# ----------------------------------------------------------------------------- 1. the line
class PerInstallerTheLineKeepsTheExtras(unittest.TestCase):

    def plan(self, d, **kw):
        return R.plan_upgrade(**_route(d, **kw))

    def test_uv_tool_upgrade_keeps_the_receipts_full(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.plan(_uv_env(d))
        self.assertEqual(p.display, "uv tool upgrade mokata")
        self.assertEqual(p.specs, ["mokata[full]"])

    def test_a_pinned_uv_tool_reinstall_restates_extras_python_and_withs(self):
        with tempfile.TemporaryDirectory() as d:
            _uv_env(d, req='{ name = "mokata", extras = ["full"], specifier = "==0.0.24" }, '
                           '{ name = "rich" }', python="3.12")
            p = self.plan(d)
        self.assertEqual(p.display,
                         "uv tool install 'mokata[full]' --python 3.12 --with rich --upgrade")
        self.assertIn("==0.0.24", p.reason)

    def test_pipx_keeps_the_installed_spec_and_reinjects(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.plan(_pipx_env(d))
        self.assertEqual(p.display, "pipx upgrade mokata")
        self.assertEqual(p.specs, ["mokata[full]"])
        with tempfile.TemporaryDirectory() as d:
            p = self.plan(_pipx_env(d, "mokata", injected={"model2vec": {}}))
        self.assertEqual(p.display, "pipx upgrade --include-injected mokata")

    def test_pip_names_the_extras_the_environment_has(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.plan(d, has_pip=True)
        self.assertEqual(p.display, "/env/bin/python -m pip install --upgrade 'mokata[full]'")

    def test_uv_pip_names_them_too(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.plan(d, has_pip=False)
        self.assertEqual(
            p.display, "uv pip install --python /env/bin/python --upgrade 'mokata[full]'")

    def test_windows_lines_are_double_quoted_and_say_close_mokata(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.plan(d, has_pip=True, platform="win32",
                          have=has(*FULL_PKGS, "pywinpty"))
        self.assertIn('"mokata[full]"', p.display)
        self.assertNotIn("'", p.display)
        self.assertIn("Windows locks mokata.exe", p.reason)

    def test_the_runnable_command_is_the_displayed_one(self):
        for setup in (lambda d: _uv_env(d), lambda d: _pipx_env(d), lambda d: d):
            with tempfile.TemporaryDirectory() as d:
                setup(d)
                for hp in (True, False):
                    p = self.plan(d, has_pip=hp)
                    shown = p.display.replace("'", "").split()
                    self.assertEqual([os.path.basename(p.argv[0])] + p.argv[1:],
                                     [os.path.basename(shown[0])] + shown[1:])


class ExtrasAreDerivedFromTheInstalledPackages(unittest.TestCase):

    def extras(self, *pkgs, platform="linux"):
        return R.installed_extras(REQUIRES, has(*pkgs), platform)

    def test_full_covers_its_parts(self):
        self.assertEqual(self.extras(*FULL_PKGS), ["full"])

    def test_extras_outside_full_are_kept_beside_it(self):
        self.assertEqual(self.extras(*FULL_PKGS, "psycopg"), ["full", "postgres"])

    def test_a_partial_install_names_only_what_is_whole(self):
        self.assertEqual(self.extras("mcp", "model2vec", "numpy"), ["embeddings"])
        self.assertEqual(self.extras("mcp", "model2vec"), [])

    def test_a_base_dependency_extra_is_not_the_users_choice(self):
        self.assertNotIn("mcp", self.extras(*FULL_PKGS))

    def test_a_windows_only_requirement_counts_only_on_windows(self):
        # Without pywinpty a Windows env does not have [full] (nor [ide]) — only what is whole.
        self.assertEqual(self.extras(*FULL_PKGS, platform="win32"), ["embeddings", "graph-ts"])
        self.assertEqual(self.extras(*FULL_PKGS, "pywinpty", platform="win32"), ["full"])


# ----------------------------------------------------------------------------- 2. the feed
FEED = json.dumps([
    {"tag_name": "v0.0.28rc1", "prerelease": True, "body": "rc"},
    {"tag_name": "v0.0.27", "body": "## 0.0.27\n- seventh thing"},
    {"tag_name": "v0.0.26", "body": "- sixth thing\n- also this"},
    {"tag_name": "v0.0.25", "body": "- installed already"},
    {"tag_name": "v0.0.24", "body": "- older"},
])


class TheReleaseFeed(unittest.TestCase):

    def test_latest_and_the_delta_between(self):
        with tempfile.TemporaryDirectory() as d:
            p = U.plan_update(d, current="0.0.25", fetcher=lambda _u: FEED, home=d, **_route(d))
        self.assertTrue(p.online)
        self.assertEqual(p.latest, "0.0.27")
        self.assertEqual([r.version for r in p.delta], ["0.0.26", "0.0.27"])
        out = U.render_update(p)
        self.assertIn("What changed 0.0.25 → 0.0.27", out)
        self.assertIn("- seventh thing", out)
        self.assertIn("- sixth thing", out)
        self.assertNotIn("installed already", out)
        self.assertNotIn("rc", [r.version for r in p.delta])

    def test_up_to_date_says_nothing_to_do_and_no_line(self):
        with tempfile.TemporaryDirectory() as d:
            p = U.plan_update(d, current="0.0.27", fetcher=lambda _u: FEED, home=d, **_route(d))
        out = U.render_update(p)
        self.assertIn("(up to date)", out)
        self.assertIn("Nothing to do.", out)
        self.assertNotIn("Run:", out)

    def test_the_check_is_one_accounted_call(self):
        calls = []
        led = mock.Mock()
        with tempfile.TemporaryDirectory() as d:
            U.plan_update(d, current="0.0.25", ledger=led, home=d,
                          fetcher=lambda u: calls.append(u) or FEED, **_route(d))
        self.assertEqual(len(calls), 1)
        led.record.assert_called_once()
        self.assertEqual(led.record.call_args[0][0], "update_check")


class OfflineSaysSoAndStillPrintsTheLine(unittest.TestCase):

    def test_a_failed_fetch(self):
        def boom(_u):
            raise OSError("Network is unreachable")
        with tempfile.TemporaryDirectory() as d:
            p = U.plan_update(d, current="0.0.25", fetcher=boom, home=d, **_route(_uv_env(d)))
        out = U.render_update(p)
        self.assertFalse(p.online)
        self.assertIn("could not reach the release feed", out)
        self.assertIn("uv tool upgrade mokata", out)

    def test_the_real_fetch_under_no_network_degrades_clean(self):
        with tempfile.TemporaryDirectory() as d, no_network():
            p = U.plan_update(d, current="0.0.25", home=d, **_route(d))
        self.assertTrue(p.checked)
        self.assertFalse(p.online)

    def test_offline_flag_makes_no_call(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(U, "_default_fetch",
                                  side_effect=AssertionError("--offline went online")):
            rc, out = run_cli(["update", "--offline", "--path", d])
        self.assertEqual(rc, 0)
        self.assertIn("not checked", out)
        self.assertIn("Run:", out)


# ----------------------------------------------------------------------------- 3. vendored tier
class _Repo(unittest.TestCase):
    def setUp(self):
        # 0.0.26 O15 — `update` asks "Update now?"; at a REAL terminal the ambient stdin answered
        # it, and with stdout captured the prompt was invisible: `test_no_answer_runs_nothing`
        # sat forever on Jas's Mac (measured under a pty: `prompt.read_yes_no` <- `cmd_update`).
        # The test OWNS its stdin: not a terminal, so the safe default is graded on every machine.
        import _support
        double = _support.stdin_is(_support.NoTtyStdin())
        double.__enter__()
        self.addCleanup(double.__exit__, None, None, None)
        self.tmp = tempfile.mkdtemp(prefix="mk-l2-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, "repo")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.root)
        os.makedirs(self.home)

    def refresh(self):
        from mokata.harness_setup import setup_harness
        return setup_harness("claude", root=self.root, scope="project", assume_yes=True,
                             home=self.home, skills_only=True, out=lambda *_a: None)

    def project(self):
        from mokata.harness_setup import vendored_drift
        return {v["scope"]: v for v in vendored_drift(self.root, home=self.home)}["project"]


class SkillsDrift(_Repo):

    def test_nothing_set_up_is_not_drift(self):
        rep = self.project()
        self.assertFalse(rep["installed"])
        self.assertFalse(rep["drift"])

    def test_what_setup_writes_is_exactly_what_drift_expects(self):
        self.refresh()
        rep = self.project()
        self.assertTrue(rep["installed"])
        self.assertFalse(rep["drift"], rep)

    def test_an_edited_skill_a_deleted_command_and_a_retired_skill_are_each_reported(self):
        self.refresh()
        claude = Path(self.root, ".claude")
        skill = claude / "skills" / "brainstorm" / "SKILL.md"
        skill.write_text(skill.read_text(encoding="utf-8") + "\nlocal edit\n", encoding="utf-8")
        (claude / "commands" / "spec.md").unlink()
        old = claude / "skills" / "retired-skill"
        old.mkdir()
        from mokata.agent_skills import SKILL_MARKER
        (old / "SKILL.md").write_text(f"---\nname: x\n---\n{SKILL_MARKER}\n", encoding="utf-8")
        rep = self.project()
        self.assertTrue(rep["drift"])
        self.assertEqual((rep["changed"], rep["missing"], rep["stale"]), (1, 1, 1))
        self.assertIn("mokata setup claude --skills-only", rep["summary"])

    def test_a_users_own_skill_is_never_drift(self):
        self.refresh()
        mine = Path(self.root, ".claude", "skills", "mine")
        mine.mkdir()
        (mine / "SKILL.md").write_text("---\nname: mine\n---\nmy own\n", encoding="utf-8")
        self.assertFalse(self.project()["drift"])

    def test_crlf_on_disk_is_not_drift(self):
        self.refresh()
        p = Path(self.root, ".claude", "commands", "spec.md")
        p.write_bytes(p.read_bytes().replace(b"\n", b"\r\n"))
        self.assertFalse(self.project()["drift"])

    def test_update_prints_the_skills_only_refresh_when_up_to_date(self):
        self.refresh()
        Path(self.root, ".claude", "commands", "spec.md").unlink()
        p = U.plan_update(self.root, current="0.0.27", fetcher=lambda _u: FEED, home=self.home,
                          **_route(self.tmp))
        self.assertEqual(p.follow_up, ["mokata setup claude --skills-only"])
        self.assertIn("mokata setup claude --skills-only", U.render_update(p))

    def test_a_new_version_follows_up_with_the_full_setup(self):
        self.refresh()
        p = U.plan_update(self.root, current="0.0.25", fetcher=lambda _u: FEED, home=self.home,
                          **_route(self.tmp))
        self.assertEqual(p.follow_up, ["mokata setup claude"])


class SkillsOnlyRefresh(_Repo):

    def test_it_restores_the_tier_and_touches_nothing_else(self):
        res = self.refresh()
        self.assertEqual(res.message, "ok")
        claude = Path(self.root, ".claude")
        self.assertTrue((claude / "skills" / "brainstorm" / "SKILL.md").is_file())
        self.assertTrue(any((claude / "commands").glob("*.md")))
        self.assertFalse((claude / "settings.json").exists(), "skills-only wrote settings.json")
        self.assertFalse(Path(self.root, ".mcp.json").exists(), "skills-only registered MCP")
        self.assertFalse(Path(self.root, ".mokata").exists(), "skills-only ran init")

    def test_it_fixes_the_drift_it_was_printed_for(self):
        self.refresh()
        (Path(self.root, ".claude", "commands") / "spec.md").unlink()
        self.assertTrue(self.project()["drift"])
        self.refresh()
        self.assertFalse(self.project()["drift"])

    def test_it_is_gated_like_every_setup(self):
        from mokata.harness_setup import setup_harness
        res = setup_harness("claude", root=self.root, home=self.home, skills_only=True,
                            confirm=lambda _q: False, out=lambda *_a: None)
        self.assertTrue(res.aborted)
        self.assertFalse(Path(self.root, ".claude").exists())

    def test_the_cli_flag_excludes_mode(self):
        with self.assertRaises(SystemExit):
            with redirect_stdout(io.StringIO()), mock.patch.object(sys, "stderr", io.StringIO()):
                main(["setup", "claude", "--skills-only", "--mode", "full", "--path", self.root])


# ----------------------------------------------------------------------------- 4. never runs
class CheckNeverSpawns(_Repo):

    def test_check_and_offline_spawn_nothing(self):
        with no_spawn(), mock.patch.object(U, "_default_fetch", return_value=FEED):
            rc, out = run_cli(["update", "--check", "--path", self.root])
            rc2, out2 = run_cli(["update", "--offline", "--path", self.root])
        self.assertEqual((rc, rc2), (0, 0))
        self.assertIn("installs and changes nothing", out)
        self.assertIn("Run:", out2)

    def test_the_ide_row_and_check_spawn_nothing(self):
        from mokata.ide import health
        with no_spawn(), mock.patch.object(U, "_default_fetch", return_value=FEED):
            row = health._mokata_row(self.root)
            res = health.check_update(self.root)
        self.assertEqual(row["update"]["command"], res["update"]["command"])

    def test_update_writes_nothing(self):
        before = sorted(str(p) for p in Path(self.tmp).rglob("*"))
        with mock.patch.object(U, "_default_fetch", return_value=FEED):
            run_cli(["update", "--check", "--path", self.root])
        self.assertEqual(sorted(str(p) for p in Path(self.tmp).rglob("*")), before)

    def test_the_upgrade_executor_is_gone(self):
        import mokata.version as V
        for name in ("run_upgrade", "finish_upgrade", "upgrade_plan", "upgrade_steps"):
            self.assertFalse(hasattr(V, name), name)
        out = io.StringIO()
        try:
            with redirect_stdout(out), mock.patch.object(sys, "stderr", out):
                rc = main(["upgrade", "--path", self.root])
        except SystemExit as exc:
            rc = exc.code
        self.assertEqual(rc, 2)             # "'upgrade' is not a mokata command. Did you mean 'update'?"


# ----------------------------------------------------------------------------- 5. the executor
def _pip_plan(d, **kw):
    """`update._install_line` replaced by a pip env that has [full] (POSIX unless overridden)."""
    kw.setdefault("has_pip", True)
    kw.setdefault("have", has(*FULL_PKGS, "pywinpty"))      # [full] whole on Windows too
    return mock.patch.object(U, "_install_line", lambda _m, _h, _r: R.plan_upgrade(**_route(d, **kw)))


class TheConfirmedPathRunsExactlyThePlan(_Repo):

    def cli(self, *extra, platform="linux", feed=FEED):
        ran = []
        with _pip_plan(self.tmp, platform=platform), \
                mock.patch.object(U, "_default_fetch", return_value=feed), \
                mock.patch.object(U, "_fg_run", side_effect=lambda a: ran.append(a) or 0), \
                mock.patch.object(U, "__version__", "0.0.25"), \
                mock.patch.object(U, "launch_detached",
                                  side_effect=lambda *a, **k: ran.append(("detached", a, k))):
            rc, out = run_cli(["update", "--path", self.root, *extra])
        return rc, out, ran

    def test_yes_runs_the_command_with_its_extras_then_the_finish(self):
        rc, out, ran = self.cli("--yes")
        self.assertEqual(rc, 0, out)
        self.assertEqual(ran[0], ["/env/bin/python", "-m", "pip", "install", "--upgrade",
                                  "mokata[full]"])
        self.assertEqual(ran[1], U.finish_argv(self.root))
        self.assertEqual(ran[1][-4:], ["update", "--finish", "--path", os.path.abspath(self.root)])
        self.assertEqual(len(ran), 2, "exactly the planned command and the follow-up")
        self.assertIn("What changed 0.0.25 → 0.0.27", out)

    def test_no_answer_runs_nothing(self):
        rc, out, ran = self.cli()                    # tests have no TTY: never asked -> no
        self.assertEqual(ran, [])
        self.assertIn("not updated", out)

    def test_an_explicit_no_runs_nothing(self):
        with mock.patch("mokata.prompt._stdin_is_tty", return_value=True), \
                mock.patch("builtins.input", return_value="n"):
            _rc, _out, ran = self.cli()
        self.assertEqual(ran, [])

    def test_enter_means_yes(self):
        with mock.patch("mokata.prompt._stdin_is_tty", return_value=True), \
                mock.patch("builtins.input", return_value=""):
            _rc, _out, ran = self.cli()
        self.assertEqual(len(ran), 2)

    def test_a_failed_upgrade_skips_the_finish_and_gives_the_line(self):
        ran = []
        with _pip_plan(self.tmp), mock.patch.object(U, "_default_fetch", return_value=FEED), \
                mock.patch.object(U, "__version__", "0.0.25"), \
                mock.patch.object(U, "_fg_run", side_effect=lambda a: ran.append(a) or 1):
            rc, out = run_cli(["update", "--yes", "--path", self.root])
        self.assertEqual(rc, 1)
        self.assertEqual(len(ran), 1)
        self.assertIn("pip install --upgrade 'mokata[full]'", out)

    def test_a_blocked_feed_says_so_and_runs_nothing(self):
        def boom(_u):
            raise OSError("blocked")
        ran = []
        with _pip_plan(self.tmp), mock.patch.object(U, "_default_fetch", side_effect=boom), \
                mock.patch.object(U, "_fg_run", side_effect=lambda a: ran.append(a) or 0):
            rc, out = run_cli(["update", "--yes", "--path", self.root])
        self.assertEqual((rc, ran), (0, []))
        self.assertIn("could not reach the release feed", out)
        self.assertIn("Not updating without knowing what is newer", out)
        self.assertIn("pip install --upgrade 'mokata[full]'", out)

    def test_windows_hands_off_to_the_detached_updater_and_runs_nothing_in_process(self):
        rc, out, ran = self.cli("--yes", platform="win32")
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(ran), 1)
        tag, args, kw = ran[0]
        self.assertEqual(tag, "detached")
        self.assertEqual(args[0].argv[-1], "mokata[full]")
        self.assertTrue(args[0].deferred)
        self.assertEqual(kw["wait_pids"], [os.getpid()])
        self.assertIn("as soon as this command exits", out)

    def test_up_to_date_with_drifted_skills_refreshes_them_on_yes(self):
        calls = []
        with _pip_plan(self.tmp), mock.patch.object(U, "_default_fetch", return_value=FEED), \
                mock.patch.object(U, "__version__", "0.0.27"), \
                mock.patch.object(U, "_follow_up",
                                  side_effect=lambda p: ["mokata setup claude --skills-only"]), \
                mock.patch.object(U, "finish_update", side_effect=lambda *a, **k: calls.append(a) or 0), \
                mock.patch.object(U, "_fg_run", side_effect=AssertionError("ran an installer")):
            rc, out = run_cli(["update", "--yes", "--path", self.root])
        self.assertEqual((rc, calls), (0, [(self.root,)]))


class TheDetachedUpdater(_Repo):

    def plan(self):
        with _pip_plan(self.tmp, platform="win32"):
            return U.plan_update(self.root, check=False, home=self.home)

    def test_launch_spawns_the_base_python_on_a_copy_with_no_window(self):
        seen = []
        job = U.launch_detached(self.plan(), self.root, wait_pids=[4242], home=self.home,
                                windows=True, spawn=lambda argv, **kw: seen.append((argv, kw)))
        (argv, kw), = seen
        script = os.path.join(self.home, ".mokata", "update", "updater.py")
        self.assertEqual(argv[1:], ["-I", script,
                                    os.path.join(self.home, ".mokata", "update", "job.json")])
        self.assertNotIn(os.sep + "src" + os.sep + "mokata", argv[2])  # never the package's own file
        self.assertEqual(Path(script).read_bytes(),
                         Path(U.__file__).with_name("_updater.py").read_bytes())
        self.assertTrue(kw["creationflags"] & 0x08000000, "CREATE_NO_WINDOW")
        self.assertTrue(kw["creationflags"] & 0x00000200, "CREATE_NEW_PROCESS_GROUP")
        self.assertEqual(job["wait_pids"], [4242])
        self.assertEqual(job["argv"][-1], "mokata[full]")
        self.assertEqual(job["finish_argv"], U.finish_argv(self.root))
        self.assertEqual(job["result"], U.result_path(self.home))

    def test_posix_launch_is_a_new_session(self):
        seen = []
        U.launch_detached(self.plan(), self.root, wait_pids=[1], home=self.home, windows=False,
                          spawn=lambda argv, **kw: seen.append(kw))
        self.assertTrue(seen[0]["start_new_session"])

    def _job(self):
        return {"wait_pids": [7], "argv": ["pip", "install", "--upgrade", "mokata[full]"],
                "display": "pip install --upgrade 'mokata[full]'", "from": "0.0.25",
                "finish_argv": ["py", "-m", "mokata", "update", "--finish"],
                "version_argv": ["py", "-c", "v"], "result": U.result_path(self.home)}

    def test_it_waits_for_mokata_then_installs_then_finishes(self):
        from mokata import _updater
        log, alive = [], iter([True, True, False])
        res = _updater.run_job(self._job(), alive=lambda p: log.append(("alive", p)) or next(alive),
                               sleep=lambda _s: None,
                               run=lambda a: log.append(a) or (0, "0.0.27\n" if a[1] == "-c" else ""))
        self.assertEqual([x for x in log if isinstance(x, tuple)], [("alive", 7)] * 3)
        self.assertEqual([x for x in log if isinstance(x, list)],
                         [self._job()["argv"], self._job()["finish_argv"], ["py", "-c", "v"]])
        self.assertTrue(res["ok"])
        self.assertEqual(res["to"], "0.0.27")

    def test_it_never_installs_while_mokata_still_runs(self):
        from mokata import _updater
        t = iter(range(0, 10_000, 100))
        res = _updater.run_job(self._job(), alive=lambda p: True, sleep=lambda _s: None,
                               clock=lambda: next(t),
                               run=lambda a: self.fail("installed while mokata ran"))
        self.assertFalse(res["ok"])
        self.assertIn("still running", res["error"])

    def test_a_failure_is_reported_once_at_the_next_start(self):
        from mokata import _updater
        job = self._job()
        Path(self.tmp, "job.json").write_text(json.dumps(job), encoding="utf-8")
        with mock.patch.object(_updater, "_alive", return_value=False), \
                mock.patch.object(_updater, "_run", return_value=(1, "WinError 32: in use")):
            _updater.main([str(Path(self.tmp, "job.json"))])
        res = U.take_result(self.home)
        self.assertFalse(res["ok"])
        text = U.render_result(res)
        self.assertIn("pip install --upgrade 'mokata[full]'", text)
        self.assertIn("close every Claude Code session", text)
        self.assertIsNone(U.take_result(self.home), "reported once")

    def test_the_next_mokata_update_reports_it(self):
        from mokata import _updater
        _updater.write_result(U.result_path(self.home), {"ok": True, "from": "0.0.25",
                                                          "to": "0.0.27"})
        with mock.patch.object(U, "result_path", lambda home=None: U.os.path.join(
                self.home, ".mokata", "update-result.json")):
            _rc, out = run_cli(["update", "--offline", "--path", self.root])
        self.assertIn("mokata updated: 0.0.25 → 0.0.27", out)


class TheFinishRunsWithTheNewMokata(_Repo):

    def finish(self, stale_wiring=False):
        calls = []
        with mock.patch("mokata.govern.doctor.wiring_drift_findings",
                        return_value=["x"] if stale_wiring else []):
            U.finish_update(self.root, home=self.home, out=lambda *_a: None,
                            setup=lambda *a, **k: calls.append(k) or mock.Mock(message="ok"))
        return calls

    def test_nothing_set_up_touches_nothing(self):
        self.assertEqual(self.finish(stale_wiring=True), [])

    def test_drifted_skills_are_refreshed_only(self):
        self.refresh()
        Path(self.root, ".claude", "commands", "spec.md").unlink()
        (call,) = self.finish()
        self.assertTrue(call["skills_only"])
        self.assertTrue(call["assume_yes"])

    def test_changed_wiring_is_rewired(self):
        self.refresh()
        (call,) = self.finish(stale_wiring=True)
        self.assertFalse(call["skills_only"])

    def test_current_everything_does_nothing(self):
        self.refresh()
        self.assertEqual(self.finish(), [])


class TheIdeClick(_Repo):

    def test_posix_click_streams_exactly_the_plan_then_the_finish(self):
        from mokata.ide import health
        ran = []
        with _pip_plan(self.tmp):
            cmd = U.plan_update(self.root, check=False).command
            res = health._update(self.root, cmd,
                                 stream=lambda a, label, buf: ran.append(a) or buf.append(
                                     "mokata 0.0.27") or 0)
        self.assertTrue(res["ok"], res)
        self.assertEqual(ran[0][-1], "mokata[full]")
        self.assertEqual(ran[1], U.finish_argv(self.root))
        self.assertIn("0.0.27", res["message"])
        self.assertIn("Restart the IDE", res["message"])

    def test_a_click_carrying_another_command_runs_nothing(self):
        from mokata.ide import health
        with _pip_plan(self.tmp):
            res = health._update(self.root, "pip install something-else",
                                 stream=lambda *a: self.fail("ran a command nobody was shown"))
        self.assertFalse(res["ok"])
        self.assertIn("changed since it was shown", res["message"])

    def test_windows_click_hands_off_and_closes_the_ide(self):
        from mokata.ide import health
        seen = []
        with _pip_plan(self.tmp, platform="win32"), \
                mock.patch.object(health, "_exit_soon", side_effect=lambda: seen.append("exit")):
            cmd = U.plan_update(self.root, check=False).command
            res = health._update(self.root, cmd, launch=lambda p, r, **k: seen.append(k),
                                 stream=lambda *a: self.fail("ran in the locked process"))
        self.assertTrue(res["ok"])
        self.assertEqual(seen, [{"wait_pids": [os.getpid()]}, "exit"])
        self.assertIn("mokata ide", res["message"])


class ThePluginRoute(unittest.TestCase):

    def test_plugin_prints_claude_codes_steps(self):
        with tempfile.TemporaryDirectory() as d:
            p = U.plan_update(d, check=False, method="plugin", home=d)
        self.assertEqual(p.installer, "plugin")
        self.assertIn("/plugin marketplace update mostack", p.command)
        self.assertEqual(p.follow_up, ["mokata doctor --wiring"])


if __name__ == "__main__":
    unittest.main()
