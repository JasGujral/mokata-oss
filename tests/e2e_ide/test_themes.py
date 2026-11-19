"""IDE e2e — themes (0.0.27 P6, ID-THEMES-FULL), driven the way a person drives them: the palette,
by keyboard. A switch changes every surface at once and costs the person NOTHING: the page is not
reloaded, the open file keeps its line and scroll, and the keyboard stays where it was.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import re
import unittest

import _harness as H

CSS = os.path.join(H.SRC, "mokata", "ide", "static", "themes.css")


def tokens():
    """{theme: {--token: value}} from the one token file the page links."""
    with open(CSS, encoding="utf-8") as fh:
        css = re.sub(r"/\*.*?\*/", "", fh.read(), flags=re.S)
    out = {}
    for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        m = re.search(r'data-theme="([a-z-]+)"', sel)
        if m:
            out[m.group(1)] = dict((k, v.strip()) for k, v in re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", body))
    return out


def rgb(hexv):
    h = hexv.lstrip("#")
    h = "".join(c * 2 for c in h) if len(h) == 3 else h
    return "rgb(%d, %d, %d)" % (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def squash(s):
    return re.sub(r"\s+", "", s or "")


def build(base):
    repo = H.make_repo("shop", base)
    H._write(repo, "docs/arch.md", "# Architecture\n\n```mermaid\ngraph TD\n  a --> b\n```\n")
    H._git(repo, "add", "-A")
    H._git(repo, "commit", "-q", "-m", "arch")
    H._write(repo, "docs/arch.md", "# Architecture\n\n```mermaid\ngraph TD\n  a --> b\n  b --> c\n```\n")
    return repo


class ThemesSwitchLiveAndCostNothing(H.IdeFlow):
    FIXTURE = staticmethod(build)

    def theme(self):
        return self.page.evaluate("document.documentElement.dataset.theme")

    def pick(self, title):
        """The keyboard path: Ctrl+K, type `>` and the title, Enter. Returns once the page is in the new theme."""
        p = self.page
        self.press("Control+K")
        p.wait_for_selector("#qo:not([hidden])")
        p.keyboard.type(">" + title)
        # Enter runs the FIRST row: wait until the debounced search has put the wanted command there
        self.until("(() => { const r = document.querySelector('#qo-results .qo-row');"
                   " return !!(r && r.textContent.includes(%r)); })()" % title, "%s to lead the palette" % title)
        self.press("Enter")
        p.wait_for_selector("#qo", state="hidden")

    def documents_loaded(self):
        base = self.server.url.split("/pair")[0].rstrip("/") + "/"
        return len([r for r in self.requests if r[1].rstrip("/") + "/" == base and r[0] == "GET"])

    def test_1_a_palette_pick_changes_the_page_without_a_reload_and_keeps_state_scroll_and_focus(self):
        """Pick "Theme: Light" from the palette with the keyboard while reading line 1,000 of a
        long file: the page is the same page (no reload), the file is at the same scroll and line,
        and the keyboard is on the element it was on."""
        p = self.page
        p.emulate_media(color_scheme="dark")          # headless Chromium reports a light OS; this person has a dark one
        self.until("document.documentElement.dataset.theme === 'dark'", "the OS's dark theme")
        self.open_file("data/words.txt")
        self.until("document.querySelectorAll('#code div.cl').length > 20", "the file's lines")
        p.evaluate("document.getElementById('code-scroll').scrollTop = 5000")
        self.until("document.getElementById('code-scroll').scrollTop > 4000", "the scroll to hold")
        p.evaluate("window.__keep = 'same page'; const t = document.querySelector('#tree [tabindex=\"0\"]');"
                   " t.dataset.kept = '1'; t.focus();")
        self.until("document.activeElement && document.activeElement.dataset.kept === '1'", "focus on the tree row")
        before = p.evaluate("({scroll: document.getElementById('code-scroll').scrollTop, path: MK.nav.path,"
                            " line: MK.nav.line, tabs: document.querySelectorAll('[role=tab]').length})")
        docs = self.documents_loaded()
        self.assertEqual("dark", self.theme())

        self.pick("Theme: Light")
        self.until("document.documentElement.dataset.theme === 'light'", "the light theme")

        self.assertEqual("same page", p.evaluate("window.__keep"), "the page was reloaded")
        self.assertEqual(docs, self.documents_loaded(), "the document was fetched again")
        after = p.evaluate("({scroll: document.getElementById('code-scroll').scrollTop, path: MK.nav.path,"
                           " line: MK.nav.line, tabs: document.querySelectorAll('[role=tab]').length})")
        self.assertEqual(before, after, "scroll, file, line or tabs moved")
        self.assertTrue(p.evaluate("document.activeElement && document.activeElement.dataset.kept === '1'"),
                        "focus fell off the element it was on (to <body>?)")
        self.assertEqual(rgb(tokens()["light"]["--bg"]), p.evaluate("getComputedStyle(document.body).backgroundColor"))
        self.assertEqual("light", p.evaluate("document.documentElement.dataset.scheme"))
        self.assertEqual("none", p.evaluate("getComputedStyle(document.querySelector('.mark-dark')).display"),
                         "the dark logo stays on a light page")
        self.assertNotEqual("none", p.evaluate("getComputedStyle(document.querySelector('.mark-light')).display"))
        self.text_until("#pal-toast", "Theme: Light", "the confirmation")

    def test_1b_escape_hands_the_keyboard_back_to_where_it_was(self):
        """Open the palette from the explorer and press Escape: focus is on that same row, not <body>."""
        p = self.page
        p.evaluate("const t = document.querySelector('#tree [tabindex=\"0\"]'); t.dataset.kept = '2'; t.focus();")
        self.press("Control+K")
        p.wait_for_selector("#qo:not([hidden])")
        self.press("Escape")
        p.wait_for_selector("#qo", state="hidden")
        self.assertTrue(p.evaluate("document.activeElement && document.activeElement.dataset.kept === '2'"),
                        "Escape dropped focus (to <body>?)")

    def test_2_every_surface_reads_the_theme_in_all_four_themes(self):
        """Body, syntax tokens, the diff's added lines, the focus ring and the graph all take the
        theme's tokens; Mermaid is redrawn in the new colours and returns when the theme does."""
        p = self.page
        toks = tokens()
        try:
            import pygments  # noqa: F401
            highlighted = True
        except ImportError:
            # Only the syntax-highlight assertion needs Pygments (`pip install -e .[ide]`); the
            # body, panel, diff, graph, focus ring and diagram checks below do not, and run anyway.
            highlighted = False
        self.open_file("shop/text.py")
        if highlighted:
            self.until("!!document.querySelector('#code .tk-kw')", "highlighted source")
        # the graph, drawn once (this repo's only change is a doc, so no diff names a symbol to
        # expand from: the graph is opened on the source file's symbol, as Code's Impact does)
        p.evaluate("MK.openGraph('ripple', 'slugify')")
        p.wait_for_selector("#view-graph:not([hidden])")
        p.fill("#g-symbol", "slugify")
        p.click("#g-go")
        cy = "document.getElementById('cy')._cyreg.cy"
        self.until("(() => { const c = document.getElementById('cy'); return !!(c && c._cyreg && c._cyreg.cy"
                   " && c._cyreg.cy.nodes().length > 1); })()", "the graph to draw nodes")
        # a diagram, drawn once (review → docs/arch.md → rendered)
        self.go("review")
        p.wait_for_selector("#nav-list li", state="attached")
        p.locator("#nav-list li", has_text="arch.md").first.click()
        p.wait_for_selector("#diff .rv-md-btn")
        p.click("#diff .rv-md-btn")
        self.until("!!document.querySelector('#diff .mk-new .mk-mermaid img.mk-m-img')", "the diagram")
        seen_svg = {}
        for theme in ("dark", "light", "hc-dark", "hc-light"):
            p.evaluate("MK.theme.set(%r)" % theme)
            self.until("document.documentElement.dataset.theme === %r" % theme, theme)
            t = toks[theme]
            self.assertEqual(rgb(t["--bg"]), p.evaluate("getComputedStyle(document.body).backgroundColor"), theme)
            self.assertEqual(rgb(t["--panel"]), p.evaluate("getComputedStyle(document.querySelector('header.top')).backgroundColor"), theme)
            # syntax highlight (Code view of an open file): needs Pygments, so only this is skipped
            if highlighted:
                self.assertEqual(rgb(t["--tk-kw"]), p.evaluate(
                    "(() => { const n = document.createElement('span'); n.className = 'tk-kw'; document.body.append(n);"
                    " const c = getComputedStyle(n).color; n.remove(); return c; })()"), theme)
            # the diff's added line + intra-line mark (Review)
            self.assertEqual(rgb(t["--add"]), p.evaluate(
                "(() => { const n = document.createElement('table'); n.className = 'd';"
                " n.innerHTML = '<tr class=\"add\"><td class=\"tx\">x</td></tr>'; document.body.append(n);"
                " const c = getComputedStyle(n.querySelector('td.tx')).color; n.remove(); return c; })()"), theme)
            # the graph restyled itself (no redraw by the person)
            self.assertEqual(squash(rgb(t["--text"])), squash(p.evaluate(
                "%s.nodes().filter(n => n.data('coverage') !== 'untested' && n.data('kind') !== 'cluster')[0].style('color')" % cy)), theme)
            # the focus ring: REAL keyboard focus on a top tab — Tab onto the next stop, Shift+Tab
            # back. (0.0.28 D: a lone Shift + focus() was a Chromium assumption — Firefox does not
            # count a modifier as keyboard use, so after the clicks above :focus-visible stayed off.)
            p.evaluate("document.querySelector('#tab-review').focus()")
            self.press("Tab")
            self.press("Shift+Tab")
            self.assertEqual("tab-review", p.evaluate("document.activeElement.id"), theme)
            self.assertEqual(rgb(t["--focus"]), p.evaluate(
                "getComputedStyle(document.querySelector('#tab-review')).outlineColor"), theme)
            # the diagram is drawn in this theme's colours
            if seen_svg:
                self.until_diagram_not(list(seen_svg.values())[-1])
            self.until_diagram(theme)
            seen_svg[theme] = self.mermaid_src()
        self.assertEqual(4, len(set(seen_svg.values())), "each theme draws the diagram differently")
        p.evaluate("MK.theme.set('dark')")
        self.until("document.documentElement.dataset.theme === 'dark'", "dark again")
        self.until_diagram("dark")

    def mermaid_src(self):
        p = self.page
        return p.evaluate("document.querySelector('#diff .mk-new img.mk-m-img').getAttribute('src')")

    def until_diagram_not(self, old):
        for _ in range(200):
            if self.mermaid_src() != old:
                return
            self.page.wait_for_timeout(100)
        self.fail("the diagram was not redrawn for the new theme")

    def until_diagram(self, theme):
        """The diagram on screen carries this theme's text colour (hex or rgb, as Mermaid writes it)."""
        import urllib.parse
        want = tokens()[theme]["--text"].lstrip("#").lower()
        want_rgb = squash(rgb(tokens()[theme]["--text"]))
        for _ in range(200):
            svg = squash(urllib.parse.unquote(self.mermaid_src().split(",", 1)[1]).lower())
            if want in svg or want_rgb in svg:
                return
            self.page.wait_for_timeout(100)
        self.fail("the diagram was not drawn in %s's colours" % theme)

    def os_flips(self, **media):
        """The OS changes a media setting: the media state flips AND the page's media queries get
        their `change` event — what a real OS flip delivers. (0.0.28 D: Playwright's Firefox
        emulation flips `prefers-contrast` with NO change event, measured by lane H; relying on the
        emulator to send it was a Chromium assumption. The event is delivered the same way in every
        browser; an extra one in Chromium is a no-op — the page re-resolves from `matches`.)"""
        self.page.emulate_media(**media)
        self.page.evaluate("(window.__mqls || []).forEach(m => m.dispatchEvent(new Event('change')))")

    def test_3_follow_the_system_switches_live_with_the_os_and_back_off_when_a_theme_is_picked(self):
        p = self.page
        # keep the page's own MediaQueryLists reachable, so an OS flip can reach the page's listeners
        p.add_init_script("(() => { const mm = window.matchMedia.bind(window); window.__mqls = [];"
                          " window.matchMedia = (q) => { const m = mm(q); window.__mqls.push(m); return m; }; })()")
        p.reload()
        self.until("!!(window.MK && MK.theme && window.__mqls && window.__mqls.length >= 2)",
                   "the page's media queries after the reload")
        self.pick("Theme: Follow the system")
        self.until("document.documentElement.dataset.themeChoice === 'system'", "system")
        p.evaluate("window.__keep = 'same page'")
        self.os_flips(color_scheme="dark")
        self.until("document.documentElement.dataset.theme === 'dark'", "dark from the OS")
        self.os_flips(color_scheme="light")
        self.until("document.documentElement.dataset.theme === 'light'", "light from the OS, live")
        self.os_flips(contrast="more")
        self.until("document.documentElement.dataset.theme === 'hc-light'", "high contrast from the OS")
        self.os_flips(color_scheme="dark")
        self.until("document.documentElement.dataset.theme === 'hc-dark'", "high contrast dark")
        self.os_flips(contrast="no-preference")
        self.until("document.documentElement.dataset.theme === 'dark'", "back to dark")
        self.assertEqual("same page", p.evaluate("window.__keep"), "following the OS reloaded the page")
        self.pick("Theme: Light")                          # a pick ignores the OS from now on
        self.until("document.documentElement.dataset.theme === 'light'", "light picked")
        self.os_flips(color_scheme="dark")
        self.assertEqual("light", self.theme(), "the OS flip moved a theme the person picked")
        self.os_flips(color_scheme="light")

    def test_4_the_pick_is_saved_with_the_workspace_and_is_there_after_a_reload(self):
        p = self.page
        self.pick("Theme: High contrast (dark)")
        self.until("document.documentElement.dataset.theme === 'hc-dark'", "hc-dark")
        for _ in range(100):
            if p.evaluate("fetch('/api/workspace').then(r => r.json()).then(w => w.theme)") == "hc-dark":
                break
            p.wait_for_timeout(100)
        else:
            self.fail("the server never held the pick")
        p.reload()
        self.until("document.documentElement.dataset.themeChoice === 'hc-dark'"
                   " && document.documentElement.dataset.theme === 'hc-dark'", "the saved theme after a reload")


PEOPLE_OLD = "id,name,score\n1,Ann,10\n2,Bob,20\n3,Cy,30\n"
PEOPLE_NEW = "id,name,score\n1,Ann,10\n2,Bob,25\n4,Dee,7\n5,Eve,-3\n"
SID, RUN = "e2e-focus-0001", "R-e2e-focus"
TEST = "tests/test_text.py::test_shout"


def build_audit(base):
    """`build` plus a changed CSV (the table view and the table diff)."""
    repo = build(base)
    H._write(repo, "data/people.csv", PEOPLE_OLD)
    H._git(repo, "add", "data/people.csv")
    H._git(repo, "commit", "-q", "-m", "people")
    H._write(repo, "data/people.csv", PEOPLE_NEW)
    return repo


def seed_session(repo):
    """One live session bound to its run (an approved approach, a spec, one RED test run): the
    Sessions card then has a next-step hint to put focus on."""
    import sys
    import time
    if H.SRC not in sys.path:               # the seeding uses THIS checkout's writers
        sys.path.insert(0, H.SRC)
    from mokata import run_intent
    from mokata import session_index as SI
    from mokata.engine.spec import AcceptanceCriterion, Spec
    from mokata.govern.resume import PipelineCheckpoint
    from mokata.state import StateStore
    from mokata.tdd_state import state_dir
    from mokata.test_runs import record_test_run
    store = StateStore(state_dir(repo))
    PipelineCheckpoint(store, RUN).ensure_registered()
    store.write("approved_approach__%s" % RUN, {"topic": "shout", "approach": {"name": "A"},
                                                "approver": "e2e", "approved_at": "2026-10-31"})
    store.write("emitted_spec__%s" % RUN, Spec(title="shout", criteria=[
        AcceptanceCriterion("AC-1", "shout upper-cases")]).to_dict())
    record_test_run(repo, RUN, passed=False, command="pytest", tests=[TEST],
                    ac_map={"AC-1": [TEST]}, at=time.time() - 600)
    SI.on_start(repo, SID, "startup", repo, t=time.time() - 30, proc=None)
    run_intent.bind_registered(repo, SID, RUN)


# 0.0.27 PINS-UI (P6 round 2) + 0.0.29 lane E (PINS-UI-FOCUS-AUDIT-EDGES): what a keyboard user SEES
# on every focusable surface, read from the browser's computed style AND from its pixels (never a
# grep of the CSS text, which missed a ring taken away by an `outline: none` in a BASE rule and a
# ring drawn in currentColor). For every visible element a person can put keyboard focus on, blur
# it, read its style, focus it as the keyboard does, read again. It passes only if focusing it DRAWS
# something a person can see:
#   - an outline with a visible style (the browser's own two-tone ring, `outline-style: auto`,
#     included) and a width >= 1px, or
#   - a box-shadow layer that focus added and that has EXTENT, where extent = blur + spread +
#     |offset| (`inset 0 -2px 0` is a 2px underline; a `0 0 0 0px` layer draws nothing), or
#   - a changed border >= 1px wide,
# and that mark is painted in the theme's --focus colour - the UA's `auto` ring is no exception: its
# computed colour must EQUAL --focus, or it is a ring in whatever colour the browser picked - and,
# composited over what is behind the element (so a transparent or half-alpha layer is graded as what
# reaches the screen), at 3:1 or more. Then a PIXEL PROBE: the element's neighbourhood is
# screenshotted before and after focus, and at least one pixel must differ (a computed ring that
# paints nothing - clipped away, covered, extent zero after rounding - fails here).
#
# "Every focusable element" is two sets: the Tab stops (tabindex >= 0), and the roving items a Tab
# never lands on (`[tabindex="-1"][role=option|treeitem|tab]`), which are reached the way a person
# reaches them - focus the group's one Tab stop, then the arrow keys - never by a script `.focus()`.
# A roving item no arrow key reaches is itself a red. The only element not graded is a column
# resize handle, excluded by its ROLE (`separator`), not by a selector string; the audit lists what it
# excluded so a test can see the exclusion is that and nothing wider.
FA_LIB = r"""
(() => {
  const vis = (e) => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden' && !e.closest('[hidden]');
  const rgba = (s) => { const m = /rgba?\(([\d.]+),\s*([\d.]+),\s*([\d.]+)(?:,\s*([\d.]+))?\)/.exec(s || ''); return m ? [+m[1], +m[2], +m[3], m[4] === undefined ? 1 : +m[4]] : null; };
  const lum = (c) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const behind = (e, outside) => { for (let n = outside ? e.parentElement : e; n && n.nodeType === 1; n = n.parentElement) { const c = rgba(getComputedStyle(n).backgroundColor); if (c && c[3] >= 0.99) return c; } return rgba(getComputedStyle(document.body).backgroundColor); };
  const probe = document.createElement('i'); probe.style.color = 'var(--focus)'; document.body.append(probe);
  const focus = getComputedStyle(probe).color; probe.remove();
  const name = (e) => e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') + (typeof e.className === 'string' && e.className.trim() ? '.' + e.className.trim().split(/\s+/).join('.') : '')
    + (e.getAttribute('role') ? '[role=' + e.getAttribute('role') + ']' : '');
  const snap = (e) => { const s = getComputedStyle(e); return { os: s.outlineStyle, ow: parseFloat(s.outlineWidth) || 0, oc: s.outlineColor, oo: parseFloat(s.outlineOffset) || 0, bs: s.boxShadow,
    bc: [s.borderTopColor, s.borderRightColor, s.borderBottomColor, s.borderLeftColor].join('|'), bw: parseFloat(s.borderTopWidth) || 0 }; };
  // computed box-shadow: "rgb(..) X Y BLUR SPREAD [inset], ..." -> [{c, key, extent}] (one per layer);
  // extent = blur + spread + |offset| (the larger of |x| and |y|), never below zero
  const layers = (bs) => { const out = []; if (!bs || bs === 'none') return out;
    const re = /(rgba?\([^)]*\))\s+([^,]*?)(?=,\s*rgba?\(|$)/g; let m;
    while ((m = re.exec(bs))) { const n = (m[2].match(/-?[\d.]+px/g) || []).map(parseFloat);
      const [ox = 0, oy = 0, blur = 0, spread = 0] = n;
      out.push({ c: m[1], key: m[0].trim(), inset: /\binset\b/.test(m[0]), extent: Math.max(0, blur + spread + Math.max(Math.abs(ox), Math.abs(oy))) }); }
    return out; };
  // what reaches the screen: the colour composited over the background behind the element
  const over = (c, bg) => { const k = rgba(c); if (!k) return null; const a = k[3];
    return [0, 1, 2].map((i) => k[i] * a + bg[i] * (1 - a)).concat(1); };
  const shows = (c, e, outside) => { const k = rgba(c), bg = behind(e, outside); return !!k && k[3] > 0 && c === focus && ratio(over(c, bg), bg) >= 3; };
  const blurActive = () => { if (document.activeElement && document.activeElement !== document.body) document.activeElement.blur(); };
  const lib = { focus, cands: [], before: [], stopEl: null, locs: [] };
  const done = window.__faDone || (window.__faDone = new WeakSet());
  // where to find an element again after the page re-draws it (live updates): its id, else the
  // n-th element with its tag and classes
  const sel = (e) => (e.id ? '#' + CSS.escape(e.id) : e.tagName.toLowerCase() + (typeof e.className === 'string' ? e.className.trim().split(/\s+/).filter(Boolean).map((c) => '.' + CSS.escape(c)).join('') : ''));
  const loc = (e) => { const q = sel(e); return { q, n: [...document.querySelectorAll(q)].indexOf(e) }; };
  const again = (l) => document.querySelectorAll(l.q)[l.n] || null;
  const el = (i) => { let e = lib.cands[i]; if (!e.isConnected) { const f = again(lib.locs[i]); if (f) { lib.cands[i] = f; e = f; } } return e; };
  lib.enumerate = (wanted) => {
    // an open modal dialog is the whole keyboard world: nothing behind it is reachable, so nothing
    // behind it is graded (a ring under an overlay is not a ring a person is meant to see)
    const modals = [...document.querySelectorAll('[aria-modal=true]')].filter(vis);
    const root = modals.length ? modals[modals.length - 1] : document;
    const stops = [...root.querySelectorAll('a[href],button,input,select,textarea,[tabindex]')].filter((e) => !e.disabled && e.tabIndex >= 0 && vis(e));
    const roving = [...root.querySelectorAll('[tabindex="-1"][role=option],[tabindex="-1"][role=treeitem],[tabindex="-1"][role=tab]')].filter((e) => !e.disabled && vis(e));
    const excluded = stops.filter((e) => e.getAttribute('role') === 'separator');
    const graded = stops.filter((e) => !excluded.includes(e));
    lib.cands = graded.concat(roving);
    lib.locs = lib.cands.map(loc);
    lib.before = [];
    return { stops: graded.length, roving: roving.length, excluded: excluded.map(name), modal: modals.length ? name(root) : '',
             list: lib.cands.map((e, i) => ({ i, name: name(e), roving: i >= graded.length, tab: e.getAttribute('role') === 'tab',
                                             hits: (wanted || []).filter((q) => e.matches(q)), done: done.has(e) })) };
  };
  lib.prep = (i) => { const e = el(i); if (!e.isConnected) return { stale: true }; blurActive(); e.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    lib.before[i] = snap(e); const r = e.getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; };
  lib.rect = (i) => { const r = el(i).getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; };
  // the group's one Tab stop, focused as the keyboard does (never the roving item itself)
  lib.stop = (i) => { const e = el(i);
    const g = e.closest('[role=listbox],[role=tree],[role=tablist],[role=menu],[role=group],[role=toolbar]') || e.parentElement;
    const s = [...g.querySelectorAll('[tabindex="0"]')].find((x) => vis(x)) || (g.tabIndex >= 0 ? g : null);
    if (!s) return { found: false, group: name(g) };
    if (!lib.stopEl) { lib.stopEl = s; lib.stopLoc = loc(s); }     // the FIRST stop: later calls land on a tab an arrow key activated
    s.focus({ focusVisible: true, preventScroll: true });
    return { found: document.activeElement === s, group: name(g), stop: name(s), axis: g.getAttribute('role') === 'tablist' || g.getAttribute('aria-orientation') === 'horizontal' ? 'x' : 'y',
             size: g.querySelectorAll('[role=option],[role=treeitem],[role=tab]').length }; };
  // arrow keys on a tablist ACTIVATE the tab they land on (the screen changes under the audit):
  // go back to the tab the group started on, by a click, so the next item is graded on the same screen
  lib.restore = () => { let s = lib.stopEl; const l = lib.stopLoc; lib.stopEl = null; if (s && !s.isConnected && l) s = again(l); if (s && s.isConnected) s.click(); return !!s; };
  lib.active = (i) => document.activeElement === el(i);
  // what is painted at the element's centre (a ring that paints nothing is usually under something)
  lib.cover = (i) => { const e = el(i), r = e.getBoundingClientRect(), t = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
    const anc = []; for (let n = e.parentElement; n && anc.length < 4; n = n.parentElement) { const o = getComputedStyle(n).overflow; anc.push(name(n) + (o !== 'visible' ? '{overflow:' + o + '}' : '')); }
    return 'box ' + [r.x, r.y, r.width, r.height].map(Math.round).join(',') + '; at its centre: ' + (t ? (t === e || e.contains(t) ? 'itself' : name(t)) : 'nothing') + '; inside ' + anc.join(' < '); };
  lib.focusIt = (i) => { const e = el(i); e.focus({ focusVisible: true, preventScroll: true }); return { focused: document.activeElement === e }; };
  lib.verdict = (i) => { const e = el(i), a = lib.before[i], b = snap(e), ways = [];
    if (b.os !== 'none' && b.os !== 'hidden' && b.ow >= 1) ways.push(['outline:' + b.os, b.oc, b.oo >= 0]);
    const was = new Set(layers(a.bs).map((l) => l.key));
    for (const l of layers(b.bs)) if (!was.has(l.key) && l.extent >= 1) ways.push(['shadow', l.c, !l.inset]);
    if (b.bc !== a.bc && b.bw >= 1) for (const c of b.bc.split('|')) ways.push(['border', c, false]);
    const ok = ways.some((w) => shows(w[1], e, w[2]));
    return { ok, why: name(e) + ' ' + JSON.stringify(ways) + ' on ' + JSON.stringify(behind(e, true)) + '/' + JSON.stringify(behind(e, false)) + ' (outside/inside; --focus is ' + focus + ')' + (e.matches(':focus-visible') ? '' : ' [NOT :focus-visible]') }; };
  lib.finish = () => { blurActive(); };
  // 0.0.29 E2: an element is DONE once its ring was judged (green or red), in any pass of one
  // screen-and-theme: a retry pass grades only what is not done (stale, unreached, not focusable
  // then, or new), so a deterministic red is never paid for three times. The set holds the NODE, so a
  // re-drawn element is a new node and is graded again.
  lib.mark = (i) => { done.add(el(i)); };
  // the graded count is what is graded AND still on the page now (never a node graded and then
  // removed, never one node counted twice)
  lib.graded = () => lib.cands.filter((e) => e.isConnected && done.has(e)).length;
  // the probe's "before": focus taken away, nothing else touched (never prep: lib.before[i] is the
  // verdict's unfocused style and is read before this)
  lib.blur = () => { blurActive(); };
  window.__fa = lib;
  return focus;
})()
"""

#: the arrow keys that walk a roving group, by its axis; the other axis is tried when the first fails
ARROWS = {"y": ("ArrowDown", "ArrowRight"), "x": ("ArrowRight", "ArrowDown")}
#: the neighbourhood of an element the pixel probe compares, in CSS px around its box
PROBE_PAD = 6
VIEW_W, VIEW_H = 1400, 900


#: 0.0.29 E: the stronger audit (§3d) is RED on the real tree in Chromium and Firefox — ~51 controls
#: draw the browser's own `outline: auto` ring, not `--focus`; the editor tabs (role=tab, roving
#: tabindex) have no arrow-key handler; `button.ref-head` sits in `overflow:hidden` and its outset ring
#: is clipped. The fix is product CSS/JS outside lane E's files, so the failing screens are pinned
#: `expectedFailure` (filed `IDE-FOCUS-RING-UA-AUTO`, doc 84): when the product is fixed they pass,
#: unittest counts the unexpected success as a failure, and the marker must come off.
class EveryFocusableSurfaceShowsAFocusRing(H.IdeFlow):
    FIXTURE = staticmethod(build_audit)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if cls.no_browser:
            return
        seed_session(cls.repo)

    def clip(self, r):
        """`r` (a CSS-px box in the viewport) padded by PROBE_PAD and clamped to the viewport, or None
        when none of it is on screen."""
        x0, y0 = max(0, r["x"] - PROBE_PAD), max(0, r["y"] - PROBE_PAD)
        x1, y1 = min(VIEW_W, r["x"] + r["w"] + PROBE_PAD), min(VIEW_H, r["y"] + r["h"] + PROBE_PAD)
        if x1 - x0 < 1 or y1 - y0 < 1:
            return None
        return {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}

    def shot(self, r):
        c = self.clip(r)
        if c is None:
            return None
        # two animation frames first: the style change of a focus or a blur is PAINTED by the next
        # frame, and a shot taken before it reads as "nothing changed" (an event, not a clock)
        self.page.evaluate("() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))")
        return self.page.screenshot(clip=c, animations="disabled", caret="hide")

    def reach_by_arrows(self, i):
        """Put keyboard focus on roving item `i` the way a person does. Returns "" or why not."""
        p = self.page
        st = p.evaluate("(i) => window.__fa.stop(i)", i)
        if not st["found"]:
            return "no Tab stop in its group (%s) to start from" % st["group"]
        for key in ARROWS[st["axis"]]:
            if p.evaluate("(i) => window.__fa.stop(i).found && window.__fa.active(i)", i):
                return ""
            for _ in range(st["size"] + 1):
                self.press(key)
                if p.evaluate("(i) => window.__fa.active(i)", i):
                    return ""
            p.evaluate("(i) => window.__fa.stop(i)", i)       # back to the stop for the other axis
        return "no arrow key reaches it from %s in its group %s" % (st["stop"], st["group"])

    def grade_current(self, where, theme, wanted=()):
        """Audit every focusable element on the current screen, in the current theme; the reds. The
        page re-draws parts of itself on live events: an item that was drawn again with no id to find
        it by, or a group still filling in when it was reached, is graded again on a fresh pass (up to
        three), and what is still missing or unreachable on the last pass is a red.

        0.0.29 E2: a retry pass grades only what no earlier pass judged (`lib.mark`: stale,
        unreached, not focusable then, or a node that is new or re-drawn). Re-grading the whole
        screen paid three times for every deterministic red - the editor tabs no arrow key reaches
        (IDE-FOCUS-RING-UA-AUTO) are unreached on every pass - and pushed both ide-e2e jobs past
        their CI ceilings (mirror runs 43869782006, 43883056679, 43896526038)."""
        p = self.page
        p.evaluate("window.__faDone = new WeakSet()")          # one screen in one theme
        judged, seen, probed, hits = [], [], 0, set()
        for attempt in range(3):
            r = self.grade_pass(p, wanted)
            judged.extend(r["bad"])                             # reds of items judged in THIS pass
            probed += r["probed"]
            seen.extend(r["seen"])
            hits.update(r["hits"])
            if not r["stale"] and not r["unreached"]:
                break
        # what is still unreached / stale is a red from the LAST pass only (an earlier pass's
        # "not reachable" for an item a later pass reached is not a red)
        bad = judged + r["unreached_bad"]
        bad.extend("%s: left the page before it could be graded (re-drawn with no id), three passes running" % n
                   for n in r["stale"])
        return {"bad": bad, "graded": r["graded"], "stops": r["stops"], "roving": r["roving"],
                "excluded": r["excluded"], "probed": probed, "stale": r["stale"],
                "unreached": r["unreached"], "hits": hits, "modal": r["modal"], "seen": seen}

    def grade_pass(self, p, wanted):
        """One pass over the screen: every element no earlier pass of this screen-and-theme judged.
        The computed-style verdict comes first; only an item whose verdict is green pays for the
        pixel probe (a red verdict's shots were thrown away)."""
        p.evaluate(FA_LIB)
        info = p.evaluate("(w) => window.__fa.enumerate(w)", list(wanted))
        bad, seen, probed, stale, unreached, unreached_bad, hits = [], [], 0, [], [], [], set()
        for item in info["list"]:
            if item["done"]:
                continue                          # judged by an earlier pass: never paid for twice
            i, label = item["i"], item["name"]
            r0 = p.evaluate("(i) => window.__fa.prep(i)", i)
            if r0.get("stale"):
                stale.append(label)
                continue
            try:
                if item["roving"]:
                    why = self.reach_by_arrows(i)
                    if why:
                        unreached.append(label)
                        unreached_bad.append("%s: roving item not reachable by keyboard: %s" % (label, why))
                        continue
                elif not p.evaluate("(i) => window.__fa.focusIt(i).focused", i):
                    continue                      # not focusable right now (covered, inert): not a Tab stop
                v = p.evaluate("(i) => window.__fa.verdict(i)", i)
                p.evaluate("(i) => window.__fa.mark(i)", i)
                seen.append(label)
                hits.update(item["hits"])
                if not v["ok"]:
                    bad.append(v["why"])
                    continue
                # the PIXEL PROBE, for a green verdict: focus away (nothing else touched), shoot,
                # focus again the same way, shoot. A tab's arrow keys already activated it on the
                # first reach, so both shots are of the same screen and differ only by the ring.
                p.evaluate("window.__fa.blur()")
                before = self.shot(p.evaluate("(i) => window.__fa.rect(i)", i))
                if before is None:
                    continue                      # nothing of it is on screen: no neighbourhood to compare
                if item["roving"]:
                    why = self.reach_by_arrows(i)
                    if why:
                        bad.append("%s: graded green, then not reachable again for the pixel probe: %s" % (label, why))
                        continue
                elif not p.evaluate("(i) => window.__fa.focusIt(i).focused", i):
                    bad.append("%s: graded green, then could not be focused again for the pixel probe" % label)
                    continue
                after = self.shot(p.evaluate("(i) => window.__fa.rect(i)", i))
                if after is None:
                    bad.append("%s: graded green, then left the screen before the pixel probe's second shot" % label)
                    continue
                probed += 1
                if before == after:
                    bad.append("%s: the computed ring paints no pixel (the neighbourhood is identical before and after focus): %s"
                               % (label, p.evaluate("(i) => window.__fa.cover(i)", i)))
            finally:
                if item["tab"]:
                    p.evaluate("window.__fa.restore()")
        graded = p.evaluate("window.__fa.graded()")
        p.evaluate("window.__fa.finish()")
        return {"bad": bad, "seen": seen, "graded": graded, "stops": info["stops"], "roving": info["roving"],
                "excluded": info["excluded"], "probed": probed, "stale": stale, "unreached": unreached,
                "unreached_bad": unreached_bad, "hits": hits, "modal": info["modal"]}

    def audit(self, where, must_see=(), roving=0, minimum=6):
        """Grade every focusable element of the screen in all four themes. `must_see` are CSS
        selectors: each must match at least one element that was reached and graded (so a screen
        that failed to draw cannot pass by auditing something else)."""
        p = self.page
        # keyboard modality, as a Tab user has it: a real Tab and Shift+Tab (a lone Shift is not
        # keyboard use to Firefox, and after the click that opened a dialog a script focus() would
        # not match :focus-visible there, so the audit would grade a ring the person never gets)
        p.evaluate("document.activeElement && document.activeElement.blur()")
        self.press("Tab")
        self.press("Shift+Tab")
        seen, roving_seen, excluded, reds, hit, probed, fewest = set(), 0, set(), [], set(), 0, None
        for theme in ("dark", "light", "hc-dark", "hc-light"):
            p.evaluate("MK.theme.set(%r)" % theme)
            self.until("document.documentElement.dataset.theme === %r" % theme, theme)
            r = self.grade_current(where, theme, must_see)
            probed += r["probed"]
            fewest = r["graded"] if fewest is None else min(fewest, r["graded"])
            reds.extend("%s in %s: %s" % (where, theme, b) for b in r["bad"])
            seen.update(r["seen"])
            hit.update(r["hits"])
            roving_seen = max(roving_seen, r["roving"])
            excluded.update(r["excluded"])
        self.maxDiff = None
        self.assertEqual([], reds, "%s: focus draws no visible ring on these" % where)
        # a pass that graded nothing proves nothing: these guards come AFTER the reds (a screen whose
        # every element is red never reaches the pixel probe, and that is a red, not a vacuous pass)
        self.assertGreaterEqual(fewest, minimum, "%s: the audit found the focusable surfaces" % where)
        self.assertGreater(probed, 0, "%s: the pixel probe ran" % where)
        for want in must_see:
            self.assertIn(want, hit, "%s: nothing matching %s was graded (graded: %s)" % (where, want, sorted(seen)))
        self.assertGreaterEqual(roving_seen, roving, "%s: the roving items were found and graded" % where)
        for e in excluded:
            self.assertIn("[role=separator]", e, "%s: only a resize handle (role=separator) is excluded, not %s" % (where, e))

    @unittest.expectedFailure      # IDE-FOCUS-RING-UA-AUTO (doc 84): a product finding
    def test_1_review(self):
        self.go("review")
        self.page.wait_for_selector("#nav-list li", state="attached")
        self.audit("Review", ("#tab-review", "#subtab-review", "#nav-list button"), roving=3)

    @unittest.expectedFailure      # IDE-FOCUS-RING-UA-AUTO (doc 84): a product finding
    def test_2_sessions(self):
        self.go("activity")
        self.audit("Sessions", ("#tab-sessions", "#s1-q"))

    @unittest.expectedFailure      # IDE-FOCUS-RING-UA-AUTO (doc 84): a product finding
    def test_3_code_with_the_find_bar_and_the_terminal(self):
        p = self.page
        self.open_file("shop/text.py")
        # The terminal drawer as Terminal opens it. This harness has no pty to start one with
        # (the Terminal button is hidden here), so the drawer is shown directly: what is graded is
        # how its screen looks when focused, before a session exists.
        p.evaluate("document.getElementById('term-drawer').hidden = false")
        self.press("Control+F")
        p.wait_for_selector("#find-bar:not([hidden])")
        self.audit("Code", ("#find-input", "#term-screen", ".tree-row"))

    def test_4_the_palette(self):
        p = self.page
        self.press("Control+K")
        p.wait_for_selector("#qo:not([hidden])")
        try:
            self.audit("the palette", ("#qo-input",), minimum=1)   # a modal: its one Tab stop is the whole keyboard world
        finally:                                 # a red here must not leave the palette over the next screen
            p.focus("#qo-input")                 # the audit blurred it; Escape is the input's key
            self.press("Escape")
            p.wait_for_selector("#qo", state="hidden")

    @unittest.expectedFailure      # IDE-FOCUS-RING-UA-AUTO (doc 84): a product finding
    def test_5_the_table_view(self):
        self.open_file("data/people.csv")
        self.page.wait_for_selector("#tb-code .tb-row .tb-td")
        self.audit("the table view", (".tb-sort",))

    @unittest.expectedFailure      # IDE-FOCUS-RING-UA-AUTO (doc 84): a product finding
    def test_6_the_table_diff(self):
        p = self.page
        self.go("review")
        p.wait_for_selector("#nav-list li", state="attached")
        p.locator("#nav-list li", has_text="people.csv").first.click()
        p.wait_for_selector("#diff .tb-dbtn:not([disabled])")
        p.click("#diff .tb-dbtn")
        p.wait_for_selector("#diff .tb-dsum")
        p.select_option("#diff .tb-key", "id")
        self.until("document.querySelector('#diff .tb-dsum') && document.querySelector('#diff .tb-dsum').textContent.includes('+2 added')",
                   "the keyed table diff")
        self.audit("the table diff", (".tb-key", ".tb-dbtn"))

    @unittest.expectedFailure      # IDE-FOCUS-RING-UA-AUTO (doc 84): a product finding
    def test_7_the_hint_card(self):
        p = self.page
        p.evaluate("document.activeElement && document.activeElement.blur()")
        self.go("activity")
        p.locator("#s1-rows button.s1-row", has_text=SID[:8]).first.click()
        self.text_until("#s1-card", "Next:", "the card's next step")
        self.until("MK.activityPanel.hintSettled()", "the hints to answer")
        self.audit("the hint card", ("#s1-card button",))

    @unittest.expectedFailure      # IDE-FOCUS-RING-UA-AUTO (doc 84): a product finding
    def test_8_the_approval_card(self):
        from mokata import approval
        p = self.page
        pr = approval.propose(self.repo, tool="remember", args={"n": 1}, run_id="R-focus-1",
                              target="memory:focus1", summary="remember focus 1", preview="a line")
        p.evaluate("MK.reloadApprovals()")
        p.wait_for_selector("#waiting:not([hidden])")
        if p.get_attribute("#wait-toggle", "aria-expanded") != "true":
            p.click("#wait-toggle")
        p.wait_for_selector("#approvals-bar .ap-card[data-pid='%s']" % pr.proposal_id)
        self.audit("the approval card", ("#approvals-bar .ap-card button", "#wait-toggle"))

    @unittest.expectedFailure      # IDE-FOCUS-RING-UA-AUTO (doc 84): a product finding
    def test_9_welcome(self):
        self.go("welcome")
        self.audit("welcome", ("#hp-welcome button, #hp-welcome a, #hp-welcome [tabindex]",))


def build_unknown(base):
    repo = build(base)
    d = os.path.join(repo, ".mokata", "temp_local", "ide")
    os.makedirs(d, exist_ok=True)
    H._write(repo, ".mokata/temp_local/ide/workspace.json",
             '{"schema": 2, "tabs": [], "theme": "neon"}\n')
    return repo


class AnUnknownSavedThemeIsSaidOnce(H.IdeFlow):
    FIXTURE = staticmethod(build_unknown)

    def test_1_the_page_follows_the_system_and_says_why(self):
        p = self.page
        self.text_until("#pal-toast", "neon", "the note about the unknown theme")
        self.assertIn("following the system", p.text_content("#pal-toast"))
        self.assertEqual("system", p.evaluate("document.documentElement.dataset.themeChoice"))

    def test_2_it_is_not_said_again_after_the_next_save(self):
        p = self.page
        self.open_file("shop/text.py")                                  # any change saves the workspace
        for _ in range(100):
            if p.evaluate("fetch('/api/workspace').then(r => r.json()).then(w => w.theme_unknown)") is None:
                break
            p.wait_for_timeout(100)
        else:
            self.fail("the note was never cleared")
        p.reload()
        self.until("!!document.getElementById('qo-open')", "the page")
        p.wait_for_timeout(800)
        self.assertNotIn("neon", p.evaluate("(document.getElementById('pal-toast') || {textContent: ''}).textContent"))


if __name__ == "__main__":
    unittest.main()
