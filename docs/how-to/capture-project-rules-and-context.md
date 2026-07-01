# How-to: capture your project's rules & context (the project brain)

mokata can hold your project's **institutional knowledge** — its rules, guardrails,
conventions, domain facts/formulas, and key points from reference docs — as **typed**,
human-gated memory that the skills then **reference when relevant**. You capture it through a
guided conversation; the inputs are **processed**, not stored verbatim.

## The typed memory parts

Each entry carries a first-class `kind`:

| kind | what it holds | how it's surfaced |
|------|---------------|-------------------|
| `rule` | hard rules ("no network in the parser") | **always-on** — in the briefing every run |
| `guardrail` | safety/quality constraints ("currency math uses Decimal") | **always-on** |
| `best-practice` | recommended patterns ("tests as `test_<name>.py`") | **JIT** — when relevant |
| `context` | domain facts/formulas ("tax_rate = 0.2") | **JIT** |
| `reference` | distilled key points from a doc, + a source pointer | **JIT** |

(plus the existing `decision` and `episodic` memory.)

## Capture it (guided, LLM-processed, gated)

```text
/mokata:onboard            # in Claude Code — the guided conversation
mokata onboard             # the same protocol from the CLI
```

`onboard` guides you **one focus at a time** (rules? guardrails? conventions? domain
facts/formulas? a doc to ingest?) — not a wall of questions. For each thing you say (or a
document you paste/link) it **distils** the essential rule/fact, **assigns the right kind**,
**normalises** the wording, and **dedups/merges** against what's already captured — routing any
**conflict** through the self-healing old→new surface (never a silent overwrite). It then shows
the proposed structured entries for **approve / edit / reject**, and persists the approved ones
through the gated write (shared per your team's memory backend). Re-run it any time to add or
update knowledge.

A secret in a value is blocked at the gate even when approved — reference an env var instead.

## See it, by category

```bash
mokata memory                    # the whole brain, grouped by kind
mokata memory --kind rule        # just the rules (or guardrail / context / reference / …)
```

A scannable, committed/reviewable view of exactly what mokata will honour.

## Edit / update an entry (a formula changes, a guardrail is revised)

```bash
mokata memory edit tax_rate --value 0.25 [--kind context] [--yes]
```

Human-gated and routed through **self-healing**: the old value is **superseded** (kept in the
record), the new one becomes active — surfaced, never silently overwritten.

## Auto-proposed guardrails (recurring corrections)

When you keep correcting the same thing — declining a write, reverting a change, hitting a spec
conflict — mokata notices and distils it into a guardrail-rule **proposal**. Running
`/mokata:onboard` (and `mokata rules`) surfaces these:

```
Proposed guardrails (recurring corrections mokata noticed — human-gated, NOT auto-added):
  - Recurring correction 'write_gate:src/x.py' — consider promoting a guardrail rule. [observed 3 times …]
```

They are **proposal-only**: approve, edit, or reject each through the normal gated capture above —
mokata **never auto-adds a rule**.

## How the skills use it (the payoff)

- **rule / guardrail** are injected into the **SessionStart briefing** and the always-on rules
  surface, so the agent honours them on **every** run — but only within the rules **line
  budget**; if you capture more than fit, the most relevant are shown and the rest flagged (run
  `mokata memory --kind rule` to see them all). The budget is **never** blown (P11).
- **context / reference / best-practice** are pulled in **just-in-time** — only when a skill's
  task is relevant to them (e.g. the pricing formula surfaces when the spec touches pricing).
  The corpus is **never** dumped wholesale; the briefing stays small.

Grounding (Stage 33) consults the brain too: decide from the captured rules + the code, never
assume. See [memory concepts](../concepts/memory.md) and
[use & heal memory](use-memory.md).
