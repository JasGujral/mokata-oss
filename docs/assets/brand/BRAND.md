# mokata brand assets — FINAL (locked July 2026)

The mark: **`mokata` in a pixelized terminal font + blinking green block cursor**. Standard
terminal letterforms (DejaVu Sans Mono) rasterized to a coarse grid so each pixel is a
visible block — old-terminal style. The cursor is the mark: "the agent is ready to type;
you steer." `mo` is bold, `kata` regular — weight, never color, separates it (nod to MoStack).

## Files (the ONLY logo files — use these everywhere)
- `mokata-wordmark-dark.svg` — dark backgrounds (text `#d9e4dc`, cursor `#3df58c`)
- `mokata-wordmark-light.svg` — light backgrounds (text `#0b120e`, cursor `#22c55e`)
- `mokata-icon.svg` — square icon: `mk` + cursor on `#070c09`, radius 48/256.
  Always `mk`, never `m` + cursor alone — that reads as Xiaomi's "mi".

All three are pure `<path>` pixels — no font dependency, render identically everywhere.
The cursor blinks (1.1s hard on/off, honors `prefers-reduced-motion`). Blink ships in every
digital surface; in print/raster exports the cursor is simply solid green.

## How they were made (for regeneration only — do not redraw by hand)
DejaVu Sans Mono (Bold for `mo`/`m`, Book for `kata`/`k`) rendered at 18 px via PIL
`ImageDraw.text` on a 1-bit canvas, each lit pixel emitted as a square: 6 px squares for the
wordmark (470×140 viewBox), 8 px for the icon (256×256). Cursor: wordmark 36×84 @ (414,20);
icon 30×112 @ (202,66).

## Rules (every asset going forward)
1. Always lowercase `mokata`, pixelized — never smooth vector text, never a substitute font.
2. Greens come from the token file (`docs/stylesheets/mokata.css`): `#3df58c` on dark,
   `#22c55e` on light. Never a third green. Green is the ONLY brand accent — no orange/amber
   or other hues (tried and rejected).
3. Cursor is ALWAYS green, always the block. Don't italicize, outline, gradient, or recolor
   anything.
4. EVERY externally-visible surface (README, docs site, landing, PyPI, GitHub, VS Code
   extension, decks, social cards, videos, PDFs, and any future surface) uses THESE files —
   no re-drawn variants, no one-off logos.

## Tagline (one line, everywhere, verbatim)
> **The memory + seatbelt for your AI coding agent.**

Used on: README, docs site (title + description), landing hero, PyPI description, GitHub repo
description, decks, social. The longer descriptor ("Spec-driven TDD for Claude Code —
knowledge-aware, human-gated, local-first.") may FOLLOW the tagline as a subtitle, never
replace it. Don't coin new taglines per surface.
