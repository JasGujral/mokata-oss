"""R-13F · D5-rider(1) — GUARD (pass-on-HEAD) for the fail-closed access policy.

The deny-by-default fallback SHIPPED already (memory/store.py:_identity_and_access_for, landed with
the 0.0.13 riders). This is a GUARD, not a regression: it PINS the shipped behaviour so a future
change cannot silently flip the team-mode error path back to `access=None` (enforcement OFF).

Contract: in TEAM mode, when the manifest's grants cannot be read (a torn `.manifest.data`), the
policy is deny-by-default — `enforce=True`, zero grants: shared items are unreachable, but the
identity's OWN personal items still resolve (the owner rule).
"""

import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata.memory.scope import PERSONAL
from mokata.memory.store import _identity_and_access_for


class _BrokenManifest:
    @property
    def data(self):
        raise AttributeError("torn / half-written manifest")


class _TeamSurface:
    def __init__(self):
        self.manifest = _BrokenManifest()
        self.root = "."


class TestFailClosedAccessGuard(unittest.TestCase):
    def test_d5_rider_1_guard_deny_by_default_on_team_manifest_fault(self):
        with mock.patch("mokata.team_audit.actor", return_value="alice"), \
             mock.patch("mokata.run_mode.read_mode", return_value="team"), \
             mock.patch("mokata.degrade.note_degraded"):
            identity, access = _identity_and_access_for(_TeamSurface())

        self.assertEqual(identity, "alice")
        self.assertIsNotNone(access, "a torn team manifest must NOT disable enforcement")
        self.assertTrue(access.enforce, "the fallback ENFORCES (deny-by-default), never OFF")
        self.assertEqual(access.grants, {}, "zero grants — nothing is granted on doubt")
        # shared scopes are denied...
        self.assertFalse(access.can_read("someone-else", "team"))
        self.assertFalse(access.can_read("alice", "project"))
        # ...but the identity's OWN personal items still resolve (owner rule).
        self.assertTrue(access.can_read("alice", PERSONAL, owner="alice"))


if __name__ == "__main__":
    unittest.main()
