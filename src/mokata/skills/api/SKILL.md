---
name: api
description: mokata · API & interface design — contract-first, blast-radius on every change.
when_to_use: Engage when an approach touches a route, handler, endpoint, or public interface, when the spec's `domains` constraint names `api`, or when a change alters a request/response shape, a signature, a status code, or any contract other code calls. Do NOT engage for a purely internal refactor that changes no caller-visible contract, or for UI-only or storage-only work with no interface surface.
---

> **mokata Agent Skill.** This is mokata's `api` domain knowledge, attached to the pipeline so
> Claude engages it automatically when an approach touches an interface surface. It is NOT a
> parallel workflow: it enriches brainstorm/develop/review, feeds the instrument that already runs
> there (blast-radius), and records its decisions to memory + the audit ledger. mokata's
> non-negotiables still hold — durable writes are **human-gated**, and the gate it feeds is never
> silently skipped.

⛭ mokata api active — gate: a contract change is walked to its callers before it lands; the decision is recorded to memory + the ledger

# mokata · API & interface design

An API is a **contract**, not just code that happens to be callable. The moment another module,
service, or client depends on a shape you exposed — a route, a signature, a status code, a field,
an ordering, an error — that shape is a promise. This skill makes mokata treat interface work as
contract work: classify the domain from the graph surface, walk the blast-radius to every caller
before a change lands, and record the contract decision so the next change can see it.

## Contract-first: decide the interface before the implementation
Design the contract before you build behind it. Name the operation, its inputs, its outputs, its
error cases, and its invariants FIRST — then implement to that contract. A contract stated up front
is reviewable, testable, and mockable independently of the code behind it; a contract that emerges
from implementation is whatever the code happened to do, which nobody agreed to. When a contract is
written as an artifact (an OpenAPI document, a typed interface, a schema), that artifact is the
source of truth both sides build against — see the OpenAPI Specification
(https://spec.openapis.org/oas/latest.html) as one primary, machine-readable contract format. The
precise conformance guarantees of any given tooling are **UNVERIFIED** here — confirm against the
spec version in use before relying on generated-code behaviour.

## Hyrum's Law: every observable behaviour will be depended upon
> With a sufficient number of users of an API, it does not matter what you promise in the contract:
> all observable behaviours of your system will be depended on by somebody. (Hyrum's Law,
> https://www.hyrumslaw.com/)

The practical consequence for mokata: the *documented* contract is a lower bound on what you have
actually promised. Incidental behaviours — an error message string, the order of a list you never
promised to sort, a timing, a default that filled an omitted field, an undocumented status code —
become de-facto contract once callers rely on them. So a "safe" change is not one that keeps the
documented contract; it is one whose blast-radius shows no caller relies on the observable behaviour
you are altering. This is exactly why this skill's governed edge is **blast-radius on callers**, not
a documentation diff.

## HTTP contracts: use the semantics the standard defines
For HTTP interfaces, the method and status-code semantics are standardized in RFC 9110 (HTTP
Semantics, https://www.rfc-editor.org/rfc/rfc9110) — build to them rather than inventing local
conventions:
- **Safe** methods (GET, HEAD) MUST NOT be relied on to cause state change; **idempotent** methods
  (GET, HEAD, PUT, DELETE) can be retried without changing the result beyond the first application
  (RFC 9110 §9.2.1–§9.2.2). Retriability is a contract clients assume — honour it.
- Status codes carry meaning clients branch on (2xx success, 4xx client error, 5xx server error,
  RFC 9110 §15). Changing the code a case returns is a contract change even when the body is
  unchanged.
- Requirement keywords (MUST / SHOULD / MAY) are defined in RFC 2119
  (https://www.rfc-editor.org/rfc/rfc2119) and RFC 8174 — use them precisely when you state a
  contract clause; they are not emphasis.
- JSON payloads follow RFC 8259 (https://www.rfc-editor.org/rfc/rfc8259); adding a member is
  generally backward-compatible for tolerant readers, removing or retyping one is not.

## Evolution: version breaking changes, extend compatibly
Distinguish a **backward-compatible** change (callers keep working: a new optional field, a new
endpoint, a new accepted input) from a **breaking** one (a caller can observe the difference: a
removed/renamed field, a narrowed type, a changed status code, a new required input, a changed
default). Semantic Versioning (https://semver.org/) encodes exactly this distinction — a breaking
change to a public contract is a MAJOR bump; compatible additions are MINOR. Prefer *extend, then
deprecate* over *break*: add the new shape, migrate callers, then remove the old one through the
deprecation domain's blast-radius-on-removal path — never break a caller you did not walk to.

## Attachment to the flow (what mokata does that a doc cannot)
- **brainstorm** — when the chosen approach's graph surface touches routes/handlers/interfaces, the
  domain classifier puts `api` in the spec's `domains`, so the contract concern is a first-class,
  human-approved constraint, not a footnote.
- **develop** — before changing a shared/exported symbol, run `mokata query callers <symbol>` /
  `blast_radius <symbol>` and design the change against the REAL callers found, not assumed ones.
- **review** — the **Architecture** axis activates: run the design-fit lens over the change and
  check blast-radius on any contract/shared-symbol change so a caller is not silently broken.
- **memory + ledger** — a contract decision (what is frozen, what may still change, why) is recorded
  as a typed `decision` through the human gate and written to the audit ledger, so it is walkable
  later (P7). Record it with `mokata`'s domain-decision path — never as loose prose.

## Gate (instrument)
This skill feeds **blast-radius** (an SK.S1/S2 instrument, not a hard gate): a contract/shared-symbol
change is walked to its callers before it lands. It is advisory — it informs the change, it does not
block it — but the *durable write* recording the contract decision is human-gated (write-gate), and
any behaviour change to an approved spec still routes through the deviation gate.

## Grounding discipline
Decide from the code and the standard, not from assumption. Before asserting a method is idempotent,
a field optional, a status code unused, or a caller absent, VERIFY it: read the handler, run the
structural queries (`callers` / `callees` / `blast_radius`), and for HTTP/protocol behaviour read the
cited RFC for the version in use and CITE the URL. Prefer the primary source (the RFC, the OpenAPI
document, the standard) over memory or a blog. Flag anything you could not verify as **UNVERIFIED**
rather than stating it as fact.

## Rationalizations — stop if you catch yourself thinking any of these

| Excuse | Reality |
|---|---|
| "It's a small field rename — callers will adapt." | A rename is a breaking change; walk the blast-radius first — Hyrum's Law says someone already depends on the old name. |
| "The docs don't promise this behaviour, so I can change it." | Observable ≠ documented. If callers can see it, they may depend on it; the blast-radius, not the doc, decides if it's safe. |
| "I'll design the contract as I code." | Contract-first: state inputs/outputs/errors/invariants BEFORE implementing, or you ship whatever the code happened to do. |
| "Changing the status code is just a tweak." | Clients branch on status codes (RFC 9110 §15) — a code change is a contract change even with an identical body. |

## Verification — confirm each before you claim this skill is done

Evidence, not "seems right" — check every box or say which is unmet and why:

- [ ] the change was classified as an `api` domain in the spec (or amended in when reached late)
- [ ] blast-radius was walked on every contract/shared-symbol change (`callers` / `blast_radius`)
- [ ] backward-compatible vs breaking was decided explicitly; a breaking change was versioned or deprecated, not silently landed
- [ ] HTTP/protocol claims are grounded in the cited RFC for the version in use (URL cited)
- [ ] the contract decision was recorded to memory + the ledger (human-gated), not left as prose

## References — pulled in just-in-time (not loaded inline)

- `references/api-contracts.md` — the RFCs, Hyrum's Law, semantic versioning, and contract-first evolution in full, each with its primary-source URL

## Contract

**CAN**
- classify the interface surface and design the contract first (inputs/outputs/errors/invariants)
- walk blast-radius on callers before a contract/shared-symbol change
- record the contract decision as a typed `decision` to memory + the ledger (human-gated)

**MUST NOT**
- persist a contract decision without the write gate (gate: write-gate)
- change an approved spec's contract scope/shape beyond approval (gate: deviation)
- land a contract change without walking its callers, or state an unverified protocol behaviour as fact (advisory)

**DEPENDS ON**
- the spec carrying the `api` domain (classified at brainstorm; amend-in if reached late) (advisory)
- the code graph for blast-radius — degrades to reading/grepping callers, and says so (advisory)

> Grounding: `(gate: …)` boundaries are enforced by that gate in code; `(advisory)` ones are protocol discipline this skill follows, not a hard block.
