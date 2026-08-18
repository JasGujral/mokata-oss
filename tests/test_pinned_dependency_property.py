"""Stage 8 — PINNED-DEPS-PIP-EDITABLE: the PROPERTY is asserted, and the score is not chased.

doc 84 §1. The row's own instruction is the whole risk of this stage: **`Do NOT chase the 6 as a
score.`** So this file grades a property of the workflows and never a number, and the property was
chosen after measuring why the number cannot be the target.

THE DECISION, IN ONE PARAGRAPH
-------------------------------
Every `pip install` in `.github/workflows/` must fall into one of three classes, and the third must
be EMPTY:

    HASH_PINNED     `--require-hashes -r requirements/*.txt`      7 sites
    LOCAL_SOURCE    this package from the checkout or from dist/  9 sites
    UNPINNED_FETCH  a NAME resolved against an index, no hash     0 sites  <- the property

There was exactly one `UNPINNED_FETCH` — the SBOM venv's `pip install --upgrade pip` — and it is
GONE rather than hash-pinned. Deleting a fetch is strictly stronger than pinning one: there is no
longer anything to pin, the venv's pip is whatever `ensurepip` bundles with the setup-python
interpreter, and the SBOM's own `pip` entry stops varying with whatever PyPI served that morning.

⚠ **THE BRIEF OFFERED TWO OPTIONS AND THIS IS A THIRD.** It said *"hash-pin the SBOM venv's `pip`
… or accept the whole thing"*. Removal was not on the list and is better than both. It does move
the Scorecard number as a side effect; that is a consequence, not the aim, and the aim is what this
file grades.

⚠ **THE HONEST RESIDUAL, AND IT IS NOT SMALL.** The row calls the unpinned transitive closure of
`-e .` *"the real (small) exposure"*. Measured 2026-08-13 against an installed 3.12 venv, the
closure of the ONE required dependency `mcp>=1.2,<2` is **thirty packages** — before the
`setuptools>=61.0` build backend pip fetches under build isolation, and before the `[postgres]`,
`[embeddings]` extras at several of the sites. `LOCAL_SOURCE` is deliberately not
spelled "safe": it means the PACKAGE is not fetched, and nothing more. Closing it means a
constraint file at all eight editable sites, which is the seven the row forbids touching, so it is
NOT fixed here and is proposed as its own row.

WHY THE SCORE CANNOT BE THE TARGET — MEASURED, NOT ARGUED
----------------------------------------------------------
There are EIGHT `pip install -e` sites and SEVEN of them alert.

The open alerts were read live off the public mirror on 2026-08-13 —
`gh api "/repos/JasGujral/mokata-oss/code-scanning/alerts?state=open" --paginate` — and there are
exactly EIGHT open `PinnedDependenciesID` alerts: seven editable installs plus the SBOM venv's
`--upgrade pip`. `ci.yml` carries THREE editable sites and produces TWO alerts.

The un-alerted site is in `ci.yml`'s `hooks-execute` job, and the ONLY structural difference
between that job and the seven — with the control in the same file — is its shell:

    ci.yml `test`          runs-on: ${{ matrix.os }}   shell: bash                        ALERTS
    ci.yml `hooks-execute` runs-on: ${{ matrix.os }}   shell: ${{ matrix.runner_shell }}  SILENT

A templated shell is one the instrument cannot resolve, and a job whose shell it cannot resolve is
a job whose `run:` steps it never reads. **The score under-counts and nothing in the score says
so.** Driving eight alerts to zero would leave behind a site the instrument never counted — which
is the argument for grading a property, made by the instrument itself.

⚠ **THIS DOES NOT RE-OPEN 0.0.17 STAGE 10, AND NOTHING HERE TOUCHES IT.** Every open alert is
`pipCommand`; ZERO are `GitHubAction`. Stage 10's SHA-pinning of every `uses:` holds completely and
is pinned by `tests/test_s10_workflow_pins.py`. Different property, different check.

⚠ **WHAT IS ONLY VERIFIABLE AT A LIVE SCORECARD READ**, and it is named rather than claimed: that
the `--upgrade pip` alert closes, that the seven remain, and that the eighth site stays invisible.
Scorecard cannot be re-run from here. See the stage-8 report's standing obligations.

SHAPE (doc 85): pure functions over a supplied corpus in `tests/_supply_chain_sweep.py` (§7i), with
synthetic offenders here; every axis derived from the workflows themselves except the alert
counts, which are an EXTERNAL reading and are therefore DECLARED, DATED and graded for exactness
(§7j). No line numbers anywhere — three files in this area moved under three consecutive stages.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import re
import unittest

from _support import sample_manifest_data  # noqa: F401  (path-fix side-effect)

import _release_assets as ra
import _supply_chain_sweep as sc
import _workflow_pins as wp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOWS = os.path.join(ROOT, ".github", "workflows")
RELEASE_YML = os.path.join(WORKFLOWS, "release.yml")
PYPROJECT = os.path.join(ROOT, "pyproject.toml")

# ---- THE EXTERNAL READING — declared, dated, and carrying the command that produced it ----------
#
# History and third-party behaviour cannot be derived from this tree (§7j), so this is a literal.
# It is graded for EXACTNESS against the derived site set below, so a stale entry reds as loudly as
# a missing one.
#
# $ gh api "/repos/JasGujral/mokata-oss/code-scanning/alerts?state=open&per_page=100" --paginate \
#       --jq '.[] | select(.rule.id=="PinnedDependenciesID")
#                 | .most_recent_instance.location.path'          (read 2026-08-13)
ALERTS_READ_ON = "2026-08-13"

#: How many open `PinnedDependenciesID` alerts each workflow carries, by FILE — never by line.
#: Eight in total: seven editable self-installs and, in `release.yml`, one more for the SBOM venv's
#: `pip install --upgrade pip`, which is the one this stage removed.
DECLARED_ALERTS_BY_FILE = {
    "ci.yml": 2,
    "embeddings-leg.yml": 1,
    "live-db-legs.yml": 1,
    "quality-at-scale.yml": 1,
    "real-crg.yml": 1,
    "release.yml": 2,           # one editable install + the `--upgrade pip` this stage deleted
}

#: The one alert in the set that is NOT an editable self-install. Named, because the whole row turns
#: on it being different in kind from the other seven.
DECLARED_UNPINNED_FETCH_ALERTS = 1

#: The job the instrument cannot see into, and WHY. Derived checks below hold this to the tree.
BLIND_SPOT_JOB = "hooks-execute"
BLIND_SPOT_FILE = "ci.yml"

_TEMPLATED = re.compile(r"\$\{\{.*\}\}")


def _read(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _docs():
    """`{filename: parsed}` for every workflow. The corpus is the directory, derived by listing."""
    return {name: ra.safe_load(_read(os.path.join(WORKFLOWS, name)),
                               "grade the pinned-dependency property")
            for name in wp.workflow_files(WORKFLOWS)}


def _installs():
    """`[(filename, where, arguments, classification)]` for every `pip install` in the corpus."""
    found = []
    for name, doc in sorted(_docs().items()):
        for where, text in ra.run_blocks(doc):
            for _line, arguments in sc.pip_installs(text):
                found.append((name, where, arguments, sc.classify_pip_install(arguments)))
    return tuple(found)


def _editable_sites():
    """Every `pip install -e <local>` site — the seven the row protects, plus the eighth."""
    return tuple((name, where, args) for name, where, args, kind in _installs()
                 if kind == sc.LOCAL_SOURCE and "-e" in args)


def _job_of(where):
    """`jobs.<id>.steps[<i>]` -> `<id>`."""
    return where.split(".")[1]


# =================================================================================================
# THE PROPERTY — the class that must be empty
# =================================================================================================

class TestTheProperty(unittest.TestCase):

    def test_the_corpus_is_not_empty(self):
        """The anti-vacuity floor: every assertion below is true of zero installs."""
        installs = _installs()
        self.assertGreaterEqual(
            len(installs), 15,
            "only %d pip installs were read out of %d workflows — the sweep has stopped seeing "
            "the corpus, and 'no unpinned fetch' would be vacuously true"
            % (len(installs), len(wp.workflow_files(WORKFLOWS))))

    def test_no_workflow_fetches_a_package_by_name_without_a_hash(self):
        """★ THE DECISION, ASSERTED. Reversing it means editing this test."""
        offenders = [(name, where, " ".join(args))
                     for name, where, args, kind in _installs() if kind == sc.UNPINNED_FETCH]
        self.assertEqual(
            offenders, [],
            "a pip install resolves a NAME against an index with no hash:\n%s"
            % "\n".join("  %s %s: pip install %s" % row for row in offenders))

    def test_every_install_is_classified_into_exactly_one_declared_class(self):
        """A fourth, silent class would let an install escape the property without reading as one."""
        for name, where, args, kind in _installs():
            self.assertIn(kind, sc.CLASSES, "%s %s: pip install %s" % (name, where, " ".join(args)))

    def test_the_sbom_venv_no_longer_upgrades_pip_from_the_index(self):
        """The one genuine site, named directly so its removal cannot be undone by accident in a
        step that happens to keep the property green some other way."""
        doc = ra.safe_load(_read(RELEASE_YML), "grade the SBOM venv")
        sbom = [text for where, text in ra.run_blocks(doc) if "sbomenv" in text]
        self.assertEqual(len(sbom), 1, "the SBOM venv step was not found exactly once: %r" % (sbom,))
        for _line, args in sc.pip_installs(sbom[0]):
            self.assertEqual(
                sc.classify_pip_install(args), sc.LOCAL_SOURCE,
                "the SBOM venv installs `%s`, which is not a local artifact" % " ".join(args))

    def test_the_hash_pinned_class_is_populated(self):
        """The NEGATIVE of the property. If nothing were hash-pinned the class split would be
        decorative, and `LOCAL_SOURCE` alone would satisfy 'no unpinned fetch' trivially."""
        pinned = [row for row in _installs() if row[3] == sc.HASH_PINNED]
        self.assertGreaterEqual(len(pinned), 5, "the hash-pinned class has all but vanished")


# =================================================================================================
# THE RESIDUAL — declared, not waved through
# =================================================================================================

class TestTheResidualIsStated(unittest.TestCase):

    def test_local_source_does_NOT_mean_dependency_free(self):
        """★ `release.yml` claimed 'mokata has no required runtime deps' and it had been FALSE
        since the MCP SDK became unconditional (§7h — a comment asserting a fact nothing checked).
        The claim is corrected; this is what keeps it corrected."""
        text = _read(PYPROJECT)
        declared = re.search(r"^dependencies\s*=\s*\[([^\]]*)\]", text, re.M)
        self.assertIsNotNone(declared, "pyproject.toml declares no `dependencies` key at all")
        self.assertTrue(
            declared.group(1).strip(),
            "`dependencies` is empty, so the residual named in this module's docstring no longer "
            "exists — say so there rather than leaving a stale warning")

    def test_the_workflow_no_longer_claims_mokata_has_no_required_deps(self):
        """⚠ The assertion is on the FALSE CLAIM's own words, and the corrected comment is written
        so as not to reproduce them. That is not fussiness: an earlier draft of this test convicted
        the correction for quoting what it was correcting — `PIN-SUBSTRING-COMMENT-HOLE` with the
        sign reversed, which is exactly what defeated a second pin at stage 7."""
        self.assertFalse(
            "no required runtime deps" in _read(RELEASE_YML),
            "release.yml still carries the claim that mokata has no required runtime dependencies; "
            "pyproject.toml requires the MCP SDK, whose closure is thirty packages")


# =================================================================================================
# THE INSTRUMENT'S BLIND SPOT — eight sites, seven alerts, and the difference is derivable
# =================================================================================================

class TestTheInstrumentUnderCounts(unittest.TestCase):
    """Every number here is DERIVED from the workflows except `DECLARED_ALERTS_BY_FILE`, which is
    an external reading and is held to the derivation for exactness."""

    def test_there_are_eight_editable_self_install_sites(self):
        sites = _editable_sites()
        self.assertEqual(
            len(sites), 8,
            "the editable-install population moved; re-derive it before reconciling it against "
            "the alerts:\n%s" % "\n".join("  %s %s" % (n, w) for n, w, _a in sites))

    def test_the_alert_declaration_names_only_files_that_carry_installs(self):
        """Exactness in the other direction: a file that stopped carrying installs but kept its
        alert row would leave a stale claim reading as a measurement."""
        carrying = {name for name, _w, _a, _k in _installs()}
        self.assertLessEqual(
            set(DECLARED_ALERTS_BY_FILE), carrying,
            "the alert declaration names a workflow with no pip install in it at all")

    def test_seven_of_the_eight_editable_sites_alert_and_the_gap_is_in_ONE_file(self):
        """★ THE FINDING. Not 'the row's 7 became 8' — the row's enumeration of ALERTS was
        complete and the population never grew. One site does not alert."""
        editable_alerts = sum(DECLARED_ALERTS_BY_FILE.values()) - DECLARED_UNPINNED_FETCH_ALERTS
        self.assertEqual(editable_alerts, 7)
        by_file = {}
        for name, _where, _args in _editable_sites():
            by_file[name] = by_file.get(name, 0) + 1
        gaps = {name: count - (DECLARED_ALERTS_BY_FILE.get(name, 0)
                               - (DECLARED_UNPINNED_FETCH_ALERTS if name == "release.yml" else 0))
                for name, count in by_file.items()}
        unseen = {name: gap for name, gap in gaps.items() if gap}
        self.assertEqual(
            unseen, {BLIND_SPOT_FILE: 1},
            "the editable sites the instrument does not see are no longer exactly one, in %s: %r. "
            "Re-derive before adjusting either number." % (BLIND_SPOT_FILE, unseen))

    def test_exactly_one_job_in_the_corpus_resolves_its_shell_from_an_expression(self):
        """The structural difference, derived. If a second job gains a templated shell, the
        single-variable explanation above stops holding and this reds instead of going stale."""
        templated = []
        for name, doc in sorted(_docs().items()):
            for job_id, job in (doc.get("jobs") or {}).items():
                shell = ((job.get("defaults") or {}).get("run") or {}).get("shell")
                if isinstance(shell, str) and _TEMPLATED.search(shell):
                    templated.append((name, job_id))
        self.assertEqual(
            templated, [(BLIND_SPOT_FILE, BLIND_SPOT_JOB)],
            "the set of jobs whose shell is an expression has changed: %r" % (templated,))

    def test_the_unseen_site_lives_in_that_job(self):
        """The two derivations are joined here rather than left adjacent. Adjacent facts are how a
        correlation gets written down as a cause and then never re-checked."""
        jobs = {_job_of(where) for name, where, _a in _editable_sites() if name == BLIND_SPOT_FILE}
        self.assertIn(
            BLIND_SPOT_JOB, jobs,
            "%s no longer carries an editable install, so the blind-spot explanation is stale"
            % BLIND_SPOT_JOB)

    def test_the_seven_alerting_sites_all_resolve_their_shell_to_a_literal(self):
        """The control. Without it, 'the silent one has a templated shell' is one observation, not
        a discrimination — the seven could have templated shells too."""
        docs = _docs()
        for name, where, _args in _editable_sites():
            job = docs[name]["jobs"][_job_of(where)]
            shell = ((job.get("defaults") or {}).get("run") or {}).get("shell")
            if _job_of(where) == BLIND_SPOT_JOB and name == BLIND_SPOT_FILE:
                continue
            self.assertFalse(
                isinstance(shell, str) and _TEMPLATED.search(shell),
                "%s %s alerts and yet resolves its shell from an expression — the discrimination "
                "in this module's docstring is wrong" % (name, where))

    def test_none_of_the_open_alerts_is_about_an_action_pin(self):
        """0.0.17 stage 10 is NOT re-opened. Its property is `uses:` SHA-pinning, which is a
        different check with its own pin; this one is `pipCommand` only."""
        self.assertEqual(
            len(wp.unpinned(WORKFLOWS)), 0,
            "an action `uses:` is no longer SHA-pinned — that is stage 10's property and it is "
            "not what this file is about; fix it there")


# =================================================================================================
# SYNTHETIC OFFENDERS — the sweep is graded on defects, not on a healthy tree (§7i)
# =================================================================================================

class TestSyntheticOffenders(unittest.TestCase):

    def classify(self, command):
        installs = sc.pip_installs(command)
        self.assertEqual(len(installs), 1, "%r did not read as one pip install: %r"
                         % (command, installs))
        return sc.classify_pip_install(installs[0][1])

    def test_a_bare_name_is_an_unpinned_fetch(self):
        self.assertEqual(self.classify("pip install requests"), sc.UNPINNED_FETCH)

    def test_the_deleted_line_is_an_unpinned_fetch(self):
        """The exact command this stage removed, so the property's one real offender is graded
        after it is gone from the tree."""
        self.assertEqual(
            self.classify("/tmp/sbomenv/bin/python -m pip install --upgrade pip >/dev/null"),
            sc.UNPINNED_FETCH)

    def test_a_requirements_file_WITHOUT_require_hashes_is_an_unpinned_fetch(self):
        """★ THE TRAP IN THE CLASSIFIER. A requirements file is a LOCAL PATH holding REMOTE NAMES,
        so a path test alone blesses an un-hashed fetch of everything in it."""
        self.assertEqual(self.classify("pip install -r requirements/ci.txt"), sc.UNPINNED_FETCH)

    def test_the_same_command_WITH_require_hashes_is_pinned(self):
        self.assertEqual(
            self.classify("pip install --require-hashes -r requirements/ci.txt"), sc.HASH_PINNED)

    def test_an_editable_self_install_is_local_source(self):
        self.assertEqual(self.classify("pip install -e ."), sc.LOCAL_SOURCE)

    def test_an_editable_install_with_extras_is_local_source(self):
        self.assertEqual(self.classify('pip install -e ".[postgres]"'), sc.LOCAL_SOURCE)

    def test_a_locally_built_wheel_is_local_source(self):
        self.assertEqual(self.classify("python -m pip install dist/*.whl >/dev/null"),
                         sc.LOCAL_SOURCE)

    def test_a_GLUED_redirection_is_not_a_requirement_specifier(self):
        """★ THE DISCRIMINATING CASE, and it took a second try to find one.

        `pip install requests >/dev/null` comes out UNPINNED_FETCH whether or not the redirection
        is stripped, because `>/dev/null` is not local either — so it grades NOTHING. The case
        that separates the two worlds is a redirection whose TARGET IS NOT PATH-SHAPED beside an
        install that IS local: strip it and the verdict is LOCAL_SOURCE, keep it and the same
        command reads as an unpinned fetch. A pin that cannot tell the two apart is decorative."""
        self.assertEqual(self.classify("pip install -e . >build.log"), sc.LOCAL_SOURCE)

    def test_a_DETACHED_redirection_consumes_its_target_too(self):
        """The other spelling. `>` and `build.log` are two words; leaving either behind makes the
        argument list non-local and inverts the verdict."""
        self.assertEqual(self.classify("pip install -e . > build.log"), sc.LOCAL_SOURCE)

    def test_a_redirected_unpinned_fetch_is_still_an_unpinned_fetch(self):
        """The negative: stripping redirections must not launder a real fetch."""
        self.assertEqual(self.classify("pip install requests >/dev/null"), sc.UNPINNED_FETCH)
        self.assertEqual(self.classify("pip install requests > /dev/null 2>&1"), sc.UNPINNED_FETCH)

    def test_pip_install_inside_an_echo_is_NOT_a_pip_install(self):
        """★ THE FALSE POSITIVE THAT IS LIVE IN THE TREE. `release.yml`'s wheel-content check
        ECHOES the words `clean pip install.` A grep convicts it; a command-position read does not.
        This is `PIN-SUBSTRING-COMMENT-HOLE` with the prose on a LIVE line, where comment-stripping
        does not save you."""
        self.assertEqual(
            sc.pip_installs('echo "ABORT: mokata setup would fail on a clean pip install."'), ())

    def test_a_commented_out_install_is_NOT_a_pip_install(self):
        """A whole-line comment is caught by the command-position test on its own — `#` is not an
        interpreter — so this documents the behaviour and grades nothing by itself. The comment
        STRIPPER's own offender is the test below."""
        self.assertEqual(sc.pip_installs("# pip install requests"), ())

    def test_a_TRAILING_comment_is_not_part_of_the_argument_list(self):
        """★ THE OFFENDER ONLY `_strip_comment` CAN SEE (§7f). `release.yml` writes a trailing
        `# PyYAML for the workflow-lint tests` after a real install. Left in, the comment's words
        become requirement specifiers and a local install reads as an unpinned fetch — a FALSE
        RED, which is how a sweep gets switched off."""
        self.assertEqual(
            self.classify("pip install -e .   # installs mokata itself"), sc.LOCAL_SOURCE)

    def test_an_install_after_a_then_is_found(self):
        """Splitting on `;` leaves the keyword attached, and `release.yml` really does write
        `if [ … ]; then python -m pip install …; fi` on one line."""
        found = sc.pip_installs(
            'if [ "$x" = "y" ]; then python -m pip install --require-hashes -r r.txt; fi')
        self.assertEqual(len(found), 1, "%r" % (found,))

    def test_an_install_behind_a_leading_assignment_is_found(self):
        found = sc.pip_installs("PIP_NO_INPUT=1 pip install requests")
        self.assertEqual(len(found), 1, "%r" % (found,))
        self.assertEqual(sc.classify_pip_install(found[0][1]), sc.UNPINNED_FETCH)

    def test_an_install_with_no_specifier_at_all_is_not_local(self):
        """`pip install` alone installs nothing; classifying it LOCAL_SOURCE would let an empty
        argument list satisfy the property the way an empty glob satisfied the old `ls`."""
        self.assertEqual(sc.classify_pip_install(()), sc.UNPINNED_FETCH)


if __name__ == "__main__":
    unittest.main()
