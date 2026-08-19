"""SIMP.S2 — the deprecation-shim layer: warn ONCE per repo per channel; delete NOTHING.

bare Claude Code strands data in dead channels when tools change. mokata deprecates with a WARN
plus a one-command, human-approved migration that provably moves every item into the ONE
canonical shape — nothing silently dropped, nothing silently kept working-until-deleted (P22).

This module is the primitive every deprecated channel routes its first-use notice through. The
notice names WHAT is deprecated, the canonical REPLACEMENT, the one-time MIGRATION command, and
WHEN it disappears (`REMOVAL_RELEASE`, derived from the one declaration below). It fires at most
ONCE per repo per channel — a state marker (an atomic `O_EXCL` file under `temp_local/`, the
`graph_adopt.disclose_first_use` precedent), never a nag on every call (doc 85 once-per-repo /
failures-only discipline). It follows the `*Notice` shape the `degrade.py`
`DegradeNotice`/`note_degraded` family already established.

The DEPRECATED SET (removed at `REMOVAL_RELEASE`, SIMP.S3–S4 — this module only WARNS + SHIMS):
  * (empty at 0.0.18 — `neo4j`, the last member, was REMOVED by lane D stage 14)

⚠ `CHANNELS` IS EMPTY AND THAT IS ASSERTED, NOT LEFT TO BE NOTICED. Three graders in
`tests/test_stage9_removal_release.py` range over it (`stale_notices`, the still-deprecated target
probe, the rendered-release pin), and an empty domain turns each of them from a check into a
vacuous pass — doc 85 §7i, arriving through the front door on the release that empties the set. The
suite therefore states the emptiness as a POSITIVE fact and grades the deprecation MECHANISM
against a PLANTED channel instead of a live one, so `warn_deprecated`, `DeprecationNotice.render`
and the marker discipline stay gradable with no channel left to grade them with.

The REMOVED SET (`REMOVED` below — the promise KEPT, and the reason this module did not just
delete the entries): a channel that is gone still has a user who was told it was going, and that
user may hold DATA in it. A deprecation notice ("will be REMOVED in X") is a lie once the code is
gone; a removal notice ("was REMOVED in X, your data is untouched at <path>, here is the one way
to bring it across") is the only honest thing left to say. Doc 85 §7d's ONE EXCEPTION, in code:
"we do not do backward compatibility" is a statement about our code, never a licence to destroy —
or to silently misrepresent — something a human owns (P2).
  * obsidian       — the Obsidian memory backend        (removed 0.0.18, lane D slice 1)
  * native-memory  — the native-memory backend          (removed 0.0.18, lane D slice 1)
  * memory-share   — the `memory-share.json` channel    (removed 0.0.18, lane D slice 2)
  * vault          — the vault SESSION-TRANSPORT kind   (removed 0.0.18, lane D slice 4)
  * neo4j          — the Neo4j code-graph backend       (removed 0.0.18, lane D stage 14)

⚠ THE `vault` ENTRY NAMES A TRANSPORT KIND, NOT `mokata.vault`. The registry used to call this
channel the *"artifact/session vault channel"*, and the artifact half was never a channel: the
replacement clause named sessions only, the migrator moved session bundles only and said so in its
own docstring, and the one production `warn_deprecated("vault", …)` call sat in `make_transport`'s
transport arm — so a user of `mokata vault push/list/pull` was never once told the thing was going.
The design-artifact vault is a LIVE, supported feature (four CLI verbs, four MCP tools, and `team
join --vault`'s hash-verified read of an untrusted teammate's repo) and it survives untouched. The
rule the boundary comes from: A CHANNEL IS WHAT THE NOTICE OFFERS A REPLACEMENT FOR AND WHAT
`mokata migrate <channel>` ACTUALLY MOVES; what a notice merely NAMES but neither replaces nor
migrates is a feature sharing the channel's storage. Storage adjacency is not channel membership —
and here the channel's bundles literally lived inside the surviving feature's directory.

⚠ THE REMOVED SET HOLDS THREE KINDS OF THING, AND THEY SAY DIFFERENT THINGS TO THE USER. The first
two are BACKENDS a manifest chain resolves to: their data is behind a store this release can no
longer open, so the honest remedy is a DOWNGRADE (`RemovedNotice`). `memory-share` and `vault` are
FILES the user owns, and this release still reads them — `mokata memory import` / `mokata session
list` restore them directly. Telling those users to `pip install mokata==<older>` would be a FALSE
REFUSAL: a remedy that costs them a downgrade for something the release in their hands already
does. See `RemovedFileNotice`.

★ AND `neo4j` IS A THIRD KIND, WHICH IS WHY THERE IS A THIRD CLASS. The distinction is CANONICAL vs
DERIVED data, and this module already drew it on the DEPRECATION side without ever drawing it here:
`DeprecationNotice.migration == ""` renders *"No migration needed — the graph is derived data;
re-index"*, and the REMOVAL half had no twin for that sentence. So the only records available said
either *"install an older mokata and run `mokata migrate neo4j`"* — a command that has NEVER
existed, since `CHANNELS["neo4j"].migration` was the empty string for the channel's whole
deprecated life — or *"your file is still at `.mokata/<path>`"*, which is false in a different way:
the graph was never under `.mokata/` at all. It is on the user's own server at `$NEO4J_URI`.

⚠ AND `native-memory`'s SHAPE — the nearest existing one — DOES NOT FIT EITHER, which is worth
saying out loud because it is one clause away from looking like it does. Its DATA clause is right
(*"an EXTERNAL store — mokata never held its data and has deleted nothing"*) and its REMEDY is
still a downgrade plus `mokata migrate native-memory`, because those memories were CANONICAL:
mokata was the only place they were going to live, so they had to be brought ACROSS. A code graph
is DERIVED. There is nothing to bring across — the canonical graph (the embedded AST floor / an
adopted CRG, doc 85 §6) re-derives it from the code, which is where it came from. Charging this
user a downgrade would charge them for work that is already done. See `RemovedDerivedNotice`.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from . import TEMP_LOCAL_DIRNAME
from .errors import MokataError

# ---- WHEN the DEPRECATED SET disappears, and why this is a declaration rather than a literal ----
#
# This read `REMOVAL_RELEASE = "0.0.17"` in the published 0.0.17 wheel, and 0.0.17 removed nothing —
# every channel below is still here. The value was true when it was typed and rotted in place, on
# the one surface that cannot afford it (`REMOVAL-RELEASE-ALREADY-PASSED`, doc 84 §1; doc 85 §7h).
#
# A removal date is a DECISION, and no derivation reads a decision out of a tree — the only record
# of the last one is `CHANGELOG.md`'s 0.0.15 entry, which records the promise that was BROKEN, so
# deriving from it would reproduce the defect. `at=` is therefore DECLARED. What a machine can do
# is catch the decision being broken, and that is why this is the `WAIVER(...)` shape stage 7 built
# for exactly this problem: ONE machine-readable place, and arithmetic against the version being
# cut instead of a sentence someone has to notice. `tests/_deprecation_removal.py` grades it — the
# release that REACHES `at=` with a channel still implemented is the release whose unit suite goes
# RED, and `release.sh`'s test preflight runs that suite, so the CUT stops rather than the wheel
# shipping a second wrong answer.
REMOVAL_DECLARATION = "REMOVAL(set=deprecated-channels, at=0.0.18, filed=2026-08-14, owner=Jas)"

_REMOVAL = re.compile(r"REMOVAL\(([^)]*)\)")
_FIELD = re.compile(r"([A-Za-z_][A-Za-z0-9_-]*)\s*=\s*([^,]*)")
_VERSION = re.compile(r"^\d+(?:\.\d+)+$")
_FILED = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def removal_fields(declaration: str) -> Dict[str, str]:
    """The `key=value` fields of a `REMOVAL(...)` declaration; `{}` when there is no declaration.

    Empty rather than raising: "no declaration here" is a different fact from "a declaration this
    module cannot read", and only the second one is a defect (see `removal_target`)."""
    match = _REMOVAL.search(declaration or "")
    if match is None:
        return {}
    return {key: value.strip() for key, value in _FIELD.findall(match.group(1))}


def removal_target(declaration: str) -> str:
    """The release a `REMOVAL(...)` declaration promises, as a dotted version.

    RAISES rather than falling back to a default. This module's entire job is to state removal
    facts honestly; a module that cannot read its own declaration has no fact to state, and a
    silent default would be a second wrong answer on the same user surface. Failing at import is
    loud, and it is caught by the suite before it can be cut."""
    at = removal_fields(declaration).get("at", "")
    if not _VERSION.match(at):
        raise ValueError(
            "REMOVAL declaration carries no readable `at=<version>`: %r" % (declaration,))
    return at


def removal_filed(declaration: str) -> str:
    """The DATE a `REMOVAL(...)` declaration was filed — i.e. the removal SET's IDENTITY.

    ★ E7, RULED 2026-08-15 (Jas). The once-per-repo removal marker is keyed on THIS, not on the
    release, and the two are different facts wearing the same shape.

    `at=` is a PROMISE and a promise can slip. A cut that misses 0.0.18 moves it to 0.0.19 without
    one channel's fate changing — and a marker keyed on the release would then re-fire in every
    repo on earth to re-announce removals the user already read, under a new number. That is a
    notice with no news in it, which is how a once-per-repo discipline teaches people to ignore it.

    `filed=` moves only when somebody DECIDES a new removal set, which is exactly when a repo needs
    telling again. So a slipping `at=` re-fires nothing and a genuinely new removal set re-fires
    everything, including channels removed under an earlier declaration: a user reading the second
    notice is being told about a NEW decision, and the set it announces is the set as it now stands.

    RAISES rather than defaulting, for `removal_target`'s reason and one that is worse: a silent
    default here would SILENCE a notice rather than print a wrong one, and a removal nobody is told
    about is the quiet half of the same defect."""
    filed = removal_fields(declaration).get("filed", "")
    if not _FILED.match(filed):
        raise ValueError(
            "REMOVAL declaration carries no readable `filed=<YYYY-MM-DD>`: %r" % (declaration,))
    return filed


# Derived from the declaration above, and named once, so that no notice — and no second constant —
# can drift from the promise.
#
# ⚠ THIS IS THE GATE'S TARGET AND NOTHING ELSE SINCE E7 (0.0.18 slice 4). It used to be the default
# for every removal record's `removed=` as well, which quietly made a HISTORICAL FACT track a
# MUTABLE PROMISE: move the declaration and every already-removed channel's notice retroactively
# claimed a different release. `removed=` is now frozen per channel at its construction site below.
REMOVAL_RELEASE = removal_target(REMOVAL_DECLARATION)

# The removal set's identity — what `warn_removed`'s marker and its ledger record are keyed on.
REMOVAL_FILED = removal_filed(REMOVAL_DECLARATION)


def last_release_with(removal: str) -> str:
    """The last release that still SHIPPED the set — i.e. the one before `removal`.

    A removed channel's only honest remedy names a release a user can actually install, and that
    release is not a second decision: it is arithmetic on the one already declared. Typing it
    would put a second release literal in this module, which is exactly the defect
    `REMOVAL-RELEASE-ALREADY-PASSED` was, one field over — the remedy would go on naming 0.0.17
    long after the removal moved.

    PRECONDITION, declared rather than assumed: patch-only versioning is in force pre-1.0
    (CLAUDE.md), so "the release before" is the patch minus one. A removal declared at a `.0`
    patch has no in-series predecessor and this RAISES rather than inventing one — loud at import,
    caught by the suite, never a second wrong answer on a user surface."""
    parts = [int(p) for p in removal.split(".")]
    if parts[-1] < 1:
        raise ValueError(
            "cannot derive the last shipping release before %r: patch-only versioning is the "
            "declared precondition and this removal is at a .0 patch" % (removal,))
    parts[-1] -= 1
    return ".".join(str(p) for p in parts)


# The release a user must install to run the migration one last time. Derived, never typed.
LAST_SHIPPING_RELEASE = last_release_with(REMOVAL_RELEASE)

# The ledger kind a first-use notice records when a ledger is supplied — an auditable "this repo
# touched a deprecated channel" event, hash-chained like every other ledger row.
DEPRECATION_LEDGER_KIND = "deprecation_notice"

# The once-per-repo marker directory (under temp_local — run-state, ungated, per-repo-ephemeral,
# exactly like `graph_adopt`'s first-use markers).
_MARKER_DIRNAME = "deprecations"


@dataclass(frozen=True)
class DeprecationNotice:
    """One deprecated channel's notice — WHAT is going, the canonical REPLACEMENT, the one-time
    MIGRATION command (empty for derived data that is re-indexed, not migrated), and WHEN it
    disappears. Static text only: it carries no item content and no DSN value, so it can never
    leak a secret (P23/CM.S1 secret-safety)."""

    channel: str            # the stable channel id (also the migration subcommand)
    what: str               # human name of the deprecated thing
    replacement: str        # the canonical shape that replaces it
    migration: str = ""     # the one-time migration command; "" ⇒ re-index (derived data)
    removal: str = REMOVAL_RELEASE

    def render(self, *, ascii_only: bool = False) -> str:
        glyph = "[deprecated]" if ascii_only else "⚠ deprecated"
        if self.migration:
            how = f"Migrate now with `{self.migration}` (one-time, human-gated)."
        else:
            how = ("No migration needed — the graph is derived data; re-index with your current "
                   "code-graph backend.")
        return (f"{glyph}: the {self.what} is deprecated and will be REMOVED in mokata "
                f"{self.removal}. {self.replacement} {how}")


# The registry — the ONE source of truth for what SIMP.S2 deprecates.
#
# ⚠ EMPTY AT 0.0.18, DELIBERATELY, AND NOT DELETED WITH ITS LAST MEMBER. `neo4j` was the last
# deprecated channel and lane D stage 14 removed it, so SIMP.S2 has nothing left to announce. The
# machinery below (`DeprecationNotice`, `warn_deprecated`, the once-per-repo marker) stays because
# an empty registry is a STATE, not a dead feature: `_deprecation_removal` grades the union of this
# dict and `REMOVED` for exactness in both directions, so deleting this half would retire the
# gate's other arm in the very release that opens it. What CHANGES is how it is graded — the
# mechanism is now exercised against a PLANTED channel (§7i), never against a live one, because
# there is no live one to exercise it with.
#
# 🔴 NOTED, NOT ABSORBED: `warn_deprecated` now has ZERO production call sites. The last one was
# `knowledge/layer.py`'s neo4j arm, deleted by this stage. That is a real fact about a primitive
# this module still exports, and it is filed rather than fixed here — deleting it is a FEATURE
# decision (§7d deletes accommodations, not framework), and a deprecation cycle with no cycle
# running is exactly what an empty `CHANNELS` is supposed to look like.
CHANNELS: Dict[str, DeprecationNotice] = {}


# ---- the REMOVED set — the promise KEPT, and what is still owed to the user who believed it ----
#
# ★ THE ONE THING THIS HALF EXISTS TO PREVENT. Delete `ObsidianBackend` and delete nothing else,
# and a repo whose committed manifest routes `memory_store` to `obsidian` resolves PAST it — the
# detect strategy is gone, so the detector reports absent, so the router degrades to the SQLite
# floor and hands back an EMPTY store. No error, no notice, no exit code: the user's memory
# appears to have been erased by an upgrade, and mokata says nothing at all. That is not a
# compatibility question (§7d) — it is misrepresenting data a human owns (P2), and it is the
# failure mode the whole of doc 85 §7g is about: "no items" and "the backend that held your items
# is gone" arriving as the same answer.
#
# So a removed channel keeps a RECORD, and the record is not a deprecation notice. "Will be
# REMOVED in 0.0.18" is false the moment the code goes; what is true is: it WAS removed, in this
# release, your data is where you left it, and here is the one command that brings it across.

@dataclass(frozen=True)
class RemovedNotice:
    """One removed channel's refusal — WHAT went, WHEN, where the user's DATA still is, and the
    one REMEDY. Static text plus a caller-supplied location; it carries no item content and no
    DSN value, so it cannot leak a secret (P23/CM.S1), exactly like `DeprecationNotice`."""

    channel: str            # the stable channel id (what a manifest still names)
    what: str               # human name of the removed thing
    data: str               # what became of the user's data — always "nothing was destroyed"
    remedy: str             # the one way to bring it into the canonical store
    # REQUIRED and FROZEN at the construction site (E7, ruled 2026-08-15). Defaulting this to
    # `REMOVAL_RELEASE` made a statement about the PAST read a constant about the FUTURE: the day
    # `at=` moves, every record here would claim its channel left in a release that had not
    # happened. What release removed this channel is not a promise, it is history.
    removed: str

    def render(self, *, detail: str = "", ascii_only: bool = False) -> str:
        glyph = "[removed]" if ascii_only else "✖ removed"
        where = f" {detail}" if detail else ""
        return (f"{glyph}: the {self.what} was REMOVED in mokata {self.removed}, and this repo "
                f"still names `{self.channel}`. {self.data}{where} mokata will NOT read it and "
                f"will NOT hand you an empty store instead. {self.remedy}")


@dataclass(frozen=True)
class RemovedFileNotice:
    """A removed channel whose subject is a FILE THE USER OWNS, not a store mokata resolves to.

    ★ IT IS A SEPARATE CLASS BECAUSE IT SAYS THE OPPOSITE THING, and one class that could say both
    would say neither (§7g). `RemovedNotice`'s load-bearing sentence is *"mokata will NOT read it
    and will NOT hand you an empty store instead"* — true of a backend whose code is gone. Render
    that over `memory-share` and every clause is false: mokata WILL read the file, there is no
    store to be handed, and the `pip install 'mokata==<older>'` remedy would send a user to a
    downgrade for something the release in their hands already does in one command.

    So this notice carries no downgrade and no migration subcommand. What it carries is WHERE the
    file is (supplied by the caller) and the ONE command in THIS release that restores it.

    Static text plus a caller-supplied location — no item content, no DSN value, so it cannot leak
    a secret (P23/CM.S1), exactly like the other two notice classes."""

    channel: str            # the stable channel id (what a 0.0.17 notice told the user to migrate)
    what: str               # human name of the removed thing
    data: str               # what became of the user's file — always "nothing was destroyed"
    remedy: str             # the one command in THIS release that reads it
    removed: str            # REQUIRED + FROZEN — see `RemovedNotice.removed` (E7)
    # WHERE the user's bytes are, relative to `.mokata/`. ⚠ THIS IS A FIELD BECAUSE IT WAS A
    # HARDCODE. `removed_share_path` composed `memory-share.json` for EVERY file channel — filed at
    # slice 3 as "unreachable today, wrong for a second file channel", and slice 4 IS the second
    # file channel, so the defect became reachable in the release that found it. A location on the
    # record cannot be inherited by the next channel the way one function's constant was.
    path: str

    def render(self, *, detail: str = "", ascii_only: bool = False) -> str:
        glyph = "[removed]" if ascii_only else "✖ removed"
        where = f" {detail}" if detail else ""
        return (f"{glyph}: the {self.what} was REMOVED in mokata {self.removed}. "
                f"{self.data}{where} {self.remedy}")


@dataclass(frozen=True)
class RemovedDerivedNotice:
    """A removed channel whose subject was DERIVED data, held in a system THE USER RUNS.

    ★ THE THIRD CLASS, AND IT EXISTS BECAUSE BOTH EXISTING ONES ARE FALSE HERE — not weaker, false,
    which is slice 2's lesson arriving on the last channel in the lane.

      * `RemovedNotice` renders `_one_last_migration`, i.e. *"install `mokata==<older>`, run
        `mokata migrate <channel>`, then upgrade again"*. There has never been a `mokata migrate
        neo4j`: this channel's `DeprecationNotice.migration` was the empty string from the day it
        was deprecated, and the notice a user actually read said *"No migration needed — the graph
        is derived data; re-index"*. A remedy naming a command that never shipped is not a remedy.
        Its other load-bearing sentence — *"mokata will NOT hand you an empty store instead"* — is
        also wrong-shaped: the floor beneath a code graph is not empty, it ANSWERS.
      * `RemovedFileNotice` says WHERE the user's bytes are, as a path under `.mokata/`. This
        channel's bytes were never under `.mokata/`, never under the repo, and never on this
        machine. They are in the user's own Neo4j server at `$NEO4J_URI`.

    ⚠ AND `native-memory` — the nearest existing record, an EXTERNAL store mokata also never held —
    still does not fit, because the axis that decides is not internal-vs-external. It is CANONICAL
    vs DERIVED. Those memories were canonical: mokata was the only place they were going to live,
    so they had to be brought ACROSS, and a downgrade-and-migrate was the honest price. A code graph
    is derived from the code, so the canonical graph (doc 85 §6: the embedded AST floor / an adopted
    CRG) RE-DERIVES it. Nothing is brought across because nothing was ever taken away.

    ⚠ SO THE RECORD DOES NOT LEAD WITH "NOTHING WAS DESTROYED" (P22). For a store mokata itself
    opened, that sentence is the news. Here it is trivially true — mokata never had the data — and
    a notice whose first clause is a tautology spends the user's attention on the one thing they
    did not need to be told. What they need is: mokata has stopped querying your server, the
    canonical graph already answers, and here is the one command that clears the dead entry.

    Static text plus a caller-supplied detail — no item content, no URI, no credential, so it
    cannot leak a secret (P23/CM.S1), exactly like the other two notice classes."""

    channel: str            # the stable channel id (what a manifest chain still names)
    what: str               # human name of the removed thing
    where: str              # the system the data is in, and that mokata never owned
    canonical: str          # what answers the same question now, and why it needs no migration
    remedy: str             # the ONE command in THIS release that clears the dead wiring
    removed: str            # REQUIRED + FROZEN — see `RemovedNotice.removed` (E7)

    def render(self, *, detail: str = "", ascii_only: bool = False) -> str:
        glyph = "[removed]" if ascii_only else "✖ removed"
        where = f" {detail}" if detail else ""
        return (f"{glyph}: the {self.what} was REMOVED in mokata {self.removed}, and this repo "
                f"still names `{self.channel}`. mokata has stopped querying it. {self.where}"
                f"{where} {self.canonical} {self.remedy}")


_CARRY_ON = ("To carry on without it, drop it from the chain: "
             "`mokata config set memory_store sqlite`.")


def _one_last_migration(channel: str, extra: str = "") -> str:
    """The remedy sentence — install the last release that shipped the channel, migrate, upgrade.

    The release is `LAST_SHIPPING_RELEASE`, DERIVED (see `last_release_with`), so this sentence
    cannot go on naming a release that is no longer the last one."""
    how = f" (migrate {extra})" if extra else ""
    return (f"To bring it into the canonical store, install the last release that shipped it — "
            f"`pip install 'mokata=={LAST_SHIPPING_RELEASE}'` — run `mokata migrate {channel}`"
            f"{how}, then upgrade again. {_CARRY_ON}")


# A removed FILE channel's location lives on its RECORD (the `path` field above) rather than in any
# live path. This is slice 1's `obsidian_vault_path` precedent, two channels on: a refusal has to
# say WHERE, or the remedy sends a user hunting for a location only mokata ever knew.
#
# ⚠ EVERY ONE OF THESE NAMES A LOCATION AND READS NOTHING, AND NONE MAY EVER BECOME A LEGACY
# READER. Nothing in this module opens a file (`test_stage11_memory_share_channel` grades that over
# the module's own AST); the function below composes a string. A name with no behaviour behind it
# is a record, and a record is the one thing a removed channel still owes its user.


def removed_file_path(channel: str, root: str) -> str:
    """Where `channel`'s bytes are for `root` — read from that channel's OWN record.

    ⚠ THIS REPLACES `removed_share_path`, WHICH COMPOSED `memory-share.json` FOR EVERY FILE
    CHANNEL. Slice 3 filed that as unreachable-today-and-wrong-for-the-second-file-channel; slice 4
    is the second file channel, so it would have told a vault user their session bundles were in a
    memory backup file. Fixed by TYPE, not by adding a name to a branch.

    PURE: joins strings, touches no filesystem. The caller decides whether it EXISTS and hands that
    fact to `removed_file_report`, so the answer is gradable without a tree (§7i)."""
    from . import MOKATA_DIR
    notice = REMOVED[channel]
    if not isinstance(notice, RemovedFileNotice):
        raise KeyError("%r is a removed %s channel, not a removed file channel — it has no file "
                       "location under `.mokata/`" % (channel, type(notice).__name__))
    return os.path.join(root, MOKATA_DIR, notice.path)


# Every `removed=` below is a FROZEN LITERAL and that is the point (E7). These are the only
# release strings in this module besides the declaration's own, and `tests/_deprecation_removal.py`
# grades exactly that: each one must be a release this project HAS reached, and none may claim a
# release later than the declaration promises — you cannot already have removed something in a
# release that has not happened.
REMOVED: Dict[str, Any] = {
    "obsidian": RemovedNotice(
        channel="obsidian", what="Obsidian memory backend",
        data="Your notes have NOT been touched — they are still markdown files on disk.",
        remedy=_one_last_migration("obsidian"), removed="0.0.18"),
    "native-memory": RemovedNotice(
        channel="native-memory", what="native-memory backend",
        data=("native-memory is an EXTERNAL store — mokata never held its data and has deleted "
              "nothing."),
        remedy=_one_last_migration("native-memory", extra="with the client wired"),
        removed="0.0.18"),
    # ⚠ NOTE WHAT IS ABSENT: no `_one_last_migration`. The two above name a release a user has to
    # install to get their data back; this one names a command they already have. That difference
    # is the whole reason `RemovedFileNotice` exists — see its docstring.
    "memory-share": RemovedFileNotice(
        channel="memory-share", what="memory-share.json channel",
        data="mokata has deleted nothing — a memory-share.json is a backup FILE you own.",
        remedy=("This release still READS that file: `mokata memory import --file <path>` "
                "restores it into the canonical store — previewed first, human-gated, secret-"
                "scanned, provenance preserved. The channel file IS a mokata memory backup, so "
                "there is nothing to convert and no older mokata to install."),
        removed="0.0.18", path="memory-share.json"),
    # ⚠ A FILE NOTICE, NOT A BACKEND ONE, AND THE CHOICE IS THE SLICE'S WHOLE ANSWER. What left is
    # the vault SESSION-TRANSPORT kind; what stays is `.mokata/vault/` itself, which is the LIVE
    # design-artifact vault (`mokata vault list/search/pull/push`, `team join --vault`). The
    # channel's bundles were namespaced INSIDE the surviving feature's directory, so a
    # `RemovedNotice` here would be false twice over: mokata WILL still read that directory, and
    # `pip install 'mokata==0.0.17'` would charge a user a downgrade for bundles this release
    # opens. They are `_FileTransport` JSON blobs and the LOCAL transport reads the same shape —
    # the store was a DIRECTORY, never a format.
    #
    # The `vault/` half of the path below is the SURVIVING feature's directory (`vault.
    # VAULT_DIRNAME`) and the `sessions/` half is the dead channel's namespace inside it; the pin
    # derives the first half from `vault.vault_dir` so this record cannot drift off it.
    "vault": RemovedFileNotice(
        channel="vault", what="vault session-transport channel",
        data=("mokata has deleted nothing — your session bundles are still JSON files you own, "
              "and the design-artifact vault they sat inside is UNCHANGED and still supported."),
        remedy=("This release still reads those bundles: move them into "
                "`.mokata/session-bundles/` and `mokata session list` shows them, `mokata session "
                "pull <tag>` resumes one — content-hash verified, human-gated and secret-scanned "
                "exactly as a local bundle always was. There is nothing to convert and no older "
                "mokata to install."),
        removed="0.0.18", path=os.path.join("vault", "sessions")),
    # ⚠ A DERIVED NOTICE, AND THE CHOICE IS THIS STAGE'S WHOLE ANSWER (see `RemovedDerivedNotice`).
    # No `_one_last_migration`, because `mokata migrate neo4j` never existed; no `path`, because
    # the graph was never under `.mokata/`. The two clauses below carry what IS true, and neither
    # of them is "nothing was destroyed": mokata never held this data, so saying so first would
    # spend the notice on a tautology (P22).
    "neo4j": RemovedDerivedNotice(
        channel="neo4j", what="Neo4j code-graph backend",
        where=("Your Neo4j server and the graph in it are untouched — mokata only ever QUERIED "
               "that graph, it never built or stored it, and it has deleted nothing."),
        canonical=("There is nothing to bring across: a code graph is DERIVED data. The canonical "
                   "one already answers here — the embedded AST floor gives real call/import "
                   "edges on a Python repo with no install, and `mokata graph adopt` pins a "
                   "code-review-graph or serena for cross-language depth."),
        remedy=("Clear the dead wiring with `mokata reconfigure --remove neo4j` (reversible, "
                "human-gated, no residue), then `mokata index` to refresh against what answers "
                "now. There is no older mokata to install and nothing to convert."),
        removed="0.0.18"),
}


class RemovedChannelError(MokataError):
    """A repo asked mokata to use a channel this release REMOVED, and it holds data.

    HARD, and deliberately not a `DegradedCapability`: a degrade means there is a floor that gives
    a weaker but TRUE answer, and here there is none. The candidate floor — the empty SQLite
    store — does not answer the question weakly, it answers it wrongly, and reporting "0 items"
    for a store mokata can no longer open is the §7g collapse this class exists to refuse."""


def is_removed_file_channel(channel: str) -> bool:
    """True when `channel`'s removal record is a FILE the user owns, not a backend chain entry.

    The ONE predicate that separates the two kinds, so no caller has to re-derive the distinction
    by listing channel names — a list is what goes stale when slice 4 lands."""
    return isinstance(REMOVED.get(channel), RemovedFileNotice)


def removed_notice(channel: str) -> RemovedNotice:
    """The removal record for a removed BACKEND (KeyError otherwise; `from_`-style contract).

    ⚠ REFUSES A FILE CHANNEL rather than returning it. Its render() carries the downgrade remedy
    and the sentence *"mokata will NOT read it"*, both false of a `memory-share.json` — a caller
    that reached here with one would print a correct-looking refusal telling a user to install an
    older mokata for a file this release opens. `removed_file_report` is that channel's answer."""
    notice = REMOVED[channel]
    if not isinstance(notice, RemovedNotice):
        raise KeyError("%r is a removed %s channel, not a removed backend — its answer is "
                       "`removal_answer`" % (channel, type(notice).__name__))
    return notice


REMOVAL_LEDGER_KIND = "removal_notice"


def warn_removed(channel: str, mokata_dir: str, *, detail: str = "",
                 out: Optional[Callable[[str], None]] = None, ledger: Any = None,
                 ascii_only: bool = False) -> bool:
    """Emit `channel`'s REMOVAL notice ONCE per repo. Same contract as `warn_deprecated`, same
    atomic `O_EXCL` marker mechanism, same degrade-clean behaviour — and a SEPARATE marker.

    ⚠ THE MARKER KEY IS NOT THE CHANNEL, IT IS `<channel>@removed-<filed>`, and that is the whole
    reason this is not a call to `warn_deprecated`. A repo that used the channel already fired
    `obsidian.marker` for the DEPRECATION warn, so keying the removal on the channel alone would
    make the removal notice silent for exactly the repos that have data in it — the users who need
    it. Two notices, two facts, two markers (§7g); both files coexist, neither is read by the
    other, and a downgrade still finds the old one.

    ⚠⚠ THE SECOND HALF OF THE KEY IS `filed=`, NOT THE RELEASE (E7, ruled 2026-08-15). It was the
    release until this slice, which made the notice re-fire on a slipping `at=` — the same removals
    re-announced under a new number, news-free. Keyed on the declaration's filed stamp, a promise
    that slips re-fires nothing and a genuinely NEW removal set re-fires everything, which is the
    ruling in one line. ⚠ It re-fires every removed channel, not only the newly-added one, and that
    is deliberate: the second notice announces a new DECISION, and the set it names is the set as it
    now stands. See `removal_filed`.

    ⚠ AND THIS IS ONLY FOR THE NO-DATA CASE. A repo that HOLDS data in a removed channel gets a
    `RemovedChannelError`, not a once-per-repo line it can scroll past. See
    `memory.selection._refuse_removed_memory_chain`."""
    notice = REMOVED.get(channel)
    if notice is None:
        return False
    marker = _marker_path(mokata_dir, "%s@removed-%s" % (channel, REMOVAL_FILED))
    try:
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    except FileExistsError:
        return False
    except (OSError, ValueError):
        return False                              # degrade-clean, exactly as `warn_deprecated`
    (out or _stderr)(notice.render(detail=detail, ascii_only=ascii_only))
    if ledger is not None:
        try:
            # `filed` rides along with `removed` because they answer different questions and the
            # marker is keyed on the first: `removed` is which release took the channel away,
            # `filed` is which DECISION this notice belongs to. An audit that only carried the
            # release could not tell a re-announcement from the original.
            ledger.record(REMOVAL_LEDGER_KIND, channel=channel, removed=notice.removed,
                          filed=REMOVAL_FILED, scope="repo")
        except Exception:                         # noqa: BLE001 — the notice already fired; the
            pass                                  # audit note is best-effort, never the guard
    return True


def removed_channels_in(chain: Any, kind: Any) -> tuple:
    """The removed channels of record type `kind` that a supplied provider chain still names, in
    the chain's own order.

    A pure function over a SUPPLIED chain (§7i): the tree this ships in holds no manifest that
    names one, so a version that went and read the repo would pass having graded nothing. The
    caller reads `manifest.fallback_order(...)` and hands the list here.

    ⚠ FILTERED BY TYPE, NOT BY LUCK. Since slice 2 the REMOVED registry has held more than one kind
    of record, and the kinds say different things: hand a `memory_store` caller a chain naming
    `memory-share` and a bare membership test returns it, after which `select_memory_backend`
    announces a removed BACKEND (downgrade-to-migrate) about a file this release reads perfectly
    well. The type is what makes them different, so the type is what filters.

    ⚠ AND `kind` IS A REQUIRED ARGUMENT, NOT A DEFAULT, AS OF STAGE 14. It was hardcoded to
    `RemovedNotice` while `memory_store` was the only capability with a removed channel in it.
    `neo4j` is a `code_graph` channel, so there are now two capabilities asking this question about
    two different record types, and a default would have quietly answered the second one with the
    first one's filter — `removed_channels_in(graph_chain)` returning `()` for a chain that names a
    removed graph provider, i.e. the silent fallback this stage exists to prevent, produced by the
    guard meant to catch it. That is `REMOVED-FILE-PATH-IS-HARDCODED-TO-ONE-CHANNEL` (slice 3,
    closed at slice 4) one function over, and pre-1.0 the fix is to fix the call sites (§7d)."""
    return tuple(tool for tool in (chain or ()) if isinstance(REMOVED.get(tool), kind))


def removed_file_report(channel: str, path: str, present: bool,
                        *, ascii_only: bool = False) -> str:
    """The whole answer for a removed FILE channel, over a SUPPLIED `(path, present)`.

    Supplied, never probed, for the §7i reason and one more: the two facts below are the only
    thing that differs between a repo that has the file and a repo that does not, so a function
    that went and looked could only be graded on a tree that happened to have one.

    ⚠ TWO DETAILS, ONE OUTCOME, AND THAT IS THE DIFFERENCE FROM SLICE 1. There, present-vs-absent
    decided between a HARD RAISE and a notice-and-carry-on, because resolution continued in one
    case and could not in the other. Here neither case continues — `mokata migrate memory-share`
    cannot run either way — so the split is not between two outcomes, it is between two true
    sentences, and collapsing them costs the user the only line they act on: print the path
    unconditionally and a repo without the file is invited to run a command that fails; omit it and
    a repo WITH the file is left guessing where mokata thinks it is.

    RAISES for a channel that is not a file channel: a backend's answer is `removed_notice`, and
    quietly rendering one shape as the other is exactly what the two classes exist to prevent."""
    notice = REMOVED[channel]
    if not isinstance(notice, RemovedFileNotice):
        raise KeyError("%r is a removed %s channel, not a removed file channel — its answer is "
                       "`removal_answer`" % (channel, type(notice).__name__))
    detail = (f"Yours is still at {path}." if present
              else f"This repo has none at {path}, so there is nothing here to bring across.")
    return notice.render(detail=detail, ascii_only=ascii_only)


def removal_answer(channel: str, root: str, present: bool = False,
                   *, ascii_only: bool = False) -> str:
    """The whole rendered answer for a REMOVED channel, whichever KIND of record it has.

    The dispatch — file channel → `removed_file_report`, backend → `removed_notice` — stood inside
    `cli_commands/migrate.py` while it had one caller. Slice 4 gives it a second (`session_
    transport.make_transport`, where a repo still naming the vault kind arrives), and two copies of
    a two-branch dispatch is how one of them ends up choosing the class by name instead of by type.
    Chosen by TYPE here, once.

    `present` is SUPPLIED, never probed (§7i) — this module reads no files, and the caller is the
    one that knows whether the location holds anything.

    ⚠ THREE ARMS SINCE STAGE 14, AND THE NEW ONE TAKES NO `present`. A derived channel has no
    location in this repo to be present or absent AT (the graph is on the user's own server), so
    passing `present` down would be inventing a fact the caller cannot have — and a `False` read
    as "this repo has none" is the §7g collapse the split exists to refuse."""
    notice = REMOVED[channel]
    if isinstance(notice, RemovedDerivedNotice):
        return notice.render(ascii_only=ascii_only)
    if is_removed_file_channel(channel):
        return removed_file_report(channel, removed_file_path(channel, root), present,
                                   ascii_only=ascii_only)
    return removed_notice(channel).render(ascii_only=ascii_only)


def deprecation_notice(channel: str) -> DeprecationNotice:
    """The notice for `channel` (always returns for a known channel; `from_`-style contract)."""
    return CHANNELS[channel]


def _marker_path(mokata_dir: str, channel: str) -> str:
    return os.path.join(mokata_dir, TEMP_LOCAL_DIRNAME, _MARKER_DIRNAME,
                        channel.replace("/", "_") + ".marker")


def _stderr(message: str) -> None:
    """Deprecation notices are diagnostics — STDERR, so they never corrupt the stdout/JSON a
    script is parsing (mirrors `degrade._stderr`)."""
    print(message, file=sys.stderr)


def warn_deprecated(channel: str, mokata_dir: str, *, out: Optional[Callable[[str], None]] = None,
                    ledger: Any = None, ascii_only: bool = False) -> bool:
    """Emit `channel`'s deprecation notice ONCE per repo, backed by an atomic `O_EXCL` state
    marker under `<mokata_dir>/temp_local/deprecations/`. Returns True on the firing use, False
    forever after (and on any degrade). `mokata_dir` is the `.mokata` directory.

    Degrade-clean (P8): a marker that can't be written (a broken/read-only temp_local) suppresses
    the notice rather than crashing the read path — a best-effort diagnostic, never a hard failure.
    When a `ledger` is supplied the firing use is recorded (`deprecation_notice`), so a repo's
    first touch of a deprecated channel is auditable long after the line scrolled away."""
    notice = CHANNELS.get(channel)
    if notice is None:
        return False
    marker = _marker_path(mokata_dir, channel)
    try:
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    except FileExistsError:
        return False                              # already warned for this repo (the state marker)
    except (OSError, ValueError):
        # degrade-clean: an unwritable/invalid temp_local path (OSError, or a ValueError for an
        # embedded-null path on Linux) suppresses the notice rather than crashing the read path.
        return False
    (out or _stderr)(notice.render(ascii_only=ascii_only))
    if ledger is not None:
        try:
            ledger.record(DEPRECATION_LEDGER_KIND, channel=channel, removal=notice.removal,
                          scope="repo")
        except Exception:                         # noqa: BLE001 — the notice already fired; the
            pass                                  # audit note is best-effort, never the guard
    return True


DEPRECATED_CHANNELS = tuple(CHANNELS)
REMOVED_CHANNELS = tuple(REMOVED)
