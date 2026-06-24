# How-to: write a skill (test-first)

Skills are authored **RED-GREEN-REFACTOR-for-docs** (G6): declare the doc requirements,
watch them fail, then write the content.

```python
from mokata.govern import SkillDraft
from mokata.skills import Gate

draft = (SkillDraft("clarify")
         .require("gate section", "## Gate")
         .require("trigger", "Use when"))

draft.check().passed          # False — RED (no content yet)
draft.status                  # "red"

draft.write("## Gate only")   # missing the trigger requirement
draft.check().passed          # still False

draft.write("## Gate\nUse when the spec is ambiguous; ask one question at a time.")
draft.check().passed          # True — GREEN

skill = draft.to_skill("clarify ambiguous specs",
                       Gate("clarify-gate", "ask before assuming"))
```

## Register it

Add the `Skill` (name, summary, prompt, gate) to `src/mokata/skills.py`, then regenerate
its slash-command template so the shipped `/<name>` command and the CLI stay in sync:

```python
from mokata.skills import command_markdown, get_skill
open("templates/commands/clarify.md", "w").write(command_markdown(get_skill("clarify")))
```

Each skill runs standalone (`mokata run clarify`) and applies only its own gate. See the
[skills reference](../reference/skills.md).
