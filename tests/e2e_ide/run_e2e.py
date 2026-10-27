"""Run the IDE browser e2e (0.0.26 O9, IDE-E2E-IN-CI): every flow in `tests/e2e_ide/test_*.py`.

    python tests/e2e_ide/run_e2e.py -v                 every flow (how ci.yml's `ide-e2e` runs it)
    python tests/e2e_ide/run_e2e.py -v -k tabs         flows whose test ids match `tabs`

A SCRIPT, not `python -m unittest discover -s tests/e2e_ide`: the release preflight mirrors every
`discover -s tests…` call in ci.yml as the unit suite (`tests/_preflight_parity.py`), and this is
not that suite — it needs a browser the unit legs never install. `tests/e2e_ide/` has no
`__init__.py`, so the unit suite's `discover -s tests` never descends here.

Exit 0 green · 1 red · 2 no flow found (a runner that ran nothing must not read green).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import argparse
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="run_e2e", description=__doc__.split("\n")[0])
    ap.add_argument("-v", dest="verbosity", action="store_const", const=2, default=1)
    ap.add_argument("-k", dest="patterns", action="append", default=None,
                    help="only test ids containing this (as unittest -k)")
    a = ap.parse_args(argv)
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    loader = unittest.TestLoader()
    if a.patterns:
        loader.testNamePatterns = ["*%s*" % p for p in a.patterns]
    suite = loader.discover(HERE, pattern="test_*.py", top_level_dir=HERE)
    if not suite.countTestCases():
        print("run_e2e: no flow found under %s" % HERE, file=sys.stderr)
        return 2
    result = unittest.TextTestRunner(verbosity=a.verbosity).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
