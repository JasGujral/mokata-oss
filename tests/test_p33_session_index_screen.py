"""0.0.26 P33 — SESSION-INDEX+SCREEN: every Claude Code session that ran in the repo is listed, and
clicking one shows the files it changed and their diffs.

Jas's report: "not seeing files changes by session other than plan under session — not seeing all
the historical sessions". Audit B measured 17 sessions on disk and 13 listed (brainstorm-only,
Bash-only and blocked sessions never appeared; the snapshot prune dropped 146 of 400; the timeline
capped at 500), a session click in Sessions mode loaded its diff into a hidden view (B-F1), "Show
this write" diffed against the working tree (B-F2), files over 1 MB vanished from a session's diff
(B-F5), and "live" meant "wrote within 15 minutes" (B-F12).

Each test is named for the PRD row it proves (R6–R13, R28, G1/G17, G23) and drives the REAL hooks
(`run_with_stdin`, as Claude Code does) and the REAL IDE app (`build_app` + TestClient).
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import session_index as SI
from mokata.hook_cli import run_with_stdin
from mokata.ide import diffs as D
from mokata.ide import sessions as SS
from mokata.ide import snapshots as S

GIT = shutil.which("git")
PORT = 47993


def _git(d, *args):
    return subprocess.run(["git", "-C", d, *args], check=True, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL).stdout


def _write(root, rel, text):
    p = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return p


def _hook(sub, payload, env=None):
    with mock.patch.dict(os.environ, env or {}), redirect_stderr(io.StringIO()), \
            redirect_stdout(io.StringIO()):
        return run_with_stdin([sub], json.dumps(payload))


@unittest.skipUnless(GIT, "git is required")
class _Repo(unittest.TestCase):
    """A git repo with mokata set up, session worktrees OFF (sessions edit the main checkout, the
    P31 fallback) unless a test enters a worktree itself, and Claude's transcripts in a temp dir."""

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(os.path.join(self._t.name, "repo"))
        os.makedirs(self.root)
        _git(self.root, "init", "-q", "-b", "main")
        _git(self.root, "config", "user.email", "t@t")
        _git(self.root, "config", "user.name", "t")
        _git(self.root, "config", "commit.gpgsign", "false")
        from mokata.init import init_repo
        init_repo(root=self.root, profile="standard", assume_yes=True, out=lambda _: None)
        from mokata.config_cmd import config_set
        from mokata import session_worktrees as SW
        config_set(self.root, SW.CONFIG_KEY, "off", assume_yes=True, out=lambda _: None)
        _write(self.root, ".gitignore", ".mokata/\n.claude/worktrees/\n")
        _write(self.root, "src/app.py", "".join(f"line {n}\n" for n in range(1, 41)))
        _write(self.root, "src/other.py", "x = 1\n")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", "base")
        self.claude_home = os.path.join(self._t.name, "claude-home")
        os.makedirs(self.claude_home)
        self._env = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": self.claude_home})
        self._env.start()
        self.clock = time.time() - 3600

    def tearDown(self):
        self._env.stop()
        self._t.cleanup()

    # ---- a session, as Claude Code drives mokata's hooks -------------------------------------
    def env(self, sid, **kw):
        return {"session_id": sid, "cwd": self.root, **kw}

    def start(self, sid, source="startup", cwd=None, transcript=None):
        p = self.env(sid, source=source)
        if cwd:
            p["cwd"] = cwd
        if transcript:
            p["transcript_path"] = transcript
        _hook("session-start", p)

    def prompt(self, sid, text):
        _hook("user-prompt-submit", self.env(sid, prompt=text))

    def end(self, sid, reason="prompt_input_exit"):
        _hook("session-end", self.env(sid, reason=reason))

    def edit(self, sid, rel, new_text):
        """A native Write: gate-guard, the write, dirty-track (as in Claude Code)."""
        env = self.env(sid, tool_name="Write",
                       tool_input={"file_path": os.path.join(self.root, rel),
                                   "content": new_text})
        rc = _hook("gate-guard", env)
        self.assertEqual(0, rc, f"the gate refused {rel}")
        _write(self.root, rel, new_text)
        _hook("dirty-track", env)

    def bash(self, sid, call, edit):
        """A Bash edit outside a worktree: P31's call-window attribution."""
        cmd = {"command": "sed -i s/a/b/ x"}
        _hook("gate-guard", self.env(sid, tool_name="Bash", tool_use_id=call, tool_input=cmd))
        time.sleep(0.06)
        edit()
        _hook("dirty-track", self.env(sid, tool_name="Bash", tool_use_id=call, tool_input=cmd),
              env={"MOKATA_HOOK_T_NS": str(time.time_ns())})

    def ide(self):
        from starlette.testclient import TestClient
        from mokata.ide.server import AppState, build_app
        state = AppState(self.root, "tok", PORT, None, False)
        c = TestClient(build_app(state), base_url=f"http://127.0.0.1:{PORT}",
                       follow_redirects=False)
        c.get("/pair?t=tok")
        return c

    def focus(self, c, sid):
        r = c.post("/api/focus", json={"session": sid},
                   headers={"Origin": f"http://127.0.0.1:{PORT}"})
        self.assertEqual(200, r.status_code, r.text)

    def listing(self, c, **q):
        r = c.get("/api/sessions/list", params=q)
        self.assertEqual(200, r.status_code, r.text)
        return r.json()


# ===================================================================================== the index
class TheIndexIsWrittenByTheHooks(_Repo):

    def test_start_prompt_end_make_one_durable_row(self):
        self.start("S1")
        self.prompt("S1", "tidy   up the\nslugify docstring")
        self.prompt("S1", "and a second prompt")
        self.end("S1")
        rec = SI.read(self.root, "S1")
        self.assertEqual("tidy up the slugify docstring", rec["prompt"], "first prompt labels it")
        self.assertEqual(1, len(rec["segments"]))
        seg = rec["segments"][0]
        self.assertEqual(("startup", "main", "prompt_input_exit"),
                         (seg["source"], seg["branch"], seg["end_reason"]))
        self.assertGreaterEqual(seg["end"], seg["start"])
        self.assertTrue(rec["path"] if "path" in rec else True)
        self.assertTrue(SI.index_dir(self.root).startswith(
            os.path.join(self.root, ".mokata", "temp_local")),
            "mokata's own run-state, under temp_local — never the user's files")

    def test_a_prompt_carrying_a_secret_is_never_stored(self):
        self.start("S1")
        self.prompt("S1", "deploy with AKIAIOSFODNN7EXAMPLE please")
        rec = SI.read(self.root, "S1")
        self.assertEqual(SI.WITHHELD, rec["prompt"])
        with open(SI.path_of(self.root, "S1"), encoding="utf-8") as fh:
            self.assertNotIn("AKIA", fh.read())

    def test_later_prompts_move_only_the_heartbeat_not_a_byte(self):
        # 0.0.26 O12b — deterministic: each hook runs at an EXPLICIT hook time (the stamp a spooled
        # call carries), one second apart, instead of a sleep racing the filesystem clock. The old
        # flake was a leaked `MOKATA_HOOK_T_NS` (pinned in `test_o12b_sessionstart_budget`).
        t0 = time.time()
        self.start("S1")
        with mock.patch.dict(os.environ, {SI.HOOK_TIME_ENV: str(int(t0 * 1e9))}):
            self.prompt("S1", "first")
        path = SI.path_of(self.root, "S1")
        with open(path, "rb") as fh:
            before = fh.read()
        m0 = os.path.getmtime(path)
        with mock.patch.dict(os.environ, {SI.HOOK_TIME_ENV: str(int((t0 + 1.0) * 1e9))}):
            self.prompt("S1", "second")
        with open(path, "rb") as fh:
            self.assertEqual(before, fh.read(), "a per-turn hook rewrote the record")
        self.assertAlmostEqual(t0 + 1.0, os.path.getmtime(path), delta=0.01,
                               msg="the heartbeat did not move to the second prompt's hook time")
        self.assertGreater(os.path.getmtime(path), m0)

    def test_a_drained_session_end_is_stamped_at_hook_time(self):
        self.start("S1")
        t_hook = time.time() - 120
        _hook("session-end", self.env("S1", reason="logout"),
              env={"MOKATA_HOOK_T_NS": str(int(t_hook * 1e9))})
        self.assertAlmostEqual(t_hook, SI.read(self.root, "S1")["segments"][-1]["end"], delta=1)


# ============================================================================= R6 every session
class R6EverySessionIsListed(_Repo):

    def test_R6_read_only_brainstorm_only_blocked_bash_only_and_pruned_sessions_are_listed(self):
        # plain edits
        self.start("S-edit"); self.prompt("S-edit", "fix the app"); \
            self.edit("S-edit", "src/app.py", "changed\n"); self.end("S-edit")
        # brainstorm only: no write at all
        self.start("S-brain"); self.prompt("S-brain", "/mokata:brainstorm should we split it?")
        self.end("S-brain")
        # read-only: a question, no write
        self.start("S-read"); self.prompt("S-read", "explain src/other.py"); self.end("S-read")
        # Bash-only (no Write/Edit, so no snapshot)
        self.start("S-bash"); self.prompt("S-bash", "bump via sed")
        self.bash("S-bash", "c1", lambda: _write(self.root, "src/other.py", "x = 2\n"))
        self.end("S-bash")
        # blocked: every write it tried was refused, so nothing landed (B-F4's victims)
        self.start("S-blocked"); self.prompt("S-blocked", "edit the forms"); self.end("S-blocked")
        c = self.ide()
        d = self.listing(c)
        ids = {r["id"]: r for r in d["rows"]}
        want = {"S-edit", "S-brain", "S-read", "S-bash", "S-blocked"}
        self.assertEqual(want, set(ids), "every SessionStart is a row")
        self.assertEqual(5, d["total"])
        self.assertEqual("brainstorm only", ids["S-brain"]["kind"])
        self.assertEqual("read-only session", ids["S-read"]["kind"])
        self.assertEqual("changed", ids["S-bash"]["kind"])
        self.assertIn("src/other.py", ids["S-bash"]["files"])
        # the snapshot store pruned away (B-F3 #3): the rows stay — the index is not the snapshots
        shutil.rmtree(S.store_dir(self.root))
        self.assertEqual(want, {r["id"] for r in self.listing(c)["rows"]})

    def test_R6_no_cap_the_list_is_paginated(self):
        for n in range(420):
            SI.on_start(self.root, f"S{n:04d}", "startup", self.root, t=self.clock + n, proc=None)
        c = self.ide()
        seen, offset = [], 0
        while True:
            d = self.listing(c, offset=offset, limit=50)
            self.assertLessEqual(len(d["rows"]), 50)
            seen += [r["id"] for r in d["rows"]]
            offset += 50
            if offset >= d["total"]:
                break
        self.assertEqual(420, d["total"])
        self.assertEqual(420, len(set(seen)), "every session on exactly one page")
        self.assertEqual("S0419", seen[0], "newest first")

    def test_R6_sessions_from_before_the_index_come_from_claude_codes_transcripts(self):
        folder = os.path.join(self.claude_home, "projects", SS.project_slug(self.root))
        os.makedirs(folder)
        with open(os.path.join(folder, "OLD-1.jsonl"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "summary", "summary": "x"}) + "\n")
            fh.write(json.dumps({"type": "user", "isMeta": True, "sessionId": "OLD-1",
                                 "timestamp": "2026-09-01T10:00:00Z",
                                 "message": {"role": "user", "content": "<caveat>"}}) + "\n")
            fh.write(json.dumps({"type": "user", "sessionId": "OLD-1", "cwd": self.root,
                                 "gitBranch": "main", "timestamp": "2026-09-01T10:00:01Z",
                                 "message": {"role": "user", "content": [
                                     {"type": "text", "text": "an old question"}]}}) + "\n")
        old = time.time() - 30 * 86400
        os.utime(os.path.join(folder, "OLD-1.jsonl"), (old, old))
        rows = {r["id"]: r for r in self.listing(self.ide())["rows"]}
        self.assertIn("OLD-1", rows)
        self.assertEqual("an old question", rows["OLD-1"]["prompt"])
        self.assertEqual("ended", rows["OLD-1"]["status"])
        self.assertIn("transcript", rows["OLD-1"]["known_by"])


# ============================================================================= R7 the row
class R7RowsAreIdentifiable(_Repo):

    def test_R7_row_shows_prompt_start_duration_status_branch_run_and_files(self):
        self.start("S05")
        self.prompt("S05", "S05 brainstorm-free edit of app")
        self.edit("S05", "src/app.py", "new\n")
        self.end("S05")
        c = self.ide()
        row = self.listing(c, q="S05 brainstorm-free")["rows"][0]
        self.assertEqual("S05 brainstorm-free edit of app", row["prompt"])
        self.assertEqual(("ended", "main"), (row["status"], row["branch"]))
        self.assertGreater(row["started"], 0)
        self.assertGreaterEqual(row["duration"], 0)
        self.assertEqual(["src/app.py"], row["files"])
        st = c.get("/api/sessions/stats", params={"ids": "S05"}).json()["stats"]["S05"]
        self.assertEqual((1, 1, 40), (st["files"], st["added"], st["removed"]))

    def test_R13_filter_by_prompt_file_and_live(self):
        self.start("A"); self.prompt("A", "alpha work"); self.edit("A", "src/app.py", "a\n")
        self.end("A")
        self.start("B"); self.prompt("B", "beta work")
        c = self.ide()
        self.assertEqual(["A"], [r["id"] for r in self.listing(c, q="alpha")["rows"]])
        self.assertEqual(["A"], [r["id"] for r in self.listing(c, q="src/app.py")["rows"]])
        self.assertEqual(["B"], [r["id"] for r in self.listing(c, filter="live")["rows"]])
        self.assertEqual(400, c.get("/api/sessions/list", params={"filter": "x"}).status_code)


# ============================================================================= R8 one diff source
class R8ClickingASessionShowsItsChanges(_Repo):

    def test_R8_session_changes_include_writes_and_bash_edits(self):
        self.start("S2")
        self.edit("S2", "src/app.py", "rewritten\n")
        self.bash("S2", "c1", lambda: _write(self.root, "src/other.py", "x = 99\n"))
        c = self.ide()
        d = c.get("/api/session", params={"id": "S2"}).json()
        self.assertEqual({"src/app.py", "src/other.py"}, {f["path"] for f in d["files"]})
        self.assertEqual("approximate", d["basis"], "a shared-checkout session says so")
        self.assertTrue(all(f["hunks"] for f in d["files"]))

    def test_G1_the_worktree_panel_and_the_session_screen_read_one_diff(self):
        self.start("S2")
        self.edit("S2", "src/app.py", "rewritten\n")
        c = self.ide()
        a = c.get("/api/session", params={"id": "S2"}).json()
        b = c.get("/api/worktree/diff", params={"session": "S2"}).json()
        self.assertEqual(a["files"], b["files"])
        self.assertNotIn("sessions", c.get("/api/state").json(),
                         "the state no longer carries a second session list")

    def test_R8_a_worktree_session_is_exact_and_includes_bash(self):
        wt = os.path.join(self.root, ".claude", "worktrees", "w1")
        _git(self.root, "worktree", "add", "-q", "-b", "worktree-w1", wt)
        self.start("SW", cwd=wt)
        _hook("dirty-track", {"session_id": "SW", "cwd": wt, "tool_name": "Read",
                              "tool_input": {}})                       # binds (P31)
        _write(wt, "src/app.py", "edited in the worktree by bash\n")
        _write(wt, "notes.txt", "new\n")
        c = self.ide()
        d = c.get("/api/session", params={"id": "SW"}).json()
        self.assertEqual("exact", d["basis"])
        self.assertEqual({"src/app.py", "notes.txt"}, {f["path"] for f in d["files"]})
        row = [r for r in self.listing(c)["rows"] if r["id"] == "SW"][0]
        self.assertEqual("worktree-w1", row["branch"])


# ============================================================================= R9 per write
class R9PerWriteDiff(_Repo):

    def test_R9_write_n_is_the_previous_capture_to_this_capture(self):
        base = "".join(f"line {n}\n" for n in range(1, 41))
        self.start("S01")
        v1 = base.replace("line 5\n", "line 5 ONE\n")
        v2 = v1.replace("line 20\n", "line 20 TWO\n")
        v3 = v2.replace("line 35\n", "line 35 THREE\n")
        for v in (v1, v2, v3):
            self.edit("S01", "src/app.py", v)
        writes = [s for s in S.timeline(self.root, session="S01") if s.kind == S.AFTER]
        self.assertEqual(3, len(writes))
        c = self.ide()

        def added(snap):
            d = c.get("/api/session/write", params={"snap": snap.id}).json()
            return d, [ln[3] for f in d["files"] for h in f["hunks"] for ln in h["lines"]
                       if ln[0] == "+"]
        d2, add2 = added(writes[1])
        self.assertEqual(["line 20 TWO"], add2, "write 2 shows ONLY write 2's change")
        self.assertIn("write 2 of 3", d2["title"])
        _d3, add3 = added(writes[2])
        self.assertEqual(["line 35 THREE"], add3, "the last write is not 'same content' (B-F2)")


# ============================================================================= R10 > 1 MB
class R10TooLargeFilesAreRows(_Repo):

    def test_R10_a_file_over_the_cap_is_a_too_large_row_not_dropped(self):
        self.start("SB")
        self.edit("SB", "src/app.py", "small change\n")
        big = "x" * (S.MAX_FILE_BYTES + 10) + "\n"
        self.edit("SB", "gen/huge.txt", big)
        d = self.ide().get("/api/session", params={"id": "SB"}).json()
        files = {f["path"]: f for f in d["files"]}
        self.assertEqual({"src/app.py", "gen/huge.txt"}, set(files))
        self.assertTrue(files["gen/huge.txt"]["too_large"])
        self.assertEqual(1, files["gen/huge.txt"]["writes"])
        self.assertFalse(files["src/app.py"]["too_large"])


# ============================================================================= R11 resume chains
class R11AResumedChainIsOneRow(_Repo):

    def test_R11_a_kept_id_resume_is_one_row_with_one_cumulative_diff(self):
        self.start("A"); self.prompt("A", "first part")
        self.edit("A", "src/app.py", "part one\n"); self.end("A")
        self.start("A", source="resume"); self.prompt("A", "continue")
        self.edit("A", "src/other.py", "part two\n"); self.end("A")
        c = self.ide()
        rows = self.listing(c)["rows"]
        self.assertEqual(["A"], [r["id"] for r in rows])
        self.assertEqual(2, rows[0]["segments"])
        self.assertEqual("first part", rows[0]["prompt"])
        d = c.get("/api/session", params={"id": "A"}).json()
        self.assertEqual({"src/app.py", "src/other.py"}, {f["path"] for f in d["files"]})

    def test_R11_a_new_id_resume_folds_into_one_row_and_one_diff(self):
        self.start("A"); self.prompt("A", "first part")
        self.edit("A", "src/app.py", "part one\n"); self.end("A")
        tp = os.path.join(self._t.name, "t.jsonl")
        with open(tp, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "user", "sessionId": "A", "message": "x"}) + "\n")
            fh.write(json.dumps({"type": "user", "sessionId": "B", "message": "y"}) + "\n")
        self.start("B", source="fork", transcript=tp); self.prompt("B", "go on")
        self.edit("B", "src/other.py", "part two\n")
        c = self.ide()
        rows = self.listing(c)["rows"]
        self.assertEqual(["B"], [r["id"] for r in rows], "the predecessor folds into its successor")
        self.assertEqual(["B", "A"], rows[0]["chain"])
        d = c.get("/api/session", params={"id": "B"}).json()
        self.assertEqual({"src/app.py", "src/other.py"}, {f["path"] for f in d["files"]})


# ============================================================================= R12 live / ended
class R12LiveVersusEnded(_Repo):

    def _proc(self):
        from mokata.session_ancestry import system_lookup
        lookup = system_lookup()
        if lookup is None:
            self.skipTest("no process table on this platform")
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(lambda: p.poll() is None and p.kill())
        for _ in range(50):
            info = lookup(p.pid)
            if info is not None and info.token:
                return p, [p.pid, str(info.token)]
            time.sleep(0.02)
        self.skipTest("the child process never showed in the table")

    def test_R12_a_killed_session_reads_ended_and_a_running_one_live(self):
        p, proc = self._proc()
        SI.on_start(self.root, "K", "startup", self.root, t=time.time() - 7200, proc=proc)
        os.utime(SI.path_of(self.root, "K"), (time.time() - 7200, time.time() - 7200))
        c = self.ide()
        row = self.listing(c)["rows"][0]
        self.assertEqual("live", row["status"], "its Claude process runs — live though silent 2 h")
        p.kill()
        p.wait()
        row = self.listing(c)["rows"][0]
        self.assertEqual("ended", row["status"])
        self.assertIn("exited without a SessionEnd", row["status_basis"])

    def test_R12_session_end_is_ended_and_a_silent_unknown_process_is_not_live(self):
        SI.on_start(self.root, "E", "startup", self.root, proc=None)
        SI.on_end(self.root, "E", "logout")
        SI.on_start(self.root, "Q", "startup", self.root, t=time.time() - 7200, proc=None)
        os.utime(SI.path_of(self.root, "Q"), (time.time() - 7200, time.time() - 7200))
        rows = {r["id"]: r for r in self.listing(self.ide())["rows"]}
        self.assertEqual("ended", rows["E"]["status"])
        self.assertIn("logout", rows["E"]["status_basis"])
        self.assertEqual("inactive", rows["Q"]["status"])

    def test_G15_an_ended_session_ages_out_of_needs_you(self):
        from mokata.ide import session_views as V
        SI.on_start(self.root, "G", "startup", self.root, proc=None)
        fake = [mock.Mock(type="gate_decision", run_id="", session_id="G", seq=1,
                          ts=time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                           time.gmtime(time.time() + 5)), ledger_seq=None,
                          data={"decision": "block", "gate": "tdd", "subject": "x"})]
        with mock.patch.object(V, "_events", return_value=fake):
            live = V.browser(self.root)
            self.assertTrue([q for q in live["needs_you"] if q["session"] == "G"])
            SI.on_end(self.root, "G", "logout")
            ended = V.browser(self.root)
        self.assertFalse([q for q in ended["needs_you"] if q["session"] == "G"],
                         "an ended session's old block still heads needs-you")


# ============================================================================= R13 scale
class R13FourHundredSessions(_Repo):

    def test_R13_400_sessions_list_and_filter_stay_fast(self):
        for n in range(400):
            SI.on_start(self.root, f"S{n:04d}", "startup", self.root, t=self.clock + n,
                        proc=None)
            SI.on_prompt(self.root, f"S{n:04d}", f"prompt number {n}", t=self.clock + n)
        SS.sessions(self.root)                                   # warm (the server stays up)
        t0 = time.perf_counter()
        d = SS.page(self.root, 0, 50)
        first = time.perf_counter() - t0
        t0 = time.perf_counter()
        f = SS.page(self.root, 0, 50, q="number 399")
        filt = time.perf_counter() - t0
        self.assertEqual(400, d["total"])
        self.assertEqual(["S0399"], [r["id"] for r in f["rows"]])
        # the PRD targets (500 ms / 150 ms on the bed) are measured in the report; this guard is
        # loose so a busy CI box does not flake, but an O(n²) regression still trips it.
        self.assertLess(first, 2.0)
        self.assertLess(filt, 2.0)


# ============================================================================= R28 viewing
class R28OneViewingContext(_Repo):

    def test_R28_the_code_gutter_follows_the_viewed_session(self):
        base = "".join(f"line {n}\n" for n in range(1, 41))
        self.start("V1")
        self.edit("V1", "src/app.py", base.replace("line 10\n", "line 10 CHANGED\n"))
        _write(self.root, "src/app.py",
               base.replace("line 10\n", "line 10 CHANGED\n").replace("line 30\n", "me 30\n"))
        c = self.ide()
        head = c.get("/api/file", params={"path": "src/app.py"}).json()
        self.assertEqual("HEAD", head["changes_against"])
        self.assertEqual(2, len(head["changes"]))
        self.focus(c, "V1")
        f = c.get("/api/file", params={"path": "src/app.py"}).json()
        self.assertEqual("session V1", f["changes_against"])
        self.assertEqual({"kind": "session", "session": "V1", "changed_here": True},
                         f["viewing"])
        self.assertEqual(2, len(f["changes"]),
                         "against what the session found: its line 10 and the later line 30")
        other = c.get("/api/file", params={"path": "src/other.py"}).json()
        self.assertEqual(([], False), (other["changes"], other["viewing"]["changed_here"]))


# ============================================================================= G23 live noise
class G23LiveHidesMokatasOwnFiles(_Repo):

    def test_G23_setup_files_are_hidden_from_live_with_a_toggle(self):
        _write(self.root, ".mcp.json", "{}\n")
        _write(self.root, ".claude/commands/brainstorm.md",
               "---\ndescription: mokata · brainstorm\n---\nbody\n")
        _write(self.root, ".claude/commands/mine.md", "---\ndescription: my own\n---\n")
        _write(self.root, "src/other.py", "x = 3\n")
        c = self.ide()
        d = c.get("/api/live").json()
        paths = {f["path"] for f in d["files"]}
        self.assertIn("src/other.py", paths)
        self.assertIn(".claude/commands/mine.md", paths, "a user's own command is never hidden")
        self.assertNotIn(".mcp.json", paths)
        self.assertNotIn(".claude/commands/brainstorm.md", paths)
        self.assertGreaterEqual(d["hidden_mokata"], 2)
        shown = {f["path"] for f in c.get("/api/live", params={"mokata": "1"}).json()["files"]}
        self.assertIn(".mcp.json", shown)


# ============================================================================= the page (static)
class TheScreenIsOneSessionSource(unittest.TestCase):
    """The page code: one list, one diff source, a click lands on the diff (B-F1), a single pick is
    one write (B-F2). Read as text — the behaviour itself is pinned by the API tests above."""

    @classmethod
    def setUpClass(cls):
        base = os.path.join(os.path.dirname(__file__), "..", "src", "mokata", "ide", "static")
        with open(os.path.join(base, "sessions.js"), encoding="utf-8") as fh:
            cls.sessions_js = fh.read()
        with open(os.path.join(base, "app.js"), encoding="utf-8") as fh:
            cls.app_js = fh.read()
        with open(os.path.join(base, "index.html"), encoding="utf-8") as fh:
            cls.html = fh.read()

    def test_R8_a_session_click_opens_the_sessions_screen_with_its_files(self):
        self.assertIn('"/api/sessions/list', self.sessions_js)
        self.assertIn('MK.setMode("sessions")', self.sessions_js)
        self.assertIn('id="session-files"', self.html)
        self.assertNotIn('id="sb-list"', self.html, "the second session list is gone")
        self.assertNotIn('id="session-pick"', self.html, "the left-panel session list is gone")

    def test_R9_one_pick_is_one_write_never_against_the_working_tree(self):
        self.assertNotIn('"work"]', self.app_js)
        self.assertIn('"/api/session/write?snap="', self.app_js)


if __name__ == "__main__":
    unittest.main()
