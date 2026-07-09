# Documentation & ADRs — primary sources (JIT detail)

Pulled in just-in-time when the docs-ADR skill is engaged and the heavier detail is needed. Authored
clean-room in mokata's own words; every external claim is anchored to a primary source below (Michael
Nygard's original ADR article and the ADR community organization at adr.github.io). Where a specific
template field or status vocabulary could not be verified against the live source at authoring time, it
is marked **UNVERIFIED** and must be confirmed at the cited URL before it is relied on.

## What an ADR is — one decision, its context, its consequences

Michael Nygard's original formulation: an Architecture Decision Record is a short text file capturing a
single architecturally significant decision, structured as **Title, Status, Context, Decision,
Consequences**. The Context states the forces (constraints, requirements, trade-offs) that led to the
choice; the Consequences state what the choice now commits you to — good and bad. Source:
https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions

The value is in Context + Consequences, not the bare Decision: a future reader needs to know what
constraints were in play (to judge whether they still hold) and what the decision locked in (so they
don't break an invariant they never knew existed). A record with only a decision is a note, not an ADR.

## The lifecycle — immutable records, superseded not edited

An ADR is a point-in-time record. When a later decision changes course, write a NEW ADR and mark the
old one **Superseded** (linking the two) rather than editing history — the trail of superseded records
IS the reasoning a reader follows. Status conventions (Proposed / Accepted / Superseded / Deprecated)
and templates such as MADR are organized by the ADR community. Source: https://adr.github.io/

The exact template fields and status vocabulary vary by ADR flavour and are **UNVERIFIED** here —
confirm against the template actually in use before relying on a specific field.

## Architecturally significant — the recording filter

Not every choice is an ADR. Record a decision when it is *architecturally significant*: it affects
structure, a boundary, a dependency, a cross-cutting concern, or a trade-off that constrains later
work. Skip the reversible, local choices — over-recording buries the signal, under-recording loses it.
A useful test: if getting this wrong later would be expensive, or a future reader would reasonably ask
"why did we do it this way", it earns a record. Source (community guidance): https://adr.github.io/

## How an ADR becomes a recorded mokata decision

The ADR's essence — the decision plus its Context and Consequences — is persisted as a typed
`decision` memory item through the human-gated WriteGate (secret-scan → human approval → audit) and
written to the audit ledger under the `domain` kind, so it is queryable and walkable (P7), not buried
in a docs folder. That persistence IS the governed edge: an ADR that is only a file on disk is not
governed; an ADR recorded through mokata's decision path is. This composes with the ledger's own
append-only, audited nature (an ADR is immutable; so is a ledger entry) and pairs with the docsync
skill, which keeps the surrounding docs true to the code as it drifts. This skill adds **no new gate** —
it reuses the EXISTING WriteGate + audit ledger.
