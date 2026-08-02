"""DB.S7b — K1: BOUNDED ≤2-HOP RETRIEVAL EXPANSION over the typed edges.

THE ONE definition of the traversal. The hop bound, the recursive CTE, the per-kind weights, the
depth decay, the seed cap and the path reconstruction all live HERE, so the local SQLite floor and
the shared Postgres table cannot disagree about what "two hops" means — the same argument
`edges.py` makes for the substrate, applied to the read over it.

NEVER A SECOND GRAPH DB (doc 84 DB.S7, doc 04). One recursive CTE, one statement per recall,
against the table `edges.py` already defines. Nothing here opens a connection or writes anything.


THE SIX DECISIONS THIS MODULE ENCODES
=====================================

**1 · THE ANCHOR TAKES EVERY TEXT COLUMN FROM A REAL COLUMN.** A recursive CTE whose anchor is
`SELECT ?, 0` leaves Postgres unable to infer the parameter's type, while SQLite — dynamically
typed — accepts it happily. That asymmetry is not hypothetical here: `teamdb.py:622-624` records
mokata already taking this bite once ("an untyped NULL in a SELECT list is `text`, and Postgres
refuses to insert text into the BIGINT `approval_ledger_id` — observed, not guessed"). So the
anchor selects `e.src_id, e.src_id, e.dst_id, e.kind` — all real columns of a real table — and the
only literal is the depth, spelled `CAST(1 AS INTEGER)` so the type is DECLARED rather than
inferred. The recursive term's bound is `CAST(? AS INTEGER)` for the same reason.

**2 · THE DEPTH BOUND IS A PREDICATE IN THE RECURSIVE TERM, NEVER A `LIMIT`.** `WHERE
w.depth < :max_hops` is the whole bound, and it is the portable one: a `LIMIT` inside a recursive
term means different things (and is variously refused) across engines, while a WHERE on an integer
column reads identically on both. There is no `LIMIT` anywhere in this module.

**3 · THE WALK RETURNS EDGES; PYTHON RECONSTRUCTS THE PATH.** Carrying a path STRING in the CTE
would need `instr()` on SQLite and `strpos()`/`position()` on Postgres — a dialect fork in the one
statement whose whole purpose is to be dialect-free. Instead the CTE returns the walked EDGES
(`seed, src, dst, kind, depth`) and `walk_paths` chains them here. At ≤2 hops the chain is at most
two steps, so this is cheaper than the string concatenation it replaces, not merely safer.

**4 · SCOPE PRUNES THE BRIDGE, NOT JUST THE RESULT.** A reached node outside the caller's visible
set is dropped AND cannot extend to hop 2. Filtering only the final result would be a leak: an
invisible item bridging hop 1 to hop 2 would have its id printed inside the returned path, which
discloses both the relation and the id even though no content was read. Doc 55's acceptance says
"no cross-scope leak through a HOP" — through, not at the end. Visibility itself is defined in
exactly ONE place (`store.scoped_active`) and passed in; this module never re-derives it, for the
reason `store.py:296-302` gives for keeping `union_read` beside the SQL pushdown: a second
definition of visibility is a second thing to get wrong.

**5 · ORDERING IS AUTHORITATIVELY PYTHON'S, NOT THE DATABASE'S.** The CTE carries an `ORDER BY` so
its plan is stable and its output reproducible, but the ordering the caller SEES is imposed here.
SQLite sorts text by BINARY collation; Postgres sorts by the database's collation, and under a
typical `en_US.UTF-8` that means punctuation is weighted differently at the first level — so two
engines holding identical rows can legitimately return them in different orders. Sorting in Python
makes the cross-engine agreement a statement about the DATA rather than about two collation
tables, which is the statement worth pinning.

**6 · THE CLOSED SET IS TRAVERSED GENERICALLY; THE WEIGHT IS THE CONTROL.** Every kind in
`edges.EDGE_KINDS` is walked. A kind that should not pull an item in is expressed as a LOW OR ZERO
WEIGHT, never as an absence from the traversal — because a kind silently excluded from the walk is
a kind nobody can observe being wrong, while a kind weighted zero is visible in one table and
tunable in one place. The three WIRED kinds carry real weights; the five unwired ones carry an
explicit provisional default so that the day a producer lands, the traversal is not silently dead.

**PROVISIONAL, AND SAID SO ONCE HERE:** every number in `KIND_WEIGHT`, `EDGE_WEIGHT`,
`DEPTH_DECAY` and `SEED_CAP` is a FIRST CUT chosen for the shape it produces (see each constant),
not a measured optimum. They are tuned at DB.S8 against the at-scale fixture, which is the first
place there is enough corpus to tune them honestly.


WHAT THIS SLICE DELIBERATELY DOES NOT DO
========================================

**The traversal is FORWARD-ONLY** — it walks `src → dst`, riding the leading column of the partial
unique index both engines carry. The REVERSE direction ("which items depend on this one") is not
built here, and its absence has one visible consequence worth naming rather than leaving to be
discovered: `about_code` edges point at a code path, which is not a memory item, so a forward hop
across one can never admit anything. The A→file→B bridge that would make `about_code` useful for
retrieval needs the reverse leg. DB.S7a already provisioned the index it would ride
(`memory_edges_dst` / `mokata_memory_edges_dst`, on `(dst_id, kind) WHERE valid_to IS NULL`) —
that index has no consumer today, and this is the read that would become one.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .edges import (DST_COLUMN, EDGE_KINDS, KIND_COLUMN, SRC_COLUMN, VALID_TO_COLUMN,
                    ABOUT_CODE, DEPENDS_ON, DERIVES_FROM, SUPERSEDES)

# ---------------------------------------------------------------- the bound (K1's whole point)
# ≤2 hops, doc 84 K1 / doc 55 §3. TWO, not "a few": the value is the contract, and it is spelled
# once here and bound into the SQL from this constant, so the Python bound and the SQL bound cannot
# drift into disagreeing about what the guarantee is.
MAX_HOPS = 2

# How many of the ranked seed hits get expanded. Doc 55: "seed hits (FTS + vector) → bounded
# expansion" — the seeds are the top of the PRE-expansion ranking, and the cap is what keeps a
# recall on a large store from fanning out of a hundred anchors. 10 is deliberately small: the
# expansion exists to add the answer that direct matching missed, and if the direct ranking's top
# ten do not touch it, an eleventh seed is not what was missing. Truncation is REPORTED, never
# silent (`ExpansionResult.seeds_dropped`). PROVISIONAL — tuned at DB.S8.
SEED_CAP = 10

# The floor a MATCH score must CLEAR to seed an expansion (exclusive). Zero, i.e. "the matching
# tiers found something, anything". See `select_seeds` for why both halves of this — the floor AND
# the fact that it is scored on the MATCH terms rather than the fused score — are load-bearing
# rather than tidy-ups. Each prevents a distinct, measured bug.
SEED_MIN_SCORE = 0.0

# A backstop on the walked-edge count, for a pathological graph (one node with thousands of open
# edges). NOT a silent cap: tripping it sets `rows_truncated`, which the caller surfaces. Applied
# in Python AFTER this module's own deterministic sort, never as a SQL `LIMIT` — a SQL cut would be
# taken in the database's collation order and could therefore fall differently on the two engines,
# turning a safety valve into a source of cross-engine disagreement. PROVISIONAL — tuned at DB.S8.
MAX_WALKED_EDGES = 5000

# ---------------------------------------------------------------- ranking knobs (ALL PROVISIONAL)
# The fusion weight for the expansion tier. Sits BELOW `GRAPH_WEIGHT` (0.5) and above nothing —
# it is deliberately under `LEXICAL_WEIGHT` (0.25) once the depth decay is applied, and that is the
# whole design intent: an item reached only by a hop must be able to SURFACE (it beats the 0.0 of
# an item nothing matched) without DISPLACING an item the query actually matched. With the decay
# below, a 1-hop `depends_on` neighbour scores 0.30 × 1.0 × 0.5 = 0.15, which is under a full
# lexical match's 0.25 — so a hop never outranks a direct hit, and a hop always outranks nothing.
# PROVISIONAL — tuned at DB.S8.
EDGE_WEIGHT = 0.30

# Multiplicative per-hop decay: a path's weight is `kind weights × DEPTH_DECAY ** depth`. Two hops
# is a quarter of the base, one hop a half. Multiplicative rather than a flat second-hop discount
# so that adding a third hop (which K1 forbids, but K-later may not) needs no new constant and
# cannot accidentally be worth MORE than a second. PROVISIONAL — tuned at DB.S8.
DEPTH_DECAY = 0.5

# Per-type weights (doc 55 §3, "per-type weights"). THE CONTROL over what expansion should pull in
# — decision 6 above. A path crossing two edges multiplies both weights, so a path through a weak
# relation is itself weak without needing a separate rule.
#
# The five UNWIRED kinds share one explicit default rather than being listed at 0.0, and the choice
# matters: at 0.0 the first producer to land would find its edges walked and then contributing
# exactly nothing, with no test failing and nobody looking. A mid default means a newly-produced
# kind starts CONTRIBUTING and is therefore observable — and the number is honestly provisional.
UNWIRED_DEFAULT_WEIGHT = 0.5      # weight TBD when produced (no producer exists — doc 02 dec. #2)

KIND_WEIGHT: Dict[str, float] = {
    # --- the three WIRED kinds (a live producer exists — `edges._ITEM_FIELD`) ------------------
    # "this item is only true while that one is" — the canonical K1 case. An answer that is only
    # reachable through a dependency is exactly what doc 55:80 says expansion must surface.
    DEPENDS_ON: 1.0,
    # Lineage. Weighted low because its target is USUALLY invisible anyway (a superseded item is
    # `status=SUPERSEDED`, so it is not in the active set and the visibility prune drops it); when
    # the target IS visible, "the thing this replaced" is weak context, not an answer.
    SUPERSEDES: 0.4,
    # ZERO, and not as a judgement about the relation's value — as an honest statement about what a
    # FORWARD hop across it can do. Its dst is a code path, never a memory item, so it can never be
    # admitted and never bridges to anything on a forward walk. It becomes useful the day the
    # reverse leg exists (see the module header); until then a non-zero weight would be decoration.
    ABOUT_CODE: 0.0,
    # WIRED 2026-08-01 (the SUMMARIZE producer). Listed explicitly now that it has a producer, and
    # deliberately AT `UNWIRED_DEFAULT_WEIGHT`'s value — so wiring the kind is byte-identical in
    # ranking and the only thing that changed is that edges now exist to walk. Moving the number
    # would be a ranking change made on intuition, and this module's own header says every one of
    # these is tuned against the at-scale fixture, not chosen. It is the one kind now OWED a
    # measured weight: unlike the still-unwired four, a sweep can finally observe it.
    #
    # For whoever tunes it: a SUMMARIZE does NOT retire its olds (`apply_consolidation`'s SUMMARIZE
    # branch writes only the new item), so the raw turns stay ACTIVE and a forward hop across this
    # edge CAN admit them. The question a sweep has to answer is whether surfacing the material a
    # summary already condenses is worth a slot — which is a real question, not an obvious one.
    DERIVES_FROM: 0.5,
}
# The four with no producer, defaulted EXPLICITLY (never by a dict `.get` fallback buried in the
# scorer) so the table below is the complete, readable answer to "what is each kind worth".
for _kind in EDGE_KINDS:
    KIND_WEIGHT.setdefault(_kind, UNWIRED_DEFAULT_WEIGHT)
del _kind

# The CTE's own column aliases. Named here for the same reason `edges.py` names the table's
# columns: the SQL and the row-unpacking below must agree, and one definition is how.
_SEED, _SRC, _DST, _KIND, _DEPTH = "seed", "src", "dst", "kind", "depth"
_WALK = "walk"


def kind_weight(kind: str) -> float:
    """The per-type weight for `kind`. An unknown kind (impossible through `validate_kind`, but a
    hand-written row or a newer build's edge could carry one) is worth ZERO rather than the unwired
    default — an unrecognised relation is not a relation whose strength we can assert."""
    return KIND_WEIGHT.get(kind, 0.0)


# ---------------------------------------------------------------- the SQL (ONE build, two engines)
def expansion_sql(table: str, seed_count: int, *, placeholder: str = "?") -> str:
    """THE recursive-CTE traversal: every edge walked within `MAX_HOPS` of the given seeds.

    ONE statement per recall (never a per-node loop, which would neither be a recursive CTE nor
    survive the DB.S7 "recursive CTEs ONLY" constraint). `table` and every identifier are mokata
    CONSTANTS, never caller input; `seed_count` only decides how many placeholders to emit. Every
    real value travels BOUND.

    INDEX-BOUND on both engines (doc 55:83, "no seq scans"): the anchor filters `src_id IN (…)`
    and the recursive term joins `e.src_id = w.dst`, both of which are the LEADING column of the
    partial unique index DB.S7a created on both engines — `memory_edges_open` (`backends.py:670`)
    and `mokata_memory_edges_open` (`teamdb.py:820`), each `(src_id, dst_id, kind) WHERE valid_to
    IS NULL`. The `valid_to IS NULL` predicate matches the index's own WHERE, so the partial index
    is usable rather than merely present.

    The `valid_to IS NULL` filter appears TWICE — once in the anchor, once in the recursive term —
    and that is not redundancy to be tidied away: they are two independent predicates over two
    different row sets, and dropping either one alone lets closed (historical) edges back into the
    walk through the half that was left unguarded.
    """
    p = placeholder
    seeds = ", ".join([p] * max(int(seed_count), 0))
    return (
        f"WITH RECURSIVE {_WALK}({_SEED}, {_SRC}, {_DST}, {_KIND}, {_DEPTH}) AS ("
        f" SELECT e.{SRC_COLUMN}, e.{SRC_COLUMN}, e.{DST_COLUMN}, e.{KIND_COLUMN}, "
        f"CAST(1 AS INTEGER)"                                                   # nosec B608
        f" FROM {table} e"
        f" WHERE e.{SRC_COLUMN} IN ({seeds}) AND e.{VALID_TO_COLUMN} IS NULL"
        f" UNION ALL"
        f" SELECT w.{_SEED}, e.{SRC_COLUMN}, e.{DST_COLUMN}, e.{KIND_COLUMN}, w.{_DEPTH} + 1"
        f" FROM {table} e JOIN {_WALK} w ON e.{SRC_COLUMN} = w.{_DST}"
        f" WHERE w.{_DEPTH} < CAST({p} AS INTEGER) AND e.{VALID_TO_COLUMN} IS NULL"
        f") SELECT {_SEED}, {_SRC}, {_DST}, {_KIND}, {_DEPTH} FROM {_WALK}"
        f" ORDER BY {_DEPTH}, {_SEED}, {_KIND}, {_DST}, {_SRC}"
    )


def expansion_params(seeds: Sequence[str], max_hops: int = MAX_HOPS) -> tuple:
    """The bound values for `expansion_sql` — the seed ids, then the hop bound."""
    return (*seeds, int(max_hops))


# ---------------------------------------------------------------- the walk (paths, in Python)
@dataclass(frozen=True)
class ExpansionStep:
    """One traversed edge. Frozen: a step is a fact about a walk that already happened."""

    src: str
    kind: str
    dst: str

    def render(self) -> str:
        return f"{self.kind} → {self.dst}"


@dataclass(frozen=True)
class ExpansionPath:
    """One reached item, WITH the actual route taken to it.

    `steps` is the real chain of walked edges, in order, seed-first. It is what "why surfaced
    answers with the path" (doc 84 K1, doc 55:41-44) is answered FROM — never re-derived from the
    item's own inline fields, which would produce a plausible sentence about a relation the
    traversal did not use.
    """

    seed: str
    node: str
    depth: int
    steps: Tuple[ExpansionStep, ...]
    weight: float

    def render(self) -> str:
        """`<seed> → <kind> → <node> → <kind> → <node>` — the route, left to right."""
        return " → ".join([self.seed] + [f"{s.kind} → {s.dst}" for s in self.steps])

    @property
    def kinds(self) -> Tuple[str, ...]:
        return tuple(s.kind for s in self.steps)


@dataclass(frozen=True)
class ExpansionResult:
    """The traversal's whole answer, INCLUDING what it had to leave out.

    `seeds_dropped` and `rows_truncated` exist so a bounded read never reports as a complete one:
    a cap that silently trims is a cap that reads as "this is everything". The caller surfaces
    them; nothing here decides they do not matter.
    """

    paths: Dict[str, ExpansionPath]          # item id → the BEST path that reached it
    seeds_used: Tuple[str, ...] = ()
    seeds_dropped: int = 0
    rows_truncated: int = 0
    cycles_pruned: int = 0                   # candidate extensions dropped for revisiting a node
    # R-1 (DB.S8) — the ITEMS an admitted path reached, when the caller had to fetch them.
    #
    # Empty on the full-set read, and that is not an oversight: there, every reachable item was
    # already materialized and the tier only ever RE-RANKED what the caller held. Under candidate
    # selection an item reached by a hop is by definition one the direct tiers did NOT nominate —
    # doc 55:80's "an answer only reachable via one hop" — so it has to be handed back or the walk
    # finds it and nobody ever sees it. This module still opens no connection: the caller hydrates
    # and attaches (`tiered._bounded_walk`).
    admitted: Tuple[Any, ...] = ()

    def score(self, item_id: str) -> float:
        path = self.paths.get(item_id)
        return path.weight if path is not None else 0.0

    def __bool__(self) -> bool:
        return bool(self.paths)


EMPTY = ExpansionResult(paths={})


def path_weight(steps: Sequence[ExpansionStep]) -> float:
    """`∏ kind weights × DEPTH_DECAY ** depth` — decision 6 + the decay knob, in one place.

    Multiplying the kinds means a path is only as strong as the relations it crossed: a hop through
    a zero-weight kind is worth zero however strong the second hop is, which is the correct reading
    of "this route is not a reason to surface anything".
    """
    weight = DEPTH_DECAY ** len(steps)
    for step in steps:
        weight *= kind_weight(step.kind)
    return weight


def _better(a: ExpansionPath, b: ExpansionPath) -> ExpansionPath:
    """The stronger of two routes to the same item, DETERMINISTICALLY.

    Highest weight wins; ties break on the shorter path, then on the rendered route — never on
    dict/row order, so two engines (or two runs) that reach one item by two equally-weighted routes
    still name the SAME route as the reason.
    """
    key_a = (-a.weight, a.depth, a.render())
    key_b = (-b.weight, b.depth, b.render())
    return a if key_a <= key_b else b


def walk_paths(rows: Iterable[Sequence[Any]], *, seeds: Sequence[str],
               visible: Optional[set] = None,
               max_hops: int = MAX_HOPS) -> ExpansionResult:
    """Chain the walked EDGES into per-item PATHS, pruning by visibility AT EVERY HOP.

    `rows` are `(seed, src, dst, kind, depth)` as `expansion_sql` returns them. `visible` is the
    caller's set of readable item ids — `None` means "no scope context", which is the local
    zero-config case and prunes nothing.

    Three prunes, and each one is a contract rather than a tidy-up:

      * **the BRIDGE prune (decision 4)** — a depth-2 row whose `src` was not itself an admitted
        depth-1 node is dropped. Without it, an item the caller may not read would appear inside a
        returned path, disclosing the relation and the id;
      * **the CYCLE prune** — a path that revisits its own seed, or any node already on it, is
        dropped. `UNION ALL` does not deduplicate, so `a→b→a` genuinely returns `a` at depth 2 on
        BOTH engines (measured, not assumed); an item must never be its own expansion;
      * **the SELF prune** — a seed is already in the ranking that produced it. Re-admitting it as
        its own neighbour would let an item boost itself.
    """
    seed_set = {s for s in seeds if s}
    by_depth: Dict[int, List[Sequence[Any]]] = {}
    truncated = 0
    ordered = sorted(rows, key=lambda r: (int(r[4]), str(r[0]), str(r[3]), str(r[2]), str(r[1])))
    if len(ordered) > MAX_WALKED_EDGES:
        truncated = len(ordered) - MAX_WALKED_EDGES
        ordered = ordered[:MAX_WALKED_EDGES]
    for row in ordered:
        by_depth.setdefault(int(row[4]), []).append(row)

    def _admits(node: str) -> bool:
        return visible is None or node in visible

    best: Dict[str, ExpansionPath] = {}
    cycles_pruned = 0
    # `reached[seed][node]` — the admitted path to `node` from `seed` at the PREVIOUS depth. Only
    # these may be extended, which is the bridge prune expressed as a data structure rather than a
    # condition someone can forget to write.
    reached: Dict[str, Dict[str, ExpansionPath]] = {s: {} for s in seed_set}

    for depth in range(1, int(max_hops) + 1):
        frontier: Dict[str, Dict[str, ExpansionPath]] = {s: {} for s in seed_set}
        for seed, src, dst, kind, _d in by_depth.get(depth, []):
            seed, src, dst, kind = str(seed), str(src), str(dst), str(kind)
            if seed not in seed_set:
                continue
            if depth == 1:
                if src != seed:
                    continue                       # a hop-1 row must start AT its seed
                prefix: Tuple[ExpansionStep, ...] = ()
            else:
                parent = reached[seed].get(src)
                if parent is None:
                    continue                       # BRIDGE PRUNE — the intermediate was not admitted
                prefix = parent.steps
            if dst == seed or any(s.dst == dst for s in prefix) or dst == src:
                # CYCLE PRUNE — and it is a FRONTIER prune, not a correctness one. Stated exactly,
                # because the difference is the whole reason this comment exists: a cyclic
                # re-entry to a node X can NEVER outrank the acyclic path to X, because the cyclic
                # path CONTAINS the acyclic one as a prefix and `path_weight` multiplies further
                # kind weights (≤ 1.0) by a further `DEPTH_DECAY` (< 1.0). Weight along a path is
                # therefore monotonically non-increasing, so `_better` would discard the cyclic
                # route anyway. MEASURED, not reasoned-to after the fact: removing this clause
                # left every result-level assertion GREEN, which is exactly how a prune gets
                # mislabelled as a correctness guard and then trusted as one.
                #
                # What it DOES buy is work: without it the frontier carries every cyclic
                # re-entry forward, and at a raised `MAX_HOPS` a dense cycle makes that growth
                # exponential. `cycles_pruned` is what makes that observable — and therefore
                # pinnable — rather than a claim in a comment.
                cycles_pruned += 1
                continue
            steps = prefix + (ExpansionStep(src=src, kind=kind, dst=dst),)
            if not _admits(dst):
                continue                           # not readable ⇒ neither admitted nor extendable
            path = ExpansionPath(seed=seed, node=dst, depth=depth, steps=steps,
                                 weight=path_weight(steps))
            known = frontier[seed].get(dst)
            frontier[seed][dst] = path if known is None else _better(known, path)
        for seed, found in frontier.items():
            for node, path in found.items():
                reached[seed][node] = path
                current = best.get(node)
                best[node] = path if current is None else _better(current, path)

    # THE SEED RULE — a seed is never an expansion RESULT, by any route, including a route from a
    # different seed. Two things justify it, and neither is tidiness:
    #   * a seed already matched the query directly and is already ranked on that merit. K1 exists
    #     to surface what direct matching MISSED (doc 55:80, "an answer only reachable via one
    #     hop"); re-scoring an item that was found is the GRAPH tier's job, not this one;
    #   * it is what makes a cycle harmless. `a→b→a` returns `a` at depth 2 on both engines, and
    #     with `a` seeded, a mutual `depends_on` pair would otherwise boost each other every
    #     recall — self-reinforcement dressed up as relevance.
    for seed in seed_set:
        best.pop(seed, None)
    return ExpansionResult(paths=best, seeds_used=tuple(sorted(seed_set)),
                           rows_truncated=truncated, cycles_pruned=cycles_pruned)


def select_seeds(ranked: Sequence[Tuple[str, float]],
                 cap: int = SEED_CAP) -> Tuple[Tuple[str, ...], int]:
    """The seed set: the top `cap` ids that ACTUALLY MATCHED, plus how many were dropped.

    `ranked` is `(id, MATCH score)` — and "match" is load-bearing, not a synonym for the fused
    score. See the two paragraphs below; both are bugs this signature exists to prevent, and both
    were found by running the thing rather than by reading it.

    **A seed must have scored.** Doc 55:41 says "seed HITS (FTS + vector) → bounded expansion", and
    an item the direct tiers scored 0.0 is not a hit — it is a row that happened to be in the
    store. Taking the top-N of everything instead is wrong in a way that only shows up on a SMALL
    store, which is the worst place for a bug to hide: with fewer items than the cap, every item
    becomes a seed, every reachable node is therefore also a seed, and (by the seed rule in
    `walk_paths`) the expansion returns nothing at all.

    **The score must be the MATCH terms only — never the fused score.** Doc 55:41 names the seed
    tiers exactly: "FTS + vector". The DB.S5 recency/usage terms are deliberately excluded, and the
    reason is a live feedback loop rather than a purity argument: `recall_relevant` STAMPS every hit
    it returns (`store.py`), including the ones expansion admitted. Seed on the fused score and
    that stamp gives a hop-admitted item a non-zero score on the NEXT recall, promoting it to a
    seed — from which the walk reaches two MORE hops. Repeat, and the reachable set creeps past the
    bound K1 exists to impose, without any single recall ever walking three hops. Seeding on the
    match terms makes the seed set a function of the QUERY alone, so a cold store and a store that
    has answered a thousand recalls expand identically.

    Truncation is RETURNED, never swallowed — the caller reports it.
    """
    hits = [item_id for item_id, score in ranked if score > SEED_MIN_SCORE]
    seeds = tuple(hits[:max(int(cap), 0)])
    return seeds, max(len(hits) - len(seeds), 0)


def expand(fetch: Any, seeds: Sequence[str], *, visible: Optional[set] = None,
           max_hops: int = MAX_HOPS) -> ExpansionResult:
    """Run the traversal through `fetch(seeds, max_hops) -> rows` and chain the result.

    `fetch` is the backend seam (`expand_from`), passed in rather than reached for, so this module
    stays connectionless and the same walk logic serves both engines and every test double. No
    seeds or no fetcher yields the EMPTY result — which scores 0.0 for every item and therefore
    leaves the ranking exactly as it was.

    A `fetch` that RAISES is deliberately NOT caught here. The degrade notice belongs to the tier
    that knows which subsystem failed and how to name it, exactly as the semantic and lexical tiers
    report their own (`tiered.py`); swallowing it at this depth would make an expansion that never
    ran indistinguishable from a graph with no edges — the precise silence D5 exists to end.
    """
    if not seeds or fetch is None:
        return EMPTY
    rows = fetch(list(seeds), max_hops)
    return walk_paths(rows or (), seeds=seeds, visible=visible, max_hops=max_hops)
