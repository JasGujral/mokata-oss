"""SI.6-DELEGATED-BLINDNESS — a RESOLVED intra-package call graph, for the zero-bypass audit.

THE HOLE THIS CLOSES. `TestZeroBypass` is a static AST scan over a fixed VOCABULARY of write
NAMES. A function that reaches disk by CALLING one of those writers rather than by naming one is
invisible to it — and "reaches disk through another name" is the normal shape of real code. Two
live confirmations, both from real builds rather than inspection:

  1. DB.S7d/P10 (2026-07-31) — a delegated `journal._append` inside `_ask_group`, an asker whose
     whole charter is "writes nothing", passed all 4972 pre-P10 unit tests, SI.6 included.
  2. DB.S7a (2026-07-31) — the entire v5 edge substrate lands durable rows on both engines through
     `conn.execute(INSERT …)`; the sweep stayed green and never asked anyone to classify it.

(2) is closed by widening the detector's vocabulary (the `.execute` receiver class). (1) needs
THIS: a call graph, so that "F delegates its write to G" is a fact the audit can state.

WHY RESOLVED EDGES, AND WHY THAT IS NOT A COMPROMISE. The backlog row proposed "if F calls G and G
is a registered write site, F is a write site too". Taken literally, with BARE-NAME matching — a
call to `put` matching any function anywhere named `put` — that explodes the register from 62 sites
to 1,650 in 11 rounds. A register nobody can read is the same false assurance in a new costume.
Measured at the stage grounding: with edges resolved to a DEFINITION SITE the full transitive
closure is 201, not 1,650. **Completeness here is CHEAPER than the compromise, not dearer**, so
this closes to a fixpoint with no depth cap and no same-module-only restriction.

HOW A SITE IS KEYED — QUALIFIED, one key per DEFINITION (0.0.17 stage 1a review, F1). A site is
`(file, dotted scope path)`: `SQLiteBackend.put`, `MemoryStore.remember._commit`. The BARE innermost
name it replaced collapsed distinct definitions into one entry and one sentence — measured at the
review: 15 of the 112 keys covered more than one definition, worst `memory/store.py:_commit` at
EIGHT commit closures under a single justification, and a planted `ExfilBackend.put` appended to
`memory/backends.py` left the site count unchanged and the sweep GREEN because it landed under the
existing `put` key. A key that cannot tell two definitions apart cannot notice a new one.

WHAT A RESOLVED EDGE IS. Exactly five rules, and nothing else counts as a call edge:

  (a) `name(...)`       where `name` is a `def` visible by LEXICAL scope   → (this file, its qualname)
  (b) `name(...)`       from `from <mokata module> import name`            → (that file, name)
  (c) `self.name(...)`  where the ENCLOSING class defines `name`           → (this file, Class.name)
  (d) `alias.name(...)` where `alias` is an imported mokata MODULE         → (that file, name)
  (e) `local.name(...)` where `local` was ASSIGNED a mokata class's constructor in an enclosing
      scope and that class defines `name`                                  → (that file, Class.name)

Rule (a) climbs lexically — innermost enclosing FUNCTION first, then outwards, skipping class
scopes, which a bare name cannot see in Python. Rule (e) is the review's fix for the receiver gap:
`store = SQLiteBackend(path)` … `store.put(item)` used to be invisible to both instruments.

WHAT THIS RESOLVER CANNOT SEE — stated here, in `UNCLOSED_SHAPES` below, in the sweep's FAILURE
MESSAGE and in the audit report, rather than discovered later (P16, honest degradation). A
limitation only its author reads is not a declared limitation. `unresolved_calls` reports the size
of the shadow. The authoritative list is `UNCLOSED_SHAPES`; it is rendered, not paraphrased.

Two definitions that share a qualname in one module — three `_commit`s in one function body, a
def rebound under `if`/`else` — still collapse to one key. That residual is MEASURED and NAMED by
`qualname_collisions()` rather than asserted away, because the register must say which of its keys
still cover more than one definition.

WHERE A CALL RUNS, added at 0.0.17 stage 1a-FU. `GATED`'s header classifies by how the write is
REACHED, which makes governance a property of the CALL SITE and not of the function — so
`gate_enclosure()` answers it per `(file, line)`: is this call lexically inside a commit callable,
or inside a def that only a commit callable reaches? Its limits are declared in `UNSEEN_ENCLOSURE`
and its recognised runners in `GATE_RUNNERS`, both rendered. It errs toward FLAGGING: a call it
cannot place reads as ungated, never as gated, because for an audit the cost of a spurious question
is nothing and the cost of a spurious excuse is the whole instrument.

That primitive is what lets the register's caller claims stop being prose. `derive_callers()` pins
the callers the graph can see (58 of 135 registered sites; the other 77 are reached through shapes
below, INCLUDING both `write_bundle` twins), and `ungated_caller_violations()` — the coherence
contract's SECOND direction — checks the file:line callers a human declared, deriving the verdict
on each rather than reading it off the entry. `CONTRACT_DIRECTIONS` says which directions run, in
the failure message and the report, because the previous version ran one direction, nobody had
written down which, and so nobody noticed the other was missing.

DEPENDENCY POSTURE. stdlib `ast` only — no third-party parser, no new dependency, and NOTHING from
`mokata.knowledge` (`ast_backend.py`, `layer.py`). An audit that depends on the subsystem it audits
cannot fail honestly when that subsystem is wrong.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import ast
import os
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Iterable, List, Optional, Sequence, Set, Tuple

# A site is keyed exactly as the SI.6 register keys it: the module's path relative to the package
# root, and the QUALIFIED name of the enclosing definition — the dotted scope path, classes
# included ("<module>" when there is no enclosing scope at all). `SQLiteBackend.put` and
# `PostgresBackend.put` are two sites; before the 0.0.17 review they were one.
Site = Tuple[str, str]

#: THE DECLARED BLIND SPOTS of the pair of instruments (this resolver + the sweep's direct
#: vocabulary detector). Rendered verbatim into the sweep's failure message AND the audit report,
#: because a limitation only its author reads is not a declared limitation — that is the whole
#: difference between "everything it sees is real" and "it sees everything". Each line is a write
#: shape CONFIRMED to evade the pair, not a hypothetical.
UNCLOSED_SHAPES = (
    "receiver is not a bare name: `self._store.backend.put(x)`, `self.backends[0].put(x)`, "
    "`foo().bar()` — the direct detector resolves at most two attribute levels and rule (e) "
    "binds bare names only",
    "receiver bound to a FACTORY rather than a constructor: `dst = make_transport(...)` then "
    "`dst.write_bundle(...)` — rule (e) binds `x = SomeClass(...)`, and infers no return types",
    "a method INHERITED from a base class: `self.x()` where `x` is defined on a superclass "
    "(stage 1b's behavioural harness is the other half of this one)",
    "a bound method taken as a value: `w = backend.put` then `w(item)`; and a callable passed as "
    "an argument and invoked by the callee (`gate.submit(commit=_commit)`)",
    "anything dynamic: `getattr(b, 'put')(item)`, a dispatch dict, a registry",
    "`open(p, mode)` where `mode` is not a literal AT the call site (a variable, a conditional)",
    "an aliased stdlib mutator: `from os import remove as _rm`, `import os as _os` then "
    "`_os.remove(p)`, `from shutil import rmtree` — the direct detector matches the bare "
    "`os.`/`shutil.` receiver only",
    "a constructor call is used to BIND a receiver (rule e) but is not itself emitted as an edge "
    "to `Class.__init__`; nor is a symbol RE-EXPORTED through an `__init__.py` followed to the "
    "module that defines it — both land as edges pointing at no definition, counted per run as "
    "`dangling_targets` and harmless to the closure, since a non-definition is never a write site",
    "two definitions sharing one QUALNAME in a module still share a key — measured and named per "
    "run by `qualname_collisions()`, never assumed absent",
)


#: THE COHERENCE CONTRACT'S DIRECTIONS, declared here and rendered into the sweep's failure message
#: and audit report — never re-derived by the next reader from the call site, which is what let
#: COHERENCE-CONTRACT-ONE-DIRECTIONAL survive (doc 84): the contract ran one direction, nobody had
#: written down which, and so nobody noticed the other one was missing.
CONTRACT_DIRECTIONS = (
    "A → B (the original): a site whose register entry CLAIMS WRITE-FREEDOM (UNGATED_BY_DESIGN) "
    "yet transitively REACHES a GATED or LEDGERED writer through the resolved call graph. This is "
    "the `_ask_group` shape, and it convicted `memory/migrate.py:migrate_memory` on its first run.",
    "B → A (added at 0.0.17 stage 1a-FU): a GATED entry one of whose DECLARED CALLERS is a call "
    "site that is NOT gate-enclosed — i.e. the entry's own caller list contradicts the header that "
    "classifies it. Facts derived per call site by `gate_enclosure`, never taken from the entry's "
    "prose; silenced only by a DECLARED exception carrying an expiry.",
    "NEITHER direction is a completeness claim. A → B sees only edges the five rules RESOLVE, and "
    "for most registered sites that is no edges at all. B → A sees only callers a human DECLARED "
    "as a file:line, because a caller named by role ('from store._commit') is not checkable — the "
    "count of entries whose caller claim is unpinnable this way is measured and printed.",
)


@dataclass
class WriteGraphResult:
    """The resolved intra-package call graph.

    `edges` maps a caller site to every site it calls through a RESOLVED edge. `unresolved_calls`
    is the count of call expressions no rule could resolve — reported, never hidden, because the
    honest reading of this instrument is "everything it sees is real", not "it sees everything"."""

    edges: Dict[Site, FrozenSet[Site]] = field(default_factory=dict)
    resolved_calls: int = 0
    unresolved_calls: int = 0
    #: how many of `resolved_calls` rule (e) explains — the receiver gap this stage closed. Kept
    #: separate so the report can say what the new rule actually bought, not just that it exists.
    receiver_calls: int = 0
    #: (file, qualname) -> how many DEFINITIONS still share that key, for the keys where that is
    #: more than one. The residual of the F1 fix, measured every run rather than assumed away.
    collisions: Dict[Site, int] = field(default_factory=dict)
    #: edge targets that name no `def` in the module they point at — a CLASS constructor
    #: (`Store()` resolves to `store.py:Store`, and this resolver does not follow it to
    #: `Store.__init__`), or a symbol RE-EXPORTED through an `__init__.py` the def does not live
    #: in. They are harmless to the closure — a target that is not a definition can never be a
    #: write site — but they are counted here rather than folded into `resolved_calls`, because
    #: "resolved" reading as "resolved to a real definition" is the kind of quiet inflation this
    #: whole stage exists to stop.
    dangling_targets: int = 0
    #: every RESOLVED call, as (caller site, LINE, target). The line is what lets a caller be
    #: located in the source rather than merely named — `gate_enclosure()` below needs it, and so
    #: does any claim of the form "this caller runs inside a gate", which is a property of the CALL
    #: SITE and not of the calling function.
    calls: List[Tuple[Site, int, Site]] = field(default_factory=list)
    #: the subset of `calls` rule (e) explains. `receiver_calls` is its length; both are kept
    #: because a COUNT with no list can only be re-asserted, never re-graded — the same rule doc 85
    #: §7b applies to a mutation score, applied to a resolver statistic.
    receiver_edges: List[Tuple[Site, int, Site]] = field(default_factory=list)

    def callees(self, site: Site) -> FrozenSet[Site]:
        return self.edges.get(site, frozenset())

    def call_lines(self, caller: Site, target: Site) -> Tuple[int, ...]:
        """Every LINE in `caller` that calls `target` through a resolved edge."""
        return tuple(sorted(ln for c, ln, t in self.calls if c == caller and t == target))

    def reverse(self) -> Dict[Site, Set[Site]]:
        rev: Dict[Site, Set[Site]] = {}
        for caller, callees in self.edges.items():
            for callee in callees:
                rev.setdefault(callee, set()).add(caller)
        return rev


@dataclass
class ClosureResult:
    """Which sites reach a durable write, and by what path.

    `direct` are the sites the vocabulary detector saw for itself. `derived` are the sites that
    reach one only by DELEGATING, each mapped to the shortest path that proves it — because a
    delegated classification the reader cannot trace is one they will rubber-stamp."""

    direct: FrozenSet[Site] = frozenset()
    derived: Dict[Site, Tuple[Site, ...]] = field(default_factory=dict)
    #: derived site -> every DIRECT writer it can reach (its classification is inherited from these)
    writers: Dict[Site, FrozenSet[Site]] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return len(self.direct) + len(self.derived)

    def path_text(self, site: Site) -> str:
        return " -> ".join(f"{rel}:{fn}" for rel, fn in self.derived.get(site, (site,)))


# ======================================================================================
# module bookkeeping — turning a package tree into (rel path, dotted module) both ways
# ======================================================================================

def _module_candidates(dotted: str, package: str) -> List[str]:
    """The rel paths a dotted module name could live at."""
    if dotted == package:
        return ["__init__.py"]
    if not dotted.startswith(package + "."):
        return []
    tail = dotted[len(package) + 1:].replace(".", "/")
    return [tail + ".py", tail + "/__init__.py"]


def _resolve_import_from(node: ast.ImportFrom, rel: str, package: str,
                         known: Set[str]) -> Optional[str]:
    """The rel path `from X import …` names, or None when X is outside the package."""
    if node.level:
        parts = rel.split("/")[:-1]
        climb = node.level - 1
        if climb:
            parts = parts[:-climb] if climb <= len(parts) else []
        dotted = ".".join([package] + parts + ([node.module] if node.module else []))
    else:
        dotted = node.module or ""
    for cand in _module_candidates(dotted, package):
        if cand in known:
            return cand
    return None


# ======================================================================================
# the builder
# ======================================================================================

def _parse_package(root: str) -> Dict[str, ast.Module]:
    trees: Dict[str, ast.Module] = {}
    for base, _dirs, files in os.walk(root):
        if "__pycache__" in base:
            continue
        for name in sorted(f for f in files if f.endswith(".py")):
            path = os.path.join(base, name)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            with open(path, encoding="utf-8") as fh:
                trees[rel] = ast.parse(fh.read(), filename=path)
    return trees


class _ModuleFacts:
    """What one module DEFINES and what names it has bound to package symbols/modules."""

    def __init__(self) -> None:
        self.defs: Set[str] = set()                      # every `def` QUALNAME, at any nesting
        self.def_counts: Dict[str, int] = {}             # qualname -> how many defs claim it
        self.classes: Dict[str, Set[str]] = {}           # class QUALNAME -> its own method names
        self.imported: Dict[str, Site] = {}              # local name -> (rel, symbol)   [rule b]
        self.modules: Dict[str, str] = {}                # local alias -> rel            [rule d]


def _scan_definitions(tree: ast.Module, facts: "_ModuleFacts") -> None:
    """Record every def/class QUALNAME, walking scopes rather than `ast.walk`.

    `ast.walk` flattens the tree and so cannot tell `SQLiteBackend.put` from `ObsidianBackend.put`
    — which is precisely the collapse the qualified key exists to undo. A def that appears twice
    under one qualname is COUNTED (`def_counts`), never silently deduplicated: that count is what
    `qualname_collisions()` reports."""
    def walk(node: ast.AST, stack: List[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                qual = ".".join(stack + [child.name])
                facts.classes.setdefault(qual, set()).update(
                    m.name for m in child.body
                    if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)))
                walk(child, stack + [child.name])
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = ".".join(stack + [child.name])
                facts.defs.add(qual)
                facts.def_counts[qual] = facts.def_counts.get(qual, 0) + 1
                walk(child, stack + [child.name])
            else:
                # not a scope of its own (an `if`, a `try`, a loop) — a def inside it belongs to
                # the SAME scope, so the stack does not grow
                walk(child, stack)

    walk(tree, [])


def _collect_facts(trees: Dict[str, ast.Module], package: str) -> Dict[str, _ModuleFacts]:
    known = set(trees)
    facts = {rel: _ModuleFacts() for rel in trees}
    for rel, tree in trees.items():
        f = facts[rel]
        _scan_definitions(tree, f)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                target = _resolve_import_from(node, rel, package, known)
                if target is not None:
                    for alias in node.names:
                        f.imported[alias.asname or alias.name] = (target, alias.name)
                elif node.level and node.module is None:
                    # `from . import sibling` — the name IS a module (rule d)
                    parts = rel.split("/")[:-1]
                    for alias in node.names:
                        stem = "/".join(parts + [alias.name])
                        for cand in (stem + ".py", stem + "/__init__.py"):
                            if cand in known:
                                f.modules[alias.asname or alias.name] = cand
                                break
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    for cand in _module_candidates(alias.name, package):
                        if cand in known:
                            f.modules[alias.asname or alias.name.split(".")[-1]] = cand
                            break

    # `from <pkg.sub> import name` where `name` is itself a MODULE, not a symbol — a very common
    # shape here (`from .memory import store`). Re-file it as a module alias so rule (d) applies.
    for rel, f in facts.items():
        for local, (target, symbol) in list(f.imported.items()):
            tf = facts[target]
            if (symbol in tf.defs or symbol in tf.classes
                    or any(symbol in ms for ms in tf.classes.values())):
                continue
            stem = target[:-3]
            if stem == "__init__":                  # `from . import sibling` at the package root
                stem = ""
            elif stem.endswith("/__init__"):
                stem = stem[: -len("/__init__")]
            prefix = (stem + "/") if stem else ""
            for cand in (prefix + symbol + ".py", prefix + symbol + "/__init__.py"):
                if cand in known:
                    f.modules[local] = cand
                    f.imported.pop(local, None)
                    break
    return facts


class _EdgeVisitor(ast.NodeVisitor):
    """Walks one module, emitting a resolved edge for every call one of the five rules explains.

    Scopes are tracked as a single stack of `(name, is_class)` so that a caller and a callee are
    keyed by the SAME qualified name — the property the whole join against the register rests on."""

    def __init__(self, rel: str, facts: Dict[str, _ModuleFacts], result: WriteGraphResult) -> None:
        self.rel, self.facts, self.result = rel, facts, result
        self.stack: List[Tuple[str, bool]] = []
        # rule (e): one binding frame per scope. name -> (rel, class qualname), or None for
        # "assigned something that is NOT a constructor" — recorded rather than dropped so a
        # rebinding SHADOWS an outer binding instead of silently inheriting it.
        self.frames: List[Dict[str, Optional[Tuple[str, str]]]] = [{}]

    # -- scope tracking -----------------------------------------------------------------
    def _qual(self) -> str:
        return ".".join(n for n, _ in self.stack) if self.stack else "<module>"

    def _enclosing_class(self) -> Optional[str]:
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][1]:
                return ".".join(n for n, _ in self.stack[: i + 1])
        return None

    def _push(self, name: str, is_class: bool) -> None:
        self.stack.append((name, is_class))
        self.frames.append({})

    def _pop(self) -> None:
        self.stack.pop()
        self.frames.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._push(node.name, True)
        self.generic_visit(node)
        self._pop()

    def visit_FunctionDef(self, node) -> None:
        self._push(node.name, False)
        self.generic_visit(node)
        self._pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    # -- rule (e) bookkeeping: which locals hold a mokata class instance -----------------
    def _class_of(self, value: ast.expr) -> Optional[Tuple[str, str]]:
        """The (rel, class qualname) a CONSTRUCTOR call yields, or None for anything else.

        Deliberately only a direct `SomeClass(...)`: a factory (`make_transport(...)`) would need
        return-type inference, and inferring one wrongly is worse than declaring the gap, which
        `UNCLOSED_SHAPES` does."""
        if not isinstance(value, ast.Call):
            return None
        fn, me = value.func, self.facts[self.rel]
        if isinstance(fn, ast.Name):
            if fn.id in me.imported:
                trel, symbol = me.imported[fn.id]
                return (trel, symbol) if symbol in self.facts[trel].classes else None
            return (self.rel, fn.id) if fn.id in me.classes else None
        if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name):
            trel = me.modules.get(fn.value.id)
            if trel and fn.attr in self.facts[trel].classes:
                return (trel, fn.attr)
        return None

    def _bind(self, targets: Iterable[ast.expr], value: Optional[ast.expr]) -> None:
        bound = self._class_of(value) if value is not None else None
        for tgt in targets:
            if isinstance(tgt, ast.Name):
                self.frames[-1][tgt.id] = bound

    def visit_Assign(self, node: ast.Assign) -> None:
        self.generic_visit(node)                    # the RHS's own calls are edges too
        self._bind(node.targets, node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.generic_visit(node)
        self._bind([node.target], node.value)

    def _binding(self, name: str) -> Optional[Tuple[str, str]]:
        """Rule (e)'s lookup: the innermost frame that MENTIONS `name` wins — including when it
        mentions it as None, which is how a rebind to a non-constructor shadows an outer binding
        instead of falling through to it.

        Class frames are skipped for the same reason `_lexical` skips class scopes: a method body
        cannot reach a class-body name by bare name, so a binding recorded there must not be
        offered to one. Frame j+1 belongs to scope j; frame 0 is the module."""
        for j in range(len(self.frames) - 1, -1, -1):
            if j and self.stack[j - 1][1]:          # that frame is a CLASS body
                continue
            if name in self.frames[j]:
                return self.frames[j][name]
        return None

    # -- the five rules -----------------------------------------------------------------
    def _lexical(self, name: str, defs: Set[str]) -> Optional[str]:
        """Rule (a): the qualname a BARE call resolves to, climbing enclosing FUNCTION scopes.

        Class scopes are skipped because Python skips them: a method body cannot reach a sibling
        method by bare name, and pretending it can is a bare-name match wearing a scope."""
        for i in range(len(self.stack), -1, -1):
            if i and self.stack[i - 1][1]:          # that candidate's parent scope is a class
                continue
            cand = ".".join([n for n, _ in self.stack[:i]] + [name])
            if cand in defs:
                return cand
        return None

    def _resolve(self, fn: ast.expr) -> Tuple[Optional[Site], bool]:
        """(site, was_rule_e). `None` means no rule explains this call."""
        me = self.facts[self.rel]
        if isinstance(fn, ast.Name):
            if fn.id in me.imported:                       # (b) from X import name
                return me.imported[fn.id], False
            qual = self._lexical(fn.id, me.defs)           # (a) lexically visible def
            return ((self.rel, qual) if qual else None), False
        if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name):
            base = fn.value.id
            if base == "self":                             # (c) self.method in the enclosing class
                cls = self._enclosing_class()
                if cls and fn.attr in me.classes.get(cls, set()):
                    return (self.rel, f"{cls}.{fn.attr}"), False
                return None, False
            if base in me.modules:                         # (d) imported module alias
                return (me.modules[base], fn.attr), False
            held = self._binding(base)                     # (e) a local holding a mokata instance
            if held is not None:
                trel, cls = held
                if fn.attr in self.facts[trel].classes.get(cls, set()):
                    return (trel, f"{cls}.{fn.attr}"), True
        return None, False

    def visit_Call(self, node: ast.Call) -> None:
        target, by_receiver = self._resolve(node.func)
        if target is None:
            self.result.unresolved_calls += 1
        else:
            self.result.resolved_calls += 1
            caller = (self.rel, self._qual())
            record = (caller, node.lineno, target)
            self.result.calls.append(record)
            if by_receiver:
                self.result.receiver_calls += 1
                self.result.receiver_edges.append(record)
            cur = self.result.edges.get(caller, frozenset())
            self.result.edges[caller] = cur | {target}
        self.generic_visit(node)


def build_call_graph(root: str, *, package: str = "mokata") -> WriteGraphResult:
    """The resolved intra-package call graph for the package rooted at `root`.

    `build_*` per doc 85 §3: it degrades to a FLOOR (an edge it cannot resolve is simply absent and
    counted) rather than raising. A syntactically broken module would raise from `ast.parse`, which
    is correct — the audit must not quietly skip a file it could not read."""
    trees = _parse_package(root)
    facts = _collect_facts(trees, package)
    result = WriteGraphResult()
    for rel, tree in trees.items():
        _EdgeVisitor(rel, facts, result).visit(tree)
    result.collisions = qualname_collisions(facts)
    result.dangling_targets = len({t for targets in result.edges.values() for t in targets
                                   if t[0] not in facts or t[1] not in facts[t[0]].defs})
    return result


def qualname_collisions(facts: Dict[str, _ModuleFacts]) -> Dict[Site, int]:
    """The keys that STILL cover more than one definition after qualification.

    The bare-name key collapsed 15 of 112 registered keys, one of them onto eight distinct commit
    closures. Qualifying the key removes almost all of that, but not the case where two definitions
    genuinely share a scope AND a name — three `def _commit()`s in one function body, or a def
    rebound in an `if`/`else`. That residual is REPORTED, per run, per key, because "the collapse is
    fixed" and "the collapse is smaller and here is exactly what is left" are different claims and
    only the second one is true."""
    return {(rel, qual): n
            for rel, f in facts.items()
            for qual, n in f.def_counts.items() if n > 1}


# ======================================================================================
# the closure, and the coherence check it makes possible
# ======================================================================================

def close_over_writers(graph: WriteGraphResult, direct: Iterable[Site]) -> ClosureResult:
    """Every site that reaches a direct durable writer, with the shortest path that proves it.

    Full transitive closure to a fixpoint — no depth cap, no same-module restriction. Cycles
    terminate because a site is enqueued only the first time it is reached (for `derived`) and only
    when its reachable-writer set actually GROWS (for `writers`)."""
    direct_set = frozenset(direct)
    rev = graph.reverse()

    # shortest delegation path, breadth-first backwards from every direct writer at once
    path: Dict[Site, Tuple[Site, ...]] = {d: (d,) for d in direct_set}
    queue = deque(sorted(direct_set))
    while queue:
        cur = queue.popleft()
        for caller in sorted(rev.get(cur, ())):
            if caller not in path:
                path[caller] = (caller,) + path[cur]
                queue.append(caller)

    # which direct writers each site can reach — the set its classification is inherited from
    writers: Dict[Site, Set[Site]] = {d: {d} for d in direct_set}
    work = deque(sorted(direct_set))
    while work:
        cur = work.popleft()
        payload = writers[cur]
        for caller in sorted(rev.get(cur, ())):
            have = writers.setdefault(caller, set())
            if not payload <= have:
                have |= payload
                work.append(caller)

    derived = {site: p for site, p in path.items() if site not in direct_set}
    return ClosureResult(
        direct=direct_set,
        derived=dict(sorted(derived.items())),
        writers={s: frozenset(w) for s, w in writers.items()},
    )


def derive_callers(graph: WriteGraphResult, sites: Iterable[Site]) -> Dict[Site, Tuple[Site, ...]]:
    """The RESOLVED callers of each site — a caller list DERIVED rather than asserted in prose.

    CALLER-LIST-UNPINNED (doc 84): several register entries earn their classification from a
    sentence like "sole callers are X and Y" that nothing checks, and every such sentence checked
    at the 0.0.17 review was false. This is the mechanism that stops the fifth: pin what the graph
    derives, and a new caller reds the sweep instead of quietly falsifying a justification.

    ⚠ WHAT THIS IS AND IS NOT. It is a FLOOR — the callers the five resolution rules can see — and
    for most registered sites that floor is EMPTY, because the calling shape is one of
    `UNCLOSED_SHAPES` (a contract dispatch through `self.backend.put(...)`, a factory-bound
    transport, a callable handed to a gate). An empty derived list therefore means "the resolver
    sees no caller", NEVER "this site has no caller", and the two readings are exactly the gap
    between "everything it sees is real" and "it sees everything". The count of registered sites
    whose caller list is unpinnable this way is MEASURED and reported, not implied."""
    rev = graph.reverse()
    return {site: tuple(sorted(rev.get(site, ()))) for site in sorted(set(sites))}


# ======================================================================================
# WHERE A CALL RUNS — gate enclosure (0.0.17 stage 1a-FU)
# ======================================================================================

#: The DECLARED gate runners: functions whose job is to execute a commit callable under the
#: WriteGate. Rendered into the sweep's report, because a hand-maintained set that only its author
#: reads is the same undeclared limitation `UNCLOSED_SHAPES` exists to stop.
GATE_RUNNERS = ("<any call>.commit=<callable>", "_gated_write(…, <lambda>, …)",
                "gated_reversible_write(…, <lambda>, …)")
_RUNNER_NAMES = frozenset({"_gated_write", "gated_reversible_write"})

#: What the gate-enclosure detector CANNOT establish — declared for the same reason
#: `UNCLOSED_SHAPES` is, and each line is a shape MEASURED on the live tree, not a hypothetical.
UNSEEN_ENCLOSURE = (
    "a commit callable handed to a wrapper NOT in `GATE_RUNNERS` — the callable is invisible and "
    "every call inside it reads as ungated (a false RED, never a false green)",
    "a helper called from BOTH a commit callable and an ungated path: it is deliberately NOT "
    "counted gate-run, because 'it can run under a gate' is not 'it runs under a gate', and the "
    "safe direction of error here is to flag, not to excuse",
    "a chain DEEPER than one resolved call from a commit callable — enclosure is not closed "
    "transitively, for the same reason: depth buys coverage by trading away the guarantee",
)


@dataclass
class GateEnclosure:
    """Which CALL SITES execute inside a callable the WriteGate runs.

    `GATED`'s header defines its class by how the write is REACHED — "executes inside a `commit=`
    callable handed to `WriteGate.submit` (directly, or as the sole callee of one)". That is a
    property of the CALL SITE, not of the function, so it is answered per `(file, line)`.

    It exists so that a claim of the form "this caller is ungated" is DERIVED FROM SOURCE rather
    than believed. The consequence that matters: a declared exception resting on such a claim
    EXPIRES BY ITSELF the moment the claim stops being true — the fix flips this predicate and the
    stale exception reds, instead of waiting for someone to remember it."""

    #: rel -> the (start, end) line spans of every commit callable handed to a gate runner
    commit_spans: Dict[str, Tuple[Tuple[int, int], ...]] = field(default_factory=dict)
    #: the functions reached from inside a commit span BY EVERY resolved call to them
    gate_run: FrozenSet[Site] = frozenset()
    #: rel -> line -> the qualname of the innermost def enclosing that line
    _owner: Dict[str, Dict[int, str]] = field(default_factory=dict)
    #: rel -> {line: called name}, every call in the package keyed by the name it is spelled with
    _named_calls: Dict[str, Dict[int, FrozenSet[str]]] = field(default_factory=dict)

    def in_commit_span(self, rel: str, lineno: int) -> bool:
        return any(lo <= lineno <= hi for lo, hi in self.commit_spans.get(rel, ()))

    def is_gate_run(self, rel: str, lineno: int) -> bool:
        """Does the call at `rel:lineno` execute inside a callable the gate runs?

        True when the call is lexically INSIDE a commit callable, or when the def that owns it is
        one the gate runs. False is honestly "not established", never "proved ungated" — see
        `UNSEEN_ENCLOSURE`."""
        if self.in_commit_span(rel, lineno):
            return True
        owner = self._owner.get(rel, {}).get(lineno)
        return owner is not None and (rel, owner) in self.gate_run

    def owner_of(self, rel: str, lineno: int) -> Optional[str]:
        """The qualname of the innermost def enclosing `rel:lineno`, or None at module level.

        A read-only view of the same `_owner` map `is_gate_run` consults. Exposed (0.0.17 stage 4)
        so `_gatereach` can ask "which function does this call site belong to" without reaching
        into private state or building a second owner index."""
        return self._owner.get(rel, {}).get(lineno)

    def call_names(self, rel: str, lineno: int) -> FrozenSet[str]:
        """Every name a call on this line is spelled with — `x.write_bundle(…)` gives
        `write_bundle`. Used to check that a DECLARED caller's file:line really does call the thing
        it is declared to call, so a stale line number cannot keep an exception alive."""
        return self._named_calls.get(rel, {}).get(lineno, frozenset())


def _commit_spans(tree: ast.Module) -> List[Tuple[int, int]]:
    """The line spans of the commit callables this module hands to a gate runner.

    Two shapes, and only two: an expression passed as the `commit=` KEYWORD (the WriteGate's own
    signature), and a `lambda` passed to one of the declared `_RUNNER_NAMES` wrappers positionally
    — which is how `mcp/tools_share.py` reaches the gate and is why the keyword alone is not
    enough. Anything else is declared unseen rather than guessed at."""
    spans: List[Tuple[int, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg == "commit":
                spans.append((kw.value.lineno, getattr(kw.value, "end_lineno", kw.value.lineno)))
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name in _RUNNER_NAMES:
            for arg in list(node.args) + [k.value for k in node.keywords]:
                if isinstance(arg, ast.Lambda):
                    spans.append((arg.lineno, getattr(arg, "end_lineno", arg.lineno)))
    return spans


def gate_enclosure(root: str, *, package: str = "mokata",
                   graph: Optional[WriteGraphResult] = None) -> GateEnclosure:
    """Build the gate-enclosure map for the package rooted at `root`.

    `gate_run` holds a function only when EVERY resolved call to it lands inside a commit span. A
    helper that is also reachable from an ungated path is left out on purpose: the question this
    answers is "does this call run under a gate", and "sometimes" is not "yes"."""
    trees = _parse_package(root)
    graph = build_call_graph(root, package=package) if graph is None else graph

    spans: Dict[str, Tuple[Tuple[int, int], ...]] = {}
    owner: Dict[str, Dict[int, str]] = {}
    named: Dict[str, Dict[int, Set[str]]] = {}
    for rel, tree in trees.items():
        found = _commit_spans(tree)
        if found:
            spans[rel] = tuple(sorted(found))
        lines: Dict[int, str] = {}
        calls: Dict[int, Set[str]] = {}

        def walk(node: ast.AST, stack: List[str]) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    inner = stack + [child.name]
                    if not isinstance(child, ast.ClassDef):
                        for ln in range(child.lineno,
                                        getattr(child, "end_lineno", child.lineno) + 1):
                            lines[ln] = ".".join(inner)   # innermost def wins: it is visited last
                    walk(child, inner)
                else:
                    if isinstance(child, ast.Call):
                        name = getattr(child.func, "id", None) or getattr(child.func, "attr", None)
                        if name:
                            calls.setdefault(child.lineno, set()).add(name)
                    walk(child, stack)

        walk(tree, [])
        owner[rel] = lines
        named[rel] = {ln: frozenset(ns) for ln, ns in calls.items()}

    enclosure = GateEnclosure(commit_spans=spans, _owner=owner,
                              _named_calls={r: dict(v) for r, v in named.items()})
    reached: Dict[Site, List[bool]] = {}
    for _caller, lineno, target in graph.calls:
        rel = _caller[0]
        reached.setdefault(target, []).append(enclosure.in_commit_span(rel, lineno))
    enclosure.gate_run = frozenset(t for t, seen in reached.items() if all(seen))
    return enclosure


def path_between(graph: WriteGraphResult, start: Site,
                 targets: Iterable[Site]) -> Tuple[Site, ...]:
    """The shortest FORWARD call path from `start` into `targets`, or () if there is none."""
    goal = set(targets) - {start}
    if not goal:
        return ()
    seen: Dict[Site, Tuple[Site, ...]] = {start: (start,)}
    queue = deque([start])
    while queue:
        cur = queue.popleft()
        for callee in sorted(graph.callees(cur)):
            if callee in seen:
                continue
            seen[callee] = seen[cur] + (callee,)
            if callee in goal:
                return seen[callee]
            queue.append(callee)
    return ()


def coherence_violations(graph: WriteGraphResult, claims_write_freedom: Iterable[Site],
                         governed: Iterable[Site]) -> List[Tuple[Site, Tuple[Site, ...]]]:
    """THE COHERENCE CONTRACT. Every site whose register entry CLAIMS it is not a governed durable
    write, yet which transitively REACHES one.

    That combination is a contradiction, not a gap: the register says "nothing here for a human to
    approve" while the call graph says the function lands approved-content bytes through a delegate.
    It is the `_ask_group` shape exactly — a charter of write-freedom with a delegated write under
    it — and it is the one thing a vocabulary scan can never notice, because neither half is wrong
    on its own."""
    targets = set(governed)
    out: List[Tuple[Site, Tuple[Site, ...]]] = []
    for site in sorted(set(claims_write_freedom)):
        found = path_between(graph, site, targets)
        if found:
            out.append((site, found))
    return out


def ungated_caller_violations(enclosure: GateEnclosure, declared: Dict[Site, Sequence[Site]],
                              governed: Iterable[Site],
                              excused: Iterable[Site]) -> List[Tuple[Site, Site]]:
    """DIRECTION B. Every GATED entry with a DECLARED caller whose call site is not gate-enclosed.

    The inverse of `coherence_violations`, and the direction that was missing: that one asks
    whether a write-free CLAIM reaches a governed write, this one asks whether a GOVERNED claim is
    reached from somewhere ungoverned. `GATED`'s header classifies by how the write is REACHED, so
    an entry in it whose own caller list names an ungated call site is false by its own register's
    definition — and no amount of resolved-call-graph work can see it, because those callers reach
    the site through shapes the resolver cannot resolve (measured: BOTH `write_bundle` twins have
    ZERO resolvable callers).

    `excused` are the declared exceptions. They are passed IN rather than read from a table here,
    so that the set of exceptions is a fact about the register — reviewable in one place, and
    pinnable by count — instead of a flag scattered across the entries it excuses."""
    skip, out = set(excused), []
    for site in sorted(set(declared) & set(governed)):
        if site in skip:
            continue
        for rel, lineno in declared[site]:
            if not enclosure.is_gate_run(rel, lineno):
                out.append((site, (rel, lineno)))
    return out


def render_site(site: Site) -> str:
    return f"{site[0]}:{site[1]}"


def render_path(path: Sequence[Site]) -> str:
    """Human string, never writes (doc 85 §3 `render_*`)."""
    return " -> ".join(f"{rel}:{fn}" for rel, fn in path)
