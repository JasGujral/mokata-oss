"""GR.S4 — the PostToolUse dirty-track hook (ASYNC observability lane).

doc 85: sync hooks are SECURITY ONLY (the exit-2 blockers); this hook is the ASYNC
observability lane — it appends touched paths to the session dirty-set and ALWAYS exits 0,
never blocks a tool call, never touches the sync security hooks.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

from mokata import hook_cli
from mokata.knowledge import freshness as F


@contextlib.contextmanager
def _stdin(text):
    old = sys.stdin
    sys.stdin = io.StringIO(text)
    try:
        yield
    finally:
        sys.stdin = old


def _envelope(cwd, path, session_id="sess-1", tool="Edit"):
    return json.dumps({
        "tool_name": tool,
        "tool_input": {"file_path": path, "new_string": "x = 1\n"},
        "cwd": cwd,
        "session_id": session_id,
    })


class TestDirtyTrackHook(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, self.root, ignore_errors=True)
        os.makedirs(os.path.join(self.root, ".mokata"), exist_ok=True)

    def test_appends_touched_path_to_dirty_set(self):
        target = os.path.join(self.root, "pkg", "m.py")
        with _stdin(_envelope(self.root, target)):
            rc = hook_cli.dirty_track_main([])
        self.assertEqual(rc, 0)
        got = F.drain_dirty(self.root, session_id="sess-1")
        self.assertTrue(any("m.py" in p for p in got), got)

    def test_always_exits_zero_never_blocks(self):
        # garbage stdin, no tool_input, empty — the async lane never fails a tool call.
        for payload in ("", "not json", "{}", json.dumps({"tool_input": {}})):
            with _stdin(payload):
                rc = hook_cli.dirty_track_main([])
            self.assertEqual(rc, 0, f"dirty-track must exit 0 (payload={payload!r})")
            self.assertNotEqual(rc, hook_cli.BLOCK_EXIT)   # never the security-block code

    def test_dispatcher_routes_dirty_track(self):
        with _stdin("{}"):
            self.assertEqual(hook_cli.main(["dirty-track"]), 0)

    def test_registered_as_subcommand(self):
        self.assertIn("dirty-track", hook_cli._SUBCOMMANDS)


class TestPostToolUseWiring(unittest.TestCase):
    def test_harness_setup_wires_posttooluse_dirty_track(self):
        from mokata import harness_setup as H
        plan = H.plan_setup("claude", root=self.root_dir(), scope="project", with_hooks=True)
        events = plan.hook_commands
        self.assertIn("PostToolUse", events, "PostToolUse not wired")
        cmds = " ".join(e.get("command", "") for e in events["PostToolUse"])
        self.assertIn("dirty-track", cmds)
        # sync security lane untouched: still exactly the two PreToolUse blockers.
        pre = " ".join(e.get("command", "") for e in events.get("PreToolUse", []))
        self.assertIn("secret-guard", pre)
        self.assertIn("gate-guard", pre)

    def test_hooks_json_declares_posttooluse(self):
        import mokata
        path = os.path.join(os.path.dirname(mokata.__file__), "hooks", "hooks.json")
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertIn("PostToolUse", data["hooks"])
        blocks = data["hooks"]["PostToolUse"]
        cmds = " ".join(h.get("command", "")
                        for b in blocks for h in b.get("hooks", []))
        self.assertIn("dirty-track", cmds)

    def root_dir(self):
        d = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, d, ignore_errors=True)
        return d


if __name__ == "__main__":
    unittest.main()
