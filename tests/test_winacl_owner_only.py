"""0.0.27 CI-FIX Lows C, WIN-SPILL-NOT-OWNER-ONLY: "owner-only" is true on Windows too.

The promise a user relies on: a file mokata calls owner-only (the explainer / usages spill, the IDE's
server and bridge records, the explainer store, a private `atomic_write_text`) is not readable by
another local account, and is protected BEFORE its first byte is written. POSIX gets that from
`0o600`; Windows ignored it, so a repo outside the profile (`D:\\`) left the spill with
`BUILTIN\\Users:(RX)` inherited from its folder. The fix sets a protected DACL. On Windows these
tests read the ACL back with `icacls`; elsewhere they read the mode bits.

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

    def test_off_windows_it_is_a_no_op(self):
        if os.name == "nt":
            self.skipTest("the no-op is the non-Windows branch")
        d = _tmp(self)
        p = os.path.join(d, "f")
        open(p, "w", encoding="utf-8").close()
        os.chmod(p, 0o644)
        winacl.owner_only(p)
        self.assertEqual(0o644, stat.S_IMODE(os.stat(p).st_mode))


@unittest.skipUnless(os.name == "nt", "the ACL is a Windows thing; POSIX reads the mode bits")
class OnWindowsTheAclIsReallyOwnerOnly(unittest.TestCase):
    def test_a_file_is_readable_by_the_current_user_only(self):
        p = os.path.join(_tmp(self), "f.txt")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("x")
        winacl.owner_only(p)
        assert_owner_only(self, p)
        with open(p, encoding="utf-8") as fh:                      # and the owner can still read it
            self.assertEqual("x", fh.read())

    def test_a_directory_is_owner_only_and_what_is_made_in_it_inherits_that(self):
        d = os.path.join(_tmp(self), "sub")
        os.mkdir(d)
        winacl.owner_only(d)
        child = os.path.join(d, "c.txt")
        with open(child, "w", encoding="utf-8") as fh:
            fh.write("y")
        from _winacl_assert import acl_entries
        entries = acl_entries(child)
        self.assertEqual(1, len(entries), entries)
        self.assertIn(os.environ["USERNAME"].lower(), entries[0].lower())


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
    """Stands in for `winacl.owner_only` and records what the file looked like AT THAT MOMENT."""

    def __init__(self, fail=False):
        self.sizes, self.paths, self.fail = [], [], fail

    def __call__(self, path):
        self.paths.append(path)
        self.sizes.append(os.path.getsize(path) if os.path.exists(path) else None)
        if self.fail:
            raise OSError(5, "access denied (spy)", path)


def _spy(case, fail=False):
    spy = _Spy(fail)
    p = mock.patch.object(winacl, "owner_only", spy)
    p.start()
    case.addCleanup(p.stop)
    return spy


class TheAclComesBeforeTheSecret(unittest.TestCase):
    """The window that matters: between `open` and the ACL the file carries its folder's grants, so
    a secret written first is readable by whoever the folder admits. Pinned on every OS by spying
    on the helper (a no-op off Windows) - the ORDER is the same code everywhere."""

    def _home(self):
        from mokata.ide import statedir
        home = _tmp(self)
        p = mock.patch.object(statedir, "user_home", return_value=home)
        p.start()
        self.addCleanup(p.stop)
        return home

    def test_the_server_record_is_protected_while_still_empty(self):
        from mokata.ide import server
        self._home()
        spy = _spy(self)
        server._write_record(_tmp(self), {"pid": 1, "token": "SECRET-TOKEN"})
        self.assertEqual([0], spy.sizes, "the ACL was set after the token was already on disk")

    def test_the_bridge_record_is_protected_while_still_empty(self):
        from mokata.ide import bus
        self._home()
        spy = _spy(self)
        bus.ensure_bridge(_tmp(self))
        self.assertEqual([0], spy.sizes, "the ACL was set after the token was already on disk")

    def test_a_private_atomic_write_is_protected_while_still_empty(self):
        from mokata.atomicfile import atomic_write_text
        spy = _spy(self)
        atomic_write_text(os.path.join(_tmp(self), "s.json"), "SECRET", private=True)
        self.assertEqual([0], spy.sizes)

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

    def test_a_failed_acl_leaves_no_secret_behind(self):
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

    def test_a_failed_acl_on_a_spill_fails_the_job_and_removes_the_file(self):
        from mokata.ide import usages as U
        _spy(self, fail=True)
        d = os.path.join(_tmp(self), "spill")
        with self.assertRaises(U.SpillFull):
            U.SpillWriter(d, "usages")
        self.assertEqual([], os.listdir(d))

    def test_the_spill_folder_is_protected_once_not_once_per_spill(self):
        """Re-applying a directory ACL walks every child (measured on Windows: ~120 ms with 3000
        spills in it), so it is set when the folder is made, and a folder that was already there
        is left alone."""
        from mokata.ide import usages as U
        spy = _spy(self)
        d = os.path.join(_tmp(self), "spill")
        for _ in range(3):
            U.SpillWriter(d, "usages").discard()
        self.assertEqual([d], [p for p in spy.paths if p == d])
        self.assertEqual(3, len([p for p in spy.paths if p != d]), "each spill file, once")
        spy.paths.clear()
        existing = _tmp(self)
        U.SpillWriter(existing, "usages").discard()
        self.assertNotIn(existing, spy.paths)


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
        entries = acl_entries(plain)
        self.assertTrue(any("(I)" in e for e in entries), "an ordinary file inherits: %s" % entries)
        self.assertGreater(len(entries), 1, entries)


class TheHookSpoolStaysInTheProfile(unittest.TestCase):
    def test_a_path_is_in_the_profile_only_when_it_is_under_it(self):
        import mokata_emit as E
        home = _tmp(self)
        self.assertTrue(E.under_profile(os.path.join(home, ".mokata", "hook-spool"), home))
        self.assertTrue(E.under_profile(home, home))
        self.assertFalse(E.under_profile(_tmp(self), home))
        self.assertFalse(E.under_profile(home + "-sibling", home))

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
