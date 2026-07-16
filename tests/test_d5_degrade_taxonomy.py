"""D5 — DEGRADE_NOTICE EVERYWHERE + the MokataError taxonomy (0.0.13, "Correctness & Trust").

The bug: failures vanished into `except Exception: pass`. The user's feature silently didn't work,
doctor saw nothing, the ledger recorded nothing. D1+D2's "loud but lying" schema degrade was ONE
instance of a whole class; this stage sweeps the class.

Doc 74 counted "168 `except Exception` / ~25 bare `pass`". Re-counted at the head of this stage:
**210 `except Exception`, 2 `except BaseException`, ZERO bare `except:`** — the codebase grew, and
the bare-except count was simply wrong (there were none). The honest numbers, and the disposition
of every one of them, are in `test_d5_sweep_register.py`.

What this file proves:

  * TAXONOMY — every exception class mokata defines subclasses `MokataError`, and the ones that
    mean "a capability degraded to a floor" subclass `DegradedCapability` carrying the right
    `failure_class` from `degrade.py`'s vocabulary. One vocabulary, not two. The AST sweep means a
    NEW exception class that forgets the base FAILS CI — the SI.4/SI.6 guard pattern, because the
    reason 210 sites existed is that nothing structurally noticed them.

  * NO BEHAVIOURAL CHANGE — re-parenting kept every secondary base (`LockTimeout` is still a
    `TimeoutError`, `SkillNotFound` still a `KeyError`), so every existing `except` clause in the
    codebase and in user code still catches exactly what it caught before.

  * THE NOTICE — a capability degrade is announced ONCE per subsystem, classed, and carries a
    remediation that is TRUE for that class (the D1 lesson: a schema failure must never be told to
    run `mokata sync`).

  * THE REGISTRY — the notices are remembered, so "what silently degraded?" has one answer.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import ast
import io
import os
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import degrade
from mokata.degrade import (FAILURE_CORRUPT, FAILURE_DOC_SCHEMA, FAILURE_ENGINE, FAILURE_LOCAL_IO,
                            FAILURE_SCHEMA, FAILURE_UNREACHABLE, FAILURE_UNSET, FAILURE_WAL,
                            CapabilityDegradeNotice, emitted_notices, note_degraded,
                            reset_degrade_notices)
from mokata.errors import DegradedCapability, MokataError, failure_class_of

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "mokata")

# The exception classes that mean "a capability could not be built — degrade to a documented
# floor", and the failure class each one honestly carries. This is the DESCRIPTIVE list: every
# entry has a real raiser in src/ (the D5 brief forbids inventing a class nothing raises).
DEGRADE_FAMILY = {
    "DsnUnset": FAILURE_UNSET,                      # the DSN env var was never set
    "PostgresUnavailable": FAILURE_UNREACHABLE,     # → the SQLite floor
    "Neo4jUnavailable": FAILURE_UNREACHABLE,        # → the grep floor
    "VectorUnavailable": FAILURE_UNREACHABLE,       # → the lexical floor
    "SessionTransportUnavailable": FAILURE_UNREACHABLE,
    "SharedAuditUnavailable": FAILURE_UNREACHABLE,  # → the log stays LOCAL
    "SubagentUnavailable": FAILURE_UNREACHABLE,     # → sequential flow
    "BackendError": FAILURE_UNREACHABLE,            # knowledge query → the grep floor
    "CrgUnavailable": FAILURE_UNREACHABLE,          # code-review-graph down → the AST floor
    "CrgVersionSkew": FAILURE_UNREACHABLE,          # CRG version out of range → the AST floor
    "_ProbeUnavailable": FAILURE_UNREACHABLE,       # teamdb's internal probe failure
    "_JournalUnavailable": FAILURE_UNREACHABLE,     # team_journal's connect failure
}

# Every OTHER exception class mokata defines is a HARD error: it propagates, nothing falls back,
# and `failure_class` is "" — honest, because there is no remediation to name. Listed explicitly
# so that adding a class forces a decision about which side of the line it is on.
HARD_ERRORS = {
    "MokataError", "AuthoringError", "BrainstormError", "BrainstormGateError", "BugError",
    "ConfigCommandError", "ConfigError", "DebugError", "GraphDegradedError", "LockTimeout",
    "ManifestError",
    "ManifestShareError", "MeasureFirstError", "MemoryDisabledError", "MemoryDocTooNew",
    "MemoryError", "MemoryShareError", "MigrateError", "NetworkEgressBlocked", "OptimizeError",
    "PhaseError", "PlanError", "ProvisionError", "RedBeforeGreenError", "RefineError",
    "RefineGateError", "ReproRequiredError", "RevertError", "RootCauseRequiredError",
    "SessionBundleError", "SetupError", "SkillNotFound", "SkillSourceError", "StackError",
    "VaultError",
}

# Hard errors that fail CLOSED (nothing degrades to a floor) but are still ABOUT a schema, so
# doctor can say WHICH. Two, and the distinction between them is the point:
#   ProvisionError    the shared DB's DDL could not be applied → FAILURE_SCHEMA.
#   MemoryDocTooNew   D6 — a memory DOC declares a schema this build does not speak (a teammate on
#                     a newer mokata wrote it) → FAILURE_DOC_SCHEMA. NOT a DegradedCapability: the
#                     only "floor" available is "write it anyway, minus the fields you couldn't
#                     read", and that floor IS the bug. So it refuses instead of degrading.
CLASSED_HARD_ERRORS = {"ProvisionError": FAILURE_SCHEMA,
                       "MemoryDocTooNew": FAILURE_DOC_SCHEMA}


def _exception_classes():
    """Every exception class defined in src/, by AST — so a new one cannot be added without this
    test seeing it. Keyed by name → (relpath, base names)."""
    found = {}
    for root, _dirs, files in os.walk(SRC):
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            rel = os.path.relpath(path, SRC)
            with open(path, "r", encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=path)
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                bases = [b.id for b in node.bases if isinstance(b, ast.Name)]
                bases += [b.attr for b in node.bases if isinstance(b, ast.Attribute)]
                looks_like_error = (
                    node.name.endswith(("Error", "Unavailable", "Timeout", "Blocked", "NotFound"))
                    or any(b.endswith(("Error", "Exception", "Unavailable")) for b in bases)
                    # after D5 a re-parented class's base is MokataError/DegradedCapability, which
                    # the suffix rules above would miss (`DsnUnset` names neither) — the sweep must
                    # still SEE it, or a re-parented class reads as "stale" and gets deleted.
                    or "DegradedCapability" in bases or "MokataError" in bases)
                if looks_like_error and node.name not in ("DegradedCapability", "MokataError"):
                    found[node.name] = (rel, bases)
    return found


class TestTheTaxonomyIsComplete(unittest.TestCase):
    """Every exception mokata defines is a MokataError. A new one that forgets FAILS here."""

    def test_every_exception_class_is_classified(self):
        classes = _exception_classes()
        known = set(DEGRADE_FAMILY) | HARD_ERRORS
        unclassified = sorted(set(classes) - known)
        detail = "\n".join(f"    {n}  ({classes[n][0]})" for n in unclassified)
        self.assertEqual(
            unclassified, [],
            "UNCLASSIFIED EXCEPTION CLASS(ES) — a new exception appeared in src/ and nobody said "
            "whether it is a DEGRADE (a capability fell back to a floor → subclass "
            "DegradedCapability + name its failure_class) or a HARD error (it propagates → "
            "subclass MokataError, failure_class stays \"\").\n\n" + detail)

    def test_the_register_carries_no_stale_entries(self):
        """A classification for a class that no longer exists is a lie that makes the next reader
        trust the whole list less (the SI.6 rule)."""
        classes = _exception_classes()
        stale = sorted((set(DEGRADE_FAMILY) | HARD_ERRORS) - set(classes) - {"MokataError"})
        self.assertEqual(stale, [], f"these classes no longer exist — remove them: {stale}")

    def test_every_mokata_exception_subclasses_MokataError(self):
        """The point of a base: `except MokataError` catches a failure mokata DEFINED, and does not
        catch an AttributeError from a typo. 210 `except Exception` handlers could not tell those
        apart — which is why every one of them was silently a bug-swallower too."""
        for name, cls in sorted(_live_classes().items()):
            with self.subTest(cls=name):
                self.assertTrue(issubclass(cls, MokataError),
                                f"{name} must subclass MokataError (errors.py)")

    def test_the_degrade_family_subclasses_DegradedCapability_with_its_class(self):
        live = _live_classes()
        for name, expected in sorted(DEGRADE_FAMILY.items()):
            with self.subTest(cls=name):
                cls = live[name]
                self.assertTrue(issubclass(cls, DegradedCapability),
                                f"{name} means 'a capability fell back to a floor' — it must "
                                f"subclass DegradedCapability")
                self.assertEqual(cls.failure_class, expected,
                                 f"{name} carries the wrong failure_class — degrade.py owns this "
                                 f"vocabulary and the exception must speak it")

    def test_a_hard_error_carries_no_failure_class(self):
        """"" is the honest value: it propagates, nothing falls back, there is no remediation to
        name. Inventing a class for it would be a word no failure ever earns."""
        live = _live_classes()
        for name in sorted(HARD_ERRORS - {"MokataError"} - set(CLASSED_HARD_ERRORS)):
            with self.subTest(cls=name):
                self.assertEqual(live[name].failure_class, "",
                                 f"{name} is a hard error — it must not claim a degrade class")

    def test_the_classed_hard_errors(self):
        live = _live_classes()
        for name, expected in CLASSED_HARD_ERRORS.items():
            self.assertEqual(live[name].failure_class, expected)
            self.assertFalse(issubclass(live[name], DegradedCapability),
                             f"{name} fails CLOSED — it is not a degrade")


class TestReParentingChangedNoBehaviour(unittest.TestCase):
    """The re-parenting must be invisible to every existing `except` clause — in the codebase AND
    in user code. A taxonomy that breaks callers is not worth having."""

    def test_secondary_bases_are_preserved(self):
        live = _live_classes()
        self.assertTrue(issubclass(live["LockTimeout"], TimeoutError),
                        "LockTimeout is caught as a TimeoutError — `except TimeoutError` must "
                        "still catch it")
        self.assertTrue(issubclass(live["SkillNotFound"], KeyError),
                        "SkillNotFound is caught as a KeyError")
        self.assertTrue(issubclass(live["SkillSourceError"], RuntimeError))
        self.assertTrue(issubclass(live["NetworkEgressBlocked"], RuntimeError))

    def test_subclass_relationships_within_the_taxonomy_are_preserved(self):
        live = _live_classes()
        self.assertTrue(issubclass(live["BrainstormGateError"], live["BrainstormError"]))
        self.assertTrue(issubclass(live["MemoryDisabledError"], live["MemoryError"]))
        self.assertTrue(issubclass(live["ReproRequiredError"], live["BugError"]))
        self.assertTrue(issubclass(live["RootCauseRequiredError"], live["DebugError"]))
        self.assertTrue(issubclass(live["MeasureFirstError"], live["OptimizeError"]))
        self.assertTrue(issubclass(live["RefineGateError"], live["RefineError"]))

    def test_raising_sites_still_construct_the_same_way(self):
        """No __init__ was touched: DsnUnset still takes (and exposes) the env-var NAME."""
        from mokata.dsn import DsnUnset
        exc = DsnUnset("MOKATA_PG_DSN")
        self.assertEqual(exc.env_name, "MOKATA_PG_DSN")
        self.assertIn("MOKATA_PG_DSN", str(exc))
        self.assertEqual(failure_class_of(exc), FAILURE_UNSET)

    def test_failure_class_of_a_plain_python_bug_is_empty(self):
        """The distinction the whole stage rests on: a designed degrade is classed; an
        AttributeError from a typo is not, and a site can now tell them apart in code."""
        self.assertEqual(failure_class_of(AttributeError("typo")), "")
        self.assertEqual(failure_class_of(ValueError("bad")), "")

    def test_an_instance_may_override_the_class_stamp(self):
        """D1's per-instance stamp: the SAME PostgresUnavailable means UNREACHABLE from a dead host
        and SCHEMA from a denied CREATE, and the remediation differs. The instance wins."""
        from mokata.memory.backends import PostgresUnavailable
        exc = PostgresUnavailable("relation does not exist")
        self.assertEqual(failure_class_of(exc), FAILURE_UNREACHABLE)
        exc.failure_class = FAILURE_SCHEMA
        self.assertEqual(failure_class_of(exc), FAILURE_SCHEMA)


class TestTheCapabilityNotice(unittest.TestCase):
    """The non-team degrade notice: MS.S4's WalDegradeNotice pattern, generalized once."""

    def setUp(self):
        reset_degrade_notices()
        self.addCleanup(reset_degrade_notices)

    def test_it_names_the_subsystem_the_fallback_and_the_class(self):
        out = io.StringIO()
        note_degraded("ledger", FAILURE_CORRUPT,
                      fallback="the hash-chain could NOT be verified",
                      fix="restore the audit ledger, then re-run `mokata doctor`",
                      out=out.write)
        line = out.getvalue()
        self.assertIn("ledger", line)
        self.assertIn("DEGRADED", line)
        self.assertIn("the hash-chain could NOT be verified", line)
        self.assertIn("restore the audit ledger", line)

    def test_it_does_NOT_claim_writes_are_journaled(self):
        """The CM.S2 team notice opens "Writes are journaled and NOT lost" — a claim about the TEAM
        WRITE PATH. On a corrupt ledger it is not merely irrelevant, it is UNTRUE, and a false
        reassurance is exactly the D5 bug wearing a helpful face."""
        out = io.StringIO()
        note_degraded("ledger", FAILURE_CORRUPT, fallback="x", fix="restore it", out=out.write)
        self.assertNotIn("journaled", out.getvalue())

    def test_it_never_interpolates_a_path(self):
        """Secret-safety (CM.S1): a notice names the subsystem and the class, never a directory
        layout."""
        notice = CapabilityDegradeNotice(subsystem="memory-journal", env_name="",
                                         failure_class=FAILURE_LOCAL_IO,
                                         fallback="reads fell back to the bare backend",
                                         detail="Permission denied")
        self.assertNotIn("/", notice.render().replace("`mokata", "").split("(")[0])

    def test_exactly_one_notice_per_subsystem_per_process(self):
        """A briefing or a recall loop must not spam. CM.S2's machinery, unforked."""
        out = io.StringIO()
        first = note_degraded("code-graph", FAILURE_UNREACHABLE, fallback="grep floor",
                              fix="check the graph tool", out=out.write)
        second = note_degraded("code-graph", FAILURE_UNREACHABLE, fallback="grep floor",
                               fix="check the graph tool", out=out.write)
        self.assertTrue(first)
        self.assertFalse(second, "the second notice for the same subsystem must be suppressed")
        self.assertEqual(out.getvalue().count("DEGRADED"), 1)

    def test_two_subsystems_both_speak(self):
        """A window that is both graph-degraded and ledger-degraded is telling the user two
        different true things, and must be able to say both (the MS.S4 rule)."""
        out = io.StringIO()
        note_degraded("code-graph", FAILURE_UNREACHABLE, fallback="grep", fix="x", out=out.write)
        note_degraded("ledger", FAILURE_CORRUPT, fallback="unverified", fix="y", out=out.write)
        self.assertEqual(out.getvalue().count("DEGRADED"), 2)

    def test_the_team_notice_is_untouched(self):
        """CM.S2/D1/D2's notice must render byte-identically — D5 ADDS a shape, it does not bend
        the existing one into saying something new."""
        from mokata.degrade import DegradeNotice
        notice = DegradeNotice(subsystem="memory", env_name="MOKATA_PG_DSN",
                               failure_class=FAILURE_SCHEMA, detail="d")
        rendered = notice.render()
        self.assertIn("served from the LOCAL fallback, NOT shared team memory", rendered)
        self.assertIn("Writes are journaled and NOT lost", rendered)
        self.assertIn("mokata team init", rendered)

    def test_every_failure_class_has_a_label(self):
        """A class with no label renders as its raw slug — a word the user has to decode."""
        for fc in (FAILURE_UNSET, FAILURE_UNREACHABLE, FAILURE_SCHEMA, FAILURE_WAL,
                   FAILURE_LOCAL_IO, FAILURE_CORRUPT, FAILURE_ENGINE):
            notice = CapabilityDegradeNotice(subsystem="s", env_name="", failure_class=fc)
            self.assertNotEqual(notice.class_label, fc, f"{fc} has no human label")


class TestTheEmittedNoticeRegistry(unittest.TestCase):
    """"What silently degraded?" — before D5 this had NO answer: the notice printed once into a
    scrollback and was gone."""

    def setUp(self):
        reset_degrade_notices()
        self.addCleanup(reset_degrade_notices)

    def test_emitted_notices_starts_empty(self):
        self.assertEqual(emitted_notices(), [])

    def test_an_emitted_notice_is_remembered(self):
        note_degraded("code-graph", FAILURE_UNREACHABLE, fallback="the grep floor",
                      fix="check the graph tool", out=lambda _m: None)
        notices = emitted_notices()
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0].subsystem, "code-graph")
        self.assertEqual(notices[0].failure_class, FAILURE_UNREACHABLE)

    def test_a_repeat_does_not_double_record(self):
        for _ in range(3):
            note_degraded("ledger", FAILURE_CORRUPT, fix="x", out=lambda _m: None)
        self.assertEqual(len(emitted_notices()), 1)

    def test_an_isolated_seen_set_does_not_pollute_the_session_registry(self):
        """A test's forced failure must not show up in a real doctor run."""
        note_degraded("code-graph", FAILURE_UNREACHABLE, fix="x", out=lambda _m: None,
                      seen=set())
        self.assertEqual(emitted_notices(), [],
                         "an injected `seen` is an isolated group — it must not be recorded")


class TestDoctorAnswersWhatDegraded(unittest.TestCase):
    """D5 item 4 — `mokata doctor` surfaces the session's emitted notices.

    Before this, "what silently degraded?" had no answer even after D5 made the degrades loud: the
    notice printed ONCE into a scrollback and was gone. The user who scrolled past it — or who never
    saw it, because it fired inside a hook while they were reading code — had no way to ask again.
    A degrade you cannot ask about is only marginally better than one that never spoke (P16/P17)."""

    def setUp(self):
        reset_degrade_notices()
        self.addCleanup(reset_degrade_notices)

    def test_a_healthy_session_prints_nothing(self):
        """No permanent "0 degrades" line. A section that is almost always empty and always present
        is a section the user learns to skip — and it is the one that matters when it is not."""
        from mokata.govern.doctor import render_degrade_report
        self.assertEqual(render_degrade_report(), "")

    def test_doctor_lists_every_subsystem_that_degraded(self):
        from mokata.govern.doctor import render_degrade_report
        note_degraded("code-graph", FAILURE_UNREACHABLE, fallback="recall is on the lexical floor",
                      fix="check the graph tool", out=lambda _m: None)
        note_degraded("secret-guard", FAILURE_ENGINE,
                      fallback="writes are NOT being scanned for secrets",
                      fix="reinstall mokata", out=lambda _m: None)
        report = render_degrade_report()

        self.assertIn("degraded this session (2)", report)
        self.assertIn("code-graph", report)
        self.assertIn("secret-guard", report)
        # each carries its CLASS and its REMEDIATION — the D1 lesson: a notice that names the wrong
        # fix is worse than none, because the user runs it and it changes nothing, forever.
        self.assertIn("writes are NOT being scanned for secrets", report)
        self.assertIn("reinstall mokata", report)
        self.assertIn("check the graph tool", report)

    def test_the_report_rides_on_the_DoctorReport(self):
        """It is a field on the report, not a print — so the MCP/JSON consumers of doctor see it
        too, and it cannot be lost by a surface that renders its own layout."""
        from mokata.govern.doctor import DoctorReport
        note_degraded("ledger", FAILURE_CORRUPT, fallback="the hash-chain could NOT be verified",
                      fix="restore the ledger", out=lambda _m: None)
        from mokata.govern.doctor import render_degrade_report
        rendered = DoctorReport(findings=[], degrade_report=render_degrade_report()).render()
        self.assertIn("ledger", rendered)
        self.assertIn("the hash-chain could NOT be verified", rendered)

    def test_a_capability_notice_never_claims_the_team_write_reassurance(self):
        """The cross-check that ties the stage together: doctor must not tell a user whose LEDGER is
        corrupt that "writes are journaled and NOT lost" — that sentence is about the team write
        path and is simply untrue here. This is the D1+D2 bug ("loud but lying") in a new costume,
        and it is the one D5 must not reintroduce while making everything else loud."""
        from mokata.govern.doctor import render_degrade_report
        note_degraded("ledger", FAILURE_CORRUPT, fallback="the hash-chain could NOT be verified",
                      fix="restore the ledger", out=lambda _m: None)
        self.assertNotIn("journaled", render_degrade_report())


def _live_classes():
    """The exception classes, IMPORTED — so we assert on real MROs, not on source text."""
    from mokata import netguard, oslock, skills, stacks, teamdb, team_audit, team_journal, vault
    from mokata import config, config_cmd, brainstorm, dsn, manifest, pipeline, plans, refine
    from mokata import agent_skills, harness_setup, session_bundle, session_transport, share
    from mokata.execmode import tasks
    from mokata.govern import authoring, graph_required, revert, tdd
    from mokata.knowledge import crg_client, neo4j_backend, query
    from mokata.memory import backends, item, migrate, share as mshare, store, vector
    from mokata.modes import bug, debug, optimize

    mods = (netguard, oslock, skills, stacks, teamdb, team_audit, team_journal, vault, config,
            config_cmd, brainstorm, dsn, manifest, pipeline, plans, refine, agent_skills,
            harness_setup, session_bundle, session_transport, share, tasks, authoring,
            graph_required, revert, tdd, crg_client, neo4j_backend, query, backends, item, migrate,
            mshare, store, vector, bug, debug, optimize)
    live = {"MokataError": MokataError}
    for mod in mods:
        for name in dir(mod):
            obj = getattr(mod, name)
            if isinstance(obj, type) and issubclass(obj, BaseException):
                live.setdefault(name, obj)
    return live


if __name__ == "__main__":
    unittest.main()
