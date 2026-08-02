"""MCP-R.D1d — typed input validation + the `path` traversal guard.

Covers: the three validators as a shared mechanism; `query.kind` against the REAL grounded enum;
the `ci_check`/`spec_check` comma-lists (malformed refuses, empty + normal stay legal); the
security-critical traversal pair (an escaping relative `path` refuses AND reads nothing outside the
root, while "." and a legitimate absolute path are unaffected); the return-through-D0 contract
(`refused`, never `error`, never an exception); secret-safety (the field name travels, the value
never does); and the byte-identity of every valid path vs post-D1c.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import ast
import contextlib
import inspect
import json
import os
import shutil
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata.config import Surface
from mokata.knowledge import QUERY_KINDS
from mokata.mcp import server as SRV
from mokata.mcp import status as ST
from mokata.mcp import tools_read as TR
from mokata.mcp import tools_spec as TSP
from mokata.mcp import validation as V


def _init(d, profile="standard"):
    """A real initialized mokata repo at `d` — the tools need a loadable surface."""
    from mokata.init import init_repo
    init_repo(root=d, profile=profile, assume_yes=True, out=lambda _: None)
    return Surface.load(d)


@contextlib.contextmanager
def _repo():
    """A temp repo whose teardown waits for D0's R5 self-registration.

    These guards call tools THROUGH `_serve` (that is the point — the refusal contract is the
    served one), and `_serve` fires the R5 registration in a daemon thread that WRITES under
    `.mokata/temp_local/`. Without the drain, teardown races that write and `rmtree` fails on a
    non-empty directory — a fixture race, not a product fault. `_await_registrations` is D0's
    documented test seam for exactly this."""
    d = tempfile.mkdtemp()
    try:
        yield d
    finally:
        SRV._await_registrations(5.0)
        shutil.rmtree(d, ignore_errors=True)


# ======================================================================================
# THE VALIDATORS — one shared module, each a pure guard
# ======================================================================================

class TestValidateEnum(unittest.TestCase):

    def test_mcp_r_d1d_enum_member_returns_unchanged(self):
        # A guard, not a normalizer: the valid value comes back byte-identical.
        self.assertEqual(V.validate_enum("callers", ("callers", "callees"), "kind"), "callers")

    def test_mcp_r_d1d_enum_miss_names_field_and_allowed(self):
        with self.assertRaises(V.ValidationError) as cm:
            V.validate_enum("nope", ("callers", "callees"), "kind")
        self.assertEqual(cm.exception.field, "kind")
        self.assertEqual(cm.exception.allowed, ("callers", "callees"))

    def test_mcp_r_d1d_enum_does_not_case_fold_or_alias(self):
        # No silent coercion — "Callers" is a caller mistake, and a guard that fixed it would hide
        # a real bug in whatever generated the argument.
        for bad in ("Callers", "callers ", "call"):
            with self.assertRaises(V.ValidationError):
                V.validate_enum(bad, ("callers",), "kind")


class TestValidateCommaList(unittest.TestCase):

    def test_mcp_r_d1d_comma_list_parse_matches_the_pre_d1d_parse(self):
        # Byte-identical to the `[x.strip() for x in v.split(",") if x.strip()]` every call site had.
        for raw in ("a.py,b.py", " a.py , b.py ", "a.py", "a.py,,b.py"):
            self.assertEqual(V.validate_comma_list(raw, "files"),
                             [x.strip() for x in raw.split(",") if x.strip()])

    def test_mcp_r_d1d_comma_list_empty_is_legal_not_malformed(self):
        # The documented default of every call site — absent is not malformed.
        self.assertEqual(V.validate_comma_list("", "files"), [])

    def test_mcp_r_d1d_comma_list_separators_only_is_malformed(self):
        for bad in (",", " , , ", ",,,", "   ,"):
            with self.assertRaises(V.ValidationError) as cm:
                V.validate_comma_list(bad, "files")
            self.assertEqual(cm.exception.field, "files")
            self.assertIsNone(cm.exception.allowed)      # open-valued: no closed set to offer

    def test_mcp_r_d1d_comma_list_control_characters_refuse(self):
        for bad in ("a.py\x00b.py", "a.py\nb.py", "a.py\r\nb.py"):
            with self.assertRaises(V.ValidationError):
                V.validate_comma_list(bad, "files")


class TestGuardPath(unittest.TestCase):

    def test_mcp_r_d1d_dot_default_passes_unchanged(self):
        # The default of all 55 tools. A false positive here would break the entire surface.
        self.assertEqual(V.guard_path("."), ".")

    def test_mcp_r_d1d_empty_path_stays_legal(self):
        # "" has always behaved as "." (os.path.join("", ".mokata")); D1d adds no new rejection.
        self.assertEqual(V.guard_path(""), "")

    def test_mcp_r_d1d_absolute_repo_path_passes_unchanged(self):
        with _repo() as d:
            self.assertEqual(V.guard_path(d), d)
            # …including one that is nowhere near the cwd — there is no server-pinned root.
            self.assertEqual(V.guard_path(os.path.abspath(os.sep)), os.path.abspath(os.sep))

    def test_mcp_r_d1d_relative_path_inside_the_root_passes(self):
        with _repo() as d:
            prev = os.getcwd()
            os.chdir(d)
            try:
                for ok in (".", "sub", "sub/repo", "./sub/../sub"):
                    self.assertEqual(V.guard_path(ok), ok)
            finally:
                os.chdir(prev)

    def test_mcp_r_d1d_relative_traversal_refuses(self):
        with _repo() as d:
            inner = os.path.join(d, "repo")
            os.makedirs(inner)
            prev = os.getcwd()
            os.chdir(inner)
            try:
                for bad in ("..", "../..", "../../etc/passwd", "sub/../../.."):
                    with self.assertRaises(V.ValidationError, msg=bad) as cm:
                        V.guard_path(bad)
                    self.assertEqual(cm.exception.field, "path")
            finally:
                os.chdir(prev)

    def test_mcp_r_d1d_symlink_cannot_launder_an_escape(self):
        # A symlink INSIDE the root pointing OUT of it is resolved before the comparison, so it
        # refuses — normpath alone would have accepted it.
        with tempfile.TemporaryDirectory() as outside, tempfile.TemporaryDirectory() as root:
            link = os.path.join(root, "escape")
            try:
                os.symlink(outside, link)
            except (OSError, NotImplementedError):       # pragma: no cover - platform without links
                self.skipTest("symlinks unavailable on this platform")
            prev = os.getcwd()
            os.chdir(root)
            try:
                with self.assertRaises(V.ValidationError):
                    V.guard_path("escape")
            finally:
                os.chdir(prev)

    def test_mcp_r_d1d_nul_byte_refuses(self):
        with self.assertRaises(V.ValidationError):
            V.guard_path("repo\x00/etc")


# ======================================================================================
# THE ENUM — `query.kind` against the REAL grounded set
# ======================================================================================

class TestQueryKindEnum(unittest.TestCase):

    def test_mcp_r_d1d_query_tool_kinds_are_the_grounded_set(self):
        # Grounded, not hard-coded: QUERY_KINDS from the knowledge layer + `semantic`, which
        # `run_query` routes itself. A drift in either is a failure here, not a runtime surprise.
        self.assertEqual(TR.QUERY_TOOL_KINDS, tuple(QUERY_KINDS) + ("semantic",))
        # CRG-NAV added the two NAVIGATION kinds (`defs`/`refs`) to the grounded set — the enum
        # follows QUERY_KINDS, which is exactly why no new MCP tool was needed for them.
        self.assertEqual(tuple(QUERY_KINDS),
                         ("callers", "callees", "implementers", "imports", "blast_radius",
                          "defs", "refs"))

    def test_mcp_r_d1d_query_bad_kind(self):
        # THE headline: a bad enum comes back as a structured refusal naming the field and the real
        # allowed set — through `_serve`, which is how every agent reaches this tool.
        with _repo() as d:
            _init(d)
            out = SRV._serve(TR.query, name="query")(path=d, kind="callerz", target="x")
        self.assertEqual(out["status"], ST.REFUSED)
        self.assertEqual(out["field"], "kind")
        self.assertEqual(out["allowed"], list(TR.QUERY_TOOL_KINDS))
        self.assertFalse(out["committed"])
        self.assertEqual(out["reason_code"], V.INVALID_INPUT)

    def test_mcp_r_d1d_query_every_valid_kind_is_accepted(self):
        with _repo() as d:
            _init(d)
            for kind in TR.QUERY_TOOL_KINDS:
                out = SRV._serve(TR.query, name="query")(path=d, kind=kind, target="x")
                self.assertNotEqual(out.get("status"), ST.REFUSED, kind)

    def test_mcp_r_d1d_query_valid_kind_is_byte_identical(self):
        # The valid path gains NO key and changes no value vs the pre-D1d body.
        with _repo() as d:
            _init(d)
            from mokata.knowledge import KnowledgeLayer
            from mokata.knowledge.layer import run_query
            from mokata.config import Surface
            expected = run_query(KnowledgeLayer.from_surface(Surface.load(d)),
                                 "callers", "x", depth=2).to_dict()
            self.assertEqual(json.dumps(TR.query(path=d, kind="callers", target="x")),
                             json.dumps(expected))


# ======================================================================================
# THE COMMA-LISTS — ci_check + spec_check
# ======================================================================================

class TestCommaListTools(unittest.TestCase):

    def test_mcp_r_d1d_ci_check_malformed(self):
        with _repo() as d:
            _init(d)
            out = SRV._serve(TR.ci_check, name="ci_check")(path=d, files=",,,")
        self.assertEqual(out["status"], ST.REFUSED)
        self.assertEqual(out["field"], "files")
        self.assertNotIn("allowed", out)                  # open-valued field: no set is offered
        # …and the symbols field refuses under its OWN name, not the first field's.
        with _repo() as d:
            _init(d)
            out = SRV._serve(TR.ci_check, name="ci_check")(path=d, files="a.py", symbols=" , ")
        self.assertEqual(out["field"], "symbols")

    def test_mcp_r_d1d_ci_check_valid(self):
        # A normal comma list AND the empty default both still work — the legal empty case is the
        # one a careless guard would break.
        with _repo() as d:
            _init(d)
            for kwargs in ({}, {"files": ""}, {"files": "a.py,b.py"},
                           {"files": "a.py", "symbols": "f,g"}):
                out = SRV._serve(TR.ci_check, name="ci_check")(path=d, **kwargs)
                self.assertNotEqual(out.get("status"), ST.REFUSED, kwargs)
                self.assertIn("blocked", out)

    def test_mcp_r_d1d_ci_check_valid_is_byte_identical(self):
        with _repo() as d:
            _init(d)
            from mokata import ci_check as CI
            res = CI.run_ci_check(d, ["a.py", "b.py"], changed_symbols=None)
            expected = {"blocked": res.blocked, "degraded": res.degraded, "overall": res.overall,
                        "initialized": res.initialized,
                        "legs": [{"name": lg.name, "status": lg.status, "summary": lg.summary,
                                  "degraded": lg.degraded, "unblock": lg.unblock}
                                 for lg in res.legs],
                        "comment_body": res.comment_body()}
            self.assertEqual(json.dumps(TR.ci_check(path=d, files="a.py,b.py")),
                             json.dumps(expected))

    def test_mcp_r_d1d_spec_check_malformed(self):
        with _repo() as d:
            _init(d)
            out = SRV._serve(TSP.spec_check, name="spec_check")(path=d, symbols=", ,")
        self.assertEqual(out["status"], ST.REFUSED)
        self.assertEqual(out["field"], "symbols")

    def test_mcp_r_d1d_spec_check_valid(self):
        with _repo() as d:
            _init(d)
            for kwargs in ({}, {"symbols": ""}, {"symbols": "f,g"}, {"files": "a.py"}):
                out = SRV._serve(TSP.spec_check, name="spec_check")(path=d, **kwargs)
                self.assertNotEqual(out.get("status"), ST.REFUSED, kwargs)


# ======================================================================================
# THE TRAVERSAL PAIR — security-critical
# ======================================================================================

class TestPathTraversal(unittest.TestCase):

    def test_mcp_r_d1d_path_traversal_refused(self):
        with _repo() as d:
            inner = os.path.join(d, "repo")
            os.makedirs(inner)
            prev = os.getcwd()
            os.chdir(inner)
            try:
                out = SRV._serve(TR.status, name="status")(path="../../etc/passwd")
            finally:
                os.chdir(prev)
        self.assertEqual(out["status"], ST.REFUSED)
        self.assertEqual(out["field"], "path")
        self.assertFalse(out["committed"])

    def test_mcp_r_d1d_path_traversal_reads_nothing_outside_the_root(self):
        # THE security claim. The pre-step runs ahead of BOTH the body thread and the R5
        # self-registration (which calls `Surface.load(path)`), so a traversing path touches the
        # filesystem zero times — proven by making every read this call could make explode.
        import mokata.config as CFG
        opened = []
        real_open = open

        def spy_open(file, *a, **kw):
            opened.append(str(file))
            return real_open(file, *a, **kw)

        loads = []
        real_load = CFG.Surface.load

        def spy_load(root=".", detector=None):
            loads.append(root)
            return real_load(root, detector)

        with _repo() as d:
            inner = os.path.join(d, "repo")
            os.makedirs(inner)
            prev = os.getcwd()
            os.chdir(inner)
            CFG.Surface.load = classmethod(lambda cls, root=".", detector=None: spy_load(root))
            import builtins
            builtins.open = spy_open
            try:
                out = SRV._serve(TR.status, name="status")(path="../../../etc")
                SRV._await_registrations(2.0)     # drain R5 — it must never have been spawned
            finally:
                builtins.open = real_open
                CFG.Surface.load = real_load
                os.chdir(prev)

        self.assertEqual(out["status"], ST.REFUSED)
        self.assertEqual(loads, [], "a refused path still reached Surface.load")
        outside = [p for p in opened if "etc" in p]
        self.assertEqual(outside, [], f"a refused path still read outside the root: {outside}")

    def test_mcp_r_d1d_path_legit_ok(self):
        # The other half of the pair — the guard must not be overbroad.
        with _repo() as d:
            _init(d)
            absolute = SRV._serve(TR.status, name="status")(path=d)      # legit absolute repo path
            self.assertNotEqual(absolute.get("status"), ST.REFUSED)

            prev = os.getcwd()
            os.chdir(d)
            try:
                dot = SRV._serve(TR.status, name="status")(path=".")     # the default
                nokw = SRV._serve(TR.status, name="status")()            # …and omitted entirely
            finally:
                os.chdir(prev)
        self.assertNotEqual(dot.get("status"), ST.REFUSED)
        self.assertNotEqual(nokw.get("status"), ST.REFUSED)

    def test_mcp_r_d1d_path_guard_covers_every_tool(self):
        # The pre-step is surface-wide: every registered tool refuses a traversing path, with no
        # per-tool wiring. A tool added tomorrow inherits the guard for free.
        from mokata.mcp.registry import TOOLS
        with _repo() as d:
            inner = os.path.join(d, "repo")
            os.makedirs(inner)
            prev = os.getcwd()
            os.chdir(inner)
            try:
                for spec in TOOLS:
                    out = SRV._serve(spec.fn, name=spec.name)(path="../../..")
                    self.assertEqual(out["status"], ST.REFUSED, spec.name)
                    self.assertEqual(out["field"], "path", spec.name)
            finally:
                os.chdir(prev)


# ======================================================================================
# RETURN THROUGH D0 — refused, never error, never an exception
# ======================================================================================

class TestReturnsThroughServe(unittest.TestCase):

    def test_mcp_r_d1d_refusal_is_a_dict_not_an_exception(self):
        with _repo() as d:
            _init(d)
            out = SRV._serve(TR.query, name="query")(path=d, kind="bogus", target="x")
        self.assertIsInstance(out, dict)
        self.assertEqual(out["status"], ST.REFUSED)

    def test_mcp_r_d1d_bad_input_is_refused_not_error(self):
        # R6 — the caller must distinguish "fix your call" from "the server broke" by `status`
        # alone. Pre-D1d a bad kind surfaced as `error` (the backend's ValueError, reclaimed).
        with _repo() as d:
            _init(d)
            out = SRV._serve(TR.query, name="query")(path=d, kind="bogus", target="x")
        self.assertNotEqual(out["status"], ST.ERROR)
        self.assertNotIn("isError", out)          # a refusal is not a server fault
        self.assertIn(out["status"], ST.STATUS_VOCAB)

    def test_mcp_r_d1d_a_real_server_fault_is_still_error(self):
        # The D0 backstop is untouched: a genuine exception is still `error`, not laundered into a
        # refusal by the new branch.
        def boom(path="."):
            raise RuntimeError("kaboom")

        out = SRV._serve(boom, name="boom")(path=".")
        self.assertEqual(out["status"], ST.ERROR)
        self.assertTrue(out["isError"])

    def test_mcp_r_d1d_refusal_names_the_operation(self):
        with _repo() as d:
            _init(d)
            out = SRV._serve(TR.ci_check, name="ci_check")(path=d, files=",")
        self.assertEqual(out["operation"], "ci_check")
        self.assertIn("ci_check", out["hint"])


# ======================================================================================
# SECRET-SAFETY — the field name travels; the value never does
# ======================================================================================

class TestSecretSafety(unittest.TestCase):

    def test_mcp_r_d1d_refusal_never_echoes_the_offending_path_value(self):
        secret = "s3cr3t-token-AKIA1234567890"
        with _repo() as d:
            inner = os.path.join(d, "repo")
            os.makedirs(inner)
            prev = os.getcwd()
            os.chdir(inner)
            try:
                out = SRV._serve(TR.status, name="status")(path=f"../../{secret}/repo")
            finally:
                os.chdir(prev)
        blob = json.dumps(out)
        self.assertNotIn(secret, blob, "the refusal echoed the offending path value")
        self.assertIn("path", blob)               # …but it DOES name the field, so it is fixable

    def test_mcp_r_d1d_refusal_never_echoes_the_offending_comma_list_value(self):
        secret = "Bearer-abcdef123456"
        with _repo() as d:
            _init(d)
            out = SRV._serve(TR.ci_check, name="ci_check")(path=d, files=f", {secret}\n,")
        self.assertNotIn(secret, json.dumps(out))
        self.assertEqual(out["field"], "files")

    def test_mcp_r_d1d_enum_refusal_echoes_allowed_but_not_the_value(self):
        # An ALLOWED set is mokata's own vocabulary and is safe to render; the caller's value is not.
        with _repo() as d:
            _init(d)
            out = SRV._serve(TR.query, name="query")(path=d, kind="tok-9f3c-secret", target="x")
        blob = json.dumps(out)
        self.assertNotIn("tok-9f3c-secret", blob)
        self.assertIn("blast_radius", blob)


# ======================================================================================
# SHARED MECHANISM — one module, one conversion site
# ======================================================================================

class TestSharedMechanism(unittest.TestCase):

    def test_mcp_r_d1d_validators_live_in_one_module(self):
        for fn in (V.validate_enum, V.validate_comma_list, V.guard_path,
                   V.validate_response_format, V.validate_surface_params, V.refusal):
            self.assertEqual(inspect.getmodule(fn).__name__, "mokata.mcp.validation")

    def test_mcp_r_d1d_no_tool_hand_rolls_a_validation_refusal(self):
        # Tools RAISE; only `_serve` builds the refusal dict. A tool that hand-rolled one would
        # drift the shape and defeat the single-vocab guarantee. Checked by IMPORT (AST), not by
        # substring: `tools_spec` has its own long-standing `_..._emit_refusal` helpers, which are
        # gate refusals, not input refusals, and must not false-positive here.
        for mod in (TR, TSP):
            self.assertNotIn("refusal", _imported_from_validation(mod), mod.__name__)
            self.assertNotIn(V.INVALID_INPUT, _code_without_docstrings(mod), mod.__name__)

    def test_mcp_r_d1d_no_tool_hand_rolls_a_comma_split(self):
        # The parse lives in the validator, not at each call site.
        for mod in (TR, TSP):
            self.assertNotIn('.split(",")', _code_without_docstrings(mod), mod.__name__)

    def test_mcp_r_d1d_single_refusal_conversion_site(self):
        # Exactly one function in the whole MCP package turns a ValidationError into a result.
        src = inspect.getsource(SRV)
        self.assertEqual(src.count("refusal(bad, op)") + src.count('refusal(box["exc"], op)'), 2)
        self.assertIn("except ValidationError", src)

    def test_mcp_r_d1d_validation_module_is_sdk_free(self):
        # Same discipline as registry/status/response_format/pagination — unit-testable without the
        # optional MCP SDK. Checked on the IMPORT STATEMENTS (the prose legitimately names FastMCP
        # when explaining how a client binds arguments), and proven by the fact that this module
        # imported at all in an env where the SDK may be absent.
        tree = ast.parse(inspect.getsource(V))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertFalse(alias.name == "mcp" or alias.name.startswith("mcp."),
                                     alias.name)
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                self.assertFalse(mod == "mcp" or mod.startswith("mcp."), mod)

    def test_mcp_r_d1d_new_tool_gets_the_path_guard_for_free(self):
        def newtool(path=".", thing=""):
            return {"status": "ok", "thing": thing}

        with _repo() as d:
            inner = os.path.join(d, "repo")
            os.makedirs(inner)
            prev = os.getcwd()
            os.chdir(inner)
            try:
                bad = SRV._serve(newtool, name="newtool")(path="../..")
                ok = SRV._serve(newtool, name="newtool")(path=".", thing="x")
            finally:
                os.chdir(prev)
        self.assertEqual(bad["status"], ST.REFUSED)
        self.assertEqual(ok, {"status": "ok", "thing": "x"})


# ======================================================================================
# response_format — D1b's deferred enum, now loud
# ======================================================================================

class TestResponseFormatEnum(unittest.TestCase):

    def test_mcp_r_d1d_unknown_response_format_refuses(self):
        with _repo() as d:
            _init(d)
            out = SRV._serve(TR.doctor, name="doctor")(path=d, response_format="verbose")
        self.assertEqual(out["status"], ST.REFUSED)
        self.assertEqual(out["field"], "response_format")
        self.assertEqual(out["allowed"], ["concise", "detailed"])

    def test_mcp_r_d1d_both_valid_formats_are_unaffected(self):
        with _repo() as d:
            _init(d)
            concise = SRV._serve(TR.doctor, name="doctor")(path=d)
            detailed = SRV._serve(TR.doctor, name="doctor")(path=d, response_format="detailed")
        self.assertNotIn("report", concise)          # D1b's concise default, unchanged
        self.assertIn("report", detailed)            # …and detailed still carries the render

    def test_mcp_r_d1d_is_detailed_stays_lenient(self):
        # D1b's predicate is NOT changed — validation is a separate, additive guard at the surface,
        # so D1b's pinned single-branch-site contract is untouched.
        from mokata.mcp import response_format as RF
        self.assertFalse(RF.is_detailed("verbose"))


# ======================================================================================
# SCHEMA — validation is IN-BODY / at the wrapper: no signature moved
# ======================================================================================

class TestSchemaUnchanged(unittest.TestCase):

    def test_mcp_r_d1d_no_tool_signature_changed(self):
        # D1d adds NO parameter and renames none — unlike D1b (`response_format`) and D1c
        # (`limit`/`offset`), the guard is in-body, so every tool's inputSchema is byte-identical
        # to post-D1c. This is the pin.
        expected = {
            "query": ["path", "kind", "target", "depth"],
            "ci_check": ["path", "files", "symbols"],
            "spec_check": ["path", "symbols", "files", "text", "phase", "approve", "confirm",
                           "proposal_id"],
            "doctor": ["path", "response_format"],
            "status": ["path"],
        }
        for name, params in expected.items():
            fn = {"spec_check": TSP.spec_check}.get(name, getattr(TR, name, None))
            self.assertEqual(list(inspect.signature(fn).parameters), params, name)

    def test_mcp_r_d1d_serve_stays_signature_transparent(self):
        # The pre-step must not disturb D0's `__wrapped__` transparency, which the FastMCP schema
        # is built from (D1a/b/c all depend on it).
        wrapped = SRV._serve(TR.query, name="query")
        self.assertEqual(inspect.signature(wrapped), inspect.signature(TR.query))


def _imported_from_validation(mod):
    """The names `mod` pulls out of `mcp.validation` — the precise way to ask "does this tool module
    build refusals itself, or only raise?" A substring scan would collide with unrelated names."""
    names = []
    for node in ast.walk(ast.parse(inspect.getsource(mod))):
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("validation"):
            names.extend(a.name for a in node.names)
    return names


def _code_without_docstrings(mod):
    """Module source with docstrings stripped — the tool docstrings legitimately NAME the validated
    fields and their allowed values, so a raw source scan would false-positive on the prose."""
    tree = ast.parse(inspect.getsource(mod))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                node.body.pop(0)
    return ast.unparse(tree)


if __name__ == "__main__":
    unittest.main()
