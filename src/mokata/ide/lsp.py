"""Read-only LSP bridge (0.0.27 P24 `ID-LSP-READONLY-EARLIER`, research ruling R3; doc 109 §3.3,
§6, §8, §14, §18.2). The IDE talks to the PROJECT'S OWN language servers — never one of its own
(§8: no LSP implementation, no parser) — and only READS from them.

WHAT IT OFFERS: hover, go-to-definition, find references, document symbols, the call hierarchy
(who calls this / what this calls, one level per question — 0.0.28 G), and the diagnostics a
server publishes (display only). `pyright` / `basedpyright` / `pylsp` for Python,
`typescript-language-server` for TS/JS, `gopls` for Go, `rust-analyzer` for Rust, and a
map the USER may extend in `~/.mokata/lsp.json`. A repo's files never choose a user-configured
binary, any argument, or `node`; a repo MAY supply its own copy of a BUILT-IN server (its venv, its
contained `node_modules`, a PATH entry the user put inside it) — the trusted case, shown in the
trust prompt's exact command lines.

NEVER AN EDIT. `Server.send` holds an ALLOWLIST of LSP methods (`ALLOWED`): a rename, a code action,
formatting, `workspace/executeCommand`, completion — anything not listed — is refused HERE, before
a byte reaches the server (`MethodRefused`). A server that asks the client to apply an edit
(`workspace/applyEdit`) is answered "not applied" and the refusal is counted in the status.

TRUST. A language server executes project code (rust-analyzer runs `build.rs`; gopls runs Go
tooling; tsserver loads plugins; pyright follows the config to the venv). The `Host` starts NOTHING
until `ide/trust.py` says the workspace is trusted — a human's P2-gated, ledgered decision. Until
then, and while a server indexes, the IDE answers from the graph layer it already has (`nav.run`),
and says so (`source: "graph"`).

PROCESSES. One server per language per workspace, started on demand (an explicit `start`, never
a GET), stopped after `IDLE_S` without a question, killed with the IDE. No console window on
Windows (`quiet_spawn.hidden`). Binaries come from the project's venv, its
`node_modules/<pkg>/package.json` `bin` (run as `node <script>`, never a `.bin`/`.cmd` shim), or the
user's PATH (absolute entries only, never the cwd); a missing one is NAMED with its install line
and never installed by mokata. A crash restarts the server at the next ask (at most `MAX_CRASHES`
within `CRASH_WINDOW_S`, then it waits for a human Start) and the UI is told. Indexing is a
visible JOB (`lsp.index`, progress from `$/progress`), cancellable — Cancel stops the server. A
query has NO deadline: it runs as an `lsp.query` job and shows as pending with Cancel; cancelling
sends `$/cancelRequest`.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import hashlib
import json
import ntpath
import os
import posixpath
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import unquote, urlsplit

from .. import __version__
from ..errors import MokataError
from ..knowledge import cancel as C
from ..quiet_spawn import Jobbed, spawn_jobbed
from ..exe_path import _SHIMS, path_dirs, which_in, win_reserved_name
from ..fs_identity import refuse_if_inside, require_inside
from ..repo_paths import name_of, split_path

# ------------------------------------------------------------------------ the method allowlist
#: Client -> server. Everything else is refused at `Server.send`.
ALLOWED_REQUESTS = frozenset({
    "initialize", "shutdown",
    "textDocument/hover", "textDocument/definition", "textDocument/references",
    "textDocument/documentSymbol",
    # 0.0.28 G (ID-LSP-CALL-HIERARCHY): three READS. The item a prepare returns (and its opaque
    # `data`) goes back to the same server inside the same job; it never reaches the page.
    "textDocument/prepareCallHierarchy", "callHierarchy/incomingCalls",
    "callHierarchy/outgoingCalls",
})
ALLOWED_NOTIFICATIONS = frozenset({
    "initialized", "exit", "textDocument/didOpen", "textDocument/didClose", "$/cancelRequest",
})
ALLOWED = ALLOWED_REQUESTS | ALLOWED_NOTIFICATIONS
#: Named so the refusal can say WHY, not only that: these are the write-shaped methods.
WRITE_METHODS = frozenset({
    "textDocument/rename", "textDocument/prepareRename", "textDocument/codeAction",
    "codeAction/resolve", "textDocument/formatting", "textDocument/rangeFormatting",
    "textDocument/onTypeFormatting", "workspace/executeCommand", "workspace/applyEdit",
    "textDocument/completion", "completionItem/resolve", "textDocument/didChange",
    "textDocument/willSave", "textDocument/didSave", "workspace/willRenameFiles",
    "workspace/didRenameFiles", "workspace/willCreateFiles", "workspace/willDeleteFiles",
    "textDocument/codeLens", "codeLens/resolve", "textDocument/inlayHint",
})
READ_ONLY = "mokata's LSP bridge is read-only"

IDLE_S = 600.0            # a server with no question for this long is shut down
MAX_CRASHES = 3           # restarts within CRASH_WINDOW_S before a human Start is needed
CRASH_WINDOW_S = 60.0
#: Per step of a stop: the `shutdown` answer, the exit after `exit`. Then the tree is ended through
#: the process door (`quiet_spawn.Jobbed.end`: the group while the leader is unreaped on POSIX, the
#: Job Object until it is EMPTY on Windows — bounded by `quiet_spawn.END_BOUND_S`, reported if
#: not). There is no fallback: a server Windows refuses a job for never starts (0.0.28 D2).
STOP_GRACE_S = 2.0
READ_POLL_S = 0.2         # the POSIX reader's select slice (it checks the stop flag between)
REAP_TICK_S = 5.0         # the reaper's tick: idle shutdown and the trust re-check run this often
STDERR_KEEP = 4096        # the tail of a server's stderr kept for its crash report
OPEN_DOCS_MAX = 32        # documents kept open on a server (LRU), bounded by the asker
NO_PROGRESS_GRACE_S = 3.0       # a server that reports no `$/progress` counts as indexed after this
CALLS_MAX = 2000                # call-hierarchy rows per expansion (all prepared items together)
CALLS_ITEMS_MAX = 20            # prepared items (overloads) asked about per expansion; more are said
CALL_LINE_MAX = 10000           # characters of a caller's line kept (snippet, column conversion)
CALL_LINE_BYTES = CALL_LINE_MAX * 4   # bytes read per line: CALL_LINE_MAX characters at UTF-8's widest
# The largest frame the reader accepts from a language server. LSP has no paging: a whole
# references / symbols / call-hierarchy reply is ONE frame, and the biggest real ones (tsserver
# project-wide references, pyright workspace symbols on a monorepo) are single-digit MB. 32 MiB
# keeps a 4x+ margin over those while bounding what one buggy or hostile server can make this
# process hold (the body is copied and parsed, ~3x its size). Over it, the server is ENDED with
# the reason — a refused frame cannot be skipped, the stream's framing is lost with it.
FRAME_MAX = 32 * 1024 * 1024
_POS_MAX = 2 ** 31 - 1          # an LSP position number past this is malformed (uinteger is 32-bit)
CALL_DIRECTIONS = {"in": "callHierarchy/incomingCalls", "out": "callHierarchy/outgoingCalls"}

# ------------------------------------------------------------------------ the languages
#: language -> {exts, id (LSP languageId), servers: [(binary, argv-tail, install-line)]}. The
#: first server found wins; `basedpyright` and `pylsp` are what a project may have instead.
BUILTIN_LANGUAGES: Dict[str, Dict[str, Any]] = {
    "python": {"exts": [".py", ".pyi"], "id": "python", "servers": [
        ["pyright-langserver", ["--stdio"], "pip install pyright  (or: npm i -g pyright)"],
        ["basedpyright-langserver", ["--stdio"], "pip install basedpyright"],
        ["pylsp", [], "pip install python-lsp-server"]]},
    "typescript": {"exts": [".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs"],
                   "id": None, "servers": [
        ["typescript-language-server", ["--stdio"],
         "npm i -D typescript typescript-language-server"]]},
    "go": {"exts": [".go"], "id": "go", "servers": [
        ["gopls", [], "go install golang.org/x/tools/gopls@latest"]]},
    "rust": {"exts": [".rs"], "id": "rust", "servers": [
        ["rust-analyzer", [], "rustup component add rust-analyzer"]]},
}
_TS_IDS = {".ts": "typescript", ".mts": "typescript", ".cts": "typescript", ".tsx": "typescriptreact",
           ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascriptreact"}
USER_CONFIG = os.path.join("~", ".mokata", "lsp.json")


class MethodRefused(MokataError, PermissionError):
    """A method outside the read-only allowlist was about to be sent — it was not."""


class ServerGone(MokataError, RuntimeError):
    """The language server exited (crashed, or was stopped) while a question was pending."""


class ServerError(MokataError, RuntimeError):
    """The server answered a JSON-RPC error."""


def languages() -> Dict[str, Dict[str, Any]]:
    """The server map: mokata's built-ins plus the USER's own `~/.mokata/lsp.json` (`{"languages":
    {name: {exts, id, servers: [[binary, [args], install]]}}}`), which may add a language or
    replace a built-in entry. A REPO never contributes (verification H1, ruled: a repo can never
    choose what executes) — there is no `.mokata/lsp.json`. A user entry is marked `user: True`:
    its binary is looked up on PATH only, never in the project's venv / node_modules. A bad file is
    ignored."""
    out = {k: {"exts": list(v["exts"]), "id": v["id"], "servers": [list(s) for s in v["servers"]],
               "user": False} for k, v in BUILTIN_LANGUAGES.items()}
    try:
        with open(os.path.expanduser(USER_CONFIG), encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        return out
    langs = cfg.get("languages") if isinstance(cfg, dict) else None
    if not isinstance(langs, dict):
        return out
    for name, spec in langs.items():
        if not isinstance(spec, dict) or not isinstance(spec.get("exts"), list) \
                or not isinstance(spec.get("servers"), list):
            continue
        servers = []
        for s in spec["servers"]:
            if isinstance(s, list) and s and isinstance(s[0], str):
                args = [a for a in (s[1] if len(s) > 1 and isinstance(s[1], list) else [])
                        if isinstance(a, str)]
                servers.append([s[0], args, str(s[2]) if len(s) > 2 else ""])
        if servers:
            out[str(name)] = {"exts": [str(e) for e in spec["exts"]],
                              "id": spec.get("id") if isinstance(spec.get("id"), str) else name,
                              "servers": servers, "user": True}
    return out


def language_of(rel: str) -> Optional[str]:
    ext = os.path.splitext(rel)[1].lower()
    for name, spec in languages().items():
        if ext in spec["exts"]:
            return name
    return None


def language_id(language: str, rel: str, spec: Dict[str, Any]) -> str:
    if language == "typescript":
        return _TS_IDS.get(os.path.splitext(rel)[1].lower(), "typescript")
    return spec.get("id") or language


def _project_dirs(root: str) -> List[str]:
    """Where a project keeps its own PYTHON tools: its venv(s). (Node servers come from
    `node_modules/<pkg>/package.json` `bin` — see `_node_package_bin` — never `.bin` shims.)"""
    bins = []
    venv = os.environ.get("VIRTUAL_ENV")
    for base in ([venv] if venv else []) + [os.path.join(root, d) for d in (".venv", "venv")]:
        bins.append(os.path.join(base, "Scripts" if os.name == "nt" else "bin"))
    return bins


def find_binary(root: str, name: str, project: bool = True) -> Optional[str]:
    """The project's own venv copy first (that is what trust is for), then the user's PATH
    (`path_dirs`: absolute entries, never the cwd). Never installed; never a `.cmd`/`.bat` shim.
    `project=False` (a user-configured server) looks at PATH only, and refuses any hit inside the
    repo: the project may supply a copy of a BUILT-IN server, not a binary of a name the user
    chose."""
    if project:
        hit = which_in(name, [d for d in _project_dirs(root) if os.path.isdir(d)])
        if hit:
            return hit
    return which_in(name, path_dirs(cwd=root), refuse_under=None if project else root)


def node_on_path(root: str, environ: Optional[Dict[str, str]] = None) -> Optional[str]:
    """`node` from the user's PATH — never the cwd, never a copy inside the repo (R2).
    `environ` is the environment whose PATH is read (default: this process's)."""
    return which_in("node", path_dirs(environ, cwd=root), refuse_under=root)


#: Built-in server name -> the npm package that ships it (`package.json` `bin`).
NODE_PACKAGES = {"typescript-language-server": "typescript-language-server",
                 "pyright-langserver": "pyright", "basedpyright-langserver": "basedpyright"}


def _package_bin(modules: str, anchor: str, root: str, name: str, pkg: Optional[str] = None,
                 environ: Optional[Dict[str, str]] = None) -> Tuple[Optional[List[str]], str]:
    """`<modules>/<pkg>/package.json` `bin[name]` as `[node, script]`, without a shim and without
    a shell (re-verification R3, ruled). Containment holds on REALPATHS, twice (verify2 N2):
    `modules` itself must sit inside `anchor` (the repo root for a project install — so a
    committed `node_modules -> /anywhere` symlink is refused), and the script must sit inside
    `modules` — pnpm's `.pnpm` store and yarn v1's hoisted tree are inside it; a package
    directory symlinked out of it, or a `bin` that climbs out (`../../..`), is refused (L4). node
    comes from the user's PATH (`node_on_path`). `.cmd` files are never read."""
    pkg = pkg or NODE_PACKAGES.get(name)       # `pkg`: a non-server tool (npm, corepack…) by name
    if pkg is None:
        return None, ""
    if os.path.isdir(modules) and not require_inside(modules, anchor):
        return None, (f"{modules} resolves outside {anchor} ({os.path.realpath(modules)}) — "
                      f"refused")
    pkg_dir = os.path.join(modules, *pkg.split("/"))
    try:
        with open(os.path.join(pkg_dir, "package.json"), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None, ""
    bins = data.get("bin") if isinstance(data, dict) else None
    rel = bins if isinstance(bins, str) else (bins.get(name) if isinstance(bins, dict) else None)
    if not isinstance(rel, str) or not rel:
        return None, f"{pkg}/package.json names no bin {name!r}"
    script = os.path.realpath(os.path.join(os.path.realpath(pkg_dir),
                                           *split_path(rel)))
    if not require_inside(script, modules) or not os.path.isfile(script):
        return None, (f"{pkg}/package.json: bin {rel!r} resolves outside {modules} "
                      f"({script}) — refused")
    node = node_on_path(root, environ)
    if not node:
        return None, f"{pkg} is installed in {modules} but node is not on PATH"
    return [node, script], ""


def _node_package_bin(root: str, name: str) -> Tuple[Optional[List[str]], str]:
    """The project's own node server: `<root>/node_modules/<pkg>` (npm, pnpm, yarn v1)."""
    return _package_bin(os.path.join(root, "node_modules"), root, root, name)


def _global_module_dirs(d: str) -> List[Tuple[str, str]]:
    """`(node_modules dir, containment anchor)` for each global-install layout a PATH entry `d`
    can be the bin directory of: npm (`<d>/node_modules`), pnpm (`<d>/global/<layout n>/
    node_modules`, `PNPM_HOME` on PATH) and yarn v1 (`…/Yarn/bin` → `…/Yarn/Data/global/
    node_modules`)."""
    out = [(os.path.join(d, "node_modules"), d)]
    try:
        layouts = sorted((n for n in os.listdir(os.path.join(d, "global")) if n.isdigit()),
                         key=int, reverse=True)
    except OSError:
        layouts = []
    out += [(os.path.join(d, "global", n, "node_modules"), d) for n in layouts]
    parent = os.path.dirname(os.path.normpath(d))
    if os.path.basename(os.path.normpath(d)).lower() == "bin" \
            and os.path.basename(parent).lower() == "yarn":
        out.append((os.path.join(parent, "Data", "global", "node_modules"), parent))
    return out


def _global_node_package_bin(root: str, name: str, pkg: Optional[str] = None,
                             environ: Optional[Dict[str, str]] = None
                             ) -> Tuple[Optional[List[str]], str]:
    """A GLOBAL npm / pnpm / yarn v1 install the user made, found through PATH without reading its
    shim: each absolute PATH entry outside the repo is asked for the package in every global
    layout it can be the bin directory of (`_global_module_dirs`). (On POSIX the global bin is a
    symlink `which_in` already finds.)"""
    why = ""
    for d in path_dirs(environ, cwd=root):
        if refuse_if_inside(d, root):
            continue
        for modules, anchor in _global_module_dirs(d):
            head, w = _package_bin(modules, anchor, root, name, pkg, environ)
            if head is not None:
                return head, ""
            why = why or w
    return None, why


def _shim_only(root: str, name: str) -> Optional[str]:
    """The `name.cmd`/`.bat`/`.ps1` on the user's PATH when that is all there is of `name` — found
    to SAY so, never run."""
    for d in path_dirs(cwd=root):
        for ext in _SHIMS:
            cand = os.path.join(d, name + ext)
            if os.path.isfile(cand):
                return cand
    return None


def find_server(root: str, language: str) -> Dict[str, Any]:
    """`{found, binary, argv, name, candidates:[{name, install, found}]}` for one language — the
    exact command line trusting would allow. No element of `argv` ever reaches a shell. For a
    built-in name: the project's own copy (`node_modules/<pkg>` `bin`, the venv), else the SAME
    name on the user's PATH (a binary, or a global npm package) — never the next name first."""
    spec = languages().get(language)
    if spec is None:
        return {"found": False, "binary": None, "argv": None, "name": None, "candidates": [],
                "note": f"no language server is configured for {language!r} — add one to "
                        f"{USER_CONFIG}"}
    cands = []
    pick = None
    note = ""
    for name, tail, install in spec["servers"]:
        head: Optional[List[str]] = None
        user = bool(spec.get("user"))
        if not user:
            head, why = _node_package_bin(root, name)       # the project's own node package
            note = note or why
        if head is None:
            exe = find_binary(root, name, project=not user)
            head = [exe] if exe else None
        if head is None and not user:
            head, why = _global_node_package_bin(root, name)
            note = note or why
        cands.append({"name": name, "install": install, "found": head is not None})
        if head is not None and pick is None:
            pick = (name, [*head, *tail])
    if pick is None:
        for name, _tail, _install in spec["servers"]:
            shim = _shim_only(root, name)
            if shim is not None:          # found and NOT run, and why — never "not on PATH"
                note = (f"`{name}` was found only as {shim} — mokata never runs a .cmd/.bat/.ps1 "
                        f"(cmd.exe / PowerShell would parse the command line); name the server's "
                        f"real executable in {USER_CONFIG}")
                break
        return {"found": False, "binary": None, "argv": None, "name": None, "candidates": cands,
                "note": note or (f"no {language} language server on PATH, in the venv or node_modules"
                                 f" — install one yourself: "
                                 + " · ".join(c["install"] for c in cands if c["install"]))}
    return {"found": True, "binary": pick[1][0], "argv": pick[1], "name": pick[0], "candidates": cands}


def will_run(root: str) -> List[Dict[str, Any]]:
    """What trusting this workspace would allow to run, per language: the exact argv (found
    servers only) — shown in the trust prompt, the TTY code line and `mokata ide --trust`."""
    out = []
    for lang in languages():
        f = find_server(root, lang)
        if f["found"]:
            out.append({"language": lang, "server": f["name"], "argv": list(f["argv"])})
    return out


# ------------------------------------------------------------------------ positions and uris
def to_uri(path: str) -> str:
    return Path(os.path.abspath(path)).as_uri()


def from_uri(uri: str, nt: Optional[bool] = None) -> Optional[str]:
    """A `file:` URI's local path. A URI with a HOST (`file://evilhost/share/x`, a UNC path on
    Windows that would open SMB to that host) is not local: None.

    The path is percent-DECODED first, then read as a path. Real Windows servers (vscode-uri:
    typescript-language-server, pyright) spell a drive as `file:///c%3A/Users/x` — lower-case
    letter, escaped colon — and CPython 3.12's `url2pathname` looks for the drive BEFORE it
    unquotes, so it returned `\\c:\\Users\\x`, a path inside no repo. `nt` injects the platform
    for the tests. On Windows: `/c:/x`, `/C:/x` and `/c%3A/x` all give `C:\\x`; a path with no drive
    letter, or one that starts `//` (a UNC share spelled as a path), is not a local file: None."""
    is_nt = (os.name == "nt") if nt is None else nt
    parts = urlsplit(uri)
    if parts.scheme != "file" or (parts.netloc and parts.netloc != "localhost"):
        return None
    path = unquote(parts.path)
    if "\0" in path:
        return None
    if not is_nt:
        return posixpath.normpath(path)
    m = re.match(r"^[/\\]?([A-Za-z]):([/\\].*)?$", path)
    if not m:
        return None
    # A device or an alias Windows would read as another file (`src/CON`, `nul .py`, `COM\xb9`,
    # `a.py.`, `a.py `, `CONOUT$`) is no file of the repo: CPython 3.13's `_isreservedname`, ported.
    if any(win_reserved_name(c) for c in re.split(r"[/\\]", m.group(2) or "") if c):
        return None
    return ntpath.normpath(m.group(1).upper() + ":" + (m.group(2) or "/"))


def utf16_col(line: str, col1: int) -> int:
    """1-based character column -> LSP's 0-based UTF-16 unit offset."""
    head = line[:max(0, col1 - 1)]
    return len(head.encode("utf-16-le")) // 2


def char_col(line: str, units: int) -> int:
    """LSP's 0-based UTF-16 unit offset -> 1-based character column."""
    n = 0
    for i, ch in enumerate(line):
        if n >= units:
            return i + 1
        n += 2 if ord(ch) > 0xFFFF else 1
    return len(line) + 1


def source_text(path: str) -> str:
    """The file as the server should see it — bytes decoded, line endings KEPT (CRLF-safe: the
    server's positions must match the bytes on disk)."""
    with open(path, "rb") as fh:
        return fh.read().decode("utf-8", "replace")


# ------------------------------------------------------------------------ one server
class Server:
    """One language-server process and its JSON-RPC conversation (stdio). Thread-safe: any
    thread may ask; one reader thread dispatches."""

    def __init__(self, root: str, language: str, name: str, argv: Sequence[str],
                 on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.root, self.language, self.name, self.argv = os.path.abspath(root), language, name, list(argv)
        self.on_event = on_event or (lambda e: None)
        self.clock = clock
        self.proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._next = 1
        self._pending: Dict[int, Dict[str, Any]] = {}
        self._docs: "Dict[str, Dict[str, Any]]" = {}      # uri -> {version, sha, at}
        self.capabilities: Dict[str, Any] = {}
        self.diagnostics: Dict[str, List[Dict[str, Any]]] = {}
        self.progress: Dict[str, Dict[str, Any]] = {}      # token -> {title, message, percentage}
        self.progress_cycles = 0
        self.refused_edits = 0
        self.refused_methods: List[str] = []
        self.sent: List[str] = []                           # method names, for the tests' audit
        self.exit_code: Optional[int] = None
        self.started_at = self.clock()
        self.last_used = self.clock()
        self.answered = 0                                   # textDocument answers
        self.stopped = threading.Event()
        self._stderr = bytearray()
        self.stopping = False
        self._ended = False                                # the first `_gone` has claimed (W5)
        self.tree: Optional[Jobbed] = None
        self._stop_readers: Optional[threading.Event] = None

    # ---- lifecycle -----------------------------------------------------------------------
    def start(self) -> None:
        # Its OWN process group / session (verification M1): stopping kills the whole tree
        # (tsserver under typescript-language-server, proc-macro-srv under rust-analyzer, `go`
        # under gopls), not only the direct child. No shell is involved anywhere (M3).
        # The process door: Windows created suspended, put in the tree's Job Object, THEN resumed
        # — a launcher's first child is in the job too — or refused (`NO_JOB`, nothing ran); POSIX
        # its own session, signalled only while the leader is unreaped.
        self.tree = spawn_jobbed(
            self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=self.root, close_fds=True)   # own session / group, hidden console
        self.proc = self.tree.proc
        self._stop_readers = threading.Event()
        threading.Thread(target=self._read_loop, daemon=True,
                         name=f"mokata-lsp-{self.language}").start()
        threading.Thread(target=self._drain_stderr, daemon=True,
                         name=f"mokata-lsp-{self.language}-err").start()
        # R1: the PROCESS is watched, not only its pipe — a child that inherited stdout would
        # otherwise hide a crash until Cancel. `wait` returns the moment the leader dies.
        threading.Thread(target=self._watch_exit, daemon=True,
                         name=f"mokata-lsp-{self.language}-wait").start()
        params = {
            "processId": os.getpid(), "rootUri": to_uri(self.root),
            "workspaceFolders": [{"uri": to_uri(self.root), "name": os.path.basename(self.root)}],
            "clientInfo": {"name": "mokata ide (read-only)", "version": __version__},
            "capabilities": {
                "window": {"workDoneProgress": True},
                "workspace": {"workspaceFolders": True, "configuration": True,
                              "applyEdit": False},
                "textDocument": {
                    "hover": {"contentFormat": ["markdown", "plaintext"]},
                    "definition": {"linkSupport": False},
                    "references": {}, "documentSymbol": {"hierarchicalDocumentSymbolSupport": True},
                    "callHierarchy": {"dynamicRegistration": False},
                    "publishDiagnostics": {"relatedInformation": False},
                    "synchronization": {"didSave": False, "willSave": False}},
            },
        }
        res = self.request("initialize", params)
        self.capabilities = (res or {}).get("capabilities", {}) if isinstance(res, dict) else {}
        self.notify("initialized", {})

    @property
    def alive(self) -> bool:
        return self.tree is not None and self.tree.poll_exit() is None and not self.stopped.is_set()

    def stop(self, why: str = "stopped") -> None:
        """`shutdown` → `exit` → the process TREE is ended if it lingers. Two STOP_GRACE_S steps,
        then the door's bounded end (verification H2, L6): no caller — revoke, IDE shutdown, idle
        reap, a cancelled index job — ever waits on the server's pipes. Pending questions end
        with ServerGone."""
        self.stopping = True
        tree = self.tree
        if tree is None:
            return
        try:
            if tree.poll_exit() is None:
                try:
                    self.request("shutdown", None, wait=STOP_GRACE_S)
                except (ServerGone, ServerError, TimeoutError):
                    pass
                try:
                    self.notify("exit", None)
                except ServerGone:
                    pass
                try:
                    tree.wait_exit(STOP_GRACE_S)
                except subprocess.TimeoutExpired:
                    pass
        finally:
            self.kill(why)

    def kill(self, why: str = "killed") -> None:
        """Kill the process group / tree and return — the readers are told to stop and own the
        closing of their pipes (closing a pipe from here would take a lock a blocked reader holds;
        a grandchild that inherited the pipe would make that wait for ever). A kill is a
        DECISION (Cancel, a failed start, a stop) — reported `stopped`, never `crashed`, so it
        neither counts toward the crash loop nor restarts at the next ask (verify2 N4)."""
        self.stopping = True
        self._gone(why)

    def _watch_exit(self) -> None:
        tree = self.tree
        if tree is None:
            return
        try:
            tree.wait_exit()                  # never reaps: the tree is ended while unreaped
        except OSError:
            return
        self._gone("exited")

    def _gone(self, why: str) -> None:
        """The server is over (stopped, killed or crashed): its whole tree is killed — on the
        crash path too (R1) — pending questions fail with ServerGone at once, the readers are
        told to stop. Idempotent: the first caller publishes the event, the rest only re-kill.

        The FIRST CALLER'S REASON is the answer (0.0.28 W5): it is claimed under the lock BEFORE
        the kill. The kill wakes the exit watcher, whose own `_gone("exited")` used to reach the
        lock first and answer a frame-cap refusal as "exited (exit -9)" — the reason the reader
        decided on lost to the signal it sent. A later caller only re-kills; it never fails the
        questions, and the questions are failed before `stopped` is set, so a waiting `request`
        never sees `stopped` without its answer."""
        proc = self.proc
        with self._lock:
            first = not self._ended
            self._ended = True
        # The tree is ended ONCE, through its door handle (verify2 N1; 0.0.28 R3): on POSIX the
        # group only while the leader is unreaped, then the id is forgotten; on Windows the Job
        # Object until it is empty. A later stop / kill / reap signals nothing.
        if self.tree is not None:
            self.tree.close()                 # end() (idempotent) + release the job handle
        if not first:
            return
        with self._lock:
            if proc is not None:
                self.exit_code = proc.returncode
            pend, self._pending = self._pending, {}
            exit_note = f" (exit {self.exit_code})" if self.exit_code is not None else ""
            for p in pend.values():
                p["error"] = ServerGone(f"{self.name} {why}{exit_note}")
                p["done"].set()
            self.stopped.set()
        if self._stop_readers is not None:
            self._stop_readers.set()
        if self.proc is not None and self.proc.stdin is not None:
            try:
                self.proc.stdin.close()                   # ours alone: no reader holds it
            except (OSError, ValueError):
                pass
        self.on_event({"kind": "lsp", "what": "stopped" if self.stopping else "crashed",
                       "language": self.language, "server": self.name,
                       "exit_code": self.exit_code, "stderr": self.stderr_tail()})

    def stderr_tail(self) -> str:
        return bytes(self._stderr[-STDERR_KEEP:]).decode("utf-8", "replace")

    # ---- the wire -----------------------------------------------------------------------
    def send(self, msg: Dict[str, Any]) -> None:
        """THE door. Refuses any method outside the allowlist before writing a byte."""
        method = msg.get("method")
        if method is not None:
            if method not in ALLOWED:
                with self._lock:
                    self.refused_methods.append(method)
                kind = "a write" if method in WRITE_METHODS else "not a read the bridge offers"
                raise MethodRefused(f"{method}: refused — {READ_ONLY} ({kind})")
            with self._lock:
                self.sent.append(method)
        proc = self.proc
        if proc is None or proc.stdin is None or self.stopped.is_set():
            raise ServerGone(f"{self.name} is not running")
        body = json.dumps({"jsonrpc": "2.0", **msg}, ensure_ascii=False).encode("utf-8")
        frame = b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
        with self._lock:
            try:
                proc.stdin.write(frame)
                proc.stdin.flush()
            except (OSError, ValueError) as exc:
                raise ServerGone(f"{self.name}: its stdin closed ({exc})") from exc

    def notify(self, method: str, params: Any) -> None:
        self.send({"method": method, "params": params})

    def request(self, method: str, params: Any, wait: Optional[float] = None) -> Any:
        """Ask and wait. NO deadline unless `wait` is given (only `shutdown` uses one): a slow
        server is a pending job with Cancel — a cancelled job sends `$/cancelRequest` and raises
        `Cancelled` through `knowledge.cancel`."""
        with self._lock:
            rid = self._next
            self._next += 1
            rec: Dict[str, Any] = {"done": threading.Event(), "result": None, "error": None,
                                   "method": method}
            self._pending[rid] = rec
        try:
            self.send({"id": rid, "method": method, "params": params})
        except MokataError:
            with self._lock:
                self._pending.pop(rid, None)
            raise
        self.last_used = self.clock()
        deadline = None if wait is None else self.clock() + wait
        while not rec["done"].wait(0.05):
            if self.stopped.is_set() and not rec["done"].is_set():
                with self._lock:
                    self._pending.pop(rid, None)
                raise ServerGone(f"{self.name} crashed with this question pending")
            if C.cancelled():
                with self._lock:
                    self._pending.pop(rid, None)
                try:
                    self.notify("$/cancelRequest", {"id": rid})
                except ServerGone:
                    pass
                raise C.Cancelled("cancelled")
            if deadline is not None and self.clock() > deadline:
                with self._lock:
                    self._pending.pop(rid, None)
                raise TimeoutError(method)
        self.last_used = self.clock()
        if rec["error"] is not None:
            raise rec["error"]
        return rec["result"]

    def _chunks(self, stream: Any) -> Any:
        """The pipe's bytes, NON-BLOCKING with respect to the stop flag: on POSIX a `select` of
        READ_POLL_S between reads; on Windows (no select on pipes) a blocking `read1` whose
        thread is a daemon that nobody joins — the tree kill makes EOF arrive. Either way the
        caller of stop/kill never waits on this thread (verification H2)."""
        fd = stream.fileno()
        stop = self._stop_readers
        if os.name != "nt":
            import select
            while not stop.is_set():
                try:
                    ready, _, _ = select.select([fd], [], [], READ_POLL_S)
                except (OSError, ValueError):
                    return
                if not ready:
                    if self.tree is not None and self.tree.poll_exit() is not None:
                        return                        # the process is gone: EOF, pipe or no pipe
                    continue
                try:
                    chunk = os.read(fd, 65536)
                except OSError:
                    return
                if not chunk:
                    return
                yield chunk
            return
        while not stop.is_set():
            chunk = stream.read1(65536) if hasattr(stream, "read1") else stream.read(65536)
            if not chunk:
                return
            yield chunk

    def _read_loop(self) -> None:
        proc = self.proc
        out = proc.stdout if proc is not None else None
        buf = bytearray()
        why = "exited"
        try:
            for chunk in (self._chunks(out) if out is not None else ()):
                buf.extend(chunk)
                while True:
                    head_end = buf.find(b"\r\n\r\n")
                    if head_end < 0:
                        if len(buf) > FRAME_MAX:             # a header that never ends
                            why = (f"sent {len(buf)} bytes with no frame header (over the "
                                   f"{FRAME_MAX}-byte frame cap)")
                        break
                    length = None
                    for line in bytes(buf[:head_end]).split(b"\r\n"):
                        k, _, v = line.decode("ascii", "replace").partition(":")
                        if k.strip().lower() == "content-length":
                            try:
                                length = int(v.strip())
                            except ValueError:
                                length = None
                    if length is None:                       # a frame with no length: skip it
                        del buf[:head_end + 4]
                        continue
                    if length > FRAME_MAX or length < 0:
                        why = (f"sent a frame of {length} bytes (over the {FRAME_MAX}-byte "
                               "frame cap)")
                        break
                    if len(buf) < head_end + 4 + length:
                        break
                    body = bytes(buf[head_end + 4:head_end + 4 + length])
                    del buf[:head_end + 4 + length]
                    try:
                        msg = json.loads(body.decode("utf-8", "replace"))
                    except ValueError:
                        continue
                    self._dispatch(msg)
                if why != "exited":
                    break
        except (OSError, ValueError):
            pass
        finally:
            try:
                if out is not None:
                    out.close()                               # the reader owns its pipe
            except (OSError, ValueError):
                pass
            if why == "exited" and self.tree is not None and not self.stopped.is_set():
                try:
                    self.tree.wait_exit(STOP_GRACE_S)         # EOF: it exited (or is exiting)
                except subprocess.TimeoutExpired:
                    pass
            self._gone(why)                                   # an over-cap server is ENDED here

    def _drain_stderr(self) -> None:
        proc = self.proc
        err = proc.stderr if proc is not None else None
        try:
            for chunk in (self._chunks(err) if err is not None else ()):
                self._stderr.extend(chunk)
                if len(self._stderr) > 4 * STDERR_KEEP:
                    del self._stderr[:-STDERR_KEEP]
        except (OSError, ValueError):
            pass
        finally:
            try:
                if err is not None:
                    err.close()
            except (OSError, ValueError):
                pass

    def _dispatch(self, msg: Dict[str, Any]) -> None:
        if not isinstance(msg, dict):
            return
        if "method" in msg:
            if "id" in msg:
                self._server_request(msg)
            else:
                self._notification(msg)
            return
        rid = msg.get("id")
        with self._lock:
            rec = self._pending.pop(rid, None) if isinstance(rid, int) else None
        if rec is None:
            return
        if "error" in msg and msg["error"] is not None:
            e = msg["error"]
            rec["error"] = ServerError(f"{self.name}: {e.get('message', 'error')} "
                                       f"(code {e.get('code')})" if isinstance(e, dict) else str(e))
        else:
            rec["result"] = msg.get("result")
            if rec["method"].startswith("textDocument/"):
                self.answered += 1                       # it answers: usable, whatever it indexes
        rec["done"].set()

    def _reply(self, rid: Any, result: Any = None, error: Optional[Dict[str, Any]] = None) -> None:
        msg: Dict[str, Any] = {"id": rid}
        if error is not None:
            msg["error"] = error
        else:
            msg["result"] = result
        try:
            self.send(msg)
        except ServerGone:
            pass

    def _server_request(self, msg: Dict[str, Any]) -> None:
        method, rid, params = msg["method"], msg["id"], msg.get("params") or {}
        if method == "workspace/applyEdit":
            with self._lock:
                self.refused_edits += 1
            self._reply(rid, {"applied": False, "failureReason": READ_ONLY})
            self.on_event({"kind": "lsp", "what": "edit-refused", "language": self.language,
                           "server": self.name, "count": self.refused_edits})
        elif method in ("window/workDoneProgress/create", "client/registerCapability",
                        "client/unregisterCapability", "window/showMessageRequest",
                        "window/showDocument"):
            self._reply(rid, {"success": False} if method == "window/showDocument" else None)
        elif method == "workspace/configuration":
            items = params.get("items") if isinstance(params, dict) else None
            self._reply(rid, [None] * (len(items) if isinstance(items, list) else 1))
        elif method == "workspace/workspaceFolders":
            self._reply(rid, [{"uri": to_uri(self.root), "name": os.path.basename(self.root)}])
        else:
            self._reply(rid, error={"code": -32601, "message": f"{method}: not offered — {READ_ONLY}"})

    def _notification(self, msg: Dict[str, Any]) -> None:
        method, params = msg["method"], msg.get("params") or {}
        if method == "textDocument/publishDiagnostics" and isinstance(params, dict):
            uri = params.get("uri")
            if isinstance(uri, str):
                diags = params.get("diagnostics")
                self.diagnostics[uri] = diags if isinstance(diags, list) else []
                self.on_event({"kind": "lsp", "what": "diagnostics", "language": self.language,
                               "server": self.name, "uri": uri, "count": len(self.diagnostics[uri])})
        elif method == "$/progress" and isinstance(params, dict):
            token, value = str(params.get("token")), params.get("value") or {}
            kind = value.get("kind") if isinstance(value, dict) else None
            if kind == "begin":
                self.progress[token] = {"title": str(value.get("title") or ""),
                                        "message": str(value.get("message") or ""),
                                        "percentage": value.get("percentage")}
            elif kind == "report" and token in self.progress:
                if value.get("message") is not None:
                    self.progress[token]["message"] = str(value.get("message"))
                if value.get("percentage") is not None:
                    self.progress[token]["percentage"] = value.get("percentage")
            elif kind == "end":
                self.progress.pop(token, None)
                self.progress_cycles += 1

    # ---- documents ----------------------------------------------------------------------
    def open(self, abspath: str, language_id: str) -> Tuple[str, str]:
        """Make sure the server sees THIS file as it is on disk now. Returns (uri, text)."""
        text = source_text(abspath)
        sha = hashlib.sha1(text.encode("utf-8", "surrogatepass")).hexdigest()
        uri = to_uri(abspath)
        with self._lock:
            doc = self._docs.get(uri)
            version = doc["version"] if doc else 0
            same = doc is not None and doc["sha"] == sha
        if doc is not None and not same:
            self.notify("textDocument/didClose", {"textDocument": {"uri": uri}})
        if doc is None or not same:
            version += 1
            self.notify("textDocument/didOpen", {"textDocument": {
                "uri": uri, "languageId": language_id, "version": version, "text": text}})
        with self._lock:
            self._docs[uri] = {"version": version, "sha": sha, "at": self.clock()}
            extra = sorted(self._docs.items(), key=lambda kv: kv[1]["at"])[:-OPEN_DOCS_MAX]
            for u, _ in extra:
                del self._docs[u]
        for u, _ in extra:
            self.notify("textDocument/didClose", {"textDocument": {"uri": u}})
        return uri, text

    def is_ready(self) -> bool:
        """Past its first indexing: a progress pass ended, or it answered a document question,
        or `NO_PROGRESS_GRACE_S` passed with no progress begun (a server that reports none,
        e.g. pylsp). Never ready while a `$/progress` is active. A dead server is "ready" so that
        nothing waits on it."""
        if self.stopped.is_set():
            return True
        if self.progress:
            return False
        if self.progress_cycles > 0 or self.answered > 0:
            return True
        return self.clock() - self.started_at > NO_PROGRESS_GRACE_S

    def indexing(self) -> Optional[Dict[str, Any]]:
        """The active `$/progress` (the first begin wins the label), or None."""
        for tok, p in list(self.progress.items()):
            return {"token": tok, **p}
        return None

    def status(self) -> Dict[str, Any]:
        state = ("stopped" if self.stopped.is_set() and self.stopping else
                 "crashed" if self.stopped.is_set() else
                 "indexing" if not self.is_ready() else "ready")
        return {"language": self.language, "server": self.name, "binary": self.argv[0],
                "state": state, "pid": self.proc.pid if self.proc is not None else None,
                "exit_code": self.exit_code, "indexing": self.indexing(),
                "answered": self.answered, "open_documents": len(self._docs),
                "refused_edits": self.refused_edits, "refused_methods": list(self.refused_methods),
                "idle_s": round(self.clock() - self.last_used, 1)}


# ------------------------------------------------------------------------ the host
class Host:
    """The servers of ONE workspace: one per language, started only when `trusted()` says so and
    only through `start()` (an explicit act — never a GET), reaped when idle, restarted after a
    crash, all killed with the IDE."""

    def __init__(self, root: str, trusted: Callable[[], bool],
                 on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
                 clock: Callable[[], float] = time.monotonic, idle_s: float = IDLE_S,
                 finder: Callable[[str, str], Dict[str, Any]] = find_server) -> None:
        self.root = os.path.abspath(root)
        self.trusted = trusted
        self.on_event = on_event or (lambda e: None)
        self.clock = clock
        self.idle_s = idle_s
        self.finder = finder
        self._servers: Dict[str, Server] = {}
        self._crashes: Dict[str, List[float]] = {}
        self._restarts: Dict[str, int] = {}
        self._held: Dict[str, str] = {}                  # language -> why it waits for a human
        self._lock = threading.RLock()
        self._reaper: Optional[threading.Thread] = None
        self.index_jobs: Dict[str, Any] = {}

    # ---- starting -----------------------------------------------------------------------
    def start(self, language: str, by_human: bool = False) -> Dict[str, Any]:
        """Start (or restart) the server for `language`. Refused untrusted (`UntrustedWorkspace`
        in the answer, nothing spawned) and when no binary is found (named, with the install line
        — never installed). `by_human` clears a crash hold."""
        if not self.trusted():
            return {"started": False, "error": "UntrustedWorkspace",
                    "note": "this workspace is not trusted — a language server runs its code; "
                            "mark it trusted first", "language": language}
        found = self.finder(self.root, language)
        if not found.get("found"):
            return {"started": False, "error": "NoServer", "note": found.get("note", ""),
                    "language": language, "candidates": found.get("candidates", [])}
        with self._lock:
            if by_human:
                self._held.pop(language, None)
                self._crashes.pop(language, None)
            if language in self._held:
                return {"started": False, "error": "CrashLoop", "note": self._held[language],
                        "language": language}
            cur = self._servers.get(language)
            if cur is not None and cur.alive:
                return {"started": True, "already": True, "language": language,
                        "server": cur.status()}
            srv = Server(self.root, language, found["name"], found["argv"], self._event, self.clock)
            self._servers[language] = srv
            if cur is not None:
                self._restarts[language] = self._restarts.get(language, 0) + 1
        try:
            srv.start()
        except (OSError, ServerGone, ServerError, TimeoutError) as exc:
            srv.kill("failed to start")
            return {"started": False, "error": "StartFailed", "language": language,
                    "note": f"{found['name']} did not start: {exc}"[:500],
                    "stderr": srv.stderr_tail()}
        self._ensure_reaper()
        self._index_job(srv)
        self.on_event({"kind": "lsp", "what": "started", "language": language,
                       "server": srv.name, "pid": srv.proc.pid if srv.proc else None})
        return {"started": True, "already": False, "language": language, "server": srv.status()}

    def _event(self, evt: Dict[str, Any]) -> None:
        if evt.get("what") == "crashed":
            lang = evt["language"]
            with self._lock:
                now = self.clock()
                recent = [t for t in self._crashes.get(lang, []) if now - t < CRASH_WINDOW_S] + [now]
                self._crashes[lang] = recent
                if len(recent) >= MAX_CRASHES:
                    self._held[lang] = (f"{evt.get('server')} crashed {len(recent)} times in "
                                        f"{int(CRASH_WINDOW_S)} s — not restarting by itself; "
                                        "Start it again when you have looked at why")
                evt = dict(evt, crashes=len(recent), restarts=self._restarts.get(lang, 0),
                           held=lang in self._held)
        self.on_event(evt)

    def _index_job(self, srv: Server) -> None:
        """Indexing as a visible job: progress from `$/progress`, done when the server is ready
        (first pass ended, or it answered), cancelled = the server is stopped."""
        from . import jobs as J
        if J.installed() is None:
            return                                  # no IDE server (a unit test, the CLI): no drawer

        def work() -> Dict[str, Any]:
            while not srv.is_ready():
                time.sleep(0.2)
                if C.cancelled():
                    srv.kill("indexing cancelled")
                    raise C.Cancelled("cancelled")
                p = srv.indexing()
                if p:
                    pct = p.get("percentage")
                    C.report(note=(p["title"] + (" · " + p["message"] if p["message"] else "")),
                             done=int(pct) if isinstance(pct, (int, float)) else None,
                             total=100 if isinstance(pct, (int, float)) else None, unit="%")
                else:
                    C.report(note=f"{srv.name} starting")
            if srv.stopped.is_set() and not srv.stopping:
                raise ServerGone(f"{srv.name} crashed while indexing: {srv.stderr_tail()[-300:]}")
            return srv.status()
        job = J.background("lsp.index", {"root": self.root, "language": srv.language,
                                         "started": srv.started_at},
                           work, label=f"{srv.name} indexing", scope=os.path.basename(self.root))
        if job is not None:
            self.index_jobs[srv.language] = job

    # ---- using --------------------------------------------------------------------------
    def get(self, language: str) -> Optional[Server]:
        with self._lock:
            return self._servers.get(language)

    def recheck_trust(self) -> bool:
        """Trust is re-read on EVERY use (verification M1): the moment the record says revoked —
        `mokata ide --untrust` in a terminal, the file deleted, the window's Revoke — every running
        server is stopped, here, not at the idle reap. Returns whether the workspace is trusted."""
        if self.trusted():
            return True
        with self._lock:
            running = [lang for lang, s in self._servers.items() if s.alive]
        for lang in running:
            self.stop(lang, "trust revoked")
        return False

    def server_for(self, language: str) -> Tuple[Optional[Server], Dict[str, Any]]:
        """The usable server, or (None, why). A crashed one is restarted here unless held — and
        a GET never starts one that was never started (`never-started`). Untrusted: every server
        is stopped first and the answer is `untrusted`."""
        if not self.recheck_trust():
            return None, {"state": "untrusted", "note": "this workspace is not trusted — the "
                                                        "graph layer answers"}
        with self._lock:
            srv = self._servers.get(language)
        if srv is None:
            return None, {"state": "never-started", "note": "the language server has not been "
                                                            "started in this window"}
        if srv.alive:
            return srv, {"state": srv.status()["state"]}
        if srv.stopping:
            return None, {"state": "stopped", "note": f"{srv.name} was stopped"}
        res = self.start(language)
        if res.get("started"):
            with self._lock:
                srv = self._servers[language]
            return srv, {"state": "restarted", "note": f"{srv.name} crashed and was restarted"}
        return None, {"state": "crashed", "note": res.get("note", ""), "error": res.get("error")}

    def stop(self, language: str, why: str = "stopped") -> bool:
        with self._lock:
            srv = self._servers.pop(language, None)
        if srv is None:
            return False
        srv.stop(why)
        return True

    def stop_all(self, why: str = "stopped") -> None:
        with self._lock:
            langs = list(self._servers)
        for lang in langs:
            self.stop(lang, why)

    def reap(self, now: Optional[float] = None) -> List[str]:
        """Shut down every server idle for `idle_s` (a question in flight counts as use)."""
        now = self.clock() if now is None else now
        with self._lock:
            idle = [lang for lang, s in self._servers.items()
                    if s.alive and not s._pending and now - s.last_used >= self.idle_s]
        for lang in idle:
            self.stop(lang, "idle")
        return idle

    def _ensure_reaper(self) -> None:
        with self._lock:
            if self._reaper is not None and self._reaper.is_alive():
                return
            self._reaper = threading.Thread(target=self._reap_loop, daemon=True,
                                            name="mokata-lsp-reaper")
            self._reaper.start()

    def _reap_loop(self) -> None:
        while True:
            time.sleep(min(REAP_TICK_S, max(1.0, self.idle_s / 4)))
            with self._lock:
                if not any(s.alive for s in self._servers.values()):
                    self._reaper = None
                    return
            self.recheck_trust()                     # a CLI --untrust stops servers within a tick
            self.reap()

    def status(self) -> Dict[str, Any]:
        self.recheck_trust()
        with self._lock:
            servers = [s.status() for s in self._servers.values()]
            for s in servers:
                s["restarts"] = self._restarts.get(s["language"], 0)
                s["held"] = self._held.get(s["language"])
                job = self.index_jobs.get(s["language"])
                s["index_job"] = getattr(job, "id", None)
        return {"servers": servers}


# ------------------------------------------------------------------------ the answers
def _rel(root: str, abspath: str) -> Tuple[Optional[str], bool]:
    """(repo-relative path with '/', outside?)"""
    real_root = os.path.realpath(root)
    real = os.path.realpath(abspath)
    try:
        if os.path.commonpath([real_root, real]) == real_root:
            return str(name_of(real, real_root)), False
    except ValueError:
        pass
    return None, True


class _Lines:
    """Per-answer cache of file lines (a reference list names the same file many times)."""

    def __init__(self) -> None:
        self._cache: Dict[str, List[str]] = {}

    def line(self, abspath: str, n0: int) -> str:
        lines = self._cache.get(abspath)
        if lines is None:
            try:
                lines = source_text(abspath).splitlines()
            except OSError:
                lines = []
            self._cache[abspath] = lines
        return lines[n0] if 0 <= n0 < len(lines) else ""


def location(root: str, loc: Any, lines: _Lines, snippet: bool = True) -> Optional[Dict[str, Any]]:
    """One LSP Location / LocationLink -> `{path, line, col, end_line, end_col, snippet, outside,
    abspath}` with 1-based positions and characters (not UTF-16 units). A location OUTSIDE the
    workspace (the stdlib, site-packages, typeshed — or anything a server names, a UNC share
    included) is reported with its path but NEVER read: no snippet, no column conversion
    (verification L1). None for a Location that is malformed — not an object, or a URI
    `urlsplit` cannot read (verify-G M-3: it RAISED and failed the whole answer); the caller
    skips it and COUNTS it."""
    if not isinstance(loc, dict):
        return None
    uri = loc.get("targetUri") or loc.get("uri") or ""
    if not _uri_ok(uri):
        return None
    rng = loc.get("targetSelectionRange") or loc.get("targetRange") or loc.get("range") or {}
    start, end = rng.get("start") or {}, rng.get("end") or {}
    abspath = from_uri(uri) or ""
    rel, outside = _rel(root, abspath) if abspath else (None, True)
    l0 = int(start.get("line", 0))
    text = lines.line(abspath, l0) if (abspath and snippet and not outside) else ""
    return {"path": rel if rel is not None else abspath, "abspath": abspath, "outside": outside,
            "line": l0 + 1, "col": char_col(text, int(start.get("character", 0))) if text else
            int(start.get("character", 0)) + 1,
            "end_line": int(end.get("line", l0)) + 1,
            "end_col": char_col(text, int(end.get("character", 0))) if text else
            int(end.get("character", 0)) + 1,
            "snippet": text.strip()[:300] if text else ""}


def hover_text(result: Any) -> str:
    """A Hover's contents as plain text (markdown kept as written; no HTML is produced)."""
    if not isinstance(result, dict):
        return ""
    c = result.get("contents")
    parts: List[str] = []
    for item in (c if isinstance(c, list) else [c]):
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            v = item.get("value")
            if isinstance(v, str):
                lang = item.get("language")
                parts.append(f"```{lang}\n{v}\n```" if lang else v)
    return "\n\n".join(p for p in parts if p).strip()


def symbols_flat(result: Any, lines: Optional[_Lines] = None) -> List[Dict[str, Any]]:
    """DocumentSymbol[] (hierarchical) or SymbolInformation[] -> `[{name, kind, line, end, depth,
    detail}]` in file order."""
    out: List[Dict[str, Any]] = []
    if not isinstance(result, list):
        return out

    def walk(items: List[Any], depth: int) -> None:
        for s in items:
            if not isinstance(s, dict):
                continue
            rng = s.get("selectionRange") or s.get("range") or (s.get("location") or {}).get("range") or {}
            full = s.get("range") or (s.get("location") or {}).get("range") or rng
            out.append({"name": str(s.get("name", "")), "kind": SYMBOL_KINDS.get(s.get("kind"), "symbol"),
                        "line": int((rng.get("start") or {}).get("line", 0)) + 1,
                        "end": int((full.get("end") or {}).get("line", 0)) + 1, "depth": depth,
                        "detail": str(s.get("detail") or s.get("containerName") or "")[:200]})
            kids = s.get("children")
            if isinstance(kids, list):
                walk(kids, depth + 1)
    walk(result, 0)
    out.sort(key=lambda s: (s["line"], s["depth"]))
    return out


SYMBOL_KINDS = {1: "file", 2: "module", 3: "namespace", 4: "package", 5: "class", 6: "method",
                7: "property", 8: "field", 9: "constructor", 10: "enum", 11: "interface",
                12: "function", 13: "variable", 14: "constant", 15: "string", 16: "number",
                17: "boolean", 18: "array", 19: "object", 20: "key", 21: "null", 22: "enum member",
                23: "struct", 24: "event", 25: "operator", 26: "type parameter"}
SEVERITY = {1: "error", 2: "warning", 3: "information", 4: "hint"}


def diagnostics_of(root: str, srv: Server, abspath: str, lines: _Lines) -> List[Dict[str, Any]]:
    out = []
    for d in srv.diagnostics.get(to_uri(abspath), []):
        if not isinstance(d, dict):
            continue
        rng = d.get("range") or {}
        start = rng.get("start") or {}
        l0 = int(start.get("line", 0))
        text = lines.line(abspath, l0)
        out.append({"line": l0 + 1, "col": char_col(text, int(start.get("character", 0))),
                    "end_line": int((rng.get("end") or {}).get("line", l0)) + 1,
                    "severity": SEVERITY.get(d.get("severity"), "information"),
                    "message": str(d.get("message", ""))[:1000],
                    "code": str(d.get("code")) if d.get("code") is not None else None,
                    "source": str(d.get("source") or srv.name)})
    out.sort(key=lambda d: (d["line"], d["col"]))
    return out


# ------------------------------------------------------------------------ the call hierarchy
def call_hierarchy_supported(srv: Server) -> bool:
    """Did the server's `initialize` reply advertise `callHierarchyProvider`? Nothing is asked of a
    server that did not."""
    caps = srv.capabilities if isinstance(srv.capabilities, dict) else {}
    return bool(caps.get("callHierarchyProvider"))


def _num_ok(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= _POS_MAX


def _pos_ok(p: Any) -> bool:
    return isinstance(p, dict) and _num_ok(p.get("line")) and _num_ok(p.get("character"))


def _range_ok(r: Any) -> bool:
    if not (isinstance(r, dict) and _pos_ok(r.get("start")) and _pos_ok(r.get("end"))):
        return False
    s, e = r["start"], r["end"]
    return (e["line"], e["character"]) >= (s["line"], s["character"])


def _uri_ok(uri: Any) -> bool:
    """A URI string `urlsplit` can read (verify-G M-1: `file://[abc/x` RAISES there, and one such
    item voided the whole answer). THE one URI check of a call answer; whether a parsed URI is a
    local file inside the repo is `_call_place`'s question, not this one."""
    if not isinstance(uri, str):
        return False
    try:
        urlsplit(uri)
    except ValueError:
        return False
    return True


def _item_ok(it: Any) -> bool:
    """A CallHierarchyItem a row can be built from — every field checked for shape: a server is
    never trusted for it (a malformed one is dropped and COUNTED by the caller)."""
    if not (isinstance(it, dict) and isinstance(it.get("name"), str) and _uri_ok(it.get("uri"))
            and _range_ok(it.get("range")) and _range_ok(it.get("selectionRange"))):
        return False
    rng, sel = it["range"], it["selectionRange"]
    inside = ((rng["start"]["line"], rng["start"]["character"])
              <= (sel["start"]["line"], sel["start"]["character"])
              and (sel["end"]["line"], sel["end"]["character"])
              <= (rng["end"]["line"], rng["end"]["character"]))
    return inside and (it.get("kind") is None or _num_ok(it.get("kind")))


class CallLines:
    """The lines a call answer shows, read BOUNDED: only regular files (a FIFO or device named by a
    server is never opened), streamed, keeping only the lines asked for — memory is O(rows), not
    O(file), so a 1M-line caller file costs one pass and its few lines. Lines split on `\n` with a
    trailing `\r` dropped (the viewer's rule, `usages.viewer_lines`; never `splitlines`, which
    breaks on form feed and U+2028 where LSP does not), each kept to CALL_LINE_MAX characters."""

    def __init__(self) -> None:
        self._want: Dict[str, set] = {}
        self._got: Dict[str, Dict[int, str]] = {}

    def want(self, abspath: str, l0: int) -> None:
        self._want.setdefault(abspath, set()).add(l0)

    def load(self, checkpoint: Callable[[], None] = lambda: None) -> None:
        import stat
        for abspath, wanted in self._want.items():
            checkpoint()
            got: Dict[int, str] = {}
            self._got[abspath] = got
            try:
                if not stat.S_ISREG(os.stat(abspath).st_mode):
                    continue
                last = max(wanted)
                with open(abspath, "rb") as fh:
                    n = 0
                    while n <= last:
                        # at most CALL_LINE_BYTES of a line are held: a one-line minified bundle
                        # is skipped through in pieces, never read whole (verify-G L-3)
                        raw = fh.readline(CALL_LINE_BYTES)
                        if not raw:
                            break
                        if n in wanted:
                            got[n] = raw.rstrip(b"\n").rstrip(b"\r").decode(
                                "utf-8", "replace")[:CALL_LINE_MAX]
                        while not raw.endswith(b"\n"):
                            raw = fh.readline(CALL_LINE_BYTES)
                            if not raw:
                                break
                        n += 1
            except OSError:
                continue

    def line(self, abspath: str, l0: int) -> Optional[str]:
        """The line, or None when it was not read (not a regular file, unreadable, past its end)."""
        return self._got.get(abspath, {}).get(l0)


def _call_place(root: str, it: Dict[str, Any]) -> Tuple[str, Optional[str], bool]:
    """(abspath, repo-relative name or None, outside?) of an item's URI — no file is read here."""
    abspath = from_uri(it["uri"]) or ""
    rel, outside = _rel(root, abspath) if abspath else (None, True)
    return abspath, rel, outside


def call_row(root: str, it: Dict[str, Any], lines: CallLines) -> Dict[str, Any]:
    """One CallHierarchyItem as a row the page may see: name (clipped), kind, path:line:col at its
    `selectionRange` start (1-based CHARACTERS, converted on the real line), snippet. An item
    OUTSIDE the workspace is shown (`shown`: its path, or the raw URI for a host/UNC one) and never
    read. The item itself — its `data` included — is NOT in the row."""
    abspath, rel, outside = _call_place(root, it)
    start = it["selectionRange"]["start"]
    l0, units = start["line"], start["character"]
    text = lines.line(abspath, l0)        # None for an outside item: `call_edges` never asked for it
    if text is not None:
        col = char_col(text, units)
        exact = not (len(text) >= CALL_LINE_MAX and col > len(text))
    else:
        col, exact = units + 1, False
    detail = it.get("detail")
    return {"name": it["name"][:300], "kind": SYMBOL_KINDS.get(it.get("kind"), "symbol"),
            "detail": detail[:200] if isinstance(detail, str) else "",
            "path": rel or "", "shown": (rel or abspath or it["uri"])[:200], "outside": outside,
            "line": l0 + 1, "col": col, "col_exact": exact,
            "snippet": text.strip()[:300] if text else ""}


def call_edges(root: str, srv: Server, uri: str, pos: Dict[str, int], direction: str,
               recheck: Callable[[], bool], checkpoint: Callable[[], None] = lambda: None,
               cap: int = CALLS_MAX) -> Dict[str, Any]:
    """`prepareCallHierarchy` at `pos`, then `incomingCalls` (`direction="in"`) or `outgoingCalls`
    (`"out"`) for each prepared item (overloads: each its own group, at most CALLS_ITEMS_MAX asked).
    `recheck()` (the host's trust re-read) runs before EVERY request: False -> `{"revoked": True}`
    and nothing more is sent. Rows: at most `cap` across all groups; each group carries the EXACT
    count of well-formed entries the server sent (`total`) and how many malformed ones were
    dropped (`skipped`). Every server value is shape-checked; the items go back to the server
    untouched and never into the result."""
    method = CALL_DIRECTIONS[direction]
    key = "from" if direction == "in" else "to"
    if not recheck():
        return {"revoked": True}
    checkpoint()
    prep = srv.request("textDocument/prepareCallHierarchy",
                       {"textDocument": {"uri": uri}, "position": pos})
    skipped = 0
    raw = prep if isinstance(prep, list) else [] if prep is None else None
    if raw is None:
        skipped, raw = 1, []
    items = [it for it in raw if _item_ok(it)]
    skipped += len(raw) - len(items)
    asked = items[:CALLS_ITEMS_MAX]
    picked: List[Tuple[Dict[str, Any], List[Tuple[Dict[str, Any], int]], int, int]] = []
    budget = cap
    for it in asked:
        if not recheck():
            return {"revoked": True}
        checkpoint()
        res = srv.request(method, {"item": it})
        entries = res if isinstance(res, list) else [] if res is None else None
        g_skipped = 0
        if entries is None:
            g_skipped, entries = 1, []
        good: List[Tuple[Dict[str, Any], int]] = []
        total = 0
        for i, e in enumerate(entries):
            if i % 5000 == 0:
                checkpoint()
            if not (isinstance(e, dict) and _item_ok(e.get(key))
                    and isinstance(e.get("fromRanges"), list)):
                g_skipped += 1
                continue
            total += 1
            if len(good) < budget:
                good.append((e[key], sum(1 for r in e["fromRanges"] if _range_ok(r))))
        budget -= len(good)
        skipped += g_skipped
        picked.append((it, good, total, g_skipped))
    lines = CallLines()
    for it, good, _t, _s in picked:
        for x in [it] + [g for g, _n in good]:
            abspath, _rel_, outside = _call_place(root, x)
            if not outside:
                lines.want(abspath, x["selectionRange"]["start"]["line"])
    lines.load(checkpoint)
    groups = []
    for it, good, total, g_skipped in picked:
        rows = [dict(call_row(root, x, lines), calls=n) for x, n in good]
        groups.append({"item": call_row(root, it, lines), "rows": rows, "total": total,
                       "truncated": len(rows) < total, "skipped": g_skipped})
    return {"groups": groups, "items_named": len(items), "items_asked": len(asked),
            "skipped": skipped, "callable": bool(items)}


__all__ = ["ALLOWED", "ALLOWED_REQUESTS", "ALLOWED_NOTIFICATIONS", "WRITE_METHODS", "READ_ONLY",
           "IDLE_S", "MAX_CRASHES", "CRASH_WINDOW_S", "MethodRefused", "ServerGone",
           "ServerError", "Server", "Host", "languages", "language_of", "language_id",
           "find_binary", "find_server", "path_dirs", "which_in", "will_run", "to_uri", "from_uri",
           "utf16_col", "char_col", "location", "hover_text", "symbols_flat", "diagnostics_of",
           "source_text", "CALLS_MAX", "CALLS_ITEMS_MAX", "CALL_DIRECTIONS",
           "call_hierarchy_supported", "call_edges", "call_row", "CallLines"]
