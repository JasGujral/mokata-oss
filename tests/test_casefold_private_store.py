"""0.0.27 CASEFOLD-FIX - mokata's private store and git's store are NEVER served, however the name
is spelled.

THE DEFECT (0.0.26, escalated by the P9 verifier). `snapshots.rel_path` refused `.mokata` with a
case-SENSITIVE string compare. On a case-insensitive filesystem (default macOS APFS, Windows NTFS)
`.MOKATA/private.txt` opens `.mokata/private.txt`, so `/api/highlight?path=.MOKATA/...` returned the
store's text and `/api/image/raw` its images. `.git/` was not refused by the shared helper at all:
`/api/file?path=.git/config` served git's config (remote URLs, credential helpers).

Business rules (each test says which):
  R1  no IDE read route serves a byte from `.mokata/` or `.git/` (at any depth), whatever the
      spelling: case flips, mixed-case nested components, a Unicode canonical twin (the Kelvin
      sign), trailing dots/spaces and an NTFS stream suffix (which Windows resolves to the same
      entry), a symlink whose name or target differs only by case, an 8.3 short name;
  R2  the refusal is the existing one (400 PathRefused JSON), never a 500, never a 200;
  R3  the decision does not depend on the filesystem: the NAME rule is the same on Linux, macOS
      and Windows, and an IDENTITY rule (same file as the repo's own `.git`/`.mokata`) backs it for
      what spelling cannot see;
  R4  it is precise: `.github/`, `.gitignore`, `.mokata-notes/` and a name that only LOOKS similar
      on paper (dotless i, sharp s) are still ordinary repo files;
  R5  fail closed: a name that cannot be folded, or a path whose identity cannot be checked, is
      refused, not served.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unicodedata
import unittest
from unittest import mock

import _support  # noqa: F401

from mokata import repo_paths as RP
from mokata.ide import snapshots as S

TOKEN, PORT = "tok", 9999
BASE = f"http://127.0.0.1:{PORT}"
MARK = "PRIVATE-STORE-BYTES-7f3a"
GIT_MARK = "GIT-CONFIG-BYTES-91c2"
KELVIN = "K"               # KELVIN SIGN: NFC turns it into a Latin K


def git(d, *a):
    subprocess.run(["git", "-C", d, *a], check=True, capture_output=True, stdin=subprocess.DEVNULL)


def write(d, rel, data):
    p = os.path.join(d, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as fh:
        fh.write(data if isinstance(data, bytes) else data.encode("utf-8"))
    return p


def png_with(marker: str) -> bytes:
    import struct
    import zlib

    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    rows = zlib.compress((b"\0" + b"\0" * 6) * 2)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
            + chunk(b"tEXt", b"k\0" + marker.encode()) + chunk(b"IDAT", rows)
            + chunk(b"IEND", b""))


def aliases_on_disk(root: str, spelled: str, real: str) -> bool:
    """Does THIS filesystem open `real` when asked for `spelled`? Probed, never assumed."""
    a, b = os.path.join(root, *spelled.split("/")), os.path.join(root, *real.split("/"))
    try:
        return os.path.exists(a) and os.path.samefile(a, b)
    except (OSError, ValueError):
        return False


# Variants of the private names that EVERY platform must refuse by name (R1, R3).
MOKATA_SPELLINGS = [".MOKATA", ".Mokata", ".mOkAtA", ".mo" + KELVIN + "ata", ".MO" + KELVIN + "ATA",
                    ".mokata.", ".mokata ", ".mokata. .", ".MOKATA..", ".mokata::$INDEX_ALLOCATION",
                    unicodedata.normalize("NFD", ".MOKATA")]
GIT_SPELLINGS = [".GIT", ".Git", ".gIT", ".git.", ".git ", ".GIT. ", ".git::$INDEX_ALLOCATION"]
# Names that look like a private name on paper but that casefold keeps apart (R4): the dotless i
# stays dotless, the dotted capital I folds to i + combining dot, and the sharp s folds to "ss",
# which is in neither name. Where a filesystem aliases one anyway, the identity rule refuses it.
LOOKALIKES = [".gıt", ".GİT", ".mokataß", ".gitss"]


class Repo(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.d = os.path.realpath(self._t.name)
        git(self.d, "init", "-q", "-b", "main")
        git(self.d, "config", "user.email", "t@t")
        git(self.d, "config", "user.name", "t")
        git(self.d, "config", "commit.gpgsign", "false")
        with open(os.path.join(self.d, ".git", "config"), "a", encoding="utf-8") as fh:
            fh.write(f"# {GIT_MARK}\n")
        write(self.d, "src/app.py", "def f():\n    return 1\n")
        write(self.d, ".github/workflows/ci.yml", "name: ci\n")
        write(self.d, ".gitignore", "*.pyc\n")
        write(self.d, ".mokata-notes/n.txt", "notes\n")
        write(self.d, ".mokata/private.txt", f"{MARK}\nline two\n")
        write(self.d, ".mokata/private.png", png_with(MARK))
        write(self.d, ".mokata/private.csv", f"a,b\n{MARK},2\n")
        write(self.d, ".mokata/temp_local/deep.txt", f"{MARK}\n")
        write(self.d, "pkg/.mokata/private.txt", f"{MARK}\n")
        write(self.d, "pkg/.git/config", f"{GIT_MARK}\n")
        git(self.d, "add", "-A")
        git(self.d, "add", "-f", ".mokata", "pkg/.mokata")
        git(self.d, "commit", "-q", "-m", "init")


class TheRules(Repo):
    """R1/R3/R4/R5 at the shared predicate - the one every route goes through."""

    def test_every_spelling_of_the_store_is_refused_by_name_on_every_platform(self):
        for v in MOKATA_SPELLINGS:
            for tail in ("private.txt", "temp_local/deep.txt", "TEMP_LOCAL/Deep.txt"):
                with self.subTest(v=v, tail=tail):
                    self.assertTrue(RP.is_private_name(f"{v}/{tail}"))
                    self.assertIsNone(S.rel_path(self.d, f"{v}/{tail}"))
        for v in GIT_SPELLINGS:
            with self.subTest(v=v):
                self.assertTrue(RP.is_private_name(f"{v}/config"))
                self.assertIsNone(S.rel_path(self.d, f"{v}/config"))

    def test_a_private_component_at_any_depth_is_refused_mixed_case_and_both_separators(self):
        for name in ("pkg/.MoKaTa/private.txt", "Pkg/.GiT/config", "a/b/.git/HEAD",
                     "a/b/.mokata", "a\\.MOKATA\\x", ".git", ".MOKATA"):
            with self.subTest(name=name):
                self.assertTrue(RP.is_private_name(name))
        for name in ("pkg/.MoKaTa/private.txt", "Pkg/.GiT/config", ".git", ".mokata"):
            with self.subTest(name=name):
                self.assertIsNone(S.rel_path(self.d, name))

    def test_nfd_and_nfc_spellings_fold_to_one_key(self):
        nfc, nfd = "Café", unicodedata.normalize("NFD", "Café")
        self.assertNotEqual(nfc, nfd)
        self.assertEqual(RP.fold_component(nfc), RP.fold_component(nfd))
        self.assertEqual(".mokata", RP.fold_component(".MO" + KELVIN + "ATA. "))
        self.assertEqual(".git", RP.fold_component(".GIT:stream"))
        # Two canonically EQUAL spellings whose marks are stored in a different order (the Greek
        # ypogegrammeni sorts after the acute): a filesystem that normalises opens one entry, so
        # the key must be one key - casefold on the raw marks would split them.
        a, b = "Áͅ", "Áͅ"
        self.assertEqual(unicodedata.normalize("NFC", a), unicodedata.normalize("NFC", b))
        self.assertEqual(RP.fold_component(a), RP.fold_component(b))

    def test_ordinary_dot_files_and_lookalikes_are_still_repo_files(self):
        for name in (".github/workflows/ci.yml", ".gitignore", ".mokata-notes/n.txt",
                     "src/.gitkeep", "docs/.mokatarc", "x.git/y", "my.mokata/z") + \
                tuple(f"{v}/x" for v in LOOKALIKES):
            with self.subTest(name=name):
                self.assertFalse(RP.is_private_name(name))
        self.assertEqual(".github/workflows/ci.yml", S.rel_path(self.d, ".github/workflows/ci.yml"))
        self.assertEqual(".gitignore", S.rel_path(self.d, ".gitignore"))
        self.assertEqual("src/app.py", S.rel_path(self.d, "src/app.py"))

    def test_the_identity_rule_catches_an_alias_the_name_cannot_see(self):
        """A directory symlink named nothing like `.mokata` is the store on disk. The IDENTITY
        rule is graded on its own (the name rule cannot see this name at all), on every OS."""
        try:
            os.symlink(os.path.join(self.d, ".mokata"), os.path.join(self.d, "notes"))
        except (OSError, NotImplementedError):
            self.skipTest("no symlinks on this platform")
        self.assertFalse(RP.is_private_name("notes/private.txt"))
        self.assertTrue(RP.aliases_private(self.d, "notes/private.txt"))
        self.assertTrue(RP.aliases_private(self.d, "notes"))
        self.assertFalse(RP.aliases_private(self.d, "src/app.py"))
        self.assertFalse(RP.aliases_private(self.d, "src/none/deeper.txt"))
        self.assertFalse(RP.aliases_private(self.d, ".github/workflows/ci.yml"))

    def test_the_identity_rule_sees_an_alias_deeper_in_the_tree(self):
        """In production the name is realpath'd first, so a SYMLINK never reaches this rule - but
        a bind mount (Linux) or a mount point is not resolved by realpath. A symlink stands in
        for one here: the store aliased two directories down is still the store."""
        os.makedirs(os.path.join(self.d, "src", "deep"))
        try:
            os.symlink(os.path.join(self.d, ".mokata"), os.path.join(self.d, "src", "deep", "m"))
        except (OSError, NotImplementedError):
            self.skipTest("no symlinks on this platform")
        self.assertTrue(RP.aliases_private(self.d, "src/deep/m/private.txt"))
        self.assertFalse(RP.aliases_private(self.d, "src/deep"))

    def test_a_repo_with_neither_store_refuses_nothing_by_identity(self):
        with tempfile.TemporaryDirectory() as t:
            plain = os.path.realpath(t)
            write(plain, "a/b.txt", "x\n")
            self.assertFalse(RP.aliases_private(plain, "a/b.txt"))
            self.assertEqual("a/b.txt", S.rel_path(plain, "a/b.txt"))

    def test_a_filesystem_without_file_ids_does_not_make_every_file_the_store(self):
        """FAT and some network filesystems report inode 0 for everything: an identity of 0 is
        no identity, or every file on that volume would compare equal to `.mokata`."""
        real = os.stat

        def no_ids(p, *a, **k):
            st = real(p, *a, **k)
            return os.stat_result((st.st_mode, 0, 7, st.st_nlink, st.st_uid, st.st_gid,
                                   st.st_size, int(st.st_atime), int(st.st_mtime),
                                   int(st.st_ctime)))
        with mock.patch.object(RP.os, "stat", side_effect=no_ids):
            self.assertFalse(RP.aliases_private(self.d, "src/app.py"))
            self.assertFalse(RP.aliases_private(self.d, ".mokata/private.txt"))

    def test_the_identity_rule_sees_the_git_dir_too(self):
        try:
            os.symlink(os.path.join(self.d, ".git"), os.path.join(self.d, "meta"))
        except (OSError, NotImplementedError):
            self.skipTest("no symlinks on this platform")
        self.assertTrue(RP.aliases_private(self.d, "meta/config"))

    def test_the_identity_rule_follows_this_filesystems_own_folding(self):
        if not aliases_on_disk(self.d, ".MOKATA/private.txt", ".mokata/private.txt"):
            self.skipTest("this filesystem is case-sensitive: .MOKATA is not .mokata here")
        self.assertTrue(RP.aliases_private(self.d, ".MOKATA/private.txt"))
        self.assertTrue(RP.aliases_private(self.d, ".GIT/config"))

    def test_the_identity_rule_alone_refuses_in_rel_path(self):
        """With the name rule switched off, the identity rule still refuses a disk alias."""
        try:
            os.symlink(os.path.join(self.d, ".mokata"), os.path.join(self.d, "notes"))
        except (OSError, NotImplementedError):
            self.skipTest("no symlinks on this platform")
        with mock.patch.object(RP, "is_private_name", return_value=False):
            self.assertIsNone(S.rel_path(self.d, "notes/private.txt"))
            self.assertEqual("src/app.py", S.rel_path(self.d, "src/app.py"))
            if aliases_on_disk(self.d, ".MOKATA/private.txt", ".mokata/private.txt"):
                self.assertIsNone(S.rel_path(self.d, ".MOKATA/private.txt"))

    def test_the_name_rule_alone_refuses_in_rel_path(self):
        with mock.patch.object(RP, "aliases_private", return_value=False):
            self.assertIsNone(S.rel_path(self.d, ".MOKATA/private.txt"))
            self.assertIsNone(S.rel_path(self.d, ".Git/config"))
            self.assertEqual("src/app.py", S.rel_path(self.d, "src/app.py"))

    def test_fail_closed_when_the_name_cannot_be_folded_or_the_identity_checked(self):
        import mokata_emit       # the rule's single source since 0.0.27 SEC-HARDEN (re-exported)
        with mock.patch.object(mokata_emit, "fold_component", side_effect=ValueError("cannot fold")):
            self.assertIsNone(S.rel_path(self.d, "src/app.py"))
        with mock.patch.object(RP, "aliases_private", side_effect=PermissionError("denied")):
            self.assertIsNone(S.rel_path(self.d, "src/app.py"))
        self.assertTrue(RP.is_private_name("\udcff/.MOKATA/x"))
        self.assertFalse(RP.is_private_name("\udcff/x"))

    def test_an_unreadable_component_on_the_way_is_refused_not_skipped(self):
        real = os.stat

        def stat(p, *a, **k):
            if os.path.basename(str(p)) == "src":
                raise PermissionError("denied")
            return real(p, *a, **k)
        with mock.patch.object(RP.os, "stat", side_effect=stat):
            with self.assertRaises(PermissionError):
                RP.aliases_private(self.d, "src/app.py")
            self.assertIsNone(S.rel_path(self.d, "src/app.py"))

    def test_the_locator_names_a_case_flipped_store_as_the_store(self):
        from mokata.ide.locator import resolve
        from mokata.ide.nav import PathRefused
        with self.assertRaises(PathRefused) as cm:
            resolve(self.d, ".MOKATA/private.txt:1")
        self.assertIn("mokata's own record", str(cm.exception))


class TheRoutes(Repo):
    """R1/R2 through every IDE read route that takes a path, as the page calls it."""

    def setUp(self):
        super().setUp()
        from starlette.testclient import TestClient
        from mokata.ide.server import AppState, build_app
        state = AppState(self.d, TOKEN, PORT, None, False)
        self.c = TestClient(build_app(state), base_url=BASE, follow_redirects=False)
        self.c.get(f"/pair?t={TOKEN}")

    def calls(self, path):
        """(label, response) for every read door, with `path` as the file asked for."""
        stem = path.rsplit("/", 1)[0]
        png = stem + "/private.png" if path.endswith(".txt") else path
        csv = stem + "/private.csv" if path.endswith(".txt") else path
        g, p = self.c.get, self.c.post
        yield "/api/file", g("/api/file", params={"path": path})
        yield "/api/file?outline", g("/api/file", params={"path": path, "outline": "1"})
        yield "/api/outline", g("/api/outline", params={"path": path})
        yield "/api/highlight", g("/api/highlight", params={"path": path})
        yield "/api/image/raw", g("/api/image/raw", params={"path": png})
        yield "/api/image/raw?rev", g("/api/image/raw", params={"path": png, "rev": "HEAD"})
        yield "/api/image/info", g("/api/image/info", params={"path": png})
        yield "/api/image/info?base", g("/api/image/info", params={"path": png, "base": "HEAD",
                                                                  "head": "HEAD"})
        yield "/api/diff/context", g("/api/diff/context", params={"path": path})
        yield "/api/diff/context?rev", g("/api/diff/context", params={"path": path, "rev": "HEAD"})
        yield "/api/table/info", g("/api/table/info", params={"path": csv})
        yield "/api/table/rows", g("/api/table/rows", params={"path": csv, "row": "0",
                                                               "count": "5"})
        yield "/api/usages", p("/api/usages", json={"path": path, "line": 1},
                               headers={"Origin": BASE})

    def assert_refused(self, path, *, status=True):
        for label, r in self.calls(path):
            with self.subTest(route=label, path=path):
                body = r.content
                self.assertNotIn(MARK.encode(), body)
                self.assertNotIn(GIT_MARK.encode(), body)
                if status:
                    self.assertEqual(400, r.status_code, (label, path, body[:200]))
                    self.assertIn(r.json().get("error"), ("PathRefused", "BadRequest"))

    def test_the_exact_names_are_refused_by_every_route(self):
        self.assert_refused(".mokata/private.txt")
        self.assert_refused(".git/config")

    def test_every_case_and_windows_spelling_of_the_store_is_refused_by_every_route(self):
        for v in MOKATA_SPELLINGS:
            self.assert_refused(f"{v}/private.txt")
        self.assert_refused(".Mokata/TEMP_LOCAL/deep.txt")

    def test_every_case_and_windows_spelling_of_git_is_refused_by_every_route(self):
        for v in GIT_SPELLINGS:
            self.assert_refused(f"{v}/config")

    def test_a_nested_store_in_mixed_case_is_refused_by_every_route(self):
        self.assert_refused("pkg/.MoKaTa/private.txt")
        self.assert_refused("PKG/.mokata/private.txt")
        self.assert_refused("pkg/.GiT/config")

    def test_lookalikes_never_leak_and_are_refused_where_this_filesystem_aliases_them(self):
        for v in LOOKALIKES:
            for real, tail in ((".mokata", "private.txt"), (".git", "config")):
                self.assert_refused(f"{v}/{tail}",
                                    status=aliases_on_disk(self.d, f"{v}/{tail}", f"{real}/{tail}"))

    def test_a_symlink_whose_name_or_target_differs_only_by_case_is_refused(self):
        try:
            os.symlink(".MOKATA", os.path.join(self.d, "alias"))
            os.symlink(os.path.join("..", ".mokata"), os.path.join(self.d, "src", ".Mokata"))
            os.symlink(".GIT", os.path.join(self.d, "galias"))
        except (OSError, NotImplementedError):
            self.skipTest("no symlinks on this platform")
        self.assert_refused("alias/private.txt")
        self.assert_refused("src/.Mokata/private.txt")
        self.assert_refused("galias/config")

    def test_an_8dot3_short_name_of_the_store_is_refused(self):
        if os.name != "nt":
            self.skipTest("8.3 short names exist only on Windows (NTFS/FAT); on this OS the "
                          "identity rule is graded by the symlink alias instead")
        import ctypes
        buf = ctypes.create_unicode_buffer(1024)
        n = ctypes.windll.kernel32.GetShortPathNameW(os.path.join(self.d, ".mokata"), buf, 1024)
        short = os.path.basename(buf.value) if n else ""
        if not short or short.lower() == ".mokata":
            self.skipTest("8.3 name generation is off on this volume")
        self.assert_refused(f"{short}/private.txt")
        self.assertTrue(RP.aliases_private(self.d, f"{short}/private.txt"))

    def test_the_live_view_never_lists_an_untracked_private_file_even_with_mokata_files_shown(self):
        """`?mokata=1` shows the files `mokata setup` wrote; it never makes the private store, nor a
        nested one, a row of the diff (a row the file routes would then refuse to open)."""
        write(self.d, ".mokata/later.txt", f"{MARK}\n")
        write(self.d, "pkg/.mokata/later.txt", f"{MARK}\n")
        write(self.d, "src/new.py", "x = 1\n")
        r = self.c.get("/api/live", params={"mokata": "1"})
        self.assertEqual(200, r.status_code)
        paths = [f["path"] for f in r.json()["files"]]
        self.assertIn("src/new.py", paths)
        self.assertFalse([p for p in paths if RP.is_private_name(p)], paths)
        self.assertNotIn(MARK.encode(), r.content)

    def test_ordinary_dot_files_are_still_served(self):
        for path, text in ((".github/workflows/ci.yml", "name: ci"), (".gitignore", "*.pyc"),
                           (".mokata-notes/n.txt", "notes")):
            with self.subTest(path=path):
                r = self.c.get("/api/file", params={"path": path})
                self.assertEqual(200, r.status_code)
                self.assertIn(text, r.json()["text"])
                r = self.c.get("/api/highlight", params={"path": path})
                self.assertEqual(200, r.status_code)



# ======================================================================== RECORD, never READ (round 2)
class AWriteIntoAPrivatePathIsRecordedPathOnly(unittest.TestCase):
    """R6 (coordinator ruling on verify M1): "may I READ this path" and "may I RECORD a write to it"
    are two questions. A file-tool write into `.git/` or `.mokata/` (any spelling) still produces
    its activity event and its snapshot record - PATH ONLY, marked `private path - content not
    captured`; no before/after bytes are ever stored or served. A write into `.git/hooks/` is
    flagged as "wrote a git hook". Since 0.0.27 SEC-HARDEN the gate refuses such a write until a
    human approves it (`test_sec_harden` G1); these tests drive an APPROVED write, which must still
    be recorded path-only."""

    HOOK = ".git/hooks/pre-commit"

    def setUp(self):
        from mokata.init import init_repo
        os.environ.pop("MOKATA_SESSION_ID", None)
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.d = os.path.realpath(self._t.name)
        git(self.d, "init", "-q", "-b", "main")
        init_repo(root=self.d, profile="standard", assume_yes=True, out=lambda _m: None)
        write(self.d, self.HOOK, f"#!/bin/sh\necho {MARK}-before\n")
        write(self.d, ".mokata/x", f"{MARK}-before\n")

    def objects(self):
        return sorted(f for _r, _d, fs in os.walk(os.path.join(S.store_dir(self.d), "objects"))
                      for f in fs)

    def hook(self, fn, payload):
        import io
        import json
        from contextlib import redirect_stdout
        from mokata import hook_cli
        from mokata import session_worktrees as SW
        from mokata import git_control as GC
        # The P31 worktree policy is not under test here: the write is ALLOWED, as in a session
        # that entered its worktree, so the gate takes its BEFORE. Neither is the git-control
        # gate: the write is one a human approved (`test_sec_harden` G1 grades the gate itself).
        with mock.patch.object(hook_cli, "_read_stdin_bounded", return_value=json.dumps(payload)), \
                mock.patch.object(SW, "entry_refusal", return_value=""), \
                mock.patch.object(GC, "refusal", return_value=""), \
                redirect_stdout(io.StringIO()):
            return fn([])

    def write_through_the_hooks(self, rel, tool="Write", sid="S1"):
        """What Claude Code does: PreToolUse (gate), the write, PostToolUse (dirty-track)."""
        from mokata import hook_cli
        env = {"cwd": self.d, "session_id": sid, "tool_name": tool,
               "tool_input": {"file_path": os.path.join(self.d, *rel.split("/")),
                              "content": f"{MARK}-after\n"}}
        self.hook(hook_cli.gate_guard_main, env)
        write(self.d, rel, f"#!/bin/sh\necho {MARK}-after\n")
        self.hook(hook_cli.dirty_track_main, env)
        return env

    def test_a_git_hook_write_is_recorded_flagged_and_its_bytes_never_kept(self):
        before = self.objects()
        self.write_through_the_hooks(self.HOOK)
        snaps = S.timeline(self.d, session="S1")
        self.assertIn(S.AFTER, [s.kind for s in snaps])
        for s in snaps:
            self.assertEqual((self.HOOK, S.PRIVATE, "", 0), (s.path, s.state, s.sha, s.size))
            self.assertIsNone(S.content_of(self.d, s))
        self.assertEqual(before, self.objects(), "no byte of a private file enters the store")

    def test_a_case_flipped_store_write_is_recorded_path_only(self):
        before = self.objects()
        self.write_through_the_hooks(".MOKATA/x", tool="Edit")
        snaps = [s for s in S.timeline(self.d, session="S1") if s.kind == S.AFTER]
        self.assertEqual(1, len(snaps))
        self.assertEqual(S.PRIVATE, snaps[0].state)
        self.assertTrue(RP.is_private_name(snaps[0].path))
        self.assertEqual(before, self.objects())

    def test_drained_hook_time_bytes_of_a_private_file_are_dropped_too(self):
        snap = S.capture(self.d, self.HOOK, S.AFTER, "S1",
                         observed=(S.OK, 30, 1.0, f"{MARK}-observed".encode()))
        self.assertEqual((S.PRIVATE, "", 0), (snap.state, snap.sha, snap.size))
        self.assertEqual(1.0, snap.t, "a drained record keeps the hook-time time")
        self.assertEqual([], self.objects())

    def test_a_git_hook_is_recognised_in_any_spelling_and_nothing_else_is(self):
        for rel in (".git/hooks/pre-commit", ".GIT/Hooks/pre-push", "pkg/.Git/HOOKS/x",
                    ".git./hooks /post-merge"):
            with self.subTest(rel=rel):
                self.assertTrue(S.git_hook(rel))
        for rel in (".git/config", ".git/hooksx/y", "hooks/.git", "src/hooks/x", ".github/hooks/x"):
            with self.subTest(rel=rel):
                self.assertFalse(S.git_hook(rel))
        with mock.patch.object(RP, "fold_component", side_effect=ValueError("cannot fold")):
            self.assertTrue(S.git_hook("src/a.py"), "undecidable is flagged, never hidden")

    def test_the_first_touch_of_a_private_path_says_its_content_was_not_captured(self):
        from mokata.ide import activity as A
        self.write_through_the_hooks(self.HOOK)
        firsts = [s for s in S.timeline(self.d, session="S1") if s.kind == S.BEFORE]
        self.assertEqual(1, len(firsts))
        item = A.item_from_snapshot(firsts[0])
        self.assertIn(S.PRIVATE_NOTE, item["title"])
        self.assertTrue(item["detail"]["git_hook"])

    def test_the_hook_adapter_emits_a_path_only_flagged_event(self):
        from mokata.ide import bus
        for rel, tool, hook in ((self.HOOK, "Write", True), (".MOKATA/x", "Edit", False),
                                (".git/config", "Edit", False)):
            with self.subTest(rel=rel):
                ev = bus.from_claude_hook({"hook_event_name": "PostToolUse", "session_id": "S1",
                                           "tool_name": tool, "cwd": self.d,
                                           "tool_input": {"file_path": os.path.join(self.d, rel),
                                                          "content": MARK}}, self.d)
                self.assertIsNotNone(ev)
                self.assertEqual((bus.EDIT, rel, True, hook),
                                 (ev["kind"], ev["path"], ev["private"], ev["git_hook"]))
                self.assertEqual(S.PRIVATE_NOTE, ev["note"])
                self.assertNotIn(MARK, repr(ev))
        ev = bus.from_neutral({"kind": bus.EDIT, "path": os.path.join(self.d, self.HOOK)}, self.d)
        self.assertEqual((self.HOOK, True, True), (ev["path"], ev["private"], ev["git_hook"]))
        ev = bus.from_claude_hook({"hook_event_name": "PostToolUse", "session_id": "S1",
                                   "tool_name": "Write", "cwd": self.d,
                                   "tool_input": {"file_path": os.path.join(self.d, "a.py")}},
                                  self.d)
        self.assertEqual(("a.py", None, None), (ev["path"], ev.get("private"), ev.get("git_hook")))

    def test_the_activity_panel_says_wrote_a_git_hook_and_private_content_not_captured(self):
        from mokata.ide import activity as A
        from mokata.ide import bus
        self.write_through_the_hooks(self.HOOK)
        self.write_through_the_hooks(".MOKATA/x", sid="S2")
        items = [A.item_from_snapshot(s) for s in S.timeline(self.d) if s.kind == S.AFTER]
        hook = [i for i in items if i["detail"]["path"] == self.HOOK][0]
        store = [i for i in items if i["detail"]["path"] != self.HOOK][0]
        self.assertIn("wrote a git hook", hook["title"])
        self.assertIn(S.PRIVATE_NOTE, hook["title"])
        self.assertNotIn("git hook", store["title"])
        self.assertIn(S.PRIVATE_NOTE, store["title"])
        for i in (hook, store):
            self.assertTrue(i["detail"]["private"])
        ev = bus.from_claude_hook({"hook_event_name": "PostToolUse", "session_id": "S1",
                                   "tool_name": "Write", "cwd": self.d,
                                   "tool_input": {"file_path": os.path.join(self.d, self.HOOK)}},
                                  self.d)
        live = A.item_from_bus({**ev, "id": "1", "ts": 1.0})
        self.assertIn("wrote a git hook", live["title"])
        self.assertTrue(live["detail"]["git_hook"])
        plain = A.item_from_bus({"kind": "edit", "path": "a.py", "id": "2", "ts": 1.0})
        self.assertEqual("wrote a.py", plain["title"])

    def test_the_session_views_list_the_write_and_serve_no_content_and_reads_stay_refused(self):
        from starlette.testclient import TestClient
        from mokata.ide.server import AppState, build_app
        self.write_through_the_hooks(self.HOOK)
        self.write_through_the_hooks(".MOKATA/x")
        c = TestClient(build_app(AppState(self.d, TOKEN, PORT, None, False)), base_url=BASE,
                       follow_redirects=False)
        c.get(f"/pair?t={TOKEN}")
        tl = c.get("/api/timeline", params={"session": "S1"})
        self.assertEqual(200, tl.status_code)
        self.assertIn(self.HOOK, tl.text)
        self.assertNotIn(MARK, tl.text)
        ids = [s.id for s in S.timeline(self.d, session="S1")]
        for a in ids:
            for b in ids:
                r = c.get("/api/between", params={"a": a, "b": b})
                self.assertNotIn(MARK, r.text)
        r = c.get("/api/session", params={"id": "S1"})
        self.assertEqual(200, r.status_code)
        self.assertNotIn(MARK, r.text)
        for path in (self.HOOK, ".MOKATA/x", ".git/hooks/PRE-COMMIT"):
            for route in ("/api/file", "/api/highlight", "/api/diff/context"):
                with self.subTest(path=path, route=route):
                    r = c.get(route, params={"path": path})
                    self.assertEqual(400, r.status_code)
                    self.assertNotIn(MARK, r.text)

if __name__ == "__main__":
    unittest.main()
