
Promotes `0.0.9rc1` unchanged (same code; version fields and notes only). Install with
`pip install mokata`.

**Installs from PyPI, no clone — and the MCP server works out of the box.**

- **pip-installable:** `pip install mokata` now ships everything (command templates, hooks, and
  Agent Skills are packaged in the wheel) — no repo clone needed. The bundled MCP server's SDK is a
  default dependency on **Python 3.10+**, so `mokata-mcp` runs out of the box (on 3.9 the CLI still
  works; the MCP server prints a clear upgrade message).
- **One-command wiring:** `mokata setup claude` registers the MCP server at an absolute path,
  verifies the connection (`CONNECTED ✓`), and wires commands + skills + the status line. New
  `mokata mcp start | status | install`, and a `/mokata:mcp` repair skill that re-registers the
  server from inside Claude Code.
- **Skills stay fresh on update:** re-running `mokata setup claude` now syncs the Agent Skills and
  prunes stale/removed mokata skills (your own skills are never touched).

**Progress you can see, and a review you can trust.**

- **Redesigned always-on status badge:** the full brainstorm → spec → develop → review → ship arc,
  each stage marked done/current/pending, with a live `develop [done/total]` counter. Configure via
  `settings.ux.badge_verbosity` (`full` default | `minimal`). `/mokata:progress` now shows the
  user-stage arc and what's pending this session.
- **Independent review closes the pipeline:** `/mokata:review` runs as a **fresh-context subagent**
  by default (re-deriving its verdict from a self-contained brief, not the builder's context), and
  `/mokata:ship` now **blocks unless a passing review is on record** for the run — evidence over
  claims. Degrades cleanly to inline review where a harness has no subagents; toggle with
  `settings.review.independent`. Fixes review not reliably firing after `develop`.
- **Brainstorm saves a plan:** when you approve an approach, the design write-up is saved as a plan
  file; `mokata plan list | show | export` keeps an editable copy in your repo.

**Under the hood.** Reproducible, Sigstore-signed wheels published to PyPI from CI via OIDC Trusted
Publishing (public repo only), a fail-closed release pipeline that won't tag on a red matrix, and
internal refactors (`cli`/`mcp` split into packages) with no behavior change. Local-first, no
telemetry, Apache-2.0.

**Known issue** (fix scheduled for the next release): invoking `mokata-hook statusline` /
`session-start` by hand with stdin attached to a pipe that never closes can block until the pipe
does. Claude Code's own hook invocation (payload + EOF) is unaffected — normal use never hits this.

