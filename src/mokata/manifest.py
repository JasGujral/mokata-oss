"""A1 — Stack manifest: load + validate the declarative stack file.

A `Manifest` is a thin, validated view over the parsed JSON. Loading is fail-loud on
*structural* problems (a malformed manifest is a config error worth stopping for), but
the router built on top of it degrades gracefully on *absent tools* (A3) — those are
two different failure classes and only the first should ever raise.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from . import schema


class ManifestError(Exception):
    """Raised when a manifest is missing, unparseable, or fails validation."""


class Manifest:
    def __init__(self, data: Dict[str, Any], source_path: str = "<memory>") -> None:
        self.data = data
        self.source_path = source_path

    # --- accessors -------------------------------------------------------------
    @property
    def manifest_version(self) -> int:
        return self.data["manifest_version"]

    @property
    def mokata_version(self) -> str:
        return self.data.get("mokata", {}).get("version", "unknown")

    @property
    def profile(self) -> str:
        return self.data.get("profile", "custom")

    @property
    def layers(self) -> Dict[str, Dict[str, Any]]:
        return self.data.get("layers", {})

    @property
    def capabilities(self) -> Dict[str, Dict[str, Any]]:
        return self.data.get("capabilities", {})

    @property
    def tools(self) -> Dict[str, Dict[str, Any]]:
        return self.data.get("tools", {})

    @property
    def settings(self) -> Dict[str, Any]:
        """Generic key/value store for stack-wide toggles. The router/layer/tool
        toggles have dedicated accessors; this is the open-ended bucket a future
        setting (e.g. E8's execution mode) reads from the same way."""
        s = self.data.get("settings", {})
        return s if isinstance(s, dict) else {}

    def setting(self, key: str, default: Any = None) -> Any:
        return self.settings.get(key, default)

    def layer_enabled(self, name: str) -> bool:
        layer = self.layers.get(name)
        # Absent layer entry reads as disabled (explicit-on convention).
        return bool(layer and layer.get("enabled", False))

    def tool_enabled(self, tool_id: str) -> bool:
        """Per-tool toggle (K1). A tool with no `enabled` key defaults to on, so
        Stage 1 manifests keep working unchanged."""
        tool = self.tools.get(tool_id)
        if not isinstance(tool, dict):
            return False
        return bool(tool.get("enabled", True))

    def capability_layer(self, need: str) -> Optional[str]:
        """The layer a capability belongs to, or None if it declares no layer
        (Stage 1 manifests) — in which case it is never layer-gated."""
        cap = self.capabilities.get(need)
        if not isinstance(cap, dict):
            return None
        return cap.get("layer")

    def capability_enabled(self, need: str) -> bool:
        """A capability is enabled when it declares no owning layer, or its owning
        layer is enabled (K1). Used by the router to drop disabled layers' caps."""
        layer = self.capability_layer(need)
        if layer is None:
            return True
        return self.layer_enabled(layer)

    def enabled_capabilities(self) -> List[str]:
        """Declared capabilities whose owning layer is enabled (declaration order)."""
        return [need for need in self.capabilities if self.capability_enabled(need)]

    def fallback_order(self, need: str) -> List[str]:
        cap = self.capabilities.get(need)
        if cap is None:
            raise ManifestError(
                f"unknown capability '{need}'; declared capabilities: "
                f"{sorted(self.capabilities)}"
            )
        return list(cap.get("fallback", []))

    # --- construction ----------------------------------------------------------
    @classmethod
    def from_dict(cls, data: Any, source_path: str = "<memory>") -> "Manifest":
        errors = schema.validate_manifest(data)
        if errors:
            joined = "\n  - ".join(errors)
            raise ManifestError(
                f"invalid manifest ({source_path}):\n  - {joined}"
            )
        return cls(data, source_path)

    @classmethod
    def load(cls, path: str) -> "Manifest":
        if not os.path.exists(path):
            raise ManifestError(f"manifest not found: {path}")
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except json.JSONDecodeError as exc:
            raise ManifestError(f"manifest is not valid JSON ({path}): {exc}") from exc
        return cls.from_dict(data, source_path=path)

    def to_json(self) -> str:
        return json.dumps(self.data, indent=2, sort_keys=False) + "\n"
