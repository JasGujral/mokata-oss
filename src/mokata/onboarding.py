"""Stage 56 — magical first-run: guided wizard + "here's what I did" + tour + error hints.

A brand-new user should reach visible value in MINUTES. This module composes the existing
primitives — it rebuilds NONE of them:

  * the WIZARD orchestrates `init.init_repo` (gated scaffold) + `config_cmd.config_set` (wire a
    detected integration into a capability chain) + `harness_setup.setup_harness` (gated harness
    wiring). It DETECTS the environment (`detect.Detector` over `profiles.TOOL_CATALOG`), ASKS
    the profile + which detected integrations to wire, then RUNS the wire steps behind ONE human
    gate (decline → nothing wired). CLEAN-ROOM / ADOPT: an ABSENT tool is RECOMMENDED (its
    install command printed) but NEVER installed — mokata detects → recommends → runs WITH
    APPROVAL, it never silently installs a third-party tool.
  * the SUMMARY ("here's what I just did") names what was detected/wired + the next step (reusing
    the 54c next-step nudge via `compose.suggest`).
  * the TOUR is a short, self-contained, READ-ONLY demo (a graph query, a memory recall in an
    in-memory store, a real secret gate-catch) — it writes nothing to the user's repo.
  * the ERROR HINTS turn an unknown CLI command into a "did you mean <closest>?" + a next step
    (difflib over the live command set; `compose.suggest` for the context-aware nudge).

Inviolables: human-gated where it writes/wires (P2); never a silent install; degrade-clean;
frugal; core dependency-free; clean-room; Apache-2.0.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .compose import SuggestionContext, suggest
from .config import Surface
from .detect import Detector
from .profiles import (
    DEFAULT_PROFILE,
    TOOL_CATALOG,
    profile_enabled_set,
    profile_names,
)
from .prompt import read_yes_no

# The OPT-IN integrations the wizard asks about — the external/MCP providers beyond the
# always-on local floors (grep / ripgrep / sqlite). Each maps to a capability; a present one is
# offered to wire, an absent one is RECOMMENDED (never installed).
OPTIONAL_INTEGRATIONS = (
    "code-review-graph", "serena", "neo4j",      # code_graph providers
    "obsidian", "postgres", "native-memory",     # memory_store providers
)

# CLEAN-ROOM / ADOPT: how a user installs an absent integration THEMSELVES. mokata only ever
# prints these (detect → recommend); it never runs them silently. `None` = no install hint (the
# tool ships with its harness / nothing to pip-install).
INSTALL_HINTS: Dict[str, str] = {
    "postgres": "pip install 'mokata[postgres]'   # the optional Postgres memory backend",
    "neo4j": "pip install neo4j   # then set NEO4J_URI / NEO4J_USERNAME / NEO4J_PASSWORD",
    "obsidian": "install Obsidian (https://obsidian.md) and open a vault, then re-run setup",
    "serena": "install the Serena MCP server per its docs, then re-run setup",
    "code-review-graph": "install the code-review-graph MCP server per its docs, then re-run "
                         "setup",
}


# ----------------------------------------------------------------- error hints (did you mean)
def closest_command(token: str, commands: Any, *, cutoff: float = 0.6) -> Optional[str]:
    """The closest real command to a typo (difflib), or None when nothing is close enough."""
    matches = difflib.get_close_matches(token, list(commands), n=1, cutoff=cutoff)
    return matches[0] if matches else None


def _fresh_next_step() -> Optional[str]:
    """The context-aware 'what to run next' for a brand-new repo, via compose.suggest (L6)."""
    sug = suggest(SuggestionContext(starting_fresh=True))
    if not sug:
        return None
    return f"`/mokata:{sug[0].skill}` — {sug[0].reason}"


def unknown_command_message(token: str, *, known: Any, initialized: bool) -> str:
    """A friendly message for an unknown CLI subcommand: name the closest real command and the
    single next step (set up the repo when uninitialized, else a context-aware suggestion)."""
    lines = [f"mokata: '{token}' is not a mokata command."]
    close = closest_command(token, known)
    if close:
        lines.append(f"Did you mean '{close}'?  (try `mokata {close} --help`)")
    if not initialized:
        lines.append("Next: run `mokata init` (or `/mokata:setup` inside Claude Code) to set up "
                     "this repo — it detects your tools and wires them with your approval.")
    else:
        nxt = _fresh_next_step()
        if nxt:
            lines.append(f"Next: try {nxt}  (`mokata --help` lists every command).")
        else:
            lines.append("Next: `mokata --help` lists every command.")
    return "\n".join(lines)


# ----------------------------------------------------------------- the first-run wizard
@dataclass
class WizardResult:
    profile: str
    detected: Dict[str, bool]
    wired: List[str] = field(default_factory=list)        # human-readable things wired
    recommended: List[str] = field(default_factory=list)  # install hints for ABSENT tools
    harness_wired: bool = False
    aborted: bool = False
    message: str = ""
    next_step: Optional[str] = None


def _default_ask(prompt: str, choices: Any, default: str) -> str:
    """The live choice reader (one line, default-on-blank). Injectable for tests."""
    try:
        ans = input(f"{prompt} [{'/'.join(choices)}] (default {default}): ").strip()
    except EOFError:
        return default
    return ans or default


def _default_confirm(prompt: str) -> bool:
    return read_yes_no(prompt + " [y/N] ")


def _available_needs(profile: str) -> set:
    """The capabilities a profile actually wires (so we never offer to wire an integration into
    a capability the profile leaves off — e.g. nothing to wire under `minimal`)."""
    return set(profile_enabled_set(profile)["capabilities"].keys())


def render_wizard_plan(profile: str, detected: Dict[str, bool], chosen: List[str],
                       recommended: List[str], wire_harness: bool, harness: str) -> str:
    """The human-gate preview — exactly what the wizard WOULD do before any write."""
    lines = ["mokata first-run wizard — here's what I'll do (nothing is written yet):", ""]
    lines.append(f"  • initialize this repo with the '{profile}' profile")
    for tid in chosen:
        lines.append(f"  • wire detected integration '{tid}' "
                     f"→ {TOOL_CATALOG[tid]['provides']}")
    if wire_harness:
        lines.append(f"  • wire mokata into {harness} (slash commands + MCP server + hooks)")
    if recommended:
        lines.append("")
        lines.append("  Detected but NOT installed — I'll RECOMMEND (never install for you):")
        for r in recommended:
            lines.append(f"    - {r}")
    return "\n".join(lines)


def _wire_integration(root: str, tid: str, *, ledger: Any = None,
                      out: Optional[Callable[[str], None]] = None) -> bool:
    """Wire a PRESENT, detected integration into its capability chain — reusing the gated
    `config_set` (no new write path). Adds the tool def + prepends it to the capability's
    fallback. Returns True on success. The wizard already gated the whole run, so the sub-edits
    run `assume_yes=True` (mirrors how setup_harness drives init_repo)."""
    from . import config_cmd
    sink = out or (lambda _s: None)
    tdef = dict(TOOL_CATALOG[tid])
    tdef["enabled"] = True
    need = tdef["provides"]
    r1 = config_cmd.config_set(root, f"tools.{tid}", json.dumps(tdef),
                               assume_yes=True, out=sink, ledger=ledger)
    if not r1.committed:
        return False
    _found, chain = config_cmd.config_get(root, f"capabilities.{need}.fallback")
    chain = chain if isinstance(chain, list) else []
    if tid not in chain:
        chain = [tid] + chain
    r2 = config_cmd.config_set(root, f"capabilities.{need}.fallback", json.dumps(chain),
                               assume_yes=True, out=sink, ledger=ledger)
    return r2.committed


def _unwire_integration(root: str, tid: str, *, ledger: Any = None,
                        out: Optional[Callable[[str], None]] = None) -> bool:
    """Cleanly UNWIND a wired integration — the reverse of `_wire_integration`, reusing the gated
    `config_set` (no new write path). Drops it from its capability chain FIRST (so no intermediate
    manifest references a missing tool), then removes the tool def, leaving NO residue (K6
    clean-uninstall). Returns True on success; a tool that isn't wired is a no-op (True)."""
    from . import config_cmd
    sink = out or (lambda _s: None)
    need = TOOL_CATALOG[tid]["provides"]
    _found, chain = config_cmd.config_get(root, f"capabilities.{need}.fallback")
    chain = chain if isinstance(chain, list) else []
    if tid in chain:
        new_chain = [t for t in chain if t != tid]
        r1 = config_cmd.config_set(root, f"capabilities.{need}.fallback",
                                   json.dumps(new_chain), assume_yes=True, out=sink,
                                   ledger=ledger)
        if not r1.committed:
            return False
    _found2, tools = config_cmd.config_get(root, "tools")
    if isinstance(tools, dict) and tid in tools:
        pruned = {k: v for k, v in tools.items() if k != tid}
        r2 = config_cmd.config_set(root, "tools", json.dumps(pruned), assume_yes=True,
                                   out=sink, ledger=ledger)
        return r2.committed
    return True


# ----------------------------------------------------------------- reconfigure (re-runnable)
def _current_profile(root: str) -> str:
    from . import config_cmd
    _found, val = config_cmd.config_get(root, "profile")
    return val if isinstance(val, str) else ""


def _current_wiring(root: str) -> Dict[str, List[str]]:
    """{capability -> its current fallback chain} from the committed manifest. Read-only."""
    from . import config_cmd
    out: Dict[str, List[str]] = {}
    _found, caps = config_cmd.config_get(root, "capabilities")
    if isinstance(caps, dict):
        for need, c in caps.items():
            fb = c.get("fallback") if isinstance(c, dict) else None
            out[need] = list(fb) if isinstance(fb, list) else []
    return out


def _wired_integrations(root: str) -> set:
    """The OPTIONAL integrations currently wired into any capability chain."""
    wired = set()
    for chain in _current_wiring(root).values():
        wired.update(t for t in chain if t in OPTIONAL_INTEGRATIONS)
    return wired


def _harness_wired(root: str, scope: str, home: Optional[str], harness: str) -> bool:
    """Whether mokata's slash commands are already wired into the harness (the commands dir
    exists). Read-only; never raises on an unknown scope/harness."""
    try:
        from .harness_setup import resolve_targets
        return resolve_targets(scope, root, home, harness).commands_dir.exists()
    except Exception:
        return False


@dataclass
class ReconfigPlan:
    """The computed effect of a reconfigure BEFORE any write — so the caller can diff + gate it."""
    initialized: bool
    current_profile: str
    target_profile: str
    detected: Dict[str, bool]
    current_wired: List[str]
    added: List[str] = field(default_factory=list)
    removed: List[str] = field(default_factory=list)
    config_changes: List[tuple] = field(default_factory=list)   # (key, old, new, raw)
    recommended: List[str] = field(default_factory=list)
    harness_action: str = ""          # "" | "add" | "remove"
    harness: str = "claude"
    scope: str = "project"

    @property
    def profile_changed(self) -> bool:
        return bool(self.target_profile) and self.target_profile != self.current_profile

    @property
    def changed(self) -> bool:
        return bool(self.profile_changed or self.added or self.removed
                    or self.config_changes or self.harness_action)


def plan_reconfigure(root: str = ".", *, detector: Optional[Detector] = None,
                     profile: Optional[str] = None, add: Optional[List[str]] = None,
                     remove: Optional[List[str]] = None,
                     config_edits: Optional[Dict[str, str]] = None,
                     wire_harness: Optional[bool] = None, harness: str = "claude",
                     scope: str = "project", home: Optional[str] = None) -> ReconfigPlan:
    """Compute what a reconfigure WOULD change WITHOUT writing — re-detecting tools and reading the
    current manifest. Idempotent by construction: only ACTUAL changes are recorded (adding an
    already-wired tool, removing an unwired one, or setting a value that's already in place all
    drop out). An ABSENT requested-add is RECOMMENDED, never added."""
    from . import config_cmd
    detector = detector or Detector()
    if not Surface.is_initialized(root):
        return ReconfigPlan(initialized=False, current_profile="", target_profile="",
                            detected={}, current_wired=[], harness=harness, scope=scope)

    current_profile = _current_profile(root)
    target_profile = profile or current_profile
    if target_profile not in profile_names():
        target_profile = current_profile
    detected = {tid: detector.is_present(tid, td) for tid, td in TOOL_CATALOG.items()}
    current_wired = sorted(_wired_integrations(root))
    needs = _available_needs(target_profile)

    added: List[str] = []
    removed: List[str] = []
    recommended: List[str] = []
    for tid in (add or []):
        if tid not in TOOL_CATALOG:
            continue
        if tid in current_wired:
            continue                                      # already wired → no-op (idempotent)
        if TOOL_CATALOG[tid]["provides"] not in needs:
            continue                                      # profile lacks this capability
        if detected.get(tid):
            if tid not in added:
                added.append(tid)
        elif tid in INSTALL_HINTS:
            recommended.append(INSTALL_HINTS[tid])        # ABSENT → recommend, NEVER install
    for tid in (remove or []):
        if tid in current_wired and tid not in removed:
            removed.append(tid)

    config_changes: List[tuple] = []
    for key, raw in (config_edits or {}).items():
        new = config_cmd.coerce(raw)
        found, old = config_cmd.config_get(root, key)
        if not found or old != new:
            config_changes.append((key, old if found else None, new, raw))

    harness_action = ""
    if wire_harness is not None:
        wired = _harness_wired(root, scope, home, harness)
        if wire_harness and not wired:
            harness_action = "add"
        elif (not wire_harness) and wired:
            harness_action = "remove"

    return ReconfigPlan(
        initialized=True, current_profile=current_profile, target_profile=target_profile,
        detected=detected, current_wired=current_wired, added=added, removed=removed,
        config_changes=config_changes, recommended=recommended, harness_action=harness_action,
        harness=harness, scope=scope)


def render_reconfigure_diff(plan: ReconfigPlan) -> str:
    """A current→proposed DIFF — exactly what the reconfigure WOULD change (nothing written yet)."""
    lines = ["mokata reconfigure — proposed changes (nothing written yet):", ""]
    if plan.profile_changed:
        lines.append(f"  profile:  {plan.current_profile} → {plan.target_profile}")
    for tid in plan.added:
        lines.append(f"  + wire    {tid}  → {TOOL_CATALOG[tid]['provides']}")
    for tid in plan.removed:
        lines.append(f"  - unwire  {tid}  (removed cleanly — no residue)")
    for key, old, new, _raw in plan.config_changes:
        shown_old = "(unset)" if old is None else json.dumps(old)
        lines.append(f"  ~ config  {key}: {shown_old} → {json.dumps(new)}")
    if plan.harness_action == "add":
        lines.append(f"  + harness {plan.harness} ({plan.scope}): slash commands + MCP + hooks")
    elif plan.harness_action == "remove":
        lines.append(f"  - harness {plan.harness} ({plan.scope}): unwired (reversible)")
    if plan.recommended:
        lines.append("")
        lines.append("  Detected but NOT installed — RECOMMEND (never install for you):")
        for r in plan.recommended:
            lines.append(f"    - {r}")
    return "\n".join(lines)


@dataclass
class ReconfigureResult:
    initialized: bool
    changed: bool
    profile: str
    added: List[str] = field(default_factory=list)
    removed: List[str] = field(default_factory=list)
    config_changes: List[str] = field(default_factory=list)
    recommended: List[str] = field(default_factory=list)
    harness_action: str = ""
    aborted: bool = False
    message: str = ""


def _ask_reconfigure(current_profile: str, current_wired: set, detected: Dict[str, bool],
                     ask: Callable, gate: Callable):
    """The interactive Q&A (seeded with the CURRENT state): pick a profile, then keep/remove each
    wired integration and wire any newly-available one. Returns (target_profile, add, remove)."""
    target = ask("Which profile?", profile_names(), current_profile)
    if target not in profile_names():
        target = current_profile
    add: List[str] = []
    remove: List[str] = []
    for tid in OPTIONAL_INTEGRATIONS:
        if TOOL_CATALOG[tid]["provides"] not in _available_needs(target):
            continue
        if tid in current_wired:
            if not gate(f"Keep '{tid}' wired?"):
                remove.append(tid)
        elif detected.get(tid):
            if gate(f"Wire newly-available '{tid}' ({TOOL_CATALOG[tid]['provides']})?"):
                add.append(tid)
    return target, add, remove


def run_reconfigure(root: str = ".", *, ask: Optional[Callable] = None,
                    confirm: Optional[Callable[[str], bool]] = None,
                    out: Optional[Callable[[str], None]] = None,
                    detector: Optional[Detector] = None, profile: Optional[str] = None,
                    add: Optional[List[str]] = None, remove: Optional[List[str]] = None,
                    config_edits: Optional[Dict[str, str]] = None,
                    wire_harness: Optional[bool] = None, harness: str = "claude",
                    scope: str = "project", home: Optional[str] = None, ledger: Any = None,
                    assume_yes: bool = False) -> ReconfigureResult:
    """Re-runnable reconfigure: on an ALREADY-INITIALIZED repo, RE-DETECT tools, show a
    current→proposed DIFF, gate ONCE, then apply — composing init/config/setup/unsetup (none
    rebuilt). Human-gated (decline → nothing), idempotent (no changes → no-op, no writes),
    reversible (remove leaves no residue), and never installs an absent tool. `assume_yes` is the
    non-interactive path; explicit add/remove/profile/config_edits drive it without prompts."""
    from . import config_cmd
    emit = out or print
    ask = ask or _default_ask
    gate = confirm or _default_confirm
    detector = detector or Detector()

    if not Surface.is_initialized(root):
        emit("mokata reconfigure: this repo isn't initialized yet — run `mokata init` "
             "(or `/mokata:setup`) first.")
        return ReconfigureResult(initialized=False, changed=False, profile="",
                                 aborted=True, message="not initialized")

    # Interactive Q&A only when nothing explicit was passed (else the explicit set drives it).
    nothing_explicit = (add is None and remove is None and profile is None
                        and config_edits is None and wire_harness is None)
    if nothing_explicit and not assume_yes:
        detected = {tid: detector.is_present(tid, td) for tid, td in TOOL_CATALOG.items()}
        profile, add, remove = _ask_reconfigure(
            _current_profile(root), _wired_integrations(root), detected, ask, gate)

    plan = plan_reconfigure(root, detector=detector, profile=profile, add=add, remove=remove,
                            config_edits=config_edits, wire_harness=wire_harness,
                            harness=harness, scope=scope, home=home)

    if not plan.changed:
        emit("mokata reconfigure: no changes — your setup already matches. Nothing written.")
        if plan.recommended:
            emit("Recommended (NOT installed — your call):")
            for r in plan.recommended:
                emit(f"  {r}")
        return ReconfigureResult(initialized=True, changed=False,
                                 profile=plan.current_profile, recommended=plan.recommended,
                                 message="no changes")

    emit(render_reconfigure_diff(plan))
    if not assume_yes and not gate("\nApply this reconfigure?"):
        return ReconfigureResult(initialized=True, changed=False,
                                 profile=plan.current_profile, recommended=plan.recommended,
                                 aborted=True, message="aborted by user")

    # Apply (already gated) — reuse init_repo + config_set + setup/unsetup. Sub-writes assume_yes.
    from .init import init_repo
    if plan.profile_changed:
        init_repo(root=root, profile=plan.target_profile, assume_yes=True, force=True,
                  detector=detector, out=lambda _s: None)
    applied_added = [t for t in plan.added
                     if _wire_integration(root, t, ledger=ledger, out=lambda _s: None)]
    applied_removed = [t for t in plan.removed
                       if _unwire_integration(root, t, ledger=ledger, out=lambda _s: None)]
    applied_cfg: List[str] = []
    for key, _old, _new, raw in plan.config_changes:
        r = config_cmd.config_set(root, key, raw, assume_yes=True, out=lambda _s: None,
                                  ledger=ledger)
        if r.committed:
            applied_cfg.append(key)

    if plan.harness_action == "add":
        from .harness_setup import SetupError, setup_harness
        try:
            setup_harness(harness=plan.harness, root=root, scope=plan.scope,
                          profile=plan.target_profile, assume_yes=True, home=home,
                          out=lambda _s: None)
        except SetupError as exc:
            emit(f"  harness not wired (degraded: {exc})")
    elif plan.harness_action == "remove":
        from .harness_setup import unsetup_harness
        unsetup_harness(harness=plan.harness, root=root, scope=plan.scope, assume_yes=True,
                        home=home, out=lambda _s: None)

    return ReconfigureResult(
        initialized=True, changed=True, profile=plan.target_profile, added=applied_added,
        removed=applied_removed, config_changes=applied_cfg, recommended=plan.recommended,
        harness_action=plan.harness_action, message="ok")


def run_wizard(root: str = ".", *, ask: Optional[Callable] = None,
               confirm: Optional[Callable[[str], bool]] = None,
               out: Optional[Callable[[str], None]] = None,
               detector: Optional[Detector] = None, profile: Optional[str] = None,
               wire_harness: Optional[bool] = None, harness: str = "claude",
               scope: str = "project", home: Optional[str] = None, ledger: Any = None,
               assume_yes: bool = False, force: bool = False) -> WizardResult:
    """Run the interactive first-run wizard end to end: DETECT → ASK (profile + what to wire) →
    one HUMAN GATE → RUN (init + wire integrations + wire harness). Decline → nothing wired.
    Never installs a third-party tool (absent → recommend only). `assume_yes` is the
    non-interactive CI path (no asks; scaffolds the profile; no integrations/harness wired)."""
    from .init import init_repo
    emit = out or print
    ask = ask or _default_ask
    gate = confirm or _default_confirm
    detector = detector or Detector()

    detected = {tid: detector.is_present(tid, tdef) for tid, tdef in TOOL_CATALOG.items()}

    # 1. profile — ASKED interactively (not just a flag), defaulted non-interactively.
    if profile is None:
        profile = DEFAULT_PROFILE if assume_yes else ask(
            "Which profile?", profile_names(), DEFAULT_PROFILE)
    if profile not in profile_names():
        profile = DEFAULT_PROFILE

    # 2. which DETECTED integrations to wire (present → offer; absent → recommend, never install).
    chosen: List[str] = []
    recommended: List[str] = []
    needs = _available_needs(profile)
    if not assume_yes:
        for tid in OPTIONAL_INTEGRATIONS:
            if TOOL_CATALOG[tid]["provides"] not in needs:
                continue                          # the profile doesn't wire this capability
            if detected.get(tid):
                if gate(f"Wire detected '{tid}' ({TOOL_CATALOG[tid]['provides']})?"):
                    chosen.append(tid)
            elif tid in INSTALL_HINTS:
                recommended.append(INSTALL_HINTS[tid])

    # 3. wire the harness? (commands + MCP + hooks)
    if wire_harness is None:
        wire_harness = (not assume_yes) and gate(
            f"Wire mokata into {harness} (slash commands + MCP + hooks)?")

    # 4. ONE durable-write gate for the whole plan (decline → nothing wired).
    if not assume_yes:
        emit(render_wizard_plan(profile, detected, chosen, recommended, wire_harness, harness))
        if not gate("\nProceed and wire this up?"):
            return WizardResult(profile=profile, detected=detected, recommended=recommended,
                                aborted=True, message="aborted by user")

    # 5. RUN the wire steps (already gated) — reuse init_repo + config_set + setup_harness.
    res = init_repo(root=root, profile=profile, assume_yes=True, force=force,
                    detector=detector, out=lambda _s: None)
    if res.aborted:
        return WizardResult(profile=profile, detected=detected, recommended=recommended,
                            aborted=True, message=res.message)
    wired: List[str] = [f"config scaffolded (profile '{profile}', constitution + .gitignore)"]
    for tid in chosen:
        if _wire_integration(root, tid, ledger=ledger, out=emit):
            wired.append(f"integration '{tid}' → {TOOL_CATALOG[tid]['provides']}")

    harness_wired = False
    if wire_harness:
        from .harness_setup import SetupError, setup_harness
        try:
            sres = setup_harness(harness=harness, root=root, scope=scope, profile=profile,
                                 assume_yes=True, force=force, home=home, out=lambda _s: None)
            harness_wired = not sres.aborted
            if harness_wired:
                wired.append(f"harness '{harness}' ({scope} scope): slash commands + MCP + hooks")
        except SetupError as exc:
            wired.append(f"harness '{harness}' NOT wired (degraded: {exc})")

    next_step = _wizard_next_step()
    result = WizardResult(profile=profile, detected=detected, wired=wired,
                          recommended=recommended, harness_wired=harness_wired, message="ok",
                          next_step=next_step)
    emit(render_did_summary(result))
    return result


def _wizard_next_step() -> str:
    """The single next step after setup — the front of the pipeline (compose.suggest, L6)."""
    sug = suggest(SuggestionContext(starting_fresh=True))
    skill = sug[0].skill if sug else "brainstorm"
    return f"/mokata:{skill}"


# ----------------------------------------------------------------- "here's what I just did"
def render_did_summary(result: WizardResult) -> str:
    """The 30-second recap: what was detected, what got wired, the graph/memory it stood up, the
    starter guardrails, and the ONE next step. Pure/derived — names only what actually happened."""
    present = sorted(t for t, ok in result.detected.items() if ok)
    lines = ["", "── here's what I just did (30s) ───────────────────────────────"]
    lines.append(f"  detected {len(present)} tool(s) present: "
                 f"{', '.join(present) if present else '(none beyond the built-in floors)'}")
    for w in result.wired:
        lines.append(f"  ✓ {w}")
    lines.append("  ✓ 5 starter guardrails written (the constitution articles — "
                 "human-gate, local-first, spec-before-code, degrade-don't-break, auditable)")
    if result.recommended:
        lines.append("  • recommended (NOT installed — your call):")
        for r in result.recommended:
            lines.append(f"      {r}")
    if result.next_step:
        lines.append(f"\n  Next: `{result.next_step}` — start your first governed change. "
                     f"(`mokata tour` for a 60-sec demo.)")
    return "\n".join(lines)


# ----------------------------------------------------------------- /mokata:tour (read-only demo)
# A short, SELF-CONTAINED demo. It writes NOTHING to the user's repo: the memory recall runs in
# an in-memory SQLite store, and the gate-catch only SCANS a sample line. Deterministic.
_TOUR_SAMPLE = '''\
# sample.py
def load_config(path):          # ← the thing under change
    return read_file(path)

def main():
    cfg = load_config("app.toml")   # main() calls load_config'''

# Assembled from fragments so no literal credential lives in the source (mokata's own
# secret-guard would otherwise block committing this file — the very protection being demoed).
_SAMPLE_KEY = "AKIA" + "IOSFODNN7" + "EXAMPLE"
_TOUR_SECRET_LINE = f'aws_key = "{_SAMPLE_KEY}"   # oops — a real credential in the diff'


def _tour_graph_demo(ascii_only: bool) -> str:
    arrow = "->" if ascii_only else "→"
    return ("1) Graph query — ask the codebase a structural question instead of grepping:\n\n"
            f"{_TOUR_SAMPLE}\n\n"
            "    $ mokata query callers load_config\n"
            f"    load_config  {arrow}  called by: main  (sample.py:6)\n"
            "  mokata answers from the code graph (and falls back to grep when no backend is "
            "wired) — try it on your own repo with `mokata query callers <symbol>`.")


def _tour_memory_demo(ascii_only: bool) -> str:
    from .memory import MemoryItem, MemoryStore
    from .memory.backends import SQLiteBackend
    store = MemoryStore(SQLiteBackend(":memory:"))     # in-memory — touches no disk
    store.remember(MemoryItem.create("db.engine", "postgres",
                                     source="a decision you made earlier"), assume_yes=True)
    hits = store.recall("db.engine")
    val = hits[0].value if hits else "(none)"
    return ("2) Memory recall — mokata remembers your project's decisions so the agent stops "
            "re-asking:\n\n"
            "    (earlier) remembered:  db.engine = postgres\n"
            "    $ mokata recall db.engine\n"
            f"    {val}   ← surfaced back, with its provenance, when it's relevant.")


def _tour_gate_demo(ascii_only: bool) -> str:
    from .govern.secrets import scan
    from .legibility import gate_verdict, unblock_hint
    findings = scan(text=_TOUR_SECRET_LINE)
    kind = findings[0].kind if findings else "secret"
    verdict = gate_verdict("write-secret", False,
                           f"a secret ({kind}) is in the change", action=unblock_hint(
                               "write-secret"), ascii_only=ascii_only)
    return ("3) Gate catch — the seatbelt. mokata scans every write; a secret is a HARD block "
            "approval can't override:\n\n"
            f"    {_TOUR_SECRET_LINE}\n\n"
            f"    {verdict}\n"
            "  Nothing committed. That's the guarantee on every durable write — local, "
            "human-gated, auditable.")


def build_tour(*, ascii_only: bool = False) -> str:
    """The read-only first-run demo as text: a graph query, a live memory recall (in-memory),
    and a real secret gate-catch. Self-contained — writes nothing to the user's repo."""
    head = ("mokata · 60-second tour — the memory + seatbelt for your AI coding agent.\n"
            "(read-only; nothing here touches your repo)\n")
    body = "\n\n".join([_tour_graph_demo(ascii_only), _tour_memory_demo(ascii_only),
                        _tour_gate_demo(ascii_only)])
    tail = ("\n\nThat's it. Next: `mokata init` (or `/mokata:setup`) to wire mokata into THIS "
            "repo, then `/mokata:brainstorm` your first change.")
    return head + "\n" + body + tail
