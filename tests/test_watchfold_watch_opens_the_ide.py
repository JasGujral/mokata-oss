"""0.0.27 WATCH-FOLD — `mokata watch` opens the IDE on Sessions; the HTML dashboard is gone.

Jas's ruling (UX-REVIEW row #46): the user gets ONE browser window, not two. `mokata watch --open`
(and `/mokata:watch`) opens the IDE on its Sessions screen for the current run, re-using the IDE
already serving this repo when there is one. The self-contained `watch.html` dashboard and its MCP
`watch` tool are deleted, not kept beside it. The terminal lanes stay.

Each rule below is user-visible:
  * no IDE running  → one is started and the link the browser is handed lands on Sessions;
  * an IDE running  → it is re-used (no second server), and the link still lands on Sessions even
                      when that window's saved screen was another one;
  * the run's own session is the one the window focuses;
  * no browser (headless, `--no-browser`, or a browser that refuses) → the link is printed and the
    command returns — it never waits on a browser that is not there;
  * plain `mokata watch` on the default tier prints the terminal lanes and starts nothing.
"""
import http.cookiejar
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import cli
from mokata import run_intent as RI
from mokata.config import Surface
from mokata.govern.resume import PipelineCheckpoint
from mokata.ide import launch as L
from mokata.init import init_repo

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "src")


def _silent(_):
    pass


def _repo(d, *runs):
    init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
    s = Surface.load(d)
    for rid in runs:
        PipelineCheckpoint(s.state, rid).ensure_registered()


def _stop_ide(d):
    rec = L.running(d)
    if rec:
        L._stop(int(L._pinged_pid(rec) or rec.get("pid") or 0), d, wait=10.0)


def _watch(d, *extra, browser=None):
    """Run `mokata watch` in-process with the browser replaced by a recorder (no real browser is
    ever opened by this suite). Returns (exit code, stdout, the URLs the browser was handed)."""
    handed = []

    def _fake_open(url, new=0, autoraise=True):
        handed.append(url)
        return True if browser is None else browser

    out = io.StringIO()
    with mock.patch("webbrowser.open", side_effect=_fake_open), \
            mock.patch.object(L, "headless", return_value=False), \
            redirect_stdout(out), redirect_stderr(io.StringIO()):
        code = cli.main(["watch", *extra, "--path", d])
    return code, out.getvalue(), handed


def _opener():
    """One browser tab's view of the server: its own cookie jar."""
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def _workspace(opener, rec):
    with opener.open(f"http://127.0.0.1:{rec['port']}/api/workspace", timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _workspace_as_other_tab(rec):
    """A tab that was paired earlier (it holds the pairing cookie, never this link's landing)."""
    req = urllib.request.Request(f"http://127.0.0.1:{rec['port']}/api/workspace",
                                 headers={"x-mokata-token": rec["token"]})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _land(url):
    """Follow the link as a browser would (the pairing redirect sets the cookies) and answer which
    screen the page will restore to, from the server's own workspace read."""
    opener = _opener()
    with opener.open(url, timeout=30) as resp:
        resp.read()
    base = url.split("/pair", 1)[0]
    with opener.open(base + "/api/workspace", timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _store_tier(d, tier):
    """A manifest edited by hand (or left by an older mokata) — the path `config set` refuses."""
    from mokata import MOKATA_DIR
    p = os.path.join(d, MOKATA_DIR, "manifest.json")
    with open(p, encoding="utf-8") as fh:
        data = json.load(fh)
    data.setdefault("settings", {}).setdefault("ux", {})["progress"] = tier
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


def _save_workspace(rec, mode):
    body = json.dumps({"tabs": [], "mode": mode}).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{rec['port']}/api/workspace", data=body, method="POST",
        headers={"x-mokata-token": rec["token"], "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


class WatchOpenLandsOnSessions(unittest.TestCase):

    def setUp(self):
        self._dirs = []
        os.environ.pop("MOKATA_SESSION_ID", None)

    def tearDown(self):
        for d in self._dirs:
            _stop_ide(d)

    def _tmp(self):
        d = os.path.realpath(tempfile.mkdtemp())
        self._dirs.append(d)
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        return d

    def test_with_no_ide_running_watch_open_starts_one_and_lands_on_sessions(self):
        """`mokata watch --open` with no IDE: one is started for this repo, the browser is handed
        its link, and following that link puts the page on the Sessions screen."""
        d = self._tmp()
        _repo(d, "rA")
        self.assertIsNone(L.running(d))
        code, out, handed = _watch(d, "--open")
        self.assertEqual(0, code, out)
        rec = L.running(d)
        self.assertIsNotNone(rec, "no IDE was started")
        self.assertEqual(1, len(handed), out)
        self.assertIn(f"127.0.0.1:{rec['port']}/", handed[0])
        self.assertIn("opened", out)
        self.assertEqual("sessions", _land(handed[0])["mode"])

    def test_with_an_ide_running_watch_open_reuses_it_and_still_lands_on_sessions(self):
        """An IDE already serving this repo is RE-USED — the same server answers afterwards, no
        second one is started — and the link lands on Sessions even though that window's saved
        screen was Review."""
        d = self._tmp()
        _repo(d, "rA")
        first = L.open_or_focus(d, None, browser=False, idle_secs=120)
        self.assertTrue(first.started)
        before = L.running(d)
        _save_workspace(before, "review")
        code, out, handed = _watch(d, "--open")
        self.assertEqual(0, code, out)
        after = L.running(d)
        self.assertEqual((before["pid"], before["port"]), (after["pid"], after["port"]),
                         "a second IDE server was started instead of re-using the running one")
        self.assertIn("re-focused", out)
        self.assertEqual(1, len(handed), out)
        self.assertIn(f"127.0.0.1:{before['port']}/", handed[0])
        self.assertEqual("sessions", _land(handed[0])["mode"])

    def test_the_landing_is_for_that_link_once_not_a_rewrite_of_the_saved_screen(self):
        """The Sessions landing belongs to the watch link: a later plain reload of the window keeps
        whatever screen the person then chose (the saved workspace is not overwritten)."""
        d = self._tmp()
        _repo(d)
        code, out, handed = _watch(d, "--open")
        self.assertEqual(0, code, out)
        rec = L.running(d)
        self.assertEqual("sessions", _land(handed[0])["mode"])
        _save_workspace(rec, "plan")
        self.assertEqual("plan", _land(L.pair_url(rec, None))["mode"])

    def test_the_landing_belongs_to_the_tab_that_followed_the_link(self):
        """Only the page opened from the watch link lands on Sessions: an older tab of the same
        window reading its workspace in between keeps its own saved screen, and still does after
        the new tab has taken the landing."""
        d = self._tmp()
        _repo(d)
        first = L.open_or_focus(d, None, browser=False, idle_secs=120)
        rec = L.running(d)
        _save_workspace(rec, "review")
        code, out, handed = _watch(d, "--open")
        self.assertEqual(0, code, out)
        new_tab = _opener()
        with new_tab.open(handed[0], timeout=30) as resp:       # the link is followed …
            resp.read()
        self.assertEqual("review", _workspace_as_other_tab(rec)["mode"])   # … an older tab reads
        self.assertEqual("sessions", _workspace(new_tab, rec)["mode"])      # the new tab lands
        self.assertEqual("review", _workspace(new_tab, rec)["mode"])        # once
        del first

    def test_a_screen_that_is_not_a_screen_id_arms_nothing(self):
        """The pairing link's `screen` is checked against the screen-id shape: anything else is
        ignored — the page restores to its own saved screen."""
        d = self._tmp()
        _repo(d)
        L.open_or_focus(d, None, browser=False, idle_secs=120)
        rec = L.running(d)
        _save_workspace(rec, "plan")
        for bad in ("Sessions", "sessions</script>", "../x", "a" * 41):
            with self.subTest(bad=bad):
                from urllib.parse import quote
                url = L.pair_url(rec, None) + "&screen=" + quote(bad, safe="")
                self.assertEqual("plan", _land(url)["mode"])

    def test_the_window_focuses_the_session_of_the_current_run(self):
        """`--run rB` focuses the Claude session that is working on run rB — the Sessions screen
        opens on that run's session, not on whichever was focused before."""
        from mokata import hook_cli
        d = self._tmp()
        _repo(d, "rA", "rB")
        RI.bind_registered(d, "S1", "rA")
        RI.bind_registered(d, "S2", "rB")
        for sid, f in (("S1", "a.md"), ("S2", "b.md")):
            with open(os.path.join(d, f), "w", encoding="utf-8") as fh:
                fh.write("x\n")
            payload = {"cwd": d, "session_id": sid, "tool_name": "Write",
                       "tool_input": {"file_path": os.path.join(d, f)}}
            with mock.patch.object(hook_cli, "_read_stdin_bounded",
                                   return_value=json.dumps(payload)), \
                    redirect_stdout(io.StringIO()):
                hook_cli.dirty_track_main([])
        code, out, handed = _watch(d, "--open", "--run", "rB")
        self.assertEqual(0, code, out)
        rec = L.running(d)
        self.assertEqual("S2", L._call(int(rec["port"]), rec["token"], "/api/ping")["focus"])
        self.assertIn("focus=S2", handed[0])


class NoBrowserNeverHangs(unittest.TestCase):

    def setUp(self):
        self._dirs = []

    def tearDown(self):
        for d in self._dirs:
            _stop_ide(d)

    def _tmp(self):
        d = os.path.realpath(tempfile.mkdtemp())
        self._dirs.append(d)
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        return d

    def test_headless_prints_the_link_and_never_calls_a_browser(self):
        """With no display (a headless box, an ssh shell), no browser is launched — a console
        browser would take the terminal over — and the link is printed instead."""
        d = self._tmp()
        _repo(d)
        out = io.StringIO()
        launched = []
        with mock.patch("webbrowser.open", side_effect=lambda *a, **k: launched.append(a) or True), \
                mock.patch.object(L, "headless", return_value=True), \
                redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = cli.main(["watch", "--open", "--path", d])
        self.assertEqual(0, code, out.getvalue())
        self.assertEqual([], launched, "a browser was launched on a headless box")
        rec = L.running(d)
        self.assertIn(f"http://127.0.0.1:{rec['port']}/pair?", out.getvalue())
        self.assertIn("no browser", out.getvalue())

    def test_a_browser_that_refuses_is_said_and_the_link_printed(self):
        """When the browser cannot be opened, the command says so and prints the link."""
        d = self._tmp()
        _repo(d)
        code, out, handed = _watch(d, "--open", browser=False)
        self.assertEqual(0, code, out)
        self.assertEqual(1, len(handed))
        self.assertIn(handed[0], out)
        self.assertIn("no browser", out)

    def test_headless_is_a_linux_or_bsd_box_with_no_display_and_no_chosen_browser(self):
        """Only a POSIX desktop-less box counts as headless; Windows and macOS always have a
        browser launcher, and a BROWSER the user set is honoured."""
        self.assertTrue(L.headless({}, "linux"))
        self.assertTrue(L.headless({"SSH_CONNECTION": "x"}, "freebsd13"))
        self.assertFalse(L.headless({"DISPLAY": ":0"}, "linux"))
        self.assertFalse(L.headless({"WAYLAND_DISPLAY": "wayland-0"}, "linux"))
        self.assertFalse(L.headless({"BROWSER": "firefox"}, "linux"))
        self.assertFalse(L.headless({}, "win32"))
        self.assertFalse(L.headless({}, "darwin"))

    def test_no_browser_flag_in_a_real_process_prints_the_link_and_returns(self):
        """`mokata watch --open --no-browser` as a real process: it prints the link and exits —
        the IDE it started keeps serving on its own (a detached child, no console window)."""
        d = self._tmp()
        _repo(d)
        env = dict(os.environ)
        env["PYTHONPATH"] = os.path.abspath(SRC) + (os.pathsep + env["PYTHONPATH"]
                                                     if env.get("PYTHONPATH") else "")
        p = subprocess.run([sys.executable, "-m", "mokata", "watch", "--open", "--no-browser",
                            "--path", d], capture_output=True, text=True, env=env,
                           stdin=subprocess.DEVNULL, timeout=300)
        self.assertEqual(0, p.returncode, p.stdout + p.stderr)
        rec = L.running(d)
        self.assertIsNotNone(rec, "the IDE did not outlive the command")
        self.assertIn(f"http://127.0.0.1:{rec['port']}/pair?", p.stdout)
        self.assertEqual("sessions", _land(L.pair_url(rec, None, screen="sessions"))["mode"])


class TheTerminalTierStays(unittest.TestCase):

    def test_plain_watch_prints_the_lanes_and_starts_nothing(self):
        """On the default `terminal` tier, `mokata watch` prints the run's terminal lanes and
        opens no window — the terminal tier is the floor."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            code, out, handed = _watch(d, "--run", "rA")
            self.assertEqual(0, code, out)
            self.assertEqual([], handed)
            self.assertIsNone(L.running(d))
            self.assertIn("[0/7 done]", out)                  # the run's terminal lanes
            self.assertIn("brainstorm", out)
            self.assertNotIn("http://", out)

    def test_the_ide_tier_opens_the_window_without_the_flag(self):
        """`settings.ux.progress ide` makes plain `mokata watch` open the IDE on Sessions."""
        d = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        self.addCleanup(_stop_ide, d)
        _repo(d)
        with redirect_stdout(io.StringIO()):
            cli.main(["config", "set", "settings.ux.progress", "ide", "--yes", "--path", d])
        code, out, handed = _watch(d)
        self.assertEqual(0, code, out)
        self.assertEqual(1, len(handed), out)
        self.assertEqual("sessions", _land(handed[0])["mode"])

    def test_a_tier_that_is_not_one_left_in_a_manifest_is_refused_with_its_remedy(self):
        """The manifest is the user's: a stored `dashboard` (the deleted tier) or any other value
        that is not a tier makes `mokata watch` REFUSE — non-zero, the one command that fixes it,
        no lanes and no window — never a silent read as some other tier."""
        for stale in ("dashboard", "bogus"):
            with self.subTest(stale=stale), tempfile.TemporaryDirectory() as d:
                _repo(d, "rA")
                _store_tier(d, stale)
                out, err = io.StringIO(), io.StringIO()
                launched = []
                with mock.patch("webbrowser.open",
                                side_effect=lambda *a, **k: launched.append(a) or True), \
                        redirect_stdout(out), redirect_stderr(err):
                    code = cli.main(["watch", "--open", "--run", "rA", "--path", d])
                self.assertNotEqual(0, code)
                self.assertIn(f"settings.ux.progress={stale!r}", err.getvalue())
                self.assertIn("mokata config set settings.ux.progress ide", err.getvalue())
                self.assertEqual("", out.getvalue())             # no lanes read as `terminal`
                self.assertEqual([], launched)
                self.assertIsNone(L.running(d))

    def test_config_set_refuses_a_value_that_is_not_a_tier(self):
        """`mokata config set settings.ux.progress <x>` refuses anything outside
        terminal | ide | both, names the set, and leaves the manifest byte-unchanged."""
        from mokata import MOKATA_DIR
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            mpath = os.path.join(d, MOKATA_DIR, "manifest.json")
            with open(mpath, "rb") as fh:
                before = fh.read()
            for bad in ("dashboard", "bogus", "IDE"):
                out = io.StringIO()
                with redirect_stdout(out), redirect_stderr(io.StringIO()):
                    code = cli.main(["config", "set", "settings.ux.progress", bad, "--yes",
                                     "--path", d])
                self.assertNotEqual(0, code, bad)
                self.assertIn("terminal | ide | both", out.getvalue())
                with open(mpath, "rb") as fh:
                    self.assertEqual(before, fh.read(), f"{bad} was stored")
            for good in ("terminal", "ide", "both"):
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(0, cli.main(["config", "set", "settings.ux.progress", good,
                                                  "--yes", "--path", d]))


class TheHtmlDashboardIsGone(unittest.TestCase):

    def test_no_dashboard_module_and_no_watch_mcp_tool(self):
        """No second window survives: the `watch.html` writer and its MCP tool are deleted."""
        import importlib.util
        self.assertIsNone(importlib.util.find_spec("mokata.dashboard"))
        from mokata import mcp_server  # noqa: F401 — registers every tool
        from mokata.mcp.registry import tool_names
        self.assertIn("lanes", tool_names())        # the terminal lanes' tool stays
        self.assertNotIn("watch", tool_names())

    def test_the_slash_command_opens_the_ide_and_never_mentions_an_html_file(self):
        """`/mokata:watch` runs `mokata watch --open`; it no longer talks about a written file."""
        path = os.path.join(SRC, "mokata", "templates", "commands", "watch.md")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("mokata watch --open", text)
        self.assertNotIn("watch.html", text)
        self.assertNotIn("temp_local", text)
        self.assertNotIn("mcp__mokata__watch", text)


if __name__ == "__main__":
    unittest.main()
