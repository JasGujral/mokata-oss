"""SI.6b — scan the STACK-SHARE pair (0.0.13 rider; closes 2 of SI.6's 8 KNOWN_BYPASS entries).

SI.6 built the export-scan machinery (C2: `scan_export_item`, egress strength) and applied it to
the memory-share pair. It then REGISTERED, without fixing, a cluster of CLI/bootstrap writers that
never touch the WriteGate. Two of those are scan-relevant, and they are the two ends of the same
trust boundary:

  `share.py:export_manifest`  — `mokata export` writes a COMMITTABLE `.mokata/mokata-stack.json`
      with no gate, no scan and no ledger entry. It is the C2 hole exactly, on a different artifact:
      a manifest carries `settings` (an open-ended free-text bucket) and `tools.<id>.config` (backend
      params — where a hand-pasted DSN lands instead of the `dsn_env` pointer it should be). A secret
      in either was copied verbatim into a file built to be committed and handed to a teammate (P23).

  `share.py:apply_manifest`  — `mokata import` OVERWRITES the project's governing config from an
      UNTRUSTED shared/community stack file, under a bespoke confirm, validated STRUCTURALLY ONLY
      (`schema.validate_manifest`: types and required keys) and never secret-scanned (P15).

The fix lands ONCE, in the shared seam, because every surface funnels through these two functions:
CLI `export`/`import`, MCP `export_stack`/`import_stack`/`stacks_install`, `team adopt`/`join`, and
`stacks install`. Two of those surfaces scanned at the CALLER (team_adopt, install_stack) and three
did not — including MCP `import_stack`, which WAS wrapped in `_gated_write` but fed it `content=""`,
so the gate's secret-scan ran over an empty string and saw nothing. Gated, ledgered, and blind. That
is the case for putting the scan in the seam rather than at each caller: a seam defends the callers
that forgot, which is every caller that will be written next.

THE ASYMMETRY, and it is deliberate:

  EXPORT  drops the offending KEY and ships the rest (the C2 per-item hard-block). It is YOUR config,
          you asked to publish it, and the useful behaviour is "publish everything clean and TELL ME
          which key I have to fix." Named by KEY, never by value (P23).

  IMPORT  REFUSES THE WHOLE FILE. Three reasons. (1) A manifest is ONE atomic config document, not a
          bag of independent items — memory-share is a LIST, where dropping a poisoned item leaves N
          others individually meaningful; a manifest's keys interlock, and silently dropping one
          produces a config nobody reviewed and nobody published (P2: the human gated THAT file, not
          a mokata-mutated derivative of it). (2) P15 — the right answer to untrusted input carrying
          a credential is to REJECT THE INPUT, not to sanitize it and trust the remainder: a stack
          file that ships a secret is evidence about its provenance, and quietly cleaning it up tells
          the user the source is fine when it is not. (3) It makes the seam agree with the two
          callers that already refuse the whole file on a hit (`team_adopt`, `install_stack`).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from mokata import MANIFEST_FILENAME, MOKATA_DIR                    # noqa: E402
from mokata.config import Surface                                   # noqa: E402
from mokata.govern.ledger import AuditLedger                        # noqa: E402
from mokata.init import init_repo                                   # noqa: E402
from mokata.share import (UNTRUSTED, apply_manifest, export_manifest,  # noqa: E402
                          load_shared, plan_export, validate_shared)


def fake_secret() -> str:
    """The canonical AWS-docs example key, assembled at RUNTIME — mokata's own secret-guard hook
    blocks committing a file that spells one out. Dogfooding (the SI.4/SI.6 convention)."""
    return "AKIA" + "IOSFODNN7" + "EXAMPLE"


def fake_dsn() -> str:
    """A connection string with an INLINE credential — the exact thing a `dsn_env` pointer exists to
    prevent. Assembled at runtime for the same reason: writing it as a literal trips mokata's own
    secret-guard hook (`signature/connection-string-credentials`), which is the scanner under test."""
    return "postgres://u:" + fake_secret() + "@db.example.com/app"


def silent(*_a):
    pass


def _repo(d, profile="standard"):
    init_repo(root=d, profile=profile, assume_yes=True, out=silent)
    return Surface.load(d)


def _manifest_path(d):
    return os.path.join(d, MOKATA_DIR, MANIFEST_FILENAME)


def _read_manifest(d):
    with open(_manifest_path(d), encoding="utf-8") as fh:
        return json.load(fh)


def _write_manifest(d, data):
    with open(_manifest_path(d), "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    return Surface.load(d)


def _poison(d, **leaves):
    """Plant a value straight into the on-disk manifest, BYPASSING every gate — i.e. a credential
    that reached the config through an unscanned path (a hand-edit, an adopted stack). This is how
    we prove the export blocks on the way OUT even when something got in."""
    data = _read_manifest(d)
    data.setdefault("settings", {}).update(leaves)
    return _write_manifest(d, data)


def _community_stack(profile="standard", **leaves):
    """A stack file as it arrives from a teammate / the community catalog: untrusted JSON.

    Read straight off disk, NOT via `export_manifest` — a mokata export now scrubs secrets on the way
    out, so routing the fixture through it would hand the import a file mokata had already cleaned.
    The incoming file this stage defends against is one mokata never touched: hand-written, published
    by another tool, or exported by a mokata too old to scan."""
    d = tempfile.mkdtemp()
    _repo(d, profile)
    if leaves:
        _poison(d, **leaves)
    return _read_manifest(d)


# ======================================================================================
# EXPORT — egress-scanned; a hit drops the KEY, names it, and never reaches the artifact
# ======================================================================================

class TestExportIsScanned(unittest.TestCase):

    def test_a_secret_never_reaches_the_exported_stack_file(self):
        """THE export half of the bug: `mokata export` wrote a committable artifact with no scan."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            surface = _poison(d, deploy_token=f"the prod key is {fake_secret()}",
                              editor="vscode")
            dest = os.path.join(d, "stack.json")
            data = export_manifest(surface, dest=dest)

            with open(dest, encoding="utf-8") as fh:
                raw = fh.read()
            self.assertNotIn(fake_secret(), raw,
                             "THE hole: a secret must never reach the exported stack file")
            self.assertNotIn("deploy_token", raw, "the poisoned KEY is dropped, not just its value")
            self.assertIn("vscode", raw, "the clean keys still export")
            self.assertNotIn("deploy_token", data.get("settings", {}))

    def test_the_blocked_key_is_named_and_the_value_is_never_printed(self):
        """P23 — a refusal must not print the credential it is refusing."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            surface = _poison(d, deploy_token=f"the prod key is {fake_secret()}")
            plan = plan_export(surface)

            self.assertEqual(plan.blocked, ["settings.deploy_token"],
                             "blocked items are named by their KEY path")
            self.assertNotIn(fake_secret(), json.dumps(plan.blocked))
            self.assertNotIn(fake_secret(), plan.payload())
            self.assertNotIn(fake_secret(), plan.render())

    def test_an_inline_dsn_in_a_tools_config_block_is_caught(self):
        """The realistic case, and the reason the seam WALKS the manifest instead of checking a
        hand-maintained list of "fields where a secret could sit": a credential pasted inline into a
        NESTED `tools.<id>.config` block, where a `dsn_env` POINTER belongs."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "full")
            data = _read_manifest(d)
            self.assertIn("sqlite", data.get("tools", {}), "the full profile ships a sqlite tool")
            data["tools"]["sqlite"].setdefault("config", {})["dsn"] = fake_dsn()
            surface = _write_manifest(d, data)

            plan = plan_export(surface)
            self.assertEqual(plan.blocked, ["tools.sqlite.config.dsn"],
                             "a nested config leaf is scanned, not just the top-level settings")
            self.assertNotIn(fake_secret(), plan.payload())
            self.assertIn("sqlite", plan.data["tools"],
                          "only the poisoned KEY is dropped — the tool itself still exports")

    def test_a_clean_stack_exports_byte_identical(self):
        """No behaviour change. The scan is invisible to every honest stack."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, "full")
            before = json.dumps(surface.manifest.data, sort_keys=True)
            plan = plan_export(surface)

            self.assertEqual(plan.blocked, [])
            self.assertFalse(plan.refused)
            self.assertEqual(json.dumps(plan.data, sort_keys=True), before,
                             "a clean manifest passes through the seam untouched")

            dest = os.path.join(d, "stack.json")
            data = export_manifest(surface, dest=dest)
            self.assertEqual(validate_shared(data), [])
            self.assertEqual(validate_shared(load_shared(dest)), [])

    def test_the_cli_export_is_scanned_and_ledgered_as_egress(self):
        """The CLI export had NO gate, NO scan and NO ledger entry at all. Consent is unchanged —
        the typed `mokata export` IS the consent — but the SCAN and the LEDGER are now real, and the
        write is recorded as kind `send` because an export is EGRESS (the C2 argument, verbatim)."""
        from mokata.cli import main
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _poison(d, deploy_token=f"the prod key is {fake_secret()}", editor="vscode")

            rc = main(["export", "--path", d])
            self.assertEqual(rc, 0)

            dest = os.path.join(d, MOKATA_DIR, "mokata-stack.json")
            with open(dest, encoding="utf-8") as fh:
                raw = fh.read()
            self.assertNotIn(fake_secret(), raw, "the CLI export must not exfiltrate a secret")
            self.assertNotIn("deploy_token", raw)
            self.assertIn("vscode", raw)

            led = AuditLedger.from_mokata_dir(os.path.join(d, MOKATA_DIR))
            sends = [e for e in led.entries()
                     if e.get("kind") == "write_gate" and e.get("write_kind") == "send"]
            self.assertTrue(sends, "the export belongs on the audit ledger as an EGRESS write")

    def test_the_mcp_export_reports_the_blocked_count_and_keys(self):
        """The MCP twin WAS gated — but it fed the gate the UNREDACTED manifest at `kind=config`
        (at-rest strength). It now gates the redacted bytes at egress strength and reports what it
        dropped, exactly like `memory_export`."""
        from _support import mcp_commit                   # propose -> human approves -> redeem
        from mokata.mcp import tools_write as TW
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _poison(d, deploy_token=f"the prod key is {fake_secret()}", editor="vscode")

            res = mcp_commit(TW.export_stack, path=d)
            self.assertTrue(res["committed"])
            self.assertEqual(res["blocked"], 1)
            self.assertEqual(res["blocked_keys"], ["settings.deploy_token"])

            with open(res["dest"], encoding="utf-8") as fh:
                self.assertNotIn(fake_secret(), fh.read())


# ======================================================================================
# IMPORT — the incoming file is UNTRUSTED: scanned at the boundary, a hit REFUSES THE FILE
# ======================================================================================

class TestApplyIsScanned(unittest.TestCase):

    def test_a_poisoned_community_stack_is_refused_whole_and_nothing_persists(self):
        """THE import half: an untrusted stack file OVERWROTE the governing config, unscanned."""
        poisoned = _community_stack(deploy_token=f"the prod key is {fake_secret()}")
        with tempfile.TemporaryDirectory() as d:
            result = apply_manifest(d, poisoned, assume_yes=True)

            self.assertFalse(result.applied)
            self.assertTrue(result.blocked, "a secret in an incoming stack must block")
            self.assertFalse(os.path.exists(_manifest_path(d)),
                             "NOTHING may persist from a poisoned stack file — not the clean keys "
                             "either: the file is refused whole, not sanitized")

    def test_the_refusal_names_the_key_labels_the_file_untrusted_and_prints_no_value(self):
        poisoned = _community_stack(deploy_token=f"the prod key is {fake_secret()}")
        with tempfile.TemporaryDirectory() as d:
            result = apply_manifest(d, poisoned, assume_yes=True)

            self.assertEqual(result.blocked, ["settings.deploy_token"])
            self.assertEqual(result.provenance, UNTRUSTED,
                             "the result must say WHERE this content came from (P15)")
            self.assertNotIn(fake_secret(), result.message)
            self.assertNotIn(fake_secret(), json.dumps(result.blocked))
            self.assertIn("settings.deploy_token", result.message,
                          "the refusal names the key so the human can fix it")

    def test_the_scan_precedes_the_human_gate_so_no_approval_can_land_it(self):
        """A secret is a SECURITY block, not a methodology gate (P2/I1): `assume_yes` does not lift
        it, and the human is never even asked to approve a poisoned file."""
        poisoned = _community_stack(deploy_token=f"the prod key is {fake_secret()}")
        asked = []
        with tempfile.TemporaryDirectory() as d:
            result = apply_manifest(d, poisoned, confirm=lambda t: asked.append(t) or True,
                                    force=True)
            self.assertFalse(result.applied)
            self.assertEqual(asked, [],
                             "the human must not be walked to a prompt to approve a secret — the "
                             "file is refused before the gate")

    def test_the_scan_precedes_validation_so_a_malformed_poisoned_file_still_reads_as_poisoned(self):
        """A structurally-invalid file that ALSO carries a credential is refused as POISONED, not
        merely as invalid — the sharper signal about the source's provenance (P15)."""
        poisoned = _community_stack(deploy_token=f"the prod key is {fake_secret()}")
        del poisoned["capabilities"]                      # now structurally invalid too
        with tempfile.TemporaryDirectory() as d:
            result = apply_manifest(d, poisoned, assume_yes=True)
            self.assertTrue(result.blocked)
            self.assertFalse(os.path.exists(_manifest_path(d)))

    def test_the_cli_import_is_scanned_and_ledgered(self):
        from mokata.cli import main
        poisoned = _community_stack(deploy_token=f"the prod key is {fake_secret()}")
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "minimal")
            shared = os.path.join(d, "incoming-stack.json")
            with open(shared, "w", encoding="utf-8") as fh:
                json.dump(poisoned, fh, indent=2)

            rc = main(["import", shared, "--path", d, "--yes", "--force"])
            self.assertEqual(rc, 1, "a poisoned stack import must FAIL, not apply")
            self.assertEqual(Surface.load(d).manifest.profile, "minimal",
                             "the local config is untouched")

            led = AuditLedger.from_mokata_dir(os.path.join(d, MOKATA_DIR))
            blocked = [e for e in led.entries()
                       if e.get("kind") == "write_gate" and e.get("decision") == "blocked"]
            self.assertTrue(blocked, "the refusal belongs on the audit ledger (I3)")

    def test_the_mcp_import_twin_no_longer_gates_an_empty_string(self):
        """The hole the register never named: `import_stack` WAS wrapped in `_gated_write` — but fed
        it `content=""`, so the gate's secret-scan ran over an empty string. Gated, ledgered, blind.
        The seam scan closes it regardless of what the caller hands the gate."""
        from _support import mcp_commit
        from mokata.mcp import tools_write as TW
        poisoned = _community_stack(deploy_token=f"the prod key is {fake_secret()}")
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "minimal")
            shared = os.path.join(d, "incoming-stack.json")
            with open(shared, "w", encoding="utf-8") as fh:
                json.dump(poisoned, fh, indent=2)

            res = mcp_commit(TW.import_stack, path=d, file=shared, force=True)
            self.assertFalse(res["committed"], "a poisoned stack must not apply on MCP either")
            self.assertEqual(Surface.load(d).manifest.profile, "minimal")

    def test_a_clean_stack_still_applies_exactly_as_before(self):
        """No behaviour change on the honest path: gated, force-guarded, applied."""
        clean = _community_stack("full")
        with tempfile.TemporaryDirectory() as d:
            result = apply_manifest(d, clean, assume_yes=True)
            self.assertTrue(result.applied)
            self.assertEqual(result.blocked, [])
            self.assertEqual(Surface.load(d).manifest.profile, "full")

    def test_the_clean_apply_is_ledgered_when_a_ledger_is_given(self):
        clean = _community_stack("full")
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "minimal")
            led = AuditLedger.from_mokata_dir(os.path.join(d, MOKATA_DIR))
            result = apply_manifest(d, clean, assume_yes=True, force=True, ledger=led)

            self.assertTrue(result.applied)
            approved = [e for e in led.entries()
                        if e.get("kind") == "write_gate" and e.get("decision") == "approved"
                        and e.get("write_kind") == "config"]
            self.assertTrue(approved, "an applied stack is a governed config write (I3)")


# ======================================================================================
# ROUND-TRIP — a clean stack survives export -> import byte-identical
# ======================================================================================

class TestCleanRoundTrip(unittest.TestCase):

    def test_export_then_import_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
            surface = _repo(src, "full")
            shared = os.path.join(src, "stack.json")
            export_manifest(surface, dest=shared)
            with open(shared, encoding="utf-8") as fh:
                exported_bytes = fh.read()

            res = apply_manifest(dst, load_shared(shared), assume_yes=True)
            self.assertTrue(res.applied)
            with open(_manifest_path(dst), encoding="utf-8") as fh:
                self.assertEqual(fh.read(), exported_bytes,
                                 "a clean stack round-trips byte-identical — the scan adds nothing "
                                 "to the artifact and takes nothing out of it")


# ======================================================================================
# THE REGISTER — SI.6's frozen KNOWN_BYPASS shrinks by exactly these two
# ======================================================================================

class TestTheRegisterRecordsTheClosure(unittest.TestCase):
    """SI.6's register is deliberately frozen: closing a bypass MEANS editing it, which means
    someone reviews the edit. This is that review, pinned."""

    def test_the_stack_share_pair_is_no_longer_a_known_bypass(self):
        from test_si_6_writegate_side_doors import GATED, KNOWN_BYPASS
        for fn in ("export_manifest", "apply_manifest"):
            self.assertNotIn(("share.py", fn), KNOWN_BYPASS,
                             f"share.py:{fn} is scanned + gated as of SI.6b — it must leave the "
                             f"bypass register")

        # The export still owns its file write, so it registers under its own name (the
        # `memory/share.py:export_memory` shape: the scan lives here, both callers gate it).
        self.assertIn(("share.py", "export_manifest"), GATED)

        # The apply does NOT: its write moved into the commit closure the WriteGate runs, so
        # `apply_manifest` is no longer a durable-write site at all and the sweep sees `_commit`.
        # That is the canonical gated shape — the same one `config_cmd.py`, `team.py` and
        # `memory/store.py` register — and it is a stronger result than registering the public
        # function, because it is the shape `TestTheStoreCannotWriteOutsideACommitClosure` exists to
        # enforce: a write that cannot execute except inside a gate.
        # (Keyed `apply_manifest._commit` since 0.0.17 stage 1a's review made the register key the
        # QUALIFIED name — one entry per definition. Same site, same claim, spelled unambiguously.)
        self.assertIn(("share.py", "apply_manifest._commit"), GATED)

    def test_the_setup_cluster_that_si_6b_left_is_now_closed_by_kb_s1(self):
        """SI.6b left exactly 6 setup one-shots on the bypass register and claimed nothing more.
        KB.S1 (0.0.14) closes all 6 — each now keeps its bespoke TTY consent and leaves an audit
        record, moving to the LEDGERED register — so KNOWN_BYPASS is empty and this stack-share
        stage's remainder is gone. (This pin flips SI.6b's transient "6 remain" to its resolution;
        the live count lives in test_si_6's `test_the_known_bypass_register_is_empty`.)

        0.0.17 stage 1a's review touched this pin twice, and neither is a weakening: `init.py`'s
        key became QUALIFIED (`InitPlan.write_files` — one entry per definition), and
        `memory/reembed.py:run_reembed` JOINED the register, moved out of UNGATED_BY_DESIGN because
        its own reason read "human-gated, but NOT through the WriteGate … and it is ledgered",
        which is this register's definition verbatim. So the assertion below is no longer "LEDGERED
        IS the setup cluster" — it is "the setup cluster is all still there, and here is exactly
        what else is", which is the claim that can actually stay true.

        0.0.17 STAGE 16 adds the third non-setup member, `memory/migrate.py:_drop_source`, and this
        pin is why it is a REVIEWED addition rather than a silent one: it went RED the moment the
        entry appeared, which is exactly the job. It is `--drop-source`'s destructive delete, split
        out of `migrate_memory` so it could earn its own key (REGISTER-KEY-COLLISION), keeping its
        bespoke batch consent and gaining the `migrate_drop_source` audit record it had been
        missing since SI.6 (MIGRATE-DROP-SOURCE-UNLEDGERED, doc 84). Same shape as `run_reembed`:
        bespoke consent + a record + no WriteGate."""
        from test_si_6_writegate_side_doors import KNOWN_BYPASS, LEDGERED
        self.assertEqual(KNOWN_BYPASS, {},
                         "the 0.0.13 exit criterion: no durable write bypasses both gate and ledger")
        setup_cluster = [("agent_skills.py", "prune_orphan_skills"),
                         ("agent_skills.py", "write_skill_files"),
                         ("govern/lifecycle.py", "_remove"),
                         ("harness_setup.py", "_write_command_file"),
                         ("harness_setup.py", "_write_json"),
                         ("init.py", "InitPlan.write_files")]
        for site in setup_cluster:
            self.assertIn(site, LEDGERED,
                          f"{site} is one of KB.S1's 6 setup one-shots — recorded, not bypassing")
        self.assertEqual(
            sorted(LEDGERED), sorted(setup_cluster + [("memory/migrate.py", "_drop_source"),
                                                      ("memory/reembed.py", "run_reembed")]),
            "LEDGERED is KB.S1's 6 setup one-shots, `run_reembed` (0.0.17 stage 1a review) and "
            "`_drop_source` (0.0.17 stage 16). Anything else arriving here is a bespoke-consent "
            "durable write nobody reviewed into it.")


if __name__ == "__main__":
    unittest.main()
