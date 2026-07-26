"""MCP-R.D0 — THE SYSTEMIC DISPATCH WRAPPER (0.0.15, doc 88 §D0).

The single universal wrapper `_with_registration` is expanded into `_serve`, so EVERY served MCP
tool call is guaranteed a bounded, machine-legible outcome instead of a client-side timeout into
silence (P16 legibility, P14 no-unbounded-hang, P22 EDGE). This suite is the fault-injection
deliverable — each numbered requirement of §D0 has at least one named test:

  R5  never-block front door     : test_mcp_r_d0_prehook_nonblocking
  R1  never-hang / timed_out     : test_mcp_r_d0_timeout_returns_status
  R2  exception -> isError       : test_mcp_r_d0_exception_becomes_iserror
  R3  never-None                 : test_mcp_r_d0_none_coerced
  R6  status vocab (SSOT)        : test_mcp_r_d0_status_vocab
  R7  baseline poster child      : test_mcp_r_d0_baseline_mcp_capped
  SCHEMA-PARITY (absolute)       : test_mcp_r_d0_schema_unchanged
  Negative (invisible on happy)  : test_mcp_r_d0_happy_path_byte_identical
  Secret-safety                  : test_mcp_r_d0_no_secret_leak

R4 (progress/heartbeat) lands as a documented degrade-clean NO-OP this stage: the SDK's
`Context.report_progress` is a coroutine that must be injected as a tool parameter and awaited on
the event loop, which a synchronous, SDK-free tool body running in a worker thread cannot reach
without altering the tool schema (forbidden this stage) — so liveness is delivered by the bounded
budget + `running`/`timed_out` statuses instead. That decision is asserted in
test_mcp_r_d0_r4_progress_is_documented_noop.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import inspect
import json
import threading
import time
import unittest
from unittest import mock

import _support  # noqa: F401 - puts src/ on the path

from mokata import baseline as B                       # noqa: E402
from mokata import degrade as D                         # noqa: E402
from mokata.mcp import server as MS                      # noqa: E402
from mokata.mcp import status as ST                      # noqa: E402
from mokata.mcp import tools_read as TR                  # noqa: E402
from mokata.mcp import tools_memory as TM                # noqa: E402
from mokata.mcp.registry import TOOLS                    # noqa: E402


def _repo(d):
    from mokata.init import init_repo
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)


class _Base(unittest.TestCase):
    """Neutralise window self-registration for the pure-wrapper tests (R5 has its own patch), so a
    best-effort registry side effect never touches disk or emits a notice during these assertions."""

    def setUp(self):
        D.reset_degrade_notices()
        self._reg_patch = mock.patch.object(MS, "_register_this_window", lambda path: None)
        self._reg_patch.start()

    def tearDown(self):
        self._reg_patch.stop()
        MS._await_registrations(2.0)          # drain any fire-and-forget registration threads
        D.reset_degrade_notices()


# ======================================================================================
# R6 — the status vocabulary is a documented single source of truth
# ======================================================================================

class TestStatusVocab(_Base):

    def test_mcp_r_d0_status_vocab(self):
        # (a) the vocab is exactly the documented set, importable by tools + tests (no magic strings)
        self.assertEqual(
            set(ST.STATUS_VOCAB),
            {"proposed", "refused", "blocked", "running", "timed_out", "degraded",
             "committed", "error"})
        # (b) every status the wrapper itself can PRODUCE is a member of the vocab
        for produced in (MS._timed_out("op", 1.0),
                         MS._as_error("op", RuntimeError("x")),
                         MS._coerce("op", None)):
            self.assertIn(produced["status"], ST.STATUS_VOCAB)

    def test_real_tool_status_is_in_the_vocab_and_proposed_is_immediate(self):
        # a REAL propose-only write returns `proposed` — a vocab member, returned immediately and
        # NEVER wrapped into a timeout (proposed is an instant return, not a long-running op).
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            started = time.monotonic()
            res = MS._serve(TM.remember, name="remember")(path=d, subject="s", value="v")
            elapsed = time.monotonic() - started
        self.assertEqual(res["status"], "proposed")
        self.assertIn(res["status"], ST.STATUS_VOCAB)
        self.assertNotIn("isError", res)               # not an error-shaped wrap
        self.assertLess(elapsed, 5.0)                  # immediate, not sat on a budget


# ======================================================================================
# R1 — never-hang: a stalled body yields a structured timed_out within budget
# ======================================================================================

class TestNeverHang(_Base):

    def test_mcp_r_d0_timeout_returns_status(self):
        def slow(path="."):
            time.sleep(5)
            return {"ok": True}

        with mock.patch.object(MS, "MCP_SURFACE_TIMEOUT_SECONDS", 0.3):
            started = time.monotonic()
            res = MS._serve(slow, name="slowtool")(path=".")
            elapsed = time.monotonic() - started

        self.assertEqual(res["status"], "timed_out")
        self.assertIs(res["isError"], True)
        self.assertEqual(res["operation"], "slowtool")
        self.assertIn("slowtool", res["reason"])            # names the OPERATION
        self.assertIn("mokata", res["hint"].lower())        # names the CLI fallback
        self.assertLess(elapsed, 3.0, "the wrapper hung past its budget instead of timing out")


# ======================================================================================
# R2 — any uncaught exception becomes mokata's own isError shape (voice reclaimed)
# ======================================================================================

class TestExceptionBecomesIsError(_Base):

    def test_mcp_r_d0_exception_becomes_iserror(self):
        def boom(path="."):
            raise ValueError("internal detail that must not surface verbatim")

        res = MS._serve(boom, name="boomtool")(path=".")

        self.assertEqual(res["status"], "error")
        self.assertIs(res["isError"], True)
        self.assertEqual(res["operation"], "boomtool")
        self.assertIn("ValueError", res["reason"])          # the TYPE names the failure
        self.assertTrue(res["hint"].strip())                # actionable, in mokata's voice
        self.assertNotIn("internal detail", json.dumps(res))  # the raw message is NOT surfaced


# ======================================================================================
# R3 — a None / non-dict return is coerced to a structured error
# ======================================================================================

class TestNeverNone(_Base):

    def test_mcp_r_d0_none_coerced(self):
        for bad in (None, "just a string", 42, ["a", "list"], (1, 2)):
            with self.subTest(bad=bad):
                res = MS._serve(lambda path=".", _b=bad: _b, name="badtool")(path=".")
                self.assertEqual(res["status"], "error")
                self.assertIs(res["isError"], True)
                self.assertEqual(res["operation"], "badtool")


# ======================================================================================
# R5 — the pre-hook is non-blocking: a stalled self-registration never delays the body
# ======================================================================================

class TestPrehookNonBlocking(_Base):

    def test_mcp_r_d0_prehook_nonblocking(self):
        gate = threading.Event()

        def blocking_register(path):
            gate.wait(10)          # simulates the up-to-10s cross-process lock wait (root cause 4)

        with mock.patch.object(MS, "_register_this_window", blocking_register):
            started = time.monotonic()
            res = MS._serve(lambda path=".": {"ok": True}, name="fast")(path=".")
            elapsed = time.monotonic() - started
            self.assertEqual(res, {"ok": True})
            self.assertLess(elapsed, 2.0,
                            "a blocking self-registration delayed the served call")
            gate.set()             # release the stuck registration before restoring the patch
        MS._await_registrations(2.0)


# ======================================================================================
# R7 — baseline: capped far below 600s on the MCP surface; the terminal bound is unchanged
# ======================================================================================

class TestBaselinePosterChild(_Base):

    def test_mcp_r_d0_baseline_mcp_capped(self):
        # the two-bound split: MCP-surface cap << terminal cap
        self.assertLess(B.BASELINE_MCP_TIMEOUT_SECONDS, B.BASELINE_TIMEOUT_SECONDS)
        self.assertLessEqual(B.BASELINE_MCP_TIMEOUT_SECONDS, 120)
        self.assertGreaterEqual(B.BASELINE_MCP_TIMEOUT_SECONDS, 60)

        # the _serve wall-clock budget for `baseline` uses the capped constant, not 600s
        self.assertEqual(MS._mcp_timeout_for("baseline"), B.BASELINE_MCP_TIMEOUT_SECONDS)
        self.assertNotEqual(MS._mcp_timeout_for("baseline"), B.BASELINE_TIMEOUT_SECONDS)

        # the MCP baseline TOOL bounds its subprocess at the MCP cap (never 600s)
        captured = {}

        def fake_status(command, cwd=None, timeout=B.BASELINE_TIMEOUT_SECONDS):
            captured["timeout"] = timeout
            return B.BaselineResult(state=B.UNKNOWN, detail="stub")

        import tempfile
        with tempfile.TemporaryDirectory() as d:
            with mock.patch("mokata.baseline.baseline_status", fake_status):
                out = TR.baseline(path=d)
        self.assertEqual(captured["timeout"], B.BASELINE_MCP_TIMEOUT_SECONDS)
        self.assertNotEqual(captured["timeout"], B.BASELINE_TIMEOUT_SECONDS)
        self.assertIn("ok", out)     # shape preserved, no new status key (MCP-R.D1b: `report` opt-in)

        # the TERMINAL bound is UNCHANGED: baseline_status still defaults to the 600s cap, which the
        # CLI/`mokata baseline` path (diagnostics.py, no override) therefore still gets.
        self.assertEqual(B.BASELINE_TIMEOUT_SECONDS, 600)
        self.assertEqual(
            inspect.signature(B.baseline_status).parameters["timeout"].default,
            B.BASELINE_TIMEOUT_SECONDS)


# ======================================================================================
# SCHEMA-PARITY — _serve is signature/schema transparent (D1a depends on this)
# ======================================================================================

class TestSchemaParity(_Base):

    def test_mcp_r_d0_schema_unchanged(self):
        # __wrapped__ points at the raw fn, so inspect.signature (and FastMCP) see the unwrapped one
        sample_specs = [s for s in TOOLS
                        if s.name in {"baseline", "status", "remember", "query", "audit"}]
        self.assertTrue(sample_specs)
        for spec in sample_specs:
            self.assertIs(MS._serve(spec.fn, name=spec.name, kind=spec.kind).__wrapped__, spec.fn)

    @unittest.skipUnless(MS.mcp_available(), "optional MCP SDK not installed")
    def test_mcp_r_d0_schema_unchanged_via_fastmcp(self):
        import asyncio

        from mcp.server.fastmcp import FastMCP

        def _schema(fn, name):
            srv = FastMCP("parity")
            srv.add_tool(fn, name=name)
            tools = asyncio.run(srv.list_tools())
            return {t.name: t.inputSchema for t in tools}[name]

        for spec in [s for s in TOOLS
                     if s.name in {"baseline", "status", "remember", "query", "audit"}]:
            with self.subTest(tool=spec.name):
                raw = _schema(spec.fn, spec.name)
                wrapped = _schema(MS._serve(spec.fn, name=spec.name, kind=spec.kind), spec.name)
                self.assertEqual(wrapped, raw,
                                 f"{spec.name}: _serve changed the FastMCP input schema")


# ======================================================================================
# Negative — the wrapper is invisible on the happy path (no new keys, no artifacts)
# ======================================================================================

class TestHappyPathInvisible(_Base):

    def test_mcp_r_d0_happy_path_byte_identical(self):
        payload = {"ok": True, "report": "baseline: GREEN", "nested": {"a": [1, 2]}}
        out = MS._serve(lambda path=".": payload, name="baseline")(path=".")
        self.assertIs(out, payload)                         # same object — no copy, no wrap
        self.assertEqual(set(out), {"ok", "report", "nested"})   # no injected keys


# ======================================================================================
# Secret-safety — timeout/error renders name the OPERATION, never the args
# ======================================================================================

class TestSecretSafety(_Base):

    def test_mcp_r_d0_no_secret_leak(self):
        secret_pw = "s3cr3t" + "_pw"
        dsn = "postgres" + "://u:" + secret_pw + "@db.internal:5432/app"
        secret_path = "/etc/mokata/" + secret_pw

        # timed_out: the stalled body carried the DSN as an arg; the render must not echo it
        with mock.patch.object(MS, "MCP_SURFACE_TIMEOUT_SECONDS", 0.2):
            res_t = MS._serve(lambda path=".", dsn="": time.sleep(3),
                              name="op")(path=secret_path, dsn=dsn)
        blob_t = json.dumps(res_t)
        self.assertNotIn(secret_pw, blob_t)
        self.assertNotIn(dsn, blob_t)
        self.assertNotIn(secret_path, blob_t)
        self.assertEqual(res_t["operation"], "op")          # the OPERATION, not the args

        # error: the exception MESSAGE itself carries the secret; only the type name may surface
        def boom(path=".", dsn=""):
            raise RuntimeError(dsn)

        res_e = MS._serve(boom, name="op")(path=secret_path, dsn=dsn)
        blob_e = json.dumps(res_e)
        self.assertNotIn(secret_pw, blob_e)
        self.assertNotIn(dsn, blob_e)
        self.assertNotIn(secret_path, blob_e)
        self.assertEqual(res_e["operation"], "op")


# ======================================================================================
# R4 — progress/heartbeat is a documented degrade-clean no-op this stage
# ======================================================================================

class TestR4DocumentedNoop(_Base):

    def test_mcp_r_d0_r4_progress_is_documented_noop(self):
        # R4 must NOT be faked: there is no live progress-emit seam that alters a tool schema this
        # stage. The contract is stated in module scope so the degrade is legible, not silent.
        self.assertTrue(getattr(MS, "R4_PROGRESS_NOOP", False))
        self.assertIn("no-op", (MS._serve.__doc__ or "").lower())


if __name__ == "__main__":
    unittest.main()
