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

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Optional

import re

from ..manifest import ManifestError
from .graph_backend import CodeReviewGraphBackend, GraphQueryClient
from .grep_backend import GrepBackend
from .query import (BASIS_LEXICAL, BUDGET_DEFAULT, BUDGET_FULL, BUDGETS, BackendError,
                    GraphBackend, GraphIndexUnbuilt, GraphNotIndexed, QueryResult)

# Tools that ARE real structural graphs (everything else is the lexical floor).
# `neo4j` left at 0.0.18 lane D stage 14 — see `_announce_removed_graph_chain` below.
from .graph_tools import GRAPH_TOOLS  # noqa: E402

STORY_ANALYSIS_PREFIX = "story_analysis__"

# 0.0.25 K — `KnowledgeLayer.history` is BOUNDED. It was a plain list of every full QueryResult the
# layer ever answered, and the IDE keeps one layer for its whole lifetime: +11 MB per hub query on
# the 1.2M-line scale repo, 1.1 GB observed. Its one reader (`build_story_analysis`) needs only
# kind / target / count / backend, so a history entry is now that summary (`QueryRecord`) and the
# record keeps the most recent `HISTORY_MAX` of them. What fell off the front is COUNTED
# (`history_dropped`) and carried onto the story analysis, never silently lost.
HISTORY_MAX = 512


@dataclass(frozen=True)
class QueryRecord:
    """What the layer REMEMBERS about one answer — never the references themselves."""

    kind: str
    target: str
    count: int
    backend: str
    basis: str = ""
    truncated: bool = False

    @classmethod
    def of(cls, result: QueryResult) -> "QueryRecord":
        return cls(kind=result.kind, target=result.target, count=result.count,
                   backend=result.backend, basis=getattr(result, "basis", ""),
                   truncated=bool(getattr(result, "truncated", False)))


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


def _announce_removed_graph_chain(router: Any, root: str) -> frozenset:
    """A committed manifest that still routes `code_graph` to a channel this release REMOVED.

    ★ THIS IS THE `code_graph` HALF OF `memory.selection._refuse_removed_memory_chain`, AND IT
    DELIBERATELY DOES NOT RAISE. The rule is not a preference, it is written down in
    `RemovedChannelError`'s own docstring: that class is HARD *"deliberately not a
    `DegradedCapability`: a degrade means there is a floor that gives a weaker but TRUE answer, and
    here there is none"*. For a removed memory BACKEND that held it: the candidate floor is an
    empty SQLite store, which does not answer "what do you remember" weakly — it answers it wrongly.
    For a removed code GRAPH there IS such a floor and it is the canonical one — the embedded AST
    floor gives real Python call/import edges, and `mokata query callers <sym>` keeps working. So
    the honest outcome is: SAY SO, then answer from the floor. Raising would refuse to run over
    derived data the survivor re-derives.

    ⚠ WHAT IS NOT ALLOWED IS SILENCE, AND SILENCE IS WHAT A BARE DELETION PRODUCES. Drop `neo4j`
    from `GRAPH_TOOLS` and delete the branch, and a user whose driver is installed still RESOLVES
    to `neo4j` — the detect strategy is `python_module`, which survives — and falls through to this
    function's last line, which builds `GrepBackend(name=res.tool)`. That is the emergency LEXICAL
    floor wearing the removed backend's name, on a Python repo that has an AST floor sitting right
    there: the user is told `code graph: floor 'neo4j'`, silently loses structural answers they had
    yesterday, and nothing anywhere says a backend was removed. Slice 1's empty-store read, one
    capability over, and the degrade-to-floor that makes it invisible is documented as CORRECT
    behaviour today — which is exactly what makes it dangerous the day the backend is gone.

    Returns the removed channels the chain names, so the caller can refuse to let one of them
    LABEL a backend. Degrade-clean on the read of the config itself: a duck-typed router with no
    `.manifest.fallback_order` announces nothing rather than raising, the same contract
    `_refuse_removed_memory_chain` has."""
    import os as _os
    from .. import MOKATA_DIR as _MD, deprecation as _dep
    try:
        chain = router.manifest.fallback_order("code_graph")
    except (ManifestError, AttributeError):
        return frozenset()
    # BY TYPE, NOT BY MEMBERSHIP: a `code_graph` chain can only name a removed GRAPH provider, and
    # announcing a removed memory backend's downgrade-and-migrate remedy here would be slice 2's
    # false refusal on a third surface.
    removed = _dep.removed_channels_in(chain, _dep.RemovedDerivedNotice)
    if not removed:
        return frozenset()
    # Guarded on a real repo dir so a bare `root="."` unit call fabricates no `.mokata` — the same
    # guard the deleted neo4j arm carried for `warn_deprecated`.
    md = _os.path.join(root, _MD)
    if _os.path.isdir(md):
        for channel in removed:
            _dep.warn_removed(channel, md)
    return frozenset(removed)


def _announce_deprecated_graph_chain(router: Any, root: str) -> tuple:
    """The DEPRECATION arm of the same surface, and until 0.0.20 stage 11 nothing anywhere called
    the announcer it uses (`WARN-DEPRECATED-HAS-NO-CALLERS`, doc 84).

    ⚠ A SEPARATE FUNCTION, NOT A SECOND LOOP INSIDE THE ONE ABOVE, and the reason is that one
    RAISES and this one MUST NOT. A removed provider labelling a floor is a refusal; a deprecated
    provider still works — the announcement is the whole difference between deprecating a thing
    and deleting it. Folding them would make the softer case inherit the harder one's control flow,
    which is how a deprecation notice turns into an outage."""
    import os as _os
    from .. import MOKATA_DIR as _MD, deprecation as _dep
    try:
        chain = router.manifest.fallback_order("code_graph")
    except (ManifestError, AttributeError):
        return ()
    deprecated = _dep.deprecated_channels_in(chain)
    md = _os.path.join(root, _MD)
    if deprecated and _os.path.isdir(md):
        for channel in deprecated:
            _dep.warn_deprecated(channel, md)
    return deprecated


def _graph_failure_summary(tool: str, failed: BaseException) -> str:
    """0.0.26 O15 — WHICH binary failed and WHY, for the code-graph DEGRADED line. Jas's notice
    said only "the backend is unreachable", naming neither the binary (it was a code-review-graph
    on PATH from another environment) nor the cause. Bounded and secret-checked: the reason can
    carry the tool's own text (`degrade.CapabilityDegradeNotice.render` — detail is not rendered
    for that reason, so a site that needs its cause on screen bounds it into `fallback`)."""
    from ..detect import find_command
    binary = find_command(tool) or f"{tool} (not resolved)"
    why = " ".join(str(failed).split())[:240] or type(failed).__name__
    try:
        from ..govern.secrets import scan
        if scan(text=why):
            why = f"{type(failed).__name__} (detail withheld: secret-shaped content)"
    except Exception:                         # noqa: BLE001 — fail CLOSED
        why = f"{type(failed).__name__} (detail withheld: could not be checked for secrets)"
    return f"{binary}: {why}"


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
    absent tool never reaches here (it resolves to grep). A tool that's present-but-broken at query
    time raises `BackendError`, which `_run` degrades to the grep floor — so the floor is the
    universal guarantee no matter where a tool fails.

    ⚠ There used to be a THIRD availability shape here: the external `neo4j` needed an extra
    BUILD-TIME probe, because a present driver module was not a reachable DB. That whole arm left
    at 0.0.18 lane D stage 14, and what replaced it is not nothing —
    `_announce_removed_graph_chain` runs first so a chain that still names it is answered rather
    than silently re-labelled as the lexical floor."""
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

    # 0.0.18 lane D stage 14 — announce a removed provider the committed chain still names, BEFORE
    # anything resolves, and never let one label a backend (see `_announce_removed_graph_chain`).
    removed_here = _announce_removed_graph_chain(router, root)
    # The softer arm, same chain, one line later — see `_announce_deprecated_graph_chain`.
    _announce_deprecated_graph_chain(router, root)
    if res is not None and res.tool in removed_here:
        # The floor, named for what it IS. `_floor_backend` is the AST floor on a Python repo and
        # grep otherwise — the canonical graph the removal record points the user at, not the
        # emergency lexical floor the fall-through would have produced under the dead name.
        return _floor_backend(router, root), None

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
        self.history: Deque[QueryRecord] = deque(maxlen=HISTORY_MAX)
        self.history_dropped = 0              # records that fell off the bounded front

    def _remember(self, result: QueryResult) -> None:
        if len(self.history) == self.history.maxlen:
            self.history_dropped += 1
        self.history.append(QueryRecord.of(result))

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
        # P41 MCP-FOLLOWS-WORKTREE — a question about a linked worktree (the surface is rooted in
        # one, or the MCP call serves a session bound to one) is answered by the MAIN checkout's
        # layer with the worktree's changed files overlaid (`tree_overlay.TreeLayer`). Never a
        # second graph built inside the worktree, never the main checkout's files alone.
        from ..tree_scope import code_tree_for
        hit = code_tree_for(surface.root) if cls is KnowledgeLayer else None
        if hit is not None:
            import os as _os
            from ..config import Surface
            from .tree_overlay import TreeLayer
            tree, main = hit
            main_surface = surface if _os.path.realpath(surface.root) == _os.path.realpath(main) \
                else Surface.load(main)
            return TreeLayer.over(cls._main_from_surface(main_surface, client, index), tree)
        return cls._main_from_surface(surface, client, index)

    @classmethod
    def _main_from_surface(cls, surface: Any, client: Optional[GraphQueryClient] = None,
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

    @property
    def pinned_graph_tool(self) -> Optional[str]:
        """The graph tool the COMMITTED manifest pins, or None (floor-only) — ADOPTION, not
        availability, and the two are different facts (§7g).

        🔴 REVIEW FINDING 3-1 (0.0.21, third independent review pass). `uses_graph` answers
        AVAILABILITY: it reads `primary.is_graph`, and `select_backends` says in its own docstring
        that *"`router.resolve` only returns a tool whose command is present, so an absent tool
        never reaches here (it resolves to grep)"*. So the MOST COMMON broken adoption — the
        `code-review-graph` command uninstalled, a wrong venv, a PATH that lost it — produced
        `uses_graph=False`, which the reason derivation read as *"no graph is wired"*, which told
        the user to **adopt a graph they had already adopted**.

        ⛔ THAT IS THE EXACT SENTENCE FIX D EXISTS TO DELETE, on the one GR.S3 refusal that fires in
        the shipped product. Reproduced end to end by the reviewer on a repo carrying what
        `adopt_graph` writes, with the binary absent: `('chain-lexical',)` → *"Adopt a real code
        graph"*. The adoption fact was a one-line durable read the whole time and nothing asked it.

        Degrade-clean: None on any manifest fault, which reads as *"not adopted"* — the direction
        that sends a reader to `mokata graph adopt`, harmless for a repo that turns out to have one,
        rather than to `mokata doctor` for a graph that may not exist."""
        try:
            from .graph_adopt import _load_manifest_data, graph_pinned_tool
            root = getattr(self.primary, "root", None)
            if not root:
                return None
            return graph_pinned_tool(_load_manifest_data(root))
        except Exception:  # noqa: BLE001 — a manifest fault is "not adopted", never a raise into a lens
            return None

    # --- typed queries ------------------------------------------------------
    def _run(self, kind: str, target: str, depth: int = 1, budget: str = BUDGET_DEFAULT,
             checkpoint: Optional[Callable[[], None]] = None) -> QueryResult:
        # 0.0.26 O7 — `budget=full` is the on-request long walk; only the blast radius has one.
        if budget not in BUDGETS:
            raise ValueError(f"unknown budget '{budget}'; one of {BUDGETS}")
        if budget == BUDGET_FULL and kind != "blast_radius":
            raise ValueError(f"budget '{BUDGET_FULL}' applies to blast_radius only, not '{kind}'")

        def ask(backend: GraphBackend) -> QueryResult:
            if budget == BUDGET_FULL:
                return backend.blast_radius_full(target, depth=depth, checkpoint=checkpoint)
            return backend.query(kind, target, depth=depth)

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
            floor = ask(self.fallback)
            # D2 — a demotion, not a flag flip: the floor may itself have certified a verified
            # ZERO, and a KNOWN-stale graph is not a basis on which to pass that certification on.
            # `demote_to_floor` moves the basis and the reason together so the two can't drift.
            floor.demote_to_floor()
            floor.note = _join_note(floor.note, fresh.note)
            self._surface_index_staleness(floor)
            self._remember(floor)
            return floor
        # CRG-NAV (b)+(d) — a MAPPING gap is not a FAILURE. When the adopted graph declares it has
        # no op for this kind (code-review-graph exposes no definition-site pattern), route the
        # kind to the floor DIRECTLY: no doomed graph call, no wasted `recover()` re-probe, and no
        # `note_degraded` alarm blaming a tool that is perfectly healthy. The answer says which
        # backend produced it and why, and the FLOOR's own verdict decides `degraded` — the AST
        # floor answers `defs` exactly on a Python repo, so it is not a degraded answer at all.
        if self.fallback is not None and not _supports_kind(self.primary, kind):
            floor = ask(self.fallback)
            floor.note = _join_note(
                floor.note,
                f"the adopted graph '{self.primary.name}' exposes no '{kind}' op; "
                f"answered by the '{floor.backend}' floor")
            if fresh is not None and fresh.note:
                floor.note = _join_note(floor.note, fresh.note)
            self._surface_index_staleness(floor)
            self._remember(floor)
            return floor
        try:
            result = ask(self.primary)
        except GraphNotIndexed as absent:
            # 0.0.26 O2 — the adopted graph is HEALTHY and holds no node for `target`. Not a
            # failure (no recover() re-probe, no degrade alarm against a working tool) and not a
            # zero (an empty impact would read as "nothing is affected"). The floor answers in its
            # own right — its verdict decides `degraded`, exactly as for a mapping gap above — and
            # the note names both backends so the reader knows which one spoke.
            if self.fallback is None:
                raise
            floor = ask(self.fallback)
            if isinstance(absent, GraphIndexUnbuilt):
                # 0.0.26 O13 — no index at all (yet): the backend already started the ONE
                # background build (or says why it did not); this query never waits for it.
                said = f"{absent} — answered by the '{floor.backend}' floor"
            else:
                said = (f"the adopted graph '{self.primary.name}' does not index `{target}` "
                        f"({absent}); answered by the '{floor.backend}' floor")
            floor.note = _join_note(floor.note, said)
            if fresh is not None and fresh.note:
                floor.note = _join_note(floor.note, fresh.note)
            self._surface_index_staleness(floor)
            self._remember(floor)
            return floor
        except BackendError as first:
            # GR.S2(k): a real graph gets ONE bounded, operational recovery attempt (re-probe
            # -> index refresh/rebuild -> retry) before we degrade — kept functional
            # automatically, no durable write. Unrecoverable -> LOUD classed degrade to the
            # floor (never a silent wrong answer) + a doctor-visible notice.
            failed = first
            result = None
            recover = getattr(self.primary, "recover", None)
            if callable(recover) and recover():
                try:
                    result = ask(self.primary)
                except BackendError as again:
                    failed = again
                    result = None
            if result is None:
                if self.fallback is None:
                    raise
                # A1 (0.0.19) — the notice says WHICH failure this was. It was hard-coded to
                # FAILURE_UNREACHABLE, so a graph that was ALIVE AND MUTE told the user their
                # backend was unreachable and sent them to re-adopt a tool that was running
                # perfectly well. The class now comes from the error, which carries it up from
                # whichever transport raised (doc 85 §7g).
                from ..degrade import FAILURE_TIMEOUT, FAILURE_UNREACHABLE, note_degraded
                from ..errors import failure_class_of
                failure_class = failure_class_of(failed) or FAILURE_UNREACHABLE
                if failure_class == FAILURE_TIMEOUT:
                    said = "did not answer in time"
                    fix = (f"'{self.primary.name}' is running but not answering — check its own "
                           f"logs/index, or raise the client timeout; then run `mokata doctor`")
                else:
                    said = "could not answer"
                    fix = ("run `mokata doctor`; re-adopt with `mokata graph adopt` if it "
                           "persists")
                note_degraded(
                    "code-graph", failure_class,
                    fallback=(f"the code graph fell back to '{self.fallback.name}' — "
                              f"{_graph_failure_summary(self.primary.name, failed)}"),
                    fix=fix,
                    detail=f"graph backend '{self.primary.name}' {said} "
                           f"{kind}({target}) and did not recover")
                result = ask(self.fallback)
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
        # stale.
        #
        # 🔴 REVIEW FINDING A8 (0.0.21 stage 14) — the sentence that used to end this comment,
        # "Only meaningful for a long-lived layer whose controller holds the cold baseline", is FALSE
        # after fix C: the recheck loads the PERSISTED baseline, so it is live in every process, for
        # a layer of any lifetime. §7h — a prose site repeating an overturned premise is a claim, and
        # it was still asserting the thing the fix removed.
        if self.freshness is not None:
            try:
                if self.freshness.recheck_after_answer(self, result):
                    result = ask(self.primary)
            except Exception:
                pass
        # GR.S4 — a freshness cap / tier note rides on the answer, honestly (never a block).
        if fresh is not None and fresh.note and not fresh.answer_from_floor:
            result.note = _join_note(result.note, fresh.note)
        # B4: surface staleness for any referenced file changed since indexing.
        self._surface_index_staleness(result)
        self._remember(result)
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
        self._remember(result)
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


def impact_degrade(layer: Any, result: Any) -> Optional[Dict[str, Any]]:
    """GRAPH-FIRST-IMPACT (d) (0.0.23 D2) — the ONE degraded wording + remedy for an IMPACT answer
    that came from the lexical floor, or None when the answer is structural (or not an impact kind).

    Every surface that hands a blast radius to a reader — `mokata query`, the MCP `query` tool and
    develop's LIVE-EVIDENCE lines — reads THIS, so the three cannot disagree. The remedy is chosen by
    `govern.graph_required.remedy_for` over the fix-D reason vocabulary, never re-derived here:

      * floor found nothing          -> check-target (a graph would find nothing either)
      * a graph is live and degraded -> repair       (`graph-failed`)
      * a graph is pinned, not live  -> repair       (`graph-unavailable`)
      * no graph in the chain        -> adopt        (`chain-lexical`)

    Read-only and total: a layer that cannot say whether it is a graph is read as "not adopted" —
    the harmless-wrong direction (`brainstorm_impact._reads_as_a_graph`)."""
    from .query import IMPACT_KINDS, PREFERRED_GRAPH_TOOL
    if result is None or getattr(result, "kind", "") not in IMPACT_KINDS:
        return None
    if not getattr(result, "degraded", False):
        return None
    from ..brainstorm_impact import (DEGRADE_CHAIN_IS_LEXICAL, DEGRADE_FLOOR_FOUND_NOTHING,
                                     DEGRADE_GRAPH_FAILED, DEGRADE_GRAPH_UNAVAILABLE,
                                     _pinned_graph_tool, _reads_as_a_graph)
    from ..govern.graph_required import REMEDY_ADOPT, REMEDY_CHECK_TARGET, remedy_for
    reasons: List[str] = []
    if not getattr(result, "references", None):
        reasons.append(DEGRADE_FLOOR_FOUND_NOTHING)
    if _reads_as_a_graph(layer):
        reasons.append(DEGRADE_GRAPH_FAILED)
    elif _pinned_graph_tool(layer):
        reasons.append(DEGRADE_GRAPH_UNAVAILABLE)
    else:
        reasons.append(DEGRADE_CHAIN_IS_LEXICAL)
    remedy = remedy_for(reasons)
    target = getattr(result, "target", "") or ""
    if remedy == REMEDY_CHECK_TARGET:
        message = (f"degraded — the lexical floor found NOTHING for `{target}`, which is no evidence "
                   f"either way and a graph would find nothing either; check the symbol "
                   f"(`mokata query defs {target}`)")
    elif remedy == REMEDY_ADOPT:
        message = (f"degraded — this blast radius is the lexical grep floor's keyword estimate, not "
                   f"a structural answer; install {PREFERRED_GRAPH_TOOL} for full impact "
                   f"(`mokata graph adopt`)")
    else:
        message = ("degraded — a code graph is adopted here and did not answer this blast radius "
                   "(the lexical floor stood in); adopting again will not fix it — run "
                   "`mokata doctor`")
    return {"reasons": reasons, "remedy": remedy, "message": message}


def run_query(layer: "KnowledgeLayer", kind: str, target: str, depth: int = 2,
              budget: str = BUDGET_DEFAULT,
              checkpoint: Optional[Callable[[], None]] = None) -> QueryResult:
    """The ONE typed-query router the MCP `query` tool and the CLI `query` command share
    (GR.S2). Routes `semantic` to the capability-gated semantic API, every structural kind to
    `_run` — so the adopted graph's semantic search reaches in-harness through the typed API
    without a second surface, and a floor-only repo degrades honestly instead of crashing."""
    if kind == "semantic":
        if budget != BUDGET_DEFAULT:
            raise ValueError(f"budget '{budget}' applies to blast_radius only, not 'semantic'")
        return layer.semantic(target)
    return layer._run(kind, target, depth=depth, budget=budget, checkpoint=checkpoint)


# ----------------------------------------------------- Stage 25 Part B: graph guidance
def impact_backend_line(layer: Any) -> str:
    """0.0.26 O2 — WHICH BACKEND ANSWERS A BLAST RADIUS here, in one sentence, for doctor and the
    IDE health page. Derived from the same `_supports_kind` routing `_run` uses, so the disclosure
    cannot say one backend while the query path uses another."""
    primary = getattr(layer, "primary", None)
    fallback = getattr(layer, "fallback", None)
    pname = getattr(primary, "name", "") or "none"
    if primary is not None and getattr(primary, "is_graph", False) \
            and _supports_kind(primary, "blast_radius"):
        tail = (f"; a symbol it does not index, or a failure, is answered by the "
                f"'{fallback.name}' floor" if fallback is not None else "")
        return f"blast radius: answered by '{pname}' (transitive callers){tail}"
    if fallback is not None:
        return (f"blast radius: answered by the '{fallback.name}' floor "
                f"('{pname}' has no blast-radius op)")
    return f"blast radius: answered by '{pname}'"


def graph_index_line(layer: Any) -> Optional[str]:
    """0.0.26 O13 (O11 F2) — "active" is said only when the graph is ACTUALLY ANSWERING: its index
    is built. A graph that is installed/pinned but whose index is building, never built, or failed
    to build answers nothing — the floor does — and each of those is its own sentence. None when
    the graph is built (or the backend has no on-disk index to report on): the caller's usual
    "active" line applies. Read-only: never starts a build."""
    primary = getattr(layer, "primary", None)
    if not getattr(primary, "is_graph", False):
        return None
    probe = getattr(primary, "index_status", None)
    if not callable(probe):
        return None
    try:
        status = probe()
    except Exception:  # noqa: BLE001 — a status line that cannot be read says nothing extra
        return None
    if status is None:
        return None
    from .crg_build import BUILDING, FAILED, UNBUILT
    tool = primary.name
    floor = getattr(getattr(layer, "fallback", None), "name", "") or "the floor"
    if status.state == BUILDING:
        return (f"code graph: {tool} building — {status.line(tool)}; until it finishes every "
                f"structural answer comes from the '{floor}' floor (and says so).")
    if status.state == UNBUILT:
        return (f"code graph: {tool} detected, unbuilt — it has no index for this repo yet, so the "
                f"'{floor}' floor answers. The first structural query starts the build in the "
                f"background; `mokata graph build` builds it now, with progress.")
    if status.state == FAILED:
        return (f"code graph: {tool} index build FAILED — {status.line(tool)}. The '{floor}' "
                f"floor answers meanwhile.")
    return None


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
    state_line = graph_index_line(layer)
    if state_line:
        return state_line
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
                f"`callees <sym>` / `blast_radius <sym>` for structural queries. "
                f"{impact_backend_line(layer)}.")
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
def graph_anchor_verdict(layer: Any, tok: str) -> Optional[bool]:
    """Does the adopted code graph confirm `tok` as a real symbol with callers? True / False, or None
    when the graph could not say. ONE definition, shared by the live scorer below and the
    off-prompt-path warmer (`memory.graph_anchors`, 0.0.26 O12), so a cached verdict and a live one
    cannot disagree.

    It asks the graph ITSELF (`layer.primary`), not `_run`: what is wanted is the graph's own
    confirmation, and `_run`'s floor answer is not that. 0.0.26 O12 also fixes how a token the graph
    simply does not hold was read: `_run` answers it from the floor with `degraded=True`, which this
    tier counted as a graph FAILURE — so every ordinary prompt word ("fix", "the") raised a
    "graph lookup failed" notice against a healthy graph. A healthy graph saying "no such node"
    (`GraphNotIndexed`, O2) is a confirmed False. An index that is not built yet is unknown (None)."""
    try:
        res = layer.primary.query("callers", tok)
    except GraphIndexUnbuilt:
        return None
    except GraphNotIndexed:
        return False
    except (BackendError, ValueError):
        return None
    return bool(res is not None and not res.degraded and res.references)


def make_graph_scorer(layer: Any, query: str):
    """A live graph-keyed relevance scorer for memory's tiered retrieval (closes the 35e
    SHOULD). Returns `(query, item) -> 0..1` that boosts a memory item which references a
    symbol the CODE GRAPH confirms is real and related to the query — or None when no real
    graph is wired (so the graph tier stays silent and lexical+semantic hold).

    Frugal (P11): runs one cheap graph lookup per identifier token in the query (a small set),
    not per memory item; the resulting anchor set scores items by token membership. It is a LIVE
    graph call (a server start, a freshness `update`), so it is never on the per-prompt hook: that
    path reads the verdicts `memory.graph_anchors` precomputed off it (0.0.26 O12).

    D5 — a graph that FAILS here used to be indistinguishable from a graph that simply found no
    anchor: both produced `None`, both left recall running on the lexical floor, and neither said a
    word. That is the whole harm — the user who wired a code graph believes graph-proximity recall
    is live when it is not. It still degrades to the floor (the fallback keeps falling back); it
    just says so, once, loudly. (The sentence named `neo4j` until 0.0.18 stage 14 removed it; the
    PROPERTY outlived its witness — every surviving graph provider is wired by the same explicit
    act and produces the same false belief when it silently stops answering.)"""
    if layer is None or not getattr(layer, "uses_graph", False):
        return None
    from ..memory.graph_anchors import query_tokens
    # GR.S4 — FRESHNESS-BEFORE-ANSWER, once for the whole token set (it ran per token via `_run`).
    # A graph known stale whose rebuild failed answers nothing here: the tier is off for this
    # recall, exactly as `_run` would have answered from the floor.
    if getattr(layer, "freshness", None) is not None:
        try:
            fresh = layer.freshness.ensure_fresh(layer)
        except Exception:  # noqa: BLE001 — freshness never breaks a recall (`_run`'s rule)
            fresh = None
        if fresh is not None and fresh.answer_from_floor:
            return None
    anchors = set()
    failed = 0
    for tok in query_tokens(query):
        verdict = graph_anchor_verdict(layer, tok)
        if verdict is None:
            failed += 1
        elif verdict:
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
    # 0.0.25 K — queries the layer's BOUNDED history no longer held (see `HISTORY_MAX`): the
    # analysis says how many earlier queries it cannot list rather than reading as complete.
    queries_dropped: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": 1,
            "story_id": self.story_id,
            "summary": self.summary,
            "symbols": list(self.symbols),
            "queries": list(self.queries),
            "backend": self.backend,
            "queries_dropped": self.queries_dropped,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "StoryAnalysis":
        return cls(
            story_id=d["story_id"],
            summary=d.get("summary", ""),
            symbols=list(d.get("symbols", [])),
            queries=list(d.get("queries", [])),
            backend=d.get("backend", ""),
            queries_dropped=int(d.get("queries_dropped", 0) or 0),
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
                         queries=queries, backend=layer.backend_name,
                         queries_dropped=int(getattr(layer, "history_dropped", 0) or 0))


def persist_story_analysis(store: Any, analysis: StoryAnalysis) -> str:
    """Persist a story's analysis via the state surface (explicit, never silent)."""
    return store.write(STORY_ANALYSIS_PREFIX + analysis.story_id, analysis.to_dict())


def load_story_analysis(store: Any, story_id: str) -> Optional[StoryAnalysis]:
    data = store.read(STORY_ANALYSIS_PREFIX + story_id)
    return StoryAnalysis.from_dict(data) if data else None
