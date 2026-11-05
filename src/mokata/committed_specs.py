"""L7b (0.0.26, ruling D1) — OPT-IN committed specs: `.mokata/specs/<run>.json`.

By default a spec lives only in `.mokata/temp_local/` (run state, git-ignored): no repo litter, and
the research behind D1 found committed spec artefacts add review burden. The cost of that default
is CI: a fresh checkout has no `temp_local/`, so `mokata docsync`'s spec-drift leg and `mokata
ci-check` have no spec to check, and they used to say so in a way that read like a pass.

`settings.specs.commit: true` (a governed manifest setting — `mokata config set` is human-gated and
ledgered) turns on the committed copy. When it is on, the ONE spec committer
(`engine/emit.spec_commit`) also writes the APPROVED spec — criteria, `tasks[]`, `clarifications[]`,
`assumptions[]`, version and plan hash, exactly the bytes the run state holds — to
`.mokata/specs/<run_id>.json`. It is written inside the same human-gated commit that persists the
spec (P2): emit, MCP emit and `spec amend` all land there, so an amendment UPDATES the committed
file (keyed by run, so a retitled spec does not leave a stale twin).

Readers: `docsync.spec_drift` and `ci_check` load this directory when it is present. When it is
absent they print `NO_COMMITTED_NOTE` — the check still exits 0 (nothing was there to check), but
never silently.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import MOKATA_DIR

#: The tracked directory (NOT under temp_local/, so `.mokata/.gitignore` leaves it versioned).
SPECS_DIRNAME = "specs"
#: The governed setting (`settings.<SETTING>`), and its default: off.
SETTING = "specs.commit"
#: The most committed spec files a reader loads — a repo with more than this many runs on record is
#: told how many were left out, never silently truncated.
MAX_COMMITTED = 500

NO_COMMITTED_NOTE = ("no committed specs — spec drift not checked (nothing in .mokata/specs/). "
                     "Opt in with `mokata config set settings.specs.commit true` so approved "
                     "specs are committed and CI can check them")


def specs_dir(root: str) -> str:
    return os.path.join(root, MOKATA_DIR, SPECS_DIRNAME)


def spec_path(root: str, run_id: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in run_id) or "spec"
    return os.path.join(specs_dir(root), safe + ".json")


def commit_enabled(root: str) -> bool:
    """Is `settings.specs.commit` true in this repo's manifest? Read directly (one small JSON read,
    no Surface), so the committer can ask from inside its gated commit. Anything unreadable or not
    exactly `true` reads as OFF — the default, which writes nothing."""
    try:
        with open(os.path.join(root, MOKATA_DIR, "manifest.json"), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return False
    specs = ((data.get("settings") or {}).get("specs") or {}) if isinstance(data, dict) else {}
    return isinstance(specs, dict) and specs.get("commit") is True


def render(run_id: str, data: Dict[str, Any]) -> str:
    """The committed file's text: the approved spec as persisted, plus its run id. Sorted keys and a
    trailing newline, so a re-commit of the same spec is byte-identical (a clean diff in review)."""
    body = dict(data)
    body["run_id"] = run_id
    return json.dumps(body, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def tree_root(tree: str, repo_root: str) -> str:
    """The WORKING TREE a committed spec is written into (0.0.27 SEC-HARDEN): the linked worktree
    `tree` is in, else `repo_root`. The run state is ONE store, in the main checkout (P31), but the
    tracked `.mokata/specs/<run>.json` is a file the user commits IN THE TREE THEY RAN IN — writing
    it into the main checkout from a worktree was a write outside the tree, onto someone else's
    branch, while the message told the user to commit a file their own tree did not have."""
    from .repo_identity import worktree_toplevel
    return (worktree_toplevel(tree) if tree else None) or repo_root


def write_committed(root: str, run_id: str, data: Dict[str, Any]) -> str:
    """Write the approved spec to `.mokata/specs/<run_id>.json`, atomically. Called ONLY from
    `engine/emit.spec_commit`, inside the WriteGate commit closure (P2). Returns the path."""
    from .atomicfile import atomic_write_text
    path = spec_path(root, run_id)
    # newline="\n": a COMMITTED file must be byte-identical on every OS (no CRLF churn on Windows).
    atomic_write_text(path, render(run_id, data), newline="\n")
    return path


@dataclass
class CommittedSpecs:
    """What `.mokata/specs/` holds. `present` is False when the directory is absent or empty — the
    case the readers must SAY out loud."""

    present: bool
    specs: List[Any] = field(default_factory=list)     # engine.spec.Spec
    skipped: List[str] = field(default_factory=list)   # unreadable files (named, never dropped)
    left_out: int = 0                                  # files past MAX_COMMITTED

    @property
    def note(self) -> str:
        if not self.present:
            return NO_COMMITTED_NOTE
        bits = [f"{len(self.specs)} committed spec(s) in .mokata/specs/"]
        if self.skipped:
            bits.append(f"{len(self.skipped)} unreadable: {', '.join(self.skipped[:5])}")
        if self.left_out:
            bits.append(f"{self.left_out} not loaded (over {MAX_COMMITTED})")
        return "; ".join(bits)


def load_committed(root: str) -> CommittedSpecs:
    """Every committed spec, read-only and bounded. Never raises."""
    from .engine.spec import Spec
    d = specs_dir(root)
    try:
        names = sorted(n for n in os.listdir(d) if n.endswith(".json"))
    except OSError:
        return CommittedSpecs(present=False)
    if not names:
        return CommittedSpecs(present=False)
    out = CommittedSpecs(present=True, left_out=max(0, len(names) - MAX_COMMITTED))
    for name in names[:MAX_COMMITTED]:
        try:
            with open(os.path.join(d, name), encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                raise ValueError("not an object")
            out.specs.append(_spec_from_file(data))
        except (OSError, ValueError, TypeError, KeyError):
            out.skipped.append(name)
    return out


def _spec_from_file(data: Dict[str, Any]) -> Any:
    """A committed file as a Spec carrying its OWN declared AC -> test map (`tests[]`, written at
    emit). A file without one (written before it existed) has `tests=None`: its tests are not
    graded, and the spec must be re-emitted to record them."""
    from .engine.spec import Spec, TestRef
    spec = Spec.from_dict(data)
    spec.committed = True
    raw = data.get("tests")
    if isinstance(raw, list):
        spec.tests = [TestRef(name=str(t["name"]), ac_ids=[str(a) for a in t.get("ac_ids", [])])
                      for t in raw if isinstance(t, dict) and "name" in t]
    return spec


_DRIVE = re.compile(r"^[A-Za-z]:")


def normalize_rel(path: str) -> str:
    """A repo-relative path in ONE spelling: '/' separators, no './' and no '//'. Raises ValueError
    for what cannot name a file inside the repo: empty, absolute, a drive letter, or any '..'.
    `tests\\x.py`, `./tests/x.py` and `tests//x.py` are the same file and must compare equal."""
    from .repo_paths import as_name
    text = str(as_name(str(path)))
    if not text.strip():
        raise ValueError("an empty test path")
    if text.startswith("/") or _DRIVE.match(text):
        raise ValueError(f"'{path}' is an absolute path — use a path relative to the repo root")
    parts = [seg for seg in text.split("/") if seg not in ("", ".")]
    if ".." in parts:
        raise ValueError(f"'{path}' climbs out of the repo ('..') — use a path inside it")
    if not parts:
        raise ValueError(f"'{path}' names no file")
    return "/".join(parts)


def _declared_file(declared: str) -> Optional[str]:
    """The normalised file part of a `file::test` name, or None for a bare name / bad path."""
    file_part, sep, _rest = declared.partition("::")
    if not sep:
        return None
    try:
        return normalize_rel(file_part)
    except ValueError:
        return None


def _test_matches(declared: str, scanned: Any) -> bool:
    """Is the scanned test the one the spec declared? Only a FULL `file::test` counts: the file
    (normalised) AND the test name must both agree. A bare name matches nothing — it would match
    that name in any file, so another spec's test would prove this one's criterion."""
    file_part = _declared_file(declared)
    if file_part is None:
        return False
    return (file_part == normalize_rel(scanned.path)
            and declared.partition("::")[2].split("::")[-1] == scanned.name)


def canonical_test_refs(root: str, tests: List[Any]) -> List[Any]:
    """Every declared test as a full, normalised `file::test`, for a spec that will be committed.
    A bare name is resolved to its file in the working tree; found in none or in several files, the
    emit REFUSES (ValueError) naming what it found. A `file::test` is only normalised: a planned
    test whose file is not written yet is legitimate (spec first, RED test after)."""
    from .engine.spec import TestRef
    bare = {t.name for t in tests if "::" not in t.name}
    found = _find_tests(root, bare) if bare else {}
    out: List[Any] = []
    for t in tests:
        if "::" in t.name:
            file_part, _sep, rest = t.name.partition("::")
            name = f"{normalize_rel(file_part)}::{rest}"
        else:
            where = found.get(t.name, [])
            if len(where) != 1:
                raise ValueError(
                    f"test '{t.name}' is a bare name and cannot be recorded in the committed spec: "
                    + (f"it is in {len(where)} files ({', '.join(where)}) — say which with "
                       f"'file::{t.name}'" if where else
                       f"no file in the repo defines it — write it as 'file::{t.name}'"))
            name = f"{where[0]}::{t.name}"
        out.append(TestRef(name=name, ac_ids=list(t.ac_ids), path=t.path, line=t.line,
                           resolved_from="" if "::" in t.name else t.name))
    return out


def resolution_lines(tests: List[Any]) -> List[str]:
    """One line per bare test name the emit resolved to a file, so a reviewer can see WHICH file's
    test a spec is leaning on: `test_x -> tests/test_alpha.py::test_x`."""
    return [f"{t.resolved_from} -> {t.name}" for t in tests if getattr(t, "resolved_from", "")]


def _find_tests(root: str, names: set) -> Dict[str, List[str]]:
    """Name -> the repo-relative files that define a test of that name (every test, tagged or not)."""
    from . import languages
    from .repo_paths import name_of
    from .repo_walk import prune_source_dirs
    where: Dict[str, List[str]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        prune_source_dirs(dirpath, dirnames)
        for fn in filenames:
            if not languages.is_source_file(fn):
                continue
            path = os.path.join(dirpath, fn)
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    lines = fh.read().splitlines()
            except OSError:
                continue
            for name, _line, _body in languages.language_for(fn).tests(lines):
                if name in names:
                    rel = name_of(path, root)
                    if rel not in where.setdefault(name, []):
                        where[name].append(rel)
    return where


def attributed_tests(spec: Any, scanned: List[Any]) -> List[Any]:
    """The scanned tests that count as THIS spec's proof: a test the spec declared for an AC, that
    really carries that AC's id in the tree. Another spec's AC-1 test never appears here."""
    from .engine.spec import TestRef
    out: List[Any] = []
    for t in scanned:
        ids = [aid for d in (spec.tests or []) if _test_matches(d.name, t)
               for aid in d.ac_ids if aid in t.ac_ids]
        if ids:
            # the FULL normalised `file::test`, so a reviewer sees whose file proves the AC
            out.append(TestRef(name=f"{normalize_rel(t.path)}::{t.name}",
                               ac_ids=list(dict.fromkeys(ids)), path=t.path, line=t.line))
    return out


def committed_path_for(root: str, run_id: Optional[str]) -> Optional[str]:
    """Where an approval of `run_id`'s spec would be committed, or None when the setting is off —
    for the approval preview, so the human sees the tracked write before approving it."""
    if not root or not run_id or not commit_enabled(root):
        return None
    from .repo_paths import name_of
    return name_of(spec_path(root, run_id), root)


__all__ = ["MAX_COMMITTED", "NO_COMMITTED_NOTE", "SETTING", "SPECS_DIRNAME", "CommittedSpecs",
           "attributed_tests", "canonical_test_refs", "commit_enabled", "normalize_rel",
           "resolution_lines", "committed_path_for", "load_committed", "render",
           "spec_path",
           "specs_dir", "write_committed"]
