"""Stage 29 — auto-engage brainstorm when the user is exploring.

Both jsonschema states. brainstorm is model-invocable (carries an autonomous-trigger
`when_to_use`) AND /mokata:brainstorm still works; the settings.brainstorm.auto toggle
(on/off/ask) is honored; auto-engaging announces via the Stage 27 banner; and the HARD-GATE
still blocks a spec without explicit approval even when brainstorm was auto-engaged.
"""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout

import _support  # noqa: F401  (puts src/ on the path)

from mokata import config_cmd
from mokata.brainstorm import (
    AUTO_OFF,
    AUTO_ON,
    Approach,
    BrainstormGateError,
    BrainstormSession,
    brainstorm_auto_mode,
    brainstorm_engaged_banner,
    decide_auto_engage,
)
from mokata.cli import main
from mokata.config import Surface
from mokata.init import init_repo
from mokata.manifest import Manifest
from mokata.skills import command_markdown, get_skill

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _silent(_):
    pass


def _manifest(auto=None):
    data = {
        "manifest_version": 1, "mokata": {"version": "0.0.0"}, "profile": "custom",
        "layers": {"engine": {"enabled": True}, "knowledge": {"enabled": True},
                   "memory": {"enabled": True}, "governance": {"enabled": True}},
        "capabilities": {}, "tools": {}, "settings": {},
    }
    if auto is not None:
        data["settings"]["brainstorm"] = {"auto": auto}
    return Manifest.from_dict(data)


# ------------------------------------------------------- model-invocable + slash still works

class TestModelInvocable(unittest.TestCase):
    def test_brainstorm_exposes_autonomous_trigger(self):
        skill = get_skill("brainstorm")
        self.assertTrue(skill.when_to_use)
        self.assertIn("exploring", skill.when_to_use.lower())
        # the generated frontmatter would carry it (when_to_use:) for model-invocation
        self.assertIn("when_to_use:", command_markdown(skill))

    def test_shipped_brainstorm_template_carries_trigger(self):
        with open(os.path.join(ROOT, "templates", "commands", "brainstorm.md"),
                  encoding="utf-8") as fh:
            text = fh.read()
        head = text.split("---", 2)[1]
        self.assertIn("when_to_use:", head)
        self.assertIn("exploring", head.lower())

    def test_slash_brainstorm_still_works(self):
        # /mokata:brainstorm is the command; the standalone CLI path still runs it.
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(["skills", "brainstorm"])
        self.assertEqual(rc, 0)
        self.assertIn("approach-approval", buf.getvalue())


# ------------------------------------------------------------------- the auto toggle

class TestAutoToggle(unittest.TestCase):
    def test_default_is_on(self):
        self.assertEqual(brainstorm_auto_mode(_manifest()), AUTO_ON)
        self.assertEqual(brainstorm_auto_mode(_manifest("bogus")), AUTO_ON)
        self.assertEqual(brainstorm_auto_mode(None), AUTO_ON)

    def test_on_engages_when_exploring(self):
        d = decide_auto_engage(_manifest("on"), exploring=True)
        self.assertTrue(d.engage)
        self.assertFalse(d.offer)
        self.assertEqual(d.banner, brainstorm_engaged_banner())

    def test_off_never_engages(self):
        d = decide_auto_engage(_manifest("off"), exploring=True)
        self.assertFalse(d.engage)
        self.assertFalse(d.offer)
        self.assertEqual(d.mode, AUTO_OFF)
        self.assertIn("disabled", d.reason)

    def test_ask_offers_not_engages(self):
        d = decide_auto_engage(_manifest("ask"), exploring=True)
        self.assertFalse(d.engage)
        self.assertTrue(d.offer)

    def test_does_not_hijack_when_not_exploring(self):
        for mode in ("on", "ask", "off"):
            d = decide_auto_engage(_manifest(mode), exploring=False)
            self.assertFalse(d.engage)
            self.assertFalse(d.offer)
            self.assertIn("not exploring", d.reason)

    def test_toggle_round_trips_through_config(self):
        with tempfile.TemporaryDirectory() as dd:
            init_repo(root=dd, profile="standard", assume_yes=True, out=_silent)
            config_cmd.config_set(dd, "settings.brainstorm.auto", "off",
                                  assume_yes=True, out=_silent)
            surface = Surface.load(dd)
            self.assertEqual(brainstorm_auto_mode(surface.manifest), AUTO_OFF)
            self.assertFalse(
                decide_auto_engage(surface.manifest, exploring=True).engage)


# ------------------------------------------------------------------- banner

class TestBanner(unittest.TestCase):
    def test_engaged_banner(self):
        self.assertEqual(brainstorm_engaged_banner(),
                         "mokata · brainstorm (engaged)")


# ------------------------------------------------------------------- HARD-GATE preserved

class TestHardGateStillHolds(unittest.TestCase):
    def test_auto_engaged_brainstorm_still_gates_the_spec(self):
        # auto-engage decides to start the conversation...
        decision = decide_auto_engage(_manifest("on"), exploring=True)
        self.assertTrue(decision.engage)
        # ...but the HARD-GATE is unchanged: no handoff/spec until explicit approval.
        session = BrainstormSession("a new caching layer")
        session.propose_approaches([
            Approach("a", "x", pros=["p"], cons=["c"]),
            Approach("b", "y", pros=["p"], cons=["c"]),
        ])
        self.assertFalse(session.can_emit_spec)
        with self.assertRaises(BrainstormGateError):
            session.handoff()
        # only an explicit approval opens the gate
        session.approve("jas", "a")
        self.assertTrue(session.can_emit_spec)
        self.assertEqual(session.handoff().approach.name, "a")


if __name__ == "__main__":
    unittest.main()
