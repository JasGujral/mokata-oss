"""0.0.27 SEC-HARDEN — six small security fixes, each graded at the business level.

Business rules (each test class names its rule):
  G1  An AGENT's write to what git executes or is configured by — a file in the hooks directory
      (`.git/hooks/`, a linked worktree's common dir, every `core.hooksPath`), git's config files,
      the `.git` pointer file, or a Bash `git config` write — is REFUSED until a human approves
      exactly that call with `mokata approve <id>`. The refusal names the command. The approval is
      single-use and bound to the whole call; a different write needs its own. Any spelling, a
      symlinked parent, a hard link, a literal `cd` into `.git` first: the same file is the same
      file. Ordinary files (`.github/`, `src/hooks/`) are
      untouched.
  G2  The IDE never serves anything under `.git/` through /api/file, /api/diff/context or
      /api/locate, and says why in plain words (git's own store), not "outside the repository".
  G3  A HARD LINK into `.mokata/` or `.git/` is the store: refused by every read route and never a
      search hit — checked by file identity (device, inode), not by path; an ordinary hard link
      (two names for a repo file) is still served.
  G4  The hook front door (`mokata_emit`) never copies a private file's bytes into its spool.
  G5  The HOME guard also catches a leaked `XDG_*`, `APPDATA` or `LOCALAPPDATA`.
  G6  `spec emit` run in a LINKED worktree writes the committed spec into THAT worktree, never into
      the main checkout.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import _support  # noqa: F401

import mokata_emit as E
from mokata import approval
from mokata import git_control as GC
from mokata.ide import snapshots as S

MARK = "SECRET-BYTES-5e1d"
TOKEN, PORT = "tok", 9999
BASE = f"http://127.0.0.1:{PORT}"
HOOK_BODY = "#!/bin/sh\n" + "".join(f"echo line {i}\n" for i in range(1, 30)) + f"curl {MARK}\n"


def git(d, *a):
    return subprocess.run(["git", "-C", d, *a], check=True, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL).stdout


def write(d, rel, data):
    p = os.path.join(d, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(data)
    return p


def same_file_on_disk(a, b):
    try:
        return os.path.exists(a) and os.path.samefile(a, b)
    except OSError:
        return False


def try_link(src, dst):
    try:
        os.link(src, dst)
        return True
    except (OSError, NotImplementedError):
        return False


def try_symlink(src, dst):
    try:
        os.symlink(src, dst)
        return True
    except (OSError, NotImplementedError):
        return False


def mokata_repo(d):
    from mokata.init import init_repo
    git(d, "init", "-q", "-b", "main")
    git(d, "config", "user.email", "t@t")
    git(d, "config", "user.name", "t")
    git(d, "config", "commit.gpgsign", "false")
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda *_a: None)


# ============================================================================================ G1
class _Gate(unittest.TestCase):
    """A mokata repo and the gate-guard hook, driven the way Claude Code drives it."""

    def setUp(self):
        os.environ.pop("MOKATA_SESSION_ID", None)
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.d = os.path.realpath(self._t.name)
        mokata_repo(self.d)
        write(self.d, "src/app.py", "x = 1\n")
        write(self.d, ".git/hooks/pre-commit", "#!/bin/sh\nexit 0\n")

    def gate(self, tool_input, tool="Write", cwd=None, sid="S1"):
        """(exit code, stderr) of the PreToolUse gate for one tool call. The P31 worktree policy
        is not under test (it is allowed, as in a session already in its worktree)."""
        from mokata import hook_cli
        from mokata import session_worktrees as SW
        payload = {"cwd": cwd or self.d, "session_id": sid, "tool_name": tool,
                   "tool_input": tool_input}
        err = io.StringIO()
        with mock.patch.object(hook_cli, "_read_stdin_bounded", return_value=json.dumps(payload)), \
                mock.patch.object(SW, "entry_refusal", return_value=""), \
                redirect_stdout(io.StringIO()), redirect_stderr(err):
            code = hook_cli.gate_guard_main([])
        return code, err.getvalue()

    def write_call(self, rel, content=HOOK_BODY, base=None):
        return {"file_path": os.path.join(base or self.d, *rel.split("/")), "content": content}

    def assertBlocked(self, got, what=""):
        code, err = got
        self.assertEqual(2, code, (what, err))
        self.assertIn("BLOCKED [git-control]", err, what)

    def assertAllowed(self, got, what=""):
        code, err = got
        self.assertEqual(0, code, (what, err))
        self.assertNotIn("git-control", err, what)

    def proposal_id(self, err):
        tail = err.split("mokata approve ", 1)[1]
        return tail.split("`", 1)[0].strip()


class G1_AnAgentCannotPlantAGitHookOrRewriteGitConfig(_Gate):

    def test_a_hook_write_is_refused_and_names_the_approve_command(self):
        code, err = self.gate(self.write_call(".git/hooks/pre-commit"))
        self.assertBlocked((code, err))
        self.assertIn("mokata approve p-", err)
        self.assertIn("own terminal", err)
        pid = self.proposal_id(err)
        p = approval.load(self.d, pid)
        self.assertEqual(GC.GIT_CONTROL_TOOL, p.tool)
        self.assertEqual(approval.STATUS_PROPOSED, p.status)
        shown = approval.render(p)
        self.assertIn(f"curl {MARK}", shown, "the human sees the WHOLE hook, line 31 included")

    def test_after_a_human_approves_exactly_that_write_passes_once(self):
        call = self.write_call(".git/hooks/pre-commit")
        _c, err = self.gate(call)
        pid = self.proposal_id(err)
        self.assertTrue(approval.approve(self.d, pid).ok)
        self.assertAllowed(self.gate(call), "the approved write")
        self.assertBlocked(self.gate(call), "an approval licenses exactly one write")

    def test_an_approval_does_not_license_a_different_write(self):
        _c, err = self.gate(self.write_call(".git/hooks/pre-commit", "#!/bin/sh\nexit 0\n"))
        self.assertTrue(approval.approve(self.d, self.proposal_id(err)).ok)
        self.assertBlocked(self.gate(self.write_call(".git/hooks/pre-commit", HOOK_BODY)),
                           "approve A, write B")
        self.assertBlocked(self.gate(self.write_call(".git/hooks/pre-commit", HOOK_BODY),
                                     sid="S2"), "another session")

    def test_git_config_and_the_hooks_dir_are_refused_in_every_spelling(self):
        for rel in (".git/config", ".git/config.worktree", ".GIT/HOOKS/pre-push", ".Git/Config",
                    ".git./hooks/post-merge", ".git/hooks", ".git/modules/sub/hooks/pre-commit",
                    ".git/worktrees/wt/config.worktree", "pkg/.git/hooks/pre-commit"):
            with self.subTest(rel=rel):
                self.assertBlocked(self.gate(self.write_call(rel)), rel)
        self.assertBlocked(self.gate({"file_path": ".git/hooks/pre-commit", "old_string": "exit",
                                      "new_string": "curl x"}, tool="Edit"), "a relative Edit")

    def test_a_symlinked_parent_or_a_hard_link_is_the_same_file(self):
        if try_symlink(os.path.join(self.d, ".git", "hooks"), os.path.join(self.d, "hk")):
            self.assertBlocked(self.gate(self.write_call("hk/pre-push")), "symlinked hooks dir")
        if try_link(os.path.join(self.d, ".git", "config"), os.path.join(self.d, "notes.txt")):
            self.assertBlocked(self.gate(self.write_call("notes.txt", "[core]\n")),
                               "a hard link to .git/config")
        if try_link(os.path.join(self.d, ".git", "hooks", "pre-commit"),
                    os.path.join(self.d, "run.sh")):
            self.assertBlocked(self.gate(self.write_call("run.sh")), "a hard link to a hook")

    def test_core_hooksPath_directories_are_hooks_too(self):
        git(self.d, "config", "core.hooksPath", ".githooks")
        os.makedirs(os.path.join(self.d, ".githooks"))
        self.assertBlocked(self.gate(self.write_call(".githooks/pre-commit")))
        if same_file_on_disk(os.path.join(self.d, ".GITHOOKS"), os.path.join(self.d, ".githooks")):
            self.assertBlocked(self.gate(self.write_call(".GITHOOKS/pre-commit")),
                               "a case alias of the hooks path")
        inc = write(self.d, "extra.gitconfig", "[core]\n\thooksPath = tools/hooks\n")
        git(self.d, "config", "include.path", inc)
        self.assertBlocked(self.gate(self.write_call("tools/hooks/pre-push")), "via an include")

    def test_bash_writes_and_git_config_writes_are_refused(self):
        for cmd in ("cp /tmp/x .git/hooks/pre-commit", "echo hi > .git/hooks/post-merge",
                    "tee -a .git/config < /tmp/x", "git config core.hooksPath /tmp/evil",
                    "git -C . config --add core.fsmonitor /tmp/evil",
                    "git config --global core.hooksPath /tmp/evil",
                    "git config unset core.hooksPath",
                    "git config -f .git/config alias.x '!sh'",
                    "bash -c 'git config core.hooksPath /tmp/evil'",
                    "cd .git/hooks && echo x > pre-commit", "cd .git && echo x > config",
                    "cd .git; cp /tmp/x hooks/post-merge", "pushd .GIT/HOOKS && tee pre-push"):
            with self.subTest(cmd=cmd):
                self.assertBlocked(self.gate({"command": cmd}, tool="Bash"), cmd)
        for cmd in ("git config --get user.name", "git config user.name", "git config --list",
                    "git status", "git config --global user.email a@b",
                    "git config --get-regexp alias x", "git config --get-all core.x some-value",
                    "git config get --all core.hooksPath", "cd src && echo x > hooks.py",
                    "cd .git && cat config"):
            with self.subTest(cmd=cmd):
                self.assertAllowed(self.gate({"command": cmd}, tool="Bash"), cmd)

    def test_ordinary_files_are_not_git_control(self):
        for rel in ("src/app.py", ".github/workflows/ci.yml", ".gitignore", "src/hooks/pre-commit",
                    "hooks/pre-commit", ".git-notes/config", ".githooks/pre-commit"):
            with self.subTest(rel=rel):
                self.assertAllowed(self.gate(self.write_call(rel, "x\n")), rel)

    def test_a_linked_worktree_cannot_reach_the_main_checkouts_hooks_or_its_own_pointer(self):
        write(self.d, "README.md", "r\n")
        git(self.d, "add", "-A")
        git(self.d, "commit", "-q", "-m", "init")
        wt = os.path.join(self.d, "..", os.path.basename(self.d) + "-wt")
        git(self.d, "worktree", "add", "-q", "-b", "side", wt)
        self.addCleanup(shutil.rmtree, wt, True)
        wt = os.path.realpath(wt)
        common = os.path.join(self.d, ".git")
        self.assertBlocked(self.gate({"file_path": os.path.join(common, "hooks", "pre-push"),
                                      "content": HOOK_BODY}, cwd=wt), "main's hooks from a wt")
        self.assertBlocked(self.gate(self.write_call(".git", "gitdir: /tmp/evil\n", base=wt),
                                     cwd=wt), "the worktree's .git pointer file")
        self.assertBlocked(self.gate({"command": "git config core.hooksPath /tmp/e"}, tool="Bash",
                                     cwd=wt), "git config from a worktree")
        self.assertEqual(os.path.realpath(os.path.join(common, "config")),
                         os.path.realpath(GC.git_config_writes("git config a.b c", wt)[0]))
        self.assertAllowed(self.gate(self.write_call("src/app.py", "y\n", base=wt), cwd=wt))

    def test_outside_a_mokata_repo_it_is_refused_and_says_to_make_the_change_yourself(self):
        with tempfile.TemporaryDirectory() as t:
            plain = os.path.realpath(t)
            git(plain, "init", "-q")
            code, err = self.gate(self.write_call(".git/hooks/pre-commit", base=plain), cwd=plain)
        self.assertBlocked((code, err))
        self.assertIn("make the change yourself", err)

    def test_undecidable_is_refused(self):
        with mock.patch.object(GC, "fold_component", side_effect=ValueError("cannot fold")):
            self.assertEqual(GC.HOOK, GC.kind_of("src/app.py", self.d))


# ============================================================================== G2 / G3 — reads
class _Ide(unittest.TestCase):

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.d = os.path.realpath(self._t.name)
        git(self.d, "init", "-q", "-b", "main")
        git(self.d, "config", "user.email", "t@t")
        git(self.d, "config", "user.name", "t")
        git(self.d, "config", "commit.gpgsign", "false")
        with open(os.path.join(self.d, ".git", "config"), "a", encoding="utf-8") as fh:
            fh.write(f"# {MARK}\n")
        write(self.d, ".mokata/temp_local/state/secret.json", f'{{"k": "{MARK}"}}\n')
        write(self.d, "src/app.py", "def f():\n    return 1\n")
        git(self.d, "add", "src")
        git(self.d, "commit", "-q", "-m", "init")
        # AFTER the commit: a hook with no shebang makes git for Windows fail the commit that finds it
        write(self.d, ".git/hooks/pre-commit", f"echo {MARK}\n")
        from starlette.testclient import TestClient
        from mokata.ide.server import AppState, build_app
        self.c = TestClient(build_app(AppState(self.d, TOKEN, PORT, None, False)), base_url=BASE,
                            follow_redirects=False)
        self.c.get(f"/pair?t={TOKEN}")

    def reads(self, path):
        yield "/api/file", self.c.get("/api/file", params={"path": path})
        yield "/api/diff/context", self.c.get("/api/diff/context", params={"path": path})
        yield "/api/locate", self.c.post("/api/locate", json={"text": path},
                                         headers={"Origin": BASE})

    def assertRefused(self, path, words):
        for route, r in self.reads(path):
            with self.subTest(route=route, path=path):
                self.assertEqual(400, r.status_code, r.text)
                self.assertNotIn(MARK, r.text)
                self.assertIn(words, r.json().get("note", ""), r.text)
                self.assertNotIn("outside", r.json().get("note", ""))


class G2_TheIdeNeverServesGitsStore(_Ide):

    def test_git_config_and_hooks_are_refused_in_plain_words(self):
        for path in (".git/config", ".git/hooks/pre-commit", ".GIT/config", ".git/HEAD"):
            self.assertRefused(path, "git's own store (.git/)")

    def test_mokatas_store_is_named_as_mokatas_record(self):
        self.assertRefused(".mokata/temp_local/state/secret.json", "mokata's own record")

    def test_an_ordinary_file_is_still_served(self):
        for route, r in self.reads("src/app.py"):
            with self.subTest(route=route):
                self.assertEqual(200, r.status_code, r.text)


class G3_AHardLinkIntoTheStoreIsTheStore(_Ide):

    def link(self, src_rel, dst_rel):
        if not try_link(os.path.join(self.d, *src_rel.split("/")),
                        os.path.join(self.d, *dst_rel.split("/"))):
            self.skipTest("no hard links on this filesystem")

    def test_a_hard_link_to_a_mokata_file_or_git_config_is_refused_by_every_read(self):
        self.link(".mokata/temp_local/state/secret.json", "src/notes.json")
        self.link(".git/config", "src/cfg.txt")
        self.link(".git/hooks/pre-commit", "src/hook.sh")
        for path in ("src/notes.json", "src/cfg.txt", "src/hook.sh"):
            self.assertRefused(path, "another name (a link or an alias)")

    def test_a_hard_link_is_never_a_search_hit(self):
        """Both engines (rg and the pure-Python floor) — the walk lists the link under an ordinary
        name, so the hit itself is judged."""
        from mokata.ide import search
        self.link(".mokata/temp_local/state/secret.json", "src/notes.json")
        write(self.d, "src/visible.txt", f"{MARK} in a plain file\n")
        for engine in ("rg", "floor"):
            with self.subTest(engine=engine):
                with mock.patch.object(search, "rg_path",
                                       return_value=None if engine == "floor"
                                       else search.rg_path()):
                    got = search.text(self.d, MARK)
                names = [f.get("path") for f in got["files"]]
                self.assertIn("src/visible.txt", names, got)
                self.assertNotIn("src/notes.json", names)

    def test_an_ordinary_hard_link_is_still_served(self):
        self.link("src/app.py", "src/app_alias.py")
        r = self.c.get("/api/file", params={"path": "src/app_alias.py"})
        self.assertEqual(200, r.status_code, r.text)

    def test_a_link_made_after_the_index_was_built_is_still_seen(self):
        self.link(".git/config", "src/a.txt")
        self.assertIsNone(S.rel_path(self.d, "src/a.txt"))              # builds the index
        write(self.d, ".mokata/temp_local/state/later.json", f"{MARK}\n")
        self.link(".mokata/temp_local/state/later.json", "src/later.json")
        self.assertIsNone(S.rel_path(self.d, "src/later.json"))
        self.assertEqual("src/app.py", S.rel_path(self.d, "src/app.py"))

    def test_identity_is_device_and_inode_not_the_path(self):
        self.link(".git/config", "src/cfg.txt")
        st = os.stat(os.path.join(self.d, "src", "cfg.txt"))
        self.assertTrue(E.hardlinked_private(self.d, st))
        self.assertFalse(E.hardlinked_private(self.d, os.stat(os.path.join(self.d, "src",
                                                                           "app.py"))))


# ============================================================================================ G4
class G4_TheFrontDoorNeverSpoolsAPrivateFilesBytes(unittest.TestCase):

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.d = os.path.realpath(self._t.name)
        self.repo = os.path.join(self.d, "repo")
        os.makedirs(self.repo)
        git(self.repo, "init", "-q")
        self.spool = os.path.join(self.d, "spool")
        write(self.repo, ".mokata/manifest.json", "{}\n")
        write(self.repo, ".mokata/temp_local/x.json", f"{MARK}\n")
        write(self.repo, ".git/hooks/pre-commit", f"{MARK}\n")
        write(self.repo, "src/app.py", "PLAIN-BYTES\n")

    def emit(self, rel):
        payload = {"session_id": "S1", "cwd": self.repo, "hook_event_name": "PostToolUse",
                   "tool_name": "Write",
                   "tool_input": {"file_path": os.path.join(self.repo, *rel.split("/")),
                                  "content": "x"}}
        with mock.patch.dict(os.environ, {E.SPOOL_ENV: self.spool}), \
                mock.patch.object(E, "_read_stdin_bytes", return_value=json.dumps(payload).encode()), \
                mock.patch.object(E, "_spawn_drainer", return_value=True), \
                mock.patch.object(E, "_lock_fresh", return_value=False):
            self.assertEqual(0, E.main(["dirty-track", E.VIA_PLUGIN_FLAG]))
        entries = sorted(os.listdir(self.spool))
        data = b""
        for e in entries:
            with open(os.path.join(self.spool, e), "rb") as fh:
                data += fh.read()
            os.remove(os.path.join(self.spool, e))
        return data

    def test_private_paths_are_spooled_without_a_byte_of_content(self):
        cases = [".mokata/temp_local/x.json", ".git/hooks/pre-commit", ".MOKATA/temp_local/x.json"]
        if try_link(os.path.join(self.repo, ".mokata", "temp_local", "x.json"),
                    os.path.join(self.repo, "src", "linked.json")):
            cases.append("src/linked.json")
        for rel in cases:
            with self.subTest(rel=rel):
                data = self.emit(rel)
                self.assertTrue(data, "the entry is still spooled (the path is recorded)")
                self.assertNotIn(MARK.encode(), data)
                self.assertIn(E.PRIVATE_STATE.encode(), data)

    def test_an_ordinary_file_keeps_its_hook_time_bytes(self):
        self.assertIn(b"PLAIN-BYTES", self.emit("src/app.py"))

    def test_the_drainer_records_the_front_doors_private_verdict_path_only(self):
        snap = S.capture(self.repo, "src/app.py", S.AFTER, "S1",
                         observed=(E.PRIVATE_STATE, 0, 1.0, None))
        self.assertEqual((S.PRIVATE, "", 0), (snap.state, snap.sha, snap.size))
        self.assertEqual(S.PRIVATE, E.PRIVATE_STATE)

    def test_undecidable_is_private(self):
        with mock.patch.object(E, "is_private_name", side_effect=ValueError("cannot fold")):
            self.assertTrue(E.private_path(os.path.join(self.repo, "src", "app.py")))


# ============================================================================================ G5
_LEAK = textwrap.dedent('''
    import os, sys, unittest, io
    sys.path.insert(0, %(here)r)
    import _support  # noqa: F401
    import test_setupclass_never_leaks_home as G
    os.environ[%(var)r] = "/tmp/leaked-sandbox"
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(G.NoModuleLeavesHomeChanged)
    res = unittest.TextTestRunner(stream=io.StringIO()).run(suite)
    print("CAUGHT" if res.failures or res.errors else "MISSED")
''')


class G5_TheHomeGuardWatchesEveryPerUserDirVariable(unittest.TestCase):

    def test_a_leaked_XDG_or_APPDATA_variable_reds_the_guard(self):
        here = os.path.dirname(os.path.abspath(__file__))
        for var in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
                    "XDG_RUNTIME_DIR", "APPDATA", "LOCALAPPDATA", "HOME"):
            with self.subTest(var=var):
                env = {k: v for k, v in os.environ.items() if k != var}
                out = subprocess.run([sys.executable, "-c", _LEAK % {"here": here, "var": var}],
                                     capture_output=True, text=True, cwd=here, timeout=300,
                                     env=env, stdin=subprocess.DEVNULL)
                self.assertIn("CAUGHT", out.stdout, out.stdout + out.stderr)


# ============================================================================================ G6
class G6_SpecEmitInALinkedWorktreeWritesIntoThatWorktree(unittest.TestCase):

    RUN = "run-sec6"

    def setUp(self):
        from mokata import session
        from mokata.config_cmd import config_set
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        base = os.path.realpath(self._t.name)
        self.main = os.path.join(base, "main")
        os.makedirs(self.main)
        mokata_repo(self.main)
        res = config_set(self.main, "settings.specs.commit", "true", assume_yes=True,
                         out=lambda *_a: None)
        self.assertTrue(res.committed, res)
        write(self.main, "src/pay.py", "def charge(card):\n    return card\n")
        write(self.main, "tests/test_pay.py", "def test_charge():\n    pass\n")
        git(self.main, "add", "-A")
        git(self.main, "commit", "-q", "-m", "init")
        self.wt = os.path.join(base, "wt")
        git(self.main, "worktree", "add", "-q", "-b", "feature", self.wt)
        self.assertTrue(os.path.isfile(os.path.join(self.wt, ".mokata", "manifest.json")))
        env = mock.patch.dict(os.environ, {session.SESSION_ID_ENV: self.RUN})
        env.start()
        self.addCleanup(env.stop)
        session.reset_for_test()
        self.addCleanup(session.reset_for_test)

    def committed_in(self, tree):
        from mokata import committed_specs as CS
        return os.path.isfile(CS.spec_path(tree, self.RUN))

    def args(self):
        return dict(title="payments",
                    criteria=[{"id": "AC1", "text": "charge() bills the card"}],
                    tests=[{"name": "tests/test_pay.py::test_charge", "ac_ids": ["AC1"]}],
                    tasks=[{"id": "T1", "title": "charge", "ac_ids": ["AC1"],
                            "scope": ["src/pay.py"]}])

    def test_the_mcp_emit_writes_the_committed_spec_into_the_worktree(self):
        from _support import mcp_commit
        from mokata.mcp import tools_write as TW
        res = mcp_commit(TW.spec_emit, path=self.wt, **self.args())
        self.assertTrue(res.get("committed"), res)
        self.assertTrue(self.committed_in(self.wt), "the worktree has its committed spec")
        self.assertFalse(self.committed_in(self.main), "nothing is written into the main checkout")

    def test_the_cli_emit_writes_the_committed_spec_into_the_worktree(self):
        from mokata.cli import main
        f = write(self.wt, "spec.json", json.dumps(self.args()))
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(out), \
                mock.patch("sys.stdin", io.StringIO("")):
            try:
                code = main(["spec", "emit", "--file", f, "--yes", "--path", self.wt])
            except SystemExit as exc:
                code = exc.code
        self.assertEqual(0, code, out.getvalue())
        self.assertTrue(self.committed_in(self.wt), out.getvalue())
        self.assertFalse(self.committed_in(self.main))

    def test_a_main_checkout_emit_still_writes_into_the_main_checkout(self):
        from _support import mcp_commit
        from mokata.mcp import tools_write as TW
        res = mcp_commit(TW.spec_emit, path=self.main, **self.args())
        self.assertTrue(res.get("committed"), res)
        self.assertTrue(self.committed_in(self.main))
        self.assertFalse(self.committed_in(self.wt))


if __name__ == "__main__":
    unittest.main()
