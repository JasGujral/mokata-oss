"""0.0.22 stage D3 — `one-context-budget`: H-8 + HP.S6.

Doc 110 criterion 5, clause 2: *one context budget, with no second channel.* Every byte mokata adds
to the model's context automatically is sized from `settings.context.*` and counted inside the
budget that governs its channel — including the D1 re-anchor block and the MCP reachability
warning, which were appended AFTER the briefing's budget check until this stage.

HP.S6: output density defaults ON. Its `ReadCache` half is NOT built, on evidence (see the report).
"""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import bootstrap as B
from mokata import context_budget as CB
from mokata import hook_cli
from mokata import run_intent as RI
from mokata.config import Surface
from mokata.govern.resume import PipelineCheckpoint
from mokata.init import init_repo


def _silent(_):
    pass


def _repo(d, context=None, *runs):
    init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
    if context is not None:
        p = os.path.join(d, ".mokata", "manifest.json")
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
        data.setdefault("settings", {})["context"] = context
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    surface = Surface.load(d)
    for rid in runs:
        PipelineCheckpoint(surface.state, rid).ensure_registered()
    return Surface.load(d)


class H8TheBudgetIsReadFromSettings(unittest.TestCase):

    def test_h8_the_defaults_are_the_shipped_constants(self):
        cb = CB.ContextBudget()
        self.assertEqual((B.BOOTSTRAP_TOKEN_BUDGET, B.INJECTION_TOKEN_BUDGET,
                          B.BRIEFING_RULES_MAX_LINES, B.INJECTION_RULES_MAX_LINES,
                          B.INJECTION_JIT_TOP_K),
                         (cb.briefing_tokens, cb.turn_tokens, cb.briefing_rules_lines,
                          cb.turn_rules_lines, cb.turn_top_k))

    def test_a_configured_briefing_budget_is_the_one_the_briefing_obeys(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, {"briefing_tokens": 400})
            result = B.build_bootstrap(surface)
            self.assertEqual(400, result.budget)
            self.assertLessEqual(result.token_estimate, 400)

    def test_a_configured_turn_budget_is_the_one_the_pack_obeys(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, {"turn_tokens": 0})
            self.assertEqual(0, B.build_injection(surface, "anything").budget)

    def test_a_bad_value_is_refused_with_its_reason_and_the_briefing_says_so(self):
        for bad, why in (({"briefing_tokens": "lots"}, "not a whole number"),
                         ({"turn_tokens": 99999}, "outside"),
                         ({"live_lines": True}, "not a whole number"),
                         ({"briefing_tokenz": 10}, "unknown key")):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as d:
                surface = _repo(d, bad)
                cb = CB.context_budget(surface.manifest)
                self.assertTrue(any(why in p for p in cb.problems), cb.problems)
                self.assertEqual(CB.LIMITS["briefing_tokens"][0], cb.briefing_tokens)
                self.assertIn("settings.context ignored", B.build_bootstrap(surface).text)

    def test_live_lines_zero_turns_the_live_block_off(self):
        from mokata.live_context import HEADER
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, {"live_lines": 0})
            self.assertNotIn(HEADER, B.build_bootstrap(surface).text)


class NoSecondChannelEverythingIsInsideTheBudget(unittest.TestCase):

    def setUp(self):
        os.environ.pop("MOKATA_SESSION_ID", None)

    def test_the_re_anchor_block_is_counted_inside_the_briefing_budget(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, None, "rA")
            RI.bind_registered(d, "S1", "rA")
            buf = io.StringIO()
            with mock.patch.object(hook_cli, "_payload_from_stdin",
                                   return_value={"cwd": d, "session_id": "S1",
                                                 "source": "compact"}), redirect_stdout(buf):
                hook_cli.session_start_main([])
            emitted = buf.getvalue()
            self.assertIn("re-anchored after compaction", emitted)
            payload = json.loads(emitted)
            ctx = payload["hookSpecificOutput"]["additionalContext"]
            self.assertLessEqual(B.estimate_tokens(ctx), CB.ContextBudget().briefing_tokens,
                                 "the emitted briefing (re-anchor included) is over budget")

    def test_under_pressure_the_re_anchor_outlives_the_live_block(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            block = "mokata · re-anchored after compaction — TEST BLOCK"
            full = B.build_bootstrap(surface, reanchor=block).text
            tight = B.build_bootstrap(surface, reanchor=block,
                                      budget=(len(full) - 40) // 4).text
            self.assertIn(block, tight)

    def test_the_footer_is_counted_too(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            result = B.build_bootstrap(surface, footer="WARN: mcp unreachable", budget=300)
            self.assertLessEqual(result.token_estimate, 300)


class HpS6DensityDefaultsOn(unittest.TestCase):

    def test_hp_s6_output_density_is_on_unless_turned_off(self):
        from mokata.govern.compress import density_enabled
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(density_enabled(_repo(d).manifest))




class TheReAnchorSurvivesASmallBudget(unittest.TestCase):
    """0.0.22 verification D-3: at briefing_tokens 200..700 the re-anchor was cut away whole."""

    def test_a_small_accepted_budget_keeps_the_whole_re_anchor(self):
        import tempfile as _tf
        from mokata.bootstrap import build_bootstrap
        from mokata.config import Surface as _S
        from mokata.init import init_repo as _init
        block = "mokata · re-anchored after compaction — THIS session's run\n- next write: BLOCKED"
        with _tf.TemporaryDirectory() as d:
            _init(root=d, profile="standard", assume_yes=True, out=lambda _: None)
            for budget in (200, 400, 700):
                with self.subTest(budget=budget):
                    r = build_bootstrap(_S.load(d), budget=budget, reanchor=block)
                    self.assertIn(block, r.text)
                    self.assertLessEqual(r.token_estimate, budget)

    def test_a_re_anchor_larger_than_the_budget_keeps_its_head_not_the_prefix(self):
        import tempfile as _tf
        from mokata.bootstrap import build_bootstrap
        from mokata.config import Surface as _S
        from mokata.init import init_repo as _init
        head = "mokata · re-anchored after compaction — THIS session's run\n- run: rA\n"
        block = head + "- next implementation write: BLOCKED [x]\n" + "- filler line\n" * 80
        with _tf.TemporaryDirectory() as d:
            _init(root=d, profile="standard", assume_yes=True, out=lambda _: None)
            r = build_bootstrap(_S.load(d), budget=200, reanchor=block)
            self.assertTrue(r.text.startswith(head + "- next implementation write"), r.text[:200])
            self.assertLessEqual(r.token_estimate, 200)


if __name__ == "__main__":
    unittest.main()
