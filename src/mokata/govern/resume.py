"""I6 — resume / recovery.

Pipeline progress is persisted to the state store after each passed gate, so an
interrupted run resumes from the last passed gate rather than the start — a crash never
loses state. Built on the state store + the spine's PIPELINE_PHASES.
"""

from __future__ import annotations

from datetime import datetime, timezone
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
        # B-LIFE — WHEN the run finished (it reached the terminal `ship` stage), stamped once by
        # `mark_completed`. Keyed on SHIP, NOT the final pipeline phase: a complete checkpoint means
        # the spec emitted and the user is AT develop (active), not that the run finished. Additive —
        # old checkpoints without the key read as None; old readers of `{run_id, passed}` are
        # unaffected. Absent stamp never means "not finished" (a legacy shipped run may lack it); it
        # only records the WHEN when present.
        ca = data.get("completed_at") if isinstance(data, dict) else None
        self.completed_at: Optional[str] = ca if isinstance(ca, str) and ca else None

    def _save(self) -> None:
        payload = {"run_id": self.run_id, "passed": self.passed}
        if self.completed_at:
            payload["completed_at"] = self.completed_at
        self.store.write(self.key, payload)

    def ensure_registered(self) -> bool:
        """PH-GATE.S0 / RUN-REG — REGISTER this run: persist an empty checkpoint iff none exists yet,
        so `progress`/`spec` find the run and the phase gate has state to bind on. Idempotent and
        non-destructive: an already-persisted checkpoint (any `passed`) is left exactly as it is, so
        re-registering can never reset progress. Returns True only when it created the checkpoint.

        This registers RUN STATE, not a durable artifact (the checkpoint is transient run-tracking
        under temp_local, keyed by run_id) — so it stays UNGATED, exactly like `mark_passed`."""
        if self.store.read(self.key) is not None:
            return False
        self._save()
        if self.ledger is not None:
            self.ledger.record("checkpoint", run=self.run_id, phase="registered")
        return True

    def mark_passed(self, phase: str) -> None:
        """Record a passed gate and persist immediately (crash-safe)."""
        if phase not in self.passed:
            self.passed.append(phase)
            self._save()
            if self.ledger is not None:
                self.ledger.record("checkpoint", run=self.run_id, phase=phase)

    def mark_completed(self) -> bool:
        """B-LIFE — stamp `completed_at` when the run reaches its terminal END-OF-RUN signal (the
        `stage_enter: ship` recorded by `mokata progress mark ship`), so `build_progress` / the
        dashboard can report the run as finished-THEN rather than current-NOW.

        Keyed on SHIP, not on the final pipeline phase: a complete pipeline checkpoint means the spec
        emitted and the user is AT `develop` (a healthy ACTIVE state), NOT that the run finished —
        develop/review/ship live in the progress-event log, not this checkpoint. Idempotent (a set
        stamp is left as-is), additive (old `{run_id, passed}` readers are unaffected), persisted
        immediately (crash-safe). Returns True only when it newly stamped. DISPLAY-only run-state:
        it records WHEN a run finished, never gates or deletes anything (P17)."""
        if self.completed_at is not None:
            return False
        self.completed_at = datetime.now(timezone.utc).isoformat()
        self._save()
        return True

    def regress(self, drop) -> bool:
        """SI-DEV — a FORCED phase regression: un-pass `drop` so those gates must run again.

        `mokata spec amend` regresses `develop -> SPEC` by dropping `completeness_gate` and `emit`:
        the amended spec has to re-earn both. Everything passed EARLIER stays passed, so
        `resume_phase()` returns the first gate that must re-run rather than the start of the
        pipeline — which is exactly P17 ("resume from the last PASSED gate, not from scratch").
        Idempotent, and persisted immediately (crash-safe), like `mark_passed`."""
        drop = set(drop)
        kept = [p for p in self.passed if p not in drop]
        if kept == self.passed:
            return False
        dropped = [p for p in self.passed if p in drop]
        self.passed = kept
        self._save()
        if self.ledger is not None:
            self.ledger.record("checkpoint", run=self.run_id, phase="regress", dropped=dropped)
        return True

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
