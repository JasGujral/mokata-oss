"""I3 — full audit ledger (MS.S3: hash-chain + cross-process lock + O(1) seq).

An append-only JSONL record spanning every gate decision, tool call, and durable write.
Append-only by construction (entries are only ever added, each with a monotonic seq), so
a human can reconstruct and walk back any choice the system made (P7). The ledger is the
proof substrate for every gate claim (P16), so its INTEGRITY is the product's honesty:

  * **Cross-process locked append (M-3/B1).** Two Claude Code windows on one repo are two MCP
    processes appending this SAME file; a `threading.Lock` is worthless across them. Every append
    holds `oslock.file_lock` (the ONE shared cross-process lock, MS.S1) across read-tail →
    assign-seq → append → fsync, so concurrent writers never interleave, drop, or collide on a seq.
    The lock is reentrant IN-PROCESS so the WriteGate can hold it across its commit + approved
    record (see `hold`), making the store's `len+1` approval-id prediction provably exact (B2).

  * **O(1) seq + count.** A tiny sidecar counter (`.<ledger>.count`) caches the last seq, the chain
    tip hash, and the file size, so assigning the next seq / reporting `len` does NOT re-read the
    whole file. It is a pure CACHE: missing or size-inconsistent (a crash between append and counter
    update, or an external append) → rebuilt by a one-time scan (self-healing, degrade-clean).

  * **Hash-chain.** Each NEW entry carries `prev_hash` + `entry_hash` (sha256 over its canonical
    JSON, covering seq + payload + prev_hash). Tampering with any chained entry, or truncating the
    tail, is detectable by `verify`. EXISTING ledgers are NOT migrated: legacy entries have no hash
    fields and stay valid; the chain starts at the FIRST new entry (its `prev_hash` is GENESIS) —
    the pre-chain span is reported as a boundary, never a failure.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional, Tuple

from .. import TEMP_LOCAL_DIRNAME
from ..oslock import file_lock

AUDIT_DIRNAME = "audit"
LEDGER_FILENAME = "ledger.jsonl"

# The genesis link for the first chained entry (and the boundary marker when a new chain is started
# on top of a pre-MS.S3 ledger whose last entry carries no hash to link back to).
GENESIS = "0" * 64
# The one field NOT covered by an entry's own hash (it IS the hash).
_HASH_FIELD = "entry_hash"


class _ReentrantFileLock:
    """One process-global, path-keyed reentrant wrapper over `oslock.file_lock`. The OS advisory
    lock is per open-file-description and NOT reentrant, so a nested append in the SAME process —
    even via a DIFFERENT `AuditLedger` instance on the same file (e.g. an MCP tool's gate wrapping
    the store's gated write) — would self-conflict and hang. Keying the reentrancy state on the lock
    PATH (not the instance) lets any nested holder in this process reuse the already-held lock. A
    per-path `RLock` serialises threads (and permits same-thread re-entry); the file lock is taken
    only at the outermost depth. Cross-PROCESS mutual exclusion is unchanged — that is the file
    lock's job, and a second process still blocks on it."""

    _registry: Dict[str, "_ReentrantFileLock"] = {}
    _registry_guard = threading.Lock()

    def __init__(self) -> None:
        self._rlock = threading.RLock()
        self._depth = 0
        self._file_cm: Any = None

    @classmethod
    def for_path(cls, lock_path: str) -> "_ReentrantFileLock":
        key = os.path.abspath(lock_path)
        with cls._registry_guard:
            inst = cls._registry.get(key)
            if inst is None:
                inst = cls._registry[key] = cls()
            inst._path = lock_path
            return inst

    @contextmanager
    def hold(self) -> Iterator[None]:
        self._rlock.acquire()
        try:
            if self._depth == 0:
                self._file_cm = file_lock(self._path)
                self._file_cm.__enter__()
            self._depth += 1
            try:
                yield
            finally:
                self._depth -= 1
                if self._depth == 0:
                    cm, self._file_cm = self._file_cm, None
                    cm.__exit__(None, None, None)
        finally:
            self._rlock.release()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _entry_hash(entry: Dict[str, Any]) -> str:
    """The canonical sha256 of `entry`, covering every field EXCEPT `entry_hash` itself — i.e. seq
    + kind + at + payload + prev_hash. Deterministic (sorted keys, compact separators) so the same
    logical entry always hashes identically across processes and Python runs."""
    payload = {k: v for k, v in entry.items() if k != _HASH_FIELD}
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass
class ChainReport:
    """The result of walking the ledger's hash-chain (read-only). Reports seqs/status only — it
    never echoes payloads, so it adds no secret surface."""
    intact: bool
    checked: int                       # number of chained (hashed) entries verified
    pre_chain_count: int               # leading legacy entries with no hash (the pre-chain boundary)
    chain_start_seq: Optional[int]     # seq of the first chained entry (None if none are chained)
    first_break_seq: Optional[int] = None   # seq at which the chain first breaks (None if intact)
    reason: str = ""                   # human label for the break ("" when intact)

    def render(self) -> str:
        if self.intact:
            tail = "" if not self.pre_chain_count else (
                f" (first {self.pre_chain_count} entries pre-chain; chain starts at "
                f"seq {self.chain_start_seq})")
            return f"audit ledger chain intact — {self.checked} entries verified{tail}"
        return (f"audit ledger chain BROKEN at seq {self.first_break_seq}: {self.reason}")


def verify_chain(entries: List[Dict[str, Any]]) -> ChainReport:
    """Walk `entries` and verify the hash-chain (pure; no I/O). Leading legacy entries with no
    `entry_hash` are the pre-chain boundary (counted, not failed); from the first chained entry on,
    each entry's `entry_hash` must recompute and its `prev_hash` must link to the previous chained
    entry (GENESIS for the first). Reports the FIRST broken seq. Interior-only — tail truncation is
    caught by :meth:`AuditLedger.verify`, which also has the recorded tip to compare against."""
    pre_chain = 0
    chain_start: Optional[int] = None
    prev_hash: Optional[str] = None      # entry_hash of the previous CHAINED entry (None = not yet)
    checked = 0
    for e in entries:
        if _HASH_FIELD not in e:
            if chain_start is None:
                pre_chain += 1
                continue
            # A gap in the middle of the chain (a chained entry followed by an unhashed one) is a
            # break, not a boundary — the boundary is only the leading pre-chain prefix.
            return ChainReport(False, checked, pre_chain, chain_start, e.get("seq"),
                               "missing hash inside the chain (entry stripped of its chain fields)")
        seq = e.get("seq")
        if chain_start is None:
            chain_start = seq
        if _entry_hash(e) != e.get(_HASH_FIELD):
            return ChainReport(False, checked, pre_chain, chain_start, seq,
                               "entry_hash mismatch (payload or seq altered after the fact)")
        expected_prev = prev_hash if prev_hash is not None else GENESIS
        if e.get("prev_hash") != expected_prev:
            return ChainReport(False, checked, pre_chain, chain_start, seq,
                               "prev_hash mismatch (an entry was removed, reordered, or inserted)")
        prev_hash = e[_HASH_FIELD]
        checked += 1
    return ChainReport(True, checked, pre_chain, chain_start)


class AuditLedger:
    def __init__(self, path: str) -> None:
        self.path = path
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)

    @classmethod
    def from_mokata_dir(cls, mokata_dir: str) -> "AuditLedger":
        # The ledger is transient runtime data (Stage 24D): under .mokata/temp_local/.
        return cls(os.path.join(mokata_dir, TEMP_LOCAL_DIRNAME, AUDIT_DIRNAME,
                                LEDGER_FILENAME))

    # --- cross-process locking (reentrant in-process) -----------------------------------------
    def _lock_path(self) -> str:
        d, base = os.path.dirname(self.path), os.path.basename(self.path)
        return os.path.join(d, f".{base}.lock")   # a dedicated sidecar handle (never the ledger)

    def hold(self):
        """Hold the cross-process append lock for the duration of the block. Reentrant per-process
        (keyed on the lock path, so nested holders via any instance in this process reuse it): the
        WriteGate wraps its commit() + approved-record in one `hold()` so no other writer can append
        between the store's `len+1` prediction and the approved entry it predicts (B2)."""
        return _ReentrantFileLock.for_path(self._lock_path()).hold()

    # --- O(1) seq/count sidecar counter -------------------------------------------------------
    def _counter_path(self) -> str:
        d, base = os.path.dirname(self.path), os.path.basename(self.path)
        return os.path.join(d, f".{base}.count")

    def _read_counter(self) -> Optional[Dict[str, Any]]:
        try:
            with open(self._counter_path(), encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict) and isinstance(data.get("seq"), int):
                return data
        except (OSError, ValueError):
            pass
        return None

    def _write_counter(self, seq: int, tip_hash: str) -> None:
        """Persist the O(1) tail cache atomically (temp + os.replace). It is only a cache — a lost
        or torn write is self-healed by :meth:`_tail` on the next append (size-mismatch → rescan)."""
        size = os.path.getsize(self.path) if os.path.exists(self.path) else 0
        data = {"seq": seq, "tip_hash": tip_hash, "size": size}
        cpath = self._counter_path()
        tmp = cpath + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(data))
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, cpath)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def _scan_tail(self) -> Tuple[int, str]:
        """One-time O(n) scan of the ledger returning (last_seq, tip_hash) — the self-healing
        fallback when the counter is absent or inconsistent. tip_hash is the last entry's
        `entry_hash`, or GENESIS if the ledger is empty or its tail is a pre-chain (unhashed) entry
        (so a new chain starts cleanly at the boundary)."""
        last_seq = 0
        tip = GENESIS
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(rec.get("seq"), int):
                        last_seq = rec["seq"]
                    tip = rec.get(_HASH_FIELD, GENESIS)
        return last_seq, tip

    def _tail(self) -> Tuple[int, str]:
        """(last_seq, tip_hash) via the O(1) counter when it is consistent with the file's current
        size; otherwise a rescan (which also repairs the cache on the next write). Called only under
        the append lock, so the size read cannot race a concurrent append."""
        cache = self._read_counter()
        size = os.path.getsize(self.path) if os.path.exists(self.path) else 0
        if cache is not None and cache.get("size") == size:
            return cache["seq"], cache.get("tip_hash", GENESIS)
        return self._scan_tail()

    def record(self, kind: str, **fields: Any) -> Dict[str, Any]:
        """Append one entry and return it (with its assigned `seq` + chain fields). Never rewrites
        existing entries. Cross-process locked across read-tail → assign-seq → hash → append →
        fsync, so concurrent windows never drop an entry or collide on a seq; the seq is assigned
        O(1) from the sidecar counter."""
        with self.hold():
            last_seq, prev_hash = self._tail()
            entry: Dict[str, Any] = {"seq": last_seq + 1, "kind": kind, "at": _now_iso()}
            entry.update(fields)
            entry["prev_hash"] = prev_hash
            entry[_HASH_FIELD] = _entry_hash(entry)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            self._write_counter(entry["seq"], entry[_HASH_FIELD])
            return entry

    def entries(self) -> List[Dict[str, Any]]:
        if not os.path.exists(self.path):
            return []
        out: List[Dict[str, Any]] = []
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    def count(self) -> int:
        """The number of entries — O(1) from the sidecar counter when consistent, else a rescan
        (self-healing). Reads the ledger's size (a stat), never its whole contents, on the hot
        path. Under no lock: the counter is written atomically, so a reader sees old-or-new."""
        cache = self._read_counter()
        size = os.path.getsize(self.path) if os.path.exists(self.path) else 0
        if cache is not None and cache.get("size") == size:
            return cache["seq"]
        last_seq, _ = self._scan_tail()
        return last_seq

    def __len__(self) -> int:
        return self.count()

    def verify(self) -> ChainReport:
        """Walk the hash-chain AND cross-check the tail against the recorded tip — the full
        integrity check (read-only). `verify_chain` catches interior tampering (an altered payload
        or a removed/reordered entry); the counter comparison additionally catches a TRUNCATED tail
        (entries lopped off the end still form a valid prefix, so only the recorded tip reveals
        them). Reports seqs/status only."""
        entries = self.entries()
        report = verify_chain(entries)
        if not report.intact:
            return report
        cache = self._read_counter()
        if cache is not None and isinstance(cache.get("seq"), int):
            chained = [e for e in entries if _HASH_FIELD in e]
            last_seq = chained[-1]["seq"] if chained else 0
            # The counter remembers we wrote up to `cache['seq']`; a file that now tops out BELOW
            # that had its tail removed after the fact.
            if cache["seq"] > last_seq:
                return ChainReport(
                    False, report.checked, report.pre_chain_count, report.chain_start_seq,
                    last_seq + 1, "tail truncated (entries removed after the recorded tip)")
        return report


# --------------------------------------------------------------------------- Stage 49 —
# decision observability: a read-only, bounded "what + decision + WHY" timeline derived from
# the ledger. Pure (no I/O beyond the caller's entries), frugal (a tail, not the whole
# history), local-first. Surfaces whatever rationale each entry carries.

WHY_TIMELINE_TAIL = 50

# A human label per ledger kind (falls back to the raw kind for anything unmapped).
_WHY_WHAT = {
    "deviation": "deviation gate", "spec_conflict": "spec-awareness",
    "write_gate": "write gate", "karpathy_gate": "karpathy gate",
    "healing_decision": "self-healing", "consolidation_proposal": "consolidation (proposed)",
    "consolidation_decision": "consolidation", "rule_promotion_proposed": "rule learning",
    "rule_promotion_decision": "rule promotion", "outbound": "outbound gate",
    "rule_override": "rule override", "rule_block": "rule enforcement",
    "enforcement_change": "enforcement promotion", "scope_promotion": "scope promotion",
    "review_transition": "review workflow", "review_rollback": "review rollback",
    "model_route": "model routing", "phase": "pipeline phase", "subagent": "subagent",
    "sequential": "task", "finish": "ship", "spec_check": "spec-awareness",
    "revert": "revert", "reversible_write": "write",
}
# Where the human-readable rationale lives, in priority order (different layers named it
# differently before this stage; we read them all so the WHY always surfaces if present).
_WHY_REASON_KEYS = ("reason", "why", "rationale", "detail", "message", "diff")
_WHY_SUBJECT_KEYS = ("target", "subject", "gate", "pattern", "action", "task", "phase")
_WHY_DECISION_KEYS = ("decision", "passed", "allowed", "changed", "added", "ok", "to")


def _why_pick(entry: Dict[str, Any], keys) -> str:
    for k in keys:
        if k in entry and entry[k] not in (None, ""):
            return str(entry[k])
    return ""


def why_timeline(entries: List[Dict[str, Any]],
                 tail: int = WHY_TIMELINE_TAIL) -> List[str]:
    """A readable what+decision+why line per entry, bounded to the last `tail` (frugal, P11).
    Read-only: derives from the given entries and returns strings; writes nothing."""
    rows = list(entries)[-tail:] if tail and tail > 0 else list(entries)
    out: List[str] = []
    for e in rows:
        kind = e.get("kind", "")
        what = _WHY_WHAT.get(kind, kind or "event")
        if e.get("simulated"):
            # B3 — an entry nothing executed reads as simulated, so its `ok` can't pass for a
            # verdict in the timeline a human walks back.
            what += " (simulated)"
        subject = _why_pick(e, _WHY_SUBJECT_KEYS)
        decision = _why_pick(e, _WHY_DECISION_KEYS)
        why = _why_pick(e, _WHY_REASON_KEYS)
        line = f"#{e.get('seq', '?'):<4} {what}"
        if subject:
            line += f" · {subject}"
        if decision:
            line += f" — {decision}"
        if why:
            line += f"  (why: {why})"
        out.append(line)
    return out
