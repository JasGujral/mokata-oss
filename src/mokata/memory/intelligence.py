"""Stage 59 — memory intelligence (the retention moat): explainable retrieval + health nudge.

The longer you use mokata, the smarter and more trustworthy its memory should feel. This
module adds two READ-ONLY, deterministic touches over the existing memory primitives — it
rebuilds NONE of them:

  * EXPLAINABLE RETRIEVAL — `why_surfaced(query, item)` (and the `explain_recall` pairing /
    the `RetrievalHit.explain` method) gives every recall hit a short, frugal "why it
    surfaced" phrase: which query token matched, whether a graph anchor or a semantic
    neighbour pulled it in, and its kind. Pure + deterministic (no LLM, no wall-clock); ONE
    short phrase per hit, so the JIT frugality bound (top-k, no corpus dump) still holds.

  * PROVENANCE HIGHLIGHT (R9) — `provenance_lines(item)` renders where an EXISTING item came
    from (who wrote it, from what source, when) and who approved it, for the human staring at a
    gate prompt that is about to overwrite it. The gate-side twin of `why_surfaced`, and it
    inherits that function's discipline exactly: say only what the data proves, and say "unknown"
    out loud rather than filling a gap with something plausible. Doc 83's poisoning row is the
    whole reason it exists — "a poisoned proposal a human rubber-stamps still lands" — and a
    provenance panel that guessed would make rubber-stamping easier, not harder.

  * MEMORY-HEALTH NUDGE — `assess_health` / `MemoryHealth.nudge()` turns the existing
    self-healing detection (stale / contradictory, from `detect_issues`) and the C8
    read/write ratio (the UNUSED-memory signal) into ONE actionable line that points at the
    GATED review path (`mokata memory` / `mokata govern`). It SURFACES only — it NEVER edits
    or prunes memory (that stays the human-gated `apply_proposal` path). Degrade-clean: a
    healthy store nudges nothing (the line is empty).

The auto-proposed GUARDRAILS (recurring corrections → rule promotion PROPOSALS) are already
the `govern.learning.learn_from_ledger` primitive; Stage 59 only SURFACES those proposals on
the onboard / rules surfaces (proposal-only, human-gated — never auto-added).

Inviolables: read-only + deterministic for retrieval-explain + nudge derivation (no stat
bumps beyond the existing recall instrumentation); proposal-only + human-gated for any memory
edit or rule promotion; frugal/bounded; degrade-clean; core dependency-free; clean-room;
Apache-2.0.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Any, List, Optional

from ..errors import DegradedCapability
from .healing import CONTRADICTION, CROSS_WRITER, NEAR_DUP, STALE
from .item import ALWAYS_ON_KINDS

# A dependency-free word tokenizer matching the lexical-score tokenizer (clean-room copy so
# this module doesn't reach into episodic's privates) — so the matched token we name is
# exactly the token the lexical tier scored on.
_WORD = re.compile(r"[a-z0-9]+")

# How many matched tokens to name in a "why" phrase — frugal: one short phrase, never a list.
_MAX_NAMED_TOKENS = 2


def _tokens(text: str) -> set:
    return set(_WORD.findall(text.lower()))


def _text(item: Any) -> str:
    return f"{item.subject} {item.value}"


# ----------------------------------------------------------------- explainable retrieval
def why_surfaced(query: str, item: Any, *, tiers: Optional[dict] = None,
                 path: Any = None) -> str:
    """A short, deterministic "why it surfaced" phrase for one recall hit — frugal (ONE
    phrase). Names the strongest signal that pulled the item in, plus its kind:

        [context] matched "auth"               (lexical keyword overlap — the JIT floor)
        [reference] semantically near          (an embedding neighbour — semantic tier)
        [context] graph-anchored "load_config" (a code-graph anchor — graph tier)
        [decision] via depends_on → decided_in (DB.S7b — reached by a ≤2-hop edge walk)
        [guardrail] always-on (project rule)   (an always-on rule/guardrail, no query signal)

    `tiers` is the optional per-tier score dict from a `RetrievalHit` (lexical/graph/semantic);
    without it (a bare `jit_recall` MemoryItem) the phrase falls back to the matched token.
    Read-only; computes nothing durable.

    **DB.S7b — `path` is the ACTUAL walked route (an `expansion.ExpansionPath`), and it is the ONLY
    thing this function may say a traversal from.** The item's own inline `supersedes` /
    `depends_on` / `about_code` lists are deliberately NOT consulted: they would let this render a
    fluent, plausible sentence about a relation the traversal never crossed — a "why" that is
    wrong precisely when it matters, since the reader's whole reason for asking is that the item
    did not match their query. If no path reached this hit, no path is claimed.
    """
    kind = getattr(item, "effective_kind", "") or "memory"
    matched = sorted(_tokens(query) & _tokens(_text(item)))

    # The PATH leads, when there is one: an item reached by a hop is one the query did not match,
    # so "matched nothing, but here is the route" is the only honest thing to say about it.
    if path is not None:
        steps = getattr(path, "steps", ()) or ()
        if steps:
            chain = " → ".join(s.kind for s in steps)
            hops = "hop" if len(steps) == 1 else "hops"
            return f'[{kind}] via {chain} ({len(steps)} {hops} from "{path.seed}")'

    if tiers:
        sem = float(tiers.get("semantic", 0.0) or 0.0)
        grp = float(tiers.get("graph", 0.0) or 0.0)
        lex = float(tiers.get("lexical", 0.0) or 0.0)
        # Name the dominant tier (semantic strongest, then graph), lexical as the floor.
        if sem > 0.0 and sem >= grp and sem >= lex:
            return f'[{kind}] semantically near your query'
        if grp > 0.0 and grp >= lex:
            anchor = f' "{matched[0]}"' if matched else ""
            return f'[{kind}] graph-anchored{anchor}'

    if matched:
        named = '" / "'.join(matched[:_MAX_NAMED_TOKENS])
        return f'[{kind}] matched "{named}"'
    # No query signal at all (e.g. an always-on rule injected, not recalled by a query).
    if kind in ALWAYS_ON_KINDS:
        return f'[{kind}] always-on (project {kind})'
    return f'[{kind}] relevant to your query'


# ----------------------------------------------------------------- R9 · provenance highlight
# What an absent fact renders as. ONE word, used for every field, so a reader learns it once and
# it can never be confused with a value: an author literally named "unknown" is not a thing this
# renders, because the word only ever appears where the doc carried nothing.
UNKNOWN = "unknown"

# The advisory label on `approved_by`. Doc 52 M-1 bound M-1's attribution this way and the binding
# holds here: `team_audit.actor()` reads a name out of the environment, so it ATTRIBUTES rather
# than authenticates, and a surface that showed it bare would be claiming more than mokata knows.
# `approval_ledger_id` carries no such label on purpose — it names a hash-chained entry, and that
# one IS checkable (`mokata audit`).
ADVISORY = "advisory"


def _field(value: Any) -> str:
    """One provenance field as text, or UNKNOWN. Total: any type, any junk, never raises."""
    try:
        text = str(value).strip() if value is not None else ""
    except Exception:                     # pragma: no cover - a __str__ that raises
        return UNKNOWN
    return text or UNKNOWN


def provenance_lines(item: Any, *, label: str = "provenance") -> List[str]:
    """R9 — the provenance block for an item a gate is about to CHANGE. Read-only, and TOTAL.

    Rendered at every gate/review surface that overwrites, retires or re-scopes an item that
    already exists, because that is the moment doc 83 is about: the human is being asked to
    approve a change to content someone else wrote and someone else approved, and until now the
    prompt showed neither. `render_write`'s own surface showed the mtype, the subject and the
    value and nothing else — a poisoned edit to a trusted memory looked exactly like a clean one.

    Two rules, and they are the whole design:

    **Say only what the doc proves.** Every field is read straight off the item; nothing is
    derived, defaulted, or inferred from a neighbouring field. An unstamped item renders
    `approved: unknown` — NOT the current actor, not `created_at` standing in for `approved_at`,
    not "you". This is `why_surfaced`'s rule ("if no path reached this hit, no path is claimed")
    applied to the surface where being plausibly wrong is most expensive: the reader's whole
    reason for looking is that they do not already know where this came from.

    **Never raise into the gate.** A provenance dict is doc JSON — it can be hand-edited,
    imported, or written by a build that modelled it differently, so it can be any shape at all.
    A render that threw on a malformed one would take out the APPROVAL PROMPT, turning a cosmetic
    defect into an inability to approve anything about that item. Every read is defensive and
    every failure degrades to UNKNOWN, which is both honest and harmless.

    Returns lines (not a string) so each caller controls its own indentation and can drop the
    block entirely; empty `item` yields no block rather than a block full of unknowns.
    """
    if item is None:
        return []
    prov = getattr(item, "provenance", None)
    if not isinstance(prov, dict):
        prov = {}

    author = _field(prov.get("author"))
    source = _field(prov.get("source"))
    created = _field(prov.get("created_at"))
    approver = _field(getattr(item, "approved_by", ""))
    approved_at = _field(getattr(item, "approved_at", ""))
    ledger_id = getattr(item, "approval_ledger_id", None)

    # The approval line names the ledger entry when there is one, because that is the element a
    # reader can actually go and check. With no id there is nothing to point at, and saying so is
    # the point — an item approved through a path that recorded nothing must not look verified.
    if isinstance(ledger_id, int) and not isinstance(ledger_id, bool):
        approval = f"{approver} ({ADVISORY}) at {approved_at} · ledger #{ledger_id}"
    elif approver != UNKNOWN:
        approval = f"{approver} ({ADVISORY}) at {approved_at} · no ledger entry recorded"
    else:
        approval = f"{UNKNOWN} — this item carries no recorded approval"

    return [
        f"  {label}:",
        f"    written by : {author} (source: {source}, {created})",
        f"    approved by: {approval}",
    ]


def provenance_block(item: Any, *, label: str = "provenance") -> str:
    """`provenance_lines` as a trailing block, or "" when there is nothing to show. Convenience for
    the renders that build one f-string."""
    lines = provenance_lines(item, label=label)
    return ("\n" + "\n".join(lines)) if lines else ""


@dataclass
class RecallExplanation:
    """One recall hit paired with its short, deterministic "why it surfaced" phrase."""
    item: Any
    why: str

    def line(self) -> str:
        """`- <subject>: <value>  ↳ <why>` — the hit rendered WITH its reason (frugal)."""
        return f"- {self.item.subject}: {self.item.value}  ↳ {self.why}"


def explain_recall(query: str, results: List[Any]) -> List[RecallExplanation]:
    """Pair each recall result with its "why it surfaced" phrase. Accepts BOTH a list of
    `RetrievalHit` (from `recall_relevant` — uses its per-tier scores) and a list of bare
    `MemoryItem` (from `jit_recall` — lexical floor). Read-only + deterministic; preserves
    the input order/bound (no extra items — the JIT frugality bound is the caller's top-k)."""
    out: List[RecallExplanation] = []
    for r in results:
        if hasattr(r, "item") and hasattr(r, "tiers"):       # a RetrievalHit
            # DB.S7b — `path` is read off the hit, never reconstructed here. `getattr` with a
            # default so a hand-built hit (or a third-party one) predating the field still works.
            item, tiers, path = r.item, r.tiers(), getattr(r, "path", None)
        else:                                                # a bare MemoryItem
            item, tiers, path = r, None, None
        out.append(RecallExplanation(item=item,
                                     why=why_surfaced(query, item, tiers=tiers, path=path)))
    return out


# ----------------------------------------------------------------- memory-health nudge
# The read/write ratio is the UNUSED-memory signal (C8): when more is captured than is ever
# recalled (reads < writes), the surplus writes are memory that isn't earning its keep. This
# is a NUDGE, not a precise per-item count — derived purely from the existing C8 counters.
UNUSED_FLOOR_RATIO = 1.0


def _unused_count(reads: int, writes: int) -> int:
    """The UNUSED-memory count from the C8 read/write ratio: writes not yet balanced by a
    recall (writes - reads), or 0 when memory is being read at least as often as written.
    Deterministic; derived only from the existing counters (no new instrumentation)."""
    if writes > 0 and reads < writes:
        return writes - reads
    return 0


@dataclass
class MemoryHealth:
    """A read-only health read of the memory store: the self-healing backlog (stale /
    contradictory) plus the UNUSED-memory count from the C8 ratio. PROPOSAL-ONLY — it points
    at the gated review path; it carries NO power to edit or prune memory."""
    stale: int
    contradictory: int
    unused: int
    reads: int
    writes: int
    # DB.S6 — additive WITH defaults, so every existing construction site and every pinned
    # zero-state read is unchanged. `cross_writer` is the count that makes I2a true: a teammate's
    # concurrent change is visible on the health surface without anyone running `mokata sync`.
    cross_writer: int = 0
    near_dup: int = 0

    @property
    def total_issues(self) -> int:
        return (self.stale + self.contradictory + self.unused
                + self.cross_writer + self.near_dup)

    @property
    def healthy(self) -> bool:
        return self.total_issues == 0

    def nudge(self, *, ascii_only: bool = False) -> str:
        """The ONE actionable line — `N stale · M contradictory · K unused — review with
        \\`mokata memory\\` / \\`mokata govern\\`` — or `""` when healthy (degrade-clean / silent).
        It points ONLY at the gated review path; it never edits or prunes memory itself."""
        if self.healthy:
            return ""
        dot = " - " if ascii_only else " · "
        parts = [
            f"{self.stale} stale",
            f"{self.contradictory} contradictory",
            f"{self.unused} unused",
        ]
        # DB.S6 — appended only when non-zero, so the healthy-team line stays exactly the line
        # every pre-DB.S6 surface (and its tests) already renders. A cross-writer conflict is the
        # loudest of these: it means an APPROVED write of yours is not in the shared store.
        if self.near_dup:
            parts.append(f"{self.near_dup} near-duplicate")
        if self.cross_writer:
            parts.append(f"{self.cross_writer} cross-writer conflict"
                         f"{'s' if self.cross_writer != 1 else ''}")
        counts = dot.join(parts)
        return (f"mokata · memory health: {counts} — review with `mokata memory` (gated) / "
                f"`mokata govern`; nothing changes until you approve.")


def memory_health(proposals: List[Any], reads: int, writes: int) -> MemoryHealth:
    """Derive a `MemoryHealth` from ALREADY-COMPUTED self-healing proposals + the C8 counters
    — no extra store reads (so a caller that already has both, e.g. the govern view, reuses
    them). Read-only + deterministic."""
    def _count(kind: str) -> int:
        return sum(1 for p in proposals if getattr(p, "kind", "") == kind)

    return MemoryHealth(stale=_count(STALE), contradictory=_count(CONTRADICTION),
                        unused=_unused_count(reads, writes), reads=reads, writes=writes,
                        cross_writer=_count(CROSS_WRITER), near_dup=_count(NEAR_DUP))


def _backend_read_errors() -> tuple:
    """D5 — the classes a live BACKEND READ (`store.detect_issues` → `backend.all`) genuinely
    raises, as an except-clause tuple.

    Evaluated lazily (an except clause's expression only runs when something is actually raised),
    so the optional-driver import costs nothing on the healthy path.

    * `sqlite3.Error`      — the SQLite floor: a locked / corrupt / permission-broken `.mokata/` DB.
    * `OSError`            — the local file IO under it (and the Obsidian vault backend).
    * `DegradedCapability` — a backend that could not be BUILT (`PostgresUnavailable` & family).
    * psycopg's `Error`    — the Postgres backend does NOT wrap its query errors, so a mid-session
                             network drop surfaces the DRIVER's class here. It is named only when
                             the optional extra is installed, because mokata's core must not take a
                             hard dependency on it — and it MUST be named, since narrowing without
                             it would turn today's swallow into a CRASH on a team-mode blip.
    """
    errors: tuple = (sqlite3.Error, OSError, DegradedCapability)
    try:
        import psycopg
    except ImportError:
        return errors
    return errors + (psycopg.Error,)


def assess_health(store: Any, now: Optional[str] = None) -> MemoryHealth:
    """Assess memory health over a live store — `detect_issues` (read-only; no read-counter
    bump) for the stale/contradictory backlog, plus the C8 `stats` for the unused signal.
    NEVER writes or auto-resolves anything. Degrade-clean: a store with memory off / no items
    yields a healthy (all-zero) read."""
    try:
        proposals = store.detect_issues(now=now)
    except _backend_read_errors():
        # D5 — narrowed to the classes a backend read actually raises (see `_backend_read_errors`).
        # An empty proposal list is the documented "healthy (all-zero) read" this degrades to; a
        # typo (AttributeError) in the detection path now SURFACES instead of reading as "healthy".
        proposals = []
    stats = getattr(store, "stats", None)
    reads = int(getattr(stats, "reads", 0) or 0)
    writes = int(getattr(stats, "writes", 0) or 0)
    return memory_health(proposals, reads, writes)
