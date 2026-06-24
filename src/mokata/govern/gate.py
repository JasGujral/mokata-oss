"""I2 — universal human-gated writes.

Every durable write — code, memory, or config — goes through one gate: scan for secrets
(I1), then require explicit human approval, then commit, recording the decision in the
audit ledger (I3). A secret is a security block that approval cannot override; a write is
never committed silently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

from .secrets import Finding, scan
from .trust import READ_ONLY, TrustPolicy

WRITE_KINDS = ("code", "memory", "config", "send")


@dataclass
class WriteRequest:
    kind: str            # one of WRITE_KINDS
    target: str          # path / destination
    content: str = ""
    actor: str = "agent"
    tool: Optional[str] = None   # the wired tool/adapter making the write (K3 trust)


@dataclass
class WriteOutcome:
    committed: bool
    aborted: bool
    reason: str
    findings: List[Finding] = field(default_factory=list)


def _default_confirm(text: str) -> bool:
    try:
        return input(text + "\nApprove this write? [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


class WriteGate:
    def __init__(self, ledger: Any = None,
                 trust: Optional[TrustPolicy] = None) -> None:
        self.ledger = ledger
        self.trust = trust            # K3 — per-adapter trust dial (optional)

    def _log(self, req: WriteRequest, decision: str, reason: str) -> None:
        if self.ledger is not None:
            self.ledger.record("write_gate", write_kind=req.kind, target=req.target,
                               actor=req.actor, decision=decision, reason=reason)

    def submit(self, req: WriteRequest, commit: Optional[Callable[[], None]] = None,
               confirm: Optional[Callable[[str], bool]] = None,
               assume_yes: bool = False) -> WriteOutcome:
        # K3 trust dial: a read-only tool cannot write at all; propose-only writes can
        # never be auto-approved (always surfaced for a human).
        if self.trust is not None and req.tool:
            if not self.trust.can_write(req.tool):
                self._log(req, "blocked", "read-only trust")
                return WriteOutcome(
                    False, True,
                    f"blocked: tool '{req.tool}' is read-only — writes not permitted",
                    [])
            if not self.trust.allows_auto_approve(req.tool):
                assume_yes = False    # propose-only -> force explicit approval

        # Layer 1 (security): secrets are a hard block, regardless of human approval.
        findings = scan(text=req.content, path=req.target,
                        for_send=(req.kind == "send"))
        if findings:
            self._log(req, "blocked", "secret detected")
            return WriteOutcome(False, True,
                                "blocked: secret(s) detected — remove before writing",
                                findings)

        # Layer 2: human gate.
        if not assume_yes:
            gate = confirm or _default_confirm
            prompt = (f"mokata · approve {req.kind} write to {req.target} "
                      f"({len(req.content)} chars)?")
            if not gate(prompt):
                self._log(req, "declined", "human declined")
                return WriteOutcome(False, True, "declined at the human gate", [])

        # Commit.
        if commit is not None:
            commit()
        self._log(req, "approved", "committed")
        return WriteOutcome(True, False, "committed", [])
