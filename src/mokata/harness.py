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
from typing import Any, Callable, Dict, Optional, Set

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
