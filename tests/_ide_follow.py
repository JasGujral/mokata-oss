"""Ask the IDE the way a page does: the widest sync window, then the job's handle until the
answer is final.

A jobbed route (`/api/query`, `/api/impact`, `/api/graph`, `/api/search`, `/api/meaning`) answers
inside a short sync window or hands back a job handle (202). The window is a courtesy to a fast
box; a slow runner (a cold Windows disk, a loaded CI machine) is entitled to be slower than it,
and a test that reads the first reply as THE answer is asserting how fast the box is. The page
never does that: it follows the handle. This does the same, with a generous bound on the whole
wait (a hang still ends the test, with the last reply in hand). Copyright 2026 MoStack.
Licensed under the Apache License, Version 2.0.
"""
import time
from urllib.parse import urlencode

GET_JOBBED = ("/api/query", "/api/impact", "/api/graph")
POST_JOBBED = ("/api/search", "/api/meaning")


class Following:
    """A TestClient whose jobbed reads follow their job handle; everything else is the client's
    own (`__getattr__`)."""

    def __init__(self, client, within=120.0):
        self._c, self._within = client, within

    def __getattr__(self, name):
        return getattr(self._c, name)

    def get(self, url, params=None, **kw):
        path = url.split("?", 1)[0]
        if path not in GET_JOBBED:
            return self._c.get(url, params=params, **kw)
        full = url + (("&" if "?" in url else "?") + urlencode(params) if params else "")
        if "sync_ms=" in full or "job=" in full:       # a test that sets the window means it
            return self._c.get(full, **kw)
        sep = "&" if "?" in full else "?"
        r = self._c.get(f"{full}{sep}sync_ms=2000", **kw)
        end = time.monotonic() + self._within
        while r.status_code == 202 and time.monotonic() < end:
            r = self._c.get(f"{full}{sep}sync_ms=2000&job={r.json()['job']}", **kw)
        return r

    def post(self, url, json=None, **kw):
        if url.split("?", 1)[0] not in POST_JOBBED or not isinstance(json, dict):
            return self._c.post(url, json=json, **kw)
        if "sync_ms" in json or "job" in json:
            return self._c.post(url, json=json, **kw)
        body = {**json, "sync_ms": 2000}
        r = self._c.post(url, json=body, **kw)
        end = time.monotonic() + self._within
        while r.status_code == 202 and time.monotonic() < end:
            r = self._c.post(url, json={**body, "job": r.json()["job"]}, **kw)
        return r
