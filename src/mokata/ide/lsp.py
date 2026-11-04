"""Read-only LSP bridge (0.0.27 P24 `ID-LSP-READONLY-EARLIER`, research ruling R3; doc 109 §3.3,
§6, §8, §14, §18.2). The IDE talks to the PROJECT'S OWN language servers — never one of its own
(§8: no LSP implementation, no parser) — and only READS from them.

WHAT IT OFFERS: hover, go-to-definition, find references, document symbols, and the diagnostics a
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
from ..quiet_spawn import detached, hidden
from ..exe_path import _under, path_dirs, shim_in, which_in
from ..repo_paths import name_of, split_path

# ------------------------------------------------------------------------ the method allowlist
#: Client -> server. Everything else is refused at `Server.send`.
ALLOWED_REQUESTS = frozenset({
    "initialize", "shutdown",
    "textDocument/hover", "textDocument/definition", "textDocument/references",
    "textDocument/documentSymbol",
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
#: Per step of a stop: the `shutdown` answer, the exit after `exit`, the leader's death after the
#: tree kill. A stop is bounded by 3 × this and a kill by 1 × — on POSIX (`killpg`) and on Windows
#: with the server's Job Object (`WinJob.terminate` does not wait). Only when Windows could not
#: make the job does the `taskkill /F /T` fallback add one more step (its own timeout): 4 × and 2 ×
#: there (re-verification L6).
STOP_GRACE_S = 2.0
READ_POLL_S = 0.2         # the POSIX reader's select slice (it checks the stop flag between)
REAP_TICK_S = 5.0         # the reaper's tick: idle shutdown and the trust re-check run this often
STDERR_KEEP = 4096        # the tail of a server's stderr kept for its crash report
OPEN_DOCS_MAX = 32        # documents kept open on a server (LRU), bounded by the asker
REFERENCES_SNIPPET_MAX = 2000   # snippets read for the first N references; the rest are bare
NO_PROGRESS_GRACE_S = 3.0       # a server that reports no `$/progress` counts as indexed after this

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
    if os.path.isdir(modules) and not _under(modules, anchor):
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
    if not _under(script, modules) or not os.path.isfile(script):
        return None, (f"{pkg}/package.json: bin {rel!r} resolves outside {modules} "
                      f"({script}) — refused")
    node = node_on_path(root, environ)
    if not node:
        return None, f"{pkg} is installed in {modules} but node is not on PATH"
    return [node, script], ""


def _node_package_bin(root: str, name: str) -> Tuple[Optional[List[str]], str]:
    """The project's own node server: `<root>/node_modules/<pkg>` (npm, pnpm, yarn v1)."""
    return _package_bin(os.path.join(root, "node_modules"), root, root, name)


def _global_module_dirs(entry: str) -> List[Tuple[str, str]]:
    """`(node_modules, anchor it must stay inside)` for each place a GLOBAL install can sit, for
    one absolute PATH entry:

      * npm's Windows prefix IS the PATH entry — `<entry>/node_modules` (shims beside it);
      * pnpm's global store — `<entry>/global/<n>/node_modules`, newest `<n>` first
        (`%LOCALAPPDATA%\\pnpm` is on PATH, the packages are one level down);
      * yarn v1's — `<...>/Yarn/bin` is on PATH, the packages are in `<...>/Yarn/Data/global/
        node_modules`.
    Only directories that exist, and only under entries the USER put on PATH (0.0.27: a global
    pnpm/yarn was reported "not installed" because only the first layout was read)."""
    out = [(os.path.join(entry, "node_modules"), entry)]
    store = os.path.join(entry, "global")
    if os.path.isdir(store):
        try:
            nums = sorted(os.listdir(store), reverse=True)
        except OSError:
            nums = []
        out += [(os.path.join(store, n, "node_modules"), entry) for n in nums]
    if os.path.basename(os.path.normpath(entry)).lower() == "bin":
        up = os.path.dirname(os.path.normpath(entry))
        out.append((os.path.join(up, "Data", "global", "node_modules"), up))
    return [(m, a) for m, a in out if os.path.isdir(m)]


def _global_node_package_bin(root: str, name: str, pkg: Optional[str] = None,
                             environ: Optional[Dict[str, str]] = None
                             ) -> Tuple[Optional[List[str]], str]:
    """A GLOBAL npm/pnpm/yarn install the user made, found through PATH without reading its shim:
    npm's Windows prefix is the PATH directory itself (`%APPDATA%\\npm\\x.cmd` beside
    `%APPDATA%\\npm\\node_modules\\<pkg>`), so each absolute PATH entry outside the repo is
    asked for `<entry>/node_modules/<pkg>/package.json` (and the pnpm/yarn layouts of
    `_global_module_dirs`). (On POSIX the global bin is a symlink `which_in` already finds.)"""
    why = ""
    for d in path_dirs(environ, cwd=root):
        if _under(d, root):
            continue
        for modules, anchor in _global_module_dirs(d):
            head, w = _package_bin(modules, anchor, root, name, pkg, environ)
            if head is not None:
                return head, ""
            why = why or w
    return None, why


def find_server(root: str, language: str, nt: Optional[bool] = None) -> Dict[str, Any]:
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
    shim_note = ""
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
        if head is None and not shim_note and (os.name == "nt" if nt is None else nt):
            shim = shim_in(name, path_dirs(cwd=root))
            if shim:
                # Windows ships npm/gem/Maven tools as `.cmd`/`.bat`/`.ps1` wrappers. They are
                # found and NOT run (cmd.exe / PowerShell would re-parse the command line); say
                # so instead of "not on PATH", which is false.
                shim_note = (f"{name} was found only as {shim}, and mokata never runs a "
                             f".cmd/.bat/.ps1 (cmd.exe or PowerShell would re-parse its command "
                             f"line) — name the server's real executable (an .exe, or node and "
                             f"its script) in {USER_CONFIG}")
        cands.append({"name": name, "install": install, "found": head is not None})
        if head is not None and pick is None:
            pick = (name, [*head, *tail])
    if pick is None:
        return {"found": False, "binary": None, "argv": None, "name": None, "candidates": cands,
                "note": note or shim_note or (f"no {language} language server on PATH, in the venv or node_modules"
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


#: Names Windows resolves to a DEVICE in any directory, with or without an extension. Ported from
#: CPython 3.13's `ntpath` (the floor here is 3.10, so the rule is copied, not called): COM and LPT
#: ports 1-9 AND the superscript digits (`COM\u00b9` is COM1 to Windows).
_DEVICE_NAMES = frozenset({"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
                          | {f"COM{c}" for c in "123456789\u00b9\u00b2\u00b3"}
                          | {f"LPT{c}" for c in "123456789\u00b9\u00b2\u00b3"})
#: Characters Win32 never allows in a file name (control characters included); `:` also begins an
#: NTFS stream (`a.py::$DATA`).
_NT_RESERVED_CHARS = frozenset({chr(i) for i in range(32)} | set('"*:<>?|'))


def _nt_reserved_name(name: str) -> bool:
    """One path component Windows cannot honestly treat as the file it spells (3.13
    `ntpath._isreservedname`): a trailing dot or space is folded away (`x.` IS `x`, so a trailing-dot
    spelling is an alias, not a source location), reserved characters, and a device name — `CON`, `nul .py`,
    `COM\u00b9`, `LPT2.txt` — whatever follows the first dot and however many spaces precede it."""
    if not name or name in (".", ".."):
        return False
    if name[-1] in ". ":
        return True
    if _NT_RESERVED_CHARS.intersection(name):
        return True
    return name.partition(".")[0].rstrip(" ").upper() in _DEVICE_NAMES


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
    for part in re.split(r"[/\\]", m.group(2) or ""):
        # A device (`CON`, `NUL.txt`, `COM1`) is not a file in the repo whatever its directory,
        # and `a.py::$DATA` / `a.py:stream` names an NTFS stream, not the file. Neither is a
        # source location a language server can honestly point at.
        if _nt_reserved_name(part):
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


class WinJob:
    """A Windows Job Object holding the server and everything it starts (re-verification R1): a
    child inherits its parent's job, so `terminate` reaches the whole tree — ALSO after the
    leader died, when `taskkill /T` has no live pid left to walk the tree from. The job is made
    KILL_ON_JOB_CLOSE, so the tree also dies if the IDE process itself dies. `kernel32` is
    injectable for the tests (no Windows box here). Never raises; `ok` says it holds the server."""

    _KILL_ON_JOB_CLOSE = 0x00002000
    _EXTENDED_LIMIT_INFORMATION = 9

    def __init__(self, proc: Any, kernel32: Any = None) -> None:
        self.handle: Any = None
        self.ok = False
        try:
            import ctypes
            from ctypes import wintypes
            k32 = kernel32 if kernel32 is not None else ctypes.WinDLL("kernel32", use_last_error=True)
            self._k32 = k32

            # DWORD is 4 bytes in the Windows ABI; `wintypes.DWORD` is `c_ulong`, which is 8 under
            # LP64 — fixed-width fields keep the layout (144 bytes on 64-bit) host-independent,
            # so the test can check it off Windows (verify2 N5).
            class _Basic(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                            ("PerJobUserTimeLimit", ctypes.c_int64),
                            ("LimitFlags", ctypes.c_uint32),
                            ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t),
                            ("ActiveProcessLimit", ctypes.c_uint32),
                            ("Affinity", ctypes.c_size_t),
                            ("PriorityClass", ctypes.c_uint32),
                            ("SchedulingClass", ctypes.c_uint32)]

            class _Io(ctypes.Structure):
                _fields_ = [(n, ctypes.c_uint64) for n in (
                    "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                    "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

            class _Extended(ctypes.Structure):
                _fields_ = [("BasicLimitInformation", _Basic), ("IoInfo", _Io),
                            ("ProcessMemoryLimit", ctypes.c_size_t),
                            ("JobMemoryLimit", ctypes.c_size_t),
                            ("PeakProcessMemoryUsed", ctypes.c_size_t),
                            ("PeakJobMemoryUsed", ctypes.c_size_t)]

            if kernel32 is None:                 # real prototypes: HANDLEs are pointer-sized
                k32.CreateJobObjectW.restype = wintypes.HANDLE
                k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
                k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                        ctypes.c_void_p, wintypes.DWORD]
                k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
                k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
                k32.CloseHandle.argtypes = [wintypes.HANDLE]
            job = k32.CreateJobObjectW(None, None)
            if not job:
                return
            self.handle = job
            info = _Extended()
            info.BasicLimitInformation.LimitFlags = self._KILL_ON_JOB_CLOSE
            # Without KILL_ON_JOB_CLOSE the tree would outlive an IDE that dies: a job whose
            # limits did not take is not the job this class promises — `ok` stays False and the
            # taskkill fallback is used (verify2 N5).
            if not k32.SetInformationJobObject(job, self._EXTENDED_LIMIT_INFORMATION,
                                               ctypes.byref(info), ctypes.sizeof(info)):
                return
            self.ok = bool(k32.AssignProcessToJobObject(job, int(proc._handle)))
        except (OSError, AttributeError, ValueError, TypeError):
            self.ok = False

    def terminate(self) -> None:
        """Kill every process in the job, then release it. Idempotent."""
        job, self.handle = self.handle, None
        if job is None:
            return
        try:
            if self.ok:
                self._k32.TerminateJobObject(job, 1)
            self._k32.CloseHandle(job)
        except (OSError, AttributeError, ValueError, TypeError):
            pass


def _win_process_table() -> List[Tuple[int, int]]:
    """`[(pid, parent pid)]` of every process, from a ToolHelp snapshot (ctypes only; Windows)."""
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _Entry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]

    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Entry)]
    k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Entry)]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)                  # TH32CS_SNAPPROCESS
    if not snap or int(snap) in (-1, 0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF):
        return []
    out: List[Tuple[int, int]] = []
    try:
        entry = _Entry()
        entry.dwSize = ctypes.sizeof(_Entry)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            out.append((int(entry.th32ProcessID), int(entry.th32ParentProcessID)))
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return out


def _win_open(pid: int, access: int) -> Any:
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    return k32.OpenProcess(access, False, pid) or None


def _win_created(pid: int) -> Optional[int]:
    """The process's creation time (FILETIME as one int), or None when it is gone/unreadable."""
    import ctypes
    from ctypes import wintypes
    h = _win_open(pid, 0x1000)                       # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return None
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    ft = [wintypes.FILETIME() for _ in range(4)]
    try:
        if not k32.GetProcessTimes(h, *[ctypes.byref(f) for f in ft]):
            return None
        return (ft[0].dwHighDateTime << 32) | ft[0].dwLowDateTime
    finally:
        k32.CloseHandle(h)


def _win_kill_pid(pid: int) -> None:
    import ctypes
    from ctypes import wintypes
    h = _win_open(pid, 0x0001)                       # PROCESS_TERMINATE
    if not h:
        return
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    try:
        k32.TerminateProcess(h, 1)
    finally:
        k32.CloseHandle(h)


class TreeWatch:
    """The fallback when no Job Object could be made (`WinJob.ok` False): remember the runner's
    descendants WHILE it lives, so they can still be ended after it has exited.

    `taskkill /F /T` needs a live root to walk from, and Windows does not re-parent an orphan: a
    child still names its dead parent's pid. So `observe()` (called every few hundred ms while the
    leader runs) records every process whose parent is the leader or an already-known descendant
    and which was CREATED AFTER that parent and BEFORE any newer process that now holds the parent's
    pid (a pid Windows reused for an unrelated process is older than whatever names it, and the
    children of a reused pid are born after the pid's new holder — neither is ever taken);
    `end()` kills what is known and still the same process (same pid AND creation time).

    WHAT IT CANNOT REACH, said exactly: a process started AND orphaned between two `observe()`
    calls (so its parent is gone before it was ever seen), a descendant that is not in a chain of
    recorded parents (one that detached itself with a new parent via a handle-inheritance trick),
    and a descendant created after the last `observe()`/`end()` pass. The Job Object has none of
    those holes; this is only the fallback. Every probe is injectable; never raises."""

    def __init__(self, leader_pid: int, table: Optional[Callable[[], List[Tuple[int, int]]]] = None,
                 created: Optional[Callable[[int], Optional[int]]] = None,
                 kill: Optional[Callable[[int], None]] = None) -> None:
        self.table = table or _win_process_table
        self.created = created or _win_created
        self.kill = kill or _win_kill_pid
        self.leader = leader_pid
        self.known: Dict[int, int] = {}                   # pid -> creation time
        self._leader_created = self.created(leader_pid)

    def observe(self) -> None:
        try:
            rows = self.table()
            roots = {self.leader: self._leader_created}
            roots.update(self.known)
            kids: Dict[int, List[int]] = {}
            for pid, ppid in rows:
                kids.setdefault(ppid, []).append(pid)
            todo = [p for p in roots if p in kids]
            while todo:
                parent = todo.pop()
                born = roots.get(parent)
                # Is `parent` still the process we recorded? A pid Windows gave to an UNRELATED
                # process after ours exited has a different creation time: its children are not
                # ours — except those born before that newer holder existed, which can only have
                # been started by the process we recorded (orphans of it), so the window for a
                # child is [recorded, current holder's creation).
                now = self.created(parent)
                before = now if (born is not None and now is not None and now != born) else None
                for pid in kids.get(parent, ()):
                    if pid in self.known or pid == self.leader:
                        continue
                    c = self.created(pid)
                    if c is None or (born is not None and c < born):
                        continue                          # gone, or a reused pid older than its "parent"
                    if before is not None and c >= before:
                        continue                          # a child of the pid's NEW, unrelated holder
                    self.known[pid] = c
                    roots[pid] = c
                    todo.append(pid)
        except (OSError, AttributeError, ValueError, TypeError):
            pass

    def end(self) -> List[int]:
        """Kill every remembered descendant that is still the same process. Returns the pids."""
        self.observe()
        ended = []
        for pid, c in list(self.known.items()):
            try:
                if self.created(pid) == c:
                    self.kill(pid)
                    ended.append(pid)
            except (OSError, AttributeError, ValueError, TypeError):
                pass
        return ended


def _resume_process(proc: Any) -> None:
    """Let a process created CREATE_SUSPENDED run: `NtResumeProcess` on its handle (Popen drops
    the primary thread's handle, so there is no thread to `ResumeThread`). ctypes only. Raises
    OSError when Windows refuses."""
    import ctypes
    from ctypes import wintypes
    ntdll = ctypes.WinDLL("ntdll")
    ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
    ntdll.NtResumeProcess.restype = ctypes.c_long
    status = ntdll.NtResumeProcess(int(proc._handle))
    if status != 0:
        raise OSError(f"NtResumeProcess failed: NTSTATUS {status & 0xFFFFFFFF:#x}")


def spawn_jobbed(argv: Sequence[str], stdin: Any, nt: Optional[bool] = None,
                 **popen_kw: Any) -> "tuple[subprocess.Popen, Optional[WinJob]]":
    """Start `argv` — in its own session / process group, with no console window (`detached()`) —
    so that it, and everything it ever starts, is in OUR Job Object from its very first
    instruction. POSIX: a plain Popen (`start_new_session` makes the group); the job is None.
    Windows: the process is created CREATE_SUSPENDED, assigned to a new KILL_ON_JOB_CLOSE job (no
    breakaway of any kind is allowed), and only then resumed. Assigning AFTER a running start lost
    a race (1 in 40 on a real runner): a launcher — a venv's `Scripts\\python.exe`, `pytest.exe`,
    `pylsp.exe` — starts its real child within milliseconds, and a child that started first is in
    no job of ours, so `terminate` never reached it.
    If the job could not be made (`job.ok` False) the process is still resumed and the caller's
    fallback (`taskkill /F /T` from the live leader, before it exits) is all there is: that
    fallback cannot reach what a leader left behind AFTER it died. Raises OSError (the process
    killed first) if it cannot be resumed. `nt` injects the platform for the tests."""
    is_nt = (os.name == "nt") if nt is None else nt
    proc = subprocess.Popen(list(argv), stdin=stdin, **popen_kw,
                            **detached(suspended=is_nt, nt=is_nt))
    if not is_nt:
        return proc, None
    job = WinJob(proc)
    resumed = False
    try:
        _resume_process(proc)
        resumed = True
    finally:
        if not resumed:                         # never leave a suspended process behind
            job.terminate()
            try:
                proc.kill()
                proc.wait(timeout=STOP_GRACE_S)
            except (OSError, subprocess.SubprocessError):
                pass
    return proc, job


def _kill_tree(proc: Any, pgid: Optional[int] = None, job: Optional[WinJob] = None) -> None:
    """Kill `proc` and everything it started — ALSO after `proc` itself is already dead
    (re-verification R1: a crashed leader's children survived). POSIX: `killpg(pgid)` with the
    group id RECORDED AT SPAWN (`start_new_session` makes it the leader's pid; the group outlives
    the reaped leader while any member lives — `getpgid` on the dead leader would raise and send
    nothing). Windows: the server's Job Object (`WinJob`), which holds the tree whether or not the
    leader lives; only if the job could not be made does it fall back to `taskkill /F /T` by
    absolute path, which needs a live leader to anchor the tree. Bounded: one STOP_GRACE_S wait
    for the leader (plus one for taskkill in that fallback); never raises."""
    try:
        if os.name == "nt":
            if job is not None and job.ok:
                job.terminate()
            elif proc.poll() is None:
                exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32",
                                   "taskkill.exe")
                subprocess.run([exe, "/F", "/T", "/PID", str(proc.pid)], stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=STOP_GRACE_S, **hidden())
        else:
            import signal
            if pgid is not None:                # None: the group was already killed (verify2 N1)
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    pass
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=STOP_GRACE_S)
    except (OSError, subprocess.SubprocessError):
        pass
    finally:
        if job is not None:
            job.terminate()                     # releases the handle (a no-op after the kill)


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
        self.pgid: Optional[int] = None
        self.job: Optional[WinJob] = None
        self._stop_readers: Optional[threading.Event] = None

    # ---- lifecycle -----------------------------------------------------------------------
    def start(self) -> None:
        # Its OWN process group / session (verification M1): stopping kills the whole tree
        # (tsserver under typescript-language-server, proc-macro-srv under rust-analyzer, `go`
        # under gopls), not only the direct child. No shell is involved anywhere (M3).
        # Windows: created suspended, put in the tree's Job Object, THEN resumed (`spawn_jobbed`) —
        # a launcher's first child is in the job too.
        self.proc, self.job = spawn_jobbed(
            self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=self.root, close_fds=True)   # own session / group, hidden console
        self.pgid = self.proc.pid if os.name != "nt" else None     # its session's id, kept
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
                    "publishDiagnostics": {"relatedInformation": False},
                    "synchronization": {"didSave": False, "willSave": False}},
            },
        }
        res = self.request("initialize", params)
        self.capabilities = (res or {}).get("capabilities", {}) if isinstance(res, dict) else {}
        self.notify("initialized", {})

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None and not self.stopped.is_set()

    def stop(self, why: str = "stopped") -> None:
        """`shutdown` → `exit` → the process TREE is killed if it lingers. Never blocks past
        3 × STOP_GRACE_S (4 × on Windows only when its Job Object could not be made — see
        STOP_GRACE_S; verification H2, L6): no caller — revoke, IDE shutdown, idle reap, a
        cancelled index job — ever waits on the server's pipes. Pending questions end with
        ServerGone."""
        self.stopping = True
        proc = self.proc
        if proc is None:
            return
        try:
            if proc.poll() is None:
                try:
                    self.request("shutdown", None, wait=STOP_GRACE_S)
                except (ServerGone, ServerError, TimeoutError):
                    pass
                try:
                    self.notify("exit", None)
                except ServerGone:
                    pass
                try:
                    proc.wait(STOP_GRACE_S)
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
        proc = self.proc
        if proc is None:
            return
        try:
            proc.wait()
        except OSError:
            return
        self._gone("exited")

    def _gone(self, why: str) -> None:
        """The server is over (stopped, killed or crashed): its whole tree is killed — on the
        crash path too (R1) — pending questions fail with ServerGone at once, the readers are
        told to stop. Idempotent: the first caller publishes the event, the rest only re-kill."""
        proc = self.proc
        # verify2 N1: the group is killed ONCE. After that its id is forgotten — once the group
        # is empty the number can be handed to an unrelated process group, and a later stop /
        # kill / reap re-sending SIGKILL to it would hit that group instead.
        with self._lock:
            pgid, self.pgid = self.pgid, None
        if proc is not None:
            _kill_tree(proc, pgid, self.job)
        with self._lock:
            if proc is not None:
                self.exit_code = proc.poll()
            pend, self._pending = self._pending, {}
            first = not self.stopped.is_set()
            self.stopped.set()
        if self._stop_readers is not None:
            self._stop_readers.set()
        if self.proc is not None and self.proc.stdin is not None:
            try:
                self.proc.stdin.close()                   # ours alone: no reader holds it
            except (OSError, ValueError):
                pass
        for p in pend.values():
            exit_note = f" (exit {self.exit_code})" if self.exit_code is not None else ""
            p["error"] = ServerGone(f"{self.name} {why}{exit_note}")
            p["done"].set()
        if first:
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
                    if self.proc is not None and self.proc.poll() is not None:
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
        try:
            for chunk in (self._chunks(out) if out is not None else ()):
                buf.extend(chunk)
                while True:
                    head_end = buf.find(b"\r\n\r\n")
                    if head_end < 0:
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
                    if len(buf) < head_end + 4 + length:
                        break
                    body = bytes(buf[head_end + 4:head_end + 4 + length])
                    del buf[:head_end + 4 + length]
                    try:
                        msg = json.loads(body.decode("utf-8", "replace"))
                    except ValueError:
                        continue
                    self._dispatch(msg)
        except (OSError, ValueError):
            pass
        finally:
            try:
                if out is not None:
                    out.close()                               # the reader owns its pipe
            except (OSError, ValueError):
                pass
            if self.proc is not None and not self.stopped.is_set():
                try:
                    self.proc.wait(STOP_GRACE_S)              # EOF: it exited (or is exiting)
                except subprocess.TimeoutExpired:
                    pass
            self._gone("exited")

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


def location(root: str, loc: Dict[str, Any], lines: _Lines, snippet: bool = True) -> Dict[str, Any]:
    """One LSP Location / LocationLink -> `{path, line, col, end_line, end_col, snippet, outside,
    abspath}` with 1-based positions and characters (not UTF-16 units). A location OUTSIDE the
    workspace (the stdlib, site-packages, typeshed — or anything a server names, a UNC share
    included) is reported with its path but NEVER read: no snippet, no column conversion
    (verification L1)."""
    uri = loc.get("targetUri") or loc.get("uri") or ""
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


__all__ = ["ALLOWED", "ALLOWED_REQUESTS", "ALLOWED_NOTIFICATIONS", "WRITE_METHODS", "READ_ONLY",
           "IDLE_S", "MAX_CRASHES", "CRASH_WINDOW_S", "MethodRefused", "ServerGone",
           "ServerError", "Server", "Host", "languages", "language_of", "language_id",
           "find_binary", "find_server", "spawn_jobbed", "path_dirs", "which_in", "will_run", "to_uri", "from_uri",
           "utf16_col", "char_col", "location", "hover_text", "symbols_flat", "diagnostics_of",
           "source_text"]
