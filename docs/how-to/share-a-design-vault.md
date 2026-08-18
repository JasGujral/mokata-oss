# How-to: share a design vault (brainstorm & spec)

Shared **memory** carries the distilled *decisions*. The **vault** carries the *artifacts* —
the **brainstorm-plan** (the *why*: the approach, its rationale, the options weighed) and the
**spec** (the *what*: ACs ↔ tests) — as human-readable markdown your team can find, pull, and
review.

The flow is **push → search → pull → review**: one person brainstorms a plan and pushes it
under a name; a teammate searches, pulls, and reviews it.

The vault lives at **`.mokata/vault/`** in the repo root — a **committed/synced** artifact
store that travels with the repo, **not** under `temp_local/`. No service is required
(local-first). Each entry is `<name>.md` plus a record in `index.json` carrying provenance
(author, source, kind, timestamps) and a content hash.

!!! info "The design vault is supported — it was the vault *transport* that was removed"
    Everything on this page works and is staying. A `vault` **channel** was deprecated and removed
    in **0.0.18**, and it was a different thing wearing the same word: the vault
    **session-transport kind**, which stored portable session bundles under
    `.mokata/vault/sessions/`. The design artifacts this page is about — the brainstorm/spec
    markdown at `.mokata/vault/<name>.md` — were never that channel and were never deprecated.

    If you pushed **session bundles** to the vault transport, see
    [portable sessions](portable-sessions.md) for where they are and how to reach them. Your
    design artifacts need nothing: `mokata vault list / search / pull / push` all work as
    documented below.

## Push a brainstorm or spec (human-gated)

```bash
mokata vault push payments-redesign ./notes/payments-plan.md
# optionally: --kind brainstorm|spec   --author alice
```

Pushing is a **durable write**, so it goes through the universal gate: a **secret** in the
artifact is a hard block (approval can't override it), then your explicit approval, then the
write — recorded in the audit ledger. The artifact kind is inferred from the name/file
(`spec` if it looks like a spec, else `brainstorm`) unless you pass `--kind`.

### Never a silent clobber

Re-pushing the same name is safe and explicit:

- **identical content** → a no-op (reported, nothing written);
- **changed content** → **refused** unless you pass `--force`;
- **`--force`** → the entry is **versioned** (v1 → v2), keeping the prior version's metadata
  (hash, author, timestamp) in its history, so a change is always auditable.

```bash
mokata vault push payments-redesign ./notes/payments-plan-v2.md --force
```

## Browse and find (read-only)

```bash
mokata vault list                          # name · kind · version · author · date
mokata vault search "idempotent ledger"    # ranks name + title + body matches (quote multi-word)
```

## Pull and review (read-only)

```bash
mokata vault pull payments-redesign                      # → ./payments-redesign.md
mokata vault pull payments-redesign --dest ./review.md   # or name the destination
```

`pull` writes the **exact** content to a file for review and verifies the stored content hash,
so a corrupted artifact is caught rather than served. Provenance is preserved across a sync,
so a teammate sees who pushed it and when.

## In Claude Code

The same flow is one step from the slash commands `mokata setup claude` wires:

```text
/vault push payments-redesign ./notes/payments-plan.md
/vault search "payments redesign"
/vault pull payments-redesign
/vault list
```

The MCP tools mirror the CLI: `vault_list` / `vault_search` / `vault_pull` are read-only, and
`vault_push` is **propose-only** — it returns a `proposal_id` and writes nothing until *you* approve
it out-of-band with `mokata approve <id>` — bare `mokata approve` lists what's waiting (consistent
with `memory_export` / `memory_import`; `approve=true` on the tool call commits nothing).

See also [use & heal memory](use-memory.md) and [share a stack](share-a-stack.md).
