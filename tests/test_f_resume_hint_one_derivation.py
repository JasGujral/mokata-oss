"""0.0.28 F `HINTS-UNIFY-RESUME` — the SessionStart line and `mokata suggest` are ONE derivation.

What must be true for the user:
  * The briefing's next-step line is the PRIMARY hint of `hints.derive` for this session — its
    command, for its run — or nothing. Never a different command: on every rung of the ladder the
    line's command is the command `mokata suggest` gives.
  * `unknown` is said (with `mokata doctor`), never silence. Only a fixed, pinned set is silent:
    `init` (the briefing offers setup), `no-run`, and `status: none` (the run is finished).
  * Round 2: the O12b refresher stores the answer keyed on what it was DERIVED FROM — the files
    `derive` reported reading (`basis.inputs`), never the session that stored it. A session's own
    records enter only as inputs (its path names it), so a new session is served a stored answer
    exactly when what it would read is unchanged. A miss derives in the hook (measured: it fits the
    session-start ceilings). There is no placeholder line — the line is always the derivation's.
  * Changing any input re-derives; a file it never read cannot move it; there is no hand-typed
    skip list.
  * A cached answer is served as current only while `now < valid_until` (the 15-minute liveness
    window and the review's age bound are computed where each rule decides).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import tempfile
import time
import unittest
from contextlib import ExitStack, redirect_stdout
from types import SimpleNamespace
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from test_p14_next_step_hints import RUN, SID, T1, T2, World
from mokata import approval
from mokata import bootstrap as B
from mokata import briefing_cache as BC
from mokata import hints as H
from mokata.config import Surface
from mokata.govern.resume import PipelineCheckpoint
from mokata.init import init_repo
from mokata.tdd_state import state_dir


def _plain(tc, init=True):
    d = os.path.realpath(tempfile.mkdtemp(prefix="f-resume-"))
    tc.addCleanup(__import__("shutil").rmtree, d, True)
    subprocess.run(["git", "-C", d, "init", "-q"], check=True, stdin=subprocess.DEVNULL,
                   capture_output=True)
    if init:
        init_repo(root=d, profile="standard", assume_yes=True, out=lambda *_: None)
    return d


def _suggest(d, run):
    from mokata.cli_commands.pipeline import cmd_suggest
    buf = io.StringIO()
    ns = argparse.Namespace(path=d, run=run, intent=[], fresh=False, spec=False,
                            failing_test=False, implementation=False, diff=False, bug=False,
                            stacktrace=False, perf=False)
    with redirect_stdout(buf):
        cmd_suggest(ns)
    return buf.getvalue()


def _cmd(line):
    """The command a briefing line carries (its last back-quoted span)."""
    spans = re.findall(r"`([^`]+)`", line or "")
    return spans[-1] if spans else None


# ------------------------------------------------------------------ one row per rung, real state
def _rungs(tc):
    """{rung: (repo, [patches])} — every rule the ladder knows, built from recorded state."""
    now = time.time()
    rows = {}
    rows[H.R_INIT] = (_plain(tc, init=False), [])
    rows[H.R_NO_RUN] = (_plain(tc), [])
    w = World(tc, run="R-a").approve()
    PipelineCheckpoint(w.store, "R-b").ensure_registered()
    rows[H.R_AMBIGUOUS] = (w.d, [mock.patch("mokata.run_resolver.live_runs",
                                            return_value=["R-a", "R-b"])])
    w = World(tc)
    approval.propose(w.d, tool="memory_write", args={"x": 1}, run_id=RUN, summary="s")
    rows[H.R_APPROVE] = (w.d, [])
    w = World(tc).approve().spec("AC-1").test_run(False, [T1], at=now - 9, ac_map={"AC-1": [T1]})
    w.store.write(f"spec_amend__{RUN}", {"status": "open", "from_version": 1, "to_version": 2})
    rows[H.R_AMEND_OPEN] = (w.d, [])
    w = World(tc).approve().live()
    w.gate("blocked", gate="red-before-green")
    rows[H.R_GATE] = (w.d, [])
    w = World(tc).approve().ended()
    w.gate("blocked")
    rows[H.R_GATE_ENDED] = (w.d, [])
    rows[H.C_APPROACH] = (World(tc).d, [])
    rows[H.C_SPEC] = (World(tc).approve().d, [])
    rows[H.C_UNMAPPED] = (World(tc).approve().spec("AC-1", "AC-2").plan({"AC-1": [T1]}).d, [])
    w = World(tc).approve().spec("AC-1").plan({"AC-1": [T1]})
    w.store.write(f"spec_amend__{RUN}", {"status": "closed", "from_version": 1, "to_version": 2,
                                         "red_owed": [T2]})
    rows[H.C_RED_OWED] = (w.d, [])
    rows[H.C_NEVER_RUN] = (World(tc).approve().spec("AC-1").plan({"AC-1": [T1]}).d, [])
    w = World(tc).approve().spec("AC-1").test_run(False, [T1], at=now - 100,
                                                 ac_map={"AC-1": [T1]})
    w.write_after(now - 10)
    rows[H.C_RERUN] = (w.d, [])
    w = World(tc).approve().spec("AC-1").test_run(True, [T1], at=now - 1000,
                                                 ac_map={"AC-1": [T1]})
    for i in range(6):
        w.write_after(now - 500 + i, session="other", run="R-other")
    rows[H.C_RERUN_UNSURE] = (w.d, [mock.patch.object(H, "TIMELINE_KEEP", 5)])
    rows[H.C_FAILING] = (World(tc).approve().spec("AC-1").test_run(
        False, [T1], at=now - 50, ac_map={"AC-1": [T1]}).d, [])
    rows[H.C_REVIEW] = (World(tc).approve().spec("AC-1").test_run(
        True, [T1], at=now - 50, ac_map={"AC-1": [T1]}).d, [])
    w = World(tc).approve().spec("AC-1").test_run(True, [T1], at=now - 50, ac_map={"AC-1": [T1]})
    w.verdict(False, ts=now - 10)
    rows[H.C_REVIEW_FAILED] = (w.d, [])
    rows[H.C_SHIP] = (World(tc).green_and_reviewed().d, [])
    rows[H.C_FINISHED] = (World(tc).green_and_reviewed().finish().d, [])
    for rule in (H.R_GRAPH, H.R_GRAPH_MISSING):
        w = World(tc)
        man = os.path.join(w.d, ".mokata", "manifest.json")
        with open(man, encoding="utf-8") as fh:
            data = json.load(fh)
        data.setdefault("capabilities", {}).setdefault("code_graph", {})["fallback"] = [
            "code-review-graph", "ast"]
        with open(man, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        state = ("ok", "/bin/crg", "") if rule == H.R_GRAPH else ("missing", "", "not installed")
        rows[rule] = (w.d, [mock.patch.object(H, "_crg_state", return_value=state)])
    w = World(tc).approve().spec("AC-1")
    with open(os.path.join(state_dir(w.d), f"test_runs__{RUN}.json"), "w",
              encoding="utf-8") as fh:
        fh.write("{torn")
    rows["unknown"] = (w.d, [])
    return rows


#: The rule ids `hints.derive` can answer with — DERIVED from the module's rule-id namespace (the
#: `R_*` / `C_*` constants the IDE, the CLI and the tests switch on), never from `ADVICE`, so a rule
#: that `ADVICE` forgot still gets a row.
RULE_IDS = frozenset(v for k, v in vars(H).items()
                     if k.startswith(("R_", "C_")) and isinstance(v, str))

#: Rungs where the hook's subject (a Claude session) and `mokata suggest`'s (a terminal: no
#: session) are different subjects, so the two answers differ BY DESIGN — pinned, each with what
#: `suggest` says instead. The live session's own gate block is "fix the block" in that session;
#: from a terminal no session is live, so the same block reads as "in a session that ended".
SUGGEST_HAS_NO_SESSION = {H.R_GATE: H.R_GATE_ENDED}

NEW = "S-new"            # a session that never populated the store


def _start(root, sid, source="startup"):
    """What the SessionStart hook does BEFORE the briefing: open this session's index segment."""
    from mokata import session_index as SI
    SI.on_start(root, sid, source, root, proc=None)


def _expected(root, sid, now):
    return B.resume_line(H.derive(root, session=sid, now=now, rules=H.PIPELINE_ONLY))


AGE = re.compile(r" \(derived [^()]* ago\)$")


def _unaged(line):
    """A hit says how old its text is (its relative times were rendered then); the derivation's
    line is the rest."""
    return AGE.sub("", line) if isinstance(line, str) else line


def _hook_read(root, sid, now):
    """(the hook's line for `sid` without its age, how many times it derived) — 0 is a hit on the
    stored answer, and only a hit carries an age."""
    with mock.patch.object(H, "derive", wraps=H.derive) as spy:
        line = B._cached_resume_hint(SimpleNamespace(root=root), sid, now=now)
    if isinstance(line, str):
        assert bool(AGE.search(line)) == (spy.call_count == 0), line
    return _unaged(line), spy.call_count


class AllRungs(unittest.TestCase):

    def test_the_rung_table_is_the_ladder(self):
        """The rows are the rule ids `derive` can answer with (+ `unknown`): a new rule with no row
        reds here, and so does a row for a rule that no longer exists. Every rule id has its
        ADVICE (the conflict property reasons over it) — a rule dropped from ADVICE reds here."""
        self.assertEqual(RULE_IDS | {"unknown"}, set(_rungs(self)))
        self.assertEqual(RULE_IDS, set(H.ADVICE))

    def test_the_briefing_line_is_the_primary_hint_on_every_rung(self):
        """Every row: the rung is REACHED (its rule is in the answer), the hook's line — for the
        session that stored it AND for a new session — is the derivation's line, and its outcome
        is pinned: said (unknown), silent (the pinned set), or `mokata suggest`'s command (or the
        pinned no-session answer). No row passes by skipping."""
        seen = {}
        for rung, (d, patches) in _rungs(self).items():
            with self.subTest(rung=rung), ExitStack() as st:
                for p in patches:
                    st.enter_context(p)
                now = time.time()
                full = H.derive(d, session=SID, now=now)
                prim = H.primary(full)
                if rung == "unknown":
                    self.assertEqual(H.STATUS_UNKNOWN, full["status"])
                else:
                    self.assertIn(rung, [h["rule"] for h in full["hints"]], full)
                inline = B.resume_line(full)
                self.assertEqual(_expected(d, SID, now), inline)
                if rung == H.R_INIT:               # the hook never briefs an uninitialised repo
                    lines = {SID: inline}
                else:
                    B.refresh_resume_hint(d, SID, now=now)
                    _start(d, NEW)
                    lines = {SID: _hook_read(d, SID, now)[0], NEW: _hook_read(d, NEW, now)[0]}
                    for sid, line in lines.items():
                        self.assertEqual(_expected(d, sid, now), line, (rung, sid))
                line = lines[SID]
                if full["status"] == H.STATUS_UNKNOWN:
                    self.assertTrue(line.startswith("mokata: next step unknown — "), line)
                    self.assertIn("`mokata doctor`", line)
                    seen[rung] = "said"
                    continue
                if full["status"] in B.RESUME_SUPPRESSED_STATUS or \
                        prim["rule"] in B.RESUME_SUPPRESSED_RULES:
                    self.assertIsNone(line, rung)
                    seen[rung] = "silent"
                    continue
                self.assertNotIn(prim["rule"], H._SIDE_RULES, (rung, full["hints"]))
                self.assertEqual(prim["command"], _cmd(line), (rung, line))
                run = full["subject"]["run"]
                if run:
                    self.assertIn(f"for run {run}", line)
                by_cli = H.primary(H.derive(d, run=run, now=now))
                if prim["rule"] in SUGGEST_HAS_NO_SESSION:
                    self.assertEqual(SUGGEST_HAS_NO_SESSION[prim["rule"]], by_cli["rule"], rung)
                    seen[rung] = "pinned"
                    continue
                self.assertEqual(prim["rule"], by_cli["rule"], rung)
                self.assertIn(f"→ {prim['command']}", _suggest(d, run))
                seen[rung] = "suggest"
        self.assertEqual(set(_rungs(self)), set(seen), "a row ended without an outcome")


class FreshSession(unittest.TestCase):
    """H1 (round 2): the stored answer is keyed on what it was derived from — never on the session
    that stored it. A new session is served it only when what IT would read is unchanged; a miss
    derives in the hook. There is no placeholder line."""

    def test_an_answer_no_session_shaped_is_served_to_a_new_session(self):
        w = World(self, run="R-a").approve()
        PipelineCheckpoint(w.store, "R-b").ensure_registered()
        now = time.time()
        with mock.patch("mokata.run_resolver.live_runs", return_value=["R-a", "R-b"]):
            stored = B.refresh_resume_hint(w.d, "S-a", now=now)
            self.assertEqual(H.R_AMBIGUOUS, H.primary(stored)["rule"])
            _start(w.d, NEW)
            line, derived = _hook_read(w.d, NEW, now)
            self.assertEqual(_expected(w.d, NEW, now), line)
        self.assertEqual(0, derived, "an answer the new session would read identically was "
                                     "re-derived: the key carries the session")

    def test_another_sessions_run_answer_is_never_served(self):
        """A's session ended (its answer: the block is in a session that ended → resume). A new,
        live session B must get B's answer, not A's — B reads its own session record."""
        w = World(self).approve().ended("S-a")
        w.gate("blocked", session="S-a")
        now = time.time()
        stored = B.refresh_resume_hint(w.d, "S-a", now=now)
        _start(w.d, NEW)
        want = H.primary(H.derive(w.d, session=NEW, now=now, rules=H.PIPELINE_ONLY))
        self.assertNotEqual(H.primary(stored)["rule"], want["rule"], "fixture: no divergence")
        line, derived = _hook_read(w.d, NEW, now)
        self.assertEqual(_expected(w.d, NEW, now), line)
        self.assertEqual(1, derived)

    def test_nothing_stored_derives_in_the_hook_and_never_promises_a_later_briefing(self):
        w = World(self).approve().spec("AC-1").plan({"AC-1": [T1]})
        _start(w.d, NEW)
        now = time.time()
        line, derived = _hook_read(w.d, NEW, now)
        self.assertEqual(_expected(w.d, NEW, now), line)
        self.assertIn(f"Next step for run {RUN}", line)
        self.assertEqual(1, derived)
        self.assertFalse(hasattr(B, "_RESUME_PENDING"))
        with open(B.__file__, encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotIn("being derived", src)
        self.assertNotIn("being re-derived", src)

    def test_a_repo_with_no_run_says_nothing(self):
        d = _plain(self)
        now = time.time()
        B.refresh_resume_hint(d, "S-a", now=now)
        for sid in ("S-a", NEW):
            _start(d, sid)
            self.assertIsNone(_hook_read(d, sid, now)[0], sid)

    def test_the_real_hook_startup_then_startup_then_resume_clear_compact(self):
        """The SessionStart hook as Claude Code runs it (a process per call), on one repo: A
        starts, A's answer is stored, then a NEW session B starts, then B resumes, clears and
        compacts. Every briefing carries exactly the line the derivation gives for that session."""
        w = World(self).approve().spec("AC-1").plan({"AC-1": [T1]})
        seq = (("S-a", "startup"), ("S-b", "startup"), ("S-b", "resume"), ("S-b", "clear"),
               ("S-b", "compact"))
        for i, (sid, source) in enumerate(seq):
            with self.subTest(sid=sid, source=source):
                ctx = _real_hook(self, w.d, sid, source)
                self.assertNotIn("being derived", ctx)
                got = [_unaged(ln) for ln in ctx.splitlines() if "next step" in ln.lower()]
                self.assertEqual([_expected(w.d, sid, time.time())], got)
                self.assertNotIn(" ago", got[0], "a relative time would race the process")
                self.assertIn(f"Next step for run {RUN}", got[0])
            if i == 0:
                B.refresh_resume_hint(w.d, "S-a")      # what A's refresher stores

    def test_the_real_hook_in_a_repo_with_no_run_says_nothing(self):
        d = _plain(self)
        for i, sid in enumerate(("S-a", "S-b")):
            ctx = _real_hook(self, d, sid, "startup")
            self.assertNotIn("being derived", ctx)
            self.assertEqual([], [ln for ln in ctx.splitlines() if "next step" in ln.lower()])
            if i == 0:
                B.refresh_resume_hint(d, "S-a")


def _real_hook(tc, d, sid, source):
    """One SessionStart hook process. The briefing refresher is held off (its lock is fresh) so
    the store is exactly what the test put there — nothing races the assertions."""
    import sys
    from mokata.quiet_spawn import hidden
    cache = BC.cache_dir(d)
    os.makedirs(cache, exist_ok=True)
    lock = os.path.join(cache, BC.LOCK_FILENAME)
    with open(lock, "w", encoding="utf-8") as fh:
        fh.write("held by the test")
    try:
        src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
        env = dict(os.environ, PYTHONPATH=src, CLAUDE_PROJECT_DIR=d)
        env.pop("CLAUDE_PLUGIN_ROOT", None)
        body = json.dumps({"session_id": sid, "cwd": d, "hook_event_name": "SessionStart",
                           "source": source})
        p = subprocess.run([sys.executable, "-m", "mokata_emit", "session-start"], input=body,
                           cwd=d, env=env, text=True, encoding="utf-8", capture_output=True,
                           timeout=600, **hidden())
    finally:
        os.remove(lock)
    tc.assertEqual(0, p.returncode, p.stderr[-2000:])
    return json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]


class Unknown(unittest.TestCase):

    def test_unknown_is_said_never_silence(self):
        d, _ = _rungs(self)["unknown"]
        for sid in ("S-a", NEW):                # nothing stored, then stored by another session
            _start(d, sid)
            line = B._cached_resume_hint(Surface.load(d), sid)
            self.assertIsNotNone(line)
            self.assertIn("next step unknown", line)
            self.assertIn("the test record", line)
            self.assertIn("`mokata doctor`", line)
            B.refresh_resume_hint(d, "S-a")


class Suppressed(unittest.TestCase):

    def test_the_silent_set_is_pinned(self):
        self.assertEqual(frozenset({H.R_INIT, H.R_NO_RUN}), B.RESUME_SUPPRESSED_RULES)
        self.assertEqual(frozenset({H.STATUS_NONE}), B.RESUME_SUPPRESSED_STATUS)
        self.assertEqual({H.STATUS_HINTS: "primary", H.STATUS_NONE: "silent",
                          H.STATUS_UNKNOWN: "said"}, dict(B.RESUME_BY_STATUS))

    def test_the_old_second_derivation_is_gone(self):
        for name in ("build_resume_hint", "_resume_run_line", "_resume_brainstorm_line",
                     "_RESUME_GLYPH", "_RESUME_KEY_SKIP", "_RESUME_PENDING"):
            self.assertFalse(hasattr(B, name), name)


class KeyIsDerived(unittest.TestCase):

    def _world(self):
        now = time.time()
        w = World(self).approve().spec("AC-1").test_run(True, [T1], at=now - 50,
                                                       ac_map={"AC-1": [T1]})
        w.write_after(now - 100)
        w.live()
        return w

    def test_the_inputs_are_what_derive_read(self):
        w = self._world()
        ans = H.derive(w.d, session=SID, rules=H.PIPELINE_ONLY)
        inputs = set(ans["basis"]["inputs"])
        sd = state_dir(w.d)
        for must in (os.path.join(sd, f"test_runs__{RUN}.json"),
                     os.path.join(sd, f"emitted_spec__{RUN}.json"),
                     H.timeline_path(w.d), sd):
            self.assertIn(os.path.realpath(must), {os.path.realpath(p) for p in inputs}, must)
        self.assertFalse([p for p in inputs if "__pycache__" in p], inputs)

    def test_changing_any_input_re_derives_and_nothing_else_does(self):
        """A hit derives nothing; a moved input is a miss, derived in the hook — and the line is
        the derivation's either way, for the storing session and for a new one."""
        w = self._world()
        # a state file a hook rewrites and derive never reads (the session map): present first
        anc = os.path.join(state_dir(w.d), "session_ancestry.json")
        with open(anc, "w", encoding="utf-8") as fh:
            fh.write("{}")
        now = time.time()
        inputs = B.refresh_resume_hint(w.d, SID, now=now)["basis"]["inputs"]
        self.assertTrue(inputs)
        self.assertEqual((_expected(w.d, SID, now), 0), _hook_read(w.d, SID, now))
        self.assertNotIn(os.path.realpath(anc), {os.path.realpath(p) for p in inputs})
        with open(anc, "a", encoding="utf-8") as fh:
            fh.write(" ")
        self.assertEqual(0, _hook_read(w.d, SID, now)[1], "a file derive never read moved the key")
        _start(w.d, NEW)
        self.assertEqual(_expected(w.d, NEW, now), _hook_read(w.d, NEW, now)[0])
        for p in inputs:
            with self.subTest(input=p):
                B.refresh_resume_hint(w.d, SID, now=now)
                self.assertEqual(0, _hook_read(w.d, SID, now)[1])
                # each input is moved, judged, then PUT BACK — so the next row's answer reads
                # what the first one did (a damaged input changes what derive goes on to read)
                if os.path.isdir(p):
                    extra = os.path.join(p, "zz-new-file.tmp")
                    with open(extra, "w", encoding="utf-8") as fh:
                        fh.write("x")
                    self.assertEqual((_expected(w.d, SID, now), 1), _hook_read(w.d, SID, now), p)
                    os.remove(extra)
                    continue
                existed = os.path.exists(p)
                if existed:
                    with open(p, "rb") as fh:
                        saved = fh.read()
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "a", encoding="utf-8") as fh:
                    fh.write("\n")
                self.assertEqual((_expected(w.d, SID, now), 1), _hook_read(w.d, SID, now), p)
                if existed:
                    with open(p, "wb") as fh:
                        fh.write(saved)
                else:
                    os.remove(p)


class ValidUntil(unittest.TestCase):

    def test_the_15_minute_liveness_window_flips_the_rung_and_ends_the_cache(self):
        from mokata import session_index as SI
        w = World(self).approve().live()
        w.gate("blocked", gate="red-before-green")
        hb = os.path.getmtime(SI.path_of(w.d, SID))
        now = hb + 60
        ans = B.refresh_resume_hint(w.d, SID, now=now)
        self.assertEqual(H.R_GATE, H.primary(ans)["rule"])
        self.assertAlmostEqual(hb + H.LIVE_SECS, ans["valid_until"], places=3)
        before, after = ans["valid_until"] - 1, ans["valid_until"] + 1
        # a hit: the answer as it was derived (its "N min ago" is then's — the line says its age)
        self.assertEqual((_expected(w.d, SID, now), 0), _hook_read(w.d, SID, before))
        self.assertEqual((_expected(w.d, SID, after), 1), _hook_read(w.d, SID, after))
        later = H.derive(w.d, session=SID, now=after)
        self.assertEqual(H.R_GATE_ENDED, H.primary(later)["rule"])
        self.assertEqual("mokata resume", _cmd(_hook_read(w.d, SID, after)[0]))

    def test_the_review_age_bound_flips_the_rung_and_ends_the_cache(self):
        now = time.time()
        w = World(self).approve().spec("AC-1").test_run(False, [T1], at=now - 300,
                                                       ac_map={"AC-1": [T1]})
        w.test_run(True, [T1], at=now - 200)
        vt = float(int(now - 100))
        w.verdict(True, ts=vt)
        ans = B.refresh_resume_hint(w.d, SID, now=now)
        self.assertEqual(H.C_SHIP, H.primary(ans)["rule"])
        self.assertAlmostEqual(vt + 24 * 3600.0, ans["valid_until"], places=3)
        after = ans["valid_until"] + 1
        line, derived = _hook_read(w.d, SID, after)
        self.assertEqual(1, derived)
        self.assertEqual(_expected(w.d, SID, after), line)
        later = H.derive(w.d, session=SID, now=after)
        self.assertEqual(H.C_REVIEW, H.primary(later)["rule"])

    def test_no_time_boundary_means_none_and_the_key_alone_decides(self):
        w = World(self).approve()
        ans = H.derive(w.d, session=SID)
        self.assertIsNone(ans["valid_until"])


class TheHookPassesItsSession(unittest.TestCase):

    def test_session_start_hands_its_session_to_the_briefing_and_the_refresher(self):
        from mokata import hook_cli
        w = World(self).approve()
        seen = {}
        real = B.build_bootstrap

        def spy(*a, **k):
            seen["bootstrap"] = k.get("session")
            return real(*a, **k)

        def refresh(root, **k):
            seen["refresh"] = k.get("session")
            return True
        payload = json.dumps({"session_id": "S-hook", "cwd": w.d, "source": "startup"})
        with mock.patch.object(hook_cli, "FRONT_DOOR", True), \
                mock.patch.object(B, "build_bootstrap", spy), \
                mock.patch.object(BC, "start_refresh", refresh), \
                mock.patch("sys.stdin", io.StringIO(payload)), \
                redirect_stdout(io.StringIO()):
            hook_cli.session_start_main([])
        self.assertEqual({"bootstrap": "S-hook", "refresh": "S-hook"}, seen)


if __name__ == "__main__":
    unittest.main()
