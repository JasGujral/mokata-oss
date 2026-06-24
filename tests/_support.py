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
