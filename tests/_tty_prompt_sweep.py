"""THE TTY-PROMPT SWEEP — which tests hand their verdict to whoever is watching the terminal.

NOT A TEST. An INSTRUMENT, committed rather than left in `/tmp`, because a finding whose
reproduction lives in a scratch directory is a finding nobody can re-check.

    PYTHONPATH=tests/_tty_sweep TTYSWEEP=1 TTYSWEEP_OUT=/tmp/ttysweep.jsonl \
        python -m unittest discover -s tests -t tests

(The directory on `PYTHONPATH` must contain this file under the name `sitecustomize.py`; that is
the only hook that runs before the first test module is imported. `tests/_tty_sweep/` holds a
one-line copy for exactly that reason.)

WHAT IT MEASURES, AND WHY THE OBVIOUS VERSION MEASURES THE WRONG THING
---------------------------------------------------------------------
Every consent gate in mokata is fail-closed off a TTY: `prompt.read_yes_no` and `_cli_ask` both
take the safe default WITHOUT calling `input()` when `stdin.isatty()` is false. That is correct
runtime behaviour and it is also a trap for the suite, because it means **the machine decides which
arm a test grades**. Off a TTY a test reaches the `NO_TTY` branch and passes; at a terminal the same
test reaches `input()` — and if it also redirected stdout, the prompt is swallowed and the run
hangs with nothing on screen until a human types into the void.

So the sweep forces the OTHER ambient value (`isatty()` -> True) and makes `input()` fatal.

⭐ THE DISTINCTION THAT MAKES THE READING WORTH ANYTHING. A test that installs its OWN stdin double
and lets the real reader read it -- `test_a3_decline_strands_at_spec`, the model of the correct
pattern (doc 85 §7e: drive the boundary, never patch the reader) -- owns its answer and can never
block a human. The first form of this instrument flagged all ten of those as offenders, which would
have made the reading useless. An offender is a test that reaches a prompt with the AMBIENT stream
still installed, and the sweep decides that by the IDENTITY of the stdin object, not by a guess.

FIRST READING — 2026-08-26, 7,616 tests, THREE offenders:

    test_stage37_spec_awareness.TestCliSpecCheck.test_cli_conflict_blocks_without_confirmation
    test_cli_exec.TestExecCLI.test_default_is_sequential
    test_si_1_hook_gates.TestOverride.test_declining_the_reconfirmation_leaves_the_gate_enforced

Filed as `SUITE-HANGS-AT-A-TTY-ON-THREE-TESTS` (doc 84).

TWO DELIBERATE DIVERGENCES, so the sweep finds things rather than stopping at the first:
  * the raised class inherits `BaseException`, so a caller's `except Exception` cannot swallow it;
  * the offender is journalled BEFORE the raise, so a caller that swallows it anyway is recorded.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""
import builtins
import json
import os
import sys
import traceback
import unittest

_OUT = os.environ.get("TTYSWEEP_OUT", "/tmp/ttysweep.jsonl")
_CURRENT = {"test": "<module-import-or-setUpModule>"}


class _ForcedTtyStdin:
    """Delegates everything to the real stdin except isatty(), which says True."""

    def __init__(self, real):
        self.__dict__["_real"] = real

    def isatty(self):
        return True

    def __getattr__(self, name):
        return getattr(self.__dict__["_real"], name)

    def __setattr__(self, name, value):
        setattr(self.__dict__["_real"], name, value)

    def __iter__(self):
        return iter(self.__dict__["_real"])


class TtyPromptReached(BaseException):
    pass


_real_input = builtins.input


def _input(prompt=""):
    """Fatal ONLY when the AMBIENT stream is still in place.

    `test_a3_decline_strands_at_spec` is the model of a CORRECT interactive test: it installs its
    own stdin double and lets the real reader read it (doc 85 §7e -- drive the boundary, never
    patch the reader). Such a test owns its answer, can never block a human, and must not be
    broken by the instrument that measures the ones that can.

    The offender is the test that reaches a prompt with the AMBIENT stdin still installed: the
    answer then comes from whoever happens to be at the terminal. That is the one distinction the
    sweep draws, and it draws it by the IDENTITY of the stream object, not by a guess."""
    if not isinstance(sys.stdin, _ForcedTtyStdin):
        return _real_input(prompt)
    frames = [f for f in traceback.extract_stack()[:-1]]
    rec = {
        "test": _CURRENT["test"],
        "prompt": str(prompt)[:400],
        "stack": [f"{f.filename}:{f.lineno} {f.name}" for f in frames[-14:]],
    }
    with open(_OUT, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")
        fh.flush()
    raise TtyPromptReached(f"MOKATA-TTY-PROMPT-REACHED in {_CURRENT['test']}")


_real_run = unittest.TestCase.run


def _run(self, result=None):
    _CURRENT["test"] = self.id()
    return _real_run(self, result)


if os.environ.get("TTYSWEEP") == "1":
    sys.stdin = _ForcedTtyStdin(sys.stdin)
    builtins.input = _input
    unittest.TestCase.run = _run
