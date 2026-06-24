"""J3 — shareable stack manifests.

Publish/share a governed stack so a team adopts it in one command: `export_manifest`
writes the current manifest as a shareable artifact; `apply_manifest` validates an
imported manifest (rejecting an invalid one) and writes it as the project's config —
a durable, human-gated write (overwriting an existing config requires `force`).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

from . import MANIFEST_FILENAME, MOKATA_DIR, schema
from .manifest import Manifest

SHARE_FILENAME = "mokata-stack.json"


@dataclass
class ApplyResult:
    applied: bool
    aborted: bool = False
    errors: List[str] = field(default_factory=list)
    path: Optional[str] = None
    message: str = ""


def validate_shared(data: Any) -> List[str]:
    return schema.validate_manifest(data)


def export_manifest(surface: Any, dest: Optional[str] = None) -> dict:
    """Return the current manifest as shareable data; optionally write it to `dest`."""
    data = surface.manifest.data
    if dest is not None:
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(Manifest.from_dict(data).to_json())
    return data


def load_shared(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _default_confirm(text: str) -> bool:
    try:
        return input(text + "\nApply this shared stack? [y/N] ").strip().lower() \
            in ("y", "yes")
    except EOFError:
        return False


def apply_manifest(root: str, data: Any,
                   confirm: Optional[Callable[[str], bool]] = None,
                   assume_yes: bool = False, force: bool = False) -> ApplyResult:
    """Validate then apply a shared manifest as this repo's config (human-gated)."""
    errors = validate_shared(data)
    if errors:
        return ApplyResult(applied=False, errors=errors,
                           message="rejected: shared manifest is invalid")

    mdir = os.path.join(root, MOKATA_DIR)
    manifest_path = os.path.join(mdir, MANIFEST_FILENAME)
    if os.path.exists(manifest_path) and not force:
        return ApplyResult(applied=False, aborted=True, path=manifest_path,
                           message="a manifest already exists; re-run with force "
                                   "to overwrite")

    if not assume_yes:
        gate = confirm or _default_confirm
        if not gate(f"apply shared stack to {manifest_path}?"):
            return ApplyResult(applied=False, aborted=True, path=manifest_path,
                               message="aborted by user")

    os.makedirs(mdir, exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8") as fh:
        fh.write(Manifest.from_dict(data).to_json())
    return ApplyResult(applied=True, path=manifest_path, message="applied")
