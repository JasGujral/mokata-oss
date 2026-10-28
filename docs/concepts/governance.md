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

**Sync** hooks block (exit code 2); **async** hooks observe and **never block** (exceptions are
captured). Two sync `PreToolUse` hooks ship, and they differ in the **kind of block** they make:

| Sync hook | Matches | Kind of block |
|---|---|---|
| `mokata-hook secret-guard` | `Write` `Edit` `MultiEdit` `Bash` | **security** — **never** overridable |
| `mokata-hook gate-guard` | `Write` `Edit` `MultiEdit` `NotebookEdit` `Bash` (+ `Skill`, `Agent`/`Task`) | **methodology** (run-state) — **is** overridable: explicitly, re-confirmed, and on the ledger |

Two **async** hooks ship alongside them: `mokata-hook session-start` (the SessionStart briefing)
and `mokata-hook dirty-track` (a `PostToolUse` observability lane that records touched paths for
the code graph's freshness check — it **always exits 0** and can never block). A third
`PostToolUse` hook, `mokata-hook run-bind`, fires only after mokata's own `session_save` tool: it
ties this Claude Code session to the run that call registered (also exit 0, never blocks). And
`mokata-hook pre-compact` records this session's run position before Claude Code compacts the
conversation; after the compaction the SessionStart briefing re-states that run, its phase and the
gate that decides the next implementation write, and says whether anything moved — so a summarised
session is told what still binds it instead of rediscovering it by being blocked. During a run,
`mokata-hook subagent-start` and `mokata-hook subagent-stop` hold each subagent to a contract: a
read-only subagent (`settings.subagents.read_only_types`, default `Explore` and `Plan`) is blocked
from writing, a subagent may not start another past `settings.subagents.max_recursion_depth`, and
the subagent must end with a `HANDBACK` report (status, summary, files, tests, open). One that
does not is sent back once, then let go and recorded as `subagent_contract_unhonored` in the audit
ledger. A session outside any run sees none of this. And `mokata-hook stop` / `session-end` (plus
the pre-compact hook) draft one **session note** per session from mokata's record — never a memory
until you accept it with `mokata memory harvest --approve <id>`. All of
them are wired
by `mokata setup claude` (on by default; `--no-hooks` opts out) or by the plugin — and **only
Claude Code declares the `hooks` capability**, so on any other harness they are never wired and
the run-state gates below enforce nothing.

## Secret protection (I1) — 4 layers

`scan(text, path, for_send)` runs four independent layers — **signature** (known
credential patterns), **entropy** (high-entropy tokens), **path** (`.env`, `id_rsa`,
`*.pem`, …), **egress** (any secret in outbound content is fatal) — catching secrets
before they're written, committed, or sent.

The entropy layer is **identifier-aware**, because the thing that makes a scanner get
switched off is flagging a variable name. A high-entropy token is only reported when it
*also* has a mixed character class and *no word structure* — where word structure means
human-placed boundaries (separators or casing transitions) splitting the token into
word-shaped segments, so `getUserAuthTokenFromRequest` and `MOKATA_SESSION_ID_OVERRIDE`
read as identifiers rather than credentials. Underneath that sits a **known-shape floor**:
a small set of anchored vendor formats (AWS, GitHub, OpenAI, Slack, Google, JWT) matched
against both the raw token and its separator-stripped form, so a real key cannot buy an
exemption by looking word-shaped or by being broken into chunks. A floor match blocks
without consulting any exemption.

## Human-gated writes (I2) + trust dial (K3)

Every durable write goes through the `WriteGate`: secret scan (an un-overridable security
block) → human approval → commit → logged. The **trust dial** enforces `read-only` (cannot
write at all), `propose-only` (never auto-approved — an explicit human decision is required),
or `gated-write` (the default).

Set it per **surface** (`settings.trust.mcp`) or per **tool**
(`settings.trust.remember`), in one flat map; the tool's level overrides the surface's, and
anything unset is `gated-write`.

`propose-only` means *no auto-approval*, **not** *"prompt at a terminal"* — a prompt is only
one way to get a human's decision. An approval a human minted out-of-band with `mokata approve
<id>` (bound to that exact write's content hash, single-use, session-scoped) is a stronger one,
and it **satisfies** the rung. That distinction matters because the MCP server has no terminal
to prompt at.

**The honest ladder, per surface.** On **`mcp`** it is really **`read-only` ▸ write-allowed**:
every MCP write already needs that human-minted approval, so `propose-only` and `gated-write`
land on the same behaviour. `propose-only` pins that floor rather than adding a new one — we
say so instead of implying three rungs where there are two. On **`cli`** the dial is **not yet
enforced**. Two floors hold at every level and on every surface: a **secret is hard-blocked**
(approval cannot override it), and no trust level lets the *model* mint its own consent.

**One-key approve / edit / reject (Stage 54c).** Where a gated write offers an *editable*
value — a memory edit, or a self-healing `old → new` proposal — the human gate isn't just
yes/no: it's **approve** (apply the proposed value), **edit** (supply a different one), or
**reject**, with the **safe default = no change** (a blank answer or EOF rejects — nothing is
ever rewritten without your say-so). This is a richer *prompt*, not a weaker gate: it routes
through the same `WriteGate`, so the **secret hard-block still fires** — `approve` (or an
edited value containing a secret) can **never** override a security block. Plain
approve/decline stays everywhere else.

## Run-state gates on native writes (the `gate-guard` hook)

The `WriteGate` above governs *mokata's own* durable writes. The **`gate-guard`** governs the
**harness's native `Write`/`Edit`** — the tool the model reaches for when it writes your code.
It is where the method stops being advice: a write that breaks the run's own methodology is
**blocked**, with a reason and the way out. It is the gate you actually *see*.

**Four run-state gates**, each blocking a native write to an **implementation** file:

| Gate | Blocks when… |
|---|---|
| `approach-approval` | a run is registered (brainstorm in progress) but **no approach is approved yet** — the idea→code jump |
| `spec-persisted` | an approach is approved for this run but **no spec is emitted** |
| `no-code-without-failing-test` | the spec is emitted but **no failing test is on record** |
| `spec-scope` | the write is **outside the spec's authorized surface** or the **active task's scope**, spells a **deferred** marker, a **spec amend is in progress**, or the **plan no longer matches its approved hash** |

A block is one line on stderr, then exit 2. Every reason names the file, says what to do, and
offers the override:

```text
BLOCKED [no-code-without-failing-test] no failing test is on record for this run — auth.py is
implementation. Write the failing test first and watch it fail (/test), or override:
mokata gate override no-code-without-failing-test --reason "<why>"
```

**Scope — what it deliberately does *not* police.**

- It fires **only inside an active mokata run** (an approach approved and/or a spec emitted).
  Hand-editing a repo outside a run is **never** policed: guardrails on the pipeline, not house
  arrest.
- **Test files are always writable.** You must be able to write the failing test — RED is the
  *permission* to implement, not the prohibition.
- **A pipeline you started waits for its run.** Typing `/brainstorm` (or Claude invoking the
  brainstorm skill) tells mokata this session wants a tracked run. Until brainstorm registers it,
  implementation writes in **that session** are blocked with the remedy (let brainstorm do its
  step 1, or `/clear` to drop the pipeline). Other sessions are not affected, and the wait
  expires after two hours.
- **Parallel runs are policed separately.** Each session that started a pipeline is tied to the
  run its own registration returned, so two windows running two pipelines in one repo each get
  their own run's verdict.
- **Another session's run never polices you.** A session that did *not* start a pipeline is not
  gated by a run that another session's registration owns, even when it is the only run in the
  repo. mokata says so once ("run-state gates are OFF for this window — run X belongs to another
  session") and tells you how to be tracked (start `/brainstorm` in this window). A run no
  session owns (started from the terminal) still polices every window, and a `MOKATA_SESSION_ID`
  pin is always honoured.
- **Ambiguity fails OPEN.** For a session that did *not* start a pipeline here, if two runs have
  state in one repo and neither is pinned, mokata will not guess which window your edit belongs
  to, so **every run-state gate turns off** for that window — and says so, once. Pin one with
  `MOKATA_SESSION_ID` to get enforcement back.
- **Shell writes, and their limit:** the gate-guard also reads `Bash`: every file a shell command writes that mokata can see (redirections, `sed -i`, `tee`, `cp`/`mv`, inline `python -c` writes, bare file names included) meets the same run-state gates as a `Write` to that file. During a run, a command whose writes mokata cannot read (`$VAR` or `$(…)` destinations, `eval`, `xargs`/`find -exec` running a writer, a heredoc into a shell, inline `node -e`-style code that writes) is refused with the reason; reads are never refused. What it does not police is a program it runs (`python -m unittest`, `make`): no command line says what those write.

**The approved plan binds every later stage (0.0.26).** The spec carries a task plan (`tasks[]`)
and the SHA-256 hash of the plan the human approved. Develop works task by task inside the active
task's scope (`mokata spec task`). If the plan on record changes after approval, implementation
writes, stage entry (`progress mark`) and ship all refuse until the plan is re-approved. That
refusal is the one `spec-scope` block the override below does **not** lift: the way on is a new
approval, not a waiver. See `mokata spec emit` and `mokata spec task` in the CLI reference.

## Loop gates: a "done" must be backed, three fixes then stop, five reviews then you

The run-state gates judge a *write*. Three more gates stop a **loop** the agent can get stuck in.
They fire only inside a run your session is attached to, and each takes the same P14 override.

| Gate | What it stops | Enforced by |
|---|---|---|
| `stop-evidence` | A session (or subagent) ending its turn **claiming the work is done** ("all tests pass", "implementation complete") when the run's **recorded** evidence does not back it. It checks four things. Is a test still **RED**? Does an acceptance criterion of the emitted spec have **no test recorded GREEN** for it? Did the last recorded test run **fail**, or is it **older than the run's last write**? Is the run **in review with no review verdict recorded**? A subagent's `status: done` handback that changed implementation must name the test it ran, and that test must have passed. | the `Stop` hook (`mokata-hook stop-gate`) and the `SubagentStop` hook |
| `fix-breaker` | A **fourth blind fix**. Each fix attempt is recorded (`mokata progress fix-attempt --failure "<test id or error line>" --failed`). The third failed attempt at the **same** failure trips the breaker, and the trip is written to the audit ledger (`loop_breaker`). Three failures say the *approach* is wrong, so implementation writes for the run stay blocked until a **new approach is approved** through the approach-approval gate (`/brainstorm`), or a human overrides. | the `gate-guard` hook |
| `review-loop` | A **sixth review round**. Only Blocking findings keep the loop going; Minor ones are deferred. At round 4 the reviewer moves up one model tier. After five failed rounds in a row, recording another verdict is refused with an escalation (ledgered), and the human takes it from there. A passed round resets the count. | `mokata progress record-review` / the `review_record` tool |

**Recorded state only.** The `stop-evidence` gate never reads your code or walks your repository,
so its cost is the same on a ten-file project and a multi-million-line one. It reads what the run
recorded:

- the AC→test map, which the spec emit seeds from the tests the completeness gate mapped;
- the TDD record (RED/GREEN);
- the last test run;
- the time of the run's last write;
- the progress log.

The agent records each test run as it makes it:

```bash
mokata progress test-run --passed --command "pytest -q" --test test_login --ac AC-1=test_login
```

A failed run records the named tests RED, and a passed run records them GREEN.

**Fail-closed, never a trap.** The gate blocks when the record does not back the claim, and it
also blocks when the record cannot be read or the hook crashes. A blocked stop goes back to the
model with the missing evidence named, for example `no test is recorded for acceptance criteria
AC-2`. Two exits keep a human from ever being stuck:

- `mokata gate override stop-evidence --reason "<why>"`: you accept the stop unverified, on the
  ledger.
- After **three** consecutive blocks in one session, the next stop is let through. You see a
  message naming what is unverified, the release is written to the audit ledger (`stop_evidence`),
  and nothing marks the run done. Ship's own gates still apply. A crashing hook blocks once and lets
  the next stop through the same way.

A stop that **does not claim completion** (a question, a blocker report) is never blocked, and
neither is a stop right after the breaker trips or the review loop hits its cap, because stopping to
escalate is exactly what those two ask for.

## Model tier per role

Every role mokata spawns a subagent for names its model tier. A tier is one of Claude Code's own
model aliases (`haiku` < `sonnet` < `opus`), resolved inside your login. There is no API key and no
model id.

| role | default | floor |
|---|---|---|
| `explorer` | haiku | — |
| `implementer` | sonnet | — |
| `debugger` | opus | — |
| `reviewer` | sonnet | sonnet |
| `final-reviewer` | opus | opus |

**How the skills use it.** A skill spawns a role's subagent with the Agent tool's `model` set and
`[mokata:<role>]` in the description. Review rounds 1–3 run as `reviewer`. Round 4 onward, and the
closing review, run as `final-reviewer`.

**What the gate-guard hook does with it:**

- It **blocks** a role-tagged spawn that names no tier, or one below its role's floor (gate
  `model-tier`).
- It records the tier for the run, and writes an audit-ledger row (`model_tier`).
- When the subagent stops, mokata reads its own transcript for the model that actually answered and
  its token usage. That is the run's cost record, as measured by Claude Code.

`mokata progress models` shows the tiers and the run's record. Change a tier with
`mokata config set settings.models.<role> <tier>`. The change is human-gated like every config
write, and a tier below a role's floor is refused.

**The override (P14).** These are *methodology* gates, not security ones, so they are
overridable — under exactly one discipline:

```bash
mokata gate status                                 # read-only: what's enforced / overridden here
mokata gate override spec-scope --reason "<why>"   # ONE gate, THIS session
mokata gate clear                                  # drop this session's overrides
```

`--reason` is **required**, the override is **re-confirmed** interactively (it restates exactly
what stops being enforced), **session-scoped** (a new session enforces again — nothing to remember
to turn off), and **ledgered** (who · when · which gate · why, visible in `mokata audit` forever).
Overrides stack, one named gate at a time. There is deliberately **no env-var kill switch** — a
side door any process could open silently — and **no MCP tool and no slash command**, because a
model-invocable override would let the model clear its own constraint. The secret-guard is not on
this list and never will be.

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

## Independent review — a fresh pair of eyes, not a self-check

The closing `/review` is the gate before you land — so by default it runs as a
**fresh-context subagent**, not as the builder re-reading its own work. mokata hands that
subagent a **self-contained brief** — the emitted spec + its acceptance criteria, the approved
approach/refinement set, the **diff** under review, and how to run the tests — and *no* builder
conclusions. The subagent re-derives the verdict from the code and its **own** test runs; it
must reach the two-pass verdict on its own, not ratify the builder's.

Where a harness has **no subagents** (or you set `settings.review.independent = off`), review
**degrades cleanly** to the inline two-pass and **says so honestly** — it prints
`review: inline — this harness has no subagents, so this review shares the builder's context`
(or the equivalent config note) and continues. Independence is the **default**, never a hard
requirement — mokata never blocks just because a harness can't spawn a subagent.

The verdict is **persisted as evidence**, and `/ship` reads it: ship **blocks** unless a
**passing** review is on record (no verdict, or a failed one, stops the finish), and it surfaces
whether that review was `independent ✓` or merely `inline` so the strength of the signal is
always visible and logged. Turn independence off (or back on) with:

```bash
mokata config set settings.review.independent off   # inline two-pass (default is on)
mokata config set settings.review.independent on
```

An absent, broken, or unrecognised value reads as `on`, so the stronger independent review is
never silently lost.

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
- **End-of-run "what I changed and WHY."** Finishing a run (`/ship`) folds in a bounded
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
conflicts, bad trust levels, and audit-ledger integrity. It also carries the **honesty**
lines — a degraded capability must never be a silent one:

- **What degraded this session** — the notices are *remembered*, not just printed, so `doctor` can
  list every capability that fell back to a floor (a team read served from local storage, a code
  graph fallen to grep), each naming its failure class. It's process-lifetime: nothing degraded ⇒
  nothing printed.
- **Team mode** — the preflight (shared-DB reachability + schema compatibility) and, when the local
  write journal has unflushed entries, the count of approved team writes **not yet flushed to the
  team DB** (with the oldest's age and the fix: `mokata sync`).
- **Trust surface-truth** — when `settings.trust` is set, one line stating exactly where the dial
  has teeth (the MCP write surface) and where it does not.

`mokata reset` removes `.mokata/` state (preview-able,
human-gated, optionally backed up — reversible-aware); `--keep-config` keeps the manifest.
