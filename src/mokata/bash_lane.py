"""The Bash write side door, closed for runs (0.0.22 D5: SP-BLIND + the Bash half of HP.S4).

THE PROBLEM
-----------
The run-state gates decide from a target PATH, and a Bash call has none. So during a run, a write
the gates would block through `Write` (`src/foo.py` before a failing test exists) went straight
through as `sed -i … src/foo.py` or `echo … > src/foo.py`: the gates never saw it. SELF-PROTECT
already parsed Bash for write destinations, but only to protect installed code, and only judged
operands that look like paths — so a bare file name passed even there. And eight shapes the parser
cannot see at all (`PARSER_BLIND_SPOTS`: `$VAR`, `$(…)`, `eval`, a runtime-built path, a heredoc
fed to a shell, `find -exec`, `xargs`, an unlisted interpreter) had no owner.

THE FIX — TWO RULES, BOTH ONLY WHERE THEY MATTER
-----------------------------------------------
  1. SEE MORE. Every destination the parser CAN see, bare names included, is resolved against the
     command's own cwd and handed to the SAME run-state gates a `Write` to that path would meet
     (`gate_hook.check_write`). A Bash write is then exactly as allowed as the native write it
     replaces — no more, no less.
  2. REFUSE WHAT WE CANNOT READ (the row's own words). In a session that is IN a pipeline (its C1
     claim is open or bound), a command that may write somewhere the parser cannot determine is
     refused, with the reason and the way out (use Write/Edit, or name the file literally). Outside
     a run nothing new is refused — the positive-trigger contract every gate keeps — and a read
     (`grep`, `ls`, `git log`, `cat <<EOF` to the screen) is never refused anywhere.

WHAT IT DOES NOT POLICE, STATED
-------------------------------
A program mokata RUNS — `python -m unittest`, `make`, `npm test`, `python scripts/x.py` — may write
files, and no parser of the command line can say which. Policing those would mean refusing to run
the tests, so they are out of scope by design: the run-state gates govern what the AGENT writes,
and a program is code the human already has. Inline code (`python -c`, `node -e`, `bash -c`, a
heredoc into an interpreter) is in scope, because there the agent IS the author of the write.

Never raises: an unexpected fault returns "nothing seen", and the hook's own fail-open floor holds.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

#: The gate name a refusal reports (a lane, not one of `gate_hook.GATES`).
GATE_UNREADABLE = "bash-unreadable"

_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "fish"})
_PYTHONS = frozenset({"python", "python3", "python2", "py", "pypy", "pypy3"})
#: Interpreters whose INLINE code the parser cannot read, and the flag that carries that code.
_INLINE_FLAGS = {
    "node": ("-e", "--eval", "-p", "--print"), "bun": ("-e", "--eval"),
    "deno": ("eval",), "ruby": ("-e",), "perl": ("-e", "-E"), "php": ("-r",),
    "Rscript": ("-e",), "lua": ("-e",), "osascript": ("-e",),
    "pwsh": ("-c", "-Command", "-command"), "powershell": ("-c", "-Command", "-command"),
}
_AWKS = frozenset({"awk", "gawk", "mawk", "nawk"})
#: The verbs that may write when another command (`xargs`, `find -exec`) runs them for us.
_WRITERS = frozenset({
    "sed", "gsed", "perl", "ruby", "tee", "truncate", "touch", "cp", "mv", "install", "rsync",
    "ln", "rename", "rm", "dd", "patch", "sponge", "chmod", "chown", "eval", "xargs",
}) | _SHELLS | _PYTHONS | _AWKS | frozenset(_INLINE_FLAGS)
_FIND_ACTIONS = ("-exec", "-execdir", "-ok", "-okdir")
_FIND_WRITES = ("-delete", "-fprint", "-fprint0", "-fprintf", "-fls")

#: Inline code that writes. Deliberately broad: a FALSE refusal costs one re-phrase (use Write);
#: a false pass is the side door this module exists to close.
_CODE_WRITES = re.compile(
    r"writeFile|appendFile|createWriteStream|copyFile|\bfs\.(?:write|append|copy|rename|unlink|rm|"
    r"mkdir|open|create)|File\.(?:write|open|new)|IO\.write|fwrite|fopen|file_put_contents|"
    r"Out-File|Set-Content|Add-Content|\bunlink|\brename|(?<![.\w])system\s*\(|(?<![.\w])exec\s*\(|"
    r"\bspawn|child_process|Deno\.(?:write|create|copy|rename|remove|mkdir|truncate|open)|"
    r"Bun\.write|\bopen\s*\([^,()]*,\s*['\"]\s*(?:\+?[>|]|\+<)|\bopen\s*\([^,()]*,\s*['\"][>|+]")
#: Python writes, judged by MODE so a read (`open('f').read()`) never trips them. `[^()]` keeps one
#: call's arguments from reaching into the next (`open('p.json')).get('name','a')` is a read).
_PY_WRITES = re.compile(
    r"open\s*\((?:[^()]|\([^()]*\))*?,\s*(?:mode\s*=\s*)?['\"][rbt]*[wax+][rbt+]*['\"]|"
    r"\bmode\s*=\s*['\"][^'\"]*[wax+]|\.open\s*\(\s*['\"][rbt]*[wax+]|\bO_(?:WRONLY|RDWR|CREAT|APPEND|TRUNC)\b|"
    r"write_text|write_bytes|\bshutil\.(?:copy|move|rmtree|copyfile|copytree)|"
    r"\bos\.(?:remove|unlink|rename|replace|makedirs|mkdir|rmdir|write|truncate)")
#: A Python process call is a write only when what it runs is a writer (`subprocess.run(['sed',
#: '-i', …])`, `os.system('… > f')`) — `check_output(['git', 'log'])` is a read.
_PY_PROC = re.compile(r"\b(?:subprocess\.\w+|os\.(?:system|popen|exec\w*|spawn\w*))\s*\(")
_RUNTIME = re.compile(r"\$|`")


@dataclass(frozen=True)
class BashWrites:
    """What one Bash command writes, as far as mokata can tell."""
    targets: Tuple[str, ...] = field(default_factory=tuple)   # absolute, normalised
    unreadable: Tuple[str, ...] = field(default_factory=tuple)  # why a write could not be placed
    mutates: Tuple[str, ...] = field(default_factory=tuple)     # deletes / git ops (read-only lane)

    @property
    def readable(self) -> bool:
        return not self.unreadable


def analyze(command: Optional[str], cwd: str) -> BashWrites:
    """The destinations `command` writes (resolved against `cwd`) and the reasons it may write
    somewhere mokata cannot place. Empty for a command that writes nothing mokata can see."""
    if not command or not command.strip():
        return BashWrites()
    try:
        from .selfprotect import (_LAST_OPERAND_VERBS, _is_flag, _segment_targets, _segments,
                                  _split_redirects, _tokenize, segment_verb, target_directory)
        reasons = _unreadable(command, depth=0)
        mutates = _mutations(command)
        # A write inside `$( … )`, backticks, `<( … )` or `>( … )` runs too, quoted or not
        # (0.0.22 verification): each body is analysed like a command of its own.
        sub_targets: List[str] = []
        for body in _substitutions(command):
            inner = analyze(body, cwd)
            sub_targets.extend(inner.targets)
            reasons.extend(inner.unreadable)
            mutates.extend(inner.mutates)
        tokens = _tokenize(command) or []
        targets: List[str] = []
        here: Optional[str] = cwd
        for seg in _segments(tokens):
            dests, words = _split_redirects(seg)
            verb, ops = segment_verb(words)
            if verb in ("cd", "pushd", "popd"):
                # Follow a LITERAL `cd` so `cd /tmp && echo x > a.py` is judged at /tmp/a.py. A
                # computed one (`cd "$D"`, `cd -`, `popd`) only matters if a RELATIVE write follows.
                dest = next((o for o in ops if not o.startswith("-")), "~")
                if verb == "popd" or "-" in ops:
                    dest = "$OLDPWD"
                here = None if (_RUNTIME.search(dest) or here is None) else _resolve(dest, here)
                continue
            if verb in _LAST_OPERAND_VERBS:
                found = dests + _copy_dests(ops, here, _is_flag, target_directory)
            else:
                found = _segment_targets(seg, 0, bare=True)
            for t in found:
                if _RUNTIME.search(t):
                    reasons.append(f"its destination `{t}` is built at runtime ($VAR / $(…))")
                elif here is None and not os.path.isabs(os.path.expanduser(t)):
                    reasons.append(f"it writes `{t}` after changing to a directory built at "
                                   f"runtime")
                else:
                    targets.append(_resolve(t, here or cwd))
        targets.extend(sub_targets)
        return BashWrites(tuple(dict.fromkeys(targets)), tuple(dict.fromkeys(reasons)),
                          tuple(dict.fromkeys(mutates)))
    except Exception:  # noqa: BLE001 — a parser fault sees nothing; the hook's floor holds
        return BashWrites()


def _substitutions(command: str) -> List[str]:
    """The bodies of `$( … )`, `<( … )`, `>( … )` (balanced) and `` `…` `` in `command`, outside
    single quotes (where they are literal text)."""
    out: List[str] = []
    i, n = 0, len(command)
    while i < n:
        c = command[i]
        if c == "'":
            j = command.find("'", i + 1)
            if j < 0:
                break
            i = j + 1
            continue
        if c in "$<>" and command.startswith("(", i + 1) and not command.startswith("((", i + 1):
            depth, j = 1, i + 2
            while j < n and depth:
                depth += {"(": 1, ")": -1}.get(command[j], 0)
                j += 1
            out.append(command[i + 2:j - 1])
            i = j
            continue
        if c == "`":
            j = command.find("`", i + 1)
            if j < 0:
                break
            out.append(command[i + 1:j])
            i = j + 1
            continue
        i += 1
    return [b for b in out if b.strip()]


def _copy_dests(ops: List[str], here: Optional[str], is_flag, target_directory) -> List[str]:
    """The files `cp`/`mv`/`install`/`ln`/`rsync` write. Into a DIRECTORY (`-t DIR`, a trailing
    `/`, or an existing directory) that is each source's name inside it — the directory itself is
    not the file the gates must judge (0.0.22 verification, D5-E)."""
    tdir = target_directory(ops)
    tail = [t for t in ops if not is_flag(t) and t != tdir]
    if tdir is not None:
        return [os.path.join(tdir, os.path.basename(src.rstrip("/"))) for src in tail]
    if len(tail) < 2:
        return []
    dest, sources = tail[-1], tail[:-1]
    if _RUNTIME.search(dest):
        return [dest]
    resolved = _resolve(dest, here) if here else None
    if dest.endswith("/") or (resolved and os.path.isdir(resolved)):
        return [os.path.join(dest, os.path.basename(src.rstrip("/"))) for src in sources]
    return [dest]


#: Commands that change the tree without naming a file the run-state gates judge — a delete, a
#: git operation that rewrites the working tree or history. NOT run-state writes (a delete is not
#: implementation code), but a READ-ONLY subagent may make none of them.
_DELETERS = frozenset({"rm", "rmdir", "unlink", "shred", "mkdir", "chmod", "chown", "chgrp",
                       "tar", "unzip", "patch", "rsync"})
_GIT_MUTATORS = frozenset({"commit", "reset", "clean", "stash", "apply", "am", "merge", "rebase",
                           "pull", "push", "cherry-pick", "revert", "mv", "rm", "checkout",
                           "switch", "restore", "tag", "init", "add", "branch", "worktree",
                           "fetch", "gc", "config", "update-index", "notes", "submodule", "lfs",
                           "checkout-index", "prune", "repack", "update-ref", "replace"})
#: Forms of a mutator subcommand that only READ (`git branch -a`, `git stash list`, …).
_BRANCH_WRITES = frozenset({"-d", "-D", "-m", "-M", "-c", "-C", "--delete", "--move", "--copy",
                            "-f", "--force", "-u", "--set-upstream-to", "--unset-upstream"})
_GIT_READ_FORMS = {
    "branch": lambda r: all(o.startswith("-") and o not in _BRANCH_WRITES for o in r),
    "tag": lambda r: not r or any(o in ("-l", "--list", "-n", "--contains", "--points-at")
                                  for o in r),
    "stash": lambda r: bool(r) and r[0] in ("list", "show"),
    "worktree": lambda r: bool(r) and r[0] == "list",
    "config": lambda r: any(o in ("--get", "--get-all", "--list", "-l", "--get-regexp")
                            for o in r) or len([o for o in r if not o.startswith("-")]) == 1,
    "submodule": lambda r: not r or r[0] in ("status", "summary"),
    "lfs": lambda r: bool(r) and r[0] in ("ls-files", "status", "env", "version"),
    "notes": lambda r: not r or r[0] in ("list", "show"),
}
#: Git operations that rewrite tracked files from a source mokata cannot list (a diff, a stash):
#: in a run they are writes it cannot place.
_GIT_UNLISTABLE = {"apply": None, "am": None, "checkout-index": None,
                   "stash": ("pop", "apply"), "reset": ("--hard", "--merge", "--keep")}


def _mutations(command: str) -> List[str]:
    """Deletes and tree/history changes — writes for a READ-ONLY subagent, not run-state writes."""
    from .selfprotect import _segments, _split_redirects, _tokenize, git_subcommand, segment_verb
    out: List[str] = []
    for seg in _segments(_tokenize(command) or []):
        verb, ops = segment_verb(_split_redirects(seg)[1])
        if verb in _DELETERS:
            out.append(f"`{verb}` changes the tree")
        elif verb in _AWKS and "-i" in ops:
            out.append(f"`{verb} -i` edits files in place")
        elif verb == "git":
            sub, rest = git_subcommand(ops)
            reads = _GIT_READ_FORMS.get(sub)
            if sub in _GIT_MUTATORS and not (reads and reads(rest)):
                out.append(f"`git {sub}` changes the repository")
    return out


def _resolve(target: str, cwd: str) -> str:
    p = os.path.expanduser(target)
    if not os.path.isabs(p):
        p = os.path.join(cwd, p)
    return os.path.normpath(p)


def _unreadable(command: str, depth: int) -> List[str]:
    from .selfprotect import _segments, _split_redirects, _tokenize, segment_verb
    tokens = _tokenize(command)
    if tokens is None:
        return ["it could not be parsed (unbalanced quotes)"]
    out: List[str] = []
    for seg in _segments(tokens):
        stdin_fed = any(t in ("<<", "<<<", "<<-") for t in seg)
        _dests, words = _split_redirects(seg)
        verb, ops = segment_verb(words)
        if not verb:
            continue
        if verb == "eval":
            out.append("`eval` runs a string mokata cannot read")
        elif verb == "git":
            from .selfprotect import git_subcommand
            sub, rest = git_subcommand(ops)
            if sub in _GIT_UNLISTABLE and (_GIT_UNLISTABLE[sub] is None
                                           or any(o in _GIT_UNLISTABLE[sub] for o in rest)):
                out.append(f"`git {sub}` rewrites files mokata cannot list")
        elif verb == "patch" and not any(not o.startswith("-") for o in ops):
            out.append("`patch` applies a diff mokata cannot read")
        elif verb in _AWKS and "-i" in ops:
            out.append(f"`{verb} -i inplace` rewrites files mokata cannot list")
        elif verb == "xargs":
            ran = _xargs_command(ops)
            if ran:
                out.append(f"`xargs` runs `{ran}` on input mokata cannot see")
        elif verb == "find":
            out.extend(_find_reasons(ops))
        elif verb in _SHELLS:
            out.extend(_shell_reasons(verb, ops, depth, stdin_fed))
        elif verb in _PYTHONS:
            out.extend(_python_reasons(verb, ops, stdin_fed))
        elif verb in _INLINE_FLAGS:
            code = _inline_code(ops, _INLINE_FLAGS[verb])
            if code is not None and _CODE_WRITES.search(code):
                out.append(f"`{verb}` inline code writes to a place mokata cannot read")
            elif code is None and stdin_fed:
                out.append(f"`{verb}` runs a program fed on stdin")
        elif verb in _AWKS:
            prog = next((o for o in ops if not o.startswith("-")), "")
            if re.search(r"\bprintf?\b[^;{}]*?(?:>|\|)|system\s*\(|\|\s*getline", prog):
                out.append(f"`{verb}` program writes to a place mokata cannot read")
    return out


def _first_writer(ops: List[str]) -> Optional[str]:
    """The writer the FIRST command word names — `sed` only with an in-place flag, so `xargs sed -n
    p` and `find -exec grep` are reads. Arguments after the command word are never verbs."""
    if not ops:
        return None
    base = os.path.basename(ops[0])
    if base not in _WRITERS:
        return None
    if base in ("sed", "gsed", "perl", "ruby") and not any(
            o.startswith("-") and not o.startswith("--") and "i" in o[1:] or
            o.startswith("--in-place") for o in ops[1:]):
        return None
    return base


_XARGS_VALUE_FLAGS = frozenset({"-I", "-n", "-P", "-L", "-d", "-E", "-s", "-a", "--max-args",
                                "--max-procs", "--delimiter", "--arg-file", "--max-lines"})


def _xargs_command(ops: List[str]) -> Optional[str]:
    i = 0
    while i < len(ops) and ops[i].startswith("-"):
        i += 2 if ops[i] in _XARGS_VALUE_FLAGS else 1
    return _first_writer(ops[i:])


def _find_reasons(ops: List[str]) -> List[str]:
    out: List[str] = []
    for i, o in enumerate(ops):
        if o in _FIND_WRITES:
            out.append(f"`find {o}` changes files mokata cannot list")
        elif o in _FIND_ACTIONS:
            ran = _first_writer(ops[i + 1:])
            if ran:
                out.append(f"`find {o} {ran}` runs on files mokata cannot list")
    return out


def _inline_code(ops: List[str], flags) -> Optional[str]:
    for i, o in enumerate(ops):
        if o in flags and i + 1 < len(ops):
            return ops[i + 1]
        for f in flags:
            if f.startswith("--") and o.startswith(f + "="):
                return o.split("=", 1)[1]
    return None


#: Flags after which an interpreter prints and exits — it reads no program from stdin.
_INFO_FLAGS = frozenset({"--version", "-V", "-VV", "-h", "--help", "-n"})


def _shell_reasons(verb: str, ops: List[str], depth: int, stdin_fed: bool) -> List[str]:
    from .selfprotect import shell_code
    if any(o in _INFO_FLAGS for o in ops) and not stdin_fed:
        return []
    code = shell_code(ops)
    if code is not None:
        if depth >= 1:
            return [f"a `{verb} -c` nested inside another shell"]
        return _unreadable(code, depth + 1)
    if stdin_fed or "-s" in ops or not any(not o.startswith("-") for o in ops):
        return [f"`{verb}` runs commands fed on stdin"]
    return []                       # `bash script.sh` runs a program — out of scope, see above


def _python_reasons(verb: str, ops: List[str], stdin_fed: bool) -> List[str]:
    from .selfprotect import shell_code
    code = shell_code(ops)
    if code is not None:
        from .selfprotect import _python_targets
        sites = len(_PY_WRITES.findall(code)) + sum(
            1 for m in _PY_PROC.finditer(code) if _proc_writes(code[m.end():m.end() + 400]))
        if sites > len(_python_targets(code)):
            return [f"`{verb} -c` writes to a path built at runtime"]
        return []
    if "-m" in ops or (any(o in _INFO_FLAGS for o in ops) and not stdin_fed):
        return []                   # `python -m unittest` runs a program; `-V` prints and exits
    if stdin_fed or "-" in ops or not any(not o.startswith("-") for o in ops):
        return [f"`{verb}` runs code fed on stdin"]
    return []


def _proc_writes(args: str) -> bool:
    """Does a process call's literal argument text run a writer, a shell, or a redirect?"""
    words = re.findall(r"['\"]([^'\"]+)['\"]", args.split(")", 1)[0])
    if any(">" in w for w in words):
        return True
    for w in words:
        first = os.path.basename(w.split()[0]) if w.split() else ""
        if first in _SHELLS or first in _PYTHONS:
            return True
        if first in _WRITERS and _first_writer(w.split() + words[words.index(w) + 1:]):
            return True
    return False


_OWN_RECORD_TEXT = re.compile(r"temp_local[\\/]+(?:\.[\\/]+)*(?:stat|[*?\[{])|\.mokata[*?\[{]|"
                              r"\.mok[*?\[]", re.I)
_REMOVERS = frozenset({"rm", "rmdir", "unlink", "shred", "mv", "trash", "srm"})


def own_record_hits(root: str, cwd: str, guarded: str, targets: List[str], command: str,
                    bash: Optional["BashWrites"]) -> str:
    """What, if anything, in this tool call writes / deletes / moves the state directory
    `guarded` (or an ancestor of it inside the repo, like `.mokata/`). "" when nothing does.

    Literal paths are resolved (realpath, so `./`, `..` and symlinks land where they really
    point), globs are expanded, and the comparison ignores case (macOS volumes do). A command
    that NAMES the state directory in a shape no path can be read from (`$S/…`, a glob, a brace
    list) and writes anything at all is refused on that alone."""
    import glob as _glob
    from .repo_paths import escapes_root, name_of
    g = os.path.realpath(guarded)

    def _inside(path: str, base: str) -> bool:
        try:
            name = name_of(path.lower(), base.lower())
        except ValueError:
            return False
        return name == "." or not escapes_root(name)

    def _expand(t: str) -> List[str]:
        p = t if os.path.isabs(os.path.expanduser(t)) else os.path.join(cwd, t)
        p = os.path.expanduser(p)
        hits = _glob.glob(p) if any(c in t for c in "*?[") else []
        return [os.path.realpath(x) for x in (hits or [p])]

    for t in targets:
        for ab in _expand(t):
            if _inside(ab, g):
                return f"`{t}` writes inside it"
    if not command:
        return ""
    from .selfprotect import _segments, _split_redirects, _tokenize, git_subcommand, segment_verb
    for seg in _segments(_tokenize(command) or []):
        verb, ops = segment_verb(_split_redirects(seg)[1])
        operands = [o for o in ops if not o.startswith("-")]
        if verb == "find" and "-delete" in ops:
            operands = operands[:1] or ["."]
        elif verb == "git":
            sub, rest = git_subcommand(ops)
            if sub == "clean" and any(o.startswith("-") and not o.startswith("--") and
                                      ("x" in o or "X" in o) for o in rest):
                return "`git clean -x` deletes it (it is git-ignored)"
            continue
        elif verb not in _REMOVERS:
            continue
        for o in operands:
            for ab in _expand(o):
                if _inside(ab, g) or _inside(g, ab):
                    return f"`{verb} {o}` removes or moves it"
    has_write = bool(targets) or bool(bash and (bash.unreadable or bash.mutates))
    if has_write and _OWN_RECORD_TEXT.search(command):
        return "this command names it in a form mokata cannot resolve"
    return ""


def refusal(reasons: Tuple[str, ...]) -> str:
    """The one-line BLOCKED message for an unreadable command in a run."""
    return (f"BLOCKED [{GATE_UNREADABLE}] mokata cannot tell which files this command writes "
            f"({reasons[0]}). During a run every write must be visible to the gates: make the "
            f"change with Write/Edit, or name the file literally in the command.")


__all__ = ["GATE_UNREADABLE", "BashWrites", "analyze", "own_record_hits", "refusal"]
