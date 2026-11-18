"""The one-door sweep (0.0.29 H3; round 2 = a CLASS rule): every string or path-part comparison of a
path-derived value in a `{relpath: source}` corpus, outside the door (`fs_identity.py`).

Round 1 matched SHAPES (`x.startswith(rp(b) + os.sep)`, ...) and a verifier planted 38 variants of which 21
slipped past (lambdas, closures, f-string/%/format prefixes, slices, `parts`, `in`, `find`, `re.match`,
`__contains__`, `os.pardir`, a parameter comparator). Round 2 asks ONE question of every comparison instead:
does a PATH-TAINTED value take part in it? Whole classes are caught, not a list of shapes.

TAINT — a value is path-tainted when it is (transitively, through ANY expression) made from:
  * a path function: `realpath abspath normcase normpath expanduser relpath commonpath commonprefix
    dirname join fspath getcwd name_of` (by attribute, by name, by `from os.path import x as y`, by a
    name bound to one — `rp = os.path.realpath`, `g = getattr(os.path, "realpath")`), a pathlib
    constructor (`Path PurePath PosixPath WindowsPath PurePosixPath PureWindowsPath`) or `.resolve()` /
    `.absolute()`;
  * `os.sep`, `os.altsep`, `os.pardir`, `os.curdir` (of `os`, `os.path`, `posixpath`, `ntpath`), or
    `"/".join(...)` / `"\\\\".join(...)`;
  * an expression with a tainted part: `+ % *`, an f-string, `.format(...)`, a subscript or slice, a
    method of a tainted receiver (`split`, `lower`, `rstrip`, `parts`, `parents`, ...), a tuple/list/set
    display or comprehension of tainted elements, a conditional or boolean of tainted values, a call of
    `str list tuple set frozenset sorted reversed re.escape` with a tainted argument;
  * a name assigned, augmented, walrus-bound, `for`-bound or comprehension-bound from a tainted value — in
    its own scope, and visible in every NESTED scope (closures, lambdas, class bodies, module globals).

FINDINGS — `(file, line, scope, idiom)`; `scope` is the qualified function (`f.<lambda>`, `C.m`,
`<module>`):
  equality     `==` / `!=` with a tainted side and a NON-literal other side
  membership   `in` / `not in` with a tainted side and a non-literal other side (`parents` when the
               container is `.parents`)
  order        `< <= > >=` with a tainted side and a non-literal other side
  dotdot       a tainted value compared with — or `startswith`/`in`/`==` — a literal holding `..`
  startswith   `startswith` / `endswith` with a tainted receiver or argument and a non-literal argument
  substring    `find rfind index rindex count __contains__ __eq__ __ne__ removeprefix removesuffix
               partition rpartition` likewise
  regex        `re.match / search / fullmatch` (or `re.compile(..).match`, ...) with a tainted argument
  commonpath   a call of `commonpath` / `commonprefix`
  relative_to  `.relative_to(` / `.is_relative_to(`
  key          `normcase(realpath(...))`: a spelling used as the identity of a directory
  name_of      `escapes_root(` in a scope that also calls `name_of(`
  helper       a function named `_within _inside _is_within _under _identity_under _is_sub _is_under
               _contains _same_path _same_dir`
  slice        `x[:k] == y` / `!=` with a non-literal `k` and `y`: a prefix test, flagged wherever the Compare
               sits (an `if`, an `and`/`or` chain, a ternary, an assignment, a return), with or without taint
  comparator   a function or lambda whose result IS a comparison (any of the above shapes, or a plain
               `a == b`) of two non-literal values read only from its PARAMETERS — the shape a path
               equality takes when its arguments carry the taint from the CALLER (`def f(a, b): return
               a == b`). No caller is needed: the comparator itself is the finding.

Not findings: a comparison with a literal that holds no `..` (`rel.startswith("/")` asks whether a NAME is
absolute; `basename(p) == ".git"` is the NAME rule), an `is` test, and the root test of a walk up
(`dirname(cur) == cur`). Everything else that compares a path-tainted value is a finding, and the allowlist
(test_c2_containment `_ALLOW`) must name each with a reason and an exact count. Stdlib only.
"""
from __future__ import annotations

import ast
from typing import Dict, Iterable, List, Optional, Set, Tuple

PATH_FUNCS = frozenset({"realpath", "abspath", "normcase", "normpath", "expanduser", "relpath", "commonpath",
                        "commonprefix", "dirname", "join", "fspath", "getcwd", "name_of", "gettempdir",
                        "cwd", "home"})
#: Calls that hand back paths FROM THE FILESYSTEM (round 3, ruling M-r2-2): a root taken from an untrusted
#: path and walked. `os.walk` taints only the ROOT of each triple (the dir/file NAME lists are names).
TREE_FUNCS = frozenset({"walk", "fwalk", "scandir", "iterdir", "glob", "iglob", "rglob"})
#: Glob-pattern tests (round 3): a pattern applied to a path.
PATTERN_FUNCS = frozenset({"fnmatch", "fnmatchcase"})
#: `fnmatch.filter(names, pat)` (round 3 close-out): the pattern applied to a LIST of paths. Told apart from
#: the builtin `filter` by what the receiver / imported name IS, recorded in the module's aliases.
FNMATCH_MODULE, FNMATCH_FILTER = "<fnmatch module>", "<fnmatch.filter>"
PATHLIB = frozenset({"Path", "PurePath", "PosixPath", "WindowsPath", "PurePosixPath", "PureWindowsPath"})
PATH_METHODS = frozenset({"resolve", "absolute"})
SEP_ATTRS = frozenset({"sep", "altsep", "pardir", "curdir"})
#: Attributes of a tainted value that are still a path (or its parts). NOT `name`/`stem`/`suffix`: one
#: component is a NAME, and a name compared with names is the name rule, not containment.
PATH_ATTRS = frozenset({"parts", "parents", "parent", "anchor", "drive", "root", "path"})
#: Methods of a tainted value whose result is still the path's text (or its pieces). NOT a read of the
#: file's CONTENT (`read_text`), a predicate (`exists`, `startswith` — those are sinks) or a stat.
KEEP_METHODS = frozenset({"split", "rsplit", "lower", "upper", "casefold", "strip", "rstrip", "lstrip",
                          "replace", "as_posix", "with_name", "with_suffix", "with_stem", "joinpath",
                          "expanduser", "resolve", "absolute", "format", "encode", "decode", "partition",
                          "rpartition", "removeprefix", "removesuffix", "__fspath__", "relative_to",
                          "translate", "zfill", "ljust", "rjust", "center", "title", "swapcase", "normalize"})
PATH_MODULES = frozenset({"os", "path", "posixpath", "ntpath"})
KEEPS = frozenset({"str", "list", "tuple", "set", "frozenset", "sorted", "reversed", "escape", "format",
                   "map", "filter", "fsencode", "fsdecode"})
STR_SINKS = frozenset({"find", "rfind", "index", "rindex", "count", "__contains__", "__eq__", "__ne__",
                       "removeprefix", "removesuffix", "partition", "rpartition"})
PREFIX_SINKS = frozenset({"startswith", "endswith"})
REGEX = frozenset({"match", "search", "fullmatch", "sub", "subn", "split", "findall", "finditer"})
HELPERS = frozenset({"_within", "_inside", "_is_within", "_under", "_identity_under", "_is_sub", "_is_under",
                     "_contains", "_same_path", "_same_dir"})
DOOR = "fs_identity.py"
_SEPS = frozenset({"/", "\\"})
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _is_literal(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return all(_is_literal(e) for e in node.elts)
    return False


def _sep_attr(node: ast.AST) -> Optional[str]:
    """`os.sep` / `os.path.pardir` / ... -> its attribute name, else None."""
    if isinstance(node, ast.Attribute) and node.attr in SEP_ATTRS and isinstance(node.value, (ast.Name, ast.Attribute)) \
            and (getattr(node.value, "id", None) in PATH_MODULES or getattr(node.value, "attr", None) == "path"):
        return node.attr
    return None


def _is_const(node: ast.AST) -> bool:
    """A literal, or a path TOKEN (`os.sep`, `os.pardir`, ...), or a display / concatenation of them: the
    other side of a NAME check (`os.sep in name`, `rel in (os.curdir, os.pardir)`), not a second path."""
    if _is_literal(node) or _sep_attr(node):
        return True
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return all(_is_const(e) for e in node.elts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return all(_is_const(x) or (isinstance(x, ast.Name) and x.id.isupper()) for x in (node.left, node.right)) \
            and (_is_const(node.left) or _is_const(node.right))   # `os.pardir + NAME_SEP`: a module CONSTANT
    return False


def _literal_strs(node: ast.AST) -> List[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if _sep_attr(node) == "pardir":
        return [".."]
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return [s for e in node.elts for s in _literal_strs(e)]
    if isinstance(node, ast.BinOp):
        return _literal_strs(node.left) + _literal_strs(node.right)
    return []


def _abs_literal(node: ast.AST) -> bool:
    """A literal that IS an absolute path beyond the bare root (`/etc/passwd`, `C:\\x`): a path compared
    with it is a containment / equality decision about a fixed place (round 3: `os.walk` root == '/x')."""
    import re as _re
    return any((len(s) > 1 and s[0] == "/" and s.strip("/")) or _re.match(r"^[A-Za-z]:[\\/].", s)
               for s in _literal_strs(node))


def _regex_receiver(scope: "_Scope", v: ast.AST) -> Optional[List[ast.AST]]:
    """The args of the `re.compile(...)` that `v` is (directly, or a name bound to one in this or an outer
    scope); `[]` when `v` IS the `re` module; None when `v` is neither."""
    if isinstance(v, ast.Name) and v.id in scope.re_names:
        return []
    s: Optional["_Scope"] = scope
    while isinstance(v, ast.Name) and s is not None:
        if v.id in s.values:
            v = s.values[v.id]
            break
        s = s.parent
    if isinstance(v, ast.Call) and _call_name(v, scope.aliases) == "compile":
        return list(v.args)
    return None


def _dotdot(node: ast.AST) -> bool:
    return any(".." in s for s in _literal_strs(node))


def _func_aliases(tree: ast.AST) -> Dict[str, str]:
    """Every name bound to a path function anywhere in the module: `from os.path import realpath as rp`,
    `rp = os.path.realpath`, `g = getattr(os.path, "realpath")`, `from pathlib import Path as P`."""
    out: Dict[str, str] = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name == "fnmatch":
                    out[a.asname or a.name] = FNMATCH_MODULE
        elif isinstance(n, ast.ImportFrom):
            for a in n.names:
                if a.name in PATH_FUNCS | PATHLIB | KEEPS | PATTERN_FUNCS | TREE_FUNCS:
                    out[a.asname or a.name] = a.name
                elif n.module == "fnmatch" and a.name == "filter":
                    out[a.asname or a.name] = FNMATCH_FILTER
        elif isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            v, name = n.value, None
            if isinstance(v, ast.Attribute) and v.attr in PATH_FUNCS | PATHLIB:
                name = v.attr
            elif isinstance(v, ast.Name) and v.id in PATH_FUNCS | PATHLIB:
                name = v.id
            elif isinstance(v, ast.Call) and isinstance(v.func, ast.Name) and v.func.id == "getattr" \
                    and len(v.args) >= 2 and isinstance(v.args[1], ast.Constant) \
                    and v.args[1].value in PATH_FUNCS | PATHLIB:
                name = v.args[1].value
            if name:
                out[n.targets[0].id] = name
    return out


def _call_name(call: ast.Call, aliases: Dict[str, str]) -> str:
    f = call.func
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return aliases.get(f.id, f.id)
    return ""


def _module_aliases(tree: ast.AST) -> Set[str]:
    """Names that ARE `re` (`import re`, `import re as r`)."""
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name == "re":
                    out.add(a.asname or "re")
    return out


class _Scope:
    def __init__(self, node: ast.AST, name: str, parent: Optional["_Scope"], aliases: Dict[str, str],
                 re_names: Set[str]):
        self.node, self.name, self.parent = node, name, parent
        self.aliases, self.re_names = aliases, re_names
        self.tainted: Set[str] = set()
        self.values: Dict[str, ast.AST] = {}
        self.params: Set[str] = set()
        args = getattr(node, "args", None)
        if isinstance(args, ast.arguments):
            for a in args.posonlyargs + args.args + args.kwonlyargs:
                self.params.add(a.arg)
            for a in (args.vararg, args.kwarg):
                if a is not None:
                    self.params.add(a.arg)

    def is_tainted_name(self, name: str) -> bool:
        s: Optional[_Scope] = self
        while s is not None:
            if name in s.tainted:
                return True
            if name in s.params and s is not self:
                return False                     # shadowed by an outer parameter: not a closure taint
            s = s.parent
        return False

    def taint(self, e: Optional[ast.AST]) -> bool:
        """Is `e` path-tainted?"""
        if e is None:
            return False
        if isinstance(e, ast.Name):
            return self.is_tainted_name(e.id)
        if isinstance(e, ast.Attribute):
            if e.attr in SEP_ATTRS and isinstance(e.value, (ast.Name, ast.Attribute)) and \
                    (getattr(e.value, "id", None) in PATH_MODULES or getattr(e.value, "attr", None) == "path"):
                return True
            return e.attr in PATH_ATTRS and self.taint(e.value)
        if isinstance(e, ast.Call):
            name = _call_name(e, self.aliases)
            f = e.func
            if name in PATH_FUNCS | PATHLIB:
                if name == "join" and isinstance(f, ast.Attribute) and isinstance(f.value, ast.Constant):
                    return f.value.value in _SEPS or any(self.taint(a) for a in e.args)
                if name in ("dirname", "join") and isinstance(f, ast.Attribute) and \
                        not self._path_module(f.value) and not self.taint(f.value):
                    return any(self.taint(a) for a in e.args)
                return True
            if name in TREE_FUNCS:
                return True                       # a path handed back by a walk of the filesystem
            if isinstance(f, ast.Attribute) and f.attr in PATH_METHODS and not e.args:
                return True                       # pathlib's `.resolve()` / `.absolute()` (no positional)
            if isinstance(f, ast.Attribute) and f.attr in KEEP_METHODS and self.taint(f.value):
                return True                       # a path-preserving method of a tainted value
            if name in KEEPS and any(self.taint(a) for a in e.args):
                return True
            if isinstance(f, ast.Attribute) and f.attr == "format":
                return any(self.taint(a) for a in e.args) or any(self.taint(k.value) for k in e.keywords)
            return False
        if isinstance(e, ast.BinOp):
            return self.taint(e.left) or self.taint(e.right)
        if isinstance(e, ast.JoinedStr):
            return any(self.taint(v) for v in e.values)
        if isinstance(e, ast.FormattedValue):
            return self.taint(e.value)
        if isinstance(e, ast.Subscript):
            return self.taint(e.value)
        if isinstance(e, ast.IfExp):
            return self.taint(e.body) or self.taint(e.orelse)
        if isinstance(e, ast.BoolOp):
            return any(self.taint(v) for v in e.values)
        if isinstance(e, (ast.Tuple, ast.List, ast.Set)):
            return any(self.taint(x) for x in e.elts)
        if isinstance(e, ast.Starred):
            return self.taint(e.value)
        if isinstance(e, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
            return self.taint(e.elt)
        if isinstance(e, ast.NamedExpr):
            return self.taint(e.value)
        return False

    def _path_module(self, v: ast.AST) -> bool:
        return (isinstance(v, ast.Name) and v.id in PATH_MODULES) or \
            (isinstance(v, ast.Attribute) and v.attr == "path")

    def dirname_of(self, x: ast.AST, y: ast.AST) -> bool:
        """`x` is `dirname(y)` (directly or through a name assigned so): the root test of a walk up."""
        if isinstance(x, ast.Name) and x.id in self.values:
            x = self.values[x.id]
        return isinstance(x, ast.Call) and _call_name(x, self.aliases) == "dirname" and bool(x.args) \
            and ast.dump(x.args[0]) == ast.dump(y)


def _targets(t: ast.AST) -> List[str]:
    if isinstance(t, ast.Name):
        return [t.id]
    if isinstance(t, (ast.Tuple, ast.List)):
        return [x for e in t.elts for x in _targets(e)]
    if isinstance(t, ast.Starred):
        return _targets(t.value)
    return []


def _own_nodes(scope: ast.AST) -> List[ast.AST]:
    """Every node of `scope` not inside a nested scope (def, lambda, class: those are scopes of their own).
    A lambda's body and a function's DEFAULTS/decorators belong to the enclosing scope's evaluation only for
    defaults; bodies are walked as their own scope."""
    out: List[ast.AST] = []
    if isinstance(scope, ast.Lambda):
        stack = [scope.body]
    elif isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        stack = list(scope.body)
    else:
        stack = list(ast.iter_child_nodes(scope))
    while stack:
        n = stack.pop()
        out.append(n)
        if isinstance(n, _SCOPES):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                stack.extend(d for d in n.args.defaults + n.args.kw_defaults if d is not None)
            continue
        stack.extend(ast.iter_child_nodes(n))
    return out


def _bind(scope: _Scope, nodes: Iterable[ast.AST]) -> None:
    pairs: List[Tuple[List[str], ast.AST]] = []
    for n in nodes:
        if isinstance(n, ast.Assign):
            if len(n.targets) == 1 and isinstance(n.targets[0], (ast.Tuple, ast.List)) and \
                    isinstance(n.value, (ast.Tuple, ast.List)) and len(n.value.elts) == len(n.targets[0].elts):
                for t, v in zip(n.targets[0].elts, n.value.elts):      # `a, c = x, y`: element by element
                    pairs.append((_targets(t), v))
            else:
                pairs.append(([x for t in n.targets for x in _targets(t)], n.value))
        elif isinstance(n, (ast.AnnAssign, ast.AugAssign)) and n.value is not None:
            pairs.append((_targets(n.target), n.value))
        elif isinstance(n, ast.NamedExpr):
            pairs.append((_targets(n.target), n.value))
        elif isinstance(n, (ast.For, ast.AsyncFor, ast.comprehension)):
            if isinstance(n.iter, ast.Call) and _call_name(n.iter, scope.aliases) in ("walk", "fwalk") and \
                    isinstance(n.target, (ast.Tuple, ast.List)) and n.target.elts:
                pairs.append((_targets(n.target.elts[0]), n.iter))     # the walk's ROOT, not its name lists
            else:
                pairs.append((_targets(n.target), n.iter))
        elif isinstance(n, ast.withitem) and n.optional_vars is not None:
            pairs.append((_targets(n.optional_vars), n.context_expr))
    for names, value in pairs:
        if len(names) == 1:
            scope.values[names[0]] = value
    changed = True
    while changed:
        changed = False
        for names, value in pairs:
            if any(x not in scope.tainted for x in names) and scope.taint(value):
                scope.tainted.update(names)
                changed = True


def _comparison_operands(e: ast.AST, aliases: Dict[str, str]) -> Optional[List[ast.AST]]:
    """The operands of `e` when `e` IS a comparison (a Compare that is not `is`, a prefix/substring method,
    a regex match), else None."""
    if isinstance(e, ast.UnaryOp) and isinstance(e.op, ast.Not):
        return _comparison_operands(e.operand, aliases)
    if isinstance(e, ast.Compare):
        if not all(isinstance(op, (ast.Eq, ast.NotEq, ast.In, ast.NotIn)) for op in e.ops):
            return None                           # an ordering or identity test is not a path comparison
        return [e.left] + list(e.comparators)
    if isinstance(e, ast.Call) and isinstance(e.func, ast.Attribute) and \
            e.func.attr in PREFIX_SINKS | STR_SINKS:
        return [e.func.value] + list(e.args)
    return None


def _prefix_slice(e: ast.AST) -> bool:
    """`e` is `x[:k]` (or `x[0:k]`) with a NON-literal `k`: the left part of `x` as long as something else.
    A conversion around it (`list(x[:k])`, `tuple(...)`, `str(...)`) is the same prefix (0.0.29 H-b xplat)."""
    while isinstance(e, ast.Call) and isinstance(e.func, ast.Name) and e.func.id in ("list", "tuple", "str") \
            and len(e.args) == 1 and not e.keywords:
        e = e.args[0]
    if not (isinstance(e, ast.Subscript) and isinstance(e.slice, ast.Slice)):
        return False
    sl = e.slice
    return sl.step is None and sl.upper is not None and not _is_const(sl.upper) and \
        (sl.lower is None or (isinstance(sl.lower, ast.Constant) and sl.lower.value == 0))


def _is_fnmatch_filter(call: ast.Call, aliases: Dict[str, str]) -> bool:
    f = call.func
    if isinstance(f, ast.Name):
        return aliases.get(f.id) == FNMATCH_FILTER
    return isinstance(f, ast.Attribute) and f.attr == "filter" and isinstance(f.value, ast.Name) and \
        aliases.get(f.value.id) == FNMATCH_MODULE


def _from_params(scope: _Scope, e: ast.AST) -> bool:
    """`e` is non-literal and every name in it is a parameter of `scope` (an attribute of one counts)."""
    if _is_literal(e):
        return False
    names = [n.id for n in ast.walk(e) if isinstance(n, ast.Name)]
    if not names:
        return False
    calls = [n for n in ast.walk(e) if isinstance(n, ast.Call)
             and not (isinstance(n.func, ast.Name) and n.func.id in ("len", "str", "tuple", "list"))]
    names = [x for x in names if x not in ("len", "str", "tuple", "list")]
    return not calls and bool(names) and all(x in scope.params and x not in ("self", "cls") for x in names)


def _scan(rel: str, scope: _Scope, out: List[Tuple[str, int, str, str]]) -> None:
    nodes = _own_nodes(scope.node)
    _bind(scope, nodes)
    called: Set[str] = set()
    name = scope.name

    def hit(n: ast.AST, idiom: str) -> None:
        out.append((rel, getattr(n, "lineno", 0), name, idiom))

    for n in nodes:
        if isinstance(n, ast.Call):
            cn = _call_name(n, scope.aliases)
            called.add(cn)
            f = n.func
            if cn == "normcase" and n.args and any(
                    isinstance(m, ast.Call) and _call_name(m, scope.aliases) == "realpath"
                    for m in ast.walk(n.args[0])):
                hit(n, "key")
            if cn in ("commonpath", "commonprefix"):
                hit(n, "commonpath")
            elif cn in ("relative_to", "is_relative_to") and isinstance(f, ast.Attribute):
                hit(n, "relative_to")
            elif cn in PREFIX_SINKS | STR_SINKS and isinstance(f, ast.Attribute) and n.args:
                recv, args = f.value, list(n.args)
                if not (scope.taint(recv) or any(scope.taint(a) for a in args)):
                    continue
                if all(_is_const(a) for a in args):
                    if any(_dotdot(a) for a in args):
                        hit(n, "dotdot")
                    continue
                hit(n, "startswith" if cn in PREFIX_SINKS else "substring")
            elif cn in REGEX and isinstance(f, ast.Attribute) and _regex_receiver(scope, f.value) is not None:
                compiled = _regex_receiver(scope, f.value)
                if any(scope.taint(a) for a in n.args) or any(scope.taint(a) for a in compiled):
                    hit(n, "regex")
            elif cn in PATTERN_FUNCS and len(n.args) >= 2 and any(scope.taint(a) for a in n.args) and \
                    not _is_const(n.args[1]):
                hit(n, "pattern")
            elif _is_fnmatch_filter(n, scope.aliases) and len(n.args) >= 2 and not _is_const(n.args[1]) and \
                    any(scope.taint(m) for m in ast.walk(n.args[0]) if isinstance(m, ast.expr)):
                hit(n, "pattern")                 # fnmatch.filter([<path>...], <non-literal pattern>)
            elif cn == "match" and isinstance(f, ast.Attribute) and scope.taint(f.value) and n.args and \
                    not _is_const(n.args[0]):
                hit(n, "pattern")                 # PurePath.match(<non-literal pattern>)
        elif isinstance(n, ast.Match) and scope.taint(n.subject):
            hit(n, "match")
        elif isinstance(n, ast.Compare):
            sides = [n.left] + list(n.comparators)
            for op, a, b in zip(n.ops, sides, sides[1:]):
                if isinstance(op, (ast.Is, ast.IsNot)):
                    continue
                if isinstance(op, (ast.Eq, ast.NotEq)) and (_prefix_slice(a) or _prefix_slice(b)) and \
                        not _is_const(a) and not _is_const(b):
                    hit(n, "slice")               # `x[:k] == y`: a prefix test, wherever the Compare sits
                    continue
                ta, tb = scope.taint(a), scope.taint(b)
                lit = a if _is_const(a) else b if _is_const(b) else None
                if lit is not None:
                    other = b if lit is a else a
                    if _dotdot(lit) and scope.taint(other):
                        hit(n, "dotdot")
                    elif _abs_literal(lit) and scope.taint(other):
                        hit(n, "literal_path")
                    continue
                if not (ta or tb):
                    continue
                if isinstance(op, (ast.Eq, ast.NotEq)):
                    if scope.dirname_of(a, b) or scope.dirname_of(b, a):
                        continue
                    hit(n, "equality")
                elif isinstance(op, (ast.In, ast.NotIn)):
                    hit(n, "parents" if isinstance(b, ast.Attribute) and b.attr == "parents" else "membership")
                else:
                    hit(n, "order")
    if "escapes_root" in called and "name_of" in called:
        line = next(n.lineno for n in nodes if isinstance(n, ast.Call)
                    and _call_name(n, scope.aliases) == "escapes_root")
        out.append((rel, line, name, "name_of"))
    # the comparator: a scope whose RESULT is a comparison of two parameter-only operands
    node = scope.node
    results: List[ast.AST] = []
    if isinstance(node, ast.Lambda):
        results = [node.body]
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        results = [r.value for r in nodes if isinstance(r, ast.Return) and r.value is not None]
    for r in results:
        ops = _comparison_operands(r, scope.aliases)
        if ops and sum(1 for o in ops if _from_params(scope, o)) >= 2:
            hit(r, "comparator")
            break


def _walk_scopes(rel: str, node: ast.AST, scope: _Scope, out: List[Tuple[str, int, str, str]]) -> None:
    _scan(rel, scope, out)
    for child in _own_nodes(node):
        if isinstance(child, _SCOPES):
            if isinstance(child, ast.Lambda):
                label = "<lambda>"
            else:
                label = child.name
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name in HELPERS:
                    out.append((rel, child.lineno, child.name, "helper"))
            qual = label if scope.name == "<module>" else f"{scope.name}.{label}"
            _walk_scopes(rel, child, _Scope(child, qual, scope, scope.aliases, scope.re_names), out)


def door_violations(corpus: Dict[str, str]) -> List[Tuple[str, int, str, str]]:
    """`[(relpath, line, scope, idiom)]` — every comparison of a path-tainted value outside the door."""
    out: List[Tuple[str, int, str, str]] = []
    for rel, src in sorted(corpus.items()):
        if rel == DOOR:
            continue
        tree = ast.parse(src)
        top = _Scope(tree, "<module>", None, _func_aliases(tree), _module_aliases(tree))
        _walk_scopes(rel, tree, top, out)
    return out


def grouped(violations) -> Dict[Tuple[str, str, str], int]:
    """`{(file, scope, idiom): count}` — the shape the allowlist is keyed by (attack H3-3)."""
    out: Dict[Tuple[str, str, str], int] = {}
    for rel, _line, fn, idiom in violations:
        out[(rel, fn, idiom)] = out.get((rel, fn, idiom), 0) + 1
    return out
