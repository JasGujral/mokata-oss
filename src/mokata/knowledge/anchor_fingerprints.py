"""H-6 S1 — the DURABLE anchor→fingerprint record, and the ONE verdict every H-6 surface reads.

THE STRUCTURAL GAP THIS CLOSES. GR.S4 already fingerprints files — `file_fingerprint` /
`KnowledgeIndex` (`knowledge/index.py:19`) — but the baseline it keeps is written to a
SESSION-SCOPED `StateStore` key (`FRESHNESS_INDEX_PREFIX + session_id`, `freshness.py:41`). That is
correct for what freshness does: reconcile a graph WITHIN a run. It cannot answer the only question
H-6 asks — *has this changed since we last had reason to believe it was current* — because the
answer needs a baseline that OUTLIVES the session that took it. So this record is durable, and
that durability is the whole of slice S1.

WHY THIS IS UNGATED AND STILL P2-CLEAN (H-6 plan of record, decision #5). P2 gates DURABLE writes
of FACTS — memory, code, config. This is derived run-state: it lives under `.mokata/temp_local/`
(which a committed `.mokata/.gitignore` keeps out of version control), it holds nothing a human
said, and every byte of it is re-derivable by re-hashing the repo. Losing it costs one silent
re-record and can never cost a fact. What it is NOT is session-scoped — the SQLite memory store
lives under the same directory and outlives every session too.

THE ANCHOR-SHAPE SPLIT (decision #1, LOCKED). An `about_code` anchor is a free-string list
(`memory/item.py`) and carries two shapes in practice: `src/mokata/memory/store.py` and
`MemoryStore.remember` are both legal entries. They are NOT one thing.

  * A PATH-SHAPED anchor resolves to a file whose content hash is a VERIFIABLE FACT. No graph is
    consulted, none is needed, and none is asked — `evaluate_anchor` on a path anchor never touches
    `layer` at all.
  * A SYMBOL-SHAPED anchor needs an AUTHORITATIVE resolver even to learn which file to hash. On the
    AST/grep floor it DECLINES.

This RESPECTS `about_code.py:8-11`'s fail-OPEN, no-false-claim rule rather than weakening it. That
rule forbids manufacturing a claim from a DEGRADED GRAPH; a changed file hash is not a claim from
the graph at all. Declining symbol anchors on the floor is the same rule applied to the shape that
genuinely does depend on one. Two shapes, two claims, two evidence bars.

WHAT THE SYMBOL ARM DELIBERATELY DOES NOT CLAIM. "This symbol no longer resolves" is
`about_code.check_about_code_anchors`'s verdict and it already owns it; a second, differently-worded
opinion on the same fact is the second-vocabulary failure this codebase refuses elsewhere. The
symbol arm therefore compares the SAME kind of evidence the path arm does — a file content hash —
and differs only in HOW the file is found: the graph names the definition site, or nobody does.
**Named cost, stated rather than hidden:** the real code-review-graph maps no `defs` pattern
(`crg_client.py:136-142`), so against today's adopted CRG the symbol arm declines everywhere and
H-6 is path-anchors-only. That is what "decline rather than claim" means when the capability is
absent, and it is filed (doc 84 H-6-SYMBOL-NEEDS-DEFS) rather than papered over.

NO BASELINE IS NO OPINION (decision #6). Inherited from `memory/staleness.is_stale` ("both sides
must be present for there to be an opinion") and from `KnowledgeIndex.is_stale`'s untracked ⇒
not-stale. First observation records and stays silent: a proposal claims movement SINCE a moment we
observed, or it claims nothing.

ONE TRIPWIRE. The comparison is `freshness.fingerprint_forces_refresh` — the hook GR.S4 pre-named
and left dormant for exactly this stage. H-6 wakes it; it does not grow a second one.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .freshness import fingerprint_forces_refresh
from .index import file_fingerprint

# --- the leaf under `.mokata/temp_local/` this record owns ---------------------------------
# Named as constants because the P2 whole-tree byte pin carves out exactly this directory and
# nothing else — a test that hard-coded the path would stop matching the moment it moved, and would
# carve out the wrong thing silently (the `injection_ledger` precedent).
RECORD_DIRNAME = "anchor_fingerprints"
RECORD_FILENAME = "anchors.json"

# --- the three verdicts -------------------------------------------------------------------
MOVED = "moved"          # the evidence says the anchored code is not what we recorded
UNCHANGED = "unchanged"  # the evidence says it is
DECLINED = "declined"    # there is no evidence, and mokata does not guess

# --- the two shapes -----------------------------------------------------------------------
SHAPE_PATH = "path"
SHAPE_SYMBOL = "symbol"

# --- why a verdict declined (human-facing; each names what is missing, never a feeling) ----
DECLINE_NO_BASELINE = "no recorded fingerprint — first observation, nothing to compare against"
DECLINE_ABSENT = "the anchored file is not present in this tree"
DECLINE_UNREADABLE = "the anchored file could not be read"
DECLINE_NO_GRAPH = ("no authoritative code graph — a symbol anchor cannot be resolved on the "
                    "AST/grep floor")
DECLINE_NO_DEFS = ("the adopted graph maps no definition-site query, so it cannot name the file "
                   "that defines this symbol")
DECLINE_UNRESOLVED = "the graph named no definition site for this symbol"
DECLINE_GRAPH_FAILED = "the code graph could not be asked"

# A string that LOOKS like a path even when nothing is there to stat. Deliberately generous: a
# mis-read of a path as a symbol sends it to the graph and it declines (a miss); a mis-read of a
# symbol as a path stats a file that does not exist and it declines too. Both errors land on
# DECLINED, which is the direction decision #6 already chose.
_PATH_SUFFIXES = (
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".go", ".rb", ".java", ".rs", ".c", ".h",
    ".cc", ".cpp", ".hpp", ".cs", ".php", ".swift", ".kt", ".scala", ".sh", ".sql", ".md",
    ".toml", ".yaml", ".yml", ".json", ".cfg", ".ini", ".txt", ".html", ".css",
)


@dataclass(frozen=True)
class AnchorVerdict:
    """The verdict for ONE `about_code` anchor (doc 85 §3: a read-only `*Verdict`-style value).

    This is the SINGLE piece of evidence every H-6 surface reads — the proposal arm and the
    STALE-REF refusal both consume it and neither derives its own (plan of record, P4). That is
    what makes "the bridge never fails loud where H-6 itself declines" a structural property rather
    than two implementations that agree today.
    """

    anchor: str
    shape: str
    verdict: str
    recorded: str = ""
    current: str = ""
    path: str = ""          # the file(s) whose hash was compared — evidence, not decoration
    reason: str = ""        # set on DECLINED: what is missing

    @property
    def moved(self) -> bool:
        return self.verdict == MOVED

    @property
    def declined(self) -> bool:
        return self.verdict == DECLINED


# ==========================================================================================
# the record on disk
# ==========================================================================================
def record_dir(root: str) -> str:
    from .. import MOKATA_DIR, TEMP_LOCAL_DIRNAME
    return os.path.join(root, MOKATA_DIR, TEMP_LOCAL_DIRNAME, RECORD_DIRNAME)


def record_path(root: str) -> str:
    """The record file. PROJECT-scoped, never session-scoped — that is slice S1's whole point."""
    return os.path.join(record_dir(root), RECORD_FILENAME)


def read_record(root: str) -> Dict[str, Dict[str, Any]]:
    """The recorded baselines, or `{}`. NEVER raises.

    A corrupt/unreadable record degrades to NO baselines, which by decision #6 means every anchor
    declines. That is the safe direction and the only honest one: a half-parsed record would let
    some anchors carry a baseline from a file we could not fully read, and a MOVED verdict built on
    that is a claim mokata cannot stand behind.
    """
    try:
        with open(record_path(root), encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:  # noqa: BLE001 — absent, unreadable and malformed all mean "no baseline"
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, dict)}


def _write_record(root: str, record: Dict[str, Dict[str, Any]]) -> None:
    from ..atomicfile import atomic_write_text
    atomic_write_text(record_path(root),
                      json.dumps(record, indent=2, sort_keys=True) + "\n")


# ==========================================================================================
# shape
# ==========================================================================================
def split_line_ref(anchor: str) -> str:
    """`src/a.py:120` → `src/a.py`. A line number is a POINTER INTO the anchored file, never part
    of its identity — the file is what gets hashed, and a decision does not go stale because its
    subject moved down twelve lines."""
    text = (anchor or "").strip()
    head, sep, tail = text.rpartition(":")
    if sep and head and tail.isdigit():
        return head
    return text


def classify_anchor(anchor: str, root: str,
                    record: Optional[Dict[str, Dict[str, Any]]] = None) -> str:
    """PATH or SYMBOL.

    **The RECORD wins when it has an opinion**, and that is a correctness property rather than a
    cache: a path anchor whose file was DELETED would otherwise re-classify as a symbol on its next
    evaluation and start asking the graph about a filename. The record remembers what it was.
    """
    text = (anchor or "").strip()
    if record:
        entry = record.get(text) or {}
        shape = entry.get("shape")
        if shape in (SHAPE_PATH, SHAPE_SYMBOL):
            return shape
    candidate = split_line_ref(text)
    if not candidate:
        return SHAPE_SYMBOL
    try:
        if os.path.isfile(os.path.join(root, candidate)):
            return SHAPE_PATH
    except (OSError, ValueError):  # a NUL byte / an over-long name is not a file, and not a crash
        pass
    if "/" in candidate or os.sep in candidate or candidate.endswith(_PATH_SUFFIXES):
        return SHAPE_PATH
    return SHAPE_SYMBOL


# ==========================================================================================
# current evidence
# ==========================================================================================
def _hash_file(root: str, rel: str) -> Tuple[str, str]:
    """(fingerprint, decline-reason). One of the two is always empty."""
    try:
        ab = os.path.join(root, rel)
        if not os.path.isfile(ab):
            return "", DECLINE_ABSENT
        return file_fingerprint(ab)[0], ""
    except (OSError, ValueError):
        return "", DECLINE_UNREADABLE


def _path_evidence(root: str, anchor: str) -> Tuple[str, str, str]:
    """(fingerprint, path, decline-reason) for a PATH anchor. **The graph is not consulted** — P3
    is enforced by this function having no `layer` parameter to consult it with."""
    rel = split_line_ref(anchor)
    fp, reason = _hash_file(root, rel)
    return fp, rel, reason


def _defining_paths(layer: Any, symbol: str) -> Tuple[List[str], str]:
    """The files the AUTHORITATIVE graph says define `symbol` — or a decline reason.

    Every gate here is a capability the floor does not have, checked in the order that makes the
    reason accurate: not a graph at all ⇒ NO_GRAPH; a graph that maps no `defs` ⇒ NO_DEFS (the real
    CRG's case); a raise ⇒ GRAPH_FAILED; a DEGRADED answer ⇒ the floor answered it after all, which
    is NO_GRAPH by another route; no references ⇒ UNRESOLVED.
    """
    primary = getattr(layer, "primary", None)
    if not getattr(primary, "is_graph", False):
        return [], DECLINE_NO_GRAPH
    supports = getattr(primary, "supports_kind", None)
    if callable(supports):
        try:
            if not supports("defs"):
                return [], DECLINE_NO_DEFS
        except Exception:  # noqa: BLE001 — a misbehaving probe is not a resolution
            return [], DECLINE_GRAPH_FAILED
    try:
        result = primary.query("defs", symbol)
    except Exception:  # noqa: BLE001 — any client/process failure ⇒ no evidence, never a claim
        return [], DECLINE_GRAPH_FAILED
    if getattr(result, "degraded", False):
        return [], DECLINE_NO_GRAPH        # the floor answered; that is not authoritative
    paths = sorted({str(getattr(r, "path", "") or "")
                    for r in (getattr(result, "references", None) or [])
                    if getattr(r, "path", "")})
    if not paths:
        return [], DECLINE_UNRESOLVED
    return paths, ""


def _symbol_evidence(root: str, anchor: str, layer: Any) -> Tuple[str, str, str]:
    """(fingerprint, path, decline-reason) for a SYMBOL anchor.

    The fingerprint is a COMPOSITE over `<path>:<hash>` for every defining site, sorted. Composing
    the PATH in is deliberate: a symbol that moves to a byte-identical new file has moved, and a
    hash-only fingerprint would call that unchanged.
    """
    paths, reason = _defining_paths(layer, split_line_ref(anchor))
    if reason:
        return "", "", reason
    parts: List[str] = []
    for rel in paths:
        fp, why = _hash_file(root, rel)
        if why:
            return "", ", ".join(paths), why
        parts.append(f"{rel}:{fp}")
    import hashlib
    digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
    return digest, ", ".join(paths), ""


def current_evidence(anchor: str, *, root: str, layer: Any = None,
                     record: Optional[Dict[str, Dict[str, Any]]] = None
                     ) -> Tuple[str, str, str, str]:
    """(shape, fingerprint, path, decline-reason) — the current state of ONE anchor."""
    shape = classify_anchor(anchor, root, record=record)
    if shape == SHAPE_PATH:
        fp, path, reason = _path_evidence(root, anchor)
    else:
        fp, path, reason = _symbol_evidence(root, anchor, layer)
    return shape, fp, path, reason


# ==========================================================================================
# the verdict — the ONE piece of evidence every H-6 surface reads (P4)
# ==========================================================================================
def evaluate_anchor(anchor: str, *, root: str, layer: Any = None,
                    record: Optional[Dict[str, Dict[str, Any]]] = None) -> AnchorVerdict:
    """MOVED / UNCHANGED / DECLINED for one anchor. PURE — reads files and (for a symbol) the
    graph; writes nothing, in particular never repairs a baseline it found stale (P7)."""
    rec = read_record(root) if record is None else record
    shape, current, path, reason = current_evidence(anchor, root=root, layer=layer, record=rec)
    if reason:
        return AnchorVerdict(anchor=anchor, shape=shape, verdict=DECLINED, path=path,
                             reason=reason)
    recorded = str((rec.get(anchor) or {}).get("fingerprint", "") or "")
    if not recorded:
        return AnchorVerdict(anchor=anchor, shape=shape, verdict=DECLINED, current=current,
                             path=path, reason=DECLINE_NO_BASELINE)
    if fingerprint_forces_refresh(recorded, current):
        return AnchorVerdict(anchor=anchor, shape=shape, verdict=MOVED, recorded=recorded,
                             current=current, path=path)
    return AnchorVerdict(anchor=anchor, shape=shape, verdict=UNCHANGED, recorded=recorded,
                         current=current, path=path)


def evaluate_anchors(anchors: Sequence[str], *, root: str, layer: Any = None,
                     record: Optional[Dict[str, Dict[str, Any]]] = None) -> List[AnchorVerdict]:
    """Verdicts for a list of anchors, reading the record ONCE. Never raises."""
    rec = read_record(root) if record is None else record
    out: List[AnchorVerdict] = []
    for a in anchors or []:
        if not a:
            continue
        try:
            out.append(evaluate_anchor(a, root=root, layer=layer, record=rec))
        except Exception as exc:  # noqa: BLE001 — one bad anchor never costs the others a verdict
            out.append(AnchorVerdict(anchor=str(a), shape=SHAPE_SYMBOL, verdict=DECLINED,
                                     reason=f"{DECLINE_GRAPH_FAILED}: {exc}"))
    return out


# ==========================================================================================
# minting the baseline
# ==========================================================================================
def record_anchors(root: str, anchors: Sequence[str], *, layer: Any = None,
                   refresh: bool = False) -> List[str]:
    """Mint (or, with `refresh=True`, re-stamp) baselines. Returns the anchors written.

    **`refresh` is off by default and that is P7, not ergonomics.** Silently re-stamping an anchor
    whose fingerprint had moved is exactly the "you are looking at old code, quietly relabelled as
    current" failure the DB.S7c2 half named (`govern/stale_ref_gate.py:21-24`). A re-stamp happens
    when a human has DECIDED, and at no other time.

    An anchor with no current evidence (a symbol on the floor, an absent file) is NOT recorded —
    there is nothing honest to write down. NEVER raises: this is derived bookkeeping on a read
    path, and its worst failure is one more session with no baseline.
    """
    staged: List[str] = []
    try:
        rec = read_record(root)
        for anchor in anchors or []:
            if not anchor:
                continue
            if not refresh and (rec.get(anchor) or {}).get("fingerprint"):
                continue
            shape, fp, path, reason = current_evidence(anchor, root=root, layer=layer, record=rec)
            if reason or not fp:
                continue
            rec[anchor] = {"shape": shape, "fingerprint": fp, "path": path}
            staged.append(anchor)
        if staged:
            _write_record(root, rec)
    except Exception:  # noqa: BLE001 — bookkeeping must never break the path that called it
        # The return names what REACHED DISK, never what was staged. A first draft returned the
        # staged list from here and the pin caught it: a caller told "these three are recorded"
        # would go on to trust a baseline that a failed `atomic_write_text` never persisted, and
        # would then read every one of them as UNCHANGED next session for the wrong reason.
        return []
    return staged


@dataclass(frozen=True)
class AnchorSignal:
    """H-6 S2 — the freshness lane's view of the record: which anchored files have moved, and how
    many were left unchecked. `skipped` exists for the reason every GR.S4 bound reports itself — a
    bounded pass that trims silently reads as a complete one."""

    paths: List[str]
    scanned: int = 0
    skipped: int = 0


# A BACKSTOP, not a tuning knob. A repo with 500 distinct `about_code` anchors is already
# pathological, and the cost here is one content hash per anchor on a path `_reconcile` runs before
# every graph query. Past it the pass is bounded and SAYS SO (the `FRESHNESS_CHANGE_CAP` shape:
# costed note, never a block).
ANCHOR_SCAN_CAP = 500


def anchor_signal(root: str, *, cap: int = ANCHOR_SCAN_CAP,
                  record: Optional[Dict[str, Dict[str, Any]]] = None) -> AnchorSignal:
    """The bounded form of `moved_paths`, for `_reconcile`.

    **No handler here, deliberately.** A first draft wrapped this in `except Exception` and the
    mutation pass proved the arm UNREACHABLE: `read_record` already answers `{}` for an absent,
    unreadable or malformed record, and `evaluate_anchor` already declines on every file-level
    failure — so there is nothing left for this frame to catch. The boundary that genuinely matters
    is one level up (`FreshnessController._anchor_signal`), where an exception WOULD cost a query
    its answer, and that is where the handler lives and is pinned. Two handlers for one boundary
    would mean the outer one is never exercised and never trusted.
    """
    rec = read_record(root) if record is None else record
    keys = sorted(rec)
    head, tail = keys[:cap], keys[cap:]
    moved = moved_paths(root, {k: rec[k] for k in head})
    return AnchorSignal(paths=moved, scanned=len(head), skipped=len(tail))


def moved_paths(root: str, record: Optional[Dict[str, Dict[str, Any]]] = None) -> List[str]:
    """The PATH-shaped anchors whose file has moved since it was recorded — the freshness tripwire's
    input (S2).

    **Path-shaped only, and it is STRUCTURAL rather than a filter.** This function takes no `layer`
    and passes none, so `_symbol_evidence` can only ever answer `DECLINE_NO_GRAPH` — a symbol anchor
    cannot reach MOVED from here at all. A `shape != SHAPE_PATH` guard was written first and the
    mutation pass proved it changed NOTHING, which is worth stating rather than leaving as
    reassuring dead code: the guarantee comes from the absent layer, and the AST pin asserts exactly
    that (`moved_paths` never hands a layer to anything).

    Why it MUST hold: `_reconcile` knows nothing of memory items and must not acquire a code-graph
    read to serve one — a freshness reconcile that queried the graph to decide whether to rebuild
    the graph is a loop, not a signal.
    """
    rec = read_record(root) if record is None else record
    out: List[str] = []
    for anchor in sorted(rec):
        v = evaluate_anchor(anchor, root=root, layer=None, record=rec)
        if v.moved:
            out.append(split_line_ref(anchor))
    return sorted(set(out))
