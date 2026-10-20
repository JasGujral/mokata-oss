---
name: backend-nestjs
description: mokata · NestJS rule pack — impact-ordered rules for modules, guards, DTOs and providers, loaded by the spec's routing.
when_to_use: Engage when mokata's per-turn routing or `spec show` names `backend-nestjs` for this run, when the spec's `domains` constraint names `backend-nestjs`, or when the approach's blast radius touches a controller, module, provider, guard, pipe or DTO file in a package that depends on `@nestjs/core`. Do NOT engage for a plain Express, Fastify or Koa server without NestJS, for a frontend-only change, or for a TypeScript library that does not depend on `@nestjs/core`.
---

> **mokata Agent Skill.** This is mokata's `backend-nestjs` domain knowledge. mokata LOADS it — it
> is routed by the approved spec's `domains` (classified from the approach's graph surface and the
> touched package's declared dependencies), not by matching words in a prompt. It enriches develop
> and review and feeds the blast-radius walk that already runs there. mokata's non-negotiables still
> hold — durable writes are **human-gated**, and a contract change is walked to its callers first.

⛭ mokata backend-nestjs active — gate: a contract change is walked to its callers before it lands; the decision is recorded to memory + the ledger

# mokata · NestJS rule pack

This file is an INDEX. Each rule lives in its own file under `references/` with an impact level,
the version branch it applies to, an **Incorrect** and a **Correct** example, and how to verify it.
Open only the rules the change touches.

## Step 0 — read the project, not your memory
Read the touched package's `package.json` for the `@nestjs/core` major, the HTTP platform
(`@nestjs/platform-express` or `@nestjs/platform-fastify`), the test runner and the validation
library in use. Version facts and defaults belong to the official docs (https://docs.nestjs.com/)
for that major, not to this pack. What you could not confirm there is **UNVERIFIED**, and you say so.

## Version branches
- **Current major (CommonJS build, Jest by default, class-validator DTOs)** — every rule applies as
  written.
- **A major that moves to ESM, Vitest and Standard Schema validation** — the rules still hold; the
  mechanics change: validation may be a schema object rather than decorators
  (`dto-is-a-runtime-class` becomes "the schema is a runtime value"), test setup follows the
  project's runner, and import paths follow ESM resolution. Which major made which change is
  **UNVERIFIED** here — read the release notes for the version the project pins.

## Rules — highest impact first

| Impact | Rule (file in `references/`) |
|---|---|
| CRITICAL | `authorize-in-guards-not-middleware.md` — authorization belongs in guards, every route guarded or explicitly public |
| CRITICAL | `dto-is-a-runtime-class.md` — a DTO imported with `import type` silently disables validation |
| CRITICAL | `validation-rejects-unknown-fields.md` — the global validation pipe strips and rejects unknown fields |
| HIGH | `verify-every-jwt-claim.md` — pin the algorithm, audience and issuer when verifying tokens |
| HIGH | `request-scope-bubbles-up.md` — a request-scoped provider makes every dependant request-scoped |
| HIGH | `no-barrels-between-modules.md` — barrel imports between modules create cycles that `forwardRef` only hides |
| MEDIUM | `provide-what-you-inject.md` — every injected provider is provided or exported by an imported module |
| MEDIUM | `errors-as-http-exceptions.md` — map failures to HTTP exceptions; never leak internals in responses |
| MEDIUM | `test-with-a-testing-module.md` — unit tests build a testing module and override providers; e2e drives HTTP |

## Attachment to the flow
- **develop** — mokata names this skill on the per-turn pack in develop. A change to a controller
  signature, a DTO, or a module's exports is a contract change: walk its callers
  (`mokata query callers <symbol>`) before you edit, then write the failing test.
- **review** — mokata names it again in review. Walk the CRITICAL rows against the diff first;
  an unguarded handler or a type-only DTO import is a Blocking finding.
- **security** — a NestJS change that crosses a trust boundary also routes the `security` skill.

## Gate
This skill feeds the **blast-radius** instrument: a contract change is walked to its callers before
it lands, and the decision is recorded. A static "every route is guarded" check is not part of this
skill; until such a check is backed in code, rule 1 is review discipline, and this skill never
claims a gate passed that does not exist.

## If this appears stuck

This guidance runs inside a pipeline phase. If that phase appears stuck, or the harness stops it mid-run: run `mokata resume` (where the run stands and the gate that still applies) and `mokata approve --list` (anything waiting on a human). Nothing is lost — run state, approvals and the audit ledger are on disk — so re-invoke the phase to continue; `mokata doctor` names a hung MCP server or a hook that is not firing.

## Rationalizations — stop if you catch yourself thinking any of these

| Rationalization | Reality |
|---|---|
| "The auth middleware already checks the token." | Middleware runs before routing details are settled and is path-matched by the adapter; authorization in a guard is the layer that sees the resolved handler. |
| "`import type` is cleaner for the DTO." | The type disappears at runtime, so the validation pipe has no class to validate against — every field passes. |
| "Unknown fields are harmless, we ignore them." | Without whitelisting they reach the service; mass-assignment is exactly an unexpected field written to the database. |
| "`forwardRef` fixed the circular import." | It hid it. The cycle is a module boundary problem; split or invert the dependency. |
| "REQUEST scope is just a bit slower." | It is transitive: every provider and controller depending on it becomes per-request too. |

## Verification — confirm each before you claim this skill is done

Evidence, not "seems right" — check every box or say which is unmet and why:

- [ ] the `@nestjs/core` major, platform and test runner were READ from the project (cited)
- [ ] every new or changed route handler has a guard or an explicit, reviewed public marker
- [ ] every DTO in the diff is a value import of a class (or a runtime schema), never `import type`
- [ ] the callers of every changed controller/provider/DTO contract were walked before the edit
- [ ] a failing test (testing module or e2e) existed before the behaviour changed

## References — pulled in just-in-time (not loaded inline)

- `references/authorize-in-guards-not-middleware.md`, `references/dto-is-a-runtime-class.md`, `references/validation-rejects-unknown-fields.md` — the CRITICAL rules
- `references/verify-every-jwt-claim.md`, `references/request-scope-bubbles-up.md`, `references/no-barrels-between-modules.md` — the HIGH rules
- `references/provide-what-you-inject.md`, `references/errors-as-http-exceptions.md`, `references/test-with-a-testing-module.md` — the MEDIUM rules

## Contract

**CAN**
- read the project's NestJS major, platform and validation library, and apply that branch's rules
- walk the callers of a changed controller, provider, module export or DTO before editing it
- report each rule violation in review with `file:line` and the rule file it breaks

**MUST NOT**
- write a secret into code, config, or a module's options (gate: secret-guard)
- change an approved spec's API surface beyond approval (gate: deviation)
- persist a NestJS convention to memory without the write gate (gate: write-gate)
- claim a route-guard check passed when no such check is backed for this project (advisory)

**DEPENDS ON**
- the spec carrying the `backend-nestjs` domain (routed at emit from the graph surface + package.json; amend-in if reached late) (advisory)
- the code graph for callers — degrades to reading/grepping the module tree, and says so (advisory)

> Grounding: `(gate: …)` boundaries are enforced by that gate in code; `(advisory)` ones are protocol discipline this skill follows, not a hard block.
