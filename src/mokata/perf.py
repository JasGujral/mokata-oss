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
import sys
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

# HOOK-BUDGET (doc 85 §7l, doc 109 §17.2; enforced from 0.0.25 by tests/test_hb_hook_budget.py for the
# `mokata-hook` COMMAND hooks and tests/test_id_s3_cli_ide_bridge.py for the ID.S3 http path).
# p95 of ONE hook invocation as the harness experiences it — a fresh process, payload on stdin — in ms.
#
# TARGETS (doc 109 §17.2): an observability hook ≤ 50, a gate hook ≤ 150. `context` (the hooks that
# inject text — SessionStart, UserPromptSubmit, SubagentStart, the statusline) ≤ 150: RULED (U14,
# 0.0.26), no longer a proposal.
#
# OBSERVE hooks are measured through the `mokata-hook` front door (`mokata_emit`, 0.0.26 O3 / U13):
# stdlib only, the event spooled and drained by a detached process off the harness's clock.
HOOK_BUDGET_TARGETS_MS: Dict[str, float] = {"observe": 50.0, "gate": 150.0, "context": 150.0}

#: Every wired `mokata-hook` subcommand → its doc 109 kind. A hook that can BLOCK or send a decision
#: (PreToolUse exit 2, SubagentStop's `decision: block`) is a gate; one that only records is observe.
HOOK_KINDS: Dict[str, str] = {
    "secret-guard": "gate", "gate-guard": "gate", "subagent-stop": "gate", "stop-gate": "gate",
    "dirty-track": "observe", "run-bind": "observe", "stop": "observe", "session-end": "observe",
    "pre-compact": "observe",
    "session-start": "context", "user-prompt-submit": "context", "subagent-start": "context",
    "statusline": "context",
}

#: 0.0.29 F (W5-HOOK-BUDGET-MARGIN-THIN, ALLOWED-REDS-A12-A9-O12-O12B) — ONE MEASURED CEILING per
#: wired hook per fixture PER OS (`sys.platform`), keyed `<sub>@<fixture>`: `outside` a mokata repo,
#: a mokata `repo` with no run, a session `bound` to a run; and SessionStart at the progress log's
#: 8 MiB bound (`<fixture>-8mib-miss` — a fresh session and no stored next-step answer, the path real
#: starts take — and `<fixture>-8mib-hit`). Re-derived per OS at the 0.0.29 wave-2 bookend (ruled by
#: Jas 2026-11-16) from the worst NORMALISED p95 measured on that OS — darwin: a Mac and the mirror's
#: macOS runner; linux and win32: the mirror's runners, both jsonschema legs:
#:   * darwin: `ceiling_from(worst)` — 1.5x, up to 5 ms, never above the kind's target unless the key
#:     is an owned gap (`HOOK_BUDGET_OWNED_GAPS`). A 2x regression reds.
#:   * linux / win32: `ceiling_off_darwin(worst, darwin)` — 1.5x up to 5 ms, never below the darwin
#:     ceiling x `OFF_DARWIN_BUFFER`. A 3x regression reds.
#: The measurements are committed (`tests/data/hook_budget_measured.json`, with the runs they came
#: from); a test re-derives every value here from them and reds on a typed or raised one (never edit
#: by hand — re-measure: `python tests/test_f_hook_ceilings.py --from <hb log>...`).
HOOK_BUDGET_CEILINGS_MS: Dict[str, Dict[str, float]] = {
    "darwin": {
        "dirty-track@bound": 50.0,
        "dirty-track@outside": 50.0,
        "dirty-track@repo": 45.0,
        "gate-guard@bound": 115.0,
        "gate-guard@outside": 100.0,
        "gate-guard@repo": 95.0,
        "pre-compact@bound": 50.0,
        "pre-compact@outside": 45.0,
        "pre-compact@repo": 50.0,
        "run-bind@bound": 50.0,
        "run-bind@outside": 45.0,
        "run-bind@repo": 50.0,
        "secret-guard@bound": 80.0,
        "secret-guard@outside": 85.0,
        "secret-guard@repo": 95.0,
        "session-end@bound": 50.0,
        "session-end@outside": 45.0,
        "session-end@repo": 50.0,
        "session-start@bound": 145.0,
        "session-start@bound-8mib-hit": 290.0,
        "session-start@bound-8mib-miss": 365.0,
        "session-start@outside": 85.0,
        "session-start@repo": 130.0,
        "session-start@repo-8mib-hit": 125.0,
        "session-start@repo-8mib-miss": 215.0,
        "statusline@bound": 120.0,
        "statusline@outside": 70.0,
        "statusline@repo": 115.0,
        "stop-gate@bound": 85.0,
        "stop-gate@outside": 45.0,
        "stop-gate@repo": 70.0,
        "stop@bound": 45.0,
        "stop@outside": 45.0,
        "stop@repo": 45.0,
        "subagent-start@bound": 70.0,
        "subagent-start@outside": 55.0,
        "subagent-start@repo": 80.0,
        "subagent-stop@bound": 75.0,
        "subagent-stop@outside": 50.0,
        "subagent-stop@repo": 70.0,
        "user-prompt-submit@bound": 150.0,
        "user-prompt-submit@outside": 70.0,
        "user-prompt-submit@repo": 150.0,
    },
    "linux": {
        "dirty-track@bound": 100.0,
        "dirty-track@outside": 100.0,
        "dirty-track@repo": 90.0,
        "gate-guard@bound": 230.0,
        "gate-guard@outside": 200.0,
        "gate-guard@repo": 190.0,
        "pre-compact@bound": 100.0,
        "pre-compact@outside": 90.0,
        "pre-compact@repo": 100.0,
        "run-bind@bound": 100.0,
        "run-bind@outside": 90.0,
        "run-bind@repo": 100.0,
        "secret-guard@bound": 160.0,
        "secret-guard@outside": 170.0,
        "secret-guard@repo": 190.0,
        "session-end@bound": 100.0,
        "session-end@outside": 90.0,
        "session-end@repo": 100.0,
        "session-start@bound": 290.0,
        "session-start@bound-8mib-hit": 580.0,
        "session-start@bound-8mib-miss": 730.0,
        "session-start@outside": 170.0,
        "session-start@repo": 260.0,
        "session-start@repo-8mib-hit": 250.0,
        "session-start@repo-8mib-miss": 430.0,
        "statusline@bound": 240.0,
        "statusline@outside": 140.0,
        "statusline@repo": 230.0,
        "stop-gate@bound": 170.0,
        "stop-gate@outside": 90.0,
        "stop-gate@repo": 140.0,
        "stop@bound": 90.0,
        "stop@outside": 90.0,
        "stop@repo": 90.0,
        "subagent-start@bound": 140.0,
        "subagent-start@outside": 110.0,
        "subagent-start@repo": 160.0,
        "subagent-stop@bound": 150.0,
        "subagent-stop@outside": 100.0,
        "subagent-stop@repo": 140.0,
        "user-prompt-submit@bound": 300.0,
        "user-prompt-submit@outside": 140.0,
        "user-prompt-submit@repo": 300.0,
    },
    "win32": {
        "dirty-track@bound": 100.0,
        "dirty-track@outside": 100.0,
        "dirty-track@repo": 90.0,
        "gate-guard@bound": 230.0,
        "gate-guard@outside": 200.0,
        "gate-guard@repo": 190.0,
        "pre-compact@bound": 105.0,
        "pre-compact@outside": 90.0,
        "pre-compact@repo": 100.0,
        "run-bind@bound": 115.0,
        "run-bind@outside": 90.0,
        "run-bind@repo": 115.0,
        "secret-guard@bound": 160.0,
        "secret-guard@outside": 170.0,
        "secret-guard@repo": 190.0,
        "session-end@bound": 100.0,
        "session-end@outside": 90.0,
        "session-end@repo": 100.0,
        "session-start@bound": 290.0,
        "session-start@bound-8mib-hit": 580.0,
        "session-start@bound-8mib-miss": 730.0,
        "session-start@outside": 170.0,
        "session-start@repo": 260.0,
        "session-start@repo-8mib-hit": 250.0,
        "session-start@repo-8mib-miss": 430.0,
        "statusline@bound": 240.0,
        "statusline@outside": 140.0,
        "statusline@repo": 230.0,
        "stop-gate@bound": 170.0,
        "stop-gate@outside": 90.0,
        "stop-gate@repo": 140.0,
        "stop@bound": 95.0,
        "stop@outside": 90.0,
        "stop@repo": 90.0,
        "subagent-start@bound": 140.0,
        "subagent-start@outside": 110.0,
        "subagent-start@repo": 160.0,
        "subagent-stop@bound": 150.0,
        "subagent-stop@outside": 100.0,
        "subagent-stop@repo": 140.0,
        "user-prompt-submit@bound": 300.0,
        "user-prompt-submit@outside": 140.0,
        "user-prompt-submit@repo": 300.0,
    },
}

#: The keys whose ceiling may exceed the kind's target — each owned, with its doc 84 row. Nothing
#: else may: a ceiling above its target with no owner is a typed number wearing a measurement.
HOOK_BUDGET_OWNED_GAPS: Dict[str, str] = {
    "session-start@bound-8mib-miss": "SESSIONSTART-DERIVE-AT-LOG-BOUND (doc 84) — the next-step "
                                     "derivation walks the 8 MiB log on a miss; owner: hints",
    "session-start@repo-8mib-miss": "SESSIONSTART-DERIVE-AT-LOG-BOUND (doc 84) — owner: hints",
    "session-start@bound-8mib-hit": "SESSIONSTART-REANCHOR-AT-LOG-BOUND (doc 84) — `/compact` "
                                    "re-anchors from the 8 MiB log even when the next step is a "
                                    "stored hit; owner: reanchor",
}

#: The ceiling rule's two numbers (the 0.0.25 HB rule, re-applied in 0.0.29 F).
CEILING_HEADROOM = 1.5
CEILING_STEP_MS = 5.0

#: o12 and the SessionStart 8 MiB cells assert at this fraction of their bound, not at it: a cell
#: that passes at 399 of 400 is a cell that flakes on the runners (F-Q5).
MARGIN = 0.9

#: The first hook after an install (or a new plugin version) compiles mokata's modules into the
#: empty bytecode cache — measured 242 ms on the prompt hook (warm: 107). Graded, not unbounded
#: (F-Q3): 4× the measured cold cost, so a hook that starts compiling the package reds.
FIRST_RUN_BUDGET_MS = 1000.0


def ceiling_from(worst_p95_ms: float, kind_target_ms: float, owned: bool = False) -> float:
    """The ceiling a measured worst normalised p95 derives: 1.5×, up to the next 5 ms, capped at the
    kind's target unless `owned` (an owned gap)."""
    import math
    raw = math.ceil(worst_p95_ms * CEILING_HEADROOM / CEILING_STEP_MS) * CEILING_STEP_MS
    return float(raw if owned else min(raw, kind_target_ms))


def margin_relax() -> float:
    """The relax applied to the hook-budget cells: ALWAYS 1.0 (0.0.29 F, HOOK-BUDGET-CI-RELAXED).
    Each cell divides by the bare interpreter measured interleaved with it
    (`interpreter_factor`), which is what runner noise moves; a 4× CI multiplier on top let the
    8 MiB cell pass at 1,476 ms. A probe leg that reds records its factor and a doc 84 row."""
    return 1.0

#: `python -c pass` p50 wall on the Linux bed the targets were measured on, idle (0.0.25 Lane HB:
#: 15.5–16.1 ms). The budget test measures the same bare interpreter in the same minute and scales by
#: `max(1, measured / this)`: a hook's cost is mostly interpreter + imports, so a box running twice as
#: slow (load from parallel suites) doubles both — on an idle box of this speed the factor is 1 and
#: the raw targets apply. A regression in mokata's own cost moves the hook and not the floor.
REFERENCE_INTERPRETER_MS = 16.0

#: The ID.S3 http path (`observe_http`, and its IDE-closed refusal) — held to the observe target.
HOOK_BUDGET_P95_MS: Dict[str, float] = {
    "observe_http": 50.0,           # target
    "observe_http_closed": 50.0,    # target: IDE not running — must fail open fast
}


#: The floor off darwin (ruled by Jas 2026-11-16): a linux or win32 ceiling is never below the darwin
#: ceiling times this, whatever that OS measured; and a platform with no measured column is graded
#: at exactly darwin x this. One named number — no skip, no relax.
OFF_DARWIN_BUFFER = 2.0


def ceiling_off_darwin(worst_p95_ms: float, darwin_ceiling_ms: float) -> float:
    """The ceiling a measured worst normalised p95 derives on a measured OS other than darwin: 1.5x,
    up to the next 5 ms, never below the darwin ceiling x `OFF_DARWIN_BUFFER`. No target cap there:
    the buffer already puts every off-darwin ceiling past it (the ruling, not a measurement)."""
    import math
    raw = math.ceil(worst_p95_ms * CEILING_HEADROOM / CEILING_STEP_MS) * CEILING_STEP_MS
    return float(max(raw, darwin_ceiling_ms * OFF_DARWIN_BUFFER))


def hook_budget_ms(sub: str, fixture: str) -> "tuple":
    """`(ms, is_target)` for one wired hook on one fixture on THIS OS: its measured ceiling in this
    platform's column of `HOOK_BUDGET_CEILINGS_MS`; a platform with no column (or a key missing from
    it) is graded at the darwin ceiling x `OFF_DARWIN_BUFFER`. `is_target` True only when that
    ceiling IS the kind's target. A key with no darwin measurement falls to the target (a test reds
    on any key left unmeasured)."""
    target = HOOK_BUDGET_TARGETS_MS[HOOK_KINDS[sub]]
    key = f"{sub}@{fixture}"
    darwin = HOOK_BUDGET_CEILINGS_MS["darwin"].get(key, target)
    if sys.platform == "darwin":
        ceiling = darwin
    else:
        ceiling = HOOK_BUDGET_CEILINGS_MS.get(sys.platform, {}).get(key,
                                                                    darwin * OFF_DARWIN_BUFFER)
    return ceiling, ceiling == target


def hook_budget_holds(sub: str, fixture: str, p95_norm_ms: float) -> bool:
    """The hook-budget gate the hb cells grade with: the normalised p95 is within this OS's
    ceiling (`hook_budget_ms`) times `margin_relax()` (always 1.0). No platform is exempt."""
    return p95_norm_ms <= hook_budget_ms(sub, fixture)[0] * margin_relax()


def interpreter_factor(floor_p50_ms: float) -> float:
    """The load factor for a measured bare-interpreter p50 (see `REFERENCE_INTERPRETER_MS`)."""
    return max(1.0, floor_p50_ms / REFERENCE_INTERPRETER_MS)

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
    # Lane F — the budgeted op is `statusline_badge`, the line the harness ACTUALLY renders, not
    # `build_stage_badge`, which is one component of it. The distinction became load-bearing when
    # the composed line grew the awaiting segment: that segment reads the approval store off disk,
    # so the 50 ms ceiling now bounds an I/O path it previously did not reach at all. A budget
    # pointed at a component is a budget that does not cover the thing the user waits for.
    from .progress import statusline_badge

    ops: Dict[str, Callable[[], Any]] = {
        "statusline": lambda: statusline_badge(surface),
        "briefing": lambda: build_bootstrap(surface),
        "secret_scan": lambda: scan(text=_SCAN_SAMPLE),
        "status": lambda: [r.summary() for r in surface.router.resolve_all()],
    }

    grep = GrepBackend(surface.root)
    ops["grep_query"] = lambda: grep.query("callers", "compute")

    from sqlite3 import Error as SQLiteError
    try:
        from .memory import MemoryStore
        from .memory.brain import jit_recall
        store = MemoryStore.from_surface(surface)
        ops["recall"] = lambda: jit_recall(store, "compute helper relevance context")
    except (ImportError, OSError, SQLiteError):
        # ImportError: memory's optional deps are absent. OSError/sqlite3.Error: the local store
        # can't be opened. This is a BENCHMARK op list, not a capability: a skipped op means one
        # fewer row in the perf report, and `run_benchmarks` reports exactly the ops it was given.
        # Nothing degrades for the user — there is nothing to fall back FROM.
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
