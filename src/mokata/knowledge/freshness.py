"""GR.S4 — the graph FRESHNESS contract (read-time, never a daemon).

doc 85 binds this hard: NO watcher, NO daemon, NO background process. Freshness is a
READ-TIME contract — every query reconciles the graph against three cheap signals BEFORE it
answers, and rebuilds a KNOWN-stale index rather than serving it:

  1. a session DIRTY-SET the `PostToolUse` async observability hook appends to (in-harness
     edits) — an atomic O(1) append to a transient run-state file (GR.S1-cache precedent,
     ungated: P2 is about DURABLE writes, this is run-tracking);
  2. one cheap `.git/HEAD` change probe (zero-subprocess SHA read) — HEAD moved ⇒ ONE batched
     `git diff --name-only <last-indexed-SHA>` (catches branch switch / commit / pull, and
     editor/out-of-band changes);
  3. a cold-start index walk — ONCE per session — to seed the mtime/hash baseline.

FRESHNESS-BEFORE-ANSWER INVARIANT (re-groom #5): a graph KNOWN stale never answers. A stale
index rebuilds first (AST incremental re-parse; CRG refresh via GR.S2(k)). Debounce only
COALESCES pending signals into ONE rebuild — it never permits a stale answer. A rebuild
FAILURE answers from the AST floor on CURRENT files with a loud classed note, never from stale
graph data.

PERF CONTRACT (option B): steady-state cost tracks CHURN, never repo size. The warm path
(nothing changed) is a dirty-set read + one HEAD stat + one small state read — no git
subprocess, no walk. Reconcile is bounded by changed files; the dirty-set past a threshold
collapses to ONE batched git diff; the full mtime walk is cold-start only, once per session.

Freshness CAP hit ⇒ an honest costed note on the answer, NEVER a block.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

# --- layout (transient run-state under temp_local — ungated, GR.S1-cache precedent) -------
DIRTY_DIRNAME = "knowledge_freshness"
FRESHNESS_STATE_PREFIX = "graph_freshness__"          # StateStore, session-scoped
FRESHNESS_INDEX_PREFIX = "graph_freshness_index__"    # StateStore, session-scoped (cold baseline)

# --- caps (option-B perf + honest costed notes) -------------------------------------------
DIRTY_BATCH_THRESHOLD = 50        # dirty-set past this ⇒ ONE batched git diff, not per-file
FRESHNESS_CHANGE_CAP = 2000       # changed set past this ⇒ costed note attached, never a block
INDEX_SIZE_CAP_FILES = 20000      # cold walk past this ⇒ honest costed note, walk bounded


# ==========================================================================================
# session id (default when a caller doesn't pass one)
# ==========================================================================================
def _sid(session_id: Optional[str]) -> str:
    if session_id:
        return session_id
    try:
        from ..session import current_session_id
        return current_session_id()
    except Exception:  # noqa: BLE001 — freshness must never break on identity resolution
        return "default"


# ==========================================================================================
# 1 — the dirty-set (atomic O(1) append, session-scoped, ungated, never raises)
# ==========================================================================================
def _dirty_dir(root: str) -> str:
    from .. import MOKATA_DIR, TEMP_LOCAL_DIRNAME
    return os.path.join(root, MOKATA_DIR, TEMP_LOCAL_DIRNAME, DIRTY_DIRNAME)


def _dirty_path(root: str, session_id: Optional[str]) -> str:
    safe = _sid(session_id).replace("/", "_")
    return os.path.join(_dirty_dir(root), f"dirty__{safe}.log")


def mark_dirty(root: str, paths: List[str], *, session_id: Optional[str] = None) -> None:
    """Append touched paths to the session dirty-set. Atomic O(1) append (single append-mode
    write, no read-modify-write), ungated, and NEVER raises — this is the async observability
    lane, and the async lane never blocks or fails an action."""
    try:
        d = _dirty_dir(root)
        os.makedirs(d, exist_ok=True)
        payload = "".join(f"{p}\n" for p in paths if p)
        if not payload:
            return
        # O_APPEND makes each write atomic on POSIX; a small line write never interleaves.
        with open(_dirty_path(root, session_id), "a", encoding="utf-8") as fh:
            fh.write(payload)
    except Exception:  # noqa: BLE001 — observability must never break flow
        pass


def read_dirty(root: str, *, session_id: Optional[str] = None) -> List[str]:
    """Peek the dirty-set WITHOUT draining. Never raises."""
    try:
        with open(_dirty_path(root, session_id), encoding="utf-8") as fh:
            return [ln.strip() for ln in fh if ln.strip()]
    except OSError:
        return []


def drain_dirty(root: str, *, session_id: Optional[str] = None) -> List[str]:
    """Read AND clear the dirty-set atomically (rename-then-read, so appends racing the drain
    land in a fresh file and are never lost). Never raises."""
    src = _dirty_path(root, session_id)
    tmp = f"{src}.drain.{os.getpid()}"
    try:
        os.replace(src, tmp)           # atomic; new appends recreate `src`
    except OSError:
        return []
    lines: List[str] = []
    try:
        with open(tmp, encoding="utf-8") as fh:
            lines = [ln.strip() for ln in fh if ln.strip()]
    except OSError:
        lines = []
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return lines


# ==========================================================================================
# 2 — git probes (zero-subprocess SHA read; ONE batched diff on demand)
# ==========================================================================================
def _git_dir(root: str) -> Optional[str]:
    """The git dir for `root` (a `.git` directory, or the gitdir a `.git` FILE points at for a
    linked worktree). Walks up from root. Zero-subprocess. None when not in a repo."""
    cur = os.path.abspath(root)
    while True:
        cand = os.path.join(cur, ".git")
        if os.path.isdir(cand):
            return cand
        if os.path.isfile(cand):
            try:
                with open(cand, encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if line.startswith("gitdir:"):
                            gd = line[len("gitdir:"):].strip()
                            if not os.path.isabs(gd):
                                gd = os.path.normpath(os.path.join(cur, gd))
                            return gd
            except OSError:
                return None
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def git_head_sha(root: str) -> Optional[str]:
    """The current commit SHA — read straight from `.git/HEAD` (+ the loose/packed ref it names).
    Zero-subprocess, cheap (a couple of small file reads). None on a non-repo / unborn branch /
    any error (degrade-clean)."""
    try:
        gitdir = _git_dir(root)
        if gitdir is None:
            return None
        with open(os.path.join(gitdir, "HEAD"), encoding="utf-8") as fh:
            head = fh.read().strip()
        if not head.startswith("ref:"):
            return head or None                 # detached HEAD: a raw SHA
        ref = head[len("ref:"):].strip()
        loose = os.path.join(gitdir, ref)
        try:
            with open(loose, encoding="utf-8") as fh:
                return fh.read().strip() or None
        except OSError:
            pass
        packed = os.path.join(gitdir, "packed-refs")
        try:
            with open(packed, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or line.startswith("^"):
                        continue
                    parts = line.split()
                    if len(parts) == 2 and parts[1] == ref:
                        return parts[0]
        except OSError:
            pass
        return None                              # unborn branch (no commit yet)
    except OSError:
        return None


def _default_git_run(root: str, args: List[str]):
    p = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=10)
    return p.returncode, p.stdout


def git_changed_since(root: str, sha: str,
                      *, run: Optional[Callable[[str, List[str]], Any]] = None) -> List[str]:
    """ONE batched `git diff --name-only <sha>` — the paths that differ from `sha` (committed AND
    working-tree). Output is ∝ changed files (option-B). Degrade-clean: [] on any error."""
    if not sha:
        return []
    runner = run or _default_git_run
    try:
        rc, out = runner(root, ["diff", "--name-only", sha])
    except Exception:  # noqa: BLE001
        return []
    if rc != 0:
        return []
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


# ==========================================================================================
# persisted freshness state (transient, session-scoped, ungated)
# ==========================================================================================
@dataclass
class FreshnessState:
    head_sha: Optional[str] = None
    cold_done: bool = False

    def to_dict(self):
        return {"head_sha": self.head_sha, "cold_done": self.cold_done}

    @classmethod
    def from_dict(cls, d):
        d = d or {}
        return cls(head_sha=d.get("head_sha"), cold_done=bool(d.get("cold_done", False)))


# ==========================================================================================
# the freshness outcome (gate verdict — doc 85 §3 `*Outcome`)
# ==========================================================================================
@dataclass
class FreshnessOutcome:
    fresh: bool = True                 # nothing changed since the last index
    rebuilt: bool = False              # a rebuild ran this reconcile
    changed: List[str] = field(default_factory=list)
    answer_from_floor: bool = False    # graph rebuild FAILED ⇒ answer from the AST floor, loudly
    capped: bool = False               # a freshness cap was hit (costed note, never a block)
    note: str = ""                     # honest classed note to ride on the answer


def _join_note(existing: str, add: str) -> str:
    if not add:
        return existing
    return f"{existing} | {add}" if existing else add


# ==========================================================================================
# AST floor invalidation + graph refresh seams
# ==========================================================================================
def _ast_backends(layer: Any):
    """Every AST floor reachable from a layer (primary and/or fallback)."""
    from .ast_backend import AstBackend
    out = []
    for b in (getattr(layer, "primary", None), getattr(layer, "fallback", None)):
        if isinstance(b, AstBackend):
            out.append(b)
    return out


def _invalidate_ast(layer: Any) -> None:
    """Drop the in-memory AST edge index so the NEXT query re-parses. The on-disk cache is
    mtime/size keyed, so the re-parse touches only CHANGED files (∝ churn)."""
    for b in _ast_backends(layer):
        try:
            b.invalidate()
        except Exception:  # noqa: BLE001
            pass


def _refresh_graph(primary: Any) -> bool:
    """Proactively refresh the adopted graph's index (GR.S2(k)). True on success, False when the
    rebuild failed (⇒ the caller answers from the AST floor, never from stale graph data)."""
    fn = getattr(primary, "refresh_index", None)
    if callable(fn):
        try:
            return bool(fn())
        except Exception:  # noqa: BLE001
            return False
    rec = getattr(primary, "recover", None)      # older seam: health→refresh→reprobe
    if callable(rec):
        try:
            return bool(rec())
        except Exception:  # noqa: BLE001
            return False
    return True                                  # no refreshable index ⇒ nothing to fail


# ==========================================================================================
# the controller — the ONE read-time reconcile every query front-runs
# ==========================================================================================
class FreshnessController:
    """Reconciles a layer against the dirty-set + git HEAD + a cold baseline BEFORE it answers.

    Constructed per query in production (a fresh layer is built per MCP/CLI call), so all
    cross-instance state (the last-indexed SHA, the cold-done flag) is PERSISTED to transient
    run-state. Within a single long-lived layer, the seeded cold-start index also serves the
    post-answer out-of-band recheck."""

    def __init__(self, root: str, *, state: Any = None, session_id: Optional[str] = None,
                 git_run: Optional[Callable] = None) -> None:
        self.root = root
        self.session_id = _sid(session_id)
        self._state_store = state           # a StateStore, or None (lazily resolved)
        self._git_run = git_run
        self._index = None                  # in-memory KnowledgeIndex seeded at cold start

    # --- construction --------------------------------------------------------
    @classmethod
    def for_root(cls, root: str, *, session_id: Optional[str] = None) -> Optional["FreshnessController"]:
        """Build a controller for a root using the config-free state accessor. None if the
        run-state surface can't be reached (freshness then simply doesn't engage)."""
        try:
            from ..state import StateStore
            from ..tdd_state import state_dir
            return cls(root, state=StateStore(state_dir(root)), session_id=session_id)
        except Exception:  # noqa: BLE001
            return None

    @classmethod
    def for_surface(cls, surface: Any) -> Optional["FreshnessController"]:
        try:
            return cls(surface.root, state=surface.state)
        except Exception:  # noqa: BLE001
            return None

    # --- persisted state -----------------------------------------------------
    def _store(self):
        if self._state_store is not None:
            return self._state_store
        try:
            from ..state import StateStore
            from ..tdd_state import state_dir
            self._state_store = StateStore(state_dir(self.root))
        except Exception:  # noqa: BLE001
            self._state_store = None
        return self._state_store

    def _state_key(self) -> str:
        return FRESHNESS_STATE_PREFIX + self.session_id

    def _load_state(self) -> FreshnessState:
        store = self._store()
        if store is None:
            return FreshnessState()
        try:
            return FreshnessState.from_dict(store.read(self._state_key()))
        except Exception:  # noqa: BLE001
            return FreshnessState()

    def _save_state(self, st: FreshnessState) -> None:
        store = self._store()
        if store is None:
            return
        try:
            store.write(self._state_key(), st.to_dict())
        except Exception:  # noqa: BLE001
            pass

    # --- cold-start baseline walk (ONCE per session) -------------------------
    def _cold_walk(self):
        """Seed the mtime/hash baseline. Returns (changed_paths, capped). Full walk, cold-start
        only; bounded by INDEX_SIZE_CAP_FILES with an honest costed note past the cap."""
        from .index import KnowledgeIndex
        idx = KnowledgeIndex()
        capped = False
        try:
            # count-bounded build (option-B: don't let a giant repo blow the cold walk unbounded)
            paths = []
            for ab in KnowledgeIndex._iter_files(self.root, (".py",)):
                paths.append(ab)
                if len(paths) >= INDEX_SIZE_CAP_FILES:
                    capped = True
                    break
            for ab in paths:
                from .index import file_fingerprint, IndexEntry
                rel = os.path.relpath(ab, self.root)
                try:
                    h, m, s = file_fingerprint(ab)
                except OSError:
                    continue
                idx.entries[rel] = IndexEntry(rel, h, m, s)
        except Exception:  # noqa: BLE001
            return [], capped
        changed = []
        prior = self._load_index()
        if prior is not None:
            # a persisted baseline from earlier THIS session ⇒ out-of-band changes since then
            changed = prior.stale_files(self.root, list(idx.entries))
        self._index = idx
        self._save_index(idx)
        return changed, capped

    def _index_key(self) -> str:
        return FRESHNESS_INDEX_PREFIX + self.session_id

    def _load_index(self):
        store = self._store()
        if store is None:
            return None
        try:
            from .index import KnowledgeIndex
            data = store.read(self._index_key())
            return KnowledgeIndex.from_dict(data) if data else None
        except Exception:  # noqa: BLE001
            return None

    def _save_index(self, idx) -> None:
        store = self._store()
        if store is None:
            return
        try:
            store.write(self._index_key(), idx.to_dict())
        except Exception:  # noqa: BLE001
            pass

    # --- the reconcile -------------------------------------------------------
    def ensure_fresh(self, layer: Any) -> FreshnessOutcome:
        """The read-time reconcile every query front-runs. Rebuilds a KNOWN-stale index BEFORE
        the answer; NEVER raises (freshness must not break a query)."""
        try:
            return self._reconcile(layer)
        except Exception as exc:  # noqa: BLE001
            # D5 — LOUD, not silent: a broken freshness reconcile means the answer may not reflect
            # the latest edits, and that must never be a secret. The query STILL proceeds (freshness
            # is a read-time enhancement, never a reason a query can't run) — it just says so once.
            from ..degrade import FAILURE_UNREACHABLE, note_degraded
            note_degraded(
                "graph-freshness", FAILURE_UNREACHABLE,
                fallback="the answer may not reflect the latest edits",
                fix="run `mokata doctor`; the graph re-reconciles on the next query",
                detail=f"freshness reconcile failed: {exc}")
            return FreshnessOutcome(fresh=True)

    def _reconcile(self, layer: Any) -> FreshnessOutcome:
        changed = set()
        capped = False
        notes: List[str] = []

        st = self._load_state()
        # 1 — dirty-set (in-harness edits). Fast O(1) drain.
        dirty = drain_dirty(self.root, session_id=self.session_id)
        head = git_head_sha(self.root)

        if len(dirty) > DIRTY_BATCH_THRESHOLD and st.head_sha:
            # Option-B: collapse a big dirty-set into ONE batched git diff vs the last-indexed
            # SHA instead of per-file work.
            changed |= set(git_changed_since(self.root, st.head_sha, run=self._git_run))
            capped = True
            notes.append(f"freshness: {len(dirty)} files touched — batched git-diff reconcile")
        else:
            changed |= set(dirty)

        # 2 — HEAD moved (branch switch / commit / pull): ONE batched diff vs the last SHA.
        if head and st.head_sha and head != st.head_sha:
            changed |= set(git_changed_since(self.root, st.head_sha, run=self._git_run))

        # 3 — cold start: ONE mtime/hash walk per session (baseline).
        if not st.cold_done:
            walked, walk_capped = self._cold_walk()
            changed |= set(walked)
            if walk_capped:
                capped = True
                notes.append("freshness: index size cap — cold walk bounded")

        # freshness change cap — costed note, NEVER a block.
        if len(changed) > FRESHNESS_CHANGE_CAP:
            capped = True
            notes.append(
                f"freshness cap: {len(changed)} files changed since last index "
                "(reconcile bounded — costed, not blocked)")

        out = FreshnessOutcome(fresh=not changed, changed=sorted(changed), capped=capped)
        if changed:
            self._rebuild(layer, out, notes)

        # persist the advanced baseline (last-indexed SHA + cold-done)
        self._save_state(FreshnessState(head_sha=head, cold_done=True))
        out.note = " | ".join(n for n in notes if n)
        return out

    def _rebuild(self, layer: Any, out: FreshnessOutcome, notes: List[str]) -> None:
        """Debounce COALESCES all pending signals into ONE rebuild (it never permits a stale
        answer). A graph rebuild failure routes the answer to the AST floor on CURRENT files."""
        out.rebuilt = True
        py = [p for p in out.changed if p.endswith(".py")]
        nonpy = [p for p in out.changed if not p.endswith(".py")]

        _invalidate_ast(layer)          # AST floor re-parses only changed .py on the next query
        primary = getattr(layer, "primary", None)
        if getattr(primary, "is_graph", False):
            # CRG is polyglot — the SAME signals (incl. non-.py) drive its incremental re-index.
            if not _refresh_graph(primary):
                out.answer_from_floor = True
                notes.append(
                    "code graph rebuild FAILED — answered from the AST floor on current files "
                    "(never from stale graph data); run `mokata doctor`")
        elif nonpy and not py:
            # AST-floor-only repo: the AST floor indexes .py only (honest tier note).
            notes.append(
                f"{len(nonpy)} non-Python file(s) changed; the AST floor indexes .py only — "
                "structural answers cover Python")

    # --- out-of-band recheck (post-answer; ∝ result size, not repo size) ------
    def recheck_after_answer(self, layer: Any, result: Any) -> bool:
        """Catch an out-of-band editor edit (no hook, HEAD unchanged) that touched a file the
        answer references: hash-staleness on the REFERENCED files (bounded by the result, never
        the repo). Returns True when a rebuild ran and the caller should re-query on fresh state."""
        if self._index is None or result is None:
            return False
        try:
            paths = [r.path for r in getattr(result, "references", [])]
            stale = self._index.stale_files(self.root, paths)
        except Exception:  # noqa: BLE001
            return False
        if not stale:
            return False
        _invalidate_ast(layer)
        primary = getattr(layer, "primary", None)
        if getattr(primary, "is_graph", False):
            _refresh_graph(primary)
        try:
            self._index.reindex(self.root, only=stale)
            self._save_index(self._index)
        except Exception:  # noqa: BLE001
            pass
        return True


# ==========================================================================================
# 9 — H-6 fingerprint tripwire: NAMED HOOK, dormant until 0.0.16
# ==========================================================================================
def fingerprint_forces_refresh(recorded: Optional[str], current: Optional[str]) -> bool:
    """H-6 NAMED HOOK (dormant until 0.0.16). A fingerprint MISMATCH is the SAME forced-refresh
    tripwire as a dirty-set/HEAD change — a KNOWN-stale graph must rebuild before it answers.

    It stays dormant now: 0.0.16 (H-6) supplies the recorded vs current environment/index
    fingerprints and wires this into `_reconcile` (one line: `if fingerprint_forces_refresh(...)
    : changed |= {...}`). With nothing supplied it is a no-op (returns False), exactly the AP-SD
    / GR.S2(m) dormant-hook precedent. The SHAPE is asserted now so the wake is a flip, not a
    redesign."""
    if not recorded or not current:
        return False
    return recorded != current
