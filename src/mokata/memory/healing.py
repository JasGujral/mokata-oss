"""C5 — self-healing, SURFACING form only.

Detection is pure and read-only: it scans active items and returns *proposals*. It NEVER
writes. Resolution (apply) is human-gated and lives in the store. The whole design
defaults to no change unless the user explicitly approves or edits — memory is never
silently rewritten. Autonomous consolidation (C7) is deliberately out of scope.

DB.S6 adds the CROSS-WRITER arm. Writer divergence used to exist in exactly one place — a
flush-time CAS conflict, resolved by a prompt inside `mokata sync` — so it was invisible to every
surface a user actually reads. A conflict now becomes a `HealingProposal` like any other issue,
and it arrives here as PLAIN FIELDS (`ConflictRecord`): this module knows nothing about the team
journal, its `ConflictView`, or Postgres, and the store does the projection at its own boundary.
That is what keeps an L2 domain module from importing the L3 collab layer, and what lets the arm
stay additive — DB.S7/K2 extends it by widening `ConflictRecord`, not by rewriting the detector.

DB.S7c1 (K2) TOOK THAT PROMISE AT ITS WORD, and it is worth recording that it held: edge-awareness
arrived as two DEFAULTED dataclass fields plus ONE branch in `detect_cross_writer`. `detect_issues`
is byte-for-byte the function DB.S6 shipped — the staleness loop, the canonical-subject grouping
and the near-dup bar are untouched, and a `git diff` of that function is empty. A conflict that
carries no edge context produces the identical proposal it produced before this stage.

**WHAT K2 IS, AND THE THIRD OF IT THAT COULD NOT BE BUILT.** Doc 55:45 specs K2 as three things:
(1) a proposal shows the subgraph it rewrites; (2) CAS covers edges too; (3) contradiction
detection queries `contradicts`/`supersedes` structure instead of full scans. (1) and (2) are here.
(3) IS NOT, and not because it was overlooked: `contradicts` is a DECLARED-BUT-UNWIRED kind
(`edges.py:80`, doc 02 decision #2) — contradiction is detected at READ time by `detect_issues`
below and never persisted as a relation, so there is no producer and no row to query. Building it
would mean INVENTING edges, which is exactly what the substrate stage refused to do. Filed as
K2-CONTRADICTS-UNBUILDABLE in doc 84 against a release where a `contradicts` producer lands, rather
than half-built here against edges nothing writes.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .edges import ITEM_TARGET_KINDS, MemoryEdge
from .item import MemoryItem, now_iso
from .lifecycle import parse_iso

CONTRADICTION = "contradiction"
STALE = "stale"
# DB.S6 — two active facts on ONE canonical subject whose VALUES are near-identical: a restatement,
# not a disagreement. Surfaced separately so a trivially-reworded duplicate does not read as a
# contradiction (which proposes retiring a fact that was never wrong).
NEAR_DUP = "near_dup"
# DB.S6 — a durable write that lost the compare-and-set against a teammate's concurrent change.
CROSS_WRITER = "cross_writer"
# H-6 S3 — an `about_code` anchor whose code has MOVED since the decision was recorded. Its own
# kind, deliberately NOT `STALE`: `STALE` means a TTL elapsed, i.e. the fact stopped being true on
# a schedule its author set. This says something else entirely — the decision may be perfectly
# valid, and the CODE underneath it changed. Folding them together would tell a human their
# decision expired when nothing of the sort happened.
CODE_ANCHOR_STALE = "code_anchor_stale"

# R5 — the near-dup bar, deliberately CONSERVATIVE. Cosine over the embedding the gated write
# already stamped; 0.97 is close to identity for a normalized embedding, so paraphrases of one fact
# clear it and merely RELATED values (which routinely sit at 0.85–0.95) do not. Erring high is the
# safe direction: a missed near-dup is surfaced as a contradiction — visible, reviewable, no fact
# lost — whereas a false near-dup would quietly recast a real disagreement as a duplicate.
NEAR_DUP_COSINE = 0.97


@dataclass(frozen=True)
class ConflictSubgraph:
    """DB.S7c1 (K2) — the OPEN relations a resolution of this conflict would re-project.

    **This is the subgraph the proposal REWRITES, and "rewrites" is meant literally rather than as
    a synonym for "is near".** `edges.project_edges` maintains the projection *per src*: on the
    winning write it closes the refs the winner withdrew and opens the ones it asserts, all keyed
    on `src_id`. So the set that a resolution actually changes is exactly the OPEN edges OUT of the
    conflicted item — one hop, forward. That is why this carries a point read rather than DB.S7b's
    ≤2-hop walk: a second hop is reached-but-not-rewritten, and showing it beside the rewritten set
    under one heading would tell a human that resolving this touches relations it does not touch.
    The right home for "what else breaks downstream" is blast-radius (K3), which is a different
    question with a different answer.

    DB.S7b's forward-only limitation is therefore NOT a limitation here, and the coincidence is
    worth stating so nobody "fixes" it: the direction the traversal can walk (`src → dst`) is the
    same direction the projection is keyed on, so for this feature forward-only is not a gap but
    the correct axis. The REVERSE question ("which items depend on the one you are about to
    overwrite") is real, unanswered, and deliberately out of scope for c1.

    `truncated` exists for the same reason every DB.S7b bound reports itself: a bounded read that
    trims silently reads as a complete one, and a human deciding a conflict from a subgraph they
    believe is whole is precisely the failure this stage exists to prevent.
    """

    edges: Tuple[MemoryEdge, ...] = ()
    truncated: int = 0

    def __bool__(self) -> bool:
        return bool(self.edges)

    def counts(self) -> Dict[str, int]:
        """Edges per kind — the summary line's material, computed once."""
        out: Dict[str, int] = {}
        for e in self.edges:
            out[e.kind] = out.get(e.kind, 0) + 1
        return out

    def summary(self) -> str:
        """One phrase: how many relations a resolution re-projects, and of what kinds."""
        if not self.edges:
            return "no open relations"
        kinds = ", ".join(f"{n}× {k}" for k, n in sorted(self.counts().items()))
        noun = "relation" if len(self.edges) == 1 else "relations"
        tail = f" (+{self.truncated} more not shown)" if self.truncated else ""
        return f"{len(self.edges)} open {noun} re-projected: {kinds}{tail}"

    def render_lines(self) -> List[str]:
        """The subgraph as the gate shows it — one line per relation, deterministic order."""
        lines = [f"  rewrites: {self.summary()}"]
        for e in sorted(self.edges, key=lambda x: (x.kind, x.dst_id)):
            lines.append(f"    - {e.kind} → {e.dst_id}")
        if self.truncated:
            lines.append(f"    … {self.truncated} more relation(s) not shown (bounded read)")
        return lines


def prune_subgraph(edges: Sequence[MemoryEdge], visible: Optional[set],
                   cap: int) -> ConflictSubgraph:
    """Build a `ConflictSubgraph` from an item's open edges: scope-prune, then bound.

    **Scope prunes ITEM targets only, and the asymmetry is deliberate.** An edge whose dst is
    another memory item is subject to memory scope — showing its id would disclose both the id and
    the relation to someone who may not read it, which is the leak DB.S7b's decision 4 refuses. An
    `about_code` dst is a CODE PATH, not a memory item: it is not in the visible set, never will
    be, and pruning it against a set of item ids would silently delete every code anchor from the
    display while looking like a scope rule. So item-target kinds are filtered and non-item-target
    kinds pass through, and `visible=None` (no scope context — the local zero-config case) prunes
    nothing at all.

    The cap is DB.S7b's `MAX_WALKED_EDGES`, imported rather than re-chosen: one bounded-edge-read
    budget for the whole package, so raising it is one edit and cannot half-land. Truncation is
    RETURNED, never swallowed.
    """
    kept: List[MemoryEdge] = []
    for e in edges:
        if visible is not None and e.kind in ITEM_TARGET_KINDS and e.dst_id not in visible:
            continue
        kept.append(e)
    kept.sort(key=lambda e: (e.kind, e.dst_id))
    if len(kept) > cap:
        return ConflictSubgraph(edges=tuple(kept[:cap]), truncated=len(kept) - cap)
    return ConflictSubgraph(edges=tuple(kept))


@dataclass
class ConflictRecord:
    """A cross-writer conflict, projected into PLAIN fields by the store (R3).

    Deliberately NOT the journal's `ConflictView`: `local` is this machine's approved-but-unlanded
    write, `remote` is what the shared row actually holds (None when the remote state could not be
    read), and `conflict_id` is the opaque handle the store hands back to the resolver. Nothing in
    this module interprets it — it only carries it, which is why the collab layer can change its
    own types without reaching in here."""

    conflict_id: str
    key: str
    detail: str
    local: MemoryItem
    remote: Optional[MemoryItem] = None
    remote_revision: Optional[int] = None
    # DB.S7c1 (K2) — the edge neighbourhood a resolution re-projects, attached by the STORE (which
    # owns the backend) rather than read here: this module opens no connection and never will.
    # DEFAULTED, so every DB.S6 construction site — the CLI edit path, `share.py`, the tests —
    # constructs an identical record without knowing this field exists.
    subgraph: Optional[ConflictSubgraph] = None


@dataclass
class AnchorStaleness:
    """H-6 S3 — a MOVED `about_code` anchor, projected into PLAIN fields by the store.

    Exactly the `ConflictRecord` shape and for exactly the same reason: this module knows nothing
    about the code graph, file hashing, or `knowledge.anchor_fingerprints`, and it opens no file.
    The store computes the verdict at its own boundary and hands the result down, which is what
    keeps an L2 memory module from acquiring a filesystem read on a detection path.

    `shape` is `path` or `symbol` and it is NOT decoration — it selects which of two DIFFERENTLY
    WORDED claims this proposal is allowed to make (H-6 plan of record, decision #2). `path` names
    the file(s) whose content hash actually moved.
    """

    item: MemoryItem
    anchor: str
    shape: str
    path: str = ""


@dataclass
class HealingProposal:
    kind: str                       # CONTRADICTION | STALE | NEAR_DUP | CROSS_WRITER
                                    # | CODE_ANCHOR_STALE
    subject: str
    mtype: str
    old: MemoryItem                 # the item proposed to change
    new: Optional[MemoryItem]       # the winning fact (contradiction), or None (stale)
    rationale: str
    # DB.S6 — set ONLY on a CROSS_WRITER proposal: the journal handle the one resolver
    # (`MemoryStore.apply_proposal`) uses to settle the conflict. Additive with a default, so every
    # existing construction site (the CLI edit path, the tests, `share.py`) is untouched.
    conflict_id: Optional[str] = None
    remote_revision: Optional[int] = None
    # H-6 S3 — set ONLY on a CODE_ANCHOR_STALE proposal: which anchor moved, and of which SHAPE.
    # Carried so the resolver and the STALE-REF refusal (S4) can name the same anchor the human was
    # shown; `shape` is what the wording pin is asserted against.
    anchor: Optional[str] = None
    shape: Optional[str] = None
    # DB.S7c1 (K2) — carried, never derived here: the proposal shows the subgraph the STORE read,
    # not one re-inferred from `old.depends_on` / `old.supersedes`. Same rule DB.S7b pinned for
    # "why surfaced", and for the same reason: the inline fields would render a fluent, plausible
    # sentence about relations the shared table may not actually hold, and a human resolving a
    # conflict against invented context is worse off than one shown none.
    subgraph: Optional[ConflictSubgraph] = None

    def diff(self) -> str:
        if self.kind == CODE_ANCHOR_STALE:
            # NOT the `-> stale` line the TTL arm renders. Nothing about the FACT changed, so a
            # diff that showed the value going stale would be describing a change that did not
            # happen. What moved is named instead.
            return f"{self.anchor} moved (the decision itself is unchanged)"
        if self.kind == CROSS_WRITER:
            theirs = "unreadable" if self.new is None else repr(self.new.value)
            return f"yours {self.old.value!r} vs theirs {theirs}"
        if self.kind == NEAR_DUP and self.new is not None:
            return f"{self.old.value!r} ≈ {self.new.value!r}"
        if self.kind == CONTRADICTION and self.new is not None:
            return f"{self.old.value!r} -> {self.new.value!r}"
        return f"{self.old.value!r} (active) -> stale"


# --------------------------------------------------------------------------- canonicalization
def canonical_subject(subject: str) -> str:
    """The GROUPING key for a subject — never a rewrite of it.

    Exact-string matching made contradiction detection "useless across 5 writers" (doc 52 #5):
    `DB Host`, `db_host` and `db-host` are one fact typed three ways, and the disagreement between
    them never surfaced. Canonicalization folds case and every separator (underscore, hyphen, dot,
    slash, punctuation, runs of whitespace) into one space-delimited form, so those three group.

    What it deliberately does NOT do is stem, singularize, or drop qualifiers. `db host` and
    `db hosts` stay distinct; so do `db host` and `staging db host`. That asymmetry is the whole
    safety argument: a MISSED grouping costs a contradiction that stays invisible until someone
    reads both facts, while a WRONG grouping makes mokata propose retiring a fact that was never
    wrong. The near-miss table in the tests is the executable form of this boundary.

    The result is used as a dict key only. Every proposal renders the subject the human actually
    typed — a normalized string is never shown back as if it were their words."""
    out: List[str] = []
    prev_space = True
    for ch in subject or "":
        if ch.isalnum():
            out.append(ch.lower())
            prev_space = False
        elif not prev_space:
            out.append(" ")
            prev_space = True
    return "".join(out).strip()


def _embedding(item: MemoryItem) -> Optional[Sequence[float]]:
    """The embedding the gated write stamped, or None. Never computes one — detection is a READ
    path and embedding here would both write to the item and (for a real embedder) reach the
    network on what must stay a pure, offline scan."""
    vec = (item.provenance or {}).get("_embedding")
    if isinstance(vec, (list, tuple)) and vec:
        return vec
    return None


def _is_near_dup(a: MemoryItem, b: MemoryItem) -> bool:
    """R5 — are these two values near-duplicates? Requires BOTH sides to carry an embedding, so a
    store with no embedder consented (or one mid-`memory reembed`) degrades to exactly the
    pre-DB.S6 behaviour rather than guessing. Called only INSIDE a canonical-subject group: value
    similarity alone never groups anything."""
    va, vb = _embedding(a), _embedding(b)
    if va is None or vb is None:
        return False
    from .embed import cosine
    return cosine(list(va), list(vb)) >= NEAR_DUP_COSINE


# --------------------------------------------------------------------------- detection
def detect_issues(active_items: List[MemoryItem],
                  now: Optional[str] = None,
                  conflicts: Optional[Sequence[ConflictRecord]] = None,
                  anchor_staleness: Optional[Sequence["AnchorStaleness"]] = None
                  ) -> List[HealingProposal]:
    """Return healing proposals for the given ACTIVE items (and cross-writer conflicts, DB.S6, and
    moved code anchors, H-6).

    Read-only; writes nothing — not a row, not a file, not a directory. `conflicts` and
    `anchor_staleness` both default to None so every earlier caller is byte-identical."""
    now = now or now_iso()
    proposals: List[HealingProposal] = []

    # Staleness: an item whose TTL has elapsed.
    #
    # I7 — compared as INSTANTS, not as ISO strings. The old `it.expires_at < now` sorted text, so
    # two stamps for the same moment written by writers in different timezones ordered by their
    # offset: `…T09:00:00+02:00` (07:00Z) sorted AFTER `…T08:00:00+00:00` and an expired fact read
    # as live. An unparseable stamp yields no proposal at all — the safe direction, since the
    # alternative is proposing to retire a live fact because its timestamp is malformed.
    ref = parse_iso(now)
    for it in active_items:
        if not it.expires_at:
            continue
        expiry = parse_iso(it.expires_at)
        if expiry is None or ref is None or expiry >= ref:
            continue
        proposals.append(HealingProposal(
            kind=STALE, subject=it.subject, mtype=it.mtype, old=it, new=None,
            rationale=f"valid_for elapsed (expired {it.expires_at})",
        ))

    # Contradiction / near-dup: two+ active items on ONE canonical subject with different values.
    groups: Dict[Any, List[MemoryItem]] = {}
    for it in active_items:
        groups.setdefault((it.mtype, canonical_subject(it.subject)), []).append(it)
    for (mtype, _canon), grp in groups.items():
        if len({g.value for g in grp}) <= 1:
            continue
        ordered = sorted(grp, key=lambda g: (g.created_at, g.id))
        old, new = ordered[0], ordered[-1]
        if _is_near_dup(old, new):
            proposals.append(HealingProposal(
                kind=NEAR_DUP, subject=old.subject, mtype=mtype, old=old, new=new,
                rationale=("two active facts restate the same thing (near-identical values on one "
                           "subject) — a duplicate, not a disagreement"),
            ))
            continue
        proposals.append(HealingProposal(
            kind=CONTRADICTION, subject=old.subject, mtype=mtype, old=old, new=new,
            rationale="two active facts disagree; newest proposed to supersede oldest",
        ))

    proposals.extend(detect_cross_writer(conflicts or ()))
    proposals.extend(detect_code_anchor_staleness(anchor_staleness or ()))
    return proposals


def detect_cross_writer(conflicts: Sequence[ConflictRecord]) -> List[HealingProposal]:
    """DB.S6 — one CROSS_WRITER proposal per surfaced conflict.

    `old` is YOUR approved-but-unlanded write and `new` is what the shared row actually holds, so
    the existing old→new rendering reads correctly for a conflict too. Kept as its own function so
    the arm is additive: DB.S7/K2 widens `ConflictRecord` and adds a branch here rather than
    rewriting `detect_issues`.

    DB.S7c1 — that is exactly what happened. The ONE branch below is the whole of K2's detection
    change; a record with no subgraph takes the `else`-shaped path and yields the DB.S6 proposal
    unchanged, field for field."""
    out: List[HealingProposal] = []
    for c in conflicts:
        rationale = (f"a teammate changed this row while your approved write was waiting to "
                     f"land ({c.detail})")
        # THE ONE BRANCH (DB.S7c1/K2). A conflict that carries edge context says what resolving it
        # re-projects; one that does not is byte-identical to DB.S6. Note it reads `if c.subgraph`
        # and not `if c.subgraph is not None`: an EMPTY subgraph (the item has no open relations,
        # which is the common case) must render like the pre-K2 proposal rather than announce "0
        # relations" at every gate — an honest nothing is better said by silence.
        if c.subgraph:
            rationale += f"; resolving it re-projects {c.subgraph.summary()}"
        out.append(HealingProposal(
            kind=CROSS_WRITER, subject=c.local.subject, mtype=c.local.mtype,
            old=c.local, new=c.remote,
            rationale=rationale,
            conflict_id=c.conflict_id, remote_revision=c.remote_revision,
            subgraph=c.subgraph,
        ))
    return out


# --------------------------------------------------------------------------- H-6 S3: code anchors
# THE WORDING PIN (H-6 plan of record, decision #2 — LOCKED).
#
# The two anchor shapes rest on different evidence, so they are allowed to make different claims,
# and the words have to keep them apart. A PATH anchor's evidence is a file content hash: it
# establishes that the FILE is not the file we recorded, and NOTHING about the symbol the decision
# was really about — a whitespace edit three hundred lines from that symbol moves the hash just as
# loudly as a rewrite of it. So the path wording states the file changed, says plainly that the
# decision itself may still be right, and never reaches for "symbol", "defines" or "definition".
#
# A SYMBOL anchor's evidence is stronger and was more expensive to get: an AUTHORITATIVE graph
# named the definition site, and THAT file moved. It is allowed to say so, and it names both the
# symbol and the site so a human can check the claim rather than take it.
#
# Collapsing these into one string is the failure this pin exists to stop, and it is the ordinary
# kind of failure: one message is tidier, reads well, and quietly upgrades every path anchor's
# claim to the symbol arm's. The tests assert the two strings differ AND that the path one carries
# no symbol-claiming vocabulary.
_PATH_RATIONALE = (
    "the file this decision is anchored to has changed since the decision was recorded ({path}). "
    "The decision itself is unchanged and may well still be right — what mokata can see is that "
    "the file underneath it is no longer the file that was there. Re-read it and decide."
)
_SYMBOL_RATIONALE = (
    "the code graph names {path} as the definition site of {anchor}, and that file has changed "
    "since this decision was recorded. This is the anchored code itself moving, not a "
    "neighbouring edit."
)


def detect_code_anchor_staleness(moved: Sequence[AnchorStaleness]) -> List[HealingProposal]:
    """H-6 S3 — one proposal per MOVED anchor.

    Pure and read-only, like every other arm in this module: it is handed verdicts and turns them
    into proposals. It opens no file, asks no graph and re-stamps no record — the anchor keeps
    proposing until a HUMAN decides, which is P7 (`govern/stale_ref_gate.py:21-24`'s rule applied
    to this half). A silent re-stamp would turn "the code under this decision moved" into "the code
    under this decision moved, quietly relabelled as current".

    `new` is None: nothing supersedes the item. That is the whole shape of this proposal — the FACT
    is untouched and the code beneath it is what moved.
    """
    out: List[HealingProposal] = []
    for m in moved or []:
        if m.shape == "symbol":
            rationale = _SYMBOL_RATIONALE.format(anchor=m.anchor, path=m.path or "an unnamed file")
        else:
            rationale = _PATH_RATIONALE.format(path=m.path or m.anchor)
        out.append(HealingProposal(
            kind=CODE_ANCHOR_STALE, subject=m.item.subject, mtype=m.item.mtype,
            old=m.item, new=None, rationale=rationale,
            anchor=m.anchor, shape=m.shape,
        ))
    return out


# Clean-room surface-and-approve prompt — show the change, default to NO change.
def render_proposal(p: HealingProposal) -> str:
    lines = [
        f"mokata · memory needs your decision ({p.kind})",
        f"  subject: [{p.mtype}] {p.subject}",
        f"  change:  {p.diff()}",
        f"  why:     {p.rationale}",
        "",
    ]
    if p.kind == CROSS_WRITER and p.subgraph:
        # DB.S7c1 (K2) — the subgraph goes ABOVE the choices, because it is evidence for the
        # decision rather than a footnote to it: "keep yours" and "keep theirs" mean different
        # things depending on what else the row holds up, and a human reading the options first
        # has already started deciding.
        lines[-1:-1] = p.subgraph.render_lines()
    if p.kind == CROSS_WRITER:
        # A conflict is not a rewrite of your memory — it is a fork between two approved writes, so
        # the three outcomes have to be named explicitly. The DEFAULT is neither side: an
        # un-decided conflict stays conflicted, which is the only choice that loses nobody's work.
        lines += [
            "Nothing changes unless you act. Choose:",
            "  approve — keep YOURS (your write overwrites the remote row)",
            "  discard — keep THEIRS (your local write is dropped)",
            "  defer   — decide later (the write stays conflicted, nothing is lost)",
            "Default is DEFER — a conflict is never resolved by silence, and 'discard' is a "
            "word you have to say: it throws away a write you already approved.",
        ]
    else:
        lines += [
            "Nothing changes unless you act. Choose: approve / edit / reject.",
            "Default is REJECT — memory is never rewritten without your say-so.",
        ]
    return "\n".join(lines)
