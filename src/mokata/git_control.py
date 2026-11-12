"""GIT-CONTROL — an agent's write to what git EXECUTES or is CONFIGURED by goes through the human gate
(0.0.27 SEC-HARDEN, P2; Jas approved 2026-11-03).

THE HOLE. `selfprotect` used `.git` only as a ROOT MARKER, so `.git/hooks/pre-commit` was an
ordinary in-workspace write: an agent could plant a hook through its Write tool (or `cp`, `tee`, a
redirect) and the only trace was an activity record. A hook runs the next time the HUMAN commits, in
the human's shell, with the human's credentials — the one write in a repository that turns into code
somebody else runs later. `.git/config` is the same class: `core.hooksPath`, `core.fsmonitor`,
`core.sshCommand`, filters and textconv drivers all name programs git will run.

THE RULE. A write whose destination is one of these is REFUSED until a human approves exactly that
write, out of band, with `mokata approve <id>` (SI.3 — the model may reference an approval, never
mint one):

  * a file in the repository's hooks directory — `<common git dir>/hooks/` (a linked worktree's
    hooks live in the MAIN checkout's git dir), and every directory `core.hooksPath` names;
  * git's config files — `<common git dir>/config`, `config.worktree` (both the git dir's and the
    common dir's), and the same under `modules/<name>/` and `worktrees/<name>/`;
  * a Bash `git config` that WRITES (anything but a read form) to this repository's config.

Decided twice, either one enough, and never on spelling alone:
  * by NAME — every component folded the CASEFOLD way (`.GIT/HOOKS/x`, `.git./hooks`), on every OS;
  * by IDENTITY — (device, inode) of the hooks directories and config files resolved from the
    workspace's own `.git` (a directory, or the FILE a linked worktree carries), so a symlinked
    parent, a case alias, or a HARD LINK to `.git/config` or to a hook is the thing itself.

THE APPROVAL IS BOUND TO THE WHOLE CALL — the full `tool_input` (every byte of the content, the
whole command), never the bounded scan the run-state gates read — so "approve A, then write B" is
arithmetically impossible: B's call has another hash, another proposal id, and no approval.

THE HONEST BOUNDS (P22). This is the native-tool lane and the Bash lane's VISIBLE writes:
  * a LITERAL `cd`/`pushd` is followed (`bash_lane.analyze`), so `cd .git/hooks && echo x >
    pre-commit` is judged at `.git/hooks/pre-commit`. A COMPUTED one (`cd "$D"`, `cd -`, `popd`)
    is not: the write after it lands somewhere mokata cannot place, and only the run-state lane
    (in a pipeline session) refuses that as unreadable;
  * a Bash command mokata cannot parse (variables, `eval`, heredoc-fed interpreters, `find -exec`,
    `xargs` — `selfprotect`'s filed blind spots) is not seen here either;
  * `git config` is read through one level of `sh -c`; a `git config` after a `cd` into another
    repository is judged against THIS checkout's config (over-gating, never under);
  * porcelain that writes config as a side effect (`git remote add`, `git push -u`, `git branch -u`)
    is not gated — it sets fixed keys, not arbitrary ones;
  * `.gitattributes`, `.git/info/attributes` and `.gitmodules` are not gated: they only SELECT a
    filter/diff/merge driver, and a driver's program is defined in config, which is;
  * on a filesystem without file ids (inode 0: FAT/exFAT, some SMB) the identity rule is off and
    only the name rule holds — a hard link to `.git/config` there is not seen;
  * the approval itself is SI.3's: whoever can run `mokata approve <id> --yes` in a shell (an agent
    with Bash, too) can approve. This gate makes the hook write VISIBLE and human-shaped; it does
    not make an agent with a shell unable to approve its own proposal.

mokata itself never writes in a hooks directory or a git config file (its own git calls pass
`core.hooksPath` on the command line — `git_safe`), so this gate refuses nothing mokata does.

Stdlib only. Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .fs_identity import refuse_if_inside
from .repo_paths import fold_component, split_path

GATE_GIT_CONTROL = "git-control"
#: The tool name a git-control proposal carries (what `mokata approve` shows and the ledger records).
GIT_CONTROL_TOOL = "git-control"

HOOK = "hook"
CONFIG = "config"

_GIT = ".git"
_CONFIG_NAMES = ("config", "config.worktree")
#: Subtrees of a git dir that are themselves git dirs (a submodule's, a linked worktree's).
_NESTED_GIT_DIRS = ("modules", "worktrees")
#: `git config` forms that only READ (old flag spellings and the 2.46+ subcommands).
_CONFIG_READS = frozenset({"--get", "--get-all", "--get-regexp", "--get-urlmatch", "--get-color",
                           "--get-colorbool", "-l", "--list", "get", "list", "--help", "-h"})
_CONFIG_WRITES = frozenset({"--add", "--unset", "--unset-all", "--replace-all", "--rename-section",
                            "--remove-section", "-e", "--edit", "set", "unset", "rename-section",
                            "remove-section", "edit"})
#: `git config` options that take a VALUE (so the value is not a positional key or value).
_CONFIG_VALUE_FLAGS = frozenset({"-f", "--file", "--blob", "--type", "--default", "--comment",
                                 "--value"})


# ======================================================================================
# the repository's own git dirs, hooks dirs and config files
# ======================================================================================

def _git_dirs(start: str) -> Tuple[Optional[str], List[str]]:
    """`(worktree top, [git dir, common dir])` for the checkout `start` is in — from its `.git`
    (a directory, or the FILE a linked worktree/submodule carries). `(None, [])` outside git."""
    from mokata_emit import store_dirs
    cur = os.path.abspath(start)
    while True:
        dot = os.path.join(cur, _GIT)
        if os.path.isdir(dot):
            return cur, [dot]
        if os.path.isfile(dot):
            dirs = [d for d in store_dirs(cur) if os.path.basename(d) != ".mokata" and d != dot]
            return cur, dirs
        parent = os.path.dirname(cur)
        if parent == cur:
            return None, []
        cur = parent


def _config_values(path: str, key_section: str, key_name: str, seen: set) -> List[str]:
    """Every value of `section.name` in the git config file `path`, following `[include]`/
    `[includeIf]` paths WITHOUT judging their conditions (over-approximating is the safe side of a
    gate). A file already read is not read again (an include loop ends; nothing is capped)."""
    try:
        real = os.path.realpath(path)
    except (OSError, ValueError):
        return []
    if real in seen:
        return []
    seen.add(real)
    try:
        with open(real, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return []
    out: List[str] = []
    section = ""
    for raw in lines:
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        if line.startswith("["):
            head = line[1:line.find("]")] if "]" in line else line[1:]
            section = head.split(None, 1)[0].lower() if head.strip() else ""
            rest = line[line.find("]") + 1:].strip() if "]" in line else ""
            if not rest:
                continue
            line = rest
        if "=" not in line:
            continue
        name, _eq, value = line.partition("=")
        name, value = name.strip().lower(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        if section == key_section and name == key_name and value:
            out.append(value)
        if section in ("include", "includeif") and name == "path" and value:
            inc = os.path.expanduser(value)
            if not os.path.isabs(inc):
                inc = os.path.join(os.path.dirname(real), inc)
            out.extend(_config_values(inc, key_section, key_name, seen))
    return out


def _user_configs() -> List[str]:
    """The user-level git config files git reads (`$XDG_CONFIG_HOME/git/config` or
    `~/.config/git/config`, and `~/.gitconfig`)."""
    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return [os.path.join(xdg, "git", "config"), os.path.join(os.path.expanduser("~"), ".gitconfig")]


def hooks_dirs(top: str, gitdirs: Sequence[str]) -> List[str]:
    """Every directory git may run this checkout's hooks from: `<common dir>/hooks` (and the git
    dir's own), plus each `core.hooksPath` any config file it reads names (relative to `top`)."""
    out = [os.path.join(g, "hooks") for g in gitdirs]
    files = _user_configs()
    for g in gitdirs:
        files += [os.path.join(g, n) for n in _CONFIG_NAMES]
    seen: set = set()
    for f in files:
        for value in _config_values(f, "core", "hookspath", seen):
            p = os.path.expanduser(value)
            out.append(p if os.path.isabs(p) else os.path.join(top, p))
    return out


def config_files(gitdirs: Sequence[str]) -> List[str]:
    return [os.path.join(g, n) for g in gitdirs for n in _CONFIG_NAMES]


# ======================================================================================
# the verdict for one write destination
# ======================================================================================

def name_kind(path: str) -> str:
    """HOOK / CONFIG / "" by NAME alone: the part after the LAST `.git` component (folded), with
    nested git dirs (`modules/<n>/`, `worktrees/<n>/`) peeled, starting `hooks/` or being exactly a
    config file — or nothing at all (the `.git` pointer FILE of a linked worktree). Raises on a name
    that cannot be folded (the caller treats that as a hit)."""
    keys = [fold_component(p) for p in split_path(path)]
    if _GIT not in keys:
        return ""
    rest = keys[len(keys) - 1 - keys[::-1].index(_GIT) + 1:]
    if not rest:
        return CONFIG                 # the `.git` FILE itself: it says which git dir runs here
    while len(rest) >= 2 and rest[0] in _NESTED_GIT_DIRS:
        rest = rest[2:]
    if rest[:1] == ["hooks"]:
        return HOOK
    if len(rest) == 1 and rest[0] in _CONFIG_NAMES:
        return CONFIG
    return ""


def _folded(path: str) -> List[str]:
    return [fold_component(p) for p in split_path(os.path.normcase(path))]


def _id(path: str) -> Optional[Tuple[int, int]]:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_dev, st.st_ino) if st.st_ino else None


def _files_in(dirs: Sequence[str]) -> set:
    """(dev, ino) of the files directly in each hooks dir — so a HARD LINK to a hook is a hook."""
    ids = set()
    for d in dirs:
        try:
            with os.scandir(d) as it:
                for e in it:
                    if e.is_file(follow_symlinks=True):
                        got = _id(e.path)
                        if got:
                            ids.add(got)
        except OSError:
            continue
    return ids


def kind_of(target: str, cwd: str) -> str:
    """HOOK / CONFIG when a write to `target` (relative to `cwd`) lands on what git executes or is
    configured by, else "". Name rule on the path as written and as resolved, then identity against
    the checkout's own hooks dirs and config files. Undecidable is a HOOK (refuse, never guess)."""
    try:
        ab = target if os.path.isabs(os.path.expanduser(target)) else os.path.join(cwd, target)
        ab = os.path.abspath(os.path.expanduser(ab))
        real = os.path.realpath(ab)
        for p in (ab, real):
            k = name_kind(p)
            if k:
                return k
        top, gitdirs = _git_dirs(os.path.dirname(real))
        if top is None:
            top, gitdirs = _git_dirs(cwd)
        if top is None:
            return ""
        hooks = hooks_dirs(top, gitdirs)
        configs = config_files(gitdirs)
        # 0.0.28 C2: inside a hooks dir by ANY spelling or on disk (`refuse_if_inside`: the fold OR
        # identity; an unreadable chain is inside) - the one containment door, no local `_under`.
        if any(refuse_if_inside(real, h) for h in hooks):
            return HOOK
        if any(_folded(real) == _folded(os.path.realpath(c)) for c in configs):
            return CONFIG
        mine = _id(real)
        if mine is not None:
            if mine in {i for i in (_id(c) for c in configs) if i}:
                return CONFIG
            if mine in _files_in(hooks):
                return HOOK
        return ""
    except (OSError, ValueError, TypeError):
        return HOOK


def git_config_writes(command: str, cwd: str, depth: int = 0) -> List[str]:
    """The config FILE each `git config` write in a shell command lands on (read forms excluded):
    `-f FILE`, `--worktree` (the git dir's `config.worktree`), `--global`/`--system` when the key is
    `core.hooksPath` (it changes which hooks THIS repository runs), else the common dir's `config`.
    One level of `sh -c`/`bash -c` nesting is read, the self-protect lane's own bound. Never raises;
    an unparseable command yields []."""
    from .selfprotect import (_SHELL_VERBS, _segments, _tokenize, git_subcommand, segment_verb,
                              shell_code)
    tokens = _tokenize(command or "")
    if not tokens:
        return []
    out: List[str] = []
    for seg in _segments(tokens):
        verb, operands = segment_verb(seg)
        if os.path.basename(verb) in _SHELL_VERBS and depth < 1:
            code = shell_code(operands)
            if code:
                out.extend(git_config_writes(code, cwd, depth + 1))
            continue
        if os.path.basename(verb) not in ("git", "git.exe"):
            continue
        sub, args = git_subcommand(operands)
        if sub != "config":
            continue
        flags, positional, file_arg, i = set(), [], None, 0
        while i < len(args):
            a = args[i]
            if a in _CONFIG_VALUE_FLAGS:
                if a in ("-f", "--file") and i + 1 < len(args):
                    file_arg = args[i + 1]
                i += 2
                continue
            if a.startswith("--file="):
                file_arg = a.split("=", 1)[1]
            elif a.startswith("-"):
                flags.add(a.split("=", 1)[0])
            else:
                positional.append(a)
            i += 1
        words = flags | set(positional[:1])
        if words & _CONFIG_READS:
            continue
        writes = bool(words & _CONFIG_WRITES) or len(positional) >= 2
        if not writes:
            continue
        if file_arg:
            out.append(file_arg)
            continue
        top, gitdirs = _git_dirs(cwd)
        if flags & {"--global", "--system"}:
            key = (positional[1] if positional[:1] == ["set"] and len(positional) > 1
                   else positional[0] if positional else "")
            if key.lower() == "core.hookspath" and gitdirs:
                out.append(os.path.join(gitdirs[-1], "config"))
            continue
        if not gitdirs:
            continue
        name = "config.worktree" if "--worktree" in flags else "config"
        out.append(os.path.join(gitdirs[0] if "--worktree" in flags else gitdirs[-1], name))
    return out


def hits(cwd: str, path: Optional[str], bash_targets: Sequence[str], command: str
         ) -> List[Tuple[str, str]]:
    """`[(destination, kind)]` for every write in this tool call that lands on git's hooks or config.
    `bash_targets` are the Bash lane's visible write destinations; `command` adds `git config`."""
    out: List[Tuple[str, str]] = []
    seen = set()
    for t in ([path] if path else []) + list(bash_targets):
        if t in seen:
            continue
        seen.add(t)
        k = kind_of(t, cwd)
        if k:
            out.append((t, k))
    for f in git_config_writes(command, cwd):
        if f not in seen:
            seen.add(f)
            out.append((f, CONFIG))
    return out


# ======================================================================================
# the human gate
# ======================================================================================

_WHY = {
    HOOK: "a git hook runs as code the next time someone commits, merges or pushes here, in their "
          "shell and with their credentials",
    CONFIG: "git's config names programs git runs (core.hooksPath, core.fsmonitor, filters, "
            "core.sshCommand)",
}


def call_args(tool_name: str, tool_input: Any, command: str, path: Optional[str]) -> Dict[str, Any]:
    """What the approval is bound to: the WHOLE call — the tool and its complete input."""
    return {"tool": tool_name or "", "input": tool_input if tool_input is not None else
            {"command": command, "path": path or ""}}


def preview_of(tool_input: Any, command: str) -> str:
    """The write as the human must read it before approving — in full (`approval.render` shows a
    git-control preview uncut)."""
    if isinstance(tool_input, dict):
        for key in ("content", "new_string", "new_source", "command"):
            v = tool_input.get(key)
            if isinstance(v, str) and v:
                return v
        edits = tool_input.get("edits")
        if isinstance(edits, list):
            return "\n---\n".join(str(e.get("new_string", "")) for e in edits if isinstance(e, dict))
    return command or ""


def refusal(cwd: str, session_id: str, hit: List[Tuple[str, str]], tool_name: str,
            tool_input: Any, command: str, path: Optional[str]) -> str:
    """"" when a human has approved exactly this call (the approval is burned here); else the
    BLOCKED line, which names the destination, why it is gated, and the one command that approves
    it. Proposes the write (writes only the proposal record) so the id in the message is real."""
    from . import approval
    from .gate_hook import find_mokata_root
    dest, kind = hit[0]
    head = (f"BLOCKED [{GATE_GIT_CONTROL}] {dest} — {_WHY[kind]}. An agent may write it only with a "
            f"human's approval of exactly this write.")
    root = find_mokata_root(cwd)
    if root is None:
        return (f"{head} This folder has no .mokata/ store, so mokata cannot record an approval "
                f"here: make the change yourself, outside the agent.")
    run_id = session_id or "no-session"
    args = call_args(tool_name, tool_input, command, path)
    pid = approval.proposal_id_for(run_id, approval.content_hash(GIT_CONTROL_TOOL, args))
    granted = approval.redeem(root, pid, tool=GIT_CONTROL_TOOL, args=args, run_id=run_id)
    if granted.granted:
        return ""
    targets = ", ".join(f"{d} ({k})" for d, k in hit)
    p = approval.propose(root, tool=GIT_CONTROL_TOOL, args=args, run_id=run_id, target=targets,
                         summary=f"{tool_name or 'tool'} writes {targets}",
                         preview=preview_of(tool_input, command))
    return (f"{head} Ask the user to run `mokata approve {p.proposal_id}` in their own terminal "
            f"(it shows this write in full), then make exactly the same call again.")


__all__ = ["CONFIG", "GATE_GIT_CONTROL", "GIT_CONTROL_TOOL", "HOOK", "call_args", "config_files",
           "git_config_writes", "hits", "hooks_dirs", "kind_of", "name_kind", "preview_of",
           "refusal"]
