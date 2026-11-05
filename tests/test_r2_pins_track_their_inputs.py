"""0.0.27 R2 - DEPENDABOT-10: the committed pins cannot drift from the files they come from.

Offline by design (no advisory lookup, no network): advisories are triaged by the release
process, but the two ways a bump silently fails to land are checkable from the checkout alone.

  * An exact pin in a `requirements/*.in` (e.g. `mkdocs-material==9.7.7`) is the version the
    hash-pinned `.txt` installs. A `.in` edited without recompiling would leave CI installing the
    old, vulnerable version while the source of truth says otherwise.
  * The Homebrew lock records the `mcp` requirement it was resolved against. If that parts from
    pyproject's declared bound, the vendored tree was resolved against a different question than
    the one pip answers, and brew users get a different SDK than pip users.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import _support  # noqa: F401

import glob
import json
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQ = os.path.join(ROOT, "requirements")
LOCK = os.path.join(ROOT, "packaging", "homebrew", "resources.lock.json")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _norm(name):
    return re.sub(r"[-_.]+", "-", name).lower()


class PinsTrackTheirInputs(unittest.TestCase):
    # CORPUS: THE WORKING TREE — CI installs from the files on disk, so the disk is the question.
    def test_every_exact_pin_in_an_in_file_is_what_the_txt_installs(self):
        """A contributor who bumps `requirements/X.in` and forgets to recompile would leave CI
        installing the old version; the compiled `.txt` must carry every `==` pin of its `.in`."""
        ins = sorted(glob.glob(os.path.join(REQ, "*.in")))
        self.assertTrue(ins, "no requirements/*.in found")
        checked = 0
        for path in ins:
            txt = path[:-3] + ".txt"
            self.assertTrue(os.path.exists(txt), f"{os.path.basename(path)} has no compiled .txt")
            compiled = {_norm(m.group(1)): m.group(2)
                        for m in re.finditer(r"(?m)^([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?==([^\s;\\]+)", _read(txt))}
            for line in _read(path).splitlines():
                m = re.match(r"([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?==([^\s;#]+)", line)
                if not m:
                    continue
                checked += 1
                self.assertEqual(
                    compiled.get(_norm(m.group(1))), m.group(2),
                    f"{os.path.basename(path)} pins {m.group(1)}=={m.group(2)} but "
                    f"{os.path.basename(txt)} does not - run scripts/gen-pinned-requirements.sh")
        self.assertGreater(checked, 0, "found no exact pins to compare - the pattern is stale")

    def test_the_brew_lock_was_resolved_against_pyprojects_mcp_bound(self):
        """Brew users must get the SDK window pip users get: the lock's recorded requirement
        equals pyproject's declared one, so `refresh-lock` is never run against a stale question."""
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import fill_homebrew_formula as FF
        self.assertEqual(json.loads(_read(LOCK))["requirement"], FF.declared_mcp_requirement(),
                         "resources.lock.json was resolved against another mcp bound - "
                         "run scripts/fill_homebrew_formula.py refresh-lock")


if __name__ == "__main__":
    unittest.main()
