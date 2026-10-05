# Contributing to mokata

Thanks for helping build mokata. This project has a few firm rules — they're what make it
trustworthy.

Full documentation: <https://mokata.ai/>.

## Dev setup

```bash
git clone https://github.com/JasGujral/mokata-oss && cd mokata-oss
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[mcp,schema]"                        # the package + both dev extras
pip install -r requirements/ci.txt                    # the TEST dependencies (PyYAML and friends)
```

mokata has **no required runtime dependencies**; `jsonschema` is optional and degraded
over. Python 3.10–3.13 are supported.

> ⚠ **A venv, and `requirements/ci.txt`, are both load-bearing for the test suite** — this is
> not boilerplate. The suite asks the environment where *its* `mokata-mcp` is, so the console
> scripts have to be installed into the interpreter that runs it; and `requirements/ci.txt`
> carries PyYAML, which ~116 tests import and deliberately **fail** on rather than skip.
>
> **Without both, a correct tree reports 30 failures and 116 errors.** That is measured, not
> hypothetical: it is what the documented instructions used to produce on a clean checkout, and
> the reasonable conclusion a reader drew was that the tree was broken.
>
> ⭐ **The suite now REFUSES to run rather than reporting that number.** An unprovisioned
> interpreter gets a short message naming what is missing and this command, and exits `2` —
> distinct from `1`, so "your interpreter is wrong" and "your tests failed" are different
> answers. There is no skip: a run that cannot measure must not report a count.

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

CI runs both states across Python 3.10–3.13 plus a `mokata playbook` smoke run.

### And on the declared floor

The commands above run on whatever `python` is on your PATH — often *newer* than the floor the
package promises, so they cannot catch a fault that only exists below it. Provision the floor
from the repo instead of by hand:

```bash
scripts/floor-python.sh                        # build build/floor-venv at the declared floor
scripts/floor-python.sh --jsonschema absent    # the same, with jsonschema removed
scripts/floor-python.sh --dry-run              # both routes, and whether you have either
scripts/floor-python.sh --check                # what is actually in there?
scripts/floor-python.sh --exec -m unittest discover -s tests -t tests
```

The floor comes from `pyproject.toml`'s `requires-python` every time, so the command follows the
floor when it moves. It needs `uv` (which fetches the interpreter for you) or a matching
`pythonX.Y` on PATH; without either it refuses and says so rather than quietly using the wrong one
— including `--dry-run`, which **exits 2** on a machine with no route instead of printing an empty
plan and succeeding.

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

- Branch from `main`; one focused change per PR.
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
