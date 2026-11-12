"""L9 (0.0.27) — G3, the PR check, re-verified on a FRESH repo.

Business rules — what must be true for a stranger who adds the mokata PR check to a new repo:
  * Run from inside the repo (the default `--path .`), approving a spec with `settings.specs.commit`
    on really writes the tracked `.mokata/specs/<run>.json` it says it wrote. A relative repo path
    must not turn the committed copy into a silent no-op while the command still announces it.
  * On a CI checkout (tracked files only, no run state) the COMPLETENESS leg checks the committed
    specs: an acceptance criterion with no test blocks the PR; a fully mapped spec passes. It must
    not say "no saved spec" when `.mokata/specs/` holds one.
  * The check's output shows the AC -> test map (every criterion, the test that covers it, or that
    it has none), so a reviewer can see what was checked without opening the spec.
  * The PR comment is well-formed markdown: one bullet per gate verdict, no doubled bullet markers.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest

import _support  # noqa: F401

from mokata import cli
from mokata.ci_check import run_ci_check
from mokata.init import init_repo

SPEC = {
    "title": "calc add",
    "criteria": [{"id": "AC-1", "text": "add returns the sum"},
                 {"id": "AC-2", "text": "add handles negatives"}],
    "tests": [{"name": "tests/test_calc.py::test_add", "ac_ids": ["AC-1"]},
              {"name": "tests/test_calc.py::test_neg", "ac_ids": ["AC-2"]}],
    "tasks": [{"id": "T1", "title": "add", "ac_ids": ["AC-1", "AC-2"],
               "scope": ["src/**", "tests/**"]}],
}
BOTH_TESTS = (
    "import unittest\n"
    "class T(unittest.TestCase):\n"
    "    def test_add(self):\n"
    "        'AC-1: add returns the sum'\n"
    "        self.assertEqual(1 + 2, 3)\n"
    "    def test_neg(self):\n"
    "        'AC-2: add handles negatives'\n"
    "        self.assertEqual(-1 + -2, -3)\n")
ONE_TEST = (
    "import unittest\n"
    "class T(unittest.TestCase):\n"
    "    def test_add(self):\n"
    "        'AC-1: add returns the sum'\n"
    "        self.assertEqual(1 + 2, 3)\n")


def _write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


class _FreshRepo:
    """A brand-new repo with mokata initialised and the committed-specs setting on."""

    def __init__(self):
        self._dir = tempfile.TemporaryDirectory()
        self.path = os.path.realpath(self._dir.name)
        init_repo(root=self.path, profile="standard", assume_yes=True, out=lambda *_a: None)
        _write(self.path, "src/calc.py", "def add(a, b):\n    return a + b\n")
        _write(self.path, "spec.json", json.dumps(SPEC))
        self._cwd = os.getcwd()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        os.chdir(self._cwd)
        self._dir.cleanup()

    def enable_commit(self):
        manifest = os.path.join(self.path, ".mokata", "manifest.json")
        with open(manifest, encoding="utf-8") as fh:
            data = json.load(fh)
        data.setdefault("settings", {}).setdefault("specs", {})["commit"] = True
        with open(manifest, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, indent=2)

    def emit_from_inside(self):
        """`mokata spec emit --file spec.json --yes` typed in the repo, default --path."""
        os.chdir(self.path)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["spec", "emit", "--file", "spec.json", "--yes"])
        return code, out.getvalue() + err.getvalue()

    def emit_tests(self, names):
        """Emit a one-AC spec whose single test is declared as `names[0]`; (code, output)."""
        spec = dict(SPEC, criteria=SPEC["criteria"][:1],
                    tests=[{"name": names[0], "ac_ids": ["AC-1"]}],
                    tasks=[dict(SPEC["tasks"][0], ac_ids=["AC-1"])])
        _write(self.path, "spec.json", json.dumps(spec))
        return self.emit_from_inside()

    def committed_spec(self):
        d = os.path.join(self.path, ".mokata", "specs")
        with open(os.path.join(d, os.listdir(d)[0]), encoding="utf-8") as fh:
            return json.load(fh)

    def committed_files(self):
        d = os.path.join(self.path, ".mokata", "specs")
        return sorted(os.listdir(d)) if os.path.isdir(d) else []

    def as_ci_checkout(self):
        os.chdir(self._cwd)
        shutil.rmtree(os.path.join(self.path, ".mokata", "temp_local"), ignore_errors=True)


def _leg(result, name):
    return next(leg for leg in result.legs if leg.name == name)


class TestTheCommittedCopyFromInsideTheRepo(unittest.TestCase):

    def test_L9_emit_with_the_default_path_writes_the_committed_spec_it_announces(self):
        """The command prints 'committed copy: .mokata/specs/<run>.json'. That file must exist
        when the user ran it from inside the repo, which is how everyone runs it."""
        with _FreshRepo() as repo:
            repo.enable_commit()
            code, out = repo.emit_from_inside()
            self.assertEqual(code, 0, out)
            self.assertIn("committed copy: .mokata/specs/", out)
            self.assertEqual(len(repo.committed_files()), 1,
                             "the announced committed spec was not written:\n" + out)


class TestCompletenessOnACiCheckout(unittest.TestCase):

    def _ci(self, tests_text):
        repo = _FreshRepo()
        self.addCleanup(repo.__exit__, None, None, None)
        repo.enable_commit()
        _write(repo.path, "tests/test_calc.py", tests_text)
        code, out = repo.emit_from_inside()
        self.assertEqual(code, 0, out)
        repo.as_ci_checkout()
        return repo, run_ci_check(repo.path, ["src/calc.py", "tests/test_calc.py"])

    def test_L9_a_committed_spec_with_every_ac_tested_passes_completeness(self):
        repo, res = self._ci(BOTH_TESTS)
        leg = _leg(res, "completeness")
        self.assertEqual(leg.status, "pass", leg.summary)
        self.assertNotIn("no saved spec", leg.summary)

    def test_L9_a_committed_spec_with_an_untested_ac_blocks_the_pr(self):
        """AC-2 has no test in the PR: the check must say so and fail, not skip."""
        repo, res = self._ci(ONE_TEST)
        leg = _leg(res, "completeness")
        self.assertEqual(leg.status, "block", leg.summary)
        self.assertTrue(res.blocked)
        self.assertEqual(res.exit_code, 1)
        self.assertIn("AC-2", "\n".join(leg.detail))
        self.assertTrue(leg.unblock)

    def test_L9_the_output_shows_the_ac_to_test_map(self):
        repo, res = self._ci(ONE_TEST)
        text = res.render(ascii_only=True)
        self.assertRegex(text, r"AC-1 -> .*test_add")
        self.assertRegex(text, r"AC-2 -> .*no test")
        comment = res.comment_body()
        self.assertRegex(comment, r"AC-1 .*test_add")
        self.assertRegex(comment, r"AC-2 .*no test")

    def test_L9_no_committed_spec_still_skips_completeness_visibly(self):
        with _FreshRepo() as repo:
            res = run_ci_check(repo.path, ["src/calc.py"])
            leg = _leg(res, "completeness")
            self.assertEqual(leg.status, "absent")
            self.assertFalse(res.blocked)


class TestTheCommentIsWellFormedMarkdown(unittest.TestCase):

    def test_L9_each_verdict_is_one_bullet_without_doubled_markers(self):
        with _FreshRepo() as repo:
            repo.enable_commit()
            _write(repo.path, "tests/test_calc.py", ONE_TEST)
            code, out = repo.emit_from_inside()
            self.assertEqual(code, 0, out)
            repo.as_ci_checkout()
            body = run_ci_check(repo.path, ["src/calc.py"]).comment_body()
            for line in body.splitlines():
                stripped = line.strip()
                self.assertFalse(stripped.startswith("- - "), "doubled bullet: " + line)
                self.assertFalse(stripped.startswith("- • "), "doubled bullet: " + line)

    def test_L9_a_skipped_leg_is_one_bullet_too(self):
        """A leg with nothing to check says so as a single list item, not a bullet inside a bullet."""
        with _FreshRepo() as repo:
            body = run_ci_check(repo.path, ["src/calc.py"]).comment_body()
            skipped = [ln for ln in body.splitlines() if "skipped" in ln]
            self.assertTrue(skipped, body)
            for line in skipped:
                self.assertTrue(line.startswith("- ") and not line.startswith("- •"), line)


def _committed(repo, name, title, ids, tests=None):
    """A committed spec file. `tests` maps AC id -> declared test (None = the file declares none)."""
    body = {"title": title, "run_id": name,
            "criteria": [{"id": i, "text": f"{title} {i}"} for i in ids]}
    if tests is not None:
        body["tests"] = [{"name": f"tests/test_{name}.py::test_{i.lower().replace('-', '_')}",
                          "ac_ids": [i]} for i in tests]
    _write(repo.path, f".mokata/specs/{name}.json", json.dumps(body))


def _tests_file(repo, name, ids):
    body = "import unittest\nclass T(unittest.TestCase):\n"
    for i in ids:
        fn = i.lower().replace("-", "_")
        body += f"    def test_{fn}(self):\n        '{i}'\n        self.assertTrue(True)\n"
    _write(repo.path, f"tests/test_{name}.py", body)


class TestSeveralCommittedSpecs(unittest.TestCase):
    """Several specs are committed at once and every one numbers its criteria AC-1, AC-2."""

    def _repo(self, specs):
        """specs: (name, title, ids, declared_tests, tests_present_in_tree)"""
        repo = _FreshRepo()
        self.addCleanup(repo.__exit__, None, None, None)
        for name, title, ids, declared, present in specs:
            _committed(repo, name, title, ids, declared)
            _tests_file(repo, name, present)
        return repo

    def _comp(self, repo):
        return _leg(run_ci_check(repo.path, ["README.md"]), "completeness")

    def test_L9_each_spec_is_graded_by_its_own_tests_and_all_pass(self):
        repo = self._repo([(n, n, ["AC-1", "AC-2"], ["AC-1", "AC-2"], ["AC-1", "AC-2"])
                           for n in ("one", "two", "three")])
        comp = self._comp(repo)
        self.assertEqual(comp.status, "pass", comp.summary)
        text = "\n".join(comp.detail)
        for n in ("one", "two", "three"):
            self.assertRegex(text, rf"{n}: AC-1 -> tests/test_{n}.py::test_ac_1")

    def test_L9_a_specs_ac_is_never_satisfied_by_another_specs_test(self):
        """"two" declares a test for AC-1 that is gone from the tree; "one" and "three" still have
        theirs, tagged AC-1 too. "two" must block: their tests are not its proof."""
        for bad_pos in range(3):
            with self.subTest(bad_pos=bad_pos):
                names = ["one", "two", "three"]
                specs = [(n, n, ["AC-1", "AC-2"], ["AC-1", "AC-2"],
                          ["AC-2"] if n == names[bad_pos] else ["AC-1", "AC-2"]) for n in names]
                repo = self._repo(specs)
                res = run_ci_check(repo.path, ["README.md"])
                comp = _leg(res, "completeness")
                self.assertEqual(comp.status, "block", comp.summary)
                self.assertTrue(res.blocked)
                text = "\n".join(comp.detail)
                bad = names[bad_pos]
                self.assertIn(f"{bad}: AC-1 -> (no test)", text)
                self.assertIn(f"{bad}: unmapped acceptance criteria: AC-1", text)
                for other in names:
                    if other != bad:
                        self.assertNotIn(f"{other}: AC-1 -> (no test)", text)
                self.assertEqual(_leg(res, "spec-drift").status, "block")

    def test_L9_a_declared_test_that_was_renamed_no_longer_proves_its_ac(self):
        """The spec declares test_ac_1; the tree's AC-1 test in the same file is now test_renamed."""
        repo = self._repo([("one", "one", ["AC-1"], ["AC-1"], [])])
        _write(repo.path, "tests/test_one.py",
               "def test_renamed():\n    'AC-1'\n    assert True\n")
        comp = self._comp(repo)
        self.assertEqual(comp.status, "block", comp.summary)
        self.assertIn("one: AC-1 -> (no test)", "\n".join(comp.detail))

    def test_L9_the_map_names_only_the_specs_own_tests(self):
        repo = self._repo([("one", "one", ["AC-1"], ["AC-1"], ["AC-1"]),
                           ("two", "two", ["AC-1"], ["AC-1"], ["AC-1"])])
        text = "\n".join(self._comp(repo).detail)
        self.assertIn("one: AC-1 -> tests/test_one.py::test_ac_1", text)
        self.assertEqual(text.count("::test_ac_1"), 2, text)

    def test_L9_a_spec_that_records_no_tests_is_not_graded_and_not_passed(self):
        repo = self._repo([("old", "old", ["AC-1"], None, ["AC-1"]),
                           ("new", "new", ["AC-1"], ["AC-1"], ["AC-1"])])
        res = run_ci_check(repo.path, ["README.md"])
        comp, drift = _leg(res, "completeness"), _leg(res, "spec-drift")
        self.assertNotEqual(comp.status, "pass", comp.summary)
        self.assertIn("NOT graded", comp.summary)
        self.assertIn("mokata spec emit", comp.summary)
        self.assertRegex("\n".join(comp.detail), r"new: AC-1 -> tests/test_new.py::test_ac_1")
        self.assertNotEqual(drift.status, "pass", drift.summary)
        self.assertIn("NOT graded", drift.summary)
        self.assertFalse(res.blocked)

    def test_L9_a_block_in_any_spec_wins_over_an_ungraded_one(self):
        repo = self._repo([("old", "old", ["AC-1"], None, ["AC-1"]),
                           ("bad", "bad", ["AC-1"], ["AC-1"], [])])
        _write(repo.path, "tests/test_other.py", "def test_x():\n    'AC-1'\n")
        self.assertEqual(self._comp(repo).status, "block")

    def test_L9_docsync_spec_drift_grades_committed_specs_by_their_own_tests(self):
        from mokata.docsync import spec_drift
        repo = self._repo([("one", "one", ["AC-1"], ["AC-1"], ["AC-1"]),
                           ("two", "two", ["AC-1"], ["AC-1"], [])])
        _write(repo.path, "tests/test_other.py", "def test_x():\n    'AC-1'\n")
        sd = spec_drift(repo.path)
        self.assertEqual([f.section.split(" · ")[0] for f in sd.findings], ["two"], sd.findings)

    def test_L9_the_emit_records_the_specs_own_test_map_in_the_committed_file(self):
        with _FreshRepo() as repo:
            repo.enable_commit()
            code, out = repo.emit_from_inside()
            self.assertEqual(code, 0, out)
            name = repo.committed_files()[0]
            with open(os.path.join(repo.path, ".mokata", "specs", name), encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertEqual(sorted(t["name"] for t in data["tests"]),
                             ["tests/test_calc.py::test_add", "tests/test_calc.py::test_neg"])
            self.assertEqual({t["name"]: t["ac_ids"] for t in data["tests"]}
                             ["tests/test_calc.py::test_neg"], ["AC-2"])


HAPPY = "import unittest\nclass T(unittest.TestCase):\n    def test_happy_path(self):\n        'AC-1'\n"


class TestDeclaredTestsAreFullNormalisedPaths(unittest.TestCase):

    def _repo(self):
        repo = _FreshRepo()
        self.addCleanup(repo.__exit__, None, None, None)
        repo.enable_commit()
        return repo

    def test_L9_a_bare_test_name_found_in_two_files_refuses_the_emit(self):
        """alpha and beta both define test_happy_path: which one proves the AC is ambiguous."""
        repo = self._repo()
        _write(repo.path, "tests/test_alpha.py", HAPPY)
        _write(repo.path, "tests/test_beta.py", HAPPY)
        code, out = repo.emit_tests(["test_happy_path"])
        self.assertEqual(code, 1, out)
        self.assertIn("tests/test_alpha.py", out)
        self.assertIn("tests/test_beta.py", out)
        self.assertEqual(repo.committed_files(), [], "a refused emit writes nothing")

    def test_L9_a_bare_test_name_found_in_no_file_refuses_the_emit(self):
        repo = self._repo()
        code, out = repo.emit_tests(["test_happy_path"])
        self.assertEqual(code, 1, out)
        self.assertIn("no file", out)
        self.assertEqual(repo.committed_files(), [])

    def test_L9_the_emit_names_every_bare_name_it_resolved(self):
        """beta writes a bare test_happy_path; the emit says it means alpha's file, out loud."""
        repo = self._repo()
        _write(repo.path, "tests/test_alpha.py", HAPPY)
        code, out = repo.emit_tests(["test_happy_path"])
        self.assertEqual(code, 0, out)
        self.assertIn("test_happy_path -> tests/test_alpha.py::test_happy_path", out)

    def test_L9_a_full_name_needs_no_resolution_note(self):
        repo = self._repo()
        _write(repo.path, "tests/test_alpha.py", HAPPY)
        code, out = repo.emit_tests(["tests/test_alpha.py::test_happy_path"])
        self.assertEqual(code, 0, out)
        self.assertNotIn("resolved to its file", out)

    def test_L9_the_approval_preview_names_the_resolution_before_the_write(self):
        """What the human approves (MCP proposal preview) already carries the resolution."""
        from mokata import approval, session
        from mokata.mcp import tools_write as TW
        repo = self._repo()
        _write(repo.path, "tests/test_alpha.py", HAPPY)
        os.environ[session.SESSION_ID_ENV] = "run-l9-preview"
        self.addCleanup(os.environ.pop, session.SESSION_ID_ENV, None)
        session.reset_for_test()
        proposed = TW.spec_emit(
            path=repo.path, title="beta", criteria=[{"id": "AC-1", "text": "b"}],
            tests=[{"name": "test_happy_path", "ac_ids": ["AC-1"]}],
            tasks=[{"id": "T1", "title": "t", "ac_ids": ["AC-1"], "scope": ["src/**"]}])
        self.assertEqual(proposed.get("status"), "proposed", proposed)
        self.assertEqual(proposed.get("test_resolutions"),
                         ["test_happy_path -> tests/test_alpha.py::test_happy_path"])
        preview = approval.load(repo.path, proposed["proposal_id"]).preview
        self.assertIn("test_happy_path -> tests/test_alpha.py::test_happy_path", preview)
        self.assertEqual(repo.committed_files(), [], "nothing is written before approval")

    def test_L9_the_preview_the_gate_shows_carries_the_resolution(self):
        """`preview_content` is what every gate shows the human; a resolved name is in it."""
        from mokata.committed_specs import canonical_test_refs
        from mokata.config import Surface
        from mokata.engine.emit import preview_content, spec_for_emit
        repo = self._repo()
        _write(repo.path, "tests/test_alpha.py", HAPPY)
        spec, tests = spec_for_emit(dict(SPEC, criteria=SPEC["criteria"][:1],
                                         tests=[{"name": "test_happy_path", "ac_ids": ["AC-1"]}],
                                         tasks=[dict(SPEC["tasks"][0], ac_ids=["AC-1"])]),
                                    repo.path)
        body = json.loads(preview_content(Surface.load(repo.path).state, spec, tests))
        self.assertEqual(body["test_resolutions"],
                         ["test_happy_path -> tests/test_alpha.py::test_happy_path"])
        plain = json.loads(preview_content(Surface.load(repo.path).state, spec))
        self.assertNotIn("test_resolutions", plain)

    def test_L9_the_mcp_commit_gate_is_shown_the_resolution_too(self):
        """The preview handed to the write gate on an approved MCP emit carries the resolution."""
        from unittest import mock
        from mokata import session
        from mokata.engine import emit as E
        from mokata.mcp import tools_write as TW
        repo = self._repo()
        _write(repo.path, "tests/test_alpha.py", HAPPY)
        os.environ[session.SESSION_ID_ENV] = "run-l9-gate"
        self.addCleanup(os.environ.pop, session.SESSION_ID_ENV, None)
        session.reset_for_test()
        seen = []
        real = E.preview_content
        def spy(store, spec, tests=None):
            seen.append(list(tests or []))
            return real(store, spec, tests)
        with mock.patch.object(E, "preview_content", spy):
            from _support import mcp_commit
            mcp_commit(TW.spec_emit, path=repo.path, title="beta",
                       criteria=[{"id": "AC-1", "text": "b"}],
                       tests=[{"name": "test_happy_path", "ac_ids": ["AC-1"]}],
                       tasks=[{"id": "T1", "title": "t", "ac_ids": ["AC-1"], "scope": ["src/**"]}])
        self.assertTrue(any(t.resolved_from == "test_happy_path" for call in seen for t in call),
                        seen)

    def test_L9_the_pr_map_prints_the_full_file_and_test(self):
        """A reviewer must see WHOSE file proves the AC, in the terminal and in the PR comment."""
        repo = self._repo()
        _write(repo.path, "tests/test_alpha.py", HAPPY)
        code, out = repo.emit_tests(["test_happy_path"])
        self.assertEqual(code, 0, out)
        repo.as_ci_checkout()
        res = run_ci_check(repo.path, ["README.md"])
        for text in (res.render(ascii_only=True), res.comment_body()):
            self.assertIn("AC-1 -> tests/test_alpha.py::test_happy_path", text)

    def test_L9_a_bare_test_name_in_one_file_is_recorded_as_file_and_test(self):
        repo = self._repo()
        _write(repo.path, "tests/test_alpha.py", HAPPY)
        code, out = repo.emit_tests(["test_happy_path"])
        self.assertEqual(code, 0, out)
        self.assertEqual(repo.committed_spec()["tests"][0]["name"],
                         "tests/test_alpha.py::test_happy_path")

    def test_L9_a_bare_name_in_a_committed_file_proves_nothing(self):
        """Even a hand-written bare name must not credit another file's test."""
        repo = self._repo()
        _write(repo.path, "tests/test_alpha.py", HAPPY)
        _write(repo.path, ".mokata/specs/beta.json", json.dumps(
            {"title": "beta", "criteria": [{"id": "AC-1", "text": "b"}],
             "tests": [{"name": "test_happy_path", "ac_ids": ["AC-1"]}]}))
        res = run_ci_check(repo.path, ["README.md"])
        self.assertEqual(_leg(res, "completeness").status, "block", res.render())

    def test_L9_every_spelling_of_the_same_path_is_the_same_test(self):
        """A backslash, a leading ./ and a doubled / name tests/test_alpha.py: all must pass, in
        the committed file and as recorded by the emit."""
        for spelling in ("tests\\test_alpha.py", "./tests/test_alpha.py", "tests//test_alpha.py"):
            with self.subTest(spelling=spelling):
                repo = self._repo()
                _write(repo.path, "tests/test_alpha.py", HAPPY)
                code, out = repo.emit_tests([spelling + "::test_happy_path"])
                self.assertEqual(code, 0, out)
                self.assertEqual(repo.committed_spec()["tests"][0]["name"],
                                 "tests/test_alpha.py::test_happy_path")
                # and a file written by hand in the other spelling is compared the same way
                _write(repo.path, ".mokata/specs/zzz.json", json.dumps(
                    {"title": "zzz", "criteria": [{"id": "AC-1", "text": "z"}],
                     "tests": [{"name": spelling + "::test_happy_path", "ac_ids": ["AC-1"]}]}))
                repo.as_ci_checkout()
                comp = _leg(run_ci_check(repo.path, ["README.md"]), "completeness")
                self.assertNotEqual(comp.status, "block", comp.summary + str(comp.detail))

    def test_L9_a_path_that_leaves_the_repo_refuses_the_emit(self):
        for bad in ("../x/test_a.py::t", "tests/../../a.py::t", "/abs/test_a.py::t",
                    "C:\\a\\test_a.py::t", "C:/a/test_a.py::t"):
            with self.subTest(bad=bad):
                repo = self._repo()
                code, out = repo.emit_tests([bad])
                self.assertEqual(code, 1, out)
                self.assertEqual(repo.committed_files(), [])

    def test_L9_a_test_that_lost_its_ac_tag_no_longer_credits_the_ac(self):
        """The spec declares test_a for AC-1 and AC-2; the tree's test_a now tags only AC-2."""
        repo = self._repo()
        _write(repo.path, "tests/test_a.py",
               "def test_a():\n    'AC-2'\n    assert True\n"
               "def test_b():\n    'AC-1'\n    assert True\n")
        _write(repo.path, ".mokata/specs/a.json", json.dumps(
            {"title": "a", "criteria": [{"id": "AC-1", "text": "x"}, {"id": "AC-2", "text": "y"}],
             "tests": [{"name": "tests/test_a.py::test_a", "ac_ids": ["AC-1", "AC-2"]}]}))
        comp = _leg(run_ci_check(repo.path, ["README.md"]), "completeness")
        self.assertEqual(comp.status, "block", comp.summary)
        text = "\n".join(comp.detail)
        self.assertIn("a: AC-1 -> (no test)", text)
        self.assertIn("a: AC-2 -> tests/test_a.py::test_a", text)


if __name__ == "__main__":
    unittest.main()
