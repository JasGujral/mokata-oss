---
name: review-window
description: mokata · Open the review window for THIS session — live diff, any point in time, PRs.
---

# mokata · /review-window

mokata's prompt hook has ALREADY opened the review window — or, if it was already open, re-focused it on THIS session (its run, the files it wrote, its timeline). The link is in the context mokata added to this turn (`mokata ide opened: …` or `mokata ide re-focused: …`). Give the user that one line and nothing else; do not open anything yourself.

If no such line is in this turn's context (the hook did not run — mokata's hooks are not wired, or this harness has none), run `mokata ide --no-browser` and give the user the link it prints. If it prints an install line, pass that on instead.

The window is READ-ONLY: it shows the working tree against HEAD, what each session wrote, the diff between any two moments of a session's writes, branch-to-branch comparisons and GitHub PRs by URL (fetched with the user's own git login). `mokata ide --demo` shows a clearly labelled SAMPLE dataset for reviewing the UI.

## Gate (check)
Read-only: the window writes nothing to the repository. Its only network call is a PR fetch the user asks for.

## Standalone
This command runs on its own — no upstream pipeline phase is required. It applies only its own gate above, and never silently skips a gate of a phase you did run.
