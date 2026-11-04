"""`/api/hints` — the next step for each session card (0.0.27 P14 `ID-NEXT-STEP-HINTS`).

  GET /api/hints?session=<id>[,<id>...]
        each named session's next step, DERIVED from what mokata recorded for its run (approach,
        spec, test record, gates, review, finish — `mokata.hints`), each hint citing the fact it
        came from. One batch: the shared stores are read once for every session asked about. The
        Activity panel asks once per load and again only for the sessions a pushed change named.
        A store that cannot be read answers `status: unknown` naming it — never "nothing to do".
        Reads only: nothing is written, spawned, or asked of a model; every command a hint offers
        asks for its own consent when the person runs it.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from typing import Any, Dict

#: The most sessions one request may name (the panel shows at most a screenful of cards).
MAX_SESSIONS = 50


def routes(reg: Any, ctx: Any) -> None:
    state = ctx.state

    def hints(request: Any) -> Dict[str, Any]:
        raw = ctx.q(request, "session")
        ids = [s for s in raw.split(",") if s] if raw else []
        if not ids:
            raise ctx.bad_request("session is required: one or more session ids, comma-separated")
        if len(ids) > MAX_SESSIONS or any(len(s) > 200 for s in ids):
            raise ctx.bad_request(f"at most {MAX_SESSIONS} session ids of at most 200 characters")
        return state.source.hints(list(dict.fromkeys(ids)))

    reg.register("/api/hints", hints, kind="json", feature="hints")
