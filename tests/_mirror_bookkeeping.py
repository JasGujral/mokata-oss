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


# =================================================================================================
# THE PROSE THAT CLAIMS TO ENUMERATE THE CONTROLS — `CLAUDE-MD-SHIPS-LIST-INCOMPLETE`
# =================================================================================================
#
# 0.0.18 stage 8. THE FOURTH CONSUMER of this module (`_public_counts`, `_shipped_reads` and
# `_mutant_driver_contract` are the first three), and deliberately not a new mechanism: the row's
# own remedy is *"DERIVE the list rather than retype it"*, and a second exclude parser beside the
# audited one would be the mistake `_shipped_reads` already refuses by name.
#
# THE ROW'S SENTENCE, because it decides the shape of everything below: **the list is not wrong
# about anything it says; it is wrong about being complete** — and *"the reverse mapping is the
# direction this class leaks"*. A reader who trusts an enumeration reads UNLISTED as PUBLIC. So the
# drift is graded in BOTH directions and the two are DIFFERENT bases, because they are different
# failures (§7g):
#
#   OMITTED      the controls exclude a TRACKED path and the prose does not name it.
#                THE LEAK. Someone adds a file next to it, reads the list, concludes the area is
#                public, and ships it. Two entries were in this state for months.
#   OVERCLAIMED  the prose names a path that is not covered by BOTH controls.
#                THE FALSE REASSURANCE. Nothing leaks today; the document asserts a control that
#                is not there, and the next reader relies on it.
#
# ⚠ TRACKEDNESS, NOT PRESENCE — `_shipped_reads`'s stage-29 lesson, reused rather than re-learned.
# The `--exclude` list holds two kinds of entry: paths the REPO carries (`docs/build/`,
# `scripts/release.sh`) and regenerable junk no checkout has (`__pycache__`, `dist/`, `.DS_Store`,
# `.venv`). Only the first kind can leak, and only the first kind belongs in a prose list about
# what must never ship. Requiring `*.pyc` in `CLAUDE.md` would be inventing an offender. The caller
# therefore supplies `_shipped_reads.tracked_excludes(...)`, which asks the git INDEX — a working
# tree also holds ignored and untracked files, so a disk probe answers differently per machine.
#
# ⚠ `docs/marketing/` IS THE REASON THE TWO DIRECTIONS USE DIFFERENT PREDICATES, and getting this
# wrong would have manufactured a false offender in this stage's own guard. It is gitignored, so it
# is in NO clone and NOT in the tracked-exclude set — yet it IS named by both controls and the
# prose is CORRECT to list it. So OMITTED is judged against the TRACKED excludes and OVERCLAIMED is
# judged against `resolve()`'s both-controls verdict. One predicate for both directions convicts
# `docs/marketing/` of being over-claimed, which it is not.

#: The bullet each list opens with. The prose is a claim about the controls, so the claim's own
#: heading is the anchor — not a line number, which is what three files in this area have moved
#: under three consecutive stages.
NEVER_SHIPS_HEADING = "NEVER ships to public:"
STAYS_PUBLIC_HEADING = "Stays public (do not exclude):"

#: WHERE AN ENUMERATION STOPS. A bullet may carry commentary after its list, and that commentary
#: legitimately names paths — this file, `sync-public.sh`, the test that grades the list. Reading
#: those as list members would make the guard grade its own footnotes and the list unmaintainable.
#: `⚠` is already this repo's universal "the caveat starts here" marker, so it is the delimiter,
#: declared rather than inferred. Everything before the first one is the enumeration.
ENUMERATION_ENDS_AT = "⚠"

_TOP_BULLET = re.compile(r"^- .*?(?=^- |\Z)", re.M | re.S)
_BACKTICKED = re.compile(r"`([^`\n]+)`")


def _bullet(prose, heading):
    """The whole top-level markdown bullet whose text contains `heading`, or None."""
    for match in _TOP_BULLET.finditer(prose):
        if heading in match.group(0):
            return match.group(0)
    return None


def listed_paths(prose, heading):
    """The backticked PATHS the named bullet enumerates, normalised — or None if it is absent.

    None rather than an empty frozenset: "the heading this derivation anchors on is gone" and "the
    list is empty" are different facts, and an empty list would make every excluded path an
    offender while a missing heading means the derivation itself has broken.
    """
    bullet = _bullet(prose, heading)
    if bullet is None:
        return None
    enumeration = bullet.split(ENUMERATION_ENDS_AT, 1)[0]
    return frozenset(
        _norm(token) for token in _BACKTICKED.findall(enumeration)
        if token and " " not in token and ("/" in token or "." in token))


# ---- verdicts for the list ---------------------------------------------------------------------

BASIS_LIST_MATCHES_CONTROLS = "list_matches_controls"
BASIS_LIST_OMITS_AN_EXCLUDED = "list_omits_an_excluded_path"
BASIS_LIST_CLAIMS_AN_UNEXCLUDED = "list_claims_an_unexcluded_path"
BASIS_LIST_DRIFTED_BOTH_WAYS = "list_drifted_both_ways"
BASIS_LIST_ABSENT = "list_absent"
BASIS_NO_TRACKED_EXCLUDES = "no_tracked_excludes"

_LIST_VERDICT_OF = {
    BASIS_LIST_MATCHES_CONTROLS: GREEN,
    BASIS_LIST_OMITS_AN_EXCLUDED: RED,
    BASIS_LIST_CLAIMS_AN_UNEXCLUDED: RED,
    BASIS_LIST_DRIFTED_BOTH_WAYS: RED,
    BASIS_LIST_ABSENT: UNDECIDABLE,
    BASIS_NO_TRACKED_EXCLUDES: UNDECIDABLE,
}

LIST_UNDECIDABLE_BASES = frozenset(
    b for b, v in _LIST_VERDICT_OF.items() if v == UNDECIDABLE)


class ShipsListResolution(object):
    """Whether the prose enumeration still matches the controls, and which way it drifted.

    `verdict` is a PROPERTY of `basis`, as everywhere else in this module. `omitted` and
    `overclaimed` are the EVIDENCE for the basis, never a second opinion about it: the basis is
    computed from them and they are reported so a failure names paths instead of a count.
    """

    __slots__ = ("heading", "basis", "omitted", "overclaimed", "detail")

    def __init__(self, heading, basis, omitted=(), overclaimed=(), detail=""):
        if basis not in _LIST_VERDICT_OF:
            raise ValueError("unknown basis %r" % (basis,))
        if basis in LIST_UNDECIDABLE_BASES and not detail:
            raise ValueError(
                "%s: basis=%r is UNDECIDABLE and carries no reason — a shrug a reader rounds to "
                "green, which is this module's whole subject." % (heading, basis))
        object.__setattr__(self, "heading", heading)
        object.__setattr__(self, "basis", basis)
        object.__setattr__(self, "omitted", tuple(sorted(omitted)))
        object.__setattr__(self, "overclaimed", tuple(sorted(overclaimed)))
        object.__setattr__(self, "detail", detail)

    def __setattr__(self, *_a):                       # pragma: no cover - immutability guard
        raise AttributeError("ShipsListResolution is immutable")

    @property
    def verdict(self):
        return _LIST_VERDICT_OF[self.basis]

    @property
    def decided(self):
        return self.verdict != UNDECIDABLE

    def render(self):
        if self.verdict == GREEN:
            return "GREEN — the prose enumerates exactly what the controls exclude"
        if self.verdict == UNDECIDABLE:
            return "UNKNOWN — %s" % self.detail
        parts = []
        if self.omitted:
            parts.append(
                "OMITTED (the LEAK direction — the controls exclude these tracked paths and the "
                "prose does not name them, so a reader takes unlisted to mean public): %s"
                % ", ".join(self.omitted))
        if self.overclaimed:
            parts.append(
                "OVERCLAIMED (the FALSE-REASSURANCE direction — the prose names these and they "
                "are not covered by both controls): %s" % ", ".join(self.overclaimed))
        return "RED — " + "; ".join(parts)

    def __repr__(self):
        return "<%s %s basis=%s>" % (self.heading, self.verdict.upper(), self.basis)


def resolve_ships_list(prose, script, tracked_excludes,
                       heading=NEVER_SHIPS_HEADING,
                       script_path="scripts/sync-public.sh"):
    """Derive the never-ships list's status. NEVER returns GREEN by default.

    `tracked_excludes` is supplied, not computed, so this stays a pure function over a corpus
    (§7i) and so the ONE reader of the git index in the test tree remains `_shipped_reads`.
    """
    listed = listed_paths(prose, heading)
    if listed is None:
        return ShipsListResolution(
            heading, BASIS_LIST_ABSENT,
            detail="no top-level bullet containing %r was found, so the claim this derivation "
                   "grades is not where it is expected and nothing was compared" % heading)
    if not tracked_excludes:
        return ShipsListResolution(
            heading, BASIS_NO_TRACKED_EXCLUDES,
            detail="the tracked-exclude set is empty, so 'the prose names everything it must' "
                   "would be vacuously true — indistinguishable from a complete list")
    omitted = frozenset(_norm(p) for p in tracked_excludes) - listed
    overclaimed = frozenset(
        p for p in listed if resolve(p, script, script_path).verdict != GREEN)
    if omitted and overclaimed:
        return ShipsListResolution(heading, BASIS_LIST_DRIFTED_BOTH_WAYS, omitted, overclaimed)
    if omitted:
        return ShipsListResolution(heading, BASIS_LIST_OMITS_AN_EXCLUDED, omitted=omitted)
    if overclaimed:
        return ShipsListResolution(
            heading, BASIS_LIST_CLAIMS_AN_UNEXCLUDED, overclaimed=overclaimed)
    return ShipsListResolution(heading, BASIS_LIST_MATCHES_CONTROLS)


def unlisted_public_helpers(prose, executed, heading=STAYS_PUBLIC_HEADING):
    """The `scripts/…` files CI executes that the stays-public bullet does not name.

    The other prose list, and the other half of the same class. A helper `release.yml` runs must be
    excluded by NEITHER control — excluding it breaks every cut in the repo the cut runs in — and
    the document is where that reason lives. Stage 6 added `scripts/check-release-assets.sh` and
    the bullet was not updated, which is this row drifting further while it sat open.

    One direction only, and it is declared: the bullet legitimately names files CI does not run
    (`scripts/directory_listing.py` is there because a shipped TEST imports it), so an entry that
    is listed-but-not-executed is not an offender. Returns None when the bullet is absent.
    """
    listed = listed_paths(prose, heading)
    if listed is None:
        return None
    return frozenset(_norm(path) for path in executed) - listed


def read_claude_md(root):
    """`CLAUDE.md`'s text, or None. Internal: it does not ship, so every caller guards its read."""
    try:
        with open(os.path.join(root, "CLAUDE.md"), encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None
