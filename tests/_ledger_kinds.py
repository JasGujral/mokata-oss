"""0.0.21 stage 10 — DERIVE every audit-ledger `kind` the tree can write.

R1.S1b projects ledger entries into typed events, and a projection needs to know what it is
projecting FROM. A hand-written list of kinds goes stale the first time someone adds a
`record("something_new", …)`, and then the projection silently drops it — which is exactly the
failure the projection exists to prevent. ⭐ **So the vocabulary is swept out of the tree and
the register is graded against the sweep** (the SI.6 / D5 pattern, third use), and a new kind
REDS until somebody classifies it.

THREE WAYS A KIND REACHES `AuditLedger.record`, and all three are resolved here:

  1. a string literal  — `ledger.record("write_gate", …)`;
  2. a module constant — `ledger.record(LEDGER_KIND, …)` where `LEDGER_KIND = "write_approval"`;
  3. a FORWARDER       — a helper taking `kind` and passing it through (`EnforcementGate._record`,
     `WorktreeManager._log`, `MemoryStore._record_review`). One more hop resolves the literals
     its own callers pass.

⛔ **THE FORWARDER HOP IS SCOPED TO THE DEFINING MODULE, AND THE FIRST DRAFT WAS NOT.**
`_log` and `_record` are among the most common private method names in this tree, so a hop
keyed on the NAME alone matched every unrelated `self._log("accept", …)` and `self._record(
"root_cause", …)` it could find: the sweep reported **75** kinds, of which ten — `accept`,
`after`, `apply_change`, `baseline`, `escalate`, `hypothesize`, `propose_fix`, `root_cause`,
`rule_out`, `synthesis updated` — were other functions entirely. ⭐ This is stage 07's `.open`
lesson in a different costume: *a detector whose output is partly false teaches its reader to
discount all of it*, and here the cost would be ten phantom kinds in a register somebody has to
classify. All three real forwarders are called only from inside the module that defines them,
so the hop is keyed on `(module, function)`.

⚠ The sweep reports what it could NOT resolve rather than dropping it, because a kind this
reader cannot see is the one case where the register's totality claim would be false while
looking true (§7e).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import ast
import os

import _support  # noqa: F401  (puts src/ on the path)

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "mokata")

# The receiver names that mean "this is the audit ledger", used to tell `ledger.record(...)` from
# `SavingsTracker.record(...)` — a real false positive the first draft of this sweep produced.
_LEDGER_RECEIVERS = ("ledger", "_ledger", "self.ledger", "self._ledger", "led", "AuditLedger")


def _is_ledger_record(node):
    """True when this call is `<something ledger-ish>.record(...)`."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
        return False
    if node.func.attr != "record" or not node.args:
        return False
    recv = node.func.value
    if isinstance(recv, ast.Name):
        return recv.id in _LEDGER_RECEIVERS
    if isinstance(recv, ast.Attribute):
        return recv.attr in ("ledger", "_ledger")
    if isinstance(recv, ast.Call):            # AuditLedger.from_mokata_dir(...).record(...)
        f = recv.func
        return isinstance(f, ast.Attribute) and f.attr in ("from_mokata_dir", "for_root")
    return False


def _module_consts(tree):
    out = {}
    for n in tree.body:
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            if isinstance(n.value, ast.Constant) and isinstance(n.value.value, str):
                out[n.targets[0].id] = n.value.value
    return out


def _enclosing_func(tree, node):
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for sub in ast.walk(fn):
                if sub is node:
                    return fn
    return None


def sweep():
    """Return `(kinds, forwarders, unresolved)`.

    `kinds` — every kind string the tree can write, as a sorted tuple.
    `forwarders` — `{(module, function name): [param name]}` for helpers that pass a kind
    through. Keyed by MODULE as well as name — see the docstring above for what the name alone
    cost.
    `unresolved` — `[(relpath, lineno, expression)]` the sweep could not resolve. NOT dropped."""
    # CORPUS: THE WORKING TREE — the question is "what kinds can THIS checkout write", which is
    # about the source on disk and not about what git has been told. An untracked module that
    # calls `ledger.record` still writes that kind at runtime, so the index would be the wrong
    # ground truth here (`_corpus_sweep`'s own distinction).
    trees = {}
    for dp, _dn, fn in os.walk(SRC):
        if "__pycache__" in dp:
            continue
        for f in sorted(fn):
            if not f.endswith(".py"):
                continue
            p = os.path.join(dp, f)
            try:
                with open(p, encoding="utf-8") as fh:
                    trees[_support.posix_rel(p, SRC)] = ast.parse(fh.read())
            except (OSError, SyntaxError):
                continue

    kinds, forwarders, unresolved = set(), {}, []
    # pass 1 — direct literals, module constants, and the forwarders
    for rel, tree in trees.items():
        consts = _module_consts(tree)
        for node in ast.walk(tree):
            if not _is_ledger_record(node):
                continue
            a0 = node.args[0]
            if isinstance(a0, ast.Constant) and isinstance(a0.value, str):
                kinds.add(a0.value)
                continue
            if isinstance(a0, ast.Name):
                if a0.id in consts:
                    kinds.add(consts[a0.id])
                    continue
                owner = _enclosing_func(tree, node)
                if owner is not None and a0.id in [p.arg for p in owner.args.args]:
                    forwarders.setdefault((rel, owner.name), []).append(a0.id)
                    continue
            unresolved.append((rel, node.lineno, ast.dump(a0)[:60]))

    # pass 2 — the literals a forwarder's own callers pass
    for rel, tree in trees.items():
        consts = _module_consts(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and node.args):
                continue
            name = node.func.attr if isinstance(node.func, ast.Attribute) else (
                node.func.id if isinstance(node.func, ast.Name) else "")
            if (rel, name) not in forwarders:
                continue
            a0 = node.args[0]
            if isinstance(a0, ast.Constant) and isinstance(a0.value, str):
                kinds.add(a0.value)
            elif isinstance(a0, ast.Name) and a0.id in consts:
                kinds.add(consts[a0.id])
    return tuple(sorted(kinds)), forwarders, unresolved
