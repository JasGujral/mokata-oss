"""TM.S4 — the HARDENED joiner: a new teammate reaches CONNECTED without reading source.

A joiner points at an ALREADY-provisioned shared DB (the first person ran `team init`) and:
  * gives the DSN via an env-var NAME only — an inline DSN value is refused (secret-scanned),
  * INHERITS the shared project identity (`settings.project.id`) from the adopted stack — it
    never re-pins/forks it (doc 48 C2/P-7),
  * runs the TM.S2 preflight → CONNECTED (it NEVER runs DDL — that's `team init`),
  * FAILS CLOSED with the S2 named fixes (unreachable/pooler-trap, schema-absent → "ask whoever
    ran team init", incompatible-version), writing no durable activation on failure,
  * and every team-mode error links the canonical "Team mode: setup & operations" page.

The probe is injected so this needs no live Postgres.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import os
import tempfile
import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata import MANIFEST_FILENAME, MOKATA_DIR, team, team_docs, teamdb
from mokata.config import Surface
from mokata.project import project_id


_SHARED_PID = "p_sharedteam0001"
_REACHABLE = teamdb.ProbeResult(reachable=True, schema_present=True,
                                schema_version=teamdb.TEAM_SCHEMA_VERSION, compatible=True,
                                detail="reachable + schema compatible")
_UNREACHABLE = teamdb.ProbeResult(reachable=False, compatible=False, error="timeout",
                                  detail="unreachable — no response within 500ms")
_UNPROVISIONED = teamdb.ProbeResult(reachable=True, schema_present=False, compatible=False,
                                    detail="reachable, but the shared schema is not provisioned")
_INCOMPATIBLE = teamdb.ProbeResult(reachable=True, schema_present=True, schema_version=99,
                                   compatible=False, detail="reachable, but v99 != v1")


def _repo(d, profile="standard"):
    from mokata.init import init_repo
    init_repo(root=d, profile=profile, assume_yes=True, out=lambda _: None)
    return Surface.load(d)


def _manifest(root):
    with open(os.path.join(root, MOKATA_DIR, MANIFEST_FILENAME), encoding="utf-8") as fh:
        return json.load(fh)


def _shared_stack(d, *, pid=_SHARED_PID):
    """A stack a `team init`'d teammate would publish: it carries the pinned project id + the
    shared-memory (env-var) pointer, exactly what a joiner inherits."""
    src = _repo(os.path.join(d, "src_repo"))
    from mokata.share import export_manifest
    data = json.loads(json.dumps(export_manifest(src)))
    data.setdefault("settings", {}).setdefault("project", {})["id"] = pid
    data.setdefault("tools", {})["postgres"] = {
        "provides": "memory_store", "kind": "external", "version": None,
        "detect": {"type": "python_module", "name": "psycopg"},
        "enabled": True, "config": {"dsn_env": "MOKATA_PG_DSN"}}
    data["capabilities"]["memory_store"]["fallback"] = \
        ["postgres"] + list(data["capabilities"]["memory_store"]["fallback"])
    path = os.path.join(d, "mokata-stack.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(data, indent=2))
    return path


class _Env:
    """Set MOKATA_PG_DSN for the duration of a joiner run (the VALUE never reaches the manifest)."""
    def __init__(self, val="postgres://joiner@shared/db"):
        self.val = val

    def __enter__(self):
        os.environ["MOKATA_PG_DSN"] = self.val
        return self

    def __exit__(self, *exc):
        os.environ.pop("MOKATA_PG_DSN", None)


def _join(dest, stack, probe_result, **kw):
    return team.team_join(dest.root, dest, stack, dsn_env="MOKATA_PG_DSN", assume_yes=True,
                          force=True, out=lambda _m: None,
                          probe=(lambda dsn, **k: probe_result) if probe_result else None, **kw)


class TestJoinerInheritsIdentity(unittest.TestCase):
    def test_joiner_inherits_project_id_and_never_repins(self):
        with tempfile.TemporaryDirectory() as d:
            stack = _shared_stack(d)
            dest = _repo(os.path.join(d, "dest"))
            with _Env():
                _join(dest, stack, _REACHABLE)
            # the pinned team id came across in the adopt — the joiner did NOT fork a new one.
            pinned = (_manifest(dest.root).get("settings") or {}).get("project", {}).get("id")
            self.assertEqual(pinned, _SHARED_PID)
            # and clients resolve the SHARED identity (not this repo's path-hash).
            self.assertEqual(project_id(Surface.load(dest.root)), _SHARED_PID)

    def test_joiner_never_runs_ddl(self):
        # Joiners never provision — that's `team init` (TM.S3/C4). Make provision explode; a full
        # join must never touch it.
        called = {"n": 0}

        def _boom(*a, **k):
            called["n"] += 1
            raise AssertionError("joiner ran DDL — it must never provision")
        saved = teamdb.provision
        teamdb.provision = _boom
        try:
            with tempfile.TemporaryDirectory() as d:
                stack = _shared_stack(d)
                dest = _repo(os.path.join(d, "dest"))
                with _Env():
                    _join(dest, stack, _REACHABLE)
        finally:
            teamdb.provision = saved
        self.assertEqual(called["n"], 0)


class TestJoinerReachesConnected(unittest.TestCase):
    def test_reachable_compatible_activates_team(self):
        with tempfile.TemporaryDirectory() as d:
            stack = _shared_stack(d)
            dest = _repo(os.path.join(d, "dest"))
            with _Env():
                res = _join(dest, stack, _REACHABLE)
            self.assertEqual(res.step("activate").status, "verified")
            self.assertEqual((_manifest(dest.root).get("settings") or {}).get("mode"), "team")


class TestJoinerFailsClosed(unittest.TestCase):
    def _fails(self, probe_result, needle):
        with tempfile.TemporaryDirectory() as d:
            stack = _shared_stack(d)
            dest = _repo(os.path.join(d, "dest"))
            with _Env():
                res = _join(dest, stack, probe_result)
            step = res.step("activate")
            self.assertEqual(step.status, "problems")
            # no durable activation on failure.
            self.assertNotEqual((_manifest(dest.root).get("settings") or {}).get("mode"), "team")
            self.assertIn(needle, step.detail.lower())
            self.assertIn(team_docs.TEAM_DOCS_URL, step.detail)   # links the page
            return step

    def test_unreachable_names_pooler_trap(self):
        self._fails(_UNREACHABLE, "direct")

    def test_schema_absent_tells_you_to_ask_whoever_ran_team_init(self):
        step = self._fails(_UNPROVISIONED, "team init")
        self.assertIn("ask", step.detail.lower())   # joiners never run init themselves

    def test_incompatible_version_named(self):
        self._fails(_INCOMPATIBLE, "99")


class TestJoinerSecretScansDsn(unittest.TestCase):
    def test_inline_dsn_is_refused_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            stack = _shared_stack(d)
            dest = _repo(os.path.join(d, "dest"))
            mpath = os.path.join(dest.root, MOKATA_DIR, MANIFEST_FILENAME)
            with open(mpath, "rb") as fh:
                before = fh.read()
            out = []
            # a DSN VALUE where an env-var NAME is expected → refused, nothing written.
            res = team.team_join(dest.root, dest, stack,
                                 dsn_env="postgres://user:pw@host:5432/db",
                                 assume_yes=True, force=True, out=out.append)
            text = "\n".join(out)
            self.assertTrue(res.aborted)
            with open(mpath, "rb") as fh:
                self.assertEqual(before, fh.read())
            self.assertIn(team_docs.TEAM_DOCS_URL, text)
            self.assertIn("env-var name", text.lower())


if __name__ == "__main__":
    unittest.main()
