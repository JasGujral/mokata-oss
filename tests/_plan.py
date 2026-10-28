"""R2 (0.0.26) — the task plan every spec now carries, for test fixtures.

A spec without `tasks[]` and a plan hash is not the 0.0.26 shape: the wire refuses it and the gates
refuse it loudly when they find one persisted. Tests that are about something ELSE (scope, TDD,
re-emit, review) give their spec the smallest honest plan — one task covering every criterion,
scoped to the spec's authorized surface (or everything, `*`) — through these helpers, so the plan
is added in one place and the test still says what it is about.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from typing import Any, Dict, List

import _support  # noqa: F401  (puts src/ on the path)

from mokata.plan_tasks import TASKS_KEY, Task, seal


def _ac_ids(criteria: Any) -> List[str]:
    out = []
    for c in criteria if isinstance(criteria, list) else []:
        cid = c.get("id") if isinstance(c, dict) else getattr(c, "id", None)
        if cid:
            out.append(str(cid))
    return out


def one_task(criteria: Any, scope: Any = None) -> List[Dict[str, Any]]:
    """The one-task plan: every criterion, the given scope (default: the whole repo)."""
    authorized = list((scope or {}).get("authorized") or []) if isinstance(scope, dict) else []
    return [{"id": "T1", "title": "the change", "ac_ids": _ac_ids(criteria),
             "scope": authorized or ["*"]}]


def planned(payload: Dict[str, Any]) -> Dict[str, Any]:
    """`payload` (a spec_emit / spec amend wire dict) with the one-task plan, unless it has one."""
    if isinstance(payload, dict) and TASKS_KEY not in payload and "criteria" in payload:
        payload = dict(payload)
        payload[TASKS_KEY] = one_task(payload.get("criteria"), payload.get("scope"))
    return payload


def call_planned(tool, **kwargs):
    """`tool(**kwargs)` with the one-task plan added to a spec_emit / spec_amend call that sends
    criteria (a redeem-by-id call that sends none is passed through untouched)."""
    return tool(**planned(kwargs))


def sealed(data: Dict[str, Any]) -> Dict[str, Any]:
    """A PERSISTED spec dict as the committer writes it: the one-task plan (unless it has one) and
    the plan hash. For fixtures that write `emitted_spec__<run>` directly."""
    out = dict(data)
    if TASKS_KEY not in out:
        out[TASKS_KEY] = one_task(out.get("criteria"), out.get("scope"))
    return seal(out)


def with_tasks(spec: Any) -> Any:
    """A `Spec` object built in code, given the one-task plan (unless it has one). Returns it."""
    if not spec.tasks:
        authorized = tuple(spec.scope.authorized) if spec.scope is not None else ()
        spec.tasks = [Task(id="T1", title="the change", ac_ids=tuple(spec.ac_ids),
                           scope=authorized or ("*",))]
    return spec


def commit_planned(store, spec, **kwargs):
    """`engine.emit.spec_commit` for a fixture `Spec` built in code, given the one-task plan."""
    from mokata.engine.emit import spec_commit
    return spec_commit(store, with_tasks(spec), **kwargs)


def emit_planned(surface, spec, tests, **kwargs):
    """`engine.emit.emit_spec` for a fixture `Spec` built in code, given the one-task plan."""
    from mokata.engine.emit import emit_spec
    return emit_spec(surface, with_tasks(spec), tests, **kwargs)
