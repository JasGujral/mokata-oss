"""0.0.29 lane D1 — `TESTS-INHERIT-HOST-GIT-CONFIG`: ONE hermetic git door for the whole suite.

The Claude desktop app exports `GIT_CONFIG_PARAMETERS="'safe.bareRepository=explicit' ..."` to
every shell it opens. 37 tests spread `os.environ` into a git child and went red there and green in
CI (`docs/build/handoff/0029-evidence/D/repro.txt`). Twelve private `_GIT_ENV` copies scrubbed two
names each and let the rest ride through.

The rule (design note D §3a):
  * a test process never sees a host `GIT_*` variable it did not set itself: `import _support`
    deletes them all (predicate: `mokata.git_safe.scrubbed_env`, through `tests/_hermetic_git.py`)
    and records the NAMES, never a value;
  * nothing in `tests/` but the door names `GIT_CONFIG_GLOBAL`/`SYSTEM`/`NOSYSTEM` as an env key
    or binds a `_GIT_ENV`, except a stated arrangement (`mock.patch.dict(os.environ, {...})`);
  * a module that spawns git reaches the door (`_support`, `_hermetic_git` or the e2e `_harness`).

FIRING CONTROLS (mutants, `scripts/mutate.sh`): drop the scrub loop in `_support` (`TheDoor` reds);
drop the sweep's patch-dict exemption (`TheSweep` reds on the real tree).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import ast
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

import _support  # noqa: F401  (puts src/ on the path)
import _hermetic_git

TESTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS)

#: The app's export set, transcribed (`repro.txt`), plus the other names a host or a git hook sets.
APP_EXPORTS = {
    "GIT_CONFIG_PARAMETERS": "'safe.bareRepository=explicit' 'core.fsmonitor='",
    "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "safe.bareRepository",
    "GIT_CONFIG_VALUE_0": "explicit",
    "GIT_DIR": os.path.join(tempfile.gettempdir(), "not-a-repo-d1"),
    "GIT_INDEX_FILE": os.path.join(tempfile.gettempdir(), "not-an-index-d1"),
    "git_editor": "planted-lowercase",                       # case-insensitive, like git_safe
}
#: A value that must never be printed (assembled so no scanner mistakes it for a credential).
MARKER = "-".join(("d1", "never", "printed", "3f9a"))


def _child(code, extra_env, cwd=None):
    """Run `code` in a fresh interpreter whose environment AT START carries `extra_env`."""
    env = dict(os.environ)
    env.update(extra_env)
    env["PYTHONPATH"] = TESTS
    return subprocess.run([sys.executable, "-c", textwrap.dedent(code)], env=env, cwd=cwd or TESTS,
                          capture_output=True, text=True, stdin=subprocess.DEVNULL)


class TheDoor(unittest.TestCase):
    """The scrub runs at `import _support`, process-wide, for every child that spreads os.environ."""

    def test_no_host_git_variable_survives_import(self):
        p = _child("""
            import os, _support
            print(sorted(k for k in os.environ if k.upper().startswith("GIT_")))
            print(sorted(_support.HERMETIC_GIT_SCRUBBED))
        """, APP_EXPORTS)
        self.assertEqual(p.returncode, 0, p.stderr)
        left, scrubbed = p.stdout.strip().splitlines()[-2:]
        self.assertEqual(left, "[]", "a host GIT_* variable survived `import _support`")
        for name in APP_EXPORTS:
            # Windows environment names are case-INSENSITIVE and `os.environ` upper-cases them
            # there, so the planted `git_editor` is recorded as `GIT_EDITOR` (mirror probe run
            # 43413469846). Still dropped either way: `left` above is `[]`.
            self.assertIn(repr(name.upper() if os.name == "nt" else name), scrubbed)

    def test_the_record_carries_names_never_values(self):
        p = _child("""
            import _support
            print(repr(_support.HERMETIC_GIT_SCRUBBED))
        """, {"GIT_CONFIG_PARAMETERS": "'http.extraHeader=X-Marker: %s'" % MARKER})
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("GIT_CONFIG_PARAMETERS", p.stdout)
        self.assertNotIn(MARKER, p.stdout + p.stderr)

    def test_the_integration_suite_gets_the_same_door(self):
        """`tests/integration/_support.py` loads the unit file BY PATH — the scrub must ride along."""
        integ = os.path.join(TESTS, "integration")
        p = _child("""
            import os, sys
            sys.path.insert(0, %r)
            import _support
            print(sorted(k for k in os.environ if k.upper().startswith("GIT_")))
        """ % integ, APP_EXPORTS, cwd=integ)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.strip().splitlines()[-1], "[]")

    @unittest.skipUnless(shutil.which("git"), "needs git")
    def test_a_bare_repo_symbolic_ref_under_the_app_export_set(self):
        """The red that started the row: under `safe.bareRepository=explicit`, a child git reading a
        bare repo found by discovery refuses. A spawn that spreads os.environ after the door: rc 0."""
        d = tempfile.mkdtemp(prefix="d1-bare-")
        self.addCleanup(shutil.rmtree, d, True)
        bare = os.path.join(d, "r.git")
        subprocess.run(["git", "init", "-q", "--bare", bare], check=True,
                       env=_hermetic_git.git_env(), stdin=subprocess.DEVNULL, capture_output=True)
        p = _child("""
            import os, subprocess, _support
            r = subprocess.run(["git", "symbolic-ref", "HEAD"], cwd=%r, env=dict(os.environ),
                               capture_output=True, text=True, stdin=subprocess.DEVNULL)
            print("rc=%%d" %% r.returncode, r.stderr.strip())
        """ % bare, {"GIT_CONFIG_PARAMETERS": APP_EXPORTS["GIT_CONFIG_PARAMETERS"]})
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("rc=0", p.stdout, p.stdout)

    def test_a_hostile_arrangement_inside_a_test_still_reaches_the_child(self):
        """The door is at import; `mock.patch.dict` after it is the test's own stated arrangement."""
        from unittest import mock
        with mock.patch.dict(os.environ, {"GIT_DIR": "/arranged"}):
            out = subprocess.run([sys.executable, "-c", "import os; print(os.environ.get('GIT_DIR'))"],
                                 capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout
        self.assertEqual(out.strip(), "/arranged")
        self.assertNotIn("GIT_DIR", os.environ)

    def test_git_env_is_the_scrub_plus_the_named_extras(self):
        from unittest import mock
        with mock.patch.dict(os.environ, {"GIT_CONFIG_PARAMETERS": "x", "GIT_SSH": "evil"}):
            env = _hermetic_git.git_env(GIT_CONFIG_COUNT="1", HOME="/h", USERPROFILE="/h")
        self.assertNotIn("GIT_CONFIG_PARAMETERS", env)
        self.assertNotIn("GIT_SSH", env)                       # a host-chosen helper never runs
        self.assertEqual(env["GIT_CONFIG_GLOBAL"], os.devnull)
        self.assertEqual(env["GIT_CONFIG_NOSYSTEM"], "1")
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(env["GIT_CONFIG_COUNT"], "1")         # `extra` is the caller's: kept
        self.assertEqual(env["HOME"], "/h")

    def test_the_e2e_harness_uses_the_door(self):
        """`tests/e2e_ide/_harness.py` cannot import `_support` (no PyYAML in the e2e job)."""
        p = _child("""
            import os, sys
            sys.modules["yaml"] = None                         # the e2e job has no PyYAML
            sys.path.insert(0, %r)
            import _harness
            print(sorted(k for k in _harness._env() if k.upper().startswith("GIT_")))
        """ % os.path.join(TESTS, "e2e_ide"), APP_EXPORTS)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.strip().splitlines()[-1], "['GIT_TERMINAL_PROMPT']")

    def test_the_e2e_harness_scrubs_the_process_so_a_bare_git_spawn_is_clean(self):
        """The e2e half of the door is PROCESS-WIDE, as `_support`'s is: an e2e flow or fixture that
        spawns git with no `env=` (`test_search_jobs_worktrees`, `_explain_fixture.git`) inherits
        this process's env, so a host `GIT_DIR` must be gone from `os.environ` once `_harness` is
        imported. FIRING CONTROL: drop the harness's `del os.environ[...]` loop — `GIT_DIR` survives
        and the bare `git rev-parse` in a real repo fails with "not a git repository"."""
        repo = tempfile.mkdtemp(prefix="d1-e2e-bare-")
        self.addCleanup(shutil.rmtree, repo, True)
        subprocess.run(["git", "init", "-q", repo], env=_hermetic_git.git_env(), check=True,
                       capture_output=True, stdin=subprocess.DEVNULL)
        p = _child("""
            import os, subprocess, sys
            sys.modules["yaml"] = None                         # the e2e job has no PyYAML
            sys.path.insert(0, %r)
            import _harness
            r = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=%r,
                               capture_output=True, text=True, stdin=subprocess.DEVNULL)
            print("LEFT=%%s" %% sorted(k for k in os.environ if k.upper().startswith("GIT_")))
            print("RC=%%d %%s" %% (r.returncode, (r.stdout + r.stderr).strip()))
        """ % (os.path.join(TESTS, "e2e_ide"), repo),
                   dict(APP_EXPORTS, GIT_DIR="/nonexistent"))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("LEFT=[]", p.stdout)
        self.assertIn("RC=0 true", p.stdout)


# ---- the sweep -------------------------------------------------------------------------------

_DOOR_KEYS = {"GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_NOSYSTEM"}
_PRIVATE_NAME = re.compile(r"^_?GIT_?ENV$", re.I)
_DOOR_MODULES = {"_support", "_hermetic_git", "_harness"}


def _is_patch_dict(call):
    f = call.func
    return isinstance(f, ast.Attribute) and f.attr == "dict" and (
        (isinstance(f.value, ast.Name) and f.value.id == "patch")
        or (isinstance(f.value, ast.Attribute) and f.value.attr == "patch"))


def offences(source, name="<planted>"):
    """Every place in `source` that builds a private git door. Pure: AST only, nothing imported."""
    tree = ast.parse(source, filename=name)
    stated = set()                                   # dict displays handed straight to patch.dict
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_patch_dict(node):
            for arg in node.args[1:]:
                stated.add(id(arg))
    out = []

    def key(n):
        return n.value if isinstance(n, ast.Constant) and isinstance(n.value, str) else None

    for node in ast.walk(tree):
        if isinstance(node, ast.Dict) and id(node) not in stated:
            for k in node.keys:
                if key(k) in _DOOR_KEYS:
                    out.append((node.lineno, "dict key %s" % key(k)))
        elif isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg in _DOOR_KEYS:
                    out.append((node.lineno, "keyword %s=" % kw.arg))
            f = node.func
            if (isinstance(f, ast.Attribute) and f.attr in ("setdefault", "__setitem__")
                    and node.args and key(node.args[0]) in _DOOR_KEYS):
                out.append((node.lineno, "%s(%s)" % (f.attr, key(node.args[0]))))
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Subscript) and key(t.slice) in _DOOR_KEYS:
                    out.append((node.lineno, "subscript [%s] =" % key(t.slice)))
                tname = t.id if isinstance(t, ast.Name) else (
                    t.attr if isinstance(t, ast.Attribute) else None)
                if tname and _PRIVATE_NAME.match(tname):
                    out.append((node.lineno, "private door %s" % tname))
    return out


def spawns_git_without_the_door(source):
    """True when `source` spawns git (a list/tuple display headed by "git") and imports no door."""
    tree = ast.parse(source)
    spawns = any(isinstance(n, (ast.List, ast.Tuple)) and n.elts
                 and isinstance(n.elts[0], ast.Constant) and n.elts[0].value == "git"
                 for n in ast.walk(tree))
    if not spawns:
        return False
    for n in ast.walk(tree):
        if isinstance(n, ast.Import) and any(a.name.split(".")[0] in _DOOR_MODULES for a in n.names):
            return False
        if isinstance(n, ast.ImportFrom) and (n.module or "").split(".")[0] in _DOOR_MODULES:
            return False
    return True


def _tree_sources():
    # CORPUS: THE WORKING TREE — every test module a run on this checkout would import,
    # tracked or not: an untracked one spawning git around the door runs all the same.
    for dirpath, dirnames, files in os.walk(TESTS):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for f in files:
            if f.endswith(".py"):
                path = os.path.join(dirpath, f)
                with open(path, encoding="utf-8") as fh:
                    yield os.path.relpath(path, TESTS), fh.read()


class TheSweep(unittest.TestCase):

    PLANTS = {
        "private dict": '_GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_AUTHOR_NAME": "t"}\n',
        "renamed constant": '_HOST = {"GIT_CONFIG_SYSTEM": os.devnull}\n',
        "spread display": 'env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull}\n',
        "dict keyword": "env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull)\n",
        "subscript": 'env = dict(os.environ)\nenv["GIT_CONFIG_NOSYSTEM"] = "1"\n',
        "update keyword": "env.update(GIT_CONFIG_SYSTEM=os.devnull)\n",
        "setdefault": 'os.environ.setdefault("GIT_CONFIG_GLOBAL", os.devnull)\n',
        "private name, no key": "GIT_ENV = dict(IDENTITY)\n",
    }

    def test_every_planted_shape_is_convicted(self):
        for label, src in self.PLANTS.items():
            with self.subTest(label):
                self.assertTrue(offences(src), "the sweep let a %s through" % label)

    def test_a_stated_arrangement_is_not_an_offence(self):
        src = 'with mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": hostile}):\n    pass\n'
        self.assertEqual(offences(src), [])
        src2 = 'with patch.dict(os.environ, {"GIT_CONFIG_SYSTEM": x}, clear=True):\n    pass\n'
        self.assertEqual(offences(src2), [])

    def test_a_git_spawn_without_the_door_is_convicted(self):
        bad = 'import os, subprocess\nsubprocess.run(["git", "init"], env=dict(os.environ))\n'
        self.assertTrue(spawns_git_without_the_door(bad))
        good = 'import _support\nimport subprocess\nsubprocess.run(["git", "init"])\n'
        self.assertFalse(spawns_git_without_the_door(good))

    def test_the_tree_has_no_private_door(self):
        found = []
        for rel, src in _tree_sources():
            if rel == "_hermetic_git.py":
                continue
            found += ["%s:%d %s" % (rel, line, what) for line, what in offences(src, rel)]
        self.assertEqual(found, [], "private git doors — use tests/_hermetic_git.py:\n  "
                         + "\n  ".join(found))

    def test_every_git_spawning_module_reaches_the_door(self):
        """Test MODULES (`test_*.py`): a helper (`_shipped_reads.py`, `_explain_fixture.py`) runs only
        inside a test process, which imported `_support` before it."""
        found = [rel for rel, src in _tree_sources()
                 if os.path.basename(rel).startswith("test_") and spawns_git_without_the_door(src)]
        self.assertEqual(found, [], "modules spawn git with no door imported: %s" % found)

    def test_the_sweep_sees_the_doors_users(self):
        """Anti-vacuity: a walk that found nothing would pass everything."""
        users = [rel for rel, src in _tree_sources() if "_hermetic_git." in src
                 and rel not in ("_hermetic_git.py", os.path.basename(__file__))]
        self.assertGreaterEqual(len(users), 13, users)
        spawning = [rel for rel, src in _tree_sources() if re.search(r'\[\s*"git"', src)]
        self.assertGreaterEqual(len(spawning), 50, "the git-spawn detector went blind")


if __name__ == "__main__":
    unittest.main()
