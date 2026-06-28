# mokata — Short Ad Script ("Review Every Decision")

**Format:** 60s screen-only spot. No actors, no faces, no voiceover. Just an IDE, a terminal, and kinetic type. Sound is ambient + mechanical keystrokes + a single rising synth pad. Cut on the beat.

**Look:** dark editor theme, monospace, one accent color (mokata mint). Cursor is the protagonist. Everything the agent does appears as real terminal output — gates, diffs, approvals — never a marketing mockup.

**Tagline (end card):** *mokata — the coding agent you don't have to babysit.*

---

## The 60-second cut

| # | Time | Visual (screen only) | On-screen text / terminal | Sound |
|---|------|----------------------|---------------------------|-------|
| 1 | 0:00–0:03 | Black. A single blinking cursor, center. | `_` | Low hum. One keystroke. |
| 2 | 0:03–0:07 | Cursor types a request into a chat pane. | `> add rate limiting to the API` | Mechanical keys. |
| 3 | 0:07–0:11 | Hard cut to a wall of red. Fast montage of *other* tools: 200 lines of code spat out instantly, untested. | Glitchy overlay: **"most agents start typing."** | Synth stab. Tension. |
| 4 | 0:11–0:13 | Snap to black. Beat of silence. | **"mokata asks first."** | Silence, then a soft chime. |
| 5 | 0:13–0:19 | The brainstorm pane. One question appears. Then a second. Never a wall. | `① fixed-window or token-bucket?`<br>`② per-user or per-IP?`<br>`◇ grounded in: middleware/auth.ts, memory/decisions` | Calm pad begins to rise. |
| 6 | 0:19–0:24 | A `HARD-GATE` banner slides in. Cursor clicks **Approve**. | `⛔ HARD-GATE — no spec until approach approved`<br>`▸ approved: token-bucket, per-user` | Click. A lock *unlatches*. |
| 7 | 0:24–0:30 | Spec scrolls past. A completeness check runs; each acceptance criterion maps to a test. | `SPEC ✓ complete`<br>`AC-1 → test_limit_resets ✓`<br>`AC-2 → test_burst_blocked ✓` | Ticks, one per AC. |
| 8 | 0:30–0:35 | Tests run **and fail first**. Red. Then code. Then green. | `RED ✗ 2 failing`<br>`…`<br>`GREEN ✓ 2 passing` | Fail buzz → satisfying resolve. |
| 9 | 0:35–0:41 | A diff appears — old → new — waiting. Nothing writes on its own. | `WRITE STAGED · memory + code`<br>`approve · edit · reject` | The pad holds. |
| 10 | 0:41–0:46 | Cursor scrolls the audit ledger: every gate, every tool call, every write, timestamped. | `ledger: 14 decisions · 0 silent` | Soft, fast page-flips. |
| 11 | 0:46–0:50 | Quick triad of side labels fade in over the running terminal. | **"Brainstorm first."**<br>**"Prove the spec."**<br>**"You approve every write."** | Pad swells. |
| 12 | 0:50–0:54 | Wide shot of the whole screen, calm, green checks down the gutter. A small footer: `local-first · no telemetry · Apache-2.0`. | `pipeline complete ✓` | Resolve to one clean note. |
| 13 | 0:54–0:58 | Cut to black. Logo draws on in the accent color. | **mokata** | Single chime. |
| 14 | 0:58–1:00 | Tagline under the logo. Cursor blinks once. | *the coding agent you don't have to babysit.*<br><sub>mostack.dev</sub> | Final keystroke. Cut. |

---

## The 15-second cut (social / pre-roll)

| Time | Visual | On-screen text | Sound |
|------|--------|----------------|-------|
| 0:00–0:03 | Blinking cursor, then a request typed. | `> add rate limiting` | Keys. |
| 0:03–0:06 | Red montage of an agent dumping untested code. | **"most agents just start typing."** | Stab. |
| 0:06–0:10 | mokata's gate + RED→GREEN, fast. | `⛔ approve approach` → `RED ✗` → `GREEN ✓` | Lock, buzz, resolve. |
| 0:10–0:13 | Audit ledger scrolls. | **"every decision, reviewable."** | Page-flips. |
| 0:13–0:15 | Logo + tagline. | **mokata** — *don't babysit your agent.* | Chime. |

---

## Why these beats (so the edit stays honest)

Every frame maps to a real mokata principle — no claim the product doesn't back:

- **"asks first" / one question at a time** → the Socratic brainstorm front-phase and its HARD-GATE (P5, D6).
- **completeness check + AC→test** → the provable completeness gate and static AC mapper (P4, D2/D3).
- **RED before GREEN** → enforced TDD spine (E1).
- **staged diff, approve/edit/reject** → human-gated writes + self-healing memory's surface-and-approve (P2, C5/C6).
- **audit ledger, "0 silent"** → the hero metric, *review every decision* (P7, I3).
- **local-first · no telemetry · Apache-2.0** → P8 and licensing, stated on screen, not just claimed.

## Voiceover option (if a VO is ever added)

Keep it to three lines, dry and confident:
> "Most coding agents start typing. mokata starts by asking. It writes the spec, proves it, tests it red before green — and never writes a line you didn't approve. Every decision, on the record. mokata."
