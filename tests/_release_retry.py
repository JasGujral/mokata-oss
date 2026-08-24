"""B2 — `release.sh` must be re-runnable. The RULES, as pure functions over a SUPPLIED corpus.

WHERE THIS CAME FROM. `release.sh` could not succeed twice. It was filed as one defect — the
release-branch push carries no force — and it is seven, of which the decisive one is not the
push at all: the preflight tag guard refuses at STEP 0, so a run that ever reached the tag step
made every later run of the script impossible. At the 0.0.18 cut `release.sh` was run and
ABORTED, the cut was hand-finished, and step 10 was skipped: the dev tag was never created.

WHY THE FIX IS NOT A FORCE FLAG. The commit a force would discard and the one it publishes have
the IDENTICAL TREE; they differ by a clock reading. Forcing republishes byte-identical content
under a new SHA and therefore throws away the green CI result `release.sh` gates the merge on —
in a release path that burned five PR-CI attempts at 0.0.18. So the branch is REUSED, and NO
FORCE APPEARS ANYWHERE. `no_force_pushes` below is the pin that keeps it that way, and it is the
one rule here whose absence would be invisible on a healthy tree.

DESIGN CONSTRAINTS, each from something that already went wrong in this repo:

1. **PURE FUNCTIONS OVER SUPPLIED TEXT** (doc 85 §7i). Once the scripts are fixed they hold no
   offender, so a rule wired to `scripts/` would pass having graded NOTHING. Every function here
   takes its corpus as an argument and the test file feeds each one a PLANTED offender — including
   the exact text that shipped before this stage, transcribed into the test.

2. **THREE STATES, NEVER TWO** (doc 85 §7g). Every guard this stage adds decides between NOT DONE,
   ALREADY DONE AT THIS COMMIT and DONE SOMEWHERE ELSE. `case_arms` exists so "the third arm was
   deleted" is a finding rather than a smaller corpus that reads as nothing wrong.

3. **A RULE THAT CANNOT CONVICT IS NOT A RULE.** `no_force_pushes` reads GIT PUSH INVOCATIONS, not
   the substring "--force": the word appears in this stage's own refusal messages, explaining why
   forcing was rejected, and a substring pin would have to choose between convicting the prose and
   never convicting anything.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import re

__all__ = [
    "RETRY_GUARDS", "code_lines", "shell_function_source", "shell_function_names",
    "push_invocations", "no_force_pushes", "case_arms", "guard_markers", "missing_guard_markers",
    "shell_function_blocks", "tag_step_lines",
]

# The seven non-idempotent steps, DERIVED from the surface rather than described: each one carries
# a `# RETRY-GUARD: N<k>` marker at the guard that makes it re-runnable, and `missing_guard_markers`
# reports any that is gone. The names are here so a DELETED guard is a named absence — a guard that
# quietly disappears leaves a script that still runs and can still only run once.
RETRY_GUARDS = {
    "N1": "preflight tag guard — refuses at step 0, the step the row never named",
    "N2": "sync-public.sh re-cuts the release branch — the clock mints a new commit each run",
    "N3": "the release-branch push — rejected non-fast-forward, and NOT to be forced",
    "N4": "gh pr create — its failure was explained with a reason that was not true",
    "N5": "gh pr merge — a bare subshell under `set -e`, so 'already merged' was fatal",
    "N6": "the dev annotated tag — `git tag -a` on an existing tag exits 128",
    "N7": "the mirror annotated tag — same, and the preflight never looked at this one",
}

_FUNC_HEAD = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\(\)\s*\{", re.MULTILINE)


def code_lines(text):
    """`[(lineno, line)]` with whole-line comments dropped.

    The scripts have to be able to DESCRIBE the defect they removed — both of them quote the old
    false message and both explain why forcing was rejected — without that prose being convicted
    as the thing it describes."""
    return [(n, ln) for n, ln in enumerate((text or "").splitlines(), 1)
            if not ln.lstrip().startswith("#")]


def shell_function_names(text):
    """Every `name() {` the supplied script defines."""
    return frozenset(_FUNC_HEAD.findall(text or ""))


def shell_function_source(text, name):
    """The verbatim source of one shell function, `name() {` through the closing `}` at column 0.

    Extraction rather than sourcing the whole script: `release.sh` EXECUTES a release when
    sourced. Returns "" when the function is absent, so a caller that lost its subject fails on
    an empty body rather than on a NameError three frames away.
    """
    lines = (text or "").splitlines()
    start = None
    for index, line in enumerate(lines):
        if re.match(r"^%s\(\)\s*\{" % re.escape(name), line):
            start = index
            break
    if start is None:
        return ""
    for index in range(start + 1, len(lines)):
        if lines[index] == "}":
            return "\n".join(lines[start:index + 1]) + "\n"
    return ""


# ---- the one rule whose absence is invisible on a healthy tree ----------------------------------

_PUSH = re.compile(r"\bgit\s+push\b([^\n;)&|]*)")
_FORCE = re.compile(r"(?:^|\s)(--force-with-lease(?:=\S*)?|--force|-[A-Za-z]*f[A-Za-z]*)(?=\s|$)")


def push_invocations(text):
    """`[(lineno, arguments)]` for every live `git push` in the supplied script.

    It also matches a `git push` INSIDE an `echo` — the script prints a manual fallback sequence
    when it has no mirror checkout. That is deliberate and it is the safe direction: an echoed
    force flag is a false POSITIVE that fails loudly, where excluding echoes would create a place
    a force could be reintroduced with the pin still green.
    """
    return [(n, m.group(1)) for n, ln in code_lines(text) for m in [_PUSH.search(ln)] if m]


def no_force_pushes(text):
    """`[(lineno, flag)]` for every live `git push` carrying a force flag — the offenders.

    ⛔ THE POINT OF THE STAGE. The reported remedy was to add `--force-with-lease` here. It works —
    the lease is evaluable, because `checkout -B` never touches `refs/remotes/origin/<branch>` —
    and it is still wrong: the discarded and the published commit carry the same tree, so forcing
    buys a fresh CI cycle for byte-identical content. Reuse replaced it, and nothing may put it
    back without this going red.

    Short flags are matched by LETTER (`-qf` is a force push), because a force that arrives
    bundled into a cluster is the shape that survives a `--force` substring pin.
    """
    return [(n, m.group(1)) for n, args in push_invocations(text)
            for m in [_FORCE.search(args)] if m]


# ---- three states, never two --------------------------------------------------------------------

def case_arms(text, subject):
    """`{label: exits}` for the `case "$subject" in ...` block in `text`.

    `exits` is True when that arm reaches an `exit`. Two facts, not one: a guard can have three
    arms and still be a two-state guard if the "already done here" arm exits like the "done
    elsewhere" one — which is exactly the collapse the preflight made, one level up.
    """
    pattern = re.compile(r'^\s*case\s+"?\$(?:\{)?%s(?:\})?"?\s+in\s*$' % re.escape(subject))
    lines = (text or "").splitlines()
    start = None
    for index, line in enumerate(lines):
        if pattern.match(line):
            start = index
            break
    if start is None:
        return {}
    arms, label, depth = {}, None, 0
    for line in lines[start + 1:]:
        stripped = line.strip()
        if stripped == "esac" and depth == 0:
            break
        head = re.match(r'^([^()\s][^()]*?)\)\s*(.*)$', stripped)
        if head and depth == 0 and label is None:
            label = head.group(1).strip().strip('"\'')
            arms[label] = False
            stripped = head.group(2)
        if label is not None and re.search(r"\bexit\b", stripped):
            arms[label] = True
        if label is not None and stripped.endswith(";;"):
            label = None
    return arms


# ---- completeness: a deleted guard is a named absence -------------------------------------------

_MARKER = re.compile(r"#\s*RETRY-GUARD:\s*([N0-9 +]+)")


def guard_markers(*texts):
    """Every `N<k>` named by a `# RETRY-GUARD:` marker across the supplied scripts."""
    found = set()
    for text in texts:
        for match in _MARKER.finditer(text or ""):
            found.update(re.findall(r"N\d+", match.group(1)))
    return found


def missing_guard_markers(*texts):
    """The guards in `RETRY_GUARDS` that no supplied script claims. Sorted, so the failure NAMES
    which step lost its guard rather than reporting a count."""
    return sorted(set(RETRY_GUARDS) - guard_markers(*texts))


# ---- WHERE THE TAG STEP IS — derived, because three files had it hard-coded ---------------------
#
# ⚠ THIS EXISTS BECAUSE ONE REFACTOR REDDENED FOUR CORRECT PINS IN THREE FILES. Routing both tag
# calls through `ensure_tag` moved the literal `git tag -a "$TAG"` off the call site, and
# `test_stage61b_release_process`, `test_stage68_supply_chain` and `test_dg7_release_notes_disclosure`
# each located "the tag step" by searching for that exact string. All four assertions were still
# TRUE — the tag still happens after the sync, after the check, after both CI waits — and all four
# went red anyway, reporting `release.sh no longer tags`.
#
# That is one fact with three representations, which is the shape doc 85 §7f warns about: it cannot
# be corrected in one place, so the next person fixes the two that reddened and leaves the third.
# It is also `PIN-SUBSTRING-COMMENT-HOLE`'s cousin — the pin was reading a SPELLING, not a step.
#
# ⛔ AND IT MUST NOT BE FIXED BY SEARCHING FOR `git tag -a` ANYWHERE, which is the obvious repair
# and the wrong one. That string now occurs inside `ensure_tag`'s DEFINITION, ~200 lines ABOVE the
# sync — so a naive search reports the tag as preceding the mirror sync. `release.sh` has already
# produced one false claim that way (`RELEASE-SH-DEV-CI-WAIVER-OUTLIVED-ITS-SCOPE`: read from the
# function definition, never from the call site). Here it would fail loudly rather than pass
# silently, but it would be the same misreading.

_FUNC_OPEN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\(\)\s*\{")
_TAG_CREATE = re.compile(r"\bgit\s+tag\s+-a\b")


def shell_function_blocks(text):
    """`{name: (first_line, last_line)}` for every `name() {` … `}` block, 1-indexed inclusive."""
    blocks, lines = {}, (text or "").splitlines()
    for index, line in enumerate(lines):
        head = _FUNC_OPEN.match(line)
        if not head:
            continue
        for end in range(index + 1, len(lines)):
            if lines[end] == "}":
                blocks[head.group(1)] = (index + 1, end + 1)
                break
    return blocks


def tag_step_lines(text):
    """Line numbers at which `release.sh` CREATES A TAG, whatever shape the step takes.

    Two shapes are recognised, and both are read at the CALL SITE:

      * an inline `git tag -a …` at top level — its own line;
      * a `git tag -a …` inside a shell function — every LIVE call site of that function.

    Echoed lines are excluded: the script prints the whole manual fallback runbook, `git tag -a`
    included, when it has no mirror checkout to work with. Printing a command is not running one,
    and counting the runbook would place "the tag step" hundreds of lines before the real one.

    Returns [] when nothing creates a tag — so `release.sh no longer tags` stays a real finding
    rather than becoming an artefact of how the step is spelled.
    """
    blocks = shell_function_blocks(text)
    inside = {}
    for name, (first, last) in blocks.items():
        inside[name] = (first, last)

    direct, via_function = [], set()
    for number, line in code_lines(text):
        code = line.strip()
        if not _TAG_CREATE.search(code) or code.startswith("echo"):
            continue
        owner = next((n for n, (f, l) in inside.items() if f <= number <= l), None)
        if owner is None:
            direct.append(number)
        else:
            via_function.add(owner)

    found = list(direct)
    for name in via_function:
        found.extend(number for number, _arguments in live_calls_in(text, name))
    return sorted(set(found))


_CALL = r"(?:^|[;&|(]|\bthen\b|\belse\b|\bdo\b|\{)[ \t]*%s\b(?![ \t]*\()"


def live_calls_in(text, name):
    """`[(lineno, line)]` for LIVE invocations of `name` — the definition is not a call.

    A local re-implementation rather than an import from `_release_repo_guards`: that module is
    the dev-repo-is-not-the-publishing-repo sweep and owns a different question. Sharing the
    regex would couple two sweeps that must be able to change independently.
    """
    pattern = re.compile(_CALL % re.escape(name))
    return [(number, line) for number, line in code_lines(text)
            if pattern.search(line.split("#")[0]) and not line.strip().startswith("echo")]
