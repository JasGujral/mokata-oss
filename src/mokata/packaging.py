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
from typing import Any, Dict, List, Tuple

PLUGIN_MANIFEST_PATH = ".claude-plugin/plugin.json"
MARKETPLACE_PATH = ".claude-plugin/marketplace.json"


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


def load_and_validate(root: str = ".") -> Tuple[List[str], List[str]]:
    """Return (plugin_errors, marketplace_errors) for the committed manifests."""
    def _load(rel: str) -> Any:
        with open(os.path.join(root, rel), encoding="utf-8") as fh:
            return json.load(fh)
    return validate_plugin(_load(PLUGIN_MANIFEST_PATH)), \
        validate_marketplace(_load(MARKETPLACE_PATH))
