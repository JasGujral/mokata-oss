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

# ⚠ THIS MODULE NO LONGER IMPORTS `deprecation.REMOVAL_RELEASE`, AND THAT IS THE END OF STAGE 9's
# FINDING RATHER THAN A REGRESSION OF IT. The release was stated ONCE, here, because this catalog
# is copied verbatim into every repo's `.mokata/manifest.json` and a literal would have been the
# same false claim on the most durable surface mokata has (`REMOVAL-RELEASE-ALREADY-PASSED`, 0.0.18
# stage 9). The only consumer was `neo4j`'s `"deprecated": REMOVAL_RELEASE` marker, and stage 14
# removed that entry, so there is no longer a release to state. The deprecation vocabulary stays in
# this comment ON PURPOSE: `_deprecation_removal.src_release_pins` selects its corpus by that
# vocabulary, so a file that stopped mentioning deprecation would fall OUT of the guard that exists
# to stop a release literal reappearing here (doc 85 §7j — the axis nobody ranges over).

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
    "ast": {
        # GR.S2: the embedded stdlib-AST floor (GR.S1) promoted to a first-class, routable
        # provider so a manifest can NAME the thing that actually answers on Python repos.
        # Builtin + zero-dep: `detect: python_files` marks it present iff the repo holds a
        # `.py` file, so a zero-Python repo routes straight past it to the lexical floor
        # (byte-identical to before). It is a FLOOR (is_graph=False), not the adopted graph.
        "provides": "code_graph",
        "kind": "builtin",
        "version": None,
        "detect": {"type": "python_files"},
    },
    #
    # SIMP.S3 (0.0.18, lane D stage 14): `neo4j` is REMOVED from this catalog. It was the one
    # `code_graph` provider marked DEPRECATED here, and — as with the two memory backends below —
    # the deletion is a write to every FUTURE user's disk rather than a source edit, because
    # `init_repo` copies this dict into `.mokata/manifest.json`.
    #
    # ⚠ AND UNLIKE SLICE 1's, THIS ONE COULD NOT INVALIDATE AN EXISTING MANIFEST, WHICH IS WHY
    # `schema.REMOVED_DETECT_TYPES` DID NOT GROW. Slice 1 removed the `obsidian` DETECT STRATEGY
    # with its backend, and every manifest naming it stopped parsing — so `Surface.load` failed
    # before the command the removal notice told the user to run. `neo4j` detected by
    # `{"type": "python_module", "name": "neo4j"}`, and `python_module` is SHARED with postgres,
    # pgvector and sqlite: it survives untouched, so nothing about a committed manifest becomes
    # unparseable. The exposure here is a catalog KEY inside a `code_graph` chain, not a detect
    # type, and the schema's referential-integrity rule already makes such a manifest
    # self-contained — a `capabilities.code_graph.fallback` entry must name a tool declared in the
    # SAME FILE, so nothing at load time consults this dict at all. Reusing slice 1's mechanism
    # here would have added a member to `REMOVED_DETECT_TYPES` that nothing could ever read.
    "grep": {
        "provides": "code_graph",
        "kind": "builtin",
        "version": None,
        # Universal floor: grep is assumed present on any POSIX dev machine. Treated
        # as the always-available last resort so code_graph never hard-fails.
        "detect": {"type": "always"},
    },
    # --- memory_store providers (storage only; the memory logic is mokata's own) ---
    #
    # SIMP.S3 (0.0.18, lane D slice 1): `native-memory` and `obsidian` are REMOVED from this
    # catalog. ⚠ THAT IS A WRITE TO EVERY FUTURE USER'S DISK, not a source edit: `init_repo`
    # copies TOOL_CATALOG into `.mokata/manifest.json`, so an entry left here would go on
    # advertising a backend that no longer exists — which is stage 9's finding (a value in this
    # dict reaching the most durable surface mokata has) arriving one release later as a whole
    # tool. Manifests ALREADY on disk still carry the two entries; what happens to those repos is
    # `memory.selection._refuse_removed_memory_chain`, not a migration written here.
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
    "pgvector": {
        # DB.S4 — the OPT-IN semantic memory store (Postgres + the pgvector extension). NOT wired
        # by any default profile and deliberately not detectable-into-use: pgvector needs
        # `CREATE EXTENSION`, which ADR-54 keeps OFF the vanilla-Postgres golden path, so a team
        # opts in explicitly (`mokata config set` + `mokata team init --vector`). Degrades to the
        # SQLite floor when the DSN is unset, psycopg/pgvector is absent, the DB is unreachable,
        # or the index's embedder stamp does not match the configured embedder.
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
        "blast-radius); the embedded AST floor answers on Python, grep is the "
        "universal fallback.",
        "fallback": ["code-review-graph", "serena", "ast", "ripgrep", "grep"],
    },
    "memory_store": {
        "description": "Where persistent/decision memory is stored; SQLite is the "
        "guaranteed default backend.",
        "fallback": ["sqlite"],
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
#
# ⚠ SIMP.S3 (0.0.18): every profile now wires the SAME `memory_store` chain, and that is not an
# oversight to be "fixed" by inventing a longer one. The two backends `full` had over `standard`
# were the removed ones; what is left — postgres / pgvector — is DELIBERATELY not wired by any
# profile (ADR-54: a DSN and `CREATE EXTENSION` are an explicit opt-in, never a detection).
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
        "`mokata init --profile full` for every graph provider.",
        "layers": {"engine": True, "knowledge": True, "memory": True,
                   "governance": True},
        "capabilities": {
            # GR.S2: names the AST floor that actually answers on Python repos (was
            # ["ripgrep","grep"] while `ast` answered unnamed — the drift bug). A real
            # graph (code-review-graph) is offered at setup and pinned via `graph adopt`.
            "code_graph": ["ast", "ripgrep", "grep"],
            "memory_store": ["sqlite"],
        },
    },
    "full": {
        "description": "Everything the spine can wire: every known graph provider (each "
        "degrades to its floor if absent). Memory is the SQLite floor here as everywhere — a "
        "shared Postgres is an explicit opt-in, never a profile. "
        "Opt in: `mokata init --profile full`.",
        "layers": {"engine": True, "knowledge": True, "memory": True,
                   "governance": True},
        "capabilities": {
            "code_graph": ["code-review-graph", "serena", "ast", "ripgrep", "grep"],
            "memory_store": ["sqlite"],
        },
    },
    "custom": {
        "description": "Everything wired as a starting point — hand-tune the layer "
        "and tool toggles from here.",
        "layers": {"engine": True, "knowledge": True, "memory": True,
                   "governance": True},
        "capabilities": {
            "code_graph": ["code-review-graph", "serena", "ast", "ripgrep", "grep"],
            "memory_store": ["sqlite"],
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
