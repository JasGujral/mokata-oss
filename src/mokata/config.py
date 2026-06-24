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

import os
import re
from dataclasses import dataclass
from typing import List, Optional

from . import CONSTITUTION_FILENAME, MANIFEST_FILENAME, MOKATA_DIR
from .detect import Detector
from .manifest import Manifest, ManifestError
from .router import Router
from .state import StateStore

# Subdirectory of .mokata/ holding durable pipeline state (e.g. the brainstorm phase's
# approved approach). Config is hand-edited; state is produced by pipeline runs.
STATE_DIRNAME = "state"


class ConfigError(Exception):
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
        return os.path.join(self.root, MOKATA_DIR)

    @property
    def state(self) -> StateStore:
        """The governed store for durable pipeline state under .mokata/state/.
        Downstream phases read the brainstorm phase's approved approach from here."""
        return StateStore(os.path.join(self.mokata_dir, STATE_DIRNAME))

    @classmethod
    def is_initialized(cls, root: str = ".") -> bool:
        return os.path.exists(os.path.join(root, MOKATA_DIR, MANIFEST_FILENAME))

    @classmethod
    def load(cls, root: str = ".", detector: Optional[Detector] = None) -> "Surface":
        mdir = os.path.join(root, MOKATA_DIR)
        manifest_path = os.path.join(mdir, MANIFEST_FILENAME)
        if not os.path.exists(manifest_path):
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
