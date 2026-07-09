"""FOOTER-2 — OSS boundary guard for shipped `src/` Python.

`src/` ships PUBLIC, but `docs/build/`, `docs/launch/`, and `docs/marketing/` are EXCLUDED from
the public mirror (`sync-public.sh`). So a shipped `.py` file that names an internal-doc path in a
docstring or comment leaves OSS users with a dangling reference to a doc they'll never have.

This mirrors the SKILL.md boundary guard (`TestNoShippedSkillLeaksAnInternalDocPath`) for the code
side: no shipped `src/**/*.py` may reference an internal `docs/(build|launch|marketing)` path. The
one validator-owned exemption is docsync's `_INTERNAL_DOC_DIRS` constant — it must NAME those dirs
because it IS the exclusion list docsync uses to skip them; that is not a dangling reference.

Pure-source (imports via `_support`); walks the tree on disk, so it runs on any interpreter.
"""

import os
import unittest

import _support  # noqa: F401  (puts src/ on the path)

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_SRC = os.path.join(_REPO, "src", "mokata")

_INTERNAL_DOC_TOKENS = ("docs/build", "docs/launch", "docs/marketing")


def scan_py_for_internal_doc_refs(text):
    """Every line of `text` that names an internal-doc tree -> [(lineno, token, stripped_line)]."""
    hits = []
    for i, line in enumerate(text.splitlines(), 1):
        for tok in _INTERNAL_DOC_TOKENS:
            if tok in line:
                hits.append((i, tok, line.strip()))
    return hits


def _is_validator_exempt(rel_path, line):
    """The ONLY exemption, owned HERE (never in the scanned file): docsync's exclusion-list
    constant, which must name the internal dirs because it is the list used to SKIP them."""
    return rel_path == os.path.join("mokata", "docsync.py") and "_INTERNAL_DOC_DIRS" in line


def _iter_shipped_py():
    for dirpath, _dirs, files in os.walk(_SRC):
        for fn in files:
            if fn.endswith(".py"):
                full = os.path.join(dirpath, fn)
                yield os.path.relpath(full, os.path.join(_REPO, "src")), full


class TestNoShippedSourceLeaksAnInternalDocPath(unittest.TestCase):
    def test_no_shipped_py_references_an_internal_doc_path(self):
        offenders = []
        for rel, full in _iter_shipped_py():
            with open(full, encoding="utf-8") as fh:
                text = fh.read()
            for lineno, tok, line in scan_py_for_internal_doc_refs(text):
                if _is_validator_exempt(rel, line):
                    continue
                offenders.append(f"{rel}:{lineno} references internal path '{tok}': {line}")
        self.assertEqual(
            offenders, [],
            "shipped src/ .py must not reference an internal docs/(build|launch|marketing) path "
            "(the mirror excludes it → dangling reference for OSS users):\n" + "\n".join(offenders))

    def test_the_scanner_catches_a_planted_leak(self):
        # A planted internal-doc reference must be detected — proves the guard has teeth.
        planted = (
            '"""A module docstring.\n\n'
            "See docs/build/99-mokata-secret-plan.md for the internal design.\n"
            '"""\n')
        hits = scan_py_for_internal_doc_refs(planted)
        self.assertTrue(hits, "scanner missed a planted internal-doc reference")
        self.assertEqual(hits[0][1], "docs/build")
        self.assertIn("99-mokata-secret-plan.md", hits[0][2])

    def test_exemption_is_narrow_and_still_flags_a_real_leak_in_docsync(self):
        # The docsync exemption is line-scoped to the constant: any OTHER docsync line naming an
        # internal path (a planted docstring leak) is still an offender.
        rel = os.path.join("mokata", "docsync.py")
        self.assertTrue(_is_validator_exempt(
            rel, '_INTERNAL_DOC_DIRS = ("docs/build", "docs/launch", "docs/marketing")'))
        self.assertFalse(_is_validator_exempt(
            rel, "    # see docs/build/76-mokata-SK.S1-gate-integrity-map.md"))


if __name__ == "__main__":
    unittest.main()
