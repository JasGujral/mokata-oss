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


def _module_of(path, src=SRC):
    rel = path.relative_to(src.parent).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return parts


def _resolve(path, node, src=SRC):
    """The absolute module a `from` import names, as parts (relative or absolute)."""
    if not node.level:
        return (node.module or "").split(".")
    here = _module_of(path, src)
    if path.name != "__init__.py":
        here = here[:-1]                                   # the module's package
    base = here[: len(here) - (node.level - 1)] if node.level > 1 else here
    return base + (node.module.split(".") if node.module else [])


def _file_of(parts, src=SRC):
    """The source file of a module, or None when it is not one of mokata's own."""
    if not parts or parts[0] != src.name:
        return None
    d = src.parent.joinpath(*parts)
    if (d / "__init__.py").is_file():
        return d / "__init__.py"
    f = d.with_suffix(".py")
    return f if f.is_file() else None


def _imports_in(path, nodes, src=SRC):
    """The mokata modules `nodes` import, as parts: `import mokata.x.y`, `from mokata.x import y`
    and relative `from .x import y` alike; `from pkg import name` also names `pkg.name` when that
    is a module."""
    out = []
    for node in nodes:
        if isinstance(node, ast.Import):
            out += [a.name.split(".") for a in node.names if a.name.split(".")[0] == src.name]
        elif isinstance(node, ast.ImportFrom):
            target = _resolve(path, node, src)
            if not target or target[0] != src.name:
                continue
            out.append(target)
            out += [target + [a.name] for a in node.names if _file_of(target + [a.name], src)]
    return out


def _at_import(body):
    """The import statements a module runs when it is imported: module level, through try/if —
    never a function, a class body or TYPE_CHECKING."""
    for stmt in body:
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            yield stmt
        elif isinstance(stmt, ast.If):
            if isinstance(stmt.test, ast.Name) and stmt.test.id == "TYPE_CHECKING":
                continue
            yield from _at_import(stmt.body)
            yield from _at_import(stmt.orelse)
        elif isinstance(stmt, ast.Try):
            for part in (stmt.body, stmt.orelse, stmt.finalbody, *[h.body for h in stmt.handlers]):
                yield from _at_import(part)


def _module_imports(parts, src=SRC):
    """The modules `parts` imports when it is imported (its parent packages included)."""
    f = _file_of(parts, src)
    if f is None:
        return []
    return _imports_in(f, _at_import(ast.parse(f.read_text(encoding="utf-8")).body), src)


def _with_parents(parts):
    return [parts[:i] for i in range(2, len(parts) + 1)]


def _closure(starts, src=SRC):
    """Every mokata module imported when `starts` are: their parent packages and everything they
    import at import time, transitively."""
    seen, todo = set(), [p for s_ in starts for p in _with_parents(s_)]
    while todo:
        m = todo.pop()
        key = ".".join(m)
        if key in seen or _file_of(m, src) is None:
            continue
        seen.add(key)
        for t in _module_imports(m, src):
            todo += _with_parents(t)
    return seen


def lazily_imported_packages_at_risk(src=SRC):
    """{'mokata.x', ...}: the packages a lazy import in an IDE function reaches (itself, its
    parents, and everything they import at import time) whose `__init__` imports a module that
    imports back into the package — its own submodule, or a sibling package's module that needs
    it (0.0.29 E: absolute `mokata.*` imports and sibling cycles, not only `from .x`)."""
    lazy = []
    for path in sorted((src / "ide").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                lazy += _imports_in(path, ast.walk(fn), src)
    reached = _closure([t for t in lazy if t[:2] != [src.name, "ide"]], src)
    risky = set()
    for pkg in sorted(reached):
        parts = pkg.split(".")
        if parts[:2] == [src.name, "ide"] or not src.parent.joinpath(*parts, "__init__.py").is_file():
            continue
        for t in _module_imports(parts, src):
            back = _closure([t], src) | {".".join(t)}
            if any(m == pkg or m.startswith(pkg + ".") for m in back):
                risky.add(pkg)
                break
    return risky


def _plant(files):
    """A throwaway `mokata` source tree: {relpath: text}; returns its `mokata` dir."""
    import tempfile
    d = pathlib.Path(tempfile.mkdtemp(prefix="lock-inv-")) / "mokata"
    for rel, text in {"__init__.py": "", "ide/__init__.py": "", **files}.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return d


class TheFinderConvicts(unittest.TestCase):
    """0.0.29 E (`IDE-UI-PIN-GAPS` import-lock finder): the shapes the 0.0.28 finder could not see."""

    def test_an_absolute_lazy_import_of_a_package_that_imports_its_own_submodule(self):
        src = _plant({"ide/h.py": "def handler():\n    import mokata.alpha.thing\n",
                      "alpha/__init__.py": "from mokata.alpha import thing\n",
                      "alpha/thing.py": "X = 1\n"})
        self.assertEqual({"mokata.alpha"}, lazily_imported_packages_at_risk(src))

    def test_a_sibling_package_cycle(self):
        src = _plant({"ide/h.py": "def handler():\n    from ..beta import util\n",
                      "beta/__init__.py": "from ..gamma.g import G\n",
                      "beta/util.py": "U = 1\n",
                      "gamma/__init__.py": "",
                      "gamma/g.py": "from mokata.beta.util import U\nG = U\n"})
        self.assertIn("mokata.beta", lazily_imported_packages_at_risk(src))

    def test_a_package_with_no_cycle_is_not_convicted(self):
        src = _plant({"ide/h.py": "def handler():\n    from ..delta import d\n",
                      "delta/__init__.py": "from ..eps import e\n",
                      "delta/d.py": "D = 1\n",
                      "eps/__init__.py": "", "eps/e.py": "E = 1\n",
                      "zeta/__init__.py": "from . import z\n", "zeta/z.py": ""})
        self.assertEqual(set(), lazily_imported_packages_at_risk(src))


#: At-risk packages the server does not yet import, each with the row that owns the fix. The
#: real-tree check reds in BOTH directions: a new one appears, or one here is fixed and stays listed.
KNOWN_OPEN = {
    # firstrun._init_preview -> mokata.cli -> cli_commands._common -> mokata.execmode, whose
    # __init__ imports its own submodules. Found by the 0.0.29 E closure; the fix is one import in
    # ide/server.py, which is outside lane E's files.
    "mokata.execmode": "IDE-IMPORT-LOCK-EXECMODE (doc 84)",
}


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
        self.assertEqual(sorted(KNOWN_OPEN), missing,
                         "a request thread would import these packages for the first time, against "
                         "another thread importing one of their submodules: %s" % missing)


if __name__ == "__main__":
    unittest.main()
