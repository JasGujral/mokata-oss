mokata **0.0.25**. Set up or upgrade with one instruction, in your repo:
`uv tool install --upgrade mokata` (or `pipx install --force mokata`), then
`mokata setup claude --mode full`.
Requires **Python ≥ 3.10**, and for the Claude Code integration **Claude Code ≥ 2.1.0**. The plugin
route needs **[`uv`](https://docs.astral.sh/uv/) on your PATH**.

---

## 0.0.25 — "Install in one step, domain stacks, and the CLI↔IDE bridge."

**One instruction installs your mode.** `mokata setup claude --mode seatbelt|memory|full` asks one
question for the mode's dependencies, installs them with the installer that owns your mokata (uv
tool, pipx, pip or uv pip), and ends with a table: what is installed, what failed, and the one
command that fixes it. `--with-deps` installs without asking; `--yes` alone installs nothing. Full
mode tells you up front that the code graph's first index takes minutes and up to ~1 GB on a
~1M-line repository.

**`mokata upgrade` uses your installer too** — `uv tool upgrade mokata` on the documented route,
where the old pip-only upgrade failed.

**The review window grew up.** `mokata ide` now:
- follows a live session (`mokata setup claude --ide-bridge` wires observe-only `http` hooks) and
  joins a CLI session; `mokata query` prints links that open the IDE at the line;
- shows a run's plan and spec — runs with a spec first — including a spec still waiting for
  `mokata approve <id>`, and your saved plan files;
- explains its call graph: what a symbol is, what each view answers, a picker as you type;
- has a health panel: what is missing, degraded or out of sync, buttons to force-rebuild the code
  graph, the AST index and the search index, and **Update mokata** on a confirmed click (checking for
  an update is a button — the only time the IDE goes online);
- calculates impact for the rest of a file's changed functions when you ask;
- stays responsive on a million-line repository and runs nothing while idle.

The embedded terminal is **opt-in**: `mokata ide --terminal`, only from your own terminal, never
inside a Claude Code session.

**Frontend and backend work routes to its own skills and gates.** React (`frontend-react`,
`frontend-perf`, `frontend-security`) and NestJS (`backend-nestjs`) skills; `react-web` and
`nestjs-api` stacks; UI acceptance criteria carry a state matrix (default, loading, empty, error,
disabled); six backed domain gates run with `mokata gate check <gate>`.

**Fixed on Windows:** cmd windows flashing while the IDE ran or from pyenv-win `.bat` shims. Run
`mokata setup claude` once after upgrading so the shims are unwired; `mokata doctor` says if any remain.

**Fixed with code-review-graph 2.3.9:** mokata's handshake was refused, so every query silently fell
back to the built-in reader while the status bar said otherwise, and answers cut at 100 were shown
as complete.

**Faster hooks:** gate hooks p95 68–128 ms (was 117–320), observe hooks outside a repo 25–38 ms
(was 80–100), on Linux. `mokata setup claude --http-gates` (off by default) runs the gates as one
`http` hook to the IDE server — every gated call is refused while the IDE is not running.

## Re-scheduled

- **The CI ceiling re-derived from the public mirror** (`INTERNAL-ONLY-TESTS-RUN-ON-NO-RUNNER`),
  open since the v0.0.21 notes, is **built in 0.0.25** (`scripts/ci_ceiling.py`). Contributor-facing.
- **code-review-graph's blast radius** — the correct mapping of its traversal result is re-scheduled
  to **0.0.26** (tracked as `CRG-BLAST-EMPTY`); until then the built-in reader answers it.
- **The declared Python floor** now has a weekly Python 3.10 workflow (`python-floor.yml`); it has not
  run on a release runner yet.

### Known limitations

- **code-review-graph's blast radius is answered by mokata's AST reader**: CRG's traversal returned every blast radius empty, so impact under CRG is Python-only until a correct mapping exists.
- **A blast radius is cut at 20,000 references or 2 s**; on a function called from tens of thousands of places the IDE says "not determined — cut at …".
- **Observe hooks inside a mokata repo take 54–125 ms** (p95, Linux) against a 50 ms budget; macOS and Windows timings are not measured.
- **Follow mode is unproven against a real `claude` session**; "edits appear within 1 s" has not been measured end to end.
- **The embedded terminal is POSIX-only**; on Windows the IDE says it is not available.
- **CI runs Python 3.12 only.** The weekly Python 3.10 floor workflow has not run on a release runner yet.
- **The clean-machine install workflow and the Windows no-console-window test have not run on a release runner yet.**
