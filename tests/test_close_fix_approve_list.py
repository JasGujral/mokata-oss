"""✂-FIX — the three pre-tag defects the 0.0.15 doc-gate filed (doc 94 §code-side defects).

The one that blocks the tag is the first: `mokata approve --list` is the recovery path D2 EMITS.
`awaiting.LIST_CMD` names it in every propose-path result head, in doctor's pending block, and in
the description contract on all twenty gated-write tools — and the CLI rejected it with argparse's
exit 2. A user who lost a proposal id was told, by the product, to run a command that did not
exist. So the round-trip is asserted here as one fact: the string a surface RENDERS is a command
that RUNS.

Secret-safety is asserted on the listing for a reason specific to THIS surface: unlike
`mokata approve <id>` — which is interactive, fails closed off a TTY, and shows the write in full
because a human asked at their own terminal — `--list` is read-only, non-interactive, and
therefore shell-runnable BY THE MODEL. Its output can land in a transcript. So it names ids,
tools, ages and commands, and never a proposal's content (the `awaiting` module's discipline).

Also covers the two help strings that had gone untrue: `setup --help`'s hook COUNT (four hooks
since GR.S4's dirty-track PostToolUse, not three) and `migrate --help`'s claim that the deprecated
channels are already "removed 0.0.17" (they work today, with a warning).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import contextlib
import io
import os
import re
import shlex
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401

from mokata import approval, session                               # noqa: E402
from mokata import awaiting as A                                   # noqa: E402
from mokata import cli                                             # noqa: E402
from mokata.init import init_repo                                  # noqa: E402

RUN = "run-close-fix"

# Planted in EVERY content-bearing field a proposal has. If any of them reaches the listing, the
# listing has become a place a secret can be read out of a transcript.
SECRET_DSN = "postgresql://admin:hunter2@db.internal:5432/prod"
SECRET_TOKEN = "sk-live-close-fix-must-never-render"


class _Repo:
    def __init__(self, run_id=RUN):
        self.dir = tempfile.TemporaryDirectory()
        self.path = self.dir.name
        init_repo(root=self.path, profile="standard", assume_yes=True, out=lambda *_a: None)
        self._env = mock.patch.dict(os.environ, {session.SESSION_ID_ENV: run_id})
        self._env.start()
        session.reset_for_test()

    def close(self):
        self._env.stop()
        session.reset_for_test()
        self.dir.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    def propose(self, tool="memory_remember", **kw):
        kw.setdefault("target", "memory:deploy-dsn")
        kw.setdefault("summary", "remember 'deploy-dsn'")
        kw.setdefault("preview", "value")
        return approval.propose(self.path, tool=tool, args={"k": "v", "tool": tool},
                                run_id=RUN, **kw)


def _run(argv):
    """Invoke the REAL cli entry point, capturing stdout. Returns (exit_code, output)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            code = cli.main(list(argv))
        except SystemExit as exc:                # argparse's own exit (the defect: code 2)
            code = int(exc.code or 0)
    return code, buf.getvalue()


def _help(command):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        with contextlib.suppress(SystemExit):
            cli.main([command, "--help"])
    return buf.getvalue()


def _help_top():
    """`mokata --help` — where a subcommand's `help=` (as opposed to its description) renders."""
    return cli.build_parser().format_help()


class ApproveListRuns(unittest.TestCase):
    """Defect 1 — the emitted recovery path is a command argparse accepts."""

    def test_list_flag_is_accepted_and_lists_pending(self):
        with _Repo() as r:
            p = r.propose()
            code, out = _run(["approve", "--list", "--path", r.path])
            self.assertEqual(code, 0, out)
            self.assertIn(p.proposal_id, out)
            self.assertIn("memory_remember", out)
            self.assertIn(f"mokata approve {p.proposal_id}", out,
                          "the listing must name the command that resolves each wait")

    def test_list_flag_before_the_defect_would_have_exited_two(self):
        """The regression guard proper: `--list` must never again be an unknown argument."""
        parser = cli.build_parser()
        args = parser.parse_args(["approve", "--list"])
        self.assertTrue(getattr(args, "list_pending", False))

    def test_bare_approve_still_lists(self):
        """The positional id stays optional — the pre-existing no-arg listing is not regressed."""
        with _Repo() as r:
            p = r.propose()
            code, out = _run(["approve", "--path", r.path])
            self.assertEqual(code, 0, out)
            self.assertIn(p.proposal_id, out)

    def test_multiple_pending_are_all_listed(self):
        with _Repo() as r:
            ids = [r.propose(tool=t, target=f"t{i}").proposal_id
                   for i, t in enumerate(("memory_remember", "spec_emit", "config_set"))]
            code, out = _run(["approve", "--list", "--path", r.path])
            self.assertEqual(code, 0, out)
            for pid in ids:
                self.assertIn(pid, out)

    def test_empty_state_is_friendly_and_exits_zero(self):
        with _Repo() as r:
            code, out = _run(["approve", "--list", "--path", r.path])
            self.assertEqual(code, 0, out)
            self.assertIn("nothing is waiting on you", out.lower())
            self.assertNotIn("error", out.lower())

    def test_list_is_read_only(self):
        """Listing must not approve, burn, or expire anything: the pending set is identical after."""
        with _Repo() as r:
            p = r.propose()
            before = approval.load(r.path, p.proposal_id)
            _run(["approve", "--list", "--path", r.path])
            after = approval.load(r.path, p.proposal_id)
            self.assertEqual(after.status, before.status)
            self.assertEqual(after.content_hash, before.content_hash)
            self.assertEqual([q.proposal_id for q in approval.pending(r.path)], [p.proposal_id])

    def test_list_never_renders_proposal_content(self):
        """id + tool + age + commands. Never summary, never preview, never the target — a
        memory target is `memory:<subject>`, which is the user's own words."""
        with _Repo() as r:
            p = r.propose(target=f"memory:{SECRET_TOKEN}",
                          summary=f"connect to {SECRET_DSN}",
                          preview=f"dsn = {SECRET_DSN}\ntoken = {SECRET_TOKEN}")
            code, out = _run(["approve", "--list", "--path", r.path])
            self.assertEqual(code, 0, out)
            self.assertIn(p.proposal_id, out)
            self.assertNotIn(SECRET_DSN, out)
            self.assertNotIn(SECRET_TOKEN, out)
            self.assertNotIn("hunter2", out)


class EmittedCommandRoundTrip(unittest.TestCase):
    """The D2 surfaces' emitted string names a command that RUNS — executed, not spot-read."""

    def test_awaiting_list_command_executes(self):
        with _Repo() as r:
            r.propose()
            argv = shlex.split(A.LIST_CMD)
            self.assertEqual(argv[0], "mokata")
            code, out = _run(argv[1:] + ["--path", r.path])
            self.assertEqual(code, 0, f"{A.LIST_CMD} must run: {out}")

    def test_awaiting_block_list_command_executes(self):
        """The head every propose-path result leads with, taken from the block itself."""
        with _Repo() as r:
            p = r.propose()
            block = A.awaiting_block(p.proposal_id, p.tool)
            argv = shlex.split(block["awaiting_list_command"])
            code, _out = _run(argv[1:] + ["--path", r.path])
            self.assertEqual(code, 0)

    def test_doctor_pending_lines_command_executes(self):
        """doctor tells a stuck human to run it; so it must run."""
        with _Repo() as r:
            r.propose()
            lines = A.pending_lines(r.path)
            cmds = re.findall(r"`(mokata approve --list)`", "\n".join(lines))
            self.assertTrue(cmds, f"doctor's pending block must name the listing: {lines}")
            code, _out = _run(shlex.split(cmds[0])[1:] + ["--path", r.path])
            self.assertEqual(code, 0)

    def test_gated_write_descriptions_name_a_runnable_command(self):
        """The D2 description contract on the gated-write tools points at `--list`; the string it
        pins is the same one that just executed."""
        from mokata.mcp import registry as REG
        from mokata.mcp import tool_annotations as TA
        described = [s.name for s in REG.TOOLS if s.kind != TA.READ
                     and A.LIST_CMD in TA.description_for(s.kind, s.name, s.fn.__doc__ or "")]
        # 20 -> 21 at M-4/R5 (0.0.16): `consolidate`, the drafted-summary submit. The CONTRACT is
        # unchanged and it satisfies it — a new gated write inherits the `--list` pointer from
        # `description_for`, which is exactly what this count is here to keep true.
        self.assertEqual(len(described), 21,
                         f"all twenty-one gated writes must still name the listing: {described}")
        with _Repo() as r:
            r.propose()
            code, _out = _run(shlex.split(A.LIST_CMD)[1:] + ["--path", r.path])
            self.assertEqual(code, 0)


class HelpStringsAreTrue(unittest.TestCase):
    """Defects 3 + 4 — two help strings that had drifted from what the code does."""

    def test_setup_help_does_not_claim_a_stale_hook_count(self):
        text = _help("setup")
        self.assertNotIn("all three hooks", text)
        self.assertNotRegex(text, r"(?i)\b(two|three|four|five|\d+)\s+hooks\b",
                            "the --no-hooks help must be COUNTLESS so the count cannot rot again")
        self.assertIn("hooks", text)

    # NOTE: that the help NAMES every hook setup actually wires — derived from `plan_setup`, so
    # the fourth (GR.S4 dirty-track) cannot be dropped again — is asserted where it belongs, in
    # `test_harness_setup.test_no_hooks_help_names_every_hook_it_skips`. One authority per fact.

    def test_migrate_help_does_not_claim_the_channels_are_already_removed(self):
        """Both surfaces a user meets: `mokata migrate --help` (the parser description) and the
        subcommand line in `mokata --help` (the parser's `help=`)."""
        for text in (_help("migrate"), _help_top()):
            self.assertNotIn("removed 0.0.17", text)
        detail = " ".join(_help("migrate").split())
        self.assertIn("scheduled for removal in 0.0.17", detail)
        self.assertIn("work today with a deprecation warning", detail)
        top = " ".join(_help_top().split())
        self.assertIn("scheduled for removal in 0.0.17", top)


if __name__ == "__main__":
    unittest.main()
