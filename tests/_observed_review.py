"""Test support (0.0.22 D4 ruling): an "independent" review verdict is credited only when a subagent
of the run was OBSERVED finishing. Tests of the credited-independent path call this BEFORE they record
the verdict, standing in for the SubagentStop hook that records a real reviewer subagent."""
import _support  # noqa: F401  (puts src/ on the path)

from mokata.config import Surface
from mokata.progress_events import _resolve_verdict_run, record_subagent_finished
from mokata.subagent_contract import HandbackReport


def observe_review_subagent(root, run_id=None):
    """Record a reviewer subagent of `run_id` (default: the run a verdict would resolve to) as finished."""
    surface = Surface.load(root)
    rid = run_id or _resolve_verdict_run(surface)
    assert rid, "no run to observe a subagent for — register the run first"
    return record_subagent_finished(
        surface.state.root, rid, {"agent_id": "test-reviewer", "agent_type": "general-purpose"},
        HandbackReport("done", "reviewed the diff against the spec"))
