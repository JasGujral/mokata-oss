"""0.0.29 F round-2 REFUTING verifier — the descriptor walk of `pycache.prune`.

Every component of the tree (root, each directory, each stale `.pyc` entry) is replaced, at every
step the walk takes (each `os.open` / `os.scandir` call), by a symlink to an outside directory, a
symlink to an outside file, a FIFO, a regular file, a directory, or a hardlink to an outside file.
Nothing outside the root may be deleted or changed, nothing may hang, nothing may raise. The bait
outside is shaped to be deleted if reached: a `<stem>.<tag>.pyc` one directory down (a file at the
TOP of a walked directory has no decodable source directory, so would be kept for that reason
alone — the builder's own bait sits there for the root-swap cell, which makes that cell weak).
"""
import itertools
import os
import shutil
import signal
import sys
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path; the per-test environment)

from mokata import pycache as PC  # noqa: E402

TAG = sys.implementation.cache_tag
KINDS = ("symlink", "filelink", "fifo", "file", "dir", "hardlink")


def _tree(base):
    root = os.path.join(base, "home", ".cache", "mokata", "pycache")
    os.makedirs(os.path.join(root, "a", "b", "c"))
    for d in ("", "a", "a/b", "a/b/c"):
        with open(os.path.join(root, d, f"m.{TAG}.pyc"), "wb") as fh:
            fh.write(b"x")
    v = os.path.join(base, "victim")
    os.makedirs(os.path.join(v, "a", "b", "c"))
    os.makedirs(os.path.join(v, "deep"))
    for d in ("", "deep", "a", "a/b"):    # a/b/c stays EMPTY: the rmdir bait
        with open(os.path.join(v, d, f"m.{TAG}.pyc"), "wb") as fh:
            fh.write(b"x")
    with open(os.path.join(base, "outfile.pyc"), "wb") as fh:
        fh.write(b"x")
    return root, v


def _swap(kind, target, v, base):
    if os.path.isdir(target) and not os.path.islink(target):
        shutil.rmtree(target)
    elif os.path.lexists(target):
        os.unlink(target)
    if kind == "symlink":
        os.symlink(v, target)
    elif kind == "filelink":
        os.symlink(os.path.join(base, "outfile.pyc"), target)
    elif kind == "fifo":
        if hasattr(os, "mkfifo"):
            os.mkfifo(target)
    elif kind == "file":
        with open(target, "wb") as fh:
            fh.write(b"y")
    elif kind == "dir":
        os.makedirs(target)
    elif kind == "hardlink":
        os.link(os.path.join(base, "outfile.pyc"), target)


class EveryComponentSwappedAtEveryStep(unittest.TestCase):
    TARGETS = ("", "a", "a/b", "a/b/c", f"m.{TAG}.pyc", f"a/m.{TAG}.pyc",
               f"a/b/m.{TAG}.pyc", f"a/b/c/m.{TAG}.pyc")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "no FIFOs here (Windows); the prune is POSIX-only")
    def test_nothing_outside_the_root_is_touched_and_nothing_raises(self):
        fired = 0
        for kind, tgt in itertools.product(KINDS, self.TARGETS):
            for k in range(0, 40):
                base = os.path.realpath(tempfile.mkdtemp(prefix="fvr2-"))
                self.addCleanup(shutil.rmtree, base, True)
                root, v = _tree(base)
                target = root if tgt == "" else os.path.join(root, tgt)
                keep = [os.path.join(v, d, f"m.{TAG}.pyc") for d in ("", "deep", "a", "a/b")]
                keep.append(os.path.join(base, "outfile.pyc"))
                inode = {p: os.stat(p, follow_symlinks=False).st_ino for p in keep}
                real_open, real_scandir = os.open, os.scandir
                real_unlink, real_rmdir = os.unlink, os.rmdir
                n = [0]
                done = [False]

                def hook():
                    nonlocal fired
                    if n[0] == k and not done[0]:
                        done[0] = True
                        if tgt == "":
                            os.rename(root, root + ".aside")
                            _swap(kind, root, v, base)
                            fired += 1
                        elif os.path.lexists(target):
                            _swap(kind, target, v, base)
                            fired += 1
                    n[0] += 1

                def w_open(*a, **kw):
                    hook()
                    return real_open(*a, **kw)

                def w_scandir(*a, **kw):
                    hook()
                    return real_scandir(*a, **kw)
                def w_unlink(*a, **kw):
                    hook()
                    return real_unlink(*a, **kw)

                def w_rmdir(*a, **kw):
                    hook()
                    return real_rmdir(*a, **kw)
                with self.subTest(kind=kind, target=tgt or "<root>", step=k):
                    hung = []

                    def on_alarm(*a):
                        hung.append(1)
                        raise TimeoutError("HANG")   # an OSError: the prune may swallow it, hung[] remembers
                    signal.signal(signal.SIGALRM, on_alarm)
                    signal.alarm(3)
                    try:
                        with mock.patch.object(os, "open", w_open), \
                                mock.patch.object(os, "scandir", w_scandir), \
                                mock.patch.object(os, "supports_fd", os.supports_fd | {w_scandir}), \
                                mock.patch.object(os, "unlink", w_unlink), \
                                mock.patch.object(os, "rmdir", w_rmdir), \
                                mock.patch.object(os, "supports_dir_fd",
                                                  os.supports_dir_fd | {w_open, w_unlink, w_rmdir}):
                            PC.prune(root)
                    finally:
                        signal.alarm(0)
                    self.assertFalse(hung, "the prune blocked (a FIFO opened for reading?)")
                    for p in keep:
                        self.assertTrue(os.path.lexists(p), f"outside file deleted: {p}")
                        self.assertEqual(inode[p], os.stat(p, follow_symlinks=False).st_ino)
                    self.assertTrue(os.path.isdir(os.path.join(v, "deep")))
                    self.assertTrue(os.path.isdir(os.path.join(v, "a", "b", "c")), "outside dir rmdir'd")
        self.assertGreater(fired, 300, "the seam must actually swap (non-vacuous)")

    @unittest.skipIf(os.name == "nt", "POSIX prune only")
    def test_a_tree_deeper_than_the_fd_limit_never_raises(self):
        import resource
        base = os.path.realpath(tempfile.mkdtemp(prefix="fvr2-"))
        self.addCleanup(shutil.rmtree, base, True)
        root = os.path.join(base, ".cache", "mokata", "pycache")
        os.makedirs(root)
        cwd = os.getcwd()
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        try:
            os.chdir(root)
            for _ in range(80):
                os.mkdir("d")
                os.chdir("d")
            with open(f"m.{TAG}.pyc", "wb") as fh:
                fh.write(b"x")
            os.chdir(cwd)
            resource.setrlimit(resource.RLIMIT_NOFILE, (40, hard))
            PC.prune(root)                      # must return, not raise EMFILE
        finally:
            os.chdir(cwd)
            resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))


if __name__ == "__main__":
    unittest.main()
