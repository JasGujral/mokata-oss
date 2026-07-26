"""G1 — graduated adoption: `mokata init --mode {seatbelt,memory,full}`.

The claims under test:
  * MAPPING   — each mode lands its grounded profile, and the manifest is IDENTICAL to the
                same init via `--profile` (the alias claim, proven byte-for-byte).
  * QUICKSTART— each mode prints its own 5-minute quickstart naming a runnable command, with
                slash commands in the BARE `/<name>` form (doc 85 §3).
  * CONSENT   — memory/full fire the DB.S4 ask through the EXISTING primitive; seatbelt
                STRUCTURALLY never invokes it; `--yes`/non-TTY never reaches pip in any mode.
  * COEXIST   — `--profile` + `--mode` is a usage error; bare `mokata init` is unchanged.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import argparse
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

import _support  # noqa: F401  — puts src/ on the path

from mokata import adoption_modes, extras_install
from mokata.cli_commands import setup as setup_cmd
from mokata.init import init_repo


def _init_args(path, **over):
    ns = argparse.Namespace(path=path, profile="standard", mode=None, yes=True, force=False,
                            preview=False, wizard=False, setup_harness=False)
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


def _run_init(path, **over):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = setup_cmd.cmd_init(_init_args(path, **over))
    return rc, buf.getvalue()


def _manifest(path):
    with open(os.path.join(path, ".mokata", "manifest.json"), encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------- mapping
class TestG1ModeResolvesProfile(unittest.TestCase):
    """Each mode lands the grounded profile — and the manifest equals the --profile twin."""

    GROUNDED = {"seatbelt": "standard", "memory": "standard", "full": "full"}

    def test_g1_mode_resolves_profile_seatbelt(self):
        self._assert_alias("seatbelt")

    def test_g1_mode_resolves_profile_memory(self):
        self._assert_alias("memory")

    def test_g1_mode_resolves_profile_full(self):
        self._assert_alias("full")

    def _assert_alias(self, mode):
        expected = self.GROUNDED[mode]
        self.assertEqual(adoption_modes.profile_for_mode(mode), expected)
        with tempfile.TemporaryDirectory() as via_mode, tempfile.TemporaryDirectory() as via_prof:
            rc, _ = _run_init(via_mode, mode=mode, profile="standard")
            self.assertEqual(rc, 0)
            res = init_repo(root=via_prof, profile=expected, assume_yes=True,
                            out=lambda _s: None)
            self.assertFalse(res.aborted)

            got, want = _manifest(via_mode), _manifest(via_prof)
            self.assertEqual(got["profile"], expected,
                             f"--mode {mode} must persist profile '{expected}'")
            self.assertEqual(got, want,
                             f"--mode {mode} manifest must be IDENTICAL to --profile {expected}")

    def test_g1_mode_persists_no_second_axis(self):
        """The mode is an alias + flavour: nothing named 'mode' reaches the manifest."""
        with tempfile.TemporaryDirectory() as d:
            _run_init(d, mode="full")
            blob = json.dumps(_manifest(d))
            for name in ("seatbelt", "memory_mode", "adoption_mode", '"mode"'):
                self.assertNotIn(name, blob, f"'{name}' leaked into the persisted manifest")

    def test_g1_unknown_mode_raises(self):
        with self.assertRaises(ValueError):
            adoption_modes.profile_for_mode("turbo")


# --------------------------------------------------------------------------- quickstart
class TestG1QuickstartPrinted(unittest.TestCase):
    """Mode-specific text, present at the end of a successful init, naming a runnable command."""

    def test_g1_quickstart_printed_seatbelt(self):
        out = self._init_and_capture("seatbelt")
        self.assertIn("seatbelt mode", out)
        self.assertIn("/brainstorm", out)
        self.assertIn("mokata rules", out)

    def test_g1_quickstart_printed_memory(self):
        out = self._init_and_capture("memory")
        self.assertIn("memory mode", out)
        self.assertIn("/onboard", out)
        self.assertIn("mokata memory", out)

    def test_g1_quickstart_printed_full(self):
        out = self._init_and_capture("full")
        self.assertIn("full mode", out)
        self.assertIn("mokata query blast_radius", out)
        self.assertIn("/brainstorm", out)

    def _init_and_capture(self, mode):
        with tempfile.TemporaryDirectory() as d:
            rc, out = _run_init(d, mode=mode)
            self.assertEqual(rc, 0)
            self.assertIn(adoption_modes.render_quickstart(mode).strip(), out)
            return out

    def test_g1_quickstart_texts_are_distinct(self):
        texts = {m: adoption_modes.render_quickstart(m) for m in adoption_modes.mode_names()}
        self.assertEqual(len(set(texts.values())), 3, "each mode needs its OWN quickstart")
        for mode, text in texts.items():
            body = [ln for ln in text.splitlines() if ln.strip()]
            self.assertGreaterEqual(len(body), 3, f"{mode}: quickstart too thin")
            self.assertLessEqual(len(body), 8, f"{mode}: quickstart too long to read in 5 min")

    def test_g1_quickstart_slash_commands_are_bare(self):
        """doc 85 §3 — the pip/setup route renders BARE `/<name>`, never `/mokata:<name>`."""
        for mode in adoption_modes.mode_names():
            text = adoption_modes.render_quickstart(mode)
            self.assertNotIn("/mokata:", text, f"{mode}: plugin-namespaced form on the pip route")

    def test_g1_quickstart_names_only_real_commands(self):
        """Every claim must be TRUE: each named skill/CLI command actually exists."""
        from mokata.skills import SKILL_NAMES
        named_skills = {"brainstorm", "onboard"}
        for name in named_skills:
            self.assertIn(name, set(SKILL_NAMES), f"quickstart names a skill that doesn't exist: {name}")

        parser = _build_cli_parser()
        cli_cmds = set(parser._subparsers._group_actions[0].choices)  # noqa: SLF001
        for name in ("rules", "memory", "query"):
            self.assertIn(name, cli_cmds, f"quickstart names a CLI command that doesn't exist: {name}")


def _build_cli_parser():
    from mokata.cli import build_parser
    return build_parser()


# --------------------------------------------------------------------------- consent
class _Spy:
    def __init__(self, ret=None):
        self.calls = []
        self.ret = ret

    def __call__(self, *a, **kw):
        self.calls.append((a, kw))
        return self.ret


class TestG1Consent(unittest.TestCase):
    """All consent through the DB.S4 primitives; seatbelt never enters the flow."""

    def test_g1_memory_mode_offers_embeddings(self):
        """Interactive double -> the ask fires and lands on the EXISTING install_extra."""
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            pip_spy = _Spy(ret=mock.Mock(returncode=0, stderr=""))
            asked = []

            def _offer(root, **kw):
                # the REAL DB.S4 primitive, with its own seams injected
                return extras_install.offer_embeddings(
                    root, already=lambda: False, verify=lambda: True,
                    prompt_fn=lambda q: (asked.append(q), True)[1],
                    runner=pip_spy,
                    **{**kw, "out": (lambda _s: None)})

            res = adoption_modes.offer_mode_extras(
                d, "memory", interactive=True, user_home=home, out=lambda _s: None,
                offer_embeddings_fn=_offer)

            self.assertTrue(res.embeddings_offered)
            self.assertEqual(len(asked), 1, "the DB.S4 ask must fire exactly once")
            self.assertEqual(len(pip_spy.calls), 1, "install must go through install_extra's pip")
            cmd = pip_spy.calls[0][0][0]
            self.assertEqual(cmd[:4], [sys.executable, "-m", "pip", "install"])
            self.assertTrue(res.embeddings_installed)

    def test_g1_full_mode_offers_embeddings_and_graph(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            emb, graph = _Spy(ret=mock.Mock(installed=True)), _Spy(ret=mock.Mock(adopted=True))
            res = adoption_modes.offer_mode_extras(
                d, "full", interactive=True, user_home=home, out=lambda _s: None,
                offer_embeddings_fn=emb, offer_graph_fn=graph)
            self.assertTrue(res.embeddings_offered and res.graph_offered)
            self.assertEqual(len(emb.calls), 1)
            self.assertEqual(len(graph.calls), 1, "full mode surfaces the GR.S2 graph offer")

    def test_g1_seatbelt_never_offers(self):
        """STRUCTURAL: the flow is not invoked — not merely suppressed downstream."""
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            with mock.patch.object(extras_install, "offer_extra") as oe, \
                 mock.patch.object(extras_install, "install_extra") as ie, \
                 mock.patch.object(extras_install, "offer_embeddings") as oem:
                res = adoption_modes.offer_mode_extras(
                    d, "seatbelt", interactive=True, user_home=home, out=lambda _s: None)
            self.assertFalse(res.embeddings_offered)
            self.assertFalse(res.graph_offered)
            oe.assert_not_called()
            ie.assert_not_called()
            oem.assert_not_called()
            self.assertFalse(adoption_modes.mode_spec("seatbelt").offers_embeddings)

    def test_g1_seatbelt_records_no_decline(self):
        """Never offering is not the same as declining: no decline ledger entry is written."""
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            adoption_modes.offer_mode_extras(d, "seatbelt", interactive=True,
                                             user_home=home, out=lambda _s: None)
            self.assertFalse(os.path.exists(
                os.path.join(home, ".mokata", "extra_declines.json")))

    def test_g1_yes_never_reaches_pip(self):
        """--yes / non-TTY init in ANY mode: zero pip, zero ask (the DB.S4 posture holds)."""
        for mode in adoption_modes.mode_names():
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as d:
                with mock.patch("subprocess.run") as sp, \
                     mock.patch.object(extras_install, "install_extra") as ie, \
                     mock.patch.object(extras_install, "offer_extra") as oe, \
                     mock.patch("mokata.prompt.read_yes_no") as ask:
                    rc, _ = _run_init(d, mode=mode, yes=True)
                self.assertEqual(rc, 0)
                sp.assert_not_called()
                ie.assert_not_called()
                oe.assert_not_called()
                ask.assert_not_called()

    def test_g1_non_interactive_flag_skips_offers_structurally(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(extras_install, "offer_embeddings") as oem:
                res = adoption_modes.offer_mode_extras(d, "full", interactive=False,
                                                       out=lambda _s: None)
            oem.assert_not_called()
            self.assertFalse(res.embeddings_offered)
            self.assertIn("non-interactive", res.skipped_reason)

    def test_g1_offer_failure_never_breaks_init(self):
        """DEGRADE_CLEAN — an already-successful init is never undone by an optional extra."""
        with tempfile.TemporaryDirectory() as d:
            def _boom(root, **kw):
                raise RuntimeError("index unreachable")
            res = adoption_modes.offer_mode_extras(
                d, "memory", interactive=True, out=lambda _s: None,
                offer_embeddings_fn=_boom)
            self.assertFalse(res.embeddings_installed)
            self.assertTrue(res.notes)


# --------------------------------------------------------------------------- coexistence
class TestG1Coexistence(unittest.TestCase):

    def test_g1_profile_and_mode_are_mutually_exclusive(self):
        parser = _build_cli_parser()
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit) as ctx, redirect_stdout(io.StringIO()):
                with mock.patch("sys.stderr", new=io.StringIO()):
                    parser.parse_args(["init", d, "--profile", "full", "--mode", "seatbelt"])
            self.assertEqual(ctx.exception.code, 2)

    def test_g1_mode_alone_parses(self):
        parser = _build_cli_parser()
        args = parser.parse_args(["init", "--mode", "memory"])
        self.assertEqual(args.mode, "memory")
        self.assertEqual(args.profile, "standard")   # the untouched default

    def test_g1_profile_alone_parses_unchanged(self):
        parser = _build_cli_parser()
        args = parser.parse_args(["init", "--profile", "minimal"])
        self.assertIsNone(args.mode)
        self.assertEqual(args.profile, "minimal")

    def test_g1_bare_init_byte_identical(self):
        """No mode -> today's behaviour, byte for byte (mode is opt-in; no default flip)."""
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            rc, out = _run_init(a)                       # bare: profile default, --yes
            self.assertEqual(rc, 0)
            res = init_repo(root=b, profile="standard", assume_yes=True, out=lambda _s: None)
            self.assertFalse(res.aborted)
            self.assertEqual(_manifest(a), _manifest(b))
            for mode in adoption_modes.mode_names():
                self.assertNotIn(f"{mode} mode:", out,
                                 "bare init must print no mode quickstart")

    def test_g1_preview_with_mode_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            rc, out = _run_init(d, mode="full", preview=True, yes=False)
            self.assertEqual(rc, 0)
            self.assertIn("profile 'full'", out)
            self.assertFalse(os.path.exists(os.path.join(d, ".mokata", "manifest.json")))


# --------------------------------------------------------------------------- grounding pin
class TestG1SeatbeltGrounding(unittest.TestCase):
    """The evidence that pinned seatbelt to `standard` rather than `minimal`.

    The gates consume the knowledge layer: `minimal` disables it, so the Lens-1 blast radius
    falls to the lexical floor with `degraded=True`, which `graph.required` (default ON)
    then REFUSES at brainstorm approval. A seatbelt on `minimal` would dead-end its own
    headline command. This pins the profile facts that argument rests on.
    """

    def test_minimal_disables_the_layer_the_gates_consume(self):
        from mokata.profiles import PROFILES
        self.assertFalse(PROFILES["minimal"]["layers"]["knowledge"])
        self.assertEqual(PROFILES["minimal"]["capabilities"], {})

    def test_seatbelt_profile_wires_the_ast_floor(self):
        from mokata.profiles import PROFILES
        chain = PROFILES[adoption_modes.profile_for_mode("seatbelt")]["capabilities"]["code_graph"]
        self.assertIn("ast", chain, "seatbelt must wire the structural floor the gates need")

    def test_graph_required_refuses_a_degraded_radius(self):
        from mokata.govern.graph_required import check_graph_required
        verdict = check_graph_required(degraded=True, required=True, overridden=False,
                                       consumer="blast radius (Lens 1)")
        self.assertTrue(verdict.refused)


if __name__ == "__main__":
    unittest.main()
