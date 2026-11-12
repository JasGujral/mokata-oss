"""IDE e2e — the Proof screen names the files a proof was bound to (0.0.28 E2, PROOF-SYMLINK-REPOINT).

Run `r1` recorded `tests/alias.py::test_a` red then green while the link pointed at `tests/test_a.py`;
the link now points at an identical `tests/test_b.py`. The Proof screen never says "proven" for it:
its test row names BOTH files, and the help text says what a proof is bound to. Waits on state.
Symlinks need privilege on some Windows hosts: there the flow is SKIPPED (recorded not-run).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import subprocess
import sys
import textwrap
import unittest

import _harness as H

RUN = "r1"
BODY = "def test_a():\n    assert 1\n"

PLANT = textwrap.dedent("""\
    import os, sys
    sys.path[:0] = [sys.argv[2], sys.argv[3]]
    root, run = sys.argv[1], sys.argv[4]
    from _plan import sealed
    from mokata.config import Surface
    from mokata.govern.resume import PipelineCheckpoint
    from mokata.test_runs import record_test_run
    surface = Surface.load(root)
    PipelineCheckpoint(surface.state, run).ensure_registered()
    surface.state.write("emitted_spec__" + run, sealed({"title": "alias", "approach": "a",
        "domains": [], "criteria": [{"id": "AC-1", "text": "the aliased test proves"}]}))
    tid = "tests/alias.py::test_a"
    record_test_run(root, run, passed=False, command="pytest", tests=[tid],
                    ac_map={"AC-1": [tid]}, at=1000.0)
    record_test_run(root, run, passed=True, command="pytest", tests=[tid], at=2000.0)
    """)


def _can_symlink():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        try:
            os.symlink("x", os.path.join(d, "l"))
            return True
        except (OSError, NotImplementedError, AttributeError):
            return False


def build(base):
    repo = H.make_repo("shop", base)
    H._write(repo, "tests/test_a.py", BODY)
    H._write(repo, "tests/test_b.py", BODY)
    link = os.path.join(repo, "tests", "alias.py")
    os.symlink("test_a.py", link)
    H._git(repo, "add", "-A")
    H._git(repo, "commit", "-q", "-m", "alias")
    tests_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    subprocess.run([sys.executable, "-c", PLANT, repo, tests_dir, H.SRC, RUN], check=True,
                   env=H._env(), stdin=subprocess.DEVNULL)
    os.remove(link)
    os.symlink("test_b.py", link)
    return repo


@unittest.skipUnless(_can_symlink(), "NOT RUN: symlinks need privileges here")
class TheProofScreenNamesTheTargets(H.IdeFlow):
    FIXTURE = staticmethod(build)

    def test_1_a_repointed_alias_is_not_proven_and_both_files_are_named(self):
        p = self.page
        self.go("proof")
        p.wait_for_selector("#view-proof:not([hidden])")
        p.locator("#view-proof .pf-pickbox summary").click()
        p.locator("#view-proof .pf-li", has_text="run %s" % RUN).click()
        self.text_until("#view-proof .pf-tables", "tests/alias.py was tests/test_a.py, now tests/test_b.py",
                        "the repoint note naming both targets")
        tables = p.text_content("#view-proof .pf-tables")
        self.assertIn("GREEN only", tables)
        self.assertNotIn("RED → GREEN (TDD-proven", tables)
        self.assertIn("not TDD-proven", p.text_content("#view-proof .pf-chips"))

    def test_2_the_help_text_says_what_a_proof_is_bound_to(self):
        help_text = self.page.text_content("#view-proof .pf-help")
        self.assertIn("bound to the file it ran against", help_text)
        self.assertIn("hard link", help_text)


if __name__ == "__main__":
    unittest.main()
