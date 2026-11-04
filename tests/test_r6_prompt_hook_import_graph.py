"""0.0.27 R6 — HOOK-BUDGET-1M-MARGIN: the per-prompt hook does less, and says exactly what it said.

Business rules — what must be true for the user:
  * Every prompt the user types waits on mokata's UserPromptSubmit hook (`context` kind, 150 ms p50,
    U14). At the 0.0.26 cut it measured 146 ms on the 1M-line fixture — no margin. The hook's cost is
    import-dominated (callgrind: ~75% of its instructions are module imports, and a third of THAT is
    `dataclasses` generating methods for every dataclass a loaded module defines), so the margin is
    bought by loading less on the prompt path — never by caching an answer.
  * The pack the turn receives is BYTE-IDENTICAL to what v0.0.26 injected for the same store and the
    same session history (the golden below was captured from the v0.0.26 tree).
  * The prompt path's import graph is guarded DETERMINISTICALLY, unlike a timing test: a cold hook
    process must not load the heavy stacks (the MCP SDK, the IDE's web stack, numpy, an embedding
    model, a Postgres driver, jsonschema) nor the mokata modules R6 took off the path (heal /
    consolidate, the team audit log) — and the number of dataclasses it
    decorates is held to a ceiling, because that number IS the cost.

Measured before/after: `/home/claude/bed/reports/R6.report.md`.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)
from _support import sqlite_disk_ok

from mokata import hook_cli
from mokata.config import Surface
from mokata.init import init_repo
from mokata.memory import MemoryStore
from mokata.memory import graph_anchors as GA
from mokata.memory.item import MemoryItem
from mokata.quiet_spawn import hidden

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")

# Fixed ids and timestamps, so the ranking's tie-breaks (created_at, subject) cannot move.
_ROWS = (("rule", "parser rule", "never parse the same buffer twice"),
         ("guardrail", "deploy guard", "no friday deploys of the parser"),
         ("decision", "session decision", "SessionMiddleware owns the cookies"),
         ("reference", "parser ref", "the parser design is in docs/parser.md"),
         ("context", "parser bug", "the parser bug lives in tokenize()"),
         ("context", "SessionMiddleware note", "SessionMiddleware wraps every request"),
         ("context", "deploy note", "deploys run from main only"),
         ("context", "unrelated", "the cafeteria opens at nine"))

_PROMPTS = ("fix the parser bug in SessionMiddleware", "deploy the parser",
            "fix the parser bug in SessionMiddleware", "what is the weather")

_HEAD = "mokata memory — relevant to this turn:\n"
_RULE = "- [rule] parser rule: never parse the same buffer twice\n"
_GUARD = "- [guardrail] deploy guard: no friday deploys of the parser\n"
#: What v0.0.26 (`559dfd1`) injected for `_PROMPTS`, in order, in ONE session on `_ROWS` — captured
#: from that tree, not written by hand. Turn 3 repeats turn 1's prompt: the S4 ledger has already
#: handed over its items, so only the always-on half is re-sent.
GOLDEN = (
    _HEAD + _RULE + _GUARD
    + "- [context] parser bug: the parser bug lives in tokenize()\n"
    + "- [reference] parser ref: the parser design is in docs/parser.md\n"
    + "- [context] SessionMiddleware note: SessionMiddleware wraps every request\n",
    _HEAD + _GUARD + _RULE + "- [context] deploy note: deploys run from main only\n",
    _HEAD + _RULE + _GUARD,
    _HEAD + _RULE + _GUARD,
)


def _silent(*_a, **_k):
    pass


def _fixture(d: str) -> None:
    subprocess.run(["git", "init", "-q", d], check=True, capture_output=True,
                   stdin=subprocess.DEVNULL, **hidden())
    init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
    store = MemoryStore.from_surface(Surface.load(d), knowledge=False)
    for i, (kind, subject, value) in enumerate(_ROWS):
        store.backend.put(MemoryItem.create(subject, value, kind=kind, id=f"g{i}",
                                            created_at="2026-01-01T00:00:%02dZ" % i))


def _run_hook(d: str, prompt: str, sid: str) -> str:
    payload = json.dumps({"prompt": prompt, "cwd": d, "session_id": sid})
    out = io.StringIO()
    saved, sys.stdin = sys.stdin, io.StringIO(payload)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            hook_cli.main(["user-prompt-submit"])
    finally:
        sys.stdin = saved
    text = out.getvalue()
    return json.loads(text)["hookSpecificOutput"]["additionalContext"] if text else ""


@unittest.skipUnless(sqlite_disk_ok(), "sandbox cannot back an on-disk SQLite DB")
class TheR6_PackIsByteIdenticalToV0026(unittest.TestCase):

    def test_R6_the_same_store_and_history_inject_the_same_bytes(self):
        with tempfile.TemporaryDirectory() as t:
            d = os.path.realpath(t)
            _fixture(d)
            with mock.patch.object(GA, "graph_possible", return_value=False):
                got = tuple(_run_hook(d, p, "R6-golden") for p in _PROMPTS)
        self.assertEqual(GOLDEN, got)


# ======================================================================================
# the import graph of ONE cold prompt-hook process
# ======================================================================================

_PROBE = r"""
import io, json, sys
import mokata_emit
real, sys.stdout = sys.stdout, io.StringIO()
try:
    mokata_emit.main(["user-prompt-submit"])
    pack = sys.stdout.getvalue()
finally:
    sys.stdout = real
mods = sorted(sys.modules)
dcs = sorted(f"{m}.{n}" for m in mods if m.startswith("mokata")
             for n, v in list(vars(sys.modules[m]).items())
             if isinstance(v, type) and "__dataclass_fields__" in vars(v) and v.__module__ == m)
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    json.dump({"modules": mods, "dataclasses": dcs, "pack": pack}, fh)
"""

#: Never on the prompt path. Third-party stacks first (each is tens of ms, and none is needed to
#: rank a memory pack); then what R6 took off it — a regression that re-imports one fails HERE,
#: deterministically, long before a timing test on a shared runner could notice.
FORBIDDEN = (
    "mcp", "starlette", "uvicorn", "anyio", "pydantic", "httpx", "numpy", "model2vec",
    "psycopg", "jsonschema", "argparse",
    "mokata.mcp", "mokata.mcp_server", "mokata.ide.server", "mokata.knowledge.layer",
    # R6: heal / consolidate run in their own flows (6 dataclasses).
    "mokata.memory.healing", "mokata.memory.consolidation",
    # R6: the store's identity is `session.actor`, not the team audit log (4 dataclasses + ledger).
    "mokata.team_audit",
)

#: How many dataclasses a cold prompt hook decorates on this fixture (v0.0.26: 55; R6: 45). Each
#: one costs CPython 3.12 an `exec` per generated method (~2 M instructions per class) — the largest
#: single share of the hook's import time. Raise this only with a measurement that justifies it.
DATACLASS_CEILING = 45


@unittest.skipUnless(sqlite_disk_ok(), "sandbox cannot back an on-disk SQLite DB")
class TheR6_PromptPathLoadsOnlyWhatItUses(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._t = tempfile.TemporaryDirectory()
        d = os.path.realpath(cls._t.name)
        _fixture(d)
        out = os.path.join(d, "probe.json")
        # No code-graph tool on PATH: the graph tier's "nothing installed" path, the common case.
        env = dict(os.environ, PYTHONPATH=SRC, CLAUDE_PROJECT_DIR=d,
                   PATH=os.path.dirname(sys.executable))
        body = json.dumps({"session_id": "R6-probe", "cwd": d, "prompt": _PROMPTS[0],
                           "hook_event_name": "UserPromptSubmit"})
        subprocess.run([sys.executable, "-c", _PROBE, out], input=body, cwd=d, env=env,
                       text=True, encoding="utf-8", capture_output=True, timeout=120,
                       check=True, **hidden())
        with open(out, encoding="utf-8") as fh:
            cls.probe = json.load(fh)

    @classmethod
    def tearDownClass(cls):
        cls._t.cleanup()

    def test_the_probe_delivered_the_pack(self):
        """A hook that imported nothing because it did nothing would pass every guard below."""
        ctx = json.loads(self.probe["pack"])["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(GOLDEN[0], ctx)

    def test_R6_no_heavy_or_off_path_module_is_loaded(self):
        loaded = [m for m in self.probe["modules"]
                  if any(m == f or m.startswith(f + ".") for f in FORBIDDEN)]
        self.assertEqual([], loaded)

    def test_R6_the_dataclass_count_holds_its_ceiling(self):
        self.assertLessEqual(len(self.probe["dataclasses"]), DATACLASS_CEILING,
                             self.probe["dataclasses"])


if __name__ == "__main__":
    unittest.main()
