"""I2 — universal human-gated writes.

Every durable write — code, memory, or config — goes through one gate: scan for secrets
(I1), then require explicit human approval, then commit, recording the decision in the
audit ledger (I3). A secret is a security block that approval cannot override; a write is
never committed silently.
"""

from __future__ import annotations

from ..prompt import read_yes_no

from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Sequence

from .secrets import Finding, scan
from .trust import TrustPolicy

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
    # TM.S8 — the enforcement verdict when in-scope governance rules were evaluated (None when no
    # rules/action were supplied, so every pre-S8 caller is byte-identical). `overridden` is True
    # when a soft-rule block was cleared by an explicit, ledgered override.
    enforcement: Any = None
    overridden: bool = False


def _default_confirm(text: str) -> bool:
    return read_yes_no(text, "Approve this write?")


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
               assume_yes: bool = False, prompt: Optional[str] = None,
               rules: Optional[Sequence[Any]] = None, action: Any = None,
               scope_context: Any = None,
               override: Optional[Callable[[Any], bool]] = None,
               matcher: Optional[Callable[[Any, Any], bool]] = None,
               actor: Optional[str] = None) -> WriteOutcome:
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

        # Layer 2 (governance, TM.S8 / P14): in-scope hard rules block with no runtime override;
        # a soft rule blocks unless an explicit override clears it (ledgered); advisory only flags.
        # No rules/action supplied → this whole layer is skipped (local/zero-config unaffected).
        enf = None
        if action is not None and rules:
            from .enforce import EnforcementGate
            enf = EnforcementGate(ledger=self.ledger).check(
                action, rules, context=scope_context, matcher=matcher,
                override=override, actor=(actor or req.actor))
            if not enf.allowed:
                self._log(req, "blocked", "governance rule fired")
                return WriteOutcome(False, True, enf.message, [], enforcement=enf.verdict)

        # Layer 2: human gate. A caller may supply a richer `prompt` surface (e.g. memory's
        # render_write / old→new healing diff) so unifying on this gate doesn't flatten it.
        if not assume_yes:
            gate = confirm or _default_confirm
            shown = prompt or (f"mokata · approve {req.kind} write to {req.target} "
                               f"({len(req.content)} chars)?")
            if not gate(shown):
                self._log(req, "declined", "human declined")
                return WriteOutcome(False, True, "declined at the human gate", [])

        # Commit.
        if commit is not None:
            commit()
        self._log(req, "approved", "committed")
        return WriteOutcome(True, False, "committed", [],
                            enforcement=(enf.verdict if enf is not None else None),
                            overridden=bool(enf is not None and enf.overridden))
