"""G3-EVIDENCE (0.0.27) — the COMMITTED RUN RECORD: `.mokata/runs/<run>/`.

The audit ledger and the gate-evidence log both live under `.mokata/temp_local/` (git-ignored), so
a CI checkout has neither, and a PR check that read them would always report an empty run as if it
had looked. A run's ledger and evidence reach CI only when a human COMMITS them, through this
module's one gated writer, `commit_run_record` (EV-GATE: "opt-in committable with the branch").
Saving stays ungated; sharing — and committing is sharing — is gated (doc 85 §6).

  * WHAT IS EXPORTED. The run's rows (`run == run_id`) of `EXPORTED_KINDS`, each PROJECTED onto its
    kind's field allowlist (`FIELDS`) at WRITE time: a field not listed — prompts, free-text
    `reason`/`summary`, test output — never reaches the record. The run's gate evidence is projected
    the same way (`_project_evidence`). Every kept string is redacted (`explain.ground.redact`) and
    home paths become `~`; a value still absolute after that is dropped and counted.
  * A SECRET BLOCKS. Each kept value is scanned (`govern.secrets.scan`) after projection; a hit
    blocks the whole commit and names the row, the field and the recovery.
  * THE HUMAN SEES THE BYTES. `plan_run_record` builds the exact three files; the gated commit
    shows them and writes exactly them (`commit_run_record`).
  * ROWS WITH NO `run=` are counted (`rows_unattributed`), never exported and never guessed.

The reader (`load_for_pr`) reads only the records a PR touched, streams each JSONL file a line at a
time, refuses symlinks, and reports one of the states below — never collapsed (§7g).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Tuple

from . import MOKATA_DIR
from .explain.safedir import UnsafeStore   # a link / junction on the record's write path

RUNS_DIRNAME = "runs"
META_FILE = "meta.json"
LEDGER_FILE = "ledger.jsonl"
EVIDENCE_FILE = "evidence.jsonl"
RECORD_FILES = (LEDGER_FILE, EVIDENCE_FILE, META_FILE)

#: The ledger kinds a run record carries, and per kind the ONLY fields that are written.
FIELDS: Dict[str, Tuple[str, ...]] = {
    "write_approval": ("seq", "at", "kind", "run", "decision", "proposal", "tool", "target",
                       "content_hash", "actor", "approved_by", "committed", "write_seq",
                       "entry_hash"),
    "write_gate": ("seq", "at", "kind", "run", "write_kind", "target", "decision", "actor",
                   "content_hash", "entry_hash"),
    "gate": ("seq", "at", "kind", "run", "gate", "phase", "decision", "ac_count", "entry_hash"),
    "gate_advisory": ("seq", "at", "kind", "run", "gate", "enforcement", "decision",
                      "would_block", "actor", "entry_hash"),
    "phase": ("seq", "at", "kind", "run", "phase", "ok", "entry_hash"),
    "enforcement_change": ("seq", "at", "kind", "run", "subject", "item_id", "gkind", "direction",
                           "old", "new", "actor", "approval_seq", "entry_hash"),
}
EXPORTED_KINDS = tuple(FIELDS)
#: Fields that hold a PATH: a value still absolute after `~`-folding is dropped, never written.
PATH_FIELDS = frozenset({"target"})
#: The gates whose `content` artifacts may be committed (their content is a test NAME, not output).
CONTENT_GATES = frozenset({"completeness"})

# ---- the reader's states (§7g: never collapsed) --------------------------------------------------
NOT_OPTED_IN = "not-opted-in"       # no `.mokata/runs/` at all
NO_FILE_LIST = "no-file-list"       # the check got no changed files: attribution is impossible
NONE_IN_PR = "none-in-pr"           # records exist; this PR touched none of them
PRESENT = "present"                 # at least one record this PR touched
# ... and per record:
OK = "ok"
UNREADABLE = "unreadable"           # a bad line, a missing file, a symlink: nothing renders
INCONSISTENT = "inconsistent"       # meta's counts disagree with the files: nothing renders

_HOME_RE = re.compile(r"(?:/Users|/home)/[^/\s\"'`]+|[A-Za-z]:\\Users\\[^\\\s\"'`]+",
                      re.IGNORECASE)
_ABS_RE = re.compile(r"^(?:/|\\\\|[A-Za-z]:[\\/])")


def runs_dir(root: str) -> str:
    return os.path.join(root, MOKATA_DIR, RUNS_DIRNAME)


def record_dirname(run_id: str) -> str:
    """The record's directory name: the portable run dir name, LOWER-CASED, so the same run written
    as `Run-A` on one branch and `run-a` on another lands in one directory on every filesystem."""
    from .govern.evidence import safe_run_dirname
    name = safe_run_dirname(run_id).lower()
    # The writer opens it as a path component that may not start with `.` or `-` (a hidden dir, an
    # option-looking name): such a run still gets a record, under a `_`-prefixed name.
    return name if name[:1].isalnum() or name[:1] == "_" else "_" + name[:119]


# ================================================================================ projection
def fold_home(text: str) -> str:
    """Home directories become `~` (this machine's, and any `/Users/<x>`, `/home/<x>`,
    `C:\\Users\\<x>` prefix)."""
    home = os.path.expanduser("~")
    if home and home not in ("~", "/") and home in text:
        text = text.replace(home, "~")
    return _HOME_RE.sub("~", text)


def is_absolute_path(text: str) -> bool:
    return bool(_ABS_RE.match(text or ""))


@dataclass
class SecretHit:
    file: str
    seq: Any
    field: str
    kind: str


@dataclass
class RunRecordPlan:
    """The exact bytes a commit would write, and what the projection did to get there."""
    run_id: str
    dirname: str
    files: Dict[str, str] = field(default_factory=dict)     # file name -> text
    rows_exported: int = 0
    evidence_exported: int = 0
    rows_unattributed: int = 0
    dropped_absolute: int = 0
    dropped_content: int = 0
    redacted: int = 0
    secrets: List[SecretHit] = field(default_factory=list)

    @property
    def rel_dir(self) -> str:
        return f"{MOKATA_DIR}/{RUNS_DIRNAME}/{self.dirname}"

    @property
    def blocked(self) -> bool:
        return bool(self.secrets)

    def preview(self) -> str:
        """Every byte the commit writes, file by file — what the human approves."""
        out = [f"mokata evidence commit — run {self.run_id} -> {self.rel_dir}/",
               f"  {self.rows_exported} ledger row(s), {self.evidence_exported} evidence "
               f"record(s); {self.rows_unattributed} ledger row(s) carry no run and are NOT "
               f"exported; {self.dropped_absolute} absolute path value(s) and "
               f"{self.dropped_content} content artifact(s) dropped; {self.redacted} line(s) "
               f"redacted"]
        for name in RECORD_FILES:
            out.append(f"----- {self.rel_dir}/{name} ({len(self.files.get(name, ''))} chars)")
            out.append(self.files.get(name, ""))
        return "\n".join(out)

    def recovery(self) -> str:
        """What a blocked commit names: which value, and the way out."""
        lines = ["secret detected — nothing was written. The run record cannot be committed "
                 "while it carries:"]
        for h in self.secrets:
            lines.append(f"  - {h.file} row seq={h.seq} field `{h.field}` ({h.kind})")
        lines.append(
            "Recovery: if it is a FALSE positive, record it with `mokata secret ignore --token "
            "'<the flagged string>' --file <the record path> --reason '<why>'`; if it is REAL, "
            "rotate the credential and remove the value from the machine-local source "
            "(`.mokata/temp_local/audit/ledger.jsonl` or `.mokata/temp_local/evidence/`). Then "
            f"re-run `mokata evidence commit --run {self.run_id}` — the projection is recomputed "
            "from the source, so nothing stale is committed.")
        return "\n".join(lines)


class _Projector:
    def __init__(self, plan: RunRecordPlan) -> None:
        self.plan = plan

    def value(self, file: str, seq: Any, name: str, value: Any, *,
              path_field: bool = False) -> Tuple[bool, Any]:
        """(keep?, projected value). Strings are scanned, redacted and home-folded; non-strings
        (numbers, booleans, None) pass as they are; lists/dicts are not allowlisted values."""
        if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
            return True, value
        if not isinstance(value, str):
            return False, None
        from .govern.secrets import scan
        for f in scan(value):
            if f.layer in ("signature", "entropy"):
                self.plan.secrets.append(SecretHit(file, seq, name, f.kind))
                return False, None
        from .explain.ground import redact
        text, n = redact(value)
        self.plan.redacted += n
        text = fold_home(text)
        if path_field and is_absolute_path(text):
            self.plan.dropped_absolute += 1
            return False, None
        return True, text

    def row(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        seq = entry.get("seq")
        for name in FIELDS[entry["kind"]]:
            if name not in entry:
                continue
            keep, v = self.value(LEDGER_FILE, seq, name, entry[name],
                                 path_field=name in PATH_FIELDS)
            if keep:
                out[name] = v
        return out

    def evidence(self, d: Dict[str, Any], n: int) -> Dict[str, Any]:
        def s(name: str, v: Any, *, path: bool = False) -> Tuple[bool, Any]:
            return self.value(EVIDENCE_FILE, n, name, v, path_field=path)

        out: Dict[str, Any] = {}
        for name in ("schema_version", "gate_id", "verdict", "enforcement", "run_id", "at"):
            if name in d:
                keep, v = s(name, d[name])
                if keep:
                    out[name] = v
        tested = []
        for t in d.get("tested") or []:
            if not isinstance(t, dict):
                continue
            row: Dict[str, Any] = {}
            for name in ("check_id", "status"):
                keep, v = s(f"tested.{name}", t.get(name, ""))
                if keep:
                    row[name] = v
            ids = []
            for a in t.get("ac_ids") or []:
                keep, v = s("tested.ac_ids", str(a))
                if keep:
                    ids.append(v)
            row["ac_ids"] = ids
            tested.append(row)
        out["tested"] = tested
        artifacts = []
        gate = str(d.get("gate_id", ""))
        for a in d.get("artifacts") or []:
            if not isinstance(a, dict):
                continue
            typ = str(a.get("type", ""))
            if typ == "content" and gate not in CONTENT_GATES:
                self.plan.dropped_content += 1
                continue
            keep, ref = s("artifacts.ref", str(a.get("ref", "")), path=(typ == "path"))
            if not keep or not ref:
                continue
            keep_l, label = s("artifacts.label", str(a.get("label", "") or ""))
            artifacts.append({"type": typ, "ref": ref, "label": label if keep_l else ""})
        out["artifacts"] = artifacts
        return out


def _iter_jsonl(path: str) -> Iterator[Any]:
    """Yield each parsed line of a JSONL file, one line at a time (never the whole file). A line
    that is not JSON raises ValueError — the caller decides what an unreadable line means."""
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def _head_sha(root: str) -> str:
    from .session_worktrees import _git
    code, out = _git(root, "rev-parse", "HEAD")
    return out.strip() if code == 0 else ""


def plan_run_record(root: str, mokata_dir: str, run_id: str, *,
                    head_sha: Optional[str] = None,
                    exported_at: Optional[str] = None) -> RunRecordPlan:
    """Project the run's machine-local ledger rows and gate evidence into the three record files.
    Pure of writes. An unreadable machine-local ledger line is skipped by the reader it shares with
    `AuditLedger.entries` semantics — here it is a hard error, because a record built over a
    partial read would be presented as the run's whole record.

    `exported_at` / `head_sha` FREEZE the two values a re-plan would otherwise re-derive: the MCP
    redeem passes the ones its approved preview carried, so the bytes it writes are the bytes the
    human approved — or the hash differs and it refuses (never a silent re-render)."""
    from .govern.evidence import evidence_path
    from .govern.ledger import AuditLedger
    plan = RunRecordPlan(run_id=run_id, dirname=record_dirname(run_id))
    proj = _Projector(plan)

    rows: List[Dict[str, Any]] = []
    ledger_path = AuditLedger.path_for(mokata_dir)
    if os.path.exists(ledger_path):
        for entry in _iter_jsonl(ledger_path):
            if not isinstance(entry, dict) or entry.get("kind") not in FIELDS:
                continue
            run = entry.get("run")
            if not isinstance(run, str) or not run:
                plan.rows_unattributed += 1
                continue
            if run == run_id:
                rows.append(proj.row(entry))

    ev_rows: List[Dict[str, Any]] = []
    ev_root = os.path.join(mokata_dir, _temp_local(), "evidence")
    ev_path = evidence_path(ev_root, run_id)
    if os.path.exists(ev_path):
        for n, d in enumerate(_iter_jsonl(ev_path), start=1):
            if isinstance(d, dict):
                ev_rows.append(proj.evidence(d, n))

    plan.rows_exported = len(rows)
    plan.evidence_exported = len(ev_rows)
    plan.files[LEDGER_FILE] = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n"
                                      for r in rows)
    plan.files[EVIDENCE_FILE] = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n"
                                        for r in ev_rows)
    meta = {
        "run_id": run_id,
        "exported_at": (datetime.now(timezone.utc).isoformat() if exported_at is None
                        else exported_at),
        "head_sha": _head_sha(root) if head_sha is None else head_sha,
        "rows_exported": plan.rows_exported,
        "evidence_exported": plan.evidence_exported,
        "rows_unattributed": plan.rows_unattributed,
        "chain_tip": (rows[-1].get("entry_hash", "") if rows else ""),
    }
    plan.files[META_FILE] = json.dumps(meta, indent=2, sort_keys=True) + "\n"
    return plan


def _temp_local() -> str:
    from . import TEMP_LOCAL_DIRNAME
    return TEMP_LOCAL_DIRNAME


# ================================================================================ the gated writer
@dataclass
class RunRecordResult:
    committed: bool
    blocked: bool
    message: str
    plan: Optional[RunRecordPlan] = None
    findings: List[Any] = field(default_factory=list)


def commit_run_record(root: str, run_id: str, *, policy: Any = None, ledger: Any = None,
                      confirm: Optional[Callable[[str], bool]] = None, assume_yes: bool = False,
                      out: Optional[Callable[[str], None]] = None,
                      expected: Optional[str] = None,
                      plan: Optional[RunRecordPlan] = None) -> RunRecordResult:
    """THE ONLY WRITER under `.mokata/runs/`. Builds the plan, shows its exact bytes, and writes all
    three files inside ONE WriteGate commit closure. `expected` (the MCP path) is the content hash
    the human approved: if the recomputed plan differs, nothing is written."""
    from .config import Surface
    from .govern.gate import WriteGate, WriteRequest
    from .govern.trust import (CLI_SURFACE, policy_approved, policy_surface, policy_tool,
                               policy_trust)
    emit = out or print
    surface = Surface.load(root)
    plan = plan if plan is not None else plan_run_record(root, surface.mokata_dir, run_id)
    if plan.blocked:
        emit(plan.recovery())
        return RunRecordResult(False, True, "blocked: secret detected", plan)
    if not plan.rows_exported and not plan.evidence_exported:
        msg = (f"nothing to commit — run {run_id} has no ledger rows carrying its run id and no "
               f"gate evidence on this machine")
        emit(msg)
        return RunRecordResult(False, False, msg, plan)
    content = plan.preview()
    if expected is not None and content_hash(plan) != expected:
        msg = "refused: the run record changed after it was approved — re-propose it"
        emit(msg)
        return RunRecordResult(False, True, msg, plan)
    try:
        _record_dir(root, plan.dirname, create=False)
    except UnsafeStore as exc:
        msg = f"refused: {exc} — nothing was written"
        emit(msg)
        return RunRecordResult(False, True, msg, plan)
    emit(content)

    def _commit() -> None:
        # Re-checked HERE, right before the write: every component from the repo root down is
        # opened without following a link (O_NOFOLLOW + fstat match where the OS has dir_fd), and
        # each file goes through an O_EXCL|O_NOFOLLOW temp + rename inside that held directory.
        from .explain.safedir import SafeDir
        d = _record_dir(root, plan.dirname, create=True)
        # Bound by its constructor so the zero-bypass audit (si_6) resolves THIS gated closure as a
        # caller of `SafeDir.write`; it shares `d`'s held descriptor (closed once, by `d`).
        held = SafeDir(d.path, d.fd)
        try:
            for name in RECORD_FILES:                   # meta.json LAST: it vouches for the rest
                st = held._lstat(name)
                if st is not None and (_st_is_link(st) or not stat.S_ISREG(st.st_mode)):
                    raise UnsafeStore(f"{plan.rel_dir}/{name} is a link or not a regular "
                                           f"file (not followed)")
                held.write(name, plan.files[name].encode("utf-8"))
        finally:
            d.close()

    gate = WriteGate(ledger=ledger, trust=policy_trust(policy))
    try:
        outcome = gate.submit(
            WriteRequest(kind="code", target=plan.rel_dir + "/", content=content,
                         tool=policy_tool(policy, "evidence_commit"),
                         surface=policy_surface(policy, CLI_SURFACE), run=run_id),
            commit=_commit, confirm=confirm, assume_yes=assume_yes,
            human_approved=policy_approved(policy))
    except UnsafeStore as exc:
        msg = f"refused: {exc} — nothing was written"
        emit(msg)
        return RunRecordResult(False, True, msg, plan)
    if outcome.committed:
        emit(f"committed: {plan.rel_dir}/ ({', '.join(RECORD_FILES)}) — commit it with the branch")
    return RunRecordResult(outcome.committed, bool(outcome.findings), outcome.reason, plan,
                           findings=list(outcome.findings))


def content_hash(plan: RunRecordPlan) -> str:
    """The hash of EVERY byte a plan writes — all three files, `exported_at` and `head_sha`
    included. Nothing is excluded: an approval binds the exact bytes, so a commit whose meta.json
    differs from the preview by one character is a different write and is refused."""
    import hashlib
    h = hashlib.sha256()
    for name in RECORD_FILES:
        data = plan.files.get(name, "").encode("utf-8")
        h.update(f"{name}\0{len(data)}\0".encode("ascii"))
        h.update(data)
    return h.hexdigest()


def frozen_meta(preview: str, plan: RunRecordPlan) -> Dict[str, str]:
    """The `exported_at` / `head_sha` an approved PREVIEW carried (its meta.json section is last,
    and its length is stated in its header), or {} when the preview is not one of this record's."""
    head = f"----- {plan.rel_dir}/{META_FILE} ("
    at = preview.rfind("\n" + head) if isinstance(preview, str) else -1
    if at < 0:
        return {}
    line_end = preview.find("\n", at + 1)
    if line_end < 0:
        return {}
    try:
        meta = json.loads(preview[line_end + 1:])
    except ValueError:
        return {}
    if not isinstance(meta, dict):
        return {}
    return {k: meta[k] for k in ("exported_at", "head_sha") if isinstance(meta.get(k), str)}


def _st_is_link(st: os.stat_result) -> bool:
    from .explain.safedir import _is_link as _safedir_is_link
    return _safedir_is_link(st)


def _record_dir(root: str, dirname: str, *, create: bool) -> Any:
    """`.mokata/runs/<dirname>` opened component by component from the repo root, NO link or
    junction followed anywhere (the reader refuses them too). `create=False` only checks what
    exists (returns None, or the open directory closed again)."""
    from .explain.safedir import open_chain
    try:
        d = open_chain(root, [MOKATA_DIR, RUNS_DIRNAME, dirname], create=create)
    except OSError as exc:
        raise UnsafeStore(f"the record path cannot be opened safely ({exc})") from None
    if not create and d is not None:
        try:
            for name in RECORD_FILES:
                st = d._lstat(name)
                if st is not None and (_st_is_link(st) or not stat.S_ISREG(st.st_mode)):
                    raise UnsafeStore(f"{MOKATA_DIR}/{RUNS_DIRNAME}/{dirname}/{name} is a "
                                           f"link or not a regular file (not followed)")
        finally:
            d.close()
        return None
    return d


# ================================================================================ the reader
@dataclass
class RunRecord:
    dirname: str
    state: str
    reason: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)
    rows: List[Dict[str, Any]] = field(default_factory=list)
    evidence: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def rel_dir(self) -> str:
        return f"{MOKATA_DIR}/{RUNS_DIRNAME}/{self.dirname}"


@dataclass
class RunRecords:
    state: str
    records: List[RunRecord] = field(default_factory=list)
    untouched: int = 0

    @property
    def degraded(self) -> bool:
        return any(r.state != OK for r in self.records)


def _is_link(path: str) -> bool:
    try:
        return stat.S_ISLNK(os.lstat(path).st_mode)
    except OSError:
        return False


def _touched_dirs(changed_files: Iterable[str]) -> List[str]:
    prefix = f"{MOKATA_DIR}/{RUNS_DIRNAME}/"
    out: List[str] = []
    from .repo_paths import as_name
    for f in changed_files:
        # The changed-file list crossed in from outside this process (`--files`, `git diff`).
        p = str(as_name(str(f)))
        while p.startswith("./"):
            p = p[2:]
        if p.startswith(prefix):
            rest = p[len(prefix):]
            if "/" in rest:
                name = rest.split("/", 1)[0]
                if name and name not in out:
                    out.append(name)
    return out


def _read_record(root: str, name: str) -> RunRecord:
    rec = RunRecord(dirname=name, state=OK)
    base = runs_dir(root)
    d = os.path.join(base, name)
    if name in (".", "..") or os.sep in name or _is_link(d):
        rec.state, rec.reason = UNREADABLE, "the record directory is a symlink (not followed)"
        return rec
    paths = {f: os.path.join(d, f) for f in RECORD_FILES}
    for f, p in paths.items():
        if _is_link(p):
            rec.state, rec.reason = UNREADABLE, f"{f} is a symlink (not followed)"
            return rec
        if not os.path.isfile(p):
            rec.state, rec.reason = UNREADABLE, f"{f} is missing"
            return rec
    try:
        with open(paths[META_FILE], encoding="utf-8") as fh:
            meta = json.load(fh)
        if not isinstance(meta, dict):
            raise ValueError("meta.json is not an object")
        for row in _iter_jsonl(paths[LEDGER_FILE]):
            if not isinstance(row, dict):
                raise ValueError("a ledger line is not an object")
            rec.rows.append(row)
        for ev in _iter_jsonl(paths[EVIDENCE_FILE]):
            if not isinstance(ev, dict):
                raise ValueError("an evidence line is not an object")
            rec.evidence.append(ev)
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        rec.state, rec.reason = UNREADABLE, f"cannot be read ({type(exc).__name__}: {exc})"
        rec.rows, rec.evidence = [], []
        return rec
    rec.meta = meta
    if meta.get("rows_exported") != len(rec.rows) \
            or meta.get("evidence_exported") != len(rec.evidence):
        rec.state = INCONSISTENT
        rec.reason = (f"meta.json says {meta.get('rows_exported')} ledger row(s) and "
                      f"{meta.get('evidence_exported')} evidence record(s); the files hold "
                      f"{len(rec.rows)} and {len(rec.evidence)}")
        rec.rows, rec.evidence = [], []
    return rec


def load_for_pr(root: str, changed_files: Optional[List[str]]) -> RunRecords:
    """The committed run records THIS PR touched (by its changed-file list), each read whole or
    not at all. Never raises."""
    base = runs_dir(root)
    if not os.path.lexists(base):
        return RunRecords(NOT_OPTED_IN)
    if _is_link(base):
        return RunRecords(PRESENT, [RunRecord(dirname=RUNS_DIRNAME, state=UNREADABLE,
                                              reason=".mokata/runs is a symlink (not followed)")])
    if not changed_files:
        return RunRecords(NO_FILE_LIST)
    try:
        existing = sorted(n for n in os.listdir(base) if os.path.lexists(os.path.join(base, n)))
    except OSError:
        existing = []
    touched = [n for n in _touched_dirs(changed_files) if n in existing]
    if not touched:
        return RunRecords(NONE_IN_PR, untouched=len(existing))
    return RunRecords(PRESENT, [_read_record(root, n) for n in touched],
                      untouched=len(existing) - len(touched))


__all__ = ["FIELDS", "EXPORTED_KINDS", "NOT_OPTED_IN", "NO_FILE_LIST", "NONE_IN_PR", "PRESENT",
           "OK", "UNREADABLE", "INCONSISTENT", "RunRecordPlan", "RunRecordResult", "RunRecord",
           "RunRecords", "plan_run_record", "commit_run_record", "content_hash", "load_for_pr",
           "record_dirname", "runs_dir", "fold_home"]
