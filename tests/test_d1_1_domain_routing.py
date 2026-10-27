"""0.0.25 Lane D1 stage 1 — the graph-derived domain classifier is WIRED into develop and review.

The defect (DK-CLUSTER-INERT, doc 84; research 2026-10-16 finding 1): `domains.classify_from_impact`
had no production caller, so `Spec.domains` was only ever what a model typed, and a domain skill
was reached — if at all — by description matching, which vendors' own evals show misses most of
the time and doc 85 §6 bans as keyword auto-activation.

What must be true for the user now:
  * a spec whose approved approach touches React WEB code gets `frontend-react` in `Spec.domains`
    at emit, and the React skill is LOADED (named on the per-turn pack) in develop AND in review;
  * a spec touching NestJS code gets `backend-nestjs`, the same way;
  * NEAR MISSES do not route: a React Native `.tsx`, an Express `*.controller.ts`, a plain TS lib;
  * the routing is a recorded fact a hook-less harness can read (`spec show` / `spec_show`);
  * outside develop/review nothing is loaded, and a run with no repo facts never guesses.
"""
import io
import json
import os
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)
from _plan import (call_planned, commit_planned, emit_planned,  # noqa: F401,E402  (R2)
                   planned, sealed, with_tasks)

from mokata import domains as dk
from mokata import hook_cli
from mokata import run_intent as RI
from mokata.brainstorm_impact import ApproachImpact
from mokata.config import Surface
from mokata.engine.emit import emit_spec, preview_content, spec_from_payload
from mokata.govern.resume import PipelineCheckpoint
from mokata.init import init_repo
from mokata.progress_events import STAGE_ENTER, ProgressLog
from mokata.run_resolver import SPEC_PREFIX
from mokata.session_state import SessionScopedStore
from mokata.state import StateStore
from mokata.tdd_state import state_dir

RID = "rA"

REACT_WEB = {"dependencies": {"react": "19.2.0", "react-dom": "19.2.0"}}
NEXT_APP = {"dependencies": {"next": "16.0.0", "react": "19.2.0"}}
REACT_NATIVE = {"dependencies": {"react": "19.1.0", "react-native": "0.81.0", "expo": "54.0.0"}}
NEST_API = {"dependencies": {"@nestjs/core": "11.1.0", "@nestjs/common": "11.1.0",
                             "@nestjs/platform-express": "11.1.0"}}
EXPRESS_API = {"dependencies": {"express": "5.1.0"}}
PLAIN_TS_LIB = {"devDependencies": {"typescript": "5.9.0", "vitest": "3.2.0"}}


def _silent(*_a):
    pass


def _write(root, rel, text=""):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _package(root, rel_dir, data):
    _write(root, os.path.join(rel_dir, "package.json"), json.dumps(data))


def _handoff(files, targets=("Thing",)):
    return {"topic": "t", "phase": "brainstorm", "approver": "jas", "approved_at": "now",
            "approach": {"name": "A", "summary": "s", "targets": list(targets)},
            "answered_questions": [], "grounding": {},
            "impact": {"approach": "A", "targets": list(targets),
                       "impacted_files": list(files)}}


def _payload(domains=None):
    p = {"title": "Save shows progress",
         "criteria": [{"id": "AC-1", "text": "Given a pending save, when the form renders, then "
                                             "the Save button is disabled and a spinner shows"}],
         "tests": [{"name": "test_save_progress", "ac_ids": ["AC-1"]}]}
    if domains is not None:
        p["domains"] = list(domains)
    return planned(p)


class _Repo(unittest.TestCase):
    """A real initialised repo with an approved approach on run `rA`, emitted through the REAL
    emit path (completeness → quality → WriteGate → `spec_commit`)."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        home = mock.patch.dict(os.environ, {"HOME": self._td.name, "USERPROFILE": self._td.name})
        home.start()
        self.addCleanup(home.stop)
        self.root = os.path.join(self._td.name, "repo")
        os.makedirs(self.root)
        init_repo(root=self.root, profile="standard", assume_yes=True, out=_silent)
        PipelineCheckpoint(Surface.load(self.root).state, RID).ensure_registered()

    def store(self):
        return SessionScopedStore(StateStore(state_dir(self.root)), RID)

    def emit(self, files, domains=None):
        self.store().write("approved_approach", _handoff(files))
        spec, tests = spec_from_payload(_payload(domains))
        out = emit_spec(Surface.load(self.root), spec, tests, store=self.store(), run_id=RID,
                        assume_yes=True)
        self.assertTrue(out.committed, out.reason)
        return StateStore(state_dir(self.root)).read(SPEC_PREFIX + RID)["domains"]

    def enter(self, stage):
        ProgressLog.from_state_dir(state_dir(self.root)).append_event(STAGE_ENTER, stage,
                                                                      run_id=RID)

    def loaded(self, stage):
        self.enter(stage)
        return dk.domain_turn_lines(self.root, RID)


def _names(lines):
    """The skill names a routing line tells the model to load."""
    out = []
    for ln in lines:
        if "load now: " in ln:
            out += [part.strip().strip("`") for part in ln.split("load now: ", 1)[1].split(",")]
    return out


class AReactSpecLoadsTheReactSkillInDevelopAndReview(_Repo):

    def test_a_react_web_spec_is_routed_at_emit_and_loaded_in_both_stages(self):
        _package(self.root, "web", REACT_WEB)
        _write(self.root, "web/src/components/SaveButton.tsx", "export {}\n")
        doms = self.emit(["web/src/components/SaveButton.tsx"])
        self.assertIn("frontend-react", doms, "the classifier's verdict must land in the spec")
        for stage in ("develop", "review"):
            with self.subTest(stage=stage):
                lines = self.loaded(stage)
                self.assertEqual(1, len(lines), "one routing line, whatever the skill count")
                self.assertIn("frontend-react", _names(lines))
                self.assertIn(f"domain skills for {stage} (routed by the approved spec)", lines[0])

    def test_a_next_app_counts_as_react_web(self):
        _package(self.root, ".", NEXT_APP)
        self.assertIn("frontend-react", self.emit(["app/checkout/page.tsx"]))

    def test_nothing_is_loaded_outside_develop_and_review(self):
        _package(self.root, "web", REACT_WEB)
        self.emit(["web/src/components/SaveButton.tsx"])
        for stage in ("brainstorm", "spec", "test", "ship"):
            with self.subTest(stage=stage):
                self.assertEqual([], self.loaded(stage))

    def test_the_human_approves_the_routing_in_the_emit_preview(self):
        _package(self.root, "web", REACT_WEB)
        self.store().write("approved_approach", _handoff(["web/src/components/SaveButton.tsx"]))
        spec, _ = spec_from_payload(_payload())
        shown = json.loads(preview_content(self.store(), spec))
        self.assertIn("frontend-react", shown["domains"])
        self.assertEqual([], spec.domains, "the preview projects onto a copy; it never mutates")


class ANestSpecLoadsTheNestSkill(_Repo):

    def test_a_nestjs_spec_is_routed_and_loaded_in_develop_and_review(self):
        _package(self.root, "api", NEST_API)
        doms = self.emit(["api/src/orders/orders.controller.ts",
                          "api/src/orders/dto/create-order.dto.ts"])
        self.assertIn("backend-nestjs", doms)
        self.assertNotIn("frontend-react", doms)
        for stage in ("develop", "review"):
            with self.subTest(stage=stage):
                self.assertIn("backend-nestjs", _names(self.loaded(stage)))


class NearMissesDoNotRoute(_Repo):

    def test_a_react_native_tsx_is_not_react_web(self):
        _package(self.root, "mobile", REACT_NATIVE)
        doms = self.emit(["mobile/src/components/SaveButton.tsx"])
        self.assertNotIn("frontend-react", doms)
        self.assertNotIn("frontend-react", _names(self.loaded("develop")))

    def test_an_express_controller_is_not_nestjs(self):
        _package(self.root, "server", EXPRESS_API)
        doms = self.emit(["server/src/users/users.controller.ts"])
        self.assertNotIn("backend-nestjs", doms)
        self.assertNotIn("backend-nestjs", _names(self.loaded("review")))

    def test_a_plain_typescript_library_routes_to_neither(self):
        _package(self.root, ".", PLAIN_TS_LIB)
        doms = self.emit(["src/index.ts", "src/format.ts", "src/parse.service.ts"])
        self.assertFalse({"frontend-react", "backend-nestjs"} & set(doms), doms)

    def test_a_monorepo_routes_per_package_not_per_repo(self):
        _package(self.root, "apps/web", REACT_WEB)
        _package(self.root, "apps/mobile", REACT_NATIVE)
        surface = dk.surface_from_files(["apps/mobile/App.tsx"], [], None, self.root)
        self.assertNotIn("frontend-react", dk.classify_domains(surface))
        both = dk.surface_from_files(["apps/mobile/App.tsx", "apps/web/src/App.tsx"],
                                     root=self.root)
        self.assertIn("frontend-react", dk.classify_domains(both))

    def test_with_no_repo_facts_a_framework_domain_never_fires(self):
        impact = ApproachImpact(approach="a", targets=["Button"],
                                impacted_files=["web/src/components/Button.tsx"])
        self.assertNotIn("frontend-react", dk.classify_from_impact(impact))   # no root → no guess
        self.assertIn("frontend-a11y", dk.classify_from_impact(impact),
                      "framework-agnostic domains still classify from the path floor")


class RoutingIsARecordedFactOfTheApprovedSpec(_Repo):

    def test_explicit_domains_are_kept_and_the_derived_ones_are_added(self):
        _package(self.root, "web", REACT_WEB)
        doms = self.emit(["web/src/components/SaveButton.tsx"], domains=["api"])
        self.assertIn("api", doms, "an explicit domain is never dropped")
        self.assertIn("frontend-react", doms, "a derived domain is never silently missed")

    def test_companions_follow_their_parents(self):
        _package(self.root, "web", REACT_WEB)
        doms = self.emit(["web/src/auth/LoginForm.tsx", "web/src/cache/useQueryCache.tsx"])
        for d in ("frontend-react", "security", "performance", "frontend-security",
                  "frontend-perf"):
            self.assertIn(d, doms)
        native = dk.surface_from_files(["web/src/auth/LoginForm.tsx"], [], None, "")
        self.assertNotIn("frontend-security", dk.classify_domains(native),
                         "without the React parent, the companion cannot fire")

    def test_cli_and_mcp_spec_show_print_the_same_routing(self):
        _package(self.root, "web", REACT_WEB)
        self.emit(["web/src/components/SaveButton.tsx"])
        from mokata.cli_commands import spec as CS
        from mokata.mcp import tools_read as T
        buf = io.StringIO()
        with redirect_stdout(buf):
            CS.cmd_spec_show(types.SimpleNamespace(path=self.root))
        routing = dk.domain_routing_line(["frontend-a11y", "frontend-react"])
        self.assertIn("develop loads frontend-a11y, frontend-react", routing)
        self.assertIn("review loads frontend-a11y, frontend-react", routing)
        self.assertIn(routing, buf.getvalue())
        out = T.spec_show(path=self.root, run=RID, response_format="detailed")
        self.assertIn("frontend-react", out["domain_skills"]["develop"])
        self.assertIn("frontend-react", out["domain_skills"]["review"])
        self.assertIn(routing, str(out["block"]))

    def test_the_hook_carries_the_routing_for_a_bound_session_in_review(self):
        _package(self.root, "web", REACT_WEB)
        self.emit(["web/src/components/SaveButton.tsx"])
        self.enter("review")
        surface = Surface.load(self.root)
        self.assertEqual([], [ln for ln in hook_cli._develop_evidence(surface, self.root, "S-x")
                              if "load now" in ln], "an unbound session gets nothing")
        RI.bind_registered(self.root, "S1", RID)
        lines = hook_cli._develop_evidence(surface, self.root, "S1")
        self.assertIn("frontend-react", _names(lines))
        pack = hook_cli._build_injection_for(surface, "review the save button", self.root, "S1")
        self.assertIn("load now: `frontend-a11y`, `frontend-react`", pack.text)


class TheClassifierIsNoLongerInert(unittest.TestCase):

    def test_classify_from_impact_has_a_production_caller(self):
        # CORPUS: THE WORKING TREE — the source this checkout would run, uncommitted edits included.
        src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "mokata")
        callers = []
        for dirpath, _dirs, files in os.walk(src):
            for name in files:
                if name.endswith(".py") and name != "domains.py":
                    with open(os.path.join(dirpath, name), encoding="utf-8") as fh:
                        if "derive_spec_domains(" in fh.read():
                            callers.append(name)
        self.assertIn("emit.py", callers, "every emit surface lands in emit.spec_commit")
        with open(os.path.join(src, "domains.py"), encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("classify_from_impact(impact, root=root)", body)
        self.assertNotIn("NEITHER BRANCH RUNS IN PRODUCTION", body)


if __name__ == "__main__":
    unittest.main()
