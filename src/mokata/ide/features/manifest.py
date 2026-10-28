"""`GET /api/manifest` — what this IDE build serves: the contract version, every route (path,
method, kind, owning feature) and the feature scripts/styles the page loads. The worked example of
a feature module (0.0.26 P0): one `routes(reg, ctx)`, one `manifest.contract.json`, no server.py
edit. A light read — it answers from what the app was built with; nothing is walked."""

from __future__ import annotations

from typing import Any, Dict


def routes(reg, ctx) -> None:
    from ..contract import load

    def manifest(_request) -> Dict[str, Any]:
        return {"contract": load()["version"], "routes": reg.table(),
                "scripts": list(ctx.assets.get("scripts", [])),
                "styles": list(ctx.assets.get("styles", []))}

    reg.register("/api/manifest", manifest, kind="json", feature="manifest")
