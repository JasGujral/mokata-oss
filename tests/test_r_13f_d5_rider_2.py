"""R-13F · D5-rider(2) — the two never-raises contracts on the read-only session surfaces.

D5-rider(2) had two halves. `session_registry.list_sessions` — the fallback `store.read` sitting
OUTSIDE the guard — SHIPPED already (session_registry.py); it is PINNED here as a GUARD (pass-on-
HEAD) against regression. `session_worktree.offer_text_once` — `repo_identity(surface.root)` called
OUTSIDE its `try` — is the remaining half; its REGRESSION test FAILS on pre-rider `src/`.
"""

import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import session_registry as SR
from mokata import session_worktree as SW


class _Surface:
    root = "."


class TestOfferTextOnce(unittest.TestCase):
    """REGRESSION (fails on old code): a raising `repo_identity` must not escape offer_text_once."""

    def test_d5_rider_2_offer_text_once_never_raises_on_repo_identity_fault(self):
        # live_siblings SUCCEEDS (a sibling exists), so control reaches the repo_identity call that
        # used to sit outside the guard; that call is forced to raise.
        with mock.patch.object(SW, "live_siblings", return_value=[object()]), \
             mock.patch.object(SW, "repo_identity", side_effect=ValueError("torn repo id")):
            got = SW.offer_text_once(_Surface(), seen=set())
        self.assertIsNone(got, "a repo_identity fault yields no offer, never an exception")


class TestListSessionsGuard(unittest.TestCase):
    """GUARD (pass-on-HEAD): pins the already-shipped never-raises contract — the fallback read is
    inside the guard, so even a store that raises on BOTH update and read lists nothing, cleanly."""

    def test_d5_rider_2_list_sessions_guard_never_raises_on_raising_store(self):
        class _RaisingStore:
            def update(self, *a, **k):
                raise OSError("locked registry")

            def read(self, *a, **k):
                raise OSError("torn registry")

        with mock.patch.object(SR, "_registry_store", return_value=_RaisingStore()):
            got = SR.list_sessions(_Surface())
        self.assertEqual(got, [], "an unreadable registry lists nothing rather than raising")


if __name__ == "__main__":
    unittest.main()
