"""P31 SESSION-WORKTREES — every Claude Code session works in its own git worktree, and mokata knows
which session made which change (rulings by Jas, 2026-10-22).

Business-level, on REAL `git worktree add` checkouts laid out exactly as Claude Code lays them out
(`.claude/worktrees/<name>/` on branch `worktree-<name>`, branched from HEAD):

  * ONE shared state: from inside a worktree every mokata resolution lands in the main checkout's
    `.mokata/` — and nothing is ever created inside the worktree;
  * attribution is EXACT for a worktree session, a Bash `sed -i` included, and parallel sessions
    are isolated from each other;
  * a resumed session keeps its worktree binding (kept id and new id);
  * the gate refuses a main-checkout edit until the session enters its worktree (policy `always`),
    and degrades to off — said — outside git;
  * merging back and deleting go through the approval channel: proposed, refused until a human
    approves, done once; a delete with unmerged work is refused unless approved WITH discard;
  * a session NOT in a worktree still gets its Bash changes attributed (overlap → shared);
  * `mokata setup claude` writes `worktree.baseRef: "head"`, `.worktreeinclude`, the .gitignore line
    — idempotently.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import io
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import approval
from mokata import session_worktrees as SW
from mokata import worktree_ops as OPS
from mokata.config import Surface
from mokata.hook_cli import run_with_stdin

GIT = shutil.which("git")


def _git(d, *args):
    return subprocess.run(["git", "-C", d, *args], check=True, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL).stdout


def _write(root, rel, text):
    p = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return p


def _read(root, rel):
    with open(os.path.join(root, *rel.split("/")), encoding="utf-8") as fh:
        return fh.read()


def _bash_edit(path, old, new):
    """A change made THROUGH BASH — no Write/Edit tool path anywhere. `sed -i` where sed exists (the
    case Jas named); the same in-place rewrite by a child process elsewhere (Windows CI)."""
    sed = shutil.which("sed")
    if sed and os.name != "nt":
        # BSD sed (macOS) requires an argument to -i; GNU sed takes none. `-i.bak` + remove is the
        # one form both accept (O16: Jas's Mac failed on the GNU-only `-i`).
        subprocess.run([sed, "-i.mokbak", f"s/{old}/{new}/", path], check=True)
        bak = path + ".mokbak"
        if os.path.exists(bak):
            os.remove(bak)
    else:
        code = (f"import pathlib;p=pathlib.Path({path!r});"
                f"p.write_text(p.read_text().replace({old!r},{new!r}))")
        subprocess.run([shutil.which("python") or "python", "-c", code], check=True)


def _hook(sub, payload, env=None):
    """Run one hook as Claude Code would; `out` is the context it injected (SessionStart's JSON
    envelope unwrapped)."""
    err, out = io.StringIO(), io.StringIO()
    with mock.patch.dict(os.environ, env or {}), redirect_stderr(err), redirect_stdout(out):
        code = run_with_stdin([sub], json.dumps(payload))
    text = out.getvalue()
    try:
        text = json.loads(text)["hookSpecificOutput"]["additionalContext"]
    except (ValueError, KeyError, TypeError):
        pass
    return code, err.getvalue(), text


@unittest.skipUnless(GIT, "git is required")
class _Repo(unittest.TestCase):
    """A mokata repo in git, with Claude Code-shaped worktrees added on demand."""

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.main = os.path.realpath(os.path.join(self._t.name, "repo"))
        os.makedirs(self.main)
        _git(self.main, "init", "-q", "-b", "main")
        _git(self.main, "config", "user.email", "t@t")
        _git(self.main, "config", "user.name", "t")
        _git(self.main, "config", "commit.gpgsign", "false")
        from mokata.init import init_repo
        init_repo(root=self.main, profile="standard", assume_yes=True, out=lambda _: None)
        _write(self.main, ".gitignore", ".mokata/\n.claude/worktrees/\n")
        _write(self.main, "src/app.py", "VALUE = 'old'\n")
        _write(self.main, "README.md", "hello\n")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "init")

    def tearDown(self):
        self._t.cleanup()

    def worktree(self, name):
        """What `claude -w <name>` / EnterWorktree does, with `worktree.baseRef: "head"`."""
        path = os.path.join(self.main, ".claude", "worktrees", name)
        _git(self.main, "worktree", "add", "-q", "-b", f"worktree-{name}", path, "HEAD")
        return path

    def commit(self, tree, rel, text, msg):
        _write(tree, rel, text)
        _git(tree, "add", "-A")
        _git(tree, "commit", "-qm", msg)
        return _git(tree, "rev-parse", "HEAD").strip()

    def ide(self):
        from starlette.testclient import TestClient
        from mokata.ide.server import AppState, build_app
        state = AppState(self.main, "tok", 47999, None, False)
        c = TestClient(build_app(state), base_url="http://127.0.0.1:47999", follow_redirects=False)
        c.get("/pair?t=tok")
        return c

    def post(self, c, path, body):
        return c.post(path, json=body, headers={"Origin": "http://127.0.0.1:47999"}).json()


# ============================================================================ (2) one shared state
class OneSharedStateFromAnyWorktree(_Repo):

    def test_every_resolution_lands_in_the_main_checkouts_mokata(self):
        wt = self.worktree("a")
        from mokata import tdd_state
        from mokata.events.store import events_dir
        from mokata.gate_hook import find_mokata_root
        from mokata.govern.ledger import AuditLedger
        from mokata.ide import statedir
        main_tl = os.path.join(self.main, ".mokata", "temp_local")
        surface = Surface.load(wt)
        self.assertEqual(os.path.realpath(wt), os.path.realpath(surface.root),
                         "the working tree stays the worktree")
        self.assertEqual(os.path.join(self.main, ".mokata"), surface.mokata_dir)
        self.assertEqual(os.path.join(main_tl, "state"), tdd_state.state_dir(wt))
        self.assertEqual(os.path.join(main_tl, "events"), events_dir(wt))
        self.assertEqual(os.path.join(main_tl, "ide"), statedir.ide_dir(wt))
        self.assertTrue(AuditLedger.path_for(os.path.join(wt, ".mokata")).startswith(main_tl))
        self.assertEqual(self.main, find_mokata_root(os.path.join(wt, "src")))

    def test_per_tree_facts_are_keyed_by_the_worktree_inside_the_shared_store(self):
        wt = self.worktree("a")
        from mokata.knowledge.freshness import _dirty_dir
        here = _dirty_dir(wt)
        self.assertTrue(here.startswith(os.path.join(self.main, ".mokata", "temp_local", "trees")))
        self.assertNotEqual(here, _dirty_dir(self.main))
        self.assertNotEqual(here, _dirty_dir(self.worktree("b")))

    def test_hooks_run_in_a_worktree_write_nothing_inside_it(self):
        wt = self.worktree("a")
        _hook("session-start", {"session_id": "S1", "cwd": wt, "source": "startup"})
        _hook("gate-guard", {"session_id": "S1", "cwd": wt, "tool_name": "Write",
                             "tool_input": {"file_path": os.path.join(wt, "src", "new.py")}})
        _hook("dirty-track", {"session_id": "S1", "cwd": wt, "tool_name": "Write",
                              "tool_input": {"file_path": os.path.join(wt, "src", "app.py")}})
        self.assertFalse(os.path.exists(os.path.join(wt, ".mokata")),
                         "a second store was created inside the worktree")
        self.assertTrue(SW.binding(self.main, "S1"))


# ============================================================================ (4) attribution
class ExactAttributionInAWorktree(_Repo):

    def test_a_bash_sed_change_is_attributed_with_commits_and_untracked_files(self):
        wt = self.worktree("a")
        _hook("session-start", {"session_id": "S1", "cwd": wt, "source": "startup"})
        self.commit(wt, "src/feature.py", "def f():\n    return 1\n", "feature")   # committed
        _bash_edit(os.path.join(wt, "src", "app.py"), "old", "new")              # Bash sed -i
        _write(wt, "notes/scratch.txt", "untracked\n")                           # untracked
        d = self.ide().get("/api/worktree/diff", params={"session": "S1"}).json()
        self.assertEqual("worktree", d["mode"])
        paths = {f["path"]: f for f in d["files"]}
        self.assertEqual({"src/app.py", "src/feature.py", "notes/scratch.txt"}, set(paths))
        added = [ln[3] for h in paths["src/app.py"]["hunks"] for ln in h["lines"] if ln[0] == "+"]
        self.assertEqual(["VALUE = 'new'"], added, "the sed -i change itself is in the diff")

    def test_parallel_sessions_are_isolated(self):
        a, b = self.worktree("a"), self.worktree("b")
        _hook("session-start", {"session_id": "S1", "cwd": a, "source": "startup"})
        _hook("session-start", {"session_id": "S2", "cwd": b, "source": "startup"})
        _bash_edit(os.path.join(a, "src", "app.py"), "old", "from-a")
        _write(b, "README.md", "changed by b\n")
        self.assertEqual("VALUE = 'old'\n", _read(b, "src/app.py"), "b saw a's edit")
        self.assertEqual("VALUE = 'old'\n", _read(self.main, "src/app.py"), "main saw a's edit")
        s1 = {f["path"] for f in SW.session_changes(self.main, "S1")["files"]}
        s2 = {f["path"] for f in SW.session_changes(self.main, "S2")["files"]}
        self.assertEqual({"src/app.py"}, s1)
        self.assertEqual({"README.md"}, s2)

    def test_the_worktrees_list_names_session_branch_base_and_counts(self):
        wt = self.worktree("a")
        base = _git(self.main, "rev-parse", "HEAD").strip()
        _hook("session-start", {"session_id": "S1", "cwd": wt, "source": "startup"})
        self.commit(wt, "src/x.py", "x = 1\n", "x")
        _write(wt, "src/y.py", "y\n")
        rows = self.ide().get("/api/worktrees").json()
        [r] = rows["worktrees"]
        self.assertEqual(("S1", "worktree-a", base, 1, 0, 1, False),
                         (r["session"], r["branch"], r["base"], r["ahead"], r["behind"], r["dirty"],
                          r["merged"]))
        self.assertEqual("claude --resume S1", r["resume"])
        self.assertTrue(r["claude_managed"])


class ASubagentNeverMovesItsSessionsBinding(_Repo):

    def test_an_isolated_subagents_worktree_is_not_the_sessions(self):
        a, sub = self.worktree("a"), self.worktree("agent-1")
        _hook("session-start", {"session_id": "S1", "cwd": a, "source": "startup"})
        _hook("gate-guard", {"session_id": "S1", "agent_id": "ag1", "cwd": sub, "tool_name": "Write",
                             "tool_input": {"file_path": os.path.join(sub, "x.py")}})
        _hook("dirty-track", {"session_id": "S1", "agent_id": "ag1", "cwd": sub,
                              "tool_name": "Write", "tool_input": {"file_path": os.path.join(sub, "x.py")}})
        self.assertEqual(os.path.realpath(a), os.path.realpath(SW.binding(self.main, "S1")["path"]))


# ============================================================================ resume
class ResumeKeepsTheWorktreeBinding(_Repo):

    def _transcript(self, *sids):
        p = os.path.join(self._t.name, "t.jsonl")
        with open(p, "w", encoding="utf-8") as fh:
            for s in sids:
                fh.write(json.dumps({"sessionId": s, "type": "user"}) + "\n")
        return p

    def test_a_new_id_resume_carries_the_predecessors_worktree(self):
        wt = self.worktree("a")
        _hook("session-start", {"session_id": "S1", "cwd": wt, "source": "startup"})
        _hook("session-start", {"session_id": "S1b", "cwd": self.main, "source": "fork",
                                "transcript_path": self._transcript("S1", "S1b")})
        b = SW.binding(self.main, "S1b")
        self.assertEqual(os.path.realpath(wt), os.path.realpath(b["path"]))
        self.assertEqual("S1", b["carried_from"])

    def test_a_kept_id_resume_from_the_main_checkout_is_sent_back_to_its_worktree(self):
        wt = self.worktree("a")
        _hook("session-start", {"session_id": "S1", "cwd": wt, "source": "startup"})
        _c, _e, out = _hook("session-start", {"session_id": "S1", "cwd": self.main,
                                              "source": "resume"})
        self.assertEqual(os.path.realpath(wt), os.path.realpath(SW.binding(self.main, "S1")["path"]))
        self.assertIn(f'EnterWorktree tool with path "{wt}"', out)
        code, err, _o = _hook("gate-guard", {"session_id": "S1", "cwd": self.main, "tool_name": "Edit",
                                             "tool_input": {"file_path": os.path.join(self.main, "README.md")}})
        self.assertEqual(2, code)
        self.assertIn(wt, err, "the refusal names the session's OWN worktree, not a new one")


# ============================================================================ (3) the gate
class TheGateWaitsForTheWorktree(_Repo):

    def _write_call(self, cwd, path, sid="S1"):
        return _hook("gate-guard", {"session_id": sid, "cwd": cwd, "tool_name": "Write",
                                    "tool_input": {"file_path": path, "content": "x = 1\n"}})

    def test_a_main_checkout_edit_is_refused_until_the_session_enters_its_worktree(self):
        code, err, _ = self._write_call(self.main, os.path.join(self.main, "src", "x.py"))
        self.assertEqual(2, code)
        self.assertIn("enter your worktree first", err)
        self.assertIn('EnterWorktree tool with name "mk-s1"', err)
        wt = self.worktree("mk-s1")
        code, err, _ = self._write_call(wt, os.path.join(wt, "src", "x.py"))
        self.assertEqual(0, code, err)
        self.assertNotIn("worktree", err)

    def test_a_bash_write_to_the_main_checkout_is_refused_too(self):
        code, err, _ = _hook("gate-guard", {"session_id": "S1", "cwd": self.main, "tool_name": "Bash",
                                            "tool_input": {"command": "echo hi > src/out.txt"}})
        self.assertEqual(2, code)
        self.assertIn("enter your worktree first", err)
        code, _e, _o = _hook("gate-guard", {"session_id": "S1", "cwd": self.main, "tool_name": "Bash",
                                            "tool_input": {"command": "git status"}})
        self.assertEqual(0, code, "a read-only command must still run (it is how Claude looks around)")

    def test_policy_off_lets_the_main_checkout_be_edited(self):
        from mokata.config_cmd import config_set
        res = config_set(self.main, SW.CONFIG_KEY, "off", assume_yes=True, out=lambda _: None)
        self.assertTrue(res.committed, res.message)
        code, _e, _o = self._write_call(self.main, os.path.join(self.main, "src", "x.py"))
        self.assertEqual(0, code)

    def test_the_briefing_instructs_enter_worktree_before_the_first_edit(self):
        _c, _e, out = _hook("session-start", {"session_id": "S9", "cwd": self.main,
                                              "source": "startup"})
        self.assertIn("BEFORE your first edit", out)
        self.assertIn('EnterWorktree tool with name "mk-s9"', out)


class NoGitDegradesToOff(unittest.TestCase):

    def test_a_non_git_mokata_repo_is_never_blocked_and_is_told_once(self):
        with tempfile.TemporaryDirectory() as d:
            d = os.path.realpath(d)
            from mokata.init import init_repo
            init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
            with mock.patch.object(SW, "is_git", return_value=False):
                code, err, _ = _hook("gate-guard", {"session_id": "S1", "cwd": d, "tool_name": "Write",
                                                    "tool_input": {"file_path": os.path.join(d, "a.py")}})
                self.assertEqual(0, code, err)
                _c, _e, out = _hook("session-start", {"session_id": "S1", "cwd": d,
                                                      "source": "startup"})
            self.assertIn("session worktrees are off here — not a git repository", out)


# ============================================================================ (5) merge / delete
class MergeAndDeleteAreGated(_Repo):

    def _proposed(self, c, action, body):
        r = self.post(c, f"/api/worktree/{action}", body)
        self.assertEqual("proposed", r["stage"], r)
        return r["proposal_id"]

    def test_merge_waits_for_the_human_then_merges_and_cleans_up(self):
        wt = self.worktree("a")
        _hook("session-start", {"session_id": "S1", "cwd": wt, "source": "startup"})
        tip = self.commit(wt, "src/feature.py", "F = 1\n", "feature")
        before = _git(self.main, "rev-parse", "HEAD").strip()
        c = self.ide()
        pid = self._proposed(c, "merge", {"path": wt})
        self.assertEqual(before, _git(self.main, "rev-parse", "HEAD").strip(), "proposing merged")
        r = self.post(c, "/api/worktree/merge", {"path": wt, "proposal_id": pid})
        self.assertEqual(("refused", approval.REFUSED_NOT_APPROVED), (r["stage"], r["code"]))
        approval.approve(self.main, pid)                       # what `mokata approve <id>` does
        r = self.post(c, "/api/worktree/merge", {"path": wt, "proposal_id": pid})
        self.assertEqual("done", r["stage"], r)
        self.assertTrue(r["result"]["merged"] and r["result"]["removed"], r)
        _git(self.main, "merge-base", "--is-ancestor", tip, "HEAD")      # the work is in main
        self.assertFalse(os.path.exists(wt), "the worktree was not removed after the merge")
        self.assertEqual("", _git(self.main, "branch", "--list", "worktree-a").strip())
        r = self.post(c, "/api/worktree/merge", {"path": wt, "proposal_id": pid})
        self.assertNotEqual("done", r["stage"], "an approval merged twice")

    def test_a_dirty_worktree_is_not_merged_and_conflicts_are_shown_first(self):
        wt = self.worktree("a")
        self.commit(wt, "src/app.py", "VALUE = 'a'\n", "a")
        self.commit(self.main, "src/app.py", "VALUE = 'main'\n", "main")
        c = self.ide()
        r = self.post(c, "/api/worktree/merge", {"path": wt})
        self.assertEqual("blocked", r["stage"])
        self.assertIn("conflict", r["reason"])
        self.assertIn("src/app.py", r["reason"])
        _write(wt, "README.md", "dirty\n")
        r = self.post(c, "/api/worktree/merge", {"path": wt})
        self.assertIn("uncommitted", r["reason"])

    def test_a_merge_approved_then_moved_is_refused(self):
        wt = self.worktree("a")
        self.commit(wt, "src/f.py", "F = 1\n", "f")
        c = self.ide()
        pid = self._proposed(c, "merge", {"path": wt})
        approval.approve(self.main, pid)
        self.commit(wt, "src/g.py", "G = 1\n", "g after approval")
        r = self.post(c, "/api/worktree/merge", {"path": wt, "proposal_id": pid})
        self.assertEqual(("refused", approval.REFUSED_CONTENT_CHANGED), (r["stage"], r["code"]))
        self.assertTrue(os.path.isdir(wt))

    def test_delete_refuses_unmerged_work_unless_approved_with_discard(self):
        wt = self.worktree("a")
        self.commit(wt, "src/f.py", "F = 1\n", "unmerged")
        c = self.ide()
        r = self.post(c, "/api/worktree/delete", {"path": wt})
        self.assertEqual("blocked", r["stage"])
        self.assertIn("1 commit(s) not in the main checkout", r["reason"])
        self.assertTrue(os.path.isdir(wt))
        pid = self._proposed(c, "delete", {"path": wt, "discard": True})
        r = self.post(c, "/api/worktree/delete", {"path": wt, "discard": True, "proposal_id": pid})
        self.assertEqual("refused", r["stage"], "deleted without a human approval")
        self.assertTrue(os.path.isdir(wt))
        approval.approve(self.main, pid)
        r = self.post(c, "/api/worktree/delete", {"path": wt, "discard": True, "proposal_id": pid})
        self.assertEqual("done", r["stage"], r)
        self.assertFalse(os.path.exists(wt))

    def test_clean_merged_removes_only_merged_clean_worktrees(self):
        merged, work = self.worktree("m"), self.worktree("w")
        tip = self.commit(merged, "src/m.py", "M = 1\n", "m")
        _git(self.main, "merge", "-q", "--no-ff", "--no-edit", "worktree-m")
        self.commit(work, "src/w.py", "W = 1\n", "w")
        c = self.ide()
        r = self.post(c, "/api/worktree/clean", {})
        self.assertEqual([merged], [os.path.realpath(p) for p in r["plan"]["paths"]])
        approval.approve(self.main, r["proposal_id"])
        r = self.post(c, "/api/worktree/clean", {"proposal_id": r["proposal_id"]})
        self.assertEqual("done", r["stage"], r)
        self.assertFalse(os.path.exists(merged))
        self.assertTrue(os.path.isdir(work), "a worktree with work was cleaned")
        self.assertTrue(tip)


# ============================================================================ fallback attribution
class BashAttributionWithoutAWorktree(_Repo):

    def setUp(self):
        super().setUp()
        from mokata.config_cmd import config_set
        config_set(self.main, SW.CONFIG_KEY, "off", assume_yes=True, out=lambda _: None)

    def _bash(self, sid, call, edit):
        _hook("gate-guard", {"session_id": sid, "cwd": self.main, "tool_name": "Bash",
                             "tool_use_id": call, "tool_input": {"command": "sed -i s/a/b/ x"}})
        edit()
        _hook("dirty-track", {"session_id": sid, "cwd": self.main, "tool_name": "Bash",
                              "tool_use_id": call, "tool_input": {"command": "sed -i s/a/b/ x"}},
              env={"MOKATA_HOOK_T_NS": str(time.time_ns())})

    def test_a_bash_change_is_attributed_to_the_session_that_ran_it(self):
        time.sleep(0.06)
        _write(self.main, "src/untouched_before.py", "x\n")     # changed BEFORE any window
        time.sleep(0.12)
        self._bash("S1", "c1", lambda: _bash_edit(os.path.join(self.main, "src", "app.py"),
                                                  "old", "new"))
        att = SW.attribution(self.main, "S1")
        self.assertEqual(["src/app.py"], sorted(att))
        entry = att["src/app.py"]
        self.assertEqual("bash", entry["how"])
        self.assertEqual(("M", []), (entry["status"], entry["shared"]))
        self.assertEqual(64, len(att["src/app.py"]["sha256"]))
        d = SW.session_changes(self.main, "S1")
        self.assertEqual("fallback", d["mode"])

    def test_overlapping_sessions_mark_the_path_shared(self):
        root = self.main
        SW.bash_begin(root, "S1", "c1", root)
        SW.bash_begin(root, "S2", "c2", root)               # S2's call is running at the same time
        _bash_edit(os.path.join(root, "src", "app.py"), "old", "new")
        SW.bash_end(root, "S1", "c1", root)
        SW.bash_end(root, "S2", "c2", root)
        self.assertEqual(["S2"], SW.attribution(root, "S1")["src/app.py"]["shared"])
        self.assertIn("S1", SW.attribution(root, "S2")["src/app.py"]["shared"])

    def test_a_deletion_is_attributed(self):
        SW.bash_end(self.main, "S0", "c0", self.main)       # a baseline scan with nothing open
        SW.bash_begin(self.main, "S1", "c1", self.main)
        os.remove(os.path.join(self.main, "README.md"))
        self.assertEqual(["README.md"], SW.bash_end(self.main, "S1", "c1", self.main))
        self.assertEqual("D", SW.attribution(self.main, "S1")["README.md"]["status"])


class TheEmitterSkipsWorktreeBash(_Repo):

    def test_only_a_bash_call_outside_a_worktree_is_spooled(self):
        import mokata_emit as E
        wt = self.worktree("a")
        self.assertFalse(E.bash_needs_tracking({"cwd": wt}))
        self.assertTrue(E.bash_needs_tracking({"cwd": os.path.join(self.main, "src")}))
        self.assertEqual("MOKATA_HOOK_T_NS", E.HOOK_TIME_ENV)
        from mokata import hook_cli
        self.assertEqual(hook_cli.HOOK_TIME_ENV, E.HOOK_TIME_ENV)


# ============================================================================ (1) setup
class SetupWritesClaudeCodesWorktreeSettings(_Repo):

    def _setup(self):
        from mokata.harness_setup import setup_harness
        out = []
        res = setup_harness("claude", root=self.main, scope="project", assume_yes=True,
                            out=out.append, home=os.path.join(self._t.name, "home"))
        return res, "\n".join(out)

    def test_setup_sets_base_ref_head_worktreeinclude_and_gitignore_idempotently(self):
        res, text = self._setup()
        self.assertFalse(res.aborted, res.message)
        settings = json.loads(_read(self.main, ".claude/settings.json"))
        self.assertEqual({"baseRef": "head"}, settings["worktree"])
        inc = _read(self.main, ".worktreeinclude").splitlines()
        for pat in (".env", ".env.*", ".claude/settings.local.json"):
            self.assertIn(pat, inc)
        self.assertNotIn(".mokata/", inc)
        self.assertIn("Session worktrees", text)
        self.assertIn("[will set]", text.split("Session worktrees", 1)[1])
        snap = {p: _read(self.main, p) for p in (".worktreeinclude", ".gitignore")}
        res, text = self._setup()
        after = text.split("Session worktrees", 1)[1]
        self.assertNotIn("[will set]", after.split("\n\n")[0])
        self.assertEqual(snap, {p: _read(self.main, p) for p in snap})
        self.assertEqual(1, _read(self.main, ".gitignore").count(".claude/worktrees/"))


class DoctorStatesThePolicy(_Repo):

    def test_doctor_says_where_sessions_may_edit(self):
        from mokata.govern.doctor import session_worktree_findings
        [f] = session_worktree_findings(Surface.load(self.main))
        self.assertEqual("session-worktrees", f.code)
        self.assertIn("= always", f.detail)


if __name__ == "__main__":
    unittest.main()
