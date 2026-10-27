"""IDE feature modules (0.0.26 P0 — ID-API-CONTRACT).

Every module here (name not starting with `_`) is a FEATURE: `server.build_app` imports it in name
order and calls its `routes(reg, ctx)`, which registers the feature's routes on the shared
`routes.Registry` (see `mokata.ide.routes` for the kinds and the guard every route sits behind).
Each feature's routes are listed in `<name>.contract.json` beside it (`mokata.ide.contract`).
Its frontend, if any, is `static/features/<name>.js` (+ `.css`), loaded by the page with no edit
to index.html. `manifest.py` is the worked example.
"""
