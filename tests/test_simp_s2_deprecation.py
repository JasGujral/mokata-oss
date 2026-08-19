"""SIMP.S2 — the deprecation-notice primitive (warn ONCE per repo per channel).

The shim layer keeps every deprecated channel working EXACTLY as today but emits ONE
deprecation warning per repo per channel — a state marker (atomic O_EXCL under temp_local,
the `graph_adopt.disclose_first_use` precedent), never a nag on every call. The notice names
WHAT is deprecated, the canonical replacement, the one-time migration command, and WHEN it
disappears. This stage WARNS + SHIMS + MIGRATES only — it deletes nothing.

⚠ **THE REMOVAL RELEASE IS NOT A LITERAL IN THIS FILE, AND THAT IS THE POINT.**
`test_removal_release_is_0_0_17` used to assert `REMOVAL_RELEASE == "0.0.17"` here, in prose that
cited two doc-84 lines as confirmation. 0.0.17 shipped and removed nothing, so from that day the
suite CERTIFIED a false claim on a live user surface and stayed green
(`REMOVAL-RELEASE-ALREADY-PASSED`, doc 84 §1; doc 85 §7h). Bumping the string to `"0.0.18"` would
have reproduced the defect one release later. It is REVERSED instead: every assertion below reads
the release from the module, and whether that release is HONEST is graded by arithmetic in
`tests/test_stage9_removal_release.py`.

★★ **`deprecation.CHANNELS` IS EMPTY AS OF 0.0.18 STAGE 14, AND THAT REWRITES HOW THIS FILE
GRADES — IT DOES NOT DELETE IT.** `neo4j` was the last deprecated channel and stage 14 removed it.
Every test below used to range over `ALL_CHANNELS`, so on an empty registry each one becomes a
`for` loop over nothing: green, forever, grading NOTHING. That is doc 85 §7i arriving through the
front door — a guard whose offenders you just removed — and the rule §7i states is the fix used
here: **the mechanism is exercised against a PLANTED channel, never against a live one.**

⚠ **AND PLANTING RESTORES A BRANCH THAT HAD ALREADY LOST ITS WITNESS BEFORE THIS STAGE.**
`DeprecationNotice.render` has two arms — a channel WITH a one-time migration command, and one
with `migration=""` that says re-index instead. `MIGRATABLE` went empty at slice 4 (`vault` was its
last member), so from that stage the migration arm was rendered by nothing at all while
`test_notice_states_what_replaces_and_when` looped over `()` and passed. Both arms are planted
below, and the migration arm's assertion now has an offender it can fail against. §7f, found by
having to re-derive the domain rather than by reading the loop.
"""

import contextlib
import io
import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import MOKATA_DIR, TEMP_LOCAL_DIRNAME
from mokata import deprecation as D


# The channels this release has REMOVED. `obsidian` / `native-memory` left at stage 10,
# `memory-share` at stage 11, `vault` at stage 13 and `neo4j` at stage 14 — each is gone, and a
# channel that is gone must NOT carry a notice promising a future removal (that is `stale_notices`,
# graded in `test_stage9_removal_release`). Their half of this file's subject lives in the
# per-slice suites, keyed on `deprecation.REMOVED`.
REMOVED_CHANNELS = ("obsidian", "native-memory", "memory-share", "vault", "neo4j")

# The two SHAPES a deprecation notice has, planted rather than borrowed from the registry. Ids that
# no registry will ever hold, so a plant that leaked would be visible rather than plausible.
_PLANTED_MIGRATABLE = D.DeprecationNotice(
    channel="planted-migratable", what="planted migratable channel",
    replacement="The canonical shape is the one that survives.",
    migration="mokata migrate planted-migratable", removal=D.REMOVAL_RELEASE)
_PLANTED_REINDEXED = D.DeprecationNotice(
    channel="planted-derived", what="planted derived-data channel",
    replacement="The canonical code graph is the embedded AST floor / adopted CRG.",
    migration="", removal=D.REMOVAL_RELEASE)
_PLANTED = {n.channel: n for n in (_PLANTED_MIGRATABLE, _PLANTED_REINDEXED)}


@contextlib.contextmanager
def planted_channels():
    """Put the two planted notices in `CHANNELS` for the body, and take them out again.

    ⚠ RESTORED IN A `finally`, and the registry is mutated in place rather than rebound, because
    `warn_deprecated` reads the module-level name — a rebind would leave every other caller in the
    process reading the original object and grade nothing."""
    for cid, notice in _PLANTED.items():
        D.CHANNELS[cid] = notice
    try:
        yield tuple(_PLANTED)
    finally:
        for cid in _PLANTED:
            D.CHANNELS.pop(cid, None)


def _mokata_dir(d):
    md = os.path.join(d, MOKATA_DIR)
    os.makedirs(md, exist_ok=True)
    return md


class TestTheRegistryIsEmptyAndSaysSo(unittest.TestCase):
    """The emptiness is a POSITIVE assertion, not a silence. Every other test in this file is a
    loop, and a loop over an empty registry passes whether or not the thing it loops over works —
    so the one fact that must be stated outright is that the registry IS empty, on purpose."""

    def test_no_channel_is_deprecated_in_this_release(self):
        self.assertEqual(D.CHANNELS, {},
                         "a deprecated channel reappeared: re-arm the loops below on the LIVE set "
                         "rather than leaving them grading planted fixtures only")
        self.assertEqual(D.DEPRECATED_CHANNELS, ())

    def test_the_planting_fixture_leaves_no_residue(self):
        # The plant is the only reason anything below grades, so its removal is graded too: a
        # leaked fixture would make `test_no_channel_is_deprecated_in_this_release` order-dependent.
        with planted_channels() as planted:
            self.assertEqual(set(D.CHANNELS), set(planted))
        self.assertEqual(D.CHANNELS, {})


class TestNoticeText(unittest.TestCase):
    def test_every_channel_has_a_notice(self):
        with planted_channels() as planted:
            for ch in planted:
                self.assertIn(ch, D.CHANNELS)
                self.assertIsInstance(D.CHANNELS[ch], D.DeprecationNotice)

    def test_a_REMOVED_channel_carries_no_deprecation_notice(self):
        # "will be REMOVED in 0.0.18" is a lie once the code is gone. The two registries are
        # disjoint by construction, and this is what says so.
        for ch in REMOVED_CHANNELS:
            self.assertNotIn(ch, D.CHANNELS)
            self.assertIn(ch, D.REMOVED)
        self.assertEqual(set(D.CHANNELS) & set(D.REMOVED), set())

    def test_every_notice_names_the_one_declared_removal_release(self):
        # REVERSED (was `test_removal_release_is_0_0_17`). The assertion is a DERIVATION: the
        # notices and the constant must agree with each other and with the declaration, whatever
        # release that declaration names. It cannot be satisfied by a stale value, and it cannot
        # go stale itself — there is no release string on this side of it.
        self.assertEqual(D.REMOVAL_RELEASE, D.removal_target(D.REMOVAL_DECLARATION))
        with planted_channels() as planted:
            for ch in planted:
                self.assertIn(D.REMOVAL_RELEASE, D.CHANNELS[ch].render())

    def test_a_migratable_notice_states_what_replaces_and_when(self):
        ch = _PLANTED_MIGRATABLE.channel
        with planted_channels():
            text = D.CHANNELS[ch].render()
        self.assertIn("REMOVED", text)
        self.assertIn(D.REMOVAL_RELEASE, text)
        # names the one-time migration command
        self.assertIn(f"mokata migrate {ch}", text)

    def test_a_derived_data_notice_needs_no_migration_command(self):
        # The arm `neo4j` occupied until stage 14: derived data is RE-INDEXED, not migrated, and
        # the two arms must not collapse into one just because the live set emptied.
        with planted_channels():
            text = D.CHANNELS[_PLANTED_REINDEXED.channel].render()
        self.assertNotIn("mokata migrate", text)
        self.assertIn("re-index", text.lower())

    def test_notice_never_leaks_a_secret_or_dsn(self):
        # Static text only — no item content, no DSN value, no connection string.
        with planted_channels() as planted:
            for ch in planted:
                text = D.CHANNELS[ch].render()
                self.assertNotIn("://", text)
                self.assertNotIn("password", text.lower())


class TestWarnOncePerRepo(unittest.TestCase):
    def test_first_use_warns_and_ledgers_second_is_silent(self):
        with planted_channels() as planted:
            for ch in planted:
                with tempfile.TemporaryDirectory() as d:
                    md = _mokata_dir(d)
                    out = []
                    first = D.warn_deprecated(ch, md, out=out.append)
                    self.assertTrue(first, f"{ch}: first use must warn")
                    self.assertEqual(len(out), 1)
                    self.assertIn(D.REMOVAL_RELEASE, out[0])

                    out2 = []
                    second = D.warn_deprecated(ch, md, out=out2.append)
                    self.assertFalse(second, f"{ch}: second use must be silent (ledgered marker)")
                    self.assertEqual(out2, [])

    def test_an_unknown_channel_warns_nothing(self):
        # The other direction of the same contract, and it is what makes the empty registry safe:
        # `warn_deprecated` is a lookup, not a printer, so with no channels nothing can fire.
        with tempfile.TemporaryDirectory() as d:
            out = []
            self.assertFalse(D.warn_deprecated("neo4j", _mokata_dir(d), out=out.append))
            self.assertEqual(out, [])

    def test_marker_persists_as_state_under_temp_local(self):
        with planted_channels(), tempfile.TemporaryDirectory() as d:
            md = _mokata_dir(d)
            D.warn_deprecated(_PLANTED_REINDEXED.channel, md, out=lambda _m: None)
            marker_dir = os.path.join(md, TEMP_LOCAL_DIRNAME, "deprecations")
            self.assertTrue(os.path.isdir(marker_dir))
            self.assertTrue(os.listdir(marker_dir), "a state marker must be written")

    def test_default_out_is_stderr(self):
        with planted_channels(), tempfile.TemporaryDirectory() as d:
            md = _mokata_dir(d)
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                D.warn_deprecated(_PLANTED_REINDEXED.channel, md)
            # diagnostics go to stderr, not stdout
            self.assertIn(D.REMOVAL_RELEASE, buf.getvalue())

    def test_records_to_ledger_when_given(self):
        from mokata.govern import AuditLedger
        with planted_channels(), tempfile.TemporaryDirectory() as d:
            md = _mokata_dir(d)
            ledger = AuditLedger.from_mokata_dir(md)
            D.warn_deprecated(_PLANTED_REINDEXED.channel, md, out=lambda _m: None, ledger=ledger)
            kinds = [e.get("kind") for e in ledger.entries()]
            self.assertIn(D.DEPRECATION_LEDGER_KIND, kinds)

    def test_degrade_clean_on_unwritable_marker_dir(self):
        # A best-effort notice: if the marker can't be written we do not crash the caller.
        with planted_channels():
            res = D.warn_deprecated(_PLANTED_REINDEXED.channel, "/nonexistent/\x00bad",
                                    out=lambda _m: None)
        self.assertIn(res, (True, False))            # never raises


if __name__ == "__main__":
    unittest.main()
