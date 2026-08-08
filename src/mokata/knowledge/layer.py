"""B3 + B6 — the knowledge layer.

`KnowledgeLayer` is the typed structural-query surface the engine uses. It selects its
backend THROUGH the existing capability router (`router.resolve("code_graph")`) — there
is deliberately no second tool-detection path. Policy: prefer the graph when the router
resolves to a real graph tool; otherwise use the grep floor. If the graph backend errors
at query time, the layer degrades to grep rather than failing (A3).

B6: a story's queries are recorded and can be persisted via the existing state surface,
so analysis enriches a durable layer instead of being recomputed each run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import re

from ..manifest import ManifestError
from .graph_backend import CodeReviewGraphBackend, GraphQueryClient
from .grep_backend import GrepBackend
from .query import BASIS_LEXICAL, BackendError, GraphBackend, QueryResult

# Tools that ARE real structural graphs (everything else is the lexical floor).
GRAPH_TOOLS = ("code-review-graph", "serena", "neo4j")

STORY_ANALYSIS_PREFIX = "story_analysis__"


def _join_note(existing: str, add: str) -> str:
    """Pipe-join a classed note onto a QueryResult.note (never overwrites — same convention as
    B4's `surface_staleness`)."""
    if not add:
        return existing
    return f"{existing} | {add}" if existing else add


def _supports_kind(backend: Any, kind: str) -> bool:
    """CRG-NAV — does `backend` have an op for `kind`? A backend that predates the declaration
    (or a test double) is assumed to answer everything, so nothing existing changes route."""
    fn = getattr(backend, "supports_kind", None)
    if not callable(fn):
        return True
    try:
        return bool(fn(kind))
    except Exception:
        return True


def _floor_backend(router: Any, root: str) -> GraphBackend:
    """The honest degrade target BENEATH a real graph: the AST floor on a Python repo
    (answers real structural edges), the grep emergency floor otherwise. GR.S2(k): when the
    adopted graph is unrecoverable, queries fall to THIS floor loudly, not to bare grep."""
    from .ast_backend import AstBackend
    has_py = False
    det = getattr(router, "detector", None)
    if det is not None:
        try:
            det.root = root
            has_py = det.is_present("ast", {"detect": {"type": "python_files"}})
        except (AttributeError, OSError):
            has_py = False
    if has_py:
        return AstBackend(root=root, grep=GrepBackend(root=root, name="grep"))
    return GrepBackend(root=root, name="grep")


def select_backends(
    router: Any,
    root: str,
    client: Optional[GraphQueryClient] = None,
):
    """Pick (primary, fallback) backends from the router's code_graph resolution.

    Returns the grep floor as the fallback only when the primary is a real graph, so a
    graph failure degrades cleanly. When the floor is already primary, fallback is None.

    Availability probing is UNIFORM across graph tools (Stage 39 / M5), it just happens at the
    layer each tool lives in: command-based tools (`code-review-graph`, `serena`, `ripgrep`) are
    probed by the detector — `router.resolve` only returns a tool whose command is present, so an
    absent tool never reaches here (it resolves to grep). The external `neo4j` needs an extra
    BUILD-TIME probe (below) because a present driver module ≠ a reachable DB. In all cases a
    tool that's present-but-broken at query time raises `BackendError`, which `_run` degrades to
    the grep floor — so the floor is the universal guarantee no matter where a tool fails.
    """
    # GR.S2: the `ast` provider is routed by the `python_files` strategy, which reads the
    # detector's root. Point it at THIS layer's root so `ast` resolves only where it can
    # answer (a zero-Python repo routes past it to the lexical floor, byte-identical).
    try:
        if getattr(router, "detector", None) is not None:
            router.detector.root = root
    except AttributeError:
        pass

    try:
        res = router.resolve("code_graph")
    except (ManifestError, AttributeError):
        res = None

    # GR.S2 (g)+(h): detect-and-OFFER. A real graph binary present on PATH but NOT pinned in
    # the committed chain may be USED read-only via the router (pinning stays gated — P2). The
    # first such USE fires a once-per-repo disclosed + ledgered notice (supply-chain, P14). A
    # human who declined adoption for this repo is respected (no auto-use).
    from . import graph_adopt, user_prefs
    overlay = graph_adopt.detected_graph_overlay(router, root)
    if overlay and not user_prefs.graph_declined(root):
        graph_adopt.disclose_first_use(root, overlay)
        return (CodeReviewGraphBackend(name=overlay, root=root, client=client),
                _floor_backend(router, root))

    # GR.S2 (d): AST selection now lives IN the router chain. When the chain resolves to
    # `ast` (a real graph tool wasn't present but the repo is Python), build the AST floor
    # from that resolution — no hardcoded `_has_python` branch bolted on after the chain.
    if res is not None and res.available and res.tool == "ast":
        from .ast_backend import AstBackend
        return AstBackend(root=root, grep=GrepBackend(root=root, name="grep")), None

    if res is not None and res.available and res.tool in GRAPH_TOOLS:
        if res.tool == "neo4j":
            # SIMP.S2 (0.0.15): the Neo4j code-graph backend is DEPRECATED (a 3rd DB contradicts
            # two-modes-one-shape). WARN once per repo the first time it is selected; removed
            # 0.0.17. No migration — the graph is derived data (the warn says re-index). Guarded on
            # a real repo dir so a bare `root="."` unit call fabricates no `.mokata`.
            import os as _os
            from .. import MOKATA_DIR as _MD, deprecation as _dep
            _md = _os.path.join(root, _MD)
            if _os.path.isdir(_md):
                _dep.warn_deprecated("neo4j", _md)
            # External Neo4j graph (Stage 35f): build its client from env; if it can't be
            # built (no driver / no NEO4J_* env / DB down) degrade cleanly to the grep floor.
            #
            # D5 — the degrade is LOUD now. Reaching this branch means the graph was CONFIGURED:
            # `neo4j` is wired into no default profile (P8), so the router only resolves it when the
            # user put it in their own `code_graph` chain AND the driver module imports. A user who
            # never wired a graph resolves to ripgrep/grep and never gets here — that is the default,
            # not a degrade, and it must stay silent. A user who DID wire it and gets the grep floor
            # is looking at a live failure, and used to be told "no codebase graph wired"
            # (`graph_guidance`) — a sentence that blames their config for mokata's silence.
            from .neo4j_backend import Neo4jUnavailable, connect_neo4j_client
            cfg: Dict[str, Any] = {}
            try:
                cfg = router.manifest.tool_config("neo4j")
            except (AttributeError, ManifestError):
                cfg = {}
            gclient = client
            if gclient is None:
                try:
                    gclient = connect_neo4j_client(cfg)
                except Neo4jUnavailable as exc:
                    from ..degrade import FAILURE_UNREACHABLE, note_degraded
                    from ..errors import failure_class_of
                    note_degraded(
                        "code-graph", failure_class_of(exc) or FAILURE_UNREACHABLE,
                        fallback="the code graph fell back to the grep floor",
                        fix="check the graph tool / NEO4J_* env", detail=str(exc))
                    return GrepBackend(root=root, name="grep"), None
            return (CodeReviewGraphBackend(name="neo4j", root=root, client=gclient),
                    _floor_backend(router, root))
        primary = CodeReviewGraphBackend(name=res.tool, root=root, client=client)
        return primary, _floor_backend(router, root)

    # No adopted graph tool and no AST (zero-Python repo, or the chain resolved to a lexical
    # tool). Grep is the EMERGENCY floor (degraded=True, byte-identical to before). The chain
    # resolves `ast` ahead of ripgrep/grep on Python repos, so reaching here means either the
    # repo has no `.py` files or only the lexical floor was wired.
    name = res.tool if (res is not None and res.available and res.tool) else "grep"
    return GrepBackend(root=root, name=name), None


class KnowledgeLayer:
    def __init__(self, primary: GraphBackend,
                 fallback: Optional[GraphBackend] = None,
                 index: Any = None, freshness: Any = None) -> None:
        self.primary = primary
        self.fallback = fallback
        self.index = index            # optional KnowledgeIndex (B4) for staleness
        # GR.S4: the read-time freshness controller. None (the default, and every directly-
        # constructed layer) means NO freshness reconcile — behaviour is byte-identical. It is
        # attached in `from_surface` for production so every real answer front-runs the
        # dirty-set/HEAD reconcile (the FRESHNESS-BEFORE-ANSWER invariant).
        self.freshness = freshness
        self.history: List[QueryResult] = []

    # --- construction (always via the router) -------------------------------
    @classmethod
    def from_router(cls, router: Any, root: str,
                    client: Optional[GraphQueryClient] = None,
                    index: Any = None, freshness: Any = None) -> "KnowledgeLayer":
        primary, fallback = select_backends(router, root, client=client)
        return cls(primary, fallback, index=index, freshness=freshness)

    @classmethod
    def from_surface(cls, surface: Any,
                     client: Optional[GraphQueryClient] = None,
                     index: Any = None) -> "KnowledgeLayer":
        # GR.S4: attach the read-time freshness controller so every production answer is fresh by
        # contract. Degrade-clean (None ⇒ no reconcile) when the run-state surface can't be
        # reached — freshness never becomes a reason a query can't run.
        from .freshness import FreshnessController
        return cls.from_router(surface.router, surface.root, client=client,
                               index=index,
                               freshness=FreshnessController.for_surface(surface))

    # --- introspection ------------------------------------------------------
    @property
    def backend_name(self) -> str:
        return self.primary.name

    @property
    def uses_graph(self) -> bool:
        return self.primary.is_graph

    # --- typed queries ------------------------------------------------------
    def _run(self, kind: str, target: str, depth: int = 1) -> QueryResult:
        # GR.S4 — FRESHNESS-BEFORE-ANSWER: reconcile the graph against the dirty-set + HEAD probe
        # BEFORE answering. A graph KNOWN stale rebuilds first; a graph whose rebuild FAILED
        # answers from the AST floor on CURRENT files with a loud classed note — never from stale
        # graph data. Degrade-clean: freshness never breaks a query.
        fresh = None
        if self.freshness is not None:
            try:
                fresh = self.freshness.ensure_fresh(self)
            except Exception:
                fresh = None
        if fresh is not None and fresh.answer_from_floor and self.fallback is not None:
            floor = self.fallback.query(kind, target, depth=depth)
            # D2 — a demotion, not a flag flip: the floor may itself have certified a verified
            # ZERO, and a KNOWN-stale graph is not a basis on which to pass that certification on.
            # `demote_to_floor` moves the basis and the reason together so the two can't drift.
            floor.demote_to_floor()
            floor.note = _join_note(floor.note, fresh.note)
            self._surface_index_staleness(floor)
            self.history.append(floor)
            return floor
        # CRG-NAV (b)+(d) — a MAPPING gap is not a FAILURE. When the adopted graph declares it has
        # no op for this kind (code-review-graph exposes no definition-site pattern), route the
        # kind to the floor DIRECTLY: no doomed graph call, no wasted `recover()` re-probe, and no
        # `note_degraded` alarm blaming a tool that is perfectly healthy. The answer says which
        # backend produced it and why, and the FLOOR's own verdict decides `degraded` — the AST
        # floor answers `defs` exactly on a Python repo, so it is not a degraded answer at all.
        if self.fallback is not None and not _supports_kind(self.primary, kind):
            floor = self.fallback.query(kind, target, depth=depth)
            floor.note = _join_note(
                floor.note,
                f"the adopted graph '{self.primary.name}' exposes no '{kind}' op; "
                f"answered by the '{floor.backend}' floor")
            if fresh is not None and fresh.note:
                floor.note = _join_note(floor.note, fresh.note)
            self._surface_index_staleness(floor)
            self.history.append(floor)
            return floor
        try:
            result = self.primary.query(kind, target, depth=depth)
        except BackendError:
            # GR.S2(k): a real graph gets ONE bounded, operational recovery attempt (re-probe
            # -> index refresh/rebuild -> retry) before we degrade — kept functional
            # automatically, no durable write. Unrecoverable -> LOUD classed degrade to the
            # floor (never a silent wrong answer) + a doctor-visible notice.
            result = None
            recover = getattr(self.primary, "recover", None)
            if callable(recover) and recover():
                try:
                    result = self.primary.query(kind, target, depth=depth)
                except BackendError:
                    result = None
            if result is None:
                if self.fallback is None:
                    raise
                from ..degrade import FAILURE_UNREACHABLE, note_degraded
                note_degraded(
                    "code-graph", FAILURE_UNREACHABLE,
                    fallback=f"the code graph fell back to '{self.fallback.name}'",
                    fix="run `mokata doctor`; re-adopt with `mokata graph adopt` if it persists",
                    detail=f"graph backend '{self.primary.name}' could not answer "
                           f"{kind}({target}) and did not recover")
                result = self.fallback.query(kind, target, depth=depth)
                # D2 — the ADOPTED graph is the backend the user configured, and it failed. What
                # the floor found stands in for it, but it does not inherit the adopted graph's
                # authority: a verified zero from the floor is demoted here rather than presented
                # as the answer the wired graph would have given.
                result.demote_to_floor()
                # CRG-NAV (d): JOIN, never overwrite. This used to ASSIGN, which threw away the
                # note the floor itself attached — including the honest "grep floor — install
                # <graph> for full navigation" line — so the one answer that MOST needed the
                # floor note (a graph that failed mid-navigation) was the one that lost it. The
                # pipe-join is the same convention every other note site in this file uses.
                result.note = _join_note(
                    result.note,
                    f"primary backend '{self.primary.name}' failed and did not recover; "
                    f"fell back to '{self.fallback.name}'"
                )
        # GR.S4 — out-of-band recheck: an editor edit (no hook, HEAD unchanged) that touched a
        # file THIS answer references is caught by hash-staleness on the referenced files (bounded
        # by the result, never the repo). Rebuild + re-query once so the answer is fresh, never
        # stale. Only meaningful for a long-lived layer whose controller holds the cold baseline.
        if self.freshness is not None:
            try:
                if self.freshness.recheck_after_answer(self, result):
                    result = self.primary.query(kind, target, depth=depth)
            except Exception:
                pass
        # GR.S4 — a freshness cap / tier note rides on the answer, honestly (never a block).
        if fresh is not None and fresh.note and not fresh.answer_from_floor:
            result.note = _join_note(result.note, fresh.note)
        # B4: surface staleness for any referenced file changed since indexing.
        self._surface_index_staleness(result)
        self.history.append(result)
        return result

    def _surface_index_staleness(self, result: QueryResult) -> None:
        if self.index is not None:
            from .index import surface_staleness
            root = getattr(self.primary, "root", None)
            if root:
                surface_staleness(result, self.index, root)

    def callers(self, symbol: str) -> QueryResult:
        return self._run("callers", symbol)

    def callees(self, symbol: str) -> QueryResult:
        return self._run("callees", symbol)

    def implementers(self, name: str) -> QueryResult:
        return self._run("implementers", name)

    def imports(self, target: str) -> QueryResult:
        return self._run("imports", target)

    def defs(self, symbol: str) -> QueryResult:
        """CRG-NAV (b) — where is this symbol DEFINED (and does it exist)?"""
        return self._run("defs", symbol)

    def refs(self, symbol: str) -> QueryResult:
        """CRG-NAV (b) — everywhere this symbol is REFERENCED."""
        return self._run("refs", symbol)

    def blast_radius(self, symbol: str, depth: int = 2) -> QueryResult:
        return self._run("blast_radius", symbol, depth=depth)

    # --- semantic (the adopted graph's capability; capability-gated) ---------
    @property
    def supports_semantic(self) -> bool:
        return bool(getattr(self.primary, "supports_semantic", False))

    def semantic(self, query: str, kind: Optional[str] = None,
                 limit: int = 20) -> QueryResult:
        """Semantic search over the adopted code graph (CRG's own bundled embedding). This is
        the ADOPTED TOOL's capability, exposed through the typed API — mokata's own retrieval
        stays lexical-first (doc 85). When no semantic-capable graph is wired, degrade HONESTLY
        (empty, degraded=True) rather than answering from the lexical floor and pretending."""
        # GR.S4 — semantic answers front-run the SAME freshness reconcile as structural queries
        # (it's a separate path from `_run`, so it needs its own hook to cover "every answer").
        fresh = None
        if self.freshness is not None:
            try:
                fresh = self.freshness.ensure_fresh(self)
            except Exception:
                fresh = None
        if not self.supports_semantic:
            return QueryResult(kind="semantic", target=query, references=[],
                               backend=self.backend_name, basis=BASIS_LEXICAL,
                               note="no semantic-capable code graph wired (lexical floor)")
        try:
            result = self.primary.semantic(query, kind=kind, limit=limit)
        except BackendError:
            result = QueryResult(kind="semantic", target=query, references=[],
                                 backend=self.backend_name, basis=BASIS_LEXICAL,
                                 note="semantic search failed; degraded to no result")
        if fresh is not None and fresh.note and not fresh.answer_from_floor:
            result.note = _join_note(result.note, fresh.note)
        self.history.append(result)
        return result


def graph_structure_line(surface: Any, top_n: int = 5) -> Optional[str]:
    """GR.S4 / HP.S1 — ONE bounded, graph-derived structure line for the SessionStart briefing.

    Derived from the AST floor (real structural edges on a Python repo), tagged with the adopted
    graph when one is active. Returns None — so the briefing is BYTE-IDENTICAL — on any repo the
    structural floor can't summarise (no Python / grep-only / no layer). Never raises; bounded
    (top-N names) so it rides inside the existing ≤2k briefing budget with no second channel."""
    try:
        from .ast_backend import AstBackend
        layer = KnowledgeLayer.from_surface(surface)
        ast = None
        for b in (layer.primary, layer.fallback):
            if isinstance(b, AstBackend):
                ast = b
                break
        if ast is None:
            return None                       # grep-only / non-Python graph → absent (byte-identical)
        count, tops = ast.structure(top_n=top_n)
        if not count or not tops:
            return None
        tag = layer.backend_name if layer.uses_graph else "ast"
        return f"Code structure ({tag}): {count} Python module(s); top: {', '.join(tops)}."
    except Exception:
        return None


def run_query(layer: "KnowledgeLayer", kind: str, target: str, depth: int = 2) -> QueryResult:
    """The ONE typed-query router the MCP `query` tool and the CLI `query` command share
    (GR.S2). Routes `semantic` to the capability-gated semantic API, every structural kind to
    `_run` — so the adopted graph's semantic search reaches in-harness through the typed API
    without a second surface, and a floor-only repo degrades honestly instead of crashing."""
    if kind == "semantic":
        return layer.semantic(target)
    return layer._run(kind, target, depth=depth)


# ----------------------------------------------------- Stage 25 Part B: graph guidance
def graph_guidance(surface: Any) -> str:
    """An ACTIONABLE one-line hint for doctor/status (Stage 25 Part B).

    When a real graph is wired, point at the structural queries it unlocks (reflecting any
    configured path/endpoint from the tool's `config` block, Stage 24A). When only a floor is
    active, give a concrete next step to wire one — not just a status line.

    GRAPH-HINT (0.0.15) — the hint is keyed on the ACTUAL ANSWERING BACKEND, three ways: real
    graph / embedded AST floor / grep floor. It used to branch on `uses_graph` alone, which is
    False for BOTH floors (`AstBackend.is_graph = False` deliberately — a floor above grep, not
    a graph), so the default CRG-less Python install — exactly the install GR.S3 made important
    — was told "no codebase graph wired — running on the grep floor" while the AST floor was
    answering structurally and `degraded=False`. Both halves were false (P16). The fix is the
    MESSAGE only: `is_graph`/`uses_graph` semantics and backend selection are untouched.

    The floor branches lead with `floor '<backend_name>'` — the SAME vocabulary `mokata graph
    status` prints (graph.py: `code graph: floor '{layer.backend_name}'`) — so the two surfaces
    cannot disagree about which backend answers."""
    from .ast_backend import AstBackend
    layer = KnowledgeLayer.from_surface(surface)
    if layer.uses_graph:
        tool = layer.backend_name
        cfg = ""
        try:
            c = surface.manifest.tool_config(tool)
            if c:
                cfg = " [config: " + ", ".join(f"{k}={v}" for k, v in c.items()) + "]"
        except (ManifestError, AttributeError):
            cfg = ""            # the SAME call is narrowly caught in `select_backends` above
        return (f"code graph active ({tool}){cfg} — use `mokata query callers <sym>` / "
                f"`callees <sym>` / `blast_radius <sym>` for structural queries.")
    if isinstance(layer.primary, AstBackend):
        # The DEFAULT on a CRG-less Python repo. Structural, embedded, zero-install — and still
        # a floor, so the actionable next step (adopt a real graph) is kept, not dropped.
        return (
            f"code graph: floor '{layer.backend_name}' — the embedded AST floor is answering "
            "structurally (real Python call/import edges, no install needed) and you can run "
            "`mokata query callers <sym>` / `callees <sym>` / `blast_radius <sym>` today. It is "
            "not a full graph: adopt one for cross-language and dynamic edges (plus semantic "
            "search) — `mokata graph adopt`, or `mokata init --profile full`."
        )
    # The lexical EMERGENCY floor. Reached when the chain resolves past `ast` — a repo with no
    # `.py` files, a chain wired to a lexical tool, or a configured graph that degraded here.
    # The original wording stays because for actual-grep it was always CORRECT.
    return (
        f"code graph: floor '{layer.backend_name}' — no codebase graph wired, and no AST floor "
        "here (the embedded AST floor answers Python repos), so answers are lexical (safe, but "
        "approximate). To enable richer structural queries, install a graph tool "
        "(code-review-graph or serena) and wire it: `mokata init --profile full`, or add it via "
        "`mokata config set tools.<graph>...` / the manifest."
    )


# ----------------------------------------------- Stage 35f: live graph-proximity scorer
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")   # identifier-like tokens (len >= 3)


def make_graph_scorer(layer: Any, query: str):
    """A live graph-keyed relevance scorer for memory's tiered retrieval (closes the 35e
    SHOULD). Returns `(query, item) -> 0..1` that boosts a memory item which references a
    symbol the CODE GRAPH confirms is real and related to the query — or None when no real
    graph is wired (so the graph tier stays silent and lexical+semantic hold).

    Frugal (P11): runs one cheap graph lookup per identifier token in the query (a small set),
    not per memory item; the resulting anchor set scores items by token membership.

    D5 — a graph that FAILS here used to be indistinguishable from a graph that simply found no
    anchor: both produced `None`, both left recall running on the lexical floor, and neither said a
    word. That is the whole harm — the user who wired neo4j believes graph-proximity recall is live
    when it is not. It still degrades to the floor (the fallback keeps falling back); it just says
    so, once, loudly."""
    if layer is None or not getattr(layer, "uses_graph", False):
        return None
    anchors = set()
    failed = 0
    for tok in {t for t in _IDENT.findall(query or "")}:
        try:
            res = layer.callers(tok)          # graph confirms the symbol exists/relates
        except (BackendError, ValueError):
            # BackendError: the graph tool errored and `_run` had no fallback to fall to.
            # ValueError: the grep floor's unknown-kind guard (structurally unreachable here —
            # "callers" is a valid kind — but it is the only other class `_run` can raise).
            failed += 1
            continue
        # `_run` ALSO degrades WITHOUT raising: a primary-backend failure is caught there and the
        # grep floor answers with `degraded=True`. That path — the common one, since a graph
        # primary always has the grep fallback wired — is why catching the exception alone would
        # have left this notice unreachable. A degraded result IS the graph having failed.
        if res is not None and res.degraded and getattr(layer, "uses_graph", False):
            failed += 1
            continue
        if res is not None and not res.degraded and res.references:
            anchors.add(tok)
    if failed:
        from ..degrade import FAILURE_UNREACHABLE, note_degraded
        note_degraded("code-graph", FAILURE_UNREACHABLE,
                      fallback="recall is running on the lexical floor",
                      fix="run `mokata doctor` / check the graph tool",
                      detail=f"{failed} graph lookup(s) failed during recall")
    if not anchors:
        return None

    def _scorer(_query: str, item: Any) -> float:
        text = f"{item.subject} {item.value}"
        return 1.0 if any(a in text for a in anchors) else 0.0

    return _scorer


# --------------------------------------------------------------- B6 story bridge
@dataclass
class StoryAnalysis:
    """The persistable record of a story's structural analysis."""

    story_id: str
    summary: str
    symbols: List[str] = field(default_factory=list)
    queries: List[Dict[str, Any]] = field(default_factory=list)
    backend: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": 1,
            "story_id": self.story_id,
            "summary": self.summary,
            "symbols": list(self.symbols),
            "queries": list(self.queries),
            "backend": self.backend,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "StoryAnalysis":
        return cls(
            story_id=d["story_id"],
            summary=d.get("summary", ""),
            symbols=list(d.get("symbols", [])),
            queries=list(d.get("queries", [])),
            backend=d.get("backend", ""),
        )


def build_story_analysis(story_id: str, summary: str,
                         layer: KnowledgeLayer) -> StoryAnalysis:
    """Assemble a StoryAnalysis from the queries a layer has run this story."""
    symbols: List[str] = []
    queries: List[Dict[str, Any]] = []
    for r in layer.history:
        if r.target not in symbols:
            symbols.append(r.target)
        queries.append({"kind": r.kind, "target": r.target,
                        "count": r.count, "backend": r.backend})
    return StoryAnalysis(story_id=story_id, summary=summary, symbols=symbols,
                         queries=queries, backend=layer.backend_name)


def persist_story_analysis(store: Any, analysis: StoryAnalysis) -> str:
    """Persist a story's analysis via the state surface (explicit, never silent)."""
    return store.write(STORY_ANALYSIS_PREFIX + analysis.story_id, analysis.to_dict())


def load_story_analysis(store: Any, story_id: str) -> Optional[StoryAnalysis]:
    data = store.read(STORY_ANALYSIS_PREFIX + story_id)
    return StoryAnalysis.from_dict(data) if data else None
