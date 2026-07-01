# Install mokata (pip / pipx / uv / brew)

mokata is a pure-Python CLI with **no required runtime dependencies**, so it installs anywhere
Python ≥ 3.9 runs. Pick the path that fits how you work. This page covers the **CLI**; to drive
the governed workflow *inside* Claude Code, also install the
[Claude Code plugin](use-the-plugin.md) or run [`mokata setup`](use-without-plugin.md).

## At a glance

| Method | Command | Status | Best for |
|---|---|---|---|
| pipx (recommended) | `pipx install mokata` | **Live** (on PyPI) | An isolated, always-available `mokata` on your PATH |
| pip | `pip install mokata` | **Live** (on PyPI) | Adding mokata to a project/venv |
| uv (pip) | `uv pip install mokata` | **Live** (on PyPI) | uv-managed environments |
| uvx (zero-install run) | `uvx mokata --version` | **Live** (on PyPI) | Running mokata once without installing |
| pipx run (zero-install) | `pipx run mokata --version` | **Live** (on PyPI) | The pipx equivalent of `uvx` |
| Homebrew | `brew install mokata` | **Pending publication** — not in homebrew-core / no official tap yet | macOS/Linux users once a tap is published |
| npm / npx | — | **Not applicable** — mokata is a Python package, not an npm one | (use `uvx`/`pipx run` for zero-install) |

> **Honest status.** pip/pipx/uv/uvx are live because mokata is published on PyPI. Homebrew is
> **not yet published** — the formula exists in the repo (`packaging/homebrew/mokata.rb`) but is
> not in homebrew-core and no official tap is live; the "self-tap" steps below work today. There
> is **no npm package**, so `npx mokata` does not apply — use `uvx`/`pipx run` for a
> zero-install runner.

## pipx (recommended)

[pipx](https://pipx.pypa.io) installs the CLI into its own isolated environment and puts `mokata`
on your PATH — no venv juggling, no dependency conflicts.

```bash
pipx install mokata
mokata --version
```

Upgrade with `pipx upgrade mokata`; uninstall with `pipx uninstall mokata`. mokata's own
`mokata upgrade` proposes a **human-gated** `pip install -U mokata` (it never upgrades on its own).

## pip / uv

```bash
pip install mokata            # into the active environment
uv pip install mokata         # the uv equivalent
```

Optional extras (each is degrade-clean when absent — never required):

```bash
pip install "mokata[mcp]"       # the in-harness MCP server (Python ≥ 3.10)
pip install "mokata[postgres]"  # optional shared-memory / session Postgres backend
pip install "mokata[neo4j]"     # optional external code-graph backend
pip install "mokata[schema]"    # richer manifest validation messages (jsonschema)
```

## Zero-install runners (uvx / pipx run)

Run mokata once without installing it — handy in CI or to try a command:

```bash
uvx mokata --version           # uv's runner
pipx run mokata stacks list    # pipx's runner
```

## Homebrew (pending publication)

mokata is **not yet in homebrew-core and has no published tap** — `brew install mokata` does not
work yet. The formula lives in the repo at `packaging/homebrew/mokata.rb`. Until a tap is
published you can self-tap it:

```bash
# 1) Prefer pipx/uv above. 2) Or, once a tap repo exists, e.g. JasGujral/homebrew-mokata:
brew tap jasgujral/mokata
brew install mokata
```

When the tap is published this page and the status table will be updated to **Live**. Until then,
treat Homebrew as pending and use pipx/uv.

## Then what?

```bash
mokata --version               # confirm the install
mokata init                    # scaffold a governed config (human-gated)
mokata stacks list             # browse ready-made governed stacks for your framework
```

See [Community stacks](community-stacks.md) to adopt a ready-made governed stack, and
[Use the plugin in Claude Code](use-the-plugin.md) to drive the workflow inside the harness.
