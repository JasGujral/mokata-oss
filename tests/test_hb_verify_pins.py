"""Verifier pins for 0.0.29 H-b: each test kills a mutant (V1 / V2 / V7) that the lane's own tests let
survive (see docs/build/handoff/0029-H-b.verify.report.md)."""
from __future__ import annotations

import os
import shutil
import stat
import sys
import tempfile
import unicodedata
import unittest
from unittest import mock

import _support  # noqa: F401
from test_hb_verify_attacks import rename_pinned_root

from mokata import fs_identity as FI
from mokata.ide import diffs as D

INSIDE, OUTSIDE, GIT = b"inside-ok", b"outside-secret", b"git-config-marker"
OBJ = b"object-db-marker"


def w(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


class Tree:
    def __init__(self, tc):
        base = os.path.realpath(tempfile.mkdtemp(prefix="mk-hbv-"))
        tc.addCleanup(shutil.rmtree, base, True)
        self.base = base
        self.repo = os.path.join(base, "repo")
        self.out = os.path.join(base, "outside")
        w(os.path.join(self.repo, "docs", "config"), INSIDE)
        w(os.path.join(self.repo, ".git", "config"), GIT)
        w(os.path.join(self.repo, ".mokata", "x"), GIT)
        w(os.path.join(self.out, "docs", "config"), OUTSIDE)
        w(os.path.join(self.out, "f.txt"), OUTSIDE)

    def p(self, *a):
        return os.path.join(self.repo, *a)


class PinsTheBuilderLeftWeak(unittest.TestCase):
    """Each of these kills a verifier mutant (V1 / V2 / V7) that the lane's own tests let survive."""

    def test_dotdot_in_the_middle_never_climbs(self):                      # V1
        t = Tree(self)
        self.assertIsNone(FI.open_beneath(t.repo, ["..", "outside", "f.txt"]))

    def test_the_gitfile_itself_is_not_a_readable_store(self):             # V2
        t = Tree(self)
        gd = os.path.join(t.base, "gitdir")
        w(os.path.join(gd, "HEAD"), GIT)
        shutil.rmtree(t.p(".git"))
        w(t.p(".git"), ("gitdir: " + gd + "\n").encode())
        self.assertIsNone(FI.open_beneath(t.repo, [".git"]))

    def test_root_swapped_between_the_decision_and_open_beneath(self):     # V7
        t = Tree(self)
        orig = FI.open_beneath

        def spy(root, parts, **k):                      # round 2: `root_id=` became `pin=`
            if rename_pinned_root(t.repo, t.repo + ".aside"):   # Windows: the held root cannot move
                os.symlink(t.out, t.repo)
            return orig(root, parts, **k)
        with mock.patch.object(FI, "open_beneath", spy):
            got = D.open_contained(t.repo, "docs/config")
        data = got[0].read() if got else None
        self.assertNotEqual(OUTSIDE, data, "bytes of OUTSIDE returned: the pin is not carried")



if __name__ == "__main__":
    unittest.main()
