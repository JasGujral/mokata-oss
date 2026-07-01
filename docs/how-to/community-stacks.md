# Community stacks — share & install a governed stack

A **stack** is a publishable, governed starting point for a framework: a schema-valid mokata
**config** + a curated **rule/guardrail set** + the relevant **skills**, all in one manifest.
Instead of wiring mokata from scratch, adopt a ready-made stack for your framework — and publish
your own for others.

> **Honest scope — no hosted marketplace.** mokata runs **no registry service** and phones
> **nothing** home. "Marketplace" here is three existing primitives composed:
>
> * **Publish** — share a stack over **git** (commit `mokata-stack.json`) or the **design vault**.
> * **Discover** — a versioned, reviewable **`index.json`** lists stacks (the bundled curated
>   catalog, or any git-org/vault one you point `--source` at).
> * **Install** — the same **human-gated, secret-scanned adopt** path as `team adopt`.
>
> Community content is **untrusted**: install secret-scans the stack first (a secret is
> hard-blocked) and human-gates the write (declining writes nothing). It reuses
> `export`/`apply_manifest`/the vault/`team` — nothing new to host or run.

## Discover a stack (read-only)

```bash
mokata stacks list                 # the curated catalog (python-web, node-ts, go-service, …)
mokata stacks search python        # keyword search over name / framework / summary / tags
mokata stacks show node-ts         # framework, curated-guardrail count, recommended skills, tags
```

The bundled curated index ships with mokata, so this works from any `pip`/`pipx` install. Point
`--source` at a git-org or vault catalog (any dir/`index.json` in the same format) to browse a
team's or community's stacks instead:

```bash
git clone https://github.com/acme/mokata-stacks ../acme-stacks
mokata stacks list --source ../acme-stacks
```

No index / no source → a clear message (degrade-clean), never a crash.

## Install a stack (the gated adopt path)

```bash
mokata stacks install python-web            # into a fresh repo → becomes your .mokata config
mokata stacks install python-web --force    # overwrite an existing config (never silent)
mokata stacks install acme-web --source ../acme-stacks --yes   # from a git-org/vault catalog
```

Install:

1. **secret-scans** the stack manifest (a stack must carry an env-var *pointer*, never a
   credential — a secret is hard-blocked, exactly like a memory import or a vault pull);
2. **human-gates** applying it (`--yes` approves non-interactively; declining writes nothing);
3. records the curated **guardrails** + recommended **skills** in your manifest's
   `settings.stack` — reviewable config you can promote to enforced, typed guardrails via
   [`/mokata:onboard`](capture-project-rules-and-context.md).

It's an audited, reversible config write (re-import a prior stack to undo).

## Publish your own stack

Export your governed config, then share it however your team already shares files:

```bash
mokata export ./mokata-stack.json          # your config as a shareable governed stack
git add mokata-stack.json && git commit -m "share our governed stack"
# …or push it to the design vault:
mokata vault push our-stack ./mokata-stack.json
```

Teammates adopt it directly with [`mokata team adopt <repo-or-file>`](team-setup.md). To curate a
**catalog** others can browse with `mokata stacks --source`, add an `index.json` next to your
stack manifests using the same format as the bundled one (see
`src/mokata/stacks/index.json` in the repo): each entry names the stack, its framework, a summary,
tags, and the `manifest` filename. Point people at your repo/vault dir — nothing to host.

## A discoverable skill catalog, too

The same progressive-disclosure catalog behind `mokata skills` is now searchable:

```bash
mokata skills                  # the full catalog (name + one-line summary)
mokata skills search test      # filter by keyword
mokata skills develop          # reveal one skill's gate + phase + prompt
```

## Inside Claude Code

`/mokata:stacks` mirrors all of this, and the `stacks_list` / `stacks_search` / `stacks_show`
(read) + `stacks_install` (human-gated write) MCP tools make the catalog reachable without
leaving the harness. See [Command surfaces](../reference/command-surfaces.md) and
[Install mokata](install-mokata.md).
