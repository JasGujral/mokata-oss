# API & interface design — primary sources (JIT detail)

Pulled in just-in-time when the API skill is engaged and the heavier detail is needed. Authored
clean-room in mokata's own words; every external claim is anchored to a primary source below. Where
a specific behaviour could not be verified against the primary source at authoring time, it is
marked **UNVERIFIED** and must be confirmed at the cited URL for the version in use before it is
relied on.

## The named principles this skill encodes

### Hyrum's Law
"With a sufficient number of users of an API, it does not matter what you promise in the contract:
all observable behaviours of your system will be depended on by somebody."
Source: https://www.hyrumslaw.com/

Encoded in mokata as: the documented contract is a *lower bound* on what you have promised. Safety
of a change is decided by the blast-radius over real callers, not by whether the documented contract
is preserved. Incidental, undocumented behaviours (error strings, list ordering, timing, defaults)
harden into contract once callers observe them.

### Contract-first design
State the operation's inputs, outputs, error cases, and invariants as an explicit artifact before
implementing behind it. A machine-readable contract format is the OpenAPI Specification
(https://spec.openapis.org/oas/latest.html), which lets both sides build and test against one
source of truth. The exact conformance/codegen guarantees of any given tool are **UNVERIFIED** here
— confirm against the spec version in use.

## HTTP semantics — RFC 9110

Source: https://www.rfc-editor.org/rfc/rfc9110 (HTTP Semantics)

- **Safe methods** (e.g. GET, HEAD) are not expected to cause state change (RFC 9110 §9.2.1).
- **Idempotent methods** (e.g. GET, HEAD, PUT, DELETE) can be repeated with the same effect as a
  single request beyond the first application (RFC 9110 §9.2.2). Clients and proxies may retry them;
  breaking that assumption breaks a contract callers rely on.
- **Status codes** (RFC 9110 §15) are the primary signal clients branch on: 2xx success, 3xx
  redirection, 4xx client error, 5xx server error. Changing the class or specific code a case
  returns is a contract change even when the response body is unchanged.
- **Extensibility** (RFC 9110 §16): new methods, status codes, and fields are the compatible way to
  evolve; tolerant readers ignore what they do not understand.

The section numbers above are cited from RFC 9110 as the primary source; confirm the exact section
text at the URL before quoting it verbatim — treat any paraphrase as **UNVERIFIED** until read.

## Requirement keywords — RFC 2119 / RFC 8174

Sources: https://www.rfc-editor.org/rfc/rfc2119 · https://www.rfc-editor.org/rfc/rfc8174

MUST / MUST NOT / REQUIRED / SHOULD / SHOULD NOT / MAY have precise, defined meanings. Use them
deliberately when stating a contract clause — they express obligation levels, not emphasis.

## JSON payloads — RFC 8259

Source: https://www.rfc-editor.org/rfc/rfc8259 (The JavaScript Object Notation (JSON) Data
Interchange Format)

Adding a member to an object is generally backward-compatible for tolerant readers; removing a
member, renaming it, or narrowing its type is observable to a caller and therefore a breaking change.

## Versioning — Semantic Versioning

Source: https://semver.org/

- MAJOR — an incompatible (breaking) change to the public contract.
- MINOR — backward-compatible added functionality.
- PATCH — backward-compatible fixes.

mokata's rule: classify every contract change as compatible or breaking, version breaking changes,
and prefer *extend → deprecate → remove* (routing the removal through the deprecation domain's
blast-radius-on-removal path) over an in-place break.

## Robustness principle (context, applied with care)

"Be conservative in what you send, be liberal in what you accept." (Postel's robustness principle,
articulated in early Internet specs such as RFC 761, https://www.rfc-editor.org/rfc/rfc761.)
Applied narrowly: accept tolerantly at boundaries, emit strictly. Note the modern caveat that overly
liberal acceptance can entrench divergent behaviour (a Hyrum's-Law effect) — treat this trade-off as
**UNVERIFIED** guidance to weigh per interface, not a rule.
