---
name: frontend-security
description: mokata · Frontend security for React web — authorization first, then secrets in bundles, injection sinks and supply chain; audit first, fix on request.
when_to_use: Engage when mokata's per-turn routing or `spec show` names `frontend-security` for this run, when the spec's `domains` carry both `frontend-react` and `security`, when a React web change adds or changes a Server Function, a route handler, a page that reads protected data, or an environment variable exposed to the client, or when a change renders user-controlled HTML or URLs. Do NOT engage for a backend-only service (the `security` skill covers it), for a React Native app, or for a purely visual change that touches no data, input, or route.
---

> **mokata Agent Skill.** This is mokata's `frontend-security` domain knowledge. mokata LOADS it
> when the approved spec routes both a React web surface and a security surface — never on a prompt
> keyword. It specialises the `security` skill for the browser/server-rendering boundary and feeds
> the secret-guard that already runs on every write. Durable writes are **human-gated**.

⛭ mokata frontend-security active — gate: secrets hard-block a write or egress; human approval cannot override

# mokata · Frontend security (React web)

**Audit first, fix on request.** Each check below follows the same shape as performance work:
**prove** the weakness (a request that should be refused and is not), **fix** the smallest thing,
**verify** the same request is now refused, **guard** it with a test. A finding without a
reproduction is a suspicion; say which one you have.

## The checklist — in this order, authorization first

1. **Authorization on every server entry point (OWASP A01, broken access control).** A Server
   Function / Server Action is a public HTTP endpoint: anyone can POST to it directly, without the
   page, the button, or the client-side check that normally guards it. Route handlers and API
   routes are the same. Every one authenticates the caller AND checks they may act on THIS object,
   inside the function. Middleware alone is never the authorization layer — path-matching bypasses
   recur. (https://owasp.org/Top10/ · https://react.dev/reference/rsc/server-functions)
2. **Data crossing to the client.** Props passed from a Server Component to a Client Component, and
   values returned from a Server Function, are delivered to the browser. Return and pass only the
   fields the user may see.
3. **Secrets in the built bundle.** Build tools inline environment variables carrying a public
   prefix into client JavaScript. A secret given such a prefix "to make it work" is published.
   Inspect the production build output for credentials — scanning git history does not catch a
   value injected at build time.
4. **Injection sinks.** `dangerouslySetInnerHTML` with anything user-influenced, `href`/`src` built
   from user input (a `javascript:` URL), and direct DOM writes (`innerHTML`) need sanitization or a
   safer construct. A strict Content-Security-Policy and Trusted Types reduce the blast radius; roll
   them out report-only first. (https://developer.mozilla.org/en-US/docs/Web/HTTP/CSP ·
   https://developer.mozilla.org/en-US/docs/Web/API/Trusted_Types_API)
5. **Supply chain (OWASP A03 in the 2025 list).** A new dependency is new code with install-time
   execution. Check the lockfile is committed and resolves from the expected registry, and whether
   the package manager enforces a minimum release age for new versions. Framework security
   advisories (React Server Components, the meta-framework) set version floors — read the
   advisories for the versions the project pins.

Browser-support figures, CVE identifiers and patched-version numbers are **UNVERIFIED** in this
skill on purpose: read the advisory or the cited page for the project's versions before quoting one.

## If this appears stuck

This guidance runs inside a pipeline phase. If that phase appears stuck, or the harness stops it mid-run: run `mokata resume` (where the run stands and the gate that still applies) and `mokata approve --list` (anything waiting on a human). Nothing is lost — run state, approvals and the audit ledger are on disk — so re-invoke the phase to continue; `mokata doctor` names a hung MCP server or a hook that is not firing.

## Rationalizations — stop if you catch yourself thinking any of these

| Rationalization | Reality |
|---|---|
| "The button is only shown to admins." | The Server Function behind it accepts a direct POST from anyone. Check inside the function. |
| "Middleware already checks the session." | Middleware is path-matched and has been bypassed repeatedly; authorize where the data is touched. |
| "It's just the public API key." | Read what the key can do. If it can write, bill, or read private data, it is a secret, prefix or not. |
| "React escapes everything." | Except `dangerouslySetInnerHTML`, URL attributes and direct DOM writes — exactly the sinks to check. |
| "We'll add the dependency now and review later." | Install scripts run at install time; the review has to come first. |

## Verification — confirm each before you claim this skill is done

Evidence, not "seems right" — check every box or say which is unmet and why:

- [ ] every Server Function / route handler in the diff authenticates AND authorizes the object, inside the function
- [ ] a test sends the forbidden request directly (no UI) and asserts it is refused
- [ ] nothing crossing to the client carries a field the user may not see
- [ ] the production build output was checked for credentials; no secret carries a public prefix
- [ ] every user-influenced HTML/URL sink in the diff is sanitized or replaced with a safe construct

## References — pulled in just-in-time (not loaded inline)

- `references/authorization-first.md` — proving and fixing broken access control on Server Functions and route handlers, with an Incorrect/Correct pair and the direct-POST test
- `references/bundle-secrets-and-sinks.md` — finding secrets in build output and handling the HTML/URL sinks, with Incorrect/Correct pairs

## Contract

**CAN**
- audit the change against the checklist in order, authorization first, and reproduce each finding
- fix the smallest thing on request, then prove the reproduction is now refused
- record an adopted security constraint as a HARD `guardrail` to memory + the ledger (human-gated)

**MUST NOT**
- write a secret into code, config, a client-exposed variable, or egress (gate: secret-guard)
- override a hard security rule the project adopted (gate: hard-rule)
- persist a security decision without the write gate (gate: write-gate)
- claim a bundle-scan or dependency-audit gate passed when none is backed for this project (advisory)

**DEPENDS ON**
- the spec carrying `frontend-security` (routed when `frontend-react` and `security` are both in play) (advisory)
- the production build output for the secrets check — degrades to reading the env config, and says so (advisory)

> Grounding: `(gate: …)` boundaries are enforced by that gate in code; `(advisory)` ones are protocol discipline this skill follows, not a hard block.
