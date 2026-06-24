"""Stage 9 — the full v1 integration playbook.

Drives ONE real story end-to-end through the actual pipeline, reusing every prior stage —
no new engine logic, only orchestration:

  brainstorm (approve an approach, persisted)        [D6/D7]
    -> spec emit BLOCKED by the completeness gate     [D2/D3]  (until ACs map to tests)
    -> tests recorded RED                             [E1]
    -> implement allowed only after RED               [E1]
    -> review (two-stage) via the chosen exec mode    [E3/E8]
  with the knowledge layer + memory active            [B/C]
  every step logged to the audit ledger               [I3]

Runs on any profile and in either execution mode; parallel degrades to sequential when no
subagent runner is supplied (degrade-safe).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .brainstorm import Approach, BrainstormSession, ground, persist_approach
from .engine import AcceptanceCriterion, Spec, TestRef, run_completeness_gate
from .execmode import SEQUENTIAL, ExecutionChoice, Task, run_tasks, two_stage_review
from .govern import AuditLedger, RedBeforeGreenError, TddGuard
from .knowledge import KnowledgeLayer
from .memory import DECISION, MemoryItem, MemoryStore

# The sample story the playbook proves the pipeline on.
STORY = {
    "topic": "add a slugify helper",
    "question": "Should slugify preserve unicode or be ASCII-only?",
    "answer": "ASCII-only, lowercased, hyphen-separated",
    "approaches": [
        {"name": "regex", "summary": "strip non-alphanumerics with a regex",
         "pros": ["tiny", "no dependency"], "cons": ["unicode edge cases"]},
        {"name": "library", "summary": "use a slug library",
         "pros": ["robust unicode"], "cons": ["adds a dependency"]},
    ],
    "chosen": "regex",
    "title": "slugify",
    "acs": [("AC-1", "lowercases and hyphenates words"),
            ("AC-2", "strips punctuation")],
    "tests": [{"name": "test_lowercase_hyphenate", "acs": ["AC-1"]},
              {"name": "test_strips_punctuation", "acs": ["AC-2"]}],
}

# The checks that must hold on every profile/mode for the run to pass.
_REQUIRED = ("brainstorm_approved", "gate_blocked_initially", "gate_passed_after_tests",
             "red_before_green", "review_passed", "knowledge_used", "within_budget")


@dataclass
class PlaybookResult:
    profile: str
    exec_mode: str
    checks: Dict[str, Any] = field(default_factory=dict)
    degraded: bool = False
    ledger_count: int = 0

    @property
    def ok(self) -> bool:
        if not all(self.checks.get(k) is True for k in _REQUIRED):
            return False
        if self.checks.get("memory_enabled"):
            return self.checks.get("memory_written") is True
        return True

    def render(self) -> str:
        head = (f"mokata v1 playbook — profile '{self.profile}', mode '{self.exec_mode}'"
                + (" (degraded -> sequential)" if self.degraded else ""))
        lines = [head]
        for key, val in self.checks.items():
            if isinstance(val, bool):
                lines.append(f"  [{'PASS' if val else 'FAIL'}] {key}")
            else:
                lines.append(f"  [info] {key} = {val}")
        lines.append(f"  ledger entries: {self.ledger_count}")
        lines.append("  RESULT: " + ("PASS" if self.ok else "FAIL"))
        return "\n".join(lines)


def run_playbook(surface: Any, exec_choice: Optional[ExecutionChoice] = None,
                 runner: Any = None) -> PlaybookResult:
    exec_choice = exec_choice or ExecutionChoice(SEQUENTIAL)
    led = AuditLedger.from_mokata_dir(surface.mokata_dir)
    checks: Dict[str, Any] = {}

    # 1) Brainstorm — explore, approve one approach, persist it (D6/D7).
    session = BrainstormSession(STORY["topic"], grounding=ground(surface.router))
    session.ask(STORY["question"])
    session.answer(STORY["answer"])
    session.propose_approaches([Approach(**a) for a in STORY["approaches"]])
    session.approve("playbook", STORY["chosen"])
    persist_approach(session, surface.state)
    led.record("playbook", step="brainstorm", approved=session.can_emit_spec)
    checks["brainstorm_approved"] = session.can_emit_spec
    checks["knowledge_layer_on"] = surface.manifest.layer_enabled("knowledge")

    spec = Spec(STORY["title"],
                [AcceptanceCriterion(i, t) for i, t in STORY["acs"]])

    # 2) Completeness gate with NO tests -> emit BLOCKED (D2/D3).
    blocked = run_completeness_gate(spec, [], store=surface.state)
    checks["gate_blocked_initially"] = not blocked.passed
    checks["approach_in_gate"] = blocked.approach_present
    led.record("playbook", step="gate_block", passed=blocked.passed,
               unmapped=blocked.unmapped_ids)

    # 3) Tests written and recorded RED (E1).
    tests = [TestRef(t["name"], t["acs"]) for t in STORY["tests"]]
    guard = TddGuard(ledger=led)
    for t in tests:
        guard.record_red(t.name)

    # 4) Completeness gate now passes once every AC maps to a test.
    passed = run_completeness_gate(spec, tests, store=surface.state)
    checks["gate_passed_after_tests"] = passed.passed
    led.record("playbook", step="gate_pass", passed=passed.passed)

    # 5) RED-before-GREEN: implementing an unwritten test is blocked; a RED one is allowed.
    blocked_impl = False
    try:
        guard.guard_implementation("test_never_written")
    except RedBeforeGreenError:
        blocked_impl = True
    guard.guard_implementation(tests[0].name)
    checks["red_before_green"] = blocked_impl and guard.allow_implementation(tests[0].name)

    # 6) Implement + review through the chosen execution mode (E8); two-stage review (E3).
    tasks = [Task(t.name, f"implement {t.name}", context=STORY["title"]) for t in tests]
    run = run_tasks(tasks, exec_choice, runner=runner, ledger=led, budget=200_000)
    reviews = [two_stage_review(tk, rs) for tk, rs in zip(tasks, run.results)]
    checks["review_passed"] = bool(reviews) and all(r.passed for r in reviews)
    checks["exec_mode"] = exec_choice.mode
    checks["within_budget"] = run.within_budget

    # 7) Knowledge layer active — a structural query runs (graph if present, else floor).
    layer = KnowledgeLayer.from_surface(surface)
    q = layer.callers("slugify")
    checks["knowledge_used"] = isinstance(q.count, int)
    checks["knowledge_backend"] = layer.backend_name

    # 8) Memory — record the decision (human-gated), when the memory layer is on.
    store = MemoryStore.from_surface(surface)
    checks["memory_enabled"] = bool(store.enabled_types)
    if store.enabled_types:
        wr = store.remember(
            MemoryItem.create(f"decision:{STORY['title']}", STORY["chosen"],
                              mtype=DECISION),
            assume_yes=True)
        checks["memory_written"] = wr.committed
    else:
        checks["memory_written"] = None

    led.record("playbook", step="done", profile=surface.manifest.profile,
               mode=exec_choice.mode, degraded=run.degraded)
    return PlaybookResult(profile=surface.manifest.profile, exec_mode=exec_choice.mode,
                          checks=checks, degraded=run.degraded,
                          ledger_count=len(led.entries()))
