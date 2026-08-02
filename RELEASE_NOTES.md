mokata **0.0.16 — "Memory intelligence at scale."** Upgrade with `mokata upgrade` (or
`pip install -U mokata`, then `mokata upgrade`). Additive; **no breaking changes**; local stays the
zero-config default; **no schema change**. Requires **Python ≥ 3.10**.

Memory stops being a feature that works on a demo store and becomes one that works on yours. The
whole retrieval path was re-measured at **100,000 items** rather than at a size that flatters it —
and the numbers moved by orders of magnitude, not percentages. Around that, memory learns to age,
teammates stop losing each other's facts, upgrades finish the job they used to leave half-done, and
one measured regression is disclosed here rather than left for you to find.

**Retrieval, measured at 100,000 items.**

- **Recall no longer reads your whole store to answer one question.** Retrieval used to
  materialize *every* active visible item and decode each one, on every recall, then rank what it
  had already paid for. On a 100,000-item store that was **51,606 rows and 4,533 ms**. Each tier
  now nominates its own ranked shortlist *in the database*, the capped union is hydrated, and —
  the part with teeth — **your scope predicate travels with that query** instead of being applied
  afterwards. Same store, after: **26 rows and 20.1 ms** — 225× on latency, ~2,000× on rows — and
  the read no longer grows with the store.
- **It also fixes a real correctness bug.** The ranked query's limit used to be taken over the
  *whole* store and only then intersected with what you were allowed to see, so a reader whose
  matching items all ranked below the cut got **nothing back while their own rows sat unread
  underneath**.
- **Per-turn memory injection cost three-quarters of a second per prompt.** The recall that rides
  `UserPromptSubmit` was still doing the whole-store read, one layer up where the fix had not
  reached. Measured on a 100,000-item store over 20 consecutive turns: **787.0 ms → 36.8 ms per
  turn (21×)**. On a large store this was a latency you paid on *every single prompt*.
- **A signal that matches nothing can no longer outrank one that does.** The semantic tier's
  weight is now derived from the embedder's own measured noise instead of one fixed number for
  every embedder — a quiet embedder keeps its full weight, a noisy one is held to what it can be
  trusted with, and an embedder that cannot be characterized fails closed and says so. The two
  recall-history terms are bounded as a *sum* rather than individually, so frequent recall can no
  longer tie a perfect match and win the tiebreak. **If you have not opted into an embedder (the
  default) and your store has no recall history yet, your ranking is unchanged** — arithmetically,
  term for term.

### Known limitations

- **The SQLite FTS5/BM25 lexical tier ranks *worse* than the keyword floor it replaced, and at
  scale it costs recall — measured, not suspected.** `normalize_lexical_scores` scales each
  engine's scores against the best score *in its own result set*, which flattens exactly the gap
  that would have ranked a mid-pack answer. On the 100,000-item benchmark, against the Jaccard
  keyword floor on the same probes and the same code — only the corpus size differs — the FTS tier
  measures **−5.6pp recall (0.5000 → 0.4444)** and **−10.8pp MRR@10 (0.8334 → 0.7258)**. At 5,000
  items the same comparison loses no recall at all and only −3.3pp MRR, so a small corpus hides
  more than half of it: normalizing against the result set's own max only begins dropping real
  answers once there are enough genuine competitors. **This ships as-is and the fix is scheduled
  for 0.0.17** alongside the BM25/H-4 ranking work, because the correct repair is rank-preserving
  normalization rather than a constant to tune, and it wants measuring in one pass with the rest
  of the ranking. It is recorded here rather than left to be discovered: if you run a large store
  and your lexical results look mis-ordered, this is why.

**Memory that ages, and teammates who stop overwriting each other.**

- **Memory ages.** Items carry usage signals (`hit_count`, `last_recalled_at`) and bi-temporal
  validity windows, and ranking gains recency and usage terms that break ties between things that
  already match. Over-budget scopes get **proposed** archival sweeps — one reviewable decision per
  bucket. Archiving closes a validity window and never deletes a row: your memory is re-openable
  and provenance survives. An item with no hits scores exactly as it did before.
- **Two teammates writing the same memory produce a proposal, not a lost fact.** A flush-time
  conflict becomes a healing proposal resolved through the gate you already use. Entries sharing
  an approval apply in a single transaction, so a conflict rolls the whole group back — a partial
  heal can no longer retire a fact and lose its replacement. Retiring a fact whose replacement is
  still undecided, discarded or blocked is refused outright.
- **Memory summaries are written, not templated.** Consolidating a turn cluster now produces a
  real drafted summary, drafted by the harness agent you are already talking to through a seam —
  no model is embedded in mokata and no API key is involved. The draft is a **proposal** on the
  same secret-scan → human-gate → ledger path. With no drafter present the output is
  byte-identical to before, and a drafter that fails falls back to the placeholder *loudly*,
  because a placeholder shown at an approval prompt looks exactly like a real summary.
- **A semantic store no longer switches on token-hash embeddings you did not ask for.** Selecting
  `pgvector` used to resolve the embedder to `auto`, whose floor is the built-in token-hash
  embedder — so a team that opted into a semantic store without naming one silently filled a real
  vector index with token-hash vectors. Name an embedder and you get it; name none and the
  semantic tier stays **off**, with a notice saying why and how to turn it on.

**Upgrades that finish, and wiring you can see.**

- **`mokata upgrade` finishes the job.** `pip install -U mokata` replaces the code and leaves
  `.claude/settings.json` carrying the wiring the *previous* version wrote — so a hook added since
  your last `mokata setup claude` was silently absent, with no error and no warning, just a gate
  that never fires. `mokata upgrade` refreshes the harness wiring through setup's own preview-diff
  gate and then runs the wiring check. Human-gated end to end: decline either gate and nothing is
  written.
- **Stale wiring is visible where you already are** — a SessionStart briefing line (when the hooks
  still run), the `status` MCP tool (when they do not), and a named `hooks-wiring-stale` doctor
  finding. A current install sees nothing on any of them.
- **`mokata doctor --wiring`** — the wiring-only check, non-zero if your gates are not wired,
  launchable and current. It works on an uninitialized repo, so it is runnable the moment a
  `pip install` finishes.

**Worktrees, navigation and spec.**

- **Pipelines can run in their own worktree** — always offered, never automatic — with the
  run↔worktree binding kept visible and a merge-ready branch at ship. **`mokata worktree list`**
  (and a `worktree_list` MCP tool) joins your worktrees against their sessions with a staleness
  verdict per row.
- **Code navigation goes through the graph** rather than ad-hoc search, degrading cleanly when no
  graph is available. **`spec_show`** fetches the current spec so phase prompts stop re-searching
  the repo for something mokata already knows, and re-emitting a spec **archives and versions** the
  prior one instead of clobbering it.
- **⚠ NEW FAILURE MODE — `spec emit` can now refuse on a stale code anchor** (`code-anchor-ref`).
  If the decisions your approach was approved against name code that has since changed, emitting
  is blocked with the anchors named and the road out. **This is a deliberate contract change.**
  Nothing is written when it fires, and it is conservative: with no recorded baseline, mokata says
  nothing rather than blocking you on a guess.

**Security and platform.**

- **Windows is now actually first-class, as the docs already claimed.** The self-protect gate
  enforced *neither way* on Windows: the tokenizer treated `\` as a shell escape rather than a
  path separator, so a write into an installed mokata was **never judged at all** while ordinary
  in-repo writes were **over-blocked**. Both directions came from one root cause and are fixed at
  the root; POSIX tokenization is byte-identical. The Windows CI matrix now *executes* these paths
  instead of asserting about them as strings.
- **Hooks resolve without a shell that completes filenames for you.** Plugin hooks leaned on
  cmd.exe's `PATHEXT` completion — a *shell* behaviour. Under PowerShell the launcher did not
  resolve and **`secret-guard` and `gate-guard` simply did not run**, with no error to see. Setup
  now uses the exec form, the plugin route names its shell, and an unresolvable wired hook is a
  loud failure instead of a quiet one.
- **Writes to mokata's own installed code are blocked, non-overridably** — site-packages, a
  mokata install, or outside the workspace root, including the Bash side-door. No override flag,
  no environment switch.
- **The secret scanner stops flagging your variable names**, and **`mokata secret ignore`** lets
  you record an entropy-layer ignore keyed by content hash and version controlled. Signature-detected
  credentials are refused by name and can never be ignored.

**Fixes.** Re-entering a pipeline no longer wedges the approval loop; a re-entered session can see
its own pipeline's state across a `/clear`; review verdicts survive a new session and a failed
review record is loud instead of silent; there is one source of review truth across CLI and MCP;
and the Homebrew formula vendors **29** resources, not 28 — the 0.0.15 note miscounted against its
own lockfile.

Full detail, including everything trimmed from these notes, is in
[`CHANGELOG.md`](https://github.com/JasGujral/mokata-oss/blob/main/CHANGELOG.md).

Local-first, no telemetry, Apache-2.0.
