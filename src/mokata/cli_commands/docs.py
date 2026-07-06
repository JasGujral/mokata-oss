"""docs — point at the published docs site (`mokata docs [topic]`).

Read-only, local-first: no arg lists the shipped topics with their live-site URLs through the A4
legibility helpers; a known topic prints that doc's site URL (+ its title). An unknown topic
prints the list plus a hint on stderr and exits non-zero. It NEVER fetches — it only resolves and
prints URLs. Colour + a Unicode box only on a real TTY; a piped / NO_COLOR run is plain ASCII with
zero escape codes.
"""
from __future__ import annotations

import argparse
import sys


def cmd_docs(args: argparse.Namespace) -> int:
    from ..legibility import _color_enabled
    from ..product_docs import BASE_URL, find_topic, render, render_topic_list

    topic = getattr(args, "topic", None)
    color = _color_enabled()
    ascii_only = not color

    if topic and find_topic(topic) is None:
        # Unknown topic: still offer the list (stdout), name the miss + point at the site (stderr).
        print(render_topic_list(ascii_only=ascii_only))
        print(f"mokata docs: no such topic '{topic}'. Pick one from the list above, or browse "
              f"the full site at {BASE_URL}", file=sys.stderr)
        return 1

    print(render(topic, ascii_only=ascii_only, color=color))
    return 0


def register(sub, common):
    p_docs = sub.add_parser(
        "docs", parents=[common],
        help="point at the published docs site; no topic lists them with URLs (read-only)",
    )
    p_docs.add_argument(
        "topic", nargs="?", default=None,
        help="a doc topic (e.g. getting-started, concepts/execution-model); omit to list them",
    )
    p_docs.set_defaults(func=cmd_docs)


__all__ = ["cmd_docs", "register"]
