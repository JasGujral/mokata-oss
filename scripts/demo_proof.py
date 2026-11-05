"""The P13 `ID-VIS-PROOF` launch demo, reproducible (0.0.27; ruling U15; D-list candidate).

    python scripts/demo_proof.py                 # build the demo repo in a temp dir and open the IDE
    python scripts/demo_proof.py --path DIR      # build it in DIR (must not exist or be empty)
    python scripts/demo_proof.py --no-open       # build only; print the `mokata ide` command

What it makes — a small git repository of its OWN (never your repo), `mokata init`ed, with:
  * a run `demo-run` whose emitted spec has three acceptance criteria;
  * a session `demo-session` whose recorded writes changed `src/shop/parser.py` (`parse`, and a new
    `report_error` nobody tests) and added `tests/test_parser.py::test_total`;
  * the test record: `test_parse` went RED then GREEN for AC-1 (TDD-proven), `test_total` only ever
    GREEN for AC-2 (not TDD-proven), AC-3 has no test;
  * one gate block in the progress log and one override + one redeemed write approval in the ledger.

Then `mokata ide --path <repo>` → the **Proof** screen (rail ✓, or `g v`) → run `demo-run`:
AC → test → code in three columns, the risk overlay on. The same screen on YOUR repository needs
no seed at all: `pip install "mokata[ide]" && mokata ide` opens Proof on the working tree (code →
tests, blast radius, flags); a run made with Claude Code (/brainstorm → /spec → tests RED →
`mokata progress test-run --failed …` → implement → `--passed …`) adds the AC half. No API key:
mokata never calls a model.

Stdlib + mokata only. Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile

PARSER_OLD = ("def parse(text):\n    return text.split(',')\n\n\ndef total(text):\n"
              "    return len(parse(text))\n")
PARSER_NEW = ("def parse(text):\n    if not text:\n        return []\n"
              "    return [p.strip() for p in text.split(',')]\n\n\ndef total(text):\n"
              "    return len(parse(text))\n\n\ndef report_error(pos):\n"
              "    return 'bad item at %d' % pos\n")
TEST_OLD = ("from shop.parser import parse\n\n\ndef test_parse():\n"
            "    assert parse('a, b') == ['a', 'b']\n")
TEST_NEW = (TEST_OLD + "\n\ndef test_total():\n    from shop.parser import total\n"
            "    assert total('a,b') == 2\n")


def _git(d, *a):
    subprocess.run(["git", "-C", d, *a], check=True, capture_output=True, stdin=subprocess.DEVNULL)


def _write(d, rel, text):
    p = os.path.join(d, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def build(d: str) -> None:
    from mokata import progress_events as PE
    from mokata import run_intent as RI
    from mokata import test_runs as TR
    from mokata.config import Surface
    from mokata.govern.ledger import AuditLedger
    from mokata.govern.resume import PipelineCheckpoint
    from mokata.ide import snapshots as S
    from mokata.init import init_repo
    from mokata.plan_tasks import TASKS_KEY, seal
    from mokata.tdd_state import state_dir

    _git(d, "init", "-q", "-b", "main")
    _git(d, "config", "user.email", "demo@example.invalid")
    _git(d, "config", "user.name", "demo")
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda *_a, **_k: None)
    _write(d, "src/shop/__init__.py", "")
    _write(d, "src/shop/parser.py", PARSER_OLD)
    _write(d, "tests/test_parser.py", TEST_OLD)
    _write(d, "README.md", "# proof demo\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-qm", "before the run")

    run, sid = "demo-run", "demo-session"
    surface = Surface.load(d)
    PipelineCheckpoint(surface.state, run).ensure_registered()
    criteria = [
        {"id": "AC-1", "text": "empty input parses to an empty list",
         "anchors": [{"file": "src/shop/parser.py", "symbol": "parse", "kind": "impl",
                      "source": "spec", "confidence": "high"}]},
        {"id": "AC-2", "text": "total counts the parsed items"},
        {"id": "AC-3", "text": "a malformed item is reported with its position"},
    ]
    spec = {"title": "parser hardening", "approach": "guard, then strip", "domains": [],
            "criteria": criteria,
            TASKS_KEY: [{"id": "T1", "title": "the change", "ac_ids": [c["id"] for c in criteria],
                         "scope": ["*"]}]}
    surface.state.write("emitted_spec__" + run, seal(spec))   # DEMO SEED (a real run emits this)
    RI.bind_registered(d, sid, run)
    for rel, new in (("src/shop/parser.py", PARSER_NEW), ("tests/test_parser.py", TEST_NEW)):
        path = os.path.join(d, *rel.split("/"))
        S.capture_before_first_write(d, path, sid, run)
        _write(d, rel, new)
        S.capture(d, path, S.AFTER, sid, run)
    # full ids — a test is identified by path::Class::name; a bare name never proves
    parse_id, total_id = "tests/test_parser.py::test_parse", "tests/test_parser.py::test_total"
    TR.record_test_run(d, run, passed=False, command="python -m pytest -q",
                       tests=[parse_id], ac_map={"AC-1": [parse_id]})
    TR.record_test_run(d, run, passed=True, command="python -m pytest -q",
                       tests=[parse_id, total_id], ac_map={"AC-2": [total_id]})
    led = AuditLedger.from_mokata_dir(os.path.join(d, ".mokata"))
    led.record("write_approval", decision="redeemed", proposal="demo-p1", tool="write_file",
               target="src/shop/parser.py", approved_by="you", run=run, committed=True)
    led.record("gate_override", gate="stop-evidence", run=run, actor="you", decision="override",
               scope="session", reason="demo: accepted the stop unverified")
    PE.record_gate_block(state_dir(d), run, "no-code-without-failing-test", "src/shop/parser.py")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--path", default=None, help="where to build the demo repo (new or empty dir)")
    ap.add_argument("--no-open", action="store_true", help="build only; print the command")
    a = ap.parse_args(argv)
    d = os.path.abspath(a.path) if a.path else tempfile.mkdtemp(prefix="mokata-proof-demo-")
    if os.path.isdir(d) and os.listdir(d):
        print(f"refusing: {d} is not empty (the demo builds a repository of its own)", file=sys.stderr)
        return 2
    os.makedirs(d, exist_ok=True)
    build(d)
    cmd = [sys.executable, "-m", "mokata", "ide", "--path", d]
    print(f"demo repository: {d}")
    print("open the IDE, then the Proof screen (rail ✓ or `g v`) and pick run demo-run:")
    print("  " + " ".join(cmd))
    if a.no_open:
        return 0
    return subprocess.call(cmd, stdin=subprocess.DEVNULL)


if __name__ == "__main__":
    sys.exit(main())
