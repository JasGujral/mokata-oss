---
name: frontend-a11y
description: mokata · Frontend UI + accessibility — WCAG-AA checked in review, design-system conventions remembered.
when_to_use: Engage when an approach touches a UI component, a view, a template, or a stylesheet, when the spec's `domains` constraint names `frontend-a11y`, or when a change adds/alters user-facing markup, an interactive control, an image, a form field, or a colour/contrast choice. Do NOT engage for a pure backend/data change with no user-facing surface, or for internal tooling output that no assistive technology consumes.
---

> **mokata Agent Skill.** This is mokata's `frontend-a11y` domain knowledge, attached to the
> pipeline so Claude engages it automatically when an approach touches a UI surface. It is NOT a
> parallel advisory: it enriches develop/review, feeds the instrument that already runs there (the
> a11y checklist in review), and records the project's design-system conventions to memory. mokata's
> non-negotiables still hold — durable writes are **human-gated**, and the accessibility check is
> part of review, not an optional afterthought.

⛭ mokata frontend-a11y active — gate: the accessibility checklist is run in review; design-system conventions are recorded to memory

# mokata · Frontend UI + accessibility

Accessibility is not a coat of paint applied at the end — it is whether the interface *works* for
people using a keyboard, a screen reader, magnification, or a non-default colour scheme. The
standard for "works" is WCAG, and the practical bar most projects target is **level AA**. This skill
makes mokata treat UI work as accessibility work: build to the WCAG principles as you develop, run
the a11y checklist in review at the AA bar, and record the project's design-system conventions so
the next component is consistent with the last.

## WCAG: build to the four principles (POUR)
The Web Content Accessibility Guidelines organize accessibility under four principles — content must
be **Perceivable, Operable, Understandable, and Robust** (POUR) — each with testable success
criteria at conformance levels A, AA, and AAA (https://www.w3.org/WAI/standards-guidelines/wcag/ ·
https://www.w3.org/TR/WCAG22/). mokata targets **AA** as the review bar: A is a floor, AAA is
aspirational and not always achievable for all content. The exact success-criterion numbering and
the AA membership of a given criterion are **UNVERIFIED** here — confirm against the WCAG version in
use at the cited URL before quoting a criterion number or level.

## The AA checks that catch the most (grounded in WCAG + MDN)
Run these in review; each maps to a WCAG success criterion (read the cited source for the exact
current wording and threshold):
- **Text alternatives** — every non-text content that conveys meaning has a text alternative
  (meaningful `alt` on images; empty `alt` for decorative). (WCAG 1.1.1 · MDN:
  https://developer.mozilla.org/en-US/docs/Web/Accessibility)
- **Keyboard operable** — every interactive control is reachable and usable with the keyboard alone,
  with a visible focus indicator and no keyboard trap. (WCAG 2.1.1 / 2.4.7)
- **Colour contrast** — text has sufficient contrast against its background (the AA bar is commonly
  cited as 4.5:1 for normal text, 3:1 for large text); colour is never the *only* way information is
  conveyed. (WCAG 1.4.3 / 1.4.1) The exact ratios are **UNVERIFIED** — confirm at the WCAG source.
- **Name, role, value** — every UI control exposes an accessible name, its role, and its state to
  assistive technology (WCAG 4.1.2), which native HTML elements provide for free.
- **Structure & relationships** — headings, lists, labels, and landmarks convey the real structure
  so it is available non-visually (WCAG 1.3.1); a `<label>` is associated with its field.

## Semantic HTML first, ARIA only to fill a gap
Prefer a native HTML element (`<button>`, `<a href>`, `<input>`, `<nav>`) over a `<div>` re-created
with ARIA: native elements come with keyboard behaviour, focus, and the accessible name/role/state
already correct. The WAI-ARIA guidance is explicit that native semantics are preferred and that
poorly-applied ARIA is worse than none — no ARIA is better than bad ARIA
(https://www.w3.org/WAI/ARIA/apg/ · https://developer.mozilla.org/en-US/docs/Web/Accessibility/ARIA).
Reach for ARIA only when no native element expresses the pattern, and then follow the authoring
practice for that pattern. Treat any specific ARIA pattern's exact required attributes as
**UNVERIFIED** until read against the cited APG page.

## Design-system conventions: decide once, remember, reuse
A design system is a set of adopted conventions — the component patterns, the accessible-name
approach, the focus style, the contrast tokens the project has standardized on. Record them, so the
next component follows the same accessible pattern instead of re-litigating it (and re-introducing
the same a11y bug). mokata's edge over a static style guide is that these conventions are stored as
typed memory the develop/review phases surface for the symbols in play — a remembered convention, not
a wiki page nobody re-reads.

## Attachment to the flow (what mokata does that a doc cannot)
- **brainstorm** — when the chosen approach's graph surface touches components/views/templates/
  stylesheets, the domain classifier puts `frontend-a11y` in the spec's `domains`, so accessibility
  is a first-class, human-approved constraint on the UI work, not a later bolt-on.
- **develop** — JIT-pull the project's recorded design-system conventions for the symbols in play,
  and build the component to the WCAG principles (semantic HTML first, keyboard operable, labelled).
- **review** — the **a11y checklist runs at the WCAG-AA bar**: text alternatives, keyboard operation
  + visible focus, contrast, name/role/value, and structure are checked before the change lands.
- **memory** — a design-system convention adopted (a focus style, an accessible-name pattern, a
  contrast token) is recorded as a typed `best-practice` entry through the human gate + the ledger,
  so it is reused (P7). Record it with mokata's domain-decision path — never as loose prose.

## Gate (instrument)
This skill feeds the **a11y checklist** (an SK.S1/S2 review instrument, not a hard gate): the
accessibility checks are run in review at the AA bar before a UI change lands. It is advisory — it
informs the change and records conventions, it does not block it — but the *durable write* recording
a design-system convention is human-gated (write-gate), and any change to an approved spec's UI
scope routes through the deviation gate.

## Grounding discipline
Decide from the standard and the rendered markup, not from assumption. Before asserting a control is
keyboard-operable, an image is decorative, a contrast ratio passes, or a role is exposed, VERIFY it:
read the markup, check the accessible name/role/state, and for a WCAG claim read the cited success
criterion for the version in use and CITE the URL. Prefer the primary source (WCAG / MDN / the ARIA
authoring practices) over memory or a blog. Flag anything you could not verify as **UNVERIFIED**
rather than stating it as fact.

## Rationalizations — stop if you catch yourself thinking any of these

| Excuse | Reality |
|---|---|
| "It looks fine on my screen." | Looks-fine is not tested; run the AA checks — keyboard, focus, contrast, name/role — before you call it accessible. |
| "I'll add a `<div onclick>` — it's quicker than a `<button>`." | A div is not keyboard-operable or announced; use the native element so name/role/keyboard come for free (semantic HTML first). |
| "I'll sprinkle some ARIA roles to make it accessible." | Bad ARIA is worse than none; prefer native semantics and only add ARIA to fill a real gap, per the APG. |
| "Colour alone marks the error state." | Colour must not be the only signal (WCAG 1.4.1) and needs sufficient contrast (1.4.3) — add a non-colour cue. |
| "Accessibility is a separate pass later." | The a11y checklist is part of review at the AA bar; deferring it ships an inaccessible control and a convention nobody recorded. |

## Verification — confirm each before you claim this skill is done

Evidence, not "seems right" — check every box or say which is unmet and why:

- [ ] every meaningful non-text element has a text alternative; decorative ones are marked decorative
- [ ] every interactive control is keyboard-operable with a visible focus indicator and no trap
- [ ] text contrast meets the AA bar and no information is conveyed by colour alone (criterion cited)
- [ ] every control exposes an accessible name, role, and state (native element preferred over ARIA)
- [ ] the design-system convention adopted was recorded to memory + the ledger (human-gated), not left as prose

## References — pulled in just-in-time (not loaded inline)

- `references/wcag-aa.md` — the WCAG POUR principles, the AA success criteria this skill checks, and the semantic-HTML-first / ARIA guidance in full, each with its primary-source URL

## Contract

**CAN**
- classify the UI surface and build to the WCAG principles (semantic HTML first, keyboard, labelled)
- run the a11y checklist at the WCAG-AA bar in review before a UI change lands
- record adopted design-system conventions as typed `best-practice` decisions to memory + the ledger (human-gated)

**MUST NOT**
- persist a design-system convention without the write gate (gate: write-gate)
- change an approved spec's UI scope beyond approval (gate: deviation)
- land a UI change without running the AA a11y checks, or state an unverified WCAG claim as fact (advisory)

**DEPENDS ON**
- the spec carrying the `frontend-a11y` domain (classified at brainstorm; amend-in if reached late) (advisory)
- the rendered markup + the project's recorded conventions — degrades to reading the markup, and says so (advisory)

> Grounding: `(gate: …)` boundaries are enforced by that gate in code; `(advisory)` ones are protocol discipline this skill follows, not a hard block.
