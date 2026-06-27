# Contributing to mokata

Thanks for helping build mokata. This project has a few firm rules — they're what make it
trustworthy.

Full documentation: <https://jasgujral.github.io/mokata-oss/>.

## Dev setup

```bash
git clone https://github.com/JasGujral/mokata-oss && cd mokata-oss
pip install -e ".[mcp,schema]" # editable install + both extras (MCP server + jsonschema), for dev
```

mokata has **no required runtime dependencies**; `jsonschema` is optional and degraded
over. Python 3.9–3.12 are supported.

## Running the tests (in BOTH jsonschema states)

The suite must pass with `jsonschema` absent **and** present — this is a hard invariant.

```bash
# absent
pip uninstall -y jsonschema
python -m unittest discover -s tests -t tests

# present
pip install "jsonschema>=4.0"
python -m unittest discover -s tests -t tests
```

CI runs both states across Python 3.9–3.12 plus a `mokata playbook` smoke run.

## The rules (non-negotiable)

1. **TDD / RED-before-GREEN.** Write the test, watch it fail, then implement. PRs without
   a failing-first test are sent back.
2. **Clean-room.** mokata reimplements the best ideas in its own code and words. Do **not**
   import, depend on, or copy text from any other agent/methodology framework (e.g.
   superpowers). Study strong prompts as a quality bar; rewrite in mokata's own words.
3. **Human-gate every durable write.** No new code path may write to code, memory, or
   config silently or autonomously — route it through the WriteGate / an approval.
4. **Local-first.** Nothing leaves the machine unless a human explicitly wires it.
5. **Apache-2.0 / MoStack; no vendor-prefixed names.**

## Commit & PR flow

- Branch from `master`; one focused change per PR.
- Commit messages: imperative summary line; explain *why* in the body when non-obvious.
- Open a PR and fill in the template (the checklist mirrors the rules above).
- CI must be green (both jsonschema states, all Python versions) and one review is required.

## Adding a skill / command / adapter

- **Skill/command:** add a `Skill` to `src/mokata/skills.py` (name, summary, prompt, gate),
  then regenerate its template under `templates/commands/` via `command_markdown`. Author
  it test-first (see `SkillDraft`, G6).
- **Adapter / tool:** declare it as an `AdapterContract` (`provides` capabilities) and
  validate with `validate_adapter`; wire it through the capability router — there is one
  detection path, never a second.

## Code style

Match the surrounding code: clear names, terse docstrings stating the feature ID, small
focused modules, no new dependencies. Keep always-on rules ≤ 60 lines and per-agent
MEMORY ≤ 200 lines.
