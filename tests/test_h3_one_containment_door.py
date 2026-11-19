"""0.0.29 H3 (`CONTAINMENT-STRING-SITES`) — every path containment / equality decision in a file lane H owns is
answered by `fs_identity`.

Business rule (note H §3a/§3d, rule page rev 2): a case-flipped spelling of a folder IS that folder (on a
volume that folds case); on a case-sensitive volume a fold-equal SIBLING is not it on the allow side, and is
refused on the refuse side (the stated cost). Allow side → `require_inside` / `disk_rel`; refuse side →
`refuse_if_inside`; equality → `same_entry`; a loop over a repo-relative name's own components is not a
containment decision. The sweep over all of `src/` lives in `test_c2_containment.OneDoor`.

Each site is driven through its caller, with the spelling the old string test got wrong: the root (or base)
typed in flipped case while the path arrives in the disk's spelling (an absolute link target, a realpath, a
recorded plugin root). Skips are recorded NOT RUN, never green: the flip cells need a case-folding volume;
the sibling cells need the macOS case-sensitive APFS image. No clock, no sleep: the cost cell counts stats.
"""
from __future__ import annotations

import ast
import os
import shutil
import statistics
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
NT = os.name == "nt"


def _write(path: str, text: str = "x\n") -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def _flip(path: str) -> str:
    """`path` with its LAST component's case swapped (`repo` -> `REPO`)."""
    head, tail = os.path.split(path.rstrip("/\\"))
    return os.path.join(head, tail.swapcase())


class _Flip(unittest.TestCase):
    """base/repo (real spelling `self.repo`, flipped spelling `self.flip`); base/repo-oss beside it."""

    def setUp(self):
        base = os.path.realpath(tempfile.mkdtemp(prefix="mk-h3-"))
        self.addCleanup(shutil.rmtree, base, True)
        self.base = base
        self.repo = os.path.join(base, "repo")
        os.makedirs(self.repo)
        self.flip = _flip(self.repo)
        self.sibling = os.path.join(base, "repo-oss")
        os.makedirs(self.sibling)

    def need_fold(self):
        if not F.folds_case(self.base):
            self.skipTest("NOT RUN: this volume does not fold case (the flip cell is a folding-volume row)")


# =========================================================================== allow side
class AllowSites(_Flip):
    def test_lsp_rel_a_disk_spelled_file_under_a_flipped_root_is_inside(self):
        self.need_fold()
        from mokata.ide.lsp import _rel
        f = _write(os.path.join(self.repo, "src", "a.py"))
        self.assertEqual(("src/a.py", False), _rel(self.flip, f))
        self.assertEqual((None, True), _rel(self.flip, _write(os.path.join(self.sibling, "a.py"))))

    def test_proof_real_a_link_with_an_absolute_disk_target_under_a_flipped_root(self):
        self.need_fold()
        from mokata.ide.proof import _Locator
        real = _write(os.path.join(self.repo, "tests", "test_real.py"))
        os.symlink(real, os.path.join(self.repo, "tests", "test_link.py"))
        loc = _Locator(self.flip, self.flip)
        self.assertEqual("tests/test_real.py", loc._real("tests/test_link.py"))
        out = _write(os.path.join(self.sibling, "test_out.py"))
        os.symlink(out, os.path.join(self.repo, "tests", "test_out.py"))
        self.assertEqual("tests/test_out.py", loc._real("tests/test_out.py"))   # outside: as typed

    def test_harness_paths_a_flipped_claude_dir_is_the_harness_dir(self):
        self.need_fold()
        from mokata import harness_paths as H
        home = self.repo
        os.makedirs(os.path.join(home, ".claude", "projects", "slug", "memory"))
        self.assertTrue(H.is_user_harness_config(os.path.join(home, ".CLAUDE", "Settings.json"), home=home))
        self.assertEqual(H.HARNESS_SETTINGS,
                         H.harness_path_kind(os.path.join(home, ".CLAUDE", "Settings.json"), home=home))
        self.assertEqual(H.HARNESS_MEMORY, H.harness_path_kind(
            os.path.join(home, ".Claude", "Projects", "slug", "MEMORY", "m.md"), home=home))
        self.assertIsNone(H.harness_path_kind(os.path.join(self.sibling, "settings.json"), home=home))

    def test_harness_paths_an_ABSENT_harness_dir_is_judged_by_shape(self):
        """0.0.29 H-b xplat (CI 43917243180): a home with NO `.claude` yet (every CI runner). The
        layout is SHAPE-based — the write being judged may be the one that creates the directory — so
        each kind is still named, and the memory carve-out still holds. Fail closed on the missing
        segment: it is matched by its EXACT spelling (no identity exists to fold against), so a
        flipped spelling of an absent `.claude` is no kind at all, and no carve-out."""
        from mokata import harness_paths as H
        home = self.repo
        self.assertFalse(os.path.lexists(os.path.join(home, ".claude")))
        c = lambda *p: os.path.join(home, ".claude", *p)           # noqa: E731
        for path, kind in ((c(), H.HARNESS_STATE), (c("settings.json"), H.HARNESS_SETTINGS),
                           (c("settings.local.json"), H.HARNESS_SETTINGS),
                           (c("projects", "-s", "a.jsonl"), H.HARNESS_TRANSCRIPTS),
                           (c("projects", "-s", "memory", "n.md"), H.HARNESS_MEMORY),
                           (c("plugins", "x.js"), H.HARNESS_PLUGINS), (c("hooks", "x.sh"), H.HARNESS_HOOKS),
                           (c("history.jsonl"), H.HARNESS_STATE)):
            self.assertEqual(kind, H.harness_path_kind(path, home=home), path)
        self.assertEqual(H.HARNESS_SETTINGS, H.harness_path_kind(
            c("projects", "-s", "memory", "..", "..", "..", "settings.json"), home=home))
        for outside in (os.path.join(home, ".claudius", "settings.json"), os.path.join(home, "x", "memory"),
                        os.path.join(home, ".CLAUDE", "projects", "-s", "memory", "n.md"),
                        os.path.join(self.sibling, ".claude", "settings.json")):
            self.assertIsNone(H.harness_path_kind(outside, home=home), outside)
        # a dangling `.claude` link is not an absent directory: no identity, no shape — no kind
        try:
            os.symlink(os.path.join(self.base, "nowhere"), c())
        except (OSError, NotImplementedError):
            return                       # NOT RUN: no symlink privilege (Windows without developer mode)
        self.assertIsNone(H.harness_path_kind(c("projects", "-s", "memory", "n.md"), home=home))

    def test_selfprotect_memory_carve_out_holds_with_no_harness_dir(self):
        """The CALLERS of `harness_path_kind` with the home it reads by default and no `.claude` in it:
        the carve-out admits the memory leaf and only it; the refusal message still names the capability."""
        from unittest import mock
        from pathlib import Path
        from mokata import selfprotect as SP
        home = self.repo
        c = lambda *p: os.path.join(home, ".claude", *p)           # noqa: E731
        with mock.patch.object(Path, "home", classmethod(lambda cls: Path(home))):
            self.assertTrue(SP._is_harness_memory(c("projects", "-s", "memory", "n.md")))
            for p in (c("settings.json"), c("projects", "-s", "a.jsonl"), c("projects", "-s"), c("hooks", "x"),
                      c("plugins", "x"), c(), os.path.join(home, ".CLAUDE", "projects", "-s", "memory", "n.md")):
                self.assertFalse(SP._is_harness_memory(p), p)
            self.assertIn("SETTINGS", SP._harness_capability(c("settings.json")) or "")

    def test_ci_evidence_a_link_to_the_disk_spelling_under_a_flipped_root_links(self):
        self.need_fold()
        from mokata.ci_evidence import LinkContext, path_link
        a = _write(os.path.join(self.repo, "src", "a.py"))
        os.symlink(a, os.path.join(self.repo, "src", "l.py"))
        got = path_link(self.flip, "src/l.py", LinkContext(base=None))
        self.assertNotIn("withheld", got)
        os.symlink(_write(os.path.join(self.sibling, "o.py")), os.path.join(self.repo, "src", "o.py"))
        self.assertIn("resolves outside", path_link(self.flip, "src/o.py", LinkContext(base=None)))

    def test_mcp_guard_path_a_link_to_a_flipped_spelling_is_inside(self):
        self.need_fold()
        from mokata.mcp.validation import ValidationError, guard_path
        os.makedirs(os.path.join(self.repo, "src"))
        os.symlink(os.path.join(self.flip, "src"), os.path.join(self.repo, "lnk"))
        os.symlink(self.sibling, os.path.join(self.repo, "out"))
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)
        os.chdir(self.repo)
        self.assertEqual("lnk", guard_path("lnk"))
        with self.assertRaises(ValidationError):
            guard_path("out")

    def test_grounding_a_citation_through_a_flipped_link_is_inside(self):
        self.need_fold()
        from mokata.memory import grounding as G
        _write(os.path.join(self.repo, "src", "a.py"))
        os.symlink(os.path.join(self.flip, "src"), os.path.join(self.repo, "lnk"))
        os.symlink(self.sibling, os.path.join(self.repo, "out"))
        cite = lambda t: G.Citation(kind="PATH", raw=t, target=t, source="value")  # noqa: E731
        self.assertTrue(G._check_path(cite("lnk/a.py"), self.repo).grounded)
        self.assertFalse(G._check_path(cite("out"), self.repo).grounded)

    def test_packaging_a_flipped_root_answers_in_root_and_the_oss_sibling_does_not(self):
        self.need_fold()
        from mokata import packaging as P
        pkg = _write(os.path.join(self.repo, "src", "mokata", "__init__.py"))
        self.assertEqual(P.ANSWERED_IN_ROOT, P.answering_package(self.flip, pkg, "0").state)
        oss = _write(os.path.join(self.sibling, "src", "mokata", "__init__.py"))
        self.assertEqual(P.ANSWERED_OUT_OF_ROOT, P.answering_package(self.repo, oss, "0").state)

    def test_version_a_flipped_plugin_root_is_the_plugin(self):
        self.need_fold()
        from mokata import version as V
        pkg = _write(os.path.join(self.repo, "src", "mokata", "__init__.py"))
        with mock.patch.object(V, "read_plugin_root", return_value=self.flip):
            self.assertEqual("plugin", V.detect_install_method(package_file=pkg))
        with mock.patch.object(V, "read_plugin_root", return_value=self.sibling):
            self.assertNotEqual("plugin", V.detect_install_method(package_file=pkg))

    def test_selfprotect_containment_a_flipped_root_holds_the_disk_spelled_target(self):
        self.need_fold()
        from mokata import selfprotect as S
        pkg = os.path.join(self.base, "site", "mokata")
        os.makedirs(pkg)
        target = os.path.join(self.repo, "a.txt")
        with mock.patch.object(S, "_temp_roots", return_value=()):
            self.assertTrue(S.check_target(target, workspace_root=self.flip, package_root=pkg).allowed)
            self.assertFalse(S.check_target(os.path.join(self.sibling, "a.txt"), workspace_root=self.flip,
                                            package_root=pkg).allowed)

    def test_selfprotect_the_dev_checkout_carve_is_identity(self):
        self.need_fold()
        from mokata import selfprotect as S
        pkg = os.path.join(self.repo, "src", "mokata")
        os.makedirs(pkg)
        target = os.path.join(pkg, "x.py")
        self.assertTrue(S.check_target(target, workspace_root=self.flip, package_root=pkg).allowed)


# =========================================================================== refuse side
class RefuseSites(_Flip):
    def test_selfprotect_rule_b_a_flipped_package_spelling_is_refused(self):
        self.need_fold()
        from mokata import selfprotect as S
        pkg = os.path.join(self.base, "site", "mokata")
        os.makedirs(pkg)
        flipped = os.path.join(self.base, "site", "MOKATA", "x.py")
        out = S.check_target(flipped, workspace_root=self.repo, package_root=pkg)
        self.assertFalse(out.allowed)
        self.assertIn("INSTALLED package", out.reason)

    def test_install_isolation_a_venv_under_a_flipped_root_warns(self):
        self.need_fold()
        from mokata import install_isolation as I
        venv = os.path.join(self.repo, ".venv")
        os.makedirs(venv)
        self.assertIsNotNone(I.in_project_env(self.flip, prefix=venv))
        self.assertIsNone(I.in_project_env(self.repo, prefix=self.sibling))

    def test_session_worktrees_a_flipped_target_in_the_main_checkout_is_refused(self):
        self.need_fold()
        from mokata import session_worktrees as SW
        pol = mock.Mock(always=True)
        with mock.patch.object(SW, "effective_policy", return_value=pol), \
                mock.patch.object(SW, "canonical_repo_root", return_value=self.repo), \
                mock.patch.object(SW, "worktree_toplevel", return_value=None), \
                mock.patch.object(SW, "enter_instruction", return_value="enter"):
            got = SW.entry_refusal(self.repo, self.repo, "sid", [os.path.join(self.flip, "a.py")])
            self.assertIn("BLOCKED", got)
            self.assertEqual("", SW.entry_refusal(self.repo, self.repo, "sid",
                                                  [os.path.join(self.sibling, "a.py")]))

    def test_bash_lane_a_flipped_spelling_of_the_state_dir_is_its_record(self):
        self.need_fold()
        from mokata import bash_lane as B
        g = os.path.join(self.repo, ".mokata", "temp_local")
        os.makedirs(g)
        flipped = os.path.join(self.flip, ".MOKATA", "temp_local", "x")
        self.assertIn("writes inside it", B.own_record_hits(self.repo, self.repo, g, [flipped], "", None))
        self.assertIn("removes or moves", B.own_record_hits(self.repo, self.repo, g, [],
                                                             "rm -rf " + os.path.join(self.flip, ".MOKATA"),
                                                             None))
        self.assertEqual("", B.own_record_hits(self.repo, self.repo, g,
                                               [os.path.join(self.sibling, "x")], "", None))


# =========================================================================== unknown vs absent identity
class UnknownIdentity(_Flip):
    """0.0.29 H-b xplat (CI 43917243180): ONE meaning for an identity the disk does not give. ABSENT (the
    entry provably does not exist) is not UNKNOWN (it could not be read). Windows read the store key `m:k`
    as drive M:, where every entry of the chain is absent; the refuse question took that for undecidable,
    refused, and the memory write was BLOCKED as self-modification (`ms_s3_ledger_chain` KeyError)."""

    def test_a_chain_that_is_wholly_ABSENT_is_outside_on_the_refuse_question(self):
        from unittest import mock
        with mock.patch.object(F, "_stat", side_effect=FileNotFoundError(2, "absent")):
            self.assertIs(False, F._identity_walk(os.path.join(self.base, "x", "k"), (1, 2)))
        nodrive = OSError(22, "no such volume")
        nodrive.winerror = 3                                   # ERROR_PATH_NOT_FOUND (`M:\` with no M:)
        with mock.patch.object(F, "_stat", side_effect=nodrive):
            self.assertIs(False, F._identity_walk(os.path.join(self.base, "x", "k"), (1, 2)))

    def test_an_UNREADABLE_chain_stays_undecidable_and_the_refuse_question_refuses(self):
        from unittest import mock
        with mock.patch.object(F, "_stat", side_effect=PermissionError(13, "denied")):
            self.assertIsNone(F._identity_walk(os.path.join(self.base, "x", "k"), (1, 2)))
        real = F._stat
        target = os.path.join(self.sibling, "k")

        def denied_below_base(path):
            if path in (target, self.sibling):
                raise PermissionError(13, "denied")
            if path in (self.base,) or len(path) < len(self.base):
                raise FileNotFoundError(2, "absent")
            return real(path)
        with mock.patch.object(F, "_stat", denied_below_base):
            self.assertTrue(F.refuse_if_inside(target, self.repo))   # one unreadable entry: refuse

    def test_an_UNKNOWN_base_is_outside_on_the_allow_question(self):
        from unittest import mock
        with mock.patch.object(F, "_stat", side_effect=PermissionError(13, "denied")):
            self.assertFalse(F.require_inside(os.path.join(self.repo, "a"), self.repo))

    def test_a_store_KEY_with_a_drive_letter_shape_is_a_label_not_a_drive(self):
        """`WriteRequest("memory", "m:k")`: self-protect judges it like every other label, and never
        blocks it as a write into mokata's own package."""
        from mokata import selfprotect as SP
        for key in ("m:k", "c:k", "M:key", "z:"):
            got = SP.check_target(key)
            self.assertTrue(got.allowed, (key, got))
        if os.name == "nt":
            cwd = os.getcwd()
            self.assertTrue(F.same_entry(os.path.dirname(SP.resolve_target("m:k")), cwd),
                            SP.resolve_target("m:k"))
            # with a base (the hook lane) a drive-relative name is NOT a label: unresolvable
            self.assertIsNone(SP.resolve_target("m:k", base_dir=self.repo))

    def test_the_hook_lane_REFUSES_a_drive_relative_name(self):
        """Verifier (xplat, Medium): `C:evil.txt` with a known base. The OS opens it against drive C:'s OWN
        cwd — not the base — which may be outside the root when the workspace is on another drive. Read
        as a label under the base, rule (c) called it inside. With a base the name is unreadable, and the
        verdict refuses (fail closed) — never the "could not be resolved, not judged" allow."""
        from unittest import mock
        from mokata import selfprotect as SP
        with mock.patch.object(SP, "_NT", True):
            for name in ("C:evil.txt", "c:x", "Z:a.py"):
                got = SP.check_target(name, workspace_root=self.repo, base_dir=self.repo)
                self.assertFalse(got.allowed, (name, got))
                self.assertEqual(SP.RULE_OUT_OF_ROOT, got.rule, (name, got))
                # and the resolver itself never reads it as a label under the base (unresolvable)
                self.assertIsNone(SP.resolve_target(name, base_dir=self.repo), name)
            got = SP.check_target("m:k")                       # no base: still a label, still allowed
            self.assertTrue(got.allowed, got)
            for ok in (os.path.join(self.repo, "a.py"), "a.py"):   # an ordinary name is unaffected
                self.assertTrue(SP.check_target(ok, workspace_root=self.repo, base_dir=self.repo).allowed, ok)
        self.assertTrue(SP.check_target("C:evil.txt", workspace_root=self.repo, base_dir=self.repo).allowed
                        if os.name != "nt" else True)            # POSIX: `C:evil.txt` is a plain name

    def test_a_volume_that_is_NOT_READY_is_unknown_not_absent(self):
        """Verifier (xplat, Low b): device not ready / network path or name not found say nothing about
        whether the entry exists — UNREADABLE, so the refuse question stays undecidable (refuses)."""
        from unittest import mock
        for code in (21, 53, 67):
            err = OSError(5, "not ready")
            err.winerror = code
            with mock.patch.object(F, "_stat", side_effect=err):
                self.assertIsNone(F._identity_walk(os.path.join(self.base, "x"), (1, 2)), code)
        for code in (2, 3, 15):
            err = OSError(2, "absent")
            err.winerror = code
            with mock.patch.object(F, "_stat", side_effect=err):
                self.assertIs(False, F._identity_walk(os.path.join(self.base, "x"), (1, 2)), code)

    @unittest.skipUnless(os.name == "nt", "NOT RUN: a junction is a Windows reparse point")
    def test_a_DANGLING_JUNCTION_base_is_unknown_not_absent(self):
        """Verifier (xplat, Low a): `islink()` is False for a junction, so a dangling junction looked like a
        base not created yet. It names nothing whose identity we know: outside (allow), no kind."""
        import _winapi
        from mokata import harness_paths as H
        home = self.repo
        target = os.path.join(self.base, "gone")
        os.makedirs(target)
        _winapi.CreateJunction(target, os.path.join(home, ".claude"))
        os.rmdir(target)
        c = os.path.join(home, ".claude", "projects", "-s", "memory", "n.md")
        self.assertIsNone(H.harness_path_kind(c, home=home))
        self.assertFalse(F.require_inside(c, os.path.join(home, ".claude")))


# =========================================================================== equality
class EqualitySites(_Flip):
    def test_same_entry_agrees_with_samestat_on_a_flip(self):
        self.need_fold()
        self.assertTrue(F.same_entry(self.flip, self.repo))
        self.assertTrue(os.path.samestat(os.stat(self.flip), os.stat(self.repo)))

    def test_install_isolation_shadowed_a_flipped_own_scripts_is_this_install(self):
        self.need_fold()
        from mokata import install_isolation as I
        exe = _write(os.path.join(self.repo, "bin", "mokata"))
        got = I.shadowed(which=lambda _n: exe, own_scripts=os.path.join(self.flip, "bin"),
                         own_version="1.0", version_of=lambda _e: "0.9")
        self.assertIsNone(got)
        other = I.shadowed(which=lambda _n: exe, own_scripts=os.path.join(self.sibling, "bin"),
                           own_version="1.0", version_of=lambda _e: "0.9")
        self.assertIsNotNone(other)

    def test_repo_identity_a_flipped_root_is_main(self):
        self.need_fold()
        from mokata import repo_identity as R
        self.assertEqual("main", R.worktree_label(self.flip))
        self.assertEqual(R.tree_temp_local_dir(self.repo), R.tree_temp_local_dir(self.repo))

    def test_plans_saved_file_under_a_flipped_base_is_the_file(self):
        """No RED on base, said: the dirname of a NON-link file under `base` is `base`, so the old
        `realpath != realpath` could not diverge here. The site moved for the sweep; this pins behaviour."""
        self.need_fold()
        from mokata.ide import plans as PL
        d = os.path.join(self.repo, "plans")
        f = _write(os.path.join(d, "p1.md"))
        with mock.patch.object(PL, "_saved_dirs", return_value={"x": os.path.join(self.flip, "plans")}):
            got = PL._saved_file(self.repo, "x", "p1")
        self.assertIsNotNone(got)
        self.assertTrue(F.same_entry(got, f))
        os.symlink(f, os.path.join(d, "p2.md"))
        with mock.patch.object(PL, "_saved_dirs", return_value={"x": d}):
            self.assertIsNone(PL._saved_file(self.repo, "x", "p2"))


    def _git_repo_with_a_worktree(self):
        def git(*a, cwd=None):
            subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=cwd or self.repo,
                           check=True, capture_output=True)
        git("init", "-q", "-b", "main")
        _write(os.path.join(self.repo, "f.txt"))
        git("add", "-A")
        git("commit", "-q", "-m", "c")
        wt = os.path.join(self.base, "wt1")
        git("worktree", "add", "-q", "-b", "w1", wt)
        return wt

    def test_worktree_ops_a_flipped_root_never_lists_the_main_checkout(self):
        """M3 (round 2): `rows` asked of the flipped spelling of the main checkout. The old `_real` key
        (normcase is the identity on POSIX) said `.../repo` != `.../REPO`, so the MAIN checkout was listed
        as a linked worktree — and `plan_delete` then planned to delete it."""
        self.need_fold()
        from mokata import worktree_ops as W
        wt = self._git_repo_with_a_worktree()
        got = W.rows(self.flip)
        self.assertTrue(got["ok"], got)
        listed = [r["path"] for r in got["worktrees"]]
        self.assertFalse(any(F.same_entry(p, self.repo) for p in listed), listed)
        self.assertEqual(1, len(listed), listed)
        self.assertTrue(F.same_entry(listed[0], wt))
        self.assertFalse(W.plan_delete(self.flip, self.repo)["ok"])
        self.assertFalse(W.plan_delete(self.flip, self.flip)["ok"])

    def test_worktree_ops_claude_managed_under_a_flipped_root(self):
        """Round 3 (verifier V5): a worktree under `<main>/.claude/worktrees` is Claude-managed when the
        repo is asked of by its flipped spelling (identity, not the string `main + /.claude/worktrees`)."""
        self.need_fold()
        from mokata import worktree_ops as W
        self._git_repo_with_a_worktree()
        cw = os.path.join(self.repo, ".claude", "worktrees", "cw1")
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "worktree", "add", "-q", "-b", "cw1",
                        cw], cwd=self.repo, check=True, capture_output=True)
        rows = {os.path.basename(r["path"]): r for r in W.rows(self.flip)["worktrees"]}
        self.assertTrue(rows["cw1"]["claude_managed"], rows)
        self.assertFalse(rows["wt1"]["claude_managed"], rows)

    def test_worktree_ops_a_binding_recorded_under_a_flipped_spelling_is_that_worktrees(self):
        """Round 3 (verifier V4): `_bound` matches a binding by identity. A binding whose recorded path is the
        case-flipped spelling of the worktree is that worktree's; realpath does not fold case, so a realpath
        string compare would drop the session."""
        self.need_fold()
        from unittest import mock
        from mokata import worktree_ops as W
        wt = self._git_repo_with_a_worktree()
        rec = {"s-flip": {"session": "s-flip", "path": _flip(wt), "last_seen": 1.0}}
        with mock.patch.object(W, "bindings", lambda root: rec):
            rows = W.rows(self.repo)["worktrees"]
        self.assertEqual(["s-flip"], [r["session"] for r in rows], rows)

    def test_worktree_ops_clean_accepts_a_flipped_spelling_of_the_approved_path(self):
        """Round 3 (verifier V6): `clean` matches the approved path by identity; the case-flipped spelling of
        a merged-and-clean worktree removes it (a realpath string compare would skip it)."""
        self.need_fold()
        from mokata import worktree_ops as W
        from mokata import session_worktrees as SW
        wt = self._git_repo_with_a_worktree()
        SW.bind(self.repo, "s-clean", wt)
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        ident = ["-c", "user.email=t@t", "-c", "user.name=t"]
        subprocess.run(["git", *ident, "commit", "-q", "--allow-empty", "-m", "w"], cwd=wt, env=env, check=True)
        subprocess.run(["git", *ident, "merge", "-q", "w1"], cwd=self.repo, env=env, check=True)
        self.assertTrue(W.plan_clean(self.repo)["ok"], W.rows(self.repo))
        got = W.clean(self.repo, [_flip(wt)])
        self.assertEqual({"removed": [_flip(wt)], "skipped": []}, got, got)
        self.assertFalse(os.path.isdir(wt))

    def test_worktree_ops_find_a_flipped_worktree_path_is_that_worktree(self):
        """M3 (round 2): `find` by the flipped spelling of a LINKED worktree finds it (identity)."""
        self.need_fold()
        from mokata import worktree_ops as W
        wt = self._git_repo_with_a_worktree()
        row = W.find(self.repo, _flip(wt))
        self.assertIsNotNone(row)
        self.assertTrue(F.same_entry(row["path"], wt))
        self.assertIsNone(W.find(self.repo, self.sibling))


# =========================================================================== component loops
class ComponentLoops(_Flip):
    def test_under_python_env_walks_the_names_own_components(self):
        from mokata.repo_walk import under_python_env
        _write(os.path.join(self.repo, "venv", "pyvenv.cfg"))
        self.assertTrue(under_python_env(self.repo, "venv/lib/python3.12/x.py"))
        self.assertTrue(under_python_env(self.repo, "src/../venv/lib/x.py"))
        self.assertFalse(under_python_env(self.repo, "../repo/venv/lib/x.py"))
        # a climbing name names NOTHING below the root — not the root's own `venv/` it would re-land on
        self.assertFalse(under_python_env(self.repo, "../venv/lib/x.py"))
        self.assertFalse(under_python_env(self.repo, "src/../../venv/lib/x.py"))
        self.assertFalse(under_python_env(self.repo, os.path.join(self.repo, "venv", "x.py")))
        self.assertFalse(under_python_env(self.repo, "venv"))

    def test_nearest_package_dir_never_climbs_and_asks_the_disk_for_absolute_paths(self):
        from mokata.stacks import nearest_package_dir
        _write(os.path.join(self.repo, "web", "package.json"), "{}")
        _write(os.path.join(self.base, "package.json"), "{}")             # ABOVE the root: never read
        self.assertEqual(os.path.join(self.repo, "web"), nearest_package_dir(self.repo, "web/src/a.ts"))
        self.assertIsNone(nearest_package_dir(self.repo, "src/a.ts"))
        self.assertIsNone(nearest_package_dir(self.repo, "../x/a.ts"))
        self.assertIsNone(nearest_package_dir(self.repo, "../web/src/a.ts"))
        os.symlink(self.sibling, os.path.join(self.repo, "out"))
        _write(os.path.join(self.sibling, "package.json"), "{}")
        self.assertIsNone(nearest_package_dir(self.repo, os.path.join(self.repo, "out", "a.ts")))
        if F.folds_case(self.base):
            got = nearest_package_dir(self.flip, os.path.join(self.repo, "web", "src", "a.ts"))
            self.assertIsNotNone(got)
            self.assertTrue(F.same_entry(got, os.path.join(self.repo, "web")))


# =========================================================================== stamp_target
class StampTarget(_Flip):
    def test_a_test_file_that_is_a_link_into_the_store_is_outside(self):
        from mokata.test_ids import OUTSIDE, stamp_target
        cfg = _write(os.path.join(self.repo, ".git", "config"))
        try:
            os.symlink(cfg, os.path.join(self.repo, "test_cfg.py"))
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"NOT RUN: no symlink privilege here ({exc})")
        self.assertEqual(OUTSIDE, stamp_target(self.repo, "test_cfg.py"))
        _write(os.path.join(self.repo, "tests", "test_ok.py"))
        self.assertEqual("tests/test_ok.py", stamp_target(self.repo, "tests/test_ok.py"))


# =========================================================================== the polarity table
#: `(file, function) -> the fs_identity predicate it must call` (attack H3-4: read from the AST, so a planted
#: flip of polarity reds; the note §3d classification).
POLARITY = {
    ("ide/lsp.py", "_rel"): "disk_rel",
    ("ide/proof.py", "_real"): "disk_rel",
    ("harness_paths.py", "_parts_below"): "require_inside",
    ("ci_evidence.py", "path_link"): "require_inside",
    ("selfprotect.py", "check_target"): "require_inside",
    ("packaging.py", "answering_package"): "require_inside",
    ("memory/grounding.py", "_resolve_beneath"): "require_inside",
    ("mcp/validation.py", "guard_path"): "require_inside",
    ("version.py", "detect_install_method"): "require_inside",
    ("install_isolation.py", "in_project_env"): "refuse_if_inside",
    ("session_worktrees.py", "entry_refusal"): "refuse_if_inside",
    ("bash_lane.py", "own_record_hits"): "refuse_if_inside",
    ("install_isolation.py", "shadowed"): "same_entry",
    ("session_worktrees.py", "bind"): "same_entry",
    ("tree_scope.py", "scope_for"): "same_entry",
    ("tree_scope.py", "code_tree_for"): "same_entry",
    ("repo_identity.py", "resolve_mokata_root"): "same_entry",
    ("repo_identity.py", "canonical_mokata_dir"): "same_entry",
    ("repo_identity.py", "worktree_label"): "same_entry",
    ("repo_identity.py", "worktree_toplevel"): "same_entry",
    ("repo_identity.py", "tree_temp_local_dir"): "same_entry",
    ("ide/source.py", "code_tree"): "same_entry",
    ("ide/plans.py", "_saved_file"): "same_entry",
    ("stacks.py", "nearest_package_dir"): "disk_rel",
}
#: The refuse-side predicate must never appear at an allow site, nor the allow one at a refuse site.
_WRONG = {"require_inside": "refuse_if_inside", "refuse_if_inside": "require_inside", "disk_rel": None,
          "same_entry": None}
#: selfprotect asks BOTH questions (rule (b) refuses; the carve-out / containment / temp allow).
_BOTH = {("selfprotect.py", "check_target")}


def calls_in(rel: str, func: str) -> list:
    tree = ast.parse((SRC / rel).read_text(encoding="utf-8"))
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == func:
            out = []
            for c in ast.walk(n):
                if isinstance(c, ast.Call):
                    f = c.func
                    out.append(f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", ""))
            return out
    raise AssertionError(f"{rel}: no function {func}")


class Polarity(unittest.TestCase):
    def test_each_site_asks_its_question(self):
        for (rel, func), want in POLARITY.items():
            with self.subTest(site=f"{rel}:{func}"):
                got = calls_in(rel, func)
                self.assertIn(want, got)
                wrong = _WRONG[want]
                if wrong and (rel, func) not in _BOTH:
                    self.assertNotIn(wrong, got, f"{rel}:{func} asks the other side's question")

    def test_selfprotect_rule_b_refuses_and_the_carve_allows(self):
        src = (SRC / "selfprotect.py").read_text(encoding="utf-8")
        self.assertIn("refuse_if_inside(resolved, pkg) and not (root and require_inside(pkg, root))", src)
        self.assertIn("not require_inside(resolved, root)", src)
        self.assertIn("require_inside(resolved, t) for t in _temp_roots()", src)

    def test_the_deleted_helpers_stay_deleted(self):
        for rel, names in {"selfprotect.py": ("_within", "_norm"), "session_worktrees.py": ("_inside",),
                           "packaging.py": ("_within",), "install_isolation.py": ("_is_within", "_norm"),
                           "memory/grounding.py": ("_inside",)}.items():
            tree = ast.parse((SRC / rel).read_text(encoding="utf-8"))
            defs = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
            for name in names:
                self.assertNotIn(name, defs, f"{rel}: {name}")


# =========================================================================== the PreToolUse cost
def _depth(path: str) -> int:
    return len([c for c in path.replace("\\", "/").split("/") if c])


class PreToolUseStatBudget(unittest.TestCase):
    """selfprotect and `bash_lane` now ask the disk. Graded by COUNT (stats per path per predicate call ≤
    2 × depth + 2, note §5); the time is reported (H-Q1: a string pre-check is taken only if the p50 cost is
    over 1 ms), never gated."""

    TOKENS = 2000

    def setUp(self):
        base = os.path.realpath(tempfile.mkdtemp(prefix="mk-h3c-"))
        self.addCleanup(shutil.rmtree, base, True)
        self.repo = os.path.join(base, "repo")
        self.guard = os.path.join(self.repo, ".mokata", "temp_local")
        os.makedirs(self.guard)
        self.paths = [os.path.join(self.repo, "d%d" % (i % 7), "f%d.txt" % i) for i in range(self.TOKENS)]

    def _counted(self, fn):
        n = [0]
        real_stat, real_lstat = os.stat, os.lstat

        def st(*a, **k):
            n[0] += 1
            return real_stat(*a, **k)

        def lst(*a, **k):
            n[0] += 1
            return real_lstat(*a, **k)

        with mock.patch("os.stat", st), mock.patch("os.lstat", lst):
            fn()
        return n[0]

    def test_bash_lane_stats_per_path_are_bounded_by_depth(self):
        from mokata import bash_lane as B
        depth = _depth(self.paths[0])
        cmd = "rm " + " ".join(self.paths)
        count = self._counted(lambda: B.own_record_hits(self.repo, self.repo, self.guard, [], cmd, None))
        # per operand: its own resolution in `_expand` (depth), then two predicate calls (`ab` in `g`,
        # `g` in `ab`), each ≤ (2 × depth + 2) for the path plus the base's own resolution (depth_g + 1)
        per = depth + 2 * ((2 * depth + 2) + (_depth(self.guard) + 1))
        self.assertLessEqual(count, self.TOKENS * per,
                             f"{count} stats for {self.TOKENS} operands at depth {depth}")

    def test_selfprotect_stats_per_write_are_bounded_by_depth(self):
        from mokata import selfprotect as S
        pkg = os.path.join(os.path.dirname(self.repo), "site", "mokata")
        os.makedirs(pkg)
        depth = _depth(self.paths[0])
        calls = 2 + len(S._temp_roots())
        base = max([_depth(pkg), _depth(self.repo)] + [_depth(t) for t in S._temp_roots()])
        count = self._counted(lambda: [S.check_target(p, workspace_root=self.repo, package_root=pkg)
                                       for p in self.paths])
        # per write: the target's own resolution (depth), then ≤ `calls` predicate calls, each ≤
        # (2 × depth + 2) for the path plus the base's own resolution (≤ base + 1)
        per = depth + calls * ((2 * depth + 2) + (base + 1))
        self.assertLessEqual(count, self.TOKENS * per,
                             f"{count} stats for {self.TOKENS} writes at depth {depth}")

    def test_report_the_p50(self):
        """Reported, never gated (H-Q1)."""
        from mokata import bash_lane as B
        from mokata import selfprotect as S
        pkg = os.path.join(os.path.dirname(self.repo), "site", "mokata")
        os.makedirs(pkg, exist_ok=True)
        one = self.paths[0]
        sp, bl = [], []
        for _ in range(1000):
            t0 = time.perf_counter()
            S.check_target(one, workspace_root=self.repo, package_root=pkg)
            sp.append(time.perf_counter() - t0)
            t0 = time.perf_counter()
            B.own_record_hits(self.repo, self.repo, self.guard, [one], "", None)
            bl.append(time.perf_counter() - t0)
        sys.stderr.write("\n[H3 PreToolUse p50] selfprotect.check_target %.1f us, bash_lane.own_record_hits "
                         "%.1f us (one path, 1,000 runs)\n" % (statistics.median(sp) * 1e6,
                                                               statistics.median(bl) * 1e6))


# =========================================================================== a REAL case-sensitive volume
@unittest.skipUnless(sys.platform == "darwin", "the case-sensitive APFS image is the macOS cell")
class CaseSensitiveSiblings(unittest.TestCase):
    """`REPO` beside `repo` on a case-sensitive image is ANOTHER directory: the allow sites refuse it; the
    refuse sites refuse it too (the stated cost)."""

    @classmethod
    def setUpClass(cls):
        t = tempfile.mkdtemp(prefix="mk-h3cs-")
        cls.addClassCleanup(shutil.rmtree, t, True)
        img, mnt = os.path.join(t, "cs.dmg"), os.path.join(t, "mnt")
        os.makedirs(mnt)
        hd = shutil.which("hdiutil") or "/usr/bin/hdiutil"
        subprocess.run([hd, "create", "-size", "20m", "-fs", "Case-sensitive APFS", "-volname", "mkh3",
                        img], check=True, capture_output=True)
        subprocess.run([hd, "attach", "-nobrowse", "-mountpoint", mnt, img], check=True, capture_output=True)
        cls.addClassCleanup(subprocess.run, [hd, "detach", "-force", mnt], capture_output=True)
        cls.mnt = os.path.realpath(mnt)
        cls.repo, cls.other = os.path.join(cls.mnt, "repo"), os.path.join(cls.mnt, "REPO")
        os.makedirs(os.path.join(cls.repo, "src"))
        os.makedirs(os.path.join(cls.other, "src"))

    def test_the_volume_is_case_sensitive(self):
        self.assertFalse(F.folds_case(self.mnt))
        self.assertFalse(F.same_entry(self.repo, self.other))

    def test_allow_sites_refuse_the_sibling(self):
        from mokata import packaging as P
        from mokata.ide.lsp import _rel
        f = _write(os.path.join(self.other, "src", "a.py"))
        self.assertEqual((None, True), _rel(self.repo, f))
        self.assertEqual(P.ANSWERED_OUT_OF_ROOT, P.answering_package(self.repo, f, "0").state)
        from mokata import selfprotect as S
        pkg = os.path.join(self.mnt, "site", "mokata")
        os.makedirs(pkg, exist_ok=True)
        with mock.patch.object(S, "_temp_roots", return_value=()):
            self.assertFalse(S.check_target(f, workspace_root=self.repo, package_root=pkg).allowed)

    def test_refuse_sites_refuse_the_sibling(self):
        from mokata import install_isolation as I
        venv = os.path.join(self.other, ".venv")
        os.makedirs(venv, exist_ok=True)
        self.assertIsNotNone(I.in_project_env(self.repo, prefix=venv))

    def test_equality_sites_tell_them_apart(self):
        from mokata import install_isolation as I
        exe = _write(os.path.join(self.other, "bin", "mokata"))
        self.assertIsNotNone(I.shadowed(which=lambda _n: exe, own_scripts=os.path.join(self.repo, "bin"),
                                        own_version="1.0", version_of=lambda _e: "0.9"))


if __name__ == "__main__":
    unittest.main()
