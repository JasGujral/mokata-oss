"""SIMP.S2 — reconcile the stale `team connect` readiness message (carried from SIMP.S1 dev-2).

SIMP.S1 made the session transport DERIVE from mode: a team-connected repo with no reachable DSN
REFUSES a portable session (a clear `SessionTransportUnavailable`), it does NOT silently degrade to
a local file. So `team connect`'s "sessions [degrade] to the local transport" line became a lie.
The truth: memory degrades to the local SQLite floor; a session REFUSES rather than silently
writing a private local bundle (`--file` forces local).
"""

import os
import tempfile
import unittest

import _support  # noqa: F401

from mokata import team
from mokata.config import Surface
from mokata.init import init_repo


def _silent(_):
    pass


def _repo(d):
    init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
    return Surface.load(d)


class TestStaleMessageReconciled(unittest.TestCase):
    def test_connect_message_says_memory_floors_but_sessions_refuse(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            os.environ.pop("MOKATA_PG_DSN", None)         # not active yet
            msgs = []
            team.team_connect(d, surface, "MOKATA_PG_DSN", assume_yes=True, out=msgs.append)
            blob = " ".join(msgs).lower()
            # memory still degrades to the local floor…
            self.assertIn("floor", blob)
            # …but a session REFUSES, it does NOT silently go to a local transport.
            self.assertIn("refus", blob)
            self.assertNotIn("sessions to the local transport", blob)


if __name__ == "__main__":
    unittest.main()
