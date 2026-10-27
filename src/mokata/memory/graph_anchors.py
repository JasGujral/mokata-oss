"""0.0.26 O12 — the graph-proximity tier of the per-prompt recall, computed OFF the prompt path.

WHY THIS EXISTS (measured, not assumed). The UserPromptSubmit hook ran the full tiered recall, and
the graph tier (`knowledge.make_graph_scorer`) made one LIVE graph call per identifier in the
prompt. On a repo with code-review-graph wired that is a cold MCP server start plus the layer's
freshness reconcile (a `code-review-graph update` subprocess) — 5+ s on every prompt, against a
150 ms budget (U14). The hook's own 5 s runaway catch then fired, so the user waited 5 s AND the
pack was dropped; the abandoned worker left the `update` orphaned behind it. The verdict it was
computing — "does the graph confirm this token as a real symbol with callers?" — changes only when
the graph does, so it is precomputed and cached here instead:

  * the hook reads verdicts from `.mokata/temp_local/recall_graph/anchors.json` (stdlib + one
    small JSON read). The cache carries a FRESHNESS KEY — the stat signature of the manifest and of
    the graph's index files — and is ignored the moment any of them changes;
  * a token with no fresh verdict is NOT silently dropped: the hook queues it, starts ONE detached
    warmer (`warm_main`), and the injected pack SAYS the graph ranking was not applied to it this
    turn (`CachedGraphScorer.note`);
  * the warmer builds the layer WITHOUT the freshness controller (`KnowledgeLayer.from_router`): it
    never runs `code-review-graph update`/`build` itself — keeping the graph current is the
    freshness pass's job on the paths a human asked for, and the cache key follows the result.

The verdict function is the live scorer's own (`knowledge.layer.graph_anchor_verdict`), so a
cached anchor and a live one cannot disagree. Run-state only (gitignored `temp_local`), never a
durable write.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import re
import time

#: identifier-like tokens (len >= 3) — THE token rule of the graph tier (live and cached).
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")

DIRNAME = "recall_graph"
CACHE_FILENAME = "anchors.json"
WANT_FILENAME = "want"
LOCK_FILENAME = "warm.lock"
CACHE_VERSION = 1

#: Bounds — the cache, one prompt's request, and the warmer's own wall clock.
MAX_CACHED = 4096
MAX_WANT_PER_PROMPT = 256
WARM_BUDGET_SECS = 120.0
#: A warmer lock older than this belongs to a dead warmer and is taken over.
LOCK_STALE_SECS = 300.0
#: A token the graph failed to answer is retried after this long (no retry storm).
RETRY_FAILED_SECS = 600.0

WARM_FLAG = "--warm-recall-graph"


def query_tokens(query: str) -> list:
    """The prompt's identifier tokens, unique and sorted (deterministic lookup order)."""
    return sorted(set(IDENT.findall(query or "")))


def graph_possible() -> bool:
    """Could a real graph be answering in this environment at all? False when no graph tool's
    command resolves (the router's own `find_command` probe) — the common install, which then never
    queues a lookup or writes a cache. True only means "ask the warmer"."""
    from ..detect import find_command
    from ..knowledge.graph_tools import GRAPH_TOOLS
    return any(find_command(t) for t in GRAPH_TOOLS)


def cache_dir(root: str) -> str:
    from .. import MOKATA_DIR, TEMP_LOCAL_DIRNAME
    return os.path.join(root, MOKATA_DIR, TEMP_LOCAL_DIRNAME, DIRNAME)


def _stat_sig(path: str):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return [st.st_mtime_ns, st.st_size]


def read_cache(root: str):
    """The cache as a dict, or None when absent, unreadable, another version, or STALE (any
    watched file's stat signature moved). Never raises."""
    import json
    try:
        with open(os.path.join(cache_dir(root), CACHE_FILENAME), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("v") != CACHE_VERSION:
        return None
    key = data.get("key")
    if not isinstance(key, dict) or not isinstance(data.get("tokens"), dict):
        return None
    for path, sig in key.items():
        if _stat_sig(path) != sig:
            return None
    return data


class CachedGraphScorer:
    """The graph tier for the per-prompt recall: `(query, item) -> 0..1`, exactly the live scorer's
    rule (1.0 when the item's text contains a graph-confirmed query token), over CACHED verdicts.

    Lazy: the cache is read on the first call, i.e. only when the recall has candidate items to
    rank — an empty store never touches it and never queues a warm."""

    def __init__(self, root: str, query: str, *, now: "float | None" = None) -> None:
        self.root = root
        self.query = query
        self._now = now
        self._anchors: "set | None" = None
        self.consulted = False
        self.pending: list = []          # tokens with no fresh verdict — queued for the warmer
        self.failed: list = []           # tokens the graph failed to answer (recently)

    def __call__(self, _query: str, item) -> float:
        if self._anchors is None:
            self._resolve()
        if not self._anchors:
            return 0.0
        text = f"{item.subject} {item.value}"
        return 1.0 if any(a in text for a in self._anchors) else 0.0

    def _resolve(self) -> None:
        self.consulted = True
        self._anchors = set()
        toks = query_tokens(self.query)
        if not toks:
            return
        cache = read_cache(self.root)
        if cache is None:
            if not graph_possible():
                return                    # no graph tool installed: the tier is off, nothing skipped
            self.pending = toks
            return
        if not cache.get("uses_graph"):
            return                        # no graph wired: the tier is honestly off, nothing skipped
        now = time.time() if self._now is None else self._now
        verdicts = cache["tokens"]
        failed_at = cache.get("failed") or {}
        for t in toks:
            v = verdicts.get(t)
            if v is True:
                self._anchors.add(t)
            elif v is False:
                continue
            elif t in failed_at and now - float(failed_at.get(t) or 0) < RETRY_FAILED_SECS:
                self.failed.append(t)
            else:
                self.pending.append(t)

    def note(self) -> str:
        """The line the pack carries when the graph tier did not fully apply this turn, or ""."""
        parts = []
        if self.pending:
            parts.append(f"code-graph ranking not applied to {len(self.pending)} term(s) this "
                         "turn — looked up in the background, used from the next turn")
        if self.failed:
            parts.append(f"code-graph ranking skipped for {len(self.failed)} term(s) — the graph "
                         "did not answer (`mokata doctor`)")
        return "; ".join(parts)

    def request_warm(self) -> bool:
        """Queue the pending tokens and start the warmer if none is running. True when a warmer
        was started. Never raises."""
        if not self.consulted or not self.pending:
            return False
        try:
            d = cache_dir(self.root)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, WANT_FILENAME), "a", encoding="utf-8", newline="\n") as fh:
                fh.write("\n".join(self.pending[:MAX_WANT_PER_PROMPT]) + "\n")
            if _lock_fresh(d):
                return False
            from mokata_emit import spawn_mokata_emit
            return spawn_mokata_emit([WARM_FLAG, self.root], cwd=d)
        except Exception:  # noqa: BLE001 — a lookup that cannot be queued is retried next turn
            return False


# ------------------------------------------------------------------------------ the warmer

def _lock_fresh(d: str) -> bool:
    try:
        return time.time() - os.stat(os.path.join(d, LOCK_FILENAME)).st_mtime < LOCK_STALE_SECS
    except OSError:
        return False


def _acquire(d: str) -> bool:
    path = os.path.join(d, LOCK_FILENAME)
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            if _lock_fresh(d):
                return False
            try:
                os.remove(path)               # a dead warmer's lock — take it over
            except OSError:
                return False
            continue
        os.write(fd, str(os.getpid()).encode("ascii"))
        os.close(fd)
        return True
    return False


def _release(d: str) -> None:
    try:
        os.remove(os.path.join(d, LOCK_FILENAME))
    except OSError:
        pass


def _take_wanted(d: str) -> list:
    """Atomically claim everything queued so far (rename, then read): a token appended while this
    runs lands in a fresh `want` file and is claimed by the next loop."""
    want = os.path.join(d, WANT_FILENAME)
    claimed = f"{want}.{os.getpid()}"
    try:
        os.replace(want, claimed)
    except OSError:
        return []
    try:
        with open(claimed, "r", encoding="utf-8") as fh:
            toks = [t.strip() for t in fh if t.strip()]
    except OSError:
        toks = []
    try:
        os.remove(claimed)
    except OSError:
        pass
    return sorted({t for t in toks if IDENT.fullmatch(t)})


def watched_paths(root: str, layer) -> list:
    """What the verdicts depend on (absolute paths — a moved repo simply reads as stale): the
    manifest (which graph is adopted) and the adopted graph's own index file (what it knows)."""
    from .. import MOKATA_DIR
    paths = [os.path.join(root, MOKATA_DIR, "manifest.json")]
    client = getattr(getattr(layer, "primary", None), "client", None)
    if getattr(layer, "uses_graph", False) and getattr(client, "can_build_index", False):
        from ..knowledge.crg_build import db_path
        # The main db file only: its WAL is touched by any reader (the warmer's own lookups
        # included), while an `update`/`build` rewrites the db itself.
        paths.append(db_path(root))
    return paths


def _key(paths: list) -> dict:
    return {p: _stat_sig(p) for p in paths}


def _write_cache(root: str, data: dict) -> None:
    import json
    from ..atomicfile import atomic_write_text
    atomic_write_text(os.path.join(cache_dir(root), CACHE_FILENAME),
                      json.dumps(data, sort_keys=True, separators=(",", ":")))


def warm(root: str, tokens: list, *, layer=None, budget_secs: float = WARM_BUDGET_SECS) -> dict:
    """Compute verdicts for `tokens` and merge them into the cache. Returns the written cache.
    `layer` is injectable; production builds one WITHOUT freshness (no `update`/`build` from here)."""
    if layer is None:
        from ..config import Surface
        from ..knowledge import KnowledgeLayer
        own = KnowledgeLayer.from_router(Surface.load(root).router, root)
        try:
            return warm(root, tokens, layer=own, budget_secs=budget_secs)
        finally:
            _close(own)
    from ..knowledge.layer import graph_anchor_verdict
    uses_graph = bool(getattr(layer, "uses_graph", False))
    rels = watched_paths(root, layer)
    old = read_cache(root)                            # None when stale: start the verdicts afresh
    same = old is not None and old.get("uses_graph") == uses_graph
    verdicts = dict(old["tokens"]) if same else {}
    failed = dict(old.get("failed") or {}) if same else {}
    if uses_graph:
        deadline = time.monotonic() + budget_secs
        for tok in tokens:
            if time.monotonic() > deadline:
                break                                  # the rest stays pending: re-queued next turn
            try:
                v = graph_anchor_verdict(layer, tok)
            except Exception:  # noqa: BLE001 — e.g. an unbuilt index: a failed lookup, retried later
                v = None
            if v is None:
                failed[tok] = time.time()
                verdicts.pop(tok, None)
            else:
                verdicts.pop(tok, None)               # re-insert: newest last (eviction order)
                verdicts[tok] = v
                failed.pop(tok, None)
    while len(verdicts) > MAX_CACHED:
        verdicts.pop(next(iter(verdicts)))
    data = {"v": CACHE_VERSION, "key": _key(rels), "uses_graph": uses_graph, "tokens": verdicts,
            "failed": failed}
    _write_cache(root, data)
    return data


def warm_main(argv: list) -> int:
    """The detached warmer: `<python> mokata_emit/__main__.py --warm-recall-graph <root>`. Drains
    the `want` queue under one lock per repo, then exits. Never raises."""
    if not argv:
        return 1
    root = argv[0]
    d = cache_dir(root)
    try:
        os.makedirs(d, exist_ok=True)
        if not _acquire(d):
            return 0
    except OSError:
        return 0
    layer = None
    try:
        while True:
            toks = _take_wanted(d)
            if not toks:
                return 0
            if layer is None:
                from ..config import Surface
                from ..knowledge import KnowledgeLayer
                layer = KnowledgeLayer.from_router(Surface.load(root).router, root)
            warm(root, toks, layer=layer)
    except Exception:  # noqa: BLE001 — a background warmer has nobody to tell; the hook says it
        return 1
    finally:
        _close(layer)
        _release(d)


def _close(layer) -> None:
    """Stop the graph helper process this warmer started — none may outlive it."""
    close = getattr(getattr(getattr(layer, "primary", None), "client", None), "close", None)
    if callable(close):
        try:
            close()
        except (OSError, RuntimeError, ValueError):
            pass
