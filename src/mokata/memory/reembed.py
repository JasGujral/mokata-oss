"""DB.S4 — the GATED re-embed migration: `mokata memory reembed`.

The stamp binding (Jas 2026-07-14) says an embedder change must never silently mix vectors. That
makes refusal the RUNTIME behaviour — `PgVectorBackend.verify_stamp` raises, the semantic tier
goes off, recall falls to lexical — and it makes THIS module the way out: re-compute every item's
vector with the configured embedder, then restamp the index.

It is gated (P2) because it is a bulk durable rewrite of the team's shared memory, and it is
PREVIEWED first because the count is the only thing that makes the gate meaningful — "re-embed?"
is not a question a human can answer, "re-embed 4,812 items in project `web`, replacing vectors
built by `hashing-v1` with `model2vec:...`?" is.

Order matters and is not arbitrary: vectors are written BEFORE the stamp. A crash mid-run then
leaves a partially re-embedded index still stamped with the OLD embedder — which the runtime
already refuses, so the failure mode is a degraded (lexical) tier and a re-runnable migration.
Stamping first would invert that into the one state this whole feature exists to prevent: an
index that claims to be consistent and is not.

Stdlib-only. Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, List, Optional

from ..errors import MokataError


class ReembedError(MokataError):
    """Raised when the vector index can't be opened for a re-embed (no DSN, driver absent,
    unreachable). NON-degrading on purpose — unlike a recall, there is no useful floor to fall to:
    silently doing nothing while reporting success is how a user ends up trusting a stale index."""


@dataclass
class ReembedPlan:
    """What the human is being asked to approve, before anything is written."""
    count: int = 0
    stamped_embedder: str = ""          # what the index says it was built with ("" = unstamped)
    stamped_dim: int = 0
    target_embedder: str = ""           # what it will be re-embedded with
    target_dim: int = 0
    project: Optional[str] = None

    @property
    def needed(self) -> bool:
        """False when the index is already stamped with the target embedder — re-embedding it
        would rewrite every row to the identical value. Idempotence you can SEE: a no-op run says
        so instead of burning a gate prompt and a bulk write on nothing."""
        return not (self.stamped_embedder == self.target_embedder
                    and self.stamped_dim == self.target_dim)

    def render(self) -> str:
        was = (f"'{self.stamped_embedder}' (dim {self.stamped_dim})"
               if self.stamped_embedder else "an UNSTAMPED index (pre-DB.S4)")
        where = f" in project '{self.project}'" if self.project else ""
        return (f"mokata memory reembed: re-compute {self.count} item vector(s){where}\n"
                f"  from: {was}\n"
                f"    to: '{self.target_embedder}' (dim {self.target_dim})\n"
                f"  Vectors are rewritten, then the index is re-stamped. The items themselves "
                f"(subjects, values, provenance) are NOT modified.")


@dataclass
class ReembedResult:
    reembedded: int = 0
    restamped: bool = False
    aborted: bool = False
    message: str = ""

    def render(self) -> str:
        if self.aborted:
            return f"mokata memory reembed: {self.message} — nothing written."
        return (f"mokata memory reembed: {self.reembedded} item(s) re-embedded; "
                f"index re-stamped." if self.restamped else
                f"mokata memory reembed: {self.message}")


def plan_reembed(backend: Any, *, project: Optional[str] = None) -> ReembedPlan:
    """READ-ONLY preview: how many items, from which embedder to which. Writes nothing."""
    from .embed import embedder_identity
    stamped = backend.read_stamp() or ("", 0)
    target = embedder_identity(getattr(backend, "_embed", None))
    return ReembedPlan(count=len(backend.all()), stamped_embedder=stamped[0],
                       stamped_dim=stamped[1], target_embedder=target[0], target_dim=target[1],
                       project=project)


def run_reembed(backend: Any, *, confirm: Optional[Callable[[str], bool]] = None,
                assume_yes: bool = False, project: Optional[str] = None,
                ledger: Any = None, out: Optional[Callable[[str], None]] = None) -> ReembedResult:
    """Preview → HUMAN GATE → re-embed → restamp. A decline writes NOTHING (asserted by tests).

    `assume_yes` is the explicit non-interactive approval (`--yes`), the same escape every other
    gated path in mokata takes; the DEFAULT `confirm` is `read_yes_no`, which is fail-closed off a
    TTY — so an agent harness that never answers declines rather than silently rewriting the
    team's index."""
    emit = out or print
    plan = plan_reembed(backend, project=project)
    emit(plan.render())

    if not plan.needed:
        return ReembedResult(aborted=False, restamped=False,
                             message=f"the index is already stamped '{plan.target_embedder}' "
                                     f"(dim {plan.target_dim}) — nothing to do")
    if not plan.target_embedder:
        return ReembedResult(aborted=True, message="no embedder is configured, so there is "
                                                   "nothing to re-embed WITH")

    if not assume_yes:
        if confirm is None:
            from ..prompt import read_yes_no
            confirm = lambda q: read_yes_no(q)     # noqa: E731 — fail-closed off TTY by default
        if not confirm(f"Re-embed {plan.count} item(s) with '{plan.target_embedder}'?"):
            return ReembedResult(aborted=True, message="declined at the gate")

    # Re-embed. `put` recomputes the vector from the item's own text with the CONFIGURED embedder
    # and upserts by id — so this is the same write path a normal store write takes, and re-running
    # after a partial failure simply redoes work rather than duplicating it.
    items: List[Any] = backend.all()
    done = 0
    for item in items:
        backend.put(item)
        done += 1

    # ONLY now, with every vector rewritten, does the index get to claim the new embedder.
    backend.write_stamp(plan.target_embedder, plan.target_dim)
    if ledger is not None:
        # Secret-safe: the embedder NAME and the count. Never an item's subject/value — a ledger
        # entry is a durable audit record, not a place to spill the memory being migrated.
        ledger.record("memory_reembed", embedder=plan.target_embedder, dim=plan.target_dim,
                      items=done, scope="repo")
    return ReembedResult(reembedded=done, restamped=True)
