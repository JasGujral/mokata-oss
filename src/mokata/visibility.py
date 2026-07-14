"""Stage 60 — trust & visibility: the "what changed since last session" diff.

You should always be able to see what mokata did. This module derives a concise, READ-ONLY
diff of the governed state against a lightweight **last-session snapshot**:

  * `build_state_fingerprint(surface)` — a small, deterministic fingerprint of the governed
    state (active memory by subject, the always-on rule subjects, the audit-ledger length).
    Read-only: it reads memory via the NON-counting `peek_active` path (no stat bump) and the
    ledger length — it mutates nothing.
  * `compute_session_diff(surface)` — compares the current fingerprint against the stored
    snapshot and returns a `SessionDiff` (new / changed memory, new rules, new gate decisions).
    PURE + read-only — it DERIVES, it never writes. Degrade-clean: no prior snapshot ⇒ a
    friendly "first session" diff.
  * `capture_session_snapshot(surface)` — writes the fingerprint to the gitignored, transient
    `temp_local/` (the same place the dashboards + ledger live — NOT the committed governed
    state). This is the ONLY writer here, and it is read-only on the governed state itself; it
    is invoked at the session boundary (the SessionStart hook), separately from the diff.

So the snapshot baseline advances once per session start: the SessionStart briefing shows what
changed while you were away, and the `mokata govern` view shows what's changed since this
session began — both compared against the last captured baseline.

Inviolables: read-only / derived (no durable mutation, no stat bumps); deterministic (no
wall-clock in the rendered diff); frugal/bounded (a short summary + a bounded decision tail,
never a dump); degrade-clean; core dependency-free; clean-room; Apache-2.0.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import TEMP_LOCAL_DIRNAME
from .atomicfile import atomic_write_text

SESSION_SNAPSHOT_FILENAME = "session_snapshot.json"
# Frugal/bounded (P11): the diff names at most this many fresh gate decisions, never the whole
# ledger; the counts always reflect the true totals even when the list is clipped.
SESSION_DIFF_DECISION_TAIL = 12

# The ledger kinds that represent an actual gate/landing DECISION worth surfacing in the diff
# (not every tool-call row). Reuses the ledger's own kinds; anything else is ignored.
_DECISION_KINDS = {
    "write_gate", "karpathy_gate", "deviation", "spec_conflict", "outbound",
    "healing_decision", "consolidation_decision", "rule_promotion_decision",
    "finish", "revert", "reversible_write",
}


def _snapshot_path(surface: Any) -> str:
    return os.path.join(surface.mokata_dir, TEMP_LOCAL_DIRNAME, SESSION_SNAPSHOT_FILENAME)


def build_state_fingerprint(surface: Any) -> Dict[str, Any]:
    """A small, deterministic fingerprint of the governed state. READ-ONLY — memory is read via
    the non-counting `peek_active` path (no stat bump); nothing is written.

    D5 — the fingerprint now says whether it is COMPLETE (`"partial": True` when the memory read
    failed). One root cause did two different harms here, and the second is the worse:

      (a) the "since last session" diff compared the real snapshot against an EMPTY memory set, so
          every remembered item read as DELETED (or, symmetrically, nothing read as new);
      (b) `capture_session_snapshot` then PERSISTED that empty fingerprint as the new BASELINE — so
          the next session, with a perfectly healthy store, reported every memory item as "+ new
          memory". A transient read failure corrupted the baseline for every session after it.

    (b) is why `capture_session_snapshot` now REFUSES to write a partial fingerprint. That is the
    one deliberate behaviour change in D5: a baseline built from a failed read is not a baseline."""
    from sqlite3 import Error as SQLiteError
    # mokata's OWN typed memory error, NOT the builtin `MemoryError` (an out-of-memory condition,
    # which must never be swallowed). Bound before the try so the except clause is always valid.
    from .memory.store import MemoryError as MemoryStoreError

    memory: Dict[str, str] = {}
    rules: List[str] = []
    partial = False
    try:
        from .memory import MemoryStore
        from .memory.item import ALWAYS_ON_KINDS
        store = MemoryStore.from_surface(surface)
        if store.enabled_types:
            for it in store.peek_active():        # non-counting read (no _bump_read)
                memory[it.subject] = it.value
                if it.effective_kind in ALWAYS_ON_KINDS:
                    rules.append(it.subject)
    except (MemoryStoreError, OSError, ImportError, SQLiteError) as exc:
        from .degrade import FAILURE_UNREACHABLE, note_degraded
        partial = True
        note_degraded("memory", FAILURE_UNREACHABLE,
                      fallback="this view is NOT your memory — the store could not be read",
                      fix="run `mokata doctor`", detail=str(exc))
    ledger_count = 0
    try:
        from .govern import AuditLedger
        ledger_count = len(AuditLedger.from_mokata_dir(surface.mokata_dir).entries())
    except (OSError, ValueError):
        # OSError: the ledger file is unreadable. ValueError: a torn JSONL line (JSONDecodeError IS
        # a ValueError). A zero count reads as "no decisions yet" — the honest floor for a ledger
        # that is genuinely absent, and the ledger is an append-only observability log, not the
        # governed state the baseline protects.
        pass
    return {"memory": memory, "rules": sorted(set(rules)), "ledger_count": ledger_count,
            "partial": partial}


def _read_snapshot(surface: Any) -> Optional[Dict[str, Any]]:
    """The stored last-session snapshot, or None when there is none / it's unreadable."""
    path = _snapshot_path(surface)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def capture_session_snapshot(surface: Any) -> str:
    """Write the current fingerprint as the new last-session baseline, under the gitignored
    transient `temp_local/`. READ-ONLY on the governed state (the fingerprint uses peek_active);
    the only durable thing touched is the transient snapshot file. Returns the path.

    D5 — REFUSES to write a PARTIAL fingerprint (a memory read that failed). Persisting one would
    take a transient failure and make it permanent: the empty memory set becomes the baseline, and
    every subsequent session — with a perfectly healthy store — reports every remembered item as "+
    new memory". The old baseline is left in place (a stale baseline is merely stale; a false one is
    a lie the diff repeats forever), and the returned path names the snapshot NOT written.

    This is the ONE place in D5 where the fallback does not simply fall back. `note_degraded` has
    already spoken inside `build_state_fingerprint`, so the refusal is never silent either."""
    fp = build_state_fingerprint(surface)
    path = _snapshot_path(surface)
    if fp.get("partial"):
        return path
    # MS.S6 — an ATOMIC replace (same bytes). The snapshot is a single global baseline that two
    # windows legitimately overwrite (last-writer-wins is the existing, intended semantics — a
    # per-session baseline would be an MS.S2 scoping decision, not a race fix); what was NOT
    # intended is the diff reader in another process seeing a half-written fingerprint.
    atomic_write_text(path, json.dumps(fp, indent=2, sort_keys=True) + "\n")
    return path


@dataclass
class SessionDiff:
    """The read-only, derived diff of the governed state vs the last-session snapshot."""
    first_session: bool
    new_memory: List[str] = field(default_factory=list)
    changed_memory: List[str] = field(default_factory=list)
    new_rules: List[str] = field(default_factory=list)
    new_decisions: List[str] = field(default_factory=list)   # bounded why_timeline lines
    decision_count: int = 0                                   # true total (may exceed the tail)
    degraded: bool = False        # D5 — computed against an INCOMPLETE read of current memory

    @property
    def has_changes(self) -> bool:
        return bool(self.new_memory or self.changed_memory or self.new_rules
                    or self.new_decisions)

    def summary_line(self) -> str:
        """One bounded, deterministic line — the headline for the briefing / the govern view."""
        if self.degraded:
            # D5 — a diff against an unreadable store is not "no changes"; it is "no comparison".
            return ("memory could not be read — this diff is INCOMPLETE (run `mokata doctor`).")
        if self.first_session:
            return "first session — no prior snapshot to compare yet."
        if not self.has_changes:
            return "no changes since last session."
        bits: List[str] = []
        if self.new_memory:
            bits.append(f"+{len(self.new_memory)} memory")
        if self.changed_memory:
            bits.append(f"~{len(self.changed_memory)} changed")
        if self.new_rules:
            bits.append(f"+{len(self.new_rules)} rule(s)")
        if self.decision_count:
            bits.append(f"{self.decision_count} decision(s)")
        return "since last session: " + " · ".join(bits)

    def detail_lines(self) -> List[str]:
        """A bounded, deterministic breakdown for the govern view (read-only)."""
        if self.degraded or self.first_session or not self.has_changes:
            return [self.summary_line()]
        out = [self.summary_line()]
        for s in self.new_memory:
            out.append(f"+ memory: {s}")
        for s in self.changed_memory:
            out.append(f"~ memory: {s}")
        for s in self.new_rules:
            out.append(f"+ rule: {s}")
        out.extend(self.new_decisions)      # already why_timeline lines (no wall-clock)
        return out


def _new_decision_lines(surface: Any, since_count: int) -> "tuple[List[str], int]":
    """The why_timeline lines for gate/landing DECISIONS appended since the snapshot — bounded.
    Returns (lines, true_total). Read-only; derives from the ledger entries only."""
    try:
        from .govern import AuditLedger
        from .govern.ledger import why_timeline
        entries = AuditLedger.from_mokata_dir(surface.mokata_dir).entries()
    except (OSError, ValueError):
        # OSError: unreadable ledger file. ValueError: a torn JSONL line (JSONDecodeError IS a
        # ValueError). An empty decision tail reads as "no decisions since the snapshot" — the same
        # thing an absent ledger legitimately means; the memory half of the diff is the load-bearing
        # one and it reports its own failure (`partial`).
        return [], 0
    fresh = [e for e in entries[max(since_count, 0):] if e.get("kind") in _DECISION_KINDS]
    return why_timeline(fresh, tail=SESSION_DIFF_DECISION_TAIL), len(fresh)


def compute_session_diff(surface: Any) -> SessionDiff:
    """Compare the current governed state against the stored last-session snapshot. PURE +
    READ-ONLY — it derives the diff and writes nothing. Degrade-clean: no prior snapshot ⇒
    `first_session` (a friendly empty diff).

    D5 — when the CURRENT memory read failed, the diff is marked `degraded` rather than computed:
    diffing a real snapshot against an empty set produces a confident, wrong answer ("nothing new")
    that is indistinguishable from the truth."""
    snap = _read_snapshot(surface)
    if snap is None:
        return SessionDiff(first_session=True)
    cur = build_state_fingerprint(surface)
    if cur.get("partial"):
        # `build_state_fingerprint` has already emitted the loud notice; do not compute a diff from
        # a read we know is incomplete — say so instead.
        return SessionDiff(first_session=False, degraded=True)
    old_mem = snap.get("memory", {}) if isinstance(snap.get("memory"), dict) else {}
    cur_mem = cur["memory"]
    new_memory = sorted(s for s in cur_mem if s not in old_mem)
    changed_memory = sorted(s for s in cur_mem if s in old_mem and old_mem[s] != cur_mem[s])
    old_rules = set(snap.get("rules", []) or [])
    new_rules = sorted(s for s in cur["rules"] if s not in old_rules)
    decisions, total = _new_decision_lines(surface, int(snap.get("ledger_count", 0) or 0))
    return SessionDiff(first_session=False, new_memory=new_memory,
                       changed_memory=changed_memory, new_rules=new_rules,
                       new_decisions=decisions, decision_count=total)


# The briefing surface — ONE bounded line, ABSENT (no noise) on a first session or no changes.
_SINCE_GLYPH = "▾"


def changed_since_line(surface: Any) -> Optional[str]:
    """The single, bounded "since last session" briefing line, or None when there's nothing to
    show (first session / no changes). Pure + read-only + degrade-clean (any error ⇒ None)."""
    try:
        diff = compute_session_diff(surface)
        if diff.degraded:
            # D5 — a degraded diff is exactly the case where the user must NOT get silence: the
            # briefing's silence means "nothing changed", and here nothing was compared.
            return f"{_SINCE_GLYPH} {diff.summary_line()}"
        if diff.first_session or not diff.has_changes:
            return None
        return f"{_SINCE_GLYPH} {diff.summary_line()} (`mokata govern` for the detail)"
    except Exception:
        # (iv) SUPPRESS-OK: this is ONE cosmetic line in the SessionStart briefing, and the briefing
        # must start even if it cannot. The callee is degrade-clean and already emits its own classed
        # notice for the failure that matters (an unreadable memory store) — so silence here loses no
        # signal. Broad because the callee spans memory + ledger + snapshot IO; re-listing its
        # classes here would drift from it, and a NameError in the briefing must not block a session.
        return None
