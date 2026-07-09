---
name: docs-adr
description: mokata · Documentation & ADRs — an architecture decision is persisted to memory + the ledger, not lost.
when_to_use: Engage when an approach makes an architecturally significant decision (a technology choice, a structural pattern, a boundary, a trade-off that constrains later work), when work adds or changes an ADR / decision record / changelog, when the spec's `domains` constraint names `docs-adr`, or when the question is "why did we do it this way" that a future reader will ask. Do NOT engage for a routine in-file code change that decides nothing structural, or for prose documentation with no decision to record (that is the docsync skill's reconciliation surface).
---

> **mokata Agent Skill.** This is mokata's `docs-adr` domain knowledge, attached to the pipeline so
> Claude engages it automatically when work makes a decision worth remembering. It is NOT a parallel
> advisory: it enriches ship + govern, and the decision it captures is **persisted to memory + the
> audit ledger** through the existing human-gated WriteGate — that persistence IS the governed edge.
> mokata's non-negotiables still hold — every durable write is secret-scanned, human-approved, and
> audited, and it adds no new gate.

⛭ mokata docs-adr active — gate: every durable write is secret-scanned, human-approved, and audited

# mokata · Documentation & ADRs

An architectural decision that lives only in someone's head, a chat log, or a merged diff is a
decision the next person will re-litigate — or worse, silently reverse. An **Architecture Decision
Record (ADR)** is the durable answer to "why is it this way": a short document capturing one
significant decision, the context that forced it, and the consequences it commits you to. This skill
makes mokata treat a significant decision as governed knowledge: record it as an ADR, persist it to
the project's typed memory + the audit ledger through the human gate, so the decision — and its
rationale — is walkable long after the pull request is forgotten.

## What an ADR is: one decision, its context, its consequences
Michael Nygard's original formulation defines an ADR as a short text file capturing a single
architecturally significant decision, structured as Title, Status, Context, Decision, and
Consequences — the *forces* that led to the choice and what the choice now commits you to
(https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions). The value is in the
Context and Consequences, not just the Decision: a future reader needs to know what constraints were
in play (so they can tell whether those constraints still hold) and what the decision locked in (so
they don't break an invariant they didn't know existed). A record that states only the decision, with
no context or consequence, is a note, not an ADR.

## Record the decision, not the debate: ADRs are immutable, superseded not edited
An ADR is a point-in-time record. When a later decision changes course, you write a NEW ADR and mark
the old one **Superseded** (linking the two) rather than editing history — the trail of superseded
records is itself the reasoning a reader follows. The ADR community organizes these conventions
(status lifecycle, templates such as MADR) at https://adr.github.io/. This immutability is why ADRs
compose with mokata's ledger: an audited, append-only record of decisions is exactly what the ledger
already is. The specific template fields and status vocabulary of any given ADR flavour are
**UNVERIFIED** here — confirm against the template actually in use.

## Architecturally significant: record what constrains, skip what doesn't
Not every choice is an ADR. Record a decision when it is *architecturally significant* — it affects
structure, a boundary, a dependency, a cross-cutting concern, or a trade-off that will constrain later
work — and skip the reversible, local ones. Over-recording buries the signal; under-recording loses
it. The community guidance frames "architecturally significant" as the filter
(https://adr.github.io/) — apply it honestly: if getting this wrong later would be expensive, or if a
future reader would reasonably ask "why did we do it this way", it earns a record.

## Docs must stay true to the code (pairs with docsync)
A decision record, like all documentation, is only worth reading if it is still true. A doc that
describes a decision the code has since reversed is worse than no doc — it actively misleads. This is
why the docs-ADR domain pairs with mokata's **docsync** reconciliation: the ADR captures *why* at
decision time, and docsync keeps the surrounding docs honest against the code as it drifts. Record the
decision here; let docsync flag when the code has outgrown it. (The docsync skill is mokata's docs↔code
reconciliation surface; treat any claim about its exact behaviour as **UNVERIFIED** against the shipped
skill.)

## Attachment to the flow (what mokata does that a doc cannot)
- **brainstorm** — when the chosen approach's graph surface touches an ADR/decision/changelog record,
  the domain classifier puts `docs-adr` in the spec's `domains`, so the decision-recording concern is
  a first-class, human-approved constraint.
- **ship** — when a change lands an architecturally significant decision, capture it as an ADR (Title,
  Status, Context, Decision, Consequences) so the landing carries its own rationale.
- **govern** — the decision is not just a file: it is persisted to the project's typed memory as a
  `decision` and written to the audit ledger through the human gate, so it is queryable and walkable
  (P7), not buried in a docs folder.
- **memory + ledger** — the ADR's essence (the decision + its context + consequences) is recorded with
  mokata's domain-decision path — the governed edge — so approving the ADR and recording it are one
  human-gated, audited act, never loose prose.

## Gate (write-gate)
This skill rides **write-gate** — a BACKED mokata gate: persisting the decision (the ADR's essence) to
memory + the ledger is a durable write, so it is secret-scanned, human-approved, and audited; approval
cannot override a secret block. That persistence IS the governed edge — an ADR that is only a file on
disk is not governed; an ADR recorded through mokata's decision path is. This skill adds **no new
gate**: it reuses the EXISTING WriteGate + audit ledger, and any change to an approved spec's decision
scope routes through the deviation gate.

## Grounding discipline
Decide from the actual decision and the primary sources, not from assumption. Before asserting a
decision is significant, superseded, or already recorded, VERIFY it against the code and the existing
records (read the ADRs, query memory) and the cited source. Prefer the primary source (Nygard's ADR
article, adr.github.io) over memory or a blog, and CITE the URL. Flag anything you could not verify as
**UNVERIFIED** rather than stating it as fact.

## Rationalizations — stop if you catch yourself thinking any of these

| Excuse | Reality |
|---|---|
| "The decision is obvious — no need to write it down." | Obvious to you today is a mystery to the reader in a year; an ADR records the Context + Consequences so they don't re-litigate it (Nygard). |
| "I'll just edit the old ADR to reflect the new decision." | ADRs are immutable point-in-time records; write a NEW one and mark the old **Superseded** — the trail is the reasoning (adr.github.io). |
| "I'll record every choice as an ADR to be safe." | Over-recording buries the signal; record only the architecturally significant decisions, skip the reversible local ones. |
| "The ADR file is committed, so it's governed." | A file on disk is not governed; persist the decision to memory + the ledger through the human gate so it is walkable and audited (P7). |
| "The doc still describes the decision, so it's current." | A doc can outlive the code that made it true; let docsync reconcile it against the code — a stale decision record actively misleads. |

## Verification — confirm each before you claim this skill is done

Evidence, not "seems right" — check every box or say which is unmet and why:

- [ ] the decision was classified as a `docs-adr` domain in the spec (or amended in when reached late)
- [ ] the decision was architecturally significant (it constrains later work) — a reversible local choice was NOT force-recorded
- [ ] it was captured with Context + Decision + Consequences (not just the decision), per the ADR structure
- [ ] a change of course was written as a NEW record marking the old **Superseded**, not an edit to history
- [ ] the ADR's essence was persisted to memory + the ledger (human-gated) — the governed edge — not left as a loose file

## References — pulled in just-in-time (not loaded inline)

- `references/docs-adr.md` — the ADR structure (Nygard), the immutable/superseded lifecycle and templates (adr.github.io), the "architecturally significant" filter, and how an ADR becomes a recorded mokata decision, each with its primary-source URL

## Contract

**CAN**
- classify the decision/doc surface and capture an architecturally significant decision as an ADR (Context + Decision + Consequences)
- mark a superseded decision with a new record (immutable trail), and apply the "architecturally significant" filter honestly
- persist the ADR's essence as a typed `decision` to memory + the ledger (human-gated) — the governed edge

**MUST NOT**
- persist a decision record without the write gate (gate: write-gate)
- change an approved spec's decision scope beyond approval (gate: deviation)
- edit a past ADR to rewrite history, or leave a significant decision as loose prose instead of a recorded decision (advisory)

**DEPENDS ON**
- the spec carrying the `docs-adr` domain (classified at brainstorm; amend-in if reached late) (advisory)
- the memory store + audit ledger for persisting the decision — the governed edge this skill exists to feed (gate: write-gate)

> Grounding: `(gate: …)` boundaries are enforced by that gate in code; `(advisory)` ones are protocol discipline this skill follows, not a hard block.
