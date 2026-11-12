"""The IDE server never makes two request threads import a package against each other (0.0.28 D).

The cause, named from the Linux Firefox full-suite red (probe run 42625339403, job 129187517555):
`GET /api/sessions` answered 500 with `_frozen_importlib._DeadlockError: deadlock detected by
_ModuleLock('mokata.events')`. Every IDE handler runs on a worker thread (`asyncio.to_thread`), and
many of them import lazily, inside the function. CPython's importer takes a module's lock BEFORE it
imports that module's parent package, so:

  thread A  `from ..events import query`          holds `mokata.events`, whose `__init__` now runs
                                                  `from .schema import ...` -> waits on `.schema`
  thread B  `from ..events.schema import ...`     holds `mokata.events.schema`, imports its parent
                                                  first -> waits on `mokata.events`

That is a real lock cycle, and the importer breaks it by raising in one thread - a 500 on whichever
request lost. It needs the PARENT package not yet imported and an `__init__` that imports its own
submodules, so it only bites on the first requests after start, and only when two land together:
which is the full suite on a loaded runner, and never one file alone.

The contract: by the time the server module is imported - before it can take a request - every
package the IDE imports lazily AND whose `__init__` imports its own submodules is already imported.
Derived from the source, so a new lazy import of such a package reds here instead of on a runner.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import ast
import json
import pathlib
import subprocess
import sys
import unittest

import _support  # noqa: F401  (the provisioning preflight every module runs)

SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "mokata"


def _module_of(path):
    rel = path.relative_to(SRC.parent).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return parts


def _resolve(path, node):
    """The absolute module a relative `from` import names, as parts."""
    here = _module_of(path)
    if path.name != "__init__.py":
        here = here[:-1]                                   # the module's package
    base = here[: len(here) - (node.level - 1)] if node.level > 1 else here
    return base + (node.module.split(".") if node.module else [])


def _init_imports_own_submodule(pkg_parts):
    init = SRC.parent.joinpath(*pkg_parts, "__init__.py")
    if not init.is_file():
        return False
    def runs_at_import(body):                              # module level, through try/if - never
        for stmt in body:                                  # a function, a class or TYPE_CHECKING
            if isinstance(stmt, ast.ImportFrom) and stmt.level == 1:
                return True
            if isinstance(stmt, ast.If):
                if isinstance(stmt.test, ast.Name) and stmt.test.id == "TYPE_CHECKING":
                    continue
                if runs_at_import(stmt.body) or runs_at_import(stmt.orelse):
                    return True
            if isinstance(stmt, ast.Try):
                for part in (stmt.body, stmt.orelse, stmt.finalbody, *[h.body for h in stmt.handlers]):
                    if runs_at_import(part):
                        return True
        return False
    return runs_at_import(ast.parse(init.read_text(encoding="utf-8")).body)


def lazily_imported_packages_at_risk():
    """{'mokata.x', ...}: packages an IDE function imports (itself or a submodule) whose `__init__`
    imports its own submodules - the two conditions of the lock cycle."""
    risky = set()
    for path in sorted((SRC / "ide").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if not (isinstance(node, ast.ImportFrom) and node.level):
                    continue
                target = _resolve(path, node)
                for i in range(2, len(target) + 1):        # every package on the way down
                    pkg = target[:i]
                    if pkg[:2] == ["mokata", "ide"]:
                        continue
                    if SRC.parent.joinpath(*pkg).is_dir() and _init_imports_own_submodule(pkg):
                        risky.add(".".join(pkg))
    return risky


class TheServerImportsThemBeforeItCanServe(unittest.TestCase):

    def test_the_derivation_finds_the_package_that_failed(self):
        """The finder is not vacuous: it names `mokata.events`, the package in the runner's traceback."""
        self.assertIn("mokata.events", lazily_imported_packages_at_risk())

    def test_every_package_at_risk_is_imported_with_the_server(self):
        risky = sorted(lazily_imported_packages_at_risk())
        code = ("import json, sys; import mokata.ide.server; "
                "print(json.dumps([m for m in %r if m not in sys.modules]))" % (risky,))
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
        self.assertEqual(0, out.returncode, out.stderr)
        missing = json.loads(out.stdout.strip().splitlines()[-1])
        self.assertEqual([], missing,
                         "a request thread would import these packages for the first time, against "
                         "another thread importing one of their submodules: %s" % missing)


if __name__ == "__main__":
    unittest.main()
