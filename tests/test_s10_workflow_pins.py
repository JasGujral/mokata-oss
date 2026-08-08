"""Stage 10 — PIN-SUBSTRING-COMMENT-HOLE breadth: ONE sweep pins every workflow's action refs.

This stage is NOT a fix. Every action reference in all nine workflows was already SHA-pinned
before it started, and still is. What was missing was anything that would NOTICE if one stopped
being: the suite's only generic assertion, `test_stage68_supply_chain.py::
test_pypi_actions_are_sha_pinned`, walked `release.yml`'s `pypi` job and nothing else, so
`ci.yml`, `codeql.yml`, `docs.yml`, `embeddings-leg.yml`, `live-db-legs.yml`,
`quality-at-scale.yml`, `real-crg.yml` and `scorecard.yml` had no generic coverage at all — and
it skipped itself away entirely when PyYAML was absent.

THE §7i PROBLEM THIS FILE HAS TO SOLVE, stated plainly: a sweep over a tree with zero offenders
grades nothing. Asserting "the healthy tree is healthy" is the shape the 7i audit convicted, and
a breadth stage builds it BY CONSTRUCTION unless it is deliberately designed out. So:

  * the sweep (`tests/_workflow_pins.py`) is a pure function over a SUPPLIED CORPUS;
  * TestSyntheticOffenders hands it corpora that are BROKEN IN ONE SPECIFIC WAY EACH and requires
    it to catch them — a tag pin, a short SHA, a branch, a missing ref, an uppercase digest, a
    tag-pinned reusable-workflow call, a mutable docker tag;
  * TestTheCommentHole hands it the exact defect this row is named after — a step that is
    tag-pinned under a comment displaying a correct 40-hex pin. A grep-based sweep passes that
    corpus. This one must not;
  * TestTheSweepSeesTheWholeTree pins that the real-tree run is not vacuous — every workflow
    contributes references. "No offenders found" means nothing if nothing was looked at.

And TestFailsLoudWithoutPyYAML pins §7g: the missing-parser path RAISES. It does not skip.
"""

import os
import shutil
import tempfile
import unittest
from unittest import mock

from _support import sample_manifest_data  # noqa: F401  (path-fix side-effect)

import _workflow_pins as wp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_CORPUS = os.path.join(ROOT, ".github", "workflows")

# The nine workflows the row enumerates. Named rather than counted so that DELETING one is as
# visible as adding one — a count alone stays green through a swap.
NINE_WORKFLOWS = (
    "ci.yml", "codeql.yml", "docs.yml", "embeddings-leg.yml", "live-db-legs.yml",
    "quality-at-scale.yml", "real-crg.yml", "release.yml", "scorecard.yml",
)

GOOD_SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"


class _CorpusMixin(unittest.TestCase):
    """Writes a throwaway corpus directory. Deliberately NOT a checked-in fixture file: a real
    .yml carrying a tag-pinned action, sitting in the repo, is something the NEXT supply-chain
    sweep (or Scorecard, or a reviewer) has to be told to ignore. The offender exists only for
    the length of the test that needs it."""

    def setUp(self):
        self.corpus = tempfile.mkdtemp(prefix="wfpins-")
        self.addCleanup(shutil.rmtree, self.corpus, ignore_errors=True)

    def write(self, name, text):
        with open(os.path.join(self.corpus, name), "w", encoding="utf-8") as fh:
            fh.write(text)

    def workflow(self, uses, job="build"):
        """A minimal but structurally real workflow carrying one `uses:`."""
        self.write("w.yml",
                   "name: W\non:\n  push:\njobs:\n  %s:\n    runs-on: ubuntu-latest\n"
                   "    steps:\n      - uses: %s\n" % (job, uses))
        return self.corpus


class TestTheSweepSeesTheWholeTree(unittest.TestCase):
    """The real tree is clean — but first prove the sweep actually looked at it."""

    def test_the_corpus_is_the_nine_workflows(self):
        self.assertEqual(
            set(NINE_WORKFLOWS), set(wp.workflow_files(REAL_CORPUS)),
            "the set of workflow files changed. A NEW workflow is swept automatically, but it "
            "must be added here so its arrival is a deliberate act rather than a silent one.")

    def test_every_workflow_contributes_references(self):
        """The anti-vacuity pin. A sweep that parsed nothing, or walked the wrong key, would
        report zero offenders just as loudly as a healthy tree does."""
        refs = wp.action_refs(REAL_CORPUS)
        seen = {r.workflow for r in refs}
        missing = sorted(set(NINE_WORKFLOWS) - seen)
        self.assertEqual(
            [], missing,
            "these workflows yielded NO action references at all, so the sweep is not "
            "covering them (a parse failure and an action-free workflow look identical "
            "here): %s" % ", ".join(missing))
        self.assertGreaterEqual(
            len(refs), 30,
            "the real tree carries ~38 action references; the sweep found only %d, which "
            "means it is walking a narrower structure than it should be" % len(refs))

    def test_no_action_reference_in_the_real_tree_is_unpinned(self):
        """The property itself, over all nine — what the row asked for."""
        offenders = wp.unpinned(REAL_CORPUS)
        self.assertEqual(
            (), offenders,
            "every third-party action must be pinned to an immutable 40-hex commit SHA; a tag "
            "or branch is repointable by its owner at any time. Offenders:\n%s"
            % wp.describe(offenders))


class TestSyntheticOffenders(_CorpusMixin):
    """§7i — each corpus is broken in exactly one way, and the sweep must catch each."""

    def _one_offender(self, uses):
        offenders = wp.unpinned(self.workflow(uses))
        self.assertEqual(
            1, len(offenders),
            "expected `uses: %s` to be caught as unpinned; got %r" % (uses, offenders))
        return offenders[0]

    def test_a_tag_pinned_action_is_an_offender(self):
        """THE synthetic offender the stage turns on: the healthy tree has none of these."""
        self.assertEqual("v4", self._one_offender("actions/checkout@v4").ref)

    def test_a_semver_tag_is_an_offender(self):
        self._one_offender("actions/checkout@v7.0.1")

    def test_a_branch_ref_is_an_offender(self):
        self._one_offender("actions/checkout@main")

    def test_a_short_sha_is_an_offender(self):
        """Abbreviated SHAs are not immutable references — GitHub resolves them, and a 7-hex
        prefix is collidable. Only the full 40 counts."""
        self._one_offender("actions/checkout@" + GOOD_SHA[:7])

    def test_an_uppercase_sha_is_an_offender(self):
        """Not pedantry: `[0-9a-fA-F]` would also match a 40-char string that git will not
        resolve, so the pin is on the canonical lowercase form."""
        self._one_offender("actions/checkout@" + GOOD_SHA.upper())

    def test_no_ref_at_all_is_an_offender(self):
        self._one_offender("actions/checkout")

    def test_a_mutable_docker_tag_is_an_offender(self):
        self._one_offender("docker://alpine:3.20")

    def test_a_tag_pinned_reusable_workflow_call_is_an_offender(self):
        """Job-level `uses:` — a whole workflow, mutable in exactly the same way, and invisible
        to any sweep that only walks `steps`."""
        self.write("w.yml",
                   "name: W\non:\n  push:\njobs:\n  call:\n"
                   "    uses: some/repo/.github/workflows/x.yml@v1\n")
        offenders = wp.unpinned(self.corpus)
        self.assertEqual(1, len(offenders),
                         "a tag-pinned reusable-workflow call went uncaught: %r" % (offenders,))
        self.assertEqual("jobs.call", offenders[0].where)

    def test_offenders_are_found_in_EVERY_file_of_a_corpus(self):
        """A sweep that stops at the first file, or at the first offender, still 'finds
        offenders' on a one-file corpus. Three files, three offenders."""
        for i, name in enumerate(("a.yml", "b.yaml", "c.yml")):
            self.write(name,
                       "name: W%d\non:\n  push:\njobs:\n  j%d:\n    runs-on: ubuntu-latest\n"
                       "    steps:\n      - uses: actions/checkout@v%d\n" % (i, i, i))
        offenders = wp.unpinned(self.corpus)
        self.assertEqual(
            {"a.yml", "b.yaml", "c.yml"}, {o.workflow for o in offenders},
            "the sweep did not report an offender from every file: %r" % (offenders,))


class TestWhatIsLegitimatelyExempt(_CorpusMixin):
    """A sweep that flags everything is as useless as one that flags nothing — it gets muted."""

    def test_a_local_action_needs_no_pin(self):
        """`./.github/actions/foo` ships in this repo and moves only when the repo moves."""
        self.assertEqual((), wp.unpinned(self.workflow("./.github/actions/mokata-check")))

    def test_a_digest_pinned_docker_image_is_pinned(self):
        self.assertEqual(
            (), wp.unpinned(self.workflow("docker://alpine@sha256:" + "a" * 64)))

    def test_a_correctly_pinned_action_is_not_an_offender(self):
        self.assertEqual((), wp.unpinned(self.workflow("actions/checkout@" + GOOD_SHA)))

    def test_a_subpath_action_is_pinned_by_its_ref(self):
        """`github/codeql-action/init@<sha>` — the subpath is part of the name, not the ref."""
        self.assertEqual(
            (), wp.unpinned(self.workflow("github/codeql-action/init@" + GOOD_SHA)))


class TestTheCommentHole(_CorpusMixin):
    """The defect this row is named after, reproduced as a corpus.

    doc 84: "a presence pin that greps a whole FILE for a literal is satisfied by any COMMENT
    containing that literal". A workflow that documents its own pinning policy — showing a
    correct 40-hex pin in a comment — while the actual step is tag-pinned, is GREEN under any
    grep and RED under a parse. That difference is the entire reason the sweep parses.
    """

    def test_a_correct_pin_in_a_COMMENT_does_not_excuse_a_tag_pinned_step(self):
        self.write("w.yml",
                   "name: W\n"
                   "# Supply-chain policy: every action is pinned to a 40-hex commit SHA, e.g.\n"
                   "#   - uses: actions/checkout@%s # v7.0.1\n"
                   "on:\n  push:\n"
                   "jobs:\n  build:\n    runs-on: ubuntu-latest\n"
                   "    steps:\n      - uses: actions/checkout@v4\n" % GOOD_SHA)
        offenders = wp.unpinned(self.corpus)
        self.assertEqual(
            1, len(offenders),
            "a tag-pinned step went uncaught because the file also CONTAINS a correctly pinned "
            "example in a comment — PIN-SUBSTRING-COMMENT-HOLE exactly. The sweep must read the "
            "parsed structure, never the file's text. Offenders: %r" % (offenders,))
        self.assertEqual("v4", offenders[0].ref)

    def test_a_trailing_version_comment_does_not_become_part_of_the_ref(self):
        """The real tree writes `@<sha> # v7.0.1`. YAML ends the scalar at the ` #`, so the ref
        is the bare SHA — but if that ever stopped being true every pin would read as unpinned,
        and this is the assertion that would say so."""
        refs = wp.action_refs(self.workflow("actions/checkout@%s # v7.0.1" % GOOD_SHA))
        self.assertEqual(GOOD_SHA, refs[0].ref)
        self.assertTrue(refs[0].pinned)


class TestFailsLoudWithoutPyYAML(unittest.TestCase):
    """§7g — an absent answer is not an answer.

    The 17 other PyYAML call sites in this suite skip themselves when the parser is missing, so
    on such a runner the pinning property is unchecked AND the run reports OK. This sweep raises.
    """

    @staticmethod
    def _import_without_yaml(name, *args, **kwargs):
        if name == "yaml" or name.startswith("yaml."):
            raise ImportError("No module named 'yaml'")
        return _REAL_IMPORT(name, *args, **kwargs)

    def test_safe_load_raises_MissingParser_rather_than_skipping(self):
        with mock.patch("builtins.__import__", self._import_without_yaml):
            with self.assertRaises(wp.MissingParser) as caught:
                wp.safe_load("name: W\n")
        self.assertIn("HARD FAILURE", str(caught.exception),
                      "the error must SAY it is deliberate, or the next reader turns it back "
                      "into a skip")

    def test_the_sweep_itself_raises_rather_than_reporting_a_clean_tree(self):
        """The failure mode that matters. Returning () when the parser is missing is
        indistinguishable from 'every action is pinned' at every call site."""
        with mock.patch("builtins.__import__", self._import_without_yaml):
            with self.assertRaises(wp.MissingParser):
                wp.unpinned(REAL_CORPUS)

    def test_MissingParser_is_not_swallowed_as_an_ordinary_failure(self):
        """It must not be a subclass of anything a caller might already be catching around a
        parse (ValueError / the yaml error hierarchy) — it has to reach the top."""
        self.assertTrue(issubclass(wp.MissingParser, RuntimeError))
        self.assertFalse(issubclass(wp.MissingParser, ValueError))


_REAL_IMPORT = __import__


if __name__ == "__main__":
    unittest.main()
