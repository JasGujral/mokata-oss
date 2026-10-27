"""R5 (0.0.26) — the `<private>` exclusion tag for memory writes.

Text wrapped in `<private>…</private>` is EXCLUDED from what memory stores: it is cut out before
the WriteGate's secret scan and before the human sees the proposal, and replaced by a visible
marker so the stored learning never pretends the excluded text was not there. It sits beside the
secret guard, not instead of it — the guard still hard-blocks a credential that was NOT tagged.

Fail closed: an unbalanced tag (`<private>` with no close, or a stray `</private>`) refuses the
write. Guessing where a private span ends is how a private span gets stored.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import re
from typing import Tuple

from ..errors import MokataError

PRIVATE_MARKER = "[private — not stored]"

_SPAN = re.compile(r"<private>.*?</private>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"</?private>", re.IGNORECASE)


class UnbalancedPrivateTag(MokataError):
    """A `<private>` tag without its pair — the write is refused rather than guessed at. A HARD
    error (D5 taxonomy): there is no floor to fall back to, because "store it anyway" is the leak."""


def strip_private(text: str) -> Tuple[str, int]:
    """(text with every private span replaced by PRIVATE_MARKER, number of spans removed).
    Raises UnbalancedPrivateTag when a tag is left over after the balanced spans are cut."""
    text = text or ""
    out, n = _SPAN.subn(PRIVATE_MARKER, text)
    if _TAG.search(out):
        raise UnbalancedPrivateTag("unbalanced <private> tag — close it with </private>; "
                                   "nothing was written")
    return out, n


__all__ = ["PRIVATE_MARKER", "UnbalancedPrivateTag", "strip_private"]
