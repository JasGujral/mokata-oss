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

import math
import os
import re
from dataclasses import dataclass
from typing import Any, List, Optional

LAYERS = ("signature", "entropy", "path", "egress")

# Known-credential signatures (Stage 46). Each has a DISTINCTIVE, low-false-positive shape
# (a fixed prefix or a structural marker) so it hard-blocks regardless of entropy — the
# named formats never depend on the entropy backstop. Provider-key bodies are matched as
# CONTIGUOUS runs (no `-`) so a kebab identifier that merely starts with a prefix can't trip.
_SIGNATURES = [
    # AWS — access key id (long-term AKIA / temporary ASIA). The 40-char secret key has no
    # distinctive prefix; it's caught by the secret-assignment rule + the entropy backstop.
    ("aws-access-key", re.compile(r"(?:AKIA|ASIA)[0-9A-Z]{16}")),
    # Private keys (PEM) — covers OpenSSH and GCP service-account `private_key` blocks too.
    ("private-key", re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP |ENCRYPTED )?PRIVATE KEY-----")),
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
_SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx")

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
        if end > 0 and line[end - 1] in "'\"":              # closing quote of a quoted key
            end -= 1
        start = end
        while start > 0 and line[start - 1] in _NAME_CHARS:
            start -= 1
        if start < end:
            spans.append((start, end))
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
    return out


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

      * ONLY `is_ignorable` findings are eligible — the entropy backstop's guess, and nothing
        else. `_scan_signatures` and the known-shape FLOOR (which also reports on layer
        `entropy`, as `known-secret-shape`) are filtered before this and never consulted
        against the store. A forged store cannot reach them.
      * The suppression is keyed to hash(token) + `path`, so it is this exact string in this
        one file. `path=None` matches nothing.
      * Egress is computed AFTER suppression, not before: an ignored guess is not a secret and
        must not manufacture an egress block, while a real finding still must.

    `ignores=None` (every pre-0.0.16 caller) is byte-identical to before."""
    findings: List[Finding] = []
    if text:
        findings += _scan_signatures(text)
        findings += _scan_entropy(text)
    if path:
        findings += _scan_path(path)
    if ignores is not None:
        from .secret_ignore import is_ignorable
        findings = [f for f in findings
                    if not (is_ignorable(f) and ignores.is_ignored(f.token, path))]
    if for_send and any(f.layer in ("signature", "entropy") for f in findings):
        findings.append(Finding("egress", "secret-egress-blocked",
                                "secret content must not leave the machine"))
    return findings


def has_secrets(findings: List[Finding]) -> bool:
    return bool(findings)
