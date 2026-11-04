"""0.0.27 CI-FIX Lows C, WIN-SPILL-NOT-OWNER-ONLY: "owner-only" is true on Windows too.

The promise a user relies on: a file mokata calls owner-only (the explainer / usages spill, the IDE's
server and bridge records, the explainer store, a private `atomic_write_text`) is not readable by
another local account - and never was, not even for the instant before a permission is applied. POSIX
gets that from `0o600` at `open`; Windows ignored it, so a repo outside the profile (`D:\\`) left the
spill with `BUILTIN\\Users:(RX)` inherited from its folder. Setting a protected DACL AFTER the file
exists is still not enough (a handle opened in between keeps reading), so the file is CREATED with
its descriptor (`winacl.create_private_file`, `make_private_dir`). On Windows these tests read the
ACL back with `icacls`; elsewhere they read the mode bits. The race itself is
`test_winacl_private_race`.

NOT covered here by a DACL: the hook spool. It must not pay for `ctypes`, so it relies on the user
profile's ACL (this user, administrators and SYSTEM) and REFUSES a spool outside the profile on
Windows (`TheHookSpoolStaysInTheProfile`). An ordinary `atomic_write_text` (a `.gitignore`, a spec)
is deliberately NOT owner-only: it keeps the ACL its folder gave it.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from _winacl_assert import assert_owner_only  # noqa: E402
from mokata import winacl  # noqa: E402


def _tmp(case):
    d = tempfile.mkdtemp(prefix="mk-winacl-")
    case.addCleanup(shutil.rmtree, d, True)
    return d


class TheDescriptorIsProtectedAndSingle(unittest.TestCase):
    def test_a_file_gets_one_protected_entry_and_a_directory_one_that_its_children_inherit(self):
        self.assertEqual("D:P(A;;FA;;;S-1-5-21-1)", winacl.sddl_for("S-1-5-21-1", False))
        self.assertEqual("D:P(A;OICI;FA;;;S-1-5-21-1)", winacl.sddl_for("S-1-5-21-1", True))

    def test_off_windows_a_private_file_is_0600_and_exclusive_and_a_private_dir_0700(self):
        if os.name == "nt":
            self.skipTest("the mode bits are the non-Windows branch")
        d = _tmp(self)
        p = os.path.join(d, "f")
        os.close(winacl.create_private_file(p))
        self.assertEqual(0o600, stat.S_IMODE(os.stat(p).st_mode))
        with self.assertRaises(FileExistsError):
            winacl.create_private_file(p)
        sub = os.path.join(d, "sub")
        winacl.make_private_dir(sub)
        self.assertEqual(0o700, stat.S_IMODE(os.stat(sub).st_mode))
        with self.assertRaises(FileExistsError):
            winacl.make_private_dir(sub)

    def test_the_after_the_fact_acl_is_gone(self):
        """A permission applied to a file that already exists is the race: it must not exist."""
        self.assertFalse(hasattr(winacl, "owner_only"))


@unittest.skipUnless(os.name == "nt", "the ACL is a Windows thing; POSIX reads the mode bits")
class OnWindowsTheAclIsReallyOwnerOnly(unittest.TestCase):
    def test_a_file_is_born_readable_by_the_current_user_only(self):
        p = os.path.join(_tmp(self), "f.txt")
        with os.fdopen(winacl.create_private_file(p), "w", encoding="utf-8") as fh:
            fh.write("x")
        assert_owner_only(self, p)
        with open(p, encoding="utf-8") as fh:                      # and the owner can still read it
            self.assertEqual("x", fh.read())
        with self.assertRaises(FileExistsError):
            winacl.create_private_file(p)

    def test_a_directory_is_born_owner_only_and_what_is_made_in_it_inherits_that(self):
        d = os.path.join(_tmp(self), "sub")
        winacl.make_private_dir(d)
        assert_owner_only(self, d)
        child = os.path.join(d, "c.txt")
        with open(child, "w", encoding="utf-8") as fh:
            fh.write("y")
        from _winacl_assert import acl_entries
        entries = acl_entries(child)
        self.assertEqual(1, len(entries), entries)
        self.assertIn(os.environ["USERNAME"].lower(), entries[0].lower())
        with self.assertRaises(FileExistsError):
            winacl.make_private_dir(d)


class EveryOwnerOnlyWriterKeepsItsPromise(unittest.TestCase):
    def test_the_spill_file_and_its_directory(self):
        from mokata.ide import usages as U
        d = os.path.join(_tmp(self), "spill")
        w = U.SpillWriter(d, "usages")
        w.row([0, "a", 1, 1], ["a", 1, 1, "", "", 0, 0])
        w.close()
        if os.name == "nt":
            assert_owner_only(self, w.path)
        else:
            self.assertEqual(0o600, stat.S_IMODE(os.stat(w.path).st_mode))

    def test_the_ide_server_record_and_the_bridge_record(self):
        from mokata.ide import bus, server, statedir
        d = _tmp(self)
        home = _tmp(self)                    # the record of a non-governed dir lives under the HOME
        patch = mock.patch.object(statedir, "user_home", return_value=home)
        patch.start()
        self.addCleanup(patch.stop)
        server._write_record(d, {"pid": os.getpid()})
        rec = bus.ensure_bridge(d)
        self.assertIn("token", rec)
        for path in (server.record_path(d), bus.bridge_path(d)):
            self.assertTrue(os.path.realpath(path).startswith(os.path.realpath(home)), path)
            if os.name == "nt":
                assert_owner_only(self, path)
            else:
                self.assertEqual(0o600, stat.S_IMODE(os.stat(path).st_mode))

    def test_the_explainer_store_file(self):
        from mokata.explain import safedir
        d = _tmp(self)
        with safedir.open_chain(d, ["store", "sub"]) as sd:
            sd.write("f.json", b"{}")
        for path in (os.path.join(d, "store", "sub", "f.json"), os.path.join(d, "store", "sub")):
            if os.name == "nt":
                assert_owner_only(self, path)
            else:
                self.assertEqual(0o700 if os.path.isdir(path) else 0o600,
                                 stat.S_IMODE(os.stat(path).st_mode))


class _Spy:
    """Stands in for `winacl.create_private_file` / `make_private_dir`: records each path and whether
    it already EXISTED when the call was made (it must not: a file that exists was born with the
    folder's ACL), then does the real thing - or fails."""

    def __init__(self, real, fail=False):
        self.real, self.fail = real, fail
        self.paths, self.existed = [], []

    def __call__(self, path):
        self.paths.append(path)
        self.existed.append(os.path.exists(path))
        if self.fail:
            raise OSError(5, "access denied (spy)", path)
        return self.real(path)


def _spy(case, name="create_private_file", fail=False):
    spy = _Spy(getattr(winacl, name), fail)
    p = mock.patch.object(winacl, name, spy)
    p.start()
    case.addCleanup(p.stop)
    return spy


def _temps(spy):
    return [p for p in spy.paths if os.path.basename(p).startswith(".tmp-")]


class ThePrivateFileIsBornOwnerOnly(unittest.TestCase):
    """The window that matters: a file made under its folder's ACL and protected afterwards is
    readable, through a handle opened in between, by whoever the folder admits. So every private
    writer must create the file THROUGH `winacl.create_private_file`, never set a permission on a file
    that exists. Pinned on every OS by spying on the helper - the routing is the same code everywhere;
    the property itself is `test_winacl_private_race` (Windows)."""

    def _home(self):
        from mokata.ide import statedir
        home = _tmp(self)
        p = mock.patch.object(statedir, "user_home", return_value=home)
        p.start()
        self.addCleanup(p.stop)
        return home

    def test_the_server_record_is_created_private(self):
        from mokata.ide import server
        self._home()
        spy = _spy(self)
        server._write_record(_tmp(self), {"pid": 1, "token": "SECRET-TOKEN"})
        self.assertEqual(1, len(_temps(spy)), spy.paths)
        self.assertEqual([False], spy.existed, "the file already existed under its folder's ACL")

    def test_the_bridge_record_is_created_private(self):
        from mokata.ide import bus
        self._home()
        spy = _spy(self)
        bus.ensure_bridge(_tmp(self))
        self.assertEqual(1, len(_temps(spy)), spy.paths)
        self.assertEqual([False], spy.existed)

    def test_a_private_atomic_write_is_created_private(self):
        from mokata.atomicfile import atomic_write_text
        spy = _spy(self)
        atomic_write_text(os.path.join(_tmp(self), "s.json"), "SECRET", private=True)
        self.assertEqual(1, len(_temps(spy)), spy.paths)
        self.assertEqual([False], spy.existed)

    def test_an_ordinary_atomic_write_is_not_made_owner_only(self):
        """A `.gitignore`, a committed spec, a manifest: the folder's ACL is theirs to keep."""
        from mokata.atomicfile import atomic_write_text
        spy = _spy(self)
        atomic_write_text(os.path.join(_tmp(self), ".gitignore"), "x\n")
        self.assertEqual([], spy.paths)

    def test_every_secret_bearing_writer_asks_for_private_and_no_other_does(self):
        """The audit, pinned: the callers that pass `private=True` are exactly the ones whose
        content is a secret (a session bundle, the IDE trust record, the server and bridge records)."""
        import re
        # CORPUS: THE WORKING TREE - `src/mokata` as it is on disk, anchored at this file (not at the
        # process CWD): what a private writer is, is a fact about the code in this checkout.
        src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "mokata")
        found = set()
        for root, _d, files in os.walk(src):
            for f in files:
                if f.endswith(".py"):
                    with open(os.path.join(root, f), encoding="utf-8") as fh:
                        text = fh.read()
                    if re.search(r"atomic_write_text\([^\n]*private=True", text):
                        found.add(f)
        self.assertEqual({"session_transport.py", "trust.py", "server.py", "bus.py"}, found)

    def test_a_private_file_that_cannot_be_made_private_is_not_made_at_all(self):
        from mokata.ide import bus, server
        from mokata.atomicfile import atomic_write_text
        self._home()
        _spy(self, fail=True)
        d = _tmp(self)
        plain = os.path.join(_tmp(self), "p")
        os.makedirs(plain)
        with self.assertRaises(OSError):
            atomic_write_text(os.path.join(plain, "s.json"), "SECRET", private=True)
        self.assertEqual([], os.listdir(plain), "a half-protected temp file was left behind")
        with self.assertRaises(OSError):
            server._write_record(d, {"pid": 1, "token": "SECRET"})
        with self.assertRaises(OSError):
            bus.ensure_bridge(d)
        for path in (server.record_path(d), bus.bridge_path(d)):
            self.assertEqual([], os.listdir(os.path.dirname(path)), os.listdir(os.path.dirname(path)))

    def test_a_spill_that_cannot_be_made_private_fails_the_job_and_leaves_no_file(self):
        from mokata.ide import usages as U
        _spy(self, fail=True)
        d = os.path.join(_tmp(self), "spill")
        with self.assertRaises(U.SpillFull):
            U.SpillWriter(d, "usages")
        self.assertEqual([], os.listdir(d))

    def test_the_spill_file_is_created_private_and_the_folder_only_when_we_make_it(self):
        """Every spill file is born private; the folder is made private once, when WE make it (a
        directory's entry is inherited by the files in it), and a folder that was already there is
        left alone."""
        from mokata.ide import usages as U
        files = _spy(self)
        dirs = _spy(self, "make_private_dir")
        d = os.path.join(_tmp(self), "spill")
        for _ in range(3):
            U.SpillWriter(d, "usages").discard()
        self.assertEqual([d], dirs.paths)
        self.assertEqual(3, len(files.paths), "each spill file, born private")
        self.assertEqual([False] * 3, files.existed)
        dirs.paths.clear()
        U.SpillWriter(_tmp(self), "usages").discard()
        self.assertEqual([], dirs.paths)

    def test_the_explainer_store_creates_its_files_and_folders_private(self):
        from mokata.explain import safedir
        if os.name != "nt":
            self.skipTest("on POSIX the store is built with 0600/0700 through dir_fd; see the mode test")
        files, dirs = _spy(self), _spy(self, "make_private_dir")
        d = _tmp(self)
        with safedir.open_chain(d, ["store", "sub"]) as sd:
            sd.write("f.json", b"{}")
        self.assertEqual(2, len(dirs.paths), dirs.paths)
        self.assertEqual(1, len(files.paths), files.paths)
        self.assertEqual([False] * 3, files.existed + dirs.existed)


@unittest.skipUnless(os.name == "nt", "ACLs are read back with icacls")
class OnWindowsOnlyASecretIsOwnerOnly(unittest.TestCase):
    def test_a_private_write_is_owner_only_and_an_ordinary_write_keeps_its_folders_acl(self):
        from mokata.atomicfile import atomic_write_text
        from _winacl_assert import acl_entries
        d = _tmp(self)
        secret, plain = os.path.join(d, "secret.json"), os.path.join(d, ".gitignore")
        atomic_write_text(secret, "S", private=True)
        atomic_write_text(plain, "x\n")
        assert_owner_only(self, secret)
        control = os.path.join(d, "control.txt")        # what the folder gives a plain new file
        with open(control, "w", encoding="utf-8") as fh:
            fh.write("x")
        self.assertEqual(acl_entries(control), acl_entries(plain))
        self.assertNotEqual(acl_entries(control), acl_entries(secret))


class TheHookSpoolStaysInTheProfile(unittest.TestCase):
    def test_a_path_is_in_the_profile_only_when_it_is_under_it(self):
        import mokata_emit as E
        home = _tmp(self)
        self.assertTrue(E.under_profile(os.path.join(home, ".mokata", "hook-spool"), home))
        self.assertTrue(E.under_profile(home, home))
        self.assertFalse(E.under_profile(_tmp(self), home))
        self.assertFalse(E.under_profile(home + "-sibling", home))

    def test_no_word_of_the_spools_documentation_calls_it_owner_only(self):
        """N-L3: on Windows the spool inherits the profile's ACL - this user, the administrators and
        SYSTEM - so "owner-only" is not true of it, in the module that writes it or in the doc that
        tells a user what mokata keeps in their home."""
        import mokata_emit as E
        here = os.path.dirname(os.path.abspath(__file__))
        # CORPUS: THE WORKING TREE - the one public doc that lists what is kept in the home folder.
        with open(os.path.join(here, "..", "docs", "security.md"), encoding="utf-8") as fh:
            doc = " ".join(fh.read().split())
        module = " ".join((E.__doc__ or "").split())
        self.assertNotIn("hook spool (owner-only)", doc)
        self.assertNotIn("owner-only file in the per-user spool", module)
        self.assertIn("hook spool (readable by you, administrators and SYSTEM only", doc)

    def test_with_no_profile_a_relative_spool_is_not_in_it(self):
        """N-L2: `%USERPROFILE%` unset leaves `~` unexpanded, so the default spool is the RELATIVE
        `~\\.mokata\\hook-spool` - a folder of the working directory (the repo). It is not the
        profile, and a relative `home` is never one either."""
        import mokata_emit as E
        cwd = _tmp(self)
        relative = "~/.mokata/hook-spool"
        with mock.patch("os.getcwd", return_value=cwd):
            self.assertFalse(E.under_profile(relative, "~"))
            self.assertFalse(E.under_profile(os.path.join(cwd, "~", ".mokata", "x"), "~"))
        self.assertFalse(E.under_profile(os.path.join(cwd, "x"), "rel-home"))
        self.assertTrue(E.under_profile(os.path.join(cwd, "x"), cwd))

    def test_on_windows_no_profile_means_the_spool_is_refused_and_says_why(self):
        import mokata_emit as E
        why = E.spool_refusal("~/.mokata/hook-spool", "~", windows=True)
        self.assertIn("USERPROFILE", why)
        self.assertIn("inline", why)

    def test_the_refusal_names_the_slowdown_and_the_remedy_and_is_windows_only(self):
        import mokata_emit as E
        profile, outside = _tmp(self), _tmp(self)
        why = E.spool_refusal(outside, profile, windows=True)
        for word in ("MOKATA_HOOK_SPOOL", outside, "inline", "Unset", "inside your profile"):
            self.assertIn(word, why)
        self.assertEqual("", E.spool_refusal(os.path.join(profile, "s"), profile, windows=True))
        self.assertEqual("", E.spool_refusal(outside, profile, windows=False),
                         "POSIX makes the spool 0700; there is nothing to refuse")

    def test_doctor_says_so_when_hooks_run_inline_because_of_the_spool(self):
        """N-L4: the refusal is correct for privacy and a silent slowdown otherwise."""
        import contextlib
        import io
        import argparse
        import mokata_emit as E
        from mokata.cli_commands.diagnostics import cmd_doctor
        from mokata.init import init_repo
        repo, profile, outside = _tmp(self), _tmp(self), _tmp(self)
        init_repo(root=repo, profile="standard", assume_yes=True, out=lambda _m: None)
        ns = argparse.Namespace(path=repo, matrix=False)

        def run(env):
            out = io.StringIO()
            with mock.patch.object(E, "_windows", return_value=True), \
                    mock.patch.dict(os.environ, env), contextlib.redirect_stdout(out):
                cmd_doctor(ns)
            return out.getvalue()
        got = run({"USERPROFILE": profile, "HOME": profile, "MOKATA_HOOK_SPOOL": outside})
        self.assertIn("hooks: MOKATA_HOOK_SPOOL", got)
        self.assertIn("run inline", got)
        self.assertNotIn("hooks: MOKATA_HOOK_SPOOL",
                         run({"USERPROFILE": profile, "HOME": profile,
                              "MOKATA_HOOK_SPOOL": os.path.join(profile, "spool")}))

    @unittest.skipUnless(os.name == "nt", "the refusal is the Windows branch")
    def test_a_spool_outside_the_profile_is_refused_so_the_hook_runs_inline(self):
        import mokata_emit as E
        profile, spool = _tmp(self), _tmp(self)
        with mock.patch.dict(os.environ, {"USERPROFILE": profile, "HOME": profile}):
            with self.assertRaises(OSError):
                E.spool_entry(spool, ["stop"], b"{}")
            self.assertEqual([], os.listdir(spool))
            inside = os.path.join(profile, "spool")
            path = E.spool_entry(inside, ["stop"], b"{}")
            self.assertTrue(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
