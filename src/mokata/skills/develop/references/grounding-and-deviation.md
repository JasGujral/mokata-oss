# develop — grounding and deviation, in depth (reference)

Pulled in just-in-time. The SKILL.md carries the rule; this file carries the drill.

## Verify or ask — never assume

Before you assert anything about a type, signature, behaviour, control-flow, convention,
dependency, error path, or file layout, VERIFY it against the actual code:

- read the source you're about to change,
- run the structural queries (`callers`, `callees`, `implementers`, `imports`, `blast_radius`),
- check memory for prior decisions and conventions on the symbols in play.

For a fact about a framework or library you did NOT read from the code, ground it in the
official documentation for the exact version in the dep file, and cite the URL (G-C). If you
cannot verify a fact from code or an official source, mark it **UNVERIFIED** and ASK — there is
no "assumed and continued" path.

## The signature develop trap: "I'll clean this up while I'm here"

A green test does not license a drive-by refactor. Unasked cleanup — renaming, reformatting a
neighbour, "improving" an unrelated helper — is unapproved scope. Leave it, or if it genuinely
matters, surface it and route it through the deviation gate. Keep the change surgical: the
minimum needed to turn the failing test green.

## Discovered an assumption mid-flight → STOP

If, partway through, you find a decision rested on an assumption, or the code contradicts
something you assumed:

1. STOP — do not keep building on it.
2. Surface it: what you assumed vs. what the code shows.
3. CONFIRM with the human and re-plan.
4. Route the change through the **deviation gate**, and amend the spec/ACs so every criterion
   still maps to a test and stays provable.

## What re-enters approval

A plan change is any change to scope, the chosen approach, an acceptance criterion, or the
design beyond what was approved. Each of these STOPS and re-enters the approval surface
(re-approve the approach/refinements, or amend the spec) and is logged to the audit ledger.
Never silently deviate — silent deviation is the exact failure this gate exists to prevent.
