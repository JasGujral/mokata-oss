# Install mokata (pip / pipx / uv / brew)

mokata is a pure-Python CLI whose **core has no required runtime dependencies**, so it installs
anywhere Python ≥ 3.10 runs. A plain `pip install mokata` also pulls the one default dependency —
the **MCP SDK** — so the in-harness **`mokata-mcp` server works out of the box**, no extra needed.
The SDK is still *lazily* imported, so the CLI runs fine even in a stripped env where it's absent.
Pick the path that fits how you work. This page covers the **CLI**; to drive the
governed workflow *inside* Claude Code, follow the pip-first [Getting started](../getting-started.md)
path (`pip install mokata` → [`mokata setup claude`](use-without-plugin.md)).

## At a glance

| Method | Command | Status | Best for |
|---|---|---|---|
| pipx (recommended) | `pipx install mokata` | **Live** (on PyPI) | An isolated, always-available `mokata` on your PATH |
| pip | `pip install mokata` | **Live** (on PyPI) | Adding mokata to a project/venv |
| uv (pip) | `uv pip install mokata` | **Live** (on PyPI) | uv-managed environments |
| uvx (zero-install run) | `uvx mokata --version` | **Live** (on PyPI) | Running mokata once without installing |
| pipx run (zero-install) | `pipx run mokata --version` | **Live** (on PyPI) | The pipx equivalent of `uvx` |
| Homebrew | `brew install JasGujral/mokata/mokata` | **Live** (tap `JasGujral/mokata`) | macOS/Linux users who manage tools with brew |
| npm / npx | — | **Not applicable** — mokata is a Python package, not an npm one | (use `uvx`/`pipx run` for zero-install) |

> **Honest status.** pip/pipx/uv/uvx are live because mokata is published on PyPI, and Homebrew
> is live from mokata's own tap, `JasGujral/mokata` — **not** homebrew-core, so the formula must
> be reached through the tap (`brew install JasGujral/mokata/mokata`) rather than by the bare
> name. There is **no npm package**, so `npx mokata` does not apply — use `uvx`/`pipx run` for a
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

That already installs the MCP SDK, so `mokata-mcp` works with no extra step — you do **not** need
a `[mcp]` extra (it's a no-op alias kept only for backwards compatibility).

Optional extras (each is degrade-clean when absent — never required):

```bash
pip install "mokata[postgres]"  # optional shared-memory / session Postgres backend
pip install "mokata[schema]"    # richer manifest validation messages (jsonschema)
```

## Zero-install runners (uvx / pipx run)

Run mokata once without installing it — handy in CI or to try a command:

```bash
uvx mokata --version           # uv's runner
pipx run mokata stacks list    # pipx's runner
```

## Homebrew

mokata ships from its own tap, **`JasGujral/mokata`**. One command installs it — `brew` resolves
the tap from the fully-qualified name, so no separate `brew tap` step is required:

```bash
brew install JasGujral/mokata/mokata
mokata --version
```

Upgrade with `brew upgrade mokata`; uninstall with `brew uninstall mokata`.

mokata is **not in homebrew-core**, so a bare `brew install mokata` does not resolve — use the
fully-qualified name above, or tap first and then use the short name:

```bash
brew tap JasGujral/mokata
brew install mokata
```

> On Homebrew 6.0.15 and newer, third-party taps must be trusted before a formula loads. If brew
> reports *"Refusing to load formula … from untrusted tap"*, run `brew trust JasGujral/mokata`
> once and re-run the install.

Homebrew builds Python formulae from source with `--no-deps`, so the formula vendors mokata's
whole dependency tree — including the MCP SDK — into its own virtualenv. `mokata-mcp` therefore
works from a brew install exactly as it does from pip.

## Then what?

```bash
mokata --version               # confirm the install
mokata init                    # scaffold a governed config (human-gated)
mokata stacks list             # browse ready-made governed stacks for your framework
```

See [Community stacks](community-stacks.md) to adopt a ready-made governed stack, and
[Getting started](../getting-started.md) to wire mokata into Claude Code with
`mokata setup claude`.
