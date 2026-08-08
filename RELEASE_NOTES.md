mokata **0.0.17 — "Trustworthy evidence."** Upgrade with `mokata upgrade` (or
`pip install -U mokata`, then `mokata upgrade`). Additive; **no breaking changes**; local stays the
zero-config default; **no schema change**. Requires **Python ≥ 3.10**.

A green check is supposed to mean something. Most of this release went into the instruments that
decide whether mokata's own claims are true — and four of the things those instruments found were
being felt by users every day. Those four are the reason to upgrade. The instruments are why the
four are fixed rather than merely reported.

**The four you have been feeling.**

- **`UserPromptSubmit hook timed out after 30s` is gone.** The hook that injects relevant memory
  into your prompt had **no clock on its own work** — reading its input was bounded, everything
  after that was not — so a slow store or a pathological turn hung it until the harness killed it,
  and you saw a timeout on a prompt you had already sent. Its work now runs under its own budget
  and, over budget, emits **nothing** rather than half an injection. **The budget is measured, not
  chosen:** end-to-end in a cold subprocess the whole hook is ~90–100 ms, of which ~85 ms is
  interpreter start and imports, and ranked recall itself runs 0.5 / 0.5 / 1.1 ms at 0 / 50 / 500
  items. The guess going in was that recall was spending the 30 seconds. It was not — and measuring
  before fixing is what moved the answer. (OSS #43.)
- **Run ids stop drifting, and an unresolvable run says so instead of picking one.** With several
  runs tracked in one repo, surfaces disagreed about which run you were in: `progress` read one
  while stage marks landed on another, unstarted one. One live session produced **three distinct
  ids across five tracked runs**. There is now one resolver, and it gives three outcomes three
  distinct representations — **resolved** (naming which rule answered), **ambiguous** (candidates
  listed, and mokata refuses, because a stage mark in the wrong run is a false green a later reader
  trusts), and **none**. Where it refuses, **`--run <id>`** and **`mokata resume --id <id>`** let you
  say which; the old refusal named a flag that did not exist. (OSS #44.)
- **A worktree no longer forks your memory and your audit ledger.** A second session in a
  `git worktree` of the same repo silently got **its own** memory store and **its own** ledger —
  measured before the fix, not theorised. Root-finding tested for a `.git` *directory*, and a linked
  worktree's `.git` is a *file*, so mokata walked past the worktree and then offered *"run
  `mokata init`"* inside it — and accepting that offer is what created the fork. Repo-scoped state
  now resolves to the **main checkout** from every tree, per-tree state stays local, and a worktree
  mokata cannot resolve is finally distinguishable from "not a mokata repo".
- **An approval you already gave stops being asked for again.** After you ran `mokata approve`, the
  statusline still said `⏳ awaiting approval` and `doctor` still counted it into *"N write(s)
  awaiting YOUR approval"*, handing you the approve command for a decision you had already made — on
  the exact surface you run when you think you are stuck. And **`spec amend`'s second step is finally
  advertised**: amending raises a proposal, you approve it, **and the amend must be re-run to redeem
  that approval**. That third step was stated in exactly one place you would only see by doing
  something else, so the menu offered one way forward that does not land the change and one way back
  that throws it away.

**Also fixed.**

- **Code navigation stops answering with a vendored copy of someone else's source.** A nested
  checkout inside your repo — a vendored dependency, a submodule, a worktree at an ordinary path —
  was indexed as *your* source, so *"where is this defined"* could answer **first** with a file you
  do not maintain. Nested checkouts are now detected structurally rather than by name, across every
  walker, and the skip is **declared**: `mokata index` names how many it pruned and where.
- **A leaf symbol no longer poisons a whole blast-radius verdict.** A symbol with no dependents has
  a legitimately empty impact set; that was read as "the graph could not answer" and degraded the
  verdict for the entire approach.
- **Destructive and data-moving paths leave an audit record, unconditionally.** `--drop-source` on a
  memory migration records a partial drop as the partial it was, and the session vault re-home now
  runs inside the same gate as every other durable write — each bundle secret-scanned, a refused
  bundle named and skipped, and a migration that cannot reach a ledger **refusing loudly instead of
  writing unrecorded**.
- **Three Windows documentation pages stop asserting a premise that had already been falsified**,
  and **one publisher now owns a GitHub Release** — two paths used to race for a single tag, which is
  why some earlier releases carry no attached artifacts at all.

**The ship-readiness gate is now advisory rather than enforcing**, and says so, along with what
promoting it would require. It was counted among the enforced gates while no production path
reaching it could be demonstrated. Claiming enforcement mokata does not perform is precisely the
failure this release is named for, so it is stated here rather than quietly recounted.

### Known limitations

- **The SQLite FTS5/BM25 lexical tier still ranks *worse* than the keyword floor it replaced, and the
  fix promised for this release did not land.** 0.0.16 disclosed this and said the repair was
  scheduled for **0.0.17**; **0.0.17 shipped no ranking work at all**, so both the measurement and the
  defect stand unchanged. `normalize_lexical_scores` scales each engine's scores against the best
  score *in its own result set*, flattening exactly the gap that would have ranked a mid-pack answer.
  On the 100,000-item benchmark, against the Jaccard keyword floor on the same probes and the same
  code — only the corpus size differs — the FTS tier measures **−5.6pp recall (0.5000 → 0.4444)** and
  **−10.8pp MRR@10 (0.8334 → 0.7258)**. At 5,000 items the same comparison loses no recall at all and
  only −3.3pp MRR, so a small corpus hides more than half of it. It is **now scheduled for 0.0.19**,
  with the rest of the ranking work, because the correct repair is rank-preserving normalization
  rather than a constant to tune. If you run a large store and your lexical results look mis-ordered,
  this is why — and this is the second release running that it has been true, which is the more
  useful thing to know than the first disclosure was.

**The instruments, in one line, because you do not consume them.** Pins that a production path
actually reaches each gate rather than only that the gate behaves when called; a register that makes
delegated writes visible to the write detector; mutation discipline that stops a size-preserving
mutant inflating a score; an audit of all 23 guards against whether they can grade anything at all;
a per-check supply-chain differ that fails on any single check dropping even when the aggregate
rises. **No behaviour of yours changes because of any of it** — it is why the fixes above are claims
you can check rather than claims you have to take.

Full detail, including everything trimmed from these notes, is in
[`CHANGELOG.md`](https://github.com/JasGujral/mokata-oss/blob/main/CHANGELOG.md).

Local-first, no telemetry, Apache-2.0.
