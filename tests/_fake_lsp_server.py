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

Call hierarchy (0.0.28 G) — advertised by default, answered over every `*.py` file of the
workspace (read from disk under `rootUri`, an open document's text winning): prepare names every
`def` of the word at the position (two defs of one name = an overload), incoming = the defs whose
bodies call it, outgoing = the defs its body calls. Every item carries `data` = {"token": DATA};
an incoming/outgoing request whose item does not carry that exact `data` back is an error, so a
green round trip proves the item went back untouched.
  --no-call-hierarchy   advertise no `callHierarchyProvider` (and refuse the methods)
  --data TOKEN          the `data` token items carry (default "fake-data-7f3a")
  --hub N               incomingCalls answers N callers, each a distinct item in --hub-file
  --hub-file REL        the caller file of --hub (repo-relative; its line i % lines is caller i)
  --malformed MODE      `entries`: incomingCalls mixes hostile entries with one good one;
                        `notlist`: incomingCalls answers an object; `prepare`: prepare mixes
                        garbage items with a good one; `error`: incomingCalls answers error -32603;
                        `baduri`: prepare AND incomingCalls each add one item whose URI does not parse,
                        and definition / references each add one such Location
  --outside PATH        the absolute path a hostile entry names outside the repo (`entries` mode)
  --delay NAME:SECONDS  answer incomingCalls / outgoingCalls about an item named NAME this late
  --frame N             answer prepare as one frame whose body is exactly N bytes (the frame cap)
  --headerless N        answer prepare with N bytes that never end a header (the frame cap)
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
NO_CALLS = "--no-call-hierarchy" in ARGS
DATA = _opt("--data", "fake-data-7f3a")
HUB = int(_opt("--hub", "0"))
HUB_FILE = _opt("--hub-file")
MALFORMED = _opt("--malformed")
OUTSIDE = _opt("--outside")
DELAY = _opt("--delay")
FRAME = int(_opt("--frame", "0"))            # reply to prepare as ONE frame of exactly this many bytes
HEADERLESS = int(_opt("--headerless", "0"))  # answer prepare with this many bytes and no header end
ROOT = [None]
KNOWN = {"--index-seconds", "--crash-on", "--slow", "--apply-edit", "--log", "--grandchild",
         "--pidfile", "--ignore-shutdown", "--no-call-hierarchy", "--data", "--hub", "--hub-file",
         "--malformed", "--outside", "--delay", "--frame", "--headerless"}
_FLAGS = {"--apply-edit", "--ignore-shutdown", "--no-call-hierarchy"}


def _check_knobs():                       # a stand-in fails LOUD on a knob it does not know (§7e)
    i = 0                                 # (run as the server only — importing a helper is not a launch)
    while i < len(ARGS):
        if ARGS[i] not in KNOWN:
            sys.stderr.write("fake-lsp: unknown knob %r\n" % ARGS[i])
            sys.exit(97)
        i += 1 if ARGS[i] in _FLAGS else 2

OUT = sys.stdout.buffer
IN = sys.stdin.buffer
LOCK = threading.Lock()
DOCS = {}
NEXT_ID = [1000]
SLOW_WAITING = {}
WORD = re.compile(r"[^\W\d]\w*")


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


# ---- call hierarchy -------------------------------------------------------------------------
def _uri_path(uri):
    from urllib.parse import unquote, urlsplit
    path = unquote(urlsplit(uri).path)
    m = re.match(r"^/([A-Za-z]:.*)$", path)
    return os.path.normpath(m.group(1) if m else path)


def _text_at(texts, path):
    """The text at `path`, matched the way the OS matches it: a Windows item URI spells the drive
    lowercase (`d%3A`) while `os.walk` spells it `D:` — a case-sensitive key would miss it."""
    want = os.path.normcase(path)
    for k, v in texts.items():
        if os.path.normcase(k) == want:
            return v
    return ""


def workspace_texts():
    """{path: text} for every .py under the root (open documents win)."""
    out = {}
    root = ROOT[0]
    if root:
        for d, dirs, files in os.walk(root):
            dirs[:] = [x for x in dirs if not x.startswith(".") and x != "node_modules"]
            for f in files:
                if f.endswith(".py"):
                    p = os.path.normpath(os.path.join(d, f))
                    try:
                        with open(p, encoding="utf-8", newline="") as fh:
                            out[p] = fh.read()
                    except (OSError, UnicodeDecodeError):
                        pass
    for uri, text in DOCS.items():
        out[_uri_path(uri)] = text
    return out


def _lines(text):
    return [ln[:-1] if ln.endswith("\r") else ln for ln in text.split("\n")]


def defs_in(path, text):
    """[(name, def line, name col, end line exclusive)] — a def's body runs until the next line
    indented at or left of it."""
    lines = _lines(text)
    out = []
    for i, ln in enumerate(lines):
        m = re.match(r"^(\s*)def\s+(\w+)", ln)
        if not m:
            continue
        ind = len(m.group(1))
        end = len(lines)
        for j in range(i + 1, len(lines)):
            if lines[j].strip() and len(lines[j]) - len(lines[j].lstrip()) <= ind:
                end = j
                break
        out.append((m.group(2), i, m.start(2), end))
    return out


def item(path, name, line, col, end):
    uri = vscode_uri("file://" + path.replace(os.sep, "/") if path.startswith("/")
                     else "file:///" + path.replace(os.sep, "/"))
    return {"name": name, "kind": 12, "uri": uri, "detail": "fake",
            "range": {"start": {"line": line, "character": 0}, "end": {"line": max(line, end - 1), "character": 10000}},
            "selectionRange": {"start": {"line": line, "character": col},
                               "end": {"line": line, "character": col + len(name)}},
            "data": {"token": DATA}}


def _all_defs():
    texts = workspace_texts()
    return texts, [(p, d) for p, t in texts.items() for d in defs_in(p, t)]


def prepare(uri, pos):
    texts, defs = _all_defs()
    text = DOCS.get(uri) or _text_at(texts, _uri_path(uri))
    word = word_at(text.replace("\r\n", "\n"), pos["line"], pos["character"])
    if not word:
        return None
    found = [item(p, n, l, c, e) for p, (n, l, c, e) in defs if n == word]
    if MALFORMED == "prepare" and found:
        return [42, {"name": word}, {"name": 7, "uri": found[0]["uri"]},
                dict(found[0], range={"start": {"line": True, "character": 0}, "end": {"line": 1, "character": 0}}),
                found[0]]
    if MALFORMED == "baduri" and found:
        # a URI `urlsplit` cannot parse (an unclosed IPv6 authority): malformed, counted
        return [dict(found[0], uri="file://[abc/x.py")] + found
    return found or None


def send_frame(rid, result, size):
    """One reply frame whose body is EXACTLY `size` bytes (padded inside the JSON)."""
    head = json.dumps({"jsonrpc": "2.0", "id": rid, "result": result, "pad": ""}).encode("utf-8")
    pad = size - len(head)
    if pad < 0:
        raise SystemExit("fake-lsp: --frame %d is smaller than the reply itself" % size)
    body = head[:-2] + b"x" * pad + head[-2:]
    with LOCK:
        OUT.write(b"Content-Length: %d\r\n\r\n" % len(body))
        OUT.write(body)
        OUT.flush()


def calls_in_body(text, line, end, name=None):
    """[(callee, line, start, stop)] for calls inside lines (line, end)."""
    out = []
    for i, ln in enumerate(_lines(text)[line + 1:end], start=line + 1):
        for m in re.finditer(r"\b(\w+)\s*\(", ln):
            if name is None or m.group(1) == name:
                out.append((m.group(1), i, m.start(1), m.end(1)))
    return out


def _rng(i, a, b):
    return {"start": {"line": i, "character": a}, "end": {"line": i, "character": b}}


def hub_calls():
    path = os.path.normpath(os.path.join(ROOT[0], *HUB_FILE.split("/")))
    n_lines = 0
    with open(path, "rb") as fh:
        for n_lines, _ in enumerate(fh, start=1):
            pass
    n_lines = max(1, n_lines)
    out = []
    for i in range(HUB):
        it = item(path, "caller_%d" % i, i % n_lines, 0, i % n_lines + 1)
        out.append({"from": it, "fromRanges": [_rng(i % n_lines, 0, 1)]})
    return out


def hostile(good):
    bad_uri = vscode_uri("file://" + OUTSIDE) if OUTSIDE else "file:///nowhere/x.py"
    rng = good["selectionRange"]
    out = [
        42, "string", None, {"fromRanges": []},
        {"from": {"name": "no-range", "uri": good["uri"]}, "fromRanges": []},
        {"from": dict(good, range={"start": {"line": "1", "character": 0}, "end": {"line": 2, "character": 0}}), "fromRanges": []},
        {"from": dict(good, selectionRange={"start": {"line": -1, "character": 0}, "end": {"line": 0, "character": 0}}), "fromRanges": []},
        {"from": dict(good, selectionRange={"start": {"line": False, "character": 0}, "end": {"line": 0, "character": 0}}), "fromRanges": []},
        {"from": good, "fromRanges": "not a list"},
        {"from": dict(good, name=["not", "a", "string"]), "fromRanges": []},
        {"from": dict(good, uri=12), "fromRanges": []},
        {"from": dict(good, range={"start": {"line": 2 ** 40, "character": 0}, "end": {"line": 2 ** 40, "character": 1}}), "fromRanges": []},
        # well-formed but OUTSIDE: shown, never read
        {"from": dict(good, name="outside_caller", uri=bad_uri), "fromRanges": [rng]},
        {"from": dict(good, name="unc_caller", uri="file://evilhost/share/x.py"), "fromRanges": [rng]},
        {"from": dict(good, name="unc_path_caller", uri="file:////evilhost/share/x.py"), "fromRanges": [rng]},
        {"from": dict(good, name="climb_caller", uri=good["uri"].rsplit("/", 1)[0] + "/../../../../../../etc/passwd"), "fromRanges": [rng]},
        {"from": dict(good, name="<script>alert(1)</script>" + "x" * (1 << 20)), "fromRanges": [rng, {"bad": 1}]},
    ]
    return out


def call_answer(method, it):
    if not isinstance(it, dict) or it.get("data") != {"token": DATA}:
        return None, {"code": -32602, "message": "the item came back without its data"}
    if DELAY:
        name, _, secs = DELAY.partition(":")
        if it.get("name") == name:
            time.sleep(float(secs))
    if method == "callHierarchy/incomingCalls":
        if MALFORMED == "error":
            return None, {"code": -32603, "message": "SERVER-SECRET internal failure text"}
        if MALFORMED == "notlist":
            return {"not": "a list"}, None
        if HUB:
            return hub_calls(), None
    texts, defs = _all_defs()
    out = []
    if method == "callHierarchy/incomingCalls":
        for p, (n, l, c, e) in defs:
            hits = calls_in_body(texts[p], l, e, it["name"])
            if hits:
                out.append({"from": item(p, n, l, c, e), "fromRanges": [_rng(i, a, b) for _x, i, a, b in hits]})
        if MALFORMED == "baduri" and out:
            out = out + [{"from": dict(out[0]["from"], name="bad_uri_caller", uri="file://[abc/y.py"),
                          "fromRanges": out[0]["fromRanges"]}]
        if MALFORMED == "entries":
            good = out[0]["from"] if out else it
            out = [out[0]] + hostile(good) if out else hostile(good)
        return out, None
    path = _uri_path(it["uri"])
    text = _text_at(texts, path)
    line = it["selectionRange"]["start"]["line"]
    mine = [d for d in defs_in(path, text) if d[1] == line]
    if not mine:
        return [], None
    _n, l, _c, e = mine[0]
    by = {}
    for callee, i, a, b in calls_in_body(text, l, e):
        for p, (n, dl, dc, de) in defs:
            if n == callee:
                by.setdefault((p, dl), [item(p, n, dl, dc, de), []])[1].append(_rng(i, a, b))
    return [{"to": v[0], "fromRanges": v[1]} for v in by.values()], None


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
            ROOT[0] = _uri_path(params["rootUri"]) if params.get("rootUri") else None
            caps = {"hoverProvider": True, "definitionProvider": True,
                    "referencesProvider": True, "documentSymbolProvider": True,
                    "textDocumentSync": 1}
            if not NO_CALLS:
                caps["callHierarchyProvider"] = True
            reply(rid, {"capabilities": caps})
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
            bad = {"uri": "file://[abc/z.py", "range": _rng(0, 0, 1)}   # `baduri`: does not parse
            if method == "textDocument/definition":
                res = definition(uri, text, word)
                if MALFORMED == "baduri":
                    res = [bad] + (res if isinstance(res, list) else [res] if res else [])
                reply(rid, res)
            elif method == "textDocument/references":
                out = [bad] if MALFORMED == "baduri" else []
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
        if method in ("textDocument/prepareCallHierarchy", "callHierarchy/incomingCalls",
                      "callHierarchy/outgoingCalls") and not NO_CALLS:
            if method == "textDocument/prepareCallHierarchy":
                found = prepare(params["textDocument"]["uri"], params["position"])
                if FRAME:
                    send_frame(rid, found, FRAME)
                elif HEADERLESS:
                    with LOCK:
                        OUT.write(b"x" * HEADERLESS)          # no `\r\n\r\n`, ever
                        OUT.flush()
                else:
                    reply(rid, found)
            else:
                def answer(rid=rid, method=method, it=params.get("item")):
                    res, err = call_answer(method, it)
                    reply(rid, res, err)
                if DELAY:
                    threading.Thread(target=answer, daemon=True).start()
                else:
                    answer()
            continue
        if method == "textDocument/documentSymbol":
            reply(rid, symbols(DOCS.get(params["textDocument"]["uri"], "")))
            continue
        if rid is not None:
            reply(rid, error={"code": -32601, "message": f"{method} not supported"})


if __name__ == "__main__":
    _check_knobs()
    sys.exit(main())
