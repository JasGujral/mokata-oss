"""The probe's safety claim, DERIVED — "a `probe/**` push cannot start a publish".

`windows-probe.yml` is a development aid that fires on `push: branches: ['probe/**']`. Its header
states, in prose, which workflows such a push can and cannot start. Prose is exactly what doc 85
§7h convicts: a comment asserting a fact that nothing checks. So the fact is checked here, by
parsing every `on:` block in `.github/workflows/` and simulating the ref.

WHY THIS IS WORTH A FILE. The probe exists to be pushed casually and often — that is its whole
value — and the repository it is pushed to is the PUBLIC MIRROR, which is where releases are cut
from. The question "can a careless branch name reach the PyPI upload" therefore has to have a
derived answer, not a remembered one. `release.yml` is tag-triggered today; if someone ever adds a
branch trigger to it, this file reds before the probe becomes a publishing vector.

⚠ THE ONE THING THIS CANNOT SEE is a PULL REQUEST. `ci.yml` carries a bare `pull_request:` trigger,
which fires on PR open/sync regardless of branch name — so OPENING a PR from a probe branch starts
the full 35-minute matrix. That is not a publish and not a safety problem, but it defeats the
probe's purpose, and it is asserted below as a KNOWN reachable workflow rather than left for
someone to rediscover. §7g: "cannot fire" and "fires only if you also open a PR" are different
facts and do not get to share a representation.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import re
import unittest

from _support import sample_manifest_data  # noqa: F401  (path-fix side-effect)

import _workflow_pins as wp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOWS = os.path.join(ROOT, ".github", "workflows")
PROBE = "windows-probe.yml"

#: The ref a probe push actually creates. Not a branch NAME — the full ref, because the tag/branch
#: distinction is the whole of the release.yml argument and a bare name erases it.
PROBE_REF = "refs/heads/probe/name-invariant"

#: The jobs in the tree that PUBLISH. Named, so that "release.yml is unreachable" is a statement
#: about something rather than about a filename.
PUBLISHING_JOBS = ("pypi", "github-release")


def _load(name):
    with open(os.path.join(WORKFLOWS, name), "r", encoding="utf-8") as handle:
        return wp.safe_load(handle.read(), "derive which workflows a probe push can start")


def _on(doc):
    """The `on:` block. PyYAML resolves the bare key `on` to the BOOLEAN True (YAML 1.1), which is
    why every reader of a GitHub workflow has to look in two places — a real trap, not a nicety."""
    if "on" in doc:
        return doc["on"]
    return doc[True]


def _branch_matches(pattern, branch):
    """GitHub's branch-filter glob, enough of it to answer this question.

    `*` matches within one path segment; `**` matches across segments. Everything else is literal.
    Implemented rather than borrowed from `fnmatch` precisely because `fnmatch` gives `*` the
    across-segment meaning, which would make `master` look like a match for `probe/**` patterns
    and quietly turn this whole file green for the wrong reason.
    """
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.fullmatch("".join(out), branch) is not None


def _fires_on_branch_push(doc, ref):
    """Whether a plain `git push` of `ref` starts this workflow.

    Only `push:` is consulted, deliberately. `pull_request`, `schedule`, `workflow_dispatch`,
    `release` and `branch_protection_rule` are all started by something OTHER than pushing a
    branch, and folding them in here would answer a different question than the one the probe's
    header claims to answer.
    """
    on = _on(doc)
    if not isinstance(on, dict) or "push" not in on:
        return False
    push = on["push"] or {}
    if not isinstance(push, dict):        # `push:` with no filters — fires on every branch AND tag
        return True
    if ref.startswith("refs/tags/"):
        patterns = push.get("tags")
        if patterns is None:
            # No `tags:` filter. A `push:` carrying only `branches:` never fires for a tag ref.
            return "branches" not in push
        return any(_branch_matches(p, ref[len("refs/tags/"):]) for p in patterns)
    patterns = push.get("branches")
    if patterns is None:
        return "tags" not in push
    return any(_branch_matches(p, ref[len("refs/heads/"):]) for p in patterns)


class TestTheProbeRefStartsNothingElse(unittest.TestCase):

    def test_a_probe_branch_push_starts_the_probe_and_only_the_probe(self):
        """★ THE CLAIM. Derived over the whole directory, so a NEW workflow that opens itself to
        `probe/**` — or to every branch — reds here rather than being discovered by a bill."""
        fired = sorted(name for name in wp.workflow_files(WORKFLOWS)
                       if _fires_on_branch_push(_load(name), PROBE_REF))
        self.assertEqual(
            [PROBE], fired,
            "pushing %s starts more than the probe: %r. A probe is pushed casually and often; "
            "anything else reachable from that ref is a cost or a risk nobody signed up for."
            % (PROBE_REF, fired))

    def test_release_yml_is_reachable_only_from_a_TAG(self):
        """The publishing workflow, named directly rather than left to the sweep above. A sweep
        that stopped seeing `release.yml` at all would pass the test above for the wrong reason."""
        doc = _load("release.yml")
        self.assertFalse(
            _fires_on_branch_push(doc, PROBE_REF),
            "release.yml fires on a probe BRANCH push — it is the only workflow in this tree with "
            "a publishing job, and it must be reachable from a tag and nothing else")
        self.assertTrue(
            _fires_on_branch_push(doc, "refs/tags/v0.0.18"),
            "release.yml no longer fires on a `v*` tag, so the negative above proves nothing — "
            "an unreachable workflow is trivially unreachable from a branch")

    def test_the_publishing_jobs_are_where_this_file_says_they_are(self):
        """The join. Without it, "release.yml is tag-only" and "publishing lives in release.yml"
        are two adjacent facts, and the second one is the one that can rot silently."""
        found = {}
        for name in wp.workflow_files(WORKFLOWS):
            for job_id in (_load(name).get("jobs") or {}):
                if job_id in PUBLISHING_JOBS:
                    found.setdefault(name, []).append(job_id)
        self.assertEqual(
            sorted(found), ["release.yml"],
            "a job named %r now lives outside release.yml: %r. The probe's safety argument is "
            "'the publishing workflow is tag-only'; a publishing job somewhere else is outside "
            "that argument entirely." % (list(PUBLISHING_JOBS), found))

    def test_opening_a_PR_from_a_probe_branch_DOES_start_ci(self):
        """§7g, the honest half. The probe is safe, not invisible — and the difference is a thing
        a reader has to be told rather than left to infer from a green suite."""
        on = _on(_load("ci.yml"))
        self.assertIn(
            "pull_request", on,
            "ci.yml lost its `pull_request` trigger — the warning in this module's docstring and "
            "in the probe's header is now stale prose, which is worse than no warning")


class TestTheProbeIsShapedTheWayItsHeaderClaims(unittest.TestCase):

    def test_the_module_list_lives_in_exactly_one_place(self):
        """The brief's requirement, as a property: retargeting the probe is a ONE-LINE edit. A
        second copy of the list (a per-job override, a second env block) is the drift that makes
        a probe answer a question you did not ask."""
        doc = _load(PROBE)
        self.assertIn(
            "PROBE_MODULES", doc.get("env") or {},
            "PROBE_MODULES is no longer a workflow-level env key, so the 'one line to edit' "
            "claim in the probe's header is false")
        for job_id, job in (doc.get("jobs") or {}).items():
            self.assertNotIn(
                "PROBE_MODULES", (job.get("env") or {}),
                "job %r overrides PROBE_MODULES; there are now two answers to 'what does the "
                "probe run'" % job_id)
            for index, step in enumerate(job.get("steps") or []):
                self.assertNotIn(
                    "PROBE_MODULES", (step.get("env") or {}),
                    "jobs.%s.steps[%d] overrides PROBE_MODULES" % (job_id, index))

    def test_the_probe_names_at_least_one_module_and_they_all_exist(self):
        """Anti-vacuity. An empty or misspelled list makes `python -m unittest -v` run nothing (or
        error), and a probe that exercises no test is a green that means nothing."""
        modules = ((_load(PROBE).get("env") or {})["PROBE_MODULES"]).split()
        self.assertTrue(modules, "PROBE_MODULES is empty")
        for module in modules:
            self.assertTrue(
                os.path.isfile(os.path.join(ROOT, "tests", module + ".py")),
                "PROBE_MODULES names `%s`, which is not a module in tests/" % module)

    def test_the_probe_is_bounded(self):
        """`ci.yml` ran unbounded Windows legs for months and one of them wedged for 54 minutes,
        costing the log download for all twelve legs of that run. A probe inherits that failure
        mode and is worth less than the gate, so its ceiling is asserted rather than assumed."""
        for job_id, job in (_load(PROBE).get("jobs") or {}).items():
            self.assertIsInstance(
                job.get("timeout-minutes"), int,
                "the probe job %r declares no timeout-minutes" % job_id)
            self.assertLessEqual(
                job["timeout-minutes"], 15,
                "probe job %r allows %s minutes; the probe's only justification is its wall "
                "clock, and a slow probe is the matrix with fewer legs"
                % (job_id, job["timeout-minutes"]))

    def test_the_probe_runs_on_windows_only(self):
        """It is not a second CI. Every job here is a Windows job or the file has lost its point."""
        for job_id, job in (_load(PROBE).get("jobs") or {}).items():
            self.assertIn(
                "windows", str(job.get("runs-on")),
                "probe job %r does not run on Windows: %r" % (job_id, job.get("runs-on")))

    def test_the_probe_declares_no_write_permission(self):
        """A workflow that fires on an easily-created branch name gets the read-only token."""
        doc = _load(PROBE)
        self.assertEqual({"contents": "read"}, doc.get("permissions"))
        for job_id, job in (doc.get("jobs") or {}).items():
            self.assertEqual(
                {"contents": "read"}, job.get("permissions"),
                "probe job %r re-declares permissions and they are not read-only" % job_id)


class TestTheMatcherItself(unittest.TestCase):
    """§7i — the branch matcher decides every answer above, so it is graded on cases where a
    wrong implementation gives a DIFFERENT answer, not on cases where everything agrees."""

    def test_double_star_crosses_a_slash(self):
        self.assertTrue(_branch_matches("probe/**", "probe/a/b"))
        self.assertTrue(_branch_matches("probe/**", "probe/a"))

    def test_single_star_does_not_cross_a_slash(self):
        """The case `fnmatch` gets wrong, and the reason this matcher is hand-written."""
        self.assertFalse(_branch_matches("probe/*", "probe/a/b"))
        self.assertTrue(_branch_matches("probe/*", "probe/a"))

    def test_master_is_not_a_probe_branch(self):
        self.assertFalse(_branch_matches("probe/**", "master"))
        self.assertFalse(_branch_matches("master", "probe/x"))

    def test_a_literal_pattern_is_not_a_prefix_match(self):
        """`re.fullmatch`, not `re.match`. A prefix matcher would report that pushing
        `master-scratch` starts CI, and every claim in this file would be one bug from meaningless."""
        self.assertFalse(_branch_matches("master", "master-scratch"))

    def test_a_dot_in_a_pattern_is_literal(self):
        self.assertFalse(_branch_matches("v1.0", "v1x0"))


class TestTheSimulatorItself(unittest.TestCase):
    """The same treatment for `_fires_on_branch_push`: synthetic `on:` blocks, one shape each."""

    def _on_block(self, block):
        return _fires_on_branch_push({"on": block}, PROBE_REF)

    def test_a_bare_push_fires_on_everything(self):
        self.assertTrue(self._on_block({"push": None}))

    def test_a_branch_filter_that_excludes_the_ref_does_not_fire(self):
        self.assertFalse(self._on_block({"push": {"branches": ["master", "main"]}}))

    def test_a_tags_only_push_does_not_fire_for_a_branch(self):
        """release.yml's exact shape, isolated from release.yml."""
        self.assertFalse(self._on_block({"push": {"tags": ["v*"]}}))

    def test_a_tags_only_push_DOES_fire_for_a_matching_tag(self):
        self.assertTrue(_fires_on_branch_push({"on": {"push": {"tags": ["v*"]}}},
                                              "refs/tags/v0.0.18"))

    def test_a_branches_only_push_does_not_fire_for_a_tag(self):
        self.assertFalse(_fires_on_branch_push({"on": {"push": {"branches": ["**"]}}},
                                               "refs/tags/v0.0.18"))

    def test_triggers_that_are_not_push_do_not_count(self):
        """A workflow reachable only by `workflow_dispatch` or `schedule` is not started by a
        push, and folding those in would make the headline claim false for four opt-in workflows
        that a probe push genuinely cannot reach."""
        self.assertFalse(self._on_block({"workflow_dispatch": None, "schedule": [{"cron": "0 0 * * *"}]}))
        self.assertFalse(self._on_block({"pull_request": None}))

    def test_the_yaml_true_key_is_read(self):
        """PyYAML resolves the bare key `on` to `True`. If `_on` stopped handling that, every
        workflow would look trigger-less and this whole module would go green while checking
        nothing — the §7i failure with the sign reversed."""
        self.assertTrue(_fires_on_branch_push({True: {"push": None}}, PROBE_REF))


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
