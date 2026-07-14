"""Stage 35d — team design & spec VAULT.

Both jsonschema states (no jsonschema is imported here — the vault is dependency-free, so the
behaviour is identical ABSENT/PRESENT). The vault is a committed/synced artifact store at
`.mokata/vault/` (the repo root, NOT under temp_local/), carrying provenance + a content hash,
human-gated on write, never a silent clobber, read-only on list/search/pull.
"""

import json
import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)
from _support import mcp_commit          # SI.3: propose -> human approves -> redeem by id

from mokata import MOKATA_DIR
from mokata import vault as V


def _write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _ledger(root):
    """The repo's audit ledger — the same one `vault_pull` records an integrity failure to."""
    from mokata.govern.ledger import AuditLedger
    return AuditLedger.from_mokata_dir(os.path.join(root, MOKATA_DIR))


BRAINSTORM = """# Payments redesign

We weighed three options for the checkout flow and chose the idempotent-ledger approach
because it survives retries. Rationale: exactly-once capture.
"""


class _TempRepo:
    def __enter__(self):
        self.d = tempfile.mkdtemp()
        return self

    def __exit__(self, *a):
        import shutil
        shutil.rmtree(self.d, ignore_errors=True)

    def file(self, name, text):
        p = os.path.join(self.d, name)
        _write(p, text)
        return p


# ----------------------------------------------------------------- push: location + provenance

class TestPushArtifactAndProvenance(unittest.TestCase):
    def test_push_writes_outside_temp_local_with_provenance_and_hash(self):
        with _TempRepo() as r:
            src = r.file("plan.md", BRAINSTORM)
            plan = V.plan_push(r.d, "payments-redesign", src, force=False)
            self.assertEqual(plan.status, "new")
            entry = V.commit_push(r.d, plan, author="alice", now="2026-06-27T00:00:00+00:00")

            # artifact lives at .mokata/vault/<name>.md — committed, NOT under temp_local/
            artifact = os.path.join(r.d, MOKATA_DIR, "vault", "payments-redesign.md")
            self.assertTrue(os.path.exists(artifact))
            self.assertNotIn("temp_local", artifact)
            with open(artifact, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), BRAINSTORM)

            # provenance + hash recorded in the committed index
            index = os.path.join(r.d, MOKATA_DIR, "vault", "index.json")
            with open(index, encoding="utf-8") as fh:
                data = json.load(fh)
            rec = data["entries"]["payments-redesign"]
            self.assertEqual(rec["author"], "alice")
            self.assertEqual(rec["kind"], "brainstorm")
            self.assertEqual(rec["title"], "Payments redesign")
            self.assertEqual(rec["content_hash"], V.content_hash(BRAINSTORM))
            self.assertEqual(rec["source"], os.path.abspath(src))
            self.assertEqual(rec["version"], 1)
            self.assertEqual(entry.content_hash, V.content_hash(BRAINSTORM))

    def test_kind_inferred_and_overridable(self):
        with _TempRepo() as r:
            src = r.file("the-spec.md", "# API spec\n\nACs.\n")
            self.assertEqual(V.plan_push(r.d, "auth-spec", src).kind, "spec")  # inferred
            self.assertEqual(
                V.plan_push(r.d, "auth-spec", src, kind="brainstorm").kind, "brainstorm")

    def test_invalid_name_rejected(self):
        with _TempRepo() as r:
            src = r.file("x.md", "# x\n")
            for bad in ("../escape", "a/b", "..", "sub/dir"):
                with self.assertRaises(V.VaultError):
                    V.plan_push(r.d, bad, src)

    def test_missing_source_rejected(self):
        with _TempRepo() as r:
            with self.assertRaises(V.VaultError):
                V.plan_push(r.d, "x", os.path.join(r.d, "nope.md"))


# ----------------------------------------------------------------- list / search (read-only)

class TestListAndSearchReadOnly(unittest.TestCase):
    def _seed(self, r):
        V.commit_push(r.d, V.plan_push(r.d, "payments-redesign", r.file("a.md", BRAINSTORM)),
                      author="alice", now="2026-06-27T00:00:00+00:00")
        V.commit_push(r.d, V.plan_push(r.d, "logging-spec",
                                       r.file("b.md", "# Logging spec\n\nstructured logs.\n")),
                      author="bob", now="2026-06-27T00:00:00+00:00")

    def test_list_finds_pushed_entries_and_does_not_write(self):
        with _TempRepo() as r:
            self._seed(r)
            before = os.path.getmtime(os.path.join(r.d, MOKATA_DIR, "vault", "index.json"))
            entries = V.vault_list(r.d)
            self.assertEqual([e.name for e in entries], ["logging-spec", "payments-redesign"])
            after = os.path.getmtime(os.path.join(r.d, MOKATA_DIR, "vault", "index.json"))
            self.assertEqual(before, after)   # read-only

    def test_search_by_name_and_by_body_term(self):
        with _TempRepo() as r:
            self._seed(r)
            by_name = V.vault_search(r.d, "payments")
            self.assertTrue(by_name)
            self.assertEqual(by_name[0].entry.name, "payments-redesign")

            # a term only in the BODY (not the name/title) still matches
            by_body = V.vault_search(r.d, "idempotent ledger retries")
            self.assertEqual(by_body[0].entry.name, "payments-redesign")

    def test_search_no_match_is_empty(self):
        with _TempRepo() as r:
            self._seed(r)
            self.assertEqual(V.vault_search(r.d, "kubernetes helm chart"), [])


# ----------------------------------------------------------------- pull round-trips to a teammate

class TestPullRoundTrip(unittest.TestCase):
    def test_pull_writes_exact_content_to_another_repo(self):
        with _TempRepo() as author_repo, _TempRepo() as teammate_repo:
            src = author_repo.file("plan.md", BRAINSTORM)
            V.commit_push(author_repo.d,
                          V.plan_push(author_repo.d, "payments-redesign", src),
                          author="alice", now="2026-06-27T00:00:00+00:00")

            # simulate the synced vault arriving in the teammate's repo
            import shutil
            shutil.copytree(os.path.join(author_repo.d, MOKATA_DIR, "vault"),
                            os.path.join(teammate_repo.d, MOKATA_DIR, "vault"))

            dest = os.path.join(teammate_repo.d, "review.md")
            content, entry = V.vault_pull(teammate_repo.d, "payments-redesign", dest=dest)
            self.assertEqual(content, BRAINSTORM)
            with open(dest, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), BRAINSTORM)
            self.assertEqual(entry.author, "alice")     # provenance preserved across the sync

    def test_pull_unknown_name_errors(self):
        with _TempRepo() as r:
            with self.assertRaises(V.VaultError):
                V.vault_pull(r.d, "nope")

    def test_pull_detects_corruption(self):
        with _TempRepo() as r:
            V.commit_push(r.d, V.plan_push(r.d, "p", r.file("a.md", BRAINSTORM)),
                          author="a", now="2026-06-27T00:00:00+00:00")
            # tamper with the artifact behind the index's back
            _write(os.path.join(r.d, MOKATA_DIR, "vault", "p.md"), "tampered\n")
            with self.assertRaises(V.VaultError):
                V.vault_pull(r.d, "p")

    # --- DB.S9: an integrity failure is an AUDITABLE event ------------------------------------
    def test_corrupt_pull_is_ledgered_and_copies_nothing(self):
        """A tamper caught at pull is not just refused — it is RECORDED (MS.S3-chained), naming the
        artifact and the hash MISMATCH by prefix (never the full hashes). Nothing is copied."""
        with _TempRepo() as r:
            V.commit_push(r.d, V.plan_push(r.d, "p", r.file("a.md", BRAINSTORM)),
                          author="a", now="2026-06-27T00:00:00+00:00")
            expected = V.content_hash(BRAINSTORM)
            _write(os.path.join(r.d, MOKATA_DIR, "vault", "p.md"), "tampered\n")

            dest = os.path.join(r.d, "out", "p.md")
            with self.assertRaises(V.VaultError):
                V.vault_pull(r.d, "p", dest=dest)
            self.assertFalse(os.path.exists(dest), "a refused pull must copy NOTHING")

            rows = [e for e in _ledger(r.d).entries() if e["kind"] == V.VAULT_INTEGRITY_KIND]
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["name"], "p")
            self.assertEqual(row["outcome"], "refused")
            # the mismatch is named by PREFIX — the full hashes are never echoed into the ledger
            self.assertEqual(row["expected"], expected[:V._HASH_PREFIX_LEN])
            self.assertEqual(row["actual"], V.content_hash("tampered\n")[:V._HASH_PREFIX_LEN])
            self.assertNotIn(expected, json.dumps(row))
            # MS.S3: it lands on the hash chain like any other auditable event
            self.assertTrue(_ledger(r.d).verify().intact)

    def test_intact_pull_ledgers_nothing(self):
        """No behaviour change on the happy path: an intact artifact pulls byte-identically and
        adds NO ledger entry (the ledger records failures, not traffic)."""
        with _TempRepo() as r:
            V.commit_push(r.d, V.plan_push(r.d, "p", r.file("a.md", BRAINSTORM)),
                          author="a", now="2026-06-27T00:00:00+00:00")
            before = len(_ledger(r.d).entries())
            dest = os.path.join(r.d, "out", "p.md")
            content, _entry = V.vault_pull(r.d, "p", dest=dest)
            self.assertEqual(content, BRAINSTORM)
            with open(dest, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), BRAINSTORM)      # byte-identical
            self.assertEqual(len(_ledger(r.d).entries()), before)

    def test_truncated_artifact_is_a_classed_refusal_and_is_ledgered(self):
        """A truncated (not absent) artifact still fails the hash check — a classed VaultError, not
        a stack trace, and an auditable event like any other tamper."""
        with _TempRepo() as r:
            V.commit_push(r.d, V.plan_push(r.d, "p", r.file("a.md", BRAINSTORM)),
                          author="a", now="2026-06-27T00:00:00+00:00")
            _write(os.path.join(r.d, MOKATA_DIR, "vault", "p.md"), BRAINSTORM[:40])
            with self.assertRaises(V.VaultError):
                V.vault_pull(r.d, "p")
            rows = [e for e in _ledger(r.d).entries() if e["kind"] == V.VAULT_INTEGRITY_KIND]
            self.assertEqual(len(rows), 1)

    def test_an_unwritable_ledger_never_masks_the_refusal_and_says_so(self):
        """D5 DEGRADES_LOUD, proven: if the audit ledger cannot be written (a read-only or foreign
        vault ref — exactly what `team join` pulls from), the REFUSAL still stands and nothing is
        copied. The lost audit note is announced, not swallowed into an empty ledger."""
        import io
        import contextlib
        from mokata.govern.ledger import AuditLedger

        with _TempRepo() as r:
            V.commit_push(r.d, V.plan_push(r.d, "p", r.file("a.md", BRAINSTORM)),
                          author="a", now="2026-06-27T00:00:00+00:00")
            _write(os.path.join(r.d, MOKATA_DIR, "vault", "p.md"), "tampered\n")

            def _boom(self, *a, **k):
                raise OSError("read-only file system")

            dest = os.path.join(r.d, "out", "p.md")
            real, err = AuditLedger.record, io.StringIO()
            AuditLedger.record = _boom
            try:
                with contextlib.redirect_stderr(err):
                    with self.assertRaises(V.VaultError):     # the refusal SURVIVES
                        V.vault_pull(r.d, "p", dest=dest)
            finally:
                AuditLedger.record = real

            self.assertFalse(os.path.exists(dest), "a refused pull must still copy NOTHING")
            # ...and the lost record is LOUD, in the CM.S2 shape: subsystem + fallback + class.
            # (The notice interpolates no free text by design — CM.S1 secret-safety — so the
            # artifact is named by the VaultError the caller already gets, not in here.)
            notice = err.getvalue()
            self.assertIn("vault: DEGRADED", notice)
            self.assertIn("the pull is still refused and nothing was copied", notice)
            self.assertIn("OSError", notice)

    def test_a_missing_artifact_is_a_classed_refusal(self):
        """Absence is not corruption: it is refused loudly (classed), and it is NOT a hash mismatch,
        so it does not manufacture an integrity row."""
        with _TempRepo() as r:
            V.commit_push(r.d, V.plan_push(r.d, "p", r.file("a.md", BRAINSTORM)),
                          author="a", now="2026-06-27T00:00:00+00:00")
            os.remove(os.path.join(r.d, MOKATA_DIR, "vault", "p.md"))
            with self.assertRaises(V.VaultError):
                V.vault_pull(r.d, "p")


# ----------------------------------------------------------------- versioning: never a silent clobber

class TestVersioningNeverClobbers(unittest.TestCase):
    def test_identical_repush_is_noop(self):
        with _TempRepo() as r:
            src = r.file("a.md", BRAINSTORM)
            V.commit_push(r.d, V.plan_push(r.d, "p", src), author="a",
                          now="2026-06-27T00:00:00+00:00")
            again = V.plan_push(r.d, "p", src)
            self.assertEqual(again.status, "unchanged")

    def test_changed_repush_refused_without_force(self):
        with _TempRepo() as r:
            V.commit_push(r.d, V.plan_push(r.d, "p", r.file("a.md", BRAINSTORM)),
                          author="a", now="2026-06-27T00:00:00+00:00")
            changed = r.file("b.md", BRAINSTORM + "\nNew section.\n")
            plan = V.plan_push(r.d, "p", changed, force=False)
            self.assertEqual(plan.status, "conflict")
            self.assertTrue(plan.blocked)
            with self.assertRaises(V.VaultError):
                V.commit_push(r.d, plan)            # commit refuses a conflict plan
            # original content untouched
            with open(os.path.join(r.d, MOKATA_DIR, "vault", "p.md"), encoding="utf-8") as fh:
                self.assertEqual(fh.read(), BRAINSTORM)

    def test_force_versions_and_keeps_prior_metadata(self):
        with _TempRepo() as r:
            V.commit_push(r.d, V.plan_push(r.d, "p", r.file("a.md", BRAINSTORM)),
                          author="alice", now="2026-06-27T00:00:00+00:00")
            v1_hash = V.content_hash(BRAINSTORM)
            new_body = BRAINSTORM + "\nDecision revised.\n"
            changed = r.file("b.md", new_body)
            plan = V.plan_push(r.d, "p", changed, force=True)
            self.assertEqual(plan.status, "version")
            entry = V.commit_push(r.d, plan, author="bob", now="2026-06-28T00:00:00+00:00")

            self.assertEqual(entry.version, 2)
            self.assertEqual(entry.content_hash, V.content_hash(new_body))
            self.assertEqual(entry.created_at, "2026-06-27T00:00:00+00:00")  # preserved
            self.assertEqual(entry.updated_at, "2026-06-28T00:00:00+00:00")
            self.assertEqual(len(entry.history), 1)
            self.assertEqual(entry.history[0]["version"], 1)
            self.assertEqual(entry.history[0]["content_hash"], v1_hash)
            self.assertEqual(entry.history[0]["author"], "alice")
            # current artifact is the new content
            with open(os.path.join(r.d, MOKATA_DIR, "vault", "p.md"), encoding="utf-8") as fh:
                self.assertEqual(fh.read(), new_body)


# ----------------------------------------------------------------- CLI surface (gated push)

class TestCliVault(unittest.TestCase):
    def _init(self, d):
        from mokata import cli
        cli.main(["init", "--path", d, "--yes"])

    def test_cli_push_list_search_pull(self):
        from mokata import cli
        import io
        import contextlib
        with _TempRepo() as r:
            self._init(r.d)
            src = r.file("plan.md", BRAINSTORM)
            rc = cli.main(["vault", "push", "payments-redesign", src,
                           "--yes", "--author", "alice", "--path", r.d])
            self.assertEqual(rc, 0)
            self.assertTrue(os.path.exists(
                os.path.join(r.d, MOKATA_DIR, "vault", "payments-redesign.md")))

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                cli.main(["vault", "list", "--path", r.d])
            self.assertIn("payments-redesign", buf.getvalue())

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                cli.main(["vault", "search", "idempotent ledger", "--path", r.d])
            self.assertIn("payments-redesign", buf.getvalue())

            dest = os.path.join(r.d, "out.md")
            rc = cli.main(["vault", "pull", "payments-redesign", "--dest", dest,
                           "--path", r.d])
            self.assertEqual(rc, 0)
            with open(dest, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), BRAINSTORM)

    def test_cli_changed_repush_without_force_fails(self):
        from mokata import cli
        with _TempRepo() as r:
            self._init(r.d)
            cli.main(["vault", "push", "p", r.file("a.md", BRAINSTORM),
                      "--yes", "--path", r.d])
            rc = cli.main(["vault", "push", "p", r.file("b.md", BRAINSTORM + "\nmore\n"),
                           "--yes", "--path", r.d])
            self.assertEqual(rc, 1)   # refused — no silent clobber

    def test_cli_push_blocks_secret_in_artifact(self):
        from mokata import cli
        with _TempRepo() as r:
            self._init(r.d)
            leak = "# Plan\n\naws key AKIA1234567890ABCDEF and more text here.\n"
            rc = cli.main(["vault", "push", "leaky", r.file("c.md", leak),
                           "--yes", "--path", r.d])
            self.assertEqual(rc, 1)   # WriteGate secret block — confirm can't override
            self.assertFalse(os.path.exists(
                os.path.join(r.d, MOKATA_DIR, "vault", "leaky.md")))


# ----------------------------------------------------------------- MCP tools (propose-only)

class TestMcpVaultTools(unittest.TestCase):
    def test_vault_push_proposes_without_confirm(self):
        from mokata import mcp_server as M
        with _TempRepo() as r:
            from mokata import cli
            cli.main(["init", "--path", r.d, "--yes"])
            src = r.file("plan.md", BRAINSTORM)
            res = M.vault_push(path=r.d, name="payments-redesign", file=src, author="alice")
            self.assertEqual(res["status"], "proposed")
            self.assertFalse(os.path.exists(
                os.path.join(r.d, MOKATA_DIR, "vault", "payments-redesign.md")))

            res = mcp_commit(M.vault_push, path=r.d, name="payments-redesign", file=src,
                             author="alice")
            self.assertTrue(res["committed"])
            self.assertEqual(res["version"], 1)

    def test_vault_read_tools(self):
        from mokata import mcp_server as M
        with _TempRepo() as r:
            from mokata import cli
            cli.main(["init", "--path", r.d, "--yes"])
            V.commit_push(r.d, V.plan_push(r.d, "payments-redesign",
                                           r.file("a.md", BRAINSTORM)),
                          author="alice", now="2026-06-27T00:00:00+00:00")
            self.assertEqual(M.vault_list(path=r.d)["count"], 1)
            hits = M.vault_search(path=r.d, query="idempotent ledger")
            self.assertEqual(hits["hits"][0]["name"], "payments-redesign")
            pulled = M.vault_pull(path=r.d, name="payments-redesign")
            self.assertEqual(pulled["content"], BRAINSTORM)
            self.assertEqual(pulled["author"], "alice")

    def test_vault_push_conflict_without_force(self):
        from mokata import mcp_server as M
        with _TempRepo() as r:
            from mokata import cli
            cli.main(["init", "--path", r.d, "--yes"])
            V.commit_push(r.d, V.plan_push(r.d, "p", r.file("a.md", BRAINSTORM)),
                          author="a", now="2026-06-27T00:00:00+00:00")
            res = mcp_commit(M.vault_push, path=r.d, name="p",
                             file=r.file("b.md", BRAINSTORM + "\nx\n"))
            self.assertEqual(res["status"], "conflict")
            self.assertFalse(res["committed"])


if __name__ == "__main__":
    unittest.main()
