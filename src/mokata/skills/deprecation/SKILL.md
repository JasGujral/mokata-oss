---
name: deprecation
description: mokata · Deprecation & migration — remove nothing without walking its blast-radius first.
when_to_use: Engage when an approach removes or retires a symbol, endpoint, flag, or module, when it migrates callers off an old shape onto a new one, when it deletes or replaces a legacy path or shim, when the spec's `domains` constraint names `deprecation`, or when a change is about taking code AWAY rather than adding behaviour. Do NOT engage for a purely additive change that removes nothing, or for an in-place refactor that keeps every caller-visible symbol and its callers intact.
---

> **mokata Agent Skill.** This is mokata's `deprecation` domain knowledge, attached to the pipeline
> so Claude engages it automatically when an approach takes code AWAY — a removal, a migration, a
> retired shim. It is NOT a parallel advisory: it enriches refine + develop, walks the **existing
> blast-radius** instrument over the removed symbol before anything is deleted, and records the
> removal decision to memory + the audit ledger. mokata's non-negotiables still hold — a plan change
> is **surfaced, human-gated, and audited** (the deviation gate), and it adds no new gate.

⛭ mokata deprecation active — gate: a plan change is surfaced, human-gated, and audited — never silent

# mokata · Deprecation & migration

Deleting code is a change to the contract, not a cleanup. The moment you remove a symbol, an
endpoint, a flag, or a module, every caller that still reaches it breaks — and the callers you did
not walk to are exactly the ones that surprise you in production. This skill makes mokata treat
removal as governed work: walk the blast-radius to the real callers of the thing being removed,
migrate them off first, retire the old path incrementally rather than in one cut, and record the
removal decision so the next change knows why the code is gone.

## Code is a liability, not an asset — but you still can't remove it blind
Every line of code carries ongoing cost: it must be read, maintained, tested, and reasoned about, and
it is a place a bug can live. That makes *unused* code worth removing — the goal is real. But the
liability framing does not license a blind delete: the cost of code you keep is only worth paying
down once you have proven no caller depends on it. Removal safety is a blast-radius property, not a
"looks unused" hunch. Martin Fowler's *Refactoring* frames refactoring as a disciplined series of
small, behaviour-preserving steps rather than a big rewrite (https://refactoring.com/) — a deletion
is the same discipline: prove the callers are gone, then remove, in a step you can revert.

## Chesterton's Fence — understand why it exists before you remove it
Do not remove a thing until you understand why it is there. The principle (G. K. Chesterton's
parable of the fence across a road: the reformer who cannot see the use of a fence is the last person
who should be allowed to tear it down) maps directly onto deprecation: a flag, a shim, a seemingly
dead branch, or an "obviously redundant" check may be load-bearing for a caller, a platform, or an
edge case you have not walked to. The exact wording/edition of the original source is **UNVERIFIED**
here — treat it as a named engineering principle, and let the blast-radius (who calls this, why)
answer the "why is it here" question before the delete, not after.

## The Strangler Fig — migrate incrementally, retire the old path last
Prefer replacing a legacy path *gradually* over cutting it out in one move. Martin Fowler's Strangler
Fig pattern describes growing the new implementation around the edges of the old one, routing traffic
over piece by piece, and only removing the old code once nothing routes to it any more
(https://martinfowler.com/bliki/StranglerFigApplication.html). The payoff is that every step is
small, releasable, and reversible — you are never one big-bang migration away from an outage. In
mokata terms: add the new shape, migrate callers off the old one (walking the blast-radius as you
go), keep a shim only as long as a caller needs it, and delete the old path when the blast-radius on
it is empty. The specific migration mechanics for any given system are **UNVERIFIED** here — confirm
against the cited source and the actual call graph.

## Removals are breaking changes — version them
Removing or renaming a caller-visible symbol is a breaking change by definition: a caller can observe
the difference. Semantic Versioning encodes this — a breaking change to a public contract is a MAJOR
bump, never a patch (https://semver.org/). So a removal is not a quiet housekeeping commit; it is a
contract change that must be announced (a deprecation window where practical), walked, and versioned.
Prefer *deprecate, then remove* — mark the old path, migrate callers, then delete — over removing a
caller you did not warn.

## Attachment to the flow (what mokata does that a doc cannot)
- **brainstorm** — when the chosen approach's graph surface touches a migration or removal
  (a `/migrations/` file, a symbol marked for deletion, a legacy shim), the domain classifier puts
  `deprecation` in the spec's `domains`, so the removal is a first-class, human-approved constraint.
- **refine** — scope the removal against the real callers: what depends on this, what must migrate
  first, what shim (if any) bridges the gap, and what the deprecation window is.
- **develop** — before deleting a shared/exported symbol, run `mokata query callers <symbol>` /
  `blast_radius <symbol>` and migrate the REAL callers found off it first; delete only when that
  blast-radius is empty. A caller reached only here that the spec did not capture routes through the
  ask→amend-spec loop (never a silent removal).
- **memory + ledger** — the removal decision (what was removed, which callers migrated, why, the
  window) is recorded as a typed `decision` through the human gate and written to the audit ledger,
  so a future reader learns why the code is gone (P7). Record it with mokata's domain-decision path —
  never as a bare `git rm`.

## Gate (deviation)
This skill rides **deviation** — a BACKED mokata gate: a plan change (and a removal from an approved
plan is a plan change) is surfaced, human-gated, and audited, never silent. Removing something the
approved spec did not scope, or expanding a removal beyond approval, routes through the deviation
gate. It adds **no new gate**: the blast-radius walk is the EXISTING SK.S1/S2 instrument (the same
one the `api` domain uses on callers), and the removal decision is persisted through the EXISTING
human-gated WriteGate to memory + the ledger. The blast-radius is advisory — it informs the removal,
it does not block it — but the durable write recording the decision is human-gated (write-gate).

## Grounding discipline
Decide from the call graph and the primary sources, not from assumption. Before asserting a symbol is
unused, a shim is dead, a branch is unreachable, or a caller has migrated, VERIFY it against the
actual graph (run `callers` / `blast_radius`, read the migration) and the cited source. Prefer the
primary source (Fowler's Strangler Fig and *Refactoring*, Semantic Versioning) over memory or a blog,
and CITE the URL. Flag anything you could not verify as **UNVERIFIED** rather than stating it as fact.

## Rationalizations — stop if you catch yourself thinking any of these

| Excuse | Reality |
|---|---|
| "This code is obviously unused — I'll just delete it." | "Looks unused" is a hunch; walk the blast-radius (`callers` / `blast_radius`) and prove no caller reaches it before you remove it. |
| "I don't see why this flag/shim is here, so it's safe to drop." | Chesterton's Fence: understand why it exists first; a load-bearing shim you don't understand is the one that breaks a caller. |
| "I'll rip out the old path and drop in the new one in one commit." | Prefer the Strangler Fig: migrate callers incrementally, keep a shim while needed, delete the old path only when its blast-radius is empty. |
| "It's just a removal — a small patch bump." | Removing a caller-visible symbol is a breaking change (SemVer MAJOR); announce, walk, and version it — don't land it quietly. |
| "I migrated the callers I know about." | The ones you *know about* aren't the risk; the blast-radius on the removed symbol is what tells you the callers you didn't. |

## Verification — confirm each before you claim this skill is done

Evidence, not "seems right" — check every box or say which is unmet and why:

- [ ] the removal/migration was classified as a `deprecation` domain in the spec (or amended in when reached late)
- [ ] blast-radius was walked on the removed symbol (`callers` / `blast_radius`) and the real callers migrated FIRST — the old path was deleted only when that blast-radius was empty
- [ ] the reason each removed thing existed was understood before removal (Chesterton's Fence), not assumed
- [ ] the migration was incremental and reversible (Strangler Fig), not a big-bang cut; a caller-visible removal was treated as a breaking change (versioned/announced)
- [ ] the removal decision was recorded to memory + the ledger (human-gated), so a future reader learns why the code is gone

## References — pulled in just-in-time (not loaded inline)

- `references/deprecation-migration.md` — code-as-liability, Chesterton's Fence, the Strangler Fig incremental-migration pattern, and breaking-change versioning in full, each with its primary-source URL

## Contract

**CAN**
- classify the removal/migration surface and walk blast-radius on the removed symbol before anything is deleted
- migrate callers incrementally (Strangler Fig), keep a shim only while a caller needs it, and version a caller-visible removal as breaking
- record the removal decision as a typed `decision` to memory + the ledger (human-gated)

**MUST NOT**
- remove something the approved spec did not scope, or expand a removal beyond approval, without the deviation gate (gate: deviation)
- persist a removal decision without the write gate (gate: write-gate)
- delete a shared symbol before its blast-radius is empty, or drop a shim/flag you don't understand (advisory — Chesterton's Fence)

**DEPENDS ON**
- the spec carrying the `deprecation` domain (classified at brainstorm; amend-in if reached late) (advisory)
- the code graph for blast-radius on the removed symbol — degrades to reading/grepping callers, and says so (advisory)

> Grounding: `(gate: …)` boundaries are enforced by that gate in code; `(advisory)` ones are protocol discipline this skill follows, not a hard block.
