# How-to: use & heal memory

Memory is on by default on `standard`/`full`. It is **human-gated** and **self-healing by
surfacing** — it never silently rewrites.

## Inspect (read-only)

```bash
mokata memory     # active items, read/write ratio, pending proposals + the health nudge
```

When the store needs attention `mokata memory` (and the `mokata govern` view) print a
one-line **health nudge** — `N stale · M contradictory · K unused — review with mokata memory
/ mokata govern` — pointing at the gated review path. It is read-only and **proposal-only**:
it never edits or prunes memory, and it's silent when the store is healthy.

## Back it up & restore it — `memory export` / `memory import`

Memory is a durable asset, so it has a **backup** surface. `export` writes a committable,
human-readable JSON file **you own**; `import` restores one through the gate.

```bash
mokata memory export                       # → .mokata/backups/memory-<UTC>.json
mokata memory export ./team-brain.json     # or name the destination
mokata memory import ./team-brain.json     # human-gated restore (--yes to skip the prompt)
```

The default destination is `.mokata/backups/memory-<UTC>.json` — **not** under `temp_local/`, so
it's committable, and it is UTC-stamped to the microsecond so successive backups **never clobber**
each other. Export is read-only on the source and carries provenance with each item.

`import` is a **restore**, not a merge-and-hope: it previews (counts + a keys-only sample), dedups,
surfaces each new item for approval, and routes a **conflict** (same subject, different value)
through the self-healing old→new surface — never a silent overwrite. Every restored item lands
through the one WriteGate, secret-scanned and stamped with import provenance, so a round trip is
content-identical.

!!! note "Backup ≠ sharing"
    This is a **backup** surface. Cross-repo/team sharing is the team Postgres store (see
    [team setup](team-setup.md)). The old `memory-share.json` **channel** was removed in 0.0.18:
    the filename is now just a path, and your existing file is untouched. It *is* a mokata memory
    backup — restore it with `mokata memory import --file .mokata/memory-share.json`.

## How recall actually ranks — the retrieval tiers

A recall fuses up to three tiers, and mokata tells you **which engines are really ranking your
results** rather than letting two installs both say "memory: ok":

| Tier | What runs | Notes |
|---|---|---|
| **lexical** (always on) | `fts5` (SQLite FTS5 + bm25) or `tsvector` (Postgres tsvector + ts_rank) — ranked **in the database** | degrades honestly to `jaccard`, a Python keyword-overlap floor, when FTS5 is absent. ⚠ **On a large store `fts5` currently ranks *worse* than that floor** — see the note below |
| **graph-proximity** (optional) | a code-graph-keyed boost | off unless a graph is wired |
| **semantic** (opt-in) | embedding cosine over the vector index | `off` by default; `hashing` is the zero-dep floor and is **honestly labelled "token-hash overlap, NOT meaning"** |

`mokata doctor` prints the live retrieval-stack line, so you never have to guess. It is
**informational** — `hashing` + `jaccard` is a legitimate, working zero-dependency install, not a
failure, and it never affects doctor's exit code.

> 🔴 **Known limitation — the SQLite FTS5/BM25 tier currently ranks *worse* than the Jaccard
> floor it replaced on a large store, and it still does in this release.** `normalize_lexical_scores`
> scales each engine's scores against the best score *in its own result set*, which flattens exactly
> the gap that would have ranked a mid-pack answer. Measured on a 100,000-item benchmark against the
> Jaccard floor — same probes, same code, only the corpus size differs — the FTS tier is
> **−5.6pp recall (0.5000 → 0.4444)** and **−10.8pp MRR@10 (0.8334 → 0.7258)**. At 5,000 items the
> same comparison loses **no** recall and only −3.3pp MRR, so a small store hides more than half of
> it. First disclosed in the **0.0.16** notes and still unfixed: 0.0.17 and 0.0.18 each shipped no
> ranking work, and neither does the release you are reading. The repair is rank-preserving
> normalization rather than a constant to tune. **If you run a large
> store and your lexical results look mis-ordered, this is why.**


### Turning on real semantics (consented, not default-on)

The embeddings tier is **opt-in**. mokata **asks before installing anything** — an extra is a real
install that runs `pip` and may touch the network, so it goes through the same consent discipline
as any durable change:

```bash
uv tool install "mokata[full]"        # or: pipx install "mokata[full]" · pip install "mokata[embeddings]"
mokata setup claude --mode full       # detects it, switches the tier on, warms the model
```

`--mode memory` and `--mode full` **never install**: they detect the extra, set
`memory.embedder = auto`, and warm the model (a ~30 MB one-time download from huggingface.co).
If the package is missing, the outcome table names the ONE install line for the installer that
owns mokata's environment; if the download is blocked, it names why (offline, proxy, TLS,
timeout). `--mode seatbelt` structurally never touches it. The interactive onboarding wizard
still offers the extra once — fail-closed off a TTY, a decline remembered.

Changed embedder? Vectors must never silently mix, so the runtime **refuses** a mismatched index
(the semantic tier goes off and recall falls to lexical). The way out is the gated migration:

```bash
mokata memory reembed        # previewed (the item count + old→new embedder), then human-gated
```

## Explainable recall — "why did this surface?"

A by-relevance recall names *why* each hit surfaced (matched token / graph anchor / semantic
neighbour / kind):

```python
from mokata.memory import explain_recall
hits = store.recall_relevant("auth token rotation")   # or jit_recall(store, query)
for e in explain_recall("auth token rotation", hits):
    print(e.line())     # - auth.policy: rotate tokens daily  ↳ [context] matched "auth"
```

Inside Claude Code, `recall(query="…")` returns each hit with its `why`. The explanation is
deterministic and read-only; one short phrase per hit, so the top-k frugality bound holds.

## Record facts/decisions (gated)

Programmatically, every write goes through the gate:

```python
from mokata.config import Surface
from mokata.memory import MemoryStore, MemoryItem, DECISION

store = MemoryStore.from_surface(Surface.load("."))
store.remember(MemoryItem.create("db.engine", "postgres"), assume_yes=True)
store.remember_decision("api.style", "REST", assume_yes=True)
```

### Citations must resolve

If a fact cites code, mokata checks the citation before anything is stored. These are refused,
and the message names the citation:

```python
store.remember(MemoryItem.create("billing", "refunds live in src/payments/refund.py:12"),
               assume_yes=True)
# refused: ungrounded memory write — 1 citation(s) do not resolve:
#   'src/payments/refund.py:12' in value (no such file in the repo). Nothing was written. …
```

Paths and line anchors must exist in the repo, commits must resolve, links must be well-formed
(they are not fetched), and symbols are checked through the code graph when one is adopted. Wrap
anything that must not be stored in `<private>…</private>`.

If the new fact overlaps an existing one on four or five of kind, subject, value, citations and
scope, the write updates that entry instead of adding a near-duplicate. The approval prompt tells
you which.

### Retire a learning when its code changes

Give a fact one or more `retire_when` predicates:

```python
store.remember(MemoryItem.create(
    "billing", "refunds go through refund() in src/billing.py",
    retire_when=["deleted:src/billing.py", "date:2027-01-01"]), assume_yes=True)
```

Over MCP, pass `retire_when=["deleted:src/billing.py"]` to `remember`. The four predicates are
`deleted:<path>`, `removed:<symbol>`, `after:<commit>` and `date:<YYYY-MM-DD>`.

When one fires, the fact shows up in `mokata memory` and in `detect_issues()` as `retire_due`.
Choose a refresh verb:

```bash
mokata memory refresh                                    # list what fired
mokata memory refresh billing --verb keep                # still true — drop the fired predicate
mokata memory refresh billing --verb update --value "…"  # rewrite in place
mokata memory refresh billing --verb consolidate --into <id> --value "…"
mokata memory refresh billing --verb replace --value "…"
mokata memory refresh billing --verb delete
```

Every verb asks for approval (or takes `--yes`). Over MCP, use `apply_proposal` with the verb as
the `decision`; it is propose-only until you run `mokata approve <id>`.

## Self-healing (C5) — surface, then approve/edit/reject

```python
for p in store.detect_issues():          # read-only: detects, writes nothing
    print(store.render_proposal(p))      # old → new diff
    store.apply_proposal(p, "approve", assume_yes=True)   # or "edit" / "reject"
```

`detect_issues()` finds contradictions and stale facts; nothing changes until you apply,
and the default is no change.

## Intentions — "remind me when X"

An **intention** is a memory of something you mean to do later. Unlike a fact, it has a
**due condition**:

- a date, which is due on or after that day;
- a keyword, which is due when the word appears in the branch name or in a path you've changed;
- a path, which is due when a matching file changes.

Over MCP you record one with `remember`, using `kind="intention"` and `due_at="2026-11-01"`,
`when_keyword="auth"` or `when_path="src/auth"`. The write is human-gated like any other, and the
due condition is part of what you approve.

There is no background process. mokata checks intentions in two places:

- **When a session starts.** The briefing shows *"N intention(s) due"*.
- **During `detect_issues()`.** Each due intention becomes an `intention_due` proposal.
  - **approve** marks it done. The intention is archived, not deleted.
  - **defer** leaves it, and it shows up again next time.

If an intention's due condition can't be read, the briefing reports it as **UNREADABLE**, and
mokata leaves it untouched.

## Consolidation (C7) — proposal-only

```python
for p in store.propose_consolidations():   # merge dupes / summarize / prune
    store.apply_consolidation(p, "approve", assume_yes=True)
```

It **never auto-applies**; both proposals and decisions are logged to the audit ledger.

## Episodic search (C3)

```python
from mokata.memory import EpisodicMemory
epi = EpisodicMemory(store)
epi.record("session-1", "we chose postgres as the database engine", assume_yes=True)
epi.search("which database did we choose")   # embeddings optional; lexical fallback
```

## Toggle a type off

Set `settings.memory.episodic: false` (etc.) in the manifest — disabling a type refuses
its writes and never surfaces it on read. See [memory concepts](../concepts/memory.md).

## Change where memory is stored

Point the backend at a custom SQLite path or a hosted Postgres
database — see [configure storage backends & paths](configure-storage-backends.md).
