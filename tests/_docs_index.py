"""The doc-number space of `docs/build/`, DERIVED — because a field maintained by a reminder goes stale.

0.0.20, filed as `INDEX-NEXT-FREE-IS-MANUAL`. `docs/build/INDEX.md` rule 4 reads *"Never renumber.
Next free number: **N**"*, and that number is typed by whoever last remembered to type it. Its own
footnote records **four** lapses — it read `89` while 89–96 existed, then `98`, then `99` while 100
and 101 existed, then `103` while 104 existed and 105 was being written. On 2026-08-27 it read
`106` while `107` existed: **the fifth.**

⭐ THE FOOTNOTE ALREADY NAMES THE FIX — *"the fix is to derive it, not to remind harder"* — AND THE
FIFTH LAPSE HAPPENED ANYWAY, WITH THE FIX WRITTEN DOWN AND UNBUILT. That is evidence about
prescriptions-without-mechanisms, not about the field. This module is the mechanism.

⛔ AND THE HARM IS NOT HYPOTHETICAL. `collisions()` measures what the stale field actually costs:
the number space already holds numbers claimed by more than one document. A colliding pair is
precisely what rule 4 exists to prevent, so the guard reports the collisions it finds rather than
asserting a count — the count is DERIVED, and it is allowed to move.

THREE STATES FOR THE FIELD, THREE REPRESENTATIONS (§7g)
-------------------------------------------------------
    DECLARED      the field is present and holds an integer
    FIELD_ABSENT  `INDEX.md` exists and carries no such field at all
    UNPARSEABLE   the field is there and its value is not a number we can read

⛔ These must not collapse. "The index does not declare a next-free number" and "the index declares
6" are different facts about the repo, and a reader that returns `None` for both makes the *first*
look like a clean sweep — §7f, a clean result meaning UNGRADABLE. The whole reason this defect
survived five lapses is that nothing distinguished "nobody checked" from "checked and fine".

WHAT IS AND IS NOT DERIVED
---------------------------
The number space is derived from FILENAMES over both `docs/build/` and `docs/build/archive/`,
because rule 4's *"never renumber"* spans the archive — a doc keeps its number when it is archived,
so an archived `104` still claims `104`. Deriving over the live tree alone would report a next-free
that is already taken, which is the exact defect one directory deeper.

⚠ `PAIRED` collisions are recognised, not excused. `89-audit-brief-gr-s6.md` and
`89-mokata-0.0.14-gr-s6-audit.md` share a number ON PURPOSE — a brief and the audit that answers it
are one document in two files. The recogniser keys on the `audit-brief-` prefix and NOTHING ELSE, so
a genuine collision cannot dress itself as a pair by being named suggestively, and `unpaired()` is
what a caller grades.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import re


# ---- the three states of the declared field (§7g) ------------------------------------------------

#: The field is present and holds an integer.
DECLARED = "declared"
#: `INDEX.md` is readable and carries no next-free field at all.
FIELD_ABSENT = "field_absent"
#: The field is present and its value could not be read as a number.
UNPARSEABLE = "unparseable"


# ---- the three verdicts --------------------------------------------------------------------------

#: The declared number is free AND is the lowest free number above the highest claimed one.
CURRENT = "current"
#: The declared number is already claimed by a document. The next author collides.
STALE = "stale"
#: The field could not be read, so the question was not answered. Never green.
UNGRADABLE = "ungradable"


#: The index's rule-4 field. The value is captured separately from the label so a present-but-empty
#: field reads as UNPARSEABLE rather than as absent.
_NEXT_FREE = re.compile(r"Next\s+free\s+number:\s*\*\*(?P<value>[^*]*)\*\*", re.I)

#: A numbered document: the run of digits before the first hyphen. ⛔ THE ANCHORING LIVES IN
#: `.match()` AND NOWHERE ELSE — deliberately not `^` as well. A property defended twice is a
#: property no single mutation can break, and therefore one no mutant can GRADE; the redundancy
#: would read as care and function as a blind spot. `_a15_docs_index_mutants.sh` D03 flips this
#: one seam, and it must go RED.
_NUMBERED = re.compile(r"(?P<number>\d+)-")

#: The one legitimate way two files share a number: an audit brief and the audit that answers it.
_PAIRED_PREFIX = "audit-brief-"


class Field(object):
    """What `INDEX.md` DECLARES, with the state that says how much to trust it."""

    __slots__ = ("state", "value", "raw")

    def __init__(self, state, value=None, raw=None):
        self.state = state
        self.value = value
        self.raw = raw

    def __repr__(self):
        return "Field(%s, value=%r, raw=%r)" % (self.state, self.value, self.raw)


def declared_next_free(text):
    """Read the rule-4 field out of `INDEX.md`'s text. Returns a `Field`, never a bare int-or-None.

    ⛔ The first match wins DELIBERATELY. The index carries prose ABOUT the field further down
    (the drift note quotes an old value), and a reader that took the last match would grade the
    commentary instead of the rule.
    """
    m = _NEXT_FREE.search(text or "")
    if m is None:
        return Field(FIELD_ABSENT)
    raw = m.group("value").strip()
    try:
        return Field(DECLARED, int(raw), raw)
    except ValueError:
        return Field(UNPARSEABLE, None, raw)


def numbered_docs(build_dir):
    """Every numbered markdown doc under `build_dir` and its `archive/`, as (number, relpath).

    ⚠ The archive is INCLUDED. Rule 4 says never renumber, so an archived doc still owns its number
    and a next-free derived over the live tree alone would hand out a number already taken.

    ⛔ `handoff/` is DELIBERATELY NOT INCLUDED, and it is not an oversight. Its files are numbered by
    STAGE within a release — `04-bounds-re-derived.report.md` sits beside `04-mirror-pins.report.md`
    from a different release — so it is a second number space with its own rule, and folding it in
    would report a hundred collisions that are not collisions. ⚠ Nothing derives THAT space; it is
    named here so its absence is a decision rather than a gap a later reader has to guess at.
    """
    out = []
    for sub in ("", "archive"):
        d = os.path.join(build_dir, sub) if sub else build_dir
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not name.endswith(".md"):
                continue
            m = _NUMBERED.match(name)
            if m is None:
                continue
            rel = "%s/%s" % (sub, name) if sub else name
            out.append((int(m.group("number")), rel))
    return tuple(sorted(out))


def claimed_numbers(build_dir):
    """The set of numbers some document already owns."""
    return frozenset(n for n, _rel in numbered_docs(build_dir))


def derive_next_free(build_dir):
    """The lowest number no document claims, at or above the highest claimed one, plus one.

    ⛔ NOT `max + 1` blindly and NOT "the first gap". Gaps in this space are historical — a number
    can be free because a doc was never written — and handing one out would break rule 4's promise
    that a reference by number is stable. The next free number is strictly ABOVE everything claimed.
    """
    claimed = claimed_numbers(build_dir)
    if not claimed:
        return None
    return max(claimed) + 1


class Collision(object):
    """One number claimed by more than one document, and whether the sharing is the sanctioned kind."""

    __slots__ = ("number", "paths", "paired")

    def __init__(self, number, paths, paired):
        self.number = number
        self.paths = tuple(paths)
        self.paired = paired

    def render(self):
        kind = "PAIRED (brief + audit)" if self.paired else "UNPAIRED"
        return "  %3d  %-22s %s" % (self.number, kind, ", ".join(self.paths))


def _basename(rel):
    return rel.rsplit("/", 1)[-1]


def _is_paired(paths):
    """Exactly one of the files is an `audit-brief-*`, and there are exactly two of them.

    The recogniser is deliberately narrow. A number shared by three documents is not a pair however
    they are named, and a brief with no audit beside it is a stray, not a pair.
    """
    if len(paths) != 2:
        return False
    stems = [_basename(p).split("-", 1)[1] if "-" in _basename(p) else "" for p in paths]
    briefs = [s for s in stems if s.startswith(_PAIRED_PREFIX)]
    return len(briefs) == 1


def collisions(build_dir):
    """Every number claimed more than once, PAIRED ones included and labelled."""
    by_number = {}
    for n, rel in numbered_docs(build_dir):
        by_number.setdefault(n, []).append(rel)
    out = []
    for n in sorted(by_number):
        paths = by_number[n]
        if len(paths) < 2:
            continue
        out.append(Collision(n, paths, _is_paired(paths)))
    return tuple(out)


def unpaired(build_dir):
    """The collisions rule 4 exists to prevent — two documents on one number, no brief/audit excuse."""
    return tuple(c for c in collisions(build_dir) if not c.paired)


class Verdict(object):
    """The graded answer, carrying the evidence a refusal needs to survive being read."""

    __slots__ = ("state", "declared", "derived", "field")

    def __init__(self, state, declared=None, derived=None, field=None):
        self.state = state
        self.declared = declared
        self.derived = derived
        self.field = field

    def render(self):
        if self.state == UNGRADABLE:
            return ("INDEX next-free: UNGRADABLE — the field is %s (raw %r). "
                    "This is NOT a pass; the question was not answered."
                    % (self.field.state if self.field else "unreadable",
                       self.field.raw if self.field else None))
        if self.state == STALE:
            return ("INDEX next-free: STALE — INDEX.md declares %s, but %s is already claimed and "
                    "the lowest free number above everything claimed is %s. The next author "
                    "COLLIDES." % (self.declared, self.declared, self.derived))
        return "INDEX next-free: CURRENT — declared %s, derived %s." % (self.declared, self.derived)


def grade(build_dir, index_text):
    """Grade `INDEX.md`'s declared next-free against the number space derived from the tree."""
    field = declared_next_free(index_text)
    derived = derive_next_free(build_dir)
    if field.state != DECLARED or derived is None:
        return Verdict(UNGRADABLE, None, derived, field)
    if field.value < derived:
        return Verdict(STALE, field.value, derived, field)
    return Verdict(CURRENT, field.value, derived, field)
