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
        except ImportError:
            self.skipTest("the syntax-highlight check needs Pygments: pip install -e .[ide]")
        self.open_file("shop/text.py")
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
            # syntax highlight (Code view of an open file)
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


# 0.0.27 PINS-UI (P6 round 2): what a keyboard user SEES on every focusable surface, read from the
# browser's computed style (never a grep of the CSS text, which missed a ring taken away by an
# `outline: none` in a BASE rule and a ring drawn in currentColor). For every visible element that
# takes Tab focus: blur it, read its style, focus it as the keyboard does, read again. It passes
# only if focusing it DRAWS something a person can see:
#   - the browser's own two-tone ring (`outline-style: auto`, width >= 1px), or
#   - an outline with a visible style and a width >= 1px, or
#   - a box-shadow layer that focus added and that has EXTENT: blur or spread >= 1px, or an offset
#     >= 1px (`inset 0 -2px 0` is a 2px underline). A `0 0 0 0px` layer draws nothing (round 2, M2), or
#   - a changed border >= 1px wide,
# and that mark is painted in the theme's --focus colour, composited over what is behind the element
# (so a transparent or half-alpha layer is graded as what reaches the screen), at 3:1 or more. A ring
# in currentColor, a border that was already that colour, a zero-extent or transparent shadow, or
# nothing at all, is a red naming the element. The rule is one predicate for every element found.
FOCUS_AUDIT = r"""
(() => {
  const vis = (e) => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden' && !e.closest('[hidden]');
  const rgba = (s) => { const m = /rgba?\(([\d.]+),\s*([\d.]+),\s*([\d.]+)(?:,\s*([\d.]+))?\)/.exec(s || ''); return m ? [+m[1], +m[2], +m[3], m[4] === undefined ? 1 : +m[4]] : null; };
  const lum = (c) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const behind = (e) => { for (let n = e; n && n.nodeType === 1; n = n.parentElement) { const c = rgba(getComputedStyle(n).backgroundColor); if (c && c[3] >= 0.99) return c; } return rgba(getComputedStyle(document.body).backgroundColor); };
  const probe = document.createElement('i'); probe.style.color = 'var(--focus)'; document.body.append(probe);
  const focus = getComputedStyle(probe).color; probe.remove();
  const name = (e) => e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') + (typeof e.className === 'string' && e.className.trim() ? '.' + e.className.trim().split(/\s+/).join('.') : '');
  const snap = (e) => { const s = getComputedStyle(e); return { os: s.outlineStyle, ow: parseFloat(s.outlineWidth) || 0, oc: s.outlineColor, bs: s.boxShadow,
    bc: [s.borderTopColor, s.borderRightColor, s.borderBottomColor, s.borderLeftColor].join('|'), bw: parseFloat(s.borderTopWidth) || 0 }; };
  // computed box-shadow: "rgb(..) X Y BLUR SPREAD [inset], ..." -> [{c, extent}] (one per layer)
  const layers = (bs) => { const out = []; if (!bs || bs === 'none') return out;
    const re = /(rgba?\([^)]*\))\s+([^,]*?)(?=,\s*rgba?\(|$)/g; let m;
    while ((m = re.exec(bs))) { const n = (m[2].match(/-?[\d.]+px/g) || []).map((v) => Math.abs(parseFloat(v)));
      out.push({ c: m[1], key: m[0].trim(), extent: Math.max(0, ...n) }); }
    return out; };
  // what reaches the screen: the colour composited over the background behind the element
  const over = (c, bg) => { const k = rgba(c); if (!k) return null; const a = k[3];
    return [0, 1, 2].map((i) => k[i] * a + bg[i] * (1 - a)).concat(1); };
  const shows = (c, e) => { const k = rgba(c), bg = behind(e); return !!k && k[3] > 0 && c === focus && ratio(over(c, bg), bg) >= 3; };
  const els = [...document.querySelectorAll('a[href],button,input,select,textarea,[tabindex]')].filter((e) => !e.disabled && e.tabIndex >= 0 && vis(e));
  const bad = [], seen = [];
  for (const e of els) {
    if (document.activeElement && document.activeElement !== document.body) document.activeElement.blur();
    const a = snap(e);
    e.focus({ focusVisible: true });
    if (document.activeElement !== e) continue;
    seen.push(name(e));
    const b = snap(e);
    const ways = [];
    if (b.os === 'auto' && b.ow >= 1) ways.push('ua');
    if (b.os !== 'none' && b.os !== 'hidden' && b.os !== 'auto' && b.ow >= 1) ways.push(['outline', b.oc]);
    const was = new Set(layers(a.bs).map((l) => l.key));
    for (const l of layers(b.bs)) if (!was.has(l.key) && l.extent >= 1) ways.push(['shadow', l.c]);
    if (b.bc !== a.bc && b.bw >= 1) for (const c of b.bc.split('|')) ways.push(['border', c]);
    const ok = ways.some((w) => w === 'ua' || shows(w[1], e));
    if (!ok) bad.push(name(e) + ' ' + JSON.stringify(ways) + ' on ' + JSON.stringify(behind(e)));
  }
  if (document.activeElement && document.activeElement !== document.body) document.activeElement.blur();
  return { n: els.length, focus, bad, seen };
})()
"""


class EveryFocusableSurfaceShowsAFocusRing(H.IdeFlow):
    FIXTURE = staticmethod(build)

    def audit(self, where, must_see=()):
        p = self.page
        self.press("Shift")                      # keyboard modality, as a Tab user has it
        seen = set()
        for theme in ("dark", "light", "hc-dark", "hc-light"):
            p.evaluate("MK.theme.set(%r)" % theme)
            self.until("document.documentElement.dataset.theme === %r" % theme, theme)
            r = p.evaluate(FOCUS_AUDIT)
            self.assertGreater(len(r["seen"]), 5, "%s: the audit found the focusable surfaces" % where)
            self.assertEqual([], r["bad"], "%s in %s: focus draws no visible ring on these" % (where, theme))
            seen.update(r["seen"])
        for want in must_see:
            self.assertTrue(any(want in s for s in seen), "%s: %s was audited (%s)" % (where, want, sorted(seen)))

    def test_1_review(self):
        self.go("review")
        self.page.wait_for_selector("#nav-list li", state="attached")
        self.audit("Review", ("#tab-review", "#subtab-review"))

    def test_2_sessions(self):
        self.go("activity")
        self.audit("Sessions", ("#tab-sessions", "#s1-q"))

    def test_3_code_with_the_find_bar_and_the_terminal(self):
        p = self.page
        self.open_file("shop/text.py")
        # The terminal drawer as Terminal opens it. This harness has no pty to start one with
        # (the Terminal button is hidden here), so the drawer is shown directly: what is graded is
        # how its screen looks when focused, before a session exists.
        p.evaluate("document.getElementById('term-drawer').hidden = false")
        self.press("Control+F")
        p.wait_for_selector("#find-bar:not([hidden])")
        self.audit("Code", ("#find-input", "#term-screen", "tree-row"))

    def test_4_the_palette(self):
        p = self.page
        self.press("Control+K")
        p.wait_for_selector("#qo:not([hidden])")
        self.audit("the palette", ("#qo-input",))
        p.focus("#qo-input")                     # the audit blurred it; Escape is the input's key
        self.press("Escape")
        p.wait_for_selector("#qo", state="hidden")


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
