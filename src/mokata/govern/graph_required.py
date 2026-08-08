"""GR.S3 — the `graph.required` gate: REFUSE a degraded blast radius as decision input.

The D1 differentiator. Bare Claude Code happily reasons about impact from a blind grep; mokata
REFUSES to present a DEGRADED blast radius as a decision input unless a human explicitly, ledgered,
accepts the degraded evidence. `settings.graph.required` is TRUE by default (it applies to ALL
projects on upgrade — the default simply READS true; there is no migration write), so on the first
degraded decision after an upgrade the user meets a loud, one-time notice explaining the flip.

This module is the SHARED verdict the three decision consumers route through — Lens-1 (brainstorm
blast radius), spec-check (the regression guard's touch-set), and domain classification — so the
refusal, its message, and the escape behave IDENTICALLY across the CLI and the MCP loop (P14 hard
guardrail + a ledgered escape). It holds no consumer logic: each consumer computes its own degraded
signal and asks `check_graph_required` for the verdict.

Honesty over convenience (P22): the escape does NOT strip the caveat. `--allow-degraded` records a
session-scoped, ledgered acceptance and the run proceeds — but the evidence stays EXPLICITLY marked
degraded in the output. The floor's honesty is never weakened to dodge a refusal; the refusal UX is
the fix.

D2 — that honesty is about what the floor CANNOT SEE, and it used to be written here as "empty-AST
evidence stays `degraded=True`". That sentence was the defect: a LEAF (a symbol the AST floor holds
the definition of, that nothing calls) is not empty evidence, it is a structurally verified ZERO,
and refusing it meant naming one entry point among an approach's targets refused `spec_emit` on
mokata's own primary language. The distinction now lives at the backend (`knowledge.query`'s basis
vocabulary), so this gate is unchanged and every consumer inherits it: a symbol the floor cannot
ACCOUNT FOR still degrades and is still refused.

Consent (SI.3 / PH-GATE.S0 P14): the override is a session-scoped state record keyed by `run_id`,
written only by a re-confirmed human action (`mokata spec-check --allow-degraded` at a TTY, or the
MCP twin's human-minted approval) and read back FAIL-CLOSED — an absent/corrupt record is simply no
override, so the gate keeps enforcing. The model cannot type its own acceptance.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, List, Optional, Sequence

from ..errors import MokataError

# --- settings -----------------------------------------------------------------------------------
# `settings.graph.required`, read as `manifest.setting("graph", {}).get("required", True)` — the
# opt-out default-TRUE pattern (mirrors `progress.statusline_enabled`). An absent key reads True, so
# an upgraded manifest with no `graph` block is required-on with NO migration write; an explicit
# `false` is respected.
GRAPH_SETTINGS_GROUP = "graph"
REQUIRED_LEAF = "required"

# --- the ledgered session override ---------------------------------------------------------------
# PH-GATE.S0 pattern: session-scoped (keyed by run_id, so it DIES with the run), re-confirmed,
# ledgered. There is deliberately no env-var kill switch (a side door any process can open silently).
OVERRIDE_PREFIX = "graph_degraded_override__"
GRAPH_OVERRIDE_KIND = "graph_degraded_override"
OVERRIDE_SCOPE = "graph-degraded"

# --- the once-per-repo upgrade notice ------------------------------------------------------------
# Backed by an atomic O_EXCL marker under temp_local (run-state; ungated), exactly like
# `graph_adopt.disclose_first_use`: fires on the FIRST degraded refusal after the default flipped,
# then never again for this repo.
_NOTICE_MARKER = "graph_required_notice"

UPGRADE_NOTICE = (
    "───────────────────────────────────────────────────────────────────────────\n"
    "mokata: `settings.graph.required` is now ON BY DEFAULT (this is a one-time notice).\n"
    "mokata will NO LONGER present a DEGRADED (grep-floor) blast radius as decision\n"
    "input — a degraded impact is now REFUSED unless you explicitly accept it. Adopt a\n"
    "real code graph (`mokata graph adopt`) to answer structurally, or accept the\n"
    "degraded evidence for this session with `--allow-degraded`. Turn the default off\n"
    "entirely with `mokata config set settings.graph.required false`.\n"
    "───────────────────────────────────────────────────────────────────────────"
)


class GraphDegradedError(MokataError):
    """Raised when a consumer is asked to treat a DEGRADED blast radius as decision input while
    `graph.required` is on and no override exists (domain classification uses this to refuse). A
    HARD gate refusal (it propagates, it is not a capability degrade): `failure_class` stays ""."""


def graph_required_enabled(surface: Any) -> bool:
    """Is `settings.graph.required` on for this project? DEFAULT true (opt-out). Degrade-clean: a
    broken/absent manifest FAILS OPEN to required (the safe default is to demand a real graph)."""
    try:
        return bool((surface.manifest.setting(GRAPH_SETTINGS_GROUP, {}) or {}).get(
            REQUIRED_LEAF, True))
    except Exception:                                     # noqa: BLE001 — any manifest fault → default on
        return True


# --------------------------------------------------------------------------- the override I/O
def override_key(run_id: str) -> str:
    return OVERRIDE_PREFIX + (run_id or "")


def read_degraded_override(root: str, run_id: str) -> frozenset:
    """The degraded-evidence scopes this run has an explicit, ledgered override for (empty when
    none). Session-scoped by construction — the key carries the run_id, so a new session (new
    run_id) has no override file and the gate enforces again. FAIL-CLOSED: an absent/corrupt record
    is no override (the gate still enforces; a broken override must never silently open the door)."""
    if not run_id:
        return frozenset()
    from ..state import StateStore
    from ..tdd_state import state_dir
    try:
        data = StateStore(state_dir(root)).read(override_key(run_id))
    except OSError:
        return frozenset()
    if not isinstance(data, dict):
        return frozenset()
    scopes = data.get("scopes")
    if not isinstance(scopes, list):
        return frozenset()
    return frozenset(s for s in scopes if isinstance(s, str))


def write_degraded_override(surface: Any, run_id: str, *, reason: str, actor: str = "human",
                            ledger: Any = None, scopes: Sequence[str] = (OVERRIDE_SCOPE,)) -> None:
    """Record a session-scoped, ledgered acceptance of degraded evidence for THIS run. The human
    re-confirmation happens in the CALLER (the CLI at a TTY, or the MCP twin's minted approval) —
    this only persists the accepted decision + audits it, mirroring `cmd_gate_override`."""
    from .ledger import _now_iso
    existing = set(read_degraded_override(surface.root, run_id))
    merged = sorted(existing | {str(s) for s in scopes})
    surface.state.write(override_key(run_id), {
        "run_id": run_id, "scopes": merged, "actor": actor,
        "reason": reason, "at": _now_iso(),
    })
    if ledger is None:
        from . import AuditLedger
        ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
    ledger.record(GRAPH_OVERRIDE_KIND, run=run_id, actor=actor, decision="override",
                  scope="session", degraded=True, reason=reason)


# --------------------------------------------------------------------------- the upgrade notice
def fire_upgrade_notice_once(root: str) -> Optional[str]:
    """Return the loud one-time upgrade notice the FIRST time a degraded decision is refused for
    this repo, `None` forever after. Backed by an atomic O_EXCL marker (run-state; ungated), so it
    is exactly-once even across concurrent windows."""
    from ..tdd_state import state_dir
    sdir = state_dir(root)
    try:
        os.makedirs(sdir, exist_ok=True)
    except OSError:
        return None
    marker = os.path.join(sdir, _NOTICE_MARKER)
    try:
        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    except FileExistsError:
        return None                                       # already shown for this repo
    except OSError:
        return None
    return UPGRADE_NOTICE


# --------------------------------------------------------------------------- the verdict
@dataclass
class GraphRequiredOutcome:
    """The `graph.required` gate verdict for one decision consumer (doc 85 §3: a `*Outcome`).

    `refused` is the gate: the consumer must NOT present its degraded evidence as decision input.
    `degraded` survives the override (honesty over convenience — an allowed run keeps the caveat).
    `notice` carries the one-time upgrade banner when this is the first refusal after the flip."""

    consumer: str
    degraded: bool
    required: bool
    overridden: bool
    refused: bool
    reason: str = ""
    notice: Optional[str] = None

    @property
    def allowed(self) -> bool:
        return not self.refused

    def render(self) -> str:
        if not self.refused:
            return self.reason
        parts: List[str] = []
        if self.notice:
            parts.append(self.notice)
        parts.append(self.reason)
        return "\n".join(p for p in parts if p)


def _why(consumer: str, backend: Optional[str], mentions: int, files: int,
         targets: Optional[Sequence[str]]) -> str:
    """The WHY line: cite AST-zero + the lexical mention count when the embedded AST floor answered
    with no structural evidence (GR.S1 deviation #4), else the plain lexical grep-floor estimate."""
    tnames = ", ".join(str(t) for t in (targets or []) if str(t).strip())
    tclause = f" for {tnames}" if tnames else ""
    if (backend or "").lower() == "ast":
        return (f"the embedded AST floor found NO structural evidence{tclause}; only the lexical "
                f"grep floor answered — {mentions} textual mention(s) across {files} file(s), which "
                f"is a keyword estimate, not a reliable blast radius.")
    return (f"no code graph is wired — this blast radius fell to the lexical grep floor{tclause} "
            f"({mentions} mention(s) across {files} file(s)), a keyword estimate, not a structural "
            f"answer.")


def check_graph_required(*, degraded: bool, required: bool, overridden: bool, consumer: str,
                         backend: Optional[str] = None, mentions: int = 0, files: int = 0,
                         targets: Optional[Sequence[str]] = None,
                         notice: Optional[str] = None) -> GraphRequiredOutcome:
    """The shared verdict. REFUSE iff the evidence is `degraded` AND `graph.required` is on AND the
    session has no ledgered override. The refusal message is informative + actionable (never a stack
    trace): it cites WHY the evidence is degraded and names the TWO roads out — adopt a real graph
    (`mokata graph adopt`), or accept the degraded evidence for this session with `--allow-degraded`
    (its MCP twin requires a human-minted approval; the model cannot accept it). An allowed-but-
    degraded verdict keeps `degraded=True` so the caveat survives into the output."""
    refused = bool(degraded and required and not overridden)
    reason = ""
    if refused:
        reason = (
            f"REFUSED: {consumer} on a DEGRADED blast radius. `settings.graph.required` is on "
            f"(default), so mokata will not present a degraded blast radius as decision input.\n"
            f"WHY: {_why(consumer, backend, mentions, files, targets)}\n"
            f"Two roads out:\n"
            f"  1. Adopt a real code graph so mokata answers structurally — `mokata graph adopt` "
            f"(or `mokata init --profile full`).\n"
            f"  2. Explicitly accept the degraded evidence for THIS session — `--allow-degraded` "
            f"(recorded to the audit ledger; the evidence stays marked degraded). Its MCP twin "
            f"needs a human-minted approval — the model cannot accept it.")
    return GraphRequiredOutcome(
        consumer=consumer, degraded=bool(degraded), required=bool(required),
        overridden=bool(overridden), refused=refused, reason=reason,
        notice=notice if refused else None)


# --------------------------------------------------------------------------- consumer entry points
def brainstorm_impact_gate(session: Any, approach_name: str, *, surface: Any, run_id: str,
                           layer: Any = None) -> GraphRequiredOutcome:
    """The Lens-1 verdict: read the chosen approach's query-level `graph_degraded`, the project's
    `graph.required`, and this session's override, and fire the one-time upgrade notice on the
    first refusal. The result is what `BrainstormSession.approve(graph_gate=...)` consumes.

    ⚠ NOTHING IN `src/` CALLS THIS. It is a consumer entry point with no consumer: neither the CLI
    skill nor the MCP loop computes it (they did not "both compute it the same way", as this
    docstring claimed until 0.0.17), so the approve-path refusal it feeds never fires in the
    shipped product — `GATE-UNREACHABLE-BRAINSTORM`, doc 84. The GR.S3 refusal that DOES fire is
    the emit-path one in `mcp/tools_spec.py`, which reaches the same verdict through
    `check_graph_required` without going through this function. Wiring this one into the approve
    path is deferred to 0.0.19 by ruling D14, alongside the JS/TS floor."""
    imp = (getattr(session, "impacts", {}) or {}).get(approach_name)
    degraded = bool(getattr(imp, "graph_degraded", False))
    required = graph_required_enabled(surface)
    overridden = bool(read_degraded_override(getattr(surface, "root", ""), run_id))
    notice = (fire_upgrade_notice_once(getattr(surface, "root", ""))
              if (degraded and required and not overridden) else None)
    return check_graph_required(
        degraded=degraded, required=required, overridden=overridden,
        consumer="blast radius (Lens 1)", backend=getattr(layer, "backend_name", None),
        mentions=int(getattr(imp, "caller_count", 0) or 0),
        files=int(getattr(imp, "file_count", 0) or 0),
        targets=list(getattr(imp, "targets", []) or []), notice=notice)
