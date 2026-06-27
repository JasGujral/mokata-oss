"""Tool catalog + profiles — the raw material `mokata init` (A7) assembles into a
manifest.

`TOOL_CATALOG` is everything the spine knows how to *detect* and *route to*. Each
entry is a manifest-ready tool definition. Capabilities list their declared fallback
order over these tools, every chain ending in an always-present pure fallback so a
need can always resolve to *something* (graceful degradation, A3 / P6).

Profiles are named bundles selecting which layers + capabilities a fresh config turns
on, each yielding a *deterministic* enabled set (K2). A profile names, per capability,
the exact provider chain it wires — so `minimal`/`standard`/`full`/`custom` differ in
a reproducible, reviewable way, not by chance of what's installed.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

# Every tool the spine can detect and route to. `provides` ties a tool to a capability;
# `detect` is consumed by detect.Detector. Versions are informational only here.
TOOL_CATALOG: Dict[str, Dict[str, Any]] = {
    # --- code_graph providers (graph backend, with grep as the universal floor) ---
    "code-review-graph": {
        "provides": "code_graph",
        "kind": "mcp",
        "version": None,
        "detect": {"type": "command", "name": "code-review-graph"},
    },
    "serena": {
        "provides": "code_graph",
        "kind": "mcp",
        "version": None,
        "detect": {"type": "command", "name": "serena"},
    },
    "ripgrep": {
        "provides": "code_graph",
        "kind": "cli",
        "version": None,
        "detect": {"type": "command", "name": "rg"},
    },
    "neo4j": {
        # Opt-in external graph DB (Stage 35f). NOT wired by any default profile (P8): a user
        # adds it to the code_graph chain + sets NEO4J_* env. Degrades to the grep floor when
        # the driver/env/DB is absent. URI + credentials via env var only (never inline).
        "provides": "code_graph",
        "kind": "external",
        "version": None,
        "detect": {"type": "python_module", "name": "neo4j"},
    },
    "grep": {
        "provides": "code_graph",
        "kind": "builtin",
        "version": None,
        # Universal floor: grep is assumed present on any POSIX dev machine. Treated
        # as the always-available last resort so code_graph never hard-fails.
        "detect": {"type": "always"},
    },
    # --- memory_store providers (storage only; the memory logic is mokata's own) ---
    "native-memory": {
        "provides": "memory_store",
        "kind": "external",
        "version": None,
        "detect": {"type": "command", "name": "claude"},
    },
    "obsidian": {
        "provides": "memory_store",
        "kind": "external",
        "version": None,
        # Stage 24A: detect the real per-OS Obsidian config dirs (and a configured
        # `config.vault`), not the bare ~/.obsidian that usually doesn't exist.
        "detect": {"type": "obsidian"},
    },
    "postgres": {
        # Opt-in hosted/remote memory backend. NOT wired by any default profile (P8
        # local-first): a user adds it explicitly via `mokata config set`. The DSN comes
        # from an env var (config.dsn_env) — never inline. Degrades to SQLite if psycopg
        # is absent or the DB is unreachable.
        "provides": "memory_store",
        "kind": "external",
        "version": None,
        "detect": {"type": "python_module", "name": "psycopg"},
    },
    "sqlite": {
        "provides": "memory_store",
        "kind": "library",
        "version": None,
        # Python ships sqlite3 in the stdlib -> the guaranteed default memory backend.
        "detect": {"type": "python_module", "name": "sqlite3"},
    },
}

# Declared fallback order for each capability (most-preferred first).
CAPABILITY_FALLBACKS: Dict[str, Dict[str, Any]] = {
    "code_graph": {
        "description": "Structural codebase queries (callers/callees, imports, "
        "blast-radius); grep is the universal fallback.",
        "fallback": ["code-review-graph", "serena", "ripgrep", "grep"],
    },
    "memory_store": {
        "description": "Where persistent/decision memory is stored; SQLite is the "
        "guaranteed default backend.",
        "fallback": ["native-memory", "obsidian", "sqlite"],
    },
}

# The spine's coarse layers. `mokata init` flips these per profile; the router (K1)
# enforces a disabled layer by dropping its capabilities at resolution time.
ALL_LAYERS = ("engine", "knowledge", "memory", "governance")

# Which layer owns each capability. A capability is routed only while its owning layer
# is enabled (K1). Written into every generated manifest's capability entry so the
# router can enforce it from the committed artifact alone.
CAPABILITY_LAYERS: Dict[str, str] = {
    "code_graph": "knowledge",
    "memory_store": "memory",
}


# Profiles: which layers are on, and — per capability — the exact provider chain to
# wire (a subset of CAPABILITY_FALLBACKS, most-preferred first). `standard` wires lean,
# local defaults; `full`/`custom` wire every known provider. This is what makes each
# profile's enabled set deterministic (K2).
PROFILES: Dict[str, Dict[str, Any]] = {
    "minimal": {
        "description": "Just the spec-driven TDD engine. No external capabilities, "
        "zero network egress.",
        "layers": {"engine": True, "knowledge": False, "memory": False,
                   "governance": True},
        "capabilities": {},
    },
    "standard": {
        "description": "The default. Engine + codebase graph + decision memory on lean, "
        "local, dependency-free defaults (grep + SQLite). Switch with "
        "`mokata init --profile full` for every graph/memory provider.",
        "layers": {"engine": True, "knowledge": True, "memory": True,
                   "governance": True},
        "capabilities": {
            "code_graph": ["ripgrep", "grep"],
            "memory_store": ["sqlite"],
        },
    },
    "full": {
        "description": "Everything the spine can wire: every known graph and memory "
        "provider (each degrades to its floor if absent). Opt in: `mokata init --profile full`.",
        "layers": {"engine": True, "knowledge": True, "memory": True,
                   "governance": True},
        "capabilities": {
            "code_graph": ["code-review-graph", "serena", "ripgrep", "grep"],
            "memory_store": ["native-memory", "obsidian", "sqlite"],
        },
    },
    "custom": {
        "description": "Everything wired as a starting point — hand-tune the layer "
        "and tool toggles from here.",
        "layers": {"engine": True, "knowledge": True, "memory": True,
                   "governance": True},
        "capabilities": {
            "code_graph": ["code-review-graph", "serena", "ripgrep", "grep"],
            "memory_store": ["native-memory", "obsidian", "sqlite"],
        },
    },
}

DEFAULT_PROFILE = "standard"


def profile_names() -> List[str]:
    return list(PROFILES)


def _profile_spec(profile: str) -> Dict[str, Any]:
    if profile not in PROFILES:
        raise ValueError(
            f"unknown profile '{profile}'; choose one of {profile_names()}"
        )
    return PROFILES[profile]


def profile_enabled_set(profile: str) -> Dict[str, Any]:
    """The deterministic enabled set a profile yields (K2).

    Returns a normalized, order-stable description:
      - layers:       tuple of enabled layer names (manifest order)
      - capabilities: {need -> ordered provider chain} for wired capabilities
      - tools:        sorted tuple of every tool the profile wires
    Same profile in, same set out — every time.
    """
    spec = _profile_spec(profile)
    caps: Dict[str, List[str]] = {
        need: list(chain) for need, chain in spec["capabilities"].items()
    }
    tools: Tuple[str, ...] = tuple(
        sorted({tid for chain in caps.values() for tid in chain})
    )
    layers: Tuple[str, ...] = tuple(
        name for name, on in spec["layers"].items() if on
    )
    return {"layers": layers, "capabilities": caps, "tools": tools}


def build_manifest_data(profile: str, mokata_version: str) -> Dict[str, Any]:
    """Assemble a complete, schema-valid manifest dict for a profile.

    Only the tools reachable from the profile's wired capabilities are written, so the
    file describes exactly the stack this profile wires — nothing dangling. Each
    capability records its owning layer (K1) and each tool an explicit `enabled` flag
    (per-tool toggle, default on). A `settings` block is included as the generic
    key/value store later toggles (e.g. E8's execution mode) read from (K-design).
    """
    spec = _profile_spec(profile)
    wired: Dict[str, List[str]] = spec["capabilities"]

    capabilities: Dict[str, Any] = {}
    tools: Dict[str, Any] = {}
    for need, chain in wired.items():
        capabilities[need] = {
            "description": CAPABILITY_FALLBACKS[need]["description"],
            "layer": CAPABILITY_LAYERS[need],
            "fallback": list(chain),
        }
        for tool_id in chain:
            tool = dict(TOOL_CATALOG[tool_id])
            tool["enabled"] = True
            tools[tool_id] = tool

    layers = {name: {"enabled": bool(on)} for name, on in spec["layers"].items()}

    return {
        "manifest_version": 1,
        "mokata": {"version": mokata_version},
        "profile": profile,
        "layers": layers,
        "capabilities": capabilities,
        "tools": tools,
        "settings": {},
    }
