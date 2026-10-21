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

Bounded: at most `MAX_NODES` nodes (the "≤2k-node sub-graphs stay interactive" line); past it the
answer says `truncated` instead of silently stopping.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Dict, List, Optional, Tuple

from . import nav

MAX_NODES = 2000
MAX_DEPTH = 6
MAX_CHAINS = 50                 # untested chains listed; the answer says how many there were
CHAIN_COUNT_CAP = 1000          # chains counted past the listed ones before saying "at least"
STRUCTURAL = ("structural", "verified_empty")


class _G:
    """A graph under construction: nodes keyed by id, edges deduplicated by (src, dst, kind)."""

    def __init__(self) -> None:
        self.nodes: Dict[str, Dict[str, Any]] = {}
        self.edges: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        self.truncated = False
        self.bases: Dict[str, int] = {}

    def node(self, nid: str, **attrs: Any) -> Optional[Dict[str, Any]]:
        if nid in self.nodes:
            n = self.nodes[nid]
            for k, v in attrs.items():
                if k == "ring" and v is not None and (n.get("ring") is None or v < n["ring"]):
                    n["ring"] = v
            return n
        if len(self.nodes) >= MAX_NODES:
            self.truncated = True
            return None
        n = {"id": nid, **attrs}
        self.nodes[nid] = n
        return n

    def edge(self, src: str, dst: str, kind: str, basis: str, path: str, line: int) -> None:
        if src not in self.nodes or dst not in self.nodes or src == dst:
            return
        key = (src, dst, kind)
        if key in self.edges:
            return
        self.edges[key] = {"id": f"e{len(self.edges)}", "source": src, "target": dst,
                           "kind": kind, "basis": basis, "site": f"{path}:{line}"}
        self.bases[basis] = self.bases.get(basis, 0) + 1

    def payload(self, **extra: Any) -> Dict[str, Any]:
        out = {"sample": False, "nodes": list(self.nodes.values()),
               "edges": list(self.edges.values()), "truncated": self.truncated,
               "max_nodes": MAX_NODES, "bases": self.bases, **extra}
        h = nav.graph_health(extra.get("root") or "") if extra.get("root") else None
        out.pop("root", None)
        if h:
            out["graph_note"] = nav.degraded_line(h)
        return out


def _truncation_note(symbol: str, hop: int, direct: Optional[int]) -> str:
    """What to do when the picture stopped at MAX_NODES. "Lower the depth" is useless when the
    FIRST ring already overflows (a megahub): then say how many direct callers there are and
    point at Focus / narrowing instead."""
    if hop <= 1:
        n = f"{direct} direct callers" if direct is not None else "more direct callers than fit"
        return (f"stopped at {MAX_NODES} nodes — {symbol} has {n}, so even depth 1 does not fit; "
                f"open one caller in Focus, or search its call sites, instead of drawing them all")
    return (f"stopped at {MAX_NODES} nodes on ring {hop} — depth {hop - 1} fits; lower the depth "
            f"or open a caller in Focus")


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


def _center(g: _G, layer: Any, root: str, symbol: str) -> Tuple[str, Dict[str, Any]]:
    defs = nav.run(root, "defs", symbol, layer=layer)
    first = defs.references[0] if defs.references else None
    nid = _sym_id(symbol)
    g.node(nid, label=symbol, symbol=symbol, path=first.path if first else None,
           line=first.line if first else None, ring=0, kind="center",
           defs=len(defs.references), defs_basis=defs.basis)
    return nid, {"defs": defs.count, "defs_basis": defs.basis}


def _layer(root: str, layer: Any) -> Any:
    return layer if layer is not None else nav._layer(root)


def ripple(root: str, symbol: str, depth: int = 2, layer: Any = None) -> Dict[str, Any]:
    layer = _layer(root, layer)
    depth = max(1, min(MAX_DEPTH, int(depth)))
    g = _G()
    center, meta = _center(g, layer, root, symbol)
    callers_of: Dict[str, List[str]] = {}          # node -> its caller nodes (for coverage)
    seen = {symbol}
    frontier = [(symbol, center)]
    answers = []
    direct: Optional[int] = None
    stopped_at = 0
    for hop in range(1, depth + 1):
        nxt = []
        for sym, nid in frontier:
            res = nav.run(root, "callers", sym, layer=layer)
            answers.append(res.basis)
            if direct is None:
                direct = len(res.references)
            for r in res.references:
                cid = _ref_node(g, r, hop)
                if cid is None:
                    if g.truncated:
                        break                # full: the rest of this answer cannot be drawn
                    continue
                g.edge(cid, nid, "CALLS", res.basis, r.path, r.line)
                callers_of.setdefault(nid, []).append(cid)
                if r.symbol and r.symbol not in seen and g.nodes[cid]["kind"] != "test":
                    seen.add(r.symbol)
                    nxt.append((r.symbol, cid))
                elif r.symbol and r.symbol not in seen:
                    seen.add(r.symbol)
            if g.truncated:
                stopped_at = hop             # do not ask the rest of this ring: it cannot be drawn
                break
        frontier = nxt
        if not frontier or g.truncated:
            break
    imp = nav.run(root, "imports", symbol, layer=layer)
    answers.append(imp.basis)
    for r in imp.references:
        mid = f"file:{r.path}"
        n = g.node(mid, label=r.path, symbol=None, path=r.path, line=r.line, ring=1,
                   kind="test" if nav._is_test(r.path) else "module")
        if n is not None:
            g.edge(mid, center, "IMPORTS", imp.basis, r.path, r.line)
            callers_of.setdefault(center, []).append(mid)
    _mark_coverage(g, callers_of)
    for n in g.nodes.values():               # tests sit on the OUTER ring, whatever their hop
        if n["kind"] == "test":
            n["ring"] = depth + 1
    chains, total, exact = _untested_chains(g, center, callers_of)
    extra = {"truncation_note": _truncation_note(symbol, stopped_at or depth, direct)} \
        if g.truncated else {}
    return g.payload(view="ripple", center=center, symbol=symbol, depth=depth,
                     answer_bases=sorted(set(answers)), untested_chains=chains,
                     untested_chains_total=total, untested_chains_exact=exact,
                     direct_callers=direct, root=root, **extra, **meta)


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


def focus(root: str, symbol: str, layer: Any = None) -> Dict[str, Any]:
    layer = _layer(root, layer)
    g = _G()
    center, meta = _center(g, layer, root, symbol)
    callers = nav.run(root, "callers", symbol, layer=layer)
    for r in callers.references:
        cid = _ref_node(g, r, 1)
        if cid is None and g.truncated:
            break
        if cid:
            g.nodes[cid]["side"] = "left"
            g.edge(cid, center, "CALLS", callers.basis, r.path, r.line)
    callees = nav.run(root, "callees", symbol, layer=layer)
    for r in callees.references:
        if not r.symbol:
            continue
        nid = _sym_id(r.symbol)
        if g.node(nid, label=r.symbol, symbol=r.symbol, path=None, line=None, ring=1,
                  kind="callee", side="right") is not None:
            g.nodes[nid].setdefault("side", "right")
            g.edge(center, nid, "CALLS", callees.basis, r.path, r.line)
    extra = {"truncation_note": _truncation_note(symbol, 1, len(callers.references))} \
        if g.truncated else {}
    return g.payload(view="focus", center=center, symbol=symbol, root=root, **extra,
                     answer_bases=sorted({callers.basis, callees.basis}),
                     callers_basis=callers.basis, callees_basis=callees.basis, **meta)


def path(root: str, src: str, dst: str, depth: int = 5, layer: Any = None) -> Dict[str, Any]:
    """Shortest call path src → dst, BFS over `callees` answers. Each edge is one callees
    reference (the call site that justifies it)."""
    layer = _layer(root, layer)
    depth = max(1, min(MAX_DEPTH, int(depth)))
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
            if len(seen) > MAX_NODES:
                break
    g = _G()
    if found:
        chain = [dst]
        while chain[-1] != src:
            chain.append(parent[chain[-1]][0])
        chain.reverse()
        for i, s in enumerate(chain):
            g.node(_sym_id(s), label=s, symbol=s, path=None, line=None, ring=i,
                   kind="center" if s in (src, dst) else "caller")
        for a, b in zip(chain, chain[1:]):
            _p, r, basis = parent[b]
            g.nodes[_sym_id(b)]["path"] = None
            g.edge(_sym_id(a), _sym_id(b), "CALLS", basis, r.path, r.line)
    return g.payload(view="path", source=src, target=dst, depth=depth, found=found, root=root,
                     explored=explored, answer_bases=sorted(bases),
                     note="" if found else f"no call path from {src} to {dst} within {depth} "
                                           "hops in the graph's answers")


def untested(root: str, symbol: str, depth: int = 3, layer: Any = None) -> Dict[str, Any]:
    out = ripple(root, symbol, depth, layer)
    out["view"] = "untested"
    return out


__all__ = ["ripple", "focus", "path", "untested", "MAX_NODES"]
