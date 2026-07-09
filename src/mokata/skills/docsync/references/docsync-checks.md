# docsync — the cross-reference checklist (pulled in just-in-time)

The full detail behind the audit: what each check proves, the severity it carries, and how a code
graph + memory sharpen it. Loaded only when you need the depth — the SKILL.md carries the summary.

## The checks

| Check | What it proves | Severity | Reconcilable |
|---|---|---|---|
| **skill-count** | a `<N> skills` claim matches the shipping registry (curated pipeline skills, or the total incl. domain skills) | Blocking | yes — the number is corrected in place |
| **command-name** | every `mokata <cmd>` and `/mokata:<name>` a doc names is a real, live command/skill | Blocking | no — a rename can't be guessed; surface it for a human |
| **install-path** | the install / getting-started invocation is the canonical one (the real package name + setup path), not a dead one | Blocking | yes — the dead path is replaced with the canonical one |
| **version-example** | a pinned `mokata==X.Y.Z` example matches the shipping version | Info | yes — the pin is bumped |
| **symbol-ref** | a dotted symbol a doc references (e.g. `module.function`) still resolves in the code | Minor | no — surface the stale/renamed symbol |

## Grounding — where the "truth" comes from

Every check reads the SAME single sources the rest of mokata reads, never a hand-kept copy:

- **counts** come from the curated-skills registry (and the shipped domain skills on disk);
- **command names** come from the live CLI parser and the shipped skill set;
- **install / setup path** is the canonical package + getting-started invocation;
- **version** comes from the version constant.

The **symbol** and **config-key** checks are the graph/memory-sharpened ones: with a code graph
wired, docsync flags a referenced symbol the graph cannot resolve; without one, those checks
degrade cleanly (the count / command / install / version checks still run lexically). Say what you
could not verify rather than asserting it — an unverifiable claim is flagged, never quietly passed.

## Severity is an OUTPUT label, not a gate

Blocking / Minor / Info triage the findings; they add and subtract no gate. A Blocking discrepancy
exits the audit non-zero so a pre-release doc gate can act on it, but docsync itself blocks nothing —
the only durable action it takes is the reconcile write, and that always goes through the human gate.

## Reconcile is always previewed and human-gated

A reconcile builds the corrected text, shows the unified diff, and writes ONLY on explicit approval
through the universal write gate (secret-scan → human approval → audit). A decline writes nothing.
docsync reconciles the **doc** to match the code — it never edits code to match a doc.
