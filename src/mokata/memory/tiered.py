"""Stage 35e — tiered, degrade-safe memory retrieval.

`recall_relevant(query)` fuses up to three tiers into one ranked result:
  - **lexical** (the floor, zero deps) — keyword overlap (`lexical_score`);
  - **graph-proximity** (optional middle tier) — a code-graph-keyed boost, supplied as a
    pluggable `graph_scorer` (off unless a graph is wired);
  - **semantic** (top tier) — embedding cosine, via the vector backend's index
    (`semantic_search`, no full-store scan) or, for any other backend, the embeddings stamped
    on each item at WRITE time (frugal — computed once, on the gated write).

Deterministic ordering: fused score DESC, then `created_at` ASC, then `id` ASC. The weights
make semantic dominate, then graph, with lexical as the always-present floor — so an
embedding-near item outranks a merely lexical match, yet lexical still returns when semantic
is off. Frugal (P11): retrieval returns only the top-k (no corpus dump) and embeds just the
query at read time (item vectors are precomputed on write).

Degrade-clean: no embedder ⇒ semantic tier silently absent; no graph_scorer ⇒ graph tier
absent; lexical always works. Nothing crashes when a tier is unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable, List, Optional, Sequence

from ..degrade import FAILURE_UNREACHABLE, note_degraded
from ..errors import failure_class_of
from .embed import Embedder, cosine
from .expansion import EDGE_WEIGHT
from .item import ACTIVE, DEFAULT_TOP_K
from .episodic import lexical_score
from .lifecycle import UsageSignal, recency_score, usage_score

# The zero signal, shared: an item with no telemetry row. Hoisted to module scope so the ranking
# loop allocates nothing per item, and frozen so it cannot be mutated into a non-zero default.
_NO_USAGE = UsageSignal()

# Tier weights — semantic strongest, graph next, lexical the always-present floor.
SEMANTIC_WEIGHT = 1.0
GRAPH_WEIGHT = 0.5
LEXICAL_WEIGHT = 0.25

# =================================================================================================
# THE RANKING PRINCIPLE — stated ONCE, here, because every weight in this file answers to it.
#
#     NO NON-MATCHING SIGNAL, ALONE OR IN COMBINATION, MAY OUTRANK A REAL MATCH.
#
# DB.S7b's K1 derived this for the expansion tier and pinned it as an INEQUALITY over the live
# constants (`EDGE_WEIGHT x kind x DECAY < LEXICAL_WEIGHT`) rather than as an assertion about a
# constant's value — so it goes red when someone tunes a knob PAST the bound and stays green when
# they tune it within. DB.S8f applies that same discipline to every remaining tier, because K1
# turned out to be the only one that had it, and the two that did not were both measurably wrong:
#
#   * the SEMANTIC tier had no bound at all. `SEMANTIC_WEIGHT` is 4x `LEXICAL_WEIGHT`, and cosine
#     between two UNRELATED items is a positive number (0.65 for `HashingEmbedder`, measured), so
#     an item matching nothing collected 0.65 where a full lexical match collects 0.25. Measured on
#     the DB.S8d arms: the tier cost -2.8pp recall and -6.3pp MRR.
#   * the two QUALITY terms were each bounded below `LEXICAL_WEIGHT` — and their SUM was exactly
#     `LEXICAL_WEIGHT` (0.15 + 0.10 = 0.25), so a non-matching, heavily-recalled item tied a full
#     lexical match and won on the `created_at` tiebreak. Bounding the terms individually and not
#     their sum is precisely the hole "alone OR IN COMBINATION" names.
#
# THE BUDGET. One full lexical match — `LEXICAL_WEIGHT` — is what "a real match" is worth, so it is
# the ceiling every non-matching signal must fit inside TOGETHER. Each signal gets a declared share;
# the shares must sum strictly below the budget, and that sum is checked (DB.S8f K4), not trusted.
NON_MATCH_BUDGET = LEXICAL_WEIGHT

#: K2 — the semantic tier's share. NOT a weight: it is the most an item that matches NOTHING may
#: collect from embedding cosine, and `semantic_weight_for` derives the live weight from it and the
#: embedder's own noise floor. A quiet embedder (floor <= 0.05) earns the full `SEMANTIC_WEIGHT`;
#: a noisy one is capped at what it can be trusted with. The tier EARNS its weight by being quiet.
SEMANTIC_NOISE_BUDGET = 0.05

#: K3 — the two DB.S5 quality terms' share, bounding their SUM. They answer "has this item proved
#: useful", which is a tiebreak between things that already match and never a reason to surface
#: something that does not; the budget is what makes "tiebreak" arithmetic rather than intent.
QUALITY_BUDGET = 0.04

# The expansion tier's share is NOT declared here — it is derived from `expansion`'s own knobs
# (`EDGE_WEIGHT x max kind weight x DEPTH_DECAY`, 0.15 at the shipped defaults), because those four
# numbers are one tuning surface and K1 already pins them. K4 sums that derivation with the two
# budgets above and checks the total against `NON_MATCH_BUDGET`.

# DB.S5 — the QUALITY terms, weighted deliberately BELOW the lexical floor. The three weights
# above answer "does this item match the query"; these two answer "has this item proved useful",
# which is a tiebreak between things that already match, never a reason to surface something that
# does not. Set at or above LEXICAL_WEIGHT they would let a popular-but-irrelevant item outrank a
# precise lexical hit — a memory store that returns your favourites instead of your answer.
#
# Both terms are exactly 0.0 whenever the item has no usage signal (see `lifecycle.recency_score` /
# `usage_score`), so on a store with no v4 columns, a v3 shared schema, or a freshly-migrated store
# where nothing has been recalled yet, `fused` is arithmetically the pre-DB.S5 three-term sum and
# the ranking is byte-identical. That is a contract, not an emergent property — it is pinned by
# test.
#
# DB.S8f (K3) — RE-WEIGHTED, and this is a RANKING CHANGE. They were 0.15 and 0.10, each below
# `LEXICAL_WEIGHT` and summing to exactly it, so the paragraph above described a bound the pair did
# not actually hold: an item matching nothing that had been recalled often enough scored 0.25, tied
# a full lexical match, and took the `created_at` tiebreak. Now their SUM is bounded — by
# `QUALITY_BUDGET`, their share of what a non-matching item may collect in total. The 3:2 ratio
# between them is preserved to within the rounding (5:3), so the two terms' relative say is
# unchanged; what changed is how much say the PAIR has against a real match.
RECENCY_WEIGHT = 0.025
USAGE_WEIGHT = 0.015

# DB.S7b (K1) — the EXPANSION term's weight lives in `expansion.py` beside the depth decay and the
# per-kind weights it multiplies with, because those four numbers are one tuning surface and
# splitting them across two modules is how half a knob gets turned. Imported, never re-declared.
#
# The same arithmetic contract the two DB.S5 terms above carry, for the same reason: an item that
# no edge reached scores exactly 0.0 on this tier, so `EDGE_WEIGHT * 0.0` leaves the DB.S5
# five-term sum bit for bit. On a store with no edge table, an un-migrated v4 team, or with
# `memory.edge_expansion` off, EVERY item is in that case — so the ranking is byte-identical to
# pre-DB.S7b, in the same order. Pinned by test, not assumed.

# A graph scorer is `(query, item) -> float` (0..1), wired only when a code graph is present.
#
# It is a MATCH tier — `(query, item)`, keyed on the query — so the principle's demand on it is
# that it return 0.0 for an item the query does not reach. That is a contract on the scorer, not
# an inequality mokata can enforce: a third-party callable can return whatever it likes, and
# clamping it would silently rewrite a host's ranking. Stated so a scorer author knows the rule;
# the tier is off by default (`graph_scorer is None`) and contributes exactly 0.0 when it is.
GraphScorer = Callable[[str, Any], float]


def semantic_weight_for(embedder: Optional[Embedder]) -> float:
    """DB.S8f (K2) — the semantic tier's LIVE weight: `SEMANTIC_WEIGHT`, capped by the noise floor
    of the embedder actually wired.

    THE BOUND, in the shape K1 established — an inequality over live values, never an assertion
    about a constant:

        semantic_weight_for(e) * noise_floor_of(e)  <=  SEMANTIC_NOISE_BUDGET

    which is what `min(SEMANTIC_WEIGHT, SEMANTIC_NOISE_BUDGET / floor)` says as arithmetic. Read it
    as: an item that matches NOTHING may not collect more than its tier's share of the non-match
    budget, whatever embedder is installed.

    Why the cap belongs here and not in a hand-set constant: the noise floor is a property of the
    EMBEDDER and `SEMANTIC_WEIGHT` is one global number, so no single value can be right for both a
    token-hash bag-of-words (floor 0.65) and a real static embedding. A constant chosen for one is
    wrong for the other, which is exactly how a tier weighted 4x lexical came to be fused with an
    embedder whose noise nearly overlaps its signal. Deriving the weight instead makes the tier EARN
    it: floor <= `SEMANTIC_NOISE_BUDGET` and it gets the full declared weight; noisier and it gets
    the share it can be trusted with.

    A floor of 0.0 — no embedder, or one that genuinely returns orthogonal vectors for unrelated
    text — returns `SEMANTIC_WEIGHT` unchanged, so the no-embedder ranking is arithmetically
    identical to the pre-DB.S8f one, term for term.
    """
    from .embed import noise_floor_of
    floor = noise_floor_of(embedder)
    if floor <= 0.0:
        return SEMANTIC_WEIGHT
    return min(SEMANTIC_WEIGHT, SEMANTIC_NOISE_BUDGET / floor)


@dataclass
class RetrievalHit:
    item: Any
    score: float           # fused
    lexical: float = 0.0
    semantic: float = 0.0
    graph: float = 0.0
    # DB.S5 — the quality terms. Defaulted so every existing constructor call (and every test
    # building a hit by hand) keeps working unchanged, and so a hit built without them carries the
    # zero signal rather than an absent attribute.
    recency: float = 0.0
    usage: float = 0.0
    # DB.S7b — the expansion tier's score, and the ACTUAL route that produced it. Defaulted for
    # the same reason the DB.S5 pair above is: every existing constructor call and every hand-built
    # test hit keeps working unchanged, and a hit built without them carries the zero signal rather
    # than an absent attribute. `path` is `None` for every directly-matched hit — which is most of
    # them — and is the ONLY source the "why" may use to speak about a traversal.
    edge: float = 0.0
    path: Optional[Any] = None               # an `expansion.ExpansionPath`, when a hop reached it

    def tiers(self) -> dict:
        return {"lexical": self.lexical, "graph": self.graph, "semantic": self.semantic,
                "recency": self.recency, "usage": self.usage, "edge": self.edge}

    def explain(self, query: str) -> str:
        """Stage 59 — a short, deterministic "why it surfaced" phrase for THIS hit, from its
        own per-tier scores + the query (the strongest tier + the matched token + kind).
        DB.S7b: when a hop reached this item, the phrase names the REAL walked path.
        Read-only; the hit carries its own explanation."""
        from .intelligence import why_surfaced
        return why_surfaced(query, self.item, tiers=self.tiers(), path=self.path)


def _text(item: Any) -> str:
    return f"{item.subject} {item.value}"


# DB.S3 — how many ranked rows to pull from the FTS index. Wider than `top_k` because the DB ranks
# over ALL rows while the caller only sees the SCOPE-VISIBLE ones: a hit the identity may not read
# is dropped after the query, so a bare `top_k` could come back short. Still bounded — this is a
# top-N, not the full-store scan it replaces.
LEXICAL_CANDIDATE_FLOOR = 50


def lexical_tier(store: Any, query: str, items: List[Any], top_k: int,
                 degrade_out: Optional[Callable[[str], None]] = None) -> tuple:
    """DB.S3 — the lexical tier: ONE ranked SQL query when the backend can search, the Jaccard
    scan when it can't. Returns `({item_id: score}, mode)`.

    Before DB.S3 this WAS `{it.id: lexical_score(query, _text(it)) for it in items}` — a full-store
    Python scan whose cost grows with the store, and which ranks by Jaccard (token overlap over the
    UNION), so a long document containing every query term scores BELOW a short one sharing a
    single term. Now a backend that can rank in the database (`lexical_search`) does, and only
    matching rows are ever scored.

    Three outcomes, and the difference between the last two is the honesty:
      * FTS live       — the DB's normalized scores, intersected with what the caller may SEE;
      * floor by DESIGN — a backend with no `lexical_search` at all (Obsidian's files, the native
        client). Jaccard is that backend's lexical tier, not a loss. NO notice: one that always
        fires is noise, and D5's whole point is that a notice MEANS something;
      * floor by DEGRADE — a SQL backend that SHOULD have FTS and doesn't (FTS5 not compiled), or
        whose search raised. The user asked for FTS recall and is getting keyword overlap, so say
        so ONCE (`note_degraded`) — the exact silence D5 was written to end.
    """
    from .backends import LEXICAL_MODE_JACCARD
    backend = getattr(store, "backend", None)
    search = getattr(backend, "lexical_search", None)
    mode = getattr(backend, "lexical_mode", LEXICAL_MODE_JACCARD)

    if search is not None and mode != LEXICAL_MODE_JACCARD:
        try:
            ranked = search(query, top_k=max(top_k * 4, LEXICAL_CANDIDATE_FLOOR))
            # The scope/access filter still WINS: `items` is what this identity may read, and a
            # hit outside it is dropped. The FTS predicate composes with visibility (the backend
            # already applied its project scope); it never overrides it.
            visible = {it.id for it in items}
            return {it.id: float(s) for it, s in ranked if it.id in visible}, mode
        except Exception as exc:
            # Broad for the same reason the semantic tier's handler is: `lexical_search` spans a
            # psycopg driver error (an OPTIONAL extra, not nameable at module scope), a sqlite3
            # error, and the decode of each stored doc. Narrowing to what we CAN name would let a
            # driver error crash every recall — a swallow turned into an outage.
            note_degraded("memory-lexical", failure_class_of(exc) or FAILURE_UNREACHABLE,
                          fallback="lexical recall fell back to keyword overlap (jaccard)",
                          fix="run `mokata doctor` to check the memory store",
                          detail=f"{type(exc).__name__}: {exc}", out=degrade_out)
    elif search is not None:
        # A SQL backend reporting the floor = the FTS5 capability probe came back False.
        note_degraded("memory-lexical", FAILURE_UNREACHABLE,
                      fallback="lexical recall is keyword overlap (jaccard) — FTS is unavailable",
                      fix="use a sqlite3 built with FTS5, or run `mokata doctor`",
                      detail="this sqlite3 has no FTS5 compiled in", out=degrade_out)

    return {it.id: lexical_score(query, _text(it)) for it in items}, LEXICAL_MODE_JACCARD


# R-1 (DB.S8) — how many ranked ids EACH tier nominates. Wider than `top_k` for the reason
# `LEXICAL_CANDIDATE_FLOOR` is (the fusion re-ranks, so the winner need not be first in any single
# tier), and BOUNDED because that is the entire point: the union of two tiers at this width is
# ~100 ids, plus their precedence groups. Compare the shape it replaces — every active row, which
# is 51,606 of them on DB.S8's 100k fixture, on every recall, including every per-turn injection.
CANDIDATE_OVER_FETCH = LEXICAL_CANDIDATE_FLOOR

#: The declared ceiling on the hydrated union. Not a silent cap: exceeding it is impossible by
#: construction today (two tiers x `CANDIDATE_OVER_FETCH`), and it is asserted rather than trusted
#: so a third nominating tier cannot quietly widen the bounded read into a scan again.
CANDIDATE_UNION_CAP = 150


def _kind_filter(items: List[Any], kinds: Optional[Sequence[str]]) -> List[Any]:
    """THE kind rule, in ONE place: keep only items whose `effective_kind` is in `kinds`.

    `None` means "every kind" and returns the list untouched — so every caller that predates this
    parameter is byte-identical, which is what lets the SQL predicate be added as a pure
    optimization. An EMPTY tuple is not the same thing as `None`: it means "no kind qualifies" and
    correctly returns nothing, matching `[i for i in items if i.effective_kind in ()]`.

    Deliberately duck-typed on `effective_kind` rather than reading `kind`/`mtype` here, because
    `effective_kind` (`item.py:485`) is where the `kind or mtype` fallback is DEFINED — spelling
    the fallback a second time is how the SQL expression and the Python rule start disagreeing.
    """
    if kinds is None:
        return items
    allowed = frozenset(kinds)
    return [i for i in items if getattr(i, "effective_kind", None) in allowed]


def _can_nominate(store: Any, embedder: Optional[Embedder], semantic: bool) -> bool:
    """Can this store's tiers rank candidates in SQL? Capability-PROBED, never assumed — the same
    posture `lexical_search` / `record_usage` / `expand_from` are probed with.

    Three conditions, and the third is the one that matters:

    1. the backend advertises `supports_candidate_selection` AND the store can hydrate. Both
       halves are required and it is not belt-and-braces: `lexical_search` NOMINATES and `hydrate`
       fetches the nominees. Either alone is useless.

    2. the lexical tier is not on the Jaccard floor. A SQL backend whose FTS5 probe came back False
       reports that floor and `lexical_search` returns `[]`, so nominating would hand the ranker an
       empty candidate set and silently return NOTHING.

    3. **THE SEMANTIC TIER MUST BE NOMINABLE TOO, or nobody nominates.** An embedder with no
       `semantic_search` index ranks by embedding every candidate in Python — which can only score
       rows something ELSE already selected. Nominating lexically and then running that tier over
       the survivors would quietly redefine semantic recall as "re-rank the lexical hits", so an
       item that is semantically near and lexically zero — precisely the item the semantic tier
       exists to find — would stop being findable. That is a QUALITY regression wearing a speed
       win's clothes, so this store keeps the full scan and pays for it honestly. A backend with a
       real vector index (pgvector) has no such problem: it nominates its own top-k.
    """
    backend = getattr(store, "backend", None)
    if backend is None or not getattr(backend, "supports_candidate_selection", False):
        return False
    if not hasattr(store, "hydrate_candidates"):
        return False
    from .backends import LEXICAL_MODE_JACCARD
    if getattr(backend, "lexical_mode", LEXICAL_MODE_JACCARD) == LEXICAL_MODE_JACCARD:
        return False
    if semantic and embedder is not None and not hasattr(backend, "semantic_search"):
        return False
    return True


def _nominate(store: Any, query: str, top_k: int,
              degrade_out: Optional[Callable[[str], None]],
              embedder: Optional[Embedder] = None, semantic: bool = True,
              kinds: Optional[Sequence[str]] = None,
              count_read: bool = True) -> Optional[tuple]:
    """R-1 — the bounded candidate read: nominate in SQL, hydrate the union, return
    `(items, {id: lexical_score})`. `None` when this backend cannot nominate.

    THE SCOPE PREDICATE TRAVELS WITH THE QUERY (`store.candidate_scope_path`), and that is the
    change with teeth. Before R-1 the ranked query took its LIMIT over the WHOLE store and the
    caller intersected the result with a separately-materialized visible set — so on a shared store
    the top 50 by rank could be entirely another tenant's rows, and the intersection then returned
    NOTHING while this reader's own matching rows sat unread below the cut. Pushing the predicate in
    makes the top-N a top-N of rows this identity may actually read.

    Visibility is still decided by `_visible_filter` on the hydrated set — the predicate is an
    optimization over the same rule, never a second definition of it. That is why nominating a row
    the reader may not see is harmless: it is dropped on hydration, by the database.
    """
    if not _can_nominate(store, embedder, semantic):
        return None
    backend = store.backend
    scope_path = store.candidate_scope_path()
    try:
        ranked = backend.lexical_search(query, top_k=max(top_k * 4, CANDIDATE_OVER_FETCH),
                                        scope_path=scope_path, statuses=(ACTIVE,), kinds=kinds)
    except Exception as exc:
        # Identical posture, and identical breadth, to `lexical_tier`'s handler: this spans a
        # psycopg driver error (an OPTIONAL extra, not nameable at module scope), a sqlite3 error,
        # and the decode of each stored doc. What differs is the FALLBACK — there is a correct one
        # here, the full-set scan, so a failed nomination degrades to the pre-R-1 read rather than
        # to an empty result. Announced once, because "nomination failed" and "nothing matched"
        # must not look alike.
        note_degraded("memory-candidates", failure_class_of(exc) or FAILURE_UNREACHABLE,
                      fallback="candidate selection is OFF — recall read the whole active set",
                      fix="run `mokata doctor` to check the memory store",
                      detail=f"{type(exc).__name__}: {exc}", out=degrade_out)
        return None

    lex = {it.id: float(score) for it, score in ranked}
    # The PRECEDENCE GROUPS of the nominees, not just the nominees. `precedence.resolve_items`
    # collapses a scope union to one winner per `subject`, so hydrating a nominee without its
    # siblings would let a narrow item that LOSES to a broader pinned one be returned as a winner
    # — a visible ranking change with no failing test, produced by an optimization. See
    # `filter_clause_for`.
    # NO `kinds` here, deliberately: the precedence GROUP must be hydrated whole. `resolve_items`
    # picks one winner per subject, and a sibling of a different kind is still a competitor — drop
    # it from the group and a loser is returned as a winner. The kind rule is applied to the
    # RESOLVED set by `tiered_recall`, after precedence has seen everything it needs.
    subjects = sorted({it.subject for it, _ in ranked})
    items = store.hydrate_candidates(lex.keys(), subjects=subjects, count_read=count_read)
    assert len(lex) <= CANDIDATE_UNION_CAP, (
        f"the candidate union grew to {len(lex)} ids, past the declared {CANDIDATE_UNION_CAP} — a "
        "bounded read that quietly became a wide one is the failure this cap exists to name")
    return items, lex


def tiered_recall(store: Any, query: str, *, embedder: Optional[Embedder] = None,
                  graph_scorer: Optional[GraphScorer] = None, top_k: int = DEFAULT_TOP_K,
                  semantic: bool = True,
                  degrade_out: Optional[Callable[[str], None]] = None,
                  now: Optional[str] = None,
                  expander: Optional[Callable] = None,
                  kinds: Optional[Sequence[str]] = None,
                  count_read: bool = True) -> List[RetrievalHit]:
    """Fuse lexical + graph + semantic + recency + usage into one ranked, top-k result (see the
    module docstring).

    `degrade_out` redirects a tier's degrade notice (default: stderr, once per subsystem).
    `now` fixes the clock the DB.S5 recency term decays against — injected so the ranking is a
    pure function of its inputs in a test; live callers omit it and get wall-clock UTC.

    DB.S7b (K1) — `expander` is the backend's `expand_from` seam, entering by exactly the same
    pluggable route `graph_scorer` already uses (`store.py:1115`) rather than a second one. When it
    is wired, the ranking becomes TWO passes, which is what doc 55's "seed hits → bounded
    expansion" literally says: rank directly, take the top `SEED_CAP` as SEEDS, walk ≤2 hops off
    them, then re-rank with the expansion term. `None` (no edge table, or the config off) skips the
    second pass entirely and the result is the one-pass DB.S5 ranking, unchanged.

    JIT-STAMP-SEAM — two parameters, both defaulting to the pre-existing behaviour so every
    existing caller is byte-identical:

      * `kinds` restricts the result to those `effective_kind`s. It is enforced HERE, in Python,
        over the resolved candidate set — that is THE rule. The SQL predicate `_nominate` passes
        down is an OPTIMIZATION over the same rule (it keeps the bounded top-k from being filled
        with kinds the caller cannot use and then filtered to nothing), never a second definition
        of it, exactly as the scope predicate is. A backend that ignores `kinds` returns a superset
        and this filter still produces the right answer.
      * `count_read=False` reads through the store's NON-COUNTING twins. For the per-turn
        injection, which must move no durable state on a hook that fires every prompt."""
    # R-1 (DB.S8) — CANDIDATE SELECTION. The store's tiers nominate their own ranked top-k in SQL
    # and only the union is materialized; `None` means this backend cannot nominate (the Jaccard
    # floor: Obsidian's files, the native client, any third-party adapter) and the full-set read
    # below is its correct tier, not a fallback it is being punished with.
    nominated = _nominate(store, query, top_k, degrade_out, embedder, semantic,
                          kinds=kinds, count_read=count_read)
    if nominated is not None:
        items, lex = nominated
        items = _kind_filter(items, kinds)
        if not items:
            return []
    else:
        # TM.S6 — candidates are the scope-path UNION (byte-identical to all_active when the store
        # has no scope context). Falls back to all_active for any store-like object lacking it.
        # JIT-STAMP-SEAM — `count_read=False` picks the non-counting twin of the SAME visible set
        # (`peek_visible_active`), so the injection path reads what a recall reads without moving
        # the counter. The chain degrades exactly like `brain._injectable_active`'s does, for the
        # duck-typed stores this function accepts.
        if count_read:
            candidates = getattr(store, "scoped_active", None) or store.all_active
        else:
            candidates = (getattr(store, "peek_visible_active", None)
                          or getattr(store, "peek_active", None)
                          or store.all_active)
        items = _kind_filter(candidates(), kinds)     # honoring toggles + scope path
        if not items:
            return []
        # DB.S3 — the lexical tier is a SQL FTS query where the backend can run one, and the
        # Jaccard scan (this line's former body) only where it can't.
        lex, _lexical_mode = lexical_tier(store, query, items, top_k, degrade_out)

    sem: dict = {}
    if semantic and embedder is not None:
        backend = store.backend
        # DB.S4 — REACHABLE. The D5-rider(3) not-yet-reachable marker that stood here has been
        # retired because it fired: `selection._select_raw_backend` now has a `pgvector` branch, so
        # a team that opts into the semantic store reaches this line on a real config. The marker
        # did its job — it kept the branch (rather than deleting a shape DB.S4 would have had to
        # re-add) and kept it honestly labelled while it waited. `test_r_13f_d5_rider_3` still pins
        # both sub-paths; DB.S4's own suite pins the live selection that reaches them.
        if hasattr(backend, "semantic_search"):
            # index-backed top-k (e.g. pgvector) — no full-store scan
            try:
                for it, score in backend.semantic_search(query, top_k=max(top_k, len(items))):
                    sem[it.id] = score
            except Exception as exc:
                # D5 — the semantic TIER just went away, and recall carried on ranking as if it had
                # never been configured. The fallback is right (lexical is the designed floor and a
                # recall must not break); the SILENCE was the bug — the user asked for semantic
                # recall, is getting keyword overlap, and nothing anywhere said so.
                #
                # Deliberately still broad, and this is the honest reason: `semantic_search` is an
                # index-backed pgvector call, so its raisables span `psycopg.Error` (an OPTIONAL
                # extra — not nameable at module scope), the embedder's own errors, and the decode of
                # each stored doc. Narrowing to the subset we CAN name would let a driver error crash
                # every recall — turning a swallow into a crash, which is worse than the bug.
                sem = {}                              # degrade to the lexical floor
                note_degraded("memory-semantic", failure_class_of(exc) or FAILURE_UNREACHABLE,
                              fallback="semantic recall is OFF — ranking is lexical-only",
                              fix="check the vector backend / run `mokata doctor`",
                              detail=f"{type(exc).__name__}: {exc}")
        else:
            qv = embedder(query)
            for it in items:
                ev = it.provenance.get("_embedding") or embedder(_text(it))
                sem[it.id] = cosine(qv, ev)

    grp: dict = {}
    if graph_scorer is not None:
        for it in items:
            try:
                grp[it.id] = float(graph_scorer(query, it))
            except Exception:
                # LEGITIMATE SUPPRESS: a PER-ITEM hiccup in a pluggable, OPTIONAL ranking signal.
                # It contributes 0.0 — the item still ranks on lexical (+ semantic), so nothing is
                # hidden and no result is lost; only a boost is. A notice per item would be noise,
                # and the tier is off by default anyway (`graph_scorer is None`).
                grp[it.id] = 0.0

    # DB.S5 — the usage telemetry for exactly the candidates being ranked: ONE bounded read keyed
    # by ids we already hold, never a scan and never a per-item round trip. `usage_signals` returns
    # `{}` for any store that cannot answer (no v4 columns, a v3 team schema, a driver error), and
    # `{}` means every item gets the zero signal — which is the back-compat floor, not a failure.
    usage_signals: dict = {}
    reader = getattr(store, "usage_signals", None)
    if reader is not None:
        try:
            usage_signals = reader([it.id for it in items]) or {}
        except Exception:
            # `MemoryStore.usage_signals` already swallows its own failures, so this guard is for
            # the DUCK-TYPED stores this function accepts (a test double, a host-supplied object)
            # whose reader makes no such promise. The module contract at the top of this file is
            # that no tier can crash a recall, and a signal that arrived after that contract was
            # written does not get an exemption from it. Empty = the zero signal = the pre-DB.S5
            # ranking, which is the same floor every other tier degrades to.
            usage_signals = {}

    # DB.S8f (K2) — the semantic tier's weight is derived from the LIVE embedder's noise floor, once
    # per recall rather than per item. With no embedder wired this is `SEMANTIC_WEIGHT` exactly, so
    # the expression below is arithmetically the pre-DB.S8f one on every store that never opted into
    # semantics — which, `memory.embedder` being unset by default, is the default install.
    sem_weight = semantic_weight_for(embedder) if (semantic and embedder is not None) \
        else SEMANTIC_WEIGHT

    hits: List[RetrievalHit] = []
    match: dict = {}
    for it in items:
        l, s, g = lex.get(it.id, 0.0), sem.get(it.id, 0.0), grp.get(it.id, 0.0)
        signal = usage_signals.get(it.id) or _NO_USAGE
        r = recency_score(signal, it.created_at, now)
        u = usage_score(signal)
        # The three original terms are UNTOUCHED and lead the expression, so the additive change is
        # visible as exactly that: two terms that vanish to `+ 0.0 + 0.0` on a store with no
        # telemetry, leaving the pre-DB.S5 sum bit for bit.
        fused = (sem_weight * s + GRAPH_WEIGHT * g + LEXICAL_WEIGHT * l
                 + RECENCY_WEIGHT * r + USAGE_WEIGHT * u)
        hits.append(RetrievalHit(item=it, score=fused, lexical=l, semantic=s, graph=g,
                                 recency=r, usage=u))
        # DB.S7b — the MATCH score (the three tiers that answer "does this item match the query"),
        # kept separately because it is what SEEDS the expansion. Not the fused score: the two
        # quality terms move with recall HISTORY, and seeding on them lets the reachable set creep
        # past the hop bound over repeated recalls. See `expansion.select_seeds`.
        match[it.id] = sem_weight * s + GRAPH_WEIGHT * g + LEXICAL_WEIGHT * l

    # deterministic ordering: fused desc, then created_at asc, then id asc — UNCHANGED. The new
    # terms enter the score, never the tiebreak: a tiebreak that consulted usage would make the
    # order of two identically-scoring items depend on run-state that a recall itself mutates.
    hits.sort(key=lambda h: (-h.score, h.item.created_at, h.item.id))

    # DB.S7b — PASS TWO. Everything above is the pre-DB.S7b ranking, produced identically and in the
    # same order; what follows can only ADD to a score, never subtract, and adds exactly 0.0 to
    # every item when nothing was reached. `expander is None` skips it outright.
    expanded = _expansion_tier(hits, match, expander, degrade_out,
                               store=store, bounded=nominated is not None,
                               count_read=count_read)
    if expanded:
        # R-1 — ADMIT, then boost. Under the full-set read every reachable item was already in
        # `hits`, so the tier only ever RE-RANKED. Under candidate selection an item reached by a
        # hop is, by definition, one the direct tiers did NOT nominate — which is exactly the item
        # doc 55:80 says expansion exists to surface. Without this block the bounded path would
        # walk the graph correctly and then silently discard everything it found.
        known = {h.item.id for h in hits}
        admitted = [i for i in (expanded.admitted or ()) if i.id not in known]
        for item in admitted:
            hits.append(RetrievalHit(item=item, score=0.0))
        for hit in hits:
            path = expanded.paths.get(hit.item.id)
            if path is None:
                continue
            hit.edge = path.weight
            hit.path = path
            hit.score += EDGE_WEIGHT * path.weight
        # A second sort with the SAME key. When no item was reached this re-sorts an already-sorted
        # list under an unchanged key — Python's sort is stable, so the order is not merely
        # equivalent but identical, which is what makes the degrade byte-identical rather than
        # merely equal-scoring.
        hits.sort(key=lambda h: (-h.score, h.item.created_at, h.item.id))
    return hits[:top_k]


def _bounded_walk(expander: Callable, seeds: Any, store: Any, hits: List[RetrievalHit],
                  walk_paths: Callable, max_hops: int, count_read: bool = True) -> Any:
    """R-1 — the ≤2-hop walk under candidate selection: fetch, hydrate, THEN chain.

    `expansion.expand` is `fetch` followed by `walk_paths`, and splitting it here is what lets the
    visible set be established from the database instead of from a candidate list that has no
    business standing in for one. The three steps:

      1. FETCH — `expander(seeds, max_hops)`, the same single recursive CTE, unchanged;
      2. HYDRATE — every id the rows mention, through `store.hydrate_candidates`, which carries the
         same scope predicate every other read carries. A row the reader may not see simply does
         not come back, so it can neither be admitted NOR bridge;
      3. CHAIN — `walk_paths` with that in-scope id set as `visible`, so the bridge, cycle and self
         prunes all run exactly as they do on the full-set path.

    The hydrated items are attached to the result as `admitted` so the caller can add the ones the
    direct tiers never nominated. Bounded throughout: the walk is capped by `SEED_CAP` and
    `MAX_WALKED_EDGES` before this ever sees it.
    """
    rows = expander(list(seeds), max_hops) or ()
    rows = list(rows)
    # Every id the walk touched, in either column — a bridge must be hydrated to be JUDGED
    # visible, not assumed invisible because it was not nominated.
    touched = {str(r[1]) for r in rows} | {str(r[2]) for r in rows}
    known = {h.item.id for h in hits}
    # JIT-STAMP-SEAM — `count_read` rides through here too, and it is NOT optional plumbing: this
    # is the SECOND counted read on a single recall, reached only when the walk touches an id the
    # direct tiers did not nominate. On the injection path it fired on almost every turn at scale
    # (measured: `stats.reads` moved 21 times across 40 non-counting recalls at N=100k), so a seam
    # that threaded only the first hydrate would have left the counter moving anyway.
    fetched = (store.hydrate_candidates(sorted(touched - known), count_read=count_read)
               if touched - known else [])
    in_scope = known | {i.id for i in fetched}
    result = walk_paths(rows, seeds=seeds, visible=in_scope, max_hops=max_hops)
    # Only the items an admitted PATH actually reached — `fetched` is everything the walk touched,
    # which includes bridges that were pruned and nodes whose path lost on weight.
    #
    # `replace`, not assignment: `ExpansionResult` is FROZEN, deliberately, so a walk's answer
    # cannot be edited after the prunes have run. Attaching the hydrated items is a new result
    # carrying the same paths, not a mutation of the one the walk returned.
    return replace(result, admitted=tuple(i for i in fetched if i.id in result.paths))


def _expansion_tier(hits: List[RetrievalHit], match: dict, expander: Optional[Callable],
                    degrade_out: Optional[Callable[[str], None]],
                    store: Any = None, bounded: bool = False,
                    count_read: bool = True) -> Any:
    """DB.S7b — seed from the MATCH ranking, walk ≤2 hops, report what was left out.

    Separated from the fusion so the two-pass structure reads as two passes. Returns an
    `ExpansionResult` (falsy when nothing was reached), or `None` when the tier is off.

    SCOPE, and R-1 changed HOW it is established without changing WHAT it is. The rule is
    unaltered: an unreadable item must not bridge hop 1 to hop 2 (`expansion.walk_paths`,
    decision 4), because a post-filter would satisfy doc 55's "no cross-scope leak through a hop"
    while leaking the bridge's id inside the returned path.

      * FULL-SET read — the visible set IS `hits`, exactly what `scoped_active()` already decided
        this identity may read. Unchanged, byte for byte.
      * BOUNDED read — `hits` is now ~150 nominated rows, so using it as the visible set would
        prune away almost the whole graph and the walk would find nothing. Instead the walk is
        split into its two existing halves: FETCH the rows (one recursive CTE, as before), HYDRATE
        the ids it reached through the store's scope-predicated bounded read — so the DATABASE
        decides visibility, on the same predicate every other read uses — and only then chain the
        paths with that in-scope set as `visible`. The bridge prune runs on a truthful visible set,
        one DB walk, one bounded hydrate, and no scan anywhere.
    """
    if expander is None:
        return None
    from .expansion import EMPTY, MAX_HOPS, expand, select_seeds, walk_paths
    # Seeded on the MATCH score, and ordered by it too — so the seed set is a function of the
    # QUERY alone and does not drift as recall telemetry accumulates.
    ranked = sorted(((h.item.id, match.get(h.item.id, 0.0)) for h in hits),
                    key=lambda pair: (-pair[1], pair[0]))
    seeds, dropped = select_seeds(ranked)
    if not seeds:
        return EMPTY
    visible = {h.item.id for h in hits}
    try:
        if bounded:
            result = _bounded_walk(expander, seeds, store, hits, walk_paths, MAX_HOPS,
                                   count_read=count_read)
        else:
            result = expand(expander, seeds, visible=visible, max_hops=MAX_HOPS)
    except Exception as exc:
        # D5 — the EXPANSION tier just went away and the recall must say so ONCE, exactly as the
        # semantic and lexical tiers do above. Broad for the identical reason they are: this spans
        # a psycopg driver error (an OPTIONAL extra, not nameable at module scope), a sqlite3 error
        # on a store mid-migration, and any third-party adapter's own classes. The fallback is
        # right — the direct matches are still ranked correctly — but silence would make "the walk
        # failed" indistinguishable from "there are no edges", which is the exact ambiguity D5
        # exists to end.
        note_degraded("memory-expansion", failure_class_of(exc) or FAILURE_UNREACHABLE,
                      fallback="edge expansion is OFF — ranking is direct-match only",
                      fix="run `mokata doctor` to check the memory store",
                      detail=f"{type(exc).__name__}: {exc}", out=degrade_out)
        return EMPTY
    # NO SILENT CAPS. A bounded read that trims and says nothing reads as a complete one.
    if dropped or result.rows_truncated:
        note_degraded("memory-expansion-bounds", FAILURE_UNREACHABLE,
                      fallback=(f"edge expansion was bounded: {dropped} seed(s) beyond the cap, "
                                f"{result.rows_truncated} walked edge(s) dropped"),
                      fix="narrow the query, or raise the DB.S7b bounds if this recurs",
                      detail=f"seed cap {len(seeds)}, hop bound {MAX_HOPS}", out=degrade_out)
    return result
