"""THE one hermetic git door for tests (0.0.29 lane D1, `TESTS-INHERIT-HOST-GIT-CONFIG`).

The Claude desktop app exports `GIT_CONFIG_PARAMETERS` (and a host can export `GIT_DIR`,
`GIT_INDEX_FILE`, `GIT_CONFIG_COUNT/KEY_n/VALUE_n`, ...). Every test spawn that spreads `os.environ`
carried them into git, and 37 tests went red in the app's terminal and green in CI. Twelve private
`_GIT_ENV` copies each scrubbed only two names. This module is the one predicate instead.

⭐ PURE: no import-time effect, no PyYAML — the e2e harness (which cannot import `_support`, see
`_provisioning`) imports it directly. `_support` APPLIES it to the process at import.

⭐ ONE PREDICATE: "which variables are git's" is `mokata.git_safe.scrubbed_env` (every `GIT_*`,
case-insensitive). This module calls it; it never restates it.

A test that WANTS a hostile git variable arranges it inside the test with
`mock.patch.dict(os.environ, ...)`. `tests/test_hermetic_git_env.py` reds on any other private copy.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if os.path.isdir(_SRC) and os.path.abspath(_SRC) not in [os.path.abspath(p) for p in sys.path]:
    sys.path.append(_SRC)

from mokata.git_safe import scrubbed_env as _scrubbed_env  # noqa: E402

#: The identity every test repo commits as (the twelve deleted copies all carried it).
IDENTITY = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}


def host_git_names(environ=None):
    """The NAMES of the `GIT_*` variables in `environ` (default `os.environ`) — never a value
    (`GIT_CONFIG_PARAMETERS` can carry a token)."""
    env = os.environ if environ is None else environ
    kept = _scrubbed_env(env)
    return sorted(k for k in env if k not in kept)


def scrub(environ=None):
    """`environ` (default `os.environ`) minus EVERY `GIT_*`, plus `GIT_TERMINAL_PROMPT=0` — exactly
    `mokata.git_safe.scrubbed_env`."""
    return _scrubbed_env(os.environ if environ is None else environ)


def git_env(**extra):
    """An env for a test's own git: `scrub(os.environ)`, the user's global and the system config
    shut out, the test identity, then `extra` (applied LAST and never scrubbed — an arrangement the
    caller names is the caller's)."""
    env = scrub(os.environ)
    env.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
                "GIT_CONFIG_NOSYSTEM": "1"})
    env.update(IDENTITY)
    env.update(extra)
    return env


#: Only the git keys `git_env` adds (for `env.update(GIT_ENV)` onto an env the caller built).
GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0", **IDENTITY}
