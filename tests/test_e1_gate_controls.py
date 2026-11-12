"""0.0.28 E1 (REDACT-MANGLED-KEY) — controls and the firm/tolerant split (E-Q5).

The tolerant detector would hard-block prose, changelog lines, mokata's own fixtures and base64 CA
data if every key finding stayed non-overridable. Ruled 2026-11-07 (E-Q5): a FIRM finding (the strict
form; >= 3 dashes with PRIVATE KEY complete; the base64 of a PRIVATE marker) stays a signature that no
store can reach. A TOLERANT finding (dash-less, cut, split, unmarked run) blocks at the WriteGate and
the hook but a human may record it through `secret_ignore` (hash + path). Egress is fatal for both.

Rows: E1.GateControls (each control's expected outcome per tier), E1.IgnoreTiers.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import base64
import os
import subprocess
import sys
import unittest

import _support  # noqa: F401

import _e1_keys as K
from test_e1_every_site_redacts import HOOK, _repo, key_kinds

from mokata.govern import secret_ignore as SI
from mokata.govern.secrets import KEY_FIRM, KEY_TOLERANT, scan
from mokata.ide import context as CX
from mokata.ide import usages as U

NL = chr(10)


def _cert_pem():
    head = K.D5 + K.BEGIN + " " + K.w(67, 69, 82, 84, 73, 70, 73, 67, 65, 84, 69) + K.D5
    tail = K.D5 + K.END + " " + K.w(67, 69, 82, 84, 73, 70, 73, 67, 65, 84, 69) + K.D5
    return NL.join([head, *K.b64_lines(K.certificate(), 64), tail]) + NL


def _signatures():
    """DER signatures (ECDSA/DSA `SEQUENCE{INTEGER r, INTEGER s}`), base64, one per line — r's
    second byte swept over all 256 values, so the bytes right after r's length take every value
    a private key's version-and-next-field could (an `INTEGER 0|1` check is load-bearing)."""
    import random
    rnd = random.Random("e1-sigs")
    lines = []
    for b in range(256):
        r = bytes([0x11 + rnd.randrange(0x60), b]) + bytes(rnd.randrange(256) for _ in range(30))
        s = bytes([0x22 + rnd.randrange(0x50)]) + bytes(rnd.randrange(256) for _ in range(31))
        lines.append(base64.b64encode(K.seq(K.tlv(2, r), K.tlv(2, s))).decode())
    return NL.join(lines) + NL


def _walker_count(text):
    counts = {}
    list(U.pem_lines(text.splitlines(keepends=True), counts, "x"))
    return counts.get("replaced", 0)


CONTROLS = {
    # name: (text, expected key-finding kinds at a gate, lines the key WALKER replaces)
    "certificate": (_cert_pem(), [], 0),
    "public-key": (NL.join([K.D5 + K.BEGIN + " PUBLIC " + K.KEY + K.D5,
                            *K.b64_lines(K.spki(), 64),
                            K.D5 + K.END + " PUBLIC " + K.KEY + K.D5]) + NL, [], 0),
    "rsa-public": (NL.join(K.b64_lines(K.rsa_public(), 64)) + NL, [], 0),
    "kubeconfig-ca-only": ("clusters:" + NL + "- cluster:" + NL + "    certificate-authority-data: "
                           + base64.b64encode(_cert_pem().encode()).decode() + NL
                           + "    server: https://k8s.example:6443" + NL, [], 0),
    "eighty-dash-rule": ("-" * 80 + NL + "title" + NL + "-" * 80 + NL, [], 0),
    "regex-mentioning-the-marker": (
        'PAT = re.compile(r"' + K.D5 + K.BEGIN + " (?:RSA |EC )?" + K.PRIVATE + " " + K.KEY
        + K.D5 + '")' + NL, [], 0),
    "changelog-private-beta": ("--- " + K.BEGIN.title() + " private beta ---" + NL, [], 0),
    "warning-banner": ("NEVER PASTE " + K.BEGIN + " CERTIFICATE OR " + K.PRIVATE + " " + K.KEY
                       + " TEXT HERE" + NL, [], 0),
    "der-signatures": (_signatures(), [], 0),
    "readme-prose": ("The file starts with " + K.BEGIN + " " + K.OPENSSH + " " + K.PRIVATE + " "
                     + K.KEY + " and ends with the matching line." + NL + "Keep it safe." + NL,
                     [KEY_TOLERANT], 1),
    "mokata-own-fixture": ('    begin = dash + "' + K.BEGIN + ' " + "' + K.RSA + " " + K.PRIVATE
                           + '" + " ' + K.KEY + '" + dash' + NL + "    return begin" + NL,
                           [KEY_TOLERANT], 1),
}


class GateControls(unittest.TestCase):
    """E1.GateControls — each control's outcome, per tier, in the cell: firm blocks for good;
    tolerant blocks but is recordable; at line sites it is replaced and counted (its own line
    only — a prose BEGIN does not open a key to the end of the file)."""

    def test_each_control(self):
        for name, (text, kinds, replaced) in CONTROLS.items():
            self.assertEqual(kinds, key_kinds(text), name)
            self.assertEqual(replaced, _walker_count(text), name)

    def test_tolerant_controls_are_recordable_at_the_gate(self):
        from mokata.govern.gate import WriteGate, WriteRequest
        root = _repo(self)
        for name, (text, kinds, _r) in CONTROLS.items():
            if KEY_TOLERANT not in kinds:
                continue
            target = os.path.join(root, "docs", f"{name}.md")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            req = WriteRequest(kind="doc", target=target, content=text)
            out = WriteGate().submit(req, commit=lambda: None, assume_yes=True, workspace_root=root)
            self.assertFalse(out.committed, f"{name} must block before it is recorded")
            for f in out.findings:
                if f.kind == KEY_TOLERANT:
                    SI.add_ignore(root, f.token, target, reason="prose, not a key",
                                  assume_yes=True, out=lambda _m: None)
            wrote = []
            out = WriteGate().submit(req, commit=lambda: wrote.append(1), assume_yes=True,
                                     workspace_root=root)
            self.assertTrue(out.committed, f"{name}: recorded, still blocks: {out.reason}")


class IgnoreTiers(unittest.TestCase):
    """E1.IgnoreTiers — a tolerant finding is recordable (and only it gains `is_ignorable`); a
    forged store cannot reach a firm finding; egress is fatal for both."""

    def setUp(self):
        self.root = _repo(self)
        self.target = os.path.join(self.root, "README.md")
        self.prose = CONTROLS["readme-prose"][0]
        self.strict = K.marker(K.BEGIN) + NL + NL.join(K.random_body(64, 3)) + NL

    def test_only_the_tolerant_kind_joins_the_ignorable_set(self):
        self.assertEqual(("high-entropy-token",), SI.IGNORABLE_KINDS)
        self.assertEqual((KEY_TOLERANT,), SI.IGNORABLE_SIGNATURE_KINDS)
        for f in scan(self.strict):
            if f.kind == KEY_FIRM:
                self.assertFalse(SI.is_ignorable(f))
        self.assertTrue(any(SI.is_ignorable(f) for f in scan(self.prose)))

    def test_a_firm_marker_is_refused_by_name(self):
        with self.assertRaises(SI.NotIgnorable) as cm:
            SI.add_ignore(self.root, K.marker(K.BEGIN), self.target, reason="x", assume_yes=True,
                          out=lambda _m: None)
        self.assertEqual(KEY_FIRM, cm.exception.shape)

    def test_a_forged_store_cannot_reach_a_firm_finding(self):
        """Hand-write a store whose entry is the hash of the firm line, with a valid checksum."""
        line = K.marker(K.BEGIN)
        entry = SI.IgnoreEntry(hash=SI.token_key(line), path="README.md", reason="forged",
                               added_at="2026-11-11T00:00:00+00:00", actor="x")
        os.makedirs(os.path.dirname(SI.ignores_path(self.root)), exist_ok=True)
        with open(SI.ignores_path(self.root), "w", encoding="utf-8") as fh:
            fh.write(SI._render([entry.as_dict()]))
        store, notice = SI.load_for_scan(self.root)
        self.assertIsNotNone(store, notice)
        self.assertIn(KEY_FIRM, [f.kind for f in scan(self.strict, path=self.target,
                                                       ignores=store)])

    def test_a_recorded_tolerant_finding_passes_the_gate_and_the_hook_but_not_egress(self):
        token = next(f.token for f in scan(self.prose) if f.kind == KEY_TOLERANT)
        SI.add_ignore(self.root, token, self.target, reason="README prose", assume_yes=True,
                      out=lambda _m: None)
        store, _n = SI.load_for_scan(self.root)
        self.assertEqual([], key_kinds(self.prose, path=self.target, ignores=store))
        # egress: fatal for both tiers, a record notwithstanding
        kinds = [f.kind for f in scan(self.prose, path=self.target, ignores=store, for_send=True)]
        self.assertIn(KEY_TOLERANT, kinds)
        self.assertIn("secret-egress-blocked", kinds)
        # the hook: the same record, the same answer
        env = dict(os.environ, PYTHONPATH=os.pathsep.join(
            [os.path.join(os.path.dirname(__file__), "..", "src"), os.environ.get("PYTHONPATH", "")]))
        rc = subprocess.run([sys.executable, HOOK, "--text", self.prose, "--path", self.target],
                            capture_output=True, text=True, env=env).returncode
        self.assertEqual(0, rc)
        rc = subprocess.run([sys.executable, HOOK, "--text", self.prose, "--path", self.target,
                             "--send"], capture_output=True, text=True, env=env).returncode
        self.assertEqual(2, rc)
        # the record is this line in this file only
        other = os.path.join(self.root, "OTHER.md")
        self.assertIn(KEY_TOLERANT, key_kinds(self.prose, path=other, ignores=store))

    def test_the_block_message_names_the_record_road_for_a_tolerant_finding(self):
        msg = SI.render_block(scan(self.prose, path=self.target), path=self.target, root=self.root)
        self.assertIn("mokata secret ignore", msg)
        line = K.marker(K.BEGIN)
        msg = SI.render_block(scan(line, path=self.target), path=self.target, root=self.root)
        self.assertNotIn("mokata secret ignore --token", msg)
        self.assertIn(KEY_FIRM, msg)

    def test_an_unmarked_run_is_recordable_by_its_head(self):
        body = NL.join(K.b64_lines(K.KEY_BODIES["ed25519"], 64)) + NL
        f = next(f for f in scan(body) if f.kind == KEY_TOLERANT)
        SI.add_ignore(self.root, f.token, self.target, reason="test vector", assume_yes=True,
                      out=lambda _m: None)
        store, _n = SI.load_for_scan(self.root)
        self.assertEqual([], key_kinds(body, path=self.target, ignores=store))

    def test_the_line_sites_never_consult_the_store(self):
        """A record lets a write through a gate; it never lets a key out of the process."""
        token = next(f.token for f in scan(self.prose) if f.kind == KEY_TOLERANT)
        SI.add_ignore(self.root, token, self.target, reason="README prose", assume_yes=True,
                      out=lambda _m: None)
        out, n = CX._redact(self.prose, "README.md")
        self.assertEqual(1, n)


if __name__ == "__main__":
    unittest.main()
