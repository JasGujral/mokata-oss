"""F1 — token / cost tracker (mokata's own, in-loop).

A conservative, dependency-free estimator (reuses the bootstrap chars/4 rule). Costs are
illustrative per-1k-token rates the caller can override; this is for in-loop governance
("are we spending more than the work is worth"), not billing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional

from ..bootstrap import estimate_tokens

# Illustrative default rates (USD per 1k tokens); override for your model.
DEFAULT_INPUT_COST = 0.003
DEFAULT_OUTPUT_COST = 0.015

# R11 — token-estimate calibration. The spine's token count is a deliberately tokenizer-free
# chars/4 ESTIMATE engineered to run HIGH so the 2k bootstrap budget (P11) holds; its safety
# margin is "estimate >= actual". When the harness reports a real token count we log
# estimate-vs-actual to the ledger so that margin is MEASURED, not merely asserted (P16). The
# estimate stays tokenizer-free — calibration only observes it.
CALIBRATION_KIND = "token_calibration"
# The estimate is meant to be conservative, so any real `actual` that EXCEEDS it has blown the
# margin (the estimate under-counted). 1.0 = strict: no slack granted beyond the estimate.
CALIBRATION_MARGIN_RATIO = 1.0


@dataclass
class CalibrationRecord:
    """One estimate-vs-actual observation. `actual`/`ratio`/`over_margin` are populated only when
    a real token count is available — with none, the estimate is recorded alone (never a
    fabricated actual), and the comparison activates when an actual later arrives."""
    context: str
    estimate: int
    actual: Optional[int] = None
    ratio: Optional[float] = None            # actual / estimate, when actual is known
    over_margin: bool = False                # a known actual exceeded the estimate's margin

    def as_fields(self) -> dict:
        fields: dict = {"context": self.context, "estimate": self.estimate}
        if self.actual is not None:
            fields["actual"] = self.actual
            fields["ratio"] = self.ratio
            fields["over_margin"] = self.over_margin
        return fields


def calibration_record(context: str, estimate: int, actual: Optional[int] = None, *,
                       margin: float = CALIBRATION_MARGIN_RATIO) -> CalibrationRecord:
    """Build a calibration record comparing the chars/4 `estimate` to a real `actual` token
    count WHEN available. No actual -> estimate recorded alone (no fabrication). Degrades clean:
    a zero/negative estimate yields no ratio and is never flagged (no ZeroDivisionError)."""
    est = int(estimate)
    if actual is None:
        return CalibrationRecord(context=context, estimate=est)
    act = int(actual)
    ratio = (act / est) if est > 0 else None
    over = ratio is not None and ratio > margin
    return CalibrationRecord(context=context, estimate=est, actual=act,
                             ratio=ratio, over_margin=over)


def log_calibration(ledger: Any, context: str, estimate: int, actual: Optional[int] = None, *,
                    margin: float = CALIBRATION_MARGIN_RATIO) -> Optional[CalibrationRecord]:
    """Write ONE calibration record to the existing ledger sink and return it. Pure
    observability: fully guarded so it NEVER raises and never blocks/slows the caller (the
    bootstrap path) — a broken ledger just yields None. Reuses the one ledger path (no new sink)."""
    try:
        rec = calibration_record(context, estimate, actual, margin=margin)
        if ledger is not None:
            ledger.record(CALIBRATION_KIND, **rec.as_fields())
        return rec
    except Exception:
        return None


def log_bootstrap_calibration(surface: Any, estimate: int, actual: Optional[int] = None, *,
                              context: str = "bootstrap",
                              margin: float = CALIBRATION_MARGIN_RATIO
                              ) -> Optional[CalibrationRecord]:
    """Convenience for the SessionStart path: resolve the surface's ledger and log the briefing's
    chars/4 `estimate` (plus a real `actual` when the harness reports one). Guarded end-to-end —
    resolving the ledger and writing are both wrapped so this observability step never raises or
    blocks the session."""
    try:
        from .ledger import AuditLedger
        ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
    except Exception:
        return None
    return log_calibration(ledger, context, estimate, actual, margin=margin)


@dataclass
class UsageEntry:
    label: str
    input_tokens: int
    output_tokens: int


@dataclass
class TokenTracker:
    input_cost_per_1k: float = DEFAULT_INPUT_COST
    output_cost_per_1k: float = DEFAULT_OUTPUT_COST
    entries: List[UsageEntry] = field(default_factory=list)

    def add(self, label: str, input_text: str = "", output_text: str = "",
            input_tokens: Optional[int] = None,
            output_tokens: Optional[int] = None) -> UsageEntry:
        it = input_tokens if input_tokens is not None else estimate_tokens(input_text)
        ot = output_tokens if output_tokens is not None else estimate_tokens(output_text)
        entry = UsageEntry(label=label, input_tokens=it, output_tokens=ot)
        self.entries.append(entry)
        return entry

    @property
    def total_input(self) -> int:
        return sum(e.input_tokens for e in self.entries)

    @property
    def total_output(self) -> int:
        return sum(e.output_tokens for e in self.entries)

    def cost(self) -> float:
        return (self.total_input / 1000 * self.input_cost_per_1k
                + self.total_output / 1000 * self.output_cost_per_1k)

    def report(self) -> str:
        return (f"tokens: {self.total_input} in / {self.total_output} out "
                f"across {len(self.entries)} call(s) — est. cost ${self.cost():.4f}")
