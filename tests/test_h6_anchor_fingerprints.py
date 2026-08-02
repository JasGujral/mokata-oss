"""H-6 S1 — the DURABLE anchor→fingerprint record, and the ONE verdict every H-6 surface reads.

The structural gap this closes: GR.S4 already fingerprints files, but only into a SESSION-scoped
`StateStore` key (`FRESHNESS_INDEX_PREFIX + session_id`, `knowledge/freshness.py:41`). A record that
dies with the session cannot answer "has this changed since we last had reason to believe it was
current" — the only question H-6 asks. So the record outlives the session, and that is slice S1.

What is pinned here (the H-6 plan of record, doc 02):

  P2  DURABLE ACROSS SESSIONS, AND NO DURABLE FACT WRITE. A record minted under one session id is
      read under another. Nothing on this path writes a memory item, a config, or code — the leaf
      under `.mokata/temp_local/` is derived run-state (decision #5) and every byte of it is
      re-derivable by re-hashing the repo. Without it, H-6 is GR.S4 with extra steps.
  P6  NO BASELINE IS NO OPINION. No recorded fingerprint / an unreadable file / a file absent from
      this tree / a shape that cannot be resolved ⇒ DECLINED, never MOVED. Without it, the first
      run after install proposes staleness on every anchor in the repo — a claim about a change
      nobody observed.
  P3  PATH-SHAPED ANCHORS FIRE OFF THE FINGERPRINT ALONE (decision #1, LOCKED). A changed file hash
      is a verifiable fact; no graph, no layer, no resolver is consulted. Proven separately from the
      symbol arm, deliberately — one test asserting both would let either carry the other.
  P4  SYMBOL-SHAPED ANCHORS REQUIRE THE AUTHORITATIVE GRAPH and decline on the AST/grep floor. The
      defining file is named by the graph or it is named by nobody.
  P1  ONE TRIPWIRE. The comparison is `knowledge.freshness.fingerprint_forces_refresh` — H-6 wakes
      the pre-named hook rather than growing a second one.

NOTE on the short class names: the self-protect entropy backstop (`84:68`) blocks long CamelCase
test identifiers. Names are kept short rather than carrying a `secret ignore` for each one.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata.knowledge import anchor_fingerprints as AF


# --------------------------------------------------------------------------- doubles
class _Ref:
    def __init__(self, path):
        self.path = path
        self.symbol = ""
        self.line = 1


class _Result:
    def __init__(self, refs, degraded=False):
        self.references = refs
        self.degraded = degraded


class _Graph:
    """An adopted graph that CAN name definition sites (`defs` mapped)."""

    is_graph = True
    supports_resolve = True

    def __init__(self, defs=None, kinds=("defs",), raises=False, degraded=False):
        self._defs = defs or {}
        self._kinds = kinds
        self._raises = raises
        self._degraded = degraded

    def supports_kind(self, kind):
        return kind in self._kinds

    def resolves(self, symbol):
        return symbol in self._defs

    def query(self, kind, target, depth=1):
        if self._raises:
            raise RuntimeError("graph hiccup")
        return _Result([_Ref(p) for p in self._defs.get(target, [])],
                       degraded=self._degraded)


class _Floor:
    """The AST/grep floor — answers every kind, but `is_graph` is False."""

    is_graph = False
    supports_resolve = False

    def __init__(self, defs=None):
        # The floor answers `defs` PERFECTLY WELL — the AST floor really does index definition
        # sites. That is the point: it is not a capability gap, it is an AUTHORITY gap, and a
        # double that answered nothing would let the arm decline for the wrong reason.
        self._defs = defs or {}

    def supports_kind(self, kind):
        return True

    def query(self, kind, target, depth=1):
        return _Result([_Ref(p) for p in self._defs.get(target, [])])


class _Layer:
    def __init__(self, primary=None):
        self.primary = primary


# --------------------------------------------------------------------------- fixture
class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.addCleanup(self.tmp.cleanup)

    def write(self, rel, text):
        ab = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(ab), exist_ok=True)
        with open(ab, "w", encoding="utf-8") as fh:
            fh.write(text)
        return ab


# ================================================================ P6 — no baseline, no opinion
class NoBaseline(_Base):

    def test_first_observation_declines(self):
        self.write("src/a.py", "x = 1\n")
        v = AF.evaluate_anchor("src/a.py", root=self.root)
        self.assertEqual(AF.DECLINED, v.verdict)
        self.assertIn("no recorded fingerprint", v.reason)

    def test_absent_file_declines(self):
        self.write("src/a.py", "x = 1\n")
        AF.record_anchors(self.root, ["src/a.py"])
        os.remove(os.path.join(self.root, "src/a.py"))
        v = AF.evaluate_anchor("src/a.py", root=self.root)
        # NEVER moved. An ABSENT file is genuinely ambiguous — deleted, or simply not in THIS tree
        # (another worktree, a sparse checkout) — while a PRESENT file with a different hash is
        # unambiguous in any tree. The cost is filed as H-6-DELETED-ANCHOR in doc 84, not hidden:
        # widening this needs a git-derived deletion signal, which is a different input.
        self.assertEqual(AF.DECLINED, v.verdict)
        self.assertEqual(AF.DECLINE_ABSENT, v.reason)

    def test_unreadable_file_declines(self):
        self.write("src/a.py", "x = 1\n")
        AF.record_anchors(self.root, ["src/a.py"])
        d = os.path.join(self.root, "src/a.py")
        os.remove(d)
        os.makedirs(d)                                # a directory where a file was: unreadable
        v = AF.evaluate_anchor("src/a.py", root=self.root)
        self.assertEqual(AF.DECLINED, v.verdict)

    def test_corrupt_record_declines_rather_than_guesses(self):
        self.write("src/a.py", "x = 1\n")
        AF.record_anchors(self.root, ["src/a.py"])
        with open(AF.record_path(self.root), "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertEqual({}, AF.read_record(self.root))
        v = AF.evaluate_anchor("src/a.py", root=self.root)
        self.assertEqual(AF.DECLINED, v.verdict)


# ================================================================ P2 — durable across sessions
class Durable(_Base):

    def _as_session(self, sid):
        from mokata import session
        os.environ["MOKATA_SESSION_ID"] = sid
        session.reset_for_test()
        self.addCleanup(session.reset_for_test)
        self.addCleanup(os.environ.pop, "MOKATA_SESSION_ID", None)
        return session.current_session_id()

    def test_record_survives_a_session_change(self):
        self.write("src/a.py", "x = 1\n")
        one = self._as_session("session-one")
        AF.record_anchors(self.root, ["src/a.py"])
        path_one = AF.record_path(self.root)

        two = self._as_session("session-two")
        self.assertNotEqual(one, two)                # a genuinely different session identity
        v = AF.evaluate_anchor("src/a.py", root=self.root)

        self.assertEqual(AF.UNCHANGED, v.verdict)    # the NEXT session still has the baseline
        self.assertEqual(path_one, AF.record_path(self.root))   # ...at the SAME path

    def test_the_freshness_index_it_replaces_is_session_keyed(self):
        # The control: GR.S4's baseline genuinely IS session-scoped, which is the gap S1 closes.
        # Without this, "durable" is asserted against nothing.
        from mokata.knowledge import freshness as F
        self._as_session("session-one")
        c1 = F.FreshnessController(self.root)
        self._as_session("session-two")
        c2 = F.FreshnessController(self.root)
        self.assertNotEqual(c1._index_key(), c2._index_key())

    def test_module_never_resolves_a_session_identity(self):
        # STRUCTURAL (AST), not a text grep — the module docstring EXPLAINS session-scoping at
        # length and must still pass.
        import ast
        import inspect
        tree = ast.parse(inspect.getsource(AF))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
        self.assertNotIn("session", {m.rsplit(".", 1)[-1] for m in imported})
        called = {n.func.id for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        self.assertNotIn("current_session_id", called)

    def test_record_lives_under_temp_local(self):
        from mokata import MOKATA_DIR, TEMP_LOCAL_DIRNAME
        p = AF.record_path(self.root)
        self.assertTrue(p.startswith(os.path.join(self.root, MOKATA_DIR, TEMP_LOCAL_DIRNAME)))

    def test_no_durable_fact_write_outside_the_leaf(self):
        # The whole-tree byte snapshot (H-1a S2/S3 precedent): a NAME-based sweep is green under a
        # delegated write, so the pin is bytes. The record leaf is carved out explicitly, the way
        # self-protect carved out the harness's per-project memory leaf.
        self.write("src/a.py", "x = 1\n")
        AF.record_anchors(self.root, ["src/a.py"])
        before = _snapshot(self.root, skip=AF.record_dir(self.root))
        AF.evaluate_anchor("src/a.py", root=self.root)
        AF.evaluate_anchors(["src/a.py", "Some.Symbol"], root=self.root)
        AF.read_record(self.root)
        self.assertEqual(before, _snapshot(self.root, skip=AF.record_dir(self.root)))

    def test_evaluate_does_not_touch_the_record_leaf_either(self):
        # The carve-out above is for the MINT. Evaluation is PURE, so the leaf is IN scope here —
        # without this, an `evaluate` that silently minted the baseline it found missing would sail
        # through the carve-out and quietly destroy P6 (it did, on the first pin pass).
        self.write("src/a.py", "x = 1\n")
        AF.record_anchors(self.root, ["src/a.py"])
        # `src/mintable.py` EXISTS but is NOT recorded — a silent mint would succeed on it. Without
        # such an anchor the pin is vacuous: every other candidate declines before any write.
        self.write("src/mintable.py", "m = 1\n")
        before = _snapshot(self.root)
        AF.evaluate_anchor("src/a.py", root=self.root)
        AF.evaluate_anchor("src/mintable.py", root=self.root)
        AF.evaluate_anchor("src/never-seen.py", root=self.root)
        AF.evaluate_anchors(["src/a.py", "Some.Symbol", "src/mintable.py"], root=self.root)
        AF.moved_paths(self.root)
        self.assertEqual(before, _snapshot(self.root))
        self.assertNotIn("src/mintable.py", AF.read_record(self.root))


def _snapshot(root, skip=""):
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        if skip and (dirpath == skip or dirpath.startswith(skip + os.sep)):
            dirnames[:] = []
            continue
        for fn in filenames:
            ab = os.path.join(dirpath, fn)
            try:
                with open(ab, "rb") as fh:
                    out[os.path.relpath(ab, root)] = fh.read()
            except OSError:
                out[os.path.relpath(ab, root)] = b"<unreadable>"
    return out


# ================================================================ P3 — the PATH arm, alone
class PathArm(_Base):
    """Decision #1: a changed file hash is a verifiable fact. NO graph is consulted — proven with
    no layer at all, with the grep/AST floor, and with a graph that would raise if touched."""

    def test_fires_with_no_layer_at_all(self):
        self.write("src/a.py", "x = 1\n")
        AF.record_anchors(self.root, ["src/a.py"])
        self.write("src/a.py", "x = 2\n")
        v = AF.evaluate_anchor("src/a.py", root=self.root, layer=None)
        self.assertEqual(AF.MOVED, v.verdict)
        self.assertEqual(AF.SHAPE_PATH, v.shape)

    def test_fires_on_the_floor(self):
        self.write("src/a.py", "x = 1\n")
        AF.record_anchors(self.root, ["src/a.py"])
        self.write("src/a.py", "x = 2\n")
        v = AF.evaluate_anchor("src/a.py", root=self.root, layer=_Layer(_Floor()))
        self.assertEqual(AF.MOVED, v.verdict)

    def test_never_touches_the_graph(self):
        self.write("src/a.py", "x = 1\n")
        AF.record_anchors(self.root, ["src/a.py"])
        self.write("src/a.py", "x = 2\n")
        exploding = _Graph(raises=True)
        v = AF.evaluate_anchor("src/a.py", root=self.root, layer=_Layer(exploding))
        self.assertEqual(AF.MOVED, v.verdict)        # a graph that raises on ANY query was not asked

    def test_unchanged_file_does_not_fire(self):
        self.write("src/a.py", "x = 1\n")
        AF.record_anchors(self.root, ["src/a.py"])
        v = AF.evaluate_anchor("src/a.py", root=self.root)
        self.assertEqual(AF.UNCHANGED, v.verdict)

    def test_line_suffix_is_stripped(self):
        self.write("src/a.py", "x = 1\n")
        AF.record_anchors(self.root, ["src/a.py:120"])
        self.write("src/a.py", "x = 2\n")
        v = AF.evaluate_anchor("src/a.py:120", root=self.root)
        self.assertEqual(AF.MOVED, v.verdict)
        self.assertEqual(AF.SHAPE_PATH, v.shape)


# ================================================================ P4 — the SYMBOL arm, alone
class SymbolArm(_Base):
    """Decision #1: a symbol needs an AUTHORITATIVE resolver even to learn which file to hash."""

    DEFS = {"MemoryStore.remember": ["src/store.py"]}
    SYM = "MemoryStore.remember"

    def _graph(self):
        return _Layer(_Graph(defs=dict(self.DEFS)))

    def _armed(self):
        """A symbol anchor that WOULD fire: baseline minted from an authoritative graph, then the
        defining file changed. Every decline test below runs from here, so a decline can only come
        from the capability gate under test — never from a missing baseline (which would be P6's
        decline wearing P4's name, and was exactly how the first pin pass passed for free)."""
        self.write("src/store.py", "a = 1\n")
        AF.record_anchors(self.root, [self.SYM], layer=self._graph())
        self.write("src/store.py", "a = 2\n")
        self.assertEqual(AF.MOVED,
                         AF.evaluate_anchor(self.SYM, root=self.root,
                                            layer=self._graph()).verdict)

    def test_declines_on_the_floor(self):
        self._armed()
        floor = _Layer(_Floor(defs=dict(self.DEFS)))     # answers `defs`, just not authoritatively
        v = AF.evaluate_anchor(self.SYM, root=self.root, layer=floor)
        self.assertEqual(AF.DECLINED, v.verdict)
        self.assertEqual(AF.SHAPE_SYMBOL, v.shape)
        self.assertEqual(AF.DECLINE_NO_GRAPH, v.reason)

    def test_declines_with_no_layer(self):
        self._armed()
        v = AF.evaluate_anchor(self.SYM, root=self.root, layer=None)
        self.assertEqual(AF.DECLINED, v.verdict)
        self.assertEqual(AF.DECLINE_NO_GRAPH, v.reason)

    def test_declines_when_the_graph_maps_no_defs(self):
        # The REAL code-review-graph: `defs` is an UNMAPPED kind (`crg_client.py:136-142`). It
        # cannot name a definition site, so H-6 declines rather than inventing one.
        self._armed()
        g = _Graph(defs=dict(self.DEFS), kinds=())
        v = AF.evaluate_anchor(self.SYM, root=self.root, layer=_Layer(g))
        self.assertEqual(AF.DECLINED, v.verdict)
        self.assertEqual(AF.DECLINE_NO_DEFS, v.reason)

    def test_declines_on_a_graph_hiccup(self):
        self._armed()
        v = AF.evaluate_anchor(self.SYM, root=self.root, layer=_Layer(_Graph(raises=True)))
        self.assertEqual(AF.DECLINED, v.verdict)
        self.assertEqual(AF.DECLINE_GRAPH_FAILED, v.reason)

    def test_declines_when_the_answer_came_from_the_floor(self):
        self._armed()
        g = _Graph(defs=dict(self.DEFS), degraded=True)
        v = AF.evaluate_anchor(self.SYM, root=self.root, layer=_Layer(g))
        self.assertEqual(AF.DECLINED, v.verdict)
        self.assertEqual(AF.DECLINE_NO_GRAPH, v.reason)

    def test_declines_when_the_graph_names_nothing(self):
        self._armed()
        v = AF.evaluate_anchor(self.SYM, root=self.root, layer=_Layer(_Graph(defs={})))
        self.assertEqual(AF.DECLINED, v.verdict)
        self.assertEqual(AF.DECLINE_UNRESOLVED, v.reason)

    def test_fires_when_the_defining_file_changes(self):
        self.write("src/store.py", "a = 1\n")
        layer = self._graph()
        AF.record_anchors(self.root, ["MemoryStore.remember"], layer=layer)
        self.write("src/store.py", "a = 2\n")
        v = AF.evaluate_anchor("MemoryStore.remember", root=self.root, layer=layer)
        self.assertEqual(AF.MOVED, v.verdict)
        self.assertEqual(AF.SHAPE_SYMBOL, v.shape)
        self.assertIn("src/store.py", v.path)

    def test_fires_when_the_symbol_moves_file(self):
        self.write("src/store.py", "a = 1\n")
        self.write("src/other.py", "a = 1\n")          # byte-identical: only the PATH differs
        g = _Graph(defs={"S": ["src/store.py"]})
        AF.record_anchors(self.root, ["S"], layer=_Layer(g))
        g._defs["S"] = ["src/other.py"]
        v = AF.evaluate_anchor("S", root=self.root, layer=_Layer(g))
        self.assertEqual(AF.MOVED, v.verdict)

    def test_unchanged_symbol_does_not_fire(self):
        self.write("src/store.py", "a = 1\n")
        layer = self._graph()
        AF.record_anchors(self.root, ["MemoryStore.remember"], layer=layer)
        v = AF.evaluate_anchor("MemoryStore.remember", root=self.root, layer=layer)
        self.assertEqual(AF.UNCHANGED, v.verdict)


# ================================================================ shape classification
class Shapes(_Base):

    def test_existing_file_is_a_path(self):
        self.write("src/a.py", "x = 1\n")
        self.assertEqual(AF.SHAPE_PATH, AF.classify_anchor("src/a.py", self.root))

    def test_slashed_or_suffixed_string_is_a_path_even_when_absent(self):
        self.assertEqual(AF.SHAPE_PATH, AF.classify_anchor("src/gone.py", self.root))
        self.assertEqual(AF.SHAPE_PATH, AF.classify_anchor("gone.py", self.root))

    def test_dotted_name_is_a_symbol(self):
        self.assertEqual(AF.SHAPE_SYMBOL, AF.classify_anchor("MemoryStore.remember", self.root))
        self.assertEqual(AF.SHAPE_SYMBOL, AF.classify_anchor("check_about_code_anchors", self.root))

    def test_the_recorded_shape_wins(self):
        # A path anchor whose file was DELETED must not silently re-classify as a symbol and start
        # asking the graph about it — the record remembers what it was. `Makefile` is chosen
        # deliberately: it has no slash and no known suffix, so disk-inference is the ONLY thing
        # that made it a path, and once it is gone the record is the only witness left. (An anchor
        # like `src/a.py` proves nothing here — it stays a path on its slash alone.)
        self.write("Makefile", "all:\n")
        AF.record_anchors(self.root, ["Makefile"])
        os.remove(os.path.join(self.root, "Makefile"))
        rec = AF.read_record(self.root)
        self.assertEqual(AF.SHAPE_SYMBOL, AF.classify_anchor("Makefile", self.root))
        self.assertEqual(AF.SHAPE_PATH, AF.classify_anchor("Makefile", self.root, record=rec))
        self.assertEqual(AF.SHAPE_PATH,
                         AF.evaluate_anchor("Makefile", root=self.root).shape)


# ================================================================ P1 — one tripwire
class OneTripwire(unittest.TestCase):

    def test_the_comparison_is_the_pre_named_hook(self):
        import inspect
        src = inspect.getsource(AF)
        self.assertIn("fingerprint_forces_refresh", src)

    def test_no_second_comparison_operator(self):
        # A hand-rolled `recorded != current` anywhere in this module would be the second tripwire
        # the pre-named hook exists to prevent. Structural (AST), not a text grep — prose that
        # EXPLAINS the comparison still passes.
        import ast
        import inspect
        tree = ast.parse(inspect.getsource(AF))
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        self.assertIn("fingerprint_forces_refresh", names)
        watched = {"recorded", "current", "fingerprint", "cur", "fp"}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            if not any(isinstance(op, (ast.Eq, ast.NotEq)) for op in node.ops):
                continue
            sides = [node.left] + list(node.comparators)
            hit = {s.id for s in sides if isinstance(s, ast.Name)} & watched
            self.assertFalse(hit, f"second fingerprint comparison on {sorted(hit)}")


# ================================================================ the record itself
class Record(_Base):

    def _raw(self):
        with open(AF.record_path(self.root), encoding="utf-8") as fh:
            return json.load(fh)

    def test_mint_is_idempotent_and_does_not_overwrite_silently(self):
        self.write("src/a.py", "x = 1\n")
        AF.record_anchors(self.root, ["src/a.py"])
        first = self._raw()
        self.write("src/a.py", "x = 2\n")
        AF.record_anchors(self.root, ["src/a.py"])            # no refresh= ⇒ existing entry stands
        second = self._raw()
        self.assertEqual(first["src/a.py"]["fingerprint"], second["src/a.py"]["fingerprint"])

    def test_refresh_restamps_only_when_asked(self):
        self.write("src/a.py", "x = 1\n")
        AF.record_anchors(self.root, ["src/a.py"])
        self.write("src/a.py", "x = 2\n")
        AF.record_anchors(self.root, ["src/a.py"], refresh=True)
        self.assertEqual(AF.UNCHANGED,
                         AF.evaluate_anchor("src/a.py", root=self.root).verdict)

    def test_declined_anchors_are_not_recorded(self):
        # A symbol with no graph yields no fingerprint, so there is nothing honest to record.
        AF.record_anchors(self.root, ["Some.Symbol"], layer=None)
        self.assertEqual({}, AF.read_record(self.root))

    def test_recording_never_raises_when_the_record_cannot_be_written(self):
        # The failure must reach the WRITE, not be absorbed by an earlier guard. So: a real root
        # holding a real anchored file (the mint genuinely produces a fingerprint), with `.mokata`
        # occupied by a FILE so the record directory cannot be created and `atomic_write_text` is
        # the thing that raises. A NUL-byte root proved nothing — every anchor declined long before
        # the write was attempted and the handler was never entered.
        self.write("src/a.py", "x = 1\n")
        with open(os.path.join(self.root, ".mokata"), "w", encoding="utf-8") as fh:
            fh.write("not a directory\n")
        self.assertFalse(os.path.isdir(AF.record_dir(self.root)))
        self.assertEqual([], AF.record_anchors(self.root, ["src/a.py"]))
        self.assertEqual({}, AF.read_record(self.root))

    def test_moved_paths_helper(self):
        self.write("src/a.py", "x = 1\n")
        self.write("src/b.py", "y = 1\n")
        self.write("src/store.py", "s = 1\n")
        layer = _Layer(_Graph(defs={"Sym.moved": ["src/store.py"]}))
        AF.record_anchors(self.root, ["src/a.py", "src/b.py"])
        AF.record_anchors(self.root, ["Sym.moved"], layer=layer)
        self.write("src/b.py", "y = 2\n")
        self.write("src/store.py", "s = 2\n")            # the SYMBOL anchor moved too
        # ...and it must NOT appear: `_reconcile` gets path-shaped evidence only, or the freshness
        # reconcile acquires a code-graph read to serve a memory item it knows nothing about.
        self.assertEqual(["src/b.py"], AF.moved_paths(self.root))
        # ...and the graph WOULD have said it moved, so the exclusion is a real one:
        self.assertEqual(AF.MOVED,
                         AF.evaluate_anchor("Sym.moved", root=self.root, layer=layer).verdict)

    def test_moved_paths_hands_no_layer_to_anything(self):
        # STRUCTURAL: the path-only guarantee comes from the ABSENT layer, not from a filter (a
        # `shape != SHAPE_PATH` guard was written first and the mutation pass proved it changed
        # nothing). If this ever passes a real layer, the freshness reconcile has acquired a
        # code-graph read to decide whether to rebuild the code graph.
        import ast
        import inspect
        fn = ast.parse(inspect.getsource(AF.moved_paths).lstrip())
        for call in [n for n in ast.walk(fn) if isinstance(n, ast.Call)]:
            for kw in call.keywords:
                if kw.arg == "layer":
                    self.assertIsInstance(kw.value, ast.Constant)
                    self.assertIsNone(kw.value.value)


if __name__ == "__main__":
    unittest.main()
