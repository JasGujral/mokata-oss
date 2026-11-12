"""0.0.28 F `SPEC-LEG-IN-MERGE-GATE` — the spec legs of `ci_check`, run as merge evidence.

The gated merge (P21) counts what mokata's own runners RECORDED, stamped at the head being merged.
The full suite was one such runner (`merge_suite.run_full_suite`); this is the second. It runs the
spec-awareness and spec-drift legs of `ci_check` IN THE SESSION WORKTREE (its files, at its head —
never the main checkout or the caller's cwd) over the change `base..H`, stamps the tree before and
after as git says it is, and records the verdict per leg with what it was valid for:

    {worktree, head, head_before, base, base_after, clean_before, clean_after,
     files_sha (the change), corpus_fp (the saved spec corpus), legs: [...], at}

`merge_gate.evidence` reads that ONE record three-state (ok / absent / unreadable) and judges it;
the plan never runs a leg. `completeness` is not part of it: P3 already asks "does every criterion
have a passing test, in THIS full run", with stronger evidence (one door per concern).

The decision corpus (memory) has no cheap fingerprint, so the record is held to the review's
freshness bound instead (`settings.review.verdict_max_age_hours`, 24 h by default): a decision
recorded since is caught by re-running the leg within that bound.

A cancelled leg records nothing: the trailing checkpoint of `run_ci_check` and the one before the
write here both raise `Cancelled` through. Copyright 2026 MoStack. Licensed under the Apache
License, Version 2.0.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any, Dict, List, Tuple

from .errors import MokataError
from .knowledge.cancel import checkpoint, report

#: The legs the merge counts, in the order they run (`ci_check.LEG_NAMES` names).
LEGS = ("spec-awareness", "spec-drift")
PREFIX = "spec_leg__"


class SpecLegError(MokataError):
    """The spec legs cannot be run as asked (the target is not a session worktree, no run is bound
    to it, git cannot say where the tree is, the worktree's config cannot be loaded). Nothing was
    recorded; the message names the fix."""


def key(run_id: str) -> str:
    return PREFIX + run_id


def files_digest(changes: List[Tuple[str, str, str, str]]) -> str:
    """The change `base..H` (`merge_gate._changes` rows), in one order: what the leg looked at."""
    rows = sorted([list(r) for r in changes], key=lambda r: r[3])
    return hashlib.sha256(json.dumps(rows).encode("utf-8", "surrogateescape")).hexdigest()


def corpus_fp(main: str) -> str:
    """The saved spec corpus `spec-awareness` reads from the shared store (the emitted spec and the
    archived corpus), by the bytes of its files. Committed specs are files AT H (the record's head
    pins them); decisions are held to the freshness bound (module doc)."""
    from .engine.spec_awareness import SPEC_CORPUS_KEY
    from .engine.spec_gate import SPEC_STATE_KEY
    from .state import StateStore
    from .tdd_state import state_dir
    store = StateStore(state_dir(main))
    h = hashlib.sha256()
    for name in (SPEC_STATE_KEY, SPEC_CORPUS_KEY):
        h.update(name.encode("utf-8") + b"\0")
        try:
            with open(store.path(name), "rb") as fh:
                h.update(hashlib.sha256(fh.read()).digest())
        except FileNotFoundError:
            h.update(b"absent")
        except OSError as exc:
            h.update(f"unreadable:{type(exc).__name__}".encode("utf-8"))
    return h.hexdigest()


def _leg_row(leg: Any) -> Dict[str, Any]:
    return {"name": leg.name, "status": leg.status, "degraded": bool(leg.degraded),
            "summary": str(leg.summary or ""), "unblock": leg.unblock or "",
            "detail": [str(d) for d in (leg.detail or [])][:20]}


def run_spec_leg(root: str, target: str) -> Dict[str, Any]:
    """Run the spec legs in the session worktree `target` (path or branch) and record them against
    the run bound to it. Returns the record plus `{run, recorded}`. Raises `SpecLegError` (nothing
    recorded) or `Cancelled` (nothing recorded)."""
    from .ci_check import run_ci_check
    from .git_safe import Git, GitSafeError
    from .merge_gate import _changes, _runs_bound, find_target, tree_stamp
    from .repo_identity import canonical_repo_root
    from .state import StateStore
    from .tdd_state import state_dir
    main = canonical_repo_root(root)
    try:
        with Git(main) as git:
            row, why = find_target(git, main, target)
            if row is None:
                raise SpecLegError(why)
            wt = os.path.realpath(str(row["worktree"]))
            runs = _runs_bound(main, wt)
            if len(runs) != 1:
                raise SpecLegError("no mokata run is bound to this worktree — there is no record to "
                                   "put the spec legs in" if not runs else
                                   f"{len(runs)} runs are bound to this worktree — ambiguous")
            before = tree_stamp(wt, git)
            if before.get("error") or not before.get("head") or not before.get("base"):
                raise SpecLegError("git could not say where the worktree is "
                                   f"({before.get('error') or 'no head or base'}) — nothing was "
                                   "checked")
            changes, err = _changes(git, main, before["base"], before["head"])
            if changes is None:
                raise SpecLegError(f"the change could not be listed: {err}")
    except GitSafeError as exc:
        raise SpecLegError(str(exc)) from exc
    paths = [p for _o, _n, _s, p in changes]
    corpus = corpus_fp(main)
    checkpoint()
    report(note=f"spec legs ({', '.join(LEGS)}) over {len(paths)} changed file(s) in {wt}")
    result = run_ci_check(wt, paths, legs=LEGS)
    if not result.initialized:
        raise SpecLegError("mokata's config could not be loaded in the worktree — nothing was "
                           "checked (`mokata doctor`)")
    after = tree_stamp(wt)
    checkpoint()                    # a Cancel that arrived during the last leg: record nothing
    rec = {"worktree": wt, "head": after.get("head"), "head_before": before.get("head"),
           "base": before.get("base"), "base_after": after.get("base"),
           "clean_before": before.get("clean"), "clean_after": after.get("clean"),
           "files_sha": files_digest(changes), "corpus_fp": corpus,
           "legs": [_leg_row(leg) for leg in result.legs], "at": time.time()}
    StateStore(state_dir(main)).write(key(runs[0]), rec)
    return {**rec, "run": runs[0], "recorded": True}


def run_suite_and_leg(root: str, target: str) -> Dict[str, Any]:
    """`mokata worktree test` and the IDE's Run suite: the full suite, then the spec legs, in ONE
    job. A red suite does not skip the legs — both records are written and both are shown. A suite
    that could not run at all still lets the legs run, then fails the job naming why."""
    from . import merge_suite as MS
    try:
        res = MS.run_full_suite(root, target)
        suite_err = None
    except MS.SuiteError as exc:
        res, suite_err = {}, exc
    try:
        leg = run_spec_leg(root, target)
    except SpecLegError:
        if suite_err is not None:
            raise suite_err from None
        raise
    if suite_err is not None:
        raise MS.SuiteError(f"{suite_err} (the spec legs ran and were recorded: "
                            + ", ".join(f"{x['name']} {x['status']}" for x in leg["legs"]) + ")")
    return {**res, "spec_leg": leg}


__all__ = ["LEGS", "PREFIX", "SpecLegError", "corpus_fp", "files_digest", "key", "run_spec_leg",
           "run_suite_and_leg"]
