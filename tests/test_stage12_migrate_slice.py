"""0.0.18 lane D slice 3 — `migrate`: the slice whose deletion set is EMPTY, and why that is right.

⭐⭐ THE QUESTION THE BRIEF ASKED FIRST, ANSWERED BY MEASUREMENT: `migrate_channels.py` is not a
framework with channels plugged into it. It IS the vault migrator. Slice 2 emptied `CHANNELS` to
`("vault",)`, and every symbol left in the file exists to serve `_migrate_vault` — the one-time
marker, the plan, the result, the dispatch. There is no machinery separable from vault's arm, so
the third possibility the brief offered ("this stage removes the machinery and stage 13 removes
only vault's arm") is not available: removing the machinery removes `mokata migrate vault`, a
channel that is STILL DEPRECATED, that every 0.0.17 notice told users to run, and whose removal is
slice 4's to take. Doing it here would delete a live migration path one slice early — §7d's ONE
EXCEPTION, since the users who would notice are the ones with vault data.

★ ONE THING IN THE FILE *WAS* CHANNEL-MIGRATION MACHINERY, AND IT IS FIVE LINES. `_source_subjects`
dispatched on `channel == "vault"` with an unreachable `return []` fallback — a fan-out kept alive
for the three channels that left in slices 1 and 2. That is this slice's BACKCOMPAT-SWEEP, and it
is the whole of this slice's deletion.

SO WHAT THIS STAGE ACTUALLY OWNS is a FIX TO A SURVIVOR, and the brief said so: the tracker row
hedged that `MIGRATE-BACKENDS-UNCLOSED-ON-RAISE` "probably dies with its subject", stage 10
measured that `migrate_memory` survives, and so does the leak. THE LARGEST FILE IN THIS SLICE IS
NOT THE TARGET, for the third slice running.

WHAT IS GRADED HERE, one class per group:
  A. the both-handles leak — RED before the fix, GREEN after, on planted raises
  B. `migrate_memory` SURVIVES and works, on a real backend-to-backend round trip
  C. the removal answer generalises to a channel removed AFTER this stage (planted, §7i)
  D. the gate is STILL CLOSED — `vault` and `neo4j` remain, the verdict stays overdue
  E. what SURVIVES in `migrate_channels` — a deletion is graded by what is left (slice 1's rule)

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

import _deprecation_removal as DR
from _local_second_store import DESTINATION_TOOL, second_store

from mokata import deprecation
from mokata.cli import main
from mokata.config import Surface
from mokata.init import init_repo
from mokata.memory import MemoryItem, MemoryStore, migrate_memory
from mokata.memory import migrate as MM
from mokata.memory.backends import SQLiteBackend


def _silent(*_a):
    pass


def _repo(d, profile="standard"):
    init_repo(root=d, profile=profile, assume_yes=True, out=_silent)
    return Surface.load(d)


def _run(argv):
    """Run the CLI, returning `(rc, stdout, stderr)`. `SystemExit` is CAUGHT and its code returned
    — argparse exits 2 for `invalid choice`, which is one of the answers group C grades."""
    out, err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(argv)
    except SystemExit as exc:
        rc = exc.code if isinstance(exc.code, int) else 1
    return rc, out.getvalue(), err.getvalue()


def _seed(surface, items):
    """`items` is `(subject, value, author)` — the author is carried so PROVENANCE is observable
    across the migration, not just the value."""
    store = MemoryStore.from_surface(surface)
    for subject, value, author in items:
        store.remember(MemoryItem.create(subject, value, source=author, author=author),
                       assume_yes=True)
    store.close()


def _snapshot(backend):
    """{subject: (value, author-provenance)} for a built backend."""
    return {i.subject: (i.value, i.provenance.get("author")) for i in backend.all()}


# --------------------------------------------------------------------------------------------
# A COUNTING WRAPPER, and it counts rather than asserts-at-close on purpose: "was it closed" and
# "how many times" are different facts, and a wrapper that only recorded a boolean could not tell
# a fix that closes once from one that closes twice on the same exit path.
class _CountingBackend:
    """Wraps a real backend, counting `close()` and optionally raising from a named method.

    ⚠ IT ALSO REFUSES USE-AFTER-CLOSE, AND THAT IS NOT DECORATION. A file-backed `SQLiteBackend`
    opens a connection per operation, so its `close()` is very nearly a no-op and a handle closed
    far too early goes on working perfectly. Under the only backends CI can build, "the stack
    unwound before the migration ran" is therefore INVISIBLE unless the double is stricter than
    the real thing. It is stricter here on purpose: the contract every backend states is that a
    closed handle is finished with, and a test double that quietly tolerates the violation grades
    the postgres leg — where it is a real broken connection — not at all."""

    def __init__(self, inner, closes, *, raise_on=None, exc=None):
        self._inner, self._closes = inner, closes
        self._raise_on, self._exc = raise_on, exc
        self._closed = False

    def __getattr__(self, name):
        if name == self._raise_on:
            def _boom(*_a, **_kw):
                raise self._exc
            return _boom
        if self._closed and not name.startswith("_"):
            raise AssertionError(
                "use-after-close: %r was called on a backend handle that is already closed" % name)
        return getattr(self._inner, name)

    def close(self):
        self._closes.append(self)
        self._closed = True
        return self._inner.close()


class TestTheBothHandlesLeak(unittest.TestCase):
    """A — `MIGRATE-BACKENDS-UNCLOSED-ON-RAISE`, fixed in the SURVIVING code.

    §7i: the tree does not produce these raises, so they are PLANTED. Each one is a real reachable
    state, not a synthetic convenience — an unreadable source store, and a destination build that
    fails with something other than `MigrateError`."""

    def _handles(self, d, *, src_raise=None, dest_build_exc=None):
        """Build a surface plus a `build_named_backend` that hands back counting wrappers."""
        surface = _repo(d)
        _seed(surface, [("alpha", "one", "alice"), ("beta", "two", "bob")])
        closes = []
        real = MM.build_named_backend
        made = {"n": 0}

        def _build(tool, build_root, config=None, project=None):
            made["n"] += 1
            if made["n"] == 2 and dest_build_exc is not None:
                raise dest_build_exc
            if tool == DESTINATION_TOOL:
                inner = SQLiteBackend(os.path.join(d, "second-store.db"))
            else:
                inner = real(tool, build_root, config, project)
            kw = {}
            if made["n"] == 1 and src_raise is not None:
                kw = {"raise_on": src_raise, "exc": RuntimeError("planted: source unreadable")}
            return _CountingBackend(inner, closes, **kw)

        return surface, closes, mock.patch.object(MM, "build_named_backend", _build)

    def test_stage12_a_raise_after_both_builds_closes_both_handles(self):
        """THE ROW'S SUBJECT. `source.all()` is the first thing that runs once both handles exist,
        and it is not a `MigrateError` — so before the fix nothing caught it and BOTH handles
        leaked. This is the test that RED-then-GREENs."""
        with tempfile.TemporaryDirectory() as d:
            surface, closes, patch = self._handles(d, src_raise="all")
            with patch:
                with self.assertRaises(RuntimeError):
                    migrate_memory(surface, to_backend=DESTINATION_TOOL, from_backend="sqlite",
                                   assume_yes=True, out=_silent)
            self.assertEqual(len(closes), 2,
                             "a raise after both builds must close BOTH handles, exactly once "
                             "each — got %d close(s)" % len(closes))

    def test_stage12_a_non_migrate_error_from_the_destination_build_closes_the_source(self):
        """⚠ THE HALF THE OLD CODE GOT WRONG WITHOUT ANYONE NOTICING. The destination build was
        wrapped in `except MigrateError`, and that clause carried the only `source.close()` on that
        path. `build_named_backend`'s pgvector leg reaches `make_embedder` and
        `build_pgvector_backend`, neither of which promises `MigrateError` — so a bad embedder name
        leaked the source and said nothing about it."""
        with tempfile.TemporaryDirectory() as d:
            surface, closes, patch = self._handles(
                d, dest_build_exc=RuntimeError("planted: embedder blew up"))
            with patch:
                with self.assertRaises(RuntimeError):
                    migrate_memory(surface, to_backend=DESTINATION_TOOL, from_backend="sqlite",
                                   assume_yes=True, out=_silent)
            self.assertEqual(len(closes), 1,
                             "the source handle must be closed when the DESTINATION build raises "
                             "something that is not a MigrateError")

    def test_stage12_the_happy_path_still_closes_both_exactly_once(self):
        """⭐ THE SURVIVOR HALF OF THE FIX. A guard that only proves closes-on-raise passes just as
        well on code that closes twice, or that closes on the raise path and stopped closing on the
        normal one. Both handles, once each, on a migration that succeeds."""
        with tempfile.TemporaryDirectory() as d:
            surface, closes, patch = self._handles(d)
            with patch:
                res = migrate_memory(surface, to_backend=DESTINATION_TOOL, from_backend="sqlite",
                                     assume_yes=True, out=_silent)
            self.assertEqual(res.migrated, 2)
            self.assertEqual(len(closes), 2)
            self.assertEqual(len(set(id(c) for c in closes)), 2, "two DISTINCT handles")

    def test_stage12_a_declined_migration_closes_both_handles(self):
        """The human gate returns before any write. It is still an exit path with two open
        handles, and it used to be one of the two that remembered to close them — pinned so the
        stack cannot regress into closing only the paths a later reader thinks of."""
        with tempfile.TemporaryDirectory() as d:
            surface, closes, patch = self._handles(d)
            with patch:
                res = migrate_memory(surface, to_backend=DESTINATION_TOOL, from_backend="sqlite",
                                     confirm=lambda _t: False, out=_silent)
            self.assertTrue(res.aborted)
            self.assertEqual(len(closes), 2)

    def test_stage12_no_close_is_left_as_a_bare_statement_on_an_exit_path(self):
        """THE MECHANISM, not just its effects. The fix is that a close is registered where its
        handle is CREATED; a later edit that "helpfully" re-adds `source.close()` before a return
        would double-close and this pins the shape rather than waiting to find out."""
        import inspect
        import re
        src = inspect.getsource(MM.migrate_memory)
        self.assertIn("handles.callback(source.close)", src)
        self.assertIn("handles.callback(dest.close)", src)
        # ⭐ ANY INDENT, and that is the offender only THIS defence can see. The behavioural pins
        # above count closes through a test double, so they grade every path CI can reach — but
        # `to_funnel` is the postgres leg and CI has no Postgres, so a bare `source.close()` added
        # inside that branch is invisible to all of them while being exactly the defect the row is
        # about. A pin keyed to two specific indentation widths would have missed it too.
        bare = re.findall(r"^\s+(?:source|dest)\.close\(\)\s*$", src, re.MULTILINE)
        self.assertEqual(bare, [],
                         "a bare close on an exit path is what the row was about — the close "
                         "belongs on the stack, registered where the handle is created")


class TestMigrateMemorySurvives(unittest.TestCase):
    """B — the boundary. `memory/migrate.py` is a SURVIVING FEATURE and the largest file the row
    counted into this slice. Graded by a real backend-to-backend round trip, not a mock."""

    def test_stage12_a_real_round_trip_survives_the_slice(self):
        """OUT AND BACK across two real, distinct, durable stores — values AND provenance.

        Not a mock and not one leg: a one-way copy passes on an engine that loses the author on
        the way home, and the brief asked for the round trip because that is what a user of
        `mokata memory migrate` actually does when a team Postgres goes away."""
        expected = {"auth": ("jwt", "alice"), "db": ("postgres", "bob")}
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed(surface, [("auth", "jwt", "alice"), ("db", "postgres", "bob")])
            root = surface.mokata_dir

            with second_store(d) as dest_path:
                # leg 1 — sqlite -> the second store
                out = migrate_memory(surface, to_backend=DESTINATION_TOOL,
                                     from_backend="sqlite", assume_yes=True, out=_silent)
                self.assertEqual(out.migrated, 2, out.render())
                self.assertEqual((out.blocked, out.refused), (0, 0))
                dest = SQLiteBackend(dest_path)
                self.assertEqual(_snapshot(dest), expected)
                dest.close()

                # NON-DESTRUCTIVE: the source still holds everything.
                src = MM.build_named_backend("sqlite", root, {})
                self.assertEqual(_snapshot(src), expected)
                # wipe it, so leg 2 has something to prove
                for it in src.all():
                    src.delete(it.id)
                self.assertEqual(_snapshot(src), {})
                src.close()

                # leg 2 — back again
                back = migrate_memory(surface, to_backend="sqlite",
                                      from_backend=DESTINATION_TOOL, assume_yes=True, out=_silent)
            self.assertEqual(back.migrated, 2, back.render())
            restored = MemoryStore.from_surface(Surface.load(d))
            self.assertEqual(_snapshot(restored.backend), expected)
            restored.close()

    def test_stage12_the_cli_surface_survives_too(self):
        """`mokata memory migrate` is named in the constraints as a must-not-delete. The library
        function surviving is not the same fact as the COMMAND surviving (§7g)."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            rc, out, err = _run(["memory", "migrate", "--help"])
            self.assertEqual(rc, 0)
            self.assertIn("--to", out + err)

    def test_stage12_migrate_memory_is_not_reachable_from_the_removal_answer(self):
        """THE BOUNDARY, stated as a property rather than a list, and RE-POINTED AT SLICE 4.

        Channel migration and BACKEND migration share a verb and nothing else. The boundary used
        to be graded against `migrate_channels`, which slice 4 deleted; its heir is
        `cli_commands/migrate.py`, the surface that now answers for removed channels — and the
        drift the property guards against is the same one, in the same direction: the command that
        no longer migrates anything must not acquire a memory-store dependency on its way to
        becoming a pure answer."""
        import inspect
        from mokata.cli_commands import migrate as M
        src = inspect.getsource(M)
        self.assertNotIn("migrate_memory", src)
        self.assertNotIn("build_named_backend", src)


class TestTheRemovalAnswerGeneralises(unittest.TestCase):
    """C — stage 11 filed `MIGRATE-ANSWERS-A-REMOVED-CHANNEL-AS-A-TYPO` as a class and fixed it for
    the three channels removed by then. THIS FILE OWNS THE MECHANISM, so the bar is a channel
    removed AFTER this stage — which does not exist in this tree, so it is PLANTED (§7i).

    ⭐ The mechanism is DERIVED end to end, and that is what makes it generalise: `choices` is
    `CHANNELS + REMOVED_CHANNELS`, `REMOVED_CHANNELS` is `tuple(REMOVED)`, the dispatch tests
    `channel in REMOVED_CHANNELS`, and the record class is chosen by `is_removed_file_channel`
    (a TYPE test, not a name list). Nothing here has to be retyped when slice 4 lands."""

    def _with_planted(self, channel, notice):
        """⚠ TWO BINDINGS, AND MISSING THE SECOND MAKES THIS TEST LIE. `cli_commands/migrate.py`
        does `from ..deprecation import REMOVED_CHANNELS`, so its module namespace holds a
        SNAPSHOT of the tuple taken at import — patching `deprecation.REMOVED_CHANNELS` alone
        leaves `choices` built from the old one and the planted channel comes back as `invalid
        choice` (exit 2), i.e. the test would report the defect it exists to disprove.

        The snapshot is harmless in production precisely because both are module-level: `REMOVED`
        is complete before `deprecation` finishes importing, so whatever slice 4 adds is in the
        tuple before anything imports it. The derivation is real; it is just fixed at import."""
        import contextlib as _ctx
        from mokata.cli_commands import migrate as M
        removed = dict(deprecation.REMOVED)
        removed[channel] = notice
        stack = _ctx.ExitStack()
        stack.enter_context(mock.patch.multiple(
            deprecation, REMOVED=removed, REMOVED_CHANNELS=tuple(removed)))
        stack.enter_context(mock.patch.object(M, "REMOVED_CHANNELS", tuple(removed)))
        return stack

    def test_stage12_a_backend_removed_after_this_stage_gets_a_removal_answer(self):
        planted = deprecation.RemovedNotice(
            channel="ganymede", what="ganymede backend",
            data="Your data has NOT been touched.",
            remedy="Install the last release that shipped it, migrate, then upgrade again.",
            removed=deprecation.REMOVAL_RELEASE)
        with tempfile.TemporaryDirectory() as d, \
                self._with_planted("ganymede", planted):
            _repo(d)
            rc, _out, err = _run(["migrate", "ganymede", "--path", d])
            self.assertEqual(rc, 1)                        # the migration did not happen...
            self.assertNotIn("invalid choice", err)        # ...and it is NOT reported as a typo
            self.assertIn("was REMOVED in mokata", err)
            self.assertIn("ganymede backend", err)

    def test_stage12_a_file_channel_removed_after_this_stage_gets_the_file_answer(self):
        """The TYPE test picks the class, so a future FILE channel gets the file report — the one
        without a downgrade remedy. Stage 11's lesson 2: the remedy's shape is not portable, and
        the code must keep choosing rather than defaulting to whichever came first."""
        planted = deprecation.RemovedFileNotice(
            channel="callisto", what="callisto.json channel",
            data="mokata has deleted nothing — it is a file you own.",
            remedy="`mokata memory import --file <path>` restores it.",
            removed=deprecation.REMOVAL_RELEASE, path="callisto.json")
        with tempfile.TemporaryDirectory() as d, \
                self._with_planted("callisto", planted):
            _repo(d)
            rc, _out, err = _run(["migrate", "callisto", "--path", d])
            self.assertEqual(rc, 1)
            self.assertNotIn("invalid choice", err)
            self.assertIn("callisto.json channel", err)
            # ⚠ NOT a downgrade — that is the false refusal slice 2 caught.
            self.assertNotIn("pip install", err)
            # ⭐ FIXED AT SLICE 4, AND THIS ASSERTION IS WHAT STAGE 12 COULD NOT MAKE.
            # `REMOVED-FILE-PATH-IS-HARDCODED-TO-ONE-CHANNEL` was filed here: the refusal named
            # `memory-share.json`'s path for EVERY file channel, because the answer went through a
            # function hardcoded to the only one that existed. Slice 4 added the second file
            # channel, so the defect became reachable in the same release it was found — the
            # location is now a FIELD on the record and the answer reads the channel's own.
            self.assertIn("callisto.json", err)
            self.assertNotIn("memory-share.json", err)

    def test_stage12_every_removed_channel_is_answerable_today(self):
        """★ THE GUARD THAT MAKES SLICE 4's OMISSION RED INSTEAD OF A CRASH. `_answer_removed`
        reaches `REMOVED[channel]` and raises `KeyError` for a channel with no record. Because
        `choices` is derived from the same registry, a channel can only get there by being IN it —
        so what this actually grades is that every record RENDERS, for both classes, without the
        caller knowing which kind it holds."""
        # ⚠ THROUGH `removal_answer`, NOT A HAND-ROLLED TWO-BRANCH DISPATCH (corrected at stage
        # 14, which added a THIRD record kind). The `if is_removed_file_channel(...) else
        # removed_notice(...)` written here re-implemented the dispatch it was meant to grade, so
        # the day a third class arrived this test raised `KeyError` from the arm it fell into —
        # the exact defect it says it exists to make RED instead of a crash, in its own body.
        # `removal_answer` is the one dispatch, chosen by TYPE, and calling it is the test.
        for channel in deprecation.REMOVED_CHANNELS:
            with self.subTest(channel=channel):
                text = deprecation.removal_answer(channel, "/tmp/x")
                self.assertIn("removed", text.lower())
                self.assertIn(deprecation.REMOVED[channel].removed, text)

    def test_stage12_the_choices_list_is_derived_from_both_registries(self):
        """Not "the list currently contains the right names" — that passes on a hand-typed list
        the day it is typed. The property is that the list IS the two registries."""
        import argparse
        from mokata.cli_commands import migrate as M
        root = argparse.ArgumentParser(prog="mokata")
        common = argparse.ArgumentParser(add_help=False)
        common.add_argument("--path", default=".")
        M.register(root.add_subparsers(), common)
        action = next(a for a in root._subparsers._group_actions[0].choices["migrate"]._actions
                      if a.dest == "channel")
        # ⚠ ONE REGISTRY SINCE SLICE 4, and the property is unchanged: the list IS the registry.
        # `migrate_channels.CHANNELS` was the other half until that module was deleted; with no
        # live channel left, what the command accepts and what it advertises are the same set, so
        # `metavar` now EQUALS `choices` instead of withholding from it.
        self.assertEqual(tuple(action.choices), tuple(deprecation.REMOVED_CHANNELS))
        self.assertEqual(action.metavar, "{%s}" % ",".join(deprecation.REMOVED_CHANNELS))
        self.assertNotEqual(action.metavar, "{}")


class TestTheGateIsStillClosed(unittest.TestCase):
    """D — this stage removes NO channel, so the gate must be exactly as closed as slice 2 left
    it. Asserted as an EXACT SET, as stage 11 did: membership would pass on a lane that quietly
    removed something it was not asked to."""

    def test_stage12_the_verdict_is_still_overdue(self):
        # ⚠ RE-POINTED AT STAGE 14. Slice 3's deletion set was EMPTY, so it never had a channel of
        # its own to hold the gate open with — the exact-set assertion here was borrowed from the
        # lane, and the lane's finish line now lives in `test_stage14_neo4j_removal`. What is still
        # slice 3's is that its work moved NOTHING: no channel gained or lost an implementation,
        # and the registries stayed coherent.
        present = DR.present_channels(DR.IMPLEMENTATIONS, DR.live_probe)
        self.assertEqual(DR.stale_notices(deprecation.CHANNELS, present), ())
        self.assertEqual(DR.removal_state(deprecation.REMOVAL_RELEASE,
                                          deprecation.REMOVAL_RELEASE,
                                          frozenset({"planted"})),
                         DR.REMOVAL_OVERDUE)
        self.assertEqual(
            DR.removal_regressions(deprecation.REMOVED, DR.IMPLEMENTATIONS, DR.live_probe), ())


# `test_stage12_vault_is_untouched_and_still_the_one_migratable_channel` and the whole of
# `TestWhatSurvivesInMigrateChannels` stood here until 0.0.18 lane D slice 4. Both graded
# `migrate_channels`, which that slice DELETED — the first asserted the constraint slice 4 exists
# to lift ("vault keeps its migrator"), and the second graded the survivors inside a module with no
# survivors left. They are removed rather than re-pointed because their subject is gone, not moved;
# what replaced them is in `test_stage13_vault_slice.py`.


if __name__ == "__main__":
    unittest.main()
