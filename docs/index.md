# mokata

**Spec-driven TDD for Claude Code — knowledge-aware, self-healing memory, human-gated, local-first.**

mokata is an open-source framework for Claude Code that brings the strongest ideas in
AI-assisted coding into one governed engine. It brainstorms the problem with you, drafts a
spec, refuses to write code until every acceptance criterion maps to a test (RED before
GREEN), and reviews the result back against the spec. Around that engine sit a codebase
knowledge graph, persistent self-healing memory (on by default), active token governance,
and a full audit trail — with **every durable write human-gated** and **nothing leaving
your machine** unless you wire it.

mokata is pure Python (≥ 3.9), has **no required runtime dependencies**, is Apache-2.0
under **MoStack**, and is built clean-room (no dependency on any other framework).

**Three tiers, in priority order:** (1) the **Claude Code plugin** (standard, from the public
marketplace); (2) **Claude Code without the public marketplace** — the plugin from a local
clone, or one command, `mokata setup claude` (no registration); (3) the **CLI, with any AI
tool** (Gemini, Codex, scripts, CI). The first two give the LLM the full workflow; the CLI is
the engine's mechanics and comes last. See [Getting started](quickstart.md).

mokata never calls a model itself — the brain always comes from the harness. For *why* there
are two ways to run it (and which fits your goal), see
[How mokata uses an LLM: harness vs CLI](concepts/execution-model.md).

## What's here

This site follows the [Diátaxis](https://diataxis.fr/) model:

- **[Getting started](quickstart.md)** — install and run your first pipeline in minutes.
- **Tutorials** — [**mokata catches a bad change**](tutorials/catches-a-bad-change.md): the 60-second wow demo (copy-paste it and watch the seatbelt catch a bad change); [**differentiators in action**](tutorials/differentiators-in-action.md): a runnable demo of every differentiator (graph, memory, governance — see them work); [run a story end-to-end](tutorials/run-a-story.md): a guided, learn-by-doing walkthrough; and [the Complete Guide](tutorials/mokata-complete-guide.md): every command, gate, and layer (with a downloadable PDF).
- **How-to guides** — task recipes: [configure a profile](how-to/configure-a-profile.md),
  [set the execution mode](how-to/set-execution-mode.md),
  [use & heal memory](how-to/use-memory.md),
  [write a skill](how-to/write-a-skill.md),
  [integrate other tools](how-to/integrate-other-ai-tools.md),
  [share a stack](how-to/share-a-stack.md),
  [install the Claude plugin](how-to/install-plugin.md),
  [use mokata without the plugin](how-to/use-without-plugin.md).
- **Concepts** — how each layer works: the [pipeline & gates](concepts/pipeline.md),
  [knowledge layer](concepts/knowledge.md), [memory](concepts/memory.md),
  [token governance](concepts/token-governance.md),
  [execution modes](concepts/execution-modes.md),
  [governance & audit](concepts/governance.md).
- **Reference** — complete specs: [CLI](reference/cli.md),
  [manifest & configuration](reference/manifest.md), [skills catalog](reference/skills.md).
- **[Developer guide](developer-guide.md)** — architecture, dev setup, testing, contributing.

## The feature set at a glance

| Part | Area | Highlights |
|---|---|---|
| A | Spine | manifest, capability router, detection + graceful degradation, bootstrap, `init` |
| B | Knowledge | adopted code graph + grep floor, typed queries, incremental index + staleness, drift anchors |
| C | Memory | persistent / decision / episodic, self-healing (surfacing), pluggable backends, consolidation |
| D | Engine | 7-phase pipeline, provable completeness gate, AC-mapper, pre-mortem, spec-compliance, dry-run |
| E | TDD & execution | RED-before-GREEN, model routing, bug/debug/optimize engines, execution-mode selector |
| F | Token governance | tracker, JIT retrieval, handback caps, output density, budget, cache-stable prefixes |
| G | Rules & governance | 4-tier rules, taxonomy, sync/async hooks, Karpathy gates, rule-learning, skill authoring |
| I | Safety & audit | secret protection, human-gated writes, audit ledger, lethal-trifecta gate, revert, resume |
| J | Distribution | plugin/marketplace packaging, cross-harness boundary, shareable stack manifests |
| K | Config | per-layer/tool toggles, profiles, local-first, committed config, trust dial, doctor, reset |
| L | Composability | standalone commands, mid-pipeline entry, direct skills, catalog, chaining, suggestions |

Published docs: <https://jasgujral.github.io/mokata-oss/> · Source & issues:
<https://github.com/JasGujral/mokata-oss>.
