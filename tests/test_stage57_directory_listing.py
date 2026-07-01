"""Stage 57 — the wow demo + the single-source plugin-directory listing flag.

The "pending directory approval" notice lives in several docs; `scripts/directory_listing.py` is
the ONE source that owns it (a flag + the two notice texts + marker-delimited regions). The
directory APPROVAL itself is external (a Claude/GitHub action), so this test proves:

  * we DON'T claim the listing is live while the flag is False;
  * every target doc is IN SYNC with the flag (the drift guard — a notice can't go stale);
  * flipping the flag flips cleanly and reversibly (apply True → False restores the docs);
  * the wow-demo tutorial exists, is in the nav, and carries the real demo + screencast script.
"""

import os
import shutil
import sys
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import directory_listing as DL  # noqa: E402


class TestNotSilentlyClaimingLive(unittest.TestCase):
    def test_flag_is_false_until_the_user_flips_it(self):
        # We must NOT ship docs claiming the listing is live while approval is pending.
        self.assertFalse(DL.LISTED, "LISTED is True — only the user flips it after approval")

    def test_pending_text_says_pending_not_live(self):
        pending = DL.notice(False)
        self.assertIn("Pending", pending)
        self.assertIn("yet", pending)
        self.assertNotIn("✅", pending)
        self.assertNotIn("is in the Claude plugin directory", pending)

    def test_live_text_says_live_not_pending(self):
        live = DL.notice(True)
        self.assertIn("✅", live)
        self.assertIn("is in the Claude plugin directory", live)
        self.assertNotIn("Pending", live)
        self.assertNotIn("yet", live)


class TestDriftGuard(unittest.TestCase):
    def test_every_target_is_in_sync_with_the_flag(self):
        bad = DL.check()                       # uses the real repo + the real LISTED flag
        self.assertEqual(bad, [], f"directory-listing notice drifted in: {bad}")

    def test_every_target_actually_carries_the_marked_region(self):
        for rel in DL.TARGETS:
            with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
                text = fh.read()
            self.assertIn(DL.START, text, f"{rel} is missing the directory-listing markers")
            self.assertIn(DL.END, text)


def _slurp(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class TestFlipIsCleanAndReversible(unittest.TestCase):
    def _temp_repo(self, d):
        """Copy the target docs into a temp root, preserving their relative paths."""
        for rel in DL.TARGETS:
            dst = os.path.join(d, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(os.path.join(ROOT, rel), dst)

    def test_apply_live_then_pending_round_trips(self):
        with tempfile.TemporaryDirectory() as d:
            self._temp_repo(d)
            originals = {rel: _slurp(os.path.join(d, rel)) for rel in DL.TARGETS}

            DL.apply(True, root=d)
            self.assertEqual(DL.check(True, root=d), [])          # all flipped to live
            for rel in DL.TARGETS:
                self.assertIn("✅", _slurp(os.path.join(d, rel)))

            DL.apply(False, root=d)
            self.assertEqual(DL.check(False, root=d), [])         # all back to pending
            for rel in DL.TARGETS:                                # byte-identical restore
                self.assertEqual(_slurp(os.path.join(d, rel)), originals[rel],
                                 f"{rel} did not restore cleanly")

    def test_apply_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            self._temp_repo(d)
            DL.apply(True, root=d)
            self.assertEqual(DL.apply(True, root=d), [], "a second apply rewrote files (not idempotent)")


class TestWowDemo(unittest.TestCase):
    def _read(self, rel):
        with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
            return fh.read()

    def test_demo_page_exists_with_the_real_blocks_and_screencast(self):
        demo = self._read("docs/tutorials/catches-a-bad-change.md")
        self.assertIn("mokata run develop", demo)
        self.assertIn("[BLOCKED] spec-persisted", demo)          # real output of beat 1
        self.assertIn("status: blocked", demo)                   # real output of beat 2
        self.assertIn("audit ledger — 2 entries", demo)          # the audit punchline
        self.assertIn("screencast script", demo.lower())         # the 60-second shot list

    def test_demo_is_registered_in_the_nav(self):
        nav = self._read("mkdocs.yml")
        self.assertIn("tutorials/catches-a-bad-change.md", nav)

    def test_readme_leads_with_the_pitch_and_links_the_demo(self):
        readme = self._read("README.md")
        self.assertIn("memory + seatbelt for your AI coding agent", readme)
        self.assertIn("docs/tutorials/catches-a-bad-change.md", readme)


if __name__ == "__main__":
    unittest.main()
