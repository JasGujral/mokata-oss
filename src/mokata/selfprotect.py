"""SELF-PROTECT — installed code and out-of-workspace code are NEVER writable (0.0.16, stage 3).

THE LIVE BUG (re-groom #17, Jas 2026-07-18): an agent edited mokata's OWN code, in
``site-packages``. Nothing anywhere in the tree stopped it. ``gate_hook.check_write`` keys on RUN
STATE only, ``WriteGate.submit`` treats ``target`` as an opaque label, and the ONE site-packages
predicate that existed (``version.detect_install_method``) was cosmetic — it labelled a
``mokata version`` line. A write to installed code was, structurally, an ordinary write.

This module is the verdict that makes it not one. It answers ONE question about a target path, and
answers it BEFORE anything else is consulted:

    (a) RULE_INSTALLED      the realpath is inside a `site-packages`/`dist-packages` tree — ANY
                            installed package, not just mokata's. Installed code is never a
                            legitimate edit target: the wheel is the source of truth, an edit
                            there is invisible to git, survives no reinstall, and silently
                            diverges every other environment.
    (b) RULE_MOKATA_INSTALL the realpath is inside mokata's OWN installed package directory
                            (`package_data_root()`) — self-modification. Distinct from (a) because
                            a `/plugin install` route is NOT in site-packages.
    (c) RULE_OUT_OF_ROOT    the realpath is outside the workspace root (below) — the HP.S4
                            containment rule, pulled forward.

WHY IT IS NOT A GATE IN THE SI.1 SENSE — and why it fires FIRST
--------------------------------------------------------------
``gate_hook``'s gates are POSITIVELY TRIGGERED: they fire only when this run's own persisted state
proves a mokata pipeline is under way, and every uncertainty is an ALLOW (gate_hook.py:14-19). That
design is deliberate and it stays — but it is also exactly the bypass here, and the bypass is NOT
the Bash side door everyone assumed:

  * ``hook_cli.gate_guard_main`` exits 0 whenever ``find_mokata_root(cwd)`` is None — so a write
    launched from ANY cwd that is not an initialized mokata repo was never examined at all;
  * ``gate_hook.check_write`` allows when no run is registered — so even inside a mokata repo, a
    write outside a pipeline run was never examined either.

``site-packages`` is never inside a mokata repo and a self-edit is never inside a run, so BOTH
doors stood wide open. This check therefore keys on the TARGET PATH alone and runs ahead of
``find_mokata_root`` and ahead of every run-state gate — a deliberate, documented departure from
the positive-trigger design. It is the same class as ``secret-guard``: security, not methodology.

NON-OVERRIDABLE, by construction
--------------------------------
``GATE_SELF_PROTECT`` is deliberately NOT in ``gate_hook.GATES``, which is the ONLY set
``read_override`` will honour (gate_hook.py:436 filters to it) and the only set
``mokata gate override`` accepts (cli_commands/gate.py:79). So there is no P14 override to write.
There is also no env kill switch — this module reads NO environment variable, ever (a test asserts
it): an env var is a side door any process can open silently, which is the reasoning
``gate_hook.OVERRIDE_PREFIX`` already records for the P14 override itself. And inside the
``WriteGate`` the verdict is checked BEFORE the trust dial and before the human gate, mirroring the
secret hard block: an approval cannot whitelist a blocked tree.

THE WRITABLE REGION — what "workspace root" means
-------------------------------------------------
Grounded in what the PreToolUse envelope actually carries: ``cwd`` (hook_cli.py:236-241 reads
``payload.get("cwd")``). From it:

    workspace root = the nearest ancestor of `cwd` (inclusive) holding a repository marker —
                     `.mokata/manifest.json` (mokata's own root marker, the one
                     `gate_hook.find_mokata_root` tests) or `.git` (a directory OR a file, so a
                     git worktree resolves to the worktree) — else `cwd` ITSELF.

So a root is ALWAYS resolvable in practice, and the fallback is the narrowest honest answer rather
than "no containment". A write with genuinely NO resolvable root — ``cwd`` cannot be realpath'd
(the directory was deleted under us, or carries a NUL) — is treated FAIL-OPEN for rule (c) only:
rules (a) and (b) need no root and still fire. That split is the point. Fail-closed on (c) with no
root would refuse EVERY write in an unreadable-cwd process, which is the "gate that makes the
editor unusable" gate_hook.py:16-19 forbids; fail-open on (a)/(b) would surrender the actual
incident. The absolute rules never depend on a root; only containment does.

The writable region is the workspace root PLUS the process temp root(s)
(``tempfile.gettempdir()`` and the platform temp dirs). That allowance is deliberate and named:
scratch files under the system temp dir are a normal, constant part of how an agent works, and a
NON-OVERRIDABLE refusal of them would get the hook uninstalled. It relaxes ONLY rule (c) —
(a) and (b) are checked FIRST and are absolute, so a temp path that is also a site-packages tree
(a throwaway venv, which is exactly what this stage's regression test builds) is still REFUSED,
and "write to temp, then `cp` into site-packages" is refused at the `cp` destination.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import re
import shlex
import tempfile
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

# The gate's name in refusals. NOT in `gate_hook.GATES` — see the module docstring.
GATE_SELF_PROTECT = "self-protect"

# The three rules, as stable ids a test (and a reader of stderr) can assert on.
RULE_INSTALLED = "installed-package-tree"
RULE_MOKATA_INSTALL = "mokata-own-install"
RULE_OUT_OF_ROOT = "outside-workspace-root"

# The directory names that mark an installed-package tree. THE extraction of the codebase's only
# such predicate (`version.py`'s cosmetic install-method label, which now calls in here) — the
# stage brief's "extract, don't duplicate". Compared as PATH COMPONENTS rather than as the
# `os.sep + name + os.sep` substring version.py used, so `my-site-packages-notes/` is not a hit and
# the tree's own root directory is.
_INSTALL_DIR_NAMES = frozenset({"site-packages", "dist-packages"})

# What makes a directory a workspace root, nearest-first. `.mokata/manifest.json` is the marker
# `gate_hook.find_mokata_root` already tests; `.git` may be a directory OR a file (a worktree's
# `.git` is a file pointing at the real gitdir), so `exists`, not `isdir`.
_ROOT_MARKERS: Tuple[str, ...] = (os.path.join(".mokata", "manifest.json"), ".git")


# ======================================================================================
# verdict
# ======================================================================================

@dataclass(frozen=True)
class ProtectOutcome:
    """The path verdict for ONE write target (doc 85 §3: `*Outcome` = gate verdict).

    `target` is the RESOLVED path and is the only thing ever rendered — never file content, never
    the surrounding command. A refusal must be legible without leaking what was being written."""

    allowed: bool
    reason: str
    rule: Optional[str] = None          # None on an allow
    target: Optional[str] = None        # the resolved realpath (paths only, never content)

    @property
    def blocked(self) -> bool:
        return not self.allowed


# ======================================================================================
# path resolution — realpath + expanduser, degrade-clean
# ======================================================================================

def resolve_target(path: str, base_dir: Optional[str] = None) -> Optional[str]:
    """`path` as an absolute realpath with `~` expanded, or None when it cannot be resolved.

    `realpath` deliberately, not `normpath`: a symlink inside the workspace that points into
    site-packages must not launder an escape — the same reasoning `mcp/validation.guard_path`
    records, and the same realpath-compare shape as `gate_hook._same_repo`. It is used on a
    possibly non-existent path on purpose (a write CREATES its target), which realpath supports.

    `base_dir` is what a RELATIVE target is relative TO. It matters: a hook is a separate process
    whose own cwd is whatever launched it, while the envelope reports the cwd the TOOL is acting in
    (`hook_cli` reads `payload["cwd"]`). Resolving `src/api/items.py` against the hook's process cwd
    would judge a path the write will never touch — and, because that path is usually outside the
    workspace, would refuse a perfectly legal in-project write. Omitted, the process cwd is used
    (correct for `WriteGate`, which runs IN the writing process).

    Degrade-clean, per `_same_repo`: unresolvable -> None, and the caller treats None as an ALLOW
    it cannot judge. The only real sources are a NUL byte (ValueError) and a cwd that no longer
    exists (OSError) — and a path carrying a NUL is refused by the OS `open()` anyway.

    The NUL is rejected EXPLICITLY rather than left to `realpath`. That used to be load-bearing
    platform behaviour: `posixpath.realpath` raises ValueError for an embedded NUL on 3.12, but
    `ntpath.realpath` SWALLOWS it and falls back to the abspath'd string. The NUL therefore
    survived on Windows, `workspace_root_for` walked up to a real marker, and a cwd that cannot
    be resolved at all yielded a ROOT where POSIX correctly answered None — rule (c) judged
    containment against a root the caller never had. POSIX already answered None, so this is a
    Windows-only correction and POSIX behaviour is byte-identical."""
    if not path:
        return None
    if "\x00" in path:
        return None
    try:
        expanded = os.path.expanduser(path)
        if base_dir and not os.path.isabs(expanded):
            expanded = os.path.join(base_dir, expanded)
        return os.path.realpath(os.path.abspath(expanded))
    except (OSError, ValueError):
        return None


def _norm(path: str) -> str:
    """Case-folded on the platforms whose filesystems are (Windows); identity on POSIX."""
    return os.path.normcase(path)


def _within(child: str, parent: str) -> bool:
    """True when `child` IS `parent` or lives under it. Both must already be resolved."""
    c, p = _norm(child), _norm(parent)
    return c == p or c.startswith(p.rstrip(os.sep) + os.sep)


def is_path_like(target: str) -> bool:
    """Does this write target name a LOCATION at all?

    GROUNDED, and load-bearing for rule (c): `WriteRequest.target` is not always a path.
    `engine/emit.py` submits `"spec:emit"`; other callers submit similar logical labels (doc 95
    recorded it as "WriteGate treats `target` as an opaque label"). Resolving such a label as a
    relative path would place it under the process cwd and rule (c) would then refuse EVERY gated
    write whose label happens not to sit inside the workspace — a fabricated violation.

    So containment is judged only for a target that names a location: absolute, `~`-anchored, or
    carrying a separator. Rules (a) and (b) stay unconditional — they match on real tree membership,
    which a label cannot accidentally satisfy. A bare relative name IS judged when the caller
    supplies a `base_dir` (the hook lane always does), because then the location is known."""
    if not target:
        return False
    return (os.path.isabs(target) or target.startswith("~")
            or "/" in target or "\\" in target)


def _components(path: str) -> List[str]:
    norm = path.replace("\\", "/")
    return [p for p in norm.split("/") if p]


def in_installed_tree(resolved: str) -> bool:
    """Rule (a): does this resolved path sit inside a `site-packages`/`dist-packages` tree?

    THE predicate `version.detect_install_method` used to carry inline (version.py:63-64), now
    single-sourced here. Component-wise, so it also answers True for the tree's own directory."""
    return any(_norm(part) in _INSTALL_DIR_NAMES for part in _components(resolved))


def mokata_package_root(package_root: Optional[str] = None) -> Optional[str]:
    """mokata's own package directory, resolved — the installed-tree anchor for rule (b).

    `package_data_root()` is the right anchor because it resolves identically for a wheel
    (`site-packages/mokata`), an editable/clone install (`<repo>/src/mokata`) AND the plugin route
    — see its docstring. `package_root` is the injection seam for tests, mirroring
    `version.detect_install_method(package_file=...)`. Never raises."""
    if package_root is not None:
        return resolve_target(package_root)
    try:
        from . import package_data_root
    except ImportError:
        # NARROW on purpose (D5): the only class this can honestly raise is a half-installed
        # package whose own `__init__` will not import — the same class `secret_guard_main` narrows
        # its engine import to. `package_data_root` itself promises never to raise, so a BROAD catch
        # here would silently disable rule (b) on a real bug in it, and rule (b) is a security
        # verdict, not a cosmetic label (contrast `version.detect_install_method`, where the same
        # never-raise callee guards a display string and SUPPRESS_OK is honest).
        return None
    return resolve_target(str(package_data_root()))


def _temp_roots() -> Tuple[str, ...]:
    """The process temp root(s), resolved — the named allowance on rule (c) (see the docstring).

    `gettempdir()` honours TMPDIR (macOS: a per-user `/var/folders/...` dir); the literals cover
    the classic locations a harness may hand out directly. Resolved, so `/tmp` matches
    `/private/tmp` on macOS."""
    roots: List[str] = []
    for cand in (tempfile.gettempdir(), "/tmp", "/private/tmp", "/var/tmp"):
        resolved = resolve_target(cand)
        if resolved and resolved not in roots:
            roots.append(resolved)
    return tuple(roots)


def _is_harness_memory(resolved: str) -> bool:
    """Is `resolved` inside the host harness's per-project MEMORY store — the ONE carve-out?

    `84:69`. Installing mokata silently disabled the harness's memory feature for the project:
    `~/.claude/projects/<slug>/memory/` is outside every workspace root, so containment refused
    every write to it. The tempting wrong fix is to widen containment; this is the narrow,
    auditable shape instead — the same spirit as the temp-scratch allowance, and like it, it
    relaxes rule (c) ONLY. Rules (a) and (b) run FIRST and take no allowance, so a
    site-packages tree that somehow sat under the memory directory is still refused.

    THE LEAF, NOT THE PARENT — the whole safety argument in one line. `<slug>/` holds session
    TRANSCRIPTS; beside it sit `settings.json` (which switches mokata's own enforcement off),
    `hooks/`, and `plugins/` (which holds mokata's OWN launcher, so a write there rewrites the
    thing doing the blocking). Carving the parent would hand all of that away to buy the leaf,
    so the match is on the MEMORY kind alone and every other kind stays blocked.

    THE CONCESSION, NAMED rather than discovered later: memory is read back into a later
    session's context, so a writable memory directory IS a persistence vector — content
    written now can influence a future session. That is conceded knowingly, and it is exactly
    why the carve-out stops here: every path that could change what mokata or the harness
    EXECUTES stays refused.

    Shape-based and single-sourced, like `_temp_roots()`: `harness_paths` owns the layout and
    the decision is made on path COMPONENTS, never on `isdir` — the directory a write is about
    to create does not exist yet. `check_target` has already `realpath`'d the input, so a path
    that merely traverses THROUGH the memory directory cannot buy the exemption."""
    try:
        from .harness_paths import HARNESS_MEMORY, harness_path_kind
    except ImportError:
        return False                        # half-installed package -> no carve-out, fail safe
    try:
        return harness_path_kind(resolved) == HARNESS_MEMORY
    except (OSError, ValueError):
        return False


def _harness_capability(resolved: str) -> Optional[str]:
    """The human name of the HOST harness capability whose storage `resolved` sits in, or None.

    Message-only (`84:69`): it changes what a refusal SAYS, never whether one happens. The
    layout is single-sourced from `harness_paths`, which the installer and the MCP admin
    already share — this module holds no `.claude` literal of its own, and a test asserts it.

    Never raises: the import is narrowed the same way `mokata_package_root` narrows its own,
    and a half-installed package simply falls back to the generic containment message."""
    try:
        from .harness_paths import harness_capability_label, harness_path_kind
    except ImportError:
        return None
    try:
        return harness_capability_label(harness_path_kind(resolved))
    except (OSError, ValueError):
        return None


def workspace_root_for(cwd: str) -> Optional[str]:
    """The workspace root for a hook whose envelope reported `cwd` — see the module docstring.

    `*_for` per doc 85 §3: it always returns an answer (`cwd` itself is the floor). None ONLY when
    `cwd` cannot be resolved at all, which the caller treats as "rule (c) has no root"."""
    start = resolve_target(cwd or ".")
    if start is None:
        return None
    cur = start
    while True:
        for marker in _ROOT_MARKERS:
            if os.path.exists(os.path.join(cur, marker)):
                return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return start                # no marker anywhere up the tree -> cwd is the root
        cur = parent


# ======================================================================================
# THE check
# ======================================================================================

def check_target(path: str, workspace_root: Optional[str] = None,
                 package_root: Optional[str] = None,
                 base_dir: Optional[str] = None) -> ProtectOutcome:
    """May a write land on `path`? The one verdict both enforcement lanes call.

    Pure, total and side-effect-free: it never raises, never writes, and reads no environment
    variable. `workspace_root` is a raw directory (resolved here); None disables rule (c) ONLY —
    rules (a) and (b) need no root and always apply. `base_dir` anchors a RELATIVE target (see
    `resolve_target`) — the hook passes the envelope's cwd.

    Rule ORDER is meaningful: (a) and (b) are absolute and are answered first, so the temp
    allowance on rule (c) can never reach a path that is also installed code."""
    if not path:
        return ProtectOutcome(True, "no target path")
    resolved = resolve_target(path, base_dir=base_dir)
    if resolved is None:
        # Degrade-clean (`_same_repo`'s shape): a path mokata cannot resolve is a path it cannot
        # judge. The OS refuses it anyway (a NUL byte raises in `open()`), so this is not a hole
        # being conceded — it is a verdict declining to guess.
        return ProtectOutcome(True, "target path could not be resolved — not judged")

    # (a) ANY installed package tree. Absolute: no root needed, no allowance.
    if in_installed_tree(resolved):
        return ProtectOutcome(
            False,
            "the target is inside an installed Python package tree (site-packages/dist-packages). "
            "Installed code is never an edit target: the change is invisible to git, is lost on "
            "the next reinstall, and diverges every other environment. Edit the package's SOURCE "
            "repository instead",
            rule=RULE_INSTALLED, target=resolved)

    root = resolve_target(workspace_root) if workspace_root else None

    # (b) mokata's OWN installed package directory — self-modification. It is NOT a violation when
    # that directory is inside the workspace root: that is a dev checkout of mokata itself
    # (`<repo>/src/mokata`), where editing mokata's source IS the work (the spec's explicit carve).
    pkg = mokata_package_root(package_root)
    if pkg and _within(resolved, pkg) and not (root and _within(pkg, root)):
        return ProtectOutcome(
            False,
            "the target is inside mokata's own INSTALLED package directory — mokata does not "
            "modify its own installed code. To change mokata, edit its source checkout and "
            "reinstall",
            rule=RULE_MOKATA_INSTALL, target=resolved)

    # (c) containment. Skipped when no root is resolvable — fail-open for THIS rule only, and only
    # for that case, per the module docstring — and skipped for a target that names no location at
    # all (see `is_path_like`).
    if root is not None and (base_dir or is_path_like(path)) and not _within(resolved, root):
        if any(_within(resolved, t) for t in _temp_roots()):
            return ProtectOutcome(True, "outside the workspace root but inside the system temp "
                                        "root — the named scratch allowance")
        if _is_harness_memory(resolved):
            return ProtectOutcome(True, "outside the workspace root but inside the host "
                                        "harness's per-project memory store — the named "
                                        "capability allowance (this ONE leaf only)")
        capability = _harness_capability(resolved)
        if capability:
            # SAME verdict, HONEST prose (`84:69`). The path really is outside the workspace and
            # the rule really is doing its job — but "not this project's code" reads like a rogue
            # write was caught, when what actually happened is that a HOST capability was switched
            # off by containment and nobody was told. Nothing here changes the outcome: same rule
            # id, same refusal, same non-overridability.
            return ProtectOutcome(
                False,
                f"this is {capability}, not a file this project owns — so mokata's containment "
                f"rule refuses it, and that HOST CAPABILITY IS DISABLED while mokata is "
                f"installed here. Nothing was written and nothing is corrupt; the feature simply "
                f"does not work in this workspace. Containment holds the workspace root "
                f"({root}) plus the system temp dir",
                rule=RULE_OUT_OF_ROOT, target=resolved)
        return ProtectOutcome(
            False,
            f"the target is OUTSIDE the workspace root ({root}). Writes stay inside the workspace "
            f"(or the system temp dir); a path outside it is not this project's code",
            rule=RULE_OUT_OF_ROOT, target=resolved)

    return ProtectOutcome(True, "inside the workspace root and not an installed tree")


def render_refusal(outcome: ProtectOutcome) -> str:
    """The legible refusal (doc 85 §3: `render_*` = human string, never writes).

    Names the PATH and the RULE and says plainly that there is no override — a refusal the reader
    cannot act on is a refusal they will work around. Never renders content: `ProtectOutcome`
    carries none, by construction."""
    return (f"BLOCKED [{GATE_SELF_PROTECT}/{outcome.rule}] {outcome.target}\n"
            f"mokata: {outcome.reason}.\n"
            f"This is a security-class block: it is NOT overridable — there is no "
            f"`mokata gate override` for it and no environment switch.")


# ======================================================================================
# the Bash lane — write DESTINATIONS parsed out of a shell command
# ======================================================================================
# `gate-guard` used to match file-mutation tools only, and shelling out to `sed -i` was a KNOWN,
# DOCUMENTED hole (harness_setup.py:101-104). It stays a hole for the run-state gates (they decide
# from a path a Bash command may not have) but it cannot stay one here: a block on native Write
# that a one-line `sed -i` walks around is theatre.
#
# HONEST BOUNDS. This is a heuristic net over a Turing-complete language, not a shell. It sees:
#   * output redirections            `> f`, `>> f`, `>f`, `1> f`, `2>> f`, `>| f`
#   * in-place editors               `sed -i`/`-ri`/`--in-place`, `perl -i`, `ruby -i`
#   * whole-operand writers          `tee`, `truncate`, `dd of=`, `patch`, `sponge`
#   * destination-last writers       `cp`, `mv`, `install`, `rsync`, `ln`, `rename`
#   * inline Python                  `python -c` with a literal `open(p, 'w'…)`,
#                                    `Path(p).write_text/bytes`, `shutil.copy*/move`, `os.rename`
#   * one level of `sh -c`/`bash -c`/`zsh -c` nesting
# It does NOT see (FILED, not claimed covered — see `tests/test_self_protect.py`'s
# `PARSER_BLIND_SPOTS`): variable expansion (`$TARGET`), command substitution (`$(…)`),
# `eval`/base64-decoded commands, a path assembled at runtime inside the Python string, heredoc
# bodies, `find -exec`, `xargs`, and any interpreter not listed above. Those remain reachable via
# Bash. The native-tool lane and the WriteGate lane are exact; this lane is a net, and saying so is
# the point — an overclaimed guard is worse than a stated partial one.

# Operator tokens, once `shlex` is told to treat shell punctuation as punctuation (below).
_SEGMENT_SEPS = frozenset({";", "&&", "||", "|", "|&", "&", "\n", "(", ")", ";;"})
_OUT_REDIRECTS = frozenset({">", ">>", ">|", ">>|", ">&", "&>", "&>>"})
_IN_REDIRECTS = frozenset({"<", "<<", "<<<", "<&"})
# Character devices that a redirection may name. Writing to one creates and modifies NOTHING —
# `> /dev/null` discards, `> /dev/stdout` is the stream the process already owns — so a device is
# not a write TARGET and never was. `84:67`: `2>/dev/null` was refused as an out-of-workspace write
# 10 times across 5 sessions, on read-only `grep`/`find`/`ls` reads and on the `initdb` that stands
# up the live-DB verification, each time aborting the WHOLE compound command.
#
# A CLOSED set of exact paths, deliberately — not a `/dev/*` prefix. `/dev/shm/<name>` is a real
# file on a tmpfs (a genuine out-of-workspace write), and a prefix rule would surrender it. `/dev/fd`
# is the one member with a variable tail, so it is matched as `/dev/fd/<digits>` — a numbered
# descriptor, never a name. Matched on the token AS WRITTEN: `/dev/null/../../etc/passwd` is not a
# member of this set and still blocks.
_CHAR_DEVICES = frozenset({"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty"})
_DEV_FD_RE = re.compile(r"/dev/fd/\d+\Z")
# `>&`/`&>` followed by a BARE descriptor number (`2>&1`) or `-` (`2>&-`) is a file-descriptor DUP
# or close, not a path. `1` was reaching the target list as if it named a file.
_FD_DUP_RE = re.compile(r"\d+\Z|-\Z")
_FD_REDIRECTS = frozenset({">&", "&>", "&>>"})
# Flags that carry sed/perl's SCRIPT, so the first bare operand is a FILE rather than the script.
_EXPR_FLAGS = frozenset({"-e", "--expression", "-f", "--file", "--script"})
_IN_PLACE_VERBS = frozenset({"sed", "gsed", "perl", "ruby"})
_ALL_OPERAND_VERBS = frozenset({"tee", "truncate", "patch", "sponge"})
_LAST_OPERAND_VERBS = frozenset({"cp", "mv", "install", "rsync", "ln", "rename"})
_PY_VERBS = frozenset({"python", "python3", "python2", "py", "pypy", "pypy3"})
_SHELL_VERBS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})
# Words that PRECEDE the real verb (`sudo tee /etc/x`), skipped alongside any `VAR=value` prefix so
# the verb is found. `dd of=…` is unaffected: `dd` is neither a prefix word nor an assignment.
_PREFIX_WORDS = frozenset({"env", "sudo", "command", "nohup", "time", "doas", "nice"})

# Literal write forms inside a `python -c` payload. Modes are checked for w/a/x/+.
_PY_OPEN_RE = re.compile(
    r"open\(\s*(?P<q>['\"])(?P<path>[^'\"]+)(?P=q)\s*,\s*(?P<mq>['\"])(?P<mode>[^'\"]*)(?P=mq)")
_PY_WRITE_RE = re.compile(
    r"(?:Path|PurePath)\(\s*(?P<q>['\"])(?P<path>[^'\"]+)(?P=q)\s*\)\s*\.\s*write_(?:text|bytes)")
_PY_COPY_RE = re.compile(
    r"shutil\.(?:copy2?|copyfile|copytree|move)\(\s*[^,()]+,\s*(?P<q>['\"])(?P<path>[^'\"]+)(?P=q)")
_PY_OSMUT_RE = re.compile(
    r"os\.(?:rename|replace)\(\s*[^,()]+,\s*(?P<q>['\"])(?P<path>[^'\"]+)(?P=q)")


def _basename_command(token: str) -> str:
    """The verb, stripped of any path (`/usr/bin/sed` -> `sed`) and of a `.exe` tail."""
    base = os.path.basename(token.replace("\\", "/"))
    return base[:-4] if _norm(base).endswith(".exe") else base


def _is_flag(token: str) -> bool:
    return token.startswith("-") and token != "-"


def _looks_like_path(token: str) -> bool:
    """Conservative: a destination worth judging carries a separator, a `~`, or a dot-segment.

    A bare `file.txt` operand is deliberately NOT judged — it is relative to a cwd this parser was
    not given, and every path this gate blocks (site-packages, another root, a home dir) reaches
    the command line WITH separators. Guessing at bare names would trade a real class of false
    positives for no extra coverage."""
    if not token or _is_flag(token):
        return False
    return ("/" in token) or ("\\" in token) or token.startswith("~") or token in (".", "..")


def _python_targets(code: str) -> List[str]:
    out: List[str] = []
    for m in _PY_OPEN_RE.finditer(code):
        if any(c in m.group("mode") for c in "wax+"):
            out.append(m.group("path"))
    for rx in (_PY_WRITE_RE, _PY_COPY_RE, _PY_OSMUT_RE):
        out.extend(m.group("path") for m in rx.finditer(code))
    return out


def _tokenize(command: str) -> Optional[List[str]]:
    r"""Shell-like tokens, with OPERATORS as their own tokens.

    `shlex.split` is NOT enough and that is a real bug this stage hit: it splits on whitespace and
    honours quotes, but it does not know `;`/`&&`/`>` are operators — so `cd /tmp; tee <path>`
    tokenized to `['cd', '/tmp;', 'tee', …]` and the segment split never happened, letting the
    write through. `punctuation_chars=True` is the documented recipe for shell-like lexing: it makes
    `();<>|&` punctuation and groups runs of them, so `>>`, `2>>` (`2`, `>>`) and `2>&1`
    (`2`, `>&`, `1`) all come apart correctly. None on an unparseable command.

    WINDOWS — `lex.escape`. In posix mode `\` is the ESCAPE character, but on Windows it is the
    PATH SEPARATOR. Left as an escape it silently ate the separators out of every Windows path,
    and that one defect broke containment in BOTH directions:

      * UNDER-block — `sed -i s/x/y/ C:\…\site-packages\pkg\mod.py` collapsed to the single token
        `C:…site-packagespkgmod.py`. With no separator left there is no `site-packages`
        COMPONENT for rule (a) to match, and `_looks_like_path` no longer sees a path at all, so
        the write was not even judged: exit 0 where 2 was required.
      * OVER-block — `echo hi > C:\…\repo/notes.md` collapsed to `C:…repo/notes.md`, which is
        RELATIVE. Resolved against the process cwd it lands outside the workspace, so rule (c)
        refused an ordinary in-repo write.

    Clearing `escape` on Windows makes `\` an ordinary character; quote-stripping, whitespace
    splitting and operator grouping are untouched. POSIX is deliberately left alone — there `\`
    really is the shell's escape (`/tmp/a\ b.py` is ONE file) and changing it would break real
    quoting, so this is platform-gated and POSIX tokenization stays byte-identical."""
    lex = shlex.shlex(command, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    if os.name == "nt":
        lex.escape = ""
    try:
        return list(lex)
    except ValueError:
        return None                     # unbalanced quotes -> nothing to judge


def _segments(tokens: Sequence[str]) -> List[List[str]]:
    """Split a token stream into command segments on shell control operators."""
    segs: List[List[str]] = [[]]
    for tok in tokens:
        if tok in _SEGMENT_SEPS:
            segs.append([])
        else:
            segs[-1].append(tok)
    return [s for s in segs if s]


def _is_device_operand(token: str) -> bool:
    """Is this redirection operand a character device rather than a file? See `_CHAR_DEVICES`."""
    return token in _CHAR_DEVICES or bool(_DEV_FD_RE.fullmatch(token))


def _split_redirects(tokens: List[str]) -> Tuple[List[str], List[str]]:
    """(redirection destinations, remaining command words) for ONE segment.

    An INPUT redirection's operand is dropped too: `tee f < /dev/null` must not offer `/dev/null`
    to `tee`'s all-operands rule as if it were a write destination.

    An OUTPUT redirection's operand is dropped when it is a character device or a file-descriptor
    dup (`2>&1`) — neither names a file. The operand is dropped, NOT the segment: a real
    destination beside it (`sed -i s/a/b/ <installed> 2>/dev/null`) is still judged.

    A redirection's LEADING descriptor number (`2` in `2>f`) is dropped from the command words too.
    `punctuation_chars` lexing emits it as its own token, so it was reaching the verb rules as an
    operand — and it is the LAST one, which silently defeated the destination-last verbs:
    `cp evil.py <installed> 2>/dev/null` resolved its tail to `2`, found no path, and saw NO
    target. That hole was masked by the `/dev/null` false positive this change removes, so closing
    it here is what keeps the change from NARROWING detection. A bare integer is never a write
    destination under any rule, so dropping it can cost no target."""
    dests: List[str] = []
    words: List[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in _OUT_REDIRECTS or tok in _IN_REDIRECTS:
            if words and words[-1].isdigit():
                words.pop()                 # the redirect's own fd prefix, not an operand
        if tok in _OUT_REDIRECTS:
            if i + 1 < len(tokens) and tokens[i + 1] not in _SEGMENT_SEPS:
                operand = tokens[i + 1]
                if not (_is_device_operand(operand)
                        or (tok in _FD_REDIRECTS and _FD_DUP_RE.fullmatch(operand))):
                    dests.append(operand)
                i += 2
                continue
        elif tok in _IN_REDIRECTS:
            i += 2 if i + 1 < len(tokens) else 1
            continue
        else:
            words.append(tok)
        i += 1
    return dests, words


def _expr_flag(token: str) -> bool:
    """Does this flag carry the script (so the operand after it is the SCRIPT, not a file)?

    `-e`/`-f`/`--expression`/`--file`, and short clusters containing them (`perl -pe`, `sed -rf`)."""
    if not _is_flag(token):
        return False
    if token.startswith("--"):
        return token.split("=")[0] in _EXPR_FLAGS
    return "e" in token[1:] or "f" in token[1:]


def _in_place_files(operands: List[str]) -> List[str]:
    """The FILES an in-place editor rewrites — the script expression excluded.

    Measured bug, twice over: `sed -i 's/a/b/' f` puts the script in the first BARE operand and
    `s/a/b/` carries slashes, so "any operand with a separator" judged the SCRIPT as the target
    (wrong rule, wrong path); and `perl -i -pe 's/a/b/' f` puts it in the operand AFTER the flag.
    Both shapes are handled, and an empty `-i` argument (BSD `sed -i '' …`) is dropped so it cannot
    absorb the drop-the-script slot."""
    files: List[str] = []
    saw_expr, skip_next = False, False
    for tok in operands:
        if skip_next:
            skip_next = False
            continue
        if _is_flag(tok):
            if _expr_flag(tok):
                saw_expr = True
                skip_next = "=" not in tok      # the script is the NEXT argument
            continue
        if tok != "":
            files.append(tok)
    if not saw_expr:
        files = files[1:]                       # the bare script expression
    return [t for t in files if _looks_like_path(t)]


def _segment_targets(tokens: List[str], depth: int) -> List[str]:
    out, words = _split_redirects(tokens)
    if not words:
        return out
    # Skip a leading `env`/`sudo`/`command`/`nohup` and any VAR=value prefix so the real verb is
    # found (`sudo tee /etc/x`, `FOO=1 sed -i …`).
    idx = 0
    while idx < len(words) and (words[idx] in _PREFIX_WORDS
                                or ("=" in words[idx] and not _is_flag(words[idx])
                                    and "/" not in words[idx].split("=")[0])):
        idx += 1
    verb = _basename_command(words[idx]) if idx < len(words) else ""
    operands = words[idx + 1:]

    if verb in _IN_PLACE_VERBS:
        long_ok = any(t == "--in-place" or t.startswith("--in-place=") for t in operands)
        short_ok = any(t.startswith("-") and not t.startswith("--") and "i" in t[1:]
                       for t in operands)
        if long_ok or short_ok:
            out.extend(_in_place_files(operands))
    elif verb in _ALL_OPERAND_VERBS:
        out.extend(t for t in operands if _looks_like_path(t))
    elif verb == "dd":
        out.extend(t.split("=", 1)[1] for t in operands if t.startswith("of="))
    elif verb in _LAST_OPERAND_VERBS:
        tail = [t for t in operands if not _is_flag(t)]
        if tail and _looks_like_path(tail[-1]):
            out.append(tail[-1])
    elif verb in _PY_VERBS:
        for j, tok in enumerate(operands):
            if tok == "-c" and j + 1 < len(operands):
                out.extend(_python_targets(operands[j + 1]))
    elif verb in _SHELL_VERBS and depth < 1:
        for j, tok in enumerate(operands):
            if tok == "-c" and j + 1 < len(operands):
                out.extend(bash_write_targets(operands[j + 1], depth=depth + 1))
    return out


def bash_write_targets(command: str, depth: int = 0) -> List[str]:
    """Every write DESTINATION this parser can see in a shell command, in order, deduped.

    Never raises: an unparseable command (unbalanced quotes) yields `[]` — nothing to judge — which
    is the same fail-open floor the rest of the hook keeps. See the bounds note above; what escapes
    this parser is FILED, not covered."""
    if not command or not command.strip():
        return []
    tokens = _tokenize(command)
    if tokens is None:
        return []
    seen, out = set(), []
    for seg in _segments(tokens):
        for target in _segment_targets(seg, depth):
            if target and target not in seen:
                seen.add(target)
                out.append(target)
    return out


def check_command(command: str, workspace_root: Optional[str] = None,
                  package_root: Optional[str] = None,
                  base_dir: Optional[str] = None) -> ProtectOutcome:
    """The FIRST blocked destination in a shell command, or an allow.

    Total, like `check_target`: a command with no visible destination is an allow."""
    for target in bash_write_targets(command):
        outcome = check_target(target, workspace_root=workspace_root, package_root=package_root,
                              base_dir=base_dir)
        if outcome.blocked:
            return outcome
    return ProtectOutcome(True, "no blocked write destination visible in the command")


__all__ = [
    "GATE_SELF_PROTECT", "RULE_INSTALLED", "RULE_MOKATA_INSTALL", "RULE_OUT_OF_ROOT",
    "ProtectOutcome", "bash_write_targets", "check_command", "check_target", "in_installed_tree",
    "mokata_package_root", "render_refusal", "resolve_target", "workspace_root_for",
]
