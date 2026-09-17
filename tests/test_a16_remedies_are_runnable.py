"""Every removal remedy that names `reconfigure --remove` is RUN against a real repo.

0.0.20 stage 11b, `RECONFIGURE-REMOVE-CANNOT-UNWIRE-A-REMOVED-PROVIDER`. Stage 11's report named
this the unbuilt half and said why it is the valuable one: *"the guard is the valuable half and it
is a runnability check, not a text check — the row exists because the remedy was asserted to be
NAMED and never RUN."*

⛔ WHAT WENT WRONG ONCE AND MUST NOT GO WRONG SILENTLY AGAIN
-------------------------------------------------------------
`mokata reconfigure --remove` picked its candidates out of `OPTIONAL_INTEGRATIONS`. A removal stage
MUST take the removed id out of that tuple or the wizard goes on selling it — **and doing so turned
the one command the removal record points at into a no-op that still exits 0.** Nothing noticed,
because every check was about the TEXT: the record named a command, the command existed, the flag
parsed. The user got *"no changes — your setup already matches"* against a manifest that plainly
still named the provider.

§7i — THE INSTANCE IS ALREADY FIXED, SO THE PLANTS DO THE GRADING
------------------------------------------------------------------
`_removable_tools`' second clause landed at 0.0.18 stage 14, so the live assertion passes today and
would pass if the clause were the only thing keeping it true. `TheOldBehaviourWouldBeCaught` plants
a channel that is neither offered NOR unknown-to-the-catalog and requires `NO_OP`, and
`_a16_remedy_runnability_mutants.sh` removes the clause outright and requires RED.

§7e — DRIVE THE BOUNDARY, NOT THE READER
-----------------------------------------
⚠ **The first cut of this harness reported the remedy UNRUNNABLE and it was WRONG.** It planted the
channel into a capability `fallback` only; the schema refuses an unknown tool, `Manifest.load`
raised, and `plan_reconfigure` never reached the code under test. A real user's manifest was written
by an older mokata that shipped the channel, so it carries the id in the **`tools` table too** — and
it validates. `TheFixtureIsAManifestARealUserWouldHave` keeps that distinction graded rather than
remembered, because the false finding was one commit away from being filed.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

import _remedy_runnability as rr

from mokata import deprecation as dep
from mokata import onboarding as OB
from mokata.init import init_repo
from mokata.manifest import ManifestError
from mokata.onboarding import OPTIONAL_INTEGRATIONS
from mokata.profiles import TOOL_CATALOG


def _repo(d, profile="standard"):
    init_repo(root=d, profile=profile, assume_yes=True, out=lambda _: None)
    return d


def _manifest(root):
    with open(os.path.join(root, ".mokata", "manifest.json"), "r", encoding="utf-8") as fh:
        return json.load(fh)


class TheClaimsAreDERIVEDFromTheRecords(unittest.TestCase):
    """What is checked comes from the registry, never from a list in this file (§7j)."""

    def test_at_least_one_record_claims_reconfigure_remove(self):
        """⛔ Anti-vacuity. With no claims, every assertion below is green having asked nothing."""
        self.assertTrue(rr.claims(dep.REMOVED),
                        "no removal record names `mokata reconfigure --remove <id>`, so this whole "
                        "module grades nothing — which is exactly how the original defect survived")

    def test_a_remedy_that_names_no_such_command_is_NOT_CLAIMED_not_absent(self):
        state, target = rr.reconfigure_remove_claim(dep.REMOVED["obsidian"])
        self.assertEqual(state, rr.NOT_CLAIMED)
        self.assertIsNone(target)

    def test_the_claim_reads_the_REMEDY_and_not_the_record_key(self):
        """A record pointing `--remove` at a different id is a defect to SEE, not to paper over."""
        class _Rec(object):
            remedy = "Clear it with `mokata reconfigure --remove somethingelse`, then reindex."
        state, target = rr.reconfigure_remove_claim(_Rec())
        self.assertEqual((state, target), (rr.CLAIMED, "somethingelse"))

    def test_a_remedy_with_a_PLACEHOLDER_is_not_treated_as_runnable(self):
        class _Rec(object):
            remedy = "Run `mokata reconfigure --remove <id>` for whichever one you wired."
        self.assertEqual(rr.reconfigure_remove_claim(_Rec())[0], rr.NOT_CLAIMED)

    def test_commands_are_read_from_BACKTICKS_and_prose_is_not_a_command(self):
        class _Rec(object):
            remedy = "You could mokata reconfigure --remove neo4j by hand, or `mokata index`."
        self.assertEqual(rr.remedy_commands(_Rec()), ("mokata index",))


class TheRemedyIsRUN(unittest.TestCase):
    """The live assertion: every claimed remedy actually unwires its channel."""

    def test_every_claimed_remedy_is_RUNNABLE(self):
        for channel, target in sorted(rr.claims(dep.REMOVED).items()):
            with self.subTest(channel=channel):
                with tempfile.TemporaryDirectory() as d:
                    _repo(d)
                    rr.plant_removed_channel(d, target, need="code_graph")
                    result = rr.run_remedy(d, target, OB.plan_reconfigure)
                    self.assertEqual(result.state, rr.RUNNABLE, result.render())

    def test_the_planted_channel_really_was_in_the_chain_first(self):
        """⛔ Without this, a fixture that planted nothing would make every removal 'work'."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            rr.plant_removed_channel(d, "neo4j", need="code_graph")
            chain = _manifest(d)["capabilities"]["code_graph"]["fallback"]
            self.assertIn("neo4j", chain)

    def test_a_channel_that_was_NEVER_wired_is_a_NO_OP_and_says_so(self):
        """The honest no-op — and it must be distinguishable from the dishonest one by evidence."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            result = rr.run_remedy(d, "neo4j", OB.plan_reconfigure)
            self.assertEqual(result.state, rr.NO_OP)
            self.assertIn("NO-OP", result.render())


class AddAndRemoveShareOneCandidateSet(unittest.TestCase):
    """🔴 THE LIVE BUG THIS HARNESS FOUND, and it is not a removed provider.

    `--add` picked its candidates out of `TOOL_CATALOG`; `--remove` picked its own out of
    `OPTIONAL_INTEGRATIONS`. **Anything the catalog KNOWS and the wizard does not OFFER could
    therefore be wired and never unwired** — measured on `pgvector`, which was never removed from
    anything:

        mokata reconfigure --add pgvector      -> ['pgvector', 'sqlite']
        mokata reconfigure --remove pgvector   -> "no changes - your setup already matches"
                                               -> ['pgvector', 'sqlite']

    ⭐ THE ROW PREDICTED *"the next removal repeats it."* It was already broken for a provider no
    removal ever touched, because the two commands did not share a candidate set. The repair is one
    derived rule — *removable = wired and not a `DEFAULT_PROFILE` floor* — and these tests hold all
    four states it has to cover.
    """

    def _wire(self, root, tool):
        """Put `tool` into its own capability's chain, the way a manifest that already has it does.

        ⚠ The `tools` row goes in for EVERY tool, catalog member or not — the schema validates a
        chain entry against the manifest's OWN tools table, not against `TOOL_CATALOG`, so a
        catalog member with no row there is just as invalid as a removed one. A first cut of this
        helper wrote the row only for non-catalog tools and every offered integration came back
        `UNRUNNABLE` — the fixture failing, reported as the subject failing (§7e again, four
        classes down from where it is documented).
        """
        need = TOOL_CATALOG[tool].get("provides")
        rr.plant_removed_channel(root, tool, need=need)
        return need

    def test_a_tool_the_catalog_KNOWS_and_the_wizard_does_NOT_OFFER_is_removable(self):
        """⭐ THE REGRESSION TEST FOR THE BUG ITSELF. Derived, not spelled `pgvector`."""
        floors = OB._default_profile_floors()
        victims = [t for t in sorted(TOOL_CATALOG)
                   if t not in OPTIONAL_INTEGRATIONS and t not in floors]
        self.assertTrue(victims,
                        "no catalog tool is unoffered-and-not-a-floor, so the state that broke "
                        "`--remove` no longer exists to be graded — say so, do not delete this")
        for victim in victims:
            with self.subTest(tool=victim):
                with tempfile.TemporaryDirectory() as d:
                    _repo(d)
                    self._wire(d, victim)
                    result = rr.run_remedy(d, victim, OB.plan_reconfigure)
                    self.assertEqual(result.state, rr.RUNNABLE, result.render())

    def test_an_OFFERED_integration_is_still_removable(self):
        for tool in sorted(OPTIONAL_INTEGRATIONS):
            with self.subTest(tool=tool):
                with tempfile.TemporaryDirectory() as d:
                    _repo(d)
                    self._wire(d, tool)
                    result = rr.run_remedy(d, tool, OB.plan_reconfigure)
                    self.assertEqual(result.state, rr.RUNNABLE, result.render())

    def test_a_tool_the_catalog_NO_LONGER_KNOWS_is_still_removable(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            rr.plant_removed_channel(d, "neo4j", need="code_graph")
            self.assertEqual(rr.run_remedy(d, "neo4j", OB.plan_reconfigure).state, rr.RUNNABLE)

    def test_the_DEFAULT_PROFILE_floors_are_still_NO_OPS(self):
        """⛔ `--remove grep` must behave exactly as it always has. The fix widened one rule; it
        must not have widened it onto the fallbacks a chain ends in."""
        floors = OB._default_profile_floors()
        self.assertTrue(floors, "the floor set derived EMPTY, so every tool would be removable")
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            wired = {t for chain in _manifest(d)["capabilities"].values()
                     for t in (chain.get("fallback") or [])}
            graded = 0
            for tool in sorted(floors & wired):
                result = rr.run_remedy(d, tool, OB.plan_reconfigure)
                self.assertEqual(result.state, rr.NO_OP, result.render())
                graded += 1
            self.assertTrue(graded, "no floor was wired in this repo, so nothing was graded")

    def test_the_floor_set_is_DERIVED_from_the_profile_table_not_typed(self):
        from mokata.profiles import DEFAULT_PROFILE, PROFILES
        expected = {t for chain in PROFILES[DEFAULT_PROFILE]["capabilities"].values()
                    for t in chain}
        self.assertEqual(set(OB._default_profile_floors()), expected)

    def test_the_floors_come_from_the_DEFAULT_profile_not_the_CURRENT_one(self):
        """`full` wires `serena` and `code-review-graph` — opt-in providers a user must still be
        able to unwire. Keying on the current profile would make removability depend on which
        profile happened to be selected."""
        from mokata.profiles import PROFILES
        full = {t for chain in PROFILES["full"]["capabilities"].values() for t in chain}
        self.assertTrue(full - set(OB._default_profile_floors()),
                        "the `full` profile adds no tool beyond the floors, so this distinction "
                        "is not being exercised")
        for tool in sorted(full - set(OB._default_profile_floors())):
            with self.subTest(tool=tool):
                with tempfile.TemporaryDirectory() as d:
                    _repo(d, profile="full")
                    self.assertEqual(
                        rr.run_remedy(d, tool, OB.plan_reconfigure).state, rr.RUNNABLE)


class TheOldBehaviourWouldBeCaught(unittest.TestCase):
    """§7i. The repaired rule is what makes the live tests green; this proves it is load-bearing."""

    def test_NO_OP_and_UNRUNNABLE_do_NOT_share_a_representation(self):
        self.assertNotEqual(rr.NO_OP, rr.UNRUNNABLE)
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            no_op = rr.run_remedy(d, "neo4j", OB.plan_reconfigure)

            def _boom(root, **kw):
                raise ManifestError("planted")
            unrunnable = rr.run_remedy(d, "neo4j", _boom)
        self.assertEqual(no_op.state, rr.NO_OP)
        self.assertEqual(unrunnable.state, rr.UNRUNNABLE)
        self.assertIn("NOT a no-op and NOT a pass", unrunnable.render())


class TheFixtureIsAManifestARealUserWouldHave(unittest.TestCase):
    """§7e. The false finding this harness produced on its first cut, kept as a graded control."""

    def test_the_HALF_PLANT_is_refused_by_the_schema_and_reads_as_UNRUNNABLE(self):
        """⚠ This is the FALSE finding, preserved. A fallback entry with no `tools` row is not a
        manifest any mokata ever wrote, and grading the remedy against it says nothing about the
        remedy — it says the fixture is wrong."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            rr.plant_removed_channel(d, "neo4j", need="code_graph", tools_table=False)
            result = rr.run_remedy(d, "neo4j", OB.plan_reconfigure)
            self.assertEqual(result.state, rr.UNRUNNABLE, result.render())
            self.assertIn("unknown tool", str(result.error))

    def test_the_FULL_PLANT_validates_which_is_why_it_is_the_right_fixture(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            rr.plant_removed_channel(d, "neo4j", need="code_graph")
            from mokata.manifest import Manifest
            Manifest.load(os.path.join(d, ".mokata", "manifest.json"))   # must not raise

    def test_the_plant_refuses_a_capability_the_manifest_does_not_declare(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            with self.assertRaises(KeyError):
                rr.plant_removed_channel(d, "neo4j", need="no_such_capability")


if __name__ == "__main__":
    unittest.main()
