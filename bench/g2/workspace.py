"""Fresh clone per run — zero state bleed between runs (G2 methodology bound).

`GitCloner` shallow-clones the task's pinned repo into a NEW directory for every run and records
the HEAD sha it got. A TEMPLATE task is first materialized into a local git repo (fixed author and
dates, so its sha is stable), then cloned over `file://` like any remote — the run never works in
the template itself.

`CopyCloner` is the test double: a plain directory copy, sha recorded as "fixture".

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Tuple

from .tasks import Task

_FIXED_GIT_ENV = {"GIT_AUTHOR_NAME": "g2", "GIT_AUTHOR_EMAIL": "g2@example.invalid",
                  "GIT_COMMITTER_NAME": "g2", "GIT_COMMITTER_EMAIL": "g2@example.invalid",
                  "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
                  "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z"}


def _git(args, cwd: str, env_extra: Dict[str, str] = None) -> str:
    env = dict(os.environ)
    env.update(env_extra or {})
    out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=env,
                         timeout=600)
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {out.stderr.strip()}")
    return out.stdout.strip()


class GitCloner:
    id = "git"

    def __init__(self, cache_dir: str):
        self.cache_dir = cache_dir
        self._materialized: Dict[str, str] = {}

    def _source(self, task: Task) -> Tuple[str, str]:
        tmpl = task.template_dir()
        if tmpl:
            if tmpl not in self._materialized:
                repo = os.path.join(self.cache_dir, "templates", task.repo["template"].replace("/", "_"))
                if os.path.exists(repo):
                    shutil.rmtree(repo)
                shutil.copytree(tmpl, repo)
                _git(["init", "-q"], repo)
                _git(["add", "-A"], repo)
                _git(["-c", "commit.gpgsign=false", "commit", "-q", "-m", "g2 template"], repo,
                     _FIXED_GIT_ENV)
                self._materialized[tmpl] = repo
            return Path(self._materialized[tmpl]).as_uri(), "HEAD"
        return task.repo["url"], task.repo["ref"]

    def fresh_clone(self, task: Task, dest: str) -> str:
        url, ref = self._source(task)
        if ref == "HEAD":
            _git(["clone", "-q", "--depth", "1", url, dest], os.path.dirname(dest))
        else:
            # A pinned sha: fetch exactly that commit, shallow.
            os.makedirs(dest)
            _git(["init", "-q"], dest)
            _git(["fetch", "-q", "--depth", "1", url, ref], dest)
            _git(["-c", "advice.detachedHead=false", "checkout", "-q", "FETCH_HEAD"], dest)
        return _git(["rev-parse", "HEAD"], dest)


class CopyCloner:
    id = "copy-fixture"

    def fresh_clone(self, task: Task, dest: str) -> str:
        tmpl = task.template_dir()
        if not tmpl:
            raise RuntimeError("CopyCloner only serves template tasks")
        shutil.copytree(tmpl, dest)
        return "fixture"
