"""Stage 36 — the guided onboarding protocol (the "institutional brain" capture).

`/mokata:onboard` (and `mokata onboard`) is an LLM-driven, guided conversation — like
brainstorm — that helps a team define a project's rules, guardrails, conventions, domain
context, and reference docs, and turns them into TYPED, human-gated memory the critical
skills then reference. The inputs are NOT stored verbatim: the model distils, categorises,
normalises, and dedups them into structured entries.

Clean-room: this is mokata's own protocol text; no external framework is imported or copied.
"""

from __future__ import annotations

ONBOARD_PROTOCOL = (
    "Capture this project's institutional knowledge as TYPED, human-gated memory — the durable "
    "\"project brain\" that mokata's skills will reference on every relevant run. Guide the user "
    "through it as a conversation, not a form; process what they say into structured entries; "
    "never store raw input verbatim.\n\n"

    "## Guide one focus at a time\n"
    "Ask about ONE area at a time, briefly, and let the user answer in their words — do not dump "
    "a wall of questions. Walk these focuses, skipping any the user waves off:\n"
    "1. **Rules** — hard constraints mokata must ALWAYS honour here (e.g. \"no network in the "
    "parser\").\n"
    "2. **Guardrails** — safety/quality constraints (e.g. \"all money math uses Decimal, never "
    "float\"; \"never log secrets\").\n"
    "3. **Conventions / best practices** — recommended patterns (e.g. \"tests live next to the "
    "module as test_<name>.py\").\n"
    "4. **Domain context** — facts, formulas, calculations, business constraints (e.g. "
    "\"tax_rate = 0.2\"; \"fiscal year starts in April\"; a pricing formula).\n"
    "5. **Documents to ingest** — offer to take a pasted or linked doc (architecture/domain "
    "spec) and distil its key points.\n\n"

    "## Process the input — store structured, not raw (this is the point)\n"
    "For each thing the user gives you (including any ingested document — capture the essential "
    "points, NOT the whole text):\n"
    "- **Distil** it to the essential rule/fact in clear, normalised wording.\n"
    "- **Assign the right kind**: one of `rule`, `guardrail`, `best-practice`, `context`, "
    "`reference` (a `reference` entry keeps a pointer to its source document).\n"
    "- **Dedup / merge** against what's already captured: check existing memory first "
    "(`mokata memory` / the `recall` tool). If it duplicates an entry, skip it; if it MERGES "
    "with one, combine them; if it CONTRADICTS an existing entry, do NOT overwrite — route it "
    "through the self-healing old→new surface so the change is reviewed (supersede, never "
    "silent).\n"
    "- **Propose** the resulting structured entries back to the user: show each as "
    "kind · subject · value, grouped by kind, so they can see exactly what will be remembered.\n\n"

    "## Human-gate every write, then persist (typed + shared)\n"
    "Nothing is stored until the user approves it. For each proposed entry let the user "
    "approve / edit / reject, then persist the approved ones through the gated write (the "
    "`remember` tool with its `kind` set, or `mokata memory`). Persisted entries are shared per "
    "the team's memory backend (Stage 35); rules/guardrails are always-on and committed. A "
    "secret in a value is blocked at the gate even when approved — strip it and reference an env "
    "var instead.\n\n"

    "## Surface the auto-proposed guardrails (recurring corrections)\n"
    "mokata watches the audit ledger for corrections that recur — writes you declined, changes "
    "you reverted, spec conflicts — and distils them into guardrail-rule PROPOSALS (the same "
    "proposals `mokata rules` surfaces). When this command runs it lists any such proposals. "
    "Treat each as a suggestion the user decides on: read it back, and if they want it, capture "
    "it as a `rule`/`guardrail` through the normal gated write above. PROPOSAL-ONLY — mokata "
    "never auto-adds a rule; the user approves, edits, or rejects each one.\n\n"

    "## Review, edit, and re-run anytime\n"
    "Show the user how to live with the brain: `mokata memory --kind <rule|guardrail|"
    "best-practice|context|reference|decision>` reviews what mokata will honour, grouped by "
    "kind; `mokata memory edit <subject>` updates an entry (a formula changes, a guardrail is "
    "revised) — human-gated and routed through self-healing, never silent. This skill is "
    "re-invokable: run it during setup and any time later to add or update knowledge.\n\n"

    "## Frugal by design (P11)\n"
    "More captured knowledge must NOT mean more tokens per run. Keep each entry terse. Only "
    "rule/guardrail go always-on (within the rules budget — if there are too many, help the "
    "user prioritise; never blow the budget). context/reference/best-practice are retrieved "
    "just-in-time, only when a later skill's task is relevant to them — never loaded wholesale."
)
