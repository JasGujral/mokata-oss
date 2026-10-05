"""A published schedule must RESOLVE, not merely appear — THREE states, and an accounted slip.

0.0.19 row B5 (`disclosure-must-resolve`). `release-notes-check` graded whether a promise was still
PRINTED. It never graded whether the promise was still TRUE. It carried the FTS disclosure into
every cut exactly as documented and did not notice that **0.0.19, the release that disclosure names,
had its scope replaced** — so `CHANGELOG.md` and `RELEASE_NOTES.md` shipped at `v0.0.18` and went to
PyPI carrying five commitments against a release that no longer contained four of them, with every
check green. A presence test passed. That is why this module's predicate is **resolves**.

WHAT "RESOLVES" MEANS, AND WHY IT IS NOT "IS PRESENT"
-----------------------------------------------------
A scheduling claim names an item and a release: *"restore full verification in 0.0.19"*. It resolves
when the planning corpus **assigns that item to that release**. The item's name is present in the
corpus either way — what differs between a true claim and a false one is the release the corpus
assigns it to. A presence test cannot see that difference; an assignment lookup is the difference,
and `test_b5_presence_is_not_resolution` holds it open by grading one key that is present in the
corpus under BOTH verdicts.

THE RESOLUTION AXIS: THREE STATES, THREE REPRESENTATIONS (doc 85 §7g)
----------------------------------------------------------------------
    RESOLVES        the corpus assigns the named item to the named release
    UNRESOLVED      the corpus was consulted and does NOT — never green
    NOT_CHECKABLE   the corpus could not be obtained from here — NEITHER green NOR red

⛔ `NOT_CHECKABLE` is the state this row is most likely to lose, and losing it in either direction
is a distinct failure. Rendered as *"the claim is false"* it reds every cut of the public mirror,
where the planning documents do not exist at all; rendered as a **pass** it makes the whole check
vacuous on precisely the tree that ships. It is therefore its own state, it carries its own reason,
and `mokata release-notes-check` exits **2** on it — non-zero, so a caller that has not been taught
what it means reads it as a refusal, which is the safe way round. That exit-code bargain is
`branch_protection`'s DEGRADED pass (exit 3), reused rather than reinvented.

TWO DISPOSITIONS THAT END THE QUESTION BEFORE RESOLUTION IS ASKED
------------------------------------------------------------------
    SPENT       the release named is at or before the version being cut — dated history, not a
                live promise. Reported, never silently pruned: a filtered claim is invisible and
                an invisible population is how a count comes to stand in for an inventory.
    ACCOUNTED   a PUBLISHED commitment, declared with what happened to it and where that was said.
                Renders as DISCLOSED (it slipped, and the slip was published) or HELD (it did not
                move, and a ruling says why).

⭐ WHERE THE BOUNDARY IS DRAWN, AND IT IS THE DESIGN PROBLEM OF THIS ROW
------------------------------------------------------------------------
This module SHIPS. The planning documents DO NOT — the maintainers' planning tree is dropped by
both mirror controls, and a shipped reader of it is `SHIPPED-TEST-READS-INTERNAL-FILE`, filed FOUR
times in this repo. So the resolution logic and the documents cannot live on the same side of the
boundary, and the split is expressed **as a parameter**: every function here takes its corpus as
`{name: text}`, and this file contains no path into an internal tree — not even to probe for one,
which is also what keeps it clear of `tests/test_footer2_src_boundary.py`.

    the shipped artefact  knows HOW to resolve a claim and has no way to OBTAIN the documents
    the internal gate     has the documents and no logic of its own

`scripts/check-tracker-tables.py` — the one mechanical doc gate that never ships, and already the
two-consumer precedent for exactly this — supplies the planning corpus and gets `RESOLVES` /
`UNRESOLVED`. The shipped CLI supplies no corpus and gets `NOT_CHECKABLE`, honestly, in every tree.
ONE reader, two callers: a second resolver beside this one would be the `badge_run` /
`find_active_run` mistake committed inside the stage that exists because a class fix was applied to
one instance.

AN ACCOUNTED SLIP IS NOT A BROKEN PROMISE
------------------------------------------
Some published commitments slipped and were **published as slipped**. The `0.0.19` strings in the
tagged `CHANGELOG.md` / `RELEASE_NOTES.md` are the live example: the work moved, the disclosure went
into the v0.0.18 GitHub release body, and the tagged files were deliberately left verbatim because
renumbering a published commitment is the silent move the whole row exists to forbid. A check that
reds on those has learned the wrong lesson — it has replaced a presence test with a stricter
presence test. And one of those strings did **not** slip: the PostgreSQL floor commitment is bound
to PostgreSQL 14's upstream end-of-life, an external clock, so it stays at the slot it was published
against. `HELD` and `DISCLOSED` are separate states because those are separate facts, and the plan
that ruled on them requires the held one to be ABSENT from the re-scheduled disclosure.

⚠ "WHAT HAPPENED TO THIS PUBLISHED COMMITMENT" IS NOT DERIVABLE FROM THIS TREE, AND THIS MODULE DOES
NOT PRETEND IT IS. The disclosure is a GitHub release body; the hold is a ruling. `_deprecation_removal`
states the same limit about the same kind of fact — *"a removal release is a DECISION; nothing in a
tree records a decision, so it is DECLARED, not derived, and this file does not pretend otherwise"* —
and `PUBLISHED_COMMITMENTS` follows it: a declared table, keyed on a **distinctive fragment** of the
claim rather than on a file or a line number (four files have drifted under four consecutive stages),
carrying what happened, where that was said, and when. It is graded in both directions —
`stale_accounts` reds on an entry that describes no claim, and the stage's tests red on an entry
with an empty fragment, a missing date or venue, a `DISCLOSED` entry that did not actually move, or
a `HELD` entry that did.

⛔ TWO NARROWINGS THE TABLE MUST NEVER LOSE, because between them they are this row's survival:
  1. **It applies ONLY to unkeyed claims.** A claim carrying an item key is resolved by lookup and
     can never be excused by declaration. Widening the table to cover keyed claims is the mutant
     that turns the whole check back into an exemption list.
  2. **It NEVER applies to `NOT_CHECKABLE`.** A slip we could not check is not a slip we disclosed.
     Letting the table answer first would hand the mirror a silent pass through the accounting
     column — the third state collapsed into a pass by the back door.

WHAT THIS DOES NOT GRADE, declared rather than discovered (doc 85 §7j)
-----------------------------------------------------------------------
  * **The vocabulary is DECLARED.** `CLAIM_PATTERNS` is a list of commitment phrasings, not a theory
    of English. A promise phrased outside it is invisible here, so the list is graded: every pattern
    must match a probe, and the live offenders must be found by DERIVATION rather than named.
    ⚠ Measured, and it is why a grep is a starting point and not the answer: the coordinator's
    `"scheduled for"` grep cannot see `RESTORE_ROW` at all — it says *"restore full verification in
    0.0.19"*, which no `scheduled for` pattern matches. `owed-in` exists for that shape.
  * **PAST-TENSE STATEMENTS ARE NOT CLAIMS.** *"fixed in 0.0.12"* records history; only the
    forward-looking phrasings are commitments, which is why `restore` is a claim and `restored` is
    not. A line stating both loses the promise — an under-report, and the direction that cannot
    invent a false green. The offender population is asserted, so an under-report that drops one of
    the live offenders reds.
  * **Comments are invisible in Python; docstrings are excluded.** A claim counts only where it can
    reach a user — a non-docstring string constant. Prose ABOUT a promise is not a promise, which is
    what keeps the population from filling with the tests and docstrings that describe this very
    mechanism.
  * **`tests/` IS OUT OF CORPUS, and it is measured rather than assumed.** A promise is something
    said to a user; a string planted in a test fixture is by construction not one. Grading them
    convicts three files whose strings are mutant sources and DG-7 fixtures — the same trap stage 05
    reported when a grep of 110 turned out to be 105 with five inside fixtures. The corpus builders
    say what they include; `DECLARATION_MODULE` below is the one further exclusion, and it is the
    `_DECLARATION_MODULE` bargain: a table's own declaration cannot be its own subject.
  * **An UNKEYED claim is unresolvable BY CONSTRUCTION.** Resolution is a lookup and a lookup needs
    a key, so a live promise no machine can resolve must be declared — with a venue and a date — or
    it reds. That is fail-closed, and it is what makes a new unaccounted promise in a shipped file
    impossible to add quietly.

Pure/offline; no subprocess, no network, no filesystem access whatsoever — every function is a
function of the text it is handed (doc 85 §7i: a supplied corpus, never a walk that goes and finds
the tree). Deterministic.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Sequence, Tuple

# ---- the resolution axis, and the dispositions that precede it -----------------------------------

#: The planning corpus assigns the named item to the named release.
RESOLVES = "RESOLVES"
#: The corpus WAS consulted and does not assign the item to that release. Never green.
UNRESOLVED = "UNRESOLVED"
#: The corpus could not be obtained from here. Neither green nor red — see the module docstring.
NOT_CHECKABLE = "NOT_CHECKABLE"
#: A published commitment that slipped and was PUBLISHED AS SLIPPED. A pass that says so.
DISCLOSED = "DISCLOSED"
#: A published commitment that did NOT move, held at its slot by a ruling. A pass that says so.
HELD = "HELD"
#: The release named is at or before the version being cut: dated history, not a live promise.
SPENT = "SPENT"
#: A promise phrased RELATIVE to the release — "this release", "the next release". There is no
#: version to resolve against, so this is not UNRESOLVED (which means "we looked and it is not
#: there") — it is unresolvABLE, and it is red.
#:
#: 🔴 `B5-BLIND-TO-VERSIONLESS-RELEASE-CLAIMS` (0.0.21 stage 03), and the row's own argument is the
#: reason this has a disposition of its own rather than reusing UNRESOLVED:
#:
#:   All nine original `CLAIM_PATTERNS` require a version literal, so *"…on Windows **this
#:   release**"* was invisible to the check built to catch exactly this class — `notify.py` IS in
#:   the 262-source corpus, B5 found ZERO claims in it, and the same reader convicted the same
#:   sentence the instant a version literal was added.
#:
#: ⭐ **AND THE VERSIONLESS SHAPE IS THE WORSE OF THE TWO.** A claim naming `0.0.20` is comparable
#: against a plan and can be found FALSE. *"This release"* **re-targets itself at whatever is being
#: cut** — it can never be false, and therefore can never be checked. **A promise that silently
#: re-points is not a weaker claim than a dated one; it is an uncheckable one.**
UNRESOLVABLE = "UNRESOLVABLE"

#: The dispositions a run may treat as satisfied. `NOT_CHECKABLE` is deliberately absent.
PASSING = (RESOLVES, DISCLOSED, HELD, SPENT)

#: The one module excluded from its own corpus: it DECLARES the accounting, and a declaration
#: cannot be its own subject. `_deprecation_removal._DECLARATION_MODULE`'s bargain, same reason.
DECLARATION_MODULE = "src/mokata/disclosure.py"

# Why a claim could not be checked. Two reasons, kept apart because they are different facts with
# different remedies: one is a leg with no planning documents, the other is a claim this module can
# form no lookup key for.
REASON_NO_CORPUS = "no planning corpus was supplied to this leg"
REASON_NO_KEY = "the claim names no resolvable item key"

# The accounting statuses. `HELD` requires the commitment NOT to have moved; `DISCLOSED` requires
# that it did. Graded both ways, so neither can be used to mean the other.
ACCOUNT_DISCLOSED = "DISCLOSED"
ACCOUNT_HELD = "HELD"


# ---- the declared vocabulary ---------------------------------------------------------------------

_VERSION = r"v?(\d+\.\d+\.\d+)"

#: A commitment phrasing followed by a release. DECLARED, graded, deliberately narrow — see the §7j
#: block. Each entry is `(name, pattern)` so a hit can say WHICH phrasing convicted it: an
#: unexplained conviction is one nobody fixes.
CLAIM_PATTERNS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("scheduled-for", re.compile(r"scheduled\s+for\s+\**" + _VERSION, re.I)),
    ("re-homed-to", re.compile(r"re-?homed\s+to\s+\**" + _VERSION, re.I)),
    ("slated-for", re.compile(r"slated\s+for\s+\**" + _VERSION, re.I)),
    ("planned-for", re.compile(r"planned\s+for\s+\**" + _VERSION, re.I)),
    ("deferred-to", re.compile(r"deferred\s+to\s+\**" + _VERSION, re.I)),
    ("displaced-to", re.compile(r"displaced\s+to\s+\**" + _VERSION, re.I)),
    ("re-scheduled-to", re.compile(r"re-?scheduled\s+(?:to|for)\s+\**" + _VERSION, re.I)),
    ("ruled-to", re.compile(r"ruled\s+to\s+\**" + _VERSION, re.I)),
    # ⭐ THE OFFENDER-1 SHAPE, and the reason a "scheduled for" grep is not the population: an
    # INFINITIVE commitment plus `in <release>`. `RESTORE_ROW` says "restore full verification in
    # 0.0.19" and no "scheduled for" grep has ever seen it.
    ("owed-in", re.compile(
        r"\b(?:restore|remove|removal|close|land|enforce|implement|complete|deliver|fix)"
        # ⚠ THE THIRD-PERSON `s` IS NOT COSMETIC. The published floor commitment says "lands in
        # **0.0.19**", and an anchored `\bland\b` cannot see it — one more measured under-report
        # from a pattern that looked complete. Past tense stays excluded by `_PAST`, above.
        r"(?:s|es)?\b"
        r"[^.;|]{0,60}?\bin\s+\**" + _VERSION, re.I)),
)

#: ⭐ RELEASE-RELATIVE PHRASINGS — the shape with no version to resolve against. SEPARATE from
#: `CLAIM_PATTERNS` on purpose: every entry there yields a `target` that is a version literal, and
#: the whole resolution ladder is built on comparing that target against a plan. A pattern here
#: yields a target that is a PHRASE, so folding it in would silently hand `version_tuple` a string
#: like "this release" (§7g: two kinds of target must not share a representation).
#:
#: ⚠ DELIBERATELY NARROW, and the narrowness is the declaration. These are commitment verbs plus a
#: release-relative noun phrase — not every sentence containing the word "release". *"Released in
#: this release"* is past tense and excluded by `_PAST` like any other history.
_RELEASE_RELATIVE = r"(?:this|the\s+next|an?\s+upcoming|a\s+(?:future|later|forthcoming))\s+release"

#: ⭐ THE FORWARD-LOOKING SUBSET, AND THE SPLIT IS A MEASUREMENT RATHER THAN A PREFERENCE.
#:
#: `this release` is as often a FACTUAL SCOPE STATEMENT as a promise, and my first version — which
#: convicted it bare — reddened the live tree on one:
#:
#:     branch_protection.py:290  "NOT OBTAINED — these four assurances were NOT verified for
#:                                this release:"
#:
#: That is a correct degraded-state message measuring the run it is printed in. It does not
#: re-target itself at a future cut, because it is not about a future cut. Convicting it would mean
#: B5 reds on an honest disclosure, which is the §7i trap pointed the wrong way: a guard whose only
#: live offender is a correct line.
#:
#: `the next release` / `an upcoming release` / `a future release` CANNOT be descriptive of the
#: release being cut. A bare occurrence of one of those IS a promise with no version in it, and it
#: is exactly the shape the row calls uncheckable. So the bare pattern covers those, and `this
#: release` is convicted only with a commitment verb (the two patterns above).
#:
#: ⚠ WHAT THAT LEAVES OPEN IS STATED RATHER THAN HIDDEN: a bare-qualifier promise phrased with
#: `this release` and no verb — *"…slower on Windows this release"*, which is the row's own quoted
#: example — is still not caught. Filed as its own row rather than closed by a pattern that reds on
#: correct prose; see doc 84's `B5-BARE-THIS-RELEASE-IS-AMBIGUOUS-BY-CONSTRUCTION`.
_FORWARD_RELATIVE = r"(?:the\s+next|an?\s+upcoming|a\s+(?:future|later|forthcoming))\s+release"

RELATIVE_CLAIM_PATTERNS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("owed-this-release", re.compile(
        r"\b(?:restore|remove|removal|close|land|enforce|implement|complete|deliver|fix|ship|add)"
        r"(?:s|es|d|ed)?\b"
        r"[^.;|]{0,60}?\b(?:in|for|with|by)\s+\**" + _RELEASE_RELATIVE, re.I)),
    ("scheduled-this-release", re.compile(
        r"\b(?:scheduled|slated|planned|deferred|displaced|re-?scheduled|re-?homed|ruled)\s+"
        r"(?:for|to)\s+\**" + _RELEASE_RELATIVE, re.I)),
    # ⚠ THE SHAPE THE ROW ACTUALLY MEASURED, and it has no commitment verb AND no preposition: a
    # bare qualifier riding a statement — *"…slower on Windows **this release**"*. It is a promise
    # because of WHERE it is printed (a shipped release surface) and because it re-targets itself,
    # not because of a verb.
    #
    # ⛔ TWO NARROWINGS, BOTH MEASURED, BOTH ON MY OWN FIRST DRAFTS. (1) It had the preposition
    # `(?:in|for|with|by)` in front, and so did not match the row's own quoted offender —
    # *"on Windows this release"* has no preposition before the phrase. (2) Removing that, it
    # convicted `branch_protection.py:290`'s correct degraded-state message, so the phrase set is
    # now `_FORWARD_RELATIVE` rather than `_RELEASE_RELATIVE` — see that constant. What is left
    # here still needs `_subject_position`, because a CHANGELOG says *"The next release adds X"*
    # too, and the phrase as grammatical SUBJECT is description rather than commitment.
    ("bare-relative-qualifier", re.compile(r"\**" + _FORWARD_RELATIVE, re.I)),
)

# Past tense is history, not a commitment. Applied BEFORE the patterns, so `restored in 0.0.18`
# never reaches `owed-in`. The tense half of the §7j declaration above.
_PAST = re.compile(
    r"\b(?:restored|removed|closed|landed|enforced|implemented|completed|delivered|fixed|shipped)\b",
    re.I)

# A backlog/row identifier: a screaming-kebab token of three or more segments. This is the KEY a
# claim resolves by, and its shape is the repo's existing row-name convention, not a new one.
_ITEM_KEY = re.compile(r"\b[A-Z][A-Z0-9]*(?:-[A-Z0-9]+){2,}\b")


#: Derived from the tuple above rather than typed again — a second list would be the §7f shape, and
#: `resolve`'s short-circuit is the one place that must never disagree with what was matched.
_RELATIVE_PATTERN_NAMES = frozenset(name for name, _p in RELATIVE_CLAIM_PATTERNS)

#: Leading markdown/bullet/emphasis noise a human line starts with, so "position in the sentence"
#: is asked of the PROSE rather than of the markup.
_LEADING_NOISE = re.compile(r"^[\s>*\-#|`\[(\u2022\u26a0\u2b50\u26d4\U0001f534\U0001f7e2\U0001f7e1]*")


def _subject_position(text: str, start: int) -> bool:
    """Is the match at `start` the grammatical SUBJECT of its sentence?

    ⭐ THE ONE DISTINCTION THAT MAKES A BARE `this release` USABLE AS A CLAIM PATTERN. A changelog
    says *"This release adds X"* constantly — the phrase as subject, describing what happened, which
    is not a commitment and must not be convicted. The row's measured offender is the opposite
    position: *"…slower on Windows this release"*, a trailing qualifier that scopes a statement to
    whatever is being cut.

    ⚠ POSITION, NOT PART-OF-SPEECH. Asking "is the next word a verb" needs a lexicon that would
    rot; asking "does the sentence start here" needs only the text. Sentence start means the
    beginning of the line (after markdown noise) or just after a `.`/`:`/`;`/`!`/`?`.
    """
    before = text[:start]
    stripped = _LEADING_NOISE.sub("", before)
    if not stripped.strip():
        return True                         # the phrase opens the line
    tail = stripped.rstrip()
    return bool(tail) and tail[-1] in ".:;!?"


def version_tuple(version: str) -> Tuple[int, ...]:
    """`"0.0.19"` -> `(0, 0, 19)`. Comparison is numeric, so 0.0.9 < 0.0.19 (string order lies)."""
    return tuple(int(part) for part in version.split("."))


# ---- claims ---------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Claim:
    """One scheduling commitment found in a shipped surface. Read-only.

    `where` is a repo-relative POSIX NAME, not a path — it is compared against declarations a human
    spelled with `/` (`_support`'s cause-B note). `key` is None when no item key could be formed,
    which is a real answer and not a missing one: see the §7j block on unkeyed claims.
    """

    where: str
    line: int
    target: str
    pattern: str
    text: str
    key: Optional[str] = None
    #: The version of the `## …` section this claim sits under, when the surface is sectioned by
    #: release (the CHANGELOG). None for an unsectioned surface or a line above the first heading.
    #:
    #: 🔴 0.0.21 stage 03. A DATED claim ages out on its own: `0.0.17 <= cutting` is SPENT, dated
    #: history rather than a live promise. A RELEASE-RELATIVE claim has no version, so nothing in
    #: its text can age it — and the first version of B5's relative patterns therefore reddened the
    #: tree on *"(fix scheduled for the next release)"* sitting in a CHANGELOG section from several
    #: releases ago. ⛔ That promise is history by POSITION, and the only honest way to say so is to
    #: read the position: the CHANGELOG is sectioned by release, and its newest `##` IS the release
    #: being cut (which `test_dg7` enforces). So the section dates the claim exactly as a version
    #: literal dates a dated one.
    section: Optional[str] = None

    @property
    def site(self) -> str:
        return "%s:%d" % (self.where, self.line)

    def render(self) -> str:
        return "%s  %s -> %s  [%s]  %s" % (
            self.site, self.key or "(no item key)", self.target, self.pattern, _squeeze(self.text))


def _squeeze(text: str, width: int = 96) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= width else flat[:width - 1] + "…"


def _claims_in_text(where: str, line: int, text: str) -> List[Claim]:
    """Every claim in one markdown line or one python string constant."""
    if _PAST.search(text):
        return []
    key_match = _ITEM_KEY.search(text)
    key = key_match.group(0) if key_match else None
    found: List[Claim] = []
    for name, pattern in CLAIM_PATTERNS:
        for match in pattern.finditer(text):
            found.append(Claim(where=where, line=line, target=match.group(1),
                               pattern=name, text=text, key=key))
    # 🔴 B5-BLIND-TO-VERSIONLESS-RELEASE-CLAIMS. A release-relative promise carries no version, so
    # its `target` is the PHRASE and `resolve` short-circuits it to UNRESOLVABLE before any version
    # arithmetic can be attempted on it.
    #
    # ⚠ ONLY WHERE NO DATED CLAIM WAS FOUND ON THE SAME LINE. *"deferred to 0.0.22 in a future
    # release"* is odd prose, but the dated half IS resolvable and is the stronger claim; reporting
    # both would convict one sentence twice and the fix for the pair is the same edit.
    if not found:
        for name, pattern in RELATIVE_CLAIM_PATTERNS:
            hit = None
            for match in pattern.finditer(text):
                # The bare qualifier is the only one that can land on a SUBJECT, because it is the
                # only one with nothing in front of it. The other two carry a verb or a
                # preposition, which already places them mid-sentence.
                if name == "bare-relative-qualifier" and _subject_position(text, match.start()):
                    continue
                hit = match
                break
            if hit is not None:
                found.append(Claim(where=where, line=line,
                                   target=" ".join(hit.group(0).split()).lstrip("*"),
                                   pattern=name, text=text, key=key))
                break               # one verdict per line: the phrasings overlap by design
    return found


def printable_constants(source: str) -> Tuple[Tuple[int, str], ...]:
    """Every NON-DOCSTRING string constant in a python module, as `((line, value), …)`.

    Docstrings are excluded and comments never reach the AST at all — the §7j reason, stated once:
    prose about a promise is not a promise. `test_b5_disclosure_must_resolve` holds this reader to
    `_deprecation_removal._string_constants` over the whole shipped corpus, so the tree's two
    answers to "which strings in this module can reach a user" cannot diverge (doc 85 §7f) without
    a refactor this stage was not asked to make.
    """
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            docstrings.add(id(body[0].value))
    return tuple((node.lineno, node.value) for node in ast.walk(tree)
                 if isinstance(node, ast.Constant) and isinstance(node.value, str)
                 and id(node) not in docstrings)


_SECTION_HEADING = re.compile(r"^##\s+\**\[?v?(\d+\.\d+\.\d+)", re.M)


def section_of_line(text: str) -> Tuple[Optional[str], ...]:
    """For each 1-indexed line of `text`, the version of the `## …` section it sits under.

    Returns a tuple whose index 0 is unused (`None`) so `result[line]` reads naturally against a
    1-indexed line number — the same indexing every `Claim` already carries.

    ⚠ PURE, AND OVER THE SUPPLIED TEXT (§7i). A heading this reader does not recognise leaves its
    lines on the PREVIOUS section rather than on None: a changelog whose format drifts should
    degrade towards "dated by the section above", not towards "undatable", because the second
    silently turns historical prose into live promises."""
    out: List[Optional[str]] = [None]
    current: Optional[str] = None
    for raw in text.splitlines():
        match = _SECTION_HEADING.match(raw)
        if match:
            current = match.group(1)
        out.append(current)
    return tuple(out)


def _prose_claims(where: str, text: str) -> List[Claim]:
    """Claims in wrapped markdown prose — read over a TWO-LINE window as well as line by line.

    ⚠ MEASURED, AND IT IS AN UNDER-REPORT THAT LOOKS LIKE A CLEAN POPULATION. The published
    PostgreSQL-floor commitment reads *"Enforcement — warn before 2026-11-12, refuse after — lands
    in"* / *"**0.0.19**"*: the phrasing ends one line and the release begins the next, because
    markdown wraps. A line-by-line reader sees a verb with no version and a version with no verb,
    finds nothing, and reports a population short by exactly the claims a human editor happened to
    wrap — the same class of silent shortfall as counting a grep. Two of the seven live published
    commitments in the tagged files are only visible this way.

    ⛔ THE WINDOW MUST NOT DOUBLE-COUNT, and the first draft of it did: every claim that fits on one
    line was reported twice, once from its own line and once from the window that begins on the line
    ABOVE — a population of 20 for 10 claims, which is an over-report wearing the same clothes as
    the under-report it fixed. A window claim is emitted only when neither line carries it alone,
    and it is attributed to the line the RELEASE fell on, so a site is where a reader will find it.
    """
    lines = text.splitlines()
    found: List[Claim] = []
    per_line: List[set] = []
    for idx, raw in enumerate(lines):
        claims = _claims_in_text(where, idx + 1, raw)
        per_line.append({(c.target, c.pattern) for c in claims})
        found.extend(claims)
    for idx in range(len(lines) - 1):
        joined = lines[idx] + " " + lines[idx + 1]
        if _PAST.search(joined):
            continue
        key_match = _ITEM_KEY.search(joined)
        key = key_match.group(0) if key_match else None
        for name, pattern in CLAIM_PATTERNS:
            for match in pattern.finditer(joined):
                signature = (match.group(1), name)
                if signature in per_line[idx] or signature in per_line[idx + 1]:
                    continue
                spans_the_wrap = match.end() > len(lines[idx]) + 1
                found.append(Claim(where=where, line=idx + 2 if spans_the_wrap else idx + 1,
                                   target=match.group(1), pattern=name, text=joined, key=key))
    return found


def scheduling_claims(sources: Dict[str, Optional[str]]) -> Tuple[Claim, ...]:
    """Every scheduling claim in a SUPPLIED corpus of shipped surfaces (§7i).

    `sources` is `{repo-relative POSIX name: text}`. A `.py` name is read through
    `printable_constants`; anything else is read line by line. `DECLARATION_MODULE` is skipped for
    the reason declared beside it. A `.py` file that will not parse is skipped here and reported by
    `unparseable`, never silently counted clean.
    """
    found: List[Claim] = []
    for where in sorted(sources):
        if where == DECLARATION_MODULE:
            continue
        text = sources[where]
        if text is None:
            continue
        if where.endswith(".py"):
            try:
                constants = printable_constants(text)
            except SyntaxError:
                continue
            for line, value in constants:
                found.extend(_claims_in_text(where, line, value))
        else:
            prose = _prose_claims(where, text)
            # The section is attached HERE rather than inside `_prose_claims`, because it is a
            # property of the FILE's shape and that function reads a two-line window with its own
            # line arithmetic. One reader of the headings, one place it is applied.
            sections = section_of_line(text)
            found.extend(
                [replace(c, section=(sections[c.line] if 0 < c.line < len(sections) else None))
                 for c in prose])
    return tuple(found)


def unparseable(sources: Dict[str, Optional[str]]) -> Tuple[Tuple[str, str], ...]:
    """The `.py` names `scheduling_claims` could not read or parse. Reported, never rounded to 0."""
    bad: List[Tuple[str, str]] = []
    for where in sorted(sources):
        if not where.endswith(".py") or where == DECLARATION_MODULE:
            continue
        text = sources[where]
        if text is None:
            bad.append((where, "unreadable"))
            continue
        try:
            ast.parse(text)
        except SyntaxError as exc:
            bad.append((where, "unparseable: %s" % (exc,)))
    return tuple(bad)


# ---- the planning corpus: which release owns which item -------------------------------------------

@dataclass(frozen=True)
class Assignment:
    """Where the planning corpus puts one item. Read-only."""

    key: str
    release: str
    where: str
    line: int

    def render(self) -> str:
        return "%s -> %s (%s:%d)" % (self.key, self.release, self.where, self.line)


def plan_assignments(plan_sources: Dict[str, Optional[str]]) -> Dict[str, Tuple[Assignment, ...]]:
    """`{item key: (assignment, …)}` read off the planning corpus's own tables.

    ⭐ THE ROW'S IDENTITY IS ITS FIRST CELL, NOT ITS TEXT, and that single decision is what makes
    this a resolution rather than a presence test. `BRANCH-PROTECTION-DEGRADED-PASS` appears in the
    backlog TWICE: once as the row that owns the work, and once inside the prose of a DIFFERENT row
    that quotes the offending constant. Those two rows carry different targets. A reader that
    accepted "the key appears in a row targeted at X" would resolve the false claim against the row
    filed to complain about it — a false green produced by the exact substitution this row exists to
    kill. Keying on the first cell disambiguates exactly.

    The release is read from the column the table's own header calls `Target`; a table without that
    header contributes nothing rather than guessing a column position. Rows are GFM-split, the same
    renderer `check-tracker-tables.py` grades these tables against.
    """
    index: Dict[str, List[Assignment]] = {}
    for where in sorted(plan_sources):
        text = plan_sources[where]
        if not text:
            continue
        target_col: Optional[int] = None
        for line, raw in enumerate(text.splitlines(), start=1):
            stripped = raw.strip()
            if not stripped.startswith("|"):
                # A table ends at the first non-row line, so a `Target` header cannot leak its
                # column index into the NEXT table, whose columns may be in another order.
                target_col = None
                continue
            cells = _split_row(stripped)
            lowered = [c.strip().strip("*`_ ").lower() for c in cells]
            if "target" in lowered and ("row" in lowered or "id" in lowered):
                target_col = lowered.index("target")
                continue
            if target_col is None or target_col >= len(cells):
                continue
            key_match = _ITEM_KEY.match(cells[0].strip().strip("*`_ "))
            if not key_match:
                continue
            version = re.search(_VERSION, cells[target_col])
            if not version:
                continue
            key = key_match.group(0)
            index.setdefault(key, []).append(
                Assignment(key=key, release=version.group(1), where=where, line=line))
    return {key: tuple(values) for key, values in index.items()}


def _split_row(row: str) -> List[str]:
    """GFM cell split: cells split BEFORE inline parsing, so an escaped `\\|` is not a break.

    Same rule `check-tracker-tables.py` grades these tables by, because GitHub is where they are
    read. A lenient renderer that keeps pipes inside code spans merges two cells here and puts the
    wrong column under `Target`.
    """
    body = row.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    cells: List[str] = []
    buf: List[str] = []
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body) and body[i + 1] == "|":
            buf.append("|")
            i += 2
            continue
        if ch == "|":
            cells.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    cells.append("".join(buf))
    return cells


# ---- published commitments: DECLARED, dated, graded in both directions ----------------------------

@dataclass(frozen=True)
class PublishedCommitment:
    """One commitment already published in a tagged file, and what became of it.

    `fragment` is a distinctive piece of the claim's own text — not a file, not a line number, not
    the whole string. A rewording that changes WHAT IS CLAIMED goes stale and reds; a rewording
    elsewhere on the line does not. `venue` is where a reader finds the disclosure or the ruling,
    and it is a PUBLIC artefact on purpose: an entry pointing into the maintainers' tree would be a
    dangling reference for every reader of the mirror.
    """

    fragment: str
    promised_for: str
    now_lands: str
    status: str
    on: str
    venue: str
    #: WHAT the commitment is about, in the words a reader would look for — `"FTS"`, `"PR gate"`,
    #: `"PostgreSQL"`. Added at 0.0.19 stage 12 because the release's own exit criterion asks a
    #: question the rest of this row cannot answer: *is every item that SLIPPED named in the notes'
    #: "Re-scheduled" section, and is the one that HELD absent from it?* Every other field describes
    #: what became of a commitment; none of them says which commitment it is, and an omission cannot
    #: be detected from the text of the bullet that is missing. Graded: an entry with no subject
    #: reds, and so does a subject that names nothing in the corpus.
    subject: str = ""

    @property
    def moved(self) -> bool:
        return self.now_lands != self.promised_for

    def render(self) -> str:
        if self.status == ACCOUNT_HELD:
            return ("%r stays at %s — held %s by %s"
                    % (self.fragment, self.promised_for, self.on, self.venue))
        return ("%r promised for %s, now lands %s — disclosed %s in %s"
                % (self.fragment, self.promised_for, self.now_lands, self.on, self.venue))


#: The published commitments this tree still PRINTS, and what became of each.
#:
#: ⚠ THIS TABLE IS A FUNCTION OF THE TREE, AND THE CUT REWRITES THE TREE. It was declared at 0.0.19
#: stage 06 against the nine `0.0.19` references in the tagged v0.0.18 files — five in
#: `RELEASE_NOTES.md` and four in `CHANGELOG.md`. `RELEASE_NOTES.md` is rewritten WHOLESALE at every
#: cut, so the five notes-side entries described no claim the moment the 0.0.19 notes were written
#: and went STALE, which is `stale_accounts` doing its job rather than a fault in it. They are
#: DELETED here, not carried: an entry that grants a pass to nothing is the shape this row exists to
#: forbid, and pre-1.0 mokata deprecates nothing. The published v0.0.18 text is untouched at its tag.
#: The four CHANGELOG-side entries survive, because a changelog entry is history and is never
#: rewritten — which is also why every one of them is now SPENT at a 0.0.19 cut and the accounting
#: is consulted for none of them. They stay because `stale_accounts` grades in both directions.
#:
#: ⭐ AND THE TABLE GAINS THE PROMISES THIS CUT MAKES. The 0.0.19 notes re-schedule two Class-C
#: items to 0.0.20. Those are LIVE, UNKEYED claims — no planning row carries a resolvable key for
#: either — so without a declaration they are UNRESOLVED and red, which is exactly the fail-closed
#: behaviour the module docstring describes: a live promise no lookup can resolve must be declared,
#: with a venue and a date, or corrected.
#:
#: ⚠ THEY ARE DECLARED `HELD`, AND THAT IS THE CLOSEST TRUE STATUS RATHER THAN AN EXACT ONE.
#: `account_for` matches on `promised_for == claim.target`, so an entry can only ever describe what
#: became of the release the text NAMES — and what these texts name is 0.0.20, which they have not
#: moved from. A promise being made for the first time and a promise held at a slot it was already
#: published against share one representation here, which is §7g at the accounting layer. Filed for
#: 0.0.20 rather than papered over; the alternative today would be to declare a slip that has not
#: happened, and `test_b5_a_disclosed_entry_moved_and_a_held_entry_did_not` is right to refuse it.
PUBLISHED_COMMITMENTS: Tuple[PublishedCommitment, ...] = (
    PublishedCommitment(
        fragment="REFUSE after it — is scheduled for **0.0.19**",
        subject="PostgreSQL",
        promised_for="0.0.19", now_lands="0.0.19", status=ACCOUNT_HELD, on="2026-08-19",
        venue="the PostgreSQL 14 upstream EOL date of 2026-11-12, which is not ours to move"),
    PublishedCommitment(
        fragment="the work is re-homed to **0.0.19**",
        subject="PR gate",
        promised_for="0.0.19", now_lands="0.0.20", status=ACCOUNT_DISCLOSED, on="2026-08-19",
        venue='the v0.0.18 GitHub release body, "Re-scheduled" section'),
    PublishedCommitment(
        fragment="**Now scheduled for 0.0.19**",
        subject="FTS",
        promised_for="0.0.19", now_lands="0.0.20", status=ACCOUNT_DISCLOSED, on="2026-08-19",
        venue='the v0.0.18 GitHub release body, "Re-scheduled" section'),
    PublishedCommitment(
        fragment="scheduled for 0.0.19**, with the rest of the ranking work",
        subject="FTS",
        promised_for="0.0.19", now_lands="0.0.20", status=ACCOUNT_DISCLOSED, on="2026-08-19",
        venue='the v0.0.18 GitHub release body, "Re-scheduled" section'),
    # --- the two promises the v0.0.19 cut makes (0.0.19 stage 12) --------------------------------
    PublishedCommitment(
        fragment="The rank-preserving repair is re-scheduled to 0.0.20",
        subject="FTS",
        promised_for="0.0.20", now_lands="0.0.20", status=ACCOUNT_HELD, on="2026-08-23",
        venue='the v0.0.19 release notes and CHANGELOG, "Re-scheduled" section'),
    PublishedCommitment(
        fragment="the sub-10-minute PR gate is re-scheduled to 0.0.20",
        subject="PR gate",
        promised_for="0.0.20", now_lands="0.0.21", status=ACCOUNT_DISCLOSED, on="2026-08-27",
        venue='the v0.0.20 release notes and CHANGELOG, "Re-scheduled" section'),
    # --- the one promise the v0.0.20 cut makes (0.0.20 release tail) ----------------------------
    #: ⚠ THE THIRD MOVE OF THE SAME ITEM, AND IT IS DECLARED RATHER THAN RENUMBERED. The row above
    #: records that the 0.0.20 slot did NOT hold it; this row is the new promise the 0.0.20 notes
    #: PRINT, and without it that line is a live, unkeyed claim — UNRESOLVED and red, which is the
    #: fail-closed behaviour this module exists for. It is `HELD` for the same reason the 0.0.19
    #: pair were: `account_for` matches on `promised_for == claim.target`, and what this text names
    #: is 0.0.21, which it has not yet moved from. §7g at the accounting layer, filed and unfixed.
    #:
    #: 🔴 AND AT THE 0.0.21 CUT IT DID NOT HOLD — the fourth release in a row. Re-declared rather than
    #: left `HELD`: a held entry for a slot that shipped without the item is a false account, and
    #: `test_cut_rescheduled_section` is what reads this table to decide whether the notes' section
    #: is complete. `now_lands` is `"unscheduled"` ON PURPOSE and it is not a placeholder: the v0.0.21
    #: notes name no new release for the PR gate, because a target nobody has booked work against is
    #: not a schedule — and printing one here would make this table the place a promise was invented.
    PublishedCommitment(
        fragment="the sub-10-minute PR gate is re-scheduled to 0.0.21",
        subject="PR gate",
        promised_for="0.0.21", now_lands="unscheduled", status=ACCOUNT_DISCLOSED, on="2026-10-05",
        venue='the v0.0.21 release notes and CHANGELOG, "Re-scheduled" section'),
)


def account_for(claim: Claim,
                accounted: Sequence[PublishedCommitment] = PUBLISHED_COMMITMENTS
                ) -> Optional[PublishedCommitment]:
    """The accounting entry covering `claim`, or None.

    ⛔ KEYED CLAIMS ARE NEVER ACCOUNTED. A claim that names an item key is resolved by lookup, and
    letting a declaration excuse it is narrowing #1 in the module docstring — the mutant that turns
    this check back into the exemption list it replaced. Matched on the fragment and the release
    together, never on the file.
    """
    if claim.key is not None:
        return None
    for entry in accounted:
        if entry.promised_for != claim.target:
            continue
        if entry.fragment and entry.fragment in claim.text:
            return entry
    return None


def stale_accounts(claims: Sequence[Claim],
                   accounted: Sequence[PublishedCommitment] = PUBLISHED_COMMITMENTS
                   ) -> Tuple[PublishedCommitment, ...]:
    """Accounting entries that describe no claim — the exactness half (§7j).

    An entry whose string was reworded keeps granting a pass to nothing while the reworded claim
    goes unguarded, and without this nothing would ever say so.
    """
    return tuple(entry for entry in accounted
                 if not any(entry.fragment and entry.fragment in claim.text
                            and entry.promised_for == claim.target and claim.key is None
                            for claim in claims))


# ---- resolution -----------------------------------------------------------------------------------

@dataclass(frozen=True)
class Verdict:
    """One claim's disposition, with the evidence that produced it. Read-only."""

    claim: Claim
    state: str
    detail: str = ""

    @property
    def passing(self) -> bool:
        return self.state in PASSING

    def render(self) -> str:
        mark = ("✗ " if self.state in (UNRESOLVED, UNRESOLVABLE)
                else ("? " if self.state == NOT_CHECKABLE else "  "))
        return "  %s%-13s %s\n      %s" % (mark, self.state, self.claim.render(), self.detail)


def resolve(claim: Claim,
            plan_index: Optional[Dict[str, Tuple[Assignment, ...]]],
            cutting: Optional[str] = None,
            accounted: Sequence[PublishedCommitment] = PUBLISHED_COMMITMENTS) -> Verdict:
    """One claim's disposition. `plan_index=None` means the corpus could not be obtained HERE.

    ⛔ ORDER IS LOAD-BEARING AND IT IS THE MUTANT THIS FUNCTION DIES OF. `NOT_CHECKABLE` is decided
    BEFORE the planning lookup and AFTER the accounting table can no longer apply, because a slip we
    could not check is not a slip we disclosed. `cutting` is the version being released: a claim
    naming a release at or before it is SPENT — dated history — and the arithmetic is against the
    version being cut rather than against a hand-typed list of dead releases (`removal_state`'s
    shape, one file over).
    """
    # 🔴 FIRST, AND BEFORE ANY VERSION ARITHMETIC. A release-relative claim's `target` is a PHRASE,
    # so `version_tuple` would raise on it — and more importantly there is nothing to compare it
    # against. ⛔ The honest verdict is UNRESOLVABLE, never ABSENT: the check did not fail to find
    # a plan entry, there IS no question a plan could answer. A promise that re-points at whatever
    # is being cut can never be false, and a claim that can never be false can never be checked.
    if claim.pattern in _RELATIVE_PATTERN_NAMES:
        # ⭐ DATED BY POSITION. A relative promise carries no version, so `SPENT` cannot be reached
        # through `claim.target` — but the CHANGELOG is sectioned by release, and a promise printed
        # under 0.0.16's heading was answered four releases ago. Without this, every historical
        # *"fix scheduled for the next release"* in the changelog reds forever and the only way to
        # go green is to EDIT HISTORY, which this project refuses everywhere else.
        # ⛔ STRICTLY OLDER, AND THE ASYMMETRY WITH THE DATED LADDER IS DELIBERATE. A dated claim
        # NAMING the version being cut is SPENT: its content is landing now. A claim sitting UNDER
        # that version's heading is the opposite — that section IS the release being cut, so a
        # relative promise printed there is live and uncheckable. `<=` here made the live case
        # SPENT, which is the fail-open direction and is what the first draft did.
        if cutting and claim.section and version_tuple(claim.section) < version_tuple(cutting):
            return Verdict(claim, SPENT,
                           "this promise is release-relative and therefore undatable from its own "
                           "text — but it sits under the %s section, which is OLDER than the "
                           "version being cut (%s). It is a record of what %s promised, not a live "
                           "commitment. ⚠ A relative promise in the NEWEST section is a different "
                           "answer: that section IS the release being cut."
                           % (claim.section, cutting, claim.section))
        return Verdict(claim, UNRESOLVABLE,
                       "this promise is phrased relative to the release (%r), so it carries no "
                       "version to resolve against and re-targets itself at whatever is being cut. "
                       "It can never be false, and therefore can never be checked — which makes it "
                       "WORSE than a dated promise that slipped, not better. Name the release it "
                       "is owed in, or delete the commitment." % claim.target)

    if cutting and version_tuple(claim.target) <= version_tuple(cutting):
        return Verdict(claim, SPENT,
                       "%s is at or before the version being cut (%s), so this records what was "
                       "promised rather than what is owed. Whether it was KEPT is the changelog's "
                       "known-limitations disclosure, not this check."
                       % (claim.target, cutting))

    entry = account_for(claim, accounted)
    if entry is not None:
        return Verdict(claim, DISCLOSED if entry.status == ACCOUNT_DISCLOSED else HELD,
                       entry.render())

    if claim.key is None:
        return Verdict(claim, UNRESOLVED,
                       "%s, and no accounting entry covers it. A live promise no lookup can resolve "
                       "must be declared — with what became of it, where that was published and "
                       "when — or corrected." % REASON_NO_KEY)

    if plan_index is None:
        return Verdict(claim, NOT_CHECKABLE,
                       "%s, so whether %s still owns %r was NOT decided here. This is not a pass: "
                       "the resolving gate runs where the planning corpus exists."
                       % (REASON_NO_CORPUS, claim.target, claim.key))

    assignments = plan_index.get(claim.key, ())
    if not assignments:
        return Verdict(claim, UNRESOLVED,
                       "the planning corpus was consulted and knows no item %r at all, so this "
                       "release promise is owned by nothing." % claim.key)
    releases = sorted({a.release for a in assignments})
    if claim.target in releases:
        return Verdict(claim, RESOLVES, "assigned there by " + "; ".join(
            a.render() for a in assignments if a.release == claim.target))
    return Verdict(claim, UNRESOLVED,
                   "the planning corpus assigns %r to %s, not %s — the promise names a release that "
                   "does not carry it: %s"
                   % (claim.key, ", ".join(releases), claim.target,
                      "; ".join(a.render() for a in assignments)))


# ---- the report ------------------------------------------------------------------------------------

@dataclass
class DisclosureReport:
    """What this tree promises, what the corpus confirms, and what could not be decided here.

    The pairing of `corpus_supplied` with the verdicts is what stops `NOT_CHECKABLE` reading as a
    pass: a caller asserts BOTH sides of it, never one (stage 05's census model).
    """

    verdicts: Tuple[Verdict, ...] = ()
    corpus_supplied: bool = False
    cutting: Optional[str] = None
    stale: Tuple[PublishedCommitment, ...] = ()
    unparseable: Tuple[Tuple[str, str], ...] = field(default_factory=tuple)

    def of(self, state: str) -> Tuple[Verdict, ...]:
        return tuple(v for v in self.verdicts if v.state == state)

    @property
    def unresolved(self) -> Tuple[Verdict, ...]:
        return self.of(UNRESOLVED)

    @property
    def not_checkable(self) -> Tuple[Verdict, ...]:
        return self.of(NOT_CHECKABLE)

    @property
    def unresolvable(self) -> Tuple[Verdict, ...]:
        """Release-relative promises — B5's versionless shape. Red, and its OWN bucket.

        ⛔ NOT folded into `unresolved`, even though both are red. `UNRESOLVED` means *we looked
        and the plan does not say that*; `UNRESOLVABLE` means *there is no question a plan could
        answer*. Same colour, different remedies: one is fixed by correcting the plan or the
        version, the other only by naming a release or deleting the commitment. Two facts, two
        representations (§7g)."""
        return self.of(UNRESOLVABLE)

    @property
    def failed(self) -> bool:
        return (bool(self.unresolved) or bool(self.unresolvable)
                or bool(self.stale) or bool(self.unparseable))

    @property
    def undecided(self) -> bool:
        """Something was left for the resolving gate. NOT a failure, and NOT a pass."""
        return bool(self.not_checkable)

    def render(self) -> str:
        if self.failed:
            head = "disclosure-resolves FAIL"
        elif self.undecided:
            head = "disclosure-resolves NOT CHECKABLE HERE"
        else:
            head = "disclosure-resolves PASS"
        lines = ["%s — %d scheduling claim(s) in shipped surfaces, cutting %s, planning corpus %s"
                 % (head, len(self.verdicts), self.cutting or "(unset)",
                    "supplied" if self.corpus_supplied else "ABSENT")]
        for state in (UNRESOLVED, NOT_CHECKABLE, HELD, DISCLOSED, RESOLVES, SPENT):
            group = self.of(state)
            if group:
                lines.append("  %s: %d" % (state, len(group)))
                lines.extend(v.render() for v in group)
        for entry in self.stale:
            lines.append("  ✗ STALE accounting entry describes no claim: " + entry.render())
        for where, why in self.unparseable:
            lines.append("  ✗ %s could not be read, so it was NOT graded: %s" % (where, why))
        if self.failed:
            lines.append("  remedy: correct the release the claim names, or — if the commitment was "
                         "published — leave the published text alone and declare what became of it, "
                         "with the venue the disclosure appeared in and its date.")
        elif self.undecided:
            lines.append("  this leg cannot obtain the planning corpus and says so rather than "
                         "passing. The resolving gate is where that verdict is made.")
        return "\n".join(lines)


def check_disclosure_resolves(
        sources: Dict[str, Optional[str]],
        plan_sources: Optional[Dict[str, Optional[str]]] = None,
        cutting: Optional[str] = None,
        accounted: Sequence[PublishedCommitment] = PUBLISHED_COMMITMENTS) -> DisclosureReport:
    """Grade every scheduling claim in `sources`. `plan_sources=None` ⇒ nothing was obtainable.

    ⚠ AN EMPTY `plan_sources` DICT IS NOT `None` AND MUST NOT BECOME IT: `{}` is a corpus that was
    supplied and contains no assignments, which reds; `None` is a corpus that could not be obtained,
    which is `NOT_CHECKABLE`. Collapsing them makes the mirror red on every keyed claim, which is
    the other half of the third state being lost.
    """
    claims = scheduling_claims(sources)
    index = None if plan_sources is None else plan_assignments(plan_sources)
    return DisclosureReport(
        verdicts=tuple(resolve(claim, index, cutting, accounted) for claim in claims),
        corpus_supplied=plan_sources is not None,
        cutting=cutting,
        stale=stale_accounts(claims, accounted),
        unparseable=unparseable(sources))


__all__ = [
    "RESOLVES", "UNRESOLVED", "NOT_CHECKABLE", "DISCLOSED", "HELD", "SPENT", "PASSING",
    "ACCOUNT_DISCLOSED", "ACCOUNT_HELD", "DECLARATION_MODULE",
    "REASON_NO_CORPUS", "REASON_NO_KEY", "CLAIM_PATTERNS",
    "UNRESOLVABLE", "RELATIVE_CLAIM_PATTERNS",
    "Claim", "Assignment", "PublishedCommitment", "Verdict", "DisclosureReport",
    "PUBLISHED_COMMITMENTS",
    "version_tuple", "printable_constants", "scheduling_claims", "unparseable",
    "plan_assignments", "account_for", "stale_accounts", "resolve", "check_disclosure_resolves",
]
