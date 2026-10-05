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
from typing import Any, List, Optional, Sequence, Tuple

from ..brainstorm_impact import (DEGRADE_CHAIN_IS_LEXICAL, DEGRADE_FLOOR_FOUND_NOTHING,
                                 DEGRADE_GRAPH_FAILED, DEGRADE_GRAPH_UNAVAILABLE, DEGRADE_NO_LAYER,
                                 DEGRADE_QUERY_FAULT)
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

# 🔴 REVIEW FINDING B-F6 — THE REMEDY SENTENCE IS GONE FROM THIS NOTICE. It said *"Adopt a\nreal
# code graph (`mokata graph adopt`) to answer structurally"*, and `render()` prepends this notice to
# the FIRST refusal in a repo — the loud one. So a `graph-failed` user read "ADOPTING AGAIN WILL NOT
# FIX THIS" and "Adopt a real code graph" in the same output, and a29's `assertNotIn("Adopt a real
# code graph")` could not see it for TWO independent reasons: the literal is broken by a newline
# here, and that test never passed a notice at all.
#
# ⭐ The split is by JOB, which is also why this is not merely a deletion: this notice's job is to
# explain that a DEFAULT FLIPPED and how to turn it off. What to do about THIS refusal is the
# reason-specific `_roads_out`, which follows it in `render()` and is derived. One fact, one owner.
UPGRADE_NOTICE = (
    "───────────────────────────────────────────────────────────────────────────\n"
    "mokata: `settings.graph.required` is now ON BY DEFAULT (this is a one-time notice).\n"
    "mokata will NO LONGER present a DEGRADED (grep-floor) blast radius as decision\n"
    "input — a degraded impact is now REFUSED unless you explicitly accept it. What to\n"
    "do about THIS refusal is below; it depends on why the evidence is degraded. Turn\n"
    "the default off entirely with `mokata config set settings.graph.required false`.\n"
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


# ---------------------------------------------------------------- the evidence, DERIVED not told
#
# 🔴 `GRAPH-REQUIRED-GATE-TURNS-ON-A-BOOLEAN-THE-MODEL-WROTE` (doc 84, measured 2026-08-26).
# `session_save` is an MCP TOOL taking the whole brainstorm as a dict; `spec_emit`'s refusal read
# `impact.graph_degraded` back off it, and NOTHING between the two ever computed it. Measured three
# ways: the field omitted → emit ALLOWED · `False` typed by the model → ALLOWED · `True` typed by
# the model → BLOCKED. So the gate was exactly as strong as the model's willingness to report
# against its own interest — and `from_dict` defaults the field to `False`, which made *"a real
# graph answered"* and *"nobody computed one"* the same value (§7g).
#
# ⭐ THAT IS `SI.3` WITH EVIDENCE IN PLACE OF CONSENT — *a `confirm=True` the MODEL types is not
# consent* — on the one gate whose whole purpose is to prove a decision input was not a guess.
#
# ⚠ THE LINE THIS DRAWS, STATED BECAUSE IT IS THE WHOLE DESIGN. The model still supplies WHICH
# SYMBOLS its approach touches — that is its design statement and legitimately its job. What it may
# no longer supply is WHETHER A REAL GRAPH ANSWERED ABOUT THEM. The first is a claim about the
# change; the second is a claim about the evidence, and evidence a party grades for itself is not
# evidence.

#: 🔴 REVIEW FINDING B-F7 — the reason vocabulary's OWN absent-value. `()` meant BOTH "nothing to
#: explain, a graph answered" and "nobody derived a reason", and the second is what
#: `spec_awareness.guard_change` produces: it refuses without passing `reasons` at all, while
#: `GraphRequiredOutcome.reasons` is documented "so a renderer can branch without re-deriving". A
#: renderer branching on that field could not tell spec-check's refusal from a clean answer — §7g,
#: in the field added to fix a §7g. A refusal now always carries at least this, so `()` on a REFUSED
#: outcome is impossible by construction and a renderer has one thing to check.
DEGRADE_NOT_DERIVED = "not-derived"

#: The three states this derivation can be in. ⛔ NOT a bool: *"a graph answered"*, *"the lexical
#: floor answered"* and *"mokata could not look"* are three facts, and collapsing the third into
#: either of the others is how the original defect was invisible.
DERIVED_CLEAN = "derived-clean"
DERIVED_DEGRADED = "derived-degraded"
UNDERIVABLE = "underivable"


# 🔴 REVIEW FINDING B-F5 — `derive_graph_degraded` WAS DELETED HERE (doc 85 §7d: pre-1.0, no
# deprecation). Fix D introduced `derive_graph_degraded_detail` and justified keeping the two-value
# wrapper beside it with *"the live caller unpacks two values, and widening a tuple every caller
# destructures is how a refactor becomes an outage"* — and THE SAME COMMIT changed that caller to
# unpack three. So the wrapper's stated reason to exist was false as of the commit that wrote it
# (§7h), and the reviewer measured zero production callers: `grep -rn "derive_graph_degraded("
# --include=*.py src/` found only its own definition. Its tests were moved to the detail form.
def derive_graph_degraded_detail(surface: Any, targets: Any, *,
                                 _lens: Any = None, _build_layer: Any = None):
    """`(basis, degraded, reasons)` — run the blast-radius lens HERE, over `targets`, and report what
    the tree's own graph actually answered, plus WHY when it did not.

    ⭐ REUSES `compute_impact` RATHER THAN RE-IMPLEMENTING ITS RULE. That function already encodes the
    subtle part — the AST floor answering WITH evidence is not degraded, a missing layer with targets
    IS, an approach naming no targets is neither — and a second implementation of that rule would
    drift from it. The honest machinery already existed; nothing on the brainstorm path was calling
    it. (The 0.0.21 stage-14 review is the proof that this warning is not theoretical: fix D shipped
    a second copy of this body and `test_a9_mutant_batches_are_swept` refused to grade it.)

    ⚠ `_lens` / `_build_layer` ARE TEST SEAMS AND EXIST FOR ONE REASON, stated rather than left to be
    guessed: the two FAIL-CLOSED branches below are unreachable from any real repo — a layer is always
    buildable and the lens catches its own query faults — so mutants that turned them into fail-OPEN
    both SURVIVED. §7f: the clean case had graded the guard away. Injection is the same idiom
    `compute_impact(layer=...)` already uses, and it is the boundary, not the reader (§7e).

    ⛔ FAILS CLOSED, and says which kind of closed. If the layer cannot be built or the lens raises,
    the answer is `UNDERIVABLE` with `degraded=True`: a gate that cannot see is not a gate that
    approves. The caller renders that differently from a measured degradation, because *"the floor
    answered"* and *"mokata could not look"* send a reader to different places.

    ⛔ The reasons are COMPUTED HERE, from the layer and the queries, and there is no argument by
    which a caller can supply them — see the field's own note in `brainstorm_impact`. An UNDERIVABLE
    verdict carries `(DEGRADE_NO_LAYER,)` or `(DEGRADE_QUERY_FAULT,)` rather than an empty tuple,
    because "no reason recorded" and "could not look" must not share a representation (§7g)."""
    tgts = [str(t).strip() for t in (targets or []) if str(t).strip()]
    if not tgts:
        # Nothing was queried, so nothing degraded. This is not a pass by omission: the caller
        # only reaches the gate for an approach that named a surface to touch.
        return DERIVED_CLEAN, False, ()
    try:
        if _lens is None:
            from ..brainstorm_impact import compute_impact as _lens_default
        else:
            _lens_default = _lens
        try:
            if _build_layer is not None:
                layer = _build_layer(surface)
            else:
                from ..knowledge import KnowledgeLayer
                layer = KnowledgeLayer.from_surface(surface)
        except Exception:                             # noqa: BLE001 — no layer IS an answer
            layer = None
        if layer is None:
            # ⭐ NO LAYER AT ALL IS `UNDERIVABLE`, NOT `DERIVED_DEGRADED`, EVEN THOUGH BOTH REFUSE.
            # `compute_impact`'s rule would call this degraded and be right, but the two send a
            # reader to different places: a degraded answer means *adopt a graph, or accept the
            # lexical floor for this session*; this means *mokata could not look at all*, which is
            # usually a broken adoption rather than a missing one. Same verdict, different sentence
            # — which is the entire content of §7g.
            return UNDERIVABLE, True, (DEGRADE_NO_LAYER,)
        impact = _lens_default("graph-required-gate", tgts, layer=layer)
    except Exception:                                 # noqa: BLE001 — see the fail-closed note
        return UNDERIVABLE, True, (DEGRADE_QUERY_FAULT,)
    degraded = bool(getattr(impact, "graph_degraded", True))
    reasons = tuple(getattr(impact, "graph_degrade_reasons", ()) or ())
    return (DERIVED_DEGRADED if degraded else DERIVED_CLEAN), degraded, reasons


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
    # Fix D — the DERIVED reason vocabulary behind `degraded` (see `brainstorm_impact`). Carried on
    # the outcome so a renderer can branch without re-deriving, and so a test can assert the verdict
    # is independent of it.
    reasons: tuple = ()
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


# ==================================================================================================
# THE REMEDY — derived ONCE, rendered three ways.
#
# 🔴 REVIEW FINDINGS B-F1 / B-F2, SECOND ROUND. The first fix for B-F1 gave the machine `hint` its own
# reason ladder beside `_roads_out`'s, and the two orders DISAGREED: `{query-fault, chain-lexical,
# floor-empty}` led with "repair" in the refusal and "check the symbol names" in the hint. That is
# B-F1's exact failure mode — two sentences about one fact, each maintained by hand — reintroduced by
# its own fix. §7f: one rule, one implementation. All three renderers below read `remedy_for`.
REMEDY_REPAIR = "repair"              # a graph is wired and something about it is broken
REMEDY_CHECK_TARGET = "check-target"  # the floor answered and found nothing; no graph would either
REMEDY_ADOPT = "adopt"                # no structural backend in the chain at all


def remedy_for(reasons: Sequence[str] = ()) -> str:
    """WHICH remedy this reason set calls for. The single precedence.

    ⛔ `chain-lexical` OUTRANKS THE REPAIR FAMILY, and that ordering is review finding B-F2's whole
    substance: **you cannot repair a graph that was never adopted.** A floor-only repo whose primary
    raises produces `{query-fault, chain-lexical}` — a real production shape, because
    `select_backends` leaves `fallback=None` when the floor IS primary and `_run` then re-raises —
    and sending that user to `mokata doctor` is the reported symptom inverted.

    Where both are wrong somewhere, prefer the harmless wrong: `adopt` costs a user with a working
    graph one no-op command, while `repair` sends a user with no graph to fix nothing."""
    rs = set(reasons or ()) - {DEGRADE_NOT_DERIVED}
    # 🔴 REVIEW FINDING 3-7 — `floor-empty` NOW OUTRANKS EVERYTHING, and it used to outrank `adopt`
    # but not `repair`, with no stated reason. The reviewer caught the contradiction: for the same
    # evidence — a grep over the whole repo finding ZERO textual mentions of the target —
    # `{chain-lexical, floor-empty}` said CHECK THE TARGET and `{graph-failed, floor-empty}` said
    # run doctor, while this module's own comment, the stage report and the test docstring all state
    # *"the floor answered and found NOTHING → check the target. Neither road helps."* The precedence
    # contradicted the ruling it was written from, and nothing graded it (the reviewer's mutant that
    # flipped it was GREEN against both suites).
    #
    # ⭐ AND IT IS FIRST ON THE MERITS, not just for consistency: if the floor can find no textual
    # mention of the symbol anywhere, a working graph has nothing to find either, so repairing one
    # changes nothing and adopting one changes nothing. The target is the only thing that can be
    # wrong. The repair advice still rides along in the WHY sentence, which names the fault.
    if DEGRADE_FLOOR_FOUND_NOTHING in rs:
        return REMEDY_CHECK_TARGET
    if DEGRADE_CHAIN_IS_LEXICAL in rs:
        return REMEDY_ADOPT
    # 🔴 3-1 — `graph-unavailable` is REPAIR, and that is the whole point of separating it from
    # `chain-lexical`: a pinned tool that will not launch is a broken install, not a missing one.
    if rs & {DEGRADE_NO_LAYER, DEGRADE_QUERY_FAULT, DEGRADE_GRAPH_FAILED,
             DEGRADE_GRAPH_UNAVAILABLE}:
        return REMEDY_REPAIR
    return REMEDY_ADOPT


def _why(consumer: str, backend: Optional[str], mentions: int, files: int,
         targets: Optional[Sequence[str]], reasons: Sequence[str] = ()) -> str:
    """The WHY line. Keyed on the DERIVED REASON when one is present, and on the old backend-name
    check when it is not, so a caller that passes no reasons gets exactly the sentence it got before
    fix D (this function is on the refusal path of three consumers; changing what they say without
    being asked would be its own defect, and there is a control for it).

    ⚠ IT FOLLOWS `remedy_for`'S PRECEDENCE, not its own. Before that, `{query-fault, chain-lexical}`
    said *"the backend is there, it raised rather than answered"* while the same set's road said there
    was no graph — a refusal contradicting itself in two adjacent lines (review finding B-F2)."""
    tnames = ", ".join(str(t) for t in (targets or []) if str(t).strip())
    tclause = f" for {tnames}" if tnames else ""
    rs = set(reasons or ()) - {DEGRADE_NOT_DERIVED}
    if not rs:
        if (backend or "").lower() == "ast":
            return (f"the embedded AST floor found NO structural evidence{tclause}; only the lexical "
                    f"grep floor answered — {mentions} textual mention(s) across {files} file(s), "
                    f"which is a keyword estimate, not a reliable blast radius.")
        return (f"no code graph is wired — this blast radius fell to the lexical grep floor{tclause} "
                f"({mentions} mention(s) across {files} file(s)), a keyword estimate, not a "
                f"structural answer.")
    remedy = remedy_for(reasons)
    fault = (" A structural query also FAILED rather than answering, so some of this radius was "
             "withheld." if DEGRADE_QUERY_FAULT in rs else "")
    if remedy == REMEDY_ADOPT:
        return (f"no code graph is wired — this blast radius fell to the lexical grep floor{tclause} "
                f"({mentions} mention(s) across {files} file(s)), a keyword estimate, not a "
                f"structural answer.{fault}")
    if remedy == REMEDY_CHECK_TARGET:
        # 🔴 3-7 — the WHY still names the fault when there IS one; what changed is which REMEDY
        # leads. An empty floor answer plus a broken graph is still "check the target first", because
        # a working graph would also find nothing — but the reader must be told the graph is broken.
        broken = ""
        if DEGRADE_GRAPH_UNAVAILABLE in rs:
            broken = (" ⚠ Separately: a code graph is adopted here and its tool is not runnable, "
                      "which `mokata doctor` will name.")
        elif DEGRADE_GRAPH_FAILED in rs:
            broken = (" ⚠ Separately: the adopted graph failed on this query, which `mokata doctor` "
                      "will name.")
        elif DEGRADE_NO_LAYER in rs:
            broken = " ⚠ Separately: no knowledge layer could be built at all."
        if DEGRADE_CHAIN_IS_LEXICAL in rs:
            return (f"no code graph is wired, and the lexical grep floor found NOTHING{tclause} — so "
                    f"this is not a small blast radius, it is an ABSENT one.{fault}{broken}")
        return (f"the floor answered and found NOTHING{tclause} — there is no evidence either way, "
                f"which is not the same as a small blast radius. A working graph would find nothing "
                f"here either.{fault}{broken}")
    # REMEDY_REPAIR — and WHICH repair, because they arrive very differently.
    if DEGRADE_GRAPH_UNAVAILABLE in rs:
        return (f"a code graph is ADOPTED in this repo and its tool is NOT RUNNABLE here{tclause}, "
                f"so the chain resolved to the lexical grep floor. ADOPTING AGAIN WILL NOT FIX THIS "
                f"— the adoption is on record; the install is what is missing.{fault}")
    if DEGRADE_NO_LAYER in rs:
        return (f"mokata could not look at all{tclause} — no knowledge layer could be built, which is "
                f"usually a BROKEN adoption rather than a missing one.")
    if DEGRADE_GRAPH_FAILED in rs:
        extra = ("; and the floor found NOTHING, so there is no evidence either way"
                 if DEGRADE_FLOOR_FOUND_NOTHING in rs else
                 f" — {mentions} mention(s) across {files} file(s), a keyword estimate")
        return (f"a code graph IS adopted, and this blast radius still fell to the lexical grep "
                f"floor{tclause}{extra}. ADOPTING AGAIN WILL NOT FIX THIS.")
    return (f"a structural query FAILED{tclause} and the answer was withheld — the backend is there, "
            f"it raised rather than answered.")


def _roads_out(reasons: Sequence[str]) -> str:
    """The ROADS OUT. Road 2 (the ledgered `--allow-degraded`) is the same in every case and is never
    weakened; it is road 1 that was wrong, because it said *adopt a graph* to a user whose graph is
    adopted and broken, or whose target does not exist."""
    accept = ("  2. Explicitly accept the degraded evidence for THIS session — `--allow-degraded` "
              "(recorded to the audit ledger; the evidence stays marked degraded). Its MCP twin "
              "needs a human-minted approval — the model cannot accept it.")
    remedy = remedy_for(reasons)
    if remedy == REMEDY_REPAIR:
        first = ("  1. REPAIR the graph you already have — `mokata doctor` first; re-adopt with "
                 "`mokata graph adopt` only if doctor says the adoption itself is gone.")
    elif remedy == REMEDY_CHECK_TARGET:
        first = ("  1. CHECK THE TARGET — the floor found nothing for it, so a graph would have "
                 "nothing to find either. Confirm the symbol exists and is spelled as the code "
                 "spells it (`mokata query defs <symbol>`), then re-run.")
    else:
        first = ("  1. Adopt a real code graph so mokata answers structurally — `mokata graph "
                 "adopt` (or `mokata init --profile full`).")
    return f"Two roads out:\n{first}\n{accept}"


def hint_for(reasons: Sequence[str] = ()) -> str:
    """The ONE-LINE hint a machine surface attaches beside the full refusal — derived from the SAME
    `remedy_for` the refusal uses, so the two cannot contradict each other.

    🔴 REVIEW FINDING B-F1. `mcp/tools_spec.py` carried its own hardcoded hint saying *"adopt a code
    graph"*, fix D did not touch it, and that surface is the ONLY GR.S3 refusal that fires in the
    shipped product — so the improved advice shipped in the same payload as the advice it exists to
    delete, and nothing tested it (`grep -rn "adopt a code graph" tests/` → no hits). It was also
    unconditional, so an UNDERIVABLE refusal called itself *"a degraded lexical estimate"*."""
    escape = (" or accept it for this session with `--allow-degraded`. Nothing was written; there is "
              "nothing to approve.")
    rs = set(reasons or ()) - {DEGRADE_NOT_DERIVED}
    remedy = remedy_for(reasons)
    if remedy == REMEDY_REPAIR:
        if DEGRADE_GRAPH_UNAVAILABLE in rs:
            lead = ("a code graph is ADOPTED here and its tool is not runnable — adopting again will "
                    "not fix it; the install is what is missing")
        elif DEGRADE_NO_LAYER in rs:
            lead = ("mokata could not run the blast-radius lens at all — no knowledge layer could be "
                    "built, which is usually a BROKEN adoption rather than a missing one")
        elif DEGRADE_GRAPH_FAILED in rs:
            lead = ("a code graph IS adopted and this blast radius still fell to the lexical grep "
                    "floor — adopting again will not fix it")
        else:
            lead = ("a structural query FAILED and the answer was withheld — the backend is there "
                    "and raised")
        return lead + ". Run `mokata doctor`" + escape
    if remedy == REMEDY_CHECK_TARGET:
        # 🔴 REVIEW FINDING 3-4 — this branch was UNGRADED: the hint/refusal agreement test asserted
        # the remedy only for `adopt` and `repair`, so deleting this whole branch let a CHECK THE
        # TARGET refusal ship beside an "adopt a code graph" hint — B-F1 verbatim, on a reason set
        # the derivation really produces. The test now asserts all three.
        if DEGRADE_CHAIN_IS_LEXICAL in rs:
            return ("no code graph is wired AND the lexical floor found nothing — check the symbol "
                    "names first; a graph would have nothing to find either" + escape)
        return ("the floor found NOTHING for this approach's targets, so a graph would have nothing "
                "to find either — check the symbol names" + escape)
    return ("this approach's blast radius is a degraded lexical estimate — adopt a code graph "
            "(`mokata graph adopt`)" + escape)


def check_graph_required(*, degraded: bool, required: bool, overridden: bool, consumer: str,
                         backend: Optional[str] = None, mentions: int = 0, files: int = 0,
                         targets: Optional[Sequence[str]] = None,
                         notice: Optional[str] = None,
                         reasons: Sequence[str] = ()) -> GraphRequiredOutcome:
    """The shared verdict. REFUSE iff the evidence is `degraded` AND `graph.required` is on AND the
    session has no ledgered override. The refusal message is informative + actionable (never a stack
    trace): it cites WHY the evidence is degraded and names the TWO roads out — adopt a real graph
    (`mokata graph adopt`), or accept the degraded evidence for this session with `--allow-degraded`
    (its MCP twin requires a human-minted approval; the model cannot accept it). An allowed-but-
    degraded verdict keeps `degraded=True` so the caveat survives into the output."""
    # ⛔ FIX D CHANGES WHAT THE REFUSAL SAYS, NEVER WHETHER IT REFUSES. This line is byte-identical
    # and there is a guard test that asserts the verdict is the same for every reason, precisely so
    # that "the message got better" can never quietly become "the gate got weaker" (§7h).
    refused = bool(degraded and required and not overridden)
    # 🔴 REVIEW FINDING B-F7 — a REFUSAL always carries a reason token, even when the consumer passed
    # none. `()` used to mean both "a graph answered, nothing to explain" and "nobody derived a
    # reason", and `spec_awareness.guard_change` produces the second on every refusal.
    reasons = tuple(reasons or ())
    if refused and not reasons:
        reasons = (DEGRADE_NOT_DERIVED,)
    reason = ""
    if refused:
        reason = (
            f"REFUSED: {consumer} on a DEGRADED blast radius. `settings.graph.required` is on "
            f"(default), so mokata will not present a degraded blast radius as decision input.\n"
            f"WHY: {_why(consumer, backend, mentions, files, targets, reasons)}\n"
            f"{_roads_out(reasons)}")
    return GraphRequiredOutcome(
        consumer=consumer, degraded=bool(degraded), required=bool(required),
        overridden=bool(overridden), refused=refused, reason=reason,
        reasons=tuple(reasons or ()), notice=notice if refused else None)


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
    path is deferred to 0.0.20 by ruling F2(b), which supersedes D14, alongside the JS/TS
    floor."""
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
