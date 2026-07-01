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

A versioned JSON object carrying:

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
- **The HARD-GATE survives the round-trip.** Re-hydration writes exactly the recorded state — it
  never marks anything approved. A brainstorm that wasn't approved before the push restores as **not
  approved** after the pull (the Stage 50 / 54g invariant). Approval is a decision, and decisions do
  not travel inside content.
- **Degrade-clean.** No session → a friendly no-op on push; a missing or corrupt bundle → a clean
  error, never a crash.

## Where it sits

The bundle file lives at `.mokata/session-bundles/<tag>.json` — in the `.mokata/` root, *not* under
`temp_local/`, so (like the [design vault](../how-to/share-a-design-vault.md) and the memory-share
file) it travels with the repo. 55a is the **local file share**: you sync the repo or copy the file.

See the [portable-sessions how-to](../how-to/portable-sessions.md) for the commands, and
[governance & audit](governance.md) for the gate it shares with every other durable write.
