"""Derive `release.sh`'s preflight install set FROM `ci.yml`, instead of maintaining it beside it.

Stage 27 (RELEASE-PREFLIGHT-NOT-CI-EQUIVALENT). The first real 0.0.17 cut aborted in
`run_test_preflight` with 17 errors + 2 failures, every one of them `ModuleNotFoundError: No
module named 'yaml'`. Nothing was broken: the preflight built a throwaway venv, installed
`-e .` and — on the `present` leg only — `requirements/jsonschema.txt`, and never installed
`requirements/ci.txt`, where PyYAML lives. So it reproduced CI's **jsonschema** dimension and not
CI's **test-tooling** dimension. It had never been CI-equivalent; nothing could say so, because
before stage 10 the tests that need PyYAML SKIPPED. Stage 10 made them hard failures and the next
cut surfaced it.

★ THE SAME DEFECT AS `embeddings-leg.yml`, ONE FILE OVER. Stage 10 found that job was the only
full-suite runner without PyYAML — 17 tests skipping silently while the job reported OK — and
fixed it with exactly the install added here. The release script had the identical hole and stage
10 did not look there: a fix applied to the INSTANCE rather than to the CLASS. Adding the missing
line would repeat that mistake a third time, so this module derives the set instead:

    every `requirements/*.txt` that ci.yml installs for the unit suite is also installed by
    release.sh's `run_test_preflight`.

BOTH SIDES ARE PARSED, NEITHER IS TYPED. ci.yml goes through PyYAML (`_workflow_pins.safe_load`,
whose MissingParser is deliberately re-exported here rather than re-invented) — a hardcoded list
of job ids would drift the same way the hardcoded install list did. release.sh is comment-stripped
and cut down to the `run_test_preflight` BODY before any read, because the function's own
commentary names requirements files in prose and a substring reader would be satisfied by the
sentence explaining the install rather than by the install (PIN-SUBSTRING-COMMENT-HOLE).

THREE OUTCOMES, THREE REPRESENTATIONS (§7g). The prohibited collapse here is specific: "ci.yml
installs no requirements files for the unit suite" makes the subset test VACUOUSLY true, and a
vacuous true is indistinguishable from "release.sh covers them all". So an empty derived set is
`BASIS_NO_CI_REQUIREMENTS` — UNDECIDABLE, with a reason — never GREEN. Likewise a release.sh with
no locatable preflight function, and an unreadable side of either corpus. And a corpus PyYAML
cannot read raises `MissingParser` rather than returning anything at all.
"""

import os
import re

from _workflow_pins import MissingParser, safe_load  # noqa: F401  (MissingParser re-exported)

# ---- verdicts --------------------------------------------------------------------------------
GREEN = "green"
RED = "red"
UNDECIDABLE = "undecidable"

# ---- bases: WHICH rung produced the verdict (the one stored signal) --------------------------
BASIS_ALL_INSTALLED = "all_installed"              # every derived file is installed by the preflight
BASIS_MISSING_INSTALLS = "missing_installs"        # at least one is not
BASIS_CI_UNREADABLE = "ci_unreadable"
BASIS_RELEASE_UNREADABLE = "release_unreadable"
BASIS_NO_UNIT_SUITE_JOBS = "no_unit_suite_jobs"    # ci.yml runs the unit suite nowhere
BASIS_NO_CI_REQUIREMENTS = "no_ci_requirements"    # ...but installs no requirements/*.txt for it
BASIS_NO_PREFLIGHT_FUNCTION = "no_preflight_function"
BASIS_NO_PREFLIGHT_FUNCTIONS = "no_preflight_functions"  # release.sh runs the suite nowhere

#: basis -> the ONE verdict it produces. `verdict` is never stored beside `basis`; two fields that
#: can disagree eventually do, which is the whole reason this stage exists.
_VERDICT_OF = {
    BASIS_ALL_INSTALLED: GREEN,
    BASIS_MISSING_INSTALLS: RED,
    BASIS_CI_UNREADABLE: UNDECIDABLE,
    BASIS_RELEASE_UNREADABLE: UNDECIDABLE,
    BASIS_NO_UNIT_SUITE_JOBS: UNDECIDABLE,
    BASIS_NO_CI_REQUIREMENTS: UNDECIDABLE,
    BASIS_NO_PREFLIGHT_FUNCTION: UNDECIDABLE,
    BASIS_NO_PREFLIGHT_FUNCTIONS: UNDECIDABLE,
}

UNDECIDABLE_BASES = frozenset(b for b, v in _VERDICT_OF.items() if v == UNDECIDABLE)

#: The DEFAULT preflight function, kept only as `resolve`'s default argument. It is no longer the
#: scope: stage 28 found `run_public_subset_preflight` failing the identical way, one FUNCTION
#: over, because this module named a function instead of deriving the set — the same
#: instance-not-class mistake it was written to fix. `preflight_functions()` is the scope now.
PREFLIGHT_FUNCTION = "run_test_preflight"

#: A shell function definition at column 0 in release.sh: `name() {`.
_SHELL_FUNCTION = re.compile(r"^([a-z_][a-z0-9_]*)\(\)\s*\{", re.M)

#: How a preflight runs the suite. A function containing this RUNS TESTS, and every function that
#: runs tests owes CI the same dependency set — that is the property, stated structurally instead
#: of as a list of names somebody remembers to extend.
_RUNS_SUITE = "unittest discover"

#: A `requirements/<name>.txt` path, wherever it sits under a prefix (`"$sub/requirements/..."`).
_REQ_FILE = re.compile(r"(?:^|[/\"'])(requirements/[A-Za-z0-9_.-]+\.txt)")

#: `-r FILE` / `--requirement FILE` / `--requirement=FILE`, the only way pip is told to read one.
_DASH_R = re.compile(r"(?:^|\s)(?:-r|--requirement)(?:=|\s+)(\S+)")

#: How the suites the preflight runs are invoked. `unittest discover -s <dir>` where <dir> is the
#: repo's own test root — the same two roots `run_test_preflight` walks.
_DISCOVER = re.compile(r"unittest\s+discover\b[^\n]*?-s\s+(tests(?:/[A-Za-z0-9_./-]+)?)")


class ParityResolution(object):
    """The parity property, derived. Frozen by convention (no setters are offered)."""

    __slots__ = ("basis", "required", "installed", "detail", "function")

    def __init__(self, basis, required=(), installed=(), detail="", function=None):
        object.__setattr__(self, "function", function or PREFLIGHT_FUNCTION)
        if basis not in _VERDICT_OF:
            raise ValueError("unknown basis %r" % (basis,))
        if basis in UNDECIDABLE_BASES and not detail:
            raise ValueError(
                "basis=%r is UNDECIDABLE and carries no reason. An undecidable that cannot say "
                "why it is undecidable is a shrug, and a reader rounds a shrug to green — which "
                "is how a preflight went four releases without reproducing CI." % (basis,))
        object.__setattr__(self, "basis", basis)
        object.__setattr__(self, "required", tuple(sorted(required)))
        object.__setattr__(self, "installed", tuple(sorted(installed)))
        object.__setattr__(self, "detail", detail)

    def __setattr__(self, *_a):                       # pragma: no cover - immutability guard
        raise AttributeError("ParityResolution is immutable")

    @property
    def verdict(self):
        return _VERDICT_OF[self.basis]

    @property
    def decided(self):
        return self.verdict != UNDECIDABLE

    @property
    def missing(self):
        """What CI installs for the unit suite and the preflight does not."""
        return tuple(r for r in self.required if r not in self.installed)

    def render(self):
        if self.verdict == GREEN:
            return ("GREEN — %s installs every requirements file ci.yml installs for the unit "
                    "suite (%s)" % (self.function, ", ".join(self.required)))
        if self.verdict == RED:
            return ("RED — ci.yml installs %s for the unit suite; %s's venv NEVER installs %s, so "
                    "the preflight runs the suite against a different dependency set than CI does"
                    % (", ".join(self.required), self.function, ", ".join(self.missing)))
        return "UNKNOWN — %s" % self.detail

    def __repr__(self):
        return "<preflight-parity %s %s basis=%s missing=%r>" % (
            self.function, self.verdict.upper(), self.basis, self.missing)


# ---- the CI side: parsed ---------------------------------------------------------------------

def _step_runs(job):
    """Every `run:` script in a job, as text. Steps without one contribute nothing."""
    steps = job.get("steps")
    if not isinstance(steps, list):
        return ()
    return tuple(s["run"] for s in steps
                 if isinstance(s, dict) and isinstance(s.get("run"), str))


def _needs_live_services(job):
    """Whether a job depends on service containers a LOCAL preflight cannot stand up.

    Read off `services:` — the declaration itself, not a name we recognise. `live-db` boots
    postgres+pgvector and neo4j as containers and runs one live-only integration pattern against
    them; demanding release.sh reproduce that would be demanding the preflight be CI, which it
    explicitly is not (`run_test_preflight`'s own comment: "a local run can't cover ... the
    live-db matrix (only CI can)").
    """
    return isinstance(job.get("services"), dict) and bool(job["services"])


def unit_suite_jobs(ci_text):
    """(included, excluded) — the ci.yml jobs whose tests `run_test_preflight` also runs.

    INCLUDED: any job with a step that invokes `unittest discover -s tests[...]`. That is the
    preflight's own definition of its scope — it runs `discover -s tests` and
    `discover -s tests/integration` — so the set is read from what a job RUNS, never from a list
    of job ids somebody keeps up to date.

    EXCLUDED, each with the reason, so a narrowing is visible rather than silent:
      * a job that declares service containers (see `_needs_live_services`);
      * a job that never runs the suite at all (build/publish/scan jobs).

    Returns two tuples of (job_id, reason).
    """
    doc = safe_load(ci_text)
    if not isinstance(doc, dict) or not isinstance(doc.get("jobs"), dict):
        return (), ()
    included, excluded = [], []
    for job_id, job in doc["jobs"].items():
        if not isinstance(job, dict):
            continue
        roots = sorted({m.group(1) for run in _step_runs(job)
                        for m in _DISCOVER.finditer(run)})
        if not roots:
            excluded.append((job_id, "runs no `unittest discover` over tests/ at all"))
        elif _needs_live_services(job):
            excluded.append((job_id, "declares service containers (%s) — a local preflight "
                                     "cannot stand those up, and does not claim to"
                             % ", ".join(sorted(job["services"]))))
        else:
            included.append((job_id, "runs the suite from %s" % ", ".join(roots)))
    return tuple(included), tuple(excluded)


def ci_requirements(ci_text):
    """Every `requirements/*.txt` those jobs pass to a `pip install -r`.

    Two narrowings, both deliberate: only steps of an INCLUDED job are read, and within them only
    a `-r`/`--requirement` argument counts. A requirements path appearing anywhere else in a
    `run:` block is not an install, and `pip install -e .` (mokata itself, deliberately unpinned —
    see requirements/README.md) names no requirements file and so contributes nothing here.
    """
    doc = safe_load(ci_text)
    included = {job_id for job_id, _ in unit_suite_jobs(ci_text)[0]}
    if not isinstance(doc, dict) or not isinstance(doc.get("jobs"), dict):
        return frozenset()
    found = set()
    for job_id, job in doc["jobs"].items():
        if job_id not in included or not isinstance(job, dict):
            continue
        for run in _step_runs(job):
            for line in run.splitlines():
                if "pip install" not in line:
                    continue
                for arg in _DASH_R.findall(line):
                    m = _REQ_FILE.search(arg)
                    if m:
                        found.add(m.group(1))
    return frozenset(found)


# ---- the release.sh side: parsed too ---------------------------------------------------------

def _code_only(text):
    """`text` with every whole-line `#` comment dropped.

    Load-bearing, not hygiene: `run_test_preflight`'s body is more comment than code, and its
    comments name the very files under pin ("Hash-pinned ... requirements/README.md"). A reader
    that did not strip them would report the install present exactly when the install was gone.
    """
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def preflight_body(release_text, function=PREFLIGHT_FUNCTION):
    """The BODY of the preflight function, comments stripped — or None if it cannot be located.

    The body, not the whole script: `run_public_subset_preflight` installs
    `"$sub/requirements/jsonschema.txt"` thirty lines below, so a whole-file read would credit the
    preflight with an install performed by a different function against a different venv.
    """
    body = _code_only(release_text)
    start = body.find("%s() {" % function)
    if start == -1:
        return None
    end = body.find("\n}", start)
    if end == -1:
        return None
    return body[start:end]


def preflight_requirements(release_text, function=PREFLIGHT_FUNCTION):
    """Every `requirements/*.txt` the preflight body installs, or None if it cannot be located.

    None, not an empty set: "the function is gone" and "the function installs nothing" are
    different answers, and only one of them is a finding about the install list.
    """
    body = preflight_body(release_text, function)
    if body is None:
        return None
    found = set()
    for line in body.splitlines():
        if "pip install" not in line:
            continue
        for arg in _DASH_R.findall(line):
            m = _REQ_FILE.search(arg)
            if m:
                found.add(m.group(1))
    return frozenset(found)


# ---- the derivation --------------------------------------------------------------------------

def resolve(ci_text, release_text, function=PREFLIGHT_FUNCTION):
    """Derive the parity property from the two supplied texts. NEVER returns GREEN by default."""
    if ci_text is None:
        return ParityResolution(
            BASIS_CI_UNREADABLE,
            detail=".github/workflows/ci.yml could not be read, so there is no install set to "
                   "derive and nothing to hold the preflight to", function=function)
    if release_text is None:
        return ParityResolution(
            BASIS_RELEASE_UNREADABLE,
            detail="scripts/release.sh could not be read, so what the preflight installs is "
                   "unknown — which is not the same as it installing everything", function=function)
    included, _excluded = unit_suite_jobs(ci_text)
    if not included:
        return ParityResolution(
            BASIS_NO_UNIT_SUITE_JOBS,
            detail="no ci.yml job runs `unittest discover` over tests/ (excluding live-service "
                   "jobs), so CI's install set for the unit suite cannot be derived at all",
            function=function)
    required = ci_requirements(ci_text)
    if not required:
        return ParityResolution(
            BASIS_NO_CI_REQUIREMENTS,
            detail="ci.yml's unit-suite jobs (%s) install NO requirements/*.txt, which makes the "
                   "subset check vacuously true. A vacuous true and a covered preflight must not "
                   "share a representation — this is the parse failing, not the preflight passing"
                   % ", ".join(sorted(j for j, _ in included)), function=function)
    installed = preflight_requirements(release_text, function)
    if installed is None:
        return ParityResolution(
            BASIS_NO_PREFLIGHT_FUNCTION, required=required,
            detail="scripts/release.sh has no locatable `%s() { ... }` body, so the install side "
                   "of the comparison could not be read" % function, function=function)
    if required - installed:
        return ParityResolution(BASIS_MISSING_INSTALLS, required=required, installed=installed,
                                function=function)
    return ParityResolution(BASIS_ALL_INSTALLED, required=required, installed=installed,
                            function=function)


def preflight_functions(release_text):
    """Every release.sh function that RUNS THE SUITE — the scope, derived instead of named.

    Stage 28. This module was written to stop a preflight's install list being maintained by hand,
    and then pinned exactly one function by name. `run_public_subset_preflight` runs the same suite
    in its own throwaway venv, installs `jsonschema.txt` and not `ci.txt`, and failed 37 tests on
    PyYAML at the very next cut — `embeddings-leg.yml` a fourth time, now one FUNCTION over
    instead of one file. A function is in scope because it invokes `unittest discover`, not
    because someone remembered to add its name here.
    """
    if release_text is None:
        return ()
    body = _code_only(release_text)
    found = []
    for m in _SHELL_FUNCTION.finditer(body):
        end = body.find("\n}", m.start())
        chunk = body[m.start():end if end != -1 else len(body)]
        if _RUNS_SUITE in chunk:
            found.append(m.group(1))
    return tuple(found)


def resolve_all(ci_text, release_text):
    """One resolution per suite-running function in release.sh. NEVER GREEN by default.

    An empty function set is UNDECIDABLE, never a clean sweep: "release.sh runs the suite nowhere"
    and "every preflight is CI-equivalent" are different answers, and only one of them is good
    news. Same §7g collapse this module already refuses for an empty CI requirement set.
    """
    functions = preflight_functions(release_text)
    if not functions:
        return (ParityResolution(
            BASIS_NO_PREFLIGHT_FUNCTIONS,
            detail="no `name() { ... }` in scripts/release.sh invokes `%s`, so there is no "
                   "preflight to hold to CI at all — the scope is empty, which is a finding "
                   "rather than a pass" % _RUNS_SUITE),)
    return tuple(resolve(ci_text, release_text, fn) for fn in functions)


def read(path):
    """A file's text, or None — so an unreadable side becomes UNDECIDABLE rather than a crash or,
    worse, an empty read that derives a clean answer from nothing."""
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def read_ci(root):
    return read(os.path.join(root, ".github", "workflows", "ci.yml"))


def read_release(root):
    return read(os.path.join(root, "scripts", "release.sh"))
