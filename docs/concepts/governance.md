# Concept: governance & audit

Everything mokata does is reviewable and gated. It is **local-first**: nothing leaves the
machine unless you wire it; there is no telemetry.

## 4-tier rules + constitution (G1)

| Tier | Source | Cap |
|---|---|---|
| `always_on` | the reflex rules injected each session | **≤ 60 lines** |
| `agent_memory` | per-agent `MEMORY.md` | **≤ 200 lines** |
| `steering` | optional `.mokata/steering.md` | — |
| `articles` | the constitution's governing articles | — |

`mokata rules` shows the tiers and their line budgets; over-cap tiers are flagged.

## Rules-vs-gates-vs-hooks taxonomy (G2)

A rule is **advisory** (stays prose), **blocking** (make it a gate), or **event-driven**
(make it a hook). "Checkable → a gate or a hook, not prose."

## Karpathy gates (G3, hybrid)

Four engine checks, each registered/toggleable/audited through the rules layer (reusing the
shared gate type), firing at their pipeline point:

| Gate | Phase | Checks |
|---|---|---|
| `think-first` | analysis | a plan/approach exists before code |
| `simplicity` | strawman | complexity under a cap |
| `surgical-scope` | emit | touched files under a cap |
| `verify` | completeness_gate | success criteria defined + verified |

Toggle via `settings.governance.karpathy.<id>` (default on); a disabled gate does not fire
and is not audited.

## Hooks (G4)

**Sync** hooks block only for **security** (exit code 2) — a non-security sync hook is
rejected. **Async** hooks observe and **never block** (exceptions are captured). The
shipped sync security hook is `hooks/secret_guard.py`; the async one is
`hooks/session_start.py`.

## Secret protection (I1) — 4 layers

`scan(text, path, for_send)` runs four independent layers — **signature** (known
credential patterns), **entropy** (high-entropy tokens), **path** (`.env`, `id_rsa`,
`*.pem`, …), **egress** (any secret in outbound content is fatal) — catching secrets
before they're written, committed, or sent.

## Human-gated writes (I2) + trust dial (K3)

Every durable write goes through the `WriteGate`: secret scan (an un-overridable security
block) → human approval → commit → logged. The **trust dial**
(`settings.trust.<tool>`) enforces `read-only` (cannot write), `propose-only` (always
surfaced for approval, never auto-approved), or `gated-write` (default).

**One-key approve / edit / reject (Stage 54c).** Where a gated write offers an *editable*
value — a memory edit, or a self-healing `old → new` proposal — the human gate isn't just
yes/no: it's **approve** (apply the proposed value), **edit** (supply a different one), or
**reject**, with the **safe default = no change** (a blank answer or EOF rejects — nothing is
ever rewritten without your say-so). This is a richer *prompt*, not a weaker gate: it routes
through the same `WriteGate`, so the **secret hard-block still fires** — `approve` (or an
edited value containing a secret) can **never** override a security block. Plain
approve/decline stays everywhere else.

## Plan adherence — never silently deviate

A plan change is a **durable change**, so it's human-gated like any other. During
implementation mokata sticks to the **approved plan**: the approved approach (brainstorm) or
refinement set (refine), the emitted spec, and its acceptance criteria. It does **not**
change scope, the chosen approach, the ACs, or the design beyond what was approved — and it
never expands scope unasked.

If a deviation becomes necessary — an AC is wrong or infeasible, the approved approach
doesn't work, a materially better design appears, or an unforeseen constraint blocks it —
mokata **STOPS and asks first**: it surfaces the deviation (*what changes · why · the
options*) and waits for explicit approval. An approved change **re-enters the approval
surface** (re-approve the approach/refinements, or amend the spec so every AC still maps to a
test), and the request *and* the decision are recorded in the audit ledger.

This is the **forward** guardrail. The backstop already exists: the two-pass `review` flags
any implementation that diverges from the approved plan, so an unapproved deviation fails
review. Together: *mokata did exactly what you approved — or it asked.*

## Spec-awareness — don't break a saved spec by mistake (Stage 37)

The deviation guard protects *this* story's plan; spec-awareness protects *previously-approved*
work. Before a change, mokata checks it against the **saved specs** and **recorded decisions**:
does it touch or contradict something already specified or decided? It computes the change's
**touch-set** — the symbols/files in play, **expanded through the code graph** (a spec about a
caller of the changed code is caught too) — and looks for overlap with the spec corpus and
decision memory.

If it finds one, it does **not** silently proceed: it surfaces *"this change affects spec X /
decision Y — here's where"* and routes it through the same **deviation gate** — you confirm
(amend/supersede the affected spec) or re-plan. The conflict **and** your resolution are logged.
It's **frugal** (only the touch-set is checked, never the whole corpus) and **degrade-clean**:
no saved specs yet ⇒ a no-op (no false alarm); no code graph ⇒ a lexical/file-overlap check that
**says so**. Run it via `mokata spec-check` or the `spec_check` tool; `spec`/`refine`/`develop`
invoke it as part of grounding.

## Audit ledger (I3)

An append-only `.mokata/temp_local/audit/ledger.jsonl` records every gate decision, tool
call, hook, write, savings event, subagent decision, healing/consolidation decision, and
deviation request/decision — each with a monotonic `seq`. `mokata audit` prints it;
`mokata audit --why` renders the read-only **what + decision + why** timeline (Stage 49).

## Trust & visibility (Stage 60)

Three read-only, derived surfaces so you can always see what mokata did **and why** — none of
them mutate the state they show:

- **Live `mokata govern` / `mokata watch`.** The governance dashboard takes the same optional
  self-meta-refresh as `watch`: `mokata govern --live` re-writes on a 2s interval and the page
  refreshes itself (honouring `settings.ux.progress`; the static snapshot — `mokata govern` /
  `--once` — always works and is byte-identical/deterministic). Self-contained: inline CSS, **no
  network**, under gitignored `temp_local/`.
- **"What changed since last session."** A concise diff of new/changed memory, new rules, and
  the gate decisions made since a lightweight **last-session snapshot**. The snapshot is captured
  at the session boundary (the SessionStart hook) into transient `temp_local/`; the diff
  **derives** against it and never writes. It surfaces in the `govern` view and as one bounded
  SessionStart briefing line (within the 2k budget; absent on a first session or when nothing
  changed). Read-only — it bumps no counter and the snapshot capture is read-only on the
  governed state.
- **End-of-run "what I changed and WHY."** Finishing a run (`/mokata:ship`) folds in a bounded
  `audit --why` recap of this run — what changed and the why behind each gate decision — so
  landing it is a reviewed decision. Shipping stays **human-gated**: mokata records the landing
  choice but **never merges / opens a PR / deletes** on its own.

## Reversibility (I5) & resume (I6)

`ReversibleStateStore` records each write's prior value to a durable undo log; `revert`
restores it. `PipelineCheckpoint` persists each passed gate so an interrupted run resumes
from the last passed gate — a crash never loses state.

## Lethal-trifecta gate (I4)

When system access + private data + an outbound action coexist, the outbound action is
**gated behind explicit human approval** (and logged). When the trifecta isn't active,
no gate is imposed.

## Diagnose & reset (K5/K6)

`mokata doctor` reports manifest errors, missing providers, broken adapters, role
conflicts, and bad trust levels. `mokata reset` removes `.mokata/` state (preview-able,
human-gated, optionally backed up — reversible-aware); `--keep-config` keeps the manifest.
