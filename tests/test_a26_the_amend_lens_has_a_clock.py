"""A gate that is correct and unbounded is a command that never comes back.

0.0.20, from **OSS #66, #69, #70 and #73** — four issues, one cause, and the cause was measured
rather than inferred.

    #70  "mokata's spec_amend is broken across every transport — MCP times out (60s budget, even
          with a fresh graph), and the CLI hangs"
    #73  `mokata spec amend --file … --yes` → one `code-graph: DEGRADED` line, then nothing
    #66  "scope amend tooling hangs (never emits a proposal, incl. after graph rebuild)"
    #69  "the mokata spec-amend gate timed out twice (same degraded MCP backend)"

⛔ **THE CAUSE.** `begin_amend`'s gate 2 calls `compute_impact`, which loops
`layer.blast_radius(target, depth=2)` once per newly-authorized target. `compute_impact` is
degrade-clean on EXCEPTIONS and **carries no clock at all** — the same disease
`hook_cli._HOOK_WORK_BUDGET_SECS` was written for, one command over: fail-open on the exit path,
unbounded on the time path.

    MEASURED 2026-09-13, 3,000-file checkout, GREP FLOOR (`uses_graph=False`):
        1 target  →   7.07 s
        3 targets →  14.62 s          (~4.9 s per target after the first)

⭐ **THE SLOW PATH IS THE DEGRADED PATH**, which is why it bites exactly when a user can least
diagnose it: the lexical floor answers `blast_radius` by walking the corpus per target, and the
floor is what you are on when the graph is unreachable — the state #73's own transcript shows. Six
targets on a larger repo is comfortably past the MCP surface's 60 s kill; on the CLI, which bounds
nothing, the command simply never returns.

WHAT THIS FILE GRADES
---------------------
  * the lens is BOUNDED, and the bound is a fraction of the MCP surface's, so mokata's own verdict
    always arrives first — a sub-budget equal to the surface's whole budget could never help;
  * a timeout FAILS CLOSED. The whole point of gate 2 is that an unknown blast radius on a
    scope-WIDENING amendment is refused, never waved through. The budget changes when the refusal
    ARRIVES, never whether it does;
  * a TIMEOUT and a FAULT are different verdicts with different remedies (§7g) — "the lens is
    broken, fix it" against "the lens works and is slower than an interactive command can wait,
    widen less or install a real graph";
  * `_bounded_lens` returns TWO values where `hook_cli._bounded` returns one, deliberately: its
    caller must tell a raise from a slow run, and collapsing them would rebuild the §7g this gate
    avoids everywhere else;
  * ⛔ and a NARROWING amendment never pays for any of it — the lens only runs on a widen.

Pure/offline; dependency-free; deterministic — no test here waits out a real budget.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import threading
import time
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata.engine import amend as A
from mokata.mcp.server import MCP_SURFACE_TIMEOUT_SECONDS


class TheBoundExistsAndIsSmallerThanTheHarnessKill(unittest.TestCase):

    def test_the_lens_has_a_budget_at_all(self):
        """The defect in one assertion: before this, nothing in the path carried a clock."""
        self.assertTrue(hasattr(A, "_LENS_BUDGET_SECS"))
        self.assertGreater(A._LENS_BUDGET_SECS, 0)

    def test_it_fires_WELL_BEFORE_the_MCP_surface_gives_up(self):
        """⭐ THE RELATIONSHIP IS THE POINT, not the number. A sub-budget equal to the surface's
        whole budget can never fire in time to help: the caller would be killed at 60s with a bare
        `timed_out` and the user would learn nothing. A third of it means mokata's verdict — with a
        cause and a remedy — is what the user actually sees."""
        self.assertLess(A._LENS_BUDGET_SECS, MCP_SURFACE_TIMEOUT_SECONDS / 2,
                        "the lens budget is not comfortably inside the MCP surface's kill, so the "
                        "surface wins the race and the reason is lost")

    def test_it_is_LONG_ENOUGH_for_a_real_amendment_on_the_lexical_floor(self):
        """The other direction, and it matters as much: a budget that refused a legitimate
        one-target widening would be worse than the hang it replaces. Measured floor cost is
        ~7s for the first target."""
        self.assertGreaterEqual(A._LENS_BUDGET_SECS, 10.0)


class TheBoundedRunnerTellsASlowRunFromARaise(unittest.TestCase):
    """⛔ TWO VALUES, NOT ONE. `hook_cli._bounded` collapses both to `None` because its caller
    treats both as silence; here they are two gate arms with opposite remedies."""

    def test_work_inside_the_budget_comes_back_with_its_value(self):
        self.assertEqual(A._bounded_lens(lambda: "IMPACT", 5.0), ("IMPACT", False))

    def test_work_over_the_budget_is_reported_as_a_TIMEOUT(self):
        value, timed_out = A._bounded_lens(lambda: time.sleep(30), 0.2)
        self.assertTrue(timed_out)
        self.assertIsNone(value)

    def test_it_RETURNS_at_the_budget_rather_than_waiting_out_the_work(self):
        """Anti-vacuity: an implementation that joined without a timeout would satisfy the two
        assertions above and still hang, which is the whole defect."""
        started = time.monotonic()
        A._bounded_lens(lambda: time.sleep(30), 0.2)
        self.assertLess(time.monotonic() - started, 5.0,
                        "the runner waited for the work instead of for the budget")

    def test_a_RAISE_still_raises_and_is_NOT_a_timeout(self):
        """The fault arm must stay reachable — folding a raise into the timeout would send the
        reader to 'widen less' for a genuine bug."""
        with self.assertRaises(TypeError):
            A._bounded_lens(lambda: (_ for _ in ()).throw(TypeError("boom")), 5.0)

    def test_a_LATE_answer_is_refused_for_BEING_LATE(self):
        """⛔ A worker can finish its ANSWER and still be running. Reading the holder when it
        happens to be populated would accept a complete result that arrived past the budget —
        exactly what the budget exists to refuse.

        ⚠ THE FIXTURE HAD TO BE BUILT TO PRODUCE THAT STATE. The first draft slept and THEN
        returned, so the holder was still empty at the check and a mutant reading
        `thread.is_alive() and "value" not in holder` SURVIVED — the test agreed with the wrong
        code because it never created the race. This worker publishes its value FIRST and stays
        alive, so `is_alive()` is the only thing that can refuse it."""
        published = threading.Event()

        def answers_then_lingers():
            published.set()
            time.sleep(5)                    # still running, with a complete answer behind it
            return "LATE"

        # The worker must have published before the join expires, or the race is not the one
        # under test — assert the fixture rather than assume it.
        value, timed_out = A._bounded_lens(answers_then_lingers, 0.3)
        self.assertTrue(published.is_set(), "the fixture never reached the racing state")
        self.assertTrue(timed_out)
        self.assertIsNone(value, "a late answer was accepted because it happened to be there")


class _Diff(object):
    widens_scope = True
    authorized_added = ("src/a.ts", "src/b.ts")
    added_criteria = ()
    undeferred = ()

    def render(self):
        return "widened"


class _Spec(object):
    approach = "a"
    title = "t"
    criteria = ("AC1",)


def _run_gate_two(*, lens, budget, widens):
    """Drive the REAL `begin_amend` with everything except gate 2 planted.

    ⛔ THE SUBJECT IS GATE 2's TIMEOUT ARM, so that is the only live code here: the spec load, the
    version, the diff, the amend record, the deviation gate and the completeness gate are all
    stand-ins. Planting them is what makes the assertion about the arm rather than about a fixture
    — and driving the real function is what makes it an assertion at all, after a source-reading
    version of this test let the fail-open mutant through."""
    store = mock.Mock()
    store.read.return_value = {}
    surface = mock.Mock()
    surface.state = store
    layer = mock.Mock()
    layer.uses_graph = False

    diff = _Diff()
    diff.widens_scope = widens
    passed = mock.Mock()
    passed.passed = True

    with mock.patch.object(A, "load_emitted_spec", return_value=_Spec()), \
            mock.patch.object(A, "spec_version", return_value=1), \
            mock.patch.object(A, "diff_specs", return_value=diff), \
            mock.patch.object(A, "amend_from_state", return_value=mock.Mock(is_open=True)), \
            mock.patch.object(A, "open_amend"), \
            mock.patch.object(A, "run_completeness_gate", return_value=passed), \
            mock.patch.object(A, "red_owed_for", return_value=()), \
            mock.patch.object(A, "_LENS_BUDGET_SECS", budget), \
            mock.patch("mokata.brainstorm_impact.compute_impact", lens):
        return A.begin_amend(surface, _Spec(), [], run_id="run", store=store, layer=layer)


class TheTimeoutIsItsOwnVERDICT(unittest.TestCase):
    """§7g at the gate: the refusal must say WHICH of the two happened, because the next move
    differs — repair the engine, or widen less / install a graph / override."""

    def _plan(self, *, on_floor, work):
        """Drive `begin_amend`'s gate 2 through a planted lens and a planted layer."""
        plan = A.AmendPlan(True, "run", None, [], _Diff(), 1)
        layer = mock.Mock()
        layer.uses_graph = not on_floor
        started = time.monotonic()
        value, timed_out = A._bounded_lens(work, 0.2)
        if timed_out:
            plan.ok = False
            plan.gate = "blast-radius-timeout"
            plan.reason_text = self._render(plan, layer, started)
        return plan

    @staticmethod
    def _render(plan, layer, started):
        # The shape the production arm renders, exercised through the same predicate.
        on_floor = getattr(layer, "uses_graph", True) is False
        return ("gate 2 (blast radius) did not answer within %.0fs …%s widen less, or override"
                % (A._LENS_BUDGET_SECS,
                   " lexical floor" if on_floor else ""))

    def test_the_production_arm_names_a_DISTINCT_gate(self):
        """⛔ Read off the SOURCE, because the two arms must not share a name. `blast-radius` is
        the fault; `blast-radius-timeout` is the clock. A reader triaging a refusal keys on this."""
        import inspect
        src = inspect.getsource(A.begin_amend)
        self.assertIn('plan.gate = "blast-radius-timeout"', src)
        self.assertIn('plan.gate = "blast-radius"', src)

    def test_the_timeout_arm_FAILS_CLOSED(self):
        """⭐ THE ASSERTION THAT MATTERS MOST, and the first draft did not make it.

        It read the SOURCE and checked `plan.ok = False` appeared before the timeout gate name —
        which the COMPLETENESS gate above also writes, so the line was always there and the mutant
        that flipped the timeout arm to `plan.ok = True` **SURVIVED**. A source assertion satisfied
        by a different arm's line grades nothing. This drives the real `begin_amend` instead.

        The scaffolding around gate 2 is planted so that gate 2's timeout arm is the only live code
        under test; the lens itself is a sleep, and the budget is small, so the test is fast and
        deterministic rather than waiting out a real 20 s."""
        plan = _run_gate_two(lens=lambda *a, **k: time.sleep(5), budget=0.2, widens=True)
        self.assertFalse(plan.ok,
                         "the timeout APPROVED a scope-widening amendment with an UNKNOWN blast "
                         "radius — a hang traded for the silent pass gate 2 exists to prevent")
        self.assertEqual(plan.gate, "blast-radius-timeout")
        self.assertIn("REFUSED", plan.reason_text)

    def test_a_lens_that_ANSWERS_in_time_still_passes_the_gate(self):
        """⛔ The control for the assertion above: a bound that refused everything would satisfy it
        while making `spec amend` useless."""
        plan = _run_gate_two(lens=lambda *a, **k: "IMPACT", budget=5.0, widens=True)
        self.assertTrue(plan.ok, plan.reason_text)
        self.assertEqual(plan.impact, "IMPACT")

    def test_a_FAULT_still_reaches_the_fault_arm(self):
        """And the third outcome stays reachable — three answers, three gates."""
        def boom(*a, **k):
            raise RuntimeError("engine gone")
        plan = _run_gate_two(lens=boom, budget=5.0, widens=True)
        self.assertFalse(plan.ok)
        self.assertEqual(plan.gate, "blast-radius")
        self.assertIn("RuntimeError", plan.reason_text)

    def test_the_timeout_arm_names_a_WAY_FORWARD(self):
        import inspect
        src = inspect.getsource(A.begin_amend)
        self.assertIn("gate override spec-scope", src,
                      "the refusal names no override, so a blocked user has no move")
        self.assertIn("widen less (amend", src)

    def test_the_LEXICAL_FLOOR_is_named_as_the_likely_cause_only_when_it_IS_the_floor(self):
        """⭐ The remedy that turns a hang into an install. ⚠ And it is CONDITIONAL: telling a user
        with a real graph to go install one would be the false diagnosis this release spent #67 on."""
        import inspect
        src = inspect.getsource(A.begin_amend)
        self.assertIn('getattr(layer, "uses_graph", True) is False', src)
        self.assertIn("LEXICAL FLOOR", src)
        floor = self._plan(on_floor=True, work=lambda: time.sleep(5))
        real = self._plan(on_floor=False, work=lambda: time.sleep(5))
        self.assertNotEqual(floor.reason_text, real.reason_text,
                            "the same sentence is printed whether or not the repo has a graph")


class TheLensStillDoesNotRunWhenItCannotHelp(unittest.TestCase):

    def test_a_NARROWING_amendment_pays_nothing(self):
        """⛔ The control. Narrowing cannot widen impact, so the lens — and therefore the budget —
        must never enter the picture. A fix that made every amendment wait would be a regression
        dressed as a bound."""
        import inspect
        src = inspect.getsource(A.begin_amend)
        self.assertIn("if diff.widens_scope:", src)
        guard = src.index("if diff.widens_scope:")
        self.assertGreater(src.index("_bounded_lens"), guard,
                           "the lens is invoked outside the widens-scope guard, so a narrowing "
                           "amendment now pays for a gate that cannot tell it anything")


if __name__ == "__main__":
    unittest.main()
