"""INT-2a — read `/api/health`'s FINISHED rows deterministically (no reliance on box load).

`/api/health` always answers 200: the finished rows, or — while the checks still run — the
CHECKING shape with the job in `pending`. This waits for that job to end (via `/api/jobs/<id>`) and
then asks for its kept answer with `job=<id>`, exactly as the page does. Copyright 2026 MoStack.
Licensed under the Apache License, Version 2.0.
"""
import time


def finished_health(c, params=None, secs=300, **kw):
    params = dict(params or {})
    r = c.get("/api/health", params=params, **kw)
    body = r.json() if r.status_code == 200 else {}
    if not body.get("checking"):
        return r
    jid = body["pending"]["job"]
    t0 = time.time()
    while time.time() - t0 < secs:
        j = c.get(f"/api/jobs/{jid}", **kw).json().get("job") or {}
        if j.get("state") in ("done", "failed", "cancelled"):
            break
        time.sleep(0.05)
    return c.get("/api/health", params={**params, "job": jid}, **kw)
