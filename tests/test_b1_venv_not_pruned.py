"""0.0.23 B1 — `VENV-NOT-PRUNED`: a Python environment is never the user's source, whatever it is called.

REPRODUCED 2026-10-14 on v0.0.21 source: `repo_walk.prune_source_dirs` dropped hidden directories,
nested checkouts and `languages.VENDOR_DIRS` — and that set was `{"node_modules"}` only. So `venv/`,
`env/`, any-name venvs and in-repo conda envs were walked as source:

    walker yields: src/app.py, env2/lib/x/c.py, venv/lib/python3.12/site-packages/requests/api.py

On a real repo that is tens of thousands of site-packages files in the graph floor, the index, the
IDE tree and search, and `defs`/`callers`/blast radius naming third-party code. Same class as the
0.0.20 `node_modules` defect (`test_a21_…`), Python side never covered.

The fix keys on STRUCTURE (`pyvenv.cfg`, `conda-meta/`), never on a name — so a real package called
`env/` is KEPT. Both directions are pinned here.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import shutil
import subprocess
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import languages
from mokata.knowledge.grep_backend import GrepBackend
from mokata.repo_walk import is_python_env, prune_source_dirs, under_python_env


def _write(root, rel, text=""):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:                      # binary: byte-exact on Windows too
        fh.write(text.encode("utf-8"))
    return path


def _fixture():
    """The exact fixture the defect was reproduced on, plus the negatives."""
    d = tempfile.mkdtemp()
    _write(d, "src/app.py", "def my_own_thing():\n    return 1\n")
    # a PEP 405 venv with no leading dot
    _write(d, "venv/pyvenv.cfg", "home = /usr/bin\n")
    _write(d, "venv/lib/python3.12/site-packages/requests/api.py", "def get():\n    pass\n")
    # a second one under an arbitrary name
    _write(d, "py312/pyvenv.cfg", "home = /usr/bin\n")
    _write(d, "py312/lib/python3.12/site-packages/thirdparty.py", "def vendored_only():\n    pass\n")
    # a conda env
    os.makedirs(os.path.join(d, "env2", "conda-meta"))
    _write(d, "env2/lib/x/c.py", "def conda_thing():\n    pass\n")
    # ⛔ a REAL source package named env/ — no marker — must be KEPT
    _write(d, "env/settings.py", "def load_settings():\n    pass\n")
    # PEP 582 + a bare site-packages outside any env
    _write(d, "__pypackages__/3.12/lib/pkg.py", "def pep582():\n    pass\n")
    _write(d, "build/site-packages/pkg2.py", "def target_install():\n    pass\n")
    return d


def _walk(root):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        prune_source_dirs(dirpath, dirnames)
        for f in filenames:
            if f.endswith(".py"):
                out.append(os.path.relpath(os.path.join(dirpath, f), root).replace(os.sep, "/"))
    return sorted(out)


class TheLiveDefect(unittest.TestCase):
    """The reproduction, required gone — through the shared walker and through a real backend."""

    def setUp(self):
        self.root = _fixture()
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_the_walker_yields_only_the_users_own_source(self):
        self.assertEqual(_walk(self.root), ["env/settings.py", "src/app.py"])

    def test_defs_answers_from_the_users_code_only(self):
        backend = GrepBackend(self.root)
        own = sorted(r.path for r in backend.query("defs", "my_own_thing").references)
        self.assertEqual(own, ["src/app.py"])
        for phantom in ("get", "vendored_only", "conda_thing", "pep582", "target_install"):
            refs = backend.query("defs", phantom).references
            self.assertEqual([r.path for r in refs], [],
                             "%s lives only inside an environment and was answered" % phantom)


class TheRuleIsStructureNotAName(unittest.TestCase):

    def test_a_venv_of_any_name_is_an_env(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        for name in ("venv", "env", "foo", "virtualenv"):
            _write(d, name + "/pyvenv.cfg")
            self.assertTrue(is_python_env(os.path.join(d, name)), name)

    def test_a_conda_env_is_an_env(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        os.makedirs(os.path.join(d, "envs", "conda-meta"))
        self.assertTrue(is_python_env(os.path.join(d, "envs")))

    def test_a_package_NAMED_env_without_a_marker_is_kept(self):
        """Pruning a user's own `env/` package would delete their code from their own graph."""
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        for n in ("env", "venv_tools", "src"):
            os.makedirs(os.path.join(d, n))
        kept = ["env", "venv_tools", "src"]
        prune_source_dirs(d, kept)
        self.assertEqual(kept, ["env", "venv_tools", "src"])

    def test_python_declares_its_install_dirs_in_the_language_table(self):
        self.assertEqual(languages.PYTHON.vendor_dirs, ("__pypackages__", "site-packages"))
        self.assertIn("site-packages", languages.VENDOR_DIRS)


class TheReviewWindowAgrees(unittest.TestCase):
    """`git ls-files --others` does not go through the walker, so it gets the same rule by path."""

    def test_under_python_env(self):
        root = _fixture()
        self.addCleanup(shutil.rmtree, root, True)
        self.assertTrue(under_python_env(root, "venv/lib/python3.12/site-packages/requests/api.py"))
        self.assertTrue(under_python_env(root, "env2/lib/x/c.py"))
        self.assertFalse(under_python_env(root, "env/settings.py"))
        self.assertFalse(under_python_env(root, "src/app.py"))
        self.assertFalse(under_python_env(root, "top.py"))

    @unittest.skipUnless(shutil.which("git"), "git not on PATH")
    def test_the_live_diff_does_not_list_an_unignored_venv(self):
        from mokata.ide import diffs
        root = _fixture()
        self.addCleanup(shutil.rmtree, root, True)
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                   GIT_COMMITTER_EMAIL="t@t")
        subprocess.run(["git", "init", "-q"], cwd=root, check=True, env=env)
        listed = sorted(f.path for f in diffs.live(root).files)
        for p in listed:
            self.assertFalse(p.startswith(("venv/", "py312/", "env2/")), p)
        self.assertIn("src/app.py", listed)
        self.assertIn("env/settings.py", listed)


class FreshnessIgnoresAnUnignoredVenv(unittest.TestCase):
    """0.0.23 verification M4: git's untracked list is filtered by the same marker rule, so an
    un-ignored venv does not look like tens of thousands of changed files on every query."""

    def test_untracked_files_inside_an_env_are_dropped(self):
        from mokata.knowledge.freshness import git_untracked
        root = _fixture()
        self.addCleanup(shutil.rmtree, root, True)
        listed = ["src/app.py", "venv/lib/python3.12/site-packages/requests/api.py",
                  "env2/lib/x/c.py", "env/settings.py", "py312/pyvenv.cfg"]
        out = "\0".join(listed) + "\0"
        got = git_untracked(root, run=lambda r, a: (0, out))
        self.assertEqual(sorted(got), ["env/settings.py", "src/app.py"])


if __name__ == "__main__":
    unittest.main()
