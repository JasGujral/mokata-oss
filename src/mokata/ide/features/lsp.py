"""0.0.27 P24 `ID-LSP-READONLY-EARLIER` (research ruling R3) — read-only LSP behind workspace trust.

  GET  /api/lsp                       trust state, the servers (state, indexing, crashes, refusals),
                                      the languages and whether their server binary was found
  POST /api/lsp/trust/code {decision} print the one-time code to the server's terminal (P38 bar)
  POST /api/lsp/trust {decision, code}  trust (code required) / revoke (no code; stops servers);
                                      LEDGERED either way (`ide/trust.py`)
  POST /api/lsp/start {language}      start that language's server — refused untrusted, or when
                                      no binary is found (named; never installed)
  POST /api/lsp/stop  {language}      stop it
  GET  /api/lsp/{definition|hover|symbols|diagnostics}?path=&line=&col=
                                      a READ, as an `lsp.query` job (no deadline: 202 + Cancel
                                      when slow). Untrusted, never started, or indexing → the
                                      GRAPH answers meanwhile (`source: "graph"`), labelled.
                                      (0.0.28 G-Q3: references have ONE surface — Find Usages,
                                      `/api/usages`; the Language panel's references read is gone.)
  GET  /api/lsp/calls?path=&line=[&col=]&dir=in|out
                                      0.0.28 G — the call hierarchy, one level per question: who
                                      calls the function at (line, col) — or the function
                                      ENCLOSING `line` when no col is sent — or what it calls.
                                      Untrusted / unsupported / indexing / a server without the
                                      capability → callers from the call graph, labelled; callees
                                      empty with the reason (G-Q4). Any other parameter is a 400:
                                      a CallHierarchyItem never crosses the wire, either way.

A GET never starts a process (doc 109 §6: px0's `/api/lsp/warm` GET spawned rust-analyzer). The
edge (doc 109 §14): every answer carries `edge` — references split into tests vs code, the symbol's
callers and blast radius from the graph with its honesty `basis`, and the ACs whose spec anchors
name this file/symbol — so a reference list says what a change here would break and which
acceptance criterion a diagnostic sits under, which no plain LSP client can.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import posixpath
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from starlette.responses import JSONResponse

from ...errors import MokataError
from ...repo_paths import as_name
from .. import lsp as L
from .. import trust as T

ACTOR = "ide-tty"
KINDS = ("definition", "hover", "symbols", "diagnostics")
#: The code book's key for this decision. P38 keys its codes by proposal id, and proposals load
#: from the repo's state dir — so the key holds a character no proposal id can (verification L2).
TRUST_KEY = "lsp:workspace-trust"
_WORD = re.compile(r"[A-Za-z_$][\w$]*")
GRAPH_KIND = {"definition": "defs"}
#: What a call question may carry. Anything else — an `item`, its `data`, a method name — is a 400
#: before anything runs (the one control for "the page never sends an item", rule M-B).
CALL_PARAMS = frozenset({"path", "line", "col", "dir", "sync_ms", "job"})
CALL_DIRS = {"in": "callers", "out": "callees"}
_CODE = re.compile(r"\(code (-?\d+)\)$")


def word_at(line: str, col1: int) -> str:
    """The identifier under a 1-based character column ('' when none)."""
    i = max(0, col1 - 1)
    for m in _WORD.finditer(line):
        if m.start() <= i <= m.end():
            return m.group(0)
    return ""


def ac_ties(root: str, rel: str,
            symbols: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
    """`[{ac, text, symbol, kind, run}]` — the acceptance criteria of the default run's emitted
    spec whose code anchors name THIS file (and, when they say so, which symbol). Empty for an
    uninitialised repo or a run with no spec. An unreadable run store (OSError / bad JSON /
    mokata's own refusal) is "no ties"; anything else is a bug and propagates (the route answers
    500).

    0.0.27 P4 (review item 3) — the tie is EXACT: the anchor's file, normalised to a repo name,
    must equal `rel` (case-sensitive; a bare basename no longer ties to every file of that name). The AC's
    text is carried WHOLE (a display clips it, an answer never does). `symbols` (optional) keeps
    the anchors that name one of them, plus the file-level anchors (no symbol)."""
    try:
        from ...engine.spec_gate import read_emitted_spec
        from .. import plans
        ids = plans.local_run_ids(root)
        rid = plans._default_run(root, ids[-50:]) if ids else None
        if not rid:
            return []
        spec, malformed = read_emitted_spec(plans._run_store(root, rid))
        if spec is None or malformed:
            return []
    except (OSError, ValueError, MokataError):
        return []
    want_syms = None if symbols is None else {s for s in symbols if s}
    out = []
    for c in spec.criteria:
        for a in c.anchors:
            # an anchor's file is what a human wrote into the spec: it crosses in, maybe natively
            raw = str(as_name(str(a.file or ""))).strip()
            f = posixpath.normpath(raw) if raw else ""
            if not f or f != rel:
                continue
            sym = a.symbol or ""
            if want_syms is not None and sym and sym not in want_syms \
                    and sym.rsplit(".", 1)[-1] not in want_syms:
                continue
            out.append({"ac": c.id, "text": c.text or "", "symbol": sym, "kind": a.kind,
                        "run": rid})
    return out


def graph_edge(root: str, symbol: str) -> Dict[str, Any]:
    """Callers and blast radius (default budget) of `symbol` from the graph layer, with basis."""
    from .. import nav
    out: Dict[str, Any] = {"symbol": symbol}
    if not symbol:
        return out
    try:
        layer = nav._layer(root)
        callers = nav.run(root, "callers", symbol, layer=layer)
        blast = nav.run(root, "blast_radius", symbol, layer=layer)
        tests = sorted({f"{r.path}:{r.line}" for r in blast.references if nav._is_test(r.path)})
        out.update({"callers": len(callers.references), "callers_basis": callers.basis,
                    "blast_radius": len(blast.references), "blast_basis": blast.basis,
                    "blast_truncated": bool(getattr(blast, "truncated", False)),
                    "tests_reaching": tests[:200], "backend": blast.backend,
                    "verdict": ("covered" if tests else "untested" if blast.structural
                                and not getattr(blast, "truncated", False) else "unknown")})
    except Exception as exc:  # noqa: BLE001 — an edge that cannot be computed says so
        out["note"] = f"graph unavailable: {type(exc).__name__}: {exc}"[:300]
    return out


def routes(reg: Any, ctx: Any) -> None:
    import asyncio
    state = ctx.state
    root = state.root

    def _publish(evt: Dict[str, Any]) -> None:
        loop = state.loop
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(lambda: asyncio.ensure_future(state.bus.publish(evt)))
        except RuntimeError:
            pass

    host = L.Host(root, trusted=lambda: T.status(root)["trusted"], on_event=_publish)
    state.lsp = host
    _close = state.close

    def close(wait: float = 0.0) -> None:       # the servers die with the IDE
        host.stop_all("the IDE stopped")
        _close(wait)
    state.close = close

    def _tty() -> Dict[str, Any]:
        ok, why = state.approve_tty
        return {"available": bool(ok), "reason": why}

    def _bad(status: int, error: str, note: str, **extra: Any) -> Any:
        return JSONResponse({"error": error, "note": note, **extra}, status_code=status)

    # ------------------------------------------------------------------------ status
    def read(_request: Any) -> Dict[str, Any]:
        if state.demo:
            return {"sample": True, "trust": {"trusted": False, "at": None, "by": None, "why": T.WHY, "note": T.NOTE,
                                              "command": T.TRUST_CMD, "revoke_command": T.UNTRUST_CMD,
                                              "will_run": [],
                                              "approve_here": {"available": False,
                                                               "reason": "SAMPLE data"}},
                    "servers": [], "languages": [], "config": [], "read_only": L.READ_ONLY}
        trust = T.status(root)
        trust["approve_here"] = _tty()
        # H1 (ruled): the trust prompt shows the EXACT command lines trusting allows.
        trust["will_run"] = L.will_run(root)
        langs = []
        for name, spec in L.languages().items():
            found = L.find_server(root, name)
            langs.append({"language": name, "exts": spec["exts"], "found": found["found"],
                          "server": found.get("name"), "candidates": found.get("candidates", []),
                          "note": found.get("note", "")})
        return {"sample": False, "trust": trust, **host.status(), "languages": langs,
                "config": [os.path.expanduser(L.USER_CONFIG)],
                "read_only": L.READ_ONLY}
    reg.register("/api/lsp", read, kind="json", feature="lsp")

    # ------------------------------------------------------------------------ trust (P2)
    async def trust_code(request: Any):
        b = await ctx.body(request)
        b = b if isinstance(b, dict) else {}
        decision = str(b.get("decision") or "")
        if decision != "trust":
            return _bad(400, "BadRequest", "decision must be 'trust' (revoking needs no code)")
        if state.demo:
            return _bad(409, "Sample", "SAMPLE data — there is nothing to trust")
        ok, why = state.approve_tty
        if not ok:
            return _bad(409, "NoTerminal", why, command=T.TRUST_CMD)
        minted, refusal, retry = state.codes.mint(TRUST_KEY, "approve")
        if minted is None:
            return _bad(429, "RateLimited", refusal, retry_in=round(retry, 1))
        from .. import approve_codes as AC
        shown = f"{minted[:4]}-{minted[4:]}"
        runs = L.will_run(root)
        lines = "".join(f"    {r['language']}: {' '.join(r['argv'])}\n" for r in runs) or \
            "    (no language server found yet — trusting allows the built-in ones once installed)\n"
        printed = AC.say(f"\n  mokata ide · to TRUST this workspace ({root}) — these commands will "
                         f"run its code:\n{lines}  type this code in the window:  {shown}   (one "
                         f"use, {int(AC.CODE_TTL)} s)\n\n")
        if not printed:
            state.codes.void(TRUST_KEY)
            return _bad(409, "NoTerminal", AC.NO_TTY, command=T.TRUST_CMD)
        return JSONResponse({"printed": True, "expires_in": int(AC.CODE_TTL), "tries": AC.MAX_TRIES,
                             "note": "a one-time code was printed in the terminal running "
                                     "`mokata ide --foreground` — type it here"})
    reg.register("/api/lsp/trust/code", trust_code, kind="action", feature="lsp")

    async def trust(request: Any):
        b = await ctx.body(request)
        b = b if isinstance(b, dict) else {}
        decision = str(b.get("decision") or "")
        if decision not in ("trust", "revoke"):
            return _bad(400, "BadRequest", "decision must be 'trust' or 'revoke'")
        if state.demo:
            return _bad(409, "Sample", "SAMPLE data — there is nothing to trust")
        if decision == "revoke":
            res = await asyncio.to_thread(T.decide, root, False, ACTOR)
            await asyncio.to_thread(host.stop_all, "trust revoked")
            res["approve_here"] = _tty()
            return JSONResponse({"decision": "revoke", "trust": res, "stopped": True})
        ok, why = state.approve_tty
        if not ok:
            return _bad(409, "NoTerminal", why, command=T.TRUST_CMD)
        good, why = state.codes.check(TRUST_KEY, "approve", b.get("code"))
        if not good:
            return _bad(403, "BadCode", why)
        res = await asyncio.to_thread(T.decide, root, True, ACTOR)
        res["approve_here"] = _tty()
        return JSONResponse({"decision": "trust", "trust": res, "stopped": False})
    reg.register("/api/lsp/trust", trust, kind="action", feature="lsp")

    # ------------------------------------------------------------------------ processes
    def _language(b: Dict[str, Any]) -> Optional[str]:
        lang = b.get("language")
        return lang if isinstance(lang, str) and lang in L.languages() else None

    async def start(request: Any):
        b = await ctx.body(request)
        b = b if isinstance(b, dict) else {}
        lang = _language(b)
        if lang is None:
            return _bad(400, "BadRequest", f"language must be one of {sorted(L.languages())}")
        if state.demo:
            return JSONResponse({"started": False, "error": "Sample", "language": lang,
                                 "note": "SAMPLE data — no language server"}, status_code=409)
        state.attach_loop()
        res = await asyncio.to_thread(host.start, lang, True)
        if not res.get("started"):
            return JSONResponse(res, status_code=409)
        return JSONResponse(res)
    reg.register("/api/lsp/start", start, kind="action", feature="lsp")

    async def stop(request: Any):
        b = await ctx.body(request)
        b = b if isinstance(b, dict) else {}
        lang = _language(b)
        if lang is None:
            return _bad(400, "BadRequest", f"language must be one of {sorted(L.languages())}")
        stopped = await asyncio.to_thread(host.stop, lang)
        return JSONResponse({"stopped": bool(stopped), "language": lang})
    reg.register("/api/lsp/stop", stop, kind="action", feature="lsp")

    # ------------------------------------------------------------------------ the reads
    def _pos(request: Any) -> Tuple[str, int, int]:
        path = ctx.q(request, "path")
        if not path:
            raise ctx.bad_request("path is required")
        try:
            line = int(ctx.q(request, "line") or 1)
            col = int(ctx.q(request, "col") or 1)
        except ValueError:
            raise ctx.bad_request("line and col are integers (1-based)") from None
        if line < 1 or col < 1:
            raise ctx.bad_request("line and col are 1-based")
        return path, line, col

    def graph_answer(kind: str, rel: str, text: str, line: int, col: int, lang: Optional[str],
                     why: Dict[str, Any]) -> Dict[str, Any]:
        """What the IDE can say WITHOUT a language server — the graph layer, labelled."""
        from .. import nav
        lines = text.splitlines()
        sym = word_at(lines[line - 1] if 0 < line <= len(lines) else "", col)
        out: Dict[str, Any] = {"sample": False, "kind": kind, "path": rel, "line": line, "col": col,
                               "language": lang, "symbol": sym, "source": "graph",
                               "lsp": {**why, "note": why.get("note", "") or
                                       ("this workspace is not trusted — the graph layer answers"
                                        if why.get("state") == "untrusted" else "")},
                               "edge": {"ac": ac_ties(root, rel)}}
        if kind in GRAPH_KIND:
            if sym:
                try:
                    res = nav.run(root, GRAPH_KIND[kind], sym).to_dict()
                except MokataError as exc:            # the floor itself is unavailable: say so
                    res = {"references": [], "basis": None, "backend": "",
                           "note": f"graph unavailable: {exc}"[:300]}
                refs = res.get("references") or []
                out["results"] = [{"path": r.get("path"), "line": r.get("line"), "col": 1,
                                   "snippet": r.get("snippet") or "", "symbol": r.get("symbol"),
                                   "outside": False, "test": nav._is_test(r.get("path") or "")}
                                  for r in refs]
                out["basis"], out["backend"] = res.get("basis"), res.get("backend")
                out["note"] = res.get("note", "")
            else:
                out["results"], out["note"] = [], "no identifier at that position"
            out["edge"].update(graph_edge(root, sym))
            out["edge"]["tests"] = sum(1 for r in out["results"] if r["test"])
        elif kind == "hover":
            out["hover"], out["results"] = "", []
            out["edge"].update(graph_edge(root, sym))
            out["note"] = "type information needs the language server; the graph says who calls it"
        elif kind == "symbols":
            ol = nav.outline(rel, text, root)
            out["results"] = [{"name": s["name"], "kind": s.get("kind", "symbol"), "line": s["line"],
                               "end": s.get("end") or s["line"], "depth": s.get("depth", 0),
                               "detail": ""} for s in ol.get("symbols", [])]
            out["basis"], out["note"] = ol.get("basis"), ol.get("note", "")
        else:
            out["results"], out["note"] = [], "diagnostics come from the language server only"
        return out

    def lsp_answer(kind: str, srv: L.Server, rel: str, abspath: str, line: int, col: int,
                   lang: str, how: Dict[str, Any]) -> Dict[str, Any]:
        from .. import nav
        spec = L.languages()[lang]
        uri, text = srv.open(abspath, L.language_id(lang, rel, spec))
        lines = text.splitlines()
        cur = lines[line - 1] if 0 < line <= len(lines) else ""
        sym = word_at(cur, col)
        pos = {"line": line - 1, "character": L.utf16_col(cur, col)}
        doc = {"textDocument": {"uri": uri}}
        out: Dict[str, Any] = {"sample": False, "kind": kind, "path": rel, "line": line, "col": col,
                               "language": lang, "symbol": sym, "source": "lsp",
                               "lsp": {**how, "server": srv.name},
                               "edge": {"ac": ac_ties(root, rel)}}
        cache = L._Lines()
        from ...knowledge.cancel import checkpoint
        if kind == "definition":
            res = srv.request("textDocument/definition", {**doc, "position": pos})
            locs = res if isinstance(res, list) else [res] if isinstance(res, dict) else []
            got = [L.location(root, x, cache) for x in locs]
            out["skipped"] = sum(1 for g in got if g is None)   # malformed: counted, not fatal
            out["results"] = [dict(g, test=nav._is_test(g["path"] or "")) for g in got if g]
        elif kind == "hover":
            res = srv.request("textDocument/hover", {**doc, "position": pos})
            out["hover"] = L.hover_text(res)
            out["results"] = []
        elif kind == "symbols":
            res = srv.request("textDocument/documentSymbol", doc)
            out["results"] = L.symbols_flat(res)
        else:
            out["results"] = L.diagnostics_of(root, srv, abspath, cache)
            out["note"] = ("what the server has published for this file so far — a server still "
                           "analysing it publishes more; re-ask" if srv.indexing() else "")
        checkpoint()
        if kind in ("definition", "hover"):
            out["edge"].update(graph_edge(root, sym))
            out["edge"]["tests"] = sum(1 for r in out.get("results", []) if r.get("test"))
            out["edge"]["code"] = sum(1 for r in out.get("results", []) if not r.get("test"))
        if kind == "diagnostics" and out["results"]:
            ol = nav.outline(rel, text, root)
            ties = out["edge"]["ac"]
            for d in out["results"]:
                s = nav.symbol_at(ol.get("symbols", []), d["line"])
                d["symbol"] = s["name"] if s else None
                d["ac"] = [t["ac"] for t in ties if not t["symbol"] or (s and t["symbol"] == s["name"])]
        return out

    def ask(kind: str):
        def handler(request: Any):
            path, line, col = _pos(request)
            if state.demo:
                return lambda: {"sample": True, "kind": kind, "path": path, "line": line, "col": col,
                                "language": None, "symbol": "", "source": "none",
                                "lsp": {"state": "sample", "note": "SAMPLE data — no language server"},
                                "results": [], "edge": {}}
            from .. import nav
            rel = nav.contained_rel(state.source._code_root(), path)
            code_root = state.source._code_root()
            abspath = os.path.join(code_root, *rel.split("/"))
            if not os.path.isfile(abspath):
                raise ctx.bad_request(f"no file {rel} in the repository")
            lang = L.language_of(rel)

            def work() -> Dict[str, Any]:
                text = L.source_text(abspath)
                if not T.status(root)["trusted"]:
                    return graph_answer(kind, rel, text, line, col, lang, {"state": "untrusted"})
                if lang is None:
                    return graph_answer(kind, rel, text, line, col, lang,
                                        {"state": "unsupported", "note": "no language server is "
                                         "mapped to this file type (add one in ~/.mokata/lsp.json)"})
                srv, why = host.server_for(lang)
                if srv is None:
                    return graph_answer(kind, rel, text, line, col, lang, why)
                busy = srv.indexing()
                if busy and kind != "diagnostics":
                    job = host.index_jobs.get(lang)
                    return graph_answer(kind, rel, text, line, col, lang, {
                        "state": "indexing", "server": srv.name, "progress": busy,
                        "job": getattr(job, "id", None),
                        "note": f"{srv.name} is still indexing ({busy.get('title') or 'working'}) "
                                "— the graph layer answers meanwhile; ask again when it is done"})
                try:
                    return lsp_answer(kind, srv, rel, abspath, line, col, lang, why)
                except L.ServerGone as exc:
                    return graph_answer(kind, rel, text, line, col, lang,
                                        {"state": "crashed", "note": f"{exc} — it restarts at the "
                                                                     "next ask"})
            return work
        return handler

    # ------------------------------------------------------------------------ the call hierarchy
    def _call_question(request: Any) -> Tuple[str, int, Optional[int], str]:
        keys = [k for k, _v in request.query_params.multi_items()]
        extra = sorted(set(keys) - CALL_PARAMS) or sorted({k for k in keys if keys.count(k) > 1})
        if extra:
            raise ctx.bad_request(f"unknown or repeated parameter {', '.join(extra)[:120]} — a call "
                                  "question is path, line, col (optional) and dir, once each")
        direction = ctx.q(request, "dir")
        if direction not in CALL_DIRS:
            raise ctx.bad_request("dir must be 'in' (who calls it) or 'out' (what it calls)")
        path = ctx.q(request, "path")
        if not path:
            raise ctx.bad_request("path is required")
        try:
            line = int(ctx.q(request, "line") or 1)
            col = int(ctx.q(request, "col")) if ctx.q(request, "col") else None
        except ValueError:
            raise ctx.bad_request("line and col are integers (1-based)") from None
        if line < 1 or (col is not None and col < 1):
            raise ctx.bad_request("line and col are 1-based")
        return path, line, col, direction

    def _target(rel: str, text: str, line: int, col: Optional[int]) -> Dict[str, Any]:
        """Where the question is asked: (line, col) exactly when a col is sent (a tree row's own
        position); otherwise the definition ENCLOSING `line` (the Find Usages resolver), at its
        name. `{line, col, cur, symbol}` or `{}` when nothing encloses the line."""
        from .. import usages as U
        lines = U.viewer_lines(text)
        if col is None:
            d, _ol = U.definition_at(state.source._code_root(), rel, line, text)
            if d is None:
                return {}
            line, col = d.line, d.col
        cur = lines[line - 1] if 0 < line <= len(lines) else ""
        return {"line": line, "col": col, "cur": cur, "symbol": word_at(cur, col)}

    def _call_rows_graph(code_root: str, refs: List[Any]) -> List[Dict[str, Any]]:
        """Graph callers as rows (the call LINE; no col — expanding one asks about the function
        enclosing that line). Snippets read through the bounded reader, inside the repo only."""
        from .. import nav
        lines = L.CallLines()
        places = []
        for r in refs:
            abspath = os.path.join(code_root, *str(r.path).split("/"))
            _rel, outside = L._rel(code_root, abspath)
            places.append((abspath, outside))
            if not outside:
                lines.want(abspath, max(0, int(r.line) - 1))
        lines.load(_checkpoint)
        out = []
        for r, (abspath, outside) in zip(refs, places):
            text = None if outside else lines.line(abspath, max(0, int(r.line) - 1))
            out.append({"name": str(r.symbol or "")[:300], "kind": "function", "detail": "",
                        "path": r.path, "shown": str(r.path)[:200], "outside": outside,
                        "line": int(r.line), "col": None, "col_exact": False,
                        "snippet": text.strip()[:300] if text else "", "calls": None,
                        "test": (not outside) and nav._is_test(r.path)})
        return out

    def _checkpoint() -> None:
        from ...knowledge.cancel import checkpoint
        checkpoint()

    def calls_graph(direction: str, rel: str, line: int, col: Optional[int],
                    tgt: Dict[str, Any], lang: Optional[str], why: Dict[str, Any]) -> Dict[str, Any]:
        """No language server answer: callers from the call graph (labelled `graph`); callees are
        EMPTY with the reason (G-Q4) — `asked: false` says nobody was asked."""
        from .. import nav
        from ...knowledge.cancel import report
        sym = tgt.get("symbol", "")
        reason = why.get("note") or why.get("state", "")
        out: Dict[str, Any] = {
            "sample": False, "kind": "calls", "dir": direction, "path": rel, "line": line,
            "col": col, "language": lang, "symbol": sym, "source": "graph", "asked": False,
            "callable": None, "lsp": dict(why, note=reason), "groups": [], "total": 0,
            "truncated": False, "skipped": 0, "complete": False, "items_named": 0,
            "items_asked": 0, "cap": L.CALLS_MAX, "basis": None, "backend": "", "note": "",
            "edge": {"ac": ac_ties(root, rel)}}
        if direction == "out":
            out["source"] = "none"
            out["note"] = (f"callees need the language server ({why.get('state')}) — the call graph "
                           "answers callers only")
            if why.get("state") == "no-call-hierarchy":
                out["note"] = (f"{why.get('server')} offers no call hierarchy — callees need it; "
                               "the call graph answers callers only")
            return out
        if not sym:
            out["note"] = "no function at that position"
            out["callable"] = False
            return out
        report(note=f"asking the call graph for the callers of {sym}")
        try:
            res = nav.run(root, "callers", sym)
            refs = list(res.references)
            out["basis"], out["backend"] = res.basis, res.backend
            out["note"] = res.note or ""
        except MokataError as exc:            # the floor itself is unavailable: say so
            refs = []
            out["note"] = f"graph unavailable: {exc}"[:300]
        rows = _call_rows_graph(state.source._code_root(), refs[:L.CALLS_MAX])
        item = {"name": sym, "kind": "function", "detail": "", "path": rel, "shown": rel,
                "outside": False, "line": tgt.get("line", line), "col": tgt.get("col"),
                "col_exact": True, "snippet": tgt.get("cur", "").strip()[:300],
                "test": nav._is_test(rel)}
        out["groups"] = [{"item": item, "rows": rows, "total": len(refs),
                          "truncated": len(rows) < len(refs), "skipped": 0}]
        out["total"], out["truncated"] = len(refs), len(rows) < len(refs)
        out["edge"].update(graph_edge(root, sym))
        return out

    def calls_lsp(direction: str, srv: L.Server, rel: str, abspath: str, line: int,
                  col: Optional[int], tgt: Dict[str, Any], lang: str, how: Dict[str, Any],
                  text: str) -> Dict[str, Any]:
        from .. import nav
        base = {"sample": False, "kind": "calls", "dir": direction, "path": rel, "line": line,
                "col": col, "language": lang, "symbol": tgt.get("symbol", ""), "source": "lsp",
                "lsp": {**how, "server": srv.name}, "cap": L.CALLS_MAX, "basis": "lsp",
                "backend": srv.name, "note": "", "edge": {"ac": ac_ties(root, rel)}}
        if not tgt:
            # mokata's resolver found no function and the server was NOT asked: not an LSP
            # answer (verify-G L-4) — `source: none`, the server's state still said
            return dict(base, source="none", basis=None, backend="", asked=False, callable=False,
                        groups=[], total=0, truncated=False, skipped=0, complete=False,
                        items_named=0, items_asked=0, note=f"no function encloses line {line}")
        if not host.recheck_trust():
            return calls_graph(direction, rel, line, col, tgt, lang, {"state": "untrusted"})
        spec = L.languages()[lang]
        uri, _ = srv.open(abspath, L.language_id(lang, rel, spec))
        pos = {"line": tgt["line"] - 1, "character": L.utf16_col(tgt["cur"], tgt["col"])}
        res = L.call_edges(state.source._code_root(), srv, uri, pos, direction,
                           recheck=host.recheck_trust, checkpoint=_checkpoint)
        if res.get("revoked"):
            host.recheck_trust()
            return calls_graph(direction, rel, line, col, tgt, lang, {"state": "untrusted"})
        groups = res["groups"]
        for g in groups:
            for r in g["rows"] + [g["item"]]:
                r["test"] = (not r["outside"]) and nav._is_test(r["path"] or "")
        total = sum(g["total"] for g in groups)
        truncated = any(g["truncated"] for g in groups)
        out = dict(base, asked=True, callable=res["callable"], groups=groups, total=total,
                   truncated=truncated, skipped=res["skipped"],
                   items_named=res["items_named"], items_asked=res["items_asked"],
                   complete=(not truncated and res["skipped"] == 0
                             and res["items_asked"] == res["items_named"]))
        if not res["callable"]:
            out["note"] = "the language server names no function at that position"
        sym = groups[0]["item"]["name"] if groups else tgt.get("symbol", "")
        out["symbol"] = sym
        out["edge"].update(graph_edge(root, sym))
        return out

    def calls_handler(request: Any):
        path, line, col, direction = _call_question(request)
        if state.demo:
            return lambda: {"sample": True, "kind": "calls", "dir": direction, "path": path,
                            "line": line, "col": col, "language": None, "symbol": "",
                            "source": "none", "asked": False, "callable": None,
                            "lsp": {"state": "sample", "note": "SAMPLE data — no language server"},
                            "groups": [], "total": 0, "truncated": False, "skipped": 0,
                            "complete": False, "items_named": 0, "items_asked": 0,
                            "cap": L.CALLS_MAX, "note": "", "edge": {}}
        from .. import nav
        code_root = state.source._code_root()
        rel = nav.contained_rel(code_root, path)
        abspath = os.path.join(code_root, *rel.split("/"))
        if not os.path.isfile(abspath):
            raise ctx.bad_request(f"no file {rel} in the repository")
        lang = L.language_of(rel)

        def work() -> Dict[str, Any]:
            text = L.source_text(abspath)
            tgt = _target(rel, text, line, col)
            # The FIRST rung, and it stops any running server (rule M-C): untrusted wins over
            # every other state, and nothing below may send.
            if not host.recheck_trust():
                return calls_graph(direction, rel, line, col, tgt, lang,
                                   {"state": "untrusted", "note": "this workspace is not trusted"
                                    " — the call graph answers callers"})
            if lang is None:
                return calls_graph(direction, rel, line, col, tgt, lang, {
                    "state": "unsupported", "note": "no language server is mapped to this file "
                    "type (add one in ~/.mokata/lsp.json)"})
            srv, why = host.server_for(lang)
            if srv is None:
                return calls_graph(direction, rel, line, col, tgt, lang, why)
            busy = srv.indexing()
            if busy:
                job = host.index_jobs.get(lang)
                return calls_graph(direction, rel, line, col, tgt, lang, {
                    "state": "indexing", "server": srv.name, "progress": busy,
                    "job": getattr(job, "id", None),
                    "note": f"{srv.name} is still indexing — the graph shows callers meanwhile"})
            if not L.call_hierarchy_supported(srv):
                return calls_graph(direction, rel, line, col, tgt, lang, {
                    "state": "no-call-hierarchy", "server": srv.name,
                    "note": f"{srv.name} offers no call hierarchy — the call graph answers callers"})
            try:
                return calls_lsp(direction, srv, rel, abspath, line, col, tgt, lang, why, text)
            except L.ServerGone as exc:
                if not host.recheck_trust():
                    return calls_graph(direction, rel, line, col, tgt, lang, {"state": "untrusted"})
                return calls_graph(direction, rel, line, col, tgt, lang, {
                    "state": "crashed", "server": srv.name,
                    "note": f"{exc} — it restarts at the next ask"[:300]})
            except L.ServerError as exc:
                # The code only: a server's own message never reaches the page (rule F5).
                m = _CODE.search(str(exc))
                code = int(m.group(1)) if m else None
                note = ("the code changed while asking — ask again" if code in (-32800, -32801)
                        else f"{srv.name} answered an error (code {code})")
                return calls_graph(direction, rel, line, col, tgt, lang, {
                    "state": "server-error", "server": srv.name, "code": code, "note": note})
        return work

    reg.register("/api/lsp/calls", calls_handler, kind="json", feature="lsp", job="lsp.query",
                 job_label=(lambda r: f"LSP {CALL_DIRS.get(ctx.q(r, 'dir'), 'calls')} · "
                                      f"{ctx.q(r, 'path')[:80]}:{ctx.q(r, 'line')}"))

    for k in KINDS:
        reg.register(f"/api/lsp/{k}", ask(k), kind="json", feature="lsp", job="lsp.query",
                     job_label=(lambda r, k=k: f"LSP {k} · {ctx.q(r, 'path')[:80]}:{ctx.q(r, 'line')}"))
