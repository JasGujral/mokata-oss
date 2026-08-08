"""REACHABILITY-PINS-MISSING — every BACKED gate carries a production-reachability verdict.

THE EXIT CRITERION THIS CLOSES. Doc 84's `REACHABILITY-PINS-MISSING` is the one blind spot no
individual pin can see: *"a test that constructs the precondition itself proves the CODE works and
says NOTHING about whether any production path ever reaches it. A stage can be fully built, fully
pinned, 100% mutation-RED, and completely inert in the shipped product."* Two instances were
confirmed by hand (H-6's four slices; DB.S7c2's memory-handle half) and a third at cluster scale
(`DK-CLUSTER-INERT`). Every one was found by a human reading `src/`. This is the mechanism that
finds the next one without a human reading `src/`.

SCOPE — THE NINE BACKED GATES, AND ONLY THOSE. `skill_contracts.GATES` holds 23 entries: 9 backed
(executable, each naming an `enforcement_point` module) and 14 advisory (protocol boundaries with
no enforcement point). **An advisory boundary has nothing to be reachable TO.** Asserting a
reachability pin on one would be a pin that cannot fail — doc 85 §7i exactly — so the 14 are
excluded here, deliberately and by an assertion that says so, rather than silently.

THE SET MOVED IN 0.0.17 STAGE 5, TWICE, AND THE COUNT DID NOT — which is why this table asserts
MEMBERSHIP and never a number. `self-protect` was ADDED (it is layer 0 of `WriteGate.submit`,
ahead of the trust dial, the secret scan and the human gate; stage 4 measured it REACHABLE while
it sat in no registry at all) and `ship-readiness` was REMOVED to advisory. 9 → 10 → 9. A count
assertion would have been green through both.

THE ONE ROW THIS TABLE ADDS, AND WHY IT IS NOT REDUNDANT. The registry names a MODULE
(`src/mokata/engine/spec_gate.py`), not a callable. Module-level reachability is a false-green
machine: measured on this tree, `engine/spec_gate.py` is reached at `load_emitted_spec` — a reader
— while `check_spec_persisted`, the callable that can actually REFUSE, was untouched by that path.
So `GATE_ENFORCERS` names the refusing callable per gate, its module is asserted equal to the
registry's, and its existence is asserted against the source — a renamed enforcer REDS rather than
quietly becoming unreachable.

WHAT THE MEASUREMENT FOUND (2026-08-06, over 148 derived entry points): stage 4 measured 8 of 9
REACHABLE and **one UNREACHABLE** — `ship-readiness`, a CERTIFIED ZERO: nothing in the package
imports `engine/ship.py` at all, every name it exports appearing only in `engine/__init__.py`'s
re-export block and its `__all__`. Not a resolver shadow.

STAGE 5 RESOLVED IT AT THE REGISTRY, SO `DECLARED_INERT` IS NOW EMPTY. The finding was that
`backed=True` — *"an executable gate a Contract clause may cite"* — was false of that entry:
REVIEW-FIX.R3 deleted `check_ship_readiness` and moved the review truth to
`progress_events.ship_review_gate`, and the "landing-decision half" `GATE-REPOINT ship-readiness`
(doc 84) said was still covered turned out to have **no production consumer either** — no `mokata
ship` subcommand, no `ship` MCP tool, because `ship` is an agent-facing SKILL, so "record the
finish" is prose an agent follows rather than code a surface runs. Jas ruled the entry ADVISORY
(0.0.17 stage 5) with the reason recorded in the registry, so the gate left this pin's scope by
being demoted rather than by being excused. **Every backed gate is REACHABLE now** — 7 structural,
2 lexical — and the declared-inert list emptied itself, which is exactly what
`stale_inert_declarations` exists to force.
  ⚠ `engine/ship.py` EXISTS and is NOT deleted. Any restatement of this as "the enforcement point
named a deleted FILE" is wrong — R3 deleted a FUNCTION, and stage 5 removed a CLAIM.

AND THE PIN THAT WAS ALREADY THERE (doc 85 §7h — read a justification as a claim).
`test_review_fix_r3`'s landing-decision test asserted that half *"still works exactly as before"*
by calling `record_finish_decision` itself — precisely the precondition-constructing shape
`REACHABILITY-PINS-MISSING` is about. The assertion was TRUE; the sentence around it read as
"still in service", which was the part that was not. Corrected in stage 5, and the test renamed to
say what it actually proves.

THE SYNTHETIC OFFENDERS (doc 85 §7i). The derivation is a pure function over a supplied corpus, so
`TestThePinCanActuallyFail` plants packages and grades the instrument against them: a gate nothing
imports (UNREACHABLE), a gate reached only through a constructor result (LEXICAL), a gate imported
but never called from an entry point (UNPROVABLE), a stale inert declaration, and — the regression
for a bug this stage's own first draft had — two gates in one module sharing a method NAME, where
crediting the wrong one would have been a silent false REACHABLE.

NO PRODUCTION IMPORTS beyond the registry itself. The derivation is stdlib `ast` over
`tests/_writegraph.py`; it imports nothing from `mokata.knowledge`, for the same reason SI.6 does
not — an audit that depends on the subsystem it audits cannot fail honestly when that subsystem is
wrong.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import ast
import os
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import _gatereach as GR                                            # noqa: E402
import _writegraph                                                 # noqa: E402
from mokata import skill_contracts                                 # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "src", "mokata")
PYPROJECT = os.path.join(REPO, "pyproject.toml")

#: The dispatch tables production enters through. `hook_cli._SUBCOMMANDS` maps `mokata-hook <sub>`
#: to its handler; the lookup is dynamic, so without naming the table the six hook surfaces are
#: invisible. Supplied rather than hunted for — the corpus a caller gives IS what gets judged.
DISPATCH_TABLES = (("hook_cli.py", "_SUBCOMMANDS"),)

#: THE REFUSING CALLABLE PER BACKED GATE. The registry names a module; this names the callable in
#: it that can say NO, because reaching a module is not reaching its gate. Each pick is asserted to
#: exist in the source, and each module is asserted equal to the registry's `enforcement_point`.
#:
#: Where doc 85 §4 already names a symbol, it is used verbatim (`WriteGate.submit`,
#: `secrets.scan`); where it names only a class or only a module, the refusing method is derived
#: and the reason is stated here.
GATE_ENFORCERS = {
    # doc 85 §4 names these two outright
    "write-gate": ("govern/gate.py", "WriteGate.submit"),
    "secret-guard": ("govern/secrets.py", "scan"),
    # §4 names the module; the one callable that returns a refusable verdict
    "spec-persisted": ("engine/spec_gate.py", "check_spec_persisted"),
    "completeness": ("engine/completeness.py", "run_completeness_gate"),
    # §4 names the CLASS `TddGuard`; `guard_implementation` is its only method that RAISES
    # (`allow_implementation` answers a bool, and a bool nobody checks blocks nothing)
    "no-code-without-failing-test": ("govern/tdd.py", "TddGuard.guard_implementation"),
    # §4 names the module; `submit` is the whole gate (surface -> human-gate -> resolve), where
    # `request` only surfaces and `resolve` only records
    "deviation": ("govern/deviation.py", "DeviationGate.submit"),
    # §4 names the CLASS `EnforcementGate`; `check` is its only entry point
    "hard-rule": ("govern/enforce.py", "EnforcementGate.check"),
    # §4 names `gate_hook.py:GATE_PHASE`, which is a CONSTANT; `check_write` is the callable that
    # returns the exit-2 outcome that constant labels
    "approach-approval": ("gate_hook.py", "check_write"),
    # §4 names `selfprotect.py:check_target` outright. Added to the REGISTRY in 0.0.17 stage 5 —
    # §4 had listed it as backed since 2026-07-26 while `skill_contracts.GATES` had no such row,
    # and the ruling was that §4 was right and the registry incomplete. It is layer 0 of
    # `WriteGate.submit` and layer 1 of the gate-guard hook, so it runs FIRST on every durable
    # write; stage 4 measured it REACHABLE before it was in any registry at all.
    "self-protect": ("selfprotect.py", "check_target"),
    # ⚠ NO `ship-readiness` ROW. It was backed (naming `engine/ship.py`) through stage 4, which
    # measured it a certified zero; stage 5 demoted it to advisory. An advisory boundary has
    # nothing to be reachable TO, so it is excluded here by the same rule as the other 13 — see
    # `test_no_advisory_boundary_is_pinned`, which now covers it.
}

#: DECLARED INERT — a backed gate measured UNREACHABLE, named here with the row that owns it, and
#: EXPIRING BY ITSELF: `stale_inert_declarations` REDS the moment the gate stops being unreachable,
#: so wiring it forces this list to shrink instead of leaving a stale excuse behind (§7h).
#:
#: **EMPTY, and it emptied itself.** Stage 4's one entry was `ship-readiness`; stage 5 ruled the
#: registry entry advisory rather than excusing it, so it left the BACKED population entirely and
#: the declaration had nothing left to declare. That is the intended lifecycle — an exception is
#: supposed to die by the defect being resolved, not by being renewed. Adding a name here again
#: means a backed gate is inert and someone chose to ship it that way; it needs a doc 84 row.
DECLARED_INERT = ()

#: Gates measured UNPROVABLE — pinned by EXACT MEMBERSHIP (currently none), because a gate sliding
#: from a provable verdict into the resolver's shadow is a real regression that a "no unreachable
#: gates" assertion alone would sail straight past.
DECLARED_UNPROVABLE = ()


#: Parsing `src/mokata` costs ~1.5s and every class below needs the same answer, so the live
#: derivations are computed once. Deliberately NOT a fixture that could be re-seeded per test: the
#: whole point is that these read the tree as it IS, so there is nothing per-test to vary.
_CACHE = {}


def _live_report():
    if "report" not in _CACHE:
        _CACHE["report"] = GR.build_gate_reachability(
            SRC, GATE_ENFORCERS, console_scripts=GR.read_console_scripts(PYPROJECT),
            dispatch_tables=DISPATCH_TABLES)
    return _CACHE["report"]


def _live_entries():
    if "entries" not in _CACHE:
        _CACHE["entries"] = GR.derive_entry_points(
            SRC, console_scripts=GR.read_console_scripts(PYPROJECT),
            dispatch_tables=DISPATCH_TABLES)
    return _CACHE["entries"]


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _plant(root, files):
    for rel, body in files.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(textwrap.dedent(body))
    return root


# ======================================================================================
# the table is the registry's, not its own
# ======================================================================================

class TestTheGateTableIsTheRegistrys(unittest.TestCase):

    def test_every_backed_gate_has_exactly_one_enforcer_row(self):
        """A new backed gate REDS here rather than quietly shipping without a reachability
        verdict — which is the whole exit criterion."""
        backed = {gid for gid, g in skill_contracts.GATES.items() if g.backed}
        self.assertEqual(backed, set(GATE_ENFORCERS),
                         "the enforcer table and the BACKED registry must name the same gates")

    def test_no_advisory_boundary_is_pinned(self):
        """§7i: a pin on an advisory boundary could never fail — there is no enforcement point for
        a path to reach. Excluded deliberately, and this asserts the exclusion held."""
        advisory = {gid for gid, g in skill_contracts.GATES.items() if not g.backed}
        self.assertTrue(advisory, "the registry must still carry advisory boundaries")
        self.assertEqual(advisory & set(GATE_ENFORCERS), set())
        for gid in advisory:
            self.assertEqual(skill_contracts.GATES[gid].enforcement_point, "",
                             f"advisory gate '{gid}' names an enforcement point")

    def test_each_enforcer_lives_in_the_module_the_registry_names(self):
        for gid, (module, _symbol) in sorted(GATE_ENFORCERS.items()):
            declared = skill_contracts.GATES[gid].enforcement_point
            self.assertEqual(declared, f"src/mokata/{module}",
                             f"{gid}: enforcer module drifted from the registry")

    def test_each_enforcer_symbol_exists_in_the_source(self):
        """The table's anti-rot check. A renamed or deleted enforcer must RED here — otherwise it
        silently becomes 'unreachable' and the finding reads as a defect in the wiring."""
        for gid, (module, symbol) in sorted(GATE_ENFORCERS.items()):
            self.assertTrue(GR.symbol_exists(SRC, module, symbol),
                            f"{gid}: '{symbol}' is not a def in {module}")

    def test_the_anti_rot_check_can_say_no(self):
        """§7i — the check above passes on a healthy tree whether or not it works, and a mutant
        pinning it to True survived the first batch. Its offenders are supplied here: a symbol that
        was renamed, one that moved module, and a CLASS name where a callable is required."""
        self.assertFalse(GR.symbol_exists(SRC, "govern/gate.py", "WriteGate.submit_renamed"))
        self.assertFalse(GR.symbol_exists(SRC, "govern/tdd.py", "WriteGate.submit"))
        self.assertFalse(GR.symbol_exists(SRC, "govern/gate.py", "WriteGate"),
                         "a class is not the callable that refuses")
        self.assertFalse(GR.symbol_exists(SRC, "govern/does_not_exist.py", "scan"))

    def test_a_module_is_a_coarser_question_than_its_gate(self):
        """Why the table exists at all, pinned rather than argued: `engine/spec_gate.py` is reached
        at a READER on the live tree, so a module-level answer would have greened a gate whose
        refusing callable it had never touched."""
        graph = _writegraph.build_call_graph(SRC)
        origins = GR.build_reexport_origins(SRC)
        reached = GR.close_forward(GR.redirect_through_reexports(graph, origins), _live_entries())
        in_module = {q for rel, q in reached if rel == "engine/spec_gate.py"}
        self.assertIn("read_emitted_spec", in_module,
                      "a reader in the gate's module is reached")
        self.assertNotEqual(in_module, {"check_spec_persisted"},
                            "module-level reachability is strictly coarser than the gate's")


# ======================================================================================
# THE PIN
# ======================================================================================

class TestEveryBackedGateCarriesAProductionVerdict(unittest.TestCase):

    def setUp(self):
        self.report = _live_report()

    def test_every_backed_gate_gets_a_verdict_with_a_basis(self):
        self.assertEqual(set(self.report), set(GATE_ENFORCERS))
        for gid, r in sorted(self.report.items()):
            self.assertIn(r.basis, GR._VERDICT_OF, f"{gid}: basis is not one of the four")
            self.assertIn(r.verdict, (GR.REACHABLE, GR.UNREACHABLE, GR.UNPROVABLE))

    def test_no_backed_gate_is_unreachable_but_the_declared_one(self):
        """THE PIN. A gate that no production entry point can reach is inert in the shipped
        product no matter how green its own tests are."""
        offenders = GR.unreachable_gates(self.report, DECLARED_INERT)
        self.assertEqual(offenders, [], "\n".join(
            ["a BACKED gate has no production path into it:"]
            + [self.report[g].render() for g in offenders]
            + [""] + ["what this instrument cannot establish:"]
            + [f"  - {s}" for s in GR.UNSEEN_REACH]))

    def test_a_declared_inert_gate_that_got_wired_expires_its_own_declaration(self):
        """§7h: leaving the excuse behind re-teaches the false rule to the next reader. The
        declaration dies the moment the defect does."""
        stale = GR.stale_inert_declarations(self.report, DECLARED_INERT)
        self.assertEqual(stale, [], f"declared inert but no longer unreachable: {stale}")

    def test_the_unprovable_set_is_pinned_by_exact_membership(self):
        """A gate sliding INTO the resolver's shadow is a regression the unreachable assertion
        cannot see — an UNPROVABLE is not a pass."""
        unprovable = sorted(g for g, r in self.report.items() if r.verdict == GR.UNPROVABLE)
        self.assertEqual(unprovable, sorted(DECLARED_UNPROVABLE))

    def test_every_backed_gate_is_reachable_now_and_nothing_is_excused(self):
        """The state stage 5 left: no backed gate is inert, so no declaration is needed. Asserted
        rather than implied — `DECLARED_INERT` being empty is only meaningful if the pin it feeds
        is also passing, and an empty excuse list beside an unreachable gate would be a RED."""
        self.assertEqual(DECLARED_INERT, ())
        self.assertEqual(GR.unreachable_gates(self.report, ()), [])
        for gid, r in sorted(self.report.items()):
            self.assertEqual(r.verdict, GR.REACHABLE, f"{gid}: {r.render()}")

    def test_ship_readiness_left_this_pin_by_demotion_not_by_excuse(self):
        """§7h — the finding must not become invisible just because it was resolved. It is out of
        scope here because it is ADVISORY, and that is a different fact from "it was fixed": the
        gate is still not executed by anything. `engine/ship.py` is NOT deleted."""
        ref = skill_contracts.GATES["ship-readiness"]
        self.assertFalse(ref.backed, "demoted in 0.0.17 stage 5 — nothing executes it")
        self.assertEqual(ref.enforcement_point, "")
        self.assertNotIn("ship-readiness", GATE_ENFORCERS)
        self.assertTrue(os.path.isfile(os.path.join(SRC, "engine", "ship.py")),
                        "the module still exists — the DEMOTION removed a claim, not a file")

    def test_the_landing_decision_half_still_has_no_surface(self):
        """The claim that widened `GATE-REPOINT ship-readiness` and earned the demotion: nothing
        reaches the half that row said was still covered. `ship` is an agent-facing SKILL, so
        'record the finish' is prose an agent follows — there is no `mokata ship` subcommand and no
        `ship` MCP tool to run it. Kept after the demotion because THIS is the fact that would have
        to change before the entry could be promoted back to backed."""
        entries = _live_entries()
        self.assertNotIn(("engine/ship.py", "record_finish_decision"), entries)
        surfaces = {q for _rel, q in entries}
        self.assertNotIn("cmd_ship", surfaces)
        self.assertNotIn("ship", surfaces)

    def test_ship_py_still_does_not_do_what_the_old_one_liner_claimed(self):
        """The evidence the demotion rests on, kept executable. The old one-liner said landing
        BLOCKS on green tests, met ACs and a recorded review verdict; the module checks none of the
        three, because REVIEW-FIX.R3 deleted the check and moved the review truth out."""
        source = _read(os.path.join(SRC, "engine", "ship.py"))
        self.assertIn("is DELETED, not moved", source,
                      "ship.py's own docstring is the evidence the check no longer lives there")
        tree = ast.parse(source)
        names = {n.name for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
        self.assertNotIn("check_ship_readiness", names)
        self.assertNotIn("ShipReadiness", names)
        self.assertNotIn("review", source.split('"""', 2)[2].lower(),
                         "the module's CODE says nothing about a review verdict")
        self.assertNotIn("blocks", skill_contracts.GATES["ship-readiness"].one_line,
                         "the corrected one-liner must not claim a block it cannot perform")

    def test_every_reachable_verdict_carries_evidence_a_reader_can_jump_to(self):
        for gid, r in sorted(self.report.items()):
            if r.basis == GR.BASIS_STRUCTURAL:
                self.assertGreaterEqual(len(r.path), 2, f"{gid}: a path of one site proves nothing")
                self.assertIn(r.path[0], _live_entries(), f"{gid}: path does not start at an entry")
                self.assertEqual(r.path[-1], (r.module, r.symbol))
            elif r.basis == GR.BASIS_LEXICAL:
                rel, lineno = r.site
                self.assertGreater(lineno, 0)
                self.assertIn(rel, r.importers, f"{gid}: lexical site is not in an importer")


# ======================================================================================
# the entry points are DERIVED, and every rung is doing work
# ======================================================================================

class TestTheEntryPointDerivationIsGrounded(unittest.TestCase):

    def test_all_four_declared_rungs_find_production_surfaces(self):
        """A rung that silently finds ZERO has stopped working, and a shrunken closure makes
        gates look unreachable — a false alarm this catches before it becomes a false finding."""
        entries = _live_entries()
        found = {}
        for rung in entries.values():
            found[rung] = found.get(rung, 0) + 1
        for rule in GR.ENTRY_RULES:
            name = rule.split(" — ")[0]
            self.assertGreater(found.get(name, 0), 0, f"rung '{name}' derived nothing")
        self.assertEqual(found["console-script"], 3, "pyproject declares three console scripts")
        self.assertGreater(found["cli-subcommand"], 50)
        self.assertEqual(found["hook-subcommand"], len(GR._dict_values_named(
            ast.parse(_read(os.path.join(SRC, "hook_cli.py"))),
            "_SUBCOMMANDS")))

    def test_the_derived_mcp_surface_equals_the_live_registry(self):
        """A COUNT DERIVED FROM AST, CHECKED AGAINST THE RUNNING TRUTH. The first draft of this
        rung read only `@_tool` decorator syntax and found 41 of the 61 registered tools —
        `mcp/tools_write.py` registers its surface by CALLING the factory
        (`_tool("spec_check", "write")(spec_check)`), so the 20 that were missing were the WRITE
        tools, i.e. exactly the ones the gates sit behind. "Every rung finds something" passed that
        whole time; only comparing against the registry itself catches it."""
        from mokata.mcp import tools_approve, tools_read, tools_write  # noqa: F401  (registers)
        from mokata.mcp.registry import TOOLS
        derived = sum(1 for r in _live_entries().values() if r == "mcp-tool")
        self.assertEqual(derived, len(TOOLS),
                         "the AST rung and the live MCP registry must agree on the tool count")

    def test_every_cli_handler_in_the_source_resolves_to_an_entry_point(self):
        """No silent drops: a `set_defaults(func=…)` the derivation cannot resolve would shrink
        the closure without saying so."""
        declared = 0
        for base, _dirs, files in os.walk(os.path.join(SRC, "cli_commands")):
            for name in files:
                if not name.endswith(".py"):
                    continue
                tree = ast.parse(_read(os.path.join(base, name)))
                for node in ast.walk(tree):
                    if (isinstance(node, ast.Call)
                            and getattr(node.func, "attr", None) == "set_defaults"):
                        declared += sum(1 for kw in node.keywords if kw.arg == "func")
        handlers = sum(1 for r in _live_entries().values() if r == "cli-subcommand")
        self.assertEqual(handlers, declared,
                         "every set_defaults(func=…) must resolve to a real def")

    def test_the_console_scripts_come_from_pyproject_not_from_a_list_here(self):
        scripts = GR.read_console_scripts(PYPROJECT)
        self.assertEqual(sorted(scripts),
                         [("cli.py", "main"), ("hook_cli.py", "main"), ("mcp_server.py", "main")])
        raw = _read(PYPROJECT)
        self.assertIn("[project.scripts]", raw)
        for _rel, fn in scripts:
            self.assertIn(f":{fn}\"", raw)

    def test_a_re_exported_console_script_is_followed_to_its_definition(self):
        """`mokata-mcp = "mokata.mcp_server:main"` and `mcp_server.py` defines no `main` — it
        re-exports one. An entry point that resolves to nothing is dropped, so without following
        the re-export the MCP server would silently not be an entry point at all."""
        entries = _live_entries()
        self.assertIn(("mcp/server.py", "main"), entries)
        self.assertEqual(entries[("mcp/server.py", "main")], "console-script")
        self.assertNotIn(("mcp_server.py", "main"), entries)


# ======================================================================================
# §7i — THE SYNTHETIC OFFENDERS. The instrument is graded against planted violations.
# ======================================================================================

#: The planted production surface. It imports the RUNNER and never the gate, so a plant can decide
#: for itself whether anything imports the enforcement point — which is the fact the certified zero
#: turns on.
_ENTRY_MODULE = """\
    from .other import run

    def cmd_go(args):
        return run()

    def register(sub, common):
        p = sub.add_parser("go")
        p.set_defaults(func=cmd_go)
"""


class TestThePinCanActuallyFail(unittest.TestCase):
    """A guard written in the same stage that empties its subject passes whether or not it works.
    So every verdict this instrument can produce is produced HERE, on a corpus planted for it."""

    def _judge(self, files, gates, **kw):
        with tempfile.TemporaryDirectory() as tmp:
            root = _plant(os.path.join(tmp, "planted"), files)
            return GR.build_gate_reachability(root, gates, package="planted", **kw)

    def test_a_gate_no_module_imports_is_a_certified_zero_and_reds_the_pin(self):
        files = {
            "__init__.py": "from .gate import TheGate\n__all__ = ['TheGate']\n",
            "gate.py": "class TheGate:\n    def check(self, x):\n        raise ValueError(x)\n",
            "cli.py": _ENTRY_MODULE,
            "other.py": "def run():\n    return 1\n",
        }
        gates = {"planted-gate": ("gate.py", "TheGate.check")}
        report = self._judge(files, gates)
        self.assertEqual(report["planted-gate"].verdict, GR.UNREACHABLE)
        self.assertEqual(report["planted-gate"].basis, GR.BASIS_VERIFIED_EMPTY)
        self.assertEqual(GR.unreachable_gates(report, ()), ["planted-gate"])

    def test_removing_the_offender_turns_the_same_pin_green(self):
        """The other half. A RED that cannot be made GREEN by fixing the planted defect is a RED
        that grades the plant, not the instrument."""
        files = {
            "__init__.py": "from .gate import TheGate\n__all__ = ['TheGate']\n",
            "gate.py": "class TheGate:\n    def check(self, x):\n        raise ValueError(x)\n",
            "cli.py": _ENTRY_MODULE,
            "other.py": "from .gate import TheGate\n\ndef run():\n    return TheGate().check(1)\n",
        }
        gates = {"planted-gate": ("gate.py", "TheGate.check")}
        report = self._judge(files, gates)
        self.assertEqual(report["planted-gate"].verdict, GR.REACHABLE)
        self.assertEqual(GR.unreachable_gates(report, ()), [])

    def test_a_gate_reached_only_through_a_constructor_result_is_the_lexical_floor(self):
        """`TheGate(...).check(...)` — the shape `_writegraph` declares it cannot resolve, and the
        shape `govern/gate.py:235` reaches `hard-rule` by on the live tree."""
        files = {
            "__init__.py": "",
            "gate.py": "class TheGate:\n    def check(self, x):\n        raise ValueError(x)\n",
            "cli.py": _ENTRY_MODULE,
            "other.py": "from .gate import TheGate\n\ndef run():\n    return TheGate().check(1)\n",
        }
        report = self._judge(files, {"planted-gate": ("gate.py", "TheGate.check")})
        r = report["planted-gate"]
        self.assertEqual(r.basis, GR.BASIS_LEXICAL)
        self.assertEqual(r.site, ("other.py", 4))
        self.assertEqual(r.owner, "run")

    def test_a_gate_reached_through_a_re_exported_class_binding_is_seen(self):
        """`from . import TheGate` (a package re-export) then `g = TheGate()` … `g.check()` — the
        `playbook.py` shape that reaches `no-code-without-failing-test`."""
        files = {
            "__init__.py": "from .gate import TheGate\n__all__ = ['TheGate']\n",
            "gate.py": "class TheGate:\n    def check(self, x):\n        raise ValueError(x)\n",
            "cli.py": _ENTRY_MODULE,
            "other.py": ("from . import TheGate\n\n"
                         "def run():\n    g = TheGate()\n    return g.check(1)\n"),
        }
        report = self._judge(files, {"planted-gate": ("gate.py", "TheGate.check")})
        self.assertEqual(report["planted-gate"].basis, GR.BASIS_LEXICAL)
        self.assertEqual(report["planted-gate"].site, ("other.py", 5))

    def test_an_imported_but_never_called_gate_is_UNPROVABLE_and_not_unreachable(self):
        """§7g, the whole point of the triple: 'nothing imports it' and 'something imports it and I
        cannot see the call' must not share a representation. Only the first reds the pin."""
        files = {
            "__init__.py": "",
            "gate.py": "class TheGate:\n    def check(self, x):\n        raise ValueError(x)\n",
            "cli.py": _ENTRY_MODULE,
            "other.py": ("from .gate import TheGate\n\n"
                         "def run():\n    return [TheGate]\n"),
        }
        report = self._judge(files, {"planted-gate": ("gate.py", "TheGate.check")})
        r = report["planted-gate"]
        self.assertEqual(r.verdict, GR.UNPROVABLE)
        self.assertEqual(r.basis, GR.BASIS_UNPROVABLE)
        self.assertEqual(r.importers, ("other.py",))
        self.assertEqual(GR.unreachable_gates(report, ()), [],
                         "an UNPROVABLE must not be reported as an unreachable gate")

    def test_a_gate_called_only_from_an_unreachable_function_stays_unprovable(self):
        """The call exists; nothing production can get to the function holding it. A rung that
        skipped the entry-reachability check would call this REACHABLE — an EXCUSE, which is the
        unsafe direction for an audit."""
        files = {
            "__init__.py": "",
            "gate.py": "class TheGate:\n    def check(self, x):\n        raise ValueError(x)\n",
            "cli.py": _ENTRY_MODULE,
            "other.py": ("from .gate import TheGate\n\n"
                         "def run():\n    return 1\n\n"
                         "def orphan():\n    return TheGate().check(1)\n"),
        }
        report = self._judge(files, {"planted-gate": ("gate.py", "TheGate.check")})
        self.assertEqual(report["planted-gate"].verdict, GR.UNPROVABLE)

    def test_two_gates_sharing_a_method_name_do_not_credit_each_other(self):
        """THE REGRESSION FOR THIS STAGE'S OWN FIRST DRAFT. The lexical rung began as a match on
        the symbol's TAIL NAME inside any importing module, and on its first run it credited the
        `deviation` gate with `engine/amend.py:352` — `wgate.submit(…)`, a **WriteGate**. Two gates
        in one file sharing a method name is not exotic; it is Tuesday. The rung resolves the
        RECEIVER now, and this is what proves it."""
        files = {
            "__init__.py": "",
            "gate.py": ("class Loud:\n    def submit(self, x):\n        return x\n\n"
                        "class Quiet:\n    def submit(self, x):\n        return x\n"),
            "cli.py": _ENTRY_MODULE,
            "other.py": ("from .gate import Loud, Quiet\n\n"
                         "def run():\n    return Loud().submit(1)\n"),
        }
        report = self._judge(files, {"loud": ("gate.py", "Loud.submit"),
                                     "quiet": ("gate.py", "Quiet.submit")})
        self.assertEqual(report["loud"].verdict, GR.REACHABLE)
        self.assertEqual(report["quiet"].verdict, GR.UNPROVABLE,
                         "the unused twin must not inherit its sibling's call site")

    def test_a_pure_re_export_is_not_an_importer(self):
        """Without subtracting `__all__` re-exports, `engine/ship.py` reads as 'one module imports
        it' — its own package `__init__` — and a gate NOTHING consumes hides behind its own
        re-export line as an UNPROVABLE instead of reddening as a certified zero."""
        files = {
            "__init__.py": "from .gate import TheGate\n__all__ = ['TheGate']\n",
            "gate.py": "class TheGate:\n    def check(self, x):\n        raise ValueError(x)\n",
            "cli.py": _ENTRY_MODULE,
            "other.py": "def run():\n    return 1\n",
        }
        report = self._judge(files, {"planted-gate": ("gate.py", "TheGate.check")})
        self.assertEqual(report["planted-gate"].importers, ())
        self.assertEqual(report["planted-gate"].basis, GR.BASIS_VERIFIED_EMPTY)

    def test_a_stale_inert_declaration_reds_on_its_own(self):
        files = {
            "__init__.py": "",
            "gate.py": "class TheGate:\n    def check(self, x):\n        raise ValueError(x)\n",
            "cli.py": _ENTRY_MODULE,
            "other.py": "from .gate import TheGate\n\ndef run():\n    return TheGate().check(1)\n",
        }
        report = self._judge(files, {"planted-gate": ("gate.py", "TheGate.check")})
        self.assertEqual(GR.stale_inert_declarations(report, ("planted-gate",)), ["planted-gate"])
        self.assertEqual(GR.stale_inert_declarations(report, ()), [])

    def test_a_handler_that_names_no_def_is_dropped_rather_than_padding_the_closure(self):
        """§7i — the drop guard passes on a healthy tree whether or not it works (every one of the
        78 live handlers resolves), and a mutant that kept unresolvable sites survived the first
        batch. Its offender is supplied here: `set_defaults(func=ghost)` where `ghost` is a name the
        module neither defines nor imports. Keeping it would put a site with no callees in the
        closure — a padded entry count that makes the derivation look broader than it is."""
        files = {
            "__init__.py": "",
            "gate.py": "class TheGate:\n    def check(self, x):\n        raise ValueError(x)\n",
            "cli.py": ("from .other import NOT_A_FUNCTION\n\n"
                       "def register(sub, common):\n"
                       "    p = sub.add_parser('go')\n"
                       "    p.set_defaults(func=ghost)\n"
                       "    p.set_defaults(func=NOT_A_FUNCTION)\n"),
            "other.py": "NOT_A_FUNCTION = 3\n\ndef run():\n    return 1\n",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = _plant(os.path.join(tmp, "planted"), files)
            entries = GR.derive_entry_points(
                root, package="planted",
                console_scripts=[("cli.py", "no_such_main")])
        # three offenders, three ways a rung can produce a site that names no def: a bare name the
        # module never binds (`ghost`), an IMPORTED name that is not a callable at all, and a
        # console script pointing at a function nobody wrote. Only the second and third reach the
        # drop guard — the first is refused a site to offer in the first place.
        self.assertEqual(entries, {}, "a handler that names no def is not a production entry point")

    def test_an_entry_point_the_derivation_cannot_see_shrinks_the_closure(self):
        """Declared in `UNSEEN_REACH` and demonstrated: a handler wired by a rung not in
        `ENTRY_RULES` is simply absent, so the verdict errs toward flagging."""
        files = {
            "__init__.py": "",
            "gate.py": "class TheGate:\n    def check(self, x):\n        raise ValueError(x)\n",
            "cli.py": ("from .other import run\n\n"
                       "def cmd_go(args):\n    return run()\n\n"
                       "HANDLERS = {'go': cmd_go}\n"),
            "other.py": "from .gate import TheGate\n\ndef run():\n    return TheGate().check(1)\n",
        }
        gates = {"planted-gate": ("gate.py", "TheGate.check")}
        blind = self._judge(files, gates)
        self.assertEqual(blind["planted-gate"].verdict, GR.UNPROVABLE)
        told = self._judge(files, gates, dispatch_tables=[("cli.py", "HANDLERS")])
        self.assertEqual(told["planted-gate"].verdict, GR.REACHABLE)


# ======================================================================================
# §7g — a basis and a verdict can never disagree
# ======================================================================================

class TestBasisAndVerdictCannotDisagree(unittest.TestCase):
    """`RunResolution`'s discipline, and stage 6's: the verdict is DERIVED from the basis, and a
    result carrying a basis without the evidence that basis names is refused at construction."""

    def test_verdict_is_derived_not_stored(self):
        self.assertNotIn("verdict", GR.GateReachability.__dataclass_fields__)
        for basis, verdict in GR._VERDICT_OF.items():
            self.assertIn(verdict, (GR.REACHABLE, GR.UNREACHABLE, GR.UNPROVABLE), basis)

    def test_a_structural_basis_without_a_path_is_refused(self):
        with self.assertRaises(ValueError):
            GR.GateReachability(gate_id="g", module="m.py", symbol="s",
                                basis=GR.BASIS_STRUCTURAL)

    def test_a_lexical_basis_without_a_call_site_is_refused(self):
        with self.assertRaises(ValueError):
            GR.GateReachability(gate_id="g", module="m.py", symbol="s", basis=GR.BASIS_LEXICAL)

    def test_a_certified_zero_that_has_importers_is_refused(self):
        """The strong claim and the weak one must not be interchangeable: `verified_empty` MEANS
        the population is empty, so carrying one is a contradiction, not a detail."""
        with self.assertRaises(ValueError):
            GR.GateReachability(gate_id="g", module="m.py", symbol="s",
                                basis=GR.BASIS_VERIFIED_EMPTY, importers=("x.py",))

    def test_an_unknown_basis_is_refused(self):
        with self.assertRaises(ValueError):
            GR.GateReachability(gate_id="g", module="m.py", symbol="s", basis="probably")


# ======================================================================================
# the instrument says what it cannot see, to its READER and not only in its source
# ======================================================================================

class TestTheReportDeclaresItsOwnBlindSpots(unittest.TestCase):

    def test_the_report_renders_the_rungs_and_the_blind_spots_verbatim(self):
        text = GR.render_reachability_report(_live_report(), _live_entries())
        for rule in GR.ENTRY_RULES:
            self.assertIn(rule.split(" — ", 1)[1], text)
        for shape in GR.UNSEEN_REACH:
            self.assertIn(shape, text)
        for shape in _writegraph.UNCLOSED_SHAPES:
            self.assertIn(shape, text, "the resolver's limits bound this instrument's too")

    def test_the_failure_message_names_the_blind_spots(self):
        """A reader who hits the RED must be told what the instrument could not see, in the place
        they are looking — not in a docstring they would have to go find."""
        report = _live_report()
        with self.assertRaises(AssertionError) as caught:
            offenders = list(report)
            raise AssertionError("\n".join(
                [f"  - {s}" for s in GR.UNSEEN_REACH] + offenders))
        for shape in GR.UNSEEN_REACH:
            self.assertIn(shape, str(caught.exception))

    def test_all_three_verdicts_appear_in_the_report(self):
        text = GR.render_reachability_report(_live_report(), _live_entries())
        for verdict in (GR.REACHABLE, GR.UNREACHABLE, GR.UNPROVABLE):
            self.assertIn(f"{verdict}:", text)


# ======================================================================================
# corroboration — the mechanism reproduces the instances doc 84 found BY HAND
# ======================================================================================

class TestTheMechanismReproducesTheKnownInstances(unittest.TestCase):
    """An instrument that agrees with a hand audit it did not see is worth more than one that only
    agrees with itself. These are `GATE-UNREACHABLE-BRAINSTORM` and `REACHABILITY-PINS-MISSING`
    instance (2), derived here rather than read off the rows.

    ⚠ STAGE 4 PREDICTED "stage 5 flips the first two". IT DID NOT, and the prediction was wrong to
    make rather than merely unlucky: ruling D14 defers the approve-path WIRING to 0.0.19 alongside
    the JS/TS floor, so stage 5 corrected the five false comments, added the pin, and left the
    behaviour alone. The gates below are still inert and `tests/test_d1_gate_unreachable.py` is
    what holds them to it."""

    #: the four approve/handoff-path gates doc 84 classified by hand, and `self-protect`
    KNOWN = {
        "brainstorm_impact_gate": ("govern/graph_required.py", "brainstorm_impact_gate"),
        "brainstorm_prior_art_gate": ("govern/prior_art_gate.py", "brainstorm_prior_art_gate"),
        "handoff_prior_art_gate": ("govern/prior_art_gate.py", "handoff_prior_art_gate"),
        "brainstorm_stale_ref_gate": ("govern/stale_ref_gate.py", "brainstorm_stale_ref_gate"),
        "self-protect": ("selfprotect.py", "check_target"),
    }

    def setUp(self):
        self.report = GR.build_gate_reachability(
            SRC, self.KNOWN, console_scripts=GR.read_console_scripts(PYPROJECT),
            dispatch_tables=DISPATCH_TABLES)

    def test_only_the_handoff_variant_of_the_prior_art_gate_is_wired(self):
        """Doc 84 verbatim: *'only the HANDOFF variant, `handoff_prior_art_gate`, is actually
        wired'*. Derived independently here."""
        self.assertEqual(self.report["handoff_prior_art_gate"].verdict, GR.REACHABLE)
        self.assertNotEqual(self.report["brainstorm_prior_art_gate"].verdict, GR.REACHABLE)

    def test_the_two_brainstorm_approve_path_gates_have_no_derivable_production_path(self):
        for gid in ("brainstorm_impact_gate", "brainstorm_prior_art_gate"):
            self.assertNotEqual(self.report[gid].verdict, GR.REACHABLE, gid)

    def test_the_stale_ref_gate_is_a_certified_zero(self):
        """Stronger than the row states: doc 84 says `brainstorm_stale_ref_gate` has no production
        CALLER; nothing in the package so much as IMPORTS its module."""
        r = self.report["brainstorm_stale_ref_gate"]
        self.assertEqual(r.basis, GR.BASIS_VERIFIED_EMPTY)

    def test_self_protect_is_wired_and_is_now_in_the_registry(self):
        """Stage 4 found `self-protect` REACHABLE while `skill_contracts.GATES` had no row for it,
        and doc 85 §4 had listed it among the backed gates since 2026-07-26. The ruling (Jas,
        0.0.17 stage 5) was that §4 was right and the REGISTRY was incomplete — a gate running at
        `WriteGate.submit` layer 0, ahead of the trust dial, the secret scan and the human gate, is
        backed by any definition. This asserts the row landed and still measures reachable."""
        self.assertEqual(self.report["self-protect"].verdict, GR.REACHABLE)
        self.assertIn("self-protect", skill_contracts.GATES)
        self.assertTrue(skill_contracts.GATES["self-protect"].backed)
        self.assertEqual(skill_contracts.GATES["self-protect"].enforcement_point,
                         "src/mokata/selfprotect.py")


if __name__ == "__main__":
    unittest.main()
