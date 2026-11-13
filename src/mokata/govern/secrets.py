"""I1 — 4-layer secret protection.

Catch secrets before they are written, committed, or sent. Four independent layers, so a
secret that slips one is likely caught by another:

  1. signature — known credential patterns (AWS keys, private keys, tokens, assignments)
  2. entropy   — long, high-entropy strings that look generated (keys/tokens)
  3. path      — writing to sensitive locations (.env, id_rsa, *.pem, credentials, …)
  4. egress    — any secret in content that is about to leave the machine is fatal
                 (pairs with netguard's outbound block)

Dependency-free and deterministic.
"""

from __future__ import annotations

import binascii
import copy
import math
import os
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable, List, NamedTuple, Optional, Tuple

from ..errors import MokataError

LAYERS = ("signature", "entropy", "path", "egress")

# Known-credential signatures (Stage 46). Each has a DISTINCTIVE, low-false-positive shape
# (a fixed prefix or a structural marker) so it hard-blocks regardless of entropy — the
# named formats never depend on the entropy backstop. Provider-key bodies are matched as
# CONTIGUOUS runs (no `-`) so a kebab identifier that merely starts with a prefix can't trip.
_SIGNATURES = [
    # AWS — access key id (long-term AKIA / temporary ASIA). The 40-char secret key has no
    # distinctive prefix; it's caught by the secret-assignment rule + the entropy backstop.
    ("aws-access-key", re.compile(r"(?:AKIA|ASIA)[0-9A-Z]{16}")),
    # Private keys are NOT a pattern here: `_scan_key_material` (the one key detector below)
    # reports them — firm `private-key` and tolerant `key-marker-tolerant` (REDACT-MANGLED-KEY).
    # GitHub: classic ghp_/gho_/ghs_/ghr_/ghu_ + fine-grained github_pat_.
    ("github-token", re.compile(r"gh[posru]_[A-Za-z0-9]{20,}")),
    ("github-pat", re.compile(r"github_pat_[0-9A-Za-z_]{20,}")),
    ("gitlab-token", re.compile(r"glpat-[0-9A-Za-z_\-]{20,}")),
    ("slack-token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    # GCP API key.
    ("gcp-api-key", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    # Azure storage account key (in a connection string).
    ("azure-storage-key", re.compile(r"(?i)AccountKey=[A-Za-z0-9+/]{40,}={0,2}")),
    # Stripe live/test keys (sk_/pk_/rk_), SendGrid, OpenAI-style sk- keys.
    ("stripe-key", re.compile(r"[rsp]k_(?:live|test)_[0-9A-Za-z]{10,}")),
    ("sendgrid-key", re.compile(r"SG\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}")),
    ("openai-key", re.compile(r"sk-(?:proj-)?[A-Za-z0-9]{20,}")),
    # JWT (header.payload.signature — both segments begin with the base64 of `{"`).
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    # Package-registry tokens.
    ("npm-token", re.compile(r"npm_[A-Za-z0-9]{36}")),
    ("pypi-token", re.compile(r"pypi-[A-Za-z0-9_\-]{16,}")),
    ("secret-assignment", re.compile(
        r"(?i)(?:api[_-]?key|secret|token|password|passwd|access[_-]?key|"
        r"client[_-]?secret|auth[_-]?token)"
        r"\s*[:=]\s*['\"][^'\"\s]{8,}['\"]")),
    # Connection string carrying inline credentials (e.g. a Postgres DSN). mokata only
    # ever references a DSN via an env var (config.dsn_env); a plaintext one in a
    # committed manifest is a leak this must block (Stage 24A).
    ("connection-string-credentials", re.compile(
        r"(?i)\b(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis|rediss|"
        r"amqps?)://[^/\s:@]+:[^/\s@]+@")),
]

_SENSITIVE_NAMES = (".env", "id_rsa", "id_dsa", "credentials", ".npmrc", ".pgpass",
                    ".netrc")
_SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".ppk")

_TOKEN_RE = re.compile(r"[A-Za-z0-9+/=_\-]{20,}")
# Path / URL / filename separators. A matched token is broken on these before the entropy
# check so a long file path or URL (e.g. "src/mokata/memory/precedence.py") is evaluated
# as its short word-like segments, not as one "high-entropy" blob (the segments don't trip it).
_SEP_RE = re.compile(r"[/\\.]+")
# Hex alphabet — a pure-hex run is a DIGEST (git SHA, md5, sha256, a UUID's hex), not a
# credential. Digests are not secrets, so the entropy backstop must not flag them (a 40-hex
# git SHA blocking legit content was a latent false positive). Credential *assignments* and
# the known token formats above still catch hex-valued secrets via the signature layer.
_HEX = frozenset("0123456789" + "abcdef" + "ABCDEF")
# Subresource-integrity / lockfile hashes (npm `sha512-…`, etc.) — benign base64 digests.
_INTEGRITY_RE = re.compile(r"^(?:sha1|sha256|sha384|sha512|md5)-", re.IGNORECASE)

# --- Identifier-aware word structure (SECRET-FP) -------------------------------------------
# Identifier separators. `/`, `\` and `.` are already split off upstream by `_SEP_RE`; `=`
# and `+` are here so base64 padding/joins are treated as boundaries too, not as word letters.
_IDENT_SEP_RE = re.compile(r"[-_=+]+")
# The conventional identifier/slug separators only — used by the lowercase-slug exemption so
# it stays exactly as narrow as the 0.0.14 rule it inherits (no `=`/`+` widening).
_SLUG_SEP_RE = re.compile(r"[-_]+")
# One casing run: an all-caps acronym, a Capitalized word, a lowercase word, or a digit run.
# The `(?![a-z])` on the acronym branch keeps `HTTPResponse` as `HTTP` + `Response`.
_CASE_RUN_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z]*|[a-z]+|[0-9]+")
# `y` is deliberately NOT a vowel here. It is a vowel in English, but counting it as one
# made random 4-char debris (`Zyym`, `Mylkc`) read as word-shaped and let a 40-char AWS
# secret key buy the exemption — measured, not assumed. Excluding it only costs a few real
# words (`myth`, `sync`), which lowers a token's word fraction rather than exempting a key.
_VOWELS = frozenset("aeiouAEIOU")
_UUID_GROUPS = (8, 4, 4, 4, 12)          # the canonical UUID hex grouping
# 4, not 3: random case-runs of exactly 3 letters containing a vowel are COMMON in generated
# keys (`Hqu`, `Umk`, `Rfi` all came out of the adversarial rows), so a 3-char floor hands
# real keys a majority of "words". Real 3-letter identifier words (`get`, `url`, `key`) are
# plentiful, but they only need to not DOMINATE — the thresholds below are fractions.
_MIN_WORD = 4
# ...EXCEPT for a CLOSED list of short English function words (`84:74` merged with `84:68`).
# THE MEASURED ROOT: with a flat 4-char floor, every English connective scores AGAINST a
# token, so an identifier looks MORE like a generated key the more it reads like a SENTENCE —
# and mokata's house style produces long descriptive test names on purpose. Three identifiers
# were measured blocking on the SEGMENT fraction alone while their CHAR fraction was healthy:
# a 57-char snake_case test METHOD name (char 0.533, seg 4/14) and two CamelCase test CLASS
# names (0.730, 4/9 and 0.591, 3/7). Each also blocked the write of the backlog row filing it.
#
# THIS IS A LIST, NOT A DICTIONARY — the distinction the rest of this module insists on, and
# the reason it is safe here where a dictionary would not be:
#   * CLOSED CLASS. Articles, prepositions, conjunctions, pronouns and auxiliaries are a
#     closed set of English: it does not grow. There is no slope from here to "add `cap`,
#     `tab`, `idx`", which is where an open dictionary and its false positives begin. (`cap`
#     was in the approved sketch and is deliberately NOT here for exactly that reason — it is
#     not closed-class, and the 84:68 row passes without it at seg 0.643.)
#   * NO SINGLE LETTERS. One-character debris is precisely what a generated key decomposes
#     into, so a one-letter "word" would hand every key a free segment.
#   * ESSENTIALLY 3-CHAR, and that is a MEASURED bound, not a style choice. A 2-letter entry
#     is cheap for a random key to hit by accident: there are only 676 two-letter pairs, so a
#     ~24-entry two-letter list collides with roughly 4% of random 2-char debris segments.
#     Swept over 4 x 30k generated keys (base64, base64url, `_`-chunked, camel-concatenated;
#     113,899 that blocked BEFORE this change), the full 2-letter set costs a 0.0325% false
#     NEGATIVE rate. Per-word measurement showed the cost is not uniform and the benefit is
#     concentrated in three words, so the list keeps only those:
#         `is` -> 0.0000% (carries `84:68` 0.500 -> 0.571 and `84:72` 0.571 -> 0.714)
#         `on` -> 0.0000% (carries `84:74` 0.556 -> 0.667)
#         `no` -> 0.0018% (the one row on this repo's own source that needs it)
#     Total 0.0018%, against 0.0325% for the unrestricted list — an 18x reduction for no lost
#     pin. Every other 2-letter word was dropped: each cost between 0.0009% and 0.0035% and
#     moved no measured identifier at all.
#   * IT WIDENS THE WORD DEFINITION ONLY. Both fractions and both 0.50 thresholds are
#     untouched — see `_has_word_structure` and the counterexample recorded there.
#
# THE RESIDUAL, NAMED. 0.0018% of unnamed generated-key shapes that previously blocked now
# pass. It is confined to this backstop: every NAMED credential format is caught by the
# SIGNATURE layer, which runs FIRST and is untouched here, so no vendor key becomes invisible.
# A key DELIBERATELY salted with English function words is a much larger hole (~20% measured)
# — but that is the pre-existing separator-mangled/TRANSFORMED-key class this module already
# names as a KNOWN LIMITATION below and `test_secret_fp.TestChunkedUnknownKeyBoundary` already
# asserts on purpose; the list widens it, it does not open it.
_FUNCTION_WORDS = frozenset({
    # prepositions / particles
    "for", "off", "out", "per", "via", "on",
    # conjunctions
    "and", "but", "nor", "yet",
    # articles / determiners / quantifiers
    "the", "all", "any", "one", "two",
    # pronouns / possessives
    "her", "him", "his", "its", "our", "own",
    # auxiliaries / copulas / negation
    "are", "was", "can", "did", "has", "had", "may", "not", "is", "no",
})
_MAX_SEGMENT = 20                        # a longer run is a blob; the token floor is 20 too
_MAX_CONSONANT_RUN = 4                   # `length`/`strength` are 4; 5+ is not word-shaped
# TWO independent majorities, because either one alone is gameable: a key can carry a long
# embedded English run (`…CYEXAMPLEKEY`) that wins on CHARACTERS while its segment list is
# obvious debris, and a key can carry many tiny word-ish runs that win on COUNT while barely
# any of the token is word material. Benign identifiers clear both comfortably (the tightest
# measured real row sits at 0.6); the adversarial keys fail at least one.
_WORDISH_CHAR_FRACTION = 0.5             # majority of the token's substance
_WORDISH_SEGMENT_FRACTION = 0.5          # majority of its ALPHA segments

# --- Known-secret SHAPE floor (SECRET-FP addendum) ------------------------------------------
# Runs BEFORE any exemption: a candidate matching one of these blocks no matter how
# word-structured it looks. It exists because a key CHUNKED with `-`/`_` (`AKIA_IOSFODNN7_
# EXAMPLE`) decomposes into perfectly word-shaped segments and would otherwise be exempted —
# and nothing STRUCTURAL separates it from `MOKATA_SESSION_ID_OVERRIDE_2`.
#
# Every entry is an ANCHORED, FULL shape — a vendor prefix AND an exact (or floored) body
# length, matched with `fullmatch` against the whole candidate. A prefix alone is never a
# match. That is what holds the false-positive risk at ~zero, and it is why no dictionary,
# casing rule, or word-likeness test appears here: those are where false positives came from.
#
# Each candidate is tested RAW and SEPARATOR-STRIPPED, so chunking cannot evade a shape whose
# prefix carries no separator (AWS, Google). For shapes whose own prefix contains a separator
# (`ghp_`, `xoxb-`, `sk-`) stripping would destroy the prefix, so only the RAW form matches —
# their canonical tokens are still caught here and by the signature layer. Deliberately NOT
# fixed by making those separators optional: `sk-?[A-Za-z0-9]{20,}` would fullmatch the
# ordinary identifier `skipUserValidationForTests`, which is exactly the FP class to avoid.
#
# Provenance (vendor-documented formats, same sources as the signature layer above):
#   AWS access key id      — 'AKIA'/'ASIA' + 16 upper-alnum (20 total)
#   GitHub token           — gh[pousr]_ + 36 alnum
#   GitHub fine-grained    — github_pat_ + 22 alnum + '_' + 59 alnum
#   OpenAI                 — 'sk-' (optionally 'proj-') + 20+ alnum
#   Slack                  — xox[baprs]- + 10+
#   Google API key         — 'AIza' + 35 of [A-Za-z0-9_-]
#   JWT                    — dotted eyJ…​.eyJ…​.sig triplet (primary catch is the signature
#                            layer: `_TOKEN_RE` excludes '.', so a JWT rarely arrives here
#                            as one candidate — kept for completeness of the set)
_KNOWN_SHAPES = (
    ("aws-access-key", re.compile(r"(?:AKIA|ASIA)[A-Z0-9]{16}")),
    ("github-token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36}")),
    ("github-pat", re.compile(r"github_pat_[A-Za-z0-9]{22}_[A-Za-z0-9]{59}")),
    ("openai-key", re.compile(r"sk-(?:proj-)?[A-Za-z0-9]{20,}")),
    ("slack-token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("gcp-api-key", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
)


# --- Binding awareness (SECRET-VALUE-SCAN) -------------------------------------------------
# The entropy layer judges the VALUE side of a binding, never the NAME side. Root (Jas
# 2026-07-27): `_scan_entropy` was position-BLIND — it tested every separator-split run on a
# line with no notion of which side of an `=` the run sat on, so an identifier was scanned as
# a candidate credential. In its purest measured form the name did not merely fail a
# heuristic, it MANUFACTURED the candidate: `_TOKEN_RE`'s alphabet includes `=`, so a 10-char
# env-var name bound to a 9-char value is ONE 20-char token — the value alone is far below the
# 20-char floor and would never have been a candidate at all.
#
# The SIGNATURE layer has always been binding-aware (`secret-assignment` matches a bound
# value); this brings the entropy backstop up to it. It is not a new concept, and not an
# allowlist: the only thing ever exempted is the identifier run immediately left of a
# binding operator.
#
# DELIBERATELY LINE-BASED, NOT AN AST. `scan()` does not receive Python source. Measured from
# the real callers (hook_cli secret-guard for tool content AND Bash command lines, govern/gate,
# govern/outbound, config_cmd, share, memory/share, team_journal, stacks, perf,
# knowledge/graph_adopt), it receives: source in ANY language, shell command lines, .env files,
# YAML, TOML, JSON both pretty-printed AND `json.dumps`-ed to a SINGLE line, markdown prose,
# unified diffs, and untrusted community manifests. No parser for one language applies.
#
# So a line-based parser CAN decide single-line bindings (`X = v`, `X: v`, `"k": "v"`,
# `--flag=v`, `KEY=v`, YAML/TOML/JSON pairs) — including SEVERAL on one line, which is why
# every operator on the line is handled rather than just the first (a `json.dumps` one-liner
# from team_journal/session_bundle is the common real shape). It CANNOT decide a value that
# spans lines (a triple-quoted string, a YAML block scalar, a heredoc body). Those
# continuation lines carry no binding and therefore keep the bare-token rule unchanged — every
# ambiguity errs toward SCANNING, which is what makes this sound rather than merely convenient.
_NAME_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
                        "0123456789+/_-")
_ENV_NAME_RE = re.compile(r"[A-Z][A-Z0-9]*(?:_[A-Z][A-Z0-9]*){3,}")
_DECL_NAME_RE = re.compile(r"\b(?:def|class|func|function|fn)\s+([A-Za-z_][A-Za-z0-9_]*)")
# Characters that turn a following `=` into a COMPOUND operator (`==`, `!=`, `<=`, `>=`, `+=`,
# `:=`, …). A comparison is not a binding, so its left operand is not a name.
_OP_LEAD = "=!<>+-*/%&|^:"


def _binding_name_spans(line: str) -> List[tuple]:
    """Character spans on `line` that are the NAME side of a binding.

    A name is exactly the token-alphabet run immediately left of a binding operator (past any
    whitespace and one optional closing quote, so `"key": v` and `--flag=v` both resolve). That
    is the most conservative reading available: nothing else on the line is ever exempted.

    `=` needs two guards that `:` does not, because `=` is INSIDE `_TOKEN_RE`'s alphabet and so
    can occur within a candidate rather than between two of them:
      - a compound operator (`==`, `!=`, `+=`, …) is a comparison, not a binding;
      - trailing base64 PADDING is not an operator. Padding is never glued to a value, so the
        glued form (`KEY=value`) requires a non-space immediately after, and the spaced form
        (`KEY = value`) requires a value somewhere after. Without this a padded key would be
        read as its own NAME and exempt itself — the one way this change could have become an
        escape hatch."""
    spans: List[tuple] = []
    n = len(line)
    for i, ch in enumerate(line):
        if ch not in ":=":
            continue
        if ch == "=":
            if i and line[i - 1] in _OP_LEAD:               # ==, !=, <=, +=, :=, …
                continue
            if i + 1 < n and line[i + 1] == "=":            # the first `=` of `==`
                continue
            if i and line[i - 1] not in " \t":              # GLUED form: NAME=value
                if not (i + 1 < n and line[i + 1] not in " \t"):
                    continue                                # nothing glued after -> padding
            elif not line[i + 1:].strip():                  # SPACED form still needs a value
                continue
        else:                                               # ":"
            if i and line[i - 1] == ":":                    # `::` scope operator
                continue
            if i + 1 < n and line[i + 1] == ":":
                continue
            if not line[i + 1:].strip():                    # dangling `:` — no value, no bind
                continue
        end = i
        while end > 0 and line[end - 1] in " \t":
            end -= 1
        if end > 1 and line[end - 1] == "]" and line[end - 2] in "'\"":
            end -= 2                                        # `d["key"] = v` — a subscript key
        elif end > 0 and line[end - 1] in "'\"":            # closing quote of a quoted key
            end -= 1
        start = end
        while start > 0 and line[start - 1] in _NAME_CHARS:
            start -= 1
        if start < end:
            spans.append((start, end))
    # A DECLARED name is a name too (0.0.26 O14b): `class R18_APathologicalTsFileIsNeverARaw500(`
    # and Go's `func schema_k8sio_api_core_v1_PodSpec(` were judged as candidate credentials.
    # Only the identifier immediately after a declaration keyword is masked; the known-shape floor
    # still runs on the RAW line, so a credential shape is caught wherever it sits.
    for m in _DECL_NAME_RE.finditer(line):
        spans.append(m.span(1))
    return spans


def _mask_binding_names(line: str) -> str:
    """`line` with every binding NAME blanked out, so tokenizing it yields only value-side and
    bare runs. Masking (rather than skipping whole tokens) is required because `_TOKEN_RE`
    matches ACROSS `=`: `NAME=value` is a single token, and only the value half is a candidate.
    Blanks preserve column positions, so reported line content stays faithful."""
    spans = _binding_name_spans(line)
    if not spans:
        return line
    chars = list(line)
    for start, end in spans:
        for k in range(start, end):
            chars[k] = " "
    return "".join(chars)


def _candidate_runs(line: str):
    """The separator-split runs on a line that are eligible to be a credential at all: not an
    SRI/lockfile hash, at least 20 chars, and char-class mixed. Unchanged from 0.0.15 — the
    two passes in `_scan_entropy` differ only in WHICH line they are handed."""
    for tok in _TOKEN_RE.findall(line):
        if _INTEGRITY_RE.match(tok):                # npm/SRI lockfile hash (sha512-…)
            continue
        for sub in _SEP_RE.split(tok):              # break paths / URLs / filenames
            if len(sub) < 20:
                continue
            if not (any(c.isdigit() for c in sub) and any(c.isalpha() for c in sub)):
                continue                            # char-class mix (conjunction term 2)
            yield sub


def _is_pure_hex(s: str) -> bool:
    return all(c in _HEX for c in s)


def _is_capitalized_word(run: str) -> bool:
    """`Xxx…` — an initial capital followed by lowercase. The marker of a new word in the
    camelCase/PascalCase conventions, and the thing an ALL-CAPS run is not."""
    return len(run) >= 2 and run[0].isupper() and run[1:].islower()


@dataclass
class Finding:
    layer: str
    kind: str
    detail: str
    line: int = 0
    # SECRET-IGNORE — the flagged candidate itself, carried so the block message can print the
    # exact `mokata secret ignore` invocation for THIS finding. IN-MEMORY ONLY: it is never
    # written to the ignore store (which keeps a sha256), never to the audit ledger, and never
    # rendered by anything but the shared block builder. Pinned by
    # `test_secret_ignore.TestTheLiteralIsNeverWritten`. Set on the entropy backstop's own
    # findings only — the layers that carry no negotiable candidate leave it empty.
    token: str = ""


# --- REDACT-MANGLED-KEY: THE ONE private-key detector ------------------------------------------
# Every surface that judges or replaces private-key text calls this block: the signature layer
# below (`_scan_key_material`), `ide.usages` (`pem_lines`, `pem_state_before`, `_residual`,
# `redact_text`) and so every line site and every whole-text site. There is no second marker
# pattern in `src/` (`test_e1_marker_detector.OneDetector` holds that), and no regex here carries
# an unbounded quantifier next to another one: a marker is found by a hand scanner that does
# O(1) bounded work per BEGIN/END keyword, so a line is examined in time linear in its length.
#
# THE RULE (0.0.28 REDACT-MANGLED-KEY; rulings E-Q3, E-Q5):
#   * keyword BEGIN or END; look back over <=16 blanks and count <=64 dashes. Three or more
#     dashes ("dashed") allow any case; fewer need every word UPPER-case (E-Q3).
#   * then <=6 type words, PRIVATE, KEY (required), optional BLOCK, optional trailing dashes.
#     A public word among THIS marker's type words cancels it.
#   * CUT (dashed BEGIN only): the line ends after BEGIN, after key-type words (or a prefix of
#     one), inside PRIVATE, or inside KEY.
#   * the view is NORMALISED first (non-ASCII lines only): Cf, Mn and soft hyphen dropped, every
#     dash to `-`, every Zs to a space, a char whose NFKC is one ASCII char to that char.
#   * a split string literal is matched on the GLUED view (junctions deleted) and across the
#     previous line (JOINED view); a base64 token `LS0t…` is decoded and judged as a marker.
#   * FIRM (signature `private-key`, never ignorable): direct view, dashed, every type word a key
#     type, PRIVATE KEY complete and followed by >=3 dashes or the end of the line; or the base64
#     of a PRIVATE marker. Everything else that matches is TOLERANT (`key-marker-tolerant`):
#     blocked at whole-text sites but recordable through `secret_ignore`; fatal on egress.
#   * an unmarked body (a base64 RUN whose decoded head is a private key's DER, or an OpenSSH
#     key) is tolerant too: `UnmarkedRun`.
KEY_FIRM = "private-key"
KEY_TOLERANT = "key-marker-tolerant"
_KEY_TYPES = ("RSA", "EC", "DSA", "OPENSSH", "ENCRYPTED", "PGP", "SSH2")
_KEY_TYPE_SET = frozenset(_KEY_TYPES)
_PUBLIC_WORDS = frozenset(("PUBLIC", "CERTIFICATE", "REQUEST", "CMS", "PKCS7", "X509",
                           "TRUSTED", "PARAMETERS"))
_MAX_DASHES = 64
_MAX_BLANKS = 16
_MAX_WORDS = 6
_MAX_WORD = 16
_CUT_LOOK = 64                 # how far past a cut the rest of the line is looked at
_JOIN_TAIL = 256               # chars of the previous line a split literal is joined from
_JOIN_HEAD = 1024              # chars of this line the joined view takes
_B64_MARKER = 128              # base64 chars of an `LS0t…` token decoded
RUN_CHARS = 128                # base64 chars of a run decoded by the unmarked-body backstop
B64_LINE_MAX = 4096            # a longer line is never a base64 body line (no copy is made)
B64_TAIL_MIN = 24              # a base64 run behind a code prefix is at least this long
KEY_WINDOW = 1 << 20           # a longer line is examined in windows …
KEY_OVERLAP = 4096             # … overlapping by this many characters
_BLANK = " \t"
#: the 11 `str.splitlines` breaks — the cut rule treats any of them as the end of the line
LINE_SEPARATORS = "\n\r\v\f\x1c\x1d\x1e\x85  "
_PEM_HEADERS = ("PROC-TYPE: 4,ENCRYPTED", "DEK-INFO:")
_BODY_HEADERS = ("PROC-TYPE:", "DEK-INFO:", "COMMENT:", "VERSION:", "HASH:")

#: candidate keywords — no quantifier, so one search is linear
_KW = re.compile(r"(?i:begin|end)")
#: a line is examined at all only if its normalised view holds one of these (bounded quantifier)
_PRE = re.compile(r"(?i:priv)|---[ \t]{0,16}(?i:b)|LS0t|(?i:proc-type|dek-info)")
#: what may follow a cut and still be "the end of the line": blanks, quotes, glue, `\n` escapes
_CUT_REST = re.compile(r"(?:[ \t\"'`,;+)]|\\[nr]?)*")
#: a string-literal junction: quote, glue operator, quote (`"a" + "b"`, `'a' . 'b'`, `"a" "b"`)
_JUNCTION = re.compile(r"[\"'`][ \t]{0,16}(?:\+|\.\.|\.|,|&|\|\||\\|_)?[ \t]{0,16}[\"'`]")
_LS0T = re.compile(r"LS0t[A-Za-z0-9+/]{0,124}")
_B64_BODY = re.compile(r"[A-Za-z0-9+/]{4,}={0,2}|[A-Za-z0-9+/]{2,3}={1,2}")
_B64_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
_CONT_END = "+\\,(.&|\"'`"
_CONT_START = "+.&|\"'`"
_PKCS5 = bytes.fromhex("2a864886f70d0105")
_PKCS12_PBE = bytes.fromhex("2a864886f70d010c01")
_OPENSSH_MAGIC = b"openssh-key-v1\x00"


class _NormTable(dict):
    """`str.translate` table for the marker view, filled on first sight of each code point."""

    def __missing__(self, code: int) -> Any:
        ch = chr(code)
        cat = unicodedata.category(ch)
        if cat in ("Cf", "Mn") or ch == "\xad":
            val: Any = None
        elif cat == "Pd" or code in (0x2212, 0xFE58, 0xFE63, 0xFF0D, 0x2043):
            val = "-"
        elif cat == "Zs":
            val = " "
        else:
            n = unicodedata.normalize("NFKC", ch)
            val = n if (len(n) == 1 and n.isascii() and n != ch) else code
        self[code] = val
        return val


_NORM = _NormTable()


def normalise_for_markers(text: str) -> str:
    """The view markers are matched on. ASCII text is its own view (no copy); otherwise each
    character maps to at most one, so a view index maps back to a RAW index (`_raw_index`)."""
    return text if text.isascii() else text.translate(_NORM)


def _raw_index(raw: str, v: int) -> int:
    """The smallest raw index whose normalised prefix is `v` long (binary search, C-speed)."""
    if raw.isascii():
        return v
    lo, hi = 0, len(raw)
    while lo < hi:
        mid = (lo + hi) // 2
        if len(raw[:mid].translate(_NORM)) < v:
            lo = mid + 1
        else:
            hi = mid
    return lo


class Marker(NamedTuple):
    """One key marker on a line. `start`/`end` are RAW indices into the line. `begin`: True a
    BEGIN, False an END, None a self-contained blob or a PEM header (it opens a run, no END).
    `clear`: nothing but glue follows it on its line. `whole`: found on a glued/joined view, so
    it stands for the whole line."""
    start: int
    end: int
    begin: Optional[bool]
    firm: bool
    clear: bool = False
    whole: bool = False


def _rest_clear(v: str, pos: int, at_eol: bool) -> bool:
    """Is everything after `pos` up to the end of the line glue only (bounded look)?"""
    lim = min(len(v), pos + _CUT_LOOK + 1)
    p = _CUT_REST.match(v, pos, lim).end()
    if p < len(v):
        return v[p] in LINE_SEPARATORS
    return at_eol


#: the words after a keyword: up to 9 (6 type words, PRIVATE, KEY, BLOCK), each 1..16 letters or
#: digits after 1..16 blanks — every quantifier bounded, so one match is O(1)
_SEQ = re.compile(r"(?:[ \t]{1,16}[A-Za-z0-9]{1,16}(?![A-Za-z0-9])){0,9}")
_TRAIL = re.compile(r"[ \t]{0,16}(-{1,64})")


def _parse(v: str, s: int, e: int, at_eol: bool) -> Optional[Marker]:
    """The marker whose keyword is `v[s:e]`, or None. O(1) bounded work: the look-back reads at
    most 16 blanks and 64 dashes, the look-forward one bounded match."""
    kw = v[s:e]
    begin = kw.upper() == "BEGIN"
    back = v[max(0, s - _MAX_BLANKS - _MAX_DASHES - 1):s]
    t = back.rstrip(" \t")
    blanks = len(back) - len(t)
    dashes = 0 if blanks > _MAX_BLANKS else min(len(t) - len(t.rstrip("-")), _MAX_DASHES)
    dashed = dashes >= 3
    if not dashed:
        if kw != kw.upper():
            return None                              # E-Q3: a dash-less marker is upper-case
        if s > 0 and v[s - 1].isascii() and v[s - 1].isalnum():
            return None                              # SEND, APPEND, XBEGIN
    start = s - blanks - dashes if dashes else s
    seq = _SEQ.match(v, e).group()
    raw = seq.split()
    ups = seq.upper().split()

    def end_of(j: int) -> int:                       # the index just past word `j`
        at = 0
        for w in raw[:j + 1]:
            at = seq.index(w, at) + len(w)
        return e + at
    k = ups.index("PRIVATE") if "PRIVATE" in ups else -1
    if not dashed:                                   # E-Q3: the marker's own words upper-case
        own = raw[:k + 3] if k >= 0 else raw
        if k >= 0 and len(own) == k + 3 and own[-1] != "BLOCK":
            own = own[:-1]
        if any(w != w.upper() for w in own):
            return None
    words = ups[:k] if k >= 0 else ups
    if len(words) > _MAX_WORDS or not _PUBLIC_WORDS.isdisjoint(words):
        return None
    if k < 0:                                        # no PRIVATE: only a CUT can be a marker
        pos = end_of(len(raw) - 1) if raw else e
        if not (dashed and begin) or not _rest_clear(v, pos, at_eol):
            return None
        if words:
            last = words[-1]
            if not _KEY_TYPE_SET.issuperset(words[:-1]):
                return None
            if not ("PRIVATE".startswith(last) or any(t.startswith(last) for t in _KEY_TYPES)):
                return None
        return Marker(start, pos, True, False, True)
    nxt = ups[k + 1] if k + 1 < len(ups) else ""
    if nxt == "KEY":
        if k + 2 < len(ups) and ups[k + 2] == "BLOCK" and (dashed or raw[k + 2] == "BLOCK"):
            pos = end_of(k + 2)
        else:
            pos = end_of(k + 1)
        tm = _TRAIL.match(v, pos)
        d = len(tm.group(1)) if tm else 0
        end = tm.end() if tm else pos
        clear = _rest_clear(v, end, at_eol)
        firm = dashed and _KEY_TYPE_SET.issuperset(words) and (d >= 3 or clear)
        return Marker(start, end, begin, firm, clear)
    if dashed and begin:                             # CUT inside PRIVATE K…
        if nxt and "KEY".startswith(nxt) and _rest_clear(v, end_of(k + 1), at_eol):
            return Marker(start, end_of(k + 1), True, False, True)
        if not nxt and _rest_clear(v, end_of(k), at_eol):
            return Marker(start, end_of(k), True, False, True)
    return None


def _b64_marker(v: str, p: int) -> Optional[Marker]:
    """An `LS0t…` token at `p` that decodes to a BEGIN … PRIVATE marker: FIRM, self-contained."""
    m = _LS0T.match(v, p)
    tok = m.group(0)
    tok = tok[:len(tok) // 4 * 4]
    try:
        dec = binascii.a2b_base64(tok).decode("latin-1")
    except (binascii.Error, ValueError):
        return None
    dec = dec.split("\n", 1)[0]
    if "PRIVATE" not in dec.upper():
        return None
    for k in _KW.finditer(dec):
        mk = _parse(dec, k.start(), k.end(), False)
        if mk is not None and mk.begin:
            return Marker(p, m.end(), None, True, False)
    return None


def _view_markers(v: str, at_eol: bool) -> List[Marker]:
    """Markers on one normalised view, in order, VIEW coordinates."""
    if _PRE.search(v) is None:
        return []
    out: List[Marker] = []
    for k in _KW.finditer(v):
        mk = _parse(v, k.start(), k.end(), at_eol)
        if mk is not None:
            out.append(mk)
    p = v.find("LS0t")
    while p >= 0:
        mk = _b64_marker(v, p)
        if mk is not None:
            out.append(mk)
        p = v.find("LS0t", p + 4)
    head = v[:96].lstrip(" \t\"'`#;/*").upper()
    if head.startswith(_PEM_HEADERS):
        out.append(Marker(0, len(v), None, False, True))
    if len(out) > 1:
        out.sort(key=lambda m: m.start)
    return out


def _glue(v: str) -> str:
    v = _JUNCTION.sub("", v)
    if "concat!(" in v:
        v = v.replace("concat!(", "")
    if "\\" in v:
        v = v.replace("\\n", "").replace("\\r", "")
    return v


_OWN: Tuple[Any, List[Marker]] = (None, [])


def _own_markers(gp: str) -> List[Marker]:
    """The previous line's own (glued) markers — the walker asks for the same line twice in a
    row (as `prev` of this line), so one entry is cached (rebound whole: a reader on another
    thread sees an old pair or a new pair, never a mix). A miss only recomputes."""
    global _OWN
    key, val = _OWN
    if key != gp:
        val = _view_markers(gp, True)
        _OWN = (gp, val)
    return val


def _window_markers(raw: str, offset: int, prev: str, at_eol: bool) -> List[Marker]:
    v = normalise_for_markers(raw)
    direct = _view_markers(v, at_eol)
    if v is not raw:
        direct = [Marker(_raw_index(raw, m.start), _raw_index(raw, m.end), m.begin, m.firm,
                         m.clear) for m in direct]
    best = direct
    if "\"" in v or "'" in v or "`" in v or "concat!(" in v or "\\" in v:
        glued = _view_markers(_glue(v), at_eol)
        if len(glued) > len(best):
            best = [Marker(0, len(raw), m.begin, False, m.clear, True) for m in glued]
    if prev and offset == 0:
        pv = normalise_for_markers(prev[-_JOIN_TAIL:]).rstrip()
        cv = v[:_JOIN_HEAD].lstrip()
        if pv and cv and (pv[-1] in _CONT_END or cv[0] in _CONT_START):
            if pv.endswith("\\"):
                pv = pv[:-1]                         # a `\` line continuation joins the two
            gp = _glue(pv)
            joined = _view_markers(_glue(pv + cv), at_eol or len(v) <= _JOIN_HEAD)
            if joined:
                own = _own_markers(gp)
                new = [m for m in joined if m.start < len(gp) and not any(
                    o.start == m.start and o.end >= m.end for o in own)]
                if new:
                    lead = [Marker(0, len(raw), m.begin, False, m.clear, True) for m in new]
                    best = lead + [m for m in best]
    if best is not direct:
        firm = [m for m in direct if m.firm]
        best = sorted(best + firm, key=lambda m: (m.start, not m.firm)) if firm else best
    return [Marker(m.start + offset, m.end + offset, m.begin, m.firm, m.clear, m.whole)
            for m in best]


def _joins(prev: str, line: str) -> bool:
    """Would the joined view look at this line (its trigger, on the raw text)?"""
    if not prev:
        return False
    p = prev[-1]
    if p == " " or p == "\t":
        p = prev.rstrip()[-1:]
        if not p:
            return False
    c = line[:1]
    if c == " " or c == "\t":
        c = line[:_JOIN_HEAD].lstrip()[:1]
    if not c:
        return False
    return p in _CONT_END or c in _CONT_START or not p.isascii() or not c.isascii()


def quiet(line: str, prev: str = "") -> bool:
    """True when `line` certainly holds no marker in ANY view. An ASCII line without `---` has
    no dashed marker; without `PRIV` no dash-less one (E-Q3: upper-case); without a quote or a
    backslash nothing for the glued view; without `LS0t` no base64 marker; and the PEM headers
    are spelled canonically. Substring probes only (C-speed): the hot path for a key's body and
    for ordinary code."""
    return (len(line) <= KEY_WINDOW and line.isascii() and "---" not in line
            and "PRIV" not in line and '"' not in line and "'" not in line and "`" not in line
            and "\\" not in line and "LS0t" not in line and "Proc-Type" not in line
            and "DEK-Info" not in line and "PROC-TYPE" not in line and "DEK-INFO" not in line
            and not _joins(prev, line))


def quiet_inside(line: str) -> bool:
    """Inside a marked key only an END matters, and an END needs `---` (dashed), `PRIV`
    (dash-less, upper-case), a quote or backslash (glued view) or a non-ASCII character
    (normalised view). The joined view needs a continuation character at a line edge — never a
    letter or a dash — so it cannot assemble `---`/`PRIV` onto a line holding no quote: the worst
    it can do is miss an END split across an unquoted continuation, which keeps the walker inside
    (over-redaction, never a leak). True when `line` cannot end the key. C-speed probes only."""
    return (line.isascii() and "---" not in line and "PRIV" not in line and '"' not in line
            and "'" not in line and "`" not in line and "\\" not in line)


def key_markers(line: str, prev: str = "") -> List[Marker]:
    """Every key marker on `line` (one line, no separator at its end), in order, RAW
    coordinates. `prev`: the line above, for a literal split across the two. A line longer than
    `KEY_WINDOW` is examined in overlapping windows; the normalised and glued copies are made
    per window, never for the whole line."""
    if len(line) <= KEY_WINDOW:
        return _window_markers(line, 0, prev, True)
    out: List[Marker] = []
    w = 0
    while w < len(line):
        last = w + KEY_WINDOW >= len(line)
        chunk = line[w:w + KEY_WINDOW + KEY_OVERLAP]
        for m in _window_markers(chunk, w, prev if w == 0 else "", last):
            if last or m.start - w < KEY_WINDOW:
                out.append(m)
        w += KEY_WINDOW
    return out


#: a backslash escape inside a literal (`\n`, `\r`, `\t`): a SEPARATOR, never base64 (the `n` of
#: `\n` is in the base64 alphabet, so an escape left in place would glue onto the span)
_ESCAPE = re.compile(r"\\[nrt]")
#: one maximal base64 span (with its padding)
_SPAN = re.compile(r"[A-Za-z0-9+/]+={0,2}")
#: the first span after the opening glue (any leading non-base64 characters)
_FIRST_SPAN = re.compile(r"[^A-Za-z0-9+/]*([A-Za-z0-9+/]+={0,2})")
#: a line holds a span long enough to be a key's head at all (one C-speed search)
_LONG_SPAN = re.compile(r"[A-Za-z0-9+/]{%d}" % B64_TAIL_MIN)


def b64_body(line: str) -> Optional[str]:
    """The base64 text of a body line, or None. THE RULE (round 4, a predicate, not a list): the
    line holds exactly ONE base64 span and nothing else but glue — any character outside the
    base64 alphabet. A line wrapped in more code than that (an escape, a `+`, a literal
    junction, a comment) is `b64_tail`'s on a run's first line and `run_line`'s after it."""
    if len(line) > B64_LINE_MAX:
        return None
    spans = _SPAN.findall(line, 0, B64_LINE_MAX)
    if len(spans) != 1 or _B64_BODY.fullmatch(spans[0]) is None:
        return None
    return spans[0]


def _wordish(span: str) -> bool:
    """A word, not base64: one case only, or letters only."""
    core = span.rstrip("=")
    return core.lower() == core or core.upper() == core or core.isalpha()


def _opens_run(b64: str) -> bool:
    """Does this base64 open an unmarked-run candidate (`UnmarkedRun.feed`'s own rule)?"""
    return b64[:1] == "M" or b64.startswith("b3Bl")


def _head_span(t: str) -> Optional[str]:
    """The longest span of `t` that can be a key's first body line (`B64_TAIL_MIN`+ characters,
    not a word); None when there is none."""
    if _LONG_SPAN.search(t) is None:
        return None
    heads = [m.group(0) for m in _SPAN.finditer(t)
             if len(m.group(0)) >= B64_TAIL_MIN and not _wordish(m.group(0))]
    if not heads:
        return None
    return max(heads, key=len)


def b64_tail(line: str) -> Optional[str]:
    """A key's FIRST body line written into code, whatever wraps it — `k = (\"\"\"MII…`,
    `let k = r#"MII…"#;`, `{"k":"MII…\\n"}`, `| a | MII… |`, `MII…", // c` — or None. THE RULE
    (round 4): the head is the longest base64 span on the line (escapes are separators) that is
    `B64_TAIL_MIN`+ characters and not a word; what sits around it is code, whatever it is — no
    list of prefixes or closers. Only for a line that is not a body line itself (`b64_body`).

    A body line split across string literals on this line (`k = "MII…" "…"`) is also read on the
    JUNCTION-GLUED view; the glued head is taken when it opens an unmarked-run candidate
    (`M…` / `b3Bl…`) and the raw one does not, or when both do and the glued one is a private-key
    head. Linear: one C-speed search rejects almost every line; the rest take one span pass."""
    if len(line) > B64_LINE_MAX:
        return None
    t = _ESCAPE.sub(" ", line) if "\\" in line else line
    raw = _head_span(t)
    if ('"' in t or "'" in t or "`" in t) and _JUNCTION.search(t):
        glued = _head_span(_JUNCTION.sub("", t))
        if glued is not None and glued != raw and _opens_run(glued) and (
                raw is None or not _opens_run(raw) or der_private_key(glued[:RUN_CHARS])):
            return glued
    return raw


def run_line(line: str, width: int, total: int,
             orig: Optional[str] = None) -> Tuple[Optional[str], bool]:
    """THE CONTINUATION RULE (round 4): a line inside an ESTABLISHED unmarked run (its first
    line `width` base64 characters wide, `total` characters so far). The line's body is its
    first maximal base64 span after the opening glue — escapes are separators, string-literal
    junctions are glued — and whatever follows it is code, whatever it is (no list of closers).
    Returns `(span, last)`:
      - a span at least `width` wide continues the run (cut to `width`: a `/` or `+` glued
        after a body line is code, `…/>`);
      - the run's SECOND line may be shorter and still continue it — it settles the width (the
        first may carry a glued second, `k = "…" "…"`) — unless it is a code word;
      - otherwise a SHORTER span is the key's LAST line, and the run ends after it, when it is
        not a code word (`_code_word`) nor only `/`/`+`, and it is padded, or the whole line, or
        8+ characters, or starts alphanumeric and is 4+ characters or makes the run's base64 a
        multiple of 4;
      - anything else is `(None, False)`: the line is not the key's, and ends the run.
    A code word (`return`, `pass`, `fooBar`, `EOF`) is never absorbed, nor a span of only `/`
    and `+` (`// c`, `*/`), nor a short one starting `/` inside code (`</pre>`): what follows a
    headerless key keeps its own line."""
    if len(line) > B64_LINE_MAX or width <= 0:
        return None, False
    t = _ESCAPE.sub(" ", line) if "\\" in line else line
    if ('"' in t or "'" in t or "`" in t) and _JUNCTION.search(t):
        t = _JUNCTION.sub("", t)
    m = _FIRST_SPAN.match(t)
    if m is None:
        return None, False
    span = m.group(1)
    if len(span) >= width:                           # a body line is exactly `width` wide: a
        return span[:width], False                   # `/` or `+` glued after it is code (`/>`)
    if total == width and len(span) >= 4 and not _code_word(span):
        return span, False                           # the run's 2nd line: it SETTLES the width
    if _code_word(span) or not span.strip("+/="):  # (the 1st may carry a glued 2nd: `"…" "…"`)
        return None, False
    if span.endswith("=") or (line if orig is None else orig).strip() == span:
        return span, True                            # padded, or the whole line: base64's own
    if len(span) >= 8:
        return span, True
    if span[0].isalnum() and (len(span) >= 4 or (total + len(span)) % 4 == 0 or (
            len(span) >= 2 and not span.isdigit())):
        return span, True                            # a `/` or `+` glued after every line
    return None, False                               # (`…/>`) shifts the count, not the line


#: the base64 alphabet (a `str.strip` / membership set)
_B64_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
#: what the block rule keeps of a line's ends (bounded: linear per line)
BLOCK_GLUE_MAX = 64


def _common_prefix(a: str, b: str) -> str:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return a[:i]


def block_lead(prefix: str) -> str:
    """THE BLOCK RULE (round 5): of the longest common PREFIX of a run's block of lines (2 or
    more), the glue is the part up to its last character OUTSIDE the base64 alphabet — key text
    never crosses such a character — plus the `/` and `+` glued right after it (`//`, `=/`,
    `; +`). A letter or digit is never glue. A wrongly stripped key `/` costs nothing: the line's
    own reading keeps it, and a reading that continues the run beats one that ends it
    (`KeyWalk._cont`)."""
    cut = max((i for i, c in enumerate(prefix) if c not in _B64_CHARS), default=-1)
    if prefix[cut + 1:].strip("+/"):
        return prefix[:cut + 1]
    return prefix                                    # `//`, `=/`, `; +`: `/`/`+` glued on too


def _skip_lead(line: str, width: int) -> Optional[str]:
    """A continuation line's OWN lead, before the block has formed (its first line carried an
    extra lead, `key = "…`): characters outside the alphabet, spans of only `/`/`+`, and code
    words (`REM`, `dnl`), skipped when a span at least `width` wide follows them."""
    for m in _SPAN.finditer(line):
        span = m.group(0)
        if len(span) >= width:
            return line[m.start():]
        if span.strip("+/=") and not _code_word(span):
            return None
    return None


#: a code word: lower case (`return`, `pass`), camel or Pascal case (`fooBar`, `None`), or a
#: short upper-case one (`EOF`, `NULL`, `TRUE`); a longer upper-case span is base64
_CODE_WORD = re.compile(r"(?:[A-Z]?[a-z]+(?:[A-Z][a-z]+)*|[A-Z]{1,6})[0-9]*")


def _code_word(span: str) -> bool:
    """Could this short span be a code token rather than a key's last line?"""
    return _CODE_WORD.fullmatch(span) is not None


def _der_len(d: bytes, p: int) -> int:
    """Index after the DER length at `p`; -1 if it is not a plausible length."""
    b = d[p]
    if b < 0x80:
        return p + 1
    if 0x81 <= b <= 0x84:
        return p + 1 + (b - 0x80)
    return -1


def der_private_key(b64: str) -> bool:
    """Does this base64 head decode to a PRIVATE key by STRUCTURE? PKCS#1 RSA, DSA, SEC1 EC,
    PKCS#8 v1/v2 (RSA, EC, Ed25519 …): SEQUENCE, INTEGER 0|1, then INTEGER | SEQUENCE{OID} |
    OCTET STRING. Encrypted PKCS#8: SEQUENCE, SEQUENCE{OID PKCS#5 / PKCS#12-PBE}. OpenSSH: its
    magic. A certificate, SPKI or PKCS#1 public key never matches. Padding ends a blob: a run
    of one-blob-per-line values is judged on the first blob, never refused for padding inside."""
    b64 = b64.split("=", 1)[0]
    b64 = b64[:len(b64) // 4 * 4]
    try:
        d = binascii.a2b_base64(b64)
    except (binascii.Error, ValueError):
        return False
    if d.startswith(_OPENSSH_MAGIC):
        return True
    try:
        if d[0] != 0x30:
            return False
        p = _der_len(d, 1)
        if p < 0:
            return False
        if d[p] == 0x02:
            if d[p + 1] != 1 or d[p + 2] not in (0, 1):
                return False
            q = p + 3
            if d[q] in (0x02, 0x04):
                return True
            if d[q] == 0x30:
                r = _der_len(d, q + 1)
                return r > 0 and d[r] == 0x06
            return False
        if d[p] == 0x30:
            q = _der_len(d, p + 1)
            if q < 0 or d[q] != 0x06:
                return False
            oid = d[q + 2:q + 2 + d[q + 1]]
            return oid.startswith(_PKCS5) or oid.startswith(_PKCS12_PBE)
    except IndexError:
        return False
    return False


class UnmarkedRun:
    """The backstop for a key with no marker: a RUN of base64 body lines. Each line starting
    `M` (DER SEQUENCE) or `b3Bl` (OpenSSH) opens a candidate that collects `RUN_CHARS` base64
    characters (or what the run has when it ends) and is decoded once. O(1) per line: at most
    `RUN_CHARS // 4` candidates are open at a time."""

    def __init__(self) -> None:
        self.cands: List[List[Any]] = []
        self.hit = ""                                # the decoded head of the hit, once hit
        self.n = 0
        self.width = 0                               # the established run's first line
        self.total = 0                               # … and its base64 characters so far

    def feed(self, b64: str) -> bool:
        """One body line's base64. True when this line made the run a key."""
        if self.hit:
            return False
        if not self.cands and not _opens_run(b64) and _opens_run(b64.lstrip("+/")):
            b64 = b64.lstrip("+/")                   # `//MII…`: a comment lead, not key text
        if _opens_run(b64):
            if not self.cands:                       # the run is ESTABLISHED from this line:
                self.width, self.total = len(b64), 0  # its width, and its count from here
            self.cands.append([self.n, ""])
        if self.cands:
            if self.total == self.width and len(b64) < self.width:
                self.width = len(b64)                # the 2nd line settles the width
            self.total += len(b64)
        self.n += 1
        keep = []
        for c in self.cands:
            c[1] += b64[:RUN_CHARS - len(c[1])]
            if len(c[1]) >= RUN_CHARS:
                if der_private_key(c[1]):
                    self.hit = c[1]
                    return True
            else:
                keep.append(c)
        self.cands = keep
        return False

    def close(self) -> bool:
        """The run ended: judge the candidates still open on what they have."""
        if not self.hit:
            for c in self.cands:
                if len(c[1]) >= 16 and der_private_key(c[1]):
                    self.hit = c[1]
                    break
        self.cands = []
        return bool(self.hit)

    def first_open(self) -> int:
        return min((c[0] for c in self.cands), default=self.n)


#: `KeyWalk.state()` for "inside a base64 run that is a key" (it ends at a non-base64 line)
RUN_STATE = "run"


class KeyRunTooLong(MokataError, ValueError):
    """An END closes a base64 run longer than the walker could hold back: the run's start has
    already been let through, so the caller must refuse rather than send the rest. HARD: every
    line site turns it into its own refusal (`RefRefused`, `ContextRefused`, or "inside")."""


def _body_header(line: str) -> bool:
    """A PEM/PGP/SSH2 header line inside a key (`Proc-Type:`, `DEK-Info:`, `Comment:` …)."""
    return line[:96].lstrip(" \t\"'`#;/*").upper().startswith(_BODY_HEADERS)


#: a line that OPENS a value whose text starts on the next line: a YAML block scalar (`key: |`),
#: an opening triple quote, a backtick, a bracket
_VALUE_OPENERS = ("|", "|-", "|+", ">", ">-", ">+", '"""', "'''", "`", "(", "[", "{")


def _opens_value(line: str) -> bool:
    """Does `line` only open a value (see `_VALUE_OPENERS`)? A pending tolerant BEGIN reads
    past it, as past a blank line: the key's first body line is the one after it."""
    return line.rstrip().endswith(_VALUE_OPENERS)


def _body_of(piece: str) -> str:
    if piece.endswith("\r\n"):
        return piece[:-2]
    if piece and piece[-1] in LINE_SEPARATORS:
        return piece[:-1]
    return piece


class KeyWalk:
    """THE line-by-line key state machine, shared by every line site. `feed(body, item)` takes
    one line (no separator) and returns `[(item, is_key), …]` for the lines now decided, in
    order: base64 lines are held until their run is judged (at most `hold` of them; past that
    they are let through and an END that closes them raises `KeyRunTooLong`).

    States: INSIDE a marked key; a FORCED run (an unmarked-body hit, a base64 marker, a PEM
    header: base64 lines are key until a non-base64 line or an END); a PENDING tolerant BEGIN
    (it opens a key only if the next non-blank line is a body line or a PEM header — a dash-less
    or cut BEGIN in prose replaces its own line and nothing else); HELD base64 lines."""

    def __init__(self, inside: Any = False, hold: int = 4096) -> None:
        self.inside = inside is True or (bool(inside) and inside != RUN_STATE)
        self.forced = inside == RUN_STATE
        self.pending = False
        self.held: List[Any] = []
        self.run = UnmarkedRun()
        self.let_through = False
        self.hold = hold
        self.prev = ""
        self._carry = ""
        self._long = False
        self.hdr = False                             # the forced run is in a PEM header block
        self.fw = 0                                  # a forced run's line width (0: not known)
        self.bp = ""                                 # the block's common prefix
        self.bk = 0                                  # … over this many consecutive lines
        self.last = ""                               # the line before (a run's head)
        self.ft = 0                                  # … and its base64 characters so far

    def state_inside(self) -> bool:
        """Would the NEXT line start inside a key? (A pending tolerant BEGIN reads as inside:
        fail closed at a window edge.)"""
        return bool(self.state())

    def state(self) -> Any:
        """The state the NEXT line starts in, as `KeyWalk(inside=…)` takes it: True inside a
        marked key (or a pending tolerant BEGIN — fail closed at a window edge), `RUN_STATE`
        inside a base64 run that is a key (it ends at the first non-base64 line), else False."""
        if self.inside or self.pending:
            return True
        if self.forced or self.run.hit:
            return RUN_STATE
        return False

    def peek(self, following: Iterable[str]) -> Any:
        """`state()`, but an undecided base64 run open at this point is judged by reading ON:
        a window that starts in the middle of an unmarked key cannot see its head below it. Reads
        at most until that run is decided (`RUN_CHARS` characters per candidate)."""
        now = self.state()
        if now or not self.run.cands:
            return now
        probe = copy.deepcopy(self)
        probe.held = [None] * len(self.held)
        for ln in following:
            b64 = b64_body(ln)
            if b64 is None:
                return RUN_STATE if probe.run.close() else False
            if probe.run.feed(b64):
                return RUN_STATE
            if not probe.run.cands:
                return False
        return RUN_STATE if probe.run.close() else False

    def hot(self) -> bool:
        """May the caller replace lines that pass `quiet_inside` without a `feed`? (Inside a
        marked key, nothing pending.) After such lines the caller calls `ran(last_body)`."""
        return self.inside and not self.pending and not self._carry

    def ran(self, last_body: str) -> None:
        self.prev = last_body[-_JOIN_TAIL:]

    def _walk(self, ms: List[Marker]) -> None:
        """Advance the marker state over one line's markers, in order."""
        for i, m in enumerate(ms):
            if m.begin is None:
                if not self.inside:
                    self.forced = True
            elif self.inside:
                if m.begin is False:
                    self.inside = False
            elif m.begin:
                if m.firm:
                    self.inside = True
                    self.forced = False
                elif m.clear and i == len(ms) - 1:
                    self.pending = True
            elif self.forced:
                self.forced = False

    def _release(self, out: List[Tuple[Any, bool]], key: bool) -> None:
        out.extend((h, key) for h in self.held)
        self.held = []

    def _close_run(self, out: List[Tuple[Any, bool]]) -> None:
        hit = self.run.close()
        self._release(out, hit)
        self.run = UnmarkedRun()
        self.let_through = False

    def feed(self, body: str, item: Any = None, eol: bool = True) -> List[Tuple[Any, bool]]:
        out: List[Tuple[Any, bool]] = []
        if not eol:                                  # one block of a line too long to hold
            text = self._carry + body
            ms = [m for m in key_markers(text, "") if m.start < len(text) - KEY_OVERLAP]
            if ms and not (self.inside or self.forced or self.pending):
                self._close_run(out)
            self._walk(ms)
            self._carry = text[-KEY_OVERLAP:]
            self._long = self._long or bool(ms)
            return out
        long_line = bool(self._carry) or self._long
        if self._carry:
            body = self._carry + body
            self._carry = ""
            self._long = False
        if not long_line and not self.pending and quiet(body, self.prev):
            self.prev = body[-_JOIN_TAIL:]
            if self.inside:                          # the hot path: a key's body line
                return [(item, True)]
            ms = []
        else:
            ms = key_markers(body, self.prev)
            self.prev = body[-_JOIN_TAIL:]
        if self.run.cands or (self.forced and self.fw):
            lead = self._block(body)                 # in a run: its block of lines
        else:
            self.bk, lead = 0, ""                    # off a run: no block, no cost
        self.last = body
        if self.pending:
            if not body.strip() or _opens_value(body):
                out.append((item, True))
                return out
            self.pending = False
            if b64_body(body) is not None or _body_header(body) or b64_tail(body) is not None:
                self.inside = True
        if self.inside:
            out.append((item, True))
            self._walk(ms)
            return out
        if self.forced:
            if ms and any(m.begin is False for m in ms):
                out.append((item, True))
                self._walk(ms)
                return out
            if self.fw:
                b64, last = self._cont(body, lead, self.fw, self.ft)
                if b64 is not None and not last and self.ft == self.fw and len(b64) < self.fw:
                    self.fw = len(b64)               # the 2nd line settles the width
            else:                                    # opened by a marker: no width yet
                b64, last = b64_body(body), False
                if b64 is None:
                    b64 = b64_tail(body)
                if b64 is not None:
                    self.fw = len(b64)
            if b64 is not None:
                self.hdr = False
                self.ft += len(b64)
                if last:
                    self.forced = False
                    self.fw = self.ft = 0
                out.append((item, True))
                return out
            if _body_header(body) or (self.hdr and not body.strip()):
                self.hdr = bool(body.strip())        # headers, then the one blank line after them
                out.append((item, True))
                return out
            self.forced = False
            self.hdr = False
            self.fw = self.ft = 0
        if ms or long_line:
            first = next((m for m in ms if m.begin is not None), None)
            if first is not None and first.begin is False and self.held:
                if self.let_through:
                    raise KeyRunTooLong("an END closes a base64 run longer than was held")
                self._release(out, True)
                self.run = UnmarkedRun()
            else:
                self._close_run(out)
            out.append((item, bool(ms) or long_line and self.state_inside()))
            self._walk([m for m in ms if not (m is first and m.begin is False)])
            self.hdr = self.forced and any(m.begin is None and not m.firm for m in ms)
            return out
        b64, last = None, False
        if self.run.cands:                           # an established run: the continuation rule
            b64, last = self._cont(body, lead, self.run.width, self.run.total)
            if b64 is None:
                self._close_run(out)                 # not the key's: the run ends before it
        if b64 is None:
            b64 = b64_body(body)
            if b64 is not None and body.strip() != b64:
                self._close_run(out)                 # wrapped in code: it can only START a run
            elif b64 is None:
                b64 = b64_tail(body)                 # a key's head wrapped in code: it, too,
                if b64 is not None:                  # can only START a run
                    self._close_run(out)
                elif _escaped_key(body):             # a key escaped onto one line: this line
                    self._close_run(out)
                    out.append((item, True))
                    return out
        if b64 is not None:
            self.held.append(item)
            if self.run.feed(b64):
                self._release(out, True)
                self.forced = not last
                self.fw, self.ft = (self.run.width, self.run.total) if not last else (0, 0)
                self.run = UnmarkedRun()
                self.let_through = False
                return out
            if last:                                 # the key's last line: judge the run now
                self._close_run(out)
                return out
            if len(self.held) > self.hold:
                keep = self.run.first_open() - (self.run.n - len(self.held))
                keep = max(0, min(keep, len(self.held)))
                out.extend((h, False) for h in self.held[:keep])
                self.held = self.held[keep:]
                self.let_through = True
            return out
        self._close_run(out)
        out.append((item, False))
        return out

    def _block(self, body: str) -> str:
        """Fold `body` into the block of consecutive lines of the run (their common prefix, at
        most `BLOCK_GLUE_MAX`) and return the glue lead the block now trusts for this line. The
        block starts at the run's head (the line before its first continuation); a line sharing
        no prefix with the block (a blank one too) starts a new block. The TRAILING side needs no
        block: `run_line` reads a line's first span, so whatever follows it is already glue."""
        if not self.bk:                              # the run's head opens the block
            self.bp, self.bk = self.last[:BLOCK_GLUE_MAX], 1
        if self.bk:
            p = _common_prefix(self.bp, body)
            if p:
                self.bp, self.bk = p, self.bk + 1
                return block_lead(p)
        self.bp, self.bk = body[:BLOCK_GLUE_MAX], 1
        return ""

    @staticmethod
    def _cont(body: str, lead: str, width: int, total: int) -> Tuple[Optional[str], bool]:
        """A continuation line, read in order: with the block's lead stripped; with only the
        lead's part before its glued `/`/`+` stripped; as it is (`run_line`); then with its own
        lead skipped (`_skip_lead`: a first line that carried an extra lead, `key = "// …`). A
        reading that CONTINUES the run beats one that ENDS it — a `/` the lead took from key
        text leaves the line one short — and otherwise the first reading that ends it wins. The
        whole-line test always judges `body` itself (round 5)."""
        reads = [body]
        if lead and body.startswith(lead):
            core = lead.rstrip("+/")
            reads = [body[len(lead):]] + ([body[len(core):]] if core else []) + reads
        ends = None
        for t in reads:
            got, glast = run_line(t, width, total, body)
            if got is not None and not glast:
                return got, glast                    # it continues the run
            if got is not None and ends is None:
                ends = (got, glast)
        if ends is not None:
            return ends
        rest = _skip_lead(body, width)
        if rest is not None:
            got, glast = run_line(rest, width, total, body)
            if got is not None:                      # a full-width span: it continues the run
                return got, glast
        return None, False

    def finish(self) -> List[Tuple[Any, bool]]:
        out: List[Tuple[Any, bool]] = []
        self._close_run(out)
        return out


_U_ESC = re.compile(r"\\u([0-9a-fA-F]{4})")


def _unescaped(line: str) -> Optional[str]:
    """`line` with its `\\n`/`\\r\\n` escapes made real breaks and `\\uXXXX` decoded, when it
    has any: a key JSON-encoded onto one line (a session bundle value, a service-account file)
    is judged as the lines it encodes. None when there is nothing to expand."""
    if "\\n" not in line and "\\u" not in line:
        return None
    out = line.replace("\\r\\n", "\n").replace("\\n", "\n")
    if "\\u" in out:
        out = _U_ESC.sub(lambda m: chr(int(m.group(1), 16)), out)
    # (`"private_key": "MII…\n…"` puts the key's first body line after `: "` on the same
    # line: `b64_tail` reads it there, as on any code line)
    return out


def _escaped_key(line: str) -> List[Finding]:
    """The key findings inside a one-line ESCAPED text (see `_unescaped`), one level deep."""
    sub = _unescaped(line)
    if sub is None or "\n" not in sub and sub == line:
        return []
    return _scan_key_material(sub, escaped=False)


def _scan_key_material(text: str, escaped: bool = True) -> List[Finding]:
    """The signature layer's private-key findings over WHOLE text: one per marker (firm or
    tolerant), and one tolerant finding per unmarked base64 run that is a key."""
    out: List[Finding] = []
    prev = ""
    run = UnmarkedRun()
    start = 0
    lines = text.splitlines() or [text]

    def judge(run: UnmarkedRun, start: int, closing: bool) -> None:
        if (run.hit or (closing and run.close())):
            out.append(Finding("signature", KEY_TOLERANT,
                               "an unmarked private-key body (base64 run)", start,
                               token=run.hit))
    for i, line in enumerate(lines, start=1):
        ms = key_markers(line, prev)
        prev = line[-_JOIN_TAIL:]
        b64 = None if ms else b64_body(line)
        if b64 is None and not ms:
            b64 = b64_tail(line)                     # a key's head behind a code prefix: it
        if b64 is not None and line.strip() != b64:  # (any line wrapped in code) can only
            if run.n:                                # START a run
                if not run.hit:
                    judge(run, start, True)
                run = UnmarkedRun()
        if b64 is not None:
            if run.n == 0:
                start = i
            if not run.hit and run.feed(b64):
                judge(run, start, False)
            continue
        if run.n:
            if not run.hit:
                judge(run, start, True)
            run = UnmarkedRun()
        if not ms and escaped:
            for f in _escaped_key(line):
                out.append(Finding(f.layer, f.kind, f.detail + " (escaped on one line)", i,
                                   token=f.token or (line.strip() if f.kind == KEY_TOLERANT
                                                     else "")))
            continue
        for m in ms:
            if m.firm:
                out.append(Finding("signature", KEY_FIRM, "matched known pattern", i))
            else:
                out.append(Finding("signature", KEY_TOLERANT,
                                   "a damaged private-key marker", i, token=line.strip()))
    if run.n and not run.hit:
        judge(run, start, True)
    return out


def _shannon(s: str) -> float:
    if not s:
        return 0.0
    counts = {c: s.count(c) for c in set(s)}
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def _scan_signatures(text: str) -> List[Finding]:
    out: List[Finding] = []
    for i, line in enumerate(text.splitlines() or [text], start=1):
        for kind, rx in _SIGNATURES:
            if rx.search(line):
                out.append(Finding("signature", kind, "matched known pattern", i))
    return out + _scan_key_material(text)


def _segments(tok: str) -> List[str]:
    """Decompose an identifier into its word segments, splitting on BOTH separators and
    casing transitions — the two ways real code marks a word boundary:

        getUserAuthToken2FromRequest -> get User Auth Token 2 From Request
        MOKATA_SESSION_ID_OVERRIDE_2 -> MOKATA SESSION ID OVERRIDE 2
        parseHTTPResponseHeader      -> parse HTTP Response Header   (acronym kept whole)

    This is what makes the exemption identifier-AWARE rather than casing-blind (SECRET-FP)."""
    out: List[str] = []
    for piece in _IDENT_SEP_RE.split(tok):
        out.extend(_CASE_RUN_RE.findall(piece))
    return out


def _is_wordish(seg: str) -> bool:
    """A segment that is dictionary-free but WORD-SHAPED. Deliberately not a dictionary: the
    point is to accept `kubernetes`, `Binding`, `MOKATA` and reject a slice of a key.
    Requires a vowel, a sane length, and no consonant run longer than `length`/`strength`.

    ONE exception, checked FIRST: a member of the closed `_FUNCTION_WORDS` list is a word
    whatever its length. It bypasses the vowel test too, because `by` and `my` carry none —
    `y` is not a vowel here, deliberately (see `_VOWELS`), and that must not un-word them.
    Case-insensitive, so `the`/`The`/`THE` read the same across snake, camel and SCREAMING."""
    if seg.isalpha() and seg.lower() in _FUNCTION_WORDS:
        return True
    if not seg.isalpha() or not (_MIN_WORD <= len(seg) <= _MAX_SEGMENT):
        return False
    if not any(c in _VOWELS for c in seg):
        return False
    run = 0
    for c in seg:
        run = 0 if c in _VOWELS else run + 1
        if run > _MAX_CONSONANT_RUN:
            return False
    return True


def _has_boundary_marker(tok: str) -> bool:
    """Does the token carry a WORD BOUNDARY a human put there — a `-`/`_` separator, or a
    casing transition (two alpha runs meeting, as in `getUser` or `HTTPResponse`)?

    A digit is NOT a boundary marker. Without this, any long alpha run split only by a digit
    reads as two "words": measured, a REVERSED AWS key (`ELPMAXE7NNDOFSOIAIKA`) decomposes
    into two vowel-bearing runs and would buy the exemption. Every real convention this
    stage must support — camel, Pascal, SCREAMING_SNAKE, snake, kebab — carries one of these
    markers by construction, so requiring it costs no legitimate identifier.

    ONE EXCEPTION (SECRET-VALUE-SCAN, a grounded deviation). A digit run IS a boundary when the
    alpha run FOLLOWING it is a Capitalized word (`Xxx…`) — camelCase/PascalCase carried across
    a digit. Measured need: the JSON-Schema draft-validator attribute name referenced in
    mokata's OWN `src/mokata/schema.py` blocked at three sites, two of them a comment and a
    quoted string on the VALUE side, so binding awareness alone could not reach them; both of
    its alpha segments are word-shaped and only the missing boundary marker blocked it.

    This is the narrowest rule that separates that case from the mangled keys above: those
    decompose into ALL-CAPS runs either side of their digit, and an ALL-CAPS run is never a
    Capitalized word, so a reversed or digit-split key gains nothing from the exception."""
    if _IDENT_SEP_RE.search(tok):
        return True
    for piece in _IDENT_SEP_RE.split(tok):
        runs = _CASE_RUN_RE.findall(piece)
        if any(a.isalpha() and b.isalpha() for a, b in zip(runs, runs[1:])):
            return True
        if any(a.isdigit() and _is_capitalized_word(b) for a, b in zip(runs, runs[1:])):
            return True
    return False


def _strip_separators(s: str) -> str:
    """Undo `-`/`_` chunking, so a key split into word-shaped pieces is tested as the key."""
    return _SLUG_SEP_RE.sub("", s)


def _matches_known_shape(candidate: str) -> Optional[str]:
    """The name of the known-secret shape this candidate IS, or None.

    Tests the candidate raw and separator-stripped, `fullmatch` in both cases: the candidate
    must BE the shape, never merely contain or begin with it."""
    for form in (candidate, _strip_separators(candidate)):
        for name, rx in _KNOWN_SHAPES:
            if rx.fullmatch(form):
                return name
    return None


def _is_lowercase_slug(tok: str) -> bool:
    """A lowercase separated slug — path component, temp-dir name, filename stem, cache key.

    This is the 0.0.14 predicate's ONE sound half, kept deliberately. Word structure cannot
    replace it: real slugs are frequently not word-shaped at all. The case that forced this
    is macOS's own per-user temp directory, which is unavoidable in any content that mentions
    a temp path:

        /var/folders/y9/r2xr67z10lb6p5n3t_m1_c740000gn/T/

    Blocking that would be a far worse false positive than the one this stage fixes — it
    fires for every macOS user. Generated credentials are overwhelmingly mixed-case or hex,
    so exempting the lowercase-with-separators shape costs little.

    TIGHTER than 0.0.14 in one respect: it does NOT apply when every separated piece is pure
    hex. Hex chunks joined by `_`/`-` are a plausible way to split a key past a
    contiguous-run matcher, and they used to be exempt purely for lacking a capital letter.
    (Canonical UUIDs are also all-hex pieces — they are exempted BEFORE this, by shape.)"""
    if any(c.isupper() for c in tok):
        return False
    pieces = [p for p in _SLUG_SEP_RE.split(tok) if p]
    if len(pieces) < 2:                                  # no separator — not a slug
        return False
    return not all(_is_pure_hex(p) for p in pieces)      # all-hex chunks are a split key


def _is_uuid_shaped(tok: str) -> bool:
    """The canonical UUID grouping (8-4-4-4-12 hex) appearing among the token's separated
    pieces. UUIDs are a named, fixed shape — like the SRI-hash exemption above — so they are
    matched exactly rather than via the generic hex-chunk path, which must stay BLOCKED
    (hex chunks joined by separators are a plausible way to smuggle a key past a
    contiguous-run matcher)."""
    pieces = _IDENT_SEP_RE.split(tok)
    n = len(_UUID_GROUPS)
    for i in range(len(pieces) - n + 1):
        window = pieces[i:i + n]
        if tuple(len(p) for p in window) == _UUID_GROUPS \
                and all(_is_pure_hex(p) for p in window):
            return True
    return False


def _has_word_structure(tok: str) -> bool:
    """Is this an identifier a human wrote, or a generated key?

    An identifier decomposes (on casing transitions AND `-`/`_` separators) into word-shaped
    segments; a key decomposes into one-and-two-character debris. So the exemption is: the
    MAJORITY of the token's substance AND the MAJORITY of its alpha segments must be
    word-shaped. Digit segments are counted as substance but never as words, and never held
    against the segment majority — `…_2` is a suffix, not evidence either way.

    Replaces the 0.0.14 predicate, which exempted a token only when a separator was present
    AND there was NO uppercase — so every camelCase / PascalCase / SCREAMING_SNAKE identifier
    ≥20 chars carrying a digit hard-blocked (the live spec-check false positive, doc 86
    rider). Recognised as word structure: camelCase, PascalCase, SCREAMING_SNAKE, snake_case,
    kebab-case, and digit-suffixed variants of each.

    This is one term of a CONJUNCTION, not a bypass: a token blocks when it is high-entropy
    AND char-class mixed AND has no word structure. It is also TIGHTER than the predicate it
    replaces in one respect — lowercase hex/base64 chunks joined by `_` or `-` used to be
    exempt purely for lacking a capital letter, and now block.

    WHY BOTH TERMS SURVIVE THE `84:74` FIX — the counterexample, recorded because the row's
    OWN recommendation was to drop the segment-count term and keep only the char term. That is
    UNSAFE and measured so: a token of one long English word plus random debris
    (`Secretariat` + 15 chars of chunked noise) scores char = 0.550 and would PASS a char-only
    rule, while seg = 1/6 = 0.167 is what actually catches it. The camouflage is cheap to
    build and the char term cannot see it. So the fix went the other way — both terms and both
    0.50 thresholds stay, and `_FUNCTION_WORDS` widens what counts as a WORD instead. Pinned
    by `test_secret_fp.TestFunctionWordsAreWords.test_the_camouflage_token_still_blocks`.

    KNOWN LIMITATION: a separator-mangled uppercase key whose every chunk is vowel-bearing
    (`AKIA_IOSFODNN7_EXAMPLE`) is exempt here, where 0.0.15 blocked it. Nothing structural
    separates it from `MOKATA_SESSION_ID_OVERRIDE_2`; separating them needs a dictionary.
    The canonical CONTIGUOUS form of every such key is caught by the signature layer above,
    which is where named credential formats are meant to be caught — this backstop is for
    unnamed generated-looking runs, not for deliberately obfuscated ones."""
    if not _has_boundary_marker(tok):                    # no human-placed word boundary
        return False
    segs = _segments(tok)
    if len(segs) < 2:                                    # a single blob is not structure
        return False
    if any(len(s) > _MAX_SEGMENT for s in segs):         # a 20+-char run is a blob, not a word
        return False
    total = sum(len(s) for s in segs)
    alpha = [s for s in segs if s.isalpha()]
    if not total or not alpha:
        return False
    wordish = [s for s in alpha if _is_wordish(s)]
    return (sum(len(s) for s in wordish) / total >= _WORDISH_CHAR_FRACTION
            and len(wordish) / len(alpha) >= _WORDISH_SEGMENT_FRACTION)


def _is_alphabet_listing(tok: str) -> bool:
    """A character-set CONSTANT written out in order — `"0123456789abc…XYZ-_"`, an unambiguous
    code alphabet `"ABCDEFGHJKMNPQRSTUVWXYZ23456789"` (0.0.26 O14b: both in mokata's own `src/`,
    both walled by secret-guard). Its code points almost never step DOWN: a listing descends only
    where one class ends and the next begins. A generated key descends at about every other
    character, so allowing one descent per ten characters costs nothing measurable: for a random
    20-char run the chance of <= 2 descents is on the order of 1e-9."""
    descents = sum(1 for a, b in zip(tok, tok[1:]) if ord(b) < ord(a))
    return descents <= len(tok) // 10


def _is_env_var_name(tok: str) -> bool:
    """A SCREAMING_SNAKE environment-variable / constant NAME written where it is not bound
    (prose, a quoted string): `MOKATA_G2_JUDGE_API_KEY` (0.0.26 O14b). Three-letter words (`API`,
    `KEY`) sit below `_MIN_WORD`, so the word-structure term could not see it. Narrow on purpose:
    at least four `_`-separated pieces, every piece vowel-bearing or a short letter+digit tag
    (`G2`, `V1`), and at least two pieces genuinely word-shaped. A generated key carries no
    separators; one deliberately chunked into vowel-bearing words is the documented limitation of
    `_has_word_structure`, which this does not widen."""
    if not _ENV_NAME_RE.fullmatch(tok):
        return False
    pieces = tok.split("_")
    for piece in pieces:
        if any(c in _VOWELS for c in piece):
            continue
        if len(piece) <= 3 and piece[0].isalpha() and piece[1:].isdigit():
            continue
        return False
    return sum(1 for piece in pieces if _is_wordish(piece)) >= 2


def _scan_entropy(text: str) -> List[Finding]:
    """Backstop for a rich-alphabet, high-entropy run that has no word structure (a
    generated-looking key/token). The blocking test is a CONJUNCTION — high entropy AND a
    char-class mix AND the ABSENCE of word structure — so everything that legitimately looks
    high-entropy but isn't a credential is exempted up front: path/URL segments, identifiers
    in any casing convention, UUIDs, pure-hex digests (git SHAs, md5/sha256 hex), and
    SRI/lockfile hashes.

    Above that conjunction sits the known-shape FLOOR: a candidate that IS a documented
    credential shape (raw or separator-stripped) blocks before any exemption is consulted.

    TWO PASSES, and the split between them is the whole point of SECRET-VALUE-SCAN:

      1. THE FLOOR runs on the RAW line and is POSITION-INDEPENDENT. A credential shape is a
         credential wherever it sits — value side, name side, or bare. This is precisely what
         makes value-side scanning safe to do at all: a secret cannot buy the name exemption
         by being written in name position, because the floor (and the signature layer above,
         which is also position-independent) never consults position. Both of the floor's arms
         — raw AND separator-stripped — stay in this pass.

      2. THE ENTROPY BACKSTOP runs on the line with binding NAMES masked out, so it judges
         assigned VALUES and bare runs only. A suspicious NAME may still raise a value's
         suspicion — that is what the `secret-assignment` signature already does — but a name
         is never itself a credential.

    A run with no binding on its line is untouched by the mask and keeps the conjunction
    exactly as it was, so a secret pasted alone, in prose, in a URL, in a comment, or inside a
    heredoc body still blocks."""
    out: List[Finding] = []
    for i, line in enumerate(text.splitlines() or [text], start=1):
        for sub in _candidate_runs(line):                   # PASS 1 — floor, RAW line
            shape = _matches_known_shape(sub)
            if shape:
                out.append(Finding("entropy", "known-secret-shape",
                                   f"IS the {shape} shape "
                                   f"(anchored full match, separator-stripped)", i))
        for sub in _candidate_runs(_mask_binding_names(line)):   # PASS 2 — VALUE side only
            if _matches_known_shape(sub):       # already reported by the floor above
                continue
            if _is_uuid_shaped(sub):            # canonical 8-4-4-4-12 UUID
                continue
            if _has_word_structure(sub):        # camel/Pascal/SCREAMING/snake/kebab
                continue
            if _is_lowercase_slug(sub):         # path component / temp dir / slug
                continue
            if _is_pure_hex(sub):               # git SHA / md5 / sha256 hex digest
                continue
            if _is_alphabet_listing(sub):       # a character-set constant, in order
                continue
            if _is_env_var_name(sub):           # SCREAMING_SNAKE env-var / constant name
                continue
            if _shannon(sub) >= 3.5:
                out.append(Finding("entropy", "high-entropy-token",
                                   f"len={len(sub)} entropy>=3.5", i, token=sub))
    return out


def _scan_path(path: str) -> List[Finding]:
    base = os.path.basename(path)
    if base in _SENSITIVE_NAMES or base.startswith(".env") \
            or base.endswith(_SENSITIVE_SUFFIXES):
        return [Finding("path", "sensitive-location", path)]
    return []


def scan(text: str = "", path: Optional[str] = None,
         for_send: bool = False, ignores: Any = None) -> List[Finding]:
    """Run every applicable layer. `for_send=True` adds the egress layer: any secret in
    outbound content is fatal.

    SECRET-IGNORE — `ignores` (a `secret_ignore.IgnoreStore`, or None) suppresses findings the
    repo has RECORDED as false positives. Three properties make that safe, and all three live
    here rather than in the store:

      * ONLY `is_ignorable` findings are eligible — the entropy backstop's guess and the
        TOLERANT key marker (`key-marker-tolerant`: a damaged-looking marker or an unmarked key
        body, 0.0.28 E-Q5), and nothing else. A FIRM key finding (`private-key`), the rest of
        `_scan_signatures` and the known-shape FLOOR (which also reports on layer
        `entropy`, as `known-secret-shape`) are filtered before this and never consulted
        against the store. A forged store cannot reach them.
      * The suppression is keyed to hash(token) + `path`, so it is this exact string in this
        one file. `path=None` matches nothing.
      * Egress is computed AFTER suppression, not before: an ignored guess is not a secret and
        must not manufacture an egress block, while a real finding still must. A tolerant key
        finding is the exception: on a send (`for_send=True`) it is NEVER suppressed — a record
        lets a write through a gate, never a key out of the machine.

    `ignores=None` (every pre-0.0.16 caller) is byte-identical to before."""
    findings: List[Finding] = []
    if text:
        findings += _scan_signatures(text)
        findings += _scan_entropy(text)
    if path:
        findings += _scan_path(path)
    if ignores is not None:
        from .secret_ignore import is_ignorable
        # E-Q5: a TOLERANT key finding is recordable at the gate and the hook, but egress is
        # fatal for both tiers — on a send it is never suppressed.
        findings = [f for f in findings
                    if not (is_ignorable(f) and not (for_send and f.kind == KEY_TOLERANT)
                            and ignores.is_ignored(f.token, path))]
    if for_send and any(f.layer in ("signature", "entropy") for f in findings):
        findings.append(Finding("egress", "secret-egress-blocked",
                                "secret content must not leave the machine"))
    return findings


def has_secrets(findings: List[Finding]) -> bool:
    return bool(findings)
