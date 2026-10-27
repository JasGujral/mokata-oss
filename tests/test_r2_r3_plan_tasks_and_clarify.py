"""R2 + R3 (0.0.26 Lane R) — the task plan, its approval hash, and the capped clarify.

Business rules — what must be true for the user:
  R2  * A spec carries its TASK PLAN (`tasks[]`): every acceptance criterion is in a task, every
        task names its criteria and the globs it may write. A spec without one is refused.
      * The plan is hashed (SHA-256) inside the human-gated emit. If the plan on record no longer
        matches the hash the human approved, every later stage refuses — develop writes, entering a
        stage, ship — until a human re-approves it. A spec persisted before 0.0.26 (no plan, no
        hash) is refused loudly, never silently accepted.
      * Develop works task by task: a write outside the ACTIVE task's scope is blocked by the
        existing `spec-scope` gate; finishing or switching the task moves the boundary.
  R3  * Clarifying questions are recorded in the spec with their recommended answer, and the cap
        HOLDS at five: a sixth is refused. After the cap, the spec proceeds on stated assumptions,
        which it records.

Exit criterion 12 ("plan-hash mismatch refuses") is `test_R2_a_plan_changed_after_approval_refuses`.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import _support  # noqa: F401
from _support import mcp_commit

from mokata import gate_hook as G                                  # noqa: E402
from mokata import plan_tasks as P                                 # noqa: E402
from mokata import session, tdd_state as T                         # noqa: E402
from mokata.brainstorm import Approach, BrainstormSession          # noqa: E402
from mokata.brainstorm_impact import FITS, DesignFitVerdict        # noqa: E402
from mokata.config import Surface                                  # noqa: E402
from mokata.engine.emit import spec_for_emit, spec_from_payload    # noqa: E402
from mokata.govern.ledger import AuditLedger                       # noqa: E402
from mokata.init import init_repo                                  # noqa: E402
from mokata.mcp import tools_read as TR                            # noqa: E402
from mokata.mcp import tools_write as TW                           # noqa: E402
from mokata.progress_events import record_review_verdict, ship_review_gate  # noqa: E402
from mokata.session_save import save_session                       # noqa: E402
from mokata.state import StateStore                                # noqa: E402

RUN = "run-r2r3"
ACS = [("AC1", "GET /items/<id> returns one item"),
       ("AC2", "the item serializer renders dates as ISO-8601")]
TESTS = [("test_get_item", ["AC1"]), ("test_serializer_dates", ["AC2"])]
SCOPE = {"authorized": ["src/api/*"], "deferred": []}
TASKS = [
    {"id": "T1", "title": "items endpoint", "ac_ids": ["AC1"], "scope": ["src/api/items.py"]},
    {"id": "T2", "title": "date serializer", "ac_ids": ["AC2"],
     "scope": ["src/api/serializers.py"], "depends_on": ["T1"]},
]
CLARIFY = [{"id": "Q1", "question": "Which date format?", "recommended": "ISO-8601",
            "answer": "ISO-8601", "category": "domain & data model"}]


def _payload(**kw):
    p = {"title": "items api", "criteria": [{"id": i, "text": t} for i, t in ACS],
         "tests": [{"name": n, "ac_ids": a} for n, a in TESTS], "scope": SCOPE,
         "tasks": TASKS, "clarifications": CLARIFY, "assumptions": []}
    p.update(kw)
    return p


class _Repo:
    def __init__(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = self.dir.name
        init_repo(root=self.path, profile="standard", assume_yes=True, out=lambda *_a: None)
        self._env = mock.patch.dict(os.environ, {session.SESSION_ID_ENV: RUN})
        self._env.start()
        session.reset_for_test()
        self.surface = Surface.load(self.path)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self._env.stop()
        session.reset_for_test()
        self.dir.cleanup()

    @property
    def raw(self):
        return StateStore(T.state_dir(self.path))

    def spec(self):
        return self.raw.read(G.SPEC_PREFIX + RUN)

    def tamper(self, mutate):
        """Change the persisted spec ON DISK, as an edit outside the gate would."""
        data = self.spec()
        mutate(data)
        self.raw.write(G.SPEC_PREFIX + RUN, data)

    def ledger_rows(self, kind):
        led = AuditLedger.from_mokata_dir(os.path.join(self.path, ".mokata"))
        return [e for e in led.entries() if e.get("kind") == kind]

    def cli(self, *argv):
        from mokata.cli import main
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = main([*argv, "--path", self.path])
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue() + err.getvalue()


def _approve(repo):
    s = BrainstormSession("items api")
    s.propose_approaches([
        Approach(name="rest", summary="plain REST", pros=["simple"], cons=["chatty"]),
        Approach(name="rpc", summary="rpc", pros=["compact"], cons=["opaque"]),
    ])
    s.assess_impacts(layer=None, memory_items=[])
    for a in s.approaches:
        s.record_design_fit(a.name, DesignFitVerdict(a.name, FITS, [], rationale="fits"))
    s.assess_prior_art(layer=None)
    s.approve("jas", "rest")
    save_session(repo.surface, brainstorm=s.to_dict())
    save_session(repo.surface, passed=["brainstorm", "analysis", "strawman", "pre_mortem",
                                       "probes"])


def _emit(repo, **kw):
    p = _payload(**kw)
    return mcp_commit(TW.spec_emit, path=repo.path, title=p["title"], approach="rest",
                      criteria=p["criteria"], tests=p["tests"], scope=p["scope"],
                      tasks=p["tasks"], clarifications=p["clarifications"],
                      assumptions=p["assumptions"])


def _in_develop(repo):
    _approve(repo)
    res = _emit(repo)
    assert res.get("committed"), res
    T.record(repo.surface.state, RUN, red=["test_get_item", "test_serializer_dates"])
    return res


# ======================================================================================
# R2 — the plan is part of the spec
# ======================================================================================

class TestR2ThePlanIsRequired(unittest.TestCase):

    def test_R2_a_spec_without_tasks_is_refused(self):
        p = _payload()
        del p["tasks"]
        with self.assertRaises(ValueError) as cm:
            spec_for_emit(p)
        self.assertIn("task plan", str(cm.exception))
        with _Repo() as repo:
            _approve(repo)
            res = TW.spec_emit(path=repo.path, title=p["title"], approach="rest",
                               criteria=p["criteria"], tests=p["tests"], scope=p["scope"])
            self.assertEqual(res["status"], "error")
            self.assertIn("task plan", res["reason"])
            self.assertNotIn("proposal_id", res, "refused before any human is asked")
            self.assertIsNone(repo.spec())

    def test_R2_every_criterion_needs_a_task(self):
        with self.assertRaises(ValueError) as cm:
            spec_from_payload(_payload(tasks=[TASKS[0]]))
        self.assertIn("AC2", str(cm.exception))

    def test_R2_a_task_needs_criteria_and_a_scope(self):
        for bad, word in ((dict(TASKS[0], ac_ids=[]), "acceptance criterion"),
                          (dict(TASKS[0], scope=[]), "scope"),
                          (dict(TASKS[0], ac_ids=["AC9"]), "unknown")):
            with self.subTest(word=word), self.assertRaises(ValueError) as cm:
                spec_from_payload(_payload(tasks=[bad, TASKS[1]]))
            self.assertIn(word, str(cm.exception))

    def test_R2_a_task_scope_is_a_subset_of_the_spec_scope(self):
        wide = dict(TASKS[0], scope=["src/db/*"])
        with self.assertRaises(ValueError) as cm:
            spec_from_payload(_payload(tasks=[wide, TASKS[1]]))
        self.assertIn("outside the spec's scope.authorized", str(cm.exception))

    def test_R2_task_dependencies_must_exist_and_not_cycle(self):
        cyc = [dict(TASKS[0], depends_on=["T2"]), TASKS[1]]
        with self.assertRaises(ValueError) as cm:
            spec_from_payload(_payload(tasks=cyc))
        self.assertIn("cycle", str(cm.exception))


class TestR2TheEmitSealsThePlan(unittest.TestCase):

    def test_R2_the_approved_spec_carries_the_sha256_of_its_plan(self):
        with _Repo() as repo:
            _approve(repo)
            res = _emit(repo)
            self.assertTrue(res["committed"], res)
            data = repo.spec()
            self.assertEqual([t["id"] for t in data["tasks"]], ["T1", "T2"])
            self.assertTrue(data["plan_hash"].startswith("sha256:"))
            self.assertEqual(data["plan_hash"], P.plan_hash(data))
            self.assertTrue(P.attest(data).ok)
            rows = repo.ledger_rows("plan_approved")
            self.assertEqual(rows[-1].get("plan_hash"), data["plan_hash"],
                             "the approved hash is on the audit ledger (the SHA-tied marker)")


# ======================================================================================
# R2 — every later stage attests the hash (exit criterion 12)
# ======================================================================================

def _widen_t1(data):
    data["tasks"][0]["scope"] = ["src/*"]


class TestR2PlanHashAttestation(unittest.TestCase):

    def test_R2_a_plan_changed_after_approval_refuses(self):
        """Exit 12 — the plan-hash mismatch REFUSES (develop writes)."""
        with _Repo() as repo:
            _in_develop(repo)
            ok = G.check_write(repo.path, "src/api/items.py", content="x = 1")
            self.assertTrue(ok.allowed, ok.reason)
            repo.tamper(_widen_t1)
            out = G.check_write(repo.path, "src/api/items.py", content="x = 1")
            self.assertFalse(out.allowed)
            self.assertEqual(out.exit_code, G.BLOCK_EXIT)
            self.assertEqual(out.gate, G.GATE_SCOPE)
            self.assertIn("CHANGED after approval", out.reason)

    def test_R2_the_mismatch_is_not_waived_by_a_scope_override(self):
        with _Repo() as repo:
            _in_develop(repo)
            repo.tamper(_widen_t1)
            repo.raw.write(G.override_key(RUN), {"scopes": [G.GATE_SCOPE]})
            out = G.check_write(repo.path, "src/api/items.py", content="x = 1")
            self.assertFalse(out.allowed, "re-approval is the way on, never an override")

    def test_R2_re_approving_the_plan_lifts_the_refusal(self):
        with _Repo() as repo:
            _in_develop(repo)
            repo.tamper(_widen_t1)
            p = _payload()
            res = mcp_commit(TW.spec_amend, path=repo.path, title=p["title"], approach="rest",
                             criteria=p["criteria"], tests=p["tests"], scope=p["scope"],
                             tasks=p["tasks"], clarifications=p["clarifications"],
                             assumptions=[], reason="re-approve the plan")
            self.assertTrue(res.get("committed"), res)
            out = G.check_write(repo.path, "src/api/items.py", content="x = 1")
            self.assertTrue(out.allowed, out.reason)

    def test_R2_a_spec_approved_before_the_plan_existed_is_refused_loudly(self):
        with _Repo() as repo:
            _in_develop(repo)
            repo.tamper(lambda d: (d.pop("tasks"), d.pop("plan_hash")))
            out = G.check_write(repo.path, "src/api/items.py", content="x = 1")
            self.assertFalse(out.allowed)
            self.assertIn("predates", out.reason)
            self.assertIn("re-emit", out.reason)

    def test_R2_entering_a_stage_attests_the_plan(self):
        with _Repo() as repo:
            _in_develop(repo)
            ok = TR.progress_mark(path=repo.path, stage="develop", run=RUN)
            self.assertTrue(ok["recorded"], ok)
            from mokata.progress_events import STAGE_ENTER, ProgressLog
            ev = [e for e in ProgressLog.from_surface(repo.surface).read_events()
                  if e["type"] == STAGE_ENTER][-1]
            self.assertEqual(ev["data"].get("plan_hash"), repo.spec()["plan_hash"])
            repo.tamper(_widen_t1)
            refused = TR.progress_mark(path=repo.path, stage="review", run=RUN)
            self.assertFalse(refused["recorded"])
            self.assertEqual(refused["status"], "refused")
            self.assertIn("CHANGED after approval", refused["message"])
            code, out = repo.cli("progress", "mark", "ship", "--run", RUN)
            self.assertEqual(code, 3, out)

    def test_R2_ship_refuses_a_changed_plan_and_a_review_of_another_plan(self):
        with _Repo() as repo:
            _in_develop(repo)
            record_review_verdict(repo.surface, passed=True, independent=False, run_id=RUN)
            self.assertFalse(ship_review_gate(repo.surface, run_id=RUN).blocks)
            repo.tamper(_widen_t1)
            gate = ship_review_gate(repo.surface, run_id=RUN)
            self.assertTrue(gate.blocks)
            self.assertIn("CHANGED after approval", gate.message)
            # the plan is re-approved as a DIFFERENT plan: the old review no longer counts
            repo.tamper(lambda d: d.update(P.seal(d)))
            gate = ship_review_gate(repo.surface, run_id=RUN)
            self.assertTrue(gate.blocks)
            self.assertIn("not the approved plan", gate.message)


# ======================================================================================
# R2 — develop works task by task
# ======================================================================================

class TestR2TaskByTask(unittest.TestCase):

    def test_R2_a_write_outside_the_active_task_is_blocked_and_names_its_owner(self):
        with _Repo() as repo:
            _in_develop(repo)
            out = G.check_write(repo.path, "src/api/serializers.py", content="x = 1")
            self.assertFalse(out.allowed)
            self.assertEqual(out.gate, G.GATE_SCOPE)
            self.assertIn("ACTIVE task T1", out.reason)
            self.assertIn("belongs to T2", out.reason)

    def test_R2_finishing_a_task_moves_the_boundary(self):
        with _Repo() as repo:
            _in_develop(repo)
            code, out = repo.cli("spec", "task", "done", "T1")
            self.assertEqual(code, 0, out)
            self.assertIn("active task: T2", out)
            self.assertTrue(G.check_write(repo.path, "src/api/serializers.py").allowed)
            self.assertFalse(G.check_write(repo.path, "src/api/items.py").allowed)
            code, out = repo.cli("spec", "task", "done", "T2")
            self.assertEqual(code, 0, out)
            # every task done: the plan's surface stays writable (review fixes), nothing else
            self.assertTrue(G.check_write(repo.path, "src/api/items.py").allowed)
            self.assertFalse(G.check_write(repo.path, "src/api/other.py").allowed)

    def test_R2_a_task_cannot_start_before_its_dependencies(self):
        with _Repo() as repo:
            _in_develop(repo)
            code, out = repo.cli("spec", "task", "start", "T2")
            self.assertEqual(code, 1)
            self.assertIn("depends on T1", out)

    def test_R2_moving_the_task_cursor_attests_the_plan(self):
        with _Repo() as repo:
            _in_develop(repo)
            repo.tamper(_widen_t1)
            code, out = repo.cli("spec", "task", "done", "T1")
            self.assertEqual(code, 3)
            self.assertIn("CHANGED after approval", out)


# ======================================================================================
# R3 — the capped clarify
# ======================================================================================

def _q(n):
    return [{"id": f"Q{i}", "question": f"question {i}?", "recommended": f"r{i}",
             "answer": f"a{i}"} for i in range(1, n + 1)]


class TestR3CappedClarify(unittest.TestCase):

    def test_R3_the_clarify_cap_holds_at_five(self):
        spec, _ = spec_from_payload(_payload(clarifications=_q(P.CLARIFY_CAP)))
        self.assertEqual(len(spec.clarifications), 5)
        with self.assertRaises(ValueError) as cm:
            spec_from_payload(_payload(clarifications=_q(P.CLARIFY_CAP + 1)))
        self.assertIn("clarify cap", str(cm.exception))
        self.assertEqual(P.CLARIFY_CAP, 5)

    def test_R3_each_question_carries_its_recommended_answer(self):
        q = [{"id": "Q1", "question": "which?", "answer": "x"}]
        with self.assertRaises(ValueError) as cm:
            spec_from_payload(_payload(clarifications=q))
        self.assertIn("recommended", str(cm.exception))

    def test_R3_an_unanswered_question_proceeds_on_a_stated_assumption(self):
        q = [{"id": "Q1", "question": "paginate?", "recommended": "no", "answer": ""}]
        with self.assertRaises(ValueError) as cm:
            spec_from_payload(_payload(clarifications=q))
        self.assertIn("assumption", str(cm.exception))
        spec, _ = spec_from_payload(_payload(
            clarifications=q, assumptions=[{"id": "A1", "text": "no pagination", "for": "Q1"}]))
        self.assertEqual(spec.assumptions[0].text, "no pagination")

    def test_R3_the_record_is_approved_and_sealed_with_the_spec(self):
        with _Repo() as repo:
            _in_develop(repo)
            data = repo.spec()
            self.assertEqual(data["clarifications"][0]["answer"], "ISO-8601")
            repo.tamper(lambda d: d["clarifications"][0].update(answer="epoch seconds"))
            self.assertFalse(G.check_write(repo.path, "src/api/items.py").allowed,
                             "changing an approved answer is a plan change")


class TestTheSkillsSayIt(unittest.TestCase):

    def test_R2_R3_spec_and_develop_skills_carry_the_plan_and_the_cap(self):
        from mokata.skills import get_skill
        spec = get_skill("spec").prompt
        self.assertIn("AT MOST FIVE clarifying questions", spec)
        self.assertIn("tasks: [{id, title, ac_ids, scope", spec)
        self.assertIn("mokata spec task done", get_skill("develop").prompt)
        from mokata.brainstorm import BRAINSTORM_PROTOCOL
        self.assertIn("capped at FIVE", BRAINSTORM_PROTOCOL)


if __name__ == "__main__":
    unittest.main()
