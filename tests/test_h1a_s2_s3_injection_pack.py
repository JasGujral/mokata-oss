"""H-1a S2/S3 — the per-turn injection PACK: budgeted, read-only, degrade-silent, scoped.

Five pins, P1–P5, and each one is written to fail for the reason it names rather than for any
reason at all.

P1  BUDGET — hard, and checked on the bytes that actually LEAVE the hook. Not on the builder's
    self-reported number (which a bug would report just as confidently), not on a target: the
    assertion is `estimate_tokens(<emitted additionalContext>) <= INJECTION_TOKEN_BUDGET`.

P2  READ-ONLY — pinned BEHAVIOURALLY, by hashing the whole repo tree around a full injection
    turn. This is the pin the defect class demands: a name-based sweep ("the hook does not call
    `record_usage`") passes for every mutation that lives one module down, and the two most
    likely regressions here — `jit_recall` switching to `recall_relevant`, or the injectable read
    switching back to `all_active` — are exactly that shape.

P3  DEGRADE — emits NO `hookSpecificOutput` at all. An empty `additionalContext` is still a
    channel: it costs the harness a parse and says "mokata spoke and had nothing", which is not
    the same as mokata staying out of the way. Exit 0, no stderr, and — separately verified in a
    subprocess — the memory stack is not even IMPORTED for a repo mokata isn't set up in.

P4  SCOPE — the C1 guarantee survives the trip through the pack (a live-Postgres leg covers the
    same claim on a real engine, in `tests/integration/test_h1a_live_db.py`).

P5  FAIL-OPEN at all five arms — stdin parse, `Surface.load`, `MemoryStore` construction, recall,
    budget cap. Exit 0, empty stdout, prompt unchanged. On UserPromptSubmit a non-zero exit does
    not block a tool call, it EATS THE HUMAN'S TURN.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)
from _support import sqlite_disk_ok, tree_snapshot

from mokata import bootstrap, hook_cli
from mokata.bootstrap import (INJECTION_RULES_MAX_LINES, INJECTION_TOKEN_BUDGET,
                              build_injection, estimate_tokens)
from mokata.config import Surface
from mokata.init import init_repo
from mokata.injection_ledger import LEDGER_DIRNAME
from mokata.memory import MemoryStore
from mokata.memory.item import MemoryItem

QUERY = "how do I deploy the release pipeline?"
SUB = "user-prompt-submit"


def _repo(d, *, rules=(), items=()):
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
    surface = Surface.load(d)
    store = MemoryStore.from_surface(surface)
    for i, (subject, value) in enumerate(rules):
        store.backend.put(MemoryItem.create(subject, value, kind="rule", id=f"r{i}"))
    for i, (subject, value) in enumerate(items):
        store.backend.put(MemoryItem.create(subject, value, kind="context", id=f"c{i}"))
    return surface


def _run_hook(prompt, cwd):
    """Run the hook exactly as the harness does — a JSON envelope on stdin — and return
    (exit_code, stdout, stderr)."""
    payload = json.dumps({"prompt": prompt, "cwd": cwd, "session_id": "s-test"})
    out, err = io.StringIO(), io.StringIO()
    saved, sys.stdin = sys.stdin, io.StringIO(payload)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = hook_cli.main([SUB])
    finally:
        sys.stdin = saved
    return code, out.getvalue(), err.getvalue()


def _emitted(stdout):
    """The `additionalContext` the hook actually emitted, or None when it emitted no channel."""
    if not stdout.strip():
        return None
    payload = json.loads(stdout)["hookSpecificOutput"]
    assert payload["hookEventName"] == "UserPromptSubmit", payload
    return payload["additionalContext"]


# The S4 ledger's own leaf, named from the module that owns it so the read-only carve-out below
# cannot drift into meaning something wider than one directory.
_LEDGER_REL = os.path.join(".mokata", "temp_local", LEDGER_DIRNAME) + os.sep


def _governed_snapshot(root):
    """The tree MINUS the S4 ledger leaf — everything a per-turn injection must not touch."""
    return {k: v for k, v in tree_snapshot(root).items() if _LEDGER_REL not in k}


_MANY_RULES = [(f"rule{n}", f"always run the deploy tests before releasing, rule {n}")
               for n in range(6)]
_MANY_ITEMS = [(f"ctx{n}", f"the deploy release pipeline runs on friday — detail {n} " * 6)
               for n in range(6)]


# ================================================================================== P1 · BUDGET
@unittest.skipUnless(sqlite_disk_ok(), "sandbox cannot back an on-disk SQLite DB")
class TestP1BudgetIsHardOnTheEmittedOutput(unittest.TestCase):
    def test_the_emitted_context_is_within_the_declared_budget(self):
        """On the BYTES THAT LEAVE THE HOOK — not on the builder's own claim about them."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d, rules=_MANY_RULES, items=_MANY_ITEMS)
            code, out, err = _run_hook(QUERY, d)
        self.assertEqual(0, code)
        self.assertEqual("", err)
        context = _emitted(out)
        self.assertIsNotNone(context, "the fixture must actually inject, or this pins nothing")
        self.assertLessEqual(estimate_tokens(context), INJECTION_TOKEN_BUDGET,
                             f"the emitted injection is over the hard {INJECTION_TOKEN_BUDGET}-"
                             f"token budget ({estimate_tokens(context)})")

    def test_the_budget_is_declared_beside_the_bootstrap_budget(self):
        """One module owns both numbers — a shared-budget claim split across two files is a
        claim nobody can check (doc 84: no second competing channel)."""
        self.assertEqual(300, INJECTION_TOKEN_BUDGET)
        self.assertEqual(2000, bootstrap.BOOTSTRAP_TOKEN_BUDGET)

    def test_a_corpus_far_over_budget_still_fits(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d,
                            rules=[(f"rule{n}", "always run the deploy tests " * 12)
                                   for n in range(10)],
                            items=[(f"ctx{n}", "the deploy release pipeline detail " * 30)
                                   for n in range(10)])
            pack = build_injection(surface, QUERY)
        self.assertTrue(pack.within_budget)
        self.assertLessEqual(estimate_tokens(pack.text), INJECTION_TOKEN_BUDGET)

    def test_the_drop_is_declared_and_counts_every_omitted_line(self):
        """An honest `(+N more)`: silently dropping context reads exactly like having none."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, rules=_MANY_RULES, items=_MANY_ITEMS)
            pack = build_injection(surface, QUERY, budget=60)
        self.assertGreater(pack.dropped, 0)
        self.assertIn(f"(+{pack.dropped} more", pack.text)
        self.assertLessEqual(estimate_tokens(pack.text), 60)

    def test_the_notices_own_cost_is_inside_the_fit_test(self):
        """The classic off-by-one-line: budget enforced, THEN a notice appended.

        Asserted on `_fit_to_budget` DIRECTLY, before the `cap_summary` backstop — which is the
        whole point. Downstream of the backstop this bug is invisible: the assembly comes back
        over budget, cap_summary silently truncates it, and the pack looks compliant while the
        notice has been chopped in half. A test that can only see the composite would be green
        for a defect in the component."""
        header = "h:"
        reserved = ["- [rule] r: " + "x" * 200]
        ranked = ["- [context] c%d: %s" % (n, "y" * 200) for n in range(5)]
        for budget in (1, 3, 5, 12, 25, 40, 80, 150, 300, 600):
            text, _kr, _kk, dropped = bootstrap._fit_to_budget(header, reserved, ranked, budget)
            self.assertLessEqual(estimate_tokens(text), budget,
                                 f"budget {budget} exceeded BEFORE the backstop: {text!r}")
            if dropped and text:
                self.assertIn(f"(+{dropped} more", text,
                              "the drop notice was truncated — it must be COSTED, not appended")

    def test_the_backstop_catches_an_over_budget_assembly(self):
        """`cap_summary` is the closing ARITHMETIC guarantee, and this is what makes it more than
        decoration: if the whole-line assembly is ever wrong — a future edit, a shape nobody
        anticipated — the published number still holds. Simulated by making the assembly return
        something far over budget, because in the CURRENT code every `_fit_to_budget` path is
        already within budget, so the backstop is otherwise a no-op that nothing would notice
        disappearing."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, rules=_MANY_RULES, items=_MANY_ITEMS)
            oversized = ("- [rule] runaway: " + "z" * 8000 + "\n", 1, 0, 0)
            with mock.patch.object(bootstrap, "_fit_to_budget", return_value=oversized):
                pack = build_injection(surface, QUERY)
        self.assertLessEqual(estimate_tokens(pack.text), INJECTION_TOKEN_BUDGET,
                             "the arithmetic backstop did not hold the published budget")
        self.assertTrue(pack.within_budget)

    def test_whole_lines_are_dropped_never_half_a_guardrail(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, rules=_MANY_RULES, items=_MANY_ITEMS)
            pack = build_injection(surface, QUERY, budget=120)
        for line in pack.text.splitlines():
            self.assertTrue(line.startswith("- ") or line.endswith(":"), line)

    def test_the_always_on_slice_is_reserved_against_the_ranked_remainder(self):
        """Rules are what the turn must not VIOLATE; a context item merely might help. So the
        ranked tail is dropped first and the reserved slice is only touched once it is empty."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, rules=_MANY_RULES, items=_MANY_ITEMS)
            pack = build_injection(surface, QUERY, budget=110)
        self.assertGreater(pack.rules_shown, 0, "the reserved rule slice was squeezed out first")
        self.assertLessEqual(pack.rules_shown, INJECTION_RULES_MAX_LINES)
        self.assertEqual(0, pack.items_shown)

    def test_the_reserved_slice_is_ranked_by_the_turns_query(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, rules=[("unrelated-a", "never edit the vendored assets"),
                                      ("unrelated-b", "prefer tabs in the makefile"),
                                      ("unrelated-c", "keep the changelog in reverse order"),
                                      ("unrelated-d", "avoid nested ternaries"),
                                      ("deploy-rule", "always run the deploy pipeline tests")])
            pack = build_injection(surface, QUERY)
        self.assertIn("deploy-rule", pack.text,
                      "the reserved slots did not go to the rules this turn is about")


# =============================================================================== P2 · READ-ONLY
@unittest.skipUnless(sqlite_disk_ok(), "sandbox cannot back an on-disk SQLite DB")
class TestP2AFullInjectionTurnWritesNothing(unittest.TestCase):
    """THE pin, and it is a byte snapshot on purpose.

    A name-based guard ("the hook's source does not mention `record_usage`") is green for every
    regression that lives one module down — and the two likeliest ones do: `jit_recall` reaching
    for `recall_relevant` (which STAMPS its hits), or the injectable read falling back to
    `all_active` (which bumps and persists `memory_stats`). Hashing the tree cannot be fooled by
    where the write came from."""

    def test_a_full_injection_turn_leaves_the_governed_tree_byte_identical(self):
        """Everything OUTSIDE the S4 ledger's own leaf must be byte-identical.

        The carve-out is exactly one directory and it is named from the module that owns it, so
        it cannot silently come to mean something wider. It is the `knowledge/freshness.py`
        dirty-set reading: P2 gates DURABLE writes, and transient run-state under `temp_local/` is
        run-tracking. Crucially it does NOT weaken the pin — the writes this test exists to catch
        all land elsewhere: `memory_stats` goes to the StateStore, `record_usage` to the memory
        DB, both outside `injection_ledger/`. `test_the_carve_out_is_exactly_one_leaf` proves the
        exclusion is narrow rather than a place for a real write to hide."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d, rules=_MANY_RULES, items=_MANY_ITEMS)
            code, out, _err = _run_hook(QUERY, d)          # warm
            self.assertEqual(0, code)
            self.assertIsNotNone(_emitted(out), "the fixture must inject, or this pins nothing")
            before = _governed_snapshot(d)
            for _ in range(5):
                self.assertEqual(0, _run_hook(QUERY, d)[0])
            self.assertEqual(before, _governed_snapshot(d),
                             "the per-turn injection performed a DURABLE WRITE — it runs on "
                             "EVERY prompt, so this is a write per turn, forever")

    def test_the_carve_out_is_exactly_one_leaf(self):
        """The exclusion above is only honest if it is narrow. Across a run of turns, EVERY path
        that changes must be inside `injection_ledger/` — nothing else may move."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d, rules=_MANY_RULES, items=_MANY_ITEMS)
            _run_hook(QUERY, d)                            # warm
            before = tree_snapshot(d)
            for n in range(5):
                _run_hook(f"{QUERY} variation {n}", d)
            after = tree_snapshot(d)
            changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
        self.assertTrue(changed, "nothing changed at all — the ledger is not being written")
        for path in changed:
            self.assertIn(_LEDGER_REL, path,
                          f"{path} changed outside the S4 ledger leaf — the read-only carve-out "
                          f"is being used to hide a real write")

    def test_the_read_counter_does_not_move_across_a_session_of_turns(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, rules=_MANY_RULES, items=_MANY_ITEMS)
            before = MemoryStore.from_surface(surface).stats.reads
            for n in range(20):
                _run_hook(f"{QUERY} ({n})", d)
            after = MemoryStore.from_surface(surface).stats.reads
        self.assertEqual(before, after,
                         "`stats.reads` — the read/write ratio `/mokata:govern` surfaces — "
                         "became a count of TURNS")

    def test_the_injection_path_does_not_route_through_recall_relevant(self):
        """A supporting check, NOT the pin: `recall_relevant` stamps what it returns
        (`record_usage`), which is why v1 is lexical-floor-only. The route to the wider tiered
        retrieval is a `recall_relevant(stamp=False)` seam, filed rather than improvised."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, rules=_MANY_RULES, items=_MANY_ITEMS)
            with mock.patch.object(MemoryStore, "recall_relevant") as recall, \
                    mock.patch.object(MemoryStore, "record_usage") as usage:
                build_injection(surface, QUERY)
            recall.assert_not_called()
            usage.assert_not_called()


# ================================================================================= P3 · DEGRADE
class TestP3DegradeEmitsNoChannelAtAll(unittest.TestCase):
    def test_an_uninitialized_repo_emits_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            code, out, err = _run_hook(QUERY, d)
        self.assertEqual(0, code)
        self.assertEqual("", out, "an empty additionalContext is STILL a channel")
        self.assertEqual("", err)

    @unittest.skipUnless(sqlite_disk_ok(), "sandbox cannot back an on-disk SQLite DB")
    def test_an_initialized_repo_with_an_empty_memory_emits_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            code, out, err = _run_hook(QUERY, d)
        self.assertEqual((0, "", ""), (code, out, err))

    @unittest.skipUnless(sqlite_disk_ok(), "sandbox cannot back an on-disk SQLite DB")
    def test_a_shared_function_word_is_not_enough_to_inject(self):
        """FIXED — doc 84 JIT-LEXICAL-FUNCTION-WORD-FLOOR. This test previously asserted the
        OPPOSITE and is inverted deliberately: it recorded the v1 floor's known limit (a query and
        an item sharing only the word "the" cleared `jit_recall`'s "any non-zero signal" bar and
        got injected), so that changing the behaviour would have to be a deliberate act. This is
        that act.

        The fix is not a minimum-relevance constant — the row rejected one, because Jaccard's
        length bias makes any threshold fitted to fixtures this small wrong at a different corpus
        size. It is the categorical rule "function words aren't match evidence": admission asks
        whether the query and the item share a CONTENT term. H-4's BM25 subsumes that via IDF
        rather than deleting it.

        Kept at the HOOK level on purpose. The rule's unit pins live in
        `test_jit_function_word_floor.py`; this one proves it reaches the end of the real
        UserPromptSubmit path — a turn with nothing genuinely relevant now opens no channel."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d, items=[("unrelated", "the invoice template lives in billing/")])
            _code, out, _err = _run_hook("what colour is the bikeshed?", d)
        self.assertIsNone(_emitted(out),
                          "an item sharing only the function word 'the' with the prompt was "
                          "injected — the JIT-LEXICAL-FUNCTION-WORD-FLOOR fix has regressed")

    @unittest.skipUnless(sqlite_disk_ok(), "sandbox cannot back an on-disk SQLite DB")
    def test_a_real_shared_term_still_reaches_the_channel(self):
        """The positive control for the test above. Without it, 'emit nothing, ever' passes the
        function-word pin — the two must be read as a pair."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d, items=[("unrelated", "the invoice template lives in billing/")])
            _code, out, _err = _run_hook("where does the invoice template live?", d)
        emitted = _emitted(out)
        self.assertIsNotNone(emitted, "a genuine term match opened no channel")
        self.assertIn("invoice", emitted)

    def test_an_empty_pack_is_an_empty_string_not_an_empty_channel(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
            pack = build_injection(Surface.load(d), "")
        self.assertEqual("", pack.text)
        self.assertEqual(0, pack.token_estimate)

    def test_the_memory_stack_is_not_imported_for_an_uninitialized_repo(self):
        """In a SUBPROCESS, because `mokata.memory` is already in this process's `sys.modules`
        from the imports at the top of this file — an in-process check would pass vacuously.

        The cost this guards is real: the memory stack is the expensive import on the path, and
        the overwhelmingly common case (a prompt in a repo mokata is not set up in) must not pay
        it once per turn."""
        probe = (
            "import json, sys, io\n"
            "sys.path.insert(0, %r)\n"
            "sys.stdin = io.StringIO(json.dumps({'prompt': 'hi', 'cwd': sys.argv[1]}))\n"
            "from mokata.hook_cli import main\n"
            "code = main(['user-prompt-submit'])\n"
            "leaked = sorted(m for m in sys.modules if m.startswith('mokata.memory'))\n"
            "print(json.dumps({'code': code, 'leaked': leaked}))\n"
        ) % os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
        with tempfile.TemporaryDirectory() as d:
            proc = subprocess.run([sys.executable, "-c", probe, d],
                                  capture_output=True, text=True, timeout=60)
        self.assertEqual(0, proc.returncode, proc.stderr)
        result = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(0, result["code"])
        self.assertEqual([], result["leaked"],
                         "the memory stack was imported for a repo mokata isn't set up in — "
                         "that import is paid on EVERY prompt in EVERY non-mokata repo")


# =================================================================================== P4 · SCOPE
@unittest.skipUnless(sqlite_disk_ok(), "sandbox cannot back an on-disk SQLite DB")
class TestP4ScopeSurvivesTheTripThroughThePack(unittest.TestCase):
    """C1 fixed the READ; this asks whether the guarantee still holds at the surface the human
    actually sees. A filter that is correct one call down and lost on the way out is not a
    filter (a live-Postgres leg covers the same claim on a real engine)."""

    def _team_repo(self, d, actor):
        from mokata import MANIFEST_FILENAME, MOKATA_DIR
        init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
        path = os.path.join(d, MOKATA_DIR, MANIFEST_FILENAME)
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        settings = data.setdefault("settings", {})
        settings["mode"] = "team"
        # The project id is pinned so the fixture's project-scoped item is ON the working scope
        # path — `_scope_context_for` builds the context from THIS key, and an item filed under
        # some other project id is legitimately out of scope (which would make the test pass for
        # the wrong reason).
        settings.setdefault("project", {})["id"] = "h1a-pack"
        settings.setdefault("access", {})["grants"] = {"project": {"viewer": ["bob", "alice"]}}
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        saved = os.environ.get("MOKATA_ACTOR")
        os.environ["MOKATA_ACTOR"] = actor
        self.addCleanup(lambda: os.environ.__setitem__("MOKATA_ACTOR", saved)
                        if saved is not None else os.environ.pop("MOKATA_ACTOR", None))
        return Surface.load(d)

    def test_a_teammates_private_item_is_not_in_the_emitted_pack(self):
        with tempfile.TemporaryDirectory() as d:
            surface = self._team_repo(d, "bob")
            store = MemoryStore.from_surface(surface)
            store.backend.put(MemoryItem.create(
                "shared", "the deploy release pipeline runs on friday", kind="context",
                id="visible", scope_level="project", scope_id="h1a-pack"))
            store.backend.put(MemoryItem.create(
                "alice-secret", "the deploy release pipeline uses alice's private key",
                kind="context", id="private", scope_level="personal", scope_id="alice"))
            pack = build_injection(surface, QUERY, budget=1000)
        self.assertIn("visible", pack.item_ids,
                      "the fixture must actually inject, or this pins nothing")
        self.assertNotIn("private", pack.item_ids)
        self.assertNotIn("alice", pack.text)


# =============================================================================== P5 · FAIL-OPEN
class TestP5FailOpenAtEveryArm(unittest.TestCase):
    """Exit 0, empty stdout, prompt unchanged — at every arm. On this event a non-zero exit does
    not block a tool call, it eats the human's TURN, so there is no failure worth that price."""

    def _assert_silent_success(self, code, out, err):
        self.assertEqual(0, code, "the hook returned non-zero — it would eat the human's turn")
        self.assertEqual("", out)
        self.assertEqual("", err)

    def test_arm_1_stdin_parse(self):
        out, err = io.StringIO(), io.StringIO()
        saved, sys.stdin = sys.stdin, io.StringIO("{ this is not json")
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = hook_cli.main([SUB])
        finally:
            sys.stdin = saved
        self._assert_silent_success(code, out.getvalue(), err.getvalue())

    @unittest.skipUnless(sqlite_disk_ok(), "sandbox cannot back an on-disk SQLite DB")
    def test_arm_2_surface_load(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, items=_MANY_ITEMS)
            with mock.patch.object(Surface, "load", side_effect=RuntimeError("bad manifest")):
                code, out, err = _run_hook(QUERY, d)
        self._assert_silent_success(code, out, err)

    @unittest.skipUnless(sqlite_disk_ok(), "sandbox cannot back an on-disk SQLite DB")
    def test_arm_3_memory_store_construction(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, items=_MANY_ITEMS)
            with mock.patch.object(MemoryStore, "from_surface",
                                   side_effect=RuntimeError("no backend")):
                code, out, err = _run_hook(QUERY, d)
        self._assert_silent_success(code, out, err)

    @unittest.skipUnless(sqlite_disk_ok(), "sandbox cannot back an on-disk SQLite DB")
    def test_arm_4_recall(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, items=_MANY_ITEMS)
            with mock.patch("mokata.memory.jit_recall", side_effect=RuntimeError("boom")):
                code, out, err = _run_hook(QUERY, d)
        self._assert_silent_success(code, out, err)

    @unittest.skipUnless(sqlite_disk_ok(), "sandbox cannot back an on-disk SQLite DB")
    def test_arm_5_budget_cap(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, rules=_MANY_RULES, items=_MANY_ITEMS)
            with mock.patch("mokata.govern.compaction.cap_summary",
                            side_effect=RuntimeError("cap exploded")):
                code, out, err = _run_hook(QUERY, d)
        self._assert_silent_success(code, out, err)

    @unittest.skipUnless(sqlite_disk_ok(), "sandbox cannot back an on-disk SQLite DB")
    def test_every_arm_leaves_the_governed_state_untouched(self):
        """Failing open must not mean failing DIRTY: a half-run injection that wrote something
        on its way down would be worse than one that never ran."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d, rules=_MANY_RULES, items=_MANY_ITEMS)
            _run_hook(QUERY, d)                              # warm
            before = _governed_snapshot(d)
            for target, kwargs in (("mokata.memory.jit_recall", {}),
                                   ("mokata.memory.always_on_lines", {}),
                                   ("mokata.govern.compaction.cap_summary", {})):
                with mock.patch(target, side_effect=RuntimeError("boom"), **kwargs):
                    self.assertEqual(0, _run_hook(QUERY, d)[0])
            self.assertEqual(before, _governed_snapshot(d))


if __name__ == "__main__":                              # pragma: no cover
    unittest.main()
