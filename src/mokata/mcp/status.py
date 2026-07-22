"""MCP-R.D0 · R6 — the ONE machine-legible dispatch-status vocabulary.

Every mokata MCP tool result carries a `status`, and the agent branches on it without prose-parsing
(P16 legibility). This module is the SINGLE SOURCE OF TRUTH for the dispatch-outcome vocabulary the
`_serve` wrapper guarantees, so those statuses are never magic strings scattered across tools and
tests — `mcp/server` and the D0 suite both import them from here.

The vocabulary is a SUPERSET, not a rename: it names the canonical dispatch outcomes the front door
speaks (the three the wrapper adds — `running`, `timed_out`, plus the reclaimed `error` — alongside
the propose/consent statuses tools already emit: `proposed`, `refused`, `blocked`, `committed`,
`degraded`). Tools keep their own finer-grained sub-statuses (`ok`, `noop`, `unchanged`, …) — those
are NOT dispatch outcomes and are passed through byte-identical, so no existing caller breaks.

Pure stdlib — never imports the optional MCP SDK.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

# Propose/consent outcomes (already emitted by the write + approve tools; unchanged here):
PROPOSED = "proposed"       # a durable write staged, awaiting a human-minted approval — instant
REFUSED = "refused"         # the write is refused (read-only dial, or a bad/expired approval)
BLOCKED = "blocked"         # a gate or a hard security block stopped the write
COMMITTED = "committed"     # an approved write landed
DEGRADED = "degraded"       # a capability ran on its documented fallback (honest, not silent)

# Dispatch outcomes the `_serve` front door adds (MCP-R.D0):
RUNNING = "running"         # a long op is in flight (reserved for the R4 progress surface)
TIMED_OUT = "timed_out"     # the op exceeded mokata's bounded MCP-surface budget
ERROR = "error"            # an uncaught exception / non-dict return, reclaimed into mokata's voice

# The whole vocabulary — import THIS, never a bare string.
STATUS_VOCAB = frozenset({
    PROPOSED, REFUSED, BLOCKED, RUNNING, TIMED_OUT, DEGRADED, COMMITTED, ERROR,
})
