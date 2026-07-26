"""KB.S1 — ledger-gate the KNOWN_BYPASS setup one-shots (0.0.14).

SI.6's zero-bypass sweep found 8 durable writers outside every gate; SI.6b closed the stack-share
pair; 6 SETUP one-shots remained (init · harness setup · skills · reset). This stage closes those 6:
each keeps its existing bespoke human-at-TTY consent and now GAINS a durable audit record (P7 —
every durable write leaves a record), so the frozen KNOWN_BYPASS register EMPTIES.

Two hard parts, answered:
  * BOOTSTRAP ORDERING — init runs before any ledger exists. The ledger's own creation IS its first
    entry: `write_files` records on `.mokata/temp_local/audit/ledger.jsonl` the moment the config
    is on disk.
  * UNSETUP — a reset deletes `.mokata/`, which deletes the ledger that would record the delete. The
    removal is recorded in a USER-scoped tombstone (`~/.mokata/removals.json`) the removal can't
    reach.

Records name PATHS + identities only — never file contents or env values.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import MOKATA_DIR
from mokata.agent_skills import SKILL_MARKER, skill_markdown
from mokata.govern.ledger import AuditLedger
from mokata.govern.lifecycle import _tombstone_path, reset_state
from mokata.harness_setup import (_templates_dir, apply_setup, plan_setup,
                                   setup_harness, unsetup_harness)
from mokata.init import init_repo
from mokata.skills import expand_grounding


def _ledger(root):
    return AuditLedger.from_mokata_dir(os.path.join(root, MOKATA_DIR))


def _entries(root):
    return _ledger(root).entries()


def _setup_records(root):
    return [e for e in _entries(root) if e.get("kind") == "setup"]


def _json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _init_repo(d):
    return init_repo(root=d, profile="standard", assume_yes=True, out=lambda *_a: None)


def _apply_full_setup(d):
    """Drive apply_setup directly (writes + records, no MCP-reachability probe) — inits on a fresh
    dir, so the ledger is created here too."""
    plan = plan_setup("claude", root=d, scope="project", profile="standard", home=d)
    apply_setup(plan, assume_yes=True, out=lambda *_a: None)
    return plan


# ======================================================================================
# THE REGRESSION — each of the 6 setup one-shots now leaves its audit record
# ======================================================================================

class TestEachSetupWriterLeavesItsRecord(unittest.TestCase):
    """Per-writer named asserts. Fails on old code: before KB.S1 none of these writers touched the
    ledger (that was precisely their KNOWN_BYPASS filing)."""

    def test_init_write_files_records_a_setup_entry_naming_the_files(self):
        with tempfile.TemporaryDirectory() as d:
            _init_repo(d)
            recs = [e for e in _setup_records(d) if e.get("action") == "init"]
            self.assertTrue(recs, "init.write_files must leave a `setup`/init audit record")
            files = recs[-1]["files"]
            self.assertTrue(any(f.endswith("manifest.json") for f in files))
            self.assertTrue(any(f.endswith("constitution.md") for f in files))
            self.assertEqual(recs[-1]["profile"], "standard")

    def test_harness_write_json_is_recorded_by_the_setup_flow(self):
        with tempfile.TemporaryDirectory() as d:
            _apply_full_setup(d)
            rec = [e for e in _setup_records(d) if e.get("action") == "setup"][-1]
            files = rec["files"]
            self.assertTrue(any(f.endswith(".mcp.json") for f in files),
                            "the _write_json MCP registration must be named in the setup record")
            self.assertTrue(any(f.endswith("settings.json") for f in files),
                            "the _write_json settings/hooks/grant write must be named")

    def test_harness_write_command_file_is_recorded_by_the_setup_flow(self):
        with tempfile.TemporaryDirectory() as d:
            _apply_full_setup(d)
            rec = [e for e in _setup_records(d) if e.get("action") == "setup"][-1]
            self.assertTrue(any(f.endswith(os.path.join("commands", "brainstorm.md"))
                                for f in rec["files"]),
                            "a materialized command file must be named in the setup record")

    def test_agent_skills_write_skill_files_is_recorded_by_the_setup_flow(self):
        with tempfile.TemporaryDirectory() as d:
            _apply_full_setup(d)
            rec = [e for e in _setup_records(d) if e.get("action") == "setup"][-1]
            self.assertTrue(any(f.endswith(os.path.join("brainstorm", "SKILL.md"))
                                for f in rec["files"]),
                            "a written SKILL.md must be named in the setup record")

    def test_agent_skills_prune_orphan_skills_is_recorded(self):
        with tempfile.TemporaryDirectory() as d:
            _apply_full_setup(d)
            # Plant a marker-bearing mokata orphan (a curated skill dropped from the set), then
            # re-run setup — the sync prune removes it, and the record must name the removal.
            skills_dir = os.path.join(d, ".claude", "skills")
            orphan = os.path.join(skills_dir, "zzz_gone", "SKILL.md")
            os.makedirs(os.path.dirname(orphan), exist_ok=True)
            with open(orphan, "w", encoding="utf-8") as fh:
                fh.write("---\nname: zzz_gone\n---\n" + SKILL_MARKER + "\n")
            self.assertTrue(os.path.exists(orphan))

            _apply_full_setup(d)
            self.assertFalse(os.path.exists(orphan), "the orphan must be pruned by the sync")
            pruned_named = [e for e in _setup_records(d)
                            if e.get("removed") and any("zzz_gone" in p for p in e["removed"])]
            self.assertTrue(pruned_named,
                            "prune_orphan_skills must be recorded — the removed dir named")

    def test_lifecycle_remove_is_recorded_in_the_tombstone(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            _init_repo(d)
            res = reset_state(d, assume_yes=True, user_home=home)
            self.assertTrue(res.removed)
            tomb = _tombstone_path(home)
            self.assertTrue(os.path.exists(tomb), "_remove must be recorded in the tombstone")
            data = _json(tomb)
            self.assertEqual(data[-1]["repo"], os.path.abspath(d))
            self.assertEqual(data[-1]["actor"], "cli")


# ======================================================================================
# BOOTSTRAP ORDERING — a fresh-dir init leaves a valid, records-carrying ledger
# ======================================================================================

class TestBootstrapOrdering(unittest.TestCase):

    def test_fresh_init_creates_the_ledger_and_it_carries_the_init_record(self):
        with tempfile.TemporaryDirectory() as d:
            # Fresh dir — no .mokata, no ledger.
            led = _ledger(d)
            self.assertEqual(led.entries(), [], "precondition: no ledger before init")

            _init_repo(d)

            after = _ledger(d)
            self.assertTrue(os.path.exists(after.path),
                            "the ledger must EXIST after a fresh init (its own creation is entry 1)")
            recs = [e for e in after.entries() if e.get("kind") == "setup"]
            self.assertTrue(recs, "the ledger must carry the bootstrap init record")
            self.assertEqual(recs[0]["seq"], 1, "the init record is the ledger's first entry")


# ======================================================================================
# RESET / UNSETUP — consent asked, tombstone written and SURVIVES the removal
# ======================================================================================

class TestResetUnsetup(unittest.TestCase):

    def test_cli_reset_asks_consent_writes_a_tombstone_that_survives_removal(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            _init_repo(d)
            asked = {"n": 0}

            def _confirm(_text):
                asked["n"] += 1
                return True

            res = reset_state(d, confirm=_confirm, user_home=home)
            self.assertEqual(asked["n"], 1, "consent must be asked (fail-closed off a TTY)")
            self.assertFalse(os.path.exists(os.path.join(d, MOKATA_DIR)),
                             ".mokata (and its ledger) is gone")
            tomb = _tombstone_path(home)
            self.assertTrue(os.path.exists(tomb),
                            "THE unsetup answer: the tombstone SURVIVES the removal that erased the "
                            "repo's own ledger")
            data = _json(tomb)
            self.assertEqual(data[-1]["repo"], os.path.abspath(d))

    def test_declining_reset_removes_nothing_and_writes_no_tombstone(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            _init_repo(d)
            res = reset_state(d, confirm=lambda _t: False, user_home=home)
            self.assertTrue(res.aborted)
            self.assertTrue(os.path.exists(os.path.join(d, MOKATA_DIR)), "nothing removed")
            self.assertFalse(os.path.exists(_tombstone_path(home)),
                             "a declined reset must leave no record-of-a-removal")

    def test_mcp_reset_propose_parity_is_unchanged(self):
        """PARITY: the MCP reset is still PROPOSE-ONLY — with no approval it stages nothing, writes
        nothing, removes nothing (SI.3 unchanged). KB.S1 does not touch the MCP consent flow.

        (NOTE: the full MCP commit round-trip has a PRE-EXISTING crash unrelated to KB.S1 — the
        `_gated_write` approved-record is written to the repo ledger AFTER `_do_reset` deletes
        `.mokata`, so `AuditLedger.record` opens a path that no longer exists. It reproduces on
        pre-KB.S1 `src/` and lives in the gate/approval-redemption path this stage is told not to
        touch; flagged for Jas. The tombstone is written by the shared `reset_state` BEFORE that
        crash, so the removal is still recorded — see the actor=mcp test below.)"""
        from mokata.mcp import tools_write as TW
        with tempfile.TemporaryDirectory() as d:
            _init_repo(d)
            out = TW.reset(path=d)
            self.assertEqual(out.get("status"), "proposed")
            self.assertFalse(out.get("committed"))
            self.assertTrue(os.path.exists(os.path.join(d, MOKATA_DIR)),
                            "propose-only: nothing removed without a human-minted approval")

    def test_the_shared_reset_path_the_mcp_uses_records_actor_mcp(self):
        """The MCP reset commits via the SAME `reset_state` the CLI uses, tagged actor=mcp — so its
        removal lands in the tombstone with the right surface."""
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            _init_repo(d)
            reset_state(d, assume_yes=True, actor="mcp", user_home=home)
            data = _json(_tombstone_path(home))
            self.assertEqual(data[-1]["actor"], "mcp")
            self.assertEqual(data[-1]["repo"], os.path.abspath(d))

    def test_unsetup_records_the_removal_on_the_repo_ledger(self):
        with tempfile.TemporaryDirectory() as d:
            _apply_full_setup(d)
            unsetup_harness("claude", root=d, scope="project", assume_yes=True,
                            home=d, out=lambda *_a: None)
            recs = [e for e in _setup_records(d) if e.get("action") == "unsetup"]
            self.assertTrue(recs, "unsetup must leave an audit record on the repo ledger")
            self.assertTrue(recs[-1].get("removed"), "and it names what it removed")


# ======================================================================================
# CONSENT NEGATIVES — a declined setup/init writes nothing and records nothing
# ======================================================================================

class TestConsentNegatives(unittest.TestCase):
    """Pick: a DECLINED act logs NOTHING (the record is written only after the durable act it
    describes completes; a decline returns before the write is reached)."""

    def test_declining_init_writes_nothing_and_records_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            res = init_repo(root=d, profile="standard", confirm=lambda _t: False,
                            out=lambda *_a: None)
            self.assertTrue(res.aborted)
            self.assertFalse(os.path.exists(os.path.join(d, MOKATA_DIR, "manifest.json")))
            self.assertEqual(_entries(d), [], "no write ⇒ no record-of-a-write")

    def test_declining_setup_writes_nothing_and_records_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            res = setup_harness("claude", root=d, scope="project", home=d,
                                confirm=lambda _t: False, out=lambda *_a: None)
            self.assertTrue(res.aborted)
            self.assertFalse(os.path.exists(os.path.join(d, ".claude", "commands")))
            self.assertEqual(_entries(d), [],
                             "a declined setup never inits, writes, or records")


# ======================================================================================
# HASH-CHAIN + SECRET-SAFETY + BYTE-IDENTICAL negatives
# ======================================================================================

class TestRecordIntegrity(unittest.TestCase):

    def test_the_new_records_chain_correctly(self):
        """MS.S3 pin — the setup/init records are real hash-chained entries."""
        with tempfile.TemporaryDirectory() as d:
            _apply_full_setup(d)
            report = _ledger(d).verify()
            self.assertTrue(report.intact, report.render())
            self.assertGreater(report.checked, 0)

    def test_records_carry_paths_not_file_contents(self):
        with tempfile.TemporaryDirectory() as d:
            _apply_full_setup(d)
            blob = json.dumps(_setup_records(d))
            # A distinctive snippet of the scaffolded constitution content must NOT appear in any
            # record — records name files, never their bytes.
            self.assertNotIn("Human-gate every durable write", blob,
                             "records must carry paths/identities, never file contents")
            self.assertNotIn("mcpServers", blob,
                             "no config-file body leaks into the record")

    def test_setup_file_contents_are_byte_identical_to_their_generators(self):
        """The added records change nothing about WHAT is written: each writer's output still equals
        exactly what its generator produces (a command == the expanded template; a SKILL.md == the
        rendered skill)."""
        with tempfile.TemporaryDirectory() as d:
            _apply_full_setup(d)
            tdir = _templates_dir()

            cmd = os.path.join(d, ".claude", "commands", "brainstorm.md")
            with open(cmd, encoding="utf-8") as fh:
                got = fh.read()
            expected = expand_grounding((tdir / "brainstorm.md").read_text(encoding="utf-8"))
            self.assertEqual(got, expected, "command file byte-identical to the expanded template")

            skill = os.path.join(d, ".claude", "skills", "brainstorm", "SKILL.md")
            with open(skill, encoding="utf-8") as fh:
                got_skill = fh.read()
            self.assertEqual(got_skill, skill_markdown("brainstorm", tdir),
                             "SKILL.md byte-identical to the rendered skill")


if __name__ == "__main__":
    unittest.main()
