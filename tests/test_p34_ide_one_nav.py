"""0.0.26 P34 `IA-ONE-NAV` — ONE information architecture for the IDE (PRD §5, rows R26-R28,
R30-R33; gaps G10, G20, G22, G25).

What must be true for the user:
  R26  the rail IS the screen: for every rail button and every `g x` chord, the highlighted rail
       button is the screen that is visible; there is no mode bar, no left Sessions panel and no
       bottom timeline (three nav systems that desynced, B-F1/G10).
  R27  each screen's own visible controls stay within PRD §5.1 (top bar <= 5).
  R28  ONE "Viewing" context: pick a session -> the Code view says "vs session X", the server
       marks the file against it, and Search's "this session's files" scope is on.
  R30  the registry REFUSES browser-reserved chords (Ctrl/Cmd+W closed the IDE's window, B-F16).
  R31  tabs are unique by (path, kind) — one tab per file with a Δ toggle — restore included.
  R32  at 900 px the top bar does not wrap and every overlay stays inside the viewport.
  R33  every region shows content, a skeleton or why it is empty/failed (with Retry) — a 503 or a
       slow answer never leaves a region blank.
  G22  no TTY -> the Terminal button is hidden and the reason is shown in its place.

The browser rows run the REAL page in Chromium (Playwright) on a small fixture repo and are
skipped, saying why, when Playwright or its browser is absent. The rest run under node / Python.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

import _support  # noqa: F401  (puts src/ on the path)

from mokata.ide import workspace as W

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "mokata" / "ide" / "static"
FEATURES = STATIC / "features"
SRC = str(ROOT / "src")


def _node(js):
    p = subprocess.run([shutil.which("node"), "-e", js], capture_output=True, text=True, timeout=60,
                       stdin=subprocess.DEVNULL, encoding="utf-8")
    if p.returncode != 0:
        raise AssertionError(p.stderr)
    return json.loads(p.stdout)


def _screens_js():
    return re.search(r"const SCREENS = \[.*?\];", (STATIC / "app.js").read_text(encoding="utf-8"),
                     re.S).group(0) + "\nMK.SCREENS = SCREENS;\n"


def _all_features_js():
    js = r"""
globalThis.window = globalThis;
globalThis.document = { body: {}, addEventListener() {}, querySelector: () => null, getElementById: () => null };
""" + (STATIC / "registry.js").read_text(encoding="utf-8") + "\n" + _screens_js()
    for name in sorted(n for n in os.listdir(FEATURES) if n.endswith(".js")):
        js += (FEATURES / name).read_text(encoding="utf-8") + "\n"
    return js


# ===================================================================== R26 — the page's structure

@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class R26_OneNavigation(unittest.TestCase):
    html = (STATIC / "index.html").read_text(encoding="utf-8")

    def test_R26_no_mode_bar_no_left_sessions_panel_no_bottom_timeline(self):
        for gone in ('class="modes"', 'class="mode"', "data-mode=", 'id="panel-sessions"',
                     'class="timeline-strip"', 'id="ticks"', 'id="timeline"', 'id="src-change"'):
            self.assertNotIn(gone, self.html, f"{gone} is a second navigation system")
        app = (STATIC / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("setPanel", app)
        for js in [STATIC / n for n in os.listdir(STATIC) if n.endswith(".js")] + list(FEATURES.glob("*.js")):
            self.assertNotIn("MK.setPanel", js.read_text(encoding="utf-8"), js.name)

    def test_R26_every_core_screen_is_one_rail_button_and_one_view_and_the_chords_match_the_map(self):
        o = _node("globalThis.MK = {};\n" + _screens_js() + "process.stdout.write(JSON.stringify(MK.SCREENS));")
        ids = [s["id"] for s in o]
        self.assertEqual(["sessions", "review", "navigate", "search", "graph", "plan"], ids)
        for sid in ids:
            self.assertEqual(1, self.html.count(f'data-screen="{sid}"'), sid)
            self.assertIn(f'id="view-{sid}"', self.html)
        # §5.5: g s / g r / g c / g i / g p (+ g w from the Worktrees feature)
        self.assertEqual({"s": "sessions", "r": "review", "c": "navigate", "i": "graph", "p": "plan"},
                         {s["chord"]: s["id"] for s in o if s["chord"]})
        wt = (FEATURES / "worktrees.js").read_text(encoding="utf-8")
        self.assertIn('chord: "w"', wt)

    def test_R26_a_feature_screen_is_a_rail_button_plus_a_view_and_its_chord_cannot_collide(self):
        o = _node(r"""
globalThis.window = globalThis;
const made = []; const fake = (tag) => { const n = { tag, dataset: {}, style: {}, children: [], hidden: false,
  setAttribute() {}, append(...k) { n.children.push(...k); }, addEventListener() {} }; made.push(n); return n; };
const rail = fake("nav"), left = fake("aside"), center = fake("main");
globalThis.document = { addEventListener() {}, createElement: fake,
  querySelector: (s) => (s === "nav.rail" ? rail : s === "main.center" ? center : null),
  getElementById: (id) => (id === "left" ? left : null) };
""" + (STATIC / "registry.js").read_text(encoding="utf-8") + "\n" + _screens_js() + r"""
const out = {};
mokataIDE.register({ id: "demo", panels: [{ id: "demo", title: "Demo", chord: "d", side: true, mount() {} }] });
out.rail = rail.children.map((b) => b.dataset.screen);
out.view = center.children.map((v) => v.id);
out.side = left.children.map((v) => v.id);
out.screen = MK.SCREENS.find((s) => s.id === "demo");
try { mokataIDE.register({ id: "clash", panels: [{ id: "clash", title: "C", chord: "s" }] }); out.clash = "accepted"; }
catch (e) { out.clash = e.message; }
process.stdout.write(JSON.stringify(out));
""")
        self.assertEqual(["demo"], o["rail"])
        self.assertEqual(["view-demo"], o["view"])
        self.assertEqual(["panel-demo"], o["side"])
        self.assertEqual("d", o["screen"]["chord"])
        self.assertIn("chord g s is taken: by sessions", o["clash"])


# ===================================================================== R30 — browser-reserved keys

@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class R30_BrowserReservedKeys(unittest.TestCase):

    def test_R30_the_registry_rejects_mod_w_mod_tab_and_mod_shift_t_and_takes_alt_twins(self):
        o = _node(r"""
globalThis.window = globalThis;
globalThis.document = { addEventListener() {} };
""" + (STATIC / "registry.js").read_text(encoding="utf-8") + r"""
const out = {};
for (const k of ["Mod+W", "Ctrl+W", "Cmd+W", "Mod+Tab", "Ctrl+Tab", "Mod+Shift+T", "Cmd+Shift+T"]) {
  try { mokataIDE.register({ id: "f" + Math.random(), commands: [{ id: "c" + Math.random(), title: "x", run() {}, keys: k }] });
        out[k] = "accepted"; } catch (e) { out[k] = e.message; }
}
mokataIDE.register({ id: "alt", commands: [{ id: "alt.close", title: "x", run() {}, keys: "Alt+W" }] });
out.alt = mokataIDE.keys.owner("Alt+W");
process.stdout.write(JSON.stringify(out));
""")
        for k in ("Mod+W", "Ctrl+W", "Cmd+W", "Mod+Tab", "Ctrl+Tab", "Mod+Shift+T", "Cmd+Shift+T"):
            self.assertIn("is the browser's", o[k], k)
        self.assertEqual("alt (alt.close)", o["alt"])

    def test_R30_every_shipped_feature_registers_and_none_binds_a_browser_key(self):
        o = _node(_all_features_js() + r"""
process.stdout.write(JSON.stringify({ conflicts: mokataIDE.conflicts(),
  combos: mokataIDE.bindings().map((b) => b.combo), reserved: Object.keys(mokataIDE.browserReserved()) }));
""")
        self.assertEqual([], o["conflicts"])
        self.assertEqual([], [c for c in o["combos"] if c in o["reserved"]])
        self.assertIn("Mod+Shift+F", o["combos"], "⌘⇧F opens Search (§5.5)")


# ===================================================================== R31 — one tab per file

class R31_TabsAreUnique(unittest.TestCase):

    def test_R31_the_server_keeps_one_tab_per_path_whatever_view_and_a_schema_1_file_is_refused(self):
        clean, dropped = W.normalize({"tabs": [
            {"kind": "file", "path": "a.py", "view": "code"},
            {"kind": "file", "path": "a.py", "view": "diff"},
            {"kind": "file", "path": "./a.py"},
            {"kind": "diff", "source": "live", "path": "a.py"},     # schema 1's separate diff tab
            {"kind": "file", "path": "b.py", "view": "diff"}]})
        self.assertEqual(["file:a.py", "file:b.py"], [t["key"] for t in clean["tabs"]])
        self.assertEqual(["code", "diff"], [t["view"] for t in clean["tabs"]])
        self.assertEqual(3, dropped)
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".mokata"))       # governed: the state stays in the repo
            path = W.workspace_path(d)
            self.assertTrue(path.startswith(os.path.realpath(d)) or path.startswith(d), path)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                json.dump({"schema": 1, "tabs": [{"kind": "diff", "path": "a.py"}]}, fh)
            doc, refused = W.load(d)
            self.assertEqual([], doc["tabs"])
            self.assertIn("schema 1", refused or "")

    @unittest.skipUnless(shutil.which("node"), "node is not on PATH")
    def test_R31_the_page_model_merges_a_restore_without_duplicates_and_the_delta_toggles_one_tab(self):
        tabs = (FEATURES / "tabs.js").read_text(encoding="utf-8")
        o = _node("globalThis.window = globalThis;\n" + tabs + r"""
const M = MK.tabsModel, out = {};
let m = M.open(M.empty(), { kind: "file", path: "a.py", view: "diff" }, { preview: true });
const r = M.merge(m, { tabs: [{ kind: "file", path: "a.py" }, { kind: "file", path: "b.py" },
                              { kind: "file", path: "b.py", view: "diff" }], active: "file:b.py", closed: [] });
out.keys = r.tabs.map((t) => t.key);
let n = M.open(M.empty(), { kind: "file", path: "c.py", view: "code" });
n = M.open(n, { kind: "file", path: "c.py", view: "diff" });
out.one = [n.tabs.length, n.tabs[0].view];
out.flip = M.toggleView(n, "file:c.py").tabs[0].view;
process.stdout.write(JSON.stringify(out));
""")
        self.assertEqual(["file:a.py", "file:b.py"], sorted(o["keys"]))
        self.assertEqual([1, "diff"], o["one"], "opening a file's change re-uses the file's tab")
        self.assertEqual("code", o["flip"])


# ===================================================================== the browser rows

def _why_no_browser():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return "Playwright is not installed in this interpreter"
    if not shutil.which("git"):
        return "git is not on PATH"
    try:
        with sync_playwright() as pw:
            pw.chromium.launch().close()
    except Exception as exc:                          # noqa: BLE001 — any launch failure skips
        return f"Chromium for Playwright cannot start here: {str(exc).splitlines()[0][:120]}"
    return ""


SKIP = _why_no_browser()

# The selector Audit B's walk.py counts with (§5.1), minus list rows (a tree / session / file row
# is content, not a control — PRD §5.1 "tree rows are not buttons for counting").
COUNT = r"""(scope) => [...document.querySelectorAll('button,a,input,select,[role=tab]')].filter((e) => {
  const r = e.getBoundingClientRect();
  if (!(r.width > 0 && r.height > 0) || getComputedStyle(e).visibility === 'hidden') return false;
  if (e.closest('[data-rows]')) return false;
  const inTop = !!e.closest('header.top'), inRail = !!e.closest('nav.rail');
  return scope === 'top' ? inTop : (!inTop && !inRail && !e.closest('#activity-drawer'));
}).length"""
TARGET = {"top": 5, "review": 25, "navigate": 30, "graph": 15, "plan": 20, "sessions": 20,
          "search": 25, "worktrees": 20}


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, stdin=subprocess.DEVNULL)


def _fixture(d):
    _git(d, "init", "-q", "-b", "main")
    _git(d, "config", "user.email", "t@t")
    _git(d, "config", "user.name", "t")
    os.makedirs(os.path.join(d, "pkg"))
    with open(os.path.join(d, "pkg", "core.py"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write('def slugify(s):\n    return s.lower()\n\n\ndef caller():\n    return slugify("A")\n')
    with open(os.path.join(d, "README.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("# fx\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-qm", "init")
    with open(os.path.join(d, "pkg", "core.py"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write('def slugify(s):\n    return s.strip().lower()\n\n\ndef caller():\n    return slugify("A")\n')


@unittest.skipIf(SKIP, SKIP)
class InTheBrowser(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls._t = tempfile.TemporaryDirectory()
        base = os.path.realpath(cls._t.name)
        cls.repo = os.path.join(base, "repo")
        os.makedirs(cls.repo)
        _fixture(cls.repo)
        env = dict(os.environ, PYTHONPATH=SRC, HOME=os.path.join(base, "home"),
                   USERPROFILE=os.path.join(base, "home"))
        os.makedirs(env["HOME"])
        cls.port = 26000 + os.getpid() % 2000
        cls.proc = subprocess.Popen([sys.executable, "-m", "mokata.cli", "ide", "--path", cls.repo,
                                     "--no-browser", "--foreground", "--port", str(cls.port)],
                                    cwd=cls.repo, env=env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        url, t0 = None, time.time()
        while time.time() - t0 < 90 and url is None:
            line = cls.proc.stdout.readline()
            if not line and cls.proc.poll() is not None:
                break
            m = re.search(r"(http://127\.0\.0\.1:%d\S*)" % cls.port, line or "")
            url = m.group(1) if m else None
        cls.started = url is not None
        if not cls.started:
            cls.proc.kill()
            return
        threading.Thread(target=lambda: [None for _ in cls.proc.stdout], daemon=True).start()
        cls.url = url
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    def setUp(self):
        if not self.started:
            self.skipTest("the IDE server did not start")

    @classmethod
    def tearDownClass(cls):
        if not cls.started:
            cls._t.cleanup()
            return
        cls.browser.close()
        cls.pw.stop()
        cls.proc.terminate()
        try:
            cls.proc.wait(10)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
        cls._t.cleanup()

    def page(self, width=1440, height=900, routes=None):
        ctx = self.browser.new_context(viewport={"width": width, "height": height})
        self.addCleanup(ctx.close)
        for pattern, handler in (routes or {}).items():
            ctx.route(pattern, handler)
        pg = ctx.new_page()
        self.errors = []
        pg.on("pageerror", lambda e: self.errors.append(str(e)))
        pg.goto(self.url)
        pg.wait_for_function("() => window.MK && MK.ui && MK.ui.state && document.body.dataset.mode")
        pg.wait_for_timeout(600)
        return pg

    def state(self, pg):
        return pg.evaluate("""() => ({
          active: [...document.querySelectorAll('nav.rail .rail-btn.active')].map((b) => b.dataset.screen),
          visible: [...document.querySelectorAll('main.center > .view-pane')].filter((v) => !v.hidden).map((v) => v.id),
          modes: document.querySelectorAll('.modes, button.mode').length })""")

    def test_R26_the_rail_highlight_is_the_visible_screen_for_every_button_and_chord(self):
        pg = self.page()
        screens = pg.evaluate("() => [...document.querySelectorAll('nav.rail .rail-btn')].map((b) => b.dataset.screen)")
        self.assertIn("worktrees", screens, "a feature screen sits on the same rail")
        for sid in screens:
            pg.click(f"nav.rail .rail-btn[data-screen={sid}]")
            st = self.state(pg)
            self.assertEqual(([sid], ["view-" + sid], 0), (st["active"], st["visible"], st["modes"]), sid)
        chords = pg.evaluate("() => MK.SCREENS.filter((s) => s.chord).map((s) => [s.chord, s.id])")
        self.assertEqual({"s", "r", "c", "i", "p", "w"}, {c for c, _ in chords})
        for letter, sid in chords:
            pg.click("nav.rail .rail-btn[data-screen=plan]" if sid != "plan" else "nav.rail .rail-btn[data-screen=review]")
            pg.locator("body").press("g")
            pg.locator("body").press(letter)
            st = self.state(pg)
            self.assertEqual(([sid], ["view-" + sid]), (st["active"], st["visible"]), f"g {letter}")
        # the palette's "Go to:" commands are the same function
        pg.evaluate("() => mokataIDE.run('screen.plan')")
        self.assertEqual((["plan"], ["view-plan"]), tuple(self.state(pg)[k] for k in ("active", "visible")))
        self.assertEqual([], self.errors)

    def test_R27_visible_controls_per_screen_are_within_the_prd_targets(self):
        pg = self.page()
        got = {"top": pg.evaluate(COUNT, "top")}
        for sid in ("sessions", "review", "navigate", "search", "graph", "plan", "worktrees"):
            pg.click(f"nav.rail .rail-btn[data-screen={sid}]")
            pg.wait_for_timeout(400)
            got[sid] = pg.evaluate(COUNT, "screen")
        print("\n[P34] R27 visible controls:", json.dumps(got), file=sys.stderr)
        for k, n in got.items():
            self.assertLessEqual(n, TARGET[k], f"{k}: {n} controls > {TARGET[k]}")

    def test_R28_a_picked_session_drives_the_code_gutter_label_and_the_search_scope(self):
        pg = self.page()
        pg.click("nav.rail .rail-btn[data-screen=search]")
        self.assertTrue(pg.is_disabled("button[data-sscope=session]"), "no session in Viewing: scope off")
        pg.evaluate("() => MK.setFocus('SESSIONR28')")
        pg.wait_for_function("() => /session SESSIONR/.test(document.getElementById('focus-chip').textContent)")
        self.assertFalse(pg.is_disabled("button[data-sscope=session]"), "the session scope follows Viewing")
        pg.evaluate("() => MK.openFile('pkg/core.py', 1, null)")
        pg.wait_for_function("() => document.body.dataset.mode === 'navigate'")
        self.assertIn("vs session SESSIONR", pg.inner_text("#code-viewing"))
        f = pg.evaluate("() => MK.api('/api/file?path=pkg/core.py')")
        self.assertEqual("session SESSIONR", f["changes_against"])
        # Viewing ▾ → Uncommitted changes: every view follows, the server's focus too
        pg.click("#session-chip")
        pg.click("#viewing-pop button.view[data-view=live]")
        pg.wait_for_function("() => /uncommitted/.test(document.getElementById('focus-chip').textContent)")
        self.assertEqual("vs HEAD", pg.inner_text("#code-viewing"))
        self.assertIsNone(pg.evaluate("() => MK.api('/api/state').then((s) => s.focus)"))

    def test_R31_one_tab_per_file_across_code_and_review_and_after_a_reload(self):
        pg = self.page()
        pg.evaluate("() => MK.openFile('pkg/core.py', 1, null)")
        pg.wait_for_timeout(500)
        pg.click("nav.rail .rail-btn[data-screen=review]")
        pg.click("#nav-list button.nav-row")
        pg.wait_for_timeout(800)
        names = pg.evaluate("() => [...document.querySelectorAll('#tabstrip .tab .tab-name')].map((n) => n.textContent)")
        self.assertEqual(1, names.count("core.py"), names)
        self.assertTrue(pg.evaluate("() => document.querySelector('#tabstrip .tab.active .tab-delta').classList.contains('on')"))
        pg.click("#tabstrip .tab.active .tab-delta")                       # Δ → the file in Code
        pg.wait_for_function("() => document.body.dataset.mode === 'navigate'")
        pg.wait_for_timeout(900)                                          # the debounced save
        pg.reload()
        pg.wait_for_function("() => MK.ui && MK.ui.restored")
        names = pg.evaluate("() => [...document.querySelectorAll('#tabstrip .tab .tab-name')].map((n) => n.textContent)")
        self.assertEqual(len(names), len(set(names)), f"duplicate tabs after restore: {names}")

    def test_R32_at_900px_the_top_bar_is_one_line_and_every_overlay_stays_in_the_viewport(self):
        pg = self.page(900, 800)
        top = pg.evaluate("() => { const t = document.querySelector('header.top'); const r = t.getBoundingClientRect();"
                          " return [t.scrollWidth, t.clientWidth, r.height, [...t.children].filter((c) => c.getBoundingClientRect().width > 0)"
                          ".map((c) => { const b = c.getBoundingClientRect(); return b.top + b.height / 2; })]; }")
        self.assertLessEqual(top[0], top[1], "the top bar overflows")
        self.assertLessEqual(top[2], 48)
        self.assertLessEqual(max(top[3]) - min(top[3]), 4, "the top bar wrapped (items on different lines)")
        # G25: under 1000 px the top bar collapses to ⌘K + Activity (+ the Viewing dot and Health)
        self.assertFalse(pg.is_visible(".qo-ph") or pg.is_visible("#repo"), "the top bar did not collapse")
        self.assertTrue(pg.is_visible("#qo-open") and pg.is_visible("#activity-chip"))
        inside = "(sel) => { const r = document.querySelector(sel).getBoundingClientRect();" \
                 " return r.width > 0 && r.left >= 0 && r.top >= 0 && r.right <= innerWidth && r.bottom <= innerHeight; }"
        opens = [("#session-chip", "#viewing-pop"), ("#activity-chip", "#activity-drawer"),
                 ("#health-chip", "#hp .qo-dialog")]
        for btn, box in opens:
            pg.click(btn)
            pg.wait_for_timeout(300)
            self.assertTrue(pg.evaluate(inside, box), f"{box} leaves the viewport at 900 px")
            pg.keyboard.press("Escape")
            pg.mouse.click(450, 790)
        pg.keyboard.press("?")
        self.assertTrue(pg.evaluate(inside, "#keys-help .qo-dialog"))
        pg.keyboard.press("Escape")
        pg.keyboard.press("Control+k")
        pg.wait_for_timeout(200)
        self.assertTrue(pg.evaluate(inside, "#qo .qo-dialog"))

    def test_R33_a_503_a_slow_answer_and_an_empty_one_each_say_so_in_their_region(self):
        def fail(route):
            route.fulfill(status=503, body="Service Unavailable", content_type="text/plain")
        pg = self.page(routes={"**/api/live*": fail, "**/api/sessions/list*": fail})
        pg.click("nav.rail .rail-btn[data-screen=review]")
        pg.wait_for_selector("#diff .region-msg.error")
        txt = pg.inner_text("#diff")
        self.assertIn("503", txt)
        self.assertIn("Retry", txt)
        self.assertIn("git diff HEAD", txt, "the CLI twin")
        self.assertNotIn("Service Unavailable", txt, "a raw 503 page is never what a region shows")
        pg.click("nav.rail .rail-btn[data-screen=sessions]")
        pg.wait_for_selector("#s1-rows .region-msg.error")
        self.assertIn("Retry", pg.inner_text("#s1-rows"))

        held, hold = [], {"on": False}

        def slow(route):                      # held — answered only after the region was read
            if hold["on"]:
                held.append(route)
            else:
                route.continue_()
        pg2 = self.page(routes={"**/api/live*": slow})
        pg2.click("nav.rail .rail-btn[data-screen=review]")
        hold["on"] = True
        pg2.evaluate("() => { MK.ui.files = []; MK.refresh(); }")
        pg2.wait_for_timeout(700)
        self.assertIn("Reading uncommitted changes", pg2.inner_text("#diff"),
                      "a read slower than 300 ms shows a skeleton in its own region")
        hold["on"] = False
        for r in held:
            r.continue_()
        # empty: a clean working tree says why and what next, never a blank pane
        pg3 = self.page(routes={"**/api/live*": lambda r: r.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"sample": False, "title": "Live", "files": [], "added": 0, "removed": 0}))})
        pg3.click("nav.rail .rail-btn[data-screen=review]")
        self.assertTrue(pg3.is_visible("#diff-empty"))
        self.assertIn("No changed file", pg3.inner_text("#nav-empty"))

    def test_G22_no_tty_hides_the_terminal_button_and_says_why(self):
        pg = self.page()
        pg.click("#activity-chip")
        pg.wait_for_selector("#term-why:not([hidden])")
        self.assertFalse(pg.is_visible("#term-toggle"))
        self.assertIn("Terminal:", pg.inner_text("#term-why"))
        self.assertTrue(pg.is_visible("#follow") and pg.is_visible("#join-btn"),
                        "Follow and Join live in the Activity drawer now")


if __name__ == "__main__":
    unittest.main()
