"""Stage 71a — project-scoped shared backends (data-isolation correctness).

The shared Postgres tables (`mokata_memory`, `mokata_memory_vectors`, `mokata_session_bundle`,
and the Stage-71 `mokata_audit_log`) were OWNED/namespaced vs OTHER apps but NOT per-project — so
two projects on ONE DSN bled together. This stage scopes every shared row + query by a stable,
deterministic PROJECT KEY, and defaults review to the current project (with --all/--project/
--list-projects escapes).

Proven here (all behind a fake/injected client — no live DB):
  * `project_id` is stable/deterministic (git remote > repo path) + configurable
    (`settings.project.id`); ssh vs https clones agree;
  * two projects on the SAME shared backend (memory / vector / session) each read back ONLY their
    own rows — no bleed, and a COLLIDING session tag never clobbers;
  * review DEFAULTS to the current project; `--all` spans, `--project` selects;
  * from OUTSIDE a project the CLI requires --all/--project or lists projects (never a silent dump);
  * an old/unscoped (NULL-project) row degrades clean — hidden when scoped, surfaced under --all,
    no crash;
  * writes stay HUMAN-GATED + SECRET-SCANNED (scoping changes storage, not the gate);
  * 54e parity stays green.
"""

import io
import json
import os
import sys
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import project as P
from mokata.config import Surface
from mokata.memory.backends import PostgresBackend
from mokata.memory.item import MemoryItem
from mokata.memory.store import MemoryStore
from mokata.memory.vector import PgVectorBackend

_SECRET = "AKIA" + "IOSFODNN7" + "EXAMPLE" + "QWER"


# --------------------------------------------------------------------- fakes (project-aware)
class _Cur:
    def __init__(self, rows=None, rowcount=0):
        self._rows = list(rows or [])
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _FakeMemPg:
    """An in-memory stand-in for the mokata_memory / mokata_memory_vectors table — enough to prove
    the project column filters every read/write/delete. `rows`: id -> (doc, project). A row may be
    seeded with project=None to model a pre-71a (legacy) row."""

    def __init__(self):
        self.rows = {}
        self.updates_without_scope = 0

    def seed_legacy(self, item, project=None):
        self.rows[item.id] = (json.dumps(item.to_dict()), project)

    def execute(self, sql, params=None):
        h = " ".join(sql.split()).upper()
        params = params or ()
        if h.startswith(("CREATE TABLE", "ALTER TABLE", "CREATE EXTENSION",
                         "CREATE UNIQUE INDEX")):
            return _Cur()
        if h.startswith("INSERT"):
            # memory: (id,mtype,subject,status,doc,project) · vector adds embedding before project
            id_, doc, project = params[0], params[4], params[-1]
            self.rows[id_] = (doc, project)
            return _Cur(rowcount=1)
        if h.startswith("SELECT DISTINCT PROJECT"):
            return _Cur([(p,) for p in {pr for _d, pr in self.rows.values()}])
        if h.startswith("SELECT DOC,"):            # semantic_search: doc + score
            scoped = " WHERE PROJECT=" in h
            proj = params[1] if scoped else None
            out = [(doc, 0.5) for _id, (doc, pr) in self.rows.items()
                   if not scoped or pr == proj]
            return _Cur(out)
        if h.startswith("SELECT DOC"):
            if "WHERE ID=" in h:                   # get
                id_ = params[0]
                scoped = "PROJECT=" in h
                row = self.rows.get(id_)
                if row and (not scoped or row[1] == params[1]):
                    return _Cur([(row[0],)])
                return _Cur([])
            scoped = "WHERE PROJECT=" in h         # all
            proj = params[0] if scoped else None
            return _Cur([(doc,) for _id, (doc, pr) in self.rows.items()
                         if not scoped or pr == proj])
        if h.startswith("DELETE"):
            id_ = params[0]
            scoped = "PROJECT=" in h
            row = self.rows.get(id_)
            if row and (not scoped or row[1] == params[1]):
                del self.rows[id_]
                return _Cur(rowcount=1)
            return _Cur(rowcount=0)
        return _Cur()

    def close(self):
        pass


def _mem(project, conn):
    return PostgresBackend(project=project, conn=conn)


def _vec(project, conn):
    return PgVectorBackend(embedder=lambda _t: [0.0] * 8, dim=8, project=project, conn=conn)


def _repo(d, profile="standard"):
    from mokata.init import init_repo
    init_repo(root=d, profile=profile, assume_yes=True, out=lambda _: None)
    return Surface.load(d)


# ============================================================ project_id: stable + configurable
class TestProjectId(unittest.TestCase):
    def test_git_remote_makes_two_clones_agree(self):
        def git_origin(_args, _cwd):
            return (0, "git@github.com:acme/app.git\n")
        a = P.derive_project_id("/tmp/clone-one", git=git_origin)
        b = P.derive_project_id("/other/clone-two", git=git_origin)
        self.assertEqual(a, b)                 # same remote → same key, different paths
        self.assertTrue(a.startswith("p_"))

    def test_ssh_and_https_remotes_normalize_equal(self):
        ssh = P.normalize_remote("git@github.com:Acme/App.git")
        https = P.normalize_remote("https://github.com/acme/app")
        self.assertEqual(ssh, https)
        self.assertEqual(ssh, "github.com/acme/app")

    def test_no_remote_falls_back_to_path_and_is_deterministic(self):
        def no_git(_args, _cwd):
            return (128, "")
        one = P.derive_project_id("/repo/alpha", git=no_git)
        again = P.derive_project_id("/repo/alpha", git=no_git)
        other = P.derive_project_id("/repo/beta", git=no_git)
        self.assertEqual(one, again)           # stable across calls
        self.assertNotEqual(one, other)        # distinct repos → distinct keys

    def test_configured_id_overrides_the_derived_value(self):
        with tempfile.TemporaryDirectory() as d:
            from mokata import config_cmd
            surface = _repo(d)
            derived = P.project_id(surface)
            config_cmd.config_set(d, "settings.project.id", "team-canonical",
                                  assume_yes=True, out=lambda _m: None)
            surface2 = Surface.load(d)
            self.assertEqual(P.project_id(surface2), "team-canonical")
            self.assertNotEqual(P.project_id(surface2), derived)


# ============================================================ no cross-project bleed (memory)
class TestMemoryIsolation(unittest.TestCase):
    def test_two_projects_one_backend_read_only_their_own(self):
        pg = _FakeMemPg()                      # ONE shared table
        a = _mem("proj-A", pg)
        b = _mem("proj-B", pg)
        a.put(MemoryItem.create("k", "A-only"))
        b.put(MemoryItem.create("k", "B-only"))
        self.assertEqual([i.value for i in a.all()], ["A-only"])
        self.assertEqual([i.value for i in b.all()], ["B-only"])
        self.assertEqual(set(a.list_projects()), {"proj-A", "proj-B"})

    def test_get_and_delete_are_project_scoped(self):
        pg = _FakeMemPg()
        a = _mem("proj-A", pg)
        b = _mem("proj-B", pg)
        it = MemoryItem.create("shared-id", "A-only")
        a.put(it)
        # B cannot get or delete A's row (same id, different project)
        self.assertIsNone(b.get(it.id))
        self.assertFalse(b.delete(it.id))
        self.assertIsNotNone(a.get(it.id))     # A still has it (B's delete didn't touch it)


# ============================================================ no cross-project bleed (vector)
class TestVectorIsolation(unittest.TestCase):
    def test_all_and_semantic_search_are_project_scoped(self):
        pg = _FakeMemPg()
        a = _vec("proj-A", pg)
        b = _vec("proj-B", pg)
        a.put(MemoryItem.create("k", "A vector row"))
        b.put(MemoryItem.create("k", "B vector row"))
        self.assertEqual([i.value for i in a.all()], ["A vector row"])
        self.assertEqual([i.value for i in b.all()], ["B vector row"])
        hits = a.semantic_search("anything", top_k=5)
        self.assertEqual([it.value for it, _s in hits], ["A vector row"])   # no B bleed


# ============================================================ no cross-project bleed (session)
class _FakeSessionPg:
    """A composite-(project, tag)-keyed stand-in for mokata_session_bundle — models the Stage-71a
    scoping so two projects can hold the SAME tag without clobbering."""

    def __init__(self):
        self.rows = {}          # (project, tag) -> blob

    def execute(self, sql, params=None):
        h = " ".join(sql.split()).upper()
        params = params or ()
        if h.startswith(("CREATE TABLE", "ALTER TABLE", "CREATE UNIQUE INDEX")):
            return _Cur()
        if h.startswith("INSERT"):
            project, tag, blob = params
            self.rows[(project, tag)] = blob
            return _Cur(rowcount=1)
        if h.startswith("SELECT BLOB"):
            tag = params[0]
            scoped = len(params) > 1
            for (pr, tg), blob in self.rows.items():
                if tg == tag and (not scoped or pr == params[1]):
                    return _Cur([(blob,)])
            return _Cur([])
        if h.startswith("SELECT TAG"):
            scoped = bool(params)
            tags = [tg for (pr, tg) in self.rows if not scoped or pr == params[0]]
            return _Cur([(t,) for t in sorted(tags)])
        if h.startswith("SELECT DISTINCT PROJECT"):
            return _Cur([(p,) for p in {pr for (pr, _t) in self.rows}])
        if h.startswith("DELETE"):
            tag = params[0]
            scoped = len(params) > 1
            key = next(((pr, tg) for (pr, tg) in self.rows
                        if tg == tag and (not scoped or pr == params[1])), None)
            if key:
                del self.rows[key]
                return _Cur(rowcount=1)
            return _Cur(rowcount=0)
        return _Cur()

    def close(self):
        pass


class TestSessionIsolation(unittest.TestCase):
    def _pg(self):
        return _FakeSessionPg()

    def test_colliding_tag_across_projects_never_clobbers(self):
        from mokata import session_transport as ST
        pg = self._pg()
        a = ST.PostgresTransport(client=pg, project="proj-A")
        b = ST.PostgresTransport(client=pg, project="proj-B")
        a.write_bundle("auth", '{"who":"A"}')
        b.write_bundle("auth", '{"who":"B"}')          # SAME tag, different project
        self.assertEqual(a.read_bundle("auth"), '{"who":"A"}')
        self.assertEqual(b.read_bundle("auth"), '{"who":"B"}')   # no clobber
        self.assertEqual(a.list_tags(), ["auth"])
        self.assertEqual(b.list_tags(), ["auth"])
        self.assertEqual(set(a.list_projects()), {"proj-A", "proj-B"})

    def test_list_tags_scoped_by_default_spans_under_all(self):
        from mokata import session_transport as ST
        pg = self._pg()
        ST.PostgresTransport(client=pg, project="proj-A").write_bundle("a1", "{}")
        ST.PostgresTransport(client=pg, project="proj-B").write_bundle("b1", "{}")
        self.assertEqual(ST.PostgresTransport(client=pg, project="proj-A").list_tags(), ["a1"])
        # ALL_PROJECTS (None) spans every project
        self.assertEqual(sorted(ST.PostgresTransport(client=pg, project=None).list_tags()),
                         ["a1", "b1"])


# ============================================================ review scoping (default/all/project)
class TestReviewScoping(unittest.TestCase):
    def test_default_current_all_spans_project_selects(self):
        pg = _FakeMemPg()
        _mem("proj-A", pg).put(MemoryItem.create("k", "A-only"))
        _mem("proj-B", pg).put(MemoryItem.create("k", "B-only"))
        # default: current project only
        self.assertEqual([i.value for i in _mem("proj-A", pg).all()], ["A-only"])
        # --project selects a specific one
        self.assertEqual([i.value for i in _mem("proj-B", pg).all()], ["B-only"])
        # --all (project=None) spans everything
        self.assertEqual(sorted(i.value for i in _mem(None, pg).all()),
                         ["A-only", "B-only"])


# ============================================================ legacy / unscoped degrade-clean
class TestLegacyDegradeClean(unittest.TestCase):
    def test_null_project_row_hidden_when_scoped_surfaced_under_all(self):
        pg = _FakeMemPg()
        legacy = MemoryItem.create("old", "pre-71a row")
        pg.seed_legacy(legacy, project=None)           # a pre-scoping row (NULL project)
        # scoped read does NOT see the legacy row (no crash) …
        self.assertEqual(_mem("proj-A", pg).all(), [])
        # … but --all surfaces it, and it buckets under LEGACY in list_projects
        self.assertEqual([i.value for i in _mem(None, pg).all()], ["pre-71a row"])
        self.assertIn(P.LEGACY_PROJECT, _mem("proj-A", pg).list_projects())


# ============================================================ writes stay gated + secret-scanned
class TestWritesStillGated(unittest.TestCase):
    def _store(self, pg, project="proj-A"):
        return MemoryStore(_mem(project, pg), stats_store=None)

    def test_decline_writes_nothing(self):
        pg = _FakeMemPg()
        store = self._store(pg)
        res = store.remember(MemoryItem.create("k", "v"), confirm=lambda _q: False)
        self.assertFalse(res.committed)
        self.assertEqual(pg.rows, {})              # nothing stored

    def test_approved_write_is_scoped(self):
        pg = _FakeMemPg()
        store = self._store(pg)
        res = store.remember(MemoryItem.create("k", "v"), assume_yes=True)
        self.assertTrue(res.committed)
        self.assertEqual({pr for _d, pr in pg.rows.values()}, {"proj-A"})   # tagged w/ project

    def test_secret_is_hard_blocked_even_when_approved(self):
        pg = _FakeMemPg()
        store = self._store(pg)
        res = store.remember(MemoryItem.create("creds", f"key {_SECRET}"), assume_yes=True)
        self.assertFalse(res.committed)
        self.assertTrue(res.blocked)
        self.assertEqual(pg.rows, {})              # secret never reached the shared store


# ============================================================ CLI review surfaces
class TestCliReview(unittest.TestCase):
    def _run(self, argv):
        from mokata import cli
        out, err = io.StringIO(), io.StringIO()
        so, se = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        try:
            rc = cli.main(argv)
        finally:
            sys.stdout, sys.stderr = so, se
        return rc, out.getvalue(), err.getvalue()

    def test_memory_list_projects_on_local_backend_is_clean(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            rc, out, err = self._run(["memory", "--list-projects", "--path", d])
            self.assertEqual(rc, 0, err)
            self.assertIn("local", (out + err).lower())     # single-project, per-repo

    def test_session_outside_a_project_refuses_to_dump(self):
        with tempfile.TemporaryDirectory() as d:
            saved = {k: os.environ.get(k) for k in ("MOKATA_SESSION_PG_DSN", "MOKATA_PG_DSN")}
            os.environ["MOKATA_PG_DSN"] = "postgres://x"     # a shared DSN is configured
            os.environ.pop("MOKATA_SESSION_PG_DSN", None)
            try:
                # a bare dir, NOT a mokata project → must require a scope, never dump everything
                rc, out, err = self._run(["session", "list", "--path", d])
            finally:
                for k, v in saved.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
            self.assertEqual(rc, 2)
            self.assertIn("--all", out + err)
            self.assertIn("--project", out + err)


# ============================================================ 54e parity stays green
class TestParityGreen(unittest.TestCase):
    def test_parity_ok(self):
        from mokata import parity
        report = parity.verify_parity()
        self.assertTrue(report.ok, report.render())


if __name__ == "__main__":
    unittest.main()
