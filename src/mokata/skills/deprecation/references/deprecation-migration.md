# Deprecation & migration — primary sources (JIT detail)

Pulled in just-in-time when the deprecation skill is engaged and the heavier detail is needed.
Authored clean-room in mokata's own words; every external claim is anchored to a primary source below
(Martin Fowler's Strangler Fig pattern and *Refactoring*, Chesterton's Fence as a named principle, and
Semantic Versioning). Where a specific source edition or migration mechanic could not be verified
against the live source at authoring time, it is marked **UNVERIFIED** and must be confirmed at the
cited URL before it is relied on.

## Code as a liability — remove the unused, but never blind

Every line of code is an ongoing cost: it is read, maintained, tested, and reasoned about, and it is a
place a defect can hide. So unused code is worth removing — the goal is real. But "unused" is a
blast-radius fact, not an eyeball judgement: the only safe basis for a delete is a walked call graph
that shows no caller reaches the symbol. Martin Fowler's *Refactoring* frames the safe way to change
code as a disciplined series of small, behaviour-preserving steps rather than a big rewrite. A
deletion is the same discipline — prove the callers are gone, delete in a step you can revert.
Source: https://refactoring.com/

## Chesterton's Fence — understand before you remove

Do not remove a thing until you understand why it is there. The parable (a reformer who sees no use
for a fence across a road is the last person who should tear it down) maps onto deprecation: a flag, a
shim, a "dead" branch, or a redundant-looking check may be load-bearing for a caller, a platform, or
an edge case you have not walked to. Let the blast-radius answer "why is this here / who needs it"
before the delete. The exact original wording/edition (G. K. Chesterton) is **UNVERIFIED** here —
treat it as a named engineering principle rather than a quotation.

## The Strangler Fig — incremental, reversible migration

Replace a legacy path *gradually*, not in one cut. Fowler's Strangler Fig pattern: grow the new
implementation around the edges of the old one, route callers over piece by piece, and remove the old
code only once nothing routes to it. Every step is small, releasable, and reversible, so you are never
one big-bang migration away from an outage. Source:
https://martinfowler.com/bliki/StranglerFigApplication.html

In mokata terms: add the new shape → migrate callers off the old one (walking the blast-radius as you
go) → keep a shim only as long as a caller needs it → delete the old path when its blast-radius is
empty. The specific migration mechanics (routing, shim lifetime) for any given system are
**UNVERIFIED** here — confirm against the cited source and the actual call graph.

## Removals are breaking changes — version them

Removing or renaming a caller-visible symbol is breaking by definition: a caller can observe the
difference. Semantic Versioning encodes this — a breaking change to a public contract is a MAJOR bump,
never a patch. Source: https://semver.org/

So a removal is a contract change, not quiet housekeeping: announce it (a deprecation window where
practical), walk the blast-radius, migrate callers, then delete — prefer *deprecate, then remove* over
removing a caller you did not warn.

## How a removal decision becomes a recorded result in mokata

A removal decision — what was removed, which callers migrated, why, the deprecation window — is
recorded as a typed `decision` memory item through the human-gated WriteGate (secret-scan → human
approval → audit) and written to the audit ledger under the `domain` kind, so a future reader learns
why the code is gone (P7). The blast-radius walk on the removed symbol is the EXISTING mokata
instrument (the same one the `api` domain uses on callers); a removal from an approved plan is a plan
change and routes through the EXISTING deviation gate. This skill adds **no new gate**.
