# The domain-skills layer

mokata ships **10 domain skills** — the technology knowledge (API design, security, performance,
frontend accessibility, browser testing, CI/CD, git, deprecation, docs/ADRs, shipping) that the
pipeline skills don't carry. What makes them *mokata* rather than a second pile of advice is how
they're wired: **each domain attaches to the phase where it applies and feeds the gate that already
runs there.** The repo advises; mokata governs, remembers, and audits.

This page explains the wiring: the trigger model that decides which domains apply, the attachment
map, and the memory/ledger edge each domain feeds.

## Attach to the flow, don't sit beside it

A parallel "security workflow" you have to remember to invoke is an afterthought. mokata's domain
skills are the opposite — a domain is three things at once:

1. **an auto-engaging skill** — it activates on its trigger with the `⛭` banner, like any
   [pipeline skill](skills-and-gates.md);
2. **JIT knowledge** the phase skills pull in for the symbols actually in play;
3. **checks that flow into an existing gate/instrument, memory, and the ledger** — never a new
   parallel gate.

## The trigger model — derived, not keyword-guessed

The crux is *how mokata decides a domain applies*. It does **not** keyword-match your prompt. It
derives the domains from the **graph surface an approach touches**, in three steps:

1. **Primary — brainstorm classifies the domains-in-play.** Brainstorm's blast-radius lens already
   knows the files, symbols, and roles an approach touches. The classifier reads *that structural
   surface* (routes/handlers → `api`; auth/input/secrets/external calls → `security`;
   components/views → `frontend-a11y`; a migration or removal → `deprecation`; a hot path or a
   perf acceptance-criterion → `performance`; …). Because the classifier is fed the graph surface
   and not the topic text, it *cannot* be fooled by wording. The resulting domain set is carried
   into the hand-off and **persisted into `emitted_spec__<run_id>.json` as a first-class constraint**, right
   beside the acceptance criteria and the approved approach — human-approved and legible. (This is
   a JSON field only; there is **no store schema change**.)

2. **Engage exactly the spec's domains.** `develop` and `review` read that constraint and engage
   precisely the domains it names — develop JIT-pulls their knowledge for the symbols in play;
   review activates only the matching axes (Architecture / Security / Performance). No extra axis
   fires; none is silently missed.

3. **Live refinement.** If develop's graph queries reach a domain the spec *didn't* capture,
   that's a **non-trivial discovery** — it routes through the same ask → amend-spec loop (the
   deviation gate, human-gated) rather than silently applying. So a domain never applies unseen and
   is never quietly dropped.

Outside a full run, ad-hoc conversational auto-dispatch stays as the fallback: describe an API
change in chat and the `api` skill still engages. Inside a run, the spec is the source of truth.

## The attachment map

Every domain enhances a phase mokata already has and feeds a gate/instrument mokata already runs.
This table is generated from the domain registry in the code:

| Domain skill | Native phase(s) | Feeds (the governed edge) | Review axis | Records as |
|---|---|---|---|---|
| **API & interface design** | brainstorm · develop · review | blast-radius — a contract change is walked to its callers before it lands | Architecture | decision |
| **Security & hardening** | develop · review | secret-guard — security items are hard-enforced rules | Security | guardrail |
| **Performance optimization** | optimize · review | measure-first — no win kept without a before/after measurement | Performance | context |
| **Frontend UI + accessibility** | develop · review | the a11y checklist runs in review; design-system conventions recorded | — | best-practice |
| **Browser testing (DevTools)** | test · debug | the test gate — runtime behaviour exercised before it's trusted | — | context |
| **CI/CD & automation** | ship | the quality-gate pipeline + Scorecard run before a release lands | — | context |
| **Git workflow & versioning** | ship | WriteGate on git actions — atomic commits + change-sizing | — | context |
| **Deprecation & migration** | refine · develop | blast-radius on removal (code-as-liability) → the deviation gate | — | decision |
| **Documentation & ADRs** | ship · govern | an ADR is persisted to memory + the ledger (the governed edge) | — | decision |
| **Shipping & launch** | ship | the release gate — pre-launch checklist + rollback thresholds | — | context |

Only **API, security, and performance** map to a named [review axis](skills-and-gates.md); the
review pass activates that axis only when the spec's domain set includes it. The full set of review
axes is Correctness · Readability · Architecture · Security · Performance.

## The governed edge — memory + ledger

A domain doesn't just check and move on. When it reaches a decision — a contract change, a security
guardrail, a design-system convention, an ADR — that decision is **typed into memory and recorded
to the audit ledger**, human-gated like every other durable write. The "Records as" column above is
the memory kind each domain's decisions carry. That's the moat: the *next* change sees the last
one. A contract you altered is remembered as a decision; a security rule is remembered as a
guardrail the security axis re-checks against.

## Clean-room authoring

All domain content is authored **clean-room from primary sources** — OWASP, web.dev / Core Web
Vitals, MDN and WCAG, the relevant RFCs, Google's engineering practices — in mokata's own words,
with the source URLs cited and anything unconfirmed flagged **UNVERIFIED**. Named principles are
encoded where they belong: Hyrum's Law and contract-first in `api`, code-as-liability and the
Strangler pattern in `deprecation`, measure-first in `performance`, the test pyramid in
browser-testing. No external framework is imported, and the citation standard is enforced by the
skill-lint.

## How you see it

When a domain engages, it announces itself with the same `⛭` activation surface as any skill and
states the boundary it will hold. For the API domain, for example:

> **⛭ mokata api active** — gate: a contract change is walked to its callers before it lands; the
> decision is recorded to memory + the ledger.

Run `mokata skills api` (or any domain name) to reveal a domain skill's full prompt, cited sources,
and the gate it feeds.

## See also

- [The skills layer & gate map](skills-and-gates.md) — the shared anatomy domains inherit.
- [Knowledge layer](../concepts/knowledge.md) — the graph the classifier reads.
- [Memory](../concepts/memory.md) — where domain decisions are typed and stored.
- [Governance & audit](../concepts/governance.md) — the ledger every decision is recorded to.
