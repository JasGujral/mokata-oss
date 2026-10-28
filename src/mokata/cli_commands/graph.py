"""graph — adopt / inspect the code graph (GR.S2).

`mokata graph adopt [tool]` PINS a real structural graph (code-review-graph) into the committed
manifest through the WriteGate (human-gated, P2) — the durable half of the detect-and-OFFER
flow. `mokata graph status` reports which backend actually answers today (degrade-clean).

Adoption is RECOMMENDED, never required (doc 00:42): the AST floor carries structural queries
without it, so a decline is first-class.
"""
from __future__ import annotations

import argparse
import os

from .. import MOKATA_DIR
from ..govern.ledger import AuditLedger
from ..knowledge import graph_adopt
from ..knowledge.graph_adopt import ADOPTABLE_GRAPH_TOOLS
from ._common import KnowledgeLayer, _load_surface


def cmd_graph_adopt(args: argparse.Namespace) -> int:
    root = args.path
    tool = getattr(args, "tool", None) or "code-review-graph"
    ledger = AuditLedger.from_mokata_dir(os.path.join(root, MOKATA_DIR))
    # A version-compat handshake runs when the tool is actually installed (the real client
    # reports its version); absent, adoption still pins it and the floor carries until it lands.
    client = None
    from ..knowledge.crg_client import CodeReviewGraphClient
    if tool == "code-review-graph":
        probe = CodeReviewGraphClient(name=tool, root=root)
        if probe.health(root):
            client = probe
    res = graph_adopt.adopt_graph(root, tool=tool, assume_yes=getattr(args, "yes", False),
                                  ledger=ledger, client=client)
    if res.committed:
        print(f"adopted {res.tool} — pinned into the code_graph chain.")
        return 0
    print(f"graph adopt not applied: {res.message}")
    return 1


def cmd_graph_status(args: argparse.Namespace) -> int:
    surface = _load_surface(args.path)
    layer = KnowledgeLayer.from_surface(surface)
    from ..knowledge.layer import graph_index_line
    pending = graph_index_line(layer)
    if pending:
        # 0.0.26 O13 (F2): installed/pinned is not answering — building / unbuilt / failed.
        print(pending)
        return 0
    if layer.uses_graph:
        sem = "with semantic search" if getattr(layer, "supports_semantic", False) \
            else "structural only"
        print(f"code graph active: '{layer.backend_name}' ({sem}).")
    else:
        print(f"code graph: floor '{layer.backend_name}' — no adopted graph. "
              f"Adopt one with `mokata graph adopt`.")
    return 0


def cmd_graph_build(args: argparse.Namespace) -> int:
    """0.0.26 O13 — build (or rebuild) code-review-graph's index for this repo NOW, in the
    foreground, with progress. The rebuild action doctor and the IDE name after a failed build.
    Ctrl+C stops it and removes the partial index. Holds the same per-repo lock as the background
    build, so it never runs beside one."""
    from ..detect import find_command
    from ..knowledge import crg_build
    root = args.path
    if find_command("code-review-graph") is None:
        from ..crg_resolve import resolve_crg
        print(resolve_crg().describe())
        return 1
    full = bool(getattr(args, "full", False)) or not crg_build.index_built(root)
    print(("building code-review-graph's index from scratch" if full else
           "bringing code-review-graph's index up to date (incremental; `--full` rebuilds)")
          + " — no time limit; Ctrl+C stops it and removes a partial index…", flush=True)
    res = crg_build.build_now(root, full=full,
                              on_progress=lambda s: print(f"  {s}", flush=True))
    if res.busy is not None:
        print(f"not started: {res.busy.line()} — `mokata graph status` shows when it is done.")
        return 1
    if res.ok:
        print(f"code-review-graph index built: {res.detail}.")
        return 0
    print(f"code-review-graph index NOT built — {res.error}. The partial index was removed; "
          f"the AST floor keeps answering.")
    return 130 if res.interrupted else 1


def register(sub, common):
    p_graph = sub.add_parser(
        "graph", parents=[common],
        help="adopt / inspect the code graph (code-review-graph); AST floor is the fallback",
    )
    gsub = p_graph.add_subparsers(dest="graph_action", required=True)

    p_adopt = gsub.add_parser(
        "adopt", parents=[common],
        help="pin a real code graph into the manifest (human-gated); AST floor stays the fallback",
    )
    p_adopt.add_argument("tool", nargs="?", default="code-review-graph",
                         choices=list(ADOPTABLE_GRAPH_TOOLS),
                         help="the graph tool to adopt (default: code-review-graph)")
    # 0.0.25 K — the human gate's non-interactive refusal tells the user to "re-run with --yes",
    # and this subcommand rejected the flag; `cmd_graph_adopt` already read `args.yes`.
    p_adopt.add_argument("--yes", action="store_true",
                         help="non-interactive (approve the gate)")
    p_adopt.set_defaults(func=cmd_graph_adopt)

    p_status = gsub.add_parser(
        "status", parents=[common],
        help="report which code-graph backend actually answers today (degrade-clean)",
    )
    p_status.set_defaults(func=cmd_graph_status)

    p_build = gsub.add_parser(
        "build", parents=[common],
        help="build or rebuild code-review-graph's index for this repo now (progress shown; "
             "Ctrl+C-safe)",
    )
    p_build.add_argument("--full", action="store_true",
                         help="rebuild from scratch even when an index exists (default: incremental)")
    p_build.set_defaults(func=cmd_graph_build)


__all__ = ["cmd_graph_adopt", "cmd_graph_build", "cmd_graph_status", "register"]
