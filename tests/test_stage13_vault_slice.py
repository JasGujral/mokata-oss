"""0.0.18 lane D SLICE 4 — the `vault` SESSION-TRANSPORT channel is removed. The lane closes here.

⭐⭐ WHAT THIS SLICE IS, AND WHY THE FIRST HALF OF THE FILE IS ABOUT WHAT SURVIVED.

`tests/_deprecation_removal.py` mapped `"vault": "mokata.vault"`, and the module is NOT the
channel. It is the Stage 35d design-artifact vault — four CLI verbs, four MCP tools, and `team
join --vault`, whose safety against an untrusted teammate's repo IS `vault_pull`'s content-hash
verification. Satisfying that map as written meant deleting all of it: the SECOND time this map
named a feature (stage 11 hit `"memory-share": "mokata.memory.share"`), and the larger of the two.

THE CHANNEL IS THE TRANSPORT KIND, derived three ways rather than read off the prose:

  1. the deprecation notice's REPLACEMENT clause named sessions and only sessions;
  2. `mokata migrate vault` re-homed session bundles, and its module docstring said the artifact
     vault *"has no canonical memory store to fold into … left for the human"*;
  3. the single production `warn_deprecated("vault", …)` call site was `make_transport`'s vault
     arm — so a user of `mokata vault push/list/pull` was never once told it was going.

THE RULE, so the next removal does not re-litigate it: **A CHANNEL IS WHAT THE NOTICE OFFERS A
REPLACEMENT FOR AND WHAT `mokata migrate <channel>` ACTUALLY MOVES. What a notice merely NAMES but
neither replaces nor migrates is a FEATURE sharing the channel's storage. Storage adjacency is not
channel membership.** Here the bundles lived at `.mokata/vault/sessions/` — inside the surviving
feature's own directory — so "delete the vault" and "delete the directory holding a user's saved
sessions AND their design specs" were one keystroke apart.

⚠ E7 IS RULED (Jas, 2026-08-15) and three of its consequences are pinned in section F: a slipping
`at=` re-fires nothing, a changed `filed=` re-fires everything, and a rendered notice names the
release its own channel left in rather than whatever the declaration currently promises.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import contextlib
import importlib.util
import inspect
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

import _deprecation_removal as DR

from mokata import MOKATA_DIR, deprecation
from mokata import session_bundle as SB
from mokata import session_transport as STX
from mokata import vault as V
from mokata.cli import main
from mokata.config import Surface
from mokata.init import init_repo

CHANNEL = "vault"


def _silent(*_a, **_k):
    pass


def _repo(d):
    init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
    return Surface.load(d)


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = main(argv)
    return rc, out.getvalue(), err.getvalue()


def _seed_run(surface, passed=("brainstorm", "analysis")):
    from mokata.govern.resume import PipelineCheckpoint
    run = "r1"
    cp = PipelineCheckpoint(surface.state, run)
    for phase in passed:
        cp.mark_passed(phase)
    return run


def _plant_bundles(root, tags):
    """Put real session bundles where the REMOVED transport kept them, byte-for-byte as it did.

    §7i — the tree ships no such repo, so the offender is planted. It is written with a plain
    `open` rather than through any mokata helper on purpose: the removed transport is gone, so a
    test that needed it to plant its own fixture could not run at all, and one that used the
    SURVIVING transport to write into the removed store would be asserting on a directory the
    production code no longer has any reason to agree about."""
    directory = os.path.join(root, MOKATA_DIR, "vault", "sessions")
    os.makedirs(directory, exist_ok=True)
    for tag in tags:
        with open(os.path.join(directory, f"{tag}.json"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"tag": tag}))
    return directory


# ==================================================================== A. the channel is GONE

class TestTheChannelIsGone(unittest.TestCase):
    def test_stage13_the_transport_kind_and_its_class_are_deleted(self):
        self.assertFalse(hasattr(STX, "VaultTransport"))
        self.assertFalse(hasattr(STX, "VAULT_SUBDIR"))
        self.assertNotIn("vault", STX.TRANSPORT_KINDS)

    def test_stage13_the_migrator_module_is_deleted_whole(self):
        # Graded by IMPORT RESOLUTION, not by a source grep: a module-level `__getattr__` shim or
        # a re-export under another name would satisfy a grep and fail this.
        self.assertIsNone(importlib.util.find_spec("mokata.migrate_channels"))

    def test_stage13_the_registry_moved_the_channel_rather_than_keeping_it_in_both(self):
        self.assertNotIn(CHANNEL, deprecation.CHANNELS)
        self.assertNotIn(CHANNEL, deprecation.DEPRECATED_CHANNELS)
        self.assertIn(CHANNEL, deprecation.REMOVED)
        self.assertIn(CHANNEL, deprecation.REMOVED_CHANNELS)

    def test_stage13_no_surviving_path_reaches_the_removed_transport_by_another_name(self):
        """A slice that makes the import probe pass while leaving the subsystem reachable has not
        deleted it, it has HIDDEN it (Part A, A3). Walks the shipped source rather than trusting
        the probe's one symbol."""
        import pathlib
        offenders = []
        for path in sorted(pathlib.Path("src/mokata").rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), 1):
                code = line.split("#", 1)[0]
                if "VaultTransport" in code or "VAULT_SUBDIR" in code:
                    offenders.append(f"{path}:{lineno}")
        self.assertEqual(offenders, [], f"the removed transport is still reachable: {offenders}")


# ================================================= B. the FEATURE survives, in full and in use

class TestTheArtifactVaultSurvives(unittest.TestCase):
    """⭐ A DELETION IS GRADED BY WHAT SURVIVES, and here that is most of the subject. These are
    not smoke tests: every one of them is a thing that would have been destroyed by satisfying the
    gate's own map as it was written."""

    def test_stage13_the_module_and_all_four_verbs_are_intact(self):
        for symbol in ("vault_list", "vault_search", "vault_pull", "plan_push", "commit_push",
                       "content_hash", "index_lock", "vault_dir"):
            self.assertTrue(hasattr(V, symbol), f"vault.{symbol} was taken by the slice")

    def test_stage13_a_users_artifacts_round_trip_through_the_surviving_cli(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            src = os.path.join(d, "spec.md")
            with open(src, "w", encoding="utf-8") as fh:
                fh.write("# Payments spec\n\nthe what.\n")
            rc, out, err = _run(["vault", "push", "payments", src, "--yes", "--path", d])
            self.assertEqual(rc, 0, err)
            rc, out, err = _run(["vault", "list", "--path", d])
            self.assertEqual(rc, 0, err)
            self.assertIn("payments", out)
            rc, out, err = _run(["vault", "search", "payments", "--path", d])
            self.assertEqual(rc, 0, err)
            self.assertIn("payments", out)
            dest = os.path.join(d, "pulled.md")
            rc, out, err = _run(["vault", "pull", "payments", "--dest", dest, "--path", d])
            self.assertEqual(rc, 0, err)
            with open(dest, encoding="utf-8") as fh:
                self.assertIn("Payments spec", fh.read())

    def test_stage13_the_integrity_refusal_no_longer_sells_a_migration(self):
        """⚠ A FALSE REMEDY THAT SHIPPED, AND IT IS WHY THE MAP LOOKED RIGHT. The corrupt-artifact
        refusal told a user the vault was deprecated and to run `mokata migrate vault` — which
        re-homed SESSION BUNDLES and could not touch an artifact. Slice 2's false-refusal class on
        a live surface: a remedy that looks correct and does nothing for the person reading it."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            src = os.path.join(d, "a.md")
            with open(src, "w", encoding="utf-8") as fh:
                fh.write("# A\n")
            V.commit_push(d, V.plan_push(d, "a", src), author="x")
            with open(os.path.join(d, MOKATA_DIR, "vault", "a.md"), "w", encoding="utf-8") as fh:
                fh.write("tampered\n")
            with self.assertRaises(V.VaultError) as cm:
                V.vault_pull(d, "a")
            message = str(cm.exception)
            self.assertIn("content-hash", message)
            self.assertNotIn("deprecated", message)
            self.assertNotIn("migrate vault", message)

    def test_stage13_team_join_still_hash_verifies_an_untrusted_shared_vault(self):
        """The dependency that made the map's target dangerous rather than merely wrong: `team
        join --vault` reads ANOTHER repo's vault, and what stops a tampered artifact landing is
        `vault_pull`'s hash check. Graded by tampering with the source repo's artifact."""
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            _repo(a)
            _repo(b)
            src = os.path.join(a, "d.md")
            with open(src, "w", encoding="utf-8") as fh:
                fh.write("# Design\n")
            V.commit_push(a, V.plan_push(a, "design", src), author="x")
            with open(os.path.join(a, MOKATA_DIR, "vault", "design.md"), "w",
                      encoding="utf-8") as fh:
                fh.write("tampered by someone else\n")
            with self.assertRaises(V.VaultError):
                V.vault_pull(a, "design")
            # …and the tampered bytes never reached the joining repo.
            self.assertFalse(os.path.exists(os.path.join(b, MOKATA_DIR, "vault", "design.md")))


class TestTheThreeSurvivingTransports(unittest.TestCase):
    """The file this slice cuts is three-quarters survivor. Real round trips, not mocks: each
    transport WRITES, READS BACK, LISTS and DELETES its own bytes."""

    class _FakePg:
        def __init__(self):
            self.rows = {}

        def execute(self, sql, params=()):
            outer = self

            class _Cur:
                rowcount = 0

                def fetchone(self):
                    key = (params[0], params[1]) if "INSERT" in sql else (params[1], params[0])
                    val = outer.rows.get((params[-1] if "INSERT" in sql else params[0]))
                    return (val,) if val else None

                def fetchall(self):
                    return [(t,) for t in sorted(outer.rows)]
            if "INSERT" in sql:
                outer.rows[params[1]] = params[2]
            elif "DELETE" in sql:
                cur = _Cur()
                cur.rowcount = 1 if outer.rows.pop(params[0], None) is not None else 0
                return cur
            return _Cur()

    def _round_trip(self, transport, label):
        self.assertEqual(transport.list_tags(), [], f"{label}: started dirty")
        transport.write_bundle("alpha", '{"tag": "alpha"}')
        self.assertEqual(transport.read_bundle("alpha"), '{"tag": "alpha"}',
                         f"{label}: the bytes did not come back")
        self.assertIn("alpha", transport.list_tags(), f"{label}: not listed")
        self.assertTrue(transport.delete_bundle("alpha"), f"{label}: delete reported nothing")
        self.assertIsNone(transport.read_bundle("alpha"), f"{label}: still readable after delete")

    def test_stage13_local_transport_round_trips_on_real_files(self):
        with tempfile.TemporaryDirectory() as d:
            self._round_trip(STX.LocalTransport(d), "local")

    def test_stage13_the_file_base_class_round_trips_on_real_files(self):
        with tempfile.TemporaryDirectory() as d:
            self._round_trip(STX._FileTransport(os.path.join(d, "store")), "_FileTransport")

    def test_stage13_postgres_transport_round_trips_on_its_injected_client(self):
        with tempfile.TemporaryDirectory() as d:
            self._round_trip(STX.PostgresTransport(client=self._FakePg(), project="p"),
                             "postgres")

    def test_stage13_the_factory_still_builds_both_surviving_kinds(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            self.assertIsInstance(STX.make_transport(None, d), STX.LocalTransport)
            self.assertIsInstance(STX.make_transport("local", d), STX.LocalTransport)
            self.assertIsInstance(
                STX.make_transport("postgres", d, client=self._FakePg()), STX.PostgresTransport)
            self.assertEqual(STX.TRANSPORT_KINDS, ("local", "postgres"))

    def test_stage13_a_session_still_pushes_and_pulls_into_another_repo(self):
        """The end-to-end survivor: the gated push/pull path over a surviving transport, all the
        way to a resumable checkpoint in a DIFFERENT repo."""
        from mokata.govern.resume import PipelineCheckpoint
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            src = _repo(a)
            run = _seed_run(src)
            transport = STX.make_transport("local", a)
            plan = SB.plan_session_push(a, src, "auth", transport=transport,
                                        now="2026-06-30T00:00:00+00:00")
            self.assertTrue(SB.commit_session_push_gated(plan, confirm=lambda _t: True).committed)
            dst = _repo(b)
            pull = SB.plan_session_pull(a, "auth", b, transport=transport)
            self.assertEqual(pull.status, "ok")
            self.assertTrue(SB.hydrate_bundle(dst, pull.bundle, confirm=lambda _t: True).committed)
            self.assertEqual(PipelineCheckpoint(dst.state, run).resume_phase(), "strawman")


# ============================== C. A2 — the user's data, twice, and NEITHER kind is destroyed

class TestNothingOnDiskIsDestroyed(unittest.TestCase):
    """Part A's A1: a user's `.mokata/` is DATA. Two kinds of it sit under `.mokata/vault/` and
    they get DIFFERENT answers, because they are different facts (§7g): the artifacts belong to a
    feature that never left, and the bundles belong to a channel that did."""

    def test_stage13_a_user_with_artifacts_loses_nothing_and_is_told_nothing(self):
        """⚠ THE SILENCE IS THE ASSERTION. An artifact user must not receive a removal notice —
        nothing of theirs was removed, and telling them otherwise is the false refusal in the
        other direction."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            src = os.path.join(d, "s.md")
            with open(src, "w", encoding="utf-8") as fh:
                fh.write("# Spec\n\nbody\n")
            V.commit_push(d, V.plan_push(d, "spec", src), author="x")
            before = open(os.path.join(d, MOKATA_DIR, "vault", "spec.md"),
                          encoding="utf-8").read()

            rc, out, err = _run(["vault", "list", "--path", d])
            self.assertEqual(rc, 0, err)
            self.assertIn("spec", out)
            self.assertNotIn("REMOVED", out + err)
            self.assertNotIn("removed", err)
            # byte-identical on disk, and still served by the read path
            after = open(os.path.join(d, MOKATA_DIR, "vault", "spec.md"), encoding="utf-8").read()
            self.assertEqual(before, after)
            self.assertEqual(V.vault_pull(d, "spec")[0], before)

    def test_stage13_a_user_with_session_bundles_is_refused_loudly_and_keeps_every_byte(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            directory = _plant_bundles(d, ("alpha", "beta"))
            # CORPUS: THE FIXTURE — a tempdir this test planted three lines up, so "tracked" and
            # "shipped" have no meaning in it; the disk is the only ground truth there is.
            before = sorted(os.listdir(directory))

            with self.assertRaises(deprecation.RemovedChannelError) as cm:
                STX.make_transport("vault", d)
            message = str(cm.exception)
            self.assertIn("was REMOVED in mokata", message)
            self.assertIn(directory, message)           # it says WHERE
            self.assertNotIn("pip install", message)    # …and does NOT charge a downgrade
            # CORPUS: THE FIXTURE — the same planted tempdir, re-read after the refusal.
            self.assertEqual(sorted(os.listdir(directory)), before, "bundles were touched")

    def test_stage13_session_list_says_so_rather_than_answering_no_bundles(self):
        """★ THE DEFECT THIS SLICE WOULD HAVE SHIPPED. `session list` spanned local + vault. Drop
        the vault leg and say nothing else, and a user whose bundles are ALL in the vault store
        reads *"no shared bundles — `mokata session push <tag>` to package this session"* — an
        invitation to redo work that is already saved. That is slice 1's empty-SQLite-floor answer
        wearing a friendlier face, and it is exit 0 either way."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _plant_bundles(d, ("alpha", "beta"))
            rc, out, err = _run(["session", "list", "--path", d])
            self.assertEqual(rc, 0, err)
            self.assertIn("was REMOVED in mokata", err)
            self.assertIn("alpha", err)
            self.assertIn("beta", err)

    def test_stage13_it_announces_on_a_NON_empty_listing_too(self):
        """⚠ THE CASE AN `if not infos` GUARD CANNOT SEE, and the reason the announcement is
        unconditional: one local bundle plus nine stranded ones produces a listing that is
        non-empty AND short, which reads as complete."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed_run(surface)
            plan = SB.plan_session_push(d, surface, "local-one",
                                        transport=STX.LocalTransport(d),
                                        now="2026-06-30T00:00:00+00:00")
            SB.commit_session_push(plan)
            _plant_bundles(d, ("stranded",))
            rc, out, err = _run(["session", "list", "--path", d])
            self.assertEqual(rc, 0, err)
            self.assertIn("local-one", out)              # the listing is real…
            self.assertIn("stranded", err)               # …and still admits what it cannot show

    def test_stage13_a_repo_with_no_bundles_hears_nothing_about_it(self):
        # The other half of §7g: no data, no announcement. A notice for a repo that never used the
        # channel is noise, and noise is how a once-per-repo discipline gets ignored.
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            rc, out, err = _run(["session", "list", "--path", d])
            self.assertEqual(rc, 0, err)
            self.assertNotIn("REMOVED", err)


class TestTheRemedyIsRunnable(unittest.TestCase):
    """⚠ STAGE 10's FINDING: CHECK THE REMEDY IS RUNNABLE, BECAUSE SLICE 1's WAS NOT. Not "the
    message names a command" — RUN it, from the planted state, and assert the user gets their
    session back."""

    def test_stage13_the_named_remedy_actually_restores_a_stranded_session(self):
        from mokata.govern.resume import PipelineCheckpoint
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            src = _repo(a)
            run = _seed_run(src)
            # Build a REAL bundle and put it where the removed transport kept it. Written through
            # the file store's own shape, which is the whole reason no conversion is needed.
            plan = SB.plan_session_push(a, src, "auth", transport=STX.LocalTransport(a),
                                        now="2026-06-30T00:00:00+00:00")
            SB.commit_session_push(plan)
            local_dir = os.path.join(a, MOKATA_DIR, STX.LOCAL_DIRNAME)
            stranded_dir = os.path.join(a, MOKATA_DIR, "vault", "sessions")
            os.makedirs(stranded_dir, exist_ok=True)
            shutil.move(os.path.join(local_dir, "auth.json"),
                        os.path.join(stranded_dir, "auth.json"))
            self.assertEqual(STX.LocalTransport(a).list_tags(), [], "precondition: it is stranded")

            # THE REMEDY, exactly as the record words it: move them into `.mokata/session-bundles/`
            # and `session pull` reads them.
            remedy = deprecation.REMOVED[CHANNEL].remedy
            self.assertIn(".mokata/session-bundles/", remedy)
            self.assertIn("session pull", remedy)
            shutil.move(os.path.join(stranded_dir, "auth.json"),
                        os.path.join(local_dir, "auth.json"))

            dst = _repo(b)
            pull = SB.plan_session_pull(a, "auth", b, transport=STX.LocalTransport(a))
            self.assertEqual(pull.status, "ok", "the remedy did not produce a readable bundle")
            self.assertTrue(SB.hydrate_bundle(dst, pull.bundle, confirm=lambda _t: True).committed)
            self.assertEqual(PipelineCheckpoint(dst.state, run).resume_phase(), "strawman")

    def test_stage13_the_remedy_names_no_downgrade(self):
        # Slice 2's lesson: charging a user a downgrade for something the release in their hands
        # already does is a FALSE refusal, and it looks correct. These bundles are `_FileTransport`
        # JSON and this release reads that shape.
        remedy = deprecation.REMOVED[CHANNEL].remedy
        self.assertNotIn("pip install", remedy)
        self.assertIsInstance(deprecation.REMOVED[CHANNEL], deprecation.RemovedFileNotice)


# ================================================== D. what `mokata migrate` IS now

class TestMigrateIsARemovalAnswerSurface(unittest.TestCase):
    """`MIGRATE-HELP-SELLS-REMOVED-CHANNELS` (doc 84 §1) — it blocked this cut and this closes it.

    The command survives with nothing to migrate, because THIS IS WHERE THE NOTICES SENT PEOPLE:
    a year of *"Migrate now with `mokata migrate <channel>`"* is in shipped wheels and published
    docs. Deleting it answers those users with `invalid choice: 'migrate'` from the TOP-LEVEL
    parser — the typo answer, one level further up than the same defect the last three slices
    each had to fix."""

    def _help(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            main(list(argv) + ["--help"])
        return " ".join(out.getvalue().split())

    def test_stage13_the_help_renders_no_empty_brace_and_sells_no_schedule(self):
        detail, top = self._help("migrate"), self._help()
        for text in (detail, top):
            self.assertNotIn("{}", text)
            self.assertNotIn(f"scheduled for removal in {deprecation.REMOVAL_RELEASE}", text)
            self.assertNotIn("work today with a deprecation warning", text)
        self.assertIn("no longer migrates", detail)

    def test_stage13_the_usage_line_advertises_EXACTLY_what_the_command_accepts(self):
        """⚠ THIS TEST EXISTS BECAUSE A MUTANT SURVIVED THE FIRST BATCH (§7f), and the survivor
        showed the assertion above was the WRONG SHAPE, not merely too narrow.

        `assertNotIn("{}")` grades one symptom of one past defect. Point `metavar` back at the LIVE
        registry and it renders `{neo4j}` — not empty, so the old assertion passes — while
        advertising a channel `choices` REFUSES: the help would send a user to type `mokata migrate
        neo4j` and argparse would answer `invalid choice`. Selling a channel the command rejects is
        the same class as selling one that was removed, and the empty-brace form was only its most
        visible instance.

        The property is EQUALITY between what is advertised and what is accepted, which cannot be
        satisfied by any wrong set."""
        usage = self._help("migrate")
        self.assertIn("{%s}" % ",".join(deprecation.REMOVED_CHANNELS), usage)
        for live in deprecation.CHANNELS:
            self.assertNotIn(live, usage,
                             f"the help advertises {live}, which this command does not accept")

    def test_stage13_every_removed_channel_gets_a_removal_answer_not_a_typo_answer(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            for channel in deprecation.REMOVED_CHANNELS:
                with self.subTest(channel=channel):
                    rc, _out, err = _run(["migrate", channel, "--path", d])
                    self.assertEqual(rc, 1)
                    self.assertNotIn("invalid choice", err)
                    self.assertIn("was REMOVED in mokata", err)

    def test_stage13_a_channel_removed_AFTER_this_stage_is_answered_too(self):
        """§7i — the bar is a channel this tree does not have, so it is PLANTED. The mechanism is
        derived end to end (`choices` and `metavar` are both `REMOVED_CHANNELS`; the record class
        is chosen by TYPE), so nothing here has to be retyped when stage 14 lands neo4j."""
        from mokata.cli_commands import migrate as M
        planted = deprecation.RemovedNotice(
            channel="ganymede", what="ganymede backend",
            data="Your data has NOT been touched.",
            remedy="Install the last release that shipped it, migrate, then upgrade again.",
            removed=deprecation.REMOVAL_RELEASE)
        removed = dict(deprecation.REMOVED, ganymede=planted)
        with tempfile.TemporaryDirectory() as d, contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.multiple(
                deprecation, REMOVED=removed, REMOVED_CHANNELS=tuple(removed)))
            # ⚠ TWO BINDINGS. `cli_commands/migrate.py` does `from ..deprecation import
            # REMOVED_CHANNELS`, so its namespace holds an import-time SNAPSHOT; patching only the
            # module attribute leaves `choices` built from the old tuple and the planted channel
            # comes back `invalid choice` — the test would report the defect it disproves.
            stack.enter_context(mock.patch.object(M, "REMOVED_CHANNELS", tuple(removed)))
            _repo(d)
            rc, _out, err = _run(["migrate", "ganymede", "--path", d])
            self.assertEqual(rc, 1)
            self.assertNotIn("invalid choice", err)
            self.assertIn("ganymede backend", err)

    def test_stage13_the_answer_names_the_channels_OWN_location(self):
        """⭐ STAGE 12 FILED THIS AND COULD NOT ASSERT IT. `REMOVED-FILE-PATH-IS-HARDCODED-TO-ONE-
        CHANNEL`: every file channel's refusal resolved its path through a function pinned to
        `memory-share.json`. Unreachable with one file channel — and slice 4 is the second, so the
        defect became reachable in the release that found it."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _plant_bundles(d, ("alpha",))
            _rc, _out, err = _run(["migrate", "vault", "--path", d])
            self.assertIn(os.path.join("vault", "sessions"), err)
            self.assertNotIn("memory-share.json", err)
            _rc, _out, err2 = _run(["migrate", "memory-share", "--path", d])
            self.assertIn("memory-share.json", err2)
            self.assertNotIn(os.path.join("vault", "sessions"), err2)

    def test_stage13_the_data_present_and_absent_answers_differ_without_the_exit_code_lying(self):
        # Two true sentences, ONE outcome: the migration did not happen either way, so a differing
        # exit code would be the §7g collapse one layer down (slice 2's split, kept).
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as e:
            _repo(d)
            _repo(e)
            _plant_bundles(d, ("alpha",))
            rc_full, _o, err_full = _run(["migrate", "vault", "--path", d])
            rc_empty, _o, err_empty = _run(["migrate", "vault", "--path", e])
            self.assertEqual((rc_full, rc_empty), (1, 1))
            self.assertIn("Yours is still at", err_full)
            self.assertIn("nothing here to bring across", err_empty)

    def test_stage13_an_EMPTY_leftover_directory_is_not_reported_as_data(self):
        # A directory a user already emptied is not data, and "your bundles are still at <path>"
        # would be false comfort pointing at nothing.
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            os.makedirs(os.path.join(d, MOKATA_DIR, "vault", "sessions"), exist_ok=True)
            _rc, _out, err = _run(["migrate", "vault", "--path", d])
            self.assertIn("nothing here to bring across", err)


class TestTheRemovedKindIsRefusedNotDegraded(unittest.TestCase):
    def test_stage13_make_transport_refuses_a_removed_kind_hard(self):
        # NOT `SessionTransportUnavailable`: a degrade tells a user to fix their environment and
        # retry, and this will never come back. Two facts, two exception types (§7g).
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            with self.assertRaises(deprecation.RemovedChannelError):
                STX.make_transport("vault", d)
            with self.assertRaises(STX.SessionTransportUnavailable):
                STX.make_transport("nope", d)

    def test_stage13_an_unknown_kind_is_not_offered_the_removed_one(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            with self.assertRaises(STX.SessionTransportUnavailable) as cm:
                STX.make_transport("nope", d)
            self.assertNotIn("vault", str(cm.exception))

    def test_stage13_the_cli_refuses_a_removed_from_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed_run(surface)
            rc, _out, err = _run(["session", "push", "auth", "--to", "vault", "--yes",
                                  "--path", d])
            self.assertNotEqual(rc, 0)
            self.assertFalse(os.path.exists(
                os.path.join(d, MOKATA_DIR, "vault", "sessions", "auth.json")))
            self.assertFalse(os.path.exists(
                os.path.join(d, MOKATA_DIR, STX.LOCAL_DIRNAME, "auth.json")),
                "the refusal silently downgraded the push to the local store")

    def test_stage13_the_mcp_listing_reports_removed_rather_than_unavailable(self):
        from mokata.mcp import tools_read as TR
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            res = TR.session_list(path=d, transport="vault")
            self.assertEqual(res["status"], "removed")
            self.assertIn("was REMOVED in mokata", res["message"])

    def test_stage13_the_mcp_listing_reports_stranded_bundles(self):
        from mokata.mcp import tools_read as TR
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _plant_bundles(d, ("alpha", "beta"))
            res = TR.session_list(path=d)
            self.assertEqual(res["removed_channel"]["stranded"], 2)
            self.assertIn("was REMOVED in mokata", res["removed_channel"]["message"])


# ========================================= E. THE GATE MOVES — and the movement is the deletion's

class TestTheGateOpensForVaultAndStaysClosedForNeo4j(unittest.TestCase):
    """⭐ THE FIRST SLICE WHOSE SUCCESS IS A GATE MOVING, so the bar is higher than "it moved":
    prove the movement is the DELETION's and not the ASSERTION's."""

    def test_stage13_this_channel_would_still_close_the_gate_if_it_came_back(self):
        # ★★ RE-POINTED AT 0.0.18 STAGE 14, like slices 1–3's twins, and for the reason all four
        # broke at once: this asserted the WHOLE implemented set — a LANE-level claim in a SLICE's
        # file — so each slice had to be edited whenever any other landed and the cheap fix was
        # always to bump a number. What belongs here is slice 4's OWN contribution to the gate,
        # graded BOTH ways against a planted offender (§7i). The lane's finish line is asserted
        # exactly once now, in `test_stage14_neo4j_removal`.
        present = DR.present_channels(DR.IMPLEMENTATIONS, DR.live_probe)
        self.assertNotIn("vault", present)
        self.assertEqual(DR.removal_state(deprecation.REMOVAL_RELEASE,
                                          deprecation.REMOVAL_RELEASE, frozenset({"vault"})),
                         DR.REMOVAL_OVERDUE)
        self.assertEqual(
            DR.removal_regressions(("vault",), DR.IMPLEMENTATIONS, lambda _t: True), ("vault",))
        self.assertEqual(
            DR.removal_regressions(("vault",), DR.IMPLEMENTATIONS, DR.live_probe), ())

    def test_stage13_the_movement_is_the_deletions_not_the_maps(self):
        """⚠ THE ONE EDIT THAT COULD CHEAT THE LANE. `IMPLEMENTATIONS["vault"]` was re-pointed
        from `mokata.vault` (the surviving feature) to the channel's behaviour. A re-point ALONE
        would open the gate with nothing deleted, so this asserts the gate is open under the OLD
        target too — i.e. `mokata.session_transport:VaultTransport` is gone whichever way you
        point at it, and the module the old target named is STILL IMPORTABLE, which is precisely
        what makes the old target the wrong one."""
        self.assertEqual(DR.IMPLEMENTATIONS["vault"],
                         "mokata.session_transport:VaultTransport")
        self.assertFalse(DR.live_probe(DR.IMPLEMENTATIONS["vault"]),
                         "the channel's implementation is still present")
        self.assertTrue(DR.live_probe("mokata.vault"),
                        "the OLD target still resolves — it names the surviving feature, which is "
                        "why satisfying it would have meant deleting one")

    def test_stage13_neo4j_was_left_for_stage_14_and_stage_14_took_it(self):
        # ⚠ REVERSED AT STAGE 14, not deleted. This graded slice 4's CONSTRAINT — "neo4j is stage
        # 14's: its CHANNELS membership, its notice and its implementation must all survive this
        # slice intact" — which was true of slice 4 and is false of the tree, because the stage it
        # was being kept for arrived. The constraint is discharged, and the assertion that records
        # the discharge is the opposite one on the same three surfaces.
        self.assertNotIn("neo4j", deprecation.CHANNELS)
        self.assertIn("neo4j", deprecation.REMOVED)
        self.assertFalse(DR.live_probe(DR.IMPLEMENTATIONS["neo4j"]))
        self.assertIsInstance(deprecation.REMOVED["neo4j"], deprecation.RemovedDerivedNotice)

    def test_stage13_the_registries_stay_exact_and_carry_no_regression(self):
        announced = set(deprecation.CHANNELS) | set(deprecation.REMOVED)
        self.assertEqual(DR.registry_drift(announced, DR.IMPLEMENTATIONS), ((), ()))
        self.assertEqual(DR.stale_notices(
            deprecation.CHANNELS, DR.present_channels(DR.IMPLEMENTATIONS, DR.live_probe)), ())
        self.assertEqual(DR.removal_regressions(
            deprecation.REMOVED, DR.IMPLEMENTATIONS, DR.live_probe), ())


# ============================================ F. E7 — the ruling, pinned in all three directions

class TestE7TheMarkerIsKeyedOnTheDECISION(unittest.TestCase):
    """E7, ruled by Jas 2026-08-15. Three pins, one per clause of the ruling."""

    def _fired(self, root, declaration):
        with mock.patch.object(deprecation, "REMOVAL_FILED",
                               deprecation.removal_filed(declaration)):
            return deprecation.warn_removed(
                CHANNEL, os.path.join(root, MOKATA_DIR), out=lambda _m: None)

    def test_stage13_a_slipping_at_re_fires_NOTHING(self):
        """The whole reason the key is `filed=` and not the release. A cut that misses 0.0.18
        moves `at=` to 0.0.19 without one channel's fate changing; keyed on the release, every
        repo on earth would re-read the same removals under a new number."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            self.assertTrue(self._fired(d, "REMOVAL(set=x, at=0.0.18, filed=2026-08-14)"))
            self.assertFalse(self._fired(d, "REMOVAL(set=x, at=0.0.19, filed=2026-08-14)"),
                             "a slipped release re-announced a removal that had not changed")
            self.assertFalse(self._fired(d, "REMOVAL(set=x, at=9.9.9, filed=2026-08-14)"))

    def test_stage13_a_changed_filed_re_fires_EVERYTHING(self):
        """The other direction, and it is deliberate that it re-fires channels removed under the
        EARLIER declaration too: the second notice announces a new DECISION, and the set it names
        is the set as it now stands."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            first = "REMOVAL(set=x, at=0.0.18, filed=2026-08-14)"
            second = "REMOVAL(set=x, at=0.0.18, filed=2026-09-01)"
            for channel in ("obsidian", "vault"):
                with mock.patch.object(deprecation, "REMOVAL_FILED",
                                       deprecation.removal_filed(first)):
                    self.assertTrue(deprecation.warn_removed(
                        channel, os.path.join(d, MOKATA_DIR), out=lambda _m: None))
                with mock.patch.object(deprecation, "REMOVAL_FILED",
                                       deprecation.removal_filed(second)):
                    self.assertTrue(
                        deprecation.warn_removed(channel, os.path.join(d, MOKATA_DIR),
                                                 out=lambda _m: None),
                        f"{channel}: a NEW removal decision did not re-fire")

    def test_stage13_a_notice_names_the_release_ITS_OWN_channel_left_in(self):
        """FROZEN, not derived. Asserted WITHOUT a release literal: the rendered text must carry
        the record's own `removed` and must NOT carry a moved declaration's release — which is the
        property, where a literal would only re-state today's value."""
        # ⚠ THROUGH `removal_answer` since stage 14 — see the twin correction in
        # `test_stage12_migrate_slice`. A hand-rolled two-branch dispatch inside a test that ranges
        # over the WHOLE registry is a copy of the thing under test, and it broke the moment the
        # registry grew a third record kind.
        moved = "0.0.19"
        with mock.patch.object(deprecation, "REMOVAL_RELEASE", moved):
            for channel, notice in deprecation.REMOVED.items():
                with self.subTest(channel=channel):
                    text = deprecation.removal_answer(channel, "/x")
                    self.assertIn(notice.removed, text)
                    self.assertNotIn(moved, text)

    def test_stage13_every_frozen_release_is_a_LITERAL_at_its_construction_site(self):
        """⚠ THIS TEST EXISTS BECAUSE A MUTANT SURVIVED THE FIRST BATCH (§7f), and the hole it
        exposed is the difference between grading a RENDER and grading a BINDING.

        `test_..._names_the_release_ITS_OWN_channel_left_in` patches `REMOVAL_RELEASE` and reads
        the rendered text — but the records are CONSTRUCTED at import, so one written
        `removed=REMOVAL_RELEASE` has already captured today's value and renders identically. The
        mutant that puts the live constant back therefore passed the test written to catch it. The
        freezing only becomes visible when the declaration MOVES, which is the one event a test
        cannot stage in-process: reloading the module re-reads the real declaration from source,
        and re-binds every class object, which breaks `except RemovedChannelError` everywhere else
        in the suite. (That was measured, not assumed — it took two other tests down with it.)

        So this grades the BINDING where it is written, over the module's own AST: every
        `removed=` on a removal record must be a string LITERAL. A literal cannot track anything.

        The two halves are separately gradable and neither covers for the other: this one sees a
        record wired to the live constant, the render test sees a record that names the wrong
        release for any other reason."""
        import ast
        source = inspect.getsource(deprecation)
        tree = ast.parse(source)
        # ⚠ THE RECORD CLASSES ARE DERIVED FROM THE MODULE, NOT TYPED (corrected at stage 14).
        # This read `("RemovedNotice", "RemovedFileNotice")` — a hand-typed scope on a derivation,
        # i.e. doc 85 §7j in the guard built to close a §7f hole. Stage 14 added a third record
        # class, and the typed tuple would have gone on passing while grading two of three: a new
        # record wired `removed=REMOVAL_RELEASE` would have been invisible to the only pin that can
        # see it. A record class is one that DECLARES a `removed` field; that is what the property
        # is about, and it cannot be out of date.
        record_classes = {
            node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
            and any(isinstance(stmt, ast.AnnAssign) and getattr(stmt.target, "id", "") == "removed"
                    for stmt in node.body)}
        self.assertGreaterEqual(len(record_classes), 3,
                                "the record classes are derived from `deprecation.py`; finding "
                                "fewer than the registry holds means the derivation broke")
        checked = 0
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if getattr(node.func, "id", None) not in record_classes:
                continue
            frozen = next((kw.value for kw in node.keywords if kw.arg == "removed"), None)
            self.assertIsNotNone(frozen, "a removal record was constructed with no `removed=`")
            self.assertIsInstance(
                frozen, ast.Constant,
                "a removal record's release is an expression, not a literal — it can track the "
                "declaration, which is the whole of what E7 froze")
            checked += 1
        self.assertEqual(checked, len(deprecation.REMOVED),
                         "the AST walk did not see every record — it would pass having graded "
                         "fewer sites than exist")

    def test_stage13_the_release_is_required_on_both_record_classes(self):
        # A default is what let history track a promise. Both classes must REFUSE to be built
        # without one, rather than quietly inheriting whatever the declaration says today.
        for cls in (deprecation.RemovedNotice, deprecation.RemovedFileNotice):
            with self.subTest(cls=cls.__name__):
                self.assertIsNot(inspect.signature(cls).parameters["removed"].default,
                                 inspect.Parameter.empty.__class__)
                with self.assertRaises(TypeError):
                    cls(channel="x", what="x", data="x", remedy="x")

    def test_stage13_an_unreadable_filed_stamp_raises_rather_than_silencing_a_notice(self):
        # `removal_target`'s contract, applied to the other field, and the failure mode is worse:
        # a silent default here SILENCES a notice instead of printing a wrong one.
        for bad in ("REMOVAL(set=x, at=0.0.18)", "REMOVAL(set=x, at=0.0.18, filed=soon)", ""):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    deprecation.removal_filed(bad)

    def test_stage13_the_ledger_record_carries_the_decision_as_well_as_the_release(self):
        from mokata.govern import AuditLedger
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            mokata_dir = os.path.join(d, MOKATA_DIR)
            ledger = AuditLedger.from_mokata_dir(mokata_dir)
            deprecation.warn_removed(CHANNEL, mokata_dir, out=lambda _m: None, ledger=ledger)
            rows = [e for e in ledger.entries()
                    if e.get("kind") == deprecation.REMOVAL_LEDGER_KIND]
            self.assertTrue(rows)
            self.assertEqual(rows[-1]["filed"], deprecation.REMOVAL_FILED)
            self.assertEqual(rows[-1]["removed"], deprecation.REMOVED[CHANNEL].removed)


# ==================================================== G. BACKCOMPAT-SWEEP (E2, the same pass)

class TestBackcompatSweep(unittest.TestCase):
    def test_stage13_the_transport_choices_are_derived_and_still_ANSWER_the_removed_kind(self):
        """⚠ THE FIRST DRAFT OF THIS SLICE SHIPPED THE LANE'S OWN DEFECT HERE, and the test is
        written the way it is because of that.

        The `--to/--from` choices were a hand-written `("local", "vault", "postgres")` triple — a
        second copy of `TRANSPORT_KINDS` this slice would have had to remember to edit, which is
        exactly how a removed channel goes on being advertised after its code is gone. Deriving
        them from `TRANSPORT_KINDS` alone fixed that and introduced the opposite fault: `--to
        vault` became `invalid choice: 'vault'` (exit 2), argparse's word for a TYPO, on a flag a
        year of shipped `--help` text told people to pass. `MIGRATE-ANSWERS-A-REMOVED-CHANNEL-AS-A-
        TYPO` on a second surface.

        So the property is BOTH halves, and neither alone: `choices` is the live registry PLUS the
        removed one (so the word is answered), `metavar` is the live registry ONLY (so the help
        does not sell it), and both are derived."""
        import argparse
        from mokata.cli_commands import collab as C
        root = argparse.ArgumentParser(prog="mokata")
        common = argparse.ArgumentParser(add_help=False)
        common.add_argument("--path", default=".")
        C.register(root.add_subparsers(), common)
        session = root._subparsers._group_actions[0].choices["session"]
        checked = 0
        for action in session._actions:
            if action.option_strings in (["--to"], ["--from"]) and action.choices:
                checked += 1
                self.assertEqual(tuple(action.choices),
                                 tuple(STX.TRANSPORT_KINDS) + tuple(deprecation.REMOVED_CHANNELS))
                self.assertEqual(action.metavar, "{%s}" % ",".join(STX.TRANSPORT_KINDS))
                self.assertNotIn("vault", action.metavar)
        self.assertEqual(checked, 2, "both --to and --from must be graded, not whichever was found")

    def test_stage13_no_shim_kept_the_channel_importable(self):
        """"Delete, do not deprecate" (§7d): no alias, no re-export, no compatibility name.
        Graded by ATTRIBUTE LOOKUP, the operation an importer performs, so a module-level
        `__getattr__` resurrecting the name would be caught where a grep would not."""
        import mokata
        for symbol in ("VaultTransport", "VAULT_SUBDIR"):
            with self.assertRaises(AttributeError, msg=f"session_transport.{symbol} resolves"):
                getattr(STX, symbol)
        with self.assertRaises(AttributeError):
            getattr(mokata, "migrate_channels")

    def test_stage13_the_shipped_session_template_no_longer_offers_the_removed_kind(self):
        # A slash-command template is a USER SURFACE: it is copied into the repo at init and read
        # as the menu of what works.
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for rel in (("templates", "commands", "session.md"), ("skills", "session", "SKILL.md")):
            with open(os.path.join(root, "src", "mokata", *rel), encoding="utf-8") as fh:
                text = fh.read()
            for offer in ("--to vault", "--from vault", "local|vault", "local/vault"):
                self.assertNotIn(offer, text, f"{rel[-1]} still offers {offer!r}")


if __name__ == "__main__":
    unittest.main()
