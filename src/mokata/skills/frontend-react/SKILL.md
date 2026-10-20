---
name: frontend-react
description: mokata · React (web) rule pack — impact-ordered rules, loaded by the spec's routing, each with an Incorrect/Correct pair.
when_to_use: Engage when mokata's per-turn routing or `spec show` names `frontend-react` for this run, when the spec's `domains` constraint names `frontend-react`, or when the approach's blast radius touches a `.jsx`/`.tsx` file in a package that depends on `react-dom` or `next`. Do NOT engage for a React Native or Expo project (no DOM, different rules), for a plain TypeScript library with no React dependency, or for a backend-only change.
---

> **mokata Agent Skill.** This is mokata's `frontend-react` domain knowledge. mokata LOADS it — it
> is routed by the approved spec's `domains` (classified from the approach's graph surface and the
> touched package's declared dependencies), not by matching words in a prompt. It enriches develop
> and review; it is not a parallel advisory. mokata's non-negotiables still hold — durable writes
> are **human-gated**, and no component behaviour lands without a failing test first.

⛭ mokata frontend-react active — gate: implementation is allowed only against a recorded FAILING (RED) test

# mokata · React (web) rule pack

This file is an INDEX. Each rule lives in its own file under `references/` with an impact level,
the version branch it applies to, an **Incorrect** and a **Correct** example, and how to verify it.
Open only the rules the change in front of you touches — never the whole pack.

## Step 0 — read the project, not your memory
Before applying any rule, read the touched package's `package.json` for the exact `react` major and
whether the React Compiler is enabled (the `babel-plugin-react-compiler` dependency, or the
framework's own compiler option — read the framework's config file, do not assume a default). The
version facts live in the project's dependency file and the framework's official docs
(https://react.dev/reference/react · https://react.dev/learn/react-compiler); this pack does not
restate them. Anything you could not confirm there is **UNVERIFIED** and is said out loud.

## Version branches
- **React ≥ 19, Compiler ON** — the compiler memoizes for you. Hunt silent **bailouts**, not missing
  `useMemo`. Do not add manual memoization by reflex (`compiler-hunt-bailouts`).
- **React ≥ 19, Compiler OFF** — memoize only what a measurement or an effect's dependency identity
  demands (`memo-only-when-measured`). Form actions and `useActionState` are available
  (`actions-for-pending-and-errors`).
- **React < 19** — the action rules do not apply; everything else does.

## Rules — highest impact first

| Impact | Rule (file in `references/`) | Applies |
|---|---|---|
| CRITICAL | `render-is-pure.md` — no side effects or mutation during render | all |
| CRITICAL | `hooks-at-top-level.md` — hooks never run conditionally or in loops | all |
| CRITICAL | `server-boundary-serializes-only-what-the-client-may-see.md` — props crossing to a client component are public | Server Components |
| HIGH | `derive-instead-of-syncing-state.md` — compute during render, do not mirror props into state with an effect | all |
| HIGH | `stable-keys.md` — list keys are stable identities, never array indexes for reorderable data | all |
| HIGH | `test-behaviour-through-the-dom.md` — component tests query by role/label and drive real events | all |
| MEDIUM | `compiler-hunt-bailouts.md` — find what the compiler skipped before tuning by hand | ≥19, Compiler on |
| MEDIUM | `memo-only-when-measured.md` — manual memoization needs a measurement or an effect-identity reason | Compiler off |
| MEDIUM | `actions-for-pending-and-errors.md` — use form actions / `useActionState` instead of hand-rolled pending flags | ≥19 |
| LOW | `refs-are-not-render-input.md` — never read `ref.current` to decide what to render | all |

## Attachment to the flow
- **develop** — mokata names this skill on the per-turn pack while the run is in develop. Write the
  failing component test first (`test-behaviour-through-the-dom`), then the minimum component.
- **review** — mokata names it again in review. Walk the CRITICAL and HIGH rows against the diff;
  each violation is a finding with `file:line` and the rule file it breaks.
- **performance / security** — a React change that also touches a hot path or a trust boundary
  routes `frontend-perf` / `frontend-security` too; follow those skills for that half.

## Gate
This skill feeds the **no-code-without-failing-test** gate: a component's behaviour is implemented
only against a recorded RED test. Lint- and bundle-level checks for React are owned by gates that
are not part of this skill; until such a gate is backed in code, running a linter here is
discipline, not a gate this skill may claim.

## If this appears stuck

This guidance runs inside a pipeline phase. If that phase appears stuck, or the harness stops it mid-run: run `mokata resume` (where the run stands and the gate that still applies) and `mokata approve --list` (anything waiting on a human). Nothing is lost — run state, approvals and the audit ledger are on disk — so re-invoke the phase to continue; `mokata doctor` names a hung MCP server or a hook that is not firing.

## Rationalizations — stop if you catch yourself thinking any of these

| Rationalization | Reality |
|---|---|
| "I'll wrap it in `useMemo` to be safe." | With the Compiler on that is noise that can hide a bailout; with it off it is a cost with no measured win. Measure or leave it out. |
| "An effect that copies props into state is fine." | It renders twice and drifts out of sync. Derive the value during render. |
| "The index is a fine key, the list never changes." | Lists change: filters, sorts, inserts. An index key reuses the wrong component state. |
| "I'll test the hook's internal state." | Users see the DOM. Query by role/label and assert what is shown; internals are free to change. |
| "The server component can pass the whole record down." | Everything crossing to a client component is shipped to the browser. Pass only the fields the UI shows. |

## Verification — confirm each before you claim this skill is done

Evidence, not "seems right" — check every box or say which is unmet and why:

- [ ] the React major and the Compiler setting were READ from the project (cited), not assumed
- [ ] a failing component test (queried by role/label) existed before the component changed
- [ ] every CRITICAL rule touched by the diff was checked, with the rule file named in the finding
- [ ] no manual memoization was added without a measurement or an effect-identity reason
- [ ] no prop crossing a server→client boundary carries data the user may not see

## References — pulled in just-in-time (not loaded inline)

- `references/render-is-pure.md`, `references/hooks-at-top-level.md`, `references/server-boundary-serializes-only-what-the-client-may-see.md` — the CRITICAL rules
- `references/derive-instead-of-syncing-state.md`, `references/stable-keys.md`, `references/test-behaviour-through-the-dom.md` — the HIGH rules
- `references/compiler-hunt-bailouts.md`, `references/memo-only-when-measured.md`, `references/actions-for-pending-and-errors.md`, `references/refs-are-not-render-input.md` — the MEDIUM/LOW rules and the version branches

## Contract

**CAN**
- read the project's React version and Compiler setting, and apply only the rules for that branch
- write the failing component test first, then the minimum component that turns it green
- report each rule violation in review with `file:line` and the rule file it breaks

**MUST NOT**
- implement component behaviour without a recorded failing test (gate: no-code-without-failing-test)
- change an approved spec's UI scope beyond approval (gate: deviation)
- persist a React convention to memory without the write gate (gate: write-gate)
- claim a lint or bundle gate passed when no such gate is backed for this project (advisory)

**DEPENDS ON**
- the spec carrying the `frontend-react` domain (routed at emit from the graph surface + package.json; amend-in if reached late) (advisory)
- the touched package's `package.json` and framework config — degrades to asking for the version, and says so (advisory)

> Grounding: `(gate: …)` boundaries are enforced by that gate in code; `(advisory)` ones are protocol discipline this skill follows, not a hard block.
