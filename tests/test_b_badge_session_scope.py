"""B-BADGE — session-scoped statusline badge (release 0.0.14, re-groom #9, live report 2026-07-15).

The bug: the statusline badge was session-BLIND. Run state deliberately survives `/clear`
(P17 resume — correct), but `progress.find_active_run` resolves ANY persisted run, so a cleared
session kept rendering a dead run's stage strip, and a fresh `/brainstorm`'s newly registered run
could lose resolution to an older one.

The fix (badge resolver only — `find_active_run` is untouched for its other consumers):
  (i)  a run explicitly BOUND to THIS Claude Code session  -> badge that run;
  (ii) else exactly-ONE-live-run narrowing (R-MCP: pid_alive + repo match) -> badge it;
  (iii) else the plain no-run `mokata` badge.

Grounding divergence (ledgered, harness gap upstream #25642): the MCP process that REGISTERS a run
CANNOT learn Claude Code's `session_id` (no env var, no `_meta`, no `initialize` params), so the
spec's step-3 "RUN-REG binds the run to the session" mechanism is impossible today. Per coordinator
directive the binding is instead written by the SessionStart hook (source-aware), and the fresh
`/brainstorm` flip rides live-narrowing (ii) until the next SessionStart. Concurrent-START isolation
is therefore best-effort until #25642 — see `test_concurrent_start_best_effort_is_b_badge_fu` (the
pin that flips when the harness exposes session identity to MCP, doc 84 B-BADGE-FU).

Business-level asserts on rendered badge strings, never internals.
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import _support  # noqa: F401  (puts src/ on the path)

from mokata import MOKATA_DIR, hook_cli, progress
from mokata.badge_run import bind_session_run, read_binding, resolve_badge_run
from mokata.config import Surface
from mokata.govern import PipelineCheckpoint
from mokata.state import StateStore
from mokata.tdd_state import state_dir
from mokata import session_registry as SR

PHASES = ("brainstorm", "analysis", "strawman", "pre_mortem", "probes",
          "completeness_gate", "emit")


# --------------------------------------------------------------------------- fixtures
def _repo(d, profile="standard"):
    from mokata.init import init_repo
    init_repo(root=d, profile=profile, assume_yes=True, out=lambda _: None)
    return Surface.load(d)


def _persist_run(root, run_id, passed=()):
    """A run with a persisted checkpoint on disk (NOT live — no registry entry)."""
    cp = PipelineCheckpoint(Surface.load(root).state, run_id)
    cp.ensure_registered()
    for p in passed:
        cp.mark_passed(p)


def _register_live_run(root, run_id, passed=(), pid=None):
    """A run that is both persisted AND live: its `run_id` sits in the MS.S2 registry with an
    alive pid rooted at this repo (exactly what `_live_runs` narrows on)."""
    _persist_run(root, run_id, passed)
    store = StateStore(state_dir(root))
    reg = store.read(SR.SESSION_REGISTRY_KEY) or {"sessions": {}}
    reg.setdefault("sessions", {})[run_id] = {
        "session_id": run_id, "started_at": "2026-07-15T00:00:00Z",
        "pid": pid if pid is not None else os.getpid(),
        "repo_root": os.path.realpath(root),
        "last_seen": "2026-07-15T00:00:00Z", "phase": None, "scope": None,
    }
    store.write(SR.SESSION_REGISTRY_KEY, reg)


def _make_run_dead(root, run_id):
    """Drop a run's registry entry so it is no longer live (its checkpoint stays on disk)."""
    store = StateStore(state_dir(root))
    reg = store.read(SR.SESSION_REGISTRY_KEY) or {"sessions": {}}
    reg.get("sessions", {}).pop(run_id, None)
    store.write(SR.SESSION_REGISTRY_KEY, reg)


def _run_statusline(payload, argv=None):
    stdin = io.StringIO(json.dumps(payload) if isinstance(payload, dict) else payload)
    out = io.StringIO()
    old = sys.stdin
    sys.stdin = stdin
    try:
        with contextlib.redirect_stdout(out):
            rc = hook_cli.statusline_main(argv or [])
    finally:
        sys.stdin = old
    return rc, out.getvalue()


def _run_session_start(payload):
    stdin = io.StringIO(json.dumps(payload))
    out = io.StringIO()
    old = sys.stdin
    sys.stdin = stdin
    try:
        with contextlib.redirect_stdout(out):
            rc = hook_cli.session_start_main([])
    finally:
        sys.stdin = old
    return rc


def _set_manifest_statusline(root, value):
    mpath = Path(root) / MOKATA_DIR / "manifest.json"
    data = json.loads(mpath.read_text(encoding="utf-8"))
    data.setdefault("settings", {}).setdefault("ux", {})["statusline"] = value
    mpath.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


# =========================================================== the load-bearing regression
class TestBBadgeRegression(unittest.TestCase):
    def test_b_badge_regression(self):
        """Persisted (dead) run from session A + a statusline render for a FRESH session B with no
        binding ⇒ CLEAN badge, no stage strip. This is what the old `find_active_run` got wrong."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "runA", passed=["brainstorm"])   # A's run: on disk, NOT live

            # Old code (find_active_run) DOES pick runA — proving the strip the bug rendered:
            self.assertEqual(progress.find_active_run(surface.state), "runA")

            # New session-aware badge for a fresh session B (no binding, run not live) -> clean.
            badge = progress.build_stage_badge(Surface.load(d), session_id="sessB")
            self.assertEqual(badge, "mokata")
            self.assertNotIn("›", badge)                     # no stage bracket at all
            self.assertNotIn("brainstorm", badge)

    def test_no_session_field_degrade_is_byte_identical(self):
        """No `session_id` in the payload (older harness / non-Claude caller) ⇒ the badge is
        byte-identical to today's `find_active_run`-derived badge. The load-bearing negative."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "runX", passed=["brainstorm"])   # -> spec stage today

            today = progress.build_stage_badge(surface)                       # no session arg
            via_none = progress.build_stage_badge(surface, session_id=None)   # explicit None
            self.assertEqual(via_none, today)
            self.assertIn("›spec‹", today)                   # today still resolves the run

            # statusline payload with NO session_id renders exactly the find_active_run badge.
            rc, out = _run_statusline({"cwd": d})
            self.assertEqual(rc, 0)
            self.assertIn("›spec‹", out)


# =========================================================== flip / bind / isolation
class TestBBadgeSessionBinding(unittest.TestCase):
    def test_new_brainstorm_flip_via_live_narrowing(self):
        """Directive #2: the fresh `/brainstorm` flip rides live-narrowing (ii) — a newly
        registered run that is the ONE live run in the repo flips the badge, with NO binding yet."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _register_live_run(d, "newrun")                  # one live run, fresh -> brainstorm
            self.assertIsNone(read_binding(d, "sessB"))      # no binding — flip is (ii), not (i)
            badge = progress.build_stage_badge(Surface.load(d), session_id="sessB")
            self.assertIn("›brainstorm‹", badge)

    def test_new_brainstorm_flip_via_sessionstart_binding(self):
        """SessionStart (source=startup) with exactly one live run binds THIS session to it, so the
        badge renders that run even once other runs later go live (the explicit (i) path)."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _register_live_run(d, "newrun")                  # sole live run -> SessionStart binds
            self.assertEqual(_run_session_start(
                {"cwd": d, "session_id": "sessB", "source": "startup"}), 0)
            self.assertEqual(read_binding(d, "sessB"), "newrun")   # bound via the real writer
            badge = progress.build_stage_badge(Surface.load(d), session_id="sessB")
            self.assertIn("›brainstorm‹", badge)

    def test_resume_rebind_shows_run_again_nothing_deleted(self):
        """P17 negative: `/mokata:resume` (SessionStart source=resume) attaches the OLD run to the
        new session ⇒ the badge shows it again, and the run state was never deleted.

        The run's checkpoint is COMPLETE (spec emitted) -> the badge sits at `develop`. B-LIFE's
        ship-based retirement leaves this UNCHANGED: a complete checkpoint is the healthy at-develop
        state, not a finished run, so the strip stays. (Retirement fires only once a run SHIPS — see
        B-LIFE's `test_bound_shipped_run_gives_clean_badge`.)"""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _register_live_run(d, "oldrun", passed=list(PHASES))   # complete -> develop
            self.assertEqual(_run_session_start(
                {"cwd": d, "session_id": "sessB", "source": "resume"}), 0)
            self.assertEqual(read_binding(d, "sessB"), "oldrun")
            badge = progress.build_stage_badge(Surface.load(d), session_id="sessB")
            self.assertIn("›develop‹", badge)
            # nothing deleted — the checkpoint survives (it was never the badge's to remove).
            self.assertIsNotNone(StateStore(state_dir(d)).read("pipeline_run__oldrun"))

    def test_two_session_isolation(self):
        """A and B each bound (via the real SessionStart writer, in a realizable sole-live-run
        sequence) to their OWN run ⇒ each render shows its own stage, never the other's — even with
        both runs live at render time (binding (i) short-circuits the ambiguous (ii))."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            # A binds while runA is the sole live run.
            _register_live_run(d, "runA", passed=["brainstorm"])          # -> spec
            _run_session_start({"cwd": d, "session_id": "sessA", "source": "startup"})
            self.assertEqual(read_binding(d, "sessA"), "runA")
            # runA idles; runB comes up as the sole live run; B binds.
            _make_run_dead(d, "runA")
            _register_live_run(d, "runB")                                 # fresh -> brainstorm
            _run_session_start({"cwd": d, "session_id": "sessB", "source": "startup"})
            self.assertEqual(read_binding(d, "sessB"), "runB")
            # both live again, concurrently:
            _register_live_run(d, "runA", passed=["brainstorm"])
            badge_a = progress.build_stage_badge(Surface.load(d), session_id="sessA")
            badge_b = progress.build_stage_badge(Surface.load(d), session_id="sessB")
            self.assertIn("›spec‹", badge_a)
            self.assertNotIn("›brainstorm‹", badge_a)
            self.assertIn("›brainstorm‹", badge_b)
            self.assertNotIn("›spec‹", badge_b)

    def test_clear_never_binds_even_with_one_live_run(self):
        """Directive rule 1: SessionStart source=clear NEVER writes a binding — the clean badge is
        the whole point of `/clear`. A live run present does not change that."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _register_live_run(d, "runA")                    # a live run is present...
            self.assertEqual(_run_session_start(
                {"cwd": d, "session_id": "sessB", "source": "clear"}), 0)
            self.assertIsNone(read_binding(d, "sessB"))      # ...but clear bound nothing

    def test_concurrent_start_best_effort_is_b_badge_fu(self):
        """B-BADGE-FU pin (upstream #25642). Two runs live at once ⇒ SessionStart can't narrow to
        one, so it writes NO binding and the badge falls to the ambiguous (ii) ⇒ clean. When the
        harness exposes session identity to MCP, the binding moves to RUN-REG and this flips to
        rendering the session's own run — at which point this assertion must be updated."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _register_live_run(d, "runA")
            _register_live_run(d, "runB")                    # TWO live runs — ambiguous
            self.assertEqual(_run_session_start(
                {"cwd": d, "session_id": "sessC", "source": "startup"}), 0)
            self.assertIsNone(read_binding(d, "sessC"))      # rule 1: ambiguous -> no binding
            badge = progress.build_stage_badge(Surface.load(d), session_id="sessC")
            self.assertEqual(badge, "mokata")                # best-effort today: clean, not runC


# =========================================================== the resolver, unit-level
class TestResolver(unittest.TestCase):
    def test_binding_wins_over_live_narrowing(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _register_live_run(d, "runA")
            _register_live_run(d, "runB")                    # two live -> (ii) ambiguous
            bind_session_run(d, "sessX", "runB")             # (i) explicit binding
            self.assertEqual(resolve_badge_run(d, "sessX"), "runB")

    def test_binding_to_missing_run_falls_through(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            bind_session_run(d, "sessX", "ghost")            # bound run has no checkpoint
            self.assertIsNone(resolve_badge_run(d, "sessX"))

    def test_single_live_run_narrows(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _register_live_run(d, "only")
            self.assertEqual(resolve_badge_run(d, "unbound-session"), "only")

    def test_no_run_no_binding_is_none(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            self.assertIsNone(resolve_badge_run(d, "sessX"))

    def test_resolve_never_raises_on_junk_root(self):
        # a non-mokata / missing dir must degrade to None, never raise (statusline reader contract)
        self.assertIsNone(resolve_badge_run("/no/such/dir/at/all", "sessX"))
        self.assertIsNone(resolve_badge_run("/no/such/dir/at/all", None))


# =========================================================== statusline contract pins
class TestStatuslineContract(unittest.TestCase):
    def test_exit_zero_with_session_id(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _register_live_run(d, "runA")
            rc, _ = _run_statusline({"cwd": d, "session_id": "sessB"})
            self.assertEqual(rc, 0)

    def test_empty_when_disabled(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _register_live_run(d, "runA")
            _set_manifest_statusline(d, False)
            rc, out = _run_statusline({"cwd": d, "session_id": "sessB"})
            self.assertEqual(rc, 0)
            self.assertEqual(out, "")

    def test_empty_when_uninitialized(self):
        with tempfile.TemporaryDirectory() as d:                 # no mokata repo here
            rc, out = _run_statusline({"cwd": d, "session_id": "sessB"})
            self.assertEqual(rc, 0)
            self.assertEqual(out, "")

    def test_garbage_stdin_exits_zero(self):
        rc, _ = _run_statusline("this is not json at all")
        self.assertEqual(rc, 0)

    def test_statusline_renders_bound_run(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _register_live_run(d, "runA", passed=["brainstorm"])
            bind_session_run(d, "sessB", "runA")
            rc, out = _run_statusline({"cwd": d, "session_id": "sessB"})
            self.assertEqual(rc, 0)
            self.assertIn("›spec‹", out)


if __name__ == "__main__":
    unittest.main()
