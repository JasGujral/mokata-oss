"""GATE-REACHABILITY — does a PRODUCTION path reach each BACKED gate? (0.0.17 stage 4)

THE HOLE THIS CLOSES. `REACHABILITY-PINS-MISSING` (doc 84) is the blind spot no individual pin
can see: *a test that constructs the precondition itself proves the CODE works and says NOTHING
about whether any production path ever reaches it.* A stage can be fully built, fully pinned,
100% mutation-RED and completely inert in the shipped product — H-6's four slices were, and so is
DB.S7c2's memory-handle half. The registry in `skill_contracts.GATES` is internally consistent —
no backed gate lacks an enforcement point, no advisory gate claims one — so the defect is not the
table. It is that **nothing derives whether a backed gate is reachable from a production path.**

THE SHAPE THE QUESTION HAS. Doc 84's widening of `GATE-UNREACHABLE-BRAINSTORM` states it best:
*"the ONLY `graph_gate=` argument pass anywhere in `src/` is `domains.py:252`, one internal
function handing the value to another internal function. No production path anywhere ORIGINATES a
`graph_gate` value"* — "a stronger and cheaper statement of the defect than counting callers of one
gate". So this module does not ask "is this gate called somewhere". It asks **does any PRODUCTION
ENTRY POINT originate a path that reaches it**, and an entry point is DERIVED from the surfaces a
user can actually invoke, never hand-listed.

WHY THE VERDICT IS A TRIPLE AND NOT A BOOLEAN (doc 85 §7g). "No production caller found" means
both *"this gate is unreachable"* and *"the resolver could not see the path"*, and those are the
two things that must never share a representation. `_writegraph.UNCLOSED_SHAPES` already enumerates
what the resolver cannot see — factory-bound receivers, inherited methods, dynamic dispatch — and
two of the nine gates are reached through exactly those shapes. So a gate whose reachability is
UNPROVABLE is a third outcome, not a failure, and every verdict carries the BASIS that produced it:

  BASIS_STRUCTURAL      a resolved call path from an entry point           → REACHABLE
  BASIS_LEXICAL         no resolved path, but an entry-reachable function  → REACHABLE (floor)
                        SPELLS the symbol and its module imports the gate
  BASIS_VERIFIED_EMPTY  NO module in the package imports the gate's        → UNREACHABLE
                        enforcement point at all — a certified zero
  BASIS_UNPROVABLE      importers exist, no call site is entry-reachable   → UNPROVABLE

The vocabulary is borrowed on purpose from `knowledge/query.py` (0.0.17 stage 6, `3ed0419`), which
learned the same lesson on the same day: *a leaf's zero is an ANSWER, and only basis can say which
zero it is.* `verdict` is DERIVED from `basis` rather than stored beside it, because two fields
that can disagree is the §7g defect itself.

DIRECTION OF ERROR. A false REACHABLE is an EXCUSE — it hides an inert gate, which is the whole
thing this instrument exists to find. A false UNREACHABLE is an alarm someone has to answer. So the
LEXICAL rung is deliberately narrow (three conditions, all required) and UNPROVABLE never counts as
a pass. What the instrument cannot establish is declared in `UNSEEN_REACH` and RENDERED, not left
in a docstring only its author reads.

REUSE, NOT A SECOND RESOLVER. Everything structural comes from `tests/_writegraph.py` — the call
graph, the five resolution rules, and `gate_enclosure`'s per-line owner map. A second call-graph
implementation beside the audited one would be the `badge_run`/`find_active_run` mistake again. The
only change there is ONE read-only accessor (`owner_of`) over a map `gate_enclosure` already
computes; no resolution rule is touched and no `_writegraph` number moves.

WHAT IS ADDED HERE INSTEAD, AND WHY IT IS NOT A SECOND RESOLVER. `_writegraph` DECLARES that it
does not follow a symbol re-exported through an `__init__.py`, and on this tree that is the
dominant import idiom, so the shadow it casts is not marginal: without following it, `run_playbook`
and `guard_change` are unreachable from every entry point and two gates that ARE wired read as
unprovable. `build_reexport_origins` computes that as a LEXICAL fact — this name came from that
file — and it is used in three places: to redirect dangling edge targets before the forward
closure, to resolve an entry point onto its definition, and to bound the lexical rung. None of them
adds a resolution RULE; each closes a shape the resolver itself says it leaves open.

PURE FUNCTION OVER A SUPPLIED CORPUS (doc 85 §7i). `build_gate_reachability` takes a package root
and a gate table and judges what it is GIVEN; it never discovers its own subject. That is what lets
the pin be graded against a PLANTED unreachable gate instead of against a healthy tree, where a
sweep that finds nothing proves nothing.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import ast
import os
import re
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Iterable, List, Optional, Sequence, Set, Tuple

import _writegraph
from _writegraph import Site

# ======================================================================================
# the four bases and the three verdicts they produce
# ======================================================================================

BASIS_STRUCTURAL = "structural"          # a RESOLVED call path from a production entry point
BASIS_LEXICAL = "lexical"                # the floor: an entry-reachable function spells the symbol
BASIS_VERIFIED_EMPTY = "verified_empty"  # nothing in the package imports the enforcement point
BASIS_UNPROVABLE = "unprovable"          # importers exist; no entry-reachable call site found

REACHABLE = "REACHABLE"
UNREACHABLE = "UNREACHABLE"
UNPROVABLE = "UNPROVABLE"

#: basis -> the ONE verdict it produces. The map is the single place the two vocabularies meet, so
#: a verdict can never be asserted independently of the basis that earned it.
_VERDICT_OF = {
    BASIS_STRUCTURAL: REACHABLE,
    BASIS_LEXICAL: REACHABLE,
    BASIS_VERIFIED_EMPTY: UNREACHABLE,
    BASIS_UNPROVABLE: UNPROVABLE,
}

#: THE ENTRY-POINT RUNGS, declared and rendered. Each is a mechanical rule over the source; none is
#: a hand-kept list of surfaces, because a hand-kept list goes stale the release after it is written
#: and then quietly narrows what "production" means.
ENTRY_RULES = (
    "console-script — a `[project.scripts]` value in pyproject.toml (`mokata.cli:main`), resolved "
    "through re-exports so `mokata.mcp_server:main` lands on the module that defines it",
    "cli-subcommand — the callable in `parser.set_defaults(func=<name>)`, resolved through the "
    "declaring module's imports; this is what `cli.main`'s `args.func(args)` dispatch invokes, and "
    "that dispatch is dynamic, so the handlers are the entry points and `main` alone is not",
    "mcp-tool — a def the `_tool(name, kind)` registry factory registers, applied EITHER as a "
    "decorator (`tools_read`) or as a plain call on a plain function (`tools_write`), i.e. every "
    "tool the MCP server exposes to the harness; the derived count is pinned against the live "
    "`registry.TOOLS`, because reading only the decorator form silently omitted 20 of the 61 — "
    "and it was the WRITE surface, where the gates are",
    "hook-subcommand — a value in `hook_cli._SUBCOMMANDS`, the dict `mokata-hook <sub>` dispatches "
    "through (again dynamic, so again the handlers are the entry points)",
)

#: WHAT THIS INSTRUMENT CANNOT ESTABLISH — declared here, rendered into the pin's failure message
#: and into the report, for the same reason `_writegraph.UNCLOSED_SHAPES` is. Each line is a shape
#: MEASURED on the live tree, not a hypothetical.
UNSEEN_REACH = (
    "everything in `_writegraph.UNCLOSED_SHAPES` limits the STRUCTURAL rung — measured on this "
    "tree, `EnforcementGate(...).check(...)` (a call on a constructor result) and "
    "`from ..govern import TddGuard` (a re-export the resolver does not follow) both hide a real "
    "production path, which is exactly why a bare 'no caller found' would be a lie",
    "the LEXICAL rung closes exactly TWO receiver shapes (a constructor result, and a local bound "
    "to a constructor whose class arrived through a re-export). A gate reached any other way — a "
    "factory-bound receiver, an inherited method, a callable passed as an argument — stays unseen, "
    "and the basis carries the file:line so a lexical verdict is a claim a reader can check in one "
    "jump rather than one they must trust",
    "the IMPORTER population subtracts any binding the module also lists in `__all__`, because a "
    "re-export is not a use. A module that BOTH re-exports a gate and calls it is therefore not "
    "counted — which can only depress a verdict, never inflate one",
    "reachability is a STATIC question here: a path that exists is not a path that runs. A gate "
    "behind a config flag nobody sets, or on a branch no input takes, still reads as REACHABLE",
    "dynamic invocation of the gate itself — `getattr(obj, 'check')(...)`, a dispatch dict, a "
    "registry — is invisible to BOTH rungs, so it can only depress a verdict toward UNPROVABLE or "
    "UNREACHABLE, never inflate one",
    "an entry point reached by a rung not in `ENTRY_RULES` (a new surface wired some other way) is "
    "simply absent, which shrinks the closure and again errs toward flagging",
    "`keyword_origination` sees only a LITERAL `kw=` at a call site. A value forwarded through "
    "`**kwargs` carries no `ast.keyword` with that name and is invisible, so the population of "
    "passes can only be UNDER-counted — the safe direction for a finding that says 'there are too "
    "few', and the unsafe one for any future use that asks 'is this parameter used anywhere'",
)


@dataclass(frozen=True)
class GateReachability:
    """One gate's verdict, and the evidence that produced it.

    `verdict` is a PROPERTY of `basis`, never a stored field — `RunResolution`'s discipline (doc 85
    §7g): *an answer that carries its own provenance cannot be mistaken for a different answer.*"""

    gate_id: str
    module: str                              # the enforcement point, package-relative
    symbol: str                              # the qualified callable that can refuse
    basis: str
    #: the resolved call path that proves a STRUCTURAL verdict, entry point first. Empty otherwise.
    path: Tuple[Site, ...] = ()
    #: the `(rel, lineno)` a LEXICAL verdict rests on, and the entry-reachable def that owns it.
    site: Optional[Tuple[str, int]] = None
    owner: Optional[str] = None
    #: every module with an import binding whose ORIGIN is `module` — the population the LEXICAL
    #: rung searched, and the count whose ZERO is what `BASIS_VERIFIED_EMPTY` certifies.
    importers: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.basis not in _VERDICT_OF:
            raise ValueError(f"unknown basis {self.basis!r}")
        if self.basis == BASIS_STRUCTURAL and not self.path:
            raise ValueError(
                f"{self.gate_id}: basis={BASIS_STRUCTURAL!r} claims a resolved path and carries "
                f"none — a structural verdict IS its path")
        if self.basis == BASIS_LEXICAL and self.site is None:
            raise ValueError(
                f"{self.gate_id}: basis={BASIS_LEXICAL!r} claims a call site and carries none — "
                f"a lexical verdict a reader cannot jump to is one they must take on trust")
        if self.basis == BASIS_VERIFIED_EMPTY and self.importers:
            raise ValueError(
                f"{self.gate_id}: basis={BASIS_VERIFIED_EMPTY!r} certifies that NOTHING imports "
                f"{self.module}, but {len(self.importers)} module(s) do — that is an UNPROVABLE, "
                f"not a certified zero")

    @property
    def verdict(self) -> str:
        return _VERDICT_OF[self.basis]

    def render(self) -> str:
        head = f"{self.gate_id:30s} {self.verdict:11s} basis={self.basis}"
        if self.basis == BASIS_STRUCTURAL:
            return f"{head}\n      {_writegraph.render_path(self.path)}"
        if self.basis == BASIS_LEXICAL:
            rel, line = self.site
            return f"{head}\n      {rel}:{line} in {self.owner} spells {self.symbol}"
        if self.basis == BASIS_VERIFIED_EMPTY:
            return f"{head}\n      no module in the package imports {self.module}"
        return (f"{head}\n      {len(self.importers)} importer(s), none with an entry-reachable "
                f"call site: {', '.join(self.importers)}")


# ======================================================================================
# re-export origins — the ONE blind spot this layer closes, and it closes it LEXICALLY
# ======================================================================================

def build_reexport_origins(root: str, *, package: str = "mokata") -> Dict[Site, Site]:
    """`(rel, local name)` -> `(defining rel, original name)` for every symbol a module RE-EXPORTS.

    `_writegraph` declares this as an unclosed shape — *"nor is a symbol RE-EXPORTED through an
    `__init__.py` followed to the module that defines it"* — and on this tree it is the dominant
    import idiom: `from ..govern import TddGuard`, `from ..engine import check_spec_persisted`.
    Following it is a LEXICAL fact (this name came from that file), not a call-graph edge, so it is
    computed HERE rather than inside the resolver whose audited numbers would move.

    Closed to a fixpoint so a chain of re-exports resolves, with the chain bounded by the number of
    bindings — a cycle simply stops making progress rather than looping."""
    trees = _writegraph._parse_package(root)
    facts = _writegraph._collect_facts(trees, package)
    origins: Dict[Site, Site] = {}
    for rel, f in facts.items():
        for local, (target, symbol) in f.imported.items():
            origins[(rel, local)] = (target, symbol)
        for alias, target in f.modules.items():
            origins[(rel, alias)] = (target, "")        # a MODULE alias — no symbol of its own
    # walk each binding to the module that DEFINES it
    resolved: Dict[Site, Site] = {}
    for key, first in origins.items():
        seen = {key}
        cur = first
        while cur in origins and cur not in seen:
            seen.add(cur)
            cur = origins[cur]
        resolved[key] = cur
    return resolved


def _dunder_all(tree: ast.Module) -> FrozenSet[str]:
    """The string literals in a module-level `__all__`. A binding a module both imports and lists
    in `__all__` is a RE-EXPORT, not a use — and counting the re-exporter as an importer is what
    turns a gate nothing consumes into a gate "one module imports"."""
    names: Set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if not any(isinstance(t, ast.Name) and t.id == "__all__" for t in targets):
                continue
            for elt in getattr(node.value, "elts", []):
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    names.add(elt.value)
    return frozenset(names)


def _is_ctor_of(node: ast.expr, origins: Dict[Site, Site], rel: str,
                module: str, cls: str) -> bool:
    """Is `node` a CONSTRUCTOR call for `module`'s class `cls`, spelled any way this tree spells
    one — a bare imported name (`EnforcementGate(...)`) or through a module alias
    (`deviation.DeviationGate(...)`), each resolved through the re-export chain."""
    if not isinstance(node, ast.Call):
        return False
    fn = node.func
    if isinstance(fn, ast.Name):
        return origins.get((rel, fn.id)) == (module, cls)
    if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name):
        return fn.attr == cls and origins.get((rel, fn.value.id)) == (module, "")
    return False


def _lexical_call_sites(tree: ast.Module, origins: Dict[Site, Site], rel: str,
                        module: str, symbol: str) -> List[int]:
    """The lines in `rel` that call `module:symbol` through a shape `_writegraph` declares unseen.

    This is NOT a bare-name match. It resolves the RECEIVER, which is the whole difference between
    an instrument and a coincidence: the first version of this rung matched any call spelled with
    the symbol's tail name inside an importing module, and on its first run it credited the
    `deviation` gate with `engine/amend.py:352` — `wgate.submit(...)`, a **WriteGate**. Two gates
    in one file with a method name in common is not an exotic shape; it is Tuesday.

    Two closed shapes, and only two, both named in `UNCLOSED_SHAPES`:

      · a call on a CONSTRUCTOR RESULT — `EnforcementGate(ledger).check(...)` (rule (e) binds bare
        names only, so `foo().bar()` resolves to nothing)
      · a call on a local BOUND to a constructor whose class arrived through a RE-EXPORT —
        `guard = TddGuard(...)` … `guard.guard_implementation(...)`, where `TddGuard` came from
        `from ..govern import TddGuard` (rule (e) works, but cannot follow the re-export)

    …plus the module-level function equivalent, `from ..engine import check_spec_persisted` then
    `check_spec_persisted(...)`. Anything else stays unseen, which can only depress a verdict."""
    hits: List[int] = []
    if "." in symbol:
        cls, tail = symbol.rsplit(".", 1)
        bound: Set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if node.value is not None and _is_ctor_of(node.value, origins, rel, module, cls):
                    bound.update(t.id for t in targets if isinstance(t, ast.Name))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not isinstance(fn, ast.Attribute) or fn.attr != tail:
                continue
            if (isinstance(fn.value, ast.Name) and fn.value.id in bound) or \
                    _is_ctor_of(fn.value, origins, rel, module, cls):
                hits.append(node.lineno)
        return sorted(set(hits))

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Name) and origins.get((rel, fn.id)) == (module, symbol):
            hits.append(node.lineno)
        elif (isinstance(fn, ast.Attribute) and fn.attr == symbol
                and isinstance(fn.value, ast.Name)
                and origins.get((rel, fn.value.id)) == (module, "")):
            hits.append(node.lineno)
    return sorted(set(hits))


# ======================================================================================
# the production entry points, DERIVED by the four declared rungs
# ======================================================================================

_SCRIPTS_SECTION = re.compile(r"^\[project\.scripts\]\s*$")
_SCRIPT_LINE = re.compile(r"""^\s*[\w-]+\s*=\s*["']([\w.]+):(\w+)["']\s*$""")


def read_console_scripts(pyproject: str, *, package: str = "mokata") -> List[Site]:
    """The `[project.scripts]` targets as `(rel, function)`. A tiny line scan rather than a TOML
    parse because `tomllib` is 3.11+ and the declared floor is 3.10; the section is three lines of
    `name = "module:function"` and the pin asserts how many were found, so a parse that silently
    read none could not pass."""
    out: List[Site] = []
    inside = False
    with open(pyproject, encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if stripped.startswith("["):
                inside = bool(_SCRIPTS_SECTION.match(stripped))
                continue
            if not inside:
                continue
            m = _SCRIPT_LINE.match(line)
            if m:
                dotted, fn = m.group(1), m.group(2)
                for cand in _writegraph._module_candidates(dotted, package):
                    out.append((cand, fn))
                    break
    return out


def _is_tool_registrar(node: ast.expr) -> bool:
    """Is this expression the MCP registry's `_tool` factory, spelled either way?"""
    return getattr(node, "id", None) == "_tool" or getattr(node, "attr", None) == "_tool"


def _dict_values_named(tree: ast.Module, name: str) -> Set[str]:
    """The bare names a module-level `name = {...}` dict maps to — a DISPATCH TABLE. `hook_cli`
    routes `mokata-hook <sub>` through one, and `cli.main` routes through `args.func`; both are
    dynamic, so the table's values are where production actually enters."""
    out: Set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            continue
        if isinstance(node.value, ast.Dict):
            for v in node.value.values:
                if isinstance(v, ast.Name):
                    out.add(v.id)
    return out


def derive_entry_points(root: str, *, package: str = "mokata",
                        console_scripts: Sequence[Site] = (),
                        dispatch_tables: Sequence[Tuple[str, str]] = ()) -> Dict[Site, str]:
    """Every production entry point, as `site -> the rung that found it`.

    `dispatch_tables` are `(rel, variable)` pairs naming a module-level dict whose values are
    handlers. Passed IN rather than hunted for, so the corpus a caller supplies is the corpus this
    judges — the §7i rule. A site that names no `def` in the tree is DROPPED, not silently kept, so
    a rung that resolved to nothing cannot pad the closure."""
    trees = _writegraph._parse_package(root)
    facts = _writegraph._collect_facts(trees, package)
    origins = build_reexport_origins(root, package=package)
    found: Dict[Site, str] = {}

    def offer(site: Optional[Site], rung: str) -> None:
        if site is None:
            return
        rel, qual = site
        if (rel, qual) in origins:                        # a re-export: follow it to the def
            rel, qual = origins[(rel, qual)]
        if rel in facts and qual in facts[rel].defs:
            found.setdefault((rel, qual), rung)

    for site in console_scripts:
        offer(site, "console-script")

    for rel, tree in trees.items():
        f = facts[rel]

        def local(name: str) -> Optional[Site]:
            if name in f.imported:
                return f.imported[name]
            if name in f.defs:
                return (rel, name)
            return None

        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "set_defaults":
                for kw in node.keywords:
                    if kw.arg == "func" and isinstance(kw.value, ast.Name):
                        offer(local(kw.value.id), "cli-subcommand")
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for dec in node.decorator_list:
                    head = dec.func if isinstance(dec, ast.Call) else dec
                    if _is_tool_registrar(head):
                        offer((rel, node.name), "mcp-tool")
            # `_tool("spec_check", "write")(spec_check)` — the SAME registration, applied as a
            # plain call instead of decorator syntax. `mcp/tools_write.py` registers its whole
            # surface this way: 20 of the 61 live tools, and the 20 that carry the WRITE gates.
            # Reading only the decorator form left the entire write surface out of the closure.
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Call)
                    and _is_tool_registrar(node.func.func) and len(node.args) == 1
                    and isinstance(node.args[0], ast.Name)):
                offer(local(node.args[0].id), "mcp-tool")

    for rel, var in dispatch_tables:
        if rel not in trees:
            continue
        f = facts[rel]
        for name in _dict_values_named(trees[rel], var):
            site = f.imported.get(name) or ((rel, name) if name in f.defs else None)
            offer(site, "hook-subcommand")
    return found


def redirect_through_reexports(graph: "_writegraph.WriteGraphResult",
                               origins: Dict[Site, Site]) -> Dict[Site, FrozenSet[Site]]:
    """The graph's adjacency with every edge target that is a RE-EXPORT pointed at the definition.

    The resolver emits `cmd_playbook -> cli_commands/_common.py:run_playbook` because that is where
    the name was imported from; `_common` re-exports it and defines nothing, so the edge DANGLES
    (this tree carries 175 such targets) and the forward closure stops dead one hop short of the
    real function. Measured: without this, `run_playbook` and `guard_change` are both unreachable
    from any entry point, and two gates that ARE wired read as unprovable.

    The redirection is the same LEXICAL fact `build_reexport_origins` already holds — this changes
    no resolution rule and no `_writegraph` number; it re-points edges the resolver itself declares
    it does not follow."""
    out: Dict[Site, FrozenSet[Site]] = {}
    for caller, callees in graph.edges.items():
        out[caller] = frozenset(origins.get(c, c) for c in callees)
    return out


def close_forward(adjacency: Dict[Site, FrozenSet[Site]],
                  entries: Iterable[Site]) -> Dict[Site, Tuple[Site, ...]]:
    """Every site reachable FORWARD from any entry point, with the shortest path that proves it.

    The mirror of `_writegraph.close_over_writers`, which closes backwards from a writer. Breadth
    first from all entries at once; a site is enqueued only the first time it is reached, so a
    cycle terminates."""
    starts = sorted(set(entries))
    path: Dict[Site, Tuple[Site, ...]] = {s: (s,) for s in starts}
    queue = deque(starts)
    while queue:
        cur = queue.popleft()
        for callee in sorted(adjacency.get(cur, ())):
            if callee not in path:
                path[callee] = path[cur] + (callee,)
                queue.append(callee)
    return path


# ======================================================================================
# THE DERIVATION — a pure function over a supplied corpus
# ======================================================================================

def build_gate_reachability(root: str, gates: Dict[str, Site], *, package: str = "mokata",
                            console_scripts: Sequence[Site] = (),
                            dispatch_tables: Sequence[Tuple[str, str]] = (),
                            graph: Optional["_writegraph.WriteGraphResult"] = None,
                            ) -> Dict[str, GateReachability]:
    """Per-gate reachability for the package at `root`, judging the `gates` table it is GIVEN.

    `gates` maps a gate id to `(module rel path, qualified symbol that can refuse)`. It is supplied
    rather than discovered so this same function grades a PLANTED corpus — a sweep that walks a
    healthy tree and finds nothing proves nothing (doc 85 §7i).

    `build_*` per doc 85 §3: it degrades to a FLOOR — a path it cannot resolve is absent and the
    verdict drops to UNPROVABLE — rather than raising."""
    graph = _writegraph.build_call_graph(root, package=package) if graph is None else graph
    enclosure = _writegraph.gate_enclosure(root, package=package, graph=graph)
    trees = _writegraph._parse_package(root)
    facts = _writegraph._collect_facts(trees, package)
    origins = build_reexport_origins(root, package=package)
    entries = derive_entry_points(root, package=package, console_scripts=console_scripts,
                                  dispatch_tables=dispatch_tables)
    reached = close_forward(redirect_through_reexports(graph, origins), entries)

    # Which modules IMPORT each enforcement point, following re-exports to the defining module. A
    # binding the module also lists in `__all__` is a re-export and is NOT a consumer — without
    # that subtraction `engine/ship.py` reads as "one module imports it" (its own package
    # `__init__`) and a gate nothing consumes hides behind its own re-export line.
    exported = {rel: _dunder_all(tree) for rel, tree in trees.items()}
    importers: Dict[str, Set[str]] = {}
    for (rel, local), (origin_rel, _sym) in origins.items():
        if origin_rel != rel and local not in exported.get(rel, frozenset()):
            importers.setdefault(origin_rel, set()).add(rel)

    out: Dict[str, GateReachability] = {}
    for gate_id, (module, symbol) in sorted(gates.items()):
        pop = tuple(sorted(importers.get(module, set()) - {module}))
        site = (module, symbol)

        if site in reached:                                        # rung 1 — STRUCTURAL
            out[gate_id] = GateReachability(
                gate_id=gate_id, module=module, symbol=symbol, basis=BASIS_STRUCTURAL,
                path=reached[site], importers=pop)
            continue

        hit = None
        for rel in pop:                                            # rung 2 — LEXICAL floor
            for lineno in _lexical_call_sites(trees[rel], origins, rel, module, symbol):
                owner = enclosure.owner_of(rel, lineno)
                if owner is not None and (rel, owner) in reached:
                    hit = (rel, lineno, owner)
                    break
            if hit is not None:
                break
        if hit is not None:
            rel, lineno, owner = hit
            out[gate_id] = GateReachability(
                gate_id=gate_id, module=module, symbol=symbol, basis=BASIS_LEXICAL,
                site=(rel, lineno), owner=owner, importers=pop)
            continue

        # NEITHER rung answered. Which zero is it? A population of ZERO importers is a CERTIFIED
        # zero — no module in the package so much as names this enforcement point, so no call to it
        # can exist outside the dynamic shapes in `UNSEEN_REACH`. A non-empty population is an
        # absence of evidence, and those two must not share a representation (§7g).
        basis = BASIS_VERIFIED_EMPTY if not pop else BASIS_UNPROVABLE
        out[gate_id] = GateReachability(
            gate_id=gate_id, module=module, symbol=symbol, basis=basis, importers=pop)
    return out


def unreachable_gates(report: Dict[str, GateReachability],
                      declared_inert: Iterable[str]) -> List[str]:
    """Gate ids whose verdict is UNREACHABLE and which are NOT on the declared-inert list.

    `declared_inert` is passed IN rather than read from a table here, exactly as
    `_writegraph.ungated_caller_violations` takes its `excused` set: the exceptions are then a fact
    about the pin, reviewable in one place and pinnable by count, instead of a flag scattered across
    the entries they excuse."""
    skip = set(declared_inert)
    return sorted(g for g, r in report.items() if r.verdict == UNREACHABLE and g not in skip)


def stale_inert_declarations(report: Dict[str, GateReachability],
                             declared_inert: Iterable[str]) -> List[str]:
    """Declared-inert gates that are no longer inert — the exception EXPIRING BY ITSELF.

    Without this, wiring a gate leaves its excuse behind and the next reader learns a false rule
    (doc 85 §7h). A declaration that outlives its defect is a comment."""
    return sorted(g for g in set(declared_inert)
                  if g not in report or report[g].verdict != UNREACHABLE)


@dataclass(frozen=True)
class KeywordPass:
    """One call site that supplies a value for a keyword parameter, and the def that owns it."""

    rel: str
    lineno: int
    owner: Optional[str]                     # the enclosing qualname, None at module level
    entry_reachable: bool                    # is that owner reachable from a production entry?


def keyword_origination(root: str, keyword: str, *, package: str = "mokata",
                        console_scripts: Sequence[Site] = (),
                        dispatch_tables: Sequence[Tuple[str, str]] = (),
                        graph: Optional["_writegraph.WriteGraphResult"] = None,
                        ) -> List[KeywordPass]:
    """Every call site in the package that PASSES `keyword=`, with whether production reaches it.

    A DIFFERENT QUESTION FROM REACHABILITY, and D1 is why both are needed. `BrainstormSession.
    approve` is measured REACHABLE (a resolved structural path from a CLI handler) — it is the
    human gate the whole brainstorm phase turns on. But its GR.S3 refusal is guarded by
    `if graph_gate is not None`, and **no caller anywhere originates a `graph_gate` value**, so the
    refusal is dead inside a function that runs constantly. A reachability verdict on `approve`
    is GREEN and says nothing about it.

    That is the general shape: a gate reached through an OPTIONAL parameter is only as live as the
    argument passes that feed it, so the population of passes is the measurement. Doc 84 puts the
    D1 case at its cheapest — *"the ONLY `graph_gate=` argument pass anywhere in `src/` is
    `domains.py`, one internal function handing the value to another internal function"* — and this
    derives that rather than restating it.

    `**kwargs` forwarding is INVISIBLE here (a `**opts` that happens to carry the key is not an
    `ast.keyword` with that `arg`), which can only UNDER-count passes — the safe direction for an
    audit whose finding is "there are too few". It is declared in `UNSEEN_REACH` alongside the
    rest."""
    trees = _writegraph._parse_package(root)
    graph = _writegraph.build_call_graph(root, package=package) if graph is None else graph
    enclosure = _writegraph.gate_enclosure(root, package=package, graph=graph)
    origins = build_reexport_origins(root, package=package)
    entries = derive_entry_points(root, package=package, console_scripts=console_scripts,
                                  dispatch_tables=dispatch_tables)
    reached = close_forward(redirect_through_reexports(graph, origins), entries)

    out: List[KeywordPass] = []
    for rel, tree in sorted(trees.items()):
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not any(kw.arg == keyword for kw in node.keywords):
                continue
            owner = enclosure.owner_of(rel, node.lineno)
            out.append(KeywordPass(
                rel=rel, lineno=node.lineno, owner=owner,
                entry_reachable=owner is not None and (rel, owner) in reached))
    return sorted(out, key=lambda p: (p.rel, p.lineno))


def symbol_exists(root: str, module: str, symbol: str, *, package: str = "mokata") -> bool:
    """Does `symbol` name a real `def` in `module`? The gate table's own anti-rot check: a renamed
    or deleted enforcement callable must RED the pin, not silently become unreachable."""
    trees = _writegraph._parse_package(root)
    if module not in trees:
        return False
    facts = _writegraph._collect_facts(trees, package)
    return symbol in facts[module].defs


def render_reachability_report(report: Dict[str, GateReachability],
                               entries: Dict[Site, str]) -> str:
    """Human string, never writes (doc 85 §3 `render_*`). Renders the rungs and the blind spots
    VERBATIM — a limitation only its author reads is not a declared limitation."""
    lines = ["GATE REACHABILITY — production paths into the backed gates", ""]
    by_rung: Dict[str, int] = {}
    for rung in entries.values():
        by_rung[rung] = by_rung.get(rung, 0) + 1
    lines.append(f"entry points derived: {len(entries)}")
    for rung in ENTRY_RULES:
        name = rung.split(" — ")[0]
        lines.append(f"  {name:18s} {by_rung.get(name, 0):4d}   {rung.split(' — ', 1)[1]}")
    lines.append("")
    for verdict in (UNREACHABLE, UNPROVABLE, REACHABLE):
        hits = [r for r in report.values() if r.verdict == verdict]
        lines.append(f"{verdict}: {len(hits)}")
        for r in sorted(hits, key=lambda x: x.gate_id):
            lines.append("  " + r.render())
        lines.append("")
    lines.append("WHAT THIS INSTRUMENT CANNOT ESTABLISH:")
    for shape in UNSEEN_REACH:
        lines.append(f"  - {shape}")
    lines.append("")
    lines.append("…and everything the resolver beneath it cannot see:")
    for shape in _writegraph.UNCLOSED_SHAPES:
        lines.append(f"  - {shape}")
    return "\n".join(lines)
