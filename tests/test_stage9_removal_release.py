"""0.0.18 stage 9 — `REMOVAL-RELEASE-ALREADY-PASSED` (doc 84 §1, doc 102 C2), lane D.

The shipped 0.0.17 wheel told every user that five channels would be REMOVED in mokata 0.0.17.
0.0.17 shipped on 2026-08-08 and removed none of them, and `test_removal_release_is_0_0_17` held
the false value in place, green, in the suite. The constant is corrected in
`src/mokata/deprecation.py`; THIS file is the half that makes the correction durable.

WHAT IS GRADED HERE, and what each part is worth:

  ① THE VERDICT (`removal_state`) — arithmetic against the version being cut. It reproduces the
     state that actually shipped, and it reds on a future that has not happened. §7i: it is a pure
     function over a SUPPLIED state, because once the constant is right this tree contains no
     offender and a function that went and looked would grade nothing.

  ② THE LIVE ASSERTION — the same arithmetic against `__version__` and a real import probe. This
     is the tripwire. `release.sh`'s `run_test_preflight` runs `unittest discover -s tests`, so
     the release that REACHES the declared removal with a channel still implemented is the release
     whose CUT ABORTS. ⚠ Concretely and on purpose: **0.0.18 cannot be cut until the deletion lane
     (stages 10–14) lands**, or until someone moves the declaration forward in a diff a reviewer
     reads. That is the `WAIVER(...)` bargain stage 7 made, applied to a promise.

  ③ THE OTHER SURFACES. The false 0.0.17 was on FIVE surfaces, not the two the row names: the
     module, the pinning test, and THREE more — two published how-to pages quoting the rendered
     notice, and `CHANGELOG.md`'s 0.0.15 entry. The docs are corrected and swept; the changelog is
     dated history and is deliberately out of corpus (reasoning in `_deprecation_removal`).

  ④ THE REGISTRY'S COHERENCE — a notice whose implementation is already gone is the same
     dishonesty with its sign flipped, and stages 10–14 are about to delete five implementations.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import inspect
import os
import unittest

import _support  # noqa: F401  (puts src/ on the path)

import _deprecation_removal as R

import mokata
from mokata import deprecation as D
from mokata import docsync


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _docs_corpus():
    """`{path: text}` for the PUBLISHED doc set, derived from the same function docsync sweeps
    with rather than from a list of the two files this stage happens to have touched (§7j).

    CORPUS: THE WORKING TREE. This asks what mokata PUBLISHES, and both publishing paths copy the
    working tree — `sync-public.sh` mirrors with `rsync` and mkdocs builds from disk — so an
    untracked page under `docs/` really is served. The index would be blind to exactly the page
    most likely to carry a hand-typed release."""
    corpus = {}
    for path in docsync.find_docs(REPO_ROOT):
        rel = _support.posix_rel(path, REPO_ROOT)
        with open(path, encoding="utf-8") as handle:
            corpus[rel] = handle.read()
    return corpus


class TestVersionKey(unittest.TestCase):
    def test_a_release_is_compared_numerically_not_as_text(self):
        # The trap this exists for: "0.0.9" > "0.0.10" as strings, so a text comparison would call
        # the 0.0.10 cut PENDING against a 0.0.9 promise and let it ship.
        self.assertLess(R.version_key("0.0.9"), R.version_key("0.0.10"))
        self.assertLess(R.version_key("0.0.17"), R.version_key("0.1.0"))
        self.assertEqual(R.version_key("0.0.18"), R.version_key(" 0.0.18 "))

    def test_a_non_version_is_none_and_does_not_sort_below_everything(self):
        for junk in ("", None, "dev", "0.0.18-rc1", "v0.0.18", "18"):
            self.assertIsNone(R.version_key(junk), junk)


class TestTheVerdict(unittest.TestCase):
    """§7i — every case is a SUPPLIED state, so the tree being clean grades nothing away."""

    def test_the_state_that_actually_shipped_is_overdue(self):
        # `v0.0.17`, 2026-08-08: the wheel said 0.0.17 and the wheel contained the channels. This
        # is the defect, reproduced. It is the assertion the old pin inverted.
        self.assertEqual(R.removal_state("0.0.17", "0.0.17", {"vault"}), R.REMOVAL_OVERDUE)

    def test_a_future_release_arriving_without_the_removal_is_overdue(self):
        # The synthetic future: the promise was 0.0.18 and 0.0.19 is being cut with the channels
        # still here. Nothing in the tree is in this state; that is why it is supplied.
        self.assertEqual(R.removal_state("0.0.19", "0.0.18", {"vault", "neo4j"}),
                         R.REMOVAL_OVERDUE)

    def test_the_promised_release_itself_arriving_is_overdue_not_pending(self):
        # `>=`, not `>`. A 0.0.18 wheel saying "REMOVED in 0.0.18" while carrying the channels is
        # C2 verbatim — the release named has ARRIVED. Off-by-one here reinstates the defect for
        # exactly one release, which is how long the last one lasted.
        self.assertEqual(R.removal_state("0.0.18", "0.0.18", {"vault"}), R.REMOVAL_OVERDUE)

    def test_a_promise_not_yet_due_is_pending(self):
        self.assertEqual(R.removal_state("0.0.17", "0.0.18", {"vault"}), R.REMOVAL_PENDING)

    def test_no_implementation_left_is_landed_whatever_the_version(self):
        for version in ("0.0.17", "0.0.18", "0.0.99"):
            self.assertEqual(R.removal_state(version, "0.0.18", frozenset()), R.REMOVAL_LANDED)

    def test_an_unreadable_version_is_its_own_state_and_never_reads_as_pending(self):
        # §7g. Folding this into PENDING would make a typo in the declaration read as "fine for
        # now" — a non-result laundered into a result, and the graded surface would be gone.
        for version, removal in (("", "0.0.18"), ("0.0.18", "soon"), ("dev", "dev")):
            state = R.removal_state(version, removal, {"vault"})
            self.assertEqual(state, R.REMOVAL_UNREADABLE, (version, removal))
            self.assertNotEqual(state, R.REMOVAL_PENDING)


class TestThePresenceProbe(unittest.TestCase):
    def test_present_channels_ranges_over_the_supplied_map_with_a_supplied_probe(self):
        targets = {"a": "mod.a", "b": "mod.b", "c": "mod.c"}
        self.assertEqual(R.present_channels(targets, lambda _t: True), frozenset("abc"))
        self.assertEqual(R.present_channels(targets, lambda _t: False), frozenset())
        self.assertEqual(R.present_channels(targets, lambda t: t.endswith(".b")), frozenset("b"))

    def test_the_live_probe_answers_both_ways_on_modules(self):
        self.assertTrue(R.live_probe("mokata.vault"))
        self.assertFalse(R.live_probe("mokata.a_channel_that_was_removed"))

    def test_the_live_probe_answers_both_ways_on_a_symbol_in_a_surviving_module(self):
        # The two memory backends WENT (0.0.18 stage 10) while `memory/backends.py` stayed, so
        # `module:Symbol` is not a decoration — a module-only probe would report them present
        # forever. `SQLiteBackend` is the surviving symbol in the same module; `ObsidianBackend`
        # is the removed one, and the pair is what makes this test grade rather than assert.
        self.assertTrue(R.live_probe("mokata.memory.backends:SQLiteBackend"))
        self.assertFalse(R.live_probe("mokata.memory.backends:ObsidianBackend"))

    def test_a_lookup_that_ERRORS_is_present_not_removed(self):
        # `find_spec("mokata.vault.deeper")` RAISES ModuleNotFoundError rather than returning None:
        # `mokata.vault` exists and is not a package. A failed lookup is not an absence, and
        # reading it as one would retire the whole guard the first time an extra went missing —
        # so the conservative direction is PRESENT, and this is the offender that grades it.
        self.assertTrue(R.live_probe("mokata.vault.deeper"))

    def test_every_STILL_DEPRECATED_target_is_readable_today(self):
        # The map is declared (§7j), so it is graded for being true of THIS tree: an entry naming
        # a module that never existed would silently report its channel as already removed.
        # ⚠ The domain is `CHANNELS`, NOT the whole map — since stage 10 the map also holds the
        # REMOVED channels, whose targets must be ABSENT. `test_no_removed_channel_is_still_
        # reachable` is the other half; between them every entry in the map is graded, in the
        # direction its own registry claims.
        for channel in sorted(D.CHANNELS):
            target = R.IMPLEMENTATIONS[channel]
            self.assertTrue(R.live_probe(target),
                            "%s: %s does not resolve — the map has rotted" % (channel, target))


class TestTheRegistryIsCoherent(unittest.TestCase):
    def test_the_declared_map_covers_the_registry_exactly(self):
        # The registry is BOTH halves since stage 10 — a channel is either still deprecated or
        # already removed, and a channel in neither is exactly the thing this asserts cannot exist.
        announced = tuple(D.CHANNELS) + tuple(D.REMOVED)
        unmapped, unannounced = R.registry_drift(announced, R.IMPLEMENTATIONS)
        self.assertEqual(unmapped, (), "announced with no implementation target: %r" % (unmapped,))
        self.assertEqual(unannounced, (), "mapped with no notice: %r" % (unannounced,))

    def test_registry_drift_reports_the_two_directions_separately(self):
        unmapped, unannounced = R.registry_drift({"a": 1, "b": 2}, {"b": "x", "c": "y"})
        self.assertEqual((unmapped, unannounced), (("a",), ("c",)))

    def test_no_notice_has_outlived_its_implementation(self):
        present = R.present_channels(R.IMPLEMENTATIONS, R.live_probe)
        self.assertEqual(R.stale_notices(D.CHANNELS, present), ())

    def test_stale_notices_finds_a_notice_whose_subject_is_gone(self):
        # What stages 10–14 will trip if one of them deletes a module and leaves the notice.
        self.assertEqual(R.stale_notices(("vault", "neo4j"), {"neo4j"}), ("vault",))


class TestTheDeclaration(unittest.TestCase):
    def test_the_constant_is_read_from_the_declaration(self):
        self.assertEqual(D.REMOVAL_RELEASE, D.removal_target(D.REMOVAL_DECLARATION))

    def test_the_declaration_carries_who_promised_it_and_when(self):
        fields = D.removal_fields(D.REMOVAL_DECLARATION)
        for key in ("set", "at", "filed", "owner"):
            self.assertIn(key, fields)

    def test_an_absent_declaration_is_empty_and_an_unreadable_one_raises(self):
        # Two facts, two behaviours: "there is no declaration in this text" is a question a caller
        # may legitimately ask, "this module's own declaration is unreadable" is a defect.
        self.assertEqual(D.removal_fields("no declaration here"), {})
        for broken in ("REMOVAL(at=soon)", "REMOVAL(at=)", "REMOVAL(owner=Jas)", ""):
            with self.assertRaises(ValueError):
                D.removal_target(broken)

    def test_every_release_string_in_the_module_is_accounted_for(self):
        """WIDENED AT E7 (ruled 2026-08-15) AND IT GRADES MORE, NOT LESS.

        This asserted "exactly one release literal, and it is the declaration". That held only
        while every removal record DEFAULTED its release off `REMOVAL_RELEASE` — and that default
        was the defect E7 removed: it made "which release took this channel away" (history) track
        "which release the next removal is due in" (a mutable promise), so moving `at=` silently
        re-dated removals that had already happened.

        Frozen per-channel literals are therefore expected now. What is still forbidden is a THIRD
        role — a release string that is neither the declaration nor a record's own frozen removal —
        which is the shape the original pin existed to catch, and it still reds (graded on a
        planted offender below rather than on this tree, §7i)."""
        literals = R.version_literals(inspect.getsource(D))
        frozen = {ch: n.removed for ch, n in D.REMOVED.items()}
        self.assertEqual(R.unaccounted_releases(literals, D.REMOVAL_RELEASE, frozen), (),
                         "an unaccounted release literal is in deprecation.py: %r" % (literals,))
        # …and every literal is actually EXERCISED, so the check above cannot pass by finding none.
        self.assertTrue(frozen, "no removal records: the accounting above would grade nothing")
        self.assertIn(D.REMOVAL_RELEASE, [t for _l, t in literals][0])

    def test_an_unaccounted_release_literal_still_reds(self):
        frozen = {"a": "0.0.18"}
        self.assertEqual(
            R.unaccounted_releases(((1, 'X = "0.0.19"'),), "0.0.18", frozen), ((1, "0.0.19"),))
        # the declaration SENTENCE is accounted for by the release inside it, not by string match
        self.assertEqual(
            R.unaccounted_releases(((1, "REMOVAL(at=0.0.18, filed=2026-08-14)"),),
                                   "0.0.18", frozen), ())

    def test_a_record_may_not_claim_a_release_that_has_not_happened(self):
        """The specific way a FROZEN field rots: somebody freezes the release they HOPE to cut in.
        A record saying a channel was removed in a release later than the declaration promises is
        not a stale value, it is an impossible one."""
        self.assertEqual(R.future_removals({ch: n.removed for ch, n in D.REMOVED.items()},
                                           D.REMOVAL_RELEASE), ())
        self.assertEqual(R.future_removals({"x": "0.0.99"}, "0.0.18"), (("x", "0.0.99"),))
        self.assertEqual(R.future_removals({"x": "not-a-release"}, "0.0.18"),
                         (("x", "not-a-release"),))

    def test_version_literals_sees_a_planted_second_literal_and_not_prose(self):
        planted = ('"""0.0.17 was wrong."""\n'
                   'X = "REMOVAL(at=0.0.18)"\n'
                   '# 0.0.17 shipped and removed nothing\n'
                   'Y = "0.0.17"\n')
        self.assertEqual(R.version_literals(planted),
                         ((2, "REMOVAL(at=0.0.18)"), (4, "0.0.17")))
        self.assertEqual(R.version_literals('"""0.0.17."""\n# 0.0.17\nZ = "fine"\n'), ())


class TestThePublishedDocsSayItToo(unittest.TestCase):
    def test_no_published_doc_names_a_release_other_than_the_declared_one(self):
        drift = R.removal_drift(_docs_corpus(), D.REMOVAL_RELEASE)
        self.assertEqual(drift, (), "published docs state a stale removal release: %r" % (drift,))

    def test_the_corpus_is_not_empty_and_does_contain_the_pages_that_state_it(self):
        # A sweep over an empty corpus passes having read nothing — the vacuity check the drift
        # assertion above cannot make about itself.
        mentions = R.removal_mentions(_docs_corpus())
        self.assertTrue(mentions, "no published doc states a removal release at all")
        self.assertIn("docs/how-to/use-a-codebase-graph.md", {path for path, _l, _v in mentions})
        self.assertIn("docs/how-to/configure-storage-backends.md",
                      {path for path, _l, _v in mentions})

    def test_the_wrapped_quotation_is_seen(self):
        # THE HOLE THIS CLOSES: both published pages wrap the rendered notice, so the version sits
        # on the NEXT line behind a `> ` blockquote marker. A line-anchored reader finds nothing
        # here and reports a clean corpus — the sweep would be green and blind.
        wrapped = {"p.md": ("> ⚠ deprecated: the Neo4j code-graph backend is deprecated and will\n"
                            "> be REMOVED in mokata\n> 0.0.17. The canonical code graph …\n")}
        self.assertEqual(R.removal_drift(wrapped, "0.0.18"), (("p.md", 2, "0.0.17"),))
        self.assertEqual(R.removal_drift(wrapped, "0.0.17"), ())

    def test_the_admonition_title_form_is_seen(self):
        titled = {"p.md": '!!! warning "The Neo4j backend is deprecated (removal: 0.0.17)"\n'}
        self.assertEqual(R.removal_drift(titled, "0.0.18"), (("p.md", 1, "0.0.17"),))

    def test_a_doc_stating_the_current_release_is_not_drift(self):
        clean = {"p.md": "will be REMOVED in mokata 0.0.18. (removal: 0.0.18)\n"}
        self.assertEqual(R.removal_drift(clean, "0.0.18"), ())
        self.assertEqual(len(R.removal_mentions(clean)), 2)


class TestTheOtherFivePins(unittest.TestCase):
    """★ THE ROW SAID THE FALSE VALUE WAS PINNED ONCE. IT WAS PINNED SIX TIMES.
    `test_simp_s2_shim_parity` (×4, two of them against the manifest mokata writes to disk),
    `test_simp_s2_vault_integrity`, `test_35b_backup_surface` — all green, all certifying 0.0.17.
    Found by running the whole suite, not by reading the row. This is the class guard."""

    def _test_sources(self):
        """`{path: text}` for every test module. CORPUS: THE WORKING TREE — a test file that is
        not yet tracked still runs, and is exactly the one most likely to hand-type a release."""
        sources = {}
        for base in (os.path.join(REPO_ROOT, "tests"),
                     os.path.join(REPO_ROOT, "tests", "integration")):
            for name in sorted(os.listdir(base)):
                if not name.endswith(".py"):
                    continue
                path = os.path.join(base, name)
                with open(path, encoding="utf-8") as handle:
                    sources[_support.posix_rel(path, REPO_ROOT)] = handle.read()
        return sources

    def test_no_test_hand_types_the_removal_release(self):
        pins = R.notice_pins(self._test_sources())
        self.assertEqual(pins, (), "a deprecation test pins a hand-typed release: %r" % (pins,))

    def test_the_corpus_is_real_and_reaches_the_files_the_offenders_were_in(self):
        sources = self._test_sources()
        self.assertGreater(len(sources), 100)
        for was_an_offender in ("tests/test_simp_s2_shim_parity.py",
                                "tests/test_simp_s2_vault_integrity.py",
                                "tests/test_35b_backup_surface.py"):
            self.assertIn(was_an_offender, sources)

    def test_notice_pins_convicts_the_shape_all_six_offenders_had(self):
        offender = ('class T:\n'
                    '    def test_x(self):\n'
                    '        # the deprecated channel warns\n'
                    '        self.assertIn("0.0.17", err)\n'
                    '        self.assertEqual(catalog, "0.0.17")\n')
        self.assertEqual(R.notice_pins({"t.py": offender}),
                         (("t.py", 4, "assertIn", "0.0.17"),
                          ("t.py", 5, "assertEqual", "0.0.17")))

    def test_a_file_that_reads_the_constant_is_clean_and_one_outside_the_domain_is_ignored(self):
        derived = ('# deprecated\nclass T:\n    def test_x(self):\n'
                   '        self.assertIn(REMOVAL_RELEASE, err)\n')
        self.assertEqual(R.notice_pins({"t.py": derived}), ())
        # DECLARED domain: a file with no deprecation vocabulary keeps its synthetic fixtures.
        unrelated = ('class T:\n    def test_x(self):\n'
                     '        self.assertIn("9.9.9", out)\n')
        self.assertEqual(R.notice_pins({"t.py": unrelated}), ())

    def test_a_version_shaped_FRAGMENT_is_not_a_release(self):
        # An offender only the TWO-DOT requirement can see. Loosen it to one dot and
        # `3.10 (the declared floor)` convicts — and the files that talk about deprecation are the
        # same files that talk about the Python floor, so the guard would become noise precisely
        # where it has to be trusted. Written after stage 9's first mutant batch, where loosening
        # the regex SURVIVED because neither the live corpus nor the synthetic offenders held a
        # version-shaped fragment (§7f). ⚠ Both of these must stay: they are the ONLY thing
        # standing between the widening below and H04.
        fragments = ('# deprecated\nclass T:\n    def test_x(self):\n'
                     '        self.assertIn("3.10 (the declared floor)", out)\n'
                     '        self.assertEqual(prefix, "0.0")\n')
        self.assertEqual(R.notice_pins({"t.py": fragments}), ())

    def test_a_release_INSIDE_a_sentence_is_still_a_hand_typed_release(self):
        # ⚠ WIDENED AT 0.0.18 STAGE 10, AND THIS TEST RECORDS WHAT CHANGED. The predicate used to
        # require the whole argument to BE a release, and `test_close_fix_approve_list` hand-typed
        # `"scheduled for removal in 0.0.17"` three times over `mokata migrate --help` — a
        # SEVENTH pin, green through the release that broke the promise, on a surface that prints
        # to a user. `0.0.18-rc1` moved to this side of the line with it: a pre-release string is
        # a hand-typed release wearing a suffix, and the guard was never meant to let one past.
        sentences = ('# deprecated\nclass T:\n    def test_x(self):\n'
                     '        self.assertIn("scheduled for removal in 0.0.17", help_text)\n'
                     '        self.assertIn("0.0.18-rc1", out)\n')
        self.assertEqual(R.notice_pins({"t.py": sentences}),
                         (("t.py", 4, "assertIn", "0.0.17"),
                          ("t.py", 5, "assertIn", "0.0.18")))


class TestTheLiveTripwire(unittest.TestCase):
    """② — the assertion that stops a cut. Everything above grades the mechanism; this one is
    about THIS tree, and it is the one that will red when the promise is broken."""

    def test_the_wheel_never_names_a_release_that_has_already_arrived(self):
        present = R.present_channels(R.IMPLEMENTATIONS, R.live_probe)
        state = R.removal_state(mokata.__version__, D.REMOVAL_RELEASE, present)
        self.assertIn(state, (R.REMOVAL_PENDING, R.REMOVAL_LANDED), (
            "mokata %s still implements %s while `REMOVAL_DECLARATION` promises removal at %s "
            "(state: %s). This is `REMOVAL-RELEASE-ALREADY-PASSED`. Land the removal, or move the "
            "declaration forward with a new `filed=` — do not edit this test."
            % (mokata.__version__, sorted(present), D.REMOVAL_RELEASE, state)))

    def test_every_rendered_notice_states_that_same_release(self):
        for channel, notice in sorted(D.CHANNELS.items()):
            self.assertIn(D.REMOVAL_RELEASE, notice.render(), channel)
            self.assertEqual(notice.removal, D.REMOVAL_RELEASE, channel)


if __name__ == "__main__":
    unittest.main()
