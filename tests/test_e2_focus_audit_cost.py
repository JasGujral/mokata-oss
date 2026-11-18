"""0.0.29 E2: the focus audit pays for what it grades, once - counted, never timed, no browser.

`test_themes.EveryFocusableSurfaceShowsAFocusRing` (0.0.29 lane E) re-graded the WHOLE screen on
every retry pass and took a pixel probe's "before" shot of every item before its verdict. The editor
tabs no arrow key reaches (IDE-FOCUS-RING-UA-AUTO, pinned expectedFailure) are unreached on every pass,
so three screens paid three times per theme, and most shots were thrown away: ~2 s became ~213 s per
browser and both ide-e2e jobs left their CI ceilings (mirror runs 43869782006, 43883056679,
43896526038). These flows drive the audit's own grade_current / grade_pass against a fake page and
COUNT what it does: verdicts per element, screenshots per verdict, reds kept and dropped. A count is
the guard, so a slow runner cannot red it and a fast one cannot hide the regression.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import sys
import unittest

import _support  # noqa: F401  (the interpreter preflight; test_a31 requires every module to import it)

# A UNIT test, not an e2e flow: every module under tests/e2e_ide/ must be harness flows that skip
# without a browser (test_o9_ide_e2e_in_ci), and this needs no browser at all. The audit module is
# imported from there the way test_d6_e2e_harness_doors imports the harness.
_HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "e2e_ide")
sys.path.insert(0, _HERE)
try:
    import test_themes as T  # noqa: E402  (the module, never its classes: discovery must not run them twice)
finally:
    sys.path.remove(_HERE)


class El:
    """One focusable element of the fake screen."""

    def __init__(self, name, ok=True, roving=False, painted=True, unreached_passes=0, stale_passes=0,
                 appears_on_pass=1):
        self.name, self.ok, self.roving, self.painted = name, ok, roving, painted
        self.unreached_passes, self.stale_passes, self.appears_on_pass = unreached_passes, stale_passes, appears_on_pass
        self.verdicts = 0
        self.reaches = 0


class FakePage:
    """Answers exactly the expressions the audit evaluates; counts verdicts and screenshots."""

    def __init__(self, els):
        self.els, self.done, self.pass_no, self.focused, self.cands = els, set(), 0, None, []
        self.shots = []                       # (element name, painted ring?) per screenshot

    def evaluate(self, expr, arg=None):
        if expr == T.FA_LIB:
            self.pass_no += 1
            return None
        if expr.startswith("window.__faDone = new WeakSet()"):
            self.done = set()
            return None
        if "requestAnimationFrame" in expr:
            return None
        if "__fa.enumerate" in expr:
            self.cands = [e for e in self.els if e.appears_on_pass <= self.pass_no]
            return {"stops": sum(not e.roving for e in self.cands), "roving": sum(e.roving for e in self.cands),
                    "excluded": [], "modal": "",
                    "list": [{"i": i, "name": e.name, "roving": e.roving, "tab": False, "hits": [e.name],
                              "done": id(e) in self.done} for i, e in enumerate(self.cands)]}
        e = self.cands[arg] if isinstance(arg, int) else None
        if "__fa.prep" in expr:
            self.focused = None
            return {"stale": True} if e.stale_passes >= self.pass_no else {"x": 10, "y": 10, "w": 20, "h": 10}
        if "__fa.rect" in expr:
            return {"x": 10, "y": 10, "w": 20, "h": 10}
        if "__fa.focusIt" in expr:
            self.focused = e
            return True
        if "__fa.verdict" in expr:
            e.verdicts += 1
            return {"ok": e.ok, "why": "%s: no ring in --focus" % e.name}
        if "__fa.mark" in expr:
            self.done.add(id(e))
            return None
        if "__fa.blur" in expr or "__fa.finish" in expr:
            self.focused = None
            return None
        if "__fa.graded" in expr:
            return sum(id(x) in self.done for x in self.cands)
        if "__fa.cover" in expr:
            return "box; at its centre: itself"
        if "__fa.restore" in expr:
            return True
        raise AssertionError("the fake page does not know %r" % expr)

    def screenshot(self, **kw):
        f = self.focused
        self.shots.append((f.name if f else None, bool(f and f.painted)))
        return b"ring" if f is not None and f.painted else b"plain"


def grade(els):
    """The real audit's grading code on the fake page; only the keyboard walk is the fake's. The
    subclass is made HERE, never at module level: a module-level TestCase subclass of the audit is
    itself discovered, and would run the whole browser audit a second time."""

    class Audit(T.EveryFocusableSurfaceShowsAFocusRing):
        def reach_by_arrows(self, i):
            e = self.page.cands[i]
            e.reaches += 1
            if e.unreached_passes >= self.page.pass_no:
                return "no arrow key reaches it"
            self.page.focused = e
            return ""

    a = Audit("test_4_the_palette")
    a.page = FakePage(els)
    a.page.audit_case = a
    return a.grade_current("screen", "dark"), a.page


class TheAuditPaysForWhatItGradesOnce(unittest.TestCase):

    def test_1_a_deterministic_unreachable_item_is_retried_alone(self):
        """An editor tab no arrow key ever reaches: three passes, and on the retries ONLY it is tried
        again - every other element is judged exactly once, and the red is said once."""
        others = [El("button#a"), El("button#b", ok=False), El("div.row[role=option]", roving=True)]
        tab = El("div.tab[role=tab]", roving=True, unreached_passes=99)
        r, page = grade(others + [tab])
        self.assertEqual(3, page.pass_no, "an unreached item still earns its three passes")
        self.assertEqual([1, 1, 1], [e.verdicts for e in others], "a judged element is never judged again")
        self.assertEqual(3, tab.reaches, "the unreached item is tried on every pass")
        self.assertEqual(1, sum("not reachable by keyboard" in b for b in r["bad"]), r["bad"])
        self.assertEqual(1, sum("button#b" in b for b in r["bad"]), "a pass-1 red survives the retries")
        self.assertEqual(3, r["graded"])

    def test_2_a_red_verdict_takes_no_screenshot_and_a_green_takes_two(self):
        reds = [El("button#r%d" % k, ok=False) for k in range(5)]
        greens = [El("button#g%d" % k) for k in range(3)]
        r, page = grade(reds + greens)
        self.assertEqual(1, page.pass_no)
        self.assertEqual([], [s for s in page.shots if s[0] and s[0].startswith("button#r")])
        self.assertEqual(6, len(page.shots), "two shots per green verdict, none per red: %s" % page.shots)
        self.assertEqual(3, r["probed"])
        self.assertEqual(5, len(r["bad"]))

    def test_3_the_probe_still_reds_a_green_verdict_that_paints_nothing(self):
        r, _ = grade([El("button#clipped", painted=False), El("button#fine")])
        self.assertEqual(1, len(r["bad"]), r["bad"])
        self.assertIn("paints no pixel", r["bad"][0])
        self.assertEqual(2, r["probed"])

    def test_4_the_probe_shoots_unfocused_then_focused(self):
        _, page = grade([El("button#g")])
        self.assertEqual([(None, False), ("button#g", True)], page.shots,
                         "the before shot has nothing focused, the after shot has the ring")

    def test_5_reached_on_a_retry_is_no_red(self):
        """A group still filling in on pass 1 and reachable on pass 2: no 'not reachable' red, and the
        rest of the screen is not judged a second time."""
        late = El("div.row[role=option]", roving=True, unreached_passes=1)
        other = El("button#a")
        r, page = grade([other, late])
        self.assertEqual(2, page.pass_no)
        self.assertEqual([], r["bad"])
        self.assertEqual((1, 1), (other.verdicts, late.verdicts))
        self.assertEqual(2, r["graded"])

    def test_6_a_shared_label_retries_only_the_node_that_was_not_reached(self):
        """Two editor tabs share a label; one is reached, one is not. The reached one is judged once;
        the label is no key for 'done' (the node is)."""
        a = El("div.tab[role=tab]", roving=True)
        b = El("div.tab[role=tab]", roving=True, unreached_passes=99)
        r, page = grade([a, b])
        self.assertEqual(1, a.verdicts)
        self.assertEqual(3, b.reaches)
        self.assertEqual(1, r["graded"])

    def test_7_an_element_that_appears_on_a_retry_is_graded(self):
        """A group that grew between passes: its new element is graded on the retry, not dropped."""
        late = El("div.tab[role=tab]", roving=True, unreached_passes=1)
        grown = El("button#new", ok=False, appears_on_pass=2)
        r, page = grade([El("button#a"), late, grown])
        self.assertEqual(1, grown.verdicts, "the element that appeared on the retry pass was graded")
        self.assertIn("button#new: no ring in --focus", r["bad"])
        self.assertEqual(3, r["graded"])

    def test_8_a_stale_item_is_retried_and_said_after_three_passes(self):
        gone = El("li.row", stale_passes=99)
        other = El("button#a")
        r, page = grade([other, gone])
        self.assertEqual(3, page.pass_no)
        self.assertEqual(1, other.verdicts)
        self.assertEqual(1, sum("three passes running" in b for b in r["bad"]), r["bad"])

    def test_9_every_theme_grades_the_screen_again(self):
        """'Done' is per screen-and-theme: the next theme's pass judges every element afresh (a
        ring can be right in dark and wrong in hc-light), never 'already done in dark'."""
        els = [El("button#a"), El("button#b", ok=False)]
        _, page = grade(els)
        a = page.audit_case
        r2 = a.grade_current("screen", "light")
        self.assertEqual([2, 2], [e.verdicts for e in els])
        self.assertEqual(2, r2["graded"])


if __name__ == "__main__":
    unittest.main()
