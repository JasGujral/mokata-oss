# See the proof: AC → test → code (`mokata ide` · Proof)

The **Proof** screen of `mokata ide` shows, for a change, the thing no other agent tool can show:
every acceptance criterion of the spec, the tests recorded for it with their **RED-then-GREEN**
history, the code those tests exercise, and the approvals and gate verdicts that let each part in.
On top of it, a **risk overlay**: criteria with no test, tests that never went red ("not
TDD-proven"), code changed without a test, blast radius, gates red or skipped.

It is computed from records mokata already keeps — the emitted spec, the run's test record, the
TDD phase, the code graph, the audit ledger, the progress log. Nothing is guessed, nothing is
asked of a model, and **no API key is involved**: mokata never calls a model.

## On a repository you just opened (no run yet)

```bash
pip install "mokata[ide]"
cd your-repo
mokata ide
```

Open **Review › Proof** (the Proof sub-tab under the Review tab — or **Open Proof** when the last
changed file is marked seen). On a repository with no mokata run the screen opens
on the **working tree**: the changed definitions, which tests reach each one (from the code graph,
or the built-in AST floor when no graph is installed — the screen names which), their callers
(blast radius), and the definitions no test reaches, flagged. The **What to prove** picker at the
top also takes a **commit range** (`base` … `head`). Under the trace, **Tests by acceptance
criterion** groups the run's tests by the AC they prove and re-runs one or all.

Without a run there are no acceptance criteria to prove, and the screen says so. The AC half comes
from a run.

## The full trace (a run)

In Claude Code with the mokata plugin: `/brainstorm` → `/spec` emits the spec and seeds the AC→test
map from the tests it names → write the tests and record them **failing**:

```bash
mokata progress test-run --failed --command "pytest -q" \
  --test tests/test_parser.py::test_empty_input --ac AC-1=tests/test_parser.py::test_empty_input
```

→ implement → record them **passing** with the same command and `--passed`. Open **Proof** and pick
the run. A test's state is its **latest** recorded outcome; *TDD-proven* means its first recorded
run was red, it went green after that, and its latest run is green. A test only ever recorded
passing is *not TDD-proven*; an AC with no recorded test is *untested*.

Record every test by its **full id** — pytest's node id, `--test tests/test_x.py::TestX::test_y`
(or `tests/test_x.py::test_y` for a module-level test). A bare name (`--test test_y`) is shown but
**never proves**. The rule, for every recorded test: it proves only when its full id names a file
git tracks in the tree being judged (not ignored) whose Python AST defines that exact
`Class::test` **once**. Two classes with one method name are two tests; a class the file does not
hold is *unlocated*; a name the file defines twice makes `path::name` *ambiguous*. Anything else is
shown with its reason and proves nothing: *ambiguous*, *unlocated*, *unparsed* (a file mokata
cannot parse), *ignored* (gitignored / untracked at head), *deleted* (git shows the removal). A
parametrised id (`test_x[case]`) maps to its test, and the test is proven only when every recorded
id of it went red, then green, and is green now.

Every spelling of one file is one test: `tests/./x.py`, `tests//x.py`, `tests/../tests/x.py`,
`tests\x.py` — and, where the filesystem folds case (probed, whatever git's
`core.ignorecase` says), `Tests/x.py` — all name `tests/x.py`, so a red under
any of them counts. An id pytest printed relative to its own rootdir (`tests/x.py` run inside
`backend/`, which holds a `pytest.ini` / `pyproject.toml` / `tox.ini` / `setup.cfg`) resolves to
`backend/tests/x.py`. If more than one tracked file could be meant — a root-level `tests/x.py`
beside `backend/tests/x.py`, config file or not — the id is *ambiguous*. A test a
runner would never collect (a class neither `Test*` nor derived, a class with `__init__`,
`__test__ = False`, a fixture, a def under `if __name__ …`) is *unlocated*. A proof is of the test
**as it ran**: edit it after its green — a `@skip`, an `assert 0` — and it is no longer proven
until you run it again. A later red under a bare or dotted name that could be this test
(`test_y`, `tests.test_x.TestX.test_y`) blocks the proof too.

A bare name is never guessed into a full id. It is recorded under one only when **the test runner
itself reported that test**: pass the runner's output for the run with
`--runner-report <file>` (pytest's `-rA`, `-v` or `--collect-only -q` text, or a JUnit XML), and a
bare name that output names exactly once is recorded under that node id — the test then says it
was "recorded as `test_y`". A dotted unittest id (`pkg.test_x.TestX.test_y`) names its module and
is recorded as `pkg/test_x.py::TestX::test_y`. Anything else stays bare, proves nothing, and its
AC says so: *bare id: run with full ids to prove*.

A proof is of the test **as it runs**: its stamp covers the test itself, the non-test code of its
file and class (helpers, `setUp`, marks) and every `conftest.py` on its path — formatting and
comments do not count. Out of reach, by design: code the test imports from other modules, plugins,
ini options, and the code under test (that is what the test is for).

A commit range is judged **at its head**: if the working tree is not that commit, or a changed or
test file — or a file the blast walk crossed — is modified or untracked, coverage reads *unknown*
and the screen says why — an uncommitted test never makes committed code "covered".

## The demo, reproducible

```bash
python scripts/demo_proof.py
```

builds a small repository of its own (never yours) with one run seeded exactly as above — one AC
proven red→green, one covered by a test that never went red, one with no test; a definition no test
reaches; one gate red and one skipped — and opens the IDE on it. `mokata ide --demo` shows the same
screen on a clearly labelled SAMPLE with nothing behind it.

## At scale

The trace is bounded by the **change set**, not the repository: one graph question per changed
definition, so a million-line repository with a three-file change costs three files. Thousands of
criteria and tests are **paged** (the pager under the flow), never capped; when a page holds more
nodes than the picture draws, the screen says *drawing N of M* and **show all** lists the same page
as a table. A wide change set runs as a visible job (the Activity drawer: progress per file,
Cancel).

## For tools built on it

`GET /api/proof?run=…|session=…|base=…&head=…|live=1[&page=&size=]` answers the trace as versioned
JSON (`version`, the `Trace` schema in `mokata/ide/features/proof.contract.json`); `GET
/api/proof/targets` lists what can be asked. The change explainer (P46) reads the same shape.

## Related

- [Review what your agent wrote](review-agent-work.md) — the review window itself
- [Use a codebase graph](use-a-codebase-graph.md) — what the graph adds to the code half
