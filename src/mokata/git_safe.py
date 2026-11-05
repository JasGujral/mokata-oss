"""0.0.27 P21 — git, run so that the REPO chooses nothing that executes (rule note §4).

A merge that a human approved must not run code the human was never shown. A repository can
choose code in five places, and each one is closed here:

  * the git BINARY — resolved once, to an absolute path, from absolute PATH entries outside the
    repo (`exe_path.which_in(..., refuse_under=<repo>)`); Windows' CreateProcess would otherwise
    search the cwd, which is the repo;
  * the ENVIRONMENT — every `GIT_*` variable is dropped (`GIT_DIR`, `GIT_INDEX_FILE`,
    `GIT_CONFIG_PARAMETERS`, `GIT_EXEC_PATH`, `GIT_EXTERNAL_DIFF`, `GIT_SSH*` …) and only the ones
    this module sets are present; stdin is closed;
  * HOOKS — `core.hooksPath` points at a fresh, private (0700), EMPTY directory made for this job,
    re-verified empty before every call and removed at the end; `reference-transaction` and every
    other hook therefore find nothing to run;
  * CONFIG that names a program — `-c` overrides (command-line values beat any file, `include.path`
    or `includeIf` value for the same key) switch off fsmonitor, external diff, signing programs and
    the ssh command;
  * ATTRIBUTES that name a filter or merge driver — judged by the caller (`merge_gate`) with
    `check-attr --source=<commit>` and REFUSED, never run.

Every call is cancellable from the job's flag (`knowledge.cancel`) unless the caller says the step
is past its commit point. Stdlib only. Copyright 2026 MoStack. Licensed under the Apache License,
Version 2.0.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .errors import MokataError
from .knowledge import cancel as C

#: `check-attr --source` (2.40) is what lets the merge judge the MERGED tree's attributes.
MIN_VERSION = (2, 40)

#: The `-c` overrides on every call. Command-line config wins over every config file and every
#: include, so no repo-local value of these keys can take effect.
OVERRIDES: Tuple[Tuple[str, str], ...] = (
    ("core.fsmonitor", "false"),
    ("core.untrackedCache", "false"),
    ("diff.external", ""),
    ("commit.gpgSign", "false"),
    ("tag.gpgSign", "false"),
    ("gpg.program", ""),
    ("gpg.ssh.program", ""),
    ("gpg.x509.program", ""),
    ("gpg.openpgp.program", ""),
    ("core.sshCommand", ""),
    ("core.pager", "cat"),
    ("core.askPass", ""),
    ("credential.helper", ""),
)


class GitSafeError(MokataError):
    """git cannot be run the safe way: not found outside the repo, too old, or the private hooks
    directory was tampered with. Each names the reason; none is ever retried silently."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def parse_version(text: str) -> Optional[Tuple[int, int, int]]:
    """`git version 2.50.1 (Apple Git-155)` / `git version 2.45.1.windows.1` -> (2, 50, 1)."""
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)


def resolve_git(repo: str, environ: Optional[Dict[str, str]] = None,
                nt: Optional[bool] = None) -> Optional[str]:
    """The absolute git binary a merge may run: from the user's absolute PATH entries, never the
    cwd, never inside `repo` (a planted `git`/`git.exe` in the repo root or a repo PATH entry)."""
    from .exe_path import path_dirs, which_in
    return which_in("git", path_dirs(environ, cwd=repo), nt=nt, refuse_under=repo)


def scrubbed_env(environ: Optional[Dict[str, str]] = None,
                 extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """The environment every P21 git call gets: the caller's, minus EVERY `GIT_*` variable, plus
    only what this module sets (`GIT_TERMINAL_PROMPT=0`, and per call e.g. `GIT_INDEX_FILE`)."""
    env = {k: v for k, v in (os.environ if environ is None else environ).items()
           if not k.upper().startswith("GIT_")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    for k, v in (extra or {}).items():
        env[k] = v
    return env


class Git:
    """One job's safe git: a resolved binary, a checked version, a private empty hooks directory.

    Use as a context manager; the hooks directory is removed on exit. `run` never raises for a
    non-zero exit (it returns the code); it raises `GitSafeError` when the safety conditions no
    longer hold, and `Cancelled` when the job was cancelled and the step is cancellable."""

    def __init__(self, repo: str, *, environ: Optional[Dict[str, str]] = None,
                 exe: Optional[str] = None) -> None:
        self.repo = repo
        self._environ = dict(os.environ if environ is None else environ)
        self.exe = exe or resolve_git(repo, self._environ)
        if not self.exe:
            raise GitSafeError("no-git", "git was not found on your PATH outside this repository "
                                         "(a git inside the repo is never run)")
        self.hooks = ""
        self.version: Optional[Tuple[int, int, int]] = None
        self.version_text = ""
        #: Set by the caller at its commit point: from then on no call is cancellable — a Cancel
        #: (or a shutdown) can no longer stop work whose stopping would leave a half-done write.
        self.sealed = False

    # ------------------------------------------------------------------ lifecycle
    def __enter__(self) -> "Git":
        self.hooks = tempfile.mkdtemp(prefix="mokata-merge-hooks-")
        try:
            os.chmod(self.hooks, 0o700)
        except OSError:                              # pragma: no cover — Windows ACLs; still empty
            pass
        code, out, err = self._exec(None, ["--version"], cancellable=True)
        self.version_text = (out.decode("utf-8", "replace") or err).strip()
        self.version = parse_version(self.version_text)
        if code != 0 or self.version is None:
            self.close()
            raise GitSafeError("no-git", f"git could not report its version: {self.version_text[:200]}")
        if self.version[:2] < MIN_VERSION:
            self.close()
            raise GitSafeError("git-too-old",
                               f"git {'.'.join(map(str, self.version))} is too old — the gated merge "
                               f"needs git {MIN_VERSION[0]}.{MIN_VERSION[1]} or later "
                               f"(`check-attr --source`); upgrade git")
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        if self.hooks:
            shutil.rmtree(self.hooks, ignore_errors=True)
            self.hooks = ""

    # ------------------------------------------------------------------ calls
    def _argv(self, cwd: Optional[str], args: Sequence[str]) -> List[str]:
        argv = [self.exe]
        if cwd is not None:
            argv += ["-C", cwd]
        if self.hooks:
            argv += ["-c", f"core.hooksPath={self.hooks}"]
        for k, v in OVERRIDES:
            argv += ["-c", f"{k}={v}"]
        return argv + list(args)

    def _check_hooks(self) -> None:
        if not self.hooks:
            return
        try:
            left = os.listdir(self.hooks)
        except OSError as exc:
            raise GitSafeError("exec-config-changed",
                               f"the merge's private hooks directory is gone ({exc}) — refusing to "
                               f"run git without it") from exc
        if left:
            raise GitSafeError("exec-config-changed",
                               "something was written into the merge's private, empty hooks "
                               f"directory ({', '.join(sorted(left)[:5])}) — refusing to run git")

    def _exec(self, cwd: Optional[str], args: Sequence[str], *, stdin: Optional[bytes] = None,
              env: Optional[Dict[str, str]] = None, cancellable: bool = True
              ) -> Tuple[int, bytes, str]:
        from .quiet_spawn import hidden
        self._check_hooks()
        cancellable = cancellable and not self.sealed
        if cancellable:
            C.checkpoint()
        try:
            proc = subprocess.Popen(self._argv(cwd, args),  # nosec B603 — argv, no shell
                                    stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    env=scrubbed_env(self._environ, env), **hidden())
        except OSError as exc:
            return 127, b"", f"git could not start: {exc}"
        if cancellable:
            with C.kill_on_cancel(proc):
                out, err = proc.communicate(stdin)
            C.checkpoint()
        else:
            out, err = proc.communicate(stdin)
        return proc.returncode, out, err.decode("utf-8", "replace")

    def run(self, cwd: str, *args: str, stdin: Optional[bytes] = None,
            env: Optional[Dict[str, str]] = None, cancellable: bool = True,
            read: bool = False) -> Tuple[int, bytes, str]:
        """`git -C cwd <args>` the safe way. `read=True` adds `GIT_OPTIONAL_LOCKS=0` — a read never
        takes (or contends with) the index lock, which the merge itself may be holding."""
        extra = dict(env or {})
        if read:
            extra.setdefault("GIT_OPTIONAL_LOCKS", "0")
        return self._exec(cwd, list(args), stdin=stdin, env=extra, cancellable=cancellable)

    def text(self, cwd: str, *args: str, **kw: Any) -> Tuple[int, str]:
        """`run`, decoded; stdout on success, stderr otherwise (stripped)."""
        code, out, err = self.run(cwd, *args, **kw)
        return code, (out.decode("utf-8", "replace") if code == 0 else (err or
                      out.decode("utf-8", "replace"))).strip()


__all__ = ["Git", "GitSafeError", "MIN_VERSION", "OVERRIDES", "parse_version", "resolve_git",
           "scrubbed_env"]
