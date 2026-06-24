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

from dataclasses import dataclass
from typing import List

from .config import Surface
from .router import Resolution

# Hard ceiling. The briefing is built to come in far under this; the budget exists so
# the spine can *prove* it stays small, and truncate defensively if a future addition
# ever grows it.
BOOTSTRAP_TOKEN_BUDGET = 2000

# Inviolable gates, surfaced every session (P2 + P8 — cannot be configured away).
_INVIOLABLE_GATES = [
    "human-gate: every durable write (code, memory, config) is staged for approval",
    "local-first: nothing leaves the machine unless you explicitly wire it; no telemetry",
]


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
    lines.append("Active gates (inviolable):")
    for gate in _INVIOLABLE_GATES:
        lines.append(f"- {gate}")
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

    lines.append("")
    lines.append(
        "Reflex: before acting, check the relevant gate/skill; verify with evidence, "
        "not claims."
    )
    return "\n".join(lines) + "\n"


def build_bootstrap(
    surface: Surface, budget: int = BOOTSTRAP_TOKEN_BUDGET
) -> BootstrapResult:
    text = _render(surface)
    tokens = estimate_tokens(text)

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
