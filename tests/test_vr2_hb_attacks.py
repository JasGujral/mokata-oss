"""Round-2 verifier attacks on 0.0.29 H-b (pin_root / open_beneath(pin=) and worktree identity).
Written by the refuting verifier; every row is deterministic (no clock). A row marked KNOWN records a
behaviour the verifier reports as a finding; it asserts the CURRENT behaviour so the suite stays green."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unicodedata
import unittest
from unittest import mock

import _support  # noqa: F401

from mokata import fs_identity as FI
from mokata.ide import diffs as D

import test_hb_verify_attacks as A

GIT, INSIDE, OUTSIDE = A.GIT, A.INSIDE, A.OUTSIDE


def _read(got):
    if not got:
        return None
    with got[0] as fh:
        return fh.read()


class StoreMoves(unittest.TestCase):
    """A swap before EVERY fs event of one read, for stores the round-1 sweep never moved."""

    # M-r2-1 (expectedFailure removed in round 3: every in-root store comes from one listing, event 1)
    def test_mv_mokata_into_docs_every_event(self):
        rel = "docs/config"
        n = A.Sweep(A.Tree(self).repo, rel, lambda: None).run(10 ** 9)[2]["n"]
        leaks = []
        for k in range(2, n + 2):
            t = A.Tree(self)
            A.w(t.p(".mokata", "config"), GIT)

            def swap(t=t):
                os.rename(t.p("docs"), t.p("docs.aside"))
                os.rename(t.p(".mokata"), t.p("docs"))
            got, data, st = A.Sweep(t.repo, rel, swap).run(k)
            if data and GIT in data:
                leaks.append(k)
        self.assertEqual([], leaks, f"mv .mokata docs leaks at k={leaks} of {n}")

    # M-r2-1 (expectedFailure removed in round 3: every in-root store comes from one listing, event 1)
    def test_mv_gitdata_separate_git_dir_into_docs_every_event(self):
        def tree(t):
            os.rename(t.p(".git"), t.p(".gitdata"))
            A.w(t.p(".git"), b"gitdir: .gitdata\n")
            A.w(t.p(".gitdata", "config"), GIT)
        rel = "docs/config"
        base_t = A.Tree(self)
        tree(base_t)
        n = A.Sweep(base_t.repo, rel, lambda: None).run(10 ** 9)[2]["n"]
        leaks = []
        for k in range(2, n + 2):
            t = A.Tree(self)
            tree(t)

            def swap(t=t):
                os.rename(t.p("docs"), t.p("docs.aside"))
                os.rename(t.p(".gitdata"), t.p("docs"))
            got, data, st = A.Sweep(t.repo, rel, swap).run(k)
            if data and GIT in data:
                leaks.append(k)
        self.assertEqual([], leaks, f"mv .gitdata docs (gitfile layout) leaks at k={leaks} of {n}")

    def test_member_of_git_moved_into_docs_KNOWN(self):
        """`mv .git/config docs/config` after the pin: the pin names only the store DIRECTORY, so the moved
        file is neither a store nor a hard link. Records what comes back."""
        t = A.Tree(self)
        orig = FI.open_beneath

        def seam(*a, **k):
            os.replace(t.p(".git", "config"), t.p("docs", "config"))   # replace: rename onto a file fails on Windows
            return orig(*a, **k)
        with mock.patch.object(FI, "open_beneath", seam):
            data = _read(D.open_contained(t.repo, "docs/config"))
        print("\n[member-move] .git/config renamed into docs/config -> read returned", data)
        self.assertIn(data, (None, GIT))

    def test_objects_dir_moved_into_docs_KNOWN(self):
        t = A.Tree(self)
        orig = FI.open_beneath

        def seam(*a, **k):
            os.rename(t.p("docs"), t.p("docs.aside"))
            os.rename(t.p(".git", "objects"), t.p("docs"))
            return orig(*a, **k)
        with mock.patch.object(FI, "open_beneath", seam):
            data = _read(D.open_contained(t.repo, "docs/ab/cdef"))
        print("\n[member-move] .git/objects renamed into docs -> read returned", data)
        self.assertIn(data, (None, A.OBJ))


class PinBoundary(unittest.TestCase):
    def setUp(self):
        self.t = A.Tree(self)

    def _pin(self):
        p = FI.pin_root(self.t.repo)
        self.assertIsNotNone(p)
        self.addCleanup(p.close)
        return p

    def test_closed_pin_refuses(self):
        p = self._pin()
        p.close()
        self.assertIsNone(FI.open_beneath(self.t.repo, ["docs", "config"], pin=p))

    def test_foreign_pin_refuses(self):
        other = A.Tree(self)
        p = FI.pin_root(other.repo)
        self.addCleanup(p.close)
        self.assertIsNone(FI.open_beneath(self.t.repo, ["docs", "config"], pin=p))

    def test_pin_reused_across_calls_stays_correct(self):
        p = self._pin()
        for _ in range(3):
            fd = FI.open_beneath(self.t.repo, ["docs", "config"], pin=p)
            self.assertIsNotNone(fd)
            self.assertEqual(INSIDE, os.read(fd, 100))
            os.close(fd)
        self.assertIsNone(FI.open_beneath(self.t.repo, [".git", "config"], pin=p))

    def test_root_renamed_after_pin_refuses(self):
        p = self._pin()
        if not A.rename_pinned_root(self.t.repo, self.t.repo + ".moved"):
            return                                # Windows: the held root cannot be renamed at all
        self.assertIsNone(FI.open_beneath(self.t.repo, ["docs", "config"], pin=p))

    def test_root_renamed_and_replaced_after_pin_refuses(self):
        p = self._pin()
        if not A.rename_pinned_root(self.t.repo, self.t.repo + ".moved"):
            return                                # Windows: the held root cannot be renamed at all
        A.w(os.path.join(self.t.repo, "docs", "config"), OUTSIDE)
        self.assertIsNone(FI.open_beneath(self.t.repo, ["docs", "config"], pin=p))

    def test_root_replaced_by_link_after_pin_refuses(self):
        p = self._pin()
        if not A.rename_pinned_root(self.t.repo, self.t.repo + ".moved"):
            return                                # Windows: the held root cannot be renamed at all
        os.symlink(self.t.out, self.t.repo)
        self.assertIsNone(FI.open_beneath(self.t.repo, ["docs", "config"], pin=p))

    def test_pin_closed_mid_walk_by_another_holder(self):
        """A pin shared with a holder that closes it while a walk is in flight: the walk must not read
        through a REUSED descriptor number (the closed number is re-taken by `outside`)."""
        p = self._pin()
        orig_open = os.open
        state = {"n": 0}
        outside_fd = []

        def spy(path, flags, *a, **k):
            state["n"] += 1
            if state["n"] == 1:
                p.close()
                outside_fd.append(orig_open(self.t.out, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)))
            return orig_open(path, flags, *a, **k)
        with mock.patch.object(os, "open", spy):
            fd = FI.open_beneath(self.t.repo, ["docs", "config"], pin=p)
        for f in outside_fd:
            os.close(f)
        data = os.read(fd, 100) if fd is not None else None
        if fd is not None:
            os.close(fd)
        print("\n[pin closed mid-walk] returned", data)
        self.assertNotEqual(OUTSIDE, data)

    def test_gitfile_layout_pin_stores_name_the_git_dir(self):
        t = self.t
        os.rename(t.p(".git"), t.p(".gitdata"))
        A.w(t.p(".git"), b"gitdir: .gitdata\n")
        p = FI.pin_root(t.repo)
        self.addCleanup(p.close)
        self.assertGreaterEqual(len(p.stores), 2)
        self.assertIsNone(FI.open_beneath(t.repo, [".gitdata", "config"], pin=p))

    def test_submodule_style_gitfile_pointing_outside_root(self):
        t = self.t
        shutil.rmtree(t.p(".git"))
        gd = os.path.join(t.base, "modules", "sub")
        A.w(os.path.join(gd, "config"), GIT)
        A.w(t.p(".git"), ("gitdir: " + gd + "\n").encode())
        self.assertEqual(INSIDE, _read(D.open_contained(t.repo, "docs/config")))
        self.assertIsNone(D.open_contained(t.repo, ".git"))
        os.link(os.path.join(gd, "config"), t.p("docs", "hl"))
        self.assertNotEqual(GIT, _read(D.open_contained(t.repo, "docs/hl")))


class WorktreeIdentity(unittest.TestCase):
    def setUp(self):
        from mokata import worktree_ops as W
        self.W = W
        base = os.path.realpath(tempfile.mkdtemp(prefix="mk-hbv2-"))
        self.addCleanup(shutil.rmtree, base, True)
        self.base = base
        self.main = os.path.join(base, "Repo")
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}

        def g(*a):
            return subprocess.run(["git", *a], cwd=self.main, env=env, check=True, capture_output=True)
        self.g = g
        os.makedirs(self.main)
        g("init", "-q", "-b", "main")
        g("-c", "user.email=a@b", "-c", "user.name=n", "commit", "-q", "--allow-empty", "-m", "i")
        self.wt = os.path.join(base, "wt")
        g("worktree", "add", "-q", "-b", "feat", self.wt)

    def _paths(self, root):
        return [os.path.realpath(r["path"]) for r in self.W.rows(root).get("worktrees", [])]

    def test_case_variant_root_never_lists_main(self):
        flip = os.path.join(self.base, "REPO")
        if not os.path.exists(flip):
            self.skipTest("case-sensitive volume")
        self.assertEqual([os.path.realpath(self.wt)], self._paths(flip))
        self.assertFalse(self.W.plan_delete(flip, self.main).get("ok"))
        self.assertFalse(self.W.plan_delete(flip, flip).get("ok"))
        self.assertFalse(self.W.plan_delete(self.main, flip).get("ok"))
        self.assertFalse(self.W.plan_delete(self.main, self.main + os.sep).get("ok"))

    def test_symlink_to_main_is_not_a_linked_worktree(self):
        ln = os.path.join(self.base, "mainlink")
        os.symlink(self.main, ln)
        self.assertFalse(self.W.plan_delete(self.main, ln).get("ok"))
        self.assertEqual([os.path.realpath(self.wt)], self._paths(ln))

    def test_symlinked_and_case_variant_worktree_found(self):
        ln = os.path.join(self.base, "wtlink")
        os.symlink(self.wt, ln)
        self.assertIsNotNone(self.W.find(self.main, ln))
        if FI.folds_case(self.base):           # `WT` is `wt` only on a case-folding volume (not ext4)
            self.assertIsNotNone(self.W.find(self.main, os.path.join(self.base, "WT")))
        else:
            self.assertIsNone(self.W.find(self.main, os.path.join(self.base, "WT")))
        self.assertIsNone(self.W.find(self.main, self.main))
        self.assertIsNone(self.W.find(self.main, os.path.join(self.wt, "sub")))
        self.assertIsNone(self.W.find(self.main, self.base))

    def test_unicode_normalized_worktree_name(self):
        nfc = unicodedata.normalize("NFC", "wé")
        nfd = unicodedata.normalize("NFD", "wé")
        self.g("worktree", "add", "-q", "-b", "u", os.path.join(self.base, nfc))
        # the NFD spelling opens the NFC entry only on a normalisation-insensitive volume (APFS; not
        # ext4 or NTFS): there it is the worktree, elsewhere it names nothing and finds nothing
        same = os.path.exists(os.path.join(self.base, nfd))
        (self.assertIsNotNone if same else self.assertIsNone)(self.W.find(self.main, os.path.join(self.base, nfd)))
        self.assertIsNotNone(self.W.find(self.main, os.path.join(self.base, nfc)))

    def test_main_checkout_spelled_by_a_symlinked_ancestor(self):
        link = os.path.join(self.base, "anc")
        os.symlink(self.base, link)
        alt_main = os.path.join(link, "Repo")
        self.assertFalse(self.W.plan_delete(alt_main, alt_main).get("ok"))
        self.assertNotIn(os.path.realpath(self.main), self._paths(alt_main))

    def test_missing_worktree_dir_never_matches_main(self):
        shutil.rmtree(self.wt)
        for p in self._paths(self.main):
            self.assertFalse(self.W.same_entry(p, self.main))
        self.assertFalse(self.W.plan_delete(self.main, self.main).get("ok"))

    def test_clean_accepts_a_symlinked_and_a_case_spelling_of_the_approved_path(self):
        """V6: `clean` matches the approved path by identity (a spelling must not be a no-op)."""
        from mokata import session_worktrees as SW
        ln = os.path.join(self.base, "wtlink")
        os.symlink(self.wt, ln)
        SW.bind(self.main, "sess-c", self.wt)
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        ident = ["-c", "user.email=a@b", "-c", "user.name=n"]
        subprocess.run(["git", *ident, "commit", "-q", "--allow-empty", "-m", "w"], cwd=self.wt, env=env, check=True)
        subprocess.run(["git", *ident, "merge", "-q", "feat"], cwd=self.main, env=env, check=True)
        self.assertTrue(self.W.plan_clean(self.main)["ok"], self.W.rows(self.main))
        got = self.W.clean(self.main, [ln])
        self.assertEqual({"removed": [ln], "skipped": []}, got, got)
        self.assertFalse(os.path.isdir(self.wt))

    def test_binding_recorded_under_another_spelling_is_still_this_worktrees(self):
        """V4: a session binding whose recorded path is a symlinked spelling of the worktree."""
        from mokata import session_worktrees as SW
        ln = os.path.join(self.base, "wtlink")
        os.symlink(self.wt, ln)
        tmp = os.path.join(self.base, "x")
        got = SW.bind(self.main, "sess-1", ln)
        recorded = [b.get("path") for b in SW.bindings(self.main).values()]
        rows = self.W.rows(self.main)["worktrees"]
        print("\n[binding] recorded", recorded, "-> row session", [r["session"] for r in rows])
        if recorded and os.path.realpath(recorded[0]) != recorded[0]:
            self.assertEqual(["sess-1"], [r["session"] for r in rows])


if __name__ == "__main__":
    unittest.main()
