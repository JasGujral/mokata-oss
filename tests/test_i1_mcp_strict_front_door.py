"""0.0.28 lane I1/I2 — the MCP FRONT DOOR (`MCP-FIRST-CALL-FAILS`, design note I §3c, table binding).

Jas, 2026-11-05: "we might have to improve the documentation of mcp - i see lot of msgs failing and
then fixed". Measured on master: an unknown argument name was SILENTLY DROPPED (`remember(text=…)`
minted a proposal for an EMPTY memory, `query(q="f1")` answered about target ""), and pydantic's lax
mode let `passed="yes"` record a passed verdict. Type faults came back as raw `errors.pydantic.dev`
text instead of mokata's voice.

The table (strict on names and presence, lenient on representable conversions, strict on ambiguous
ones):

  unknown argument name                      refused, `did_you_mean` + `accepted`
  missing required argument                  refused, `retry_with`
  array/object given a JSON string of it     ACCEPTED
  integer given "2" / number given "2.5"     ACCEPTED
  boolean given true/false or "true"/"false" ACCEPTED
  boolean given anything else                refused
  enum value not in the set                  refused, `allowed`
  null for a non-nullable / unparseable      refused, `retry_with`

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import shutil
import unittest

import _support  # noqa: F401  (puts src/ on the path)
import _mcp_sdk
import _mcp_stdio as H

from mokata.mcp import validation as V  # noqa: E402

SENTINEL = "SENTINEL-VALUE-7731"


def _props(tool):
    return (tool.get("inputSchema") or {}).get("properties") or {}


def _alts(prop):
    return prop.get("anyOf") or [prop]


def _bool_param(tool):
    return next((n for n, p in _props(tool).items()
                 if any(a.get("type") == "boolean" for a in _alts(p))), None)


def _enum_param(tool):
    return next((n for n, p in _props(tool).items()
                 if any("enum" in a for a in _alts(p))), None)


def _fixed(tool, base, retry_with):
    """Apply a refusal's `retry_with` to a call: every field it names gets a schema-valid value."""
    schema = tool.get("inputSchema") or {}
    fixed = dict(base)
    for field in (retry_with or {}).get("arguments", {}):
        fixed[field] = H.sample_value(field, _props(tool)[field], schema.get("$defs") or {})
    return fixed


@_mcp_sdk.requires_mcp_sdk
class TestI2_RefusalCarriesTheFix(unittest.TestCase):
    """Per tool: an unknown key, a missing required key, an ambiguous bool and a bad enum are each
    `refused`/`invalid_input` naming the field, the accepted names and the corrected call's shape;
    re-sending the call with `retry_with` applied is no longer `invalid_input`; the value is never
    echoed. FIRING CONTROL (mutant): skip `validate_call` — the unknown key is silently dropped."""

    def _check_refusal(self, name, out, field):
        self.assertEqual(out.get("status"), "refused", f"{name}: {out}")
        self.assertEqual(out.get("reason_code"), "invalid_input", f"{name}: {out}")
        self.assertEqual(out.get("field"), field, f"{name}: {out}")
        self.assertIsInstance(out.get("accepted"), list, name)
        self.assertIn("retry_with", out, name)
        self.assertEqual(out["retry_with"]["tool"], name)
        self.assertNotIn(SENTINEL, json.dumps(out), f"{name} echoed the value")

    def test_I2_every_tool_refuses_a_bad_call_with_its_fix(self):
        repo = H.make_repo()
        self.addCleanup(shutil.rmtree, repo, True)
        srv = H.McpStdio(repo)
        try:
            tools = [t for t in srv.tools_list() if t["name"] != "baseline"]
            cases = 0
            for t in tools:
                name, base = t["name"], H.required_call(t, repo)
                out = srv.tool(name, dict(base, **{"bogus_" + SENTINEL[:8].lower(): SENTINEL}))
                self._check_refusal(name, out, "bogus_" + SENTINEL[:8].lower())
                cases += 1
                for req in (t.get("inputSchema") or {}).get("required") or []:
                    if req == "path":
                        continue
                    call = {k: v for k, v in base.items() if k != req}
                    out = srv.tool(name, call)
                    self._check_refusal(name, out, req)
                    again = srv.tool(name, _fixed(t, call, out["retry_with"]))
                    # `retry_with` fixes the SHAPE: the front door accepts the call (a front-door
                    # refusal always carries `accepted`). Whether the sample names a real file is
                    # the world's business (`_mcp_stdio.WORLD_REFUSALS`).
                    self.assertFalse(again.get("reason_code") == "invalid_input"
                                     and "accepted" in again,
                                     f"{name}: retry_with did not fix it: {again}")
                    cases += 1
                    break
                b = _bool_param(t)
                if b:
                    out = srv.tool(name, dict(base, **{b: "yes"}))
                    self._check_refusal(name, out, b)
                    self.assertEqual(out.get("allowed"), ["true", "false"])
                    cases += 1
                e = _enum_param(t)
                if e:
                    out = srv.tool(name, dict(base, **{e: SENTINEL}))
                    self._check_refusal(name, out, e)
                    self.assertTrue(out.get("allowed"), name)
                    again = srv.tool(name, _fixed(t, base, out["retry_with"]))
                    self.assertFalse(again.get("reason_code") == "invalid_input"
                                     and "accepted" in again, f"{name}: {again}")
                    cases += 1
        finally:
            srv.close()
        self.assertGreater(cases, len(tools))

    def test_I2_a_wrong_name_is_told_the_right_one(self):
        repo = H.make_repo()
        self.addCleanup(shutil.rmtree, repo, True)
        srv = H.McpStdio(repo)
        try:
            out = srv.tool("query", {"path": repo, "targt": "f1"})
            empty = srv.tool("remember", {"path": repo, "text": "a fact"})
        finally:
            srv.close()
        self.assertEqual(out["did_you_mean"][0], "target")
        self.assertIn("target", out["retry_with"]["arguments"])
        self.assertEqual(empty["reason_code"], "invalid_input")
        self.assertNotIn("proposal_id", empty, "an unknown name minted a proposal")


@_mcp_sdk.requires_mcp_sdk
class TestI2_LenientWhereSafe(unittest.TestCase):
    """The SDK's representable conversions still succeed (no new failures): stringified
    `criteria`/`tests`/`tasks`, `depth="2"`, `blocking="0"`, `"true"` bools; `"yes"`/`"1"` are
    refused. FIRING CONTROLS (mutants): strict-type JSON strings (the stringified spec is refused),
    lax bools (`"yes"` is accepted)."""

    def test_I2_representable_values_pass_and_ambiguous_ones_do_not(self):
        repo = H.make_repo()
        self.addCleanup(shutil.rmtree, repo, True)
        spec = {"title": "adders add",
                "criteria": [{"id": "AC1", "text": "f1 adds 1"}],
                "tests": [{"name": "test_f1_adds", "ac_ids": ["AC1"]}],
                "tasks": [{"id": "T1", "title": "implement f1", "ac_ids": ["AC1"],
                           "scope": ["src/m1.py"]}],
                "scope": {"authorized": ["src/m1.py"], "deferred": []}}
        srv = H.McpStdio(repo)
        try:
            stringy = srv.tool("spec_emit", dict(H.stringified(spec), path=repo))
            depth = srv.tool("query", {"path": repo, "target": "f1", "depth": "2"})
            blocking = srv.tool("review_record", {"path": repo, "outcome": "passed",
                                                  "blocking": "0"})
            truthy = srv.tool("review_record", {"path": repo, "outcome": "failed",
                                                "independent": "TRUE"})
            yes = srv.tool("review_record", {"path": repo, "outcome": "passed",
                                             "independent": "yes"})
            one = srv.tool("review_record", {"path": repo, "outcome": "passed",
                                             "independent": "1"})
            int_bool = srv.tool("review_record", {"path": repo, "outcome": "passed",
                                                  "independent": 1})
            bad_outcome = srv.tool("review_record", {"path": repo, "outcome": "Passed"})
        finally:
            srv.close()
        self.assertEqual(stringy.get("status"), "proposed", stringy)
        self.assertEqual(depth.get("target"), "f1", depth)
        for ok in (blocking, truthy):
            self.assertNotEqual(ok.get("reason_code"), "invalid_input", ok)
            self.assertNotEqual(ok.get("status"), "error", ok)
        for bad in (yes, one, int_bool):
            self.assertEqual(bad.get("reason_code"), "invalid_input", bad)
            self.assertEqual(bad.get("field"), "independent")
        self.assertEqual(bad_outcome.get("allowed"), ["passed", "failed"])


class TestI2_ValidateCallTable(unittest.TestCase):
    """The table itself, unit-level against a hand-built schema (pure; no SDK)."""

    SCHEMA = {"properties": {
        "path": {"type": "string"},
        "n": {"type": "integer"},
        "x": {"type": "number"},
        "flag": {"type": "boolean"},
        "items": {"type": "array", "items": {"type": "string"}},
        "obj": {"type": "object"},
        "mode": {"enum": ["a", "b"], "type": "string"},
        "maybe": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
    }, "required": ["n"]}

    def _ok(self, **kw):
        return V.validate_call("t", self.SCHEMA, dict({"n": 1}, **kw))

    def _bad(self, **kw):
        with self.assertRaises(V.ValidationError) as caught:
            V.validate_call("t", self.SCHEMA, kw)
        return caught.exception

    def test_I2_lenient_conversions(self):
        self.assertEqual(self._ok(n="2")["n"], 2)
        self.assertEqual(self._ok(x="2.5")["x"], 2.5)
        self.assertIs(self._ok(flag="False")["flag"], False)
        self.assertEqual(self._ok(items='["a", "b"]')["items"], ["a", "b"])
        self.assertEqual(self._ok(obj='{"k": 1}')["obj"], {"k": 1})
        self.assertIsNone(self._ok(maybe=None)["maybe"])

    def test_I2_strict_refusals(self):
        self.assertEqual(self._bad(n=1, flag="yes").allowed, ("true", "false"))
        self.assertEqual(self._bad(n=1, flag=0).field, "flag")
        self.assertEqual(self._bad(n=1, mode="A").allowed, ("a", "b"))
        self.assertEqual(self._bad(n=None).field, "n")
        self.assertEqual(self._bad(n="2.5").field, "n")
        self.assertEqual(self._bad(n=1, items='{"not": "a list"}').field, "items")
        self.assertEqual(self._bad(n=True).field, "n")
        missing = self._bad(x=1.0)
        self.assertEqual(missing.field, "n")
        self.assertEqual(missing.retry_with, {"n": "<integer>"})
        unknown = self._bad(n=1, nn=2)
        self.assertEqual(unknown.field, "nn")
        self.assertEqual(unknown.did_you_mean[0], "n")


if __name__ == "__main__":
    unittest.main()
