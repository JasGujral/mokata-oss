"""The graph rebuilds when you EDIT, not only when you COMMIT.

0.0.21 stage 14, from Jas: *"the code graph degrades frequently — we have to find a better way to
update it."* Grounded before it was scoped, and the symptom turned out to name nothing that was
wrong with the graph.

⛔ **THE DEFECT.** `FreshnessController._reconcile` unions FOUR invalidation signals before every
graph query and the detection logic is correct in all four. THREE never fire, so in practice the
index is only ever told it is stale when `HEAD` moves — i.e. only when you commit.

  * signal 1, the dirty set, is written by the `PostToolUse` hook under the HARNESS's `session_id`
    and drained by the query process under a fresh `uuid4()`. Two namespaces that never meet, so
    every in-harness edit drove no rebuild at all.
  * signal 3, the cold walk, seeds a baseline under that same per-process id, so `_load_index()`
    looks for a file no earlier process wrote and `prior` is always None.
  * signal 2 — `git diff --name-only <last-indexed-sha>` — reports the paths differing from that
    sha COMMITTED AND WORKING-TREE, and its module docstring claims it catches "editor/out-of-band
    changes". It was gated on `head != st.head_sha`, so it only ever ran when you committed. ⭐ The
    claim was in the docstring and the gate prevented it.

⚠ **TWO FIXES WERE REJECTED ON MEASUREMENT, and this file exists partly to stop them coming back.**
Making the cold walk's comparison reachable every pass costs **1615 ms** on a 2419-file checkout
against **9.2 ms** for the git diff — 1.6 s on every query, a regression wearing a fix's clothes.
And repo-scoping the state would reverse a stated design decision: the module docstring says "The
first three signals all die with the session", with signal 4 built as the durable backstop.

WHAT THIS FILE GRADES
---------------------
  * **signal 2 detects an out-of-band edit** — the planted offender (§7i). A freshness test on a
    tree where nothing changed proves nothing, so every assertion here plants a change first.
  * **it does NOT re-trigger** — the filter is load-bearing, not tidiness. Without the advanced
    baseline an ungated signal 2 re-reports the same uncommitted edit forever and rebuilds on
    every query. That failure is invisible to a test that only ever edits once.
  * **a NEW file counts as changed** — `KnowledgeIndex.is_stale` answers False for a path absent
    from the baseline ("untracked -> not stale, just unknown"). Correct for its own caller and
    wrong at this call site, where git has already established the path differs. §7g at a filter
    boundary. `is_stale` is deliberately NOT changed; the filter treats absence as changed.
  * **a DELETED file leaves the baseline** — `is_stale` answers True for indexed-but-missing, so a
    deleted path kept in the baseline loops forever: the same defect from the other side.
  * **the session basis is a REPRESENTATION, not a bool** — `unbound` is not "nothing changed", it
    is "the per-session signals cannot see anything", and the reconcile says so.
"""

import os
import subprocess
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata.knowledge import freshness as F
from mokata.knowledge.index import IndexEntry, KnowledgeIndex


def _git(root, *args):
    subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)


def _repo(tmp):
    """A real git repo with one committed file. Real, because signal 2 IS a git subprocess — a
    fake would grade the fixture rather than the mechanism."""
    _git(tmp, "init", "-q", ".")
    _git(tmp, "config", "user.email", "t@t")
    _git(tmp, "config", "user.name", "t")
    with open(os.path.join(tmp, "a.py"), "w", encoding="utf-8") as fh:
        fh.write("def helper(x):\n    return x + 1\n")
    _git(tmp, "add", "-A")
    _git(tmp, "commit", "-qm", "init")
    return tmp


class _Layer:
    """The minimum `_rebuild` touches. Fails LOUD on anything it was not taught (§7e): a stand-in
    that quietly answers an unexpected call turns a green into a claim about nothing."""

    primary = None
    fallback = None

    def __getattr__(self, name):
        raise AssertionError(
            "the freshness test's layer double was asked for %r, which it was never taught. "
            "Teach it explicitly rather than letting it answer by accident." % (name,))


class _Base:
    """A persisted baseline holding exactly the records a test constructed.

    ⛔ WHY A STAND-IN AND NOT A REAL INDEX: `baseline_drift` compares three stat fields, and no
    filesystem operation moves one of them alone — a write moves `size` AND `ctime`, `os.utime`
    moves `mtime` AND `ctime`. Isolating one clause therefore means faulting the RECORD, not the
    file. Fails loud on anything it was not taught (§7e), so if `baseline_drift` ever starts reading
    something else off the baseline, these tests say so instead of silently grading less.
    """

    def __init__(self, entries):
        self.entries = entries

    def __getattr__(self, name):
        raise AssertionError(
            "baseline_drift asked the constructed baseline for %r, which it was never taught. "
            "The stat comparison is supposed to need only `entries`." % (name,))


class OutOfBandEditsAreNoticed(unittest.TestCase):
    """Signal 2, ungated. Each test plants its own offender."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = _repo(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _pass(self, sid="a28-stable-run"):
        return F.FreshnessController(root=self.root, session_id=sid).ensure_fresh(_Layer())

    def _edit(self, rel="a.py", text="\ndef second(x):\n    return helper(x)\n"):
        with open(os.path.join(self.root, rel), "a", encoding="utf-8") as fh:
            fh.write(text)

    def test_the_control_a_warm_pass_with_nothing_touched_is_fresh(self):
        """THE CONTROL. If this ever fails the others prove nothing — they would be reporting
        `changed` for a tree nobody changed."""
        self._pass()
        out = self._pass()
        self.assertTrue(out.fresh, "a warm pass on an untouched tree must be fresh")
        self.assertEqual([], out.changed)

    def test_an_editor_edit_with_no_hook_and_no_commit_is_detected(self):
        """THE DEFECT, planted. Before the fix this reported fresh=True: the edit is uncommitted,
        so HEAD has not moved, so signal 2 never ran."""
        self._pass()
        self._edit()
        out = self._pass()
        self.assertFalse(out.fresh, "an out-of-band edit must not read as fresh")
        self.assertIn("a.py", out.changed)

    def test_the_same_edit_does_not_re_trigger_on_the_next_pass(self):
        """The filter. An ungated signal 2 without an advanced baseline rebuilds on EVERY query
        for as long as the edit stays uncommitted — and a test that edits once cannot see it."""
        self._pass()
        self._edit()
        self.assertIn("a.py", self._pass().changed)
        again = self._pass()
        self.assertTrue(again.fresh,
                        "the edit was already reconciled — re-reporting it rebuilds forever")
        self.assertEqual([], again.changed)

    def test_a_new_file_absent_from_the_baseline_counts_as_changed(self):
        """§7g at the filter boundary: `is_stale` says False for a path it has never seen, which
        is right for its own caller and wrong here."""
        self._pass()
        with open(os.path.join(self.root, "b.py"), "w", encoding="utf-8") as fh:
            fh.write("import a\n")
        _git(self.root, "add", "b.py")
        out = self._pass()
        self.assertIn("b.py", out.changed,
                      "a file git reports as differing must count even though the baseline has "
                      "never seen it")

    def test_a_path_git_names_that_was_NEVER_indexed_and_is_GONE_is_not_a_rebuild(self):
        """🔴 WHAT THE `os.path.exists` ARM IS STILL FOR, after review finding 3-5 took its main job.

        That arm used to be the whole defence against a reconciled deletion looping. The absence
        TOMBSTONE now closes that route — a reconciled deletion IS in the baseline, so the
        absent-from-baseline arm never sees it — and mutant B02, which neutered the `exists` check,
        stopped grading ANYTHING (§7i). §7f says separate or delete, so this states the ONE case the
        arm still owns on its own: a path git reports as differing from the indexed sha that the
        baseline has never seen AND that is not on disk. Deleted before it was ever indexed. There is
        nothing for the graph to rebuild from, so it must not count."""
        self._pass()
        with open(os.path.join(self.root, "ephemeral.py"), "w", encoding="utf-8") as fh:
            fh.write("import a\n")
        _git(self.root, "add", "ephemeral.py")
        _git(self.root, "commit", "-qm", "add ephemeral")
        _git(self.root, "rm", "-q", "ephemeral.py")        # committed, then gone
        out = self._pass()
        self.assertNotIn("ephemeral.py", out.changed,
                         "git names it against the indexed sha, the baseline never saw it, and it "
                         "is not on disk — there is nothing to rebuild from")
        self.assertTrue(self._pass().fresh, "and it must not re-report on the next pass either")

    def test_a_deleted_file_leaves_the_baseline_instead_of_looping(self):
        """The same loop from the other side: `is_stale` answers True for indexed-but-missing."""
        self._pass()
        self._edit()
        self._pass()
        os.remove(os.path.join(self.root, "a.py"))
        _git(self.root, "add", "-A")
        self.assertIn("a.py", self._pass().changed)
        again = self._pass()
        self.assertTrue(again.fresh, "a deleted path kept in the baseline re-triggers forever")


# 🔴 WINDOWS CI, 0.0.21 cut (run 37310393789): `os.geteuid()` in a DECORATOR is evaluated at import,
# and Windows has no `geteuid` — so the AttributeError took the WHOLE MODULE down at discovery, and
# with it every `-k` baseline that merely imports the tree (`test_a9` saw four mutant drivers refuse
# their green step for this one line). `test_review_fix_r4._is_root` met the same line in 0.0.18.
#   * mode bits: a `chmod 0o000` file stays readable to root, and on Windows `chmod` only toggles
#     READ-ONLY, so the "exists but cannot be read" offender cannot be staged on either.
#   * directory symlinks: need SeCreateSymbolicLinkPrivilege on Windows, and Git's own
#     `core.symlinks` decides what the index records. Where one cannot be made the test SKIPS
#     with that reason instead of asserting about a path that never came into being.
_MODE_BITS_BITE = os.name != "nt" and getattr(os, "geteuid", lambda: 0)() != 0
_MODE_BITS_REASON = ("mode bits cannot make a file unreadable here (root ignores them; Windows "
                     "chmod only toggles READ-ONLY), so the offender cannot exist")


def _dir_symlink(testcase, target, link):
    """A symlink to a DIRECTORY, or a SKIP naming why this host cannot make one."""
    if os.name == "nt":
        testcase.skipTest("a .py symlink to a directory is not reliably creatable or tracked on "
                          "Windows (symlink privilege + git core.symlinks); graded on POSIX legs")
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:       # pragma: no cover - restricted host
        testcase.skipTest("cannot create a directory symlink here: %s" % exc)


class APathThatCannotBeHashedDoesNotLoopForever(unittest.TestCase):
    """🔴 REVIEW FINDING A3 — the FOURTH case the §7g split did not enumerate.

    `_advance_baseline` popped a path that did not exist and recorded a fingerprint for one that did.
    A path that EXISTS but CANNOT BE FINGERPRINTED fell through `except Exception: continue` and was
    neither — so signal 2's "absent from the baseline AND on disk ⇒ changed" arm re-fired every pass,
    for the life of the repo. The reviewer reproduced it over five passes; I had not thought of it.
    "On disk" and "readable" are not the same fact."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = _repo(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _pass(self, sid="a28-unhashable-run"):
        """⛔ `_reconcile`, NOT `ensure_fresh`, AND THAT IS THE POINT OF THIS HELPER.

        `ensure_fresh` wraps the reconcile and, on ANY exception, records a degrade and returns
        `FreshnessOutcome(fresh=True)`. So `assertTrue(out.fresh)` — every loop assertion in this
        class — is SATISFIED BY A CRASH. My first fix for this finding did crash (`is_stale` opens
        the file, so an indexed unhashable path raised `IsADirectoryError` out of the filter), the
        tests went green, and the only evidence was a degrade notice printed to stderr. That is §7e
        inside the fix for A3. Calling the reconcile directly makes a crash a test ERROR."""
        return F.FreshnessController(root=self.root, session_id=sid)._reconcile(_Layer())

    def test_the_control_ensure_fresh_does_not_DEGRADE_on_an_unhashable_path(self):
        """The other half of the guard above: `_reconcile` not raising is what makes the public entry
        point honest, so assert the public one records no degrade rather than trusting the private
        one. A degraded freshness pass reports `fresh=True` with a note — indistinguishable from a
        clean tree unless you look for the note (§7g)."""
        self._pass()
        os.mkdir(os.path.join(self.root, "ctl_dir"))
        _dir_symlink(self, "ctl_dir", os.path.join(self.root, "ctl.py"))
        for _ in range(3):
            out = F.FreshnessController(root=self.root,
                                        session_id="a28-unhashable-run").ensure_fresh(_Layer())
            self.assertNotIn("may not reflect the latest edits", out.note or "",
                             "the reconcile RAISED and ensure_fresh swallowed it — a crash is not "
                             "a fresh tree")

    def test_a_py_symlink_pointing_at_a_DIRECTORY_settles(self):
        self._pass()
        os.mkdir(os.path.join(self.root, "adir"))
        _dir_symlink(self, "adir", os.path.join(self.root, "link.py"))
        first = self._pass()
        self.assertIn("link.py", first.changed, "it must be NOTICED once — it is new source")
        for n in range(4):
            again = self._pass()
            self.assertTrue(again.fresh,
                            "pass %d still reported it — this is the forever-rebuild loop, and it "
                            "costs a graph rebuild on EVERY query for the life of the repo" % (n + 2))

    @unittest.skipUnless(_MODE_BITS_BITE, _MODE_BITS_REASON)
    def test_an_unreadable_py_file_settles(self):
        self._pass()
        ab = os.path.join(self.root, "secret.py")
        with open(ab, "w", encoding="utf-8") as fh:
            fh.write("x = 1\n")
        os.chmod(ab, 0o000)
        self.addCleanup(lambda: os.chmod(ab, 0o600))
        self.assertIn("secret.py", self._pass().changed)
        for n in range(4):
            self.assertTrue(self._pass().fresh, "pass %d re-reported an unreadable file" % (n + 2))

    def test_and_it_is_re_reported_the_moment_it_becomes_READABLE(self):
        """⭐ THE CONTROL THAT MATTERS, because the cheap way to stop the loop is to stop looking.
        The sentinel must not become an amnesty: a path parked as unhashable has to come back the
        moment it can be hashed, or A3's fix has traded a loop for a missed change."""
        self._pass()
        ab = os.path.join(self.root, "later.py")
        os.mkdir(os.path.join(self.root, "later_dir"))
        _dir_symlink(self, "later_dir", ab)
        self.assertIn("later.py", self._pass().changed)
        self.assertTrue(self._pass().fresh)
        os.unlink(ab)                                   # now a real, readable source file
        with open(ab, "w", encoding="utf-8") as fh:
            fh.write("def real():\n    return 1\n")
        self.assertIn("later.py", self._pass().changed,
                      "a path parked as unhashable must be re-reported once it can be hashed")

    def test_the_sentinel_cannot_collide_with_a_real_fingerprint(self):
        """The unit-level pin: `UNFINGERPRINTABLE` must not be a value `file_fingerprint` can
        return, or the sentinel silently means "unchanged" for some real file (§7g)."""
        from mokata.knowledge.index import file_fingerprint
        ab = os.path.join(self.root, "a.py")
        self.assertNotEqual(F.UNFINGERPRINTABLE, file_fingerprint(ab)[0])
        self.assertFalse(all(ch in "0123456789abcdef" for ch in F.UNFINGERPRINTABLE),
                         "a hex-shaped sentinel could one day BE a digest")


class GitQUOTESPathsAndThatMadeSignal2Blind(unittest.TestCase):
    """🔴 REVIEW FINDING 3-2 — a whole class of filename was invisible, and nothing noticed.

    `core.quotePath` is ON BY DEFAULT, so git renders any path with a non-ASCII byte as a quoted,
    backslash-escaped C string literal:

        $ git diff --name-only
        "caf\303\251.py"
        plain.py

    `_source_only` then DROPPED it, because the name it was handed ends in `"` rather than `.py`. So
    an out-of-band edit to an accented filename reported `fresh=True` while the baseline knew it was
    stale, and a NEW such file was invisible to the untracked reader for the same reason — fix C's
    gap was only half closed. The reviewer reproduced both halves side by side in one pass.

    ⚠ This is not an exotic input. Accented filenames are ordinary outside English-language repos,
    and this module's entire subject is answering questions about somebody's codebase."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = _repo(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _pass(self, sid="a28-quotepath-run"):
        return F.FreshnessController(root=self.root, session_id=sid)._reconcile(_Layer())

    def test_the_readers_return_the_PATH_not_a_C_string_literal(self):
        """The unit pin, at the call. The integration one below can be satisfied by other signals."""
        ab = os.path.join(self.root, "caf\u00e9.py")
        with open(ab, "w", encoding="utf-8") as fh:
            fh.write("def accented():\n    return 1\n")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-qm", "accented")
        with open(ab, "a", encoding="utf-8") as fh:
            fh.write("\n# edit\n")
        sha = F.git_head_sha(self.root)
        self.assertIn("caf\u00e9.py", F.git_changed_since(self.root, sha),
                      "git quoted the path and the reader passed the quoting through")
        with open(os.path.join(self.root, "na\u00efve.py"), "w", encoding="utf-8") as fh:
            fh.write("def n():\n    return 2\n")
        self.assertIn("na\u00efve.py", F.git_untracked(self.root))

    def test_an_edit_to_an_accented_filename_is_NOTICED(self):
        ab = os.path.join(self.root, "caf\u00e9.py")
        with open(ab, "w", encoding="utf-8") as fh:
            fh.write("def accented():\n    return 1\n")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-qm", "accented")
        self._pass()
        with open(ab, "a", encoding="utf-8") as fh:
            fh.write("\n# out-of-band\n")
        self.assertIn("caf\u00e9.py", self._pass().changed)

    def test_the_control_an_ASCII_name_was_always_caught(self):
        """The control that makes the test above a finding about QUOTING rather than about editing:
        the same edit to an ASCII name was caught before the fix and must still be."""
        self._pass()
        with open(os.path.join(self.root, "a.py"), "a", encoding="utf-8") as fh:
            fh.write("\n# out-of-band\n")
        self.assertIn("a.py", self._pass().changed)


class TheBaselineIsADDITIVENow(unittest.TestCase):
    """🔴 REVIEW FINDING 3-5 — findings A4 and A5 are CLOSED, and the excuse for filing them was false.

    Stage 14 filed both on the stated grounds that an additive baseline *"hashes every indexed file —
    that IS the full mtime/hash walk, 1615 ms"*. ⛔ **`IndexEntry` had persisted `mtime` AND `size`
    since it was written, and nothing ever compared them.** Measured on the same 2423-file corpus:
    full hash walk 843 ms, stat-only pass **6.7 ms** — 127x — and no traversal at all, because the
    persisted baseline IS the path list. The signal the module said it could not afford costs ~7 ms.

      * **A4** — an out-of-band REVERT to a previously-indexed state. git cannot name a file that now
        matches HEAD, so a subtractive signal has an empty candidate set and never asks the baseline.
      * **A5** — a reconciled DELETE then a byte-identical RECREATE. `_advance_baseline` POPPED the
        missing path, so the recreate had nothing to compare against. An absence TOMBSTONE makes
        "reconciled as gone" a third state (§7g) and "gone but back" detectable.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = _repo(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _pass(self, sid="a28-additive-run"):
        return F.FreshnessController(root=self.root, session_id=sid)._reconcile(_Layer())

    def _write(self, rel, text):
        with open(os.path.join(self.root, rel), "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_A4_an_out_of_band_REVERT_to_an_indexed_state_is_noticed(self):
        self._pass()
        self._write("a.py", "def helper(x):\n    return x + 99\n")
        self.assertIn("a.py", self._pass().changed, "the edit itself must reconcile first")
        # ...and now put it back to exactly what the index already holds. git reports NOTHING.
        self._write("a.py", "def helper(x):\n    return x + 1\n")
        sha = F.git_head_sha(self.root)
        self.assertEqual([], F.git_changed_since(self.root, sha),
                         "the premise: git cannot name a file that matches HEAD again")
        self.assertIn("a.py", self._pass().changed,
                      "the baseline KNOWS the graph holds the edited version — and before 3-5 it "
                      "was never asked")
        self.assertTrue(self._pass().fresh, "and it settles rather than looping")

    def test_A5_a_reconciled_delete_then_a_byte_identical_recreate_is_noticed(self):
        self._pass()
        body = "def gone():\n    return 1\n"
        self._write("gone.py", body)
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-qm", "add gone")
        self._pass()
        os.remove(os.path.join(self.root, "gone.py"))
        self.assertIn("gone.py", self._pass().changed, "the deletion reconciles")
        self._write("gone.py", body)                 # byte-identical
        self.assertIn("gone.py", self._pass().changed,
                      "the graph was rebuilt believing this file gone; it is back, and the "
                      "tombstone is what makes that visible")
        self.assertTrue(self._pass().fresh)

    def test_a_TOUCH_that_changes_nothing_does_not_rebuild(self):
        """⚠ THE CONTROL THAT KEEPS THE SIGNAL ∝ CHURN. A drifted `(mtime, size)` is a CANDIDATE, not
        a verdict — `touch` moves mtime without changing content, and reporting that would rebuild
        the graph for nothing. The candidate is confirmed by hash, and its record is then refreshed
        so it does not re-drift on every pass."""
        self._pass()
        ab = os.path.join(self.root, "a.py")
        os.utime(ab, (os.stat(ab).st_atime + 500, os.stat(ab).st_mtime + 500))
        self.assertTrue(self._pass().fresh, "a touch is not a change")
        self.assertTrue(self._pass().fresh, "and it must not re-drift on the next pass either")

    def test_4_1_a_SAME_SIZE_edit_that_RESTORES_the_mtime_is_still_caught(self):
        """🔴 REVIEW FINDING 4-1 — a REAL content edit to a tracked, indexed, non-ignored `.py` read
        `fresh=True` with an EMPTY note. §7g: the same representation as a genuinely clean tree.

        Both pre-answer signals were blind to the same thing. `mtime` and `size` are settable from
        userspace, so a write that preserves both — `tar -xp`, `cp -p`, `rsync --times`, `touch -r`,
        i.e. every vendor drop and every restore — moved neither, AND git's own stat cache trusts
        exactly those two fields, so `git diff --name-only` reported clean as well.

        ⭐ `st_ctime` is the inode CHANGE time and POSIX gives userspace no way to set it. Measured
        on both attacks: mtime False, size False, **ctime True**."""
        ab = os.path.join(self.root, "a.py")
        os.utime(ab, (1700000000, 1700000000))        # a ROUND mtime, as tar/touch -d gives
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-qm", "round mtime")
        _git(self.root, "status")                     # warm git's stat cache
        self._pass()
        before = os.stat(ab)
        with open(ab, "w", encoding="utf-8") as fh:
            fh.write("def helper(x):\n    return x + 9\n")   # SAME LENGTH as the original
        os.utime(ab, (1700000000, 1700000000))        # ...and the mtime restored exactly
        after = os.stat(ab)
        self.assertEqual(before.st_size, after.st_size, "the premise: the size did not move")
        self.assertEqual(before.st_mtime, after.st_mtime, "the premise: the mtime did not move")
        self.assertEqual([], F.git_changed_since(self.root, F.git_head_sha(self.root)),
                         "the premise: git's own stat cache is blind to this, so signal 2 cannot "
                         "be what catches it")
        out = self._pass()
        self.assertIn("a.py", out.changed,
                      "a real content edit read as a clean tree — the worst shape this module has")
        self.assertTrue(self._pass().fresh, "and it settles")

    def test_4_4_each_of_the_THREE_stat_fields_is_graded_on_its_own(self):
        """🔴 REVIEW FINDING 4-4, §7f. `baseline_drift`'s comparison is the thing the signal is named
        after, and NOTHING asserted it: mutants removing the mtime half and the size half were BOTH
        survivors, and only the pair was graded — indirectly, through the tombstone arms.

        ⛔ AND THE FIRST FIX DID NOT GRADE THEM EITHER. It attacked the real file three times —
        a longer write, an `os.utime`, a same-size write — and asserted the path came back. But a
        write moves `ctime` as well as `size`, and `os.utime` moves `ctime` as well as `mtime`, so
        every single-field attack was ALSO caught by the ctime clause: the mtime-dropped and
        size-dropped mutants both survived a test written to kill exactly them. A real filesystem
        cannot move one of these three fields alone, so the offender has to be CONSTRUCTED: the
        record is what is made stale in one field, with the other two matching `stat` exactly."""
        self._pass()
        ab = os.path.join(self.root, "a.py")
        st = os.stat(ab)
        h = "0" * 64                    # not ABSENT and not UNFINGERPRINTABLE: the stat arms are
                                        # the only ones this record can reach.

        def drift(**bad):
            fields = {"mtime": st.st_mtime, "size": st.st_size, "ctime": st.st_ctime}
            fields.update(bad)
            entry = IndexEntry("a.py", h, fields["mtime"], fields["size"], fields["ctime"])
            return F.baseline_drift(_Base({"a.py": entry}), self.root)

        self.assertEqual([], drift(),
                         "the polarity control: all three fields matching is NOT drift, so each "
                         "assertion below is the clause firing and not the pass reporting always")
        self.assertEqual(["a.py"], drift(mtime=st.st_mtime + 900),
                         "the MTIME clause must catch a record only its own field can fault")
        self.assertEqual(["a.py"], drift(size=st.st_size + 7),
                         "the SIZE clause must catch a record only its own field can fault")
        self.assertEqual(["a.py"], drift(ctime=st.st_ctime + 900),
                         "the CTIME clause must catch a record only its own field can fault")

    def test_4_1_a_record_with_NO_ctime_has_no_opinion_rather_than_drifting(self):
        """§7g for the new field: an index persisted before `ctime` existed records 0.0, and 0.0 must
        mean NO OPINION. Treating it as "changed" would make every upgrade rebuild the whole graph
        once, and treating a real 0.0 ctime as a match would be the other error."""
        from mokata.knowledge.index import IndexEntry, KnowledgeIndex, file_fingerprint
        ab = os.path.join(self.root, "a.py")
        h, m, sz = file_fingerprint(ab)
        legacy = KnowledgeIndex(entries={"a.py": IndexEntry("a.py", h, m, sz)})   # ctime defaults 0.0
        self.assertEqual(0.0, legacy.entries["a.py"].ctime)
        self.assertEqual([], F.baseline_drift(legacy, self.root),
                         "a legacy record must not drift on a field it never carried")

    def test_4_7_a_TOUCHED_path_has_its_RECORD_refreshed_not_just_its_verdict(self):
        """🔴 REVIEW FINDING 4-7 (R04). `drift_settled = []` was a SURVIVOR: the touch test asserted
        the pass stayed fresh, which is true either way, so nothing graded the re-stamp — and without
        it a touched file re-drifts and is re-hashed on EVERY pass, turning an ∝-churn signal into an
        ∝-touched-files one. That is this module's whole perf contract."""
        self._pass()
        ab = os.path.join(self.root, "a.py")
        os.utime(ab, (os.stat(ab).st_atime + 700, os.stat(ab).st_mtime + 700))
        self.assertTrue(self._pass().fresh, "a touch is not a change")
        base = F.FreshnessController(root=self.root,
                                     session_id="a28-additive-run")._load_index()
        self.assertEqual([], F.baseline_drift(base, self.root),
                         "the record was not re-stamped, so this path drifts and is re-hashed every "
                         "pass for as long as it exists")

    def test_4_7_a_TOMBSTONED_path_that_is_still_gone_is_not_a_candidate(self):
        """🔴 REVIEW FINDING 4-7 (R07). The tombstoned-and-still-gone arm of `baseline_drift` was a
        survivor — and it is the loop guard from the other side: without it a reconciled deletion is
        a drift candidate on every pass forever."""
        from mokata.knowledge.index import IndexEntry, KnowledgeIndex
        idx = KnowledgeIndex(entries={"gone.py": IndexEntry("gone.py", F.ABSENT, 0.0, -1)})
        self.assertEqual([], F.baseline_drift(idx, self.root),
                         "a tombstone for a path that is still gone has already been reconciled")

    def test_4_7_an_UNHASHABLE_path_is_RE_ASKED_once_it_stats(self):
        """🔴 REVIEW FINDING 4-7 (R08). The `UNFINGERPRINTABLE` re-ask arm was a survivor, so nothing
        stopped a path parked as unhashable from being parked FOREVER — the sentinel becoming an
        amnesty, which is exactly what A3's own control warned about one layer up."""
        from mokata.knowledge.index import IndexEntry, KnowledgeIndex
        idx = KnowledgeIndex(entries={"a.py": IndexEntry("a.py", F.UNFINGERPRINTABLE, 0.0, -1)})
        self.assertIn("a.py", F.baseline_drift(idx, self.root),
                      "a path parked as unhashable must be re-asked the moment it stats again")

    def test_the_drift_pass_is_STAT_only_for_the_paths_it_clears(self):
        """The property the whole finding turns on, asserted rather than trusted: `baseline_drift`
        must not read file contents. If it hashed, it would be the 843 ms walk under a new name."""
        self._pass()
        base = F.FreshnessController(root=self.root,
                                     session_id="a28-additive-run")._load_index()
        self.assertIsNotNone(base, "the cold walk must have seeded a baseline")
        self.assertTrue(base.entries, "...with entries to drift against")
        import mokata.knowledge.index as IDX
        calls = []
        real = IDX.file_fingerprint
        IDX.file_fingerprint = lambda ab: (calls.append(ab), real(ab))[1]
        try:
            self.assertEqual([], F.baseline_drift(base, self.root),
                             "nothing changed, so nothing drifts")
        finally:
            IDX.file_fingerprint = real
        self.assertEqual([], calls,
                         "baseline_drift hashed a file — it is a STAT pass, and hashing every "
                         "indexed path is precisely the 843 ms cost this fix exists to avoid")


class TheSessionBasisIsARepresentation(unittest.TestCase):
    """Fix A. `unbound` and "nothing changed" must never share a representation."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = _repo(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_an_explicit_session_is_reported_as_the_EXPLICIT_rung_not_invented(self):
        """🔴 CHANGED BY REVIEW FINDING A7. An explicit id now goes into the ladder as `run_id`, so
        the ladder ANSWERS it at its own EXPLICIT rung and the basis names that rung. `SESSION_GIVEN`
        is now reachable only when the ladder itself faults, which is the honest shape: the basis
        should name whoever answered, and before this it named a fallback that had not been used."""
        key, basis = F.resolve_freshness_session(self.root, "cc-abc123")
        self.assertEqual("cc-abc123", key)
        self.assertEqual("explicit", basis)

    def test_an_explicit_session_id_OUTRANKS_an_ambient_pin(self):
        """🔴 REVIEW FINDING A7, planted. The explicit id used to be passed as the BOUND rung's
        INPUT only, so an ambient `MOKATA_SESSION_ID` won at PINNED and the caller's id was SILENTLY
        DISCARDED — the controller then read and wrote state under a key its caller never asked for,
        and `SESSION_GIVEN` became a dead representation whenever a pin existed (§7g)."""
        prior = os.environ.get("MOKATA_SESSION_ID")
        os.environ["MOKATA_SESSION_ID"] = "an-ambient-run"
        try:
            key, basis = F.resolve_freshness_session(self.root, "caller-gave-this-id")
            self.assertEqual("caller-gave-this-id", key,
                             "an explicit id must not be silently replaced by an ambient pin")
            self.assertEqual("explicit", basis)
            c = F.FreshnessController(root=self.root, session_id="caller-gave-this-id")
            self.assertEqual("caller-gave-this-id", c.session_id,
                             "the state key follows the id the caller asked for")
            # THE CONTROL: with no explicit id, the pin DOES win — otherwise "explicit wins" would
            # also be satisfied by a resolver that ignored the pin entirely.
            k2, b2 = F.resolve_freshness_session(self.root, None)
            self.assertEqual("an-ambient-run", k2)
            self.assertEqual("pinned", b2)
        finally:
            if prior is None:
                os.environ.pop("MOKATA_SESSION_ID", None)
            else:
                os.environ["MOKATA_SESSION_ID"] = prior


class TheWriterAndTheReaderResolveTheSameKey(unittest.TestCase):
    """🔴 REVIEW FINDING A1 — the defect fix A was supposed to close and did not.

    Fix A rewired the READER of the dirty set to the run-resolution ladder and left the WRITER
    (`mark_dirty`, called by the `PostToolUse` hook) on `_sid`, which returns Claude Code's
    `session_id` verbatim. Two namespaces that still never met, so signal 1 was STILL dead — and the
    blind-signal note was gated on `SESSION_UNBOUND`, so a resolved session draining a key nobody
    wrote said NOTHING. **That is worse than the original defect in one dimension:** before fix A the
    mismatch at least showed up as a 1.6 s cold walk on every query. The independent review measured
    all of it; I did not."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = _repo(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self._prior = os.environ.pop("MOKATA_SESSION_ID", None)
        self.addCleanup(self._restore)

    def _restore(self):
        if self._prior is None:
            os.environ.pop("MOKATA_SESSION_ID", None)
        else:
            os.environ["MOKATA_SESSION_ID"] = self._prior

    def _a_bound_run(self, cc="cc-session-abcdef", run="run-7f3a91"):
        """A repo that looks the way production looks: ONE tracked run, and Claude Code's session id
        bound to it by `SessionStart`. This is the fixture the first version of these tests lacked,
        and lacking it is why they passed against the defective source (§7i)."""
        from mokata.run_resolver import bind_session_run
        from mokata.state import StateStore
        from mokata.tdd_state import TDD_STATE_PREFIX, state_dir
        StateStore(state_dir(self.root)).write(TDD_STATE_PREFIX + run, {"phase": "RED"})
        bind_session_run(self.root, cc, run)
        return cc, run

    def test_what_the_hook_writes_is_what_the_query_process_drains(self):
        """⭐ THE DEFECT, PLANTED WITH THE RIGHT ASYMMETRY — and my first version of this test did
        NOT have it, so it passed against the defective source and graded nothing.

        The asymmetry IS the defect: the hook HAS Claude Code's `session_id` and the query process
        does NOT (harness gap #25642, and `for_surface` passes none). Handing the same id to both
        sides, as the first version did, makes them meet even under the old `_sid` writer — which is
        exactly the shape §7i warns about. So the writer resolves WITH the harness id and the reader
        resolves with NOTHING, and they must still land on the same key."""
        cc, run = self._a_bound_run()
        F.mark_dirty(self.root, ["a.py"], session_id=cc)          # the hook
        self.assertEqual(["a.py"], F.drain_dirty(self.root),      # the query process: NO id
                         "the hook's record must be visible to a process that cannot see the "
                         "harness session id — that is the whole point of resolving through the "
                         "ladder on BOTH sides")

    def test_a_reconcile_SEES_a_hook_edit_rather_than_reporting_fresh(self):
        """The same thing through the real reconcile, which is where it matters. The controller is
        built exactly as `for_surface` builds it: no `session_id`."""
        cc, run = self._a_bound_run()
        F.FreshnessController(root=self.root).ensure_fresh(_Layer())     # warm the state
        F.mark_dirty(self.root, ["a.py"], session_id=cc)                 # the hook
        out = F.FreshnessController(root=self.root).ensure_fresh(_Layer())
        self.assertIn("a.py", out.changed,
                      "an in-harness edit recorded by the hook must drive a rebuild")
        self.assertNotIn("filed under another session key", out.note or "",
                         "and it must not be reported as a partitioned record — it was READ")

    def test_a_PARTITIONED_dirty_set_is_SAID_not_silently_fresh(self):
        """🔴 A1's second half. A record filed under a key this process is not reading is not the
        same fact as "nothing changed" (§7g), and conditioning the note on the BASIS missed it
        entirely — a `pinned` session suppressed the note while draining a key nobody wrote.

        ⚠ RE-SCOPED BY REVIEW FINDING 3-6(a): the note fires only on a pass that would otherwise
        report FRESH, which is the one case it exists for. Before that it fired on EVERY pass — two
        leftover logs from finished sessions nagged for the life of the repo, including passes where
        signal 1 worked perfectly, which is the §7i nagging this stage argued against twice."""
        F.mark_dirty(self.root, ["a.py"], session_id="some-other-session")
        out = F.FreshnessController(root=self.root, session_id="this-session").ensure_fresh(_Layer())
        self.assertTrue(out.fresh, "the premise: this pass found nothing of its own")
        self.assertIn("other session key", out.note or "",
                      "a partitioned signal 1 must say so rather than report a silent fresh")
        self.assertNotIn("no run is bound", out.note or "",
                         "this session RESOLVED — the unbound note is a different fact")

    def test_the_note_does_NOT_ride_a_pass_that_found_something(self):
        """🔴 REVIEW FINDING 3-6(a). On a pass that rebuilt, the note adds nothing and costs the
        reader's attention — and the reader's attention is the thing a nagging note spends."""
        F.mark_dirty(self.root, ["a.py"], session_id="some-other-session")
        c = F.FreshnessController(root=self.root, session_id="this-session")
        c.ensure_fresh(_Layer())
        with open(os.path.join(self.root, "a.py"), "a", encoding="utf-8") as fh:
            fh.write("\ndef mine(x):\n    return x\n")
        out = F.FreshnessController(root=self.root,
                                    session_id="this-session").ensure_fresh(_Layer())
        self.assertFalse(out.fresh, "the premise: this pass DID find something")
        self.assertNotIn("other session key", out.note or "")

    def test_the_note_does_NOT_claim_which_signal_answered(self):
        """🔴 REVIEW FINDING 3-6(c). It used to end *"HEAD/working-tree and code-anchor signals …
        are what answered here"* — a claim this function cannot make, and measurably false on a first
        pass where `st.head_sha` is None so signal 2 never ran. A note added to stop a silent fresh
        was itself asserting something unmeasured, which is §7g one level up."""
        F.mark_dirty(self.root, ["a.py"], session_id="some-other-session")
        out = F.FreshnessController(root=self.root, session_id="this-session").ensure_fresh(_Layer())
        self.assertNotIn("what answered here", out.note or "")
        self.assertNotIn("still apply", out.note or "")

    def test_the_control_no_foreign_record_means_no_such_note(self):
        """THE CONTROL. Without it, "the note is present" would also be true of a note that is
        always present — which is exactly how the basis-gated version passed review the first time."""
        F.mark_dirty(self.root, ["a.py"], session_id="this-session")
        out = F.FreshnessController(root=self.root, session_id="this-session").ensure_fresh(_Layer())
        self.assertNotIn("other session key", out.note or "")

    def test_the_note_counts_RECORDS_not_FILES(self):
        """🔴 REVIEW FINDING 3-6(b). It passed `len(foreign)` — the number of KEYS — to a `%d …
        record(s)` format, so two logs holding six paths read as "2 record(s)"."""
        F.mark_dirty(self.root, ["a.py", "b.py", "c.py"], session_id="alpha")
        F.mark_dirty(self.root, ["d.py", "e.py", "f.py"], session_id="beta")
        keys, records = F.foreign_dirty_records(self.root, "gamma")
        self.assertEqual(["alpha", "beta"], keys)
        self.assertEqual(6, records, "two keys, six records — the note must not conflate them")
        out = F.FreshnessController(root=self.root, session_id="gamma").ensure_fresh(_Layer())
        self.assertIn("6 in-harness edit record(s) under 2 other session key(s)", out.note or "")

    def test_foreign_dirty_keys_is_measured_not_inferred(self):
        """The unit-level pin, because the integration one above is absorbed by whatever else the
        reconcile found (§7i)."""
        F.mark_dirty(self.root, ["a.py"], session_id="alpha")
        F.mark_dirty(self.root, ["b.py"], session_id="beta")
        self.assertEqual(["alpha"], F.foreign_dirty_keys(self.root, "beta"))
        self.assertEqual(["beta"], F.foreign_dirty_keys(self.root, "alpha"))
        self.assertEqual(["alpha", "beta"], F.foreign_dirty_keys(self.root, "gamma"))

    def test_the_writer_and_reader_CANNOT_meet_before_a_run_is_registered_and_it_SAYS_so(self):
        """🔴 REVIEW FINDING 3-8 — the residual gap, pinned as a gap rather than papered over.

        With no pin and no registered run the WRITER returns the raw harness id at `SESSION_GIVEN`
        and the reader mints a per-process uuid4 at `SESSION_UNBOUND`. They cannot meet, by
        construction: the reader has no route to the harness session id (harness gap #25642), so
        there is nothing for it to resolve to. ⛔ **So signal 1 is dead before a run is registered**
        — edits made before `/mokata:brainstorm` starts one. What the module CAN do is say so, and
        that is what this grades. The docstring claimed both sides fell to the per-process id; they
        do not, and the reviewer measured it."""
        wkey, wbasis = F.resolve_harness_session(self.root, "claude-code-session-abc")
        rkey, rbasis = F.resolve_freshness_session(self.root, None)
        self.assertEqual("claude-code-session-abc", wkey)
        self.assertEqual(F.SESSION_GIVEN, wbasis)
        self.assertEqual(F.SESSION_UNBOUND, rbasis)
        self.assertNotEqual(wkey, rkey, "the gap is real; the test records it rather than hiding it")
        F.mark_dirty(self.root, ["a.py"], session_id="claude-code-session-abc")
        out = F.FreshnessController(root=self.root).ensure_fresh(_Layer())
        self.assertIn("other session key", out.note or "",
                      "unreachable is acceptable; SILENT is not (§7g)")
        self.assertIn("no run is bound", out.note or "",
                      "and the unbound basis is a second, different fact the reader needs")

    def test_no_binding_reports_unbound_rather_than_a_plausible_id(self):
        """The id still exists — freshness must not crash without one — but the BASIS says it
        cannot cross a process boundary, which is the fact a caller needs."""
        key, basis = F.resolve_freshness_session(self.root, None)
        self.assertTrue(key, "a key is always produced; the basis is what carries the caveat")
        self.assertEqual(F.SESSION_UNBOUND, basis)

    def test_an_unbound_reconcile_SAYS_the_in_harness_signals_are_blind(self):
        """Not a silent fresh. Before this, a process with no bound run reported exactly what a
        genuinely clean tree reports."""
        out = F.FreshnessController(root=self.root).ensure_fresh(_Layer())
        self.assertIn("no run is bound", out.note or "",
                      "an unbound pass must say the in-harness signals are not visible")

    def test_the_LADDER_is_what_resolves_a_session_when_none_is_handed_in(self):
        """⚠ ADDED AFTER A MUTANT SURVIVED. Skipping `resolve_run` entirely was GREEN against the
        first version of this file: every test either handed a `session_id` in (which the
        `SESSION_GIVEN` branch answers without the ladder) or had nothing to resolve (which is
        `unbound` either way). So nothing here depended on the ladder, and the fix that IS fix A
        was ungraded. This pins the PINNED rung — the one rung reachable without building a run —
        with no `session_id` argument at all, so the ladder is the only thing that can answer."""
        prior = os.environ.get("MOKATA_SESSION_ID")
        os.environ["MOKATA_SESSION_ID"] = "a28-ladder-pinned"
        try:
            key, basis = F.resolve_freshness_session(self.root, None)
            self.assertEqual("a28-ladder-pinned", key,
                             "the pinned run must be the freshness session key")
            self.assertEqual("pinned", basis,
                             "the BASIS must name the rung that answered — not `given` (nothing "
                             "was given) and not `unbound` (something resolved)")
            # ...and it must be the SAME key from a second construction, which is the whole point:
            # a per-process id is what made signals 1 and 3 blind.
            a = F.FreshnessController(root=self.root)
            b = F.FreshnessController(root=self.root)
            self.assertEqual(a.session_id, b.session_id,
                             "two processes in one run must share the freshness session key")
            self.assertNotEqual(F.SESSION_UNBOUND, a.session_basis)
        finally:
            if prior is None:
                os.environ.pop("MOKATA_SESSION_ID", None)
            else:
                os.environ["MOKATA_SESSION_ID"] = prior

    def test_the_control_an_UNBOUND_key_does_NOT_survive_a_process_boundary(self):
        """The control for the assertion above — and MY FIRST VERSION OF IT WAS WRONG, which is
        recorded here rather than quietly fixed. It asserted two controllers built back-to-back get
        DIFFERENT unbound ids. They do not: `_sid(None)` resolves through `session.current_session`,
        which is cached per PROCESS, so two instances in one process share it. The property that
        actually matters is the one the basis exists to announce — the key is minted from this
        process, not from the repo, so it does not survive a restart. `session.reset_for_test()` is
        the process-restart equivalent, and it is how this is graded without spawning one."""
        from mokata import session as S
        prior = os.environ.pop("MOKATA_SESSION_ID", None)
        try:
            a = F.FreshnessController(root=self.root)
            self.assertEqual(F.SESSION_UNBOUND, a.session_basis)
            b = F.FreshnessController(root=self.root)
            self.assertEqual(a.session_id, b.session_id,
                             "within ONE process the unbound key is stable — that is why an "
                             "unbound pass still reconciles anything at all")
            S.reset_for_test()                    # the process boundary, in one call
            c = F.FreshnessController(root=self.root)
            self.assertNotEqual(a.session_id, c.session_id,
                                "across processes it is a different key — which is exactly why "
                                "signals 1 and 3 are blind, and why the basis says so")
            self.assertEqual(F.SESSION_UNBOUND, c.session_basis)
        finally:
            S.reset_for_test()
            if prior is not None:
                os.environ["MOKATA_SESSION_ID"] = prior

    def test_a_bound_session_does_NOT_carry_that_note(self):
        """The control for the assertion above — without it, 'the note is present' would also be
        true of a note that is always present."""
        out = F.FreshnessController(root=self.root,
                                    session_id="a28-stable-run").ensure_fresh(_Layer())
        self.assertNotIn("no run is bound", out.note or "")


class _Ref:
    def __init__(self, path):
        self.path = path


class _Result:
    """The shape `recheck_after_answer` reads: a result that NAMES the files it answered from."""

    def __init__(self, *paths):
        self.references = [_Ref(p) for p in paths]


class TheUntrackedFileGapIsClosed(unittest.TestCase):
    """Fix C. A source file git has never been told about was seen by NO signal.

    ⭐ This is the hole the FIRST DRAFT of the cold walk's re-scoped docstring claimed was covered.
    Probe, four processes, `brand_new.py` written and not added: cold walk -> nothing, warm pass ->
    nothing, a BRAND-NEW session's cold walk -> nothing, and only `git add` -> caught. The walk
    reports `prior.stale_files(...)` against a baseline from EARLIER IN THE SAME SESSION, and
    `cold_done` is set in the pass that runs it, so `prior` is None every real time. Signal 3 seeds;
    it does not report. `git_untracked` is what closes this, and these tests are what stop it being
    deleted as a redundant second git call."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = _repo(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _pass(self, sid="a28-untracked-run"):
        return F.FreshnessController(root=self.root, session_id=sid).ensure_fresh(_Layer())

    def _write(self, rel, text="def brand_new():\n    return 1\n"):
        with open(os.path.join(self.root, rel), "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_the_control_a_warm_pass_with_no_new_file_is_fresh(self):
        """THE CONTROL. Without it "the untracked file was reported" would also be satisfied by a
        signal that reports on every pass regardless."""
        self._pass()
        out = self._pass()
        self.assertTrue(out.fresh)
        self.assertEqual([], out.changed)

    def test_an_untracked_new_source_file_is_noticed(self):
        self._pass()
        self._write("brand_new.py")
        out = self._pass()
        self.assertIn("brand_new.py", out.changed,
                      "a new source file must be noticed before someone runs `git add`")

    def test_and_the_git_diff_half_of_signal_2_could_never_have_caught_it(self):
        """Attribution, not decoration (§7b). This proves WHICH half of signal 2 answered: if the
        diff reported the path too, the test above would pass with `git_untracked` deleted."""
        self._pass()
        self._write("brand_new.py")
        sha = F.git_head_sha(self.root)
        self.assertNotIn("brand_new.py", F.git_changed_since(self.root, sha),
                         "`git diff --name-only <sha>` cannot name a path git has never indexed — "
                         "if it can, this whole fix is redundant and should go")
        self.assertIn("brand_new.py", F.git_untracked(self.root))

    def test_the_untracked_file_does_not_re_trigger_on_every_pass(self):
        """`git ls-files --others` keeps reporting it for as long as it stays untracked, which is
        forever. Only the advanced baseline stops that being a rebuild per query."""
        self._pass()
        self._write("brand_new.py")
        self.assertIn("brand_new.py", self._pass().changed)
        again = self._pass()
        self.assertTrue(again.fresh, "an untracked file reconciled once must go quiet")
        self.assertEqual([], again.changed)

    def test_deleting_the_untracked_file_is_NOTICED_ONCE_then_goes_quiet(self):
        """The deletion loop from the untracked side — the defect Fix B's three-case filter was
        written for, arriving by the new route.

        🔴 CHANGED BY REVIEW FINDING 3-5, AND THE CHANGE IS AN IMPROVEMENT RATHER THAN A CONCESSION.
        This used to assert the deletion was fresh IMMEDIATELY — because an untracked file's removal
        was named by no signal at all: `git ls-files --others` stops listing it and `git diff` never
        did. The graph was left holding a file that no longer exists and nothing said so. The
        additive baseline pass sees it (the indexed path no longer stats), so it is REPORTED ONCE —
        which is what a deletion should do — and the tombstone then settles it.

        ⚠ The second half of the old assertion is the half that mattered and it is kept: once
        reconciled, it must STAY quiet. A deletion that reports forever is the loop."""
        self._pass()
        self._write("brand_new.py")
        self._pass()
        os.remove(os.path.join(self.root, "brand_new.py"))
        first = self._pass()
        self.assertIn("brand_new.py", first.changed,
                      "a deletion the graph has indexed must be noticed — the graph is holding a "
                      "file that is gone, and before 3-5 no signal named it")
        self.assertTrue(self._pass().fresh, "a removed untracked path must not re-trigger forever")
        self.assertTrue(self._pass().fresh, "...and must stay settled, not alternate")

    def test_a_gitignored_file_is_NOT_reported(self):
        """`--exclude-standard` is what bounds this call on a real checkout: without it a venv or a
        build tree would arrive as thousands of changed source files."""
        with open(os.path.join(self.root, ".gitignore"), "w", encoding="utf-8") as fh:
            fh.write("ignored/\n")
        _git(self.root, "add", ".gitignore")
        _git(self.root, "commit", "-qm", "ignore")
        os.makedirs(os.path.join(self.root, "ignored"))
        # ⚠ THE REPO-RELATIVE NAME IS SPELLED POSIX, AND `os.path.join` IS NOT HOW YOU SPELL IT.
        # `test_windows_shell_and_paths` caught the first version of this: git reports `ignored/junk.py`
        # with forward slashes on every platform, so a native join builds `ignored\junk.py` on
        # Windows and the assertion silently matches nothing. The join is for the FILESYSTEM write
        # only, which `_write` does against `self.root`.
        self._write("ignored/junk.py")
        # ⚠ AT THE CALL, NOT THROUGH THE RECONCILE. Dropping `--exclude-standard` SURVIVED the
        # integration assertion below: the cold walk indexes every `.py` on the filesystem including
        # the ignored one, so by the second pass the baseline already holds it and `is_stale` says
        # no. The reconcile genuinely cannot tell the two apart, so the pin belongs where the
        # distinction exists (§7i — a guard whose offender is absorbed upstream grades nothing).
        self.assertNotIn("ignored/junk.py", F.git_untracked(self.root),
                         "`--exclude-standard` is what keeps a venv or build tree out of the "
                         "candidate set — without it this call reports ignored files")
        self._pass()
        out = self._pass()
        self.assertEqual([], out.changed, "a gitignored source file must not force a rebuild")

    def test_the_control_the_SAME_file_outside_the_ignore_IS_reported(self):
        """The control for the assertion above, which would otherwise also pass if `git_untracked`
        returned [] unconditionally."""
        self._pass()
        os.makedirs(os.path.join(self.root, "notignored"))
        self._write("notignored/junk.py")          # POSIX — see the note above
        self.assertIn("notignored/junk.py", self._pass().changed)


class ThePostAnswerRecheckSurvivesAProcessBoundary(unittest.TestCase):
    """Fix C. `recheck_after_answer` was filed as "structurally dead — always returns False".

    ⛔ IT WAS NOT DEAD. It was alive only because the SESSION was broken, and Fix A killed it.
    `self._index` is seeded by `_cold_walk` alone; the walk runs once per session KEY. With a fresh
    uuid4 per process every query was a cold start, so the walk ran every time (1.6 s of it) and
    the recheck worked every time. Once Fix A made the key stable, process 1 seeds and processes
    2..n do not. Two defects were masking each other, and the delete-or-wire ruling was conditional
    on it being unable to fire — so it is WIRED: the baseline was persisted all along."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = _repo(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _controller(self, sid="a28-recheck-run"):
        """A FRESH controller each time — the stand-in for a new `mokata query` process. The point
        of the test is the process boundary, so reusing one instance would grade nothing."""
        return F.FreshnessController(root=self.root, session_id=sid)

    def test_the_control_no_edit_means_no_requery(self):
        """THE CONTROL, and it is the one that matters here: a recheck that returned True
        unconditionally would satisfy every other assertion in this class."""
        c1 = self._controller()
        c1.ensure_fresh(_Layer())
        c2 = self._controller()
        c2.ensure_fresh(_Layer())
        self.assertFalse(c2.recheck_after_answer(_Layer(), _Result("a.py")),
                         "nothing was edited — a rebuild here is a rebuild on every query")
        # ⚠ AND IT MUST BE FALSE FOR THE RIGHT REASON. Pre-Fix-C this returned False because
        # `self._index` was None and it never looked at anything — the same answer for the opposite
        # reason (§7g). Asserting the baseline was CONSULTED is what makes this a control on the
        # fix rather than a control on the bug.
        self.assertIsNotNone(c2._index,
                             "the persisted baseline must have been loaded and consulted — a "
                             "False from never looking is not the same answer")

    def test_a_LATER_process_still_catches_an_out_of_band_edit(self):
        """THE DEFECT, planted at the process boundary. Process 1 seeds the baseline; process 2
        never runs a cold walk, so before Fix C its `self._index` was None and this was False."""
        self._controller().ensure_fresh(_Layer())        # process 1: seeds the baseline
        c2 = self._controller()                          # process 2: no cold walk
        c2.ensure_fresh(_Layer())
        self.assertIsNone(c2._index, "process 2 must NOT have seeded an in-memory index — if it "
                                     "did, this test is grading process 1 all over again")
        with open(os.path.join(self.root, "a.py"), "a", encoding="utf-8") as fh:
            fh.write("\ndef after_the_answer():\n    return 2\n")
        self.assertTrue(c2.recheck_after_answer(_Layer(), _Result("a.py")),
                        "an edit that landed after the answer must force a requery")

    def test_a_path_already_reconciled_this_pass_is_not_rebuilt_twice(self):
        """Fix B's advanced baseline is what makes this true, and it is worth an assertion: the
        cheap way to make the test above pass would rebuild once per query as well."""
        self._controller().ensure_fresh(_Layer())
        with open(os.path.join(self.root, "a.py"), "a", encoding="utf-8") as fh:
            fh.write("\ndef edited(x):\n    return x\n")
        c2 = self._controller()
        out = c2.ensure_fresh(_Layer())
        self.assertIn("a.py", out.changed, "signal 2 must have taken this one")
        self.assertFalse(c2.recheck_after_answer(_Layer(), _Result("a.py")),
                         "the reconcile already handled a.py — rechecking it rebuilds twice")
        self.assertIsNotNone(c2._index,
                             "same trap as the control above: this must be False because the "
                             "baseline says a.py is current, not because there was no baseline")

    def test_no_baseline_at_all_is_NO_OPINION_rather_than_a_crash(self):
        """§7g once more: "no baseline on disk" is not "nothing changed", and it is also not an
        exception a query should pay for."""
        c = self._controller("a28-never-reconciled")
        self.assertFalse(c.recheck_after_answer(_Layer(), _Result("a.py")))


class TheThreePropertiesTheREVIEWFoundUNGRADED(unittest.TestCase):
    """🔴 REVIEW FINDING A6 — three mutants the reviewer wrote SURVIVED this file and the whole
    freshness suite. Each is a property the build depends on and nothing asserted:

      * **N03** `recheck_after_answer` never reindexing/saving the baseline — the POST-answer loop
        guard, the mirror of the pre-answer one `_advance_baseline` covers.
      * **N07** `drain_dirty` replaced by `read_dirty` — signal 1 peeked and never cleared, so one
        in-harness edit rebuilds the graph on every query for the rest of the session. **No test
        anywhere exercised signal 1 end to end** (`grep -c dirty` over this file returned 1, the
        docstring).
      * **N02** `_source_only`'s extension filter removed — survived only because a second,
        unreachable `.mokata` guard was standing beside it (§7f). That guard is deleted now, so this
        is the one defence and it must grade.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = _repo(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_the_post_answer_recheck_ADVANCES_the_baseline_so_it_fires_once(self):
        """N03. Without the reindex, every subsequent answer that references the file rechecks it,
        re-invalidates the AST and re-queries — a rebuild per query, from the other side."""
        c = F.FreshnessController(root=self.root, session_id="a28-n03")
        c.ensure_fresh(_Layer())
        with open(os.path.join(self.root, "a.py"), "a", encoding="utf-8") as fh:
            fh.write("\ndef post(x):\n    return x\n")
        self.assertTrue(c.recheck_after_answer(_Layer(), _Result("a.py")),
                        "the first recheck must catch it")
        self.assertFalse(c.recheck_after_answer(_Layer(), _Result("a.py")),
                         "the SECOND must not — the baseline was advanced by the first, and without "
                         "that this rebuilds on every answer that mentions the file")

    def test_signal_1_is_DRAINED_not_peeked(self):
        """N07, and it is the first end-to-end exercise of signal 1 in this file. A peek leaves the
        record in place, so the edit is re-reported forever — the same loop class as B02/B04,
        arriving in the one signal no test had ever driven."""
        F.mark_dirty(self.root, ["a.py"], session_id="a28-n07-harness")
        first = F.FreshnessController(root=self.root,
                                      session_id="a28-n07-harness")._reconcile(_Layer())
        self.assertIn("a.py", first.changed, "the dirty-set record must be READ")
        again = F.FreshnessController(root=self.root,
                                     session_id="a28-n07-harness")._reconcile(_Layer())
        self.assertTrue(again.fresh,
                        "the dirty set must be CLEARED by the read — a peek rebuilds the graph on "
                        "every query for the rest of the session")
        self.assertEqual([], F.read_dirty(self.root, session_id="a28-n07-harness"))

    def test_the_extension_filter_is_the_one_defence_and_it_grades(self):
        """N02. `_source_only` had two defences and NEITHER was individually gradable (§7f). The
        unreachable one is deleted, so this pins the one that does the work — including the actual
        offender the loop was found on, mokata's own transient state file."""
        self.assertEqual(["src/a.py"], F._source_only([
            "src/a.py",
            ".mokata/temp_local/state/graph_freshness_index__x.json",   # the real offender
            "README.md", "poetry.lock", "docs/build/84.md", "a.json",
        ]))

    def test_the_control_every_language_the_graph_indexes_survives_the_filter(self):
        """The control for the assertion above: a filter that returned [] would satisfy it. The
        extension set is `languages.SOURCE_EXTENSIONS`, so the two cannot drift."""
        from mokata import languages
        paths = ["x%s" % ext for ext in languages.SOURCE_EXTENSIONS]
        self.assertEqual(paths, F._source_only(paths))


class TheSecondConstructionPathIsGone(unittest.TestCase):
    """Fix C, doc 85 §7d: pre-1.0 there is no deprecation — scaffolding is deleted."""

    def test_for_root_no_longer_exists(self):
        """`FreshnessController.for_root` had ZERO callers: none in `src/`, none in `tests/`. The
        doc-84 row filing it said "its only caller anywhere is a test" — that was wrong; the one
        mention was `test_d5_sweep_register`'s register TABLE naming its `except Exception`, which
        describes a handler rather than calling it (§7i). This guard is what stops it coming back
        as a second construction path that bypasses `for_surface`'s ladder resolution."""
        self.assertFalse(hasattr(F.FreshnessController, "for_root"),
                         "for_root was deleted at 0.0.21 stage 14 — a new construction path must "
                         "resolve its session through the ladder, as `for_surface` does")
        self.assertTrue(hasattr(F.FreshnessController, "for_surface"),
                        "the CONTROL: for_surface is the one that stays")


class EVERY_WRITER_OF_A_RECORD_RECORDS_THE_CTIME(unittest.TestCase):
    """🔴 REVIEW FINDING 4-7, §7f and §7j. The ctime CLAUSE and the ctime FIELD are two
    separate things to get wrong, and the first fix graded only the clause: the mutant that deleted
    `file_ctime(ab)` from `KnowledgeIndex.build` SURVIVED a 56-test module. The clause then compares
    a recorded 0.0, which by design has no opinion — so dropping the write silently disarms the
    whole signal and every assertion above still passes.

    There are FOUR sites that construct a live record, and `test_4_1`/`test_4_3` only ever reach
    freshness's own two. Each site gets its own offender here, so none of them is graded by
    proximity to another.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = _repo(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _ctime_of(self, entries, rel="a.py"):
        self.assertIn(rel, entries, "the premise: this writer produced a record for %s" % rel)
        return float(getattr(entries[rel], "ctime", 0) or 0)

    def test_index_BUILD_records_it(self):
        idx = KnowledgeIndex()
        built = idx.build(self.root)
        self.assertIn("a.py", built, "the premise: build indexed the file")
        self.assertGreater(self._ctime_of(idx.entries), 0.0,
                           "KnowledgeIndex.build is the cold-start writer; a record it writes "
                           "without a ctime makes the 4-1 clause compare 0.0 forever, which by "
                           "design is 'no opinion' — the signal is disarmed, not noisy")

    def test_index_REINDEX_records_it(self):
        idx = KnowledgeIndex()
        idx.build(self.root)
        with open(os.path.join(self.root, "a.py"), "w", encoding="utf-8") as fh:
            fh.write("def helper(x):\n    return x + 2\n")
        self.assertEqual(["a.py"], idx.reindex(self.root),
                         "the premise: reindex is what rewrote this record")
        self.assertGreater(self._ctime_of(idx.entries), 0.0,
                           "reindex REPLACES the record, so a reindex that drops the field "
                           "downgrades an entry build got right — the one direction a cold-start "
                           "test can never see")

    def test_the_FRESHNESS_cold_walk_records_it(self):
        ctl = F.FreshnessController(root=self.root, session_id="a28-ctime-sites")
        ctl.ensure_fresh(_Layer())
        base = ctl._load_index()
        self.assertGreater(self._ctime_of(getattr(base, "entries", {})), 0.0,
                           "freshness builds its own baseline rather than reusing "
                           "KnowledgeIndex.build, so it is a THIRD writer (§7f)")

    def test_the_BASELINE_ADVANCE_records_it(self):
        ctl = F.FreshnessController(root=self.root, session_id="a28-ctime-sites")
        ctl.ensure_fresh(_Layer())
        ab = os.path.join(self.root, "a.py")
        with open(ab, "w", encoding="utf-8") as fh:
            fh.write("def helper(x):\n    return x + 3\n")
        ctl.ensure_fresh(_Layer())                   # reconciles, and re-stamps the record
        base = ctl._load_index()
        recorded = self._ctime_of(getattr(base, "entries", {}))
        self.assertGreater(recorded, 0.0,
                           "_advance_baseline is the FOURTH writer and the one that runs on every "
                           "reconciliation: a record it re-stamps without a ctime means the signal "
                           "works once and is disarmed from the second edit onwards")
        self.assertAlmostEqual(recorded, os.stat(ab).st_ctime, places=5,
                               msg="and it must record the CURRENT ctime, not carry the stale one "
                                   "forward — a carried-forward value re-drifts on every pass")



class SIGNAL_2b_IS_BOUNDED_AND_SAYS_SO(unittest.TestCase):
    """🔴 REVIEW FINDING 4-8. 2b was the only signal in `_reconcile` with no cap and no note,
    and it is the one that HASHES. Every other signal is bounded and discloses the bound when it
    bites. A branch switch or a `git stash pop` moving thousands of files paid a hash per file
    inside a read — the ∝ repo-size cost the stat-only design was argued for avoiding, arriving
    by the back door with nothing on the answer to say the read had become expensive.

    ⛔ AND THE DIRECTION OF THE CAP IS THE WHOLE POINT (§7e). Dropping the candidates it cannot
    afford would make an unaffordable confirmation read as "clean" — an instrument failing open,
    which is a false green. The over-cap candidates are rebuilt UNCONFIRMED instead: a redundant
    rebuild, never an unreported edit.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = _repo(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self._cap = F.BASELINE_DRIFT_HASH_CAP
        self.addCleanup(setattr, F, "BASELINE_DRIFT_HASH_CAP", self._cap)

    def _pass(self):
        return F.FreshnessController(root=self.root,
                                     session_id="a28-2b-cap").ensure_fresh(_Layer())

    def _record_for(self, rel):
        """A record whose stat fields are all wrong, so the path IS a drift candidate."""
        return IndexEntry(rel, "0" * 64, 1.0, 1, 1.0)

    def test_the_candidates_past_the_cap_are_REBUILT_not_assumed_clean(self):
        """⭐ THE SHARP ARM, and it needs every candidate to hash CLEAN so the two directions
        cannot be confused: under the cap a clean hash means NOT changed, over the cap there is no
        hash at all, so if the over-cap paths appear in `changed` it is the cap putting them there
        and nothing else. A fail-open cap makes this set EMPTY."""
        for i in range(4):
            with open(os.path.join(self.root, "m%d.py" % i), "w", encoding="utf-8") as fh:
                fh.write("x = %d\n" % i)
        self._pass()                                  # index all five files
        ctl = F.FreshnessController(root=self.root, session_id="a28-2b-cap")
        base = ctl._load_index()
        for rel in list(base.entries):
            e = base.entries[rel]                     # keep the HASH, fault the stat fields only:
            base.entries[rel] = IndexEntry(rel, e.content_hash, 1.0, 1, 1.0)
        ctl._save_index(base)
        drifted = F.baseline_drift(ctl._load_index(), self.root)
        self.assertEqual(5, len(drifted), "the premise: every indexed path is a candidate")

        F.BASELINE_DRIFT_HASH_CAP = 1
        out = self._pass()
        self.assertEqual(sorted(drifted[1:]), sorted(out.changed),
                         "the four paths past the cap were rebuilt WITHOUT a hash — every one of "
                         "them hashes clean, so a cap that dropped them would report fresh, which "
                         "is an instrument failing open (§7e)")
        self.assertNotIn(drifted[0], out.changed,
                         "and the CONTROL in the same assertion: the one candidate that WAS "
                         "affordable hashed clean and is therefore not changed — so the set above "
                         "is the cap's doing, not a pass that reports everything")

    def test_the_cap_puts_an_HONEST_COSTED_NOTE_on_the_answer(self):
        for i in range(4):
            with open(os.path.join(self.root, "n%d.py" % i), "w", encoding="utf-8") as fh:
                fh.write("y = %d\n" % i)
        self._pass()                                  # index them
        F.BASELINE_DRIFT_HASH_CAP = 1
        base = F.FreshnessController(root=self.root, session_id="a28-2b-cap")._load_index()
        for rel in list(base.entries):
            e = base.entries[rel]
            base.entries[rel] = IndexEntry(rel, e.content_hash, 1.0, 1, 1.0)   # all four drift
        F.FreshnessController(root=self.root, session_id="a28-2b-cap")._save_index(base)
        out = self._pass()
        self.assertIn("baseline-drift cap", out.note,
                      "a read that just became expensive must SAY so — every other capped signal "
                      "in this reconcile does")
        self.assertTrue(out.capped, "and it is a cap, which is the costed-note flag")
        self.assertFalse(out.fresh,
                         "⛔ THE DIRECTION: the unconfirmed candidates are CHANGED. A cap that "
                         "reported fresh would be the fail-open §7e names")

    def test_the_CONTROL_under_the_cap_there_is_no_note_and_no_nagging(self):
        out = self._pass()
        self.assertNotIn("baseline-drift cap", out.note,
                         "the control: a cap that fires when it was not hit is the nagging this "
                         "stage has argued against twice (§7i)")
        self.assertNotIn("baseline-drift cap", self._pass().note)

    def test_a_capped_pass_SETTLES_rather_than_re_drifting_every_time(self):
        """⚠ THIS TEST USED TO CLAIM MORE THAN IT MEASURED, and the mutant batch is what caught it.
        It was written as "an unaffordable candidate is not re-stamped as settled", on the theory
        that re-stamping an unhashed path would record it as current and the drift would never
        report again. False: `_advance_baseline` hashes the file itself, and every unaffordable
        candidate is also in `changed`, so the rebuild arm advances the identical records. The
        mutant that widens `drift_settled` back to every candidate is EQUIVALENT — it is filed as
        equivalent in the batch rather than carried as a survivor.

        What is left is the property that is real and worth a pin: a capped pass must still SETTLE.
        An unconfirmed rebuild that did not advance the baseline would re-drift on every query for
        the life of the repo — the forever-loop this signal has already produced twice."""
        for i in range(4):
            with open(os.path.join(self.root, "p%d.py" % i), "w", encoding="utf-8") as fh:
                fh.write("z = %d\n" % i)
        self._pass()
        ctl = F.FreshnessController(root=self.root, session_id="a28-2b-cap")
        base = ctl._load_index()
        for rel in list(base.entries):
            e = base.entries[rel]
            base.entries[rel] = IndexEntry(rel, e.content_hash, 1.0, 1, 1.0)
        ctl._save_index(base)
        F.BASELINE_DRIFT_HASH_CAP = 1
        self._pass()
        self.assertTrue(self._pass().fresh,
                        "it settles — the unconfirmed paths were rebuilt, so their records were "
                        "advanced by the REBUILD arm rather than by the settled arm")


class THE_TOMBSTONES_DO_NOT_GROW_FOREVER(unittest.TestCase):
    """🔴 REVIEW FINDING 4-8(b). A reconciled deletion is recorded as an ABSENT entry so a
    byte-identical recreate is still noticed (finding A5) — and NOTHING ever removed one. Every file
    ever deleted in the repo's life stayed in the persisted baseline and was `stat`ed on every 2b
    pass, so the signal's cost grew with the repo's HISTORY rather than its contents, monotonically,
    in a file the user cannot see.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = _repo(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self._cap = F.BASELINE_TOMBSTONE_CAP
        self.addCleanup(setattr, F, "BASELINE_TOMBSTONE_CAP", self._cap)

    def _ctl(self):
        return F.FreshnessController(root=self.root, session_id="a28-tombstones")

    def _base_with(self, tombs, reals=()):
        base = KnowledgeIndex()
        for rel in tombs:
            base.entries[rel] = IndexEntry(rel, F.ABSENT, 0.0, -1)
        for rel in reals:
            base.entries[rel] = IndexEntry(rel, "0" * 64, 1.0, 1, 1.0)
        return base

    def test_the_OLDEST_tombstones_are_the_ones_dropped(self):
        F.BASELINE_TOMBSTONE_CAP = 3
        base = self._base_with(["t%d.py" % i for i in range(6)])
        dropped = F.FreshnessController._prune_tombstones(base)
        self.assertEqual(["t0.py", "t1.py", "t2.py"], dropped,
                         "insertion order is deletion order, so oldest-first is "
                         "least-recently-deleted-first — the ones whose recreate is least likely")
        self.assertEqual(["t3.py", "t4.py", "t5.py"], sorted(base.entries),
                         "and the newest survive, at exactly the cap")

    def test_a_REAL_record_is_never_counted_or_dropped_by_this_cap(self):
        """§7j — the cap types its own scope. A real entry is the index doing its job; counting it
        here would prune live records the moment a repo got large, which is a data-loss bug wearing
        a cap's clothes."""
        F.BASELINE_TOMBSTONE_CAP = 2
        base = self._base_with(["t0.py", "t1.py"], reals=["a.py", "b.py", "c.py"])
        self.assertEqual([], F.FreshnessController._prune_tombstones(base),
                         "five entries, two tombstones, cap 2 — nothing to drop")
        self.assertEqual(5, len(base.entries), "and nothing was dropped")

    def test_the_CONTROL_under_the_cap_nothing_is_pruned(self):
        F.BASELINE_TOMBSTONE_CAP = 10
        base = self._base_with(["t%d.py" % i for i in range(4)])
        self.assertEqual([], F.FreshnessController._prune_tombstones(base))
        self.assertEqual(4, len(base.entries))

    def test_what_the_cap_DROPS_is_disclosed_on_the_answer(self):
        """⛔ A dropped tombstone REOPENS the A5 gap for that path, and a reopened gap that
        nobody is told about is the §7g shape: the answer looks the same as one with no gap."""
        F.BASELINE_TOMBSTONE_CAP = 1
        ctl = self._ctl()
        ctl.ensure_fresh(_Layer())
        base = ctl._load_index()
        for i in range(4):
            base.entries["old%d.py" % i] = IndexEntry("old%d.py" % i, F.ABSENT, 0.0, -1)
        ctl._save_index(base)
        with open(os.path.join(self.root, "a.py"), "w", encoding="utf-8") as fh:
            fh.write("def helper(x):\n    return x + 77\n")
        out = self._ctl().ensure_fresh(_Layer())      # reconciles ⇒ advances ⇒ prunes
        self.assertIn("tombstone cap", out.note,
                      "the user is told the signal lost records, and which")
        self.assertIn("old0.py", out.note, "and the note NAMES what it dropped")
        self.assertTrue(out.capped)

    def test_the_CONTROL_an_ordinary_reconcile_says_nothing_about_tombstones(self):
        ctl = self._ctl()
        ctl.ensure_fresh(_Layer())
        with open(os.path.join(self.root, "a.py"), "w", encoding="utf-8") as fh:
            fh.write("def helper(x):\n    return x + 78\n")
        out = self._ctl().ensure_fresh(_Layer())
        self.assertNotIn("tombstone cap", out.note,
                         "the control: the note must ride a pass where the cap actually bit")

    def test_the_A5_TOMBSTONE_still_works_under_the_cap(self):
        """The regression the cap could cause: pruning must not cost the behaviour it bounds."""
        ctl = self._ctl()
        ctl.ensure_fresh(_Layer())
        body = "def kept():\n    return 1\n"
        with open(os.path.join(self.root, "kept.py"), "w", encoding="utf-8") as fh:
            fh.write(body)
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-qm", "add kept")
        self._ctl().ensure_fresh(_Layer())
        os.remove(os.path.join(self.root, "kept.py"))
        self.assertIn("kept.py", self._ctl().ensure_fresh(_Layer()).changed)
        with open(os.path.join(self.root, "kept.py"), "w", encoding="utf-8") as fh:
            fh.write(body)                            # byte-identical
        self.assertIn("kept.py", self._ctl().ensure_fresh(_Layer()).changed,
                       "the tombstone is what makes a recreate visible, and the cap did not bite")



if __name__ == "__main__":
    unittest.main()
