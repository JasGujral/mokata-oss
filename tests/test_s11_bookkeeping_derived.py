"""Stage 11 — BOOKKEEPING-SWEEP-MANUAL: the status column becomes a computed output.

WHAT THE BOOKKEEPING DOES TODAY (grounded before replacing anything):

  * doc 02's 0.0.16 verification sweep is a markdown table whose last column, "Blocking the
    0.0.16 cut?", is TYPED BY A HUMAN from the sweep author's notes. It reported `TODELETE-LEAK`
    🔴 when `9ec1b05` had already closed it.
  * `scripts/check-tracker-tables.py` is the only mechanical doc gate, and it checks table SHAPE
    (cell count vs header), not the TRUTH of any cell. A row can be perfectly shaped and wholly
    wrong; that is exactly what happened.
  * `test_stage68_supply_chain.py` pins the CONTROLS (both `--exclude` and `INTERNAL_PATHS` cover
    every REQUIRED path) but says nothing about what any doc CLAIMS about them.

So the control was pinned and the doc was shaped, and the sentence connecting them was trusted.
This file derives that sentence instead, and then holds the doc to it.

THREE OUTCOMES, THREE REPRESENTATIONS — the row's own spec, and §7g before §7g existed:
"undecidable rows render UNKNOWN, never default-green". `_mirror_bookkeeping` returns GREEN, RED
or UNDECIDABLE, `verdict` is DERIVED from `basis` rather than stored beside it, and an
UNDECIDABLE that cannot say WHY is refused at construction.

§7i: the real controls cover every required path, so every real-tree derivation is GREEN and
grades nothing on its own. TestSyntheticallyBrokenControls supplies scripts broken one way each.

MIRROR BOUNDARY (stage 28, SHIPPED-TEST-READS-INTERNAL-FILE). `tests/` ships to the public mirror;
`scripts/sync-public.sh` and `docs/build/` — the two things this file derives FROM — do not. As
first written, the four real-tree assertions here failed or errored on the public subset: green in
this repo, broken in the one users clone, and `run_public_subset_preflight` refused the cut over
it. The guard that fixes it (`@unittest.skipUnless(os.path.exists(...), ...)`) had existed at
`test_stage68_supply_chain.py:369` since stage 68; this file was written straight past it. So:

  * the two real-tree batteries carry the CLASS DECORATOR — never a `setUpClass` skip. That
    matters twice: a setUpClass skip collapses the class into ONE skip and drops its tests out of
    `Ran N` (measured: `Ran 0 ... skipped=1` against the decorator's `Ran 2 ... skipped=2`), and
    `TestTheDocIsHeldToTheDerivation`'s setUpClass READ the file before any skip could fire —
    which is exactly how it errored on the subset rather than skipping;
  * the SYNTHETIC batteries stay unguarded and keep running on the mirror, because their whole
    point is that they supply their own scripts and depend on no file at all. Nothing was
    weakened to be portable;
  * `TestTheControlsAreAbsentOnlyBecauseOfTheMirrorBoundary` runs UNGUARDED on both sides, so a
    DELETED control cannot hide inside the skip.

`tests/_shipped_reads.py` derives that property over the whole shipped corpus, so the next file to
read an internal path fails there instead of at the next cut.
"""

import os
import re
import unittest

from _support import sample_manifest_data  # noqa: F401  (path-fix side-effect)

import _mirror_bookkeeping as mb

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC02 = os.path.join(ROOT, "docs", "build", "02-mokata-build-status.md")
SYNC_SH = os.path.join(ROOT, "scripts", "sync-public.sh")
RELEASE_SH = os.path.join(ROOT, "scripts", "release.sh")

# The internal paths the mirror boundary must hold back. Mirrors test_stage68's REQUIRED set —
# the same controls, now read for a STATUS rather than for a pass/fail.
REQUIRED = (
    "docs/build", "docs/launch", "docs/marketing", "CLAUDE.md",
    "scripts/sync-public.sh", "scripts/release.sh",
    "_to_delete", "editors/vscode/node_modules", "editors/vscode/out",
)

# doc 02 row name -> the control path(s) whose derived status that row is CLAIMING.
DOC_ROWS = {
    "TODELETE-LEAK": ("_to_delete",),
    "NODE_MODULES-LEAK": ("editors/vscode/node_modules", "editors/vscode/out"),
}

_MINIMAL = (
    "rsync -a \\\n"
    "  --exclude='docs/build/' \\\n"
    "  --exclude='_to_delete/' \\\n"
    "  \"$SRC/\" \"$DST/\"\n"
    "INTERNAL_PATHS=(\n"
    "  docs/build\n"
    "  _to_delete\n"
    ")\n"
    "for p in \"${INTERNAL_PATHS[@]}\"; do :; done\n"
)


def _doc_rows():
    """doc 02's sweep rows, keyed on the row's FIRST cell.

    Keyed on the first cell, NOT on "the name appears in the line": the NODE_MODULES-LEAK row's
    prose contrasts itself with TODELETE-LEAK at length, so a line-level search returns that row
    for both names and would compare the wrong status. The same substring hazard the whole
    0.0.17 pin work is about, met here in the reader rather than in the pin.
    """
    rows = {}
    with open(DOC02, encoding="utf-8") as fh:
        for line in fh:
            if not line.startswith("|"):
                continue
            cells = [c.strip() for c in re.split(r"(?<!\\)\|", line.rstrip("\n"))][1:-1]
            if len(cells) < 2:
                continue
            for name in DOC_ROWS:
                if cells[0].startswith("**" + name) or cells[0].startswith(name):
                    rows[name] = cells[-1]
    return rows


def _doc_claim(cell):
    """CLOSED / OPEN / UNREADABLE — the doc side gets three representations too."""
    if "✅" in cell and "NO" in cell:
        return "CLOSED"
    if "🔴" in cell or "YES" in cell:
        return "OPEN"
    return "UNREADABLE"


class TestTheDerivationIsWellFormed(unittest.TestCase):
    """The vocabulary, before any use of it."""

    def test_every_basis_maps_to_exactly_one_verdict(self):
        for basis in (mb.BASIS_VERIFIED_BOTH, mb.BASIS_EXCLUDE_ONLY, mb.BASIS_GUARD_ONLY,
                      mb.BASIS_VERIFIED_NEITHER, mb.BASIS_NO_EXCLUDE_CLAUSES,
                      mb.BASIS_NO_GUARD_ARRAY, mb.BASIS_SCRIPT_UNREADABLE,
                      mb.BASIS_PATTERN_COVERAGE):
            self.assertIn(mb._VERDICT_OF[basis], (mb.GREEN, mb.RED, mb.UNDECIDABLE))

    def test_verdict_is_derived_from_basis_not_stored_beside_it(self):
        """Two fields that can disagree eventually do — which is how a written status column
        drifts from its control in the first place."""
        self.assertNotIn("verdict", mb.ControlResolution.__slots__)
        self.assertEqual(mb.GREEN, mb.ControlResolution("x", mb.BASIS_VERIFIED_BOTH).verdict)
        self.assertEqual(mb.RED, mb.ControlResolution("x", mb.BASIS_GUARD_ONLY).verdict)

    def test_an_unknown_basis_is_refused(self):
        with self.assertRaises(ValueError):
            mb.ControlResolution("x", "probably_fine")

    def test_an_UNDECIDABLE_that_cannot_say_why_is_refused(self):
        """The row's requirement, enforced at construction rather than hoped for: an undecidable
        with no reason is a shrug, and a reader rounds a shrug to green."""
        for basis in sorted(mb.UNDECIDABLE_BASES):
            with self.assertRaises(ValueError, msg="basis %r accepted an empty reason" % basis):
                mb.ControlResolution("x", basis)

    def test_a_decided_resolution_needs_no_reason(self):
        self.assertTrue(mb.ControlResolution("x", mb.BASIS_VERIFIED_BOTH).decided)
        self.assertFalse(
            mb.ControlResolution("x", mb.BASIS_SCRIPT_UNREADABLE, "gone").decided)


class TestTheControlsAreAbsentOnlyBecauseOfTheMirrorBoundary(unittest.TestCase):
    """★ The companion the two guards below cannot do without (stage 28).

    `sync-public.sh` and `docs/build/` are excluded from the public mirror, so the guards must let
    this file run there. Left alone, that same skip would swallow a DELETED sync-public.sh
    anywhere else: both real-tree batteries would vanish, the run would report OK, and "the file
    is excluded from the mirror" and "someone deleted the mirror boundary" would share a green.
    `release.sh` is held back by the SAME two controls, so on any tree carrying one, the others
    must be there too.

    Deliberately UNGUARDED: it is the one thing here that must run on both sides of the boundary.
    """

    def test_the_controls_are_present_wherever_release_sh_is(self):
        if not os.path.exists(RELEASE_SH):
            self.skipTest("public subset — no internal path ships, as intended")
        self.assertTrue(
            os.path.exists(SYNC_SH),
            "scripts/release.sh is present, so this is the PRIVATE tree — but "
            "scripts/sync-public.sh is gone. Every derivation below reads it; the batteries "
            "would SKIP rather than fail, and the mirror boundary would go unpinned.")
        self.assertTrue(
            os.path.exists(DOC02),
            "scripts/release.sh is present, so this is the PRIVATE tree — but doc 02 is gone. "
            "The doc-vs-controls battery below would SKIP rather than fail, and the typed status "
            "column would be trusted again by default.")


@unittest.skipUnless(os.path.exists(SYNC_SH),
                     "sync-public.sh is dev-only, excluded from the public mirror")
class TestAgainstTheRealControls(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.script = mb.read_script(ROOT)

    def test_the_script_is_readable_at_all(self):
        self.assertIsNotNone(
            self.script, "scripts/sync-public.sh could not be read — every derivation below "
                         "would be UNDECIDABLE and this file would prove nothing")

    def test_every_required_path_derives_GREEN_from_both_controls(self):
        for r in mb.resolve_all(REQUIRED, self.script):
            self.assertEqual(
                mb.GREEN, r.verdict,
                "%s derives %s (basis=%s): %s" % (r.path, r.verdict, r.basis, r.render()))
            self.assertEqual(mb.BASIS_VERIFIED_BOTH, r.basis)

    def test_the_derivation_reads_real_entries_and_not_an_empty_set(self):
        """Anti-vacuity: an empty entry set would derive RED for everything, which would look
        like vigilance while meaning the parse failed."""
        self.assertGreaterEqual(len(mb.exclude_entries(self.script)), 20)
        self.assertGreaterEqual(len(mb.guard_entries(self.script)), 20)


class TestSyntheticallyBrokenControls(unittest.TestCase):
    """§7i — the real controls are intact, so each defect is supplied."""

    def test_intact_minimal_script_derives_GREEN(self):
        self.assertEqual(mb.GREEN, mb.resolve("_to_delete", _MINIMAL).verdict)

    def test_dropping_the_exclude_only_is_RED_and_says_which_control_went(self):
        broken = _MINIMAL.replace("  --exclude='_to_delete/' \\\n", "")
        r = mb.resolve("_to_delete", broken)
        self.assertEqual(mb.RED, r.verdict)
        self.assertEqual(mb.BASIS_GUARD_ONLY, r.basis)
        self.assertIn("rsync still copies it", r.render())

    def test_dropping_the_guard_only_is_RED_and_says_which_control_went(self):
        broken = _MINIMAL.replace("  _to_delete\n", "")
        r = mb.resolve("_to_delete", broken)
        self.assertEqual(mb.RED, r.verdict)
        self.assertEqual(mb.BASIS_EXCLUDE_ONLY, r.basis)
        self.assertIn("hard-guard", r.render())

    def test_dropping_both_is_RED_and_says_nothing_covers_it(self):
        broken = _MINIMAL.replace("  --exclude='_to_delete/' \\\n", "").replace(
            "  _to_delete\n", "")
        r = mb.resolve("_to_delete", broken)
        self.assertEqual(mb.BASIS_VERIFIED_NEITHER, r.basis)
        self.assertIn("NEITHER", r.render())

    def test_a_comment_naming_the_path_does_not_satisfy_either_control(self):
        """TD-2, as a corpus. Both controls are documented IN PLACE and the comments name the
        very paths under pin, so a substring reader reports GREEN exactly when the entry is gone.
        """
        commented = _MINIMAL.replace(
            "  --exclude='_to_delete/' \\\n",
            "  # --exclude='_to_delete/' — scratch dir, see TODELETE-LEAK (_to_delete)\n"
        ).replace("  _to_delete\n", "  # _to_delete lives here; see the note above\n")
        r = mb.resolve("_to_delete", commented)
        self.assertEqual(
            mb.BASIS_VERIFIED_NEITHER, r.basis,
            "a commented-out entry satisfied a control — the derivation is reading prose")

    def test_a_missing_script_is_UNDECIDABLE_not_GREEN_and_not_RED(self):
        """THE load-bearing case. Absence of evidence must not render as either verdict."""
        r = mb.resolve("_to_delete", None)
        self.assertEqual(mb.UNDECIDABLE, r.verdict)
        self.assertEqual(mb.BASIS_SCRIPT_UNREADABLE, r.basis)
        self.assertTrue(r.render().startswith("UNKNOWN"))

    def test_a_script_with_no_exclude_clauses_is_UNDECIDABLE(self):
        r = mb.resolve("_to_delete", "INTERNAL_PATHS=(\n  _to_delete\n)\n")
        self.assertEqual(mb.UNDECIDABLE, r.verdict)
        self.assertEqual(mb.BASIS_NO_EXCLUDE_CLAUSES, r.basis)

    def test_a_script_with_no_guard_array_is_UNDECIDABLE(self):
        r = mb.resolve("_to_delete", "rsync --exclude='_to_delete/' a b\n")
        self.assertEqual(mb.UNDECIDABLE, r.verdict)
        self.assertEqual(mb.BASIS_NO_GUARD_ARRAY, r.basis)

    def test_an_unterminated_guard_array_is_UNDECIDABLE(self):
        r = mb.resolve("_to_delete",
                       "rsync --exclude='_to_delete/' a b\nINTERNAL_PATHS=(\n  _to_delete\n")
        self.assertEqual(mb.BASIS_NO_GUARD_ARRAY, r.basis)

    def test_glob_only_coverage_is_UNDECIDABLE_and_names_the_pattern(self):
        """Neither GREEN (guessing the glob matches) nor RED (inventing a leak). It asks."""
        script = ("rsync --exclude='.env*' a b\nINTERNAL_PATHS=(\n  .env\n)\n")
        r = mb.resolve(".env", script)
        self.assertEqual(mb.UNDECIDABLE, r.verdict)
        self.assertEqual(mb.BASIS_PATTERN_COVERAGE, r.basis)
        self.assertIn(".env*", r.detail)

    def test_the_enforcement_loop_below_the_array_does_not_count_as_the_array(self):
        """The array literal, not "everything after it" — the loop and its commentary name
        internal paths and would satisfy a membership check on their own."""
        script = ("rsync --exclude='_to_delete/' a b\n"
                  "INTERNAL_PATHS=(\n  docs/build\n)\n"
                  "for p in \"${INTERNAL_PATHS[@]}\"; do echo _to_delete; done\n")
        self.assertEqual(mb.BASIS_EXCLUDE_ONLY, mb.resolve("_to_delete", script).basis)


@unittest.skipUnless(os.path.exists(SYNC_SH) and os.path.exists(DOC02),
                     "sync-public.sh and docs/build/ are dev-only, excluded from the mirror")
class TestTheDocIsHeldToTheDerivation(unittest.TestCase):
    """The point of the stage: doc 02's typed status stops being trusted.

    This does not rewrite the doc. It makes the doc CHECKABLE — the same move
    check-tracker-tables.py made for table shape, applied to what a cell claims. If either side
    drifts, in either direction, this reds and names which.
    """

    @classmethod
    def setUpClass(cls):
        cls.script = mb.read_script(ROOT)
        cls.rows = _doc_rows()

    def test_every_tracked_row_was_found_in_the_doc(self):
        missing = sorted(set(DOC_ROWS) - set(self.rows))
        self.assertEqual(
            [], missing,
            "these rows are no longer locatable in doc 02, so nothing is holding their status "
            "to the controls: %s" % ", ".join(missing))

    def test_no_tracked_row_status_is_unreadable(self):
        """An unreadable cell is UNDECIDABLE on the doc side, and must not pass quietly."""
        for name, cell in sorted(self.rows.items()):
            self.assertNotEqual(
                "UNREADABLE", _doc_claim(cell),
                "doc 02's status cell for %s cannot be read as open or closed, so it cannot be "
                "compared to the derived status: %r" % (name, cell))

    def test_the_doc_status_agrees_with_the_derived_status(self):
        for name, paths in sorted(DOC_ROWS.items()):
            resolutions = mb.resolve_all(paths, self.script)
            undecided = [r for r in resolutions if not r.decided]
            self.assertEqual(
                [], undecided,
                "%s cannot be derived (%s) — the doc's claim is unverifiable, which is a "
                "finding, not a pass" % (name, [r.render() for r in undecided]))
            derived = "CLOSED" if all(r.verdict == mb.GREEN for r in resolutions) else "OPEN"
            claimed = _doc_claim(self.rows[name])
            self.assertEqual(
                derived, claimed,
                "doc 02 claims %s is %s but the controls in sync-public.sh derive %s.\n"
                "  derived: %s\n  doc cell: %r\n"
                "One of the two is stale. The controls are the fact; the cell is the claim."
                % (name, claimed, derived,
                   [r.render() for r in resolutions], self.rows[name]))


if __name__ == "__main__":
    unittest.main()
