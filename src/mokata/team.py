"""Stage 69 — zero-setup team sync (one-command adopt + OPTIONAL managed-Postgres connect).

HONEST SCOPE — mokata runs **NO hosted service**. "Hosted sync" here means the team's OWN
managed Postgres (Supabase / Neon / RDS — just a DSN). This module gives a team two
one-command steps, both reusing existing primitives (nothing rebuilt):

  * `team_adopt(source)` — pull a teammate's governed stack (the shared J3 manifest, the vault
    that travels with the repo, and the shared-memory DSN POINTER) and wire it locally, in ONE
    human-gated + secret-scanned step. The shared content is UNTRUSTED, so it is secret-scanned
    before anything is written (like memory_import / vault pull); idempotent; reversible (an
    audited config write).

  * `team_connect(dsn_env)` — point the shared-memory backend + session transport at the team's
    managed Postgres via an ENV-VAR DSN. It records only the env-var NAME (the pointer); the DSN
    SECRET is NEVER read into the manifest (env-var only). Reuses the memory Postgres backend
    (`config.dsn_env`) and the 55b `PostgresTransport`. Degrade-clean: no `psycopg` / no DSN →
    a clear message and a clean fall back to the local SQLite floor + local session transport.

Local-first (the remote is OPT-IN), human-gated, audited; `psycopg` stays an optional extra.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

from . import MANIFEST_FILENAME, MOKATA_DIR
from .govern.gate import WriteGate, WriteRequest
from .govern.secrets import Finding, scan
from .manifest import Manifest
from .profiles import TOOL_CATALOG

# The conventional shared-Postgres env var (matches session_transport.PG_DSN_ENVS so memory AND
# sessions resolve the SAME managed DB out of the box). A team may pick another NAME.
DEFAULT_DSN_ENV = "MOKATA_PG_DSN"

# Where the team pointer lives in the manifest settings (the env-var NAME + connected flag).
_TEAM_SETTINGS_KEY = "team"


def honest_note() -> str:
    """The one honest sentence reused in output + docs: mokata hosts nothing."""
    return ("mokata does not host anything — 'hosted sync' means your own managed Postgres "
            "(Supabase / Neon / RDS, just a DSN). mokata never stores the DSN secret; only the "
            "env-var name is recorded, and the DSN stays in your environment.")


def driver_present() -> bool:
    """True if the optional `psycopg` driver is importable (degrade-clean otherwise)."""
    import importlib.util
    try:
        return importlib.util.find_spec("psycopg") is not None
    except Exception:
        return False


def _manifest_path(root: str) -> str:
    return os.path.join(root, MOKATA_DIR, MANIFEST_FILENAME)


def _load_data(root: str) -> dict:
    with open(_manifest_path(root), encoding="utf-8") as fh:
        return json.load(fh)


def _gated_write(root: str, new_data: dict, *, assume_yes: bool,
                 confirm: Optional[Callable[[str], bool]], out: Callable[[str], None],
                 ledger: Any, prompt: str):
    """Write `new_data` as the manifest through the universal WriteGate (secret-scan hard-block +
    human gate + audit). Reuses the gate — no second write path. Returns the WriteOutcome."""
    path = _manifest_path(root)
    text = Manifest.from_dict(new_data).to_json()

    def _commit() -> None:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    gate = WriteGate(ledger=ledger)
    return gate.submit(WriteRequest(kind="config", target=path, content=text),
                       commit=_commit, confirm=confirm, assume_yes=assume_yes, prompt=prompt)


# ============================================================================ connect (managed PG)
@dataclass
class ConnectReadiness:
    driver: bool          # psycopg importable
    dsn_set: bool         # the env var is exported (value NEVER read/stored)

    @property
    def active(self) -> bool:
        return self.driver and self.dsn_set


@dataclass
class ConnectResult:
    connected: bool
    changed: bool
    aborted: bool
    dsn_env: str
    readiness: ConnectReadiness
    message: str = ""


def _is_connected_to(data: dict, dsn_env: str) -> bool:
    tool = (data.get("tools") or {}).get("postgres") or {}
    cfg = tool.get("config") or {}
    chain = ((data.get("capabilities") or {}).get("memory_store") or {}).get("fallback") or []
    settings_env = ((data.get("settings") or {}).get(_TEAM_SETTINGS_KEY) or {}).get("dsn_env")
    return (cfg.get("dsn_env") == dsn_env and "postgres" in chain
            and settings_env == dsn_env)


def _readiness(dsn_env: str) -> ConnectReadiness:
    # bool() only — the DSN VALUE is never captured/stored, just probed for presence.
    return ConnectReadiness(driver=driver_present(), dsn_set=bool(os.environ.get(dsn_env)))


def team_connect(root: str, surface: Any, dsn_env: str = DEFAULT_DSN_ENV, *,
                 assume_yes: bool = False,
                 confirm: Optional[Callable[[str], bool]] = None,
                 out: Optional[Callable[[str], None]] = None,
                 ledger: Any = None) -> ConnectResult:
    """Point the shared-memory backend + session transport at the team's managed Postgres via the
    env var `dsn_env`. Records only the env-var NAME; the DSN secret stays in the environment."""
    emit = out or (lambda *_a: None)
    ready = _readiness(dsn_env)
    data = _load_data(root)

    emit(honest_note())

    if _is_connected_to(data, dsn_env):
        msg = f"already connected to your managed Postgres via ${dsn_env} — nothing to change."
        emit(msg)
        return ConnectResult(True, False, False, dsn_env, ready, msg)

    new_data = copy.deepcopy(data)
    # 1) the memory Postgres backend (reuses build_postgres_backend's config.dsn_env contract).
    tool = dict(TOOL_CATALOG["postgres"])
    tool["enabled"] = True
    tool["config"] = {"dsn_env": dsn_env}
    new_data.setdefault("tools", {})["postgres"] = tool
    chain = new_data.setdefault("capabilities", {}).setdefault(
        "memory_store", {"description": "", "layer": "memory", "fallback": []})
    fb = list(chain.get("fallback") or [])
    if "postgres" not in fb:
        chain["fallback"] = ["postgres"] + fb
    # 2) the pointer the session transport + `team status` read (the env-var NAME only).
    new_data.setdefault("settings", {})[_TEAM_SETTINGS_KEY] = {"dsn_env": dsn_env}

    # Readiness messaging — honest about degrade-clean BEFORE we even write.
    if ready.active:
        emit(f"managed Postgres reachable (${dsn_env} set, psycopg installed) — shared memory + "
             f"sessions will use it.")
    else:
        parts = []
        if not ready.driver:
            parts.append("install the driver: `pip install 'mokata[postgres]'`")
        if not ready.dsn_set:
            parts.append(f"export your DSN: `export {dsn_env}=...` (Supabase/Neon/RDS)")
        emit("wiring recorded, but not active yet — " + "; ".join(parts) + ". Until then memory "
             "degrades to the local SQLite floor and sessions to the local transport "
             "(degrade-clean).")

    prompt = (f"mokata · approve wiring shared memory + sessions to your managed Postgres "
              f"(${dsn_env}; the DSN is NEVER stored)?")
    outcome = _gated_write(root, new_data, assume_yes=assume_yes, confirm=confirm,
                           out=emit, ledger=ledger, prompt=prompt)
    if not outcome.committed:
        return ConnectResult(False, False, outcome.aborted, dsn_env, ready,
                             outcome.reason or "not connected (declined)")
    msg = (f"connected — shared memory + sessions point at your managed Postgres via ${dsn_env}. "
           + ("active now." if ready.active else "active once the driver + DSN are present."))
    emit(msg)
    return ConnectResult(True, True, False, dsn_env, ready, msg)


def team_disconnect(root: str, surface: Any, *, assume_yes: bool = False,
                    confirm: Optional[Callable[[str], bool]] = None,
                    out: Optional[Callable[[str], None]] = None,
                    ledger: Any = None) -> ConnectResult:
    """Reverse `team_connect`: drop the managed-Postgres pointer + the memory chain entry, back to
    the local SQLite floor. Gated + audited (reversible by construction)."""
    emit = out or (lambda *_a: None)
    data = _load_data(root)
    dsn_env = (((data.get("settings") or {}).get(_TEAM_SETTINGS_KEY) or {}).get("dsn_env")
               or DEFAULT_DSN_ENV)
    ready = _readiness(dsn_env)

    if not _is_connected_to(data, dsn_env) and "postgres" not in (
            ((data.get("capabilities") or {}).get("memory_store") or {}).get("fallback") or []):
        msg = "not connected to a managed Postgres — nothing to disconnect."
        emit(msg)
        return ConnectResult(False, False, False, dsn_env, ready, msg)

    new_data = copy.deepcopy(data)
    chain = (new_data.get("capabilities") or {}).get("memory_store") or {}
    chain["fallback"] = [t for t in (chain.get("fallback") or []) if t != "postgres"]
    (new_data.get("tools") or {}).pop("postgres", None)
    (new_data.get("settings") or {}).pop(_TEAM_SETTINGS_KEY, None)

    prompt = "mokata · approve disconnecting from the managed Postgres (back to local SQLite)?"
    outcome = _gated_write(root, new_data, assume_yes=assume_yes, confirm=confirm,
                           out=emit, ledger=ledger, prompt=prompt)
    if not outcome.committed:
        return ConnectResult(False, False, outcome.aborted, dsn_env, ready,
                             outcome.reason or "not disconnected (declined)")
    msg = "disconnected — shared memory + sessions are back on the local floor."
    emit(msg)
    return ConnectResult(False, True, False, dsn_env, ready, msg)


def connect_status(surface: Any) -> Optional[str]:
    """The currently-wired team DSN env-var NAME, or None when local-only."""
    data = surface.manifest.data
    if not _is_connected_to(data, (((data.get("settings") or {}).get(_TEAM_SETTINGS_KEY) or {})
                                   .get("dsn_env") or "")):
        # fall back to "postgres in the chain" as a looser signal
        chain = ((data.get("capabilities") or {}).get("memory_store") or {}).get("fallback") or []
        if "postgres" not in chain:
            return None
    return (((data.get("settings") or {}).get(_TEAM_SETTINGS_KEY) or {}).get("dsn_env")
            or DEFAULT_DSN_ENV)


# ================================================================================= adopt (J3 pull)
@dataclass
class AdoptResult:
    adopted: bool
    aborted: bool = False
    blocked: bool = False
    idempotent: bool = False
    wired: List[str] = field(default_factory=list)
    findings: List[Finding] = field(default_factory=list)
    message: str = ""


def resolve_stack_source(source: str) -> Optional[str]:
    """Find the shared-stack file for `source` — a file, or a repo/dir holding one. Returns the
    path, or None when nothing adoptable is present (degrade-clean)."""
    from .share import SHARE_FILENAME
    if os.path.isfile(source):
        return source
    if os.path.isdir(source):
        for rel in (os.path.join(MOKATA_DIR, SHARE_FILENAME), SHARE_FILENAME,
                    os.path.join(MOKATA_DIR, MANIFEST_FILENAME)):
            cand = os.path.join(source, rel)
            if os.path.isfile(cand):
                return cand
    return None


def _wired_pieces(root: str, data: dict) -> List[str]:
    """What the adopt brought in — for a legible, honest report."""
    pieces = ["config"]
    from .vault import vault_dir
    if os.path.isdir(vault_dir(root)):
        pieces.append("vault (travels with the repo)")
    cfg = ((data.get("tools") or {}).get("postgres") or {}).get("config") or {}
    if cfg.get("dsn_env"):
        pieces.append(f"shared-memory pointer (export ${cfg['dsn_env']}; the DSN is never shared)")
    return pieces


def team_adopt(root: str, source: str, *, assume_yes: bool = False,
               confirm: Optional[Callable[[str], bool]] = None,
               out: Optional[Callable[[str], None]] = None,
               ledger: Any = None, force: bool = False) -> AdoptResult:
    """Pull a team's governed stack and wire it locally — UNTRUSTED content, so it is
    secret-scanned BEFORE anything is written, then human-gated. Idempotent + reversible."""
    emit = out or (lambda *_a: None)
    path = resolve_stack_source(source)
    if path is None:
        msg = f"no shared stack found at '{source}' — nothing to adopt."
        emit(msg)
        return AdoptResult(False, message=msg)

    try:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
        data = json.loads(raw)
    except (OSError, ValueError) as exc:
        msg = f"could not read the shared stack '{path}': {exc}"
        emit(msg)
        return AdoptResult(False, message=msg)

    # SECURITY FIRST — the shared stack is untrusted content (like a memory import / vault pull).
    findings = scan(text=raw, path=path)
    if findings:
        emit("team adopt blocked — secret detected in the shared stack (it must not carry a "
             "credential; use an env-var DSN pointer instead):")
        for f in findings:
            emit(f"  [{f.layer}/{f.kind}] {f.detail}")
        return AdoptResult(False, blocked=True, findings=findings,
                           message="blocked: secret in shared content")

    # Idempotent: re-adopting the identical stack changes nothing.
    try:
        current = _load_data(root)
    except OSError:
        current = None
    if current is not None and current == data:
        msg = "already adopted — the shared stack matches your config; nothing to change."
        emit(msg)
        return AdoptResult(True, idempotent=True, wired=_wired_pieces(root, data), message=msg)

    from .share import apply_manifest
    result = apply_manifest(root, data, confirm=confirm, assume_yes=assume_yes, force=force)
    if result.errors:
        emit("team adopt rejected — the shared manifest is invalid:")
        for e in result.errors:
            emit(f"  - {e}")
        return AdoptResult(False, message="rejected: invalid shared manifest")
    if not result.applied:
        emit(f"team adopt: {result.message}")
        return AdoptResult(False, aborted=result.aborted, message=result.message)

    wired = _wired_pieces(root, data)
    emit("team adopt — wired: " + ", ".join(wired) + ".")
    emit(honest_note())
    emit("reversible: this is an audited config write — re-import a prior stack or `mokata team "
         "disconnect` to undo the managed-Postgres pointer.")
    return AdoptResult(True, wired=wired, message="adopted")


# ============================================================== join (Stage 70b — guided onboarding)
# ONE guided command that takes a new teammate from zero to fully wired — shared stack, shared
# memory, the shared vault, the project knowledge — by orchestrating the EXISTING primitives in
# order (team_adopt → team_connect → vault pull → /mokata:onboard → doctor). No new engine: each
# step reuses its own gated/secret-scanned/degrade-clean building block. Every writing step is
# human-gated (decline → that step does nothing, the flow continues); an absent source/backend/
# driver SKIPS its step (never blocks); it is idempotent (re-join converges) and reversible
# (compose `team disconnect` / re-import a prior stack where it wrote). mokata hosts nothing.

# The status vocabulary each step reports (used by the "what you're wired to" summary).
_JOIN_GLYPHS = {"wired": "✓", "verified": "✓", "skipped": "–", "declined": "–",
                "pending": "○", "blocked": "✗", "problems": "⚠"}
# Steps that leave something for the teammate to finish (surfaced as pending/skipped).
_JOIN_OPEN = {"skipped", "declined", "pending", "blocked", "problems"}

JOIN_STEP_NAMES = ("adopt", "connect", "vault", "onboard", "verify")


@dataclass
class JoinStep:
    name: str            # adopt | connect | vault | onboard | verify
    status: str          # wired | verified | skipped | declined | pending | blocked | problems
    detail: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "status": self.status, "detail": self.detail}


@dataclass
class JoinResult:
    steps: List[JoinStep] = field(default_factory=list)
    dsn_env: str = DEFAULT_DSN_ENV
    aborted: bool = False

    def step(self, name: str) -> Optional[JoinStep]:
        for s in self.steps:
            if s.name == name:
                return s
        return None

    @property
    def wired(self) -> List[str]:
        return [s.name for s in self.steps if s.status in ("wired", "verified")]

    @property
    def pending(self) -> List[JoinStep]:
        return [s for s in self.steps if s.status in _JOIN_OPEN]

    def summary(self) -> str:
        """The honest "here's what you're now wired to" recap — every step + what's still open."""
        lines = ["mokata team join — here's what you're now wired to:"]
        for s in self.steps:
            glyph = _JOIN_GLYPHS.get(s.status, "·")
            lines.append(f"  {glyph} {s.name:<8} {s.status:<9} {s.detail}")
        pend = self.pending
        if pend:
            lines.append("still open: " + ", ".join(f"{s.name} ({s.status})" for s in pend))
        else:
            lines.append("every step complete — you're fully wired.")
        lines.append("reverse anything: `mokata team disconnect` (shared memory) or re-import a "
                     "prior stack (config). " + honest_note())
        return "\n".join(lines)


def _join_adopt(root: str, source: Optional[str], *, assume_yes, confirm, out, ledger,
                force) -> JoinStep:
    if not source:
        return JoinStep("adopt", "skipped",
                        "no stack source given — skipped (nothing to adopt).")
    ad = team_adopt(root, source, assume_yes=assume_yes, confirm=confirm, out=out,
                    ledger=ledger, force=force)
    if ad.blocked:
        return JoinStep("adopt", "blocked", "secret in the shared stack — nothing wired.")
    if ad.idempotent:
        return JoinStep("adopt", "wired", "governed stack already in sync — no change.")
    if ad.adopted:
        return JoinStep("adopt", "wired",
                        "governed stack + rules/guardrails + pointers wired.")
    if ad.aborted:
        return JoinStep("adopt", "declined", "adopt declined — nothing wired.")
    return JoinStep("adopt", "skipped", ad.message or "no shared stack found — skipped.")


def _join_connect(root: str, surface: Any, dsn_env: str, *, assume_yes, confirm, out,
                  ledger) -> JoinStep:
    data = _load_data(root)
    ready = _readiness(dsn_env)
    if _is_connected_to(data, dsn_env):
        return JoinStep("connect", "wired",
                        f"shared memory already points at your managed Postgres (${dsn_env}).")
    if not ready.active:
        # No DSN exported and/or no driver → SKIP (stay on the local memory floor), never block.
        missing = []
        if not ready.dsn_set:
            missing.append(f"no ${dsn_env} exported")
        if not ready.driver:
            missing.append("psycopg not installed")
        return JoinStep("connect", "skipped",
                        f"staying on local memory ({'; '.join(missing)}) — run "
                        f"`mokata team connect --dsn-env {dsn_env}` once ready.")
    res = team_connect(root, surface, dsn_env, assume_yes=assume_yes, confirm=confirm,
                       out=out, ledger=ledger)
    if res.connected and (res.changed or not res.aborted):
        return JoinStep("connect", "wired",
                        f"shared memory + sessions point at your managed Postgres (${dsn_env}).")
    if res.aborted:
        return JoinStep("connect", "declined", "connect declined — staying on local memory.")
    return JoinStep("connect", "skipped", res.message or "not connected — staying local.")


def _join_vault(root: str, vault_ref: Optional[str], *, assume_yes, confirm, out,
                ledger: Any = None) -> JoinStep:
    emit = out or (lambda *_a: None)
    if not vault_ref:
        return JoinStep("vault", "skipped",
                        "no --vault ref — skipped (the vault travels with the repo; adopt "
                        "brings it in).")
    from . import vault as V
    try:
        src_index = V.load_index(vault_ref)
    except Exception as exc:                                  # noqa: BLE001 — degrade-clean
        return JoinStep("vault", "skipped",
                        f"'{vault_ref}' is not a readable mokata vault ({exc}) — skipped.")
    entries = (src_index or {}).get("entries") or {}
    if not entries or not os.path.isdir(V.vault_dir(vault_ref)):
        return JoinStep("vault", "skipped",
                        f"no shared vault at '{vault_ref}' — skipped (keeping the repo's vault).")

    # UNTRUSTED content — read + hash-verify (vault_pull) + secret-scan BEFORE any write.
    pulled: List[Any] = []
    texts: List[str] = []
    for name in sorted(entries):
        try:
            content, _entry = V.vault_pull(vault_ref, name)  # read-only; verifies content hash
        except V.VaultError as exc:
            emit(f"  skipped vault entry '{name}' — {exc}")
            continue
        pulled.append((name, content, entries[name]))
        texts.append(content)
    findings = scan(text="\n".join(texts), path=vault_ref) if texts else []
    if findings:
        emit("team join · vault pull BLOCKED — secret detected in the shared vault content "
             "(remove it before sharing):")
        for f in findings:
            emit(f"  [{f.layer}/{f.kind}] {f.detail}")
        return JoinStep("vault", "blocked", "secret in shared vault content — nothing pulled.")
    if not pulled:
        return JoinStep("vault", "skipped", f"no readable artifacts at '{vault_ref}' — skipped.")

    # Idempotent: only pull artifacts whose content differs from what's already local.
    local = V.load_index(root)
    local_entries = local.setdefault("entries", {})
    fresh = [(n, c, r) for (n, c, r) in pulled
             if V.content_hash(c) != (local_entries.get(n) or {}).get("content_hash")]
    if not fresh:
        return JoinStep("vault", "wired",
                        f"vault already in sync ({len(pulled)} artifact(s)) — nothing to pull.")

    prompt = (f"mokata · approve pulling {len(fresh)} vault artifact(s) (shared specs/designs) "
              f"from '{vault_ref}'?")
    if not (assume_yes or (confirm(prompt) if confirm else False)):
        return JoinStep("vault", "declined", "vault pull declined — nothing written.")

    # Route each artifact write through the universal WriteGate so every write is audit-logged
    # (like vault_push / _gated_write). The human already approved the batch above, so the gate
    # runs with assume_yes=True — its secret-scan still hard-blocks (belt-and-suspenders), and it
    # records one `write_gate` ledger entry per artifact.
    os.makedirs(V.vault_dir(root), exist_ok=True)
    gate = WriteGate(ledger=ledger)
    written = 0
    for name, content, rec in fresh:
        path = os.path.join(V.vault_dir(root), f"{name}.md")

        def _commit(_p=path, _c=content) -> None:
            with open(_p, "w", encoding="utf-8") as fh:
                fh.write(_c)

        outcome = gate.submit(
            WriteRequest(kind="config", target=path, content=content, actor="team-join"),
            commit=_commit, assume_yes=True)
        if outcome.committed:
            local_entries[name] = rec
            written += 1
    if not written:
        return JoinStep("vault", "declined", "vault pull blocked at the gate — nothing written.")
    V._save_index(root, local)
    emit(f"team join · pulled {written} vault artifact(s).")
    return JoinStep("vault", "wired",
                    f"pulled {written} shared vault artifact(s) (the specs/designs).")


def _join_onboard(out: Callable[[str], None]) -> JoinStep:
    # /mokata:onboard is a GUIDED, interactive capture — mokata can't complete it non-interactively;
    # it hands the teammate the single next command (honest about the mechanism).
    msg = ("run `/mokata:onboard` to capture the project's rules, guardrails, conventions & "
           "domain context into typed, human-gated memory (guided).")
    out("  " + msg)
    return JoinStep("onboard", "pending", msg)


def _join_verify(root: str, out: Callable[[str], None]) -> JoinStep:
    # Reload a fresh surface so the check reflects what the join just wrote; reuse `mokata doctor`.
    try:
        from .config import Surface
        from .govern import diagnose
        report = diagnose(Surface.load(root))
        out(report.render())
        if report.ok:
            return JoinStep("verify", "verified",
                            "config healthy (mokata doctor: all checks passed).")
        return JoinStep("verify", "problems",
                        f"doctor found {len(report.errors)} problem(s) — run `mokata doctor`.")
    except Exception as exc:                                  # noqa: BLE001 — never block the join
        return JoinStep("verify", "skipped", f"doctor could not run ({exc}).")


def team_join(root: str, surface: Any, source: Optional[str], *,
              dsn_env: str = DEFAULT_DSN_ENV, vault_ref: Optional[str] = None,
              assume_yes: bool = False, confirm: Optional[Callable[[str], bool]] = None,
              out: Optional[Callable[[str], None]] = None,
              ledger: Any = None, force: bool = False) -> JoinResult:
    """Guided team onboarding — the ONE command a new teammate runs. Orchestrates the existing
    primitives in order, each a confirmable step: (1) adopt the governed stack, (2) connect shared
    memory at the team's managed Postgres, (3) pull the shared design/spec vault, (4) onboard the
    project knowledge, (5) verify + summarize. Human-gated per writing step; secret-scanned on the
    untrusted pulls; the DSN secret never stored; degrade-clean (skip-not-block); idempotent +
    reversible. No new engine — pure orchestration."""
    emit = out or (lambda *_a: None)
    result = JoinResult(dsn_env=dsn_env)
    emit("mokata team join — one guided path: adopt stack → connect shared memory → pull vault "
         "→ onboard knowledge → verify. Every writing step is human-gated; shared content is "
         "secret-scanned; your DSN secret is never stored.")
    emit(honest_note())

    emit("team join · step 1/5 — adopt the governed stack:")
    result.steps.append(_join_adopt(root, source, assume_yes=assume_yes, confirm=confirm,
                                    out=emit, ledger=ledger, force=force))

    emit("team join · step 2/5 — connect shared memory (your own managed Postgres):")
    result.steps.append(_join_connect(root, surface, dsn_env, assume_yes=assume_yes,
                                       confirm=confirm, out=emit, ledger=ledger))

    emit("team join · step 3/5 — pull the design/spec vault:")
    result.steps.append(_join_vault(root, vault_ref, assume_yes=assume_yes, confirm=confirm,
                                    out=emit, ledger=ledger))

    emit("team join · step 4/5 — onboard the project knowledge:")
    result.steps.append(_join_onboard(emit))

    emit("team join · step 5/5 — verify (mokata doctor):")
    result.steps.append(_join_verify(root, emit))

    emit(result.summary())
    return result
