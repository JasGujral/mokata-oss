"""A5 — Unified config + constitution surface.

One object, one place to read from. Every other layer (bootstrap, init, future
stages) goes through `Surface` rather than touching files directly, so there is a
single governed entry point to the manifest, the prose constitution, and a router
wired from them.

Layout under a repo root:
    .mokata/
        manifest.json     <- the stack manifest (A1)
        constitution.md    <- the prose constitution (governing articles)
"""

from __future__ import annotations

import functools
import os
import re
from dataclasses import dataclass
from typing import List, Optional

from . import (
    CONSTITUTION_FILENAME,
    MANIFEST_FILENAME,
    MOKATA_DIR,
    TEMP_LOCAL_DIRNAME,
)
from .detect import Detector
from .manifest import Manifest, ManifestError
from .router import Router
from .repo_identity import find_project_root, is_initialized  # noqa: F401 — re-exported
from .state import StateStore
from .errors import MokataError

# Subdirectory holding transient pipeline state (e.g. the brainstorm phase's approved
# approach, resume checkpoints). Config is hand-edited and committed; state is produced by
# pipeline runs and is runtime/transient, so it lives under .mokata/temp_local/ (Stage
# 24D) — gitignored, alongside the memory store and audit ledger.
STATE_DIRNAME = "state"


class ConfigError(MokataError):
    """Raised when the unified surface cannot be loaded (e.g. not initialized)."""


@dataclass
class Constitution:
    text: str
    path: Optional[str]

    @property
    def present(self) -> bool:
        return bool(self.text.strip())

    def articles(self) -> List[str]:
        """Article headings (## or ###) read as governing articles, for at-a-glance
        counts in the bootstrap and `mokata` summaries. The H1 document title is not
        an article and is excluded."""
        return [
            line.lstrip("#").strip()
            for line in self.text.splitlines()
            if re.match(r"^#{2,3}\s+\S", line)
        ]


class Surface:
    """The single governed read surface over mokata's committed config."""

    def __init__(
        self,
        manifest: Manifest,
        constitution: Constitution,
        root: str,
        detector: Optional[Detector] = None,
    ) -> None:
        self.manifest = manifest
        self.constitution = constitution
        self.root = root
        self.detector = detector or Detector()
        self.router = Router(manifest, self.detector)

    @property
    def mokata_dir(self) -> str:
        """The ONE `.mokata/` of this repo — the main checkout's, from a linked worktree too (0.0.26
        P31, SESSION-WORKTREES: every session runs in its own worktree, and its runs, approvals and
        snapshots must be the same store the IDE and every other session read). `root` itself stays
        THIS tree: the working tree and the branch are per-tree."""
        from .repo_identity import canonical_mokata_dir
        return canonical_mokata_dir(os.path.join(self.root, MOKATA_DIR))

    @property
    def temp_local_dir(self) -> str:
        """The gitignored runtime area under .mokata/ for transient data (Stage 24D)."""
        return os.path.join(self.mokata_dir, TEMP_LOCAL_DIRNAME)

    @property
    def plans_dir(self) -> str:
        """Stage 6p — the INTERNAL brainstorm plan-file area. The approved design is saved here at
        approval; `mokata plan export` copies a plan into the project-root `plans/` (the committable
        copy). It's internal runtime data, so it lives under the gitignored `temp_local/` (24D) —
        the same split as the memory store and audit ledger — NOT the committable `.mokata/` root."""
        from .plans import PLANS_DIRNAME
        return os.path.join(self.temp_local_dir, PLANS_DIRNAME)

    @functools.cached_property
    def state(self):
        """The governed store for transient pipeline state under
        .mokata/temp_local/state/. Downstream phases read the brainstorm phase's approved
        approach from here; it's runtime data, not committed config (Stage 24D).

        MS.S2 — the store is SESSION-SCOPED: the per-run pipeline singletons (approved_approach,
        brainstorm_progress, approved_refinements, emitted_spec) live under THIS window's session_id
        so two Claude Code windows on one repo never clobber each other, while shared repo state
        (memory stats, spec corpus, the registry) passes through global. Scoping is transparent to a
        single-session flow (same keys, same value format); only the physical file NAME gains the
        session dimension, with a one-way legacy fallback for pre-upgrade runs (see session_state).

        MCP-SURF — `cached_property`, not `property`: this was rebuilding a StateStore AND re-running
        `scoped_store` (which resolves the session identity) on EVERY one of ~70 `surface.state` reads,
        and a single MCP tool call touches it many times. The cache is PER-SURFACE-INSTANCE, and that
        is what makes it safe rather than a staleness bug:

          * a `Surface` is per-invocation — built fresh inside a tool call / CLI command and dropped,
            never memoized at module level or shared across calls, so a cached store cannot outlive
            the operation that made it;
          * neither cached object holds READ data. `StateStore` and `SessionScopedStore` are stateless
            path resolvers — every read/write still hits disk on every call, so a value written by
            anyone (this process or another window) is still seen immediately. What is cached is the
            *addressing*, not the state;
          * the only input the addressing binds is the session_id, which `session.current_session()`
            mints ONCE per process and holds immutable by design.

        The one pattern this would break is a single Surface held across a session-identity change
        (`session.reset_for_test()` between two `.state` reads on the SAME instance). That is a test-
        only shape, and no test does it: production session identity is immutable, and the registry
        key that IS read across resets (`session_registry`) is deliberately NOT session-scoped, so its
        physical name is identical either way."""
        base = StateStore(os.path.join(self.temp_local_dir, STATE_DIRNAME))
        from .session_state import scoped_store
        return scoped_store(base)

    @classmethod
    def is_initialized(cls, root: str = ".") -> bool:
        return is_initialized(root)

    @classmethod
    def load(cls, root: str = ".", detector: Optional[Detector] = None) -> "Surface":
        # WT-ROOT — the committed CONFIG (manifest + constitution) is repo-scoped and resolves to
        # the main checkout, so a session inside a linked worktree loads the repo's real manifest
        # instead of erroring "not initialized" (and being told to run `mokata init`, which is the
        # advice that forks the state). `root` itself deliberately stays THIS tree: the working tree
        # and the branch are per-tree, and so is everything under this tree's `temp_local/`.
        from .repo_identity import canonical_repo_root, resolve_mokata_root
        resolution = resolve_mokata_root(root)
        config_root = resolution.root or root
        mdir = os.path.join(config_root, MOKATA_DIR)
        manifest_path = os.path.join(mdir, MANIFEST_FILENAME)
        if not os.path.exists(manifest_path):
            if resolution.unresolved_worktree:
                # WT-ROOT — the silent None goes, HERE, where a human is reading. The generic
                # message below would be actively harmful in this case: `mokata init` run inside a
                # linked worktree is exactly what forks the memory store and the audit ledger, so
                # the one repo this advice must never be given to is this one.
                raise ConfigError(
                    f"'{os.path.abspath(root)}' is a linked git WORKTREE of "
                    f"{canonical_repo_root(root)}, which has a {MOKATA_DIR}/ but no readable "
                    f"{MANIFEST_FILENAME} — so mokata cannot resolve this repo's config. This is a "
                    f"broken mokata repo, NOT an uninitialized one. Fix it in the MAIN checkout; do "
                    f"NOT run `mokata init` here, which would fork memory and the audit ledger."
                )
            raise ConfigError(
                f"mokata is not initialized in '{os.path.abspath(root)}' "
                f"(no {MOKATA_DIR}/{MANIFEST_FILENAME}). Run `mokata init` first."
            )
        try:
            manifest = Manifest.load(manifest_path)
        except ManifestError as exc:
            raise ConfigError(str(exc)) from exc

        const_path = os.path.join(mdir, CONSTITUTION_FILENAME)
        if os.path.exists(const_path):
            with open(const_path, "r", encoding="utf-8") as fh:
                constitution = Constitution(text=fh.read(), path=const_path)
        else:
            constitution = Constitution(text="", path=None)

        return cls(manifest, constitution, root=root, detector=detector)
