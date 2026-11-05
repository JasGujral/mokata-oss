"""0.0.27 P46 `CHANGE-EXPLAINER` — a review page that explains one change set.

mokata computes FACTS (a deterministic, change-set-bounded bundle) and renders the page; Claude Code
— on the user's own login, through `/mokata:explain` — writes the INFERENCES, each one cited. mokata
never calls a model and never needs a key. The accepted rule is `reports/P46.rule.md` (rev 2 plus
the coordinator's delta rulings):

  * `safedir`  — the store's directories, opened one component at a time without following a link
  * `bundle`   — the fact bundle (diff, graph both trees, complexity, tests, conventions, gates)
  * `ground`   — what a cite is, and the per-block grounding rule `explain_save` enforces
  * `render`   — the page (zero scripts), its positive allowlist validator, the Mermaid allowlist
  * `store`    — pages + sidecars + the ledger row, staleness, retention, the hand-off records
  * `runner`   — the `explain.bundle` job in the MCP process: a status file, a poll lease, Cancel

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""
