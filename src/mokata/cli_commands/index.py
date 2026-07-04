"""index / lat-check / coverage — the freshness index, @lat drift scan, and capability coverage report."""
from __future__ import annotations

import argparse

from ._common import (
    AdapterContract,
    negotiate,
    overlapping_capabilities,
    KnowledgeIndex,
    KnowledgeLayer,
    lat_check,
    _load_surface,
)


def cmd_index(args: argparse.Namespace) -> int:
    # B4 — build/refresh the per-file freshness index; report what changed + stale files.
    surface = _load_surface(args.path)
    store = surface.state
    data = store.read("knowledge_index")
    idx = KnowledgeIndex.from_dict(data) if data else KnowledgeIndex()
    if data is None:
        built = idx.build(surface.root)
        print(f"index: built {len(built)} file(s)")
    else:
        d = idx.diff(surface.root)
        reindexed = idx.reindex(surface.root)
        print(f"index: reindexed {len(reindexed)} changed, "
              f"+{len(d['added'])} added, -{len(d['removed'])} removed")
    store.write("knowledge_index", idx.to_dict())
    print(f"index: tracking {len(idx.entries)} file(s)")
    # Stage 35f: name the code-graph backend the refresh runs against — the wired adapter
    # (e.g. neo4j) when present, the grep floor when not. Degrade-clean: never a hard error.
    layer = KnowledgeLayer.from_surface(surface)
    if layer.uses_graph:
        print(f"index: code graph '{layer.backend_name}' wired — "
              f"`mokata lat-check` flags drift against it.")
    else:
        print("index: no code graph wired — refresh runs on the grep floor "
              "(`mokata lat-check` still flags concept drift lexically).")
    return 0


def cmd_coverage(args: argparse.Namespace) -> int:
    # A6 — treat the manifest's tools as adapters; report capability coverage + gaps.
    surface = _load_surface(args.path)
    m = surface.manifest
    adapters = [AdapterContract(name=tid, provides=[t.get("provides")],
                                kind=t.get("kind", "external"))
                for tid, t in m.tools.items() if t.get("provides")]
    report = negotiate(list(m.capabilities), adapters)
    print(report.render())
    overlaps = overlapping_capabilities(m)
    if overlaps:
        print("overlaps (resolved by manifest precedence):")
        for need, providers in overlaps.items():
            print(f"  {need}: {' > '.join(providers)}")
    return 0


def cmd_lat_check(args: argparse.Namespace) -> int:
    # B5 — scan @lat anchors and flag concept drift (degrades cleanly when absent).
    surface = _load_surface(args.path)
    report = lat_check(surface.root)
    print(report.render())
    return 1 if report.has_drift else 0


def register(sub, common):
    p_index = sub.add_parser(
        "index", parents=[common],
        help="build/refresh the freshness index (incremental); report stale files",
    )
    p_index.set_defaults(func=cmd_index)

    p_lat = sub.add_parser(
        "lat-check", parents=[common],
        help="scan @lat anchors and flag concept drift (degrades cleanly when absent)",
    )
    p_lat.set_defaults(func=cmd_lat_check)

    p_cov = sub.add_parser(
        "coverage", parents=[common],
        help="report capability coverage + unmet gaps + overlaps (A6/H6)",
    )
    p_cov.set_defaults(func=cmd_coverage)


__all__ = [
    "cmd_index",
    "cmd_coverage",
    "cmd_lat_check",
]
