"""0.0.25 I-02 — `mokata setup claude --mode full`: one consent, one installer run, honest outcome.

Business rules (doc 112 §4 exit criteria 1–3):
  * `setup <harness> --mode` exists and resolves the mode's profile (the ONE documented instruction);
  * an interactive full setup asks EXACTLY ONE dependency question;
  * `--yes` without `--with-deps` installs nothing; `--with-deps` installs without asking;
  * a forced install failure ends with a failed row carrying the installer's one-line fix, and the
    quickstart never claims "everything wired" over it;
  * a human's decline is remembered once and never re-asked.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import argparse
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import adoption_modes as A
from mokata.cli_commands import setup as S

MISSING = {"embeddings": (lambda: False, lambda: True),
           "code-graph": (lambda: False, lambda: True)}


def _ok(specs, **kw):
    return mock.Mock(ok=True, deferred=False, fix="", message="installed")


def _run(mode="full", **kw):
    calls = {"asked": [], "installs": [], "adopted": []}
    base = dict(
        out=lambda _s: None, probes=MISSING, graph_pinned_fn=lambda _r: False, embedder_wired_fn=lambda _r: False,
                wire_embedder_fn=lambda root, **k: True,
        prompt_fn=lambda q: (calls["asked"].append(q), True)[1],
        install_fn=lambda specs, **k: calls["installs"].append(list(specs)) or _ok(specs),
        adopt_fn=lambda root, **k: calls["adopted"].append(k) or mock.Mock(committed=True))
    base.update(kw)
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
        res = A.offer_mode_extras(d, mode, user_home=home, **base)
    return res, calls


class TestOneConsent(unittest.TestCase):
    def test_interactive_full_asks_exactly_one_question(self):
        res, c = _run(interactive=True)
        self.assertEqual(len(c["asked"]), 1)
        self.assertIn("mokata[embeddings]", c["asked"][0])
        self.assertIn("code-review-graph", c["asked"][0])
        # 0.0.25 scale run: the graph's first index on a 1M-line repo is minutes and ~1 GB — the
        # one question says so before the yes.
        self.assertIn("several minutes", c["asked"][0])
        self.assertIn(".code-review-graph/", c["asked"][0])
        self.assertEqual(c["installs"], [["mokata[embeddings]", "code-review-graph"]])
        self.assertTrue(res.complete)

    def test_with_deps_installs_without_asking(self):
        res, c = _run(interactive=False, with_deps=True)
        self.assertEqual(c["asked"], [])
        self.assertEqual(len(c["installs"]), 1)
        self.assertTrue(res.consented and res.complete)

    def test_yes_without_with_deps_installs_nothing(self):
        res, c = _run(interactive=False)
        self.assertEqual(c["installs"], [])
        self.assertEqual(c["adopted"], [], "pinning the graph is not consented either")
        self.assertFalse(res.complete)

    def test_already_present_is_never_asked(self):
        res, c = _run(interactive=True, probes={"embeddings": (lambda: True, lambda: True),
                                                "code-graph": (lambda: True, lambda: True)},
                      graph_pinned_fn=lambda _r: True, embedder_wired_fn=lambda _r: True)
        self.assertEqual(c["asked"], [])
        self.assertTrue(all(r.status == A.PRESENT for r in res.rows))

    def test_memory_mode_does_not_mention_the_graph_cost(self):
        res, c = _run(mode="memory", interactive=True)
        self.assertEqual(len(c["asked"]), 1)
        self.assertNotIn(".code-review-graph/", c["asked"][0])

    def test_seatbelt_has_no_dependencies(self):
        res, c = _run(mode="seatbelt", interactive=True)
        self.assertEqual((res.rows, c["asked"], c["installs"]), ([], [], []))

    def test_a_human_decline_is_remembered_and_not_re_asked(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            asked = []
            kw = dict(interactive=True, user_home=home, out=lambda _s: None, probes=MISSING,
                      graph_pinned_fn=lambda _r: False, embedder_wired_fn=lambda _r: False,
                wire_embedder_fn=lambda root, **k: True,
                      prompt_fn=lambda q: (asked.append(q), False)[1])
            first = A.offer_mode_extras(d, "full", **kw)
            second = A.offer_mode_extras(d, "full", **kw)
        self.assertEqual(len(asked), 1)
        self.assertTrue(all(r.status == A.DECLINED for r in first.rows))
        self.assertTrue(all(r.status.startswith(A.DECLINED) for r in second.rows))

    def test_with_deps_overrides_an_earlier_decline(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            A.offer_mode_extras(d, "memory", interactive=True, user_home=home,
                                out=lambda _s: None, probes=MISSING,
                                embedder_wired_fn=lambda _r: False,
                                prompt_fn=lambda q: False)
            installs = []
            res = A.offer_mode_extras(d, "memory", interactive=False, with_deps=True,
                                      user_home=home, out=lambda _s: None, probes=MISSING,
                                      embedder_wired_fn=lambda _r: False,
                                      wire_embedder_fn=lambda root, **k: True,
                                      install_fn=lambda s, **k: installs.append(s) or _ok(s))
        self.assertEqual(len(installs), 1)
        self.assertTrue(res.embeddings_installed)


class TestHonestOutcome(unittest.TestCase):
    def test_failed_install_has_a_row_and_the_installers_fix(self):
        fail = mock.Mock(ok=False, deferred=False, message="uv-tool exited 2: no network",
                         fix="uv tool install mokata --with 'mokata[embeddings]'")
        res, _ = _run(interactive=False, with_deps=True, install_fn=lambda s, **k: fail)
        self.assertTrue(all(r.status.startswith(A.FAILED) for r in res.rows))
        table = A.render_outcome(res)
        self.assertIn("fix: uv tool install mokata", table)
        quick = A.render_quickstart("full", res)
        self.assertNotIn("everything wired", quick)
        self.assertIn("PARTIALLY wired", quick)

    def test_a_complete_outcome_keeps_the_banner(self):
        res, _ = _run(interactive=True)
        self.assertIn("everything wired", A.render_quickstart("full", res))

    def test_windows_deferral_is_its_own_status(self):
        dfr = mock.Mock(ok=False, deferred=True, message="", fix="uv tool install mokata ...")
        res, _ = _run(interactive=False, with_deps=True, install_fn=lambda s, **k: dfr)
        self.assertTrue(all(r.status == A.DEFERRED for r in res.rows))
        self.assertIn("fix: uv tool install mokata", A.render_outcome(res))

    def test_verification_not_exit_code_decides(self):
        res, _ = _run(interactive=False, with_deps=True,
                      probes={"embeddings": (lambda: False, lambda: False),
                              "code-graph": (lambda: False, lambda: True)})
        self.assertTrue(res.row("embeddings").status.startswith(A.FAILED))
        self.assertEqual(res.row("code-graph").status, A.INSTALLED)


class TestSemanticTierIsSwitchedOn(unittest.TestCase):
    """Measured at I-02: model2vec installed + `memory.embedder` unset = semantic tier OFF."""

    def test_consent_wires_the_embedder_setting(self):
        wired = []
        res, _ = _run(interactive=False, with_deps=True,
                      wire_embedder_fn=lambda root, **k: wired.append(root) or True)
        self.assertEqual(len(wired), 1)
        self.assertTrue(res.embedder_wired and res.complete)

    def test_present_package_but_unwired_setting_is_still_asked_once(self):
        asked = []
        res, _ = _run(interactive=True,
                      probes={"embeddings": (lambda: True, lambda: True),
                              "code-graph": (lambda: True, lambda: True)},
                      graph_pinned_fn=lambda _r: True,
                      prompt_fn=lambda q: (asked.append(q), True)[1])
        self.assertEqual(len(asked), 1)
        self.assertIn("memory.embedder", asked[0])
        self.assertTrue(res.embedder_wired)

    def test_no_consent_no_setting_write(self):
        wired = []
        res, _ = _run(interactive=False,
                      wire_embedder_fn=lambda root, **k: wired.append(root) or True)
        self.assertEqual(wired, [])
        self.assertFalse(res.embedder_wired)

    def test_installed_but_unloadable_model_says_why_and_not_reinstall(self):
        def _boom():
            raise RuntimeError("ProxyError: 403")
        res, _ = _run(interactive=False, with_deps=True,
                      probes={"embeddings": (lambda: False, _boom),
                              "code-graph": (lambda: False, lambda: True)})
        row = res.row("embeddings")
        self.assertIn("installed, but not usable here", row.status)
        self.assertIn("403", row.status)
        self.assertIn("huggingface.co", row.fix)
        self.assertNotIn("uv tool install", row.fix)
        self.assertTrue(res.embedder_wired, "auto is wired so it switches on once cached")


class TestDoctorNamesTheRightFix(unittest.TestCase):
    """`DOCTOR-HASHING-NOTE-WRONG-FIX` (seen in the I-03 rehearsal): with the package installed but
    its model not yet downloaded, doctor must not tell the user to install what they have."""

    def test_installed_but_not_loaded_points_at_the_download(self):
        from mokata.memory import tier_report as T
        with mock.patch.object(T, "_model2vec_installed", return_value=True):
            text = "\n".join(T.RetrievalStack(semantic="hashing-v1").render_lines())
        self.assertIn("IS installed", text)
        self.assertIn("huggingface.co", text)
        self.assertNotIn("install mokata[embeddings] for real semantics", text)

    def test_not_installed_still_says_install(self):
        from mokata.memory import tier_report as T
        with mock.patch.object(T, "_model2vec_installed", return_value=False):
            text = "\n".join(T.RetrievalStack(semantic="hashing-v1").render_lines())
        self.assertIn("install mokata[embeddings]", text)


class TestSetupModeFlag(unittest.TestCase):
    def _parser(self):
        p = argparse.ArgumentParser()
        sub = p.add_subparsers()
        common = argparse.ArgumentParser(add_help=False)
        common.add_argument("--path", default=".")
        S.register(sub, common)
        return p

    def test_setup_accepts_mode_and_with_deps(self):
        a = self._parser().parse_args(["setup", "claude", "--mode", "full", "--yes",
                                       "--with-deps"])
        self.assertEqual((a.mode, a.yes, a.with_deps), ("full", True, True))

    def test_setup_mode_and_profile_are_one_axis(self):
        with self.assertRaises(SystemExit), redirect_stdout(io.StringIO()), \
                mock.patch("sys.stderr", io.StringIO()):
            self._parser().parse_args(["setup", "claude", "--mode", "full",
                                       "--profile", "minimal"])

    def test_with_deps_without_mode_is_refused(self):
        a = self._parser().parse_args(["setup", "claude", "--with-deps"])
        with mock.patch("sys.stderr", io.StringIO()) as err:
            self.assertEqual(S.cmd_setup(a), 2)
        self.assertIn("--mode", err.getvalue())

    def test_setup_mode_resolves_profile_and_runs_the_dependency_step(self):
        a = self._parser().parse_args(["setup", "claude", "--mode", "full", "--yes"])
        seen = {}
        with mock.patch.object(S, "setup_harness",
                               side_effect=lambda **kw: seen.update(kw) or
                               mock.Mock(aborted=False)), \
             mock.patch.object(S, "offer_mode_extras",
                               return_value=A.ModeOffersResult(mode="full")) as off, \
             redirect_stdout(io.StringIO()) as out:
            self.assertEqual(S.cmd_setup(a), 0)
        self.assertEqual(seen["profile"], A.profile_for_mode("full"))
        self.assertFalse(off.call_args.kwargs["interactive"], "--yes is never interactive")
        self.assertFalse(off.call_args.kwargs["with_deps"])
        self.assertIn("full mode", out.getvalue())


if __name__ == "__main__":
    unittest.main()
