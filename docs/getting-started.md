# Getting started with mokata

mokata installs from PyPI with pip — no clone required. Pick the path that matches how you work.

## Path A — In Claude Code (recommended)

You get the full spec-driven TDD workflow (brainstorm → spec → develop → review → ship) as slash
commands and Agent Skills inside Claude Code, plus the bundled MCP server.

```bash
pip install mokata          # Python 3.10+ recommended (see the note below)
mokata setup claude         # wires the commands, Agent Skills, MCP server, and status line — with your approval
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
