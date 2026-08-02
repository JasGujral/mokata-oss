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
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

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
    ARCHIVE,
    MERGE,
    PRUNE,
    SUMMARIZE,
    ConsolidationProposal,
    SummaryDrafter,
    propose_archival,
    propose_consolidations,
    render_consolidation,
)
from .healing import (CONTRADICTION, CROSS_WRITER, HealingProposal, detect_issues,
                      render_proposal)
from .item import ACTIVE, ARCHIVED, DECISION, DEFAULT_TOP_K, MEMORY_TYPES, STALE as STATUS_STALE
from .item import SUPERSEDED, MemoryItem, approval_ledger_id_of, downgrade_refusal, now_iso
from . import lifecycle
from ..degrade import FAILURE_LOCAL_IO, note_degraded
from ..errors import MokataError, failure_class_of

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
        return self._visible_filter(self.all_active(mtype=mtype,
                                                    scope_context=self.scope_context))

    # --- R-1 (DB.S8) the BOUNDED candidate read -----------------------------
    def candidate_scope_path(self) -> Optional[List[Any]]:
        """The scope path to send DOWN with a candidate query, or `None`.

        `None` in exactly the two cases `peek_active` already omits it: no scope context (local /
        zero-config), or a backend whose backfill has not run. Omitting it is always SAFE — the
        hydrated set still goes through `_visible_filter`, so this can only ever change how many
        rows the database returns, never which ones survive.
        """
        if self.scope_context is None:
            return None
        if not getattr(self.backend, "supports_scope_pushdown", False):
            return None
        from .scope import scope_path
        return scope_path(self.scope_context)

    def hydrate_candidates(self, ids: Any, *, subjects: Any = (),
                           mtype: Optional[str] = None,
                           count_read: bool = True) -> List[MemoryItem]:
        """R-1 — `scoped_active`'s BOUNDED twin: the same visibility rule over the rows the tiers
        NOMINATED instead of over every active row.

        The visibility half is deliberately unchanged and shares `_visible_filter` with
        `scoped_active`, so scope union, precedence and readability keep exactly ONE implementation.
        What changes is only how many rows reach it: ~150 nominated ids and their precedence groups
        rather than the whole active set (51,606 rows at DB.S8's 100k fixture).

        It COUNTS a read by default, like `scoped_active`, so `memory_stats.reads` — which
        `/mokata:govern` surfaces as the read/write ratio — keeps meaning the same thing across
        this change.

        JIT-STAMP-SEAM — `count_read=False` is the NON-COUNTING twin, and it is the same split
        `peek_visible_active` already makes against `scoped_active`: split the INSTRUMENTATION,
        never the rule (DB.S7c1). The per-turn injection reads through here on EVERY prompt, and a
        counted read per turn is exactly the JIT-RECALL-COUNTS-A-READ defect one layer up —
        `memory_stats.reads` would become a count of turns. Visibility, precedence and readability
        are untouched by the flag: there is still ONE `_visible_filter` and one rule, and the only
        thing that moves is whether the counter does.
        """
        if count_read:
            self._bump_read()
        items = self.backend.hydrate(list(ids), subjects=list(subjects), statuses=(ACTIVE,),
                                     scope_path=self.candidate_scope_path())
        if mtype is not None:
            items = [i for i in items if i.mtype == mtype]
        return self._visible_filter([i for i in items if i.mtype in self.enabled_types])

    def peek_visible_active(self, mtype: Optional[str] = None) -> List[MemoryItem]:
        """`scoped_active`'s NON-COUNTING twin — the SAME visible set, read without moving the
        read counter or persisting stats.

        For the surfaces that AUTO-INJECT memory rather than answer a user's recall: the always-on
        rules, the JIT context pull (`memory/brain.py`), the governance/visibility views. Merely
        OFFERING memory must mutate no durable state, or `memory_stats.reads` — which
        `/mokata:govern` surfaces as the read/write ratio — becomes a count of TURNS.

        DB.S7c1's precedent, made reusable: split the INSTRUMENTATION, never the rule. Visibility
        (scope union TM.S6 · precedence winner M-A2 · readability TM.S10) still has exactly ONE
        implementation, `_visible_filter`; only the counting differs. Before this existed, every
        non-counting caller had to open-code `_visible_filter(peek_active(...))` — and the JIT path
        simply never did, which is JIT-RECALL-UNSCOPED (doc 84 §4)."""
        return self._visible_filter(self.peek_active(mtype=mtype,
                                                     scope_context=self.scope_context))

    def _visible_filter(self, items: List[MemoryItem]) -> List[MemoryItem]:
        """THE visibility rule — scope union, precedence winner, readability — over an already-read
        list. Extracted at DB.S7c1 so that "what may this identity see" has exactly ONE
        implementation while its two callers differ in the ONE way they must: `scoped_active`
        COUNTS a read (it is recall), and the K2 healing path does NOT (it is a read-only surface
        that must mutate no durable state, `peek_active`'s contract). Splitting the rule instead of
        the instrumentation would have given visibility a second definition — the exact thing the
        `union_read` comment below refuses."""
        if self.scope_context is not None:
            from .scope import union_read
            # DB.S2b — `union_read` STAYS, even when the backend already pushed the same predicate
            # into SQL. It is not redundant belt-and-braces: it is what makes the pushdown an
            # OPTIMIZATION rather than a second, competing definition of visibility. Filtering an
            # already-filtered list is a no-op, so the result is identical either way — and on any
            # backend that can't push (a vault, a native client, a store whose backfill hasn't run)
            # this line is still the ONLY thing doing the scope filtering.
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
        """The gate surface for a remember.

        R9 — when this write SUPERSEDES existing items, the human is not being asked to store a
        new fact; they are being asked to retire someone else's approved one. So the prompt names
        what is being displaced and where it came from. Doc 83's poisoning row is exactly this
        moment: "a poisoned proposal a human rubber-stamps still lands", and a prompt that showed
        only the incoming value gave the reader nothing to be suspicious with.

        A plain remember (superseding nothing) renders byte-identically to before — there is no
        prior item, so there is no provenance to highlight and no block is added."""
        from .intelligence import provenance_block
        base = (f"mokata · propose to remember [{item.mtype}] {item.subject} = "
                f"{item.value!r}")
        displaced = ""
        for prior in self._superseded_items(item):
            displaced += (f"\n  this REPLACES an existing memory: {prior.subject} = "
                          f"{prior.value!r}" + provenance_block(prior))
        return base + displaced + "\nNothing is stored unless you approve."

    def _superseded_items(self, item: MemoryItem) -> List[MemoryItem]:
        """The existing items this write would retire, for the R9 highlight. Read-only and TOTAL:
        a missing id, an unreadable backend or any lookup error yields fewer lines, NEVER an
        exception — a provenance panel must not be able to break the approval prompt it decorates
        (the whole point of showing it is that the human can still decide)."""
        out: List[MemoryItem] = []
        for rid in (getattr(item, "supersedes", None) or []):
            try:
                prior = self.get(str(rid))
            except Exception:
                prior = None
            if prior is not None:
                out.append(prior)
        return out

    # --- identity + access (TM.S10, doc 52 M-1/M-2) -------------------------
    def _stamp_author(self, item: MemoryItem) -> None:
        """M-1 — stamp the REAL author on a durable write. Only replaces the "user"/empty
        placeholder (an explicit author, and every legacy item already on disk, is untouched);
        a no-op when the store has no identity (local/direct construction — byte-identical)."""
        if self.identity and item.provenance.get("author", "") in ("", "user"):
            item.provenance["author"] = self.identity

    def _stamp_approval(self, item: MemoryItem, ledger_id: Any) -> None:
        """M-1/R9 — stamp the CONSENT CHAIN on a durable write: who let this content land, when,
        and which audit-ledger entry says so (doc 52 M-1, "the item carries its own consent chain").

        Called from `_durable_write` only, i.e. inside the WriteGate's commit closure under its
        ledger hold. That placement IS the contract: the stamp cannot exist without the gated write
        that produced the ledger entry it names, and there is no second, ungated path that writes
        these fields. It sits beside `_stamp_author` for the same reason — one act, one place.

        Unlike `_stamp_author`, this OVERWRITES rather than filling a placeholder, and the
        difference is not an oversight. `author` is who wrote the item and does not change when
        someone else edits it; the approval names the human decision that licensed the content the
        item carries RIGHT NOW. An edit, a promotion, a publish or a supersede is a new decision,
        so it is a new stamp — an item whose content moved under approval #12 must not still claim
        approval #7, which is precisely the "approved once, mutated later" hole R9 exists to close.

        `approved_by` is `self.identity` — `team_audit.actor()`, the SAME source `_stamp_author`
        uses. Deliberately not a second notion of "who": the gate's `WriteRequest.actor` is the
        literal `"memory"` (the subsystem, not a person) and would be a lie in this field. With no
        identity (direct/local construction) it stays "" rather than guessing.

        Degrades to a no-op on the id alone: an unjoinable ledger id leaves `approval_ledger_id`
        None while `approved_by`/`approved_at` still record the decision honestly. Nothing here
        invents an id (`approval_ledger_id_of`)."""
        item.approved_by = self.identity or ""
        item.approved_at = now_iso()
        item.approval_ledger_id = approval_ledger_id_of(ledger_id)

    @staticmethod
    def _pending_approval_seq(led: Any) -> Any:
        """The seq the gate's upcoming `approved` ledger entry will land at, or None with no ledger.

        `len+1` is exact, and MS.S3/B2 is why: the WriteGate holds the ledger's cross-process append
        lock across the commit closure this is called from AND the `approved` record that follows it
        (`govern/gate.py:_ledger_hold`), so no other writer can append in between. B2's own fix —
        `WriteOutcome.approval_seq`, the REAL seq — is not available here and cannot be: it exists
        only after the entry is written, and the item has to be serialised before that. So the
        prediction is used where the real value cannot yet exist, and the tests pin the two to be
        equal rather than trusting the reasoning.

        ONE definition, used by both the team and the local branch below. It used to be inline on
        the team branch only, which meant a local-mode item had no approval id available to stamp
        at all — the gap M-1/R9 closes.

        Degrades on any ledger that cannot be measured (a test stub without `__len__`): None, so a
        write still lands and simply carries no joinable id."""
        if led is None:
            return None
        try:
            return len(led) + 1
        except TypeError:
            return None

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
        write) rides the entry so the deferred flush inherits the original consent (C5/P2).

        M-1/R9 — the approval id is resolved for BOTH modes here and stamped onto the item before
        either branch runs. Two consequences worth stating, because both were previously untrue:

          * a LOCAL-mode item now carries its consent chain too. The id was computed on the `team`
            branch only, so the local path had nothing to stamp — approval provenance would have
            existed for teams and silently not for everyone else.
          * every item ONE approval touches carries the SAME id. Several `_durable_write` calls in
            one commit closure (a supersede writes old + new; a rollback writes item + prior; a
            consolidation writes the whole group) all resolve the same `len+1`, because no ledger
            append happens between them. That matches the journal's own notion of an approval group
            (`team_journal._approval_key`) rather than inventing a per-item one.

        The stamp is applied HERE, on the single fork, rather than at the ~20 call sites — a new
        gated write path inherits it by construction instead of by remembering to."""
        led = ledger if ledger is not None else self._ledger
        ledger_id = self._pending_approval_seq(led)
        self._stamp_approval(item, ledger_id)
        self._record_code_anchors(item, op)
        if team:
            self._journal_team_write(item, ledger_id, op=op, base_revision=base_revision)
        else:
            backend_call()

    def _record_code_anchors(self, item: MemoryItem, op: str) -> None:
        """H-6 — mint the anchor→fingerprint baseline for this item's `about_code` anchors.

        **WHY IT IS HERE, beside `_stamp_approval`, and nowhere else.** M-1/R9 put the approval
        stamp on this single fork for a stated reason — "a new gated write path inherits it by
        construction instead of by remembering to" — and the baseline is the same class of act:
        something that must be true of every gated write and of no ungated one. Bolted onto
        `remember` alone it would leave `promote` · `propose` · the review transitions · `rollback`
        · `heal` · `consolidate` silently baseline-less, and an anchor with no baseline is an
        anchor H-6 has no opinion about (decision #6) — i.e. the feature would be quietly off for
        every path but one.

        **WHAT THE RECORD MEANS: the code as it stood when the human approved the decision.** This
        closure runs if and only if a human's approval licensed this content, which is exactly the
        moment that sentence describes. Minting at the PROPOSE step would record code nobody
        approved anything about; minting on a read path would record code nobody looked at.

        **`refresh` is NOT passed, and that is P7 at the write path.** The record is keyed per
        ANCHOR, so a second decision naming a file someone else already anchored must not advance
        that file's baseline — doing so would silently erase a pending staleness proposal the first
        decision's owner needed to see, the "quietly relabelled as current" failure STALE-REF
        exists to stop. The cost is that a later decision is judged against an earlier decision's
        baseline, so it may propose staleness the moment it lands. That over-proposes rather than
        under-proposes — a proposal changes nothing and is reviewable — and it is filed as
        H-6-ANCHOR-KEYED-PER-FILE (doc 84) with per-(item, anchor) keying as the fix.

        A DELETE is skipped: retiring a fact is not a fresh observation of the code it named.
        Never raises (`record_anchors` swallows) — a bookkeeping failure must not undo a gated
        write a human already approved.
        """
        if op == _OP_DELETE or not getattr(item, "about_code", None):
            return
        root = getattr(self._surface, "root", "") if self._surface is not None else ""
        if not root:
            return                      # direct/local construction: no repo to fingerprint against
        from ..knowledge.anchor_fingerprints import record_anchors
        record_anchors(root, list(item.about_code), layer=self.knowledge_layer)

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
            # M-1/R9 — `remember` was the ONE gated writer that hand-rolled the TM.S5b fork inline
            # instead of calling `_durable_write`, which every other path (promote · promote_scope ·
            # propose · the review transitions · rollback · heal · consolidate) already used. The
            # resolved write is byte-identical — team took `_journal_team_write(item, len+1)` with
            # the default `op=_OP_PUT` / `base_revision=None`, which is exactly what the fork passes,
            # and local took `backend.put(item)`, which is the `backend_call` below.
            #
            # It is routed through the seam now because a duplicate fork is how the seam stops being
            # one: the approval stamp lives in `_durable_write`, so a copy here would have been a
            # durable write that silently carried no consent chain — on the most-used write path in
            # the store.
            self._durable_write(item, op=_OP_PUT, backend_call=lambda: self.backend.put(item),
                                team=team, base_revision=None)
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
        from .intelligence import provenance_block
        verb = {"promote": "PROMOTE", "demote": "DEMOTE"}.get(direction, "CHANGE")
        return (f"mokata · {verb} rule enforcement [{item.governance_kind}] {item.subject}: "
                f"{old} → {new}\nThis changes ONLY the enforcement binding — the rule's condition "
                f"({item.value!r}) is NOT rewritten."
                # R9 — whose rule this is, and on whose approval it currently stands. Making a
                # rule HARD is the highest-leverage change in the store; the reader should know
                # whether they are hardening their own decision or somebody else's.
                + provenance_block(item, label="this rule") +
                "\nNothing changes unless you approve.")

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
        from .intelligence import provenance_block
        return (f"mokata · PROMOTE scope [{item.subject}]: {old} → {new}\nThis WIDENS who can see "
                f"this item (its content is NOT changed)."
                # R9 — widening the audience of content you did not write is the M-2 half of the
                # same risk: the reader is vouching for someone else's item to a bigger room.
                + provenance_block(item, label="this item") +
                "\nNothing changes unless you approve.")

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
    def peek_active(self, mtype: Optional[str] = None, *,
                    scope_context: Any = None) -> List[MemoryItem]:
        """Active items WITHOUT counting a read — for read-only surfaces (the governance
        dashboard, always-on rule injection) that must mutate NO durable state. Same data as
        `all_active`; only the instrumentation differs.

        DB.S2b — `scope_context`, when given AND the backend can push it, sends the broad→narrow
        UNION down as a WHERE so the other scopes' rows are never materialized. It is passed only
        to a backend advertising `supports_scope_pushdown`, which is the backfill-before-filter
        guard: a store whose scope columns aren't yet a faithful projection of its docs never has a
        predicate built on them. Omitting it is always SAFE — the caller's `union_read` filters
        from the doc regardless, so this can only ever change performance, never the result."""
        extra = {}
        if scope_context is not None and getattr(self.backend, "supports_scope_pushdown", False):
            from .scope import scope_path
            # Passed as **kwargs, not as an explicit `scope_path=None`, so a backend that predates
            # the seam (or any third-party one implementing the older three-argument `all`) is
            # called with EXACTLY the signature it has always had. The keyword only ever appears
            # for a backend that advertised it can take it.
            extra["scope_path"] = scope_path(scope_context)
        items = self.backend.all(mtype=mtype, statuses=(ACTIVE,), **extra)
        return [i for i in items if i.mtype in self.enabled_types]

    def all_active(self, mtype: Optional[str] = None, *,
                   scope_context: Any = None) -> List[MemoryItem]:
        self._bump_read()
        return self.peek_active(mtype=mtype, scope_context=scope_context)

    def recall(self, subject: str, mtype: Optional[str] = None) -> List[MemoryItem]:
        # TM.S6 — recall reads the UNION across the scope path (byte-identical when no context).
        return [i for i in self.scoped_active(mtype=mtype) if i.subject == subject]

    def recall_relevant(self, query: str, top_k: int = DEFAULT_TOP_K, semantic: bool = True,
                        graph_scorer: Any = None, *, stamp: bool = True,
                        kinds: Optional[Sequence[str]] = None,
                        degrade_out: Optional[Callable[[str], None]] = None) -> List[Any]:
        """Stage 35e — tiered, by-relevance retrieval (lexical floor + optional graph +
        semantic when an embedder is wired), fused + ranked, top-k only (frugal). Returns
        a list of RetrievalHit. Degrades to lexical when the semantic/graph tiers are off.

        Stage 35f: the graph tier is now LIVE BY DEFAULT — when no explicit graph_scorer is
        passed and a knowledge layer is wired, build one from the code graph. It silently
        contributes nothing on the grep floor (no real graph ⇒ scorer is None), so
        lexical+semantic always hold.

        DB.S5: the fusion gained a recency + usage term (both 0 for an item with no telemetry, so
        a store without the v4 columns ranks identically), and the hits this call RETURNS are
        stamped as recalled. The stamp happens LAST, after the answer exists, and cannot fail it
        (`record_usage` raises nothing).

        DB.S7b (K1): the fusion gained a bounded ≤2-hop EXPANSION term over the typed edges, wired
        by the same route the graph tier already uses. It contributes exactly 0.0 for every item
        when no edge reached it — which is every item on a store with no edge table, on an
        un-migrated v4 team, and whenever `memory.edge_expansion` is off — so those three cases
        rank byte-identically to pre-DB.S7b.

        JIT-STAMP-SEAM (2026-08-01) — `stamp` and `kinds`, both defaulting to today's behaviour.

        `stamp=False` is THE seam this method exists to offer, and it is made here at the STORE
        rather than at the caller on purpose: suppressing the write by monkeypatching, or by a flag
        on `brain`, would leave this method's own contract saying one thing and doing another. It
        governs BOTH instrumentation writes on this path, because they are one category — "record
        that a recall happened":

          * `record_usage` (DB.S5 recency/usage telemetry), and
          * `_bump_read` inside `hydrate_candidates` / `scoped_active`, which feeds the read/write
            ratio `/mokata:govern` surfaces.

        Stamping only one of them would be worse than stamping neither: the per-turn injection
        fires on EVERY prompt, so a counted read per turn turns `memory_stats.reads` into a count
        of TURNS (JIT-RECALL-COUNTS-A-READ, one layer down) even with the usage write suppressed.

        WHAT `stamp=False` MEANS FOR DB.S5's TELEMETRY, decided and recorded rather than left for a
        reader to infer: **`hit_count` counts times an item was surfaced to a HUMAN who asked, and
        that is now a narrower and more useful claim than "times it was returned by the ranker".**
        Automatic injection is not a recall the user asked for — it is mokata OFFERING context —
        and letting it stamp would make the two most-injected kinds (`context`, `reference`) the
        most-recalled items on the store by construction, on every turn, regardless of whether
        anyone read them. That would then feed BACK into the ranking through DB.S5's usage term:
        an item injected because it ranked highly would rank more highly because it was injected.
        Suppressing the stamp keeps the signal exogenous. The cost is that the telemetry is blind
        to the injection channel, which is real and is why this is a documented decision rather
        than an implementation detail; when a per-channel counter is wanted it is a NEW column
        (`injected_count`), never this one silently widened.

        `kinds` restricts the result to those `effective_kind`s — what `jit_recall` has always
        needed and could not ask for. Enforced in `tiered_recall` over the resolved set; the SQL
        predicate that rides along is an optimization over the same rule, never a second one.

        `degrade_out` redirects the tiers' degrade notices, which previously had no way through
        this method and therefore always reached stderr. The per-turn injection needs it: a tier
        notice is written for someone who ran a recall and got a worse answer than they asked for,
        and on a hook that fires every prompt the SAME notice would print on every turn — the one
        case `bootstrap.build_injection` already documents as "announcing it buys nothing and
        trains the user to ignore the channel". Default `None` is unchanged (stderr, once per
        subsystem), so every existing caller keeps its notices."""
        from .tiered import tiered_recall
        if graph_scorer is None and self.knowledge_layer is not None:
            try:
                from ..knowledge import make_graph_scorer
                graph_scorer = make_graph_scorer(self.knowledge_layer, query)
            except Exception:
                graph_scorer = None
        hits = tiered_recall(self, query, embedder=self.embedder,
                             graph_scorer=graph_scorer, top_k=top_k, semantic=semantic,
                             expander=self._edge_expander(), kinds=kinds, count_read=stamp,
                             degrade_out=degrade_out)
        # Only the TOP-K that were actually returned are stamped — the signal being recorded is
        # "this item was surfaced to someone", not "this item was considered by the ranker", and
        # every active item is considered on every recall. Counting candidates would make
        # `hit_count` a measure of how often recall RAN, which carries no information at all.
        if stamp:
            self.record_usage([h.item.id for h in hits])
        return hits

    # --- DB.S7b (K1) edge expansion -----------------------------------------
    def edge_expansion_enabled(self) -> bool:
        """Is the ≤2-hop expansion live? **ON BY DEFAULT** (doc 84 K1, doc 55:78 — "behind a config
        flag ON by default"), read from `settings.memory.edge_expansion`.

        The key lives in the SAME `memory` settings dict that already carries non-type keys
        (`memory.embedder` at `:260`, `memory.category` in `selection.py:239`) rather than a new
        section — and that is safe by construction, because `enabled_memory_types` above reads only
        keys named after a MEMORY TYPE (`settings.get(t, True)` for `t in MEMORY_TYPES`), so a
        non-type key added here can never be mistaken for a type toggle.

        Absent surface / absent manifest ⇒ ON, matching the default and matching every other
        zero-config path here: a store built without a surface (a test double, an embedded caller)
        gets mokata's default behaviour, not a silently different one.
        """
        surface = getattr(self, "surface", None)
        manifest = getattr(surface, "manifest", None)
        if manifest is None:
            return True
        return bool((manifest.setting(MEMORY_SETTINGS_KEY, {}) or {}).get("edge_expansion", True))

    def _edge_expander(self) -> Optional[Any]:
        """The backend's `expand_from` seam, or `None` when the tier must not run.

        `None` in THREE cases, and all three must produce a byte-identical pre-DB.S7b ranking:
        the config is off; the backend has no `expand_from` at all (Obsidian's files, the native
        client, any third-party adapter); or — for the shared store — the team is still on v4 and
        `expand_from` itself answers `[]`. Capability-PROBED with `getattr`, never assumed, exactly
        as `lexical_search` / `record_usage` / `usage_stats` are probed above.
        """
        if not self.edge_expansion_enabled():
            return None
        return getattr(self.backend, "expand_from", None)

    def get(self, item_id: str) -> Optional[MemoryItem]:
        return self.backend.get(item_id)

    # --- DB.S5 usage telemetry (transient run-state, D5) ---------------------
    def usage_signals(self, item_ids: Any) -> Dict[str, "lifecycle.UsageSignal"]:
        """The `{id: UsageSignal}` telemetry for `item_ids` — READ-ONLY, and it writes nothing.

        Degrade-clean in the same direction as everything else on a read path: a backend with no
        usage columns (Obsidian, native, any third-party adapter), a v3 shared store that has not
        been migrated, or a driver error all return `{}` — and an absent signal is the ZERO signal,
        which the fusion treats as "no recency, no usage" and therefore ranks exactly as it did
        before this stage. There is no arrangement of failures in which missing telemetry can
        change a result rather than merely fail to improve it.
        """
        ids = [str(i) for i in item_ids if i]
        reader = getattr(self.backend, "usage_stats", None)
        if reader is None or not ids:
            return {}
        try:
            raw = reader(ids)
        except Exception:
            # LEGITIMATE SUPPRESS, and the reasoning is the same one `lexical_tier` carries: this
            # spans a psycopg driver error (an OPTIONAL extra, not nameable at module scope), a
            # sqlite3 error on a store mid-migration, and a third-party adapter's own classes.
            # There is nothing to degrade TO and nothing for a user to act on — the signal is a
            # RANKING BOOST, not a result. Losing it costs the pre-DB.S5 ranking, which is a
            # correct ranking. Announcing it would be a notice about a feature getting no better.
            return {}
        return {k: lifecycle.UsageSignal(hits=int(v[0] or 0), last_recalled_at=v[1])
                for k, v in (raw or {}).items()}

    def record_usage(self, item_ids: Any, now: Optional[str] = None) -> bool:
        """Record that `item_ids` were just recalled: bump `hit_count`, set `last_recalled_at`.

        THE ONE DEGRADE-CLEAN SEAM for usage telemetry, and the reason it is a seam at all: this
        is a WRITE riding a READ, so the failure mode it must never have is turning a recall into
        something that can fail. It returns True/False and raises NOTHING — the caller in
        `recall_relevant` cannot be broken by it, whatever the backend does.

        Why it is ungated, stated plainly rather than assumed (P2 is not being bent here): this is
        transient RUN-STATE, not a governed durable write. It touches no content a human approved —
        not the `doc`, not the value, not the provenance, not the validity window — only two
        counter columns that exist solely to rank. There is nothing to review, nothing to secret-
        scan (the write contains no user content, just an integer and a clock reading), and nothing
        an approval could meaningfully say yes or no to. It is registered as such in the SI.6
        zero-bypass audit rather than being left for someone to discover.

        In TEAM mode it deliberately does NOT journal. The team journal exists to CAS-guard
        contended writes to approved content; a monotonic counter has no conflict to resolve (two
        seats each recalling an item should produce two hits, which is exactly what two increments
        produce) and routing telemetry through the approval-carrying journal would put un-approved
        rows in an approval ledger.
        """
        ids = [str(i) for i in item_ids if i]
        if not ids or not hasattr(self.backend, "record_usage"):
            return False
        try:
            # Called as `self.backend.record_usage(...)` rather than through a local alias
            # DELIBERATELY: the SI.6 zero-bypass audit finds writers by scanning for exactly this
            # shape, and a writer routed through `writer = getattr(...)` would be INVISIBLE to it.
            # A new durable-ish write that the audit cannot see is the precise failure mode that
            # audit exists to prevent, so this one is written to be found and is registered in
            # UNGATED_BY_DESIGN with the D5 reasoning above.
            self.backend.record_usage(ids, now or now_iso())
            return True
        except Exception:
            # The whole point of this method. A failed telemetry write is SWALLOWED — the recall it
            # rode has already produced its answer, and that answer is not made wrong by a counter
            # that did not move. Broad by necessity (driver / sqlite3 / third-party adapter, as
            # above) and silent by design: this fires on a read-only store, a v3 team schema, or a
            # locked file, none of which the user needs to be told about mid-recall.
            return False

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
        return detect_issues(active, now=now, conflicts=self._cross_writer_conflicts(),
                             anchor_staleness=self._moved_code_anchors(active))

    def _moved_code_anchors(self, active: List[MemoryItem]) -> List[Any]:
        """H-6 S3 — the MOVED `about_code` anchors as plain `AnchorStaleness` records.

        Computed HERE, at the store's own boundary, for the reason `_cross_writer_conflicts` is:
        `healing.py` is an L2 domain module and must not acquire a filesystem read or a code-graph
        client on a detection path. It gets verdicts; it does not gather them.

        Empty without a surface (no root to resolve anchors against) — so a directly-constructed
        store, and every pre-H-6 caller, is byte-identical. The knowledge layer is passed THROUGH
        rather than built: with no adopted graph, symbol anchors decline, which is the anchor-shape
        split's own rule and not a degradation of it.

        Read-only (P1): it hashes files and reads a JSON record, and bumps no read counter — the
        same posture `peek_active` + `detect_issues` already hold on this path.
        """
        if self._surface is None:
            return []
        try:
            root = getattr(self._surface, "root", "")
            if not root:
                return []
            from ..knowledge.anchor_fingerprints import (ANCHOR_SCAN_CAP, evaluate_anchors,
                                                         read_record)
            from .healing import AnchorStaleness
            record = read_record(root)
            if not record:
                return []                      # no baselines ⇒ no opinion, and no file hashing
            out: List[Any] = []
            budget = ANCHOR_SCAN_CAP
            for item in active:
                anchors = [a for a in (item.about_code or []) if a][:budget]
                if not anchors:
                    continue
                budget -= len(anchors)
                for v in evaluate_anchors(anchors, root=root, layer=self.knowledge_layer,
                                          record=record):
                    if v.moved:
                        out.append(AnchorStaleness(item=item, anchor=v.anchor, shape=v.shape,
                                                   path=v.path))
                if budget <= 0:
                    break
            return out
        except Exception as exc:  # noqa: BLE001
            # D5 — LOUD. This arm is the ONLY thing that would tell a human the code under a
            # decision moved; if it silently answers "nothing moved" they read a clean governance
            # view and conclude their anchors are current. The detection still proceeds without it
            # (the other arms are unaffected), so this degrades rather than raises.
            note_degraded("memory-code-anchors", FAILURE_LOCAL_IO,
                          detail=str(exc),
                          fallback="moved `about_code` anchors are not surfaced this run",
                          fix="run `mokata doctor`; the anchor record lives under "
                              ".mokata/temp_local/anchor_fingerprints/ and is rebuilt if removed")
            return []

    def cross_writer_proposals(self) -> List[HealingProposal]:
        """Just the CROSS_WRITER proposals — the same objects `detect_issues` embeds, without the
        backend scan the item-level arms need.

        This is the public seam `mokata sync` resolves through (R4): it asks the human, looks the
        conflict up here by `conflict_id`, and hands it to `apply_proposal`. Read-only."""
        from .healing import detect_cross_writer
        return detect_cross_writer(self._cross_writer_conflicts())

    def _cross_writer_conflicts(self) -> List[Any]:
        """DB.S6/I2a — the surfaced CAS conflicts as plain `ConflictRecord`s, so a teammate's
        concurrent change reaches every surface that already renders `detect_issues` (the
        governance view, `mokata memory`, the health nudge, the MCP proposal tool) WITHOUT anyone
        running `mokata sync`. Empty in local/zero-config mode, which is why the local path stays
        byte-identical and consults no journal at all.

        Read-only: it writes nothing (I6), and it never raises — a broken journal degrades to "no
        conflicts" LOUDLY, because silence here is the exact failure this arm exists to remove."""
        if self._surface is None or not self._team_mode():
            return []
        try:
            # DB.S7c1 — the edge context is attached HERE, after the collab projection and before
            # the detector sees it, so both `detect_issues` and `cross_writer_proposals` get it
            # from one place and cannot drift into showing different evidence for one conflict.
            return self._attach_subgraph(
                list(self._resolve_team_writer().conflicts(self._surface)))
        except OSError as exc:
            # The ONLY raisable class: the projection opens the journal file (a torn JSON line is
            # already skipped by the replay, and an unreadable DOC degrades to `remote=None`
            # inside the projection). A locked/permission-broken `.mokata/temp_local/` is the real
            # case — and it would otherwise read as "no conflicts", i.e. an approved write that
            # never landed, silently. Say so.
            note_degraded("memory-conflicts", FAILURE_LOCAL_IO,
                          fallback="cross-writer conflicts are NOT being surfaced",
                          fix="check permissions on `.mokata/temp_local/`, then run `mokata sync`",
                          detail=f"{type(exc).__name__}: {exc}")
            return []

    # --- DB.S7c1 (K2) edge-aware healing -------------------------------------
    def _attach_subgraph(self, records: List[Any]) -> List[Any]:
        """K2 — give each conflict the OPEN relations a resolution would re-project.

        THIS is the boundary the edge read belongs on, and the placement is the whole reason the
        DB.S6 arm stayed additive: `memory/healing.py` owns what a conflict MEANS and opens no
        connection; `team_writer` owns the collab projection and knows no backend; the store owns
        the backend, so the store is the only object that can answer "what edges does the shared
        graph hold for this id". Each layer answers the question it is the only one able to answer.

        **`open_edges` finally has its production consumer**, which DB.S7a provisioned and DB.S7b
        explicitly declined to become (`02:231`). It is the right read here for a reason DB.S7b's
        traversal is not: `project_edges` maintains the projection keyed on `src_id`, so the point
        read over one src returns EXACTLY the set a resolution rewrites. The ≤2-hop walk would
        return that set plus a hop of context that resolving this conflict does not touch.

        **NOT gated on `settings.memory.edge_expansion`**, deliberately, and this is a judgement
        worth naming rather than leaving as an omission. That flag governs whether hops may
        influence RANKING — a relevance question. This is evidence attached to a human decision at
        a gate, and a user who turned off retrieval expansion has not asked to be shown less of
        what they are about to overwrite. Coupling the two would make a perf/relevance preference
        silently withhold information at the one surface where withholding it changes an outcome.

        Capability-PROBED, never assumed (`getattr`), exactly as `expand_from` / `lexical_search` /
        `usage_stats` are: a backend with no edge table — Obsidian's files, the native client, any
        third-party adapter, an un-migrated v4 team — yields records with no subgraph, and those
        render as the pre-K2 proposal rather than as an error.
        """
        read = getattr(self.backend, "open_edges", None)
        if read is None or not records:
            return records
        from .expansion import MAX_WALKED_EDGES
        from .healing import prune_subgraph
        visible = self._subgraph_visible()
        for rec in records:
            try:
                rec.subgraph = prune_subgraph(read(rec.local.id), visible, MAX_WALKED_EDGES)
            except Exception as exc:  # noqa: BLE001
                # D5 — the subgraph is CONTEXT on a conflict, and the conflict itself is the thing
                # the user must not lose. A failed edge read therefore degrades to "no subgraph"
                # and the conflict still surfaces, because dropping a CAS conflict to protect a
                # decoration would invert the priority this whole arm exists to set. LOUD, though:
                # silently showing no relations is indistinguishable from an item that genuinely
                # has none, and the second is the common case — so a human would read a broken
                # read as reassurance. BROAD for the same reason the sibling handlers here are: it
                # spans a psycopg driver error (an OPTIONAL, lazily imported extra not nameable at
                # module scope), a sqlite3 error on a store mid-migration, and any third-party
                # adapter's own classes.
                note_degraded("memory-subgraph", failure_class_of(exc) or FAILURE_LOCAL_IO,
                              fallback="conflict shown WITHOUT the relations it re-projects",
                              fix="run `mokata doctor` to check the memory store",
                              detail=f"{type(exc).__name__}: {exc}")
                rec.subgraph = None
        return records

    def _subgraph_visible(self) -> Optional[set]:
        """The ids this identity may read, or None for "no scope context".

        `peek_active` + the SHARED `_visible_filter` — not `scoped_active`, and the difference is
        a P1 contract rather than a micro-optimization: `scoped_active` COUNTS a read, so using it
        here would make merely LOOKING at a conflict bump `memory_stats.reads` and write the
        counter to disk. Detection would then no longer be pure, and P1 ("a detect run writes
        NOTHING") would be false — which is exactly how the propose-only test caught it.

        Visibility itself is still defined in ONE place; only the instrumentation differs — which
        is now `peek_visible_active`, the named non-counting twin this call used to open-code."""
        try:
            return {i.id for i in self.peek_visible_active()}
        except Exception:  # noqa: BLE001
            # D5 — SUPPRESS-adjacent but deliberately fail-CLOSED-ish: an unreadable scope set must
            # not become "no scope context", because None prunes NOTHING and would show every dst
            # id to an identity whose visibility we just failed to establish. Returning an empty
            # set prunes every item-target edge instead — the subgraph degrades to code anchors
            # only, which discloses nothing. The conflict itself is unaffected and the sibling
            # handler above already announces a broken store read.
            return set()

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
        and edit are human-gated. NEVER auto-rewrites.

        DB.S6/R4 — this is also the ONE resolver for a cross-writer conflict. `mokata sync` no
        longer settles conflicts itself: it asks, then calls this method, so there is exactly one
        implementation of "what happens when two writers disagree" and both entry points provably
        converge on the same end state."""
        if decision not in ("approve", "edit", "reject", "defer", "discard"):
            raise MemoryError(f"unknown decision '{decision}'")
        if p.kind == CROSS_WRITER:
            return self._resolve_cross_writer(p, decision, confirm=confirm,
                                              assume_yes=assume_yes, policy=policy)
        if decision == "discard":
            raise MemoryError("'discard' resolves a cross-writer conflict only "
                              f"(this proposal is '{p.kind}')")
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

    def _resolve_cross_writer(self, p: HealingProposal, decision: str, *,
                              confirm: Optional[Callable[[str], bool]] = None,
                              assume_yes: bool = False,
                              policy: Any = None) -> HealingResult:
        """R4 — the ONE place a cross-writer conflict is settled.

        THREE outcomes, and the mapping is deliberate:

          * `approve` — keep YOURS. The local write is re-queued at the CURRENT remote revision, so
            the next flush's CAS lands it over the teammate's row. An explicit overwrite, chosen.
          * `discard` — keep THEIRS. The local write is dropped. This needed its own word rather
            than riding `reject`, because `reject` is what every safe default in this codebase
            falls back to (an EOF, a non-interactive prompt, a dismissed MCP consent) — and a
            default that DISCARDS an approved write is precisely the silent loss this stage exists
            to make unreachable.
          * `reject` / `defer` — leave it conflicted. Nothing is lost, nothing is decided, and the
            proposal is surfaced again by the next `detect_issues` (I2a).

        `edit` is refused rather than half-implemented: a merged value is a NEW write with its own
        provenance, not a resolution of this one, and pretending otherwise would let a merge slip
        in without the CAS the merged row still needs. DB.S7/K2 can add it as its own kind.

        Not counted by `_bump_write`: the write was already counted when the human approved it.
        Where it finally lands is not a second write, and double-counting would skew the C8 ratio
        that the unused-memory nudge reads."""
        if decision == "edit":
            self._record_healing(p, decision, changed=False)
            return HealingResult(
                changed=False, aborted=True,
                message=("a cross-writer conflict cannot be resolved by editing in place — "
                         "approve (keep yours) / discard (keep theirs) / defer, then remember the "
                         "merged value as its own gated write"))
        if decision in ("reject", "defer"):
            self._record_healing(p, decision, changed=False)
            return HealingResult(changed=False,
                                 message=f"{decision}: the conflict is still open (nothing lost)")
        if not p.conflict_id:
            return HealingResult(changed=False, aborted=True,
                                 message="this proposal carries no conflict handle to resolve")

        from .team_writer import KEEP_LOCAL, KEEP_REMOTE
        keep = KEEP_LOCAL if decision == "approve" else KEEP_REMOTE
        # What the gate secret-scans. Keeping YOURS re-publishes your value to the shared row, so
        # the value is the untrusted content; keeping THEIRS publishes nothing new, so — exactly as
        # in the STALE branch above — the subject alone is the content.
        content = (f"{p.old.subject}\n{p.old.value}" if keep == KEEP_LOCAL else p.old.subject)
        writer, surface = self._resolve_team_writer(), self._surface

        # DB.S6/I1b — the group guard, BEFORE the gate for the same reason `_downgrade_refusal` is:
        # a resolution mokata will not commit must not cost the human an approval prompt, and
        # nothing may be written on the way to refusing. I1 made the FLUSH atomic; this closes the
        # other half — a rolled-back approval surfaces as N separate conflicts, and settling them
        # one at a time can still retire a fact whose replacement is being discarded or deferred.
        refusal = writer.group_refusal(surface, p.conflict_id, keep)
        if refusal is not None:
            self._record_healing(p, decision, changed=False)
            return HealingResult(changed=False, aborted=True, refused=True, message=refusal)

        def _commit() -> None:
            writer.resolve_conflict(surface, p.conflict_id, keep,
                                    remote_revision=p.remote_revision)

        outcome = self._gated_commit(p.subject, content, _commit, self.render_proposal(p),
                                     confirm=confirm, assume_yes=assume_yes, policy=policy)
        self._record_healing(p, decision, changed=outcome.committed)
        if outcome.committed:
            if keep == KEEP_LOCAL:
                # Re-queued at the remote revision — flush it now so "kept mine" actually means the
                # shared row says mine. Offline it stays journaled (work-locally, nothing lost).
                self._best_effort_flush()
            return HealingResult(
                changed=True, item=(p.old if keep == KEEP_LOCAL else p.new),
                message=("kept yours — re-queued over the remote row" if keep == KEEP_LOCAL
                         else "kept theirs — your local write was discarded"))
        if outcome.findings:
            return HealingResult(changed=False, aborted=True, blocked=True,
                                 message="blocked: secret detected — the conflict is still open")
        return HealingResult(changed=False, aborted=True,
                             message="declined at the human gate — the conflict is still open")

    # --- DB.S7d: the one-prompt group decision -------------------------------
    def cross_writer_group(self, p: HealingProposal) -> List[HealingProposal]:
        """The still-conflicted CROSS_WRITER proposals sharing `p`'s approval, in journal order.

        Read-only, and it returns `[p]` for a conflict with no approval group — a solo conflict is
        a group of one, so every caller can treat the group as the unit without first asking
        whether there is one."""
        if p.kind != CROSS_WRITER or not p.conflict_id:
            return [p]
        by_conflict = {q.conflict_id: q for q in self.cross_writer_proposals()}
        writer = self._resolve_team_writer()
        members = [by_conflict[cid]
                   for cid in writer.group_members(self._surface, p.conflict_id)
                   if cid in by_conflict]
        return members or [p]

    def apply_group_decision(self, p: HealingProposal, decision: str,
                             confirm: Optional[Callable[[str], bool]] = None,
                             assume_yes: bool = False, policy: Any = None) -> HealingResult:
        """DB.S7d — settle a WHOLE approval in ONE prompt (`02:231`, deferred here from DB.S6/I1b).

        WHAT THIS FIXES. A rolled-back approval surfaces as N separate conflicts, and until now the
        only way to settle them was one prompt at a time. That is not merely tedious: it is the
        source of both half-decided end states the two guards refuse — retire-without-replace
        (a fact lost) and duplicate-both-active (a fact doubled). Deciding the approval as a unit
        makes both unreachable through this path by construction: every member gets the SAME
        verdict, so no member can strand or duplicate another.

        "BY CONSTRUCTION" IS NOT ENOUGH, AND THAT IS THE LOAD-BEARING PART. The uniform-verdict
        argument holds only for members still CONFLICTED. A member settled in an earlier
        one-at-a-time pass is outside this group's reach entirely — discard the replacement
        yesterday, and today's "keep local for the whole approval" has exactly one member left to
        decide, lands the retirement, and the fact is gone in a single prompt the human trusted
        BECAUSE it claimed to cover everything. So this does not reason about safety itself: it
        hands the projected end state to `retire_without_replace_refusal` and
        `duplicate_both_active_refusal` — the same shipped functions the single-member path runs —
        and refuses if either does.

        ORDER: refuse BEFORE the gate, exactly as `_resolve_cross_writer` and `_downgrade_refusal`
        do. A decision mokata will not commit must not cost the human an approval prompt, and
        nothing may be written on the way to refusing.

        ATOMIC: the members' resolutions reach the journal in ONE append (`resolve_group`), so a
        crash cannot leave the approval half-settled on disk. Combined with the pre-gate refusal,
        the group is all-or-nothing at both ends — nothing is written when it refuses, and what it
        does write lands together."""
        if p.kind != CROSS_WRITER:
            raise MemoryError("apply_group_decision resolves a cross-writer conflict only "
                              f"(this proposal is '{p.kind}')")
        if decision not in ("approve", "discard", "reject", "defer"):
            raise MemoryError(f"unknown group decision '{decision}'")
        if decision in ("reject", "defer"):
            self._record_healing(p, decision, changed=False)
            return HealingResult(changed=False,
                                 message=f"{decision}: the approval is still open (nothing lost)")
        if not p.conflict_id:
            return HealingResult(changed=False, aborted=True,
                                 message="this proposal carries no conflict handle to resolve")

        from .team_writer import KEEP_LOCAL, KEEP_REMOTE
        keep = KEEP_LOCAL if decision == "approve" else KEEP_REMOTE
        members = self.cross_writer_group(p)
        writer, surface = self._resolve_team_writer(), self._surface
        decisions = [(m.conflict_id, keep, m.remote_revision) for m in members]

        refusal = writer.group_decision_refusal(surface, decisions)
        if refusal is not None:
            self._record_healing(p, decision, changed=False)
            return HealingResult(changed=False, aborted=True, refused=True, message=refusal)

        # Same content rule as the single-member path: keeping YOURS re-publishes every member's
        # value to the shared rows, so all of them are the untrusted content; keeping THEIRS
        # publishes nothing new, so the subjects alone are. The secret-scan covers the whole group
        # for the same reason the gate does — one decision, one scan of everything it would push.
        content = "\n".join(f"{m.old.subject}\n{m.old.value}" if keep == KEEP_LOCAL
                            else m.old.subject for m in members)

        def _commit() -> None:
            writer.resolve_group(surface, decisions)

        outcome = self._gated_commit(p.subject, content, _commit,
                                     self.render_group_decision(members, decision),
                                     confirm=confirm, assume_yes=assume_yes, policy=policy)
        self._record_healing(p, decision, changed=outcome.committed)
        if outcome.committed:
            if keep == KEEP_LOCAL:
                self._best_effort_flush()
            return HealingResult(
                changed=True, item=p.old,
                message=(f"kept yours for all {len(members)} writes in this approval — re-queued "
                         f"over the remote rows" if keep == KEEP_LOCAL else
                         f"kept theirs for all {len(members)} writes in this approval — your local "
                         f"writes were discarded"))
        if outcome.findings:
            return HealingResult(changed=False, aborted=True, blocked=True,
                                 message="blocked: secret detected — the approval is still open")
        return HealingResult(changed=False, aborted=True,
                             message="declined at the human gate — the approval is still open")

    @staticmethod
    def render_group_decision(members: List[HealingProposal], decision: str) -> str:
        """The ONE prompt. It NAMES every member it would decide — a single prompt that hides its
        own blast radius is worse than the N honest prompts it replaces, because the human is
        approving N durable writes and has to see N durable writes."""
        side = "YOUR version" if decision == "approve" else "THEIR version"
        lines = [f"mokata · resolve this whole approval — {len(members)} conflicted write(s) — "
                 f"by keeping {side}:"]
        for m in members:
            theirs = "unreadable" if m.new is None else repr(m.new.value)
            lines.append(f"  · {m.old.id} ({m.old.subject}): yours {m.old.value!r} vs {theirs}")
        lines.append("They are decided together, or not at all. Nothing is written unless you "
                     "approve.")
        return "\n".join(lines)

    # --- consolidation (C7, PROPOSAL-ONLY + gated apply) --------------------
    def propose_consolidations(self, ledger: Any = None,
                               drafter: Optional[SummaryDrafter] = None
                               ) -> List[ConsolidationProposal]:
        """Surface consolidation proposals (merge/summarize/prune). Reads only — never
        writes. Each proposal is logged to the ledger.

        M-4/R5 — `drafter` is the injected summary writer (the harness agent, via the propose
        flow). It is threaded straight through to `propose_consolidations` and used for nothing
        else; it can only affect a SUMMARIZE proposal's drafted VALUE, and that value then rides
        the identical secret-scan → human gate → ledger → commit path in `apply_consolidation` that
        the placeholder rode. Omitted (the default, and every existing caller) the behaviour is
        byte-identical to the pre-M-4 build."""
        led = ledger if ledger is not None else self._ledger
        active = [i for i in self.backend.all(statuses=(ACTIVE,))
                  if i.mtype in self.enabled_types]
        stale = [i for i in self.backend.all(statuses=(STATUS_STALE,))
                 if i.mtype in self.enabled_types]
        proposals = propose_consolidations(active, stale, drafter=drafter)
        if led is not None:
            for p in proposals:
                led.record("consolidation_proposal", op=p.kind, subject=p.subject,
                           mtype=p.mtype, count=len(p.olds))
        return proposals

    def propose_archival(self, ledger: Any = None, now: Optional[str] = None
                         ) -> List[ConsolidationProposal]:
        """DB.S5 — surface the size-budget sweep's archival proposals. Reads only; writes nothing
        and evicts nothing. Each proposal is logged, and applying one is human-gated exactly like
        every other consolidation (`apply_consolidation`).

        Counts only the ACTIVE items whose validity window is still OPEN — an already-archived,
        superseded or stale item has left the working set and must not be counted against the
        budget that governs it, or a store would stay permanently "over budget" on the strength of
        items it already retired.
        """
        led = ledger if ledger is not None else self._ledger
        active = [i for i in self.backend.all(statuses=(ACTIVE,))
                  if i.mtype in self.enabled_types and lifecycle.is_open(i)]
        usage = self.usage_signals([i.id for i in active])
        proposals = propose_archival(active, usage, now)
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
                # THE `derives_from` PRODUCER (2026-08-01). A SUMMARIZE lands a NEW item distilled
                # out of `p.olds`, and this is the only place in the codebase that knows which
                # items those were — so it is the only place the lineage can be recorded without
                # inventing it. Written on `keep` (which is `edited or p.new`, so a human who
                # rewrote the summary still gets the lineage of what it summarized) BEFORE the
                # durable write, because the edge row is a projection OF the persisted field: set
                # it afterwards and the field would be right while the edge table stayed empty.
                #
                # Idempotent and additive, in that order: re-applying the same proposal must not
                # duplicate ids, and a caller that pre-populated the list keeps what it set.
                for o in p.olds:
                    if o.id != keep.id and o.id not in keep.derives_from:
                        keep.derives_from.append(o.id)
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
            elif p.kind == ARCHIVE:
                # DB.S5 — THE NEVER-DELETE PATH, and the contrast with the PRUNE branch directly
                # above is the point. PRUNE issues `_OP_DELETE`; ARCHIVE issues `_OP_UPDATE` on
                # every item and touches nothing but the status and the validity window. The row,
                # the value, the provenance and every approved field survive, so an archival is
                # reversible in a way a prune is not — which is what makes it safe to let a size
                # heuristic propose it at all.
                for o in p.olds:
                    lifecycle.close_window(o, now_iso())
                    o.status = ARCHIVED
                    self._durable_write(o, op=_OP_UPDATE, base_revision=self._base_revision(o),
                                        backend_call=lambda o=o: self.backend.update(o),
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
