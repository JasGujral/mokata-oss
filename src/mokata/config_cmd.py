"""`mokata config get/set` — read and update the committed manifest (Stage 24A).

So users set backend parameters (a custom SQLite path, an Obsidian vault, a Postgres
`dsn_env`) without hand-editing JSON. `get` is read-only. `set` is **human-gated** (P2):
it previews the old→new change, runs it through the one `WriteGate` — which **secret-scans
the whole resulting manifest** (an inline DSN/credential is a hard block, since the
manifest is committed) — then writes only on explicit approval. The new manifest is
schema-validated before the gate so a bad edit is refused, not committed.
"""

from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Tuple

from . import MANIFEST_FILENAME, MOKATA_DIR
from . import schema
from .govern.gate import WriteGate, WriteRequest
from .govern.secrets import Finding, scan
from .manifest import Manifest, ManifestError

_MISSING = object()


class ConfigCommandError(Exception):
    """Raised when the manifest can't be read for a config operation."""


def _manifest_path(root: str) -> str:
    return os.path.join(root, MOKATA_DIR, MANIFEST_FILENAME)


def _load_data(root: str) -> dict:
    path = _manifest_path(root)
    if not os.path.exists(path):
        raise ConfigCommandError(
            f"mokata is not initialized in '{os.path.abspath(root)}' "
            f"(no {MOKATA_DIR}/{MANIFEST_FILENAME}). Run `mokata init` first."
        )
    try:
        Manifest.load(path)  # fail loud on a structurally broken manifest
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (ManifestError, json.JSONDecodeError, OSError) as exc:
        raise ConfigCommandError(str(exc)) from exc


def _split(key: str) -> List[str]:
    parts = [p for p in key.split(".") if p]
    if not parts:
        raise ConfigCommandError("config key must be a non-empty dotted path")
    return parts


def _get(data: Any, key: str, default: Any = None) -> Any:
    cur = data
    for part in _split(key):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur


def _set(data: dict, key: str, value: Any) -> None:
    parts = _split(key)
    cur = data
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


def coerce(raw: str) -> Any:
    """Best-effort scalar typing: JSON (true/false/numbers/objects) when it parses,
    otherwise the literal string (so a filesystem path stays a path)."""
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return raw


# ------------------------------------------------------------------------------- get
def config_get(root: str, key: str) -> Tuple[bool, Any]:
    """Return (found, value) for a dotted key in the committed manifest."""
    data = _load_data(root)
    val = _get(data, key, _MISSING)
    return (False, None) if val is _MISSING else (True, val)


# ------------------------------------------------------------------------------- set
@dataclass
class ConfigSetResult:
    committed: bool
    aborted: bool
    message: str
    key: str = ""
    old: Any = None
    new: Any = None
    findings: List[Finding] = field(default_factory=list)


def config_set(
    root: str,
    key: str,
    raw_value: str,
    *,
    assume_yes: bool = False,
    confirm: Optional[Callable[[str], bool]] = None,
    out: Optional[Callable[[str], None]] = None,
    ledger: Any = None,
) -> ConfigSetResult:
    """Preview + human-gate a single dotted-key edit to the manifest, then write it."""
    emit = out or print
    data = _load_data(root)
    value = coerce(raw_value)

    old = _get(data, key, _MISSING)
    proposed = copy.deepcopy(data)
    _set(proposed, key, value)
    new_text = json.dumps(proposed, indent=2, sort_keys=False) + "\n"
    path = _manifest_path(root)
    _old = None if old is _MISSING else old

    # Security FIRST: a secret in the committed manifest (e.g. an inline DSN) is a hard
    # block, ahead of structural checks — it must be caught even if the edit is also
    # structurally invalid. Use an env-var reference (config.dsn_env) instead.
    findings = scan(text=new_text, path=path)
    if findings:
        emit("mokata config set blocked — secret detected in the manifest "
             "(reference an env var instead, e.g. config.dsn_env):")
        for f in findings:
            emit(f"  [{f.layer}/{f.kind}] {f.detail}")
        return ConfigSetResult(False, True, "blocked: secret detected", key=key,
                               old=_old, new=value, findings=findings)

    # Then refuse a structurally bad edit before gating.
    errors = schema.validate_manifest(proposed)
    if errors:
        msg = "config set rejected — the change would make the manifest invalid:\n  - " \
            + "\n  - ".join(errors)
        emit(msg)
        return ConfigSetResult(False, True, msg, key=key, old=_old, new=value)

    shown_old = "(unset)" if old is _MISSING else json.dumps(old)
    emit(f"mokata config set {key}: {shown_old} -> {json.dumps(value)}")

    def _commit() -> None:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(new_text)

    gate = WriteGate(ledger=ledger)
    # The gate secret-scans the whole new manifest: an inline DSN/credential is blocked.
    outcome = gate.submit(
        WriteRequest(kind="config", target=path, content=new_text),
        commit=_commit, confirm=confirm, assume_yes=assume_yes)

    return ConfigSetResult(
        committed=outcome.committed,
        aborted=outcome.aborted,
        message=outcome.reason,
        key=key,
        old=_old,
        new=value,
        findings=outcome.findings,
    )
