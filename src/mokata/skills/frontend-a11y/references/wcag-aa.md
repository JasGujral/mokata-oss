# Frontend UI + accessibility — primary sources (JIT detail)

Pulled in just-in-time when the Frontend-a11y skill is engaged and the heavier detail is needed.
Authored clean-room in mokata's own words; every external claim is anchored to a WCAG / MDN / WAI
primary source below. Where a specific success-criterion number, level, or contrast ratio could not
be verified against the live source at authoring time, it is marked **UNVERIFIED** and must be
confirmed at the cited URL for the version in use before it is relied on.

## The four principles — WCAG POUR

Sources: https://www.w3.org/WAI/standards-guidelines/wcag/ · https://www.w3.org/TR/WCAG22/

The Web Content Accessibility Guidelines organize accessibility under four principles:
- **Perceivable** — information and UI components must be presentable to users in ways they can
  perceive (text alternatives, contrast, non-colour signals, adaptable structure).
- **Operable** — UI components and navigation must be operable (keyboard access, enough time,
  no seizure-inducing content, findable).
- **Understandable** — information and operation must be understandable (readable, predictable,
  input assistance).
- **Robust** — content must be robust enough to be interpreted by a wide range of user agents and
  assistive technologies (valid markup, exposed name/role/value).

Each principle has testable **success criteria** at conformance levels **A**, **AA**, and **AAA**.
mokata targets **AA** as the review bar. The exact criterion numbering and the level of a given
criterion are **UNVERIFIED** here — confirm at the cited WCAG version before quoting a number/level.

## The AA checks this skill runs (each cites its criterion)

Source (guidance + how-to): https://developer.mozilla.org/en-US/docs/Web/Accessibility

### Text alternatives — WCAG 1.1.1
Non-text content that conveys meaning has a text alternative; purely decorative images are marked
decorative (empty `alt`) so assistive technology skips them.

### Keyboard operable + visible focus — WCAG 2.1.1 / 2.4.7
Every interactive control is reachable and usable with the keyboard alone, there is no keyboard
trap, and the focused element has a visible focus indicator.

### Colour contrast + not-colour-alone — WCAG 1.4.3 / 1.4.1
Text has sufficient contrast against its background (the AA bar is commonly cited as 4.5:1 for
normal text and 3:1 for large text), and colour is never the only means of conveying information.
The exact ratios are **UNVERIFIED** — confirm at the cited criterion.

### Name, role, value — WCAG 4.1.2
Every UI control exposes an accessible name, its role, and its current state/value to assistive
technology. Native HTML elements provide these by default.

### Info and relationships — WCAG 1.3.1
Structure conveyed visually (headings, lists, labels, landmarks, a label associated with its field)
is also available programmatically, so it is perceivable non-visually.

## Semantic HTML first; ARIA to fill a gap

Sources: https://www.w3.org/WAI/ARIA/apg/ ·
https://developer.mozilla.org/en-US/docs/Web/Accessibility/ARIA

Prefer a native HTML element over a generic element re-created with ARIA: native elements bring
keyboard behaviour, focus management, and the correct accessible name/role/state for free. The
WAI-ARIA guidance is explicit that native semantics are preferred and that incorrectly-applied ARIA
is worse than none — "no ARIA is better than bad ARIA". Use ARIA only when no native element
expresses the pattern, and then follow the authoring practice for that specific pattern (its exact
required attributes are **UNVERIFIED** until read against the cited APG page).

## How these become remembered conventions in mokata

A design system is the set of accessible conventions a project has adopted — component patterns, the
accessible-name approach, the focus style, the contrast tokens. mokata stores an adopted convention
as a typed `best-practice` memory item through the human-gated write path and records it to the
audit ledger under the `domain` kind, so the develop/review phases surface it for the symbols in
play and the next component follows the same accessible pattern instead of re-introducing the same
a11y defect. The a11y checklist is a **review instrument** run at the AA bar, not a hard gate that
blocks a change.
