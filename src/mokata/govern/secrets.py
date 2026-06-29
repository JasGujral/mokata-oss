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
from typing import List, Optional

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
# check so a long file path or URL (e.g. "docs/build/02-mokata-build-status.md") is evaluated
# as its short word-like segments, not as one "high-entropy" blob (the segments don't trip it).
_SEP_RE = re.compile(r"[/\\.]+")
# Hex alphabet — a pure-hex run is a DIGEST (git SHA, md5, sha256, a UUID's hex), not a
# credential. Digests are not secrets, so the entropy backstop must not flag them (a 40-hex
# git SHA blocking legit content was a latent false positive). Credential *assignments* and
# the known token formats above still catch hex-valued secrets via the signature layer.
_HEX = frozenset("0123456789" + "abcdef" + "ABCDEF")
# Subresource-integrity / lockfile hashes (npm `sha512-…`, etc.) — benign base64 digests.
_INTEGRITY_RE = re.compile(r"^(?:sha1|sha256|sha384|sha512|md5)-", re.IGNORECASE)


def _is_pure_hex(s: str) -> bool:
    return all(c in _HEX for c in s)


@dataclass
class Finding:
    layer: str
    kind: str
    detail: str
    line: int = 0


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


def _is_structured_identifier(tok: str) -> bool:
    """A lowercase kebab/snake/UUID-style identifier (path segment, slug, filename stem,
    UUID) — has `-`/`_` separators and no uppercase. Real secrets are either contiguous
    (hex/base64 runs) or mixed-case random; word-structured lowercase tokens are not secrets,
    and flagging them (paths, slugs, UUIDs) is the false positive we must avoid. The signature
    layer still catches credential *assignments*; this only relaxes the heuristic entropy
    backstop for a clearly-identifier shape."""
    return ("-" in tok or "_" in tok) and not any(c.isupper() for c in tok)


def _scan_entropy(text: str) -> List[Finding]:
    """Backstop for a CONTIGUOUS, rich-alphabet, high-entropy run (a generated-looking
    key/token with no path separators). Everything that legitimately looks high-entropy but
    isn't a credential is exempted up front: path/URL segments, kebab/snake/UUID
    identifiers, pure-hex digests (git SHAs, md5/sha256 hex), and SRI/lockfile hashes."""
    out: List[Finding] = []
    for i, line in enumerate(text.splitlines() or [text], start=1):
        for tok in _TOKEN_RE.findall(line):
            if _INTEGRITY_RE.match(tok):            # npm/SRI lockfile hash (sha512-…)
                continue
            for sub in _SEP_RE.split(tok):          # break paths / URLs / filenames
                if len(sub) < 20:
                    continue
                has_digit = any(c.isdigit() for c in sub)
                has_alpha = any(c.isalpha() for c in sub)
                if not (has_digit and has_alpha):
                    continue
                if _is_structured_identifier(sub):  # kebab/snake/lowercase-UUID
                    continue
                if _is_pure_hex(sub):               # git SHA / md5 / sha256 hex digest
                    continue
                if _shannon(sub) >= 3.5:
                    out.append(Finding("entropy", "high-entropy-token",
                                       f"len={len(sub)} entropy>=3.5", i))
    return out


def _scan_path(path: str) -> List[Finding]:
    base = os.path.basename(path)
    if base in _SENSITIVE_NAMES or base.startswith(".env") \
            or base.endswith(_SENSITIVE_SUFFIXES):
        return [Finding("path", "sensitive-location", path)]
    return []


def scan(text: str = "", path: Optional[str] = None,
         for_send: bool = False) -> List[Finding]:
    """Run every applicable layer. `for_send=True` adds the egress layer: any secret in
    outbound content is fatal."""
    findings: List[Finding] = []
    if text:
        findings += _scan_signatures(text)
        findings += _scan_entropy(text)
    if path:
        findings += _scan_path(path)
    if for_send and any(f.layer in ("signature", "entropy") for f in findings):
        findings.append(Finding("egress", "secret-egress-blocked",
                                "secret content must not leave the machine"))
    return findings


def has_secrets(findings: List[Finding]) -> bool:
    return bool(findings)
