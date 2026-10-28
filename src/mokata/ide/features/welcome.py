"""`/api/welcome` — S0, the first-run screen (0.0.26 P39 `FIRST-RUN`; `ide/firstrun.py`).

  GET  /api/welcome                 the repo (files counted, languages) and every capability:
                                    ready / limited / not-ready / building / failed / needs-setup,
                                    with the ONE fix line (doctor's) or a Build/Set-up action and
                                    its estimated time + disk. A job route (`welcome.scan`): the
                                    walk is the explorer's, so a big repo answers 202 + progress.
  GET  /api/welcome/consent?action= what the action writes, where, its cost — and the token
                                    (build-graph · build-meaning · init)
  POST /api/welcome/run             `{action, confirm}` -> `{id, job}`: the action as a P32 job,
                                    ONLY when `confirm` is the token of the consent in force now
                                    (409 `ConsentStale` otherwise, or when the action cannot
                                    run here now — nothing runs)

The IDE never installs a dependency and never goes online: a missing one is a fix line. `--demo`:
an empty sample; a run answers `sample: true` and starts nothing.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
from typing import Any, Dict


def routes(reg: Any, ctx: Any) -> None:
    import asyncio
    from starlette.responses import JSONResponse

    from .. import firstrun as F

    state = ctx.state

    def read(_request: Any):
        if state.demo:
            return lambda: F.welcome(state.root, demo=True)
        return lambda: F.welcome(state.root, state.jobs)
    reg.register("/api/welcome", read, kind="json", feature="welcome", job="welcome.scan",
                 job_label=lambda _r: f"welcome · {os.path.basename(os.path.abspath(state.root))}")

    def consent(request: Any) -> Dict[str, Any]:
        action = ctx.q(request, "action")
        if state.demo:
            raise ctx.bad_request("the SAMPLE has nothing to set up or build")
        try:
            return {**F.consent(state.root, action, state.jobs), "available": True,
                    "sample": False}
        except F.Unavailable as exc:
            return {"action": action, "available": False, "note": str(exc), "token": "",
                    "sample": False}
        except ValueError as exc:
            raise ctx.bad_request(str(exc)) from exc
    reg.register("/api/welcome/consent", consent, kind="json", feature="welcome")

    async def run(request: Any):
        b = await ctx.body(request)
        action = b.get("action") if isinstance(b.get("action"), str) else ""
        confirm = b.get("confirm") if isinstance(b.get("confirm"), str) else ""
        if state.demo:
            return JSONResponse({"sample": True, "note": "SAMPLE data — nothing is set up or "
                                 "built"})
        state.attach_loop()
        try:
            out = await asyncio.to_thread(F.start, state.root, action, confirm[:200], state.jobs)
        except ValueError as exc:
            return JSONResponse({"error": "BadRequest", "note": str(exc)}, status_code=400)
        except F.ConsentStale as exc:
            return JSONResponse({"error": "ConsentStale", "note": str(exc)}, status_code=409)
        return JSONResponse(out)
    reg.register("/api/welcome/run", run, kind="action", feature="welcome")
