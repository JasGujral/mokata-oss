"""A4 — SessionStart bootstrap (sub-2k-token context injection).

At session start mokata injects one compact briefing: which stack you're in, which
capabilities are live (and which degraded), and which gates are inviolable. It is
written terse on purpose (P11 "caveman vocab") and is enforced to stay under a hard
2,000-token budget so it never crowds the context window.

Token counting here is a deliberately conservative *estimate* (chars/4, the common
rule of thumb) — the spine must not depend on a tokenizer library. The estimate runs
high rather than low, so passing the budget here means passing it for real.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import List, Optional

from .config import Surface
from .router import Resolution

# Hard ceiling. The briefing is built to come in far under this; the budget exists so
# the spine can *prove* it stays small, and truncate defensively if a future addition
# ever grows it.
BOOTSTRAP_TOKEN_BUDGET = 2000

# H-1a — the PER-TURN recall injection budget (`UserPromptSubmit`). A HARD 300 tokens, not a
# target: the check is `estimate_tokens(<the emitted text>) <= 300`, on the bytes that actually
# leave the hook, with `cap_summary` as the closing arithmetic backstop.
#
# It is declared HERE, beside the briefing budget, on purpose. The two are one claim — "mokata's
# automatic context injection costs at most this much per session and this much per turn" — and a
# claim split across two modules is a claim nobody can check. Doc 84's constraint on the injection
# layer says the same thing from the other side: H-1 must SHARE the budget accounting, never open
# a second competing channel. Both budgets live here; both are spent through `_emit`.
#
# Why 300 and not more: it is paid EVERY TURN. A 2k briefing costs 2k once; 300 tokens across a
# 40-turn session is already 12k. The frugality is the feature (P11) — the budget is what forces
# the injection to be the few items relevant to THIS turn rather than a standing corpus dump.
INJECTION_TOKEN_BUDGET = 300

# Stage 36 — how many captured rule/guardrail lines the briefing surfaces. A small, capped
# always-on set (P11): the rest stay retrievable via `mokata memory --kind rule`, never dumped.
BRIEFING_RULES_MAX_LINES = 12

# H-1a — the RESERVED always-on slice inside the per-turn budget. Rules and guardrails are the
# always-on set: they are what the turn must not violate, so they get a floor the ranked recall
# cannot squeeze out. Small (the briefing already carried the full set at session start); ranked
# by the turn's query so the reserved slots go to the rules this turn is most likely to touch.
INJECTION_RULES_MAX_LINES = 4

# H-1a — how many JIT items are CONSIDERED for the remainder. The budget, not this number, is what
# decides how many survive; this only bounds the ranking work.
INJECTION_JIT_TOP_K = 5

# Inviolable gates, surfaced every session (P2 + P8 — cannot be configured away).
_INVIOLABLE_GATES = [
    "human-gate: every durable write (code, memory, config) is staged for approval",
    "local-first: nothing leaves the machine unless you explicitly wire it; no telemetry",
]

# Injected on SessionStart when a repo isn't set up yet (Stage 23 Part 4): mokata asks
# FIRST instead of waiting to be told. One offer, never a nag — the moment .mokata/ exists
# this disappears for good. It instructs Claude to offer; it never writes anything itself.
SETUP_OFFER = (
    "mokata: this project is NOT set up yet (no .mokata/). Proactively OFFER to initialize "
    "it: ask which profile (minimal / standard / full — full wires every graph & memory "
    "provider, standard is the lean default, minimal is engine-only), then run "
    "`/mokata:init <profile>` (or the gated `init` MCP tool). Preview first, get explicit "
    "approval, and never write without it. If the user declines, do not ask again. Once it's "
    "set up, OFFER `/mokata:onboard` to capture the project's rules, guardrails, conventions, "
    "and domain context as typed memory mokata will honour — optional, never forced."
)


def build_setup_offer(budget: int = BOOTSTRAP_TOKEN_BUDGET) -> "BootstrapResult":
    """The one-line setup offer for an uninitialized repo (Stage 23). Same shape as the
    briefing so the SessionStart hook emits it identically."""
    text = SETUP_OFFER + "\n"
    return BootstrapResult(text=text, token_estimate=estimate_tokens(text), budget=budget)


def estimate_tokens(text: str) -> int:
    """Conservative token estimate. ~4 chars/token, rounded up; empty -> 0."""
    if not text:
        return 0
    return -(-len(text) // 4)  # ceil division


@dataclass
class BootstrapResult:
    text: str
    token_estimate: int
    budget: int

    @property
    def within_budget(self) -> bool:
        return self.token_estimate <= self.budget


def _render(surface: Surface) -> str:
    m = surface.manifest
    lines: List[str] = []
    lines.append(f"# mokata {m.mokata_version} · profile: {m.profile}")
    lines.append("")
    # TM.S1 — the run mode (local|team) leads the live briefing so a session is never
    # ambiguous about which mode it's in. Degrade-clean (→ local); one line (token budget).
    from .run_mode import mode_line, read_mode, TEAM
    lines.append(mode_line(surface))
    # TM.S5 — in team mode the in-chat briefing carries the ONE health verdict (same cached probe
    # the badge/mode/doctor use): a broken connection is surfaced HERE, never silent, and trouble
    # offers work-locally. The session-start probe also refreshes the shared cache the badge reads.
    # Bounded (≤500ms) + degrade-clean; one compact line healthy, +offer on trouble (token budget).
    if read_mode(surface) == TEAM:
        # D5 — the fallback SHAPE, not a `pass`. This used to swallow every error, which made the
        # health verdict line AND the work-locally offer DISAPPEAR from the briefing — and their
        # ABSENCE is exactly what a local-mode briefing looks like, so a broken shared DB read as a
        # clean session. Mirror `degrade.resolve_read_routing`: on failure fall back to an OFFLINE
        # verdict and STILL PRINT THE LINE (+ the offer, since OFFLINE is trouble). `team_health`
        # itself is imported unguarded, exactly like `.run_mode` above — a first-party import that
        # cannot fail without the whole briefing being meaningless.
        import os as _os
        from . import team_health
        try:
            verdict = team_health.check(surface, environ=_os.environ)
        except (ImportError, OSError) as exc:      # check() is itself fail-closed; belt-and-braces
            verdict = team_health.HealthVerdict(team_health.OFFLINE,
                                                f"health check failed ({str(exc)[:120]})")
        lines.append(team_health.summary_line(verdict))
        if verdict.trouble:
            lines.append(f"  → {team_health.work_locally_offer()}")
    lines.append("")
    lines.append("Active gates (inviolable):")
    for gate in _INVIOLABLE_GATES:
        lines.append(f"- {gate}")
    # DOC-ONBOARD — immediately under the gates, because that is what stale wiring costs you:
    # the gates listed above are the ones this mokata HAS, not necessarily the ones wired. One
    # line, only when actually drifted (a current install's briefing is byte-identical).
    drift_line = _wiring_drift_line(surface)
    if drift_line:
        lines.append(drift_line)
    lines.append("")

    # Live capability routing — the heart of "what stack am I in right now".
    resolutions: List[Resolution] = surface.router.resolve_all()
    if resolutions:
        lines.append("Capabilities (resolved now):")
        for r in resolutions:
            if r.available and not r.degraded:
                lines.append(f"- {r.need} -> {r.tool}")
            elif r.available:
                lines.append(
                    f"- {r.need} -> {r.tool} (degraded; preferred "
                    f"'{r.preferred}' absent)"
                )
            else:
                lines.append(f"- {r.need} -> UNAVAILABLE (no provider present)")
        lines.append("")

    # GR.S4 / HP.S1 — ONE bounded, graph-derived structure line. Rides the SAME briefing text
    # (no second channel) inside the ≤2k budget; ABSENT (byte-identical) when the structural
    # floor can't summarise the repo. So a session opens knowing the real code structure, not a
    # memory of it.
    structure_line = _graph_structure_line(surface)
    if structure_line:
        lines.append(structure_line)
        lines.append("")

    # Layers (one terse line).
    if m.layers:
        on = [name for name in m.layers if m.layer_enabled(name)]
        off = [name for name in m.layers if not m.layer_enabled(name)]
        layer_bits = []
        if on:
            layer_bits.append("on: " + ", ".join(on))
        if off:
            layer_bits.append("off: " + ", ".join(off))
        lines.append("Layers — " + "; ".join(layer_bits))

    # Constitution pointer + article count (don't inline the prose; keep budget low).
    c = surface.constitution
    if c.present:
        n = len(c.articles())
        lines.append(
            f"Constitution: {surface.mokata_dir}/constitution.md "
            f"({n} article{'s' if n != 1 else ''}) — read before non-trivial work."
        )
    else:
        lines.append("Constitution: none committed yet.")

    # Stage 36 — captured project rules/guardrails, honoured EVERY run. A small capped set
    # (P11); over-budget entries are flagged, not dumped. Degrade-clean if memory is off.
    rules_lines = _always_on_rule_lines(surface)
    if rules_lines:
        lines.append("")
        lines.append("Project rules & guardrails (always honour):")
        lines.extend(rules_lines)

    # Stage 54 — proactive resume surfacing: ONE line (max two) when there's a resumable run
    # or an in-progress brainstorm, ABSENT (no noise) when there's nothing to pick up.
    resume_hint = build_resume_hint(surface)
    if resume_hint:
        lines.append("")
        lines.extend(resume_hint.split("\n"))

    # Stage 60 — "what changed since last session": ONE bounded line, read-only/derived, ABSENT
    # (no noise) on a first session or when nothing changed. So reopening a repo tells you what
    # moved while you were away. Never writes; the baseline is captured by the SessionStart hook.
    since_line = _changed_since_line(surface)
    if since_line:
        lines.append("")
        lines.append(since_line)

    lines.append("")
    lines.append(
        "Reflex: before acting, check the relevant gate/skill; verify with evidence, "
        "not claims. Captured context/reference surfaces just-in-time when relevant "
        "(`mokata memory`), never all at once."
    )
    # SK.S4 — proactive dispatch: every mokata skill (not just brainstorm) is model-invocable
    # and should auto-fire the moment a task fits its triggers, announced with the ⛭ banner.
    lines.append(
        "Skills: mokata's capabilities are auto-firing skills — engage the matching one "
        "PROACTIVELY when a task fits it (brainstorm, spec, test, develop, review, refine, "
        "debug, bug, optimize, ship, onboard, govern, session, playbook, mcp-repair), announce "
        "it with the ⛭ banner, follow its Contract + gate, and don't engage against its "
        "stated NOT-when."
    )
    return "\n".join(lines) + "\n"


def _wiring_drift_line(surface: Surface) -> Optional[str]:
    """DOC-ONBOARD — ONE line when the harness wiring is older than the installed mokata, and
    None (byte-identical briefing) when it is current.

    This is the channel that reaches the user who never runs `doctor`: the session they are
    already in. It is honest about its own blind spot — if the wiring is dead rather than merely
    stale, this hook did not run and nothing here printed, which is exactly why the same verdict
    also rides the MCP `status` surface and `mokata upgrade`.

    Derived, never re-derived: the verdict is `hook_wiring.wiring_drift` and the wording is
    `wiring_drift_line`, the same two the doctor finding and the MCP surface read. Fully
    guarded — a briefing must never fail because a diagnostic did."""
    try:
        from .hook_wiring import wiring_drift, wiring_drift_line
        return wiring_drift_line(wiring_drift(surface.root))
    except Exception:
        # The floor: this is an ADVISORY line on the SessionStart path. Anything it could raise
        # (an exotic settings.json, a half-installed package) must cost the user the line, never
        # the briefing — and `wiring_drift` is itself never-raise, so this is belt-and-braces.
        return None


def _graph_structure_line(surface: Surface) -> Optional[str]:
    """The GR.S4 briefing structure line, or None (byte-identical) on any repo the structural
    floor can't summarise. Fully guarded — the briefing never crashes on it."""
    try:
        from .knowledge.layer import graph_structure_line
        return graph_structure_line(surface)
    except Exception:
        return None


def _always_on_rule_lines(surface: Surface) -> List[str]:
    """The capped rule/guardrail lines for the briefing, or [] when the memory store can't be read.

    D5 — this was the WORST silent degrade in the codebase. It swallowed every error and returned
    [], so the project's captured rules & guardrails simply NEVER REACHED the briefing: Claude then
    proceeded with NONE of the user's guardrails, and the briefing looked completely normal —
    byte-indistinguishable from a project that had captured no rules at all. The user believed
    governance was on and it was NOT.

    The [] fallback STAYS (a briefing must never crash the session), but it is no longer a secret:
    one loud, classed notice per process says the rules are not being applied and how to fix it.
    The narrow classes are the ones a memory read genuinely raises — the SQLite floor
    (`sqlite3.Error`: locked/corrupt/permission-broken `.mokata/`), the local IO under it
    (`OSError`), and a half-installed package (`ImportError`). A shared-Postgres failure never gets
    here: `select_memory_backend` already degrades it to the SQLite floor."""
    try:
        from .memory import MemoryStore, always_on_lines
        store = MemoryStore.from_surface(surface)
        lines, _overflow = always_on_lines(store, BRIEFING_RULES_MAX_LINES)
        return lines
    except (OSError, ImportError, sqlite3.Error) as exc:
        from .degrade import FAILURE_UNREACHABLE, note_degraded
        note_degraded("memory-rules", FAILURE_UNREACHABLE,
                      fallback="your project rules are NOT being applied this session",
                      fix="run `mokata doctor` to repair the memory store",
                      detail=str(exc)[:200])
        return []


def _changed_since_line(surface: Surface) -> "str | None":
    """The Stage 60 "since last session" briefing line, or None. Read-only/derived; degrade-clean
    (any issue / first session / no changes -> None, never an exception)."""
    try:
        from .visibility import changed_since_line
        return changed_since_line(surface)
    except (ImportError, OSError, ValueError):
        # D5 — the real raisers: a half-installed package (ImportError), an unreadable state/
        # baseline file (OSError), a torn/unparseable JSON baseline (ValueError, the base of
        # json.JSONDecodeError). Behaviour is unchanged; a typo (AttributeError) now surfaces.
        return None


# Stage 54 — proactive resume surfacing. The briefing leads off with ONE line (max two) when
# there's something to pick up: a resumable run and/or an in-progress brainstorm. So reopening
# a repo TELLS you there's a run mid-flight, instead of you having to remember. Read-only —
# composes progress.list_sessions + brainstorm.restore_brainstorm_progress, which only `read`
# the StateStore (no stat/counter bumps, no MemoryStore); deterministic; degrade-clean to None.
_RESUME_GLYPH = "▸"


def _resume_run_line(surface: Surface) -> "str | None":
    """The single most-actionable resumable-run line, or None. A run is resumable when it's the
    active run (the one `resume` would pick), has a passed gate (progress to pick up), and isn't
    complete. A fresh run with nothing passed is not surfaced (no progress = no noise)."""
    from .progress import list_sessions
    active = next((s for s in list_sessions(surface.state) if s.active), None)
    if active is None or active.complete:
        return None
    if active.last_passed is None or active.resume_phase is None:
        return None
    return (f"{_RESUME_GLYPH} Resume: pipeline at '{active.resume_phase}' "
            f"(last passed '{active.last_passed}') — run `mokata resume`")


def _resume_brainstorm_line(surface: Surface) -> "str | None":
    """The in-progress-brainstorm line, or None. An already-approved checkpoint is not an
    in-progress brainstorm, so it isn't surfaced here (the run line covers downstream work)."""
    from .brainstorm import restore_brainstorm_progress
    bs = restore_brainstorm_progress(surface.state)
    if bs is None or getattr(bs, "approved", False):
        return None
    topic = (bs.topic or "").strip()
    label = f" '{topic}'" if topic else ""
    return (f"{_RESUME_GLYPH} Resume: in-progress brainstorm{label} — "
            f"run `/brainstorm` (or `mokata brainstorm`)")


def build_resume_hint(surface: Surface) -> "str | None":
    """ONE line (max two) when there's something to pick up, else None. Pure + read-only +
    degrade-clean: any error (no sessions, no brainstorm, a corrupt checkpoint) -> None, never
    an exception. Prefers the most actionable single line; surfaces both compactly when both
    exist (still ≤ 2 short lines — never a dump)."""
    try:
        lines = [ln for ln in (_resume_run_line(surface),
                               _resume_brainstorm_line(surface)) if ln]
        return "\n".join(lines) if lines else None
    except (OSError, ValueError, KeyError, AttributeError):
        # D5 — the real raisers behind `list_sessions` / `restore_brainstorm_progress`: an
        # unreadable state dir (OSError), a torn JSON checkpoint (ValueError), a checkpoint from an
        # older shape missing a key (KeyError), and a duck-typed/absent state store (AttributeError,
        # which the docstring's "a corrupt checkpoint" case genuinely produces here). Unchanged
        # behaviour — None, no noise — for every one of them.
        return None


# F6 — the briefing leads with a deterministic, cache-stable prefix (manifest identity +
# always-on rules + constitution) so a prompt cache hits on it; the live, per-run content
# (capability resolution, captured rules) follows after this boundary. Keeping the volatile
# part below the prefix is what lets the cache keep hitting across sessions.
_LIVE_BOUNDARY = "\n\n=== session (live) ===\n\n"


def build_bootstrap(
    surface: Surface, budget: int = BOOTSTRAP_TOKEN_BUDGET
) -> BootstrapResult:
    # Local import: govern imports bootstrap.estimate_tokens, so importing it at module
    # top would be circular. Resolved at call time, after both modules are loaded.
    from .govern import stable_prefix_for
    prefix = stable_prefix_for(surface).text()      # F6: byte-stable across runs
    text = prefix + _LIVE_BOUNDARY + _render(surface)
    tokens = estimate_tokens(text)

    # MS.S2 — SessionStart is a window's natural birth; register it in the live-session registry so
    # `mokata windows` can see it. Transient registry upkeep (ungated), degrade-clean, and does NOT
    # touch the briefing text — a registry hiccup must never affect the bootstrap output/budget.
    try:
        from .session_registry import touch as _touch_registry
        _touch_registry(surface, phase="session-start")
    except Exception:
        pass

    # WT.S1 — if another mokata window is already live on this repo, append a ONE-TIME human-gated
    # worktree offer to the live section (never creates anything). Single-window sessions get no
    # offer, so their briefing is byte-identical. Degrade-clean; recomputes `tokens` when appended.
    try:
        from .session_worktree import offer_text_once
        offer = offer_text_once(surface)
        if offer:
            text = text.rstrip("\n") + "\n" + offer + "\n"
            tokens = estimate_tokens(text)
    except (ImportError, OSError):
        # D5 — `offer_text_once` is already degrade-clean (it returns None on any registry
        # problem); what can still reach here is a half-installed package (ImportError) or an
        # unreadable registry path under `repo_identity` (OSError). The offer is an OFFER — its
        # absence costs nothing and must never break the briefing, so the swallow stays.
        pass

    # B-SKILLS — if THIS root has no mokata skills/commands wired (a worktree / fresh checkout /
    # never-set-up root — the new-session repro), append a ONE-LINE human-gated offer so the
    # session SAYS why the `/` menu is empty and names the fix. Never writes; a root that IS wired
    # gets no offer, so its briefing is byte-identical. Local FS reads only — no subprocess, no
    # handshake — so the async SessionStart path is never blocked/slowed. Degrade-clean.
    try:
        from .skills_visibility import briefing_offer
        offer = briefing_offer(surface.root)
        if offer:
            text = text.rstrip("\n") + "\n" + offer + "\n"
            tokens = estimate_tokens(text)
    except Exception:
        # A briefing offer is an OFFER — its absence costs nothing and must never break the
        # briefing. The check itself never raises; this guards a half-installed package.
        pass

    if tokens > budget:
        # Defensive truncation: keep the briefing inside budget no matter what, and
        # say so plainly rather than silently dropping context. Guaranteed to fit even
        # when the notice itself is larger than a (pathologically tiny) budget.
        max_chars = budget * 4
        notice = "\n[bootstrap truncated to fit the token budget]\n"
        if len(notice) >= max_chars:
            text = notice[:max_chars]
        else:
            text = text[: max_chars - len(notice)] + notice
        tokens = estimate_tokens(text)

    return BootstrapResult(text=text, token_estimate=tokens, budget=budget)


# ==========================================================================================
# H-1a — the PER-TURN recall injection pack (`UserPromptSubmit`)
# ==========================================================================================
# The briefing answers "what project am I in"; this answers "what does THIS turn need to know".
# Memory stops being a once-per-session briefing and becomes per-turn inbuilt RAG.
#
# Three deliberate limits, each of which is a decision rather than an omission:
#
#   * ANCHOR-FREE. Items only — no code anchors, no graph handles. Binding the injection to the
#     code graph is H-6's job, and inheriting that coupling here would create it by accident
#     instead of by decision.
#
#   * LEXICAL FLOOR ONLY. `jit_recall` + `always_on_lines`, never `store.recall_relevant` — and
#     the reason is not the ranking, it is the WRITE. `recall_relevant` STAMPS the hits it
#     returns (`record_usage`, DB.S5's recency/usage telemetry), so routing a per-turn injection
#     through it would perform a durable write on EVERY turn, which is precisely the defect
#     H-1a C1 just fixed one layer down. The wider tiered retrieval (semantic + graph + K1 edge
#     expansion) is genuinely better ranking and is worth having here — the route is a
#     `recall_relevant(stamp=False)` seam, FILED (doc 84) rather than improvised now, because it
#     needs the stamp to become optional at the store rather than suppressed at the caller.
#
#   * WHOLE LINES. When the pack is over budget, whole lines are dropped lowest-relevance-first
#     and the drop is DECLARED (`+N more`). A half-sentence of a guardrail is worse than an
#     absent one: it reads as complete, and nobody can tell it was cut.


@dataclass
class InjectionResult:
    """The per-turn pack, plus what it cost and what it left out.

    `item_ids` is what was actually injected (in emitted order) — the S4 dedup ledger keys on it,
    and it is also what makes "was this item injected this turn" answerable rather than inferred
    from the rendered text."""

    text: str
    token_estimate: int
    budget: int
    rules_shown: int = 0
    items_shown: int = 0
    dropped: int = 0
    item_ids: List[str] = None          # noqa: RUF012 - populated in __post_init__

    def __post_init__(self) -> None:
        if self.item_ids is None:
            self.item_ids = []

    @property
    def within_budget(self) -> bool:
        return self.token_estimate <= self.budget


_INJECTION_HEADER = "mokata memory — relevant to this turn:"


def _dropped_notice(n: int) -> str:
    return (f"- (+{n} more relevant item(s) not shown — over the per-turn injection budget; "
            f"ask, or run `mokata memory`)")


def _fit_to_budget(header: str, reserved: List[str], ranked: List[str], budget: int):
    """Assemble `header` + `reserved` + `ranked` into at most `budget` tokens, dropping WHOLE
    lines lowest-relevance-first and DECLARING every line dropped.

    Returns `(text, kept_reserved, kept_ranked, dropped)`. Two rules decide the drop order:

      * the ranked JIT tail goes first, and `jit_recall` already returned it sorted by relevance
        descending — so "drop from the end" IS "drop the least relevant";
      * `reserved` (the always-on rules) is the floor and is only touched once `ranked` is empty.
        A rule the turn must not violate outranks a context item that merely might help.

    `dropped` counts EVERYTHING omitted, reserved lines included. Counting only the JIT tail
    would print "+5 more" on a turn that also silently dropped four guardrails — an honest-looking
    number that is wrong in the one direction that matters.

    The `+N more` notice COSTS tokens, so it is inside the fit test rather than appended after it;
    appending afterwards is how an "enforced" budget ends up exceeded by exactly one line."""
    def _render(keep_reserved: int, keep_ranked: int, dropped: int) -> str:
        lines = [header] + reserved[:keep_reserved] + ranked[:keep_ranked]
        if dropped:
            lines.append(_dropped_notice(dropped))
        return "\n".join(lines) + "\n"

    total = len(reserved) + len(ranked)
    # 1 — drop the ranked tail, lowest relevance first; the reserved slice is untouched.
    for keep in range(len(ranked), -1, -1):
        text = _render(len(reserved), keep, total - len(reserved) - keep)
        if estimate_tokens(text) <= budget:
            return text, len(reserved), keep, total - len(reserved) - keep
    # 2 — the reserved always-on slice is over budget on its own. Give up slots there too,
    # rather than emit something that silently exceeds the number this module publishes.
    for keep_reserved in range(len(reserved) - 1, -1, -1):
        text = _render(keep_reserved, 0, total - keep_reserved)
        if estimate_tokens(text) <= budget:
            return text, keep_reserved, 0, total - keep_reserved
    return "", 0, 0, total


def build_injection(surface: Surface, query: str,
                    budget: int = INJECTION_TOKEN_BUDGET,
                    exclude_ids: "Optional[set]" = None) -> InjectionResult:
    """The per-turn pack for `query`, guaranteed to fit `budget`.

    Never raises: every failure (an unreadable store, a half-installed package, a store that
    cannot be constructed) yields an EMPTY pack, and the hook emits nothing at all. This is the
    async context-injection lane — it only ever adds context, so having nothing to add is a
    perfectly ordinary outcome and not a condition worth announcing every turn.

    `exclude_ids` is the S4 already-injected set (repeating an item the model was handed three
    turns ago spends the budget on nothing)."""
    if not query or budget <= 0:
        return InjectionResult(text="", token_estimate=0, budget=budget)

    exclude = exclude_ids or set()
    try:
        from .memory import MemoryStore, always_on_lines, jit_recall, render_item_line
        store = MemoryStore.from_surface(surface)
        # Both reads are NON-COUNTING and scope/readability filtered (H-1a C1) — a per-turn
        # injection must move no durable state and must never surface a teammate's private item.
        reserved, _overflow = always_on_lines(store, INJECTION_RULES_MAX_LINES, query=query)
        # `exclude_ids` goes INTO the recall, not around it: filtering the RESULT would leave the
        # already-seen items occupying the top k, so a session would fall silent while unseen
        # items sat at rank k+1. The whole point of the ledger is to promote what is new.
        # The always-on slice is deliberately NOT deduped — a guardrail is not "already known"
        # because it scrolled out of the window.
        hits = jit_recall(store, query, top_k=INJECTION_JIT_TOP_K, exclude_ids=exclude)
    except Exception:  # noqa: BLE001
        # (iv) SUPPRESS-OK, and the one place the usual "announce the degrade" answer is wrong:
        # see the D5 register entry for `hook_cli.user_prompt_submit_main`. This runs on EVERY
        # prompt, and what it would announce is that one turn got less context than it could
        # have — never that a guarantee was broken. The rules are still enforced by the gates and
        # the always-on set still reached the SessionStart briefing, which DOES announce a broken
        # memory read loudly (`_briefing_rule_lines`). Saying it again every turn buys nothing
        # and trains the user to ignore the channel.
        return InjectionResult(text="", token_estimate=0, budget=budget)

    ranked = [render_item_line(i) for i in hits]
    if not reserved and not ranked:
        return InjectionResult(text="", token_estimate=0, budget=budget)

    text, kept_rules, kept, dropped = _fit_to_budget(_INJECTION_HEADER, reserved, ranked, budget)

    # The closing ARITHMETIC backstop. `_fit_to_budget` drops whole lines and can therefore fail
    # in one shape it cannot fix: a single line longer than the entire budget (a very long rule,
    # a pathologically small budget). `cap_summary` cannot fail in that shape — it truncates to
    # `budget * 4` chars and marks the cut — so the published number holds unconditionally rather
    # than "unless someone captured a 400-token guardrail".
    from .govern.compaction import cap_summary
    handback = cap_summary(text, budget)
    return InjectionResult(text=handback.summary, token_estimate=handback.tokens, budget=budget,
                           rules_shown=kept_rules, items_shown=kept, dropped=dropped,
                           item_ids=[i.id for i in hits[:kept]])
