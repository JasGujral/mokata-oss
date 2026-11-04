"""0.0.27 CI-FIX Lows C, WIN-SPILL-NOT-OWNER-ONLY: "owner-only" is true on Windows too.

The promise a user relies on: a file mokata calls owner-only (the explainer / usages spill, the IDE's
server and bridge records, the hook spool, the explainer store) is not readable by another local
account. POSIX gets that from `0o600`; Windows ignored it, so a repo outside the profile (`D:\\`)
left the spill with `BUILTIN\\Users:(RX)` inherited from its folder. The fix sets a protected DACL.
On Windows these tests read the ACL back with `icacls`; elsewhere they read the mode bits.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
import tempfile
import unittest

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
        from mokata.ide import bus, server
        d = _tmp(self)
        server._write_record(d, {"pid": os.getpid()})
        rec = bus.ensure_bridge(d)
        self.assertIn("token", rec)
        for path in (server.record_path(d), bus.bridge_path(d)):
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


if __name__ == "__main__":
    unittest.main()
