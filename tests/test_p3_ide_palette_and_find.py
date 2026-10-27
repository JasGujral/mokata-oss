"""0.0.26 P3 — ID-PALETTE-FULL + ID-INFILE-FIND: one palette with prefixes, and find in the view.

Business rules — what must be true for the person using the IDE:
  * The palette's `>` list names EVERY `/mokata:*` command and skill the shipped plugin carries —
    read from the very directories `.claude-plugin/plugin.json` ships, so a new command or skill
    file is in the palette with no code edit (a skill left out fails here).
  * The list is a paired, light GET that answers the same in SAMPLE mode (it is mokata's surface,
    not the repo's) and says the IDE copies a command for the human — it never sends one itself.
  * `#` asks the WORKSPACE symbols: the symbols-only form of ⌘P's route ranks the definition the
    human typed first and returns no files.
  * One prefix grammar — none files, `@` this file's symbols, `#` workspace, `:` line, `>`
    commands, `/` mokata commands — and one ranking rule (prefix > substring > scattered), with a
    mokata command winning a tie against a skill.
  * Every feature's keys register together with no conflict: ⌘K, ⌘⇧P, ⌘G, ⌘F, ⌘P each have one
    owner, and the command list shows each command's shortcut (which-key).
  * Find: literal by default (regex characters are literal), case-insensitive unless asked,
    regex on request, a bad pattern is refused (not silently empty), an empty-match pattern
    never loops.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

import _support  # noqa: F401  (puts src/ on the path)

from test_p0_ide_api_contract import ORIGIN, client, fixture

REPO = Path(__file__).resolve().parents[1]
STATIC = REPO / "src" / "mokata" / "ide" / "static"
FEATURES = STATIC / "features"


def _frontmatter_name(md: str, default: str) -> str:
    m = re.search(r"(?m)^name:\s*(.+?)\s*$", md.split("\n---", 1)[0])
    return m.group(1).strip() if m else default


def _shipped():
    """(command names, skill names) straight from the plugin manifest's directories."""
    manifest = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    cdir, sdir = REPO / manifest["commands"], REPO / manifest["skills"]
    cmds = {_frontmatter_name(p.read_text(encoding="utf-8"), p.stem) for p in cdir.glob("*.md")}
    skills = {_frontmatter_name((d / "SKILL.md").read_text(encoding="utf-8"), d.name)
              for d in sdir.iterdir() if (d / "SKILL.md").is_file()}
    return cmds, skills


class SlashCommandsAreDerivedFromThePlugin(unittest.TestCase):

    def test_every_shipped_command_and_skill_is_listed_once(self):
        from mokata.ide.features.palette import slash_commands
        rows = slash_commands()
        names = [r["name"] for r in rows]
        self.assertEqual(len(names), len(set(names)), "a /mokata: entry is listed twice")
        cmds, skills = _shipped()
        self.assertTrue(cmds and skills)
        missing = (cmds | skills) - set(names)
        self.assertFalse(missing, f"shipped but not in the palette: {sorted(missing)}")
        self.assertEqual(cmds | skills, set(names), "the palette lists something the plugin does not ship")
        for r in rows:
            self.assertEqual("/mokata:" + r["name"], r["slash"])
            self.assertEqual("command" if r["name"] in cmds else "skill", r["kind"])
        gated = {r["name"] for r in rows if r["gated"]}
        self.assertIn("spec", gated, "spec stops at a human gate; the palette must say so")

    def test_a_new_skill_file_appears_with_no_code_edit(self):
        from mokata import package_data_root
        from mokata.ide.features.palette import slash_commands
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            shutil.copytree(package_data_root() / "templates" / "commands", root / "templates" / "commands")
            shutil.copytree(package_data_root() / "skills", root / "skills")
            before = {r["name"] for r in slash_commands(root)}
            (root / "skills" / "zz-new").mkdir()
            (root / "skills" / "zz-new" / "SKILL.md").write_text(
                "---\nname: zz-new\ndescription: mokata · A brand-new skill.\n---\n\n# zz\n\n## Gate\n",
                encoding="utf-8")
            rows = {r["name"]: r for r in slash_commands(root)}
            self.assertNotIn("zz-new", before)
            self.assertIn("zz-new", rows)
            self.assertEqual(("/mokata:zz-new", "A brand-new skill.", True, "skill"),
                             (rows["zz-new"]["slash"], rows["zz-new"]["description"],
                              rows["zz-new"]["gated"], rows["zz-new"]["kind"]))


class TheRoutes(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._d = tempfile.TemporaryDirectory()
        cls.root = cls._d.name
        fixture(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls._d.cleanup()

    def test_the_command_list_route_is_paired_and_says_it_copies(self):
        from mokata.ide.features.palette import slash_commands
        c, _app, _st = client(self.root)
        r = c.get("/api/palette/commands")
        self.assertEqual(200, r.status_code)
        body = r.json()
        self.assertEqual(slash_commands(), body["commands"])
        self.assertEqual(len(body["commands"]), body["count"])
        self.assertIsNone(body["joined"])
        self.assertIn("never sends", body["how"])
        c.cookies.clear()
        self.assertIn(c.get("/api/palette/commands").status_code, (401, 403))

    def test_the_sample_lists_the_same_commands(self):
        c, _app, _st = client(self.root, demo=True)
        r = c.get("/api/palette/commands")
        self.assertEqual(200, r.status_code)
        self.assertGreaterEqual(r.json()["count"], 30)

    def test_workspace_symbols_rank_the_typed_definition_first_and_return_no_files(self):
        c, _app, _st = client(self.root)
        deadline = time.monotonic() + 30
        while True:
            r = c.post("/api/quickopen", json={"q": "@total", "changed": []}, headers=ORIGIN)
            self.assertEqual(200, r.status_code)
            body = r.json()
            if body.get("symbols_state") != "building" or time.monotonic() > deadline:
                break
            time.sleep(0.3)
        self.assertEqual([], body["files"], "`#` (symbols-only) returned files")
        self.assertEqual([], body["changed"])
        self.assertTrue(body["symbols"], body)
        top = body["symbols"][0]
        self.assertEqual(("total", "src/app/core.py"), (top["name"], top["path"]))
        self.assertTrue(top["basis"])


def _node(js: str):
    p = subprocess.run(_support.node_argv(js + "\nprocess.stdout.write(JSON.stringify(out));"),
                       capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL,
                       encoding="utf-8")
    if p.returncode:
        raise AssertionError(p.stderr)
    return json.loads(p.stdout)


@unittest.skipUnless(shutil.which("node"), "node is not on PATH — the page logic runs in a browser")
class ThePageLogic(unittest.TestCase):

    def test_the_prefix_grammar_and_the_ranking(self):
        js = "globalThis.window = globalThis; globalThis.MK = {};\n" + \
             (FEATURES / "palette.js").read_text(encoding="utf-8") + r"""
const P = MK.palette, out = {};
out.parse = ["", "core", "@par", "#tot", ":42", ">git", "/mokata:sp", "/sp"].map((q) => P.parse(q));
out.order = [P.score("spec", "spec"), P.score("spec", "spec-lite"), P.score("spec", "my spec"),
             P.score("spec", "sapec"), P.score("spec", "nope")];
const ide = [{ id: "find.open", title: "Find in this view…", keys: ["Mod+F"], feature: "find" },
             { id: "palette.line", title: "Go to line…", keys: ["Mod+G"], feature: "palette" }];
const slash = [{ name: "refine", slash: "/mokata:refine", description: "Tighten a spec", kind: "command" },
               { name: "spec", slash: "/mokata:spec", description: "Write the spec", kind: "skill" },
               { name: "spec", slash: "/mokata:spec", description: "Write the spec", kind: "command" },
               { name: "git", slash: "/mokata:git", description: "Branches", kind: "skill" }];
const label = (r) => r.type + ":" + (r.c.title || r.c.name) + (r.c.kind ? ":" + r.c.kind : "");
out.spec = P.rankCommands("spec", ide, slash).map(label);
out.find = P.rankCommands("find", ide, slash).map(label);
out.all = P.rankCommands("", ide, slash).length;
out.line = [P.lineTarget("42", 0), P.lineTarget("900", 120), P.lineTarget("0", 10), P.lineTarget("x", 10)];
out.keys = [P.keyLabel("Mod+Shift+P", false), P.keyLabel("Mod+Shift+P", true), P.keyLabel("F3", false)];
"""
        o = _node(js)
        self.assertEqual(
            [["files", ""], ["files", "core"], ["symbol", "par"], ["workspace", "tot"],
             ["line", "42"], ["command", "git"], ["slash", "sp"], ["slash", "sp"]],
            [[p["mode"], p["needle"]] for p in o["parse"]])
        a, b, c, d, e = o["order"]
        self.assertIsNone(e)
        self.assertTrue(a > b > c > d, o["order"])
        self.assertEqual(["slash:spec:command", "slash:spec:skill", "slash:refine:command"], o["spec"],
                         "a name match must beat a description match, a command a skill")
        self.assertEqual(["ide:Find in this view…"], o["find"])
        self.assertEqual(6, o["all"], "an empty `>` lists everything")
        self.assertEqual([42, 120, 1, None], o["line"])
        self.assertEqual(["Ctrl+Shift+P", "⌘⇧P", "F3"], o["keys"])

    def test_every_feature_registers_with_no_conflict_and_the_keys_have_one_owner(self):
        """Loads the registry and EVERY static/features/*.js in the page's order. A key two
        features want — including a later stage's — fails here, naming both."""
        js = r"""
globalThis.window = globalThis;
globalThis.document = { body: {}, addEventListener() {}, querySelector: () => null,
                        getElementById: () => null };
""" + (STATIC / "registry.js").read_text(encoding="utf-8") + "\n"
        # P34: app.js owns the core SCREENS (the page loads it before the features).
        js += re.search(r"const SCREENS = \[.*?\];", (STATIC / "app.js").read_text(encoding="utf-8"),
                        re.S).group(0) + "\nMK.SCREENS = SCREENS;\n"
        for name in sorted(n for n in os.listdir(FEATURES) if n.endswith(".js")):
            js += (FEATURES / name).read_text(encoding="utf-8") + "\n"
        js += r"""
const out = { conflicts: mokataIDE.conflicts(), features: mokataIDE.features(),
              keys: Object.fromEntries(mokataIDE.bindings().map((b) => [b.combo, b.command])),
              listed: mokataIDE.commands().filter((c) => c.keys.length).map((c) => c.id) };
"""
        o = _node(js)
        self.assertEqual([], o["conflicts"])
        for f in ("core", "palette", "find"):
            self.assertIn(f, o["features"])
        want = {"Mod+P": "quickopen.open", "Mod+K": "palette.open", "Mod+Shift+P": "palette.commands",
                "Mod+G": "palette.line", "Mod+F": "find.open", "F3": "find.next", "Shift+F3": "find.prev"}
        for combo, cmd in want.items():
            self.assertEqual(cmd, o["keys"].get(combo), combo)
            self.assertIn(cmd, o["listed"], f"{cmd}'s shortcut is not shown in the command list")

    def test_find_is_literal_by_default_and_regex_on_request(self):
        js = "globalThis.window = globalThis; globalThis.MK = {};\n" + \
             (FEATURES / "find.js").read_text(encoding="utf-8") + r"""
const F = MK.find.findSpans, out = {};
const t = "AC-1: Parse a.b — parse(a.b) PARSE";
out.ci = F(t, "parse");
out.cs = F(t, "parse", { caseSensitive: true });
out.lit = F(t, "a.b");
out.dot = F("axb a.b", "a.b");
out.re = F("axb a.b", "a.b", { regex: true });
out.ac = F("AC-1 AC-22 ac-3", "AC-\\d+", { regex: true, caseSensitive: true });
out.empty = F("abc", "x*", { regex: true });
out.none = F("abc", "");
try { F("abc", "(", { regex: true }); out.bad = "no throw"; } catch (e) { out.bad = "threw"; }
"""
        o = _node(js)
        self.assertEqual([[6, 11], [18, 23], [29, 34]], o["ci"])
        self.assertEqual([[18, 23]], o["cs"])
        self.assertEqual([[12, 15], [24, 27]], o["lit"])
        self.assertEqual([[4, 7]], o["dot"], "a literal find treated `.` as a pattern")
        self.assertEqual([[0, 3], [4, 7]], o["re"])
        self.assertEqual([[0, 4], [5, 10]], o["ac"])
        self.assertEqual([], o["empty"])
        self.assertEqual([], o["none"])
        self.assertEqual("threw", o["bad"])


class OnePaletteOwnsTheKeys(unittest.TestCase):

    def test_app_js_no_longer_carries_its_own_quick_open(self):
        app = (STATIC / "app.js").read_text(encoding="utf-8")
        for gone in ("function openQuick", "function runQuick", "qo.open", 'q.startsWith("#")'):
            self.assertNotIn(gone, app, f"app.js still has {gone!r} — two palettes")
        pal = (FEATURES / "palette.js").read_text(encoding="utf-8")
        self.assertIn("MK.openQuick = function openQuick", pal)
        self.assertIn('"/api/palette/commands"', pal)
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        for ident in ('id="qo-prefixes"', 'id="qo-hint"', 'id="qo-label"'):
            self.assertIn(ident, html)


if __name__ == "__main__":
    unittest.main()
