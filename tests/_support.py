"""Shared test support: put `src/` on the import path and build sample manifests."""

import os
import shutil
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# --- STAGE 05: THE INTERPRETER REFUSES BEFORE IT COUNTS ----------------------------------------
# `NO-COMMAND-PROVISIONS-THE-INTERPRETER-THE-SUITE-NEEDS`. A full run on an unprovisioned
# interpreter returned 30 failures and 116 errors on a CORRECT tree, and the documented way to run
# the suite was that way. ⛔ An unprovisioned run must not be able to report a NUMBER at all — a
# skip reports `OK (skipped=146)`, which is a green with a footnote, and that is not the same fact
# (§7g). ⛔ `_provisioning` calls `os._exit` — `SystemExit` was tried first and `unittest`'s loader
# swallowed it into a `_FailedTest`, printing `Ran 1 test … FAILED (errors=1)`, i.e. a number, per
# module. See that module's docstring; the measurement is recorded there.
#
# ⚠ HERE AND NOT IN A `tests/__init__.py`, WHICH THE FIX SHAPE NAMED. Discovery runs with
# `-t tests`, so `tests/` is the top-level directory and not a package — a `tests/__init__.py`
# would not be imported at all, and the refusal would simply never fire. ⭐ EVERY test module in
# this tree imports `_support` to reach `src/`, which makes this the one import that is actually
# on every path. `test_a31_the_interpreter_and_the_path` grades that claim rather than trusting it.
#
# 🔴 `THE-PRECUT-PATH-MASKED-THE-RELEASE-PATH` (0.0.21 cut, 2026-10-05). `_provisioning` is this
# file's SIBLING, and the bare import below found it only because `tests/` happened to be on
# `sys.path` — true for the unit suite (`-t tests`) and for the precut (`PYTHONPATH=tests`), FALSE
# for `release.sh`'s integration leg (`discover -s tests/integration -t tests/integration`), which
# loads this file by PATH through `tests/integration/_support.py`. All 27 integration modules died
# at import on the release machine and on no other run. ⭐ Resolve the sibling relative to THIS
# FILE, not to whoever loaded it. APPENDED, not inserted at 0: `tests/` must never outrank the
# integration package's own same-named modules (`_support` above all) in that suite's lookup.
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TESTS_DIR not in sys.path:
    sys.path.append(_TESTS_DIR)
import _provisioning  # noqa: E402

_provisioning.refuse_unless_provisioned()


# --- 0.0.26 O15 `CRG-RESOLUTION-HERMETIC`: THE SUITE NEVER BORROWS THE DEVELOPER'S TOOLS ---------
# Measured on Jas's Mac at checkpoint 2: a core venv (`.[dev,ide]`, no code-review-graph) ran the
# unit suite at a real terminal while a `uv tool` mokata[full] had put a code-review-graph on PATH.
# Tests that meant "no external graph" got the uv tool's copy, printed `code-graph: DEGRADED`, a
# test FAILED, and the run sat silent. The product fix is `mokata.crg_resolve` (mokata's own env
# only); this is the suite's half, installed by the one import every module makes:
#
#   1. code-review-graph is OFF unless a test asks for it: `MOKATA_CRG=off` (inherited by every
#      subprocess a test starts), so a core venv and a full venv mean the SAME thing — the "5+
#      tests that wrongly assume CRG absent" in a full venv were this. A test that wants a graph
#      NAMES it: `MOKATA_CRG=<fake>`, `_crg_present` (stubbed at the wire), or `real_crg(tc)` (the
#      env's real one). `MOKATA_TEST_REAL_CRG=1` keeps the env's real one for a whole run (the
#      real-crg CI leg). And PATH is SCRUBBED of any `code-review-graph` that is not this
#      interpreter's own — the dir is replaced by a mirror of its other executables (dropped where
#      symlinks are refused, e.g. unprivileged Windows), so `git`, `rg`, `node` beside it resolve.
#   2. Every in-process spawn of a code-review-graph is CHECKED: it must be this environment's, a
#      fake under the temp dir, or the one the test named in `MOKATA_CRG`. Anything else raises
#      `HermeticViolation` — at the spawn, in the test that did it (`test_o15_*` sweeps the tree).
#   3. A WATCHDOG: any single test running past `MOKATA_TEST_WATCHDOG_S` (default 900 s) has every
#      thread's stack dumped to the real stderr (repeating), so a hang is never silent again.
import faulthandler as _faulthandler
import subprocess as _subprocess
import sysconfig as _sysconfig
import tempfile as _tempfile

HERMETIC_COMMANDS = ("code-review-graph",)


def _own_scripts_dirs():
    dirs = []
    for d in (_sysconfig.get_path("scripts"), os.path.dirname(sys.executable),
              os.path.join(sys.prefix, "Scripts" if os.name == "nt" else "bin")):
        if d and os.path.normcase(os.path.realpath(d)) not in dirs:
            dirs.append(os.path.normcase(os.path.realpath(d)))
    return dirs


def _mirror_without(d, names):
    """A temp dir of symlinks to every entry of `d` except `names`; None when symlinks fail."""
    mirror = _tempfile.mkdtemp(prefix="mokata-hermetic-path-")
    import atexit
    atexit.register(shutil.rmtree, mirror, True)
    try:
        for entry in os.listdir(d):
            stem = os.path.splitext(entry)[0] if os.name == "nt" else entry
            if stem in names:
                continue
            os.symlink(os.path.join(d, entry), os.path.join(mirror, entry))
    except OSError:
        shutil.rmtree(mirror, ignore_errors=True)
        return None
    return mirror


def hermetic_path(path, own_dirs=None, names=HERMETIC_COMMANDS):
    """`path` with every dir that holds one of `names` OUTSIDE `own_dirs` replaced by a mirror
    without it (or dropped). Returns (new_path, [scrubbed dirs])."""
    own = own_dirs if own_dirs is not None else _own_scripts_dirs()
    out, scrubbed = [], []
    for d in (path or "").split(os.pathsep):
        if not d:
            continue
        hit = any(shutil.which(n, path=d) for n in names)
        if hit and os.path.normcase(os.path.realpath(d)) not in own:
            scrubbed.append(d)
            mirror = _mirror_without(d, names)
            if mirror:
                out.append(mirror)
            continue
        out.append(d)
    return os.pathsep.join(out), scrubbed


#: What the scrub removed at import (empty on a clean machine) — `test_o15_*` reads it.
os.environ["PATH"], HERMETIC_SCRUBBED = hermetic_path(os.environ.get("PATH", ""))
if os.environ.get("MOKATA_TEST_REAL_CRG") == "1":
    os.environ.pop("MOKATA_CRG", None)
else:
    os.environ["MOKATA_CRG"] = "off"


def real_crg(testcase):
    """Opt THIS test into the environment's real code-review-graph resolution (MOKATA_CRG unset
    for its duration)."""
    from unittest import mock
    p = mock.patch.dict(os.environ)
    p.start()
    testcase.addCleanup(p.stop)
    os.environ.pop("MOKATA_CRG", None)


class HermeticViolation(AssertionError):
    """A test spawned a tool from outside its own environment without naming it."""


def _allowed_crg(exe):
    real = os.path.normcase(os.path.realpath(exe))
    if os.path.dirname(real) in _own_scripts_dirs():
        return True

    def _under(path, base):
        try:
            return os.path.commonpath([path, base]) == base
        except ValueError:                           # different drives (Windows)
            return False
    tmp = _tempfile.gettempdir()
    if _under(real, os.path.normcase(os.path.realpath(tmp))) or _under(
            os.path.normcase(os.path.abspath(exe)), os.path.normcase(os.path.abspath(tmp))):
        return True                                  # a fake the test built
    named = os.environ.get("MOKATA_CRG")
    return bool(named) and named.lower() not in ("off", "none", "0") and os.path.normcase(os.path.realpath(named)) == real


_REAL_POPEN_INIT = _subprocess.Popen.__init__


def _guarded_popen_init(self, args, *a, **kw):
    argv0 = args if isinstance(args, (str, bytes, os.PathLike)) else (args[0] if args else "")
    argv0 = os.fsdecode(argv0) if isinstance(argv0, (bytes, os.PathLike)) else argv0
    if not kw.get("shell") and isinstance(argv0, str):
        stem = os.path.basename(argv0)
        if os.name == "nt":
            stem = os.path.splitext(stem)[0]
        if stem in HERMETIC_COMMANDS:
            env = kw.get("env")
            exe = argv0 if os.path.dirname(argv0) else shutil.which(
                argv0, path=(env or os.environ).get("PATH"))
            if exe and not _allowed_crg(exe):
                raise HermeticViolation(
                    f"a test spawned {exe} — a {stem} from OUTSIDE this test environment that the "
                    f"test did not name. Name it (MOKATA_CRG / a temp-dir fake / `_crg_present`) "
                    f"or hide it (`hide_external_graphs`).")
    return _REAL_POPEN_INIT(self, args, *a, **kw)


_subprocess.Popen.__init__ = _guarded_popen_init

WATCHDOG_S = float(os.environ.get("MOKATA_TEST_WATCHDOG_S", "900") or 0)


def _install_watchdog():
    import unittest
    if getattr(unittest.TestCase.run, "_mokata_watchdog", False) or WATCHDOG_S <= 0:
        return
    real_run = unittest.TestCase.run

    def run(self, result=None):
        stream = sys.__stderr__
        armed = stream is not None
        if armed:
            try:
                stream.write("")  # a closed stream cannot carry the dump
                _faulthandler.dump_traceback_later(WATCHDOG_S, repeat=True, file=stream)
            except (ValueError, OSError, AttributeError):
                armed = False
        try:
            return real_run(self, result)
        finally:
            if armed:
                _faulthandler.cancel_dump_traceback_later()
    run._mokata_watchdog = True
    unittest.TestCase.run = run


_install_watchdog()


# --- WINDOWS PORTABILITY: THE TWO THINGS A POSIX-ONLY AUTHOR GETS WRONG -------------------
# Both of these were found the expensive way — 19 of the 20 tests that reded on all three Windows
# legs of the 0.0.18 cut were one or the other of them, and NOT nineteen separate defects. They
# live here, together, because the repair has to be one place or the class comes straight back.
#
# ⚠ 1. `bash` IS NOT THE `bash` PATH NAMES. `subprocess.run(["bash", ...])` on Windows reaches
#    CreateProcess with a bare name, and CreateProcess searches **System32 BEFORE PATH**. On a
#    GitHub `windows-latest` runner `C:\Windows\System32\bash.exe` is the **WSL launcher**, so a
#    bare "bash" argv runs WSL — which has no distribution installed, prints
#    *"Windows Subsystem for Linux has no installed distributions."* in UTF-16, and exits 1.
#    Every assertion downstream then grades that message instead of the script.
#
#    `shutil.which` searches PATH in PATH's order and finds the Git Bash the runner actually
#    installs, which is why `tests/test_floor_provisioner.py` — the one file that already resolved
#    it this way — ran its script correctly on the same three legs that failed everywhere else.
#    That contrast is the evidence, and `BASH` below makes it the tree's single answer.
#
# ⚠ 2. `os.sep` LEAKS INTO TEXT THAT IS SPELLED WITH `/`. A repo-relative path built by
#    `os.path.join` / `os.path.relpath` is `mokata\deprecation.py` on Windows, and every
#    declaration it is compared against — a corpus key, an exemption table, the command a doc
#    tells a contributor to run — is spelled `mokata/deprecation.py` by a human. The comparison is
#    then false on exactly one platform, which is the platform nobody develops on.
#
#    A repo-relative path used as an IDENTITY is not a filesystem path: it is a name, and the name
#    is POSIX-spelled. `as_posix` / `posix_rel` are where that conversion happens, and
#    `mokata.repo_walk.posix_name` is the product-side twin (deliberately two: a test cannot import
#    product code to run a check ABOUT product code).
#
# ⚠ 3. A SHELL SUBPROCESS MUST SAY WHAT ITS STDIN IS — added in round 2, because fixing (1)
#    CAUSED it. While `bash` resolved to WSL every such call died in milliseconds having read
#    nothing; once it resolved to real Git Bash the subprocess actually ran, and on run
#    32094654167 the `windows · py3.12 · jsonschema=absent` unit step sat at 54m27s against ~25m
#    beside it. Pass `stdin=subprocess.DEVNULL`. `release.sh`'s `run_test_preflight` had the
#    precedent (`< /dev/null`) and the comment explaining it; nothing else in the tree did.
#
# ⚠ THE SWEEP THAT ENFORCES ALL THREE IS `tests/_windows_portability.py`, over a WALKED corpus.
#    The first version of it read `os.listdir(tests/)` and two named corpus builders, and two
#    cause-B sites reached the mirror through the gap. A guard's corpus is a claim.

#: The bash the *shell* would run, or None. Never pass a bare "bash" as argv[0] — see above.
BASH = shutil.which("bash")

#: The reason a bash-driven check did not run. An UN-RUN check, never a passing one (doc 85 §7g).
NO_BASH = ("no bash on PATH — this check drives a shell script and cannot be RUN here. That is an "
           "un-run check, not a passing one (doc 85 §7g).")


def bash_argv(*args):
    """argv for running bash, resolved through PATH rather than left to CreateProcess.

    Raises rather than falling back to a bare "bash": a fallback would silently restore the exact
    defect this exists for, on the one platform where it is invisible to the author."""
    if not BASH:
        raise RuntimeError(NO_BASH)
    return [BASH, *[str(a) for a in args]]


def as_posix(path, *, sep=os.sep):
    """A path spelled the way the repo spells it — `/`, on every platform.

    `sep` is a parameter so the Windows branch is DRIVEN on a POSIX host rather than being a line
    that only ever executes where nobody is looking (§7i)."""
    return path.replace(sep, "/") if sep != "/" else path


def posix_rel(path, start):
    """`os.path.relpath`, spelled as a repo-relative NAME rather than as a filesystem path."""
    return as_posix(os.path.relpath(path, start))


# --- SI.3 (0.0.13): driving a human-gated MCP write from a test --------------------------
# `approve=True` is no longer consent — it is a parameter the MODEL types, and SI.3 demoted it to
# nothing (see `mokata/approval.py`). A durable MCP write now needs an approval a HUMAN minted
# out-of-band with `mokata approve <id>`, in a process the model is not driving.
#
# A test is not a model, so a test IS allowed to play the human — but it must play the human
# HONESTLY: propose, mint the approval through the real `approval.approve` path (the same code the
# CLI runs), then redeem it by id. That is what this helper does, and it is the ONLY way a test
# should commit through an MCP write tool. A test that just passes `approve=True` and expects a
# commit is asserting the bug SI.3 fixed.

def mcp_commit(tool, **kwargs):
    """Drive an MCP write tool through the FULL human round-trip and return the committed result.

    propose -> the human approves out-of-band -> the model redeems the approval by id.

    Any non-proposal outcome (error / noop / conflict / unchanged / unavailable / a secret hard
    block on the propose scan) is returned as-is: there is nothing for a human to approve, and the
    caller's assertions about that outcome still hold."""
    from mokata import approval
    from mokata.govern.ledger import AuditLedger

    # R2 (0.0.26) — every spec carries its task plan. A spec_emit / spec_amend fixture that is
    # about something else gets the smallest honest one (`_plan.planned`); one that sends its own
    # `tasks` is untouched.
    if getattr(tool, "__name__", "") in ("spec_emit", "spec_amend"):
        from _plan import planned
        kwargs = planned(kwargs)
    proposed = tool(**kwargs)
    if not isinstance(proposed, dict):
        return proposed
    pid = proposed.get("proposal_id")
    if not pid:
        return proposed                     # nothing was staged — no approval to mint

    root = kwargs.get("path", ".")
    ledger = AuditLedger.from_mokata_dir(os.path.join(root, ".mokata"))
    approval.approve(root, pid, actor="test-human", ledger=ledger)   # <-- the out-of-band human act
    return tool(**dict(kwargs, proposal_id=pid))


# --- B4 (0.0.12): the sandbox-disk-artifact guard --------------------------------------
# A handful of tests build a REAL file-backed SQLite DB (the memory floor). On a native
# filesystem — CI runners and any normal dev machine — that works and those tests RUN + PASS.
# The mokata build sandbox uses an overlay FS that raises `sqlite3.OperationalError: disk I/O
# error` when SQLite opens the on-disk DB (doc 60 / doc 02 0.0.11 caveat): a broken-disk
# artifact unrelated to the code under test. `sqlite_disk_ok()` detects that case PRECISELY by
# doing the same create-table + write a file-backed SQLiteBackend does, so a guarded test skips
# EXACTLY (and only) where the on-disk artifact genuinely can't exist — never an always-skip,
# never a silent skip on an unrelated error. Probed once per process, then cached.
#
# B4-FU (0.0.12): the probe MUST exercise the SAME on-disk location `SQLiteBackend` writes to.
# The guarded playbook tests build the backend via `run_playbook(make_surface())` with `root="."`,
# so `select_memory_backend` puts the real DB at `<cwd>/.mokata/temp_local/memory/memory.db`.
# The original probe used a SYSTEM temp dir (e.g. /tmp) — but a split-disk sandbox can back /tmp
# SQLite fine while THAT repo-dir location raises `disk I/O error`. Probing /tmp therefore reported
# "disk ok", the 2 tests RAN and ERRORED (and the B4 meta-test FAILed). Probing the repo `.mokata`
# location makes the guard reflect the disk the backend actually uses, so those tests skip cleanly.
_SQLITE_DISK_OK = None

# The OperationalError messages that mean "this filesystem can't back an on-disk SQLite DB"
# (the sandbox artifact) — kept narrow so an UNRELATED OperationalError never triggers a skip.
_DISK_BROKEN_MARKERS = ("disk i/o error", "unable to open database")


def _sqlite_probe_dir():
    """The on-disk directory the probe exercises — the SAME location class `SQLiteBackend` writes
    to when the guarded tests run (`select_memory_backend` with `root="."`): `<cwd>/.mokata/
    temp_local/memory`. Deliberately NOT a system temp dir: a split-disk sandbox can back /tmp
    SQLite while this repo-dir location raises `disk I/O error`, so the probe MUST live here to
    reflect the backend's real disk. The dir names are pulled from mokata's own constants so this
    can't drift from where the store actually writes."""
    from mokata import MOKATA_DIR, TEMP_LOCAL_DIRNAME
    from mokata.memory.store import MEMORY_DIRNAME
    return os.path.join(os.getcwd(), MOKATA_DIR, TEMP_LOCAL_DIRNAME, MEMORY_DIRNAME)


def _probe_sqlite_disk():
    """Create + write a throwaway file-backed SQLite DB AT THE SAME on-disk location `SQLiteBackend`
    uses (`_sqlite_probe_dir()` — the repo `.mokata` dir, not /tmp). Return False ONLY when SQLite
    reports the broken-disk artifact there; True otherwise (default to RUNNING the test — an
    unrelated failure must surface as a real failure, not a silent skip)."""
    base = _sqlite_probe_dir()
    try:
        os.makedirs(base, exist_ok=True)
    except OSError:
        # Couldn't even stage the probe dir on this disk. Degrade clean: treat it as its own
        # decision (not a crash) and DEFAULT TO RUN — a dir-create failure is not the precise
        # broken-disk SQLite marker, so it must never masquerade as a clean skip.
        return True
    # A per-process unique name so a concurrent real backend under the same dir can't collide.
    probe_path = os.path.join(base, ".sqlite_disk_probe.%d.db" % os.getpid())
    try:
        conn = sqlite3.connect(probe_path)
        try:
            conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
            conn.execute("INSERT INTO t (v) VALUES (?)", ("x",))
            conn.commit()
            conn.execute("SELECT v FROM t").fetchall()
        finally:
            conn.close()
        return True
    except sqlite3.OperationalError as exc:
        if any(m in str(exc).lower() for m in _DISK_BROKEN_MARKERS):
            return False        # the sandbox broken-disk case → skip cleanly, never error
        return True             # an unrelated OperationalError → let the real test run
    except Exception:           # pragma: no cover - any other error is a real test concern
        return True
    finally:
        # Leave no artifact behind. A committed DB in rollback-journal mode has no side files once
        # the connection is closed; removing the main file is enough (ignore if never created).
        try:
            os.remove(probe_path)
        except OSError:
            pass


def sqlite_disk_ok():
    """True when this filesystem can back a real on-disk SQLite DB (CI + normal dev); False ONLY
    on the sandbox's broken-disk artifact. Cached after the first probe."""
    global _SQLITE_DISK_OK
    if _SQLITE_DISK_OK is None:
        _SQLITE_DISK_OK = _probe_sqlite_disk()
    return _SQLITE_DISK_OK


# --- The repo walk: a nested checkout is NOT this repo's source -------------------------
# A repo-wide sweep that walks the checkout root will happily descend into a SECOND checkout
# placed inside it and count its files as mokata's own. That is not hypothetical: a git
# worktree at `.claude/worktrees/…`, created by Claude Code for a parallel session, duplicated
# every action-pin file and turned `test_pin_drift_all_pins_guarded` red at a clean HEAD.
#
# The rule itself lives in `mokata.repo_walk` — ONE definition, because the product walkers
# (the knowledge index, both graph floors, the anchor scan, the AC mapper, `detect`) need the
# same boundary and a second copy here is the name-drift shape this repo has already paid for.
# It is keyed on STRUCTURE, not on a name: a directory carrying a `.git` entry is the root of a
# different checkout, `.git` as a DIRECTORY (clone, submodule) or as a FILE (the `gitdir:`
# pointer `git worktree add` writes).
from mokata.repo_walk import is_checkout_boundary  # noqa: E402  (after the sys.path fix above)


def iter_worktree_files(root, skip_dirs=()):
    """Yield `(rel_posix_path, abs_path)` for every file ON DISK under `root`, skipping nested
    checkouts and `skip_dirs`.

    `skip_dirs` entries match either a bare directory name (`node_modules`) or a
    repo-relative path (`docs/build`). `.git` needs no entry — it is pruned as the boundary
    marker it is. Use this for any sweep rooted at the REPO ROOT so the nested-checkout
    property is a fact about the repo rather than a habit each author has to remember.

    ⚠ THIS IS THE WORKING TREE, NOT THE INDEX. It yields untracked and gitignored files —
    every `.mokata/temp_local/*.json` a suite run left behind, every editor artefact, every
    build output. That is CORRECT for one question and WRONG for the other, and the name now
    says which it answers:

        "what will `sync-public.sh` rsync into the mirror?"  -> iter_worktree_files  (disk is truth)
        "what does this repo actually track?"                -> iter_tracked_files   (index is truth)

    A sweep that asks the second question and calls this function is `CORPUS-IS-A-FILESYSTEM-
    WALK-NOT-THE-INDEX`: it passes today only because no untracked file happens to match its
    pattern yet. (It was called `iter_repo_files` until stage 3 — a name that named the root
    and not the corpus, so neither caller could be wrong out loud.)

    Deliberately NOT `repo_walk.prune_source_dirs`: that prunes every hidden directory, and a
    sweep over this repo must still reach `.github/workflows` (where the action pins live).
    The BOUNDARY rule is shared; the dot policy is each walker's own."""
    skip = set(skip_dirs)
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = posix_rel(dirpath, root)
        rel_dir = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")
        dirnames[:] = sorted(
            d for d in dirnames
            if d != ".git"
            and d not in skip
            and f"{rel_dir}/{d}".lstrip("/") not in skip
            and not is_checkout_boundary(os.path.join(dirpath, d))
        )
        for name in sorted(filenames):
            yield (f"{rel_dir}/{name}".lstrip("/"), os.path.join(dirpath, name))


class NotACheckout(Exception):
    """`iter_tracked_files` was pointed at a directory git does not track.

    Raised rather than degraded to a walk ON PURPOSE. A silent fallback from "ask the index"
    to "walk the disk" is the exact false green this stage exists to remove: the caller would
    keep getting an answer, the answer would quietly change corpus, and nothing would go red.
    A sweep that cannot reach the index has not performed its check and must say so."""


def iter_tracked_files(root, skip_dirs=()):
    """Yield `(rel_posix_path, abs_path)` for every file GIT TRACKS under `root`.

    The index-reading sibling of `iter_worktree_files`, for the question *"what does this repo
    actually track?"* — the one a walk answers only by luck. `skip_dirs` matches exactly as it
    does there, so a caller swaps one for the other without rewriting its exclusions.

    Three properties, none of them maintained by hand:

    * **Untracked and gitignored files are absent BY CONSTRUCTION**, not by a pruner. This
      repo's walk-based sweeps carry a `.mokata` entry, an `__pycache__` entry, an
      `.egg-info` suffix test — each added by an author who was bitten once. The index needs
      none of them: measured 2026-08-12, `iter_worktree_files(REPO)` yields 33,861 paths of
      which 32,757 are untracked, and this function yields the 1,104 that are.
    * **A nested checkout is excluded FOR FREE.** `git ls-files` never reports another
      checkout's files, so the boundary `is_checkout_boundary` maintains structurally is a
      property of the index rather than a rule. That is why the row calls this the principled
      fix and not merely a faster one.
    * **A linked worktree works**, because git resolves the `gitdir:` pointer itself. Verified
      against a real `git worktree add`, not assumed — `SYNC-PUBLIC-GIT-EXCLUDE-IS-DIRECTORY-
      ONLY` (doc 84) is this repo's standing reminder that naive `.git` handling breaks there.

    Deleted-but-still-tracked paths are dropped: the index lists them, the disk does not have
    them, and every caller here is about to open what it is handed.
    """
    import subprocess

    root = os.path.abspath(root)
    try:
        proc = subprocess.run(
            ["git", "-C", root, "ls-files", "-z", "--cached", "--exclude-standard"],
            capture_output=True,
        )
    except OSError as exc:                        # no git binary at all
        raise NotACheckout("cannot run git at %s: %s" % (root, exc))
    if proc.returncode != 0:
        raise NotACheckout(
            "not a git checkout: %s (git ls-files exit %d: %s)"
            % (root, proc.returncode, proc.stderr.decode("utf-8", "replace").strip())
        )

    skip = set(skip_dirs)
    for rel in sorted(proc.stdout.decode("utf-8", "surrogateescape").split("\0")):
        if not rel:
            continue
        parts = rel.split("/")
        # Same two-form match as the walker: a bare directory name anywhere in the path, or a
        # repo-relative directory prefix.
        if any(p in skip for p in parts[:-1]):
            continue
        if any("/".join(parts[:i]) in skip for i in range(1, len(parts))):
            continue
        ab = os.path.join(root, *parts)
        if not os.path.isfile(ab):
            continue                              # tracked but deleted from the worktree
        yield (rel, ab)


# --- H-1a: the BEHAVIOURAL read-only pin ------------------------------------------------
# "This code path writes nothing durable" cannot be pinned by a name-based sweep (asserting no
# call to `record_usage`/`all_active` catches only the mutations someone thought to name, and
# passes for every one they didn't). The honest pin is a WHOLE-TREE byte snapshot around the
# call: hash every file under the repo root before and after, and require them equal. It catches
# a stats bump, a usage stamp, a cache file, a journal line — anything that touched the disk,
# named or not.
def tree_snapshot(root):
    """`{relpath: (size, sha256)}` for every file under `root`. Unreadable files are skipped
    (they are equally unreadable in both snapshots, so they cannot mask a change)."""
    import hashlib
    snap = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            ab = os.path.join(dirpath, name)
            try:
                with open(ab, "rb") as fh:
                    data = fh.read()
            except OSError:
                continue
            snap[posix_rel(ab, root)] = (len(data), hashlib.sha256(data).hexdigest())
    return snap


# A tiny, deterministic Python repo for exercising the knowledge-layer queries.
# Relationships (used by the structural-query tests):
#   helper  <- called by compute
#   compute <- called by Impl.run (mod_a) and main (mod_b)
#   Base    <- subclassed by Impl (mod_a) and OtherImpl (mod_b)
#   mod_a   <- imported by mod_b
_MOD_A = '''\
def helper():
    return 1


def compute():
    return helper() + helper()


class Base:
    def run(self):
        raise NotImplementedError


class Impl(Base):
    def run(self):
        return compute()
'''

_MOD_B = '''\
from mod_a import compute, Base


def main():
    return compute()


class OtherImpl(Base):
    pass
'''


def write_sample_repo(root):
    """Write the sample repo into `root` and return the path."""
    with open(os.path.join(root, "mod_a.py"), "w", encoding="utf-8") as fh:
        fh.write(_MOD_A)
    with open(os.path.join(root, "mod_b.py"), "w", encoding="utf-8") as fh:
        fh.write(_MOD_B)
    return root


# --- TM.S11a: satisfy the brainstorm decision-lens HARD-GATE in fixtures ----------------
# `session.approve()` now refuses until BOTH pre-spec lenses (blast radius + architectural fit)
# are on the table. For fixtures whose focus is a DOWNSTREAM phase (engine/premortem/completeness/
# lifecycle), this records both lenses (degraded Lens 1, a neutral "fits" Lens 2) then approves —
# so those tests exercise their real subject without re-deriving the lenses themselves.
def approve_with_lenses(session, approver, name, **kw):
    from mokata.brainstorm_impact import DesignFitVerdict, FITS
    session.assess_impacts()                    # Lens 1 (no layer → degraded, but on the table)
    for a in session.approaches:
        session.record_design_fit(a.name, DesignFitVerdict(a.name, FITS, []))   # Lens 2 (neutral)
    return session.approve(approver, name, **kw)


# --- Stage 65: a tiny polyglot repo, one file per language, same relationships ---------
# In every language: helper() <- called by compute(); compute() <- called by caller();
# a type Impl implements/extends Base; a module mod_a is imported; one AC-1-tagged test.
_POLYGLOT = {
    "svc.py": '''\
import mod_a


def helper():
    return 1


def compute():
    return helper()


def caller():
    return compute()


class Impl(Base):
    pass


def test_login():
    # AC-1 login works
    assert compute() == 1
''',
    "svc.ts": '''\
import { thing } from "./mod_a";


function helper() {
    return 1;
}


function compute() {
    return helper();
}


function caller() {
    return compute();
}


class Impl extends Base {
}


test("logs in", () => {
    // AC-1 login works
    expect(compute()).toBe(1);
});
''',
    "svc.go": '''\
package main

import "mod_a"


func helper() int {
    return 1
}


func compute() int {
    return helper()
}


func caller() int {
    return compute()
}


func TestLogin(t *testing.T) {
    // AC-1 login works
    compute()
}
''',
    "svc.rs": '''\
use mod_a;


fn helper() -> i32 {
    1
}


fn compute() -> i32 {
    helper()
}


fn caller() -> i32 {
    compute()
}


struct Impl;

impl Base for Impl {
}


#[test]
fn test_login() {
    // AC-1 login works
    assert_eq!(compute(), 1);
}
''',
    "Svc.java": '''\
import com.example.mod_a;


class Impl extends Base {
    int helper() {
        return 1;
    }

    int compute() {
        return helper();
    }

    int caller() {
        return compute();
    }

    @Test
    void testLogin() {
        // AC-1 login works
        compute();
    }
}
''',
}


def write_polyglot_repo(root):
    """Write one source file per supported language into `root`; return the path.

    The same structural relationships hold in each file so a single set of assertions can
    exercise the language-aware grep floor across Python/JS-TS/Go/Rust/Java."""
    for name, body in _POLYGLOT.items():
        with open(os.path.join(root, name), "w", encoding="utf-8") as fh:
            fh.write(body)
    return root


def polyglot_files():
    """Map language name -> the filename written by write_polyglot_repo (for per-file asserts)."""
    return {"python": "svc.py", "ts": "svc.ts", "go": "svc.go",
            "rust": "svc.rs", "java": "Svc.java"}


def sample_manifest_data():
    """A small, valid manifest with one degradable and one always-on capability."""
    return {
        "manifest_version": 1,
        "mokata": {"version": "0.1.0"},
        "profile": "standard",
        "layers": {
            "engine": {"enabled": True},
            "knowledge": {"enabled": True},
            "memory": {"enabled": True},
            "governance": {"enabled": True},
        },
        "capabilities": {
            "code_graph": {
                "description": "structural queries",
                "fallback": ["graphtool", "grep"],
            },
            "memory_store": {
                "description": "where memory lives",
                "fallback": ["sqlite"],
            },
        },
        "tools": {
            "graphtool": {
                "provides": "code_graph",
                "kind": "mcp",
                "version": None,
                "detect": {"type": "command", "name": "definitely-not-a-real-cmd-xyz"},
            },
            "grep": {
                "provides": "code_graph",
                "kind": "builtin",
                "version": None,
                "detect": {"type": "always"},
            },
            "sqlite": {
                "provides": "memory_store",
                "kind": "library",
                "version": None,
                "detect": {"type": "python_module", "name": "sqlite3"},
            },
        },
    }


# ======================================================================================
# stdin doubles — the ONE seam a test may drive to choose which consent arm it grades
# ======================================================================================
#
# 🔴 WHY THIS IS SHARED CODE AND NOT A HELPER EACH TEST WRITES.
#
# Every consent gate in mokata is fail-closed off a TTY: `prompt.read_yes_no` and
# `cli_commands/_common._cli_ask` both take the safe default WITHOUT calling `input()` when
# `stdin.isatty()` is false. That is correct at runtime and a trap in a suite, because it means
# **the machine decides which arm a test grades**. Off a TTY the test reaches the `NO_TTY` branch
# and passes; at a real terminal the same test reaches `input()` — and if it also redirected
# stdout, the prompt is swallowed and the run hangs, silently, until a human types into the void.
#
# Measured 2026-08-26 over all 7,616 tests (`tests/_tty_prompt_sweep.py`): three tests were in that
# state, and one of them had produced a failure that looked for all the world like a regression.
# Filed as `SUITE-HANGS-AT-A-TTY-ON-THREE-TESTS`.
#
# ⭐ `test_a3_decline_strands_at_spec` had the right answer already and had it first: install your
# own stdin, let the REAL reader read it (doc 85 §7e — drive the boundary, never patch the reader),
# and the arm you grade is the arm you chose, on every host. These are that file's doubles, moved
# here so the other three use the same ones rather than three more copies of a non-obvious shape.


class NoTtyStdin:
    """A captured / redirected stdin: not a terminal, and any read raises. This is what an agent
    harness, a pipe, or `< /dev/null` under capture actually hands the process."""

    def isatty(self):
        return False

    def readline(self, *a):
        raise OSError("reading from a non-interactive stdin")

    def read(self, *a):
        raise OSError("reading from a non-interactive stdin")

    def fileno(self):
        raise OSError("no fileno")


class TtyStdin:
    """A REAL terminal, as far as every check mokata makes is concerned, handing back a scripted
    answer. `input()` falls back to `sys.stdin.readline()` because `fileno()` raises, so the answer
    is read through the same path a human's keystroke takes."""

    def __init__(self, answer="n"):
        self._lines = [answer if answer.endswith("\n") else answer + "\n"]

    def isatty(self):
        return True

    def readline(self, *a):
        return self._lines.pop(0) if self._lines else ""

    def read(self, *a):
        return "".join(self._lines)

    def fileno(self):
        raise OSError("no fileno")


class stdin_is:
    """`with stdin_is(TtyStdin("y")):` — swap stdin, always put it back.

    ⛔ Restores in `__exit__` unconditionally: a test that left a double installed would decide the
    arm for every test after it in the same process, which is the ambient-decides defect this whole
    block exists to remove, moved one scope over."""

    def __init__(self, fake):
        self.fake = fake

    def __enter__(self):
        import sys as _sys
        self.orig = _sys.stdin
        _sys.stdin = self.fake
        return self.fake

    def __exit__(self, *_exc):
        import sys as _sys
        _sys.stdin = self.orig


# --- the release scripts, copied into a synthetic tree ------------------------------------------
#: `sync-public.sh` does not stand alone any more: since 0.0.21 stage 02 it asks
#: `scripts/verify-mirror.sh` for the mirror's default branch rather than guessing `main`
#: (`RELEASE-SH-HARDCODES-main`). A synthetic tree carrying only `sync-public.sh` is therefore not
#: a copy of the script — it is a copy that hits the script's own "my helper is missing" refusal,
#: and that refusal is what three test modules measured instead of what they meant to.
#:
#: ⚠ THREE CALL SITES WANTED THIS AND I ADDED THE THIRD, which is the moment to make it one
#: declaration rather than the moment to copy the line again. A fourth site that copies by hand
#: will drift the day a second companion appears; this one cannot.
SYNC_COMPANIONS = ("sync-public.sh", "verify-mirror.sh")


def install_release_scripts(repo_root, dest_scripts_dir, names=SYNC_COMPANIONS):
    """Copy the release scripts a synthetic source tree needs into `<tree>/scripts/`.

    Returns the names actually copied. A name that is absent from `repo_root` is SKIPPED rather
    than raised on: these files are internal-only, so a shipped-subset run legitimately has none
    of them, and the caller's own `skipUnless` guard is what decides whether that matters.
    """
    import shutil as _shutil
    os.makedirs(dest_scripts_dir, exist_ok=True)
    copied = []
    for name in names:
        src = os.path.join(repo_root, "scripts", name)
        if os.path.exists(src):
            _shutil.copy2(src, os.path.join(dest_scripts_dir, name))
            copied.append(name)
    return tuple(copied)


# --- 0.0.26 O11: THE GRAPH BACKEND A TEST MEANS, PINNED ----------------------------------------
# `mokata[full]` installs code-review-graph, and GR.S2(g) USES a detected one read-only on a repo
# that never adopted it (`knowledge.graph_adopt.detected_graph_overlay`). `detect.find_command`
# also looks in the running interpreter's scripts dir (0.0.25 I-02), so a venv that has CRG finds
# it even when that venv is not on PATH. A test that drives the real router through
# `Surface.load` therefore meant one thing in a core venv and another in a full one — 30+ tests
# went red in the full venv on a correct tree. A test that depends on which graph answers says so
# with ONE of these, and then means the same thing everywhere:
#
#   * `hide_external_graphs(tc)`  — the machine has no external graph (the core install);
#   * `crg_present_unbuilt(tc)`   — code-review-graph is installed and this repo never built its
#     index (the common `mokata[full]` first run). Every `query_graph_tool` call answers the reply
#     RECORDED from 2.3.9 in that state; returns the list of calls so a test can prove CRG was asked.
EXTERNAL_GRAPH_COMMANDS = ("code-review-graph", "serena")

# RECORDED from code-review-graph 2.3.9: `query_graph_tool(callers_of, …)` in a git repo it never
# built (`target` is filled in per call, as the tool does).
CRG_EMPTY_GRAPH_REPLY = {
    "status": "not_found",
    "confidence": "graph is empty: nothing is indexed, so this 0 says nothing about the code; "
                  "run `code-review-graph build`"}


def hide_external_graphs(testcase=None):
    """Pin "no external graph on this machine". With no `testcase`, for the whole module (call it
    from `setUpModule`; the patch is undone by `unittest.addModuleCleanup`)."""
    import unittest
    from unittest import mock
    from mokata import detect
    real = detect.find_command

    def find_command(name):
        return None if name in EXTERNAL_GRAPH_COMMANDS else real(name)
    p = mock.patch.object(detect, "find_command", find_command)
    p.start()
    (testcase.addCleanup if testcase is not None else unittest.addModuleCleanup)(p.stop)


# RECORDED from 2.3.9: the MCP `tools/call` result when `repo_root` is not a project (no .git,
# .svn or .code-review-graph) — every temp dir a test builds without `git init`.
CRG_NOT_A_PROJECT_RESULT = {
    "content": [{"type": "text", "text": "Error calling tool 'query_graph_tool': repo_root does "
                 "not look like a project root (no .git, .svn, or .code-review-graph directory "
                 "found)"}],
    "isError": True}


def _crg_present(testcase, *, index_built, reply):
    """Force code-review-graph PRESENT through the real detector and stub it at the MCP wire
    (`_McpStdioSession._rpc`), so the client's own reply parsing runs. The index probe and the
    background builder are stubbed too: no test ever spawns a real build. Returns the list of
    calls: `(tool, params)` per MCP call, `("index-probe", root)` per probe, `("build", root)` per
    background build started."""
    import json
    from unittest import mock
    from mokata import detect
    from mokata.knowledge import crg_build, crg_client
    from mokata.knowledge.crg_client import CodeReviewGraphClient
    real = detect.find_command
    calls = []

    def find_command(name):
        if name == "code-review-graph":
            return os.path.join(os.sep, "opt", "crg", "bin", name)
        return None if name in EXTERNAL_GRAPH_COMMANDS else real(name)

    def rpc(_self, method, params):
        if method != "tools/call":
            return {}
        calls.append((params.get("name"), dict(params.get("arguments") or {})))
        return json.loads(json.dumps(reply(params.get("arguments") or {})))

    def run_cli(_self, args):
        if list(args) == ["--version"]:
            return 0, "code-review-graph 2.3.9", ""
        return 1, "", "not available in this test"

    def probe(root):
        calls.append(("index-probe", root))
        return index_built() if callable(index_built) else index_built

    def spawn(root):
        calls.append(("build", root))
        return os.getpid()            # a live pid: the "build" stays running for the test
    for p in (mock.patch.object(detect, "find_command", find_command),
              mock.patch.object(crg_client._McpStdioSession, "_ensure", lambda _self: None),
              mock.patch.object(crg_client._McpStdioSession, "_rpc", rpc),
              mock.patch.object(CodeReviewGraphClient, "_default_run_cli", run_cli),
              mock.patch.object(crg_build, "index_built", probe),
              mock.patch.object(crg_build, "_spawn_worker", spawn)):
        p.start()
        testcase.addCleanup(p.stop)
    return calls


def crg_present_unbuilt(testcase):
    """code-review-graph is on this machine and the repo never built its index (the `mokata[full]`
    first run). 0.0.26 O13: the index probe says unbuilt, so the tool is NOT asked (no `serve`); a
    first query starts ONE background build (stubbed: recorded as `("build", root)`) and the floor
    answers. Should the tool be asked anyway, it answers the recorded empty-graph reply."""
    import json

    def reply(args):
        r = dict(CRG_EMPTY_GRAPH_REPLY)
        r["summary"] = f"No node found matching '{args.get('target')}'."
        return {"content": [{"type": "text", "text": json.dumps(r)}]}
    return _crg_present(testcase, index_built=False, reply=reply)


def crg_present_failing(testcase):
    """code-review-graph is on this machine, its index is built, and the tool FAILS when asked —
    the recorded 2.3.9 MCP error (`isError: true`). The layer must degrade loudly, never read it
    as a zero."""
    return _crg_present(testcase, index_built=True, reply=lambda _a: CRG_NOT_A_PROJECT_RESULT)
