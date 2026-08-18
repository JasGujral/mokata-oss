"""The dev-repo-is-not-the-publishing-repo sweep — PURE FUNCTIONS over a SUPPLIED CORPUS.

0.0.18 stage 7 (`TAG-ON-THE-DEV-REPO-IS-A-SILENT-NO-OP` + `RELEASE-SH-DEV-CI-WAIVER-OUTLIVED-ITS-SCOPE`
+ `RELEASE-CHECK-BARE-COMMAND-READS-SITE-PACKAGES`, doc 84 §9; doc 102 exit criterion 6, FIRST half).

THREE ROWS, ONE SENTENCE, stated once so the functions below are not a style guide:

    **The dev repo is not the publishing repo, and no surface said so at the moment of use.**

Three surfaces, three silences, and the three fixes are the three sections of this file.

  ① THE WORKFLOW.  All five jobs in `release.yml` (`test`, `validate`, `build`, `github-release`,
     `pypi`) carry `if: github.repository == 'JasGujral/mokata-oss'`. Tag `v0.0.17` on the private
     `JasGujral/mokata` and the workflow triggers, all five skip, and the run concludes GREEN — no
     build, no Release, no upload, and nothing anywhere saying a release was attempted and did not
     happen. "This repository does not publish" and "publishing succeeded" shared ONE
     representation, and the representation was a green check (doc 85 §7g).

     ⚠ THE GUARDS ARE CORRECT AND NONE OF THEM MAY BE REMOVED. What this stage adds is a voice:
     one job carrying the INVERSE guard, whose only step fails loud. It cannot regress the publish
     path because on the mirror it never runs.

     ⚠ IT NEARLY SHIPPED. The 0.0.17 cut was instructed to *"tag v0.0.17 and let release.yml
     publish"* against the DEV repo, by a coordinator that had already read those five guards
     earlier in the same session. What caught it was a builder choosing to re-derive the publish
     path. A guard that holds only because someone chose to re-derive is not a mechanism — it is a
     habit with a good track record.

  ② THE SCRIPT.  `release.sh` carried a commented-out `wait_for_ci_green "$DEV_REPO" ...` under a
     billing exemption reading *"KEPT THROUGH 0.0.10 — Jas 2026-07-06"*, plus an unconditional
     `echo "SKIPPED (billing waiver, through 0.0.10)"`. Its stated scope ended SEVEN releases before
     0.0.18 and nothing re-armed it, re-affirmed it, or could DETECT that it had lapsed. At the
     0.0.17 cut it was handed down as an enforced gate — read from the function DEFINITION, never
     from the call site, which is `BACKLOG-EVIDENCE-UNDATED` at one session's width.

     ⭐ A WAIVER WHOSE SUNSET LIVES ONLY IN PROSE IS NOT TIME-BOXED AT ALL. Section ② is the
     mechanism that makes an expiry detectable by something other than a human reading a comment:
     a declaration whose `through=` version is below the version being cut is a TEST FAILURE.

  ③ THE DOCUMENTED HUMAN COMMAND.  `python3 -m mokata release-check <ver>` run bare imported an
     old mokata from `site-packages` and printed PASS against ITS OWN, SMALLER field set. Fixed in
     `src/mokata/packaging.py` (the answer names the package that produced it and REFUSES when that
     package is outside `--root`), so this file only pins the SHAPE of what the script and the docs
     tell a human to run.

DESIGN CONSTRAINTS, each from something that already went wrong in this repo:

1. **PURE FUNCTIONS OVER SUPPLIED TEXT** (doc 85 §7i). Once the tree is fixed it holds no offender,
   so a sweep wired to `.github/workflows/` and `scripts/` would pass having graded NOTHING. Every
   function here takes its corpus as an argument, and `test_tag_is_not_a_publish.py` hands it
   synthetic offenders — including the real v0.0.17 text, transcribed below.

2. **§7i AGAIN, AND THE ROW STATED IT: THIS CANNOT BE GRADED ON A HEALTHY TREE, AND IT MUST NOT BE
   GRADED ON A LIVE ONE.** The offender is *a tag pushed to the wrong repository*. A live tag is
   exactly what must not be created to test it. So the guard is EVALUATED against two SYNTHETIC
   repository names rather than observed on a real run — `evaluate_repository_guard` is a tiny
   interpreter for the one expression shape the release path uses, and `guard_classes` decides each
   job's class by what it DOES on those two names rather than by matching its text.

3. **THREE STATES, NEVER TWO** (doc 85 §7g), everywhere:
     jobs      PUBLISH-GUARDED · REFUSAL · UNGUARDED · NEVER-RUNS · UNREADABLE  (five, in fact)
     waivers   LIVE · EXPIRED · UNDATED
     answers   a guard that cannot be parsed RAISES; it never quietly reads as "not guarded".

⚠ WHAT THIS DOES NOT GRADE, declared rather than discovered:

  * **It is not a GitHub expression engine.** `evaluate_repository_guard` understands
    `github.repository == '<literal>'` and `!=`, optionally wrapped in `${{ }}`. ANY other shape
    raises `UnreadableGuard` and the caller must classify it as UNREADABLE — a fail-loud false red,
    never a silent pass. Extending the release path to a richer condition means extending this.
  * **It does not prove a run.** That a tag pushed to `JasGujral/mokata` produces a RED run is
    verifiable only on `JasGujral/mokata`, whose Actions billing is off. What is proven here is
    that the condition selects the dev repo and rejects the mirror, that the job depends on nothing
    that could skip it, and that its step — executed verbatim under bash — exits non-zero naming
    the mirror. The run itself is a dated obligation in the stage report, not a claim.
  * **`disabled_calls` reads a SHAPE, not an intent.** A commented-out invocation of a function the
    script itself defines is the shape the 2026-07-06 exemption had. A reader who genuinely wants
    one must attach a `WAIVER(...)` declaration, which then expires.
"""

import re

from _workflow_pins import safe_load        # the ONE representation of "the parser is absent"

__all__ = [
    "PUBLISHING_REPOSITORY", "DEV_REPOSITORY", "UnreadableGuard", "evaluate_repository_guard",
    "runs_on_repository", "GUARD_PUBLISH", "GUARD_REFUSAL", "GUARD_UNGUARDED", "GUARD_NEVER_RUNS",
    "GUARD_UNREADABLE", "classify_condition", "guard_classes", "jobs_in_class", "job_needs",
    "job_run_steps", "WAIVER_LIVE", "WAIVER_EXPIRED", "WAIVER_UNDATED", "waiver_declarations",
    "waiver_states", "shell_function_names", "disabled_calls", "live_calls", "call_arguments",
    "safe_load",
]

# The two repositories, and they are not interchangeable. One publishes; one does not.
PUBLISHING_REPOSITORY = "JasGujral/mokata-oss"
DEV_REPOSITORY = "JasGujral/mokata"


# ---- ① the workflow: a guard evaluated, not matched ---------------------------------------------

class UnreadableGuard(ValueError):
    """This `if:` is a shape the evaluator does not implement, so it CANNOT ANSWER.

    Raised rather than returning False on purpose. "the guard says do not run" and "we could not
    read the guard" are different facts, and a caller that cannot tell them apart would report a
    new, unparseable condition as a correctly-guarded job (§7g).
    """


_GUARD = re.compile(
    r"""^\s*(?:\$\{\{)?\s*github\.repository\s*(==|!=)\s*'([^']*)'\s*(?:\}\})?\s*$""")


def evaluate_repository_guard(expression, repository):
    """Whether a job's `if:` admits `repository`. Raises `UnreadableGuard` on any other shape.

    The whole point of the SYNTHETIC input: the row's offender is a tag pushed to the wrong
    repository, which no test on this checkout may stage. Feeding the condition two repository
    NAMES asks the same question the runner would, without a tag existing anywhere.
    """
    match = _GUARD.match(expression or "")
    if match is None:
        raise UnreadableGuard(expression)
    operator, literal = match.group(1), match.group(2)
    return (repository == literal) if operator == "==" else (repository != literal)


def runs_on_repository(condition, repository):
    """Whether a job with this `if:` (possibly absent) would run on `repository`.

    An ABSENT `if:` is not an unreadable one — a job with no condition runs everywhere, which is
    the ungated job `TestEveryReleaseJobIsRepoGated` was written to make impossible.
    """
    if condition is None or str(condition).strip() == "":
        return True
    return evaluate_repository_guard(str(condition), repository)


GUARD_PUBLISH = "publish-only-on-the-mirror"       # runs on the mirror, not on the dev repo
GUARD_REFUSAL = "refuse-off-the-mirror"            # runs on the dev repo, not on the mirror
GUARD_UNGUARDED = "unguarded"                      # runs on BOTH — the defect this cannot regress
GUARD_NEVER_RUNS = "never-runs"                    # runs on NEITHER — dead, and it reads as gated
GUARD_UNREADABLE = "unreadable"                    # a condition this evaluator cannot answer for


def classify_condition(condition, mirror=PUBLISHING_REPOSITORY, dev=DEV_REPOSITORY):
    """One job's class, DECIDED BY BEHAVIOUR on two synthetic repository names.

    Not by string matching: `github.repository != 'JasGujral/mokata-oss'` CONTAINS the equality
    guard's repository name, so a substring test cannot tell the publish guard from its inverse —
    and the pin that used a substring test is exactly the one this stage had to widen.
    """
    try:
        on_mirror = runs_on_repository(condition, mirror)
        on_dev = runs_on_repository(condition, dev)
    except UnreadableGuard:
        return GUARD_UNREADABLE
    if on_mirror and not on_dev:
        return GUARD_PUBLISH
    if on_dev and not on_mirror:
        return GUARD_REFUSAL
    return GUARD_UNGUARDED if on_mirror else GUARD_NEVER_RUNS


def guard_classes(doc, mirror=PUBLISHING_REPOSITORY, dev=DEV_REPOSITORY):
    """`{job_id: class}` for EVERY job in a parsed workflow — the SET, derived, never typed.

    Ranging over `jobs:` rather than over the five names we happen to know is what makes a SIXTH,
    unguarded job a test failure instead of an addition nobody notices.
    """
    if not isinstance(doc, dict):
        return {}
    jobs = doc.get("jobs")
    if not isinstance(jobs, dict):
        return {}
    return {job_id: classify_condition(
        (job or {}).get("if") if isinstance(job, dict) else None, mirror, dev)
        for job_id, job in jobs.items()}


def jobs_in_class(doc, wanted, mirror=PUBLISHING_REPOSITORY, dev=DEV_REPOSITORY):
    return tuple(sorted(job_id for job_id, cls in guard_classes(doc, mirror, dev).items()
                        if cls == wanted))


def job_needs(doc, job_id):
    """A job's `needs:`, normalised to a tuple. `()` means it depends on nothing.

    A refusal job that `needs:` a publish-guarded job would be SKIPPED on the dev repo for the
    same reason those jobs are — the row again, one job over — so the emptiness is graded.
    """
    job = ((doc.get("jobs") or {}) if isinstance(doc, dict) else {}).get(job_id) or {}
    needs = job.get("needs") if isinstance(job, dict) else None
    if needs is None:
        return ()
    if isinstance(needs, str):
        return (needs,)
    return tuple(needs)


def job_run_steps(doc, job_id):
    """`[(name, run_text)]` for every `run:` step of one job — what the refusal actually says."""
    job = ((doc.get("jobs") or {}) if isinstance(doc, dict) else {}).get(job_id) or {}
    steps = job.get("steps") if isinstance(job, dict) else None
    if not isinstance(steps, list):
        return ()
    return tuple((step.get("name") or "", step["run"]) for step in steps
                 if isinstance(step, dict) and isinstance(step.get("run"), str))


# ---- ② the script: a call site, a disabled call, and a sunset a machine can see ------------------

WAIVER_LIVE = "live"          # declared, and its `through` version has not been cut yet
WAIVER_EXPIRED = "expired"    # declared, and the release being cut is past its stated scope
WAIVER_UNDATED = "undated"    # declared with no readable `through` — a permanent waiver in a hat

# The machine-readable form. The prose may say anything; THIS is what expires.
#     # WAIVER(id=dev-ci-billing, through=0.0.10, owner=Jas, filed=2026-07-06): prose…
_WAIVER = re.compile(r"WAIVER\(([^)]*)\)")
_FIELD = re.compile(r"([A-Za-z_][A-Za-z0-9_-]*)\s*=\s*([^,]*)")
_VERSION = re.compile(r"^\d+(?:\.\d+)*$")


def _version_key(text):
    if not _VERSION.match((text or "").strip()):
        return None
    return tuple(int(part) for part in text.strip().split("."))


def waiver_declarations(text):
    """`[(line_number, {field: value})]` for every `WAIVER(...)` declaration in a supplied text."""
    found = []
    for number, line in enumerate((text or "").splitlines(), start=1):
        for match in _WAIVER.finditer(line):
            fields = {k: v.strip() for k, v in _FIELD.findall(match.group(1))}
            found.append((number, fields))
    return tuple(found)


def waiver_states(text, current_version):
    """`[(line_number, state, fields)]` — every declared waiver, judged against the version being
    cut.

    ⭐ THIS IS THE MECHANISM THE ROW ASKED FOR. `through=0.0.10` stops being a sentence a human has
    to notice and becomes an arithmetic comparison against `pyproject.toml`'s version: the release
    that goes past a waiver's stated scope is the release whose test suite goes red. Nothing here
    reads a date, because a date needs a clock and a clock is not in the tree; the version being
    cut is.
    """
    now = _version_key(current_version)
    out = []
    for number, fields in waiver_declarations(text):
        through = _version_key(fields.get("through", ""))
        if through is None or now is None:
            out.append((number, WAIVER_UNDATED, fields))
        elif now > through:
            out.append((number, WAIVER_EXPIRED, fields))
        else:
            out.append((number, WAIVER_LIVE, fields))
    return tuple(out)


_FUNC_HEAD = re.compile(
    r"^[ \t]*(?:function[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)[ \t]*\([ \t]*\)[ \t]*\{", re.M)


def shell_function_names(text):
    """Every `name() {` this script defines. DERIVED — nothing below names a function."""
    return frozenset(_FUNC_HEAD.findall(text or ""))


def _code_and_comment(line):
    """`(code, comment)` for one shell line, splitting at the first `#` that starts a word
    outside quotes. A mention of a command inside a sentence is not a call, and a call commented
    out is not a mention — telling them apart is the whole of `disabled_calls`."""
    quote = None
    for index, char in enumerate(line):
        if quote:
            if char == quote:
                quote = None
            continue
        if char in "'\"":
            quote = char
            continue
        if char == "#" and (index == 0 or line[index - 1] in " \t"):
            return line[:index], line[index + 1:]
    return line, None


def disabled_calls(text, names=None):
    """`[(line_number, comment)]` for COMMENTED-OUT invocations of the script's own functions.

    THE SHAPE THE EXEMPTION ACTUALLY HAD: `# wait_for_ci_green "$DEV_REPO" "$(git rev-parse HEAD)"`
    sat under five lines of prose, and for seven releases it read to every reader as a gate that
    was merely resting. A disabled call is not a gate and not a comment — it is a third thing no
    reader can predict, which is precisely why the row refused to prefer re-arming or deleting and
    demanded that one be CHOSEN.

    The function names are DERIVED from the same text, so this cannot be satisfied by renaming and
    cannot fire on prose: `# under $PYTHON (default python3)` begins with a word that is not a
    function, and `# the live-db matrix (only CI can — see wait_for_ci_green below)` does not begin
    with one at all.
    """
    if names is None:
        names = shell_function_names(text)
    if not names:
        return ()
    pattern = re.compile(r"^[ \t]*(%s)[ \t]+[\"'$]" % "|".join(sorted(map(re.escape, names))))
    found = []
    for number, line in enumerate((text or "").splitlines(), start=1):
        _code, comment = _code_and_comment(line)
        if comment is not None and pattern.match(comment):
            found.append((number, comment.strip()))
    return tuple(found)


def live_calls(text, name):
    """`[(line_number, argument_text)]` for LIVE (uncommented) invocations of one function.

    Live only, deliberately: a commented-out call is `disabled_calls`' offender and charging both
    would make one defect read as two, so neither could be graded alone (doc 85 §7f).

    Arguments are OPTIONAL — `run_test_preflight` takes none, and a call-site finder that required
    one would report the fail-closed local preflight as never invoked. What makes it a call is the
    COMMAND POSITION: a name inside `echo "see wait_for_ci_green for details"` is preceded by a
    quote, not by a statement boundary, so it is a mention.
    """
    # `(?![ \t]*\()` drops the DEFINITION — `wait_for_ci_green() {` is where the function is
    # written, not where it is invoked, and counting it as a call is how the claim behind
    # `RELEASE-SH-DEV-CI-WAIVER-OUTLIVED-ITS-SCOPE` was formed in the first place: read from the
    # definition, never from the call site.
    pattern = re.compile(r"(?:^|[;&|(]|\bthen\b|\belse\b|\bdo\b|\{)[ \t]*%s\b(?![ \t]*\()[ \t]*(.*)$"
                         % re.escape(name))
    found = []
    for number, line in enumerate((text or "").splitlines(), start=1):
        code, _comment = _code_and_comment(line)
        match = pattern.search(code)
        if match:
            found.append((number, match.group(1).strip()))
    return tuple(found)


def call_arguments(argument_text):
    """The first argument of a call, unquoted — `"$PUB_REPO"` -> `$PUB_REPO`."""
    words = argument_text.split()
    return words[0].strip("\"'") if words else ""


# ---- THE v0.0.17 RECORD — literal, dated, each with the command that produced it -----------------
#
# History cannot be derived (§7j: DECLARED literals, not a typed scope). Each block below is
# transcribed from the tree as it stood at the 0.0.17 cut, with the command that reads it back.

# $ git show v0.0.17:.github/workflows/release.yml | grep -n 'github.repository'
# 23:    if: github.repository == 'JasGujral/mokata-oss'   # releases only from the public repo
# 52:    if: github.repository == 'JasGujral/mokata-oss'
# 109:   if: github.repository == 'JasGujral/mokata-oss'
# 208:   if: github.repository == 'JasGujral/mokata-oss'
# 256:   if: github.repository == 'JasGujral/mokata-oss'
#
# The five job ids those guards sat under, in file order. ⚠ FIVE JOBS AND NO SIXTH: a tag on the
# dev repo skipped every one of them and the run concluded green.
V0017_GUARDED_JOBS = ("test", "validate", "build", "github-release", "pypi")

# The v0.0.17 workflow's `jobs:` mapping, reduced to what decides a skip. This is the document
# `guard_classes` is graded against — every job PUBLISH-GUARDED, not one REFUSAL among them.
V0017_JOBS_DOC = {
    "jobs": {job: {"if": "github.repository == '%s'" % PUBLISHING_REPOSITORY}
             for job in V0017_GUARDED_JOBS}
}

# $ git show v0.0.17:scripts/release.sh | sed -n '311,317p'      — verbatim, including the echo.
V0017_WAIVER_BLOCK = """# --- 2b) wait for the dev repo's CI to conclude GREEN on the exact pushed commit ----------
# BILLING-WAIVER (KEPT THROUGH 0.0.10 — Jas 2026-07-06; RESTORE ONCE PRIVATE-REPO BILLING IS FIXED):
# the private repo's Actions cannot start jobs (billing: every job in run 28700943491 was "not
# started"), so the dev-repo CI wait is skipped. The step-4b PUBLIC mirror CI wait below (free, full
# matrix, the repo actually tagged/published from) remains the enforced gate. Deviation logged in
# docs/build/02-mokata-build-status.md (2026-07-04; re-affirmed for 0.0.10 2026-07-06).
# wait_for_ci_green "$DEV_REPO" "$(git rev-parse HEAD)" "the dev repo"
echo "SKIPPED (billing waiver, through 0.0.10): dev-repo CI wait — the mirror CI wait still gates the tag."
"""

# The same block with the function defined, so `disabled_calls` has a corpus to derive names from.
V0017_SCRIPT = "wait_for_ci_green() {\n  :\n}\n" + V0017_WAIVER_BLOCK

# The version the exemption was written against, and the one it was still in force for. Its stated
# scope ended at 0.0.10; 0.0.11 … 0.0.17 were cut with it silently in place.
V0017_WAIVER_THROUGH = "0.0.10"
V0017_RELEASES_PAST_SUNSET = ("0.0.11", "0.0.12", "0.0.13", "0.0.14",
                              "0.0.15", "0.0.16", "0.0.17")

# $ python3 -m mokata release-check 0.0.17          (bare, from the repo root, 2026-08-13)
# release-check PASS — intended tag 0.0.17     … five field lines, exit 0.
# $ python3 -c 'import mokata; print(mokata.__version__, mokata.__file__)'
# 0.0.9 /Users/jas/Library/Python/3.9/lib/python/site-packages/mokata/__init__.py
V0017_BARE_FIELD_COUNT = 5
V0017_SITE_PACKAGES_VERSION = "0.0.9"
