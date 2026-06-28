"""I4 (wiring) — the outbound/publish chokepoint that invokes the lethal-trifecta gate.

mokata is local-first, so its one genuinely *outbound* action is publishing an artifact
for others to pull (the team vault, a shared stack). When such a publish carries PRIVATE
data, the lethal trifecta is live (system access + private data + an exfil/outbound path)
and the action is human-gated + audited via `TrifectaGuard`. Clean content is not gated,
so normal publishing is unaffected (degrade-clean). Dependency-free.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Optional

from .secrets import scan
from .trifecta import OutboundRequest, TrifectaGuard, TrifectaState

# Markers that signal genuinely private/sensitive content (deliberately conservative — bare
# words like "private"/"secret" appear in ordinary prose, so they are NOT triggers).
_PRIVATE_MARKERS = re.compile(
    r"(?i)\b(confidential|proprietary|do not (?:share|distribute)|"
    r"internal[- ]only|for internal use|classified|nda)\b")


def looks_private(text: str) -> bool:
    """True when content carries private/sensitive data worth gating before it goes out:
    an explicit confidentiality marker, or anything the secret scanner flags."""
    if not text:
        return False
    if _PRIVATE_MARKERS.search(text):
        return True
    return bool(scan(text=text))


def gate_outbound_publish(request: OutboundRequest, *, private_data: bool,
                          ledger: Any = None,
                          confirm: Optional[Callable[[str], bool]] = None,
                          assume_yes: bool = False, system_access: bool = True,
                          outbound: bool = True):
    """Run an outbound publish through the lethal-trifecta gate. The trifecta is active
    only when system access + private data + an outbound path all hold; when it is, the
    publish is human-gated (confirm / assume_yes) and logged. Otherwise it is allowed and
    logged ungated. Returns the `OutboundDecision`."""
    state = TrifectaState(system_access=system_access, private_data=private_data,
                          outbound=outbound)
    return TrifectaGuard(ledger=ledger).gate_outbound(
        request, state, confirm=confirm, assume_yes=assume_yes)
