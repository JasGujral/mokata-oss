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

from ..quiet_spawn import hidden
from . import snapshots as S
from ..repo_walk import under_python_env

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
    too_large: bool = False        # P33 (R10): changed, but over the snapshot cap — never dropped
    size: int = 0                  # bytes of the newest side, when known
    writes: int = 0                # how many recorded writes made this change (snapshots)

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
    meta: Dict[str, Any] = field(default_factory=dict)   # how a session's changes were attributed

    def facts(self) -> Dict[str, Any]:
        return {"title": self.title, "note": self.note, **self.meta,
                "added": sum(f.added for f in self.files),
                "removed": sum(f.removed for f in self.files),
                "files": [{"path": f.path, "old_path": f.old_path, "status": f.status,
                           "binary": f.binary, "added": f.added, "removed": f.removed,
                           "too_large": f.too_large, "size": f.size, "writes": f.writes,
                           "hunks": [{"header": h.header,
                                      "lines": [[ln.op, ln.old, ln.new, ln.text]
                                                for ln in h.lines]} for h in f.hunks]}
                          for f in self.files]}


# ------------------------------------------------------------------------------------------ git

def _git(root: str, *args: str, timeout: float = GIT_TIMEOUT) -> Tuple[int, str]:
    try:
        p = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout,
                           stdin=subprocess.DEVNULL, **hidden())
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


def open_contained(root: str, rel: str):
    """Open the file as it is now — CONTAINED: resolved through `snapshots.rel_path` (a symlink out
    of the repo is outside, and is not read), opened without following a final symlink and without
    blocking (a FIFO swapped in never hangs the reader), confirmed a regular file on the OPEN
    descriptor, and re-checked inside the repo after opening (a path swapped between check and open
    is caught). Returns `(binary file object, stat)` — the caller closes it — or None when not
    readable, or "" when gone. Shared by `_read_text` and the windowed highlighter (0.0.26 P1)."""
    import stat as _stat
    from ..repo_paths import path_of
    native = path_of(root, rel)
    if not os.path.lexists(native):
        return ""                                   # gone: the "now" side of a delete
    if S.rel_path(root, rel) is None:
        return None
    ab = os.path.realpath(native)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) \
        | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(ab, flags)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not _stat.S_ISREG(st.st_mode) or S.rel_path(root, ab) is None:
            os.close(fd)
            return None
        now = os.stat(ab, follow_symlinks=False)
        if (now.st_dev, now.st_ino) != (st.st_dev, st.st_ino):
            os.close(fd)
            return None                             # swapped after the open
        return os.fdopen(fd, "rb"), st
    except OSError:
        try:
            os.close(fd)
        except OSError:
            pass
        return None


def _read_text(root: str, rel: str) -> Optional[str]:
    """The file as it is now, read through `open_contained` and bounded. Not readable → None;
    gone → ""."""
    got = open_contained(root, rel)
    if not got:
        return got
    fh, _st = got
    try:
        with fh:
            data = fh.read(S.MAX_FILE_BYTES + 1)
    except OSError:
        return None
    if len(data) > S.MAX_FILE_BYTES or b"\0" in data[:8192]:
        return None
    return data.decode("utf-8", errors="replace")


# ------------------------------------------------------------------------------------ sources

# P33 (G23) — what `mokata setup` writes into a repo. The Live view hides these by default (its
# first diff after setup was 122 files, 114 of them mokata's own) and SAYS how many it hid; the
# page's toggle shows them. A command/skill counts only when it is mokata's (its marker / name),
# so a user's own `.claude/commands/x.md` is never hidden.
MOKATA_FILES = (".mcp.json", ".claude/settings.json", ".worktreeinclude")
MOKATA_DIRS = (".mokata/", ".code-review-graph/", ".claude/worktrees/")


def mokata_written(root: str, rel: str) -> bool:
    """Is `rel` a file `mokata setup` (or mokata's own state) wrote? Read-only, bounded."""
    if rel in MOKATA_FILES or rel.startswith(MOKATA_DIRS):
        return True
    if rel.startswith(".claude/commands/") and rel.endswith(".md"):
        try:
            with open(os.path.join(root, *rel.split("/")), encoding="utf-8",
                      errors="replace") as fh:
                head = fh.read(600)
            from ..harness_setup import MOKATA_COMMAND_MARK, _frontmatter_description
            return _frontmatter_description(head).startswith(MOKATA_COMMAND_MARK)
        except Exception:  # noqa: BLE001 — unreadable: shown, never hidden by a guess
            return False
    if rel.startswith(".claude/skills/"):
        parts = rel.split("/")
        if len(parts) > 3:
            try:
                from ..harness_setup import installed_skill_names
                return parts[2] in set(installed_skill_names())
            except Exception:  # noqa: BLE001 — unknown: shown, never hidden by a guess
                return False
    return False


def live(root: str, mokata_files: bool = False) -> DiffSet:
    """The working tree against HEAD, uncommitted and untracked included. mokata's own setup files
    are left out unless `mokata_files` (G23) — the answer says how many were hidden."""
    ds = _live(root)
    if mokata_files or not ds.files:
        ds.meta["hidden_mokata"] = 0
        return ds
    keep = [f for f in ds.files if not mokata_written(root, f.path)]
    hidden = len(ds.files) - len(keep)
    ds.files = keep
    ds.meta["hidden_mokata"] = hidden
    return ds


def _live(root: str) -> DiffSet:
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
            if under_python_env(root, rel):          # 0.0.23 B1: an un-ignored venv/ is not a change
                continue
            files.append(text_diff(rel, "", _read_text(root, rel), status="added"))
    return DiffSet("Live changes (working tree vs HEAD)", files)


def _chain(root: str, session: str) -> List[str]:
    from .sessions import chain
    try:
        return chain(root, session)
    except Exception:  # noqa: BLE001 — an unreadable claim store is a chain of one
        return [session]


def _too_large(path: str, size: int, writes: int, status: str = "modified") -> FileDiff:
    return FileDiff(path=path, status=status, binary=True, too_large=True, size=size,
                    writes=writes)


def session_diff(root: str, session: str) -> DiffSet:
    """What ONE session changed — the ONE diff source every view uses (P33, G1/G17):

      * in a Claude Code worktree (P31 binding): EXACT — `git diff <base>` + untracked in its
        worktree, so Bash-made changes are in it;
      * otherwise: per file, the BEFORE of its first write against its latest AFTER (snapshots),
        plus the paths its Bash calls changed (P31's call-window attribution), basis "approximate".

    A resumed chain is ONE diff across its segments (R11). A file over the snapshot cap is a
    "too large to diff" row, never dropped (R10)."""
    from .. import session_worktrees as SW
    ids = _chain(root, session)
    for sid in ids:
        b = SW.binding(root, sid)
        if b:
            return _worktree_diff(root, session, b)
    snaps = sorted((x for sid in ids for x in S.timeline(root, session=sid)), key=lambda x: x.t)
    first: Dict[str, S.Snapshot] = {}
    last: Dict[str, S.Snapshot] = {}
    writes: Dict[str, int] = {}
    for s in snaps:
        if s.kind == S.BEFORE and s.path not in first:
            first[s.path] = s
        if s.kind == S.AFTER:
            last[s.path] = s
            writes[s.path] = writes.get(s.path, 0) + 1
    files: List[FileDiff] = []
    for path in sorted(set(first) | set(last)):
        a, b2 = first.get(path), last.get(path)
        if (a is not None and a.state == S.TOO_LARGE) or (b2 is not None and b2.state == S.TOO_LARGE):
            files.append(_too_large(path, max(a.size if a else 0, b2.size if b2 else 0),
                                    writes.get(path, 0),
                                    "added" if a is None or a.state == S.ABSENT else
                                    "deleted" if b2 is not None and b2.state == S.ABSENT else
                                    "modified"))
            continue
        old = S.content_of(root, a) if a is not None else ""   # no BEFORE recorded — new
        new = S.content_of(root, b2) if b2 is not None else _read_text(root, path)
        if old == new:
            continue
        fd = text_diff(path, old, new)
        fd.writes = writes.get(path, 0)
        files.append(fd)
    have = {f.path for f in files}
    shared: List[str] = []
    bash = 0
    for sid in ids:
        for rel, v in sorted(SW.attribution(root, sid).items()):
            if v.get("shared"):
                shared.append(rel)
            if rel in have:
                continue
            have.add(rel)
            bash += 1
            files.extend(_working_tree_file(root, rel, v.get("status") == "A"))
    title = f"What session {session[:8]} changed" + (" (resumed chain)" if len(ids) > 1 else "")
    note = ""
    if bash:
        note = ("not in a worktree — Bash changes attributed by call window (approximate)"
                + (f"; {len(shared)} path(s) SHARED with an overlapping session: "
                   + ", ".join(shared[:10]) if shared else ""))
    return DiffSet(title, files, note, meta={"mode": "fallback", "basis": "approximate" if bash
                                             else "snapshots", "session": session,
                                             "chain": ids, "worktree": "", "branch": "",
                                             "base": "", "shared": shared})


def _working_tree_file(root: str, rel: str, added: bool) -> List[FileDiff]:
    """One path as the working tree holds it now, against HEAD (a Bash-attributed change)."""
    from ..repo_paths import path_of
    try:
        size = os.path.getsize(path_of(root, rel))
    except OSError:
        size = 0
    if size > S.MAX_FILE_BYTES:
        return [_too_large(rel, size, 0, "added" if added else "modified")]
    code, out = _git(root, "diff", "HEAD", "--no-color", "--no-ext-diff", "-U3", "--", rel)
    parsed = parse_unified(out[:MAX_DIFF_BYTES]) if code == 0 else []
    if parsed:
        return parsed
    if added:
        return [text_diff(rel, "", _read_text(root, rel), status="added")]
    return []


def _worktree_diff(root: str, session: str, b: Dict[str, Any]) -> DiffSet:
    """EXACT attribution: the session's worktree since its base — committed, staged, unstaged,
    untracked. Files over the cap are rows marked too large (never in the text diff)."""
    from .. import session_worktrees as SW
    from ..repo_paths import path_of
    tree, base = str(b["path"]), str(b.get("base") or "")
    meta = {"mode": "worktree", "basis": "exact", "session": session, "chain": [session],
            "worktree": tree, "branch": b.get("branch", ""), "base": base, "shared": []}
    if not os.path.isdir(tree):
        return DiffSet(f"Session {session[:8]} (worktree)",
                       note=f"the worktree {tree} no longer exists (merged or deleted)", meta=meta)
    listed = SW.worktree_changes(tree, base)
    if not listed.get("ok"):
        return DiffSet(f"Session {session[:8]}", note=str(listed.get("note") or ""), meta=meta)
    big: List[FileDiff] = []
    untracked: List[str] = []
    for f in listed["files"]:
        try:
            size = os.path.getsize(path_of(tree, f["path"]))
        except OSError:
            size = 0
        if size > S.MAX_FILE_BYTES:
            big.append(_too_large(f["path"], size, 0, "added" if f["status"] in ("A", "?")
                                  else "modified"))
        elif f["status"] == "?":
            untracked.append(f["path"])
    skip = [":(exclude,literal)" + f.path for f in big]
    code, out = _git(tree, "diff", base or "HEAD", "--no-color", "--no-ext-diff", "-U3", "-M",
                     "--", ".", *skip)
    if code != 0:
        return DiffSet(f"Session {session[:8]}", note=f"git diff failed: {out[:200]}", meta=meta)
    files = parse_unified(out[:MAX_DIFF_BYTES]) + big
    for rel in untracked:
        files.append(text_diff(rel, "", _read_text(tree, rel), status="added"))
    title = (f"Session {session[:8]} — worktree {b.get('branch') or tree} since "
             f"{(base or 'HEAD')[:10]}")
    note = "" if base else "base unknown — against HEAD"
    if listed.get("truncated"):
        note = (note + "; " if note else "") + f"first {SW.MAX_CHANGED_FILES} files listed"
    return DiffSet(title, sorted(files, key=lambda f: f.path), note, meta=meta)


def write_diff(root: str, snap_id: str) -> DiffSet:
    """ONE write (R9, B-F2): the previous capture of the same file by the same session (chain) →
    this capture. Never "→ the working tree", which mixed in every later write by anyone."""
    snap = S.get(root, snap_id)
    if snap is None:
        return DiffSet("One write", note=f"no snapshot {snap_id}")
    ids = set(_chain(root, snap.session)) if snap.session else {""}
    prev: Optional[S.Snapshot] = None
    n = total = 0
    for s in S.timeline(root, path=snap.path):
        if s.session not in ids:
            continue
        if s.kind == S.AFTER:
            total += 1
        if s.id == snap.id:
            n = total
            break
        prev = s
    for s in S.timeline(root, path=snap.path):          # count the writes after it too
        if s.session in ids and s.kind == S.AFTER and s.t > snap.t:
            total += 1
    title = f"{snap.path}: write {n} of {total}" if snap.kind == S.AFTER else \
        f"{snap.path}: as the session found it"
    if snap.state == S.TOO_LARGE or (prev is not None and prev.state == S.TOO_LARGE):
        return DiffSet(title, [_too_large(snap.path, snap.size, 1)],
                       meta={"write": n, "writes": total, "prev": prev.id if prev else ""})
    old = S.content_of(root, prev) if prev is not None else ""
    fd = text_diff(snap.path, old, S.content_of(root, snap))
    note = "" if prev is not None else "no earlier capture of this file by this session — " \
                                        "shown as created by this write"
    return DiffSet(title, [fd], note, meta={"write": n, "writes": total,
                                           "prev": prev.id if prev else ""})


def regions(old: str, new: str) -> List[Dict[str, Any]]:
    """Changed regions of `new` against `old` on the NEW side's line numbers (the Code gutter's
    shape): `add`, `mod`, `del` (marked at the line the removed lines preceded)."""
    a, b = old.splitlines(), new.splitlines()
    out: List[Dict[str, Any]] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if j2 > j1:
            out.append({"start": j1 + 1, "end": j2, "kind": "add" if tag == "insert" else "mod"})
        else:
            out.append({"start": max(1, j1 + 1), "end": max(1, j1 + 1), "kind": "del",
                        "removed": i2 - i1})
    return out


def session_regions(root: str, session: str, rel: str, text: str,
                    code_root: Optional[str] = None) -> Optional[List[Dict[str, Any]]]:
    """R28 — the Code gutter under "Viewing: session X": `rel`'s regions against what the session
    found (its base commit in its worktree, or its first BEFORE snapshot). None when the session
    did not change this file."""
    from .. import session_worktrees as SW
    ids = _chain(root, session)
    for sid in ids:
        b = SW.binding(root, sid)
        if b and os.path.isdir(str(b["path"])):
            tree = str(b["path"])
            code, out = _git(tree, "show", f"{b.get('base') or 'HEAD'}:{rel}")
            if code != 0:
                code2, untracked = _git(tree, "ls-files", "--others", "--exclude-standard",
                                        "--", rel)
                return [{"start": 1, "end": 10 ** 9, "kind": "add"}] \
                    if code2 == 0 and untracked.strip() else None
            return regions(out, text) if out != text else None
    befores = [s for sid in ids for s in S.timeline(root, session=sid, path=rel)]
    if not befores:
        return None
    befores.sort(key=lambda s: s.t)
    first = befores[0]
    old = S.content_of(root, first) if first.kind == S.BEFORE else ""
    if old is None:
        return None
    return regions(old, text)


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
           "live", "session_diff", "write_diff", "regions", "session_regions", "mokata_written",
           "between", "refs", "branches", "parse_pr_url", "pr"]
