"""0.0.27 P21 — mokata's full-suite runner: the only test run the gated merge counts (P2).

It runs the project's configured test command (`settings.baseline.test_command`) UNFILTERED — no
test ids appended — in the session worktree, in the project's env, without a shell. Around the run
it stamps the tree as git says it is (worktree, HEAD, clean incl. untracked) BEFORE and AFTER, and
records the run with `source="runner"` and that stamp. A run whose HEAD moved, or whose tree was
dirty, is recorded honestly and then refused by the merge gate; a cancelled run records nothing.

When the run passes, the tests mapped to the run's acceptance criteria that EXIST at that head
(`test_runs.stamps_of` finds them) are recorded green — the suite ran them. A failing run records
only that it failed: which test failed is the runner's output, never a guess.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import subprocess
import time
from typing import Any, Dict, List, Optional

from .errors import MokataError
from .knowledge.cancel import Cancelled, checkpoint, report

OUTPUT_TAIL = 64 * 1024


class SuiteError(MokataError):
    """The full suite cannot be run as asked (no test command, shell syntax in it, no run bound to
    the worktree). The message names the fix."""


def _mapped_existing(worktree: str, main: str, run_id: str) -> List[str]:
    from .state import StateStore
    from .tdd_state import state_dir
    from .test_runs import _map, key, stamps_of
    rec = StateStore(state_dir(main)).read(key(run_id)) or {}
    ids = sorted({t for v in _map(rec.get("ac_tests")).values() for t in v}
                 | {t for v in _map(rec.get("planned")).values() for t in v})
    stamps = stamps_of(worktree, ids)
    return [t for t in ids if stamps.get(t) is not None]


def run_full_suite(root: str, target: str, *, environ: Optional[Dict[str, str]] = None
                   ) -> Dict[str, Any]:
    """Run the full suite in the session worktree `target` and record it against the run bound to
    that worktree. Returns `{passed, exit_code, command, tree, output, recorded}`."""
    from .git_safe import Git
    from .ide.review_context import (_drive, _kill_tree, _manifest, _path_of, ide_actor,
                                     resolve_runner, test_command)
    from .merge_gate import _runs_bound, find_target, tree_stamp
    from .ide.lsp import TreeWatch, spawn_jobbed
    from .repo_identity import canonical_repo_root
    from . import project_env
    from .test_runs import record_test_run
    main = canonical_repo_root(root)
    with Git(main) as git:
        row, why = find_target(git, main, target)
        if row is None:
            raise SuiteError(why)
        wt = os.path.realpath(str(row["worktree"]))
        runs = _runs_bound(main, wt)
        if len(runs) != 1:
            raise SuiteError("no mokata run is bound to this worktree — there is no record to put "
                             "the run in" if not runs else
                             f"{len(runs)} runs are bound to this worktree — ambiguous")
        cmd = test_command(main)
        if not cmd["runnable"]:
            raise SuiteError(cmd["note"])
        before = tree_stamp(wt, git)
    man, _problem = _manifest(main)
    base_env = dict(os.environ if environ is None else environ)
    penv = project_env.resolve(main, project_env.settings_python(man), base_env)
    run_env = project_env.activated(base_env, penv)
    # The ONE resolver the preflight and the IDE re-run use: a bare name is found on absolute PATH
    # entries only (never the cwd), on Windows npm/npx/pnpm/yarn run through node and their own JS
    # entry, and a `.cmd`/`.bat` is never started (cmd.exe would re-parse the arguments).
    head, why = resolve_runner(main, cmd["argv"][0], _path_of(run_env), None,
                               list(penv.bin_dirs) if penv is not None else [])
    if head is None:
        raise SuiteError(why or "the test command could not be resolved")
    argv = [*head, *cmd["argv"][1:]]
    out = {"tail": bytearray(), "bytes": 0}

    def take(chunk: bytes) -> None:
        out["bytes"] += len(chunk)
        out["tail"].extend(chunk)
        if len(out["tail"]) > OUTPUT_TAIL:
            del out["tail"][:len(out["tail"]) - OUTPUT_TAIL]

    def tick() -> None:
        report(note="running the full suite", partial={
            "output": out["tail"].decode("utf-8", "replace"), "output_bytes": out["bytes"]})

    checkpoint()
    report(note=f"running `{cmd['text']}` in {wt}")
    job = None
    try:
        # The same spawn the IDE re-run uses: POSIX a new session; Windows created suspended, put
        # in a KILL_ON_JOB_CLOSE Job Object, then resumed — so nothing the suite starts can outlive
        # it, hold the worktree open (the post-merge `git worktree remove` fails on Windows while
        # anything runs there) or keep this run alive by writing to the pipe (0.0.27 LB-M2).
        proc, job = spawn_jobbed(argv, cwd=wt, env=run_env,  # nosec B603 — argv, no shell
                                 stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT)
    except OSError as exc:
        raise SuiteError(f"the test command could not start: {exc}") from exc
    watch = TreeWatch(proc.pid) if os.name == "nt" and job is not None and not job.ok else None
    try:
        rc = _drive(proc, take, tick, job, watch)
    except Cancelled:
        _kill_tree(proc, job=job, watch=watch)
        raise                                   # a cancelled run records nothing
    finally:
        if job is not None:
            job.terminate()                     # releases the handle; a no-op once the tree is ended
    after = tree_stamp(wt)
    tree = {"scope": "full", "worktree": wt,
            "head_before": before.get("head"), "head_after": after.get("head"),
            "clean_before": before.get("clean"), "clean_after": after.get("clean"),
            "base": after.get("base")}
    passed = rc == 0
    greens = _mapped_existing(wt, main, runs[0]) if passed else []
    record_test_run(wt, runs[0], passed=passed, command=cmd["text"],
                    results={t: True for t in greens} or None, source="runner",
                    actor=ide_actor(), exit_code=rc, tree=tree, at=time.time())
    return {"passed": passed, "exit_code": rc, "command": cmd["text"], "tree": tree,
            "run": runs[0], "greened": greens, "output": out["tail"].decode("utf-8", "replace"),
            "output_bytes": out["bytes"], "recorded": True}


__all__ = ["SuiteError", "run_full_suite"]
