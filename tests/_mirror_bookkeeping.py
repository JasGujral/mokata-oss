"""Derive a mirror-boundary control's status FROM THE CONTROLS, instead of writing it down.

Stage 11 (BOOKKEEPING-SWEEP-MANUAL). Doc 02's 0.0.16 verification sweep carries a
"Blocking the cut?" column that a human types. It reported `TODELETE-LEAK` 🔴 when commit
`9ec1b05` had already closed it. That instance was stale in the SAFE direction — it over-reported
a risk — and the row was filed anyway, for the reason that matters: THE MECHANISM HAS NO
DIRECTION PREFERENCE. The same hand-maintained column that called a closed leak open will, on
some later pass, call an open leak closed, and a stale-GREEN row on a mirror-boundary control is
a silent leak found by whoever reads the public repo.

THREE OUTCOMES, THREE REPRESENTATIONS. The row's own words are "a row that cannot be derived
should render as UNKNOWN, never as a default-green", which is §7g written before §7g existed. So:

    GREEN        decided — both controls cover the path
    RED          decided — at least one control does not
    UNDECIDABLE  NOT decided — and `detail` says WHY, because "we could not tell" and
                 "it is fine" must never share a representation

The vocabulary is `RunResolution`'s and `knowledge/query.py`'s, deliberately not a third one:
`basis` is the ONE stored signal and `verdict` is DERIVED from it through `_VERDICT_OF`, never
stored beside it. Two fields that can disagree eventually do — that is exactly how a written
status column drifts from the control it describes, which is the defect this module exists to
remove. `BASIS_VERIFIED_*` here plays the part `BASIS_VERIFIED_EMPTY` plays there: a checked
answer and an unchecked one are different answers.

THE COUPLING THE ROW WARNED ABOUT. "This fix is only as trustworthy as the pins it derives from —
see PIN-SUBSTRING-COMMENT-HOLE, where six of those very pins pass on their own comments. Deriving
status from a vacuous pin would manufacture exactly the stale-green this row warns about, so the
pins must be scoped FIRST." Hence two things here:

  * every read is COMMENT-STRIPPED (TD-2 in doc 02: the `_to_delete` entry sits under six lines of
    prose naming it four times, so a raw substring check passed on the comment after the entry
    was deleted);
  * the controls are PARSED INTO ENTRY SETS — the values inside `--exclude=` clauses, and the
    items of the `INTERNAL_PATHS=( ... )` array literal — rather than substring-searched inside a
    block. A substring check cannot tell `editors/vscode/out` covered from `editors/vscode/out`
    merely mentioned somewhere in the same region.
"""

import fnmatch
import os
import re

# ---- verdicts: what a caller may do about it -------------------------------------------------
GREEN = "green"
RED = "red"
UNDECIDABLE = "undecidable"

# ---- bases: WHICH rung produced the verdict (the one stored signal) --------------------------
BASIS_VERIFIED_BOTH = "verified_both"        # in --exclude AND in INTERNAL_PATHS
BASIS_EXCLUDE_ONLY = "exclude_only"          # bytes excluded, hard-guard backstop gone
BASIS_GUARD_ONLY = "guard_only"              # guard names it, rsync still copies it
BASIS_VERIFIED_NEITHER = "verified_neither"  # covered by nothing at all
BASIS_NO_EXCLUDE_CLAUSES = "no_exclude_clauses"
BASIS_NO_GUARD_ARRAY = "no_guard_array"
BASIS_SCRIPT_UNREADABLE = "script_unreadable"
BASIS_PATTERN_COVERAGE = "pattern_coverage"  # covered by a GLOB, not a literal — cannot decide

#: basis -> the ONE verdict it produces. The single place the two vocabularies meet, so a verdict
#: can never be asserted independently of the basis that earned it.
_VERDICT_OF = {
    BASIS_VERIFIED_BOTH: GREEN,
    BASIS_EXCLUDE_ONLY: RED,
    BASIS_GUARD_ONLY: RED,
    BASIS_VERIFIED_NEITHER: RED,
    BASIS_NO_EXCLUDE_CLAUSES: UNDECIDABLE,
    BASIS_NO_GUARD_ARRAY: UNDECIDABLE,
    BASIS_SCRIPT_UNREADABLE: UNDECIDABLE,
    BASIS_PATTERN_COVERAGE: UNDECIDABLE,
}

#: The bases that mean "no answer was produced". Named rather than derived by comparison so a
#: caller can ask the question directly.
UNDECIDABLE_BASES = frozenset(
    b for b, v in _VERDICT_OF.items() if v == UNDECIDABLE)

_EXCLUDE = re.compile(r"--exclude=(?:'([^']*)'|\"([^\"]*)\"|(\S+))")


class ControlResolution(object):
    """One internal path's status, derived. Frozen by convention (no setters are offered).

    `verdict` is a PROPERTY of `basis`, never a stored field — `RunResolution`'s discipline.
    """

    __slots__ = ("path", "basis", "detail")

    def __init__(self, path, basis, detail=""):
        if basis not in _VERDICT_OF:
            raise ValueError("unknown basis %r" % (basis,))
        if basis in UNDECIDABLE_BASES and not detail:
            raise ValueError(
                "%s: basis=%r is UNDECIDABLE and carries no reason. An undecidable row that "
                "cannot say why it is undecidable is indistinguishable from a shrug, and a "
                "reader will round it to green — which is the whole defect this module removes."
                % (path, basis))
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "basis", basis)
        object.__setattr__(self, "detail", detail)

    def __setattr__(self, *_a):                       # pragma: no cover - immutability guard
        raise AttributeError("ControlResolution is immutable")

    @property
    def verdict(self):
        return _VERDICT_OF[self.basis]

    @property
    def decided(self):
        return self.verdict != UNDECIDABLE

    def render(self):
        """The cell a doc table would carry — a computed output, not a written claim."""
        if self.verdict == GREEN:
            return "GREEN — covered by both controls"
        if self.verdict == RED:
            return "RED — %s" % _RED_REASON[self.basis]
        return "UNKNOWN — %s" % self.detail

    def __repr__(self):
        return "<%s %s basis=%s>" % (self.path, self.verdict.upper(), self.basis)


_RED_REASON = {
    BASIS_EXCLUDE_ONLY: "in --exclude but NOT in INTERNAL_PATHS (the bytes are held back, "
                        "but the hard-guard that would catch a regression is gone)",
    BASIS_GUARD_ONLY: "in INTERNAL_PATHS but NOT in --exclude (rsync still copies it; the "
                      "guard aborts the sync instead of preventing the copy)",
    BASIS_VERIFIED_NEITHER: "named by NEITHER control — it reaches the public checkout and "
                            "nothing says a word",
}


def _code_only(text):
    """`text` with every whole-line `#` comment dropped — TD-2's lesson, applied to both reads."""
    return "\n".join(
        ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def exclude_entries(script):
    """Every LITERAL path inside an `--exclude=` clause, comments stripped, trailing `/` dropped.

    Entries carrying a `$` are omitted: `sync-public.sh:52` builds `--exclude="/$rel/"` inside a
    loop over discovered nested checkouts, so its value does not exist until run time. Reading it
    as the literal string `/$rel` would put a path in the covered set that covers nothing.
    """
    return frozenset(
        _norm(v) for v in (
            m.group(1) or m.group(2) or m.group(3)
            for m in _EXCLUDE.finditer(_code_only(script)))
        if "$" not in v)


def guard_entries(script):
    """The items of the `INTERNAL_PATHS=( ... )` ARRAY LITERAL.

    The array, not "everything after it": the literal is followed by the enforcement loop and its
    commentary, which name internal paths in prose and would satisfy a membership check on their
    own. Returns None when the array cannot be located — an absent answer, not an empty one.
    """
    body = _code_only(script)
    start = body.find("INTERNAL_PATHS=(")
    if start == -1:
        return None
    end = body.find("\n)", start)
    if end == -1:
        return None
    items = body[start + len("INTERNAL_PATHS=("):end]
    return frozenset(
        _norm(tok.strip("'\"")) for tok in items.split() if tok.strip("'\""))


def _norm(path):
    return path.rstrip("/")


# How an entry set covers a path. Three states again, for the same reason as everywhere else.
COVERED_LITERAL = "literal"
COVERED_PATTERN = "pattern"
COVERED_NONE = "none"

_GLOB_CHARS = "*?["


def _coverage(entries, want):
    """(state, matching_pattern_or_None) — literal entries decide; globs only raise a question.

    A leading `/` is rsync's "anchored at the transfer root" marker, so it is stripped before the
    glob comparison; that makes the pattern check DELIBERATELY over-eager. Over-eagerness is the
    safe direction here because a pattern match never decides anything — it only routes the path
    to UNDECIDABLE, where a human looks.
    """
    if want in entries:
        return COVERED_LITERAL, None
    for e in sorted(entries):
        if any(c in e for c in _GLOB_CHARS) and fnmatch.fnmatch(want, e.lstrip("/")):
            return COVERED_PATTERN, e
    return COVERED_NONE, None


def resolve(path, script, script_path="scripts/sync-public.sh"):
    """Derive one path's status from the script text. NEVER returns GREEN by default."""
    if script is None:
        return ControlResolution(
            path, BASIS_SCRIPT_UNREADABLE,
            "%s could not be read, so neither control could be inspected" % script_path)
    excludes = exclude_entries(script)
    if not excludes:
        return ControlResolution(
            path, BASIS_NO_EXCLUDE_CLAUSES,
            "%s contains no `--exclude=` clauses at all; the rsync boundary is not where this "
            "derivation expects it, so no status can be computed" % script_path)
    guards = guard_entries(script)
    if guards is None:
        return ControlResolution(
            path, BASIS_NO_GUARD_ARRAY,
            "%s has no locatable `INTERNAL_PATHS=( ... )` array literal, so the hard-guard side "
            "of the pair could not be read" % script_path)
    want = _norm(path)
    ex, ex_pat = _coverage(excludes, want)
    gu, gu_pat = _coverage(guards, want)
    # A GLOB that would match is not a literal entry, and rsync's anchoring rules make "would
    # this pattern cover this path" a question a string comparison has no business answering.
    # Guessing GREEN manufactures the stale-green this module exists to remove; calling it RED
    # invents a leak that is not there. Both are wrong, so it says so instead.
    if COVERED_PATTERN in (ex, gu):
        return ControlResolution(
            path, BASIS_PATTERN_COVERAGE,
            "covered by the PATTERN %r rather than by a literal entry, and whether that pattern "
            "matches under rsync's anchoring rules is not decidable by string comparison — "
            "confirm by hand or add the literal entry" % (ex_pat or gu_pat,))
    if ex == COVERED_LITERAL and gu == COVERED_LITERAL:
        return ControlResolution(path, BASIS_VERIFIED_BOTH)
    if ex == COVERED_LITERAL:
        return ControlResolution(path, BASIS_EXCLUDE_ONLY)
    if gu == COVERED_LITERAL:
        return ControlResolution(path, BASIS_GUARD_ONLY)
    return ControlResolution(path, BASIS_VERIFIED_NEITHER)


def resolve_all(paths, script, script_path="scripts/sync-public.sh"):
    return tuple(resolve(p, script, script_path) for p in paths)


def read_script(root):
    """The script text, or None — so an unreadable script becomes UNDECIDABLE rather than a
    crash or, worse, an empty read that derives RED for every path."""
    try:
        with open(os.path.join(root, "scripts", "sync-public.sh"), encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None
