"""Stage 7 — the dev repo is not the publishing repo, and every surface now SAYS so.

doc 102 exit criterion 6, FIRST half: *"a tag on the dev repo cannot read as a successful
publish."* (Stage 6 delivered the second half, the pinned asset set.)

WHAT IS BEING GRADED, in one sentence: **a repository that does not publish must not be able to
report a release the way a repository that published one does.** Tag `v0.0.17` on
`JasGujral/mokata` and all five guarded jobs skipped and the run concluded GREEN. It nearly
shipped: the 0.0.17 cut was instructed to tag the DEV repo and let `release.yml` publish, by a
coordinator that had already read those five guards in the same session. The full account is in
`tests/_release_repo_guards.py`.

THE SHAPE OF THIS FILE, and every part of it is a doc 85 rule:

  * **§7i, AND THE ROW STATED IT BEFORE THE STAGE OPENED: THIS CANNOT BE GRADED ON A HEALTHY TREE,
    AND IT MUST NOT BE GRADED ON A LIVE ONE.** The offender is a tag pushed to the wrong
    repository. A live tag is exactly what must not be created to test it, so `TestTheInverseGuard`
    feeds the condition two SYNTHETIC repository names and EXECUTES the refusal step under bash.
    Nothing here creates, pushes or deletes a tag, and nothing here contacts GitHub.
  * **§7i again** — `TestSyntheticOffenders` plants one defect per pure function. Once the tree is
    fixed it holds no offender, so a sweep that only read the real tree would pass having graded
    nothing.
  * **§7g** — five job classes, not two: PUBLISH-GUARDED · REFUSAL · UNGUARDED · NEVER-RUNS ·
    UNREADABLE. And `release-check` gets three exit codes, because "the versions disagree" and
    "another package answered" are different facts that used to share the number 1 by sharing 0.
  * **§7j, the class not the instance** — the guard set is DERIVED by ranging over `jobs:`, never
    typed. A sixth unguarded job reddens. The refusal is classified by what its condition DOES on
    two repository names, not by matching its text — `!=` and `==` differ by one character and both
    contain the mirror's name, so a substring test cannot tell them apart.
  * **The negative is a pin, not a sentence** — `TestTheMirrorPathIsUnchanged` asserts the set of
    jobs that run on the mirror is exactly the publish set, and that no publish job `needs:` the
    refusal (which would make the refusal's own skip gate the release on the mirror).

⚠ WHAT IS ONLY VERIFIABLE ONCE ACTIONS BILLING ON `JasGujral/mokata` IS RESTORED. That a tag
pushed there produces a RED run with this job's message in it is a claim about a run that cannot
happen today — the dev repo's Actions cannot start jobs. What is proven in-repo is that the
condition selects the dev repo and rejects the mirror, that the job depends on nothing that could
skip it, and that its step exits non-zero naming the mirror when executed verbatim. See the stage
report's standing obligation.
"""

import os
import subprocess
import sys
import unittest

import _support
from _support import sample_manifest_data  # noqa: F401  (path-fix side-effect)

import _release_repo_guards as rg
from mokata import __version__
from mokata.cli_commands.core import (
    RELEASE_CHECK_ANSWERED_ELSEWHERE,
    RELEASE_CHECK_MISMATCH,
    RELEASE_CHECK_OK,
    RELEASE_CHECK_UNRESOLVABLE_PACKAGE,
)
from mokata.packaging import (
    ANSWERED_IN_ROOT,
    ANSWERED_OUT_OF_ROOT,
    ANSWERED_UNRESOLVABLE,
    _VERSION_FIELDS,
    answering_package,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RELEASE_YML = os.path.join(ROOT, ".github", "workflows", "release.yml")
# INTERNAL: excluded from the public mirror twice (`sync-public.sh`'s `--exclude` list and its
# `INTERNAL_PATHS` guard), so every read of these two is class-decorator guarded below and
# `TestTheInternalScriptsAreAbsentTogether` keeps a DELETED one from hiding inside the skip
# (`SHIPPED-TEST-READS-INTERNAL-FILE`, stage 28; `tests/_shipped_reads.py` derives it).
RELEASE_SH = os.path.join(ROOT, "scripts", "release.sh")
SYNC_SH = os.path.join(ROOT, "scripts", "sync-public.sh")

# The version being cut, and it is the value a waiver's `through=` is judged against. Taken from
# the package rather than re-parsed out of pyproject.toml: `release-check` already refuses to tag
# unless the two agree, and `test_pin_drift` asserts it green on this tree, so this IS the
# pyproject version — derived through a guarded invariant rather than through a second reader.
VERSION_BEING_CUT = __version__


def _read(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _release_doc():
    return rg.safe_load(_read(RELEASE_YML), "grade the release workflow's repository guards")


# =================================================================================================
# ① THE WORKFLOW
# =================================================================================================


class TestTheGuardEvaluator(unittest.TestCase):
    """The synthetic repository name is the only way to ask this question safely."""

    def test_the_publish_guard_admits_the_mirror_and_rejects_the_dev_repo(self):
        guard = "github.repository == '%s'" % rg.PUBLISHING_REPOSITORY
        self.assertTrue(rg.evaluate_repository_guard(guard, rg.PUBLISHING_REPOSITORY))
        self.assertFalse(rg.evaluate_repository_guard(guard, rg.DEV_REPOSITORY))

    def test_the_inverse_guard_admits_the_dev_repo_and_rejects_the_mirror(self):
        """THE ROW'S OWN ACCEPTANCE TEST, quoted: *assert the job's `if` expression evaluates true
        for `JasGujral/mokata` and false for `JasGujral/mokata-oss`.*"""
        guard = "github.repository != '%s'" % rg.PUBLISHING_REPOSITORY
        self.assertTrue(rg.evaluate_repository_guard(guard, rg.DEV_REPOSITORY))
        self.assertFalse(rg.evaluate_repository_guard(guard, rg.PUBLISHING_REPOSITORY))

    def test_a_third_repository_is_answered_too(self):
        """A fork is not the mirror either, and it must not publish."""
        guard = "github.repository == '%s'" % rg.PUBLISHING_REPOSITORY
        self.assertFalse(rg.evaluate_repository_guard(guard, "someone-else/mokata-oss"))
        self.assertTrue(rg.evaluate_repository_guard(
            "github.repository != '%s'" % rg.PUBLISHING_REPOSITORY, "someone-else/mokata-oss"))

    def test_the_expression_wrapped_in_the_template_form_is_the_same_guard(self):
        self.assertTrue(rg.evaluate_repository_guard(
            "${{ github.repository == '%s' }}" % rg.PUBLISHING_REPOSITORY,
            rg.PUBLISHING_REPOSITORY))

    def test_an_unreadable_condition_raises_rather_than_reading_as_false(self):
        """§7g. "the guard says no" and "we could not read the guard" must not share a value — a
        sweep that returned False here would report a brand-new, unparseable condition as a
        correctly-guarded job."""
        for shape in ("github.event_name == 'push'",
                      "github.repository == \"JasGujral/mokata-oss\"",
                      "success() && github.repository == 'JasGujral/mokata-oss'",
                      "!cancelled()"):
            with self.subTest(shape=shape):
                with self.assertRaises(rg.UnreadableGuard):
                    rg.evaluate_repository_guard(shape, rg.PUBLISHING_REPOSITORY)

    def test_an_absent_condition_is_a_job_that_runs_everywhere(self):
        """Absent is not unreadable: a job with no `if:` runs on both repositories, and that is
        the ungated publish job every guard here exists to make impossible."""
        self.assertTrue(rg.runs_on_repository(None, rg.DEV_REPOSITORY))
        self.assertTrue(rg.runs_on_repository("", rg.PUBLISHING_REPOSITORY))
        self.assertEqual(rg.classify_condition(None), rg.GUARD_UNGUARDED)


class TestTheRealWorkflowsGuardSet(unittest.TestCase):
    """THE SET, DERIVED. Ranges over `jobs:`; nothing here types a job name or a count."""

    def setUp(self):
        self.doc = _release_doc()
        self.classes = rg.guard_classes(self.doc)

    def test_the_document_parses_and_has_jobs(self):
        self.assertTrue(self.classes, "release.yml has no jobs — the parse failed")

    def test_no_job_is_unguarded_unreadable_or_dead(self):
        """A SIXTH job with no `if:` reddens here, which is the only reason this is a set and not
        a list of five names."""
        for bad in (rg.GUARD_UNGUARDED, rg.GUARD_NEVER_RUNS, rg.GUARD_UNREADABLE):
            offenders = rg.jobs_in_class(self.doc, bad)
            self.assertEqual(
                offenders, (),
                "release.yml job(s) classified %s: %s. Every job must either publish ONLY on %s "
                "or refuse ONLY off it. If a new condition shape is legitimate, extend "
                "_release_repo_guards.evaluate_repository_guard — never let it read as guarded."
                % (bad, ", ".join(offenders), rg.PUBLISHING_REPOSITORY))

    def test_every_publishing_job_is_guarded_to_the_mirror(self):
        publish = rg.jobs_in_class(self.doc, rg.GUARD_PUBLISH)
        self.assertTrue(publish, "no job publishes from the mirror — the guards were removed")
        for job_id in publish:
            with self.subTest(job=job_id):
                condition = self.doc["jobs"][job_id]["if"]
                self.assertTrue(rg.runs_on_repository(condition, rg.PUBLISHING_REPOSITORY))
                self.assertFalse(rg.runs_on_repository(condition, rg.DEV_REPOSITORY))

    def test_exactly_one_job_refuses_off_the_mirror(self):
        """One voice, not two. Two refusal jobs would be two messages a reader has to reconcile,
        and neither would be the authority."""
        self.assertEqual(len(rg.jobs_in_class(self.doc, rg.GUARD_REFUSAL)), 1)

    def test_the_v0017_workflow_had_no_refusal_and_that_is_the_defect(self):
        """§7i — the guard is shown red on the KNOWN-BAD case, not only green on the fixed one.
        Every job at v0.0.17 was publish-guarded, so a tag on the dev repo selected NOTHING."""
        classes = rg.guard_classes(rg.V0017_JOBS_DOC)
        self.assertEqual(sorted(classes), sorted(rg.V0017_GUARDED_JOBS))
        self.assertEqual(set(classes.values()), {rg.GUARD_PUBLISH})
        self.assertEqual(rg.jobs_in_class(rg.V0017_JOBS_DOC, rg.GUARD_REFUSAL), ())
        # And what that meant on the dev repo: not one job selected, and no job to say so.
        selected = [job for job, cond in
                    ((j, rg.V0017_JOBS_DOC["jobs"][j]["if"]) for j in rg.V0017_GUARDED_JOBS)
                    if rg.runs_on_repository(cond, rg.DEV_REPOSITORY)]
        self.assertEqual(selected, [])


class TestTheInverseGuard(unittest.TestCase):
    """The refusal job itself: what selects it, what it depends on, and what it actually does."""

    def setUp(self):
        self.doc = _release_doc()
        refusals = rg.jobs_in_class(self.doc, rg.GUARD_REFUSAL)
        self.assertEqual(len(refusals), 1, "expected exactly one refusal job")
        self.job_id = refusals[0]

    def test_it_is_selected_on_the_dev_repo_and_not_on_the_mirror(self):
        condition = self.doc["jobs"][self.job_id]["if"]
        self.assertTrue(rg.runs_on_repository(condition, rg.DEV_REPOSITORY))
        self.assertFalse(rg.runs_on_repository(condition, rg.PUBLISHING_REPOSITORY))

    def test_it_depends_on_nothing(self):
        """⚠ THE ONE THAT WOULD BE THIS ROW AGAIN. `needs:` any publish-guarded job and the
        refusal inherits their skip on the dev repo — a job that fails to fire for exactly the
        reason it exists to report."""
        self.assertEqual(
            rg.job_needs(self.doc, self.job_id), (),
            "the refusal job declares `needs:` — on the dev repo its dependencies are skipped, so "
            "the refusal would be skipped too and the run would conclude green again.")

    @unittest.skipUnless(_support.BASH, _support.NO_BASH)
    def test_its_step_names_the_mirror_and_the_dev_repository(self):
        steps = rg.job_run_steps(self.doc, self.job_id)
        self.assertTrue(steps, "the refusal job has no `run:` step — it cannot fail")
        text = "\n".join(run for _name, run in steps)
        self.assertIn(rg.PUBLISHING_REPOSITORY, text,
                      "the refusal never names the repository the tag belongs on")

    def test_its_step_actually_fails_when_executed(self):
        """§7i at the shell level: the step is RUN, not read. Asserting that its source contains
        `exit 1` is asserting that a string is present; running it is asserting that a tag pushed
        here ends in a red job.

        ⚠ This is why the step spells `$GITHUB_REPOSITORY` rather than `${{ github.repository }}`
        — the expression form is expanded by the runner and would reach bash as an empty word, so
        the message a real run prints and the message this test grades would be different texts.
        """
        steps = rg.job_run_steps(self.doc, self.job_id)
        for name, run in steps:
            with self.subTest(step=name):
                env = dict(os.environ, GITHUB_REPOSITORY=rg.DEV_REPOSITORY)
                # `bash_argv`, not a bare argv[0] — see `tests/test_windows_shell_and_paths.py`.
                # ⚠ This site is the class's WORST shape, not its mildest: WSL's launcher also
                # exits non-zero, so `assertNotEqual(returncode, 0)` would have PASSED against a
                # shell that never read the step at all.
                proc = subprocess.run(_support.bash_argv("-c", run), env=env,
                                      stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT, universal_newlines=True)
                self.assertNotEqual(proc.returncode, 0,
                                    "the refusal step exited 0 — a silent skip with extra words")
                self.assertIn(rg.PUBLISHING_REPOSITORY, proc.stdout)
                self.assertIn(rg.DEV_REPOSITORY, proc.stdout,
                              "the refusal does not name the repository the tag landed on")


class TestTheMirrorPathIsUnchanged(unittest.TestCase):
    """THE NEGATIVE, as a pin rather than a sentence: this stage adds a voice, not a behaviour."""

    def setUp(self):
        self.doc = _release_doc()

    def test_the_jobs_that_run_on_the_mirror_are_exactly_the_publishing_jobs(self):
        selected = tuple(sorted(
            job_id for job_id, job in self.doc["jobs"].items()
            if rg.runs_on_repository((job or {}).get("if"), rg.PUBLISHING_REPOSITORY)))
        self.assertEqual(selected, rg.jobs_in_class(self.doc, rg.GUARD_PUBLISH),
                         "the set of jobs a tag on the mirror selects is no longer the publish "
                         "set — this stage was not allowed to change that")

    def test_no_publishing_job_depends_on_the_refusal(self):
        """The one way this stage COULD have broken publishing. The refusal job is skipped on the
        mirror; anything that `needs:` it would be skipped with it — a release that silently does
        not happen, which is the row itself, inverted."""
        refusal = rg.jobs_in_class(self.doc, rg.GUARD_REFUSAL)[0]
        for job_id in rg.jobs_in_class(self.doc, rg.GUARD_PUBLISH):
            with self.subTest(job=job_id):
                self.assertNotIn(refusal, rg.job_needs(self.doc, job_id))

    def test_the_publishing_jobs_are_the_five_the_row_recorded(self):
        """DECLARED, not derived — the v0.0.17 job ids are history and history cannot be derived.
        A publish job DISAPPEARING is as much a regression as one appearing unguarded, and only a
        transcribed record can notice it."""
        self.assertEqual(rg.jobs_in_class(self.doc, rg.GUARD_PUBLISH),
                         tuple(sorted(rg.V0017_GUARDED_JOBS)))


# =================================================================================================
# ② THE SCRIPT
# =================================================================================================


class TestTheInternalScriptsAreAbsentTogether(unittest.TestCase):
    """★ The companion the guard below cannot do without (stage 28), deliberately UNGUARDED.

    `release.sh` does not ship, so the battery that reads it must be allowed to skip on the public
    subset. Left alone, that same skip would swallow a DELETED release.sh here: the battery would
    vanish, the run would report OK, and "excluded from the mirror" and "someone deleted it" would
    share a green — §7g at the boundary that exists to prevent §7g elsewhere.
    """

    def test_release_sh_is_present_wherever_sync_public_is(self):
        if not os.path.exists(SYNC_SH):
            self.skipTest("public subset — neither internal script ships, as intended")
        self.assertTrue(os.path.exists(RELEASE_SH),
                        "sync-public.sh is here, so this is the PRIVATE tree — but release.sh is "
                        "gone, and the battery below would SKIP rather than fail.")


@unittest.skipUnless(os.path.exists(RELEASE_SH),
                     "release.sh is dev-only, excluded from the public mirror")
class TestTheReleaseScriptSurface(unittest.TestCase):
    def setUp(self):
        self.text = _read(RELEASE_SH)

    def test_it_carries_no_expired_or_undated_waiver(self):
        states = rg.waiver_states(self.text, VERSION_BEING_CUT)
        bad = [(line, state, fields) for line, state, fields in states if state != rg.WAIVER_LIVE]
        self.assertEqual(bad, [], "waiver(s) past their stated scope in release.sh: %s" % (bad,))

    def test_the_remedy_the_refusal_names_is_the_one_the_script_uses(self):
        """§7g's corollary: a refusal pointing at a remedy nobody built is a new defect. The
        remedy `release-check` prints is `PYTHONPATH=<root>/src`, which `release.sh` has run since
        stage 61b — so it existed before it was advertised."""
        self.assertIn('PYTHONPATH="${root}/src"', self.text)

    def test_no_call_is_left_commented_out(self):
        """THE SHAPE THE EXEMPTION HAD. A commented-out call to a function the script defines is
        neither a gate nor a comment; it is a third thing no reader can predict, which is exactly
        why it was read back as an enforced gate seven releases after its scope ended."""
        offenders = rg.disabled_calls(self.text)
        self.assertEqual(
            offenders, (),
            "scripts/release.sh carries commented-out call(s) to its own functions: %s. Either "
            "run it or delete it — a disabled call reads as a resting gate. If one must stay, "
            "attach a WAIVER(id=…, through=<version>, owner=…) so it EXPIRES."
            % ", ".join("%s: %s" % pair for pair in offenders))

    def test_no_ci_wait_names_the_dev_repository(self):
        """THE DIRECTION CHOSEN, PINNED. The waiver was deleted rather than re-armed, so a dev-repo
        CI wait must not exist in any form — a re-armed one behind an env override would be a
        third state again."""
        for line, arguments in rg.live_calls(self.text, "wait_for_ci_green"):
            with self.subTest(line=line):
                self.assertNotEqual(rg.call_arguments(arguments), "$DEV_REPO")

    def test_both_mirror_ci_waits_survive(self):
        """THE NEGATIVE for ②: the ENFORCED gate is the mirror's, and deleting the dead dev call
        must not have touched it. Two call sites — the release PR branch, and the merged main
        commit that is actually tagged."""
        mirror = [line for line, arguments in rg.live_calls(self.text, "wait_for_ci_green")
                  if rg.call_arguments(arguments) == "$PUB_REPO"]
        self.assertEqual(len(mirror), 2,
                         "expected the mirror's two CI waits (PR branch, merged main); found %d "
                         "at lines %s" % (len(mirror), mirror))

    def test_the_script_says_the_dev_repo_is_not_a_release_gate(self):
        """AT THE MOMENT OF USE. The row's second defect was that the truth existed only at the
        console; an operator running the cut must be told, in the run, which CI gates the tag."""
        self.assertIn("not a release gate", self.text)

    def test_every_documented_release_check_carries_the_import_path(self):
        """③ at the surface that hands a human a command. Each `release-check` invocation the
        script RUNS or PRINTS must name PYTHONPATH, so the reader never gets the site-packages
        answer the row was filed for."""
        for number, line in enumerate(self.text.splitlines(), start=1):
            if "release-check" in line and "-m mokata" in line:
                with self.subTest(line=number):
                    self.assertIn("PYTHONPATH", line)


class TestWaiversExpire(unittest.TestCase):
    """⭐ THE MECHANISM THE ROW ASKED FOR: an expiry detectable by something other than a human
    reading a comment. `through=` is compared against the version being cut, so the release that
    goes past a waiver's stated scope is the release whose suite goes red."""

    def test_the_release_workflow_carries_no_expired_or_undated_waiver(self):
        """`release.yml` SHIPS, so this half runs on both sides of the mirror boundary. The
        `release.sh` half is in the guarded battery above — split rather than swept under one
        guard, so nothing the mirror can enforce is silently dropped there."""
        states = rg.waiver_states(_read(RELEASE_YML), VERSION_BEING_CUT)
        bad = [(line, state, fields) for line, state, fields in states
               if state != rg.WAIVER_LIVE]
        self.assertEqual(bad, [], "waiver(s) past their stated scope in release.yml: %s" % (bad,))

    def test_a_waiver_expires_the_release_after_its_scope(self):
        text = "# WAIVER(id=dev-ci-billing, through=0.0.10, owner=Jas, filed=2026-07-06): billing"
        self.assertEqual([s for _l, s, _f in rg.waiver_states(text, "0.0.10")], [rg.WAIVER_LIVE])
        self.assertEqual([s for _l, s, _f in rg.waiver_states(text, "0.0.11")], [rg.WAIVER_EXPIRED])

    def test_the_real_waiver_would_have_reddened_seven_releases_running(self):
        """Transcribed history, run forward. Had the declaration existed, 0.0.11 through 0.0.17
        would each have failed on it — instead the prose sat there and seven cuts went green."""
        declared = ("# WAIVER(id=dev-ci-billing, through=%s, owner=Jas, filed=2026-07-06): the "
                    "private repo's Actions cannot start jobs." % rg.V0017_WAIVER_THROUGH)
        for version in rg.V0017_RELEASES_PAST_SUNSET:
            with self.subTest(version=version):
                self.assertEqual([s for _l, s, _f in rg.waiver_states(declared, version)],
                                 [rg.WAIVER_EXPIRED])

    def test_the_waiver_that_actually_shipped_carried_no_declaration_at_all(self):
        """⭐ THE FINDING, as an assertion. The real block said "KEPT THROUGH 0.0.10" in prose and
        "through 0.0.10" in an echo, and NOTHING could read either. A sunset written only in prose
        is not time-boxed — it is permanent with an apology attached, and this is why section ②
        needs `disabled_calls` as well: the sunset mechanism cannot see the very block that
        motivated it."""
        self.assertEqual(rg.waiver_declarations(rg.V0017_WAIVER_BLOCK), ())
        self.assertEqual(rg.waiver_states(rg.V0017_WAIVER_BLOCK, "0.0.17"), ())
        # What DOES see it: the disabled call. One offender, and it is the wait itself.
        offenders = rg.disabled_calls(rg.V0017_SCRIPT)
        self.assertEqual(len(offenders), 1)
        self.assertIn("wait_for_ci_green", offenders[0][1])
        self.assertIn("$DEV_REPO", offenders[0][1])

    def test_a_version_is_compared_numerically_not_lexically(self):
        """0.0.9 is BEFORE 0.0.10 and every string comparison in the world disagrees. A waiver
        written at 0.0.9 `through=0.0.10` is live; read lexically it would already be expired, and
        the first stage to hit that would learn to distrust the mechanism."""
        text = "# WAIVER(id=x, through=0.0.10, owner=Jas): billing"
        self.assertEqual([s for _l, s, _f in rg.waiver_states(text, "0.0.9")], [rg.WAIVER_LIVE])

    def test_an_undated_waiver_is_not_a_live_one(self):
        text = "# WAIVER(id=x, owner=Jas): until we get round to it"
        self.assertEqual([s for _l, s, _f in rg.waiver_states(text, "0.0.18")],
                         [rg.WAIVER_UNDATED])


# =================================================================================================
# ③ THE DOCUMENTED HUMAN COMMAND
# =================================================================================================


class TestReleaseCheckNamesThePackageThatAnswered(unittest.TestCase):
    """`RELEASE-CHECK-BARE-COMMAND-READS-SITE-PACKAGES`. §7g: an answer that carries its own
    provenance cannot be mistaken for a different answer.

    ⚠ THE TAG-TIME PATH IS NOT TOUCHED AND MUST NOT BE. `release.sh` runs this with
    `PYTHONPATH="${root}/src"`, which resolves INSIDE `--root`, so the first test below is the
    tag-time path and it still passes.
    """

    def test_the_in_root_case_is_the_tag_time_path_and_still_answers(self):
        prov = answering_package(ROOT, os.path.join(ROOT, "src", "mokata", "packaging.py"),
                                 __version__)
        self.assertEqual(prov.state, ANSWERED_IN_ROOT)
        self.assertTrue(prov.trustworthy)

    def test_a_package_outside_the_root_is_a_different_state(self):
        """The offender, synthesised — the real one lives on a machine, not in the tree (§7i)."""
        prov = answering_package(
            ROOT, "/usr/lib/python3/site-packages/mokata/packaging.py", "0.0.9")
        self.assertEqual(prov.state, ANSWERED_OUT_OF_ROOT)
        self.assertFalse(prov.trustworthy)

    def test_a_symlinked_root_is_the_same_tree(self):
        """A path is not a string. On macOS `/tmp` is a symlink to `/private/tmp`, so a caller who
        reaches the checkout through a link would be REFUSED by a textual prefix test — a false
        refusal, which teaches the next reader to work around the guard.

        ⚠ THIS AND ITS SIBLING BELOW ARE A §7f PAIR, and they are separate because a mutation run
        proved they had to be: `--root` and the package path are resolved at two different sites,
        and one test could only ever see one of them."""
        import tempfile
        link_dir = tempfile.mkdtemp(prefix="linked-root-")
        link = os.path.join(link_dir, "checkout")
        os.symlink(ROOT, link)
        prov = answering_package(link, os.path.join(ROOT, "src", "mokata", "packaging.py"),
                                 __version__)
        self.assertEqual(prov.state, ANSWERED_IN_ROOT)

    def test_a_symlinked_package_path_is_still_inside_the_root(self):
        """The other half: the PACKAGE reached through a link, the root given directly. An
        editable install's `.pth` can point at a symlinked `src/`, and refusing that would be a
        refusal nobody could act on."""
        import tempfile
        link_dir = tempfile.mkdtemp(prefix="linked-src-")
        link = os.path.join(link_dir, "src")
        os.symlink(os.path.join(ROOT, "src"), link)
        prov = answering_package(ROOT, os.path.join(link, "mokata", "packaging.py"), __version__)
        self.assertEqual(prov.state, ANSWERED_IN_ROOT)

    def test_a_sibling_sharing_a_path_prefix_is_not_inside(self):
        """`/…/mokata-oss` starts with `/…/mokata`. A bare `startswith` would let the MIRROR
        checkout answer for the DEV one — the two trees this whole stage exists to keep apart."""
        prov = answering_package(ROOT, ROOT + "-oss/src/mokata/packaging.py", __version__)
        self.assertEqual(prov.state, ANSWERED_OUT_OF_ROOT)

    def test_a_package_with_no_file_is_a_third_state(self):
        prov = answering_package(ROOT, None, __version__)
        self.assertEqual(prov.state, ANSWERED_UNRESOLVABLE)
        self.assertFalse(prov.trustworthy)

    def test_the_answer_names_the_package_and_the_field_count(self):
        """The 5-vs-7 tell the row called *"detectable at a glance"*. The stale package guarded
        five fields; this checkout guards seven, and the two `action-pin` entries are the ones a
        0.0.9 PASS silently omitted."""
        prov = answering_package(ROOT, os.path.join(ROOT, "src", "mokata", "packaging.py"),
                                 __version__)
        self.assertEqual(prov.field_count, len(_VERSION_FIELDS))
        self.assertGreater(prov.field_count, rg.V0017_BARE_FIELD_COUNT)
        self.assertIn(str(prov.field_count), prov.render())
        self.assertIn(os.path.join("src", "mokata"), prov.render())

    def test_the_refusal_names_a_remedy(self):
        """§7g's corollary: a refusal pointing at a remedy nobody built is a new defect. That the
        remedy is the SAME one `release.sh` runs is asserted in the guarded battery above; that it
        actually works is `test_the_remedy_runs_and_answers`, which executes it."""
        prov = answering_package(ROOT, "/usr/lib/python3/site-packages/mokata/packaging.py",
                                 "0.0.9")
        self.assertIn("PYTHONPATH", prov.refusal())
        self.assertIn(os.path.join(ROOT, "src"), prov.refusal())


class TestReleaseCheckExitContract(unittest.TestCase):
    """Three outcomes, three codes. Collapsing them would hand `release.sh` and a human the same
    number for "the versions disagree" and "a different package answered"."""

    def _run(self, argv, root_arg=None):
        import io
        from contextlib import redirect_stderr, redirect_stdout

        from mokata.cli import main
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue() + err.getvalue()

    def test_answering_from_inside_the_root_passes(self):
        code, text = self._run(["release-check", __version__, "--root", ROOT])
        self.assertEqual(code, RELEASE_CHECK_OK)
        self.assertIn("answered by mokata", text)

    def test_a_mismatch_is_still_exit_one(self):
        code, _text = self._run(["release-check", "9.9.9", "--root", ROOT])
        self.assertEqual(code, RELEASE_CHECK_MISMATCH)

    def test_answering_about_another_tree_refuses_with_its_own_code(self):
        """The site-packages shape, reproduced without site-packages: this checkout's code asked
        about a directory it does not live in is the same relation, the other way round."""
        import tempfile
        other = tempfile.mkdtemp(prefix="not-this-checkout-")
        code, text = self._run(["release-check", __version__, "--root", other])
        self.assertEqual(code, RELEASE_CHECK_ANSWERED_ELSEWHERE)
        self.assertIn("REFUSED", text)
        self.assertIn("PYTHONPATH", text)

    def test_the_three_codes_are_distinct(self):
        codes = (RELEASE_CHECK_OK, RELEASE_CHECK_MISMATCH, RELEASE_CHECK_ANSWERED_ELSEWHERE,
                 RELEASE_CHECK_UNRESOLVABLE_PACKAGE)
        self.assertEqual(len(set(codes)), len(codes))

    def test_the_remedy_runs_and_answers(self):
        """The remedy is EXECUTED, in a subprocess, as a human would type it — the one form of
        proof that a printed instruction is not fiction."""
        env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "src"),
                   PYTHONDONTWRITEBYTECODE="1")
        proc = subprocess.run(
            [sys.executable, "-m", "mokata", "release-check", __version__, "--root", ROOT],
            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True)
        self.assertEqual(proc.returncode, RELEASE_CHECK_OK, proc.stdout)
        self.assertIn("answered by mokata", proc.stdout)
        for name in _VERSION_FIELDS:
            self.assertIn(name, proc.stdout)


# =================================================================================================
# §7i — ONE SYNTHETIC OFFENDER PER FUNCTION, so each is separately gradable
# =================================================================================================


class TestSyntheticOffenders(unittest.TestCase):
    """Once the tree is fixed there is nothing left to catch. These are the offenders."""

    def test_a_sixth_unguarded_job_is_caught(self):
        doc = {"jobs": {
            "test": {"if": "github.repository == '%s'" % rg.PUBLISHING_REPOSITORY},
            "notify": {"runs-on": "ubuntu-latest"},
        }}
        self.assertEqual(rg.jobs_in_class(doc, rg.GUARD_UNGUARDED), ("notify",))

    def test_a_job_guarded_to_nobody_is_its_own_class(self):
        """`if: github.repository == 'JasGujral/typo'` runs on neither repository. It READS as
        gated and it is dead — a fourth state that must not be filed under "guarded"."""
        doc = {"jobs": {"pypi": {"if": "github.repository == 'JasGujral/typo'"}}}
        self.assertEqual(rg.jobs_in_class(doc, rg.GUARD_NEVER_RUNS), ("pypi",))
        self.assertEqual(rg.jobs_in_class(doc, rg.GUARD_PUBLISH), ())

    def test_an_unreadable_condition_is_its_own_class(self):
        doc = {"jobs": {"build": {"if": "success() && github.repository == 'x/y'"}}}
        self.assertEqual(rg.jobs_in_class(doc, rg.GUARD_UNREADABLE), ("build",))

    def test_a_refusal_that_depends_on_a_skipped_job_is_visible(self):
        doc = {"jobs": {
            "refuse": {"if": "github.repository != '%s'" % rg.PUBLISHING_REPOSITORY,
                       "needs": ["test"]},
            "test": {"if": "github.repository == '%s'" % rg.PUBLISHING_REPOSITORY},
        }}
        self.assertEqual(rg.job_needs(doc, "refuse"), ("test",))
        self.assertEqual(rg.job_needs(doc, "test"), ())

    def test_a_single_string_needs_is_read_as_a_dependency(self):
        doc = {"jobs": {"refuse": {"if": "github.repository != 'a/b'", "needs": "test"}}}
        self.assertEqual(rg.job_needs(doc, "refuse"), ("test",))

    def test_a_disabled_call_is_caught_and_prose_about_one_is_not(self):
        """The two false positives that would have switched this sweep off, kept apart. Both come
        from `release.sh` verbatim."""
        text = ("wait_for_ci_green() {\n  :\n}\n"
                "# under $PYTHON (default python3). Point it at a 3.10+ interpreter\n"
                "# the live-db matrix (only CI can — see wait_for_ci_green below), but it CAN\n"
                "# wait_for_ci_green \"$DEV_REPO\" \"$(git rev-parse HEAD)\" \"the dev repo\"\n")
        offenders = rg.disabled_calls(text)
        self.assertEqual(len(offenders), 1)
        self.assertEqual(offenders[0][0], 6)

    def test_a_function_that_does_not_exist_is_not_a_disabled_call(self):
        """DERIVED, not typed: the names come from the same text. A comment beginning with a word
        that is not a function of this script is prose."""
        self.assertEqual(rg.disabled_calls("# deploy \"$THING\"\n"), ())

    def test_a_live_call_is_read_with_its_first_argument(self):
        text = ('wait_for_ci_green "$PUB_REPO" "$(git rev-parse HEAD)" "the mirror"\n'
                '# wait_for_ci_green "$DEV_REPO" "x" "the dev repo"\n')
        live = rg.live_calls(text, "wait_for_ci_green")
        self.assertEqual(len(live), 1, "a commented-out call is not a live one")
        self.assertEqual(rg.call_arguments(live[0][1]), "$PUB_REPO")

    def test_a_re_armed_dev_wait_would_be_caught(self):
        """If a later stage reverses this stage's direction, it must do so deliberately: a live
        dev-repo wait reddens the pin that recorded the choice."""
        text = 'wait_for_ci_green "$DEV_REPO" "$(git rev-parse HEAD)" "the dev repo"\n'
        live = rg.live_calls(text, "wait_for_ci_green")
        self.assertEqual([rg.call_arguments(a) for _l, a in live], ["$DEV_REPO"])

    def test_a_function_name_appearing_mid_line_is_not_a_call(self):
        self.assertEqual(rg.live_calls('echo "see wait_for_ci_green for details"\n',
                                       "wait_for_ci_green"), ())

    def test_the_definition_is_not_a_call_site(self):
        """⭐ THE DISTINCTION THE WHOLE ② ROW TURNS ON. `RELEASE-SH-DEV-CI-WAIVER-OUTLIVED-ITS-SCOPE`
        exists because a claim about the gate was *"read from the function DEFINITION and never
        from the call site"*. A call-site finder that counted the definition would make the same
        mistake in code."""
        text = 'wait_for_ci_green() {\n  :\n}\nwait_for_ci_green "$PUB_REPO" "abc" "the mirror"\n'
        live = rg.live_calls(text, "wait_for_ci_green")
        self.assertEqual([line for line, _a in live], [4])

    def test_a_call_with_no_arguments_is_still_a_call(self):
        """`run_test_preflight` takes none. A finder that required an argument would report the
        fail-closed local preflight as never invoked — and it did, until this stage."""
        text = "run_test_preflight() {\n  :\n}\nrun_test_preflight\n"
        self.assertEqual([line for line, _a in rg.live_calls(text, "run_test_preflight")], [4])


if __name__ == "__main__":
    unittest.main()
