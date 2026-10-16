# `bench/` — mokata's eval harnesses (not the product)

`bench/` sits beside `src/`, is excluded from the wheel (`pyproject.toml`
`[tool.setuptools.packages.find]`), and is never imported by `src/mokata`
(`tests/test_no_api_key_guard.py`, rule R6). mokata itself never needs or reads an API key.

| package | what it measures |
|---|---|
| `bench.g2` | **G2 proof engine** — bare Claude Code vs mokata-on, conditions × tasks × ≥5 repeats |
| `bench.locomo` | **BM (LOCOMO)** — governed-memory retrieval on LOCOMO, 8 pins, pin 8 is a stop condition |

## G2 — how a real run works

* **Agent:** Claude Code headless (`claude -p … --output-format stream-json --verbose`) on **your
  own Claude login**. Its environment is scrubbed of `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`,
  `OPENAI_API_KEY` and the Bedrock/Vertex switches, so it cannot drift onto metered billing.
* **Isolation:** every condition runs with `--setting-sources project,local --strict-mcp-config
  --mcp-config <condition's>`; the stream's own `init` event is then checked, and a run whose MCP
  servers disagree with its condition is **CONTAMINATED** and fails validation.
* **Fresh clone per run** (`git clone --depth 1`, or `fetch --depth 1 <pinned sha>`), deleted after.
* **Judge:** a *separate* session that sees the full trajectory and the rubric, not the condition
  name. `--judge claude-cli` (default, your login, no key) or `--judge anthropic-api
  --judge-model <id>` (opt-in; reads **only** `MOKATA_G2_JUDGE_API_KEY`, refuses loudly if unset).
* **Archive:** `<out>/run.json` + `<out>/{condition}/{task}/run{N}/` with `stream.jsonl` (raw),
  `trajectory.json`, `verdict.json`, `judge_raw.txt`, `meta.json`; `STUDY.md` is generated from it.

```bash
python -m bench.g2 plan --repeats 5                    # matrix + cost drivers, runs nothing
python -m bench.g2 run  --repeats 5 --out runs/g2-YYYYMMDD --yes \
                        [--judge anthropic-api --judge-model <model-id>]
python -m bench.g2 validate --run runs/g2-YYYYMMDD     # complete REAL run?
python -m bench.g2 study    --run runs/g2-YYYYMMDD     # (re)write STUDY.md
python -m bench.g2 page     --run runs/g2-YYYYMMDD --out proof.html   # D9 public proof page
python -m bench.g2 continuity --run runs/g2-YYYYMMDD   # D10 CTX-CONT
python -m bench.g2 lean     --run runs/g2-YYYYMMDD     # D11 AXI-LEAN verdict (adoption needs owner GO)
python -m bench.g2 audit    --run runs/g2-YYYYMMDD     # PIPE-TOKEN-AUDIT report
```

Run from the repository root, in a virtualenv where mokata is installed (`pip install -e .`): the
`mokata` condition wires each clone with `<that python> -m mokata.cli setup claude --scope
project`, and the `mokata-mcp` it registers must be on `PATH` for the `claude` child. `claude`
itself must be installed and logged in (`claude` once, interactively).

Before the first real run, check `claude --help` lists `--setting-sources`, `--strict-mcp-config`,
`--mcp-config`, `--disallowedTools`, `--max-turns` and `--permission-mode` on your Claude Code
version; the contamination check catches a flag that silently did nothing, but cannot add one.

**Known limit:** mokata's human-gated writes (memory `remember`, `apply_proposal`, …) have no human
to approve them in a headless run. G2 therefore measures mokata *with its gates unanswered*; a
condition with a declared auto-approve posture is future work and must be ledgered if added.

The four shipped tasks (`bench/g2/tasks/`) are **starter tasks** against a tiny committed sample
repo — one per mechanism (graph, memory, gates, error-recovery). Ratify or replace them before a
publishable run; a task's `repo` may be `{"url": …, "ref": <pinned sha>}` instead.

## The rule this directory exists to keep

The product runs on the user's Claude login. If a measurement ever needs a key, the key is read
here, opt-in, under a dedicated variable name — never in `src/mokata`.
