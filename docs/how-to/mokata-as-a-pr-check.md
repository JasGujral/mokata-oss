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
| **spec-drift** | do the **committed specs** (`.mokata/specs/`) still match the code — every AC's code anchor resolves, and every AC still has its test? | a committed spec points at code or a test that is gone, or a committed spec file is unreadable |

**Committed specs are opt-in.** By default a spec lives only in `.mokata/temp_local/` (git-ignored),
so a CI checkout has none and the spec-drift leg prints *"no committed specs — spec drift not
checked"*. That leg is **absent** — neither a pass nor a failure. It is shown as *not checked*, never
counted as a pass, and the check still exits 0 (it did not fail; it also did not pass). To have CI check
specs, turn on `settings.specs.commit` (human-gated, like every config change):

```bash
mokata config set settings.specs.commit true
```

From then on, approving a spec (emit or amend) also writes the approved spec to
`.mokata/specs/<run>.json`, inside the same human-gated commit. The approval preview names the file.
Commit it with the change it describes. The completeness and spec-awareness legs read these files too.

On a CI checkout (no run state) the completeness leg reads these committed specs, and its output lists
the **AC -> test map**: each acceptance criterion with the test that covers it, or `(no test)`.

Each block names the **single unblock action** (e.g. *write a test for the unmapped AC*, or
*confirm/amend the affected spec*).

## It never false-blocks (degrade-clean)

A PR gate that cries wolf gets turned off. mokata only flags what it can genuinely check — it
**does not fail** (and says why) when there's nothing to check. Every leg ends **pass**, **fail** or
**absent**, and *absent is not a pass*: the headline reads `NOT CHECKED` when no leg graded anything
and `PASSED (PARTIAL: … not checked)` when some did, the comment names each unchecked leg, and the
green tick is kept for a check that passed in full.

- the repo isn't mokata-initialized → nothing was checked;
- there's **no saved spec** → the completeness leg is absent;
- the repo doesn't tag its tests with **AC ids** → the completeness leg is absent (no convention to
  enforce), rather than blocking;
- there's **no saved spec corpus** → the spec-awareness leg is absent;
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
      - uses: actions/checkout@v7        # Node-24 majors, tag-pinned
        with:
          fetch-depth: 0                 # full history so the base diff resolves
      - uses: actions/setup-python@v7
        with:
          python-version: "3.12"
      - run: python -m pip install --quiet mokata
      - id: check
        uses: JasGujral/mokata-oss/.github/actions/mokata-check@v0.0.28
        with:
          base: ${{ github.event.pull_request.base.sha }}
          fail-on-block: "true"          # "false" = report-only: exits 0 unless the check could not run (exit 2)
      - name: Post the mokata review comment
        if: always() && hashFiles(steps.check.outputs.comment-file) != ''
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: gh pr comment "${{ github.event.pull_request.number }}" --body-file "${{ steps.check.outputs.comment-file }}"
```

The reusable composite action lives at
[`.github/actions/mokata-check/action.yml`](https://github.com/JasGujral/mokata-oss/blob/main/.github/actions/mokata-check/action.yml)
(a runnable copy of the workflow above ships next to it as `example-pr-check.yml`).

### Action inputs

| Input | Default | Meaning |
|---|---|---|
| `base` | merge-base with `origin/HEAD` | the ref/SHA to diff the PR against |
| `path` | `.` | repo root mokata operates on |
| `install` | *(empty)* | optional `pip install` target (e.g. `mokata` or `-e .`) |
| `comment-file` | `mokata-check-comment.md` | where the review-comment body is written |
| `evidence-file` | `mokata-evidence.md` | the evidence render (verdicts + gate evidence + committed run records), appended to the job's step summary |
| `fail-on-block` | `true` | fail the check on a real block (`false` = report-only) |

## Evidence in the comment

Under the verdicts the comment shows the gate **evidence**: the AC → test checks this check ran
(each test file linked at the PR's commit), and any **run record the PR committed** with
`mokata evidence commit` (`.mokata/runs/<run>/`: the run's approvals, writes, gate verdicts and
lowered gates, field-allowlisted and redacted). A committed record is the PR author's claim — it is
marked `[committed, unverified]` on every line and never changes the verdict. With none committed
the comment says so (*not opted in* / *none in this PR*), never "no evidence found". GitHub limits
a comment to 65,536 characters and a step summary to 1 MiB: mokata keeps failing sections first,
leaves any section out whole rather than cutting it, and says how many were left out and where to
find them.

## Run it locally

The same check runs in your terminal — no CI required:

```bash
# explicit changed files:
mokata ci-check --files src/payments.py,tests/test_payments.py

# or diff against a base ref (what CI does):
mokata ci-check --base origin/main --comment-file mokata-check-comment.md
```

Exit code is 1 on a real block, 0 on any verdict with `--no-fail`, and 2 when the check could not run (an unresolvable `--base`, usually a shallow checkout) even with `--no-fail`. Inside Claude Code the read-only
`ci_check` MCP tool returns the same verdict + comment body.

See also [the pipeline & gates](../concepts/pipeline.md) and
[`mokata spec-check`](../reference/cli.md) (the regression guard this builds on).
