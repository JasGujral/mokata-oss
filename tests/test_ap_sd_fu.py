"""AP-SD-FU — the emit PREVIEW shows the derived deferred scope, not the payload's pre-derivation one.

The one-truth derivation lives in `spec_commit` (the write authority): the WriteGate's previewed
`content` (`json.dumps(spec.to_dict())`) was serialized BEFORE derivation, so a human approving an
emit saw the deferred scope UNDERSTATED — the items the approved approach's `decisions[].deferred`
adds were absent from the preview even though they land in the written spec.

This pins the honesty fix: the previewed content DERIVES the scope (so the human sees exactly what
will be written), while the durable write is byte-identical (still `spec_commit`, which derives).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import tempfile
import types
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata.brainstorm import persist_approach                       # noqa: E402
from mokata.engine import Spec, AcceptanceCriterion                  # noqa: E402
from mokata.engine.emit import SPEC_STATE_KEY, commit_spec           # noqa: E402
from mokata.spec_scope import SpecScope, DeferredItem                # noqa: E402
from mokata.state import StateStore                                  # noqa: E402

# reuse the AP-SD fixtures (Decision/DecisionDeferral builders + a session with decisions)
from test_ap_sd import _decision, _deferral, _session_with_decisions  # noqa: E402


def _spec():
    """A spec whose scope declares ONLY an authorized surface — the deferred list is NOT
    hand-written; it must come from the approved approach's decisions[]."""
    return Spec(title="add a retry helper",
                criteria=[AcceptanceCriterion(id="AC1", text="retry wraps a failing call")],
                scope=SpecScope(authorized=("src/retry.py",), deferred=()))


class _CaptureGate:
    """A WriteGate double: captures the previewed WriteRequest, then runs the commit closure."""

    def __init__(self):
        self.request = None

    def submit(self, request, *, commit, **_kw):
        self.request = request
        commit()
        return types.SimpleNamespace(committed=True, reason="ok")


class TestEmitPreviewDerivesScope(unittest.TestCase):

    def test_preview_content_shows_the_derived_deferred_item(self):
        """THE AP-SD-FU regression: the previewed content carries the deferred item DERIVED from the
        approach's decisions[].deferred (F1), not just the payload's empty deferred list. Fails on
        pre-fix code (preview serialized before derivation)."""
        with tempfile.TemporaryDirectory() as root:
            store = StateStore(root)
            persist_approach(_session_with_decisions([_decision(deferred=[_deferral()])]), store)

            gate = _CaptureGate()
            committed, _reason, _size = _emit(store, gate)

            self.assertTrue(committed)
            preview = json.loads(gate.request.content)
            deferred_ids = {d["id"] for d in preview.get("scope", {}).get("deferred", [])}
            self.assertIn("F1", deferred_ids,
                          "the emit preview must show the deferred item derived from decisions[]")

    def test_written_spec_is_unchanged_the_write_still_derives(self):
        """The write authority is untouched: the persisted spec derives the SAME deferred item, so the
        fix changes only what the preview SHOWS, never what is written."""
        with tempfile.TemporaryDirectory() as root:
            store = StateStore(root)
            persist_approach(_session_with_decisions([_decision(deferred=[_deferral()])]), store)
            _emit(store, _CaptureGate())
            written = Spec.from_dict(store.read(SPEC_STATE_KEY))
            self.assertIn("F1", {d.id for d in written.scope.deferred})

    def test_no_decisions_preview_is_byte_identical(self):
        """No decisions[] ⇒ the preview is exactly the payload's scope (the derive step is a no-op)."""
        with tempfile.TemporaryDirectory() as root:
            store = StateStore(root)
            persist_approach(_session_with_decisions([]), store)
            gate = _CaptureGate()
            spec = Spec(title="t", criteria=[AcceptanceCriterion(id="AC1", text="x")],
                        scope=SpecScope(authorized=("a.py",),
                                        deferred=(DeferredItem(id="H1", item="hand"),)))
            commit_spec_via(store, spec, gate)
            preview = json.loads(gate.request.content)
            self.assertEqual({d["id"] for d in preview["scope"]["deferred"]}, {"H1"})


def _emit(store, gate):
    return commit_spec_via(store, _spec(), gate)


def commit_spec_via(store, spec, gate):
    return commit_spec(store, spec, gate=gate, human_approved=True)


if __name__ == "__main__":
    unittest.main()
