"""0.0.27 P6 `ID-THEMES-FULL` — IDE themes: dark, light, two high-contrast themes, "follow the system".

Sources (docs/build): 113 §5b "P6 ID-THEMES-FULL (auto OS + high-contrast + font settings + user theme
file + AA check)"; 84 ID-THEMES-FULL "brand tokens + auto OS light/dark + high-contrast"; 109 §8 "brand
dark/light + high-contrast + user theme file over the token system"; 86 splits the row (user theme
files, font settings and the axe-core AA run are 0.1.2). This stage builds the 0.0.26/27 half.

What must be true for the user:
  * Every colour the IDE draws comes from ONE token file (static/themes.css), once per theme. No
    stylesheet or script holds a colour literal, so a theme cannot be half applied.
  * Text and focus rings reach WCAG AA in EVERY theme — computed from the token values, not eyeballed:
    4.5:1 for text, 3:1 for a focus ring; the two high-contrast themes reach 7:1.
  * The theme the person picked is saved with the workspace (the server's file), comes back after a
    restart, and an unknown value never lands in it.
  * "Follow the system" resolves prefers-color-scheme AND prefers-contrast and re-resolves live; a
    pick the person made is never undone by a late workspace load.
  * The palette (Ctrl/Cmd+K, type ">theme") picks a theme; no new chord is taken.
  * Mermaid is drawn in the theme's colours and cannot be handed anything but hex.
  * The page-level proof — no reload, no lost editor state, scroll or focus — is the browser flow
    tests/e2e_ide/test_themes.py.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import _support  # noqa: F401  (puts src/ on the path)

from test_p0_ide_api_contract import ORIGIN, fixture
from test_p2_ide_tabs_restore import boot

from mokata.ide import workspace as W

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "mokata" / "ide" / "static"
THEMES_CSS = STATIC / "themes.css"
THEMES = ("dark", "light", "hc-dark", "hc-light")


# ===================================================================== colour maths

def _rgba(v: str):
    v = v.strip()
    m = re.fullmatch(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})", v)
    if m:
        h = m.group(1)
        h = "".join(c * 2 for c in h) if len(h) == 3 else h
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 1.0
    m = re.fullmatch(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)", v)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3)), float(m.group(4) or 1)
    raise AssertionError("not a colour the contrast test can read: %r" % v)


def over(fg: str, bg: str):
    """`fg` (maybe translucent) painted over the opaque `bg`: the colour the eye gets."""
    r, g, b, a = _rgba(fg)
    br, bg_, bb, _ = _rgba(bg)
    return tuple(round(a * x + (1 - a) * y) for x, y in ((r, br), (g, bg_), (b, bb)))


def _lum(rgb) -> float:
    def lin(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(a, b) -> float:
    """WCAG 2.x contrast ratio of two opaque (r, g, b) colours."""
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def hexof(rgb) -> str:
    return "#%02x%02x%02x" % tuple(rgb)


def tokens() -> dict:
    """{theme: {token: value}} read from themes.css (the `:root` block is the dark theme's)."""
    css = re.sub(r"/\*.*?\*/", "", THEMES_CSS.read_text(encoding="utf-8"), flags=re.S)
    out = {}
    for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        m = re.search(r'data-theme="([a-z-]+)"', sel)
        if not m:
            continue
        out[m.group(1)] = dict((k, v.strip()) for k, v in re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", body))
    return out


# ===================================================================== the colour-literal sweep

CSS_COLOUR_WORDS = ("aliceblue antiquewhite aqua aquamarine azure beige bisque black blanchedalmond blue "
                    "blueviolet brown burlywood cadetblue chartreuse chocolate coral cornflowerblue cornsilk "
                    "crimson cyan darkblue darkcyan darkgray darkgreen darkgrey darkkhaki darkmagenta "
                    "darkolivegreen darkorange darkorchid darkred darksalmon darkseagreen darkslateblue "
                    "darkslategray darkturquoise darkviolet deeppink deepskyblue dimgray dimgrey dodgerblue "
                    "firebrick floralwhite forestgreen fuchsia gainsboro ghostwhite gold goldenrod gray green "
                    "greenyellow grey honeydew hotpink indianred indigo ivory khaki lavender lavenderblush "
                    "lawngreen lemonchiffon lightblue lightcoral lightcyan lightgray lightgreen lightgrey "
                    "lightpink lightsalmon lightseagreen lightskyblue lightslategray lightsteelblue lightyellow "
                    "lime limegreen linen magenta maroon mediumaquamarine mediumblue mediumorchid mediumpurple "
                    "mediumseagreen mediumslateblue mediumspringgreen mediumturquoise mediumvioletred "
                    "midnightblue mintcream mistyrose moccasin navajowhite navy oldlace olive olivedrab orange "
                    "orangered orchid palegoldenrod palegreen paleturquoise palevioletred papayawhip peachpuff "
                    "peru pink plum powderblue purple rebeccapurple red rosybrown royalblue saddlebrown salmon "
                    "sandybrown seagreen seashell sienna silver skyblue slateblue slategray slategrey snow "
                    "springgreen steelblue tan teal thistle tomato turquoise violet wheat white whitesmoke "
                    "yellow yellowgreen").split()
_WORD = re.compile(r"(?<![\w-])(%s)(?![\w-])" % "|".join(CSS_COLOUR_WORDS), re.I)
_HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
_FUNC = re.compile(r"\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color)\(\s*[\d.\-]", re.I)


def css_colour_literals(css: str):
    """Every colour literal in the DECLARATIONS of a stylesheet (selectors like `#add` are not)."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    hits = []
    for body in re.findall(r"\{([^{}]*)\}", css):
        for decl in body.split(";"):
            if ":" not in decl:
                continue
            prop, _, value = decl.partition(":")
            value = re.sub(r"var\([^)]*\)|url\([^)]*\)|\"[^\"]*\"|'[^']*'", " ", value)
            for rx in (_HEX, _FUNC):
                hits += [m.group(0) for m in rx.finditer(value)]
            if re.search(r"color|background|border|outline|fill|stroke|shadow|decoration|caret|accent", prop, re.I):
                hits += [m.group(0) for m in _WORD.finditer(value)]
    return hits


def js_colour_literals(js: str):
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    js = re.sub(r"(?m)(^|\s)//.*$", "", js)
    hits = [m.group(0) for m in re.finditer(r"""["'`]#[0-9a-fA-F]{3,8}["'`]""", js)]
    hits += [m.group(0) for m in re.finditer(r"\b(?:rgba?|hsla?)\(\s*\d", js)]
    return hits


def swept_files():
    css = [p for p in sorted(STATIC.glob("*.css")) + sorted((STATIC / "features").glob("*.css"))
           if p.name != "themes.css"]
    js = [p for p in sorted(STATIC.glob("*.js")) + sorted((STATIC / "features").glob("*.js"))]
    return css, js


class NoColourLiteralOutsideTheTokenFile(unittest.TestCase):
    """The rule: one place decides colours, so a theme is never half applied."""

    def test_the_detector_sees_each_kind_of_literal_and_ignores_selectors_and_tokens(self):
        for bad in ("a { color: #fff; }", "a { background: rgba(0, 0, 0, .5); }", "a { color: red; }",
                    "a { border: 1px solid #3df58c; }", "a { box-shadow: 0 1px 2px rgb(1,2,3); }",
                    "a { fill: hsl(10 20% 30%); }"):
            self.assertTrue(css_colour_literals(bad), bad)
        for ok in ("#add { color: var(--text); }", "a { color: var(--blue); }", "a { border-color: transparent; }",
                   "a { color: currentColor; }", "a { outline: 2px solid var(--focus); }",
                   "a { color: CanvasText; background: Mark; }", "a { content: 'red'; }"):
            self.assertEqual([], css_colour_literals(ok), ok)
        self.assertTrue(js_colour_literals('x.style.color = "#fff";'))
        self.assertTrue(js_colour_literals("x.style.color = `rgb(1,2,3)`;"))
        self.assertEqual([], js_colour_literals('// "#fff" is a comment\nq("#code"); x = `rgb(${v},${v},${v})`;'))

    def test_the_sweep_reads_every_ide_stylesheet_and_script(self):
        css, js = swept_files()
        names = {p.name for p in css} | {p.name for p in js}
        for must in ("app.css", "highlight.css", "diffview.css", "graph.js", "terminal.js", "render.js", "theme.js"):
            self.assertIn(must, names)
        self.assertGreater(len(css) + len(js), 40)
        self.assertTrue(css_colour_literals(THEMES_CSS.read_text(encoding="utf-8")),
                        "themes.css is where the literals live — the sweep must see them there")

    def test_no_stylesheet_but_themes_css_holds_a_colour_literal(self):
        css, _ = swept_files()
        for p in css:
            self.assertEqual([], css_colour_literals(p.read_text(encoding="utf-8")), p.name)

    def test_no_script_holds_a_colour_literal(self):
        _, js = swept_files()
        for p in js:
            self.assertEqual([], js_colour_literals(p.read_text(encoding="utf-8")), p.name)

    def test_the_page_has_no_inline_colour_and_links_the_tokens_before_the_rules(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertEqual([], _HEX.findall(re.sub(r'href="#[^"]*"', "", " ".join(re.findall(r'style="[^"]*"', html)))))
        self.assertLess(html.index("/static/themes.css"), html.index("/static/app.css"))
        self.assertLess(html.index("/static/theme.js"), html.index("/static/registry.js"),
                        "theme.js is in <head>: the page is never painted in the wrong theme")

    def test_the_ansi_palette_is_the_themes(self):
        """The terminal's 16 colours are tokens, so a switch repaints text already on screen."""
        t = (STATIC / "terminal.js").read_text(encoding="utf-8")
        self.assertIn("var(--ansi-${i})", t)
        for name, toks in tokens().items():
            for i in range(16):
                self.assertIn("--ansi-%d" % i, toks, name)


class EveryVariableIsDefinedInEveryTheme(unittest.TestCase):

    def test_the_four_themes_define_the_same_tokens(self):
        t = tokens()
        self.assertEqual(set(THEMES), set(t))
        union = set().union(*(set(v) for v in t.values()))
        for name in THEMES:
            self.assertEqual([], sorted(union - set(t[name])), "%s is missing tokens" % name)
        self.assertGreater(len(union), 60)

    def test_every_var_a_stylesheet_reads_is_a_token_or_declared_where_it_is_used(self):
        union = set(tokens()["dark"])
        css = list(STATIC.glob("*.css")) + list((STATIC / "features").glob("*.css"))
        body = "\n".join(re.sub(r"/\*.*?\*/", "", p.read_text(encoding="utf-8"), flags=re.S) for p in css)
        declared = set(re.findall(r"(--[a-z0-9-]+)\s*:", body))
        used = set(re.findall(r"var\((--[a-z0-9-]+)", body))
        js = "\n".join(p.read_text(encoding="utf-8") for p in
                       list(STATIC.glob("*.js")) + list((STATIC / "features").glob("*.js")))
        used |= set(re.findall(r'css\("(--[a-z0-9-]+)"\)', js)) | set(re.findall(r'g\("(--[a-z0-9-]+)"\)', js))
        declared |= set(re.findall(r'setProperty\("(--[a-z0-9-]+)"', js))        # set on one element by the page
        self.assertEqual([], sorted(used - union - declared), "a var() no theme defines paints nothing")

    def test_only_the_brand_greens_are_green_accents(self):
        """BRAND.md: #3df58c on dark, #22c55e on light, never a third green as the accent."""
        t = tokens()
        self.assertEqual("#3df58c", t["dark"]["--accent"])
        self.assertEqual("#3df58c", t["hc-dark"]["--accent"])
        self.assertEqual("#22c55e", t["light"]["--accent"])
        self.assertEqual("#22c55e", t["hc-light"]["--accent"])


# ===================================================================== WCAG AA from the token values

AA_TEXT, AA_RING, HC_TEXT = 4.5, 3.0, 7.0


def _floor(theme: str, aa: float) -> float:
    return HC_TEXT if theme.startswith("hc-") else aa


def contrast_failures(toks: dict, theme: str):
    """Every (name, ratio, needed) below the bar for ONE theme's tokens."""
    bad = []
    need = _floor(theme, AA_TEXT)
    ring = _floor(theme, AA_RING)

    def check(name, fg, bg, floor):
        r = ratio(fg, bg)
        if r < floor:
            bad.append((name, round(r, 2), floor))

    surfaces = {k: _rgba(toks["--" + k])[:3] for k in ("bg", "panel", "panel2")}
    ink = ["text", "text2", "muted", "faint", "accent-ink", "add", "del", "amber", "blue"]
    ink += sorted(k[2:] for k in toks if k.startswith("--tk-"))
    ink += ["ansi-%d" % i for i in range(1, 16)]
    for s, sc in surfaces.items():
        for k in ink:
            check("%s on %s" % (k, s), over(toks["--" + k], hexof(sc)), sc, need)
        check("focus ring on %s" % s, _rgba(toks["--focus"])[:3], sc, ring)
    sel = over(toks["--sel"], toks["--panel"])
    for k in ("text", "text2", "accent-ink", "add", "del", "amber", "blue"):
        check("%s on the selected row" % k, over(toks["--" + k], hexof(sel)), sel, need)
    # rows that tint the page: the tint is painted over the panel, then the text over the tint
    for tint, fg in (("add-bg", "add"), ("del-bg", "del"), ("amber-bg", "amber"), ("amber-bg-strong", "amber"),
                     ("find-hit", "text"), ("heat-1", "text"), ("heat-2", "text")):
        for s, sc in surfaces.items():
            row = over(toks["--" + tint], hexof(sc))
            check("%s on %s over %s" % (fg, tint, s), over(toks["--" + fg], hexof(row)), row, need)
    # intra-line marks sit on a changed row's tint
    for ix, row_tint, fg in (("ix-add", "add-bg", "add"), ("ix-del", "del-bg", "del")):
        row = over(toks["--" + row_tint], toks["--panel"])
        mark = over(toks["--" + ix], hexof(row))
        check("%s text on the intra-line mark" % fg, over(toks["--" + fg], hexof(mark)), mark, need)
        check("text on the intra-line mark", over(toks["--text"], hexof(mark)), mark, need)
    for fill in ("accent", "sample"):
        check("on-accent on %s" % fill, _rgba(toks["--on-accent"])[:3], _rgba(toks["--" + fill])[:3], need)
    return bad


_DRAWS = re.compile(r"^(outline(-color)?|border(-[a-z]+)*|box-shadow|stroke|fill|text-decoration(-color)?)$")


def focus_rules_in(css: str, name: str):
    """[(selector, file, property, [tokens drawn])] for every :focus / :focus-visible rule that DRAWS
    something with a colour token (outline, border, box-shadow, stroke). `outline: none` draws nothing."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out = []
    for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        if not re.search(r":focus(-visible)?\b", sel):
            continue
        toks = []
        for decl in body.split(";"):
            prop, _, value = decl.partition(":")
            if not _DRAWS.match(prop.strip()) or re.search(r"\bnone\b|^\s*0\s*$", value):
                continue
            toks += re.findall(r"var\(--([a-z0-9-]+)", value)
        if toks:
            out.append((sel.strip(), name, "", sorted(set(toks))))
    return out


def _base(sel: str) -> str:
    return re.sub(r":[a-z-]+(\([^)]*\))?", "", sel).strip()


def removed_rings_in(css: str):
    """Selectors whose :focus rule says `outline: none` and draws nothing, while NO :focus-visible rule
    for the same element draws a ring — a focus indicator taken away and not replaced."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    drawn, nones = set(), []
    for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        if not re.search(r":focus(-visible)?\b", sel):
            continue
        draws = bool(focus_rules_in(sel.strip() + "{" + body + "}", "x"))
        for one in sel.split(","):
            if ":focus" not in one:
                continue
            if draws:
                drawn.add(_base(one))
            elif re.search(r"outline(-style)?\s*:\s*(none|0)\b", body):
                nones.append(one.strip())
    return sorted(n for n in nones if _base(n) not in drawn)


def focus_rules():
    css = list(STATIC.glob("*.css")) + list((STATIC / "features").glob("*.css"))
    out = []
    for p in sorted(css):
        out += focus_rules_in(p.read_text(encoding="utf-8"), p.name)
    return out


class WcagAAInEveryTheme(unittest.TestCase):
    """The rule: the person can read the text and see where the keyboard is, in every theme."""

    def test_the_ratio_function_matches_the_wcag_reference_values(self):
        self.assertAlmostEqual(21.0, ratio((0, 0, 0), (255, 255, 255)), places=2)
        self.assertAlmostEqual(1.0, ratio((9, 9, 9), (9, 9, 9)), places=2)
        self.assertGreaterEqual(ratio((0x76, 0x76, 0x76), (255, 255, 255)), 4.5)
        self.assertLess(ratio((0x77, 0x77, 0x77), (255, 255, 255)), 4.6)
        self.assertEqual((128, 128, 128), over("rgba(255, 255, 255, .5)", "#000000"))

    def test_light_green_is_not_text_and_the_ink_green_is(self):
        """The brand light green fills; as text on white it would be 2.2:1 — the ink green carries text."""
        t = tokens()["light"]
        self.assertLess(ratio(_rgba(t["--accent"])[:3], _rgba(t["--panel"])[:3]), AA_TEXT)
        self.assertGreaterEqual(ratio(_rgba(t["--accent-ink"])[:3], _rgba(t["--panel"])[:3]), AA_TEXT)

    def test_dark_theme_reaches_aa(self):
        self.assertEqual([], contrast_failures(tokens()["dark"], "dark"))

    def test_light_theme_reaches_aa(self):
        self.assertEqual([], contrast_failures(tokens()["light"], "light"))

    def test_high_contrast_dark_reaches_7_to_1(self):
        self.assertEqual([], contrast_failures(tokens()["hc-dark"], "hc-dark"))

    def test_high_contrast_light_reaches_7_to_1(self):
        self.assertEqual([], contrast_failures(tokens()["hc-light"], "hc-light"))

    def test_the_check_goes_red_on_a_pale_token_and_on_a_weak_focus_ring(self):
        for theme in THEMES:
            weak = dict(tokens()[theme])
            weak["--muted"] = weak["--bg"]
            self.assertTrue([f for f in contrast_failures(weak, theme) if f[0].startswith("muted")], theme)
            ring = dict(tokens()[theme])
            ring["--focus"] = ring["--bg"]
            self.assertTrue([f for f in contrast_failures(ring, theme) if f[0].startswith("focus")], theme)

    def test_every_focus_rule_in_the_stylesheets_is_graded_on_whatever_it_draws(self):
        """The rule: wherever the keyboard is, it can be SEEN — 3:1 (7:1 in high contrast) against the
        surface, in every theme, whether the rule draws an outline, a border, a box-shadow or an SVG
        stroke. The rules are found by PARSING the stylesheets, so a new :focus rule is graded too."""
        rules = focus_rules()
        self.assertGreater(len(rules), 12, "the parser lost the focus rules")
        drawn = {tok for _, _, _, toks in rules for tok in toks}
        self.assertEqual({"focus"}, drawn, "a focus indicator drawn with anything but the --focus token: %s"
                         % sorted(r[:3] for r in rules if r[3] != ["focus"]))
        for theme in THEMES:
            toks = tokens()[theme]
            need = _floor(theme, AA_RING)
            for tok in drawn:
                for surf in ("bg", "panel", "panel2"):
                    r = ratio(_rgba(toks["--" + tok])[:3], _rgba(toks["--" + surf])[:3])
                    self.assertGreaterEqual(r, need, "%s: --%s on --%s" % (theme, tok, surf))

    def test_no_stylesheet_takes_a_focus_ring_away_without_putting_one_back(self):
        css = list(STATIC.glob("*.css")) + list((STATIC / "features").glob("*.css"))
        text = "\n".join(p.read_text(encoding="utf-8") for p in css)
        self.assertEqual([], removed_rings_in(text))
        self.assertEqual([".x:focus"], removed_rings_in(".x:focus { outline: none; }"))
        self.assertEqual([], removed_rings_in(".x:focus { outline: none; } .x:focus-visible { outline: 2px solid var(--focus); }"))

    def test_the_focus_parser_sees_border_shadow_and_stroke_and_skips_none(self):
        css = """a:focus { border-color: var(--line); } b:focus-visible { box-shadow: inset 0 0 0 1px var(--edge); }
                 .c:hover, .c:focus rect { stroke: var(--accent); } d:focus { outline: none; }
                 e:focus-visible { outline: 2px solid var(--focus); outline-offset: 1px; } f:hover { color: var(--text); }
                 @media (x) { g:focus { border: 1px solid var(--muted); } }"""
        got = sorted((sel.strip(), toks) for sel, _, _, toks in focus_rules_in(css, "t.css"))
        self.assertEqual(sorted([("a:focus", ["line"]), ("b:focus-visible", ["edge"]), (".c:hover, .c:focus rect", ["accent"]),
                          ("e:focus-visible", ["focus"]), ("g:focus", ["muted"])]), sorted(got))

    def test_green_text_in_the_stylesheets_is_the_ink_green(self):
        css = list(STATIC.glob("*.css")) + list((STATIC / "features").glob("*.css"))
        for p in css:
            text = p.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"(^|[\s;{])color:\s*var\(--accent\)", text), p.name)


# ===================================================================== the choice is saved with the workspace

class TheChoiceSurvivesARestart(unittest.TestCase):

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.d = os.path.realpath(self._t.name)
        fixture(self.d)

    def tearDown(self):
        self._t.cleanup()

    def save(self, c, theme):
        body = {"tabs": [], "theme": theme}
        r = c.post("/api/workspace", json=body, headers=ORIGIN)
        self.assertEqual(200, r.status_code, r.text)

    def test_a_new_repo_follows_the_system(self):
        c, _ = boot(self.d)
        self.assertEqual("system", c.get("/api/workspace").json()["theme"])

    def test_each_theme_is_saved_and_comes_back_after_a_server_restart(self):
        for theme in W.THEMES:
            c, _ = boot(self.d)
            self.save(c, theme)
            del c
            c2, _ = boot(self.d)
            self.assertEqual(theme, c2.get("/api/workspace").json()["theme"])
        raw = json.loads(Path(W.workspace_path(self.d)).read_text(encoding="utf-8"))
        self.assertEqual("hc-light", raw["theme"], "it is the server's per-repo workspace file, not a new store")

    def test_an_unknown_theme_is_never_stored(self):
        c, _ = boot(self.d)
        for bad in ("neon", "", None, 3, ["dark"], "DARK", "dark\n", "../x"):
            self.save(c, bad)
            self.assertEqual("system", c.get("/api/workspace").json()["theme"], repr(bad))

    def test_an_unknown_saved_theme_falls_back_to_system_and_says_so_until_the_next_save(self):
        c, _ = boot(self.d)
        self.save(c, "neon")
        w = c.get("/api/workspace").json()
        self.assertEqual(("system", "neon"), (w["theme"], w["theme_unknown"]))
        self.save(c, "light")                                    # the page's next save carries a real theme
        w = c.get("/api/workspace").json()
        self.assertEqual(("light", None), (w["theme"], w["theme_unknown"]))
        for ok in ("system", "dark"):
            self.save(c, ok)
            self.assertIsNone(c.get("/api/workspace").json()["theme_unknown"], ok)
        self.save(c, None)
        self.assertIsNone(c.get("/api/workspace").json()["theme_unknown"], "no theme at all is not an unknown one")

    def test_the_page_tells_the_person_once_at_load(self):
        tabs = (STATIC / "features" / "tabs.js").read_text(encoding="utf-8")
        self.assertRegex(tabs, r"ws\.theme_unknown && MK\.toast\) MK\.toast\(")

    def test_the_theme_does_not_cost_the_tabs(self):
        c, _ = boot(self.d)
        doc = {"tabs": [{"kind": "file", "path": "src/app/core.py", "line": 3, "scroll": 9}], "theme": "light"}
        self.assertEqual(200, c.post("/api/workspace", json=doc, headers=ORIGIN).status_code)
        w = c.get("/api/workspace").json()
        self.assertEqual(("light", ["file:src/app/core.py"]), (w["theme"], [t["key"] for t in w["tabs"]]))

    def test_the_server_serves_the_tokens_and_the_switch_to_the_page(self):
        c, _ = boot(self.d)
        for name, needle in (("themes.css", 'data-theme="hc-light"'), ("theme.js", "MK.theme")):
            r = c.get("/static/" + name)
            self.assertEqual(200, r.status_code, name)
            self.assertIn(needle, r.text)
        html = c.get("/").text
        self.assertIn("/static/themes.css", html)
        self.assertIn("/static/features/themes.js", html)


# ===================================================================== the page logic under node

def _node(js: str):
    p = subprocess.run(_support.node_argv(js), capture_output=True, text=True, timeout=60,
                       stdin=subprocess.DEVNULL, encoding="utf-8")
    if p.returncode:
        raise AssertionError(p.stderr)
    return json.loads(p.stdout)


THEME_JS = (STATIC / "theme.js").read_text(encoding="utf-8")

#: A page with a controllable OS: `os.light` / `os.more` flip and fire the matchMedia listeners.
STUB = r"""
const os = { light: false, more: false };
const fire = { light: [], more: [] };
const mq = (key) => ({ get matches() { return os[key]; }, addEventListener(_t, f) { fire[key].push(f); } });
const root = { MK: { saveView() { root.saves += 1; } }, saves: 0,
  matchMedia: (q) => (q.includes("color-scheme") ? mq("light") : mq("more")),
  CustomEvent: class { constructor(n, o) { this.type = n; this.detail = o.detail; } },
  document: { documentElement: { dataset: {} }, events: [], dispatchEvent(e) { this.events.push(e.type); } } };
const flip = (k, v) => { os[k] = v; fire[k].forEach((f) => f()); };
"""


def _page(body: str):
    return _node(STUB + "globalThis.window = root;\n" + THEME_JS
                 + "\nconst T = root.MK.theme, H = root.document.documentElement.dataset;\nconst out = {};\n"
                 + body + "\nprocess.stdout.write(JSON.stringify(out));")


@unittest.skipUnless(shutil.which("node"), "node is not on PATH — the page logic runs in a browser")
class TheSwitchResolvesAndFollowsTheSystem(unittest.TestCase):

    def test_system_resolves_scheme_and_contrast_and_a_pick_is_taken_as_it_is(self):
        o = _page(r"""
const grid = {};
for (const light of [false, true]) for (const more of [false, true]) grid[`${light}/${more}`] = T.resolve("system", { light, more });
out.grid = grid;
out.picks = T.CHOICES.slice(1).map((c) => T.resolve(c, { light: true, more: true }));
out.choices = T.CHOICES;
""")
        self.assertEqual({"false/false": "dark", "false/true": "hc-dark", "true/false": "light", "true/true": "hc-light"},
                         o["grid"])
        self.assertEqual(["dark", "light", "hc-dark", "hc-light"], o["picks"])
        self.assertEqual(list(W.THEMES), o["choices"], "theme.js and ide/workspace.py name the same choices")

    def test_following_the_system_re_resolves_live_with_no_reload(self):
        o = _page(r"""
out.start = [H.theme, H.scheme, H.themeChoice];
const seen = []; T.onChange((eff, ch) => seen.push([eff, ch]));
flip("light", true);  out.light = [H.theme, H.scheme];
flip("more", true);   out.hcLight = H.theme;
flip("light", false); out.hcDark = H.theme;
flip("more", false);  out.dark = H.theme;
out.seen = seen; out.events = root.document.events;
""")
        self.assertEqual(["dark", "dark", "system"], o["start"])
        self.assertEqual(["light", "light"], o["light"])
        self.assertEqual(("hc-light", "hc-dark", "dark"), (o["hcLight"], o["hcDark"], o["dark"]))
        self.assertEqual([["light", "system"], ["hc-light", "system"], ["hc-dark", "system"], ["dark", "system"]],
                         o["seen"])
        self.assertEqual(["mokata:theme"] * 4, o["events"])

    def test_a_pick_ignores_the_os_and_back_to_system_follows_it_again(self):
        o = _page(r"""
T.set("light"); flip("light", false); flip("more", true);
out.picked = H.theme;
T.set("system"); out.back = H.theme;
flip("more", false); out.after = H.theme;
""")
        self.assertEqual(("light", "hc-dark", "dark"), (o["picked"], o["back"], o["after"]))

    def test_an_unknown_choice_is_refused_and_changes_nothing(self):
        o = _page(r"""
out.r = ["neon", "", null, 3, "Dark"].map((c) => T.set(c)); out.theme = H.theme; out.saves = root.saves;
""")
        self.assertEqual([False] * 5, o["r"])
        self.assertEqual(("dark", 0), (o["theme"], o["saves"]))

    def test_the_workspace_never_undoes_what_the_person_picked_and_a_pick_is_saved(self):
        o = _page(r"""
out.fromServer = T.set("light", { fromServer: true }); out.afterServer = [H.theme, root.saves];
out.pick = T.set("hc-dark"); out.afterPick = [H.theme, root.saves];
out.late = T.set("dark", { fromServer: true }); out.afterLate = H.theme;
out.same = T.set("hc-dark"); out.saves = root.saves;
""")
        self.assertTrue(o["fromServer"])
        self.assertEqual(["light", 0], o["afterServer"], "restoring the saved theme must not re-save it")
        self.assertEqual(["hc-dark", 1], o["afterPick"], "a pick is saved with the workspace")
        self.assertFalse(o["late"])
        self.assertEqual("hc-dark", o["afterLate"])
        self.assertEqual(1, o["saves"], "picking what is already in force saves nothing")

    def test_one_listener_that_throws_does_not_stop_the_others(self):
        o = _page(r"""
const seen = []; T.onChange(() => { throw new Error("boom"); }); T.onChange((e) => seen.push(e));
T.set("light"); out.seen = seen;
""")
        self.assertEqual(["light"], o["seen"])


@unittest.skipUnless(shutil.which("node"), "node is not on PATH — the page logic runs in a browser")
class ThePaletteOffersEveryTheme(unittest.TestCase):

    def test_five_commands_through_the_real_registry_and_no_new_chord(self):
        reg = (STATIC / "registry.js").read_text(encoding="utf-8")
        feat = (STATIC / "features" / "themes.js").read_text(encoding="utf-8")
        js = ("globalThis.window = globalThis;\n" + reg + "\n" + THEME_JS + "\n"
              + "const toasts = []; window.MK.toast = (t) => toasts.push(t); window.MK.saveView = () => {};\n" + feat + r"""
const R = window.mokataIDE;
const cmds = R.commands().filter((c) => c.feature === "themes");
const out = { titles: cmds.map((c) => c.title), keys: cmds.map((c) => c.keys || null), conflicts: R.conflicts() };
R.run("theme.hc-light");
out.after = window.MK.theme.choice(); out.toasts = toasts;
process.stdout.write(JSON.stringify(out));
""")
        o = _node(js)
        self.assertEqual(sorted(["Theme: Follow the system", "Theme: Dark", "Theme: Light",
                                 "Theme: High contrast (dark)", "Theme: High contrast (light)"]), sorted(o["titles"]))
        self.assertEqual([[]] * 5, o["keys"], "no chord: the palette (Mod+K, `>`) is the keyboard path")
        self.assertEqual([], o["conflicts"])
        self.assertEqual("hc-light", o["after"])
        self.assertEqual(["Theme: High contrast (light)"], o["toasts"])


@unittest.skipUnless(shutil.which("node"), "node is not on PATH — the page logic runs in a browser")
class MermaidIsDrawnInTheThemesColoursAndOnlyHex(unittest.TestCase):

    def _frame(self, theme_json: str):
        src = (STATIC / "mermaid-frame.js").read_text(encoding="utf-8")
        js = r"""
const calls = [], listeners = [];
globalThis.window = { mermaid: { initialize: (c) => calls.push(c), render: async () => ({ svg: "<svg/>" }) },
  addEventListener: (t, f) => listeners.push(f), location: { origin: "o" }, parent: { postMessage() {} } };
window.parent.postMessage = () => {};
""" + src + r"""
(async () => {
  const send = (theme) => listeners[0]({ source: window.parent, origin: "o", data: { id: "a", text: "graph TD;A-->B", theme } });
  for (const t of %s) await send(t);
  process.stdout.write(JSON.stringify(calls.map((c) => ({ theme: c.theme, v: c.themeVariables || null }))));
})();
""" % theme_json
        return _node(js)

    GOOD = ('{dark:true,background:"#111714",text:"#d8e2dc",line:"#b6c2bb",node:"#0e1411",'
            'border:"#2c3a33",note:"#1a2620",accent:"#3df58c"}')

    def test_a_theme_in_hex_becomes_mermaid_base_theme_variables(self):
        c = self._frame("[%s]" % self.GOOD)
        self.assertEqual("base", c[0]["theme"])
        self.assertEqual(("#d8e2dc", "#2c3a33", True), (c[0]["v"]["primaryTextColor"], c[0]["v"]["primaryBorderColor"],
                                                       c[0]["v"]["darkMode"]))

    def test_a_new_theme_re_initialises_and_the_same_theme_does_not(self):
        light = self.GOOD.replace("dark:true", "dark:false").replace("#111714", "#ffffff")
        c = self._frame("[%s, %s, %s]" % (self.GOOD, self.GOOD, light))
        self.assertEqual(2, len(c))
        self.assertEqual("#ffffff", c[1]["v"]["background"])

    def test_anything_that_is_not_hex_never_reaches_mermaid(self):
        evil = self.GOOD.replace('"#d8e2dc"', '"red;} body{background:url(/api/x)"')
        c = self._frame("[%s]" % evil)
        self.assertEqual(("dark", None), (c[0]["theme"], c[0]["v"]))
        c = self._frame('["light", null, {dark:false}]')
        self.assertTrue(all(x["v"] is None for x in c))


if __name__ == "__main__":
    unittest.main()
