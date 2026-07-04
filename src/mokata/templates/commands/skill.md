---
name: skill
description: mokata · Author a new mokata skill test-first (RED-GREEN-for-docs); the write is human-gated.
argument-hint: "<name> --summary <s> --require <doc>:<must-contain> --content-file <f>"
allowed-tools: Bash, Read, Write
---

# mokata · skill (author your own governed command)

Author a new `/<name>` skill the mokata way: **test-first for docs** (G6). You declare what
the skill's content MUST contain (`--require name:must-contain`, repeatable); the draft is
checked against those requirements (**RED**) and only a passing draft (**GREEN**) is promoted
to a command. Writing the rendered command is a durable write, so it is **human-gated**.

## How to run

1. **Draft the content** to a markdown file (use Write), then decide the doc requirements it
   must satisfy — each as `--require <name>:<must-contain>`.
2. Resolve the bundled engine (read `~/.mokata/plugin-root` → `ROOT`, or a `mokata` CLI on
   PATH):

   ```bash
   PY="$(command -v python3 || command -v python)"
   ENGINE="PYTHONPATH=\"$ROOT/src\" \"$PY\" -m mokata"
   ```

3. **Check it RED→GREEN first** (writes nothing while RED):

   ```bash
   eval "$ENGINE skill author <name> --summary \"mokata · <one-liner>\" \
     --require <doc>:<must-contain> --content-file <draft.md> --path ."
   ```

   If it reports RED (a requirement unmet), revise the draft until every requirement passes.

4. **Approve the write** — only after the draft is GREEN and the user confirms, add `--yes`:

   ```bash
   eval "$ENGINE skill author <name> --summary \"mokata · <one-liner>\" \
     --require <doc>:<must-contain> --content-file <draft.md> --yes --path ."
   ```

   The write goes through the universal gate (a secret in the content is hard-blocked even
   when approved). Report where the new skill was written.
