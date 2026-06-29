"""Stage 46 — secret-guard robustness battery (I1 + the PreToolUse hook).

The guard shipped two precision bugs (0.0.2 envelope, 0.0.3 entropy/path). This is the
proof it can't bite a third time WITHOUT loosening real-secret detection:

  - TRUE_POSITIVES — every named credential format hard-blocks.
  - FALSE_POSITIVES — paths/URLs/UUIDs/SHAs/identifiers/semver/digests/lockfile hashes pass.
  - a seeded PROPERTY/FUZZ test asserts the invariant:
        * random paths / URLs / identifiers / hex digests NEVER entropy-block;
        * random CONTIGUOUS rich-alphabet (mixed-case + digits, no path separators)
          high-entropy strings ALWAYS block.
  - the PreToolUse hook scans only `tool_input` (Write/Edit/MultiEdit/Bash/NotebookEdit +
    the target path), NEVER the envelope metadata.

Real-secret (and high-entropy metadata) literals are ASSEMBLED from sub-20-char fragments
at runtime, so this source file itself carries no blockable literal (the guard scans
writes). Benign strings are real literals — they pass the guard, which is the point.
Dependency-free, deterministic.
"""

import json
import os
import random
import string
import subprocess
import sys
import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.govern.secrets import has_secrets, scan

HOOK = os.path.join(os.path.dirname(__file__), "..", "hooks", "secret_guard.py")


def j(*parts):
    """Join fragments — the runtime secret; the source only holds the (benign) fragments."""
    return "".join(parts)


# Each value is built from fragments so no source line is itself a blockable literal.
TRUE_POSITIVES = {
    "aws-access-key":   j("AKIA", "IOSFODNN7", "EXAMPLE"),
    "aws-secret (assign)": "aws_secret_access_key = '" + j("wJalrXUtnF", "EMIK7MDENG",
                                                            "bPxRfiCYEX") + "'",
    "gcp-api-key":      j("AIza", "SyDaGkq3mN", "pL7wRtVbGc", "HdEf1JkMnO", "pXyZ3"),
    "azure-storage":    "AccountKey=" + j("AbCdEf1234", "GhIjKl5678", "MnOpQrStUv",
                                          "WxYz012345") + "==",
    "github-classic":   j("ghp_", "016c1f2e3a", "4b5d6e7f8a", "9b0c1d2e3f"),
    "github-pat":       j("github_pat_", "11ABCDE0a0", "AbCdEfGhIj", "KlMnOpQrSt"),
    "gitlab":           j("glpat-", "AbCdEf1234", "GhIjKl5678", "MnOpQr"),
    "slack":            j("xoxb-", "1234567890", "1234567890", "abcdefABCD"),
    "stripe":           j("sk_live_", "4eC39HqLyj", "WDarjtT1zd", "p7dc"),
    "sendgrid":         j("SG.", "abcdefghij", "klmnop", ".", "qrstuvwxyz",
                          "ABCDEFGHIJ", "KLMNOPqrst", "uvwxyz1234"),
    "openai":           j("sk-", "abcdefghij", "klmnopqrst", "uvwxyz0123"),
    "jwt":              j("eyJ", "abc123ABC4", ".", "eyJ", "def456DEF7", ".",
                          "sigABC123x", "yzDEF"),
    "npm":              j("npm_", "abcdefghij", "klmnopqrst", "uvwxyz0123", "ABCDEF"),
    "pypi":             j("pypi-", "AgEIcHlwaS", "5vcmc6abcd", "efghij"),
    "private-key-pem":  j("-----BEGIN ", "RSA PRIVATE ", "KEY-----"),
    "openssh-key-pem":  j("-----BEGIN ", "OPENSSH PRIVATE ", "KEY-----"),
    "postgres-dsn":     j("postgres", "ql://user:", "p4ssw0rd@", "host:5432/db"),
    "secret-assign":    "api_key = \"" + j("Xy9KqWmZ3b", "PnL7vTsRdG") + "\"",
}

# Real literals — these MUST pass (no finding). Pure-hex digests, identifiers, paths, URLs.
FALSE_POSITIVES = [
    "docs/build/02-mokata-build-status.md",
    "/Users/x/Documents/Development/claude/cowork/mokata/src/mokata/govern/secrets.py",
    "https://github.com/JasGujral/mokata-oss/releases/tag/v0.0.4",
    "src/mokata/execmode/orchestrator.py",
    "550e8400-e29b-41d4-a716-446655440000",                      # UUID
    "a1b2c3d",                                                    # short git SHA (7 hex)
    "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0",                  # full git SHA (40 hex)
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",  # sha256 (64 hex)
    "sha512-" + "Q" * 86 + "==",                                 # npm/SRI lockfile hash
    "my_long_snake_case_identifier_here",
    "this-is-a-very-long-kebab-case-string-here",
    "1.2.3-beta.4",                                              # semver
    "data:image/png;base64,iVBORw0KGgo=",                       # trivial data URI
    "the quick brown fox jumps over the lazy dog",
]


class TestTruePositives(unittest.TestCase):
    def test_every_named_secret_hard_blocks(self):
        for label, secret in TRUE_POSITIVES.items():
            with self.subTest(secret=label):
                self.assertTrue(has_secrets(scan(text=secret)),
                                f"MISSED a real secret: {label}")

    def test_corpus_size(self):
        self.assertGreaterEqual(len(TRUE_POSITIVES), 15)


class TestFalsePositives(unittest.TestCase):
    def test_benign_strings_never_flag(self):
        for benign in FALSE_POSITIVES:
            with self.subTest(value=benign):
                self.assertFalse(has_secrets(scan(text=benign)),
                                 f"FALSE POSITIVE on: {benign!r}")

    def test_corpus_size(self):
        self.assertGreaterEqual(len(FALSE_POSITIVES), 12)


class TestEntropyFuzzInvariant(unittest.TestCase):
    """Seeded + deterministic (no real randomness leaks into CI). The invariant:
    path/URL/identifier/hex shapes never entropy-block; contiguous rich-alphabet
    high-entropy runs (no path separators) always do."""

    def _word(self, rng, lo=1, hi=12):
        n = rng.randint(lo, hi)
        return "".join(rng.choice(string.ascii_lowercase + string.digits)
                       for _ in range(n))

    def _entropy(self, s):
        return any(f.layer == "entropy" for f in scan(text=s))

    def test_benign_shapes_never_entropy_block(self):
        rng = random.Random(0xC0FFEE)
        for _ in range(400):
            kind = rng.randrange(4)
            if kind == 0:                                   # path (with digits)
                s = "/".join(self._word(rng) for _ in range(rng.randint(2, 6))) \
                    + "." + rng.choice(("py", "md", "txt", "json"))
            elif kind == 1:                                 # url
                s = "https://" + self._word(rng) + ".com/" + \
                    "/".join(self._word(rng) for _ in range(rng.randint(1, 4)))
            elif kind == 2:                                 # snake/kebab identifier
                sep = rng.choice(("_", "-"))
                s = sep.join(self._word(rng) for _ in range(rng.randint(3, 8)))
            else:                                           # hex digest (git sha / hash)
                s = "".join(rng.choice("0123456789abcdef")
                            for _ in range(rng.choice((7, 40, 64))))
            self.assertFalse(self._entropy(s), f"false-positive entropy on {s!r}")

    def test_secret_shaped_strings_always_block(self):
        rng = random.Random(0x5EC2E7)
        nonhex = "ghijklmnopqrstuvwxyzGHIJKLMNOPQRSTUVWXYZ"
        alpha = string.ascii_letters + string.digits        # no path separators
        for _ in range(400):
            n = rng.randint(28, 44)
            # Guarantee rich + non-pure-hex: a non-hex letter, a digit, an uppercase.
            chars = [rng.choice(nonhex), rng.choice(string.digits),
                     rng.choice(string.ascii_uppercase)]
            chars += [rng.choice(alpha) for _ in range(n - 3)]
            rng.shuffle(chars)
            s = "".join(chars)
            self.assertTrue(self._entropy(s), f"missed secret-shaped {s!r}")


class TestHookToolShapes(unittest.TestCase):
    """The PreToolUse hook scans only tool_input across every Claude Code tool shape, and
    never the envelope metadata. Secrets + the high-entropy metadata are built at runtime."""

    # A high-entropy transcript path segment, built from fragments so this source file
    # carries no blockable literal (it simulates the metadata that must NOT be scanned).
    _META = {
        "session_id": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",   # 32-hex — a digest, not a secret
        "transcript_path": "/Users/u/.claude/projects/"
                           + j("xY9kZ2mQ7p", "L4nR8vT3wB", "6cF1dG5hJ0") + "/t.jsonl",
        "cwd": "/Users/u/dev",
        "hook_event_name": "PreToolUse",
    }

    def _run(self, payload):
        return subprocess.run([sys.executable, HOOK], input=payload,
                              capture_output=True, text=True).returncode

    def _envelope(self, tool_name, tool_input):
        return json.dumps({**self._META, "tool_name": tool_name,
                           "tool_input": tool_input})

    _SECRET = j("AKIA", "IOSFODNN7", "EXAMPLE")

    def test_write_content_blocks(self):
        env = self._envelope("Write", {"file_path": "a.txt",
                                        "content": "key " + self._SECRET})
        self.assertEqual(self._run(env), 2)

    def test_edit_new_string_blocks(self):
        env = self._envelope("Edit", {"file_path": "a.py", "old_string": "x",
                                       "new_string": "tok=" + self._SECRET})
        self.assertEqual(self._run(env), 2)

    def test_multiedit_nested_edits_block(self):
        env = self._envelope("MultiEdit", {"file_path": "a.py", "edits": [
            {"old_string": "a", "new_string": "b"},
            {"old_string": "c", "new_string": "k=" + self._SECRET}]})
        self.assertEqual(self._run(env), 2)

    def test_bash_command_blocks(self):
        env = self._envelope("Bash", {"command": "export K=" + self._SECRET})
        self.assertEqual(self._run(env), 2)

    def test_notebook_edit_new_source_blocks(self):
        env = self._envelope("NotebookEdit", {"notebook_path": "n.ipynb",
                                               "new_source": "k = '" + self._SECRET + "'"})
        self.assertEqual(self._run(env), 2)

    def test_sensitive_target_path_blocks_even_clean_content(self):
        env = self._envelope("Write", {"file_path": "/repo/.env",
                                        "content": "PORT=8080"})
        self.assertEqual(self._run(env), 2)

    def test_clean_tool_call_with_high_entropy_metadata_passes(self):
        env = self._envelope("Edit", {"file_path": "foo.py",
                                       "old_string": "a", "new_string": "b"})
        self.assertEqual(self._run(env), 0)

    def test_clean_notebook_edit_passes(self):
        env = self._envelope("NotebookEdit", {"notebook_path": "n.ipynb",
                                               "new_source": "print('hello world')"})
        self.assertEqual(self._run(env), 0)


if __name__ == "__main__":
    unittest.main()
