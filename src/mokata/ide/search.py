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

import base64
import json
import os
import re
import shutil
import struct
import subprocess
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ..quiet_spawn import hidden
from . import nav
from ..errors import MokataError
from ..repo_paths import name_of

MAX_MATCHES = 2000
MAX_LINE_CHARS = 400
MAX_FILE_BYTES = 1024 * 1024
RG_TIMEOUT = 20.0
INSTALL_LINE = "pip install 'mokata[embeddings]'"
CHUNK_MAX_LINES = 80
CHUNK_MAX_CHARS = 2400
INDEX_SCHEMA = 1
MEANING_LIMIT = 20

# A seam for tests (and nothing else): when set, it is called instead of loading model2vec. It must
# return an embedder (a callable text -> vector with `embedder_id`/`dim`) or raise. Production
# leaves it None — and production NEVER substitutes the hashing embedder.
EMBEDDER_FACTORY: Optional[Callable[[], Any]] = None


# =========================================================================== word / regex

def rg_path() -> Optional[str]:
    return shutil.which("rg")


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


def _floor(root: str, files: Sequence[str], pat: "re.Pattern[str]") -> Tuple[Dict[str, list], int, bool]:
    found: Dict[str, list] = {}
    total = 0
    for rel in files:
        ab = os.path.join(root, *rel.split("/"))
        try:
            if os.path.getsize(ab) > MAX_FILE_BYTES:
                continue
            with open(ab, "rb") as fh:
                data = fh.read(MAX_FILE_BYTES + 1)
        except OSError:
            continue
        if b"\0" in data[:8192]:
            continue                                  # binary: rg skips it too
        text = data.decode("utf-8", errors="replace")
        for n, line in enumerate(text.splitlines(), 1):
            sp = _spans(pat, line)
            if sp:
                found.setdefault(rel, []).append({"line": n, "text": line[:MAX_LINE_CHARS],
                                                  "spans": [s for s in sp if s[0] < MAX_LINE_CHARS]})
                total += 1
                if total >= MAX_MATCHES:
                    return found, total, True
    return found, total, False


def _rg(root: str, q: str, mode: str, case: bool, allowed: set,
        only: Optional[Sequence[str]]) -> Optional[Tuple[Dict[str, list], int, bool]]:
    """`rg --json`; None when rg could not answer (missing, refused the pattern, timed out) — the
    caller then answers with the floor AND says so. The pattern goes through `-e` after every
    option, never through a shell, so a pattern can never become an rg flag.

    STREAMED (0.0.25 lane S): rg's output is read line by line and rg is stopped the moment
    MAX_MATCHES are in. It used to be buffered whole first — on a common word over a 1M-line repo
    that was every match in the repo (+~100 MB RSS) only to keep the first 2000.
    `--sort path` makes WHICH 2000 deterministic (the same query gives the same answer). It makes rg
    walk single-threaded; measured on the 1M-line scale repo it costs nothing that matters — a full
    scan for a rare word 0.17 s sorted vs 0.18 s unsorted, a common word stops after its first
    2000 matches in ~0.04 s — so determinism wins."""
    from ..languages import VENDOR_DIRS
    exe = rg_path()
    if not exe:
        return None
    cmd = [exe, "--json", "--no-ignore", "--no-config", "--sort", "path", "--max-filesize", "1M",
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
        return None
    timer = threading.Timer(RG_TIMEOUT, proc.kill)    # a hung rg is stopped, never waited on
    timer.daemon = True
    timer.start()
    found: Dict[str, list] = {}
    total = 0
    capped = eof = False
    try:
        pat = _compile(q, mode, case)
    except BadPattern:
        pat = None
    try:
        assert proc.stdout is not None
        for raw in proc.stdout:
            try:
                ev = json.loads(raw)
            except ValueError:
                continue
            if ev.get("type") != "match":
                continue
            d = ev["data"]
            path = (d.get("path") or {}).get("text")
            if not path:
                continue
            rel = str(name_of(os.path.join(root, path), root))
            if rel not in allowed:
                continue                              # outside the explorer's file set (a venv)
            line = ((d.get("lines") or {}).get("text") or "").rstrip("\r\n")
            spans = [[s["start"], s["end"]] for s in d.get("submatches", [])]
            if pat is not None:                       # rg spans are BYTE offsets; re-derive chars
                spans = _spans(pat, line) or spans
            found.setdefault(rel, []).append({"line": d.get("line_number") or 0,
                                              "text": line[:MAX_LINE_CHARS],
                                              "spans": [s for s in spans if s[0] < MAX_LINE_CHARS]})
            total += 1
            if total >= MAX_MATCHES:
                capped = True
                break                                 # enough: stop rg instead of draining it
        else:
            eof = True
    finally:
        if not eof and proc.poll() is None:
            proc.kill()                               # capped (or failed mid-read): stop rg now
        code = proc.wait()
        timer.cancel()
        proc.stdout.close()
    if capped:
        return found, total, True
    if code not in (0, 1):
        return None                                   # 2 = error (a pattern rg rejects); killed
    return found, total, False


def text(root: str, q: str, mode: str = "word", case: bool = False,
         only: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """Word (literal) or regex search. `only` limits it to those files (the "changed files only"
    toggle). The answer names its `engine`; when rg was present but could not answer, the floor
    answers and `engine_note` says why."""
    q = q or ""
    mode = "regex" if mode == "regex" else "word"
    out: Dict[str, Any] = {"sample": False, "q": q, "mode": mode, "files": [], "total": 0,
                           "truncated": False, "engine": engine_name(), "engine_note": ""}
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
    got = _rg(root, q, mode, case, allowed, scope)
    if got is None:
        if out["engine"] == "rg":
            out["engine_note"] = "rg could not answer this pattern — the pure-Python floor did"
        out["engine"] = "python-floor"
        got = _floor(root, scope if scope is not None else files, pat)
    found, total, trunc = got
    out["files"] = [{"path": p, "matches": sorted(m, key=lambda x: x["line"])}
                    for p, m in sorted(found.items())]
    out["total"], out["truncated"] = total, trunc
    return out


# =========================================================================== meaning (R2)

_EMB_LOCK = threading.Lock()
_EMBEDDER: Dict[str, Any] = {}


def index_dir(root: str) -> str:
    from ..tdd_state import state_dir
    return os.path.join(os.path.dirname(state_dir(root)), "ide", "semantic")


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
                "install": INSTALL_LINE}
    try:
        emb = _load_embedder()
    except ModelUnavailable as exc:
        return {"available": False, "state": "model-not-local",
                "note": "semantic unavailable — the embeddings extra is installed but its model "
                        "is not on this machine (mokata loads it offline; fetching is your call)",
                "install": INSTALL_LINE, "detail": str(exc)[:300], "can_fetch": True}
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
                "install": INSTALL_LINE}
    return status()


def chunks_of(rel: str, text_: str) -> List[Dict[str, Any]]:
    """Function- and class-level chunks, cut by the graph layer's parser (`nav.outline`). A file
    with no definition the graph can name contributes one `module` chunk (its head)."""
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


def _pack(vec: Sequence[float]) -> str:
    return base64.b64encode(struct.pack(f"<{len(vec)}f", *vec)).decode("ascii")


def _unpack(s: str) -> List[float]:
    raw = base64.b64decode(s)
    return list(struct.unpack(f"<{len(raw) // 4}f", raw))


_IDX_LOCK = threading.Lock()
_SEMANTIC_EXTS = (".py", ".ts", ".tsx", ".js", ".jsx", ".mts", ".cts")


def build_index(root: str, emb: Any) -> Dict[str, Any]:
    """Bring the local index up to date: re-embed only files whose (mtime, size) moved, drop files
    that are gone, rebuild from scratch when the embedder's identity changed (vectors of two
    embedders never meet — memory's stamp rule). Written atomically under `.mokata/`."""
    from ..memory.embed import embedder_identity
    eid, dim = embedder_identity(emb)
    path = os.path.join(index_dir(root), "index.json")
    with _IDX_LOCK:
        try:
            with open(path, encoding="utf-8") as fh:
                old = json.load(fh)
        except (OSError, ValueError):
            old = {}
        if not isinstance(old, dict) or old.get("schema") != INDEX_SCHEMA or \
                old.get("embedder") != eid or old.get("dim") != dim:
            old = {}
        prev = old.get("files", {}) if isinstance(old.get("files"), dict) else {}
        files: Dict[str, Any] = {}
        embedded = 0
        for rel in nav.walk_files(root):
            if not rel.endswith(_SEMANTIC_EXTS):
                continue
            ab = os.path.join(root, *rel.split("/"))
            try:
                st = os.stat(ab)
            except OSError:
                continue
            hit = prev.get(rel)
            if hit and hit.get("mtime") == st.st_mtime and hit.get("size") == st.st_size:
                files[rel] = hit
                continue
            if st.st_size > MAX_FILE_BYTES:
                continue
            try:
                with open(ab, encoding="utf-8", errors="replace") as fh:
                    src = fh.read()
            except OSError:
                continue
            rows = []
            for c in chunks_of(rel, src):
                row = {k: c[k] for k in ("name", "kind", "line", "end")}
                row["v"] = _pack(emb(c["text"]))
                rows.append(row)
                embedded += 1
            files[rel] = {"mtime": st.st_mtime, "size": st.st_size, "chunks": rows}
        data = {"schema": INDEX_SCHEMA, "embedder": eid, "dim": dim, "built": time.time(),
                "files": files}
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + f".tmp-{os.getpid()}-{threading.get_ident()}"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
    return {"index": data, "embedded": embedded}


def meaning(root: str, q: str, only: Optional[Sequence[str]] = None,
            limit: int = MEANING_LIMIT) -> Dict[str, Any]:
    """Ranked by MEANING, or an explicit unavailable state. Never a word answer under this name."""
    from ..memory.embed import cosine, noise_floor_of
    st = status()
    base = {"sample": False, "q": q, "kind": "meaning", "results": [], **st}
    if not st["available"]:
        return base
    if not (q or "").strip():
        return base
    emb = _load_embedder()
    built = build_index(root, emb)
    idx = built["index"]
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
    scored = []
    for rel, f in idx["files"].items():
        if scope is not None and rel not in scope:
            continue
        for c in f.get("chunks", []):
            s = cosine(qv, _unpack(c["v"]))
            if s > floor:
                scored.append((s, rel, c))
    scored.sort(key=lambda r: (-r[0], r[1], r[2]["line"]))
    base["results"] = [{"score": round(s, 4), "path": rel, "name": c["name"], "kind": c["kind"],
                        "line": c["line"], "end": c["end"], "link": "graph-symbol"
                        if c["kind"] != "module" else "file"}
                       for s, rel, c in scored[:limit]]
    base["noise_floor"] = round(floor, 4)
    base["indexed_chunks"] = sum(len(f.get("chunks", [])) for f in idx["files"].values())
    base["embedded_now"] = built["embedded"]
    return base


__all__ = ["text", "meaning", "status", "fetch_model", "chunks_of", "build_index", "engine_name",
           "BadPattern", "INSTALL_LINE", "index_dir"]
