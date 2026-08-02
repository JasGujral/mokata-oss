"""C7 — consolidation pass (PROPOSAL-ONLY).

A periodic pass that *proposes* consolidations over memory — merge duplicates, summarize
episodic clusters, prune already-stale items, and (DB.S5) archive the coldest items in an
over-budget scope — and **never commits**. Each proposal is surfaced as an old → new diff for
the human to approve / edit / reject, exactly like the C5 self-healing flow; the default is no
change. This preserves P2 (no autonomous writes); the riskier autonomous form stays out —
application is always human-gated.

DB.S5's size-budget sweep (`propose_archival`) lives HERE rather than in its own module, and
that placement is the design: it is a new KIND on this proposal type, so it inherits this
module's surface/render path and the store's single gated apply. mokata has ONE way a memory
change gets proposed to a human, and a budget heuristic does not earn a second one.

`ARCHIVE` is deliberately not `PRUNE`. PRUNE deletes; ARCHIVE closes a bi-temporal validity
window and retains everything. A budget can therefore never destroy a memory, whatever is
approved.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Union

from .item import ALWAYS_ON_KINDS, EPISODIC, MEMORY_KINDS, PERSISTENT, REFERENCE, MemoryItem
from .lifecycle import coldest_over_budget

MERGE = "merge"
SUMMARIZE = "summarize"
PRUNE = "prune"
# DB.S5 — the budget sweep's proposal kind. It is a NEW KIND on the EXISTING proposal type, not a
# new proposal system, and that is the whole design: an archival proposal is surfaced, rendered,
# ledgered, secret-scanned, gated and applied by the same code path as a merge or a summarize, so
# there is exactly one place a human approves a memory change and exactly one place it commits.
#
# ARCHIVE vs PRUNE is the invariant made structural. PRUNE deletes (locally) — it is the pre-
# existing disposal of items C5 already marked stale. ARCHIVE never deletes anything: it CLOSES the
# item's validity window (`valid_to`) and moves it to the `archived` status, leaving the row, the
# value and the provenance intact and re-openable. A budget can therefore never destroy a memory,
# whatever the user approves.
ARCHIVE = "archive"

# ------------------------------------------------------------------ M-4/R5 — the DRAFTER SEAM
# The SUMMARIZE proposal's value used to be an f-string that summarized nothing. It is now drafted
# — by an INJECTED CALLABLE, not by mokata.
#
# WHY A SEAM AND NOT A MODEL CALL (D9): mokata does not call an LLM. It has no API key, no model
# dependency and no network, and keeping it that way is the clean-room posture, not an accident of
# packaging. So the drafting is INVERTED: mokata hands the drafter the turn cluster, the HARNESS
# AGENT writes the summary, and the text returns through this seam into the proposal. The drafter
# is a parameter precisely so the harness supplies the real one and tests supply a deterministic
# one; nothing here is hardcoded to a provider.
#
# WHY THIS IS STILL P2-CLEAN: the drafter produces a PROPOSAL, never a durable write. Whatever it
# returns is secret-scanned, rendered into the human gate and ledgered on exactly the path the
# placeholder rode (store.apply_consolidation) — a drafted value has no shortcut to the store. Who
# writes the words does not change who approves them.
#
# WHY IT DEGRADES INSTEAD OF RAISING: a summary is a nicety; the consolidation pass is not. A
# drafter that is absent, dies, times out (surfacing here as the exception it raises) or returns
# nothing must never take the pass down or lose the merge/prune/archive proposals riding beside
# it. With no drafter the output is byte-identical to the pre-M-4 build — that is the bar, and
# `test_m4_r5_drafted_summary` pins the exact string.
DRAFTED_SUMMARY_KIND = REFERENCE
"""The kind a DRAFTED summary is born with (doc 62 / Stage-36 taxonomy, `item.py`).

REFERENCE is "distilled key points from a document, + a source pointer" — which is exactly what a
consolidated summary is: the key points distilled out of a cluster of turns, with the session it
came from carried in the subject (`summary:{session}`). It is deliberately NOT `context` (a domain
fact/constraint the project asserts — a summary asserts nothing new) and NOT `rule`/`guardrail`
(those are ALWAYS-ON and count against the rules budget; machine-drafted prose must never be born
into a budget it can crowd out, nor into an enforcement posture). REFERENCE normalizes to the
governance kind `fact`, so it carries no enforcement, and it is JIT-retrieved rather than always-on.

The FALLBACK placeholder keeps the bare default kind it has always had: back-compat is the bar, and
an undrafted mechanical line has not earned a category."""

# A drafter MAY name the kind, but not any kind. RULE and GUARDRAIL are the ALWAYS-ON kinds: they
# are injected into every run and count against the rules budget, and a GUARDRAIL is additionally
# born `hard`-enforced (`item.default_enforcement`). A drafter is model-written text arriving
# through a seam whose entire premise is "this is prose" — letting it hand itself an always-on,
# hard-enforced category would turn a summary into a project rule that blocks work, which is an
# escalation the human gate is not being asked to review (the gate renders the VALUE, and nobody
# approving a paragraph is thereby approving a new hard rule). So an unknown or always-on kind is
# clamped back to the drafted default rather than honoured or raised on: the summary still lands,
# just as the fact it always was.
_DRAFTABLE_KINDS = frozenset(k for k in MEMORY_KINDS if k not in ALWAYS_ON_KINDS)


@dataclass
class SummaryDraft:
    """What a drafter returns: the summary text, and optionally the kind it should carry."""
    value: str
    kind: str = DRAFTED_SUMMARY_KIND


# The seam's type. Input: the turn cluster + the session it belongs to (the drafter needs the
# turns' CONTENT — summarizing from counts is what the placeholder did). Output: the summary text,
# a `SummaryDraft`, or None to decline.
SummaryDrafter = Callable[[List[MemoryItem], str], Optional[Union[str, "SummaryDraft"]]]


DRAFTER_SUBSYSTEM = "memory-summary-drafter"


def _note_drafter_degraded(detail: str) -> None:
    """Say ONCE that the summary drafter failed and the proposal fell back to the placeholder.

    Imported lazily, matching `embed.py`: `degrade` is only needed on the failure path, and this
    module is on the consolidation read path."""
    from ..degrade import FAILURE_PROVIDER, note_degraded
    note_degraded(
        DRAFTER_SUBSYSTEM, FAILURE_PROVIDER,
        fallback="the consolidation proposal carries the MECHANICAL placeholder line, not a "
                 "drafted summary — review it as such before approving",
        fix="re-run the consolidation pass; if it persists, the harness agent's drafter is the "
            "thing to check — mokata itself drafts nothing",
        detail=detail)


def _draft_summary(drafter: Optional[SummaryDrafter],
                   turns: List[MemoryItem],
                   session: str) -> Optional[SummaryDraft]:
    """Run the injected drafter, or return None to mean "fall back to the placeholder".

    Every failure mode collapses to that same None: no drafter, a drafter that raises (including a
    timeout, which reaches us as an exception), a drafter that returns None, and a drafter that
    returns blank text. Bare `Exception` is caught on purpose — the drafter is FOREIGN code from
    this module's point of view (a harness callback), and there is no class of failure in it that
    should be allowed to fail the consolidation pass that merely invited it.

    D5 — the swallow is LOUD (register: DEGRADES_LOUD). The fallback still falls back; it just
    stops being a secret, and here that matters more than usual: the human is about to approve a
    proposal, and a placeholder is visually a summary. Someone reading "summary of 5 episodic turns
    in 'x'" with no notice has no way to know a real draft was attempted and DIED — they would
    approve mechanical text believing the turns had actually been read. The notice is what makes
    the difference visible at the gate.

    The three outcomes are deliberately NOT all noticed. NO DRAFTER is the documented default (the
    zero-config path, and a notice on every default install is noise — the embed.py lesson), and
    returning None is an explicit DECLINE ("I have nothing to say"), which is an answer, not a
    fault. A raise or a garbage return is a MALFUNCTION, and only those speak up."""
    if drafter is None:
        return None                      # the documented default — not a degrade, no notice
    try:
        drafted: Any = drafter(list(turns), session)
    except Exception as exc:
        _note_drafter_degraded(f"{type(exc).__name__}: {exc}")
        return None
    if drafted is None:
        return None                      # an explicit decline is an answer, not a fault
    if isinstance(drafted, SummaryDraft):
        value, kind = drafted.value, drafted.kind
    else:
        # A bare string is the harness agent's natural shape — it hands back text, not a dataclass.
        value, kind = drafted, DRAFTED_SUMMARY_KIND
    if not isinstance(value, str) or not value.strip():
        _note_drafter_degraded(f"returned {type(value).__name__} with no usable text")
        return None
    if kind not in _DRAFTABLE_KINDS:
        kind = DRAFTED_SUMMARY_KIND        # clamp: no drafter mints an always-on/unknown kind
    return SummaryDraft(value=value.strip(), kind=kind)


@dataclass
class ConsolidationProposal:
    kind: str                       # MERGE | SUMMARIZE | PRUNE
    mtype: str
    subject: str
    olds: List[MemoryItem] = field(default_factory=list)
    new: Optional[MemoryItem] = None
    rationale: str = ""

    def diff(self) -> str:
        if self.kind == MERGE:
            return f"{len(self.olds)} identical items -> 1 ({self.olds[0].value!r})"
        if self.kind == SUMMARIZE and self.new is not None:
            return f"{len(self.olds)} turns -> summary {self.new.value!r}"
        if self.kind == PRUNE:
            return f"prune {len(self.olds)} stale item(s) -> removed"
        if self.kind == ARCHIVE:
            # Says "archived (retained)" and not "removed", because the human reading this is
            # deciding on the strength of this one line and the difference between the two words
            # is the difference between a reversible and an irreversible answer.
            return (f"archive {len(self.olds)} coldest item(s) -> validity window closed "
                    f"(retained, not deleted)")
        return f"{self.kind}: {len(self.olds)} item(s)"


def propose_consolidations(active_items: List[MemoryItem],
                           stale_items: Optional[List[MemoryItem]] = None,
                           drafter: Optional[SummaryDrafter] = None
                           ) -> List[ConsolidationProposal]:
    """Build proposals from the current memory. Pure: reads only, writes nothing.

    `drafter` (M-4/R5) is the injected summary writer — see the DRAFTER SEAM block above. Omitted
    or failing, the SUMMARIZE proposal falls back to the mechanical placeholder, so this function's
    output with no drafter is byte-identical to the pre-M-4 build."""
    proposals: List[ConsolidationProposal] = []

    # Merge: identical active items (same type + subject + value).
    groups: dict = {}
    for it in active_items:
        groups.setdefault((it.mtype, it.subject, it.value), []).append(it)
    for (mtype, subject, _value), grp in groups.items():
        if len(grp) > 1:
            ordered = sorted(grp, key=lambda g: (g.created_at, g.id))
            proposals.append(ConsolidationProposal(
                MERGE, mtype, subject, olds=ordered, new=ordered[-1],
                rationale=f"{len(grp)} identical active items"))

    # Summarize: a cluster of episodic turns in one session.
    sessions: dict = {}
    for it in active_items:
        if it.mtype == EPISODIC:
            sessions.setdefault(it.subject, []).append(it)
    for session, turns in sessions.items():
        if len(turns) >= 3:
            # M-4/R5 — THE one change site. The trigger (>=3), the grouping, and everything the
            # proposal is then put through are untouched; only the value and its kind are drafted.
            draft = _draft_summary(drafter, turns, session)
            summary = MemoryItem.create(
                subject=f"summary:{session}",
                value=(draft.value if draft is not None
                       else f"summary of {len(turns)} episodic turns in '{session}'"),
                mtype=PERSISTENT,
                kind=(draft.kind if draft is not None else ""),
                source="consolidation")
            proposals.append(ConsolidationProposal(
                SUMMARIZE, EPISODIC, session, olds=list(turns), new=summary,
                rationale=(f"{len(turns)} turns summarized into one fact" if draft is not None
                           else f"{len(turns)} turns can be summarized into one fact")))

    # Prune: items already marked stale (e.g. by C5) are eligible for removal.
    if stale_items:
        proposals.append(ConsolidationProposal(
            PRUNE, mtype="*", subject="(stale)", olds=list(stale_items), new=None,
            rationale=f"{len(stale_items)} stale item(s) eligible for pruning"))

    return proposals


def propose_archival(active_items: List[MemoryItem],
                     usage: Optional[dict] = None,
                     now: Optional[str] = None) -> List[ConsolidationProposal]:
    """DB.S5 — the SIZE-BUDGET SWEEP: propose archiving the coldest items in any over-budget
    (scope × type) bucket. Pure — it reads, ranks and proposes; it writes NOTHING and evicts
    nothing. A sweep that could evict on its own would be an autonomous durable write, which is
    the one thing P2 does not permit.

    One proposal per over-budget bucket (not one per item), so a human approves "archive these 40
    coldest personal episodic turns" as a single reviewable decision with the full list attached,
    rather than being asked forty times. `olds` carries every item the approval would close.

    `usage` is the `{id: UsageSignal}` telemetry that decides what "cold" means. Omitted, every
    item reads as the zero signal and coldness collapses to the deterministic created_at/id
    tiebreak — oldest-first, which is a defensible and stable answer for a store that has never
    recorded a recall. Cold is never guessed from content.
    """
    proposals: List[ConsolidationProposal] = []
    for overage, cold in coldest_over_budget(active_items, usage, now):
        proposals.append(ConsolidationProposal(
            ARCHIVE, mtype=overage.mtype,
            subject=f"({overage.scope_level} {overage.mtype} over budget)",
            olds=list(cold), new=None,
            rationale=(f"{overage.count} active {overage.mtype} items at scope "
                       f"'{overage.scope_level}' exceeds the {overage.budget} budget by "
                       f"{overage.excess}; the {len(cold)} least-recently-used are proposed for "
                       f"archival (validity window closed, nothing deleted)")))
    return proposals


# ==================================================================== M-4/R5 — THE TWO-PHASE FLOW
# `mokata memory consolidate` runs in a SEPARATE PROCESS from the agent's session (the discipline
# `cli_commands/spec.py` spells out for `spec emit`: "a human at a terminal, exactly like `mokata
# approve`"). There is therefore NO synchronous in-process drafter to call — the harness agent
# cannot be blocked on. Grounded in the surfaces that already exist, the honest shape is the one
# `spec_emit` already uses for agent-authored content, in two phases:
#
#   PHASE 1  mokata surfaces the SUMMARIZE proposals WITH the turns to draft from — a DRAFTING
#            REQUEST. It reads only, and the value it shows is still the placeholder.
#   PHASE 2  the agent drafts each summary and SUBMITS it back (CLI `--draft`, or the `consolidate`
#            MCP tool). The submitted text rides the drafter seam exactly as an in-process drafter
#            would, so it is typed, clamped, secret-scanned, rendered at the human gate and
#            ledgered by the same code — and the human still approves before anything is written.
#
# The agent authors; mokata never self-drafts; the human still gates. P2 is untouched by the fact
# that the two phases are two processes.

DRAFTING_INSTRUCTION = (
    "Draft each summary from the turns shown, then submit it with "
    "`mokata memory consolidate --draft <session> --value \"<your summary>\"` "
    "(or the `consolidate` MCP tool). mokata does not write the summary — you do. "
    "The human still approves the drafted summary at the gate before anything is stored."
)
"""The ONE sentence telling the agent how to fulfil a drafting request.

A module constant for the reason `cli_commands/spec.py` lifted its recovery lines to constants: the
MCP tool must return the SAME sentence the CLI prints, rather than paraphrasing it into a second,
drifting copy. It carries no memory content, so it is safe on every surface."""


def drafting_request(proposals: List[ConsolidationProposal]) -> List[dict]:
    """PHASE 1 — the SUMMARIZE proposals rendered as a DRAFTING REQUEST the agent can fulfil.

    Pure. Each entry carries the session to submit against and the TURNS to draft from — the turns'
    content is the whole point, since summarizing from a count is exactly what the placeholder did.
    Non-SUMMARIZE proposals are omitted: a merge or a prune needs no prose written for it."""
    requests = []
    for p in proposals:
        if p.kind != SUMMARIZE:
            continue
        requests.append({
            "session": p.subject,
            "turns": [{"id": t.id, "value": t.value, "created_at": t.created_at}
                      for t in p.olds],
            "placeholder": p.new.value if p.new is not None else "",
        })
    return requests


def constant_drafter(session: str, value: str) -> SummaryDrafter:
    """PHASE 2 — the submitted draft, as a drafter.

    The agent's text is injected through the SAME seam an in-process drafter would use, rather than
    hand-building the item: that way the submitted summary picks up the real `kind`, the always-on
    clamp, the secret-scan, the gate render and the ledger from one code path, and a drafted summary
    cannot acquire a second, laxer route into the store just because it arrived over the CLI.

    It answers for `session` ONLY. Every other cluster in the same pass gets None — an explicit
    decline, so those proposals keep their placeholder and stay quiet."""
    def _drafter(turns: List[MemoryItem], sess: str):
        return value if sess == session else None
    return _drafter


def find_summarize(proposals: List[ConsolidationProposal],
                   session: str) -> Optional[ConsolidationProposal]:
    """The SUMMARIZE proposal for `session`, or None. Pure.

    Proposals are recomputed from live memory on every pass and carry no id, so the session IS the
    handle — which is also why phase 2 re-derives the proposal instead of trying to persist one
    between the two processes."""
    for p in proposals:
        if p.kind == SUMMARIZE and p.subject == session:
            return p
    return None


def render_consolidation(p: ConsolidationProposal) -> str:
    lines = [
        f"mokata · memory consolidation proposed ({p.kind})",
        f"  subject: [{p.mtype}] {p.subject}",
        f"  change:  {p.diff()}",
        f"  why:     {p.rationale}",
        "",
        "Nothing changes unless you act. Choose: approve / edit / reject.",
        "Default is REJECT — consolidation never rewrites memory on its own.",
    ]
    return "\n".join(lines)
