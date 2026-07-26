"""GR.S2 (e)+(g)+(h)+(i) — adopt/consent flow + detect-and-offer + first-use disclosure.

code-review-graph is the RECOMMENDED DEFAULT at setup (embedder pattern), NOT hard-mandatory:
  (i) setup/init ASK + assisted install, consented (fail-closed off TTY), USER-scoped decline
      record (never re-asked); accept => gated `graph adopt` pins it; decline => AST floor.
  (e) an existing repo's manifest is refreshed ONLY through the gated path — never a silent
      rewrite.
  (g) a detected-but-unpinned graph tool may be USED read-only via the router; PINNING stays
      gated (P2).
  (h) the first USE of a newly PATH-detected external graph binary gets a once-per-repo
      disclosed + ledgered notice (supply-chain surface, P14).
"""

import json
import os
import tempfile
import unittest

from mokata.knowledge import graph_adopt, user_prefs
from mokata.profiles import build_manifest_data


def _init_repo(root, profile="standard"):
    mdir = os.path.join(root, ".mokata")
    os.makedirs(mdir, exist_ok=True)
    data = build_manifest_data(profile, "0.1.0")
    with open(os.path.join(mdir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    return root


def _chain(root):
    with open(os.path.join(root, ".mokata", "manifest.json"), encoding="utf-8") as fh:
        data = json.load(fh)
    return data["capabilities"]["code_graph"]["fallback"], data["tools"]


def _mtext(root):
    with open(os.path.join(root, ".mokata", "manifest.json"), encoding="utf-8") as fh:
        return fh.read()


class TestAdoptPinsThroughTheGate(unittest.TestCase):
    """(i) accept => gated `graph adopt` pins code-review-graph into the committed manifest."""

    def test_adopt_pins_tool_and_chain(self):
        with tempfile.TemporaryDirectory() as d:
            _init_repo(d)
            res = graph_adopt.adopt_graph(d, tool="code-review-graph", assume_yes=True)
            self.assertTrue(res.committed)
            chain, tools = _chain(d)
            self.assertEqual(chain[0], "code-review-graph")  # pinned at the front
            self.assertIn("code-review-graph", tools)
            self.assertIn("ast", chain)                       # floor preserved

    def test_adopt_refuses_on_version_skew(self):
        with tempfile.TemporaryDirectory() as d:
            _init_repo(d)

            class SkewClient:
                def version(self, root=None):
                    return "3.9.0"
            res = graph_adopt.adopt_graph(d, tool="code-review-graph", assume_yes=True,
                                          client=SkewClient())
            self.assertFalse(res.committed)
            chain, _ = _chain(d)
            self.assertNotIn("code-review-graph", chain)      # skew => not pinned


class TestGatedRefreshOnly(unittest.TestCase):
    """(e) the manifest is refreshed ONLY through the gate — a declined gate writes nothing."""

    def test_declined_gate_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            _init_repo(d)
            before = _mtext(d)
            res = graph_adopt.adopt_graph(d, tool="code-review-graph",
                                          confirm=lambda _msg: False)   # human says no
            self.assertFalse(res.committed)
            after = _mtext(d)
            self.assertEqual(before, after, "a declined gate must not rewrite the manifest")


class TestConsentAndDecline(unittest.TestCase):
    """(i) the setup OFFER: consent, USER-scoped decline record, never re-asked."""

    def test_accept_installs_and_adopts(self):
        with tempfile.TemporaryDirectory() as d:
            _init_repo(d)
            installs = []
            out = graph_adopt.offer_graph_at_setup(
                d, prompt_fn=lambda _q: True, assume_yes=True,
                install_fn=lambda: installs.append(True) or True)
            self.assertTrue(out.adopted)
            self.assertTrue(installs)
            chain, _ = _chain(d)
            self.assertIn("code-review-graph", chain)

    def test_decline_records_user_scope_and_never_re_asks(self):
        with tempfile.TemporaryDirectory() as d, \
                tempfile.TemporaryDirectory() as home:
            _init_repo(d)
            asked = []

            def prompt(_q):
                asked.append(True)
                return False
            graph_adopt.offer_graph_at_setup(d, prompt_fn=prompt, user_home=home)
            self.assertEqual(len(asked), 1)
            self.assertTrue(user_prefs.graph_declined(d, user_home=home))
            # a SECOND offer must not ask again (no nag)
            graph_adopt.offer_graph_at_setup(d, prompt_fn=prompt, user_home=home)
            self.assertEqual(len(asked), 1, "declined once => never re-asked")

    def test_off_tty_is_fail_closed_not_adopted(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            _init_repo(d)
            # read_yes_no returns False off a TTY; simulate that default behaviour.
            out = graph_adopt.offer_graph_at_setup(d, prompt_fn=lambda _q: False,
                                                   user_home=home)
            self.assertFalse(out.adopted)
            chain, _ = _chain(d)
            self.assertNotIn("code-review-graph", chain)


class TestDetectAndOfferReadOnly(unittest.TestCase):
    """(g) a detected-but-unpinned real graph tool is surfaced for read-only use; PINNING
    stays gated (the manifest is not touched by detection)."""

    def test_overlay_surfaces_detected_unpinned_tool(self):
        from mokata.detect import Detector
        from mokata.manifest import Manifest
        from mokata.router import Router
        with tempfile.TemporaryDirectory() as d:
            _init_repo(d)                      # standard: chain has no real graph pinned
            m = Manifest.from_dict(build_manifest_data("standard", "0.1.0"))
            det = Detector(overrides={"code-review-graph": True})
            router = Router(m, det)
            tool = graph_adopt.detected_graph_overlay(router, d)
            self.assertEqual(tool, "code-review-graph")

    def test_overlay_is_none_when_already_pinned(self):
        from mokata.detect import Detector
        from mokata.manifest import Manifest
        from mokata.router import Router
        with tempfile.TemporaryDirectory() as d:
            _init_repo(d, profile="full")      # full pins code-review-graph
            m = Manifest.from_dict(build_manifest_data("full", "0.1.0"))
            det = Detector(overrides={"code-review-graph": True})
            router = Router(m, det)
            self.assertIsNone(graph_adopt.detected_graph_overlay(router, d))

    def test_detection_does_not_write_the_manifest(self):
        from mokata.detect import Detector
        from mokata.manifest import Manifest
        from mokata.router import Router
        with tempfile.TemporaryDirectory() as d:
            _init_repo(d)
            before = _mtext(d)
            m = Manifest.from_dict(build_manifest_data("standard", "0.1.0"))
            router = Router(m, Detector(overrides={"code-review-graph": True}))
            graph_adopt.detected_graph_overlay(router, d)
            after = _mtext(d)
            self.assertEqual(before, after)


class TestFirstUseDisclosure(unittest.TestCase):
    """(h) the first USE of a PATH-detected external graph binary: once-per-repo, ledgered."""

    def test_disclosure_is_ledgered_once(self):
        from mokata.govern.ledger import AuditLedger
        with tempfile.TemporaryDirectory() as d:
            _init_repo(d)
            mdir = os.path.join(d, ".mokata")
            led = AuditLedger.from_mokata_dir(mdir)
            fired1 = graph_adopt.disclose_first_use(d, "code-review-graph", ledger=led)
            fired2 = graph_adopt.disclose_first_use(d, "code-review-graph", ledger=led)
            self.assertTrue(fired1)
            self.assertFalse(fired2, "the disclosure must fire once per repo, then never again")
            kinds = [e.get("kind") for e in led.entries()]
            self.assertEqual(kinds.count("graph_first_use"), 1)


if __name__ == "__main__":
    unittest.main()
