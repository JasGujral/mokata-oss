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

from _door_sweep import door_violations, grouped  # noqa: E402  (0.0.29 H3: the sweep, shared with H3)

REST = "CONTAINMENT-STRING-SITES-REST"
UNLISTED = "CONTAINMENT-STRING-SITES-UNLISTED"
_WALKED = "enumerates a tree mokata itself walked (every name came from the walk); not a containment question"
_KEY = (UNLISTED + ": a spelling used as a record/cache KEY, not a decision; a flipped spelling gets a "
        "second key (over-separates, the safe side)")

#: `(file, function, idiom) -> (count, reason)` — EVERY string containment/equality decision left in `src/`
#: outside `fs_identity.py`, each with a reason (an empty one is a failure), with an EXACT count (a second
#: offender in an allowlisted function reds; attack H3-3), and never stale (an entry no violation matches
#: reds). Sites in other lanes' files carry the doc 84 row id that owns their move.
_NAME = ("compares repo-relative NAMES (or name keys) that mokata itself produced (`name_of`, a walk, git "
         "output) — the name rule, not a disk containment question")
_CMP = "a comparator of values that are not paths"
_DEDUP = ("de-duplicates a list of directories mokata assembled itself; a flipped spelling is listed twice "
          "(a repeat, never a wrong answer)")
_CACHE = UNLISTED + ": an in-process CACHE key; a flipped spelling misses the cache (recomputes), never a wrong answer"

#: `(file, scope, idiom) -> (count, reason)` — EVERY comparison of a path-tainted value left in `src/` outside
#: `fs_identity.py` (round 2: the sweep is a class rule, `tests/_door_sweep.py`), each with a reason (an
#: empty one is a failure), with an EXACT count (a second offender in an allowlisted scope reds; attack
#: H3-3), and never stale (an entry no violation matches reds). Sites in other lanes' files carry the doc 84
#: row id that owns their move. `scope` is qualified (`C.m`, `f.<lambda>`, `f.inner`).
_ALLOW = {
    # note H §3d: allowlisted with a reason
    ("spec_scope.py", "_rel", "name_of"): (1, "names a TYPED repo path as a spec record key; any access "
                                              "goes through the gate later; a flip over-blocks (safe side)"),
    ("govern/secret_ignore.py", "normalize_target", "name_of"): (1, "names a TYPED path as an ignore-record "
                                                                    "key; a flip over-blocks (safe side)"),
    ("merge_lock.py", "network_fs", "startswith"): (1, "longest mount prefix in /proc/self/mountinfo: kernel "
                                                      "mount TEXT, not a path the user typed"),
    ("merge_lock.py", "network_fs", "equality"): (1, "the same mount-table TEXT match (the mount point itself)"),
    ("agent_skills.py", "copy_skill_references", "relative_to"): (1, _WALKED),
    ("agent_skills.py", "install_domain_skills", "relative_to"): (1, _WALKED),
    ("docsync.py", "find_docs", "relative_to"): (1, _WALKED),
    ("docsync.py", "find_docs", "startswith"): (1, _WALKED + " (is a walked doc under an internal docs dir name)"),
    # wave-2 integration (0.0.29 H-b onto 0029/security-a): the new disk-touching code G/F/H-a added
    ("briefing_cache.py", "store_resume", "equality"): (1, _KEY + " (which recorded key is this session's own "
                                                           "index row; a miss leaves the key unslotted and "
                                                           "the briefing is re-derived)"),
    ("hints.py", "derive_batch", "membership"): (1, REST),   # drops the event store's db/-wal/-shm from cache inputs
    ("pycache.py", "prune", "equality"): (1, "the NAME shape of the resolved prefix (`…/mokata/pycache`) as a "
                                             "cheap pre-check; the walk itself is held by descriptor from an "
                                             "fstat-checked root (`_open_root`: dev/ino + owner), so no name "
                                             "decides what is unlinked"),
    ("pycache.py", "prune", "order"): (1, _CMP + " (a frame's entry index against its entry count)"),
    ("harness_setup.py", "vendored_drift", "membership"): (1, "`present` is a filter of `expected`: the same "
                                                              "Path objects mokata built, not a spelling test"),
    ("ide/sessions.py", "project_slug", "regex"): (1, "a re.sub TRANSFORM (Claude Code's folder slug), not "
                                                      "a test"),
    ("harness_setup.py", "vendored_files", "relative_to"): (1, _WALKED),
    ("test_ids.py", "normalise_path", "dotdot"): (2, "a lexical NAME check on a typed test id (does it "
                                                    "climb?), not a filesystem decision"),
    ("test_ids.py", "_module_hits", "membership"): (1, _NAME + " (a parse cache keyed by module name)"),
    ("repo_paths.py", "escapes_root", "dotdot"): (1, "THE lexical climb rule on a repo NAME (`..`, `../x`); "
                                                     "repo_paths owns names, fs_identity owns the disk"),
    ("ide/snapshots.py", "record_name", "dotdot"): (2, "a lexical climb check on the name `disk_rel` just "
                                                       "built (defence in depth after the identity decision)"),
    ("ide/snapshots.py", "_known_tail", "slice"): (1, "picks the store's own spelling of a VANISHED tail among "
                                                      "names the store recorded; the file is gone, so no disk "
                                                      "decision rests on it (containment was decided by the "
                                                      "door before)"),
    ("committed_specs.py", "_find_tests", "membership"): (1, _NAME),
    ("ide/nav.py", "_DirIndex.__init__", "membership"): (1, _NAME),
    ("ide/search.py", "_rg", "membership"): (2, _NAME + " (the explorer's file set)"),
    ("ide/search.py", "_fast_rel", "equality"): (1, _CACHE),
    ("ide/search.py", "_matrix", "equality"): (1, _CACHE),
    ("ide/features/lsp.py", "ac_ties", "equality"): (1, _NAME + " (a spec anchor's name vs the open file's)"),
    ("ide/proof.py", "_Locator._real", "equality"): (1, "did `disk_rel` respell the name? (after the identity "
                                                        "decision; equality of two names)"),
    ("ide/proof.py", "_Locator.link_at_head", "membership"): (1, _NAME + " (git ls-tree symlink names)"),
    ("knowledge/ast_backend.py", "AstBackend._walk_index", "membership"): (1, _NAME),
    ("knowledge/ast_backend.py", "AstBackend._uncovered_files", "membership"): (1, _NAME),
    ("selfprotect.py", "in_installed_tree", "membership"): (1, "one component NAME vs `site-packages` / "
                                                               "`dist-packages`: the name rule"),
    ("tdd_state.py", "root_of_state_dir", "equality"): (1, "the LAYOUT tail of a state dir mokata made "
                                                           "(component names); a mismatch says no run, never "
                                                           "guesses"),
    ("exe_path.py", "which_in", "startswith"): (1, "an EXTENSION test of one candidate name (`.bat`/`.cmd`)"),
    ("exe_path.py", "win_runnable", "equality"): (1, "did resolving change the path? then the target is "
                                                     "checked too (a flip only adds a check: the safe side)"),
    ("detect.py", "env_scripts_dirs", "membership"): (1, _DEDUP),
    ("harness_setup.py", "resolved_console_script", "membership"): (1, _DEDUP),
    ("git_control.py", "_config_values", "membership"): (1, "the include-loop guard: a flipped spelling of one "
                                                            "config file is read once more (over-approximates, "
                                                            "the gate's safe side); spellings come from finite "
                                                            "file text, so the loop still ends"),
    ("git_control.py", "_git_dirs", "equality"): (1, "drops the `.git` FILE from `store_dirs`' own list: both "
                                                     "sides are the same join of the same `cur`"),
    ("worktree_setup.py", "apply_rows", "equality"): (1, "the plan's own row for `settings_path` carries "
                                                         "`str(settings_path)` (plan_rows): a value against "
                                                         "itself"),
    ("run_record.py", "fold_home", "membership"): (1, "redacts the home directory out of TEXT (a substring "
                                                      "replace), not a decision"),
    ("project.py", "derive_project_id", "membership"): (1, _CACHE),
    ("mcp/server.py", "_serves", "comparator"): (1, _CMP + " (tree CONTENT keys)"),
    ("memory/review.py", "separation_ok", "comparator"): (1, _CMP + " (approver/proposer identities)"),
    ("memory/staleness.py", "is_stale", "comparator"): (1, _CMP + " (handle stamps)"),
    ("knowledge/freshness.py", "fingerprint_forces_refresh", "comparator"): (1, _CMP + " (fingerprints)"),
    # CONTAINMENT-STRING-SITES-REST (note H §1): files other lanes edit in 0.0.29
    ("hook_cli.py", "_main_equivalent", "equality"): (1, REST),
    ("hook_cli.py", "dirty_track_main", "equality"): (1, REST),
    ("hook_cli.py", "_matcher_covers", "comparator"): (1, REST),     # (hook matcher strings, not paths)
    ("session_registry.py", "_registry_store", "equality"): (1, REST),
    ("run_resolver.py", "_same_repo", "equality"): (1, REST),
    ("knowledge/crg_client.py", "_rel", "dotdot"): (2, REST),
    ("knowledge/crg_nodes.py", "NodeReader._rel", "dotdot"): (2, REST),
    ("knowledge/crg_nodes.py", "NodeReader.symbols", "startswith"): (1, REST),
    ("hints.py", "_inputs.keep", "startswith"): (2, REST),
    ("hints.py", "_inputs.keep", "equality"): (2, REST),
    # CONTAINMENT-STRING-SITES-UNLISTED (rule page rev 2 + this build): sites the note did not list, in files
    # H does not own (or, `tree_key`, a key and not a decision), filed for 0.0.30
    ("ide/launch.py", "running", "equality"): (1, UNLISTED + ": H-a's file (H4 rewrites `running`)"),
    ("ide/context.py", "_store", "key"): (1, _KEY + "; ide/context.py is on note H's NOT-touched list"),
    ("ide/statedir.py", "repo_key", "key"): (1, _KEY),
    ("ide/workspace.py", "_memory_key", "key"): (1, _KEY),
    ("crg_resolve.py", "_norm", "key"): (1, UNLISTED + ": which CRG binary answers (a label/warning), "
                                                       "not an access decision"),
    ("crg_resolve.py", "resolve_crg", "membership"): (1, UNLISTED + ": the same label/warning (is the CRG on "
                                                                    "PATH outside the expected dirs?)"),
    ("repo_identity.py", "tree_key", "key"): (1, _KEY + " (a worktree's scratch directory name)"),
    ("ide/nav.py", "note_change", "equality"): (1, UNLISTED + ": which OTHER roots' caches to invalidate; "
                                                           "a flip over-invalidates (safe side); nav.py is "
                                                           "lanes A/E's"),
    ("knowledge/layer.py", "KnowledgeLayer.from_surface", "equality"): (
        1, UNLISTED + ": reuses the open surface when it is the main checkout, else re-loads it (a flip "
                      "costs a re-load)"),
    ("explain/spill.py", "live", "equality"): (1, UNLISTED + ": an in-process registry key; a flip lists "
                                                            "nothing (no access)"),
    ("explain/spill.py", "release", "equality"): (1, UNLISTED + ": an in-process registry key; a flip "
                                                               "releases nothing"),
    ("harness_setup.py", "_same_file", "equality"): (1, UNLISTED + ": setup's own two paths; a flip "
                                                                  "refuses a setup (safe side); C1's "
                                                                  "file in wave 3"),
}

#: Every idiom the sweep must catch, as a planted offender in a file NO old scope named. A planted case
#: per idiom keeps the sweep from going vacuous (a narrowed scope or a dropped idiom reds here).
_PLANTED = {
    "commonpath": "import os\ndef f(p, b):\n    return os.path.commonpath([p, b]) == b\n",
    "relative_to": "from pathlib import Path\ndef f(p, b):\n    return Path(p).relative_to(b)\n",
    "parents": "from pathlib import Path\ndef f(p, b):\n    return Path(b) in Path(p).parents\n",
    "dotdot": "import os\ndef f(p, b):\n    r = os.path.relpath(p, b)\n    return r.startswith('..')\n",
    "startswith": "import os\ndef f(p, b):\n    rb = os.path.realpath(b)\n    return p.startswith(rb)\n",
    "equality": "import os\ndef f(a, b):\n    return os.path.realpath(a) == os.path.realpath(b)\n",
    "key": "import os\ndef f(a):\n    return os.path.normcase(os.path.realpath(a))\n",
    "name_of": "from .repo_paths import escapes_root, name_of\ndef f(p, b):\n"
               "    return not escapes_root(name_of(p, b))\n",
    "helper": "def _within(p, b):\n    return False\n",
    "membership": "import os\ndef f(p, seen):\n    return os.path.realpath(p) in seen\n",
    "order": "import os\ndef f(p, b):\n    return os.path.realpath(p) < b\n",
    "substring": "import os\ndef f(p, b):\n    return os.path.realpath(p).find(b) == 0\n",
    "regex": "import os, re\ndef f(p, b):\n    return re.match(b, os.path.realpath(p))\n",
    "comparator": "def f(a, b):\n    return a == b\n",
    "pattern": "import os, fnmatch\ndef f(p, b):\n    return fnmatch.fnmatch(os.path.realpath(p), b + '/*')\n",
    "match": "import os\ndef f(p, K):\n    match os.path.realpath(p):\n        case K.ROOT:\n            return True\n",
    "literal_path": "import os\ndef f(b):\n    for r, ds, fs in os.walk(b):\n        if r == '/x':\n            return True\n",
}


#: The verifier's 38 planted variants (0.0.29 H-b verify, `sweep_variants.py`), embedded verbatim: round 1's
#: shape matcher missed 21 of them. Every one must red under the class rule.
_VARIANTS = {
 # baseline (must be caught)
 "BASE realpath+sep startswith": "import os\ndef f(p,b):\n    return os.path.realpath(p).startswith(os.path.realpath(b)+os.sep)\n",
 # aliasing
 "alias local rp = os.path.realpath": "import os\ndef f(p,b):\n    rp = os.path.realpath\n    return rp(p).startswith(rp(b)+os.sep)\n",
 "alias module-level rp": "import os\nrp = os.path.realpath\ndef f(p,b):\n    return rp(p).startswith(rp(b)+os.sep)\n",
 "getattr(os.path,'realpath')": "import os\ndef f(p,b):\n    g = getattr(os.path,'realpath')\n    return g(p).startswith(g(b)+os.sep)\n",
 "lambda": "import os\nf = lambda p,b: os.path.realpath(p).startswith(os.path.realpath(b)+os.sep)\n",
 "lambda default arg": "import os\ndef mk():\n    return lambda p,b: os.path.realpath(p).startswith(os.path.realpath(b)+os.sep)\n",
 "nested closure": "import os\ndef f(p,b):\n    rb = os.path.realpath(b)\n    def g(x):\n        return os.path.realpath(x).startswith(rb)\n    return g(p)\n",
 "param prefix startswith(base)": "import os\ndef f(p,base):\n    return os.path.realpath(p).startswith(base)\n",
 "param equality": "def f(a,b):\n    return a == b\n",
 "fstring prefix": "import os\ndef f(p,b):\n    return os.path.realpath(p).startswith(f'{os.path.realpath(b)}/')\n",
 "percent prefix": "import os\ndef f(p,b):\n    return os.path.realpath(p).startswith('%s/' % os.path.realpath(b))\n",
 "format prefix": "import os\ndef f(p,b):\n    return os.path.realpath(p).startswith('{}/'.format(os.path.realpath(b)))\n",
 "join prefix": "import os\ndef f(p,b):\n    return os.path.realpath(p).startswith(os.path.join(os.path.realpath(b), ''))\n",
 "startswith(os.pardir)": "import os\ndef f(p,b):\n    r = os.path.relpath(p,b)\n    return r.startswith(os.pardir)\n",
 "split()[0]=='..'": "import os\ndef f(p,b):\n    r = os.path.relpath(p,b)\n    return r.split(os.sep)[0] == '..'\n",
 "'..' in split": "import os\ndef f(p,b):\n    r = os.path.relpath(p,b)\n    return '..' in r.split(os.sep)\n",
 "r[:2]=='..'": "import os\ndef f(p,b):\n    r = os.path.relpath(p,b)\n    return r[:2] == '..'\n",
 "slice prefix": "import os\ndef f(p,b):\n    rb = os.path.realpath(b)\n    return os.path.realpath(p)[:len(rb)] == rb\n",
 "parents alias": "from pathlib import Path\ndef f(p,b):\n    ps = Path(p).resolve().parents\n    return Path(b).resolve() in ps\n",
 "parents list": "from pathlib import Path\ndef f(p,b):\n    return Path(b).resolve() in list(Path(p).resolve().parents)\n",
 "parts slice": "from pathlib import Path\ndef f(p,b):\n    a=Path(p).resolve().parts; c=Path(b).resolve().parts\n    return a[:len(c)] == c\n",
 "parts tuple compare": "from pathlib import Path\ndef f(p,b):\n    return Path(p).resolve().parts[:2] == Path(b).resolve().parts\n",
 "substring in": "import os\ndef f(p,b):\n    return os.path.realpath(b) in os.path.realpath(p)\n",
 "find==0": "import os\ndef f(p,b):\n    return os.path.realpath(p).find(os.path.realpath(b)) == 0\n",
 "re.match": "import os, re\ndef f(p,b):\n    return bool(re.match(re.escape(os.path.realpath(b)), os.path.realpath(p)))\n",
 "os.path.commonprefix via alias": "from os.path import commonprefix as cp\ndef f(p,b):\n    return cp([p,b]) == b\n",
 "os.path.relpath.startswith('..')": "import os\ndef f(p,b):\n    return os.path.relpath(p,b).startswith('..')\n",
 "pathlib != comparison": "from pathlib import Path\ndef f(p,b):\n    return Path(p).resolve() != Path(b).resolve()\n",
 "str() wrap equality": "import os\ndef f(p,b):\n    return str(os.path.realpath(p)) == str(os.path.realpath(b))\n",
 "ntpath alias module": "import ntpath as n\ndef f(p,b):\n    return n.normcase(p).startswith(n.normcase(b)+'\\\\')\n",
 "class-body": "import os\nclass C:\n    def m(self,p,b):\n        return os.path.realpath(p).startswith(os.path.realpath(b)+os.sep)\n",
 "comprehension": "import os\ndef f(ps,b):\n    rb=os.path.realpath(b)\n    return [p for p in ps if os.path.realpath(p).startswith(rb + '/')]\n",
 "match via helper name _is_sub": "import os\ndef _is_sub(p,b):\n    return os.path.realpath(p).startswith(os.path.realpath(b)+os.sep)\n",
 "os.sep via variable": "import os\nSEP=os.sep\ndef f(p,b):\n    return os.path.realpath(p).startswith(os.path.realpath(b)+SEP)\n",
 "sep via join literal": "import os\ndef f(p,b):\n    return os.path.realpath(p).startswith(os.path.realpath(b)+'/'.join(['','']))\n",
 "path-call via from-import no alias": "from os.path import realpath\ndef f(p,b):\n    return realpath(p).startswith(realpath(b)+'/')\n",
 "dunder call": "import os\ndef f(p,b):\n    return os.path.realpath(p).__contains__(b)\n",
 "tuple-unpack assign": "import os\ndef f(p,b):\n    a, c = os.path.realpath(p), os.path.realpath(b)\n    return a.startswith(c + '/')\n",
}


def _corpus():
    """ALL of `src/` (derived: `rglob`, both packages), keyed by the path below `src/mokata` (or
    `mokata_emit/...`)."""
    out = {"/".join(p.relative_to(SRC).parts): p.read_text(encoding="utf-8") for p in SRC.rglob("*.py")}
    emit = SRC.parent / "mokata_emit"
    out.update({"mokata_emit/" + "/".join(p.relative_to(emit).parts): p.read_text(encoding="utf-8")
                for p in emit.rglob("*.py")})
    return out


def allow_failures(found, allow):
    """Pure: what is wrong with `found` (grouped violations) against `allow` — unlisted offenders, wrong
    counts, stale entries, empty reasons. [] when the tree has one door."""
    bad = []
    for key, n in sorted(found.items()):
        if key not in allow:
            bad.append(f"unlisted string containment {key} x{n}")
        elif allow[key][0] != n:
            bad.append(f"{key}: {n} offenders, the allowlist says {allow[key][0]}")
    for key, (_n, reason) in sorted(allow.items()):
        if key not in found:
            bad.append(f"stale allowlist entry {key}")
        if not str(reason).strip():
            bad.append(f"allowlist entry {key} has no reason")
    return bad


class OneDoor(unittest.TestCase):
    def test_the_sweep_finds_a_planted_offender_per_idiom(self):
        for idiom, src in _PLANTED.items():
            with self.subTest(idiom=idiom):
                got = {i for _r, _l, _f, i in door_violations({"brand/new_module.py": src})}
                self.assertIn(idiom, got)

    def test_every_verifier_variant_reds(self):
        """M2 (round 2): the class rule catches all 38 of the verifier's variants (round 1: 17)."""
        missed = [k for k, src in _VARIANTS.items() if not door_violations({"brand/new.py": src})]
        self.assertEqual(38, len(_VARIANTS))
        self.assertEqual([], missed)

    def test_each_taint_carrier_reds_on_its_own(self):
        """Round 2: a case per carrier where NOTHING ELSE carries the taint (the verifier's variants also
        carry it through `+ os.sep` or a tainted receiver, so they cannot pin a carrier alone)."""
        cases = {
            "f-string": "import os\ndef f(p, b):\n    rb = os.path.realpath(b)\n    return p.startswith(f'{rb}/')\n",
            "module alias": "import os\nrp = os.path.realpath\ndef f(p, seen):\n    return rp(p) in seen\n",
            "getattr alias": "import os\ng = getattr(os.path, 'realpath')\ndef f(p, seen):\n    return g(p) in seen\n",
            "module scope": "import os\nA, B = 'a', 'b'\nSAME = os.path.realpath(A) == B\n",
        }
        for label, src in cases.items():
            with self.subTest(carrier=label):
                self.assertTrue(door_violations({"brand/new.py": src}), label)

    def test_round3_ruling_classes_red(self):
        """M-r2-2 ruling: the must-red classes from the round-2 verifier's probe (`sweep_variants_r2.py`),
        embedded verbatim, plus one per class it named that the probe had no case for."""
        H = "import os\n"
        must = {
            "param slice": "def inside(child, parent):\n    return child[:len(parent)] == parent\n",
            "os.walk root compare": H + "def f(b):\n    for r, ds, fs in os.walk(b):\n        if r == '/x':\n            return True\n",
            "scandir path compare": H + "def f(b):\n    return [e.path for e in os.scandir(b) if e.path.startswith(b)]\n",
            "fnmatch": H + "import fnmatch\ndef f(p,b):\n    return fnmatch.fnmatch(os.path.realpath(p), os.path.realpath(b) + '/*')\n",
            "fnmatchcase": H + "from fnmatch import fnmatchcase as fm\ndef f(p,b):\n    return fm(os.path.realpath(p), os.path.realpath(b) + '/*')\n",
            "regex compile": H + "import re\ndef f(p,b):\n    rx = re.compile('^' + re.escape(os.path.realpath(b)))\n    return rx.match(os.path.realpath(p)) is not None\n",
            "regex sub": H + "import re\ndef f(p,b):\n    return re.sub('^' + re.escape(os.path.realpath(b)), '', os.path.realpath(p))\n",
            "match literal capture": H + "def f(p,b):\n    r = os.path.realpath(p)\n    match r:\n        case '/etc/passwd':\n            return True\n",
            "match value pattern": H + "class K:\n    ROOT='/x'\ndef f(p):\n    r = os.path.realpath(p)\n    match r:\n        case K.ROOT:\n            return True\n",
            # classes the ruling names that the probe had no case for
            "iterdir": "from pathlib import Path\ndef f(b, x):\n    return [c for c in Path(b).iterdir() if c == x]\n",
            "glob": "import glob\ndef f(b, x):\n    return [g for g in glob.glob(b + '/*') if g.startswith(x)]\n",
            "PurePath.match": "from pathlib import Path\ndef f(p, pat):\n    return Path(p).resolve().match(pat)\n",
            "match on parts": "from pathlib import Path\ndef f(p):\n    match Path(p).resolve().parts:\n        case ('/', 'etc', *_):\n            return True\n",
            "fsencode": H + "def f(p,b):\n    return os.fsencode(os.path.realpath(p)).startswith(os.fsencode(os.path.realpath(b)))\n",
            # the comparator's `len/str/tuple/list` allowance, WITHOUT a slice (the `slice` idiom does not
            # cover it): a parameter path equality spelled through a conversion (integration rerun, M2-16)
            "param str comparator": "def same(a, b):\n    return str(a) == str(b)\n",
            "param tuple(parts) comparator": "def same(a, b):\n    return tuple(a.parts) == tuple(b.parts)\n",
            # a conversion around the slice is the same prefix test (0.0.29 H-b xplat)
            "wrapped slice": "def f(c, p):\n    if list(c[:len(p)]) != p:\n        return None\n    return c\n",
        }
        missed = [k for k, src in must.items() if not door_violations({"brand/new.py": src})]
        self.assertEqual([], missed)

    def test_disclosed_indirection_and_benign_lookups_are_not_findings(self):
        """The ruling's DISCLOSED class (functional indirection: doc 84 CONTAINMENT-SWEEP-FUNCTIONAL-INDIRECTION;
        a reviewer rejects it) and the benign name lookups. Neither reds: the indirection is out of a static
        sweep's reach by ruling, and a lookup is not a decision. Pinned so the rule does not drift into either."""
        H = "import os\n"
        quiet = {
            "dict get": H + "def f(p,d):\n    return d.get(os.path.realpath(p))\n",
            "dict subscript": H + "def f(p,d):\n    return d[os.path.realpath(p)]\n",
            "set intersection": H + "def f(p,l):\n    return {os.path.realpath(p)} & set(l)\n",
            "sorted key": H + "def f(ps):\n    return sorted(ps, key=os.path.realpath)\n",
            "basename eq": H + "def f(p,b):\n    return os.path.basename(p) == b\n",
            "samefile": H + "def f(p,b):\n    return os.path.samefile(p, b)\n",
            "walk name list": H + "def f(b, n):\n    for r, ds, fs in os.walk(b):\n        if n in fs:\n            return True\n",
            "operator.eq (disclosed)": H + "import operator\ndef f(p,b):\n    return operator.eq(os.path.realpath(p), os.path.realpath(b))\n",
        }
        loud = [k for k, src in quiet.items() if door_violations({"brand/new.py": src})]
        self.assertEqual([], loud)

    def test_a_path_token_name_check_is_not_a_finding(self):
        src = ("import os\ndef f(name, rel):\n    return os.sep in name or rel in (os.curdir, '.') "
               "or os.path.basename(rel) == '.git' or rel.endswith(('/', os.sep))\n")
        self.assertEqual([], door_violations({"x.py": src}))

    def test_a_closure_and_a_lambda_are_scopes_with_inherited_taint(self):
        src = ("import os\ndef f(p, b):\n    rb = os.path.realpath(b)\n"
               "    g = lambda x: x == rb\n    def h(x):\n        return x.startswith(rb)\n    return g, h\n")
        got = {(fn, i) for _r, _l, fn, i in door_violations({"x.py": src})}
        self.assertIn(("f.<lambda>", "equality"), got)
        self.assertIn(("f.h", "startswith"), got)

    def test_an_aliased_os_path_import_is_resolved(self):
        src = "from os.path import commonpath as cp\ndef f(p, b):\n    return cp([p, b]) == b\n"
        self.assertIn("commonpath", {i for *_x, i in door_violations({"x.py": src})})

    def test_a_bare_literal_prefix_never_counts(self):
        src = "def f(rel):\n    return rel.startswith('/') or rel.startswith('src/')\n"
        self.assertEqual([], door_violations({"x.py": src}))

    def test_the_door_itself_is_not_swept(self):
        self.assertEqual([], door_violations({"fs_identity.py": _PLANTED["commonpath"]}))

    def test_the_scope_is_all_of_src(self):
        corpus = _corpus()
        self.assertIn("ide/lsp.py", corpus)
        self.assertIn("mokata_emit/__init__.py", corpus)
        self.assertEqual(len(list(SRC.rglob("*.py"))) + len(list((SRC.parent / "mokata_emit").rglob("*.py"))),
                         len(corpus))

    def test_the_tree_has_one_door(self):
        self.assertEqual([], allow_failures(grouped(door_violations(_corpus())), _ALLOW))

    def test_the_allowlist_checker_is_not_vacuous(self):
        found = {("a.py", "f", "equality"): 2, ("b.py", "g", "key"): 1}
        bad = allow_failures(found, {("a.py", "f", "equality"): (1, "r"), ("c.py", "h", "key"): (1, " ")})
        self.assertTrue(any("unlisted" in b and "b.py" in b for b in bad))
        self.assertTrue(any("2 offenders" in b for b in bad))
        self.assertTrue(any("stale" in b and "c.py" in b for b in bad))
        self.assertTrue(any("no reason" in b for b in bad))

    def test_rest_entries_name_their_row(self):
        for key, (_n, reason) in _ALLOW.items():
            if key[0] in ("hook_cli.py", "session_registry.py", "run_resolver.py", "hints.py",
                          "knowledge/crg_client.py", "knowledge/crg_nodes.py"):
                self.assertEqual(REST, reason, key)

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
