"""Stage 71a — a stable, deterministic PROJECT KEY that scopes SHARED backends per project.

The shared Postgres tables (`mokata_memory`, `mokata_memory_vectors`, `mokata_session_bundle`,
and the Stage-71 `mokata_audit_log`) are OWNED/namespaced vs OTHER apps but were NOT per-project —
so two projects on ONE DSN would bleed together. Local SQLite `.mokata/` and the committed vault
are already per-repo/clean.

This module gives every shared backend a single **project key** to scope by:

  * `project_id(surface_or_root)` — the key for the current project: the configured
    `settings.project.id` if set, else a deterministic value derived from the repo.
  * `derive_project_id(root)` — the deterministic fallback: the git **remote** URL (normalized so
    two clones — ssh vs https — of the same repo agree) if present, else the repo root path. Hashed
    to a short, stable, machine-path-free token.

Stable across sessions (same repo → same key), deterministic, and overridable via
`mokata config set settings.project.id <id>`. `ALL_PROJECTS` is the review sentinel meaning "span
every project" (used by `--all`); `LEGACY_PROJECT` is how a pre-scoping (NULL) row reads back.

Dependency-free (stdlib only), clean-room. Copyright 2026 MoStack. Licensed under the Apache
License, Version 2.0.
"""

from __future__ import annotations

import hashlib
import os
from typing import Callable, List, Optional

PROJECT_SETTINGS_KEY = "project"        # settings.project.id
_PREFIX = "p_"                          # a short, readable id token
_HASH_LEN = 16

# Review sentinels (never a real key): span-all, and the bucket a pre-71a NULL row reads back as.
ALL_PROJECTS = None                     # `--all` → no project filter
LEGACY_PROJECT = "legacy"               # an unscoped (NULL/empty) shared row, surfaced under --all


# A git runner returns (rc, stdout); injectable so tests never shell out. Never raises.
GitRunner = Callable[[List[str], str], "tuple"]


def _default_git(args: List[str], cwd: str) -> "tuple":
    import subprocess
    try:
        p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=30)
        return (p.returncode, p.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return (127, "")


def _has_git_dir(root: str) -> bool:
    """True if `root` (or a parent) holds a `.git` — a cheap gate so we never spawn `git` in a
    non-repo (the common case in tests + non-git checkouts). `.git` is a dir (normal repo) or a
    file (worktree/submodule)."""
    cur = os.path.abspath(root)
    while True:
        if os.path.exists(os.path.join(cur, ".git")):
            return True
        parent = os.path.dirname(cur)
        if parent == cur:
            return False
        cur = parent


def _git_remote(root: str, git: Optional[GitRunner] = None) -> Optional[str]:
    """The repo's `origin` remote URL, or None (no git / no remote). Never raises. With the default
    runner we first check for a `.git` so a non-repo costs no subprocess; an injected runner (tests)
    is always called."""
    if git is None and not _has_git_dir(root):
        return None
    run = git or _default_git
    rc, out = run(["config", "--get", "remote.origin.url"], root)
    url = (out or "").strip()
    return url if rc == 0 and url else None


def normalize_remote(url: str) -> str:
    """Collapse the equivalent forms of a git remote to a single canonical `host/path` so two
    clones of the SAME repo (ssh `git@host:o/r.git` vs https `https://host/o/r.git`) get the SAME
    project key. Lower-cased, `.git` stripped, credentials/ports dropped."""
    s = url.strip()
    for scheme in ("https://", "http://", "ssh://", "git://"):
        if s.lower().startswith(scheme):
            s = s[len(scheme):]
            break
    else:
        # scp-like syntax: git@host:owner/repo(.git)
        if "@" in s and ":" in s and "://" not in s:
            s = s.split("@", 1)[1].replace(":", "/", 1)
    if "@" in s.split("/", 1)[0]:          # strip any leftover user@host credentials
        s = s.split("@", 1)[1]
    host, _, path = s.partition("/")
    host = host.split(":", 1)[0]           # drop a :port
    s = f"{host}/{path}" if path else host
    if s.endswith(".git"):
        s = s[:-4]
    return s.strip("/").lower()


# Per-root cache for the DEFAULT-git derivation (the id is stable for a repo, so shelling `git`
# once per process is enough — avoids a subprocess on every from_surface). Bypassed when a git
# runner is injected (tests supply their own deterministic runner).
_ID_CACHE: dict = {}


def derive_project_id(root: str, *, git: Optional[GitRunner] = None) -> str:
    """A deterministic project key for `root`: the normalized git remote if present, else the
    absolute repo path. Hashed to a short, stable, machine-path-free token. Never raises. Cached
    per root for the default runner (stable for the life of the process)."""
    key = os.path.abspath(root)
    if git is None and key in _ID_CACHE:
        return _ID_CACHE[key]
    remote = _git_remote(root, git)
    basis = ("remote:" + normalize_remote(remote)) if remote else ("path:" + key)
    out = _PREFIX + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:_HASH_LEN]
    if git is None:
        _ID_CACHE[key] = out
    return out


def _configured(surface: object) -> Optional[str]:
    manifest = getattr(surface, "manifest", None)
    if manifest is None:
        return None
    try:
        cfg = manifest.setting(PROJECT_SETTINGS_KEY, {}) or {}
    except Exception:
        return None
    val = cfg.get("id") if isinstance(cfg, dict) else None
    return str(val) if val else None


def project_id(surface_or_root: object, *, git: Optional[GitRunner] = None) -> str:
    """The current project's key. A `Surface` (has `.manifest`/`.root`) honors the configured
    `settings.project.id` override, then falls back to the derived value; a bare root string just
    derives. Stable + deterministic."""
    if hasattr(surface_or_root, "manifest") or hasattr(surface_or_root, "root"):
        configured = _configured(surface_or_root)
        if configured:
            return configured
        root = getattr(surface_or_root, "root", ".")
        return derive_project_id(root, git=git)
    return derive_project_id(str(surface_or_root), git=git)
