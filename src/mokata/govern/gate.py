"""I2 — universal human-gated writes.

Every durable write — code, memory, or config — goes through one gate: scan for secrets
(I1), then require explicit human approval, then commit, recording the decision in the
audit ledger (I3). A secret is a security block that approval cannot override; a write is
never committed silently.
"""

from __future__ import annotations

from ..prompt import read_yes_no

from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, List, Optional, Sequence

from .secrets import Finding, scan
from .trust import TrustPolicy

WRITE_KINDS = ("code", "memory", "config", "send")


@dataclass
class WriteRequest:
    kind: str            # one of WRITE_KINDS
    target: str          # path / destination
    content: str = ""
    actor: str = "agent"
    tool: Optional[str] = None      # WHICH tool/command is writing (K3 trust keys on this)
    surface: Optional[str] = None   # WHERE it came from: "mcp" | "cli" (the trust surface default)


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
    # MS.S3/B2 — the REAL seq of the gate's `approved` ledger entry (None when declined/blocked or
    # no ledger). Callers record THIS as the approval id instead of predicting `len(ledger)`, so a
    # racing gated write can never be misattributed to another's approval.
    approval_seq: Optional[int] = None


def _default_confirm(text: str) -> bool:
    return read_yes_no(text, "Approve this write?")


class WriteGate:
    def __init__(self, ledger: Any = None,
                 trust: Optional[TrustPolicy] = None) -> None:
        self.ledger = ledger
        self.trust = trust            # K3 — per-adapter trust dial (optional)

    def _log(self, req: WriteRequest, decision: str, reason: str) -> Any:
        """Record the gate decision and RETURN the ledger entry (or None with no ledger). The
        returned `seq` is the approval id the caller propagates (B2)."""
        if self.ledger is not None:
            return self.ledger.record("write_gate", write_kind=req.kind, target=req.target,
                                      actor=req.actor, decision=decision, reason=reason)
        return None

    @contextmanager
    def _ledger_hold(self) -> Iterator[None]:
        """Hold the ledger's cross-process append lock across commit() + the approved record, so the
        store's `len(ledger)+1` approval-id prediction (computed inside commit) is provably exact —
        no other writer can append between the prediction and the approved entry it predicts (B2).
        A no-op when there is no ledger, or a ledger without the lock (a test stub)."""
        if self.ledger is not None and hasattr(self.ledger, "hold"):
            with self.ledger.hold():
                yield
        else:
            with nullcontext():
                yield

    def submit(self, req: WriteRequest, commit: Optional[Callable[[], None]] = None,
               confirm: Optional[Callable[[str], bool]] = None,
               assume_yes: bool = False, prompt: Optional[str] = None,
               rules: Optional[Sequence[Any]] = None, action: Any = None,
               scope_context: Any = None,
               override: Optional[Callable[[Any], bool]] = None,
               matcher: Optional[Callable[[Any, Any], bool]] = None,
               actor: Optional[str] = None,
               human_approved: bool = False) -> WriteOutcome:
        """`human_approved` says an explicit human decision on THIS EXACT write has already been
        obtained and verified out-of-band — today, an SI.3 approval a human minted with
        `mokata approve <id>`, bound to the write's content hash, single-use and session-scoped.

        It is deliberately NOT `assume_yes`. `assume_yes` is a STAND-IN approval ("nobody is here to
        ask; proceed"), which is exactly what propose-only exists to refuse. `human_approved` is the
        human's real decision, already made. Conflating them is what made propose-only unusable on
        MCP (see below), so they stay two flags."""
        # K3 trust dial (SI.4). The dial keys on WHO is writing: the tool's own level, else the
        # surface's (`mcp`/`cli`), else gated-write. An untagged request resolves to the default, so
        # a caller that threads no identity is byte-identical to before.
        if self.trust is not None and (req.tool or req.surface):
            if not self.trust.can_write(req.tool, req.surface):
                self._log(req, "blocked", "read-only trust")
                return WriteOutcome(
                    False, True,
                    f"blocked: '{req.tool or req.surface}' is read-only — writes not permitted",
                    [])
            if not self.trust.allows_auto_approve(req.tool, req.surface) and not human_approved:
                # propose-only: no AUTO-approval. An explicit human decision is required — and none
                # has been verified yet, so fall through to the human gate below and ASK.
                #
                # This is the whole of the SI.4 semantic fix. The rule used to be a bare
                # `assume_yes = False`, i.e. "always force the interactive prompt". That is wrong in
                # a way that only bites off a TTY: `prompt.read_yes_no` fails CLOSED when stdin is
                # not a terminal, and the MCP server is stdio-bound to the harness and HAS no
                # terminal. So a propose-only tool on MCP had every write DECLINED — including one
                # carrying a genuine, human-minted approval. The rung was a dead end on the surface
                # it most needed to govern.
                #
                # `propose-only` means "a human must decide", not "a human must be prompted". A
                # prompt is one way to obtain the decision; SI.3's out-of-band approval is another,
                # and a stronger one. So a verified approval SATISFIES this rung and skips the ask.
                assume_yes = False

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
        #
        # `human_approved` clears it because the human ALREADY decided, out-of-band, on this exact
        # content — re-asking would be asking a terminal that isn't there. `assume_yes` clears it as
        # the stand-in for a non-interactive run. Everything else asks.
        if not assume_yes and not human_approved:
            gate = confirm or _default_confirm
            shown = prompt or (f"mokata · approve {req.kind} write to {req.target} "
                               f"({len(req.content)} chars)?")
            if not gate(shown):
                self._log(req, "declined", "human declined")
                return WriteOutcome(False, True, "declined at the human gate", [])

        # Commit. The commit and the `approved` ledger record are held under ONE ledger-lock window
        # so a caller predicting the approval seq inside commit (the store's `len+1`) gets exactly
        # the seq the approved entry lands at — no interleave, no misattribution (B2). The human gate
        # above is deliberately OUTSIDE the hold (never hold a cross-process lock across a prompt).
        with self._ledger_hold():
            if commit is not None:
                commit()
            approval = self._log(req, "approved", "committed")
        approval_seq = approval.get("seq") if isinstance(approval, dict) else None
        return WriteOutcome(True, False, "committed", [],
                            enforcement=(enf.verdict if enf is not None else None),
                            overridden=bool(enf is not None and enf.overridden),
                            approval_seq=approval_seq)
