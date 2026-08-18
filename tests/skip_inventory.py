"""WHICH TESTS DO NOT RUN HERE, AND WHY — the inventory, printed, for one platform at a time.

    python tests/skip_inventory.py            # human-readable, grouped by reason
    python tests/skip_inventory.py --json     # machine-readable, one object

WHY IT EXISTS. Every current mokata user is on Windows, and `ci.yml`'s Windows unit leg reports
`skipped=204` against ubuntu's `139` on the SAME tree and the SAME run. A count is not an
inventory: it says how many tests did not run and nothing at all about whether any of them covers
code this release changed. "It passed" and "it did not run" are different facts and a summary line
gives them one representation (doc 85 §7g).

⭐ WHY IT DOES NOT RUN THE SUITE, WHICH IS THE ONLY REASON IT IS USABLE. A full Windows unit run is
~30 minutes; that cost is exactly why nobody has produced this inventory before. `unittest`
evaluates DECORATOR skips at LOAD time — `@skipIf` / `@skipUnless` set `__unittest_skip__` and
`__unittest_skip_why__` on the class or method as the module is imported — so discovery alone
answers for every one of them, in seconds, on the platform in question.

THE HONEST LIMIT, REPORTED RATHER THAN HIDDEN: a `self.skipTest(...)` inside a test BODY is decided
at run time and is invisible to discovery. Those are counted separately, by parsing, and reported
as `runtime-decided` — a population, not a silence. A tool that quietly omitted them would be
telling you the inventory is complete when it is not, which is the failure this file is about.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import argparse
import ast
import json
import os
import platform
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))


def _walk(suite):
    """Every TestCase in a suite tree, however deeply nested."""
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            for inner in _walk(item):
                yield inner
        else:
            yield item


def _skip_reason(test):
    """The declared skip reason for `test`, or None when it will actually run.

    METHOD first, then CLASS. A method-level `@skipUnless` on a class that is itself skipped would
    otherwise be reported under the class's reason, and the two are different facts: one says "this
    whole area is off here", the other says "this one case needs a thing this host lacks"."""
    method = getattr(test, test._testMethodName, None)
    if getattr(method, "__unittest_skip__", False):
        return getattr(method, "__unittest_skip_why__", "") or "(no reason given)"
    if getattr(test.__class__, "__unittest_skip__", False):
        return getattr(test.__class__, "__unittest_skip_why__", "") or "(no reason given)"
    return None


def _load_errors(test):
    """`unittest` turns an unimportable module into a synthetic failing test. That is neither a
    skip nor a pass and must not be counted as either."""
    return test.__class__.__module__ == "unittest.loader"


def collect(start=HERE, pattern="test*.py"):
    """`{"skipped": [...], "ran": int, "load_errors": [...]}` for THIS interpreter, THIS host."""
    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=start, top_level_dir=start, pattern=pattern)
    skipped, ran, broken = [], 0, []
    for test in _walk(suite):
        if _load_errors(test):
            broken.append(str(test))
            continue
        reason = _skip_reason(test)
        if reason is None:
            ran += 1
        else:
            skipped.append({"test": test.id(), "reason": reason,
                            "module": test.__class__.__module__})
    return {"skipped": skipped, "ran": ran, "load_errors": broken}


def runtime_skip_sites(start=HERE):
    """Modules containing a `self.skipTest(...)` call — the population discovery cannot see.

    Parsed rather than grepped: a `skipTest` inside a docstring or a comment is prose about the
    mechanism, not a use of it, and this file's whole point is to stop a count standing in for an
    inventory."""
    found = {}
    for name in sorted(os.listdir(start)):
        if not (name.startswith("test") and name.endswith(".py")):
            continue
        try:
            with open(os.path.join(start, name), encoding="utf-8") as handle:
                tree = ast.parse(handle.read())
        except (OSError, SyntaxError):                       # pragma: no cover
            continue
        count = sum(
            1 for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("skipTest", "skipIfCondition"))
        if count:
            found[name] = count
    return found


def _host():
    return {"os_name": os.name, "platform": sys.platform,
            "python": platform.python_version(),
            "machine": platform.machine()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="emit one JSON object")
    args = parser.parse_args(argv)

    data = collect()
    data["host"] = _host()
    data["runtime_skip_sites"] = runtime_skip_sites()

    if args.json:
        print(json.dumps(data, indent=1, sort_keys=True))
        return 0

    by_reason = {}
    for row in data["skipped"]:
        by_reason.setdefault(row["reason"], []).append(row)

    host = data["host"]
    print("SKIP INVENTORY — os.name=%s sys.platform=%s python=%s"
          % (host["os_name"], host["platform"], host["python"]))
    print("  %d tests will run · %d are skipped at LOAD time · %d modules hold a runtime "
          "skipTest (invisible here, and that is stated rather than hidden)"
          % (data["ran"], len(data["skipped"]), len(data["runtime_skip_sites"])))
    if data["load_errors"]:
        print("  ⚠ %d modules FAILED TO LOAD and are counted as neither: %s"
              % (len(data["load_errors"]), ", ".join(data["load_errors"][:5])))
    print()
    for reason, rows in sorted(by_reason.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        modules = sorted({r["module"] for r in rows})
        print("%4d  %s" % (len(rows), reason))
        print("      in: %s%s" % (", ".join(modules[:6]),
                                  "" if len(modules) <= 6 else " (+%d more)" % (len(modules) - 6)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
