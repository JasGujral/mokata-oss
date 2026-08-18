"""0.0.18 lane D stage 14 — the Neo4j `code_graph` backend is REMOVED, and the gate OPENS.

★★ THIS IS THE FIRST STAGE IN THE LANE WHOSE JOB IS TO MAKE THE GATE GO GREEN, AND THAT INVERTS
THE TEST BAR. Stages 10–13 each asserted the gate STILL CLOSED — `overdue`, with an exact
non-empty implemented set — which is a strong assertion because the set it names is a fact about
the tree. `landed` is the opposite shape: it is the ABSENCE of a fact, and an absence is what a
broken derivation also produces. So the opening is graded three ways below, and none of them is
"the suite went green".

⚠ **THE VERDICT'S ONLY EXISTING ASSERTION IS A FIXTURE, AND THE LIVE TRIPWIRE CANNOT SEE THE
DIFFERENCE.** `REMOVAL_LANDED` is named in exactly two files. `_deprecation_removal.py` defines
it; `test_stage9_removal_release.py` asserts it ONCE —

    def test_no_implementation_left_is_landed_whatever_the_version(self):
        for version in ("0.0.17", "0.0.18", "0.0.99"):
            self.assertEqual(R.removal_state(version, "0.0.18", frozenset()), R.REMOVAL_LANDED)

— over a HAND-BUILT `frozenset()`. That grades the arithmetic and nothing about this tree. The
live tripwire does read the tree, but its assertion is `assertIn(state, (PENDING, LANDED))`, a
DISJUNCTION: with `mokata.__version__` at 0.0.17 and `neo4j` still implemented the state was
`pending`, so the tripwire was already green before this stage and is still green after it. **It
cannot distinguish the gate opening from the gate never having been due.** `TestTheGateOpens`
below is the assertion that can: the implemented set is derived by the REAL import probe, asserted
as an EXACT empty frozenset, and the verdict is asserted by EQUALITY to `landed` — at the version
in the tree AND at the release the declaration promises, which is the one the cut will run at.

⚠ **AND THE REAL PROBE IS GRADED BEFORE IT IS BELIEVED.** An empty implemented set is also what a
probe that answers "absent" for everything produces, and that probe would open the gate for all
five channels at once while the code was still there. `test_the_probe_still_answers_both_ways`
pins a surviving module PRESENT and the removed one ABSENT through the same `live_probe`, so the
emptiness is the deletion's and not the probe's.

---

WHAT THE USER LOSES, WHICH IS THE OTHER HALF OF THE STAGE.

Nothing, and that is not a figure of speech — it is why this channel needed a THIRD removal-record
class. mokata never held this data: it QUERIED a graph the user's own team populated on the user's
own server. So the lane's inherited remedy shapes all invert one more time. `RemovedNotice` renders
*"install `mokata==0.0.17`, run `mokata migrate neo4j`"* — and `mokata migrate neo4j` has never
existed, because `CHANNELS["neo4j"].migration` was the empty string for the channel's entire
deprecated life. `RemovedFileNotice` renders a path under `.mokata/` — and there is none. Even
`native-memory`'s *"an EXTERNAL store — mokata never held its data"* does not fit, because its
remedy is still a downgrade-and-migrate: those memories were CANONICAL. A code graph is DERIVED,
so the canonical graph re-derives it and the honest remedy is `mokata reconfigure --remove neo4j`
— a command that exists, that this suite RUNS, and that costs the user nothing.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import ast
import contextlib
import importlib
import io
import json
import os
import re
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)
import _deprecation_removal as R
from _removed_channel_fixture import NEO4J_TOOL_BLOCK, plant_neo4j_graph_chain

import mokata
from mokata import MOKATA_DIR, deprecation as D, profiles, schema
from mokata.config import Surface
from mokata.init import init_repo
from mokata.knowledge import GrepBackend
from mokata.knowledge.ast_backend import AstBackend
from mokata.knowledge.graph_backend import CodeReviewGraphBackend
from mokata.knowledge.layer import GRAPH_TOOLS, KnowledgeLayer, select_backends

CHANNEL = "neo4j"
SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
REPO = os.path.dirname(SRC)


def _silent(_):
    pass


@contextlib.contextmanager
def driver_installed():
    """Make `import neo4j` succeed for the body — i.e. BE the user who ran `pip install neo4j`.

    ⚠ THIS IS THE ONLY WAY TO REACH THE DANGEROUS CASE, and finding that out is a measurement
    worth recording. `detect.Detector` resolves `python_module` with `importlib.util.find_spec`,
    so on a machine WITHOUT the driver the router walks straight past `neo4j` to the next entry in
    the user's own chain and the removed provider is never resolved at all. A test that planted the
    manifest and stopped there would grade the harmless half and report the dangerous one green.

    A real file on `sys.path` rather than a `sys.modules` patch, because `find_spec` is what is
    asked and `find_spec` consults the finders, not the module cache."""
    import importlib.util
    import sys
    with tempfile.TemporaryDirectory() as libdir:
        with open(os.path.join(libdir, "neo4j.py"), "w", encoding="utf-8") as fh:
            fh.write("# stands in for the driver a wired user pip-installed\n"
                     "class GraphDatabase:\n"
                     "    @staticmethod\n"
                     "    def driver(uri, auth=None):\n"
                     "        raise AssertionError('nothing may connect after the removal')\n")
        sys.path.insert(0, libdir)
        importlib.invalidate_caches()
        try:
            assert importlib.util.find_spec("neo4j") is not None
            yield
        finally:
            sys.path.remove(libdir)
            sys.modules.pop("neo4j", None)
            importlib.invalidate_caches()


def _repo(d, python=True):
    init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
    if python:
        with open(os.path.join(d, "app.py"), "w", encoding="utf-8") as fh:
            fh.write("def caller():\n    target()\n\n\ndef target():\n    return 1\n")
    return d


class _Res:
    def __init__(self, tool, available=True):
        self.tool, self.available = tool, available


class _Manifest:
    def __init__(self, chain, cfg=None):
        self._chain, self._cfg = list(chain), cfg or {}

    def fallback_order(self, need):
        return list(self._chain)

    def tool_config(self, name):
        return dict(self._cfg)


class _Router:
    """A duck-typed router that resolves `tool` and answers `fallback_order` from `chain`.

    Both are needed together and that is the point: the announcement reads the COMMITTED CHAIN and
    the backend choice reads the RESOLUTION, so a double that supplied only one of them could not
    tell the silent-fallback case from the announced one."""

    def __init__(self, tool, chain=None, cfg=None, detector=None):
        self._tool = tool
        self.manifest = _Manifest(chain if chain is not None else [tool], cfg)
        self.detector = detector

    def resolve(self, need):
        return _Res(self._tool)


# ============================================================ ① THE GATE OPENS

class TestTheGateOpens(unittest.TestCase):
    """The lane's finish line, asserted as an EXACT empty set from the REAL derivation."""

    def test_the_implemented_set_is_exactly_empty(self):
        present = R.present_channels(R.IMPLEMENTATIONS, R.live_probe)
        self.assertEqual(present, frozenset(), (
            "the deletion lane is not finished: %s still implemented" % sorted(present)))

    def test_the_verdict_is_landed_by_equality_not_by_membership(self):
        # ⚠ EQUALITY, and at BOTH versions that matter. The live tripwire in
        # `test_stage9_removal_release` asserts `assertIn(state, (PENDING, LANDED))`, which was
        # already satisfied by PENDING at 0.0.17 — so it cannot witness the gate opening. This can.
        present = R.present_channels(R.IMPLEMENTATIONS, R.live_probe)
        self.assertEqual(
            R.removal_state(mokata.__version__, D.REMOVAL_RELEASE, present), R.REMOVAL_LANDED)
        # And at the release the declaration promises — the one `release.sh`'s preflight runs at,
        # where every prior slice's answer was `overdue` and the cut aborted.
        self.assertEqual(
            R.removal_state(D.REMOVAL_RELEASE, D.REMOVAL_RELEASE, present), R.REMOVAL_LANDED)
        self.assertNotEqual(
            R.removal_state(D.REMOVAL_RELEASE, D.REMOVAL_RELEASE, present), R.REMOVAL_OVERDUE)

    def test_the_probe_still_answers_both_ways(self):
        # §7i, and it is the load-bearing test of this class. An empty implemented set is ALSO what
        # a probe that answered "absent" for everything would produce — and that probe would report
        # the lane finished while five backends sat in the tree. The pair is what grades it.
        self.assertTrue(R.live_probe("mokata.knowledge.layer"))
        self.assertFalse(R.live_probe("mokata.knowledge.neo4j_backend"))
        self.assertTrue(R.live_probe("mokata.knowledge.layer:select_backends"))
        self.assertFalse(R.live_probe("mokata.knowledge.layer:Neo4jGraphClient"))

    def test_every_removed_channel_stays_in_the_map_and_stays_absent(self):
        # The map does NOT shrink when a channel is removed — it is what the probe ranges over, so
        # a deleted entry stops being checked at the moment the check acquires a subject.
        self.assertEqual(sorted(R.IMPLEMENTATIONS), sorted(D.REMOVED))
        self.assertEqual(R.removal_regressions(D.REMOVED, R.IMPLEMENTATIONS, R.live_probe), ())
        self.assertEqual(R.IMPLEMENTATIONS[CHANNEL], "mokata.knowledge.neo4j_backend")

    def test_the_registry_is_still_exactly_covered(self):
        announced = tuple(D.CHANNELS) + tuple(D.REMOVED)
        self.assertEqual(R.registry_drift(announced, R.IMPLEMENTATIONS), ((), ()))
        self.assertEqual(R.stale_notices(D.CHANNELS, frozenset()), ())
        # CHANNELS is EMPTY, stated rather than left to a vacuous loop (see `deprecation.py`).
        self.assertEqual(D.CHANNELS, {})


# ============================================================ ② THE WIRED USER

class TestAWiredNeo4jRepo(unittest.TestCase):
    """A repo whose committed `code_graph` chain still names `neo4j` — planted, because nothing in
    this tree can produce one any more (§7i)."""

    def _wired(self, d, python=True):
        _repo(d, python=python)
        plant_neo4j_graph_chain(d)
        return Surface.load(d)

    def test_the_committed_manifest_still_validates_and_still_loads(self):
        # ★ TRAP 1's ANSWER. Slice 1 removed the `obsidian` DETECT STRATEGY with its backend, which
        # made every manifest naming it INVALID — and `Surface.load` runs before every command,
        # INCLUDING the one the removal notice names, so the remedy was unrunnable. Stage 10 built
        # `REMOVED_DETECT_TYPES` for that. It is NOT needed here and was NOT extended: `neo4j`'s
        # detect type is `python_module`, shared with postgres/pgvector/sqlite, and the schema's
        # referential-integrity rule makes a wired manifest self-contained — the chain entry is
        # backed by a `tools.neo4j` block in the SAME FILE, so nothing at load consults the catalog.
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(self._wired(d).root, MOKATA_DIR, "manifest.json")
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertEqual(schema.validate_manifest(data), [])
            surface = Surface.load(d)                       # the load itself is the assertion
            self.assertEqual(surface.manifest.fallback_order("code_graph"),
                             ["neo4j", "ripgrep", "grep"])

    def test_the_removed_detect_type_list_did_not_grow(self):
        # The other half of the same derivation, and it must be an EXACT set: adding `neo4j` here
        # would have been a member nothing could ever read, and reusing slice 1's mechanism because
        # it was nearby is what slice 2 proved produces a refusal that is false and looks correct.
        self.assertEqual(schema.REMOVED_DETECT_TYPES, ("obsidian",))
        self.assertEqual(NEO4J_TOOL_BLOCK["detect"]["type"], "python_module")
        self.assertIn("python_module", schema.KNOWN_DETECT_TYPES)
        shared = {tid for tid, tool in profiles.TOOL_CATALOG.items()
                  if tool.get("detect", {}).get("type") == "python_module"}
        self.assertGreaterEqual(len(shared), 2, "the detect type is shared — that is why it stays")

    def test_the_refusal_fires_once_with_the_exact_record(self):
        with tempfile.TemporaryDirectory() as d:
            surface = self._wired(d)
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                select_backends(surface.router, root=d)
            self.assertEqual(buf.getvalue().strip(),
                             D.removal_answer(CHANNEL, d).strip())
            # once per repo, keyed on the removal DECISION (`filed=`), exactly like every other
            # removed channel — a second command must not re-nag.
            buf2 = io.StringIO()
            with contextlib.redirect_stderr(buf2):
                select_backends(surface.router, root=d)
            self.assertEqual(buf2.getvalue(), "")

    def test_the_answer_is_the_ast_floor_named_ast_never_the_lexical_floor_named_neo4j(self):
        # ★ THE NO-SILENT-FALLBACK PROOF, and the shape it would have taken. Delete the branch and
        # nothing else and this resolution falls through `select_backends`' last line, which builds
        # `GrepBackend(name=res.tool)` — the EMERGENCY lexical floor wearing the removed backend's
        # name, on a Python repo that has an AST floor sitting right there. The user silently loses
        # structural answers and is told `code graph: floor 'neo4j'`.
        with tempfile.TemporaryDirectory() as d:
            surface = self._wired(d)
            with driver_installed(), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(surface.router.resolve("code_graph").tool, CHANNEL,
                                 "the router did not even reach the removed entry")
                primary, fallback = select_backends(surface.router, root=d)
            self.assertIsInstance(primary, AstBackend)
            self.assertEqual(primary.name, "ast")
            self.assertNotEqual(primary.name, CHANNEL)
            self.assertIsNone(fallback)
            layer = KnowledgeLayer(primary, fallback)
            self.assertFalse(layer.uses_graph)
            self.assertNotIn(CHANNEL, layer.backend_name)
            # and it ANSWERS — the floor is canonical here, not a consolation
            res = layer.callers("target")
            self.assertTrue([r for r in res.references if r.symbol == "caller"],
                            "the AST floor did not answer a real call edge")

    def test_a_zero_python_repo_falls_to_grep_named_grep(self):
        # The same property where the AST floor cannot answer: the name must still be the floor's.
        with tempfile.TemporaryDirectory() as d:
            surface = self._wired(d, python=False)
            with driver_installed(), contextlib.redirect_stderr(io.StringIO()):
                primary, _fb = select_backends(surface.router, root=d)
            self.assertIsInstance(primary, GrepBackend)
            self.assertEqual(primary.name, "grep")

    def test_without_the_driver_the_chain_resolves_past_it_and_is_still_announced(self):
        # ⚠ TWO FACTS, AND ONLY ONE OF THEM IS THE DANGEROUS ONE (§7g). With no driver installed
        # the detector reads `neo4j` absent, so the router never resolves it and the user gets the
        # next entry of their OWN chain — `ripgrep` here, because the chain the how-to told them to
        # write has no `ast` in it. That is their config being honoured, not a degrade. What must
        # STILL happen is the announcement, because the announcement reads the COMMITTED CHAIN and
        # not the resolution: a repo wired to a removed provider is told so whether or not the
        # driver that would have reached it happens to be on this machine.
        with tempfile.TemporaryDirectory() as d:
            surface = self._wired(d)
            self.assertNotEqual(surface.router.resolve("code_graph").tool, CHANNEL)
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                primary, _fb = select_backends(surface.router, root=d)
            self.assertIn("REMOVED in mokata", buf.getvalue())
            self.assertNotEqual(primary.name, CHANNEL)

    def test_nothing_on_disk_is_destroyed(self):
        with tempfile.TemporaryDirectory() as d:
            surface = self._wired(d)
            path = os.path.join(d, MOKATA_DIR, "manifest.json")
            before = open(path, "rb").read()
            src_before = open(os.path.join(d, "app.py"), "rb").read()
            with contextlib.redirect_stderr(io.StringIO()):
                select_backends(surface.router, root=d)
            self.assertEqual(open(path, "rb").read(), before,
                             "the refusal rewrote the user's manifest")
            self.assertEqual(open(os.path.join(d, "app.py"), "rb").read(), src_before)
            # the chain still names it — the refusal REPORTS, it does not silently self-heal (P2)
            self.assertIn(CHANNEL, json.loads(before)["capabilities"]["code_graph"]["fallback"])

    def test_the_named_remedy_actually_runs_and_clears_the_wiring(self):
        # ⚠ RUN, NOT NAMED — stage 10's finding, and the remedy is DERIVED FROM THE RECORD rather
        # than typed here, so a record that started naming a command nobody can run goes red.
        from mokata import cli
        remedy = D.REMOVED[CHANNEL].remedy
        self.assertIn("mokata reconfigure --remove neo4j", remedy)
        with tempfile.TemporaryDirectory() as d:
            self._wired(d)
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                rc = cli.main(["reconfigure", "--remove", CHANNEL, "--yes", "--path", d])
            self.assertEqual(rc, 0, "the remedy the record names did not run")
            after = Surface.load(d)
            self.assertNotIn(CHANNEL, after.manifest.fallback_order("code_graph"))
            self.assertNotIn(CHANNEL, after.manifest.tools)   # no residue: the tool block too
            # …and the repo is now SILENT, with nothing left to announce, answering from whatever
            # the user's own chain names.
            #
            # ⚠ NOT ASSERTED: that this lands on the AST floor. It does not, and the reason is a
            # finding rather than a defect of the remedy — the chain
            # `docs/how-to/use-a-codebase-graph.md` told neo4j users to write was
            # `["neo4j","ripgrep","grep"]`, which predates GR.S2 promoting `ast` to a routable
            # provider and therefore never named it. While the dead entry is present the refusal
            # routes to `_floor_backend` (the AST floor on a Python repo); once it is cleared the
            # router honours the chain the user actually committed. Asserting AST here would have
            # pinned a floor the manifest does not ask for.
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                primary, _fb = select_backends(after.router, root=d)
            self.assertEqual(buf.getvalue(), "")
            self.assertNotEqual(primary.name, CHANNEL)
            self.assertIsNotNone(KnowledgeLayer(primary).callers("target"))

    def test_migrate_answers_the_channel_by_name_rather_than_as_a_typo(self):
        # Stage 12's C01 mechanism, collected for free by a channel removed AFTER it: `choices` is
        # the REMOVED registry, so nobody gets `invalid choice`. Exit 1 — no migration happened.
        from mokata import cli
        with tempfile.TemporaryDirectory() as d:
            self._wired(d)
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                rc = cli.main(["migrate", CHANNEL, "--path", d])
            self.assertEqual(rc, 1)
            self.assertEqual(err.getvalue().strip(), D.removal_answer(CHANNEL, d).strip())


# ============================================================ ③ THE RECORD'S SHAPE

class TestTheRemovalRecord(unittest.TestCase):
    """A THIRD record class, and the two inherited ones REFUSE this channel by name."""

    def test_the_record_is_the_derived_kind(self):
        self.assertIsInstance(D.REMOVED[CHANNEL], D.RemovedDerivedNotice)
        self.assertFalse(D.is_removed_file_channel(CHANNEL))
        self.assertEqual(D.REMOVED[CHANNEL].removed, D.REMOVAL_RELEASE)
        # DERIVED, never typed: a literal here is `REMOVAL-RELEASE-ALREADY-PASSED`
        # in the suite that grades it (stage 9), and `notice_pins` reds on one.

    def test_the_two_inherited_accessors_refuse_it(self):
        # Not "return something wrong" — RAISE. `removed_notice` carries the downgrade sentence and
        # `removed_file_path` composes a `.mokata/` location; either rendered over this channel is
        # a correct-LOOKING refusal that is false, which is slice 2's class.
        with self.assertRaises(KeyError):
            D.removed_notice(CHANNEL)
        with self.assertRaises(KeyError):
            D.removed_file_path(CHANNEL, "/tmp")
        with self.assertRaises(KeyError):
            D.removed_file_report(CHANNEL, "/tmp/x", True)

    def test_the_record_names_no_downgrade_and_no_migration(self):
        text = D.removal_answer(CHANNEL, "/tmp")
        self.assertNotIn("pip install 'mokata==", text)
        self.assertNotIn("mokata migrate", text)
        self.assertNotIn(D.LAST_SHIPPING_RELEASE, text)
        self.assertNotIn(".mokata/", text)
        # what it DOES say: their server is untouched, the canonical graph answers, one command.
        self.assertIn("untouched", text)
        self.assertIn("DERIVED data", text)
        self.assertIn("mokata reconfigure --remove neo4j", text)
        self.assertIn(D.REMOVED[CHANNEL].removed, text)

    def test_the_record_does_not_lead_with_nothing_was_destroyed(self):
        # P22. mokata never held this data, so "nothing was destroyed" is trivially true — a first
        # clause that spends the notice on a tautology. The record leads with what CHANGED.
        text = D.removal_answer(CHANNEL, "/tmp")
        first = text.split(".")[0]
        self.assertIn("REMOVED in mokata", first)
        self.assertNotIn("deleted nothing", first)

    def test_it_leaks_no_uri_or_credential(self):
        text = D.removal_answer(CHANNEL, "/tmp")
        self.assertNotIn("://", text)
        self.assertNotIn("password", text.lower())
        self.assertNotIn("NEO4J_", text)

    def test_removed_channels_in_filters_by_kind_in_both_directions(self):
        # ⚠ THE ARGUMENT IS REQUIRED, and this is what a default would have cost. Two capabilities
        # now ask this question about two record types; a `kind` defaulted to `RemovedNotice` would
        # answer the graph chain with the memory filter and return `()` for a chain that names a
        # removed graph provider — the silent fallback, produced by the guard meant to catch it.
        chain = ["neo4j", "ripgrep", "grep"]
        self.assertEqual(D.removed_channels_in(chain, D.RemovedDerivedNotice), (CHANNEL,))
        self.assertEqual(D.removed_channels_in(chain, D.RemovedNotice), ())
        mem = ["obsidian", "sqlite"]
        self.assertEqual(D.removed_channels_in(mem, D.RemovedNotice), ("obsidian",))
        self.assertEqual(D.removed_channels_in(mem, D.RemovedDerivedNotice), ())
        with self.assertRaises(TypeError):
            D.removed_channels_in(chain)                       # no default to fall back on

    def test_the_memory_backstop_no_longer_answers_a_graph_channel_with_a_keyerror(self):
        from mokata.memory.selection import _select_raw_backend
        with self.assertRaises(D.RemovedChannelError):
            _select_raw_backend("obsidian", "/tmp", {}, None, None)
        # `neo4j` is not a memory backend: the backstop must not claim it, and must not KeyError.
        with tempfile.TemporaryDirectory() as d:
            backend = _select_raw_backend(CHANNEL, d, {}, None, None)
            self.assertIsNotNone(backend)

    def test_the_transport_backstop_no_longer_claims_a_graph_channel(self):
        # The same membership-vs-type correction one module over. `session_transport`'s refusal
        # says in its own docstring that it is "derived from the REMOVED registry, never a list of
        # names" — true of the SET and false of the SHAPES in it the moment a record without a
        # `.mokata/` location joined, because the refusal resolves one to report where the bundles
        # are. A claim about a derivation is still a claim (§7h).
        from mokata.session_transport import _refuse_removed_kind
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(_refuse_removed_kind(CHANNEL, d))
            with self.assertRaises(D.RemovedChannelError):
                _refuse_removed_kind("vault", d)


# ============================================================ ④ WHAT SURVIVES

class TestWhatSurvives(unittest.TestCase):
    """Deletion is graded by what SURVIVES: a test that only asserts absence passes on an empty
    repo."""

    def test_graph_tools_is_exactly_the_two_survivors(self):
        self.assertEqual(GRAPH_TOOLS, ("code-review-graph", "serena"))

    def test_both_survivors_still_resolve_to_a_real_graph(self):
        class _Client:
            def query(self, kind, target, root, depth=1):
                return [{"path": "a.py", "line": 1, "symbol": target, "snippet": ""}]
        for tool in GRAPH_TOOLS:
            with tempfile.TemporaryDirectory() as d:
                primary, fallback = select_backends(
                    _Router(tool, chain=[tool, "grep"]), root=d, client=_Client())
                self.assertIsInstance(primary, CodeReviewGraphBackend)
                self.assertEqual(primary.name, tool)
                self.assertTrue(primary.is_graph)
                self.assertIsNotNone(fallback)

    def test_the_ast_floor_and_the_grep_floor_both_still_answer(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            layer = KnowledgeLayer(AstBackend(root=d, grep=GrepBackend(root=d, name="grep")))
            res = layer.callers("target")
            self.assertTrue([r for r in res.references if r.symbol == "caller"])
            self.assertIsNotNone(KnowledgeLayer(GrepBackend(root=d, name="grep")).callers("target"))

    def test_the_public_surface_lost_exactly_three_names(self):
        import mokata.knowledge as K
        gone = {"Neo4jGraphClient", "build_neo4j_client", "Neo4jUnavailable"}
        self.assertEqual(gone & set(K.__all__), set())
        for name in gone:
            self.assertFalse(hasattr(K, name), name)
        # the neighbours in the same export block SURVIVE — absence-only would pass on a stub
        for name in ("CodeReviewGraphBackend", "GraphQueryClient", "SubprocessGraphClient",
                     "CodeReviewGraphClient", "CrgUnavailable", "GRAPH_TOOLS", "KnowledgeLayer",
                     "select_backends", "AstBackend", "GrepBackend", "make_graph_scorer"):
            self.assertIn(name, K.__all__, name)
            self.assertTrue(hasattr(K, name), name)

    def test_the_degraded_capability_hierarchy_is_untouched(self):
        # ⚠ `Neo4jUnavailable` subclassed `DegradedCapability`, so the question is whether any
        # surviving handler caught it GENERICALLY — a broad `except DegradedCapability` would
        # change behaviour when a subclass stops being raised.
        from mokata.errors import DegradedCapability, MokataError
        from mokata.knowledge.crg_client import CrgUnavailable
        from mokata.knowledge.query import BackendError
        from mokata.memory.backends import PostgresUnavailable
        for cls in (CrgUnavailable, BackendError, PostgresUnavailable):
            self.assertTrue(issubclass(cls, DegradedCapability), cls.__name__)
        self.assertTrue(issubclass(DegradedCapability, MokataError))
        self.assertFalse(issubclass(D.RemovedChannelError, DegradedCapability))

    def test_no_src_module_catches_degraded_capability_generically(self):
        # Derived over the AST of every `src/` module, not grepped: the claim above is only worth
        # anything if nothing anywhere relied on catching the family rather than a member.
        # CORPUS: THE WORKING TREE. The question is "does the code that will be BUILT catch this
        # family generically", and what gets built is the tree on disk — an index read would miss
        # an uncommitted handler, which is precisely the state this stage is in.
        offenders = []
        for base, _dirs, files in os.walk(os.path.join(SRC, "mokata")):
            for name in files:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(base, name)
                with open(path, encoding="utf-8") as fh:
                    try:
                        tree = ast.parse(fh.read())
                    except SyntaxError:              # pragma: no cover
                        continue
                for node in ast.walk(tree):
                    if not isinstance(node, ast.ExceptHandler) or node.type is None:
                        continue
                    names = ([node.type] if not isinstance(node.type, ast.Tuple)
                             else list(node.type.elts))
                    for n in names:
                        label = n.attr if isinstance(n, ast.Attribute) else getattr(n, "id", "")
                        if label == "DegradedCapability":
                            offenders.append((os.path.relpath(path, SRC), node.lineno))
        self.assertEqual(offenders, [], "a generic DegradedCapability catch exists: removing a "
                                        "subclass may have changed behaviour there")

    def test_the_do_not_build_rule_still_forbids_the_name(self):
        # doc 85 §6 is satisfied by an ABSENCE, so the forbidden set must keep the member: dropping
        # `neo4j` from it because nothing imports it is the §7i mistake — it would retire the guard
        # in the release that made it true.
        import mokata.memory.edges as E
        import inspect
        tree = ast.parse(inspect.getsource(E))
        imported = {a.name.split(".")[0] for n in ast.walk(tree)
                    if isinstance(n, ast.Import) for a in n.names}
        imported |= {(n.module or "").split(".")[0] for n in ast.walk(tree)
                     if isinstance(n, ast.ImportFrom)}
        self.assertNotIn("neo4j", imported)
        # ⚠ AGAINST THE CANONICAL SET, NOT A COPY OF IT. The first version of this test re-derived
        # the import check here and asserted its own answer, so dropping `neo4j` from the ACTUAL
        # guard in `test_db_s7a_edge_substrate` changed nothing it could see — a mutant survived on
        # that. The rule lives in one place; this reads it there.
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "test_db_s7a_edge_substrate.py"), encoding="utf-8") as fh:
            guard = fh.read()
        sets = [chunk.split("}", 1)[0] for chunk in guard.split("imported & {")[1:]]
        forbidden = next((s for s in sets if "graphviz" in s), "")
        self.assertIn('"neo4j"', forbidden,
                      "the do-NOT-build set dropped the member this release just made true — "
                      "retiring the rule in the release that satisfies it (§7i)")

    def test_no_surviving_prose_cites_the_deleted_module(self):
        # ★ TRAP 4 AS A PROPERTY RATHER THAN FIVE EDITS. A prose citation is a CLAIM, and a claim
        # whose referent you delete becomes false the moment you commit — which is what happened to
        # `memory/edges.py`'s *"`neo4j_backend.py` is the deprecated counter-example"*. The general
        # form is gradable where the wording is not: no comment or docstring in `src/` may cite a
        # `mokata` module file that does not exist.
        #
        # CORPUS: THE WORKING TREE — a citation added in an uncommitted file is just as false.
        self.assertEqual(cited_missing_modules(_src_corpus()), ())

    def test_the_citation_sweep_finds_a_planted_offender(self):
        # §7i: the tree has no offender left, so the sweep is graded against one — and against a
        # citation of a module that DOES exist, which must not be convicted.
        known = {"layer.py", "m.py"}
        self.assertEqual(
            cited_missing_modules({"m.py": "# `neo4j_backend.py` is the counter-example"}, known),
            (("m.py", 1, "neo4j_backend.py"),))
        self.assertEqual(
            cited_missing_modules({"m.py": "# see `layer.py` for the resolution"}, known), ())
        # un-backticked history is not a citation — the fix both real offenders took
        self.assertEqual(
            cited_missing_modules({"m.py": "# neo4j_backend.py was the counter-example"}, known),
            ())
        # and the exemption is graded for exactness: a stale one grants a pass to nothing
        for path, names in CITATION_EXEMPT.items():
            for name in names:
                self.assertIn(name, _src_corpus()[path],
                              "a declared citation exemption no longer appears in its file")


_MODULE_CITATION = re.compile(r"`([a-z_][a-z0-9_]*\.py)`")


# A citation that is DECLARED not to be one, graded for exactness so a stale exemption reds.
CITATION_EXEMPT = {
    # A generic FORM in prose about basename patterns — "(`test_x.py`, `x_test.go`, `x.spec.ts`)".
    # `x` is a metasyntactic placeholder, not a file, and the three siblings beside it are not
    # Python so the pattern never sees them. Exempted as a SITE, never a file.
    "mokata/gate_hook.py": frozenset({"test_x.py"}),
}


def _src_corpus():
    """`{relpath: text}` for every `src/mokata` module."""
    # CORPUS: THE WORKING TREE — a comment added and not yet committed cites just as falsely.
    out = {}
    for base, _dirs, files in os.walk(os.path.join(SRC, "mokata")):
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            path = os.path.join(base, name)
            with open(path, encoding="utf-8") as fh:
                # ⚠ `posix_rel`, not `os.path.relpath`. `CITATION_EXEMPT` is keyed
                # `mokata/gate_hook.py`, so an OS-separated key `KeyError`s on Windows and only on
                # Windows (tests/test_windows_shell_and_paths.py, cause B).
                out[_support.posix_rel(path, SRC)] = fh.read()
    return out


# The trees a citation may honestly name. DECLARED, and the reason is a mutant that survived.
_SOURCE_ROOTS = ("src", "tests", "scripts")


def _repo_module_names():
    """Every `.py` basename under the SOURCE trees — a citation may name a test or a script.

    ⚠ NOT "every `.py` in the repo", AND THE DIFFERENCE IS A BUILD ARTEFACT. The first version
    walked `REPO` with a blocklist of `.git`/`.venv`/`__pycache__`, and `build/lib/mokata/knowledge/
    neo4j_backend.py` — a stale wheel-build snapshot of a PREVIOUS release, untracked and sitting
    in the tree — made the deleted module read as PRESENT. The sweep then reported a clean corpus
    and the mutant that restores the false citation survived. Doc 85 §7j exactly: the PREDICATE was
    derived and the SCOPE was typed, so "the repo" quietly meant "the repo plus a copy of last
    release". An ALLOW-list of source roots cannot acquire a build directory the way a blocklist
    can fail to exclude one."""
    # CORPUS: THE WORKING TREE. Whether a cited module is there to be OPENED is answered by the
    # tree on disk — a module added and not yet committed is just as citable. ⚠ And an untracked
    # directory is exactly what got this wrong the first time, which is why the scope is NAMED
    # (`_SOURCE_ROOTS`) rather than filtered: an index read would have been the other principled
    # answer, and it would have missed a module this stage itself has not committed.
    names = set()
    for root in _SOURCE_ROOTS:
        for base, dirs, files in os.walk(os.path.join(REPO, root)):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            names.update(f for f in files if f.endswith(".py"))
    return names


def cited_missing_modules(sources, known=None, exempt=None):
    """`[(path, line, cited)]` — backticked `<name>.py` citations naming a module that is not in
    the tree, over a SUPPLIED corpus.

    ★ THE CLASS TRAP 4 IS ABOUT, MADE MECHANICAL. `memory/edges.py` justified the do-NOT-build rule
    with *"`neo4j_backend.py` is the deprecated counter-example"* — true until this stage deleted
    that file, after which the sentence pointed at nothing and the rule stood with no reason behind
    it. Prose is a CLAIM (§7h); a claim whose referent you delete goes false at the commit, and
    nothing in the tree said so. The WORDING is not gradable and pinning it would grade the
    wording; the REFERENT is.

    ⚠ BACKTICKS ARE THE PREDICATE, AND THAT IS A CONVENTION THIS STAGE IS STATING RATHER THAN
    ASSUMING: a backticked `name.py` asserts that the file EXISTS; the same name in running prose
    is history. Both of the offenders this sweep found on its first run were fixed by dropping the
    backticks, not by dropping the sentence — the sentences are the reasons the rules exist.

    `known` is every `.py` basename in the REPO (a module may honestly cite a test or a script),
    derived not typed. `exempt` is DECLARED and graded for exactness — a site, never a file."""
    known = _repo_module_names() if known is None else known
    exempt = CITATION_EXEMPT if exempt is None else exempt
    found = []
    for path in sorted(sources):
        allowed = exempt.get(path, frozenset())
        for i, line in enumerate((sources[path] or "").splitlines(), start=1):
            for cited in _MODULE_CITATION.findall(line):
                if cited not in known and cited not in allowed:
                    found.append((path, i, cited))
    return tuple(found)


# ============================================================ ⑤ THE SELL SURFACES

def sell_offenders(sources, token=CHANNEL):
    """`[(path, line, text)]` — every line of a supplied `{path: text}` that INSTALLS or OFFERS
    `token`, as opposed to recording that it was removed.

    ★ TRAP 2, AS A PURE FUNCTION OVER A SUPPLIED CORPUS (§7i). A sell surface is not reachable by
    an import-graph sweep — `pip install neo4j` is a STRING in a wizard's hint table, a `[neo4j]`
    key in packaging metadata, a service block in a workflow, a bash fence in a shipped template.
    On the day this stage started, a brand-new user running the first-run wizard was still being
    told to `pip install neo4j` and export three env vars for the backend this release deletes.

    ⚠ THE PREDICATE IS SELL-vs-RECORD, NOT MENTION. A removal record must be free to NAME the thing
    it removed — `docs/how-to/use-a-codebase-graph.md` now carries a whole admonition about it, and
    a sweep that convicted mentions would force the docs to go silent about a removal, which is the
    opposite of the lane's discipline. What is forbidden is an INSTRUCTION.

    ⚠ IT READS ACTIVE LINES, NOT ALL LINES, AND THAT IS NOT A CONVENIENCE. In `.py` / `.toml` /
    `.yml` a `#` comment is provenance addressed to a maintainer — this very stage had to write
    "a brand-new user was still being handed `pip install neo4j`" into `onboarding.py` to say what
    was deleted and why, and a sweep that convicted it would forbid recording the removal in the
    file the removal happened in. Markdown is NOT stripped: it has no comment syntax, `#` is a
    heading, and every line of it is addressed to a user.
    """
    verbs = ("pip install", "install_hints", '"neo4j"]', "image: neo4j", "optional-dependencies")
    stripped = (".py", ".toml", ".yml", ".yaml")
    found = []
    for path in sorted(sources):
        code = path.endswith(stripped)
        for i, line in enumerate((sources[path] or "").splitlines(), start=1):
            active = line.split("#")[0] if code else line
            low = active.lower()
            if token not in low:
                continue
            if "remove" in low:
                continue                              # a record, not an offer
            if any(v in low for v in verbs) or low.strip().startswith("neo4j ="):
                found.append((path, i, line.strip()))
    return tuple(found)


class TestNothingStillSellsIt(unittest.TestCase):
    SHIPPED = ("pyproject.toml", ".github/workflows/ci.yml", "requirements/README.md",
               "PRIVACY.md", "src/mokata/onboarding.py", "src/mokata/profiles.py",
               "src/mokata/templates/commands/reconfigure.md",
               "docs/how-to/install-mokata.md", "docs/how-to/first-run.md",
               "docs/how-to/use-a-codebase-graph.md", "docs/concepts/knowledge.md",
               "docs/developer-guide.md", "docs/how-it-works/index.md",
               "docs/tutorials/differentiators-in-action.md")

    def _corpus(self):
        out = {}
        for rel in self.SHIPPED:
            path = os.path.join(REPO, rel)
            if os.path.exists(path):
                with open(path, encoding="utf-8", errors="replace") as fh:
                    out[rel] = fh.read()
        return out

    def test_the_sweep_finds_a_planted_offender(self):
        # §7i — the guard is graded against violations, because the tree now has none.
        planted = {
            "wizard.py": 'INSTALL_HINTS = {\n    "neo4j": "pip install neo4j   # then set …",\n}',
            "pyproject.toml": "[project.optional-dependencies]\nneo4j = [\"neo4j>=5.0\"]",
            "ci.yml": "      neo4j:\n        image: neo4j:5",
            "guide.md": "Run `pip install neo4j` and export NEO4J_URI.",
        }
        hits = {p for p, _l, _t in sell_offenders(planted)}
        self.assertEqual(hits, {"wizard.py", "pyproject.toml", "ci.yml", "guide.md"})

    def test_a_comment_recording_the_removal_is_not_an_offender_but_markdown_is(self):
        # The comment-stripping half, graded by an offender only it can see (§7f): the SAME
        # sentence is provenance in a `.py` and an instruction in a `.md`, and a sweep that
        # cannot tell them apart is one somebody turns off.
        # ⚠ THE OFFENDER CARRIES A `#`, AND THE FIRST VERSION OF THIS TEST DID NOT — which made
        # it pass with the stripping switched off entirely (a mutant survived on exactly that).
        # A markdown line with no `#` in it is identical before and after stripping, so it grades
        # the predicate and nothing about the extension rule. The `#` is where the two diverge.
        sentence = "# Wiring: run `pip install neo4j`, then export NEO4J_URI"
        self.assertEqual(sell_offenders({"m.py": sentence}), (),
                         "a maintainer comment recording the removal is not an instruction")
        self.assertEqual(len(sell_offenders({"m.md": sentence})), 1,
                         "a markdown heading IS addressed to a user; `#` is not a comment there")

    def test_a_removal_record_is_not_an_offender(self):
        # The other direction, and it is what keeps the sweep usable: the docs MUST be able to say
        # a thing was removed and how to clear it.
        record = {"doc.md": "Clear it with `mokata reconfigure --remove neo4j`, then re-index.\n"
                            "The `[neo4j]` extra was removed in 0.0.18."}
        self.assertEqual(sell_offenders(record), ())

    def test_no_shipped_surface_still_offers_it(self):
        self.assertEqual(sell_offenders(self._corpus()), ())

    def test_the_wizard_no_longer_offers_or_hints_it(self):
        from mokata import onboarding as OB
        self.assertNotIn(CHANNEL, OB.OPTIONAL_INTEGRATIONS)
        self.assertNotIn(CHANNEL, OB.INSTALL_HINTS)
        # the surviving offers are pinned — absence alone would pass on an emptied tuple
        self.assertEqual(OB.OPTIONAL_INTEGRATIONS,
                         ("code-review-graph", "serena", "postgres"))
        self.assertEqual(set(OB.INSTALL_HINTS), set(OB.OPTIONAL_INTEGRATIONS))

    def test_the_catalog_and_the_packaging_extra_are_both_gone(self):
        self.assertNotIn(CHANNEL, profiles.TOOL_CATALOG)
        with open(os.path.join(REPO, "pyproject.toml"), encoding="utf-8") as fh:
            pyproject = fh.read()
        self.assertNotIn('neo4j = ["neo4j>=5.0"]', pyproject)
        for extra in ("schema", "postgres", "embeddings", "mcp"):   # survivors, pinned
            self.assertIn("%s = [" % extra, pyproject)

    def test_the_profiles_module_stays_inside_the_release_pin_guard(self):
        # ⚠ §7j. `profiles.py` stopped importing `REMOVAL_RELEASE` when the marked entry left, and
        # `src_release_pins` selects its corpus by deprecation VOCABULARY — so a file that also
        # stopped mentioning deprecation would silently fall OUT of the guard that exists to stop a
        # release literal reappearing on the surface `init_repo` writes to a user's disk.
        with open(os.path.join(SRC, "mokata", "profiles.py"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("deprecat", text.lower())
        self.assertEqual(R.src_release_pins({"mokata/profiles.py": text}), ())


# ============================================================ ⑥ BACKCOMPAT SWEEP (E2)

class TestBackcompatSweepOnThisStage(unittest.TestCase):
    def test_no_module_still_imports_the_deleted_backend(self):
        self.assertIsNone(importlib.util.find_spec("mokata.knowledge.neo4j_backend"))
        # CORPUS: THE WORKING TREE. Same question, same answer: an import of a deleted module in
        # an uncommitted file would still break the build, so the tree is the honest corpus.
        offenders = []
        for base, _dirs, files in os.walk(os.path.join(SRC, "mokata")):
            for name in files:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(base, name)
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
                try:
                    tree = ast.parse(text)
                except SyntaxError:                  # pragma: no cover
                    continue
                for node in ast.walk(tree):
                    mod = ""
                    if isinstance(node, ast.ImportFrom):
                        mod = node.module or ""
                    elif isinstance(node, ast.Import):
                        mod = ",".join(a.name for a in node.names)
                    if "neo4j" in mod:
                        offenders.append((os.path.relpath(path, SRC), node.lineno))
        self.assertEqual(offenders, [])

    def test_no_surviving_path_reaches_it_by_another_name(self):
        # A `__getattr__` shim would slip past a grep; attribute lookup catches it.
        import mokata.knowledge as K
        import mokata.knowledge.layer as L
        for name in ("Neo4jGraphClient", "build_neo4j_client", "connect_neo4j_client",
                     "Neo4jUnavailable"):
            self.assertFalse(hasattr(K, name), name)
            self.assertFalse(hasattr(L, name), name)

    def test_no_accommodation_was_left_behind_for_the_removed_channel(self):
        # E2 on this stage's own files: the removal added no version branch, no legacy reader and
        # no second code path — the ONE thing it added is a record, which is §7d's exception.
        import inspect
        import mokata.knowledge.layer as L
        # Over CODE string constants (docstrings excluded, comments never reach the AST) — the
        # same distinction `version_literals` draws, and for the same reason: prose about a
        # historical value is not a value, and this module now has prose about one.
        self.assertEqual(R.version_literals(inspect.getsource(L)), (),
                         "a release literal appeared in the resolution path")
        self.assertEqual(inspect.getsource(L).count('== "neo4j"'), 0)
        # and `deprecation.py` opens no file — a record that learned to read is a legacy reader
        tree = ast.parse(inspect.getsource(D))
        calls = {n.func.id for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        self.assertNotIn("open", calls)


if __name__ == "__main__":
    unittest.main()
