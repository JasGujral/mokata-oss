"""IDE e2e -- the harness pins the page's locale (0.0.28 H, the behavioural guard for it).

Both browsers default to en-US on a developer's Mac and on ubuntu runners, so a harness that
dropped its `locale=` would still read en-US and every check against "en-US" would stay green. This
flow therefore pins a locale NO host defaults to (de-DE): if `new_page` is not given the harness's
`LOCALE`, the page reports the host's language instead and these steps (and the setUpClass self-check)
red.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import shutil
import subprocess
import sys
import tempfile

import _harness as H


class TheHarnessPinsThePagesLocale(H.IdeFlow):
    FIXTURE = "shop"
    LOCALE = "de-DE"
    SELF_CHECK_LOCALE = False        # the steps below ARE the check, so a drop reds as a step

    def test_1_the_page_reports_the_pinned_language(self):
        self.assertEqual("de-DE", self.page.evaluate("navigator.language"))

    def test_2_the_page_formats_numbers_in_the_pinned_locale(self):
        self.assertEqual("1.234,5", self.page.evaluate("(1234.5).toLocaleString(navigator.language)"))


# ---- 0.0.29 D6 `E2E-RACES-UNNAMED`: a red flow keeps its evidence ----------------------------
# (The pure D6 checks -- the context-door sweep and the Firefox filter -- are unit tests:
# tests/test_d6_e2e_harness_doors.py. Every class in THIS directory is a harness flow, o9.)

_HERE = os.path.dirname(os.path.abspath(__file__))

#: The planted red flow, run in a CHILD process: Playwright's sync API cannot be started twice in
#: one thread, and this flow already holds one. The child counts `_keep_evidence` calls itself.
_RED_FLOW = """
import unittest
import _harness as H
calls = []
real = H.IdeFlow._keep_evidence
def counted(self):
    calls.append(self.id())
    return real(self)
H.IdeFlow._keep_evidence = counted

class Red(H.IdeFlow):
    FIXTURE = "shop"
    def test_1_red_on_purpose(self):
        self.page.evaluate("console.error('planted for the evidence check')")
        self.page.wait_for_timeout(200)
        self.fail("planted red")

result = unittest.TestResult()
unittest.TestSuite([Red("test_1_red_on_purpose")]).run(result)
print("ERRORS=%d FAILURES=%d SKIPPED=%d KEPT=%d" % (len(result.errors), len(result.failures),
                                                    len(result.skipped), len(calls)))
for _, tb in result.errors:
    print(tb)
"""


class AFailingFlowLeavesItsEvidence(H.IdeFlow):
    """A red flow under `$MOKATA_E2E_EVIDENCE_DIR` writes its trace, a screenshot, the console and
    request logs and the server's output; unset, it keeps nothing. A deliberately failing flow, run
    for real (its own server and browser) in a child process."""
    FIXTURE = "shop"

    def _run_red_flow(self, evidence_dir):
        env = dict(os.environ)
        env.pop("MOKATA_E2E_EVIDENCE_DIR", None)
        if evidence_dir:
            env["MOKATA_E2E_EVIDENCE_DIR"] = evidence_dir
        p = subprocess.run([sys.executable, "-c", _RED_FLOW], cwd=_HERE, env=env,
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=300)
        tail = (p.stdout + p.stderr)[-3000:]
        self.assertEqual(0, p.returncode, tail)
        return p.stdout, tail

    def test_1_set_writes_trace_screenshot_console_requests_and_server_log(self):
        d = tempfile.mkdtemp(prefix="mk-e2e-evidence-")
        self.addCleanup(shutil.rmtree, d, True)
        out, tail = self._run_red_flow(d)
        self.assertIn("ERRORS=0 FAILURES=1 SKIPPED=0 KEPT=1", out, tail)
        (case,) = os.listdir(d)
        got = set(os.listdir(os.path.join(d, case)))
        self.assertLessEqual({"trace.zip", "screenshot.png", "console.txt", "requests.txt",
                              "server.log"}, got)
        with open(os.path.join(d, case, "console.txt"), encoding="utf-8") as fh:
            self.assertIn("planted for the evidence check", fh.read())
        with open(os.path.join(d, case, "server.log"), encoding="utf-8") as fh:
            self.assertTrue(fh.read().strip(), "the server's own output")

    def test_2_unset_keeps_nothing(self):
        out, tail = self._run_red_flow(None)
        self.assertIn("ERRORS=0 FAILURES=1 SKIPPED=0 KEPT=0", out, tail)
