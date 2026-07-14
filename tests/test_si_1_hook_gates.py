"""SI.1 — HOOK-ENFORCED RUN-STATE GATES (0.0.13 seatbelt cluster, stage 2).

The bug this closes: every mokata gate fired INSIDE mokata's MCP write tools, so the model could
bypass all of them by using its NATIVE Write/Edit — "the whole seatbelt has a door with no lock"
(doc 74). This stage puts the lock on: a `PreToolUse` hook on the native file-mutation tools that
decides from PERSISTED state and exits 2 on a violation. The gate stops being a sentence the model
may ignore and becomes an exit code it cannot.

The enforced table (both gates POSITIVELY triggered — they fire only inside an active mokata run,
so a mokata repo being hand-edited outside a run is never policed):

    approach absent AND spec absent  -> exit 0   no active run; not our business
    approach present, spec absent    -> exit 2   `spec-persisted` (coding before the spec)
    spec present, red-set EMPTY      -> exit 2   `no-code-without-failing-test`
    spec present, red-set NON-EMPTY  -> exit 0   a failing test is on record (RED = permission)
    target is a TEST file            -> exit 0   always (you must be able to write the failing test)

Note the DIRECTION of the TDD gate, which these tests pin hard: RED is the PERMISSION to implement
(`TddGuard.allow_implementation(t)` is `t in self._red`), so the violation is an implementation
write with NO failing test on record at all — never "a write while RED", which is the state
`/mokata:develop` exists for. A greened red-set still permits (the red-set is a high-water mark,
matching red-membership semantics); a refinement cycle re-records RED.

What these tests pin:
  (a) THE regression: a REAL subprocess PreToolUse invocation (real stdin JSON, the hook's actual
      situation) — an implementation Write with no failing test on record exits 2 and NAMES what is
      owed; the test file itself is allowed; a failing test on record exits 0.
  (b) The spec gate: pre-spec implementation write blocked / allowed exactly per the persisted
      signal; the unreliable signals (`pipeline_run__*`) are proven NOT to gate anything.
  (c) Override (P14): a ledgered, session-scoped override makes the hook allow; the ledger entry is
      present; a NEW session enforces again; and NO env var can bypass a gate (grep-guard).
  (d) Window identity: a pinned run_id enforces correctly; two candidate runs -> exit 0 + a notice
      shown ONCE; wrong-window blocking is structurally impossible (asserted against a red/green
      pair — the hook must not pick either).
  (e) Fail-open honesty: corrupt state, absent state, a non-mokata repo, an unparseable envelope,
      and a raising decision core ALL exit 0 — the hook never wedges the editor.
  (f) Latency: the no-op path (a non-mokata repo) stays inside the stated budget, measured here.
  (g) Registration round-trip: `setup claude` wires BOTH PreToolUse hooks, re-setup REFRESHES
      (never duplicates), and unsetup removes them while leaving the user's own entries intact.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import contextlib
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from mokata import gate_hook as G                                  # noqa: E402
from mokata import hook_cli                                        # noqa: E402
from mokata import tdd_state as T                                  # noqa: E402
from mokata.config import STATE_DIRNAME, Surface, find_project_root  # noqa: E402
from mokata.govern import TddGuard                                 # noqa: E402
from mokata.govern.ledger import AuditLedger                       # noqa: E402

RUN = "run0123456789abcdef"

# The latency budget for the hook's no-op path (a native write in a NON-mokata repo — the case that
# must cost nothing). Measured against the interpreter+import floor, not an absolute wall time: the
# gate hook must be no more expensive than the secret-guard already on that same event. Generous
# here because CI machines are slow and a flaky perf test is worse than no perf test; the real
# measured number is in the stage report.
NOOP_BUDGET_SECS = 1.0


def _repo(d):
    """An initialized mokata repo at `d`."""
    from mokata.init import init_repo
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
    return Surface.load(d)


def _state_dir(root):
    return G.state_dir(root) if hasattr(G, "state_dir") else T.state_dir(root)


def _raw_store(root):
    """A StateStore on the raw state dir (NOT session-scoped) so tests write the exact physical
    keys the hook reads — `approved_approach__<run>`, `emitted_spec__<run>`, `tdd_phase__<run>`."""
    from mokata.state import StateStore
    return StateStore(T.state_dir(root))


def _approve(root, run=RUN):
    _raw_store(root).write(G.APPROACH_PREFIX + run, {"approach": "the chosen one"})


def _emit_spec(root, run=RUN):
    _raw_store(root).write(G.SPEC_PREFIX + run, {"criteria": [{"id": "AC1", "text": "it works"}]})


def _record_red(root, run=RUN, test_id="test_login"):
    _raw_store(root).write(T.state_key(run), T.to_state(run, red=[test_id], green=[]))


def _record_green(root, run=RUN, test_id="test_login"):
    _raw_store(root).write(T.state_key(run),
                           T.to_state(run, red=[test_id], green=[test_id]))


def _envelope(path, cwd, tool="Write", session_id="cc-session-1"):
    """A real Claude Code PreToolUse envelope."""
    return json.dumps({
        "session_id": session_id,
        "transcript_path": "/tmp/transcript.jsonl",
        "cwd": cwd,
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": {"file_path": path, "content": "def login(): return True\n"},
    })


def _hook(path, cwd, tool="Write", session_id="cc-session-1", env=None, timeout=30):
    """Invoke the hook the way Claude Code does: a REAL subprocess, the envelope on stdin, and the
    exit code + stderr as the only outputs that matter. This is the hook's actual situation — not a
    function call with the module already imported."""
    e = dict(os.environ)
    e.pop("MOKATA_SESSION_ID", None)          # a stray pin in the dev env must not leak into tests
    e["PYTHONPATH"] = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
    if env:
        e.update(env)
    proc = subprocess.run(
        [sys.executable, "-c",
         "import sys; from mokata.hook_cli import gate_guard_main; sys.exit(gate_guard_main([]))"],
        input=_envelope(path, cwd, tool, session_id), text=True, capture_output=True,
        env=e, timeout=timeout, cwd=cwd,
    )
    return proc.returncode, proc.stderr


# ======================================================================================
# (a) THE regression — the native path is now gated
# ======================================================================================

class TestSI1Regression(unittest.TestCase):
    """A native Write to an implementation file, inside an active run, with no failing test on
    record — the exact hole doc 74 named. It must now exit 2."""

    def test_impl_write_with_no_failing_test_on_record_is_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _approve(d), _emit_spec(d)                 # an active run, past the spec
            impl = os.path.join(d, "src", "auth.py")

            code, err = _hook(impl, d, env={"MOKATA_SESSION_ID": RUN})

            self.assertEqual(code, 2, f"native impl write was NOT blocked: {err!r}")
            self.assertIn(G.GATE_TDD, err)             # the gate is named
            self.assertIn("no failing test", err)      # WHAT is owed
            self.assertIn("/mokata:test", err)         # the fix
            self.assertIn("mokata gate override", err)  # the exact override command

    def test_the_owed_test_file_itself_is_always_allowed(self):
        """The gate must never block the very write that would satisfy it."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _approve(d), _emit_spec(d)
            for test_path in ("tests/test_auth.py", "src/auth_test.go", "web/auth.test.ts",
                              "spec/auth.spec.ts", "tests/deep/nested/test_x.py"):
                code, err = _hook(os.path.join(d, test_path), d,
                                  env={"MOKATA_SESSION_ID": RUN})
                self.assertEqual(code, 0, f"{test_path} was blocked: {err!r}")

    def test_red_on_record_permits_implementation(self):
        """RED is the PERMISSION to implement — the direction that matters."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _approve(d), _emit_spec(d), _record_red(d)
            code, err = _hook(os.path.join(d, "src", "auth.py"), d,
                              env={"MOKATA_SESSION_ID": RUN})
            self.assertEqual(code, 0, f"impl blocked despite a failing test on record: {err!r}")

    def test_greened_red_set_still_permits(self):
        """A greened red-set still licenses implementation for the rest of the run — matching
        `allow_implementation`'s red-MEMBERSHIP semantics (the red-set is a high-water mark, not a
        level). Refactoring after green is not a gate violation."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _approve(d), _emit_spec(d), _record_green(d)
            self.assertEqual(T.read_tdd_phase(d, RUN).phase, T.PHASE_GREEN)   # phase IS green
            code, err = _hook(os.path.join(d, "src", "auth.py"), d,
                              env={"MOKATA_SESSION_ID": RUN})
            self.assertEqual(code, 0, f"post-green refactor was blocked: {err!r}")

    def test_the_gate_matches_the_in_tool_guard_it_shadows(self):
        """The hook is a NET UNDER `TddGuard`, not a second opinion: where the guard refuses
        implementation (no RED on record), the hook blocks; where the guard allows it, the hook
        allows. Same gate id, same meaning, new enforcement point."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _approve(d), _emit_spec(d)
            guard = TddGuard(store=_raw_store(d), run_id=RUN)

            self.assertFalse(guard.allow_implementation("test_login"))        # guard: refuse
            self.assertFalse(G.check_write(d, os.path.join(d, "a.py"), RUN).allowed)  # hook: block

            guard.record_red("test_login")
            self.assertTrue(guard.allow_implementation("test_login"))         # guard: allow
            self.assertTrue(G.check_write(d, os.path.join(d, "a.py"), RUN).allowed)   # hook: allow
            self.assertEqual(G.GATE_TDD, "no-code-without-failing-test")      # doc 85 §4's id
            del surface


# ======================================================================================
# (b) the spec gate + the signals that are NOT enforceable
# ======================================================================================

class TestSpecGate(unittest.TestCase):

    def test_pre_spec_impl_write_is_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _approve(d)                                # approach approved, NO spec emitted
            code, err = _hook(os.path.join(d, "src", "auth.py"), d,
                              env={"MOKATA_SESSION_ID": RUN})
            self.assertEqual(code, 2, f"pre-spec impl write was NOT blocked: {err!r}")
            self.assertIn(G.GATE_SPEC, err)
            self.assertIn("/mokata:spec", err)

    def test_spec_emitted_clears_the_spec_gate(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _approve(d), _emit_spec(d), _record_red(d)
            out = G.check_write(d, os.path.join(d, "src", "auth.py"), RUN)
            self.assertTrue(out.allowed)
            self.assertNotEqual(out.gate, G.GATE_SPEC)

    def test_no_active_run_is_never_policed(self):
        """The positive trigger, and the harness-usability floor: a mokata repo with no active run
        is ordinary code you can hand-edit. Blocking here would be house arrest, and a gate that
        makes the editor unusable gets uninstalled."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            # no approved_approach, no emitted_spec — but a stale RED from some earlier run exists
            _record_red(d, run="some-other-old-run")
            code, err = _hook(os.path.join(d, "src", "auth.py"), d,
                              env={"MOKATA_SESSION_ID": RUN})
            self.assertEqual(code, 0, f"hand-editing outside a run was blocked: {err!r}")

    def test_pipeline_checkpoint_is_not_used_to_gate(self):
        """`pipeline_run__<rid>` IS persisted, but its phase vocabulary is brainstorm's INNER phases
        (`brainstorm.PIPELINE_PHASES`: analysis/strawman/pre_mortem/…) — it has no "spec emitted" or
        "test written" phase, so it cannot decide a code write. Proven fail-open: a checkpoint alone
        gates nothing."""
        from mokata.brainstorm import PIPELINE_PHASES
        from mokata.govern.resume import CHECKPOINT_PREFIX
        self.assertNotIn("spec", PIPELINE_PHASES)
        self.assertNotIn("develop", PIPELINE_PHASES)

        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _raw_store(d).write(CHECKPOINT_PREFIX + RUN, {"run_id": RUN, "passed": ["brainstorm"]})
            code, _ = _hook(os.path.join(d, "src", "auth.py"), d,
                            env={"MOKATA_SESSION_ID": RUN})
            self.assertEqual(code, 0)                  # a checkpoint alone never blocks

    def test_non_source_files_are_out_of_scope(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _approve(d)                                # the spec gate WOULD fire on a .py here
            for benign in ("README.md", "docs/guide.md", "package.json", "config.yaml", "a.txt"):
                out = G.check_write(d, os.path.join(d, benign), RUN)
                self.assertTrue(out.allowed, f"{benign} was blocked")


# ======================================================================================
# (c) P14 override — explicit, re-confirmed, session-scoped, ledgered; no env side door
# ======================================================================================

class TestOverride(unittest.TestCase):

    def _override(self, d, gate, reason="shipping a hotfix"):
        from mokata.cli import main
        return main(["gate", "override", gate, "--reason", reason, "--yes",
                     "--run", RUN, "--path", d])

    def test_ledgered_session_scoped_override_makes_the_hook_allow(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _approve(d), _emit_spec(d)
            impl = os.path.join(d, "src", "auth.py")

            code, _ = _hook(impl, d, env={"MOKATA_SESSION_ID": RUN})
            self.assertEqual(code, 2)                                  # enforcing

            self.assertEqual(self._override(d, G.GATE_TDD), 0)

            code, _ = _hook(impl, d, env={"MOKATA_SESSION_ID": RUN})
            self.assertEqual(code, 0, "the ledgered override was not honored")

            # the ledger records who / when / what-scope / why
            entries = list(AuditLedger.from_mokata_dir(surface.mokata_dir).entries())
            overrides = [e for e in entries if e.get("kind") == "gate_override"]
            self.assertEqual(len(overrides), 1)
            rec = overrides[0]
            self.assertEqual(rec["gate"], G.GATE_TDD)
            self.assertEqual(rec["run"], RUN)
            self.assertEqual(rec["scope"], "session")
            self.assertEqual(rec["decision"], "override")
            self.assertEqual(rec["actor"], "human")
            self.assertEqual(rec["reason"], "shipping a hotfix")
            self.assertIn("at", rec)                               # when
            self.assertIn("entry_hash", rec)                       # chained (MS.S3) — tamper-evident

    def test_a_new_session_enforces_again(self):
        """Session-scoped: the override is keyed by run_id, so a NEW session (a new run_id) has no
        override file and the gate is back. Nothing to remember to turn off."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _approve(d), _emit_spec(d)
            self.assertEqual(self._override(d, G.GATE_TDD), 0)
            impl = os.path.join(d, "src", "auth.py")
            self.assertEqual(_hook(impl, d, env={"MOKATA_SESSION_ID": RUN})[0], 0)

            # a NEW session: same repo, same run-state, new run_id
            new_run = "run-fresh-session-9999"
            _approve(d, run=new_run), _emit_spec(d, run=new_run)
            code, err = _hook(impl, d, env={"MOKATA_SESSION_ID": new_run})
            self.assertEqual(code, 2, f"the override survived into a new session: {err!r}")

    def test_override_is_scoped_to_its_gate_only(self):
        """Overriding the TDD gate does not unlock the spec gate — explicit means explicit."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _approve(d)                                # pre-spec: the SPEC gate is what fires
            self.assertEqual(self._override(d, G.GATE_TDD), 0)
            code, err = _hook(os.path.join(d, "src", "auth.py"), d,
                              env={"MOKATA_SESSION_ID": RUN})
            self.assertEqual(code, 2, "a TDD override wrongly cleared the SPEC gate")
            self.assertIn(G.GATE_SPEC, err)

    def test_clear_restores_enforcement_and_is_ledgered(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _approve(d), _emit_spec(d)
            self._override(d, G.GATE_TDD)
            from mokata.cli import main
            self.assertEqual(main(["gate", "clear", "--run", RUN, "--path", d]), 0)
            self.assertEqual(_hook(os.path.join(d, "src", "auth.py"), d,
                                   env={"MOKATA_SESSION_ID": RUN})[0], 2)
            kinds = [e for e in AuditLedger.from_mokata_dir(surface.mokata_dir).entries()
                     if e.get("kind") == "gate_override"]
            self.assertEqual([k["decision"] for k in kinds], ["override", "cleared"])

    def test_override_requires_a_reason(self):
        from mokata.cli import main
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            with self.assertRaises(SystemExit):        # argparse: --reason is required
                main(["gate", "override", G.GATE_TDD, "--yes", "--run", RUN, "--path", d])

    def test_declining_the_reconfirmation_leaves_the_gate_enforced(self):
        """P14 re-confirmation: without an explicit yes (non-interactive stdin -> No, fail-closed),
        nothing is written and the gate still enforces."""
        from mokata.cli import main
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _approve(d), _emit_spec(d)
            rc = main(["gate", "override", G.GATE_TDD, "--reason", "nope",
                       "--run", RUN, "--path", d])          # no --yes, stdin not a TTY -> declined
            self.assertEqual(rc, 1)
            self.assertEqual(G.read_override(d, RUN), frozenset())
            self.assertEqual(_hook(os.path.join(d, "src", "auth.py"), d,
                                   env={"MOKATA_SESSION_ID": RUN})[0], 2)

    def test_no_env_var_can_bypass_a_gate(self):
        """A blanket env-var kill switch is a SIDE DOOR, not an override: any process (or an
        over-eager agent) can set it silently, with no human, no scope, and no ledger. There is
        none — proven behaviourally AND by a grep-guard on the source."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _approve(d), _emit_spec(d)
            impl = os.path.join(d, "src", "auth.py")
            for var in ("MOKATA_SKIP_GATES", "MOKATA_NO_GATES", "MOKATA_DISABLE_GATES",
                        "MOKATA_GATE_OVERRIDE", "MOKATA_FORCE", "SKIP_GATES", "MOKATA_BYPASS"):
                code, _ = _hook(impl, d, env={"MOKATA_SESSION_ID": RUN, var: "1"})
                self.assertEqual(code, 2, f"{var} bypassed the gate")

        # grep-guard: the ONLY env var the hook path may consult is the run-id PIN (which selects
        # WHICH run to enforce — it never disables enforcement).
        with open(G.__file__, encoding="utf-8") as fh:
            src = fh.read()
        env_reads = [ln for ln in src.splitlines()
                     if "os.environ" in ln and not ln.strip().startswith("#")]
        self.assertEqual(len(env_reads), 1, f"unexpected env reads in the hook path: {env_reads}")
        self.assertIn("MOKATA_SESSION_ID", env_reads[0])


# ======================================================================================
# (d) window identity — never guess, never block the wrong window
# ======================================================================================

class TestWindowIdentity(unittest.TestCase):

    def test_pinned_run_id_enforces(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _approve(d), _emit_spec(d)
            self.assertEqual(_hook(os.path.join(d, "src", "a.py"), d,
                                   env={"MOKATA_SESSION_ID": RUN})[0], 2)

    def test_sole_run_needs_no_pin(self):
        """The unambiguous single-window case (SI.2's contract): exactly one run has state, so
        there is nothing to guess between."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _approve(d), _emit_spec(d)
            code, err = _hook(os.path.join(d, "src", "a.py"), d)   # NO pin
            self.assertEqual(code, 2, f"the sole run did not enforce: {err!r}")

    def test_two_windows_are_ambiguous_and_never_block(self):
        """WRONG-WINDOW BLOCKING IS IMPOSSIBLE. Two runs have state; one is mid-violation (spec, no
        RED) and the other is fine (RED on record). With no pin the hook must not pick EITHER — it
        exits 0. This is the assertion that makes the fail-open contract structural: there is no
        code path in which the hook enforces run A's state against run B's window."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _approve(d, run="run-A"), _emit_spec(d, run="run-A")               # A: would BLOCK
            _approve(d, run="run-B"), _emit_spec(d, run="run-B")
            _record_red(d, run="run-B")                                        # B: would ALLOW

            run = G.resolve_run(d)
            self.assertTrue(run.ambiguous)
            self.assertIsNone(run.run_id)                    # picked NEITHER
            self.assertEqual(set(run.candidates), {"run-A", "run-B"})

            code, err = _hook(os.path.join(d, "src", "a.py"), d)                # no pin
            self.assertEqual(code, 0, f"the hook guessed a window and blocked: {err!r}")

    def test_the_ambiguity_notice_is_shown_once_per_window(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _approve(d, run="run-A"), _approve(d, run="run-B")
            impl = os.path.join(d, "src", "a.py")

            c1, e1 = _hook(impl, d, session_id="cc-1")
            c2, e2 = _hook(impl, d, session_id="cc-1")          # SAME window
            c3, e3 = _hook(impl, d, session_id="cc-2")          # a DIFFERENT window

            self.assertEqual((c1, c2, c3), (0, 0, 0))           # never a block
            self.assertIn("gates are OFF", e1)                  # said once...
            self.assertEqual(e2.strip(), "")                    # ...and not again
            self.assertIn("gates are OFF", e3)                  # once per WINDOW

    def test_a_pin_disambiguates_two_windows(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _approve(d, run="run-A"), _emit_spec(d, run="run-A")               # would BLOCK
            _approve(d, run="run-B"), _emit_spec(d, run="run-B")
            _record_red(d, run="run-B")                                        # would ALLOW
            impl = os.path.join(d, "src", "a.py")
            self.assertEqual(_hook(impl, d, env={"MOKATA_SESSION_ID": "run-A"})[0], 2)
            self.assertEqual(_hook(impl, d, env={"MOKATA_SESSION_ID": "run-B"})[0], 0)


# ======================================================================================
# (e) fail-open honesty — the hook never wedges the editor
# ======================================================================================

class TestFailOpen(unittest.TestCase):

    def test_non_mokata_repo_is_instant_zero(self):
        with tempfile.TemporaryDirectory() as d:
            code, err = _hook(os.path.join(d, "src", "a.py"), d)
            self.assertEqual(code, 0)
            self.assertEqual(err.strip(), "")
            self.assertIsNone(G.find_mokata_root(d))

    def test_corrupt_state_fails_open(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _approve(d), _emit_spec(d)
            # a corrupt TDD file: unreadable red-set -> we must NOT block on a guess
            with open(os.path.join(T.state_dir(d), T.state_key(RUN) + ".json"), "w",
                      encoding="utf-8") as fh:
                fh.write("{not json at all")
            code, _ = _hook(os.path.join(d, "src", "a.py"), d,
                            env={"MOKATA_SESSION_ID": RUN})
            self.assertEqual(code, 0, "a corrupt state file caused a block")

    def test_corrupt_override_fails_closed_on_the_override_not_the_gate(self):
        """A broken override file must not silently DISABLE a gate — it reads as 'no override', so
        the gate keeps enforcing. (Fail-open is about not blocking on uncertainty; it is not a
        licence for a corrupt file to unlock a gate.)"""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _approve(d), _emit_spec(d)
            with open(os.path.join(T.state_dir(d), G.override_key(RUN) + ".json"), "w",
                      encoding="utf-8") as fh:
                fh.write("{{{garbage")
            self.assertEqual(G.read_override(d, RUN), frozenset())
            self.assertEqual(_hook(os.path.join(d, "src", "a.py"), d,
                                   env={"MOKATA_SESSION_ID": RUN})[0], 2)

    def test_unparseable_envelope_and_empty_stdin_fail_open(self):
        env = dict(os.environ)
        env["PYTHONPATH"] = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
        for payload in ("", "not json", "[]", '{"tool_input": null}', "{}"):
            proc = subprocess.run(
                [sys.executable, "-c",
                 "import sys; from mokata.hook_cli import gate_guard_main; "
                 "sys.exit(gate_guard_main([]))"],
                input=payload, text=True, capture_output=True, env=env, timeout=30)
            self.assertEqual(proc.returncode, 0, f"payload {payload!r} did not fail open")

    def test_a_raising_decision_core_still_exits_zero_but_says_so_loudly(self):
        """The outermost fail-open floor: even if `check_write` itself blows up, the hook allows.

        D5 — and it is no longer SILENT about it. Failing open means the gate is enforcing NOTHING
        while the badge and the docs tell the user governance is on; the write is still allowed (the
        fail-open contract is untouched, rc 0), it just stops being a secret."""
        import io
        import mokata.gate_hook as gh
        from mokata import degrade
        original = gh.check_write
        gh.check_write = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        err = io.StringIO()
        degrade.reset_degrade_notices()
        try:
            with tempfile.TemporaryDirectory() as d, contextlib.redirect_stderr(err):
                _repo(d)
                rc = hook_cli.gate_guard_main(["--path", os.path.join(d, "a.py"), "--cwd", d])
            self.assertEqual(rc, 0, "the fail-open floor must still allow the write")
            self.assertIn("DEGRADED", err.getvalue())
            self.assertIn("FAILING OPEN", err.getvalue())
            self.assertIn("mokata doctor", err.getvalue())
            # the notice is RECORDED, not just printed — doctor can answer "what degraded?"
            self.assertIn("gate-guard", [n.subsystem for n in degrade.emitted_notices()])
        finally:
            gh.check_write = original
            degrade.reset_degrade_notices()

    def test_check_write_never_raises(self):
        with tempfile.TemporaryDirectory() as d:
            for path in ("", "a.py", "/nonexistent/x.py", "\x00bad"):
                try:
                    G.check_write(d, path)
                except Exception as exc:                       # noqa: BLE001
                    self.fail(f"check_write raised on {path!r}: {exc}")


# ======================================================================================
# (f) latency + the cheap import surface
# ======================================================================================

class TestLatency(unittest.TestCase):

    def test_noop_path_is_within_budget(self):
        """The case that must cost nothing: a native write in a NON-mokata repo."""
        with tempfile.TemporaryDirectory() as d:
            _hook(os.path.join(d, "a.py"), d)                  # warm the interpreter/import cache
            samples = []
            for _ in range(5):
                t0 = time.perf_counter()
                code, _ = _hook(os.path.join(d, "a.py"), d)
                samples.append(time.perf_counter() - t0)
                self.assertEqual(code, 0)
            best = min(samples)
            self.assertLess(best, NOOP_BUDGET_SECS,
                            f"no-op hook path took {best * 1000:.0f}ms (budget "
                            f"{NOOP_BUDGET_SECS * 1000:.0f}ms); samples={samples}")

    def test_the_hook_path_imports_no_engine_govern_or_config(self):
        """SI.2's contract, inherited: the hook pays a plain-JSON import cost, not a framework boot.
        Asserted in a FRESH interpreter (this process has everything imported already)."""
        env = dict(os.environ)
        env["PYTHONPATH"] = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
        probe = (
            "import sys; import mokata.gate_hook;"
            "bad=[m for m in sys.modules if m.startswith(('mokata.engine','mokata.govern',"
            "'mokata.memory','mokata.knowledge')) or m in ('mokata.config','mokata.manifest',"
            "'mokata.router','mokata.detect')];"
            "print(','.join(sorted(bad)))"
        )
        proc = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                              env=env, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "",
                         f"gate_hook dragged in a heavy import surface: {proc.stdout.strip()}")

    def test_gate_ids_are_pinned_to_their_owners(self):
        """The literals in `gate_hook` (kept as literals for the cheap import surface) must stay
        equal to the constants they mirror — this is the pin that catches a drift."""
        from mokata.brainstorm import APPROACH_STATE_KEY
        from mokata.engine.spec_gate import SPEC_PERSISTED_GATE_ID, SPEC_STATE_KEY
        from mokata.govern.tdd import GATE_ID
        self.assertEqual(G.GATE_TDD, GATE_ID)
        self.assertEqual(G.GATE_SPEC, SPEC_PERSISTED_GATE_ID)
        self.assertEqual(G.APPROACH_PREFIX, APPROACH_STATE_KEY + "__")
        self.assertEqual(G.SPEC_PREFIX, SPEC_STATE_KEY + "__")
        self.assertEqual(G.BLOCK_EXIT, hook_cli.BLOCK_EXIT)
        self.assertEqual(T.STATE_DIRNAME, STATE_DIRNAME)

    def test_find_mokata_root_agrees_with_config(self):
        """The cheap root-finder must agree with `config.find_project_root` on initialized repos —
        including from a SUBDIRECTORY (the hook's cwd is wherever the tool call happened)."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            deep = os.path.join(d, "src", "pkg", "sub")
            os.makedirs(deep, exist_ok=True)
            for start in (d, deep):
                self.assertEqual(os.path.realpath(G.find_mokata_root(start)),
                                 os.path.realpath(find_project_root(start)))


# ======================================================================================
# (g) registration round-trip
# ======================================================================================

class TestRegistration(unittest.TestCase):

    def _settings(self, d):
        with open(os.path.join(d, ".claude", "settings.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def _mokata_pretool(self, settings):
        from mokata.harness_setup import _is_mokata_hook
        return [e for e in settings["hooks"]["PreToolUse"] if _is_mokata_hook(e)]

    def _commands(self, entries):
        return sorted(h["command"] for e in entries for h in e["hooks"])

    def test_setup_wires_both_pretooluse_hooks(self):
        from mokata.harness_setup import setup_harness
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            setup_harness("claude", root=d, scope="project", home=d, assume_yes=True)
            entries = self._mokata_pretool(self._settings(d))
            self.assertEqual(len(entries), 2)
            cmds = self._commands(entries)
            self.assertTrue(any("gate-guard" in c for c in cmds), cmds)
            self.assertTrue(any("secret-guard" in c for c in cmds), cmds)
            # each carries its OWN matcher; the gate hook does NOT match Bash (it decides on a path)
            gate_entry = [e for e in entries
                          if any("gate-guard" in h["command"] for h in e["hooks"])][0]
            self.assertIn("Write", gate_entry["matcher"])
            self.assertNotIn("Bash", gate_entry["matcher"])

    def test_re_setup_refreshes_and_never_duplicates(self):
        from mokata.harness_setup import setup_harness
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            setup_harness("claude", root=d, scope="project", home=d, assume_yes=True)
            first = self._commands(self._mokata_pretool(self._settings(d)))
            setup_harness("claude", root=d, scope="project", home=d, assume_yes=True)
            second = self._mokata_pretool(self._settings(d))
            self.assertEqual(len(second), 2, "re-setup duplicated the hook entries")
            self.assertEqual(self._commands(second), first, "re-setup did not converge")

    def test_re_setup_refreshes_a_stale_mokata_entry(self):
        """The skills-sync pattern: a stale mokata-wired command is REPLACED, not kept alongside."""
        from mokata.harness_setup import setup_harness
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            os.makedirs(os.path.join(d, ".claude"), exist_ok=True)
            with open(os.path.join(d, ".claude", "settings.json"), "w", encoding="utf-8") as fh:
                json.dump({"hooks": {"PreToolUse": [
                    {"matcher": "Write", "hooks": [
                        {"type": "command", "command": "/old/path/mokata-hook secret-guard"}]},
                    {"matcher": "Write", "hooks": [
                        {"type": "command", "command": "keepme.sh"}]},
                ]}}, fh)
            setup_harness("claude", root=d, scope="project", home=d, assume_yes=True)
            settings = self._settings(d)
            all_cmds = [h["command"] for e in settings["hooks"]["PreToolUse"] for h in e["hooks"]]
            self.assertNotIn("/old/path/mokata-hook secret-guard", all_cmds)  # stale one is gone
            self.assertIn("keepme.sh", all_cmds)                              # the user's survives
            self.assertEqual(len(self._mokata_pretool(settings)), 2)

    def test_unsetup_removes_both_and_leaves_user_entries(self):
        from mokata.harness_setup import setup_harness, unsetup_harness
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            os.makedirs(os.path.join(d, ".claude"), exist_ok=True)
            with open(os.path.join(d, ".claude", "settings.json"), "w", encoding="utf-8") as fh:
                json.dump({"hooks": {"PreToolUse": [
                    {"matcher": "Write", "hooks": [
                        {"type": "command", "command": "keepme.sh"}]}]}}, fh)
            setup_harness("claude", root=d, scope="project", home=d, assume_yes=True)
            self.assertEqual(len(self._mokata_pretool(self._settings(d))), 2)

            unsetup_harness("claude", root=d, scope="project", home=d, assume_yes=True)
            settings = self._settings(d)
            self.assertEqual(self._mokata_pretool(settings), [])         # both gone
            remaining = [h["command"] for e in settings["hooks"]["PreToolUse"] for h in e["hooks"]]
            self.assertEqual(remaining, ["keepme.sh"])                   # the user's untouched

    def test_the_plugin_hooks_json_carries_the_gate_hook(self):
        """The plugin path (hooks.json) must wire the same two PreToolUse hooks as `mokata setup`,
        or the plugin install silently has no run-state gate."""
        from mokata import package_data_root
        with open(os.path.join(str(package_data_root()), "hooks", "hooks.json"),
                  encoding="utf-8") as fh:
            data = json.load(fh)
        cmds = [h["command"] for e in data["hooks"]["PreToolUse"] for h in e["hooks"]]
        self.assertTrue(any("gate-guard" in c for c in cmds), cmds)
        self.assertTrue(any("secret-guard" in c for c in cmds), cmds)

    def test_gate_guard_is_a_dispatchable_subcommand(self):
        self.assertIn("gate-guard", hook_cli._SUBCOMMANDS)
        self.assertEqual(hook_cli.main(["gate-guard", "--path", "", "--cwd", "."]), 0)


if __name__ == "__main__":
    unittest.main()
