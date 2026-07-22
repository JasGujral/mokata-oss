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
    if layer.uses_graph:
        sem = "with semantic search" if getattr(layer, "supports_semantic", False) \
            else "structural only"
        print(f"code graph active: '{layer.backend_name}' ({sem}).")
    else:
        print(f"code graph: floor '{layer.backend_name}' — no adopted graph. "
              f"Adopt one with `mokata graph adopt`.")
    return 0


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
    p_adopt.set_defaults(func=cmd_graph_adopt)

    p_status = gsub.add_parser(
        "status", parents=[common],
        help="report which code-graph backend actually answers today (degrade-clean)",
    )
    p_status.set_defaults(func=cmd_graph_status)


__all__ = ["cmd_graph_adopt", "cmd_graph_status", "register"]
