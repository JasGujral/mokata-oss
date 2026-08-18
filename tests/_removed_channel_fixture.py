"""Plant a repo that upgraded INTO the removal — the offender this tree cannot otherwise contain.

0.0.18 stage 10 (lane D slice 1), doc 85 §7i.

WHY THIS FILE EXISTS. The slice removes `obsidian` and `native-memory`, and the refusal that
makes the removal honest can only fire against a repo whose `.mokata/manifest.json` still NAMES
one. After the slice lands, nothing in this tree writes such a manifest — `TOOL_CATALOG` no longer
holds the entries and no profile wires them — so a test that built its fixture from the live
catalog would pass having exercised nothing. That is precisely "a guard whose offenders you just
fixed grades nothing": the offender has to be PLANTED, and planting it means writing the manifest
shape 0.0.17 actually wrote.

⚠ THE BLOCKS BELOW ARE A RECORD, NOT A SHIM. They are what `profiles.TOOL_CATALOG` held at
`v0.0.17` — copied here verbatim, in the test tree, where they describe the disks users have
rather than the code we ship. Nothing in `src/` may grow a copy of them: a legacy shape kept in
production is the second code path doc 85 §7d exists to forbid, and a legacy READER of it is the
one thing §7d's exception explicitly rules out.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import os

from mokata import MOKATA_DIR

__all__ = ["LEGACY_TOOL_BLOCKS", "NEO4J_TOOL_BLOCK", "plant_removed_chain",
           "plant_obsidian_vault", "plant_neo4j_graph_chain"]

# `profiles.TOOL_CATALOG["obsidian" | "native-memory"]` as v0.0.17 shipped them, plus the
# `enabled` flag `init_repo` adds when it writes a tool into a manifest.
LEGACY_TOOL_BLOCKS = {
    "obsidian": {
        "provides": "memory_store",
        "kind": "external",
        "version": None,
        "detect": {"type": "obsidian"},
        "deprecated": "0.0.17",
        "enabled": True,
    },
    "native-memory": {
        "provides": "memory_store",
        "kind": "external",
        "version": None,
        "detect": {"type": "command", "name": "claude"},
        "deprecated": "0.0.17",
        "enabled": True,
    },
}

# `code_graph`'s removed provider (0.0.18 stage 14). ⚠ THIS BLOCK IS NOT `TOOL_CATALOG["neo4j"]`
# AS THE CATALOG HELD IT — it is what `docs/how-to/use-a-codebase-graph.md` told users to run:
#
#     mokata config set tools.neo4j '{"provides":"code_graph", … ,"config":{"uri_env":…}}'
#     mokata config set capabilities.code_graph.fallback '["neo4j","ripgrep","grep"]'
#
# and that distinction is the whole reason this fixture is separate from the two above. `neo4j` was
# wired by NO profile (P8), so `init_repo` never wrote it into a manifest by itself: the only way a
# repo has it is that a human typed the documented command, WITH the `config` block naming env
# vars. A fixture copied from the catalog would have graded a manifest nobody holds.
#
# ⚠ AND THE `detect.type` IS `python_module`, NOT A REMOVED STRATEGY. That is why no manifest here
# needs `schema.REMOVED_DETECT_TYPES` to stay parseable — see `test_stage14_neo4j_removal`.
NEO4J_TOOL_BLOCK = {
    "provides": "code_graph",
    "kind": "external",
    "version": None,
    "detect": {"type": "python_module", "name": "neo4j"},
    "config": {"uri_env": "NEO4J_URI", "user_env": "NEO4J_USERNAME",
               "password_env": "NEO4J_PASSWORD"},
    "deprecated": "0.0.17",
    "enabled": True,
}


def plant_removed_chain(root, chain, tool_blocks=None):
    """Rewrite an initialized repo's committed `memory_store` chain to `chain`, adding whatever
    tool blocks it names that the CURRENT catalog can no longer supply.

    Returns nothing; the caller re-loads its own `Surface`. Writes only inside `root`."""
    from mokata import profiles
    path = os.path.join(root, MOKATA_DIR, "manifest.json")
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    data["capabilities"]["memory_store"]["fallback"] = list(chain)
    blocks = dict(LEGACY_TOOL_BLOCKS)
    blocks.update(tool_blocks or {})
    for tid in chain:
        if tid in profiles.TOOL_CATALOG:
            data.setdefault("tools", {}).setdefault(
                tid, dict(profiles.TOOL_CATALOG[tid], enabled=True))
        else:
            data.setdefault("tools", {})[tid] = dict(blocks[tid])
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    return path


def plant_obsidian_vault(vault, subjects=("db.engine", "cache.ttl")):
    """Write markdown notes into `vault` in the exact shape `ObsidianBackend.put` wrote them —
    a `# memory:` heading, the value, and the authoritative item dict in a fenced JSON block.

    The BYTES are what matter, not the class: the class is gone, and a fixture that imported it
    to build its own offender would be a legacy reader wearing a test's clothes. Returns the
    filenames written, so a test can assert afterwards that every one is still there."""
    from mokata.memory import MemoryItem
    os.makedirs(vault, exist_ok=True)
    written = []
    for subject in subjects:
        item = MemoryItem.create(subject, "planted", source="fixture", author="fixture")
        name = f"{item.id}.md"
        body = (f"# memory: {item.subject}  ({item.mtype})\n\n"
                f"{item.value}\n\n"
                f"```json\n{json.dumps(item.to_doc(), indent=2)}\n```\n")
        with open(os.path.join(vault, name), "w", encoding="utf-8") as fh:
            fh.write(body)
        written.append(name)
    return written


def plant_neo4j_graph_chain(root, chain=("neo4j", "ripgrep", "grep")):
    """Rewrite an initialized repo's committed `code_graph` chain to `chain`, adding the `neo4j`
    tool block the current catalog can no longer supply.

    The `code_graph` twin of `plant_removed_chain`, and separate rather than parameterised because
    the two capabilities need DIFFERENT tool blocks and different removed shapes — folding them
    would give one of them the other's fixture, which is the mistake `removed_channels_in`'s
    `kind` argument exists to prevent one layer down.

    Returns the manifest path; the caller re-loads its own `Surface`. Writes only inside `root`."""
    from mokata import profiles
    path = os.path.join(root, MOKATA_DIR, "manifest.json")
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    data["capabilities"]["code_graph"]["fallback"] = list(chain)
    for tid in chain:
        if tid in profiles.TOOL_CATALOG:
            data.setdefault("tools", {}).setdefault(
                tid, dict(profiles.TOOL_CATALOG[tid], enabled=True))
        elif tid == "neo4j":
            data.setdefault("tools", {})[tid] = dict(NEO4J_TOOL_BLOCK)
        else:                                        # pragma: no cover - caller error
            raise KeyError("no planted block for %r" % (tid,))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    return path
