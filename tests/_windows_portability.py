"""THE WINDOWS-PORTABILITY SWEEP — pure detectors over a WALKED corpus.

WHY THIS FILE EXISTS AND `tests/test_windows_shell_and_paths.py` DOES NOT HOLD IT.
That guard was written to close the two Windows classes of the 0.0.18 cut, was graded against a
planted offender and a near-miss it must acquit, and **two cause-B sites still walked past it** —
because its corpus was ENUMERATED: `os.listdir(TESTS_DIR)` (top level of `tests/` only, so not
`tests/integration/`, not `src/`, not `scripts/`) for cause A, and for cause B a hand-written list
of exactly TWO corpus builders by module-and-function name. A sweep whose corpus is a list is a
claim that the list is complete, and this is the sixth time in 0.0.18 that claim has been false —
the first time inside a guard written to close the class.

So the corpus here is WALKED (`walked_sources`), the detectors are PURE (they take a
`{name: source}` dict, so they can be handed a synthetic offender rather than trusted because the
real tree happens to be clean — doc 85 §7i), and every exemption is a ROW WITH A REASON rather
than a silence.

⚠ WALKED CORPUS, DECLARED THRESHOLDS. The distinction matters and is the whole lesson: what must
never be enumerated is the set of files the sweep READS. A rule's *threshold* may be declared — the
System32 shadow list below is a fact about Windows, not a claim about this repo — because a reader
can check it against Windows, whereas nobody can check a corpus list against "every file that
might have the defect".

THE CAUSES, and there are more than the two the cut named. Causes A and B are the 0.0.18 pair;
C, D and E were derived from the ten failures on run 32094654167 and account for five of them,
which is why a sweep for A and B alone could never have found the round's remaining work.

  CAUSE A — a bare program name as argv[0]. `subprocess.run(["bash", ...])` hands CreateProcess a
    bare name, and **CreateProcess searches System32 BEFORE PATH**. On `windows-latest`
    `C:\\Windows\\System32\\bash.exe` is the WSL launcher, which has no distribution installed,
    prints its refusal in UTF-16 and exits 1 — so every downstream assertion graded WSL's message
    instead of the script's. `shutil.which` searches PATH in PATH's order and finds Git Bash.

  CAUSE B — a `/`-spelled literal compared against an OS-built path. A repo-relative path from
    `os.path.join`/`os.path.relpath` is `mokata\\deprecation.py` on Windows; every declaration it
    is compared against is `/`-spelled by a human. Three detectable shapes, below.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import ast
import os
import re

import _support

#: The trees a Windows-portability sweep must reach. `tests/` alone was the old corpus, and
#: `src/` is where the release-check refusal built `"%s/src"` — a PRODUCT defect a tests-only
#: sweep is structurally incapable of seeing.
WALK_ROOTS = ("tests", "src", "scripts")

_SKIP_DIRS = {"__pycache__", ".git", ".mokata", "node_modules", ".venv", "venv"}

#: `subprocess`, however it is imported. A spawner is only a spawner when it is qualified by the
#: module: the previous sweep matched a bare `run(...)`/`Popen(...)` by name and therefore
#: convicted `run(["baseline", "--path", d])` and `run(["config", "--get", …])` — local CLI-driver
#: helpers that spawn nothing. Four false convictions out of thirty-seven is not a rounding error
#: in a guard whose whole output is a list of things a human must now go and change.
_SUBPROCESS_MODULES = frozenset({"subprocess", "sp"})
_SPAWNERS = frozenset({"run", "Popen", "call", "check_call", "check_output"})

# --------------------------------------------------------------------- the System32 shadow set
#
# PROVENANCE, because a threshold that cannot be checked is no better than a corpus that cannot.
#
#   PRIMARY SOURCE — Microsoft Learn, "Windows commands" A-Z reference
#   https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/windows-commands
#   (fetched 2026-08-18; page ms.date 2025-07-29). It opens: *"All supported versions of Windows
#   and Windows Server have a set of Win32 console commands built in."* Every name below marked
#   (doc) is listed there.
#
#   SECONDARY — names that ship in System32 but are ABSENT from that A-Z page, marked (extra):
#   `curl` and `tar` (shipped in System32 since Windows 10 1803) and `bash` (the WSL launcher,
#   `C:\\Windows\\System32\\bash.exe`).
#
# ⚠ CONFIDENCE: **the documented list is NOT a complete inventory of System32, and the proof is
# `bash` itself.** `bash.exe` demonstrably sits in System32 on `windows-latest` — that is the whole
# mechanism behind `BARE-BASH-ARGV-RESOLVES-TO-WSL`, twenty failures at the 0.0.18 cut — and it
# appears NOWHERE on Microsoft's A-Z command page. So a declared list is a lower bound with
# unknown slack, and the one name it provably omitted is the one that cost us the cut.
#
# ⭐ WHICH IS WHY THE DECLARED SET IS NOT THE CONTROL. `shadowed_on_host()` below asks the REAL
# `%SystemRoot%\\System32` when it is running on Windows, and `TestTheShadowSetIsCheckedOnWindows`
# reds if the declared set and the real directory disagree. On POSIX the declared set is the best
# available approximation and is used as such; on the platform where it matters, it is verified.
# Declared thresholds are allowed — declared thresholds that nothing ever checks are not.
# ⚠ cmd.exe INTERNAL commands are deliberately ABSENT — `echo`, `set`, `type`, `help`, `start`,
# `path`, `cd`, `dir`, `rem`, `if`, `for`. Microsoft's A-Z page documents them as *commands*, but
# they are built into cmd.exe and there is no `echo.exe` in System32, so `CreateProcess("echo")`
# fails "not found" rather than silently running something else. They are not this hazard, and
# including them made the sweep convict `("route", "handler", "endpoint")` — a data tuple.
# The set is EXECUTABLES, not commands, and that distinction is the whole precision of it.
SYSTEM32_SHADOWED = frozenset({
    # (doc) — on Microsoft's A-Z page AND a real file in System32
    "find", "findstr", "sort", "more", "where", "whoami", "timeout", "print", "fc",
    "label", "mode", "replace", "expand", "certutil", "ftp", "net", "reg", "tree",
    "convert", "recover", "shutdown", "hostname", "ping", "route", "telnet", "tftp",
    "attrib", "comp", "compact", "forfiles", "cmd", "robocopy", "xcopy", "subst",
    "schtasks", "takeown", "icacls", "cacls", "systeminfo", "tasklist", "taskkill",
    # (extra) — ship in System32, absent from that page. `bash` is the one that bit us.
    "bash", "curl", "tar",
})

#: `PATHEXT`'s default, for asking "would CreateProcess find a program by this bare name?".
_WINDOWS_EXECUTABLE_SUFFIXES = (".com", ".exe", ".bat", ".cmd")


def system32_dir():
    """The real System32 directory, or None when not running on Windows."""
    if os.name != "nt":
        return None
    root = os.environ.get("SystemRoot") or os.environ.get("windir") or r"C:\\Windows"
    path = os.path.join(root, "System32")
    return path if os.path.isdir(path) else None


def shadowed_on_host(name):
    """Does a bare `name` resolve to something in System32 on THIS machine?

    Returns None off Windows — three states, not two (§7g): "shadowed", "not shadowed", and
    "this host cannot answer". A POSIX box collapsing the third into the second is how a
    Windows-only hazard gets graded green by a machine that has never seen it."""
    base = system32_dir()
    if base is None:
        return None
    stem = os.path.splitext(name)[0]
    return any(os.path.isfile(os.path.join(base, stem + suffix))
               for suffix in _WINDOWS_EXECUTABLE_SUFFIXES)


def is_shadowed(name):
    """The predicate the sweep uses: the REAL directory on Windows, the declared set elsewhere."""
    live = shadowed_on_host(name)
    if live is not None:
        return live
    return os.path.splitext(name)[0].lower() in SYSTEM32_SHADOWED

#: The conversions that turn an OS path into a repo NAME. A call wrapped in one of these has
#: already answered the question this sweep asks.
_POSIX_WRAPPERS = frozenset({"as_posix", "posix_rel", "posix_name"})

#: Names and calls whose value is an ABSOLUTE, OS-SPELLED filesystem path — the only kind for
#: which welding a `/` on is wrong. Deliberately NARROW: `repos/{repo}/branches/{branch}` is a
#: GitHub API route, `HEAD:{MOKATA_DIR}/{MANIFEST}` is a git revision spec, and `{skills_dir}/
#: <name>/SKILL.md` is a display template — all three are `/`-spelled by their own grammar, not
#: by the filesystem's, and a detector that convicted them would be answered with twenty-one
#: exemptions and then switched off. What is left is the case that actually shipped wrong: a
#: native absolute path with a `/` welded onto it, in a command a human is told to run.
_PATHY_NAME = re.compile(r"(^|_)(root|cwd|home|checkout|workdir|tmpdir)s?$", re.IGNORECASE)

#: Calls that RETURN an absolute filesystem path.
_PATHY_CALL = frozenset({"realpath", "abspath", "getcwd", "mkdtemp", "gettempdir"})

#: Calls that CONSUME a path as a path — a `join` inside one of these is a filesystem operand,
#: not a name being matched against a declaration, so its separator is correct as the OS spells it.
_PATH_CONSUMERS = frozenset({"exists", "isfile", "isdir", "islink", "getsize", "open", "listdir",
                             "makedirs", "mkdir", "remove", "rmtree", "read_text", "stat"})

_ASSERTIONS = frozenset({"assertEqual", "assertNotEqual", "assertIn", "assertNotIn",
                         "assertCountEqual", "assertListEqual", "assertSetEqual",
                         "assertDictEqual", "assertTrue", "assertFalse", "assertGreater"})

#: String methods that MATCH one value against another. `rel.startswith(self._LEDGER)` is a
#: comparison spelled as a method call, and it is exactly how `test_h6_mint_site` applies the
#: carve-out that Gate B lost on Windows — so a detector that only understood `==` and `in`
#: would walk past half of Gate B while claiming to cover it.
_MATCHERS = frozenset({"startswith", "endswith"})


# ---------------------------------------------------------------------------- the WALKED corpus
def walked_sources(root, roots=WALK_ROOTS):
    """`{repo-relative posix path: source}` for every `.py` file under `roots`, WALKED.

    The corpus is the filesystem, not a list. A test file added and not yet committed carries the
    defect exactly as well as a tracked one, and the point of the guard is to catch it before it
    reaches a Windows leg — so this walks the working tree rather than asking the index."""
    out = {}
    for base in roots:
        top = os.path.join(root, base)
        if not os.path.isdir(top):
            continue
        for dirpath, dirnames, filenames in os.walk(top):
            dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
            for name in sorted(filenames):
                if not name.endswith(".py"):
                    continue
                path = os.path.join(dirpath, name)
                rel = _support.posix_rel(path, root)
                try:
                    with open(path, encoding="utf-8") as handle:
                        out[rel] = handle.read()
                except OSError:                 # pragma: no cover - unreadable file
                    continue
    return out


def _parse(source):
    try:
        return ast.parse(source)
    except SyntaxError:                         # pragma: no cover - a broken file reds elsewhere
        return None


def _callee(node):
    """`(module, attribute)` for a call, where module is None unless it is `mod.attr(...)`."""
    func = node.func
    if isinstance(func, ast.Attribute):
        mod = func.value.id if isinstance(func.value, ast.Name) else None
        return mod, func.attr
    return None, getattr(func, "id", "")


def _is_spawn(node):
    if not isinstance(node, ast.Call):
        return False
    mod, attr = _callee(node)
    return mod in _SUBPROCESS_MODULES and attr in _SPAWNERS


# ------------------------------------------------------------------ CAUSE A — bare-name argv[0]
def bare_argv0_sites(sources):
    """Every `subprocess` spawn whose argv[0] is a bare program NAME.

    Returns `(name, lineno, program, shadowed)` — `shadowed` True when Windows resolves that name
    out of System32 ahead of PATH, which is the difference between "this is theoretically
    PATH-order-dependent" and "this ran the WSL launcher on the 0.0.18 cut"."""
    found = []
    for name, source in sorted(sources.items()):
        tree = _parse(source)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not _is_spawn(node) or not node.args:
                continue
            argv = node.args[0]
            if not isinstance(argv, (ast.List, ast.Tuple)) or not argv.elts:
                continue
            first = argv.elts[0]
            if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                continue
            program = first.value
            if "/" in program or "\\" in program or os.path.isabs(program):
                continue                        # already resolved to a location
            found.append((name, node.lineno, program, is_shadowed(program)))
    return tuple(found)


# ------------------------------------------------------------------- CAUSE A' — inherited stdin
def shell_spawn_without_stdin_sites(sources):
    """Every `subprocess` spawn that runs a SHELL and does not say what its stdin is.

    A shell subprocess with inherited stdin is the shape behind the 0.0.18 round-2 wedge: the
    bash repair turned a subprocess that failed in milliseconds (WSL, exit 1) into one that
    actually runs, and a shell that actually runs can read fd 0 and block forever. `release.sh`'s
    `run_test_preflight` already redirects `< /dev/null` and its comment says why; nothing else
    in the tree did.

    A shell is: `shell=True`, an argv[0] naming a shell, or an argv built by `bash_argv`."""
    shells = {"bash", "sh", "zsh", "dash", "ksh", "cmd", "powershell", "pwsh"}
    found = []
    for name, source in sorted(sources.items()):
        tree = _parse(source)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not _is_spawn(node):
                continue
            keywords = {k.arg for k in node.keywords if k.arg}
            # `input=` IS an answer to "what is this process's stdin": subprocess opens a pipe,
            # writes the payload and closes it, and the two kwargs are mutually exclusive. A
            # detector that demanded `stdin=` beside it would be asking for a TypeError.
            if "stdin" in keywords or "input" in keywords:
                continue
            if any(k.arg is None for k in node.keywords):
                # `**kwargs` — the answer may be in there and the AST cannot see it. Reported, not
                # silently acquitted: an unreadable answer is not a given answer (doc 85 §7g).
                found.append((name, node.lineno, "**kwargs (undecidable — read the call site)"))
                continue
            why = None
            if any(k.arg == "shell" and getattr(k.value, "value", False) is True
                   for k in node.keywords):
                why = "shell=True"
            elif node.args:
                argv = node.args[0]
                if isinstance(argv, ast.Call) and _callee(argv)[1] == "bash_argv":
                    why = "bash_argv"
                elif isinstance(argv, (ast.List, ast.Tuple)) and argv.elts:
                    first = argv.elts[0]
                    if isinstance(first, ast.Constant) and isinstance(first.value, str) \
                            and os.path.splitext(os.path.basename(first.value))[0].lower() in shells:
                        why = "argv0=%s" % first.value
                    elif isinstance(first, ast.Name) and first.id in {"SH", "SHELL", "shell", "BASH"}:
                        why = "argv0=%s" % first.id
            if why:
                found.append((name, node.lineno, why))
    return tuple(found)


# -------------------------------------------------- CAUSE B.1 — a relpath IS a name, not a path
def unwrapped_relpath_sites(sources):
    """Every `os.path.relpath(...)` whose result is not passed through `as_posix`/`posix_rel`.

    `relpath` exists to answer *"what is this file called, relative to there?"* — and that is a
    NAME. Names in this repo are `/`-spelled, in corpus keys, exemption tables, doc surfaces and
    the commands documents tell contributors to run. On Windows an unwrapped `relpath` spells
    that name with `\\` and every comparison against a human-written declaration is false.

    This is the detector that finds BOTH escapees in `tests/test_stage9_removal_release.py` — and
    it finds them at the BUILDER, which is where one repair fixes every assertion fed by it,
    rather than at the individual `assertIn` the CI log happened to name.

    ⚠ SCOPED TO IDENTITY USE, and the narrowing is the difference between a finding and a wall.
    `relpath` answers "what is this called relative to there?", but the answer is only a NAME when
    something later matches on it — a dict key, a set member, a comparison. A relpath handed
    straight to `open()` or interpolated into a log line is a PATH, and on Windows a backslash
    there is not merely acceptable, it is correct. Convicting all 56 raw sites would bury the
    real ones under fifty that need no change, which is how a guard gets tuned off."""
    found = []
    for name, source in sorted(sources.items()):
        tree = _parse(source)
        if tree is None:
            continue
        wrapped, identity = set(), set()
        # `rel = os.path.relpath(...)` then `corpus[rel] = text` is the SAME defect one hop away,
        # and it is the shape `_docs_corpus` uses — i.e. the shape of the escapee this detector
        # exists to catch. A purely syntactic identity check walks straight past it, so bindings
        # are followed: a name bound to a relpath inherits that relpath's site.
        bound = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                    and isinstance(node.targets[0], ast.Name):
                for inner in ast.walk(node.value):
                    if isinstance(inner, ast.Call) and _callee(inner)[1] == "relpath":
                        bound.setdefault(node.targets[0].id, []).append(inner)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _callee(node)[1] in _POSIX_WRAPPERS:
                for inner in ast.walk(node):
                    if isinstance(inner, ast.Call) and inner is not node:
                        wrapped.add(id(inner))
            # a dict KEY (literal or comprehension), a set member, or a subscript target
            for holder in (getattr(node, "keys", None) or []):
                if holder is not None:
                    identity.update(id(c) for c in ast.walk(holder) if isinstance(c, (ast.Call, ast.Name)))
            if isinstance(node, (ast.Set, ast.SetComp)):
                parts = node.elts if isinstance(node, ast.Set) else [node.elt]
                for part in parts:
                    identity.update(id(c) for c in ast.walk(part) if isinstance(c, (ast.Call, ast.Name)))
            if isinstance(node, (ast.DictComp,)):
                identity.update(id(c) for c in ast.walk(node.key) if isinstance(c, (ast.Call, ast.Name)))
            if isinstance(node, ast.Subscript):
                identity.update(id(c) for c in ast.walk(node.slice) if isinstance(c, (ast.Call, ast.Name)))
            # a comparison / an assertion — the moment a name is matched against a declaration
            if isinstance(node, ast.Compare):
                for part in [node.left, *node.comparators]:
                    identity.update(id(c) for c in ast.walk(part) if isinstance(c, (ast.Call, ast.Name)))
            if isinstance(node, ast.Call) and _callee(node)[1] in _ASSERTIONS:
                for part in node.args:
                    identity.update(id(c) for c in ast.walk(part) if isinstance(c, (ast.Call, ast.Name)))
        # A NAME used as an identity promotes every relpath bound to it.
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in bound and id(node) in identity:
                identity.update(id(c) for c in bound[node.id])
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _callee(node)[1] == "relpath" \
                    and id(node) not in wrapped and id(node) in identity:
                found.append((name, node.lineno, ast.unparse(node)[:90]))
    return tuple(found)


# ------------------------------------------ CAUSE B.2 — a `/` literal welded onto an OS-built path
def welded_slash_sites(sources):
    """Every place a `/` is concatenated onto a value that holds a filesystem path.

    Three spellings, all the same defect: `f"{root}/src"`, `'%s/src' % root`, and `root + "/src"`.
    On Windows the result is `D:\\a\\repo/src` — a path with both separators, which is what the
    release-check refusal actually printed to users at the 0.0.18 cut. Build it with the path API
    and the platform spells it once, correctly."""
    found = []
    for name, source in sorted(sources.items()):
        tree = _parse(source)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr):
                values = node.values
                for i, value in enumerate(values[:-1]):
                    after = values[i + 1]
                    if isinstance(value, ast.FormattedValue) \
                            and isinstance(after, ast.Constant) \
                            and isinstance(after.value, str) and after.value.startswith("/") \
                            and _is_pathy(value.value):
                        found.append((name, node.lineno, "f-string", ast.unparse(node)[:90]))
                        break
            elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod) \
                    and isinstance(node.left, ast.Constant) \
                    and isinstance(node.left.value, str) \
                    and re.search(r"%s/[A-Za-z._-]", node.left.value):
                operands = (node.right.elts if isinstance(node.right, (ast.Tuple, ast.List))
                            else [node.right])
                if any(_is_pathy(x) for x in operands):
                    found.append((name, node.lineno, "percent", ast.unparse(node)[:90]))
            elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add) \
                    and isinstance(node.right, ast.Constant) \
                    and isinstance(node.right.value, str) \
                    and node.right.value.startswith("/") and _is_pathy(node.left):
                found.append((name, node.lineno, "concat", ast.unparse(node)[:90]))
    return tuple(found)


def _is_pathy(node):
    if isinstance(node, ast.Name):
        return bool(_PATHY_NAME.search(node.id))
    if isinstance(node, ast.Attribute):
        return bool(_PATHY_NAME.search(node.attr))
    if isinstance(node, ast.Call):
        return _callee(node)[1] in _PATHY_CALL
    return False


# ------------------------------------------------- CAUSE B.3 — one side converted, the other not
def one_sided_posix_sites(sources):
    """An `os.path.join(...)` inside an ASSERTION, in a module that elsewhere calls `as_posix`.

    ⭐ THE HALF-FIX DETECTOR, and it is the one that catches what the 0.0.18 repair itself left
    behind. `tests/test_release_asset_set.py` was converted to pass `as_posix(directory)` INTO
    the shell script — so the script's output came back `/`-spelled — while the expectation it is
    compared against stayed `os.path.join(self.dist, name)`, i.e. `\\`-spelled. The module knows
    about the boundary; it converted one side of the comparison and not the other, and two of the
    ten Windows failures on run 32094654167 are exactly that.

    Scoped to modules that use a posix wrapper ON PURPOSE: a module with no `as_posix` anywhere
    has not made a claim about spelling, and convicting its every `os.path.join` would bury the
    real finding under hundreds of sites nobody can act on."""
    found = []
    for name, source in sorted(sources.items()):
        tree = _parse(source)
        if tree is None:
            continue
        if not any(isinstance(n, ast.Call) and _callee(n)[1] in _POSIX_WRAPPERS
                   for n in ast.walk(tree)):
            continue                            # module makes no spelling claim
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and _callee(node)[1] in _ASSERTIONS):
                continue
            consumed = set()
            for inner in ast.walk(node):
                # a join CONSUMED as a path (opened, stat'd, listed) is a path — its separator is
                # correct as the OS spells it — and a join already WRAPPED has answered the
                # question this detector asks.
                if isinstance(inner, ast.Call) and (_callee(inner)[1] in _PATH_CONSUMERS
                                                    or _callee(inner)[1] in _POSIX_WRAPPERS):
                    consumed.update(id(c) for c in ast.walk(inner) if isinstance(c, ast.Call))
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call) and _callee(inner)[1] == "join" \
                        and isinstance(inner.func, ast.Attribute) \
                        and isinstance(inner.func.value, ast.Attribute) \
                        and inner.func.value.attr == "path" \
                        and id(inner) not in consumed:
                    found.append((name, node.lineno, ast.unparse(inner)[:90]))
                    break
    return tuple(found)



# ------------------------------- CAUSE B.4 — a NATIVE-BUILT EXPECTATION vs a POSIX-SPELLED name
#: The `join` arguments that ANCHOR a value to a real place on this disk. A join whose first
#: operand is one of these is a filesystem path and its separator is the OS's business; a join
#: whose first operand is a bare relative string literal is anchored to NOTHING, so it can only
#: ever be a repo-relative NAME waiting to be matched against something.
_NON_ANCHORS = frozenset({"", ".", ".."})


def _is_name_builder(node):
    """Is this `os.path.join(...)` building a NAME rather than a path?

    The test is the FIRST operand and nothing else. `os.path.join(d, "pkg", "mod.py")` is rooted
    in a tempdir — a real location, so a real path. `os.path.join("pkg", "mod.py")` is rooted in
    nothing at all: it cannot be opened, stat'd or walked without something else supplying the
    anchor, so the only thing it can be is a name."""
    if not (isinstance(node, ast.Call) and _callee(node)[1] == "join"
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "path" and node.args):
        return False
    first = node.args[0]
    return (isinstance(first, ast.Constant) and isinstance(first.value, str)
            and not os.path.isabs(first.value) and first.value not in _NON_ANCHORS)


def native_name_expectation_sites(sources):
    """Every hand-declared repo-relative NAME built with `os.path.join`, used as an IDENTITY.

    ⭐ THE DETECTOR FOR WHAT THE ROUND-2 SWEEP ITSELF LEFT BEHIND, and the reason B.3 could not
    find it. B.3 asks *"does THIS MODULE convert one side and not the other?"* — and on the
    fourteen Windows failures of run 32117189879 the answer was no, because **the side that moved
    was in a different module**. The sweep converted the PRODUCERS (`repo_walk.posix_name`,
    `knowledge/index.py`, `_support.tree_snapshot`) so their output is `/`-spelled on every
    platform; the CONSUMERS went on building their expectations with `os.path.join`, which is
    `\\`-spelled on exactly one. Half of each pair moved, and a module-scoped detector is
    structurally blind to a pair whose halves live in different files.

    So this detector does not ask what the module habitually does. It asks what the VALUE is:

      * `os.path.join(d, "pkg", "mod.py")`      — anchored to a tempdir. A PATH. Acquitted.
      * `os.path.join("pkg", "mod.py")`         — anchored to nothing. A NAME. Convicted, if it
                                                   is then matched against something.

    ⚠ SCOPED TO IDENTITY USE, for B.1's reason and against B.1's precedent: a literal-anchored
    join handed to `os.makedirs` under a `cwd` is a path being built, and a `\\` there is correct.
    It is only a defect at the moment it MEETS a value someone else produced. Convicting all 53
    literal-anchored joins in the corpus rather than the ones that are matched would answer the
    guard with exemptions, which is how a guard gets switched off (see B.1's own narrowing).

    Bindings are followed for the same reason B.1 follows them, and here it is load-bearing
    rather than a refinement: `test_h6_mint_site`'s `_LEDGER` and `test_h1a_s2_s3_injection_pack`'s
    `_LEDGER_REL` are both declared once at class/module level and matched somewhere else
    entirely — those two are the whole of Gate B, and a purely syntactic check walks past both."""
    found = []
    for name, source in sorted(sources.items()):
        tree = _parse(source)
        if tree is None:
            continue
        builders = [n for n in ast.walk(tree) if _is_name_builder(n)]
        if not builders:
            continue
        # `_LEDGER = os.path.join(...)` — the declaration site, so a match on `self._LEDGER`
        # somewhere else promotes it. Keyed by BOTH spellings a later reference can take.
        #
        # ⚠ THE BINDING MUST BE THE VALUE ITSELF, not a builder buried anywhere inside it.
        # `text = self._text(os.path.join("execmode", "orchestrator.py"))` binds `text` to the
        # FILE'S CONTENTS; a later `assertIn(..., text)` says nothing about the join's spelling,
        # and following that binding convicted a site where the join is a path being READ. B.1
        # walks `ast.walk(node.value)` and can afford to — a `relpath` anywhere in an expression
        # is still a relpath — but a `join` is only the bound value when it IS the value.
        bound = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                    and isinstance(node.targets[0], ast.Name):
                candidates = [node.value]
                while candidates:
                    value = candidates.pop()
                    if _is_name_builder(value):
                        bound.setdefault(node.targets[0].id, []).append(value)
                    elif isinstance(value, ast.BinOp):      # `join(...) + os.sep` — still the value
                        candidates.extend([value.left, value.right])
        identity, consumed = set(), set()
        for node in ast.walk(tree):
            # already answered: wrapped in a posix conversion, or consumed as a real path
            if isinstance(node, ast.Call) and (_callee(node)[1] in _POSIX_WRAPPERS
                                               or _callee(node)[1] in _PATH_CONSUMERS):
                consumed.update(id(c) for c in ast.walk(node) if isinstance(c, ast.Call))
            # the same identity surfaces B.1 names: keys, set members, subscripts, comparisons,
            # assertions. A name is only a name once something matches on it.
            for holder in (getattr(node, "keys", None) or []):
                if holder is not None:
                    identity.update(id(c) for c in ast.walk(holder)
                                    if isinstance(c, (ast.Call, ast.Name, ast.Attribute)))
            if isinstance(node, (ast.Set, ast.SetComp)):
                parts = node.elts if isinstance(node, ast.Set) else [node.elt]
                for part in parts:
                    identity.update(id(c) for c in ast.walk(part)
                                    if isinstance(c, (ast.Call, ast.Name, ast.Attribute)))
            if isinstance(node, ast.DictComp):
                identity.update(id(c) for c in ast.walk(node.key)
                                if isinstance(c, (ast.Call, ast.Name, ast.Attribute)))
            if isinstance(node, ast.Subscript):
                identity.update(id(c) for c in ast.walk(node.slice)
                                if isinstance(c, (ast.Call, ast.Name, ast.Attribute)))
            if isinstance(node, ast.Compare):
                for part in [node.left, *node.comparators]:
                    identity.update(id(c) for c in ast.walk(part)
                                    if isinstance(c, (ast.Call, ast.Name, ast.Attribute)))
            if isinstance(node, ast.Call) and (_callee(node)[1] in _ASSERTIONS
                                               or _callee(node)[1] in _MATCHERS):
                for part in node.args:
                    identity.update(id(c) for c in ast.walk(part)
                                    if isinstance(c, (ast.Call, ast.Name, ast.Attribute)))
        # a NAME or an ATTRIBUTE used as an identity promotes every builder bound to it
        for node in ast.walk(tree):
            if id(node) not in identity:
                continue
            key = node.id if isinstance(node, ast.Name) else getattr(node, "attr", None)
            for builder in bound.get(key, ()):
                identity.add(id(builder))
        for builder in builders:
            if id(builder) in identity and id(builder) not in consumed:
                found.append((name, builder.lineno, ast.unparse(builder)[:90]))
    return tuple(sorted(found))



# ------------------------- CAUSE B.5 — an `os.sep` COMPARISON against a value that was posixed
def native_sep_comparison_sites(sources):
    r"""`os.sep` used to MATCH, in a module that also converts paths to `/`-spelled names.

    ⭐ THE SHAPE THAT OPENED A SECURITY BOUNDARY, and B.4 cannot see it because there is no
    `os.path.join` anywhere near it. `govern/secret_ignore.normalize_target` refused a traversal
    with `rel.startswith(os.pardir + os.sep)` and was CORRECT for as long as `rel` came straight
    out of `os.path.relpath`. The round-2 sweep wrapped that producer in `posix_name` and left
    the comparison spelled with `os.sep`; on Windows `rel` became `../outside.py`, the check
    tested it against `..\`, and `add_ignore(root, token, "../outside.py")` was ACCEPTED on all
    three Windows legs of run 32117189879. Half of the pair moved — the same defect as B.4, in a
    place B.4 does not look, and this time in a path-traversal defence.

    It was caught because a test happened to assert the refusal. That is luck, and luck is not a
    control; this is the control.

    Scoped the way B.3 is scoped — to modules that posix-wrap ON PURPOSE — because comparing two
    genuine filesystem paths with `os.sep` is not merely allowed, it is right: `version.py`,
    `selfprotect.py`, `packaging.py` and `mcp/validation.py` all do it to a `realpath`, and
    convicting them would answer the guard with four exemptions arguing that paths are paths.

    ⚠ AND AN EXPRESSION THAT NAMES BOTH SEPARATORS IS ACQUITTED WITHOUT A ROW. `raw.endswith(("/",
    os.sep))` and `"/" in candidate or os.sep in candidate` have already answered this question —
    they are the FIX for this defect, not the defect — so a detector that convicted them would be
    convicting its own remedy. That is a derived clearing, not a declared one."""
    found = []
    for name, source in sorted(sources.items()):
        tree = _parse(source)
        if tree is None:
            continue
        if not any(isinstance(n, ast.Call) and _callee(n)[1] in _POSIX_WRAPPERS
                   for n in ast.walk(tree)):
            continue                            # module makes no spelling claim
        for node in ast.walk(tree):
            matching = (isinstance(node, ast.Compare)
                        or (isinstance(node, ast.Call)
                            and _callee(node)[1] in _MATCHERS | _ASSERTIONS))
            if not matching:
                continue
            mentions_sep = any(isinstance(c, ast.Attribute) and c.attr in ("sep", "altsep")
                               and isinstance(c.value, ast.Name) and c.value.id == "os"
                               for c in ast.walk(node))
            if not mentions_sep:
                continue
            # the remedy acquits itself: an expression that also names "/" is separator-agnostic
            says_posix = any(isinstance(c, ast.Constant) and isinstance(c.value, str)
                             and "/" in c.value for c in ast.walk(node))
            if not says_posix:
                found.append((name, node.lineno, ast.unparse(node)[:90]))
    return tuple(sorted(set(found)))


# =================================================================================================
# EXEMPTIONS — a ROW WITH A REASON, never a silence
# =================================================================================================
# B4's rule: "no exemption without a named reason each". Each row below is a site the detectors
# convict and a human has cleared, with the argument written down so the next reader can disagree
# with it. `test_windows_shell_and_paths.py` pins that every row still RESOLVES to a real site —
# a stale exemption (the site moved, or was fixed) reds, so this table cannot quietly outlive the
# thing it excuses. That is the control that makes an exemption table safe; the reason text is
# what makes it reviewable.
#
# ⚠ NOT here, and deliberately: the 33 bare-name argv[0] sites (`git`, `gh`, `grep`). Those are
# cleared as a DERIVED CLASS by the `shadowed` flag — no System32 executable carries those names,
# so PATH order is the only resolution and PATH order is what the caller wants. Writing 33 rows
# would turn a fact a reader can check against Windows into 33 assertions they cannot.

EXEMPT = {
    ("one_sided_posix", "tests/test_db_s7a_edge_substrate.py"):
        "both sides of the comparison are built with os.path.join — a self-consistent OS-spelled "
        "comparison, not a mixed one. The defect is a `/` literal meeting a `\\` path; two `\\` "
        "paths meet correctly on every platform.",
    ("one_sided_posix", "tests/test_h6_anchor_fingerprints.py"):
        "`AF.record_path()` returns a real filesystem path and the join is the PREFIX it is "
        "startswith-tested against — both OS-spelled, and prefixing a posix name onto a native "
        "path is what would break it.",
    ("one_sided_posix", "tests/test_r_man_atomic_manifest.py"):
        "the join is the argument to `_read()`, a local file-opener — a path CONSUMER the "
        "detector cannot recognise because it is module-local. A path being opened is a path.",
    ("welded_slash", "tests/test_mutant_driver_contract.py"):
        "`MDC.INTERNAL_ROOT + \"/\"` welds a separator onto a DECLARED repo-relative name, not "
        "onto an OS path — both operands are `/`-spelled declarations and the comparison is "
        "between two names. Using os.path.join here would introduce the defect, not remove it.",
    # ------ B.4: the counterparty is a REAL FILESYSTEM PATH, so the native separator is correct.
    # Each row below was cleared by opening the PRODUCER and reading what it puts in the value the
    # join is matched against. That is the one question B.4 cannot answer from the AST — the
    # producer is in another module — so it reports and a human resolves (doc 85 §7g). The wrong
    # move here would be to "fix" these: spelling a POSIX name into a suffix-match against a real
    # Windows path is how you turn a passing test into a Windows-only failure, which is the exact
    # defect this detector exists to find, arriving from the other direction.
    ("native_name_expectation", "tests/test_agent_skills.py"):
        "`res.touched` is filled by `harness_setup` as `str(f)` over `pathlib.Path` objects — "
        "real OS paths, natively spelled. The join is a suffix of a path, not a name.",
    ("native_name_expectation", "tests/test_harness_codex.py"):
        "`t.commands_dir` is a `Path`; `str(...).endswith(join)` matches the tail of a real "
        "filesystem path. A `/`-spelled tail would never match it on Windows.",
    ("native_name_expectation", "tests/test_kb_s1_ledger_setup.py"):
        "`rec['files']` is `harness_setup.py`'s `sorted(str(f) for f in files)` — the same "
        "`str(Path)` producer as `test_agent_skills`, and the same answer.",
    ("native_name_expectation", "tests/test_si_2_tdd_persistence.py"):
        "`opened[0]` is a path recorded as it was handed to `open()` — a real filesystem path, "
        "so the suffix it is matched against must be OS-spelled too.",
    ("native_name_expectation", "tests/test_stage13_vault_slice.py"):
        "`deprecation.removed_file_path` composes a location on the user's disk and the CLI "
        "prints it as `Yours is still at {path}` — somewhere a human is being told to go and "
        "look. Both sides are OS-spelled and both are right to be.",
    ("native_name_expectation", "tests/test_stage35b_memory_share.py"):
        "`res2['dest']` is the real backup file just written (the next line `os.path.exists`es "
        "it). A path that exists is a path.",
    ("native_name_expectation", "tests/test_stage66_cross_platform.py"):
        "the NATIVE separator is this module's declared subject — `test_bundle_dir_uses_os_join` "
        "exists to assert that `bundle_dir` builds an OS path and stays `normpath`-stable. "
        "Converting these would delete the test's reason to exist.",
    ("native_name_expectation", "tests/test_tag_is_not_a_publish.py"):
        "`prov.render()` interpolates `self.root` and the package file it actually resolved — "
        "both real OS paths — into its sentence, so `src\\mokata` is what a Windows reader sees "
        "and what the assertion must look for.",
    ("native_sep_comparison", "tests/test_h6_anchor_fingerprints.py"):
        "`dirpath` comes straight out of `os.walk` and `skip` is an `os.path.join`ed real "
        "directory — two genuine filesystem paths, so `os.sep` is the correct separator between "
        "them and a `/` would match nothing on Windows. The module posix-wraps ELSEWHERE (for "
        "the anchor NAMES it compares against declarations), which is the scope this detector "
        "uses and the reason it looked here at all; paths and names sit side by side in it.",
    ("shell_stdin", "tests/hook_execution_check.py"):
        "reported `**kwargs (undecidable)`: the AST cannot see inside the spread. Read at the "
        "call site — `kwargs = dict(input=payload, ...)` — so stdin IS stated, as a pipe carrying "
        "the hook payload. Cleared by reading, which is why the detector reports rather than "
        "acquits an unreadable answer (doc 85 §7g).",
}


def exempt_reason(detector, path):
    """The named reason `path` is cleared for `detector`, or None."""
    return EXEMPT.get((detector, path))


def apply_exemptions(detector, sites):
    """`(kept, excused)` — sites the detector found, split by the exemption table."""
    kept, excused = [], []
    for site in sites:
        (excused if exempt_reason(detector, site[0]) else kept).append(site)
    return tuple(kept), tuple(excused)


# ------------------------------------------ CAUSE A, AS A CLASS — argv[0] resolved through hops
def _argv_literals(tree):
    """`{node_id_of_expression -> [list/tuple literal, ...]}` for every expression that can reach a
    spawn as its argv: an inline literal, a name bound to one, or a call to a module-local
    function that returns them."""
    bound, returns, from_call = {}, {}, {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for inner in ast.walk(node):
                if isinstance(inner, ast.Return) and isinstance(inner.value, (ast.List, ast.Tuple)):
                    returns.setdefault(node.name, []).append(inner.value)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            continue
        target = node.targets[0].id
        if isinstance(node.value, (ast.List, ast.Tuple)):
            bound.setdefault(target, []).append(node.value)
        elif isinstance(node.value, ast.Call):
            # `argv = _notification_argv(platform, body)` — the literal lives one function away.
            # THIS is the hop `notify.py` needs, and the hop whose absence made round 2's first
            # answer to 2a ({git, gh, grep}) an artefact of the detector rather than a fact.
            from_call.setdefault(target, []).append(_callee(node.value)[1])
    for target, fnames in from_call.items():
        for fname in fnames:
            bound.setdefault(target, []).extend(returns.get(fname, []))
    return bound, returns


def shadowed_name_argv_sites(sources):
    """Every `subprocess` spawn whose argv[0] is a System32-shadowed program name — through an
    inline literal, a bound variable, OR a helper's return value.

    ⭐ WHY THE HOPS, and it is a gap round 2's own derivation actually fell into. Reporting only
    inline argv gave the tree's bare-name set as `{git, gh, grep}` — and that was WRONG.
    `src/mokata/notify.py` builds its argv in helpers and returns them:

        return ["notify-send", TITLE, body]      # the spawn takes a VARIABLE; never seen inline
        return ["osascript", "-e", ...]
        return ["afplay", _MACOS_SOUND]

    Five more bare names. None is shadowed, so the verdict did not change — but the METHOD was
    unsound, and a shadowed name written that way would have walked straight past.

    ⚠ AND WHY NOT PURE SYNTAX ("any literal starting with a shadowed name"), which was tried
    first and discarded: it convicted 30 sites, essentially all data — `roles=("route", "handler",
    "endpoint")`, `return ("timeout", None, msg)`, `_run(["mode", "--path", d])` (mokata's OWN CLI
    subcommand), and `("bash", "rsync", "git")`, the PRESENCE check the cause-A sweep had already
    learned to acquit. A shadowed name is only a hazard in **argv[0] of a spawn**, so that is
    exactly what this asks."""
    found = []
    for name, source in sorted(sources.items()):
        tree = _parse(source)
        if tree is None:
            continue
        bound, returns = _argv_literals(tree)
        for node in ast.walk(tree):
            if not _is_spawn(node) or not node.args:
                continue
            a = node.args[0]
            candidates = []
            if isinstance(a, (ast.List, ast.Tuple)):
                candidates = [a]
            elif isinstance(a, ast.Name):
                candidates = bound.get(a.id, [])
            elif isinstance(a, ast.Call):
                candidates = returns.get(_callee(a)[1], [])
            for lit in candidates:
                if not lit.elts:
                    continue
                first = lit.elts[0]
                if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                    continue
                program = first.value
                if "/" in program or "\\" in program or os.path.isabs(program):
                    continue
                if is_shadowed(program):
                    found.append((name, node.lineno, program))
    return tuple(found)


def all_spawned_bare_names(sources):
    """`{program: site_count}` for every bare argv[0] reachable at a spawn through any hop — the
    FULL published set for a review, not the subset one spelling happens to reach."""
    counts = {}
    for name, source in sorted(sources.items()):
        tree = _parse(source)
        if tree is None:
            continue
        bound, returns = _argv_literals(tree)
        for node in ast.walk(tree):
            if not _is_spawn(node) or not node.args:
                continue
            a = node.args[0]
            if isinstance(a, (ast.List, ast.Tuple)):
                candidates = [a]
            elif isinstance(a, ast.Name):
                candidates = bound.get(a.id, [])
            elif isinstance(a, ast.Call):
                candidates = returns.get(_callee(a)[1], [])
            else:
                candidates = []
            for lit in candidates:
                if not lit.elts:
                    continue
                first = lit.elts[0]
                if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                    continue
                program = first.value
                if "/" in program or "\\" in program or os.path.isabs(program):
                    continue
                counts[program] = counts.get(program, 0) + 1
    return counts


# --------------- CAUSE A, TIER 2 — belt and braces for the hop dataflow provably cannot follow
#
# ⚠ THE LIMIT THIS EXISTS FOR, stated because it was found the embarrassing way. The dataflow
# tier above follows inline argv, a bound name, and a helper's return value — and it STILL cannot
# see `notify.py`'s audio arm, because that argv reaches the spawn through an INJECTED `runner=`
# seam (`fired = runner(sound, …)`, default `_run_argv`, which spawns). No static pass follows an
# injected callable in general. `afplay`, `canberra-gtk-play` and `paplay` are invisible to it.
#
# (Worse, and worth admitting: the tier above DOES report `notify-send`/`osascript`, but only
# because `_run_argv`'s parameter happens to be spelled `argv` too — the same name the caller
# binds. It is right there by coincidence, not by analysis.)
#
# So this tier asks a question that needs no dataflow: is a shadowed name sitting at the head of
# any argv-shaped literal, anywhere? Restricted to names that are NOT ordinary English or data
# words, because the unrestricted version convicted `("route", "handler", "endpoint")`,
# `("timeout", None, msg)` and `_run(["mode", "--path", d])` — thirty sites, all data.
_AMBIGUOUS_WITH_ORDINARY_DATA = frozenset({
    "mode", "route", "path", "set", "echo", "type", "help", "start", "timeout", "replace",
    "print", "convert", "label", "recover", "comp", "tree", "net", "reg", "find", "sort",
    "more", "where", "fc", "expand", "compact", "attrib", "ping", "hostname",
})

#: Shadowed names nobody writes at the head of a list except to run them.
SYSTEM32_UNAMBIGUOUS = SYSTEM32_SHADOWED - _AMBIGUOUS_WITH_ORDINARY_DATA


def shadowed_name_anywhere_sites(sources):
    """Any argv-shaped literal headed by an UNAMBIGUOUS shadowed name, wherever it is written.

    Acquits the one shape that legitimately puts program names in a literal: a PRESENCE check
    (`[t for t in ("bash", "rsync", "git") if shutil.which(t) is None]`). A literal being ITERATED
    is a set of names being asked about, not an argv being run — and acquitting it is a
    correction the cause-A sweep already had to make once."""
    found = []
    for name, source in sorted(sources.items()):
        tree = _parse(source)
        if tree is None:
            continue
        iterated = set()
        for node in ast.walk(tree):
            for comp in getattr(node, "generators", []) or []:
                iterated.add(id(comp.iter))
            if isinstance(node, ast.For):
                iterated.add(id(node.iter))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.List, ast.Tuple)) or len(node.elts) < 2:
                continue
            if id(node) in iterated:
                continue                      # a presence check, not an argv
            first = node.elts[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str) \
                    and first.value in SYSTEM32_UNAMBIGUOUS:
                found.append((name, node.lineno, first.value))
    return tuple(found)
