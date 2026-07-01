# How-to: mokata as a PR check

Put mokata in your team's daily workflow — run its **completeness gate** + **spec-awareness
regression guard** on every pull request, and post the verdict as a PR review comment. It's
**opt-in**, **least-privilege**, and **degrade-clean**: it never false-blocks a PR when there's
nothing for it to check.

## What it checks

`mokata ci-check` runs two gates over a PR's **changed files** (it reuses the exact engines the
CLI and plugin use — no separate logic):

| Leg | What it asks | Blocks when |
|---|---|---|
| **completeness gate** | does the repo's **saved spec** still map every acceptance criterion to a test? | a saved spec has an AC with **no test** (a real completeness gap) |
| **spec-awareness** | does this change **touch** a previously saved spec or decision? | the changed files/symbols overlap a saved spec/decision (a regression a reviewer must confirm) |

Each block names the **single unblock action** (e.g. *write a test for the unmapped AC*, or
*confirm/amend the affected spec*).

## It never false-blocks (degrade-clean)

A PR gate that cries wolf gets turned off. mokata only flags what it can genuinely check — it
**PASSES** (and says why) when there's nothing to:

- the repo isn't mokata-initialized → nothing to check;
- there's **no saved spec** → the completeness leg skips;
- the repo doesn't tag its tests with **AC ids** → the completeness leg skips (no convention to
  enforce), rather than blocking;
- there's **no saved spec corpus** → the spec-awareness leg skips;
- **no code graph** → spec-awareness falls back to lexical/file overlap and says so.

## Opt in — add the workflow

mokata only **produces** the review comment; your repo's own `GITHUB_TOKEN` **posts** it (the
standard CI pattern — mokata never acts on your behalf outside your CI). Copy this into
`.github/workflows/mokata-pr-check.yml`:

```yaml
name: mokata PR check
on:
  pull_request

# Least-privilege: read the code; write only PR comments.
permissions:
  contents: read
  pull-requests: write

jobs:
  mokata-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0                 # full history so the base diff resolves
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: python -m pip install --quiet mokata
      - id: check
        uses: JasGujral/mokata-oss/.github/actions/mokata-check@v0.0.5
        with:
          base: ${{ github.event.pull_request.base.sha }}
          fail-on-block: "true"          # set "false" for report-only (never fails the job)
      - name: Post the mokata review comment
        if: always() && hashFiles(steps.check.outputs.comment-file) != ''
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: gh pr comment "${{ github.event.pull_request.number }}" --body-file "${{ steps.check.outputs.comment-file }}"
```

The reusable composite action lives at
[`.github/actions/mokata-check/action.yml`](https://github.com/JasGujral/mokata-oss/blob/master/.github/actions/mokata-check/action.yml)
(a runnable copy of the workflow above ships next to it as `example-pr-check.yml`).

### Action inputs

| Input | Default | Meaning |
|---|---|---|
| `base` | merge-base with `origin/HEAD` | the ref/SHA to diff the PR against |
| `path` | `.` | repo root mokata operates on |
| `install` | *(empty)* | optional `pip install` target (e.g. `mokata` or `-e .`) |
| `comment-file` | `mokata-check-comment.md` | where the review-comment body is written |
| `fail-on-block` | `true` | fail the check on a real block (`false` = report-only) |

## Run it locally

The same check runs in your terminal — no CI required:

```bash
# explicit changed files:
mokata ci-check --files src/payments.py,tests/test_payments.py

# or diff against a base ref (what CI does):
mokata ci-check --base origin/main --comment-file mokata-check-comment.md
```

Exit code is non-zero on a real block (0 with `--no-fail`). Inside Claude Code the read-only
`ci_check` MCP tool returns the same verdict + comment body.

See also [the pipeline & gates](../concepts/pipeline.md) and
[`mokata spec-check`](../reference/cli.md) (the regression guard this builds on).
