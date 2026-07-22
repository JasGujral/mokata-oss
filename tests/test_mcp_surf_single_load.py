"""MCP-SURF — ONE `Surface.load` per MCP tool call (doc 84 §7; the cheap slice of 0.1.1's F7).

Pure plumbing, zero behavior change. Every mokata MCP write tool was rebuilding the entire config
world up to THREE times inside a single invocation — `remember` resolved a `Surface` at its top,
then `_consent` -> `_trust` loaded a second one to read the trust dial, then (on the commit path)
`_policy` -> `_trust` loaded a THIRD. Each load is a manifest parse plus a fresh Router and Detector.
On top of that, `Surface.state` was an UNCACHED property that rebuilt a StateStore and re-ran the
session-scoping resolution on all ~70 of its call sites.

The fix threads the already-resolved surface into `_trust`/`_policy`/`_consent` (an OPTIONAL
`surface=` parameter — every existing path-based caller is untouched) and makes `Surface.state` a
`functools.cached_property`.

What this file pins:
  (a) the LOAD COUNT — deterministic, counted through a spy on `Surface.load`, not timed: exactly
      ONE per tool call on both the propose and the commit path, down from 2 and 3;
  (b) the state CACHE — same object within one Surface, a fresh one per new Surface (no leak);
  (c) ZERO BEHAVIOR CHANGE — a passing gate still commits, a refusal still refuses with the same
      reason code, a proposal still carries the same id and preview, in BOTH the surface-passed and
      the path-only mode;
  (d) DEGRADE-CLEAN — an uninitialized repo still reads as the gated `TrustPolicy` default through
      BOTH modes (the path that lets `init` itself run).

Secret-safety: n/a for this stage — it is plumbing. No new surface renders any value, no content
crosses a boundary it did not cross before, and the WriteGate's secret scan is untouched (the
`_gated_write` body is not modified by MCP-SURF).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401
from _support import mcp_commit

from mokata.config import Surface                                  # noqa: E402
from mokata.govern.trust import MCP_SURFACE, REFUSED_READ_ONLY     # noqa: E402
from mokata.init import init_repo                                  # noqa: E402
from mokata.mcp import consent as C                                # noqa: E402
from mokata.mcp import tools_write as TW                           # noqa: E402


class _LoadSpy:
    """Count `Surface.load` calls while still performing the real load.

    Deterministic by construction: it counts CALLS, never elapsed time, so the assertion is a fact
    about control flow rather than a performance measurement that could flake on a busy machine."""

    def __init__(self):
        self.n = 0
        self._real = Surface.load.__func__          # the underlying function of the classmethod

    def __enter__(self):
        spy = self

        def counting(cls, root=".", detector=None):
            spy.n += 1
            return spy._real(cls, root, detector)

        self._patch = mock.patch.object(Surface, "load", classmethod(counting))
        self._patch.start()
        return self

    def __exit__(self, *exc):
        self._patch.stop()
        return False


def _repo(d):
    init_repo(d, profile="standard", assume_yes=True)
    return d


class TestSingleLoadPerCall(unittest.TestCase):
    """(a) exactly ONE Surface.load per write-tool invocation."""

    def test_remember_propose_path_loads_the_surface_once(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            with _LoadSpy() as spy:
                res = TW.remember(path=d, subject="db", value="postgres")
            self.assertEqual(res["status"], "proposed")
            self.assertEqual(spy.n, 1,
                             "remember must resolve the Surface ONCE (was 2 on the propose path: "
                             "the tool's own load + _consent -> _trust)")

    def test_remember_commit_path_loads_the_surface_once(self):
        """The commit path is where the old cost was worst — it paid the THIRD load in `_policy`."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            proposed = TW.remember(path=d, subject="db", value="postgres")
            pid = proposed["proposal_id"]
            from mokata import approval
            from mokata.govern.ledger import AuditLedger
            approval.approve(d, pid, actor="test-human",
                             ledger=AuditLedger.from_mokata_dir(os.path.join(d, ".mokata")))
            with _LoadSpy() as spy:
                res = TW.remember(path=d, subject="db", value="postgres", proposal_id=pid)
            self.assertTrue(res["committed"], "the gate must still COMMIT under a human approval")
            self.assertEqual(spy.n, 1,
                             "commit path must load ONCE (was 3: tool + _consent->_trust + "
                             "_policy->_trust)")

    def test_other_write_tools_also_load_once(self):
        """The same pattern held across every write tool that resolves a surface at its top."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            for tool, kw in ((TW.memory_export, {}),
                             (TW.apply_proposal, {"subject": "nothing-pending"}),
                             (TW.spec_check, {"symbols": "foo"})):
                with self.subTest(tool=tool.__name__):
                    with _LoadSpy() as spy:
                        tool(path=d, **kw)
                    self.assertLessEqual(spy.n, 1,
                                         f"{tool.__name__} must resolve the Surface at most once")


class TestStateIsCachedPerInstance(unittest.TestCase):
    """(b) `Surface.state` caches per instance — and ONLY per instance."""

    def test_state_is_the_same_object_within_one_surface(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            surface = Surface.load(d)
            self.assertIs(surface.state, surface.state,
                          "state must be built once per Surface (cached_property)")

    def test_a_new_surface_gets_its_own_state(self):
        """No cross-instance leak: the cache lives in the instance __dict__, not on the class."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            a, b = Surface.load(d), Surface.load(d)
            self.assertIsNot(a.state, b.state,
                             "a FRESH Surface must build its OWN state — a class-level cache would "
                             "leak one repo's store into another's")
            self.assertEqual(a.state.root, b.state.root, "same repo -> same physical root")

    def test_the_cached_store_still_reads_through_to_disk(self):
        """The cache holds ADDRESSING, not data — a write is visible on the very next read, which
        is what makes caching safe here rather than a staleness bug."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            surface = Surface.load(d)
            store = surface.state
            store.write("memory_stats", {"n": 1})
            # a SECOND Surface (a separate "process") sees the write through its own cached store.
            self.assertEqual(Surface.load(d).state.read("memory_stats"), {"n": 1})
            store.write("memory_stats", {"n": 2})
            self.assertEqual(surface.state.read("memory_stats"), {"n": 2},
                             "the cached store must still hit disk on every read")


class TestZeroBehaviorChange(unittest.TestCase):
    """(c) the whole bar: the surface-passed and path-only modes are indistinguishable."""

    def test_trust_agrees_in_both_modes(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            surface = Surface.load(d)
            by_path, by_surface = C._trust(d), C._trust(d, surface)
            self.assertEqual(by_path.can_write("remember", MCP_SURFACE),
                             by_surface.can_write("remember", MCP_SURFACE))
            self.assertEqual(by_path.levels, by_surface.levels)
            self.assertEqual(by_path.default, by_surface.default)

    def test_policy_agrees_in_both_modes(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            surface = Surface.load(d)
            a = C._policy(d, "remember", human_approved=True)
            b = C._policy(d, "remember", human_approved=True, surface=surface)
            self.assertEqual((a.tool, a.surface, a.human_approved),
                             (b.tool, b.surface, b.human_approved))
            self.assertEqual(a.trust.levels, b.trust.levels)
            self.assertEqual(a.trust.default, b.trust.default)

    def test_a_read_only_dial_still_refuses_identically_in_both_modes(self):
        """The refusal is the sharpest zero-change probe: same reason CODE, both ways in."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            # `settings.trust` is a FLAT {tool-or-surface: level} map (govern/trust.py:76-88).
            import json
            mpath = os.path.join(d, ".mokata", "manifest.json")
            with open(mpath, encoding="utf-8") as fh:
                m = json.load(fh)
            m.setdefault("settings", {})["trust"] = {"remember": "read-only"}
            with open(mpath, "w", encoding="utf-8") as fh:
                json.dump(m, fh)

            surface = Surface.load(d)
            by_path = C._consent(d, "remember", {"path": d}, "")
            by_surface = C._consent(d, "remember", {"path": d}, "", surface=surface)
            self.assertTrue(by_path.refused and by_surface.refused)
            self.assertEqual(by_path.code, by_surface.code)
            self.assertEqual(by_path.code, REFUSED_READ_ONLY)

            # and end-to-end through the tool: still a refusal, still the same code.
            res = TW.remember(path=d, subject="db", value="postgres")
            self.assertEqual(res["status"], "refused")
            self.assertEqual(res["reason_code"], REFUSED_READ_ONLY)

    def test_the_gate_still_gates_end_to_end(self):
        """propose -> human approves -> redeem commits, and the proposal id/preview are stable."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            proposed = TW.remember(path=d, subject="db", value="postgres")
            self.assertEqual(proposed["status"], "proposed")
            self.assertFalse(proposed["committed"], "a bare call must NEVER commit (P2)")
            self.assertTrue(proposed["proposal_id"].startswith("p-"))
            self.assertIn("postgres", proposed["preview"])

            committed = mcp_commit(TW.remember, path=d, subject="db", value="postgres")
            self.assertTrue(committed["committed"])
            self.assertEqual(committed["status"], "committed")


class TestDegradeClean(unittest.TestCase):
    """(d) an UNINITIALIZED repo reads as the gated default — in BOTH modes."""

    def test_trust_degrades_to_the_gated_default_by_path(self):
        with tempfile.TemporaryDirectory() as d:
            # no `mokata init` at all — no manifest, so no dial to read.
            trust = C._trust(d)
            self.assertTrue(trust.can_write("init", MCP_SURFACE),
                            "init must remain able to run on an uninitialized repo")

    def test_trust_degrades_to_the_gated_default_with_a_surface(self):
        """The surface-passed branch degrades identically. `TrustPolicy.from_surface` itself falls
        back to the empty (= gated-write default) policy for a surface with no usable manifest
        (govern/trust.py:108-115), and MCP-SURF's `surface` branch sits inside the SAME try/except
        as the path branch — so neither mode can fail open or fail shut where the other would not."""
        class _NoManifest:                     # a surface-shaped object with nothing to read
            manifest = None

        trust = C._trust("/nonexistent", _NoManifest())
        self.assertTrue(trust.can_write("init", MCP_SURFACE))
        self.assertEqual(trust.levels, {})
        # and it matches what the PATH mode returns for an equally unreadable repo.
        self.assertEqual(trust.levels, C._trust("/nonexistent").levels)
        self.assertEqual(trust.default, C._trust("/nonexistent").default)

    def test_consent_still_reaches_the_proposal_stage_uninitialized(self):
        with tempfile.TemporaryDirectory() as d:
            gate = C._consent(d, "init", {"path": d}, "")
            self.assertFalse(gate.refused,
                             "an uninitialized repo must not read as a read-only dial")


if __name__ == "__main__":
    unittest.main()
