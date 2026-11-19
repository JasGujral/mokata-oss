"""0.0.29 H2 (`CASEFOLD-OPEN-CONTAINED-TOCTOU`) — a read through `diffs.open_contained` holds the fd.

Business rule (note H §3a/§3c, rule page rev 2): another local process may swap a directory or file for a
link, swap in a hard link or a FIFO, rename `.git` into place, or swap the repo root itself, at any moment.
A read through `open_contained` still never returns bytes from outside the repo or from `.git/`/
`.mokata/`, and never blocks. The decision is made once, on one resolved name; the bytes come only from
`fs_identity.open_beneath`, which walks HELD descriptors and judges the private stores ON them.

The adversary seam (`Adversary`): it patches `os.open` so the swap lands just before the walk opens a
component — the seam the code has today — and, like the racer that leaked 2 in 1.2M on 0.0.28, it UNDOES
the swap while any `os.path.realpath` runs after that (a path re-check sees the benign tree) and puts it
back after. Every wait here is an event the test controls (guard: the swap is applied synchronously
inside the patched call); no clock, no sleep.
"""
from __future__ import annotations

import ast
import os
import shutil
import stat
import sys
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401
from test_hb_verify_attacks import rename_pinned_root

from mokata import fs_identity as FI
from mokata.ide import diffs as D

INSIDE = b"inside-ok-marker"
OUTSIDE = b"outside-secret-marker"
GIT = b"git-config-marker"
NT = os.name == "nt"


def _write(path: str, data: bytes) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def _dir_link(target: str, link: str) -> None:
    """A directory link: a junction on Windows (no privilege needed), a symlink elsewhere."""
    if NT:
        import _winapi
        _winapi.CreateJunction(target, link)
    else:
        os.symlink(target, link)


def _unlink_dir_link(link: str) -> None:
    if NT:
        os.rmdir(link)
    else:
        os.unlink(link)


class _Tree:
    """repo/{docs/config, docs/sub/f.txt, .git/{config,HEAD}, .mokata/x}; outside/{docs/config, f.txt}."""

    def __init__(self, tc: unittest.TestCase):
        base = os.path.realpath(tempfile.mkdtemp(prefix="mk-h2-"))
        tc.addCleanup(shutil.rmtree, base, True)
        self.base = base
        self.repo = os.path.join(base, "repo")
        self.out = os.path.join(base, "outside")
        _write(os.path.join(self.repo, "docs", "config"), INSIDE)
        _write(os.path.join(self.repo, "docs", "sub", "f.txt"), INSIDE)
        _write(os.path.join(self.repo, ".git", "config"), GIT)
        _write(os.path.join(self.repo, ".git", "HEAD"), GIT)
        _write(os.path.join(self.repo, ".mokata", "x"), GIT)
        _write(os.path.join(self.out, "docs", "config"), OUTSIDE)
        _write(os.path.join(self.out, "f.txt"), OUTSIDE)

    def p(self, *a: str) -> str:
        return os.path.join(self.repo, *a)


class Adversary:
    """Swap in just before the first `os.open` after `read()` starts; undo while `os.path.realpath` runs."""

    def __init__(self, swap, undo):
        self.swap, self.undo = swap, undo
        self.armed = self.swapped = False
        self._open, self._real = os.open, os.path.realpath

    blocking_fifo_open = False

    def _os_open(self, *a, **k):
        if self.armed and not self.swapped:
            self.swap()
            self.swapped = True
        if self.armed and len(a) >= 2 and not (a[1] & getattr(os, "O_NONBLOCK", 0)):
            # guard (an event the test controls, not a clock): an open WITHOUT O_NONBLOCK of a FIFO with
            # no writer never returns. The seam sees the flags and the node BEFORE the open, records the
            # offence and refuses the open — so the mutant reds by name instead of hanging the suite.
            try:
                st = os.stat(a[0], dir_fd=k.get("dir_fd"), follow_symlinks=False) \
                    if k.get("dir_fd") is not None else os.lstat(a[0])
            except (OSError, TypeError, ValueError):
                st = None
            if st is not None and stat.S_ISFIFO(st.st_mode):
                self.blocking_fifo_open = True
                raise OSError(4, "the test refused a blocking FIFO open")
        return self._open(*a, **k)

    def _realpath(self, *a, **k):
        if not self.swapped:
            return self._real(*a, **k)
        self.undo()
        try:
            return self._real(*a, **k)
        finally:
            self.swap()

    def read(self, root: str, rel: str):
        self.armed = True
        try:
            with mock.patch.object(os, "open", self._os_open), \
                    mock.patch.object(os.path, "realpath", self._realpath):
                got = D.open_contained(root, rel)
        finally:
            self.armed = False
        data = None
        if got:
            with got[0] as fh:
                data = fh.read()
        if self.swapped:
            self.undo()
            self.swapped = False
        return got, data


class SwapBetweenDecideAndOpen(unittest.TestCase):
    """Each swap: None, and never a byte from outside or from the store."""

    def setUp(self):
        self.t = _Tree(self)

    def _assert_refused(self, rel, swap, undo):
        adv = Adversary(swap, undo)
        got, data = adv.read(self.t.repo, rel)
        self.assertFalse(adv.blocking_fifo_open, "a FIFO was opened without O_NONBLOCK (it would hang)")
        self.assertNotIn(OUTSIDE, data or b"", "bytes from OUTSIDE the repo were returned")
        self.assertNotIn(GIT, data or b"", "bytes from .git were returned")
        self.assertFalse(got, f"{rel}: the swapped read was not refused (got {data!r})")

    def _swap_dir(self, rel_dir, target):
        real = self.t.p(rel_dir)
        aside = real + ".aside"

        def swap():
            os.rename(real, aside)
            _dir_link(target, real)

        def undo():
            _unlink_dir_link(real)
            os.rename(aside, real)
        return swap, undo

    def _swap_file(self, rel, make):
        real = self.t.p(*rel.split("/"))
        aside = real + ".aside"

        def swap():
            os.rename(real, aside)
            make(real)

        def undo():
            os.unlink(real)
            os.rename(aside, real)
        return swap, undo

    def test_intermediate_dir_to_a_link_out(self):
        self._assert_refused("docs/config", *self._swap_dir("docs", os.path.join(self.t.out, "docs")))

    def test_intermediate_dir_to_a_link_into_git(self):
        self._assert_refused("docs/config", *self._swap_dir("docs", self.t.p(".git")))

    @unittest.skipIf(NT, "no FIFO on Windows")
    def test_file_to_a_fifo_never_blocks(self):
        # guard: O_NONBLOCK — a blocking open of a FIFO with no writer never returns. The adversary's
        # `os.open` seam refuses such an open before it is made and records it (no clock, no hang).
        self._assert_refused("docs/config", *self._swap_file("docs/config", os.mkfifo))

    def test_file_to_a_hard_link_to_git_config(self):
        git_config = self.t.p(".git", "config")
        self._assert_refused("docs/config",
                             *self._swap_file("docs/config", lambda p: os.link(git_config, p)))

    def test_mv_git_into_place(self):
        docs, aside, dot_git = self.t.p("docs"), self.t.p("docs.aside"), self.t.p(".git")

        def swap():
            os.rename(docs, aside)
            os.rename(dot_git, docs)

        def undo():
            os.rename(docs, dot_git)
            os.rename(aside, docs)
        self._assert_refused("docs/config", swap, undo)

    def test_file_to_a_symlink_out(self):
        out = os.path.join(self.t.out, "f.txt")
        try:
            probe = self.t.p("probe-link")
            os.symlink(out, probe)
            os.unlink(probe)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"NOT RUN: no symlink privilege here ({exc})")
        self._assert_refused("docs/config", *self._swap_file("docs/config", lambda p: os.symlink(out, p)))

    def test_intermediate_dir_swapped_after_it_was_opened(self):
        """The swap lands AFTER the walk opened `docs` and before it opens `config`: a walk that re-joins a
        path for the file follows the new link out; one that opens `config` relative to the HELD `docs`
        reads the directory it already holds (the in-repo bytes) or nothing — never outside."""
        real, aside = self.t.p("docs"), self.t.p("docs.aside")
        state = {"done": False, "moved": False}
        real_open = os.open

        def at_file(*a, **k):
            if not state["done"] and a and os.path.basename(str(a[0])) == "config":
                state["done"] = True
                # Windows: the walk HOLDS `docs` (data access, no delete share), so it cannot be moved
                if rename_pinned_root(real, aside):
                    state["moved"] = True
                    _dir_link(os.path.join(self.t.out, "docs"), real)
            return real_open(*a, **k)
        try:
            with mock.patch.object(os, "open", at_file):
                got = D.open_contained(self.t.repo, "docs/config")
            data = None
            if got:
                with got[0] as fh:
                    data = fh.read()
        finally:
            if state["moved"]:
                _unlink_dir_link(real)
                os.rename(aside, real)
        self.assertTrue(state["done"], "the seam never saw the file open")
        self.assertNotIn(OUTSIDE, data or b"", "bytes from OUTSIDE the repo were returned")

    def test_the_root_swapped_for_a_link_after_the_decision(self):
        """Attack H2-1: the walk pins the root the decision was about."""
        twin = os.path.join(self.t.base, "twin")
        _write(os.path.join(twin, "docs", "config"), OUTSIDE)
        repo, aside = self.t.repo, self.t.repo + ".aside"

        def swap():
            os.rename(repo, aside)
            _dir_link(twin, repo)

        def undo():
            _unlink_dir_link(repo)
            os.rename(aside, repo)
        self._assert_refused("docs/config", swap, undo)


def _swapper(docs: str, aside: str, out_docs: str, go: str, counter: str) -> None:
    """The racer: a separate PROCESS (no GIL shared with the reader), swapping `docs` for a link out and
    back for as long as the test's `go` file exists (the event the test controls)."""
    n = 0
    while os.path.exists(go):
        try:
            os.rename(docs, aside)
            _dir_link(out_docs, docs)
            _unlink_dir_link(docs)
            os.rename(aside, docs)
            n += 1
        except OSError:
            pass
    with open(counter, "w", encoding="utf-8") as fh:
        fh.write(str(n))


class SwapRace(unittest.TestCase):
    """A SUPPLEMENT, not a RED row (note §5, L2): a real swapper process against a reader, 0 leaks.
    `MOKATA_H2_RACE=<reads>` sets the size (lane: 200k); unset, the cell is NOT RUN. The swapper runs
    while a file the test owns exists, and is joined after the test deletes it (guard: no clock)."""

    def test_swapper_process_vs_reader_never_leaks(self):
        import multiprocessing
        n = int(os.environ.get("MOKATA_H2_RACE", "0") or 0)
        if n <= 0:
            self.skipTest("NOT RUN: the race supplement (set MOKATA_H2_RACE=<reads>, lane size 200000)")
        t = _Tree(self)
        docs, aside = t.p("docs"), t.p("docs.aside")
        go, counter = os.path.join(t.base, "go"), os.path.join(t.base, "swaps")
        _write(go, b"")
        proc = multiprocessing.get_context("spawn").Process(
            target=_swapper, args=(docs, aside, os.path.join(t.out, "docs"), go, counter))
        proc.start()
        leaks = reads = refused = 0
        try:
            for _ in range(n):
                got = D.open_contained(t.repo, "docs/config")
                if got:
                    with got[0] as fh:
                        data = fh.read()
                    reads += 1
                    if data != INSIDE:
                        leaks += 1
                else:
                    refused += 1
        finally:
            os.unlink(go)
            proc.join()
        with open(counter, encoding="utf-8") as fh:
            swaps = fh.read()
        sys.stderr.write(f"\n[H2 SwapRace] {n} reads: {reads} returned bytes, {refused} refused/gone, "
                         f"{swaps} swaps by the racer process, {leaks} leaks\n")
        self.assertEqual(0, leaks)


class StillReads(unittest.TestCase):
    def setUp(self):
        self.t = _Tree(self)

    def _read(self, rel, root=None):
        got = D.open_contained(root or self.t.repo, rel)
        self.assertTrue(got, f"{rel} did not read")
        fh, st = got
        with fh:
            data = fh.read()
        self.assertTrue(stat.S_ISREG(st.st_mode))
        return data

    def test_a_plain_file(self):
        self.assertEqual(INSIDE, self._read("docs/config"))
        self.assertEqual(INSIDE, self._read("docs/sub/f.txt"))

    def test_in_repo_symlinked_dir(self):
        _dir_link(self.t.p("docs"), self.t.p("docs2"))
        self.assertEqual(INSIDE, self._read("docs2/sub/f.txt"))

    def test_in_repo_symlinked_file(self):
        try:
            os.symlink(self.t.p("docs", "config"), self.t.p("alias.txt"))
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"NOT RUN: no symlink privilege here ({exc})")
        self.assertEqual(INSIDE, self._read("alias.txt"))

    def test_dotdot_reads_the_same_file(self):
        self.assertEqual(INSIDE, self._read("docs/sub/../config"))

    def test_flipped_root_reads_where_the_disk_folds(self):
        flipped = os.path.join(self.t.base, "REPO")
        if not os.path.exists(flipped):
            self.skipTest("NOT RUN: this volume does not fold case")
        self.assertEqual(INSIDE, self._read("docs/config", root=flipped))

    def test_private_and_outside_refused(self):
        for rel in (".git/config", ".GIT/config", ".mokata/x", "../outside/f.txt"):
            self.assertFalse(D.open_contained(self.t.repo, rel), rel)

    def test_a_link_into_git_is_refused(self):
        _dir_link(self.t.p(".git"), self.t.p("innocent"))
        self.assertFalse(D.open_contained(self.t.repo, "innocent/config"))

    def test_a_gone_file_is_empty_string(self):
        self.assertEqual("", D.open_contained(self.t.repo, "docs/nope.txt"))


class OpenBeneathParts(unittest.TestCase):
    def setUp(self):
        self.t = _Tree(self)

    def test_bad_parts_refused(self):
        for parts in ([], [""], ["."], [".."], ["docs/config"], ["docs\\config"], ["docs", "con\0fig"]):
            self.assertIsNone(FI.open_beneath(self.t.repo, parts), parts)

    def test_a_good_read(self):
        fd = FI.open_beneath(self.t.repo, ["docs", "config"])
        self.assertIsNotNone(fd)
        with os.fdopen(fd, "rb") as fh:
            self.assertEqual(INSIDE, fh.read())

    def test_a_pin_of_another_root_or_a_spent_pin_is_refused(self):
        other = os.path.join(os.path.dirname(self.t.repo), "other")
        os.makedirs(os.path.join(other, "docs"))
        with open(os.path.join(other, "docs", "config"), "wb") as fh:
            fh.write(b"other")
        with FI.pin_root(other) as pin:
            self.assertIsNone(FI.open_beneath(self.t.repo, ["docs", "config"], pin=pin))
        pin = FI.pin_root(self.t.repo)
        pin.close()
        self.assertIsNone(FI.open_beneath(self.t.repo, ["docs", "config"], pin=pin))

    def test_a_gitfile_repointed_at_the_read_directory_after_the_pin_is_refused(self):
        """Round 2: after the pin, `.git` becomes a FILE naming `docs` as the git dir. `docs` was not a store
        when pinned, so only the post-open comparison of the held stores with the pinned set refuses it."""
        with FI.pin_root(self.t.repo) as pin:
            shutil.rmtree(self.t.p(".git"))
            with open(self.t.p(".git"), "w", encoding="utf-8") as fh:
                fh.write("gitdir: docs\n")
            self.assertIsNone(FI.open_beneath(self.t.repo, ["docs", "config"], pin=pin))

    def test_a_root_renamed_after_the_pin_is_refused(self):
        with FI.pin_root(self.t.repo) as pin:
            if not rename_pinned_root(self.t.repo, self.t.repo + ".moved"):
                return                            # Windows: the held root cannot be renamed at all
            os.makedirs(os.path.join(self.t.repo, "docs"))
            with open(os.path.join(self.t.repo, "docs", "config"), "wb") as fh:
                fh.write(b"newroot")
            self.assertIsNone(FI.open_beneath(self.t.repo, ["docs", "config"], pin=pin))

    def test_store_dirs_and_files_refused_on_the_descriptors(self):
        self.assertIsNone(FI.open_beneath(self.t.repo, [".git", "config"]))
        self.assertIsNone(FI.open_beneath(self.t.repo, [".mokata", "x"]))

    def test_a_hard_link_into_the_store_refused_on_the_descriptor(self):
        os.link(self.t.p(".git", "config"), self.t.p("docs", "hl"))
        self.assertIsNone(FI.open_beneath(self.t.repo, ["docs", "hl"]))

    @unittest.skipUnless(NT, "Windows spellings")
    def test_windows_streams_and_devices_refused(self):
        for part in ("config:s", "config::$DATA", "aux.txt", "CON", "nul", "com1.log", "LPT¹",
                     "CONIN$"):
            self.assertIsNone(FI.open_beneath(self.t.repo, ["docs", part]), part)

    @unittest.skipUnless(NT, "Windows 8.3 short names")
    def test_windows_short_names(self):
        import ctypes
        buf = ctypes.create_unicode_buffer(1024)
        n = ctypes.windll.kernel32.GetShortPathNameW(self.t.p(".git", "HEAD"), buf, 1024)
        if not n or "~" not in buf.value:
            self.skipTest("NOT RUN: 8.3 names are off on this volume")
        from mokata.repo_paths import name_of
        rel = str(name_of(buf.value, self.t.repo))
        self.assertFalse(D.open_contained(self.t.repo, rel))


class WindowsFinalPath(unittest.TestCase):
    @unittest.skipUnless(NT, "the handle's final path is the Windows check")
    def test_final_path_is_asked_of_the_handle(self):
        from mokata import winacl
        t = _Tree(self)
        fd = os.open(t.p("docs", "config"), os.O_RDONLY)
        try:
            got = winacl.final_path(fd)
        finally:
            os.close(fd)
        self.assertTrue(got and got.startswith("\\\\?\\Volume{"), got)
        self.assertTrue(got.lower().endswith("\\docs\\config"), got)

    @unittest.skipIf(NT, "the POSIX row")
    def test_final_path_off_windows_is_none(self):
        from mokata import winacl
        self.assertIsNone(winacl.final_path(0))


class NoFdLeak(unittest.TestCase):
    """Attack H2-9: an OSError injected at each open of the walk leaves no descriptor behind."""

    @unittest.skipIf(NT or not os.path.isdir("/dev/fd"), "POSIX fd table")
    def test_each_failure_point(self):
        t = _Tree(self)
        real_open = os.open

        def count():
            return len(os.listdir("/dev/fd"))
        for k in range(1, 7):
            calls = [0]

            def failing(*a, _k=k, **kw):
                calls[0] += 1
                if calls[0] == _k:
                    raise OSError(5, "injected")
                return real_open(*a, **kw)
            before = count()
            with mock.patch.object(os, "open", failing):
                fd = FI.open_beneath(t.repo, ["docs", "sub", "f.txt"])
            if fd is not None:
                os.close(fd)
            self.assertEqual(before, count(), f"fd leaked with a failure at open #{k}")
            for refused in (["docs", "hl-missing"], [".git", "config"]):
                before = count()
                self.assertIsNone(FI.open_beneath(t.repo, refused))
                self.assertEqual(before, count(), f"fd leaked on refusing {refused}")


class OneReadDoor(unittest.TestCase):
    def _fn(self, mod_file, name):
        with open(mod_file, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)

    def test_open_contained_reads_only_through_open_beneath(self):
        fn = self._fn(D.__file__, "open_contained")
        called = set()
        for n in ast.walk(fn):
            if isinstance(n, ast.Call):
                f = n.func
                called.add(f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", ""))
        self.assertIn("open_beneath", called)
        self.assertNotIn("open", called, "a second open in open_contained")

    def test_a_read_reaches_open_beneath(self):
        t = _Tree(self)
        seen = []
        real = FI.open_beneath
        with mock.patch.object(FI, "open_beneath",
                               side_effect=lambda *a, **k: (seen.append(a), real(*a, **k))[1]):
            got = D._read_text(t.repo, "docs/config")
        self.assertEqual(INSIDE.decode(), got)
        self.assertTrue(seen)

    def test_the_callers_use_open_contained(self):
        """Every IDE read that note §2 lists as a caller still names the door."""
        here = os.path.dirname(D.__file__)
        for mod in ("context.py", "highlight.py", "imagediff.py", "tableview.py", "review_context.py",
                    "diffview.py"):
            with open(os.path.join(here, mod), encoding="utf-8") as fh:
                src = fh.read()
            self.assertIn("open_contained(", src, mod)


class ContainedReadCost(unittest.TestCase):
    def test_opens_per_read_bounded_by_depth(self):
        t = _Tree(self)
        real = os.open
        n = [0]

        def counting(*a, **k):
            n[0] += 1
            return real(*a, **k)
        for rel in ("docs/config", "docs/sub/f.txt"):
            n[0] = 0
            with mock.patch.object(os, "open", counting):
                got = D.open_contained(t.repo, rel)
            got[0].close()
            depth = len(rel.split("/"))
            self.assertLessEqual(n[0], depth + 3, f"{rel}: {n[0]} opens")


if __name__ == "__main__":
    unittest.main()
