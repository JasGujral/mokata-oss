"""J2 — cross-harness portability.

A THIN boundary the engine talks to so the pipeline can run beyond Claude Code (Codex,
OpenCode, …) without duplicating the engine. mokata's operations map to four harness
capabilities — commands, hooks, context injection, subagents. A `HarnessBoundary` routes
each operation to the concrete `Harness`; when a harness lacks a capability it degrades
with a clear message rather than crashing.

The engine stays harness-agnostic: it only ever calls the boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set

HARNESS_CAPABILITIES = ("commands", "hooks", "context_injection", "subagents")


@dataclass
class HarnessResult:
    ok: bool
    degraded: bool
    message: str
    value: Any = None


class Harness:
    """A concrete harness: a name, the capabilities it supports, and optional operation
    callables (ops keyed by capability). Subclass or construct directly."""

    def __init__(self, name: str, capabilities: Set[str],
                 ops: Optional[Dict[str, Callable]] = None) -> None:
        self.name = name
        self.capabilities = set(capabilities)
        self.ops = dict(ops or {})

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    def perform(self, capability: str, label: str, *args) -> HarnessResult:
        fn = self.ops.get(capability)
        if fn is not None:
            return HarnessResult(True, False, f"{self.name}: {label}", fn(*args))
        return HarnessResult(True, False, f"{self.name}: {label} (noop)")


def claude_code_harness(ops: Optional[Dict[str, Callable]] = None) -> Harness:
    """The reference harness — supports all capabilities."""
    return Harness("claude-code", set(HARNESS_CAPABILITIES), ops)


def codex_harness(ops: Optional[Dict[str, Callable]] = None) -> Harness:
    """A generic Codex / shell harness (Stage 52a). It runs prompt-style commands and injects
    context, but has NO PreToolUse hooks and NO native subagent fan-out — so it declares only
    {commands, context_injection}. The engine degrades CLEARLY (states the limitation, falls
    back) when it needs `hooks` or `subagents` here; it never pretends they exist."""
    return Harness("codex", {"commands", "context_injection"}, ops)


def cowork_harness(ops: Optional[Dict[str, Callable]] = None) -> Harness:
    """The Cowork plugin host (Stage 52b). Cowork supports plugins, so the `/mokata:*` commands,
    the SessionStart briefing (context injection), and a subagent runner are available — but its
    PreToolUse hook enforcement is NOT guaranteed (the secret-guard hook may not run there). So
    `hooks` is declared False and that gate degrades CLEARLY: in Cowork, durable-write protection
    relies on mokata's own gated CLI/MCP WriteGate (which scans + gates + audits regardless of
    the hook), NOT on the PreToolUse hook. Honest over assumed parity."""
    return Harness("cowork", {"commands", "context_injection", "subagents"}, ops)


# ======================================================================================
# Stage 63 — the Reach batch: more agents than Claude Code, behind the SAME boundary.
# Each adapter declares ONLY the capabilities we can VERIFY the agent actually supports; an
# unverified/unsupported capability is declared ABSENT so the HarnessBoundary degrades
# CLEARLY (ok=False/degraded, names the missing capability) — never a silent gate no-op.
# When unsure, declare absent. (MCP auto-wiring is tracked separately in harness_setup, per
# whether the agent's MCP config schema matches mokata's `mcpServers` merge.)
# ======================================================================================

def cursor_harness(ops: Optional[Dict[str, Callable]] = None) -> Harness:
    """Cursor. Supports custom markdown commands (`.cursor/commands/*.md`) and project context
    (`.cursor/rules`), and speaks MCP (`.cursor/mcp.json`, `mcpServers`). It has NO mokata-
    drivable PreToolUse enforcement hook and NO programmatic subagent fan-out we can drive, so
    `hooks` + `subagents` are declared absent and degrade clearly. Honest, not assumed."""
    return Harness("cursor", {"commands", "context_injection"}, ops)


def copilot_harness(ops: Optional[Dict[str, Callable]] = None) -> Harness:
    """GitHub Copilot. Supports prompt files (`.github/prompts/*.prompt.md`) and custom
    instructions (`.github/copilot-instructions.md`, context). No enforcement hook, no
    drivable subagent fan-out → `hooks` + `subagents` declared absent (degrade clearly). Its
    MCP config (VS Code `mcp.json`) uses a DIFFERENT schema (`servers`), so MCP is a documented
    manual step, not auto-wired — never a silent half-wiring."""
    return Harness("copilot", {"commands", "context_injection"}, ops)


def windsurf_harness(ops: Optional[Dict[str, Callable]] = None) -> Harness:
    """Windsurf (Codeium). Supports workflows (`.windsurf/workflows/*.md`, slash-invocable) and
    rules (`.windsurf/rules`, context). No mokata-drivable enforcement hook / subagent fan-out →
    `hooks` + `subagents` absent (degrade clearly). Its MCP config lives outside the project
    (`~/.codeium/windsurf/mcp_config.json`), so MCP is a documented manual step."""
    return Harness("windsurf", {"commands", "context_injection"}, ops)


def gemini_harness(ops: Optional[Dict[str, Callable]] = None) -> Harness:
    """Gemini CLI. Supports custom commands (`.gemini/commands/*.toml`) and context
    (`GEMINI.md`), and speaks MCP (`.gemini/settings.json`, `mcpServers`). No enforcement hook /
    drivable subagent fan-out → `hooks` + `subagents` absent (degrade clearly)."""
    return Harness("gemini", {"commands", "context_injection"}, ops)


def aider_harness(ops: Optional[Dict[str, Callable]] = None) -> Harness:
    """Aider. Supports context injection (conventions / `--read` files, `CONVENTIONS.md`) but
    has NO user-authored slash-command FILE system (its `/commands` are built-in, not
    extensible by prompt files), no enforcement hook, no drivable subagent fan-out, and no
    native MCP. So it declares ONLY `context_injection`; `commands`/`hooks`/`subagents` are
    absent and degrade CLEARLY (mokata's commands are provided as REFERENCE prompts, never
    pretended to be native slash commands)."""
    return Harness("aider", {"context_injection"}, ops)


# The harness registry, keyed by the short name `mokata setup`/`mokata harness` use. The
# reference harness is "claude"; the rest are portable adapters (codex/cowork from Stage 52,
# cursor/copilot/windsurf/gemini/aider from Stage 63).
HARNESS_FACTORIES: Dict[str, Callable[..., Harness]] = {
    "claude": claude_code_harness,
    "codex": codex_harness,
    "cowork": cowork_harness,
    "cursor": cursor_harness,
    "copilot": copilot_harness,
    "windsurf": windsurf_harness,
    "gemini": gemini_harness,
    "aider": aider_harness,
}


def available_harnesses() -> List[str]:
    """The registered harness names (deterministic order)."""
    return list(HARNESS_FACTORIES)


def get_harness(name: str) -> Harness:
    """Construct a registered harness, or raise ValueError for an unknown name."""
    factory = HARNESS_FACTORIES.get(name)
    if factory is None:
        raise ValueError(
            f"unknown harness '{name}'; available: {', '.join(HARNESS_FACTORIES)}")
    return factory()


def capability_matrix() -> Dict[str, Dict[str, bool]]:
    """{harness name -> {capability -> supported}} across every registered harness."""
    return {name: {cap: factory().supports(cap) for cap in HARNESS_CAPABILITIES}
            for name, factory in HARNESS_FACTORIES.items()}


class HarnessBoundary:
    def __init__(self, harness: Harness) -> None:
        self.harness = harness

    @property
    def name(self) -> str:
        return self.harness.name

    def _guard(self, capability: str, label: str, *args) -> HarnessResult:
        if not self.harness.supports(capability):
            return HarnessResult(
                ok=False, degraded=True, value=None,
                message=(f"harness '{self.harness.name}' lacks capability "
                         f"'{capability}' — degraded ({label} skipped)"))
        return self.harness.perform(capability, label, *args)

    # the engine's harness-facing operations
    def run_command(self, name: str, args: Any = None) -> HarnessResult:
        return self._guard("commands", f"/{name}", name, args)

    def run_hook(self, name: str, payload: Any = None) -> HarnessResult:
        return self._guard("hooks", f"hook:{name}", name, payload)

    def inject_context(self, text: str) -> HarnessResult:
        return self._guard("context_injection", "inject context", text)

    def run_subagent(self, task: Any) -> HarnessResult:
        return self._guard("subagents", f"subagent:{task}", task)
