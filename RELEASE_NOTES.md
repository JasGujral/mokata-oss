mokata **0.0.26**. Set up or upgrade with one instruction, in your repo:
`uv tool install --upgrade "mokata[full]"` (or `pipx install --force "mokata[full]"`), then
`mokata setup claude --mode full`.
Requires **Python ≥ 3.12**, and for the Claude Code integration **Claude Code ≥ 2.1.198**. The plugin
route needs **[`uv`](https://docs.astral.sh/uv/) on your PATH**.

---

## 0.0.26 — "Stable sessions, a worktree per session, and an IDE at multi-million-line scale."

**⚠ Before you upgrade.** Python 3.12+ and Claude Code 2.1.198+ are now required. Specs approved by
0.0.25 need re-approving with a task plan — `mokata doctor` names each run and the command
(`mokata spec migrate <run>`). Run `mokata setup claude` once after upgrading.

**🔒 Security fix.** 19 slash-command templates passed your or the repository's text through `eval`,
a shell-injection path through `$ARGUMENTS`. They now pass it as single-quoted arguments, and CI runs
six security lints on every pull request. The posture is published in `docs/security.md`.

**The MCP server is stable.** Tool calls no longer block each other, a call still working answers
`running` and can be re-joined, and a retried spec amend is answered from the record instead of being
applied twice. Spec registration and amend stay on your run after a resume, a long idle or a dropped
connection (`/mcp` reconnect) — and never take another window's session.

**Every session in its own worktree.** Each Claude Code session edits its own git worktree; runs,
approvals and the IDE share one `.mokata/` store, and code answers follow the worktree you are in. A
worktree that is behind its base cannot be merged: the IDE offers an approve-gated **Sync** first,
and `/ship` always rebases and re-runs the suite before you merge or open a pull request.

**Plans you can hold the agent to.** A spec carries a task plan, approved and sealed with a hash;
writes outside the active task are blocked. A session that claims "done" without the evidence is sent
back with what is missing; the third failed fix at one failure, or a sixth review round, stops for a
new approach. Memory writes must cite things that exist.

**The IDE works on multi-million-line repositories.** One navigation; every session in one place;
heavy work as visible jobs with progress and Cancel; go-to-definition, outline, search (every hit,
paged) and meaning search from the code graph at kubernetes scale; graph views that draw in about a
second and never more than 150 nodes. Approve proposed writes in the window with a one-time code from
your terminal. Closing the IDE with work running asks first. A repo nobody set up opens on Welcome
and writes nothing until you confirm.

**Full mode, built in one go.** `mokata setup claude --mode full` ends with code-review-graph's
index, the meaning-search index and ripgrep word search ready. The per-prompt hook no longer takes
5–6 s with code-review-graph (~75 ms p95), and code-review-graph's blast radius is mapped correctly.

## Re-scheduled

- **Read-only code intelligence in the IDE** — references, hover and call hierarchy from a language
  server, behind workspace trust — is scheduled for **0.0.27** (tracked as `ID-LSP-READONLY-EARLIER`).
  Until then go-to-definition and callers come from the code graph.

## Known limitations
- **Not yet run on the macOS and Windows CI legs.** Tested on Linux and by hand on macOS; Windows
  setup, the Windows IDE terminal and the `.cmd` hook launcher are not yet verified on a real desktop.
- **Hook timings are measured on Linux only.**
- **Follow mode is unproven against a real `claude` session** end to end.
- **Runs created before 0.0.26 keep the previous re-entry behaviour** for a resumed or idle window
  until a new run is registered.
- **A restarted MCP server is recognised only when Claude sits within two launcher processes of its
  hooks**; a deeper chain (e.g. the Windows `py.exe` stub) refuses as ambiguous, as in 0.0.25.
- **The clean-machine install workflow has not run on a release runner yet.**
