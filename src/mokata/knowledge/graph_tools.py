"""The tools that ARE real structural graphs (everything else is the lexical floor).

Its own leaf module (0.0.26 O12) so the per-prompt hook can ask "could a graph be answering here?"
without importing the knowledge layer. `neo4j` left at 0.0.18 lane D stage 14 — see
`layer._announce_removed_graph_chain`.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

GRAPH_TOOLS = ("code-review-graph", "serena")
