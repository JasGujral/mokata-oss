"""0.0.29 F round-3 REFUTING verifier — every ANCESTOR of the prune root, swapped at every call.

The root is `<base>/home/.cache/mokata/pycache`. For each ancestor (`base`, `home`, `.cache`,
`mokata`, `pycache`) and each swap kind (link to a look-alike tree OUTSIDE; the ancestor renamed away
and a NEW real directory with a stale-looking bait put in its place; link to the real tree under
another name), the swap lands just before the k-th call of ANY of os.open / stat / lstat / fstat /
scandir / path.isdir / path.exists / unlink / rmdir / geteuid — for every k the prune makes, from the
first (before `realpath`'s first lstat) to the last. Nothing outside the real root may be deleted.
Plus: a legitimately symlinked `~/.cache` (and a symlinked `home`) still prunes in the real root;
a root owned by another uid prunes nothing (the owner check's own cell); a relative XDG_CACHE_HOME
yields no prefix.
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path; the per-test environment)

from mokata import pycache as PC  # noqa: E402

TAG = sys.implementation.cache_tag
REL = ("home", ".cache", "mokata", "pycache")
HOOKED = [(os, n) for n in ("open", "stat", "lstat", "fstat", "scandir", "unlink", "rmdir",
                            "geteuid")] + [(os.path, n) for n in ("isdir", "exists")]


def _bait(d):
    p = os.path.join(d, "gone", f"m.{TAG}.pyc")
    os.makedirs(os.path.dirname(p))
    with open(p, "wb") as fh:
        fh.write(b"x")
    return p


def _tree():
    base = os.path.realpath(tempfile.mkdtemp(prefix="mk-r3-"))
    root = os.path.join(base, *REL)
    os.makedirs(root)
    stale = _bait(root)
    out = {}
    for i, anc in enumerate(("base",) + REL):
        other = os.path.join(base + "-out", f"elsewhere-{anc}")
        sub = REL[i:]
        target = os.path.join(other, *sub)
        os.makedirs(target)
        out[anc] = (other, _bait(target))
    return base, root, stale, out


def _path_of(base, anc):
    return base if anc == "base" else os.path.join(base, *REL[:REL.index(anc) + 1])


def _run(root, on_step, euid=None, log=None):
    steps = [0]
    inside = [False]
    saved = [(m, n, getattr(m, n)) for m, n in HOOKED]
    supports_fd, supports_dir = set(os.supports_fd), set(os.supports_dir_fd)
    patches = []
    wrapped = {}

    def mk(m, n, real):
        def w(*a, **k):
            if not inside[0]:
                inside[0] = True
                try:
                    steps[0] += 1
                    if log is not None:
                        log.append((steps[0], n, a[0] if a else None))
                    on_step(steps[0])
                finally:
                    inside[0] = False
            if n == "geteuid" and euid is not None:
                return euid
            return real(*a, **k)
        return w
    for m, n, real in saved:
        wrapped[(m, n)] = mk(m, n, real)
        patches.append(mock.patch.object(m, n, wrapped[(m, n)]))
    patches.append(mock.patch.object(os, "supports_fd", supports_fd | {wrapped[(os, "scandir")]}))
    patches.append(mock.patch.object(os, "supports_dir_fd",
                                     supports_dir | {wrapped[(os, "open")], wrapped[(os, "unlink")],
                                                     wrapped[(os, "rmdir")]}))
    for p in patches:
        p.start()
    try:
        n = PC.prune(root)
    finally:
        for p in reversed(patches):
            p.stop()
    return n, steps[0]


#: The prune walks by descriptor (`os.open` with `dir_fd`, `O_NOFOLLOW`) and checks the owner
#: (`os.geteuid`): POSIX capabilities. On Windows the prune is a no-op by design (0.0.29 F §9 M1);
#: `OnWindowsThePruneIsANoOp` below asserts that on a real tree.
POSIX_PRUNE = os.name != "nt" and hasattr(os, "geteuid") and hasattr(os, "O_NOFOLLOW")


@unittest.skipUnless(POSIX_PRUNE, "the descriptor walk and owner check are POSIX capabilities; "
                                  "Windows: OnWindowsThePruneIsANoOp")
class AncestorsSwappedAtEveryCall(unittest.TestCase):
    def _cleanup(self, base):
        for d in (base, base + "-out", base + ".was"):
            shutil.rmtree(d, True)

    def test_unswapped_prunes_the_root(self):
        base, root, stale, _o = _tree()
        self.addCleanup(self._cleanup, base)
        n, steps = _run(root, lambda i: None)
        self.assertEqual(1, n)
        self.assertFalse(os.path.exists(stale))
        self.assertGreater(steps, 10)

    def test_no_swap_of_any_ancestor_at_any_call_deletes_outside(self):
        base, root, _s, _o = _tree()
        self._cleanup(base)
        base, root, _s, _o = _tree()
        self.addCleanup(self._cleanup, base)
        log = []
        _n, total = _run(root, lambda i: None, log=log)
        # the step of the stat that records the resolved root's identity (just before `/` is opened)
        want = max(i for i, n, a in log if n == "stat" and a == root and
                   i < next(j for j, m, b in log if m == "open" and b == os.sep))
        resolved = next(i for i, n, a in log if n == "isdir")   # realpath is over once isdir runs
        fired = 0
        late = 0
        pre = []
        for anc in ("base",) + REL:
            for kind in ("link", "newdir", "alias", "badlink"):
                for k in range(1, total + 3):
                    b, r, stale, out = _tree()
                    other, bait = out[anc]
                    path = _path_of(b, anc)
                    sub = REL[REL.index(anc) + 1:] if anc != "base" else REL

                    def on_step(i, path=path, other=other, kind=kind, k=k, sub=sub):
                        nonlocal fired
                        if i != k or os.path.islink(path) or not os.path.exists(path):
                            return
                        os.rename(path, path + ".was")
                        if kind == "link":
                            os.symlink(other, path)
                        elif kind == "badlink":    # a look-alike whose last component is NOT pycache
                            bad = os.path.join(os.path.dirname(other), "bad-" + os.path.basename(other))
                            os.makedirs(os.path.join(bad, *sub[:-1], "wrongname")) if sub else \
                                os.makedirs(os.path.join(bad, "wrongname"))
                            tail = os.path.join(bad, *sub[:-1], "wrongname") if sub else \
                                os.path.join(bad, "wrongname")
                            _bait(tail)
                            badbait.append(os.path.join(tail, "gone", f"m.{TAG}.pyc"))
                            os.symlink(bad if sub else tail, path)
                        elif kind == "alias":
                            os.symlink(path + ".was", path)
                        else:
                            os.makedirs(os.path.join(path, *sub))
                            _bait(os.path.join(path, *sub))
                        fired += 1
                        fired_at.append(i)
                    fired_at = []
                    badbait = []
                    with self.subTest(ancestor=anc, kind=kind, step=k):
                        try:
                            _run(r, on_step)
                            if kind == "badlink":
                                for bb in badbait:
                                    self.assertTrue(os.path.exists(bb), "followed a link to a "
                                                    "directory that is not <x>/mokata/pycache")
                            elif kind != "newdir" and fired_at and fired_at[0] >= resolved:
                                self.assertTrue(os.path.exists(bait), "deleted OUTSIDE the cache "
                                                "(swap landed AFTER realpath)")
                            elif kind != "newdir" and not os.path.exists(bait):
                                pre.append((anc, kind, k))
                            if kind == "newdir" and fired_at and fired_at[0] > want:
                                late += 1
                                self.assertTrue(os.path.exists(
                                    os.path.join(path, *sub, "gone", f"m.{TAG}.pyc")),
                                    "deleted in a NEW directory that was not the resolved root")
                        finally:
                            self._cleanup(b)
        self.assertGreater(fired, 5 * 3 * 10, "the seam rarely fired")
        sys.stderr.write("PRE-REALPATH swaps whose look-alike (<x>/mokata/pycache, user-owned) was "
                         f"pruned: {len(pre)} cases, all with step < {resolved}: "
                         f"{all(k < resolved for _a, _k, k in pre)}\n")
        self.assertGreater(late, 5, "no post-identity swap was graded")

    def test_a_legitimately_symlinked_cache_still_prunes_the_real_root(self):
        for anc in ("home", ".cache"):
            base = os.path.realpath(tempfile.mkdtemp(prefix="mk-r3-leg-"))
            self.addCleanup(shutil.rmtree, base, True)
            real = os.path.join(base, "real-" + anc)
            parts = REL[REL.index(anc) + 1:]
            os.makedirs(os.path.join(real, *parts))
            stale = _bait(os.path.join(real, *parts))
            pre = os.path.join(base, *REL[:REL.index(anc)])
            os.makedirs(pre, exist_ok=True)
            os.symlink(real, os.path.join(pre, anc))
            with self.subTest(symlinked=anc):
                self.assertEqual(1, PC.prune(os.path.join(base, *REL)))
                self.assertFalse(os.path.exists(stale))

    def test_a_root_owned_by_another_uid_prunes_nothing(self):
        base, root, stale, _o = _tree()
        self.addCleanup(self._cleanup, base)
        n, _ = _run(root, lambda i: None, euid=os.geteuid() + 1)
        self.assertEqual(0, n)
        self.assertTrue(os.path.exists(stale))



class ARelativeCacheHomeGivesNoPrefix(unittest.TestCase):
    """Each platform by its own rule (`nt=` explicit), so the cell means the same on every OS."""

    def test_posix(self):
        self.assertIsNone(PC.bytecode_prefix({"XDG_CACHE_HOME": "rel/dir"}, nt=False))
        # USERPROFILE beside HOME: the POSIX rule never reads it (and `~` on Windows does)
        self.assertEqual("/h/.cache/mokata/pycache",
                         PC.bytecode_prefix({"XDG_CACHE_HOME": "rel", "HOME": "/h",
                                             "USERPROFILE": "C:\\other"}, nt=False))

    def test_windows(self):
        self.assertIsNone(PC.bytecode_prefix({"XDG_CACHE_HOME": "rel\\dir"}, nt=True))
        self.assertEqual("C:\\h\\.cache\\mokata\\pycache",
                         PC.bytecode_prefix({"XDG_CACHE_HOME": "rel", "USERPROFILE": "C:\\h"},
                                            nt=True))


@unittest.skipUnless(os.name == "nt", "the Windows behaviour, on Windows")
class OnWindowsThePruneIsANoOp(unittest.TestCase):
    """As designed: no prune on Windows (its prefix drops the drive; a decoded path is unsafe)."""

    def test_a_stale_pyc_under_a_real_root_is_kept(self):
        base = os.path.realpath(tempfile.mkdtemp(prefix="mk-r3-nt-"))
        self.addCleanup(shutil.rmtree, base, True)
        root = os.path.join(base, *REL)
        stale = _bait(root)
        self.assertEqual(0, PC.prune(root))
        self.assertTrue(os.path.exists(stale))


if __name__ == "__main__":
    unittest.main()
