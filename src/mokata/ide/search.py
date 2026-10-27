"""Search for `mokata ide` (0.0.23 C2): by WORD, by REGEX, and by MEANING — each answer naming the
engine that produced it, and no engine ever standing in for another under its name.

    text(root, q, mode, ...)      word (literal) or regex search over the repo's own source —
                                  `rg` when it is on PATH, else the pure-Python floor; the result
                                  says which (`engine`). The file set is the explorer's: the graph's
                                  own walk (`nav.walk_files`), so search can never surface a venv.
    meaning(root, q, ...)         ID.S2-SEMANTIC per ruling R2 — mokata's OWN local code-embedding
                                  path. Function- and class-level chunks (cut by the graph layer's
                                  own parser, `nav.outline`) are embedded with the optional
                                  `[embeddings]` extra (model2vec, the SAME `Model2VecEmbedder`
                                  memory uses) into a local index under `.mokata/temp_local/ide/`,
                                  and ranked results link to graph symbols.

HONEST DEGRADE (R2). When model2vec is not installed — or its model is not on this machine — the
meaning answer is an explicit `available: false` state carrying the install line. It NEVER returns
word results labelled as meaning, and it never falls back to the token-hash embedder (whose noise
floor makes "semantic" near-inert): a hashing answer under the word "meaning" would be the exact
mislabel this module exists to refuse.

NO NETWORK BY DEFAULT. The model is loaded OFFLINE (`local_files_only=True`, per call): a cache
miss is the `model-not-local` state, not a download. Fetching it is one explicit request the user makes
(`fetch_model`), like the PR fetch — the review window's only other network call. No API key is
read anywhere.
"""

from __future__ import annotations

import json
import os
import re
import struct
import subprocess
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ..quiet_spawn import hidden
from . import nav
from ..errors import MokataError
from ..knowledge.cancel import Cancelled, checkpoint, kill_on_cancel, report
from ..repo_paths import name_of

# 0.0.26 P36 (G13/R16) — no hard stop. A word/regex answer is a PAGE of `PAGE_MATCHES` hits in a
# deterministic order (rg `--sort path`); `next_offset` asks for the next page ("load more"), so
# every hit of a common word on a 7M-line repo is reachable. The old `MAX_MATCHES = 2000` stop
# ("stopped at 2000 hits — narrow the search") is gone.
PAGE_MATCHES = 1000
MAX_LINE_CHARS = 400
# 0.0.26 P36 (G33) — files over this size ARE searched (they used to be skipped silently by both
# rg's `--max-filesize 1M` and the floor); each such file in an answer carries its size, said.
LARGE_FILE_BYTES = 1024 * 1024
BINARY_PROBE_BYTES = 8192
PARTIAL_EVERY_S = 0.3   # P32: a running word search publishes its hits so far this often


def fix(state: str) -> str:
    """The ONE fix line for meaning search in `state` — doctor's (`govern.doctor.fix_line`), the
    same string the Health row and the Welcome row print (0.0.26 P39, R36). Never spawns."""
    from ..govern.doctor import fix_line
    return fix_line("meaning", state)


CHUNK_MAX_LINES = 80
CHUNK_MAX_CHARS = 2400
INDEX_SCHEMA = 2                 # 0.0.26 O13: metadata JSON + one float32 vectors file
MEANING_LIMIT = 20

# A seam for tests (and nothing else): when set, it is called instead of loading model2vec. It must
# return an embedder (a callable text -> vector with `embedder_id`/`dim`) or raise. Production
# leaves it None — and production NEVER substitutes the hashing embedder.
EMBEDDER_FACTORY: Optional[Callable[[], Any]] = None


# =========================================================================== word / regex

def rg_path() -> Optional[str]:
    """`rg` on PATH, else in mokata's own environment (0.0.26 O13: `mokata[full]` installs the
    `ripgrep` wheel there, and `uv tool` / `pipx` never put a dependency's executable on PATH)."""
    from ..detect import find_command
    return find_command("rg")


def engine_name() -> str:
    return "rg" if rg_path() else "python-floor"


class BadPattern(MokataError, ValueError):
    """A regex the user typed that does not compile — a HARD refusal with the reason (D5), never
    a silent switch to a literal search."""


def _compile(q: str, mode: str, case: bool) -> "re.Pattern[str]":
    flags = 0 if case else re.IGNORECASE
    if mode == "regex":
        try:
            return re.compile(q, flags)
        except re.error as exc:
            raise BadPattern(f"not a valid regular expression: {exc}") from exc
    return re.compile(re.escape(q), flags)


def _spans(pat: "re.Pattern[str]", line: str) -> List[List[int]]:
    out = []
    for m in pat.finditer(line):
        if m.end() > m.start():
            out.append([m.start(), m.end()])
        if len(out) >= 20:
            break
    return out


class _Page:
    """Collects one page of hits: skips the first `offset` (they were on earlier pages), keeps the
    next `page`, and notes whether at least one more exists (the "load more" signal)."""

    def __init__(self, offset: int, page: int):
        self.offset, self.page = max(0, offset), max(1, page)
        self.seen = 0
        self.kept = 0
        self.more = False
        self.found: Dict[str, list] = {}

    def add(self, rel: str, hit: Dict[str, Any]) -> bool:
        """False once the page is full AND one more hit proved there is a next page."""
        self.seen += 1
        if self.seen <= self.offset:
            return True
        if self.kept >= self.page:
            self.more = True
            return False
        self.found.setdefault(rel, []).append(hit)
        self.kept += 1
        return True


def _floor(root: str, files: Sequence[str], pat: "re.Pattern[str]", pg: "_Page") -> None:
    for i, rel in enumerate(files):
        checkpoint()                                  # P32: a cancelled job stops, per file
        if i % 500 == 0:
            report(done=i, total=len(files), unit="files", note="searching (pure-Python floor)")
        ab = os.path.join(root, *rel.split("/"))
        try:
            with open(ab, "rb") as fh:
                if b"\0" in fh.read(BINARY_PROBE_BYTES):
                    continue                          # binary: rg skips it too
                fh.seek(0)
                # line by line: a file of any size is searched (P36, G33), never held whole
                for n, raw in enumerate(fh, 1):
                    line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                    sp = _spans(pat, line)
                    if sp and not pg.add(rel, {"line": n, "text": line[:MAX_LINE_CHARS],
                                               "spans": [s_ for s_ in sp
                                                         if s_[0] < MAX_LINE_CHARS]}):
                        return
        except OSError:
            continue


def _rg(root: str, q: str, mode: str, case: bool, allowed: set,
        only: Optional[Sequence[str]], pg: "_Page") -> bool:
    """`rg --json` into the page `pg`; False when rg could not answer (missing, refused the
    pattern) — the caller then answers with the floor AND says so. The pattern goes through `-e`
    after every option, never through a shell, so a pattern can never become an rg flag.

    STREAMED: rg's output is read line by line and rg is stopped the moment the page is full and
    one more hit proved a next page exists (0.0.25 lane S; 0.0.26 P36 pages instead of stopping
    for good). `--sort path` makes the ORDER deterministic, which is what lets page N+1 start
    exactly where page N ended. No `--max-filesize`: big files are searched (P36, G33)."""
    from ..languages import VENDOR_DIRS
    exe = rg_path()
    if not exe:
        return False
    cmd = [exe, "--json", "--no-ignore", "--no-config", "--sort", "path",
           "--max-columns", str(MAX_LINE_CHARS * 2), "--max-columns-preview"]
    cmd.append("--case-sensitive" if case else "--ignore-case")
    if mode != "regex":
        cmd.append("--fixed-strings")
    for v in sorted(VENDOR_DIRS):
        cmd += ["--glob", f"!{v}/"]
    cmd += ["-e", q, "--"]
    cmd += [os.path.join(*p.split("/")) for p in only] if only else ["."]
    try:
        proc = subprocess.Popen(cmd, cwd=root, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, **hidden())
    except (OSError, subprocess.SubprocessError):
        return False
    stopped = eof = False
    try:
        pat = _compile(q, mode, case)
    except BadPattern:
        pat = None
    last_batch = time.monotonic()
    try:
        assert proc.stdout is not None
        # 0.0.26 P32 — no stall timer (Jas: no arbitrary timeouts on search): rg runs until it is
        # done, the page is full, or its job is cancelled (`kill_on_cancel` kills it).
        # While it runs, the page so far is published as the job's partial answer in batches.
        with kill_on_cancel(proc):
            for raw in proc.stdout:
                checkpoint()
                if b'"type":"match"' not in raw[:40]:
                    continue                          # begin/end/context/summary lines
                if pg.seen < pg.offset:
                    # an earlier page's hit: only its path matters (the explorer-set filter), so
                    # it is sliced out, not parsed — a deep page skips ~3x faster
                    rel = _fast_rel(root, raw)
                    if rel is not None:
                        if rel in allowed:
                            pg.add(rel, {})
                            if pg.seen % 20000 == 0:
                                report(done=pg.seen, total=pg.offset, unit="hits",
                                       note=f"skipping to hit {pg.offset}")
                        continue
                try:
                    ev = json.loads(raw)
                except ValueError:
                    continue
                d = ev.get("data") or {}
                path = (d.get("path") or {}).get("text")
                if not path:
                    continue
                rel = str(name_of(os.path.join(root, path), root))
                if rel not in allowed:
                    continue                          # outside the explorer's file set (a venv)
                if pg.seen < pg.offset:               # an earlier page's hit: count, don't shape
                    pg.add(rel, {})
                    continue
                line = ((d.get("lines") or {}).get("text") or "").rstrip("\r\n")
                spans = [[s["start"], s["end"]] for s in d.get("submatches", [])]
                if pat is not None:                   # rg spans are BYTE offsets; re-derive chars
                    spans = _spans(pat, line) or spans
                if not pg.add(rel, {"line": d.get("line_number") or 0,
                                    "text": line[:MAX_LINE_CHARS],
                                    "spans": [s for s in spans if s[0] < MAX_LINE_CHARS]}):
                    stopped = True
                    break                             # page full + one more seen: stop rg
                if pg.kept and time.monotonic() - last_batch >= PARTIAL_EVERY_S:
                    last_batch = time.monotonic()
                    report(done=pg.offset + pg.kept, unit="hits",
                           note=f"{pg.offset + pg.kept} hit(s) so far",
                           partial=_shape(root, pg, partial=True))
            else:
                eof = True
        checkpoint()                                  # killed by a cancel: never a "result"
    finally:
        if not eof and proc.poll() is None:
            proc.kill()                               # page full (or failed mid-read): stop rg
        code = proc.wait()
        proc.stdout.close()
    if stopped:
        return True
    return code in (0, 1)                             # 2 = error (a pattern rg rejects); killed


_PATH_KEY = b'"path":{"text":"'


def _fast_rel(root: str, raw: bytes) -> Optional[str]:
    """The repo path of one rg `match` line without parsing it; None when the path is not a plain
    string there (escapes, or rg's `bytes` form) — the caller then parses the line properly."""
    at = raw.find(_PATH_KEY)
    if at < 0:
        return None
    at += len(_PATH_KEY)
    end = raw.find(b'"', at)
    if end < 0:
        return None
    path = raw[at:end]
    if b"\\" in path:
        return None
    global _LAST_REL
    key, rel = _LAST_REL                          # one tuple, swapped whole: thread-safe
    if key == (root, path):                       # hits of one file come together: one lookup
        return rel
    rel = str(name_of(os.path.join(root, path.decode("utf-8", "replace")), root))
    _LAST_REL = ((root, path), rel)
    return rel


_LAST_REL: Tuple[Any, Optional[str]] = (None, None)


def _size_mb(n: int) -> str:
    return f"{n / (1024 * 1024):.1f} MB"


def _shape(root: str, pg: "_Page", partial: bool = False) -> Dict[str, Any]:
    """The page as the client draws it. Each file over `LARGE_FILE_BYTES` carries its size and a
    note (G33): it WAS searched, and the page says how big it is."""
    files = []
    large = []
    for p, m in sorted(pg.found.items()):
        entry: Dict[str, Any] = {"path": p, "matches": sorted(m, key=lambda x: x["line"])}
        try:
            size = os.path.getsize(os.path.join(root, *p.split("/")))
        except OSError:
            size = 0
        if size > LARGE_FILE_BYTES:
            entry["size"] = size
            entry["size_note"] = f"large file ({_size_mb(size)}) — searched in full"
            large.append(p)
        files.append(entry)
    nxt = pg.offset + pg.kept if pg.more else None
    return {"files": files, "offset": pg.offset, "count": pg.kept,
            "total": pg.offset + pg.kept, "more": pg.more, "next_offset": nxt,
            "large_files": large, "partial": partial}


def text(root: str, q: str, mode: str = "word", case: bool = False,
         only: Optional[Sequence[str]] = None, offset: int = 0,
         page: int = PAGE_MATCHES) -> Dict[str, Any]:
    """Word (literal) or regex search: ONE PAGE of hits starting at `offset` (0.0.26 P36). `more`
    / `next_offset` say whether another page exists — no hit is ever out of reach. `only` limits it
    to those files (the "changed files" / "this session's files" scopes). The answer names its
    `engine`; when rg was present but could not answer, the floor answers and `engine_note` says
    why."""
    q = q or ""
    mode = "regex" if mode == "regex" else "word"
    pg = _Page(int(offset or 0), int(page or PAGE_MATCHES))
    out: Dict[str, Any] = {"sample": False, "q": q, "mode": mode, "engine": engine_name(),
                           "engine_note": "", **_shape(root, pg)}
    if not q.strip():
        return out
    pat = _compile(q, mode, case)                     # BadPattern → 400 with the reason
    files = nav.files_of(root)
    allowed = set(files)
    scope = None
    if only is not None:
        scope = []
        for p in only:
            try:
                rel = nav.contained_rel(root, p)
            except nav.PathRefused:
                continue
            if rel in allowed:
                scope.append(rel)
        if not scope:
            return out
    if not _rg(root, q, mode, case, allowed, scope, pg):
        if out["engine"] == "rg":
            out["engine_note"] = "rg could not answer this pattern — the pure-Python floor did"
        out["engine"] = "python-floor"
        pg = _Page(pg.offset, pg.page)                # rg's half-read page is discarded whole
        _floor(root, sorted(scope) if scope is not None else files, pat, pg)
    out.update(_shape(root, pg))
    return out


# =========================================================================== meaning (R2)

_EMB_LOCK = threading.Lock()
_EMBEDDER: Dict[str, Any] = {}


def index_dir(root: str) -> str:
    from .statedir import ide_dir
    return os.path.join(ide_dir(root), "semantic")


def _extra_installed() -> bool:
    try:
        import model2vec  # noqa: F401
        return True
    except ImportError:
        return False


def _model_dir(allow_network: bool) -> str:
    """The extra's model on THIS disk. Offline is a per-call argument (`local_files_only`), never
    the `HF_HUB_OFFLINE` variable: huggingface_hub reads that once, at import, so setting it for
    the first (offline) load made the user's later "fetch" silently offline too — measured, not
    guessed, when this path was first run against the real extra."""
    from ..memory.embed import MODEL2VEC_MODEL, MODEL_FETCH_TIMEOUT_S, ModelUnavailable
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise ModelUnavailable(f"model2vec's model fetcher is not installed ({exc})") from exc
    try:
        return snapshot_download(MODEL2VEC_MODEL, local_files_only=not allow_network,
                                 etag_timeout=MODEL_FETCH_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 — the hub's error tree is optional-extra territory
        how = "fetched" if allow_network else "found on this machine (loaded offline)"
        raise ModelUnavailable(f"model2vec model '{MODEL2VEC_MODEL}' could not be {how}: "
                               f"{type(exc).__name__}: {str(exc)[:200]}") from exc


def _load_embedder(allow_network: bool = False) -> Any:
    """The `[embeddings]` extra's model2vec embedder — memory's own `Model2VecEmbedder`, with the
    same model id, so its stamp is memory's — loaded from a LOCAL directory unless the user asked
    to fetch. Raises `ModelUnavailable`; never returns the hashing embedder."""
    from ..memory.embed import MODEL2VEC_MODEL, Model2VecEmbedder, ModelUnavailable
    if EMBEDDER_FACTORY is not None:
        return EMBEDDER_FACTORY()
    with _EMB_LOCK:
        if "m" in _EMBEDDER:
            return _EMBEDDER["m"]
        try:
            from model2vec import StaticModel
        except ImportError as exc:
            raise ModelUnavailable(f"model2vec is not installed ({exc})") from exc
        path = _model_dir(allow_network)
        try:
            model = StaticModel.from_pretrained(path)
        except Exception as exc:  # noqa: BLE001 — a corrupt local model is a degrade, said
            raise ModelUnavailable(f"model2vec could not load the local model at {path}: "
                                   f"{type(exc).__name__}: {str(exc)[:200]}") from exc
        emb = Model2VecEmbedder(MODEL2VEC_MODEL, model=model)
        _EMBEDDER["m"] = emb
        return emb


def status() -> Dict[str, Any]:
    """Whether meaning search can answer here — and if not, exactly why and the one fix."""
    from ..memory.embed import ModelUnavailable, embedder_identity
    if EMBEDDER_FACTORY is None and not _extra_installed():
        return {"available": False, "state": "not-installed",
                "note": "semantic unavailable — the optional embeddings extra is not installed",
                "install": fix("not-installed")}
    try:
        emb = _load_embedder()
    except ModelUnavailable as exc:
        return {"available": False, "state": "model-not-local",
                "note": "semantic unavailable — the embeddings extra is installed but its model "
                        "is not on this machine (mokata loads it offline; fetching is your call)",
                "install": fix("model-not-local"), "detail": str(exc)[:300], "can_fetch": True}
    eid, dim = embedder_identity(emb)
    return {"available": True, "state": "ready", "embedder": eid, "dim": dim}


def fetch_model() -> Dict[str, Any]:
    """The ONE network call meaning search can make — and only when the user clicks for it."""
    from ..memory.embed import ModelUnavailable
    if EMBEDDER_FACTORY is None and not _extra_installed():
        return status()
    try:
        _load_embedder(allow_network=True)
    except ModelUnavailable as exc:
        return {"available": False, "state": "fetch-failed", "note": str(exc)[:300],
                "install": fix("model-not-local")}
    return status()


# 0.0.26 O13 — Go. Measured on kubernetes (7.3M lines, 18k .go files): the meaning index held 34
# chunks from 9 files, because only Python/TS/JS were cut. mokata has no Go parser (the code graph
# is code-review-graph's), so Go is cut LEXICALLY at its top-level declarations — `func`,
# `func (recv)`, `type X struct|interface` all start at column 0 in gofmt'd code — and each chunk
# says so (`cut: "lexical"`).
_GO_DECL = re.compile(r"^(?:func\s+(?:\((?P<recv>[^)]*)\)\s*)?(?P<fn>[A-Za-z_]\w*)"
                      r"|type\s+(?P<ty>[A-Za-z_]\w*)\s+(?P<tk>struct|interface)\b)")


def _go_chunks(rel: str, text_: str) -> List[Dict[str, Any]]:
    lines = text_.splitlines()
    starts = []
    for i, line in enumerate(lines, 1):
        m = _GO_DECL.match(line)
        if m:
            if m.group("fn"):
                starts.append((i, m.group("fn"), "method" if m.group("recv") else "function"))
            else:
                starts.append((i, m.group("ty"), "class"))
    out = []
    for n, (line, name, kind) in enumerate(starts):
        nxt = starts[n + 1][0] - 1 if n + 1 < len(starts) else len(lines)
        end = min(nxt, line - 1 + CHUNK_MAX_LINES)
        body = "\n".join(lines[line - 1:end])
        out.append({"name": name, "kind": kind, "line": line, "end": end, "cut": "lexical",
                    "text": f"{rel} {kind} {name}\n{body}"[:CHUNK_MAX_CHARS]})
    return out


def chunks_of(rel: str, text_: str) -> List[Dict[str, Any]]:
    """Function- and class-level chunks, cut by the graph layer's parser (`nav.outline`); Go is cut
    lexically at its top-level declarations (`_go_chunks`). A file with no definition that can be
    named contributes one `module` chunk (its head)."""
    if rel.endswith(".go"):
        out = _go_chunks(rel, text_)
        if out:
            return out
        lines = text_.splitlines()
        return [{"name": rel.rsplit("/", 1)[-1], "kind": "module", "line": 1,
                 "end": min(len(lines), 40),
                 "text": f"{rel}\n" + "\n".join(lines[:40])[:CHUNK_MAX_CHARS]}] if lines else []
    ol = nav.outline(rel, text_)
    lines = text_.splitlines()
    out = []
    for s in ol["symbols"]:
        end = s.get("end") or min(len(lines), s["line"] + 30)
        body = "\n".join(lines[s["line"] - 1:min(end, s["line"] - 1 + CHUNK_MAX_LINES)])
        out.append({"name": s["name"], "kind": s["kind"], "line": s["line"], "end": end,
                    "text": f"{rel} {s['kind']} {s['name']}\n{body}"[:CHUNK_MAX_CHARS]})
    if not out and lines:
        out.append({"name": rel.rsplit("/", 1)[-1], "kind": "module", "line": 1,
                    "end": min(len(lines), 40),
                    "text": f"{rel}\n" + "\n".join(lines[:40])[:CHUNK_MAX_CHARS]})
    return out


_IDX_LOCK = threading.Lock()
_SEMANTIC_EXTS = (".py", ".ts", ".tsx", ".js", ".jsx", ".mts", ".cts", ".go")


# ---------------------------------------------------------------------- the index (0.0.26 O13)
# Built to hold on a million-line repo (Jas, 2026-10-21: "working on code bases with millions of
# lines of code — so don't put non-reasonable timeouts"). Measured on django (0.56M lines, 44k
# chunks) with the 0.0.25 shape — ONE JSON with every vector base64'd inside, rebuilt only at the
# end, and a pure-Python cosine per chunk per query — a build was 43 s with no progress and nothing
# kept on Ctrl+C, and EVERY query took 6.4 s (re-read the whole JSON, then 44k Python cosines).
#
#   * vectors live in ONE float32 matrix file (`vectors-<gen>.f32`), metadata in `index.json`
#     (which names its vectors file — a crash between the two writes can never pair a metadata
#     file with another build's vectors); a query is ONE numpy matrix-vector product over a
#     matrix cached in memory until the files change;
#   * chunks are embedded in BATCHES (`embed_many`) when the embedder offers it;
#   * the build reports progress (files done / total, chunks, elapsed), CHECKPOINTS every
#     `CHECKPOINT_S` (and on Ctrl+C), and a re-run RESUMES: every file already embedded is reused
#     by (mtime, size) — the same rule that makes the everyday re-run incremental;
#   * no time limit anywhere; a big FIRST build behind a query runs in the background (one per
#     repo), and the query says so instead of waiting.

CHECKPOINT_S = 60.0
FIRST_CHECKPOINT_S = 5.0
EMBED_BATCH = 512
# How many files a first build may take INLINE, inside the query that needed it. A bigger first
# build goes to the background and the query answers "building" — never a query that waits for
# minutes. (A repo this small builds in a second or two.)
INLINE_BUILD_MAX_FILES = 400
PROGRESS_EVERY_S = 0.5
# How often a query re-checks the repo for changed files (a walk + a stat per file: ~1 s on django,
# ~10 s on a 70k-file repo). Between checks a query ranks against the index as it stands; on a big
# repo the check itself runs in the background.
REFRESH_EVERY_S = 30.0
_LAST_REFRESH: Dict[str, float] = {}


def _index_paths(root: str) -> Tuple[str, str]:
    d = index_dir(root)
    return d, os.path.join(d, "index.json")


_META: Dict[str, Any] = {}


def _read_meta(root: str) -> Dict[str, Any]:
    """The index's metadata, parsed once per version of the file (a big repo's is tens of MB)."""
    _d, path = _index_paths(root)
    try:
        st = os.stat(path)
    except OSError:
        return {}
    sig = (st.st_mtime_ns, st.st_size)
    hit = _META.get(path)
    if hit and hit[0] == sig:
        return hit[1]
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    data = data if isinstance(data, dict) else {}
    _META[path] = (sig, data)
    return data


def _read_vectors(root: str, meta: Dict[str, Any]) -> Any:
    """`meta`'s vectors as a read-only (rows, dim) float32 MEMORY MAP (0.0.26 P36, G32): the OS
    pages it in on demand, so a 200 MB matrix on kubernetes is not copied into the process; None
    when there is none. Without numpy (the extra pulls it in, so only a bare install) the raw
    bytes."""
    name = meta.get("vectors")
    dim = int(meta.get("dim") or 0)
    if not name or not dim:
        return None
    path = os.path.join(index_dir(root), name)
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    try:
        import numpy as np
    except ImportError:
        try:
            with open(path, "rb") as fh:
                return fh.read()
        except OSError:
            return None
    rows = size // (4 * dim)
    if rows == 0:
        return np.zeros((0, dim), dtype="<f4")
    try:
        return np.memmap(path, dtype="<f4", mode="r", shape=(rows, dim))
    except (OSError, ValueError):
        return None


def _row_bytes(src: Any, rows: Sequence[int], dim: int) -> bytes:
    """Rows `rows` of an old vectors source (memmap or bytes), packed, for the next write."""
    if src is None:
        return b""
    if isinstance(src, (bytes, bytearray)):
        w = 4 * dim
        return b"".join(bytes(src[r * w:(r + 1) * w]) for r in rows)
    if rows and rows[-1] - rows[0] + 1 == len(rows):        # contiguous: one slice
        return src[rows[0]:rows[-1] + 1].tobytes()
    return b"".join(src[r].tobytes() for r in rows)


def _n_rows(src: Any, dim: int) -> int:
    if src is None:
        return 0
    if isinstance(src, (bytes, bytearray)):
        return len(src) // (4 * dim) if dim else 0
    return int(src.shape[0])


def _vectors_bytes(emb: Any, texts: List[str], dim: int) -> bytes:
    """`texts` embedded, as packed little-endian float32 rows."""
    many = getattr(emb, "embed_many", None)
    if callable(many):
        arr = many(texts)
        try:
            return arr.astype("<f4").tobytes()
        except AttributeError:
            return b"".join(struct.pack(f"<{dim}f", *v) for v in arr)
    return b"".join(struct.pack(f"<{dim}f", *emb(t)) for t in texts)


def _write_index(root: str, meta: Dict[str, Any], blobs: Dict[str, Any], dim: int,
                 old_src: Any = None) -> None:
    """Write `meta` + the vectors it points at, atomically and in an order that can never pair
    metadata with another build's vectors: new vectors file first (a fresh name), then the JSON
    that names it, then the old vectors file is removed. A blob is new bytes, or a list of row
    numbers in `old_src` (the previous build's map) — reused work is copied file to file, never
    gathered in memory first (P36: on kubernetes that was the whole 200 MB, every 30 s)."""
    d, path = _index_paths(root)
    os.makedirs(d, exist_ok=True)
    old = _read_meta(root).get("vectors")
    gen = f"{int(time.time() * 1000)}-{os.getpid()}-{threading.get_ident()}"
    vec_name = f"vectors-{gen}.f32"
    row = 0
    out_files: Dict[str, Any] = {}
    with open(os.path.join(d, vec_name), "wb") as vf:
        for rel, f in meta["files"].items():
            b = blobs.get(rel, b"")
            vf.write(_row_bytes(old_src, b, dim) if isinstance(b, list) else b)
            chunks = []
            for c in f.get("chunks", []):
                chunks.append(dict(c, r=row))       # COPIES: a caller's records keep their rows
                row += 1
            out_files[rel] = dict(f, chunks=chunks)
    meta = dict(meta, files=out_files, vectors=vec_name, rows=row, dim=dim)
    tmp = path + f".tmp-{gen}"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(meta, fh)
    os.replace(tmp, path)
    _MATRIX.pop(os.path.abspath(root), None)   # let go of the old map before it is removed
    for name in os.listdir(d):
        if name.startswith("vectors-") and name != vec_name and (
                name == old or name.endswith(".f32")):
            try:
                os.remove(os.path.join(d, name))
            except OSError:
                pass


def build_index(root: str, emb: Any, *,
                on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
                checkpoint_s: float = CHECKPOINT_S) -> Dict[str, Any]:
    """Bring the local index up to date: re-embed only files whose (mtime, size) moved, drop files
    that are gone, rebuild from scratch when the embedder's identity changed (vectors of two
    embedders never meet — memory's stamp rule). Written under `.mokata/` (gitignored).

    Reports progress to `on_progress({"done", "total", "chunks", "embedded", "elapsed"})`,
    checkpoints every `checkpoint_s` and on Ctrl+C (KeyboardInterrupt, re-raised after the
    checkpoint is written), so an interrupted build resumes where it stopped. No time limit."""
    from ..memory.embed import embedder_identity
    eid, dim = embedder_identity(emb)
    with _IDX_LOCK:
        old = _read_meta(root)
        if old.get("schema") != INDEX_SCHEMA or old.get("embedder") != eid or \
                old.get("dim") != dim:
            old, old_vec = {}, None
        else:
            old_vec = _read_vectors(root, old)
        prev = old.get("files", {}) if isinstance(old.get("files"), dict) else {}
        width = 4 * dim if dim else 0
        have = _n_rows(old_vec, dim)

        def old_blob(rec: Dict[str, Any]) -> Optional[List[int]]:
            """The previous build's rows for this file — a REFERENCE, copied at write time."""
            rows = [c.get("r") for c in rec.get("chunks", [])]
            if not width or any(not isinstance(r, int) or not 0 <= r < have for r in rows):
                return None
            return rows

        todo = [rel for rel in nav.walk_files(root) if rel.endswith(_SEMANTIC_EXTS)]
        total = len(todo)
        files: Dict[str, Any] = {}
        blobs: Dict[str, Any] = {}
        embedded = 0
        t0 = last_said = last_ckpt = time.monotonic()
        # 0.0.26 P32 — the FIRST checkpoints come early (5 s, 10 s, 20 s … up to `checkpoint_s`):
        # a query during a first build ranks what a checkpoint holds, so partial hits appear
        # seconds into a build, not a minute in. Later ones are spaced out (each rewrites the
        # vectors file).
        gap = min(checkpoint_s, FIRST_CHECKPOINT_S)
        pending: List[Tuple[str, Dict[str, Any], List[Dict[str, Any]]]] = []
        pend_texts: List[str] = []

        def flush() -> None:
            nonlocal embedded
            if not pending:
                return
            data = _vectors_bytes(emb, pend_texts, dim)
            at = 0
            for rel, rec, rows in pending:
                n = len(rows) * width
                blobs[rel] = data[at:at + n]
                at += n
                files[rel] = rec
                embedded += len(rows)
            pending.clear()
            pend_texts.clear()

        def snapshot(complete: bool) -> None:
            # Files not reached yet keep their previous (still mtime-checked) entries: a checkpoint
            # never forgets work an earlier build did.
            keep = {rel: prev[rel] for rel in prev if rel not in files}
            kb = {}
            for rel, rec in keep.items():
                b = old_blob(rec)
                if b is not None:
                    kb[rel] = b
            meta = {"schema": INDEX_SCHEMA, "embedder": eid, "dim": dim, "built": time.time(),
                    "complete": complete,
                    "files": {**{r: keep[r] for r in kb}, **files}}
            _write_index(root, meta, {**kb, **blobs}, dim, old_vec)

        def say(done: int) -> None:
            report(done=done, total=total, unit="files",
                   note=f"{embedded} chunks embedded")          # P32: the job's n/N
            if on_progress is not None:
                on_progress({"done": done, "total": total, "embedded": embedded,
                             "chunks": sum(len(f.get("chunks", [])) for f in files.values()),
                             "elapsed": time.monotonic() - t0})

        try:
            for i, rel in enumerate(todo, 1):
                checkpoint()                  # P32: Cancel stops here — the work is kept below
                ab = os.path.join(root, *rel.split("/"))
                try:
                    st = os.stat(ab)
                except OSError:
                    continue
                hit = prev.get(rel)
                if hit and hit.get("mtime") == st.st_mtime and hit.get("size") == st.st_size:
                    b = old_blob(hit)
                    if b is not None:
                        files[rel], blobs[rel] = hit, b
                        continue
                # P36 (G33): a file over 1 MB is embedded too (Go's 1.4 MB checker.go on T)
                try:
                    with open(ab, encoding="utf-8", errors="replace") as fh:
                        src = fh.read()
                except OSError:
                    continue
                rows = []
                bad = ""
                try:
                    cut = chunks_of(rel, src)
                except (RecursionError, ValueError, MemoryError) as exc:
                    bad = type(exc).__name__                    # said in the index (P36)
                    # One pathological file (machine-generated, nested thousands deep) is indexed by
                    # its head, never allowed to take a million-line build down with it.
                    cut = [{"name": rel.rsplit("/", 1)[-1], "kind": "module", "line": 1,
                            "end": min(len(src.splitlines()), 40),
                            "text": f"{rel}\n" + "\n".join(src.splitlines()[:40])[:CHUNK_MAX_CHARS]}]
                for c in cut:
                    rows.append({k: c[k] for k in ("name", "kind", "line", "end")})
                    pend_texts.append(c["text"])
                rec = {"mtime": st.st_mtime, "size": st.st_size, "chunks": rows}
                if bad:
                    rec["unparsed"] = bad          # indexed by its head; the reason is kept
                pending.append((rel, rec, rows))
                if len(pend_texts) >= EMBED_BATCH:
                    flush()
                now = time.monotonic()
                if now - last_said >= PROGRESS_EVERY_S:
                    say(i)
                    last_said = now
                if now - last_ckpt >= gap:
                    flush()
                    snapshot(complete=False)
                    last_ckpt = time.monotonic()
                    gap = min(checkpoint_s, gap * 2)
            flush()
        except (KeyboardInterrupt, Cancelled):
            pending.clear()
            pend_texts.clear()
            snapshot(complete=False)          # resume from here next time
            raise
        say(total)
        if embedded or not old.get("complete") or set(files) != set(prev):
            snapshot(complete=True)
            meta = _read_meta(root)
        else:
            meta = old                         # nothing moved: no rewrite, the cached matrix stays
    return {"index": meta, "embedded": embedded, "complete": True,
            "elapsed": time.monotonic() - t0}


# ---- the in-memory matrix a query ranks against (one per repo, reloaded when the files change)
_MATRIX: Dict[str, Any] = {}


def _matrix(root: str, meta: Dict[str, Any]):
    """(rows, matrix, norms, file_of_row, files) for `meta`'s vectors, cached until the vectors
    file changes. The matrix is a memory map (P36); `file_of_row` lets a scope be one vectorised
    mask. Without numpy: (rows, list-of-vectors, None, None, None)."""
    name = meta.get("vectors") or ""
    key = (os.path.abspath(root), name)
    hit = _MATRIX.get(key[0])
    if hit and hit[0] == key:
        return hit[1]
    dim = int(meta.get("dim") or 0)
    rows = []
    file_of_row = []
    files = []
    for rel, f in meta.get("files", {}).items():
        files.append(rel)
        for c in f.get("chunks", []):
            rows.append((rel, c))
            file_of_row.append(len(files) - 1)
    src = _read_vectors(root, meta)
    try:
        import numpy as np
    except ImportError:
        raw = src or b""
        vecs = [list(struct.unpack_from(f"<{dim}f", raw, i * 4 * dim)) for i in range(len(rows))]
        val = (rows, vecs, None, None, None)
    else:
        m = src if src is not None and dim else np.zeros((0, max(dim, 1)), dtype="<f4")
        m = m[:len(rows)]
        rows = rows[:m.shape[0]]
        norms = np.linalg.norm(m, axis=1).astype("<f4") if m.shape[0] else np.zeros(0, "<f4")
        val = (rows, m, norms, np.asarray(file_of_row[:len(rows)], dtype=np.int32), files)
    _MATRIX[key[0]] = (key, val)
    return val


def _rank(qv: Sequence[float], mat: Tuple[Any, ...], scope: Optional[set], floor: float,
          limit: int) -> List[Tuple[float, str, Dict[str, Any]]]:
    """The best `limit` (score, path, chunk) above `floor`, best first — ONE matrix-vector product
    and an `argpartition` (P36, G32), never a Python loop over every chunk."""
    rows, m, norms, file_of_row, files = mat
    if norms is None:                                  # no numpy: the plain loop
        from ..memory.embed import cosine
        got = [(cosine(qv, v), rel, c) for v, (rel, c) in zip(m, rows)
               if scope is None or rel in scope]
        got = [g for g in got if g[0] > floor]
        got.sort(key=lambda r: (-r[0], r[1], r[2]["line"]))
        return got[:limit]
    import numpy as np
    if not rows:
        return []
    q = np.asarray(qv, dtype=np.float32)
    qn = float(np.linalg.norm(q)) or 1.0
    sc = (m @ q) / (np.where(norms == 0, 1.0, norms) * qn)
    if scope is not None:
        keep = np.fromiter((i for i, rel in enumerate(files) if rel in scope), dtype=np.int32)
        sc = np.where(np.isin(file_of_row, keep), sc, -np.inf)
    cand = np.flatnonzero(sc > floor)
    if cand.size > limit * 4:                          # a margin keeps the tie-break exact
        part = np.argpartition(-sc[cand], limit * 4)[:limit * 4]
        cand = cand[part]
    got = [(float(sc[i]), rows[i][0], rows[i][1]) for i in cand.tolist()]
    got.sort(key=lambda r: (-r[0], r[1], r[2]["line"]))
    return got[:limit]


# ---- the first big build runs in the background (one per repo per process) ------------------
_BUILDS: Dict[str, Dict[str, Any]] = {}
_BUILDS_LOCK = threading.Lock()


def build_state(root: str) -> Optional[Dict[str, Any]]:
    """The background build for `root`, if one ran in this process: state + progress + error."""
    with _BUILDS_LOCK:
        st = _BUILDS.get(os.path.abspath(root))
        return dict(st) if st else None


def _start_background(root: str, emb: Any) -> Dict[str, Any]:
    key = os.path.abspath(root)
    with _BUILDS_LOCK:
        st = _BUILDS.get(key)
        if st and st.get("state") == "building":
            return dict(st)
        st = {"state": "building", "started": time.time(), "progress": {}}
        _BUILDS[key] = st

    def progress(p: Dict[str, Any]) -> None:
        with _BUILDS_LOCK:
            st["progress"] = dict(p)

    from .jobs import background, installed

    def run() -> None:
        try:
            build_index(root, emb, on_progress=progress)
            with _BUILDS_LOCK:
                st["state"] = "built"
        except Cancelled:
            with _BUILDS_LOCK:               # kept + resumable: the next query carries on
                st["state"] = "cancelled"
            raise
        except Exception as exc:  # noqa: BLE001 — a failed build is REPORTED on the next query
            with _BUILDS_LOCK:
                st["state"], st["error"] = "failed", f"{type(exc).__name__}: {exc}"[:300]
            if installed() is not None:
                raise                        # the job says FAILED, with this error, in Activity

    def ended() -> None:
        with _BUILDS_LOCK:
            if st.get("state") == "building":
                st["state"] = "cancelled"    # cancelled before it started
    # 0.0.26 P32 — an `index.meaning` JOB of the serving IDE: n/N files in Activity, Cancel keeps
    # what is embedded (checkpointed) and the next query resumes it.
    job = background("index.meaning", {"root": key}, run, label="meaning index",
                     scope=os.path.basename(key), on_end=ended)
    with _BUILDS_LOCK:
        st["job_id"] = job.id if job is not None else None       # P39: Welcome follows it
        return dict(st)


def _consented(root: str) -> bool:
    """May a search write this repo's meaning index? Yes in a repo mokata governs (O13: the first
    search builds it); elsewhere only once the user built one (the gated Welcome/Health build)."""
    from .statedir import governed
    return governed(root) or bool(_read_meta(root).get("files"))


def _needs_first_build(root: str, emb: Any) -> bool:
    from ..memory.embed import embedder_identity
    meta = _read_meta(root)
    eid, dim = embedder_identity(emb)
    return not (meta.get("complete") and meta.get("schema") == INDEX_SCHEMA
                and meta.get("embedder") == eid and meta.get("dim") == dim)


def meaning(root: str, q: str, only: Optional[Sequence[str]] = None,
            limit: int = MEANING_LIMIT) -> Dict[str, Any]:
    """Ranked by MEANING, or an explicit unavailable state. Never a word answer under this name.

    0.0.26 O13 — a FIRST build bigger than `INLINE_BUILD_MAX_FILES` runs in the background and
    this answer says `state: "building"` with its progress (answering from whatever part of the
    index a checkpoint already holds); once the index is complete, each query brings it up to date
    incrementally (changed files only) and ranks with one matrix product."""
    from ..memory.embed import noise_floor_of
    st = status()
    base = {"sample": False, "q": q, "kind": "meaning", "results": [], **st}
    if not st["available"]:
        return base
    if not (q or "").strip():
        return base
    emb = _load_embedder()
    embedded_now = 0
    if not _consented(root):
        # 0.0.26 P39 — a folder that has not run setup gets no index written for it by a search:
        # the first build is the user's call (Welcome's gated Build, which names its cost).
        return {**base, "state": "unbuilt", "install": fix("unbuilt"),
                "note": "no meaning index for this folder yet — build it from Welcome (it names "
                        "the time and disk it takes), or run the command below"}
    if _needs_first_build(root, emb):
        running = build_state(root)
        small = sum(1 for rel in nav.walk_files(root) if rel.endswith(_SEMANTIC_EXTS)) \
            <= INLINE_BUILD_MAX_FILES if not running else False
        if small:
            embedded_now = build_index(root, emb)["embedded"]
        else:
            bg = running if running and running.get("state") in ("building", "failed") \
                else _start_background(root, emb)
            base["state"] = "building" if bg.get("state") == "building" else bg.get("state")
            base["building"] = bg
            if bg.get("state") == "failed":
                base["note"] = f"the meaning index build failed — {bg.get('error')}"
    else:
        key = os.path.abspath(root)
        if len(_read_meta(root).get("files", {})) <= INLINE_BUILD_MAX_FILES:
            embedded_now = build_index(root, emb)["embedded"]     # small: always exact, ~ms
        elif time.monotonic() - _LAST_REFRESH.get(key, -1e9) >= REFRESH_EVERY_S:
            _LAST_REFRESH[key] = time.monotonic()
            running = build_state(root)
            if not (running and running.get("state") == "building"):
                _start_background(root, emb)           # incremental, never in the query's way
    meta = _read_meta(root)
    if not meta.get("files"):
        base["embedded_now"] = embedded_now
        base["indexed_chunks"] = 0
        return base
    qv = emb(q)
    floor = noise_floor_of(emb)
    scope = None
    if only is not None:
        scope = set()
        for p in only:
            try:
                scope.add(nav.contained_rel(root, p))
            except nav.PathRefused:
                continue
    mat = _matrix(root, meta)
    rows = mat[0]
    scored = _rank(qv, mat, scope, floor, limit)
    base["results"] = [{"score": round(s, 4), "path": rel, "name": c["name"], "kind": c["kind"],
                        "line": c["line"], "end": c["end"], "link": "graph-symbol"
                        if c["kind"] != "module" else "file"}
                       for s, rel, c in scored]
    base["noise_floor"] = round(floor, 4)
    base["indexed_chunks"] = len(rows)
    base["embedded_now"] = embedded_now
    base["index_complete"] = bool(meta.get("complete"))
    base["unparsed_files"] = sum(1 for f in meta.get("files", {}).values() if f.get("unparsed"))
    return base


__all__ = ["text", "meaning", "status", "fetch_model", "chunks_of", "build_index", "build_state",
           "engine_name",
           "BadPattern", "fix", "index_dir"]
