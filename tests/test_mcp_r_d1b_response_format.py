"""MCP-R.D1b — `response_format` {concise (default), detailed}: stop the double payload.

Before D1b several read tools returned the full structured dict AND a pre-rendered human view
(`block`/`report`) in the SAME result — double token cost (doc 88 §D1, second bullet). D1b adds a
`response_format` param (concise default) routed through ONE shared helper
(`mcp.response_format.apply_response_format`): concise drops the render (the token win), detailed
adds it back BYTE-IDENTICAL to the pre-D1b output (a lossless opt-in). The human view is never lost
(P16); it moves behind `response_format:detailed` (P22).

These guards:
  DOUBLE sites (progress/lanes/decompose)  : concise has NO `block` + every structured key; detailed
                                             == the pre-D1b to_dict()+render, byte-identical.
  RENDER-ONLY sites (doctor/coverage/budget/lat_check/baseline): concise answers with the structured
                                             summary key (never empty); detailed adds the `report`.
  DEFAULT                                  : no response_format arg → concise.
  SHARED MECHANISM                         : the concise/detailed branch lives in exactly ONE place;
                                             a new tool gets the behaviour for free; concise is lazy.
  SCHEMA                                   : response_format is the ONLY inputSchema change, present
                                             on exactly the touched tools and no others.
  TOKEN WIN                                : concise serialized < detailed for a double site.

Secret-safety: N/A — response_format is a pure OUTPUT-format toggle. It adds no arg that could carry
a secret and only ever DROPS content (never adds), so it opens no leak surface; the D0 served-path
secret guard (test_mcp_r_d0_no_secret_leak) is unaffected.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import inspect
import json
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import mcp_server as M
from mokata.config import Surface
from mokata.engine.spec import AcceptanceCriterion, Spec
from mokata.mcp import response_format as RF
from mokata.mcp import server as MS
from mokata.mcp import tools_read as TR
from mokata.mcp.registry import TOOLS

# The tools D1b touches — the 3 DOUBLE (block) sites + the 5 RENDER-ONLY (report) sites. This set is
# the schema-diff contract: exactly these gain `response_format`, and no other tool does.
TOUCHED = {"progress", "lanes", "decompose", "doctor", "coverage", "budget", "lat_check", "baseline"}


def _repo(d, profile="standard"):
    from mokata.init import init_repo
    init_repo(root=d, profile=profile, assume_yes=True, out=lambda _: None)
    return Surface.load(d)


def _emit_spec(surface, *pairs):
    spec = Spec(title="T", criteria=[AcceptanceCriterion(id=i, text=t) for i, t in pairs])
    surface.state.write("emitted_spec", spec.to_dict())


# ======================================================================================
# DOUBLE sites — progress / lanes / decompose: concise drops `block`, detailed keeps it
# ======================================================================================

class TestDoubleSites(unittest.TestCase):

    def test_mcp_r_d1b_progress_concise_drops_block(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            concise = M.progress(path=d)
            self.assertNotIn("block", concise)               # the render is gone under concise
            self.assertIn("active", concise)                 # …but concise still ANSWERS (structured)
            detailed = M.progress(path=d, response_format="detailed")
            self.assertIn("block", detailed)
            # concise is EXACTLY detailed minus the render — same structured data, no key dropped
            self.assertEqual(concise, {k: v for k, v in detailed.items() if k != "block"})

    def test_mcp_r_d1b_progress_detailed_keeps_block(self):
        # detailed == the pre-D1b return (to_dict() + render), BYTE-IDENTICAL (order preserved)
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            from mokata.progress import build_progress, render_progress
            p = build_progress(surface.state)
            expected = p.to_dict()
            expected["block"] = render_progress(p, surface=surface)
            detailed = M.progress(path=d, response_format="detailed")
            self.assertEqual(detailed, expected)
            self.assertEqual(json.dumps(detailed), json.dumps(expected))   # key order too

    def test_mcp_r_d1b_lanes_concise_drops_block(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            concise = M.lanes(path=d)
            self.assertNotIn("block", concise)
            self.assertIn("active", concise)
            detailed = M.lanes(path=d, response_format="detailed")
            self.assertIn("block", detailed)
            self.assertEqual(concise, {k: v for k, v in detailed.items() if k != "block"})

    def test_mcp_r_d1b_lanes_detailed_keeps_block(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            from mokata.govern import AuditLedger
            from mokata.progress import build_run_lanes, render_lanes
            ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
            rl = build_run_lanes(surface.state, ledger=ledger)
            expected = rl.to_dict()
            expected["block"] = render_lanes(rl)
            detailed = M.lanes(path=d, response_format="detailed")
            self.assertEqual(detailed, expected)
            self.assertEqual(json.dumps(detailed), json.dumps(expected))

    def test_mcp_r_d1b_decompose_concise_drops_block(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _emit_spec(surface, ("AC1", "add `parse_config`"), ("AC2", "add `render_view`"))
            concise = M.decompose(path=d)
            self.assertNotIn("block", concise)
            self.assertTrue(concise["available"])            # concise still ANSWERS (the split)
            self.assertEqual(len(concise["subtasks"]), 2)
            detailed = M.decompose(path=d, response_format="detailed")
            self.assertIn("block", detailed)
            self.assertEqual(concise, {k: v for k, v in detailed.items() if k != "block"})

    def test_mcp_r_d1b_decompose_detailed_keeps_block(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _emit_spec(surface, ("AC1", "add `parse_config`"), ("AC2", "add `render_view`"))
            from mokata.engine import load_emitted_spec
            from mokata.execmode.decompose import decompose as _decompose
            from mokata.knowledge import KnowledgeLayer
            plan = _decompose(load_emitted_spec(surface.state),
                              layer=KnowledgeLayer.from_surface(surface))
            expected = plan.to_dict()
            expected["available"] = True
            expected["block"] = plan.render()
            detailed = M.decompose(path=d, response_format="detailed")
            self.assertEqual(detailed, expected)
            self.assertEqual(json.dumps(detailed), json.dumps(expected))


# ======================================================================================
# RENDER-ONLY sites — concise answers with the structured summary; detailed adds `report`
# ======================================================================================

class TestRenderOnlySites(unittest.TestCase):

    # (fn, render_key, the structured summary key that concise MUST still carry)
    CASES = {
        "doctor":    ("report", "ok"),
        "coverage":  ("report", "overlaps"),
        "budget":    ("report", "events"),
        "lat_check": ("report", "has_drift"),
        "baseline":  ("report", "ok"),
    }

    def test_mcp_r_d1b_renderonly_concise_answers(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            for name, (render_key, summary_key) in self.CASES.items():
                fn = getattr(M, name)
                with self.subTest(tool=name):
                    concise = fn(path=d)
                    self.assertNotIn(render_key, concise)        # render dropped
                    self.assertIn(summary_key, concise)          # concise still ANSWERS
                    self.assertTrue(concise)                     # …and is NEVER empty
                    detailed = fn(path=d, response_format="detailed")
                    self.assertIn(render_key, detailed)
                    self.assertIsInstance(detailed[render_key], str)   # detailed adds the render str
                    # detailed == concise + render (structured data byte-identical)
                    self.assertEqual(concise,
                                     {k: v for k, v in detailed.items() if k != render_key})


# ======================================================================================
# DEFAULT — a call with no response_format arg gets the concise (trimmed) result
# ======================================================================================

class TestDefaultIsConcise(unittest.TestCase):

    def test_mcp_r_d1b_default_is_concise(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _emit_spec(surface, ("AC1", "add `parse_config`"))
            # no response_format arg on any touched tool → the render key is absent
            self.assertNotIn("block", M.progress(path=d))
            self.assertNotIn("block", M.lanes(path=d))
            self.assertNotIn("block", M.decompose(path=d))
            self.assertNotIn("report", M.doctor(path=d))
            self.assertNotIn("report", M.coverage(path=d))
            self.assertNotIn("report", M.budget(path=d))
            self.assertNotIn("report", M.lat_check(path=d))
            self.assertNotIn("report", M.baseline(path=d))


# ======================================================================================
# SHARED MECHANISM — the concise/detailed branch lives in EXACTLY ONE place, and is lazy
# ======================================================================================

class TestSharedMechanism(unittest.TestCase):

    def test_mcp_r_d1b_single_branch_site(self):
        # No tool hand-rolls the concise/detailed decision — every one routes through the helper.
        src = inspect.getsource(TR)
        for needle in ('== "detailed"', "== DETAILED", "is_detailed", '== "concise"',
                       "response_format ==", "== response_format"):
            self.assertNotIn(needle, src, f"tools_read hand-rolls the branch ({needle!r})")
        self.assertIn("apply_response_format", src)          # …it defers to the helper
        # and the ONE branch site is the helper itself
        self.assertIn("== DETAILED", inspect.getsource(RF.is_detailed))

    def test_mcp_r_d1b_new_tool_gets_format_for_free(self):
        # A NEW tool that wraps its render in LazyRender + returns apply_response_format() gets
        # concise/detailed with NO per-tool branch — proof the mechanism is shared, not copy-paste.
        def newtool(response_format="concise"):
            return RF.apply_response_format(
                response_format, {"answer": 42, "view": RF.LazyRender(lambda: "RENDERED")})

        self.assertEqual(newtool(), {"answer": 42})                       # concise: render dropped
        self.assertEqual(newtool("detailed"), {"answer": 42, "view": "RENDERED"})   # detailed: kept

    def test_mcp_r_d1b_concise_is_lazy(self):
        # concise must NEVER build the render it is about to drop (the callable stays uncalled).
        calls = []

        def spy():
            calls.append(1)
            return "R"

        RF.apply_response_format("concise", {"a": 1, "v": RF.LazyRender(spy)})
        self.assertEqual(calls, [])                           # not built under concise
        RF.apply_response_format("detailed", {"a": 1, "v": RF.LazyRender(spy)})
        self.assertEqual(calls, [1])                          # built exactly once under detailed


# ======================================================================================
# SCHEMA — response_format is the ONLY inputSchema change, confined to the touched tools
# ======================================================================================

class TestSchema(unittest.TestCase):

    @unittest.skipUnless(MS.mcp_available(), "optional MCP SDK not installed")
    def test_mcp_r_d1b_schema_adds_only_response_format(self):
        import asyncio

        built = {t.name: t.inputSchema for t in asyncio.run(MS.build_server().list_tools())}

        # The set of tools whose schema carries response_format is EXACTLY the touched set — no tool
        # gained it unexpectedly, and none of the eight missed it (the confinement contract).
        have_rf = {name for name, schema in built.items()
                   if "response_format" in (schema.get("properties") or {})}
        self.assertEqual(have_rf, TOUCHED)

        fn_by_name = {s.name: s.fn for s in TOOLS}
        for name in TOUCHED:
            with self.subTest(tool=name):
                props = built[name].get("properties") or {}
                # the added param is a plain string defaulting to concise (typed-enum validation is
                # D1d, not this stage) …
                self.assertEqual(props["response_format"].get("default"), "concise")
                # … and it is the ONLY schema property beyond the tool's pre-existing signature
                # params — i.e. the diff is confined to response_format, nothing else moved.
                sig = set(inspect.signature(fn_by_name[name]).parameters)
                self.assertEqual(set(props), sig)            # schema == signature (no stray prop)
                self.assertEqual(set(props) - {"response_format"}, sig - {"response_format"})


# ======================================================================================
# TOKEN WIN — concise serialized is strictly smaller than detailed for a double site
# ======================================================================================

class TestTokenWin(unittest.TestCase):

    def test_mcp_r_d1b_concise_smaller_than_detailed(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            concise = json.dumps(M.progress(path=d))
            detailed = json.dumps(M.progress(path=d, response_format="detailed"))
            self.assertLess(len(concise), len(detailed))


if __name__ == "__main__":
    unittest.main()
