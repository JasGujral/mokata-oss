# How-to: share a governed stack (J3)

Publish your stack so a teammate adopts the same governed configuration in one command.

## Export

```bash
mokata export                      # writes <path>/mokata-stack.json
mokata export team-stack.json      # custom destination
```

This writes the current manifest as a shareable, valid artifact.

## Import + apply

```bash
mokata import team-stack.json --yes        # validate + apply (human-gated)
mokata import team-stack.json --yes --force  # overwrite an existing config
```

Apply is **validated before it writes** — an invalid manifest is rejected (exit 1, nothing
written) — and the durable write is **human-gated** (omit `--yes` to confirm interactively;
`--force` is required to overwrite an existing `.mokata/manifest.json`).

Programmatically: `export_manifest(surface, dest=...)`, `validate_shared(data)`,
`apply_manifest(root, data, assume_yes=..., force=...)`.
