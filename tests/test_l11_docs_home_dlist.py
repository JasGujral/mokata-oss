"""0.0.27 L11 — the docs home leads with the headline differentiators, and every one is backed.

The docs-home rule: the landing page (`overrides/home.html`, and the body it renders,
`docs/index.md`) LEADS with D1 (the codebase graph), D2–D4 (memory: self-healing, shared,
captured), D11 (review every decision) and D13 (local-first) — and the per-D rule says a claim
without a command that runs today is not a differentiator. So each of the six names the command
that proves it and the tutorial beat that runs it.

What a reader is protected from, rule by rule:

* the page stops leading with the six (a later section pushed above them, one dropped or reordered);
* a card names a command the CLI no longer accepts (a renamed subcommand, a removed flag);
* a card points at a tutorial beat that no longer exists, or a beat that no longer runs the
  command the card names;
* the page body (`index.md`) and the template disagree about which command proves which claim;
* a copy-paste command in the differentiator tutorials, or the spec it emits, that the CLI would
  refuse — the 0.0.26 task-plan requirement made both tutorials' `spec.json` unrunnable and
  nothing went red (found by the L11 sweep).

The checks parse with the shipped parser (`cli.build_parser`) and the shipped spec parser
(`engine.emit.spec_for_emit`) — never a hand-typed list of command names.
"""

import argparse
import contextlib
import io
import os
import re
import shlex
import unicodedata
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata.cli import build_parser
from mokata.engine.emit import spec_for_emit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = os.path.join(ROOT, "overrides", "home.html")
INDEX = os.path.join(ROOT, "docs", "index.md")
TUTORIAL_REL = "tutorials/differentiators-in-action"
TUTORIAL = os.path.join(ROOT, "docs", "tutorials", "differentiators-in-action.md")
BAD_CHANGE = os.path.join(ROOT, "docs", "tutorials", "catches-a-bad-change.md")

#: The docs-home rule's lead, in order.
LEAD = ["D1", "D2", "D3", "D4", "D11", "D13"]

CARD = re.compile(r'<article class="lp-diff__card" data-d="(D\d+)" data-proof="([^"]+)" '
                  r'data-demo="([^"#]+)#([^"]+)">(.*?)</article>', re.S)
ROW = re.compile(r"^\| (D\d+) \| .+? \| `([^`]+)` \| \[\1\]\(([^)#]+)#([^)]+)\) \|$", re.M)
HEADING = re.compile(r"^(#{2,3}) (.+)$", re.M)


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def slugify(title):
    """Python-Markdown's `toc` slug (the anchor mkdocs renders), over the heading's plain text."""
    text = re.sub(r"[*`]", "", title)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[-\s]+", "-", text)


def sections(md):
    """{anchor: body} for every ##/### heading — the body runs to the next heading of any level."""
    heads = list(HEADING.finditer(md))
    out = {}
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(md)
        out[slugify(m.group(2))] = md[m.end():end]
    return out


def cli_refusal(command):
    """None when the shipped parser accepts `command` (a `mokata …` line), else its complaint."""
    argv = shlex.split(command, comments=True)
    if not argv or argv[0] != "mokata":
        return f"not a mokata command: {command!r}"
    parser = build_parser()
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            parser.parse_args(argv[1:])
    except SystemExit as exc:
        if exc.code:
            return err.getvalue().strip().splitlines()[-1] if err.getvalue() else f"exit {exc.code}"
    # argparse accepts a unique PREFIX of a flag, so `--preview` still parses after the flag is
    # renamed `--preview-plan`. A doc must spell the flag the CLI actually has.
    bad = _unknown_skill(argv[1:])
    if bad:
        return bad
    known = _option_strings(parser, argv[1:])
    for tok in argv[1:]:
        if tok.startswith("--") and tok.split("=", 1)[0] not in known:
            return f"{tok.split('=', 1)[0]} is not a flag of `{command}` (only a prefix of one)"
    return None


def _unknown_skill(args):
    """`mokata run <name>` parses for ANY word (argparse sees a string), so `run develope` passed.
    The name must be a skill the shipped registry has; the refusal lists the valid ones."""
    from mokata.skills import SKILL_NAMES
    words = [a for a in args if not a.startswith("-")]
    if len(words) >= 2 and words[0] == "run" and words[1] not in SKILL_NAMES:
        return f"no skill {words[1]!r}; available: {', '.join(SKILL_NAMES)}"
    return None


def _option_strings(parser, args):
    """Every option string on the parser chain `args` walks down (root → sub → sub-sub)."""
    known = set(parser._option_string_actions)
    for tok in args:
        if tok.startswith("-"):
            continue
        subs = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
        if not subs or tok not in subs[0].choices:
            continue
        parser = subs[0].choices[tok]
        known |= set(parser._option_string_actions)
    return known


def home_cards(html):
    return [{"d": m.group(1), "proof": m.group(2), "page": m.group(3), "anchor": m.group(4),
             "body": m.group(5)} for m in CARD.finditer(html)]


def index_rows(md):
    return [{"d": m.group(1), "proof": m.group(2), "page": m.group(3), "anchor": m.group(4)}
            for m in ROW.finditer(md)]


def bash_fences(md):
    return re.findall(r"```bash\n(.*?)```", md, re.S)


def mokata_lines(fence):
    """The `mokata …` invocations a reader copy-pastes from one bash fence, redirections cut."""
    out = []
    for raw in fence.splitlines():
        line = raw.strip()
        if not line.startswith("mokata "):
            continue
        line = re.split(r"\s(?:>|2>|\||&&)", line)[0]
        out.append(line.replace("$ID", "p-0123456789ab"))
    return out


def spec_payloads(md):
    """Every `spec.json` a tutorial writes with a heredoc and then emits."""
    return re.findall(r"cat > spec\.json <<'JSON'\n(.*?)\nJSON\n", md, re.S)


class TheHomeLeadsWithTheHeadlineDifferentiators(unittest.TestCase):
    def test_the_first_section_after_the_hero_is_the_differentiator_lead_in_order(self):
        """The page leads with D1, D2, D3, D4, D11, D13 — nothing sits between the hero and them."""
        html = _read(HOME)
        hero_end = html.index("</section>", html.index('class="lp-hero"'))
        first = re.search(r"<section\b[^>]*>", html[hero_end + len("</section>"):])
        self.assertIsNotNone(first)
        self.assertIn('id="different"', first.group(0),
                      "the first section after the hero must be the differentiator lead")
        self.assertEqual(LEAD, [c["d"] for c in home_cards(html)])

    def test_the_page_body_leads_with_the_same_six_before_anything_else(self):
        """index.md's first section is the same lead, same order, before 'About mokata'."""
        md = _read(INDEX)
        first_h2 = re.search(r"^## (.+)$", md, re.M)
        self.assertEqual("What makes mokata different", first_h2.group(1))
        self.assertEqual(LEAD, [r["d"] for r in index_rows(md)])


class EveryLeadClaimIsBacked(unittest.TestCase):
    def test_every_card_names_a_command_the_cli_accepts(self):
        """A card whose command the CLI no longer accepts is an unbacked claim."""
        for c in home_cards(_read(HOME)):
            with self.subTest(d=c["d"]):
                self.assertIsNone(cli_refusal(c["proof"]), c["proof"])

    def test_the_visible_command_is_the_attested_one(self):
        """What the reader sees on the card is the exact command the check graded."""
        for c in home_cards(_read(HOME)):
            with self.subTest(d=c["d"]):
                self.assertIn(f"<code>{c['proof']}</code>", c["body"])
                self.assertIn(f'href="{c["page"]}#{c["anchor"]}"', c["body"])

    def test_every_card_points_at_a_tutorial_beat_that_runs_its_command(self):
        """The 'see it run' link resolves to a heading, and that beat contains the command."""
        beats = sections(_read(TUTORIAL))
        for c in home_cards(_read(HOME)):
            with self.subTest(d=c["d"]):
                self.assertEqual(TUTORIAL_REL + "/", c["page"])
                self.assertIn(c["anchor"], beats, f"no tutorial heading renders #{c['anchor']}")
                self.assertTrue(c["anchor"].startswith(c["d"].lower() + "-"),
                                f"{c['d']} links to another beat: #{c['anchor']}")
                self.assertIn(c["proof"], beats[c["anchor"]],
                              f"the {c['d']} beat no longer shows `{c['proof']}`")

    def test_the_page_body_and_the_template_name_the_same_proof_for_each_claim(self):
        """index.md and home.html never disagree on which command proves which D."""
        cards = {c["d"]: (c["proof"], c["anchor"]) for c in home_cards(_read(HOME))}
        for r in index_rows(_read(INDEX)):
            with self.subTest(d=r["d"]):
                self.assertEqual("tutorials/differentiators-in-action.md", r["page"])
                self.assertEqual(cards[r["d"]], (r["proof"], r["anchor"]))


class AnUnknownSkillNameIsRefused(unittest.TestCase):
    def test_a_typo_in_a_run_command_is_refused_and_lists_the_valid_names(self):
        from mokata.skills import SKILL_NAMES
        why = cli_refusal("mokata run develope")
        self.assertIsNotNone(why)
        for name in SKILL_NAMES:
            self.assertIn(name, why)
        self.assertIsNone(cli_refusal("mokata run develop"))


class TheDifferentiatorDemosStayRunnable(unittest.TestCase):
    def test_every_copy_paste_mokata_command_in_the_demos_parses(self):
        """Each `mokata …` line in a bash fence of the two demo tutorials is one the CLI accepts."""
        for path in (TUTORIAL, BAD_CHANGE):
            for fence in bash_fences(_read(path)):
                for line in mokata_lines(fence):
                    with self.subTest(doc=os.path.basename(path), line=line):
                        self.assertIsNone(cli_refusal(line))

    def test_the_demos_find_something_to_check(self):
        """The fence scan sees the demos at all (a reformatted fence must not pass vacuously)."""
        lines = [ln for fence in bash_fences(_read(TUTORIAL)) for ln in mokata_lines(fence)]
        self.assertGreaterEqual(len(lines), 30)
        self.assertEqual(1, len(spec_payloads(_read(TUTORIAL))))
        self.assertEqual(1, len(spec_payloads(_read(BAD_CHANGE))))

    def test_every_spec_a_demo_emits_is_one_the_emit_surface_accepts(self):
        """The spec.json a tutorial writes passes the same parse `mokata spec emit` runs first."""
        import json
        for path in (TUTORIAL, BAD_CHANGE):
            for raw in spec_payloads(_read(path)):
                with self.subTest(doc=os.path.basename(path)):
                    spec_for_emit(json.loads(raw))


if __name__ == "__main__":
    unittest.main()
