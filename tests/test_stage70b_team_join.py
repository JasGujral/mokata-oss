"""Stage 70b — guided team onboarding: `mokata team join`.

ONE guided command takes a new teammate from zero to fully wired — shared stack + shared memory +
shared vault + onboard — by orchestrating the EXISTING primitives in order. These tests prove:
  * `team join` runs the five steps IN ORDER (adopt → connect → vault → onboard → verify) and, on
    confirm (with the DSN/driver ready), wires the shared stack + shared memory + vault;
  * declining a step does NOTHING for that step and the flow stays clean (later steps still run);
  * a secret in adopted OR vault content is BLOCKED (nothing written for that step);
  * a missing DSN / vault ref / driver SKIPS that step (never blocks) and stays local;
  * the DSN secret is NEVER persisted (only the env-var name);
  * an idempotent re-join is a no-op (no duplicate writes);
  * the summary names what's wired vs pending/skipped;
  * `--yes` (assume_yes) drives it non-interactively; and 54e parity stays green.

53b lesson: confirm callables / EOF-style non-interactive declines are exercised so a decline is
the safe default (nothing wired).
"""

import json
import os
import tempfile
import unittest

from _support import sample_manifest_data  # noqa: F401  (path-fix side-effect)

from mokata import team
from mokata import MANIFEST_FILENAME, MOKATA_DIR
from mokata import vault as V
from mokata.config import Surface

# A fake DSN VALUE assembled from fragments so no literal credential lives in this file — it must
# NEVER reach the manifest (only the env-var NAME is stored).
_FAKE_DSN = "postgres://u" + ":" + "p" + "w@h:5432/db"
_DENY = lambda _t: False          # decline (also the EOF / non-interactive default)
_ALLOW = lambda _t: True


def _repo(d, profile="standard"):
    from mokata.init import init_repo
    init_repo(root=d, profile=profile, assume_yes=True, out=lambda _: None)
    return Surface.load(d)


def _manifest_text(root):
    with open(os.path.join(root, MOKATA_DIR, MANIFEST_FILENAME), encoding="utf-8") as fh:
        return fh.read()


def _shared_stack(d, *, with_pg=False, secret=False):
    """A shared stack file a teammate would publish; return its path."""
    src = _repo(os.path.join(d, "src_repo"))
    from mokata.share import export_manifest
    data = json.loads(json.dumps(export_manifest(src)))
    if with_pg:
        data.setdefault("tools", {})["postgres"] = {
            "provides": "memory_store", "kind": "external", "version": None,
            "detect": {"type": "python_module", "name": "psycopg"},
            "enabled": True, "config": {"dsn_env": "MOKATA_PG_DSN"}}
        data["capabilities"]["memory_store"]["fallback"] = \
            ["postgres"] + list(data["capabilities"]["memory_store"]["fallback"])
    path = os.path.join(d, "mokata-stack.json")
    with open(path, "w", encoding="utf-8") as fh:
        if secret:
            blob = json.dumps(data)
            fh.write(blob[:-1] + ', "leak": "' + _FAKE_DSN + '"}')
        else:
            fh.write(json.dumps(data, indent=2))
    return path


def _make_source_vault(root, name="payments-spec", body="# Spec\n\nAC1 -> test_ac1\n"):
    """Create a shared design/spec vault at `root/.mokata/vault/` (a teammate's vault to pull)."""
    os.makedirs(V.vault_dir(root), exist_ok=True)
    with open(os.path.join(V.vault_dir(root), f"{name}.md"), "w", encoding="utf-8") as fh:
        fh.write(body)
    index = {"schema_version": V.VAULT_SCHEMA_VERSION, "kind": V.VAULT_KIND,
             "entries": {name: {"name": name, "kind": "spec", "title": name, "author": "alice",
                                "source": "", "content_hash": V.content_hash(body),
                                "created_at": "2026-07-01", "updated_at": "2026-07-01",
                                "version": 1}}}
    V._save_index(root, index)
    return root


class _DriverReady:
    """Context manager: force psycopg 'present' + export the DSN so connect can go active."""
    def __init__(self):
        self._saved = None

    def __enter__(self):
        self._saved = team.driver_present
        team.driver_present = lambda: True
        os.environ["MOKATA_PG_DSN"] = _FAKE_DSN
        return self

    def __exit__(self, *exc):
        team.driver_present = self._saved
        os.environ.pop("MOKATA_PG_DSN", None)


class TestTeamJoinOrderAndWiring(unittest.TestCase):
    def test_runs_steps_in_order(self):
        with tempfile.TemporaryDirectory() as d:
            dest = _repo(os.path.join(d, "dest"))
            os.environ.pop("MOKATA_PG_DSN", None)
            res = team.team_join(dest.root, dest, None, assume_yes=True, out=lambda _m: None)
            self.assertEqual([s.name for s in res.steps], list(team.JOIN_STEP_NAMES))

    def test_wires_stack_memory_and_vault_on_confirm(self):
        with tempfile.TemporaryDirectory() as d:
            stack = _shared_stack(d, with_pg=True)
            vault_src = _make_source_vault(os.path.join(d, "vault_src"))
            dest = _repo(os.path.join(d, "dest"))
            with _DriverReady():
                res = team.team_join(dest.root, dest, stack, dsn_env="MOKATA_PG_DSN",
                                     vault_ref=vault_src, assume_yes=True, force=True,
                                     out=lambda _m: None)
            self.assertEqual(res.step("adopt").status, "wired")
            self.assertEqual(res.step("connect").status, "wired")
            self.assertEqual(res.step("vault").status, "wired")
            self.assertEqual(res.step("onboard").status, "pending")   # guided — always handed off
            # shared memory really wired in the manifest ...
            data = json.loads(_manifest_text(dest.root))
            self.assertEqual(data["tools"]["postgres"]["config"]["dsn_env"], "MOKATA_PG_DSN")
            self.assertIn("postgres", data["capabilities"]["memory_store"]["fallback"])
            # ... and the pulled vault artifact really landed locally.
            self.assertTrue(os.path.exists(
                os.path.join(V.vault_dir(dest.root), "payments-spec.md")))

    def test_vault_pull_writes_are_audit_logged(self):
        # Pre-release fix #1: `_join_vault` routes each artifact write through the universal
        # WriteGate, so pulling a vault artifact records a `write_gate` audit-ledger entry.
        from mokata.govern import AuditLedger
        with tempfile.TemporaryDirectory() as d:
            vault_src = _make_source_vault(os.path.join(d, "vault_src"))
            dest = _repo(os.path.join(d, "dest"))
            ledger = AuditLedger.from_mokata_dir(dest.mokata_dir)
            res = team.team_join(dest.root, dest, None, vault_ref=vault_src, assume_yes=True,
                                 force=True, out=lambda _m: None, ledger=ledger)
            self.assertEqual(res.step("vault").status, "wired")
            vault_writes = [e for e in ledger.entries()
                            if e.get("kind") == "write_gate"
                            and e.get("actor") == "team-join"
                            and str(e.get("target", "")).endswith("payments-spec.md")]
            self.assertTrue(vault_writes, "no audit entry recorded for the vault artifact write")
            self.assertEqual(vault_writes[0]["decision"], "approved")

    def test_dsn_secret_never_persisted(self):
        with tempfile.TemporaryDirectory() as d:
            stack = _shared_stack(d, with_pg=True)
            dest = _repo(os.path.join(d, "dest"))
            with _DriverReady():
                team.team_join(dest.root, dest, stack, dsn_env="MOKATA_PG_DSN",
                               assume_yes=True, force=True, out=lambda _m: None)
            text = _manifest_text(dest.root)
            self.assertIn("MOKATA_PG_DSN", text)      # the env-var NAME (pointer) is stored
            self.assertNotIn(_FAKE_DSN, text)         # the DSN VALUE is NEVER stored
            self.assertNotIn("pw@h", text)


class TestTeamJoinDeclineAndBlock(unittest.TestCase):
    def test_declining_adopt_writes_nothing_but_flow_continues(self):
        with tempfile.TemporaryDirectory() as d:
            stack = _shared_stack(d, with_pg=True)     # differs from dest → reaches the gate
            dest = _repo(os.path.join(d, "dest"))
            before = _manifest_text(dest.root)
            res = team.team_join(dest.root, dest, stack, confirm=_DENY, force=True,
                                 out=lambda _m: None)
            self.assertEqual(res.step("adopt").status, "declined")
            self.assertEqual(_manifest_text(dest.root), before)   # decline → nothing wired
            # the flow still ran the remaining steps cleanly
            self.assertEqual([s.name for s in res.steps], list(team.JOIN_STEP_NAMES))
            self.assertEqual(res.step("verify").status, "verified")

    def test_secret_in_stack_is_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            stack = _shared_stack(d, secret=True)
            dest = _repo(os.path.join(d, "dest"))
            before = _manifest_text(dest.root)
            res = team.team_join(dest.root, dest, stack, assume_yes=True, force=True,
                                 out=lambda _m: None)
            self.assertEqual(res.step("adopt").status, "blocked")
            self.assertEqual(_manifest_text(dest.root), before)   # blocked → nothing wired

    def test_secret_in_vault_is_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            vault_src = _make_source_vault(os.path.join(d, "vault_src"),
                                           body="# Spec\nleak = " + _FAKE_DSN + "\n")
            dest = _repo(os.path.join(d, "dest"))
            res = team.team_join(dest.root, dest, None, vault_ref=vault_src, assume_yes=True,
                                 out=lambda _m: None)
            self.assertEqual(res.step("vault").status, "blocked")
            # nothing pulled locally
            self.assertFalse(os.path.exists(
                os.path.join(V.vault_dir(dest.root), "payments-spec.md")))

    def test_declining_vault_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            vault_src = _make_source_vault(os.path.join(d, "vault_src"))
            dest = _repo(os.path.join(d, "dest"))
            res = team.team_join(dest.root, dest, None, vault_ref=vault_src, confirm=_DENY,
                                 out=lambda _m: None)
            self.assertEqual(res.step("vault").status, "declined")
            self.assertFalse(os.path.exists(
                os.path.join(V.vault_dir(dest.root), "payments-spec.md")))


class TestTeamJoinDegradeClean(unittest.TestCase):
    def test_missing_dsn_skips_connect_and_stays_local(self):
        with tempfile.TemporaryDirectory() as d:
            dest = _repo(os.path.join(d, "dest"))
            os.environ.pop("MOKATA_PG_DSN", None)          # no DSN exported
            res = team.team_join(dest.root, dest, None, dsn_env="MOKATA_PG_DSN",
                                 assume_yes=True, out=lambda _m: None)
            self.assertEqual(res.step("connect").status, "skipped")
            self.assertIn("local", res.step("connect").detail.lower())
            # stayed local — no postgres wired
            data = json.loads(_manifest_text(dest.root))
            self.assertNotIn("postgres", (data.get("tools") or {}))

    def test_missing_vault_ref_skips_vault(self):
        with tempfile.TemporaryDirectory() as d:
            dest = _repo(os.path.join(d, "dest"))
            res = team.team_join(dest.root, dest, None, assume_yes=True, out=lambda _m: None)
            self.assertEqual(res.step("vault").status, "skipped")

    def test_missing_driver_skips_connect(self):
        with tempfile.TemporaryDirectory() as d:
            dest = _repo(os.path.join(d, "dest"))
            os.environ["MOKATA_PG_DSN"] = _FAKE_DSN         # DSN set ...
            saved = team.driver_present
            team.driver_present = lambda: False             # ... but no driver
            try:
                res = team.team_join(dest.root, dest, None, dsn_env="MOKATA_PG_DSN",
                                     assume_yes=True, out=lambda _m: None)
            finally:
                team.driver_present = saved
                os.environ.pop("MOKATA_PG_DSN", None)
            self.assertEqual(res.step("connect").status, "skipped")
            self.assertIn("psycopg", res.step("connect").detail.lower())

    def test_absent_stack_source_skips_adopt_never_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            dest = _repo(os.path.join(d, "dest"))
            res = team.team_join(dest.root, dest, os.path.join(d, "nope.json"),
                                 assume_yes=True, out=lambda _m: None)
            self.assertEqual(res.step("adopt").status, "skipped")
            # the rest of the flow still completed
            self.assertEqual(res.step("verify").status, "verified")


class TestTeamJoinIdempotentAndSummary(unittest.TestCase):
    def test_idempotent_rejoin_is_a_noop(self):
        with tempfile.TemporaryDirectory() as d:
            stack = _shared_stack(d, with_pg=True)
            vault_src = _make_source_vault(os.path.join(d, "vault_src"))
            dest = _repo(os.path.join(d, "dest"))
            with _DriverReady():
                team.team_join(dest.root, dest, stack, dsn_env="MOKATA_PG_DSN",
                               vault_ref=vault_src, assume_yes=True, force=True,
                               out=lambda _m: None)
                after_first = _manifest_text(dest.root)
                vault_first = sorted(os.listdir(V.vault_dir(dest.root)))
                # re-join — everything already in sync
                res = team.team_join(dest.root, dest, stack, dsn_env="MOKATA_PG_DSN",
                                     vault_ref=vault_src, assume_yes=True, force=True,
                                     out=lambda _m: None)
            self.assertEqual(_manifest_text(dest.root), after_first)   # no manifest churn
            self.assertEqual(sorted(os.listdir(V.vault_dir(dest.root))), vault_first)
            self.assertEqual(res.step("adopt").status, "wired")        # reported as in-sync
            self.assertEqual(res.step("connect").status, "wired")
            self.assertIn("sync", res.step("vault").detail.lower())    # nothing to pull

    def test_summary_names_wired_and_pending(self):
        with tempfile.TemporaryDirectory() as d:
            dest = _repo(os.path.join(d, "dest"))
            os.environ.pop("MOKATA_PG_DSN", None)
            res = team.team_join(dest.root, dest, None, assume_yes=True, out=lambda _m: None)
            summary = res.summary()
            self.assertIn("wired to", summary.lower())
            self.assertIn("connect", summary)                 # a skipped step is named ...
            self.assertIn("onboard", summary)                 # ... and the pending onboard
            self.assertIn("still open", summary.lower())
            self.assertIn(team.honest_note()[:20], summary)   # honest note reused

    def test_yes_flag_drives_it_noninteractively(self):
        with tempfile.TemporaryDirectory() as d:
            stack = _shared_stack(d, with_pg=True)
            dest = _repo(os.path.join(d, "dest"))
            # no confirm callable at all — assume_yes must carry the writing steps
            res = team.team_join(dest.root, dest, stack, assume_yes=True, force=True,
                                 out=lambda _m: None)
            self.assertEqual(res.step("adopt").status, "wired")


class TestTeamJoinCliAndParity(unittest.TestCase):
    def test_parity_stays_green(self):
        from mokata import parity
        self.assertIn("team", parity.SURFACE_MATRIX)
        report = parity.verify_parity()
        self.assertTrue(report.ok, report.render())

    def test_cli_join_action_runs(self):
        import io
        from contextlib import redirect_stdout
        from mokata.cli import cmd_team

        class A:
            pass
        with tempfile.TemporaryDirectory() as d:
            dest = _repo(os.path.join(d, "dest"))
            a = A()
            a.path = dest.root
            a.action = "join"
            a.source = None
            a.dsn_env = None
            a.vault = None
            a.yes = True
            a.force = False
            os.environ.pop("MOKATA_PG_DSN", None)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_team(a)
            self.assertEqual(rc, 0)
            self.assertIn("team join", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
