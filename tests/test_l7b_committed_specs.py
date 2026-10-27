"""L7b (0.0.26, ruling D1) — opt-in committed specs.

Business rules — what must be true for the user:
  * By default a spec stays in `.mokata/temp_local/`: approving one writes nothing tracked.
  * With `settings.specs.commit: true` (a governed, human-gated setting), approving a spec ALSO
    writes the approved spec — criteria, tasks[], clarifications[], plan hash — to the tracked
    `.mokata/specs/<run>.json`, inside the same human-gated commit, and the approval preview names
    that file. Nothing is written before the human approves. An amendment updates the file.
  * `mokata ci-check` and `mokata docsync` read `.mokata/specs/` on a fresh checkout (no run state)
    and fail on drift between a committed spec and the code.
  * Without committed specs, both say "no committed specs — spec drift not checked" (exit 0, never a
    silent pass).
  * `mokata doctor` names each run blocked by the 0.0.26 task-plan change and the exact command
    that re-approves it.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401
from _support import mcp_commit

from mokata import committed_specs as CS                          # noqa: E402
from mokata import gate_hook as G                                 # noqa: E402
from mokata import session, tdd_state as T                        # noqa: E402
from mokata.ci_check import run_ci_check                          # noqa: E402
from mokata.config import Surface                                 # noqa: E402
from mokata.config_cmd import config_set                          # noqa: E402
from mokata.docsync import render_spec_drift, spec_drift          # noqa: E402
from mokata.govern.doctor import diagnose                         # noqa: E402
from mokata.init import init_repo                                 # noqa: E402
from mokata.mcp import tools_write as TW                          # noqa: E402
from mokata.state import StateStore                               # noqa: E402

RUN = "run-l7b"
CRITERIA = [{"id": "AC1", "text": "charge() bills the card",
             "anchors": [{"file": "src/pay.py", "symbol": "charge", "kind": "impl",
                          "source": "spec", "confidence": "high"}]}]
TESTS = [{"name": "test_charge", "ac_ids": ["AC1"]}]
TASKS = [{"id": "T1", "title": "charge", "ac_ids": ["AC1"], "scope": ["src/pay.py"]}]
CLARIFY = [{"id": "Q1", "question": "which currency?", "recommended": "EUR", "answer": "EUR"}]


class _Repo:
    def __init__(self, *, commit=False):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.realpath(self.dir.name)
        init_repo(root=self.path, profile="standard", assume_yes=True, out=lambda *_a: None)
        os.makedirs(os.path.join(self.path, "src"))
        with open(os.path.join(self.path, "src", "pay.py"), "w", encoding="utf-8") as fh:
            fh.write("def charge(card):\n    return card\n")
        self._env = mock.patch.dict(os.environ, {session.SESSION_ID_ENV: RUN})
        self._env.start()
        session.reset_for_test()
        if commit:
            res = config_set(self.path, "settings.specs.commit", "true", assume_yes=True,
                             out=lambda *_a: None)
            assert res.committed, res

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self._env.stop()
        session.reset_for_test()
        self.dir.cleanup()

    def emit(self, **kw):
        args = dict(path=self.path, title="payments", criteria=CRITERIA, tests=TESTS,
                    tasks=TASKS, clarifications=CLARIFY)
        args.update(kw)
        return mcp_commit(TW.spec_emit, **args)

    def committed(self):
        p = CS.spec_path(self.path, RUN)
        if not os.path.isfile(p):
            return None
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)

    def cli(self, *argv):
        import io
        from contextlib import redirect_stderr, redirect_stdout
        from mokata.cli import main
        out, err = io.StringIO(), io.StringIO()
        # Never the ambient stdin (a36): a non-TTY empty stream, so "no human here" is the arm
        # graded whether the suite runs at a terminal or in CI.
        with redirect_stdout(out), redirect_stderr(err), \
                mock.patch("sys.stdin", io.StringIO("")):
            try:
                code = main([*argv, "--path", self.path])
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue() + err.getvalue()

    def run_state(self):
        return StateStore(T.state_dir(self.path)).read(G.SPEC_PREFIX + RUN)

    def fresh_checkout(self):
        """What CI sees: the tracked tree, no run state."""
        shutil.rmtree(os.path.join(self.path, ".mokata", "temp_local"), ignore_errors=True)


class TestL7bTheCommittedCopy(unittest.TestCase):

    def test_L7b_off_by_default_nothing_tracked_is_written(self):
        with _Repo() as repo:
            res = repo.emit()
            self.assertTrue(res.get("committed"), res)
            self.assertFalse(os.path.exists(CS.specs_dir(repo.path)))

    def test_L7b_on_the_approved_spec_is_committed_whole(self):
        with _Repo(commit=True) as repo:
            res = repo.emit()
            self.assertTrue(res.get("committed"), res)
            on_disk = repo.committed()
            self.assertIsNotNone(on_disk, "the approved spec must be committed")
            state = repo.run_state()
            self.assertEqual(on_disk.pop("run_id"), RUN)
            self.assertEqual(on_disk, state, "the committed copy is exactly the approved spec")
            self.assertEqual(on_disk["tasks"][0]["id"], "T1")
            self.assertEqual(on_disk["clarifications"][0]["answer"], "EUR")
            self.assertTrue(on_disk["plan_hash"].startswith("sha256:"))

    def test_L7b_the_committed_file_is_lf_only_and_stable_on_every_os(self):
        """A committed file must not churn by OS: LF line endings (written with newline="\\n",
        never the platform's translation) and sorted keys, so a re-commit is byte-identical."""
        import mokata.atomicfile as AF
        seen = []
        real = AF.os.fdopen

        def spy(fd, *a, **kw):
            seen.append(kw.get("newline", "<platform>"))
            return real(fd, *a, **kw)
        with _Repo(commit=True) as repo, mock.patch.object(AF.os, "fdopen", spy):
            repo.emit()
            with open(CS.spec_path(repo.path, RUN), "rb") as fh:
                raw = fh.read()
            self.assertNotIn(b"\r", raw)
            self.assertEqual(raw, CS.render(RUN, repo.run_state()).encode("utf-8"))
            self.assertIn("\n", seen, "the committed spec is written with newline='\\n'")
            keys = list(json.loads(raw))
            self.assertEqual(keys, sorted(keys))

    def test_L7b_nothing_is_committed_before_the_human_approves(self):
        with _Repo(commit=True) as repo:
            proposed = TW.spec_emit(path=repo.path, title="payments", criteria=CRITERIA,
                                    tests=TESTS, tasks=TASKS)
            self.assertEqual(proposed.get("status"), "proposed", proposed)
            self.assertIsNone(repo.committed())
            from mokata import approval
            p = approval.load(repo.path, proposed["proposal_id"])
            self.assertIn(".mokata/specs/" + RUN + ".json", p.preview,
                          "the human sees the tracked write before approving it")

    def test_L7b_an_amendment_updates_the_committed_spec(self):
        with _Repo(commit=True) as repo:
            repo.emit()
            T.record(Surface.load(repo.path).state, RUN, red=["test_charge"])
            res = mcp_commit(TW.spec_amend, path=repo.path, title="payments", criteria=CRITERIA,
                             tests=TESTS, tasks=TASKS,
                             clarifications=[dict(CLARIFY[0], answer="USD")],
                             reason="the currency changed")
            self.assertTrue(res.get("committed"), res)
            on_disk = repo.committed()
            self.assertEqual(on_disk["clarifications"][0]["answer"], "USD")
            self.assertEqual(on_disk["version"], 2)
            self.assertEqual(on_disk["plan_hash"], repo.run_state()["plan_hash"])


class TestL7bCiReadsTheCommittedSpecs(unittest.TestCase):

    def test_L7b_ci_check_checks_committed_specs_on_a_fresh_checkout(self):
        with _Repo(commit=True) as repo:
            repo.emit()
            repo.fresh_checkout()
            res = run_ci_check(repo.path, ["src/pay.py"])
            leg = next(leg for leg in res.legs if leg.name == "spec-drift")
            self.assertEqual(leg.status, "pass", leg.summary)
            # the code drifts from the committed spec: the anchored symbol is renamed
            with open(os.path.join(repo.path, "src", "pay.py"), "w", encoding="utf-8") as fh:
                fh.write("def bill(card):\n    return card\n")
            res = run_ci_check(repo.path, ["src/pay.py"])
            leg = next(leg for leg in res.legs if leg.name == "spec-drift")
            self.assertEqual(leg.status, "block")
            self.assertEqual(res.exit_code, 1)
            self.assertIn("charge", "\n".join(leg.detail))

    def test_L7b_without_committed_specs_ci_says_so_and_exits_zero(self):
        with _Repo() as repo:
            repo.emit()
            repo.fresh_checkout()
            res = run_ci_check(repo.path, ["src/pay.py"])
            self.assertEqual(res.exit_code, 0)
            self.assertIn("no committed specs — spec drift not checked", res.render())
            self.assertIn("no committed specs — spec drift not checked", res.comment_body())

    def test_L7b_an_unreadable_committed_spec_blocks(self):
        with _Repo() as repo:
            os.makedirs(CS.specs_dir(repo.path))
            with open(CS.spec_path(repo.path, "broken"), "w", encoding="utf-8") as fh:
                fh.write("{not json")
            res = run_ci_check(repo.path, [])
            self.assertEqual(res.exit_code, 1)

    def test_L7b_docsync_reads_committed_specs_and_says_when_there_are_none(self):
        with _Repo(commit=True) as repo:
            repo.emit()
            repo.fresh_checkout()
            sd = spec_drift(repo.path)
            self.assertEqual(sd.status, "checked")
            self.assertFalse(sd.findings)
        with _Repo() as repo:
            repo.fresh_checkout()
            out = render_spec_drift(spec_drift(repo.path))
            self.assertIn("no committed specs — spec drift not checked", out)
        with _Repo() as repo:
            repo.emit()               # checked from local run state — still says CI would not be
            out = render_spec_drift(spec_drift(repo.path))
            self.assertIn("OK", out)
            self.assertIn("no committed specs", out)


class TestL7bDoctorNamesTheReapproval(unittest.TestCase):

    def _old_spec(self, repo, scope=None):
        data = {"title": "old", "criteria": [{"id": "AC1", "text": "x"}]}
        if scope is not None:
            data["scope"] = scope
        StateStore(T.state_dir(repo.path)).write(G.SPEC_PREFIX + RUN, data)

    def test_L7b_doctor_names_the_exact_command_for_a_0025_spec(self):
        with _Repo() as repo:
            self._old_spec(repo)
            found = [f for f in diagnose(Surface.load(repo.path)).findings
                     if f.code == "spec-needs-reapproval"]
            self.assertEqual(len(found), 1)
            self.assertIn(f"mokata spec migrate {RUN}", found[0].detail)

    def test_L7b_a_0025_run_is_blocked_then_migrate_and_approve_unblocks_it(self):
        with _Repo() as repo:
            self._old_spec(repo, scope={"authorized": ["src/*"], "deferred": []})
            T.record(StateStore(T.state_dir(repo.path)), RUN, red=["test_charge"])
            target = os.path.join(repo.path, "src", "pay.py")
            self.assertFalse(G.check_write(repo.path, target, content="x = 1").allowed,
                             "a 0.0.25-shaped spec blocks implementation writes")
            # off a TTY with no approval: a proposal, nothing written, still blocked
            code, out = repo.cli("spec", "migrate", RUN)
            self.assertEqual(code, 3, out)
            self.assertIn("task T1", out, "the plan is shown before it is approved")
            self.assertIn("scope src/*", out)
            pid = out.split("mokata approve ")[1].split("`")[0]
            self.assertFalse(G.check_write(repo.path, target, content="x = 1").allowed)
            from mokata import approval
            approval.approve(repo.path, pid, actor="jas")
            code, out = repo.cli("spec", "migrate", RUN)
            self.assertEqual(code, 0, out)
            data = repo.run_state()
            self.assertEqual(data["tasks"][0]["ac_ids"], ["AC1"])
            self.assertEqual(data["tasks"][0]["scope"], ["src/*"])
            self.assertTrue(data["plan_hash"].startswith("sha256:"))
            self.assertTrue(G.check_write(repo.path, target, content="x = 1").allowed,
                            "migrated + approved: writes are allowed again")

    def test_L7b_migrate_refuses_a_run_that_already_has_a_plan(self):
        with _Repo() as repo:
            repo.emit()
            code, out = repo.cli("spec", "migrate", RUN, "--yes")
            self.assertEqual(code, 1)
            self.assertIn("already has a task plan", out)

    def test_L7b_doctor_is_quiet_for_an_approved_plan(self):
        with _Repo() as repo:
            repo.emit()
            codes = [f.code for f in diagnose(Surface.load(repo.path)).findings]
            self.assertNotIn("spec-needs-reapproval", codes)


class TestL7bTheSettingIsGoverned(unittest.TestCase):

    def test_L7b_the_setting_is_documented_and_walked_by_the_wizard(self):
        from mokata.config_wizard import SETTINGS
        self.assertIn("settings.specs.commit", [s.key for s in SETTINGS])
        with _Repo() as repo:
            self.assertFalse(CS.commit_enabled(repo.path), "off by default")
            config_set(repo.path, "settings.specs.commit", "true", assume_yes=True,
                       out=lambda *_a: None)
            self.assertTrue(CS.commit_enabled(repo.path))


if __name__ == "__main__":
    unittest.main()
