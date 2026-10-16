"""F6 — prompt-cache awareness.

Prompt caches hit only on byte-stable prefixes. This builds a deterministic prefix from
slow-changing, stable sources (manifest identity, the always-on rules, the constitution)
and exposes a check that a prefix is cache-stable across runs (identical fingerprint).
Keep volatile, per-run content OUT of the prefix so the cache keeps hitting.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

_SEP = "\n---\n"


def build_stable_prefix(parts: List[str]) -> str:
    """Join stable segments deterministically into a single cache prefix."""
    return _SEP.join(parts)


def prefix_fingerprint(prefix: str) -> str:
    return hashlib.sha256(prefix.encode("utf-8")).hexdigest()


def is_cache_stable(a: str, b: str) -> bool:
    """Two prefixes are cache-stable iff byte-identical (same fingerprint)."""
    return prefix_fingerprint(a) == prefix_fingerprint(b)


@dataclass
class CachePrefix:
    segments: List[str]

    def text(self) -> str:
        return build_stable_prefix(self.segments)

    def fingerprint(self) -> str:
        return prefix_fingerprint(self.text())


def stable_prefix_for(surface: Any) -> CachePrefix:
    """Assemble the cache-stable prefix for a project: manifest identity + the always-on
    rules + the constitution. All slow-changing; no live routing/state included."""
    from .rules import always_on_rules
    manifest = surface.manifest
    segments = [
        f"mokata {manifest.mokata_version} · profile {manifest.profile}",
        "\n".join(always_on_rules().lines),
        surface.constitution.text if getattr(surface, "constitution", None) else "",
    ]
    return CachePrefix(segments=segments)


# ======================================================================================
# HP.S5 (0.0.23 E1) — the prefix fingerprint at RUNTIME
# ======================================================================================
# `prefix_fingerprint` had no runtime caller: the stable prefix was BUILT into every briefing and
# its stability was only ever asserted by a unit test. This gives it one. Each session records the
# fingerprint of the mokata-controlled prefix (manifest identity · always-on rules · constitution)
# when it first sees it, and every later turn compares against that record. A change is surfaced as
# ONE line — observability, never a block — and logged as data PIPE-TOKEN-AUDIT reads.
#
# What the line can honestly claim: the briefing already in the transcript does not change mid-
# session, so a drifted prefix costs the cache at the NEXT briefing (a new session, a resume, a
# compaction), not this turn. The notice says exactly that.
#
# TRANSIENT RUN-STATE under `.mokata/temp_local/cache_prefix/` (the injection ledger's class:
# derived, disposable, ungated). It is written ONLY on a session's first sighting and on a change,
# never on an unchanged turn — the per-turn lane must not become a write per turn. NEVER RAISES.

PREFIX_WATCH_DIRNAME = "cache_prefix"
PREFIX_LOG_NAME = "observations.jsonl"
SEGMENT_NAMES = ("identity", "rules", "constitution")
# The observation log is bounded: past this size it is rewritten to its newest half.
_LOG_MAX_BYTES = 256 * 1024
_SHORT = 8


@dataclass
class PrefixObservation:
    """One look at the prefix. `changed` is against THIS session's last record (first sighting is
    never a change); `changed_segments` names which of SEGMENT_NAMES moved."""
    session: str
    event: str
    fingerprint: str
    segments: Dict[str, str]
    prefix_bytes: int
    previous: Optional[str] = None
    changed: bool = False
    changed_segments: List[str] = field(default_factory=list)
    recorded: bool = False

    def notice(self) -> str:
        """The one-line notice — empty when nothing changed (callers emit nothing)."""
        if not self.changed:
            return ""
        which = ", ".join(self.changed_segments) or "prefix"
        return (f"mokata: cache prefix changed this session ({which}: "
                f"{(self.previous or '')[:_SHORT]} → {self.fingerprint[:_SHORT]}) — the next "
                f"briefing (new session, resume or compaction) will miss the prompt cache once.")

    def to_record(self) -> Dict[str, Any]:
        return {
            "ts": round(time.time(), 3), "session": self.session, "event": self.event,
            "fingerprint": self.fingerprint, "segments": dict(self.segments),
            "prefix_bytes": self.prefix_bytes,
            "prefix_tokens_est": (self.prefix_bytes + 3) // 4,
            "previous": self.previous, "changed": self.changed,
            "changed_segments": list(self.changed_segments),
        }


def prefix_watch_dir(root: str) -> str:
    from .. import MOKATA_DIR, TEMP_LOCAL_DIRNAME
    return os.path.join(root, MOKATA_DIR, TEMP_LOCAL_DIRNAME, PREFIX_WATCH_DIRNAME)


def _session_key(session_id: Optional[str]) -> str:
    if session_id:
        return session_id
    try:
        from ..session import current_session_id
        return current_session_id()
    except Exception:  # noqa: BLE001 — identity resolution never breaks bookkeeping
        return "default"


def _state_path(root: str, session: str) -> str:
    safe = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in session) or "default"
    return os.path.join(prefix_watch_dir(root), f"last__{safe}.json")


def _read_state(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) and isinstance(data.get("fingerprint"), str) else None
    except (OSError, ValueError):
        return None


def _record_observation(root: str, obs: PrefixObservation) -> None:
    """Write the session's last-seen state and append the observation. Ungated run-state."""
    d = prefix_watch_dir(root)
    os.makedirs(d, exist_ok=True)
    state = _state_path(root, obs.session)
    tmp = state + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"fingerprint": obs.fingerprint, "segments": obs.segments}, fh, sort_keys=True)
    os.replace(tmp, state)
    log = os.path.join(d, PREFIX_LOG_NAME)
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(obs.to_record(), sort_keys=True) + "\n")
    if os.path.getsize(log) > _LOG_MAX_BYTES:
        with open(log, encoding="utf-8") as fh:
            lines = fh.readlines()
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.writelines(lines[len(lines) // 2:])
        os.replace(tmp, log)


def observe_prefix(surface: Any, root: str, session_id: Optional[str] = None, *,
                   event: str = "turn") -> Optional[PrefixObservation]:
    """Fingerprint the mokata-controlled prefix for this session and compare with its last record.

    Writes only on a first sighting or a change. Returns None (never raises) on any failure —
    an observability step that cannot observe says nothing."""
    try:
        cp = stable_prefix_for(surface)
        text = cp.text()
        segments = {name: prefix_fingerprint(seg)
                    for name, seg in zip(SEGMENT_NAMES, cp.segments)}
        session = _session_key(session_id)
        obs = PrefixObservation(session=session, event=event,
                                fingerprint=prefix_fingerprint(text), segments=segments,
                                prefix_bytes=len(text.encode("utf-8")))
        last = _read_state(_state_path(root, session))
        if last is not None:
            obs.previous = last["fingerprint"]
            obs.changed = obs.previous != obs.fingerprint
            if obs.changed:
                prev_segs = last.get("segments") if isinstance(last.get("segments"), dict) else {}
                obs.changed_segments = [n for n in SEGMENT_NAMES
                                        if prev_segs.get(n) != segments.get(n)]
        if last is None or obs.changed:
            try:
                _record_observation(root, obs)
                obs.recorded = True
            except OSError:
                obs.recorded = False
        return obs
    except Exception:  # noqa: BLE001 — observability never fails a session or a turn
        return None


def read_prefix_observations(root: str) -> List[Dict[str, Any]]:
    """Every logged observation, oldest first — the data PIPE-TOKEN-AUDIT reads. A missing or
    unreadable log is an empty list; a malformed line is skipped."""
    path = os.path.join(prefix_watch_dir(root), PREFIX_LOG_NAME)
    out: List[Dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    out.append(rec)
    except OSError:
        return []
    return out
