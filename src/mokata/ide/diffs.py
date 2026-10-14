"""Every diff the review window shows, in ONE model, whatever its source (0.0.22 E2).

    live(root)                       the working tree against HEAD — uncommitted and untracked too
    session_diff(root, session)      what ONE session wrote: each file's BEFORE (its first write)
                                     against its latest AFTER — "what is coded by session"
    between(root, a, b)              two snapshots of one file — the diff at any point in time
    refs(root, base, head)           two commits / branches (`base...head`, merge-base semantics)
    pr(root, url)                    a GitHub PR by URL, fetched with the USER's own git login

Each returns a `DiffSet`: files, each with hunks of typed lines, plus a `note` saying plainly when a
source could not answer (not a git repo, git missing, a fetch refused). An empty DiffSet with no
note means "nothing changed"; one WITH a note means "could not tell" — never the same thing (§7g).

Git is called with a timeout, `--no-ext-diff` and `--no-color`, never through a shell. Read-only
except `pr`, which runs `git fetch` — the user's explicit request, and the one network call here.
"""

from __future__ import annotations

import difflib
import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from . import snapshots as S

GIT_TIMEOUT = 10.0
FETCH_TIMEOUT = 60.0
MAX_DIFF_BYTES = 4 * 1024 * 1024
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")
_PR_URL = re.compile(r"^https?://github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)(?:[/?#].*)?$")


@dataclass
class Line:
    op: str              # " " context · "+" added · "-" removed
    old: Optional[int]
    new: Optional[int]
    text: str


@dataclass
class Hunk:
    header: str
    lines: List[Line] = field(default_factory=list)


@dataclass
class FileDiff:
    path: str
    status: str          # added | modified | deleted | renamed
    hunks: List[Hunk] = field(default_factory=list)
    binary: bool = False
    old_path: str = ""

    @property
    def added(self) -> int:
        return sum(1 for h in self.hunks for ln in h.lines if ln.op == "+")

    @property
    def removed(self) -> int:
        return sum(1 for h in self.hunks for ln in h.lines if ln.op == "-")


@dataclass
class DiffSet:
    title: str
    files: List[FileDiff] = field(default_factory=list)
    note: str = ""       # set when the source could not answer — never confused with "no changes"

    def facts(self) -> Dict[str, Any]:
        return {"title": self.title, "note": self.note,
                "added": sum(f.added for f in self.files),
                "removed": sum(f.removed for f in self.files),
                "files": [{"path": f.path, "old_path": f.old_path, "status": f.status,
                           "binary": f.binary, "added": f.added, "removed": f.removed,
                           "hunks": [{"header": h.header,
                                      "lines": [[ln.op, ln.old, ln.new, ln.text]
                                                for ln in h.lines]} for h in f.hunks]}
                          for f in self.files]}


# ------------------------------------------------------------------------------------------ git

def _git(root: str, *args: str, timeout: float = GIT_TIMEOUT) -> Tuple[int, str]:
    try:
        p = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout,
                           stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        return 127, "git is not installed"
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)
    return p.returncode, p.stdout if p.returncode == 0 else (p.stderr or p.stdout)


def is_git(root: str) -> bool:
    code, out = _git(root, "rev-parse", "--is-inside-work-tree")
    return code == 0 and out.strip() == "true"


def parse_unified(text: str) -> List[FileDiff]:
    """`git diff` output → FileDiffs. Tolerant: an unexpected line is skipped, never raised on."""
    files: List[FileDiff] = []
    cur: Optional[FileDiff] = None
    hunk: Optional[Hunk] = None
    old_no = new_no = 0
    for raw in text.splitlines():
        if raw.startswith("diff --git "):
            m = re.match(r"diff --git a/(.*) b/(.*)$", raw)
            path = m.group(2) if m else raw[11:]
            cur = FileDiff(path=path, status="modified")
            files.append(cur)
            hunk = None
            continue
        if cur is None:
            continue
        if raw.startswith("new file mode"):
            cur.status = "added"
        elif raw.startswith("deleted file mode"):
            cur.status = "deleted"
        elif raw.startswith("rename from "):
            cur.old_path, cur.status = raw[12:], "renamed"
        elif raw.startswith("Binary files ") or raw.startswith("GIT binary patch"):
            cur.binary = True
        elif raw.startswith("@@"):
            m = _HUNK.match(raw)
            if not m:
                continue
            old_no, new_no = int(m.group(1)), int(m.group(3))
            hunk = Hunk(header=raw)
            cur.hunks.append(hunk)
        elif hunk is not None and raw[:1] in (" ", "+", "-") and not raw.startswith(("+++", "---")):
            op, body = raw[0], raw[1:]
            if op == " ":
                hunk.lines.append(Line(" ", old_no, new_no, body))
                old_no += 1
                new_no += 1
            elif op == "-":
                hunk.lines.append(Line("-", old_no, None, body))
                old_no += 1
            else:
                hunk.lines.append(Line("+", None, new_no, body))
                new_no += 1
    return files


def text_diff(path: str, old: Optional[str], new: Optional[str], status: str = "") -> FileDiff:
    """Two texts → one FileDiff. None on either side = not text (binary / too large)."""
    if old is None or new is None:
        return FileDiff(path=path, status=status or "modified", binary=True)
    lines = list(difflib.unified_diff(old.splitlines(), new.splitlines(), "a/" + path,
                                      "b/" + path, lineterm="", n=3))
    parsed = parse_unified("diff --git a/%s b/%s\n%s" % (path, path, "\n".join(lines)))
    fd = parsed[0] if parsed else FileDiff(path=path, status="modified")
    fd.status = status or ("added" if old == "" and new else
                           "deleted" if new == "" and old else "modified")
    return fd


def _read_text(root: str, rel: str) -> Optional[str]:
    """The file as it is now — CONTAINED: resolved through `snapshots.rel_path` (a symlink out of
    the repo is outside, and is not read), opened without following a final symlink and without
    blocking (a FIFO swapped in never hangs the reader), confirmed a regular file on the OPEN
    descriptor, re-checked inside the repo after opening (a path swapped between check and open is
    caught), and read bounded. Not readable → None; gone → ""."""
    import stat as _stat
    from ..repo_paths import path_of
    native = path_of(root, rel)
    if not os.path.lexists(native):
        return ""                                   # gone: the "now" side of a delete
    if S.rel_path(root, rel) is None:
        return None
    ab = os.path.realpath(native)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(ab, flags)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not _stat.S_ISREG(st.st_mode) or S.rel_path(root, ab) is None:
            return None
        try:
            now = os.stat(ab, follow_symlinks=False)
        except OSError:
            return None
        if (now.st_dev, now.st_ino) != (st.st_dev, st.st_ino):
            return None                             # swapped after the open
        with os.fdopen(fd, "rb") as fh:
            fd = -1
            data = fh.read(S.MAX_FILE_BYTES + 1)
    except OSError:
        return None
    finally:
        if fd >= 0:
            os.close(fd)
    if len(data) > S.MAX_FILE_BYTES or b"\0" in data[:8192]:
        return None
    return data.decode("utf-8", errors="replace")


# ------------------------------------------------------------------------------------ sources

def live(root: str) -> DiffSet:
    """The working tree against HEAD, uncommitted and untracked included."""
    if not is_git(root):
        return DiffSet("Live changes", note="not a git repository — there is no HEAD to diff "
                                            "against (snapshots still work)")
    code, has_head = _git(root, "rev-parse", "--verify", "HEAD")
    base = "HEAD" if code == 0 else "4b825dc642cb6eb9a060e54bf8d69288fbee4904"   # empty tree
    code, out = _git(root, "diff", base, "--no-color", "--no-ext-diff", "-U3", "-M")
    if code != 0:
        return DiffSet("Live changes", note=f"git diff failed: {out.strip()[:200]}")
    files = parse_unified(out[:MAX_DIFF_BYTES])
    code, untracked = _git(root, "ls-files", "--others", "--exclude-standard", "-z")
    if code == 0:
        for rel in [u for u in untracked.split("\0") if u]:
            if rel.split("/", 1)[0] == ".mokata":
                continue
            files.append(text_diff(rel, "", _read_text(root, rel), status="added"))
    return DiffSet("Live changes (working tree vs HEAD)", files)


def session_diff(root: str, session: str) -> DiffSet:
    """What ONE session wrote: per file, the BEFORE of its first write against its latest AFTER."""
    snaps = S.timeline(root, session=session)
    if not snaps:
        return DiffSet(f"Session {session[:8]}", note="")
    first: Dict[str, S.Snapshot] = {}
    last: Dict[str, S.Snapshot] = {}
    for s in snaps:
        if s.kind == S.BEFORE and s.path not in first:
            first[s.path] = s
        if s.kind == S.AFTER:
            last[s.path] = s
    files = []
    for path in sorted(set(first) | set(last)):
        old = S.content_of(root, first[path]) if path in first else None
        new = S.content_of(root, last[path]) if path in last else _read_text(root, path)
        if path not in first:
            old = ""                                   # no BEFORE recorded — treat as new
        if old == new:
            continue
        files.append(text_diff(path, old, new))
    return DiffSet(f"What session {session[:8]} wrote", files)


def between(root: str, a_id: str, b_id: str) -> DiffSet:
    """Two snapshots of the SAME file — the diff at any point in time. `b_id` may be "work" for the
    file as it is now."""
    a = S.get(root, a_id)
    if a is None:
        return DiffSet("Point in time", note=f"no snapshot {a_id}")
    if b_id == "work":
        new = _read_text(root, a.path)
        label = "now"
    else:
        b = S.get(root, b_id)
        if b is None or b.path != a.path:
            return DiffSet("Point in time", note="both points must be snapshots of the same file")
        new = S.content_of(root, b)
        label = b.id
    return DiffSet(f"{a.path}: {a.id} → {label}", [text_diff(a.path, S.content_of(root, a), new)])


_REF = re.compile(r"^[\w./@^~][\w./@^~-]{0,199}$")   # never a leading "-" (a git option)


def refs(root: str, base: str, head: str) -> DiffSet:
    """`base...head` — what `head` changed since it left `base` (the review a PR shows)."""
    if not (_REF.match(base or "") and _REF.match(head or "")) or ".." in base + head:
        return DiffSet("Compare", note="refs must be plain branch / commit names")
    if not is_git(root):
        return DiffSet("Compare", note="not a git repository")
    code, out = _git(root, "diff", "--no-color", "--no-ext-diff", "-U3", "-M", "--end-of-options",
                     f"{base}...{head}")
    if code != 0:
        return DiffSet(f"{base}...{head}", note=out.strip()[:300])
    return DiffSet(f"{base}...{head}", parse_unified(out[:MAX_DIFF_BYTES]))


def branches(root: str) -> List[str]:
    code, out = _git(root, "for-each-ref", "--format=%(refname:short)", "refs/heads")
    return [b for b in out.splitlines() if b] if code == 0 else []


def parse_pr_url(url: str) -> Optional[Tuple[str, str, int]]:
    m = _PR_URL.match((url or "").strip())
    return (m.group(1), m.group(2), int(m.group(3))) if m else None


_SCP = re.compile(r"^(?:[^@/:]+@)?([^@/:]+):([^/].*)$")


def _origin_slug(remote: str) -> Optional[str]:
    """`owner/repo` of a remote on the host github.com EXACTLY (https / ssh / scp-style),
    lowercased — or None. Compared for EQUALITY by the caller: `acme/widgets-evil` is not
    `acme/widgets`, and `github.com.evil.com` / `github.com@evil.com` are not github.com."""
    from urllib.parse import urlsplit
    text = (remote or "").strip()
    if "://" in text:
        try:
            u = urlsplit(text)
            host, path = (u.hostname or ""), u.path
        except ValueError:
            return None
    else:
        m = _SCP.match(text)
        if not m:
            return None
        host, path = m.group(1), m.group(2)
    if host.lower() != "github.com":
        return None
    parts = [p for p in path.strip("/").split("/") if p]
    if len(parts) != 2:
        return None
    repo = parts[1][:-4] if parts[1].lower().endswith(".git") else parts[1]
    return f"{parts[0]}/{repo}".lower()


def pr(root: str, url: str) -> DiffSet:
    """A GitHub PR by URL: `git fetch origin pull/<n>/head` with the user's own git login (mokata
    stores no token), then the PR head against its merge-base with the default branch."""
    parsed = parse_pr_url(url)
    if parsed is None:
        return DiffSet("Pull request", note="not a GitHub PR URL "
                                            "(https://github.com/<owner>/<repo>/pull/<n>)")
    owner, repo, num = parsed
    if not is_git(root):
        return DiffSet(f"PR #{num}", note="not a git repository")
    code, remote = _git(root, "remote", "get-url", "origin")
    if code != 0 or _origin_slug(remote) != f"{owner}/{repo}".lower():
        return DiffSet(f"PR #{num}", note=f"this repo's `origin` is not {owner}/{repo} — open the "
                                          f"window in a checkout of that repo")
    code, out = _git(root, "fetch", "--no-tags", "origin", f"pull/{num}/head",
                     timeout=FETCH_TIMEOUT)
    if code != 0:
        return DiffSet(f"PR #{num}", note=f"git fetch failed (your git login is used; mokata "
                                          f"stores no token): {out.strip()[:200]}")
    code, head = _git(root, "rev-parse", "FETCH_HEAD")
    base = None
    for cand in ("origin/HEAD", "origin/main", "origin/master"):
        c, out = _git(root, "rev-parse", "--verify", cand)
        if c == 0:
            base = cand
            break
    if base is None or code != 0:
        return DiffSet(f"PR #{num}", note="could not find the default branch to compare against")
    result = refs(root, base, head.strip())
    result.title = f"PR #{num} ({owner}/{repo}) against {base}"
    return result


__all__ = ["Line", "Hunk", "FileDiff", "DiffSet", "is_git", "parse_unified", "text_diff",
           "live", "session_diff", "between", "refs", "branches", "parse_pr_url", "pr"]
