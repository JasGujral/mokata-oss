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

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from .backends import MemoryBackend
# PRE-SIMP (0.0.15) — backend build/select/scope/identity resolution moved to `selection.py` (the
# deprecated-backend selection branches land in ONE file for the 0.0.17 SIMP.S3 removal). Re-exported
# here so every existing `from .store import build_backend` / `from ..memory.store import X` caller
# works unchanged — no caller edits needed.
from .selection import (  # noqa: F401 - re-export shim (import-compat)
    MEMORY_DIRNAME,
    _identity_and_access_for,
    _overlay_for_team,
    _scope_context_for,
    _select_raw_backend,
    build_backend,
    select_memory_backend,
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
from .item import SUPERSEDED, MemoryItem, downgrade_refusal
from ..errors import MokataError

MEMORY_SETTINGS_KEY = "memory"     # manifest.settings["memory"] = {type: bool}
MEMORY_STATS_KEY = "memory_stats"  # StateStore key

# TM.S5c — the journal op labels (mirror team_journal.OP_*), passed per team-mode call site so the
# flush's compare-and-set picks the right operation: put = believed-new INSERT, update = revision-
# guarded UPDATE, delete = revision-guarded DELETE (a PRUNE never hard-deletes a shared row).
_OP_PUT = "memory_put"
_OP_UPDATE = "memory_update"
_OP_DELETE = "memory_delete"

# Stage 71a — sentinel: "scope to the CURRENT project" (the default). Distinct from ALL_PROJECTS
# (None → span all) and from a concrete project-id string, so from_surface can tell them apart.
_PROJECT_CURRENT = object()


class MemoryError(MokataError):
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
    # D6 — True when THIS BUILD refused the write: the doc is newer than the doc schema it speaks,
    # so rewriting it would drop fields it cannot read. Distinct from `blocked` (a secret) and from
    # a plain abort (the human declined) — reporting a client refusal as "you declined" would be
    # the quiet lie CM.S2/D5 exist to kill. Nothing was written; the doc is untouched.
    refused: bool = False


@dataclass
class HealingResult:
    changed: bool
    aborted: bool = False
    message: str = ""
    item: Optional[MemoryItem] = None
    blocked: bool = False        # True when a secret was detected and the change was hard-blocked
    refused: bool = False        # D6 — this build may not rewrite the doc (see WriteResult.refused)


@dataclass
class PromoteResult:
    """TM.S7 — the outcome of a gated enforcement PROMOTION/demotion (binding change only)."""
    changed: bool
    aborted: bool = False
    message: str = ""
    item: Optional[MemoryItem] = None
    old: str = ""                # enforcement before
    new: str = ""                # enforcement after
    direction: str = ""          # "promote" | "demote" | "unchanged"


@dataclass
class ScopePromoteResult:
    """TM.S10 — the outcome of a gated SCOPE promotion (broadening an item's audience, doc 52 M-2).
    SEPARATE from the S7 enforcement-level promotion; both are human-gated + ledgered."""
    changed: bool
    aborted: bool = False
    message: str = ""
    item: Optional[MemoryItem] = None
    old: str = ""                # scope level before (narrower)
    new: str = ""                # scope level after (broader)


@dataclass
class ReviewResult:
    """TM.S11 — the outcome of a review-workflow step (propose / transition / rollback, doc 62 §6).
    `state` is the resulting review state; `item` is the proposal touched. Every step is human-gated
    + ledgered; a refused step (illegal transition, self-approval, missing role) sets `aborted`."""
    ok: bool
    aborted: bool = False
    message: str = ""
    item: Optional[MemoryItem] = None
    state: str = ""              # the review state after the step (draft|in-review|approved|…)


def _default_confirm(text: str) -> bool:
    # PRE-SIMP (0.0.15) — the default confirm callable. `read_yes_no` (the terminal prompt, an L4
    # surface concern) is imported LAZILY here so this L2 domain module carries NO module-top import
    # of the prompt/UI layer (LAYER-LINT's seed). Same prompt text, byte-identical behaviour.
    from ..prompt import read_yes_no
    return read_yes_no(text, "Approve?")


class MemoryStore:
    def __init__(self, backend: MemoryBackend,
                 enabled_types: Tuple[str, ...] = MEMORY_TYPES,
                 stats_store: Any = None,
                 stats_key: str = MEMORY_STATS_KEY,
                 ledger: Any = None,
                 embedder: Any = None,
                 knowledge_layer: Any = None,
                 surface: Any = None,
                 scope_context: Any = None,
                 identity: Any = None,
                 access: Any = None,
                 read_routing: Any = None,
                 team_writer: Any = None) -> None:
        self.backend = backend
        # PRE-SIMP (0.0.15) — the injected TEAM-mode write strategy (doc 91 layering seed). None
        # (from_router / direct construction) => resolved lazily to the default `TeamWriter` on first
        # team write, so behaviour is byte-identical whether injected or not. Set (from_surface) =>
        # the collab-layer writer resolved where the surface is built, so `store` no longer reaches
        # UP to team_journal/team_audit/teamdb itself — SIMP.S3 can evolve the writers behind it.
        self._team_writer = team_writer
        # CM.S2 (C-2) — the ONE read-routing decision (degrade.ReadRoutingDecision) for this
        # store. None (local/zero-config/direct construction) => never degraded, byte-identical.
        # Set (from_surface) => carries whether a team-mode read is served from the LOCAL
        # fallback, so every read surface can mark it LOUDLY degraded instead of silently.
        self.read_routing = read_routing
        self.enabled_types = tuple(enabled_types)
        # TM.S10 — the REAL run identity (doc 52 M-1). None (direct/local construction) => the
        # author placeholder is untouched (byte-identical). When set (from_surface), it stamps the
        # real author on every durable write, replacing the "user" placeholder.
        self.identity = identity
        # TM.S10 — the config-derived access policy (doc 52 M-2). None (local/zero-config) => NO
        # enforcement: every read/edit/scope-promotion is allowed, byte-identical to today. Set
        # (team mode) => reads are filtered + non-permitted writes/promotions refused, fail-closed.
        self.access = access
        # TM.S6 — the working scope context for the UNION read (doc 62 §2). None (the default and
        # every local/zero-config store) means NO scope filtering — recall is byte-identical to
        # pre-TM.S6. When set (team mode), reads return the union of items across the scope path.
        self.scope_context = scope_context
        self.embedder = embedder        # Stage 35e — None => semantic tier off (local-first)
        self.knowledge_layer = knowledge_layer   # Stage 35f — live graph-proximity tier
        self._stats_store = stats_store
        self._stats_key = stats_key
        self._ledger = ledger
        # TM.S5b — the surface is present only when built via `from_surface`; it enables the
        # team-mode journal-first write path. None (from_router / direct construction) => the
        # local backend path only, byte-for-byte unchanged (zero-config default).
        self._surface = surface
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
        # CM.S2 (C-2) — compute the ONE read-routing decision FIRST (mode + shared-backend
        # verdict from the same cached E2 probe), then route the backend through it. Local mode
        # returns a non-degraded decision with NO probe (zero-network hot path, byte-identical).
        from ..degrade import resolve_read_routing
        routing = resolve_read_routing(surface)
        backend = select_memory_backend(surface.router, surface.mokata_dir, clients,
                                        project=scope, routing=routing)
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
        # TM.S10 — the REAL run identity (doc 52 M-1 / doc 63 §5): stamp the actual author on every
        # durable write, not the "user" placeholder. Reuses team_audit.actor() (the run identity —
        # full session identity is 0.0.13). The access POLICY only enforces in TEAM mode (doc 62 §6);
        # local/zero-config gets identity stamping but NO enforcement (byte-identical), so a single
        # user keeps full personal access. Fail-safe: any error degrades to no identity / no policy.
        identity, access = _identity_and_access_for(surface)
        # PRE-SIMP (0.0.15) — resolve the TEAM-writer strategy HERE, where the surface is built, so
        # the store never imports the collab layer (team_journal/team_audit/teamdb) at its own edge.
        from .team_writer import TeamWriter
        return cls(backend, enabled_types=enabled_memory_types(surface.manifest),
                   stats_store=surface.state,
                   ledger=AuditLedger.from_mokata_dir(surface.mokata_dir),
                   embedder=embedder, knowledge_layer=knowledge_layer, surface=surface,
                   scope_context=_scope_context_for(surface, scope),
                   identity=identity, access=access, read_routing=routing,
                   team_writer=TeamWriter())

    # --- scope (TM.S6, doc 62 §2) -------------------------------------------
    def scoped_active(self, mtype: Optional[str] = None) -> List[MemoryItem]:
        """The active items VISIBLE in the working scope: the UNION up the scope path, then filtered
        to what the identity may READ (TM.S10). With no scope context AND no access policy
        (local/zero-config) this is exactly `all_active` — byte-identical recall. Counts one read
        (like `all_active`), so instrumentation is unchanged."""
        items = self.all_active(mtype=mtype)
        if self.scope_context is not None:
            from .scope import union_read
            items = union_read(items, self.scope_context)
            # M-A2 — collapse the union to the single precedence WINNER per key (doc 62 §3), so two
            # conflicting scoped items for one subject never BOTH inject (the reader picks no winner
            # today — the double-inject bug). Reuses the existing engine (precedence.resolve). LOCAL
            # mode has no scope context → this whole branch is skipped → recall is byte-identical.
            from .precedence import resolve_items
            items = resolve_items(items)
        # TM.S10 — drop items the identity can't see (a teammate's private items never leak, S-2).
        # Fail-closed: an item whose readability can't be determined is filtered out (deny on doubt).
        if self.access is not None and getattr(self.access, "enforce", False):
            items = [it for it in items if self._can_read_item(it)]
        return items

    # --- degrade surface (CM.S2, C-2) ---------------------------------------
    @property
    def read_degraded(self) -> bool:
        """True when this store's reads are a team-mode fallback served from the LOCAL floor
        (shared team memory unreachable). False for every local/zero-config store."""
        return bool(getattr(self.read_routing, "degraded", False))

    @property
    def degrade_notice(self) -> Any:
        """The `degrade.DegradeNotice` for a degraded team read (env-var NAME + failure class),
        or None. Read surfaces use it for the loud MCP marker + the once-per-subsystem CLI line."""
        return getattr(self.read_routing, "notice", None) if self.read_degraded else None

    @property
    def pending_status(self) -> Any:
        """CM.S4 (C-4) — the surfaced local-only backlog: the count of approved-but-unflushed team
        writes (+ oldest-backlog age + last-failure class), or None when nothing is pending / local
        mode. Reuses `read_routing` (the ONE CM.S2 verdict) so the class agrees with the degrade
        notice — no second probe. Never carries a DSN value or memory content."""
        try:
            from .. import flush_liveness
            return flush_liveness.pending_status(self._surface, routing=self.read_routing)
        except Exception:  # pragma: no cover - surfacing is best-effort
            # D5 — deliberately left BROAD, with no narrow class to name: `flush_liveness
            # .pending_status`'s own contract is never-raise (it is degrade-clean to None), so this
            # handler guards a promise the callee already keeps. There is no honest class to
            # enumerate for an exception the callee says cannot occur.
            return None

    # --- toggles ------------------------------------------------------------
    def type_enabled(self, mtype: str) -> bool:
        return mtype in self.enabled_types

    # --- instrumentation ----------------------------------------------------
    def _persist_stats(self, reads: int = 0, writes: int = 0) -> None:
        """Persist the counters by adding THIS store's DELTA to the current on-disk value under the
        cross-process lock (MS.S6 / M-6), not by overwriting with a stale in-memory total.

        `memory_stats` is explicitly shared repo state (never session-scoped — see
        `session_state.SESSION_SCOPED_KEYS`), so the old blind write meant two Claude Code windows
        each counted only their OWN reads: whichever persisted last erased the other's, and the
        surfaced read/write ratio silently under-reported. A delta merge is correct under any
        interleaving. The in-memory `self.stats` is then re-synced from the merged result, so this
        process's view is the true total, not just its own share.

        Single-process: the merged value equals the in-memory total, so the output is unchanged. A
        store without `update` (an injected fake) keeps the previous blind write."""
        store = self._stats_store
        if store is None:
            return
        if not hasattr(store, "update"):             # pragma: no cover - a fake/minimal store
            store.write(self._stats_key, self.stats.to_dict())
            return
        merged = store.update(
            self._stats_key,
            lambda cur: {"reads": int((cur or {}).get("reads", 0) or 0) + reads,
                         "writes": int((cur or {}).get("writes", 0) or 0) + writes},
            default={"reads": 0, "writes": 0})
        self.stats = MemoryStats.from_dict(merged)

    def _bump_read(self, n: int = 1) -> None:
        self.stats.reads += n            # in-memory total (authoritative with no stats store)
        self._persist_stats(reads=n)     # ... then re-synced from the merged on-disk total

    def _bump_write(self, n: int = 1) -> None:
        self.stats.writes += n
        self._persist_stats(writes=n)

    # --- writes (human-gated, C6) -------------------------------------------
    def _gated_commit(self, subject: str, content: str, commit: Callable[[], None],
                      prompt: str, confirm: Optional[Callable[[str], bool]] = None,
                      assume_yes: bool = False, policy: Any = None, ledger: Any = None):
        """M2 (Stage 39): the SINGLE write path for memory — the universal WriteGate does the
        secret-scan (hard block), the human gate (showing the rich `prompt` surface), the audit-
        ledger record, then the commit. Returns the gate's WriteOutcome.

        `ledger` overrides the store's own (SI.6): `apply_consolidation` is handed the caller's
        ledger, and the gate must record to THAT one — otherwise the approval id its journal entries
        inherit would name a seq in a ledger nobody reads."""
        from ..govern import WriteGate, WriteRequest
        from ..govern.trust import (CLI_SURFACE, policy_approved, policy_surface, policy_tool,
                                    policy_trust)
        led = ledger if ledger is not None else self._ledger
        gate = WriteGate(ledger=led, trust=policy_trust(policy))
        return gate.submit(
            WriteRequest("memory", f"memory:{subject}", content=content, actor="memory",
                         tool=policy_tool(policy, "memory"),
                         surface=policy_surface(policy, CLI_SURFACE)),
            commit=commit, confirm=confirm, assume_yes=assume_yes, prompt=prompt,
            human_approved=policy_approved(policy))

    def render_write(self, item: MemoryItem) -> str:
        return (f"mokata · propose to remember [{item.mtype}] {item.subject} = "
                f"{item.value!r}\nNothing is stored unless you approve.")

    # --- identity + access (TM.S10, doc 52 M-1/M-2) -------------------------
    def _stamp_author(self, item: MemoryItem) -> None:
        """M-1 — stamp the REAL author on a durable write. Only replaces the "user"/empty
        placeholder (an explicit author, and every legacy item already on disk, is untouched);
        a no-op when the store has no identity (local/direct construction — byte-identical)."""
        if self.identity and item.provenance.get("author", "") in ("", "user"):
            item.provenance["author"] = self.identity

    @staticmethod
    def _item_scope_cat(item: MemoryItem) -> "Tuple[str, Optional[str], Optional[str]]":
        """(scope_level, category, owner) for access checks: `category` is the item's `scope_id`
        for a category-scoped item (else None); `owner` is the `scope_id` for a personal item
        (else None). Duck-typed over the item's scope fields."""
        from .scope import CATEGORY as _CAT, DEFAULT_SCOPE_LEVEL, PERSONAL as _PERS
        scope = getattr(item, "scope_level", "") or DEFAULT_SCOPE_LEVEL
        sid = getattr(item, "scope_id", "") or ""
        if scope == _CAT:
            return scope, (sid or None), None
        if scope == _PERS:
            return scope, None, (sid or None)
        return scope, None, None

    def _can_read_item(self, item: MemoryItem) -> bool:
        """Fail-closed read check (TM.S10): may the identity SEE this item? Any error → deny."""
        try:
            scope, cat, owner = self._item_scope_cat(item)
            return self.access.can_read(self.identity, scope, cat, owner=owner)
        except Exception:
            return False

    def _access_denied_edit(self, item: MemoryItem) -> Optional[str]:
        """The refusal message when the identity may NOT write `item`, else None. Enforced only
        when an enforcing policy is present (team mode); local/direct construction always allows.
        Fail-closed: an undeterminable grant denies."""
        if self.access is None or not getattr(self.access, "enforce", False):
            return None
        scope, cat, owner = self._item_scope_cat(item)
        try:
            allowed = self.access.can_edit(self.identity, scope, cat, owner=owner)
        except Exception:
            allowed = False
        if allowed:
            return None
        return (f"access denied: {self.identity or 'unknown'} is not permitted to write "
                f"[{scope}] items — nothing written (ask a project approver for the editor role)")

    # --- doc-schema downgrade safety (D6) -----------------------------------
    @staticmethod
    def _downgrade_refusal(*items: Optional[MemoryItem]) -> Optional[str]:
        """D6 — the refusal message when ANY doc this call would rewrite was written by a newer
        mokata than this one, else None. Called at the head of every mutation path, in the same
        place and the same shape as `_access_denied_edit`: BEFORE the gate, so the refusal costs
        the user no approval prompt and NOTHING is written — the doc on disk is byte-identical
        after the attempt.

        Takes every item the path would durably write, not just the one it is "about": publishing
        supersedes its base, a rollback restores its prior, a heal supersedes both sides of a
        contradiction, a consolidation touches every `old`. Each of those is a write-back, so each
        is a place the strip could happen — and a path that refuses for one doc must refuse for the
        whole call, or it lands a half-applied change (a superseded base whose successor never
        published). The first refusal wins; announcing is once-per-session (`downgrade_refusal`)."""
        for item in items:
            if item is None:
                continue
            message = downgrade_refusal(item)
            if message is not None:
                return message
        return None

    # --- team-mode journal-first routing (TM.S5b, doc 48 E3/C5) --------------
    def _team_mode(self) -> bool:
        """True only when this store was built from a surface AND that surface is in team mode.
        Degrade-clean + fail-closed: no surface, or any error reading the mode, reads as local
        (so the local backend path is always taken by default)."""
        if self._surface is None:
            return False
        try:
            from .. import run_mode as _rm
            return _rm.read_mode(self._surface) == _rm.TEAM
        except Exception:
            # D5 — deliberately left BROAD, with no narrow class to name: `run_mode.read_mode`'s own
            # docstring promises "Never raises" (it is the fail-closed mode resolver), so there is
            # no honest class to enumerate. False = LOCAL, which is the fail-closed direction.
            return False

    @staticmethod
    def _base_revision(item: MemoryItem) -> Optional[int]:
        """TM.S5c — the compare-and-set base for a gated UPDATE/DELETE: the shared-row `revision`
        the item was READ at, stamped onto it by a revision-tracking backend (PostgresBackend). None
        when the backend doesn't track revisions (the SQLite floor / a brand-new item) — the flush
        then treats the write as believed-new (INSERT-or-conflict), never a silent overwrite."""
        return getattr(item, "_revision", None)

    def _durable_write(self, item: MemoryItem, *, op: str,
                       backend_call: Callable[[], None], team: bool,
                       base_revision: Optional[int] = None, ledger: Any = None) -> None:
        """TM.S5c — the SINGLE durable-write fork every gated method uses. In TEAM mode the write is
        journal-first + CAS-guarded (`op` + `base_revision`), NEVER direct-to-backend; in LOCAL mode
        it is exactly today's `backend_call` (byte-identical). The captured `ledger_id` (the human
        approval's ledger seq — the gate's `approved` entry is the next seq with no intervening
        write) rides the entry so the deferred flush inherits the original consent (C5/P2)."""
        if team:
            led = ledger if ledger is not None else self._ledger
            # `len+1` is the seq the gate's upcoming `approved` entry will land at. MS.S3/B2: the
            # WriteGate holds the ledger append-lock across THIS commit and that approved record, so
            # no other window can append between here and there — the prediction is provably exact,
            # never misattributed to a racing write's approval.
            ledger_id = (len(led) + 1) if led is not None else None
            self._journal_team_write(item, ledger_id, op=op, base_revision=base_revision)
        else:
            backend_call()

    def _resolve_team_writer(self) -> Any:
        """The injected TEAM-writer strategy, or a lazily-constructed default `TeamWriter` (PRE-SIMP,
        0.0.15). Only ever reached on the team-mode write path (guarded by `_team_mode()`), so a
        directly-constructed store that forces team mode still gets the identical default writer."""
        if self._team_writer is None:
            from .team_writer import TeamWriter
            self._team_writer = TeamWriter()
        return self._team_writer

    def _journal_team_write(self, item: MemoryItem, ledger_id: Any, *,
                            op: str = _OP_PUT,
                            base_revision: Optional[int] = None) -> None:
        """Journal-first (doc 48 E3): buffer this durable write in the crash-safe local journal
        instead of writing direct-to-backend. `ledger_id` is the gate's approval id (C5/P2): the
        deferred flush re-records it, so deferred durability inherits the human decision. `op`
        (put/update/delete) + `base_revision` drive the flush's compare-and-set (TM.S5/S5c), so a
        concurrent change SURFACES as a conflict — never silently last-writer-wins.

        PRE-SIMP (0.0.15) — delegates to the injected team-writer seam so `store` no longer reaches
        UP to team_journal/team_audit/teamdb itself; the resolved write is byte-identical."""
        self._resolve_team_writer().journal_write(
            self._surface, item, ledger_id, op=op, base_revision=base_revision)

    def _best_effort_flush(self) -> None:
        """After a healthy gated team write, flush the journal so the write reaches Postgres
        immediately (doc 48 E3: 'flush when healthy'). NEVER blocks and NEVER raises — offline
        returns skipped (the write stays journaled: work-locally, nothing lost; `mokata sync`
        reconciles later). The committed gate decision is never undone by a flush hiccup.

        PRE-SIMP (0.0.15) — delegates to the injected team-writer seam (byte-identical flush)."""
        self._resolve_team_writer().flush(self._surface, self._ledger)

    def remember(self, item: MemoryItem,
                 confirm: Optional[Callable[[str], bool]] = None,
                 assume_yes: bool = False, policy: Any = None) -> WriteResult:
        if not self.type_enabled(item.mtype):
            raise MemoryDisabledError(
                f"memory type '{item.mtype}' is disabled; enable it to remember this"
            )

        # TM.S10 — refuse a write the identity isn't permitted for, BEFORE the gate (nothing is
        # written, no gate prompt). Fail-closed. A no-op in local/zero-config (no policy).
        denied = self._access_denied_edit(item)
        if denied is not None:
            return WriteResult(None, committed=False, aborted=True, message=denied)

        # D6 — refuse to rewrite a doc a NEWER mokata wrote (the strip). `remember` is a write-back
        # path whenever its item came from a read: a memory-share import hands it a teammate's
        # parsed doc, and a re-remember hands it one straight off the backend.
        refusal = self._downgrade_refusal(item)
        if refusal is not None:
            return WriteResult(None, committed=False, aborted=True, refused=True, message=refusal)

        team = self._team_mode()

        def _commit() -> None:
            # TM.S10 — stamp the REAL author (M-1); a no-op without an identity (byte-identical).
            self._stamp_author(item)
            # Stage 35e (frugal): compute the embedding once, on the gated write, so semantic
            # recall later embeds only the query. No-op when no embedder is configured.
            if self.embedder is not None and "_embedding" not in item.provenance:
                item.provenance["_embedding"] = list(
                    self.embedder(f"{item.subject} {item.value}"))
            if team:
                # TM.S5b — journal-first, capturing the gate's approval ledger id AT this point.
                # The WriteGate records its "approved" entry immediately AFTER this commit with no
                # intervening ledger write, so the next seq (len+1) IS that approval's id (C5/P2).
                # MS.S3/B2: the gate holds the ledger append-lock across this commit + that approved
                # record, so `len+1` is provably the approval's seq — exact under concurrency.
                ledger_id = (len(self._ledger) + 1) if self._ledger is not None else None
                self._journal_team_write(item, ledger_id)
            else:
                self.backend.put(item)     # local: byte-for-byte the pre-TM.S5b path
            self._bump_write()

        # M2 (Stage 39): every memory write routes through the ONE universal WriteGate —
        # secret-scan (hard block) + human gate + audit ledger — with the rich render_write
        # surface preserved. No second gate path.
        outcome = self._gated_commit(item.subject, f"{item.subject}\n{item.value}",
                                     _commit, self.render_write(item),
                                     confirm=confirm, assume_yes=assume_yes, policy=policy)
        if outcome.committed:
            if team:
                self._best_effort_flush()   # flush when healthy; offline → journaled, not lost
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

    # --- enforcement promotion (TM.S7 — the human-gated moment, doc 62 §4 + P2) ----------
    def render_promotion(self, item: MemoryItem, old: str, new: str, direction: str) -> str:
        """The gate surface for an enforcement change — honest about what it does (and doesn't):
        it moves the BINDING only, the rule text is untouched."""
        verb = {"promote": "PROMOTE", "demote": "DEMOTE"}.get(direction, "CHANGE")
        return (f"mokata · {verb} rule enforcement [{item.governance_kind}] {item.subject}: "
                f"{old} → {new}\nThis changes ONLY the enforcement binding — the rule's condition "
                f"({item.value!r}) is NOT rewritten.\nNothing changes unless you approve.")

    def _record_enforcement_change(self, item: MemoryItem, old: str, new: str,
                                   direction: str, actor: str, approval_seq: Any) -> None:
        """Audit the promotion (doc 62 §6 who/old→new/approval). `approval_seq` is the REAL seq the
        WriteGate returned for its `approved` entry (B2) — NOT `len(ledger)`, which a concurrent
        append could have moved past the actual approval."""
        if self._ledger is not None:
            self._ledger.record("enforcement_change", subject=item.subject, item_id=item.id,
                                 gkind=item.governance_kind, direction=direction,
                                 old=old, new=new, actor=actor, approval_seq=approval_seq)

    def promote(self, item_id: str, to: str,
                confirm: Optional[Callable[[str], bool]] = None,
                assume_yes: bool = False, actor: str = "user") -> PromoteResult:
        """Change a RULE's enforcement binding to `to` (advisory|soft|hard) — the ONE gated moment
        (doc 62 §4 + P2). Human-gated through the WriteGate + ledgered (who/old→new/approval).
        Changes ONLY the binding — the rule's condition is byte-identical. Works both ways: a
        raise is a 'promote', a lower level is a 'demote' (also gated). Fail-closed: an unknown
        level, a missing item, or a non-rule item makes NO change and writes nothing."""
        from .item import (ENFORCEMENT_LEVELS, RULE as _RULE, enforcement_rank,
                           governance_kind)
        if to not in ENFORCEMENT_LEVELS:
            return PromoteResult(False, aborted=True,
                                 message=f"unknown enforcement level '{to}' "
                                         f"(use {'/'.join(ENFORCEMENT_LEVELS)})")
        item = self.get(item_id)
        if item is None:
            return PromoteResult(False, aborted=True,
                                 message=f"no memory item with id '{item_id}'")
        if governance_kind(item.kind) != _RULE:
            return PromoteResult(False, aborted=True, item=item,
                                 message=f"enforcement applies only to rules; "
                                         f"'{item.subject}' is a {item.governance_kind}")
        # D6 — a promotion is a read-modify-WRITE of the whole doc: it rewrites every field to
        # change one binding. On a newer doc that rewrite is the strip.
        refusal = self._downgrade_refusal(item)
        if refusal is not None:
            return PromoteResult(False, aborted=True, item=item, message=refusal)
        old = item.effective_enforcement
        if old == to:
            return PromoteResult(False, aborted=False, item=item, old=old, new=to,
                                 direction="unchanged",
                                 message=f"'{item.subject}' is already {to} (no change)")
        direction = "promote" if enforcement_rank(to) > enforcement_rank(old) else "demote"

        original_value, original_kind = item.value, item.kind    # prove the condition is untouched
        team = self._team_mode()

        def _commit() -> None:
            item.enforcement = to                # the BINDING only — value/kind/subject untouched
            self._stamp_author(item)             # TM.S10 — real author on the promotion write (M-1)
            # TM.S5c — team: journal-first + CAS (never direct-to-backend); local: today's path.
            self._durable_write(item, op=_OP_UPDATE, base_revision=self._base_revision(item),
                                backend_call=lambda: self.backend.update(item), team=team)
            self._bump_write()

        # The universal WriteGate (secret-scan is a no-op — the condition isn't changing — + the
        # human gate over render_promotion + the audit ledger). No second gate path.
        outcome = self._gated_commit(item.subject, item.subject, _commit,
                                     self.render_promotion(item, old, to, direction),
                                     confirm=confirm, assume_yes=assume_yes)
        if not outcome.committed:
            # declined / blocked → nothing changed; the in-memory item stays as it was.
            item.value, item.kind = original_value, original_kind
            return PromoteResult(False, aborted=True, item=item, old=old, new=to,
                                 direction=direction, message="declined at the human gate")
        self._record_enforcement_change(item, old, to, direction, actor, outcome.approval_seq)
        if team:
            self._best_effort_flush()            # flush when healthy; offline → journaled, not lost
        return PromoteResult(True, aborted=False, item=item, old=old, new=to,
                             direction=direction, message=f"{direction}d {item.subject}: "
                                                          f"{old} → {to}")

    # --- scope promotion (TM.S10 — the audience-widening gate, doc 52 M-2 + P2) -----------
    def render_scope_promotion(self, item: MemoryItem, old: str, new: str) -> str:
        """The gate surface for a SCOPE promotion — honest that it widens the AUDIENCE (who can
        see the item), not its content."""
        return (f"mokata · PROMOTE scope [{item.subject}]: {old} → {new}\nThis WIDENS who can see "
                f"this item (its content is NOT changed).\nNothing changes unless you approve.")

    def _record_scope_promotion(self, item: MemoryItem, old: str, new: str, actor: str,
                                approval_seq: Any) -> None:
        """Audit the scope promotion (doc 62 §6 who/old→new/approval). `approval_seq` is the REAL
        seq the WriteGate returned for its `approved` entry (B2), not `len(ledger)`."""
        if self._ledger is not None:
            self._ledger.record("scope_promotion", subject=item.subject, item_id=item.id,
                                 old=old, new=new, actor=actor, approval_seq=approval_seq)

    def promote_scope(self, item_id: str, to_level: str, to_id: Optional[str] = None,
                      confirm: Optional[Callable[[str], bool]] = None,
                      assume_yes: bool = False, actor_id: Optional[str] = None
                      ) -> ScopePromoteResult:
        """Broaden an item's scope TO `to_level` (personal→category→project→team→global) — the
        audience-widening act (doc 52 M-2). SEPARATE from the S7 enforcement promotion; both gated.
        Requires the promotion-approver role at the target scope (team mode) — human-gated through
        the WriteGate + ledgered (who/old→new/approval). Fail-closed: an unknown level, a missing
        item, a non-broadening move, a missing role, or an undeterminable grant makes NO change and
        writes nothing. Local/zero-config (no policy) → a single user has full authority."""
        from .scope import DEFAULT_SCOPE_LEVEL, is_valid_scope, scope_depth
        who = actor_id or self.identity or "user"
        if not is_valid_scope(to_level):
            return ScopePromoteResult(False, aborted=True,
                                      message=f"unknown scope level '{to_level}'")
        item = self.get(item_id)
        if item is None:
            return ScopePromoteResult(False, aborted=True,
                                      message=f"no memory item with id '{item_id}'")
        # D6 — a scope promotion rewrites the whole doc to widen its audience. On a newer doc that
        # rewrite is the strip — and it would ALSO broadcast the stripped item to more people.
        refusal = self._downgrade_refusal(item)
        if refusal is not None:
            return ScopePromoteResult(False, aborted=True, item=item, message=refusal)
        old = item.scope_level or DEFAULT_SCOPE_LEVEL
        # Only BROADENING is gated here (doc 52 M-2): the target must be strictly broader (smaller
        # depth). A same/narrower target is refused — narrowing an audience is not a promotion.
        if scope_depth(to_level) >= scope_depth(old):
            return ScopePromoteResult(False, aborted=True, item=item, old=old, new=to_level,
                                      message=f"scope promotion only BROADENS: {old} → {to_level} "
                                              f"is not a widening (target must be broader)")
        # Access: promotion-approver at the target (broader) scope. Fail-closed on any doubt.
        if self.access is not None and getattr(self.access, "enforce", False):
            from .scope import CATEGORY as _CAT
            cat = to_id if to_level == _CAT else None
            try:
                permitted = self.access.can_promote_scope(who, to_level, cat)
            except Exception:
                permitted = False
            if not permitted:
                return ScopePromoteResult(False, aborted=True, item=item, old=old, new=to_level,
                                          message=(f"access denied: {who} lacks the "
                                                   f"promotion-approver role for [{to_level}] — "
                                                   f"scope promotion refused (nothing written)"))

        original_level, original_id = item.scope_level, item.scope_id
        team = self._team_mode()

        def _commit() -> None:
            item.scope_level = to_level
            item.scope_id = to_id if to_id is not None else ""
            self._stamp_author(item)             # TM.S10 — real author on the promotion write (M-1)
            # TM.S5c — team: journal-first + CAS (never direct-to-backend); local: today's path.
            self._durable_write(item, op=_OP_UPDATE, base_revision=self._base_revision(item),
                                backend_call=lambda: self.backend.update(item), team=team)
            self._bump_write()

        outcome = self._gated_commit(item.subject, item.subject, _commit,
                                     self.render_scope_promotion(item, old, to_level),
                                     confirm=confirm, assume_yes=assume_yes)
        if not outcome.committed:
            item.scope_level, item.scope_id = original_level, original_id   # untouched on decline
            return ScopePromoteResult(False, aborted=True, item=item, old=old, new=to_level,
                                      message="declined at the human gate")
        self._record_scope_promotion(item, old, to_level, who, outcome.approval_seq)
        if team:
            self._best_effort_flush()            # flush when healthy; offline → journaled, not lost
        return ScopePromoteResult(True, aborted=False, item=item, old=old, new=to_level,
                                  message=f"promoted {item.subject}: {old} → {to_level}")

    # --- PM review workflow (TM.S11 — the review state machine + roles, doc 62 §6 + P2) --------
    def review_required(self) -> bool:
        """True only when an ENFORCING access policy is present (team mode) — the review workflow
        engages: proposed changes enter as Drafts and must be reviewed before they publish. In
        local/zero-config (no policy) a single user needs NO review: edits/promotions publish
        directly, byte-identical to today (doc 63 §5)."""
        return bool(self.access is not None and getattr(self.access, "enforce", False))

    def _can_approve(self, who: Optional[str], scope: str, category: Optional[str]) -> bool:
        """Fail-closed approver check (TM.S11): does `who` hold the approver role at `scope`
        (+category)? A non-enforcing policy (local) needs no approval → True. Any doubt → deny."""
        if self.access is None or not getattr(self.access, "enforce", False):
            return True
        try:
            return self.access.can_approve(who, scope, category)
        except Exception:
            return False

    @staticmethod
    def _review_state(item: MemoryItem) -> str:
        return (getattr(item, "review", None) or {}).get("state", "")

    def pending_reviews(self) -> List[MemoryItem]:
        """The proposals awaiting a decision — Draft / In-Review / Approved-not-yet-published — for
        the PM's `mokata memory review` list. Read-only: PROPOSED status keeps them out of live
        recall; PUBLISHED (→ ACTIVE) and REJECTED proposals are already excluded by status."""
        from .item import PROPOSED
        return [i for i in self.backend.all(statuses=(PROPOSED,))
                if i.mtype in self.enabled_types]

    def _record_review(self, kind: str, item: MemoryItem, *, actor: str, diff: str,
                       approval_seq: Any, **fields: Any) -> None:
        """Audit a review step (doc 62 §6 who/what/when/approval + the rendered diff). `approval_seq`
        is the REAL seq the WriteGate returned for its `approved` entry (B2), not `len(ledger)` —
        the same fix as promote / scope-promotion."""
        if self._ledger is not None:
            self._ledger.record(kind, subject=item.subject, item_id=item.id, actor=actor,
                                diff=diff, approval_seq=approval_seq, **fields)

    def propose(self, item: MemoryItem, *, base_id: str = "", change: str = "new",
                proposer: Optional[str] = None,
                confirm: Optional[Callable[[str], bool]] = None,
                assume_yes: bool = False) -> ReviewResult:
        """Enter a proposed change into the review workflow as a DRAFT (doc 62 §6). The proposal is
        stored NOT-live (status PROPOSED) — it only goes live when PUBLISHED. Editor edits, S7
        enforcement promotions, and S9 formula proposals all feed this. Human-gated (secret-scan +
        the human gate + ledger) — a proposer must hold edit rights at the item's scope (fail-closed
        in team mode; a no-op in local). `base_id` is the published item this change supersedes
        (for the diff + rollback); `change` labels the origin (edit|enforce|formula|new)."""
        from .review import DRAFT
        who = proposer or self.identity or "user"
        # The proposer is an Editor/Author: they must be permitted to write at this scope (S10).
        denied = self._access_denied_edit(item)
        if denied is not None:
            return ReviewResult(False, aborted=True, message=denied)
        # D6 — the proposal doc itself is written (as a not-live DRAFT). `base` is only READ here
        # (for the diff); publishing is what rewrites it, and `_transition` guards it there.
        refusal = self._downgrade_refusal(item)
        if refusal is not None:
            return ReviewResult(False, aborted=True, message=refusal)
        base = self.get(base_id) if base_id else None

        from .item import PROPOSED
        from .review import diff_line, render_transition
        item.status = PROPOSED
        item.review = {"state": DRAFT, "proposer": who, "approver": "",
                       "base_id": base_id or "", "change": change}
        diff = diff_line(base, item)
        team = self._team_mode()

        def _commit() -> None:
            self._stamp_author(item)
            # TM.S5c — a NEW draft is believed-new (base_revision None → INSERT-or-conflict).
            self._durable_write(item, op=_OP_PUT, base_revision=None,
                                backend_call=lambda: self.backend.put(item), team=team)
            self._bump_write()

        outcome = self._gated_commit(
            item.subject, f"{item.subject}\n{item.value}", _commit,
            render_transition(item, base, "", DRAFT, who),
            confirm=confirm, assume_yes=assume_yes)
        if not outcome.committed:
            if outcome.findings:
                return ReviewResult(False, aborted=True,
                                    message="blocked: secret detected — not proposed")
            return ReviewResult(False, aborted=True, message="declined at the human gate")
        self._record_review("review_transition", item, actor=who, diff=diff,
                            approval_seq=outcome.approval_seq, frm="", to=DRAFT, change=change)
        if team:
            self._best_effort_flush()            # flush when healthy; offline → journaled, not lost
        return ReviewResult(True, item=item, state=DRAFT,
                            message=f"proposed {item.subject} as a Draft ({item.id})")

    def _transition(self, item_id: str, to_state: str, *, actor: Optional[str] = None,
                    confirm: Optional[Callable[[str], bool]] = None,
                    assume_yes: bool = False) -> ReviewResult:
        """Move a proposal one step through Draft→In-Review→Approved→Published (or →Rejected). Each
        transition is human-gated + ledgered with the rendered diff (doc 62 §6). Fail-closed: an
        unknown item, a non-proposal, an ILLEGAL transition, a self-approval, or a missing approver
        role makes NO change and writes nothing (deny on doubt)."""
        from . import review as R
        from .item import ACTIVE, REJECTED as REJECTED_STATUS, SUPERSEDED
        who = actor or self.identity or "user"
        item = self.get(item_id)
        if item is None:
            return ReviewResult(False, aborted=True, message=f"no memory item with id '{item_id}'")
        # D6 — every transition rewrites the proposal doc (its review state), and PUBLISH also
        # rewrites the base it supersedes. Refuse before any of the workflow checks: a refusal is
        # not a workflow outcome, and nothing here has written yet.
        refusal = self._downgrade_refusal(item)
        if refusal is not None:
            return ReviewResult(False, aborted=True, item=item, message=refusal)
        frm = self._review_state(item)
        if not R.is_valid_state(frm) or frm == "":
            return ReviewResult(False, aborted=True, item=item,
                                message=f"'{item.subject}' is not a proposal in the review workflow")
        if not R.can_transition(frm, to_state):
            return ReviewResult(False, aborted=True, item=item, state=frm,
                                message=f"illegal transition {frm} → {to_state} for "
                                        f"'{item.subject}' (refused, fail-closed)")

        scope, cat, _owner = self._item_scope_cat(item)
        proposer = (item.review or {}).get("proposer", "")

        # APPROVE — the separation-of-duties + named-approver gate (HARD, fail-closed).
        if to_state == R.APPROVED:
            if not R.separation_ok(proposer, who):
                msg = (f"separation of duties: {who} proposed this change and may not self-approve"
                       if who and who == proposer
                       else "separation of duties: a distinct approver identity is required")
                return ReviewResult(False, aborted=True, item=item, state=frm, message=msg)
            if not self._can_approve(who, scope, cat):
                return ReviewResult(False, aborted=True, item=item, state=frm,
                                    message=f"access denied: {who} is not an approver for "
                                            f"[{scope}] — cannot approve (ask the project PM)")

        # PUBLISH — required-review: a recorded, still-valid approver sign-off is mandatory, AND
        # (TM.S5c fold-in) separation of duties at publish too — the proposer may not publish their
        # own change (fail-closed), not only self-approve. A distinct publisher is required.
        if to_state == R.PUBLISHED:
            if not R.separation_ok(proposer, who):
                msg = ("separation of duties: the proposer may not publish their own change"
                       if who and who == proposer
                       else "separation of duties: a distinct publisher identity is required")
                return ReviewResult(False, aborted=True, item=item, state=frm, message=msg)
            approver = (item.review or {}).get("approver", "")
            if not approver or not self._can_approve(approver, scope, cat):
                return ReviewResult(False, aborted=True, item=item, state=frm,
                                    message="publish blocked: no required-approver sign-off "
                                            "recorded (a change can't publish without review)")

        base = self.get((item.review or {}).get("base_id", "")) or None
        # D6 — PUBLISH supersedes `base`, which is a write-back of the BASE's doc. A newer base
        # refuses the whole transition: publishing the successor while failing to supersede its
        # base would leave two live items claiming the same subject.
        refusal = self._downgrade_refusal(base)
        if refusal is not None:
            return ReviewResult(False, aborted=True, item=item, state=frm, message=refusal)
        diff = R.diff_line(base, item)
        team = self._team_mode()

        def _commit() -> None:
            item.review = dict(item.review or {})
            item.review["state"] = to_state
            if to_state == R.APPROVED:
                item.review["approver"] = who
            elif to_state == R.PUBLISHED:
                item.status = ACTIVE                     # now live
                if base is not None and base.status == ACTIVE:
                    base.status = SUPERSEDED             # supersede lineage → rollback
                    if base.id not in item.supersedes:
                        item.supersedes.append(base.id)
                    # TM.S5c — the superseded prior is its own CAS-guarded team write.
                    self._durable_write(base, op=_OP_UPDATE,
                                        base_revision=self._base_revision(base),
                                        backend_call=lambda: self.backend.update(base), team=team)
            elif to_state == R.REJECTED:
                item.status = REJECTED_STATUS            # terminal, never live
            self._stamp_author(item)
            # TM.S5c — team: journal-first + CAS (never direct-to-backend); local: today's path.
            self._durable_write(item, op=_OP_UPDATE, base_revision=self._base_revision(item),
                                backend_call=lambda: self.backend.update(item), team=team)
            self._bump_write()

        outcome = self._gated_commit(item.subject, item.subject, _commit,
                                     R.render_transition(item, base, frm, to_state, who),
                                     confirm=confirm, assume_yes=assume_yes)
        if not outcome.committed:
            return ReviewResult(False, aborted=True, item=item, state=frm,
                                message="declined at the human gate")
        self._record_review("review_transition", item, actor=who, diff=diff,
                            approval_seq=outcome.approval_seq, frm=frm, to=to_state)
        if team:
            self._best_effort_flush()            # flush when healthy; offline → journaled, not lost
        return ReviewResult(True, item=item, state=to_state,
                            message=f"{item.subject}: {frm} → {to_state}")

    def submit_for_review(self, item_id: str, **kw) -> ReviewResult:
        """Draft → In-Review (the proposer advances their draft). Gated + ledgered."""
        from .review import IN_REVIEW
        return self._transition(item_id, IN_REVIEW, **kw)

    def approve(self, item_id: str, **kw) -> ReviewResult:
        """In-Review → Approved. HARD separation of duties (proposer ≠ approver) + the named
        approver role at the item's scope/category are enforced, fail-closed (doc 62 §6)."""
        from .review import APPROVED
        return self._transition(item_id, APPROVED, **kw)

    def reject(self, item_id: str, **kw) -> ReviewResult:
        """Reject a proposal (from any non-terminal state) → terminal Rejected. Gated + ledgered."""
        from .review import REJECTED
        return self._transition(item_id, REJECTED, **kw)

    def publish(self, item_id: str, **kw) -> ReviewResult:
        """Approved → Published: the change goes LIVE and supersedes the item it changed (rollback
        lineage). Blocked without a recorded required-approver sign-off (fail-closed, doc 62 §6)."""
        from .review import PUBLISHED
        return self._transition(item_id, PUBLISHED, **kw)

    def rollback(self, item_id: str, *, actor: Optional[str] = None,
                 confirm: Optional[Callable[[str], bool]] = None,
                 assume_yes: bool = False) -> ReviewResult:
        """Restore the PRIOR published item this one superseded — the one-click restore over the
        supersede lineage (doc 62 §6). Human-gated + ledgered; requires approver rights in team
        mode (a PM action), fail-closed. No item / no lineage → nothing changes."""
        from .item import ACTIVE, SUPERSEDED
        from .review import render_rollback, diff_line
        who = actor or self.identity or "user"
        item = self.get(item_id)
        if item is None:
            return ReviewResult(False, aborted=True, message=f"no memory item with id '{item_id}'")
        prior_ids = list(getattr(item, "supersedes", []) or [])
        prior = None
        for pid in reversed(prior_ids):                  # the most-recent superseded ancestor
            prior = self.get(pid)
            if prior is not None:
                break
        if prior is None:
            return ReviewResult(False, aborted=True, item=item,
                                message=f"nothing to roll back: '{item.subject}' has no prior "
                                        "in its supersede lineage")
        # D6 — a rollback rewrites BOTH docs (the current steps aside, the prior is restored). If
        # either is newer than this build, neither is touched — a half-rollback would leave the
        # store with no live item at all.
        refusal = self._downgrade_refusal(item, prior)
        if refusal is not None:
            return ReviewResult(False, aborted=True, item=item, message=refusal)
        scope, cat, _owner = self._item_scope_cat(item)
        if not self._can_approve(who, scope, cat):
            return ReviewResult(False, aborted=True, item=item,
                                message=f"access denied: {who} is not an approver for [{scope}] "
                                        "— cannot roll back")
        diff = diff_line(item, prior)
        team = self._team_mode()

        def _commit() -> None:
            item.status = SUPERSEDED                     # the current one steps aside
            prior.status = ACTIVE                        # restore the prior
            self._stamp_author(prior)
            # TM.S5c — both writes are CAS-guarded team journals (never direct-to-backend); local
            # is today's two backend updates.
            self._durable_write(item, op=_OP_UPDATE, base_revision=self._base_revision(item),
                                backend_call=lambda: self.backend.update(item), team=team)
            self._durable_write(prior, op=_OP_UPDATE, base_revision=self._base_revision(prior),
                                backend_call=lambda: self.backend.update(prior), team=team)
            self._bump_write(2)

        outcome = self._gated_commit(item.subject, item.subject, _commit,
                                     render_rollback(item, prior),
                                     confirm=confirm, assume_yes=assume_yes)
        if not outcome.committed:
            return ReviewResult(False, aborted=True, item=item,
                                message="declined at the human gate")
        self._record_review("review_rollback", item, actor=who, diff=diff,
                            approval_seq=outcome.approval_seq, restored=prior.id)
        if team:
            self._best_effort_flush()            # flush when healthy; offline → journaled, not lost
        return ReviewResult(True, item=prior, state="published",
                            message=f"rolled back {item.subject}: restored {prior.id}")

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
        # TM.S6 — recall reads the UNION across the scope path (byte-identical when no context).
        return [i for i in self.scoped_active(mtype=mtype) if i.subject == subject]

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

    # --- formula recall by applicability (TM.S9, doc 62 §2/§8) ---------------
    def recall_formulas(self, query: str) -> List[MemoryItem]:
        """The in-scope FORMULAE whose APPLICABILITY matches `query` — matched by their trigger/
        topic metadata (not general similarity), returned for INJECTION (template + params),
        alongside the existing fact/rule recall. Reads the S6 UNION across the scope path (so
        team-scoped formulae surface on the path, byte-identical local recall when unscoped).
        Never computes/evaluates a formula — computed formulae are deferred (doc 62 §8)."""
        from .formula import recall_applicable
        return recall_applicable(self.scoped_active(), query,
                                 context=self.scope_context)

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
                       assume_yes: bool = False, policy: Any = None) -> HealingResult:
        """Resolve a surfaced proposal. Default (reject/defer) changes nothing; approve
        and edit are human-gated. NEVER auto-rewrites."""
        if decision not in ("approve", "edit", "reject", "defer"):
            raise MemoryError(f"unknown decision '{decision}'")
        if decision in ("reject", "defer"):
            self._record_healing(p, decision, changed=False)
            return HealingResult(changed=False, message=f"{decision}: no change")

        # D6 — self-healing is the most dangerous write-back of all: it rewrites items the human
        # approved long ago, on the machine that happens to be running. Every doc this decision
        # would touch is guarded — the stale/superseded `old`, the winning `new`, and the human's
        # `edited` replacement. reject/defer are above: they write nothing, so they need no guard.
        refusal = self._downgrade_refusal(p.old, p.new, edited)
        if refusal is not None:
            self._record_healing(p, decision, changed=False)
            return HealingResult(changed=False, aborted=True, refused=True, message=refusal)

        team = self._team_mode()

        # Build the commit closure + the (untrusted) content to secret-scan + the result.
        # TM.S5c — every backend write below routes through `_durable_write`: team journals-first +
        # CAS-guarded (never direct-to-backend), local is byte-identical.
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
                    self._durable_write(item, op=_OP_UPDATE,
                                        base_revision=self._base_revision(item),
                                        backend_call=lambda item=item: self.backend.update(item),
                                        team=team)
                self._durable_write(edited, op=_OP_PUT, base_revision=None,
                                    backend_call=lambda: self.backend.put(edited), team=team)
                self._bump_write(len(replaced) + 1)
        elif p.kind == CONTRADICTION and p.new is not None:
            content = f"{p.new.subject}\n{p.new.value}"       # the winning new value is scanned
            result_item, result_msg = p.new, "approved"

            def _commit() -> None:
                p.old.status = SUPERSEDED
                if p.old.id not in p.new.supersedes:
                    p.new.supersedes.append(p.old.id)
                self._durable_write(p.old, op=_OP_UPDATE, base_revision=self._base_revision(p.old),
                                    backend_call=lambda: self.backend.update(p.old), team=team)
                self._durable_write(p.new, op=_OP_UPDATE, base_revision=self._base_revision(p.new),
                                    backend_call=lambda: self.backend.update(p.new), team=team)
                self._bump_write(2)
        else:   # STALE approve — marks an existing item stale; no new untrusted value
            content = p.old.subject
            result_item, result_msg = None, "approved"

            def _commit() -> None:
                p.old.status = STATUS_STALE
                self._durable_write(p.old, op=_OP_UPDATE, base_revision=self._base_revision(p.old),
                                    backend_call=lambda: self.backend.update(p.old), team=team)
                self._bump_write()

        # M2 (Stage 39): the SAME WriteGate path as remember — scan + gate (old→new surface) +
        # ledger — so healing never bypasses the universal gate either.
        outcome = self._gated_commit(p.subject, content, _commit, self.render_proposal(p),
                                     confirm=confirm, assume_yes=assume_yes, policy=policy)
        self._record_healing(p, decision, changed=outcome.committed)
        if outcome.committed:
            if team:
                self._best_effort_flush()        # flush when healthy; offline → journaled, not lost
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
                            ledger: Any = None, policy: Any = None) -> HealingResult:
        """Apply a consolidation proposal. Default (reject/defer) changes nothing;
        approve/edit are human-gated. NEVER auto-applies.

        SI.6 (74 C1 = 52 M-6): this used to run its OWN bare confirm and then write — the last
        durable memory writer that never entered the universal WriteGate. It therefore had no
        secret-scan (a merged/edited/summarized value could carry a credential straight into the
        store), no `write_gate` ledger record, and no WritePolicy seam. It now commits through
        `_gated_commit` like every other memory writer: the writes move into a `_commit` closure the
        gate runs, so scan → gate → ledger → commit is the ONLY order they can happen in."""
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

        # D6 — a consolidation rewrites EVERY doc in `p.olds` (supersede on MERGE, CAS-guarded
        # delete on PRUNE) plus the item it lands. A PRUNE is guarded like the rest, deliberately:
        # deleting a doc whose fields this build cannot read destroys them just as thoroughly as
        # stripping them, and "I did not understand it, so I removed it" is the same bug wearing a
        # different verb. Guard the landing item, the human's edit, and every old.
        refusal = self._downgrade_refusal(edited or p.new, p.new, *p.olds)
        if refusal is not None:
            _log(False, "refused")
            return HealingResult(changed=False, aborted=True, refused=True, message=refusal)

        team = self._team_mode()
        keep = edited or p.new              # MERGE/SUMMARIZE: the value this consolidation LANDS

        # What the gate must scan: the untrusted NEW content this consolidation would write. MERGE
        # and SUMMARIZE land a value (an edited/summarized item is exactly where a secret could be
        # introduced); PRUNE writes no new value at all — it only deletes — so, like the STALE branch
        # of `apply_proposal`, the subject alone is the content.
        content = f"{keep.subject}\n{keep.value}" if p.kind in (MERGE, SUMMARIZE) else p.subject

        def _commit() -> None:
            # TM.S5c — every write below routes through `_durable_write` (bound to THIS `led` so the
            # journaled entry inherits the approval's ledger id): team journals-first + CAS-guarded
            # (never a direct backend upsert/delete), local is byte-identical. Crucially the PRUNE
            # branch journals a CAS-guarded DELETE in team mode — it never hard-deletes a shared row
            # (the destructive path the audit flagged); local single-user prune still deletes.
            if p.kind == MERGE:
                if edited is not None:
                    self._durable_write(edited, op=_OP_PUT, base_revision=None,
                                        backend_call=lambda: self.backend.put(edited),
                                        team=team, ledger=led)
                for o in p.olds:
                    if o.id == keep.id:
                        continue
                    o.status = SUPERSEDED
                    if o.id not in keep.supersedes:
                        keep.supersedes.append(o.id)
                    self._durable_write(o, op=_OP_UPDATE, base_revision=self._base_revision(o),
                                        backend_call=lambda o=o: self.backend.update(o),
                                        team=team, ledger=led)
                if edited is None:
                    self._durable_write(keep, op=_OP_UPDATE,
                                        base_revision=self._base_revision(keep),
                                        backend_call=lambda: self.backend.update(keep),
                                        team=team, ledger=led)
                self._bump_write(len(p.olds))
            elif p.kind == SUMMARIZE:
                self._durable_write(keep, op=_OP_PUT, base_revision=None,
                                    backend_call=lambda: self.backend.put(keep),
                                    team=team, ledger=led)
                self._bump_write()
            elif p.kind == PRUNE:
                for o in p.olds:
                    self._durable_write(o, op=_OP_DELETE, base_revision=self._base_revision(o),
                                        backend_call=lambda o=o: self.backend.delete(o.id),
                                        team=team, ledger=led)
                self._bump_write(max(1, len(p.olds)))

        outcome = self._gated_commit(p.subject, content, _commit,
                                     self.render_consolidation(p), confirm=confirm,
                                     assume_yes=assume_yes, policy=policy, ledger=led)
        if outcome.committed:
            _log(True, decision)
            if team:
                self._best_effort_flush()    # flush when healthy; offline → journaled, not lost
            return HealingResult(changed=True, message=decision)
        if outcome.findings:
            _log(False, "blocked")
            return HealingResult(changed=False, aborted=True, blocked=True,
                                 message="blocked: secret detected — not applied")
        _log(False, "declined")
        return HealingResult(changed=False, aborted=True,
                             message="declined at the human gate")

    def close(self) -> None:
        self.backend.close()
