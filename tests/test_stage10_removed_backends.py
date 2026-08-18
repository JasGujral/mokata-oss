"""0.0.18 stage 10 (lane D, slice 1) — obsidian / native-memory / detection are REMOVED.

WHAT THIS SLICE ACTUALLY HAD TO GET RIGHT, stated once so the tests below are not a checklist.

Deleting `ObsidianBackend` and `NativeMemoryBackend` is four lines of `git rm`-shaped work. The
job is the OTHER half, and it is the half that touches a user's disk:

  * `profiles.PROFILES["full"|"custom"]` wrote `["native-memory", "obsidian", "sqlite"]` into the
    `memory_store` chain of every manifest they generated, and `init_repo` copied
    `TOOL_CATALOG`'s entries in beside it. Those manifests are on people's machines RIGHT NOW.
  * Take the backends away and that chain STILL RESOLVES. The removed detect strategy reads as
    absent, the router degrades past it, and the read is served by the SQLite floor — which for a
    user whose memory lived in an Obsidian vault is an EMPTY STORE, returned with no error, no
    notice and exit 0. Their memory reads as erased by an upgrade.

That is doc 85 §7g at its most expensive — "no items" and "the backend that held your items is
gone" arriving as the same answer — and doc 85 §7d's ONE EXCEPTION (a user's `.mokata/` store is
DATA, so refuse loudly and name the remedy; never destroy what a human owns, P2) is the rule that
forbids it. So the removal ships with two refusals, split because they are two facts:

    data behind the removed channel   → RemovedChannelError, naming the vault and the remedy
    a stale chain entry and no data   → a once-per-repo notice, and resolution carries on

⚠ EVERY OFFENDER HERE IS PLANTED (§7i). After this slice nothing in the tree writes a manifest
naming a removed channel — `TOOL_CATALOG` has no entry and no profile wires one — so a test that
built its fixture from the live catalog would pass having exercised nothing at all. The fixtures
come from `_removed_channel_fixture`, which writes the manifest shape v0.0.17 actually wrote and
the vault bytes `ObsidianBackend.put` actually wrote. Nothing here imports a removed class to
build its own offender; that would be a legacy reader in a test's clothes.

⚠ AND THE GATE STAYS CLOSED. This was slice 1 of 4, and it is still closed two slices in.
`test_stage9_removal_release`'s tripwire reds at a 0.0.18 cut while ANY channel is implemented,
and this file asserts the survivors ON PURPOSE: a slice that made the whole probe green would have
been a slice that deleted more than it was asked to, or a probe that stopped looking.

⚠ THE SURVIVOR LIST IS MAINTAINED BY EACH SLICE AS IT LANDS, and slice 2 shortened it — this
file's assertions are what would have gone quietly wrong otherwise, which is why they are written
as an exact set rather than a membership test. `memory-share` left at stage 11 (lane D slice 2);
`vault` is slice 4's and `neo4j` is stage 13's.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr

import _support  # noqa: F401
from _removed_channel_fixture import (LEGACY_TOOL_BLOCKS, plant_obsidian_vault,
                                      plant_removed_chain)
import _deprecation_removal as R

from mokata import MOKATA_DIR, TEMP_LOCAL_DIRNAME, deprecation as D, profiles, schema
from mokata.config import Surface
from mokata.detect import Detector
from mokata.init import init_repo
from mokata.memory import MemoryStore
from mokata.memory.selection import (build_backend, obsidian_vault_path, removed_channel_data,
                                     select_memory_backend)

REMOVED_THIS_SLICE = ("obsidian", "native-memory")


def _silent(_s):
    pass


def _repo(root):
    init_repo(root=root, profile="standard", assume_yes=True, out=_silent)
    return Surface.load(root)


def _default_vault(root):
    return os.path.join(root, MOKATA_DIR, TEMP_LOCAL_DIRNAME, "memory", "vault")


def _read(directory, name):
    with open(os.path.join(directory, name), encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------- the removal itself, both ways
class TestTheChannelsAreGone(unittest.TestCase):
    def test_the_import_probe_no_longer_finds_either_backend(self):
        # The stage-9 probe, on this stage's subject. `ObsidianBackend` and `NativeMemoryBackend`
        # lived in a module that SURVIVES, so a module-only probe would report them present
        # forever — the `module:Symbol` form is what makes this an assertion and not a wish.
        for channel in REMOVED_THIS_SLICE:
            self.assertFalse(R.live_probe(R.IMPLEMENTATIONS[channel]),
                             f"{channel} is still importable — it was hidden, not removed")

    def test_the_module_that_held_them_survives_and_so_does_the_floor(self):
        # Deletion is graded by what SURVIVES. A test that only asserts absence passes on an
        # empty repo.
        self.assertTrue(R.live_probe("mokata.memory.backends"))
        self.assertTrue(R.live_probe("mokata.memory.backends:SQLiteBackend"))
        self.assertTrue(R.live_probe("mokata.memory.backends:PostgresBackend"))
        self.assertTrue(R.live_probe("mokata.memory.backends:MemoryBackend"))

    def test_no_removed_channel_is_still_reachable_by_any_declared_target(self):
        self.assertEqual(R.removal_regressions(D.REMOVED, R.IMPLEMENTATIONS, R.live_probe), ())

    def test_removal_regressions_finds_a_channel_that_came_back(self):
        # The §7i offender: this tree holds none, so one is supplied.
        self.assertEqual(
            R.removal_regressions(("obsidian", "native-memory"),
                                  {"obsidian": "mokata.vault", "native-memory": "mokata.gone"},
                                  R.live_probe),
            ("obsidian",))

    def test_the_registries_moved_them_from_deprecated_to_removed(self):
        for channel in REMOVED_THIS_SLICE:
            self.assertNotIn(channel, D.CHANNELS, f"{channel} still carries a DEPRECATION notice "
                                                  f"promising a future removal that happened")
            self.assertIn(channel, D.REMOVED)

    def test_no_channel_this_slice_left_behind_is_still_pending(self):
        # ⚠ THE "NOT MINE YET" SET IS EMPTY AS OF STAGE 14, so this asserts the positive fact that
        # replaced it: the lane finished, and every channel it ever announced is now in the REMOVED
        # registry rather than the deprecated one. Leaving the old loop over `("neo4j",)` would
        # have been a loop over a channel that no longer belongs to a later slice.
        self.assertEqual(D.CHANNELS, {})
        for channel in ("obsidian", "native-memory", "memory-share", "vault", "neo4j"):
            self.assertIn(channel, D.REMOVED)

    def test_this_slices_channels_would_still_close_the_gate_if_they_came_back(self):
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
        mine = ("obsidian", "native-memory")
        present = R.present_channels(R.IMPLEMENTATIONS, R.live_probe)
        self.assertEqual(present & frozenset(mine), frozenset())
        for channel in mine:
            self.assertEqual(
                R.removal_state(D.REMOVAL_RELEASE, D.REMOVAL_RELEASE, frozenset({channel})),
                R.REMOVAL_OVERDUE, channel)
            self.assertEqual(
                R.removal_regressions((channel,), R.IMPLEMENTATIONS, lambda _t: True), (channel,))
        self.assertEqual(
            R.removal_regressions(mine, R.IMPLEMENTATIONS, R.live_probe), ())


# ------------------------------------------------------- the one exception: a user's own data
class TestAnExistingStoreRefusesLoudly(unittest.TestCase):
    def _planted_repo(self, tmp, chain, notes=("db.engine", "cache.ttl")):
        _repo(tmp)
        plant_removed_chain(tmp, chain)
        written = plant_obsidian_vault(_default_vault(tmp), notes) if notes else []
        return Surface.load(tmp), written

    def test_a_repo_with_obsidian_NOTES_refuses_and_names_the_vault_and_the_remedy(self):
        with tempfile.TemporaryDirectory() as d:
            surface, _written = self._planted_repo(d, ["obsidian", "sqlite"])
            with self.assertRaises(D.RemovedChannelError) as caught:
                MemoryStore.from_surface(surface)
            message = str(caught.exception)
            # WHAT happened, WHEN, WHERE the data is, and the ONE way to bring it across.
            self.assertIn("REMOVED", message)
            self.assertIn(D.REMOVAL_RELEASE, message)
            self.assertIn("Obsidian memory backend", message)
            self.assertIn(_default_vault(d), message)
            self.assertIn("2 note(s)", message)
            self.assertIn(f"pip install 'mokata=={D.LAST_SHIPPING_RELEASE}'", message)
            self.assertIn("mokata migrate obsidian", message)
            self.assertIn("mokata config set memory_store sqlite", message)

    def test_the_remedy_names_the_release_that_STILL_HAD_IT_not_this_one(self):
        # ⚠ WRITTEN AFTER A MUTANT SURVIVED (§7f). `test_a_repo_with_obsidian_NOTES_refuses…`
        # asserts the message contains `pip install 'mokata=={LAST_SHIPPING_RELEASE}'` — and
        # that assertion READS the constant it is supposed to be checking, so
        # `LAST_SHIPPING_RELEASE = REMOVAL_RELEASE` satisfied it perfectly while telling every
        # user to install the release that REMOVED the thing they are trying to migrate. A
        # derivation cannot be graded by an assertion that consumes it; this grades the
        # ARITHMETIC instead.
        self.assertNotEqual(D.LAST_SHIPPING_RELEASE, D.REMOVAL_RELEASE)
        before = [int(part) for part in D.LAST_SHIPPING_RELEASE.split(".")]
        promised = [int(part) for part in D.REMOVAL_RELEASE.split(".")]
        self.assertEqual(before[:-1], promised[:-1])          # same series
        self.assertEqual(before[-1] + 1, promised[-1])        # exactly one patch behind
        # ⚠ THE EXPECTATIONS ARE COMPOSED, NOT TYPED, and the reason is that stage 9's class
        # guard CAUGHT THE FIRST DRAFT OF THIS TEST: it hand-typed two release literals as
        # assertion arguments, which is precisely the shape `notice_pins` forbids in a
        # deprecation-aware test. The guard was right — a synthetic fixture and a pin are
        # indistinguishable from the outside, which is why the rule has no "but mine is fine"
        # branch. Composing them grades the arithmetic harder anyway.
        for series, patch in ((("1", "2"), 9), (("0", "0"), 18), (("3", "4", "5"), 1)):
            promised = ".".join(series + (str(patch),))
            expected = ".".join(series + (str(patch - 1),))
            self.assertEqual(D.last_release_with(promised), expected)

    def test_a_removal_at_a_ZERO_patch_refuses_rather_than_inventing_a_predecessor(self):
        # The declared precondition (patch-only versioning) with its offender. Without this the
        # guard is unreachable in a tree whose declaration happens to be at a non-zero patch, and
        # a `0.1.0` removal would silently produce `0.1.-1` in a message sent to a user.
        for undecrementable in ("0.1.0", "1.0.0", "2.0"):
            with self.assertRaises(ValueError):
                D.last_release_with(undecrementable)

    def test_the_refusal_DESTROYS_NOTHING(self):
        # P2. The refusal is only correct if the data it refuses to read is still there
        # afterwards, byte for byte.
        with tempfile.TemporaryDirectory() as d:
            surface, written = self._planted_repo(d, ["obsidian", "sqlite"])
            vault = _default_vault(d)
            before = {name: _read(vault, name) for name in written}
            with self.assertRaises(D.RemovedChannelError):
                MemoryStore.from_surface(surface)
            after = {name: _read(vault, name) for name in sorted(os.listdir(vault))}
            self.assertEqual(after, before)

    def test_the_refusal_fires_EVERY_time_not_once_per_repo(self):
        # A once-per-repo marker is right for a notice and WRONG for a refusal: the second
        # command would silently serve the empty floor, which is the whole defect.
        with tempfile.TemporaryDirectory() as d:
            surface, _w = self._planted_repo(d, ["obsidian", "sqlite"])
            for _attempt in range(3):
                with self.assertRaises(D.RemovedChannelError):
                    MemoryStore.from_surface(Surface.load(d))

    def test_a_repo_with_the_chain_but_NO_notes_is_told_ONCE_and_keeps_working(self):
        with tempfile.TemporaryDirectory() as d:
            surface, _w = self._planted_repo(d, ["obsidian", "sqlite"], notes=())
            buf = io.StringIO()
            with redirect_stderr(buf):
                store = MemoryStore.from_surface(surface)
                store.close()
            self.assertIn("REMOVED", buf.getvalue())
            self.assertIn("obsidian", buf.getvalue())
            second = io.StringIO()
            with redirect_stderr(second):
                MemoryStore.from_surface(Surface.load(d)).close()
            self.assertEqual(second.getvalue(), "")

    def test_the_removal_notice_is_NOT_suppressed_by_the_deprecation_marker(self):
        # ★ THE ONE THAT WOULD HAVE BEEN SILENT. A repo that USED the channel already fired
        # `obsidian.marker` for the deprecation warn under 0.0.17 — so keying the removal notice
        # on the channel alone would suppress it for exactly the repos that have data. Two facts,
        # two markers.
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            mokata_dir = os.path.join(d, MOKATA_DIR)
            markers = os.path.join(mokata_dir, TEMP_LOCAL_DIRNAME, "deprecations")
            os.makedirs(markers, exist_ok=True)
            with open(os.path.join(markers, "obsidian.marker"), "w",
                      encoding="utf-8"):        # the 0.0.17 deprecation marker, planted
                pass
            fired = D.warn_removed("obsidian", mokata_dir, out=_silent)
            self.assertTrue(fired, "the removal notice was swallowed by the deprecation marker")
            self.assertFalse(D.warn_removed("obsidian", mokata_dir, out=_silent))
            self.assertIn("obsidian.marker", os.listdir(markers))          # old one NOT deleted

    def test_warn_removed_is_degrade_clean_on_an_unwritable_marker_dir(self):
        # P8, and the same offender `warn_deprecated`'s own degrade test uses.
        self.assertFalse(D.warn_removed("obsidian", "/nonexistent/\x00bad", out=_silent))

    def test_native_memory_has_no_on_disk_store_so_it_can_only_be_the_notice(self):
        # §7g on the EVIDENCE: `removed_channel_data` may never invent a location for a store
        # mokata never held. Saying "your data is at …" about an external client would be a
        # fabricated fact on a user surface.
        self.assertEqual(removed_channel_data("native-memory", {"vault": "/tmp"}, "/tmp"), "")
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            plant_removed_chain(d, ["native-memory", "sqlite"])
            buf = io.StringIO()
            with redirect_stderr(buf):
                MemoryStore.from_surface(Surface.load(d)).close()
            self.assertIn("native-memory", buf.getvalue())
            self.assertIn("has deleted nothing", buf.getvalue())

    def test_an_EXPLICIT_ask_for_a_removed_backend_never_falls_to_the_floor(self):
        # The backstop for every caller that does not go through `select_memory_backend` — the
        # migrate path, a hand-written `build_backend`. Silently returning the SQLite floor here
        # is the §7g collapse in its purest form.
        with tempfile.TemporaryDirectory() as d:
            for channel in REMOVED_THIS_SLICE:
                with self.assertRaises(D.RemovedChannelError):
                    build_backend(channel, d)

    def test_a_configured_EXTERNAL_vault_is_named_rather_than_the_default(self):
        # `tools.obsidian.config.vault` points at the user's real Obsidian vault, which may be
        # anywhere. Naming the default location instead would send them to an empty directory.
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as elsewhere:
            _repo(d)
            block = dict(LEGACY_TOOL_BLOCKS["obsidian"], config={"vault": elsewhere})
            plant_removed_chain(d, ["obsidian", "sqlite"], tool_blocks={"obsidian": block})
            plant_obsidian_vault(elsewhere, ("only.here",))
            with self.assertRaises(D.RemovedChannelError) as caught:
                MemoryStore.from_surface(Surface.load(d))
            self.assertIn(elsewhere, str(caught.exception))
            self.assertNotIn(_default_vault(d), str(caught.exception))

    def test_the_evidence_probe_is_pure_over_a_supplied_config_and_root(self):
        # `root` is the `.mokata` DIR (what `select_memory_backend` is handed), never the repo
        # root — the two differ by one path element and the wrong one silently finds no vault.
        with tempfile.TemporaryDirectory() as d:
            mokata_dir = os.path.join(d, MOKATA_DIR)
            os.makedirs(mokata_dir)
            self.assertEqual(removed_channel_data("obsidian", {}, mokata_dir), "")  # no vault
            vault = _default_vault(d)
            os.makedirs(vault)
            self.assertEqual(removed_channel_data("obsidian", {}, mokata_dir), "")  # empty
            with open(os.path.join(vault, "notes.txt"), "w", encoding="utf-8") as fh:
                fh.write("not a note")
            self.assertEqual(removed_channel_data("obsidian", {}, mokata_dir), "")  # not a note
            plant_obsidian_vault(vault, ("a",))
            self.assertIn("1 note(s)", removed_channel_data("obsidian", {}, mokata_dir))
            self.assertEqual(obsidian_vault_path({"vault": "~/x"}, mokata_dir),
                             os.path.expanduser("~/x"))


# ------------------------------------------------------------------ the DISK surface, and detect
class TestTheSurfacesTheDeletionHadToReach(unittest.TestCase):
    def test_a_manifest_written_TODAY_advertises_neither_removed_backend(self):
        # ★ Stage 9 found `TOOL_CATALOG` reaching `.mokata/manifest.json`. This is the same
        # surface one release later, as a whole tool rather than a field.
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            with open(os.path.join(d, MOKATA_DIR, "manifest.json"), encoding="utf-8") as fh:
                written = json.load(fh)
            text = json.dumps(written)
            for channel in REMOVED_THIS_SLICE:
                self.assertNotIn(channel, written.get("tools", {}))
                self.assertNotIn(channel, written["capabilities"]["memory_store"]["fallback"])
                self.assertNotIn(channel, text)
            self.assertIn("sqlite", written["capabilities"]["memory_store"]["fallback"])

    def test_every_profile_writes_a_manifest_free_of_them(self):
        for profile in profiles.profile_names():
            data = profiles.build_manifest_data(profile, "0.0.18")
            for channel in REMOVED_THIS_SLICE:
                self.assertNotIn(channel, data.get("tools", {}), f"{profile} still writes it")
                self.assertNotIn(channel, json.dumps(data), f"{profile} still names it")

    def test_the_detect_strategy_is_gone_from_the_LIVE_set_and_reads_as_absent(self):
        self.assertNotIn("obsidian", schema.KNOWN_DETECT_TYPES)
        detector = Detector(cache=False)
        self.assertFalse(detector.is_present("obsidian", LEGACY_TOOL_BLOCKS["obsidian"]))
        # …and detection is still TOTAL: an unknown strategy is a value, never a raise.
        self.assertFalse(detector.is_present("x", {"detect": {"type": "invented"}}))

    def test_an_EXISTING_manifest_that_names_it_still_PARSES(self):
        # ⚠ THE REMEDY HAS TO BE RUNNABLE. `Surface.load` is the first thing every command does,
        # including the `mokata config set memory_store sqlite` the notice tells the user to run.
        # Rejecting the old detect type at the parser would brick the repo AND the fix, with a
        # message ("'obsidian' is not one of […]") that says nothing about their notes.
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            plant_removed_chain(d, ["obsidian", "sqlite"])
            surface = Surface.load(d)                       # must not raise
            self.assertEqual(surface.manifest.fallback_order("memory_store"),
                             ["obsidian", "sqlite"])

    def test_the_parseable_set_is_exactly_the_live_set_plus_the_removed_one(self):
        # §7j exactness: a strategy that is neither live nor declared-removed cannot hide here.
        self.assertEqual(set(schema.PARSEABLE_DETECT_TYPES),
                         set(schema.KNOWN_DETECT_TYPES) | set(schema.REMOVED_DETECT_TYPES))
        planted = {block["detect"]["type"] for block in LEGACY_TOOL_BLOCKS.values()}
        self.assertEqual(set(schema.REMOVED_DETECT_TYPES),
                         planted - set(schema.KNOWN_DETECT_TYPES),
                         "REMOVED_DETECT_TYPES has drifted from the manifests users actually hold")

    def test_migrate_no_longer_offers_a_channel_it_cannot_read(self):
        # ⚠ `migrate_channels.CHANNELS` was the other half of this until slice 4 DELETED that
        # module — there is no live migration channel left anywhere, which is a stronger form of
        # the same assertion and is pinned at the surviving surface in `test_stage13_vault_slice`.
        # What still belongs here is the memory-migrate destination list, which is a SURVIVOR.
        from mokata.memory.migrate import SUPPORTED
        for channel in REMOVED_THIS_SLICE:
            self.assertNotIn(channel, SUPPORTED)
        self.assertEqual(SUPPORTED, ("sqlite", "postgres", "pgvector"))

    def test_onboarding_no_longer_recommends_installing_them(self):
        from mokata.onboarding import INSTALL_HINTS, OPTIONAL_INTEGRATIONS
        for channel in REMOVED_THIS_SLICE:
            self.assertNotIn(channel, OPTIONAL_INTEGRATIONS)
            self.assertNotIn(channel, INSTALL_HINTS)
        self.assertIn("postgres", OPTIONAL_INTEGRATIONS)                  # survivor, pinned


# ------------------------------------------------------------------------ what must still work
class TestWhatSurvives(unittest.TestCase):
    def test_a_plain_repo_reads_and_writes_exactly_as_before_with_no_new_output(self):
        from mokata.memory import MemoryItem
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            buf = io.StringIO()
            with redirect_stderr(buf):
                store = MemoryStore.from_surface(surface)
                store.remember(MemoryItem.create("db.engine", "postgres", source="t", author="t"),
                               assume_yes=True)
                store.close()
                reread = MemoryStore.from_surface(Surface.load(d))
                subjects = [i.subject for i in reread.all_active()]
                reread.close()
            self.assertIn("db.engine", subjects)
            self.assertEqual(buf.getvalue(), "",
                             "a canonical repo must see ZERO new output from the removal")

    def test_the_sqlite_floor_is_still_what_an_unknown_tool_resolves_to(self):
        with tempfile.TemporaryDirectory() as d:
            backend = build_backend("ripgrep", d)             # unknown memory tool → the floor
            self.assertEqual(backend.name, "sqlite")
            backend.close()

    def test_select_memory_backend_still_answers_for_a_canonical_chain(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            backend = select_memory_backend(surface.router, surface.mokata_dir)
            self.assertEqual(backend.name, "sqlite")
            backend.close()

    def test_refusal_is_degrade_clean_on_a_duck_typed_router(self):
        # The contract the deprecation warn had and the refusal inherits: a router with no
        # `.manifest.fallback_order` refuses NOTHING rather than raising.
        class _NoManifest:
            def resolve(self, _need):
                return None
        with tempfile.TemporaryDirectory() as d:
            backend = select_memory_backend(_NoManifest(), d)
            self.assertEqual(backend.name, "sqlite")
            backend.close()


# ------------------------------------- the class guard's other axis: hand-typed releases in src/
class TestNoSrcModuleHandTypesAReleaseAgain(unittest.TestCase):
    def _src_corpus(self):
        root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
        corpus = {}
        # CORPUS: THE WORKING TREE. The question is "does any module mokata SHIPS hand-type a
        # release?", and what ships is what `sync-public.sh` rsyncs — the files on disk under
        # `src/`, not the git index. An untracked module sitting in `src/` would be copied into
        # the mirror and into a wheel, so it must be swept; a tracked-but-deleted one would not.
        # (`test_dk_s5_docsync`'s shipped-asset walks answer the same predicate the same way.)
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [name for name in dirnames if name != "__pycache__"]
            for name in filenames:
                if name.endswith(".py"):
                    path = os.path.join(dirpath, name)
                    with open(path, encoding="utf-8") as fh:
                        # ⚠ `posix_rel`, not `os.path.relpath`. These keys are NAMES, not
                        # filesystem paths: `SRC_RELEASE_EXEMPT` and the assertions below spell
                        # them `mokata/parity.py`, so an OS-separated key stops matching on
                        # Windows and only on Windows. Three of the twenty failures that halted
                        # the 0.0.18 cut were this line (tests/test_windows_shell_and_paths.py).
                        corpus[_support.posix_rel(path, root)] = fh.read()
        return corpus

    def test_the_corpus_is_not_empty_and_holds_the_modules_that_talk_about_deprecation(self):
        # A sweep over an empty corpus passes having read nothing — the vacuity check the
        # assertion below cannot make about itself.
        corpus = self._src_corpus()
        self.assertGreater(len(corpus), 200)
        for path in ("mokata/deprecation.py", "mokata/cli_commands/migrate.py",
                     "mokata/vault.py", "mokata/parity.py"):
            self.assertIn(path, corpus)

    def test_no_deprecation_aware_src_module_hand_types_a_release(self):
        pins = R.src_release_pins(self._src_corpus())
        self.assertEqual(pins, (), "a src module hand-types a release: %r" % (pins,))

    def test_every_declared_exemption_still_exists(self):
        stale = R.stale_src_exemptions(self._src_corpus())
        self.assertEqual(stale, (), "SRC_RELEASE_EXEMPT has gone stale: %r" % (stale,))

    def test_the_sweep_finds_a_PLANTED_offender_and_leaves_prose_alone(self):
        # §7i — the tree holds no offender now, so one is supplied. The three cases are the three
        # the real sites were: a `--help` string (caught), a comment (not a value), a docstring
        # (prose about a historical value is not a value).
        planted = {
            "mokata/x.py": ('"""deprecated: removal was 0.0.17."""\n'
                            '# deprecated since 0.0.16\n'
                            'HELP = "scheduled for removal in 0.0.17"\n'),
            "mokata/clean.py": '"""deprecated."""\nHELP = "reads the constant"\n',
            "mokata/out_of_domain.py": 'HELP = "pin numpy==1.2.3"\n',
        }
        self.assertEqual(R.src_release_pins(planted, exempt={}),
                         (("mokata/x.py", 3, "0.0.17"),))

    def test_an_exemption_is_a_FRAGMENT_of_a_constant_not_a_whole_file(self):
        planted = {"mokata/x.py": ('"""deprecated."""\n'
                                   'A = "shipped in 0.0.17, a fact"\n'
                                   'B = "removal in 0.0.17, a promise"\n')}
        exempt = {"mokata/x.py": frozenset({"shipped in 0.0.17"})}
        self.assertEqual(R.src_release_pins(planted, exempt=exempt),
                         (("mokata/x.py", 3, "0.0.17"),))
        self.assertEqual(R.stale_src_exemptions(planted, exempt=exempt), ())
        gone = {"mokata/x.py": frozenset({"a wording that no longer exists"})}
        self.assertEqual(R.stale_src_exemptions(planted, exempt=gone),
                         (("mokata/x.py", "a wording that no longer exists"),))

    def test_the_declaration_module_is_graded_by_version_literals_not_by_this(self):
        # Two guards over one file would be §7f — each covering for the other, neither gradable.
        # The split is declared, so this asserts the split rather than the absence.
        planted = {"mokata/deprecation.py": '"""deprecated."""\nX = "0.0.17"\n'}
        self.assertEqual(R.src_release_pins(planted, exempt={}), ())
        self.assertEqual([text for _line, text in R.version_literals(planted[
            "mokata/deprecation.py"])], ["0.0.17"])


if __name__ == "__main__":
    unittest.main()
