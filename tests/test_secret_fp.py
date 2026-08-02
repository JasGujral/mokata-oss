"""SECRET-FP (0.0.16) — identifier-aware entropy discrimination in the secret backstop.

THE LIVE BUG (0.0.14, doc 86 rider): the entropy backstop blocked spec-check on legitimate
code. `_is_structured_identifier` exempted an identifier only when a separator was present
AND there was NO uppercase, so every mixed-case or SCREAMING_SNAKE identifier ≥20 chars with
a digit hard-blocked — including mokata's OWN env-var convention.

The three MEASURED false positives (grooming, doc 95 §9):
    getUserAuthToken2FromRequest · MOKATA_SESSION_ID_OVERRIDE_2 · kubernetesClusterRoleBinding2Spec

The fix is a CONJUNCTION, not a loosening: a token blocks when it is high-entropy AND
char-class mixed AND has NO word structure. Word structure = the token decomposes, on
casing transitions and `-`/`_` separators, into dictionary-free but WORD-SHAPED segments.

The 0.0.14 predicate's one SOUND half is kept alongside it: a lowercase separated slug
(path component, macOS temp dir, cache key) stays exempt, because real slugs are often not
word-shaped at all. It is now narrower than 0.0.14 — it no longer applies when every
separated piece is pure hex, which closes a pre-existing hole (see the adversarial bar).

WHY THE EXISTING SUITE COULD NOT CATCH THIS (the reason this file exists): every benign row
in `test_secret_corpus.FALSE_POSITIVES` was lowercase-only, and the fuzzer's positive case
FORCED an uppercase char into every generated secret. The suite would have stayed green if
the heuristic were literally "block anything with a capital letter". This file pins the
CASING-BLINDNESS shut on both surfaces.

THE KNOWN-SHAPE FLOOR (addendum, Jas 2026-07-27). A key CHUNKED with `-`/`_` decomposes into
perfectly word-shaped segments — `AKIA_IOSFODNN7_EXAMPLE` is structurally indistinguishable
from `MOKATA_SESSION_ID_OVERRIDE_2`. Rather than reach for a dictionary or a casing rule (the
sources of the original false positives), a floor of ANCHORED known-secret shapes runs BEFORE
every exemption: a candidate that IS a documented credential shape — tested raw AND
separator-stripped, `fullmatch` in both cases — blocks regardless of word structure. A prefix
alone never matches, which is what keeps the floor's own FP risk at ~zero.

THE DOCUMENTED BOUNDARY (residual, filed to R7 / 0.1.2). An UNKNOWN key type chunked by
separators into word-shaped segments stays EXEMPT, and `TestChunkedUnknownKeyBoundary` below
asserts that on purpose. Grounding: a separator-chunked key is a TRANSFORMED key, outside a
structural backstop's scope — the untransformed original still blocks, and closing the general
case needs semantic knowledge this layer deliberately does not have. R7's full corpus overhaul
owns widening the shape set.

SCOPE BOUNDARY: this stage changes the PREDICATE only. It does not change which surfaces
consult the backstop, adds no config knob and no allowlist file. R7's full corpus overhaul
(broader provider formats, corpus generation) stays 0.1.2 — do not grow this file into it.

Real-secret literals are ASSEMBLED from sub-20-char fragments at runtime (the same
convention as `test_secret_corpus.py`) so this source file carries no blockable literal.
All "secrets" here are LEAK-CANARY fakes — never a real key.
Dependency-free, deterministic.
"""

import inspect
import random
import string
import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.govern.secrets import _matches_known_shape, has_secrets, scan

import test_secret_corpus as corpus


def j(*parts):
    """Join fragments — the runtime secret; the source only holds the (benign) fragments."""
    return "".join(parts)


def _entropy_blocks(s):
    return any(f.layer == "entropy" for f in scan(text=s))


# ── The three FPs grooming measured on 0.0.14. Each is ≥20 chars, carries a digit, and is
#    ordinary production code. All three BLOCKED before this stage. ─────────────────────
MEASURED_FALSE_POSITIVES = [
    "getUserAuthToken2FromRequest",          # camelCase, digit infix
    "MOKATA_SESSION_ID_OVERRIDE_2",          # SCREAMING_SNAKE — mokata's OWN env convention
    "kubernetesClusterRoleBinding2Spec",     # camelCase, long vendor-ish words
]

# The casing conventions the predicate must recognise as word structure, each ≥20 chars with
# a digit so it actually reaches the entropy backstop (a shorter row proves nothing).
CASING_FALSE_POSITIVES = [
    "camelCaseIdentifier2Here",                  # camelCase
    "PascalCaseIdentifier2Here",                 # PascalCase
    "SCREAMING_SNAKE_CASE_2_HERE",               # SCREAMING_SNAKE
    "lower_snake_case_identifier_2",             # snake_case + digit suffix
    "lower-kebab-case-identifier-2",             # kebab-case + digit suffix
    "mixedCase_with_Separators_2",               # mixed convention (real code is messy)
    "parseHTTPResponseHeader2Value",             # embedded acronym (HTTP)
    "xmlHttpRequestFactory2Builder",             # vowel-less abbreviations among real words
    "AWS_REGION_US_EAST_1_ENDPOINT",             # SCREAMING_SNAKE with digit segment
    "renderUserProfileAvatar2Url",               # camelCase, digit before a short word
]

# Lowercase separated SLUGS. Not word-shaped at all, so word structure cannot carry them —
# they need their own exemption. Found live: tightening the predicate without this made the
# team-audit suites fail, because macOS's own per-user temp directory has exactly this shape
# and appears in any content that mentions a temp path.
SLUG_FALSE_POSITIVES = [
    "/var/folders/y9/r2xr67z10lb6p5n3t_m1_c740000gn/T/mokata-run.json",  # macOS $TMPDIR
    "r2xr67z10lb6p5n3t_m1_c740000gn",                                    # the bare component
    "zyju5a9quf0_x0gp_xwxcx1jnzikv_e14_5erogs_8tm5",                     # generated slug/cache key
    "build-artifacts-2f9x-cache-v3-linux-x64",                           # CI cache key
]

# ── ADVERSARIAL: the widened exemption must NOT become an escape hatch. Every row here is a
#    real-SHAPED credential that CONTAINS separators or casing — exactly the material the
#    word-structure exemption now looks at. All must still BLOCK. LEAK-CANARY fakes. ─────
ADVERSARIAL_TRUE_POSITIVES = {
    # URL-safe base64 (the `-`/`_` alphabet) — separators, but no word structure.
    "urlsafe-base64-key": j("xY9kZ2mQ7p", "-L4nR8vT3wB", "_6cF1dG5hJ0"),
    # URL-safe base64 with `=` padding.
    "urlsafe-base64-padded": j("dGhpc0lzQV", "-9mYWtlS2V5", "_Rk9SX1RFU1", "=="),
    # Hex chunks joined by underscores — a key split to dodge a contiguous-run matcher.
    # NOTE: this shape was EXEMPT before this stage (lowercase + separator), so blocking it
    # is a strict tightening, not a relaxation.
    "hex-chunks-underscored": j("9f8e7d6c5b4a3928", "_", "1726354453627180",
                                "_", "a1b2c3d4e5f6a7b8"),
    # A key someone camelCased into a variable VALUE (bare, no assignment keyword to lean on).
    "camelcased-key-value": j("aZ9kQ2mX7p", "L4nR8vT3wB", "6cF1dG5hJ0"),
    # The same key in an assignment (the signature layer must also hold).
    "camelcased-key-assigned": "token = \"" + j("aZ9kQ2mX7p", "L4nR8vT3wB",
                                                 "6cF1dG5hJ0") + "\"",
    # AWS access key id embedded INSIDE a camelCase identifier — the contiguous run survives.
    "aws-akia-inside-camel": "awsKey" + j("AKIA", "IOSFODNN7", "EXAMPLE") + "Value",
    # AWS 40-char secret access key (no distinctive prefix — the backstop is the only catch).
    "aws-secret-contiguous": j("wJalrXUtnF", "EMIK7MDENG", "bPxRfiCYEX", "AMPLEKEY01"),
    # A JWT's dotted triplet — separators of a different kind.
    "jwt-dotted-triplet": j("eyJ", "abc123ABC4", ".", "eyJ", "def456DEF7", ".",
                            "sigABC123x", "yzDEF"),
    # A random mixed-case token with ONE word-shaped segment glued on — a hand-crafted attempt
    # to buy the exemption cheaply. The word-shaped material must not be the majority.
    "word-prefixed-random-key": "session" + j("Xq7Zk2Mv9p", "L4nR8vT3wB", "6cF1dG5hJ0"),
    # An AWS access key id REVERSED: one contiguous run whose only internal boundary is a
    # DIGIT. A digit is not a word boundary, so this must not read as an identifier.
    "aws-key-reversed": j("AKIA", "IOSFODNN7", "EXAMPLE")[::-1],
    # Same idea, generic: a long uppercase key run split only by digits.
    "digit-split-upper-run": j("QVBSTUXWZK", "4", "PLMDNBVCXZ", "7", "GHJKLTRWQP"),
}


class TestMeasuredFalsePositives(unittest.TestCase):
    """The live 0.0.14 incident: legitimate identifiers must not block."""

    def test_the_three_measured_identifiers_pass(self):
        for ident in MEASURED_FALSE_POSITIVES:
            with self.subTest(identifier=ident):
                self.assertFalse(has_secrets(scan(text=ident)),
                                 f"FALSE POSITIVE on legitimate identifier: {ident!r}")

    def test_measured_identifiers_pass_inside_a_source_line(self):
        """The incident shape: the identifiers embedded in real code, not bare."""
        line = ("def " + MEASURED_FALSE_POSITIVES[0] + "(request):\n"
                "    return os.environ.get(\"" + MEASURED_FALSE_POSITIVES[1] + "\")\n"
                "# see " + MEASURED_FALSE_POSITIVES[2] + "\n")
        self.assertEqual(scan(text=line), [],
                         "FALSE POSITIVE on a source file of legitimate identifiers")


class TestCasingConventionsAreWordStructure(unittest.TestCase):
    """camelCase / PascalCase / SCREAMING_SNAKE / snake_case / kebab-case + digit-suffixed
    variants of each are word structure, so none of them entropy-block."""

    def test_every_casing_convention_passes(self):
        for ident in CASING_FALSE_POSITIVES:
            with self.subTest(identifier=ident):
                self.assertFalse(has_secrets(scan(text=ident)),
                                 f"FALSE POSITIVE on {ident!r}")

    def test_rows_actually_reach_the_entropy_backstop(self):
        """A benign row shorter than the 20-char token floor, or with no digit, would be
        exempted by the char-class gate and prove NOTHING about the predicate."""
        for ident in MEASURED_FALSE_POSITIVES + CASING_FALSE_POSITIVES:
            with self.subTest(identifier=ident):
                self.assertGreaterEqual(len(ident), 20, "row never reaches the backstop")
                self.assertTrue(any(c.isdigit() for c in ident), "row has no digit")
                self.assertTrue(any(c.isalpha() for c in ident), "row has no letter")

    def test_uppercase_is_represented(self):
        """The casing-blindness guard: if every benign row were lowercase again, the suite
        could not tell a correct predicate from 'block anything with a capital letter'."""
        upper_rows = [i for i in MEASURED_FALSE_POSITIVES + CASING_FALSE_POSITIVES
                      if any(c.isupper() for c in i)]
        self.assertGreaterEqual(len(upper_rows), 8)


class TestKnownShapeFloor(unittest.TestCase):
    """The floor that closes the chunked-key gap. Pins WHICH rule fires, not just that
    something did — a block for the wrong reason is not the guarantee we want."""

    _CHUNKED = "AKIA" + "_" + "IOSFODNN7" + "_" + "EXAMPLE"
    _UNSEPARATED = j("AKIA", "IOSFODNN7", "EXAMPLE")

    def test_separator_chunked_aws_key_blocks_via_the_shape_floor(self):
        findings = scan(text=self._CHUNKED)
        self.assertTrue(findings, "chunked AWS key was not blocked")
        kinds = [f.kind for f in findings]
        self.assertIn("known-secret-shape", kinds,
                      f"blocked, but not by the shape floor — kinds were {kinds}")
        self.assertEqual(_matches_known_shape(self._CHUNKED), "aws-access-key")

    def test_hyphen_chunked_aws_key_blocks_too(self):
        self.assertEqual(_matches_known_shape("AKIA-IOSFODNN7-EXAMPLE"), "aws-access-key")
        self.assertTrue(has_secrets(scan(text="AKIA-IOSFODNN7-EXAMPLE")))

    def test_unseparated_aws_key_still_blocks(self):
        """The pre-existing guarantee — unchanged by the addendum."""
        findings = scan(text=self._UNSEPARATED)
        self.assertTrue(findings)
        self.assertIn("aws-access-key", [f.kind for f in findings],
                      "the signature layer must still catch the canonical form")

    def test_the_benign_identifiers_match_no_shape_at_all(self):
        """Pinned against the shape set DIRECTLY, not merely end-to-end: if a benign
        identifier ever starts matching a shape, that is the failure to catch early."""
        for ident in MEASURED_FALSE_POSITIVES + CASING_FALSE_POSITIVES:
            with self.subTest(identifier=ident):
                self.assertIsNone(_matches_known_shape(ident),
                                  f"{ident!r} matched a known-secret shape")

    def test_a_prefix_alone_is_never_a_match(self):
        """The property that keeps the floor's FP risk at ~zero: shapes are anchored FULL
        matches, so a vendor prefix with the wrong body length must not match."""
        for near_miss in ("AKIA", "AKIA_SHORT", "ASIA_PACIFIC_REGION_01",
                          "ASIA_PACIFIC_REGIONS_001", "ghp_tooshort",
                          "sk-short", "AIza" + "x" * 10,
                          "skipUserValidationForTests"):
            with self.subTest(candidate=near_miss):
                self.assertIsNone(_matches_known_shape(near_miss))

    def test_every_shape_in_the_set_is_live(self):
        """No dead entries: each shape matches its own canonical token."""
        canonical = {
            "aws-access-key": j("AKIA", "IOSFODNN7", "EXAMPLE"),
            "github-token": j("ghp_", "016c1f2e3a", "4b5d6e7f8a",   # 36-char body
                              "9b0c1d2e3f", "016c1f"),
            "openai-key": j("sk-", "abcdefghij", "klmnopqrst", "uvwxyz0123"),
            "slack-token": j("xoxb-", "1234567890", "1234567890", "abcdefABCD"),
            "gcp-api-key": j("AIza", "SyDaGkq3mN", "pL7wRtVbGc", "HdEf1JkMnO", "pXyZ3"),
        }
        for expected, token in canonical.items():
            with self.subTest(shape=expected):
                self.assertEqual(_matches_known_shape(token), expected)


class TestChunkedUnknownKeyBoundary(unittest.TestCase):
    """DOCUMENTED BOUNDARY — this exemption is deliberate, not an oversight.

    An UNKNOWN key type (no vendor prefix, so no shape to anchor on) that has been chunked by
    separators into word-shaped segments is EXEMPT. Grounding: a separator-chunked key is a
    TRANSFORMED key. Reversing an arbitrary transformation is outside a structural backstop's
    scope — separating this from `MOKATA_SESSION_ID_OVERRIDE_2` would need semantic knowledge
    (a dictionary) or the casing rule that caused the live 0.0.14 incident, and both of those
    bring the false positives back. What the layer DOES guarantee is pinned below: the
    untransformed original still blocks, and any key whose type IS known blocks even chunked.
    Widening the shape set is filed to R7 (0.1.2 full corpus overhaul)."""

    _RAW = j("Xq7Zk2Mv9p", "L4nR8vT3wB", "6cF1dG5hJ0")     # unknown-vendor key, 30 chars

    def test_the_untransformed_key_blocks(self):
        """The guarantee that still holds — this is why the boundary is acceptable."""
        self.assertTrue(has_secrets(scan(text=self._RAW)),
                        "the untransformed key must still block")

    def test_chunked_into_word_shaped_segments_is_exempt_by_design(self):
        chunked = "Session_Manager_Token_Value_2"       # the same class, word-shaped chunks
        self.assertIsNone(_matches_known_shape(chunked), "no vendor shape to anchor on")
        self.assertFalse(has_secrets(scan(text=chunked)),
                         "documented boundary: an unknown key type chunked into word-shaped "
                         "segments is indistinguishable from an ordinary identifier")


class TestLowercaseSlugsKeepTheirExemption(unittest.TestCase):
    """Word structure is not the only exemption. Lowercase separated slugs are often not
    word-shaped at all, so they need their own — and losing it is not hypothetical: it broke
    the team-audit suites, because macOS's own temp directory has exactly this shape."""

    def test_lowercase_slugs_pass(self):
        for slug in SLUG_FALSE_POSITIVES:
            with self.subTest(slug=slug):
                self.assertFalse(has_secrets(scan(text=slug)),
                                 f"FALSE POSITIVE on slug: {slug!r}")

    def test_the_slug_exemption_does_not_cover_all_hex_chunks(self):
        """The carve-out that keeps the slug rule from being an escape hatch: hex chunks
        joined by `_`/`-` were exempt on 0.0.15 purely for lacking a capital letter."""
        self.assertTrue(_entropy_blocks(ADVERSARIAL_TRUE_POSITIVES["hex-chunks-underscored"]))


class TestAdversarialRowsStillBlock(unittest.TestCase):
    """The false-negative bar. A widened exemption that lets any of these through is an
    escape hatch, not a fix — this is a security predicate and a one-way door."""

    def test_every_adversarial_shape_blocks(self):
        for label, secret in ADVERSARIAL_TRUE_POSITIVES.items():
            with self.subTest(secret=label):
                self.assertTrue(has_secrets(scan(text=secret)),
                                f"ESCAPE HATCH — adversarial shape passed: {label}")

    def test_separator_bearing_keys_block_on_entropy_not_only_signature(self):
        """The rows that have no named signature must be caught by the BACKSTOP itself —
        otherwise they'd be 'blocking' only by accident of a provider regex."""
        for label in ("urlsafe-base64-key", "urlsafe-base64-padded",
                      "hex-chunks-underscored", "camelcased-key-value",
                      "aws-secret-contiguous", "word-prefixed-random-key",
                      "aws-key-reversed", "digit-split-upper-run"):
            with self.subTest(secret=label):
                self.assertTrue(_entropy_blocks(ADVERSARIAL_TRUE_POSITIVES[label]),
                                f"{label} did not trip the entropy backstop")


# ── FUNCTION-WORD LIST (0.0.16, `84:74` merged with `84:68`) ──────────────────────────────
# THE MEASURED ROOT: `_is_wordish` requires `_MIN_WORD = 4`, so every English connective
# (`the`, `on`, `is`, `not`, `own`) counts as NOT-a-word. An identifier therefore looks MORE
# like a generated key the more it reads like a SENTENCE — and mokata's house style produces
# exactly that. All three rows below block on the SEGMENT fraction while their CHAR fraction
# is healthy.
#
# Each is spelled in FRAGMENTS, and not for tidiness: every one of them blocks the write of
# any file that spells it out, which is why the backlog rows that filed them had to be written
# the same way. A false positive that conceals its own bug report is the thing being fixed.
FUNCTION_WORD_FALSE_POSITIVES = {
    # `84:68` — a 57-char snake_case unittest METHOD name. char=0.533, seg=4/14=0.286.
    "snake-case test method name (84:68)":
        j("test_the_cap_is_DB_", "S7b_s_own_constant_", "not_a_second_number"),
    # `84:74` — a 37-char CamelCase test CLASS name. char=0.730, seg=4/9=0.444.
    "CamelCase test class name (84:74)":
        j("Test", "The", "V5", "Migration", "On", "A", "Populated", "V4", "Store"),
    # `84:72` — the class name SECRET-VALUE-SCAN measured as surviving: a class statement has
    # no binding, so value-side scanning could not reach it. char=0.591, seg=3/7=0.429.
    "CamelCase test class name (84:72)":
        j("Test", "Eval", "Feeds", "G2", "Not", "Is", "G2"),
}

# THE COUNTEREXAMPLE that decides the SHAPE of the fix, and the reason `84:74`'s own
# recommendation is not the one implemented. That row recommended DROPPING the segment-count
# term and keeping only the length-weighted CHAR term. This token defeats exactly that: ONE
# long English word carrying a majority of the CHARACTERS, with the rest random debris.
# char=0.550 — it would PASS a char-only rule — while seg=1/6=0.167 is what actually catches
# it. Dropping segF would have reopened this class; raising the word list keeps both terms.
CAMOUFLAGE_COUNTEREXAMPLE = j("Secretari", "at_kQ9_Xz", "_V_S_9_7")


class TestFunctionWordsAreWords(unittest.TestCase):
    """`84:74` + `84:68`, merged. The fix keeps the segment-fraction term AND its 0.50
    threshold and widens what counts as a WORD by a closed, frozen list of short English
    function words — the class the `_MIN_WORD = 4` floor was silently scoring against you."""

    def test_the_three_measured_identifiers_pass(self):
        for label, ident in FUNCTION_WORD_FALSE_POSITIVES.items():
            with self.subTest(identifier=label):
                self.assertFalse(has_secrets(scan(text=ident)),
                                 f"FALSE POSITIVE still live: {label}")

    def test_they_pass_inside_a_real_source_line(self):
        """The live cost is that ONE identifier makes the WHOLE FILE unsaveable, so the pin
        must be on the line as written, not on the bare token."""
        for label, ident in FUNCTION_WORD_FALSE_POSITIVES.items():
            for line in (f"class {ident}(unittest.TestCase):",
                         f"    def {ident}(self):",
                         f"# see {ident} above"):
                with self.subTest(identifier=label, line=line):
                    self.assertFalse(has_secrets(scan(text=line)), line)

    def test_the_rows_actually_reached_the_backstop(self):
        """Honesty pin: each row must be a real CANDIDATE (≥20 chars, char-class mixed), or
        it proves nothing about the predicate — it was merely never judged."""
        from mokata.govern.secrets import _candidate_runs
        for label, ident in FUNCTION_WORD_FALSE_POSITIVES.items():
            with self.subTest(identifier=label):
                self.assertTrue(list(_candidate_runs(ident)),
                                f"{label} never reaches the entropy backstop")

    def test_the_camouflage_token_still_blocks(self):
        """THE LOAD-BEARING PIN. `84:74` recommended dropping the segment-count term; this
        token is why that recommendation is UNSAFE. One long English word wins the CHAR
        majority (0.550) while the token is otherwise random debris — a char-only rule
        exempts it. It must still block, and it must block on the BACKSTOP, not by accident
        of a provider signature."""
        self.assertTrue(_entropy_blocks(CAMOUFLAGE_COUNTEREXAMPLE),
                        "the camouflage class is no longer caught — the segment-fraction "
                        "term has been weakened past what the function-word list allows")

    def test_the_aws_secret_access_key_is_UNMOVED(self):
        """THE SECOND LOAD-BEARING PIN, and the reason this fix is shaped as a WORD LIST.

        SECRET-VALUE-SCAN measured the obvious residual-(2) fix — treat short alpha segments
        as NEUTRAL, as digit segments already are — and rejected it because it un-blocks the
        40-char AWS SECRET ACCESS KEY: that key's segment majority goes 0.400 -> 1.000 while
        its char majority is already 0.625, so it becomes EXEMPT. It carries no vendor prefix,
        so this backstop is its ONLY catch, and the trade would be a total false negative on a
        real credential.

        A CLOSED word list does not make that trade. The key contains no English function word,
        so both its fractions are byte-identical before and after: char 0.625, seg 0.400.
        That is the difference between widening what a WORD is and neutralising short segments
        wholesale."""
        key = ADVERSARIAL_TRUE_POSITIVES["aws-secret-contiguous"]
        self.assertTrue(_entropy_blocks(key),
                        "the 40-char AWS secret access key stopped blocking — the word list "
                        "has become a neutralise-short-segments rule, which SECRET-VALUE-SCAN "
                        "measured and rejected")
        from mokata.govern.secrets import _is_wordish, _segments
        segs = _segments(key)
        alpha = [s for s in segs if s.isalpha()]
        wordish = [s for s in alpha if _is_wordish(s)]
        self.assertEqual(len(wordish) / len(alpha), 0.4,
                         "the key's segment fraction MOVED — it was 0.400 before this change")

    def test_the_acronym_density_residual_is_NOT_claimed_fixed(self):
        """Honesty pin. The two rows SECRET-VALUE-SCAN named as `KNOWN_SURVIVORS` dissolve
        here, but they dissolve because each carries a function word (`Not`/`Is`, `NO`) — NOT
        because acronym density was solved. An ALL-acronym constant has no function word and
        no segment ≥4 chars, so it still blocks. Claiming otherwise would overstate the fix."""
        self.assertTrue(_entropy_blocks(j("API_AWS", "_KEY_MAX", "_TTL_SEC_V2")),
                        "acronym density now passes — that is a DIFFERENT fix than the one "
                        "made here; update the report rather than the assertion")

    def test_the_word_list_is_closed_and_frozen(self):
        """It is a LIST, not a dictionary — the distinction the module's docstring insists on.
        Closed-class English (articles, prepositions, conjunctions, pronouns, auxiliaries),
        every entry 2-3 chars, and NO single letters: a one-character 'word' is the exact
        debris a generated key decomposes into, and admitting one would hand every key a free
        segment."""
        from mokata.govern.secrets import _FUNCTION_WORDS
        self.assertIsInstance(_FUNCTION_WORDS, frozenset)
        self.assertLessEqual(len(_FUNCTION_WORDS), 60)
        for w in _FUNCTION_WORDS:
            self.assertRegex(w, r"^[a-z]{2,3}$", f"{w!r} is not a 2-3 char lowercase word")

    def test_the_two_letter_entries_are_the_three_measured_ones(self):
        """LOAD-BEARING, and the reason the list is not simply 'closed-class English'.

        A 2-letter entry is cheap for a random key to hit by accident — 676 pairs exist, so an
        unrestricted 2-letter set collides with ~4% of random 2-char debris. Measured over
        4 x 30k generated keys (113,899 that blocked before this change), the full 2-letter set
        costs a 0.0325% FALSE NEGATIVE rate; `is`, `on` and `no` together cost 0.0018%, an 18x
        reduction, and they are the only ones that move a measured identifier at all.

        If a 2-letter word is added here, the sweep must be re-run — this assertion is the
        thing that forces that, rather than letting the list drift back toward 0.03%."""
        from mokata.govern.secrets import _FUNCTION_WORDS
        self.assertEqual({w for w in _FUNCTION_WORDS if len(w) == 2}, {"is", "on", "no"},
                         "the 2-letter set changed — re-run the false-negative sweep before "
                         "moving this assertion (each added pair costs ~0.001-0.004%)")

    def test_each_two_letter_entry_earns_its_place(self):
        """The other direction: none of the three is decoration. Removing any one of them
        drops a measured identifier back below the 0.50 segment majority."""
        from mokata.govern import secrets as S
        need = {"is": ("84:68", "84:72"), "on": ("84:74",)}
        for word, rows in need.items():
            trimmed = frozenset(S._FUNCTION_WORDS - {word})
            for row in rows:
                ident = FUNCTION_WORD_FALSE_POSITIVES[
                    next(k for k in FUNCTION_WORD_FALSE_POSITIVES if row in k)]
                segs = S._segments(ident)
                alpha = [s for s in segs if s.isalpha()]
                with_word = [s for s in alpha if S._is_wordish(s)]
                without = [s for s in alpha
                           if (s.isalpha() and s.lower() in trimmed) or (
                               len(s) >= S._MIN_WORD and S._is_wordish(s))]
                with self.subTest(word=word, row=row):
                    self.assertGreaterEqual(len(with_word) / len(alpha), 0.5)
                    self.assertLess(len(without) / len(alpha), len(with_word) / len(alpha),
                                    f"{word!r} buys nothing on {row} — drop it and re-measure")

    def test_a_function_word_is_matched_case_insensitively(self):
        """`The`, `THE` and `the` are the same word across camel, Pascal and SCREAMING."""
        from mokata.govern.secrets import _is_wordish
        for form in ("the", "The", "THE", "not", "Not", "NOT"):
            with self.subTest(form=form):
                self.assertTrue(_is_wordish(form))

    def test_the_list_does_not_rescue_random_short_debris(self):
        """The other direction. Short segments that are NOT function words stay non-words, so
        a key made of two- and three-character debris gains nothing."""
        from mokata.govern.secrets import _is_wordish
        for junk in ("kQ", "Xz", "zQ", "vBn", "xQ7", "mZk", "Rfi", "Hqu", "Umk", "Zyy"):
            with self.subTest(segment=junk):
                self.assertFalse(_is_wordish(junk), f"{junk!r} counted as a word")

    def test_the_threshold_and_the_segment_term_are_UNCHANGED(self):
        """Scope pin. The approved fix widens the WORD definition only: both fractions and
        both 0.50 thresholds stay exactly as they were, so this cannot drift into the
        `drop segF` shape the counterexample above rules out."""
        from mokata.govern import secrets as S
        self.assertEqual(S._WORDISH_CHAR_FRACTION, 0.5)
        self.assertEqual(S._WORDISH_SEGMENT_FRACTION, 0.5)
        self.assertEqual(S._MIN_WORD, 4)
        src = inspect.getsource(S._has_word_structure)
        self.assertIn("_WORDISH_SEGMENT_FRACTION", src,
                      "the segment-fraction term has been REMOVED — see "
                      "TestFunctionWordsAreWords.test_the_camouflage_token_still_blocks")


class TestPriorTruePositivesUnchanged(unittest.TestCase):
    """Every true positive already in the corpus must STILL block after the widening."""

    def test_named_credential_formats_still_block(self):
        for label, secret in corpus.TRUE_POSITIVES.items():
            with self.subTest(secret=label):
                self.assertTrue(has_secrets(scan(text=secret)),
                                f"REGRESSION — a real secret stopped blocking: {label}")

    def test_prior_benign_rows_still_pass(self):
        for benign in corpus.FALSE_POSITIVES:
            with self.subTest(value=benign):
                self.assertFalse(has_secrets(scan(text=benign)),
                                 f"REGRESSION — a benign row started blocking: {benign!r}")


class TestFuzzerHonesty(unittest.TestCase):
    """The corpus fuzzer must be structurally capable of catching a casing-based heuristic.

    Pinned here (not only in the corpus file) so a later edit that quietly narrows the
    generator back to lowercase fails loudly."""

    def test_benign_generator_emits_every_casing_convention(self):
        rng = random.Random(0xFEED)
        fuzz = corpus.TestEntropyFuzzInvariant()
        produced = [fuzz._benign_identifier(rng) for _ in range(300)]
        self.assertTrue(any(any(c.isupper() for c in w) for w in produced),
                        "the benign fuzzer generates lowercase-only identifiers — it is "
                        "still blind to the casing bug this stage fixed")
        self.assertTrue(any("_" in w and w.upper() == w for w in produced), "no SCREAMING_SNAKE")
        self.assertTrue(any("-" in w for w in produced), "no kebab-case")
        self.assertTrue(any("_" in w and w.lower() == w for w in produced), "no snake_case")
        self.assertTrue(any(w[0].islower() and any(c.isupper() for c in w)
                            for w in produced), "no camelCase")
        self.assertTrue(any(w[0].isupper() for w in produced), "no PascalCase")

    def test_positive_generator_is_not_distinguishable_by_casing_alone(self):
        """A classifier that says 'has an uppercase letter -> secret' must MISCLASSIFY the
        benign fuzz stream. If it doesn't, casing is still the tell and the fuzzer is blind."""
        rng = random.Random(0xBEEF)
        fuzz = corpus.TestEntropyFuzzInvariant()
        benign = [fuzz._benign_identifier(rng) for _ in range(200)]
        misclassified = [b for b in benign if any(c.isupper() for c in b)]
        self.assertGreaterEqual(
            len(misclassified), 40,
            "fewer than 20% of benign fuzz samples carry uppercase — a 'block any capital' "
            "predicate would still look correct to this fuzzer")

    def test_benign_generated_identifiers_never_block(self):
        rng = random.Random(0x1D3A)
        fuzz = corpus.TestEntropyFuzzInvariant()
        for _ in range(400):
            ident = fuzz._benign_identifier(rng)
            self.assertFalse(_entropy_blocks(ident),
                             f"false-positive entropy on generated identifier {ident!r}")

    def test_generated_secret_shapes_always_block(self):
        rng = random.Random(0x2E4B)
        alpha = string.ascii_letters + string.digits
        for _ in range(400):
            n = rng.randint(28, 44)
            chars = [rng.choice("ghijklmnopqrstuvwxyz"), rng.choice(string.digits)]
            chars += [rng.choice(alpha) for _ in range(n - 2)]
            rng.shuffle(chars)
            s = "".join(chars)
            self.assertTrue(_entropy_blocks(s), f"missed secret-shaped {s!r}")


if __name__ == "__main__":
    unittest.main()
