"""DB.S8a — THE at-scale memory fixture: deterministic, seeded, bulk-loaded, with ground truth.

Every DB.S8 leg reads its corpus from here. One generator, so the scale legs, the contract legs
and the quality harness are all measuring the SAME store rather than three stores that happen to
be the same size.

FOUR properties, each of which a previous stage got bitten by the absence of:

**1. DETERMINISTIC.** Everything derives from `ScaleSpec.seed` through one `random.Random`. No
wall clock, no `uuid4`, no set iteration order. The same spec produces byte-identical items on
every machine and every run, which is what lets a quality number be COMPARED to a stored baseline
rather than merely printed.

**2. BULK, THROUGH THE REAL WRITE.** `put` opens a connection and commits per item: 2,000 rows
take 1.43s, so 100k take 71.7s — measured, and unacceptable for a leg that must run in CI. The
loaders below hoist the connection and the commit and call `backend._put_on`, which IS `put`'s
body. They do NOT carry their own INSERT. That distinction is the whole point: doc 84 carries
SHIM-FALSE-GREEN as a 🔴 row precisely because a fixture that hand-mirrors the production
statement proves the fixture's statement, and every column the real writer projects
(`scope_columns_from_doc`, `validity_columns_from_doc`, the DB.S7a edge projection) would be free
to drift out from under it silently. Same rows, one transaction: 1.9s at 100k.

**3. N IS DECLARED, NEVER SILENTLY REDUCED.** `ScaleSpec.n_items` is what the corpus HAS, and
`Corpus.declared_n` carries it into every report. A leg that quietly ran 2,000 rows while its name
said 100,000 is the silent-cap failure — it reads as coverage it does not have. If a leg wants a
smaller N it must say the smaller number out loud.

**4. GROUND TRUTH, PLANTED.** A quality number needs a right answer. Each `Probe` plants two items
and knows which they are:

  * the DIRECT item carries a token that occurs NOWHERE else in the corpus, so any working lexical
    tier finds it;
  * the HOP item shares NO token with the query at all — it is drawn from a disjoint vocabulary —
    and is reachable ONLY by walking the `depends_on` edge from the direct item. It is what
    "an answer only reachable via one hop" (doc 55:80) looks like as a measurable thing: no
    lexical, FTS or vector arm can surface it, and the expansion arm must.

That asymmetry is deliberate and it is what makes the A→D threshold mean something. If the hop
item shared even one query token, the direct arms would score it and the expansion arm's gain
would be measuring vocabulary rather than traversal.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import _support  # noqa: F401  (puts src/ on the path)

from mokata.memory import scope as _scope
from mokata.memory.item import (BEST_PRACTICE, CONTEXT, DECISION, GUARDRAIL, PERSISTENT,
                                REFERENCE, RULE, SUPERSEDED, MemoryItem)

# ---------------------------------------------------------------- the vocabularies
# Three DISJOINT token pools. The disjointness is load-bearing, not tidiness: it is what lets a
# probe's ground truth be stated as a fact about the corpus rather than hoped for. A token from
# one pool can never accidentally give an item a score it was not meant to have.
#
#   * `_TOPIC`   — the shared, contested vocabulary. Every filler item draws from it, so a probe
#                  query that names a topic has thousands of plausible-looking competitors and the
#                  ranking has real work to do.
#   * `_HOP`     — reachable-only vocabulary. Used by hop items and by NOTHING else, and never
#                  placed in a query, so a hop item's lexical and FTS score against any probe is
#                  exactly 0.0 by construction.
#   * the probe token `mk####` — unique per probe, in no pool, in exactly one item.
_TOPIC: Tuple[str, ...] = (
    "auth", "session", "token", "cache", "queue", "worker", "schema", "migration",
    "index", "query", "retry", "timeout", "backoff", "shard", "replica", "leader",
    "quorum", "commit", "rollback", "isolation", "latency", "throughput", "budget",
    "quota", "tenant", "scope", "policy", "audit", "ledger", "digest", "envelope",
    "handler", "router", "adapter", "codec", "buffer", "stream", "cursor", "pool",
)
_HOP: Tuple[str, ...] = (
    "zephyr", "quokka", "obsidian", "marzipan", "tessellate", "phalanx", "cobalt",
    "lantern", "trellis", "vermilion", "gantry", "solstice", "quiver", "halcyon",
)
_KINDS: Tuple[str, ...] = (RULE, GUARDRAIL, BEST_PRACTICE, CONTEXT, REFERENCE)

#: How many genuine competitors each HARD probe plants around its answer. Chosen to sit ABOVE the
#: default `SEED_CAP` (10) and BELOW the default lexical over-fetch (50): the answer is then
#: nominated but NOT seeded at the default setting, which is precisely the regime where the
#: SEED_CAP finding (10 seeds taken from 50 nominated rows) changes the result rather than merely
#: describing it.
_HARD_DISTRACTORS = 24


def probe_token(index: int) -> str:
    """The token unique to probe `index`. `[a-z0-9]+` so both tokenizers see exactly one term."""
    return f"mk{index:04d}"


# ---------------------------------------------------------------- the spec + the corpus
@dataclass(frozen=True)
class ScaleSpec:
    """WHAT THE CORPUS IS, declared. Every field appears in `Corpus.describe()`, so a leg cannot
    report a size it did not run."""

    n_items: int = 2_000
    projects: int = 3                  # M
    seats: int = 4                     # K
    probes: int = 40
    #: HARD probes — the ones a knob sweep can actually see. Each plants a chain rather than a
    #: pair, and its direct answer is DELIBERATELY not rank 1.
    #:
    #: The easy probes above saturate every metric: their direct answer carries a corpus-unique
    #: token so it ranks first, and their hop answer is ONE `depends_on` hop away, the
    #: strongest-weighted kind. Measured, not assumed — the first knob sweep scored 1.000 on every
    #: single setting, including settings that break the K1 bound, so it could not distinguish a
    #: good `SEED_CAP` from a bad one or say anything about `DEPTH_DECAY` at all. A fixture that
    #: makes every configuration look perfect cannot tune anything.
    #:
    #: A hard probe fixes both halves. Its query names only COMMON vocabulary, so its direct answer
    #: sits mid-pack among genuine competitors instead of at rank 1 — which is what makes
    #: `SEED_CAP` load-bearing, because the answer only seeds an expansion if it is in the top
    #: `SEED_CAP` of the match ranking. And its hop answer is TWO hops away across a weaker kind,
    #: which is what makes `DEPTH_DECAY` and `KIND_WEIGHT` observable.
    hard_probes: int = 0
    seed: int = 20260801
    team: str = "acme"
    #: Stamp a 64-dim hashing embedding into each item's provenance at generation time. The
    #: semantic tier without one embeds every candidate at READ time (`tiered.py`), which is an
    #: O(N) embedder call per recall — fine at 9 items, not at 10,000. OFF by default because it
    #: roughly triples the doc bytes and only the quality harness's arm C needs it.
    stamp_embeddings: bool = False

    def __post_init__(self) -> None:
        planted = 2 * self.probes + 3 * self.hard_probes + _HARD_DISTRACTORS * self.hard_probes
        if self.n_items < planted:
            raise ValueError(
                f"n_items={self.n_items} cannot hold {self.probes} probes (2 planted items each) "
                f"plus {self.hard_probes} hard probes (3 planted + {_HARD_DISTRACTORS} "
                "distractors each). Raise n_items or lower the probe counts — the fixture will "
                "not silently plant fewer.")

    def project_key(self, index: int) -> str:
        return f"proj-{index:02d}"

    def seat_user(self, index: int) -> str:
        return f"seat-{index:02d}"


@dataclass(frozen=True)
class Probe:
    """One ground-truth query and the two items that answer it."""

    index: int
    query: str
    direct_id: str
    hop_id: str
    project: str            # the project key both planted items are scoped to
    topic: str
    #: A HARD probe: the direct answer is mid-pack rather than rank 1, and the hop answer is two
    #: hops out across a weaker kind. See `ScaleSpec.hard_probes`.
    hard: bool = False

    @property
    def relevant(self) -> Tuple[str, str]:
        """THE right answer. Both items, and the order is not significant."""
        return (self.direct_id, self.hop_id)


@dataclass
class Corpus:
    spec: ScaleSpec
    items: List[MemoryItem] = field(default_factory=list)
    probes: List[Probe] = field(default_factory=list)

    @property
    def declared_n(self) -> int:
        """The N this corpus DECLARES — and it is the same number it has. Asserted, not trusted:
        a generator bug that planted fewer would otherwise be reported as a full-size run."""
        assert len(self.items) == self.spec.n_items, (
            f"corpus holds {len(self.items)} items but declares {self.spec.n_items} — a leg "
            "reporting the declared number would be reporting coverage it does not have")
        return self.spec.n_items

    def describe(self) -> str:
        return (f"N={self.declared_n} items · M={self.spec.projects} projects · "
                f"K={self.spec.seats} seats · {len(self.probes)} probes · seed={self.spec.seed}")

    def context_for(self, probe: Probe, seat: int = 0) -> _scope.ScopeContext:
        """The reading context a probe is answered in: this team, this project, this seat. Both
        planted items are project-scoped to `probe.project`, so they are on the path for any seat
        working in that project — the quality arms measure RANKING, never visibility."""
        return _scope.ScopeContext(team=self.spec.team, project=probe.project,
                                   user=self.spec.seat_user(seat))

    def seat_context(self, seat: int, project: int = 0) -> _scope.ScopeContext:
        return _scope.ScopeContext(team=self.spec.team,
                                   project=self.spec.project_key(project),
                                   user=self.spec.seat_user(seat))

    def by_id(self) -> Dict[str, MemoryItem]:
        return {it.id: it for it in self.items}


# ---------------------------------------------------------------- generation
def _sentence(rng: random.Random, pool: Sequence[str], n: int) -> str:
    return " ".join(rng.choice(pool) for _ in range(n))


def _embedding_for(text: str) -> List[float]:
    from mokata.memory.embed import HashingEmbedder
    return HashingEmbedder()(text)


def generate(spec: Optional[ScaleSpec] = None) -> Corpus:
    """Build the corpus. Pure, seeded, and the ONLY producer — every leg calls this."""
    spec = spec or ScaleSpec()
    rng = random.Random(spec.seed)
    corpus = Corpus(spec=spec)
    # A fixed clock. `now_iso()` would make every run a different corpus, which would make the
    # stored quality baseline uncomparable — the exact thing a seeded fixture exists to prevent.
    base_year = 2026

    def created(n: int) -> str:
        # Deterministic, ordered, and spread across a year so the DB.S5 recency term has a real
        # distribution to act on rather than one instant.
        day = n % 365
        return f"{base_year}-01-01T{n % 24:02d}:{n % 60:02d}:00+00:00" if day == 0 else \
               f"{base_year}-{1 + day // 31:02d}-{1 + day % 28:02d}T{n % 24:02d}:{n % 60:02d}:00+00:00"

    def stamp(item: MemoryItem, n: int) -> MemoryItem:
        item.provenance = {"author": spec.seat_user(n % spec.seats),
                           "created_at": created(n),
                           "source": "db-s8-fixture"}
        # M-1/R9 — the CONSENT CHAIN, which is what S-3 is a contract about: who WROTE it
        # (`provenance.author`), who let it LAND (`approved_by`), and WHICH hash-chained ledger
        # entry says so (`approval_ledger_id`). The approver is deliberately a DIFFERENT seat from
        # the author wherever there is more than one seat — on a poisoned proposal those are two
        # different people, which is the whole of R9, and a fixture where they always coincide
        # cannot tell a read path that returns one from a read path that returns both.
        item.approved_by = spec.seat_user((n + 1) % spec.seats)
        item.approved_at = created(n)
        item.approval_ledger_id = n + 1
        if spec.stamp_embeddings:
            item.provenance["_embedding"] = _embedding_for(f"{item.subject} {item.value}")
        return item

    planted = (2 * spec.probes + 3 * spec.hard_probes
               + _HARD_DISTRACTORS * spec.hard_probes)
    n_filler = spec.n_items - planted

    # --- the filler corpus: the competition ------------------------------------------------
    # Spread across every scope level, every project and every seat, so the scope predicate has
    # something to exclude on every axis a contract test names.
    for n in range(n_filler):
        proj = n % spec.projects
        seat = n % spec.seats
        level, sid = _assign_scope(n, spec, proj, seat)
        topics = _sentence(rng, _TOPIC, 8)
        item = MemoryItem(
            id=f"fill-{n:07d}",
            subject=f"{rng.choice(_TOPIC)} {rng.choice(_TOPIC)} note {n}",
            value=f"{topics} — filler {n}",
            mtype=DECISION if n % 7 == 0 else PERSISTENT,
            kind=DECISION if n % 7 == 0 else _KINDS[n % len(_KINDS)],
            scope_level=level, scope_id=sid,
        )
        corpus.items.append(stamp(item, n))

    # --- the planted probes: the right answers ---------------------------------------------
    for q in range(spec.probes):
        tok = probe_token(q)
        topic = _TOPIC[q % len(_TOPIC)]
        proj = spec.project_key(q % spec.projects)
        hop_id = f"probe-{q:04d}-hop"
        direct_id = f"probe-{q:04d}-direct"
        # The HOP item FIRST — the direct item's `depends_on` must name an id that exists, or the
        # v5 backfill would (correctly) skip the edge as dangling.
        hop = MemoryItem(
            id=hop_id,
            subject=f"{_HOP[q % len(_HOP)]} {_HOP[(q + 3) % len(_HOP)]}",
            # DISJOINT VOCABULARY — no `_TOPIC` word, no probe token. Its lexical score against
            # this probe's query is 0.0 by construction, on both the Jaccard floor and FTS.
            value=_sentence(rng, _HOP, 10),
            mtype=PERSISTENT, kind=CONTEXT,
            scope_level=_scope.PROJECT, scope_id=proj,
        )
        direct = MemoryItem(
            id=direct_id,
            subject=f"{topic} {tok}",
            value=f"{tok} {topic} {_sentence(rng, _TOPIC, 6)}",
            mtype=PERSISTENT, kind=BEST_PRACTICE,
            scope_level=_scope.PROJECT, scope_id=proj,
            depends_on=[hop_id],        # → the typed `depends_on` edge the walk rides
        )
        corpus.items.append(stamp(hop, n_filler + 2 * q))
        corpus.items.append(stamp(direct, n_filler + 2 * q + 1))
        corpus.probes.append(Probe(index=q, query=f"{tok} {topic}", direct_id=direct_id,
                                   hop_id=hop_id, project=proj, topic=topic))

    _plant_hard_probes(corpus, rng, n_filler + 2 * spec.probes, created, stamp)
    _plant_lineage(corpus, rng)
    return corpus


def _plant_hard_probes(corpus: "Corpus", rng: random.Random, offset: int,
                       created: Any, stamp: Any) -> None:
    """The probes a knob sweep can see. Each plants a 3-item CHAIN plus its own competitors.

        direct --derives_from--> mid --derives_from--> hop

The chain is `depends_on`, TWO hops, and the depth is what makes `DEPTH_DECAY` observable: the
    hop answer arrives at `EDGE_WEIGHT x 1.0 x 1.0 x DEPTH_DECAY**2` (0.019 at the defaults)
    instead of the easy probes' single strong hop at 0.15.

    An earlier draft used `derives_from` to exercise a weaker, UNWIRED kind. It could not work and
    the reason is worth recording: only the three WIRED kinds have an item field for the projection
    to read (`edges._ITEM_FIELD`), so `item.derives_from = [...]` sets an attribute nothing
    persists and the chain would simply have no second edge. Exercising an unwired kind's weight
    needs an edge row written directly, which is the substrate's business rather than the
    fixture's; `k1_bound_violations` covers those kinds arithmetically instead.

    The query names only COMMON topic words, and `_HARD_DISTRACTORS` competitors share them, so the
    direct answer is a genuine mid-pack hit rather than a unique-token gimme.
    """
    spec = corpus.spec
    n = offset
    for q in range(spec.hard_probes):
        topic_a = _TOPIC[(q * 3) % len(_TOPIC)]
        topic_b = _TOPIC[(q * 3 + 1) % len(_TOPIC)]
        proj = spec.project_key(q % spec.projects)
        base = f"hard-{q:04d}"
        hop = MemoryItem(id=f"{base}-hop", subject=f"{_HOP[q % len(_HOP)]} conclusion",
                         value=_sentence(rng, _HOP, 10),
                         mtype=PERSISTENT, kind=CONTEXT,
                         scope_level=_scope.PROJECT, scope_id=proj)
        mid = MemoryItem(id=f"{base}-mid", subject=f"{_HOP[(q + 5) % len(_HOP)]} step",
                         value=_sentence(rng, _HOP, 8),
                         mtype=PERSISTENT, kind=CONTEXT,
                         scope_level=_scope.PROJECT, scope_id=proj)
        mid.depends_on = [hop.id]
        direct = MemoryItem(id=f"{base}-direct", subject=f"{topic_a} {topic_b} decision",
                            value=f"{topic_a} {topic_b} {_sentence(rng, _TOPIC, 6)}",
                            mtype=DECISION, kind=DECISION,
                            scope_level=_scope.PROJECT, scope_id=proj)
        direct.depends_on = [mid.id]
        for item in (hop, mid, direct):
            corpus.items.append(stamp(item, n)); n += 1
        # The competitors — same two topics, same scope, so they are real rivals for the top slots.
        for d in range(_HARD_DISTRACTORS):
            rival = MemoryItem(id=f"{base}-rival-{d:02d}",
                               subject=f"{topic_a} {topic_b} note {d}",
                               value=f"{topic_a} {topic_b} {_sentence(rng, _TOPIC, 7)}",
                               mtype=PERSISTENT, kind=_KINDS[d % len(_KINDS)],
                               scope_level=_scope.PROJECT, scope_id=proj)
            corpus.items.append(stamp(rival, n)); n += 1
        corpus.probes.append(Probe(index=1000 + q, query=f"{topic_a} {topic_b}",
                                   direct_id=direct.id, hop_id=hop.id, project=proj,
                                   topic=topic_a, hard=True))


def _assign_scope(n: int, spec: ScaleSpec, proj: int, seat: int) -> Tuple[str, str]:
    """Which scope level this filler item lives at. Deliberately NOT random: a fixed rotation, so
    every level has a known, reproducible population at every N.

    **Personal items carry a non-empty `scope_id`.** That is the one choice here worth naming:
    `scope.on_path` treats an EMPTY personal id as "matches any personal reader" (the legacy /
    local-default carve-out), so a fixture that left it blank would make every seat's private
    items visible to every other seat and the S-2 no-cross-seat-leak contract would pass against a
    corpus that has no seats to leak between.
    """
    slot = n % 5
    if slot == 0:
        return _scope.GLOBAL, ""
    if slot == 1:
        return _scope.TEAM, spec.team
    if slot == 2:
        return _scope.PROJECT, spec.project_key(proj)
    if slot == 3:
        return _scope.CATEGORY, _scope.CONVENTIONAL_CATEGORIES[n % len(
            _scope.CONVENTIONAL_CATEGORIES)]
    return _scope.PERSONAL, spec.seat_user(seat)


def _plant_lineage(corpus: Corpus, rng: random.Random) -> None:
    """Typed edges beyond the probes' `depends_on`: `supersedes` lineage and `about_code` anchors.

    They exist so the walk is not exercised on ONE kind, and so the edge table at scale is not a
    single uniform shape. `supersedes` targets are flipped to `status=SUPERSEDED` — which is what
    a real lineage looks like, and it also means the visibility prune has something to drop
    mid-walk rather than being asserted against a graph where every node is visible anyway.
    """
    fillers = [it for it in corpus.items if it.id.startswith("fill-")]
    if len(fillers) < 20:
        return
    step = max(len(fillers) // 200, 2)          # ~200 lineage pairs at any N, deterministic
    for i in range(0, len(fillers) - step, step * 2):
        newer, older = fillers[i], fillers[i + step]
        newer.supersedes = [older.id]
        older.status = SUPERSEDED
    for i in range(0, len(fillers), max(step * 3, 3)):
        fillers[i].about_code = [f"src/pkg/mod_{i % 97:02d}.py"]
    # `rng` is threaded in (and deliberately unused) so this stays a pure function of the seed if
    # a future variant wants randomised lineage — the caller must not have to re-derive an rng.
    _ = rng


# ---------------------------------------------------------------- bulk loading
def load_sqlite(backend: Any, corpus: Corpus, *, chunk: int = 5_000) -> int:
    """Land `corpus` in a `SQLiteBackend` through the REAL write, in one transaction per chunk.

    `backend._put_on` IS `SQLiteBackend.put`'s body — the same INSERT, the same scope/validity
    projections, the same DB.S7a edge projection. What is hoisted is only the connection and the
    commit. Nothing here knows the table's shape, which is exactly why it cannot drift from it.
    """
    written = 0
    items = corpus.items
    for start in range(0, len(items), chunk):
        with backend._connect() as conn:
            for item in items[start:start + chunk]:
                backend._put_on(conn, item)
                written += 1
            conn.commit()
    return written


def load_postgres(backend: Any, corpus: Corpus) -> int:
    """The same, for a `PostgresBackend`. `_pg` connections are AUTOCOMMIT, so `put` per item is a
    commit per item; one explicit transaction makes it one.

    The edge projection is capability-probed inside `_put_on` (`supports_edges`) exactly as it is
    on the production path — a v4 shared store loads its items and no edges, which is the same
    thing a real un-migrated team would do.
    """
    conn = backend._conn
    written = 0
    with conn.transaction():
        for item in corpus.items:
            backend._put_on(conn, item)
            written += 1
    return written
