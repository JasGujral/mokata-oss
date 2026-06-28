"""G4 + I1 — the shipped sync SECURITY hook: blocks (exit 2) when a secret is present,
passes (exit 0) otherwise."""

import os
import subprocess
import sys
import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

HOOK = os.path.join(os.path.dirname(__file__), "..", "hooks", "secret_guard.py")


class TestSecretGuardHook(unittest.TestCase):
    def test_blocks_with_exit_2_on_secret(self):
        proc = subprocess.run(
            [sys.executable, HOOK, "--text", "KEY='AKIAIOSFODNN7EXAMPLE'"],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)   # sync security hook BLOCKS

    def test_passes_with_exit_0_on_clean_content(self):
        proc = subprocess.run(
            [sys.executable, HOOK, "--text", "just some ordinary code"],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)

    # --- PreToolUse envelope path (regression: the envelope metadata is high-entropy by
    #     nature and must NOT be scanned, or the guard blocks every Claude Code tool call) ---

    _ENVELOPE_META = (
        '"session_id":"a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",'
        '"transcript_path":"/Users/u/.claude/projects/xY9kZ2mQ7pL4nR8vT3wB6cF1dG5hJ0aS/abc123.jsonl",'
        '"cwd":"/Users/u/dev","hook_event_name":"PreToolUse"'
    )

    def _run_stdin(self, payload):
        return subprocess.run([sys.executable, HOOK], input=payload,
                              capture_output=True, text=True).returncode

    def test_clean_envelope_with_high_entropy_metadata_passes(self):
        # session_id + transcript_path are high-entropy; scanning them would false-positive.
        payload = ('{' + self._ENVELOPE_META +
                   ',"tool_name":"Edit","tool_input":{"file_path":"foo.py",'
                   '"old_string":"a","new_string":"b"}}')
        self.assertEqual(self._run_stdin(payload), 0)   # must NOT block

    def test_secret_in_tool_input_command_still_blocks(self):
        payload = ('{' + self._ENVELOPE_META +
                   ',"tool_name":"Bash","tool_input":{"command":'
                   '"export KEY=AKIAIOSFODNN7EXAMPLE"}}')
        self.assertEqual(self._run_stdin(payload), 2)   # real secret in content blocks

    def test_secret_in_tool_input_content_still_blocks(self):
        payload = ('{' + self._ENVELOPE_META +
                   ',"tool_name":"Write","tool_input":{"file_path":"a.txt",'
                   '"content":"token AKIAIOSFODNN7EXAMPLE here"}}')
        self.assertEqual(self._run_stdin(payload), 2)

    def test_raw_non_envelope_stdin_still_scanned(self):
        # backward-compat: non-JSON stdin is scanned as raw text.
        self.assertEqual(self._run_stdin("here is AKIAIOSFODNN7EXAMPLE raw"), 2)


if __name__ == "__main__":
    unittest.main()
