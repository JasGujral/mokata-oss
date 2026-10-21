"""0.0.23 stage C4 — `mokata ide` platform parity (ID-PLATFORM-PARITY), the review window's own
verification minors (VERIFICATION-MINORS-0022, the IDE-side ones), and the medians script.

Asserted:
  * the launch is serialised on EVERY OS by the shared OS lock (`oslock.file_lock`: flock on POSIX,
    msvcrt on Windows) — no bare `fcntl`, so Windows is no longer the unlocked platform;
  * stopping a server never probes with `os.kill(pid, 0)` (on Windows that TERMINATES the process);
    liveness is the server's own ping;
  * the child is detached on Windows too (`start_new_session` is POSIX-only);
  * the first-write BEFORE is recorded ONCE under the lock, even when two hooks race;
  * pruning the timeline never drops a BEFORE a surviving session still diffs against;
  * the navigation and search code is spelled cross-platform (no POSIX-only calls), and refuses a
    Windows absolute / UNC / drive path on every OS;
  * the view (mode, panel, open file) is restored on reopen, with throw-safe storage;
  * the benchmark script exists, runs, prints medians, and is not wired into CI.
"""
import os
import re
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata.ide import diffs as D
from mokata.ide import launch as L
from mokata.ide import nav
from mokata.ide import snapshots as S
from mokata.init import init_repo

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
IDE = os.path.join(ROOT, "src", "mokata", "ide")


def _silent(*_a, **_k):
    pass


def _write(d, rel, text):
    p = os.path.join(d, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as fh:
        fh.write(text.encode("utf-8"))


def _src(name):
    with open(os.path.join(IDE, name), encoding="utf-8") as fh:
        return fh.read()


def _code(name):
    """The module's CODE — comments and string literals dropped — so a docstring that explains
    why `os.kill(pid, 0)` is gone does not read as a use of it."""
    import io
    import tokenize
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(_src(name)).readline):
        if tok.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        out.append(tok.string)
    return " ".join(out)


class TheLaunchIsLockedOnEveryOS(unittest.TestCase):

    def test_open_or_focus_holds_the_shared_os_lock(self):
        held = {}

        class Spy:
            def __init__(self, path, timeout=None):
                held["path"], held["timeout"] = path, timeout

            def __enter__(self):
                held["inside"] = True
                return self

            def __exit__(self, *a):
                return False
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
            with mock.patch("mokata.oslock.file_lock", Spy), \
                    mock.patch.object(L, "_open_or_focus", return_value="ok") as inner:
                self.assertEqual("ok", L.open_or_focus(d, "S1", browser=False))
            inner.assert_called_once()
            from mokata.ide.server import record_path
            self.assertEqual(record_path(d) + ".lock", held["path"])
            self.assertGreater(held["timeout"], L.START_TIMEOUT, "the wait is bounded, not forever")
        self.assertFalse("fcntl" in _code("launch.py"), "no POSIX-only lock")

    def test_two_launchers_never_both_start_a_server(self):
        """Two concurrent `open_or_focus` on one repo: the second waits for the first's lock and
        then sees ITS server (re-focus), so exactly one start."""
        starts = []
        started = {"rec": None}
        lock = threading.Lock()

        def fake_inner(root, session, demo, browser, idle, terminal=False):
            with lock:
                if started["rec"] is None:
                    starts.append(session)
                    started["rec"] = {"port": 1}
                    return "started"
                return "refocused"
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
            out = []
            with mock.patch.object(L, "_open_or_focus", side_effect=fake_inner):
                ts = [threading.Thread(target=lambda s=s: out.append(L.open_or_focus(d, s, browser=False)))
                      for s in ("A", "B")]
                for t in ts:
                    t.start()
                for t in ts:
                    t.join(10)
            self.assertEqual(1, len(starts))
            self.assertEqual(["refocused", "started"], sorted(out))

    def test_stop_never_probes_with_kill_zero_and_waits_on_the_ping(self):
        import signal
        calls = []
        answers = iter([{"pid": 7}, {"pid": 7}, None])
        with mock.patch.object(L.os, "kill", side_effect=lambda pid, sig: calls.append((pid, sig))), \
                mock.patch.object(L, "running", side_effect=lambda root: next(answers)), \
                mock.patch.object(L.time, "sleep"):
            L._stop(7, "/repo", wait=5)
        self.assertEqual([(7, signal.SIGTERM)], calls)
        self.assertIsNone(re.search(r"os \. kill \( \w+ , 0 \)", _code("launch.py")))

    def test_the_child_is_detached_on_windows_too(self):
        with mock.patch.object(L.os, "name", "nt"):
            kw = L._detach_kwargs()
        self.assertIn("creationflags", kw)
        self.assertNotIn("start_new_session", kw)
        with mock.patch.object(L.os, "name", "posix"):
            self.assertEqual({"start_new_session": True}, L._detach_kwargs())


class TheReviewWindowMinors(unittest.TestCase):

    def test_racing_first_writes_record_one_before(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
            _write(d, "src/a.py", "x = 1\n")
            for attempt in range(5):
                sid = f"S{attempt}"
                barrier = threading.Barrier(4)

                def go():
                    barrier.wait()
                    S.capture_before_first_write(d, "src/a.py", sid)
                ts = [threading.Thread(target=go) for _ in range(4)]
                for t in ts:
                    t.start()
                for t in ts:
                    t.join(10)
                befores = [s for s in S.timeline(d, session=sid) if s.kind == S.BEFORE]
                self.assertEqual(1, len(befores), f"attempt {attempt}")

    def test_pruning_keeps_the_before_a_surviving_session_diffs_against(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
            _write(d, "src/a.py", "original\n")
            with mock.patch.object(S, "MAX_ENTRIES", 5):
                S.capture_before_first_write(d, "src/a.py", "S1")
                for i in range(12):
                    _write(d, "src/a.py", f"v{i}\n")
                    S.capture(d, "src/a.py", S.AFTER, "S1")
                snaps = S.timeline(d, session="S1")
            self.assertEqual(S.BEFORE, snaps[0].kind, "the BEFORE survived the cut")
            self.assertEqual("original\n", S.content_of(d, snaps[0]), "and so did its object")
            self.assertLessEqual(len(snaps), 7)
            diff = D.session_diff(d, "S1")
            removed = [ln.text for f in diff.files for h in f.hunks for ln in h.lines if ln.op == "-"]
            self.assertEqual(["original"], removed, "the session diff still starts from its BEFORE")

    def test_an_unreferenced_before_is_still_pruned(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
            _write(d, "src/old.py", "x\n")
            _write(d, "src/a.py", "y\n")
            with mock.patch.object(S, "MAX_ENTRIES", 5):
                S.capture_before_first_write(d, "src/old.py", "OLD")
                for i in range(12):
                    _write(d, "src/a.py", f"v{i}\n")
                    S.capture(d, "src/a.py", S.AFTER, "NEW")
            self.assertEqual([], S.timeline(d, session="OLD"),
                             "a BEFORE no surviving line refers to is not pinned forever")


class CrossPlatformSpelling(unittest.TestCase):

    NEW = ("nav.py", "search.py", "visuals.py", "launch.py", "server.py", "source.py")

    def test_no_posix_only_call_in_the_ide(self):
        bad = (r"\bfcntl\b", r"os \. mkfifo", r"os \. kill \( \w+ , 0 \)", r"os \. getuid",
               r"\bpwd\b", r"os \. fork\b", r"signal \. SIGKILL", r"os \. setsid\b")
        for name in self.NEW:
            code = _code(name)
            for pat in bad:
                self.assertIsNone(re.search(pat, code), f"{name}: {pat}")

    def test_windows_shaped_paths_are_refused_on_every_os(self):
        with tempfile.TemporaryDirectory() as d:
            for p in ("C:\\Windows\\win.ini", "c:/x.py", "\\\\server\\share\\x.py", "\\x.py",
                      "src\\..\\..\\x.py", "/etc/passwd"):
                with self.subTest(p=p):
                    with self.assertRaises(nav.PathRefused):
                        nav.contained_rel(d, p)

    def test_names_are_slash_spelled_whatever_the_os(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "src/pkg/mod.py", "def f():\n    pass\n")
            self.assertEqual(["src/pkg/mod.py"], nav.walk_files(d))
            self.assertEqual("src/pkg/mod.py", nav.contained_rel(d, "src/pkg/mod.py"))
            self.assertEqual(("f", "src/pkg/mod.py", 1, "function"), nav.symbol_index(d)[0])


class TheViewIsRestored(unittest.TestCase):

    def test_mode_panel_and_file_are_saved_and_restored_with_safe_storage(self):
        with open(os.path.join(IDE, "static", "app.js"), encoding="utf-8") as fh:
            js = fh.read()
        for fn in ("saveView", "loadView"):
            body = re.search(rf"function {fn}\(\) \{{(.*?)\n\}}", js, re.S).group(1)
            self.assertIn("try", body)
            self.assertIn("catch", body)
        self.assertIn("MK.restoreView", js)
        with open(os.path.join(IDE, "static", "boot.js"), encoding="utf-8") as fh:
            self.assertIn("MK.restoreView()", fh.read())


class TheMediansScript(unittest.TestCase):

    SCRIPT = os.path.join(ROOT, "scripts", "bench_ide_fuzzy.py")

    def test_it_runs_on_a_small_tree_and_prints_medians(self):
        with tempfile.TemporaryDirectory() as d:
            keep = os.path.join(d, "tree")
            p = subprocess.run([sys.executable, self.SCRIPT, "--files", "600", "--runs", "2",
                                "--keep", keep], capture_output=True, text=True, timeout=300,
                               stdin=subprocess.DEVNULL)
        self.assertEqual(0, p.returncode, p.stderr)
        self.assertIn("walk_files: 600 files", p.stdout)
        self.assertIn("median of per-query p50", p.stdout)

    def test_it_is_not_wired_into_ci(self):
        wf = os.path.join(ROOT, ".github", "workflows")
        # CORPUS: THE WORKING TREE — the workflows CI would actually read from this checkout.
        for name in (os.listdir(wf) if os.path.isdir(wf) else ()):
            with open(os.path.join(wf, name), encoding="utf-8") as fh:
                self.assertNotIn("bench_ide_fuzzy", fh.read(), name)


if __name__ == "__main__":
    unittest.main()
