"""A capability chain committed before an embedded floor shipped is NOTICED and a fix is PROPOSED.

0.0.20 stage 11c, `WIRED-GRAPH-CHAIN-PREDATES-THE-AST-PROVIDER`. A `code_graph` chain written
before the embedded AST provider existed names external tools and the grep floor, and **nothing
re-offers the AST provider to a repo whose manifest was written earlier.** A user whose chain read
`['neo4j', 'ripgrep', 'grep']` lost Neo4j at 0.0.18 and landed on **grep**, while the AST floor —
no install, real call/import edges — sat right there unwired.

⭐ THE REMOVAL NOTICE CAN SAY *"the canonical graph already answers here"* AND BE TRUE ABOUT THE
PRODUCT AND FALSE ABOUT THAT REPO. That gap — between a correct message and a correct outcome — is
the whole row, and no amount of re-reading the notice closes it.

WHY THIS IS A DOCTOR CHECK AND NOT A FIX (P2)
----------------------------------------------
The row itself ruled the write out: *"re-writing a user's committed chain is a governed write and a
removal stage is the wrong place to introduce one."* ⛔ **Doctor NOTICES and PROPOSES; the human
gate does the rest.** `TheFindingProposesAndNeverWrites` pins that the manifest is byte-identical
after a diagnosis.

⚠ THE REMEDY IS GRADED BY EXECUTION, NOT BY SPELLING — the lesson stage 11b paid for. It is not
enough that the finding names `mokata reconfigure --add ast`; `TheProposedRemedyActuallyRUNS` runs
it against the very chain the finding fires on and requires the provider to land at the FRONT.

FOUR STATES, FOUR REPRESENTATIONS (§7g)
----------------------------------------
    WIRED            the chain already names every embedded floor for this capability
    MISSING          it omits one AND that one would ANSWER on this repo
    NOT_APPLICABLE   it omits one that would answer NOTHING here — a zero-Python repo
    UNDECIDABLE      the manifest or the probe was unreadable; the question was not asked

⛔ `NOT_APPLICABLE` and `UNDECIDABLE` both produce no warning, and folding them together is the
defect. One means *asked and there is nothing to say*; the other means *never asked*, and it emits
an `info` finding rather than silence — the same posture `diagnose` already takes for the rule tiers
(`rules-unverifiable`: *"Could not check" is a finding*).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import onboarding as OB
from mokata.config import Surface
from mokata.detect import Detector
from mokata.govern import doctor as D
from mokata.init import init_repo
from mokata.profiles import DEFAULT_PROFILE, PROFILES, TOOL_CATALOG


def _repo(chain=None, python=True, need="code_graph"):
    d = tempfile.mkdtemp()
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
    if chain is not None:
        path = os.path.join(d, ".mokata", "manifest.json")
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        data["capabilities"][need]["fallback"] = list(chain)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
    if python:
        with open(os.path.join(d, "module.py"), "w", encoding="utf-8") as fh:
            fh.write("def f():\n    return 1\n")
    return d


def _chain(root, need="code_graph"):
    with open(os.path.join(root, ".mokata", "manifest.json"), "r", encoding="utf-8") as fh:
        return json.load(fh)["capabilities"][need]["fallback"]


def _manifest_bytes(root):
    with open(os.path.join(root, ".mokata", "manifest.json"), "rb") as fh:
        return fh.read()


class TheFloorSetIsDERIVEDNotNamed(unittest.TestCase):
    """§7j. `ast` is what this row is about TODAY; the row is about the class."""

    def test_the_floors_are_the_BUILTIN_providers_of_that_capability(self):
        expected = tuple(t for t, e in sorted(TOOL_CATALOG.items())
                         if e["kind"] == "builtin" and e["provides"] == "code_graph")
        self.assertEqual(D._embedded_floors_for("code_graph"), expected)

    def test_a_builtin_of_ANOTHER_capability_is_not_a_floor_for_this_one(self):
        """The capability match is the second half of the rule and it is graded on its own."""
        others = {t for t, e in TOOL_CATALOG.items()
                  if e["kind"] == "builtin" and e["provides"] != "code_graph"}
        for tid in others:
            self.assertNotIn(tid, D._embedded_floors_for("code_graph"))

    def test_the_rule_is_ONE_property_and_not_two(self):
        """⭐ A property defended twice is a property no mutant can grade. The first cut read
        `DEFAULT_PROFILE`'s chain AND filtered by `kind`; swapping the profile to `full` came back
        GREEN, because `full` adds only non-builtins and the `kind` filter removes them again."""
        floors = set(D._embedded_floors_for("code_graph"))
        by_profile = {t for t in PROFILES[DEFAULT_PROFILE]["capabilities"]["code_graph"]
                      if TOOL_CATALOG[t]["kind"] == "builtin"}
        self.assertEqual(floors, by_profile,
                         "the two derivations disagree — which is fine, but then the docstring's "
                         "claim that the profile read added nothing is no longer true")

    def test_the_set_is_NOT_EMPTY_or_every_assertion_here_is_vacuous(self):
        self.assertTrue(D._embedded_floors_for("code_graph"))

    def test_a_provider_that_needs_an_INSTALL_is_never_a_floor(self):
        """⚠ THE `kind` FILTER IS WHAT KEEPS THIS FROM NAGGING. Without it a user who deliberately
        unwired `ripgrep` would be told their chain is behind the default, forever, about a choice
        they made on purpose. A builtin is not a choice — it was not on offer yet."""
        floors = D._embedded_floors_for("code_graph")
        for tid in floors:
            self.assertEqual(TOOL_CATALOG[tid]["kind"], "builtin")
        default = PROFILES[DEFAULT_PROFILE]["capabilities"]["code_graph"]
        non_builtin = [t for t in default if TOOL_CATALOG[t]["kind"] != "builtin"]
        self.assertTrue(non_builtin,
                        "the default chain is all builtins, so the filter is not being exercised")
        for tid in non_builtin:
            self.assertNotIn(tid, floors)

    def test_an_unknown_capability_has_no_floors_rather_than_raising(self):
        self.assertEqual(D._embedded_floors_for("no_such_capability"), ())


class TheFourStatesAreDistinguishable(unittest.TestCase):

    def test_a_fresh_repo_is_WIRED(self):
        surface = Surface.load(_repo())
        self.assertEqual(D.graph_floor_verdict(surface)[0], D.FLOOR_WIRED)
        self.assertEqual(D.graph_floor_findings(surface), [])

    def test_a_chain_that_predates_the_floor_is_MISSING_on_a_python_repo(self):
        surface = Surface.load(_repo(chain=["ripgrep", "grep"]))
        verdict, missing = D.graph_floor_verdict(surface)
        self.assertEqual(verdict, D.FLOOR_MISSING)
        self.assertIn("ast", missing)

    def test_THE_ROWS_OWN_REPO_the_neo4j_chain_that_lost_its_provider(self):
        """⭐ The exact repo the row was found on: `['neo4j', 'ripgrep', 'grep']` after 0.0.18."""
        surface = Surface.load(_repo(chain=["ripgrep", "grep"]))   # neo4j already resolved away
        findings = D.graph_floor_findings(surface)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].code, "graph-floor-unwired")
        self.assertEqual(findings[0].severity, "warning")

    def test_a_repo_with_NO_PYTHON_is_NOT_APPLICABLE_and_is_told_nothing(self):
        """⛔ Reporting a floor that would answer NOTHING is worse than silence: the user follows
        the advice, wires it, and gets the same grep results."""
        surface = Surface.load(_repo(chain=["ripgrep", "grep"], python=False))
        verdict, candidates = D.graph_floor_verdict(surface)
        self.assertEqual(verdict, D.FLOOR_NOT_APPLICABLE)
        self.assertEqual(candidates, ("ast",), "the candidate is still NAMED — it is not applicable,"
                                               " not undiscovered")
        self.assertEqual(D.graph_floor_findings(surface), [])

    def test_an_UNREADABLE_probe_is_UNDECIDABLE_and_emits_an_INFO_finding(self):
        """§7f. A check that could not run must not look like a check that passed."""
        class _Boom(object):
            def is_present(self, *_a, **_k):
                raise RuntimeError("planted probe failure")

        surface = Surface.load(_repo(chain=["ripgrep", "grep"]))
        verdict, _c = D.graph_floor_verdict(surface, detector=_Boom())
        self.assertEqual(verdict, D.FLOOR_UNDECIDABLE)
        findings = D.graph_floor_findings(surface, detector=_Boom())
        self.assertEqual([f.code for f in findings], ["graph-floor-unverifiable"])
        self.assertEqual(findings[0].severity, "info")
        self.assertIn("not a pass", findings[0].detail)

    def test_NOT_APPLICABLE_and_UNDECIDABLE_do_NOT_share_a_representation(self):
        class _Boom(object):
            def is_present(self, *_a, **_k):
                raise RuntimeError("planted")
        quiet = Surface.load(_repo(chain=["ripgrep", "grep"], python=False))
        broken = Surface.load(_repo(chain=["ripgrep", "grep"]))
        self.assertNotEqual(D.graph_floor_verdict(quiet)[0],
                            D.graph_floor_verdict(broken, detector=_Boom())[0])
        self.assertEqual(D.graph_floor_findings(quiet), [])
        self.assertTrue(D.graph_floor_findings(broken, detector=_Boom()),
                        "'asked and nothing to say' and 'never asked' both went silent")

    def test_a_capability_the_manifest_does_NOT_declare_is_NOT_APPLICABLE(self):
        surface = Surface.load(_repo())
        self.assertEqual(D.graph_floor_verdict(surface, need="no_such_capability")[0],
                         D.FLOOR_NOT_APPLICABLE)


class TheFindingProposesAndNeverWrites(unittest.TestCase):
    """P2. The chain is a file the user owns. Doctor is read-only, and this asserts it."""

    def test_diagnosing_leaves_the_manifest_BYTE_IDENTICAL(self):
        root = _repo(chain=["ripgrep", "grep"])
        before = _manifest_bytes(root)
        surface = Surface.load(root)
        self.assertTrue(D.graph_floor_findings(surface), "nothing fired, so nothing was proven")
        self.assertEqual(_manifest_bytes(root), before)
        self.assertEqual(_chain(root), ["ripgrep", "grep"])

    def test_the_finding_NAMES_the_command_and_says_it_asks_first(self):
        surface = Surface.load(_repo(chain=["ripgrep", "grep"]))
        detail = D.graph_floor_findings(surface)[0].detail
        self.assertIn("mokata reconfigure --add ast", detail)
        self.assertIn("asks before writing", detail)

    def test_the_finding_reaches_a_full_DIAGNOSE_run(self):
        """⛔ A check nothing calls grades nothing — REACHABILITY, filed six times in this
        programme."""
        report = D.diagnose(Surface.load(_repo(chain=["ripgrep", "grep"])))
        self.assertIn("graph-floor-unwired", [f.code for f in report.findings])

    def test_a_healthy_repo_adds_no_noise_to_DIAGNOSE(self):
        report = D.diagnose(Surface.load(_repo()))
        self.assertNotIn("graph-floor-unwired", [f.code for f in report.findings])
        self.assertNotIn("graph-floor-unverifiable", [f.code for f in report.findings])


class TheProposedRemedyActuallyRUNS(unittest.TestCase):
    """⚠ Stage 11b's lesson, applied one stage later: a remedy is graded by RUNNING it."""

    def test_the_named_command_wires_the_floor_at_the_FRONT_of_the_chain(self):
        root = _repo(chain=["ripgrep", "grep"])
        surface = Surface.load(root)
        _verdict, missing = D.graph_floor_verdict(surface)
        self.assertEqual(missing, ("ast",))

        OB.run_reconfigure(root, add=list(missing), assume_yes=True, out=lambda _m: None,
                           detector=Detector(root=root))

        self.assertEqual(_chain(root), ["ast", "ripgrep", "grep"],
                         "the proposed remedy ran and did not put the floor where it answers first")

    def test_and_the_finding_STOPS_firing_afterwards(self):
        """The other end of the loop: a remedy that runs but does not clear the finding would
        leave a user typing it forever."""
        root = _repo(chain=["ripgrep", "grep"])
        self.assertTrue(D.graph_floor_findings(Surface.load(root)))
        OB.run_reconfigure(root, add=["ast"], assume_yes=True, out=lambda _m: None,
                           detector=Detector(root=root))
        self.assertEqual(D.graph_floor_findings(Surface.load(root)), [])


if __name__ == "__main__":
    unittest.main()
