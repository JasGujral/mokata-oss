"""A REAL second local memory store, for the migrate tests that used to use the Obsidian vault.

0.0.18 stage 10 (lane D slice 1).

WHAT THIS REPLACES, AND WHAT IT DOES NOT PROVE — stated once, because an undeclared double is how
a suite comes to believe things it never tested.

`migrate_memory` moves items between two backends and its properties — provenance fidelity, the
idempotent upsert, `--drop-source` only after a gated confirm, a secret HARD-BLOCKED before it can
reach the destination, one ledger record per decision — need two DISTINCT stores to be observable
at all. Until stage 10 the second one was `ObsidianBackend`: a real local store, no server, no
extra, so CI exercised the whole path. `SUPPORTED` is now `(sqlite, postgres, pgvector)`, and
every one of those except `sqlite` needs a live database; `sqlite` cannot be its own destination
(`migrate_memory` refuses a self-migrate drop, correctly). **So the local cross-backend migration
that CI used to run is no longer expressible through the public API.**

The honest options were: skip those tests without a live DB — which is `PYYAML-SKIP-CLUSTER`, a
suite that stops testing and still reports OK — or supply the second store here. This supplies it.

    with second_store(root) as dest_path:
        ...  migrate_memory(surface, to_backend="pgvector", from_backend="sqlite", ...)

`build_named_backend` is patched so the destination NAME resolves to a real `SQLiteBackend` at a
DIFFERENT path. `pgvector` rather than `postgres` is not arbitrary: `to_funnel =
(to_backend == "postgres")` routes writes through the team journal, and these tests are about the
direct write path the Obsidian destination had.

⚠ WHAT A GREEN HERE DOES NOT PROVE: anything about Postgres or pgvector. The destination is
SQLite; the name is a label selecting a branch. It proves the MIGRATE ENGINE's properties across
two real, distinct, durable stores — which is exactly what the Obsidian destination proved, and
nothing more than it proved either.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import contextlib
import os
from unittest import mock

__all__ = ["DESTINATION_TOOL", "second_store"]

# The destination NAME the tests pass. Must be in `memory.migrate.SUPPORTED` and must NOT be
# `postgres` (that name selects the team-journal funnel).
DESTINATION_TOOL = "pgvector"


@contextlib.contextmanager
def second_store(root, filename="second-store.db"):
    """Patch `build_named_backend` so `DESTINATION_TOOL` builds a real SQLite store at
    `<root>/<filename>`, and every other tool builds exactly as it would have.

    Yields the destination path, so a test can open it afterwards and read what landed."""
    from mokata.memory import migrate as _migrate_module
    from mokata.memory.backends import SQLiteBackend

    path = os.path.join(root, filename)
    real = _migrate_module.build_named_backend

    def _build(tool, build_root, config=None, project=None):
        if tool == DESTINATION_TOOL:
            return SQLiteBackend(path)
        return real(tool, build_root, config, project)

    with mock.patch.object(_migrate_module, "build_named_backend", _build):
        yield path
