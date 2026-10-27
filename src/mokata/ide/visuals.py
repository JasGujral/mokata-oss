"""The graph views of `mokata ide` (0.0.23 C3, ID-VIS-CORE, ruling R3) — as DATA. The page draws
them with the vendored Cytoscape.js; every node and edge here comes from a `mokata query` answer.

    ripple(root, symbol, depth)   the blast-radius ripple: the symbol at the centre, ring k = the
                                  callers k hops out (CALLS), plus the files that import it
                                  (IMPORTS) on ring 1; covering tests on the outer ring; nodes on a
                                  caller chain that NO test reaches are flagged `untested`
    focus(root, symbol)           one hop each way: callers (left), callees (right)
    path(root, src, dst, depth)   a call path src → … → dst over CALLEES, shortest first
    untested(root, symbol, depth) the caller chains into `symbol` that no test is on

THE RULE: never draw an edge the graph cannot justify. Every edge carries the `basis` of the
query that produced it (solid = structural, dashed = lexical), the kind (`CALLS` / `IMPORTS`) and
the site that justifies it (`path:line`). A lexical answer can never mark a node `untested` — an
absence of evidence is not evidence of absence — so "untested" is claimed only where every edge of
the chain is structural.

Paged, never capped (0.0.26 P37; U10 overruled): a picture is drawn PAGE_NODES nodes at a time;
a full page says `more` and "Continue" (page + 1) resumes the same walk where it stopped. The page
itself draws at most `draw.limit` (150) of them — the rest as clusters.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

from ..knowledge.cancel import report
from . import nav

PAGE_NODES = 2000               # nodes per page of a picture (the page draws <= DRAW_LIMIT)
MAX_DEPTH = 6
MAX_CHAINS = 50                 # untested chains listed; the answer says how many there were
CHAIN_COUNT_CAP = 1000
PARTIAL_EVERY_S = 2.0          # P37: a long ring re-sends its picture this often while confirming          # chains counted past the listed ones before saying "at least"
STRUCTURAL = ("structural", "verified_empty")


class _G:
    """A graph under construction: nodes keyed by id, edges deduplicated by (src, dst, kind).

    0.0.26 P37: nodes and edges drawn from the code graph's INDEX ahead of the tool's answer are
    `pending` (the page draws them faded) until an answer confirms them; `drop_unconfirmed` takes
    away what no answer confirmed once every ring is in."""

    def __init__(self) -> None:
        self.nodes: Dict[str, Dict[str, Any]] = {}
        self.edges: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        self.truncated = False             # this PAGE is full (the walk resumes on the next)
        self.limit = PAGE_NODES

    @property
    def bases(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for e in self.edges.values():
            out[e["basis"]] = out.get(e["basis"], 0) + 1
        return out

    def node(self, nid: str, **attrs: Any) -> Optional[Dict[str, Any]]:
        if nid in self.nodes:
            n = self.nodes[nid]
            for k, v in attrs.items():
                if k == "ring" and v is not None and (n.get("ring") is None or v < n["ring"]):
                    n["ring"] = v
            return n
        if len(self.nodes) >= self.limit:
            self.truncated = True
            return None
        n = {"id": nid, **attrs}
        self.nodes[nid] = n
        return n

    def edge(self, src: str, dst: str, kind: str, basis: str, path: str, line: int,
             pending: bool = False) -> Optional[Tuple[str, str, str]]:
        if src not in self.nodes or dst not in self.nodes or src == dst:
            return None
        key = (src, dst, kind)
        if key in self.edges:
            if not pending:
                self.edges[key].pop("pending", None)
            return key
        e = {"id": f"e{len(self.edges)}", "source": src, "target": dst,
             "kind": kind, "basis": basis, "site": f"{path}:{line}"}
        if pending:
            e["pending"] = True
        self.edges[key] = e
        return key

    def confirm(self, nid: Optional[str]) -> None:
        if nid is not None and nid in self.nodes:
            self.nodes[nid].pop("pending", None)

    def drop_unconfirmed(self) -> int:
        gone = {nid for nid, n in self.nodes.items() if n.get("pending")}
        for nid in gone:
            del self.nodes[nid]
        for key in [k for k, e in self.edges.items()
                    if e.get("pending") or k[0] in gone or k[1] in gone]:
            del self.edges[key]
        return len(gone)

    def payload(self, **extra: Any) -> Dict[str, Any]:
        nodes = [dict(n) for n in self.nodes.values()]
        edges = [dict(e) for e in self.edges.values()]
        bases = self.bases
        out = {"sample": False, "nodes": nodes, "edges": edges,
               "bases": bases, **extra}
        out["basis"] = basis_word(bases, extra.get("answer_bases") or [],
                                  bool(extra.get("partial")))
        out["draw"] = draw_plan(nodes, edges)
        h = nav.graph_health(extra.get("root") or "") if extra.get("root") else None
        out.pop("root", None)
        if h:
            out["graph_note"] = nav.degraded_line(h)
        return out


# --- 0.0.26 P37 (R25): ONE basis word for the whole picture -------------------------------------
BASIS_WORDS = ("structural", "approximate", "floor", "pending")


def basis_word(bases: Dict[str, int], answers: List[str], partial: bool = False) -> str:
    """THE basis of a picture, in one word, derived from what was drawn — the status line, the
    legend and the summary all say this word and nothing else (PRD §5.4 rule 5).

    structural   every edge drawn is a graph edge (and every question was answered by the graph
                 when nothing was drawn)
    floor        every edge is a name match (the lexical floor answered)
    approximate  some of each
    pending      nothing answered yet (a job's first partial)"""
    struct = sum(n for b, n in bases.items() if b in STRUCTURAL)
    lex = sum(n for b, n in bases.items() if b not in STRUCTURAL)
    if struct and lex:
        return "approximate"
    if lex:
        return "floor"
    if struct:
        return "structural"
    if not answers:
        return "pending" if partial else "structural"
    return "structural" if all(a in STRUCTURAL for a in answers) else "floor"


# --- 0.0.26 P37 (R24, G19): never draw more than DRAW_LIMIT nodes ---------------------------------
DRAW_LIMIT = 150        # nodes the page draws at most, cluster nodes included
DRAW_TOP = 100          # real nodes shown before the rest are grouped
DRAW_CLUSTERS = 40      # groups at most (DRAW_TOP + DRAW_CLUSTERS < DRAW_LIMIT)
NO_FILE = "(no file)"


def _rank_key(n: Dict[str, Any], degree: Dict[str, int]) -> Tuple[Any, ...]:
    """Relevance: the centre, then nearer rings, untested before covered, more connected first."""
    if n.get("kind") == "center":
        return (0, 0, 0, 0, "")
    return (1, n.get("ring") if n.get("ring") is not None else 99,
            0 if n.get("coverage") == "untested" else 1, -degree.get(n["id"], 0),
            str(n.get("label") or n["id"]))


def _dir_of(n: Dict[str, Any]) -> str:
    p = str(n.get("path") or "")
    return p.rsplit("/", 1)[0] if "/" in p else (p and ".") or NO_FILE


def _group(dirs: List[str]) -> Dict[str, str]:
    """dir -> its group. Starts from the top-level directories and keeps splitting the BIGGEST
    group into its sub-directories while the total stays within DRAW_CLUSTERS — so a crowded
    `tests/` becomes `tests/queries`, `tests/admin_views`, … while a small `django/` stays one.
    Past DRAW_CLUSTERS top-level groups: the biggest ones and one `other`."""
    def head(d: str, level: int) -> str:
        return d if d in (NO_FILE, ".") else "/".join(d.split("/")[:level])

    level_of: Dict[str, int] = {}
    groups: Dict[str, List[str]] = {}
    for d in dirs:
        groups.setdefault(head(d, 1), []).append(d)
    for g in groups:
        level_of[g] = 1
    if len(groups) > DRAW_CLUSTERS:
        keep = set(sorted(groups, key=lambda k: (-len(groups[k]), k))[:DRAW_CLUSTERS - 1])
        return {d: g if g in keep else "other" for g, ds in groups.items() for d in ds}
    stuck: set = set()
    while True:
        cands = [g for g in groups if g not in stuck]
        if not cands:
            break
        big = max(cands, key=lambda k: (len(groups[k]), k))
        lvl = level_of[big] + 1
        parts: Dict[str, List[str]] = {}
        for d in groups[big]:
            parts.setdefault(head(d, lvl) if d.count("/") + 1 >= lvl else d, []).append(d)
        if len(parts) <= 1 and all(d.count("/") + 1 < lvl for d in groups[big]):
            stuck.add(big)                       # nothing deeper to split into
            continue
        if len(groups) - 1 + len(parts) > DRAW_CLUSTERS:
            stuck.add(big)
            continue
        del groups[big]
        for k, ds in parts.items():
            if k in groups:                      # a member dir that IS the old prefix
                groups[k].extend(ds)
            else:
                groups[k] = ds
                level_of[k] = lvl
    return {d: g for g, ds in groups.items() for d in ds}


def draw_plan(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]) -> Dict[str, Any]:
    """What the page draws: every node when they fit in DRAW_LIMIT; otherwise the DRAW_TOP most
    relevant and one CLUSTER node per directory group for the rest, with its count — a cluster
    opens on click (the page reveals its `members`, ordered by relevance, within the limit)."""
    degree: Dict[str, int] = {}
    for e in edges:
        degree[e["source"]] = degree.get(e["source"], 0) + 1
        degree[e["target"]] = degree.get(e["target"], 0) + 1
    ranked = sorted(nodes, key=lambda n: _rank_key(n, degree))
    if len(ranked) <= DRAW_LIMIT:
        return {"limit": DRAW_LIMIT, "total": len(ranked), "shown": [n["id"] for n in ranked],
                "clusters": []}
    shown, rest = ranked[:DRAW_TOP], ranked[DRAW_TOP:]
    groups = _group([_dir_of(n) for n in rest])
    clusters: Dict[str, Dict[str, Any]] = {}
    for n in rest:
        key = groups[_dir_of(n)]
        c = clusters.get(key)
        if c is None:
            c = clusters[key] = {"id": "cluster:" + key, "dir": key, "members": [], "count": 0,
                                 "untested": 0, "tests": 0, "ring": n.get("ring")}
        c["members"].append(n["id"])
        c["count"] += 1
        c["untested"] += n.get("coverage") == "untested"
        c["tests"] += n.get("kind") == "test"
        if n.get("ring") is not None and (c["ring"] is None or n["ring"] < c["ring"]):
            c["ring"] = n["ring"]
    out = sorted(clusters.values(), key=lambda c: (-c["count"], c["dir"]))
    for c in out:
        c["label"] = f"{c['dir']}/ · {c['count']}" if c["dir"] not in (NO_FILE, "other", ".") \
            else f"{c['dir']} · {c['count']}"
    return {"limit": DRAW_LIMIT, "total": len(ranked), "shown": [n["id"] for n in shown],
            "clusters": out}


def _sym_id(name: str) -> str:
    return "sym:" + name


def _ref_node(g: _G, r: Any, ring: Optional[int]) -> Optional[str]:
    """A caller reference → its node. With an enclosing symbol it is that symbol; a lexical hit
    with none is the SITE itself (`path:line`) — named for what it is, never guessed into a
    function."""
    is_test = nav._is_test(r.path)
    if r.symbol:
        nid = _sym_id(r.symbol)
        n = g.node(nid, label=r.symbol, symbol=r.symbol, path=r.path, line=r.line, ring=ring,
                   kind="test" if is_test else "caller")
    else:
        nid = f"site:{r.path}:{r.line}"
        n = g.node(nid, label=f"{r.path}:{r.line}", symbol=None, path=r.path, line=r.line,
                   ring=ring, kind="test" if is_test else "site")
    if n is not None and is_test:
        n["kind"] = "test"
    return nid if n is not None else None


def _center_node(g: _G, symbol: str) -> str:
    nid = _sym_id(symbol)
    g.node(nid, label=symbol, symbol=symbol, path=None, line=None, ring=0, kind="center")
    return nid


def _fill_center(g: _G, layer: Any, root: str, symbol: str) -> Dict[str, Any]:
    """The centre's definition site(s). Asked LAST by the ripple (P32): on a repo whose graph has
    no `defs` operation it is the floor's slowest question, and the rings must not wait on it."""
    defs = nav.run(root, "defs", symbol, layer=layer)
    first = defs.references[0] if defs.references else None
    n = g.nodes[_sym_id(symbol)]
    n.update(path=first.path if first else None, line=first.line if first else None,
             defs=len(defs.references), defs_basis=defs.basis)
    return {"defs": defs.count, "defs_basis": defs.basis}


def _layer(root: str, layer: Any) -> Any:
    return layer if layer is not None else nav._layer(root)


class _Ref:
    """A caller row read from the code graph's index (the shape `_ref_node` reads)."""
    __slots__ = ("path", "line", "symbol")

    def __init__(self, path: str, line: int, symbol: str) -> None:
        self.path, self.line, self.symbol = path, line, symbol


def _index_for(root: str, layer: Any) -> Any:
    """The code graph's index, read directly (`crg_nodes`), when code-review-graph is the graph
    answering this repo and its index is built — else None (no early rings: the answer alone)."""
    if getattr(layer, "backend_name", "") != "code-review-graph":
        return None
    from ..knowledge import crg_nodes
    return crg_nodes.reader(root)


def _early_rings(g: _G, index: Any, symbol: str, center: str, depth: int,
                 progress: Any) -> int:
    """0.0.26 P37 (R4) — every ring's PROVEN edges, read from the index in milliseconds, drawn
    `pending` before the tool's (slow) answers confirm them ring by ring. Never truncates the
    picture: it stops where this page of the tool's answer would (`g.limit`) and leaves the rest
    to it. Returns the rings read. An unreadable index reads nothing — the answers still come."""
    import sqlite3
    frontier = {symbol: center}
    seen = {symbol}
    rings = 0
    for hop in range(1, depth + 1):
        try:
            rows = index.callers_by_name(list(frontier))
        except (sqlite3.Error, OSError, ValueError):
            break
        nxt: Dict[str, str] = {}
        for callee, caller, path, line in rows:
            dst = frontier.get(callee)
            if dst is None:
                continue
            if len(g.nodes) >= g.limit and _sym_id(caller) not in g.nodes:
                break
            fresh = _sym_id(caller) not in g.nodes
            cid = _ref_node(g, _Ref(path, line, caller), hop)
            if cid is None:
                break
            if fresh:
                g.nodes[cid]["pending"] = True
            g.edge(cid, dst, "CALLS", "structural", path, line, pending=True)
            if caller not in seen and g.nodes[cid]["kind"] != "test":
                seen.add(caller)
                nxt[caller] = cid
        g.truncated = False                  # the index's rings never cut the answer's picture
        rings = hop
        progress(hop)
        frontier = nxt
        if not frontier or len(g.nodes) >= g.limit:
            break
    return rings


class _Ripple:
    """One ripple, kept between PAGES (0.0.26 P37, U10 overruled: no hard cap). A page draws up to
    PAGE_NODES more nodes; when it is full the walk stops WHERE IT IS (ring, caller, row) and the
    answer says `more`; "Continue" (page + 1) resumes it here instead of asking again."""

    def __init__(self, root: str, symbol: str, depth: int, layer: Any, index: Any) -> None:
        self.root, self.symbol, self.depth, self.layer, self.index = root, symbol, depth, layer, \
            index
        self.g = _G()
        self.center = _center_node(self.g, symbol)
        self.page = 0
        self.callers_of: Dict[str, List[str]] = {}
        self.seen = {symbol}
        self.answers: List[str] = []
        self.direct: Optional[int] = None
        self.hop = 1
        self.frontier: List[Tuple[str, str]] = [(symbol, self.center)]
        self.nxt: List[Tuple[str, str]] = []
        self.fi = 0                         # the caller of `frontier` being asked
        self.rows: Optional[Tuple[Any, int]] = None   # its answer, and the next row to draw
        self.complete = False
        self.early = 0
        self.imports_done = False
        self.imports: Any = None
        self.meta: Dict[str, Any] = {}
        self.lock = threading.Lock()

    def _payload(self, **extra: Any) -> Dict[str, Any]:
        return self.g.payload(view="ripple", center=self.center, symbol=self.symbol,
                              depth=self.depth, root=self.root, page=self.page,
                              page_nodes=PAGE_NODES, **extra)

    def run_page(self) -> Dict[str, Any]:
        g = self.g
        self.page += 1
        g.limit = PAGE_NODES * self.page
        g.truncated = False
        if self.page == 1:
            if self.index is None:
                # With the index, the FIRST partial is ring 1 itself (a job announces its first
                # partial at once — an empty one first would cost the page a round trip).
                report(done=0, total=self.depth, unit="rings",
                       note=f"asking who calls {self.symbol}",
                       partial=self._payload(partial=True, rings_done=0))
            else:
                self.early = _early_rings(g, self.index, self.symbol, self.center, self.depth,
                                          lambda hop: report(
                    note=f"read {hop} ring(s) from the code graph's index; confirming ring 1 of "
                         f"{self.depth}",
                    partial=self._payload(partial=True, rings_done=0, index_rings=hop)))
        last_partial = time.monotonic()
        while self.hop <= self.depth and not g.truncated:
            hop = self.hop
            while self.fi < len(self.frontier):
                sym, nid = self.frontier[self.fi]
                if self.rows is None:
                    now = time.monotonic()
                    shown = None
                    if self.early and now - last_partial >= PARTIAL_EVERY_S:
                        last_partial = now       # what is confirmed so far stops being faded
                        shown = self._payload(partial=True, rings_done=hop - 1,
                                              index_rings=self.early,
                                              answer_bases=sorted(set(self.answers)))
                    report(done=self.fi, total=len(self.frontier), unit=f"ring-{hop} names asked",
                           note=f"{hop - 1} of {self.depth} ring(s) done; asking who calls {sym}",
                           **({"partial": shown} if shown else {}))
                    res = nav.run(self.root, "callers", sym, layer=self.layer)
                    self.answers.append(res.basis)
                    if self.direct is None:
                        self.direct = len(res.references)
                    self.rows = (res, 0)
                res, at = self.rows
                refs = res.references
                while at < len(refs):
                    r = refs[at]
                    cid = _ref_node(g, r, hop)
                    if cid is None:
                        if g.truncated:
                            break            # this page is full: resume at this row
                        at += 1
                        continue
                    at += 1
                    g.confirm(cid)
                    g.edge(cid, nid, "CALLS", res.basis, r.path, r.line)
                    self.callers_of.setdefault(nid, []).append(cid)
                    if r.symbol and r.symbol not in self.seen and g.nodes[cid]["kind"] != "test":
                        self.seen.add(r.symbol)
                        self.nxt.append((r.symbol, cid))
                    elif r.symbol and r.symbol not in self.seen:
                        self.seen.add(r.symbol)
                if g.truncated:
                    self.rows = (res, at)
                    break
                self.rows = None
                self.fi += 1
            if g.truncated:
                break
            # 0.0.26 P32 (R4) — each finished ring is drawn while the next one is asked.
            report(done=hop, total=self.depth, unit="rings",
                   note=f"ring {hop} of {self.depth}: {len(g.nodes) - 1} caller(s) so far",
                   partial=self._payload(partial=True, rings_done=hop, index_rings=self.early,
                                         answer_bases=sorted(set(self.answers)), **self.meta))
            self.frontier, self.nxt, self.fi = self.nxt, [], 0
            self.hop += 1
            if not self.frontier:
                self.hop = self.depth + 1      # every ring is in
                break
        self.complete = not g.truncated
        return self._finish()

    def _finish(self) -> Dict[str, Any]:
        g = self.g
        dropped = g.drop_unconfirmed() if self.complete else 0
        if not self.imports_done:
            self.imports_done = True
            self.imports = nav.run(self.root, "imports", self.symbol, layer=self.layer)
            self.answers.append(self.imports.basis)
            report(note=f"rings done; finding where {self.symbol} is defined")
            self.meta = _fill_center(g, self.layer, self.root, self.symbol)
        imp = self.imports                     # importers on ring 1 — as many as this page holds
        full = g.truncated
        for r in imp.references:
            mid = f"file:{r.path}"
            if mid in g.nodes:
                continue
            n = g.node(mid, label=r.path, symbol=None, path=r.path, line=r.line, ring=1,
                       kind="test" if nav._is_test(r.path) else "module")
            if n is None:
                self.complete = False          # the next page draws the rest of them
                break
            g.edge(mid, self.center, "IMPORTS", imp.basis, r.path, r.line)
            self.callers_of.setdefault(self.center, []).append(mid)
        g.truncated = full or not self.complete
        callers_of = {k: list(v) for k, v in self.callers_of.items()}
        for (src, dst, kind), e in g.edges.items():   # index edges no answer reached yet
            if e.get("pending") and kind == "CALLS":
                callers_of.setdefault(dst, []).append(src)
        _mark_coverage(g, callers_of)
        for n in g.nodes.values():               # tests sit on the OUTER ring, whatever their hop
            if n["kind"] == "test":
                n["ring"] = self.depth + 1
        chains, total, exact = _untested_chains(g, self.center, callers_of)
        extra: Dict[str, Any] = {"more": not self.complete}
        if not self.complete:
            extra["more_note"] = (f"showing {len(g.nodes):,} affected so far (ring {self.hop} of "
                                  f"{self.depth}) — Continue draws the next {PAGE_NODES:,}")
        if self.early:
            extra["index_rings"] = self.early
            extra["index_dropped"] = dropped
        return self._payload(answer_bases=sorted(set(self.answers)), untested_chains=chains,
                             untested_chains_total=total, untested_chains_exact=exact,
                             direct_callers=self.direct, **extra, **self.meta)


#: Ripples kept for "Continue" (by repo, symbol, depth): the few most recent.
_RIPPLES: "Dict[Tuple[str, str, int], _Ripple]" = {}
_RIPPLES_KEEP = 4
_RIPPLES_LOCK = threading.Lock()


def ripple(root: str, symbol: str, depth: int = 2, layer: Any = None,
           index: Any = None, page: int = 1) -> Dict[str, Any]:
    """The blast-radius ripple, PAGE by page (page 1 = the first PAGE_NODES nodes). A later page
    resumes the kept ripple where the last page stopped; with none kept (or a repo that moved
    since), it is walked afresh up to that page."""
    layer = _layer(root, layer)
    depth = max(1, min(MAX_DEPTH, int(depth)))
    page = max(1, int(page))
    key = (os.path.abspath(root), symbol, depth)
    with _RIPPLES_LOCK:
        rp = _RIPPLES.get(key) if page > 1 else None
        if rp is not None and (rp.page != page - 1 or rp.complete or rp.layer is not layer):
            rp = None
    if rp is None:
        rp = _Ripple(root, symbol, depth, layer,
                     index if index is not None else _index_for(root, layer))
        while rp.page < page - 1 and not rp.complete:
            rp.run_page()
    with rp.lock:
        out = rp.run_page() if not rp.complete or rp.page < page else rp._finish()
    with _RIPPLES_LOCK:
        if rp.complete:
            _RIPPLES.pop(key, None)
        else:
            _RIPPLES[key] = rp
            while len(_RIPPLES) > _RIPPLES_KEEP:
                _RIPPLES.pop(next(iter(_RIPPLES)))
    return out


def _mark_coverage(g: _G, callers_of: Dict[str, List[str]]) -> None:
    """`covered` = some test reaches the node through edges drawn here. `untested` = not covered
    AND every edge from it toward the centre and every edge into it is structural — a chain with a
    lexical link is `unknown`, never `untested`."""
    memo: Dict[str, bool] = {}

    def covered(nid: str, stack: frozenset) -> bool:
        if nid in memo:
            return memo[nid]
        if g.nodes[nid]["kind"] == "test":
            memo[nid] = True
            return True
        hit = False
        for c in callers_of.get(nid, []):
            if c not in stack and covered(c, stack | {nid}):
                hit = True
                break
        memo[nid] = hit
        return hit

    lexical_in = {e["target"] for e in g.edges.values() if e["basis"] not in STRUCTURAL}
    lexical_out = {e["source"] for e in g.edges.values() if e["basis"] not in STRUCTURAL}
    for nid, n in g.nodes.items():
        if n["kind"] == "test":
            n["coverage"] = "test"
        elif n["kind"] == "center" and not n.get("defs"):
            n["coverage"] = "unknown"          # the graph holds no definition: claim nothing
        elif covered(nid, frozenset()):
            n["coverage"] = "covered"
        elif nid in lexical_in or nid in lexical_out or n["kind"] == "site":
            n["coverage"] = "unknown"
        else:
            n["coverage"] = "untested"


def _untested_chains(g: _G, center: str, callers_of: Dict[str, List[str]],
                     limit: Optional[int] = None) -> Tuple[List[List[str]], int, bool]:
    """Every caller chain into `center` whose ROOT (the outermost caller — nothing here calls it)
    no test reaches: no test runs that path from its entry. Claimed only when every node on it is
    structurally known (`coverage` not `unknown`). Listed outermost-first, as a reader traces it.
    The centre itself may be covered through ANOTHER chain — this chain is still untested.

    Returns (the first `limit` chains, how many were counted, whether that count is exact): the
    count goes on to CHAIN_COUNT_CAP so the page can say "showing 50 of 312" (or "of at least
    1000") instead of presenting the cap as the answer."""
    limit = MAX_CHAINS if limit is None else limit
    chains: List[List[str]] = []
    count = [0]

    def walk(nid: str, chain: List[str]) -> None:
        if count[0] >= CHAIN_COUNT_CAP:
            return
        ups = [c for c in callers_of.get(nid, []) if c not in chain]
        if not ups:
            outer = g.nodes[chain[-1]].get("coverage")
            if len(chain) > 1 and outer == "untested" and \
                    all(g.nodes[x].get("coverage") != "unknown" for x in chain):
                count[0] += 1
                if len(chains) < limit:
                    chains.append([g.nodes[x]["label"] for x in reversed(chain)])
            return
        for c in ups:
            walk(c, chain + [c])

    walk(center, [center])
    return chains, count[0], count[0] < CHAIN_COUNT_CAP


def focus(root: str, symbol: str, layer: Any = None, index: Any = None,
          page: int = 1) -> Dict[str, Any]:
    """One hop each way, PAGE by page: page k draws up to k × PAGE_NODES callers (two questions,
    asked again — no walk to resume)."""
    layer = _layer(root, layer)
    page = max(1, int(page))
    g = _G()
    g.limit = PAGE_NODES * page
    center = _center_node(g, symbol)
    index = index if index is not None else _index_for(root, layer)
    if index is not None:                    # P37: the proven callers first, from the index
        _early_rings(g, index, symbol, center, 1, lambda _hop: report(
            note=f"read {symbol}'s callers from the code graph's index; confirming",
            partial=g.payload(view="focus", center=center, symbol=symbol, partial=True,
                              index_rings=1, root=root)))
        for n in g.nodes.values():
            if n["kind"] != "center":
                n["side"] = "left"
    meta = _fill_center(g, layer, root, symbol)
    callers = nav.run(root, "callers", symbol, layer=layer)
    for r in callers.references:
        cid = _ref_node(g, r, 1)
        if cid is None and g.truncated:
            break
        if cid:
            g.confirm(cid)
            g.nodes[cid]["side"] = "left"
            g.edge(cid, center, "CALLS", callers.basis, r.path, r.line)
    if not g.truncated:
        g.drop_unconfirmed()
    report(note=f"{len(callers.references)} caller(s); asking what {symbol} calls",
           partial=g.payload(view="focus", center=center, symbol=symbol, partial=True,
                             answer_bases=[callers.basis], root=root, **meta))
    callees = nav.run(root, "callees", symbol, layer=layer)
    for r in callees.references:
        if not r.symbol:
            continue
        nid = _sym_id(r.symbol)
        if g.node(nid, label=r.symbol, symbol=r.symbol, path=None, line=None, ring=1,
                  kind="callee", side="right") is not None:
            g.nodes[nid].setdefault("side", "right")
            g.edge(center, nid, "CALLS", callees.basis, r.path, r.line)
    more = g.truncated
    extra: Dict[str, Any] = {"more": more, "page": page, "page_nodes": PAGE_NODES}
    if more:
        shown = sum(1 for n in g.nodes.values() if n.get("side") == "left")
        extra["more_note"] = (f"showing {shown:,} of {len(callers.references):,} callers — "
                              f"Continue draws the next {PAGE_NODES:,}")
    return g.payload(view="focus", center=center, symbol=symbol, root=root, **extra,
                     answer_bases=sorted({callers.basis, callees.basis}),
                     callers_basis=callers.basis, callees_basis=callees.basis, **meta)


def _chain_graph(chain: List[str], hops: List[Tuple[str, Any, str]], pending: bool) -> _G:
    g = _G()
    for i, s in enumerate(chain):
        g.node(_sym_id(s), label=s, symbol=s, path=None, line=None, ring=i,
               kind="center" if i in (0, len(chain) - 1) else "caller",
               **({"pending": True} if pending else {}))
    for a, (b, r, basis) in zip(chain, hops):
        g.edge(_sym_id(a), _sym_id(b), "CALLS", basis, r.path, r.line, pending=pending)
    return g


def _confirm_path(root: str, layer: Any, chain: List[str]) -> Optional[List[Tuple[str, Any, str]]]:
    """Ask the tool, hop by hop, whether each call of the index's chain is in its answer: the
    chain's few questions instead of the breadth-first walk's hundreds. None when one is not."""
    hops = []
    for i, (a, b) in enumerate(zip(chain, chain[1:])):
        report(done=i, total=len(chain) - 1, unit="hops confirmed",
               note=f"confirming {a} → {b} with the code graph")
        res = nav.run(root, "callees", a, layer=layer)
        hit = next((r for r in res.references if r.symbol == b), None)
        if hit is None:
            return None
        hops.append((b, hit, res.basis))
    return hops


def path(root: str, src: str, dst: str, depth: int = 5, layer: Any = None,
         index: Any = None) -> Dict[str, Any]:
    """Shortest call path src → dst, BFS over `callees` answers. Each edge is one callees
    reference (the call site that justifies it). 0.0.26 P37: with code-review-graph's index,
    the chain is found there first (milliseconds to seconds on kubernetes, drawn as pending)
    and the tool confirms it hop by hop; only an unconfirmed chain walks the answers."""
    layer = _layer(root, layer)
    depth = max(1, min(MAX_DEPTH, int(depth)))
    index = index if index is not None else _index_for(root, layer)
    if index is not None:
        import sqlite3
        try:
            found_in_index = index.call_path(src, dst, depth)
        except (sqlite3.Error, OSError, ValueError):
            found_in_index = None
        if found_in_index:
            chain = [h[0] for h in found_in_index]
            sites = [(h[0], _Ref(h[1] or "", h[2], h[0]), "structural") for h in found_in_index[1:]]
            report(note="a path is in the code graph's index; confirming it hop by hop",
                   partial=_chain_graph(chain, sites, True).payload(
                       view="path", source=src, target=dst, depth=depth, found=True,
                       partial=True, index_path=True, root=root))
            hops = _confirm_path(root, layer, chain)
            if hops is not None:
                return _chain_graph(chain, hops, False).payload(
                    view="path", source=src, target=dst, depth=depth, found=True, root=root,
                    explored=len(hops), index_path=True,
                    answer_bases=sorted({h[2] for h in hops}), note="")
    parent: Dict[str, Tuple[str, Any, str]] = {}
    q = deque([(src, 0)])
    seen = {src}
    explored = 0
    found = False
    bases = set()
    while q and not found:
        sym, d = q.popleft()
        if d >= depth:
            continue
        res = nav.run(root, "callees", sym, layer=layer)
        explored += 1
        report(done=explored, unit="symbols explored", note=f"searching paths: {len(seen)} seen")
        bases.add(res.basis)
        for r in res.references:
            nxt = r.symbol
            if not nxt or nxt in seen:
                continue
            seen.add(nxt)
            parent[nxt] = (sym, r, res.basis)
            if nxt == dst:
                found = True
                break
            q.append((nxt, d + 1))
    g = _G()
    if found:
        chain = [dst]
        while chain[-1] != src:
            chain.append(parent[chain[-1]][0])
        chain.reverse()
        g = _chain_graph(chain, [(b, parent[b][1], parent[b][2]) for b in chain[1:]], False)
    return g.payload(view="path", source=src, target=dst, depth=depth, found=found, root=root,
                     explored=explored, answer_bases=sorted(bases),
                     note="" if found else f"no call path from {src} to {dst} within {depth} "
                                           "hops in the graph's answers")


def untested(root: str, symbol: str, depth: int = 3, layer: Any = None,
             page: int = 1) -> Dict[str, Any]:
    out = ripple(root, symbol, depth, layer, page=page)
    out["view"] = "untested"
    return out


__all__ = ["ripple", "focus", "path", "untested", "PAGE_NODES", "DRAW_LIMIT"]
