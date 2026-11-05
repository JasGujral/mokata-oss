"""0.0.27 P13 — what a recorded TEST ID names: one spelling of a test's identity, the Python AST
that locates it, and the content stamp that ties a recorded outcome to the test as it was.

A test is identified by its FULL id `path::Class::name` (pytest's node id, the parametrisation
`[…]` stripped). Every spelling of one file is one path: `\\` separators, `./`, `//` and `..`
segments are normalised here (`test_path_of`); the trace then keys the path on the spelling git
reports (case included). A dotted segment after the path (`path::TestX.test_a`) is a class
qualifier, never dropped.

`py_test_defs` reads a Python test file's AST — fresh from its text — and says, for every name
bound where a runner collects tests, how many times it is bound, whether a runner would COLLECT it
(and why not), and a content STAMP of the test as written: its own source with its decorators,
plus the non-test code of its scopes and the conftest.py files on its path (`stamp_of`). A proof
recorded under one stamp does not survive an edit that changes it (`@skip`, `assert 0`,
`__test__ = False`, a changed helper, a new autouse fixture).

`qualify_ids` turns a bare name into its full id ONLY from the test runner's own report of what it
ran (`runner_ids`), and a dotted unittest id only from the module it names — never a guess.

Light on purpose: the record path (hooks, the MCP gate, the CLI) imports it.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import ast
import hashlib
import os
import posixpath
import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

_PARAM = re.compile(r"\[.*\]$")
_SEG = re.compile(r"::|\.")
_SOURCE_EXT = (".py", ".ts", ".js", ".go")
#: a class-level / module-level assignment that changes what a runner collects
_COLLECT_NAMES = ("__test__", "pytestmark")


# ----------------------------------------------------------------------------------- the id itself

def test_param_of(test_id: str) -> str:
    """`test_x[a-b]` → `a-b`; "" when the id carries no parametrisation."""
    m = _PARAM.search(test_id.strip())
    return m.group(0)[1:-1] if m else ""


def _segments(test_id: str) -> Tuple[str, List[str]]:
    """`(raw path part or "", [qualifier…, name])` of an id, parametrisation stripped."""
    base = _PARAM.sub("", test_id.strip())
    head, sep, rest = base.partition("::")
    if sep and _looks_like_path(head):
        return head, [p for p in _SEG.split(rest) if p]
    return "", [p for p in re.split(r"::|\.|/|\\", base) if p]


def _looks_like_path(head: str) -> bool:
    return "/" in head or "\\" in head or head.endswith(_SOURCE_EXT)


def test_name_of(test_id: str) -> str:
    """`tests/test_x.py::TestA::test_y[case]` → `test_y`; `test_y` → `test_y`; a dotted id's last
    segment (`tests.test_x.TestA.test_y` → `test_y`)."""
    _p, segs = _segments(test_id)
    return segs[-1] if segs else _PARAM.sub("", test_id.strip())


def test_qualifier_of(test_id: str) -> str:
    """`tests/t.py::TestA::Inner::test_x[c]` (or `…::TestA.Inner.test_x`) → `TestA.Inner`; "" when
    the id names no class, or names no file."""
    p, segs = _segments(test_id)
    return ".".join(segs[:-1]) if p else ""


def test_key_of(test_id: str) -> str:
    """The QUALIFIED definition key an id names within its file: `TestA.test_x` / `test_y`."""
    q, n = test_qualifier_of(test_id), test_name_of(test_id)
    return f"{q}.{n}" if q else n


def normalise_path(head: str) -> str:
    """One spelling of a repo-relative path a person or a tool typed: `/` separators, `.` and
    empty segments dropped, `..` folded (`tests/../tests//./x.py` → `tests/x.py`). A path that
    climbs out of the repo or is absolute is returned as typed after the separator change — it
    then locates nothing."""
    from .repo_paths import as_name
    head = str(as_name(head.strip()))     # a test id crosses in: a CLI / MCP argument
    if head.startswith("/") or re.match(r"^[A-Za-z]:", head):
        return head
    norm = posixpath.normpath(head)
    return head if norm == ".." or norm.startswith("../") else norm


def test_path_of(test_id: str) -> str:
    """The file a test id names, normalised (`normalise_path`); "" for a bare / dotted id."""
    p, _segs = _segments(test_id)
    return normalise_path(p) if p else ""


def test_label_of(test_id: str, path: Optional[str] = None) -> str:
    """A test's IDENTITY — `path::Class::name` in one spelling (`path` given: the resolved file
    the trace located); a bare id is its own (non-proving) label."""
    path = test_path_of(test_id) if path is None else path
    if not path:
        return _PARAM.sub("", test_id.strip())
    return f"{path}::{test_key_of(test_id).replace('.', '::')}"


# ----------------------------------------------------------------------------- the AST locator

_COMPOUND = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith, ast.Try) + \
    ((ast.TryStar,) if hasattr(ast, "TryStar") else ()) + \
    ((ast.Match,) if hasattr(ast, "Match") else ())


def _bound_names(target: Any) -> List[str]:
    """The plain names an assignment / loop / with / del target binds (tuples unpacked)."""
    out: List[str] = []
    todo = [target]
    while todo:
        t = todo.pop()
        if isinstance(t, ast.Name):
            out.append(t.id)
        elif isinstance(t, (ast.Tuple, ast.List)):
            todo.extend(t.elts)
        elif isinstance(t, ast.Starred):
            todo.append(t.value)
    return out


def _is_false(node: Any) -> bool:
    return isinstance(node, ast.Constant) and node.value is False


def _deco_text(d: Any) -> str:
    return ast.unparse(d)


def _dump(node: Any) -> str:
    """A node's structure without positions — formatting, comments and blank lines never change
    it; a changed statement, decorator, docstring or helper does."""
    return ast.dump(node, include_attributes=False)


def _is_test_node(node: Any) -> bool:
    """A test def (`test*`) or a class holding tests — the parts of a scope that are OTHER tests,
    left out of a test's stamp so editing a sibling test never re-opens this one's proof."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return node.name.startswith("test")
    if isinstance(node, ast.ClassDef):
        return any(_is_test_node(n) for n in node.body)
    return False


def py_test_defs(text: Optional[str]) -> Optional[Dict[str, Any]]:
    """Every name a Python file BINDS where a test runner collects tests — the module body and
    class bodies (nested classes too), through compound statements (every arm), never inside a
    function — parsed fresh from `text` with `ast`. None when the text does not parse.

    `{"keys": {key: [line, …]}, "funcs": [(key, line, end)], "stars": {scope, …},
      "uncollected": {key: why}, "stamps": {key: sha256}}`:

      * a def, class, import, assignment, loop/with/except target and `del` each count as a
        binding — a name rebound anywhere in its scope is bound more than once, never unique;
      * `stars`: the scopes (`""` module, `TestA.`) a `from x import *` can rebind anything in;
      * `uncollected`: test functions a runner would NOT collect, with the reason — a class that
        is neither `Test*` nor derived, a class with `__init__`, `__test__ = False` (class or
        module), a `fixture` decorator, a def under `if __name__ …`;
      * `stamps`: each function's content stamp — the AST (no positions: formatting and comments
        never count) of the def with its decorators, plus every NON-test statement of its scopes:
        the module's helpers, imports, constants and `__test__` / `pytestmark`, and each enclosing
        class's header, `setUp`, helpers and marks. Sibling tests are left out.

    An explicit stack — a pathologically nested file never recurses this walk."""
    if text is None:
        return None
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return None
    # the code a test RUNS WITH besides its own def — the module's non-test statements (helpers,
    # imports, constants, `__test__` / `pytestmark`) — as AST, so formatting and comments never
    # count but a changed helper does (K4)
    module_shared = "\n".join(_dump(n) for n in tree.body if not _is_test_node(n))

    keys: Dict[str, List[int]] = {}
    funcs: List[Tuple[str, int, int]] = []
    stars: Set[str] = set()
    uncollected: Dict[str, str] = {}
    stamps: Dict[str, str] = {}
    module_off = any(isinstance(n, ast.Assign) and "__test__" in _bound_names(n.targets[0])
                     and _is_false(n.value) for n in tree.body)
    # (body, prefix, why-not-collected-or-"", headers so far)
    stack: List[Tuple[List[Any], str, str, Tuple[str, ...]]] = [
        (list(tree.body), "", "the module sets __test__ = False" if module_off else "",
         (module_shared,))]
    while stack:
        body, prefix, why, headers = stack.pop()
        for node in body:
            line = int(getattr(node, "lineno", 0) or 0)
            names: List[str] = []
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names = [node.name]
                key, end = prefix + node.name, int(node.end_lineno or line)
                funcs.append((key, line, end))
                stamps[key] = hashlib.sha256("\n".join(headers + (_dump(node),))
                                             .encode("utf-8")).hexdigest()
                fwhy = why
                if not fwhy and any("fixture" in _deco_text(d) for d in node.decorator_list):
                    fwhy = "a fixture, not a test"
                if fwhy:
                    uncollected[key] = fwhy
            elif isinstance(node, ast.ClassDef):
                names = [node.name]
                body_ = list(node.body)
                cwhy = why
                if not cwhy and not node.name.startswith("Test") and not node.bases:
                    cwhy = f"class {node.name} is neither Test* nor derived from a test case"
                if not cwhy and any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                                    and n.name == "__init__" for n in body_):
                    cwhy = f"class {node.name} has __init__"
                if not cwhy and any(isinstance(n, ast.Assign) and "__test__" in _bound_names(
                        n.targets[0]) and _is_false(n.value) for n in body_):
                    cwhy = f"class {node.name} sets __test__ = False"
                shared = _dump(ast.ClassDef(
                    name=node.name, bases=node.bases, keywords=node.keywords, body=[],
                    decorator_list=node.decorator_list,
                    **({"type_params": []} if "type_params" in ast.ClassDef._fields else {})
                    )) + "\n".join(
                    _dump(n) for n in body_ if not _is_test_node(n))
                stack.append((body_, f"{prefix}{node.name}.", cwhy, headers + (shared,)))
            elif isinstance(node, ast.Assign):
                names = [n for t in node.targets for n in _bound_names(t)]
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
                names = _bound_names(node.target)
            elif isinstance(node, ast.Delete):
                names = [n for t in node.targets for n in _bound_names(t)]
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for al in node.names:
                    if al.name == "*":
                        stars.add(prefix)
                    else:
                        names.append(al.asname or al.name.split(".")[0])
            if isinstance(node, _COMPOUND):
                inner = why
                if isinstance(node, ast.If) and "__name__" in _deco_text(node.test):
                    inner = why or "defined under `if __name__ …` — never collected"
                if isinstance(node, (ast.For, ast.AsyncFor)):
                    names += _bound_names(node.target)
                if isinstance(node, (ast.With, ast.AsyncWith)):
                    names += [n for it in node.items if it.optional_vars is not None
                              for n in _bound_names(it.optional_vars)]
                stack.append((list(node.body), prefix, inner, headers))
                for field in ("orelse", "finalbody"):
                    stack.append((list(getattr(node, field, None) or ()), prefix, why, headers))
                for h in getattr(node, "handlers", None) or ():
                    if getattr(h, "name", None):
                        names.append(h.name)
                    stack.append((list(h.body), prefix, why, headers))
                for c in getattr(node, "cases", None) or ():
                    stack.append((list(c.body), prefix, why, headers))
            for n in names:
                keys.setdefault(prefix + n, []).append(line)
    return {"keys": keys, "funcs": sorted(funcs), "stars": stars, "uncollected": uncollected,
            "stamps": stamps}


def file_stamp(text: str) -> str:
    """The content stamp of a non-Python test file — the whole file (no finer AST here)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def conftests_of(rel: str) -> List[str]:
    """The conftest.py files pytest loads for a test file: one per directory from the root down
    to the file's own (`a/b/test_x.py` → conftest.py, `a/conftest.py`, `a/b/conftest.py`)."""
    parts = rel.split("/")[:-1]
    return ["/".join(parts[:i] + ["conftest.py"]) for i in range(len(parts) + 1)]


def _conftest_part(text: Optional[str]) -> str:
    if text is None:
        return "-"
    try:
        return _dump(ast.parse(text))
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return file_stamp(text)


def stamp_of(rel: str, text: Optional[str], key: str,
             read: Optional[Callable[[str], Optional[str]]] = None,
             defs: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """The stamp of `key` in file `rel` as `text` holds it now — the test's own AST, its scopes'
    non-test code, and (`read` given: how to read a file of the same tree) every conftest.py
    on its path (K4: an autouse fixture added there changes what the test runs). None when it
    cannot be named. OUT OF REACH, disclosed: code the test imports from other modules, plugins,
    pytest/ini options, and the code under test."""
    if text is None:
        return None
    if rel.endswith(".py"):
        defs = py_test_defs(text) if defs is None else defs
        own = None if defs is None else defs["stamps"].get(key)
    else:
        own = file_stamp(text)
    if own is None or read is None:
        return own
    parts = [own] + [_conftest_part(read(c)) for c in conftests_of(rel)]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def fs_folds_case(root: str) -> bool:
    """Whether THIS filesystem folds case — probed, never read from git's `core.ignorecase`
    (K3: a repo can say false on a volume that folds): the repo's own `.git` stat-ed under a
    flipped case."""
    real = os.path.join(root, ".git")
    if not os.path.exists(real):
        return False
    try:
        return os.path.exists(os.path.join(root, ".GIT"))
    except OSError:
        return False


# ------------------------------------------------------------------ write-time qualification

def _read(root: str, rel: str) -> Optional[str]:
    try:
        with open(os.path.join(root, *rel.split("/")), encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def tracked_ending(root: str, rel: str) -> List[str]:
    """The tracked files whose path ENDS in `/<rel>` — a rootdir-relative path's candidates, from
    ONE pathspec query of git's index (no filesystem walk). [] without git, or for a path that
    carries glob characters (it cannot be asked literally)."""
    import subprocess
    from .quiet_spawn import hidden
    if not rel or any(c in rel for c in "*?[]") or rel.startswith("/"):
        return []
    try:
        p = subprocess.run(["git", "-C", root, "ls-files", "-z", "--", f":(glob)**/{rel}"],
                           capture_output=True, stdin=subprocess.DEVNULL, **hidden())
    except OSError:
        return []
    if p.returncode != 0:
        return []
    return [x for x in p.stdout.decode("utf-8", "replace").split("\0") if x]


def _module_hits(root: str, dotted: str, parsed: Dict[str, Optional[Dict[str, Any]]]) -> Set[str]:
    """The full ids a dotted unittest id names: `pkg.test_mod.TestA.test_x` → `pkg/test_mod.py`
    (from the repo root, where `python -m unittest` runs) binding `TestA.test_x` once and
    collecting it. The id NAMES its module; nothing is searched for."""
    segs = [x for x in dotted.split(".") if x]
    hits: Set[str] = set()
    for i in range(1, len(segs)):
        mod = "/".join(segs[:i]) + ".py"
        if mod not in parsed:
            parsed[mod] = py_test_defs(_read(root, mod))
        d = parsed[mod]
        key = ".".join(segs[i:])
        if d and len(d["keys"].get(key, [])) == 1 and key not in d["uncollected"]:
            hits.add(f"{mod}::{key.replace('.', '::')}")
    return hits


def qualify_ids(root: str, ids: Iterable[str], reported: Iterable[str] = ()) -> Dict[str, str]:
    """`{recorded id: full id}` — K1 (coordinator ruling): an id is qualified ONLY from what names
    it outright, never from a guess about which file the runner looked in:

      * a BARE name: from the test runner's own report of what it ran (`reported`: pytest node
        ids, `runner_ids`) — exactly one reported test of that name;
      * a DOTTED unittest id: from the module it names (`_module_hits`) — exactly one split.

    An id with no such answer, or with several, keeps its spelling and never proves."""
    parsed: Dict[str, Optional[Dict[str, Any]]] = {}
    labels: Set[str] = set()
    for r in reported:
        if not isinstance(r, str) or not r.strip():
            continue
        if test_path_of(r):
            labels.add(test_label_of(r))
        else:
            got = _module_hits(root, _PARAM.sub("", r.strip()), parsed)
            labels |= got if len(got) == 1 else set()
    out: Dict[str, str] = {}
    for tid in ids:
        if not isinstance(tid, str) or test_path_of(tid) or not tid.strip():
            continue
        base = _PARAM.sub("", tid.strip())
        param = tid.strip()[len(base):]
        if "." in base:
            hits = _module_hits(root, base, parsed)
        else:
            hits = {lb for lb in labels if test_name_of(lb) == base}
        if len(hits) == 1:
            out[tid] = next(iter(hits)) + param
    return out


_NODE = re.compile(r"(?<![\w./\\-])([\w./\\-]+\.py(?:::[^\s:]+)+)")


def runner_ids(text: str) -> List[str]:
    """The test ids a runner REPORTED, read from its own output: pytest's `-rA` / `-v` /
    `--collect-only -q` text (every `path.py::…` node id in it), or a JUnit XML report (each
    `<testcase>`: its `file` + `classname` + `name`, or the dotted `classname.name`). A report
    that declares a DOCTYPE or entities is refused (nothing is read from it)."""
    t = text.lstrip()
    if not t.startswith("<"):
        return sorted({m.group(1) for m in _NODE.finditer(text)})
    if "<!DOCTYPE" in t or "<!ENTITY" in t:
        return []
    import xml.etree.ElementTree as ET   # noqa: S405 — no DOCTYPE / entities (refused above)
    try:
        tree = ET.fromstring(t)  # noqa: S314
    except ET.ParseError:
        return []
    out: Set[str] = set()
    for tc in tree.iter("testcase"):
        name, cls, fil = tc.get("name") or "", tc.get("classname") or "", tc.get("file") or ""
        if not name:
            continue
        if fil and fil.endswith(".py"):
            path = normalise_path(fil)
            mod = path[:-3].replace("/", ".")
            quals = cls[len(mod):].strip(".") if cls.startswith(mod) else ""
            out.add(f"{path}::" + "::".join([q for q in quals.split(".") if q] + [name]))
        elif cls:
            out.add(f"{cls}.{name}")
    return sorted(out)


__all__ = ["test_param_of", "test_name_of", "test_qualifier_of", "test_key_of", "test_path_of",
           "test_label_of", "normalise_path", "py_test_defs", "file_stamp", "stamp_of",
           "qualify_ids", "tracked_ending", "runner_ids", "conftests_of", "fs_folds_case"]
