"""J1 — Claude Code plugin packaging: manifest paths + dependency-free validators.

mokata ships as a Claude Code plugin under the MoStack marketplace. Two committed,
reviewable manifests describe it:
  - .claude-plugin/plugin.json      — the plugin (name/version/commands/hooks/license)
  - .claude-plugin/marketplace.json — the marketplace listing (install/update metadata)

These validators mirror the spine's own validation style (structural, no dependency).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

PLUGIN_MANIFEST_PATH = ".claude-plugin/plugin.json"
MARKETPLACE_PATH = ".claude-plugin/marketplace.json"
PYPROJECT_PATH = "pyproject.toml"
PACKAGE_INIT_PATH = "src/mokata/__init__.py"


def _req_str(data: Dict[str, Any], key: str, where: str, errors: List[str]) -> None:
    if not isinstance(data.get(key), str) or not data.get(key):
        errors.append(f"{where}.{key} must be a non-empty string")


def validate_plugin(data: Any) -> List[str]:
    errors: List[str] = []
    if not isinstance(data, dict):
        return ["plugin manifest must be a JSON object"]
    _req_str(data, "name", "plugin", errors)
    _req_str(data, "version", "plugin", errors)
    # Optional-but-typed fields.
    if "license" in data and not isinstance(data["license"], str):
        errors.append("plugin.license must be a string")
    if "keywords" in data and not isinstance(data["keywords"], list):
        errors.append("plugin.keywords must be an array")
    return errors


def validate_marketplace(data: Any) -> List[str]:
    errors: List[str] = []
    if not isinstance(data, dict):
        return ["marketplace manifest must be a JSON object"]
    _req_str(data, "name", "marketplace", errors)
    plugins = data.get("plugins")
    if not isinstance(plugins, list) or not plugins:
        errors.append("marketplace.plugins must be a non-empty array")
        return errors
    for i, entry in enumerate(plugins):
        where = f"marketplace.plugins[{i}]"
        if not isinstance(entry, dict):
            errors.append(f"{where} must be an object")
            continue
        _req_str(entry, "name", where, errors)
        _req_str(entry, "source", where, errors)
    return errors


# =====================================================================================
# Stage 61b — release version-consistency check (PURE / OFFLINE).
# =====================================================================================
# The 0.0.4 cut tagged a commit whose version fields lagged the tag, so the
# tag-triggered CI went red. This is the single source of truth for "do all version
# fields equal the tag we're about to push?" — read by `mokata release-check`, the
# `release.sh` preflight (which verifies AT THE EXACT COMMIT being tagged, on the dev
# checkout AND the public mirror), and the ship-artifact test. No network, never raises:
# a missing/unreadable field is reported as a named mismatch (None), not a crash.

# field name -> (relative path, extractor). The four version-bearing FILES + the package
# __version__ — the set a tag must match before it can be pushed.
_VERSION_FIELDS = (
    "pyproject.toml:version",
    "plugin.json:version",
    "marketplace.json:metadata.version",
    "marketplace.json:plugins[0].version",
    "src/mokata/__init__.py:__version__",
)


def _read_text(root: str, rel: str) -> Optional[str]:
    try:
        with open(os.path.join(root, rel), encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def _toml_version(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    m = re.search(r'(?m)^\s*version\s*=\s*"([^"]+)"', text)
    return m.group(1) if m else None


def _dunder_version(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    m = re.search(r'(?m)^\s*__version__\s*=\s*"([^"]+)"', text)
    return m.group(1) if m else None


def _json_get(root: str, rel: str, *path: Any) -> Optional[str]:
    text = _read_text(root, rel)
    if text is None:
        return None
    try:
        node: Any = json.loads(text)
        for key in path:
            node = node[key]
    except (ValueError, KeyError, IndexError, TypeError):
        return None
    return node if isinstance(node, str) else None


def read_version_fields(root: str = ".") -> "Dict[str, Optional[str]]":
    """The version string at each of the five canonical locations (None when the file or
    field is missing/unreadable). Pure/offline; never raises."""
    return {
        "pyproject.toml:version": _toml_version(_read_text(root, PYPROJECT_PATH)),
        "plugin.json:version": _json_get(root, PLUGIN_MANIFEST_PATH, "version"),
        "marketplace.json:metadata.version":
            _json_get(root, MARKETPLACE_PATH, "metadata", "version"),
        "marketplace.json:plugins[0].version":
            _json_get(root, MARKETPLACE_PATH, "plugins", 0, "version"),
        "src/mokata/__init__.py:__version__":
            _dunder_version(_read_text(root, PACKAGE_INIT_PATH)),
    }


def _normalize_tag(target: str) -> str:
    """`v0.0.4` / `0.0.4` -> `0.0.4` (the comparable version string)."""
    return (target or "").strip().lstrip("vV")


@dataclass
class ReleaseConsistency:
    """Whether every version field equals the intended tag — fail-closed."""

    target: str                                          # the intended version (no 'v')
    fields: "Dict[str, Optional[str]]" = field(default_factory=dict)
    mismatches: List[Tuple[str, Optional[str]]] = field(default_factory=list)

    @property
    def consistent(self) -> bool:
        # an empty target is itself a failure (nothing to release against)
        return bool(self.target) and not self.mismatches

    def render(self) -> str:
        head = ("release-check PASS" if self.consistent else "release-check FAIL")
        lines = [f"{head} — intended tag {self.target or '(none given)'}"]
        for name, val in self.fields.items():
            ok = (val == self.target)
            mark = "  " if ok else "✗ "
            lines.append(f"  {mark}{name}: {val if val is not None else '(missing)'}")
        if not self.target:
            lines.append("  offenders: no intended version supplied")
        elif self.mismatches:
            offenders = ", ".join(f"{n}={v if v is not None else '(missing)'}"
                                  for n, v in self.mismatches)
            lines.append(f"  offenders (≠ {self.target}): {offenders}")
        return "\n".join(lines)


def check_release_consistency(target: str, root: str = ".") -> ReleaseConsistency:
    """Verify that all five version fields under `root` equal `target` (the intended tag).
    PURE/OFFLINE and fail-closed: a missing field or any value ≠ the tag is a named
    mismatch (so a caller — release.sh — REFUSES to tag). Never raises."""
    norm = _normalize_tag(target)
    fields = read_version_fields(root)
    mismatches = [(name, val) for name, val in fields.items() if val != norm]
    return ReleaseConsistency(target=norm, fields=fields, mismatches=mismatches)
