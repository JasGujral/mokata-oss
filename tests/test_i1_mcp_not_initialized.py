"""0.0.28 lane I1 — `MCP-NOT-INITIALIZED-READS-AS-SERVER-FAULT` (design note I §2b H9, §3b).

The only real-session evidence on disk (Claude Code's own MCP log for this repo, 8 of 8 starts):
every start logged `session-registry: DEGRADED … (local mokata state could not be read) … check
permissions/disk under .mokata/temp_local/`. Root cause: that checkout has `.mokata/temp_local/` but
no `.mokata/manifest.json`. `Surface.load` raises "not initialized … run `mokata init`", the
registration filed it as local IO, and ~30 tools answered `status:"error"` + "this is a mokata-side
error … retry" — telling the model to retry a call that cannot succeed.

Now: every tool that needs state answers `refused` / `not-initialized` + `remedyCommand: "mokata
init"`, never `error`; and startup says so ONCE on stderr, with no DEGRADED notice.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)
import _mcp_sdk
import _mcp_stdio as H

from mokata.mcp import server as MS  # noqa: E402
from mokata.mcp import status as ST  # noqa: E402


def _uninitialized_repo():
    """The field shape exactly: `.mokata/temp_local/` exists, `.mokata/manifest.json` does not."""
    repo = H.make_repo(prefix="i1-noinit-", init=False)
    os.makedirs(os.path.join(repo, ".mokata", "temp_local"))
    return repo


@_mcp_sdk.requires_mcp_sdk
class TestI1_NotInitializedIsRefusedWithInit(unittest.TestCase):
    """Every tool, called from its schema alone in a repo that was never `mokata init`-ed, answers
    without `error`; each one that needs state answers `refused`/`not-initialized` naming `mokata
    init`. Startup stderr carries the one "not initialized" line and no DEGRADED notice. FIRING
    CONTROL (mutant): remove the not-initialized branch in `_as_error` — ~30 tools answer `error`."""

    def test_I1_an_uninitialized_repo_is_refused_with_its_fix_never_error(self):
        repo = _uninitialized_repo()
        self.addCleanup(shutil.rmtree, repo, True)
        srv = H.McpStdio(repo)
        try:
            tools = srv.tools_list()
            ids = {srv.tool_start(t["name"], H.required_call(t, repo)): t["name"]
                   for t in tools if t["name"] != "baseline"}
            verdict = srv.settle(ids)
            replies = {name: H.payload(srv.reply(rid)) for rid, name in ids.items()}
            srv.request("ping")
            err = srv.stderr_text()
        finally:
            srv.close()
        self.assertTrue(verdict.ok, repr(verdict))
        errors = {n: (o or {}).get("reason") for n, o in replies.items()
                  if not isinstance(o, dict) or o.get("status") == ST.ERROR}
        self.assertEqual(errors, {}, "a setup fault was reported as a mokata-side error")
        refused = {n: o for n, o in replies.items() if o.get("reason_code") == "not-initialized"}
        self.assertGreaterEqual(len(refused), 25, sorted(replies))
        for name, out in refused.items():
            self.assertEqual(out["status"], ST.REFUSED, name)
            self.assertEqual(out["remedyCommand"], "mokata init", name)
            self.assertIs(out["retryable"], False, name)
            self.assertNotIn(repo, json.dumps(out), f"{name} echoed the repo path")
            self.assertNotIn("retry", out["hint"].replace("re-call", ""), name)
        self.assertEqual(err.count(MS.NOT_INITIALIZED_NOTICE), 1, err)
        self.assertNotIn("DEGRADED", err)
        self.assertNotIn("temp_local", err)


class TestI1_RegistrationOfAnUninitializedRepo(unittest.TestCase):
    """In-process: `_register_this_window` on an uninitialized repo says the one line, once, and
    files nothing as degraded; a real IO failure stays DEGRADED and carries its detail."""

    def setUp(self):
        MS._NOT_INITIALIZED_SAID.clear()
        self.addCleanup(MS._NOT_INITIALIZED_SAID.clear)

    def test_I1_not_initialized_is_one_line_not_degraded(self):
        repo = _uninitialized_repo()
        self.addCleanup(shutil.rmtree, repo, True)
        from mokata import degrade
        with mock.patch.object(degrade, "note_degraded") as noted, \
                mock.patch("sys.stderr") as err:
            MS._register_this_window(repo)
            MS._register_this_window(repo)
        noted.assert_not_called()
        written = "".join(c.args[0] for c in err.write.call_args_list)
        self.assertEqual(written.count(MS.NOT_INITIALIZED_NOTICE), 1)

    def test_I1_a_real_registry_failure_stays_degraded_with_detail(self):
        repo = H.make_repo(prefix="i1-io-")
        self.addCleanup(shutil.rmtree, repo, True)
        from mokata import degrade, session_registry
        with mock.patch.object(session_registry, "touch", side_effect=OSError("disk full")), \
                mock.patch.object(degrade, "note_degraded") as noted:
            MS._register_this_window(repo)
        noted.assert_called_once()
        self.assertEqual(noted.call_args.kwargs.get("detail"), "disk full")


class TestI1_ConfigErrorKindIsClassifiedNotParsed(unittest.TestCase):
    """The branch matches `ConfigError.kind`, set at the raise in `Surface.load` — never the text."""

    def test_I1_surface_load_sets_the_kind(self):
        from mokata.config import NOT_INITIALIZED, ConfigError, Surface
        d = tempfile.mkdtemp(prefix="i1-kind-")
        self.addCleanup(shutil.rmtree, d, True)
        with self.assertRaises(ConfigError) as caught:
            Surface.load(d)
        self.assertEqual(caught.exception.kind, NOT_INITIALIZED)

    def test_I1_other_config_errors_stay_errors(self):
        from mokata.config import ConfigError
        out = MS._as_error("status", ConfigError("mokata is not initialized (but kind unset)"))
        self.assertEqual(out["status"], ST.ERROR)


if __name__ == "__main__":
    unittest.main()
