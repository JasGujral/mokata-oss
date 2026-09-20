"""A degrade notice that names a cause it never measured is worse than one that names none.

0.0.20, from **OSS #67 and #68** — two issues that are one defect seen from the outside.

⛔ **THE DEFECT.** `session_flow.checkpoint` wrapped the whole persist in `except Exception:` with a
BARE handler, discarded the exception, and printed:

    "could not checkpoint '<moment>' to local state — the pipeline moment still succeeded
     (your work is not blocked). Retry a save once the disk/permissions recover."

Every clause after the dash is a DIAGNOSIS THE FUNCTION NEVER MADE. It fired identically for a
`PermissionError` and for a `TypeError` in mokata's own checkpoint code, because by the time the
message was composed the one fact that told them apart had already been thrown away.

⚠ **AND SOMEBODY ACTED ON IT.** #67 is a user working that sentence to its end — 670 GB free, the
shell writing to that exact directory, the locks cleared, across a restart and an 0.0.19 upgrade —
and then reaching, unaided, the conclusion the first line should have handed them: *"This is a
mokata internal defect in the checkpoint-write path ... beyond what I can repair from inside the
session."* #68 is the same session's other half: *"the brainstorm approval never persisted to
run-state because session_save was degrading all session."*

WHAT THIS FILE GRADES, and each half fails in its own direction
---------------------------------------------------------------
  * `classify_persist_error` — an `OSError` is the FILESYSTEM refusing (the disk advice is TRUE);
    anything else is mokata's own code raising (the disk advice is FALSE and the reader must be
    sent somewhere else entirely). ⭐ A classifier that answered one class for both would pass any
    test that only ever injected one kind, which is exactly how this shipped.
  * `describe_persist_error` — the exception's TYPE reaches the user and its TEXT never does. Both
    directions: a summary with no type is useless, a summary carrying a path is a CM.S1 leak.
  * the notice itself — the two classes produce OPPOSITE remediations, and the internal one must
    not contain the words the local-io one turns on.
  * the COUNT — #68's half. The notice is once per moment; the failure is not, and doctor says how
    many. A run that failed a hundred times must not render as one that failed once.

⛔ **NO TEST HERE ASSERTS THE PROSE OF A SENTENCE.** They assert which class was chosen, that the
type is present, that the path is absent, and that the two remediations are different. A gate on
wording is a gate nobody can ship past.

Pure/offline; dependency-free; deterministic.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import session_flow as FLOW
from mokata.degrade import FAILURE_INTERNAL, FAILURE_LOCAL_IO
from mokata.govern.doctor import render_degrade_report


#: The path a real OSError carries. If any of this reaches a notice, CM.S1 is broken.
_SECRET_PATH = "/Users/jas/Clients/acme-confidential/.mokata/temp_local/state.json"


def _oserror():
    return PermissionError(13, "Permission denied", _SECRET_PATH)


def _notices(error, moment="turn"):
    """One fresh degrade world: the notice(s) this failure produces, as strings."""
    out = []
    FLOW.reset_persist_warnings()
    from mokata.degrade import reset_degrade_notices
    reset_degrade_notices()
    FLOW.note_persist_failure(moment, out.append, error=error)
    return out


class TheClassifierTellsTheTwoStoriesApart(unittest.TestCase):
    """⭐ THE WHOLE DEFECT, IN ONE FUNCTION. Before it existed there was no place in mokata that
    could answer 'is this my fault or the machine's', and the notice guessed."""

    def test_an_OSError_is_the_filesystem_refusing(self):
        self.assertEqual(FLOW.classify_persist_error(_oserror()), FAILURE_LOCAL_IO)

    def test_every_OSError_SUBCLASS_is_too(self):
        """⛔ Anti-vacuity in the direction that actually bites: `PermissionError`,
        `FileNotFoundError` and a plain `OSError` are what a broken disk raises, and a classifier
        keyed on the exact type would file two of the three as a mokata bug."""
        for error in (OSError("x"), PermissionError("x"), FileNotFoundError("x"),
                      IsADirectoryError("x"), TimeoutError("x")):
            with self.subTest(type(error).__name__):
                self.assertEqual(FLOW.classify_persist_error(error), FAILURE_LOCAL_IO)

    def test_anything_else_is_MOKATAS_OWN_CODE(self):
        """The #67 case. A `TypeError` from mokata's checkpoint path is not a disk problem, and
        every minute spent checking the disk for it is a minute the notice cost."""
        for error in (TypeError("x"), AttributeError("x"), KeyError("x"), ValueError("x"),
                      RuntimeError("x")):
            with self.subTest(type(error).__name__):
                self.assertEqual(FLOW.classify_persist_error(error), FAILURE_INTERNAL)

    def test_the_two_classes_are_NOT_THE_SAME_CLASS(self):
        """⛔ The control. Every assertion above is satisfied by a classifier that returns one
        constant, and a one-constant classifier is precisely what the old code had."""
        self.assertNotEqual(FLOW.classify_persist_error(_oserror()),
                            FLOW.classify_persist_error(TypeError("x")))


class TheSummaryCarriesTheTypeAndNothingElse(unittest.TestCase):

    def test_the_TYPE_reaches_the_reader(self):
        self.assertIn("PermissionError", FLOW.describe_persist_error(_oserror()))
        self.assertIn("TypeError", FLOW.describe_persist_error(TypeError("boom")))

    def test_an_OSError_carries_its_ERRNO_SYMBOL(self):
        """`EACCES` is a symbol from a fixed table — bounded, non-user data — and it is the
        difference between 'the disk' and 'this exact permission problem'."""
        self.assertIn("EACCES", FLOW.describe_persist_error(_oserror()))

    def test_the_PATH_never_reaches_the_reader(self):
        """⛔ CM.S1. `str(OSError)` interpolates the path it failed on, and a notice may not leak a
        directory layout. This is why the summary is BUILT rather than taken from the exception."""
        summary = FLOW.describe_persist_error(_oserror())
        self.assertNotIn(_SECRET_PATH, summary)
        self.assertNotIn("acme-confidential", summary)
        self.assertNotIn("Permission denied", summary)

    def test_the_MESSAGE_of_a_non_OSError_never_reaches_the_reader_either(self):
        """A `ValueError` message is just as capable of carrying user content."""
        summary = FLOW.describe_persist_error(ValueError("topic=acme merger, answer=confidential"))
        self.assertNotIn("acme", summary)
        self.assertNotIn("confidential", summary)

    def test_a_failure_that_was_NEVER_CAPTURED_says_so(self):
        """§7g. 'nobody passed me the exception' and 'the exception was a TypeError' are different
        facts. Rendering the first as the second would rebuild the guess this file exists to end."""
        self.assertNotEqual(FLOW.describe_persist_error(None),
                            FLOW.describe_persist_error(TypeError("x")))
        self.assertIn("not captured", FLOW.describe_persist_error(None))


class TheNoticeSendsTheReaderToTHEOTHERPLACE(unittest.TestCase):
    """The two remediations must be DIFFERENT, and the internal one must not contain the local-io
    advice. That is the assertion #67 would have gone red on."""

    def test_a_filesystem_failure_still_says_check_permissions_and_disk(self):
        notice = _notices(_oserror())[0]
        self.assertIn("permissions/disk", notice)

    def test_a_MOKATA_failure_does_NOT_say_check_permissions_and_disk(self):
        """⭐ THE REGRESSION TEST FOR OSS #67, stated as the sentence that must not appear."""
        notice = _notices(TypeError("'NoneType' object is not subscriptable"))[0]
        self.assertNotIn("permissions/disk", notice)
        self.assertNotIn("disk", notice.lower().replace("disks", ""))

    def test_a_MOKATA_failure_says_it_is_NOT_the_users_machine(self):
        notice = _notices(TypeError("x"))[0].lower()
        self.assertIn("not in your machine", notice)

    def test_the_notice_carries_the_exception_TYPE(self):
        """⚠ IT RIDES `fallback`, NOT `detail`, AND THE DIFFERENCE IS MEASURED. Rendering `detail`
        globally was drafted and then withdrawn: twenty-odd sites in this tree put a RAW exception
        in it, one of them a psycopg connection error carrying host/port/user, so showing them all
        would have broken the notice's never-the-DSN-value promise (doc 84,
        `DEGRADE-DETAIL-IS-COMPUTED-BY-38-SITES-AND-SHOWN-BY-NONE`). THIS site's summary is bounded
        by construction, so it goes where a person reads it — which is the whole fix."""
        self.assertIn("TypeError", _notices(TypeError("x"))[0])
        self.assertIn("PermissionError", _notices(_oserror())[0])

    def test_the_notice_shows_the_type_even_though_DETAIL_is_not_rendered(self):
        """⛔ The trap this pins shut: a later change that starts rendering `detail` must not be
        able to make this file green by accident, and a change that stops must not silently take
        the type away. The summary's presence is asserted against the RENDERED string either way."""
        from mokata.degrade import CapabilityDegradeNotice
        bare = CapabilityDegradeNotice(subsystem="s", env_name="", failure_class=FAILURE_INTERNAL,
                                       detail="TypeError", fix="f", fallback="fell back")
        self.assertNotIn("TypeError", bare.render(),
                         "`detail` is being rendered — if that is now deliberate, the 20 raw-"
                         "exception producers must be bounded FIRST (doc 84 row)")

    def test_the_notice_leaks_no_path(self):
        self.assertNotIn("acme-confidential", _notices(_oserror())[0])

    def test_the_two_notices_are_not_the_same_notice(self):
        """⛔ The control again, one layer up: every assertion above passes for a pair of notices
        that differ only in a word nobody reads."""
        self.assertNotEqual(_notices(_oserror())[0], _notices(TypeError("x"))[0])


class TheREPEATSAreCountedEvenThoughTheyAreNotAnnounced(unittest.TestCase):
    """#68's half: *"session_save was degrading all session"*. The notice is once per moment and
    that is right; the FACT is every time and that was missing."""

    def setUp(self):
        FLOW.reset_persist_warnings()
        from mokata.degrade import reset_degrade_notices
        reset_degrade_notices()

    def test_the_notice_fires_ONCE_and_the_count_keeps_rising(self):
        sink = []
        for _ in range(5):
            FLOW.note_persist_failure("turn", sink.append, error=TypeError("x"))
        self.assertEqual(len(sink), 1, "a long run must not spam — the notice is once per moment")
        self.assertEqual(FLOW.persist_failures()["turn"], 5,
                         "the notice is once; the failure is not, and doctor needs the number")

    def test_each_MOMENT_is_counted_separately(self):
        FLOW.note_persist_failure("turn", lambda m: None, error=TypeError("x"))
        FLOW.note_persist_failure("gate:spec", lambda m: None, error=TypeError("x"))
        FLOW.note_persist_failure("gate:spec", lambda m: None, error=TypeError("x"))
        self.assertEqual(FLOW.persist_failures(), {"turn": 1, "gate:spec": 2})

    def test_doctor_reports_a_repeat_and_stays_silent_about_a_single(self):
        """⚠ Both directions. A permanent 'x1' on every row is the noise `render_degrade_report`'s
        own docstring refuses; a run that failed a hundred times reading as one is §7g."""
        FLOW.note_persist_failure("turn", lambda m: None, error=TypeError("x"))
        single = render_degrade_report()
        self.assertNotIn("times this session", single)
        for _ in range(9):
            FLOW.note_persist_failure("turn", lambda m: None, error=TypeError("x"))
        many = render_degrade_report()
        self.assertIn("failed 10 times this session", many)

    def test_a_healthy_session_reports_NOTHING(self):
        self.assertEqual(FLOW.persist_failures(), {})
        self.assertEqual(render_degrade_report(), "")


class TheFailureIsREACHABLEFromTheRealSeam(unittest.TestCase):
    """§7c/§7f — every assertion above talks to `note_persist_failure` directly. Without this the
    battery would be green against a `checkpoint()` that still swallowed the exception."""

    def setUp(self):
        FLOW.reset_persist_warnings()
        from mokata.degrade import reset_degrade_notices
        reset_degrade_notices()

    def _flow_that_explodes(self, error):
        sink = []
        original = FLOW.save_session

        def boom(*a, **k):
            raise error

        FLOW.save_session = boom
        try:
            flow = FLOW.SessionFlow(object(), warn=sink.append)
            self.assertIsNone(flow.turn({"topic": "t"}), "the moment must not fail")
        finally:
            FLOW.save_session = original
        return sink

    def test_the_real_checkpoint_seam_forwards_a_MOKATA_error(self):
        notice = self._flow_that_explodes(TypeError("x"))[0]
        self.assertIn("TypeError", notice)
        self.assertNotIn("permissions/disk", notice)

    def test_the_real_checkpoint_seam_forwards_a_FILESYSTEM_error(self):
        notice = self._flow_that_explodes(_oserror())[0]
        self.assertIn("PermissionError", notice)
        self.assertIn("permissions/disk", notice)
        self.assertNotIn("acme-confidential", notice)


class TheOtherSWALLOWSitesForwardItToo(unittest.TestCase):
    """⛔ §7j — a fix that reaches ONE of four identical handlers is a fix for a subset. The engine
    has three more `except Exception` sites on this same channel, and a reader that stops at the
    one it was reported against leaves the other three guessing."""

    def _handlers(self):
        import ast
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        found = []
        for rel in ("src/mokata/session_flow.py", "src/mokata/engine/phases.py",
                    "src/mokata/engine/emit.py"):
            with open(os.path.join(root, *rel.split("/")), "r", encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if name != "note_persist_failure":
                    continue
                found.append((rel, sorted(k.arg for k in node.keywords if k.arg)))
        return found

    def test_every_call_site_passes_the_exception(self):
        sites = self._handlers()
        self.assertGreaterEqual(len(sites), 4,
                                "the population shrank — this test now grades a subset: %r" % sites)
        for rel, kwargs in sites:
            with self.subTest(rel):
                self.assertIn("error", kwargs,
                              "%s calls note_persist_failure WITHOUT the exception, so its notice "
                              "falls back to guessing — the OSS #67 shape" % rel)


if __name__ == "__main__":
    unittest.main()
