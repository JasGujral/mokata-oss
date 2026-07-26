"""DB.S4 — the honest RETRIEVAL-STACK line for `mokata doctor`.

Retrieval quality was a mystery. Two installs could both say "memory: ok" while one ranked by
meaning and the other by token-hash overlap, and nothing anywhere told the user which — so a
recall that missed the obvious paraphrase looked like mokata being bad at its job rather than a
tier being off. This module answers, in one line, the only question that matters: **which
engines are actually ranking your recall right now.**

Two axes, reported separately because they degrade independently:

  * SEMANTIC — `model2vec` (the blessed extra, real meaning), `hashing` (the zero-dep floor,
    token-hash — honestly labelled as NOT semantic), or `off` (no embedder configured);
  * LEXICAL  — `fts5` / `tsvector` (DB.S3's in-database ranking) or `jaccard` (the Python floor).

INFORMATIONAL, always. It emits no `DoctorFinding` and never touches `report.ok` or doctor's exit
code — every state it can print is a legitimate, supported configuration. `hashing` + `jaccard`
is a working zero-dependency install, not a problem; telling the user it is what they have is the
entire deliverable.

Read-only and degrade-clean: it opens no database it wouldn't otherwise open, and any failure to
resolve a tier prints `unknown` rather than breaking the doctor run that was called to diagnose
things in the first place.

Stdlib-only. Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional

SEMANTIC_OFF = "off"
UNKNOWN = "unknown"

# What each engine is honestly worth — the labels doctor prints next to the name, so nobody has to
# read the source to learn that "semantic (hashing)" isn't semantic.
_SEMANTIC_NOTE = {
    "hashing": "token-hash overlap, NOT meaning — install mokata[embeddings] for real semantics",
    SEMANTIC_OFF: "no embedder configured — ranking is lexical only",
    UNKNOWN: "the configured embedder could not be resolved — run `mokata doctor` in the repo",
}
_LEXICAL_NOTE = {
    "jaccard": "Python keyword overlap — the floor",
    "fts5": "SQLite FTS5 + bm25, ranked in the database",
    "tsvector": "Postgres tsvector + ts_rank, ranked in the database",
}


@dataclass
class RetrievalStack:
    semantic: str = SEMANTIC_OFF     # "model2vec:<model>" | "hashing" | "off" | "unknown"
    lexical: str = "jaccard"         # "fts5" | "tsvector" | "jaccard" | "unknown"
    detail: str = ""                 # a degrade reason (e.g. a stamp mismatch), if any

    @property
    def semantic_family(self) -> str:
        """`model2vec` / `hashing` / `off` / `unknown` — the FAMILY, so the label map keys off the
        engine rather than its exact version. Both halves of an id are versioned (`hashing-v1`,
        `model2vec:<model>`) precisely so the stamp can tell them apart; the report wants the
        coarser answer, and getting that split wrong is how the hashing tier silently loses its
        "NOT meaning" warning."""
        if not self.semantic:
            return SEMANTIC_OFF
        return self.semantic.split(":", 1)[0].split("-v", 1)[0]

    def render_lines(self, *, ascii_only: bool = False) -> List[str]:
        bullet = "-" if ascii_only else "•"
        sem_note = _SEMANTIC_NOTE.get(self.semantic_family, "")
        lex_note = _LEXICAL_NOTE.get(self.lexical, "")
        lines = [
            "retrieval stack",
            f"  {bullet} semantic: {self.semantic}" + (f"  ({sem_note})" if sem_note else ""),
            f"  {bullet} lexical:  {self.lexical}" + (f"  ({lex_note})" if lex_note else ""),
        ]
        if self.detail:
            lines.append(f"  {bullet} note:     {self.detail}")
        return lines


def resolve_stack(surface: Any) -> RetrievalStack:
    """Resolve which engines are live for THIS repo. Never raises."""
    return RetrievalStack(semantic=_semantic_engine(surface), lexical=_lexical_engine(surface))


def _semantic_engine(surface: Any) -> str:
    """The configured embedder's identity, resolved through the SAME `make_embedder` the store
    uses — so doctor cannot report a tier the store wouldn't actually build. `auto` resolves here
    exactly as it resolves there, which means doctor also reports the model2vec→hashing fallback
    when the extra is installed but its model can't load."""
    try:
        from .embed import embedder_identity, make_embedder
        name = (surface.manifest.setting("memory", {}) or {}).get("embedder")
        # An opted-in pgvector store implies "auto" even without an explicit embedder setting —
        # the selection branch does the same, and doctor must describe the store that WILL be
        # built, not the one the settings block literally spells out.
        if not name and _memory_tool(surface) == "pgvector":
            name = "auto"
        embedder = make_embedder(name)
        if embedder is None:
            return SEMANTIC_OFF
        return embedder_identity(embedder)[0] or UNKNOWN
    except Exception:
        # DEGRADE_CLEAN: doctor is the command you run WHEN things are broken, so a duck-typed or
        # half-written surface must yield `unknown` rather than an exception — a diagnostic that
        # crashes on a broken repo is a diagnostic that is never there when it is needed.
        return UNKNOWN


def _lexical_engine(surface: Any) -> str:
    """DB.S3's `lexical_mode`, read off the resolved backend. Opens the store the same way a
    recall would (and closes it), so the answer is the live one rather than a guess from config."""
    store = None
    try:
        from .backends import LEXICAL_MODE_JACCARD
        from .store import MemoryStore
        store = MemoryStore.from_surface(surface)
        return getattr(store.backend, "lexical_mode", LEXICAL_MODE_JACCARD)
    except Exception:
        return UNKNOWN
    finally:
        try:
            if store is not None:
                store.close()
        except Exception:
            # Teardown of a diagnostic read: nothing to degrade to and nothing a user could act on.
            pass


def _memory_tool(surface: Any) -> Optional[str]:
    try:
        res = surface.router.resolve("memory_store")
        return res.tool if (res is not None and res.available) else None
    except Exception:
        return None


def retrieval_lines(surface: Any, *, ascii_only: bool = False) -> List[str]:
    """The doctor section. Informational — never a finding, never an exit-code input."""
    return resolve_stack(surface).render_lines(ascii_only=ascii_only)
