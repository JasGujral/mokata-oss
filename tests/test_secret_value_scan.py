"""SECRET-VALUE-SCAN (0.0.16) — the entropy backstop judges assigned VALUES, not NAMES.

THE ROOT (Jas 2026-07-27). `_scan_entropy` was POSITION-BLIND: it ran `_TOKEN_RE.findall(line)`
and tested every separator-split run >=20 chars with NO notion of which side of an `=` the run
sat on. An identifier was therefore scanned as a candidate credential. Every false positive in
this family shares that root — the 0.0.14 incident, SECRET-FP's two residuals, and the older
CamelCase FP. SECRET-FP made the identifier heuristic much better, but a heuristic applied to
the wrong operand is still the wrong operand.

THE STAKES. `secret-guard` is documented non-overridable (G4/I1) — no setting, no allowlist, no
escape. A false positive is therefore not an annoyance, it is a total wall on a legitimate
write; measured live at 0.0.15, users stopped using mokata because they could not proceed.

THE PRECEDENT (not a new concept). `_SIGNATURES` already carries `secret-assignment`, which
reasons about a BINDING. The signature layer has always been binding-aware; the entropy layer
was not. This stage brings the backstop up to the layer above it.

THE PUREST FORM OF THE BUG, measured (see `TestTheNamePaddedTheValue`): a line binding a
10-char env-var name to a 9-char value tokenizes as ONE 20-char run, because `_TOKEN_RE`'s
alphabet includes `=`. The value is 9 characters — far below the 20-char candidate floor.
Without the NAME there is no candidate at all: the identifier did not merely fail a heuristic,
it MANUFACTURED the candidate it was then judged as.

WHAT A LINE-BASED PARSER CAN AND CANNOT DECIDE (deliverable 0, measured against the real
callers: hook_cli secret-guard for tool content AND Bash command lines, govern/gate,
govern/outbound, config_cmd, share, memory/share, team_journal, stacks, perf,
knowledge/graph_adopt). `scan()` never receives Python source — it receives source in any
language, shell command lines, .env files, YAML, TOML, JSON both pretty-printed AND dumped to
a SINGLE line, markdown prose, unified diffs, and untrusted community manifests. So:

  CAN decide  — single-line bindings: name/value split by `=` or `:`, quoted JSON pairs,
                `--flag=value`, `KEY=value`, and YAML/TOML pairs, INCLUDING several on one
                line (a `json.dumps` one-liner from team_journal/session_bundle is the common
                case, so a parser that split a line at its FIRST operator would be wrong).
  CANNOT decide — a value spanning lines (a triple-quoted string, a YAML block scalar, a
                heredoc body). Those continuation lines carry no binding, so they fall back to
                the bare-token conjunction.

That fallback is why the approach is sound rather than merely convenient: the ONLY thing ever
exempted is the identifier run immediately left of a binding operator. Everything a line-based
parser cannot decide keeps today's rule, so every ambiguity errs toward SCANNING. Fail-closed.

SCOPE BOUNDARY: this stage changes the entropy layer's OPERAND (and, as a grounded deviation,
one measured word-structure weakness — see `TestDigitBoundaryDeviation`). It does not change
which surfaces consult the scanner, adds no config knob and no allowlist file, and
`secret-guard` stays non-overridable. R7's full corpus overhaul (broader provider formats,
corpus generation) stays 0.1.2 — do not grow this file into it.

Real-secret literals are ASSEMBLED from sub-20-char fragments at runtime (the same convention
as `test_secret_corpus.py` / `test_secret_fp.py`) so this source file carries no blockable
literal. NOTE: the BENIGN identifiers under test are assembled too, and the prose here refers
to them by their Python constant names rather than by their literal text — because on 0.0.15
they block, and this file could not otherwise be written through mokata's own guard. That is
the bug, demonstrated on this file.
All "secrets" here are LEAK-CANARY fakes — never a real key.
Dependency-free, deterministic.
"""

import os
import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.govern import secrets
from mokata.govern.secrets import has_secrets, scan, _matches_known_shape

import test_secret_corpus as corpus
import test_secret_fp as fp

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _is_debris(name):
    """A directory under `src/` or `tests/` that the REPO does not carry — build output, import
    caches, and `.mokata/` state that only exists once the suite has been run in this tree.

    ★ Stage 29 rider. The FP bar below is measured against "a REAL corpus", and the corpus was a
    filesystem walk, so it also swept whatever a given machine happened to have lying around —
    here 327 `.mokata/temp_local/state/*.json` files full of hex run-ids, plus `mokata.egg-info`.
    A bar whose corpus depends on whether the suite has been run before is not one bar. Pruning
    can only REMOVE findings, so it cannot mask a new false positive; and the size floor below
    counts `.py` only, of which this repo has no untracked file.
    """
    return name == "__pycache__" or name == ".mokata" or name.endswith(".egg-info")


def j(*parts):
    """Join fragments — the runtime string; the source only holds the (benign) fragments."""
    return "".join(parts)


def _entropy_blocks(s):
    return any(f.layer == "entropy" for f in scan(text=s))


def _kinds(s):
    return [f.kind for f in scan(text=s)]


# ── The identifiers that block on 0.0.15 purely because of their NAME. Assembled, because a
#    literal here would block this very file. ────────────────────────────────────────────
#
# Rows 1-3 are SECRET-FP's own measured residuals; row 4 is the AKIA/ASIA strip collision the
# stage prompt names; row 5 is an acronym-dense constant. Rows 1-3 were found by MEASURING
# mokata's own src/ + tests/ (see `TestOwnCorpusFalsePositiveRate`), not by invention — each
# cites the real file it blocks in.
DRAFT_VALIDATOR = j("Draft", "202012", "Validator")        # src/mokata/schema.py:114,117,126
EVAL_TEST_CLASS = j("TestEval", "FeedsG2", "NotIsG2")      # tests/test_mcp_r_d3_eval.py:885
NO_SUCH_VAR = j("MOKATA_NO", "_SUCH_VAR", "_35A")          # tests/test_stage35a…:120,135,152
ASIA_CONST = j("ASIA_PAC", "IFIC_REGI", "ONS_01")          # the AKIA/ASIA strip collision
ACRONYM_CONST = j("API_AWS_KEY", "_MAX_TTL", "_SEC_V2")    # acronym-dense constant

# The purest form of the root: the name manufactures the candidate.
SHORT_VALUE = j("abc", "123", "XYZ")                       # 9 chars — under the 20-char floor
NAME_PADS_VALUE = j("AWS_SEC", "RET") + "=" + SHORT_VALUE  # tests/test_self_protect.py:811

# A blank placeholder in a `.env.example` — the name plus `=` and NO value at all.
BLANK_ENV_LINE = j("STRIPE_PUBLIS", "HABLE_KEY") + "="

# LEAK-CANARY fakes used as the VALUE side throughout.
UNKNOWN_KEY = j("xY9kZ2mQ7p", "L4nR8vT3wB", "6cF1dG5hJ0")   # unknown-vendor 30-char key
AWS_KEY = j("AKIA", "IOSFODNN7", "EXAMPLE")
AWS_KEY_CHUNKED = j("AKIA", "_", "IOSFODNN7", "_", "EXAMPLE")


class TestTheNamePaddedTheValue(unittest.TestCase):
    """The root, in its purest measured form. `_TOKEN_RE`'s alphabet includes `=`, so the
    whole binding is ONE 20-char token: the NAME manufactured the candidate."""

    def test_the_value_alone_is_not_even_a_candidate(self):
        """9 characters — the entropy layer's floor is 20. There is nothing here to judge."""
        self.assertLess(len(SHORT_VALUE), 20)
        self.assertEqual(scan(text=SHORT_VALUE), [])

    def test_the_whole_binding_is_one_token_whose_length_comes_from_the_name(self):
        self.assertEqual(secrets._TOKEN_RE.findall(NAME_PADS_VALUE), [NAME_PADS_VALUE])
        self.assertGreaterEqual(len(NAME_PADS_VALUE), 20)

    def test_the_binding_does_not_block(self):
        """GREEN AFTER: the value side is judged, and it is too short to be a candidate."""
        self.assertEqual(scan(text=NAME_PADS_VALUE), [],
                         "FALSE POSITIVE — the identifier NAME was scanned as a credential")

    def test_a_blank_env_placeholder_does_not_block(self):
        """`.env.example` with an empty value: name + operator + nothing. There is no value
        to judge, so there is nothing to block on."""
        self.assertEqual(scan(text=BLANK_ENV_LINE), [])


class TestMeasuredNameFalsePositives(unittest.TestCase):
    """Deliverable 1 — each row blocks on 0.0.15 purely because of its NAME. Green after."""

    def test_identifiers_pass_in_binding_position(self):
        for label, ident in (("draft-validator", DRAFT_VALIDATOR),
                             ("acronym-const", ACRONYM_CONST),
                             ("no-such-var", NO_SUCH_VAR)):
            for line in (ident + " = 30", ident + ": 30", '"' + ident + '": 30',
                         "--" + ident + "=30"):
                with self.subTest(row=label, line=line):
                    self.assertEqual(scan(text=line), [],
                                     "FALSE POSITIVE on a legitimate identifier NAME")

    def test_the_identifiers_pass_inside_a_real_source_file(self):
        """The incident shape: ordinary code, not bare rows."""
        src = ("import os\n"
               "\n"
               + ACRONYM_CONST + " = 3600\n"
               + NO_SUCH_VAR + " = os.environ.get(\"HOME\")\n"
               "validator = getattr(mod, \"" + DRAFT_VALIDATOR + "\", None)\n")
        self.assertEqual(scan(text=src), [],
                         "FALSE POSITIVE on a source file of legitimate identifiers")

    def test_rows_actually_reach_the_entropy_backstop(self):
        """A row under 20 chars, or with no digit, is exempted by the char-class gate up
        front and would prove nothing about the predicate."""
        for ident in (DRAFT_VALIDATOR, EVAL_TEST_CLASS, NO_SUCH_VAR, ASIA_CONST,
                      ACRONYM_CONST, NAME_PADS_VALUE):
            with self.subTest(identifier=ident):
                self.assertGreaterEqual(len(ident), 20, "row never reaches the backstop")
                self.assertTrue(any(c.isdigit() for c in ident), "row has no digit")
                self.assertTrue(any(c.isalpha() for c in ident), "row has no letter")


class TestBindingShapesFromTheInventory(unittest.TestCase):
    """Deliverable 0's shapes, each decided correctly: the VALUE side is the candidate, the
    NAME side is not."""

    def test_value_side_blocks_in_every_binding_shape(self):
        for shape, line in (
            ("python-assign", 'token = "%s"' % UNKNOWN_KEY),
            ("python-annot", 'token: str = "%s"' % UNKNOWN_KEY),
            ("json-pair", '{"token": "%s"}' % UNKNOWN_KEY),
            ("json-oneline-many", '{"a": 1, "b": "x", "token": "%s"}' % UNKNOWN_KEY),
            ("yaml-pair", "token: %s" % UNKNOWN_KEY),
            ("toml-pair", 'token = "%s"' % UNKNOWN_KEY),
            ("env-line", "TOKEN=%s" % UNKNOWN_KEY),
            ("cli-flag", "--api-token=%s" % UNKNOWN_KEY),
            ("shell-export", "export SESSION_TOKEN=%s" % UNKNOWN_KEY),
            ("walrus", 'if (tok := "%s"):' % UNKNOWN_KEY),
        ):
            with self.subTest(shape=shape):
                self.assertTrue(_entropy_blocks(line),
                                "ESCAPE HATCH — a credential on the VALUE side passed")

    def test_name_side_is_not_a_candidate_in_every_binding_shape(self):
        for shape, line in (
            ("python-assign", "%s = 30" % ACRONYM_CONST),
            ("python-annot", "%s: int = 30" % ACRONYM_CONST),
            ("json-pair", '{"%s": 30}' % ACRONYM_CONST),
            ("yaml-pair", "%s: 30" % ACRONYM_CONST),
            ("toml-pair", "%s = 30" % ACRONYM_CONST),
            ("env-line", "%s=30" % ACRONYM_CONST),
            ("cli-flag", "--%s=30" % ACRONYM_CONST),
            ("attribute", "mod.%s = 30" % DRAFT_VALIDATOR),
            ("diff-added", "+%s = 30" % ACRONYM_CONST),
            ("diff-removed", "-%s = 30" % ACRONYM_CONST),
        ):
            with self.subTest(shape=shape):
                self.assertEqual(scan(text=line), [], "FALSE POSITIVE on a NAME")

    def test_several_bindings_on_one_line_are_each_decided(self):
        """A `json.dumps` one-liner (team_journal / session_bundle) is the common real shape,
        so a parser that split the line at its FIRST operator would be wrong."""
        line = '{"%s": 1, "%s": 2, "token": "%s"}' % (ACRONYM_CONST, NO_SUCH_VAR, UNKNOWN_KEY)
        self.assertTrue(_entropy_blocks(line), "the value on a multi-binding line was missed")
        clean = '{"%s": 1, "%s": 2, "n": 3}' % (ACRONYM_CONST, NO_SUCH_VAR)
        self.assertEqual(scan(text=clean), [], "FALSE POSITIVE on a multi-binding line")


class TestBareTokensKeepTheConjunction(unittest.TestCase):
    """Deliverable 3 — with NO binding, today's rule stands. A secret pasted alone, in prose,
    in a URL, in a comment, or inside a heredoc must still block."""

    def test_bare_and_embedded_secrets_still_block(self):
        for where, line in (
            ("bare", UNKNOWN_KEY),
            ("prose", "the rotation key is %s so keep it safe" % UNKNOWN_KEY),
            ("url-path", "https://api.example.com/v1/%s/resource" % UNKNOWN_KEY),
            ("url-query", "https://api.example.com/v1?token=%s" % UNKNOWN_KEY),
            ("comment-hash", "# leftover: %s" % UNKNOWN_KEY),
            ("comment-slash", "// leftover: %s" % UNKNOWN_KEY),
            ("markdown-code", "    %s" % UNKNOWN_KEY),
            ("list-item", "- %s" % UNKNOWN_KEY),
        ):
            with self.subTest(where=where):
                self.assertTrue(_entropy_blocks(line),
                                "ESCAPE HATCH — a bare secret passed at %s" % where)

    def test_a_heredoc_body_still_blocks(self):
        """A heredoc body has no binding on its own lines, so it falls back to the bare
        rule — the CANNOT-decide case erring toward scanning."""
        text = "cat <<'EOF' > out.txt\n%s\nEOF\n" % UNKNOWN_KEY
        self.assertTrue(_entropy_blocks(text))

    def test_a_trailing_operator_with_no_value_is_not_a_binding(self):
        """A secret followed by a dangling operator must not read as a NAME — there is no
        value, so there is no binding, so the token stays bare."""
        for line in (UNKNOWN_KEY + ":", UNKNOWN_KEY + ": ", UNKNOWN_KEY + " ="):
            with self.subTest(line=line):
                self.assertTrue(_entropy_blocks(line),
                                "ESCAPE HATCH — a dangling operator bought the name exemption")

    def test_base64_padding_is_not_a_binding_operator(self):
        """`=` is inside `_TOKEN_RE`'s alphabet, so base64 padding must never be mistaken for
        an assignment — otherwise a padded key would read as its own NAME."""
        padded = fp.ADVERSARIAL_TRUE_POSITIVES["urlsafe-base64-padded"]
        for line in (padded, padded + " trailing words", "note: " + padded):
            with self.subTest(line=line):
                self.assertTrue(_entropy_blocks(line),
                                "ESCAPE HATCH — base64 padding read as a binding")

    def test_a_single_pad_char_followed_by_words_is_not_a_binding(self):
        """One `=` of padding, then more text on the line. The value side must be GLUED to
        the operator for a glued binding to count, or padding would bind."""
        single_pad = j("dGhpc0lzQV", "-9mYWtlS2V5", "_Rk9SX1RFU1") + "="
        self.assertTrue(_entropy_blocks(single_pad + " and then some prose"),
                        "ESCAPE HATCH — single base64 padding read as a binding")


class TestFloorStaysPositionIndependent(unittest.TestCase):
    """Deliverable 4 — the anchored `_KNOWN_SHAPES` floor (raw AND separator-stripped) runs
    regardless of binding position. A credential shape ANYWHERE is a credential. This is what
    makes value-side scanning safe to do at all: a secret cannot buy the name exemption by
    being written in name position."""

    def test_a_known_shape_blocks_in_name_position(self):
        for shape, line in (
            ("python-assign", "%s = 1" % AWS_KEY),
            ("env-line", "%s=1" % AWS_KEY),
            ("json-key", '{"%s": 1}' % AWS_KEY),
            ("yaml-key", "%s: 1" % AWS_KEY),
            ("cli-flag", "--%s=1" % AWS_KEY),
        ):
            with self.subTest(shape=shape):
                self.assertTrue(has_secrets(scan(text=line)),
                                "ESCAPE HATCH — a credential shape passed in NAME position")

    def test_the_chunked_shape_blocks_in_name_position_via_the_floor(self):
        """The separator-stripped arm specifically — SECRET-FP's headline catch must not be
        scoped to the value side."""
        self.assertIn("known-secret-shape", _kinds("%s = 1" % AWS_KEY_CHUNKED),
                      "the stripped arm of the floor stopped running on the name side")

    def test_the_floor_still_fires_on_the_value_side(self):
        self.assertIn("known-secret-shape", _kinds("key = %s" % AWS_KEY_CHUNKED))

    def test_the_signature_layer_is_position_independent_too(self):
        """The second half of why name-exemption is safe: named formats never depended on
        the entropy backstop."""
        self.assertIn("aws-access-key", _kinds("%s = 1" % AWS_KEY))


class TestDigitBoundaryDeviation(unittest.TestCase):
    """GROUNDED DEVIATION (declared in the report and in `_has_boundary_marker`).

    Binding awareness alone does NOT clear mokata's own source: `DRAFT_VALIDATOR` blocks in
    `src/mokata/schema.py` at three sites, two of which are a COMMENT and a quoted string on
    the VALUE side — no binding to lean on. Its root is a different SECRET-FP rule: a digit was
    never a word boundary, so it decomposed with no boundary marker at all, even though both
    of its alpha segments are word-shaped.

    SECRET-FP excluded digits for a measured reason — a REVERSED AWS key also decomposes into
    two vowel-bearing runs across a digit. So the deviation is the narrowest rule that
    separates them: a digit run is a boundary marker ONLY when the alpha run FOLLOWING it is a
    Capitalized word — the camel/Pascal-across-a-digit convention. Two ALL-CAPS runs split by
    a digit stay unmarked, so the mangled-key shapes cannot buy anything from it."""

    def test_camel_across_a_digit_is_a_boundary(self):
        self.assertTrue(secrets._has_boundary_marker(DRAFT_VALIDATOR))
        self.assertEqual(scan(text=DRAFT_VALIDATOR), [])

    def test_the_reversed_aws_key_gains_nothing(self):
        reversed_key = AWS_KEY[::-1]
        self.assertFalse(secrets._has_boundary_marker(reversed_key),
                         "an ALL-CAPS run split by a digit must not read as a word boundary")
        self.assertTrue(_entropy_blocks(reversed_key))

    def test_the_digit_split_upper_run_gains_nothing(self):
        self.assertTrue(_entropy_blocks(
            fp.ADVERSARIAL_TRUE_POSITIVES["digit-split-upper-run"]))


class TestResidualsSupersededOrNamed(unittest.TestCase):
    """Deliverable 5 — supersede, don't accumulate. Each folded residual is checked here and
    either DISSOLVES or is named as still live, with its grounding."""

    def test_residual_3_camelcase_fp_dissolved_in_general(self):
        """doc 84's older Secret-guard CamelCase FP ('long CamelCase-with-digit identifiers,
        e.g. test class names'), and SECRET-FP's three measured rows. Dissolved by SECRET-FP's
        case-boundary splitting and, for the digit-only-boundary tail, by this stage."""
        for ident in fp.MEASURED_FALSE_POSITIVES + fp.CASING_FALSE_POSITIVES:
            with self.subTest(identifier=ident):
                self.assertFalse(has_secrets(scan(text=ident)))
        self.assertEqual(scan(text=DRAFT_VALIDATOR), [])

    def test_residual_3_test_class_name_tail_DISSOLVED_by_the_function_word_list(self):
        """SUPERSEDED 2026-07-31 (`84:74` + `84:68`, the function-word list).

        This test used to assert the OPPOSITE: that a `class <Name>(TestCase):` line has no
        binding for value-side scanning to use, so an acronym-dense test class name still
        blocked and the rename workaround was still required. The rename workaround was always
        the WRONG repair — it lets an entropy heuristic set the naming convention — and the
        tail is now closed at the predicate instead: `Not` and `Is` are words, so this name
        clears the segment majority (0.429 -> 0.714) without any binding.

        The assertion is INVERTED rather than deleted, so the residual's history stays legible
        and a regression fails here rather than going quiet."""
        self.assertFalse(_entropy_blocks("class %s(unittest.TestCase):" % EVAL_TEST_CLASS),
                         "residual (3)'s test-class-name tail regressed — see "
                         "test_secret_fp.TestFunctionWordsAreWords")

    def test_residual_2_acronym_density_dissolves_in_binding_position(self):
        """The real-code shape of an acronym-dense constant is a NAME, and it now passes."""
        for ident in (ACRONYM_CONST, NO_SUCH_VAR):
            with self.subTest(identifier=ident):
                self.assertEqual(scan(text=ident + " = 30"), [])

    def test_residual_2_acronym_density_is_STILL_LIVE_when_bare(self):
        """NOT DISSOLVED — reported, not quietly filed.

        An acronym-dense identifier with no binding (a bare row, a class name, or one used as
        a VALUE rather than a name) still fails the word-structure segment majority and still
        blocks.

        Why it was not fixed here, MEASURED rather than asserted. The obvious fix is to treat
        short alpha segments as neutral (as digit segments already are) instead of counting
        them against the word majority. It does clear both survivors — and it also un-blocks
        the 40-char AWS SECRET ACCESS KEY, whose long alpha segments (Jalr / FEMIK / MDEN /
        CYEXAMPLEKEY) are ALL word-shaped: its segment majority goes 0.400 -> 1.000 while its
        character majority is already 0.625, so it becomes EXEMPT. That key has no distinctive
        vendor prefix, so this backstop is its ONLY catch — the trade is a total false negative
        on a real credential in exchange for a false positive on an identifier.

        The same measurement also shows the fix would not even be sufficient: an ALL-acronym
        constant has no alpha segment long enough to be a word, so it blocks either way and is
        cleared here only by binding awareness.

        Closing this needs semantic knowledge (a dictionary) that this layer deliberately does
        not have — and a dictionary/casing rule is exactly where the original false positives
        came from. It is R7's corpus overhaul (0.1.2), not this stage.

        STILL TRUE 2026-07-31, and narrowed to what it actually covers. The function-word list
        (`84:74` + `84:68`) dissolved the two rows this test used to name — but it dissolved
        them because each carries a function word (`Not`/`Is`, `NO`), NOT because acronym
        density was solved. An ALL-acronym constant has no function word and no segment ≥4
        chars, so it still blocks, and the measured counter-example below is still the reason.
        The assertion now names the shape that genuinely survives instead of two rows that
        happened to."""
        self.assertTrue(_entropy_blocks(ACRONYM_CONST),
                        "residual (2) status changed — update the report")
        self.assertTrue(_entropy_blocks('{"acronym": "%s"}' % ACRONYM_CONST))

    def test_the_counter_example_that_kept_residual_2_open(self):
        """The row any residual-(2) fix must not break, pinned so a later attempt is measured
        against it rather than re-deriving it: the 40-char AWS secret access key. It has no
        vendor prefix, so neither the signature layer nor the shape floor covers it and the
        entropy backstop is the only thing standing between it and a commit."""
        aws_secret = fp.ADVERSARIAL_TRUE_POSITIVES["aws-secret-contiguous"]
        self.assertTrue(_entropy_blocks(aws_secret))
        self.assertIsNone(_matches_known_shape(aws_secret),
                          "no shape floor covers it — the backstop is its ONLY catch")

    def test_short_segments_are_load_bearing_for_that_counter_example(self):
        """The mechanism, pinned directly: it is the SHORT alpha segments that hold the AWS
        secret key's segment majority under 0.5. Neutralise them and the key walks."""
        aws_secret = fp.ADVERSARIAL_TRUE_POSITIVES["aws-secret-contiguous"]
        alpha = [s for s in secrets._segments(aws_secret) if s.isalpha()]
        long_alpha = [s for s in alpha if len(s) >= secrets._MIN_WORD]
        wordish = [s for s in alpha if secrets._is_wordish(s)]
        self.assertLess(len(wordish) / len(alpha), 0.5, "today: blocked on segment majority")
        self.assertEqual(len(wordish) / len(long_alpha), 1.0,
                         "short-neutral: EVERY long segment is word-shaped — the key escapes")

    def test_residual_1_akia_asia_strip_collision_is_STILL_LIVE(self):
        """NOT DISSOLVED — reported, not quietly filed.

        `ASIA_CONST` separator-strips to a run that IS `ASIA` + 16 upper-alnum, i.e. exactly
        the documented AWS access-key-id shape, so the floor fires. Deliverable 4 requires the
        floor to stay position-independent on BOTH arms, so it fires on the name side too and
        binding awareness cannot dissolve this.

        Narrowing the stripped arm was tried and rejected: every candidate rule (e.g. 'do not
        strip when all pure-alpha chunks are word-shaped') also un-blocks `AWS_KEY_CHUNKED`,
        which is SECRET-FP's headline catch. Widening or refining the shape set is R7's
        (0.1.2). This is a deliberate trade in the SAFE direction, and the shape does not
        occur anywhere in mokata's own corpus."""
        self.assertEqual(_matches_known_shape(ASIA_CONST), "aws-access-key")
        self.assertIn("known-secret-shape", _kinds(ASIA_CONST + " = 1"))

    def test_the_neighbouring_near_misses_still_do_not_collide(self):
        """SECRET-FP's own near-miss rows: only the exact-16 body collides."""
        for near in (j("ASIA_PAC", "IFIC_REGI", "ON_01"),
                     j("ASIA_PAC", "IFIC_REGI", "ONS_001")):
            with self.subTest(candidate=near):
                self.assertIsNone(_matches_known_shape(near))


class TestNoRegressionOnTruePositives(unittest.TestCase):
    """Deliverable 6, the non-negotiable — a value-side scan must be STRICTER on values and
    never looser overall. Every credential shape that blocked before must still block."""

    def test_secret_corpus_true_positives_unchanged(self):
        for label, secret in corpus.TRUE_POSITIVES.items():
            with self.subTest(secret=label):
                self.assertTrue(has_secrets(scan(text=secret)),
                                "REGRESSION — a real secret stopped blocking: %s" % label)

    def test_secret_fp_adversarial_rows_unchanged(self):
        for label, secret in fp.ADVERSARIAL_TRUE_POSITIVES.items():
            with self.subTest(secret=label):
                self.assertTrue(has_secrets(scan(text=secret)),
                                "ESCAPE HATCH — adversarial shape passed: %s" % label)

    def test_adversarial_rows_still_trip_the_BACKSTOP_not_only_a_signature(self):
        for label in ("urlsafe-base64-key", "urlsafe-base64-padded",
                      "hex-chunks-underscored", "camelcased-key-value",
                      "aws-secret-contiguous", "word-prefixed-random-key",
                      "aws-key-reversed", "digit-split-upper-run"):
            with self.subTest(secret=label):
                self.assertTrue(_entropy_blocks(fp.ADVERSARIAL_TRUE_POSITIVES[label]))

    def test_the_chunked_key_catch_is_intact(self):
        self.assertEqual(_matches_known_shape(AWS_KEY_CHUNKED), "aws-access-key")

    def test_prior_benign_rows_still_pass(self):
        for benign in corpus.FALSE_POSITIVES + fp.SLUG_FALSE_POSITIVES:
            with self.subTest(value=benign):
                self.assertFalse(has_secrets(scan(text=benign)))

    def test_every_true_positive_still_blocks_when_ASSIGNED(self):
        """The stage's own direction of travel: on the VALUE side the scan must be at least
        as strict as it was bare."""
        for label, secret in corpus.TRUE_POSITIVES.items():
            if "\n" in secret:
                continue
            with self.subTest(secret=label):
                self.assertTrue(has_secrets(scan(text='credential = "%s"' % secret)),
                                "REGRESSION — %s stopped blocking as a VALUE" % label)


# ── The empirical bar: measure the false-positive rate against a REAL corpus. ────────────
def _entropy_findings_in_repo():
    """Every entropy-layer finding across mokata's own src/ + tests/, with the offending run.

    This repo contains no real credential, so every finding is either a PLANTED LEAK-CANARY
    fake (a test fixture) or a false positive. Thousands of real identifiers in every casing
    convention — including the SCREAMING_SNAKE conventions that caused the incident."""
    exts = (".py", ".md", ".json", ".toml", ".yml", ".yaml", ".sh", ".cfg", ".txt")
    out = []
    for target in ("src", "tests"):
        for dirpath, dirnames, filenames in os.walk(os.path.join(REPO, target)):
            dirnames[:] = [d for d in dirnames if not _is_debris(d)]
            for name in sorted(filenames):
                if not name.endswith(exts):
                    continue
                path = os.path.join(dirpath, name)
                try:
                    with open(path, encoding="utf-8") as fh:
                        text = fh.read()
                except (UnicodeDecodeError, OSError):
                    continue
                for lineno, line in enumerate(text.splitlines(), start=1):
                    for finding in secrets._scan_entropy(line):
                        runs = [sub for tok in secrets._TOKEN_RE.findall(line)
                                for sub in secrets._SEP_RE.split(tok)
                                if len(sub) >= 20 and secrets._scan_entropy(sub)]
                        out.append((os.path.relpath(path, REPO), lineno, finding.kind,
                                    runs, line.strip()))
    return out


class TestOwnCorpusFalsePositiveRate(unittest.TestCase):
    """THE EMPIRICAL BAR. Example rows are necessary but not sufficient — this predicate has
    repeatedly passed its own tests while failing in the field, so it is measured against a
    real corpus of thousands of real identifiers.

    Every surviving finding must be a DELIBERATELY PLANTED fake. Anything else is a false
    positive on legitimate code, which — `secret-guard` being non-overridable — is a total
    wall on a legitimate write."""

    # The planted LEAK-CANARY credentials this repo carries on purpose (fixtures, and the
    # scanner's own in-file documentation of the shapes it catches). Matched against the
    # offending run, and assembled so this list is not itself a blockable literal.
    PLANTED = (
        AWS_KEY,                                            # the canonical fixture key
        j("AKIA", "1234567890", "ABCDEF"),                  # doc/test variant
        AWS_KEY[::-1],                                      # its REVERSE, documented in-file
        j("dGhpc2lzYX", "NlY3JldGtl", "eTEyMzQ1Ng"),        # base64 blob fixture
        j("aB3-xY9z-kQ", "2m-NpL7-wRt", "V-bGcH-dEf1"),     # base64url fixture
        UNKNOWN_KEY,                                        # unknown-vendor key fixture
        j("s3cr3t-token", "-AKIA123456", "7890"),           # mixed fixture
        AWS_KEY + j("KEYDATA1234", "567890xx"),             # long variant
        AWS_KEY_CHUNKED,                                    # the CHUNKED key (SECRET-FP)
        j("AKIA", "-", "IOSFODNN7", "-", "EXAMPLE"),        # its hyphen form
    )

    # The residual-(2) rows that survive on THIS repo's own source, named rather than left
    # silent (see `TestResidualsSupersededOrNamed` for the measurement that keeps each open).
    #
    # EMPTY as of 2026-07-31 (`84:74` + `84:68`, the function-word list). Both prior entries —
    # a test class NAME and an identifier used as a VALUE, neither reachable by binding
    # awareness — cleared at the predicate: each carries a function word, so each now makes the
    # segment majority. Measured on this repo: 28 entropy findings -> 24, and all 4 removed
    # were these. The remaining 24 are planted LEAK-CANARY fakes.
    #
    # This is a REGISTER, not a target: the empty set is the claim that mokata's own source now
    # has ZERO entropy false positives, and `test_no_unnamed_false_positive_survives…` is what
    # fails if a new one appears. Acronym density is NOT fixed — it simply has no surviving
    # occurrence here (`test_residual_2_acronym_density_is_STILL_LIVE_when_bare` holds it).
    KNOWN_SURVIVORS = {}

    def _is_planted(self, runs):
        return any(p in run or run in p for run in runs for p in self.PLANTED)

    def _is_named_survivor(self, runs):
        return any(run in self.KNOWN_SURVIVORS for run in runs)

    def test_no_unnamed_false_positive_survives_on_mokata_s_own_source(self):
        """The bar: every finding on this repo is a PLANTED fake or an already-NAMED residual.
        Anything else is a new false positive on legitimate code — which, secret-guard being
        non-overridable, is a total wall on a legitimate write."""
        findings = _entropy_findings_in_repo()
        survivors = [f for f in findings
                     if not self._is_planted(f[3]) and not self._is_named_survivor(f[3])]
        if survivors:
            report = "\n".join(
                "  %s:%d [%s] runs=%s\n      | %s" % (rel, ln, kind, runs, src[:140])
                for rel, ln, kind, runs, src in survivors)
            self.fail("%d NEW FALSE POSITIVE(s) on mokata's own source — every one of these "
                      "is a legitimate write that secret-guard would WALL, "
                      "non-overridably:\n%s" % (len(survivors), report))

    def test_the_named_residual_survivors_are_exactly_as_reported(self):
        """Pins the residual count so it can only move DELIBERATELY. If a later change fixes
        one, this fails and the report gets updated; if a change adds one, the test above
        fails instead."""
        findings = _entropy_findings_in_repo()
        named = [f for f in findings if self._is_named_survivor(f[3])]
        distinct = {run for _, _, _, runs, _ in named for run in runs
                    if run in self.KNOWN_SURVIVORS}
        self.assertEqual(distinct, set(self.KNOWN_SURVIVORS),
                         "the named residual set drifted — update the stage report")
        self.assertEqual(len(named), 0,
                         "residual (2) occurrence count changed on mokata's own source — it "
                         "went 4 -> 0 with the function-word list; a non-zero count means a "
                         "false positive came back")

    def test_the_corpus_is_actually_large_enough_to_mean_something(self):
        """A shrinking corpus would make the bar above pass for the wrong reason."""
        n_files = n_lines = 0
        for target in ("src", "tests"):
            for dirpath, dirnames, filenames in os.walk(os.path.join(REPO, target)):
                dirnames[:] = [d for d in dirnames if not _is_debris(d)]
                for name in filenames:
                    if name.endswith(".py"):
                        n_files += 1
                        try:
                            with open(os.path.join(dirpath, name), encoding="utf-8") as fh:
                                n_lines += len(fh.read().splitlines())
                        except (UnicodeDecodeError, OSError):
                            pass
        self.assertGreater(n_files, 300, "corpus shrank — the FP measurement lost its power")
        self.assertGreater(n_lines, 100000, "corpus shrank")

    def test_the_planted_fakes_really_are_still_caught(self):
        """The other direction: the bar above must not be passing because the scanner went
        quiet. The planted credentials must still be found in the corpus."""
        findings = _entropy_findings_in_repo()
        planted = [f for f in findings if self._is_planted(f[3])]
        self.assertGreaterEqual(len(planted), 15,
                                "the planted LEAK-CANARY keys stopped being detected")


if __name__ == "__main__":
    unittest.main()
