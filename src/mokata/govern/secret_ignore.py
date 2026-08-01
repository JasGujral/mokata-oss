"""SECRET-IGNORE — a road out of a secret-scan FALSE POSITIVE, without moving the floor.

`secret-guard` is documented non-overridable (G4/I1), and for a RECOGNISED credential it stays
exactly that. This module changes one thing, deliberately: the entropy backstop is a GUESS, and
a guess with no appeal is a total wall. SECRET-VALUE-SCAN cut corpus false positives 13->4 and
MEASURED two residual classes as surviving, so "the predicate will eventually be perfect" is not
a plan — 0.0.15 measured users abandoning mokata when it guessed wrong at them.

────────────────────────────────────────────────────────────────────────────────────────────
THE THREAT MODEL. Written first, because the mechanism is designed to it.

  DEFENDS AGAINST: a false positive from the entropy GUESS costing a user their day.
  That is the whole of it.

  DOES NOT DEFEND AGAINST: a determined user who wants to commit a credential. They own the
  repo, the file, and the commit — they can uninstall the hook, drop mokata, or `git commit
  --no-verify`. No allowlist design changes that, and a comment here claiming otherwise would
  be worse than no comment at all.

  SO THE SAFETY DOES NOT REST ON SECRECY OR ON TAMPER-PROOFING. It rests on three things:

    1. ONLY THE GUESS IS NEGOTIABLE. `_SIGNATURES` and the `_KNOWN_SHAPES` floor never consult
       this module. A forged store — see `TestAdversarialLaundering.test_route_6` — still
       cannot launder an AWS/GitHub/Slack/GCP/Stripe/PEM/JWT/DSN credential, because those
       findings are not ignorable at any point in the pipeline.
    2. THE NARROWEST POSSIBLE UNIT. hash(token) + path: this exact string, in this one file.
       A different string, or the same string one file over, still blocks.
    3. REVIEWABILITY. The store is VERSION-CONTROLLED, so an ignore lands in a PR diff where a
       human sees that someone suppressed a secret finding, and the reason they gave. This —
       not the checksum — is the property that makes the reversal safe.

  THE ACCEPTED RESIDUAL, named rather than denied: an UNKNOWN-VENDOR credential (no signature,
  no known shape) is caught only by the guess, so it CAN be ignored. It is contained rather
  than prevented — one exact string, one exact file, listed by `mokata secret ignores`,
  ledgered on add and remove, counted by `mokata doctor`, and visible in the diff.

  THE CHECKSUM IS A SPEED BUMP AND AN AUDIT TRAIL, NOT A SECURITY BOUNDARY. Anyone can
  recompute it — it is `sha256` over the entry list with no key, and it must be recomputable
  or the CLI could not write the file either. Its job is to convert a silent hand-edit into a
  loud "re-add it via the CLI", so that the CLI stays the only entry (and therefore the only
  place the signature/known-shape refusal runs). It is stated this way here, in the refusal
  text, and in the tests, precisely because a comment that read like a guarantee would be the
  dangerous thing.
────────────────────────────────────────────────────────────────────────────────────────────

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, List, Optional, Tuple

from .. import MOKATA_DIR
from ..atomicfile import atomic_write_text, lock_path_for
from ..errors import MokataError
from ..oslock import file_lock
from .secrets import Finding, _matches_known_shape, _scan_entropy, _scan_signatures

# The committed .mokata/ ROOT, never `temp_local/` — see (e) above. `temp_local/` is what the
# init-written `.mokata/.gitignore` excludes, so a file placed there would be exactly the
# local, invisible suppression list this must not be.
IGNORES_FILENAME = "secret-ignores.json"
STORE_VERSION = 1

# The ONE ignorable finding kind. NOT "the entropy layer": the known-shape FLOOR also reports on
# layer `entropy` (`Finding("entropy", "known-secret-shape", …)`), and it is part of the floor,
# not the guess. Keying on layer alone would have handed every documented credential shape an
# appeal — the single most likely way to get this stage wrong.
IGNORABLE_KINDS = ("high-entropy-token",)

# The ledger kind (I3 — the existing audit ledger, never a second log).
LEDGER_KIND = "secret_ignore"

# `inert()` re-scans a target to see whether its entry still matches anything. That is a listing/
# doctor-path convenience, never the hot scan path, so it is bounded rather than clever: a file
# past this size reports nothing instead of costing a human's `doctor` run.
_INERT_SCAN_MAX_BYTES = 2_000_000

_HAND_EDIT_REFUSAL = (
    "`.mokata/{name}` was edited outside mokata (its integrity checksum does not match), so "
    "NO ignore in it is being honoured — every finding blocks again. Re-add it via the CLI: "
    "`mokata secret ignore --token '<the flagged string>' --file <path> --reason '<why>'`. "
    "The CLI is the only entry because it is where a recognised credential shape is refused; "
    "the checksum is a speed bump and an audit trail, not a security boundary — anyone can "
    "recompute it."
)


class IgnoreError(MokataError):
    """A refused ignore request (bad path, blank reason, nothing to ignore).

    D5 — a HARD error, and so is every subclass below: nothing degrades to a floor. The request
    simply does not happen and the CLI exits non-zero."""


class TamperedIgnoreFile(IgnoreError):
    """The store's checksum does not match its entries."""


class NotIgnorable(IgnoreError):
    """The token IS a recognised credential shape — the non-negotiable layers."""

    def __init__(self, shape: str, message: str) -> None:
        super().__init__(message)
        self.shape = shape


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def token_key(token: str) -> str:
    """The stored key for `token` — sha256 hex, and the ONLY thing about the token that is ever
    written down. Not a secure commitment (a low-entropy candidate is guessable by brute force),
    and it does not need to be: the point is that the flagged string does not get persisted into
    a committed file by the very machinery that exists to keep it out of one."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def is_ignorable(finding: Finding) -> bool:
    """Only the entropy GUESS is negotiable — see `IGNORABLE_KINDS`."""
    return finding.layer == "entropy" and finding.kind in IGNORABLE_KINDS


def classify(token: str) -> Optional[str]:
    """The NAME of the non-negotiable shape `token` is, or None if it is only ever a guess.

    Runs the REAL signature layer and the REAL known-shape floor over the token — never a
    second, drifting copy of either. This is why the CLI must be handed the literal: a hash
    cannot be classified, so a hash-only entry point would be a blind hash-adder, which is the
    laundering route this design exists to close."""
    for f in _scan_signatures(token):
        return f.kind
    return _matches_known_shape(token)


def _is_entropy_candidate(token: str) -> bool:
    """Would the entropy backstop actually flag this string on its own? If not there is nothing
    to ignore — and refusing here is also what stops the store being pre-seeded with short
    fragments of something that will be assembled later."""
    return any(f.kind in IGNORABLE_KINDS for f in _scan_entropy(token))


def ignores_path(root: str) -> str:
    return os.path.join(root, MOKATA_DIR, IGNORES_FILENAME)


def normalize_target(root: str, path: str) -> str:
    """`path` as a repo-relative POSIX path, or raise.

    Refuses anything that is not ONE concrete file inside the repo: an empty path, a traversal
    out of the root, an absolute path elsewhere, a directory, and a glob. An ignore scoped to a
    directory or a pattern would stop being "this exact string in this one file" — which is the
    containment (2) the threat model above rests on."""
    if not path or not path.strip():
        raise IgnoreError("a target file is required — an ignore is scoped to one exact file")
    raw = path.strip()
    if any(ch in raw for ch in "*?[") or raw.endswith(("/", os.sep)):
        raise IgnoreError(
            f"'{raw}' is a directory or a pattern — an ignore is scoped to ONE exact file, "
            f"never a glob or a tree")
    root_abs = os.path.realpath(root)
    target = raw if os.path.isabs(raw) else os.path.join(root_abs, raw)
    target = os.path.realpath(target)
    rel = os.path.relpath(target, root_abs)
    if rel == os.curdir or rel.startswith(os.pardir + os.sep) or rel == os.pardir:
        raise IgnoreError(
            f"'{raw}' is outside the repo — an ignore only ever applies to a file in this "
            f"repository, because that is what makes it reviewable in the diff")
    if os.path.isdir(target):
        raise IgnoreError(f"'{raw}' is a directory — an ignore is scoped to ONE exact file")
    return rel.replace(os.sep, "/")


@dataclass(frozen=True)
class IgnoreEntry:
    hash: str          # sha256 of the flagged token — never the token
    path: str          # repo-relative POSIX path
    reason: str        # REQUIRED; the thing a reviewer reads in the diff
    added_at: str
    actor: str

    def as_dict(self) -> dict:
        return {"hash": self.hash, "path": self.path, "reason": self.reason,
                "added_at": self.added_at, "actor": self.actor}


def _checksum(rows: List[dict]) -> str:
    blob = json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _render(rows: List[dict]) -> str:
    """The file's exact bytes. Human-readable on purpose: it is read in a PR diff."""
    return json.dumps({"version": STORE_VERSION, "checksum": _checksum(rows), "entries": rows},
                      indent=2, sort_keys=False) + "\n"


class IgnoreStore:
    """The version-controlled ignore list for one repo. Read cheaply, written under the same
    cross-process lock + atomic-replace primitives every other shared mokata file uses."""

    def __init__(self, root: str, entries: Optional[List[IgnoreEntry]] = None) -> None:
        self.root = root
        self._entries: List[IgnoreEntry] = list(entries or [])
        self._index = {(e.hash, e.path) for e in self._entries}

    # --- loading ---------------------------------------------------------------------------
    @classmethod
    def load(cls, root: str) -> "IgnoreStore":
        """The store on disk (empty when there is no file). Raises `TamperedIgnoreFile` when the
        checksum does not match — including for an unparseable file, because "I could not read
        the suppression list" must never render as "there are no suppressions"."""
        path = ignores_path(root)
        if not os.path.exists(path):
            return cls(root, [])
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            rows = data["entries"]
            recorded = data["checksum"]
            if not isinstance(rows, list):
                raise ValueError("entries is not a list")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise TamperedIgnoreFile(
                f"{_HAND_EDIT_REFUSAL.format(name=IGNORES_FILENAME)} ({type(exc).__name__})")
        if _checksum(rows) != recorded:
            raise TamperedIgnoreFile(_HAND_EDIT_REFUSAL.format(name=IGNORES_FILENAME))
        try:
            entries = [IgnoreEntry(hash=r["hash"], path=r["path"], reason=r["reason"],
                                   added_at=r.get("added_at", ""), actor=r.get("actor", ""))
                       for r in rows]
        except (KeyError, TypeError) as exc:
            raise TamperedIgnoreFile(
                f"{_HAND_EDIT_REFUSAL.format(name=IGNORES_FILENAME)} ({type(exc).__name__})")
        return cls(root, entries)

    # --- reading ---------------------------------------------------------------------------
    def entries(self) -> List[IgnoreEntry]:
        return list(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def is_ignored(self, token: str, path: Optional[str]) -> bool:
        """Is `token` ignored FOR `path`? A pathless scan (a Bash command line, an egress check)
        can never match — an entry is path-scoped, so there is nothing to match against, and the
        fail-closed answer is the only honest one."""
        if not path:
            return False
        try:
            rel = normalize_target(self.root, path)
        except IgnoreError:
            return False                     # a path we cannot place in the repo grants nothing
        return (token_key(token), rel) in self._index

    def inert(self) -> List[Tuple[IgnoreEntry, str]]:
        """`(entry, why)` for every entry that currently suppresses NOTHING — the no-TTL hygiene
        answer (see `add_ignore`'s expiry note). Reported, never auto-removed: silently dropping
        a row from a version-controlled file would be a write nobody asked for.

        Deliberately NOT called "stale", and deliberately NOT triggered by a missing file alone.
        The commonest way to record an ignore is from a BLOCKED write — so at that moment the
        target does not exist yet, because the write it unblocks has not been retried. Labelling
        that "stale (file gone)" made the very first thing a freshly-unwalled user saw read like
        a mistake; it was caught in the live demo, not by inspection. A file that is not there is
        a PENDING state and says nothing, so it is passed over in silence.

        What DOES report is content expiry, which is the thing worth pruning on: the file is
        there, and no string in it hashes to this entry any more (the identifier was renamed, the
        line deleted). Read-only, bounded, and degrade-clean — an unreadable file reports nothing
        rather than guessing."""
        out: List[Tuple[IgnoreEntry, str]] = []
        for e in self._entries:
            target = os.path.join(self.root, e.path)
            if not os.path.exists(target):
                continue                     # pending, not rot — see above
            try:
                if os.path.getsize(target) > _INERT_SCAN_MAX_BYTES:
                    continue                 # too big to re-scan cheaply; say nothing
                with open(target, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue                     # unreadable — no evidence either way
            if e.hash not in {token_key(f.token) for f in _scan_entropy(text) if f.token}:
                out.append((e, "that string is no longer flagged in this file"))
        return out


def load_for_scan(root: str) -> Tuple[Optional[IgnoreStore], str]:
    """`(store, notice)` for the scanning path, which must never raise and must never silently
    lose its suppression list either. A tampered/unreadable store yields `(None, <loud reason>)`:
    None means NOTHING is suppressed, so every finding blocks again — fail-closed."""
    try:
        return IgnoreStore.load(root), ""
    except TamperedIgnoreFile as exc:
        return None, str(exc)
    except OSError as exc:
        return None, (f"{_HAND_EDIT_REFUSAL.format(name=IGNORES_FILENAME)} "
                      f"({type(exc).__name__})")


# ══════════════════════════════════════════════════════════════════════════════════════════
# The ONE shared block-message builder (R3/R4 — wording lives here, never inside a tool).
# ══════════════════════════════════════════════════════════════════════════════════════════

BLOCK_HEADLINE = "mokata: secret detected — write/commit/send blocked."
_FP_LEAD = ("If this is a FALSE POSITIVE, the entropy backstop's guess can be ignored for this "
            "exact string in this exact file:")
_FP_FOOT = ("The ignore is recorded in `.mokata/{name}` — version-controlled on purpose, so it "
            "shows up in the PR diff with your reason. It never applies to a recognised "
            "credential shape, and never to any other string or file.")
_NON_NEGOTIABLE = ("Findings above from the signature layer or the known-shape floor "
                   "({shapes}) CANNOT be ignored — those are documented credential formats, "
                   "not a guess. Remove the value and reference it from the environment "
                   "instead.")


def remedy_command(token: str, path: str) -> str:
    """The exact, pasteable invocation for ONE finding.

    The literal is in the command because the CLI must classify it (see `classify`): a hash
    cannot be refused by name. It goes into argv and shell history, never into a committed
    file — and it is already in the transcript, because it is the content of the write that was
    just rejected, so the message reveals nothing the surface did not already carry."""
    return (f"mokata secret ignore --token {shlex.quote(token)} "
            f"--file {shlex.quote(path)} --reason {shlex.quote('why this is not a secret')}")


def render_block(findings: List[Finding], *, path: Optional[str] = None,
                 root: Optional[str] = None) -> str:
    """The block message for BOTH surfaces — the CLI `secret-guard` hook and the MCP WriteGate.

    Deliverable (g): an entropy-layer block names the exact command for THAT finding. Before
    this, a blocked user was told what was wrong and given no road out at all."""
    lines = [f"BLOCKED [{f.layer}/{f.kind}] {f.detail}" for f in findings]
    lines.append(BLOCK_HEADLINE)
    # The path is rendered REPO-RELATIVE — the form the user types and the form the store keys
    # on. `root` is resolved here rather than trusted from the caller so the hook and the
    # WriteGate cannot render the same finding two different ways; that drift is exactly what
    # "one shared builder" exists to prevent.
    rel = path or ""
    if rel:
        base = root
        if base is None:
            from ..gate_hook import find_mokata_root
            base = find_mokata_root(os.path.dirname(os.path.abspath(path)))
        if base:
            try:
                rel = normalize_target(base, path)
            except IgnoreError:
                rel = path
    seen = set()
    remedies = []
    for f in findings:
        if is_ignorable(f) and f.token and f.token not in seen and rel:
            seen.add(f.token)
            remedies.append("  " + remedy_command(f.token, rel))
    if remedies:
        lines.append("")
        lines.append(_FP_LEAD)
        lines.extend(remedies)
        lines.append(_FP_FOOT.format(name=IGNORES_FILENAME))
    shapes = sorted({f.kind for f in findings if not is_ignorable(f)})
    if shapes:
        lines.append("")
        lines.append(_NON_NEGOTIABLE.format(shapes=", ".join(shapes)))
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════════════════
# The gated commands. `mokata secret ignore` / `ignores` / `--remove` are the ONLY entry (d).
# ══════════════════════════════════════════════════════════════════════════════════════════

def _ledger_for(root: str):
    """The repo's existing I3 audit ledger, or None. Never creates a second log."""
    try:
        from .ledger import AuditLedger
        return AuditLedger.from_mokata_dir(os.path.join(root, MOKATA_DIR))
    except (OSError, ImportError):
        return None


def _gated_write(root: str, rows: List[dict], *, target: str, assume_yes: bool,
                 confirm: Optional[Callable[[str], bool]], ledger: Any,
                 policy: Any) -> bool:
    """Commit `rows` through the SAME universal WriteGate every other durable mokata write uses
    (I2/P2) — reuse, not a parallel gate. The gate secret-scans the rendered store, so a secret
    pasted into `--reason` is hard-blocked; the store itself carries only hashes, so a clean
    write always passes. Returns True when committed."""
    from ..govern.gate import WriteGate, WriteRequest
    from ..govern.trust import (CLI_SURFACE, policy_approved, policy_surface, policy_tool,
                                policy_trust)
    text = _render(rows)
    path = ignores_path(root)

    def _commit() -> None:
        with file_lock(lock_path_for(path)):
            atomic_write_text(path, text)

    gate = WriteGate(ledger=ledger, trust=policy_trust(policy))
    outcome = gate.submit(
        WriteRequest(kind="config", target=path, content=text,
                     tool=policy_tool(policy, "secret_ignore"),
                     surface=policy_surface(policy, CLI_SURFACE)),
        commit=_commit, confirm=confirm, assume_yes=assume_yes,
        human_approved=policy_approved(policy), workspace_root=root)
    return outcome.committed


def add_ignore(root: str, token: str, path: str, *, reason: str,
               assume_yes: bool = False, confirm: Optional[Callable[[str], bool]] = None,
               out: Optional[Callable[[str], None]] = None,
               actor: str = "cli", policy: Any = None) -> Optional[IgnoreEntry]:
    """Record that `token` in `path` is a false positive. Raises `NotIgnorable` (by NAME) for a
    recognised credential shape, `IgnoreError` for anything else refused.

    EXPIRY — the decision, with its grounding. Entries do NOT expire on a clock, and carry no
    TTL field. A TTL re-walls a user on a day they changed nothing, which is exactly the failure
    this stage exists to remove, reintroduced on a timer. What a TTL is reaching for is "do not
    let the list rot" — and the key already delivers that better: an entry is keyed to
    hash(token)+path, so it EXPIRES ON CONTENT. Fix the identifier, rename or delete the file,
    and the entry matches nothing and is reported STALE by `mokata secret ignores` and by
    `mokata doctor`. Hygiene on evidence, not on a calendar; (f) surfaces it either way."""
    emit = out or print
    if not reason or not reason.strip():
        raise IgnoreError(
            "--reason is required — the reason is what a reviewer reads in the diff when they "
            "see that a secret finding was suppressed")
    rel = normalize_target(root, path)
    shape = classify(token)
    if shape is not None:
        raise NotIgnorable(shape, (
            f"refused: that string IS the {shape} shape — a recognised credential format, not "
            f"the entropy backstop's guess. Only entropy-layer findings are negotiable; the "
            f"signature layer and the known-shape floor stay non-overridable (G4/I1). If this "
            f"is a real key, rotate it; if it is a fixture, use an obviously-fake value."))
    if not _is_entropy_candidate(token):
        raise IgnoreError(
            "refused: the entropy backstop does not flag that string, so there is nothing to "
            "ignore. Paste the exact string the block message named.")
    key = token_key(token)
    store = IgnoreStore.load(root)
    if (key, rel) in store._index:
        emit(f"already ignored: {rel} (nothing to do)")
        return None
    entry = IgnoreEntry(hash=key, path=rel, reason=reason.strip(), added_at=_now_iso(),
                        actor=actor)
    rows = [e.as_dict() for e in store.entries()] + [entry.as_dict()]
    emit(f"mokata secret ignore: {rel}  [{key[:12]}…]  reason: {entry.reason}")
    emit("  entropy-layer only · this exact string in this one file · committed to "
         f"`.mokata/{IGNORES_FILENAME}` so it appears in your PR diff")
    ledger = _ledger_for(root)
    if not _gated_write(root, rows, target=rel, assume_yes=assume_yes, confirm=confirm,
                        ledger=ledger, policy=policy):
        return None
    if ledger is not None:
        # I3 — the durable, human-readable record, alongside the gate's own `write_gate` entry.
        # Hash + path + reason only: the literal is never written here either.
        ledger.record(LEDGER_KIND, action="added", token_hash=key, target=rel,
                      reason=entry.reason, actor=actor, layer="entropy")
    return entry


def remove_ignore(root: str, token_or_hash: str, path: str, *,
                  assume_yes: bool = False, confirm: Optional[Callable[[str], bool]] = None,
                  out: Optional[Callable[[str], None]] = None,
                  actor: str = "cli", policy: Any = None) -> Optional[IgnoreEntry]:
    """Revoke an ignore. Accepts the literal token OR its stored hash — a revocation only ever
    TIGHTENS the scanner, so it needs no shape verification (and a user who no longer has the
    string must still be able to remove the row they can see in the list)."""
    emit = out or print
    rel = normalize_target(root, path)
    store = IgnoreStore.load(root)
    rows = [e for e in store.entries() if e.path == rel]
    # The literal, the full hash, or — git-style — an unambiguous hash PREFIX. The prefix form is
    # not a convenience: `mokata secret ignores` prints an ABBREVIATED hash, so without it the
    # listing→remove loop is a dead end for anyone pasting what is on their screen. The trailing
    # ellipsis the listing draws is stripped for the same reason.
    probe = token_or_hash.strip().rstrip("….").lower()
    if not probe:
        raise IgnoreError("--token or --hash is required to remove an ignore")
    matches = [e for e in rows if e.hash == token_key(token_or_hash)] \
        or [e for e in rows if e.hash.startswith(probe)]
    if len(matches) > 1:
        # Refuse rather than guess: removing the WRONG row silently re-walls a user on a
        # different false positive, and they have no way to tell that is what happened.
        raise IgnoreError(
            f"'{token_or_hash}' matches {len(matches)} ignores in {rel} — pass more of the hash")
    match = matches[0] if matches else None
    if match is None:
        emit(f"no ignore recorded for that string in {rel}")
        return None
    rows = [e.as_dict() for e in store.entries() if e is not match]
    emit(f"mokata secret ignore --remove: {rel}  [{match.hash[:12]}…] — that string will "
         f"block again")
    ledger = _ledger_for(root)
    if not _gated_write(root, rows, target=rel, assume_yes=assume_yes, confirm=confirm,
                        ledger=ledger, policy=policy):
        return None
    if ledger is not None:
        ledger.record(LEDGER_KIND, action="removed", token_hash=match.hash, target=rel,
                      reason=match.reason, actor=actor, layer="entropy")
    return match


def render_list(root: str) -> str:
    """`mokata secret ignores` — the listing. Hash + path + reason + age, never the literal."""
    store, notice = load_for_scan(root)
    if store is None:
        return notice
    entries = store.entries()
    if not entries:
        return ("no secret-scan ignores recorded — every finding blocks.\n"
                "(An entropy-layer false positive can be ignored with "
                "`mokata secret ignore --token '<string>' --file <path> --reason '<why>'`.)")
    inert = {(e.hash, e.path): why for e, why in store.inert()}
    lines = [f"{len(entries)} secret-scan ignore(s) — entropy layer only, "
             f"`.mokata/{IGNORES_FILENAME}` (version-controlled):"]
    for e in entries:
        why = inert.get((e.hash, e.path), "")
        lines.append(f"  {e.hash[:12]}…  {e.path}" + (f"  INERT — {why}" if why else ""))
        lines.append(f"      reason: {e.reason}   added: {e.added_at}  by: {e.actor}")
    if inert:
        lines.append(f"{len(inert)} entry(ies) suppress nothing any more — remove them with "
                     f"`mokata secret ignore --remove`.")
    lines.append("A recognised credential shape can never be ignored; these suppress the "
                 "entropy backstop's guess only.")
    return "\n".join(lines)
