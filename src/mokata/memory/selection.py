"""Backend selection & store-composition resolution — the deprecated-backend branches in ONE file.

Extracted from `memory/store.py` (PRE-SIMP, release 0.0.15) so the 0.0.17 SIMP.S3 removal of the
deprecated channels (Obsidian / native-memory backends, and the detection/scope chains around them)
is a near one-file diff rather than surgery inside the store. Everything here is the backend
build/select/scope/identity resolution that used to be module-level in store.py; `store.py` keeps a
re-export shim so every existing `from .store import build_backend` / `from ..memory.store import X`
caller works unchanged.

The SIMP.S3 deletion set lives HERE: `_select_raw_backend`'s `obsidian` and `native-memory` branches
are the deprecated-backend selection the removal deletes — SQLite is the guaranteed floor and Postgres
(+vector) is the one remote store that stays. Removing them is a change to this file alone.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from .. import TEMP_LOCAL_DIRNAME
from ..manifest import ManifestError
from .backends import (
    MemoryBackend,
    NativeMemoryBackend,
    ObsidianBackend,
    SQLiteBackend,
    build_postgres_backend,
)

MEMORY_DIRNAME = "memory"


# -------------------------------------------------------------- backend selection
def _overlay_for_team(backend: MemoryBackend, routing: Any, root: str) -> MemoryBackend:
    """CM.S3 (C-3) — in TEAM mode, wrap `backend` in the read-your-writes `JournalOverlay` so reads
    merge the pending journaled-but-unflushed team writes over the backend. Applies whether the read
    is served by the live shared backend or the degraded LOCAL floor — a just-approved write is
    visible either way. LOCAL mode / no routing (from_router / migrate — no surface) returns the
    backend UNWRAPPED: the zero-network local hot path is byte-identical and consults no journal."""
    from .. import run_mode as _rm
    if routing is None or getattr(routing, "mode", _rm.LOCAL) != _rm.TEAM:
        return backend
    # The SAME path TeamJournal.for_surface(surface) resolves (root == surface.mokata_dir), so the
    # overlay reads exactly the journal the team-write path appends to.
    from ..team_journal import JOURNAL_FILENAME, TeamJournal
    from .overlay import JournalOverlay
    journal = TeamJournal(os.path.join(root, TEMP_LOCAL_DIRNAME, JOURNAL_FILENAME))
    return JournalOverlay(backend, journal)


def build_backend(tool: str, root: str,
                  clients: Optional[Dict[str, Any]] = None,
                  config: Optional[Dict[str, Any]] = None,
                  project: Optional[str] = None,
                  routing: Any = None) -> MemoryBackend:
    """Build the backend the router resolved to, honoring the tool's per-tool `config`
    (Stage 24A) and degrading to the SQLite floor when a chosen backend needs external
    wiring that isn't present. `config` is the manifest's `tools.<id>.config` block;
    defaults are unchanged when it's absent. `project` (Stage 71a) SCOPES the shared
    Postgres backend to the current project; None spans all (review). Local SQLite/Obsidian
    are already per-repo and ignore it.

    CM.S2 (C-2): `routing` is the ONE read-routing decision (`degrade.resolve_read_routing`).
    For the shared Postgres tool it GATES the choice on the SAME cached E2 health verdict the
    badge/doctor use — a troubled verdict routes straight to the LOCAL floor (no redundant
    connect) and the fall-back is marked degraded on `routing`, so a team-mode read served
    locally is never silent. When `routing` is None (from_router / migrate — no surface) the
    behaviour is byte-identical to before (the local, non-team path).

    CM.S3 (C-3): in TEAM mode the resolved backend is wrapped ONCE, at this boundary, in the
    read-your-writes `JournalOverlay` — so a journaled-but-unflushed team write is visible to the
    very next read. This is applied for WHATEVER team mode resolved to (the live shared backend OR
    the degraded local floor), because the journal-first write path activates on team MODE, not on
    a particular tool. LOCAL / no routing returns the raw backend UNWRAPPED (byte-identical
    zero-network hot path; the journal is never consulted). This RETIRES the B3 read-through
    cache (D7): stores are per-call and the overlay supplies coherence."""
    backend = _select_raw_backend(tool, root, clients or {}, config or {}, project, routing)
    return _overlay_for_team(backend, routing, root)


def _select_raw_backend(tool: str, root: str, clients: Dict[str, Any], config: Dict[str, Any],
                        project: Optional[str], routing: Any) -> MemoryBackend:
    """Resolve the concrete storage backend (NO overlay — `build_backend` applies that). This is
    the CM.S2 selection body verbatim: the shared-Postgres choice is gated on the routing verdict,
    and any unavailability degrades to the guaranteed SQLite floor."""
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
        # CM.S2 — when the ONE routing decision (from the shared E2 health verdict) already
        # says degraded, route straight to the LOCAL floor: no redundant connect, and the
        # loud degrade marker on `routing` is what the read surfaces (never a silent fallback).
        if routing is not None and getattr(routing, "degraded", False):
            return floor()
        # D1 — capture WHY the build failed. A schema failure (unprovisioned / out of range /
        # a DML-only role that cannot CREATE) is not an unreachable database, and telling the
        # user to `mokata sync` a healthy connection is how this bug stayed invisible.
        failures: List[Exception] = []
        backend = build_postgres_backend(config, project=project,
                                         on_unavailable=failures.append)
        if backend is None:
            # Residual race: health said OK but the live backend build still failed, so the
            # read really did fall back. Mark it degraded so the store stays honest (never a
            # silent team→local fall-through) — mirrors the routing-gated case above.
            if routing is not None and getattr(routing, "served_by_team", False):
                from ..degrade import FAILURE_UNREACHABLE
                exc = failures[0] if failures else None
                routing.mark_degraded_from_build_failure(
                    failure_class=getattr(exc, "failure_class", FAILURE_UNREACHABLE),
                    detail=(str(exc) if exc else "shared backend unavailable at read time"),
                    fix=getattr(exc, "fix", ""))
            return floor()          # unavailable → LOCAL SQLite floor
        return backend
    if tool == "pgvector":
        # DB.S4 — the OPT-IN semantic store, and the branch that finally makes the 35e backend
        # reachable. It is a `memory_store` provider like any other (it implements the whole
        # `MemoryBackend` contract), so selecting it is how a team opts in; no default profile
        # lists it and no core path touches it — pgvector needs `CREATE EXTENSION`, which ADR-54
        # keeps OFF the golden path. Absent/unopted ⇒ this branch is never entered and the
        # postgres/sqlite behaviour above is byte-identical to pre-DB.S4.
        if routing is not None and getattr(routing, "degraded", False):
            return floor()
        from .embed import make_embedder
        from .vector import build_pgvector_backend
        # THE EMBEDDER FOR AN OPT-IN SEMANTIC STORE, and the two asks are NOT the same ask.
        #
        # This branch used to read `config.get("embedder") or "auto"`. `auto` means "use whatever
        # is best", and its documented floor is `HashingEmbedder` — correct for a zero-dep default,
        # WRONG here, because this is the ONE path (DB.S8f) that reaches a live embedder without
        # `memory.embedder` being set anywhere. Defaulting it to `auto` therefore let an unset
        # config fill a REAL pgvector index with token-hash vectors that nobody asked for.
        #
        # VECTOR-TIER-NOISE (doc 84) is why that is not a cosmetic default. Hashing returns cosine
        # ~0.75 between UNRELATED items; DB.S8f's K2 bound weights it down to 0.077 so it cannot
        # dominate the fusion, but DB.S8g measured at the declared N=100,000 that bounding is not
        # eliminating — the tier is still NET-NEGATIVE on recall (arm C 0.4306 < arm B 0.4444).
        # A tier that measurably subtracts recall must not be switched on by the ABSENCE of
        # configuration. Off is the honest default; hashing-by-omission is not.
        #
        # So the ask is split, and each half keeps its own guarantee:
        #   * NAMED  — `make_embedder` resolves it exactly as before, and EXPLICIT-EMBEDDER-ASK
        #              still announces when a named ask lands on hashing. A user who wrote
        #              `embedder: hashing` gets hashing; this refusal is about silence, not choice.
        #   * UNSET  — a real embedder or NOTHING. `None` makes `build_pgvector_backend` return
        #              None, the tier stays OFF, and the store falls to the local floor with a
        #              notice that names the cause and the one command that fixes it.
        asked = (config or {}).get("embedder")
        if asked:
            embedder = make_embedder(asked, semantic_store=True)
        else:
            embedder = _unasked_semantic_embedder()
        failures: List[Exception] = []
        backend = build_pgvector_backend(config, embedder, project=project,
                                         on_unavailable=failures.append)
        if backend is None:
            _note_vector_degrade(failures)
            return floor()
        return backend
    if tool == "native-memory":
        client = clients.get("native-memory")
        if client is not None:
            return NativeMemoryBackend(client)
        # no client wired -> degrade to the guaranteed floor (not a second detection)
        return floor()
    # "ripgrep"/unknown, or unavailable -> SQLite floor
    return floor()


def _unasked_semantic_embedder() -> Optional[Any]:
    """The embedder for a pgvector store that named NONE: a real one, or `None` so the tier is OFF.

    VECTOR-TIER-NOISE (doc 84). The question here is deliberately narrower than `detect_embedder`'s
    — that function answers "semantic on, quality honest", and its whole design is to FALL BACK to
    hashing so a recall never breaks. That is the right answer for a lexical/semantic read tier and
    the wrong one for a vector STORE, where the fallback is not a weaker ranking but a persisted
    index full of token hashes and a measured recall LOSS at scale. Asking the narrow question
    directly is also what keeps the reporting honest: routing `detect_embedder` through here would
    emit its "semantic recall is TOKEN-HASH" notice, which would now be FALSE — the tier is off,
    not hashing — and `note_degraded` records for `doctor` even when its output is redirected, so
    the wrong sentence would outlive the call.

    Both causes end in the same verdict and differ only in the FIX, exactly as `detect_embedder`
    splits them: the extra is not installed (install it), or it is installed and the model would
    not load (network/`doctor`). Naming the wrong one sends the user to the wrong place.
    """
    from ..degrade import FAILURE_ENGINE, note_degraded
    from .embed import Model2VecEmbedder, ModelUnavailable, _extra_is_installed
    try:
        return Model2VecEmbedder()
    except ModelUnavailable as exc:
        if _extra_is_installed():
            fix = ("run `mokata doctor`; the model is fetched once — connect to the network once "
                   "and re-run, or `pip install -U mokata[embeddings]`")
            cause = "the `embeddings` extra is installed but the model could not be loaded"
        else:
            fix = ("pip install 'mokata[embeddings]'  (~30MB, numpy-only) — or set "
                   "`memory.embedder: hashing` to say token-hash vectors are deliberate")
            cause = "the `embeddings` extra is not installed"
        note_degraded(
            "memory-semantic", FAILURE_ENGINE,
            fallback=f"semantic recall is OFF — a pgvector store was selected but no embedder was "
                     f"configured, and {cause}. mokata will NOT fill a vector index with "
                     f"token-hash vectors you did not ask for (they measure net-negative on "
                     f"recall); memory is local",
            fix=fix, detail=str(exc))
        return None


def _note_vector_degrade(failures: List[Exception]) -> None:
    """DB.S4 — say WHY the opt-in semantic store fell back to the local floor.

    The two causes need different sentences, which is the entire reason `build_pgvector_backend`
    reports its typed failure instead of a bare None. A STAMP MISMATCH is not a fault at all — the
    tier is off because mokata refused to rank against another embedder's vectors — so its notice
    names both embedders and the one command that fixes it. Everything else (unset DSN, driver
    absent, unreachable, unprovisioned) is ordinary unavailability and reads like the postgres
    branch's. Neither raises: a memory read falls to the floor and carries on."""
    from ..degrade import FAILURE_ENGINE, FAILURE_UNREACHABLE, note_degraded
    from .vector import EmbedderStampMismatch
    exc = failures[0] if failures else None
    if isinstance(exc, EmbedderStampMismatch):
        note_degraded(
            "memory-semantic", FAILURE_ENGINE,
            fallback=f"semantic recall is OFF — the index was built with "
                     f"'{exc.stamped[0]}' but '{exc.current[0]}' is configured",
            fix=f"re-embed the index with `{exc.MIGRATION_COMMAND}` (preview first — it is gated)",
            detail="refusing to rank a query against another embedder's vectors")
        return
    note_degraded(
        "memory-semantic", getattr(exc, "failure_class", FAILURE_UNREACHABLE),
        fallback="semantic recall is OFF — the pgvector store is unavailable; memory is local",
        fix=getattr(exc, "fix", "") or "check the DSN env var and run `mokata doctor`",
        detail=(str(exc) if exc else "pgvector store unconfigured or unreachable"))


def _warn_deprecated_memory_chain(router: Any, root: str) -> None:
    """SIMP.S2 — MANIFEST PARITY (Jas 2026-07-15). A committed manifest that LISTS `native-memory`
    or `obsidian` in its memory_store chain keeps resolving (never silently vanishes) but emits the
    once-per-repo deprecation warn — so a repo whose config still names a deprecated provider is
    told it goes away at 0.0.17 and how to migrate, whether that provider serves the read or has
    already fallen back to the SQLite floor (the swap-to-come is never silent). Degrade-clean: a
    duck-typed router with no `.manifest.fallback_order` warns for nothing rather than raising."""
    from .. import deprecation
    try:
        chain = router.manifest.fallback_order("memory_store")
    except (ManifestError, AttributeError):
        return
    for tool in deprecation.DEPRECATED_MEMORY_TOOLS:
        if tool in chain:
            deprecation.warn_deprecated(tool, root)


def select_memory_backend(router: Any, root: str,
                          clients: Optional[Dict[str, Any]] = None,
                          project: Optional[str] = None,
                          routing: Any = None) -> MemoryBackend:
    _warn_deprecated_memory_chain(router, root)
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
    # CM.S2 — thread the ONE read-routing decision so the shared-tool choice is gated on the
    # SAME E2 health verdict (never a second, divergent availability answer). None → unchanged.
    return build_backend(tool, root, clients, config, project=project, routing=routing)


def _scope_context_for(surface: Any, project: Any) -> Any:
    """TM.S6 — the working scope context (doc 62 §2) for a store built from a surface.

    LOCAL mode (the default) → None: NO scope filtering, so recall is byte-identical to
    pre-TM.S6. TEAM mode → a shared context whose PROJECT element is the current project key
    (mapping the Stage-71a project onto the project level); the personal element matches any
    personal item (per-user identity is TM.S10) so existing personal items are never dropped.
    Fail-closed + degrade-clean: any error reads as local (None)."""
    try:
        from .. import run_mode as _rm
        if _rm.read_mode(surface) != _rm.TEAM:
            return None
        from .scope import ScopeContext
        proj = project if isinstance(project, str) and project else None
        category = (surface.manifest.setting("memory", {}) or {}).get("category")
        return ScopeContext(project=proj, category=category or None, user=None)
    except (ImportError, AttributeError, ManifestError):
        # D5 — the real raisers: a half-installed package (ImportError), a duck-typed surface with
        # no `.manifest`/a non-mapping `memory` block (AttributeError), and a broken manifest
        # (ManifestError). None → LOCAL/no scope filtering, the fail-closed default this docstring
        # already promises: an unresolvable scope NEVER silently widens a team read.
        return None


def _identity_and_access_for(surface: Any) -> Tuple[Any, Any]:
    """TM.S10 — the run identity + access policy for a store built from a surface (doc 52 M-1/M-2).

    Identity is the run identity (`team_audit.actor()`) in BOTH modes — the real author is always
    stamped (M-1, "no more `user` everywhere"). The access POLICY only ENFORCES in team mode (doc 62
    §6): team mode → an enforcing policy from `settings.access.grants`; local mode → None. None IS
    the local contract — no enforcement, byte-identical recall, single-user full personal access —
    so local is NOT a degrade and never earns a notice.

    D5b — the TEAM-mode fallback is now genuinely FAIL-CLOSED, which is what this docstring always
    claimed and the code did not do. A team store whose grants could not be READ (an unreadable
    `.manifest.data`) fell back to `access = None`, and None turns enforcement OFF: the broken
    manifest — the very case enforcement has to survive — was the one case that disabled it. The
    fallback is now a DENY-BY-DEFAULT policy (`enforce=True`, zero grants): every shared
    (team/project/global) item becomes unreadable and uneditable, and only the identity's OWN
    personal items stay reachable (`AccessPolicy.roles_for`'s owner rule — and if the identity is
    unresolvable too, not even those). A teammate's private items cannot leak out of a degrade.

    The ONE residual fail-open, named rather than papered over: a half-installed package
    (ImportError) leaves neither the mode resolver nor the policy ENGINE importable, and code that
    cannot import `AccessPolicy` cannot construct a deny-by-default one. Forcing enforcement on what
    may well be a LOCAL store would break local's zero-config contract, so the floor there stays
    local — but LOUDLY (`note_degraded`), never as a secret. Neither path crashes the store."""
    try:
        from ..team_audit import actor
        identity = actor()
    except Exception:
        # D5 — deliberately left BROAD, with no narrow class to name: `team_audit.actor()`'s own
        # contract is never-raise (it resolves the run identity and falls back to a placeholder),
        # so there is no honest class to enumerate here. An unresolvable identity is not a degrade
        # of a capability — the write path stamps a placeholder author and carries on.
        identity = None
    try:
        from .. import run_mode as _rm
        from .access import AccessPolicy
    except ImportError as exc:
        # D5b — a half-installed package: the mode resolver and/or the policy engine are absent.
        # The mode is UNKNOWABLE here (only `run_mode` can answer it), so a deny-by-default policy
        # would be imposed on local stores too — breaking the zero-config contract for a fault that
        # is not theirs. Local is the honest floor; the notice is what stops it being a silent one.
        from ..degrade import FAILURE_ENGINE, note_degraded
        note_degraded("memory-access", FAILURE_ENGINE,
                      fallback="access enforcement is OFF — the policy engine could not be imported",
                      fix="reinstall mokata (`pip install -U mokata`), then run `mokata doctor`",
                      detail=str(exc))
        return identity, None

    access = None
    if _rm.read_mode(surface) == _rm.TEAM:      # never raises; an unknown mode is NEVER team
        try:
            settings = surface.manifest.data.get("settings") or {}
            access = AccessPolicy.from_settings(settings, enforce=True)
        except AttributeError as exc:
            # D5b — the real (and only) raiser left: a surface whose `.manifest.data` is absent or
            # is not a mapping — a duck-typed surface, a half-written/torn manifest. `read_mode`
            # never raises and `from_settings` is itself fail-closed, so nothing else reaches here.
            # FAIL-CLOSED: deny by default rather than disable enforcement.
            from ..degrade import FAILURE_CORRUPT, note_degraded
            access = AccessPolicy(grants={}, enforce=True)
            note_degraded("memory-access", FAILURE_CORRUPT,
                          fallback="team access DENIED by default — the manifest's grants could "
                                   "not be read",
                          fix="repair the manifest (`mokata doctor`), then retry",
                          detail=str(exc))
    return identity, access
