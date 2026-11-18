"""Verifier attacks on 0.0.29 H-b (H2 open_beneath / H3 one door). Written by the refuting verifier.

The adversary here is NOT the builder's "swap before the first os.open": `Sweep` counts EVERY filesystem
event the contained read performs (os.open / os.stat / os.fstat / os.lstat / os.scandir / os.readlink /
realpath) and, for each k, runs a fresh read that applies the swap just BEFORE the k-th event — so every
window between the decision and the last post-open check is landed on, deterministically (no clock, no sleep).
Invariant under every swap at every k: never a byte from outside the repo or from .git/.mokata, never a hang.
"""
from __future__ import annotations

import os
try:
    import resource                       # POSIX only; absent on Windows (0.0.29 H-b xplat)
except ImportError:
    resource = None
import shutil
import stat
import sys
import tempfile
import unicodedata
import unittest
from unittest import mock

import _support  # noqa: F401

from mokata import fs_identity as FI
from mokata.ide import diffs as D

INSIDE, OUTSIDE, GIT = b"inside-ok", b"outside-secret", b"git-config-marker"
OBJ = b"object-db-marker"


def w(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def rename_pinned_root(src, dst):
    """Rename a root a pin HOLDS. True when it moved. Windows: the pin's handle has data access and no delete
    share, so the rename itself is refused (WinError 32) — the attack cannot be mounted, which is the pass;
    False then (0.0.29 H-b xplat). Anywhere else a refusal is a real error."""
    try:
        os.rename(src, dst)
    except PermissionError:
        if os.name != "nt":
            raise
        return False
    return True


class Tree:
    def __init__(self, tc):
        base = os.path.realpath(tempfile.mkdtemp(prefix="mk-hbv-"))
        tc.addCleanup(shutil.rmtree, base, True)
        self.base = base
        self.repo = os.path.join(base, "repo")
        self.out = os.path.join(base, "outside")
        w(os.path.join(self.repo, "docs", "config"), INSIDE)
        w(os.path.join(self.repo, "docs", "sub", "f.txt"), INSIDE)
        w(os.path.join(self.repo, ".git", "config"), GIT)
        w(os.path.join(self.repo, ".git", "HEAD"), GIT)
        w(os.path.join(self.repo, ".git", "objects", "ab", "cdef"), OBJ)
        w(os.path.join(self.repo, ".mokata", "x"), GIT)
        w(os.path.join(self.out, "docs", "config"), OUTSIDE)
        w(os.path.join(self.out, "docs", "sub", "f.txt"), OUTSIDE)
        w(os.path.join(self.out, "f.txt"), OUTSIDE)

    def p(self, *a):
        return os.path.join(self.repo, *a)


class Sweep:
    """Land `swap` before the k-th fs event of one contained read; report what came back."""

    EVENTS = ("open", "stat", "fstat", "lstat", "scandir", "readlink", "realpath")

    def __init__(self, repo, rel, swap):
        self.repo, self.rel, self.swap = repo, rel, swap

    def _wrap(self, mod, name, kind, st):
        orig = getattr(mod, name)

        def f(*a, **k):
            if st["armed"]:
                st["n"] += 1
                if st["n"] == st["target"] and not st["done"]:
                    st["done"] = True
                    self.swap()
                if kind == "open" and len(a) >= 2 and not (a[1] & getattr(os, "O_NONBLOCK", 0)):
                    try:
                        s = os.stat(a[0], dir_fd=k["dir_fd"], follow_symlinks=False) \
                            if k.get("dir_fd") is not None else os.lstat(a[0])
                        if stat.S_ISFIFO(s.st_mode):
                            st["blocking_fifo"] = True
                            raise OSError(4, "refused blocking FIFO open")
                    except (OSError, TypeError, ValueError) as e:
                        if st.get("blocking_fifo"):
                            raise
            return orig(*a, **k)
        return mock.patch.object(mod, name, f)

    def run(self, target):
        st = {"armed": False, "n": 0, "target": target, "done": False, "blocking_fifo": False}
        ps = [self._wrap(os, "open", "open", st), self._wrap(os, "stat", "stat", st),
              self._wrap(os, "fstat", "fstat", st), self._wrap(os, "lstat", "lstat", st),
              self._wrap(os, "scandir", "scandir", st), self._wrap(os.path, "realpath", "realpath", st)]
        for p in ps:
            p.start()
        try:
            st["armed"] = True
            got = D.open_contained(self.repo, self.rel)
            st["armed"] = False
        finally:
            for p in ps:
                p.stop()
        data = None
        if got:
            with got[0] as fh:
                data = fh.read()
        return got, data, st


class EveryWindow(unittest.TestCase):
    """For every k in 1..N (N = the events of an unswapped read), a swap before event k never leaks."""

    def setUp(self):
        self.t = Tree(self)

    def _grade(self, rel, mk_swap, label, prep=None, inside_from=2):
        """Round 2 (builder): EVERY k is graded, not only k > entry. k = 1 lands before the call's first
        filesystem event — the tree before the call — and must answer exactly what a read of the already
        swapped tree answers. Round 3: the pin's first event is ONE scandir of the root, which judges every
        store that is an entry of the root (`.git`, `.mokata`, a gitfile's in-root git dir) at once, so
        every k >= inside_from (default 2) must return no byte of outside, `.git` or `.mokata`. A store that
        is NOT a root entry (a git dir outside the root) is judged later; its test passes the measured event
        as inside_from, and k below it is graded like k = 1."""
        if prep:
            prep(self.t)
        base = Sweep(self.t.repo, rel, lambda: None).run(10 ** 9)
        n = base[2]["n"]
        self.assertGreater(n, 5, label)
        leaks, landed = [], 0
        for k in range(1, n + 2):
            t = Tree(self)
            if prep:
                prep(t)
            swap, undo = mk_swap(t)
            got, data, st = Sweep(t.repo, rel, swap).run(k)
            if st["done"]:
                landed += 1
            if st["blocking_fifo"]:
                leaks.append((k, "blocking FIFO open"))
            if k < inside_from:
                ref = Tree(self)
                if prep:
                    prep(ref)
                mk_swap(ref)[0]()
                want = Sweep(ref.repo, rel, lambda: None).run(10 ** 9)[1]
                if data is not None and data != want:   # a refusal is never a leak (Windows judges sooner)
                    leaks.append((k, "before the call", data, want))
            elif data and (OUTSIDE in data or GIT in data or OBJ in data):
                leaks.append((k, data))
        self.assertEqual([], leaks, f"{label}: leaks at event k (of {n})")
        self.assertGreater(landed, 15, f"{label}: the swap landed in too few windows ({landed}/{n})")
        self.assertEqual(n, landed, f"{label}: every one of the {n} events had a swap land before it")

    def _entry_count(self, rel):
        """Fs events counted up to the moment `open_beneath` is entered (= the decision is over)."""
        st = {"n": 0, "entry": None}
        s = Sweep(self.t.repo, rel, lambda: None)
        orig = FI.open_beneath

        def spy(*a, **k):
            st["entry"] = lambda_n[0]
            return orig(*a, **k)
        lambda_n = [0]
        # count through the same wrappers: run once with a hook on the swap callable
        calls = {"n": None}

        def probe_swap():
            pass
        # simplest: replay with target = huge and read the counter when open_beneath starts
        stt = {"armed": False, "n": 0, "target": 10 ** 9, "done": False, "blocking_fifo": False}
        wraps = [s._wrap(os, "open", "open", stt), s._wrap(os, "stat", "stat", stt),
                 s._wrap(os, "fstat", "fstat", stt), s._wrap(os, "lstat", "lstat", stt),
                 s._wrap(os, "scandir", "scandir", stt), s._wrap(os.path, "realpath", "realpath", stt)]
        seen = {}

        def spy2(*a, **k):
            seen["n"] = stt["n"]
            return orig(*a, **k)
        for w_ in wraps:
            w_.start()
        try:
            with mock.patch.object(FI, "open_beneath", spy2):
                stt["armed"] = True
                got = D.open_contained(self.t.repo, rel)
                stt["armed"] = False
        finally:
            for w_ in wraps:
                w_.stop()
        if got:
            got[0].close()
        return seen["n"]

    def _dir_to_link(self, name, target):
        def mk(t):
            real = t.p(name)

            def swap():
                os.rename(real, real + ".aside")
                os.symlink(target(t), real)
            return swap, None
        return mk

    def test_docs_to_link_out(self):
        self._grade("docs/config", self._dir_to_link("docs", lambda t: os.path.join(t.out, "docs")), "dir->out")

    def test_docs_to_link_into_git(self):
        self._grade("docs/config", self._dir_to_link("docs", lambda t: t.p(".git")), "dir->.git")

    def test_deep_dir_to_link_out(self):
        self._grade("docs/sub/f.txt", self._dir_to_link("docs/sub", lambda t: os.path.join(t.out, "docs", "sub")),
                    "deep dir->out")

    def test_file_to_symlink_out(self):
        def mk(t):
            real = t.p("docs", "config")

            def swap():
                os.rename(real, real + ".aside")
                os.symlink(os.path.join(t.out, "docs", "config"), real)
            return swap, None
        self._grade("docs/config", mk, "file->symlink out")

    def test_file_to_hardlink_git_config(self):
        def mk(t):
            real = t.p("docs", "config")

            def swap():
                os.rename(real, real + ".aside")
                os.link(t.p(".git", "config"), real)
            return swap, None
        self._grade("docs/config", mk, "file->hardlink .git/config")

    def test_file_to_hardlink_git_object(self):
        # DISCLOSED gap (objects dir is not indexed): graded separately, see ObjectDbHardLink.
        pass

    @unittest.skipUnless(hasattr(os, "mkfifo"), "no FIFO on Windows")
    def test_file_to_fifo(self):
        def mk(t):
            real = t.p("docs", "config")

            def swap():
                os.rename(real, real + ".aside")
                os.mkfifo(real)
            return swap, None
        self._grade("docs/config", mk, "file->fifo")

    # VERIFIER FINDING M1 (expectedFailure removed in round 2: the pin is taken before the decision)
    def test_mv_git_into_docs(self):
        def mk(t):
            def swap():
                os.rename(t.p("docs"), t.p("docs.aside"))
                os.rename(t.p(".git"), t.p("docs"))
            return swap, None
        self._grade("docs/config", mk, "mv .git docs")

    # ---- round 3 (M-r2-1): EACH protected store is moved, not only `.git`
    def _mv_into_docs(self, store):
        def mk(t):
            def swap():
                os.rename(t.p("docs"), t.p("docs.aside"))
                os.rename(store(t), t.p("docs"))
            return swap, None
        return mk

    def test_mv_mokata_into_docs(self):
        def prep(t):
            w(t.p(".mokata", "config"), GIT)
        self._grade("docs/config", self._mv_into_docs(lambda t: t.p(".mokata")), "mv .mokata docs", prep)

    def test_mv_gitfile_gitdir_inside_the_root_into_docs(self):
        def prep(t):
            os.rename(t.p(".git"), t.p(".gitdata"))
            w(t.p(".git"), b"gitdir: .gitdata\n")
        self._grade("docs/config", self._mv_into_docs(lambda t: t.p(".gitdata")), "mv .gitdata docs", prep)

    @staticmethod
    def _outside_layout(t):
        out = os.path.join(t.base, "outgit")
        os.rename(t.p(".git"), out)
        w(t.p(".git"), ("gitdir: " + out + "\n").encode())
        return out

    def _outside_gitdir_judged_at(self):
        """The fs event (Sweep's count) at which a read stats an OUTSIDE git dir."""
        t = Tree(self)
        out = os.path.normpath(self._outside_layout(t))
        st = {"armed": False, "n": 0, "target": 10 ** 9, "done": False, "blocking_fifo": False}
        s = Sweep(t.repo, "docs/config", lambda: None)
        real, at = os.stat, {}

        def counted_stat(*a, **k):
            if st["armed"]:
                st["n"] += 1
                if a and a[0] == out and "n" not in at:
                    at["n"] = st["n"]
            return real(*a, **k)
        ps = [s._wrap(os, "open", "open", st), s._wrap(os, "fstat", "fstat", st), s._wrap(os, "lstat", "lstat", st),
              s._wrap(os, "scandir", "scandir", st), s._wrap(os.path, "realpath", "realpath", st),
              mock.patch.object(os, "stat", counted_stat)]
        for p in ps:
            p.start()
        try:
            st["armed"] = True
            got = D.open_contained(t.repo, "docs/config")
            st["armed"] = False
        finally:
            for p in ps:
                p.stop()
        if got:
            got[0].close()
        return at.get("n")

    def test_the_outside_gitdir_window_is_what_is_disclosed(self):
        """DISCLOSED (round 3): a git dir OUTSIDE the root (linked worktree, submodule) cannot be an entry of
        the root listing, so it is judged by a stat after the root's realpath/open/fstat and the gitfile read.
        Pinned: it IS judged inside the pin (before the decision), within the first 20 events."""
        at = self._outside_gitdir_judged_at()
        self.assertIsNotNone(at)
        self.assertLessEqual(at, 20)

    def test_mv_gitfile_gitdir_OUTSIDE_the_root_into_docs(self):
        """Graded strictly from the event after that stat; before it, the read must equal a read of the
        already-swapped tree (the store had not been judged yet)."""
        at = self._outside_gitdir_judged_at()
        self._grade("docs/config", self._mv_into_docs(lambda t: os.path.join(t.base, "outgit")),
                    "mv <outside gitdir> docs", self._outside_layout, inside_from=at + 1)

    def test_dir_replaced_by_real_dir_with_hardlink_to_git(self):
        def mk(t):
            def swap():
                os.rename(t.p("docs"), t.p("docs.aside"))
                os.mkdir(t.p("docs"))
                os.link(t.p(".git", "config"), t.p("docs", "config"))
            return swap, None
        self._grade("docs/config", mk, "dir->real dir w/ hardlink")

    def test_root_replaced_by_link_to_outside(self):
        def mk(t):
            def swap():
                os.rename(t.repo, t.repo + ".aside")
                os.symlink(t.out, t.repo)
            return swap, None
        self._grade("docs/config", mk, "root->link out")

    def test_root_replaced_by_dir_holding_a_link_to_old_git_config(self):
        def mk(t):
            def swap():
                aside = t.repo + ".aside"
                os.rename(t.repo, aside)
                os.makedirs(os.path.join(t.repo, "docs"))
                os.link(os.path.join(aside, ".git", "config"), os.path.join(t.repo, "docs", "config"))
            return swap, None
        self._grade("docs/config", mk, "root->new dir hardlink")

    def test_root_parent_dir_swapped(self):
        def mk(t):
            def swap():
                os.rename(t.repo, t.base + "/repo.aside")
                os.makedirs(os.path.join(t.base, "repo", "docs"))
                w(os.path.join(t.base, "repo", "docs", "config"), OUTSIDE)
            return swap, None
        # a wholly different (but in-repo, plain) tree: contents are 'OUTSIDE' marker only because the test
        # needs a marker; the identity pin must refuse it (root id changed)
        base = Sweep(self.t.repo, "docs/config", lambda: None).run(10 ** 9)
        n = base[2]["n"]
        bad = []
        for k in range(2, n + 2):                       # round 2: every k inside the call (k=1 = before it)
            t = Tree(self)
            swap, _ = mk(t)
            got, data, st = Sweep(t.repo, "docs/config", swap).run(k)
            if st["done"] and data and OUTSIDE in data:
                bad.append(k)
        self.assertEqual([], bad)


class ObjectDbHardLink(unittest.TestCase):
    """Disclosed by the builder: git's object DB is not indexed for hard links. How reachable is it?"""

    def test_hardlink_to_a_loose_object_is_readable(self):
        t = Tree(self)
        os.link(t.p(".git", "objects", "ab", "cdef"), t.p("docs", "obj"))
        got = D.open_contained(t.repo, "docs/obj")
        data = got[0].read() if got else None
        if got:
            got[0].close()
        # RECORD: True means a file inside .git/objects is readable via a hard link (disclosed gap).
        self.assertIn(data, (None, OBJ))
        sys.stderr.write(f"\n[verifier] object-db hardlink readable: {data == OBJ}\n")


class Edges(unittest.TestCase):
    def setUp(self):
        self.t = Tree(self)

    def _fds(self):
        if os.path.isdir("/dev/fd"):
            return len(os.listdir("/dev/fd"))
        n = 0                                     # Windows: no /dev/fd; count the CRT fds that answer fstat
        for fd in range(4096):
            try:
                os.fstat(fd)
            except OSError:
                continue
            n += 1
        return n

    def test_dotdot_and_absolute(self):
        r = self.t.repo
        for rel in ("docs/../.git/config", "docs/../../outside/f.txt", "../outside/f.txt",
                    os.path.join(self.t.out, "f.txt"), "docs/./../.mokata/x", "DOCS/../.GIT/config",
                    ".git//config", "docs/config/..", "docs//config"):
            got = D.open_contained(r, rel)
            data = got[0].read() if got else None
            if got:
                got[0].close()
            self.assertNotIn(data or b"", (OUTSIDE, GIT), rel)
            self.assertFalse(data and (OUTSIDE in data or GIT in data), rel)

    def test_case_fold_and_unicode_names(self):
        r = self.t.repo
        for nm in ("café.txt", "café.txt"):
            w(self.t.p("docs", nm), INSIDE)
        # `DOCS/CONFIG` names `docs/config` only on a volume that folds case (APFS, NTFS; not ext4)
        folds = FI.folds_case(r)
        for rel in ("docs/café.txt", "docs/café.txt") + (("DOCS/CONFIG",) if folds else ()):
            got = D.open_contained(r, rel)
            self.assertTrue(got, rel)
            got[0].close()
        if not folds:
            self.assertFalse(D.open_contained(r, "DOCS/CONFIG"))
        # private spellings must stay private whatever the normalisation
        for rel in (".GIT/config", ".Git/HEAD", ".MOKATA/x", ".mokata/x", ".git/config"):
            got = D.open_contained(r, rel)
            self.assertFalse(got, rel)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "no FIFO on Windows")
    def test_fifo_device_socket_targets(self):
        os.mkfifo(self.t.p("docs", "fifo"))
        import socket
        s = socket.socket(socket.AF_UNIX)
        s.bind(self.t.p("docs", "sock"))
        self.addCleanup(s.close)
        for rel in ("docs/fifo", "docs/sock"):
            self.assertFalse(D.open_contained(self.t.repo, rel), rel)
        try:
            os.symlink("/dev/null", self.t.p("docs", "devnull"))
        except OSError:
            pass
        self.assertFalse(D.open_contained(self.t.repo, "docs/devnull"))
        self.assertIsNone(FI.open_beneath(self.t.repo, ["docs", "fifo"]))
        self.assertIsNone(FI.open_beneath(self.t.repo, ["docs", "sock"]))

    def test_long_names_and_deep_paths_never_raise_never_leak_fds(self):
        before = self._fds()
        long = "a" * 300
        self.assertFalse(D.open_contained(self.t.repo, f"docs/{long}") not in ("", None, False) and True)
        self.assertIsNone(FI.open_beneath(self.t.repo, ["docs", long]))
        deep = self.t.repo
        parts = []
        for i in range(120):
            parts.append("d")
        os.makedirs(os.path.join(self.t.repo, *parts), exist_ok=True)
        w(os.path.join(self.t.repo, *parts, "f"), INSIDE)
        fd = FI.open_beneath(self.t.repo, parts + ["f"])
        if fd is not None:
            os.close(fd)
        self.assertEqual(before, self._fds(), "fd leak")

    def test_very_deep_over_fd_limit(self):
        parts = ["d"] * 400
        try:
            os.makedirs(os.path.join(self.t.repo, *parts), exist_ok=True)
        except OSError:
            self.skipTest("cannot build 400-deep tree here")
        w(os.path.join(self.t.repo, *parts, "f"), INSIDE)
        before = self._fds()
        try:
            r = FI.open_beneath(self.t.repo, parts + ["f"])
        except BaseException as e:  # noqa
            self.fail(f"raised {e!r}")
        if r is not None:
            os.close(r)
        self.assertEqual(before, self._fds())

    @unittest.skipIf(resource is None, "NOT RUN: no `resource` module (Windows) to lower RLIMIT_NOFILE")
    def test_fd_exhaustion_fails_closed_without_leak(self):
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        before = self._fds()
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (before + 3, hard))
            for rel in ("docs/sub/f.txt", "docs/config"):
                got = D.open_contained(self.t.repo, rel)
                if got:
                    got[0].close()
        finally:
            resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))
        self.assertEqual(before, self._fds())

    def test_root_renamed_mid_call_by_name_still_refused_or_same_dir(self):
        # root path now names a different directory entirely -> root_id refuses
        # (round 2: the root_id argument became the pin taken before the decision)
        with FI.pin_root(self.t.repo) as pin:
            if not rename_pinned_root(self.t.repo, self.t.repo + ".moved"):
                return                            # Windows: the held root cannot be renamed at all
            os.makedirs(self.t.p("docs"))
            w(self.t.p("docs", "config"), b"newroot")
            self.assertIsNone(FI.open_beneath(self.t.repo, ["docs", "config"], pin=pin))

    def test_gitfile_worktree_hardlink_to_gitdir_file(self):
        gd = os.path.join(self.t.base, "gitdir")
        w(os.path.join(gd, "config"), GIT)
        w(os.path.join(gd, "HEAD"), GIT)
        shutil.rmtree(self.t.p(".git"))
        w(self.t.p(".git"), ("gitdir: " + gd + "\n").encode())
        os.link(os.path.join(gd, "config"), self.t.p("docs", "gc"))
        got = D.open_contained(self.t.repo, "docs/gc")
        data = got[0].read() if got else None
        self.assertFalse(data and GIT in data, "hard link to the gitfile's gitdir config was read")
        self.assertIsNone(FI.open_beneath(self.t.repo, ["docs", "gc"]))

    def test_part_with_backslash_posix_legit_name(self):
        w(self.t.p("docs", "a\\b"), INSIDE)
        got = D.open_contained(self.t.repo, "docs/a\\b")
        sys.stderr.write(f"\n[verifier] posix file named a\\\\b readable: {bool(got)}\n")
        if got:
            got[0].close()


if __name__ == "__main__":
    unittest.main()


class MvWindow(unittest.TestCase):
    """M1 reproducer, one deterministic seam: `.git` is renamed into `docs` AFTER the decision
    (`aliases_private` saw `.git` as the store) and BEFORE `open_beneath` samples its `pre` store ids.
    `pre` is then sampled from the already-moved tree, so `before == pre`, the held dir `docs` is not in
    `before`, and `.git/config` is returned. `root_id` is pinned from the decision; the stores are not."""

    def test_git_renamed_between_the_decision_and_pre(self):
        # Round 2: the seam moved from `FI._store_ids` (the pin no longer calls it) to the entry of
        # `open_beneath` — the same instant: after the decision, before the old code's second sample.
        t = Tree(self)
        orig = FI.open_beneath

        def seam(*a, **k):
            os.rename(t.p("docs"), t.p("docs.aside"))
            os.rename(t.p(".git"), t.p("docs"))
            return orig(*a, **k)
        with mock.patch.object(FI, "open_beneath", seam):
            got = D.open_contained(t.repo, "docs/config")
        data = got[0].read() if got else None
        self.assertNotEqual(GIT, data, "M1: bytes of .git/config were returned after .git was renamed in")
