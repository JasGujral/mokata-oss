"""0.0.18 lane D slice 2 — the `memory-share` CHANNEL is removed; the BACKUP SURFACE is not.

⭐ THE ROW SAID "memory-share (660)" AND NOT ONE OF THE THREE FILES BEHIND THAT NUMBER IS THE
CHANNEL. doc 102 spells the arithmetic out: `memory/share.py` 320 + `share.py` 277 +
`mcp/tools_share.py` 63. The second is J3 stack MANIFESTS (nothing to do with memory); the third
is the artifact-VAULT MCP tool, which belongs to slice 4 and says so in its own first line; and
the first is 35b's memory BACKUP surface — `mokata memory export` / `import`, a live, supported,
P23 feature. A slice that deleted the 660 would have removed two other features and a supported
command under the banner of removing a deprecated channel.

WHAT THE CHANNEL ACTUALLY WAS, measured: the special treatment of ONE destination FILENAME.
`MEMORY_SHARE_FILENAME`, the `is_legacy_share_dest` detector that made writing there warn, the
`mokata migrate memory-share` branch that read it, and the deprecation notice that announced it.
Everything else in that module is the backup surface and survives untouched (P22: removing a
deprecated channel must not cost a user a supported feature that shares a file with it).

★ AND THE USER'S FILE IS THE HALF THAT MATTERS. A `memory-share.json` is a file a human exported
and may have committed (P23 says they own it). §7d's ONE EXCEPTION applies — but the answer is the
OPPOSITE of slice 1's, and that is this slice's finding. A removed BACKEND leaves data behind a
store this release cannot open, so the honest remedy is a downgrade. A removed FILE channel leaves
a file this release READS: the channel file and a 35b backup are the same format, and
`mokata memory import` funnels both through the identical `import_memory` call the deleted branch
wrapped. Telling that user to `pip install 'mokata==<older>'` would be a FALSE REFUSAL — a
downgrade charged for something the release in their hands already does in one command. So there
are two removal-record classes now, and `RemovedNotice` is REFUSED for this channel by name.

WHAT IS GRADED HERE, one class per group:
  A. the channel is GONE, and the gate can SEE it is gone
  B. the user's planted file — both facts, and nothing destroyed in either
  C. what SURVIVES — a deletion is graded by what is left (slice 1's rule), on a REAL file
  D. no fallback path reaches the channel, and the removal RECORD is not a legacy reader
  E. the gate is STILL CLOSED — `vault` and `neo4j` remain, the 0.0.18 verdict stays overdue
  F. the two record classes cannot render each other's shape
  G. BACKCOMPAT-SWEEP (E2, same pass) — the parameters the deletion stranded are DELETED

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import ast
import glob
import inspect
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

import _support  # noqa: F401  (puts src/ on the path)

import _deprecation_removal as DR
from mokata import MOKATA_DIR
from mokata import deprecation
from mokata.cli import main
from mokata.config import Surface
from mokata.init import init_repo
from mokata.memory import (
    MemoryItem,
    MemoryStore,
    SHARE_KIND,
    SHARE_SCHEMA_VERSION,
    default_backup_path,
    export_memory,
    import_memory,
    load_memory_share,
    plan_memory_import,
)

CHANNEL = "memory-share"
LEGACY_FILENAME = "memory-share.json"


def _silent(*_a):
    pass


def _repo(d, profile="standard"):
    init_repo(root=d, profile=profile, assume_yes=True, out=_silent)
    return MemoryStore.from_surface(Surface.load(d))


def _plant_share_file(root, items):
    """§7i — the tree ships no `memory-share.json`, so a test about one has to write it.

    Written in the EXPORTED shape (schema_version / kind / items), which is what a 0.0.17 user's
    `mokata memory export --file .mokata/memory-share.json` actually produced."""
    path = os.path.join(root, MOKATA_DIR, LEGACY_FILENAME)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {"schema_version": SHARE_SCHEMA_VERSION, "kind": SHARE_KIND,
               "items": [i.to_dict() for i in items]}
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, indent=2) + "\n")
    return path


def _run(argv):
    """Run the CLI, returning `(rc, stdout, stderr)` — the surface a user actually sees.

    ⚠ `SystemExit` is CAUGHT AND ITS CODE RETURNED, not allowed to escape, because argparse uses
    it for two of the three answers this stage is about: `--help` exits 0 and an unrecognised
    channel exits 2. A harness that let it through could not tell those apart from a crash, which
    is the exact distinction (`removed` vs `invalid choice`) being graded."""
    out, err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(argv)
    except SystemExit as exc:                      # argparse: --help (0) / invalid choice (2)
        rc = exc.code if isinstance(exc.code, int) else 1
    return rc, out.getvalue(), err.getvalue()


def _src_files():
    """Every shipped `src/` module, as `{repo-relative path: text}` — a SUPPLIED corpus."""
    # CORPUS: THE WORKING TREE — deliberately, and the choice is load-bearing rather than
    # incidental. The question this walk answers is "can any module still REACH the removed
    # channel", and the modules that run are the ones ON DISK: a builder who reintroduced the
    # filename and had not yet staged it would be invisible to the index, and is exactly the state
    # a hiding check exists to catch. `src/` carries no untracked churn (unlike `tests/`), so the
    # two corpora differ here only while someone is mid-edit — which is when this guard is most
    # worth having.
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
    found = {}
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if not name.endswith(".py"):
                continue
            full = os.path.join(dirpath, name)
            with open(full, encoding="utf-8") as fh:
                found[_support.posix_rel(full, root).replace(os.sep, "/")] = fh.read()
    return found


# ================================================================== A. the channel is GONE

class TestChannelRemoved(unittest.TestCase):
    def test_stage11_channel_symbols_are_gone(self):
        # The channel was a filename and a detector. Both go, from the module AND from the
        # package re-export — a symbol still reachable through `mokata.memory` is not deleted.
        import mokata.memory as M
        import mokata.memory.share as S
        for symbol in ("MEMORY_SHARE_FILENAME", "is_legacy_share_dest"):
            self.assertFalse(hasattr(S, symbol), f"{symbol} survives in memory.share")
            self.assertFalse(hasattr(M, symbol), f"{symbol} survives in the memory package")
            self.assertNotIn(symbol, M.__all__)

    def test_stage11_channel_is_no_longer_deprecated_it_is_removed(self):
        # A deprecation notice for a channel that is gone tells a user to migrate off something
        # that is not there. The registry moves it; it does not keep it in both.
        self.assertNotIn(CHANNEL, deprecation.CHANNELS)
        self.assertNotIn(CHANNEL, deprecation.DEPRECATED_CHANNELS)
        self.assertIn(CHANNEL, deprecation.REMOVED)
        self.assertIn(CHANNEL, deprecation.REMOVED_CHANNELS)

    def test_stage11_migration_branch_is_gone(self):
        # `mokata migrate memory-share` had a whole read/import path. It is deleted, not disabled.
        # ⚠ WIDENED AT SLICE 4, WHICH DELETED THE WHOLE MODULE. Asserting three symbol names are
        # absent from a module that no longer exists would pass on the module's absence rather
        # than on the deletion — a guard satisfied by the wrong fact. The property that carries
        # this test's meaning now is that nothing anywhere imports the migrator.
        import importlib.util
        self.assertIsNone(importlib.util.find_spec("mokata.migrate_channels"))

    def test_stage11_no_export_path_warns_about_a_channel_any_more(self):
        # Both export surfaces (CLI + MCP) called `is_legacy_share_dest` and warned. A call site
        # left behind would be a NameError on the one path a user takes to write a backup.
        from mokata.cli_commands import memory as cli_memory
        from mokata.mcp import tools_memory
        for module in (cli_memory, tools_memory):
            source = inspect.getsource(module)
            self.assertNotIn("is_legacy_share_dest", source)
            self.assertNotIn('warn_deprecated("memory-share"', source)

    def test_stage11_the_gate_probe_sees_the_removal(self):
        # The lane's acceptance test (stage 9's), for THIS channel only.
        self.assertFalse(DR.live_probe(DR.IMPLEMENTATIONS[CHANNEL]))
        present = DR.present_channels(DR.IMPLEMENTATIONS, DR.live_probe)
        self.assertNotIn(CHANNEL, present)

    def test_stage11_the_probe_target_is_the_channel_not_the_backup_module(self):
        # ⚠ THE ONE EDIT THAT COULD CHEAT THE GATE, pinned as an intention rather than a diff.
        # The declared target used to be the whole module `mokata.memory.share`, which the backup
        # surface lives in — so the only way to satisfy it was to delete `export`/`import`. It is
        # now a SYMBOL in that module, and the module must still be importable.
        target = DR.IMPLEMENTATIONS[CHANNEL]
        module, _, symbol = target.partition(":")
        self.assertEqual(module, "mokata.memory.share")
        self.assertTrue(symbol, "the target must name a symbol, not the whole backup module")
        import importlib
        self.assertIsNotNone(importlib.import_module(module))       # the module SURVIVES


# ============================================== B. the user's file — two facts, nothing destroyed

class TestPlantedShareFile(unittest.TestCase):
    def test_stage11_migrate_answers_a_removed_channel_by_name_not_as_a_typo(self):
        # ★ THIS IS WHERE THE 0.0.17 NOTICE SENT PEOPLE: "Migrate now with `mokata migrate
        # memory-share`". Dropping the channel from argparse `choices` answers that command the
        # way it answers a misspelling — exit 2, "invalid choice" — which is §7g on the one
        # surface where the user is already doing what we asked.
        with tempfile.TemporaryDirectory() as d:
            _repo(d).close()
            rc, _out, err = _run(["migrate", CHANNEL, "--path", d])
            self.assertEqual(rc, 1)                       # the migration did not happen...
            self.assertNotIn("invalid choice", err)       # ...but it is not a typo either
            self.assertIn("was REMOVED in mokata", err)
            self.assertIn(deprecation.REMOVAL_RELEASE, err)

    def test_stage11_a_planted_file_is_named_and_the_remedy_is_the_import_command(self):
        with tempfile.TemporaryDirectory() as d:
            store = _repo(d)
            planted = _plant_share_file(d, [MemoryItem.create("indent", "four spaces")])
            store.close()
            with open(planted, encoding="utf-8") as fh:
                before = fh.read()

            rc, _out, err = _run(["migrate", CHANNEL, "--path", d])

            self.assertEqual(rc, 1)
            self.assertIn(planted, err)                   # WHERE their file is
            self.assertIn("mokata memory import", err)    # the ONE command that reads it
            # ⚠ AND THE REMEDY IS NOT A DOWNGRADE. Slice 1's remedy names an older release to
            # install; charging this user for one would be a false refusal.
            self.assertNotIn("pip install", err)
            self.assertNotIn(deprecation.LAST_SHIPPING_RELEASE, err)
            # NOTHING DESTROYED — §7d's exception, byte-for-byte.
            self.assertTrue(os.path.exists(planted))
            with open(planted, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), before)

    def test_stage11_no_file_is_a_different_sentence_and_writes_nothing(self):
        # The split is between two TRUE sentences, not two outcomes: print the path
        # unconditionally and a repo without the file is invited to run a command that fails.
        with tempfile.TemporaryDirectory() as d:
            _repo(d).close()
            expected = os.path.join(d, MOKATA_DIR, LEGACY_FILENAME)

            rc, _out, err = _run(["migrate", CHANNEL, "--path", d])

            self.assertEqual(rc, 1)                       # same outcome...
            self.assertIn("nothing here to bring across", err)   # ...different sentence
            self.assertNotIn("Yours is still at", err)
            self.assertFalse(os.path.exists(expected))    # and the refusal created nothing

    def test_stage11_the_two_sentences_are_actually_different(self):
        # Guards the split itself: a collapse that rendered one message for both facts would pass
        # each assertion above on its own file-state.
        present = deprecation.removed_file_report(CHANNEL, "/r/.mokata/memory-share.json", True)
        absent = deprecation.removed_file_report(CHANNEL, "/r/.mokata/memory-share.json", False)
        self.assertNotEqual(present, absent)
        self.assertIn("Yours is still at", present)
        self.assertIn("nothing here to bring across", absent)

    def test_stage11_the_remedy_the_refusal_names_actually_runs(self):
        # ⭐ SLICE 1's LESSON 2: check the remedy is RUNNABLE. There, dropping a detect type made
        # every old manifest invalid — including for the command the refusal named. Here the
        # remedy is `mokata memory import`, so RUN IT, on the very file the refusal pointed at.
        with tempfile.TemporaryDirectory() as d:
            store = _repo(d)
            items = [MemoryItem.create("indent", "four spaces"),
                     MemoryItem.create("db", "postgres per ADR-7")]
            planted = _plant_share_file(d, items)
            store.close()

            _rc, _out, err = _run(["migrate", CHANNEL, "--path", d])
            self.assertIn(planted, err)

            rc, out, _err = _run(["memory", "import", planted, "--path", d, "--yes"])
            self.assertEqual(rc, 0)
            self.assertIn("2 added", out)

            store = MemoryStore.from_surface(Surface.load(d))
            try:
                subjects = {i.subject for i in store.backend.all()}
            finally:
                store.close()
            self.assertEqual(subjects, {"indent", "db"})
            self.assertTrue(os.path.exists(planted))      # the restore is non-destructive

    def test_stage11_slice_1s_removed_backends_are_answered_here_too(self):
        # FOUND WHILE WIRING THIS, AND FIXED BY THE SAME MECHANISM RATHER THAN FILED. Slice 1
        # dropped `obsidian` / `native-memory` from `migrate_channels.CHANNELS`, so since that
        # stage `mokata migrate obsidian` has answered "invalid choice" — while slice 1's own
        # remedy sentence tells the user to run exactly that command. One dispatch, all three.
        with tempfile.TemporaryDirectory() as d:
            _repo(d).close()
            for channel in ("obsidian", "native-memory"):
                rc, _out, err = _run(["migrate", channel, "--path", d])
                self.assertEqual(rc, 1, channel)
                self.assertNotIn("invalid choice", err, channel)
                self.assertIn("was REMOVED in mokata", err, channel)
                # ...and THESE keep the downgrade remedy, because their data really is unreachable
                self.assertIn("pip install", err, channel)

    def test_stage11_help_advertises_exactly_what_it_answers_for(self):
        # ⚠ INVERTED AT SLICE 4, AND IT IS THE SAME RULE. While the command MIGRATED, `choices`
        # accepted a removed channel so it could be ANSWERED and `metavar` withheld it so the help
        # did not SELL it. The command no longer migrates anything, so what it accepts and what it
        # advertises are the same set, and this channel is now IN it.
        rc, out, _err = _run(["migrate", "--help"])
        self.assertEqual(rc, 0)
        self.assertIn(CHANNEL, out)
        self.assertNotIn("{}", out)


# ================================================================== C. what SURVIVES

class TestBackupSurfaceSurvives(unittest.TestCase):
    """⭐ Slice 1's rule: a deletion is graded by what SURVIVES. A test that only asserts absence
    passes on an empty repo."""

    def test_stage11_export_import_round_trip_on_a_real_file(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            src = _repo(a)
            src.remember(MemoryItem.create("indent", "four spaces"), assume_yes=True)
            src.remember(MemoryItem.create("db", "postgres per ADR-7"), assume_yes=True)
            src.close()

            rc, out, _err = _run(["memory", "export", "--path", a])
            self.assertEqual(rc, 0)
            self.assertIn("backed up 2 memory item(s)", out)
            backups = glob.glob(os.path.join(a, MOKATA_DIR, "backups", "memory-*.json"))
            self.assertEqual(len(backups), 1)

            dst = _repo(b)
            dst.close()
            rc, out, _err = _run(["memory", "import", backups[0], "--path", b, "--yes"])
            self.assertEqual(rc, 0)

            dst = MemoryStore.from_surface(Surface.load(b))
            try:
                restored = {i.subject: i.value for i in dst.backend.all()}
            finally:
                dst.close()
            self.assertEqual(restored,
                             {"indent": "four spaces", "db": "postgres per ADR-7"})

    def test_stage11_the_library_surface_is_intact(self):
        # The module-level API 35b promises, imported through the package re-export the CLI and
        # the MCP tools both use. Absent any one of these, `memory export`/`import` is dead.
        for fn in (export_memory, import_memory, plan_memory_import, load_memory_share,
                   default_backup_path):
            self.assertTrue(callable(fn))
        self.assertEqual(SHARE_KIND, "mokata-memory-share")

    def test_stage11_share_kind_is_the_users_data_and_did_not_move(self):
        # ⚠ THE BOUNDARY, PINNED. `SHARE_KIND` reads like the channel's name and is not: it is the
        # `kind` field INSIDE every backup already on a user's disk, and `_validate` refuses a file
        # that does not carry it. Renaming it pre-1.0 would be free for our code and would make
        # every existing backup unreadable — which is the one thing §7d's exception forbids.
        with tempfile.TemporaryDirectory() as d:
            store = _repo(d)
            planted = _plant_share_file(d, [MemoryItem.create("x", "1")])
            data = load_memory_share(planted)
            self.assertEqual(data["kind"], "mokata-memory-share")
            self.assertEqual(plan_memory_import(store, data, source=planted).errors, [])
            store.close()

    def test_stage11_the_legacy_path_is_now_just_a_path(self):
        # P22, concretely: a user who exports to that filename gets a normal backup and is told
        # nothing, because there is no channel left for it to be. It must still round-trip.
        with tempfile.TemporaryDirectory() as d:
            store = _repo(d)
            store.remember(MemoryItem.create("indent", "four spaces"), assume_yes=True)
            store.close()
            dest = os.path.join(d, MOKATA_DIR, LEGACY_FILENAME)

            rc, out, err = _run(["memory", "export", dest, "--path", d])

            self.assertEqual(rc, 0)
            self.assertTrue(os.path.exists(dest))
            # ⚠ GRADED BY THE MARKER DIRECTORY, NOT BY A WORD — see the twin assertion in
            # `test_35b_backup_surface`. Keying this on "deprecated" let a mutant re-announce the
            # very same channel through `warn_removed`, whose sentence says "removed" and whose
            # marker is keyed `<channel>@removed-<release>`. Both notices mint under
            # `temp_local/deprecations/`, so an EMPTY directory is the vocabulary-free property.
            markers = os.path.join(d, MOKATA_DIR, "temp_local", "deprecations")
            self.assertEqual(os.listdir(markers) if os.path.isdir(markers) else [], [])
            for word in ("deprecated", "removed"):
                self.assertNotIn(word, (out + err).lower())
            self.assertEqual(load_memory_share(dest)["kind"], SHARE_KIND)

    def test_stage11_the_mcp_backup_tools_still_exist(self):
        from mokata.mcp import tools_memory
        for name in ("memory_export", "memory_import"):
            self.assertTrue(callable(getattr(tools_memory, name, None)),
                            f"the MCP {name} tool did not survive the slice")


# ================================================== D. nothing reaches the channel any more

class TestNoFallbackPath(unittest.TestCase):
    def test_stage11_no_src_module_treats_that_filename_as_a_channel(self):
        # ⚠ THE HIDING CHECK (A3): a slice that makes the import probe pass while leaving the
        # subsystem reachable has not deleted it. The filename may appear in exactly ONE module —
        # the removal RECORD — and nowhere that resolves, opens or writes.
        sources = _src_files()
        self.assertIn("mokata/deprecation.py", sources)          # the corpus really is the tree
        offenders = sorted(path for path, text in sources.items()
                           if path != "mokata/deprecation.py"
                           and LEGACY_FILENAME in "".join(
                               v for _l, v in _string_constants(text)))
        self.assertEqual(offenders, [])

    def test_stage11_the_removal_record_is_not_a_legacy_reader(self):
        # Slice 1 kept `obsidian_vault_path` to NAME a location and wrote "this is not a legacy
        # reader and must never become one". This is that promise made gradable: the module that
        # holds the removed filename must contain no filesystem READ at all.
        #
        # ⚠ THE MODULE DOES OPEN ONE THING, AND IT IS NOT A READ. `warn_deprecated` /
        # `warn_removed` mint their once-per-repo markers with an atomic
        # `os.open(..., O_CREAT|O_EXCL|O_WRONLY)`. Convicting that would make this guard cry wolf
        # about the notice machinery itself; letting `os.open` through unexamined would let a
        # legacy reader in under the same spelling. So the WRITE is required to declare itself.
        tree = ast.parse(inspect.getsource(deprecation))
        readers, opens = set(), []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name) and node.func.id == "open":
                readers.add("open (builtin)")                    # never write-only by convention
            if isinstance(node.func, ast.Attribute):
                if node.func.attr in ("listdir", "scandir", "glob", "read_text", "read_bytes",
                                      "load", "loads"):
                    readers.add(node.func.attr)
                elif node.func.attr == "open":
                    opens.append(ast.dump(node))
        self.assertEqual(readers, set(), f"deprecation.py grew a reader: {sorted(readers)}")
        self.assertTrue(opens, "the marker writes vanished — this guard now grades nothing")
        for dumped in opens:
            self.assertIn("O_WRONLY", dumped, "an os.open in the removal record is not write-only")
        # and the path namer composes a string for a root that does not exist, creating nothing
        path = deprecation.removed_file_path(CHANNEL, "/no/such/root")
        self.assertTrue(path.endswith(LEGACY_FILENAME))
        self.assertFalse(os.path.exists("/no/such/root"))

    def test_stage11_a_file_channel_is_excluded_from_a_backend_chain_by_type(self):
        # §7i — the tree holds no manifest naming it, so the offender is SUPPLIED. Filtered by
        # membership, a chain that named `memory-share` would make `select_memory_backend`
        # announce a removed BACKEND (downgrade-to-migrate) about a file this release reads.
        chain = ["memory-share", "obsidian", "sqlite"]
        self.assertEqual(
            deprecation.removed_channels_in(chain, deprecation.RemovedNotice),
            ("obsidian",))
        self.assertEqual(
            deprecation.removed_channels_in(["memory-share"], deprecation.RemovedNotice), ())

    def test_stage11_the_channel_cannot_be_migrated_through_the_library_either(self):
        # The CLI answers it; the library offers no migration to reach. ⚠ RE-POINTED AT SLICE 4:
        # this used to assert `migrate_channels` REFUSED the channel, and that module is gone, so
        # the strongest true form is that no importable migration entry point exists for it at all.
        import importlib.util
        self.assertIsNone(importlib.util.find_spec("mokata.migrate_channels"))
        from mokata.memory.migrate import SUPPORTED
        self.assertNotIn(CHANNEL, SUPPORTED)


# ============================================== E. the gate is STILL CLOSED — three slices left

class TestGateStillClosed(unittest.TestCase):
    def test_stage11_this_channel_would_still_close_the_gate_if_it_came_back(self):
        # ★★ RE-POINTED AT 0.0.18 STAGE 14, AND THE RE-POINT IS THE WHOLE LESSON OF THE LANE.
        # This asserted the WHOLE implemented set — a LANE-level claim living in a SLICE's file —
        # so every one of the four slices had to be edited whenever any other one landed, and the
        # cheap fix each time was to bump a number. Stage 14 empties the set, and four files break
        # at once for a reason none of them is about.
        #
        # The claim that actually belongs here is about THIS slice's own contribution to the gate,
        # and it is graded BOTH ways against a planted offender (§7i): the channel is gone, AND if
        # it came back the gate would close again. That survives every later stage, and it is
        # STRONGER than the old form — the old one could only ever say "the number is still N".
        # The lane's finish line is asserted exactly once, in `test_stage14_neo4j_removal`.
        present = DR.present_channels(DR.IMPLEMENTATIONS, DR.live_probe)
        self.assertNotIn("memory-share", present)
        self.assertEqual(
            DR.removal_state(deprecation.REMOVAL_RELEASE, deprecation.REMOVAL_RELEASE,
                             frozenset({"memory-share"})),
            DR.REMOVAL_OVERDUE)
        self.assertEqual(
            DR.removal_regressions(("memory-share",), DR.IMPLEMENTATIONS, lambda _t: True),
            ("memory-share",))
        self.assertEqual(
            DR.removal_regressions(("memory-share",), DR.IMPLEMENTATIONS, DR.live_probe), ())

    def test_stage11_the_registries_stay_exact_across_the_move(self):
        # A channel must not be able to belong to neither set — the move is what could drop one.
        announced = set(deprecation.CHANNELS) | set(deprecation.REMOVED)
        self.assertEqual(DR.registry_drift(announced, DR.IMPLEMENTATIONS), ((), ()))
        self.assertEqual(DR.stale_notices(deprecation.CHANNELS, DR.present_channels(
            DR.IMPLEMENTATIONS, DR.live_probe)), ())
        self.assertEqual(
            DR.removal_regressions(deprecation.REMOVED, DR.IMPLEMENTATIONS, DR.live_probe), ())

    def test_stage11_no_channel_is_left_announced_but_not_removed(self):
        # ⚠ REVERSED AT STAGE 14. This said "a channel still implemented must still WARN, or the
        # lane has gone quiet about something it has not removed" — true while a survivor existed,
        # and there is none. The invariant underneath it is the one that survives: nothing may be
        # announced as going without being gone, and `stale_notices` is what says so mechanically.
        self.assertEqual(deprecation.CHANNELS, {})
        present = DR.present_channels(DR.IMPLEMENTATIONS, DR.live_probe)
        self.assertEqual(DR.stale_notices(deprecation.CHANNELS, present), ())
        # graded against a planted offender, because the live domain is now empty (§7i)
        self.assertEqual(DR.stale_notices(("planted",), present), ("planted",))


# ================================================ F. the two record classes stay apart

class TestTheTwoRecordClasses(unittest.TestCase):
    def test_stage11_a_backend_record_is_refused_for_the_file_channel(self):
        # `RemovedNotice.render()` says "mokata will NOT read it" and names a downgrade. Rendered
        # over this channel every clause is false, and it would LOOK like a correct refusal.
        with self.assertRaises(KeyError):
            deprecation.removed_notice(CHANNEL)
        self.assertTrue(deprecation.is_removed_file_channel(CHANNEL))

    def test_stage11_a_file_record_is_refused_for_a_removed_backend(self):
        for channel in ("obsidian", "native-memory"):
            self.assertFalse(deprecation.is_removed_file_channel(channel))
            with self.assertRaises(KeyError):
                deprecation.removed_file_report(channel, "/r/x.json", True)
            # and their own record still renders, with the downgrade remedy intact
            self.assertIn("pip install", deprecation.removed_notice(channel).render())

    def test_stage11_the_file_record_names_this_release_and_no_other(self):
        # Derived, like every other release string in that module: a hand-typed one would go on
        # naming 0.0.18 after the declaration moved.
        rendered = deprecation.removed_file_report(CHANNEL, "/r/x.json", True)
        self.assertIn(deprecation.REMOVAL_RELEASE, rendered)
        self.assertNotIn(deprecation.LAST_SHIPPING_RELEASE, rendered)

    def test_stage11_the_record_carries_no_item_content(self):
        # P23/CM.S1 — a notice must not be able to leak a value. Every field is STATIC text, so
        # the ONLY thing a render can vary by is the caller-supplied location: strip the detail
        # back out and two renders of different repos are byte-identical. That is the property;
        # "no field is a store" is what it means.
        notice = deprecation.REMOVED[CHANNEL]
        self.assertTrue(all(isinstance(f, str) for f in
                            (notice.channel, notice.what, notice.data, notice.remedy)))
        bare = notice.render()
        for detail in ("/repo-a/.mokata/memory-share.json", "/repo-b/elsewhere.json"):
            rendered = notice.render(detail=detail)
            self.assertIn(detail, rendered)
            self.assertEqual(rendered.replace(f" {detail}", ""), bare)


# ======================================= G. BACKCOMPAT-SWEEP (E2 — one pass reads one file)

class TestBackcompatSweep(unittest.TestCase):
    def test_stage11_the_stranded_file_parameter_is_deleted_not_defaulted(self):
        # `--file` / `file=` existed for the memory-share source read alone. Left in place they
        # would be an option a user can pass and nothing can act on — a second code path with no
        # second behaviour, which is what the pre-1.0 rule exists to keep out.
        # ⚠ The signature half went with `migrate_channels` at slice 4; the USER-FACING half is
        # what mattered and it still grades, on the rendered help.
        rc, out, _err = _run(["migrate", "--help"])
        self.assertEqual(rc, 0)
        self.assertNotIn("--file", out)

    def test_stage11_no_shim_kept_the_channel_importable(self):
        # "Delete, do not deprecate": no alias, no re-export, no compatibility name. Graded by
        # ATTRIBUTE LOOKUP on the imported module — the operation `from mokata.memory import
        # is_legacy_share_dest` performs — so a module-level `__getattr__` shim resurrecting the
        # name would be caught, which a source grep would not be.
        import importlib
        for symbol in ("MEMORY_SHARE_FILENAME", "is_legacy_share_dest"):
            for name in ("mokata.memory", "mokata.memory.share"):
                module = importlib.import_module(name)
                with self.assertRaises(AttributeError, msg=f"{name}.{symbol} still resolves"):
                    getattr(module, symbol)


def _string_constants(text):
    """Every non-docstring string constant in a module, as `[(line, value)]`.

    Reused shape from `_deprecation_removal._string_constants`, and the reason is the same:
    comments never reach the AST and a docstring describing a removal is PROSE ABOUT A HISTORICAL
    VALUE, not a value. A module explaining what it deleted must not convict itself."""
    try:
        tree = ast.parse(text)
    except SyntaxError:                                    # pragma: no cover — not in this corpus
        return []
    docstrings = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            docstrings.add(id(body[0].value))
    return [(node.lineno, node.value) for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node) not in docstrings]


if __name__ == "__main__":                                 # pragma: no cover
    unittest.main()
