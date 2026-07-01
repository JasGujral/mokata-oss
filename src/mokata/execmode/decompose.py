"""Stage 54f — assisted task decomposition + parallel-plan confirm.

The fan-out *engine* already exists (E2/E3 isolation + two-stage review, E8 the
parallel-vs-sequential selector, F1 the cost estimate, the orchestrator's degrade-clean
`run_tasks`). What was missing is **splitting the approved work into the tasks it runs**.

This module is that splitter — and nothing more. From the emitted spec's acceptance
criteria it proposes one INDEPENDENT subtask per AC, infers DEPENDENCIES (subtasks that
must stay sequential because they touch the same symbol/file, or the code graph links
them), and produces a legible plan. The plan is **read-only** until a human confirms it
(`confirm_decomposition`, gated + audit-logged); only then do the confirmed tasks flow into
the EXISTING `resolve_execution_choice` → `run_tasks` path (`run_decomposition`).

Conservative by construction: it NEVER silently parallelizes work that might be dependent.
Shared symbols/files → a `depends_on` edge → kept ordered. With no code graph wired,
independence is lexical-only and *unverified*, so the recommendation stays sequential and
concurrent fan-out is withheld — the human can still choose parallel after reviewing.

Pure/deterministic where it derives. Clean-room (no superpowers dependency); Apache-2.0.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from .selector import ExecutionChoice, resolve_execution_choice
from .tasks import Task

# Extensions that mark a dotted token as a FILE (vs a qualified symbol like `User.save`).
_FILE_EXTS = (
    "py", "pyi", "js", "jsx", "ts", "tsx", "go", "rs", "java", "rb", "c", "h", "hpp",
    "cpp", "cc", "cs", "kt", "swift", "php", "scala", "md", "rst", "json", "yaml", "yml",
    "toml", "cfg", "ini", "txt", "sh", "sql", "html", "css",
)
_BACKTICK = re.compile(r"`([^`]+)`")
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_./-]*")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_HAS_UPPER_CAMEL = re.compile(r"[a-z][A-Z]|^[A-Z][a-z]")

# Frugal cap on graph lookups during dependency inference (P11 — bounded, never a corpus sweep).
_MAX_GRAPH_SYMBOLS = 60


def _looks_like_file(token: str) -> bool:
    if "/" in token:
        return True
    if "." not in token:
        return False
    ext = token.rsplit(".", 1)[-1].lower()
    return ext in _FILE_EXTS


def _is_codey(token: str) -> bool:
    """A free-text token worth treating as a symbol: snake_case, camelCase/PascalCase, or
    dotted-qualified. Plain lowercase English words are ignored (they'd create false overlap)."""
    if len(token) < 3:
        return False
    if "_" in token or "." in token:
        return True
    return bool(_HAS_UPPER_CAMEL.search(token))


def extract_refs(text: str) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """Deterministically pull (symbols, files) an AC text references.

    Files = path-like / known-extension tokens. Symbols = code-like identifiers (from
    backticks always, from free text when snake/camel/dotted), plus the head component of a
    qualified name (so `User.save` and `User.delete` both surface `User`). Sorted + deduped."""
    symbols: Set[str] = set()
    files: Set[str] = set()

    def _consume(token: str, backticked: bool) -> None:
        token = token.strip("`.,;:()[]{}<>\"'").lstrip("./")
        if not token:
            return
        if _looks_like_file(token):
            files.add(token)
            return
        # A dotted symbol (User.save): keep the whole token and each identifier component.
        if "." in token:
            for part in [token] + token.split("."):
                ident = part
                if len(ident) >= 3 and (backticked or _is_codey(ident) or "." in part):
                    symbols.add(ident)
            return
        if backticked:
            if len(token) >= 3 and _IDENT.fullmatch(token):
                symbols.add(token)
        elif _is_codey(token):
            symbols.add(token)

    for span in _BACKTICK.findall(text or ""):
        for tok in _TOKEN.findall(span):
            _consume(tok, backticked=True)
    # Free text outside backticks (drop the backticked spans first to avoid double-count).
    free = _BACKTICK.sub(" ", text or "")
    for tok in _TOKEN.findall(free):
        _consume(tok, backticked=False)

    return tuple(sorted(symbols)), tuple(sorted(files))


@dataclass
class Subtask:
    """One proposed unit of work, traced to the AC it implements."""

    id: str
    ac_id: str
    description: str
    symbols: Tuple[str, ...] = ()
    files: Tuple[str, ...] = ()
    depends_on: Tuple[str, ...] = ()      # ids of subtasks that must precede this one

    @classmethod
    def from_ac(cls, ac: Any) -> "Subtask":
        text = (getattr(ac, "text", "") or "").strip() or ac.id
        symbols, files = extract_refs(getattr(ac, "text", "") or "")
        return cls(id=f"task-{ac.id}", ac_id=ac.id, description=text,
                   symbols=symbols, files=files)

    def to_task(self) -> Task:
        """Convert to the engine's Task — its isolated context names the AC + the surface it
        touches, so a fresh subagent is grounded (E2)."""
        bits = [f"Acceptance criterion {self.ac_id}."]
        if self.symbols:
            bits.append("symbols: " + ", ".join(self.symbols))
        if self.files:
            bits.append("files: " + ", ".join(self.files))
        return Task(id=self.id, description=self.description, context=" ".join(bits))

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "ac_id": self.ac_id, "description": self.description,
                "symbols": list(self.symbols), "files": list(self.files),
                "depends_on": list(self.depends_on)}


@dataclass
class DecompositionPlan:
    """The proposed split + dependency plan. Read-only until a human confirms it."""

    subtasks: List[Subtask] = field(default_factory=list)
    graph_backed: bool = False            # a real code graph verified the edges
    spec_title: str = ""
    warnings: List[str] = field(default_factory=list)

    # ----- derived views (pure) --------------------------------------------------------
    @property
    def dependency_count(self) -> int:
        return sum(len(s.depends_on) for s in self.subtasks)

    @property
    def has_dependencies(self) -> bool:
        return self.dependency_count > 0

    @property
    def independent(self) -> List[Subtask]:
        """Subtasks that neither depend on another nor are depended upon — the parallel
        candidates."""
        depended = {d for s in self.subtasks for d in s.depends_on}
        return [s for s in self.subtasks if not s.depends_on and s.id not in depended]

    @property
    def fanout_safe(self) -> bool:
        """Concurrent fan-out is safe ONLY when the graph verified independence AND no
        dependency edges exist. Without a graph we never claim fan-out is safe."""
        return self.graph_backed and not self.has_dependencies and len(self.subtasks) > 1

    @property
    def recommended_parallel(self) -> bool:
        return self.fanout_safe

    def tasks(self) -> List[Task]:
        return [s.to_task() for s in self.subtasks]

    def subset(self, keep_ids: Sequence[str]) -> "DecompositionPlan":
        """A human EDIT: keep only the named subtasks, pruning dependency edges to dropped
        ones. Re-derives nothing else (the kept subtasks keep their refs)."""
        keep = [s for s in self.subtasks if s.id in set(keep_ids)]
        kept_ids = {s.id for s in keep}
        pruned = [Subtask(s.id, s.ac_id, s.description, s.symbols, s.files,
                          tuple(d for d in s.depends_on if d in kept_ids)) for s in keep]
        return DecompositionPlan(subtasks=pruned, graph_backed=self.graph_backed,
                                 spec_title=self.spec_title, warnings=list(self.warnings))

    def to_dict(self) -> Dict[str, Any]:
        return {"spec_title": self.spec_title, "graph_backed": self.graph_backed,
                "subtasks": [s.to_dict() for s in self.subtasks],
                "dependency_count": self.dependency_count,
                "fanout_safe": self.fanout_safe,
                "recommended_parallel": self.recommended_parallel,
                "warnings": list(self.warnings)}

    def render(self, ascii_only: bool = False) -> str:
        """Compact, legible split (54c style). Read-only formatting of derived values."""
        bullet = "-" if ascii_only else "•"
        arrow = "->" if ascii_only else "↳"
        backend = "code graph" if self.graph_backed else "grep floor (lexical)"
        by_id = {s.id: s for s in self.subtasks}
        if not self.subtasks:
            return ("mokata decompose: nothing to split — "
                    + (self.warnings[0] if self.warnings else "no acceptance criteria."))
        lines = [f"mokata decompose — {len(self.subtasks)} subtask(s) from spec "
                 f"'{self.spec_title}' [{backend}]:"]
        for s in self.subtasks:
            surf = []
            if s.symbols:
                surf.append("symbols: " + ", ".join(s.symbols))
            if s.files:
                surf.append("files: " + ", ".join(s.files))
            tail = ("  {" + " · ".join(surf) + "}") if surf else ""
            lines.append(f"  {bullet} {s.id}  [{s.ac_id}] {s.description}{tail}")
            for dep in s.depends_on:
                shared = sorted((set(s.symbols) & set(by_id[dep].symbols))
                                | (set(s.files) & set(by_id[dep].files)))
                why = (" (shares: " + ", ".join(shared) + ")") if shared else ""
                lines.append(f"      {arrow} depends on {dep}{why}")
        rec = ("parallel candidate — {0} independent subtask(s) (graph-verified)".format(
                   len(self.independent))
               if self.recommended_parallel else "sequential gated flow (default)")
        lines.append(f"  recommendation: {rec}")
        for w in self.warnings:
            lines.append(f"  ! {w}")
        return "\n".join(lines)


# --------------------------------------------------------------------------- decompose
def _graph_expand(symbols: Sequence[str], layer: Any) -> Tuple[Dict[str, Set[str]], bool]:
    """Expand each symbol with the code graph's blast-radius neighbours, so a dependency the
    lexical floor would miss still gets caught. Returns (expansion, graph_backed). Frugal:
    one bounded query per unique symbol; any failure/degrade is skipped (degrade-clean)."""
    expansion: Dict[str, Set[str]] = {}
    graph_backed = False
    if layer is None or not getattr(layer, "uses_graph", False):
        return expansion, False
    for sym in list(symbols)[:_MAX_GRAPH_SYMBOLS]:
        try:
            res = layer.blast_radius(sym)
        except Exception:
            continue
        if res is None or getattr(res, "degraded", False):
            continue
        graph_backed = True
        related = {sym}
        for ref in getattr(res, "references", []) or []:
            if getattr(ref, "symbol", None):
                related.add(ref.symbol)
        expansion[sym] = related
    return expansion, graph_backed


def _expanded_symbols(sub: Subtask, expansion: Dict[str, Set[str]]) -> Set[str]:
    out: Set[str] = set(sub.symbols)
    for sym in sub.symbols:
        out |= expansion.get(sym, set())
    return out


def decompose(spec: Any, layer: Any = None) -> DecompositionPlan:
    """Propose an independent-subtask split of the emitted spec's ACs + a dependency plan.

    One subtask per acceptance criterion. Two subtasks are kept SEQUENTIAL (a `depends_on`
    edge, later → earlier so declared order holds) when they share a symbol or file — using
    the code graph to expand each symbol's neighbourhood when one is wired, else the lexical
    floor. Degrade-clean: no spec/ACs → an empty plan with a friendly warning."""
    if spec is None or not getattr(spec, "criteria", None):
        return DecompositionPlan(warnings=["no emitted spec with acceptance criteria to "
                                           "decompose — run /mokata:spec first."])

    subtasks = [Subtask.from_ac(ac) for ac in spec.criteria]
    all_symbols = sorted({s for st in subtasks for s in st.symbols})
    expansion, graph_backed = _graph_expand(all_symbols, layer)

    exp = {st.id: _expanded_symbols(st, expansion) for st in subtasks}
    for j in range(len(subtasks)):
        deps: List[str] = []
        for i in range(j):
            shares_symbol = bool(exp[subtasks[j].id] & exp[subtasks[i].id])
            shares_file = bool(set(subtasks[j].files) & set(subtasks[i].files))
            if shares_symbol or shares_file:
                deps.append(subtasks[i].id)
        subtasks[j].depends_on = tuple(deps)

    warnings: List[str] = []
    if not graph_backed:
        warnings.append("no code graph wired — task independence is lexical-only and "
                        "UNVERIFIED; the sequential gated flow is recommended (you can still "
                        "choose parallel after reviewing the split).")
    dep_count = sum(len(s.depends_on) for s in subtasks)
    if dep_count:
        warnings.append(f"{dep_count} dependency edge(s) detected (subtasks touching the "
                        f"same symbol/file) — these stay ordered; concurrent fan-out is "
                        f"withheld for them.")

    return DecompositionPlan(subtasks=subtasks, graph_backed=graph_backed,
                             spec_title=getattr(spec, "title", "") or "", warnings=warnings)


# --------------------------------------------------------------------- confirm (gated)
@dataclass
class ConfirmOutcome:
    confirmed: bool
    plan: DecompositionPlan
    edited: bool = False
    message: str = ""


def confirm_decomposition(plan: DecompositionPlan,
                          ask: Optional[Callable[[str, str], str]] = None,
                          ledger: Any = None,
                          out: Optional[Callable[[str], None]] = None,
                          assume_yes: bool = False) -> ConfirmOutcome:
    """Present the proposed split and HUMAN-GATE it. Nothing fans out until this returns
    `confirmed=True`. Safe default: with no asker and no `assume_yes`, the answer is "no"
    (the split stays read-only). The user may approve as-is, reject, or EDIT by naming the
    subtask ids to keep. The decision is logged to the audit ledger either way."""
    emit = out or (lambda *_a: None)
    emit(plan.render())

    if not plan.subtasks:
        if ledger is not None:
            ledger.record("decompose_confirm", confirmed=False, edited=False,
                          subtasks=0, dependencies=0, graph_backed=plan.graph_backed,
                          reason="nothing to decompose")
        return ConfirmOutcome(False, plan, message="nothing to confirm")

    if assume_yes:
        answer = "yes"
    elif ask is None:
        answer = "no"          # never fan out without an explicit human choice
    else:
        answer = (ask("Confirm this split? [y]es / [n]o / a list of task ids to KEEP",
                      "n") or "n").strip()

    low = answer.lower()
    edited = False
    final = plan
    if low in ("y", "yes", "approve", "confirm"):
        confirmed = True
    elif low in ("", "n", "no", "reject"):
        confirmed = False
    else:
        # Treat anything else as an edit: the ids (comma/space-separated) to KEEP.
        wanted = [tok.strip() for tok in re.split(r"[,\s]+", answer) if tok.strip()]
        valid = {s.id for s in plan.subtasks}
        keep = [w for w in wanted if w in valid]
        if keep:
            final = plan.subset(keep)
            edited = True
            confirmed = bool(final.subtasks)
        else:
            confirmed = False     # unrecognized answer → safe default, nothing runs

    if ledger is not None:
        ledger.record("decompose_confirm", confirmed=confirmed, edited=edited,
                      subtasks=len(final.subtasks), dependencies=final.dependency_count,
                      graph_backed=final.graph_backed)
    msg = ("confirmed" + (" (edited)" if edited else "")) if confirmed else "not confirmed"
    return ConfirmOutcome(confirmed, final, edited=edited, message=msg)


# --------------------------------------------------------------------- run (existing flow)
def run_decomposition(plan: DecompositionPlan, manifest: Any = None,
                      ask: Optional[Callable[[str, str], str]] = None,
                      ledger: Any = None, out: Optional[Callable[[str], None]] = None,
                      runner: Any = None, tracker: Any = None,
                      subagents_available: bool = True) -> Any:
    """Feed the (already-confirmed) plan's tasks into the EXISTING engine: the Stage-25
    selector surfaces the cost estimate + asks parallel-vs-sequential (default sequential),
    then `run_tasks` executes with isolation + two-stage review + degrade-clean. This adds NO
    fan-out logic of its own.

    Safety backstop: if the user picks concurrent fan-out but the plan is not `fanout_safe`
    (dependencies present, or independence unverified for lack of a graph), fan-out is
    DISABLED — isolated tasks run in declared order instead — and the downgrade is announced
    (never a silent parallelization) and logged."""
    from .orchestrator import run_tasks
    emit = out or (lambda *_a: None)
    tasks = plan.tasks()
    choice = resolve_execution_choice(manifest=manifest, ask=ask, tasks=tasks,
                                      ledger=ledger, out=out,
                                      subagents_available=subagents_available)
    if choice.fanout and not plan.fanout_safe:
        emit("execution: dependencies present (or independence unverified) — disabling "
             "concurrent fan-out; isolated tasks run in declared order (never silently "
             "parallel).")
        if ledger is not None:
            ledger.record("decompose_fanout_guard",
                          reason="dependencies-or-unverified-independence")
        choice = ExecutionChoice(choice.mode, isolation=True, fanout=False)
    return run_tasks(tasks, choice, runner=runner, ledger=ledger, tracker=tracker)
