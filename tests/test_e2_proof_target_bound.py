"""0.0.28 E2 — `PROOF-SYMLINK-REPOINT`: a proof is bound to the FILE it ran against (note E §3e/§3f).

The business rule, as a reviewer reads the Proof screen: "proven" means the file the RED ran
against, the file the GREEN ran against and the file whose bytes the stamp covers NOW are one
repo-relative real file. A link re-pointed at an identical file, a file swapped for a link, a link
that leaves the repo (even unchanged and readable), a dangling or looping link, a record with no
target, a red and a green in different files — none of these is ever `proven`, and the screen names
the targets. And the other side of the same rule: an unchanged link stays proven, a red→green re-run
cures a re-point, and a case spelling never makes a stale a re-run cannot cure.

Windows: directory cells use a junction where a directory symlink needs privilege; a file-symlink
cell without the privilege is SKIPPED (recorded not-run), never green.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import unittest

import _support  # noqa: F401  (puts src/ on the path)

import test_p13_ide_vis_proof as P13
from mokata import test_ids as TI
from mokata import test_runs as TR
from mokata.ide import nav
from mokata.ide import proof as P
from mokata.state import StateStore
from mokata.tdd_state import state_dir

BODY = "def test_a():\n    assert 1\n"


def _file_link(target: str, link: str) -> None:
    os.symlink(target, link)


def _dir_link(target: str, link: str) -> None:
    """A directory link: a symlink, or on Windows without the privilege, a junction."""
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        import _winapi
        base = os.path.dirname(link)
        _winapi.CreateJunction(os.path.normpath(os.path.join(base, target)), link)


def _unlink(p: str) -> None:
    if os.path.isdir(p) and not os.path.islink(p) and os.name == "nt":
        os.rmdir(p)                          # a junction
    else:
        os.remove(p)


class _Bound(unittest.TestCase):
    """r1 with an emitted spec (P13's fixture); each case records under AC-3."""

    def setUp(self):
        if not hasattr(os, "symlink"):
            self.skipTest("no symlinks on this platform")
        self._t_ = tempfile.TemporaryDirectory()
        self.d = os.path.realpath(self._t_.name)
        P13.fixture(self.d)

    def tearDown(self):
        nav.close_layers()
        self._t_.cleanup()

    def w(self, rel, text=BODY):
        P13._write(self.d, rel, text)

    def p(self, rel):
        return os.path.join(self.d, *rel.split("/"))

    def link(self, target, rel, directory=False):
        if os.name == "nt" and not os.path.isabs(target):
            target = target.replace("/", "\\")     # a Windows link's text uses its own separator
        try:
            (_dir_link if directory else _file_link)(target, self.p(rel))
        except OSError as exc:
            self.skipTest(f"NOT RUN: symlinks need privileges here ({exc})")

    def relink(self, target, rel, directory=False):
        _unlink(self.p(rel))
        self.link(target, rel, directory)

    def commit(self, msg="c"):
        P13._git(self.d, "add", "-A")
        P13._git(self.d, "commit", "-qm", msg)

    def rec(self, tid, passed, at, ac="AC-3"):
        TR.record_test_run(self.d, "r1", passed=passed, command="pytest", tests=[tid],
                           ac_map={ac: [tid]} if ac else None, at=at)

    def red_green(self, tid, at=3000.0):
        self.rec(tid, False, at)
        self.rec(tid, True, at + 100)

    def ac(self, tr=None):
        tr = tr or P.trace(self.d, run="r1")
        return P13._ac(tr, "AC-3"), tr

    def why(self, tr):
        row = P13._ac(tr, "AC-3")
        parts = []
        for nid in row["tests"]:
            t = P13._t(tr, nid)
            parts += [t.get("reason") or "", t.get("tdd_note") or ""]
        return " | ".join(parts)

    def history(self):
        return TR.read_test_record(self.d, "r1")["tests"]

    def assertNotProven(self, tr, *needles):
        row = P13._ac(tr, "AC-3")
        self.assertNotEqual("proven", row["status"], self.why(tr))
        for nid in row["tests"]:
            self.assertNotEqual("proven", P13._t(tr, nid)["tdd"], self.why(tr))
        for n in needles:
            self.assertIn(n, self.why(tr))


class RepointFile(_Bound):
    def test_E2_RepointFile_a_link_repointed_at_an_identical_file_never_proves_and_names_both(self):
        self.w("tests/test_a.py")
        self.w("tests/test_b.py")
        self.link("test_a.py", "tests/alias.py")
        self.commit()
        self.red_green("tests/alias.py::test_a")
        self.assertEqual("proven", self.ac()[0]["status"], "the control: an unchanged link proves")
        self.relink("test_b.py", "tests/alias.py")
        _row, tr = self.ac()
        self.assertNotProven(tr, "tests/alias.py was tests/test_a.py, now tests/test_b.py")

    def test_E2_the_record_states_its_targets_in_clear_and_never_an_absolute_path(self):
        self.w("tests/test_a.py")
        self.link("test_a.py", "tests/alias.py")
        self.commit()
        self.red_green("tests/alias.py::test_a")
        h = self.history()["tests/alias.py::test_a"]
        self.assertEqual(("tests/test_a.py",) * 3, (h["red_target"], h["green_target"], h["target"]))
        raw = json.dumps(StateStore(state_dir(self.d)).read(TR.key("r1")))
        for absolute in (self.d, os.path.realpath(self.d), os.path.expanduser("~"),
                         tempfile.gettempdir()):
            self.assertNotIn(absolute.replace("\\", "\\\\"), raw)


class RepointDir(_Bound):
    def test_E2_RepointDir_a_repointed_directory_component_never_proves(self):
        self.w("d1/test_w.py")
        self.w("d2/test_w.py")
        self.link("d1", "tlink", directory=True)
        self.commit()
        self.red_green("tlink/test_w.py::test_a")
        self.assertEqual("proven", self.ac()[0]["status"], "the control")
        self.relink("d2", "tlink", directory=True)
        _row, tr = self.ac()
        self.assertNotProven(tr, "tlink/test_w.py was d1/test_w.py, now d2/test_w.py")


class RepointJunction(_Bound):
    """Windows: a directory JUNCTION (no privilege needed), re-pointed — the junction control for the
    directory cells, whatever the symlink privilege. Elsewhere: NOT RUN (no junctions)."""

    def junction(self, target, rel):
        import _winapi
        _winapi.CreateJunction(self.p(target), self.p(rel))

    def test_E2_RepointJunction_a_repointed_junction_never_proves(self):
        if os.name != "nt":
            self.skipTest("NOT RUN: junctions are a Windows directory link")
        self.w("d1/test_w.py")
        self.w("d2/test_w.py")
        self.junction("d1", "jlink")
        self.commit()
        self.red_green("jlink/test_w.py::test_a")
        self.assertEqual("d1/test_w.py", self.history()["jlink/test_w.py::test_a"]["target"])
        self.assertEqual("proven", self.ac()[0]["status"], "the control: an unchanged junction proves")
        os.rmdir(self.p("jlink"))
        self.junction("d2", "jlink")
        _row, tr = self.ac()
        self.assertNotProven(tr, "jlink/test_w.py was d1/test_w.py, now d2/test_w.py")


class RepointBetweenRedAndGreen(_Bound):
    def test_E2_red_via_a_then_repoint_then_green_via_the_alias_is_not_proven(self):
        self.w("tests/test_a.py")
        self.w("tests/test_b.py")
        self.link("test_a.py", "tests/alias.py")
        self.commit()
        self.rec("tests/alias.py::test_a", False, 3000.0)
        self.relink("test_b.py", "tests/alias.py")
        self.rec("tests/alias.py::test_a", True, 3100.0)
        _row, tr = self.ac()
        self.assertNotProven(tr, "red and green ran in different files", "tests/test_a.py",
                             "tests/test_b.py")


class Swap(_Bound):
    def test_E2_Swap_a_regular_file_swapped_for_a_link_to_an_identical_file_never_proves(self):
        self.w("tests/test_s.py")
        self.w("tests/other.py")
        self.commit()
        self.red_green("tests/test_s.py::test_a")
        self.assertEqual("proven", self.ac()[0]["status"], "the control")
        os.remove(self.p("tests/test_s.py"))
        self.link("other.py", "tests/test_s.py")
        _row, tr = self.ac()
        self.assertNotProven(tr, "tests/test_s.py was tests/test_s.py, now tests/other.py")

    def test_E2_Swap_a_link_swapped_back_for_a_regular_copy_never_proves(self):
        self.w("tests/test_a.py")
        self.link("test_a.py", "tests/alias.py")
        self.commit()
        self.red_green("tests/alias.py::test_a")
        os.remove(self.p("tests/alias.py"))
        self.w("tests/alias.py")
        _row, tr = self.ac()
        self.assertNotProven(tr, "tests/alias.py was tests/test_a.py, now tests/alias.py")


class Escapes(_Bound):
    def test_E2_an_unchanged_readable_link_out_of_the_repo_never_proves(self):
        outside = self.d + "-outside.py"
        with open(outside, "w", encoding="utf-8") as fh:
            fh.write(BODY)
        self.addCleanup(os.remove, outside)
        self.link(outside, "tests/ext.py")
        self.commit()
        self.red_green("tests/ext.py::test_a")
        h = self.history()["tests/ext.py::test_a"]
        self.assertEqual("outside", h["target"])
        _row, tr = self.ac()
        self.assertNotProven(tr, "outside the repository")

    def test_E2_a_dangling_link_never_proves(self):
        self.link("nowhere.py", "tests/dang.py")
        self.commit()
        self.red_green("tests/dang.py::test_a")
        self.assertEqual("unresolved", self.history()["tests/dang.py::test_a"]["target"])
        _row, tr = self.ac()
        self.assertNotProven(tr, "cannot be resolved")

    def test_E2_a_looping_link_never_proves(self):
        self.link("loop2.py", "tests/loop1.py")
        self.link("loop1.py", "tests/loop2.py")
        self.commit()
        self.red_green("tests/loop1.py::test_a")
        self.assertEqual("unresolved", self.history()["tests/loop1.py::test_a"]["target"])
        _row, tr = self.ac()
        self.assertNotProven(tr, "cannot be resolved")

    def test_E2_a_dotdot_through_a_link_component_binds_nothing(self):
        """POSIX: `tests/lnk/../test_x.py` is, to the OS, `other/../test_x.py` = `test_x.py` at the
        root — not the lexical `tests/test_x.py`; the record must not bind the lexical file.
        Windows folds `..` BEFORE it resolves a link (Win32 path rules), so there the OS opens the
        lexical `tests/test_x.py`, the runner ran it, and that IS the target (measured on the mirror,
        `probe/matrix/E2` windows-probe run 42976544147)."""
        self.w("test_x.py")
        self.w("tests/test_x.py")
        self.w("other/keep.py", "x = 1\n")
        self.link("../other", "tests/lnk", directory=True)
        self.commit()
        self.red_green("tests/lnk/../test_x.py::test_a")
        tid = next(k for k in self.history() if k.endswith("test_x.py::test_a"))
        if os.name == "nt":
            self.assertEqual("tests/test_x.py", self.history()[tid]["target"])
            return
        self.assertEqual("unresolved", self.history()[tid]["target"])
        _row, tr = self.ac()
        self.assertNotProven(tr, "cannot be resolved")

    def test_E2_a_file_that_vanishes_while_it_is_resolved_is_unresolved(self):
        """A4: the real path is resolved, then the file is gone before its spelling is read (a
        race): the vanished tail is never taken as the target."""
        from unittest import mock
        self.w("tests/test_a.py")
        real = os.path.realpath
        with mock.patch("os.path.realpath",
                        side_effect=lambda p, *a, **k: real(p).replace("test_a.py", "gone_a.py")):
            self.assertEqual("unresolved", TI.stamp_target(self.d, "tests/test_a.py"))


class Chain(_Bound):
    def test_E2_Chain_one_hop_repointed_never_proves(self):
        self.w("tests/test_a.py")
        self.w("tests/test_b.py")
        self.link("test_a.py", "tests/hop.py")
        self.link("hop.py", "tests/alias.py")
        self.commit()
        self.red_green("tests/alias.py::test_a")
        self.assertEqual("proven", self.ac()[0]["status"], "the control")
        self.relink("test_b.py", "tests/hop.py")
        _row, tr = self.ac()
        self.assertNotProven(tr, "tests/alias.py was tests/test_a.py, now tests/test_b.py")


class NoFalseStale(_Bound):
    def test_E2_an_unchanged_link_stays_proven(self):
        self.w("tests/test_a.py")
        self.link("test_a.py", "tests/alias.py")
        self.commit()
        self.red_green("tests/alias.py::test_a")
        self.assertEqual("proven", self.ac()[0]["status"])
        self.assertEqual("proven", self.ac()[0]["status"], "a second trace agrees")

    def test_E2_a_red_then_green_rerun_cures_a_repoint(self):
        self.w("tests/test_a.py")
        self.w("tests/test_b.py")
        self.link("test_a.py", "tests/alias.py")
        self.commit()
        self.red_green("tests/alias.py::test_a")
        self.relink("test_b.py", "tests/alias.py")
        self.rec("tests/alias.py::test_a", True, 4000.0)
        self.assertNotEqual("proven", self.ac()[0]["status"], "a green alone is not a cure")
        self.red_green("tests/alias.py::test_a", at=5000.0)
        self.assertEqual("proven", self.ac()[0]["status"], self.why(self.ac()[1]))

    def test_E2_a_stale_alias_does_not_poison_the_file_it_now_names(self):
        """The alias was proven on test_a, then re-pointed at test_b; test_b's own canonical id
        went red→green. test_b is proven — the alias's record is about another file, said."""
        self.w("tests/test_a.py")
        self.w("tests/test_b.py")
        self.link("test_a.py", "tests/alias.py")
        self.commit()
        self.red_green("tests/alias.py::test_a")
        self.relink("test_b.py", "tests/alias.py")
        self.red_green("tests/test_b.py::test_a", at=5000.0)
        row, tr = self.ac()
        self.assertEqual("proven", row["status"], self.why(tr))
        self.assertIn("tests/alias.py was tests/test_a.py, now tests/test_b.py", self.why(tr))


class CaseSpelling(_Bound):
    def setUp(self):
        super().setUp()
        if not TI.fs_folds_case(self.d):
            self.skipTest("NOT RUN: this filesystem does not fold case")

    def test_E2_a_differently_cased_rerun_cures_a_repoint(self):
        self.w("tests/test_a.py")
        self.w("tests/test_b.py")
        self.link("test_a.py", "tests/alias.py")
        self.commit()
        self.red_green("Tests/Alias.py::test_a")
        self.relink("test_b.py", "tests/alias.py")
        self.assertNotEqual("proven", self.ac()[0]["status"])
        self.red_green("tests/alias.py::test_a", at=5000.0)
        self.assertEqual("proven", self.ac()[0]["status"], self.why(self.ac()[1]))

    def test_E2_case_spellings_of_one_file_are_one_target(self):
        self.w("tests/test_a.py")
        self.commit()
        self.red_green("TESTS/TEST_A.PY::test_a")
        got = self.history()["TESTS/TEST_A.PY::test_a"]["target"]
        self.assertTrue(TI.same_target(self.d, got, "tests/test_a.py"), got)
        self.assertEqual("proven", self.ac()[0]["status"], self.why(self.ac()[1]))

    def test_E2_a_case_only_rename_on_disk_is_not_a_repoint(self):
        self.w("tests/Test_C.py")
        self.commit()
        self.red_green("tests/Test_C.py::test_a")
        os.rename(self.p("tests/Test_C.py"), self.p("tests/tmp_c.py"))
        os.rename(self.p("tests/tmp_c.py"), self.p("tests/test_c.py"))
        from mokata import fs_identity
        fs_identity.forget()
        self.assertTrue(TI.same_target(self.d, "tests/Test_C.py", TI.stamp_target(self.d, "tests/Test_C.py")))
        row, tr = self.ac()
        self.assertNotIn("now tests/test_c.py", self.why(tr), "a case-only rename is one file")


class OldRecordLoud(_Bound):
    def test_E2_a_record_without_target_is_unstamped_and_loud(self):
        self.w("tests/test_a.py")
        self.commit()
        self.red_green("tests/test_a.py::test_a")

        def strip(cur):
            for h in cur["tests"].values():
                for k in ("target", "red_target", "green_target"):
                    h.pop(k, None)
            return cur
        StateStore(state_dir(self.d)).update(TR.key("r1"), strip, default=None)
        _row, tr = self.ac()
        self.assertNotProven(tr, "re-run it")
        self.assertIn("target", self.why(tr))

    def test_E2_a_bare_id_still_never_proves(self):
        self.w("tests/test_a.py")
        self.commit()
        self.red_green("test_a")
        self.assertNotEqual("proven", self.ac()[0]["status"])

    def test_E2_an_outcome_with_no_known_target_drops_the_old_one(self):
        """A8: an outcome recorded where the target cannot be told never inherits the previous
        outcome's target (it reads unstamped instead)."""
        h = TR._advance({}, ["t.py::test_a"], False, 1.0, {"t.py::test_a": "s"},
                        {"t.py::test_a": "t.py"})
        h = TR._advance(h, ["t.py::test_a"], True, 2.0, {"t.py::test_a": "s"}, {})
        self.assertNotIn("target", h["t.py::test_a"])
        self.assertNotIn("green_target", h["t.py::test_a"])


class AtHead(_Bound):
    """A commit range judged AT `head` (the working tree is not `head`): a link in the commit is
    never followed, on disk or in the tree — `unlocated`, fail closed (refute M6)."""

    def head_trace(self):
        tr = P.trace(self.d, run="r1", base="HEAD~1", head="HEAD")
        self.assertIn("judged at HEAD", tr["graph"]["note"], "the cell must judge at head")
        return tr

    def test_E2_AtHead_a_committed_link_is_unlocated_by_label_and_verdict(self):
        self.w("tests/test_a.py")
        self.w("tests/test_b.py")
        self.link("test_a.py", "tests/alias.py")
        self.commit("c2")
        self.red_green("tests/alias.py::test_a")
        self.relink("test_b.py", "tests/alias.py")
        self.commit("c3")
        self.relink("test_a.py", "tests/alias.py")      # the DISK agrees with the record; head does not
        tr = self.head_trace()
        node = P13._t(tr, "test:tests/alias.py::test_a")
        self.assertEqual("tests/alias.py::test_a", node["label"], "the link is never followed at head")
        self.assertEqual("unlocated", node["state"])
        self.assertIn("symlink", node["reason"])
        self.assertNotEqual("proven", P13._ac(tr, "AC-3")["status"])

    def test_E2_AtHead_a_committed_directory_link_is_unlocated(self):
        self.w("d1/test_w.py")
        self.w("d2/test_w.py")
        self.link("d1", "tlink", directory=True)
        self.commit("c2")
        self.red_green("tlink/test_w.py::test_a")
        self.relink("d2", "tlink", directory=True)
        self.commit("c3")
        self.relink("d1", "tlink", directory=True)
        tr = self.head_trace()
        node = P13._t(tr, "test:tlink/test_w.py::test_a")
        self.assertEqual("unlocated", node["state"])
        self.assertIn("symlink", node["reason"])
        self.assertNotEqual("proven", P13._ac(tr, "AC-3")["status"])

    def test_E2_AtHead_a_committed_link_elsewhere_is_not_a_second_candidate(self):
        """A7: `docs/tests/test_a.py -> ../../tests/test_a.py` is committed; at head it is not
        followed, so it is not a second file answering to `tests/test_a.py` (never `ambiguous`)."""
        self.w("tests/test_a.py")
        os.makedirs(self.p("docs/tests"))
        self.link("../../tests/test_a.py", "docs/tests/test_a.py")
        self.commit("c2")
        self.red_green("tests/test_a.py::test_a")
        self.w("notes.txt", "a\n")
        self.commit("c3")
        self.w("notes.txt", "b\n")
        tr = self.head_trace()
        self.assertEqual("present", P13._t(tr, "test:tests/test_a.py::test_a")["state"], self.why(tr))
        self.assertEqual("proven", P13._ac(tr, "AC-3")["status"], self.why(tr))

    def test_E2_AtHead_a_link_free_test_still_proves(self):
        self.w("tests/test_a.py")
        self.commit("c2")
        self.red_green("tests/test_a.py::test_a")
        self.w("notes.txt", "a\n")
        self.commit("c3")
        self.w("notes.txt", "b\n")
        tr = self.head_trace()
        self.assertEqual("proven", P13._ac(tr, "AC-3")["status"], self.why(tr))


class OneTarget(_Bound):
    def test_E2_OneTarget_writer_and_reader_agree(self):
        self.w("tests/test_a.py")
        self.link("test_a.py", "tests/alias.py")
        self.w("d1/test_w.py")
        self.link("d1", "tlink", directory=True)
        self.commit()
        for tid, rel in (("tests/alias.py::test_a", "tests/alias.py"),
                         ("tlink/test_w.py::test_a", "tlink/test_w.py"),
                         ("tests/test_a.py::test_a", "tests/test_a.py")):
            self.red_green(tid)
            self.assertEqual(TI.stamp_target(self.d, rel), self.history()[tid]["target"])
            loc = P._Locator(self.d, self.d)
            self.assertEqual(self.history()[tid]["target"], loc.target(rel))

    def test_E2_the_stamp_of_a_link_free_test_is_unchanged(self):
        """No stale-hint storm: for every test with no link on its path the stamp is master's."""
        self.w("tests/conftest.py", "import pytest\n")
        self.w("tests/test_a.py")
        self.commit()
        with open(self.p("tests/test_a.py"), encoding="utf-8") as fh:
            text = fh.read()

        def read(rel):
            try:
                with open(self.p(rel), encoding="utf-8") as fh:
                    return fh.read()
            except OSError:
                return None
        for spelling in ("tests/test_a.py",) + (("Tests/Test_A.py",) if TI.fs_folds_case(self.d) else ()):
            master = TI.stamp_of(spelling, text, "test_a", read=read)
            self.assertEqual(master, TR.stamps_of(self.d, [f"{spelling}::test_a"])[f"{spelling}::test_a"])

    def _conftests(self):
        self.w("tests/conftest.py", "import pytest\n")
        self.w("other/conftest.py", "import pytest\n")
        self.w("other/test_a.py")
        self.link("../other/test_a.py", "tests/alias.py")
        self.commit()
        self.red_green("tests/alias.py::test_a")
        self.assertEqual("proven", self.ac()[0]["status"], self.why(self.ac()[1]))

    def test_E2_the_stamp_covers_the_real_directorys_conftest(self):
        self._conftests()
        self.w("other/conftest.py", "import pytest\n\n\n@pytest.fixture(autouse=True)\ndef f():\n    assert 0\n")
        self.assertNotEqual("proven", self.ac()[0]["status"])

    def test_E2_the_stamp_covers_the_typed_directorys_conftest(self):
        self._conftests()
        self.w("tests/conftest.py", "import pytest\n\n\n@pytest.fixture(autouse=True)\ndef f():\n    assert 0\n")
        self.assertNotEqual("proven", self.ac()[0]["status"])

    def test_E2_OneTarget_scale_the_trace_lists_directories_by_the_ids_never_by_the_tree(self):
        """The 68k-file cell's invariant, at any size: the directories the target function lists are
        bounded by the recorded ids. Grown 10x, the count does not move. (`MOKATA_E2_SCALE=1` runs the
        68k-file tree itself and records the trace time in the evidence log.)"""
        from mokata import fs_identity
        n_files = 68000 if os.environ.get("MOKATA_E2_SCALE") == "1" else 600
        self.w("tests/test_a.py")
        self.link("test_a.py", "tests/alias.py")
        self.commit()
        self.red_green("tests/alias.py::test_a")
        counts, times = [], []
        for size in (n_files // 10, n_files):
            bulk = os.path.join(self.d, "bulk")
            shutil.rmtree(bulk, ignore_errors=True)
            for i in range(size):
                sub = os.path.join(bulk, f"d{i % 97}")
                os.makedirs(sub, exist_ok=True)
                with open(os.path.join(sub, f"f{i}.txt"), "w", encoding="utf-8") as fh:
                    fh.write("x")
            fs_identity.forget()
            seen = []
            real = fs_identity._listing

            def counting(directory):
                seen.append(str(directory))
                return real(directory)
            fs_identity._listing = counting
            try:
                t0 = time.perf_counter()
                tr = P.trace(self.d, run="r1")
                times.append(time.perf_counter() - t0)
            finally:
                fs_identity._listing = real
            self.assertEqual("proven", P13._ac(tr, "AC-3")["status"])
            counts.append(len([s for s in seen if os.path.join(self.d, "bulk") in s]))
        self.assertEqual([0, 0], counts, "the target function listed the bulk of the tree")
        if os.environ.get("MOKATA_E2_SCALE") == "1":
            print(f"\nE2.OneTarget scale: files={n_files // 10},{n_files} trace_s={times}")


if __name__ == "__main__":
    unittest.main()
