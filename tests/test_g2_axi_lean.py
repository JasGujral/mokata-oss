"""AXI-LEAN (0.0.23 D11) — the full-vs-lean MCP surface as a G2 condition, the verdict computed
from the archive, and the `settings.surface.lean` stub: OFF and NOT adopted.
"""

import json
import os
import shutil
import tempfile
import unittest

import _support  # noqa: F401  (src/ on the path + provisioning refusal)

import _g2_fixture as FX

from bench.g2 import conditions as C
from bench.g2.archive import RunArchive
from bench.g2.lean import (KEPT_KINDS, LEAN_SETTING, MIN_CONTEXT_SAVING, lean_disallowed_tools,
                           lean_verdict, mokata_tool_kinds, render_markdown, would_register)
from bench.g2.study import render_study

SRC = os.path.join(FX.ROOT, "src", "mokata")


def _lean_script(workdir, prompt, condition, repeat):
    ev = FX.default_script(workdir, prompt, condition, repeat)
    if condition == "mokata-lean":
        tools = [t for t in ev[0]["tools"] if t not in set(lean_disallowed_tools())]
        ev[0] = FX.init_event(condition, tools=tools)
        for e in ev:                                 # a leaner schema: every turn re-sends less
            u = (e.get("message") or {}).get("usage") if e.get("type") == "assistant" else None
            if u:
                u["input_tokens"] = int(u["input_tokens"] * 0.7)
    return ev


class TestTheStub(unittest.TestCase):
    def test_setting_is_off_and_not_adopted(self):
        self.assertEqual(LEAN_SETTING, {"key": "settings.surface.lean", "default": False,
                                        "adopted": False})
        self.assertTrue(would_register("read"))       # default OFF: the full surface

    def test_the_product_does_not_read_the_setting(self):
        hits = []
        for dirpath, _dirs, files in os.walk(SRC):
            for fn in files:
                if fn.endswith((".py", ".md", ".json")):
                    with open(os.path.join(dirpath, fn), encoding="utf-8") as fh:
                        text = fh.read()
                    if "surface.lean" in text or "surface_lean" in text:
                        hits.append(fn)
        self.assertEqual(hits, [], "settings.surface.lean is NOT adopted — the product must not read it")


class TestTheLeanSurface(unittest.TestCase):
    def test_lean_never_moves_a_write_or_approve_off_mcp(self):
        kinds = dict(mokata_tool_kinds())
        disallowed = {t[len("mcp__mokata__"):] for t in lean_disallowed_tools()}
        self.assertTrue(disallowed)
        self.assertEqual({kinds[n] for n in disallowed}, {"read"})
        kept = {n for n, k in kinds.items() if k in KEPT_KINDS}
        self.assertTrue(kept)
        self.assertFalse(kept & disallowed)
        self.assertIn("approve", kept)

    def test_lean_list_tracks_the_registry(self):
        reads = [n for n, k in mokata_tool_kinds() if k == "read"]
        self.assertEqual(len(lean_disallowed_tools()), len(reads))

    def test_lean_is_a_g2_condition(self):
        cond = C.resolve(["bare", "mokata", "mokata-lean"])[2]
        self.assertEqual((cond.name, cond.surface, cond.expect_mokata), ("mokata-lean", "lean", True))
        self.assertNotEqual(cond.fingerprint(), C.mokata_on().fingerprint())
        tmp = tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(tmp, "w"))
            args = cond.prepare(os.path.join(tmp, "w"), tmp, runner=lambda a, c: 0)
            self.assertIn("--disallowedTools", args)
            self.assertIn("mcp__mokata__query", args[args.index("--disallowedTools") + 1])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_a_disallowed_tool_still_offered_is_contamination(self):
        from bench.g2.streamparse import parse_stream
        cond = C.resolve(["bare", "mokata-lean"])[1]
        tr = parse_stream([json.dumps(FX.init_event("mokata"))])     # full surface leaked in
        self.assertTrue(any("disallowed tool still offered" in p for p in cond.check(tr)))


class TestTheVerdict(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        conds = C.resolve(["bare", "mokata", "mokata-lean"])
        cls.out = FX.build_fixture_archive(os.path.join(cls.tmp, "run"), repeats=5,
                                           conditions=conds, script=_lean_script,
                                           task_ids=["graph-rename-line-total",
                                                     "gates-tdd-quantity-guard"])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_fixture_saving_is_measured_but_is_not_a_win(self):
        v = lean_verdict(RunArchive(self.out))
        self.assertTrue(v["measured"])
        self.assertGreater(v["context_saving"], MIN_CONTEXT_SAVING)
        self.assertFalse(v["measured_win"])                   # a fixture is never evidence
        self.assertFalse(v["adopt"])
        self.assertIn("not a complete real run", v["reason"])

    def test_a_shape_real_win_still_does_not_adopt(self):
        dst = os.path.join(self.tmp, "shape-real")
        shutil.copytree(self.out, dst)
        mp = os.path.join(dst, "run.json")
        with open(mp, encoding="utf-8") as fh:
            m = json.load(fh)
        m["agent"]["id"], m["judge"]["id"], m["cloner"], m["kind"] = \
            "claude-code", "claude-cli", "git", "real"
        with open(mp, "w", encoding="utf-8") as fh:
            json.dump(m, fh)
        v = lean_verdict(RunArchive(dst))
        self.assertTrue(v["measured_win"])
        self.assertFalse(v["adopt"])
        self.assertIn("owner's GO", v["reason"])

    def test_lean_losing_a_task_is_not_a_win(self):
        def judge(task, transcript):
            ok = "mcp__mokata__query" in transcript or "Grep" in transcript   # lean lost its nav
            return json.dumps({"criteria": {c.id: {"pass": ok} for c in task.rubric}})

        def script(workdir, prompt, condition, repeat):
            ev = _lean_script(workdir, prompt, condition, repeat)
            if condition == "mokata-lean":
                ev = [e for e in ev if not (e.get("type") == "assistant"
                                            and any(b.get("name") == "mcp__mokata__query"
                                                    for b in e["message"]["content"]))]
            return ev
        out = FX.build_fixture_archive(os.path.join(self.tmp, "worse"), repeats=1,
                                       conditions=C.resolve(["bare", "mokata", "mokata-lean"]),
                                       script=script, decide=judge,
                                       task_ids=["graph-rename-line-total"])
        v = lean_verdict(RunArchive(out))
        self.assertEqual(v["tasks_worse"], ["graph-rename-line-total"])
        self.assertFalse(v["measured_win"])

    def test_study_carries_the_lean_section(self):
        text = render_study(RunArchive(self.out))
        self.assertIn("## AXI-LEAN — full vs lean MCP surface", text)
        self.assertIn("adopted **False**", text)

    def test_no_pair_means_not_measured(self):
        out = FX.build_fixture_archive(os.path.join(self.tmp, "nopair"), repeats=1,
                                       task_ids=["gates-tdd-quantity-guard"])
        v = lean_verdict(RunArchive(out))
        self.assertFalse(v["measured"])
        self.assertIn("Not measured", render_markdown(v))


if __name__ == "__main__":
    unittest.main()
