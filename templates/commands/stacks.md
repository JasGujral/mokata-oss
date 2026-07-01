---
name: stacks
description: mokata · Community stacks — discover a curated catalog of governed per-framework stacks and install one (human-gated, secret-scanned adopt). No hosted marketplace; publish over git/the vault.
argument-hint: "list | search <query> | show <name> | install <name> [--source <dir>] [--force]"
allowed-tools: Bash, Read
---

# mokata · stacks (adopt a ready-made governed stack for your framework)

A **stack** is a publishable, governed starting point for a framework: a schema-valid mokata
**config** + a curated **rule/guardrail set** + the relevant **skills**. Discover one from a
curated catalog, then **install** it as your project's config in one human-gated step.

> **Honest scope:** mokata runs **no hosted marketplace** — there is no registry service and
> nothing is phoned home. **Publish** a stack by committing it to a git repo or pushing it to the
> design vault (`mokata export`). **Discover** via a versioned, reviewable `index.json` (the
> bundled curated catalog, or any git-org/vault one you point `--source` at). **Install** is the
> same **human-gated, secret-scanned adopt** path as `team adopt` — community content is
> untrusted, so a secret in a stack is hard-blocked and declining writes nothing.

## Discover — read-only

```bash
mokata stacks list                     # the curated catalog (python-web, node-ts, go-service, …)
mokata stacks search python            # keyword search over name/framework/summary/tags
mokata stacks show node-ts             # framework, curated-guardrail count, recommended skills
mokata stacks list --source ../org-stacks   # a git-org/vault catalog (same index.json format)
```

These are read-only. No index / no source → a clear message (degrade-clean).

## Install — the gated adopt path

```bash
mokata stacks install python-web              # into a fresh repo → becomes your .mokata config
mokata stacks install python-web --force      # overwrite an existing config (never silent)
mokata stacks install acme-web --source ../org-stacks --yes   # from a git-org/vault catalog
```

Install **secret-scans** the stack manifest first (a stack must carry an env-var pointer, never a
credential), then **human-gates** applying it (`--yes` approves non-interactively; declining
writes nothing). It's an audited, reversible config write. The curated guardrails + recommended
skills land in your manifest's `settings.stack` — reviewable, and promotable to enforced typed
guardrails via `/mokata:onboard`.

## Publish your own

```bash
mokata export ./mokata-stack.json      # export your governed config as a shareable stack
# commit it to a git repo, or: mokata vault push my-stack ./mokata-stack.json
```

Others adopt it with `mokata team adopt <repo-or-file>` or, for a catalog you curate, by adding an
`index.json` entry and pointing `mokata stacks --source` at it. Nothing to host or run.

## Discoverable skills, too

`mokata skills` lists every skill; `mokata skills search <query>` filters the catalog by keyword —
the same progressive-disclosure, read-only catalog, now searchable.

## Reachable inside Claude Code

`/mokata:stacks` and the `stacks_list` / `stacks_search` / `stacks_show` (read) + `stacks_install`
(human-gated write) MCP tools mirror this — the harness is the primary surface; the CLI is the
use-anywhere secondary.
