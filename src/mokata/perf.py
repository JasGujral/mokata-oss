"""Stage 67 — wall-clock latency budget (it-feels-instant).

Latency is a *measured, gated* constraint here: each hot, user-facing operation that runs every
turn/session (the statusline, the SessionStart briefing, the per-PreToolUse secret scan, the
grep-floor structural query, memory recall, `status`) has a wall-clock budget, and the perf tests
assert each stays under it.

This is **wall-clock** latency — distinct from the *token* budget (F5 `mokata budget`), which is
about tokens. Dependency-free: `time.perf_counter` + the stdlib `statistics` median, nothing else.
Read-only: every benchmarked op is one of mokata's existing read paths; the bench never writes.

Robustness over precision: budgets are generous CEILINGS (typical timings are sub-millisecond),
measured as a warmup + median-of-N, with a relax multiplier for noisy/CI runners — so they assert
a real upper bound without ever false-failing.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import statistics
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

# --- the budget table -----------------------------------------------------------------------
# Per-operation wall-clock budgets in MILLISECONDS. These are deliberately generous ceilings,
# not targets: the hot paths run sub-millisecond on a dev machine, so each budget carries large
# headroom (≥100×) and asserts a real upper bound that won't flake on a slow shared runner.
LATENCY_BUDGETS_MS: Dict[str, float] = {
    "statusline": 50.0,    # the stage badge — rendered every statusline refresh
    "briefing": 150.0,     # the SessionStart briefing (build_bootstrap)
    "secret_scan": 100.0,  # the PreToolUse secret scan — runs on every tool call
    "grep_query": 150.0,   # the grep-floor structural query (no graph wired)
    "recall": 100.0,       # JIT memory recall
    "status": 150.0,       # `mokata status` capability resolution
}

DEFAULT_REPEAT = 7
DEFAULT_WARMUP = 2

# A deterministic, realistic ~4 KB code-ish blob for the secret-scan op. Contains NO real secret
# (so the scan does its full signature + entropy work without a false positive in the fixture).
_SCAN_SAMPLE = (
    "def compute(x):\n    return helper(x) + 1\n\n" * 70
    + "# configuration\nDEBUG = False\nTIMEOUT_SECONDS = 30\nRETRIES = 3\n" * 25
)


def relax_factor() -> float:
    """Budget multiplier for noisy environments. `MOKATA_PERF_RELAX=<float>` overrides; otherwise
    CI auto-relaxes (shared runners are noisy). 1.0 on a normal dev machine."""
    env = os.environ.get("MOKATA_PERF_RELAX")
    if env:
        try:
            v = float(env)
            if v > 0:
                return v
        except ValueError:
            pass
    if os.environ.get("CI"):
        return 4.0
    return 1.0


@dataclass
class BenchResult:
    """The measured latency of one hot op vs its budget."""

    name: str
    runs: int
    median_ms: float
    min_ms: float
    max_ms: float
    budget_ms: float
    relax: float = 1.0

    @property
    def effective_budget_ms(self) -> float:
        return self.budget_ms * self.relax

    @property
    def within_budget(self) -> bool:
        # a 0/absent budget means "unbudgeted" — never fails (the op is still reported).
        return self.budget_ms <= 0.0 or self.median_ms <= self.effective_budget_ms

    @property
    def headroom_x(self) -> float:
        """How many times under budget the median is (∞ for an unbudgeted op)."""
        if self.budget_ms <= 0.0 or self.median_ms <= 0.0:
            return float("inf")
        return self.effective_budget_ms / self.median_ms

    def render(self, *, ascii_only: bool = False) -> str:
        ok = "OK" if self.within_budget else "OVER"
        mark = ("[ok]" if self.within_budget else "[!!]") if ascii_only \
            else ("✓" if self.within_budget else "✗")
        budget = (f"≤ {self.effective_budget_ms:.0f} ms"
                  if self.budget_ms > 0 else "(unbudgeted)")
        if ascii_only:
            budget = budget.replace("≤", "<=")
        return (f"{mark} {self.name:<12} median {self.median_ms:7.3f} ms  "
                f"(min {self.min_ms:.3f} / max {self.max_ms:.3f})  {budget}  {ok}")


def measure(fn: Callable[[], Any], *, repeat: int = DEFAULT_REPEAT,
            warmup: int = DEFAULT_WARMUP):
    """Time `fn` with a warmup then `repeat` samples; return (median_ms, min_ms, max_ms).

    The median is robust to the occasional GC/scheduler spike a single timing would catch."""
    for _ in range(max(0, warmup)):
        fn()
    samples: List[float] = []
    for _ in range(max(1, repeat)):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000.0)
    return statistics.median(samples), min(samples), max(samples)


def build_hot_ops(surface: Any) -> Dict[str, Callable[[], Any]]:
    """The hot, user-facing read operations to benchmark, as zero-arg callables.

    Inputs (store, grep backend, the scan sample) are built ONCE here, OUTSIDE the timed
    callable, so each measurement times the OPERATION, not fixture construction. Every op is an
    existing read path — nothing here writes. Degrade-clean: an op whose inputs can't be built is
    simply omitted (never a crash)."""
    from .bootstrap import build_bootstrap
    from .govern.secrets import scan
    from .knowledge.grep_backend import GrepBackend
    from .progress import build_stage_badge

    ops: Dict[str, Callable[[], Any]] = {
        "statusline": lambda: build_stage_badge(surface),
        "briefing": lambda: build_bootstrap(surface),
        "secret_scan": lambda: scan(text=_SCAN_SAMPLE),
        "status": lambda: [r.summary() for r in surface.router.resolve_all()],
    }

    grep = GrepBackend(surface.root)
    ops["grep_query"] = lambda: grep.query("callers", "compute")

    try:
        from .memory import MemoryStore
        from .memory.brain import jit_recall
        store = MemoryStore.from_surface(surface)
        ops["recall"] = lambda: jit_recall(store, "compute helper relevance context")
    except Exception:
        pass  # memory layer absent/disabled -> skip the recall op (degrade-clean)

    return ops


def run_benchmarks(surface: Any, *, repeat: int = DEFAULT_REPEAT,
                   warmup: int = DEFAULT_WARMUP,
                   only: Optional[List[str]] = None) -> List[BenchResult]:
    """Benchmark every hot op against its budget. Read-only; deterministic order."""
    relax = relax_factor()
    results: List[BenchResult] = []
    for name, fn in build_hot_ops(surface).items():
        if only and name not in only:
            continue
        med, mn, mx = measure(fn, repeat=repeat, warmup=warmup)
        results.append(BenchResult(name=name, runs=repeat, median_ms=med, min_ms=mn,
                                   max_ms=mx, budget_ms=LATENCY_BUDGETS_MS.get(name, 0.0),
                                   relax=relax))
    return results


def render_report(results: List[BenchResult], *, ascii_only: bool = False) -> str:
    """A compact, read-only latency report (the `mokata bench` body)."""
    if not results:
        return "bench: nothing to measure."
    relax = results[0].relax
    head = "mokata bench — wall-clock latency vs budget (median of "
    head += f"{results[0].runs}"
    head += f", relax ×{relax:g})" if relax != 1.0 else ")"
    lines = [head]
    lines += ["  " + r.render(ascii_only=ascii_only) for r in results]
    over = [r.name for r in results if not r.within_budget]
    if over:
        lines.append(f"  OVER BUDGET: {', '.join(over)}")
    else:
        lines.append("  all hot paths within budget.")
    return "\n".join(lines)
