"""0.0.28 lane C3 — `CASEFOLD-EVENT-PATH-PLATFORM-CASE`: one file typed three ways, or reached through a
case-flipped repo ROOT, is ONE record in the disk's case, on every OS.

Business rules (note C §3e, rule page §3e):
  * `record_name` decides containment FIRST by identity (the ancestor that IS the root), then spells every
    existing component as the directory holds it (`fs_identity.disk_spelling`, scandir only);
  * an event is never dropped because the root was typed in another case; a `.GIT/hooks` write is kept
    and flagged `private` + `git_hook`;
  * a deleted file resolves against the keys already recorded, only where the directory folds;
  * a directory's listing is re-read at most once per second, never per event: a 200k-entry directory
    under churn costs listings linear in elapsed time, not in events.
Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import unittest
from unittest import mock

import _support  # noqa: F401

from mokata import fs_identity as F
from mokata.ide import bus as B
from mokata.ide import snapshots as S


def _tool(name: str) -> str:
    """A spawned program by its resolved path (never a bare name: `_windows_portability`)."""
    return shutil.which(name) or name



def _touch(path: str, text: str = "x") -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def _flip(path: str) -> str:
    head, tail = os.path.split(path.rstrip("/\\"))
    return os.path.join(head, tail.swapcase())


class _Repo(unittest.TestCase):
    def setUp(self):
        t = tempfile.mkdtemp(prefix="mk-c3-")
        self.addCleanup(shutil.rmtree, t, True)
        self.base = os.path.realpath(t)
        self.root = os.path.join(self.base, "repo")
        os.makedirs(os.path.join(self.root, ".mokata", "temp_local"))
        _touch(os.path.join(self.root, "src", "a.py"))
        os.makedirs(os.path.join(self.root, ".git", "hooks"))
        F.forget()
        self.folds = F.folds_case(self.root)

    def key(self, path):
        got = B._edited(self.root, path, self.root)
        return None if got is None else got["path"]


class OneKey(_Repo):
    def test_three_spellings_one_key(self):
        keys = {self.key("src/A.py"), self.key("SRC/a.py"), self.key("src//a.py"),
                self.key(os.path.join(self.root, "src", "a.py"))}
        if self.folds:
            self.assertEqual({"src/a.py"}, keys)
        else:
            self.assertEqual({"src/a.py", "src/A.py", "SRC/a.py"}, keys,
                             "case-sensitive: distinct names are distinct files (correct)")

    def test_snapshot_capture_records_the_disk_spelling(self):
        if not self.folds:
            self.skipTest("NOT RUN here: the flip names no file on a case-sensitive volume")
        snap = S.capture(self.root, "SRC/A.py", S.AFTER, "S1")
        self.assertEqual("src/a.py", snap.path)


class FlippedRoot(_Repo):
    def test_an_absolute_path_through_a_flipped_root_is_kept(self):
        ab = os.path.join(_flip(self.root), "src", "a.py")
        if self.folds:
            self.assertEqual("src/a.py", self.key(ab))
        else:
            self.assertIsNone(self.key(ab), "case-sensitive: another directory, outside (correct)")

    def test_a_flipped_root_into_dot_git_hooks_is_flagged(self):
        ab = os.path.join(_flip(self.root), ".GIT", "hooks", "pre-commit")
        _touch(os.path.join(self.root, ".git", "hooks", "pre-commit"))
        got = B._edited(self.root, ab, self.root)
        if not self.folds:
            self.assertIsNone(got)
            return
        self.assertEqual({"path": ".git/hooks/pre-commit", "private": True, "git_hook": True,
                          "note": S.PRIVATE_NOTE}, got)

    def test_outside_stays_outside(self):
        _touch(os.path.join(self.base, "other", "a.py"))
        self.assertIsNone(self.key(os.path.join(self.base, "other", "a.py")))
        self.assertIsNone(self.key(os.path.join(self.root + "2", "a.py")))


class NormTwin(_Repo):
    """An NFD-typed name with no cased letter opens the NFC file on APFS and NTFS-normalising hosts:
    ONE key, the disk's (NFC) spelling."""

    def test_nfd_and_nfc_are_one_key(self):
        nfc = unicodedata.normalize("NFC", "が-1")       # NO cased letter anywhere (".py" has some)
        nfd = unicodedata.normalize("NFD", nfc)
        self.assertNotEqual(nfc, nfd)
        _touch(os.path.join(self.root, "src", nfc))
        if not os.path.exists(os.path.join(self.root, "src", nfd)):
            self.skipTest("NOT RUN: this volume does not open an NFD twin as the NFC file "
                          "(normalisation-sensitive: two names are two files, correct)")
        self.assertEqual({"src/" + nfc}, {self.key("src/" + nfc), self.key("src/" + nfd)})


class DeleteAfterEdit(_Repo):
    def test_a_deleted_file_typed_in_another_case_is_one_key(self):
        if not self.folds:
            self.skipTest("NOT RUN here: case-sensitive volume, the typed tail is correct")
        snap = S.capture(self.root, "src/a.py", S.AFTER, "S1")
        self.assertEqual("src/a.py", snap.path)
        os.remove(os.path.join(self.root, "src", "a.py"))
        gone = S.capture(self.root, "SRC/A.py", S.AFTER, "S1")
        self.assertEqual("src/a.py", gone.path)
        self.assertEqual(S.ABSENT, gone.state)

    def test_a_new_file_takes_its_typed_spelling(self):
        self.assertEqual("src/New.py", self.key("src/New.py"))


class BigDirAndChurn(_Repo):
    """Scale: a 200k-entry directory is listed once per second at most, never per event; N creates
    interleaved with N events cost listings linear in elapsed (fake) time."""

    N_BIG = 200_000

    def test_a_big_directory_under_churn_is_not_rescanned_per_event(self):
        big = os.path.join(self.root, "big")
        os.makedirs(big)
        for i in range(self.N_BIG):
            with open(os.path.join(big, f"f{i}.txt"), "w", encoding="utf-8"):
                pass
        clock = [1000.0]
        scans = []
        real = os.scandir

        def scandir(p="."):
            scans.append(p)
            return real(p)
        n = 2000
        with mock.patch.object(F, "_clock", side_effect=lambda: clock[0]), \
                mock.patch("os.scandir", side_effect=scandir):
            for i in range(n):
                _touch(os.path.join(big, f"new{i}.txt"))
                self.assertEqual(f"big/new{i}.txt", self.key(f"big/new{i}.txt"))
                self.assertEqual(f"big/f{i}.txt", self.key(f"big/F{i}.TXT") if self.folds
                                 else f"big/f{i}.txt")
                clock[0] += 0.001                       # 2000 events in 2 (fake) seconds
        big_scans = [p for p in scans if os.path.realpath(str(p)) == os.path.realpath(big)]
        self.assertLessEqual(len(big_scans), 4, f"{len(big_scans)} listings of a 200k directory "
                             f"for {n} events")

    def test_the_entry_cache_expires_with_the_listing(self):
        if not self.folds:
            self.skipTest("NOT RUN here: a case-only rename needs a folding volume")
        clock = [1000.0]
        with mock.patch.object(F, "_clock", side_effect=lambda: clock[0]):
            self.assertEqual("src/a.py", self.key("SRC/A.PY"))
            os.rename(os.path.join(self.root, "src", "a.py"), os.path.join(self.root, "src", "x"))
            os.rename(os.path.join(self.root, "src", "x"), os.path.join(self.root, "src", "A.py"))
            clock[0] += 1.5
            self.assertEqual("src/A.py", self.key("src/a.py"))


class Bounded(unittest.TestCase):
    """The listing cache holds at most `MAX_DIRS` directories, least recently used out first (verify M5)."""

    def test_the_listing_cache_keeps_at_most_max_dirs(self):
        t = os.path.realpath(tempfile.mkdtemp(prefix="mk-c3lru-"))
        self.addCleanup(shutil.rmtree, t, True)
        dirs = [os.path.join(t, f"d{i}") for i in range(6)]
        for d in dirs:
            _touch(os.path.join(d, "a.txt"))
        F.forget()
        self.addCleanup(F.forget)
        with mock.patch.object(F, "MAX_DIRS", 3):
            for d in dirs:
                F.disk_spelling(d, "a.txt")
            self.assertEqual(3, len(F._LISTINGS))
            self.assertEqual(dirs[3:], list(F._LISTINGS))
            F.disk_spelling(dirs[3], "a.txt")               # used again: the most recent now
            F.disk_spelling(dirs[0], "a.txt")
            self.assertEqual([dirs[5], dirs[3], dirs[0]], list(F._LISTINGS))


@unittest.skipUnless(sys.platform == "darwin",
                     "NOT RUN here: the case-sensitive cell is a hdiutil APFS image (macOS)")
class _CaseSensitive(_Repo):
    """The same repo on a case-SENSITIVE APFS image (`hdiutil`, no sudo): two spellings are two files,
    and one file is still one key (verify M5). Attach refused: NOT RUN, never green."""

    def setUp(self):
        t = tempfile.mkdtemp(prefix="mk-c3cs-")
        self.addCleanup(shutil.rmtree, t, True)
        img, mnt = os.path.join(t, "cs.dmg"), os.path.join(t, "mnt")
        os.makedirs(mnt)
        p = subprocess.run([_tool("hdiutil"), "create", "-size", "20m", "-fs", "Case-sensitive APFS",
                            "-volname", "mkc3", img], capture_output=True, text=True)
        if p.returncode == 0:
            p = subprocess.run([_tool("hdiutil"), "attach", "-nobrowse", "-mountpoint", mnt, img],
                               capture_output=True, text=True)
        if p.returncode != 0:
            self.skipTest(f"NOT RUN: hdiutil refused the case-sensitive image ({p.stderr.strip()[:160]})")
        self.addCleanup(subprocess.run, [_tool("hdiutil"), "detach", "-force", mnt], capture_output=True)
        self.base = os.path.realpath(mnt)
        self.root = os.path.join(self.base, "repo")
        os.makedirs(os.path.join(self.root, ".mokata", "temp_local"))
        _touch(os.path.join(self.root, "src", "a.py"))
        os.makedirs(os.path.join(self.root, ".git", "hooks"))
        F.forget()
        self.addCleanup(F.forget)
        self.folds = F.folds_case(self.root)


class CaseSensitiveVolume(_CaseSensitive):
    def test_the_image_is_case_sensitive(self):
        self.assertFalse(self.folds)
        self.assertFalse(F.folds_case(os.path.join(self.root, "src")))

    def test_a_case_twin_made_inside_the_refresh_second_is_another_key(self):
        """p3: `readme.md` is in the listing snapshot; `README.md`, a DIFFERENT file, is created before
        the snapshot is re-read. It keeps its own name: the fold candidate is not the same file."""
        src = os.path.join(self.root, "src")
        _touch(os.path.join(src, "readme.md"), "lower")
        with mock.patch.object(F, "_clock", return_value=1000.0):
            self.assertEqual("src/readme.md", self.key("src/readme.md"))
            _touch(os.path.join(src, "README.md"), "UPPER")
            self.assertEqual("README.md", F.disk_spelling(src, "README.md"))
            self.assertEqual("src/README.md", self.key("src/README.md"))
            self.assertEqual("src/readme.md", self.key("src/readme.md"))

    def test_a_delete_typed_in_another_case_is_its_own_key(self):
        """`A.py` and `a.py` are two files here: deleting `A.py` is never merged with a recorded `a.py`."""
        snap = S.capture(self.root, "src/a.py", S.AFTER, "S1")
        self.assertEqual("src/a.py", snap.path)
        twin = _touch(os.path.join(self.root, "src", "A.py"), "y")
        os.remove(twin)
        gone = S.capture(self.root, "src/A.py", S.AFTER, "S1")
        self.assertEqual("src/A.py", gone.path)
        self.assertEqual(S.ABSENT, gone.state)


class CaseSensitiveOneKey(_CaseSensitive, OneKey):
    """`OneKey` and `FlippedRoot` on the case-sensitive image: their case-sensitive branches RUN here."""


class CaseSensitiveFlippedRoot(_CaseSensitive, FlippedRoot):
    pass


@unittest.skipUnless(sys.platform.startswith("linux") or os.name == "nt",
                     "per-directory case flags exist on ext4 casefold and NTFS")
class PerDir(unittest.TestCase):
    """A case-INSENSITIVE directory on an otherwise case-sensitive volume: identity decides."""

    def test_a_casefold_directory_gives_one_key(self):
        t = os.path.realpath(tempfile.mkdtemp(prefix="mk-c3pd-"))
        self.addCleanup(shutil.rmtree, t, True)
        root = os.path.join(t, "repo")
        os.makedirs(os.path.join(root, "ci"))
        if os.name == "nt":
            p = subprocess.run([_tool("fsutil"), "file", "setCaseSensitiveInfo",
                                os.path.join(root, "ci"), "enable"], capture_output=True, text=True)
            if p.returncode != 0:
                self.skipTest(f"NOT RUN: setCaseSensitiveInfo needs admin + the WSL component "
                              f"({p.stdout.strip()[:120]})")
            _touch(os.path.join(root, "ci", "a.py"))
            _touch(os.path.join(root, "ci", "A.py"), "y")
            got = {B._edited(root, "ci/a.py", root)["path"], B._edited(root, "ci/A.py", root)["path"]}
            self.assertEqual({"ci/a.py", "ci/A.py"}, got, "a case-sensitive dir: two files")
            return
        if subprocess.run([_tool("chattr"), "+F", os.path.join(root, "ci")],
                          capture_output=True).returncode != 0:
            self.skipTest("NOT RUN: chattr +F needs an ext4 made with -O casefold (a sudo loop file)")
        _touch(os.path.join(root, "ci", "a.py"))
        F.forget()
        self.assertEqual("ci/a.py", B._edited(root, "ci/A.PY", root)["path"])


if __name__ == "__main__":
    unittest.main()
