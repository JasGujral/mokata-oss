"""The ONE line numbering every line number mokata shows is counted in (0.0.27 P4, ruling R2-3).

The IDE's viewer splits a file on `\\n` and nothing else (`ide/static/nav.js`, `f.text.split("\\n")`);
a CRLF is one break (the `\\r` is dropped from the shown line), and a bare CR, a form feed, a
vertical tab or U+2028 are characters INSIDE a line. Python's own `str.splitlines()` splits on all
of those, and CPython's tokenizer counts a bare CR as a line break, so a file holding one used to
get outline / caller line numbers that pointed at a different viewer line than the one shown.

Every reader that hands a line number to the page goes through here:
  * `viewer_lines(text)` — the viewer's lines (the windowed highlighter, P4's Copy Ref / Copy with
    Context, the grep floor's streaming `refs`);
  * `for_parse(text)` — the text `ast.parse` is given (`knowledge.ast_backend.parse_source`): a bare
    CR becomes a space, so the parser's `lineno` IS the viewer's line. A file whose bare CR was a
    real statement break then fails to parse and gets no outline — said, never a shifted one.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import re
from typing import List

_BARE_CR = re.compile(r"\r(?!\n)")


def viewer_lines(text: str) -> List[str]:
    """The lines the viewer shows: split on `\\n` only, one trailing `\\r` dropped per line, and no
    phantom empty last line after a final newline."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [ln[:-1] if ln.endswith("\r") else ln for ln in lines]


def has_bare_cr(text: str) -> bool:
    """True when a CR not followed by LF is in `text` (the one place the parser and the viewer
    count lines differently)."""
    return "\r" in text and _BARE_CR.search(text) is not None


def for_parse(text: str) -> str:
    """`text` with every bare CR replaced by a space, so `ast` line numbers equal the viewer's."""
    return _BARE_CR.sub(" ", text) if "\r" in text else text


__all__ = ["viewer_lines", "has_bare_cr", "for_parse"]
