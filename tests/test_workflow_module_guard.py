"""Every test module a workflow NAMES must exist where that step will look for it.

The 0.0.16 audit's claim 7(a): the DB.S5 live leg had never once run. Its step said
`working-directory: tests` while `test_db_s5_live_db.py` lives in `tests/integration`, so
the job died on `ModuleNotFoundError` the first time anyone dispatched it — after months of
sitting in the file looking like coverage. `python -m unittest <module>` resolves the module
against the CWD, so a step's `working-directory` and the module's real location are a pair
that nothing checked.

**The sharper finding, recorded so this file is not mistaken for the whole fix:** what let
that defect persist was not the shape of the step — it was that `live-db-legs.yml` had NO
RUNNER for months in the form that mattered. A wrong path fails loudly the moment it runs;
it only stayed invisible because it never ran. This guard is defence-in-depth for the shape,
and it is CHEAP. The actual remedy is scheduling — the opt-in legs are dispatched as part of
the release checklist (doc 86's ✂ CLOSE row), which is what turned two genuine defects up
the first two times the workflow was pressed. Do not let a green here read as "the live legs
are covered".

Needs PyYAML to parse a workflow. That is a required TEST dependency (requirements/ci.txt), not
a mokata dependency, and its absence RAISES — these checks used to `skipUnless` it away, which
reported OK for a workflow lint that never ran (PYYAML-SKIP-CLUSTER, 0.0.18 stage 2).
Pure/offline; deterministic.
"""

import os
import re
import unittest

from _support import sample_manifest_data  # noqa: F401  (path-fix side-effect)
from _workflow_pins import safe_load

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW_DIR = os.path.join(ROOT, ".github", "workflows")

# `python -m unittest [-flags] mod[.Class[.method]] …` — the module form. `discover` is a
# different resolver (`-s`/`-t` name the roots explicitly) and is deliberately not our business.
_UNITTEST_RE = re.compile(r"python\s+-m\s+unittest\b([^\n;|&)]*)")
_DOTTED_RE = re.compile(r"^test[A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")


def _named_modules(run_text, workdir):
    """(module, workdir) for every test module a `run:` block names by module path.

    Line continuations are folded first: the DB.S8e step lists eight modules across
    backslash-continued lines, and a per-line reader would see one of them.
    """
    folded = run_text.replace("\\\n", " ")
    out = []
    for args in _UNITTEST_RE.findall(folded):
        tokens = args.split()
        if tokens and tokens[0] == "discover":
            continue
        for tok in tokens:
            if _DOTTED_RE.match(tok):
                out.append((tok.split(".")[0], workdir))
    return out


def _workflow_named_modules():
    """Every (workflow, step name, module, working-directory) pair in .github/workflows."""
    found = []
    # CORPUS: THE WORKING TREE. `sync-public.sh` rsyncs `tests/` to the public mirror, so an
    # untracked test file ships and must be held to this rule. Converting to the index here is
    # what would reintroduce `SHIPPED-TEST-READS-INTERNAL-FILE` from the blind side.
    for name in sorted(os.listdir(WORKFLOW_DIR)):
        if not name.endswith((".yml", ".yaml")):
            continue
        path = os.path.join(WORKFLOW_DIR, name)
        with open(path, encoding="utf-8") as fh:
            doc = safe_load(fh.read(), "lint the test modules the workflows name")
        for job in (doc.get("jobs") or {}).values():
            job_dir = ((job.get("defaults") or {}).get("run") or {}).get("working-directory", "")
            for step in job.get("steps") or []:
                if not isinstance(step, dict) or "run" not in step:
                    continue
                workdir = step.get("working-directory", job_dir) or ""
                for mod, wd in _named_modules(step["run"], workdir):
                    found.append((name, step.get("name", "(unnamed step)"), mod, wd))
    return found


class TestEveryNamedModuleResolvesWhereTheStepRuns(unittest.TestCase):
    def setUp(self):
        self.named = _workflow_named_modules()

    def test_the_guard_has_something_to_guard(self):
        # If the workflows stop naming modules this whole file silently becomes a no-op.
        self.assertGreater(
            len(self.named), 5,
            "no `python -m unittest <module>` steps found — either the workflows changed shape "
            "or the extractor broke; a vacuous guard is worse than none",
        )

    def test_no_step_names_a_module_that_is_not_there(self):
        broken = []
        for workflow, step, module, workdir in self.named:
            target = os.path.join(ROOT, workdir, module + ".py")
            if os.path.exists(target):
                continue
            # Name where it ACTUALLY is: "you pointed the step at the wrong directory" is the
            # real defect, and a bare "not found" sends the reader hunting.
            elsewhere = [
                rel for rel in ("tests", "tests/integration")
                if os.path.exists(os.path.join(ROOT, rel, module + ".py"))
            ]
            hint = (f" — it exists at {'/'.join(elsewhere)}/{module}.py; the step's "
                    f"working-directory is {workdir or '<repo root>'}"
                    if elsewhere else " — no such test module anywhere")
            broken.append(f"{workflow} · {step!r}: `{module}` will not import{hint}")
        self.assertEqual(broken, [], "\n" + "\n".join(broken))

    def test_every_named_module_is_syntactically_importable(self):
        # Cheaper than importing (no dependency install, no side effects) and catches the
        # other half of "does not import": a module that is present but cannot be parsed.
        import ast
        bad = []
        for workflow, step, module, workdir in self.named:
            target = os.path.join(ROOT, workdir, module + ".py")
            if not os.path.exists(target):
                continue                     # covered, with a better message, by the test above
            try:
                with open(target, encoding="utf-8") as fh:
                    ast.parse(fh.read(), filename=target)
            except SyntaxError as exc:
                bad.append(f"{workflow} · {step!r}: `{module}` does not parse — {exc}")
        self.assertEqual(bad, [], "\n" + "\n".join(bad))


class TestTheExtractorItself(unittest.TestCase):
    """A guard that cannot see the steps it is meant to guard passes for the wrong reason.

    ⚠ These four tests never touched YAML — they feed `_named_modules` synthetic `run:` strings —
    yet they carried the same `skipUnless(_HAVE_YAML, …)` as the class above, so the §7i grading
    of the extractor was itself skipped on any runner without a parser it does not use. Removing
    that decorator does not "make them honest"; it makes four already-honest tests RUN.
    """

    def test_it_folds_backslash_continuations(self):
        run = "python -m unittest -v \\\n  test_alpha \\\n  test_beta\n"
        self.assertEqual(_named_modules(run, "tests"),
                         [("test_alpha", "tests"), ("test_beta", "tests")])

    def test_it_takes_the_module_off_a_dotted_class_path(self):
        run = "python -m unittest -v test_db_s2a_pushdown.LivePostgresPushdownTest\n"
        self.assertEqual(_named_modules(run, "tests"), [("test_db_s2a_pushdown", "tests")])

    def test_it_ignores_discover_and_shell_noise(self):
        self.assertEqual(_named_modules("python -m unittest discover -s tests -t tests", ""), [])
        self.assertEqual(
            _named_modules('out=$(python -m unittest -v test_db_s8c_live_db 2>&1) || exit 1', "x"),
            [("test_db_s8c_live_db", "x")],
        )

    def test_it_reads_the_real_workflows_including_the_leg_that_was_broken(self):
        modules = {(m, w) for _, _, m, w in _workflow_named_modules()}
        self.assertIn(("test_db_s5_live_db", "tests/integration"), modules,
                      "the DB.S5 leg — the one that had never run — must be in the guard's view")


if __name__ == "__main__":
    unittest.main()
