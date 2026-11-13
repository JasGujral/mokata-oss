"""The explainer STORE — pages, sidecars, the bundle cache, hand-off requests, job status files —
all under `.mokata/temp_local/reviews/`, every directory verified by `safedir` (no link followed).

    reviews/<sid>/<ts>.html · <ts>.json      a page + its sidecar (`explain.sidecar/1`)
    reviews/_bundles/<sid>.<hash>.json       a built bundle + its redacted blobs (cites resolve
                                             against these); `<sid>.<hash>.read.json` = the pages
                                             of it that were served
    reviews/_requests/<id>.json              an IDE hand-off (the WAITING state is derived from it)
    reviews/_jobs/<id>.json · <id>.cancel    an MCP-side `explain.bundle` job's status / Cancel flag

RETENTION is by count and VISIBLE: the newest `KEEP_PAGES` pages per `<sid>` and `KEEP_BUNDLES`
bundles per `<sid>` stay; older ones are pruned on save and the save result, the sidecar, the ledger
row and the Review tab all say how many. Pruning unlinks only exact generated names through the
verified directory. Pages committed into the repo are never here, so they are never touched.

This writer is UNGATED BY DESIGN (registered in `test_si_6_writegate_side_doors`): the page holds
MODEL-AUTHORED text, but only under gitignored `temp_local` (mirror-excluded), never read back as
an instruction, bounded by retention; committing a page INTO the repo is the P2-gated path
(`propose_commit`).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets as _secrets
import time
from typing import Any, Dict, List, Optional, Tuple

from .. import MOKATA_DIR, TEMP_LOCAL_DIRNAME
from ..errors import ControlSignal, MokataError
from . import bundle as B
from . import ground as G
from . import render as R
from .safedir import SafeDir, UnsafeStore, open_chain

SIDECAR = "explain.sidecar/1"
REVIEWS = "reviews"
KEEP_PAGES = 20
KEEP_BUNDLES = 5
KEEP_REQUESTS = 50
KEEP_JOBS = 20
WAIT_S = 30 * 60
PAGE_BYTES = 60_000
MAX_PAGE_BYTES = 32 * 1024 * 1024
#: THE bound of a cached bundle record (bundle + blobs + page map), for the write AND the read
#: (P46 r3 M1): a record over it is never written — it would be refused on every read. It is not
#: held either (r4 N1): only a small stub naming its target is written; the answer that built it
#: is served from the build, and any later ask REBUILDS it — said as "not cached — N MB".
CACHE_MAX_BYTES = 32 * 1024 * 1024
#: the slice a blob's JSON size is measured in — the only copy the measurement makes
_SIZE_SLICE = 1 << 20
TS_RE = re.compile(r"^\d{8}T\d{6}\.\d{6}Z(?:-\d{1,4})?$")
PAGE_RE = re.compile(r"^(\d{8}T\d{6}\.\d{6}Z(?:-\d{1,4})?)\.(html|json)$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
REQ_RE = re.compile(r"^[0-9a-f]{16}$")
_RESERVED = frozenset({"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
                       *(f"lpt{i}" for i in range(1, 10))})


class SaveRefused(MokataError, ValueError):
    """`explain_save` refused the whole save (unknown bundle, an invalid page) — said in words."""


def check_sid(sid: Any) -> str:
    """A store directory name: `ID_RE` (first char alphanumeric, no dots — never `..`), ≤120,
    never a Windows reserved device name in any case."""
    if not isinstance(sid, str) or not B.ID_RE.match(sid) or sid.lower() in _RESERVED:
        raise UnsafeStore(f"not a valid explainer id {str(sid)[:60]!r}")
    return sid


def _reviews(root: str, *more: str, create: bool = True) -> Optional[SafeDir]:
    """The store directory, every component verified. It never CREATES `.mokata/` itself: a repo
    nobody set up stays untouched (P29) — the caller is told to run `mokata init`."""
    if create and not os.path.isdir(os.path.join(root, MOKATA_DIR)):
        raise UnsafeStore("mokata is not set up in this repository (no .mokata/) — run "
                          "`mokata init` first; nothing was written")
    # 0.0.28 C1: private from `reviews/` down ONLY — `.mokata` and `temp_local` are never created by
    # the private door (their later ordinary files must not inherit an owner-only entry).
    base = open_chain(root, [MOKATA_DIR, TEMP_LOCAL_DIRNAME], create=create)
    if base is None:
        return None
    cur = base
    for part in [REVIEWS, *more]:
        try:
            nxt = cur.sub(part, create=create, private=True)
        finally:
            cur.close()
        if nxt is None:
            return None
        cur = nxt
    return cur


def _now_ts(now: float) -> str:
    us = int(round((now - int(now)) * 1_000_000)) % 1_000_000
    return time.strftime("%Y%m%dT%H%M%S", time.gmtime(int(now))) + f".{us:06d}Z"


def _iso(now: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))


def _dump(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, indent=1).encode("utf-8")


def _load(d: SafeDir, name: str, limit: int = MAX_PAGE_BYTES) -> Optional[Any]:
    raw = d.read(name, limit)
    if raw is None or len(raw) > limit:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None


# ===================================================================================== bundles

def paginate(bundle: Dict[str, Any]) -> Dict[str, int]:
    """fact id → page (1-based): consecutive facts up to `PAGE_BYTES` of JSON per page, never
    fewer than one fact. Deterministic."""
    out: Dict[str, int] = {}
    page, used = 1, 0
    for f in bundle["facts"]:
        n = len(json.dumps(f, ensure_ascii=False))
        if used and used + n > PAGE_BYTES:
            page, used = page + 1, 0
        out[f["id"]] = page
        used += n
    return out


def _mb(n: int) -> str:
    return f"{n / 1048576:.1f} MB"


def _escaped_len(text: str) -> int:
    """The UTF-8 byte length of `text` as a JSON string body (`_dump`'s escaping), measured a
    slice at a time — never a second whole copy of the text."""
    n = 0
    for i in range(0, len(text), _SIZE_SLICE):
        piece = json.dumps(text[i:i + _SIZE_SLICE], ensure_ascii=False)[1:-1]
        n += len(piece) if piece.isascii() else len(piece.encode("utf-8"))
    return n


def record_size(rec: Dict[str, Any]) -> int:
    """EXACTLY `len(_dump(rec))`, streamed (P46 r4 N1): the record is dumped with every blob
    emptied (small), and each blob's escaped length is added where its `""` stands."""
    shell = dict(rec, blobs={k: "" for k in rec.get("blobs") or {}})
    return len(_dump(shell)) + sum(_escaped_len(v) for v in (rec.get("blobs") or {}).values())


class NeedsRebuild(ControlSignal):
    """A control signal, not a failure (d5): an oversize bundle whose spill is gone (released, or another process built it): it must
    be REBUILT — as a job, by the caller — never inline. Carries the target to rebuild."""

    def __init__(self, spec: str) -> None:
        super().__init__(f"the bundle is rebuilt as a job: {spec}")
        self.spec = spec


def _pages_hash(page_of: Dict[str, int]) -> str:
    return hashlib.sha256(json.dumps(page_of, sort_keys=True).encode("utf-8")).hexdigest()


def cache_bundle(root: str, built: Dict[str, Any], spill: bool = True) -> Dict[str, Any]:
    """Keep a built bundle (+ blobs, page map) for cites to resolve against; prune the oldest of
    its `<sid>` beyond `KEEP_BUNDLES` (said in the answer). Idempotent: two builders of the same
    content write the same bytes under the same name.

    A record over `CACHE_MAX_BYTES` (measured without serialising it) is not cached as one file
    and not held in memory: a small stub names its target, and — `spill` — the record is
    SPILLED row by row (`explain.spill`, P4's `SpillWriter`), keyed by its hash, so every later
    page and the save stream from it. The IDE passes `spill=False` (it hands off and never reads
    the bundle again). The answer says `not cached — N MB`."""
    from . import spill as SP
    b = built["bundle"]
    sid = check_sid(B.sid_of(B.parse_target(b["target"]["spec"])))
    h = b["content_hash"]
    rec = {"bundle": b, "blobs": built["blobs"], "page_of": paginate(b), "at": time.time()}
    size = record_size(rec)
    over = size > CACHE_MAX_BYTES
    with _reviews(root, "_bundles") as d:
        if over:
            ph = _pages_hash(rec["page_of"])
            old = _load(d, f"{sid}.{h}.over.json") or {}
            if old.get("pages_hash") not in (None, ph):
                d.unlink(f"{sid}.{h}.read.json")     # pages moved (volatile facts): re-read them
            d.write(f"{sid}.{h}.over.json", _dump({"spec": b["target"]["spec"],
                                                  "content_hash": h, "size": size,
                                                  "pages_hash": ph}))
        else:
            d.write(f"{sid}.{h}.json", _dump(rec))
        mine = [n for n in d.names()
                if re.match(re.escape(sid) + r"\.[0-9a-f]{64}(?:\.over)?\.json$", n)]
        mine.sort(key=lambda n: (d.mtime(n), n), reverse=True)
        pruned = 0
        for n in mine[KEEP_BUNDLES:]:
            if d.unlink(n):
                pruned += 1
            d.unlink(n[:-5].removesuffix(".over") + ".read.json")
    rec["pruned"] = pruned
    rec["size"] = size
    if over:
        if spill:
            SP.write(root, sid, rec, size)
        rec["cache"] = _over_note(size, spill)
    return rec


def _over_note(size: int, spilled: bool = True) -> str:
    head = f"not cached — {_mb(size)} is over the {_mb(CACHE_MAX_BYTES)} cache bound"
    if spilled:
        return (head + "; spilled to a private temp file that this server's pages and save read "
                "from (released when unused for 30 min or when the change moves; rebuilt as a "
                "job after that)")
    return head + "; Claude Code's server builds and spills its own"


def _base_name(d: SafeDir, content_hash: str) -> Optional[str]:
    """`<sid>.<hash>` of a stored record or of an oversize stub."""
    for n in d.names():
        for tail in (f".{content_hash}.json", f".{content_hash}.over.json"):
            if n.endswith(tail) and not n.endswith(".read.json"):
                return n[:-len(tail)] + f".{content_hash}"
    return None


def _served(d: SafeDir, base: str) -> List[int]:
    read = _load(d, base + ".read.json") or {}
    return sorted(int(x) for x in read.get("pages", []) if isinstance(x, int))


def _locate(root: str, content_hash: str) -> Tuple[Optional[Dict[str, Any]], Any, Optional[str],
                                                   List[int], Optional[Dict[str, Any]]]:
    """(the cached record, the live spill, the base name, the served pages, the stub)."""
    from . import spill as SP
    with _reviews(root, "_bundles") as d:
        base = _base_name(d, content_hash)
        if base is None:
            return None, None, None, [], None
        rec = _load(d, base + ".json", CACHE_MAX_BYTES)
        served = _served(d, base)
        stub = None if rec else _load(d, base + ".over.json")
    sp = None if rec else SP.get(root, content_hash)
    return rec, sp, base, served, stub


def load_bundle(root: str, content_hash: str) -> Optional[Dict[str, Any]]:
    """The cached record of `content_hash`: from its file, or — oversize — streamed from its
    spill (all facts; blobs read on demand). None when unknown. Raises `NeedsRebuild` when only
    the stub is left (the spill was released): the caller starts the rebuild JOB."""
    from . import spill as SP
    if not isinstance(content_hash, str) or not HASH_RE.match(content_hash):
        return None
    rec, sp, base, served, stub = _locate(root, content_hash)
    if rec:
        rec["_name"] = base + ".json"
        rec["served"] = served
        return rec
    if sp is not None:
        try:
            b = dict(sp.meta, facts=sp.facts())
        except SP.SpillGone:
            sp = None
        else:
            return {"bundle": b, "blobs": SP.SpillBlobs(sp), "page_of": sp.page_of,
                    "_name": base + ".json", "served": served, "cache": _over_note(sp.size)}
    if stub and isinstance(stub.get("spec"), str):
        raise NeedsRebuild(stub["spec"])
    return None


def view(root: str, content_hash: str, page: int) -> Optional[Dict[str, Any]]:
    """ONE page of the bundle as the MCP tool returns it — from the cached file, or streamed
    from the spill (only that page's facts are read). None when unknown; `NeedsRebuild` when
    only the stub is left."""
    from . import spill as SP
    if not isinstance(content_hash, str) or not HASH_RE.match(content_hash):
        return None
    rec, sp, _base, _served_, stub = _locate(root, content_hash)
    if rec:
        return page_view(rec, page)
    if sp is not None:
        pages = max(sp.page_of.values()) if sp.page_of else 1
        p = max(1, min(int(page), pages))
        try:
            facts = sp.page_facts(p)
        except SP.SpillGone:
            sp = None
        else:
            lite = {"bundle": dict(sp.meta, facts=facts), "page_of": sp.page_of,
                    "cache": _over_note(sp.size)}
            return page_view(lite, p)
    if stub and isinstance(stub.get("spec"), str):
        raise NeedsRebuild(stub["spec"])
    return None


def find_cached(root: str, target: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A cached bundle built from exactly this resolved target (spec, SHAs, dirty hash)."""
    from . import spill as SP
    sid = check_sid(B.sid_of(B.parse_target(target["spec"])))
    keys = ("spec", "base_sha", "head_sha", "dirty_hash")
    for sp in SP.live(root):                      # an oversize bundle still spilled: reuse it
        t = sp.meta.get("target") or {}
        if all(t.get(k) == target.get(k) for k in keys) and SP.get(root, sp.hash) is not None:
            return {"bundle": sp.meta}
    with _reviews(root, "_bundles") as d:
        names = [n for n in d.names() if n.startswith(sid + ".") and n.endswith(".json")
                 and not n.endswith(".read.json")]
        names.sort(key=lambda n: (d.mtime(n), n), reverse=True)
        for n in names:
            rec = _load(d, n, CACHE_MAX_BYTES)
            t = (rec or {}).get("bundle", {}).get("target", {})
            if all(t.get(k) == target.get(k) for k in ("spec", "base_sha", "head_sha",
                                                       "dirty_hash")):
                return rec
    return None


def mark_served(root: str, content_hash: str, page: int) -> None:
    """Record that page `page` of the bundle was served — without loading (or rebuilding) it."""
    if not isinstance(content_hash, str) or not HASH_RE.match(content_hash):
        return
    with _reviews(root, "_bundles") as d:
        base = _base_name(d, content_hash)
        if base is None:
            return
        d.write(base + ".read.json", _dump({"pages": sorted(set(_served(d, base))
                                                             | {int(page)})}))


def page_view(rec: Dict[str, Any], page: int) -> Dict[str, Any]:
    """One page of a cached bundle as the MCP tool returns it: the header on every page, the
    facts of that page, and how many pages there are."""
    b = rec["bundle"]
    page_of = rec["page_of"]
    pages = max(page_of.values()) if page_of else 1
    page = max(1, min(int(page), pages))
    return {"schema": b["schema"], "untrusted": True, "untrusted_note": b["untrusted_note"],
            "content_hash": b["content_hash"], "target": _public_target(b["target"]),
            "files": b["files"], "notes": b["notes"], "edges_basis": b["edges_basis"],
            "refs_basis": b["refs_basis"], "page": page, "pages": pages,
            **({"cache": rec["cache"]} if rec.get("cache") else {}),
            "facts": [f for f in b["facts"] if page_of.get(f["id"]) == page]}


def _public_target(t: Dict[str, Any]) -> Dict[str, Any]:
    return {k: t.get(k) for k in ("kind", "spec", "session_id", "run_id", "worktree", "branch",
                                  "base_sha", "head_sha", "dirty", "dirty_hash", "refs",
                                  "origin")}


def _git_text(root: str) -> Any:
    from ..ide import snapshots as S

    def get(sha: str, rel: str) -> Optional[str]:
        code, out = B._git(root, "ls-tree", "--end-of-options", sha, "--", rel)
        if code != 0 or not out.strip() or out.split()[0] == "120000" or out.split()[1] != "blob":
            return None
        got = B.cat_blobs(root, [(sha, rel)], S.MAX_FILE_BYTES).get((sha, rel))
        return got if isinstance(got, str) else None
    return get


# ======================================================================================== save

def save(root: str, doc: Any, *, claude_version: str = "", now: Optional[float] = None
         ) -> Dict[str, Any]:
    """`explain_save`: ground the document against its bundle, render, validate, store, ledger.
    Refuses the WHOLE save only for an unknown bundle or an invalid page; a block that fails its
    cite is dropped and recorded, never refused silently."""
    now = time.time() if now is None else now
    if not isinstance(doc, dict):
        raise SaveRefused("the document is an object: {content_hash, model, pages_read, sections}")
    rec = load_bundle(root, doc.get("content_hash", ""))
    if rec is None:
        raise SaveRefused("unknown bundle (content_hash) — call explain_bundle again and write "
                          "the page from what it returns")
    bundle = rec["bundle"]
    served = set(rec.get("served") or [])
    claimed = {int(x) for x in (doc.get("pages_read") or []) if isinstance(x, int)}
    pages_read = (claimed & served) if served else set()
    res = G.Resolver(bundle, rec["blobs"], pages_read, rec["page_of"], _git_text(root))
    sections, dropped = G.check_doc(doc, res, R.mermaid_problem)
    for s in sections:
        for b in s["blocks"]:
            b["text"] = G.redact(b.get("text") or "")[0]
            if b.get("mermaid"):
                b["mermaid"] = G.redact(b["mermaid"])[0]
    model = doc.get("model") if isinstance(doc.get("model"), str) else ""
    model = re.sub(r"[^\w.\-:/ ]", "", model)[:80]
    t = bundle["target"]
    sid = check_sid(B.sid_of(B.parse_target(t["spec"])))
    kept = sum(len(s["blocks"]) for s in sections)
    meta = {"claude_version": claude_version[:40], "model": model, "generated_at": _iso(now),
            "dropped": len(dropped)}
    page, cites, diagrams = R.render(bundle, sections, meta)
    problems = R.validate(page)
    if problems:
        raise SaveRefused("the rendered page failed validation: " + "; ".join(problems[:5]))
    with _reviews(root, sid) as d:
        ts = _now_ts(now)
        n = 1
        while d.size(ts + ".html") or d.size(ts + ".json"):
            n += 1
            ts = f"{_now_ts(now)}-{n}"
        review_id = f"{sid}/{ts}"
        page_bytes = page.encode("utf-8")
        sidecar = {"schema": SIDECAR, "id": review_id, "session_id": t.get("session_id") or "",
                   "run_id": t.get("run_id") or "", "worktree": t.get("worktree") or "",
                   "branch": t.get("branch") or "", "target": _public_target(t),
                   "claude_code": {"version": claude_version[:40], "model": model,
                                   "model_basis": R.SELF_REPORTED},
                   "content_hash": bundle["content_hash"], "pages_read": sorted(pages_read),
                   "generated_at": _iso(now), "generated_ts": now, "kept": kept,
                   "dropped_count": len(dropped), "dropped": dropped,
                   "omitted": [{"path": f["path"], "why": f["shown"]} for f in bundle["files"]
                               if f.get("shown") != "full"],
                   "cites": cites, "diagrams": diagrams,
                   "page_sha256": hashlib.sha256(page_bytes).hexdigest(), "pruned": 0}
        d.write(ts + ".html", page_bytes)
        pruned = _prune_pages(d, keep=KEEP_PAGES)
        sidecar["pruned"] = pruned
        d.write(ts + ".json", _dump(sidecar))
    _ledger(root, sidecar)
    return {"status": "saved", "review_id": review_id,
            "path": "/".join((MOKATA_DIR, TEMP_LOCAL_DIRNAME, REVIEWS, sid, ts + ".html")),
            "kept": kept, "dropped": len(dropped),
            "dropped_reasons": [f"{x['section']}[{x['block']}]: {x['reason']}" for x in dropped],
            "pruned": pruned, "keep": KEEP_PAGES}


def _prune_pages(d: SafeDir, keep: int) -> int:
    stamps = sorted({m.group(1) for m in (PAGE_RE.match(n) for n in d.names()) if m},
                    reverse=True)
    pruned = 0
    for ts in stamps[keep:]:
        a = d.unlink(ts + ".html")
        b = d.unlink(ts + ".json")
        pruned += 1 if (a or b) else 0
    return pruned


def _ledger(root: str, s: Dict[str, Any]) -> None:
    from ..govern.ledger import AuditLedger
    AuditLedger.from_mokata_dir(os.path.join(root, MOKATA_DIR)).record(
        "change_explained", review_id=s["id"], session_id=s["session_id"], run_id=s["run_id"],
        target_kind=s["target"]["kind"], base_sha=s["target"].get("base_sha") or "",
        head_sha=s["target"].get("head_sha") or "", content_hash=s["content_hash"],
        page_sha256=s["page_sha256"], kept=s["kept"], dropped_count=s["dropped_count"],
        pruned_count=s["pruned"])


# ===================================================================================== reading

def check_review_id(rid: Any) -> Tuple[str, str]:
    if not isinstance(rid, str) or rid.count("/") != 1:
        raise UnsafeStore("a review id is <sid>/<timestamp>")
    sid, ts = rid.split("/")
    check_sid(sid)
    if not TS_RE.match(ts):
        raise UnsafeStore("a review id is <sid>/<timestamp>")
    return sid, ts


def read_page(root: str, rid: str) -> Optional[bytes]:
    sid, ts = check_review_id(rid)
    d = _reviews(root, sid, create=False)
    if d is None:
        return None
    with d:
        return d.read(ts + ".html", MAX_PAGE_BYTES)


def read_sidecar(root: str, rid: str) -> Optional[Dict[str, Any]]:
    sid, ts = check_review_id(rid)
    d = _reviews(root, sid, create=False)
    if d is None:
        return None
    with d:
        return _load(d, ts + ".json")


def list_reviews(root: str, sid: Optional[str] = None, *, stale: bool = True
                 ) -> Dict[str, Any]:
    """Every stored page (newest first), each with its staleness computed NOW — or one `sid`'s.
    An unreadable sidecar is listed as `unreadable — regenerate`, never dropped or deleted."""
    top = _reviews(root, create=False)
    out: List[Dict[str, Any]] = []
    total_bytes = 0
    if top is None:
        return {"reviews": [], "count": 0, "bytes": 0, "keep": KEEP_PAGES}
    with top:
        sids = [check_sid(sid)] if sid else [n for n in top.names() if B.ID_RE.match(n)
                                            and n.lower() not in _RESERVED]
        cache: Dict[str, Any] = {}
        for s in sids:
            d = top.sub(s, create=False)
            if d is None:
                continue
            with d:
                for n in d.names():
                    m = PAGE_RE.match(n)
                    if not m:
                        continue
                    total_bytes += d.size(n)
                    if m.group(2) != "json":
                        continue
                    sc = _load(d, n)
                    rid = f"{s}/{m.group(1)}"
                    if not sc or sc.get("schema") != SIDECAR:
                        out.append({"id": rid, "sid": s, "state": "unreadable",
                                    "why": "unreadable — regenerate (the sidecar is not "
                                           f"{SIDECAR}); reveal it in its folder to remove it",
                                    "generated_ts": d.mtime(n)})
                        continue
                    row = {"id": rid, "sid": s, "session_id": sc.get("session_id"),
                           "run_id": sc.get("run_id"), "target": sc.get("target"),
                           "generated_at": sc.get("generated_at"),
                           "generated_ts": sc.get("generated_ts") or 0, "kept": sc.get("kept"),
                           "dropped": sc.get("dropped_count"), "pruned": sc.get("pruned", 0),
                           "content_hash": sc.get("content_hash")}
                    if stale:
                        row.update(staleness(root, sc, cache))
                    out.append(row)
    out.sort(key=lambda r: (-(r.get("generated_ts") or 0), r["id"]))
    return {"reviews": out, "count": len(out), "bytes": total_bytes, "keep": KEEP_PAGES}


def staleness(root: str, sc: Dict[str, Any], cache: Optional[Dict[str, Any]] = None
              ) -> Dict[str, str]:
    """`{state: fresh|stale|orphaned, why}` — computed on read, never stored."""
    t = sc.get("target") or {}
    cache = {} if cache is None else cache
    try:
        spec = B.parse_target(t.get("spec") or "")
    except B.BundleRefused as exc:
        return {"state": "unreadable", "why": str(exc)}
    if spec["kind"] == "range":
        for side in ("base", "head"):
            ref = (t.get("refs") or {}).get(side) or ""
            now = B.rev(root, ref) if ref else ""
            was = t.get("head_sha") if side == "head" else None
            if side == "head" and now != was:
                return {"state": "stale", "why": f"{ref} now points at {now[:12] or 'nothing'}"}
        return {"state": "fresh", "why": "the range's refs still resolve to the same commits"}
    key = t.get("spec")
    if key not in cache:
        try:
            r = B.resolve(root, spec)
            cache[key] = (r["target"].get("head_sha"), B.current_dirty_hash(root, spec))
        except (B.BundleRefused, OSError) as exc:
            cache[key] = ("orphan", str(exc))
    head, dirty = cache[key]
    if head == "orphan":
        return {"state": "orphaned", "why": f"the target is gone: {dirty}"}
    if head != (t.get("head_sha") or ""):
        return {"state": "stale", "why": f"head moved: {str(t.get('head_sha'))[:12]} → "
                                         f"{str(head)[:12]}"}
    if t.get("dirty_hash") and dirty != t.get("dirty_hash"):
        return {"state": "stale", "why": "uncommitted changes differ from what was explained"}
    return {"state": "fresh", "why": "head and uncommitted changes match"}


# ===================================================================================== requests

def new_request(root: str, spec: Dict[str, str], via: str, *, session: str = "",
                terminal: str = "", now: Optional[float] = None) -> Dict[str, Any]:
    now = time.time() if now is None else now
    rid = _secrets.token_hex(8)
    rec = {"request_id": rid, "spec": B.target_text(spec), "sid": B.sid_of(spec), "via": via,
           "session": session, "terminal": terminal, "at": now, "wait_s": WAIT_S,
           "delivered_at": None, "cancelled": False}
    with _reviews(root, "_requests") as d:
        d.write(rid + ".json", _dump(rec))
        names = sorted((n for n in d.names() if re.match(r"^[0-9a-f]{16}\.json$", n)),
                       key=lambda n: (d.mtime(n), n), reverse=True)
        for n in names[KEEP_REQUESTS:]:
            d.unlink(n)
    return rec


def update_request(root: str, rid: str, **fields: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(rid, str) or not REQ_RE.match(rid):
        raise UnsafeStore("a request id is 16 hex characters")
    with _reviews(root, "_requests") as d:
        rec = _load(d, rid + ".json")
        if not rec:
            return None
        rec.update(fields)
        d.write(rid + ".json", _dump(rec))
        return rec


def requests(root: str) -> List[Dict[str, Any]]:
    d = _reviews(root, "_requests", create=False)
    if d is None:
        return []
    with d:
        out = [_load(d, n) for n in d.names() if re.match(r"^[0-9a-f]{16}\.json$", n)]
    return sorted((r for r in out if isinstance(r, dict)), key=lambda r: -float(r.get("at") or 0))


def request_state(rec: Dict[str, Any], pages: List[Dict[str, Any]], *, alive: bool,
                  now: Optional[float] = None) -> Dict[str, str]:
    """The WAITING state, derived on read (never a job, never "done" without a page):
    arrived · cancelled · queued (for a live session's next turn) · waiting · no page arrived."""
    now = time.time() if now is None else now
    at = float(rec.get("at") or 0)
    for p in pages:
        if (p.get("target") or {}).get("spec") == rec.get("spec") and \
                float(p.get("generated_ts") or 0) >= at:
            return {"state": "arrived", "text": "the page arrived", "review": p["id"]}
    if rec.get("cancelled"):
        return {"state": "cancelled", "text": "cancelled before it reached Claude Code"}
    if rec.get("via") == "session" and not rec.get("delivered_at"):
        if not alive:
            return {"state": "no page arrived", "text": "the session ended before its next turn"}
        return {"state": "queued", "text": "queued for that session's next turn — it runs when "
                                           "you next send a message there"}
    if now - at > float(rec.get("wait_s") or WAIT_S):
        return {"state": "no page arrived",
                "text": f"no page arrived within {int(rec.get('wait_s') or WAIT_S) // 60} min"}
    if not alive:
        return {"state": "no page arrived", "text": "Claude Code is no longer running there"}
    return {"state": "waiting", "text": "Waiting for Claude Code — mokata cannot see or stop "
                                        "it. Close the terminal tab to abandon."}


# ========================================================================================= jobs

def jobs_dir(root: str) -> SafeDir:
    return _reviews(root, "_jobs")


def write_job(root: str, job_id: str, status: Dict[str, Any]) -> None:
    with jobs_dir(root) as d:
        d.write(job_id + ".json", _dump(status))


def cancel_requested(root: str, job_id: str) -> bool:
    d = _reviews(root, "_jobs", create=False)
    if d is None:
        return False
    with d:
        return d.size(job_id + ".cancel") > 0 or (job_id + ".cancel") in d.names()


def request_cancel(root: str, job_id: str) -> None:
    if not re.match(r"^[0-9a-f]{16}$", job_id or ""):
        raise UnsafeStore("a job id is 16 hex characters")
    with jobs_dir(root) as d:
        d.write(job_id + ".cancel", b"1")


def list_jobs(root: str) -> List[Dict[str, Any]]:
    d = _reviews(root, "_jobs", create=False)
    if d is None:
        return []
    with d:
        out = [_load(d, n) for n in d.names() if re.match(r"^[0-9a-f]{16}\.json$", n)]
    return sorted((j for j in out if isinstance(j, dict)), key=lambda j: -float(j.get("started")
                                                                               or 0))


def prune_jobs(root: str) -> None:
    with jobs_dir(root) as d:
        names = sorted((n for n in d.names() if re.match(r"^[0-9a-f]{16}\.json$", n)),
                       key=lambda n: (d.mtime(n), n), reverse=True)
        for n in names[KEEP_JOBS:]:
            d.unlink(n)
            d.unlink(n[:-5] + ".cancel")


def store_size(root: str) -> Dict[str, int]:
    """Bytes of pages and of cached bundles (shown on the Review tab)."""
    pages = bundles = 0
    top = _reviews(root, create=False)
    if top is None:
        return {"pages": 0, "bundles": 0}
    with top:
        for n in top.names():
            if n == "_bundles":
                d = top.sub(n, create=False)
                if d:
                    with d:
                        bundles += sum(d.size(x) for x in d.names())
            elif B.ID_RE.match(n):
                d = top.sub(n, create=False)
                if d:
                    with d:
                        pages += sum(d.size(x) for x in d.names() if PAGE_RE.match(x))
    return {"pages": pages, "bundles": bundles}


__all__ = ["save", "SaveRefused", "list_reviews", "read_page", "read_sidecar", "staleness",
           "cache_bundle", "load_bundle", "find_cached", "page_view", "mark_served",
           "new_request", "update_request", "requests", "request_state", "write_job",
           "cancel_requested", "request_cancel", "list_jobs", "check_sid", "check_review_id",
           "store_size", "KEEP_PAGES", "KEEP_BUNDLES", "CACHE_MAX_BYTES", "record_size", "view", "NeedsRebuild"]
