"""GATE-UNREACHABLE-BRAINSTORM (D1) — the approve-path gates are inert, and it is PINNED.

THE DEFECT. `BrainstormSession.approve` takes four optional gate verdicts — `graph_gate`,
`prior_art_gate`, `stale_ref_gate`, `code_anchor_gate` — and refuses the approval when one of them
REFUSES. Five comment sites across `src/` claimed the CLI approve gate enforces the first of those.
**No production path anywhere originates a value for ANY of the four**, so all four refusals are
dead code inside a function that runs on every brainstorm.

WHY A REACHABILITY VERDICT ALONE WOULD HAVE MISSED THIS, which is the point of this module.
Stage 4's `build_gate_reachability` measures `BrainstormSession.approve` **REACHABLE**, by a
resolved structural path from a CLI handler — correctly, because it IS the human gate the whole
phase turns on. A pin asserting "the approve gate is reachable" is GREEN and the defect sails past
it. The refusal is guarded by `if graph_gate is not None`, so what is dead is not the function but
the ARGUMENT PASS, and **a gate reached through an optional parameter is only as live as the calls
that feed it**. That is the general lesson, and `_gatereach.keyword_origination` is the mechanism:
the population of `kw=` passes, each with whether production reaches the def that holds it.

THE DECISIVE FACT, DERIVED RATHER THAN RESTATED. Doc 84 states D1 at its cheapest: *"the ONLY
`graph_gate=` argument pass anywhere in `src/` is `domains.py` — one internal function handing the
value to another internal function. No production path anywhere ORIGINATES a `graph_gate` value."*
This module derives that from the source instead of trusting the row, and the derivation is
anchored on the FACT, never the line number: doc 99 recorded the tools_spec claim at `:236` when
it was at `:238`, and this stage's own comment corrections moved the `domains.py` pass from `:252`
to `:269`. A pin that asserted a line would have been wrong twice in one release.

WHAT THE DERIVATION FOUND THAT THE ROWS DID NOT. D1 is filed about `graph_gate`. Measured here,
`prior_art_gate`, `stale_ref_gate` and `code_anchor_gate` have **zero** argument passes — not one,
where `graph_gate` at least has an inert one. So the D1 shape covers all four approve-path
parameters, and `graph_gate` is merely the one somebody wrote a row about. (`handoff_prior_art_gate`
is REACHABLE and is NOT one of these: it is the handoff-path variant, which IS wired — doc 84's
*"only the HANDOFF variant is wired"*, reproduced here independently.)

D14 STILL BINDS, SO THIS STAGE PINS AND DOES NOT WIRE. Doc 99 row 7b: the wiring moved to 0.0.19
alongside the JS/TS floor, because landing it sooner moves a TypeScript user's refusal from phase 7
to phase 1 two releases before they have a graph that can answer it. These assertions therefore pin
the INERT state, and every one is written to RED the moment 0.0.19 wires it — the §7h discipline:
a declaration that outlives its defect is a comment, so this expires by itself.

SCOPE — WHAT THIS DOES NOT RULE ON. `classify_from_impact` and `classify_session_domains`, which
hold the one inert pass between them, have no production caller at all. That is `DK-CLUSTER-INERT`
(doc 84): 13 of `domains.py`'s 16 top-level symbols are unreachable and every dead one is the
behavioural half of the domain framework. Doc 99 rules that stage 5 corrects the comments and
REPORTS — the wire-or-delete belongs with 0.0.18's SIMP deletion work, because "should the domain
framework be live?" is a product question. Pinned here as a measured fact, not resolved.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import _gatereach as GR                                            # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "src", "mokata")
PYPROJECT = os.path.join(REPO, "pyproject.toml")
DISPATCH_TABLES = (("hook_cli.py", "_SUBCOMMANDS"),)

#: The four optional gate verdicts `BrainstormSession.approve` consumes. Named here rather than
#: read off the signature on purpose: if a parameter is REMOVED, this list still asks about it and
#: the "zero passes" assertion keeps passing for the wrong reason — so `test_all_four_are_still
#: _parameters_of_approve` asserts the signature separately, and the two together mean a rename
#: cannot quietly empty the subject.
APPROVE_GATE_PARAMS = ("graph_gate", "prior_art_gate", "stale_ref_gate", "code_anchor_gate")

#: THE INERT PASS. `classify_session_domains` -> `classify_from_impact`, the only `graph_gate=`
#: anywhere in `src/`. Held as (file, enclosing def) — never a line number, which has already moved
#: twice (doc 99 cited `:252`; this stage's comment corrections pushed it to `:269`).
THE_ONLY_GRAPH_GATE_PASS = ("domains.py", "classify_session_domains")

_CACHE = {}


def _passes(keyword):
    if keyword not in _CACHE:
        _CACHE[keyword] = GR.keyword_origination(
            SRC, keyword, console_scripts=GR.read_console_scripts(PYPROJECT),
            dispatch_tables=DISPATCH_TABLES)
    return _CACHE[keyword]


def _plant(root, files):
    for rel, body in files.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(textwrap.dedent(body))
    return root


# ======================================================================================
# THE PIN — no production path originates a value for any approve-path gate
# ======================================================================================

class TestNoProductionPathOriginatesAnApproveGate(unittest.TestCase):

    def test_the_only_graph_gate_pass_is_one_dead_function_calling_another(self):
        """D1's decisive fact, derived. ONE pass in the whole package, and it is not reachable from
        any production entry point — so nothing ORIGINATES a `graph_gate`."""
        passes = _passes("graph_gate")
        self.assertEqual([(p.rel, p.owner) for p in passes], [THE_ONLY_GRAPH_GATE_PASS],
                         "the only `graph_gate=` pass in src/ must be the known inert one")
        self.assertFalse(passes[0].entry_reachable,
                         "the one pass is inside a function production cannot reach either")

    def test_the_other_three_approve_gates_have_no_pass_at_all(self):
        """WIDER THAN THE ROW. D1 is filed about `graph_gate`; these three have ZERO passes, so the
        same defect covers all four approve-path parameters."""
        for kw in ("prior_art_gate", "stale_ref_gate", "code_anchor_gate"):
            with self.subTest(parameter=kw):
                self.assertEqual(_passes(kw), [],
                                 f"'{kw}=' is never passed anywhere in src/")

    def test_not_one_approve_gate_parameter_is_fed_from_production(self):
        """THE PIN, stated once over all four: no entry-reachable call site supplies any of them.

        REDS WHEN 0.0.19 WIRES IT (D14), which is the intent — the declaration expires with the
        defect rather than teaching the next reader a false rule (§7h)."""
        live = [(kw, p.rel, p.lineno, p.owner)
                for kw in APPROVE_GATE_PARAMS for p in _passes(kw) if p.entry_reachable]
        self.assertEqual(live, [], "\n".join(
            ["an approve-path gate is fed from a production entry point — if this is the 0.0.19",
             "D14 wiring, that is the SUCCESS case: delete this pin and repoint it at the new",
             "surface rather than excusing it. Live passes:"]
            + [f"  {kw} at {rel}:{n} in {owner}" for kw, rel, n, owner in live]
            + [""] + ["what this instrument cannot establish:"]
            + [f"  - {s}" for s in GR.UNSEEN_REACH]))

    def test_all_four_are_still_parameters_of_approve(self):
        """The anti-rot half. Without this, RENAMING a parameter would empty the population above
        and every assertion would pass for the wrong reason — a pin that greens on the subject
        disappearing is the false-green this whole exit criterion is about."""
        import ast
        with open(os.path.join(SRC, "brainstorm.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        found = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "approve":
                found = {a.arg for a in node.args.kwonlyargs}
        self.assertIsNotNone(found, "BrainstormSession.approve not found")
        for kw in APPROVE_GATE_PARAMS:
            self.assertIn(kw, found, f"'{kw}' is no longer a parameter — repoint this pin")


# ======================================================================================
# the reachability question and the origination question are DIFFERENT questions
# ======================================================================================

class TestWhyAReachabilityVerdictAloneWouldMissThis(unittest.TestCase):
    """The justification for `keyword_origination` existing beside `build_gate_reachability`,
    pinned rather than argued — the §7h rule that a justification is a claim like any other."""

    def test_approve_itself_is_measured_reachable(self):
        """So "is the gate reachable" answers GREEN here while the refusal inside it is dead. If
        this ever goes UNREACHABLE the two instruments have stopped disagreeing and this module's
        reason for existing needs re-reading."""
        report = GR.build_gate_reachability(
            SRC, {"approve": ("brainstorm.py", "BrainstormSession.approve")},
            console_scripts=GR.read_console_scripts(PYPROJECT), dispatch_tables=DISPATCH_TABLES)
        self.assertEqual(report["approve"].verdict, GR.REACHABLE)

    def test_the_producer_of_the_verdict_is_not_reachable(self):
        """`brainstorm_impact_gate` is the consumer entry point that COMPUTES a `graph_gate`. It
        has no production caller — the other end of the same hole."""
        report = GR.build_gate_reachability(
            SRC, {"impact": ("govern/graph_required.py", "brainstorm_impact_gate")},
            console_scripts=GR.read_console_scripts(PYPROJECT), dispatch_tables=DISPATCH_TABLES)
        self.assertNotEqual(report["impact"].verdict, GR.REACHABLE)

    def test_the_handoff_prior_art_variant_IS_wired_and_is_not_one_of_these(self):
        """Doc 84 verbatim: *'only the HANDOFF variant is wired'*. Reproduced independently, and
        asserted so the four-inert claim above cannot be read as 'prior art is entirely dead'."""
        report = GR.build_gate_reachability(
            SRC, {"handoff": ("govern/prior_art_gate.py", "handoff_prior_art_gate")},
            console_scripts=GR.read_console_scripts(PYPROJECT), dispatch_tables=DISPATCH_TABLES)
        self.assertEqual(report["handoff"].verdict, GR.REACHABLE)


# ======================================================================================
# §7i — THE PLANTED OFFENDERS. The derivation is graded against a corpus built for it.
# ======================================================================================

_ENTRY = """\
    from .other import run

    def cmd_go(args):
        return run()

    def register(sub, common):
        p = sub.add_parser("go")
        p.set_defaults(func=cmd_go)
"""


class TestTheOriginationDerivationCanActuallyFail(unittest.TestCase):
    """A guard written in the stage that empties its subject passes whether or not it works. The
    live tree has ONE inert pass and zero live ones, so every assertion above is a green that
    proves nothing on its own — these plant each outcome instead."""

    def _passes(self, files, keyword, **kw):
        with tempfile.TemporaryDirectory() as tmp:
            root = _plant(os.path.join(tmp, "planted"), files)
            return GR.keyword_origination(root, keyword, package="planted", **kw)

    def test_a_pass_from_an_entry_reachable_function_is_seen_as_live(self):
        """THE OFFENDER THE PIN EXISTS FOR — this is what 0.0.19's wiring will look like, and the
        pin must RED on it. Without this the "no live passes" assertion is untested."""
        files = {
            "__init__.py": "",
            "gate.py": "def approve(x, *, graph_gate=None):\n    return graph_gate\n",
            "cli.py": _ENTRY,
            "other.py": ("from .gate import approve\n\n"
                         "def run():\n    return approve(1, graph_gate=2)\n"),
        }
        passes = self._passes(files, "graph_gate")
        self.assertEqual(len(passes), 1)
        self.assertTrue(passes[0].entry_reachable, "a pass inside a reached def is LIVE")
        self.assertEqual((passes[0].rel, passes[0].owner), ("other.py", "run"))

    def test_a_pass_from_an_unreachable_function_is_seen_but_not_live(self):
        """The live tree's actual shape: the pass exists, nothing production can reach it. The two
        must be distinguishable, or the pin cannot tell 'inert' from 'wired'."""
        files = {
            "__init__.py": "",
            "gate.py": "def approve(x, *, graph_gate=None):\n    return graph_gate\n",
            "cli.py": _ENTRY,
            "other.py": ("from .gate import approve\n\n"
                         "def run():\n    return 1\n\n"
                         "def orphan():\n    return approve(1, graph_gate=2)\n"),
        }
        passes = self._passes(files, "graph_gate")
        self.assertEqual(len(passes), 1)
        self.assertFalse(passes[0].entry_reachable)
        self.assertEqual(passes[0].owner, "orphan")

    def test_a_keyword_nobody_passes_is_an_empty_population(self):
        files = {
            "__init__.py": "",
            "gate.py": "def approve(x, *, graph_gate=None):\n    return graph_gate\n",
            "cli.py": _ENTRY,
            "other.py": "def run():\n    return 1\n",
        }
        self.assertEqual(self._passes(files, "graph_gate"), [])

    def test_a_different_keyword_on_the_same_call_is_not_counted(self):
        """The derivation keys on the PARAMETER NAME, so a sibling keyword at the same call site
        must not credit it — otherwise `approve(..., prior_art_gate=x)` would green `graph_gate`."""
        files = {
            "__init__.py": "",
            "gate.py": ("def approve(x, *, graph_gate=None, prior_art_gate=None):\n"
                        "    return graph_gate or prior_art_gate\n"),
            "cli.py": _ENTRY,
            "other.py": ("from .gate import approve\n\n"
                         "def run():\n    return approve(1, prior_art_gate=2)\n"),
        }
        self.assertEqual(self._passes(files, "graph_gate"), [])
        self.assertEqual(len(self._passes(files, "prior_art_gate")), 1)

    def test_a_module_level_pass_has_no_owner_and_is_never_live(self):
        """A call at import time is owned by no def. `owner is None` must not be mistaken for
        reachable — `entry_reachable` requires an owner that is IN the closure."""
        files = {
            "__init__.py": "",
            "gate.py": "def approve(x, *, graph_gate=None):\n    return graph_gate\n",
            "cli.py": _ENTRY,
            "other.py": ("from .gate import approve\n\n"
                         "AT_IMPORT = approve(1, graph_gate=2)\n\n"
                         "def run():\n    return 1\n"),
        }
        passes = self._passes(files, "graph_gate")
        self.assertEqual(len(passes), 1)
        self.assertIsNone(passes[0].owner)
        self.assertFalse(passes[0].entry_reachable)

    def test_the_population_is_returned_in_source_order(self):
        """`ast.walk` is BREADTH-FIRST, not source order: a method inside a `class` is visited
        AFTER a module-level function defined below it (measured — walk yields lines [7, 4] on the
        corpus below). So the sort is load-bearing, and the assertions above index `passes[0]`.

        Added when a mutant that deleted the sort SURVIVED the first batch: every corpus in this
        module had at most one pass, so ordering could not matter anywhere it was checked."""
        files = {
            "__init__.py": "",
            "gate.py": "def approve(x, *, graph_gate=None):\n    return graph_gate\n",
            "cli.py": _ENTRY,
            "other.py": ("from .gate import approve\n\n"
                         "class A:\n"
                         "    def m(self):\n"
                         "        return approve(1, graph_gate=1)\n\n"
                         "def run():\n"
                         "    return approve(1, graph_gate=2)\n"),
        }
        passes = self._passes(files, "graph_gate")
        self.assertEqual([p.lineno for p in passes], [5, 8],
                         "returned in SOURCE order, not ast.walk's breadth-first order")
        self.assertEqual([p.owner for p in passes], ["A.m", "run"])

    def test_kwargs_forwarding_is_declared_unseen_and_really_is_unseen(self):
        """`UNSEEN_REACH` says a `**kwargs` forward is invisible. Demonstrated rather than
        asserted in prose — it under-counts, which is the safe direction here, and this is what
        makes the limitation checkable instead of taken on trust."""
        files = {
            "__init__.py": "",
            "gate.py": "def approve(x, *, graph_gate=None):\n    return graph_gate\n",
            "cli.py": _ENTRY,
            "other.py": ("from .gate import approve\n\n"
                         "def run():\n    opts = {'graph_gate': 2}\n"
                         "    return approve(1, **opts)\n"),
        }
        self.assertEqual(self._passes(files, "graph_gate"), [],
                         "a **kwargs forward is invisible — declared in UNSEEN_REACH")
        self.assertTrue(any("**kwargs" in s for s in GR.UNSEEN_REACH),
                        "and the limitation must be DECLARED, not only true")


if __name__ == "__main__":
    unittest.main()
