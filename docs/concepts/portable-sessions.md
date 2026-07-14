# Portable sessions (the bundle)

mokata's session state — the resumable run checkpoint(s), the approved approach, the emitted spec,
and any in-progress brainstorm — normally lives under `.mokata/temp_local/`, local and gitignored.
A **session bundle** is the portable form of that state: a single, self-contained file you can carry
to another machine or hand to a teammate, from which `mokata resume` continues the work.

It is **composed from existing primitives**, not a new state store: the run checkpoints
(`pipeline_run__<id>`), the brainstorm progress, the approved approach, and the emitted spec are all
read back through the same `StateStore` that the pipeline writes to. The bundle just collects,
packages, ships, and re-hydrates them.

## What a bundle is

A versioned JSON object — the bundle schema is at **v2**, and a **v1 bundle still pulls fine**
(back-compat is kept; a bundle *newer* than the reader is **refused**, never silently downgraded)
— carrying:

- **`state`** — the collected session keys, **machine-path-free** (absolute paths are stripped to
  basenames, so nothing machine-specific travels);
- **`repo_fingerprint`** — a deterministic, content-free signature of the *codebase* (its top-level
  layout), used to detect a cross-codebase pull;
- **`content_hash`** — a SHA-256 over the substantive payload (schema, kind, fingerprint, run id,
  state) — *not* the provenance, so a re-push of the same session at a later time stays idempotent;
- **`provenance`** — author, source (a repo label, never a machine path), and created timestamp;
- **`resume`** — a small descriptor (run id, resume phase, done/total) so `list` reads well.

It is **deterministic**: the same `(session, tag, author, timestamp)` always produces the same
bytes.

## Saving vs sharing — where the gate sits

**`mokata session save` is ungated and purely local.** It snapshots the in-flight session (the
brainstorm's progress, the approved approach, the run checkpoints) into your own `.mokata/` so
`mokata resume` can continue it — nothing leaves the machine, so there is nothing to gate. The
human gate sits at the **share** boundary: `push` and `pull`, where state crosses to (or arrives
from) somewhere else.

That boundary is also where mokata asks for **consent to share unfinished thinking**. A push of an
in-progress session — a brainstorm with no approved approach — **refuses** unless you say so:

| Flag | What it does |
|---|---|
| `--save-first` | snapshot the session, *then* bundle it — one atomic action, with no gap between what you see and what you share |
| `--allow-in-progress` | consent to share an unfinished session (a brainstorm with no approved approach) |
| `--requirements-only` | bundle **only the distilled requirements** (the anchor, goal, constraints, and requirement lines) as a **cross-repo handoff** — no approaches, no approval, no transcript; the repo-fingerprint check is replaced by an origin label |

`--save-first` is pure convenience. The other two are the two *alternative* consents: share the
unfinished thinking, or share only what it distilled to.

## The invariants (why it's safe to share)

Sharing session state means moving *untrusted, mutable* content between repos, so the bundle is held
to the same inviolables as every other mokata write — on **both** ends of the trip:

- **Human-gated on push *and* pull** (P2). Neither end writes silently; a declined gate writes /
  hydrates nothing.
- **Secret-scanned on push *and* pull.** The bundle is untrusted on pull, so it is re-scanned there;
  a secret anywhere in the session is a **hard block approval cannot override**.
- **Content-hash verified on pull.** A corrupted bundle is caught, not served.
- **Cross-codebase mismatch surfaced, never silently applied.** If the bundle's repo fingerprint
  differs from the target repo's, the pull *stops and surfaces it*; applying anyway is an explicit
  `--force` override.
- **The approach approval never crosses machines.** On pull, the `approved_approach` handoff is
  **stripped** and the brainstorm's approved flag is **cleared** — even one that *was* approved on
  the source machine hydrates as **not approved**, with `imported: approval not transferred —
  re-approve on this machine (HARD-GATE)` appended to the record. Approving an approach is *your*
  decision, and a decision does not travel inside content: the HARD-GATE re-runs here.

    Precisely, and no further: this is true of the **approach approval**. An **emitted spec crosses
    intact** — it is content the completeness gate already proved, not a pending decision — and
    write proposals, gate overrides, and TDD red/green state are **never bundled at all**.
- **Degrade-clean.** No session → a friendly no-op on push; a missing or corrupt bundle → a clean
  error, never a crash.

## Where it sits

The bundle file lives at `.mokata/session-bundles/<tag>.json` — in the `.mokata/` root, *not* under
`temp_local/`, so (like the [design vault](../how-to/share-a-design-vault.md) and the memory-share
file) it travels with the repo. 55a is the **local file share**: you sync the repo or copy the file.

See the [portable-sessions how-to](../how-to/portable-sessions.md) for the commands, and
[governance & audit](governance.md) for the gate it shares with every other durable write.
