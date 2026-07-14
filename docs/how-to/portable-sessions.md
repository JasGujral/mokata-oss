# How-to: portable / shareable sessions (start here, resume there)

Your **session** is the work-in-flight: the resumable run checkpoint(s), the approved approach,
the emitted spec, and any in-progress brainstorm. It lives under **`.mokata/temp_local/`** — local
and gitignored, so by default it does **not** travel.

`mokata session` makes it portable. **push** packages the session into a **machine-path-free,
versioned, content-hashed, provenance-stamped** bundle and shares it as a **local file** under a
tag; on another machine (or for a teammate) **pull** re-hydrates it so **`mokata resume`** picks up
exactly where you left off.

The default bundle lives at **`.mokata/session-bundles/<tag>.json`** in the repo root — like the
vault, it's outside `temp_local/`, so it travels with the repo (commit/sync it, or hand the file
over). No service is required (local-first).

## Where the bundle travels — transports

The same gated bundle can ride more than one **transport** (`--to` on push, `--from` on pull). The
gates are identical on every one; only the byte store changes:

| Transport | Where | Use it for |
|---|---|---|
| `local` (default) | `.mokata/session-bundles/<tag>.json` | this machine / committing the file |
| `vault` | `.mokata/vault/sessions/<tag>.json` | **travels with the repo** — a teammate who clones/pulls the repo pulls the session |
| `postgres` | a shared, owned DB table (`mokata_session_bundle`) | a **shared team store** — everyone pushes/pulls one place |

**Postgres is opt-in & local-first.** It reads its DSN from `MOKATA_SESSION_PG_DSN` (or the shared
`MOKATA_PG_DSN`) — never inline in the committed manifest. Its table (`mokata_session_bundle`) is
provisioned once by **`mokata team init`** (mokata runs no DDL at runtime, so the role you use
day-to-day needs no `CREATE` rights). With no `psycopg`, no DSN, or no provisioned schema it
**degrades clean**: a clear message naming the failure and its fix, no crash, and it **never**
silently falls back to a less-secure store. `psycopg` is an optional extra
(`pip install "mokata[postgres]"`); the core stays dependency-free.

**Project-scoped on a shared DSN (Stage 71a).** When several projects share one Postgres DSN, the
`postgres` transport is **scoped by project key**, so a tag like `auth` never collides across
projects and `session list` shows only the current project. Two clones of the **same** repo resolve
to the same key (via the git remote, or a pinned `settings.project.id`), so cross-machine resume
still works; `--all` / `--project` widen the listing. See
[Multi-project on one shared backend](multi-project-shared-backend.md).

## Save the session (ungated — it's local)

```bash
mokata session save     # snapshot this session's in-flight state; `mokata resume` continues it
```

`session save` snapshots the in-flight session — brainstorm progress, the approved approach, the run
checkpoints — so it's **recoverable**. It is **not** human-gated, deliberately: it writes **local,
transient state that is already yours**. The gate belongs at the **share** boundary (`push`), where
something actually leaves your machine — gating a local snapshot would only train you to click
through prompts that guard nothing.

Nothing in flight yet → a friendly no-op (start a brainstorm/run first).

## Push the current session (human-gated)

```bash
mokata session push auth-refactor
# choose a transport:        --to vault       (travels with the repo)
#                            --to postgres    (the shared team store; needs a DSN)
# optionally: --run <id> (scope to one run; default: every recorded run)  --author alice
```

Pushing is a **durable write**, so it goes through the universal gate: a **secret** anywhere in the
session is a hard block (approval can't override it), then your explicit approval, then the write —
recorded in the audit ledger. The bundle is **machine-path-free** (absolute paths are stripped so it
travels), carries **provenance** (author, source, created) + a **content hash**, and a **repo
fingerprint** used to catch a cross-codebase pull.

No session in progress → a friendly no-op (nothing to package).

### The three share flags

| Flag | What it does |
|---|---|
| `--save-first` | **snapshot, then bundle** — one atomic action, so there is no gap between the session you're looking at and the one you share |
| `--allow-in-progress` | consent to share **unfinished thinking** — a brainstorm with no approved approach |
| `--requirements-only` | bundle **only the distilled requirements** (the original ask + goal + constraints + requirement lines) as a **cross-repo handoff** — no approaches, no approval, no transcript |

Sharing unfinished thinking is a decision, so mokata makes you make it. Push an unapproved
brainstorm with no flag and it **refuses**:

```text
session: refusing to share an in-progress session (in-progress: 6 answered turns, no approved
approach) — re-run with --allow-in-progress to share unfinished thinking (or --requirements-only
to share just the distilled requirements).
```

A completed session (an approved approach, or a spec/checkpoint) needs no flag at all.

`--requirements-only` is the other way to say yes — and it's the one for handing work to a
**different codebase**: because it carries only the requirements, the cross-codebase fingerprint
check below is **replaced by an origin label** the receiver sees at the gate ("captured in
`<repo>` — cross-repo by design").

```bash
mokata session push auth-refactor --save-first                       # snapshot + share, atomically
mokata session push spike --allow-in-progress                        # share the thinking so far
mokata session push auth-reqs --requirements-only --to vault         # hand the requirements over
```

### Never a silent clobber

Re-pushing the same tag is safe and explicit:

- **identical session** → a no-op (reported, nothing written);
- **changed session** → **refused** unless you pass `--force` (which overwrites the bundle).

## List what's shared (read-only)

```bash
mokata session list      # spans local + the committed vault (+ shared Postgres when a DSN is set)
                         # each row: tag @transport · resume point · author · date
```

## Pull and resume on the other side (human-gated)

Sync the repo (or copy the bundle file) to the other machine, then:

```bash
mokata session pull auth-refactor                         # re-hydrate into this repo (local)
mokata session pull auth-refactor --from vault            # pull from the committed vault store
mokata session pull auth-refactor --from postgres         # pull from the shared team store
mokata session pull auth-refactor --into /path/to/clone   # or target another repo
mokata resume                                             # continues from the bundle's resume point
```

Because the bundle is **untrusted on pull**, this path is **human-gated and secret-scanned again**,
and:

- the **content hash is verified** — a corrupted bundle is caught, not served;
- a **cross-codebase mismatch** (the bundle came from a different repo than the target) is
  **surfaced** and *not* applied unless you pass `--force`:

    ```text
    'auth-refactor' was captured on a DIFFERENT codebase (repo fingerprints differ) — re-pull with
    --force to apply it here anyway, or pull into the matching repo. Nothing was hydrated.
    ```

- the **approach approval does not cross machines** (below).

These guarantees hold on **every transport** — including a pull from the shared Postgres store.

### What crosses, and what doesn't

The **approval of an approach is yours to give, on your machine**. So on pull the approval is
**stripped**: the approved-approach record is dropped, the brainstorm's approved flag is cleared,
and a line is appended to the record —

```text
imported: approval not transferred — re-approve on this machine (HARD-GATE)
```

— so an approach approved before the push arrives **unapproved**, and re-earns approval here.

Be precise about the scope of that claim:

| | Crosses? |
|---|---|
| the **approved approach** | **no** — stripped on pull; re-approve here |
| the **emitted spec** | **yes**, intact — it is *not* de-approved |
| the brainstorm turns / run checkpoints / provenance | yes — that's the point of the bundle |
| **write proposals**, **gate overrides**, **TDD red/green state** | **never bundled at all** |

### Bundle versions

The bundle schema is **v2**. A **v1 bundle still pulls fine** (back-compat — an old bundle is not
orphaned). A bundle **newer** than the mokata reading it is **refused**, never silently downgraded:

```text
session bundle is schema v3 but this mokata reads up to v2 — upgrade to pull it (refusing to
silently drop its newer sections)
```

## Rename a session (a name you chose, not a hash)

Refer to work by a human-friendly name. Renaming is **human-gated** where it writes durable:

```bash
mokata session name explore auth-refactor       # rename the tag
# --force ONLY to overwrite a colliding name (never a silent clobber); --to picks the transport
```

It's **idempotent** (renaming to the current name is a no-op), a name **collision is refused**
unless `--force`, and **provenance is preserved** (the original author/source/created stay, plus a
`prior_names` trail) — the content-hash is untouched, so the session itself is unchanged. The name
is what `push` / `pull` / `resume` and the status badge read.

## In Claude Code

The same flow is one step inside the plugin:

```text
/mokata:session push auth-refactor --to vault
/mokata:session list
/mokata:session pull auth-refactor --from vault
/mokata:session name explore auth-refactor
/mokata:resume
```

The MCP tools mirror the CLI: `session_list` is read-only (and spans transports), and
`session_push` / `session_pull` / `session_name` are **propose-only** — they hand back a
`proposal_id` and write nothing until *you* approve it out-of-band with `mokata approve <id>`
(consistent with the vault and memory write tools; `approve=true` on the tool call commits
nothing). Each carries a `transport` argument
(`local` | `vault` | `postgres`); an unreachable remote returns a clean `unavailable` status.

See also [share a design vault](share-a-design-vault.md) and
[the pipeline & gates](../concepts/pipeline.md).
