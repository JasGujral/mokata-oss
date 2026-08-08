"""The public mirror must not carry a DIFFERENT repo's source — pinned by running the script.

WHERE THIS CAME FROM. `sync-public.sh` excludes `.claude/` by NAME, which is why the 0.0.17
stage-20 worktree incident was not also a distribution incident. But the name is the control: a
nested checkout at any other path — a vendored dependency, a submodule-shaped clone, a worktree
placed at `worktrees/x` — is rsynced into the public checkout and then committed by `git add -A`.
And the hard guard could not catch it, because the guard only ever tests the paths named in
`INTERNAL_PATHS`; a whole foreign checkout arriving as CONTENT matches nothing in that array.

So this is the same structural rule as the walkers, on the distribution surface: a directory
carrying a `.git` entry is a different checkout, it is excluded from the mirror, and its arrival
in the public checkout is a hard-guard condition IN ITS OWN RIGHT rather than a lucky match
against a list of internal names.

WHY THIS RUNS THE REAL SCRIPT. Every other pin on `sync-public.sh` is textual — it reads the
file and asserts a path appears in both controls. That is the right shape for "this exclude did
not get deleted" and the wrong shape for a rule with two moving parts (an exclude computed at
run time, and a guard that walks the destination). A textual pin here would assert the code I
happened to write, not the behaviour. So the script is copied into a synthetic source tree —
`SRC` is derived from the script's own location, which is what makes that possible — and run for
real against a throwaway git checkout.

Requires bash, rsync and git; skipped without them. Nothing outside temp dirs is touched, and
the real mokata-oss checkout is never involved.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata.repo_walk import CHECKOUT_MARKER

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SYNC_SH = os.path.join(_REPO, "scripts", "sync-public.sh")


# --------------------------------------------------------------- the drift rule, as a FUNCTION
# Doc 85 §7i: a gate that both DISCOVERS its corpus and JUDGES it cannot be graded, because the
# tree it walks has no offender in it. So the rule is a pure function over a SUPPLIED script, and
# `test_the_drift_check_can_actually_fire` below feeds it planted ones.
#
# The 0.0.17 audit found the previous form — `assertIn(f"-name {CHECKOUT_MARKER}", code)` over the
# whole file — unable to convict the drift it names, for two independent reasons:
#
#   * it is satisfied by ANY ONE occurrence. The script expresses the marker TWICE (the exclude
#     computation at the top, the destination guard at the bottom), and drifting either one alone
#     left the pin green because the other still spelled `.git`;
#   * it is a SUBSTRING test. `-name .gitdir` contains `-name .git`, so drifting BOTH arms to a
#     superstring left it green as well.
#
# Both were confirmed by planting the drift and watching the pin stay green while the behavioural
# pins above went red. The rule below is per-arm and exact.

# Each boundary walk in the script has the same shape: a `while read` loop fed by a process
# substitution running `find … -name <marker> …`. Keying on that shape rather than on the token
# means an unrelated `find -name '*.pyc'` cannot be mistaken for a boundary walk, and a boundary
# walk cannot hide from the sweep by being spelled with a different marker — which is the whole
# failure being guarded against.
_BOUNDARY_FIND = re.compile(r"done\s*<\s*<\(\s*find\b[^)]*?-name\s+(\S+)")

# Both arms, named — so a DROPPED arm is a reportable finding rather than a smaller corpus that
# reads as "nothing wrong". They are asserted by count, and the count is explained here.
BOUNDARY_WALKS = 2   # (1) the exclude computation over SRC · (2) the guard over DEST


def _code_lines(script_text):
    """The script with whole-line comments removed. The file has to be able to DESCRIBE the
    marker in prose (it does, at length) without that prose counting as a boundary walk."""
    return [(n, ln) for n, ln in enumerate(script_text.splitlines(), 1)
            if not ln.lstrip().startswith("#")]


def boundary_find_sites(script_text):
    """`[(lineno, marker_token)]` for every checkout-boundary walk in `script_text`."""
    return [(n, m.group(1)) for n, ln in _code_lines(script_text)
            for m in [_BOUNDARY_FIND.search(ln)] if m]


def marker_drift(script_text, marker):
    """`[(lineno, token)]` for every boundary walk that searches for something other than
    `marker`. EXACT comparison, never containment: `.gitdir` is a different marker that happens
    to start with the right letters, and treating it as a match is how a drifted arm passes."""
    sites = boundary_find_sites(script_text)
    offenders = [(n, tok) for n, tok in sites if tok != marker]
    return offenders

_MISSING = [t for t in ("bash", "rsync", "git") if shutil.which(t) is None]

_GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}


@unittest.skipUnless(os.path.exists(SYNC_SH),
                     "sync-public.sh is dev-only, excluded from the public mirror")
@unittest.skipIf(_MISSING, f"needs {', '.join(_MISSING)} on PATH")
class TestNestedCheckoutNeverReachesTheMirror(unittest.TestCase):

    # --- fixtures ------------------------------------------------------------
    def tmp(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        return os.path.realpath(d)

    @staticmethod
    def _write(path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def _git(self, *args, cwd, check=True):
        return subprocess.run(["git", *args], cwd=cwd, check=check,
                              capture_output=True, text=True,
                              env={**os.environ, **_GIT_ENV})

    def _src(self, nested=()):
        """A synthetic source tree carrying the REAL script. `SRC` is `dirname($0)/..`, so
        placing the script at `<tree>/scripts/sync-public.sh` makes `<tree>` the source."""
        src = self.tmp()
        os.makedirs(os.path.join(src, "scripts"))
        shutil.copy2(SYNC_SH, os.path.join(src, "scripts", "sync-public.sh"))
        self._write(os.path.join(src, "pyproject.toml"), 'version = "9.9.9"\n')
        self._write(os.path.join(src, "README.md"), "shippable\n")
        self._write(os.path.join(src, "src", "pkg", "mod.py"), "def widget():\n    return 1\n")
        for rel in nested:
            base = os.path.join(src, *rel.split("/"))
            self._write(os.path.join(base, "vendored.py"), "def widget():\n    return 2\n")
            os.makedirs(os.path.join(base, CHECKOUT_MARKER))
            self._write(os.path.join(base, CHECKOUT_MARKER, "HEAD"), "ref: refs/heads/main\n")
        return src

    def _dest(self):
        dest = self.tmp()
        self._write(os.path.join(dest, "seed.txt"), "seed\n")
        self._git("init", "-q", cwd=dest)
        self._git("add", "-A", cwd=dest)
        self._git("commit", "-qm", "seed", cwd=dest)
        self._git("branch", "-M", "main", cwd=dest)
        return dest

    def _sync(self, src, dest, expect_rc=0):
        proc = subprocess.run(
            ["bash", os.path.join(src, "scripts", "sync-public.sh"), dest],
            capture_output=True, text=True, env={**os.environ, **_GIT_ENV})
        self.assertEqual(proc.returncode, expect_rc,
                         f"sync-public.sh rc={proc.returncode}\n"
                         f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}")
        return proc

    # --- the mirror ----------------------------------------------------------
    def test_the_shippable_tree_still_arrives(self):
        # The control: without this, "nothing leaked" would also be true of a script that
        # copied nothing at all.
        src, dest = self._src(), self._dest()
        self._sync(src, dest)
        self.assertTrue(os.path.exists(os.path.join(dest, "README.md")))
        self.assertTrue(os.path.exists(os.path.join(dest, "src", "pkg", "mod.py")))

    def test_a_nested_checkout_at_a_non_dot_path_is_not_mirrored(self):
        src, dest = self._src(nested=("vendor/lib",)), self._dest()
        self._sync(src, dest)
        self.assertFalse(
            os.path.exists(os.path.join(dest, "vendor", "lib")),
            "a vendored checkout was rsynced into the public mirror — the exclusion is keyed "
            "on the NAME `.claude`, not on the `.git` boundary")
        self.assertTrue(os.path.exists(os.path.join(dest, "README.md")),
                        "the shippable tree was dropped along with the checkout")

    def test_several_nested_checkouts_are_all_excluded(self):
        src = self._src(nested=("vendor/lib", "worktrees/probe", "deep/a/b/clone"))
        dest = self._dest()
        self._sync(src, dest)
        for rel in ("vendor/lib", "worktrees/probe", "deep/a/b/clone"):
            self.assertFalse(os.path.exists(os.path.join(dest, *rel.split("/"))),
                             f"'{rel}' reached the mirror")

    def test_an_ordinary_vendor_directory_with_no_checkout_still_ships(self):
        # The rule is the `.git` boundary, not the word "vendor" — a plain vendored SOURCE dir
        # that the project maintains is shippable and must not be swept up.
        src = self._src()
        self._write(os.path.join(src, "vendor", "lib", "kept.py"), "x = 1\n")
        dest = self._dest()
        self._sync(src, dest)
        self.assertTrue(os.path.exists(os.path.join(dest, "vendor", "lib", "kept.py")))

    # --- the hard guard ------------------------------------------------------
    def test_an_untracked_checkout_already_in_the_mirror_is_removed(self):
        """rsync's --delete does not remove an EXCLUDED path, so a checkout that reached the
        public checkout on an earlier run would survive every later sync. The guard is what
        actually removes it."""
        src, dest = self._src(nested=("vendor/lib",)), self._dest()
        stale = os.path.join(dest, "vendor", "lib")
        self._write(os.path.join(stale, "old.py"), "x = 1\n")
        os.makedirs(os.path.join(stale, CHECKOUT_MARKER))
        proc = self._sync(src, dest)
        self.assertFalse(os.path.exists(stale),
                         "a foreign checkout survived in the public checkout and would be "
                         f"committed by `git add -A`\n{proc.stdout}")
        self.assertIn("vendor/lib", proc.stdout, "the guard removed it silently")

    def test_a_checkout_whose_content_is_TRACKED_publicly_aborts(self):
        """The other arm of the established policy: untracked junk is cleaned, but content
        already in the public HISTORY is a human's decision, never a script's."""
        src, dest = self._src(nested=("vendor/lib",)), self._dest()
        self._write(os.path.join(dest, "vendor", "lib", "leaked.py"), "x = 1\n")
        self._git("add", "-A", cwd=dest)
        self._git("commit", "-qm", "vendored", cwd=dest)
        os.makedirs(os.path.join(dest, "vendor", "lib", CHECKOUT_MARKER))
        proc = self._sync(src, dest, expect_rc=1)
        self.assertIn("ABORT", proc.stdout)
        self.assertIn("vendor/lib", proc.stdout)

    def test_the_mirrors_own_git_directory_is_not_treated_as_foreign(self):
        # The destination IS a checkout. Reading the boundary rule naively would abort every
        # sync on the public repo's own `.git`.
        src, dest = self._src(), self._dest()
        self._sync(src, dest)
        self.assertTrue(os.path.isdir(os.path.join(dest, CHECKOUT_MARKER)))


@unittest.skipUnless(os.path.exists(SYNC_SH), "sync-public.sh is dev-only")
class TestTheShellRuleCannotDriftFromThePythonRule(unittest.TestCase):
    """The script cannot import `mokata.repo_walk` — it is bash, and it runs at release time
    against a tree it is about to mirror. So the boundary marker is necessarily expressed twice.
    The behaviour is pinned above by running the script; this pins the one thing behaviour
    cannot: that both expressions still name the SAME marker."""

    def setUp(self):
        with open(SYNC_SH, encoding="utf-8") as fh:
            self.text = fh.read()

    def test_every_boundary_walk_searches_for_the_marker_the_python_rule_uses(self):
        drifted = marker_drift(self.text, CHECKOUT_MARKER)
        self.assertEqual(
            [], drifted,
            f"sync-public.sh boundary walk(s) no longer look for '{CHECKOUT_MARKER}' — the "
            f"mirror's boundary rule has drifted from `mokata.repo_walk`: "
            + ", ".join(f"line {n} searches for {tok!r}" for n, tok in drifted))

    def test_both_boundary_walks_are_still_there(self):
        """The half a drift check cannot see. `marker_drift` reports offenders, and a DELETED arm
        is not an offender — it is an absence, and an absence reads as clean. The script expresses
        the marker in two independent places (the exclude computation and the destination guard);
        losing either is the same exposure as drifting it."""
        sites = boundary_find_sites(self.text)
        self.assertEqual(
            BOUNDARY_WALKS, len(sites),
            f"sync-public.sh has {len(sites)} checkout-boundary walk(s), not {BOUNDARY_WALKS}: "
            f"{sites}. One computes the rsync excludes over SRC and one guards DEST after the "
            "mirror; they are not redundant (the excludes stop a checkout arriving, the guard "
            "removes one that arrived on an earlier run). If a third is genuinely needed, this "
            "count is where that decision gets reviewed.")

    def test_the_drift_check_can_actually_fire(self):
        """§7i — the rule above is fed PLANTED scripts, because the real one has no offender in it
        and a gate that has only ever seen a clean corpus has never been graded.

        Every arm of the audited failure is a case here: one arm drifted, both drifted, a drift to
        a SUPERSTRING of the real marker (the shape that defeated the old substring test), an arm
        deleted, and prose that merely mentions the marker."""
        def script(*markers):
            return "\n".join(
                f"while IFS= read -r m; do :; done < <(find . -name {tok} -print)"
                for tok in markers)

        clean = script(CHECKOUT_MARKER, CHECKOUT_MARKER)
        self.assertEqual([], marker_drift(clean, CHECKOUT_MARKER),
                         "the rule flags a script that is correct — it would force its own removal")
        self.assertEqual(BOUNDARY_WALKS, len(boundary_find_sites(clean)))

        one = script(CHECKOUT_MARKER, "DOTGIT")
        self.assertEqual([(2, "DOTGIT")], marker_drift(one, CHECKOUT_MARKER),
                         "ONE drifted arm went unreported — the audited defect: the other arm "
                         "still spelling the marker made the whole file look clean")

        both = script("DOTGIT", "DOTGIT")
        self.assertEqual([(1, "DOTGIT"), (2, "DOTGIT")], marker_drift(both, CHECKOUT_MARKER))

        sup = script(CHECKOUT_MARKER + "dir", CHECKOUT_MARKER + "dir")
        self.assertEqual(2, len(marker_drift(sup, CHECKOUT_MARKER)),
                         f"'{CHECKOUT_MARKER}dir' was accepted as '{CHECKOUT_MARKER}' — the "
                         "containment test the audit convicted")

        self.assertEqual(1, len(boundary_find_sites(script(CHECKOUT_MARKER))),
                         "a deleted boundary walk must shrink the site count, which is what "
                         "`test_both_boundary_walks_are_still_there` converts into a red")

    def test_each_half_of_the_extraction_carries_an_offender_only_it_catches(self):
        """The two filters — strip comments, then require the `find` to feed a boundary loop —
        are INDEPENDENT, and the first mutation run on this rule proved they were covering for
        each other: killing either came back GREEN, because the survivor's own mechanism caught
        every fixture the batch had. Two redundant defences cannot be graded (the same argument
        `sync-public.sh` makes about its own `-path ./.git -prune`), so each gets a planted case
        the OTHER one cannot see."""
        # Only comment-stripping can reject this: the loop anchor matches it exactly.
        commented_out = ("# an older revision of the exclude computation, kept for the reader:\n"
                         f"# done < <(find . -name DOTGIT -print)\n")
        self.assertEqual([], boundary_find_sites(commented_out),
                         "a commented-out boundary walk counted as a live one — the script keeps "
                         "prose about this rule and would red on its own documentation")

        # Only the loop anchor can reject this: it is live code, so comment-stripping keeps it.
        unrelated_find = "find . -name '*.pyc' -delete\n"
        self.assertEqual([], boundary_find_sites(unrelated_find),
                         "an ordinary `find -name` was read as a checkout-boundary walk — the "
                         "rule would then red on any unrelated housekeeping the script grows")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
