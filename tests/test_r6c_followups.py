"""0.0.27 R6 follow-ups (c) and (d) — migrate's default source, and ONE run identity.

Business rule (d): the shared event log, the team audit log and the memory store attribute an
action to the SAME person, read ONE way (`session.actor`). `team_events` carried a second,
identical copy of that function, and two copies drift.

Business rule (c): when the memory_store route cannot be resolved (the manifest is broken, or the
surface has no router at all), migrate falls back to the documented default source, the SQLite
floor. It must not raise. Before this fix, the `except (ManifestError, AttributeError)` clause in
`migrate._resolved_memory_tool` named a class the module never imported. So reaching that clause
raised `NameError`: the handler written for exactly this failure crashed on it.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata.manifest import ManifestError
from mokata.memory import migrate as M


class _BrokenRouter:
    def resolve(self, _capability):
        raise ManifestError("torn manifest")


class _Surface:
    def __init__(self, router=None):
        if router is not None:
            self.router = router


class TheR6c_DefaultSourceSurvivesAnUnresolvableRoute(unittest.TestCase):

    def test_R6c_a_broken_manifest_defaults_to_sqlite(self):
        self.assertEqual("sqlite", M._resolved_memory_tool(_Surface(_BrokenRouter())))

    def test_R6c_a_surface_with_no_router_defaults_to_sqlite(self):
        self.assertEqual("sqlite", M._resolved_memory_tool(_Surface()))


class TheR6d_EveryLogAttributesTheSamePerson(unittest.TestCase):

    def test_R6d_one_actor_definition(self):
        from mokata import session, team_audit, team_events
        self.assertIs(session.actor, team_events.actor)
        self.assertIs(session.actor, team_audit.actor)

    def test_R6d_the_actor_is_the_configured_name(self):
        from unittest import mock
        from mokata import team_events
        with mock.patch.dict("os.environ", {"MOKATA_ACTOR": "alice"}):
            self.assertEqual("alice", team_events.actor())


if __name__ == "__main__":
    unittest.main()
