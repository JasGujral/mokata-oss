---
name: review
description: Two-pass review: against the spec, then quality.
---

# mokata · /review

Review a diff in two passes. (1) Against the spec: does it do exactly what was specified — nothing more? (2) Quality: correctness, clarity, simplicity. Surface findings clearly; any fix is human-gated.

## Gate (human)
Review checks the diff against the spec (no extra features) first, then quality. Findings are surfaced for human-gated fixes.

## Standalone
This command runs on its own — no upstream pipeline phase is required. It applies only its own gate above, and never silently skips a gate of a phase you did run.
