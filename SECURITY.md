# Security Policy

Documentation: <https://jasgujral.github.io/mokata-oss/> ·
Supply-chain details: <https://jasgujral.github.io/mokata-oss/reference/supply-chain/>

mokata follows a **coordinated (responsible) disclosure** policy: report privately, we
investigate and fix, and we disclose together once a fix is available.

## Supported versions

mokata is pre-1.0; security fixes land on the **latest released `0.0.x`** line. Update to the
latest release before reporting, and pin to a released version (not `main`) for production use.

| Version | Supported |
|--------------------|-----------|
| latest `0.0.x`     | ✅ security fixes |
| older `0.0.x`      | ❌ upgrade to latest |
| unreleased `main`  | ❌ (development) |

## Reporting a vulnerability (private)

**Please do not open a public issue, PR, or discussion for security vulnerabilities** — that
would disclose the issue before a fix exists.

Report privately via **GitHub's private vulnerability reporting** (GitHub Security Advisories):

1. Go to the repository's **Security** tab → **Report a vulnerability**
   (<https://github.com/JasGujral/mokata-oss/security/advisories/new>).
2. Include: a description, reproduction steps, the affected version (`mokata version`), impact,
   and any suggested remediation.

This opens a private advisory thread visible only to you and the maintainers.

## Our commitment (coordinated disclosure)

These are good-faith targets, not contractual SLAs (mokata is a small open-source project):

| Stage | We aim to… |
|-------|------------|
| Acknowledge | confirm receipt within **3 business days** |
| Triage | assess severity + give an initial response within **~7 days** |
| Fix | agree a remediation/timeline within **~14 days**, severity-dependent |
| Disclose | publish a fix + advisory **together**, crediting you (unless you prefer anonymity) |

We coordinate the public disclosure date with you. We will not pursue legal action against
good-faith research that follows this policy (no privacy violation, no data destruction, no
service disruption, and no access beyond what's needed to demonstrate the issue).

## Scope

In scope: vulnerabilities in **mokata's own code** — the engine, gates, hooks, CLI, MCP server,
and packaging (including the release/supply-chain tooling).

Out of scope: issues in third-party tools mokata merely *orchestrates* (report those upstream);
findings that require a compromised host or already-leaked credentials; and the *defensive*
feature mokata ships — the 4-layer secret protection + sync security hook (`secret_guard.py`,
exit code 2) that blocks secrets before they are written/committed/sent. That feature is part of
the product, not the vulnerability-reporting channel here.

mokata is **local-first** and sends nothing off-machine by default; the `minimal` profile
performs **zero network egress**.

## Verifying a release (supply chain)

Releases are built in CI at tag time with three trust signals (see the
[supply-chain reference](https://jasgujral.github.io/mokata-oss/reference/supply-chain/)):

- **Build-provenance attestation** (Sigstore / SLSA) — verify a downloaded artifact with:

  ```bash
  gh attestation verify <artifact> --repo JasGujral/mokata-oss
  ```

- **SBOM** (`sbom.cdx.json`, CycloneDX) attached to each GitHub Release.
- **Reproducible build** — the sdist/wheel are deterministic (honor `SOURCE_DATE_EPOCH`); you
  can rebuild and compare with `scripts/check-reproducible.sh`.
