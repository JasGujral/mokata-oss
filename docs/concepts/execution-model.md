# How mokata uses an LLM: harness vs CLI

mokata is a **framework around an LLM, not an LLM itself.** It has no model client, no API
key, and no required dependencies — it never calls a model or picks a vendor. The reasoning
("the brain") always comes from whatever **harness** runs mokata. There are two ways to run
it, and which you pick depends on whether you want code written *with* you, or just the
engine's deterministic mechanics.

## Approach A — Harness-driven (the brain runs the protocol)

mokata supplies the **structure**: the phase prompts (`/mokata:brainstorm`, `/mokata:spec`,
`/mokata:test`, `/mokata:develop`, `/mokata:review`), the gates (HARD-GATE before spec,
completeness gate, RED-before-GREEN, secret-guard), the knowledge graph, the self-healing
memory, and the audit trail. The harness's LLM supplies the **reasoning** — it brainstorms
approaches, drafts the spec, writes the tests and code, and reviews — all constrained by
mokata's gates.

- **Where:** inside **Claude Code** — via the plugin, or `mokata setup claude` (install
  tiers 1 and 2). Here **Claude is the brain**, on your existing Claude Code sign-in. Nothing
  to configure, no API key.
- **What "picks the model":** the harness, not mokata. mokata's model router only *suggests*
  a capability tier (`fast` / `balanced` / `deep`) for cost control; the harness maps that to
  a real model.
- **Use it for:** day-to-day feature work; spec-driven TDD where the gates, memory, and
  knowledge actively shape the LLM's output; anyone working inside Claude Code.

## Approach B — CLI as mechanics (the engine without a brain)

The `mokata` CLI runs the **deterministic engine** with no LLM attached: it enforces gates,
runs structural queries, manages state and config, prints the audit and budget, and runs the
playbook check. `mokata brainstorm` *prints the brainstorm protocol* (grounded against your
graph + memory) — it does not reason about your problem; a brain would do that.

- **Where:** any terminal, script, or CI — and any non-Claude harness (Gemini, Codex) that
  supplies its own LLM (install tier 3).
- **Use it for:** scripting and CI (run gates/coverage/validation programmatically);
  inspection (`mokata query`, `mokata audit`, `mokata status`, `mokata doctor`); wiring mokata
  into a different AI tool; smoke-testing that the plumbing works; any LLM-free, deterministic
  operation.
- **Why it exists:** it's dependency-light and runs anywhere, the "lowest common denominator"
  integration — the engine you can call from anything.

> **The CLI alone does not give you mokata *inside* Claude Code.** A `pip install` puts the
> `mokata` command in your terminal (Approach B). To drive the gated workflow *with Claude as
> the brain* (Approach A), install the [plugin](../how-to/install-plugin.md) or run
> [`mokata setup claude`](../how-to/use-without-plugin.md).

## Which should I use?

| You want to… | Approach | How |
|---|---|---|
| Have features written *with* you, gated and memory-aware | A — harness | Claude Code plugin or `mokata setup claude` |
| Inspect a repo, query structure, read the audit | B — CLI | `mokata query` / `audit` / `status` |
| Run gates/checks in CI or a script | B — CLI | `mokata playbook`, `validate`, `coverage` |
| Use mokata with Gemini / Codex / another agent | B — CLI (+ that harness's brain) | wire the CLI/MCP into that harness |

They are the **same engine**: Approach A is the engine with a brain attached; Approach B is
the engine alone. And in both, the LLM is never mokata's — it's the harness's. That's the
local-first guarantee: mokata holds no credentials and phones nothing home.

## Skills: emit (CLI) vs execute (Claude Code)

A **skill** is a reusable, gated capability — a **prompt (the protocol) + its gate**
(e.g. `review` = the review protocol + the spec-compliance gate). Skills are the same in both
approaches because mokata generates them from **one definition** (`skills.py`): the same
source produces the CLI launch text *and* the `/mokata:<name>` slash command, so they can
never drift. What differs is how the skill is *consumed*:

| | From the CLI (Approach B) | Inside Claude Code (Approach A) |
|---|---|---|
| Invoke | `mokata run review` / `mokata skills review` | `/mokata:review` |
| What happens | **emits** the skill — prints the prompt + gate + live grounding (what graph/memory is available) | **executes** the skill — Claude does the review using that prompt |
| Who reasons | nobody (you/another tool act on the output) | Claude (the brain), under the same gate + hooks |
| Result | the recipe + the rule | the work, gated |

So: **same skill, two modes — emit vs execute.** The CLI hands you the recipe and enforces
the rules; the harness's LLM performs the work. This is why a skill behaves "differently"
from the CLI vs inside Claude Code without being two different things — it's one definition,
consumed two ways.
