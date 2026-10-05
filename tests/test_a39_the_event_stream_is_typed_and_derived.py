"""0.0.21 stage 10 · R1.S1a–d — the event stream: typed, derived, and content-free.

THE MEASUREMENT THIS STAGE OPENED WITH, because doc 84 carried R1.S1a–d as ✅ and the tree did
not agree: `src/mokata/events/` did not exist, none of doc 42's seven typed dataclasses existed,
there was no store and no `events.enabled`, and across the audit ledger's **49 distinct kinds /
90 call sites NOT ONE carried a duration** — exactly one carried token ESTIMATES. doc 99's
events exit criterion, *"every governance decision emits a typed event carrying duration and
real token usage"*, was at 0%.

WHAT THIS FILE GRADES, and the order is the argument:

  1. the vocabulary is CLOSED — a new event type cannot be introduced by typing a new string;
  2. no event carries CONTENT, probed by planting a marker rather than by reading the types;
  3. the store is append-only in MOKATA (an AST claim, bounded — not a claim about SQLite), and
     a ledger-backed event is distinguishable from one that is not (§7g);
  4. the 65-kind register is TOTAL over a sweep of the tree, so a new ledger kind reds until
     somebody classifies it — the SI.6 / D5 pattern, third use;
  5. the ledger projects every mapped kind and NOTHING ELSE, and cannot fail the append;
  6. MCP dispatch carries a REAL duration, on every outcome including `timed_out`;
  7. the seventh type comes from the gate, because the ledger cannot hold it;
  8. clean resume CLEARS before it loads — the doc 42 rule `hydrate_bundle` never honoured;
  9. the progress log is absorbed as a superset, which it promised in 0.0.16;
 10. an estimate and a measurement never look alike;
 11. ⛔ **R1.S1d's premise is FALSE and this file is where that is recorded as a measurement
     rather than an opinion** — there are no internal print-diagnostics to move onto `logging`,
     and a guard keeps that true.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import ast
import dataclasses
import io
import json
import os
import shutil
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

import _ledger_kinds

from mokata.events import (
    EVENT_TYPES,
    PAYLOAD_TYPES,
    EventStore,
    GateDecision,
    MemoryOp,
    PhaseTransition,
    SecretScanHit,
    TokenSpend,
    ToolCall,
    emit,
    events_dir,
    payload_for,
    query,
    store_for_root,
)
from mokata.events import projection as PROJ
from mokata.events import store as STORE

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src", "mokata")


def a_repo():
    from mokata.init import init_repo
    d = tempfile.mkdtemp()
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda *_a: None)
    STORE.reset_enabled_cache()
    return d


def a_ledger(root):
    from mokata.govern.ledger import AuditLedger
    return AuditLedger.from_mokata_dir(os.path.join(root, ".mokata"))


# ------------------------------------------------------------ 1 · a closed, typed vocabulary

class TheVocabularyIsClosedAndTyped(unittest.TestCase):

    def test_the_seven_types_doc_42_names_all_exist(self):
        self.assertEqual(len(EVENT_TYPES), 7)
        self.assertEqual(len(PAYLOAD_TYPES), 7)
        self.assertEqual({p.TYPE for p in PAYLOAD_TYPES}, set(EVENT_TYPES))

    def test_every_declared_type_has_a_payload_class(self):
        """A type named in the vocabulary with no class behind it is a hole a call site would
        find as a `KeyError`. Derived from the tuple, so adding one without a class reds."""
        for t in EVENT_TYPES:
            self.assertIsNotNone(payload_for(t), f"{t} is declared with no payload class")

    def test_a_type_outside_the_vocabulary_is_REFUSED_not_stored(self):
        """The vocabulary is closed by construction: a new event type cannot be introduced by
        typing a new string at a call site, which is exactly how the LEDGER grew 65 kinds."""
        class Invented:
            TYPE = "something_i_made_up"

            def to_data(self):
                return {}
        with tempfile.TemporaryDirectory() as d:
            store = EventStore(os.path.join(d, "e.db"))
            self.assertIsNone(store.emit(Invented(), session_id="s"))
            self.assertEqual(store.count(), 0)

    def test_every_payload_field_is_a_scalar(self):
        """No nesting, so no payload can grow a dict a fragment could hide in."""
        for cls in PAYLOAD_TYPES:
            for f in dataclasses.fields(cls):
                self.assertIn(f.type.replace("Optional[", "").rstrip("]"),
                              ("str", "int", "bool"),
                              f"{cls.__name__}.{f.name} is not a scalar")


# ------------------------------------------------------------------ 2 · no event carries content

class NoEventCarriesContent(unittest.TestCase):
    """The privacy floor, probed rather than asserted from the types.

    ⚠ **"CONTENT-FREE" IS THE WRONG WORD FOR THIS FLOOR, AND THE STAGE 10 REPORT USED IT
    (review F4).** Events carry **identifiers, verdicts and counts** — `MemoryOp`'s own docstring
    is the standing position: *"the item's ID and TYPE, never its subject or value"*, and
    `team_events.notify` ships the same rule. What they must never carry is a SUBJECT, a VALUE,
    a DIFF, a REASON's prose or a scanned secret, which is what the marker below probes.

    ⛔ The report's stronger claim is what let an UNBOUNDED identifier list past a reader:
    `budget.record_retrieval` joined every retrieved identifier into a `savings` label, so a
    500-item retrieval wrote a 20 KB retrieval manifest into the ledger and from there into a
    projected `TokenSpend.label`. Identifiers are fine; an unbounded list of them in a field whose
    readers treat it as a short name is a different object. Capped and counted now, and pinned
    below.
    """

    def test_a_planted_marker_reaches_no_event(self):
        marker = "CONTENT-MARKER-71bc4e-MUST-NOT-TRAVEL"
        root = a_repo()
        led = a_ledger(root)
        # the three shapes most likely to carry content: a gate's reason, a memory review's
        # rendered DIFF, and a secret-scan subject.
        led.record("write_gate", write_kind="code", target="src/a.py", decision="blocked",
                   reason=marker)
        led.record("review_transition", item_id="i1", subject=marker, diff=marker, actor="cli")
        rows = query(root)
        self.assertTrue(rows, "the fixture must actually produce events")
        self.assertNotIn(marker, json.dumps([r.data for r in rows]))

    def test_a_retrieval_label_is_CAPPED_and_says_how_many_it_dropped(self):
        """⛔ REVIEW F4. The label named every retrieved identifier, unbounded. ⭐ The cap DROPS
        rather than truncates and names the count — half an identifier is a wrong identifier, the
        same argument `team_events.notify` makes about a truncated actor name."""
        from mokata.govern.budget import RETRIEVAL_LABEL_ID_CAP, SavingsTracker

        class _Result:
            identifiers = ["mem-%03d" % i for i in range(RETRIEVAL_LABEL_ID_CAP + 17)]
            tokens_if_dumped = 9000
            tokens_retrieved = 400

        event = SavingsTracker().record_retrieval(_Result())
        self.assertTrue(event.label.startswith("retrieval:mem-000,"))
        self.assertIn("+17 more", event.label, "the dropped count is not reported: %r"
                                               % event.label)
        named = event.label.split(":", 1)[1].split("+")[0].split(",")
        self.assertEqual(RETRIEVAL_LABEL_ID_CAP, len([n for n in named if n]))
        for ident in named:
            if ident:
                self.assertIn(ident, _Result.identifiers,
                              "a TRUNCATED identifier reached the label: %r" % ident)

    def test_a_SHORT_retrieval_label_names_everything_and_adds_no_noise(self):
        """The negative control: the cap must be invisible below it, or every label grows a
        meaningless `+0 more`."""
        from mokata.govern.budget import SavingsTracker

        class _Result:
            identifiers = ["mem-a", "mem-b"]
            tokens_if_dumped = 90
            tokens_retrieved = 40

        self.assertEqual("retrieval:mem-a,mem-b", SavingsTracker().record_retrieval(_Result()).label)

    def test_the_memory_op_drops_a_diff_the_LEDGER_keeps(self):
        """⭐ The projection REFUSING content the canonical record keeps, which is the whole
        reason a projection is not just a second copy."""
        root = a_repo()
        led = a_ledger(root)
        led.record("review_transition", item_id="i1", subject="a secret subject",
                   diff="- old\\n+ new", actor="cli", mtype="persistent")
        rows = [r for r in query(root, types=["memory_op"])]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].data["item_id"], "i1")
        self.assertNotIn("subject", rows[0].data)
        self.assertNotIn("diff", rows[0].data)
        # ...and the ledger still has it, which is what makes this a refusal and not a loss
        entry = [e for e in led.entries() if e.get("kind") == "review_transition"][0]
        self.assertEqual(entry["diff"], "- old\\n+ new")

    def test_a_memory_READ_carries_a_count_and_no_identity(self):
        self.assertEqual(set(MemoryOp(op="read", count=3).to_data()),
                         {"op", "item_id", "mtype", "count"})
        self.assertEqual(MemoryOp(op="read", count=3).to_data()["item_id"], "")

    def test_a_COUNTED_READ_actually_EMITS_and_this_test_is_why(self):
        """⚠ The shape assertion above graded the dataclass and NOT the wiring: the mutant that
        unhooked `_emit_memory_read` from `_bump_read` survived it (§7i — a mechanism nothing
        runs). This drives a real read through a real store and looks for the row."""
        from mokata.memory.store import MemoryStore
        from mokata.memory.backends import SQLiteBackend
        from mokata.tdd_state import StateStore, state_dir
        root = a_repo()
        try:
            os.makedirs(state_dir(root), exist_ok=True)
            store = MemoryStore(SQLiteBackend(os.path.join(root, ".mokata", "m.db")),
                                stats_store=StateStore(state_dir(root)))
            before = len([r for r in query(root, types=["memory_op"])
                          if r.data.get("op") == "read"])
            store.all_active()
            after = [r for r in query(root, types=["memory_op"]) if r.data.get("op") == "read"]
            self.assertEqual(len(after), before + 1, "a counted read emitted no event")
            self.assertEqual(after[-1].data["item_id"], "", "a read names nothing it found")
            self.assertGreaterEqual(after[-1].data["count"], 1)
        finally:
            shutil.rmtree(root, ignore_errors=True)


# ------------------------------------------------- 3 · append-only in mokata, and bounded as such

class TheStoreIsAppendOnlyAndDerived(unittest.TestCase):

    def test_the_events_package_contains_no_update_or_delete_sql(self):
        """An AST claim about MOKATA, which is the only claim available. ⚠ It is NOT a claim
        about SQLite — anyone holding the file can rewrite it, and pretending otherwise would
        be the fail-open class (§7e). The honest integrity story is the `ledger_seq` below."""
        offenders = []
        # CORPUS: THE WORKING TREE — the claim is about the code that SHIPS, and `sync-public.sh`
        # mirrors `src/` with `rsync`, so an untracked `.py` under `src/mokata/events` is
        # published whether or not the index knows about it.
        for base, _dirs, files in os.walk(os.path.join(SRC, "events")):
            if "__pycache__" in base:
                continue
            for fn in sorted(f for f in files if f.endswith(".py")):
                path = os.path.join(base, fn)
                with io.open(path, encoding="utf-8") as fh:
                    tree = ast.parse(fh.read(), filename=path)
                for node in ast.walk(tree):
                    for part in ([node.value] if isinstance(node, ast.Constant) else []):
                        if not isinstance(part, str):
                            continue
                        up = part.upper()
                        if "UPDATE EVENTS" in up or "DELETE FROM EVENTS" in up:
                            offenders.append((fn, part[:40]))
        self.assertEqual(offenders, [], f"the store has a non-append verb: {offenders}")

    def test_a_ledger_backed_event_is_distinguishable_from_one_that_is_not(self):
        with tempfile.TemporaryDirectory() as d:
            store = EventStore(os.path.join(d, "e.db"))
            backed = store.emit(GateDecision("g", "approved"), session_id="s", ledger_seq=4)
            loose = store.emit(SecretScanHit("aws-key"), session_id="s")
            self.assertTrue(backed.ledger_backed)
            self.assertFalse(loose.ledger_backed)
            self.assertIsNone(loose.ledger_seq, "absent must not render as 0 (§7g)")

    def test_a_duration_nobody_measured_is_NONE_and_not_zero(self):
        with tempfile.TemporaryDirectory() as d:
            store = EventStore(os.path.join(d, "e.db"))
            untimed = store.emit(GateDecision("g", "approved"), session_id="s")
            timed = store.emit(GateDecision("g", "approved"), session_id="s", duration_ms=0)
            self.assertIsNone(untimed.duration_ms)
            self.assertEqual(timed.duration_ms, 0, "a measured zero is a real answer")

    def test_an_ENVELOPE_cannot_FORGET_to_say_whether_it_was_timed_or_backed(self):
        """⛔ FOUND BY MUTATION. `Envelope` declared `duration_ms = None` and
        `ledger_seq = None`, and the mutants that changed both to `= 0` SURVIVED all 52 tests —
        every construction passes them explicitly, so the defaults were dead text wearing the
        §7g guarantee (§7f). They are gone: an envelope must STATE the absence."""
        from mokata.events import Envelope
        with self.assertRaises(TypeError):
            Envelope(type="gate_decision", session_id="s")          # type: ignore[call-arg]
        env = Envelope(type="gate_decision", session_id="s", duration_ms=None, ledger_seq=None)
        self.assertIsNone(env.to_row()["duration_ms"])
        self.assertIsNone(env.to_row()["ledger_seq"])

    def test_the_PUBLIC_emit_defaults_to_absent_too_and_not_to_zero(self):
        """The module-level `emit` is a second signature with its own defaults, and the class
        above only reaches `EventStore.emit`. A caller who omits both must get two absences."""
        root = a_repo()
        try:
            ev = emit(root, GateDecision("g", "approved"), session_id="s")
            self.assertIsNotNone(ev, "the event was not stored at all")
            self.assertIsNone(ev.duration_ms, "nobody timed it — 0 would say it took no time")
            self.assertIsNone(ev.ledger_seq, "it derives from no ledger entry (§7g)")
            self.assertFalse(ev.ledger_backed)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_the_store_goes_through_the_ONE_sqlite_factory(self):
        """`test_ms_s4_sqlite_wal` convicted the first version twice. Pinned here too, from the
        other side: the module must NAME the factory, so a future author who reaches for a bare
        connect has to delete this as well."""
        with io.open(os.path.join(SRC, "events", "store.py"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("connect_sqlite", text)
        self.assertNotIn("sqlite3.connect(", text)

    def test_a_query_anchored_to_a_session_returns_only_that_session(self):
        with tempfile.TemporaryDirectory() as d:
            store = EventStore(os.path.join(d, "e.db"))
            store.emit(GateDecision("g", "a"), session_id="A")
            store.emit(GateDecision("g", "b"), session_id="B")
            self.assertEqual([r.session_id for r in store.query(session_id="A")], ["A"])

    def test_an_absent_or_unreadable_store_reads_as_empty_and_never_raises(self):
        with tempfile.TemporaryDirectory() as d:
            # Built OUTSIDE the assertions: this module posix-spells elsewhere, and
            # `test_windows_shell_and_paths` convicts an `os.path.join` inside an assert in a
            # module that does (one_sided_posix) — rightly, since the two spellings must never
            # meet across a comparison.
            absent = os.path.join(d, "nope.db")
            self.assertEqual(EventStore(absent).query(), [])
            self.assertEqual(EventStore(absent).count(), 0)
            self.assertEqual(EventStore(d).query(), [], "a DIRECTORY as a store path")


# ------------------------------------------------------- 4 · the register is total over the tree

class TheRegisterIsTotalOverTheTree(unittest.TestCase):
    """§7i's lesson applied to a map: a guard over the kinds we happened to think of protects
    the kinds we happened to think of."""

    def test_every_kind_the_tree_can_write_is_registered(self):
        swept, _forwarders, unresolved = _ledger_kinds.sweep()
        self.assertEqual(unresolved, [], f"the sweep could not resolve: {unresolved}")
        missing = sorted(set(swept) - PROJ.registered_kinds())
        self.assertEqual(missing, [],
                         "UNREGISTERED LEDGER KIND(S) — a new `record(...)` appeared and nobody "
                         "said whether it is a governance event. Classify it in "
                         "`events/projection.py`'s KIND_REGISTER; UNMAPPED with a reason is a "
                         f"real answer: {missing}")

    def test_the_register_names_no_kind_the_tree_cannot_write(self):
        """The other direction. A phantom entry is a classification of nothing, and it makes the
        totality claim above look stronger than it is."""
        swept, _f, _u = _ledger_kinds.sweep()
        phantom = sorted(PROJ.registered_kinds() - set(swept))
        self.assertEqual(phantom, [], f"registered but unwritable: {phantom}")

    def test_the_sweep_finds_the_three_forwarders_and_resolves_through_them(self):
        """Anti-vacuity: the sweep is only worth something if it sees the INDIRECT kinds."""
        swept, forwarders, _u = _ledger_kinds.sweep()
        self.assertEqual(len(forwarders), 3, f"the forwarder set moved: {forwarders}")
        for kind in ("review_transition", "rule_block", "worktree_create"):
            self.assertIn(kind, swept, f"{kind} reaches the ledger through a forwarder")

    def test_the_forwarder_hop_is_module_scoped_so_it_finds_no_phantoms(self):
        """⛔ My own first draft keyed the hop on the function NAME, and `_log` / `_record` are
        among this tree's most common private methods: it reported 75 kinds, ten of them other
        functions entirely. Stage 07's `.open` lesson in a different costume."""
        swept, _f, _u = _ledger_kinds.sweep()
        for phantom in ("accept", "root_cause", "hypothesize", "synthesis updated", "rule_out"):
            self.assertNotIn(phantom, swept,
                             f"{phantom!r} is another function's step log, not a ledger kind")

    def test_every_mapped_kind_projects_and_every_unmapped_one_does_not(self):
        for kind, (etype, why) in PROJ.KIND_REGISTER.items():
            payload = PROJ.project(kind, {"decision": "x", "target": "t", "actor": "a"})
            if etype is None:
                self.assertIsNone(payload, f"{kind} is UNMAPPED and must project nothing")
            else:
                self.assertIsNotNone(payload, f"{kind} is mapped to {etype} and projected None")
                self.assertEqual(payload.TYPE, etype)
            self.assertTrue(why.strip(), f"{kind} carries an empty reason")

    def test_an_unregistered_kind_projects_nothing_rather_than_raising(self):
        self.assertIsNone(PROJ.project("a_kind_from_2027", {"decision": "x"}))


# ------------------------------------------- 5 · the ledger projects, and cannot fail the append

class TheLedgerProjectsEveryGovernanceDecision(unittest.TestCase):

    def test_a_gate_decision_projects_with_its_ledger_seq(self):
        root = a_repo()
        entry = a_ledger(root).record("write_gate", write_kind="code", target="src/a.py",
                                      actor="cli", decision="approved", reason="")
        rows = [r for r in query(root, types=["gate_decision"]) if r.ledger_seq == entry["seq"]]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].data["gate"], "write_gate")
        self.assertEqual(rows[0].data["subject"], "src/a.py")
        self.assertEqual(rows[0].actor, "cli")

    def test_an_UNMAPPED_kind_projects_NOTHING(self):
        """`impact` is a blast-radius measurement. A number, not a decision — nothing was
        gated, approved or spent — so it is UNMAPPED, and UNMAPPED must mean no row."""
        root = a_repo()
        before = store_for_root(root).count()
        a_ledger(root).record("impact", phase="spec-amend", approach="x", widened=False)
        self.assertEqual(store_for_root(root).count(), before,
                         "an unmapped kind produced an event")

    def test_the_projection_CANNOT_fail_the_canonical_append(self):
        """⛔ The property that makes it safe to wire this at the append point at all. The
        ledger is the proof substrate for every gate claim (P16); an observability projection
        that could break it would be the worst trade in the tree.

        ⚠ THE FIRST VERSION OF THIS TEST PATCHED `EventStore.emit` AND GRADED NOTHING. The
        module-level `emit` swallows first, so the RuntimeError never reached the ledger's own
        handler and the mutant that made that handler `raise` SURVIVED (§7f — the two swallows
        are separate guards and each needs its own test, which is the test below). The failure
        is planted at the boundary the LEDGER calls."""
        from unittest import mock
        root = a_repo()
        led = a_ledger(root)
        with mock.patch.object(STORE, "emit", side_effect=RuntimeError("boom")):
            entry = led.record("write_gate", write_kind="code", target="src/a.py",
                               decision="approved", reason="")
        self.assertIsInstance(entry["seq"], int)
        self.assertTrue(any(e.get("kind") == "write_gate" for e in led.entries()),
                        "the canonical entry must survive a broken projection")

    def test_the_PUBLIC_emit_swallows_a_broken_STORE_and_returns_None(self):
        """The OTHER swallow, graded on its own: the observability lane never fails its caller,
        and a caller that gets None knows nothing was stored (rather than being told it was)."""
        from unittest import mock
        root = a_repo()
        with mock.patch.object(STORE.EventStore, "emit", side_effect=RuntimeError("boom")):
            self.assertIsNone(emit(root, GateDecision("g", "approved"), session_id="s"))

    def test_an_UNREADABLE_toggle_for_a_ROOT_resolves_to_ON_as_well(self):
        """`enabled_for_root` is the cached path the projection actually takes, and it has its
        own §7e decision. The class below covers `enabled(surface)`; this covers the root."""
        from unittest import mock
        root = a_repo()
        STORE.reset_enabled_cache()
        from mokata.config import Surface
        with mock.patch.object(Surface, "is_initialized", side_effect=RuntimeError("boom")):
            self.assertTrue(STORE.enabled_for_root(root),
                            "an unreadable toggle must never SILENTLY disable an audit surface")
        STORE.reset_enabled_cache()

    def test_the_ledger_root_is_derived_structurally_and_refuses_a_foreign_path(self):
        """⛔ **THIS TEST ASSERTED `os.path.realpath(root)` AND THAT IS A PROPERTY OF THE HOST.**

        On Linux `/tmp` is a real directory, so `abspath` and `realpath` agree and this was green
        in every run — CI, the dev VM, every pre-cut. On macOS `mkdtemp()` hands back
        `/var/folders/…` and `/var` is a symlink to `/private/var`, so the two sides differ by a
        prefix and the test reds **on the one machine the release is actually cut from.** Found
        2026-10-04, by running it there for the first time.

        ⭐ **AND THE CODE IS RIGHT, WHICH IS WHY THIS IS THE SIDE THAT MOVES.** `repo_identity`
        draws the distinction on purpose and documents it: `repo_identity(root)` is an IDENTITY
        and is `realpath`-ed (for grouping and keying), while `canonical_repo_root(root)` is a
        PATH and is deliberately *"exactly `abspath(root)` when there is no symlink, so the
        derived project key is unchanged (the pinned-project-key regression guard holds)"*.
        `root_of_path` is the INVERSE OF `path_for` — a path, not an identity — so `abspath` is
        both correct and consistent, and `realpath`-ing it would move the project key that guard
        pins.

        ⭐ So the assertion is now the ROUND TRIP rather than a spelling: whatever `path_for`
        builds from a root, `root_of_path` returns that root. A mechanism cannot be host-specific
        the way a spelling can."""
        from mokata.govern.ledger import AuditLedger
        root = a_repo()
        led = a_ledger(root)
        self.assertEqual(AuditLedger.root_of_path(led.path), os.path.abspath(root))
        # The inverse property, stated as an inverse — this is what the docstring of
        # `root_of_path` actually promises, and it holds on every platform.
        #
        # ⚠ The join is HOISTED OUT of the assertion. `test_repo_paths_invariant` and
        # `test_windows_shell_and_paths` both convict an `os.path.join` inside an assert in a
        # module that posix-spells elsewhere, and they are right to: the two spellings must never
        # meet across a comparison. Third time this release, and the rule has been correct every
        # time.
        mokata_dir = os.path.join(root, ".mokata")
        self.assertEqual(AuditLedger.root_of_path(AuditLedger.path_for(mokata_dir)),
                         os.path.abspath(root))
        self.assertIsNone(AuditLedger.root_of_path("/tmp/somewhere/ledger.jsonl"))
        self.assertIsNone(AuditLedger.root_of_path(""))

    def test_events_enabled_false_stops_the_projection(self):
        import json as _json
        root = a_repo()
        before = store_for_root(root).count()      # init's own bootstrap event already landed
        mpath = os.path.join(root, ".mokata", "manifest.json")
        with io.open(mpath, encoding="utf-8") as fh:
            data = _json.load(fh)
        data.setdefault("settings", {})["events"] = {"enabled": False}
        with io.open(mpath, "w", encoding="utf-8") as fh:
            _json.dump(data, fh)
        STORE.reset_enabled_cache()
        a_ledger(root).record("write_gate", write_kind="code", target="src/a.py",
                              decision="approved", reason="")
        self.assertEqual(store_for_root(root).count(), before,
                         "the toggle is OFF, so nothing new may land")

    def test_an_unreadable_toggle_resolves_to_ON_because_a_silent_OFF_is_worse(self):
        STORE.reset_enabled_cache()

        class NoManifest:
            @property
            def manifest(self):
                raise RuntimeError("unreadable")
        self.assertTrue(STORE.enabled(NoManifest()))


# ============================================================ 5b · THE REVIEW'S F1 AND F2

class TheTOGGLEIsHonouredForEveryProducerAndNotOnlyForASurface(unittest.TestCase):
    """⛔ **REVIEW F1 — `events.enabled: false` TURNED OFF NOTHING FOR FIVE OF THE SIX PRODUCERS.**

    `emit()` read `hasattr(surface_or_root, "manifest")` before consulting the toggle, and that is
    False for a root STRING — which is what every producer in this tree passes. The toggle existed,
    `enabled()` was correct, `enabled_for_root()` was correct AND cached, and the one call site
    that decides consulted neither.

    ⭐ **The ledger was the single producer that appeared to obey it**, because it carried its own
    `_events_enabled()` — so the one test anybody wrote for the toggle
    (`test_events_enabled_false_stops_the_projection`) went through the ONE path that worked.
    *A toggle nobody reads is worse than no toggle: it is a promise the manifest makes on the
    product's behalf.* That private copy is now DELETED (§7f/§7d) and the gate is in one place."""

    def _disable(self, root):
        mpath = os.path.join(root, ".mokata", "manifest.json")
        with io.open(mpath, encoding="utf-8") as fh:
            data = json.load(fh)
        data.setdefault("settings", {})["events"] = {"enabled": False}
        with io.open(mpath, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        STORE.reset_enabled_cache()

    def test_a_ROOT_STRING_emit_obeys_the_toggle(self):
        root = a_repo()
        self.addCleanup(shutil.rmtree, root, True)
        before = store_for_root(root).count()
        self._disable(root)
        out = STORE.emit(root, MemoryOp(op="read", item_id="x", count=1), session_id="s")
        self.assertIsNone(out, "a root-string emit ignored events.enabled")
        self.assertEqual(before, store_for_root(root).count())

    def test_the_SAME_call_lands_when_the_toggle_is_ON(self):
        """The control. Without it, the assertion above is satisfied by an emit that never works."""
        root = a_repo()
        self.addCleanup(shutil.rmtree, root, True)
        before = store_for_root(root).count()
        out = STORE.emit(root, MemoryOp(op="read", item_id="x", count=1), session_id="s")
        self.assertIsNotNone(out)
        self.assertEqual(before + 1, store_for_root(root).count())

    def test_EVERY_producer_reaches_the_toggle_through_the_ONE_gate(self):
        """⭐ DERIVED, not a list of five hand-written end-to-end tests. Each producer calls
        `events.emit`; the question is whether any of them has grown its OWN toggle check beside
        the gate, which is how F1 hid in the first place — the ledger's private copy made the
        toggle look honoured while five producers had no check at all. One reader, or the next
        one is invisible again."""
        text = io.open(os.path.join(SRC, "events", "store.py"), encoding="utf-8").read()
        tree = ast.parse(text)
        gates = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                 and n.func.id == "enabled_for_root"]
        self.assertEqual(1, len(gates),
                         "`enabled_for_root` is called %d times inside the store; the gate is "
                         "supposed to be ONE place" % len(gates))
        others = []
        for rel in ("govern/ledger.py", "progress_events.py", "mcp/server.py", "govern/gate.py",
                    "execmode/orchestrator.py", "memory/store.py"):
            body = io.open(os.path.join(SRC, rel), encoding="utf-8").read()
            for name in ("enabled_for_root(", "_events_enabled"):
                # a PROSE mention is not a call: the deletion note in ledger.py names both.
                for line in body.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("#") or stripped.startswith('"'):
                        continue
                    if name in stripped:
                        others.append("%s: %s" % (rel, stripped))
        self.assertEqual([], others,
                         "a producer consults the toggle itself, so the gate is doubled and "
                         "neither copy can be graded (§7f):\n  " + "\n  ".join(others))


class NothingIsCreatedOutsideAMokataRepo(unittest.TestCase):
    """⛔ **REVIEW F2.** `store_for_root` opens — and therefore CREATES —
    `.mokata/temp_local/events/events.db` under whatever directory it is handed, so an emit with a
    root that is not a mokata repo littered a database into a user's tree.

    ⚠ This is a SEPARATE refusal from the toggle and must stay separate: `enabled_for_root`
    resolves an absent or unreadable manifest to ON by design (§7e — an unreadable toggle must
    never silently disable an audit surface), so it can never be the thing that answers *"is this
    even a repo?"*. Two questions, two answers (§7g)."""

    def test_an_emit_into_a_bare_directory_writes_NOTHING(self):
        with tempfile.TemporaryDirectory() as d:
            STORE.reset_enabled_cache()
            out = STORE.emit(d, MemoryOp(op="read", item_id="x", count=1), session_id="s")
            self.assertIsNone(out, "an emit outside a repo was accepted")
            self.assertEqual([], sorted(os.listdir(d)),
                             "something was created in a directory that is not a mokata repo")

    def test_and_the_absent_manifest_still_reads_as_events_ON(self):
        """The §7e half, pinned beside the refusal so a later reader cannot 'simplify' the two
        into one answer. `enabled_for_root` on a bare directory is ON; the EMIT still refuses."""
        with tempfile.TemporaryDirectory() as d:
            STORE.reset_enabled_cache()
            self.assertTrue(STORE.enabled_for_root(d))

    def test_an_INITIALIZED_repo_is_of_course_accepted(self):
        root = a_repo()
        self.addCleanup(shutil.rmtree, root, True)
        self.assertIsNotNone(
            STORE.emit(root, MemoryOp(op="read", item_id="x", count=1), session_id="s"))


# ------------------------------------------------------- 6 · MCP dispatch, with a real duration

class TheMcpDispatchCarriesARealDuration(unittest.TestCase):

    def _serve(self, fn, name, kind="read"):
        from mokata.mcp.server import _serve
        return _serve(fn, name=name, kind=kind)

    def test_a_served_call_carries_a_measured_duration(self):
        import time as _t
        root = a_repo()
        self._serve(lambda path=".": (_t.sleep(0.03), {"status": "ok"})[1], "slow_tool")(path=root)
        rows = [r for r in query(root, types=["tool_call"]) if r.data["tool"] == "slow_tool"]
        self.assertEqual(len(rows), 1)
        self.assertIsNotNone(rows[0].duration_ms)
        self.assertGreaterEqual(rows[0].duration_ms, 20)

    def test_ok_is_read_from_the_STATUS_and_not_from_did_it_raise(self):
        """A `refused`, an `error` and a `timed_out` are all non-exceptional RETURNS here.
        Calling them successes would make the event say the opposite of what the caller saw."""
        root = a_repo()

        def boom(path="."):
            raise RuntimeError("nope")
        self._serve(boom, "bad_tool", kind="write")(path=root)
        rows = [r for r in query(root, types=["tool_call"]) if r.data["tool"] == "bad_tool"]
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0].data["ok"])

    def test_EVERY_outcome_is_evented_including_the_one_that_matters_most(self):
        """⛔ The wrapper had FOUR returns and the instrumentation went on one exit, so every
        outcome reaches the store. An instrument that goes quiet on `timed_out` is quiet exactly
        where the trouble is (§7e)."""
        from unittest import mock
        root = a_repo()
        with mock.patch("mokata.mcp.server._mcp_timeout_for", return_value=0.01):
            import time as _t
            self._serve(lambda path=".": (_t.sleep(0.4), {"status": "ok"})[1], "wedged")(path=root)
        rows = [r for r in query(root, types=["tool_call"]) if r.data["tool"] == "wedged"]
        self.assertEqual(len(rows), 1, "a timed-out dispatch must still be evented")
        self.assertFalse(rows[0].data["ok"])

    def test_the_tool_result_is_byte_identical_with_the_instrumentation_in_place(self):
        root = a_repo()
        out = self._serve(lambda path=".": {"status": "ok", "n": 7}, "pure")(path=root)
        self.assertEqual(out, {"status": "ok", "n": 7})


# ------------------------------------------------------ 7 · the seventh type comes from the gate

class TheSeventhTypeComesFromTheGate(unittest.TestCase):

    def _blocked(self, root, body):
        from mokata.govern import WriteGate, WriteRequest
        return WriteGate(ledger=a_ledger(root)).submit(
            WriteRequest("code", os.path.join(root, "a.py"), content=body, actor="cli"),
            commit=lambda: None, assume_yes=True, workspace_root=root)

    def test_a_blocked_write_emits_a_scan_hit_the_ledger_cannot_hold(self):
        root = a_repo()
        out = self._blocked(root, "AKIAIOSFODNN7EXAMPLE\n")
        self.assertFalse(out.committed)
        rows = query(root, types=["secret_scan_hit"])
        self.assertTrue(rows)
        for r in rows:
            self.assertIsNone(r.ledger_seq, "it derives from no ledger entry, so it claims none")
            self.assertEqual(set(r.data), {"kind", "where", "count"})

    def test_ONE_event_per_kind_and_not_per_finding(self):
        """Eight keys in one file is one fact about that file. A row per finding would let a
        reader infer how many secrets a path holds, which is more about the content than this
        store should know."""
        root = a_repo()
        self._blocked(root, "AKIAIOSFODNN7EXAMPLE\nAKIAIOSFODNN7EXAMPLF\nAKIAIOSFODNN7EXAMPLG\n")
        rows = query(root, types=["secret_scan_hit"])
        kinds = [r.data["kind"] for r in rows]
        self.assertEqual(len(kinds), len(set(kinds)), f"one row per KIND, got {kinds}")
        self.assertTrue(any(r.data["count"] >= 3 for r in rows), "the count carries the number")

    def test_a_clean_write_emits_no_scan_hit(self):
        root = a_repo()
        out = self._blocked(root, "RATE = 2\n")
        self.assertTrue(out.committed)
        self.assertEqual(query(root, types=["secret_scan_hit"]), [])


# ------------------------------------------------------------ 8 · clean resume clears, then loads

class CleanResumeClearsBeforeItLoads(unittest.TestCase):

    def _repo_with_a_live_session(self):
        from mokata.config import Surface
        root = a_repo()
        surface = Surface.load(root)
        store = surface.state
        store.write("brainstorm_progress", {"topic": "A"})
        store.write("emitted_spec", {"v": "A-spec"})
        store.write("approved_approach", {"approach": "A-approved"})
        return root, surface, store

    def _bundle(self, root, state):
        from mokata import session_bundle as SB
        b = {"schema_version": SB.BUNDLE_SCHEMA_VERSION, "kind": SB.BUNDLE_KIND, "run_id": "r1",
             "state": state, "repo_fingerprint": SB.repo_fingerprint(root),
             "transcript": None, "meta": {}, "cross_repo": False, "origin_repo": ""}
        b["content_hash"] = SB._hash_core(b)
        return b

    def test_a_pull_leaves_ZERO_trace_of_the_session_it_replaced(self):
        from mokata import session_bundle as SB
        root, surface, store = self._repo_with_a_live_session()
        res = SB.hydrate_bundle(surface, self._bundle(root, {"brainstorm_progress": {"topic": "B"}}),
                                assume_yes=True)
        self.assertTrue(res.committed)
        self.assertEqual(store.read("brainstorm_progress"), {"topic": "B"})
        self.assertIsNone(store.read("emitted_spec"), "session A's spec bled through")
        self.assertIsNone(store.read("approved_approach"), "session A's APPROVAL bled through")

    def test_what_it_cleared_is_REPORTED_not_a_silent_side_effect(self):
        from mokata import session_bundle as SB
        root, surface, _store = self._repo_with_a_live_session()
        res = SB.hydrate_bundle(surface, self._bundle(root, {"brainstorm_progress": {"topic": "B"}}),
                                assume_yes=True)
        self.assertEqual(sorted(res.cleared),
                         ["approved_approach", "brainstorm_progress", "emitted_spec"])

    def test_a_PRE_MS_S2_SINGLETON_is_cleared_and_REPORTED(self):
        """⛔ **REVIEW F3, AND IT IS THE APPROVAL THAT SURVIVED.**

        A `SessionScopedStore` answers `exists("approved_approach")` True when only the LEGACY
        unscoped file is on disk — a deliberate fallback, so a pre-MS.S2 session keeps working.
        Its `delete` removed only `approved_approach__<sid>`. So `clear_session_state` asked for
        every present key, deleted a scoped file that was not there, got `False` for each, and
        **reported `cleared: []` while the replaced session's approval sat untouched** — the exact
        authority `strip_imported_approval` exists to withhold, surviving the one step that exists
        to remove it.

        ⭐ The report was the second half of the defect: `cleared: []` is also what an
        already-clean repo says (§7g), so nothing on any surface distinguished *"there was nothing
        to clear"* from *"I could not clear it"*.

        ⚠ The fixture writes through the BASE store under the UNSCOPED name, which is exactly what
        a repo upgraded from a pre-MS.S2 mokata has on disk. The classes above all write through
        the scoped store, which is why every one of them passed over this."""
        from mokata import session_bundle as SB
        from mokata.config import Surface
        from mokata.state import StateStore
        from mokata.tdd_state import state_dir

        root = a_repo()
        self.addCleanup(shutil.rmtree, root, True)
        base = StateStore(state_dir(root))
        base.write("approved_approach", {"approach": "A-approved"})     # the legacy singleton
        base.write("emitted_spec", {"v": "A-spec"})

        surface = Surface.load(root)
        store = surface.state
        self.assertTrue(store.exists("approved_approach"),
                        "fixture assumption: the legacy fallback makes it read as PRESENT")
        self.assertEqual({"approach": "A-approved"}, store.read("approved_approach"),
                         "fixture assumption: and it is what this session READS")

        res = SB.hydrate_bundle(surface,
                                self._bundle(root, {"brainstorm_progress": {"topic": "B"}}),
                                assume_yes=True)
        self.assertTrue(res.committed)
        self.assertIsNone(store.read("approved_approach"),
                          "the replaced session's APPROVAL survived the clean resume")
        self.assertIsNone(store.read("emitted_spec"))
        self.assertIn("approved_approach", res.cleared,
                      "it was cleared and the report denied it: %r" % (res.cleared,))
        self.assertIn("emitted_spec", res.cleared)

    def test_a_scoped_delete_on_its_OWN_still_leaves_the_singleton(self):
        """The negative control, and it is what keeps the repair narrow. `delete`'s one-way
        contract is RIGHT for ordinary writes — a scoped delete must not reach through and destroy
        a singleton other sessions still read — so only the clean-resume path gets the stronger
        clear (§7f: a fix that also changes the healthy case is a fix somebody reverts)."""
        from mokata.config import Surface
        from mokata.state import StateStore
        from mokata.tdd_state import state_dir

        root = a_repo()
        self.addCleanup(shutil.rmtree, root, True)
        base = StateStore(state_dir(root))
        base.write("approved_approach", {"approach": "A-approved"})
        store = Surface.load(root).state
        store.delete("approved_approach")
        self.assertEqual({"approach": "A-approved"}, base.read("approved_approach"),
                         "a plain scoped delete reached through to the legacy singleton")
        self.assertTrue(store.delete_session_state("approved_approach"),
                        "and the clean-resume delete must report that it removed something")
        self.assertIsNone(base.read("approved_approach"))

    def test_a_DECLINED_pull_clears_nothing(self):
        """The clear lives INSIDE the gate's commit closure for this reason. A refused pull that
        had already wiped the user's session would be the worst outcome available here."""
        from mokata import session_bundle as SB
        root, surface, store = self._repo_with_a_live_session()
        res = SB.hydrate_bundle(surface, self._bundle(root, {"brainstorm_progress": {"topic": "B"}}),
                                confirm=lambda *_a, **_k: False, assume_yes=False)
        self.assertFalse(res.committed)
        self.assertEqual(res.cleared, [])
        self.assertEqual(store.read("emitted_spec"), {"v": "A-spec"})
        self.assertEqual(store.read("brainstorm_progress"), {"topic": "A"})

    def test_the_ADDITIVE_LOOP_offender_is_planted_so_this_class_grades_the_property(self):
        """§7i. The pre-fix `hydrate_bundle` wrote `for key, data in write_state.items()` with no
        clear, and every individual value it left behind was well-formed — which is why the
        bleed-through was invisible."""
        from mokata.config import Surface
        root, surface, store = self._repo_with_a_live_session()
        for key, data in {"brainstorm_progress": {"topic": "B"}}.items():
            store.write(key, data)                      # the offender, inline
        self.assertEqual(store.read("emitted_spec"), {"v": "A-spec"},
                         "the additive loop leaves the old spec — that IS the defect")
        self.assertIsInstance(Surface.load(root), Surface)

    def test_the_clear_set_is_DERIVED_from_the_two_declarations_that_already_exist(self):
        """§7j. A third hand-written list would have been wrong inside a release:
        `SESSION_SCOPED_KEYS` carries `approved_refinements` and `_SESSION_KEYS` does not."""
        from mokata import session_bundle as SB
        from mokata.session_state import SESSION_SCOPED_KEYS
        root, _surface, store = self._repo_with_a_live_session()
        store.write("approved_refinements", {"r": 1})
        keys = SB.resumable_state_keys(store)
        self.assertIn("approved_refinements", keys)
        self.assertTrue(set(SB._SESSION_KEYS) | set(SESSION_SCOPED_KEYS) >= set(keys))

    def test_checkpoints_are_cleared_too_and_are_found_on_disk_not_listed(self):
        from mokata import session_bundle as SB
        from mokata.govern.resume import CHECKPOINT_PREFIX
        root, surface, store = self._repo_with_a_live_session()
        store.write(CHECKPOINT_PREFIX + "oldrun", {"passed": ["brainstorm"]})
        res = SB.hydrate_bundle(surface, self._bundle(root, {"brainstorm_progress": {"topic": "B"}}),
                                assume_yes=True)
        self.assertIn(CHECKPOINT_PREFIX + "oldrun", res.cleared)
        self.assertIsNone(store.read(CHECKPOINT_PREFIX + "oldrun"))


# -------------------------------------- 9 · the progress log is absorbed, as it said it would be

class TheProgressLogIsAbsorbedAsASuperset(unittest.TestCase):
    """`progress_events.py` has declared since 0.0.16 that its envelope is *"a COMPATIBLE SUBSET
    of 0.1.0's R1.S1a event stream, so R1.S1a can absorb this log as a superset rather than a
    second store to migrate."* Until now, nothing had tried."""

    def _log(self, root):
        from mokata.progress_events import ProgressLog
        return ProgressLog(os.path.join(root, ".mokata", "temp_local", "state",
                                        "progress-events.jsonl"))

    def test_enter_and_pass_become_phase_transitions_carrying_the_run(self):
        from mokata.progress_events import STAGE_ENTER, STAGE_PASS
        root = a_repo()
        log = self._log(root)
        log.append_event(STAGE_ENTER, "develop", run_id="r1")
        log.append_event(STAGE_PASS, "develop", run_id="r1")
        rows = query(root, types=["phase_transition"])
        self.assertEqual([(r.data["phase"], r.data["outcome"]) for r in rows],
                         [("develop", "enter"), ("develop", "pass")])
        self.assertEqual({r.run_id for r in rows}, {"r1"})

    def test_a_review_verdict_becomes_a_GATE_DECISION_and_the_distinction_is_the_point(self):
        """The log's own docstring separates *"PhaseTransition (enter/pass)"* from *"verdict
        events"*. A verdict IS a gate's decision, and filing it as a phase transition would lose
        the thing an auditor is looking for."""
        from mokata.progress_events import REVIEW_VERDICT
        root = a_repo()
        self._log(root).append_event(REVIEW_VERDICT, "review", run_id="r1",
                                     data={"verdict": "pass"})
        rows = query(root, types=["gate_decision"])
        self.assertTrue(any(r.data["gate"] == "review" and r.data["decision"] == "pass"
                            for r in rows))
        self.assertEqual(query(root, types=["phase_transition"]), [])

    def test_these_carry_no_ledger_seq_because_they_derive_from_no_ledger_entry(self):
        from mokata.progress_events import STAGE_ENTER
        root = a_repo()
        self._log(root).append_event(STAGE_ENTER, "develop", run_id="r1")
        for r in query(root, types=["phase_transition"]):
            self.assertIsNone(r.ledger_seq)

    def test_the_LOG_ITSELF_still_holds_the_line_and_is_not_replaced(self):
        """⚠ The log stays. `build_stage_badge` and the ship-review gate read it on the hot path
        with a BACKWARD scan that stops at the first match for the run it asks about
        (REVIEW-FIX.R2) — an answer that no longer depends on how much noise landed after the
        evidence is not a property to trade for tidiness."""
        from mokata.progress_events import STAGE_ENTER
        root = a_repo()
        log = self._log(root)
        log.append_event(STAGE_ENTER, "develop", run_id="r1")
        self.assertEqual([e["type"] for e in log.read_events()], [STAGE_ENTER])

    def test_an_unknown_progress_type_projects_nothing_rather_than_guessing(self):
        root = a_repo()
        self._log(root).append_event("a_type_from_2027", "develop", run_id="r1")
        self.assertEqual(store_for_root(root).count(), 1,
                         "only init's own bootstrap event; the unknown type projected nothing")


# ------------------------------------ 10 · an estimate and a measurement never look alike

class AnEstimateAndAMeasurementNeverLookAlike(unittest.TestCase):

    def test_the_run_emits_an_ESTIMATE_before_and_a_MEASUREMENT_after(self):
        from mokata.execmode import SEQUENTIAL, ExecutionChoice, Task, TaskResult, run_tasks
        root = a_repo()

        class Runner:
            def run(self, task, **_kw):
                return TaskResult(task.id, True, "done", output="o", input_tokens=700,
                                  output_tokens=300, seen_context=task.context)
        run_tasks([Task("t1", "a", context="c")], ExecutionChoice(SEQUENTIAL),
                  runner=Runner(), ledger=a_ledger(root))
        rows = query(root, types=["token_spend"])
        estimates = [r for r in rows if r.data["estimated"]]
        measured = [r for r in rows if not r.data["estimated"]]
        self.assertTrue(estimates, "exec_estimate must still project")
        self.assertEqual(len(measured), 1)
        self.assertEqual((measured[0].data["input_tokens"], measured[0].data["output_tokens"]),
                         (700, 300))

    def test_a_SIMULATED_run_emits_no_measurement_because_nothing_ran(self):
        """R-13F's principle, carried into the event stream: a `TokenSpend` of zero would read
        as *"this run cost nothing"* rather than *"nothing ran"* (§7g)."""
        from mokata.execmode import SEQUENTIAL, ExecutionChoice, Task, run_tasks
        root = a_repo()
        run_tasks([Task("t1", "a", context="c")], ExecutionChoice(SEQUENTIAL),
                  runner=None, ledger=a_ledger(root))
        self.assertEqual([r for r in query(root, types=["token_spend"])
                          if not r.data["estimated"]], [])

    def test_the_estimate_label_is_kind_qualified_so_the_pair_cannot_be_confused(self):
        """⛔ Found by running it: `exec_estimate` carries `mode="sequential"`, which rendered a
        TokenSpend labelled exactly `sequential` — indistinguishable at a glance from the real
        `exec:sequential` spend, differing only in a boolean."""
        est = PROJ.project("exec_estimate", {"mode": "sequential", "est_in": 10, "est_out": 20})
        self.assertEqual(est.label, "exec_estimate:sequential")
        self.assertTrue(est.estimated)

    def test_only_a_transcript_derived_calibration_is_marked_MEASURED(self):
        real = PROJ.project("token_calibration", {"context": "transcript:m", "estimate": 100,
                                                  "actual": 90})
        self.assertFalse(real.estimated, "a transcript `actual` is a measurement")
        no_actual = PROJ.project("token_calibration", {"context": "bootstrap", "estimate": 100})
        self.assertTrue(no_actual.estimated, "an estimate-only row is still an estimate")


# ---------------------------- 11 · R1.S1d: the premise is false, and that is the measurement

_PRINT_REGISTER = {
    # Every `print()` in src/ OUTSIDE the CLI surfaces, with what it is. ⛔ R1.S1d asked for
    # "internal print-diagnostics → logging + a JSON formatter". THERE ARE NONE. Each of these
    # is a NAMED channel, and D5 is why: it swept every silent degrade in this tree and gave each
    # subsystem one notice channel rather than a logger. Routing these through `logging` would
    # make a deliberately LOUD notice quiet unless configured — the opposite of what D5 bought.
    "degrade.py": "the ONE stderr sink for every classed degrade notice (D5)",
    "deprecation.py": "the ONE stderr sink for deprecation/removal notices",
    "notify.py": "the ONE stderr sink for attention notifications",
    "memory/_sqlite.py": "the WAL-declined notice — once per process, loud by design",
    "session_flow.py": "note_persist_failure's loud, secret-safe warning (D5 added it)",
    "prompt.py": "the fail-closed refusals: stdin is not a TTY / unreadable, defaulting to No. "
                 "A refusal nobody can see is not a governed refusal",
    "execmode/selector.py": "the same shape, for the execution-mode default",
    "hook_cli.py": "the hook PROTOCOL — a JSON payload on stdout that Claude Code reads. Not a "
                   "diagnostic at all; routing it through a logger would break the hook",
    "agent_skills.py": "inside `if __name__ == \"__main__\"` — a developer script's own output",
}

_CLI_SURFACES = ("cli.py", "menu.py", "onboard.py", "onboarding.py", "dashboard.py")


def _print_sites():
    """Every `print()` call in src/, grouped by module. CORPUS: THE WORKING TREE — this asks what
    mokata SHIPS, and `sync-public.sh` mirrors with `rsync`, so an untracked `.py` under `src/`
    really is published."""
    out = {}
    # CORPUS: THE WORKING TREE — see the docstring; a declaration in prose absolves nothing.
    for base, _dirs, files in os.walk(SRC):
        if "__pycache__" in base:
            continue
        for fn in sorted(f for f in files if f.endswith(".py")):
            path = os.path.join(base, fn)
            rel = _support.posix_rel(path, SRC)
            with io.open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=path)
            n = sum(1 for node in ast.walk(tree)
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "print")
            if n:
                out[rel] = n
    return out


class TheTreeHasNoInternalPrintDiagnostics(unittest.TestCase):
    """⛔ **R1.S1d's PREMISE IS FALSE, AND THIS IS WHERE THAT IS A MEASUREMENT RATHER THAN AN
    OPINION.** doc 42 asked for "internal print-diagnostics → `logging` + JSON formatter behind
    one knob; human-facing CLI output unchanged". Measured at the head of this stage: **513
    `print()` calls, 502 of them in the CLI surfaces the spec says must stay unchanged**, and
    every one of the other eleven is a named notice channel.

    ⭐ **D5 ALREADY DID THIS WORK, DIFFERENTLY AND BETTER.** It swept every broad handler in
    `src/` and gave each subsystem ONE notice channel with a classed reason, so a degrade stopped
    being a secret. Moving those onto `logging` would make a deliberately LOUD notice quiet
    unless someone configured a handler — which is the defect D5 existed to remove.

    So R1.S1d ships a GUARD instead of a swap: the premise stays true, and a new bare diagnostic
    print in an internal module reds here."""

    def test_the_non_cli_print_sites_are_exactly_the_declared_register(self):
        sites = _print_sites()
        non_cli = {k for k in sites
                   if not k.startswith("cli") and k not in _CLI_SURFACES}
        undeclared = sorted(non_cli - set(_PRINT_REGISTER))
        self.assertEqual(undeclared, [],
                         "UNDECLARED print() IN AN INTERNAL MODULE — R1.S1d measured that this "
                         "tree has no internal print-diagnostics, and this is the guard that "
                         "keeps that true. If it is a diagnostic, it belongs on an existing "
                         "notice channel (`degrade.note_degraded`); if it is a named channel, "
                         f"register it with its reason: {undeclared}")

    def test_the_register_names_no_module_that_has_stopped_printing(self):
        sites = _print_sites()
        stale = sorted(k for k in _PRINT_REGISTER if k not in sites)
        self.assertEqual(stale, [], f"registered but no longer prints: {stale}")

    def test_every_register_entry_carries_a_real_reason(self):
        for mod, why in _PRINT_REGISTER.items():
            self.assertGreater(len(why.strip()), 20, f"{mod}'s reason is a placeholder")

    def test_the_cli_surfaces_are_where_the_printing_actually_is(self):
        """The measurement that overturns the premise, asserted rather than remembered. If this
        ratio ever inverts, R1.S1d's original shape becomes the right one after all."""
        sites = _print_sites()
        total = sum(sites.values())
        cli = sum(v for k, v in sites.items()
                  if k.startswith("cli") or k in _CLI_SURFACES)
        self.assertGreater(total, 400, "the corpus shrank — re-measure before trusting the ratio")
        self.assertGreater(cli / total, 0.95,
                           f"only {cli} of {total} prints are CLI output; R1.S1d's premise that "
                           "the tree is full of internal diagnostics may now hold")

    def test_no_internal_module_imports_logging_either(self):
        """The negative half: the swap was not quietly half-done somewhere. Zero modules in
        `src/mokata` import `logging`, which is the other side of the same measurement."""
        importers = []
        # CORPUS: THE WORKING TREE — the same corpus as `_print_sites`, for the same reason.
        for base, _dirs, files in os.walk(SRC):
            if "__pycache__" in base:
                continue
            for fn in sorted(f for f in files if f.endswith(".py")):
                path = os.path.join(base, fn)
                with io.open(path, encoding="utf-8") as fh:
                    tree = ast.parse(fh.read(), filename=path)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        if any(a.name == "logging" for a in node.names):
                            importers.append(_support.posix_rel(path, SRC))
                    elif isinstance(node, ast.ImportFrom) and node.module == "logging":
                        importers.append(_support.posix_rel(path, SRC))
        self.assertEqual(sorted(set(importers)), [],
                         "a module imports `logging` — if the structured-logging swap is being "
                         f"built after all, this guard and R1.S1d's re-grade both need revisiting: {importers}")


if __name__ == "__main__":
    unittest.main()
