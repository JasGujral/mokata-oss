"""DB.S4 — pgvector WIRED + the consented embeddings tier.

The stage's claim is that mokata's semantic tier now actually ships: an opted-in team config
REACHES `PgVectorBackend` (it was export-only through 0.0.14 — the D5-rider(3) marker said so in
`tiered.py`), a real embedder installs on explicit consent, and every path degrades honestly to
hashing and then to lexical rather than lying about retrieval quality.

Eight things are pinned here, and each exists because its absence is a specific silent failure:

  1. WIRING      — an opted-in config selects PgVectorBackend and `tiered_recall` uses its
                   index-backed `semantic_search`; a NON-opted config is byte-identical (ADR-54:
                   the core path stays extension-free).
  2. SEAM        — model2vec when importable, hashing when not, hashing + a LOUD notice when the
                   extra is installed but its model can't load (bounded, never a hang).
  3. STAMP       — an index stamped by embedder A REFUSES embedder B, names the migration, and
                   mixes NOTHING. This is the one that matters most: mixed-embedder cosine does
                   not error, it just ranks wrong forever.
  4. RE-EMBED    — previewed, gated, restamped; a decline writes zero.
  5. CONSENT     — accept ⇒ pip (mocked) + verify + ledger; decline ⇒ ledgered once, NEVER
                   re-asked; pip failure ⇒ degrade-clean.
  6. DOCTOR      — every tier combination renders its honest line; exit code untouched.
  7. FUSION+SCOPE— semantic still outranks lexical with the real seam, and a semantic hit still
                   cannot cross a project boundary.
  8. SECRETS     — DSNs and memory content never reach a finding or the ledger.

No live Postgres in CI, so the pgvector leg rides `_PgVectorShim` (the DB.S2a/DB.S3 precedent):
the backend's REAL SQL runs on SQLite with `<=>` emulated as cosine distance. It proves the SQL is
valid, the scope predicate composes, the ORDER BY ranks and every value binds — NOT pgvector's own
index recall. That is the `MOKATA_TEST_DSN` live leg, skipped here.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import math
import os
import re
import sqlite3
import subprocess
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import extras_install, teamdb
from mokata.memory import embed, reembed, selection, tier_report, tiered
from mokata.memory.embed import (
    EMBEDDINGS_EXTRA,
    HASHING_ID,
    HashingEmbedder,
    ModelUnavailable,
    detect_embedder,
    embedder_identity,
    make_embedder,
)
from mokata.memory.item import ACTIVE, MemoryItem
from mokata.memory.vector import (
    MAX_TOP_K,
    EmbedderStampMismatch,
    PgVectorBackend,
    build_pgvector_backend,
)


# ================================================================== the pgvector test double
def _parse_vec(lit):
    return [float(x) for x in (lit or "[]").strip("[]").split(",") if x.strip()]


def _cos_distance(a_lit, b_lit):
    """pgvector's `<=>` — cosine DISTANCE (0 = identical), the operator `semantic_search` orders by."""
    a, b = _parse_vec(a_lit), _parse_vec(b_lit)
    if not a or len(a) != len(b):
        return 1.0
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    if not na or not nb:
        return 1.0
    return 1.0 - (dot / (na * nb))


_VEC_OP = re.compile(r"embedding\s*<=>\s*\?")


class _PgVectorShim:
    """Runs `PgVectorBackend`'s REAL SQL on SQLite, with `<=>` emulated as cosine distance.

    What this proves: the emitted SQL is valid, the project scope composes with the ranking, the
    ORDER BY actually orders by distance, the LIMIT bounds the result, and every value travels as
    a BOUND parameter. What it does NOT prove: pgvector's HNSW recall behaviour — that is the
    live-DB leg (`MOKATA_TEST_DSN`), the same honest boundary DB.S2a and DB.S3 recorded."""

    def __init__(self, stamp=None):
        self._c = sqlite3.connect(":memory:")
        self._c.execute(
            """CREATE TABLE mokata_memory_vectors (
                   seq INTEGER PRIMARY KEY AUTOINCREMENT,
                   id TEXT UNIQUE, mtype TEXT, subject TEXT, status TEXT, doc TEXT,
                   embedding TEXT, project TEXT)""")
        self._c.execute(
            "CREATE TABLE mokata_vector_stamp (id INT PRIMARY KEY, embedder TEXT, dim INT)")
        if stamp is not None:
            self._c.execute("INSERT INTO mokata_vector_stamp (id, embedder, dim) VALUES (1,?,?)",
                            (stamp[0], stamp[1]))
        self._c.create_function("vec_cos_dist", 2, _cos_distance)
        self.sql_log = []
        self.param_log = []

    def execute(self, sql, params=()):
        self.sql_log.append(sql)
        self.param_log.append(tuple(params or ()))
        run = _VEC_OP.sub("vec_cos_dist(embedding, ?)", sql.replace("%s", "?"))
        # SQLite has no ON CONFLICT (id) DO UPDATE SET col=EXCLUDED.col ordering quirk here; the
        # statement is already valid SQLite, so it runs verbatim.
        return self._c.execute(run, tuple(params or ()))

    def close(self):
        self._c.close()

    def last_select(self):
        return [s for s in self.sql_log if s.lstrip().upper().startswith("SELECT")][-1]

    def stamp_row(self):
        return self._c.execute("SELECT embedder, dim FROM mokata_vector_stamp WHERE id=1").fetchone()

    def vectors(self):
        return dict(self._c.execute("SELECT id, embedding FROM mokata_memory_vectors").fetchall())


class _Store:
    """The minimal store-like object `tiered_recall` documents itself against."""

    def __init__(self, items, backend):
        self._items = items
        self.backend = backend

    def all_active(self):
        return list(self._items)


def _items():
    return [MemoryItem.create("alpha subject", "alpha value"),
            MemoryItem.create("beta subject", "beta value")]


class _Ledger:
    def __init__(self):
        self.entries = []

    def record(self, kind, **fields):
        self.entries.append((kind, fields))


def _ok_proc(returncode=0, stderr=""):
    return subprocess.CompletedProcess(args=["pip"], returncode=returncode, stdout="", stderr=stderr)


# ================================================================================ 1 · WIRING
class TestPgVectorReachable(unittest.TestCase):
    """The orphan is wired: an opted-in config SELECTS the backend and recall USES its index."""

    def test_db_s4_pgvector_reachable(self):
        # An opted-in team config — `memory_store` resolved to `pgvector`, DSN via env (never
        # inline). Only the DRIVER CONNECT is stubbed (the shim stands in for a live server), so
        # everything above it — the selection branch, the embedder resolution, the DSN resolver,
        # the backend construction, the stamp verify — is the real shipped path.
        shim = _PgVectorShim()
        with mock.patch.dict(os.environ, {"MOKATA_DSN": "postgres://host/db"}), \
             mock.patch("mokata.memory._pg.connect_psycopg", return_value=shim):
            backend = selection._select_raw_backend(
                "pgvector", tempfile.mkdtemp(), {}, {"dsn_env": "MOKATA_DSN"}, None, None)
        self.assertIsInstance(backend, PgVectorBackend,
                              "an opted-in pgvector config selects the vector backend — the "
                              "D5-rider(3) 'no shipped store selects it' state is over")
        self.assertTrue(hasattr(backend, "semantic_search"),
                        "and it is the backend that carries the index-backed semantic tier")

    def test_db_s4_tiered_recall_uses_the_index_backed_search(self):
        # The reachable branch is the INDEX one: `tiered_recall` must call `semantic_search`, not
        # fall through to the per-item Python cosine (which is what a store WITHOUT an index does).
        items = _items()
        backend = PgVectorBackend(embedder=HashingEmbedder(), conn=_PgVectorShim())
        for it in items:
            backend.put(it)
        hits = tiered.tiered_recall(_Store(items, backend), "beta",
                                    embedder=HashingEmbedder(), top_k=2)
        self.assertTrue(any(h.semantic > 0 for h in hits),
                        "the index-backed semantic tier contributed real scores")
        self.assertIn("<=>", backend._conn.last_select(),
                      "ranking went through pgvector's distance operator, not a Python scan")

    def test_db_s4_non_opted_config_is_byte_identical(self):
        # NEGATIVE + the ADR-54 guarantee: without the opt-in, nothing about selection changes.
        root = tempfile.mkdtemp()
        for tool in ("sqlite", "ripgrep", "unknown-tool"):
            be = selection._select_raw_backend(tool, root, {}, {}, None, None)
            self.assertEqual("sqlite", be.name,
                             f"'{tool}' still resolves to the SQLite floor — no pgvector anywhere")
            be.close()

    def test_db_s4_unavailable_pgvector_degrades_to_the_floor_loudly(self):
        # No DSN env var set ⇒ no backend ⇒ the SQLite floor, and it SAYS so (never silent).
        with mock.patch("mokata.memory.selection._note_vector_degrade") as noted:
            be = selection._select_raw_backend(
                "pgvector", tempfile.mkdtemp(), {}, {"dsn_env": "MOKATA_ABSENT_DSN"}, None, None)
        self.assertEqual("sqlite", be.name, "unavailable pgvector falls to the guaranteed floor")
        self.assertTrue(noted.called, "the fallback is announced — a silent degrade was the bug")
        be.close()

    def test_db_s4_top_k_is_bounded_at_the_backend(self):
        # `tiered_recall` passes max(top_k, len(items)), so an unbounded LIMIT would reinstate the
        # full-store scan the index exists to replace. The bound lives where the cost is paid.
        backend = PgVectorBackend(embedder=HashingEmbedder(), conn=_PgVectorShim())
        backend.semantic_search("q", top_k=10_000)
        self.assertEqual(MAX_TOP_K, backend._conn.param_log[-1][-1],
                         "an absurd top_k is clamped to MAX_TOP_K, not passed to the database")

    def test_db_s4_registered_as_a_memory_store_provider(self):
        from mokata.memory.migrate import SUPPORTED
        from mokata.profiles import TOOL_CATALOG
        self.assertIn("pgvector", TOOL_CATALOG)
        self.assertEqual("memory_store", TOOL_CATALOG["pgvector"]["provides"])
        self.assertIn("pgvector", SUPPORTED, "migrating INTO pgvector is how a store gets embedded")

    def test_db_s4_no_default_profile_wires_pgvector(self):
        # ADR-54 is a CONSTRAINT, not a preference: the extension-needing store must never appear
        # on the golden path. A profile that quietly listed it would be the whole ADR undone.
        from mokata.profiles import PROFILES
        for name, prof in PROFILES.items():
            self.assertNotIn("pgvector", json.dumps(prof),
                             f"profile '{name}' must not wire the opt-in pgvector store")


# =================================================================================== 2 · SEAM
class TestEmbedderAutodetect(unittest.TestCase):

    def test_db_s4_embedder_autodetect_prefers_the_extra_when_usable(self):
        class _Fake:
            def encode(self, texts):
                return [[0.1] * 256 for _ in texts]
        with mock.patch("mokata.memory.embed._load_model2vec", return_value=_Fake()):
            e = detect_embedder()
        eid, dim = embedder_identity(e)
        self.assertTrue(eid.startswith("model2vec:"), "the blessed extra wins when it loads")
        self.assertEqual(256, dim, "the dim is the MODEL's, probed — never a mokata constant")

    def test_db_s4_embedder_autodetect_falls_back_to_hashing_when_absent(self):
        # The extra NOT installed is the documented zero-dep default, NOT a degrade — so it gets
        # the hashing embedder and, deliberately, NO notice (a notice that fires on every default
        # install is noise; the DB.S3 lexical-floor lesson).
        with mock.patch("mokata.memory.embed._load_model2vec",
                        side_effect=ModelUnavailable("model2vec is not installed")), \
             mock.patch("mokata.memory.embed._extra_is_installed", return_value=False), \
             mock.patch("mokata.degrade.note_degraded") as noted:
            e = detect_embedder()
        self.assertEqual(HASHING_ID, embedder_identity(e)[0])
        self.assertFalse(noted.called, "the zero-dep default is not a degrade and earns no notice")

    def test_db_s4_model_fetch_failure_degrades_with_a_notice(self):
        # The extra IS installed and the MODEL won't load (offline, cold cache, hub error). The
        # user paid 30MB for semantic recall and is getting token-hash — that IS a degrade.
        seen = []
        with mock.patch("mokata.memory.embed._load_model2vec",
                        side_effect=ModelUnavailable("offline: model could not be fetched")), \
             mock.patch("mokata.memory.embed._extra_is_installed", return_value=True):
            e = detect_embedder(degrade_out=seen.append)
        self.assertEqual(HASHING_ID, embedder_identity(e)[0], "degrades to hashing, never crashes")
        self.assertTrue(seen, "the degrade is announced once, with the fix")
        self.assertIn("hash", " ".join(seen).lower(), "the notice names what you ARE getting")

    def test_db_s4_model_fetch_is_bounded_no_hang(self):
        # A cold cache on a black-holed network must not hang a recall. The fetch is bounded by
        # the env vars huggingface_hub actually reads, set only for the load and restored after.
        captured = {}

        class _Boom:
            @staticmethod
            def from_pretrained(name):
                captured.update({k: os.environ.get(k) for k in
                                 ("HF_HUB_ETAG_TIMEOUT", "HF_HUB_DOWNLOAD_TIMEOUT")})
                raise OSError("connection timed out")

        before = os.environ.get("HF_HUB_ETAG_TIMEOUT")
        with mock.patch.dict("sys.modules", {"model2vec": mock.Mock(StaticModel=_Boom)}):
            with self.assertRaises(ModelUnavailable):
                embed._load_model2vec("some/model")
        self.assertEqual(str(int(embed.MODEL_FETCH_TIMEOUT_S)), captured["HF_HUB_ETAG_TIMEOUT"],
                         "the fetch runs under a finite timeout — the D0 discipline")
        self.assertEqual(before, os.environ.get("HF_HUB_ETAG_TIMEOUT"),
                         "the caller's environment is restored — mokata mutates nothing durably")

    def test_db_s4_make_embedder_default_is_unchanged(self):
        # Stage 35e's contract: no `settings.memory.embedder` ⇒ semantic OFF. Opting a user into
        # embedding their memory is exactly what P2 says you ask about first.
        self.assertIsNone(make_embedder(None))
        self.assertIsNone(make_embedder("something-else"))
        self.assertEqual(HASHING_ID, embedder_identity(make_embedder("hashing"))[0])

    def test_db_s4_bare_callable_is_identified_as_custom(self):
        # The seam accepts ANY `text -> list[float]`. mokata cannot tell two anonymous lambdas
        # apart, so it says `custom` rather than pretending — and probes the dim from a real call.
        eid, dim = embedder_identity(lambda _t: [0.0, 1.0, 0.0])
        self.assertEqual(("custom", 3), (eid, dim))

    def test_db_s4_unprobeable_embedder_fails_closed(self):
        def _broken(_t):
            raise RuntimeError("nope")
        self.assertEqual(("custom", 0), embedder_identity(_broken),
                         "an unidentifiable embedder gets dim 0, which the stamp reads as a "
                         "mismatch — fail-closed, never waved through onto an index")


# ================================================================================== 3 · STAMP
class TestStampBinding(unittest.TestCase):

    def test_db_s4_stamp_mismatch_refuses(self):
        # THE test of the binding. Index built by A, embedder B configured ⇒ the tier is OFF and
        # the finding NAMES both embedders and the migration. Nothing is silently mixed.
        shim = _PgVectorShim(stamp=("model2vec:some/model", 256))
        with self.assertRaises(EmbedderStampMismatch) as ctx:
            PgVectorBackend(embedder=HashingEmbedder(), conn=shim)
        msg = str(ctx.exception)
        self.assertIn("model2vec:some/model", msg, "the finding names the STAMPED embedder")
        self.assertIn(HASHING_ID, msg, "…and the CONFIGURED one")
        self.assertIn("mokata memory reembed", msg, "…and the command that fixes it")
        self.assertEqual([], [s for s in shim.sql_log if "INSERT" in s.upper()],
                         "a refused open writes NOTHING — no vectors, no restamp")

    def test_db_s4_stamp_mismatch_degrades_selection_with_a_named_finding(self):
        # End to end: the mismatch reaches SELECTION, which falls to the local floor and reports
        # the re-embed fix rather than a generic "unreachable" (the two have different remedies).
        seen = []
        exc = EmbedderStampMismatch(("model2vec:m", 256), (HASHING_ID, 64))
        with mock.patch("mokata.degrade.note_degraded",
                        side_effect=lambda *a, **k: seen.append((a, k))):
            selection._note_vector_degrade([exc])
        (_args, kw) = seen[0]
        self.assertIn("reembed", kw["fix"], "the fix names the migration, not a DSN check")
        self.assertIn("model2vec:m", kw["fallback"])

    def test_db_s4_matching_stamp_is_accepted(self):
        shim = _PgVectorShim(stamp=(HASHING_ID, 64))
        be = PgVectorBackend(embedder=HashingEmbedder(), conn=shim)
        self.assertEqual((HASHING_ID, 64), be.read_stamp())

    def test_db_s4_unstamped_index_is_compatible(self):
        # A PRE-DB.S4 index carries no stamp. Refusing it would turn a safety feature into an
        # outage for exactly the users who already opted in; it behaves as it did yesterday.
        be = PgVectorBackend(embedder=HashingEmbedder(), conn=_PgVectorShim(stamp=None))
        self.assertIsNone(be.read_stamp(), "no stamp on a pre-DB.S4 index")
        item = MemoryItem.create("legacy subject", "legacy value")
        be.put(item)
        self.assertEqual([item.id], [h[0].id for h in be.semantic_search("legacy subject")],
                         "it keeps working exactly as it did yesterday, until the first re-embed")

    def test_db_s4_same_dim_different_embedder_still_refuses(self):
        # The dim alone would let two 256-dim models pass for each other. Both axes must match.
        class _Other(HashingEmbedder):
            embedder_id = "some-other-embedder"
        with self.assertRaises(EmbedderStampMismatch):
            PgVectorBackend(embedder=_Other(), conn=_PgVectorShim(stamp=(HASHING_ID, 64)))

    def test_db_s4_stamp_is_provisioned_by_init_ddl_only(self):
        stmts = teamdb.vector_provision_sql(64, HASHING_ID)
        text = " ".join(s if isinstance(s, str) else s[0] for s in stmts)
        self.assertIn(teamdb.VECTOR_STAMP_TABLE, text)
        self.assertIn("USING hnsw (embedding vector_cosine_ops)", text,
                      "HNSW matches `<=>`'s operator class — a mismatched one is silently unused")
        stamp_stmt = [s for s in stmts if isinstance(s, tuple)][0]
        self.assertEqual((HASHING_ID, 64), stamp_stmt[1],
                         "the embedder id travels as a BOUND parameter, never interpolated")

    def test_db_s4_hnsw_absence_does_not_change_the_query(self):
        # The DB.S3 GIN posture: the index makes it FAST, its absence makes it SLOW, not wrong.
        # The shim has no HNSW index at all and the search still ranks correctly.
        items = _items()
        be = PgVectorBackend(embedder=HashingEmbedder(), conn=_PgVectorShim())
        for it in items:
            be.put(it)
        hits = be.semantic_search("beta subject beta value", top_k=2)
        self.assertEqual(items[1].id, hits[0][0].id,
                         "correct ranking with NO vector index present — the index is perf only")


# =============================================================================== 4 · RE-EMBED
class TestReembedGated(unittest.TestCase):

    def _stamped_backend(self, stamp=("old-embedder", 64)):
        shim = _PgVectorShim(stamp=stamp)
        be = PgVectorBackend(embedder=HashingEmbedder(), conn=shim, verify_stamp=False)
        for it in _items():
            be.put(it)
        return be, shim

    def test_db_s4_reembed_gated(self):
        be, shim = self._stamped_backend()
        ledger = _Ledger()
        res = reembed.run_reembed(be, confirm=lambda _q: True, ledger=ledger, out=lambda _s: None)
        self.assertTrue(res.restamped)
        self.assertEqual(2, res.reembedded)
        self.assertEqual((HASHING_ID, 64), shim.stamp_row(),
                         "the index is restamped with the embedder that actually wrote it")
        self.assertEqual([("memory_reembed", ledger.entries[0][1])], ledger.entries)

    def test_db_s4_reembed_decline_writes_nothing(self):
        be, shim = self._stamped_backend()
        before = shim.vectors()
        res = reembed.run_reembed(be, confirm=lambda _q: False, out=lambda _s: None)
        self.assertTrue(res.aborted)
        self.assertEqual(("old-embedder", 64), shim.stamp_row(), "the stamp is untouched")
        self.assertEqual(before, shim.vectors(), "not one vector was rewritten")

    def test_db_s4_reembed_previews_the_count_before_asking(self):
        # "Re-embed?" is not a question a human can answer. The count and both embedder names are.
        be, _shim = self._stamped_backend()
        shown, asked = [], []
        reembed.run_reembed(be, confirm=lambda q: asked.append(q) or False, out=shown.append)
        preview = "\n".join(shown)
        self.assertIn("2 item", preview)
        self.assertIn("old-embedder", preview)
        self.assertIn(HASHING_ID, preview)
        self.assertTrue(asked, "the preview came BEFORE the gate")

    def test_db_s4_reembed_is_a_noop_when_already_stamped(self):
        be, shim = self._stamped_backend(stamp=(HASHING_ID, 64))
        asked = []
        res = reembed.run_reembed(be, confirm=lambda q: asked.append(q) or True,
                                  out=lambda _s: None)
        self.assertFalse(res.restamped)
        self.assertFalse(asked, "no gate prompt is burned on a no-op")
        self.assertIn("already stamped", res.message)

    def test_db_s4_reembed_writes_vectors_before_the_stamp(self):
        # Order is the crash-safety argument: a mid-run failure must leave the index stamped OLD
        # (which the runtime refuses ⇒ degraded + re-runnable), never stamped NEW over old vectors.
        be, shim = self._stamped_backend()
        order = []
        real_put, real_stamp = be.put, be.write_stamp
        be.put = lambda it: order.append("put") or real_put(it)
        be.write_stamp = lambda e, d: order.append("stamp") or real_stamp(e, d)
        reembed.run_reembed(be, assume_yes=True, out=lambda _s: None)
        self.assertEqual("stamp", order[-1], "the stamp is the LAST write, after every vector")
        self.assertIn("put", order[:-1])

    def test_db_s4_reembed_ledger_carries_no_memory_content(self):
        be, _shim = self._stamped_backend()
        ledger = _Ledger()
        reembed.run_reembed(be, assume_yes=True, ledger=ledger, out=lambda _s: None)
        blob = json.dumps(ledger.entries)
        for leaked in ("alpha subject", "alpha value", "beta subject", "beta value"):
            self.assertNotIn(leaked, blob,
                             "the audit record names the embedder and a count — never the memory")


# ================================================================================ 5 · CONSENT
class TestConsentInstall(unittest.TestCase):

    def test_db_s4_consent_install(self):
        # ACCEPT ⇒ pip runs (mocked), the result is VERIFIED by import (not by pip's exit code),
        # and the decision is ledgered.
        with tempfile.TemporaryDirectory() as d:
            ledger, calls = _Ledger(), []
            res = extras_install.offer_extra(
                d, "embeddings", EMBEDDINGS_EXTRA, "install?",
                prompt_fn=lambda _q: True, verify=lambda: True, ledger=ledger,
                user_home=d, out=lambda _s: None,
                runner=lambda cmd, **kw: calls.append((cmd, kw)) or _ok_proc())
            self.assertTrue(res.installed)
            self.assertEqual([("extra_offer", {"extra": "embeddings", "decision": "accepted",
                                               "installed": True, "scope": "repo"})],
                             ledger.entries)
            cmd, kw = calls[0]
            self.assertEqual(["-m", "pip", "install", EMBEDDINGS_EXTRA], cmd[1:],
                             "installs into THIS interpreter, not whatever `pip` is on PATH")
            self.assertIn("timeout", kw, "the ONE subprocess is bounded (the D0 discipline)")

    def test_db_s4_consent_decline_is_ledgered_once_and_never_re_asked(self):
        with tempfile.TemporaryDirectory() as d:
            ledger, asks = _Ledger(), []

            def _ask(q):
                asks.append(q)
                return False

            first = extras_install.offer_extra(d, "embeddings", EMBEDDINGS_EXTRA, "install?",
                                               prompt_fn=_ask, ledger=ledger, user_home=d,
                                               out=lambda _s: None)
            second = extras_install.offer_extra(d, "embeddings", EMBEDDINGS_EXTRA, "install?",
                                                prompt_fn=_ask, ledger=ledger, user_home=d,
                                                out=lambda _s: None)
        self.assertTrue(first.declined and first.asked)
        self.assertTrue(second.declined)
        self.assertFalse(second.asked, "NO NAG — the second run never asks again")
        self.assertEqual(1, len(asks), "asked exactly once, ever")
        self.assertEqual(1, len(ledger.entries), "and ledgered exactly once")

    def test_db_s4_consent_pip_failure_degrades_clean(self):
        with tempfile.TemporaryDirectory() as d:
            said = []
            res = extras_install.offer_extra(
                d, "embeddings", EMBEDDINGS_EXTRA, "install?", prompt_fn=lambda _q: True,
                verify=lambda: True, user_home=d, out=said.append,
                runner=lambda cmd, **kw: _ok_proc(returncode=1, stderr="ERROR: no matching dist"))
        self.assertTrue(res.accepted)
        self.assertFalse(res.installed, "a failed install never claims the capability")
        self.assertIn("fallback", " ".join(said), "the user is told what they DO have")

    def test_db_s4_consent_install_timeout_degrades_clean(self):
        def _hang(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 1))
        res = extras_install.install_extra(EMBEDDINGS_EXTRA, runner=_hang, timeout=5,
                                           out=lambda _s: None)
        self.assertFalse(res.ok)
        self.assertIn("timed out", res.message, "a wedged pip index degrades — it never hangs")

    def test_db_s4_consent_install_verified_by_import_not_exit_code(self):
        # pip exiting 0 says a wheel landed, not that it is importable from THIS interpreter.
        res = extras_install.install_extra(EMBEDDINGS_EXTRA, verify=lambda: False,
                                           runner=lambda cmd, **kw: _ok_proc(),
                                           out=lambda _s: None)
        self.assertFalse(res.ok)
        self.assertTrue(res.ran)
        self.assertIn("not usable", res.message)

    def test_db_s4_consent_already_present_does_not_ask(self):
        with tempfile.TemporaryDirectory() as d:
            asks = []
            res = extras_install.offer_extra(d, "embeddings", EMBEDDINGS_EXTRA, "install?",
                                             already=lambda: True,
                                             prompt_fn=lambda q: asks.append(q) or True,
                                             user_home=d, out=lambda _s: None)
        self.assertTrue(res.installed and not res.asked)
        self.assertEqual([], asks, "an idempotent offer asks nothing when the capability is there")

    def test_db_s4_consent_never_installs_without_being_asked(self):
        # P2, stated as a test: no path reaches pip without an explicit yes.
        with tempfile.TemporaryDirectory() as d:
            ran = []
            extras_install.offer_extra(d, "embeddings", EMBEDDINGS_EXTRA, "install?",
                                       prompt_fn=lambda _q: False, user_home=d,
                                       out=lambda _s: None,
                                       runner=lambda cmd, **kw: ran.append(cmd) or _ok_proc())
        self.assertEqual([], ran, "a decline never runs pip")

    def test_db_s4_consent_ledger_carries_no_secrets(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = _Ledger()
            with mock.patch.dict(os.environ, {"MOKATA_DSN": "postgres://user:pw@host/db"}):
                extras_install.offer_extra(d, "embeddings", EMBEDDINGS_EXTRA, "install?",
                                           prompt_fn=lambda _q: True, verify=lambda: True,
                                           ledger=ledger, user_home=d, out=lambda _s: None,
                                           runner=lambda cmd, **kw: _ok_proc())
            blob = json.dumps(ledger.entries)
        for secret in ("postgres://", "user:pw", "pw@host"):
            self.assertNotIn(secret, blob, "no DSN — by value or by fragment — reaches the ledger")

    def test_db_s4_crg_seam_reuse_hook_is_the_shared_primitive(self):
        # The GR.S2 CRG seam-reuse hook DB.S4 was tasked with carrying: `graph_adopt`'s assisted
        # install now DRIVES `extras_install.install_extra` (bounded, import-verified) instead of
        # printing a command. GR.S2-FU's CI leg exercises this same path, so what CI proves is the
        # code users run.
        import inspect

        from mokata.knowledge import graph_adopt
        src = inspect.getsource(graph_adopt.offer_graph_at_setup)
        self.assertIn("install_extra", src, "the CRG install rides the shared primitive")
        self.assertTrue(callable(graph_adopt._crg_importable),
                        "and is verified by import, not by pip's exit code")


# ================================================================================= 6 · DOCTOR
class TestDoctorTierLine(unittest.TestCase):

    def _lines(self, semantic, lexical):
        return "\n".join(tier_report.RetrievalStack(semantic=semantic,
                                                    lexical=lexical).render_lines())

    def test_db_s4_doctor_tier(self):
        # The full matrix: every combination renders an honest line, and the hashing tier is
        # explicitly labelled as NOT meaning — the single most important word in this report.
        matrix = [
            ("model2vec:minishlab/potion-base-8M", "tsvector"),
            ("model2vec:minishlab/potion-base-8M", "fts5"),
            (HASHING_ID, "fts5"),
            (HASHING_ID, "jaccard"),
            ("off", "fts5"),
            ("off", "jaccard"),
        ]
        for semantic, lexical in matrix:
            text = self._lines(semantic, lexical)
            self.assertIn(semantic, text)
            self.assertIn(lexical, text)
        self.assertIn("NOT meaning", self._lines(HASHING_ID, "fts5"),
                      "the hashing tier is never allowed to read as real semantic recall")
        self.assertIn("no embedder configured", self._lines("off", "fts5"))

    def test_db_s4_doctor_tier_is_informational_only(self):
        # It must never flip doctor's exit. `report.ok` derives from findings, and this emits none.
        import inspect

        from mokata.cli_commands import diagnostics
        src = inspect.getsource(diagnostics.cmd_doctor)
        section = src.split("tier_report")[1]
        self.assertNotIn("report.findings.append", section,
                         "the retrieval line adds no finding — every state it prints is supported")
        self.assertIn("return 0 if report.ok else 1", src, "exit stays derived from report.ok")

    def test_db_s4_doctor_tier_degrades_to_unknown_never_raises(self):
        # doctor is what you run WHEN things are broken; it must survive a broken surface.
        class _Broken:
            pass
        stack = tier_report.resolve_stack(_Broken())
        self.assertEqual(tier_report.UNKNOWN, stack.semantic)
        self.assertEqual(tier_report.UNKNOWN, stack.lexical)

    def test_db_s4_doctor_tier_reports_the_resolved_embedder_not_the_setting(self):
        # `auto` must be resolved through the SAME `make_embedder` the store uses, so doctor can
        # never report a tier the store wouldn't actually build.
        class _Surface:
            class manifest:
                @staticmethod
                def setting(_k, _d=None):
                    return {"embedder": "auto"}
        with mock.patch("mokata.memory.embed._load_model2vec",
                        side_effect=ModelUnavailable("absent")), \
             mock.patch("mokata.memory.embed._extra_is_installed", return_value=False):
            self.assertEqual(HASHING_ID, tier_report._semantic_engine(_Surface()),
                             "doctor reports what auto ACTUALLY resolved to")

    def test_db_s4_doctor_tier_ascii_mode_has_no_unicode(self):
        text = self._lines(HASHING_ID, "jaccard")
        ascii_text = "\n".join(
            tier_report.RetrievalStack(HASHING_ID, "jaccard").render_lines(ascii_only=True))
        self.assertIn("•", text)
        self.assertNotIn("•", ascii_text, "a piped/NO_COLOR run stays plain ASCII")


# ========================================================================= 7 · FUSION + SCOPE
class TestFusionAndScope(unittest.TestCase):

    def test_db_s4_semantic_still_outranks_lexical_with_the_real_seam(self):
        # DB.S3's fusion guarantee, re-asserted through the LIVE pgvector seam rather than an
        # injected double: an embedding-near item beats a merely-lexical match.
        near = MemoryItem.create("durable write gate", "the gate blocks unapproved writes")
        lexical_only = MemoryItem.create("gate", "unrelated content about palettes")
        items = [near, lexical_only]
        be = PgVectorBackend(embedder=HashingEmbedder(), conn=_PgVectorShim())
        for it in items:
            be.put(it)
        hits = tiered.tiered_recall(_Store(items, be), "durable write gate",
                                    embedder=HashingEmbedder(), top_k=2)
        self.assertEqual(near.id, hits[0].item.id)
        self.assertGreater(hits[0].semantic, 0.0, "the semantic tier is live and contributing")
        self.assertGreater(tiered.SEMANTIC_WEIGHT, tiered.LEXICAL_WEIGHT,
                           "the weighting that makes semantic dominate is unchanged")

    def test_db_s4_semantic_hits_respect_project_scope(self):
        # Cross-tenant, again (the DB.S3 composition): a shared DSN hosts many projects and a
        # neighbour in project B must never surface in project A's recall.
        shim = _PgVectorShim()
        a = PgVectorBackend(embedder=HashingEmbedder(), conn=shim, project="alpha-proj")
        b = PgVectorBackend(embedder=HashingEmbedder(), conn=shim, project="beta-proj")
        secret = MemoryItem.create("tenant secret", "beta's private memory")
        b.put(secret)
        a.put(MemoryItem.create("tenant secret", "alpha's own memory"))
        hits = a.semantic_search("tenant secret", top_k=10)
        self.assertEqual(1, len(hits), "only this project's row is a candidate")
        self.assertNotIn("beta's private memory", json.dumps(hits[0][0].to_doc()))
        self.assertIn("project=", shim.last_select(), "the scope predicate is IN the SQL")

    def test_db_s4_semantic_search_filters_by_status(self):
        be = PgVectorBackend(embedder=HashingEmbedder(), conn=_PgVectorShim())
        live = MemoryItem.create("live subject", "live value")
        be.put(live)
        self.assertEqual([], be.semantic_search("live subject", statuses=("archived",)),
                         "a status the caller didn't ask for never reaches the result")
        self.assertEqual(1, len(be.semantic_search("live subject", statuses=(ACTIVE,))))

    def test_db_s4_values_are_bound_never_formatted(self):
        # Every VALUE rides the driver's placeholder; only mokata-owned table constants are
        # interpolated. A hostile query is a vector, not SQL.
        be = PgVectorBackend(embedder=HashingEmbedder(), conn=_PgVectorShim(), project="p'; DROP--")
        be.semantic_search("'; DROP TABLE mokata_memory_vectors; --", top_k=3)
        sql = be._conn.last_select()
        self.assertNotIn("DROP", sql, "neither the query nor the project reached the SQL text")
        self.assertIn("%s", sql)


# ================================================================================ 8 · SECRETS
class TestSecretSafety(unittest.TestCase):

    def test_db_s4_findings_name_the_env_var_never_the_dsn(self):
        with mock.patch.dict(os.environ, {"MOKATA_DSN": "postgres://user:pw@host:5432/db"}):
            seen = []
            with mock.patch("mokata.degrade.note_degraded",
                            side_effect=lambda *a, **k: seen.append((a, k))):
                selection._note_vector_degrade([])
        blob = json.dumps(seen, default=str)
        for secret in ("postgres://", "user:pw", "5432/db"):
            self.assertNotIn(secret, blob, "a degrade notice never carries the DSN value")

    def test_db_s4_stamp_mismatch_message_carries_no_memory_content(self):
        exc = EmbedderStampMismatch(("model2vec:m", 256), (HASHING_ID, 64))
        msg = str(exc)
        self.assertIn("model2vec:m", msg, "embedder IDS are safe to name — they are config, not data")
        self.assertNotIn("value", msg.replace("vectors", ""), "no memory content in the finding")


# ============================================================ the live-DB leg (opt-in, skipped)
# GR.S2-FU — run by `.github/workflows/live-db-legs.yml` (opt-in: workflow_dispatch + weekly cron).
@unittest.skipUnless(os.environ.get("MOKATA_TEST_DSN"),
                     "no MOKATA_TEST_DSN — the live pgvector leg is opt-in (the shim proves the "
                     "SQL shape; only a real server proves HNSW recall)")
class TestLivePgVector(unittest.TestCase):

    def test_db_s4_live_provision_and_search(self):
        dsn = os.environ["MOKATA_TEST_DSN"]
        e = HashingEmbedder()
        eid, dim = embedder_identity(e)
        teamdb.provision_vector(dsn, dim=dim, embedder_id=eid)
        be = build_pgvector_backend({"dsn_env": "MOKATA_TEST_DSN"}, e)
        self.assertIsNotNone(be, "a provisioned, opted-in DSN builds the real backend")
        self.assertEqual((eid, dim), be.read_stamp())
        item = MemoryItem.create("live pgvector subject", "live pgvector value")
        be.put(item)
        hits = be.semantic_search("live pgvector subject", top_k=5)
        self.assertTrue(any(h[0].id == item.id for h in hits))
        be.close()


if __name__ == "__main__":
    unittest.main()
