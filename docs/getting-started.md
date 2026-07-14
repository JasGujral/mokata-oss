# Getting started with mokata

mokata installs from PyPI with pip — no clone required. Pick the path that matches how you work.

## Path A — In Claude Code (recommended)

You get the full spec-driven TDD workflow (brainstorm → spec → develop → review → ship) as slash
commands and Agent Skills inside Claude Code, plus the bundled MCP server.

```bash
pip install mokata          # requires Python 3.10+ (see the note below)
mokata setup claude         # wires the commands, Agent Skills, MCP server, hooks, and status line — with your approval
# restart Claude Code so it loads the newly registered MCP server
```

Verify it worked:

```bash
mokata mcp status           # expect: mokata-mcp: CONNECTED ✓
```

Then, inside Claude Code, start with `/mokata:brainstorm` (a new problem) or `/mokata:refine`
(existing code). If the MCP tools ever stop showing up, just tell Claude "mokata mcp isn't working"
and the `/mokata:mcp-repair` repair skill will re-register it (you'll need to restart Claude Code after).

> **Python version.** mokata requires **Python ≥ 3.10**; the MCP server ships and runs out of the
> box on a plain `pip install mokata`.

### Approving a write

mokata's writes are gated on **you**, not on the model. When the agent wants a durable write
(memory, config, a session bundle) nothing is committed: it gets back a *proposal id*. You run
`mokata approve <id>` in your own terminal, and the agent re-tries the same write, which then
commits **once**. The approval is single-use, content-hashed (change an argument and the id no
longer matches), and expires after 15 minutes. Bare `mokata approve` lists what's waiting.

There is deliberately **no approve tool or slash command** inside Claude Code — a model must
never be able to approve its own write.

### The two guards on Claude's file writes

`mokata setup claude` also wires two blocking hooks (on by default; `--no-hooks` opts out):

- **secret-guard** — a write carrying a secret is blocked outright. Never overridable, and it
  still blocks a write you *did* approve: approval is a methodology gate, never a security
  override.
- **gate-guard** — inside an active run, an implementation write is blocked if there's no spec
  yet, no failing test on record, or the write strays outside the spec's authorized scope. Test
  files are always writable, and editing your repo outside a run is never policed.
  `mokata gate status` shows what's enforced here; `mokata gate override <gate> --reason "…"`
  lifts one gate for the session — explicit, re-confirmed, and on the audit ledger.

## Path B — Terminal CLI (any AI tool, CI, scripting)

You get the engine — gates, memory, structural queries, the audit ledger — driven from the terminal.

```bash
pip install mokata
mokata init                 # scaffold a governed config in the current repo
mokata brainstorm           # or: mokata --help to see every command
```

## Working as a team

Everything above is **local mode** — the zero-config default. When you're ready to share memory,
sessions, and a governed audit trail across a team, one person runs `mokata team init` and everyone
else reaches **CONNECTED** with a single guided `mokata team join`. See
[Team mode — setup & operations](how-to/team-setup.md) for the full path (and its security model).

## Path C — Contribute to mokata (developers only)

Only clone if you're working on mokata itself; end users never need to.

```bash
git clone https://github.com/JasGujral/mokata-oss.git
cd mokata-oss
pip install -e .            # editable install (Python 3.10+ also pulls the MCP SDK)
python -m unittest discover -s tests -t tests
```

## Installing in an isolated environment

`pipx` keeps mokata off your global Python:

```bash
pipx install mokata
pipx upgrade mokata         # when a new version ships
```

Zero-install runners also work: `uvx mokata --version`.

## Trying a pre-release

Release candidates are published to PyPI but **not** installed by default — plain `pip install
mokata` always gives you the latest stable. To test a candidate:

```bash
pip install --pre mokata            # newest pre-release
# or pin an exact candidate (see the PyPI release history for the tag):
pip install "mokata==<version>rc1"
```

Once you've validated it, move back to the stable line with `pip install -U mokata`.

## What's next

- New to the workflow? Run `mokata tour` for a 60-second read-only walkthrough.
- See the full command list in the [CLI reference](reference/cli.md).

> The mokata Claude Code **plugin** (one-click install from the in-app plugin directory) is planned
> but **not yet available** — it isn't registered on the marketplace. For now, the pip + `mokata
> setup claude` path above is the supported way to use mokata inside Claude Code.
