# Hash-pinned CI / release / docs requirements

These files hash-pin the **tooling** that CI, the release pipeline and the docs build install
from PyPI, so the OpenSSF Scorecard *Pinned-Dependencies* check stops flagging unpinned `pip
install`s. Each `.txt` is `--require-hashes`-compatible (exact `==` versions + per-package
sha256), and every workflow/script installs from one with:

```sh
pip install --require-hashes -r requirements/<file>.txt
```

| File | Installed by | Contents |
|---|---|---|
| `ci.txt` | `ci.yml` (test) · `release.yml` (test) | PyYAML — a **test-only** dep for the workflow-lint tests (not a mokata dependency); installed in both jsonschema matrix legs |
| `jsonschema.txt` | `ci.yml` / `release.yml` present leg · `release.sh` preflight | the optional `[schema]` extra, pinned to the exact version mokata resolves in `uv.lock` |
| `release-build.txt` | `release.yml` (build) | `build` (PyPA sdist/wheel) + `cyclonedx-bom` (SBOM) |
| `docs.txt` | `docs.yml` (build) | `mkdocs` + `mkdocs-material` for the docs site |

## What is NOT pinned here (deliberately)

- **`pip install -e .` / `-e ".[postgres,neo4j]"` / `-e "$sub"`** — the editable/local install of
  **mokata itself**. Hash-pinning applies to the CI/release *dependency* installs, never to the
  local package under test. These stay exactly as they were.
- **`pip install dist/*.whl`** (release SBOM venv) — installs the freshly-built mokata artifact,
  not a remote dependency.
- **`pip install --upgrade pip`** — bootstrapping the installer itself, not a project dependency.

Runtime dependencies of the `mokata` package live in `pyproject.toml` and are locked in `uv.lock`;
they are unchanged by this — nothing here ships in the wheel.

## Regenerating (single source of truth = the `.in` files + `uv.lock`)

Edit the loose pins in the `*.in` files, then recompile — never hand-edit a `*.txt`:

```sh
scripts/gen-pinned-requirements.sh      # requires `uv` (pipx install uv)
```

This runs `uv pip compile --universal --python-version 3.10 --generate-hashes` for each `.in`, so
one file is valid across the whole CI matrix (Python 3.10–3.13, Linux + Windows). `jsonschema.in`
is pinned to the exact `jsonschema` version `uv.lock` resolves for mokata's `>=3.10` floor, keeping
the CI install consistent with what mokata itself locks; its transitive deps are freshly resolved
and hash-pinned. The other tools are not part of mokata's dependency graph, so they are declared in
the `.in` files and compiled with the same `uv` toolchain.
