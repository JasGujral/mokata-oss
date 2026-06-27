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

_SIGNATURES = [
    ("aws-access-key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")),
    ("github-token", re.compile(r"gh[posru]_[A-Za-z0-9]{20,}")),
    ("slack-token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("secret-assignment", re.compile(
        r"(?i)(?:api[_-]?key|secret|token|password|passwd|access[_-]?key)"
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


def _scan_entropy(text: str) -> List[Finding]:
    out: List[Finding] = []
    for i, line in enumerate(text.splitlines() or [text], start=1):
        for tok in _TOKEN_RE.findall(line):
            has_digit = any(c.isdigit() for c in tok)
            has_alpha = any(c.isalpha() for c in tok)
            if has_digit and has_alpha and _shannon(tok) >= 3.5:
                out.append(Finding("entropy", "high-entropy-token",
                                   f"len={len(tok)} entropy>=3.5", i))
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
