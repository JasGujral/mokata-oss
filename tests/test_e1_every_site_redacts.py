"""0.0.28 E1 (REDACT-MANGLED-KEY) — every site that sends or keeps text redacts a key WHOLE.

The user-visible rule: a private key — strict, or with a damaged marker, or with no marker at all —
is replaced whole in every text mokata sends out, every replacement is counted, and every
whole-text gate refuses it. This module is the "every family" half of the matrix (note §5):

  * line sites (replace and count): `context.build_item` file / selection / diff,
    `usages.copy_context` full + summary, `usages.scan_spill`, `ground.redact` (via the one
    redactor `usages.redact_text`) — `assert_line_sites` runs a text through all of them;
  * whole-text sites (block): one business test per egress family — the WriteGate, the hook (Write,
    Edit, Bash), session push, memory export, events share, audit share, vault push, the explain
    bundle and CI evidence.

Fixtures are built at run time from character codes (`_e1_keys`): this repo's own secret-guard
hook would block a literal marker in this file.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import _support  # noqa: F401

import _e1_keys as K

from mokata.explain import ground as G
from mokata.govern.secrets import KEY_FIRM, KEY_TOLERANT, scan
from mokata.ide import context as CX
from mokata.ide import usages as U

HOOK = os.path.join(os.path.dirname(__file__), "..", "src", "mokata", "hooks", "secret_guard.py")
KEY_KINDS = (KEY_FIRM, KEY_TOLERANT)


NL = chr(10)


def _tmp(test, prefix="mk-e1-"):
    d = os.path.realpath(tempfile.mkdtemp(prefix=prefix))
    test.addCleanup(shutil.rmtree, d, True)
    return d


def _put(d, rel, text, newline=""):
    p = os.path.join(d, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline=newline) as fh:
        fh.write(text)
    return p


def _git(d, *args):
    subprocess.run(["git", "-C", d, "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   check=True, capture_output=True, stdin=subprocess.DEVNULL)


def key_kinds(text, **kw):
    return [f.kind for f in scan(text=text, **kw) if f.kind in KEY_KINDS]


class LineSites(unittest.TestCase):
    """A text through every line site. `secrets` = the strings that must not survive anywhere."""

    def leaked(self, text, secrets):
        return [s for s in secrets if s and s in text]

    def assert_line_sites(self, text, secrets, name, *, copy=True, file=True):
        # the KEY WALKER alone: a key must be held back by the one detector, never only because
        # the entropy layer happened to flag a wide random body line (that masks a walker miss)
        walked = "".join(U.pem_lines(text.splitlines(keepends=True), {}, "src/x.txt"))
        self.assertEqual([], self.leaked(walked, secrets), f"{name}: the key walker alone leaked")
        # the one redactor, and the explainer's grounding on top of it
        out, n = U.redact_text(text, "src/x.txt", mark=CX.REDACTED)
        self.assertEqual([], self.leaked(out, secrets), f"{name}: redact_text leaked")
        self.assertGreater(n, 0, f"{name}: redact_text counted nothing")
        self.assertEqual(len(text.splitlines()), len(out.splitlines()), f"{name}: line count")
        gout, gn = G.redact(text)
        self.assertEqual([], self.leaked(gout, secrets), f"{name}: ground leaked")
        self.assertEqual((out, n), (gout.replace(CX.REDACTED.split(":")[0], CX.REDACTED.split(":")[0]),
                                    gn), f"{name}: ground and context disagree")
        if not file:
            return
        d = _tmp(self)
        _put(d, "src/k.txt", text)
        item = CX.build_item(d, "file", "src/k.txt")
        self.assertEqual([], self.leaked(item["text"], secrets), f"{name}: build_item file")
        self.assertGreater(item["redacted"], 0, f"{name}: build_item counted nothing")
        # scan_spill: the copy path's assembler
        src = _put(d, "raw.txt", text)
        w = U.SpillWriter(d, "full")
        got = U.scan_spill(src, w, "src/k.txt")
        with open(w.path, encoding="utf-8", newline="") as fh:
            spilled = fh.read()
        w.discard()
        self.assertEqual([], self.leaked(spilled, secrets), f"{name}: scan_spill leaked")
        self.assertGreater(got["replaced"], 0, f"{name}: scan_spill counted nothing")
        if copy:
            n_lines = text.count("\n") + (0 if text.endswith("\n") else 1)
            meta, files, _sizes = U.copy_context(d, d, "src/k.txt", 1, n_lines, d)
            try:
                for shape in ("full", "summary"):
                    with open(files[shape], encoding="utf-8") as fh:
                        body = fh.read()
                    self.assertEqual([], self.leaked(body, secrets), f"{name}: copy {shape}")
                    self.assertGreater(meta["shapes"][shape]["replaced"], 0, f"{name}: {shape}")
            finally:
                for p in files.values():
                    try:
                        os.remove(p)
                    except OSError:
                        pass


def mangled_key(width=16):
    """A key whose markers are dash-less (E-Q3 upper-case), body random (marked key)."""
    begin = K.marker(K.BEGIN, K.RSA, lead="", trail="")
    end = K.marker(K.END, K.RSA, lead="", trail="")
    body = K.random_body(width, 12, "mangled")
    return begin, body, end


class EveryLineSiteRedactsAMangledKey(LineSites):

    def test_a_dashless_key_in_every_line_site(self):
        begin, body, end = mangled_key()
        text = "a = 1\n" + begin + "\n" + "\n".join(body) + "\n" + end + "\nb = 2\n"
        self.assert_line_sites(text, body + [begin, end], "dashless")

    def test_an_unmarked_key_in_every_line_site(self):
        body = K.b64_lines(K.KEY_BODIES["rsa2048"], 64)
        text = "a = 1\n" + "\n".join(body) + "\nb = 2\n"
        self.assert_line_sites(text, body, "unmarked")

    def test_a_selection_that_starts_inside_a_mangled_key(self):
        begin, body, end = mangled_key(24)
        text = "x = 0\n" + begin + "\n" + "\n".join(body) + "\n" + end + "\ny = 1\n"
        d = _tmp(self)
        _put(d, "src/s.txt", text)
        for lo in range(3, 3 + len(body)):
            item = CX.build_item(d, "selection", "src/s.txt", f"{lo}-{lo + 2}")
            self.assertEqual([], self.leaked(item["text"], body), lo)
            self.assertGreater(item["redacted"], 0, lo)
            self.assertTrue(U.pem_state_before(d, "src/s.txt", lo), lo)

    def test_a_selection_that_starts_inside_an_unmarked_key(self):
        body = K.b64_lines(K.KEY_BODIES["ec-P-256"], 16)
        text = "x = 0\n" + "\n".join(body) + "\ny = 1\n"
        d = _tmp(self)
        _put(d, "src/u.txt", text)
        for lo in range(4, 1 + len(body)):          # the run's head (its DER) is above
            item = CX.build_item(d, "selection", "src/u.txt", f"{lo}-{lo + 1}")
            self.assertEqual([], self.leaked(item["text"], body[lo - 2:lo]), lo)
            self.assertGreater(item["redacted"], 0, lo)


@unittest.skipUnless(shutil.which("git"), "git is needed for a live diff")
class EveryDiffSideRedactsAMangledKey(LineSites):

    def test_added_removed_and_middle_hunks(self):
        begin, body, end = mangled_key(16)
        d = _tmp(self)
        _git(d, "init", "-q")
        keyed = "a = 1\nK = '''\n" + begin + "\n" + "\n".join(body) + "\n" + end + "\n'''\nb = 2\n"
        # removed key
        _put(d, "src/k.py", keyed)
        _git(d, "add", "-A")
        _git(d, "commit", "-qm", "k")
        _put(d, "src/k.py", "a = 1\nb = 2\n")
        item = CX.build_item(d, "diff", "src/k.py")
        self.assertEqual([], self.leaked(item["text"], body), "removed key")
        self.assertGreaterEqual(item["redacted"], len(body))
        # a hunk in the middle of the key (its markers outside the hunk)
        mid = list(body)
        mid[5] = K.random_body(16, 1, "changed")[0]
        _put(d, "src/k.py", keyed.replace(body[5], mid[5]))
        item = CX.build_item(d, "diff", "src/k.py")
        self.assertEqual([], self.leaked(item["text"], body + mid), "middle hunk")
        self.assertGreater(item["redacted"], 0)
        # an added unmarked key
        ub = K.b64_lines(K.KEY_BODIES["ed25519"], 16)
        _put(d, "src/k.py", keyed + "\n".join(ub) + "\n")
        item = CX.build_item(d, "diff", "src/k.py")
        self.assertEqual([], self.leaked(item["text"], ub), "added unmarked")


# ======================================================================= whole-text families

def _repo(test):
    from _support import sample_manifest_data
    root = _tmp(test, "mk-e1-repo-")
    os.makedirs(os.path.join(root, ".mokata"))
    with open(os.path.join(root, ".mokata", "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(sample_manifest_data(), fh)
    return root


def _cases():
    """(name, text, tier) — the three kinds of damaged key every gate must refuse."""
    begin, body, end = mangled_key()
    unmarked = "\n".join(K.b64_lines(K.KEY_BODIES["rsa2048"], 64))
    cut = K.D5 + K.BEGIN + " " + K.RSA + "\n" + "\n".join(K.random_body(64, 6, "cut")) + "\n"
    return [("dashless", begin + "\n" + "\n".join(body) + "\n" + end + "\n", KEY_TOLERANT),
            ("unmarked", unmarked + "\n", KEY_TOLERANT),
            ("cut", cut, KEY_TOLERANT),
            ("strict", "\n".join([K.marker(K.BEGIN)] + K.random_body(64, 4) + [K.marker(K.END)]),
             KEY_FIRM)]


class TheCopyPathReScanHoldsKeys(unittest.TestCase):
    """Round 3: `verify_assembled` — the copy path's re-scan of the ASSEMBLED block — is the
    guard there (no per-line key check precedes it, unlike `redact_text`). A key of every kind
    the one detector knows, reaching the assembled block, refuses the copy: whole-scan and the
    windowed scan alike."""

    def test_every_key_kind_refuses_the_assembled_block(self):
        d = _tmp(self)
        begin, body, end = mangled_key()
        cases = {
            "strict marker": K.marker(K.BEGIN) + NL,
            "dash-less marker": begin + NL,
            "unmarked DER run": NL.join(K.b64_lines(K.KEY_BODIES["rsa2048"], 64)) + NL,
            "prefixed DER head": 'k = "' + NL.join(K.b64_lines(K.KEY_BODIES["ec-P-256"], 64)) + NL,
        }
        for name, block in cases.items():
            p = _put(d, "assembled.txt", "intro" + NL + block + "outro" + NL)
            for whole in (U.WHOLE_SCAN_MAX, 0):
                with self.subTest(name=name, whole=whole):
                    old = U.WHOLE_SCAN_MAX
                    U.WHOLE_SCAN_MAX = whole
                    try:
                        with self.assertRaises(U.RefRefused):
                            U.verify_assembled(p, "src/k.txt")
                    finally:
                        U.WHOLE_SCAN_MAX = old

    def test_a_clean_block_passes(self):
        d = _tmp(self)
        p = _put(d, "assembled.txt", "intro" + NL + "x = 1" + NL)
        U.verify_assembled(p, "src/k.txt")


class EveryGateBlocksAMangledKey(unittest.TestCase):

    def test_writegate(self):
        from mokata.govern.gate import WriteGate, WriteRequest
        root = _repo(self)
        for name, text, tier in _cases():
            wrote = []
            out = WriteGate().submit(WriteRequest(kind="memory", target=os.path.join(root, "m.md"),
                                                  content=text),
                                     commit=lambda: wrote.append(1), assume_yes=True,
                                     workspace_root=root)
            self.assertFalse(out.committed, name)
            self.assertEqual([], wrote, name)
            self.assertIn(tier, [f.kind for f in out.findings], name)

    def _hook(self, payload):
        env = dict(os.environ, PYTHONPATH=os.pathsep.join(
            [os.path.join(os.path.dirname(__file__), "..", "src"), os.environ.get("PYTHONPATH", "")]))
        return subprocess.run([sys.executable, HOOK], input=payload, capture_output=True,
                              text=True, env=env, stdin=None).returncode

    def test_hook_write_edit_and_bash(self):
        for name, text, _tier in _cases():
            for tool, inp in (("Write", {"file_path": "a.txt", "content": text}),
                              ("Edit", {"file_path": "a.txt", "old_string": "x",
                                        "new_string": text}),
                              ("Bash", {"command": "printf '%s' '" + text + "' > k"})):
                payload = json.dumps({"hook_event_name": "PreToolUse", "tool_name": tool,
                                      "tool_input": inp})
                self.assertEqual(2, self._hook(payload), f"{name} via {tool}")

    def test_session_push(self):
        from mokata.session_bundle import scan_bundle_values, scan_transcript_turns
        for name, text, _tier in _cases():
            self.assertIn("notes", scan_bundle_values({"notes": text, "ok": "fine"}), name)
            hits = scan_transcript_turns({"transcript": {"turns": [
                {"index": 3, "question": "q", "answer": text}]}})
            self.assertIn(3, hits, name)

    def test_memory_export(self):
        from mokata.memory.item import MemoryItem
        from mokata.memory.share import scan_export_item
        for name, text, _tier in _cases():
            kinds = [f.kind for f in scan_export_item(MemoryItem(subject="deploy-notes",
                                                                 value=text))]
            self.assertIn("secret-egress-blocked", kinds, name)

    def test_events_and_audit_share_publish_as_a_gated_send(self):
        """`team_events.publish` and `team_audit` share both submit kind `send` to the WriteGate;
        the shared team journal scans each publish the same way (`_default_scan`)."""
        from mokata.govern.gate import WriteGate, WriteRequest
        from mokata import team_events, team_journal
        from mokata.govern.trust import CLI_SURFACE
        for name, text, _tier in _cases():
            payload = json.dumps({"event": "note", "body": text})
            # The request `team_events.publish` really builds: its `team-events:<env>/<ns>` label,
            # the CLI surface, and NO workspace_root. A path-shaped target with a temp root was
            # refused by self-protect containment (empty findings, not the secret block) whenever
            # the suite ran outside the temp root -- every CI leg, mirror run 42985179907 4/4.
            target = team_events.PUBLISH_TARGET_PREFIX + "MOKATA_TEAM_DSN/ns"
            out = WriteGate().submit(WriteRequest(kind="send", target=target, content=payload,
                                                  surface=CLI_SURFACE),
                                     commit=lambda: None, assume_yes=True)
            self.assertFalse(out.committed, name)
            self.assertIn("secret-egress-blocked", [f.kind for f in out.findings], name)
            entry = type("E", (), {"payload": {"body": text}, "key": "audit/1"})()
            self.assertIn("secret-egress-blocked",
                          [f.kind for f in team_journal._default_scan(entry)], name)
        from mokata.share import blocked_keys
        for name, text, _tier in _cases():
            self.assertTrue(blocked_keys({"settings": {"deploy": text}}, for_send=True), name)

    def test_vault_push(self):
        """`vault_push` commits through the WriteGate (kind `config`) after the human's yes: a
        secret in the artifact blocks even when approved."""
        from mokata.govern.gate import WriteGate, WriteRequest
        root = _repo(self)
        for name, text, _tier in _cases():
            out = WriteGate().submit(
                WriteRequest(kind="config",
                             target=os.path.join(root, ".mokata", "vault", "design.md"),
                             content="# design\n\n" + text),
                commit=lambda: None, assume_yes=True, human_approved=True, workspace_root=root)
            self.assertFalse(out.committed, name)

    def test_explain_bundle_and_ci_evidence_redact(self):
        from mokata import ci_evidence
        for name, text, _tier in _cases():
            out, n = G.redact("intro\n" + text + "outro\n")
            self.assertEqual([], key_kinds(out), name)
            self.assertGreater(n, 0, name)
            tally = ci_evidence._Tally()
            block = ci_evidence._redacted_block(text, tally)
            self.assertEqual([], key_kinds(block), name)
            self.assertGreater(tally.redacted, 0, name)


if __name__ == "__main__":
    unittest.main()
