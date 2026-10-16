"""mokata's EVAL HARNESSES — outside the product, never shipped in the wheel.

`bench/` is a top-level package beside `src/`, not under it, so `[tool.setuptools.packages.find]
where = ["src"]` cannot pick it up; `tests/test_g2_harness.py` pins that, and
`tests/test_no_api_key_guard.py` pins that `src/mokata` never imports it.

    bench.g2      — the G2 proof engine: bare Claude Code vs mokata-on, conditions × tasks × ≥5
                    repeats, fresh clone per run, a SEPARATE judge on the full trajectory.
    bench.locomo  — the LOCOMO memory benchmark harness (8 pins; pin 8 is a stop condition).

THE API-KEY BOUNDARY. The product (`src/mokata`) never reads an API key. The AGENT under test here
is Claude Code headless (`claude -p`) on the user's own login — its child environment is SCRUBBED
of API-key variables so a stray `ANTHROPIC_API_KEY` cannot silently switch it to API billing. The
only key this package ever reads is the OPT-IN judge's (`MOKATA_G2_JUDGE_API_KEY`), and only when
`--judge anthropic-api` is asked for; absent, it refuses loudly.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""
