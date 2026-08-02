"""H-6 S3 — the PROPOSAL arm: an `about_code` item whose anchor moved becomes a healing proposal.

This is the "bridge" in "code-staleness → healing bridge". A decision that names the code it
concerns (`about_code`, TM.S11a) is the one kind of memory item that CAN go stale for a reason
mokata is able to observe — the code moved. So it joins contradiction / near-dup / cross-writer on
the surface a human already reads, through the ONE gate, as a PROPOSAL that changes nothing.

What is pinned here:

  P3  PATH-SHAPED ANCHORS PROPOSE WITH NO GRAPH AT ALL.
  P4  SYMBOL-SHAPED ANCHORS DECLINE WITHOUT AN AUTHORITATIVE GRAPH — and the arm consumes the SAME
      `AnchorVerdict` S4's refusal will, rather than deriving a second opinion.
  P8  THE WORDING PIN. The two shapes make DIFFERENT CLAIMS and must say so in different words. A
      path proposal says the FILE changed; it must never imply the anchored SYMBOL moved, because a
      whitespace edit three hundred lines away moves the file hash and says nothing about the
      symbol. Shared wording, or symbol-claiming wording on a path anchor ⇒ RED.
  P7  LOUD, NEVER SILENT-CORRECT. Raising the proposal does not re-stamp the record; the anchor
      keeps proposing until a HUMAN decides.
  P5  THE H-1a INJECTION PATH STAYS ANCHOR-FREE. The coupling is one-directional: H-6 may read what
      H-1a persists, never the reverse. H-1a's per-turn lane runs under a HARD 300-token budget on
      `UserPromptSubmit`, and file hashing on it would spend that budget on I/O.
  ADD `detect_issues` stays ADDITIVE — no anchor evidence ⇒ the pre-H-6 proposal set, item for item
      (the `detect_cross_writer` precedent this arm is built on).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import shutil
import tempfile
import unittest

import _support  # noqa: F401

from mokata.knowledge import anchor_fingerprints as AF
from mokata.memory.healing import (CODE_ANCHOR_STALE, CONTRADICTION, STALE, AnchorStaleness,
                                   detect_code_anchor_staleness, detect_issues, render_proposal)
from mokata.memory.item import MemoryItem


def _item(subject, value="v", about_code=(), mtype="persistent"):
    it = MemoryItem.create(subject, value, mtype=mtype)
    it.about_code = list(about_code)
    return it


class _Ref:
    def __init__(self, path):
        self.path = path


class _Result:
    def __init__(self, refs):
        self.references = refs
        self.degraded = False


class _Graph:
    is_graph = True

    def __init__(self, defs):
        self._defs = defs

    def supports_kind(self, kind):
        return True

    def query(self, kind, target, depth=1):
        return _Result([_Ref(p) for p in self._defs.get(target, [])])


class _Floor:
    is_graph = False

    def supports_kind(self, kind):
        return True

    def query(self, kind, target, depth=1):
        return _Result([_Ref("src/store.py")])


class _Layer:
    def __init__(self, primary=None):
        self.primary = primary


class _Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def write(self, rel, text):
        ab = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(ab), exist_ok=True)
        with open(ab, "w", encoding="utf-8") as fh:
            fh.write(text)


# ================================================================ ADD — additive
class Additive(unittest.TestCase):

    def test_no_anchor_evidence_is_the_pre_h6_proposal_set(self):
        old = _item("db host", "one")
        new = _item("db host", "two")
        base = detect_issues([old, new], now="2026-07-01T00:00:00+00:00")
        with_arg = detect_issues([old, new], now="2026-07-01T00:00:00+00:00",
                                 anchor_staleness=[])
        self.assertEqual([p.kind for p in base], [p.kind for p in with_arg])
        self.assertEqual([CONTRADICTION], [p.kind for p in base])

    def test_the_arm_is_its_own_function(self):
        # The `detect_cross_writer` precedent: a new arm is a new function plus one call, never a
        # rewrite of the detector every other arm lives in.
        import inspect
        src = inspect.getsource(detect_issues)
        for token in ("about_code", "fingerprint", "AnchorVerdict", "anchor_fingerprints"):
            self.assertNotIn(token, src)


# ================================================================ P3/P4 — the two shapes
class Shapes(_Base):

    def _evidence(self, item, layer=None):
        """What the STORE projects: plain fields, computed at its own boundary (the R3 shape the
        cross-writer arm established). The detector opens no file and knows no graph."""
        out = []
        for v in AF.evaluate_anchors(item.about_code, root=self.root, layer=layer):
            if v.moved:
                out.append(AnchorStaleness(item=item, anchor=v.anchor, shape=v.shape,
                                           path=v.path))
        return out

    def test_a_path_anchor_proposes_with_no_graph(self):
        self.write("src/store.py", "a = 1\n")
        it = _item("write path", about_code=["src/store.py"])
        AF.record_anchors(self.root, it.about_code)
        self.write("src/store.py", "a = 2\n")

        props = detect_code_anchor_staleness(self._evidence(it, layer=None))
        self.assertEqual([CODE_ANCHOR_STALE], [p.kind for p in props])
        self.assertEqual("write path", props[0].subject)
        self.assertIs(it, props[0].old)
        self.assertIsNone(props[0].new)          # nothing supersedes it — the CODE moved

    def test_a_symbol_anchor_declines_without_an_authoritative_graph(self):
        self.write("src/store.py", "a = 1\n")
        it = _item("write path", about_code=["MemoryStore.remember"])
        graph = _Layer(_Graph({"MemoryStore.remember": ["src/store.py"]}))
        AF.record_anchors(self.root, it.about_code, layer=graph)
        self.write("src/store.py", "a = 2\n")

        self.assertEqual([], detect_code_anchor_staleness(self._evidence(it, layer=None)))
        self.assertEqual([], detect_code_anchor_staleness(
            self._evidence(it, layer=_Layer(_Floor()))))
        # ...and WITH the graph it fires, so the decline is a real one:
        self.assertEqual([CODE_ANCHOR_STALE],
                         [p.kind for p in detect_code_anchor_staleness(
                             self._evidence(it, layer=graph))])

    def test_an_unmoved_anchor_proposes_nothing(self):
        self.write("src/store.py", "a = 1\n")
        it = _item("write path", about_code=["src/store.py"])
        AF.record_anchors(self.root, it.about_code)
        self.assertEqual([], detect_code_anchor_staleness(self._evidence(it)))

    def test_an_unrecorded_anchor_proposes_nothing(self):
        self.write("src/store.py", "a = 1\n")
        it = _item("write path", about_code=["src/store.py"])
        self.write("src/store.py", "a = 2\n")
        self.assertEqual([], detect_code_anchor_staleness(self._evidence(it)))

    def test_one_proposal_per_moved_anchor(self):
        self.write("src/a.py", "a = 1\n")
        self.write("src/b.py", "b = 1\n")
        it = _item("two anchors", about_code=["src/a.py", "src/b.py"])
        AF.record_anchors(self.root, it.about_code)
        self.write("src/a.py", "a = 2\n")
        self.write("src/b.py", "b = 2\n")
        props = detect_code_anchor_staleness(self._evidence(it))
        self.assertEqual(2, len(props))
        self.assertEqual(["src/a.py", "src/b.py"], sorted(p.anchor for p in props))


# ================================================================ P8 — the WORDING pin
class Wording(_Base):
    """The two shapes make DIFFERENT CLAIMS and must say so in different words."""

    # A path anchor's proposal may NOT reach for any of these: each one asserts something about
    # the SYMBOL, which a file hash cannot establish.
    SYMBOL_CLAIMS = ("symbol", "defines", "definition", "declares", "declaration", "signature")

    def _rationales(self):
        self.write("src/store.py", "a = 1\n")
        it = _item("s", about_code=["src/store.py", "MemoryStore.remember"])
        graph = _Layer(_Graph({"MemoryStore.remember": ["src/store.py"]}))
        AF.record_anchors(self.root, it.about_code, layer=graph)
        self.write("src/store.py", "a = 2\n")
        props = detect_code_anchor_staleness(
            [AnchorStaleness(item=it, anchor=v.anchor, shape=v.shape, path=v.path)
             for v in AF.evaluate_anchors(it.about_code, root=self.root, layer=graph) if v.moved])
        by_shape = {p.shape: p for p in props}
        self.assertEqual({AF.SHAPE_PATH, AF.SHAPE_SYMBOL}, set(by_shape))
        return by_shape[AF.SHAPE_PATH], by_shape[AF.SHAPE_SYMBOL]

    def test_the_two_shapes_do_not_share_wording(self):
        path_p, symbol_p = self._rationales()
        self.assertNotEqual(path_p.rationale, symbol_p.rationale)

    def test_a_path_proposal_never_claims_the_symbol_moved(self):
        path_p, _ = self._rationales()
        low = path_p.rationale.lower()
        for word in self.SYMBOL_CLAIMS:
            self.assertNotIn(word, low,
                             f"the PATH proposal claims '{word}' — a file hash cannot show that")

    def test_a_path_proposal_names_the_file_and_says_what_it_does_not_know(self):
        path_p, _ = self._rationales()
        self.assertIn("src/store.py", path_p.rationale)
        self.assertIn("file", path_p.rationale.lower())

    def test_a_symbol_proposal_makes_the_stronger_claim_explicitly(self):
        _, symbol_p = self._rationales()
        low = symbol_p.rationale.lower()
        self.assertTrue(any(w in low for w in ("defines", "definition")),
                        "the SYMBOL proposal must name the definition site it was given")
        self.assertIn("MemoryStore.remember", symbol_p.rationale)

    def test_the_rendered_prompt_carries_the_shape_specific_words(self):
        path_p, symbol_p = self._rationales()
        self.assertIn(path_p.rationale, render_proposal(path_p))
        self.assertIn(symbol_p.rationale, render_proposal(symbol_p))
        # Default is REJECT — memory is never rewritten without a say-so (the C5 contract).
        self.assertIn("Default is REJECT", render_proposal(path_p))

    def test_it_is_not_the_ttl_stale_wording(self):
        # CODE_ANCHOR_STALE and STALE are different facts: one says a TTL elapsed, the other that
        # the code underneath a still-valid decision moved. Sharing a kind (or a diff line) would
        # tell a human their decision expired when nothing of the sort happened.
        path_p, _ = self._rationales()
        self.assertNotEqual(STALE, path_p.kind)
        self.assertNotIn("valid_for elapsed", path_p.rationale)
        self.assertNotIn("-> stale", path_p.diff())


# ================================================================ P7 — never silent-correct
class NeverRestamps(_Base):

    def test_proposing_does_not_restamp_the_record(self):
        self.write("src/store.py", "a = 1\n")
        it = _item("s", about_code=["src/store.py"])
        AF.record_anchors(self.root, it.about_code)
        before = AF.read_record(self.root)["src/store.py"]["fingerprint"]
        self.write("src/store.py", "a = 2\n")

        for _ in range(3):
            evidence = [AnchorStaleness(item=it, anchor=v.anchor, shape=v.shape, path=v.path)
                        for v in AF.evaluate_anchors(it.about_code, root=self.root) if v.moved]
            self.assertEqual(1, len(detect_code_anchor_staleness(evidence)))
        self.assertEqual(before, AF.read_record(self.root)["src/store.py"]["fingerprint"])

    def test_the_detector_writes_nothing_at_all(self):
        import ast
        import inspect
        tree = ast.parse(inspect.getsource(detect_code_anchor_staleness).lstrip())
        called = {n.func.id for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        called |= {n.func.attr for n in ast.walk(tree)
                   if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        for banned in ("record_anchors", "open", "write", "remember", "put", "_write_record"):
            self.assertNotIn(banned, called)


# ================================================================ the WIRING, not just the arm
class Wiring(_Base):
    """DB.S7c1's lesson, taken rather than re-learned: an arm proven only at the detector is an arm
    nobody has proven reaches a surface a human reads. `store.detect_issues` IS that surface (the
    governance view, `mokata memory`, the MCP proposal tool all render it)."""

    def _store(self, surface=None):
        from mokata.memory.backends import SQLiteBackend
        from mokata.memory.store import MemoryStore
        db = os.path.join(self.root, ".mokata", "m.db")
        os.makedirs(os.path.dirname(db), exist_ok=True)
        return MemoryStore(SQLiteBackend(db), surface=surface)

    class _Surface:
        def __init__(self, root):
            self.root = root
            self.mokata_dir = os.path.join(root, ".mokata")

    def test_a_moved_anchor_reaches_store_detect_issues(self):
        self.write("src/store.py", "a = 1\n")
        store = self._store(surface=self._Surface(self.root))
        it = _item("write path", about_code=["src/store.py"])
        store.remember(it, assume_yes=True)
        AF.record_anchors(self.root, ["src/store.py"])
        self.assertEqual([], [p for p in store.detect_issues()
                              if p.kind == CODE_ANCHOR_STALE])

        self.write("src/store.py", "a = 2\n")
        props = [p for p in store.detect_issues() if p.kind == CODE_ANCHOR_STALE]
        self.assertEqual(1, len(props))
        self.assertEqual("write path", props[0].subject)
        self.assertEqual("src/store.py", props[0].anchor)

    def _count(self, name):
        """Count calls into `anchor_fingerprints.<name>`. An emptiness assertion cannot tell "we
        looked and found nothing" from "we never looked", and both survivors of the first mutation
        pass here were exactly that confusion."""
        original = getattr(AF, name)
        calls = []

        def counted(*a, **k):
            calls.append(1)
            return original(*a, **k)

        setattr(AF, name, counted)
        self.addCleanup(setattr, AF, name, original)
        return calls

    def test_no_surface_never_even_reads_the_record(self):
        # A directly-constructed store has no root to resolve anchors against — every pre-H-6
        # caller lands here, and must pay NOTHING, not merely produce nothing.
        self.write("src/store.py", "a = 1\n")
        store = self._store(surface=None)
        store.remember(_item("write path", about_code=["src/store.py"]), assume_yes=True)
        AF.record_anchors(self.root, ["src/store.py"])
        self.write("src/store.py", "a = 2\n")
        reads = self._count("read_record")
        self.assertEqual([], store.detect_issues())
        self.assertEqual([], reads)

    def test_no_record_means_no_anchor_evaluation_at_all(self):
        # A repo whose anchored items PREDATE H-6: they are on disk, but no gated write ever minted
        # a baseline for them. That is now the honest way to reach this early-out — a gated
        # `remember` mints one (H-6 MINT), so the item is put straight on the backend here, which
        # is exactly the shape a legacy item has. One cheap record read decides it; no anchor is
        # hashed, and `detect_issues` is on every governance surface.
        self.write("src/store.py", "a = 1\n")
        store = self._store(surface=self._Surface(self.root))
        store.backend.put(_item("write path", about_code=["src/store.py"]))
        self.assertFalse(os.path.exists(AF.record_path(self.root)))
        evaluations = self._count("evaluate_anchors")
        self.assertEqual([], store.detect_issues())
        self.assertEqual([], evaluations)

        # ...and the control: WITH a record it does evaluate, so the early-out is a real one.
        AF.record_anchors(self.root, ["src/store.py"])
        store.detect_issues()
        self.assertEqual(1, len(evaluations))

    def test_a_symbol_anchor_reaches_the_surface_only_with_the_graph(self):
        # P4 through the WIRING: the store must pass its knowledge layer through, or the symbol arm
        # is dead on every surface a human reads while its unit tests stay green.
        self.write("src/store.py", "a = 1\n")
        graph = _Layer(_Graph({"MemoryStore.remember": ["src/store.py"]}))
        AF.record_anchors(self.root, ["MemoryStore.remember"], layer=graph)
        self.write("src/store.py", "a = 2\n")

        blind = self._store(surface=self._Surface(self.root))
        blind.remember(_item("write path", about_code=["MemoryStore.remember"]), assume_yes=True)
        self.assertEqual([], [p for p in blind.detect_issues() if p.kind == CODE_ANCHOR_STALE])

        seeing = self._store(surface=self._Surface(self.root))
        seeing.knowledge_layer = graph
        props = [p for p in seeing.detect_issues() if p.kind == CODE_ANCHOR_STALE]
        self.assertEqual(1, len(props))
        self.assertEqual(AF.SHAPE_SYMBOL, props[0].shape)

    def test_detecting_writes_nothing_durable(self):
        self.write("src/store.py", "a = 1\n")
        store = self._store(surface=self._Surface(self.root))
        store.remember(_item("write path", about_code=["src/store.py"]), assume_yes=True)
        AF.record_anchors(self.root, ["src/store.py"])
        self.write("src/store.py", "a = 2\n")
        store.detect_issues()
        with open(AF.record_path(self.root), "rb") as fh:
            before = fh.read()
        store.detect_issues()
        store.detect_issues()
        with open(AF.record_path(self.root), "rb") as fh:
            self.assertEqual(before, fh.read())
        self.assertEqual(AF.MOVED, AF.evaluate_anchor("src/store.py", root=self.root).verdict)


# ================================================================ P5 — H-1a stays anchor-free
class InjectionStaysAnchorFree(unittest.TestCase):
    """One-directional coupling: H-6 may read what H-1a persists, H-1a never reads H-6.

    STRUCTURAL (an AST import/identifier scan over the modules on the per-turn lane), not a text
    grep — a module that merely EXPLAINS the boundary in prose still passes, which is the standard
    DB.S7c2 set for the same class of claim."""

    LANE = ("bootstrap", "hook_cli", "injection_ledger", "hooks.user_prompt_submit")

    def test_no_module_on_the_per_turn_lane_reaches_for_anchors(self):
        import ast
        import importlib
        import inspect
        for name in self.LANE:
            mod = importlib.import_module(f"mokata.{name}")
            tree = ast.parse(inspect.getsource(mod))
            names = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    names.add((node.module or "").rsplit(".", 1)[-1])
                    names.update(a.name for a in node.names)
                elif isinstance(node, ast.Import):
                    names.update(a.name.rsplit(".", 1)[-1] for a in node.names)
                elif isinstance(node, ast.Name):
                    names.add(node.id)
                elif isinstance(node, ast.Attribute):
                    names.add(node.attr)
            for banned in ("anchor_fingerprints", "evaluate_anchor", "evaluate_anchors",
                           "anchor_signal", "record_anchors", "moved_paths",
                           "detect_code_anchor_staleness"):
                self.assertNotIn(banned, names,
                                 f"mokata.{name} (H-1a per-turn lane) reached for {banned}")

    def test_the_direction_h6_may_take_is_open(self):
        # The control: the coupling is one-DIRECTIONAL, not absent. H-6's own module is free to
        # read what H-1a persists, and this test exists so "anchor-free" is never read as
        # "the two must not know about each other".
        from mokata import injection_ledger
        self.assertTrue(callable(injection_ledger.already_injected))


if __name__ == "__main__":
    unittest.main()
