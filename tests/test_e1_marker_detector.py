"""0.0.28 E1 (REDACT-MANGLED-KEY) — the one marker detector, row by row (note §5).

The promise: a private key whose marker is damaged — no trailing dashes, one or two, a cut BEGIN, a
Unicode dash, a zero-width or no-break space, a marker split across string literals, or no marker at
all — is replaced WHOLE at every line site and counted, exactly as a strict one is. Each row runs
the key through every line site (`test_e1_every_site_redacts.LineSites.assert_line_sites`).

Rows: E1.Strict, E1.Mangled.{dashes,cut,unicode,split}, E1.Unmarked, E1.Glued, E1.Formats,
E1.OneDetector, E1.Counted (its browser half is `tests/e2e_ide/test_e1_redacted_label.py`).

Fixtures are assembled from character codes (`_e1_keys`): no spelling of this file is a marker.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import ast
import base64
import os
import re
import unittest

import _support  # noqa: F401

import _e1_keys as K
from test_e1_every_site_redacts import LineSites, _put, _tmp, key_kinds

from mokata.explain import ground as G
from mokata.govern import secrets as S
from mokata.govern.secrets import KEY_FIRM, KEY_TOLERANT
from mokata.ide import context as CX
from mokata.ide import usages as U

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
NL = chr(10)


def keytext(begin, body, end, before="a = 1", after="b = 2", nl=NL):
    return nl.join([before, begin, *body, end, after]) + nl


class Strict(LineSites):
    """E1.Strict — every type word, widths 8/16/64/1000, LF and CRLF: 0 body lines survive."""

    def test_every_type_word_at_every_width(self):
        for label in K.TYPE_LABELS:
            for width in (8, 16, 64, 1000):
                begin, end = K.pair(label)
                body = K.random_body(width, 6, f"strict-{label}-{width}")
                self.assert_line_sites(keytext(begin, body, end), body + [begin, end],
                                       f"{label!r} {width}", copy=width == 16)
                self.assertIn(KEY_FIRM, key_kinds(begin), label)

    def test_crlf(self):
        begin, end = K.pair(K.OPENSSH)
        body = K.random_body(64, 8, "crlf")
        self.assert_line_sites(keytext(begin, body, end, nl="\r\n"), body, "crlf")

    def test_pgp_block_form_is_firm(self):
        begin, end = K.pair(K.w(80, 71, 80), block=True)
        self.assertEqual([KEY_FIRM], key_kinds(begin))


class MangledDashes(LineSites):
    """E1.Mangled.dashes — trailing 0/1/2, leading 0/1/2 (upper-case BEGIN when < 3)."""

    def test_every_dash_count(self):
        for lead in range(0, 6):
            for trail in range(0, 6):
                if lead >= 3 and trail >= 3:
                    continue                      # the strict row
                begin, end = K.pair(K.RSA, lead="-" * lead, trail="-" * trail)
                body = K.random_body(16, 6, f"d{lead}{trail}")
                self.assert_line_sites(keytext(begin, body, end), body, f"lead {lead} trail {trail}",
                                       copy=False)
                kinds = key_kinds(begin)
                self.assertTrue(kinds, (lead, trail))
                # FIRM: >= 3 leading dashes and PRIVATE KEY complete, ending the line or followed
                # by a dash run (rule fold H1); every other spelling is TOLERANT
                self.assertEqual(KEY_FIRM if lead >= 3 else KEY_TOLERANT, kinds[0], (lead, trail))

    def test_lower_case_needs_three_dashes(self):
        """E-Q3: a dash-less lower-case marker is prose, not a key — the keyword AND the marker's
        own words each upper-case (either one lower-case is prose)."""
        low = K.marker(K.BEGIN, K.RSA, lead="", trail="").lower()
        self.assertEqual([], key_kinds(low))
        self.assertEqual([KEY_FIRM], key_kinds(K.marker(K.BEGIN, K.RSA).lower()))
        upper = K.marker(K.BEGIN, K.RSA, lead="", trail="")
        self.assertEqual([], key_kinds(K.BEGIN.lower() + upper[len(K.BEGIN):]), "keyword lower")
        self.assertEqual([], key_kinds(K.BEGIN + upper[len(K.BEGIN):].lower()), "words lower")

    def test_a_public_word_elsewhere_on_the_line_does_not_cancel_the_marker(self):
        """The public-word exclusion reads THIS marker's type words, never the rest of the line."""
        self.assertEqual([KEY_FIRM], key_kinds(K.marker(K.BEGIN) + "  # not a PUBLIC key"))


class MangledCut(LineSites):
    """E1.Mangled.cut — BEGIN cut at the line end, at a type word, inside PRIVATE, inside KEY."""

    def test_every_cut(self):
        cuts = {"after-begin": K.D5 + K.BEGIN,
                "after-type": K.D5 + K.BEGIN + " " + K.RSA,
                "type-prefix": K.D5 + K.BEGIN + " " + K.RSA[:2],
                "priv": K.D5 + K.BEGIN + " " + K.RSA + " " + K.PRIVATE[:4],
                "private": K.D5 + K.BEGIN + " " + K.RSA + " " + K.PRIVATE,
                "private-k": K.D5 + K.BEGIN + " " + K.RSA + " " + K.PRIVATE + " " + K.KEY[:1],
                # the cut shows only once the literal junction is glued (`… RSA " + "PRIV"`)
                "glued-cut": 'k = "' + K.D5 + K.BEGIN + " " + K.RSA + ' " + "' + K.PRIVATE[:4] + '"'}
        end = K.marker(K.END, K.RSA)
        for name, begin in cuts.items():
            body = K.random_body(64, 6, name)
            self.assert_line_sites(keytext(begin, body, end), body, name, copy=False)
            self.assertEqual([KEY_TOLERANT], key_kinds(begin), name)

    def test_a_cut_in_prose_replaces_only_its_own_line(self):
        """Rule fold H2: a tolerant BEGIN opens a key only when a body line follows it."""
        text = "notes" + NL + K.D5 + K.BEGIN + NL + "the next line is prose, not a body" + NL
        out, n = CX._redact(text, "notes.md")
        self.assertEqual(1, n)
        self.assertIn("the next line is prose", out)


class MangledUnicode(LineSites):
    """E1.Mangled.unicode — Unicode dashes, zero-width, NBSP, thin space, soft hyphen, fullwidth,
    a zero-width space inside BEGIN."""

    def test_every_spelling(self):
        en, em, minus, fwh, small = "–", "—", "−", "－", "﹣"
        zw, nbsp, thin, shy = "​", " ", " ", "­"
        fw_begin = "".join(chr(ord(c) + 0xFEE0) for c in K.BEGIN)
        rows = {
            "en": K.marker(K.BEGIN, K.RSA, lead=en * 5, trail=en * 5),
            "em": K.marker(K.BEGIN, K.RSA, lead=em * 5, trail=em * 5),
            "minus": K.marker(K.BEGIN, K.RSA, lead=minus * 5, trail=minus * 5),
            "fullwidth-hyphen": K.marker(K.BEGIN, K.RSA, lead=fwh * 5, trail=fwh * 5),
            "small-hyphen": K.marker(K.BEGIN, K.RSA, lead=small * 5, trail=small * 5),
            "nbsp": K.marker(K.BEGIN, K.RSA, sep=nbsp),
            "thin": K.marker(K.BEGIN, K.RSA, sep=thin),
            "zero-width-in-dashes": K.marker(K.BEGIN, K.RSA, lead="-" + zw + "----"),
            "soft-hyphen": K.marker(K.BEGIN, K.RSA, lead="--" + shy + "---"),
            "split-letter": K.D5 + K.BEGIN[:3] + zw + K.BEGIN[3:] + " " + K.RSA + " "
            + K.PRIVATE + " " + K.KEY + K.D5,
            "fullwidth-begin": K.D5 + fw_begin + " " + K.RSA + " " + K.PRIVATE + " " + K.KEY + K.D5,
            # a space separator with no NFKC decomposition: only the Zs mapping reads it
            "ogham-space": K.marker(K.BEGIN, K.RSA, sep="\u1680"),
            # a zero-width space inside PRIVATE: the RAW line holds no `PRIV`
            "zw-in-private": K.D5 + K.BEGIN + " " + K.RSA + " " + K.PRIVATE[:3] + zw + K.PRIVATE[3:]
            + " " + K.KEY + K.D5,
            # a cut BEGIN after em dashes: dashed only once the dashes are mapped
            "em-dash-cut": em * 5 + K.BEGIN,
            # lower case after em dashes: E-Q3 lets it be a key only once the dashes are mapped
            "em-dash-lower": K.marker(K.BEGIN, K.RSA, lead=em * 5, trail=em * 5).lower(),
        }
        firm = {"en", "em", "minus", "fullwidth-hyphen", "small-hyphen", "nbsp", "thin",
                "ogham-space", "zero-width-in-dashes", "soft-hyphen", "split-letter",
                "fullwidth-begin", "zw-in-private", "em-dash-lower"}
        end = K.marker(K.END, K.RSA, lead=em * 5, trail=em * 5)
        for name, begin in rows.items():
            body = K.random_body(16, 6, name)
            self.assert_line_sites(keytext(begin, body, end), body, name, copy=False)
            self.assertEqual([KEY_FIRM if name in firm else KEY_TOLERANT], key_kinds(begin), name)

    def test_raw_coordinates(self):
        """The detector returns RAW indices (`pem_state_before` slices the raw text)."""
        zw = "​"
        line = "x" + zw * 3 + "= " + K.D5 + K.BEGIN + " " + K.RSA + " " + K.PRIVATE + " " + K.KEY + K.D5
        m = S.key_markers(line)[0]
        self.assertEqual(line.index(K.D5), m.start)
        self.assertEqual(len(line), m.end)


SPLITS = {
    # one row per literal-joining form; `{a}`/`{b}` are the two halves of the BEGIN marker
    "python-parenthesised": ('k = ("{a}"', '     "{b}\\n"'),
    "js-leading-plus": ('var k = "{a}"', '  + "{b}\\n"'),
    "java-trailing-plus": ('String k = "{a}" +', '  "{b}\\n";'),
    "go-plus": ('k := "{a}" +', '"{b}\\n"'),
    "php-dot": ("$k = '{a}' .", "'{b}';"),
    "vb-amp": ('k = "{a}" &', '"{b}"'),
    "sql-pipes": ("select '{a}' ||", "'{b}';"),
    "lua-dots": ('k = "{a}" ..', '"{b}"'),
    "c-adjacent": ('char *k = "{a}"', '"{b}\\n";'),
    "rust-concat": ('let k = concat!("{a}",', '"{b}");'),
    "continuation": ('K="{a}\\', '{b}"'),
    # the line above ends in no continuation character: only the LEADING glue joins the two
    "underscore-continuation": ('k = "{a}" _', '"{b}"'),
}


class MangledSplit(LineSites):
    """E1.Mangled.split — a marker split across string literals, one row per form. The halves
    are cut mid-word (`…PRIV` / `ATE KEY…`) so neither line is a marker alone."""

    def test_every_form(self):
        a = K.D5 + K.BEGIN + " " + K.RSA + " " + K.PRIVATE[:4]
        b = K.PRIVATE[4:] + " " + K.KEY + K.D5
        ea = K.D5 + K.END + " " + K.RSA + " " + K.PRIVATE[:4]
        for name, (first, second) in SPLITS.items():
            body = ['"%s\\n"' % ln for ln in K.random_body(64, 6, name)]
            lines = ["x = 0", first.format(a=a, b=b), second.format(a=a, b=b), *body,
                     first.format(a=ea, b=b), second.format(a=ea, b=b), "y = 1"]
            text = NL.join(lines) + NL
            self.assert_line_sites(text, [x.strip('"\\n') for x in body], name, copy=False)
            kinds = [f.kind for f in S.scan(text) if f.line == 3]
            self.assertIn(KEY_TOLERANT, kinds, f"{name}: the completing line is not flagged")

    def test_a_marker_split_mid_keyword_completes_on_the_second_line(self):
        a, b = K.D5 + K.BEGIN[:3], K.BEGIN[3:] + " " + K.RSA + " " + K.PRIVATE + " " + K.KEY + K.D5
        text = 'k = ("%s"%s     "%s")%s' % (a, NL, b, NL)
        self.assertEqual([2], [f.line for f in S.scan(text) if f.kind == KEY_TOLERANT])

    def test_the_disclosed_misses_are_misses(self):
        """Disclosed (note §3c.6): shell `"$A$B"`, a `%s` template, and a three-way split whose
        middle fragment carries no keyword. The MARKER is missed; a DER body is still caught by
        the unmarked-body backstop, so the key does not leak."""
        a = K.D5 + K.BEGIN[:1]
        misses = {
            "shell-vars": ['A="' + K.D5 + K.BEGIN[:3] + '"', 'B="' + K.BEGIN[3:] + " " + K.RSA + '"',
                           'echo "$A$B ' + K.PRIVATE + " " + K.KEY + K.D5 + '"'],
            "template": ['fmt = "' + K.D5 + '%s ' + K.RSA + " " + K.PRIVATE + " " + K.KEY + K.D5 + '"'],
            "three-way": ['k = ("' + a + '"', '  "' + K.BEGIN[1:3] + '"',
                          '  "' + K.BEGIN[3:] + " " + K.RSA + " " + K.PRIVATE + " " + K.KEY + K.D5 + '")'],
        }
        for name, lines in misses.items():
            self.assertEqual([], key_kinds(NL.join(lines)), f"{name} is no longer a miss: "
                             "move it out of the disclosed list")
            body = K.b64_lines(K.KEY_BODIES["rsa2048"], 64)
            out, n = CX._redact(NL.join(lines + body) + NL, "x.sh")
            self.assertEqual([], [b for b in body if b in out], name)


#: Round 2 (verifier High, aa19854): the first body line behind a code prefix
PREFIXES = {
    "assign": ("k = ", ""),
    "triple-quote": ('k = ("""', ""),
    "single-quote": ("k = '", ""),
    "yaml-scalar": ("key: ", ""),
    "yaml-block": ("key: |" + NL + "  ", "  "),
    "comment": ("# ", ""),
    "json": ('"k": "', ""),
}


def _prefixed_keys():
    """{name: (der, type label)}: RSA PKCS#1, PKCS#8 (RSA, EC, Ed25519), SEC1 EC, OpenSSH —
    synthesised, plus the real openssl keys when openssl is on PATH."""
    import base64
    out = {"rsa-pkcs1": (K.KEY_BODIES["rsa2048"], K.RSA),
           "rsa-pkcs8": (K.KEY_BODIES["rsa2048-pkcs8"], ""),
           "ec-sec1": (K.KEY_BODIES["ec-P-256"], K.w(69, 67)),
           "ec-pkcs8": (K.KEY_BODIES["ec-P-256-pkcs8"], ""),
           "openssh": (K.KEY_BODIES["openssh"], K.OPENSSH),
           # a MARKED key whose body is not DER (PGP, legacy-encrypted, a damaged copy): only
           # the marker can hold it back, so the pending BEGIN must open on the prefixed line
           "non-der": (None, K.RSA)}
    for name, pem in K.real_keys().items():
        b = "".join(ln for ln in pem.splitlines() if ln and not ln.startswith("-"))
        out["openssl-" + name] = (base64.b64decode(b), "")
    return out


def _prefixed_headers(label):
    """{header form: (header lines, end lines)} for one type label."""
    words = (label + " " if label else "") + K.PRIVATE + " " + K.KEY
    strict = K.D5 + K.BEGIN + " " + words + K.D5
    end = K.D5 + K.END + " " + words + K.D5
    cut = len(strict) // 2
    ecut = len(end) // 2
    return {
        "split": (['x = "' + strict[:cut] + '" "' + strict[cut:] + '"'],
                  ['y = "' + end[:ecut] + '" "' + end[ecut:] + '"']),
        "dashless": ([K.BEGIN + " " + words], [K.END + " " + words]),
        "cut": ([K.D5 + K.BEGIN], []),
        "strict": ([strict], [end]),
        "unmarked": ([], []),
    }


class PrefixedHead(unittest.TestCase):
    """E1.Mangled.prefixed (round 2) — {split, dash-less, cut, strict, none} header x the FIRST body
    line behind a code prefix (`k = `, `k = (\"\"\"`, `k = '`, YAML scalar and block, `# `, a
    JSON value) x {RSA PKCS#1, PKCS#8 RSA/EC, SEC1 EC, OpenSSH, real openssl keys} x widths
    {64, 76}. Through the walker alone, `usages.redact_text`, `context._redact` and
    `ground.redact`: no body line survives, and the count is > 0 or the text is refused. And the
    gates' whole-text scan flags every row as a key (one detector: the egress walker and the
    gate agree)."""

    SITES = {
        "walker": lambda t: ("".join(U.pem_lines(t.splitlines(keepends=True), {}, "src/k.py")), 1),
        "usages": lambda t: U.redact_text(t, "src/k.py"),
        "context": lambda t: CX._redact(t, "src/k.py"),
        "ground": lambda t: G.redact(t),
    }

    def _survivors(self, out, body):
        lines = {ln.strip() for ln in out.splitlines()}
        return [b for b in body if b in lines or (len(b) >= 16 and b in out)]

    def test_every_pairing(self):
        bad = []
        rows = 0
        for kname, (der, label) in _prefixed_keys().items():
            for width in (64, 76):
                body = (K.b64_lines(der, width) if der is not None
                        else K.random_body(width, 12, "non-der"))
                for hname, (head, tail) in _prefixed_headers(label).items():
                    if der is None and hname == "unmarked":
                        continue                         # not a key: nothing marks it
                    for pname, (prefix, indent) in PREFIXES.items():
                        # a short base64 value just above: NOT part of the key (the prefixed
                        # line starts a run of its own; nothing above it is replaced)
                        lines = ["import os", "v = 1", "QUJDRA==", *head, prefix + body[0],
                                 *[indent + b for b in body[1:]], *tail, "z = 3"]
                        text = NL.join(lines) + NL
                        rows += 1
                        if not key_kinds(text):
                            bad.append((kname, width, hname, pname, "scan", 0, 0))
                        for site, fn in self.SITES.items():
                            try:
                                out, n = fn(text)
                            except (U.RefRefused, CX.ContextRefused):
                                continue
                            s = self._survivors(out, body)
                            if s or n <= 0:
                                bad.append((kname, width, hname, pname, site, len(s), n))
                            if out.splitlines()[2] != "QUJDRA==":
                                bad.append((kname, width, hname, pname, site, "above", n))
        self.assertGreaterEqual(rows, (5 * 5 + 4) * 7 * 2)
        self.assertEqual([], bad, f"{len(bad)} leaking (key, width, header, prefix, site, "
                                  f"survivors, count) of {rows} rows x 4 sites")

    def test_an_escaped_key_whose_line_ends_in_its_body_is_still_the_escaped_key(self):
        """A one-line JSON value that ENDS in the key's last body line: the tail behind the
        backslash is not a prefixed body line — the escaped view judges the line."""
        body = K.b64_lines(K.KEY_BODIES["rsa2048"], 64)
        line = '"private_key": "' + "\\n".join(body) + '"'
        self.assertEqual([KEY_TOLERANT], key_kinds(line + NL), "the gates' whole-text scan")
        out, n = U.redact_text("{" + NL + line + NL + "}" + NL, "k.json")
        self.assertEqual([], [b for b in body if b in out])

    def test_a_prefixed_line_after_a_tolerant_begin_in_prose_is_not_a_key(self):
        """Controls: a dash-less BEGIN in prose, then a code line ending in a WORD or a short
        value, replaces its own line only."""
        begin = K.BEGIN + " " + K.RSA + " " + K.PRIVATE + " " + K.KEY
        words = ("x = Ab1cD2",                                  # short: under the minimum
                 "x = " + "Super" + "cali" * 5 + "Docious",      # a word: letters only
                 "name = " + "very" * 8 + "1234",                # one case
                 "LABEL = " + "LONG" * 8 + "99")                 # one case
        for after in ("value = internationalization", "x = 'abc'", "# see the README section",
                      "url = https://example.com/path/to/thing", *words):
            text = NL.join(["intro", begin, after, "end"]) + NL
            walked = "".join(U.pem_lines(text.splitlines(keepends=True), {}, "README.md"))
            self.assertIn(NL + after + NL, walked, after)


#: Round 3 (re-verify Medium, bd73199): the verifier's widened OPENING family x the CLOSING suffix
OPENERS = {
    "go-raw": "k := `",
    "rust-raw": 'let k = r#"',
    "c-string": 'const char *k = "',
    "f-string": 'k = f"{x}',
    "heredoc": "cat <<EOF >k" + NL,
    "md-table": "| key | ",
    "b64-looking": "aGVsbG8= ",
    "long-300": "x" * 300 + " ",
    "triple-quote": 'k = """',
    "yaml": "key: ",
    "json": '{"k": "',
    "env": "KEY=",
}
CLOSERS = {
    "none": "", "dq": '"', "dq-semi": '";', "rust-raw": '"#;', "c-cont": '" \\',
    "cont": " \\", "md-table": " |", "paren-comma": '"),', "dq-comma": '",', "backtick": "`",
    "eof-line": NL + "EOF",
}


class SuffixedTail(unittest.TestCase):
    """E1.Mangled.suffixed (round 3) — the closing side of round 2's prefixed head: {12 opening
    prefixes} x {11 closing suffixes on the LAST body line} x {header none, strict, dash-less,
    cut, split} x 4 real keys (RSA PKCS#1, RSA PKCS#8, EC SEC1, OpenSSH Ed25519; generated now,
    synthesised DER when a tool is missing) x widths {64, 76}, through the walker,
    `usages.redact_text`, `context._redact` and `ground.redact`. A row passes when no line that
    holds body text comes out unchanged and the count is > 0 (or the text is refused); and when a
    short base64 value two lines above is untouched."""

    SITES = PrefixedHead.SITES

    @classmethod
    def setUpClass(cls):
        cls.keys = K.four_real_keys()

    def _rows(self, der, label, width):
        body = K.b64_lines(der, width)
        for hname, (head, tail) in _prefixed_headers(label).items():
            for oname, opener in OPENERS.items():
                for cname, closer in CLOSERS.items():
                    text_lines = (["import os", "v = 1", "QUJDRA==", *head]
                                  + (opener + body[0]).split(NL) + body[1:-1]
                                  + (body[-1] + closer).split(NL) + tail + ["z = 3"])
                    yield hname, oname, cname, text_lines, body

    def _check(self, rows, bad):
        n = 0
        for name, lines, body in rows:
            text = NL.join(lines) + NL
            held = [i for i, ln in enumerate(lines) if any(b in ln for b in body if len(b) >= 4)]
            n += 1
            for site, fn in self.SITES.items():
                try:
                    out, cnt = fn(text)
                except (U.RefRefused, CX.ContextRefused):
                    continue
                got = out.split(NL)
                s = [i for i in held if got[i] == lines[i]]
                if s or cnt <= 0 or got[2] != "QUJDRA==":
                    bad.append((*name, site, [lines[i][-30:] for i in s], cnt))
        return n

    def test_every_pairing(self):
        bad, rows, real = [], [], []
        for kname, (der, is_real) in self.keys.items():
            real.append((kname, is_real))
            label = {"rsa-pkcs1": K.RSA, "ec-sec1": K.w(69, 67),
                     "openssh-ed25519": K.OPENSSH}.get(kname, "")
            for width in (64, 76):
                for hname, oname, cname, lines, body in self._rows(der, label, width):
                    rows.append(((kname, width, hname, oname, cname), lines, body))
        n = self._check(rows, bad)
        self.assertEqual(n, 4 * 2 * 5 * len(OPENERS) * len(CLOSERS))
        self.assertEqual([], bad, f"{len(bad)} leaking (key, width, header, opener, closer, "
                                  f"site, survivors, count) of {n} rows x 4 sites; keys {real}")

    def test_a_whole_key_on_one_line_opened_and_closed_by_code(self):
        """The opener and the closer on ONE line: a headerless key written as one literal
        (`let k = r#"MII…"#;`) — the closing glue is stripped from the prefixed tail too."""
        bad, rows = [], []
        for kname, (der, _real) in self.keys.items():
            one = "".join(K.b64_lines(der, 64))
            for oname, opener in OPENERS.items():
                for cname, closer in CLOSERS.items():
                    lines = (["import os", "v = 1", "QUJDRA=="]
                             + (opener + one + closer).split(NL) + ["z = 3"])
                    rows.append(((kname, oname, cname), lines, [one]))
        self._check(rows, bad)
        self.assertEqual([], bad)

    def test_a_body_line_split_mid_line_across_two_literals(self):
        """The round-2 Low: `k = "<line 1>" "<line 2, first half>" "<second half>"` in a
        headerless key — the glued view of the one line carries the DER head."""
        bad = []
        rows = []
        for kname, (der, _real) in self.keys.items():
            for width in (64, 76):
                body = K.b64_lines(der, width)
                for cut in (8, 30, width - 8):
                    rows.append(self._midsplit(kname, body, width, cut))
        # the second half itself opening a candidate (`M…`): the glued head still wins when it
        # is a private-key head — deterministic rows from the synthesised bodies
        opening = []
        for kname, der in K.KEY_BODIES.items():
            body = K.b64_lines(der, 64)
            cut = body[1].find("M", 8, 64 - S.B64_TAIL_MIN) if len(body) > 2 else -1
            if cut > 0:
                opening.append(self._midsplit(kname, body, 64, cut))
        self.assertTrue(opening, "no synthesised body has an M to cut at")
        # a LATER body line split mid-line on its own line (`"…" "…"`): it stays in the run
        middle = []
        for kname, (der, _real) in self.keys.items():
            for width in (64, 76):
                body = K.b64_lines(der, width)
                for i in range(1, min(4, len(body) - 1)):
                    for cut in (8, width // 2):
                        split = '"' + body[i][:cut] + '" "' + body[i][cut:] + '"'
                        lines = (["import os", "v = 1", "QUJDRA==", 'k = "' + body[0]]
                                 + body[1:i] + [split] + body[i + 1:] + ["z = 3"])
                        middle.append(((kname, width, i, cut), lines,
                                       body[:i] + [body[i][cut:]] + body[i + 1:]))
        self.assertTrue(middle)
        self._check(rows + opening + middle, bad)
        self.assertEqual([], bad)

    @staticmethod
    def _midsplit(kname, body, width, cut):
        first = 'k = "' + body[0] + '" "' + body[1][:cut] + '" "' + body[1][cut:] + '"'
        lines = ["import os", "v = 1", "QUJDRA==", first, *body[2:], "z = 3"]
        return ((kname, width, cut), lines, body[2:] + [body[0], body[1][cut:]])


def _verifier_closers():
    """The re-verify's 36 closers (round 3, `r3a.py`), rebuilt here from their parts."""
    q, bs, n = '"', chr(92), chr(92) + "n"
    return [q, "'", "`", ";", q + ",", "',", q + ");", q + "),", q + ")", q + "]", q + "]);",
            q + "}", q + "},", q + " +", q + " " + bs, " " + bs, bs, q + "#;", q + "#", "|",
            " |", q + " |", " | ", " || ", q + n + q, n + q, n, n + q + " +", n + q + ";",
            q * 3, "'" * 3, q * 3 + ")", "'" * 3 + ")", "`;", "`,", "` +", "),", ")", ");",
            q + ",\t", q + "\t// c", q + "  # c", q + " /* c */", q + " // c", " # c", " // c",
            q + ";}", ">", "</s>", q + "</pre>", q + "/>", "] ", q + ",\r"]


def _generated_closers():
    """Closers GENERATED from the parts code wraps a literal in — an escape, a quote, brackets,
    a terminator, a trailing comment — not listed: every part of each category appears, and a
    seeded walk over the product adds the combinations."""
    import random
    esc = ["", chr(92) + "n", chr(92) + "r" + chr(92) + "n", chr(92) + "t"]
    quote = ["", '"', "'", "`", '"' * 3, "'#", '"#']
    brack = ["", ")", "]", "}", "])", ")]", "})", "]}", ")))", ">", "/>", "</pre>", "</s>",
             "</code>"]
    term = ["", ";", ",", " +", " .", " ..", " &", " " + chr(92), " |", " ||", ":", " =>"]
    comment = ["", " // c", " # c", " /* c */", "\t// c", " -- c", " <!-- c -->", " ; c",
               " % c", "  # noqa: E501"]
    out = set()
    for cat in (esc, quote, brack, term, comment):
        for part in cat:
            out.add(part)
    r = random.Random(28)
    while len(out) < 160:
        out.add(r.choice(esc) + r.choice(quote) + r.choice(brack) + r.choice(term)
                + r.choice(comment))
    out.discard("")
    return sorted(out)


#: the re-verify's 9 opening prefixes (round 3)
R3_PREFIXES = ['k = "', 'let k = r#"', "", "'", "`", "| a | ", '  - "', '{"k":"', "x=<<EOF" + NL]


class GlueIsAClass(unittest.TestCase):
    """E1.Mangled.suffixed, round 4 — closing glue as a CLASS, not a list. A headerless key whose
    first line has a code prefix, with ANY closer: the re-verify's 36 plus closers generated from
    the parts code wraps a literal in (escape, quote, bracket, terminator, comment), on the LAST
    line, on EVERY line, or on the MIDDLE line, x the re-verify's 9 prefixes x 4 real keys. Through
    the walker and `usages.redact_text` (`context._redact` and `ground.redact` are thin callers of
    it; they run on the verifier's 36 in `SuffixedTail`'s sites below): no line holding body text
    survives, the count is > 0, and the legitimate line after the key is NOT redacted."""

    @classmethod
    def setUpClass(cls):
        cls.keys = K.four_real_keys()

    SITES = {"walker": PrefixedHead.SITES["walker"], "usages": PrefixedHead.SITES["usages"]}

    def _rows(self, closers, sites):
        bad, n = [], 0
        for kname, (der, _real) in self.keys.items():
            body = K.b64_lines(der, 64)
            for pre in R3_PREFIXES:
                for c in closers:
                    for where in ("last", "all", "mid"):
                        v = [pre + body[0]] + body[1:]
                        if where == "last":
                            v[-1] += c
                        elif where == "all":
                            v = [x + c for x in v]
                        else:
                            v[len(v) // 2] += c
                        lines = NL.join(v).split(NL) + ["next_legit = 1"]
                        text = NL.join(lines) + NL
                        held = [i for i, ln in enumerate(lines)
                                if any(b in ln for b in body if len(b) >= 4)]
                        n += 1
                        for site, fn in sites.items():
                            try:
                                out, cnt = fn(text)
                            except (U.RefRefused, CX.ContextRefused):
                                continue
                            got = out.split(NL)
                            sv = [i for i in held if got[i] == lines[i]]
                            if sv or cnt <= 0 or got[len(lines) - 1] != "next_legit = 1":
                                bad.append((kname, pre, c, where, site, len(sv), cnt))
        return n, bad

    def test_the_verifiers_closers_at_every_site(self):
        sites = dict(PrefixedHead.SITES)
        n, bad = self._rows(_verifier_closers(), sites)
        self.assertEqual(n, 4 * 9 * len(_verifier_closers()) * 3)
        self.assertEqual([], bad, f"{len(bad)} leaking (key, prefix, closer, placement, site) "
                                  f"of {n} rows")

    def test_generated_closers(self):
        closers = _generated_closers()
        self.assertGreaterEqual(len(closers), 150)
        n, bad = self._rows(closers, self.SITES)
        self.assertEqual([], bad, f"{len(bad)} leaking (key, prefix, closer, placement, site) "
                                  f"of {n} rows")

    def test_legitimate_code_after_a_headerless_key_is_not_redacted(self):
        """The over-eat guard: what follows a headerless key in real code keeps its own line."""
        follow = ["}", "]);", "return x;", "return", "// done", "# done", "x = 1",
                  "someLongCamelCaseName", "  end", "})", "</pre>", "EOF", "fooBarBaz",
                  "return value", "*/", "-->", "pass"]
        bad = []
        for kname, (der, _real) in self.keys.items():
            body = K.b64_lines(der, 64)
            for pre, close in (('k = "', '"'), ("", ""), ('k = """', '"""'), ("x = '", "';")):
                for nxt in follow:
                    lines = [pre + body[0], *body[1:-1], body[-1] + close, nxt, "y = 2"]
                    text = NL.join(lines) + NL
                    for site, fn in PrefixedHead.SITES.items():
                        out, _n = fn(text)
                        got = out.split(NL)
                        if got[len(lines) - 2] != nxt or got[len(lines) - 1] != "y = 2":
                            bad.append((kname, pre, nxt, site))
        self.assertEqual([], bad)


class TheContinuationRule(unittest.TestCase):
    """E1.Mangled.suffixed, round 4 — `run_line`'s edges, DETERMINISTIC: each row builds a
    headerless key from a synthesised DER whose tail bytes are chosen so the last line has the
    shape under test (a fresh key only reaches these shapes by chance). Every row runs through
    the walker and `usages.redact_text`; it passes when every body line is replaced and the
    lines after the key are untouched."""

    SITES = {"walker": PrefixedHead.SITES["walker"], "usages": PrefixedHead.SITES["usages"]}
    DER = K.KEY_BODIES["rsa2048"]
    #: a headerless Ed25519 PKCS#8 key: 48 bytes, 64 base64 characters, decided at the run's close
    ED = bytes.fromhex("302e020100300506032b657004220420") + bytes(range(32))

    def _row(self, body, close="", mid="", after=("next_legit = 1",), lead="", first=None):
        v = [first or 'k = "' + body[0]] + body[1:]
        if mid:
            v[len(v) // 2] += mid
        v[-1] = lead + v[-1] + close
        lines = v + list(after)
        bad = []
        for site, fn in self.SITES.items():
            out, cnt = fn(NL.join(lines) + NL)
            got = out.split(NL)
            kept = [i for i in range(len(body)) if got[i] == lines[i]]
            moved = [a for i, a in enumerate(after) if got[len(body) + i] != a]
            if kept or moved or cnt <= 0:
                bad.append((site, kept, moved))
        return bad

    def test_an_escape_is_a_separator(self):
        """`…01\\n"`, `\\n01"`: a digits-only last line, taken on the multiple-of-4 count; with
        the escape's `n` glued on as opening glue (`n01`) it would be a code word, and not taken."""
        body = K.b64_lines(self.DER[:156] + bytes([0, 0x0D, 0x35]), 70)
        self.assertEqual("01", body[-1])
        for close in (chr(92) + 'n"', chr(92) + 'n" +', chr(92) + 'n";', chr(92) + "n"):
            self.assertEqual([], self._row(body, close), close)
        # ... and as OPENING glue: left in place, the escape's `n` would make the span `nD/`
        for lead in (chr(92) + "n", '"' + chr(92) + "n", chr(92) + "t"):
            self.assertEqual([], self._row(body, '"', lead=lead), lead)

    def test_a_slash_glued_after_a_body_line_is_code(self):
        """`…/>` on a middle line: the line is cut to the width, or the count is one off and the
        digits-only last line (`01`, which needs the count) fails it."""
        body = K.b64_lines(self.DER[:156] + bytes([0, 0x0D, 0x35]), 70)
        self.assertEqual("01", body[-1])
        for close in ('"', '";'):
            self.assertEqual([], self._row(body, close, mid="/>"), close)

    def test_a_padded_last_line_starting_with_a_slash(self):
        body = K.b64_lines(self.DER[:192] + bytes([255]), 64)
        self.assertEqual("/w==", body[-1])
        self.assertEqual([], self._row(body, '"', mid="/>"))

    def test_a_whole_last_line_starting_with_a_slash(self):
        body = K.b64_lines(self.DER[:192] + bytes([252, 0, 0]), 64)
        self.assertEqual("/AAA", body[-1])
        self.assertEqual([], self._row(body))

    def test_a_long_last_line_starting_with_a_slash(self):
        body = K.b64_lines(self.DER[:192] + bytes([252, 0, 0, 0, 0, 0]), 64)
        self.assertEqual("/AAAAAAA", body[-1])
        self.assertEqual([], self._row(body, '"'))

    def test_a_forced_runs_last_line_ends_it(self):
        """A key past its hit: after its short last line, a base64 value is its own."""
        body = K.b64_lines(self.DER, 64)
        self.assertLess(len(body[-1]), 64)
        self.assertEqual([], self._row(body, '"', after=("QUJD" * 16, "y = 2")))

    def test_the_head_is_the_longest_span_on_its_line(self):
        """A shorter base64-looking token before the key on its first line is code."""
        body = K.b64_lines(self.DER, 64)
        tag = base64.b64encode(bytes(range(65, 89))).decode("ascii")
        self.assertEqual([], self._row(body, '"', first='tag = "' + tag + '", k = "' + body[0]))

    def test_the_first_body_line_split_across_literals(self):
        """`k = "MIIEowIB" "<rest of line 1>"`: only the glued view opens a candidate."""
        body = K.b64_lines(self.DER, 64)
        first = 'k = "' + body[0][:8] + '" "' + body[0][8:] + '"'
        self.assertEqual([], self._row(body, '"', first=first))

    def test_the_glued_head_wins_when_both_open_a_candidate(self):
        """The longer half itself starts `M` (but is not a key's head): the glued line, a
        private-key head, is taken."""
        for name, cut in (("ec-P-256", 13), ("ec-P-256-pkcs8", 8), ("ed25519", 26)):
            body = K.b64_lines(K.KEY_BODIES[name], 64)
            self.assertEqual("M", body[0][cut])
            first = 'k = "' + body[0][:cut] + '" "' + body[0][cut:] + '"'
            self.assertEqual([], self._row(body, '"', first=first), name)

    def test_a_marked_key_concatenated_in_code(self):
        """`"-----BEGIN …-----\\n" +` then `"<line>\\n" +` lines: the marked run's FIRST body
        line is wrapped in more than glue, so it is read as a head (`b64_tail`), then `run_line`."""
        body = K.b64_lines(self.DER, 64)
        esc = chr(92) + "n"
        label = K.RSA + " " + K.w(80, 82, 73, 86, 65, 84, 69) + " " + K.w(75, 69, 89)
        dash = "-" * 5
        begin = '"' + dash + "BEGIN " + label + dash + esc + '" +'
        end = '"' + dash + "END " + label + dash + esc + '";'
        lines = ["String k ="] + [begin] + ['"' + b + esc + '" +' for b in body] + [end, "int y = 2;"]
        for site, fn in self.SITES.items():
            out, cnt = fn(NL.join(lines) + NL)
            got = out.split(NL)
            kept = [i for i in range(2, 2 + len(body)) if got[i] == lines[i]]
            self.assertEqual(([], "int y = 2;"), (kept, got[-2]), site)

    def test_a_short_word_above_a_clean_key_is_not_held_with_it(self):
        """`end` / `fi` / `OK` just above a headerless key: under 4 characters and unpadded, not a
        body line, so not in the run the key's hit releases."""
        body = K.b64_lines(self.DER, 64)
        for word in ("end", "fi", "ab", "OK"):
            lines = ["x = 1", word] + body + ["y = 2"]
            for site, fn in self.SITES.items():
                out, _n = fn(NL.join(lines) + NL)
                self.assertEqual(word, out.split(NL)[1], (word, site))

    def test_a_window_that_opens_inside_a_concatenated_key(self):
        """A walker started INSIDE an unmarked run (`RUN_STATE`, a window's carry) with no width
        yet: its first line `"<line>\\n" +` is read as a head, the rest by `run_line`."""
        body = K.b64_lines(self.DER, 64)
        esc = chr(92) + "n"
        lines = ['"' + b + esc + '" +' for b in body[3:]] + ["int y = 2;"]
        w = S.KeyWalk(S.RUN_STATE)
        got = {}
        for i, ln in enumerate(lines):
            for item, key in w.feed(ln, i):
                got[item] = key
        for item, key in w.finish():
            got[item] = key
        self.assertEqual([True] * (len(lines) - 1) + [False], [got.get(i) for i in range(len(lines))])

    def test_a_long_upper_case_last_line(self):
        body = K.b64_lines(self.DER[:192] + bytes([1, 2, 3, 4, 5, 6]), 64)
        self.assertEqual("AQIDBAUG", body[-1])
        self.assertEqual([], self._row(body, '"'))

    def test_code_after_a_key_whose_last_line_is_full(self):
        """The over-eat guard where it bites: the key's last line is a FULL line, so the line
        after it is read as a possible last line — and code must not pass."""
        body = K.b64_lines(self.DER[:192], 64)
        self.assertEqual(64, len(body[-1]))
        follow = ["}", "]);", "return x;", "return", "// done", "# done", "x = 1", "pass",
                  "someLongCamelCaseName", "fooBarBaz", "EOF", "HTTP2", "</pre>", "*/", "-->",
                  "/* c */", "end", "None", "//", "/" * 16, "+" * 8, "0,", "42);"]
        bad = []
        for close in ("", '"', '";', '",'):
            for nxt in follow:
                r = self._row(body, close, after=(nxt, "y = 2"))
                if r:
                    bad.append((close, nxt, r))
        self.assertEqual([], bad)

    def test_a_rejected_line_ends_a_run_still_held(self):
        """A one-line key decided at the run's close: the code word after it is not fed in."""
        body = K.b64_lines(self.ED, 64)
        self.assertEqual(1, len(body))
        self.assertEqual([], self._row(body, '"', after=("fooBarBaz", "y = 2")))

    def test_a_held_runs_last_line_ends_it(self):
        """A short key's last line is judged at once: a base64 value after it is its own."""
        body = K.b64_lines(self.ED, 24)
        self.assertEqual([24, 24, 16], [len(b) for b in body])
        value = "QUJD" * 8
        self.assertEqual([], self._row(body, '"', after=(value, "y = 2")))

    def test_the_second_line_settles_a_run_not_yet_a_key(self):
        """`k = "<l1>" "<l2>"` at width 40: 120 characters after two lines, no hit yet — the
        third line is judged on the settled width 40, not the glued 80."""
        body = K.b64_lines(self.DER, 40)
        first = 'k = "' + body[0] + '" "' + body[1] + '"'
        lines = [first] + body[2:] + ["next_legit = 1"]
        for site, fn in self.SITES.items():
            out, cnt = fn(NL.join(lines) + NL)
            got = out.split(NL)
            kept = [i for i in range(len(lines) - 1) if got[i] == lines[i]]
            self.assertEqual(([], "next_legit = 1"), (kept, got[len(lines) - 1]), site)


def _generated_leads():
    """Leads GENERATED, not listed (round 5): every 1-3 character combination of the base64
    alphabet's non-alphanumerics (`/`, `+`, `=`) mixed with space, `!`, `#`, `-` and `;` — plus
    word leads."""
    import itertools
    parts = ["/", "+", "=", " ", "!", "#", "-", ";"]
    out = {"".join(c) for k in (1, 2, 3) for c in itertools.product(parts, repeat=k)}
    out |= {"REM ", "rem ", ":: ", "dnl ", "// ", "//! ", "/// ", "// // "}
    return sorted(out)


def _short_word_tail(last, total):
    """The disclosed Low (round 5): an UNPADDED last line of 1-3 characters that a code line could
    also be (`ug`, `f`, `+`), or digits / one character that do not make the key's `total`
    base64 a multiple of 4 (`42`) — at most 2 bytes of key. Telling it from `// done` after the
    key would mean eating code. Exact, not a range: anything else the rule must catch."""
    if len(last) > 3 or last.endswith("="):
        return False
    if S._code_word(last) or not last[0].isalnum():
        return True
    return (len(last) < 2 or last.isdigit()) and total % 4 != 0


class CommonLeadIsGlue(unittest.TestCase):
    """E1.Mangled.commented (round 5) — a headerless key whose EVERY line carries the same lead
    (`// `, `REM `, `/// `, generated): the block's common prefix and suffix are glue, whatever
    alphabet they are in. Shapes: the lead on every line; a first line with an extra lead
    (`key = "`); a closing quote on the last line. Each row runs through the walker and
    `usages.redact_text`; it passes when every body line is replaced and the two lines after the
    key are untouched."""

    SITES = {"walker": PrefixedHead.SITES["walker"], "usages": PrefixedHead.SITES["usages"]}

    @staticmethod
    def _der(n):                                     # the re-verify's key set (r4a), seeded
        import random
        r = random.Random(n)
        rb = lambda k: bytes(r.getrandbits(8) for _ in range(k))  # noqa: E731
        return K.seq(K.small(0), K.integer(rb(n)), K.integer(bytes([1, 0, 1])), K.integer(rb(n)),
                     *(K.integer(rb(n // 2)) for _ in range(5)))

    def _rows(self, ns, widths, leads, sites):
        bad, n = [], 0
        for kn in ns:
            full = base64.b64encode(self._der(kn)).decode("ascii")
            for width in widths:
                for strip in (False, True):
                    text = full.rstrip("=") if strip else full
                    body = [text[i:i + width] for i in range(0, len(text), width)]
                    for lead in leads:
                        for shape in ("every", "first-extra", "close"):
                            v = [lead + b for b in body]
                            if shape == "first-extra":
                                v[0] = 'key = "' + v[0]
                            elif shape == "close":
                                v[-1] += '"'
                            lines = v + ["next_legit = 1", "y = 2"]
                            n += 1
                            for site, fn in sites.items():
                                out, cnt = fn(NL.join(lines) + NL)
                                got = out.split(NL)
                                kept = [i for i in range(len(body)) if got[i] == lines[i]]
                                if kept == [len(body) - 1] and _short_word_tail(body[-1], sum(map(len, body))):
                                    kept = []        # the disclosed Low: a 1-3 char code word
                                if kept or got[len(body):len(body) + 2] != lines[len(body):] \
                                        or cnt <= 0:
                                    bad.append((kn, width, strip, lead, shape, site, len(kept)))
        return n, bad

    def test_every_generated_lead(self):
        leads = _generated_leads()
        self.assertGreater(len(leads), 580)
        n, bad = self._rows([102], (64,), leads, self.SITES)
        self.assertEqual([], bad[:20], f"{len(bad)} leaking of {n} rows")

    def test_comment_and_word_leads_on_every_key_and_width(self):
        leads = ["// ", "//", "/// ", "//! ", "// // ", "REM ", "rem ", ":: ", "dnl ", "# ",
                 "-- ", "; ", "% ", "> ", "* ", " * ", "| ", "\t", "    "]
        n, bad = self._rows(list(range(100, 112)) + [130, 200], (64, 70, 76), leads,
                            {"usages": self.SITES["usages"]})
        self.assertEqual([], bad[:20], f"{len(bad)} leaking of {n} rows")

    def test_a_slash_glued_after_every_line(self):
        """`…/` on every body line, then a short WHOLE last line starting `/` or `+` (the
        re-verify's round-4 shape): a regression guard — the whole-line rule holds it."""
        full = base64.b64encode(self._der(102)).decode("ascii")
        body = [full[i:i + 64] for i in range(0, len(full), 64)]
        for last in ("/abc", "+abcde", "/abcdef", "+ab"):
            lines = [b + "/" for b in body[:-1]] + [last, "after = 1"]
            for site, fn in self.SITES.items():
                out, _n = fn(NL.join(lines) + NL)
                got = out.split(NL)
                kept = [i for i in range(len(lines) - 1) if got[i] == lines[i]]
                self.assertEqual(([], "after = 1"), (kept, got[len(lines) - 1]), (last, site))

    def test_a_short_last_line_without_its_padding(self):
        """A 1-3 character last line with its `=` padding stripped, inside code (`f/c"`, `# k2k`,
        `// 7;`): a 2-3 character one with a letter needs no count; digits or one character are
        the key's when they make its base64 a multiple of 4. Only `_short_word_tail` is excused."""
        rows = 0
        for n in range(100, 300):
            full = base64.b64encode(self._der(n)).decode("ascii").rstrip("=")
            for width in (64, 70, 76):
                body = [full[i:i + width] for i in range(0, len(full), width)]
                last = body[-1]
                if len(last) > 3 or _short_word_tail(last, len(full)):
                    continue
                for lead, close in (("", '"'), ("# ", ""), ("// ", ";")):
                    lines = ['k = "' + lead + body[0]] + [lead + b for b in body[1:]]
                    lines[-1] += close
                    lines.append("after = 1")
                    rows += 1
                    for site, fn in self.SITES.items():
                        out, _c = fn(NL.join(lines) + NL)
                        self.assertNotEqual(lines[len(body) - 1], out.split(NL)[len(body) - 1],
                                            (n, width, lead, site))
        self.assertGreaterEqual(rows, 20)

    def test_a_one_digit_last_line_that_completes_the_count(self):
        """A key whose last line is ONE digit (`// 7;`) that makes its base64 a multiple of 4:
        the count is what tells it from code — the multiple-of-4 path, pinned."""
        rows = 0
        for n in range(100, 140):
            der = bytearray(self._der(n))
            if len(der) % 3:
                continue
            der[-1] = (der[-1] & 0xC0) | 55          # the last base64 character is `3`
            full = base64.b64encode(bytes(der)).decode("ascii")
            self.assertEqual((0, "3"), (len(full) % 4, full[-1]))
            for width in range(40, 81):
                if (len(full) - 1) % width:
                    continue
                body = [full[i:i + width] for i in range(0, len(full), width)]
                for lead, close in (("", ";"), ("// ", ""), ("# ", '"')):
                    lines = ['k = "' + lead + body[0]] + [lead + b for b in body[1:]]
                    lines[-1] += close
                    lines.append("after = 1")
                    rows += 1
                    for site, fn in self.SITES.items():
                        out, _c = fn(NL.join(lines) + NL)
                        got = out.split(NL)
                        self.assertEqual((True, "after = 1"),
                                         (got[len(body) - 1] != lines[len(body) - 1],
                                          got[len(body)]), (n, width, lead, site))
        self.assertGreaterEqual(rows, 6)

    def test_key_lines_that_share_a_slash_under_a_lead(self):
        """2, 3 or 4 key lines in a row start `/` (second characters differ), under every
        generated lead, the first line carrying an extra `k = "`: the block's common `/` is KEY
        text the lead rule took as glue. The reading that keeps it continues the run, and a
        reading that continues beats one that ends: no line leaks."""
        bad, n = [], 0
        for k in (2, 3, 4):
            der = bytearray(TheContinuationRule.DER)
            for j in range(1, k + 1):
                der[48 * j], der[48 * j + 1] = 0xFC, 16 * j   # line j starts `/`, then A, B, …
            body = K.b64_lines(bytes(der), 64)
            self.assertEqual(["/"] * k, [b[0] for b in body[1:k + 1]])
            for lead in _generated_leads() + ["// ", "REM ", "# "]:
                for close in ("", '"'):
                    lines = ['k = "' + lead + body[0]] + [lead + b for b in body[1:]]
                    lines[-1] += close
                    lines.append("after = 1")
                    for site, fn in self.SITES.items():
                        out, _c = fn(NL.join(lines) + NL)
                        got = out.split(NL)
                        n += 1
                        kept = [i for i in range(len(body)) if got[i] == lines[i]]
                        if kept or got[len(body)] != "after = 1":
                            bad.append((k, lead, close, site, len(kept)))
        self.assertEqual([], bad[:20], f"{len(bad)} leaking of {n} rows")

    def test_off_a_run_there_is_no_block(self):
        """The block costs one prefix compare per line INSIDE a run, and nothing outside one: a
        file of code with no key makes no compare (the scale budget, pinned structurally)."""
        from unittest import mock
        text = NL.join(f"    value_{i} = compute(a, b) + {i}  # note" for i in range(400)) + NL
        with mock.patch.object(S, "_common_prefix", wraps=S._common_prefix) as cp:
            for site, fn in self.SITES.items():
                fn(text)
        self.assertEqual(0, cp.call_count)

    def test_a_lead_never_ends_in_a_letter_or_digit(self):
        """Every key line after the first starts `Q`, under a lead, and the last is `Q/abcde"`:
        the block's common prefix is `// Q`, and its `Q` is key text — a letter or digit is never
        glue (stripped, the last line would be `/abcde`, which is not taken)."""
        body = K.b64_lines(TheContinuationRule.DER, 64)
        body = [body[0]] + ["Q" + b[1:] for b in body[1:-1]] + ["Q/abcde"]
        for lead in ("# ", "// ", "REM "):
            for close in ('"', ";"):
                lines = ['k = "' + lead + body[0]] + [lead + b for b in body[1:]]
                lines[-1] += close
                lines.append("after = 1")
                for site, fn in self.SITES.items():
                    out, _c = fn(NL.join(lines) + NL)
                    got = out.split(NL)
                    kept = [i for i in range(len(body)) if got[i] == lines[i]]
                    self.assertEqual(([], "after = 1"), (kept, got[len(body)]),
                                     (lead, close, site))

    def test_a_two_line_key_under_a_lead(self):
        """An Ed25519 key in TWO lines (48 + 16), each under the same lead: the block starts at
        the run's head, so its second line — the last — is read under the lead too."""
        body = K.b64_lines(TheContinuationRule.ED, 48)
        self.assertEqual([48, 16], [len(b) for b in body])
        for lead in ("// ", "REM ", "# "):
            for close in ("", '"'):
                lines = [lead + b for b in body]
                lines[-1] += close
                lines.append("after = 1")
                for site, fn in self.SITES.items():
                    out, _c = fn(NL.join(lines) + NL)
                    got = out.split(NL)
                    kept = [i for i in range(len(body)) if got[i] == lines[i]]
                    self.assertEqual(([], "after = 1"), (kept, got[len(body)]),
                                     (lead, close, site))

    def test_code_after_a_commented_key(self):
        """The over-eat guard under a block lead: `// done` after a `// ` key keeps its line. The
        key fills its last line (640 = 10 x 64), so the run is still open at the line after it;
        `// /x9` is not a whole line, whatever a stripped reading of it looks like."""
        full = base64.b64encode(self._der(100)).decode("ascii")
        self.assertEqual(0, len(full) % 64)
        body = [full[i:i + 64] for i in range(0, len(full), 64)]
        for lead in ("// ", "# ", "REM "):
            for nxt in ("done", "return x;", "TODO fix", "x = 1", "}", "", "end", "/x9", "+ab",
                        "/x9;"):
                lines = [lead + b for b in body] + [lead + nxt, "y = 2"]
                for site, fn in self.SITES.items():
                    out, _c = fn(NL.join(lines) + NL)
                    got = out.split(NL)
                    self.assertEqual(lines[len(body):], got[len(body):len(body) + 2],
                                     (lead, nxt, site))


class Unmarked(LineSites):
    """E1.Unmarked — width {8,16,64,76} x every key shape, no marker at all."""

    def test_every_shape_at_every_width(self):
        for name, der in K.KEY_BODIES.items():
            for width in (8, 16, 64, 76):
                body = K.b64_lines(der, width)
                self.assert_line_sites("x = 1" + NL + NL.join(body) + NL + "y = 2" + NL,
                                       body[:-1] if len(body[-1]) < 4 else body,
                                       f"{name} {width}", copy=width == 64)

    def test_real_openssl_keys_with_their_markers_stripped(self):
        keys = K.real_keys()
        if not keys:
            self.skipTest("openssl is not on PATH — row recorded not-run")
        for name, pem in keys.items():
            body = [ln for ln in pem.splitlines() if ln and not ln.startswith("-")]
            self.assert_line_sites("x" + NL + NL.join(body) + NL + "y" + NL, body[:-1], name,
                                   copy=False)

    def test_an_unmarked_key_escaped_onto_one_json_line(self):
        """A service-account style value: the key body JSON-escaped onto ONE line (`\\n`)."""
        body = K.b64_lines(K.KEY_BODIES["rsa2048"], 64)
        line = '{"private_key": "' + "\\n".join(body) + '\\n", "type": "x"}'
        text = "{" + NL + line + NL + "}" + NL
        self.assertEqual([KEY_TOLERANT], key_kinds(text), "the gates' whole-text scan")
        self.assert_line_sites(text, body, "escaped json", copy=False)

    def test_the_run_ends_at_the_first_line_that_is_not_base64(self):
        """After an unmarked key, a prose line ends it: a short base64 value below is not the key."""
        body = K.b64_lines(K.KEY_BODIES["ec-P-256"], 64)
        later = "QUJDRA=="
        text = NL.join(["x = 1", *body, "y = 2", later, "z = 3"]) + NL
        walked = "".join(U.pem_lines(text.splitlines(keepends=True), {}, "x"))
        self.assertIn(NL + later + NL, walked)
        self.assertEqual([], [b for b in body if b in walked])

    def test_a_homoglyph_marker_is_caught_by_the_backstop(self):
        cyr_e = "Е"                          # Cyrillic capital IE, looks like E
        begin = K.D5 + "B" + cyr_e + "GIN " + K.RSA + " " + K.PRIVATE + " " + K.KEY + K.D5
        self.assertEqual([], [m for m in S.key_markers(begin)], "homoglyph now a marker")
        body = K.b64_lines(K.KEY_BODIES["rsa2048"], 64)
        self.assert_line_sites(keytext(begin, body, K.marker(K.END)), body, "homoglyph", copy=False)


class Glued(LineSites):
    """E1.Glued — `cat a b` with no newline: one key's END glued to the next key's BEGIN, ends
    mangled. The END's trailing dashes never consume the BEGIN's leading ones."""

    def test_glued_end_begin(self):
        for tdash, ldash in ((10, 0), (5, 5), (0, 0), (2, 5), (64, 3)):
            b1 = K.random_body(16, 5, f"g1{tdash}")
            b2 = K.random_body(16, 5, f"g2{ldash}")
            glued = (K.marker(K.END, K.RSA, trail="-" * tdash) + " " * (tdash == 0)
                     + K.marker(K.BEGIN, K.RSA, lead="-" * ldash))
            text = keytext(K.marker(K.BEGIN), b1, glued, after=NL.join(b2 + [K.marker(K.END), "z"]))
            self.assert_line_sites(text, b1 + b2, f"glued {tdash}/{ldash}", copy=False)
            ms = S.key_markers(glued)
            self.assertEqual([False, True], [m.begin for m in ms], (tdash, ldash))


class Formats(LineSites):
    """E1.Formats — `.ppk` by suffix, PGP armour headers and checksum, an SSH2 `Comment:` header
    inside a body, a UTF-16LE file (refused as binary)."""

    def test_ppk_is_sensitive_by_suffix(self):
        self.assertTrue(any(f.layer == "path" for f in S.scan("", path="keys/server.ppk")))
        d = _tmp(self)
        _put(d, "keys/server.ppk", "PuTTY-User-Key-File-3: ssh-ed25519" + NL)
        with self.assertRaises(CX.ContextRefused):
            CX.build_item(d, "file", "keys/server.ppk")

    def test_pgp_armour(self):
        pgp = K.w(80, 71, 80)
        begin, end = K.pair(pgp, block=True, lead=K.D5[:2], trail="")      # mangled ends
        body = K.random_body(64, 8, "pgp")
        lines = ["Version: GnuPG v2", "Comment: test", "", *body, "=AbCd"]
        self.assert_line_sites(keytext(begin, lines, end), body + ["=AbCd"], "pgp", copy=False)

    def test_ssh2_comment_header(self):
        ssh2 = K.w(83, 83, 72, 50) + " " + K.w(69, 78, 67, 82, 89, 80, 84, 69, 68)
        begin, end = K.pair(ssh2, lead="---- ", trail=" ----")
        body = K.random_body(64, 8, "ssh2")
        lines = ['Comment: "rsa-key-20260101"', "Subject: me", *body]
        self.assert_line_sites(keytext(begin, lines, end), body, "ssh2", copy=False)

    def test_legacy_encrypted_pem_headers_open_a_run(self):
        cyr = "В"                             # the BEGIN is homoglyph-mangled
        begin = K.D5 + cyr + "EGIN " + K.RSA + " " + K.PRIVATE + " " + K.KEY + K.D5
        body = K.random_body(64, 8, "legacy")
        lines = ["Proc-Type: 4,ENCRYPTED", "DEK-Info: AES-128-CBC,00112233445566778899AABBCCDDEEFF",
                 "", *body]
        self.assert_line_sites(keytext(begin, lines, "trailer"), body, "legacy", copy=False)

    def test_a_base64_encoded_key_is_firm_by_its_marker(self):
        """kubeconfig `client-key-data:` — the whole PEM base64-encoded on one line. Its body is
        not DER (only the `LS0t…` marker can know it is a key)."""
        import base64
        begin, end = K.pair()
        pem = NL.join([begin, *K.random_body(16, 6, "kube"), end]) + NL
        token = base64.b64encode(pem.encode()).decode()
        text = "users:" + NL + "- user:" + NL + "    client-key-data: " + token + NL
        self.assertEqual([KEY_FIRM], key_kinds(text))
        self.assert_line_sites(text, [token], "client-key-data", copy=False)

    def test_an_end_closes_a_run_whose_begin_is_missing(self):
        """A key whose BEGIN line was lost (cut off above): the END line releases the narrow body
        lines held before it AS the key."""
        body = K.random_body(16, 9, "orphan")
        self.assert_line_sites(NL.join(["a = 1", *body, K.marker(K.END), "b = 2"]) + NL, body,
                               "orphan END", copy=False)

    def test_utf16_file_is_refused_as_binary(self):
        d = _tmp(self)
        begin, end = K.pair()
        p = os.path.join(d, "k.txt")
        with open(p, "wb") as fh:
            fh.write(keytext(begin, K.random_body(16, 4), end).encode("utf-16-le"))
        with self.assertRaises(CX.ContextRefused) as cm:
            CX.build_item(d, "file", "k.txt")
        self.assertIn("not a readable text file", str(cm.exception))


class Counted(unittest.TestCase):
    """E1.Counted — every replacement is counted, and the count is what the IDE shows."""

    def test_the_count_is_every_replaced_line(self):
        begin, end = K.pair(K.RSA, lead="", trail="")
        body = K.random_body(16, 7)
        out, n = CX._redact(keytext(begin, body, end), "x.py")
        self.assertEqual(len(body) + 2, n)
        self.assertEqual(n, out.count("redacted by mokata's secret scan"))

    def test_the_ide_label_wording(self):
        with open(os.path.join(SRC, "mokata", "ide", "static", "bridge.js"), encoding="utf-8") as fh:
            self.assertIn("line(s) redacted", fh.read())


class OneDetector(unittest.TestCase):
    """E1.OneDetector — `src/` holds ONE marker detector. A "marker regex" is a string constant
    (not a docstring) holding BEGIN or END together with PRIVATE, KEY or a dash run."""

    WORD = re.compile(r"\b(?:" + K.BEGIN + "|" + K.END + r")\b|(?i:begin|end)\[|\\b(?i:begin|end)")
    TAIL = re.compile(r"(?i)" + K.PRIVATE + "|" + K.KEY + r"|-{3}|-\{")

    def test_no_second_marker_pattern_in_src(self):
        # CORPUS: THE WORKING TREE. The question is what `src/` holds and ships (`sync-public.sh`
        # mirrors the working tree), so the disk is ground truth, not the index.
        hits = []
        for base, _dirs, files in os.walk(os.path.join(SRC, "mokata")):
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                p = os.path.join(base, fn)
                with open(p, encoding="utf-8") as fh:
                    tree = ast.parse(fh.read())
                docs = {id(n.value) for n in ast.walk(tree)
                        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)}
                for n in ast.walk(tree):
                    if isinstance(n, ast.Constant) and isinstance(n.value, str) \
                            and id(n) not in docs and self.WORD.search(n.value) \
                            and self.TAIL.search(n.value):
                        hits.append(f"{os.path.relpath(p, SRC)}:{n.lineno}")
        self.assertEqual([], hits)

    def test_every_redactor_calls_the_detector(self):
        """`context._redact` and `ground.redact` are thin callers of `usages.redact_text`."""
        import inspect
        from mokata.explain import ground
        self.assertIn("redact_text", inspect.getsource(CX._redact))
        self.assertIn("redact_text", inspect.getsource(ground.redact))
        self.assertNotIn("pem_lines", inspect.getsource(CX._redact))


if __name__ == "__main__":
    unittest.main()
