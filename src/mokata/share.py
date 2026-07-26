"""J3 — shareable stack manifests.

Publish/share a governed stack so a team adopts it in one command: `export_manifest`
writes the current manifest as a shareable artifact; `apply_manifest` validates an
imported manifest (rejecting an invalid one) and writes it as the project's config —
a durable, human-gated write (overwriting an existing config requires `force`).

SI.6b — BOTH DIRECTIONS OF THE TRUST BOUNDARY ARE SCANNED, and the scan lives HERE, in the seam
every surface funnels through (CLI `export`/`import`, MCP `export_stack`/`import_stack`/
`stacks_install`, `team adopt`/`join`, `stacks install`). It is deliberately not at each caller:
before this stage two callers scanned (`team_adopt`, `install_stack`) and three did not — including
MCP `import_stack`, which WAS wrapped in `_gated_write` but fed it `content=""`, so the gate's
secret-scan ran over an empty string. Gated, ledgered, and blind. A seam defends the callers that
forgot; a convention only defends the ones that remembered.

The two directions are NOT symmetric, and that is the design:

  EXPORT (egress, P23) drops the offending KEY and ships the rest. It is YOUR config and you asked to
  publish it, so the useful answer is "published everything clean, and here is the key you must fix".
  A hit is named by KEY, never by value — a refusal must not print the credential it is refusing.

  IMPORT (ingress, P15) REFUSES THE WHOLE FILE. A manifest is ONE atomic config document, not a bag
  of independent items: memory-share is a LIST, where dropping a poisoned item leaves N others
  individually meaningful, but a manifest's keys interlock and dropping one silently produces a
  config nobody reviewed and nobody published (P2 — the human gated THAT file, not a mokata-mutated
  derivative of it). And a stack file that ships a credential is evidence about its PROVENANCE:
  sanitizing it and applying the rest tells the user the source is fine when it is not. Refusing also
  keeps the seam agreeing with the two callers that already refuse whole (`team_adopt`,
  `install_stack`) rather than contradicting them.
"""

from __future__ import annotations

from .prompt import read_yes_no

import copy
import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, List, Optional, Tuple

from . import MANIFEST_FILENAME, MOKATA_DIR, schema
from .atomicfile import atomic_write_text
from .manifest import Manifest
from .errors import MokataError

SHARE_FILENAME = "mokata-stack.json"


class ManifestShareError(MokataError):
    """Raised when a stack manifest cannot be shared at all (a secret sits in a required key)."""


def validate_shared(data: Any) -> List[str]:
    return schema.validate_manifest(data)


# The provenance every incoming stack file carries. There is no second value: a shared/community
# stack is untrusted by construction — that is what makes the boundary scan below non-negotiable.
UNTRUSTED = "untrusted"


# ------------------------------------------------------------------------------- the scan seam
def scan_stack_value(key: str, value: Any, for_send: bool = False) -> list:
    """SI.6b — the secret-scan of ONE manifest leaf, in either direction.

    THE SAME scanner as C2's `scan_export_item` (`govern.secrets.scan`), with the same `for_send`
    dial: an export is EGRESS (the artifact is written to be COMMITTED and handed to a teammate), so
    it is held to the outbound bar; an import is at-rest content arriving from outside, held to the
    at-rest bar exactly as `team_adopt` and `install_stack` already hold it. One scanner, two
    callers — not a second implementation.

    The key is scanned ALONGSIDE the value (the H4 rule: a secret pasted into a NAME must not slip
    a gate that only reads values). It is deliberately NOT passed as `path=`: the path layer matches
    on a *filename*, and a dotted manifest key like `settings.deploy.key` ends in `.key`, which is in
    `_SENSITIVE_SUFFIXES` — passing it there would flag an honest stack as poisoned on the strength
    of its key's spelling alone."""
    from .govern.secrets import scan
    return scan(text=f"{key}\n{value}", for_send=for_send)


def _leaves(data: Any, prefix: str = "") -> Iterator[Tuple[str, Any]]:
    """Every scalar leaf of the manifest, as (dotted-key, value). A manifest nests arbitrarily
    (`tools.<id>.config.*`, the open-ended `settings.*`), so the scan walks it rather than checking
    a hand-maintained list of "the fields where a secret could sit" — a list that would be wrong the
    first time someone adds a field."""
    if isinstance(data, dict):
        for k, v in data.items():
            yield from _leaves(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(data, list):
        for i, v in enumerate(data):
            yield from _leaves(v, f"{prefix}[{i}]")
    else:
        yield prefix, data


def blocked_keys(data: Any, for_send: bool = False) -> List[str]:
    """The dotted keys whose value carries a secret. Named by KEY only — the caller reports these to
    a human, and a refusal must never print the credential it is refusing (P23)."""
    return [key for key, value in _leaves(data)
            if value not in (None, True, False) and scan_stack_value(key, value, for_send=for_send)]


def _without(data: Any, keys: List[str]) -> dict:
    """A deep copy of the manifest with each dotted key REMOVED (export-side redaction)."""
    out = copy.deepcopy(data)
    for key in keys:
        parts, node = key.split("."), out
        for part in parts[:-1]:
            node = node.get(part) if isinstance(node, dict) else None
            if node is None:
                break
        if isinstance(node, dict):
            node.pop(parts[-1], None)
    return out


# ----------------------------------------------------------------------------------- export
@dataclass
class ExportPlan:
    """What an export WOULD write, computed with no side effects (doc 85 §3: `plan_*` → `*Plan`).
    The CLI/MCP surfaces plan first, hand `payload()` to the WriteGate as the exact bytes it hashes
    and scans, then commit — so the ledger records what actually left, not a summary of it."""
    data: dict                                          # the manifest MINUS every poisoned key
    blocked: List[str] = field(default_factory=list)    # the keys dropped, by NAME (never by value)
    refused: bool = False                               # redaction left an unshareable manifest
    message: str = ""

    def payload(self) -> str:
        return Manifest.from_dict(self.data).to_json()

    def render(self) -> str:
        if self.refused:
            return f"stack export refused: {self.message}"
        if not self.blocked:
            return ""
        return (f"{len(self.blocked)} key(s) BLOCKED (secret detected — NOT exported): "
                f"{', '.join(self.blocked)}")


def plan_export(surface: Any) -> ExportPlan:
    """Egress-scan the current manifest and drop every key whose value carries a secret. Read-only:
    it never touches the surface or the disk."""
    data = surface.manifest.data
    blocked = blocked_keys(data, for_send=True)
    if not blocked:
        return ExportPlan(data=data)

    redacted = _without(data, blocked)
    # Pathological, but it must not write a corrupt artifact: if the secret sat in a STRUCTURALLY
    # REQUIRED key, removing it leaves something that is no longer a manifest. Refuse the export
    # rather than emit a file that cannot be imported (and rather than raise out of `Manifest`).
    errors = validate_shared(redacted)
    if errors:
        return ExportPlan(data=redacted, blocked=blocked, refused=True,
                          message=(f"a secret sits in a required key ({', '.join(blocked)}) — "
                                   f"removing it leaves an invalid manifest. Fix the key, then "
                                   f"re-export."))
    return ExportPlan(data=redacted, blocked=blocked)


def export_manifest(surface: Any, dest: Optional[str] = None) -> dict:
    """Return the current manifest as shareable data; optionally write it to `dest`.

    SI.6b: every value is egress-scanned FIRST and a hit DROPS that key — it is left out of the
    artifact entirely, exactly as C2 drops a secret-bearing memory item out of a memory export. The
    returned dict is the REDACTED manifest (a clean stack is returned untouched and writes
    byte-identical, so nothing else changes); the blocked keys ride `plan_export`, which the CLI and
    MCP surfaces call to report them and to feed the WriteGate the exact bytes that leave.

    The file write itself stays here and is gated by BOTH callers (cli `WriteGate` kind=send /
    mcp `_gated_write`) — the same shape as `memory/share.py:export_memory`."""
    plan = plan_export(surface)
    if dest is not None:
        if plan.refused:
            raise ManifestShareError(plan.message)
        parent = os.path.dirname(dest)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(plan.payload())
    return plan.data


def load_shared(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# ----------------------------------------------------------------------------------- import
@dataclass
class ApplyResult:
    applied: bool
    aborted: bool = False
    errors: List[str] = field(default_factory=list)
    path: Optional[str] = None
    message: str = ""
    # SI.6b
    blocked: List[str] = field(default_factory=list)   # keys carrying a secret — the file is REFUSED
    provenance: str = UNTRUSTED                        # where this content came from (P15)


def _default_confirm(text: str) -> bool:
    return read_yes_no(text, "Apply this shared stack?")


def apply_manifest(root: str, data: Any,
                   confirm: Optional[Callable[[str], bool]] = None,
                   assume_yes: bool = False, force: bool = False,
                   ledger: Any = None) -> ApplyResult:
    """Validate then apply a shared manifest as this repo's config (human-gated).

    SI.6b — the incoming file is UNTRUSTED input (P15), so it is secret-scanned AT THE BOUNDARY:
    before validation, before the existing-config check, before the human gate, and before any byte
    is written. A hit REFUSES THE WHOLE FILE (see the module docstring for why this is not the
    export's per-key drop) — named by key, never by value.

    The scan runs BEFORE validation deliberately: a malformed file that ALSO carries a credential is
    reported as POISONED rather than merely as invalid, because the credential is the sharper fact
    about where the file came from.

    The durable write now goes through the universal WriteGate (kind `config`). Consent is UNCHANGED:
    the gate is handed the same `confirm`/`assume_yes` and the same question this function has always
    asked, so the human sees exactly one prompt with the same text as before. What the gate ADDS is
    the scan of the bytes that land and the `write_gate` ledger record — the two things that were
    missing. It ledgers only when a `ledger` is supplied, so the MCP twins (already inside
    `_gated_write`, and passing none) record their decision exactly once."""
    mdir = os.path.join(root, MOKATA_DIR)
    manifest_path = os.path.join(mdir, MANIFEST_FILENAME)

    # SECURITY FIRST — the boundary scan. An `assume_yes` does not lift it and no approval overrides
    # it: a secret is a security block, not a methodology gate (P2/I1).
    blocked = blocked_keys(data, for_send=False)
    if blocked:
        if ledger is not None:
            # A refused write is a decision like any other, and belongs on the audit trail (I3) — in
            # the same `write_gate` shape the gate itself records, so one query finds every blocked
            # write regardless of whether the gate or the boundary scan caught it. Named by KEY: the
            # ledger must not become the place the credential finally gets written down (P23).
            ledger.record("write_gate", write_kind="config", target=manifest_path, actor="human",
                          decision="blocked",
                          reason=f"secret detected in an untrusted shared stack: {', '.join(blocked)}")
        return ApplyResult(
            applied=False, blocked=blocked, provenance=UNTRUSTED,
            message=("refused: this shared stack carries a secret in "
                     f"{', '.join(blocked)} — a stack must carry an env-var pointer, never a "
                     f"credential. Nothing was applied."))

    errors = validate_shared(data)
    if errors:
        return ApplyResult(applied=False, errors=errors, provenance=UNTRUSTED,
                           message="rejected: shared manifest is invalid")

    if os.path.exists(manifest_path) and not force:
        return ApplyResult(applied=False, aborted=True, path=manifest_path, provenance=UNTRUSTED,
                           message="a manifest already exists; re-run with force "
                                   "to overwrite")

    from .govern import WriteGate, WriteRequest
    content = Manifest.from_dict(data).to_json()

    def _commit() -> None:
        os.makedirs(mdir, exist_ok=True)
        atomic_write_text(manifest_path, content)   # R-MAN — crash leaves the OLD manifest, whole

    outcome = WriteGate(ledger=ledger).submit(
        WriteRequest("config", manifest_path, content=content, actor="human",
                     tool="stack_import"),
        commit=_commit, confirm=confirm or _default_confirm, assume_yes=assume_yes,
        prompt=f"apply shared stack to {manifest_path}?")   # the SAME question as before SI.6b

    if not outcome.committed:
        # `blocked` stays empty here by construction: the boundary scan above already refused every
        # secret-bearing file, so the only way to reach this branch is a human declining.
        return ApplyResult(applied=False, aborted=True, path=manifest_path, provenance=UNTRUSTED,
                           message=outcome.reason if outcome.findings else "aborted by user")
    return ApplyResult(applied=True, path=manifest_path, provenance=UNTRUSTED, message="applied")
