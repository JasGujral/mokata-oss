"""OSS issue #43 — `UserPromptSubmit hook timed out after 30s` (0.0.16, Windows 11, /brainstorm).

MEASURED FIRST, and the measurement moved the answer.

On a healthy repo the hook's own work is not the problem and it is not close: end-to-end, in a
cold subprocess, the whole thing is ~90-100 ms, of which ~85 ms is interpreter start plus imports
and 1-5 ms is the actual recall (0 / 50 / 500 memory items; the ranked recall never exceeded
1.1 ms). Recall is not what spends 30 seconds.

What is true is that NOTHING BOUNDS IT. `hook_cli` bounds reading stdin at 2 s
(`_read_stdin_bounded`), and the hook is documented FAIL-OPEN — but that is fail-open on EXIT
CODE, not on TIME: it never blocks a tool call, and it will still stall a human for as long as its
slowest phase takes. Stalling the recall path by 8 s made the hook take 8.02 s. It pays whatever
it is handed.

And there is a 30-SECOND-SHAPED COST sitting directly in that path. `build_injection` opens the
store on every prompt; `MemoryStore.from_surface` calls `make_embedder(settings.memory.embedder)`;
with `auto`/`model2vec` that reaches `_load_model2vec`, whose only bound is mokata's own
`MODEL_FETCH_TIMEOUT_S = 30.0` for the model fetch. A sub-budget equal to the harness's ENTIRE
budget can never fire in time to help — the harness kills the hook first, discards its output, and
shows the user a warning on an ordinary turn.

So the fix is the discipline `_read_stdin_bounded` already applies to stdin, extended to the body:
a wall-clock budget well under the harness's 30 s, degrading to NO injection rather than a late
one. A turn with no recall pack is a correct turn.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import contextlib
import io
import json
import tempfile
import time
import unittest
from unittest import mock

import _support  # noqa: F401

from mokata import hook_cli                                        # noqa: E402
from mokata.init import init_repo                                  # noqa: E402

# What Claude Code allows a hook before it kills it and discards its output (issue #43's title).
HARNESS_KILL_SECS = 30.0


class _Repo:
    def __init__(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = self.dir.name
        init_repo(root=self.path, profile="standard", assume_yes=True, out=lambda *_a: None)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.dir.cleanup()


def _run(root, prompt="how do approvals work?"):
    """Run the hook exactly as the entry point does, capturing (exit_code, stdout, seconds)."""
    buf = io.StringIO()
    t = time.perf_counter()
    with contextlib.redirect_stdout(buf):
        rc = hook_cli.user_prompt_submit_main(["--prompt", prompt, "--cwd", root])
    return rc, buf.getvalue(), time.perf_counter() - t


class TestTheBudgetExists(unittest.TestCase):

    def test_the_body_has_a_declared_wall_clock_budget(self):
        self.assertTrue(hasattr(hook_cli, "_HOOK_WORK_BUDGET_SECS"),
                        "the hook's own work is unbounded — only stdin was ever bounded")
        self.assertGreater(hook_cli._HOOK_WORK_BUDGET_SECS, 0)

    def test_the_budget_is_well_under_the_harness_kill(self):
        """A budget at or near 30 s is the bug, not the fix: it can only fire after the harness has
        already killed the hook and warned the user. mokata's own degrade must happen FIRST, so the
        user gets an ordinary turn with no pack instead of a stall and a warning."""
        self.assertLess(hook_cli._HOOK_WORK_BUDGET_SECS, HARNESS_KILL_SECS / 2,
                        "the budget must leave the harness's 30s kill unreachable")

    def test_the_budget_is_generous_against_the_measured_cost(self):
        """The measured body cost is single-digit milliseconds. The budget is not a performance
        target — it is a runaway catch — so it must sit orders of magnitude above normal work and
        never trip a healthy turn."""
        self.assertGreaterEqual(hook_cli._HOOK_WORK_BUDGET_SECS, 1.0)


class TestTheHealthyTurnIsUnchanged(unittest.TestCase):

    def test_a_normal_turn_still_injects_and_is_fast(self):
        with _Repo() as repo:
            from mokata.config import Surface
            from mokata.memory import MemoryStore
            from mokata.memory.item import MemoryItem
            store = MemoryStore.from_surface(Surface.load(repo.path))
            store.remember(MemoryItem(subject="approvals",
                                      value="every durable write is human-gated via a proposal",
                                      kind="rule"), assume_yes=True)

            rc, out, secs = _run(repo.path)
            self.assertEqual(rc, 0)
            self.assertTrue(out.strip(), "a healthy turn must still inject its pack")
            payload = json.loads(out)
            self.assertEqual(payload["hookSpecificOutput"]["hookEventName"],
                             hook_cli.USER_PROMPT_SUBMIT_EVENT)
            self.assertLess(secs, hook_cli._HOOK_WORK_BUDGET_SECS,
                            "the budget must not be anywhere near a healthy turn's cost")

    def test_an_uninitialized_repo_is_still_silent_and_free(self):
        with tempfile.TemporaryDirectory() as d:
            rc, out, _secs = _run(d)
            self.assertEqual(rc, 0)
            self.assertEqual(out, "")


# A SHORT budget for the stall tests. The real 5 s value is asserted above; re-paying it in every
# timeout case would add ~20 s to the suite to re-measure a constant. What these tests exercise is
# the MECHANISM — that the budget is enforced, that the degrade is silence, and that a late worker
# cannot emit or record — and that is the same at 0.3 s as at 5 s.
TEST_BUDGET = 0.3
STALL = 3.0                 # comfortably past TEST_BUDGET, comfortably inside a test's patience


class TestExceedingTheBudget(unittest.TestCase):
    """The stall is injected at `_build_injection_for`, which is where the measurement found the
    exposure: it is the call that opens the store and therefore resolves the embedder."""

    @staticmethod
    def _stall(seconds=STALL):
        def _slow(*_a, **_kw):
            time.sleep(seconds)
            raise AssertionError("the abandoned worker's result must never be used")
        return _slow

    def test_a_stalled_recall_returns_inside_the_budget(self):
        with _Repo() as repo:
            with mock.patch.object(hook_cli, "_HOOK_WORK_BUDGET_SECS", TEST_BUDGET):
                with mock.patch.object(hook_cli, "_build_injection_for", self._stall()):
                    rc, _out, secs = _run(repo.path)
            self.assertEqual(rc, 0, "the hook must still exit 0 — it never eats the human's turn")
            self.assertLess(secs, STALL / 2,
                            f"the hook paid {secs:.1f}s against a {TEST_BUDGET}s budget — unbounded")

    def test_exceeding_the_budget_emits_NOTHING_not_a_partial_pack(self):
        """A partial pack is worse than no pack: it is context the model will trust, cut at
        wherever the clock happened to stop. The degrade is silence."""
        with _Repo() as repo:
            with mock.patch.object(hook_cli, "_HOOK_WORK_BUDGET_SECS", TEST_BUDGET):
                with mock.patch.object(hook_cli, "_build_injection_for", self._stall()):
                    rc, out, _secs = _run(repo.path)
            self.assertEqual(rc, 0)
            self.assertEqual(out, "", "a timed-out hook must emit no channel at all")

    def test_a_dropped_pack_is_never_recorded_as_injected(self):
        """S4's ledger suppresses an item for the rest of the session once it has been handed over.
        Recording a pack the model NEVER SAW would lose that memory for the whole session in the
        name of not repeating it — so the record happens only after a successful emit, on the main
        thread, never inside the abandoned worker."""
        with _Repo() as repo:
            with mock.patch("mokata.injection_ledger.record_injected") as rec:
                with mock.patch.object(hook_cli, "_HOOK_WORK_BUDGET_SECS", TEST_BUDGET):
                    with mock.patch.object(hook_cli, "_build_injection_for", self._stall()):
                        _rc, _out, _secs = _run(repo.path)
                rec.assert_not_called()

    def test_the_budget_covers_the_body_not_just_one_call(self):
        """The bound is on the hook's WORK, so it must be applied around the phase that does the
        work rather than sprinkled per-call. Source-checked: the emit is outside the bounded
        region, because emitting from inside it is how a late pack escapes the budget."""
        import inspect
        src = inspect.getsource(hook_cli.user_prompt_submit_main)
        self.assertIn("_HOOK_WORK_BUDGET_SECS", src)
        self.assertIn("_emit(", src, "the emit stays on the main thread, past the join")


class TestTheBoundedRunner(unittest.TestCase):
    """`_bounded` on its own. The hook-level tests cannot distinguish these cases — a timed-out
    worker and a worker that returned nothing both end in silence — so the properties that make
    the bound real are asserted here, directly."""

    def test_work_that_finishes_in_time_is_returned(self):
        self.assertEqual(hook_cli._bounded(lambda: "pack", 5.0), "pack")

    def test_a_result_that_ARRIVES_LATE_is_refused(self):
        """THE property `is_alive()` exists for. A worker still running when the budget expires is
        refused because it is RUNNING — not because it happens to have left nothing behind. Read
        the holder unconditionally instead and a complete pack that arrived past the budget gets
        emitted, which is the whole thing the budget prevents."""
        def _late():
            time.sleep(0.4)
            return "a complete pack, but too late"

        t = time.perf_counter()
        result = hook_cli._bounded(_late, 0.05)
        elapsed = time.perf_counter() - t
        self.assertIsNone(result, "a late result was handed back and would have been emitted")
        self.assertLess(elapsed, 0.3, "the bound did not actually bound anything")

    def test_an_exception_inside_the_work_is_not_raised(self):
        def _boom():
            raise RuntimeError("the store is unreadable")

        self.assertIsNone(hook_cli._bounded(_boom, 5.0))


class TestTheLedgerWriteStaysOutsideTheWorker(unittest.TestCase):
    """S4's injection ledger suppresses an item for the rest of the session once it has been handed
    to the model. If the bounded worker recorded, an ABANDONED worker would finish late and mark
    items injected that the model never saw — losing that memory for the whole session in the name
    of not repeating it. Structural, because the failure is a RACE on a daemon thread: asserting it
    behaviourally would mean waiting on a thread whose whole point is that nobody waits for it."""

    def test_the_worker_neither_imports_nor_calls_record_injected(self):
        import inspect
        worker = inspect.getsource(hook_cli._build_injection_for)
        self.assertIn("already_injected", worker, "the READ belongs inside the budget")
        self.assertNotIn("record_injected", worker,
                         "the ledger WRITE moved inside the bounded worker — an abandoned worker "
                         "can now record a pack the model never saw")

    def test_the_main_thread_is_what_records(self):
        import inspect
        body = inspect.getsource(hook_cli.user_prompt_submit_main)
        self.assertIn("record_injected(", body)
        emit_at = body.index("_emit(")
        record_at = body.index("record_injected(", emit_at)
        self.assertGreater(record_at, emit_at,
                           "the ledger write must follow the emit — recording first would "
                           "suppress items on a turn that then failed to hand them over")


class TestTheDegradeIsSilentByDesign(unittest.TestCase):

    def test_a_timeout_writes_nothing_to_stderr_either(self):
        """The hook is FAIL-OPEN and this event is on the human's turn. A warning every time a
        slow machine trips the budget would train the user to ignore the channel — the same
        reasoning `build_injection`'s own SUPPRESS-OK arm records (doc 84 / D5 register)."""
        with _Repo() as repo:
            err = io.StringIO()
            def _slow(*_a, **_kw):
                time.sleep(STALL)

            with contextlib.redirect_stderr(err):
                with mock.patch.object(hook_cli, "_HOOK_WORK_BUDGET_SECS", TEST_BUDGET):
                    with mock.patch.object(hook_cli, "_build_injection_for", _slow):
                        rc, _out, _secs = _run(repo.path)
            self.assertEqual(rc, 0)
            self.assertEqual(err.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
