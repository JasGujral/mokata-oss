"""`GET /api/code-tree` — which tree the code views read right now (0.0.26 P41, MCP-FOLLOWS-WORKTREE).

Every Claude Code session works in its own git worktree (P31). When the IDE is focused on such a
session, the Code view, outline, ⌘P, word search, the structural query, impact and the graph views
read THAT worktree's files (the code graph answers as the main checkout's graph with the worktree's
changed files overlaid — "graph + N files from your worktree"). This route says which tree that is,
so the page can show it next to the focus chip instead of leaving the reader to guess. A light read:
one binding lookup, nothing walked.
"""

from __future__ import annotations

from typing import Any, Dict


def routes(reg, ctx) -> None:
    state = ctx.state

    def code_tree(_request) -> Dict[str, Any]:
        if state.demo:
            return {"sample": True, "root": "", "tree": "", "worktree": False, "label": "main",
                    "session": state.focus, "note": ""}
        return state.source.code_tree()

    reg.register("/api/code-tree", code_tree, kind="json", feature="codetree")
