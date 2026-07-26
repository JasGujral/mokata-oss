"""PIN-DRIFT (0.0.15) — the PR-check action pins join release-check's guarded set.

Doc 84 §1: the `mokata-check@vX.Y.Z` pins in the how-to + the example workflow used to be
bumped BY HAND at each doc gate (0.0.14: `916c65f`), and one gate missed them. After this
stage they are `_VERSION_FIELDS` entries like any other version-bearing location: the
release check FAILS (naming the file and the stale pin) when a pin ≠ the version being cut.

Verify-only, matching the rest of the machinery — `release.sh` never bumps, it REFUSES to
tag (scripts/release.sh:46 `verify_versions`).

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import unittest

from _support import sample_manifest_data  # noqa: F401  (path-fix side-effect)

from mokata import __version__
from mokata.packaging import (
    ACTION_PIN_PATHS,
    ACTION_PIN_RE,
    check_release_consistency,
    read_version_fields,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PIN_DOC = "docs/how-to/mokata-as-a-pr-check.md"
PIN_YML = ".github/actions/mokata-check/example-pr-check.yml"

# Guarded-set field names for the two pins (the `path:selector` shape).
PIN_FIELDS = (f"{PIN_DOC}:action-pin", f"{PIN_YML}:action-pin")


def _pin_text(version: str) -> str:
    return (
        "      - id: check\n"
        f"        uses: JasGujral/mokata-oss/.github/actions/mokata-check@v{version}\n"
        "        with:\n"
        "          base: ${{ github.event.pull_request.base.sha }}\n"
    )


def _make_repo(tmp, *, version="0.0.4", doc_pin=None, yml_pin=None, omit=()):
    """A minimal repo carrying every guarded field, with the two action pins independently
    settable (default: equal to `version`) so a planted pin drift can be asserted NAMED."""
    doc_pin = version if doc_pin is None else doc_pin
    yml_pin = version if yml_pin is None else yml_pin
    os.makedirs(os.path.join(tmp, ".claude-plugin"), exist_ok=True)
    os.makedirs(os.path.join(tmp, "src", "mokata"), exist_ok=True)
    os.makedirs(os.path.join(tmp, os.path.dirname(PIN_DOC)), exist_ok=True)
    os.makedirs(os.path.join(tmp, os.path.dirname(PIN_YML)), exist_ok=True)
    import json

    with open(os.path.join(tmp, "pyproject.toml"), "w", encoding="utf-8") as fh:
        fh.write(f'[project]\nname = "mokata"\nversion = "{version}"\n')
    with open(os.path.join(tmp, ".claude-plugin", "plugin.json"), "w", encoding="utf-8") as fh:
        json.dump({"name": "mokata", "version": version}, fh)
    with open(os.path.join(tmp, ".claude-plugin", "marketplace.json"), "w", encoding="utf-8") as fh:
        json.dump({"name": "mostack", "metadata": {"version": version},
                   "plugins": [{"name": "mokata", "source": ".", "version": version}]}, fh)
    with open(os.path.join(tmp, "src", "mokata", "__init__.py"), "w", encoding="utf-8") as fh:
        fh.write(f'__version__ = "{version}"\n')
    if "doc" not in omit:
        with open(os.path.join(tmp, PIN_DOC), "w", encoding="utf-8") as fh:
            fh.write("# Use mokata as a PR check\n\n```yaml\n" + _pin_text(doc_pin) + "```\n")
    if "yml" not in omit:
        with open(os.path.join(tmp, PIN_YML), "w", encoding="utf-8") as fh:
            fh.write("name: mokata check\njobs:\n  check:\n    steps:\n" + _pin_text(yml_pin))
    return tmp


class _TmpMixin(unittest.TestCase):
    def tmp(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        return d


class TestPinDriftSelector(_TmpMixin):
    """The new `action-pin` selector kind reads an `@vX.Y.Z` pin out of arbitrary text."""

    def test_pin_drift_selector_reads(self):
        tmp = _make_repo(self.tmp(), version="0.0.4")
        fields = read_version_fields(tmp)
        for name in PIN_FIELDS:
            self.assertIn(name, fields, f"{name} is not in the guarded set")
            self.assertEqual(fields[name], "0.0.4", name)

    def test_pin_drift_mismatch_fails(self):
        # The exact drift doc 84 §1 describes: version bumped, the doc's pin left behind.
        tmp = _make_repo(self.tmp(), version="0.0.15", doc_pin="0.0.14")
        res = check_release_consistency("0.0.15", root=tmp)
        self.assertFalse(res.consistent, res.render())
        self.assertEqual([n for n, _ in res.mismatches], [f"{PIN_DOC}:action-pin"])
        report = res.render()
        self.assertIn(PIN_DOC, report)          # names the FILE
        self.assertIn("0.0.14", report)         # names the STALE PIN
        offenders = [ln for ln in report.splitlines() if "offenders" in ln][0]
        self.assertIn(PIN_DOC, offenders)
        self.assertNotIn(PIN_YML, offenders)    # the in-step pin is not an offender

    def test_pin_drift_match_passes(self):
        tmp = _make_repo(self.tmp(), version="0.0.15")
        self.assertTrue(check_release_consistency("v0.0.15", root=tmp).consistent)

    def test_pin_drift_both_pins_stale_are_both_named(self):
        tmp = _make_repo(self.tmp(), version="0.0.15", doc_pin="0.0.14", yml_pin="0.0.14")
        res = check_release_consistency("0.0.15", root=tmp)
        self.assertEqual(sorted(n for n, _ in res.mismatches), sorted(PIN_FIELDS))

    def test_pin_drift_missing_file_is_a_named_mismatch_not_a_crash(self):
        tmp = _make_repo(self.tmp(), version="0.0.4", omit=("yml",))
        res = check_release_consistency("0.0.4", root=tmp)   # must not raise
        self.assertFalse(res.consistent)
        self.assertIn(PIN_YML, res.render())
        self.assertIn((f"{PIN_YML}:action-pin", None), res.mismatches)

    def test_pin_drift_disagreeing_pins_in_one_file_fail_closed(self):
        # Two different pins in the SAME file can never both equal the tag — fail, don't
        # silently report the first one.
        tmp = _make_repo(self.tmp(), version="0.0.15")
        with open(os.path.join(tmp, PIN_DOC), "a", encoding="utf-8") as fh:
            fh.write("\n```yaml\n" + _pin_text("0.0.9") + "```\n")
        res = check_release_consistency("0.0.15", root=tmp)
        self.assertFalse(res.consistent, res.render())
        self.assertIn(f"{PIN_DOC}:action-pin", [n for n, _ in res.mismatches])


class TestPinDriftCoverageSweep(unittest.TestCase):
    """R-MAN-register pattern: every pin in the tree must be COVERED by a guarded entry, so
    a future doc adding a third pin unguarded fails CI instead of drifting silently."""

    def _sweep(self):
        """Every repo-relative file containing a `mokata-check@v` pin. `docs/build/` is
        excluded by its provenance rule — historical pins there are correct history — and
        `tests/` because pins there are synthetic fixtures (this module builds them), not
        published references a release has to keep in step."""
        skip_dirs = {".git", "docs/build", "tests", ".venv", "venv", "node_modules",
                     "__pycache__", "site", "dist", "build", ".mypy_cache",
                     ".pytest_cache", ".ruff_cache"}
        found = []
        for dirpath, dirnames, filenames in os.walk(ROOT):
            rel_dir = os.path.relpath(dirpath, ROOT)
            rel_dir = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")
            dirnames[:] = [
                d for d in dirnames
                if d not in skip_dirs
                and f"{rel_dir}/{d}".lstrip("/") not in skip_dirs
            ]
            for fn in filenames:
                rel = os.path.join(rel_dir, fn).replace(os.sep, "/").lstrip("/")
                try:
                    with open(os.path.join(dirpath, fn), encoding="utf-8") as fh:
                        text = fh.read()
                except (OSError, UnicodeDecodeError):
                    continue
                if ACTION_PIN_RE.search(text):
                    found.append(rel)
        return sorted(found)

    def test_pin_drift_all_pins_guarded(self):
        found = self._sweep()
        self.assertTrue(found, "the sweep found no action pins at all — the regex is wrong")
        uncovered = [p for p in found if p not in set(ACTION_PIN_PATHS)]
        self.assertEqual(
            uncovered, [],
            "action pin(s) not in release-check's guarded set — add them to "
            f"packaging.ACTION_PIN_PATHS: {uncovered}",
        )

    def test_pin_drift_guarded_paths_all_exist(self):
        # The converse: a guarded path that no longer carries a pin is dead guard weight.
        for rel in ACTION_PIN_PATHS:
            path = os.path.join(ROOT, rel)
            self.assertTrue(os.path.exists(path), f"guarded pin path missing: {rel}")
            with open(path, encoding="utf-8") as fh:
                self.assertRegex(fh.read(), ACTION_PIN_RE, rel)


class TestPinDriftAgainstTheRealTree(unittest.TestCase):
    """The live invariant: the committed tree is self-consistent, pins included."""

    def test_release_check_is_green_on_the_current_tree(self):
        res = check_release_consistency(__version__, root=ROOT)
        self.assertTrue(res.consistent, res.render())

    def test_real_tree_pins_are_read_and_equal_the_package_version(self):
        fields = read_version_fields(ROOT)
        for name in PIN_FIELDS:
            self.assertEqual(fields[name], __version__, name)

    def test_pin_regex_matches_the_canonical_reference_shape(self):
        m = ACTION_PIN_RE.search(
            "uses: JasGujral/mokata-oss/.github/actions/mokata-check@v1.22.333\n")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "1.22.333")
        self.assertIsNone(ACTION_PIN_RE.search("uses: actions/checkout@v4"))
        self.assertIsNone(ACTION_PIN_RE.search("mokata-check@main"))
        self.assertIsInstance(ACTION_PIN_RE, re.Pattern)


if __name__ == "__main__":
    unittest.main()
