"""Shared test support: put `src/` on the import path and build sample manifests."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


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
