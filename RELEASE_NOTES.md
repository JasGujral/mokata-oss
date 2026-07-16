
mokata **0.0.14 — "Graph mandatory + trust fixes."** Upgrade with `pip install -U mokata`.
Additive; **no breaking changes**; local stays the zero-config default; **no schema change**.
Requires **Python ≥ 3.10**.

The codebase graph stops being an optional nicety and becomes a first-class, always-on structural
layer — with an honest fallback and a ledgered escape, never a silent lexical guess. Around it,
several trust surfaces are tightened: a ninth backed gate, an opt-in in-chat approve that still
can't let a model approve itself, a session-true statusline, and the setup one-shots that 0.0.13
left registered are now ledgered.

**Graph, mandatory by default — but honest.**

- **An embedded stdlib-AST floor ships in the box.** On a Python repo, mokata now answers
  callers/callees/imports/blast-radius by real name-resolution (`degraded=false`) with **zero
  dependencies** — a structural floor **above** grep, not the adopted graph. Adopt a richer graph
  when you want cross-language precision (`mokata graph adopt [code-review-graph|serena]`,
  human-gated); `mokata graph status` tells you which backend actually answers today.
- **`graph.required` defaults on.** A *degraded* (grep-floor) blast radius is now **refused** as a
  decision input — mokata will not let a lexical guess drive a decision. The way out is explicit and
  recorded: `--allow-degraded` accepts the degraded evidence for the session, is **TTY-reconfirmed**
  (a model cannot type it), is **ledgered**, and the result stays marked degraded.
- **Freshness before answer.** Every graph query front-runs a freshness check: a known-stale graph
  rebuilds *before* it answers, and a rebuild failure degrades **loud** to the AST floor on the
  files as they are right now — never stale structure.

**Trust, tightened.**

- **A ninth backed gate — the idea→code jump.** With a run registered but no approach approved, a
  native `Write`/`Edit` to an implementation file is now refused (exit 2) by the gate-guard hook —
  `approach-approval` is the fourth run-state gate, overridable like the others (named, reasoned,
  session-scoped, ledgered).
- **In-chat approve, opt-in and still un-mintable by the model.** You can enable an
  `mcp__mokata__approve` tool (`settings.approvals.in_chat`) — but it is **default-OFF**, enabling it
  is a human-gated, ledgered config write, it never rides the `mcp__mokata__*` auto-grant (setup
  writes a `permissions.ask` entry so the harness prompts you on **every** call), and it performs the
  exact same single-use, content-hash-bound, expiring approval as `mokata approve` (ledgered
  `actor="chat-relayed"`). Out of the box the model still cannot type its own consent.
- **Typed approach decisions.** An approved approach now carries machine-readable `decisions[]`
  (statement · rationale · code anchors · deferred). The spec's deferred scope **derives** from those
  decisions — one truth, never hand-written twice — review's first pass checks the diff's reach
  against the declared anchors, and a **prior-art bound step** refuses approach approval unless the
  step actually ran.
- **Session-true statusline + run lifecycle.** The active-run badge is now session-aware (a fresh
  window never wears another session's run), and a **shipped** run retires from the badge and
  `mokata progress` while a spec-emitted-but-unshipped run stays active. Nothing is deleted — explicit
  `run_id` views and resume still work.

**Closing 0.0.13's honest boundary.** 0.0.13 registered six CLI setup one-shots (init, harness
setup, skill write/prune, lifecycle remove) as writing outside the gate and filed them for here.
They now sit in a **ledgered** register (TTY consent + an audit record), the `KNOWN_BYPASS` register
is **empty**, and a sweep fails CI if any ungated durable writer ever appears. `mokata reset` writes
a user-scoped tombstone that survives `.mokata`'s removal.

**Fixes.** Simulated exec batches report **zero** actual token spend and a `simulated` (never green)
review verdict instead of a placeholder estimate or a false pass; `offer_text_once` never raises;
the tiered-semantic retrieval branch is kept and marked; and the `reset` propose→approve→redeem
round trip no longer crashes — the delete is deferred past the gate so an approved record is never
orphaned.

Local-first, no telemetry, Apache-2.0.
