"""D5b — the two fallbacks that did not do what their docstrings said (0.0.13, "Correctness & Trust").

D5 swept the silent degrades and, in two places, RECORDED a mismatch rather than closing it. This
stage closes both. They are the same class of bug — a contract asserted in prose and contradicted by
the code — which is the most expensive kind, because the next reader trusts the prose.

  * `memory/store.py::_identity_and_access_for` claimed "fail-closed" and was FAIL-OPEN. On a
    team-mode store whose grants could not be read it returned `access = None`, and None does not
    mean "deny" — it means "do not enforce". The one case access control exists to survive (a
    broken/unreadable manifest) was the one case that switched it off. The fallback is now a
    DENY-BY-DEFAULT policy (enforce=True, zero grants).

  * `session_registry.py::list_sessions` claimed "an unreadable/locked registry lists nothing rather
    than raising into the read-only `mokata windows` path" — and then raised. Its typed handler's
    own fallback (`store.read`) re-opened the same file the locked RMW had just failed on, unguarded,
    so the very failure the handler absorbed came straight back out one line later.

The rule both fixes serve: a loud message must not say something untrue, and neither must a
docstring. Where the truth is narrower than the claim, the claim is corrected (a half-installed
package cannot construct a policy engine it cannot import, so THAT path stays local — loudly).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import io
import os
import sys
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import degrade, session_registry
from mokata.degrade import FAILURE_CORRUPT, FAILURE_ENGINE, FAILURE_LOCAL_IO, emitted_notices
from mokata.memory.access import AccessPolicy
from mokata.memory.scope import GLOBAL, PERSONAL, PROJECT, TEAM
from mokata.memory.store import _identity_and_access_for

ME = "alice"
TEAMMATE = "bob"


class _Manifest:
    """A manifest whose `data` is what the caller says it is. `setting()` is the ONLY thing
    `run_mode.stored_mode` asks for, so the run mode and the readability of `.data` are set
    INDEPENDENTLY here — which is the whole point: the bug lives where the mode is TEAM and the
    data is not readable."""

    def __init__(self, mode, data):
        self._mode = mode
        self.data = data

    def setting(self, key, default=None):
        return self._mode if key == "mode" else default


class _BrokenManifest:
    """A manifest that reports TEAM mode and has NO readable `.data` — the real raiser
    (AttributeError): a duck-typed surface, a half-written manifest, a torn file."""

    def setting(self, key, default=None):
        return "team" if key == "mode" else default


class _Surface:
    def __init__(self, manifest):
        self.manifest = manifest
        self.root = ""
        self.state = None


def _team(data):
    return _Surface(_Manifest("team", data))


def _local(data):
    return _Surface(_Manifest("local", data))


class D5bTestCase(unittest.TestCase):
    """Every notice is once-per-subsystem per PROCESS, so a test that does not reset the memory
    passes only because another test ran first (or fails only because one did)."""

    def setUp(self):
        degrade.reset_degrade_notices()
        self._err = io.StringIO()
        self._real_err, sys.stderr = sys.stderr, self._err
        self.addCleanup(self._restore)

    def _restore(self):
        sys.stderr = self._real_err
        degrade.reset_degrade_notices()

    @property
    def stderr(self):
        return self._err.getvalue()


# ---------------------------------------------------------------- the team-mode fallback DENIES
class TestTheTeamFallbackIsFailClosed(D5bTestCase):
    """The fix: an unreadable manifest in TEAM mode must DENY, not disable enforcement."""

    def test_the_fallback_is_a_policy_not_None(self):
        """`None` was the bug. It is not a deny — it is the absence of a decision, which the store
        reads as "do not enforce"."""
        _identity, access = _identity_and_access_for(_Surface(_BrokenManifest()))
        self.assertIsNotNone(access, "team-mode fallback returned None — enforcement is OFF")
        self.assertIsInstance(access, AccessPolicy)

    def test_it_enforces_and_grants_nothing(self):
        """Deny-by-default is exactly these two properties together: enforce=True (a non-enforcing
        policy allows EVERYTHING) and zero grants (a grant is the only thing that allows)."""
        _identity, access = _identity_and_access_for(_Surface(_BrokenManifest()))
        self.assertTrue(access.enforce)
        self.assertEqual(access.grants, {})

    def test_no_shared_item_is_readable_or_editable(self):
        """The property that matters: on a broken team manifest, nothing SHARED leaks. Before D5b
        every one of these returned True — the policy was not there to say no."""
        _identity, access = _identity_and_access_for(_Surface(_BrokenManifest()))
        for scope in (TEAM, PROJECT, GLOBAL):
            self.assertFalse(access.can_read(ME, scope), f"{scope} readable on a broken manifest")
            self.assertFalse(access.can_edit(ME, scope), f"{scope} editable on a broken manifest")
            self.assertFalse(access.can_promote_scope(ME, scope), f"{scope} promotable")

    def test_a_teammates_private_item_does_not_leak(self):
        """The S-2 property the degrade used to void: someone else's personal item is reachable only
        by an explicit grant, and a zero-grant policy has none."""
        _identity, access = _identity_and_access_for(_Surface(_BrokenManifest()))
        self.assertFalse(access.can_read(ME, PERSONAL, owner=TEAMMATE))

    def test_my_OWN_personal_items_stay_reachable(self):
        """The honest edge of "deny by default" — asserted so the docstring cannot quietly overclaim.
        `AccessPolicy.roles_for` gives the personal-scope OWNER viewer+editor over their OWN items by
        construction (the single-user default). A degrade must not lock a user out of their own
        memory; it must stop SHARED memory leaking. Both halves are the contract."""
        _identity, access = _identity_and_access_for(_Surface(_BrokenManifest()))
        self.assertTrue(access.can_read(ME, PERSONAL, owner=ME))
        self.assertTrue(access.can_edit(ME, PERSONAL, owner=ME))
        self.assertFalse(access.can_promote_scope(ME, TEAM),
                         "owning personal scope is NOT authority to broaden it")

    def test_it_says_so_LOUDLY_naming_the_real_cause(self):
        """CM.S2 shape: one notice, the right class, and a fix that names the REAL cause (the
        manifest) — not a generic 'run doctor' that would send the user hunting."""
        _identity_and_access_for(_Surface(_BrokenManifest()))
        notices = emitted_notices()
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0].subsystem, "memory-access")
        self.assertEqual(notices[0].failure_class, FAILURE_CORRUPT)
        self.assertIn("DEGRADED", self.stderr)
        self.assertIn("DENIED by default", self.stderr)
        self.assertIn("manifest", self.stderr)

    def test_the_notice_leaks_no_path(self):
        """CM.S1 secret-safety: a notice names the subsystem, the fallback and the class — never a
        directory layout. Same technique as the CapabilityDegradeNotice test in
        test_d5_degrade_taxonomy: check the text BEFORE the class label, whose own wording
        ("unreadable/corrupt") legitimately carries a slash."""
        _identity_and_access_for(_Surface(_BrokenManifest()))
        self.assertNotIn("/", self.stderr.split("(")[0])
        self.assertNotIn(os.sep + "Users", self.stderr, "no absolute path may reach the notice")

    def test_a_HEALTHY_team_manifest_still_reads_its_grants(self):
        """The fix must not turn every team store into a deny-all one — the fallback is the FALLBACK."""
        surface = _team({"settings": {"access": {"grants": {"team": {"editor": [ME]}}}}})
        _identity, access = _identity_and_access_for(surface)
        self.assertTrue(access.enforce)
        self.assertTrue(access.can_edit(ME, TEAM))
        self.assertFalse(access.can_edit(TEAMMATE, TEAM))
        self.assertEqual(emitted_notices(), [], "a healthy team store is not a degrade")


# --------------------------------------------------------------------------- LOCAL is UNTOUCHED
class TestLocalModeIsUnchanged(D5bTestCase):
    """`None` in local mode is not the bug — it IS the contract. No enforcement, byte-identical
    recall, single-user full personal access. A fail-closed policy imposed here would break every
    zero-config user for a fault that is not theirs."""

    def test_local_still_gets_no_policy(self):
        _identity, access = _identity_and_access_for(_local({"settings": {}}))
        self.assertIsNone(access)

    def test_local_with_an_unreadable_manifest_still_gets_no_policy(self):
        """The `.manifest.data` read is not even REACHED in local mode — the mode gate comes first.
        A local user with a torn manifest is not silently promoted into enforcement."""
        _identity, access = _identity_and_access_for(_Surface(_Manifest("local", None)))
        self.assertIsNone(access)

    def test_an_unknown_mode_is_never_team(self):
        """`read_mode` is fail-closed to local, and local means no policy. Asserted here because the
        D5b branch keys off exactly this call."""
        _identity, access = _identity_and_access_for(_Surface(_Manifest("nonsense", {})))
        self.assertIsNone(access)

    def test_local_is_SILENT(self):
        """Local is not a degrade, so it earns no notice. If it spoke, every zero-config user would
        see a scary line on every session — the CM.S2 credibility bug in reverse."""
        _identity_and_access_for(_local({"settings": {}}))
        self.assertEqual(emitted_notices(), [])
        self.assertEqual(self.stderr, "")


# ------------------------------------------------------- the ONE residual fail-open, named
class TestTheHalfInstallFloorIsLocalAndLOUD(D5bTestCase):
    """A half-installed package leaves the policy ENGINE unimportable. Code that cannot import
    `AccessPolicy` cannot construct a deny-by-default one, and the mode is unknowable without
    `run_mode` — so forcing enforcement would break local's contract on a guess. The floor is local,
    and the notice is what keeps that from being a secret. This test exists so the residual
    fail-open is a DECISION on record, not an oversight."""

    def _half_install(self, module):
        """Simulate a module that is not on disk. A `None` entry in `sys.modules` makes the import
        machinery raise ImportError — but ONLY if the machinery is actually consulted: `from ..
        import run_mode` is an ATTRIBUTE lookup on the already-imported parent package first, so
        the attribute has to go too or the import quietly succeeds. (Found by this test failing:
        the first version of it proved nothing.)"""
        parent, _, child = module.rpartition(".")
        real_mod = sys.modules.get(module)
        real_attr = getattr(sys.modules[parent], child, None)
        sys.modules[module] = None
        if real_attr is not None:
            delattr(sys.modules[parent], child)

        def _restore():
            if real_mod is not None:
                sys.modules[module] = real_mod
            else:
                sys.modules.pop(module, None)
            if real_attr is not None:
                setattr(sys.modules[parent], child, real_attr)
        self.addCleanup(_restore)

    def test_an_unimportable_policy_engine_falls_back_to_local_LOUDLY(self):
        self._half_install("mokata.memory.access")
        _identity, access = _identity_and_access_for(_team({"settings": {}}))
        self.assertIsNone(access, "no policy engine → no policy; the floor is local, not a guess")
        notices = emitted_notices()
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0].subsystem, "memory-access")
        self.assertEqual(notices[0].failure_class, FAILURE_ENGINE)
        self.assertIn("enforcement is OFF", self.stderr)
        self.assertIn("reinstall mokata", self.stderr.lower())

    def test_an_unimportable_run_mode_falls_back_to_local_LOUDLY(self):
        """The mode is unknowable without `run_mode`, so enforcement cannot be imposed on what may
        be a local store."""
        self._half_install("mokata.run_mode")
        _identity, access = _identity_and_access_for(_team({"settings": {}}))
        self.assertIsNone(access)
        self.assertEqual(emitted_notices()[0].failure_class, FAILURE_ENGINE)


# ------------------------------------------------- list_sessions: the contract, actually kept
class _Raiser:
    """A registry store that raises an ARBITRARY exception — not one of the three classes the typed
    handler names. Before D5b this escaped `list_sessions` and wedged `mokata windows`."""

    def __init__(self, exc):
        self.exc = exc
        self.reads = 0

    def update(self, *_a, **_kw):
        raise self.exc

    def read(self, *_a, **_kw):
        self.reads += 1
        raise self.exc


class _RaiseOnUpdate(_Raiser):
    """The exact shape D5's own comment promised was handled: the locked RMW fails, and the FALLBACK
    plain read fails the same way — because it re-opens the same unreadable file."""

    def update(self, *_a, **_kw):
        raise OSError("registry unreadable")


class _RegistrySurface:
    def __init__(self, store):
        self.state = store
        self.root = ""


class TestListSessionsNeverRaises(D5bTestCase):

    def test_an_arbitrary_raiser_lists_nothing_instead_of_raising(self):
        """The contract, asserted with a class the typed handler does NOT name. This is the test the
        old code could not pass."""
        surface = _RegistrySurface(_Raiser(RuntimeError("boom")))
        self.assertEqual(session_registry.list_sessions(surface), [])

    def test_the_typed_degrade_no_longer_escapes_through_its_OWN_fallback(self):
        """The actual bug: OSError on the locked RMW was caught, and then the fallback `store.read`
        raised OSError re-opening the same file — straight out of the handler written to absorb it.
        The fallback IS attempted (reads == 1), and its failure no longer escapes."""
        store = _RaiseOnUpdate(OSError("registry unreadable"))
        self.assertEqual(session_registry.list_sessions(_RegistrySurface(store)), [])
        self.assertEqual(store.reads, 1, "the plain-read fallback must still be attempted")

    def test_a_missing_state_store_lists_nothing(self):
        """`_registry_store` returns None when the surface has no state — the AttributeError that
        followed was a crash in a read-only view."""
        self.assertEqual(session_registry.list_sessions(_RegistrySurface(None)), [])

    def test_it_says_so_LOUDLY(self):
        """A swallow that speaks is a degrade; one that does not is the D5 bug. Even the typo this
        broad handler could otherwise hide stops reading as "no windows open": the printed line names
        the class, and the exception TEXT rides the notice's `detail` (which is what the structured
        MCP marker carries — `render()` deliberately keeps the printed line class-only, so asserting
        the text on stderr would assert something the notice format does not promise)."""
        session_registry.list_sessions(_RegistrySurface(_Raiser(AttributeError("typo"))))
        notices = emitted_notices()
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0].subsystem, "session-registry")
        self.assertEqual(notices[0].failure_class, FAILURE_LOCAL_IO)
        self.assertIn("no live windows are listed", self.stderr)
        self.assertIn("typo", notices[0].detail, "the real cause must be carried, not swallowed")
        self.assertIn("typo", notices[0].to_dict()["detail"])

    def test_it_speaks_ONCE_not_once_per_call(self):
        """`mokata windows` in a loop, or a statusline refresh, must not spam."""
        surface = _RegistrySurface(_Raiser(RuntimeError("boom")))
        for _ in range(5):
            session_registry.list_sessions(surface)
        self.assertEqual(len(emitted_notices()), 1)
        self.assertEqual(self.stderr.count("session-registry"), 1)


if __name__ == "__main__":
    unittest.main()
