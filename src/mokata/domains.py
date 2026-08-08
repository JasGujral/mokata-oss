"""DK.S0 — the domain-knowledge integration framework + the DERIVED trigger chain.

The agent-skills repo ships domain knowledge (API, security, performance, …) as parallel,
advice-only workflows. mokata's opposite: each domain area is knowledge that ATTACHES to the
phase where it applies and feeds the gate/instrument that already runs there — the repo advises,
mokata governs + remembers + audits. This module is the FRAMEWORK the domain skills (DK.S1–S4)
plug into; it authors no domain content itself.

The crux is a trigger chain DERIVED from the graph (approach × structure), NEVER keyword-guessed:

  1. PRIMARY — brainstorm classifies the domains-in-play from the SURFACE an approach touches
     (its graph-derived files/symbols/roles via the blast-radius lens), NOT from the free-text
     topic. :func:`classify_domains` takes a :class:`DomainSurface` that carries NO topic/summary
     field, so it *cannot* keyword-match a prompt. The classified set is carried into the hand-off
     and PERSISTED into ``emitted_spec.json`` as a first-class constraint beside the ACs +
     approach (human-approved + legible). No store SCHEMA change — a JSON field only.
  2. develop/review engage EXACTLY the spec's domains (:func:`engage_for_spec`) — develop
     JIT-pulls their knowledge; review activates only the matching SK.S2 axes (Architecture /
     Security / Performance). No extra axis fires; none is silently missed.
  3. LIVE refinement — a domain reached only at develop-time (:func:`late_discoveries`) is a
     NON-TRIVIAL discovery routed through SK.S3's ask→amend-spec loop (:func:`domain_amend_request`
     → the deviation gate, human-gated), so a domain is NEVER silently applied and NEVER silently
     missed.

The shared framework the domain skills inherit: the gate/instrument each domain feeds
(:class:`Domain`), the typed-memory + ledger wiring (:func:`record_domain_decision`, human-gated +
audited), the clean-room primary-source authoring standard (:data:`DOMAIN_AUTHORING_STANDARD`,
enforced by the SK.S4 citation lint), and the shared anatomy (:data:`DOMAIN_ANATOMY_REQUIREMENTS`
— Contract + anti-rationalization + verification + ``references/``, reusing the SK.S1/S2 sources).

Reuses SK.S1 gates, SK.S2 axes/anatomy, SK.S3's amend loop and adds NO new gate: the
``emitted_spec.json`` domain set is a constraint the existing gates already carry.

Clean-room: mokata's own words; no external framework imported.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Tuple

from .memory.item import BEST_PRACTICE, CONTEXT, DECISION, GUARDRAIL


# --------------------------------------------------------------- the domain model
@dataclass(frozen=True)
class Domain:
    """One domain/technology area, ATTACHED to the pipeline: the phase(s) it is native to, the
    gate/instrument it feeds (the governed edge), the SK.S2 review axis it activates (only API /
    security / performance map to an axis), the memory kind its decisions are typed with, and the
    structural signals — model-supplied ``roles`` (grounded in the graph) + a coarse
    ``path_markers`` FLOOR — the classifier derives it from. ``sources`` anchors clean-room
    authoring (DK.S1–S4) to primary sources; the framework ships no domain content itself."""

    id: str
    title: str
    phases: Tuple[str, ...]
    gate: str                            # an existing SK.S1 gate id or a named SK.S2 instrument
    review_axis: Optional[str]           # Architecture | Security | Performance | None
    memory_kind: str                     # a real memory kind its typed decisions use
    roles: Tuple[str, ...]               # graph-derived structural roles that imply this domain
    path_markers: Tuple[str, ...]        # the coarse path-segment FLOOR (degrade-clean)
    sources: Tuple[str, ...] = ()        # primary-source anchors for clean-room authoring


# The 10 domain areas of the attachment map (doc 72 Phase E). Each attaches to a phase we already
# run and feeds a gate/instrument we already have — never a new one. Instruments named here are
# SK.S1/S2 surfaces (blast-radius, measure-first, the review checklists), not new gates.
DOMAIN_REGISTRY: Dict[str, Domain] = {
    "api": Domain(
        "api", "API & interface design",
        phases=("brainstorm", "develop", "review"), gate="blast-radius",
        review_axis="Architecture", memory_kind=DECISION,
        roles=("route", "handler", "endpoint", "api-contract", "interface"),
        path_markers=("/routes/", "/handlers/", "/api/", "/endpoints/", "/controllers/")),
    "security": Domain(
        "security", "Security & hardening",
        phases=("develop", "review"), gate="secret-guard",
        review_axis="Security", memory_kind=GUARDRAIL,
        roles=("auth", "input-validation", "secret", "external-call", "deserialization",
               "crypto", "egress"),
        path_markers=("/auth/", "/security/", "secret", "crypto", "/login")),
    "performance": Domain(
        "performance", "Performance optimization",
        phases=("optimize", "review"), gate="measure-first",
        review_axis="Performance", memory_kind=CONTEXT,
        roles=("hot-path", "db-query", "cache", "perf-ac", "batch"),
        path_markers=("cache", "/queries/", "hotpath", "hot_path")),
    "frontend-a11y": Domain(
        "frontend-a11y", "Frontend UI + accessibility",
        phases=("develop", "review"), gate="a11y-checklist",
        review_axis=None, memory_kind=BEST_PRACTICE,
        roles=("ui-component", "view", "a11y", "template", "stylesheet"),
        path_markers=("/components/", "/views/", "/ui/", ".jsx", ".tsx", ".vue", ".css",
                      ".html")),
    "browser-testing": Domain(
        "browser-testing", "Browser testing (DevTools)",
        phases=("test", "debug"), gate="test-gate",
        review_axis=None, memory_kind=CONTEXT,
        roles=("browser-test", "e2e", "devtools"),
        path_markers=("e2e", "playwright", "cypress", "selenium")),
    "ci-cd": Domain(
        "ci-cd", "CI/CD & automation",
        phases=("ship",), gate="scorecard",
        review_axis=None, memory_kind=CONTEXT,
        roles=("ci-config", "pipeline", "quality-gate"),
        path_markers=(".github/workflows", "/ci/", ".gitlab-ci", "dockerfile", "jenkins")),
    "git": Domain(
        "git", "Git workflow & versioning",
        phases=("ship",), gate="write-gate",
        review_axis=None, memory_kind=CONTEXT,
        roles=("vcs", "commit-flow", "branch-policy"),
        path_markers=(".gitignore", ".gitattributes")),
    "deprecation": Domain(
        "deprecation", "Deprecation & migration",
        phases=("refine", "develop"), gate="deviation",
        review_axis=None, memory_kind=DECISION,
        roles=("migration", "removal", "deprecation", "legacy-shim"),
        path_markers=("/migrations/", "migration", "deprecat", "legacy")),
    "docs-adr": Domain(
        "docs-adr", "Documentation & ADRs",
        phases=("ship", "govern"), gate="write-gate",
        review_axis=None, memory_kind=DECISION,
        roles=("doc", "adr", "changelog"),
        path_markers=("/adr/", "/docs/adr", "architecture-decision")),
    "shipping": Domain(
        "shipping", "Shipping & launch",
        phases=("ship",), gate="release-gate",
        review_axis=None, memory_kind=CONTEXT,
        roles=("release", "rollout", "rollback", "launch-checklist"),
        path_markers=("release", "deploy", "rollout", "changelog")),
}

# Ordered ids (registry insertion order — the attachment-map order).
DOMAIN_IDS: Tuple[str, ...] = tuple(DOMAIN_REGISTRY.keys())

# The SK.S2 review axes, in order. Only three of them are DOMAIN-activatable instrument-axes
# (Architecture / Security / Performance); Correctness + Readability always apply.
REVIEW_AXES: Tuple[str, ...] = (
    "Correctness", "Readability", "Architecture", "Security", "Performance")

# The shipped domain SKILL.md set — the domain areas DK.S1–S4 have actually authored so far.
# EMPTY in DK.S0 (framework only); each later batch appends the ids it ships. Distinct from
# :data:`DOMAIN_REGISTRY`, which is the full knowledge map the framework defines up front.
# DK.S1 lands the build/review core: `api` (→ Architecture axis + blast-radius) and `security`
# (→ Security axis + secret-guard + hard-rule enforcement). DK.S2 adds the verify/UI batch:
# `performance` (→ Performance axis + measure-first → ledger), `frontend-a11y` (→ a11y-checklist
# in review → memory records design-system conventions), and `browser-testing` (→ test-gate,
# live runtime captured via the EXISTING MCP surface — no new server). DK.S3 adds the
# pipeline/landing batch: `ci-cd` (→ the quality-gate pipeline + Scorecard posture, ties to SC.S1;
# adds no new gate) and `git` (→ atomic commits + change-sizing advisory riding the EXISTING
# WriteGate on git actions). DK.S4 completes the domain knowledge with the ship/govern batch:
# `deprecation` (→ blast-radius on the removed symbol → ledger, riding the EXISTING deviation gate;
# Strangler Fig / code-as-liability), `docs-adr` (→ an ADR's essence persisted to memory + the
# ledger through the EXISTING WriteGate — the governed edge; Nygard's ADRs) and `shipping` (→ a
# pre-launch checklist + staged rollout + rollback thresholds feeding the EXISTING ship-readiness
# gate; progressive-delivery / rollback literature). Registry order gives the attachment-map order.
DOMAIN_SKILLS: FrozenSet[str] = frozenset({
    "api", "security", "performance", "frontend-a11y", "browser-testing", "ci-cd", "git",
    "deprecation", "docs-adr", "shipping"})


def shipped_domain_skills() -> Tuple[str, ...]:
    """The shipped domain skill ids in attachment-map order (registry order filtered to
    :data:`DOMAIN_SKILLS`). The packaging keep-set + setup install path iterate this, so a domain
    skill is delivered and never mistaken for a prunable orphan."""
    return tuple(d for d in DOMAIN_IDS if d in DOMAIN_SKILLS)


# --------------------------------------------------------------- 1. classify (PRIMARY trigger)
@dataclass
class DomainSurface:
    """The GRAPH-DERIVED surface an approach touches — the ONLY input to classification. It
    carries the touched files/symbols (from the blast-radius lens) and the structural ``roles``
    the model tags them with (grounded in the graph, like the design-fit verdict). It deliberately
    holds NO topic/summary/prompt field: a domain can never be guessed from a free-text keyword,
    only DERIVED from what the approach structurally touches."""

    touched_files: List[str] = field(default_factory=list)
    touched_symbols: List[str] = field(default_factory=list)
    roles: List[str] = field(default_factory=list)


# Reverse indexes, built once from the registry.
_ROLE_TO_DOMAIN: Dict[str, str] = {
    role: dom.id for dom in DOMAIN_REGISTRY.values() for role in dom.roles}


def _floor_domains_for_path(path: str) -> List[str]:
    """The coarse path-marker FLOOR: which domains a single touched file implies by its structural
    location (deterministic, lexical — like ``brainstorm_impact.classify_path``). This scores when
    no model-supplied role tag is present, so a domain is never MISSED for lack of a hint; it is a
    lower-confidence signal the roles refine."""
    p = (path or "").lower()
    out: List[str] = []
    for dom in DOMAIN_REGISTRY.values():
        if any(m in p for m in dom.path_markers):
            out.append(dom.id)
    return out


def classify_domains(surface: "DomainSurface") -> List[str]:
    """Derive the domains-in-play from a graph-derived :class:`DomainSurface` — the PRIMARY
    trigger. Model-supplied ``roles`` map to domains directly; the ``touched_files`` add the
    path-marker floor. Returns a sorted, de-duplicated list of domain ids. There is NO topic
    argument — classification is derived from structure, never from a prompt keyword."""
    found: set = set()
    for role in surface.roles:
        did = _ROLE_TO_DOMAIN.get(str(role).strip())
        if did:
            found.add(did)
    for path in surface.touched_files:
        found.update(_floor_domains_for_path(path))
    return sorted(found)


def classify_from_impact(impact: Any, roles: Optional[Sequence[str]] = None, *,
                         graph_gate: Any = None) -> List[str]:
    """Classify domains from a brainstorm blast-radius report (:class:`ApproachImpact`). The
    impact's ``targets`` + ``impacted_files`` are the graph-derived surface; ``roles`` are the
    model's structural tags. A convenience wrapper over :func:`classify_domains`.

    GR.S3 — ``graph_gate`` is the ``graph.required`` verdict for this impact's blast radius. When it
    REFUSES (the surface is a degraded lexical estimate, ``graph.required`` on, no override), the
    classification is refused with :class:`~mokata.govern.graph_required.GraphDegradedError`: a
    domain set derived from an unreliable surface is not a decision input. Absent (the
    ``graph.required=false`` path) it is a no-op — byte-identical.

    ⚠ NEITHER BRANCH RUNS IN PRODUCTION. This function has ZERO callers anywhere in ``src/``
    outside this module, so the refusal above is unreachable twice over: nothing originates a
    ``graph_gate`` value, AND nothing calls the function that would consume one. It is not an inert
    gate on a live function — the function is inert too. Filed as ``DK-CLUSTER-INERT`` (doc 84):
    13 of this module's 16 top-level symbols have no production caller, and every dead one is the
    BEHAVIOURAL half (classification, engagement, domain memory, handoff) while the three live ones
    are the skill-REGISTRY half. The domain framework was built and never connected. Stage 5 of
    0.0.17 corrects this comment and REPORTS; the wire-or-delete ruling belongs with 0.0.18's SIMP
    deletion work, because "should the domain framework be live?" is a product question."""
    if graph_gate is not None and getattr(graph_gate, "refused", False):
        from .govern.graph_required import GraphDegradedError
        raise GraphDegradedError(graph_gate.render())
    files = list(getattr(impact, "impacted_files", []) or []) + \
        list(getattr(impact, "targets", []) or [])
    symbols = list(getattr(impact, "impacted_symbols", []) or [])
    surface = DomainSurface(touched_files=files, touched_symbols=symbols,
                            roles=list(roles or []))
    return classify_domains(surface)


def classify_session_domains(session: Any, roles: Optional[Sequence[str]] = None, *,
                             graph_gate: Any = None) -> List[str]:
    """Classify the domains-in-play for a brainstorm session's CHOSEN approach — from its
    computed blast-radius impact when present, else its declared targets. The ``roles`` are the
    model's structural tags for the touched surface (grounded in the graph). GR.S3 — ``graph_gate``
    refuses classification from a degraded surface (see :func:`classify_from_impact`).

    ⚠ ALSO UNREACHABLE, and it is the other half of the same finding. This function has no
    production caller either, and the ``graph_gate=`` pass below is the ONLY ``graph_gate``
    argument pass anywhere in ``src/`` — one dead function handing a value to another dead
    function. That single pass is what makes the cheap statement of the defect true: no production
    path ORIGINATES a ``graph_gate`` at all. See :func:`classify_from_impact` for
    ``DK-CLUSTER-INERT`` and why stage 5 reports rather than wires or deletes."""
    chosen = getattr(session, "chosen", None)
    if chosen is None:
        return []
    impact = getattr(session, "impacts", {}).get(chosen.name)
    if impact is not None:
        return classify_from_impact(impact, roles=roles, graph_gate=graph_gate)
    surface = DomainSurface(touched_files=list(getattr(chosen, "targets", []) or []),
                            roles=list(roles or []))
    return classify_domains(surface)


def handoff_with_domains(session: Any, domains: Sequence[str]) -> Any:
    """Produce the brainstorm hand-off carrying the classified domain set as a first-class
    constraint. HARD-GATE is unchanged: it delegates to ``session.handoff()`` (which refuses on an
    unapproved session), then stamps the domains onto it so they ride into the spec."""
    handoff = session.handoff()
    handoff.domains = sorted(set(str(d) for d in domains))
    return handoff


# --------------------------------------------------------------- 2. engage EXACTLY the domains
@dataclass(frozen=True)
class Engagement:
    """What a phase engages for a spec's domains — the knowledge develop JIT-pulls and the review
    axes to activate — restricted to EXACTLY the spec's domains (no more, no less)."""

    domains: Tuple[str, ...]
    knowledge: Tuple[str, ...]           # the domain skills develop pulls in
    axes: Tuple[str, ...]                # the SK.S2 axes review activates

    def render(self) -> str:
        doms = ", ".join(self.domains) or "(none)"
        axes = ", ".join(self.axes) or "(none beyond Correctness + Readability)"
        return (f"mokata · domains engaged: {doms}\n"
                f"  · develop pulls: {', '.join(self.knowledge) or '(no domain knowledge)'}\n"
                f"  · review activates axes: {axes}")


def engage_for_spec(spec_domains: Sequence[str]) -> Engagement:
    """Engage EXACTLY the spec's domains. develop JIT-pulls exactly these domains' knowledge;
    review activates exactly their matching SK.S2 axes (Architecture / Security / Performance) —
    a domain not in the spec never fires, and a spec domain is never dropped. Raises ``KeyError``
    on an unknown domain id (fail-loud, never a silent drop)."""
    ordered = [d for d in DOMAIN_IDS if d in set(spec_domains)]
    for d in spec_domains:
        if d not in DOMAIN_REGISTRY:
            raise KeyError(f"unknown domain {d!r} — not in the registry")
    axes = [DOMAIN_REGISTRY[d].review_axis for d in ordered if DOMAIN_REGISTRY[d].review_axis]
    return Engagement(domains=tuple(ordered), knowledge=tuple(ordered), axes=tuple(axes))


# --------------------------------------------------------------- 3. LIVE refinement (amend loop)
# The discipline the develop/review skills carry so a domain is never silently applied or missed.
DOMAIN_TRIGGER_DISCIPLINE = (
    "The domains-in-play are DERIVED from the approach's graph surface at brainstorm and persisted "
    "into the spec as a first-class constraint, so a domain is NEVER silently applied (it is only "
    "the human-approved set in the spec) and NEVER silently missed (a domain your develop-time "
    "graph queries reach but the spec didn't capture is a non-trivial discovery — STOP and route "
    "it through the ask→amend-spec loop, human-gated, before you rely on it)."
)


def late_discoveries(spec_domains: Sequence[str],
                     reached_domains: Sequence[str]) -> List[str]:
    """The domains reached at develop-time that the spec did NOT capture (sorted). Non-empty means
    a live refinement is due — route each through :func:`domain_amend_request` (never apply it
    silently). Empty means develop stays inside the approved domain set."""
    have = set(spec_domains)
    return sorted(d for d in set(reached_domains) if d not in have)


def domain_amend_request(domain_id: str, phase: str = "develop") -> Any:
    """Build the SK.S3 ask→amend-spec request for a domain reached only at develop-time. It is a
    :class:`DeviationRequest` (a plan change: a new constraint), so it composes with the existing
    deviation gate + the human-gated spec write — it adds no new gate. The human amends the spec
    (adds the domain) or re-plans; the domain never silently applies."""
    from .govern.deviation import ACCEPTANCE_CRITERIA, DeviationRequest
    dom = DOMAIN_REGISTRY.get(domain_id)
    title = dom.title if dom else domain_id
    return DeviationRequest(
        what=(f"develop reached the {domain_id} domain ({title}) — a constraint the approved spec "
              "did not capture"),
        why=("a domain reached only at develop-time is a non-trivial discovery; applying it "
             "silently would apply an un-approved constraint, and ignoring it would silently miss "
             "one — so it must be surfaced and the spec amended before it is relied on"),
        options=[f"amend the spec — add {domain_id} to its domains and re-map the ACs/tests",
                 "re-plan so the change does not reach this domain"],
        target=ACCEPTANCE_CRITERIA, phase=phase)


# --------------------------------------------------------------- 4a. memory + ledger wiring
DOMAIN_LEDGER_KIND = "domain"


def domain_memory_item(domain_id: str, subject: str, value: str,
                       author: str = "mokata",
                       about_code: Optional[Sequence[str]] = None) -> Any:
    """A TYPED memory entry for a domain decision — kinded by the domain (an API decision is a
    ``decision``, a security item a ``guardrail``, …) so the project brain surfaces it by category.
    Pure: builds the item; the human-gated WRITE happens in :func:`record_domain_decision`."""
    from .memory.item import MemoryItem
    dom = DOMAIN_REGISTRY.get(domain_id)
    kind = dom.memory_kind if dom else CONTEXT
    return MemoryItem.create(subject, value, author=author, source=f"domain:{domain_id}",
                             kind=kind, about_code=list(about_code or []))


def record_domain_decision(domain_id: str, subject: str, value: str, *,
                           store: Any, ledger: Any = None, author: str = "mokata",
                           about_code: Optional[Sequence[str]] = None,
                           decision: str = "recorded",
                           confirm: Any = None, assume_yes: bool = False) -> Any:
    """Persist a domain decision as a TYPED memory entry through the SAME universal WriteGate
    (secret-scan → human approval → audit) and record the decision to the audit ledger. Adds no
    new gate — it reuses the memory store's human gate (P2). Returns the store's ``WriteResult``;
    the ledger entry is written under the ``domain`` kind so the decision is walkable (P7)."""
    item = domain_memory_item(domain_id, subject, value, author=author, about_code=about_code)
    result = store.remember(item, confirm=confirm, assume_yes=assume_yes)
    if ledger is not None:
        ledger.record(DOMAIN_LEDGER_KIND, domain=domain_id, subject=subject,
                      memory_kind=item.kind,
                      decision=decision if getattr(result, "committed", False) else "declined",
                      about_code=list(about_code or []))
    return result


# --------------------------------------------------------------- 4b. clean-room authoring standard
# The standard DK.S1–S4 author domain content against — enforced by the SK.S4 citation lint
# (`skill_lints.domain_citation_findings`). Clean-room: mokata's OWN words, never the repo's text.
DOMAIN_AUTHORING_STANDARD = (
    "Author every domain skill CLEAN-ROOM, in mokata's OWN WORDS, from PRIMARY SOURCES (OWASP, "
    "web.dev / Core Web Vitals, MDN, the RFCs, WCAG, Google's engineering practices) — never the "
    "agent-skills repo's text, never a paraphrase of it. CITE the primary-source URL for each "
    "external claim the skill relies on, and flag anything you could not verify as UNVERIFIED "
    "rather than stating it as fact. A domain skill that makes external claims without a cited "
    "primary source, or without the UNVERIFIED discipline, fails the SK.S4 citation lint."
)

# The shared anatomy every domain skill inherits (reusing the SK.S1/S2 single sources), so a
# domain skill is indistinguishable from a native one.
DOMAIN_ANATOMY_REQUIREMENTS: Tuple[str, ...] = (
    "a ## Contract (CAN / MUST NOT / DEPENDS ON) grounded in the gate-integrity map (SK.S1)",
    "the ⛭ activation line, single-sourced to progress.active_skill_line (SK.S1)",
    "an anti-rationalization table (## Rationalizations) — domain-specific excuses (SK.S2)",
    "a ## Verification checkbox demanding evidence, not a vibe (SK.S2)",
    "progressive-disclosure references/ carrying the cited primary sources (SK.S2)",
)


def is_domain_skill(name: str) -> bool:
    """True when ``name`` is a domain skill — i.e. it names a domain in :data:`DOMAIN_REGISTRY`
    (DK.S1–S4 name their SKILL.md after the domain id). The SK.S4 citation lint uses this to fire
    only on domain content, leaving the pipeline skills untouched."""
    return name in DOMAIN_REGISTRY


# --------------------------------------------------------------- 4c. the ⛭ activation line (single source)
# A domain skill's ⛭ line announces the ONE-LINE gate copy for the governed edge it feeds. Single-
# sourced like the pipeline skills' `progress.active_skill_line`: a BACKED SK.S1 gate reuses its
# `skill_contracts` one-liner (so the domain announces the SAME text the pipeline does, no drift);
# an SK.S2 INSTRUMENT (blast-radius, measure-first, …) — which has no skill_contracts GateRef —
# takes its one-liner here. It never invents a gate: the id is always the domain's registered
# ``gate``, a real gate/instrument the DK.S0 tests already assert. The glyph is imported from
# progress (assigned in exactly one module), never re-declared here.
DOMAIN_INSTRUMENT_ONELINE: Dict[str, str] = {
    "blast-radius": ("a contract change is walked to its callers before it lands; the decision "
                     "is recorded to memory + the ledger"),
    "measure-first": "no optimization is kept without a before/after measurement proving the win",
    "a11y-checklist": ("the accessibility checklist is run in review; design-system conventions "
                       "are recorded to memory"),
    "test-gate": "runtime behaviour is exercised through the test phase before it is trusted",
    "scorecard": "the quality-gate pipeline + Scorecard run before a release lands",
    "release-gate": ("landing blocks until the pre-launch checklist + rollback thresholds are "
                     "satisfied"),
}


def domain_headline_gate_line(domain_id: str) -> str:
    """The one-line gate copy a domain skill's ⛭ line announces — the governed edge it feeds. A
    backed SK.S1 gate reuses its ``skill_contracts`` one-liner (single source, no drift); an SK.S2
    instrument reads from :data:`DOMAIN_INSTRUMENT_ONELINE`. Raises ``KeyError`` on an unknown
    domain (fail-loud, never a silent fallback)."""
    dom = DOMAIN_REGISTRY[domain_id]
    if dom.gate in DOMAIN_INSTRUMENT_ONELINE:
        return DOMAIN_INSTRUMENT_ONELINE[dom.gate]
    from .skill_contracts import GATES
    return GATES[dom.gate].one_line


def domain_active_skill_line(domain_id: str, *, state: str = "active") -> str:
    """``⛭ mokata <domain> active — gate: <its governed edge>`` — the single-sourced domain-skill
    activation line, mirroring ``progress.active_skill_line`` for the pipeline skills. The shipped
    domain SKILL.md renders EXACTLY this line (a DK.S1 test asserts byte-equality), so the ⛭ line
    can never drift from the registry. ``state`` lets a caller say ``engaged`` when it auto-fires."""
    from .progress import SKILL_GLYPH
    verb = "active" if state == "active" else state
    return f"{SKILL_GLYPH} mokata {domain_id} {verb} — gate: {domain_headline_gate_line(domain_id)}"
