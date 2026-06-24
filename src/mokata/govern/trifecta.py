"""I4 — lethal-trifecta mitigation.

When all three of {system access, private/memory data, an outbound action} coexist, an
outbound action can exfiltrate private data — the "lethal trifecta". mokata detects the
condition and GATES the outbound action behind explicit human approval; nothing leaves
silently, and the decision is logged. When the trifecta is not active, no gate is imposed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass
class TrifectaState:
    system_access: bool
    private_data: bool
    outbound: bool

    @property
    def active(self) -> bool:
        return self.system_access and self.private_data and self.outbound


@dataclass
class OutboundRequest:
    action: str
    destination: str
    payload: str = ""


@dataclass
class OutboundDecision:
    allowed: bool
    gated: bool
    reason: str


def detect_trifecta(system_access: bool, private_data: bool, outbound: bool) -> bool:
    return system_access and private_data and outbound


def _default_confirm(text: str) -> bool:
    try:
        return input(text + "\nApprove this outbound action? [y/N] ").strip().lower() \
            in ("y", "yes")
    except EOFError:
        return False


class TrifectaGuard:
    def __init__(self, ledger: Any = None) -> None:
        self.ledger = ledger

    def _render(self, request: OutboundRequest, state: TrifectaState) -> str:
        return (f"mokata · LETHAL TRIFECTA — system access + private data + outbound.\n"
                f"  action: {request.action} -> {request.destination}\n"
                f"  Outbound is blocked unless you explicitly approve it.")

    def _log(self, request: OutboundRequest, state: TrifectaState,
             gated: bool, allowed: bool) -> None:
        if self.ledger is not None:
            self.ledger.record("outbound", action=request.action,
                               destination=request.destination, trifecta=state.active,
                               gated=gated, allowed=allowed)

    def gate_outbound(self, request: OutboundRequest, state: TrifectaState,
                      confirm: Optional[Callable[[str], bool]] = None,
                      assume_yes: bool = False) -> OutboundDecision:
        if not state.active:
            self._log(request, state, gated=False, allowed=True)
            return OutboundDecision(True, False, "no lethal trifecta — outbound allowed")

        if not assume_yes:
            gate = confirm or _default_confirm
            if not gate(self._render(request, state)):
                self._log(request, state, gated=True, allowed=False)
                return OutboundDecision(
                    False, True, "blocked: lethal trifecta — human approval declined")

        self._log(request, state, gated=True, allowed=True)
        return OutboundDecision(True, True, "approved under the lethal-trifecta gate")
