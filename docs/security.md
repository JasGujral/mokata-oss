# Security

Report vulnerabilities **privately** via GitHub's
[private vulnerability reporting](https://github.com/JasGujral/mokata-oss/security/advisories/new) —
do not open a public issue. See the repository's
[`SECURITY.md`](https://github.com/JasGujral/mokata-oss/blob/master/SECURITY.md) for supported
versions and response expectations.

Separately, mokata ships a *defensive* feature: 4-layer secret protection and a sync
security hook (`secret_guard.py`, exit code 2) that blocks secrets before they are written,
committed, or sent. mokata is local-first and sends nothing off-machine by default.
