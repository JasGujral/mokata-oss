"""LIVE-EVIDENCE + DEV-CC passive (0.0.23 D1) — the code is the source of truth over a frozen list.

The defect this exists for (Jas, live 0.0.14): a spec froze a NAMED blast-radius list that was wrong
— it listed non-consumers and MISSED the real one — and implementation, holding the live grep
evidence for the missed consumer, DEFERRED it because "it is not in the spec's list". The list was
followed over the code.

So at DEVELOP time, before an AC is touched, the approved approach's frozen consumer list (its Lens-1
`impact`: `targets` → `impacted_files`) is RE-VERIFIED against LIVE callers from the knowledge layer
(graph-first: code-review-graph → serena → AST floor → grep, whichever answers). A discrepancy is a
FINDING, never a directive:

  * MISSING — a file the live answer names that the frozen list does not (a consumer in code, absent
    from the list). This is the 0.0.14 defect's exact shape.
  * STALE   — a CODE file the frozen list names that no live answer for ANY target names (a listed
    file that does not reference what the approach touches). Only decided when EVERY target was
    queried — a file that belongs to a target this turn did not query is not stale, it is unasked.

DEV-CC (passive half, doc 84 row "DEV-CC"): the same per-turn lines also name (a) the approved
approach's `decisions[]` whose `about_code` reaches a file this run has touched, and (b) the existing
GUARD clauses on those touched Python files (an `if …: raise|return` or an `assert`) — so a new
condition that would negate one is written with the guard in view. Informational; the ENFORCING half
is 0.1.2 (SL.S2), not here.

⚠ PASSIVE, BY THE ROW'S OWN TERMS. Nothing here blocks, writes, or proposes. The lines ride the ONE
per-turn channel (`bootstrap.build_injection`) inside the ONE budget (`settings.context.turn_tokens`)
— there is no second injection channel and no second budget. Every probe that fails is omitted;
`develop_turn_lines` never raises.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass
from typing import Any, Callable, Iterable, List, Optional, Sequence, Tuple

#: Bounds — this runs on the per-turn hook, so every walk is capped and the cap is a constant.
MAX_TARGETS = 3            # live blast-radius queries per turn
MAX_FINDINGS = 3           # live-evidence finding lines per turn
MAX_DECISIONS = 2          # dev-cc decision lines per turn
MAX_GUARDS = 2             # dev-cc guard lines per turn
MAX_GUARD_FILES = 3        # touched files scanned for guards (most recent first)
MAX_GUARD_BYTES = 256_000  # a touched file larger than this is not parsed on the hook
SNIPPET_CHARS = 72

MISSING = "missing-consumer"
STALE = "stale-consumer"

#: The stage this module speaks in. Nothing outside develop gets these lines.
DEVELOP_STAGE = "develop"


def _norm(path: str, root: str = "") -> str:
    """Repo-relative, `/`-spelled, `./`-free — so `a/b.py`, `./a/b.py` and an absolute path under
    `root` compare equal on every OS. Routed through `repo_paths` (the one choke point): an absolute
    path becomes its NAME via `name_of`; anything else is re-joined from `split_path`'s components."""
    from ..repo_paths import name_of, split_path
    p = str(path or "").strip()
    if not p:
        return ""
    if root and os.path.isabs(p):
        try:
            p = str(name_of(p, root))
        except ValueError:          # another drive on Windows — not under the repo
            pass
    return "/".join(x for x in split_path(p) if x != ".")


@dataclass(frozen=True)
class EvidenceFinding:
    """One discrepancy between the frozen list and live code. `basis` is the answering rung's
    (`structural` / `verified_empty` / `lexical`) so a lexical finding is never read as structural."""

    kind: str
    target: str
    path: str
    basis: str
    remedy: str = ""          # D2 (d) — the degraded wording + remedy, when the answer was lexical

    def line(self) -> str:
        if self.kind == MISSING:
            text = (f"live-evidence: `{self.path}` references `{self.target}` in LIVE code but is "
                    f"NOT in the approach's frozen list — handle it or amend; never skip it for "
                    f"not being listed")
        else:
            text = (f"live-evidence: `{self.path}` is in the approach's frozen list but no live "
                    f"answer for its targets names it — verify before changing it")
        tag = f" [{self.basis}]"
        if self.remedy:
            tag += f" ({self.remedy})"
        return "- " + text + tag


def _impact_of(handoff: Any) -> Tuple[List[str], List[str]]:
    imp = getattr(handoff, "impact", None)
    if imp is None:
        return [], []
    targets = [str(t).strip() for t in (getattr(imp, "targets", None) or []) if str(t).strip()]
    files = [str(f).strip() for f in (getattr(imp, "impacted_files", None) or []) if str(f).strip()]
    return targets, files


def _live_files(qr: Any, root: str) -> List[str]:
    out: List[str] = []
    for ref in (getattr(qr, "references", None) or []):
        p = _norm(getattr(ref, "path", ""), root)
        if p and p not in out:
            out.append(p)
    return out


def reverify_consumers(targets: Sequence[str], frozen_files: Sequence[str], layer: Any, *,
                       root: str = "", max_targets: int = MAX_TARGETS,
                       remedy_for: Optional[Callable[[Any], str]] = None
                       ) -> List[EvidenceFinding]:
    """Re-verify a frozen consumer list against LIVE answers. Pure over the injected `layer`
    (duck-typed `.blast_radius(sym, depth)`); a target whose query raises is skipped (and makes the
    STALE half undecidable for this turn, because the union is incomplete). Never raises."""
    from ..brainstorm_impact import CODE, classify_path
    frozen = [_norm(f, root) for f in frozen_files]
    frozen_set = {f for f in frozen if f}
    asked = [t for t in targets if t][:max_targets]
    complete = len(asked) == len([t for t in targets if t])
    findings: List[EvidenceFinding] = []
    union: set = set()
    if layer is None or not asked:
        return findings
    for t in asked:
        try:
            qr = layer.blast_radius(t, depth=2)     # like-for-like with Lens 1
        except Exception:  # noqa: BLE001 — a failing query is omitted, never a broken turn
            complete = False
            continue
        basis = str(getattr(qr, "basis", "") or ("lexical" if getattr(qr, "degraded", False)
                                                  else "structural"))
        remedy = ""
        if remedy_for is not None and getattr(qr, "degraded", False):
            try:
                remedy = remedy_for(qr) or ""
            except Exception:  # noqa: BLE001
                remedy = ""
        live = _live_files(qr, root)
        union.update(live)
        for p in live:
            if p not in frozen_set and not any(f.kind == MISSING and f.path == p for f in findings):
                findings.append(EvidenceFinding(MISSING, t, p, basis, remedy))
    if complete:
        for p in frozen:
            if p and p not in union and classify_path(p) == CODE:
                findings.append(EvidenceFinding(STALE, ", ".join(asked), p,
                                                "structural" if union else "lexical"))
    return findings


# ---------------------------------------------------------------------------------- DEV-CC (passive)
def _about_path(entry: str) -> str:
    """The path half of an `about_code` entry (`a/b.py`, `a/b.py:sym`, `a/b.py::sym`)."""
    e = str(entry or "").strip()
    for sep in ("::", ":"):
        if sep in e:
            head = e.split(sep, 1)[0]
            if head and not (len(head) == 1 and sep == ":"):      # not a Windows drive letter
                return head
    return e


def decisions_on_touched(decisions: Iterable[Any], touched: Sequence[str], root: str = "",
                         limit: int = MAX_DECISIONS) -> List[str]:
    """The approved approach's decisions whose `about_code` reaches a touched file, one line each."""
    touched_set = {_norm(t, root) for t in touched if t}
    lines: List[str] = []
    for d in decisions or []:
        about = [_norm(_about_path(a), root) for a in (getattr(d, "about_code", None) or [])]
        hit = next((a for a in about if a and a in touched_set), None)
        if not hit:
            continue
        stmt = " ".join(str(getattr(d, "statement", "") or "").split())
        if len(stmt) > SNIPPET_CHARS * 2:
            stmt = stmt[:SNIPPET_CHARS * 2 - 1] + "…"
        lines.append(f"- dev-cc: approach decision {getattr(d, 'id', '?')} on `{hit}`: {stmt}")
        if len(lines) >= limit:
            break
    return lines


def _guard_text(node: ast.AST, source: str) -> str:
    seg = ast.get_source_segment(source, node) or ""
    seg = " ".join(seg.split())
    return seg if len(seg) <= SNIPPET_CHARS else seg[:SNIPPET_CHARS - 1] + "…"


def guards_in_source(source: str) -> List[Tuple[int, str]]:
    """`(line, text)` for every GUARD clause in `source`: an `if` whose body opens with `raise` or
    `return`, or an `assert`. In source order. A file that does not parse has none."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    out: List[Tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and node.body and isinstance(node.body[0], (ast.Raise,
                                                                                ast.Return)):
            out.append((node.lineno, "if " + _guard_text(node.test, source) + ": "
                        + ("raise" if isinstance(node.body[0], ast.Raise) else "return")))
        elif isinstance(node, ast.Assert):
            out.append((node.lineno, "assert " + _guard_text(node.test, source)))
    return sorted(out)


def guards_on_touched(root: str, touched: Sequence[str], limit: int = MAX_GUARDS) -> List[str]:
    """Existing guards on the touched Python files (most recently touched first), one line each."""
    lines: List[str] = []
    for rel in list(reversed([t for t in touched if t]))[:MAX_GUARD_FILES]:
        if not rel.endswith(".py"):
            continue
        from ..repo_paths import split_path
        path = os.path.join(root, *split_path(rel))
        try:
            if os.path.getsize(path) > MAX_GUARD_BYTES:
                continue
            with open(path, "rb") as fh:
                source = fh.read().decode("utf-8", errors="replace")
        except OSError:
            continue
        for line, text in guards_in_source(source):
            lines.append(f"- dev-cc: existing guard `{_norm(rel)}:{line}` `{text}` — a new "
                         f"condition must not silently negate it")
            if len(lines) >= limit:
                return lines
    return lines


# ---------------------------------------------------------------------------------- the per-turn lines
def run_in_develop(root: str, run_id: str) -> bool:
    """Is `run_id` in the develop stage? Its latest `stage_enter`, or — when it has none — an emitted
    spec on record (the progress view's own rule: spec emitted ⇒ develop is next)."""
    from ..progress_events import current_stage
    from ..run_resolver import SPEC_PREFIX
    from ..state import StateStore
    from ..tdd_state import state_dir
    stage = current_stage(state_dir(root), run_id, default="")
    if stage:
        return stage == DEVELOP_STAGE
    try:
        return bool(StateStore(state_dir(root)).exists(SPEC_PREFIX + run_id))
    except Exception:  # noqa: BLE001
        return False


def develop_turn_lines(root: str, run_id: str, *, layer: Any = None,
                       layer_factory: Optional[Callable[[], Any]] = None,
                       remedy_for: Optional[Callable[[Any], str]] = None,
                       extra: Optional[Callable[[Any, Any, Any], List[str]]] = None) -> List[str]:
    """The develop-time per-turn lines for `run_id`: LIVE-EVIDENCE findings first (they are the
    defect class), then DEV-CC decisions and guards. `[]` outside develop, with no approach on record,
    or on any failure. `layer` (or the lazy `layer_factory`) is built only when there is a frozen list
    to re-verify. `extra(handoff, spec, layer)` lets a later stage add its own lines (SPEC-ANCHOR's
    per-AC lines) through this same channel rather than a second one."""
    try:
        if not root or not run_id or not run_in_develop(root, run_id):
            return []
        from ..brainstorm import Handoff
        from ..run_resolver import APPROACH_PREFIX, SPEC_PREFIX
        from ..state import StateStore
        from ..tdd_state import state_dir
        from ..working_set import working_set
        store = StateStore(state_dir(root))
        handoff = spec = None
        try:
            raw = store.read(APPROACH_PREFIX + run_id)
            handoff = Handoff.from_dict(raw) if isinstance(raw, dict) and raw else None
        except Exception:  # noqa: BLE001 — an unreadable approach is "no frozen list", not a crash
            handoff = None
        try:
            from .spec import Spec
            raw = store.read(SPEC_PREFIX + run_id)
            spec = Spec.from_dict(raw) if isinstance(raw, dict) and raw else None
        except Exception:  # noqa: BLE001
            spec = None
        lines: List[str] = []
        targets, frozen = _impact_of(handoff)
        if targets and layer is None and layer_factory is not None:
            try:
                layer = layer_factory()
            except Exception:  # noqa: BLE001
                layer = None
        if targets and remedy_for is None and layer is not None:
            # D2 (GRAPH-FIRST-IMPACT (b)+(d)) — the re-verify is graph-first through the layer, and a
            # lexical answer carries the SAME degraded wording + remedy `mokata query` prints.
            from ..knowledge.layer import impact_degrade

            def remedy_for(qr: Any, _layer: Any = layer) -> str:
                deg = impact_degrade(_layer, qr)
                return deg["message"] if deg else ""
        if targets:
            lines += [f.line() for f in reverify_consumers(
                targets, frozen, layer, root=root, remedy_for=remedy_for)][:MAX_FINDINGS]
        if extra is not None:
            try:
                lines += list(extra(handoff, spec, layer) or [])
            except Exception:  # noqa: BLE001
                pass
        touched = working_set(root, run_id).touched_files
        if touched:
            decisions = getattr(getattr(handoff, "approach", None), "decisions", None) or []
            lines += decisions_on_touched(decisions, touched, root)
            lines += guards_on_touched(root, touched)
        return lines
    except Exception:  # noqa: BLE001 — never a broken turn
        return []


__all__ = ["MAX_TARGETS", "MAX_FINDINGS", "MISSING", "STALE", "DEVELOP_STAGE", "EvidenceFinding",
           "reverify_consumers", "decisions_on_touched", "guards_in_source", "guards_on_touched",
           "run_in_develop", "develop_turn_lines"]
