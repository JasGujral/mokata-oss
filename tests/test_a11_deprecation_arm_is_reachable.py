"""THE DEPRECATION HALF OF THE PIPELINE HAS A CALLER AGAIN. (0.0.20 stage 11 — lane E.)

`WARN-DEPRECATED-HAS-NO-CALLERS` (doc 84), and the row's own question is the one this file answers:
*"what proves it still WORKS on the day it is next needed?"*

Half of that was already answered. `test_simp_s2_deprecation.TestWarnOncePerRepo` drives
`warn_deprecated` against a PLANTED channel, so the announcer — the once-per-repo `O_EXCL` marker,
the render, the ledger row — is exercised and correct.

⛔ **THE OTHER HALF WAS NOT, AND IT IS THE HALF THAT BITES.** `warn_deprecated` had **zero
production call sites**: `vault`'s went with the transport at 0.0.18 stage 13, `neo4j`'s with the
backend at stage 14. A working announcer that nothing announces through is not a deprecation cycle,
and the failure is silent by construction — **an empty registry and an unwired one produce byte-for-
byte the same output** (§7g). The day somebody added a channel to `CHANNELS`, the notice this
project's whole deprecate-then-remove contract rests on would simply never have fired, and every
test in the tree would have stayed green.

So the reach is now code — `deprecation.deprecated_channels_in`, the exact mirror of
`removed_channels_in` — called at the two capability surfaces that already announce REMOVED
channels, one line after them. And it is graded here, on a PLANTED channel wired into a real
manifest, by driving the production entry point rather than the announcer.

⭐ WHY THE TWO ARMS ARE SEPARATE FUNCTIONS AND NOT ONE LOOP: the removal arm RAISES and the
deprecation arm must not. A deprecated provider still works — that is the entire difference between
deprecating a thing and deleting it — and folding them would make the softer case inherit the
harder one's control flow, which is how a deprecation notice turns into an outage.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import ast
import contextlib
import io
import os
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import MOKATA_DIR
from mokata import deprecation as D

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CHANNEL = "planted-graph-channel"
#: ⚠ The rendered notice names `what`, not the channel id — so the assertions below look for THIS
#: string. Asserting on the id would have passed against an empty stderr the day `what` changed.
WHAT = "planted channel (test fixture)"
NOTICE = D.DeprecationNotice(
    channel=CHANNEL, what=WHAT,
    replacement="The canonical store already answers here.",
    migration="", removal=D.REMOVAL_RELEASE)


@contextlib.contextmanager
def planted():
    """⚠ The registry is MUTATED IN PLACE, never rebound — every reader holds the module-level
    name, so a rebind would leave the production code reading the original object and grade
    nothing. Same reasoning as `test_simp_s2_deprecation.planted_channels`."""
    D.CHANNELS[CHANNEL] = NOTICE
    try:
        yield CHANNEL
    finally:
        D.CHANNELS.pop(CHANNEL, None)


class _Manifest:
    """A duck-typed manifest naming the planted channel in one capability's chain."""

    def __init__(self, capability, chain):
        self._capability, self._chain = capability, chain

    def fallback_order(self, capability):
        return list(self._chain) if capability == self._capability else []

    def tool_config(self, _tool):
        return {}


class _Router:
    def __init__(self, manifest):
        self.manifest = manifest


class TheReachIsDerivedFromTheChain(unittest.TestCase):
    """§7i — a SUPPLIED chain. The tree ships no manifest naming a deprecated channel, so a version
    that went and read the repo would pass having graded nothing."""

    def test_a_chain_naming_a_deprecated_channel_reports_it(self):
        with planted():
            self.assertEqual((CHANNEL,),
                             D.deprecated_channels_in([CHANNEL, "sqlite"]))

    def test_a_chain_naming_none_reports_none(self):
        with planted():
            self.assertEqual((), D.deprecated_channels_in(["sqlite", "postgres"]))

    def test_an_UNKNOWN_name_is_not_a_deprecated_channel(self):
        """The control: a filter that returned the whole chain would satisfy the first test."""
        self.assertEqual((), D.deprecated_channels_in(["not-a-channel"]))

    def test_a_REMOVED_channel_is_not_reported_as_deprecated(self):
        """The two registries say different things and must not be confused — the mistake
        `removed_channels_in` grew a `kind` parameter to prevent."""
        for removed in D.REMOVED_CHANNELS:
            self.assertEqual((), D.deprecated_channels_in([removed]), removed)

    def test_an_empty_or_absent_chain_degrades_rather_than_raising(self):
        for chain in ([], None, ()):
            self.assertEqual((), D.deprecated_channels_in(chain), chain)


class TheProductionSurfacesReachIt(unittest.TestCase):
    """🔴 THE ROW. Driven at the ENTRY POINT, never at the announcer — that is the whole finding."""

    def _repo(self):
        import tempfile
        tmp = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, tmp, True)
        os.makedirs(os.path.join(tmp, MOKATA_DIR), exist_ok=True)
        return tmp

    def test_the_memory_surface_announces_a_deprecated_chain(self):
        from mokata.memory import selection
        root = self._repo()
        router = _Router(_Manifest("memory_store", [CHANNEL, "sqlite"]))
        buf = io.StringIO()
        with planted(), contextlib.redirect_stderr(buf):
            selection._refuse_removed_memory_chain(router, root)
        self.assertIn(WHAT, buf.getvalue(),
                      "the memory chain named a deprecated channel and nothing announced it — "
                      "which is `WARN-DEPRECATED-HAS-NO-CALLERS` back")

    def test_the_graph_surface_announces_a_deprecated_chain(self):
        """⚠ DRIVEN AT `select_backends`, NOT AT THE ARM. The first draft of this test called
        `_announce_deprecated_graph_chain` directly and a mutant that DELETED THE CALL TO IT
        survived — the test proved the arm worked and said nothing about whether anything reached
        it, which is `WARN-DEPRECATED-HAS-NO-CALLERS` reproduced inside the test written to close
        it. The mutant is `_a11_deprecation_arm_mutants.sh` K02 and it is why this line is here."""
        from mokata.knowledge import layer
        root = self._repo()
        router = _Router(_Manifest("code_graph", [CHANNEL, "grep"]))
        buf = io.StringIO()
        with planted(), contextlib.redirect_stderr(buf):
            try:
                layer.select_backends(router, root)
            except Exception:          # noqa: BLE001 — a duck-typed router may not resolve; the
                pass                   # announcement happens before resolution either way
        self.assertIn(WHAT, buf.getvalue())

    def test_it_fires_ONCE_per_repo_at_the_surface_too(self):
        """The marker contract is `warn_deprecated`'s, and it must survive being reached through a
        caller — a surface that re-announced on every read would be the noise that gets a notice
        ignored."""
        from mokata.memory import selection
        root = self._repo()
        router = _Router(_Manifest("memory_store", [CHANNEL]))
        with planted():
            first, second = io.StringIO(), io.StringIO()
            with contextlib.redirect_stderr(first):
                selection._refuse_removed_memory_chain(router, root)
            with contextlib.redirect_stderr(second):
                selection._refuse_removed_memory_chain(router, root)
        self.assertIn(WHAT, first.getvalue())
        self.assertEqual("", second.getvalue())

    def test_a_deprecated_channel_does_NOT_raise(self):
        """⭐ The line between the two arms. A REMOVED channel in a memory chain raises
        `RemovedChannelError`; a DEPRECATED one still works and must only be announced. If this
        ever raises, deprecating something has become the same act as deleting it."""
        from mokata.memory import selection
        root = self._repo()
        router = _Router(_Manifest("memory_store", [CHANNEL]))
        with planted(), contextlib.redirect_stderr(io.StringIO()):
            selection._refuse_removed_memory_chain(router, root)   # must not raise

    def test_a_clean_chain_announces_NOTHING(self):
        """The anti-vacuity control: an announcer that fired unconditionally would pass every test
        above and turn every repo's first read into a false deprecation warning."""
        from mokata.memory import selection
        root = self._repo()
        router = _Router(_Manifest("memory_store", ["sqlite"]))
        buf = io.StringIO()
        with planted(), contextlib.redirect_stderr(buf):
            selection._refuse_removed_memory_chain(router, root)
        self.assertEqual("", buf.getvalue())


class TheRegistryBeingEmptyIsAStateAndNotAnAbsence(unittest.TestCase):
    """§7g — *"an empty registry and a registry nobody reads are different facts"*, which is the
    row's closing line and the reason this file exists rather than a comment."""

    def test_the_live_registry_is_empty_AND_the_reach_exists(self):
        self.assertEqual({}, dict(D.CHANNELS),
                         "a channel is deprecated again — good; this file's fixtures still hold, "
                         "but the tree now has a live case and it should be exercised too")
        self.assertTrue(callable(getattr(D, "deprecated_channels_in", None)),
                        "the reach is gone again, so an added channel would announce nothing")


# --------------------------------------------------------- the second row on this lane's surface

#: How many members of one channel registry a literal `choices=` has to reproduce before it counts
#: as a hand-typed COPY of that registry rather than a coincidence of names. ⚠ Two, not one, and
#: the number is measured rather than chosen: `local` is a member of `TRANSPORT_KINDS` and also the
#: name of a deployment mode and of a run mode, so a threshold of one convicts
#: `choices=("local", "team")` — a surface that has nothing to do with transports and never will.
_COPY_THRESHOLD = 2


def _registry_names():
    from mokata.session_transport import TRANSPORT_KINDS
    return {
        "TRANSPORT_KINDS": frozenset(TRANSPORT_KINDS),
        "CHANNELS": frozenset(D.CHANNELS),
        "REMOVED_CHANNELS": frozenset(D.REMOVED_CHANNELS),
    }


def _literal_choice_sites(root):
    """`(rel, line, registry, overlap)` for every argparse `choices=` written as a literal that
    reproduces a channel registry."""
    registries = _registry_names()
    out = []
    src = os.path.join(root, "src", "mokata")
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in sorted(filenames):
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            with open(path, encoding="utf-8") as handle:
                tree = ast.parse(handle.read())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                for kw in node.keywords:
                    if kw.arg != "choices" or not isinstance(kw.value, (ast.Tuple, ast.List)):
                        continue
                    vals = {e.value for e in kw.value.elts
                            if isinstance(e, ast.Constant) and isinstance(e.value, str)}
                    for reg_name, members in registries.items():
                        overlap = vals & members
                        if len(overlap) >= _COPY_THRESHOLD:
                            out.append((rel, kw.value.lineno, reg_name, sorted(overlap)))
    return out


class AChoicesListMayNotBeAHandTypedCopyOfARegistry(unittest.TestCase):
    """`TRANSPORT-KINDS-DEDUPE-REINTRODUCED-THE-TYPO-ANSWER` (doc 84), the general case.

    The row's own words: *"a single derived list is the right answer for drift, and a REMOVED member
    needs to stay acceptable while not being advertised — which is two lists"*, and
    *"nothing generalises that, and the next surface to dedupe a choices list will hit this again."*

    Something generalises it now — `deprecation.answerable_choices` — and this is the guard that
    makes the next surface use it. ⛔ The failure being prevented is a user typing the flag a year of
    published `--help` text told them to type and getting **argparse's word for a TYPO** back."""

    def test_no_argparse_surface_retypes_a_channel_registry(self):
        sites = _literal_choice_sites(ROOT)
        detail = "\n".join("  %s:%d reproduces %d member(s) of %s: %s"
                            % (rel, line, len(ov), reg, ov) for rel, line, reg, ov in sites)
        self.assertEqual([], sites,
                         "%d argparse choices list(s) are hand-typed copies of a channel registry. "
                         "Use `deprecation.answerable_choices(live, removed)`, which keeps a removed "
                         "channel ACCEPTABLE (so it can be answered with its record) without "
                         "ADVERTISING it:\n%s" % (len(sites), detail))

    def test_the_scan_can_actually_fire(self):
        """§7f — a clean tree grades nothing away. The detector is driven over a synthetic module
        holding exactly the shape the row describes: the pre-fix `--to/--from` triple."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            pkg = os.path.join(tmp, "src", "mokata")
            os.makedirs(pkg)
            live = ", ".join('"%s"' % k for k in
                             __import__("mokata.session_transport", fromlist=["x"]).TRANSPORT_KINDS)
            with open(os.path.join(pkg, "surface.py"), "w", encoding="utf-8") as fh:
                fh.write('def f(p):\n    p.add_argument("--to", choices=(%s))\n' % live)
            sites = _literal_choice_sites(tmp)
        self.assertTrue(sites, "the detector found nothing in a module that retypes the registry")

    def test_the_two_surfaces_that_had_it_now_share_one_helper(self):
        from mokata.cli_commands import collab
        live, removed = ("local",), ("gone",)
        choices, metavar = D.answerable_choices(live, removed)
        self.assertEqual(("local", "gone"), choices, "a removed channel must stay ACCEPTABLE")
        self.assertEqual("{local}", metavar, "and must NOT be advertised")
        self.assertEqual(collab.STX_KINDS, D.answerable_choices(
            __import__("mokata.session_transport", fromlist=["x"]).TRANSPORT_KINDS)[0])

    def test_the_helper_keeps_order_and_drops_a_duplicate_once(self):
        choices, _ = D.answerable_choices(("a", "b"), ("b", "c"))
        self.assertEqual(("a", "b", "c"), choices)
