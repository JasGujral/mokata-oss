"""C1/C2/C6/C8/C9 — the memory store: mokata's own memory logic over a pluggable backend.

Responsibilities:
  - C6 human-gated writes: nothing reaches the backend without explicit approval.
  - C9 per-type toggles: memory is on by default; a disabled type is refused on write
    and never surfaced on read (reuses the Stage 2 settings-toggle mechanism).
  - C5 resolution: apply a surfaced healing proposal — approve/edit/reject, default none.
  - C8 instrumentation: count reads vs writes (persisted via the state surface).
The backend is chosen THROUGH the capability router (`memory_store`) — no second
detection path; SQLite is the guaranteed floor.
"""

from __future__ import annotations

from ..prompt import read_yes_no

import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from .. import TEMP_LOCAL_DIRNAME
from ..manifest import ManifestError
from .backends import (
    MemoryBackend,
    NativeMemoryBackend,
    ObsidianBackend,
    SQLiteBackend,
    build_postgres_backend,
)
from .consolidation import (
    MERGE,
    PRUNE,
    SUMMARIZE,
    ConsolidationProposal,
    propose_consolidations,
    render_consolidation,
)
from .healing import CONTRADICTION, HealingProposal, detect_issues, render_proposal
from .item import ACTIVE, DECISION, DEFAULT_TOP_K, MEMORY_TYPES, STALE as STATUS_STALE
from .item import SUPERSEDED, MemoryItem

MEMORY_SETTINGS_KEY = "memory"     # manifest.settings["memory"] = {type: bool}
MEMORY_STATS_KEY = "memory_stats"  # StateStore key
MEMORY_DIRNAME = "memory"

# Stage 71a — sentinel: "scope to the CURRENT project" (the default). Distinct from ALL_PROJECTS
# (None → span all) and from a concrete project-id string, so from_surface can tell them apart.
_PROJECT_CURRENT = object()


class MemoryError(Exception):
    pass


class MemoryDisabledError(MemoryError):
    """Raised on an attempt to write a memory type that is toggled off (C9)."""


def enabled_memory_types(manifest: Any) -> Tuple[str, ...]:
    """The memory types that are live (C9). Default-on: a type with no explicit toggle
    is enabled. The whole 'memory' layer being off disables every type."""
    if not manifest.layer_enabled("memory"):
        return ()
    settings = manifest.setting(MEMORY_SETTINGS_KEY, {}) or {}
    return tuple(t for t in MEMORY_TYPES if settings.get(t, True))


# -------------------------------------------------------------- backend selection
def build_backend(tool: str, root: str,
                  clients: Optional[Dict[str, Any]] = None,
                  config: Optional[Dict[str, Any]] = None,
                  project: Optional[str] = None) -> MemoryBackend:
    """Build the backend the router resolved to, honoring the tool's per-tool `config`
    (Stage 24A) and degrading to the SQLite floor when a chosen backend needs external
    wiring that isn't present. `config` is the manifest's `tools.<id>.config` block;
    defaults are unchanged when it's absent. `project` (Stage 71a) SCOPES the shared
    Postgres backend to the current project; None spans all (review). Local SQLite/Obsidian
    are already per-repo and ignore it."""
    clients = clients or {}
    config = config or {}
    # Default runtime stores are transient: under .mokata/temp_local/memory/ (Stage 24D).
    # A user-set config.path/config.vault overrides this and may point anywhere.
    mem_dir = os.path.join(root, TEMP_LOCAL_DIRNAME, MEMORY_DIRNAME)
    floor = lambda: SQLiteBackend(os.path.join(mem_dir, "memory.db"))  # noqa: E731

    if tool == "obsidian":
        vault = config.get("vault")
        vault = os.path.expanduser(vault) if vault else os.path.join(mem_dir, "vault")
        return ObsidianBackend(vault)
    if tool == "sqlite":
        path = config.get("path")
        path = os.path.expanduser(path) if path else os.path.join(mem_dir, "memory.db")
        return SQLiteBackend(path)
    if tool == "postgres":
        # opt-in remote store; degrade to the SQLite floor if unset/unreachable (P8).
        # Stage 71a — scoped to the current project so one shared DSN hosts many, no bleed.
        backend = build_postgres_backend(config, project=project)
        return backend if backend is not None else floor()
    if tool == "native-memory":
        client = clients.get("native-memory")
        if client is not None:
            return NativeMemoryBackend(client)
        # no client wired -> degrade to the guaranteed floor (not a second detection)
        return floor()
    # "ripgrep"/unknown, or unavailable -> SQLite floor
    return floor()


def select_memory_backend(router: Any, root: str,
                          clients: Optional[Dict[str, Any]] = None,
                          project: Optional[str] = None) -> MemoryBackend:
    try:
        res = router.resolve("memory_store")
    except (ManifestError, AttributeError):
        res = None
    tool = res.tool if (res is not None and res.available and res.tool) else "sqlite"
    config: Dict[str, Any] = {}
    try:
        config = router.manifest.tool_config(tool)
    except AttributeError:
        config = {}
    return build_backend(tool, root, clients, config, project=project)


# -------------------------------------------------------------- instrumentation (C8)
@dataclass
class MemoryStats:
    reads: int = 0
    writes: int = 0

    @property
    def ratio(self) -> float:
        if self.writes == 0:
            return float(self.reads)
        return self.reads / self.writes

    def to_dict(self) -> Dict[str, int]:
        return {"reads": self.reads, "writes": self.writes}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MemoryStats":
        return cls(reads=int(d.get("reads", 0)), writes=int(d.get("writes", 0)))

    def log_line(self) -> str:
        return (f"memory read/write ratio: {self.ratio:.2f} "
                f"({self.reads} reads / {self.writes} writes)")


@dataclass
class WriteResult:
    item: Optional[MemoryItem]
    committed: bool
    aborted: bool
    message: str
    blocked: bool = False        # True when a secret was detected and the write was hard-blocked


@dataclass
class HealingResult:
    changed: bool
    aborted: bool = False
    message: str = ""
    item: Optional[MemoryItem] = None
    blocked: bool = False        # True when a secret was detected and the change was hard-blocked


def _default_confirm(text: str) -> bool:
    return read_yes_no(text, "Approve?")


class MemoryStore:
    def __init__(self, backend: MemoryBackend,
                 enabled_types: Tuple[str, ...] = MEMORY_TYPES,
                 stats_store: Any = None,
                 stats_key: str = MEMORY_STATS_KEY,
                 ledger: Any = None,
                 embedder: Any = None,
                 knowledge_layer: Any = None) -> None:
        self.backend = backend
        self.enabled_types = tuple(enabled_types)
        self.embedder = embedder        # Stage 35e — None => semantic tier off (local-first)
        self.knowledge_layer = knowledge_layer   # Stage 35f — live graph-proximity tier
        self._stats_store = stats_store
        self._stats_key = stats_key
        self._ledger = ledger
        self.stats = MemoryStats()
        if stats_store is not None:
            existing = stats_store.read(stats_key)
            if existing:
                self.stats = MemoryStats.from_dict(existing)

    # --- construction (backend always via the router) -----------------------
    @classmethod
    def from_router(cls, router: Any, root: str,
                    enabled_types: Optional[Tuple[str, ...]] = None,
                    stats_store: Any = None,
                    clients: Optional[Dict[str, Any]] = None,
                    project: Optional[str] = None) -> "MemoryStore":
        backend = select_memory_backend(router, root, clients, project=project)
        types = enabled_types if enabled_types is not None else MEMORY_TYPES
        return cls(backend, enabled_types=types, stats_store=stats_store)

    @classmethod
    def from_surface(cls, surface: Any,
                     clients: Optional[Dict[str, Any]] = None,
                     project: Any = _PROJECT_CURRENT) -> "MemoryStore":
        # Stage 71a — SCOPE the shared backend to the current project by default. `project` can be
        # overridden for review: a specific id (str), or `ALL_PROJECTS` (None) to span all.
        from ..project import project_id
        scope = project_id(surface) if project is _PROJECT_CURRENT else project
        backend = select_memory_backend(surface.router, surface.mokata_dir, clients,
                                        project=scope)
        # attach the audit ledger so consolidation proposals/decisions are recorded (I3)
        from ..govern import AuditLedger
        from .embed import make_embedder
        # Stage 35e: semantic tier is OPT-IN — `settings.memory.embedder` (e.g. "hashing");
        # absent => None => semantic off (lexical floor only). Local-first, no network.
        embedder = make_embedder((surface.manifest.setting("memory", {}) or {})
                                 .get("embedder"))
        # Stage 35f: auto-wire the live graph-proximity tier from the in-repo knowledge layer
        # (degrade-clean — on the grep floor it contributes nothing). Defensive: a layer
        # build problem must never break memory.
        try:
            from ..knowledge import KnowledgeLayer
            knowledge_layer = KnowledgeLayer.from_surface(surface)
        except Exception:
            knowledge_layer = None
        return cls(backend, enabled_types=enabled_memory_types(surface.manifest),
                   stats_store=surface.state,
                   ledger=AuditLedger.from_mokata_dir(surface.mokata_dir),
                   embedder=embedder, knowledge_layer=knowledge_layer)

    # --- toggles ------------------------------------------------------------
    def type_enabled(self, mtype: str) -> bool:
        return mtype in self.enabled_types

    # --- instrumentation ----------------------------------------------------
    def _persist_stats(self) -> None:
        if self._stats_store is not None:
            self._stats_store.write(self._stats_key, self.stats.to_dict())

    def _bump_read(self, n: int = 1) -> None:
        self.stats.reads += n
        self._persist_stats()

    def _bump_write(self, n: int = 1) -> None:
        self.stats.writes += n
        self._persist_stats()

    # --- writes (human-gated, C6) -------------------------------------------
    def _gated_commit(self, subject: str, content: str, commit: Callable[[], None],
                      prompt: str, confirm: Optional[Callable[[str], bool]] = None,
                      assume_yes: bool = False):
        """M2 (Stage 39): the SINGLE write path for memory — the universal WriteGate does the
        secret-scan (hard block), the human gate (showing the rich `prompt` surface), the audit-
        ledger record, then the commit. Returns the gate's WriteOutcome."""
        from ..govern import WriteGate, WriteRequest
        gate = WriteGate(ledger=self._ledger)
        return gate.submit(
            WriteRequest("memory", f"memory:{subject}", content=content, actor="memory"),
            commit=commit, confirm=confirm, assume_yes=assume_yes, prompt=prompt)

    def render_write(self, item: MemoryItem) -> str:
        return (f"mokata · propose to remember [{item.mtype}] {item.subject} = "
                f"{item.value!r}\nNothing is stored unless you approve.")

    def remember(self, item: MemoryItem,
                 confirm: Optional[Callable[[str], bool]] = None,
                 assume_yes: bool = False) -> WriteResult:
        if not self.type_enabled(item.mtype):
            raise MemoryDisabledError(
                f"memory type '{item.mtype}' is disabled; enable it to remember this"
            )

        def _commit() -> None:
            # Stage 35e (frugal): compute the embedding once, on the gated write, so semantic
            # recall later embeds only the query. No-op when no embedder is configured.
            if self.embedder is not None and "_embedding" not in item.provenance:
                item.provenance["_embedding"] = list(
                    self.embedder(f"{item.subject} {item.value}"))
            self.backend.put(item)
            self._bump_write()

        # M2 (Stage 39): every memory write routes through the ONE universal WriteGate —
        # secret-scan (hard block) + human gate + audit ledger — with the rich render_write
        # surface preserved. No second gate path.
        outcome = self._gated_commit(item.subject, f"{item.subject}\n{item.value}",
                                     _commit, self.render_write(item),
                                     confirm=confirm, assume_yes=assume_yes)
        if outcome.committed:
            return WriteResult(item, committed=True, aborted=False, message="ok")
        if outcome.findings:
            return WriteResult(None, committed=False, aborted=True, blocked=True,
                               message="blocked: secret detected — not stored")
        return WriteResult(None, committed=False, aborted=True,
                           message="declined at the human gate")

    def remember_decision(self, subject: str, value: str, **kw) -> WriteResult:
        """C2 — decision memory is wired through the same gated store."""
        confirm = kw.pop("confirm", None)
        assume_yes = kw.pop("assume_yes", False)
        item = MemoryItem.create(subject, value, mtype=DECISION, **kw)
        return self.remember(item, confirm=confirm, assume_yes=assume_yes)

    # --- reads (honor toggles, count) ---------------------------------------
    def peek_active(self, mtype: Optional[str] = None) -> List[MemoryItem]:
        """Active items WITHOUT counting a read — for read-only surfaces (the governance
        dashboard, always-on rule injection) that must mutate NO durable state. Same data as
        `all_active`; only the instrumentation differs."""
        items = self.backend.all(mtype=mtype, statuses=(ACTIVE,))
        return [i for i in items if i.mtype in self.enabled_types]

    def all_active(self, mtype: Optional[str] = None) -> List[MemoryItem]:
        self._bump_read()
        return self.peek_active(mtype=mtype)

    def recall(self, subject: str, mtype: Optional[str] = None) -> List[MemoryItem]:
        return [i for i in self.all_active(mtype=mtype) if i.subject == subject]

    def recall_relevant(self, query: str, top_k: int = DEFAULT_TOP_K, semantic: bool = True,
                        graph_scorer: Any = None) -> List[Any]:
        """Stage 35e — tiered, by-relevance retrieval (lexical floor + optional graph +
        semantic when an embedder is wired), fused + ranked, top-k only (frugal). Returns
        a list of RetrievalHit. Degrades to lexical when the semantic/graph tiers are off.

        Stage 35f: the graph tier is now LIVE BY DEFAULT — when no explicit graph_scorer is
        passed and a knowledge layer is wired, build one from the code graph. It silently
        contributes nothing on the grep floor (no real graph ⇒ scorer is None), so
        lexical+semantic always hold."""
        from .tiered import tiered_recall
        if graph_scorer is None and self.knowledge_layer is not None:
            try:
                from ..knowledge import make_graph_scorer
                graph_scorer = make_graph_scorer(self.knowledge_layer, query)
            except Exception:
                graph_scorer = None
        return tiered_recall(self, query, embedder=self.embedder,
                             graph_scorer=graph_scorer, top_k=top_k, semantic=semantic)

    def get(self, item_id: str) -> Optional[MemoryItem]:
        return self.backend.get(item_id)

    # --- self-healing (C5, surfacing + gated resolution) --------------------
    def detect_issues(self, now: Optional[str] = None) -> List[HealingProposal]:
        active = [i for i in self.backend.all(statuses=(ACTIVE,))
                  if i.mtype in self.enabled_types]
        return detect_issues(active, now=now)

    def render_proposal(self, p: HealingProposal) -> str:
        return render_proposal(p)

    def _record_healing(self, p: HealingProposal, decision: str, changed: bool) -> None:
        """Audit the self-healing resolution with the WHY (Stage 49): the old→new diff and
        the proposal's rationale, plus the decision + whether anything changed."""
        if self._ledger is not None:
            self._ledger.record("healing_decision", op=p.kind, subject=p.subject,
                                 decision=decision, changed=changed,
                                 diff=p.diff(), reason=p.rationale)

    def apply_proposal(self, p: HealingProposal, decision: str,
                       edited: Optional[MemoryItem] = None,
                       confirm: Optional[Callable[[str], bool]] = None,
                       assume_yes: bool = False) -> HealingResult:
        """Resolve a surfaced proposal. Default (reject/defer) changes nothing; approve
        and edit are human-gated. NEVER auto-rewrites."""
        if decision not in ("approve", "edit", "reject", "defer"):
            raise MemoryError(f"unknown decision '{decision}'")
        if decision in ("reject", "defer"):
            self._record_healing(p, decision, changed=False)
            return HealingResult(changed=False, message=f"{decision}: no change")

        # Build the commit closure + the (untrusted) content to secret-scan + the result.
        if decision == "edit":
            if edited is None:
                raise MemoryError("edit requires an edited item")
            content = f"{edited.subject}\n{edited.value}"     # the new value is scanned
            result_item, result_msg = edited, "edited"

            def _commit() -> None:
                # the edit replaces the whole issue: supersede every item it resolves
                # (both sides of a contradiction; the single stale item otherwise).
                replaced = [p.old] + ([p.new] if p.new is not None else [])
                for item in replaced:
                    item.status = SUPERSEDED
                    if item.id not in edited.supersedes:
                        edited.supersedes.append(item.id)
                    self.backend.update(item)
                self.backend.put(edited)
                self._bump_write(len(replaced) + 1)
        elif p.kind == CONTRADICTION and p.new is not None:
            content = f"{p.new.subject}\n{p.new.value}"       # the winning new value is scanned
            result_item, result_msg = p.new, "approved"

            def _commit() -> None:
                p.old.status = SUPERSEDED
                if p.old.id not in p.new.supersedes:
                    p.new.supersedes.append(p.old.id)
                self.backend.update(p.old)
                self.backend.update(p.new)
                self._bump_write(2)
        else:   # STALE approve — marks an existing item stale; no new untrusted value
            content = p.old.subject
            result_item, result_msg = None, "approved"

            def _commit() -> None:
                p.old.status = STATUS_STALE
                self.backend.update(p.old)
                self._bump_write()

        # M2 (Stage 39): the SAME WriteGate path as remember — scan + gate (old→new surface) +
        # ledger — so healing never bypasses the universal gate either.
        outcome = self._gated_commit(p.subject, content, _commit, self.render_proposal(p),
                                     confirm=confirm, assume_yes=assume_yes)
        self._record_healing(p, decision, changed=outcome.committed)
        if outcome.committed:
            return HealingResult(changed=True, item=result_item, message=result_msg)
        if outcome.findings:
            return HealingResult(changed=False, aborted=True, blocked=True,
                                 message="blocked: secret detected — not applied")
        return HealingResult(changed=False, aborted=True,
                             message="declined at the human gate")

    # --- consolidation (C7, PROPOSAL-ONLY + gated apply) --------------------
    def propose_consolidations(self, ledger: Any = None
                               ) -> List[ConsolidationProposal]:
        """Surface consolidation proposals (merge/summarize/prune). Reads only — never
        writes. Each proposal is logged to the ledger."""
        led = ledger if ledger is not None else self._ledger
        active = [i for i in self.backend.all(statuses=(ACTIVE,))
                  if i.mtype in self.enabled_types]
        stale = [i for i in self.backend.all(statuses=(STATUS_STALE,))
                 if i.mtype in self.enabled_types]
        proposals = propose_consolidations(active, stale)
        if led is not None:
            for p in proposals:
                led.record("consolidation_proposal", op=p.kind, subject=p.subject,
                           mtype=p.mtype, count=len(p.olds))
        return proposals

    def render_consolidation(self, p: ConsolidationProposal) -> str:
        return render_consolidation(p)

    def apply_consolidation(self, p: ConsolidationProposal, decision: str,
                            edited: Optional[MemoryItem] = None,
                            confirm: Optional[Callable[[str], bool]] = None,
                            assume_yes: bool = False,
                            ledger: Any = None) -> HealingResult:
        """Apply a consolidation proposal. Default (reject/defer) changes nothing;
        approve/edit are human-gated. NEVER auto-applies."""
        led = ledger if ledger is not None else self._ledger
        if decision not in ("approve", "edit", "reject", "defer"):
            raise MemoryError(f"unknown decision '{decision}'")

        def _log(changed: bool, outcome: str) -> None:
            if led is not None:
                led.record("consolidation_decision", op=p.kind, subject=p.subject,
                           decision=outcome, changed=changed, reason=p.rationale)

        if decision in ("reject", "defer"):
            _log(False, decision)
            return HealingResult(changed=False, message=f"{decision}: no change")

        if not assume_yes:
            gate = confirm or _default_confirm
            if not gate(self.render_consolidation(p)):
                _log(False, "declined")
                return HealingResult(changed=False, aborted=True,
                                     message="declined at the human gate")

        if p.kind == MERGE:
            keep = edited or p.new
            if edited is not None:
                self.backend.put(edited)
            for o in p.olds:
                if o.id == keep.id:
                    continue
                o.status = SUPERSEDED
                if o.id not in keep.supersedes:
                    keep.supersedes.append(o.id)
                self.backend.update(o)
            if edited is None:
                self.backend.update(keep)
            self._bump_write(len(p.olds))
        elif p.kind == SUMMARIZE:
            self.backend.put(edited or p.new)
            self._bump_write()
        elif p.kind == PRUNE:
            for o in p.olds:
                self.backend.delete(o.id)
            self._bump_write(max(1, len(p.olds)))

        _log(True, decision)
        return HealingResult(changed=True, message=decision)

    def close(self) -> None:
        self.backend.close()
