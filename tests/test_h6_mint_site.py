"""H-6 MINT — the anchor baseline is minted inside the gated write's commit closure.

WHY THIS EXISTS AT ALL, and it is worth stating plainly because the gap it closes was invisible to
every test H-6 already had: S1–S4 were fully built, fully pinned, and **completely inert in
production**. Nothing minted a baseline, so `read_record` always answered `{}`, so the tripwire saw
nothing, the proposal arm early-returned and the refusal early-returned. Every unit test passed
because every unit test minted the baseline itself. That is the systemic finding filed as
REACHABILITY-PINS-MISSING (doc 84): a pin that proves BEHAVIOUR proves nothing about whether a
production path ever invokes the behaviour.

So the first test in this file is a REACHABILITY pin, and it is deliberately written as the whole
product path end to end — init a repo, remember an `about_code` item through the real gated write,
edit the anchored file, ask the store what it thinks. No `record_anchors` call anywhere in it. If
someone deletes the mint, THAT test goes red, and nothing else in the H-6 suite would.

WHERE THE MINT LIVES (the M-1/R9 precedent, followed exactly). `_durable_write` is the single fork
every gated write path takes, and M-1/R9 put `_stamp_approval` there for a stated reason: "a new
gated write path inherits it by construction instead of by remembering to." The anchor baseline is
the same kind of act — something that must be true of every gated write and of no ungated one — so
it sits beside it. One gated write, no second path.

WHAT THE RECORD MEANS: the code as it stood when the human approved the decision. That is why the
mint is inside the commit closure rather than at the propose step or on a read path — the closure
runs if and only if a human's approval licensed this content.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import shutil
import tempfile
import unittest

import _support  # noqa: F401

from mokata.knowledge import anchor_fingerprints as AF
from mokata.memory.healing import CODE_ANCHOR_STALE
from mokata.memory.item import MemoryItem


def _item(subject, value="v", about_code=()):
    it = MemoryItem.create(subject, value, mtype="persistent")
    it.about_code = list(about_code)
    return it


class _Base(unittest.TestCase):
    """A REAL repo with a REAL surface — `init_repo`, not a hand-built store. The mint reads its
    root off the surface, so a directly-constructed store would prove nothing about production."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        from mokata.init import init_repo
        self.write("src/pay.py", "RATE = 1\n")
        init_repo(root=self.root, profile="standard", assume_yes=True, out=lambda _: None)

    def write(self, rel, text):
        ab = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(ab), exist_ok=True)
        with open(ab, "w", encoding="utf-8") as fh:
            fh.write(text)

    def store(self):
        from mokata.config import Surface
        from mokata.memory import MemoryStore
        return MemoryStore.from_surface(Surface.load(self.root))

    def anchor_props(self, store=None):
        return [p for p in (store or self.store()).detect_issues()
                if p.kind == CODE_ANCHOR_STALE]


# ================================================================ (a) REACHABILITY
class Reachability(_Base):
    """THE pin the whole H-6 cluster was missing. Every step here is the product's own path."""

    def test_the_product_path_yields_a_proposal_end_to_end(self):
        store = self.store()
        store.remember(_item("payment rule", "charge at RATE",
                             about_code=["src/pay.py"]), assume_yes=True)

        # The baseline exists because the GATED WRITE minted it — nothing in this test recorded it.
        self.assertTrue(os.path.exists(AF.record_path(self.root)))
        self.assertIn("src/pay.py", AF.read_record(self.root))
        self.assertEqual([], self.anchor_props())          # control: nothing has moved yet

        self.write("src/pay.py", "RATE = 2\n")             # the anchored code MOVES

        props = self.anchor_props()
        self.assertEqual(1, len(props))
        self.assertEqual("payment rule", props[0].subject)
        self.assertEqual("src/pay.py", props[0].anchor)
        self.assertEqual(AF.SHAPE_PATH, props[0].shape)

    def test_no_record_anchors_call_appears_in_this_reachability_path(self):
        # The pin is only worth anything if it CANNOT be satisfied by the test minting for itself.
        # A future edit that "fixes" a red reachability test by seeding the record has defeated it,
        # so the source of the test above is asserted to contain no mint call.
        import inspect
        src = inspect.getsource(Reachability.test_the_product_path_yields_a_proposal_end_to_end)
        self.assertNotIn("record_anchors", src)

    def test_the_freshness_tripwire_sees_it_too(self):
        # S2's wake reads the same durable record, so the mint makes signal 4 live as well.
        store = self.store()
        store.remember(_item("payment rule", about_code=["src/pay.py"]), assume_yes=True)
        self.assertEqual([], AF.anchor_signal(self.root).paths)      # control
        self.write("src/pay.py", "RATE = 2\n")
        self.assertEqual(["src/pay.py"], AF.anchor_signal(self.root).paths)


# ================================================================ (b) it rides the GATED write
class RidesTheGate(_Base):
    """The mint is inside the commit closure — no closure, no baseline. Behavioural, not a name
    scan: H-1a S2/S3 established that a name sweep is green under a *delegated* write."""

    # The ONE carve-out, and it is the gate doing its job rather than an exception to the pin: a
    # DECLINED write still appends its `write_gate` decision to the audit ledger. That record is
    # the point of the gate — a refusal nobody can see is not a governed refusal — so the ledger
    # is expected to move here and everything else is not. Named as a path prefix, not a guess.
    _LEDGER = os.path.join(".mokata", "temp_local", "audit")

    def _snapshot(self):
        out = {}
        for dirpath, dirnames, filenames in os.walk(self.root):
            for fn in filenames:
                ab = os.path.join(dirpath, fn)
                rel = os.path.relpath(ab, self.root)
                if rel.startswith(self._LEDGER):
                    continue
                try:
                    with open(ab, "rb") as fh:
                        out[rel] = fh.read()
                except OSError:
                    out[rel] = b"<unreadable>"
        return out

    def test_a_declined_write_mints_nothing(self):
        store = self.store()
        before = self._snapshot()
        res = store.remember(_item("payment rule", about_code=["src/pay.py"]),
                             confirm=lambda _: False)          # the human says NO
        self.assertFalse(res.committed)
        self.assertEqual(before, self._snapshot())              # WHOLE TREE, byte for byte
        self.assertFalse(os.path.exists(AF.record_path(self.root)))

    def test_a_refused_write_mints_nothing(self):
        # A write refused BEFORE the gate (a disabled type) never reaches the closure either.
        from mokata.config import Surface
        from mokata.memory import MemoryStore
        store = MemoryStore.from_surface(Surface.load(self.root))
        store.enabled_types = ("session",)                      # 'persistent' now disabled
        before = self._snapshot()
        with self.assertRaises(Exception):
            store.remember(_item("payment rule", about_code=["src/pay.py"]), assume_yes=True)
        self.assertEqual(before, self._snapshot())

    def test_an_approved_write_mints(self):
        store = self.store()
        res = store.remember(_item("payment rule", about_code=["src/pay.py"]), assume_yes=True)
        self.assertTrue(res.committed)
        self.assertEqual(["src/pay.py"], sorted(AF.read_record(self.root)))

    def test_the_mint_sits_on_the_single_fork_beside_the_approval_stamp(self):
        # M-1/R9's own argument: on the ONE fork, so a new gated write path inherits it by
        # construction. A mint bolted onto `remember` only would leave promote/propose/heal/
        # consolidate silently baseline-less.
        import inspect
        from mokata.memory.store import MemoryStore
        src = inspect.getsource(MemoryStore._durable_write)
        self.assertIn("_stamp_approval", src)
        self.assertIn("_record_code_anchors", src)

    def test_a_gated_path_other_than_remember_also_mints(self):
        # `propose` is a different gated writer entirely; it must inherit the mint.
        store = self.store()
        it = _item("payment rule", about_code=["src/pay.py"])
        store.propose(it, assume_yes=True)
        self.assertIn("src/pay.py", AF.read_record(self.root))

    def test_a_gated_DELETE_mints_nothing(self):
        # Retiring a fact is not a fresh observation of the code it named. A PRUNE consolidation is
        # the gated path that issues `_OP_DELETE`, and it must leave the record alone — otherwise
        # deleting an item would plant a baseline for code nobody looked at, and that baseline
        # would then be judged against by S4's refusal on a citation of some OTHER decision.
        from mokata.memory.consolidation import PRUNE, ConsolidationProposal
        store = self.store()
        it = _item("payment rule", about_code=["src/pay.py"])
        store.backend.put(it)                       # already on disk, un-anchored in the record
        self.assertFalse(os.path.exists(AF.record_path(self.root)))
        res = store.apply_consolidation(
            ConsolidationProposal(kind=PRUNE, mtype=it.mtype, subject=it.subject, olds=[it]),
            "approve", assume_yes=True)
        self.assertTrue(res.changed)
        self.assertEqual({}, AF.read_record(self.root))

    def test_a_store_with_no_surface_mints_nowhere(self):
        # A directly-constructed store has no repo to fingerprint against. It must mint NOTHING —
        # not "nothing here", but nothing anywhere: a root defaulted to "." would write the record
        # into whatever directory the process happens to be in, which is somebody else's repo.
        from mokata.memory.backends import SQLiteBackend
        from mokata.memory.store import MemoryStore
        cwd = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, cwd, ignore_errors=True)
        # The cwd must genuinely CONTAIN the anchored file, or a root defaulted to "." would fail
        # to fingerprint it and the test would pass for the wrong reason — which is exactly how the
        # first version of this pin survived the mutation.
        os.makedirs(os.path.join(cwd, "src"))
        with open(os.path.join(cwd, "src", "pay.py"), "w", encoding="utf-8") as fh:
            fh.write("RATE = 1\n")
        here = os.getcwd()
        os.chdir(cwd)
        self.addCleanup(os.chdir, here)

        store = MemoryStore(SQLiteBackend(os.path.join(cwd, "m.db")))
        store.remember(_item("payment rule", about_code=["src/pay.py"]), assume_yes=True)
        self.assertEqual(["m.db", "src"], sorted(os.listdir(cwd)))
        self.assertFalse(os.path.exists(AF.record_path(cwd)))


# ================================================================ (c) no anchors, no baseline
class OnlyAnchoredItems(_Base):

    def _count(self, name):
        """Count calls into `anchor_fingerprints.<name>`. `_durable_write` runs on EVERY gated
        write in the store, so "an unanchored item produces no record" is not the contract worth
        pinning — every write producing no record still passes that. The contract is that an
        unanchored write does no anchor WORK at all, and only a counter can tell those apart."""
        original = getattr(AF, name)
        calls = []

        def counted(*a, **k):
            calls.append(1)
            return original(*a, **k)

        setattr(AF, name, counted)
        self.addCleanup(setattr, AF, name, original)
        return calls

    def test_an_item_with_no_anchors_does_no_anchor_work(self):
        store = self.store()
        mints = self._count("record_anchors")
        store.remember(_item("a plain fact", "no code named"), assume_yes=True)
        self.assertEqual([], mints)
        self.assertFalse(os.path.exists(AF.record_path(self.root)))

        store.remember(_item("payment rule", about_code=["src/pay.py"]), assume_yes=True)
        self.assertEqual(1, len(mints))            # ...the control

    def test_a_mixed_write_records_only_the_anchored_item(self):
        store = self.store()
        store.remember(_item("a plain fact", "no code named"), assume_yes=True)
        store.remember(_item("payment rule", about_code=["src/pay.py"]), assume_yes=True)
        self.assertEqual(["src/pay.py"], sorted(AF.read_record(self.root)))

    def test_a_symbol_anchor_IS_minted_when_the_store_has_a_graph(self):
        # The mint must pass the store's knowledge layer through, or symbol anchors never get a
        # baseline and the symbol arm is dead from the write side — invisibly, because the
        # no-graph case (below) looks identical.
        class _Ref:
            def __init__(self, path):
                self.path = path

        class _Result:
            def __init__(self, refs):
                self.references, self.degraded = refs, False

        class _Graph:
            is_graph = True

            def supports_kind(self, kind):
                return True

            def query(self, kind, target, depth=1):
                return _Result([_Ref("src/pay.py")])

        class _Layer:
            primary = _Graph()

        store = self.store()
        store.knowledge_layer = _Layer()
        store.remember(_item("payment rule", about_code=["Pay.charge"]), assume_yes=True)
        self.assertIn("Pay.charge", AF.read_record(self.root))

    def test_an_anchor_with_no_evidence_is_not_recorded(self):
        # A symbol anchor with no adopted graph yields no fingerprint — there is nothing honest to
        # write down, so the record stays empty rather than gaining a hollow entry.
        store = self.store()
        store.remember(_item("payment rule", about_code=["Pay.charge"]), assume_yes=True)
        self.assertEqual({}, AF.read_record(self.root))


# ================================================================ (d) idempotent
class Idempotent(_Base):

    def test_re_approving_an_unchanged_anchor_changes_nothing(self):
        store = self.store()
        store.remember(_item("payment rule", about_code=["src/pay.py"]), assume_yes=True)
        with open(AF.record_path(self.root), "rb") as fh:
            first = fh.read()
        store.remember(_item("payment rule two", about_code=["src/pay.py"]), assume_yes=True)
        with open(AF.record_path(self.root), "rb") as fh:
            self.assertEqual(first, fh.read())

    def test_a_later_approval_never_erases_a_pending_staleness_signal(self):
        # P7 at the mint. `refresh=False` is the whole of it: a second decision naming the same
        # anchor must NOT advance the baseline, or the first decision's pending proposal vanishes
        # silently — the exact "quietly relabelled as current" failure STALE-REF exists to stop.
        # The cost (a later decision judged against an earlier baseline) is filed as
        # H-6-ANCHOR-KEYED-PER-FILE in doc 84; it over-proposes, which is the safe direction.
        store = self.store()
        store.remember(_item("first rule", about_code=["src/pay.py"]), assume_yes=True)
        self.write("src/pay.py", "RATE = 2\n")
        self.assertEqual(1, len(self.anchor_props()))

        store.remember(_item("second rule", about_code=["src/pay.py"]), assume_yes=True)
        props = self.anchor_props()
        self.assertEqual({"first rule", "second rule"}, {p.subject for p in props})

    def test_the_mint_never_refreshes(self):
        import ast
        import inspect
        from mokata.memory.store import MemoryStore
        tree = ast.parse(inspect.getsource(MemoryStore._record_code_anchors).lstrip())
        for call in [n for n in ast.walk(tree) if isinstance(n, ast.Call)]:
            for kw in call.keywords:
                self.assertNotEqual("refresh", kw.arg,
                                    "the mint must never re-stamp — P7 at the write path")


if __name__ == "__main__":
    unittest.main()
