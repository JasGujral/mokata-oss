"""G1 — GRADUATED ADOPTION: `mokata init --mode {seatbelt,memory,full}`.

Bare Claude Code's onboarding is "read the docs". mokata's is three NAMED on-ramps, each
landing a visible win in under five minutes and printing the ONE command that proves it.

A mode is an **alias + an onboarding flavour**, never a second config axis (P8, and the
Two-modes-one-shape decision). It resolves to an EXISTING profile, and the PROFILE is the
only thing persisted: a manifest written by `--mode memory` is byte-identical to one written
by `--profile standard`. Nothing records the mode, because nothing later needs it — the
offers fire and the quickstart prints inside the same init.

**Why `seatbelt` maps to `standard`, not `minimal`** (grounded, not assumed). The seatbelt IS
the gates, and brainstorm's HARD-GATE is a two-lens gate whose Lens 1 is the blast radius from
`KnowledgeLayer.blast_radius()`. `settings.graph.required` is ON by default (GR.S3), so a
DEGRADED radius is REFUSED as decision input. Verified from code on one scratch repo, same
symbol, only the profile changed:

    --profile minimal   ->  blast_radius(beta) via grep [grep fallback]
                            "(lexical fallback (no structural graph; results are approximate))"
    --profile standard  ->  blast_radius(beta) via ast  [graph]

`minimal` disables the `knowledge` layer, so `code_graph` is dropped at resolution (K1) and the
radius falls to the lexical floor with `degraded=True` — which `govern.graph_required` then
refuses at `BrainstormSession.approve(graph_gate=...)`. A `seatbelt` mode on `minimal` would
therefore ship a mode whose headline command dead-ends on default settings. The gates consume
the AST floor, so seatbelt wires it. `seatbelt` and `memory` land the same profile and differ
in FLAVOUR: memory offers the semantic tier, seatbelt structurally never does.

All consent runs through the DB.S4 primitives (`extras_install.offer_embeddings`) and the
GR.S2 seam (`knowledge.graph_adopt.offer_graph_at_setup`) — zero new ask/install machinery, and
the interactive-only guarantee is preserved: `offer_mode_extras` is a no-op unless the caller
says the session is interactive, so a `--yes`/CI init can never reach `pip`.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class ModeSpec:
    """One named on-ramp: the profile it aliases, which offers it carries, its quickstart."""

    name: str
    profile: str
    summary: str
    offers_embeddings: bool
    offers_graph: bool
    quickstart: Tuple[str, ...]


# The three on-ramps. `profile` is the ONLY thing that reaches the manifest.
ADOPTION_MODES: Dict[str, ModeSpec] = {
    "seatbelt": ModeSpec(
        name="seatbelt",
        profile="standard",
        summary="just the gates (and the code graph they need to be real)",
        offers_embeddings=False,
        offers_graph=False,
        quickstart=(
            "── seatbelt mode: the gates are on ─────────────────────────────",
            "  You got: the spec-driven TDD engine, the governance gates (human-gated",
            "  writes, secret scanning, RED-before-GREEN), and the built-in AST code",
            "  graph the blast-radius gate needs to answer structurally instead of by grep.",
            "  Try it now:  /brainstorm  — propose a change and watch the HARD-GATE refuse",
            "  to emit a spec until you have approved an approach.",
            "  See the guardrails any time:  mokata rules",
        ),
    ),
    "memory": ModeSpec(
        name="memory",
        profile="standard",
        summary="the gates + typed project memory that survives every session",
        offers_embeddings=True,
        offers_graph=False,
        quickstart=(
            "── memory mode: the gates + memory that persists ────────────────",
            "  You got: everything seatbelt gives you, plus typed project memory on the",
            "  local SQLite store — rules, guardrails, decisions and context that outlive",
            "  the session instead of being re-explained every time.",
            "  Try it now:  /onboard  — teach mokata one project rule, then run",
            "  `mokata memory` to see it back in the store.",
            "  Semantic recall rides mokata[embeddings]; mokata offers it once, interactively.",
        ),
    ),
    "full": ModeSpec(
        name="full",
        profile="full",
        summary="everything the spine can wire",
        offers_embeddings=True,
        offers_graph=True,
        quickstart=(
            "── full mode: everything wired ─────────────────────────────────",
            "  You got: the gates, persistent memory, and every graph and memory provider",
            "  the spine knows about (each degrades to its floor when absent).",
            "  Try it now:  mokata query blast_radius <symbol>  — ask who actually calls a",
            "  function; answered from the code graph, not from a grep.",
            "  Then:  /brainstorm  — that same blast radius lands inside the decision.",
        ),
    ),
}


@dataclass
class ModeOffersResult:
    """What the post-init offers actually did. `*Result` = produced value (doc 85 §3)."""

    mode: str = ""
    embeddings_offered: bool = False
    graph_offered: bool = False
    embeddings_installed: bool = False
    graph_adopted: bool = False
    skipped_reason: str = ""
    notes: List[str] = field(default_factory=list)


def mode_names() -> List[str]:
    """The `--mode` choices, in adoption order (least to most)."""
    return list(ADOPTION_MODES)


def mode_spec(mode: str) -> ModeSpec:
    try:
        return ADOPTION_MODES[mode]
    except KeyError:
        raise ValueError(
            f"unknown mode '{mode}'; choose one of {mode_names()}"
        ) from None


def profile_for_mode(mode: str) -> str:
    """The profile a mode aliases. `*_for` always returns (doc 85 §3) — unknown mode raises."""
    return mode_spec(mode).profile


def render_quickstart(mode: str) -> str:
    """The mode's printed 5-minute quickstart. Pure: renders, never writes (doc 85 §3).

    Every command named here is real and works on the wiring the mode actually lands — the
    doc-00 rule-2 spirit applied to CLI output. Slash commands use the BARE `/<name>` form,
    which is the canonical pip/setup route this command belongs to (doc 85 §3).
    """
    return "\n".join(("",) + mode_spec(mode).quickstart)


def offer_mode_extras(
    root: str,
    mode: str,
    *,
    interactive: bool,
    ledger: Any = None,
    user_home: Optional[str] = None,
    out: Optional[Callable[[str], None]] = None,
    prompt_fn: Optional[Callable[[str], bool]] = None,
    runner: Optional[Callable[..., Any]] = None,
    offer_embeddings_fn: Optional[Callable[..., Any]] = None,
    offer_graph_fn: Optional[Callable[..., Any]] = None,
) -> ModeOffersResult:
    """Fire the mode's consented post-init offers through the EXISTING primitives.

    * `seatbelt` — structurally never offers: the embeddings branch is not entered, so
      `offer_extra`/`install_extra` are never called and nothing is even imported.
    * `memory` / `full` — the DB.S4 embeddings ask (`extras_install.offer_embeddings`).
    * `full` — additionally the GR.S2 graph offer (`graph_adopt.offer_graph_at_setup`), which
      has no other production caller today.

    `interactive` is the hard interlock: off a TTY, or under `--yes`, the caller passes False
    and this returns immediately, so no non-interactive path can reach `pip` (the DB.S4
    posture). Every offer is DEGRADE_CLEAN — an init that has already succeeded must never be
    undone by an optional extra.
    """
    emit = out or print
    spec = mode_spec(mode)
    result = ModeOffersResult(mode=mode)

    if not interactive:
        result.skipped_reason = "non-interactive (no offers; nothing installed)"
        return result

    if spec.offers_embeddings:
        result.embeddings_offered = True
        try:
            from .extras_install import offer_embeddings as _offer_embeddings
            offer = offer_embeddings_fn or _offer_embeddings
            kwargs: Dict[str, Any] = {"ledger": ledger, "user_home": user_home, "out": emit}
            if prompt_fn is not None:
                kwargs["prompt_fn"] = prompt_fn
            if runner is not None:
                kwargs["runner"] = runner
            ores = offer(root, **kwargs)
            result.embeddings_installed = bool(getattr(ores, "installed", False))
        except Exception as exc:                      # noqa: BLE001 — DEGRADE_CLEAN
            # The repo is already wired; an optional offer that blows up costs the user the
            # semantic tier, never their setup. Same posture as onboarding.run_wizard.
            result.notes.append(f"embeddings offer skipped ({type(exc).__name__}: {exc})")
            emit(f"note: the embeddings offer was skipped ({type(exc).__name__}: {exc}); "
                 f"mokata's built-in hashing tier is active.")

    if spec.offers_graph:
        result.graph_offered = True
        try:
            from .knowledge.graph_adopt import offer_graph_at_setup as _offer_graph
            offer_g = offer_graph_fn or _offer_graph
            gkwargs: Dict[str, Any] = {"ledger": ledger, "user_home": user_home, "out": emit}
            if prompt_fn is not None:
                gkwargs["prompt_fn"] = prompt_fn
            gres = offer_g(root, **gkwargs)
            result.graph_adopted = bool(getattr(gres, "adopted", False))
        except Exception as exc:                      # noqa: BLE001 — DEGRADE_CLEAN
            result.notes.append(f"graph offer skipped ({type(exc).__name__}: {exc})")
            emit(f"note: the code-graph offer was skipped ({type(exc).__name__}: {exc}); "
                 f"mokata's AST floor is active.")

    return result


__all__ = [
    "ADOPTION_MODES",
    "ModeSpec",
    "ModeOffersResult",
    "mode_names",
    "mode_spec",
    "profile_for_mode",
    "render_quickstart",
    "offer_mode_extras",
]
