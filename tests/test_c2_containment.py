"""0.0.28 lane C2 — `MAC-CASEFLIP-PATH-INTO-REPO`: a PATH entry that is, on THIS disk, inside the repo never
supplies a tool, however its case is spelled; and a path that must be inside (a `node_modules` bin) is never
accepted on a spelling trick.

Business rules (note C §3d, rule page §3d): two questions, two named predicates in `fs_identity`, no default:
  * `refuse_if_inside` — identity OR the fold; an unreadable candidate chain is inside; a base with no
    identity lets the fold decide. On a case-sensitive volume a fold-equal sibling IS refused (stated cost);
  * `require_inside` — identity, else the exact realpath string; NEVER the fold; undecidable is outside;
  * `same_entry` — the cwd skip and `path_dirs` are an equality, not containment.
Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import _support  # noqa: F401

from mokata import fs_identity as F

SRC = Path(__file__).resolve().parents[1] / "src" / "mokata"


def _tool(name: str) -> str:
    """A spawned program by its resolved path (never a bare name: `_windows_portability`)."""
    return shutil.which(name) or name



def _exe(path: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\necho planted\n")
    os.chmod(path, 0o755)
    return path


def _flip(path: str) -> str:
    """`path` with its LAST component's case swapped (`repo` -> `REPO`)."""
    head, tail = os.path.split(path.rstrip("/\\"))
    return os.path.join(head, tail.swapcase())


class _Tmp(unittest.TestCase):
    def setUp(self):
        t = tempfile.mkdtemp(prefix="mk-c2-")
        self.addCleanup(shutil.rmtree, t, True)
        self.base = os.path.realpath(t)
        self.repo = os.path.join(self.base, "repo")
        os.makedirs(os.path.join(self.repo, "tools"))
        self.folds = F.folds_case(self.base)


# =============================================================== refuse_if_inside

class RefuseIfInside(_Tmp):
    def test_exact_trailing_slash_dotdot_and_nested(self):
        r = self.repo
        self.assertTrue(F.refuse_if_inside(r, r))
        self.assertTrue(F.refuse_if_inside(r + os.sep, r))
        self.assertTrue(F.refuse_if_inside(os.path.join(r, "tools"), r))
        self.assertTrue(F.refuse_if_inside(os.path.join(r, "tools", "..", "tools", "x"), r))
        self.assertFalse(F.refuse_if_inside(os.path.join(r, "..", "elsewhere"), r))
        self.assertFalse(F.refuse_if_inside(r + "2", r), "/repo2 is not inside /repo (component-wise)")

    def test_a_flipped_spelling_is_refused_on_every_volume(self):
        """Folding volume: identity says inside. Case-sensitive volume: the fold says inside (the
        stated over-refusal). Either way the flip is refused."""
        r = self.repo
        self.assertTrue(F.refuse_if_inside(_flip(r), r))
        self.assertTrue(F.refuse_if_inside(os.path.join(_flip(r), "TOOLS"), r))
        self.assertTrue(F.refuse_if_inside(os.path.join(os.path.dirname(r), "Repo", "TOOLS"), r))

    @unittest.skipUnless(hasattr(os, "symlink") and os.name != "nt", "POSIX symlinks")
    def test_symlinks_in_and_out(self):
        outside = os.path.join(self.base, "outside")
        os.makedirs(outside)
        os.symlink(os.path.join(self.repo, "tools"), os.path.join(outside, "into-repo"))
        os.symlink(outside, os.path.join(self.repo, "out-link"))
        self.assertTrue(F.refuse_if_inside(os.path.join(outside, "into-repo"), self.repo))
        self.assertFalse(F.refuse_if_inside(os.path.join(self.repo, "out-link"), self.repo))

    def test_a_hard_link_outside_is_accepted_as_stated(self):
        """L7: a hard link to a repo binary in an OUTSIDE directory passes (its ancestors are
        outside; git cannot create one)."""
        src = _exe(os.path.join(self.repo, "tools", "poetry"))
        out = os.path.join(self.base, "bin")
        os.makedirs(out)
        try:
            os.link(src, os.path.join(out, "poetry"))
        except OSError as exc:
            self.skipTest(f"NOT RUN: no hard links here ({exc})")
        self.assertFalse(F.refuse_if_inside(os.path.join(out, "poetry"), self.repo))

    def test_a_hundred_thousand_path_entries_cost_linear_in_entries_times_depth(self):
        """Scale: the stat count is bounded by entries x depth (each walk stops at the root)."""
        entries = [os.path.join(self.base, f"d{i}", "bin") for i in range(100_000)]
        calls = []
        real = F._stat
        with mock.patch.object(F, "_stat", side_effect=lambda p: (calls.append(p), real(p))[1]):
            for e in entries:
                F.refuse_if_inside(e, self.repo)
        depth = len(Path(entries[0]).parts) + 1
        self.assertLessEqual(len(calls), len(entries) * depth + 1)


# =============================================================== require_inside

class RequireInside(_Tmp):
    def setUp(self):
        super().setUp()
        self.modules = os.path.join(self.repo, "node_modules")
        os.makedirs(os.path.join(self.modules, "pkg", "bin"))

    def test_inside_is_inside(self):
        self.assertTrue(F.require_inside(os.path.join(self.modules, "pkg", "bin", "x.js"),
                                         self.modules))

    def test_a_climbing_bin_is_refused(self):
        climb = os.path.join(self.modules, "pkg", "..", "..", "..", "evil.js")
        self.assertFalse(F.require_inside(os.path.realpath(climb), self.modules))

    @unittest.skipIf(os.name == "nt", "POSIX symlink")
    def test_node_modules_symlinked_out_is_refused(self):
        outside = os.path.join(self.base, "anywhere")
        os.makedirs(outside)
        shutil.rmtree(self.modules)
        os.symlink(outside, self.modules)
        self.assertFalse(F.require_inside(self.modules, self.repo))

    def test_a_fold_equal_distinct_sibling_is_refused(self):
        """On a case-sensitive volume `REPO` beside `repo` is ANOTHER directory: require_inside never
        folds, so it is outside. (On a folding volume the two are one directory: inside, by identity.)"""
        other = _flip(self.repo)
        if not self.folds:
            os.makedirs(os.path.join(other, "node_modules"))
            self.assertFalse(F.require_inside(os.path.join(other, "node_modules"), self.repo))
        else:
            self.assertTrue(F.require_inside(os.path.join(other, "node_modules"), self.repo))

    def test_forced_undecidable_identity_is_refused(self):
        climb = os.path.join(self.base, "evil.js")
        with mock.patch.object(F, "_stat", side_effect=OSError("unreadable")):
            self.assertFalse(F.require_inside(climb, self.modules))

    def test_require_inside_never_calls_the_fold(self):
        with mock.patch.object(F, "fold_component", side_effect=AssertionError("folded")):
            F.require_inside(os.path.join(self.modules, "pkg"), self.modules)
            F.require_inside(_flip(self.modules), self.modules)


# =============================================================== undecidable

class Undecidable(_Tmp):
    def test_an_unreadable_candidate_chain_is_inside_for_the_refuse_side(self):
        real = F._stat
        cand = os.path.join(self.base, "elsewhere", "bin")

        def stat(p):                     # only the BASE can be stat-ed: the candidate chain cannot
            if p != os.path.realpath(self.repo):
                raise OSError("unreadable")
            return real(p)
        with mock.patch.object(F, "_stat", side_effect=stat):
            self.assertTrue(F.refuse_if_inside(cand, self.repo))
            self.assertFalse(F.require_inside(cand, self.repo))

    def test_a_base_with_no_identity_lets_the_fold_decide(self):
        real = F._stat

        def stat(p):
            st = real(p)
            if p == os.path.realpath(self.repo):
                return os.stat_result((st.st_mode, 0, st.st_dev) + tuple(st)[3:10])
            return st
        with mock.patch.object(F, "_stat", side_effect=stat):
            self.assertTrue(F.refuse_if_inside(os.path.join(self.repo, "tools"), self.repo))
            self.assertFalse(F.refuse_if_inside(os.path.join(self.base, "x"), self.repo))
            self.assertFalse(F.identity_known(self.repo))

    def test_a_zero_index_never_counts_as_identity(self):
        real = F._stat

        def stat(p):
            st = real(p)
            return os.stat_result((st.st_mode, 0, st.st_dev) + tuple(st)[3:10])
        with mock.patch.object(F, "_stat", side_effect=stat):
            self.assertFalse(F.same_entry(self.repo, self.base))
            self.assertFalse(F.require_inside(os.path.join(self.base, "x"), self.repo))


# =============================================================== which_in end to end

class WhichInCaseflip(_Tmp):
    """LB5-1: `/x/REPO/tools` on PATH, `refuse_under=/x/repo`: the planted tool never answers."""

    def _name(self, n):
        return n + ".exe" if os.name == "nt" else n

    def test_a_planted_tool_behind_a_flipped_entry_is_refused(self):
        from mokata.exe_path import path_dirs, which_in
        for tool in ("poetry", "git", "node"):
            _exe(os.path.join(self.repo, "tools", self._name(tool)))
        flipped = os.path.join(_flip(self.repo), "TOOLS")
        for tool in ("poetry", "git", "node"):
            self.assertIsNone(which_in(tool, [flipped], refuse_under=self.repo), tool)
        dirs = path_dirs({"PATH": os.pathsep.join([flipped, os.path.join(self.repo, "tools")])},
                         cwd=self.base)
        self.assertIsNone(which_in("poetry", dirs, refuse_under=self.repo))

    def test_the_control_outside_answers(self):
        from mokata.exe_path import which_in
        out = os.path.join(self.base, "bin")
        want = _exe(os.path.join(out, self._name("poetry")))
        self.assertEqual(os.path.normcase(want),
                         os.path.normcase(which_in("poetry", [out], refuse_under=self.repo)))

    @unittest.skipIf(os.name == "nt", "the venv bin layout is POSIX here")
    def test_an_activated_repo_venv_still_answers_find_binary(self):
        from mokata.ide.lsp import find_binary
        venv_bin = os.path.join(self.repo, ".venv", "bin")
        want = _exe(os.path.join(venv_bin, "pyright-langserver"))
        with mock.patch.dict(os.environ, {"PATH": venv_bin}):
            self.assertEqual(want, find_binary(self.repo, "pyright-langserver", project=True))

    def test_the_lsp_package_bin_refuses_a_flipped_escape(self):
        from mokata.ide import lsp
        mods = os.path.join(self.repo, "node_modules")
        pkg = os.path.join(mods, "typescript-language-server")
        os.makedirs(pkg)
        with open(os.path.join(pkg, "package.json"), "w", encoding="utf-8") as fh:
            json.dump({"bin": {"typescript-language-server": "../../../evil.js"}}, fh)
        _exe(os.path.join(self.base, "evil.js"))
        head, why = lsp._package_bin(mods, self.repo, self.repo, "typescript-language-server")
        self.assertIsNone(head)
        self.assertIn("refused", why)


class CwdSkip(_Tmp):
    def test_the_cwd_skip_is_equality_not_containment(self):
        from mokata.exe_path import path_dirs, which_in
        sub = os.path.join(self.repo, "tools")
        got = path_dirs({"PATH": os.pathsep.join([self.repo, _flip(self.repo), sub])}, cwd=self.repo)
        expect = [sub] if self.folds else [_flip(self.repo), sub]
        self.assertEqual(expect, got)
        _exe(os.path.join(sub, "x.exe" if os.name == "nt" else "x"))
        cwd = os.getcwd()
        os.chdir(self.repo)
        try:
            self.assertIsNotNone(which_in("x", [sub]), "a subdirectory of the cwd is not the cwd")
        finally:
            os.chdir(cwd)

    def test_which_in_skips_the_cwd_typed_in_another_case(self):
        """A planted `x` in the cwd, reached through the cwd's flipped spelling, never answers."""
        from mokata.exe_path import which_in
        _exe(os.path.join(self.repo, "x.exe" if os.name == "nt" else "x"))
        cwd = os.getcwd()
        os.chdir(self.repo)
        try:
            got = which_in("x", [_flip(self.repo)])
        finally:
            os.chdir(cwd)
        if self.folds:
            self.assertIsNone(got, "the cwd answered through a case-flipped PATH entry")
        else:
            self.assertIsNone(got, "case-sensitive: the flipped directory does not exist")

    def test_path_dirs_uses_same_entry(self):
        from mokata import exe_path
        calls = []
        real = F.same_entry
        with mock.patch.object(exe_path, "same_entry",
                               side_effect=lambda a, b: (calls.append((a, b)), real(a, b))[1]):
            exe_path.path_dirs({"PATH": self.repo}, cwd=self.base)
        self.assertTrue(calls)


# =============================================================== one door

_SCOPE = ("exe_path.py", "git_control.py", "ide/lsp.py", "ide/review_context.py", "ide/snapshots.py")


def door_violations(corpus):
    """Pure: offences against the one door in a `{relpath: source}` corpus — a function named `_under`
    or `_identity_under` anywhere, and a `commonpath(...)` or `normcase(realpath(...))` containment in a
    scoped file. `ide/lsp.py` is scoped to its executable/package region (`find_binary`..`_shim_only`)."""
    out = []
    for rel, src in corpus.items():
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in ("_under", "_identity_under"):
                out.append((rel, node.lineno, node.name))
        if rel not in _SCOPE:
            continue
        lo, hi = 0, 10 ** 9
        if rel == "ide/lsp.py":
            for node in tree.body:
                if isinstance(node, ast.FunctionDef) and node.name == "find_binary":
                    lo = node.lineno
                if isinstance(node, ast.FunctionDef) and node.name == "_shim_only":
                    hi = node.lineno
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not (lo <= node.lineno < hi):
                continue
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            if name == "commonpath":
                out.append((rel, node.lineno, "commonpath"))
            if name == "normcase" and node.args and isinstance(node.args[0], ast.Call):
                inner = node.args[0].func
                iname = inner.attr if isinstance(inner, ast.Attribute) else getattr(inner, "id", "")
                if iname == "realpath":
                    out.append((rel, node.lineno, "normcase(realpath)"))
    return out


class OneDoor(unittest.TestCase):
    def test_the_sweep_finds_planted_offenders(self):
        planted = {"exe_path.py": "import os\ndef _under(p, b):\n    return os.path.commonpath([p, b]) == b\n",
                   "other.py": "def _identity_under(p, ids):\n    return False\n",
                   "ide/snapshots.py": "import os\nx = os.path.normcase(os.path.realpath('a'))\n"}
        got = {(r, n) for r, _l, n in door_violations(planted)}
        self.assertEqual({("exe_path.py", "_under"), ("exe_path.py", "commonpath"),
                          ("other.py", "_identity_under"), ("ide/snapshots.py", "normcase(realpath)")},
                         got)

    def test_the_tree_has_one_door(self):
        corpus = {"/".join(p.relative_to(SRC).parts): p.read_text(encoding="utf-8")
                  for p in SRC.rglob("*.py")}
        self.assertEqual([], door_violations(corpus))

    def test_every_containment_caller_uses_a_named_predicate(self):
        """The classification of note §3d, read from the code: which predicate each caller calls."""
        want = {"exe_path.py": {"refuse_if_inside", "same_entry"},
                "ide/lsp.py": {"require_inside", "refuse_if_inside"},
                "ide/review_context.py": {"refuse_if_inside"},
                "git_control.py": {"refuse_if_inside"}}
        for rel, names in want.items():
            src = (SRC / rel).read_text(encoding="utf-8")
            for n in names:
                self.assertIn(n + "(", src, f"{rel} must ask {n}")
            self.assertNotIn("undecided=", src)


# =============================================================== a REAL case-sensitive volume

@unittest.skipUnless(sys.platform == "darwin", "the case-sensitive APFS image is the macOS cell")
class CaseSensitiveImage(unittest.TestCase):
    """A case-sensitive APFS image (`hdiutil`, no sudo): `REPO` beside `repo` is another directory.
    refuse_if_inside still refuses it (the fold, stated cost); require_inside does not accept it."""

    def setUp(self):
        t = tempfile.mkdtemp(prefix="mk-c2cs-")
        self.addCleanup(shutil.rmtree, t, True)
        img, self.mnt = os.path.join(t, "cs.dmg"), os.path.join(t, "mnt")
        os.makedirs(self.mnt)
        subprocess.run([_tool("hdiutil"), "create", "-size", "20m", "-fs", "Case-sensitive APFS",
                        "-volname", "mkcs", img], check=True, capture_output=True)
        subprocess.run([_tool("hdiutil"), "attach", "-nobrowse", "-mountpoint", self.mnt, img],
                       check=True, capture_output=True)
        self.addCleanup(subprocess.run, [_tool("hdiutil"), "detach", "-force", self.mnt],
                        capture_output=True)
        self.mnt = os.path.realpath(self.mnt)

    def test_on_a_case_sensitive_volume(self):
        repo, other = os.path.join(self.mnt, "repo"), os.path.join(self.mnt, "REPO")
        os.makedirs(os.path.join(repo, "node_modules"))
        os.makedirs(os.path.join(other, "tools"))
        self.assertFalse(F.folds_case(self.mnt))
        self.assertFalse(F.same_entry(repo, other))
        self.assertTrue(F.refuse_if_inside(os.path.join(other, "tools"), repo))
        self.assertFalse(F.require_inside(os.path.join(other, "tools"), repo))
        self.assertTrue(F.require_inside(os.path.join(repo, "node_modules"), repo))


if __name__ == "__main__":
    unittest.main()
