# Security Policy

Documentation: <https://jasgujral.github.io/mokata-oss/>

## Supported versions

| Version | Supported |
|---------|-----------|
| 1.0.x   | ✅        |
| < 1.0   | ❌        |

## Reporting a vulnerability

**Please do not open a public issue for security vulnerabilities.**

Report privately via GitHub's private vulnerability reporting:

1. Go to the repository's **Security** tab → **Report a vulnerability**
   (<https://github.com/JasGujral/mokata-oss/security/advisories/new>).
2. Include a description, reproduction steps, affected version, and impact.

We aim to acknowledge a report within **3 business days** and to provide a remediation
plan or fix timeline within **14 days**, coordinating disclosure with you.

## Scope

This policy covers vulnerabilities in mokata's own code (the engine, gates, hooks, CLI,
and packaging). Note that mokata also ships a *defensive* feature — 4-layer secret
protection and a sync security hook (`secret_guard.py`, exit code 2) — that blocks secrets
before they are written, committed, or sent. That feature is part of the product, not the
vulnerability-reporting channel described here.

mokata is local-first and sends nothing off-machine by default; the `minimal` profile
performs zero network egress.
