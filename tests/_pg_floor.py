"""The PostgreSQL-floor drift sweep — a PURE FUNCTION over a SUPPLIED CORPUS of text.

0.0.18 stage 20 (`PG-FLOOR-RATIFIED-NOWHERE-BUT-THE-ADR`, doc 84 §1). ADR-54 V1 moved the team-mode
floor from `>= 14` to `>= 15`, target 17, on 2026-08-03 (`601ca35`). The ratification updated the
ADR box and ONE source comment. Ten other places went on saying `14`, five of them in the wheel and
on the published docs site, and the number they were telling users to stand up reaches upstream end
of life on **2026-11-12**. Nobody was careless: **nothing connected the decision to the places that
state it**, because there was no place to connect them to — the floor was prose in every one of
them, so every one had to be remembered on its own. `teamdb.MIN_PG_MAJOR` is now that place and
this module is the thing that checks nothing has drifted from it.

WHAT THIS GRADES, and the distinction is the whole design:

    A FLOOR CLAIM        prose asserting a MINIMUM — a postgres word, a `>=` or `≥`, and a major.
                         It must state the declared floor exactly. There is one right answer.
                         (The offending forms are not spelled out here on purpose: this file is
                         itself in the corpus, and a sweep that cannot survive its own docstring
                         would have to be exempted from itself to pass.)
    AN INSTANTIATION     a concrete image tag — `postgres:16`, `pgvector/pgvector:pg16`.
                         It must SATISFY the floor. Any tag at or above it is correct, and
                         `docker-compose.team.yml`'s pin is the worked example: the file's
                         contract is "≥ the floor", the tag is one thing that meets it, and
                         grading the tag for equality would red on a perfectly good upgrade.

Collapsing those two is how a sweep produces a false red on a correct pin — and a sweep that reds
on correct code is one a reader learns to route around, which is `SECRET-GUARD-FIRES-ON-BACKLOG-
ROW-IDENTIFIERS` (doc 84) arriving through a different door.

THREE DESIGN CONSTRAINTS, each from something that already went wrong in this repo:

1. **THE DECLARED FLOOR IS READ, NEVER TYPED** (§7j). `declared_floor()` parses
   `src/mokata/teamdb.py` and returns `None` when it cannot find the declaration. It does not
   import the module: an audit that imports the subsystem it audits cannot fail honestly
   (`GATES-COMPLETENESS-UNDERIVED`'s note, reused rather than re-learned), and `teamdb` pulls in
   the memory package. A guard carrying its own copy of `15` would be the eleventh site.

2. **IT TAKES A CORPUS, it does not go and find the repo** (doc 85 §7i, following
   `tests/_mutant_driver_contract.py` rather than re-deriving it). `claims()` is pure over a
   supplied string, so the tests hand it synthetic offenders — and synthetic NON-offenders, which
   matter more here: the two near-misses in this tree are `mutate.sh`'s *"14 characters -> 14
   characters"* beside a `_pg.` symbol and `test_db_s0_dsn_inspect.py`'s `len(value) > 12` beside
   a `postgres://` literal. Both would be false reds under a looser pattern, and a sweep is only
   trustworthy if its NEGATIVES are graded too.

3. **HISTORY IS DENIED BY PATH, NEVER BY VOCABULARY.** `REMOVAL-DRIFT-KEYS-ON-A-WORD` (doc 84) is
   the row one instrument over: a sweep scoped by a keyword reports clean about the sites it
   cannot see, and widening it naively reds on correct historical prose — measured there at 53
   hits, mostly changelog history. So the exclusions below are PATHS with a recorded reason each,
   and `stale_exemptions()` expires any that stops carrying a claim, so the deny-list cannot
   quietly outlive what it was written for.

⚠ **WHAT THIS SWEEP DOES NOT GRADE, declared rather than discovered:**

  * **The internal decision docs.** `docs/build/` is denied wholesale (see below) and that is a
    real hole, not a tidy boundary: doc 85 §1's own headline read `≥14` for thirteen days and
    doc 84's FINALIZED ledger cell read `≥14` for thirteen more, both corrected by hand. Tracked
    as `PG-FLOOR-INTERNAL-LEDGER-UNGUARDED` (doc 84). It is denied here because `docs/build/`
    is excluded from the public mirror, so a SHIPPED test that read those files would be
    `SHIPPED-TEST-READS-INTERNAL-FILE` — the class this repo has now filed three times.
  * **Runtime enforcement.** Nothing here connects to a database. A user pointed at a live PG14
    still gets no refusal; that is `PG-FLOOR-UNENFORCED-AT-CONNECT` (doc 84) and it needs the
    live-DB legs.
"""

import os
import re

# ---- the single source ------------------------------------------------------------------------

#: WHERE THE FLOOR IS DECLARED. One file, one line, parsed as text.
DECLARATION_RELPATH = os.path.join("src", "mokata", "teamdb.py")

_MIN_DECL = re.compile(r"^MIN_PG_MAJOR\s*=\s*(\d+)\s*(?:#.*)?$", re.M)
_TARGET_DECL = re.compile(r"^TARGET_PG_MAJOR\s*=\s*(\d+)\s*(?:#.*)?$", re.M)

# ---- what a floor claim looks like --------------------------------------------------------------
#
# A postgres token and a MINIMUM comparator and a two-digit major, within 25 characters of each
# other, in either order. Every element is load-bearing:
#
#   the comparator is required   — `_pg.get_connection`, 14 characters` (scripts/mutate.sh) has a
#                                  postgres-ish token and a `14` and asserts no minimum at all.
#   `>` alone is NOT a comparator — `len(value) > 12` beside `"postgres://"`
#                                  (test_db_s0_dsn_inspect.py) is a length check, not a floor. Every
#                                  real floor claim in this tree writes `>=` or `≥`.
#   two digits are required      — a bare `pg 9` would be a different era's problem and there is
#                                  none in the tree; matching one digit invites arithmetic.
#   25 characters, not 60        — measured: the widest true claim here is "PostgreSQL ≥ 15, target
#                                  17" and the nearest false one sits at 22. The window is the
#                                  narrowest that keeps every true positive.
_PG = r"(?:postgres(?:ql)?|pg)"
_MIN = r"(?:>=|≥|&ge;)"
_PATTERNS = (
    re.compile(_PG + r".{0,25}?" + _MIN + r"\s*(\d{2})\b", re.I),
    re.compile(_MIN + r"\s*(\d{2})\b.{0,25}?" + _PG, re.I),
)

#: A concrete image tag — `postgres:16`, `pgvector/pgvector:pg16`. Graded for SATISFACTION, not
#: equality (see the module docstring).
_IMAGE_TAG = re.compile(r"\b(?:postgres|pgvector)[:/](?:pgvector:)?(?:pg)?(\d{2})\b", re.I)

# ---- the corpus ---------------------------------------------------------------------------------

# WHY EVERY ENTRY DECLARES WHETHER IT SHIPS, and it is not bookkeeping. This guard SHIPS: it runs
# in the public mirror, where `docs/build/` DOES NOT EXIST. A first cut of the expiry check below
# graded that absence as rot and went red on the mirror while passing in the dev tree — caught by
# running it as SHIPPED rather than as source, which is the one thing this stage's brief insisted
# on. `MIRROR-ABSENCE-READS-AS-MATCH` (doc 84) is the same mistake with the sign flipped: there an
# absent mirror read as a match, here an absent path read as rot. Both come from two states sharing
# one representation (§7g), so there are THREE here:
#
#   INERT   the path is present and no longer carries a floor claim  -> the exemption has rotted
#   ABSENT  the path is not in this checkout at all                  -> ROT in a tree that has the
#           internal partition, UNDECIDABLE in one that does not, and the caller is the only thing
#           that knows which tree it is standing in
#   (live)  present and still carrying a claim                       -> nothing to report

#: `entry -> (reason, ships)`. `ships=False` means "absent from the public mirror BY DESIGN".
#: Directory prefixes; matched against the repo-relative POSIX path, so a whole tree goes at once.
HISTORY_PREFIXES = {
    "docs/build/": (
        "the internal build record — archive/ is provenance-only by CLAUDE.md, handoff/ holds "
        "stage briefs and reports that are true at their timestamp and nothing more, doc 02 is "
        "append-only, and docs 96/101 are DATED ADR re-checks whose whole subject is the floor "
        "that moved. It is also excluded from the public mirror twice over, so a shipped test "
        "reading it would be SHIPPED-TEST-READS-INTERNAL-FILE. The cost is recorded as "
        "PG-FLOOR-INTERNAL-LEDGER-UNGUARDED (doc 84), not swallowed.",
        False,
    ),
}

#: Exact paths that are out of scope. Exact rather than prefixed so that a NEW file beside one of
#: them is in scope by default — the deny-list must not widen by accident.
HISTORY_FILES = {
    "CHANGELOG.md": (
        "dated release entries. The 0.0.11 entry describing what shipped against the superseded "
        "floor WAS TRUE THEN; editing it rewrites the past to flatter the present, which is the "
        "opposite of what a changelog is for. CHANGELOG-HAS-NO-UNRELEASED-SECTION (doc 84, "
        "0.0.18, rides the cut) is where the floor MOVE gets said.",
        True,
    ),
    "docs/changelog.md": (
        "the published mirror of the entries above, and stale at 0.0.14 besides "
        "(DOCS-CHANGELOG-STALE-AT-0-0-14, doc 84). Same reason, same remedy.",
        True,
    ),
}

#: The three bases an exemption can be reported under. `ABSENT` is the one the CALLER must judge.
INERT = "inert"
ABSENT = "absent"


def declared_floor(root):
    """`(min_major, target_major)` read from the declaration, or `None` if it cannot be read.

    `None` rather than a default, because a guard that cannot find the floor has not checked
    anything and must say so — a default would grade the whole corpus against a number nobody
    declared, which is the false green this module exists to remove (doc 85 §7g)."""
    try:
        with open(os.path.join(root, DECLARATION_RELPATH), encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return None
    lo, hi = _MIN_DECL.search(text), _TARGET_DECL.search(text)
    if lo is None or hi is None:
        return None
    return int(lo.group(1)), int(hi.group(1))


def claims(text):
    """Every FLOOR CLAIM in `text`, as `(line_number, major, line)`. Pure; no filesystem.

    One claim per line at most: a line asserting the floor twice asserts it once for a reader."""
    found = []
    for n, line in enumerate(text.split("\n"), 1):
        for pattern in _PATTERNS:
            match = pattern.search(line)
            if match is not None:
                found.append((n, int(match.group(1)), line.strip()))
                break
    return found


def instantiations(text):
    """Every concrete PostgreSQL IMAGE TAG in `text`, as `(line_number, major, line)`. Pure."""
    found = []
    for n, line in enumerate(text.split("\n"), 1):
        match = _IMAGE_TAG.search(line)
        if match is not None:
            found.append((n, int(match.group(1)), line.strip()))
    return found


def in_scope(rel):
    """True when a repo-relative path is graded. The deny-list is consulted here and NOWHERE else,
    so no caller can accidentally apply half of it."""
    if rel in HISTORY_FILES:
        return False
    return not any(rel.startswith(prefix) for prefix in HISTORY_PREFIXES)


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):    # binaries and unreadable paths carry no prose
        return None


def scan(corpus):
    """`(drifted, satisfied_tags, violating_tags)` over a supplied `{rel: abs}` corpus.

    `drifted` is `(rel, line_number, major, line)` for every in-scope FLOOR CLAIM — the CALLER
    compares each `major` against the declared floor, because this module never decides what the
    floor is (it reads it) and never decides what to do about a mismatch (the test does)."""
    drifted, tags = [], []
    for rel in sorted(corpus):
        if not in_scope(rel):
            continue
        text = _read(corpus[rel])
        if text is None:
            continue
        for line_no, major, line in claims(text):
            drifted.append((rel, line_no, major, line))
        for line_no, major, line in instantiations(text):
            tags.append((rel, line_no, major, line))
    return drifted, tags


def stale_exemptions(corpus):
    """Deny-list entries that no longer earn their place, as `(entry, basis, detail)`.

    An exemption outlives the thing it exempted silently: the file is renamed, or its last floor
    claim is edited away, and a permanent hole stays in the corpus with a reason that describes
    nothing. This function REPORTS; it does not judge, because whether `ABSENT` is rot depends on
    which checkout the caller is standing in and this module does not know that (see the note
    above the deny-lists). `INERT` is unconditional."""
    stale = []
    for rel in sorted(HISTORY_FILES):
        if rel not in corpus:
            stale.append((rel, ABSENT, "not a tracked path in this checkout"))
            continue
        text = _read(corpus[rel])
        if not (text and claims(text)):
            stale.append((rel, INERT, "tracked, but carries no floor claim to exempt"))
    for prefix in sorted(HISTORY_PREFIXES):
        under = [r for r in corpus if r.startswith(prefix)]
        if not under:
            stale.append((prefix, ABSENT, "no tracked path under this prefix in this checkout"))
            continue
        if not any(claims(_read(corpus[r]) or "") for r in under):
            stale.append((prefix, INERT, "no path under this prefix carries a floor claim"))
    return stale


# ---- the ACCOUNTING half: for a corpus that legitimately quotes SUPERSEDED floors -------------
#
# C1 (0.0.19), closing `PG-FLOOR-INTERNAL-LEDGER-UNGUARDED`. The sweep above answers "does this
# surface state the wrong floor?" and denies `docs/build/` wholesale, which is a real hole: doc 85
# §1's headline and doc 84's FINALIZED ledger cell each stated a superseded floor for thirteen days,
# and both were documents a stage is told to treat as standing truth.
#
# ⚠ WHY THE OBVIOUS FIX IS THE WRONG ONE, and it was MEASURED rather than reasoned. Pointing the
# sweep above at the living build docs convicts TEN lines, and every one of them is CORRECT: doc 02
# is append-only history, docs 96/101 are DATED ADR re-checks whose whole subject is the floor as it
# stood on the day they were written, and two doc 84 rows are ABOUT the superseded major by name. A
# gate that reds on ten correct sentences and zero defects is one a reader learns to route around —
# `SECRET-GUARD-FIRES-ON-BACKLOG-ROW-IDENTIFIERS` (doc 84) arriving through a different door.
#
# ⚠ AND WHY A PATH DENY-LIST CANNOT EXPRESS IT EITHER. The two REAL failures were in doc 84 and doc
# 85 — the same two files that also hold legitimate quotations. Denying those paths would swallow
# both instances the gate exists to catch. The distinction is not per-FILE, it is per-LINE: a live
# claim about the CURRENT floor versus a dated quotation of a past one, and nothing marks which is
# which (that is `DOC-85-FLOOR-PROVENANCE-UNMARKED`, an open row with a ruling owed).
#
# So the accounting is per-CLAIM and the key is a DISTINCTIVE FRAGMENT of the line, following
# `_deprecation_removal.SRC_RELEASE_EXEMPT` rather than re-deriving it — never a line number, which
# four files have drifted under four consecutive stages. A NEW stale sentence is unaccounted and
# reds; a reworded quotation goes stale LOUDLY instead of keeping its pass.
#
# Both functions are PURE over a supplied corpus AND a supplied register (§7i + §7j): the register
# describes an INTERNAL tree, so it lives with the internal caller, and this module — which SHIPS —
# never learns the name of a path the public mirror does not have.


def unaccounted_claims(corpus, low, register):
    """`(rel, line_no, major, line)` for every claim stating a major OTHER than `low` that no
    entry in `register` accounts for.

    `register` is `{rel: {fragment: reason}}`. `corpus` is `{rel: abs}`; the caller has already
    applied whatever scope it wants, because scope is the caller's question and not this module's
    (the same contract `scan` keeps)."""
    found = []
    for rel in sorted(corpus):
        text = _read(corpus[rel])
        if text is None:
            continue
        fragments = tuple(register.get(rel, {}))
        for line_no, major, line in claims(text):
            if major == low:
                continue
            if any(fragment in line for fragment in fragments):
                continue
            found.append((rel, line_no, major, line))
    return tuple(found)


def stale_accounts(corpus, low, register):
    """`(rel, fragment, basis)` for every register entry that no longer earns its place.

    An accounting entry outlives its subject silently — the file is renamed, or the quotation is
    reworded — and a permanent per-line hole stays behind with a reason that describes nothing.
    The two bases are kept apart for §7g's reason: a path that is GONE and a path that is still
    here and no longer carries the claim are different findings with different remedies."""
    stale = []
    for rel in sorted(register):
        if rel not in corpus:
            stale.append((rel, None, ABSENT))
            continue
        text = _read(corpus[rel]) or ""
        drifting = [line for _n, major, line in claims(text) if major != low]
        for fragment in sorted(register[rel]):
            if not any(fragment in line for line in drifting):
                stale.append((rel, fragment, INERT))
    return tuple(stale)


def internal_entries():
    """The exemptions the public mirror is EXPECTED not to have."""
    return {e for e, (_r, ships) in list(HISTORY_FILES.items()) + list(HISTORY_PREFIXES.items())
            if not ships}
