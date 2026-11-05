"""A tiny stdio language server for the P24 tests (no third-party code; stdlib only).

Speaks just enough LSP: initialize (then a `$/progress` indexing pass, `window/workDoneProgress/
create` first), didOpen/didClose, definition / references / hover / documentSymbol over the texts
it was handed, publishDiagnostics on open (a warning on every line that says TODO), shutdown/exit.

Knobs (argv):
  --index-seconds N     how long the indexing pass takes (progress every 0.1 s)
  --crash-on METHOD     exit(3) the moment METHOD arrives (a crash mid-conversation)
  --slow METHOD         never answer METHOD until `$/cancelRequest` names it (then -32800)
  --apply-edit          before answering the first hover, ask the client to `workspace/applyEdit`
  --log PATH            append every method received, one per line (the tests' audit)
  --grandchild MODE     start a long-lived grandchild (like tsserver / proc-macro-srv): `inherit`
                        leaves it holding this server's stdout/stderr pipes, `detach` does not
  --pidfile PATH        write the grandchild's pid there
  --ignore-shutdown     never answer `shutdown`, ignore `exit` (a server that lingers)
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time

ARGS = sys.argv[1:]


def _opt(name, default=None):
    if name in ARGS:
        i = ARGS.index(name)
        return ARGS[i + 1] if i + 1 < len(ARGS) else default
    return default


INDEX_S = float(_opt("--index-seconds", "0.3"))
CRASH_ON = _opt("--crash-on")
SLOW = _opt("--slow")
APPLY_EDIT = "--apply-edit" in ARGS
LOG = _opt("--log")
GRANDCHILD = _opt("--grandchild")
PIDFILE = _opt("--pidfile")
IGNORE_SHUTDOWN = "--ignore-shutdown" in ARGS

OUT = sys.stdout.buffer
IN = sys.stdin.buffer
LOCK = threading.Lock()
DOCS = {}
NEXT_ID = [1000]
SLOW_WAITING = {}
WORD = re.compile(r"[A-Za-z_]\w*")


def log(method):
    if LOG:
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(method + "\n")


def send(msg):
    body = json.dumps({"jsonrpc": "2.0", **msg}).encode("utf-8")
    with LOCK:
        OUT.write(b"Content-Length: %d\r\n\r\n" % len(body))
        OUT.write(body)
        OUT.flush()


def reply(rid, result=None, error=None):
    send({"id": rid, "result": result} if error is None else {"id": rid, "error": error})


def request(method, params):
    NEXT_ID[0] += 1
    send({"id": NEXT_ID[0], "method": method, "params": params})
    return NEXT_ID[0]


def read():
    length = None
    while True:
        line = IN.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        k, _, v = line.decode().partition(":")
        if k.strip().lower() == "content-length":
            length = int(v)
    return json.loads(IN.read(length).decode("utf-8"))


def index_pass():
    request("window/workDoneProgress/create", {"token": "idx"})
    send({"method": "$/progress", "params": {"token": "idx", "value": {
        "kind": "begin", "title": "Indexing", "message": "0 files", "percentage": 0}}})
    t0 = time.time()
    while time.time() - t0 < INDEX_S:
        time.sleep(0.1)
        pct = min(99, int(100 * (time.time() - t0) / INDEX_S))
        send({"method": "$/progress", "params": {"token": "idx", "value": {
            "kind": "report", "message": f"{pct} files", "percentage": pct}}})
    send({"method": "$/progress", "params": {"token": "idx", "value": {"kind": "end"}}})


def word_at(text, line, ch):
    lines = text.split("\n")
    if line >= len(lines):
        return ""
    for m in WORD.finditer(lines[line]):
        if m.start() <= ch <= m.end():
            return m.group(0)
    return ""


def vscode_uri(uri):
    """The URI the way vscode-uri based servers (typescript-language-server, pyright, pylsp)
    spell a location: the path percent-encoded, a Windows drive as a LOWER-case letter with an
    escaped colon (`file:///c%3A/Users/x`). The client's own spelling (`file:///C:/Users/x`) is
    NOT what real servers send back, and a client that only reads its own spelling passes here
    and fails on a real machine."""
    from urllib.parse import quote, unquote, urlsplit
    path = unquote(urlsplit(uri).path)
    m = re.match(r"^/([A-Za-z]):(/.*)?$", path)
    if m:
        return "file:///%s%%3A%s" % (m.group(1).lower(), quote(m.group(2) or "/", safe="/"))
    return "file://" + quote(path, safe="/")


def occurrences(uri, text, word):
    uri = vscode_uri(uri)
    out = []
    for i, ln in enumerate(text.split("\n")):
        for m in re.finditer(r"\b%s\b" % re.escape(word), ln):
            out.append({"uri": uri, "range": {"start": {"line": i, "character": m.start()},
                                              "end": {"line": i, "character": m.end()}}})
    return out


def definition(uri, text, word):
    uri = vscode_uri(uri)
    for i, ln in enumerate(text.split("\n")):
        m = re.search(r"^\s*(?:def|class)\s+(%s)\b|^\s*(%s)\s*=" % (re.escape(word), re.escape(word)), ln)
        if m:
            start = m.start(1) if m.group(1) else m.start(2)
            return [{"uri": uri, "range": {"start": {"line": i, "character": start},
                                           "end": {"line": i, "character": start + len(word)}}}]
    return []


def symbols(text):
    out = []
    for i, ln in enumerate(text.split("\n")):
        m = re.match(r"^(\s*)(def|class)\s+(\w+)", ln)
        if m:
            out.append({"name": m.group(3), "kind": 5 if m.group(2) == "class" else 12,
                        "range": {"start": {"line": i, "character": 0}, "end": {"line": i, "character": len(ln)}},
                        "selectionRange": {"start": {"line": i, "character": len(m.group(1)) + len(m.group(2)) + 1},
                                           "end": {"line": i, "character": len(ln)}},
                        "children": []})
    return out


def diagnostics(uri, text):
    diags = []
    for i, ln in enumerate(text.split("\n")):
        if "TODO" in ln:
            diags.append({"range": {"start": {"line": i, "character": 0}, "end": {"line": i, "character": len(ln)}},
                          "severity": 2, "message": "a TODO was left here", "source": "fake-lsp"})
    send({"method": "textDocument/publishDiagnostics", "params": {"uri": uri, "diagnostics": diags}})


def main():
    hovered = [0]
    if GRANDCHILD:
        import subprocess
        # "inherit": the grandchild keeps the server's stdout/stderr (the verifier's deadlock
        # construction); "detach": it holds none of the server's pipes. stdin is never inherited.
        quiet = {} if GRANDCHILD == "inherit" else {
            "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        child = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(300)"],
                                 stdin=subprocess.DEVNULL, **quiet)
        if PIDFILE:
            with open(PIDFILE, "w", encoding="utf-8") as fh:
                fh.write(str(child.pid))
    while True:
        msg = read()
        if msg is None:
            return 0
        method = msg.get("method")
        rid = msg.get("id")
        params = msg.get("params") or {}
        if method is None:
            continue                                    # a response to one of our requests
        log(method)
        if CRASH_ON and method == CRASH_ON:
            os._exit(3)
        if method == "initialize":
            reply(rid, {"capabilities": {"hoverProvider": True, "definitionProvider": True,
                                         "referencesProvider": True, "documentSymbolProvider": True,
                                         "textDocumentSync": 1}})
            continue
        if method == "initialized":
            threading.Thread(target=index_pass, daemon=True).start()
            continue
        if method == "shutdown":
            if not IGNORE_SHUTDOWN:
                reply(rid, None)
            continue
        if method == "exit":
            if IGNORE_SHUTDOWN:
                continue
            return 0
        if method == "$/cancelRequest":
            cid = params.get("id")
            if cid in SLOW_WAITING:
                reply(cid, error={"code": -32800, "message": "cancelled"})
                SLOW_WAITING.pop(cid, None)
            continue
        if method == "textDocument/didOpen":
            td = params["textDocument"]
            DOCS[td["uri"]] = td["text"]
            diagnostics(td["uri"], td["text"])
            continue
        if method == "textDocument/didClose":
            DOCS.pop(params["textDocument"]["uri"], None)
            continue
        if SLOW and method == SLOW:
            SLOW_WAITING[rid] = True
            continue
        if method in ("textDocument/rename", "textDocument/codeAction", "textDocument/formatting",
                      "workspace/executeCommand", "textDocument/completion"):
            # The proxy must never let these through; if one arrives, answer so the test can see it.
            reply(rid, {"changes": {}})
            continue
        if method in ("textDocument/definition", "textDocument/references", "textDocument/hover"):
            uri = params["textDocument"]["uri"]
            text = DOCS.get(uri, "")
            pos = params["position"]
            word = word_at(text, pos["line"], pos["character"])
            if method == "textDocument/definition":
                reply(rid, definition(uri, text, word))
            elif method == "textDocument/references":
                out = []
                for u, t in DOCS.items():
                    out.extend(occurrences(u, t, word))
                reply(rid, out)
            else:
                hovered[0] += 1
                if APPLY_EDIT and hovered[0] == 1:
                    request("workspace/applyEdit", {"label": "fake edit", "edit": {"changes": {
                        uri: [{"range": {"start": {"line": 0, "character": 0},
                                         "end": {"line": 0, "character": 0}}, "newText": "# edited\n"}]}}})
                reply(rid, {"contents": {"kind": "markdown", "value": f"```python\n(function) {word}\n```"}}
                      if word else None)
            continue
        if method == "textDocument/documentSymbol":
            reply(rid, symbols(DOCS.get(params["textDocument"]["uri"], "")))
            continue
        if rid is not None:
            reply(rid, error={"code": -32601, "message": f"{method} not supported"})


if __name__ == "__main__":
    sys.exit(main())
