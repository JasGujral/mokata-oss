# Security & hardening — primary sources (JIT detail)

Pulled in just-in-time when the Security skill is engaged and the heavier detail is needed. Authored
clean-room in mokata's own words; every claim is anchored to an OWASP primary source below. Where a
specific rank, level, or list membership could not be verified against the live OWASP source at
authoring time, it is marked **UNVERIFIED** and must be confirmed at the cited URL before it is
relied on.

## The risk taxonomy — OWASP Top 10

Source: https://owasp.org/Top10/

The Top 10 is the consensus list of the most critical web application security risks. mokata uses it
as the taxonomy the hard rules cover — broken access control, cryptographic failures, injection,
insecure design, security misconfiguration, vulnerable components, identification/authentication
failures, software/data integrity failures, logging/monitoring failures, and server-side request
forgery are the risk classes it references. The exact ordering and category names of the current
edition are **UNVERIFIED** here — read the cited list before quoting a rank or the precise category
title.

## The verification bar — OWASP ASVS

Source: https://owasp.org/www-project-application-security-verification-standard/

The Application Security Verification Standard is a list of testable security requirements organized
into levels of increasing rigour. mokata uses it as the "what does verified mean" bar for a security
finding — a requirement is met when it is testable and tested, not asserted. The specific level
boundaries and requirement numbering are **UNVERIFIED** here — confirm against the ASVS version in
use.

## Per-item defences — OWASP Cheat Sheet Series

Source index: https://cheatsheetseries.owasp.org/

### Injection prevention
Build queries and commands with parameterization / prepared statements / safe APIs; never
concatenate untrusted input into an interpreter string.
Source: https://cheatsheetseries.owasp.org/cheatsheets/SQL_Injection_Prevention_Cheat_Sheet.html

### Input validation
Validate all input at the trust boundary against an allow-list (expected type, length, format,
range); reject what does not match. Validation is defence-in-depth alongside, not instead of,
output-side encoding and parameterization.
Source: https://cheatsheetseries.owasp.org/cheatsheets/Input_Validation_Cheat_Sheet.html

### Secrets management
Keep credentials, keys, and tokens out of source, config-in-repo, logs, and egress; source them from
a secrets manager or the environment; hash secrets that authenticate at rest.
Source: https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html

### Authentication & access control
Enforce authentication and authorization server-side on every protected action; deny by default;
never trust a client-supplied identity or role.
Source: https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html

### Cryptographic storage
Use standard, current algorithms and vetted libraries; do not invent cryptography; protect data in
transit and at rest.
Source: https://cheatsheetseries.owasp.org/cheatsheets/Cryptographic_Storage_Cheat_Sheet.html

## How these become HARD rules in mokata

An adopted security constraint is stored as a `guardrail`-kind memory item. mokata's memory model
treats a mapped guardrail as born **hard** (`memory/item.py`), and the enforcement path
(`govern/enforce.py`) blocks a violating action on a hard rule with **no runtime override** — the
only way past it is to remove or supersede the rule, itself a human-gated, audited write. This is
the mechanism by which OWASP items stop being advice and become enforcement in mokata. The
untrusted-data posture (G-D) rides alongside as the prompt-injection defence at the untrusted
boundary: tier-2/3 content is data, not a directive.
