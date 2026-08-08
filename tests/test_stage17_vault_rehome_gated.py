"""0.0.17 stage 17 (VAULT-CHANNEL-UNSCANNED) — the vault re-home goes through the WriteGate.

`migrate_channels._migrate_vault` re-homes portable session bundles off the deprecated `vault`
transport onto the mode-derived canonical one. Until this stage it wrote the bundle bytes with a
raw `dst.write_bundle(tag, blob)` — NO secret scan, and its only audit record sat under
`if ledger is not None:`, so a caller with no ledger re-homed durable bundles leaving nothing at
all behind (P7 fails before the scan question is even asked).

TWO defects, one fix:

  * VAULT-CHANNEL-UNSCANNED — the bundle bytes come off a store the human has not re-read since
    they were captured; they must be scanned like every other durable write. The gate does that,
    and it is the UNIVERSAL gate (I2/P2), not a second one.
  * GATED-ADMITS-UNGATED-CALLER — the same call site is the third, ungated caller of the
    `write_bundle` twins that `GATED` in `test_si_6_writegate_side_doors` describes. Routing it
    through the gate expires `GATED_CALLER_EXCEPTIONS['VAULT-REHOME-UNGATED-WRITE-BUNDLE']`.

THE SHAPE, and what it deliberately is NOT: ONE human decision over the batch (unchanged), then a
gate submit PER BUNDLE so the scan sees each bundle's own bytes. Turning the batch consent into N
prompts would be a UX regression dressed as governance; the batch decision rides in on
`human_approved`, exactly as the sibling `memory.migrate_memory` carries its own.
"""

import json
import os
import tempfile
import unittest

import _support  # noqa: F401

from mokata import session_bundle as SB
from mokata import migrate_channels as MC
from mokata.config import Surface
from mokata.govern.ledger import AuditLedger
from mokata.init import init_repo
from mokata.session_transport import (SessionTransportUnavailable, LocalTransport,
                                      VaultTransport)

# Split so the literal never sits in the file as one token (the repo's own convention).
SECRET = "AKIA" + "IOSFODNN7" + "EXAMPLE"


def _silent(_):
    pass


def _repo(d):
    init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
    return Surface.load(d)


def _ledger(surface):
    return AuditLedger.from_mokata_dir(surface.mokata_dir)


def _bundle(note: str) -> str:
    """A well-formed, integrity-VALID session bundle whose state carries `note`.

    Valid on purpose: this suite is about the SECRET SCAN and the AUDIT RECORD, so a bundle must
    clear `verify_bundle` before the question this stage asks is even reached. The corrupt-bundle
    refusal is pinned separately (it is a do-not-change)."""
    core = {
        "schema_version": SB.BUNDLE_SCHEMA_VERSION,
        "kind": SB.BUNDLE_KIND,
        "repo_fingerprint": "fingerprint",
        "run_id": None,
        "state": {"note": note},
        "transcript": None,
        "meta": {},
        "cross_repo": False,
        "origin_repo": "",
    }
    bundle = dict(core)
    bundle["content_hash"] = SB._hash_core(core)
    bundle["provenance"] = {"author": "tester", "source": "repo", "created": "2026-08-04T00:00:00Z"}
    return SB.serialize_bundle(bundle)


def _plant(root, **tags) -> None:
    """Put bundles into the DEPRECATED vault transport — the source this stage re-homes from."""
    src = VaultTransport(root)
    for tag, note in tags.items():
        src.write_bundle(tag, _bundle(note))


def _dst_tags(root):
    return sorted(LocalTransport(root).list_tags())


class TestVaultReHomeIsScanned(unittest.TestCase):
    """The gate's secret scan now sees every bundle's own bytes."""

    def test_a_bundle_carrying_a_secret_is_refused_and_never_reaches_the_target(self):
        """THE STAGE. A raw `write_bundle` copied whatever the vault held; the gate hard-blocks a
        secret (I1 — a block approval cannot override) and the bundle stays where it was."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _plant(d, leaky=f"token {SECRET} here")
            lines = []
            res = MC.run_channel_migration(surface, "vault", assume_yes=True,
                                           ledger=_ledger(surface), out=lines.append)
            self.assertEqual(_dst_tags(d), [],
                             "a bundle carrying a secret was re-homed — the gate did not scan it")
            self.assertEqual(res.migrated, 0)
            self.assertTrue(any("leaky" in ln for ln in lines),
                            f"the refusal was not ANNOUNCED — nothing named the bundle: {lines}")

    def test_the_rest_of_the_batch_still_lands_when_one_bundle_is_blocked(self):
        """A per-bundle refusal, matching the integrity refusal already in this loop: the blocked
        one is named and skipped, the clean ones are re-homed. Never an all-or-nothing abort, and
        never a silent skip."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _plant(d, clean_a="fine", leaky=f"key={SECRET}", clean_b="also fine")
            lines = []
            res = MC.run_channel_migration(surface, "vault", assume_yes=True,
                                           ledger=_ledger(surface), out=lines.append)
            self.assertEqual(_dst_tags(d), ["clean_a", "clean_b"])
            self.assertEqual(res.migrated, 2)
            self.assertFalse(res.aborted)
            self.assertTrue(any("leaky" in ln for ln in lines), lines)

    def test_a_refused_bundle_never_leaks_its_secret_into_the_announcement(self):
        """The refusal is printed to a human's terminal. Printing the matched literal there would
        re-emit the secret the scan just blocked."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _plant(d, leaky=f"token {SECRET} here")
            lines = []
            MC.run_channel_migration(surface, "vault", assume_yes=True,
                                     ledger=_ledger(surface), out=lines.append)
            self.assertNotIn(SECRET, "\n".join(lines))

    def test_a_clean_bundle_lands_through_the_gate_and_the_gate_records_it(self):
        """GATED means the write executes inside the `commit=` callable the gate runs — so a
        committed bundle leaves the gate's OWN `write_gate` approved entry, not just the
        channel's."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _plant(d, alpha="fine")
            ledger = _ledger(surface)
            res = MC.run_channel_migration(surface, "vault", assume_yes=True, ledger=ledger,
                                           out=_silent)
            self.assertEqual(res.migrated, 1)
            self.assertEqual(_dst_tags(d), ["alpha"])
            gate_entries = [e for e in ledger.entries()
                            if e.get("kind") == "write_gate" and e.get("decision") == "approved"]
            self.assertTrue(gate_entries,
                            "no `write_gate` approved entry — the write did not run inside the "
                            "gate's commit callable, so it is not GATED in the register's sense")

    def test_a_blocked_bundle_is_recorded_as_blocked(self):
        """A refusal that leaves no trace is a refusal nobody can audit (I3)."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _plant(d, leaky=f"key={SECRET}")
            ledger = _ledger(surface)
            MC.run_channel_migration(surface, "vault", assume_yes=True, ledger=ledger,
                                     out=_silent)
            blocked = [e for e in ledger.entries()
                       if e.get("kind") == "write_gate" and e.get("decision") == "blocked"]
            self.assertTrue(blocked, "the gate blocked a bundle and recorded nothing")


class TestTheRecordIsUnconditional(unittest.TestCase):
    """`if ledger is not None:` meant a caller with no ledger re-homed durable bundles silently."""

    def test_no_ledger_no_migration(self):
        """The production caller (`cli_commands/migrate.py:34`) passes `_ledger_for(path)`, which
        DEGRADES TO None on an unreadable/uninitialized repo — so this is a reachable production
        state, not a test-only one. Refuse loudly rather than write unrecorded."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _plant(d, alpha="fine")
            res = MC.run_channel_migration(surface, "vault", assume_yes=True, ledger=None,
                                           out=_silent)
            self.assertTrue(res.aborted)
            self.assertEqual(res.migrated, 0)
            self.assertEqual(_dst_tags(d), [],
                             "bundles were re-homed with NO audit ledger — the write is unrecorded")
            self.assertIn("ledger", res.message.lower(),
                          f"the refusal does not say WHY: {res.message!r}")

    def test_a_refusal_for_want_of_a_ledger_does_not_mark_the_channel_migrated(self):
        """A marker written on a refusal would make the next run a no-op — one missing ledger and
        the channel is permanently 'already migrated' with nothing moved."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _plant(d, alpha="fine")
            MC.run_channel_migration(surface, "vault", assume_yes=True, ledger=None, out=_silent)
            self.assertIsNone(MC._read_marker(surface.mokata_dir, "vault"))
            res = MC.run_channel_migration(surface, "vault", assume_yes=True,
                                           ledger=_ledger(surface), out=_silent)
            self.assertEqual(res.migrated, 1)

    def test_every_re_homed_bundle_records_its_channel_entry(self):
        """The `migrate_channel` record, now unconditional — one per bundle actually re-homed."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _plant(d, alpha="fine", beta="fine too")
            ledger = _ledger(surface)
            MC.run_channel_migration(surface, "vault", assume_yes=True, ledger=ledger, out=_silent)
            recorded = sorted(e.get("tag") for e in ledger.entries()
                              if e.get("kind") == "migrate_channel")
            self.assertEqual(recorded, ["alpha", "beta"])


class TestOneHumanDecisionNotN(unittest.TestCase):
    """The scan is per-content; the DECISION is not. Turning the batch consent into one prompt per
    bundle would be a UX regression dressed as governance."""

    def test_the_batch_is_asked_exactly_once_however_many_bundles(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _plant(d, a="1", b="2", c="3", e="4")
            asked = []

            def _confirm(text):
                asked.append(text)
                return True

            res = MC.run_channel_migration(surface, "vault", confirm=_confirm,
                                           ledger=_ledger(surface), out=_silent)
            self.assertEqual(res.migrated, 4)
            self.assertEqual(len(asked), 1,
                             f"the human was asked {len(asked)} times for one batch: {asked}")


class TestTheBatchDecisionIsCarriedTRUTHFULLY(unittest.TestCase):
    """GATE-HUMAN-APPROVED-CONFLATES-ASSUME-YES (found at the stage 17 review).

    The first draft submitted every bundle with a hardcoded `human_approved=True` and never passed
    `assume_yes` — but the batch consent it claimed to be carrying sits under `if not assume_yes:`,
    so on a `--yes` run NO HUMAN IS ASKED ANYTHING and the gate was told the opposite. `gate.py:146`
    forbids exactly that conflation: `human_approved` is "an explicit human decision on THIS EXACT
    write, obtained and verified out-of-band"; `assume_yes` is "a STAND-IN approval".

    Inert today (`WriteGate(ledger=ledger)` threads no trust, so the propose-only rung is skipped),
    which is why it is pinned rather than merely fixed: `req.tool`/`req.surface` are ALREADY
    stamped, so the rung engages the day the trust dial arrives (SI.4-b, 0.0.18) and a hardcoded
    True would clear propose-only with no human. The pin is what stops it coming back."""

    def _submissions(self, **run_kwargs):
        """Capture the flags every bundle is submitted with, through the REAL gate — the write still
        happens, so this pins the live call site rather than a stubbed one."""
        from mokata.govern import WriteGate
        seen = []
        real = WriteGate.submit

        def _spy(self, req, **kw):
            seen.append(kw)
            return real(self, req, **kw)

        WriteGate.submit = _spy
        try:
            res = MC.run_channel_migration(**run_kwargs)
        finally:
            WriteGate.submit = real
        return seen, res

    def test_under_yes_the_gate_is_never_told_a_human_approved(self):
        """THE FIX. `--yes` skips the consent, so there IS no human decision to carry."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _plant(d, alpha="fine", beta="fine")
            seen, res = self._submissions(surface=surface, channel="vault", assume_yes=True,
                                          ledger=_ledger(surface), out=_silent)
            self.assertEqual(res.migrated, 2)
            self.assertEqual(len(seen), 2, f"expected one submit per bundle: {seen}")
            for kw in seen:
                self.assertFalse(kw.get("human_approved"),
                                 "under --yes the gate was told an explicit human decision was "
                                 "obtained out-of-band, and none was — nobody was asked")
                self.assertTrue(kw.get("assume_yes"),
                                "the stand-in approval was not carried as `assume_yes`, so the "
                                "batch decision reaches the gate as nothing at all")

    def test_a_human_who_answered_yes_is_carried_as_human_approved(self):
        """The other half: a real answer must NOT be downgraded to a stand-in, or the batch consent
        stops de-duplicating the ask and the human is prompted per bundle."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _plant(d, alpha="fine", beta="fine")
            seen, res = self._submissions(surface=surface, channel="vault",
                                          confirm=lambda _t: True,
                                          ledger=_ledger(surface), out=_silent)
            self.assertEqual(res.migrated, 2)
            self.assertEqual(len(seen), 2, f"expected one submit per bundle: {seen}")
            for kw in seen:
                self.assertTrue(kw.get("human_approved"),
                                "a human answered the batch consent and the gate was not told")
                self.assertFalse(kw.get("assume_yes"),
                                 "a real human decision was submitted as a STAND-IN approval")

    def test_the_two_flags_are_never_both_asserted(self):
        """They are mutually exclusive by construction — either a human decided, or one stood in."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _plant(d, alpha="fine")
            for kwargs in ({"assume_yes": True}, {"confirm": lambda _t: True}):
                with tempfile.TemporaryDirectory() as d2:
                    s2 = _repo(d2)
                    _plant(d2, alpha="fine")
                    seen, _ = self._submissions(surface=s2, channel="vault",
                                                ledger=_ledger(s2), out=_silent, **kwargs)
                    for kw in seen:
                        self.assertNotEqual(bool(kw.get("human_approved")),
                                            bool(kw.get("assume_yes")),
                                            f"both/neither flag asserted for {kwargs}: {kw}")


class TestDoNotChange(unittest.TestCase):
    """The properties stage 17 must carry through untouched. Pinned so 'did not change it' is a
    fact rather than an intention."""

    def test_consent_is_fail_closed_with_no_confirm_callable(self):
        """`gate = confirm or (lambda _t: False)` — no way to ask means NO."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _plant(d, alpha="fine")
            res = MC.run_channel_migration(surface, "vault", ledger=_ledger(surface), out=_silent)
            self.assertTrue(res.aborted)
            self.assertEqual(_dst_tags(d), [])

    def test_declining_the_batch_re_homes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _plant(d, alpha="fine", beta="fine")
            res = MC.run_channel_migration(surface, "vault", confirm=lambda _t: False,
                                           ledger=_ledger(surface), out=_silent)
            self.assertTrue(res.aborted)
            self.assertEqual(res.migrated, 0)
            self.assertEqual(_dst_tags(d), [])

    def test_a_corrupt_bundle_is_refused_and_announced_and_the_rest_continue(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _plant(d, good="fine")
            VaultTransport(d).write_bundle("broken", "{not json at all")
            lines = []
            res = MC.run_channel_migration(surface, "vault", assume_yes=True,
                                           ledger=_ledger(surface), out=lines.append)
            self.assertEqual(_dst_tags(d), ["good"])
            self.assertEqual(res.migrated, 1)
            self.assertTrue(any("broken" in ln and "integrity" in ln for ln in lines), lines)

    def test_a_bundle_that_parses_but_fails_its_content_hash_is_refused(self):
        """⚠ ADDED BY A SURVIVING MUTANT (M15), and that is why it is here rather than in the first
        draft. The corrupt-bundle pin above plants `{not json at all`, which dies in `json.loads`
        — so replacing `SB.verify_bundle(json.loads(blob))` with a bare `json.loads(blob)` left the
        whole suite GREEN. The refusal LOOKED pinned and only its cheaper half was.

        `verify_bundle` is the half that matters here: a bundle that parses fine but whose payload
        no longer matches its stored content-hash is TAMPERED or truncated, and re-homing it copies
        a session nobody approved onto the canonical transport."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            tampered = json.loads(_bundle("original"))
            tampered["state"] = {"note": "swapped out after the hash was taken"}
            VaultTransport(d).write_bundle("tampered", json.dumps(tampered))
            _plant(d, good="fine")
            lines = []
            res = MC.run_channel_migration(surface, "vault", assume_yes=True,
                                           ledger=_ledger(surface), out=lines.append)
            self.assertEqual(_dst_tags(d), ["good"],
                             "a bundle failing its content-hash was re-homed")
            self.assertEqual(res.migrated, 1)
            self.assertTrue(any("tampered" in ln and "integrity" in ln for ln in lines), lines)

    def test_the_vault_copies_are_left_in_place(self):
        """SIMP.S2 is NEVER destructive."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _plant(d, alpha="fine", beta="fine")
            MC.run_channel_migration(surface, "vault", assume_yes=True, ledger=_ledger(surface),
                                     out=_silent)
            self.assertEqual(sorted(VaultTransport(d).list_tags()), ["alpha", "beta"])

    def test_a_tag_already_in_the_target_is_skipped_not_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _plant(d, alpha="from the vault")
            LocalTransport(d).write_bundle("alpha", _bundle("already here"))
            res = MC.run_channel_migration(surface, "vault", assume_yes=True,
                                           ledger=_ledger(surface), out=_silent)
            self.assertEqual(res.migrated, 0)
            self.assertIn("already here",
                          LocalTransport(d).read_bundle("alpha"))

    def test_an_unresolvable_transport_degrades_typed_and_re_homes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _plant(d, alpha="fine")
            real = MC.make_transport if hasattr(MC, "make_transport") else None
            self.assertIsNone(real, "make_transport is imported inside the function — patch there")
            import mokata.session_transport as ST
            saved = ST.make_transport

            def _boom(*_a, **_k):
                raise SessionTransportUnavailable("no DSN")

            ST.make_transport = _boom
            try:
                res = MC.run_channel_migration(surface, "vault", assume_yes=True,
                                               ledger=_ledger(surface), out=_silent)
            finally:
                ST.make_transport = saved
            self.assertTrue(res.aborted)
            self.assertIn("no DSN", res.message)
            self.assertEqual(_dst_tags(d), [])

    def test_an_empty_vault_is_still_a_clean_no_op(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            res = MC.run_channel_migration(surface, "vault", assume_yes=True,
                                           ledger=_ledger(surface), out=_silent)
            self.assertEqual(res.migrated, 0)
            self.assertFalse(res.aborted)
            self.assertIn("nothing to re-home", res.message)


class TestTheGateIsTheUniversalOne(unittest.TestCase):
    """I2/P2 — reuse the one gate, never a parallel one. Read from source, not from prose."""

    def test_migrate_channels_submits_to_the_write_gate(self):
        import inspect
        src = inspect.getsource(MC._migrate_vault)
        self.assertIn("WriteGate", src,
                      "the vault re-home does not construct the universal WriteGate")
        self.assertIn("commit=", src,
                      "the write must execute inside the `commit=` callable the gate runs — that "
                      "is what `GATED` means, and what expires the declared exception")
        self.assertNotIn("read_yes_no", src,
                         "a second consent surface inside the migrator is a parallel gate")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
