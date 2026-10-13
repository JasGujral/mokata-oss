"""0.0.22 stage E2 — `mokata ide`, the read-only review window (Jas 2026-10-11).

What it must do, each asserted below:
  * live diff incl. uncommitted + untracked;  * the diff at ANY point in time (per-write snapshots);
  * what ONE session wrote (parallel sessions separate);  * branch compare and PR by URL;
  * `/mokata:review-window` from inside a session opens it, or RE-FOCUSES the open one on that session;
  * every section has an empty state, and `--demo` is a SAMPLE that can never pass for real data;
  * the security baseline: loopback, pairing cookie, Host + Origin checks, realpath containment.
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import hook_cli
from mokata import run_intent as RI
from mokata.govern.resume import PipelineCheckpoint
from mokata.config import Surface
from mokata.ide import diffs as D
from mokata.ide import sessions as SS
from mokata.ide import snapshots as S
from mokata.init import init_repo

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "..", "src", "mokata", "ide", "static")


def _silent(_):
    pass


def _git(d, *a):
    subprocess.run(["git", "-C", d, *a], check=True, capture_output=True, stdin=subprocess.DEVNULL)


def _repo(d, *runs, git=False):
    if git:
        _git(d, "init", "-q", "-b", "main")
        _git(d, "config", "user.email", "t@t")
        _git(d, "config", "user.name", "t")
    init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
    s = Surface.load(d)
    for rid in runs:
        PipelineCheckpoint(s.state, rid).ensure_registered()
    os.makedirs(os.path.join(d, "src"), exist_ok=True)


def _write(d, rel, text):
    with open(os.path.join(d, rel), "w", encoding="utf-8") as fh:
        fh.write(text)


def _hook(fn, payload):
    with mock.patch.object(hook_cli, "_read_stdin_bounded", return_value=json.dumps(payload)), \
            redirect_stdout(io.StringIO()):
        return fn([])


class TheSnapshotStore(unittest.TestCase):

    def test_before_and_after_are_kept_deduplicated_and_readable(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _write(d, "src/a.py", "x = 1\n")
            b = S.capture_before_first_write(d, "src/a.py", "S1")
            self.assertIsNone(S.capture_before_first_write(d, "src/a.py", "S1"), "once per session")
            _write(d, "src/a.py", "x = 2\n")
            a1 = S.capture(d, "src/a.py", S.AFTER, "S1")
            a2 = S.capture(d, "src/a.py", S.AFTER, "S1")
            self.assertEqual(a1.sha, a2.sha, "same content, one object")
            self.assertEqual("x = 1\n", S.content_of(d, b))
            self.assertEqual("x = 2\n", S.content_of(d, a1))
            self.assertEqual(3, len(S.timeline(d, session="S1")))

    def test_a_file_that_did_not_exist_is_recorded_as_absent(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            snap = S.capture_before_first_write(d, "src/new.py", "S1")
            self.assertEqual((S.ABSENT, ""), (snap.state, S.content_of(d, snap)))

    def test_outside_the_repo_a_symlink_out_and_mokatas_own_record_are_never_captured(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as other:
            _repo(d)
            _write(other, "secret.txt", "nope")
            os.symlink(os.path.join(other, "secret.txt"), os.path.join(d, "link.txt"))
            for p in (os.path.join(other, "secret.txt"), "link.txt", ".mokata/manifest.json",
                      "../x.py"):
                with self.subTest(p=p):
                    self.assertIsNone(S.capture(d, p, S.AFTER, "S1"))

    def test_too_large_keeps_no_content(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _write(d, "src/big.txt", "x" * 50)
            with mock.patch.object(S, "MAX_FILE_BYTES", 10):
                snap = S.capture(d, "src/big.txt", S.AFTER, "S1")
            self.assertEqual(S.TOO_LARGE, snap.state)
            self.assertIsNone(S.content_of(d, snap))

    def test_the_timeline_is_capped_and_orphans_collected(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            with mock.patch.object(S, "MAX_ENTRIES", 5):
                for i in range(12):
                    _write(d, "src/a.py", f"v{i}\n")
                    S.capture(d, "src/a.py", S.AFTER, "S1")
            self.assertLessEqual(len(S.timeline(d)), 6)
            objs = sum(len(fs) for _r, _d, fs in os.walk(os.path.join(S.store_dir(d), "objects")))
            self.assertLessEqual(objs, 6, "objects no line references are collected")


class TheHooksFeedIt(unittest.TestCase):

    def setUp(self):
        os.environ.pop("MOKATA_SESSION_ID", None)

    def test_an_allowed_write_records_its_before_and_dirty_track_its_after(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _write(d, "notes.md", "old\n")
            env = {"cwd": d, "session_id": "S1", "tool_name": "Write",
                   "tool_input": {"file_path": os.path.join(d, "notes.md"), "content": "new\n"}}
            self.assertEqual(0, _hook(hook_cli.gate_guard_main, env))
            _write(d, "notes.md", "new\n")
            _hook(hook_cli.dirty_track_main, env)
            kinds = [(s.kind, S.content_of(d, s)) for s in S.timeline(d, session="S1")]
            self.assertEqual([(S.BEFORE, "old\n"), (S.AFTER, "new\n")], kinds)

    def test_a_blocked_write_records_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            RI.bind_registered(d, "S1", "rA")            # no approach → impl writes blocked
            env = {"cwd": d, "session_id": "S1", "tool_name": "Write",
                   "tool_input": {"file_path": os.path.join(d, "src", "app.py"), "content": "x"}}
            self.assertEqual(hook_cli.BLOCK_EXIT, _hook(hook_cli.gate_guard_main, env))
            self.assertEqual([], S.timeline(d))

    def test_parallel_sessions_are_separate_rows_with_their_own_runs(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA", "rB")
            RI.bind_registered(d, "S1", "rA")
            RI.bind_registered(d, "S2", "rB")
            for sid, f in (("S1", "a.md"), ("S2", "b.md")):
                _write(d, f, "x\n")
                _hook(hook_cli.dirty_track_main, {"cwd": d, "session_id": sid, "tool_name": "Write",
                                                  "tool_input": {"file_path": os.path.join(d, f)}})
            rows = {r.id: r for r in SS.sessions(d)}
            self.assertEqual((["a.md"], "rA"), (rows["S1"].files, rows["S1"].run))
            self.assertEqual((["b.md"], "rB"), (rows["S2"].files, rows["S2"].run))


class EveryDiffSource(unittest.TestCase):

    def test_live_shows_modified_and_untracked_and_never_mokatas_record(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, git=True)
            _write(d, "a.py", "one\n")
            _git(d, "add", "a.py")
            _git(d, "commit", "-qm", "c1")
            _write(d, "a.py", "two\n")
            _write(d, "new.py", "fresh\n")
            ds = D.live(d)
            by = {f.path: f for f in ds.files}
            self.assertEqual("", ds.note)
            self.assertEqual(("modified", 1, 1), (by["a.py"].status, by["a.py"].added,
                                                   by["a.py"].removed))
            self.assertEqual("added", by["new.py"].status)
            self.assertFalse(any(p.startswith(".mokata") for p in by))

    def test_no_git_is_a_note_never_an_empty_answer(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            ds = D.live(d)
            self.assertEqual([], ds.files)
            self.assertIn("not a git repository", ds.note)

    def test_what_a_session_wrote_and_any_point_in_time(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _write(d, "src/a.py", "v0\n")
            b = S.capture_before_first_write(d, "src/a.py", "S1")
            _write(d, "src/a.py", "v1\n")
            mid = S.capture(d, "src/a.py", S.AFTER, "S1")
            _write(d, "src/a.py", "v2\n")
            S.capture(d, "src/a.py", S.AFTER, "S1")
            sess = D.session_diff(d, "S1").files[0]
            self.assertEqual([("-", "v0"), ("+", "v2")],
                             [(ln.op, ln.text) for h in sess.hunks for ln in h.lines])
            pit = D.between(d, b.id, mid.id).files[0]
            self.assertEqual([("-", "v0"), ("+", "v1")],
                             [(ln.op, ln.text) for h in pit.hunks for ln in h.lines])
            self.assertIn("same file", D.between(d, b.id, "nope").note)

    def test_branch_compare_and_ref_validation(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, git=True)
            _write(d, "a.py", "base\n")
            _git(d, "add", "a.py")
            _git(d, "commit", "-qm", "c1")
            _git(d, "checkout", "-qb", "feature")
            _write(d, "a.py", "feature\n")
            _git(d, "commit", "-qam", "c2")
            self.assertEqual(["a.py"], [f.path for f in D.refs(d, "main", "feature").files])
            self.assertIn("plain branch", D.refs(d, "main", "x; rm -rf /").note)
            self.assertEqual(["feature", "main"], sorted(D.branches(d)))

    def test_a_pr_url_is_parsed_and_a_foreign_origin_refused_before_any_fetch(self):
        self.assertEqual(("o", "r", 7), D.parse_pr_url("https://github.com/o/r/pull/7/files"))
        self.assertIsNone(D.parse_pr_url("https://evil.com/o/r/pull/7"))
        with tempfile.TemporaryDirectory() as d:
            _repo(d, git=True)
            _git(d, "remote", "add", "origin", "https://github.com/me/mine.git")
            with mock.patch.object(D, "_git", wraps=D._git) as g:
                ds = D.pr(d, "https://github.com/other/repo/pull/3")
            self.assertIn("is not other/repo", ds.note)
            self.assertFalse(any("fetch" in c.args for c in g.call_args_list), "no fetch")


def _client(demo=False, root=None, token="tok", port=9999):
    from starlette.testclient import TestClient
    from mokata.ide.server import AppState, build_app
    state = AppState(root or tempfile.gettempdir(), token, port, None, demo)
    return TestClient(build_app(state), base_url=f"http://127.0.0.1:{port}"), state


class TheSecurityBaseline(unittest.TestCase):

    def test_unpaired_is_refused_and_pairing_sets_an_httponly_strict_cookie(self):
        c, _ = _client(demo=True)
        self.assertEqual(401, c.get("/api/state").status_code)
        self.assertEqual(401, c.get("/pair?t=wrong", follow_redirects=False).status_code)
        r = c.get("/pair?t=tok", follow_redirects=False)
        self.assertEqual(303, r.status_code)
        cookie = r.headers["set-cookie"].lower()
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=strict", cookie)
        self.assertEqual(200, c.get("/api/state").status_code)

    def test_a_foreign_host_is_refused_even_when_paired(self):
        c, _ = _client(demo=True)
        c.get("/pair?t=tok")
        self.assertEqual(403, c.get("/api/state", headers={"Host": "evil.example:9999"}).status_code)

    def test_a_cross_origin_post_is_refused(self):
        c, _ = _client(demo=True)
        c.get("/pair?t=tok")
        r = c.post("/api/focus", json={"session": "x"}, headers={"Origin": "https://evil.example"})
        self.assertEqual(403, r.status_code)
        ok = c.post("/api/focus", json={"session": "x"},
                    headers={"Origin": "http://127.0.0.1:9999"})
        self.assertEqual(200, ok.status_code)

    def test_only_the_shipped_static_files_are_served_with_a_strict_csp(self):
        c, _ = _client(demo=True)
        c.get("/pair?t=tok")
        self.assertEqual(404, c.get("/static/..%2F..%2Fserver.py").status_code)
        self.assertEqual(404, c.get("/static/server.py").status_code)
        r = c.get("/static/app.js")
        self.assertEqual(200, r.status_code)
        self.assertIn("script-src 'self'", r.headers["content-security-policy"])

    def test_the_page_never_puts_repo_text_into_the_dom_as_html(self):
        with open(os.path.join(STATIC, "app.js"), encoding="utf-8") as fh:
            js = fh.read()
        for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
            self.assertNotIn(bad, js)


class SampleNeverPassesForReal(unittest.TestCase):

    def test_every_demo_answer_says_sample_and_every_real_one_says_not(self):
        c, _ = _client(demo=True)
        c.get("/pair?t=tok")
        for path in ("/api/state", "/api/live", "/api/timeline", "/api/session?id=x",
                     "/api/between?a=a&b=b", "/api/compare?base=main&head=x"):
            with self.subTest(path=path):
                self.assertIs(True, c.get(path).json()["sample"])
        self.assertIs(True, c.post("/api/pr", json={"url": "u"},
                                   headers={"Origin": "http://127.0.0.1:9999"}).json()["sample"])
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            real, _ = _client(root=d)
            real.get("/pair?t=tok")
            for path in ("/api/state", "/api/live", "/api/timeline"):
                self.assertIs(False, real.get(path).json()["sample"])

    def test_the_page_shows_a_banner_on_sample_and_an_empty_state_in_every_section(self):
        with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        self.assertIn('id="sample-banner"', html)
        for section in ("sessions-empty", "timeline-empty", "compare-empty", "diff-empty"):
            self.assertIn(f'id="{section}"', html, f"no empty state for {section}")

    def test_the_wordmarks_are_the_canonical_brand_files_not_redrawn(self):
        brand = os.path.join(HERE, "..", "assets", "brand")
        for name in ("mokata-wordmark-dark.svg", "mokata-wordmark-light.svg", "mokata-icon.svg"):
            with open(os.path.join(brand, name), "rb") as a, open(os.path.join(STATIC, name),
                                                                    "rb") as b:
                self.assertEqual(a.read(), b.read(), f"{name} drifted from assets/brand")


class OpenOrRefocus(unittest.TestCase):

    def test_ide_prompt_opens_or_refocuses_for_THIS_session(self):
        from mokata.ide.launch import Launched
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            out = io.StringIO()
            with mock.patch("mokata.ide.launch.open_or_focus",
                            return_value=Launched("http://127.0.0.1:1/pair?t=x", False, False)) as o, \
                    mock.patch.object(hook_cli, "_read_stdin_bounded", return_value=json.dumps(
                        {"cwd": d, "session_id": "S7", "prompt": "/mokata:review-window"})), \
                    redirect_stdout(out):
                self.assertEqual(0, hook_cli.user_prompt_submit_main([]))
            self.assertEqual("S7", o.call_args.args[1])
            ctx = json.loads(out.getvalue())["hookSpecificOutput"]["additionalContext"]
            self.assertIn("mokata ide re-focused", ctx)

    def test_an_ordinary_prompt_never_launches_anything(self):
        self.assertIsNone(hook_cli._ide_for_prompt("/r", "please idempotently fix it", "S1"))
        self.assertIsNone(hook_cli._ide_for_prompt("/r", "/mokata:ideas", "S1"))

    def test_a_second_open_refocuses_the_running_server_instead_of_starting_another(self):
        from mokata.ide import launch as L
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            first = L.open_or_focus(d, "S1", browser=False, idle_secs=60)
            try:
                self.assertTrue(first.started)
                second = L.open_or_focus(d, "S2", browser=False)
                self.assertFalse(second.started, "a second server was started")
                rec = L.running(d)
                self.assertEqual("S2", L._call(int(rec["port"]), rec["token"], "/api/ping")["focus"])
            finally:
                rec = L.running(d)
                if rec:
                    os.kill(int(rec["pid"]), 15)
                    for _ in range(40):
                        if not L.running(d):
                            break
                        time.sleep(0.1)


class VerificationFindingsStayFixed(unittest.TestCase):
    """The 0.0.22 independent verification's E2 release-gating findings, each pinned."""

    def test_a_symlink_out_of_the_repo_or_to_a_device_is_never_read(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as out:
            _repo(d, git=True)
            _write(out, "secret.txt", "secret-outside\n")
            os.symlink(os.path.join(out, "secret.txt"), os.path.join(d, "link_out"))
            os.symlink("/dev/zero", os.path.join(d, "zero"))
            files = {f.path: f for f in D.live(d).files}
            for name in ("link_out", "zero"):
                self.assertTrue(files[name].binary, f"{name} was read")
            blob = json.dumps(D.live(d).facts())
            self.assertNotIn("secret-outside", blob)

    def test_point_in_time_now_side_does_not_follow_a_swapped_in_symlink(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as out:
            _repo(d)
            _write(d, "n.txt", "x\n")
            snap = S.capture(d, "n.txt", S.BEFORE, session_id="S1")
            _write(out, "host", "OUTSIDE\n")
            os.remove(os.path.join(d, "n.txt"))
            os.symlink(os.path.join(out, "host"), os.path.join(d, "n.txt"))
            self.assertNotIn("OUTSIDE", json.dumps(D.between(d, snap.id, "work").facts()))

    def test_the_pr_origin_must_match_exactly_not_as_a_substring(self):
        self.assertEqual("acme/widgets", D._origin_slug("https://github.com/acme/widgets.git"))
        self.assertEqual("acme/widgets", D._origin_slug("git@github.com:acme/widgets.git"))
        self.assertEqual("acme/widgets-evil", D._origin_slug("https://github.com/acme/widgets-evil"))
        self.assertEqual("acme/widgets", D._origin_slug("ssh://git@github.com:22/acme/widgets.git"))
        for host_trick in ("https://github.com.evil.com/acme/widgets",
                           "https://github.com@evil.com/acme/widgets",
                           "git@github.com.evil.com:acme/widgets", "https://notgithub.com/acme/widgets",
                           "https://evil.com/github.com/acme/widgets"):
            self.assertIsNone(D._origin_slug(host_trick), host_trick)
        with tempfile.TemporaryDirectory() as d:
            _repo(d, git=True)
            _git(d, "remote", "add", "origin", "https://github.com/acme/widgets-evil.git")
            with mock.patch.object(D, "_git", wraps=D._git) as g:
                for url in ("https://github.com/acme/widgets/pull/5",
                            "https://github.com/me/widgets-evil/pull/5"):
                    self.assertIn("is not", D.pr(d, url).note)
            self.assertFalse(any("fetch" in c.args for c in g.call_args_list), "fetched")

    def test_a_fifo_or_a_swapped_path_never_hangs_or_escapes_the_reader(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            os.mkfifo(os.path.join(d, "pipe"))
            t0 = time.time()
            self.assertIsNone(D._read_text(d, "pipe"))
            self.assertLess(time.time() - t0, 2)

    def test_a_ref_can_never_be_a_git_option(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, git=True)
            for bad in ("-O/etc/passwd", "-Smaster", "--output=x"):
                self.assertIn("plain branch", D.refs(d, bad, "HEAD").note, bad)

    def test_a_valid_launcher_header_is_required_and_the_page_cannot_be_framed(self):
        c, _ = _client(demo=True)
        c.get("/pair?t=tok")
        self.assertEqual(403, c.post("/api/focus", json={"session": "x"},
                                     headers={"X-Mokata-Token": "junk"}).status_code)
        self.assertEqual(200, c.post("/api/focus", json={"session": "x"},
                                     headers={"X-Mokata-Token": "tok"}).status_code)
        r = c.get("/")
        self.assertIn("frame-ancestors 'none'", r.headers["content-security-policy"])
        self.assertEqual("DENY", r.headers["x-frame-options"])

    def test_ide_and_brainstorm_prompts_match_the_whole_command_only(self):
        self.assertIsNone(hook_cli._IDE_PROMPT.match("/review-window-anything"))
        self.assertIsNotNone(hook_cli._IDE_PROMPT.match("/mokata:review-window S1"))
        self.assertIsNone(RI._SLASH.match("/mokata:brainstorm-lite"))
        self.assertIsNotNone(RI._SLASH.match("/mokata:brainstorm a parser"))

    def test_switching_demo_and_real_leaves_one_server_and_hands_out_the_right_link(self):
        from mokata.ide import launch as L
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            first = L.open_or_focus(d, None, demo=True, browser=False, idle_secs=60)
            old = L.running(d)
            try:
                real = L.open_or_focus(d, "S1", demo=False, browser=False, idle_secs=60)
                rec = L.running(d)
                self.assertTrue(real.started)
                self.assertNotEqual(old["pid"], rec["pid"])
                self.assertIn(f":{rec['port']}/", real.url)
                self.assertFalse(L._call(int(rec["port"]), rec["token"], "/api/ping")["demo"])
                with self.assertRaises(OSError):
                    os.kill(int(old["pid"]), 0)
            finally:
                rec = L.running(d)
                if rec:
                    os.kill(int(rec["pid"]), 15)
                    time.sleep(0.5)
            del first


class TheCommandExists(unittest.TestCase):

    def test_cli_and_slash_command(self):
        from mokata.cli import build_parser
        self.assertIn("ide", build_parser()._subparsers._group_actions[0].choices)
        self.assertTrue(os.path.isfile(os.path.join(HERE, "..", "src", "mokata", "templates",
                                                    "commands", "review-window.md")))

    def test_no_command_file_is_named_skill(self):
        """0.0.22 live check — on Claude Code 2.1.x a plugin command file named `skill.md` keeps the
        plugin's OTHER command files from registering (bisected: with it present `/mokata:version`
        and this window's command are not invocable; renamed, both are). The authoring command is
        `skill-author`."""
        cmds = os.path.join(HERE, "..", "src", "mokata", "templates", "commands")
        self.assertFalse(os.path.exists(os.path.join(cmds, "skill.md")))
        self.assertTrue(os.path.exists(os.path.join(cmds, "skill-author.md")))

    def test_the_slash_command_does_not_collide_with_claude_codes_own_ide(self):
        """0.0.22 live check — Claude Code ships a built-in `/ide` (connect to VS Code/JetBrains),
        so a project-scope `/ide` never reached mokata and `/mokata:ide` was never listed. The
        command is `/review-window`; a bare or namespaced `ide` must not reach the launcher."""
        cmds = os.path.join(HERE, "..", "src", "mokata", "templates", "commands")
        self.assertFalse(os.path.exists(os.path.join(cmds, "ide.md")))
        for prompt in ("/ide", "/mokata:ide", "/ide S1"):
            self.assertIsNone(hook_cli._IDE_PROMPT.match(prompt), prompt)
        self.assertIsNotNone(hook_cli._IDE_PROMPT.match("/review-window"))


if __name__ == "__main__":
    unittest.main()
