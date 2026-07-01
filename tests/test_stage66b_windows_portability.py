"""Stage 66b — Windows portability regression guards (cross-platform; run on every OS).

Two real Windows-only bugs surfaced when the CI matrix first exercised windows-latest:

  * WinError 32 — the SQLite memory backend held a persistent OS file handle that
    outlived operations, so a tempdir teardown (shutil.rmtree) failed on Windows
    (Linux/macOS tolerate unlinking an open file; Windows does not).
  * UnicodeDecodeError 0x97 — a text file written without encoding="utf-8" landed as
    cp1252 on Windows (em-dash "—" -> 0x97), then the utf-8 read blew up.

These guards fail fast on ANY OS if either class of bug returns. Pure stdlib; no deps.
"""

from __future__ import annotations

import ast
import os
import shutil
import sqlite3
import tempfile
import unittest
import warnings
from pathlib import Path

from mokata.memory.backends import SQLiteBackend
from mokata.memory.item import MemoryItem

REPO_ROOT = Path(__file__).resolve().parent.parent


# --- lint guard: every text-mode file open must declare encoding="utf-8" -----------
# AST-based (not regex) so it is precise across multi-line calls and never trips on a
# string literal / docstring that merely mentions the word.
def _open_mode_is_binary(call: ast.Call) -> bool:
    mode = None
    if len(call.args) >= 2 and isinstance(call.args[1], ast.Constant):
        mode = call.args[1].value
    for kw in call.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            mode = kw.value.value
    return isinstance(mode, str) and "b" in mode


def _has_encoding(call: ast.Call) -> bool:
    return any(kw.arg == "encoding" for kw in call.keywords)


def _text_opens_without_encoding(root: Path):
    """Every builtin text-mode open() and Path.read_text/write_text under `root` that
    omits encoding=. Binary opens are ignored. Returns (path, lineno)."""
    offenders = []
    for py in sorted(root.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")  # don't surface a scanned file's own warnings
                tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - not our concern here
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name) and func.id == "open":
                if not _open_mode_is_binary(node) and not _has_encoding(node):
                    offenders.append((str(py), node.lineno))
            elif isinstance(func, ast.Attribute) and func.attr in ("read_text", "write_text"):
                if not _has_encoding(node):
                    offenders.append((str(py), node.lineno))
    return offenders


class TestTextIOEncoding(unittest.TestCase):
    def test_no_text_open_without_encoding(self):
        offenders = []
        for sub in ("src/mokata", "tests"):
            offenders += _text_opens_without_encoding(REPO_ROOT / sub)
        self.assertEqual(
            offenders, [],
            "Text-mode file I/O must pass encoding=\"utf-8\" (cp1252 breaks Windows). "
            "Offending lines:\n" + "\n".join(f"  {f}:{n}" for f, n in offenders),
        )


# --- portability guard: a memory op must leave no open file handle -----------------
class TestMemoryBackendNoLingeringHandle(unittest.TestCase):
    def test_sqlite_op_leaves_no_open_handle_and_dir_removable(self):
        d = tempfile.mkdtemp()
        try:
            backend = SQLiteBackend(os.path.join(d, "state", "memory.db"))
            backend.put(MemoryItem(subject="s", value="an em-dash — value"))
            self.assertEqual(len(backend.all()), 1)
            got = backend.get(backend.all()[0].id)
            self.assertIsNotNone(got)

            # (1) The backend must not retain an open sqlite connection (fails on any OS
            #     while a persistent self._conn exists — the root cause of WinError 32).
            live = [v for v in vars(backend).values() if isinstance(v, sqlite3.Connection)]
            self.assertEqual(
                live, [],
                "SQLite backend must not hold a persistent connection after an operation",
            )

            # (2) The real Windows repro: the tempdir must be fully removable while the
            #     backend object is still alive (a lingering handle => PermissionError 32).
            shutil.rmtree(d)
            self.assertFalse(os.path.exists(d))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_in_memory_backend_persists_across_operations(self):
        # An in-memory DB lives only inside its connection: per-operation connections would
        # lose the table/data between calls. It must keep a persistent connection (it has no
        # file handle, so no Windows hazard). Guards the ":memory:" tour/demo path.
        backend = SQLiteBackend(":memory:")
        backend.put(MemoryItem(subject="db.engine", value="postgres"))
        got = backend.all()
        self.assertEqual([i.subject for i in got], ["db.engine"])
        self.assertEqual(got[0].value, "postgres")


if __name__ == "__main__":
    unittest.main()
