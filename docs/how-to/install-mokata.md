# Install mokata (plugin / pipx / uv / brew / pip)

mokata is a pure-Python CLI whose **core has no required runtime dependencies**, so it installs
anywhere Python ≥ 3.12 runs. A plain `pip install mokata` also pulls the one default dependency —
the **MCP SDK** — so the in-harness **`mokata-mcp` server works out of the box**, no extra needed.
The SDK is still *lazily* imported, so the CLI runs fine even in a stripped env where it's absent.
Pick the path that fits how you work. This page covers the **CLI**.

> **The primary way — one install line, one setup:** in your repo run
> `uv tool install "mokata[full]"` (or `pipx install "mokata[full]"`), then
> `mokata setup claude --mode full`, restart Claude Code and run `mokata doctor`. The installer
> fetches everything full mode uses; setup only detects and wires it, and never installs. Update
> later with `uv tool upgrade mokata` / `pipx upgrade mokata` — both keep the extra. `setup claude` runs `mokata init` for you on a new
> repo and only refreshes the wiring on an existing one.
>
> **Alternative inside Claude Code: the plugin** — you may not need the CLI at all; it installs
> with two commands inside Claude Code: `/plugin marketplace add JasGujral/mokata-oss`, then
> `/plugin install mokata@mostack` (its MCP server starts through `uvx`, so
> [`uv`](https://docs.astral.sh/uv/) must be on PATH). See [Install the plugin](install-plugin.md).
> Want the CLI too (for `mokata setup claude`, `mokata approve`, CI)? Install it **isolated** —
> pipx, `uv tool` or Homebrew below — then follow [Getting started](../getting-started.md).

## At a glance

| Method | Command | Status | Best for |
|---|---|---|---|
| uv tool (recommended) | `uv tool install "mokata[full]"` | **Live** (on PyPI) | An isolated `mokata` + everything full mode uses — then `mokata setup claude --mode full`; update with `uv tool upgrade mokata` |
| pipx (recommended) | `pipx install "mokata[full]"` | **Live** (on PyPI) | The same, for pipx users; update with `pipx upgrade mokata` |
| pip | `pip install mokata` | **Live** (on PyPI) | Last resort — inside a project venv this mixes mokata's deps into yours; prefer pipx / uv tool |
| uv (pip) | `uv pip install mokata` | **Live** (on PyPI) | Same caveat as pip — it installs into the active environment |
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

Upgrade with `pipx upgrade mokata`; uninstall with `pipx uninstall mokata`. `mokata update`
does the same in one step for any install — it shows the release notes and the command, and asks
first.

## uv tool

The uv equivalent of pipx — its own environment, `mokata` on your PATH:

```bash
uv tool install mokata
mokata --version
```

Upgrade with `uv tool upgrade mokata`; uninstall with `uv tool uninstall mokata`.

## pip / uv pip (last)

```bash
pip install mokata            # into the active environment
uv pip install mokata         # the uv equivalent
```

> **Inside a project venv this mixes mokata's deps into yours — prefer the above.** It also binds
> the hooks `mokata setup claude` wires to that venv's path, so they break when the venv is
> recreated. `mokata doctor` and `mokata setup claude` warn when they find mokata installed inside
> the repo's own environment, or an older copy on PATH shadowing the one you meant to run.

That already installs the MCP SDK, so `mokata-mcp` works with no extra step — you do **not** need
a `[mcp]` extra (it's a no-op alias kept only for backwards compatibility).

Optional extras (each is degrade-clean when absent — never required):

```bash
pip install "mokata[full]"      # everything `--mode full` wires: embeddings + graph-ts +
                                #   code-review-graph + the IDE companion's web stack
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

## "mokata needs Python 3.12 or newer"

mokata requires **Python 3.12+**. On an older Python, pip refuses the install
(`requires-python >=3.12`), and a checkout run without installing (the Claude Code plugin's hooks,
`mokata-launch`, a clone on `PYTHONPATH`) says so in one line, naming the Python it found — for
example *"found Python 3.10 (python3), but mokata needs Python 3.12+"*. Fix it either way:

- install uv — `uv tool install mokata` and the plugin route fetch a Python 3.12 themselves; or
- install Python 3.12+ (python.org, `brew install python@3.12`, `uv python install 3.12`, or your
  distro's package) and install mokata with it.

## Then what?

```bash
mokata --version               # confirm the install
mokata init                    # scaffold a governed config (human-gated)
mokata stacks list             # browse ready-made governed stacks for your framework
```

See [Community stacks](community-stacks.md) to adopt a ready-made governed stack, and
[Getting started](../getting-started.md) to wire mokata into Claude Code with
`mokata setup claude`.
