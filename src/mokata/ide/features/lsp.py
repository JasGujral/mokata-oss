"""0.0.27 P24 `ID-LSP-READONLY-EARLIER` (research ruling R3) — read-only LSP behind workspace trust.

  GET  /api/lsp                       trust state, the servers (state, indexing, crashes, refusals),
                                      the languages and whether their server binary was found
  POST /api/lsp/trust/code {decision} print the one-time code to the server's terminal (P38 bar)
  POST /api/lsp/trust {decision, code}  trust (code required) / revoke (no code; stops servers);
                                      LEDGERED either way (`ide/trust.py`)
  POST /api/lsp/start {language}      start that language's server — refused untrusted, or when
                                      no binary is found (named; never installed)
  POST /api/lsp/stop  {language}      stop it
  GET  /api/lsp/{definition|references|hover|symbols|diagnostics}?path=&line=&col=
                                      a READ, as an `lsp.query` job (no deadline: 202 + Cancel
                                      when slow). Untrusted, never started, or indexing → the
                                      GRAPH answers meanwhile (`source: "graph"`), labelled.

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
KINDS = ("definition", "references", "hover", "symbols", "diagnostics")
#: The code book's key for this decision. P38 keys its codes by proposal id, and proposals load
#: from the repo's state dir — so the key holds a character no proposal id can (verification L2).
TRUST_KEY = "lsp:workspace-trust"
_WORD = re.compile(r"[A-Za-z_$][\w$]*")
GRAPH_KIND = {"definition": "defs", "references": "refs"}


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
            out["results"] = [dict(L.location(root, x, cache), test=nav._is_test(
                L.location(root, x, cache, snippet=False)["path"] or "")) for x in locs]
        elif kind == "references":
            res = srv.request("textDocument/references",
                              {**doc, "position": pos, "context": {"includeDeclaration": True}})
            locs = res if isinstance(res, list) else []
            results = []
            for i, x in enumerate(locs):
                if i % 500 == 0:
                    checkpoint()
                r = L.location(root, x, cache, snippet=i < L.REFERENCES_SNIPPET_MAX)
                r["test"] = nav._is_test(r["path"] or "") if not r["outside"] else False
                results.append(r)
            out["results"] = results
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
        if kind in ("definition", "references", "hover"):
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

    for k in KINDS:
        reg.register(f"/api/lsp/{k}", ask(k), kind="json", feature="lsp", job="lsp.query",
                     job_label=(lambda r, k=k: f"LSP {k} · {ctx.q(r, 'path')[:80]}:{ctx.q(r, 'line')}"))
