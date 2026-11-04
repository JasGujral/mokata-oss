"""0.0.27 P11 — explorer polish + `path:line:col` launch.

Each test states the user-visible rule it protects. The rules:
  * `path:line:col` means one thing everywhere (CLI, palette, in-IDE cites): `path`, `path:line`,
    `path:line:col`; a Windows drive letter is part of the path (`C:\\x\\y.py:3:1`), not a separator;
  * a path outside the workspace, or that is not a file, is refused loudly and nothing is started;
  * `mokata ide file.py:42:7` opens the window AT that file, line and column — the launcher's target
    rides the pairing link, is contained again by the server, and reaches the page;
  * the OSC-8 links and the agent's `ide_open` carry the column too;
  * the explorer marks a chain of single-child folders so it shows as ONE row, and does so on a
    68,000-file tree without walking it per call (still paged, never cut silently);
  * `/api/locate` is the in-IDE door for a pasted / linked location: contained, never a write.
"""
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata.ide import launch, links, locator, nav
from mokata.ide.nav import PathRefused


def _write(d, rel, text="x = 1\n"):
    p = os.path.join(d, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _git(d, *a):
    subprocess.run(["git", "-C", d, *a], check=True, capture_output=True, stdin=subprocess.DEVNULL)


def _repo():
    d = os.path.realpath(tempfile.mkdtemp(prefix="mk-p11-"))
    _git(d, "init", "-q", "-b", "main")
    _write(d, "src/app/core.py", "def total():\n    return 1\n")
    _write(d, "pkg/core/engine/run.py", "run = 1\n")
    _write(d, "pkg/core/engine/deep/leaf.py", "leaf = 1\n")
    _write(d, "docs/readme.md", "# hi\n")
    return d


def _client(root):
    from starlette.testclient import TestClient
    from mokata.ide.server import AppState, build_app
    state = AppState(root, "tok", 9999, None, False)
    c = TestClient(build_app(state), base_url="http://127.0.0.1:9999", follow_redirects=False)
    c.get("/pair?t=tok")
    return c


ORIGIN = {"Origin": "http://127.0.0.1:9999"}


class AWindowsDriveLetterIsPartOfThePath(unittest.TestCase):
    def test_path_line_col_forms_parse_the_same_everywhere(self):
        """`file`, `file:42` and `file:42:7` are the three forms; a missing line is line 1."""
        self.assertEqual(("a/b.py", 1, None), locator.parse("a/b.py"))
        self.assertEqual(("a/b.py", 42, None), locator.parse("a/b.py:42"))
        self.assertEqual(("a/b.py", 42, 7), locator.parse("a/b.py:42:7"))
        self.assertEqual(("a/b.py", 3, 1), locator.parse("a/b.py:3:1:"), "compiler-style trailing colon")
        self.assertEqual(("a/b.py", 1, 1), locator.parse("a/b.py:0:0"), "0 reads as 1")

    def test_windows_drive_letters_are_not_a_line_separator(self):
        r"""`C:\x\y.py:3:1` is the file `C:\x\y.py`; `C:\x\y.py` alone has no line; both slashes."""
        self.assertEqual(("C:\\x\\y.py", 3, 1), locator.parse("C:\\x\\y.py:3:1"))
        self.assertEqual(("C:\\x\\y.py", 1, None), locator.parse("C:\\x\\y.py"))
        self.assertEqual(("C:/x/y.py", 9, None), locator.parse("C:/x/y.py:9"))
        self.assertEqual(("d:\\proj\\a.py", 12, 40), locator.parse("d:\\proj\\a.py:12:40"))

    def test_nothing_to_open_is_refused(self):
        for bad in ("", "   ", None, "a\0b.py:3"):
            with self.assertRaises(PathRefused, msg=repr(bad)):
                locator.parse(bad)


class AnOutsidePathIsRefused(unittest.TestCase):
    def setUp(self):
        self.root = _repo()

    def test_inside_relative_and_absolute_resolve_to_the_workspace_name(self):
        """The CLI reads a relative path from where it ran; an absolute path inside is fine."""
        got = locator.resolve(self.root, "src/app/core.py:2:5")
        self.assertEqual(("src/app/core.py", 2, 5), got)
        ab = os.path.join(self.root, "src", "app", "core.py")
        self.assertEqual(("src/app/core.py", 2, None), locator.resolve(self.root, ab + ":2"))
        self.assertEqual(("src/app/core.py", 1, None),
                         locator.resolve(self.root, "core.py", os.path.join(self.root, "src", "app")))

    def test_outside_the_workspace_is_refused(self):
        for bad in ("../x.py:3", "/etc/passwd:1", os.path.join(tempfile.gettempdir(), "z.py") + ":2"):
            with self.assertRaises(PathRefused, msg=bad):
                locator.resolve(self.root, bad)

    def test_a_symlink_out_of_the_workspace_is_refused(self):
        if os.name == "nt":
            self.skipTest("symlinks need privileges on Windows")
        other = tempfile.mkdtemp(prefix="mk-p11-out-")
        _write(other, "secret.py")
        os.symlink(os.path.join(other, "secret.py"), os.path.join(self.root, "link.py"))
        with self.assertRaises(PathRefused):
            locator.resolve(self.root, "link.py:1")

    def test_a_windows_drive_path_on_a_machine_without_drives_is_outside(self):
        if os.name == "nt":
            self.skipTest("drive letters are real paths here")
        with self.assertRaises(PathRefused) as cm:
            locator.resolve(self.root, "C:\\x\\y.py:3:1")
        self.assertIn("outside", str(cm.exception))

    def test_a_directory_or_missing_file_is_not_opened(self):
        for bad in ("src/app", "src/app/nope.py:3"):
            with self.assertRaises(PathRefused, msg=bad):
                locator.resolve(self.root, bad)

    def test_the_mokata_record_is_not_code(self):
        _write(self.root, ".mokata/state.json", "{}")
        with self.assertRaises(PathRefused) as cm:
            locator.resolve(self.root, ".mokata/state.json:1")
        # the reason is the true one: it IS in the workspace, it is just not code
        self.assertNotIn("outside this workspace", str(cm.exception))
        self.assertIn(".mokata", str(cm.exception))

    def test_a_suffix_that_is_not_a_number_says_what_a_location_looks_like(self):
        for bad in ("src/app/core.py:3:x", "src/app/core.py:-3", "src/app/core.py:x"):
            with self.assertRaises(PathRefused, msg=bad) as cm:
                locator.resolve(self.root, bad)
            self.assertIn("path:LINE", str(cm.exception), bad)
            self.assertIn("src/app/core.py is", str(cm.exception), bad)

    def test_a_number_too_long_is_called_too_long_not_not_a_number(self):
        for bad in ("src/app/core.py:9999999999", "src/app/core.py:3:9999999999"):
            with self.assertRaises(PathRefused, msg=bad) as cm:
                locator.resolve(self.root, bad)
            self.assertIn("9 digits", str(cm.exception), bad)
            self.assertNotIn("whole numbers", str(cm.exception), bad)

    def test_a_symlink_out_of_the_workspace_gets_no_hint_about_what_lies_beyond(self):
        if os.name == "nt":
            self.skipTest("symlinks need privileges on Windows")
        other = tempfile.mkdtemp(prefix="mk-p11-out-")
        _write(other, "secret.py")
        os.symlink(os.path.join(other, "secret.py"), os.path.join(self.root, "link2.py"))
        with self.assertRaises(PathRefused) as cm:
            locator.resolve(self.root, "link2.py:3:x")
        self.assertNotIn("path:LINE", str(cm.exception))
        self.assertNotIn("link2.py is", str(cm.exception))

    def test_a_missing_file_gets_no_misleading_number_hint(self):
        with self.assertRaises(PathRefused) as cm:
            locator.resolve(self.root, "src/app/nope.py:3:x")
        self.assertNotIn("path:LINE", str(cm.exception))


class MokataIdeOpensAtTheLocation(unittest.TestCase):
    def setUp(self):
        self.root = _repo()

    def _run(self, argv):
        from mokata.cli import build_parser
        args = build_parser().parse_args(["ide", "--path", self.root, "--no-browser"] + argv)
        out, err = io.StringIO(), io.StringIO()
        seen = {}

        def fake(root, session, **kw):
            seen.update(kw)
            return launch.Launched("http://127.0.0.1:1/pair?t=x", True, False)
        with mock.patch("mokata.ide.launch.open_or_focus", fake), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            from mokata.cli_commands.ide import cmd_ide
            cwd = os.getcwd()
            os.chdir(self.root)
            try:
                code = cmd_ide(args)
            finally:
                os.chdir(cwd)
        return code, seen, err.getvalue()

    def test_a_target_reaches_the_launcher_as_path_line_col(self):
        code, seen, _ = self._run(["src/app/core.py:2:5"])
        self.assertEqual(0, code)
        self.assertEqual(("src/app/core.py", 2, 5), seen["target"])

    def test_no_target_is_the_plain_launch(self):
        code, seen, _ = self._run([])
        self.assertEqual(0, code)
        self.assertIsNone(seen["target"])

    def test_a_path_outside_the_workspace_starts_nothing(self):
        """Refused with a reason and a non-zero exit; no server is started for it."""
        code, seen, err = self._run(["/etc/passwd:1"])
        self.assertEqual(2, code)
        self.assertEqual({}, seen, "the launcher ran for a refused path")
        self.assertIn("outside this workspace", err)

    def test_the_target_rides_the_pairing_link(self):
        rec = {"port": 8123, "token": "tok"}
        url = launch.pair_url(rec, None, ("src/app/core.py", 2, 5))
        self.assertTrue(url.startswith("http://127.0.0.1:8123/pair?t=tok&open=src/app/core.py"), url)
        self.assertIn("&line=2&col=5", url)
        self.assertEqual("http://127.0.0.1:8123/pair?t=tok", launch.pair_url(rec, None))


class TheServerOpensTheTargetAndRefusesAnEscape(unittest.TestCase):
    def setUp(self):
        self.root = _repo()
        self.c = _client(self.root)

    def test_pairing_with_a_target_lands_the_page_on_that_file_line_and_column(self):
        r = self.c.get("/pair?t=tok&open=src/app/core.py&line=2&col=5")
        self.assertEqual(303, r.status_code)
        self.assertEqual("/?open=src/app/core.py&line=2&col=5", r.headers["location"])

    def test_pairing_with_an_escaping_target_opens_nothing(self):
        for bad in ("../../etc/passwd", "/etc/passwd", "C:\\x\\y.py"):
            r = self.c.get("/pair", params={"t": "tok", "open": bad, "line": "1"})
            self.assertEqual("/", r.headers["location"], bad)

    def test_the_osc8_open_link_keeps_the_column(self):
        r = self.c.get("/open?path=src/app/core.py&line=2&col=5")
        self.assertEqual("/?open=src/app/core.py&line=2&col=5", r.headers["location"])
        r = self.c.get("/open?path=src/app/core.py&line=2")
        self.assertEqual("/?open=src/app/core.py&line=2&col=0", r.headers["location"])
        self.assertEqual(400, self.c.get("/open?path=/etc/passwd&line=2&col=5").status_code)

    def test_the_cli_link_builder_adds_a_column_only_with_a_line(self):
        self.assertEqual("http://127.0.0.1:5/open?path=a/b.py&line=3&col=4", links.open_url(5, "a/b.py", 3, 4))
        self.assertEqual("http://127.0.0.1:5/open?path=a/b.py&line=3", links.open_url(5, "a/b.py", 3))
        self.assertEqual("http://127.0.0.1:5/open?path=a/b.py", links.open_url(5, "a/b.py", None, 4))

    def test_ide_open_publishes_the_column_to_every_page(self):
        r = self.c.post("/api/open", json={"path": "src/app/core.py", "line": 2, "col": 5}, headers=ORIGIN)
        self.assertEqual(200, r.status_code)
        self.assertEqual(5, r.json()["col"])
        r = self.c.post("/api/open", json={"path": "src/app/core.py", "line": 2}, headers=ORIGIN)
        self.assertNotIn("col", r.json())

    def test_locate_answers_the_contained_file_line_and_column(self):
        r = self.c.post("/api/locate", json={"text": "src/app/core.py:2:5"}, headers=ORIGIN)
        self.assertEqual({"path": "src/app/core.py", "line": 2, "col": 5}, r.json())
        ab = os.path.join(self.root, "src", "app", "core.py")
        r = self.c.post("/api/locate", json={"text": ab + ":9"}, headers=ORIGIN)
        self.assertEqual({"path": "src/app/core.py", "line": 9, "col": None}, r.json())

    def test_locate_refuses_what_is_outside_or_not_a_file(self):
        for bad in ("/etc/passwd:1", "../x.py:2", "src/app/nope.py:2", "C:\\x\\y.py:3:1", ""):
            r = self.c.post("/api/locate", json={"text": bad}, headers=ORIGIN)
            self.assertEqual(400, r.status_code, bad)
            self.assertIn("error", r.json())


class ACompactChainIsOneRow(unittest.TestCase):
    def test_a_chain_of_single_child_folders_is_marked_for_one_row(self):
        """`pkg/core/engine` has no file until `engine`: the row for `pkg` names the chain."""
        root = _repo()
        top = nav.tree(root, "")
        pkg = next(e for e in top["entries"] if e["path"] == "pkg")
        self.assertEqual("pkg/core/engine", pkg["compact_path"])
        self.assertEqual("pkg/core/engine", pkg["compact_name"])
        src = next(e for e in top["entries"] if e["path"] == "src")
        self.assertEqual("src/app", src["compact_path"])
        docs = next(e for e in top["entries"] if e["path"] == "docs")
        self.assertNotIn("compact_path", docs, "a folder with a file of its own is not a chain")

    def test_the_chains_last_folder_lists_its_files_and_the_name_is_relative_to_the_listing(self):
        root = _repo()
        inner = nav.tree(root, "pkg/core/engine")
        names = {e["path"] for e in inner["entries"]}
        self.assertEqual({"pkg/core/engine/deep", "pkg/core/engine/run.py"}, names)
        mid = nav.tree(root, "pkg")
        core = mid["entries"][0]
        self.assertEqual("core/engine", core["compact_name"], "relative to the listing, not the root")


class TheExplorerHoldsAt68kFiles(unittest.TestCase):
    """The explorer is lazy: one folder per call, paged, and compaction costs the chain's depth."""

    @classmethod
    def setUpClass(cls):
        files = []
        for a in range(40):
            for b in range(40):
                for c in range(40):
                    files.append(f"mod{a:02d}/pkg{b:02d}/f{c:02d}.py")
        for i in range(4000):
            files.append(f"wide/only/one/chain/leaf{i:05d}.py")
        files.append("deep/a/b/c/d/e/f/g/h/i/j/k/z.py")
        cls.files = sorted(files)

    def _tree(self, **kw):
        with mock.patch.object(nav, "files_of", return_value=self.files), \
                mock.patch.dict(nav._DIR_INDEX, clear=True):
            return nav.tree("/virtual", **kw)

    def test_the_root_lists_folders_not_the_repo_and_counts_every_file(self):
        out = self._tree()
        self.assertEqual(len(self.files), out["count"])
        self.assertEqual(sum(e["files"] for e in out["entries"]), len(self.files))
        self.assertLess(len(out["entries"]), 100, "the root listed more than its own children")

    def test_a_long_chain_is_one_row_and_its_files_page_without_loss(self):
        top = self._tree()
        wide = next(e for e in top["entries"] if e["path"] == "wide")
        self.assertEqual("wide/only/one/chain", wide["compact_path"])
        deep = next(e for e in top["entries"] if e["path"] == "deep")
        self.assertEqual("deep/a/b/c/d/e/f/g/h/i/j/k", deep["compact_path"])
        seen, off = [], 0
        while off is not None:
            page = self._tree(dir=wide["compact_path"], offset=off)
            seen += [e["path"] for e in page["entries"]]
            off = page["next"]
        self.assertEqual(4000, len(seen), "a page was cut silently")


class TheExplorerAssetsCarryTheControls(unittest.TestCase):
    def test_the_page_has_expand_collapse_and_compact_controls(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "src", "mokata", "ide", "static", "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        for ident in ("tree-expand", "tree-collapse", "tree-compact", "tree-job"):
            self.assertIn(f'id="{ident}"', html)


if __name__ == "__main__":
    unittest.main()
