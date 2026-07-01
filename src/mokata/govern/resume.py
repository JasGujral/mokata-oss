"""I6 — resume / recovery.

Pipeline progress is persisted to the state store after each passed gate, so an
interrupted run resumes from the last passed gate rather than the start — a crash never
loses state. Built on the state store + the spine's PIPELINE_PHASES.
"""

from __future__ import annotations

from typing import Any, List, Optional

from ..brainstorm import PIPELINE_PHASES

CHECKPOINT_PREFIX = "pipeline_run__"


class PipelineCheckpoint:
    def __init__(self, store: Any, run_id: str, ledger: Any = None) -> None:
        self.store = store
        self.run_id = run_id
        self.ledger = ledger
        self.key = CHECKPOINT_PREFIX + run_id
        data = store.read(self.key)
        # Degrade-clean: a corrupt or wrong-shape checkpoint (top-level not a mapping, or a
        # `passed` that isn't a list) reads as a fresh/empty run rather than crashing the
        # read-only progress/badge/lanes/resume hot paths (the documented "never raises"
        # contract). The state file is already broken; treating it as fresh is the safe floor.
        passed = data.get("passed") if isinstance(data, dict) else None
        self.passed: List[str] = list(passed) if isinstance(passed, list) else []

    def _save(self) -> None:
        self.store.write(self.key, {"run_id": self.run_id, "passed": self.passed})

    def mark_passed(self, phase: str) -> None:
        """Record a passed gate and persist immediately (crash-safe)."""
        if phase not in self.passed:
            self.passed.append(phase)
            self._save()
            if self.ledger is not None:
                self.ledger.record("checkpoint", run=self.run_id, phase=phase)

    def last_passed(self) -> Optional[str]:
        return self.passed[-1] if self.passed else None

    def resume_phase(self, phases=PIPELINE_PHASES) -> Optional[str]:
        """The phase to resume at: the first phase after the last passed one. The first
        phase for a fresh run; None when the run is complete."""
        if not self.passed:
            return phases[0]
        last = self.passed[-1]
        if last not in phases:
            return phases[0]
        i = phases.index(last)
        return phases[i + 1] if i + 1 < len(phases) else None

    def is_complete(self, phases=PIPELINE_PHASES) -> bool:
        return self.resume_phase(phases) is None
