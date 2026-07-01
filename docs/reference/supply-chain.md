# Supply-chain & security

mokata is built to clear a company security review. Every release carries three
independently-verifiable trust signals, and the project runs a coordinated-disclosure policy.

> **What runs where (be honest):** the signing, SBOM, and attestation steps **execute in CI at
> release time** — when a `v*` tag is pushed and the maintainer cuts the release on the public
> repo (`JasGujral/mokata-oss`). The repository itself ships **no pre-signed artifacts**; the
> trust signals attach to the **GitHub Release**. The reproducible-build *check* you can also run
> locally. All release-time jobs are **gated to the real repo**, so they're a no-op on a fork.

## 1. Signed releases — build provenance (Sigstore / SLSA)

The release workflow produces a **build-provenance attestation** for the built wheel + sdist
(and the SBOM) via [`actions/attest-build-provenance`](https://github.com/actions/attest-build-provenance),
backed by Sigstore. It records *what* was built, *from which commit*, *by which workflow* — a
SLSA-style provenance statement, signed with a short-lived OIDC identity (no long-lived keys).

Verify a downloaded artifact:

```bash
gh attestation verify mokata-X.Y.Z-py3-none-any.whl --repo JasGujral/mokata-oss
```

Least-privilege: only the `build` job holds `id-token: write` + `attestations: write`; the
workflow default is `contents: read`, and only the publish job gets `contents: write`.

## 2. SBOM (CycloneDX)

Each release attaches **`sbom.cdx.json`**, a [CycloneDX](https://cyclonedx.org/) Software Bill
of Materials generated with the pinned `cyclonedx-bom` tool. mokata's core has **no required
runtime dependencies**, so the SBOM is small by design — it lets a reviewer enumerate exactly
what `pip install mokata` brings in.

## 3. Reproducible builds

The sdist and wheel are **deterministic**: the build honors `SOURCE_DATE_EPOCH` (set from the
tag commit's time), and a small stdlib normalizer (`scripts/normalize_sdist.py`) closes the one
gap setuptools leaves — the build-generated members (`PKG-INFO`, `*.egg-info`, directory
entries) otherwise carry the wall-clock build time. Normalization rewrites **archive metadata
only** (mtimes, ownership, member order, gzip header) — never file **contents** — so the
installed package is byte-for-byte unchanged.

Verify it yourself — build twice and compare:

```bash
python -m pip install build
scripts/check-reproducible.sh        # builds twice, normalizes, fails closed on any sha256 diff
```

Identical hashes mean anyone can rebuild the published artifacts and confirm they match.

## 4. Coordinated disclosure

Security issues are handled under a **coordinated (responsible) disclosure** policy: report
privately via GitHub Security Advisories, we triage and fix, and we disclose together once a fix
is available. Supported versions, scope, response targets, and the reporting link are in
[`SECURITY.md`](https://github.com/JasGujral/mokata-oss/blob/main/SECURITY.md).

## Release ordering (unchanged)

These supply-chain steps **do not weaken** the fail-closed release order: `scripts/release.sh`
still verifies version-consistency at the exact commit being tagged and tags **only after** the
public mirror sync + a passing `release-check` (the tag-triggered workflow above then builds,
signs, and publishes).
