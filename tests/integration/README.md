# Integration tests (the release gate)

End-to-end scenarios that prove mokata's modules work **together** — the whole pipeline
across every profile and both execution modes, plus the cross-cutting round-trips
(config, memory, knowledge, governance, resume). They drive the real engine; they add no
product code.

This package is **separate from the unit suite** and **independently runnable**. It has its
own support shim (`_support.py`) and its own discovery top-level dir, so the unit run never
sweeps it and this run never depends on the unit suite's layout.

## Run

```bash
# integration suite only
python -m unittest discover -s tests/integration -t tests/integration

# the profile × mode PASS/FAIL grid
python tests/integration/test_matrix_profiles_modes.py
```

Must pass with `jsonschema` both **absent** and **present** (CI runs both legs). The same
command is wired into `.github/workflows/ci.yml` and as a required gate in
`.github/workflows/release.yml`.
