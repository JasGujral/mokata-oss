"""No tracked surface may state a PostgreSQL floor other than the one the code declares.

0.0.18 stage 20, closing `PG-FLOOR-RATIFIED-NOWHERE-BUT-THE-ADR` (doc 84 §1) as a CLASS rather
than as ten edits. The instances are fixed in the same commit as this file; without the guard the
next ratification reproduces them, because the failure was never that someone forgot a file — it
was that a decision recorded in one document had no mechanical relationship to the nine sentences
that repeat it.

⚠ **THE TWO WEAK SHAPES THIS FILE IS BUILT TO AVOID**, both of them things that already happened
in this repo:

  * **A sweep that would pass over an empty corpus.** `iter_tracked_files` raises `NotACheckout`
    rather than degrading to a disk walk, so "the index could not be read" cannot arrive here
    disguised as "nothing to check". `TestTheDetectorIsNotVacuous` then grades the DETECTOR itself
    against synthetic offenders, because a clean tree makes every other test in this file pass by
    saying nothing (`tests/_mutant_driver_contract.py`'s constraint 1, followed not re-derived).
  * **A sweep that grades only what it can already see.** The corpus is the whole git index minus
    an explicit, reasoned, PATH-keyed deny-list — not a list of directories someone remembered.
    That is `REMOVAL-DRIFT-CORPUS-EXCLUDES-INTERNAL-TREES` (doc 84), whose lesson cost a client
    deck three releases of a false claim, and it is why `docs/talks/` is IN scope here: "internal"
    is a statement about the tree, not about the audience.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _pg_floor as floor                                                    # noqa: E402
from _support import iter_tracked_files                                      # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The superseded major, as a bare number with no comparator beside it — so this file states the
# offending SHAPE without becoming an instance of it in its own corpus.
_STALE_MAJOR = 14


def _corpus():
    # CORPUS: THE INDEX. The question is "what does this repository PUBLISH a floor claim in",
    # and the answer is a property of what is committed — an untracked scratch file carrying a
    # stale sentence is nobody's problem and would red this guard on one machine only. It also
    # makes a nested checkout structurally invisible rather than pruned by hand
    # (`CORPUS-IS-A-FILESYSTEM-WALK-NOT-THE-INDEX`, doc 84).
    return {rel: abs_ for rel, abs_ in iter_tracked_files(REPO)}


class TestTheDeclarationIsTheSingleSource(unittest.TestCase):
    """The floor is a number the code states, not a sentence a document has to remember."""

    def test_the_declaration_is_readable_and_ordered(self):
        got = floor.declared_floor(REPO)
        self.assertIsNotNone(
            got, "%s no longer declares MIN_PG_MAJOR and TARGET_PG_MAJOR — every assertion below "
                 "grades the corpus against it, so this is the one failure that must not be read "
                 "as a passing sweep" % floor.DECLARATION_RELPATH)
        low, target = got
        self.assertLess(low, target,
                        "the floor must sit below the target; got floor=%d target=%d" % (low, target))

    def test_an_unreadable_declaration_answers_none_rather_than_a_default(self):
        """§7g: "could not read the floor" and "the floor is 15" must never share a value."""
        self.assertIsNone(floor.declared_floor(os.path.join(REPO, "no", "such", "tree")))

    def test_half_a_declaration_is_not_a_declaration(self):
        """Both names are required, and the ONE-MISSING case is the one a whole-tree absence
        never exercises: a file that still parses, still answers, and answers with a target
        nobody declared. Planted in both directions so neither name can quietly become optional."""
        import tempfile
        both = "MIN_PG_MAJOR = 15\nTARGET_PG_MAJOR = 17\n"
        for body, why in ((both, None),
                          ("MIN_PG_MAJOR = 15\n", "the target is missing"),
                          ("TARGET_PG_MAJOR = 17\n", "the floor itself is missing"),
                          ("", "neither name is there")):
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, floor.DECLARATION_RELPATH)
                os.makedirs(os.path.dirname(path))
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(body)
                got = floor.declared_floor(tmp)
                if why is None:
                    self.assertEqual((15, 17), got)
                else:
                    self.assertIsNone(got, "a partial declaration answered %r when %s" % (got, why))

    def test_the_declaration_is_read_as_text_not_imported(self):
        """An audit that imports the subsystem it audits cannot fail honestly — and `teamdb`
        pulls the memory package in with it. `declared_floor` must work with nothing loaded."""
        self.assertNotIn("import", floor.DECLARATION_RELPATH)
        self.assertIsNotNone(floor.declared_floor(REPO))


class TestNoSurfaceStatesAnotherFloor(unittest.TestCase):
    """The class. Every in-scope claim states the declared floor, or this reds naming the site."""

    def test_every_tracked_floor_claim_states_the_declared_floor(self):
        low = floor.declared_floor(REPO)[0]
        drifted, _tags = floor.scan(_corpus())
        wrong = [(rel, n, major, line) for rel, n, major, line in drifted if major != low]
        self.assertEqual(
            [], wrong,
            "these tracked surfaces state a PostgreSQL floor the code does not declare "
            "(declared: %d). Each one is a sentence a user acts on:\n%s"
            % (low, "\n".join("  %s:%d says %d — %s" % (r, n, m, l) for r, n, m, l in wrong)))

    def test_the_sweep_actually_read_something(self):
        """A pass over nothing is not a pass. The tree HAS floor claims; if it stops having any,
        the surfaces have been reworded and this guard has quietly stopped guarding."""
        drifted, _tags = floor.scan(_corpus())
        self.assertTrue(drifted,
                        "no tracked surface states a PostgreSQL floor at all — either the "
                        "detector broke or every claim was deleted; both need a human")

    def test_every_postgres_image_tag_satisfies_the_floor(self):
        """The other half, and graded differently ON PURPOSE: a tag is an INSTANTIATION, so any
        major at or above the floor is correct. Grading a pin for equality would red on a
        perfectly good upgrade, and a guard that reds on correct code teaches people to skip it."""
        low = floor.declared_floor(REPO)[0]
        _drifted, tags = floor.scan(_corpus())
        below = [(rel, n, major, line) for rel, n, major, line in tags if major < low]
        self.assertEqual(
            [], below,
            "these PostgreSQL images sit BELOW the declared floor of %d:\n%s"
            % (low, "\n".join("  %s:%d pins %d — %s" % (r, n, m, l) for r, n, m, l in below)))

    def test_the_backend_guidance_renders_the_declared_floor(self):
        """`REACHABILITY-PINS-MISSING` (doc 84): the three strings a user reads out of the PRODUCT
        now interpolate the constant, so they carry no digit for the sweep above to grade. That
        makes them invisible to it — so the rendered output is checked HERE, at the seam, or the
        one surface that made this a user-facing bug would be the one surface nothing covers."""
        from mokata import team, teamdb
        low = teamdb.MIN_PG_MAJOR
        for backend in team.INIT_BACKENDS:
            rendered = team._BACKEND_GUIDANCE[backend] % {"env": "MOKATA_PG_DSN", "floor": low}
            self.assertIn(str(low), rendered,
                          "the %r guidance no longer names the declared floor: %s"
                          % (backend, rendered))
            self.assertNotIn(str(_STALE_MAJOR), rendered,
                             "the %r guidance still names the superseded floor: %s"
                             % (backend, rendered))


class TestTheDenyListCannotRotQuietly(unittest.TestCase):
    """An exemption is a permanent hole in the corpus. It has to keep earning its place."""

    def test_every_exemption_carries_a_reason(self):
        for entry, (reason, _ships) in (list(floor.HISTORY_FILES.items())
                                        + list(floor.HISTORY_PREFIXES.items())):
            self.assertTrue(reason and reason.strip(),
                            "%s is exempted with no reason — an unexplained hole is indistinguishable "
                            "from an oversight" % entry)

    def test_no_exemption_has_gone_inert(self):
        """The unconditional half: a path that is HERE and no longer carries a floor claim is an
        open hole wearing a justification, in every checkout."""
        inert = [(e, d) for e, basis, d in floor.stale_exemptions(_corpus()) if basis == floor.INERT]
        self.assertEqual(
            [], inert,
            "these exemptions no longer describe anything:\n%s"
            % "\n".join("  %s — %s" % (e, d) for e, d in inert))

    def test_the_internal_partition_is_declared_exactly(self):
        """Which exemptions the mirror is allowed to lack is DECLARED and graded for exactness in
        both directions, the treatment `_mutant_driver_contract.DECLARED_NONCONFORMING` gets. The
        two failures are different: an entry wrongly declared internal forgives a real absence
        forever, and one wrongly declared shipping reds the whole guard on the public mirror. The
        set is pinned against `sync-public.sh`'s exclude list — which this file may NOT read,
        because it ships (`SHIPPED-TEST-READS-INTERNAL-FILE`) — and `test_claude_md_ships_list.py`
        is what keeps that list honest. Adding a non-shipping exemption updates this line."""
        self.assertEqual({"docs/build/"}, floor.internal_entries())

    def test_a_missing_exemption_is_judged_against_which_checkout_this_is(self):
        """The conditional half, and the one that caught this guard out. This file SHIPS, and in
        the public mirror `docs/build/` does not exist — so grading every absence as rot reds on
        the mirror and passes in the dev tree, which is a guard that disagrees with itself about
        the same code. The internal partition is therefore required PRESENT WHOLE OR ABSENT WHOLE
        (`tests/_mutant_driver_contract.py`'s idiom, reused not re-derived): a half-present one is
        a real finding in either tree, and a shipping exemption that has vanished always is."""
        absent = {e for e, basis, _d in floor.stale_exemptions(_corpus()) if basis == floor.ABSENT}
        internal = floor.internal_entries()

        self.assertEqual(set(), absent - internal,
                         "these SHIPPING exemptions name paths that are not here at all: %s"
                         % sorted(absent - internal))
        missing = absent & internal
        self.assertIn(missing, (set(), internal),
                      "the internal partition is half here: %s of %s are absent — in the dev tree "
                      "that is a rename nobody swept, and in the mirror it means an internal path "
                      "leaked" % (sorted(missing), sorted(internal)))

    def test_the_expiry_check_reports_both_ways_it_can_rot(self):
        """The test above passes trivially against a `stale_exemptions` that always answers `[]`,
        which is exactly the shape of expiry check this repo has been finding all release. Both
        rot modes are planted here, and they are DIFFERENT findings (§7g): a path that has gone
        AWAY, and a path that is still there and no longer carries what it was exempted for."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            empty = os.path.join(tmp, "empty.md")
            with open(empty, "w", encoding="utf-8") as fh:
                fh.write("no floor claim lives here\n")

            # EVERY entry, not merely "something was reported" — a check that reports the
            # prefix half and silently drops the file half looks identical from the outside.
            every = set(floor.HISTORY_FILES) | set(floor.HISTORY_PREFIXES)

            absent = floor.stale_exemptions({})
            self.assertEqual(every, {e for e, _b, _d in absent},
                             "not every exemption was reported against an empty corpus")
            self.assertEqual({floor.ABSENT}, {b for _e, b, _d in absent}, absent)

            corpus = dict.fromkeys(floor.HISTORY_FILES, empty)
            corpus["docs/build/placeholder.md"] = empty
            inert = floor.stale_exemptions(corpus)
            self.assertEqual(every, {e for e, _b, _d in inert},
                             "not every exemption was reported against a claim-free corpus")
            self.assertEqual({floor.INERT}, {b for _e, b, _d in inert}, inert)

            # The two bases must not collapse into one another — that collapse is the defect.
            self.assertNotEqual(floor.ABSENT, floor.INERT)

    def test_the_deny_list_has_exactly_three_readers(self):
        """`in_scope` decides scope, `stale_exemptions` expires entries, `internal_entries` says
        which are absent from the mirror by design; nothing else may read the lists. A fourth,
        partial reader is how half a deny-list gets applied while the sweep still reads as
        complete — `MIRROR-PIN-COVERS-FOUR-OF-SIXTEEN` (doc 84), one file over."""
        import ast
        with open(os.path.join(REPO, "tests", "_pg_floor.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        readers = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for sub in ast.walk(node):
                if isinstance(sub, ast.Name) and sub.id in ("HISTORY_FILES", "HISTORY_PREFIXES"):
                    readers.add(node.name)
        self.assertEqual({"in_scope", "stale_exemptions", "internal_entries"}, readers,
                         "unexpected reader(s) of the deny-list: %s" % sorted(readers))


class TestTheDetectorIsNotVacuous(unittest.TestCase):
    """The tree is clean, so every assertion above passes by saying nothing unless the DETECTOR
    is graded on its own. Positives and negatives both — the negatives are the two real near-
    misses in this repo, and a looser pattern would have reported them as violations."""

    def test_a_planted_claim_is_convicted(self):
        planted = "the shared schema is plain Postgres >= %d, no extensions" % _STALE_MAJOR
        found = floor.claims(planted)
        self.assertEqual(1, len(found), "the detector missed a planted floor claim: %r" % planted)
        self.assertEqual(_STALE_MAJOR, found[0][1])

    def test_every_spelling_used_in_this_tree_is_convicted(self):
        for text in ("vanilla Postgres >= %d" % _STALE_MAJOR,
                     "vanilla Postgres \u2265%d" % _STALE_MAJOR,
                     "PostgreSQL \u2265 %d, no extensions" % _STALE_MAJOR,
                     "ADR-54's floor is **PG \u2265 %d**" % _STALE_MAJOR,
                     "a minimum of \u2265%d for the team Postgres" % _STALE_MAJOR):
            self.assertTrue(floor.claims(text), "spelling not detected: %r" % text)

    def test_a_length_check_beside_a_dsn_is_not_a_floor_claim(self):
        """`tests/test_db_s0_dsn_inspect.py` — a `>` length comparison next to a `postgres://`
        literal. Convicting it would be a false red inside the suite's own fixtures."""
        self.assertEqual(
            [], floor.claims('self.assertNotIn(value if len(value) > 12 else "postgres://" + _USER, blob)'))

    def test_a_character_count_beside_a_pg_symbol_is_not_a_floor_claim(self):
        """`scripts/mutate.sh` — a symbol-length note beside `_pg.`. No comparator, no claim."""
        self.assertEqual(
            [], floor.claims("# (`_pg.open_unmanaged` -> `_pg.get_connection`, 14 characters -> 14 characters"))

    def test_an_image_tag_is_not_a_floor_claim(self):
        """The distinction the whole design rests on: `postgres:16` asserts no minimum."""
        line = "    image: postgres:%d" % (_STALE_MAJOR + 2)
        self.assertEqual([], floor.claims(line))
        self.assertEqual([(1, _STALE_MAJOR + 2, line.strip())], floor.instantiations(line))

    def test_a_stale_image_tag_is_convicted_as_an_instantiation(self):
        line = "    image: pgvector/pgvector:pg%d" % _STALE_MAJOR
        self.assertEqual([(1, _STALE_MAJOR, line.strip())], floor.instantiations(line))

    def test_scope_is_decided_by_path_and_the_deny_list_bites(self):
        self.assertTrue(floor.in_scope("docs/reference/cli.md"))
        self.assertTrue(floor.in_scope("docs/talks/pycon-2026/90-feature-inventory-raw.md"))
        self.assertFalse(floor.in_scope("CHANGELOG.md"))
        self.assertFalse(floor.in_scope("docs/build/85-mokata-standing-references.md"))
        self.assertFalse(floor.in_scope("docs/build/archive/54-adr-team-database-choice.md"))

    def test_a_planted_claim_reaches_the_sweep_and_a_denied_path_does_not(self):
        """The end-to-end axis, in BOTH directions: `scan` must route a convicted line out of an
        in-scope corpus entry, and must NOT route the identical line out of a denied one.
        Everything above grades one half or the other, and a sweep that convicts nothing and a
        sweep whose deny-list swallows everything look the same from the outside."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "planted.md")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("shared tables on vanilla Postgres >= %d, no extensions\n" % _STALE_MAJOR)

            drifted, _tags = floor.scan({"docs/reference/cli.md": path})
            self.assertEqual([("docs/reference/cli.md", 1, _STALE_MAJOR,
                               "shared tables on vanilla Postgres >= %d, no extensions" % _STALE_MAJOR)],
                             drifted)

            denied, _tags = floor.scan({"CHANGELOG.md": path})
            self.assertEqual([], denied, "the deny-list did not hold for an identical planted line")


if __name__ == "__main__":
    unittest.main()
