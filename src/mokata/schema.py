"""A1 — Stack manifest schema + validation.

The manifest is the one declarative file that lists wired tools, the capabilities
("needs") they provide, and the *declared fallback order* for each capability. The
router (A2) and presence detection (A3) read nothing else to decide what to use.

Validation is layered, demonstrating mokata's own graceful-degradation rule (A3) at
the framework's own boundary:

  1. A built-in, dependency-free structural validator (always runs).
  2. An optional `jsonschema` pass when that library is present (richer messages).

If `jsonschema` is absent, validation degrades to the built-in checks — never fails
for lack of the optional dependency.
"""

from __future__ import annotations

from typing import Any, Dict, List

# Capability roles mokata's spine understands out of the box. Tools declare which
# one they `provides`, and capabilities are keyed by these names.
KNOWN_DETECT_TYPES = ("command", "python_module", "path", "obsidian", "always")
KNOWN_TOOL_KINDS = ("mcp", "cli", "library", "builtin", "external")
SUPPORTED_MANIFEST_VERSION = 1


# A JSON Schema (draft-2020-12 compatible) used only when `jsonschema` is installed.
# The built-in validator below enforces the same shape without the dependency.
MANIFEST_JSON_SCHEMA: Dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "mokata stack manifest",
    "type": "object",
    "required": ["manifest_version", "mokata", "profile", "capabilities", "tools"],
    "additionalProperties": True,
    "properties": {
        "manifest_version": {"type": "integer", "minimum": 1},
        "mokata": {
            "type": "object",
            "required": ["version"],
            "properties": {"version": {"type": "string"}},
        },
        "profile": {"type": "string", "minLength": 1},
        # Generic stack-wide toggle store (K-design): open-ended key/value so a future
        # setting (e.g. E8's execution mode) is stored/read the same way. Optional.
        "settings": {"type": "object"},
        "layers": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "properties": {"enabled": {"type": "boolean"}},
                "required": ["enabled"],
            },
        },
        "capabilities": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "required": ["fallback"],
                "properties": {
                    "description": {"type": "string"},
                    # Optional owning layer (K1): when set, the capability is routable
                    # only while that layer is enabled. Absent -> never layer-gated.
                    "layer": {"type": "string"},
                    "fallback": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                    },
                },
            },
        },
        "tools": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "required": ["provides", "kind", "detect"],
                "properties": {
                    "provides": {"type": "string"},
                    "kind": {"enum": list(KNOWN_TOOL_KINDS)},
                    "version": {"type": ["string", "null"]},
                    # Per-tool toggle (K1). Absent -> on. A disabled tool is treated
                    # as absent by the router.
                    "enabled": {"type": "boolean"},
                    "detect": {
                        "type": "object",
                        "required": ["type"],
                        "properties": {
                            "type": {"enum": list(KNOWN_DETECT_TYPES)},
                            "name": {"type": "string"},
                        },
                    },
                },
            },
        },
    },
}


def _require(cond: bool, msg: str, errors: List[str]) -> bool:
    if not cond:
        errors.append(msg)
    return cond


def _optional_jsonschema_errors(data: Any) -> List[str]:
    """Best-effort richer validation via `jsonschema`, if a *compatible* one is here.

    This is purely additive polish; the built-in structural validator is
    authoritative. So this degrades on ANY problem — not just a missing package:

      - jsonschema absent entirely            -> ImportError
      - jsonschema present but too old/broken  -> no Draft202012Validator attribute,
                                                  or it raises at construct/iterate time

    Many distros still ship jsonschema 3.2.0, which has no Draft202012Validator;
    relying on it must never break mokata's own validation.
    """
    try:
        import jsonschema  # type: ignore
    except ImportError:
        return []

    # Feature-detect the validator class; an old/incompatible jsonschema lacks it.
    validator_cls = getattr(jsonschema, "Draft202012Validator", None)
    if validator_cls is None:
        return []

    try:
        validator = validator_cls(MANIFEST_JSON_SCHEMA)
        errors: List[str] = []
        for err in sorted(validator.iter_errors(data), key=lambda e: list(e.path)):
            loc = "/".join(str(p) for p in err.path) or "<root>"
            errors.append(f"{loc}: {err.message}")
        return errors
    except Exception:
        # A present-but-incompatible jsonschema must not crash validation; fall back
        # to the authoritative built-in checks silently.
        return []


def validate_manifest(data: Any) -> List[str]:
    """Return a list of human-readable validation errors (empty == valid).

    Always runs the built-in structural checks. If `jsonschema` is importable it is
    run first for richer messages; its absence is silently fine (graceful degrade).
    """
    # Optional richer pass — additive only, and degrades on any failure (absent,
    # old, or incompatible jsonschema). The built-in checks below are authoritative.
    errors: List[str] = _optional_jsonschema_errors(data)

    # Built-in structural validation (authoritative, dependency-free).
    if not isinstance(data, dict):
        return ["manifest must be a JSON object"]

    mv = data.get("manifest_version")
    if _require(isinstance(mv, int), "manifest_version must be an integer", errors):
        _require(
            mv == SUPPORTED_MANIFEST_VERSION,
            f"manifest_version {mv} is unsupported (this build supports "
            f"{SUPPORTED_MANIFEST_VERSION})",
            errors,
        )

    mok = data.get("mokata")
    if _require(isinstance(mok, dict), "mokata block must be an object", errors):
        _require(
            isinstance(mok.get("version"), str),
            "mokata.version must be a string",
            errors,
        )

    _require(
        isinstance(data.get("profile"), str) and bool(data.get("profile")),
        "profile must be a non-empty string",
        errors,
    )

    if "settings" in data and not isinstance(data.get("settings"), dict):
        errors.append("settings must be an object")

    layers = data.get("layers", {})
    if not isinstance(layers, dict):
        errors.append("layers must be an object")
        layers = {}
    for name, layer in layers.items():
        if not isinstance(layer, dict) or not isinstance(layer.get("enabled"), bool):
            errors.append(f"layers.{name}.enabled must be a boolean")

    tools = data.get("tools")
    if not isinstance(tools, dict):
        errors.append("tools must be an object")
        tools = {}
    for tool_id, tool in tools.items():
        if not isinstance(tool, dict):
            errors.append(f"tools.{tool_id} must be an object")
            continue
        if not isinstance(tool.get("provides"), str):
            errors.append(f"tools.{tool_id}.provides must be a string")
        if "enabled" in tool and not isinstance(tool.get("enabled"), bool):
            errors.append(f"tools.{tool_id}.enabled must be a boolean")
        kind = tool.get("kind")
        if kind not in KNOWN_TOOL_KINDS:
            errors.append(
                f"tools.{tool_id}.kind '{kind}' invalid (one of {KNOWN_TOOL_KINDS})"
            )
        detect = tool.get("detect")
        if not isinstance(detect, dict):
            errors.append(f"tools.{tool_id}.detect must be an object")
        else:
            dt = detect.get("type")
            if dt not in KNOWN_DETECT_TYPES:
                errors.append(
                    f"tools.{tool_id}.detect.type '{dt}' invalid "
                    f"(one of {KNOWN_DETECT_TYPES})"
                )
            if dt in ("command", "python_module", "path") and not detect.get("name"):
                errors.append(
                    f"tools.{tool_id}.detect.name is required for type '{dt}'"
                )

    caps = data.get("capabilities")
    if not isinstance(caps, dict):
        errors.append("capabilities must be an object")
        caps = {}
    for need, cap in caps.items():
        if not isinstance(cap, dict):
            errors.append(f"capabilities.{need} must be an object")
            continue
        # Optional owning layer (K1): if declared it must be a string naming a layer
        # that actually exists, so a typo can't silently un-gate a capability.
        if "layer" in cap:
            layer_ref = cap.get("layer")
            if not isinstance(layer_ref, str):
                errors.append(f"capabilities.{need}.layer must be a string")
            elif layer_ref not in layers:
                errors.append(
                    f"capabilities.{need}.layer references undeclared layer "
                    f"'{layer_ref}'"
                )
        fallback = cap.get("fallback")
        if not isinstance(fallback, list) or not fallback:
            errors.append(f"capabilities.{need}.fallback must be a non-empty array")
            continue
        for tool_id in fallback:
            # Referential integrity: every fallback entry must be a declared tool
            # that actually provides this capability.
            if tool_id not in tools:
                errors.append(
                    f"capabilities.{need}.fallback references unknown tool "
                    f"'{tool_id}'"
                )
            elif isinstance(tools.get(tool_id), dict):
                provides = tools[tool_id].get("provides")
                if provides != need:
                    errors.append(
                        f"capabilities.{need}.fallback lists '{tool_id}', but that "
                        f"tool provides '{provides}', not '{need}'"
                    )

    return errors


def is_valid(data: Any) -> bool:
    return not validate_manifest(data)
