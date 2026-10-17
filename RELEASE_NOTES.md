mokata **0.0.24**. Set up or upgrade with the same instruction, in your repo:
`uv tool install --upgrade mokata` (or `pipx install --force mokata`), then `mokata setup claude`.
Requires **Python ≥ 3.10**, and for the Claude Code integration **Claude Code ≥ 2.1.0**. The plugin
route needs **[`uv`](https://docs.astral.sh/uv/) on your PATH**.

---

## 0.0.24 — "A patch for slow suites on Windows, and a lighter CI gate."

**0.0.23 never reached PyPI** — its release workflow's tests were cancelled at a 22-minute ceiling
(no test failed). 0.0.24 carries every 0.0.23 change, so read the 0.0.23 section of the CHANGELOG
too; this is the first published release since 0.0.22.

Two reports against 0.0.22 on Windows (OSS #82, #83) came down to three causes. One — a Windows
`PermissionError` when another mokata process held the state file, which lost an approach approval —
was already fixed in 0.0.23. This patch fixes the rest.

**`baseline` over MCP can wait for a slow suite.** The 120 s budget is now
`settings.mcp.test_timeout_seconds` (up to 600 s):

    mokata config set settings.mcp.test_timeout_seconds 300

**An approved spec commits from its id.** `spec_emit` with only `proposal_id` reloads the payload
the proposal saved; the content hash still refuses anything the human did not approve.

**`gate override` in an agent's shell refuses up front** and names `--yes`, instead of asking a
question nobody can answer.

**A degraded `session_save` names its cause** — the error type and code — in the MCP result.

**The release workflow's test ceiling is 32 minutes** (it was 22, which the 0.0.23 suite outgrew).

**CI runs Python 3.12 only.** The declared floor (`>=3.10`) is no longer exercised by any workflow;
that is a deliberate trade and it is disclosed.

## Re-scheduled

No published commitment named **0.0.24**, so nothing slipped from this release. One item from an
earlier release's notes is still open, and is restated so it is not lost:

- **The CI ceiling re-derived from the public mirror.** The v0.0.21 notes said it was, with the
  internal-tooling suite's recorded local gate, re-scheduled to **0.0.22** (tracked as `INTERNAL-ONLY-TESTS-RUN-ON-NO-RUNNER`).
  The gate shipped at 0.0.22; no stage of 0.0.22, 0.0.23 or 0.0.24 built the ceiling half. **These
  notes name no new release for it.** Contributor-facing.

### Known limitations

- **The declared Python floor is untested.** No workflow runs 3.10 or 3.11, so a bug that exists only below 3.12 (like 0.0.15's ISO-date split) would reach users first.
- **A suite slower than 600 s** cannot be baselined over MCP; use `mokata baseline` in a terminal.
