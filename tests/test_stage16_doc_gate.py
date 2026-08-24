"""Stage 16 (CI-SPEED) — the only mechanical doc gate RUNS, and reds when it should.

`scripts/check-tracker-tables.py` is called "the ONLY mechanical doc gate" by
`tests/test_s11_bookkeeping_derived.py`, doc 99 cites its `exit 0` as a release-check line, and
until 0.0.18 **nothing executed it**. It was red at HEAD and nobody knew, because a gate that
nothing runs is indistinguishable from a gate that passes.

WHY THIS IS A UNIT TEST AND NOT A CI STEP — the stage brief said to wire it into CI, and that
instruction cannot be carried out as written. Three homes were available and two of them are
green-by-never-running:

  * **A plain step in `ci.yml`.** `ci.yml` carries no repository guard, so it runs on the PUBLIC
    mirror too — where `sync-public.sh` excludes BOTH this checker and the `docs/build/` corpus it
    reads. The step would invoke a missing script against a missing corpus on every mirror run.

  * **A step guarded to the dev repo** (`if: github.repository != '…mokata-oss'`). Correct in
    principle, and it resolves to "run it where nothing runs": Actions on the dev repo are
    billing-blocked — measured 2026-08-16, run `31951396347` failed in 2s without starting a
    step — while the mirror's Actions execute normally. Wiring the only mechanical doc gate to the
    one runner that cannot start a job reproduces the exact defect this file exists to close.

  * **A `pre-push` hook.** Runs where the docs are written and needs no runner, but `.git/hooks/`
    is untracked: it does not survive a fresh clone, so it is a convention rather than a control.
    `CLAUDE.md`'s `.gitignore`-does-not-control-this rule is the same shape — an accident, not a
    control.

So the gate is a TEST. It is tracked, it survives a clone, it needs no runner and no billing, it
runs on every stage's suite run in the repo where `docs/build/` actually exists, and when the dev
repo's Actions come back it runs in CI automatically as part of the suite — with no second
mechanism to keep in step.

THE GUARD IS THE DERIVED SHAPE, NOT A HAND-ADDED DECORATOR. `tests/` ships to the mirror, and this
module reads two paths that do not (`SHIPPED-TEST-READS-INTERNAL-FILE`, stage 28). The class
therefore carries the one guard `tests/_shipped_reads.py` accepts — a class-level
`@unittest.skipUnless(os.path.exists(...) and os.path.exists(...))`. The `and` is the shape that
module explicitly sanctions for a class needing two internal files; an `or` would be "the un-guard
wearing a guard's clothes."

AND THE GREEN ASSERTION IS THE WEAK HALF, DELIBERATELY STATED. `test_living_docs_are_well_formed`
passes today and would pass just as well against a checker that had stopped checking. The gate is
the PLANTED half (§7i): each red case constructs a corpus whose defect is known, and asserts the
checker both fails AND names the offending row. A pin that only proves the clean corpus is clean
is the absence-assertion satisfied by any silence that stage 18's M07 survivor was.
"""

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECKER = os.path.join(ROOT, "scripts", "check-tracker-tables.py")
DOCS_BUILD = os.path.join(ROOT, "docs", "build")

#: A four-column header. Every planted case below differs from the clean case in exactly one row,
#: so a red proves the ROW is what was detected rather than the file being unparseable.
_HEADER = "| id | stage | verdict | approved on |\n|---|---|---|---|\n"
_GOOD_ROW = "| A1 | one | PASS | 2026-08-16 |\n"


def _check(*targets):
    """Run the checker over explicit targets (or the living docs when none are given)."""
    proc = subprocess.run(
        [sys.executable, CHECKER, *targets],
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout + proc.stderr


def _corpus(body):
    """Write a one-file corpus and return its path, cleaned up by the caller's tempdir."""
    handle = tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8"
    )
    with handle as fh:
        fh.write("# planted\n\n" + _HEADER + body)
    return handle.name


@unittest.skipUnless(
    os.path.exists(CHECKER) and os.path.exists(DOCS_BUILD),
    "check-tracker-tables.py and docs/build/ are dev-only, excluded from the public mirror",
)
class TestDocGateRuns(unittest.TestCase):
    """The gate executes, over the real corpus, on every suite run."""

    def tearDown(self):
        for path in getattr(self, "_planted", ()):
            try:
                os.unlink(path)
            except OSError:  # pragma: no cover - best-effort cleanup
                pass

    def plant(self, body):
        path = _corpus(body)
        self._planted = getattr(self, "_planted", []) + [path]
        return path

    def test_living_docs_are_well_formed(self):
        """The real corpus passes — the half that would also pass on a broken checker."""
        code, out = _check()
        self.assertEqual(code, 0, "the living docs carry a malformed tracker row:\n" + out)

    def test_reds_on_an_extra_cell(self):
        """§7i — five cells against a four-column header DROPS the trailing cell at render."""
        path = self.plant(_GOOD_ROW + "| A2 | two | PASS | EXTRA | 2026-08-16 |\n")
        code, out = _check(path)
        self.assertNotEqual(code, 0, "an extra cell must red the gate, got exit 0:\n" + out)
        self.assertIn("A2", out, "the refusal must name the offending row, not just count it")
        self.assertNotIn("A1", out, "the well-formed row must not be reported")

    def test_reds_on_a_missing_cell(self):
        """Fewer cells render as a blank trailing column — 'no verdict recorded', not a defect."""
        path = self.plant(_GOOD_ROW + "| A2 | two | PASS |\n")
        code, out = _check(path)
        self.assertNotEqual(code, 0, "a short row must red the gate, got exit 0:\n" + out)
        self.assertIn("A2", out, "the refusal must name the offending row")

    def test_reds_on_an_unescaped_pipe_in_a_code_span(self):
        """The defect that was actually live at HEAD one commit ago.

        GFM splits cells BEFORE inline parsing, so a pipe inside a code span still breaks the row.
        Python-Markdown does not, which is precisely how this survived — the lenient renderer
        showed a correct table. The checker counts the way GitHub does.
        """
        path = self.plant(_GOOD_ROW + "| A2 | `a | b` | PASS | 2026-08-16 |\n")
        code, out = _check(path)
        self.assertNotEqual(
            code, 0, "an unescaped pipe in a code span must red the gate, got exit 0:\n" + out
        )

    def test_escaping_the_pipe_is_what_makes_it_pass(self):
        """The remedy the refusal names is RUN, not quoted — the escaped form must be accepted."""
        path = self.plant(_GOOD_ROW + "| A2 | `a \\| b` | PASS | 2026-08-16 |\n")
        code, out = _check(path)
        self.assertEqual(code, 0, "the escaped pipe is the documented fix and must pass:\n" + out)


# ----------------------------------------- C1: the gate's SECOND check — the PostgreSQL floor
#: The superseded major, as a bare number with no comparator beside it — so this file states the
#: offending SHAPE without becoming an instance of it in the shipped floor sweep's own corpus.
#: (`tests/test_pg_floor_drift.py` grades the whole git index, and that includes this file.)
_STALE_MAJOR = 14


@unittest.skipUnless(
    os.path.exists(CHECKER) and os.path.exists(DOCS_BUILD),
    "check-tracker-tables.py and docs/build/ are dev-only, excluded from the public mirror",
)
class TestTheDocGateAlsoChecksTheFloor(unittest.TestCase):
    """C1 (0.0.19), closing `PG-FLOOR-INTERNAL-LEDGER-UNGUARDED`.

    `tests/test_pg_floor_drift.py` denies `docs/build/` wholesale, because that tree is excluded
    from the public mirror twice over and a SHIPPED test reading it would be
    `SHIPPED-TEST-READS-INTERNAL-FILE`. The hole was not at the edges: doc 85 §1's headline and doc
    84's FINALIZED ledger cell each stated a superseded floor for thirteen days, and both are
    documents a stage is told to treat as standing truth.

    The internal half therefore lives in the one mechanical doc gate that already never ships, and
    it is graded HERE the way the shape half above is — by PLANTING a corpus whose defect is known
    and requiring the gate to red AND to name it. The clean-corpus assertion is the weak half and
    is stated as such: it would pass just as well against a checker that had stopped checking.

    ⚠ WHY THE PLANTED FILES ARE NAMED ON THE COMMAND LINE. An explicit target list is a SUBSET, so
    the gate cannot judge an accounting entry ABSENT from it — that distinction is the caller's,
    and `check_floor`'s `whole_corpus` flag is where it lives. A first draft collapsed it and reded
    on every explicit-target run, which is a gate that disagrees with itself about the same tree.
    `test_a_correct_claim_on_an_explicit_target_passes` is the pin that keeps it split.
    """

    def tearDown(self):
        for path in getattr(self, "_planted", ()):
            try:
                os.unlink(path)
            except OSError:  # pragma: no cover - best-effort cleanup
                pass

    def plant(self, body):
        handle = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False,
                                             encoding="utf-8")
        with handle as fh:
            fh.write("# planted\n\n" + body)
        self._planted = getattr(self, "_planted", []) + [handle.name]
        return handle.name

    def test_the_living_docs_state_the_declared_floor_or_are_accounted_for(self):
        """The real corpus — and the half a broken checker would also pass."""
        code, out = _check()
        self.assertEqual(code, 0, "an internal doc states an undeclared PostgreSQL floor:\n" + out)
        self.assertIn("floor claim", out,
                      "the gate reported nothing about the floor at all — it ran the shape check "
                      "and silently skipped the half this class exists for:\n" + out)

    def test_reds_on_an_internal_doc_stating_an_undeclared_floor(self):
        """§7i — the planted offender. This is the sentence doc 84 and doc 85 each carried for
        thirteen days, and nothing mechanical could see it."""
        path = self.plant("Team mode runs on vanilla PostgreSQL >= %d, no extensions.\n"
                          % _STALE_MAJOR)
        code, out = _check(path)
        self.assertNotEqual(code, 0,
                            "a stale internal floor claim must red the gate, got exit 0:\n" + out)
        self.assertIn(str(_STALE_MAJOR), out,
                      "the refusal must name the wrong number, not just count it")

    def test_a_correct_claim_on_an_explicit_target_passes(self):
        """The negative, and it is the one that caught this gate out. A guard that reds on correct
        code is one a reader learns to route around."""
        path = self.plant("Team mode runs on vanilla PostgreSQL >= 15, no extensions.\n")
        code, out = _check(path)
        self.assertEqual(code, 0, "a correct floor claim reded the gate:\n" + out)

    def test_an_image_tag_is_not_convicted_as_a_floor_claim(self):
        """The distinction the shipped detector rests on, re-asserted at the gate: `postgres:16`
        asserts no minimum, and grading a pin for equality would red on a good upgrade."""
        path = self.plant("    image: postgres:%d\n" % _STALE_MAJOR)
        code, out = _check(path)
        self.assertEqual(code, 0, "an image tag was convicted as a floor claim:\n" + out)

    def test_the_gate_refuses_rather_than_passes_when_the_floor_cannot_be_read(self):
        """A gate that cannot find the DECLARATION has checked nothing, and 'checked nothing' must
        not share a representation with 'everything is fine' (doc 85 §7g). Driven through the
        module's own seam, because the declaration's absence cannot be planted on disk without
        editing `src/`."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("_ctt_probe", CHECKER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        # ⚠ STDOUT IS CAPTURED, and not for tidiness. The gate's refusal line starts with "FAIL:",
        # and printed into a green suite run it reads to a human — and to a log grep — as a failing
        # test. This repo has now paid twice for evidence that was true and read as something else.
        buffer = io.StringIO()
        with mock.patch.object(module.floor, "declared_floor", lambda root: None):
            with contextlib.redirect_stdout(buffer):
                code = module.main(["check-tracker-tables.py"])
        self.assertEqual(1, code, "the gate exited 0 with no floor to grade against")
        self.assertIn("graded NOTHING", buffer.getvalue(),
                      "the gate refused without saying it had checked nothing — 'checked nothing' "
                      "and 'everything is fine' must not share a representation (doc 85 §7g)")


if __name__ == "__main__":  # pragma: no cover - manual invocation
    unittest.main()
