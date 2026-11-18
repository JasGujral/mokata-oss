"""0.0.29 D6 `E2E-HARNESS-LOWS`: the IDE e2e harness's pure doors, graded without a browser.

Every class under tests/e2e_ide/ must be a harness flow (test_o9_ide_e2e_in_ci), so the checks that
need no page live here, in the unit suite, and run on every leg:

- no flow opens a browser context around `IdeFlow.new_context` (the door that pins the locale and
  attaches the listeners);
- the Firefox `/api/events` "interrupted while the page was loading" exemption holds ONLY while a
  harness-started navigation is in flight.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import ast
import os
import sys
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

_HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "e2e_ide")
sys.path.insert(0, _HERE)
try:
    import _harness as H  # noqa: E402  (imports without playwright: the flows skip, this needs none)
finally:
    sys.path.remove(_HERE)

_EVENTS_MSG = ("The connection to http://127.0.0.1:1/api/events was interrupted while the page "
               "was loading.")


def direct_contexts(source):
    """Line numbers in `source` that open a browser context or page around the harness door:
    `<anything>.browser.new_context(` / `.new_page(` and bare `browser.new_context(` / `.new_page(`."""
    out = []
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("new_context", "new_page")):
            recv = node.func.value
            name = recv.attr if isinstance(recv, ast.Attribute) else getattr(recv, "id", "")
            if name == "browser":
                out.append(node.lineno)
    return sorted(out)


class NoFlowBypassesTheContextDoor(unittest.TestCase):
    """A flow's own context comes from `IdeFlow.new_context` (locale pin + listeners), never from
    the browser directly: a direct one is how `test_image_diff`'s second context ran unpinned and
    unheard. FIRING CONTROL: the planted bypasses in the second test."""

    def test_no_flow_opens_a_context_around_the_door(self):
        found, flows = [], 0
        # CORPUS: THE WORKING TREE — the flows ci.yml's e2e jobs run from the checkout.
        for name in sorted(os.listdir(_HERE)):
            if name.startswith("test_") and name.endswith(".py"):
                flows += 1
                with open(os.path.join(_HERE, name), encoding="utf-8") as fh:
                    found += ["%s:%d" % (name, n) for n in direct_contexts(fh.read())]
        self.assertGreaterEqual(flows, 20, "the sweep read the flows")
        self.assertEqual([], found, "use self.new_context(...), the harness door")

    def test_the_sweep_convicts_a_planted_bypass_and_passes_the_door(self):
        self.assertEqual([2], direct_contexts("x = 1\nother = self.browser.new_context()\n"))
        self.assertEqual([1], direct_contexts("tab = cls.browser.new_page(locale='x')\n"))
        self.assertEqual([1], direct_contexts("tab = browser.new_context()\n"))
        self.assertEqual([], direct_contexts("other = self.new_context()\ntab = other.new_page()\n"))

    def test_the_harness_opens_its_own_page_through_the_door(self):
        with open(os.path.join(_HERE, "_harness.py"), encoding="utf-8") as fh:
            src = fh.read()
        # the ONE direct call is inside the door itself
        self.assertEqual(1, len(direct_contexts(src)))
        self.assertTrue("cls.browser.new_context(**kw)" in src, "the door opens the context")


class TheFirefoxFilterExemptsOnlyTheHarnessNavigation(unittest.TestCase):
    """The Firefox "interrupted while the page was loading" message about `/api/events` is the
    browser's own abort ONLY while the harness navigates; from a navigation the PAGE started it is
    a product bug and reds. FIRING CONTROL: drop the `_navigating > 0` term (row 2 reds)."""

    def _flow(self, navigating):
        return type("F", (H.IdeFlow,), {"_navigating": navigating})

    def test_exempt_while_a_harness_navigation_is_in_flight(self):
        with mock.patch.object(H, "BROWSER", "firefox"):
            self.assertTrue(self._flow(1)._expected_console(_EVENTS_MSG))

    def test_a_page_initiated_navigation_abort_is_not_exempt(self):
        with mock.patch.object(H, "BROWSER", "firefox"):
            self.assertFalse(self._flow(0)._expected_console(_EVENTS_MSG))

    def test_chromium_never_exempts_it(self):
        with mock.patch.object(H, "BROWSER", "chromium"):
            self.assertFalse(self._flow(1)._expected_console(_EVENTS_MSG))

    def test_another_route_is_never_exempt(self):
        with mock.patch.object(H, "BROWSER", "firefox"):
            self.assertFalse(self._flow(1)._expected_console(
                _EVENTS_MSG.replace("/api/events", "/api/tree")))


class TheNavigationCounterClosesBehindItself(unittest.TestCase):
    """`_counted` holds the Firefox exemption open ONLY while a harness navigation is in flight: up
    by one around the call, back down after it — returned or raised. A counter that never comes
    down leaves the exemption open for the rest of the flow (verifier D36 L-a). FIRING CONTROL:
    drop the `finally` decrement (both rows red)."""

    class _Page:
        def __init__(self):
            self.drained = 0

        def evaluate(self, _js):
            self.drained += 1

    def _flow(self):
        return type("F", (H.IdeFlow,), {"_navigating": 0})

    def test_counted_while_in_flight_and_closed_after_a_return(self):
        flow, page, seen = self._flow(), self._Page(), []
        run = flow._counted(page, lambda: seen.append(flow._navigating) or "done")
        self.assertEqual("done", run())
        self.assertEqual(([1], 0, 1), (seen, flow._navigating, page.drained))

    def test_closed_after_a_raise(self):
        flow = self._flow()

        def boom():
            raise RuntimeError("navigation failed")
        with self.assertRaises(RuntimeError):
            flow._counted(self._Page(), boom)()
        self.assertEqual(0, flow._navigating)


class TheTraceTakesNoDomSnapshots(unittest.TestCase):
    """CI traces every e2e flow (the evidence of a red run). DOM snapshots made it ~45% slower and
    turned `test_explorer_locator` test_16 red 3/3 on the mirror (probe 43416006107); without them
    0/3. The setting is pinned here, not by a comment (verifier D36 L-e)."""

    def test_the_harness_starts_tracing_with_snapshots_off(self):
        with open(os.path.join(_HERE, "_harness.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        starts = [n for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                  and n.func.attr == "start" and isinstance(n.func.value, ast.Attribute)
                  and n.func.value.attr == "tracing"]
        self.assertEqual(1, len(starts), "one place starts the trace")
        kw = {k.arg: k.value for k in starts[0].keywords}
        self.assertIn("snapshots", kw, "snapshots must be stated (Playwright's default varies)")
        self.assertIs(False, getattr(kw["snapshots"], "value", None))


if __name__ == "__main__":
    unittest.main()
