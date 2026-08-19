"""Shared test support: put `src/` on the import path and build sample manifests."""

import os
import shutil
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


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
