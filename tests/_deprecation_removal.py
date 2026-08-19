"""The removal-promise sweep — PURE FUNCTIONS over a SUPPLIED CORPUS.

0.0.18 stage 9 (`REMOVAL-RELEASE-ALREADY-PASSED`, doc 84 §1; doc 102 C2), lane D.

WHAT WENT WRONG, stated once so the functions below are not a style guide.

`src/mokata/deprecation.py` said `REMOVAL_RELEASE = "0.0.17"`, in the published wheel, rendered
verbatim to users on first use of any of five channels:

    ⚠ deprecated: the artifact/session vault channel is deprecated and will be REMOVED in
    mokata 0.0.17. …

**0.0.17 shipped on 2026-08-08 and removed none of them.** The value was true when it was typed
and rotted where nothing could see it rot — doc 85 §7h, in the one module whose entire job is to
state removal facts honestly. It was also PINNED: `test_removal_release_is_0_0_17` asserted the
false value, green, so the suite certified the defect. That is stage 7's finding one file over.

WHAT A DERIVATION CAN AND CANNOT DO HERE, because the distinction is the whole design:

  * A removal release is a DECISION. Nothing in a tree records a decision, so `at=` in
    `deprecation.REMOVAL_DECLARATION` is DECLARED, not derived, and this file does not pretend
    otherwise. ⚠ The one in-tree memory of the last such decision is `CHANGELOG.md`'s `0.0.15`
    entry — *"Removal is scheduled for 0.0.17"* — i.e. the promise that was BROKEN. A derivation
    reading it would have re-imported the defect it exists to prevent.

  * What IS derived is everything downstream, and it is the half that makes the constant unable
    to rot a second time: the constant from the declaration, every rendered notice from the
    constant, the set of channels still IMPLEMENTED from an import probe, the published docs'
    claims from the docs themselves, and the VERDICT from arithmetic against the version being
    cut. `removal_state` is that verdict, and it is what goes RED.

WHERE THE REDNESS APPEARS. `removal_state(__version__, REMOVAL_RELEASE, present)` is asserted by
`tests/test_stage9_removal_release.py`, a unit test. `scripts/release.sh`'s `run_test_preflight`
runs `unittest discover -s tests`, so the release that reaches `at=` with a channel still
implemented is the release whose CUT ABORTS. It does not warn and it is not a docs note: the
0.0.18 cut cannot happen until the deletion lane (stages 10–14) lands, or until someone moves the
declaration forward on purpose, in a diff a reviewer sees — the `WAIVER(...)` bargain stage 7
made, applied to a promise instead of an exemption.

⚠ WHAT THIS DOES NOT GRADE, declared rather than discovered (§7j):

  * **`CHANGELOG.md` is out of corpus ON PURPOSE.** Its `0.0.15` entry still says removal is
    scheduled for 0.0.17. That entry is DATED HISTORY under its own version heading — it records
    what 0.0.15 claimed, which is exactly what 0.0.15 claimed. Editing it would falsify a
    changelog; the corrected schedule belongs in the entry for the release that carries this
    change. The docs corpus below is `docsync.find_docs()` (README + `docs/`, minus the internal
    trees), which does not include it, and that exclusion is this sentence rather than an
    accident.

  * **The version only.** `removal_drift` compares the RELEASE a doc states against the constant.
    It does not require a doc to quote the rendered notice byte-for-byte — the two published
    quotations wrap across lines, and a byte pin over wrapped prose grades the wrapping.

  * **`IMPLEMENTATIONS` is a DECLARED map**, not a discovered one: "which import target IS this
    channel" cannot be read off the registry. It is graded for EXACTNESS against
    `CHANNELS | REMOVED` in both directions, so a stale entry reds as loudly as a missing one.

⚠ WHAT CHANGED AT 0.0.18 STAGE 10 (lane D slice 1), and why the map did NOT shrink.

Two channels — `obsidian` and `native-memory` — are now REMOVED. The obvious move is to drop them
from `IMPLEMENTATIONS`, and it is the wrong one: the map is what the PROBE ranges over, so a
removed channel deleted from it stops being checked at the exact moment the check acquires a
subject. That is doc 85 §7i in the file that quotes §7i — a guard whose offenders you just fixed
grades nothing. So the map keeps all five and the announcement is split in two:

    CHANNELS   still deprecated → its implementation must STILL BE PRESENT (`stale_notices`)
    REMOVED    already removed  → its implementation must BE GONE      (`removal_regressions`)

The union is graded for exactness against the map, so a channel cannot quietly belong to neither.
`removal_regressions` is what reds if a later slice re-introduces a removed backend, or if this
slice had left one of them importable behind a different name.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import ast
import importlib
import importlib.util
import re

__all__ = [
    "IMPLEMENTATIONS", "REMOVAL_PENDING", "REMOVAL_LANDED", "REMOVAL_OVERDUE",
    "REMOVAL_UNREADABLE", "version_key", "removal_state", "present_channels", "live_probe",
    "stale_notices", "removal_regressions", "registry_drift", "removal_mentions", "removal_drift",
    "version_literals", "unaccounted_releases", "future_removals",
    "notice_pins", "NOTICE_PIN_METHODS", "src_release_pins",
    "stale_src_exemptions", "SRC_RELEASE_EXEMPT",
]


# ---- the DEPRECATED SET's implementations — DECLARED, graded for exactness (§7j) ---------------
#
# The channel id is the notice's key; THIS is the thing whose absence means the removal happened.
# `mokata migrate <channel>` moves the data; these are the modules and symbols the deletion lane
# (stages 10–14) takes away. A `module:Symbol` target is present only when the symbol is too — the
# two memory backends live in a module that survives them.
IMPLEMENTATIONS = {
    "obsidian": "mokata.memory.backends:ObsidianBackend",
    "native-memory": "mokata.memory.backends:NativeMemoryBackend",
    # ⚠⚠ RE-POINTED AT 0.0.18 LANE D SLICE 4, FROM THE MODULE TO A SYMBOL — THE SECOND TIME THIS
    # MAP HAS NAMED A FEATURE, AND THE LARGER OF THE TWO. Say the reason out loud so a reviewer
    # grades the reason and not the diff.
    #
    # This entry read `mokata.vault` — the whole module — and the module is NOT the channel. It is
    # the Stage 35d design-artifact vault: `mokata vault list/search/pull/push`, the `vault_list` /
    # `vault_search` / `vault_pull` MCP reads, `vault_push`, and `team join --vault`, whose safety
    # against an untrusted teammate's repo IS `vault_pull`'s content-hash verification. Left
    # pointing here, this map offered the lane one way to go green: delete all of that.
    #
    # THE CHANNEL IS THE SESSION-TRANSPORT KIND, derived three ways and none of them a reading of
    # the prose: the deprecation notice's REPLACEMENT clause named sessions only; `mokata migrate
    # vault` re-homed session bundles and its own module docstring said the artifact vault *"has no
    # canonical memory store to fold into … left for the human"*; and the single production
    # `warn_deprecated("vault", …)` call site was `make_transport`'s vault arm, so a user of the
    # artifact vault was never once told it was deprecated. The registry's own `what` string said
    # "artifact/session vault channel" and was simply wrong — corrected in `deprecation.py`.
    #
    # The rule, stated so the next slice does not re-litigate it: A CHANNEL IS WHAT THE NOTICE
    # OFFERS A REPLACEMENT FOR AND WHAT `mokata migrate <channel>` ACTUALLY MOVES. What a notice
    # merely NAMES but neither replaces nor migrates is a feature sharing the channel's storage,
    # and storage adjacency is not channel membership — the bundles lived in `.mokata/vault/
    # sessions/`, inside the surviving feature's own directory.
    #
    # ⚠ THE RE-POINT IS NOT THE PROOF. `removal_regressions` grades ONE symbol; the slice's own
    # suite grades the rest — the transport arm gone, `TRANSPORT_KINDS` shrunk, the migrator module
    # gone, a removed kind answered with its record rather than "unknown transport", and no
    # surviving path that reaches the deleted class by another name.
    "vault": "mokata.session_transport:VaultTransport",
    # ⚠⚠ RE-POINTED AT 0.0.18 STAGE 10's SUCCESSOR (lane D slice 2), FROM THE MODULE TO A SYMBOL,
    # AND THAT IS THE ONE EDIT IN THIS FILE THAT COULD BE USED TO CHEAT THE GATE. Say the reason
    # out loud so a reviewer grades the reason and not the diff.
    #
    # This entry read `mokata.memory.share` — the whole module — and the module is NOT the channel.
    # It is 35b's memory BACKUP surface: `mokata memory export` / `import`, a live, supported,
    # P23 feature. The `memory-share` CHANNEL was one destination FILENAME inside it, plus the
    # detector that gave the name behaviour. Left pointing at the module, this map offered the lane
    # exactly one way to go green — delete `export`/`import` — and the tracker row's "660 LOC"
    # reads like an invitation to do it. A probe that can only be satisfied by removing a feature
    # nobody asked to remove is a wrong probe, not a strict one.
    #
    # `is_legacy_share_dest` is the target because it is the channel's BEHAVIOUR, not its datum:
    # the constant alone is an inert string, and what made `memory-share.json` a channel rather
    # than a filename was a function that recognised it and warned. The map's own contract already
    # covers this shape — *"a `module:Symbol` target is present only when the symbol is too — the
    # two memory backends live in a module that survives them"* — and this is the third instance,
    # not a new rule invented to fit.
    #
    # ⚠ THE RE-POINT IS NOT THE PROOF. `removal_regressions` grades ONE symbol; the slice's own
    # suite grades the rest — the constant gone, the migrate branch gone, both warn call sites
    # gone, and NO surviving path that treats that filename as anything but a path.
    "memory-share": "mokata.memory.share:is_legacy_share_dest",
    "neo4j": "mokata.knowledge.neo4j_backend",
}

# ---- the four states, and they are four because they are four different facts (§7g) -----------
#
# PENDING and LANDED are both fine and are NOT the same fact — one is a promise not yet due, the
# other is a promise kept. OVERDUE is the defect this stage exists to end. UNREADABLE is a version
# string no arithmetic can grade, and it is separate because folding it into PENDING would make a
# typo in the declaration read as "all is well until later" — which is precisely how a wrong
# constant survives a release.
REMOVAL_PENDING = "pending"        # the channels are here and the promised release has not come
REMOVAL_LANDED = "landed"          # no implementation is left — the removal happened
REMOVAL_OVERDUE = "overdue"        # the promised release has ARRIVED and the channels are here
REMOVAL_UNREADABLE = "unreadable"  # a version that is not a version — graded by nothing

_VERSION = re.compile(r"^\d+(?:\.\d+)+$")


def version_key(text):
    """A comparable key for a dotted release, or None when the text is not one.

    None rather than a zero tuple: an unreadable version must not sort BELOW every real one, which
    would make every comparison against it quietly succeed."""
    if not _VERSION.match((text or "").strip()):
        return None
    return tuple(int(part) for part in text.strip().split("."))


def removal_state(version, removal, present):
    """The verdict, over a SUPPLIED (version being cut, promised release, channels still here).

    Supplied, not read from the tree, for the §7i reason: once the constant is corrected this tree
    holds no offender, so a function that went and looked would pass having graded nothing. The
    tests hand it the 0.0.17 state that actually shipped, and a future that has not happened yet.
    """
    if not present:
        return REMOVAL_LANDED
    now, due = version_key(version), version_key(removal)
    if now is None or due is None:
        return REMOVAL_UNREADABLE
    return REMOVAL_OVERDUE if now >= due else REMOVAL_PENDING


def present_channels(targets, probe):
    """The channels whose implementation the supplied `probe` still finds — `{channel: target}`
    in, a frozenset of channel ids out."""
    return frozenset(channel for channel, target in targets.items() if probe(target))


def live_probe(target):
    """The real probe: is `module` importable / does `module:Symbol` still exist?

    Import errors that are NOT "the module is gone" (a dependency missing, a broken extra) must
    not read as a removal, so anything importable-but-unimportable counts as PRESENT — the safe
    direction, since a false "landed" would silently retire the whole guard."""
    module, _, symbol = target.partition(":")
    try:
        spec = importlib.util.find_spec(module)
    except (ImportError, AttributeError, ValueError):
        return True
    if spec is None:
        return False
    if not symbol:
        return True
    try:
        return hasattr(importlib.import_module(module), symbol)
    except ImportError:
        return True


def stale_notices(announced, present):
    """Channels that still carry a deprecation NOTICE while their implementation is already gone.

    The other direction of the same dishonesty: "will be REMOVED in X" about something that has
    already left tells a user to migrate off a thing that is not there. Stages 10–14 delete the
    implementations; this is what reds if one of them forgets the notice."""
    return tuple(sorted(frozenset(announced) - frozenset(present)))


def removal_regressions(removed, targets, probe):
    """REMOVED channels whose implementation the supplied `probe` can STILL find.

    The mirror of `stale_notices`, and the half that grades a DELETION rather than a promise. A
    slice that made the import probe pass while leaving the subsystem reachable by another path
    has not deleted it — it has hidden it — and the only way to say that mechanically is to keep
    the removed channel's target in the map and require it to be absent forever after."""
    return tuple(sorted(channel for channel in removed
                        if channel in targets and probe(targets[channel])))


def registry_drift(announced, targets):
    """`(unmapped, unannounced)` — the exactness grading of the DECLARED `IMPLEMENTATIONS` map.

    A channel with a notice and no target is a channel this sweep cannot see the removal of; a
    target with no notice is a map entry that has outlived its channel. Two facts, two tuples."""
    announced, targets = frozenset(announced), frozenset(targets)
    return (tuple(sorted(announced - targets)), tuple(sorted(targets - announced)))


# ---- the published docs say it too, and they said 0.0.17 as well ------------------------------
# ⚠ WIDENED AT 0.0.18 STAGE 10, AND WHAT IT FOUND IS THE REASON.
# This matched TWO phrasings — the rendered notice (`REMOVED in mokata X`) and the admonition
# title (`removal: X`) — and stage 9 corrected the five pages that used them. There is a THIRD,
# and it is the one the prose pages actually use: *"(removal in 0.0.17)"* / *"scheduled for
# removal in 0.0.17"*. Eight published pages carried it, every one still naming a release that
# shipped and removed nothing, and the sweep built to catch exactly that read the corpus CLEAN.
#
# Same defect as `notice_pins` had, one corpus over, and the same §7j shape: the VALUE was
# derived and the PHRASING was hand-typed, so the sweep meant "the two spellings I remembered"
# while reading as "the docs". The form below matches `remov*` followed by a release within a
# short window, so it does not depend on remembering a spelling — and the window steps over line
# wraps and blockquote markers, because the quoted notice wraps on two of the five pages and a
# line-anchored reader finds nothing and reports a clean corpus.
_MENTION = re.compile(r"remov\w*(?:[^\d\n]|[\s>]*\n[\s>]*){0,40}?(\d+\.\d+(?:\.\d+)+)", re.I)


def removal_mentions(docs):
    """`[(path, line, version)]` for every stated removal release in a supplied `{path: text}`."""
    found = []
    for path in sorted(docs):
        text = docs[path] or ""
        for match in _MENTION.finditer(text):
            found.append((path, text.count("\n", 0, match.start()) + 1, match.group(1)))
    return tuple(found)


def removal_drift(docs, removal):
    """Every mention naming a release OTHER than `removal` — the docs' half of the same defect."""
    return tuple(m for m in removal_mentions(docs) if m[2] != removal)


# ---- the pin was not one pin. It was SIX ------------------------------------------------------
#
# The row named ONE pinning test. The full 3.12 unit run named five more, in three files the row
# does not mention, plus a SECOND copy of the release in `src/mokata/profiles.py` that `init_repo`
# writes into every repo's `.mokata/manifest.json`. That is instance-versus-class (§7j) on the
# defect's own subject: fixing the named instance would have left five green assertions certifying
# the old release, and the next reader would have found them the way this run did — or not at all.
#
# So the guard is over the CLASS. The domain is DECLARED and narrow on purpose: a test file that
# talks about deprecation at all. Measured across all 469 test files before it was written — the
# predicate selected the eight real offenders and NOTHING else, while the same predicate without
# the domain filter selected 39 sites, 31 of them legitimate synthetic version fixtures
# (`9.9.9`, `2.3.6`, a DSN's `10.0.0.1`). A guard that cries wolf 31 times is a guard someone
# turns off.
NOTICE_PIN_METHODS = ("assertIn", "assertNotIn", "assertEqual", "assertNotEqual")
_DEPRECATION_VOCABULARY = "deprecat"

# WIDENED AT 0.0.18 STAGE 10, BECAUSE THE ANCHORED FORM MISSED A SEVENTH PIN.
# This was `re.match` against a fully-anchored release — the whole argument had to BE one.
# `test_close_fix_approve_list` asserted `assertIn("scheduled for removal in 0.0.17", help_text)`
# three times, green, over `mokata migrate --help`, and the guard could not see it: the release
# was INSIDE a sentence. Six pins became seven, and the seventh was on a surface that prints to
# a user.
#
# AND THE WIDENING IS NOT H04's MUTANT. Stage 9's surviving mutant loosened this to a bare
# one-dot pattern, which convicts "3.10 (the declared floor)" in exactly the files that must
# trust the guard. This form still requires TWO dots, so 3.10 is not a release here and never
# was; only the FULL-MATCH anchoring is dropped. Measured before it was changed: across all 450
# test files the search form selects three sites, all in one file, all real — zero false
# positives.
_BARE_RELEASE = re.compile(r"\d+\.\d+(?:\.\d+)+")


def notice_pins(sources):
    """`[(path, line, method, literal)]` — assertions in a DEPRECATION-AWARE test that compare
    against a HAND-TYPED release instead of reading `deprecation.REMOVAL_RELEASE`.

    Both of the first two arguments are read: `assertIn(literal, rendered)` is the shape all six
    offenders had, `assertEqual(catalog_value, literal)` is the shape the manifest ones had, and
    `assertIn("... removal in 0.0.17", help_text)` is the shape the SEVENTH had — a release inside
    a sentence, which is why the release is now SEARCHED FOR in the literal rather than required
    to be the whole of it. The reported `literal` is the release found, not the whole argument.
    A file that never mentions deprecation is out of domain, and a file that mentions it while
    reading the constant from the module has nothing to report."""
    found = []
    for path in sorted(sources):
        text = sources[path] or ""
        if _DEPRECATION_VOCABULARY not in text.lower():
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in NOTICE_PIN_METHODS:
                continue
            for arg in node.args[:2]:
                if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
                    continue
                for literal in _BARE_RELEASE.findall(arg.value):
                    found.append((path, node.lineno, node.func.attr, literal))
    return tuple(sorted(found))


# ---- the axis `notice_pins` does not range over, and where the next instance was --------------
#
# ★ STAGE 9 CORRECTED ELEVEN SURFACES AND THERE WAS A TWELFTH, in `--help` text.
# `src/mokata/cli_commands/migrate.py` said *"scheduled for removal in 0.0.17"* three times, twice
# in argparse strings that `mokata migrate --help` prints verbatim, and `src/mokata/vault.py`
# raised a `VaultError` naming 0.0.17 to a user holding a corrupt artifact. Neither is a test, so
# `notice_pins` could not see either: its PROPERTY is derived and its CORPUS is `tests/`. That is
# doc 85 §7j exactly — "of any mechanism that derives, ask which axis is derived and which is a
# literal; the literal is where the next instance will be."
#
# This is the same predicate pointed at the other corpus. It is deliberately NOT the same function
# with a wider domain: the two corpora need different exemptions, and folding them would give one
# of them the other's blind spot.
#
#   * The SHAPE is a string LITERAL containing a dotted release, in a deprecation-aware module.
#     Comments never reach the AST (a comment describing 0.0.17's history is history), and
#     docstrings are excluded for the same reason `version_literals` excludes them: prose about a
#     historical value is not a value.
#   * `SRC_RELEASE_EXEMPT` is DECLARED and graded for exactness. ⚠ It exempts a SITE, never a
#     FILE, and the difference is the whole value of it: `parity.py` is one of the most
#     deprecation-aware modules in the tree, so exempting the file would hand the guard's blind
#     spot to exactly the file most likely to grow the next instance. A site is named by the
#     EXACT string constant that carries the release — no line number (four files have drifted
#     under four consecutive stages), and a reworded string goes stale LOUDLY rather than
#     silently keeping its pass.
SRC_RELEASE_EXEMPT = {
    # A `CommandSurface` note about work that SHIPPED in 0.0.17. A release that happened is a
    # fact, not a promise, and this is the one shape the property cannot tell apart on its own.
    #
    # ⚠ AN EXEMPTION IS A DISTINCTIVE FRAGMENT OF THE CONSTANT, not the whole constant and not a
    # line number. The whole constant is a 700-character `CommandSurface` note, so pinning it
    # entire would make every unrelated word of that note load-bearing; a line number is what four
    # files have drifted under four consecutive stages. The fragment carries the release itself,
    # so a rewording that changes what is CLAIMED goes stale and reds, while a rewording elsewhere
    # in the same note does not.
    "mokata/parity.py": frozenset({"FR-WT-2/3 (`clean`/`remove`, 0.0.17)"}),
}

# The module `version_literals` owns, and it grades it STRICTLY MORE than this function could:
# not "no hand-typed release" but "exactly one release literal, and it is the declaration". Two
# guards over one file would be doc 85 §7f — each covering for the other, neither gradable — so
# the split is declared here instead: `deprecation.py` is `version_literals`', everything else in
# `src/` is this function's, and no module is in both.
_DECLARATION_MODULE = "mokata/deprecation.py"


def _string_constants(text):
    """Every non-docstring string constant in a module, as `[(line, value)]`.

    Docstrings are excluded for `version_literals`' reason: prose about a historical value is not
    a value. Comments never reach the AST at all."""
    tree = ast.parse(text)
    docstrings = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            docstrings.add(id(body[0].value))
    return [(node.lineno, node.value) for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node) not in docstrings]


def src_release_pins(sources, exempt=None):
    """`[(path, line, literal)]` — release strings HAND-TYPED into a deprecation-aware `src/`
    module, where `deprecation.REMOVAL_RELEASE` belongs.

    Same corpus contract as everything else here: a supplied `{path: text}`, never a walk that
    goes and finds the tree (§7i). `exempt` defaults to `SRC_RELEASE_EXEMPT` and is keyed
    `{path: {distinctive fragment of the string constant}}` — a site, not a file."""
    exempt = SRC_RELEASE_EXEMPT if exempt is None else exempt
    found = []
    for path in sorted(sources):
        if path == _DECLARATION_MODULE:
            continue
        exempt_here = exempt.get(path, frozenset())
        text = sources[path] or ""
        if _DEPRECATION_VOCABULARY not in text.lower():
            continue
        try:
            constants = _string_constants(text)
        except SyntaxError:
            continue
        for line, value in constants:
            if any(fragment in value for fragment in exempt_here):
                continue
            for literal in _LITERAL_VERSION.findall(value):
                found.append((path, line, literal))
    return tuple(sorted(found))


def stale_src_exemptions(sources, exempt=None):
    """Declared `SRC_RELEASE_EXEMPT` fragments that no longer appear in any string constant.

    The exactness half (§7j): an exemption whose string was reworded keeps granting a pass to
    nothing while the reworded string is unguarded, and nothing would ever say so."""
    exempt = SRC_RELEASE_EXEMPT if exempt is None else exempt
    stale = []
    for path, constants in sorted(exempt.items()):
        text = sources.get(path)
        try:
            present = {value for _line, value in _string_constants(text or "")}
        except SyntaxError:
            present = set()
        for fragment in sorted(constants):
            if text is None or not any(fragment in value for value in present):
                stale.append((path, fragment))
    return tuple(stale)


# ---- one release string, and a way to know it stayed one --------------------------------------

_LITERAL_VERSION = re.compile(r"\d+\.\d+(?:\.\d+)+")


def unaccounted_releases(literals, declared_at, frozen):
    """Release literals in `deprecation.py` that are NEITHER the declaration's `at=` NOR a frozen
    `removed=` on a removal record — over supplied values, never a read of the tree (§7i).

    ⚠ THE CONTRACT CHANGED AT E7 (ruled 2026-08-15) AND IT GOT STRONGER, NOT LOOSER. It used to be
    "exactly one release literal, and it is the declaration", which worked only while every removal
    record DEFAULTED its release from `REMOVAL_RELEASE` — and that default was the defect the
    ruling removed: it made a statement about the PAST (which release took this channel away) track
    a MUTABLE PROMISE (which release the next removal is due in), so moving `at=` silently re-dated
    every removal that had already happened.

    Frozen per-channel literals are therefore EXPECTED now, and counting them is no longer the
    test. What is: every literal is accounted for by one of the two roles, so a THIRD release
    string — the shape the original guard existed to catch — still reds.

    `future_removals` is the other half; the two are separate because they are separate facts.

    ⚠ It ranges over the RELEASES INSIDE each literal, not the literal itself: the declaration is a
    whole `REMOVAL(set=…, at=…, filed=…)` sentence, so a whole-string comparison would report the
    declaration as unaccounted and, worse, would accept any new sentence that happened to embed a
    second release."""
    allowed = frozenset([declared_at]) | frozenset(frozen.values())
    return tuple((line, found) for line, literal in literals
                 for found in _LITERAL_VERSION.findall(literal) if found not in allowed)


def future_removals(frozen, declared_at):
    """Channels whose FROZEN `removed=` names a release later than the declaration promises.

    A record saying a channel was removed in a release that has not happened is not a stale value,
    it is an impossible one — and it is the specific way a frozen field rots: somebody freezes the
    release they HOPE to cut in. Ordered by `version_key`, so an unreadable literal on either side
    reports rather than silently comparing as strings."""
    due = version_key(declared_at)
    out = []
    for channel in sorted(frozen):
        got = version_key(frozen[channel])
        if got is None or due is None or got > due:
            out.append((channel, frozen[channel]))
    return tuple(out)


def version_literals(source):
    """`[(line, literal)]` for every CODE string in a module that contains a dotted release.

    The corpus half of the two graders above: this FINDS the literals, they DECIDE which are
    legitimate. Splitting it that way keeps the finder honest — it has no opinion about what a
    release string is for, so a new role for one cannot be smuggled in by widening the finder.

    Docstrings are excluded and comments never reach the AST, so the module can go on describing
    the 0.0.17 it used to say — PROSE ABOUT A HISTORICAL VALUE IS NOT A VALUE. That distinction is
    the same one `_release_repo_guards.disabled_calls` had to make, one file over."""
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            docstrings.add(id(body[0].value))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in docstrings or not _LITERAL_VERSION.search(node.value):
            continue
        found.append((node.lineno, node.value))
    return tuple(sorted(found))
