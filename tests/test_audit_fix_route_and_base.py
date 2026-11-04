"""0.0.27 AUDIT-FIX — two business rules from the independent release audit.

M1  Every explain hand-off tells the user to type the slash command THEIR route installed:
    `/mokata:explain` on the plugin route, `/explain` after `mokata init` / `setup claude`. The
    string `mokata explain` prints, the string the IDE terminal types and the line queued for a
    live session all agree, and the IDE's allow-regex accepts exactly that one form.

L1  A `--base` is a ref, never an option: `ci-check --base=--output=X` must not make git write X.
    It is refused like any base git cannot use (exit 2, naming the value), and no file appears.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401
import _explain_fixture as F
from test_l7b_committed_specs import _Repo

from mokata.ide import context as X
from mokata.ide import terminal as T
from mokata.cli_commands import explain as CE
from mokata.plugin_runtime import slash_command

_ROUTE_VARS = ("CLAUDE_PLUGIN_ROOT", "MOKATA_VIA_PLUGIN")


def _route(plugin: bool, home=None):
    """A process on the plugin route (env), or not — whatever the ambient env says; `home` fakes ~."""
    env = {k: v for k, v in os.environ.items() if k not in _ROUTE_VARS}
    if plugin:
        env["MOKATA_VIA_PLUGIN"] = "1"
    if home:
        env["HOME"] = env["USERPROFILE"] = home
    return mock.patch.dict(os.environ, env, clear=True)


def _install(base, bare):
    d = os.path.join(base, ".claude", "commands")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, bare + ".md"), "w", encoding="utf-8") as fh:
        fh.write("x\n")


class TheExplainCommandMatchesWhatIsInstalled(unittest.TestCase):
    """Four route states x (what the user is told, what the IDE types, what a live session is told)."""

    @classmethod
    def setUpClass(cls):
        import shutil
        cls.d = tempfile.mkdtemp()
        cls.addClassCleanup(shutil.rmtree, cls.d, True)
        F.build_repo(cls.d)

    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, self.home, True)
        for bare in ("explain",):
            for base in (self.d, self.home):
                p = os.path.join(base, ".claude", "commands", bare + ".md")
                if os.path.exists(p):
                    os.remove(p)

    def _surfaces(self, plugin):
        with _route(plugin, self.home):
            printed = CE.command_for(self.d, ["session", "S1"])
            typed = T.explain_prompt("session S1", self.d)
            X.queue_explain(self.d, "S1", "session S1", "req1")
            queued, _ = X.explain_block(self.d, "S1")
        return printed, typed, queued

    def _expect(self, cmd, plugin=False):
        printed, typed, queued = self._surfaces(plugin)
        self.assertEqual(f'claude "{cmd} session S1"', printed)
        self.assertEqual(f"{cmd} session S1", typed)
        self.assertIn(f"`{cmd} session S1`", queued)

    def test_plugin_env_is_namespaced_even_if_init_files_exist(self):
        _install(self.d, "explain")
        self._expect("/mokata:explain", plugin=True)

    def test_init_file_in_the_project_is_bare(self):
        _install(self.d, "explain")
        self._expect("/explain")

    def test_init_file_in_the_home_is_bare(self):
        _install(self.home, "explain")
        self._expect("/explain")

    def test_neither_keeps_the_namespaced_form(self):
        """A plugin user with a pip mokata and no init: the namespaced command is what works."""
        self._expect("/mokata:explain")

    def test_a_builtin_colliding_name_is_the_mokata_prefixed_file(self):
        with _route(False, self.home):
            self.assertEqual("/mokata:review", slash_command("review", self.d))
            _install(self.d, "review")                # Claude Code's own /review: not ours
            self.assertEqual("/mokata:review", slash_command("review", self.d))
            _install(self.d, "mokata-review")
            self.assertEqual("/mokata-review", slash_command("review", self.d))
            _install(self.home, "mokata-doctor")
            self.assertEqual("/mokata-doctor", slash_command("doctor", self.d))
            self.assertEqual("/mokata:review", slash_command("review", self.d, {"MOKATA_VIA_PLUGIN": "1"}))

    def test_the_allow_regex_accepts_exactly_the_emitted_form(self):
        bare, ns = T.explain_re("/explain"), T.explain_re("/mokata:explain")
        self.assertTrue(bare.fullmatch("/explain session S1"))
        self.assertFalse(bare.fullmatch("/mokata:explain session S1"))
        self.assertTrue(ns.fullmatch("/mokata:explain session S1"))
        self.assertFalse(ns.fullmatch("/explain session S1"))
        self.assertFalse(bare.fullmatch("/explainer session S1"))
        self.assertFalse(bare.fullmatch("x /explain session S1"))
        for bad in ("/explain session S1;x", "/explain session $(x)", "/explain a\nb",
                    "/explain session S1\n"):
            self.assertFalse(bare.fullmatch(bad), bad)

    def test_the_check_inside_explain_prompt_is_reached(self):
        """A target text that slips past parse_target (crafted) is stopped by the allow-regex."""
        from mokata.explain import bundle as B
        for crafted in ("session abc\n", "live;x", "session $(x)"):
            with _route(False, self.home), \
                    mock.patch.object(B, "target_text", return_value=crafted):
                with self.assertRaises(T.TerminalRefused, msg=repr(crafted)):
                    T.explain_prompt("live", self.d)

    def test_the_terminal_host_types_the_installed_form(self):
        """`TerminalHost.open(explain=...)` passes its own root: the argv carries the init form."""
        host = T.TerminalHost(self.d, enabled=(True, ""))
        host.programs = {"claude": lambda resume: ["/bin/claude"], "shell": lambda r: ["/bin/sh"]}
        self.addCleanup(host._terms.clear)          # lets the reaper thread end
        made = {}

        def fake(tid, program, argv, root, cols, rows):
            made["argv"] = argv
            return mock.Mock(alive=True, id=tid)
        _install(self.d, "explain")
        with _route(False, self.home), mock.patch.object(T, "_make_terminal", fake):
            host.open("claude", explain="session S1")
        self.assertEqual(["/bin/claude", "/explain session S1"], made["argv"])

    def test_a_pr_target_is_refused_in_the_bare_spelling_too(self):
        from mokata.explain.bundle import is_pr_target
        self.assertTrue(is_pr_target("/explain pr 12"))


class TheFirstMinuteNextStepFollowsTheRoute(unittest.TestCase):

    def test_home_and_suggest_name_the_installed_brainstorm(self):
        home = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, home, True)
        with _Repo() as repo:
            from mokata import home as H
            from mokata.config import Surface
            from mokata.hints import derive, render
            for plugin, install, want in ((True, True, "/mokata:brainstorm"),
                                          (False, True, "/brainstorm"),
                                          (False, False, "/mokata:brainstorm")):
                if install:
                    _install(repo.path, "brainstorm")
                elif os.path.exists(os.path.join(repo.path, ".claude", "commands", "brainstorm.md")):
                    os.remove(os.path.join(repo.path, ".claude", "commands", "brainstorm.md"))
                with _route(plugin, home):
                    surface = Surface.load(repo.path)
                    line, nxt = H._run(surface)
                self.assertEqual(want, nxt[0], (plugin, install))


    def test_suggest_names_the_installed_brainstorm_for_the_approach_hint(self):
        from test_p14_next_step_hints import RUN, World
        from mokata.hints import derive
        home = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, home, True)
        w = World(self)
        for plugin, install, want in ((True, True, "/mokata:brainstorm"),
                                      (False, True, "/brainstorm"),
                                      (False, False, "/mokata:brainstorm")):
            f = os.path.join(w.d, ".claude", "commands", "brainstorm.md")
            if install:
                _install(w.d, "brainstorm")
            elif os.path.exists(f):
                os.remove(f)
            with _route(plugin, home):
                ans = derive(w.d, run=RUN)
            self.assertEqual(want, ans["hints"][0]["command"], (plugin, install))


    def test_home_and_suggest_after_a_finished_run_and_with_no_run(self):
        from test_p14_next_step_hints import RUN, World
        from mokata import home as H
        from mokata.config import Surface
        from mokata.hints import derive
        from mokata.init import init_repo
        home = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, home, True)
        bare = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, bare, True)
        subprocess.run(["git", "-C", bare, "init", "-q"], check=True, stdin=subprocess.DEVNULL,
                       capture_output=True)
        init_repo(root=bare, profile="minimal", assume_yes=True, out=lambda *_a: None)
        w = World(self).approve().spec("AC-1")
        w.finish()
        for plugin, want in ((True, "/mokata:brainstorm"), (False, "/mokata:brainstorm"),
                             (None, "/brainstorm")):
            for d in (bare, w.d):
                _install(d, "brainstorm") if plugin is None else None
            with _route(bool(plugin), home):
                self.assertEqual(want, H._run(Surface.load(bare))[1][0], ("none active", plugin))
                self.assertEqual(want, derive(w.d, run=RUN)["hints"][0]["command"], plugin)
                with mock.patch("mokata.run_resolver.resolve_run",
                                return_value=mock.Mock(run_id=RUN, ambiguous=False)):
                    self.assertEqual(want, H._run(Surface.load(w.d))[1][0], ("finished", plugin))


class TheRouteHelper(unittest.TestCase):

    def test_env_argument_is_honoured(self):
        self.assertEqual("/mokata:x", slash_command("x", None, {"CLAUDE_PLUGIN_ROOT": "/p"}))


class ABaseIsNeverAnOption(unittest.TestCase):

    def _repo(self):
        repo = _Repo()
        self.addCleanup(repo.__exit__, None, None, None)
        subprocess.run(["git", "-C", repo.path, "init", "-q"], check=True,
                       stdin=subprocess.DEVNULL, capture_output=True)
        return repo

    def test_output_option_as_base_is_refused_and_writes_nothing(self):
        repo = self._repo()
        target = os.path.join(tempfile.mkdtemp(), "pwned")
        self.addCleanup(lambda: [os.remove(os.path.join(os.path.dirname(target), f))
                                 for f in os.listdir(os.path.dirname(target))])
        for extra in ([], ["--no-fail"]):
            code, out = repo.cli("ci-check", f"--base=--output={target}", *extra)
            self.assertEqual(2, code, out)
            self.assertIn(f"--output={target}", out)
            self.assertNotIn("PASSED", out)
        self.assertEqual([], os.listdir(os.path.dirname(target)), "git wrote a file")

    def test_a_dash_base_never_reaches_git(self):
        from mokata.cli_commands.knowledge import _git_changed_files
        with mock.patch("subprocess.run", side_effect=AssertionError("git was run")):
            self.assertIsNone(_git_changed_files(".", "--output=x"))
            self.assertIsNone(_git_changed_files(".", " -p"))

    def test_the_proof_head_reads_never_see_a_ref_as_an_option(self):
        from mokata.ide import proof as P
        repo = self._repo()
        subprocess.run(["git", "-C", repo.path, "-c", "user.name=t", "-c", "user.email=t@e.c",
                        "-c", "commit.gpgsign=false", "commit", "-qm", "x", "--allow-empty"],
                       check=True, stdin=subprocess.DEVNULL, capture_output=True)
        out = os.path.join(tempfile.mkdtemp(), "o")
        self.addCleanup(lambda: os.path.exists(out) and os.remove(out))
        self.assertIsNone(P._head_text(repo.path, f"--output={out}", "a.py"))
        self.assertFalse(os.path.exists(out), "git show wrote a file")


if __name__ == "__main__":
    unittest.main()
