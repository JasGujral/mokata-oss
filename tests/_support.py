"""Shared test support: put `src/` on the import path and build sample manifests."""

import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


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
