"""`LANDING-COUNTS-UNGUARDED` — the public numbers are derived, and the docs that state them are
graded against the derivation.

Built at GATE-COUNT-TRUTH, from the row LP-15 filed. The defect it guards against is not a typo:
`docs/how-it-works/skills-and-gates.md` claimed **10 backed gates** — counting `ship-readiness`,
which 0.0.17 stage 5 demoted to advisory, and adding `self-protect` a second time on the belief
that it sat outside `skill_contracts.GATES`. Two errors that produced a plausible total, on the
page a reader goes to FOR the gate accounting, live on mokata.ai. The 0.0.17 CHANGELOG names the
class in mokata's own words: *"claiming enforcement mokata does not perform is the failure this
release is named for."*

Three gates, and the second and third exist because the first is not enough:

  1. `test_every_public_number_matches_the_code` — every stated count equals the derived one.
  2. `test_every_marked_roster_names_the_derived_set` — MEMBERSHIP. The backed count went
     9 → 10 → 9 across two corrections that landed together and cancelled; doc 85 §4: *"a count
     assertion would have been green through both moves."*
  3. `test_no_public_page_enumerates_backed_gates_outside_a_marker` — the corpus axis. A
     membership check over hand-marked rosters means "the rosters somebody marked" (§7j).

Every gate is a pure function fed a supplied corpus, and every one is graded here against a
PLANTED offender as well as against the tree (doc 85 §7i — a guard that has never failed is not a
guard).

MIRROR BOUNDARY (GATE-COUNT-TRUTH-FU; `SHIPPED-TEST-READS-INTERNAL-FILE`, stage 28). `tests/`
ships to the public mirror; `scripts/sync-public.sh` — which `_public_counts` derives the CORPUS
from — does not. As first written the eight corpus-reading tests here ERRORED on the mirror with
`AttributeError: 'NoneType' object has no attribute 'splitlines'`: green in this repo, broken in
the one users clone. `test_s11_bookkeeping_derived.py:29` records the same lesson from stage 28 and
already defines the same `SYNC_SH`; this file was written straight past both.

The stage-28 sweep graded this file GREEN and was right about its own model: its taint crosses
**one** module hop and this chain is two (`test_public_counts_guard` → `_public_counts` →
`_mirror_bookkeeping.read_script`). Filed as `TAINT-STOPS-ONE-HOP-SHORT` (doc 84).

What that costs, and the three rules that pay it:

  * ONLY the classes that reach the corpus carry the decorator. The registry derivations —
    `DerivationTest`, and the sixteen PLANTED-offender tests, which supply their own text and read
    no file at all — run UNGUARDED on both sides. The mirror is where "9 backed gates" being true
    matters most, and stage 11's note applies verbatim: *"Nothing was weakened to be portable."*
  * The class DECORATOR, never a `setUpClass` skip — the one shape `_shipped_reads.ACCEPTED_GUARDS`
    accepts. A setUpClass skip collapses the class into ONE skip and drops its tests out of `Ran N`.
  * ★ AND A POSITIVE CONTROL, because a `skipUnless` trades a loud crash on the mirror for a SILENT
    SKIP everywhere, and nothing goes red either way. That is `PYYAML-SKIP-CLUSTER` — stage 2 of
    this very release, a job that skipped 17 tests and reported OK. `MirrorGuardControlTest` runs
    unguarded on both sides and asserts the guarded classes actually RAN here and SKIPPED there.
    A skip that reads as a pass is the same defect in a new coat.
"""

import ast
import io
import os
import shutil
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

import _public_counts as pc
import _shipped_reads as sr


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: The mirror boundary, as a path to probe. `os.path.exists` on it is a PROBE, not a read — it
#: never opens the file, and it is exactly what `_shipped_reads` builds the accepted guard from.
SYNC_SH = os.path.join(ROOT, "scripts", "sync-public.sh")

#: The same path as a PLAIN STRING, for failure messages only.
#:
#: ⚠ Not a nicety. `_shipped_reads` reads this file's AST and cannot tell a diagnostic mention of
#: `SYNC_SH` from an `open(SYNC_SH)` — so interpolating the constant into a message graded this
#: file RED ("MirrorGuardControlTest reads scripts/sync-public.sh (via SYNC_SH) with no guard"),
#: and the only decoration that would silence it is the one the control must never carry. The
#: sweep is right to be conservative, so the constant stays where its own header says a reference
#: is safe — inside `os.path.exists(...)`, which is a PROBE and never opens anything — and prose
#: uses this literal. `test_s11_bookkeeping_derived.py:165` keeps the same discipline by never
#: naming SYNC_SH outside a probe.
MIRROR_SCRIPT_REL = "scripts/sync-public.sh"

#: Internal paths that leave for the mirror IN THE SAME MOVE as `sync-public.sh` — each is both a
#: `--exclude` clause and an `INTERNAL_PATHS` entry — so a tree carrying ANY of them is the private
#: tree. The control below asks THESE whether it is in a dev tree, never `SYNC_SH` itself: a
#: constant pointing at the wrong path would otherwise answer "mirror", every guarded class would
#: skip, and the run would say OK — the mis-pointed constant hiding inside its own remedy.
#:
#: A DECLARED list, and it has to be (doc 85 §7j): the only file it could be derived from is the
#: one whose absence is the question. `test_s11_bookkeeping_derived.py:152` makes the same call.
DEV_TREE_WITNESSES = ("scripts/release.sh", "CLAUDE.md", "docs/build")


def dev_tree_witnesses():
    """The internal siblings actually present here. Non-empty means: this is the private tree."""
    return [w for w in DEV_TREE_WITNESSES if os.path.exists(os.path.join(ROOT, w))]


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


class DerivationTest(unittest.TestCase):
    """The values come from the registries, or the guard is a second hand-maintained list."""

    def test_every_fact_is_derived_from_a_named_source(self):
        facts = pc.derived_facts(ROOT)
        self.assertTrue(facts, "no facts derived at all")
        for key, fact in facts.items():
            self.assertIsInstance(fact.value, int, key)
            self.assertGreater(fact.value, 0, "%s derived as %r" % (key, fact.value))
            self.assertTrue(fact.source, key)

    def test_backed_gates_are_the_registry_rows_with_backed_true(self):
        from mokata import skill_contracts

        facts = pc.derived_facts(ROOT)
        expected = {n for n, g in skill_contracts.GATES.items() if g.backed}
        self.assertEqual(facts["backed_gates"].members, frozenset(expected))
        self.assertEqual(facts["backed_gates"].value, len(expected))
        self.assertTrue(
            all(hasattr(g, "backed") for g in skill_contracts.GATES.values()),
            "a GateRef without a `backed` field would make the filter above silently partial",
        )

    def test_ship_readiness_is_advisory_and_therefore_not_a_backed_gate(self):
        """The demotion this stage's docs had missed. Pinned so a silent re-promotion — or a
        silent re-demotion of something else — cannot pass as a docs-only question."""
        from mokata import skill_contracts

        gate = skill_contracts.GATES["ship-readiness"]
        self.assertFalse(gate.backed, "ship-readiness is backed again — the docs must say so")
        self.assertIn("not a code gate", gate.one_line)
        self.assertNotIn("ship-readiness", pc.derived_facts(ROOT)["backed_gates"].members)

    def test_self_protect_is_in_the_gates_map_and_backed(self):
        """The other half of the same paragraph's error: the page said `self-protect` sat OUTSIDE
        `skill_contracts.GATES`. It has been in it since 0.0.17 stage 5 (`f8ad47e`)."""
        from mokata import skill_contracts

        self.assertIn("self-protect", skill_contracts.GATES)
        self.assertTrue(skill_contracts.GATES["self-protect"].backed)
        self.assertIn("self-protect", pc.derived_facts(ROOT)["backed_gates"].members)

    def test_the_hook_lane_and_the_backed_set_are_two_overlapping_sets(self):
        """`spec-scope` is run-state and NOT backed; `self-protect` is backed and runs ahead of
        all four. So the copy may say "the hook stops 5 things", never "5 of the 9"."""
        facts = pc.derived_facts(ROOT)
        backed = facts["backed_gates"].members
        run_state = facts["run_state_gates"].members
        self.assertIn("spec-scope", run_state)
        self.assertNotIn("spec-scope", backed)
        self.assertFalse(run_state <= backed, "the hook lane is a subset after all — reword §4")
        self.assertEqual(facts["hook_enforced"].members, run_state | {"self-protect"})


@unittest.skipUnless(os.path.exists(SYNC_SH),
                     "sync-public.sh is dev-only, excluded from the public mirror")
class CorpusTest(unittest.TestCase):
    """The corpus is derived from the mirror script, not typed here — so all three tests read it,
    and on the mirror the script it derives from is the one file that is guaranteed absent."""

    def test_the_landing_surfaces_are_both_in_the_derived_corpus(self):
        corpus = pc.public_claim_files(ROOT)
        self.assertIn("docs/index.md", corpus)
        self.assertIn(
            "overrides/home.html", corpus,
            "overrides/home.html is outside docs/, which is why no sweep in mokata's history had "
            "ever read it (docsync.py:733-740). If it leaves this corpus the drift returns.",
        )
        self.assertIn("docs/how-it-works/skills-and-gates.md", corpus)
        self.assertIn("README.md", corpus)

    def test_internal_trees_are_excluded_because_the_mirror_excludes_them(self):
        corpus = pc.public_claim_files(ROOT)
        for internal in ("docs/build", "docs/talks", "docs/marketing", "docs/launch"):
            leaked = [p for p in corpus if p.startswith(internal + "/")]
            self.assertEqual([], leaked, "%s is internal but reached the public corpus" % internal)

    def test_dated_records_are_declared_rather_than_silently_skipped(self):
        """A count in a release note is a claim about THAT release. Excluding it is a decision
        with a reason, and the reason is written down where the exclusion is."""
        corpus = pc.public_claim_files(ROOT)
        for historical in pc.HISTORICAL_FILES:
            self.assertNotIn(historical, corpus)
            self.assertTrue(os.path.exists(os.path.join(ROOT, historical)), historical)


@unittest.skipUnless(os.path.exists(SYNC_SH),
                     "sync-public.sh is dev-only, excluded from the public mirror")
class TheTreeTest(unittest.TestCase):
    """The three gates, run over the shipped tree. Every one of the four walks the derived corpus,
    so the whole class is a boundary read."""

    def test_every_public_number_matches_the_code(self):
        facts = pc.derived_facts(ROOT)
        findings = []
        for rel in pc.public_claim_files(ROOT):
            findings.extend(pc.grade(rel, _read(rel), facts))
        self.assertEqual([], findings, "\n\n" + "\n\n".join(findings))

    def test_every_derived_fact_is_actually_STATED_somewhere_public(self):
        """The anti-vacuity floor. An anchor that matches nothing grades nothing, and reads from
        the outside exactly like a page that agrees with the code — §7i's "a function that finds
        nothing in a clean tree proves nothing", one level up. If a number stops being claimed on
        any public page, that is a real change and it should be a decision, not a silent hole."""
        facts = pc.derived_facts(ROOT)
        stated = {}
        for rel in pc.public_claim_files(ROOT):
            for claim in pc.claims(_read(rel)):
                stated.setdefault(claim.key, []).append("%s:%d" % (rel, claim.line_no))
        missing = sorted(set(facts) - set(stated))
        self.assertEqual(
            [], missing,
            "no public page states these derived numbers, so the guard is not grading them: %s"
            % ", ".join(missing),
        )

    def test_every_marked_roster_names_the_derived_set(self):
        facts = pc.derived_facts(ROOT)
        vocabulary = pc.gate_vocabulary()
        findings = []
        seen = 0
        for rel in pc.public_claim_files(ROOT):
            text = _read(rel)
            seen += len(pc.rosters(text, vocabulary))
            findings.extend(pc.grade_rosters(rel, text, facts, vocabulary))
        self.assertEqual([], findings, "\n\n" + "\n\n".join(findings))
        self.assertGreater(
            seen, 0,
            "no marked roster exists anywhere in the public docs — the membership check is "
            "grading nothing, which is how it would pass while the pages are wrong.",
        )

    def test_no_public_page_enumerates_backed_gates_outside_a_marker(self):
        vocabulary = pc.gate_vocabulary()
        findings = []
        for rel in pc.public_claim_files(ROOT):
            findings.extend(pc.unmarked_rosters(rel, _read(rel), vocabulary))
        self.assertEqual([], findings, "\n\n" + "\n\n".join(findings))


#: The English number-words the public pages actually state a count with. A DECLARED LIMIT,
#: not a derivation — which spelling a writer reaches for is a prose choice with no source to
#: read it from (doc 85 §7j: "state it as a declared limit, or derive it too").
#:
#: It is pinned in BOTH directions below because the two failures are different. A word that
#: stops resolving does not FAIL a claim, it silently DROPS one — and the anti-vacuity floor
#: stays green, because every fact here is also stated in digits somewhere else. Mutant B10
#: survived the first batch on exactly that gap: delete `five` and `docs/index.md:26` stops
#: being graded, with nothing anywhere going red.
#:
#: Module-level rather than a class attribute because the two directions no longer share a class:
#: direction 1 is synthetic and runs on the mirror, direction 2 reads the corpus and cannot.
WORDS_IN_USE = {"one": 1, "four": 4, "five": 5, "ten": 10}


class PlantedOffenderTest(unittest.TestCase):
    """Doc 85 §7i: every gate is fed a violation it must find. A function that finds nothing in a
    clean tree proves nothing — these prove each gate can still see an offender.

    UNGUARDED, and deliberately: every test here supplies its own text and reads no file, so the
    whole battery runs on the public mirror. Decorating this class to be rid of ONE corpus read
    would have skipped sixteen synthetic tests on the tree users clone — trading the defect this
    stage fixes for `PYYAML-SKIP-CLUSTER`. The one corpus-reading test moved out instead."""

    def setUp(self):
        self.facts = pc.derived_facts(ROOT)
        self.vocabulary = pc.gate_vocabulary()

    # -- the count gate ---------------------------------------------------------------------
    def test_the_count_gate_catches_the_exact_paragraph_this_stage_repaired(self):
        """The pre-fix text, restored verbatim. This is the mutant the stage brief demands: if
        this comes back clean, the guard would have shipped green over the live defect."""
        offender = (
            "that isn't there. There are **10 *backed* gates** — `write-gate`, `secret-guard`, "
            "`spec-persisted`,\n"
            "`completeness`, `no-code-without-failing-test`, `deviation`, `hard-rule`, "
            "`ship-readiness`,\n"
            "`approach-approval` (the nine in the `skill_contracts.GATES` map) and `self-protect` "
            "— and only a\n"
            "backed gate may be cited as enforcement.\n"
        )
        findings = pc.grade("docs/how-it-works/skills-and-gates.md", offender, self.facts)
        self.assertEqual(1, len(findings), findings)
        self.assertIn("states backed_gates = 10, the code derives 9", findings[0])
        self.assertIn("re-derive:", findings[0])

    def test_the_count_gate_reads_html_stat_tiles(self):
        """`overrides/home.html` states its numbers inside nested tags. A gate that only speaks
        Markdown would have gone green on the file that drifted four numbers."""
        offender = (
            '<div class="mk-stat"><span class="mk-stat__n">10</span>'
            '<span class="mk-stat__l">backed gates<br><em>4 enforced on native writes</em>'
            "</span></div>\n"
        )
        findings = pc.grade("overrides/home.html", offender, self.facts)
        self.assertEqual(2, len(findings), findings)
        self.assertIn("states backed_gates = 10, the code derives 9", findings[0])
        self.assertIn("states hook_enforced = 4, the code derives 5", findings[1])

    def test_the_count_gate_reads_a_claim_THAT_WRAPS_across_a_line(self):
        """Prose wraps, and this stage's own repaired paragraph wraps mid-claim: `there are
        **9 backed` / `gates**`. A line-at-a-time reader saw nothing there — the guard would have
        been green over the exact sentence it was built to grade, on the page the stage exists to
        fix. Found by asking which numbers on the key pages NO claim covered."""
        offender = ("Only a *backed* gate may be cited as enforcement, and there are **10 backed\n"
                    "gates** — every one a row in the `skill_contracts.GATES` map.\n")
        [finding] = pc.grade("x.md", offender, self.facts)
        self.assertIn("states backed_gates = 10, the code derives 9", finding)
        self.assertIn("x.md:1", finding)

    def test_the_count_gate_reads_the_mcp_split_across_a_wrap_and_a_parenthetical(self):
        """`docs/how-it-works/index.md` writes the split with example tool names between the read
        and write legs, and wraps in the middle of them. Both legs were ungraded."""
        offender = ("exposes **61 tools** — **40 read** (`progress`, `lanes`,\n"
                    "`watch`, `govern`, …), **19 write**, and **1 approve** — reachable in chat.\n")
        findings = pc.grade("x.md", offender, self.facts)
        self.assertEqual(1, len(findings), findings)
        self.assertIn("states mcp_write = 19, the code derives 20", findings[0])

    def test_a_claim_does_not_WELD_across_a_paragraph_break(self):
        """The other side of joining: a number ending one paragraph must not marry a phrase
        opening the next. Blank lines are the boundary, so this reads as no claim at all."""
        self.assertEqual([], pc.grade("x.md", "The answer is 10\n\nbacked gates listed.\n",
                                      self.facts))

    def test_the_count_gate_reads_numbers_written_as_words(self):
        findings = pc.grade("x.md", "Four of those gates are enforced by a hook.\n", self.facts)
        self.assertEqual(1, len(findings), findings)
        self.assertIn("states hook_enforced = 4, the code derives 5", findings[0])

    def test_the_count_gate_stays_silent_on_correct_text(self):
        correct = (
            "**26 Agent Skills** (16 curated + 10 domain) · **37 slash commands** ·\n"
            "**69 CLI subcommands** · **61 MCP tools** · **9 backed gates** · "
            "**1 runtime dependency**.\n"
        )
        self.assertEqual([], pc.grade("x.md", correct, self.facts))

    def test_the_count_gate_names_the_file_the_number_and_the_way_back(self):
        """A guard whose red is unreadable gets skipped."""
        [finding] = pc.grade("docs/index.md", "**8 backed gates**\n", self.facts)
        self.assertIn("docs/index.md:1", finding)
        self.assertIn("states backed_gates = 8", finding)
        self.assertIn("the code derives 9", finding)
        self.assertIn("skill_contracts.GATES where backed=True", finding)
        self.assertIn(pc.REDERIVE_CMD, finding)

    def test_every_number_word_the_pages_use_still_resolves(self):
        """Direction 1: each declared spelling reads back as its value. Kills the silent drop.
        Synthetic — the probes are written here, so this half runs on the mirror too."""
        probes = {
            "one": ("**1 runtime dependency**", "runtime_dependencies"),
            "four": ("**4 run-state gates**", "run_state_gates"),
            "five": ("**5 of those gates are enforced by a hook**", "hook_enforced"),
            "ten": ("**10 domain skills**", "domain_skills"),
        }
        self.assertEqual(set(probes), set(WORDS_IN_USE), "add a probe for every pinned word")
        for word, value in sorted(WORDS_IN_USE.items()):
            template, key = probes[word]
            spelled = template.replace(str(value), word.capitalize(), 1)
            found = [c for c in pc.claims(spelled) if c.key == key]
            self.assertEqual(
                [value], [c.stated for c in found],
                "%r no longer reads as %s=%s — a page spelling it that way is now UNGRADED, and "
                "nothing else goes red when that happens" % (spelled, key, value),
            )

    # -- the membership gate ----------------------------------------------------------------
    def test_the_membership_gate_catches_a_roster_of_the_right_SIZE_and_wrong_SET(self):
        """The 9 → 10 → 9 case, planted. A cardinality assertion is green on this input; the
        whole reason doc 85 §4 states membership is that this text once shipped."""
        wrong = sorted((self.facts["backed_gates"].members - {"self-protect"}) | {"ship-readiness"})
        offender = (
            "<!-- mokata:gates backed -->\n"
            + "The backed gates: " + " · ".join("`%s`" % g for g in wrong) + "\n"
            "<!-- /mokata:gates -->\n"
        )
        self.assertEqual(len(wrong), self.facts["backed_gates"].value, "planted set must be 9-long")
        findings = pc.grade_rosters("x.md", offender, self.facts, self.vocabulary)
        self.assertEqual(1, len(findings), findings)
        self.assertIn("stated but not backed : ship-readiness", findings[0])
        self.assertIn("Backed but not stated : self-protect", findings[0])

    def test_the_membership_gate_catches_a_missing_member(self):
        short = sorted(self.facts["backed_gates"].members - {"deviation"})
        offender = ("<!-- mokata:gates backed -->\n" + " ".join("`%s`" % g for g in short)
                    + "\n<!-- /mokata:gates -->\n")
        [finding] = pc.grade_rosters("x.md", offender, self.facts, self.vocabulary)
        self.assertIn("Backed but not stated : deviation", finding)

    def test_the_membership_gate_stays_silent_on_the_derived_set(self):
        good = sorted(self.facts["backed_gates"].members)
        clean = ("<!-- mokata:gates backed -->\n" + " ".join("`%s`" % g for g in good)
                 + "\n<!-- /mokata:gates -->\n")
        self.assertEqual([], pc.grade_rosters("x.md", clean, self.facts, self.vocabulary))

    def test_the_membership_gate_grades_the_run_state_roster_too(self):
        offender = (
            "<!-- mokata:gates run-state -->\n"
            "`approach-approval`, `spec-persisted`, `no-code-without-failing-test` and "
            "`self-protect`\n"
            "<!-- /mokata:gates -->\n"
        )
        [finding] = pc.grade_rosters("x.md", offender, self.facts, self.vocabulary)
        self.assertIn("stated but not run-state : self-protect", finding)
        self.assertIn("Run-state but not stated : spec-scope", finding)

    # -- the corpus gate --------------------------------------------------------------------
    def test_the_corpus_gate_catches_an_unmarked_enumeration(self):
        offender = (
            "There are nine backed gates — `write-gate`, `secret-guard` and `spec-persisted` "
            "among them.\n"
        )
        [finding] = pc.unmarked_rosters("x.md", offender, self.vocabulary)
        self.assertIn("outside a <!-- mokata:gates backed --> marker", finding)
        self.assertIn("secret-guard", finding)

    def test_the_corpus_gate_does_not_fire_inside_a_marker(self):
        good = sorted(self.facts["backed_gates"].members)
        inside = ("<!-- mokata:gates backed -->\n"
                  "There are backed gates: " + " ".join("`%s`" % g for g in good) + "\n"
                  "<!-- /mokata:gates -->\n")
        self.assertEqual([], pc.unmarked_rosters("x.md", inside, self.vocabulary))

    def test_the_corpus_gate_tolerates_a_single_gate_named_in_passing(self):
        passing = "Ahead of all four, `self-protect` refuses a write to an installed package.\n"
        self.assertEqual([], pc.unmarked_rosters("x.md", passing, self.vocabulary))


@unittest.skipUnless(os.path.exists(SYNC_SH),
                     "sync-public.sh is dev-only, excluded from the public mirror")
class WordSpellingUsageTest(unittest.TestCase):
    """Direction 2 of `WORDS_IN_USE`, alone in its own class because it is the ONE half that reads
    the derived corpus. Its twin, `test_every_number_word_the_pages_use_still_resolves`, stays with
    the synthetic battery and keeps running on the mirror."""

    def test_no_page_uses_a_number_word_that_is_not_pinned(self):
        """A page reaching for a new spelling reds until it is pinned in `WORDS_IN_USE`, rather
        than joining the ungraded set quietly."""
        unpinned = {}
        for rel in pc.public_claim_files(ROOT):
            for word in pc.word_numbers_in_use(_read(rel)):
                if word not in WORDS_IN_USE:
                    unpinned.setdefault(word, []).append(rel)
        self.assertEqual({}, unpinned, "add these to WORDS_IN_USE with a probe: %s" % unpinned)


class CorpusDerivationRefusesToGuessTest(unittest.TestCase):
    """The two ways the corpus cannot be derived, each with its OWN representation (§7g).

    UNGUARDED: every case builds its own root in a temp directory, so nothing here depends on the
    mirror boundary and the whole class runs on both sides — which is the point, since what it
    pins is how the derivation behaves when the boundary is missing.

    Before this stage there was ONE representation and it was an accident: `read_script` returned
    `None`, `exclude_entries` dereferenced it, and every caller got `AttributeError: 'NoneType'
    object has no attribute 'splitlines'` from two modules away. The friendly message below it was
    unreachable."""

    def _root(self, script=None):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        if script is not None:
            os.makedirs(os.path.join(root, "scripts"))
            with open(os.path.join(root, "scripts", "sync-public.sh"), "w",
                      encoding="utf-8") as fh:
                fh.write(script)
        return root

    def test_an_ABSENT_script_names_itself_and_points_at_the_decorator(self):
        """The mirror's state. The failure must say what is missing and what the remedy is —
        `AttributeError` two modules away said neither."""
        with self.assertRaises(pc.MirrorScriptAbsent) as caught:
            pc.public_claim_files(self._root())
        message = str(caught.exception)
        self.assertIn("scripts/sync-public.sh is not in this tree", message)
        self.assertIn("skipUnless", message)
        self.assertIn("never a setUpClass skip", message)

    def test_a_PRESENT_script_with_no_literal_excludes_is_a_DIFFERENT_failure(self):
        """The second representation, and previously dead code: the deref happened first, so this
        message could not be reached however the script was written. A glob-only script parses
        fine and yields nothing usable — which is not the same fact as no script at all."""
        globs_only = "rsync -a \\\n  --exclude='*.pyc' \\\n  --exclude='/mokata-*/' \\\n  ./ x/\n"
        with self.assertRaises(AssertionError) as caught:
            pc.public_claim_files(self._root(globs_only))
        self.assertNotIsInstance(caught.exception, pc.MirrorScriptAbsent,
                                 "an empty read and an absent script must not share a message")
        self.assertIn("yielded NO literal --exclude entries", str(caught.exception))

    def test_the_derivation_never_answers_with_an_EMPTY_corpus(self):
        """The failure mode `read_script`'s own docstring warns about, from the other end: an
        empty exclude set would grade every internal page as public. Both refusals above are
        raises precisely so no caller can receive one silently."""
        for root in (self._root(), self._root("rsync -a --exclude='*.pyc' ./ x/\n")):
            with self.assertRaises(AssertionError):
                pc.public_claim_files(root)


# ---- 5 · the control on the guard itself --------------------------------------------------------

class MirrorGuardControlTest(unittest.TestCase):
    """★ A SKIP MUST NOT READ AS A PASS.

    Adding `@unittest.skipUnless` above traded a loud crash on the mirror for a SILENT SKIP
    everywhere, and NOTHING GOES RED EITHER WAY — a dev tree that quietly stopped running the
    corpus gates would look exactly like a dev tree that runs them and agrees. That is
    `PYYAML-SKIP-CLUSTER`, stage 2 of this very release: a job that skipped 17 tests and reported
    OK. The remedy for the mirror boundary must not re-introduce the defect the release is named
    for, so the guard gets a control.

    UNGUARDED on purpose — it must run on BOTH sides, because each side is an assertion:

      * in a DEV tree the guarded classes must RUN, with zero skips;
      * on the MIRROR they must SKIP CLEANLY, and the registry derivations must still run.

    DERIVED, not typed. Which classes are guarded is read off THIS FILE'S OWN SOURCE with
    `_shipped_reads.guard_of` — the audited reader the stage-28 mirror sweep grades the whole
    shipped corpus with. A hand-typed list here would drift the moment a class gained or lost a
    decorator, and drift silently, which is the failure it exists to catch. Reading this file is
    not a boundary read: `tests/` ships."""

    def _classes_by_guard(self):
        """This module's TestCase classes split into (guarded, unguarded) by the guard shape
        `_shipped_reads.guard_of` reads off the class's decorators. This class is excluded — it is
        the instrument, not a subject, and running it inside itself would recurse."""
        with open(os.path.abspath(__file__), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        guarded, unguarded = [], []
        for stmt in tree.body:
            if not isinstance(stmt, ast.ClassDef) or stmt.name == type(self).__name__:
                continue
            obj = globals().get(stmt.name)
            if not (isinstance(obj, type) and issubclass(obj, unittest.TestCase)):
                continue
            (guarded if sr.guard_of(stmt) == sr.GUARD_DECORATOR else unguarded).append(obj)
        return guarded, unguarded

    @staticmethod
    def _run(cls):
        """Run one class in-process and hand back its result. The real thing, not an inspection of
        `__unittest_skip__`: `skipUnless` sets no attribute at all when its condition HOLDS, and an
        attribute check would in any case only see the decorator — never a skip arriving by some
        other route, which is precisely how a skip cluster goes unnoticed."""
        return unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(
            unittest.TestLoader().loadTestsFromTestCase(cls))

    def test_both_sets_are_non_empty_so_neither_assertion_below_is_vacuous(self):
        """A control that grades an empty set passes forever. Both halves of the split must exist:
        something is guarded, and something is not."""
        guarded, unguarded = self._classes_by_guard()
        self.assertTrue(
            guarded, "no class in this file carries the mirror decorator, so the control below "
                     "asserts nothing — either the decorators were removed or `guard_of` stopped "
                     "recognising them, and both are the bug this exists to catch")
        self.assertTrue(
            unguarded, "every class is guarded, so the ENTIRE file skips on the mirror — the "
                       "9-backed-gates derivation would stop being checked where it matters most")

    def test_the_guarded_classes_RAN_here_and_would_SKIP_only_on_the_mirror(self):
        """The control proper. In a dev tree a guarded class that skipped is a silent hole; on the
        mirror one that RAN is the crash this stage fixed, coming back."""
        guarded, _ = self._classes_by_guard()
        dev = bool(dev_tree_witnesses())
        for cls in guarded:
            result = self._run(cls)
            self.assertGreater(
                result.testsRun, 0,
                "%s contributed no tests at all — a class decorator keeps its tests in `Ran N` "
                "even when they skip, so zero means they vanished (the setUpClass-skip shape "
                "stage 11 measured: `Ran 0 ... skipped=1`)" % cls.__name__)
            self.assertEqual(
                [], result.errors,
                "%s ERRORED rather than running or skipping: %s" % (cls.__name__, result.errors))
            if dev:
                self.assertEqual(
                    [], [str(test) for test, _ in result.skipped],
                    "%s SKIPPED in a DEV tree (internal siblings present: %s). Its decorator "
                    "probes %s under %s, so either the mirror script was deleted or SYNC_SH "
                    "points somewhere it is not. A skip that reads as a pass is "
                    "PYYAML-SKIP-CLUSTER in a new coat: the corpus gates would be grading nothing "
                    "and the suite would still say OK."
                    % (cls.__name__, ", ".join(dev_tree_witnesses()),
                       MIRROR_SCRIPT_REL, ROOT))
                self.assertEqual([], result.failures, "%s failed: %s"
                                 % (cls.__name__, result.failures))
            else:
                self.assertEqual(
                    result.testsRun, len(result.skipped),
                    "%s ran %d tests on a tree with no sync-public.sh; every one of them must "
                    "skip, because the corpus they grade cannot be derived there"
                    % (cls.__name__, result.testsRun))

    def test_the_boundary_is_ALL_OR_NOTHING_so_a_mispointed_constant_cannot_pass_as_a_mirror(self):
        """`sync-public.sh` never leaves alone: the same commit that holds it back holds back
        `release.sh`, `CLAUDE.md` and `docs/build/`. So a tree carrying any of those and NOT the
        script is not a mirror — it is a dev tree whose boundary has been deleted or whose
        `SYNC_SH` points at nothing, and either one would silently skip every guarded class.
        Stage 11's companion, made to serve the control instead of the batteries."""
        witnesses = dev_tree_witnesses()
        if witnesses:
            self.assertTrue(
                os.path.exists(SYNC_SH),
                "%s present, so this is the PRIVATE tree — but no %s resolves under %s. Every "
                "corpus class would skip and the suite would report OK."
                % (", ".join(witnesses), MIRROR_SCRIPT_REL, ROOT))
        else:
            self.assertFalse(
                os.path.exists(SYNC_SH),
                "no internal path ships, so this is the public mirror — yet sync-public.sh is "
                "here. The mirror is carrying a file the boundary exists to hold back.")

    def test_the_registry_derivations_run_on_BOTH_sides_of_the_boundary(self):
        """The half that must never be guarded. `docs/index.md` claiming 10 backed gates against a
        code truth of 9 is the defect this whole guard was built for, and the mirror is the tree
        that ships it — so the derivation of the 9 runs there, decorator or no decorator."""
        _, unguarded = self._classes_by_guard()
        self.assertIn(DerivationTest, unguarded,
                      "DerivationTest reads no corpus and must never carry the mirror decorator")
        self.assertIn(PlantedOffenderTest, unguarded,
                      "the planted offenders supply their own text — guarding them would skip the "
                      "whole §7i battery on the mirror to be rid of a read it does not make")
        for cls in (DerivationTest, PlantedOffenderTest):
            result = self._run(cls)
            self.assertGreater(result.testsRun, 0, cls.__name__)
            self.assertEqual([], result.skipped, "%s skipped: %s" % (cls.__name__, result.skipped))
            self.assertEqual(([], []), (result.failures, result.errors),
                             "%s: %s %s" % (cls.__name__, result.failures, result.errors))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
