"""0.0.26 L12 PY-FLOOR-3.12 — one Python floor, said the same way everywhere.

Jas, 2026-10-22: "we can force mokata to support 3.12 and onwards". What must be true for a user:

  * pip refuses mokata on Python < 3.12 (`requires-python = ">=3.12"`), and that floor is the ONE
    number `mokata.PYTHON_FLOOR`, every workflow's Python, the pinned requirements and the docs use;
  * a route that RUNS the source without installing it (the plugin's hooks and `mokata-launch`, a
    clone on PYTHONPATH) never runs an older Python: each launcher ASKS the candidate its version,
    skips one below 3.12, and — when that is all there is — says "found Python 3.11 (python3), but
    mokata needs Python 3.12+", instead of "no Python" or a traceback from deep inside;
  * `import mokata` on an older Python stops with one sentence naming the floor and the Python;
  * no 3.10/3.11 compatibility code survives (no backcompat pre-1.0).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import _support  # noqa: F401

import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
import unittest

from _workflow_pins import safe_load

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
HOOKS = os.path.join(SRC, "mokata", "hooks")
WORKFLOWS = os.path.join(ROOT, ".github", "workflows")

import mokata  # noqa: E402

FLOOR = "%d.%d" % mokata.PYTHON_FLOOR


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def _ver(v):
    return tuple(int(x) for x in str(v).split(".")[:2])


class OneFloor(unittest.TestCase):
    def test_pyproject_requires_the_floor_and_classifies_nothing_below_it(self):
        with open(os.path.join(ROOT, "pyproject.toml"), "rb") as fh:
            proj = tomllib.load(fh)["project"]
        self.assertEqual("3.12", FLOOR)
        self.assertEqual(">=" + FLOOR, proj["requires-python"])
        below = [c for c in proj["classifiers"]
                 if re.search(r"Python :: 3\.\d+$", c)
                 and _ver(c.rsplit(" ", 1)[1]) < mokata.PYTHON_FLOOR]
        self.assertEqual([], below)

    def test_every_workflow_runs_a_python_at_or_above_the_floor(self):
        seen = []
        # CORPUS: THE WORKING TREE — the workflows the mirror runs.
        for name in sorted(os.listdir(WORKFLOWS)):
            if not name.endswith((".yml", ".yaml")):
                continue
            doc = safe_load(_read(".github", "workflows", name), "grade the Python of " + name)
            for jid, job in (doc.get("jobs") or {}).items():
                matrix = ((job.get("strategy") or {}).get("matrix") or {})
                pys = [str(v) for v in matrix.get("python", []) or []]
                pys += [str(e["python"]) for e in matrix.get("include", []) or []
                        if isinstance(e, dict) and "python" in e]
                for step in job.get("steps") or []:
                    v = (step.get("with") or {}).get("python-version")
                    if v is not None and "${{" not in str(v):
                        pys.append(str(v))
                for v in pys:
                    seen.append(v)
                    self.assertGreaterEqual(_ver(v), mokata.PYTHON_FLOOR,
                                            "%s:%s runs Python %s, below the floor" % (name, jid, v))
        self.assertIn(FLOOR, seen)

    def test_the_floor_leg_runs_exactly_the_floor(self):
        doc = safe_load(_read(".github", "workflows", "python-floor.yml"), "grade the floor leg")
        pys = [str(s["with"]["python-version"]) for s in doc["jobs"]["floor"]["steps"]
               if "setup-python" in str(s.get("uses", ""))]
        self.assertEqual([FLOOR], pys)

    def test_pinned_requirements_are_compiled_for_the_floor(self):
        for name in sorted(os.listdir(os.path.join(ROOT, "requirements"))):
            if name.endswith(".txt"):
                with self.subTest(req=name):
                    self.assertIn("--python-version %s " % FLOOR, _read("requirements", name)[:400])
        self.assertIn("--python-version %s" % FLOOR, _read("scripts", "gen-pinned-requirements.sh"))

    def test_the_docs_say_the_floor_and_no_older_one(self):
        for rel in ("README.md", "docs/getting-started.md", "docs/how-to/install-mokata.md",
                    "docs/quickstart.md", "docs/index.md", "docs/reference/platform-support.md",
                    "CONTRIBUTING.md"):
            text = _read(*rel.split("/"))
            with self.subTest(doc=rel):
                self.assertIn("3.12", text)
                self.assertIsNone(re.search(r"(≥ ?|>= ?)3\.1[01]\b|3\.1[01]\+|3\.10[–-]3\.13", text),
                                  "%s still promises an older Python" % rel)
        self.assertIn("mokata needs Python 3.12 or newer", _read("docs", "how-to", "install-mokata.md"))


class TheCompatCodeIsGone(unittest.TestCase):
    def test_no_3_10_or_3_11_branch_or_shim_in_src(self):
        hits = []
        # CORPUS: THE WORKING TREE — what ships.
        for dirpath, _d, files in os.walk(os.path.join(SRC, "mokata")):
            if "vendor" in dirpath or "__pycache__" in dirpath:
                continue
            for f in files:
                if not f.endswith(".py"):
                    continue
                path = os.path.join(dirpath, f)
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
                for pat in (r"version_info\s*[<>]=?\s*\(3,\s*1[01]\)", r"\bimport tomli\b",
                            r"typing_extensions", r"_ReceiptReader"):
                    if re.search(pat, text):
                        hits.append("%s: %s" % (os.path.relpath(path, ROOT), pat))
        self.assertEqual([], hits)


def _fake_python(d, version, name="python3"):
    """A `python3` that answers every question with `version` — the launcher only ASKS it."""
    path = os.path.join(d, name)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("#!/bin/sh\necho %s\n" % version)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


@unittest.skipIf(os.name == "nt" or not shutil.which("sh"), "the POSIX launchers need a POSIX sh")
class TheLaunchersAskForTheFloor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mk-l12-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)

    def _run(self, script, *args):
        env = {"PATH": self.bin, "HOME": self.tmp, "USERPROFILE": self.tmp, "MOKATA_PYTHON_DIRS": "",
               "MOKATA_UV_DIRS": "", "CLAUDE_CONFIG_DIR": os.path.join(self.tmp, "cc")}
        return subprocess.run([shutil.which("sh"), os.path.join(HOOKS, script), *args], env=env,
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)

    def test_an_older_python_is_skipped_and_named(self):
        _fake_python(self.bin, "3.11")
        for script, args, code in (("mokata-hook-launch", ("session-start",), 1),
                                   ("mokata-launch", ("doctor",), 1),
                                   ("launch.sh", ("x.py",), 0)):
            with self.subTest(launcher=script):
                p = self._run(script, *args)
                self.assertEqual(code, p.returncode, p.stderr)
                self.assertIn("3.11 (python3)", p.stderr)
                self.assertIn("mokata needs Python 3.12+", p.stderr)
                self.assertEqual("", p.stdout, "an older Python was RUN")

    def test_a_floor_python_is_used(self):
        _fake_python(self.bin, "3.12")
        for script, args in (("mokata-hook-launch", ("session-start",)),
                             ("mokata-launch", ("doctor",)), ("launch.sh", ("x.py",))):
            with self.subTest(launcher=script):
                p = self._run(script, *args)
                self.assertEqual(0, p.returncode, p.stderr)
                self.assertEqual("3.12", p.stdout.strip(), "the 3.12 python was not exec'd")

    def test_no_python_at_all_names_no_version(self):
        p = self._run("mokata-hook-launch", "session-start")
        self.assertEqual(1, p.returncode)
        self.assertIn("neither a Python 3.12+ nor uv", p.stderr)
        self.assertNotIn("found Python", p.stderr)


class TheWindowsLauncherAsksToo(unittest.TestCase):
    def test_the_cmd_ladder_probes_each_candidate_for_the_floor(self):
        text = _read("src", "mokata", "hooks", "mokata-hook-launch.cmd")
        self.assertIn("sys.version_info >= (3, 12)", text)
        for cand in ('call :probe "%MOKATA_PYTHON%"', "call :probe python",
                     "call :probe python3", "call :probe py -3"):
            self.assertIn(cand, text)
        self.assertIn("mokata needs Python 3.12+", text)
        self.assertNotIn('set "MOKATA_PY=python"', text, "an unprobed candidate is still used")


class ImportSaysTheFloor(unittest.TestCase):
    def test_import_on_an_older_python_stops_with_one_sentence(self):
        code = ("import sys; sys.version_info = (3, 11, 9, 'final', 0); "
                "sys.path.insert(0, %r); import mokata" % SRC)
        p = subprocess.run([sys.executable, "-c", code], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(1, p.returncode)
        self.assertIn("mokata needs Python 3.12 or newer; this is Python 3.11", p.stderr)
        self.assertNotIn("Traceback", p.stderr)

    def test_import_on_the_floor_is_silent(self):
        p = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, %r); "
                            "import mokata; print(mokata.PYTHON_FLOOR)" % SRC],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
        self.assertEqual(0, p.returncode, p.stderr)
        self.assertEqual("(3, 12)", p.stdout.strip())


if __name__ == "__main__":
    unittest.main()
