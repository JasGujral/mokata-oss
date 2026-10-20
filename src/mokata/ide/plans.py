"""The plan, the spec and the lifecycle of a run, for the review window (0.0.23 C5).

READ-ONLY, and nothing here is a second reader. Every answer comes from the seam the CLI already
reads through:

  * the PLAN is the run's approved approach — `brainstorm.load_approved_approach` over the run's
    scoped store (the `scoped_store(StateStore(state_dir(root)), run)` construction
    `cli_commands/spec._run_scoped_store` uses);
  * the SPEC is `engine.spec_gate.read_emitted_spec` (absent vs MALFORMED kept apart), its history
    `engine.spec_history` (the SAME reader `mokata spec show --versions / --version N` uses), its
    anchors `engine.spec_anchor.ac_anchor_lines`, and its tests the run's TDD record
    (`tdd_state.load`: which criteria have a failing test on record, which pass);
  * the LIFECYCLE is the progress-event log (`progress_events.ProgressLog`, read backward under its
    own byte cap), the run's TDD record, its checkpoint, its open amendment (`spec_amend__<run>`),
    and the audit ledger's `checkpoint`/`spec_amend` rows — all bounded reads.

TEAM. When the repo is team-connected (`session_transport.transport_kind_for_mode` says
`postgres` — the ONE mode derivation the session channel uses) a run's plan and spec can also be
read from the TEAM store: the shared session bundles (`session_bundle.list_session_bundles` /
`parse_bundle`, content-hash verified, over `make_transport("postgres", root)`). No SQL lives here.
When the team store cannot be reached the answer is an explicit `team-unreachable` state —
never the local data relabelled as team.

A run id is validated twice before any read: its SHAPE (`RUN_ID_RE` — no separator, no dot, so
it can never name a path outside the state dir) and its EXISTENCE (it must be a run the store
actually holds). Anything else is a `BadRequest` (HARD, 400).
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..repo_paths import name_of
from .server import BadRequest

RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
SOURCES = ("local", "team")
MAX_RUNS = 200            # the run chooser lists at most this many (most recent first)
MAX_EVENTS = 400          # lifecycle marks per run
MAX_TEXT = 4000           # any one free-text field the page renders
LEDGER_BYTE_CAP = 4 * 1024 * 1024


# ----------------------------------------------------------------------------- the stage arc
def lifecycle_stages() -> Tuple[str, ...]:
    """The user-facing pipeline arc, DERIVED: the skill arc every stack ships
    (`stacks._COMMON_SKILLS` — brainstorm, spec, test, develop, review, ship), which carries the
    progress badge's own stages (`progress.STAGE_BADGE_STAGES`) in the same order. If the two ever
    disagree, the badge's stages win and are said in that order — nothing here invents a stage."""
    from ..progress import STAGE_BADGE_STAGES
    try:
        from ..stacks import _COMMON_SKILLS as arc
    except ImportError:  # pragma: no cover — the arc is a module constant; keep the badge floor
        return tuple(STAGE_BADGE_STAGES)
    badge = [s for s in arc if s in STAGE_BADGE_STAGES]
    if badge != list(STAGE_BADGE_STAGES):
        return tuple(STAGE_BADGE_STAGES)
    return tuple(arc)


# ----------------------------------------------------------------------------- local stores
def _state_dir(root: str) -> str:
    from ..tdd_state import state_dir
    return state_dir(root)


def local_run_ids(root: str) -> List[str]:
    """Every run with run-scoped pipeline state — `run_resolver.run_ids`, the set the gates scan."""
    from ..run_resolver import run_ids
    return sorted(run_ids(_state_dir(root)))


def _run_store(root: str, run_id: str) -> Any:
    from ..session_state import scoped_store
    from ..state import StateStore
    return scoped_store(StateStore(_state_dir(root)), run_id)


def check_run_id(run_id: str) -> str:
    rid = (run_id or "").strip()
    if not RUN_ID_RE.match(rid):
        raise BadRequest("run must be a run id (letters, digits, '-' or '_'; no path)")
    return rid


def check_local_run(root: str, run_id: str) -> str:
    rid = check_run_id(run_id)
    if rid not in set(local_run_ids(root)):
        raise BadRequest(f"no run {rid} has state in this repository")
    return rid


def check_source(source: str) -> str:
    s = (source or "local").strip()
    if s not in SOURCES:
        raise BadRequest("source must be local or team")
    return s


def team_mode(root: str) -> Dict[str, Any]:
    """`{configured, note}` — whether this repo is team-connected, by the session channel's own
    derivation. A manifest that cannot be read makes the mode UNKNOWN, which is said, not guessed."""
    from ..session_transport import SessionTransportUnavailable, transport_kind_for_mode
    try:
        return {"configured": transport_kind_for_mode(root) == "postgres", "note": ""}
    except SessionTransportUnavailable as exc:
        return {"configured": False, "unknown": True, "note": str(exc)[:300]}


def _clip(s: Any) -> str:
    s = "" if s is None else str(s)
    return s if len(s) <= MAX_TEXT else s[:MAX_TEXT] + " …"


def _mtime_iso(path: str) -> str:
    try:
        return datetime.fromtimestamp(os.path.getmtime(path), timezone.utc).isoformat()
    except OSError:
        return ""


# ----------------------------------------------------------------------------- projections
def _approach_view(store: Any) -> Dict[str, Any]:
    from ..brainstorm import load_approved_approach
    try:
        h = load_approved_approach(store)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        return {"state": "malformed", "note": f"the approved approach is present but unreadable "
                f"({type(exc).__name__}) — it is kept, not lost"}
    if h is None:
        return {"state": "absent", "note": "no approved approach on this run — the plan is "
                "written when a brainstorm is approved (/mokata:brainstorm)"}
    a = h.approach
    return {
        "state": "ok", "topic": _clip(h.topic), "name": _clip(a.name), "summary": _clip(a.summary),
        "pros": [_clip(x) for x in a.pros], "cons": [_clip(x) for x in a.cons],
        "targets": [_clip(x) for x in a.targets],
        "decisions": [{"id": _clip(d.id), "statement": _clip(d.statement),
                       "rationale": _clip(d.rationale_ref), "about_code": list(d.about_code),
                       "deferred": [x.to_dict() for x in d.deferred]} for d in a.decisions],
        "questions": [{"text": _clip(q.text), "answer": _clip(q.answer)}
                      for q in h.answered_questions],
        "approver": _clip(h.approver), "approved_at": _clip(h.approved_at),
        "domains": list(h.domains),
    }


def _spec_view(store: Any, run_id: str, version: int, *, history: bool,
               tests: Optional[Tuple[set, set]]) -> Dict[str, Any]:
    from ..engine import spec_history as SH
    from ..engine.spec_anchor import ac_anchor_lines
    from ..engine.spec_gate import read_emitted_spec
    current = SH.current_version(store)
    if version and version != current:
        if not history:
            return {"state": "no-such-version", "current_version": current,
                    "note": "the team bundle carries the current spec only"}
        row = SH.spec_at_version(store, run_id, int(version))
        spec, malformed, shown = row["spec"], row["malformed"], int(version)
        if spec is None and not malformed:
            return {"state": "no-such-version", "version": shown, "current_version": current,
                    "note": f"this run has no spec v{shown} (current is v{current})"}
    else:
        spec, malformed = read_emitted_spec(store)
        shown = current
    if malformed:
        from ..engine.spec_gate import SPEC_MALFORMED_MESSAGE
        return {"state": "malformed", "current_version": current, "note": SPEC_MALFORMED_MESSAGE}
    if spec is None:
        from ..cli_commands.spec import NO_SPEC_RECOVERY
        return {"state": "absent", "current_version": current, "note": NO_SPEC_RECOVERY}
    red, green = tests if tests is not None else (set(), set())
    crit = []
    for c in spec.criteria:
        d = c.to_dict()
        d["text"] = _clip(d.get("text"))
        if tests is not None:
            d["test"] = "passing" if c.id in green else "failing" if c.id in red else "none"
        crit.append(d)
    out: Dict[str, Any] = {
        "state": "ok", "version": shown or 1, "current_version": current,
        "superseded": bool(shown and shown != current), "title": _clip(spec.title),
        "approach": _clip(spec.approach or ""), "domains": list(spec.domains),
        "criteria": crit, "anchor_lines": ac_anchor_lines(spec),
        "tests": None if tests is None else {"failing": sorted(red), "passing": sorted(green)},
    }
    if spec.scope is not None:
        out["scope"] = spec.scope.to_dict()
    return out


def _amendment(store: Any, run_id: str) -> Dict[str, Any]:
    from ..spec_scope import NO_AMEND, amend_from_state, amend_key
    try:
        rec = amend_from_state(store.read(amend_key(run_id)))
    except (OSError, AttributeError):
        return {"open": False}
    if rec is NO_AMEND:
        return {"open": False}
    return {"open": bool(rec.is_open), "status": rec.status, "from_version": rec.from_version,
            "to_version": rec.to_version, "reason": _clip(rec.reason), "item": _clip(rec.item),
            "red_owed": list(rec.red_owed)}


# ----------------------------------------------------------------------------- runs
def _sessions_by_run(root: str) -> Dict[str, List[str]]:
    from . import sessions as SS
    out: Dict[str, List[str]] = {}
    for r in SS.sessions(root):
        if r.run:
            out.setdefault(r.run, []).append(r.id)
    return out


def _recency(root: str, rid: str) -> float:
    from ..run_resolver import RUN_STATE_PREFIXES
    sd, best = _state_dir(root), 0.0
    for p in RUN_STATE_PREFIXES:
        try:
            best = max(best, os.path.getmtime(os.path.join(sd, p + rid + ".json")))
        except OSError:
            pass
    return best


def _tier(sd: str, rid: str, drafts: Dict[str, Any]) -> int:
    """2 = the run has a spec (emitted, or drafted and awaiting approval); 1 = an approved
    approach only; 0 = nothing to show yet (a TDD phase / checkpoint). Two stats, no read."""
    from ..run_resolver import APPROACH_PREFIX, SPEC_PREFIX
    if rid in drafts or os.path.isfile(os.path.join(sd, SPEC_PREFIX + rid + ".json")):
        return 2
    return 1 if os.path.isfile(os.path.join(sd, APPROACH_PREFIX + rid + ".json")) else 0


def local_runs(root: str) -> Dict[str, Any]:
    """The run chooser. PLAN-VISIBLE (0.0.25): runs that hold a plan or a spec come FIRST, then
    the rest by recency — and a run with a spec (emitted, or drafted and awaiting approval)
    ahead of a run with only an approach. A long-lived repo collects thousands of runs; ordered
    by recency alone the run with the spec fell past the `MAX_RUNS` cut and the newest run with
    nothing on it became the default, so the plan and spec never showed in the Plan view."""
    from ..engine.spec_gate import read_emitted_spec
    sd = _state_dir(root)
    every = local_run_ids(root)
    drafts = pending_specs(root)
    tier = {r: _tier(sd, r, drafts) for r in every}
    planned = {r for r, t in tier.items() if t}
    ids = sorted(every, key=lambda r: (tier[r], _recency(root, r)), reverse=True)[:MAX_RUNS]
    by_run = _sessions_by_run(root)
    rows = []
    for rid in ids:
        store = _run_store(root, rid)
        ap = _approach_view(store)
        spec, malformed = read_emitted_spec(store)
        title = (spec.title if spec is not None else "") or ap.get("topic") or ""
        rows.append({"id": rid, "short": rid[:8], "title": _clip(title)[:200],
                     "approach": ap["state"], "spec": "malformed" if malformed else
                     ("ok" if spec is not None else
                      "awaiting approval" if drafts.get(rid) else "absent"),
                     "has_plan": rid in planned,
                     "sessions": by_run.get(rid, []), "updated": _recency(root, rid)})
    return {"sample": False, "source": "local", "runs": rows,
            "default": _default_run(root, ids, planned),
            "counts": {"total": len(every), "with_plan": len(planned),
                       "with_spec": sum(1 for t in tier.values() if t == 2), "listed": len(ids)},
            "team": team_mode(root)}


def _default_run(root: str, ids: List[str], planned: "set | None" = None) -> Optional[str]:
    """THE resolver's run when this store holds it AND it has a plan or spec; else the first
    listed run (the list is already best-first: spec, then plan, then recency); else the
    resolver's run. A run with nothing to show is never preferred over one with a plan — that
    was the 'plan not visible' report."""
    from ..run_resolver import resolve_run
    planned = planned or set()
    rid = resolve_run(root).run_id
    if rid in ids and rid in planned:
        return rid
    for r in ids:
        if r in planned:
            return r
    if rid in ids:
        return rid
    return ids[0] if ids else None


# ----------------------------------------------------------------------------- saved plan files
SAVED_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SAVED_WHERE = ("internal", "exported")
MAX_SAVED_PLANS = 200
MAX_PLAN_BYTES = 256 * 1024


def _saved_dirs(root: str) -> Dict[str, str]:
    """`internal` = `.mokata/temp_local/plans` (written at approval), `exported` = the repo's
    `plans/` (`mokata plan export`) — the two places `mokata plan` itself reads and writes."""
    from ..plans import PLANS_DIRNAME
    return {"internal": os.path.join(os.path.dirname(_state_dir(root)), PLANS_DIRNAME),
            "exported": os.path.join(root, PLANS_DIRNAME)}


def _saved_file(root: str, where: str, slug: str) -> Optional[str]:
    """The plan file's path, or None — never a symlink, never outside its folder."""
    base = _saved_dirs(root)[where]
    path = os.path.join(base, slug + ".md")
    try:
        if os.path.islink(path) or not os.path.isfile(path):
            return None
        if os.path.dirname(os.path.realpath(path)) != os.path.realpath(base):
            return None
    except OSError:
        return None
    return path


def saved_plans(root: str) -> Dict[str, Any]:
    """Every saved plan file, newest first — READ-ONLY (names + mtimes, no content)."""
    from ..plans import list_plans
    rows: List[Dict[str, Any]] = []
    for where, base in _saved_dirs(root).items():
        for slug in list_plans(base):
            if not SAVED_SLUG_RE.match(slug):
                continue
            path = _saved_file(root, where, slug)
            if path is None:
                continue
            try:
                mt = os.path.getmtime(path)
            except OSError:
                continue
            rows.append({"slug": slug, "where": where, "updated": mt,
                         "path": str(name_of(path, root))})
    rows.sort(key=lambda r: r["updated"], reverse=True)
    return {"plans": rows[:MAX_SAVED_PLANS], "total": len(rows),
            "dirs": {w: str(name_of(d, root))
                     for w, d in _saved_dirs(root).items()}}


def saved_plan(root: str, slug: str, where: str) -> Dict[str, Any]:
    """One saved plan file's text (bounded). 400 on a bad name/place or a missing file."""
    w = (where or "internal").strip()
    if w not in SAVED_WHERE:
        raise BadRequest("where must be internal or exported")
    s = (slug or "").strip()
    if not SAVED_SLUG_RE.match(s):
        raise BadRequest("plan must be a saved plan name (no path)")
    path = _saved_file(root, w, s)
    if path is None:
        raise BadRequest(f"no saved plan {s} in this repository")
    with open(path, "rb") as fh:
        raw = fh.read(MAX_PLAN_BYTES + 1)
    return {"slug": s, "where": w, "path": str(name_of(path, root)),
            "updated": os.path.getmtime(path), "truncated": len(raw) > MAX_PLAN_BYTES,
            "text": raw[:MAX_PLAN_BYTES].decode("utf-8", errors="replace")}


SPEC_TOOLS = ("spec_emit", "spec_amend")


def pending_specs(root: str) -> Dict[str, List[Dict[str, Any]]]:
    """Spec writes PROPOSED but not committed, by run: `spec_emit` stages a proposal and writes
    nothing until a human runs `mokata approve <id>` AND the agent re-calls with that id. Until
    then the run has no spec — and the Plan view used to say only "no spec", so a drafted spec
    looked lost. Read-only over `approval.pending` (live = unexpired and unused)."""
    from .. import approval
    out: Dict[str, List[Dict[str, Any]]] = {}
    try:
        live = approval.pending(root)
    except (OSError, ValueError, TypeError):
        return out
    for p in live:
        if p.tool not in SPEC_TOOLS:
            continue
        out.setdefault(p.run_id, []).append({
            "proposal_id": p.proposal_id, "tool": p.tool, "status": p.status,
            "summary": _clip(p.summary)[:300], "preview": str(p.preview or "")[:8000],
            "expires_in": p.expires_in(), "approve": f"mokata approve {p.proposal_id}"})
    return out


def plan_files_for(root: str, topic: str) -> List[Dict[str, str]]:
    """The saved plan file(s) a run's approved topic was written to (same slug rule)."""
    if not topic:
        return []
    from ..plans import plan_slug
    slug = plan_slug(topic)
    return [{"slug": slug, "where": w, "path": str(name_of(p, root))}
            for w in SAVED_WHERE for p in [_saved_file(root, w, slug)] if p]


# ----------------------------------------------------------------------------- team
class _BundleStore:
    """A read-only `store` over a pulled bundle's `state` section, so the SAME readers
    (`load_approved_approach`, `read_emitted_spec`, `spec_history.current_version`) answer."""

    def __init__(self, state: Dict[str, Any]) -> None:
        self._s = state if isinstance(state, dict) else {}

    def read(self, key: str) -> Any:
        return self._s.get(key)


def _team_transport(root: str) -> Any:
    from ..session_transport import make_transport
    return make_transport("postgres", root)


def _unreachable(exc: BaseException) -> Dict[str, Any]:
    return {"sample": False, "source": "team", "state": "team-unreachable",
            "note": f"team store unreachable — {type(exc).__name__}: {str(exc)[:300]}. "
                    "Nothing is shown in its place; local data stays under the Local source."}


def _team_bundles(root: str) -> List[Tuple[str, Dict[str, Any]]]:
    """(tag, verified bundle) for every bundle on the team transport. Raises on an unreachable
    store — the caller turns that into the explicit unreachable state."""
    from ..session_bundle import SessionBundleError, parse_bundle
    t = _team_transport(root)
    out = []
    for tag in t.list_tags()[:MAX_RUNS * 4]:
        blob = t.read_bundle(tag)
        if blob is None:
            continue
        try:
            out.append((tag, parse_bundle(blob)))
        except SessionBundleError:
            continue                      # a corrupt bundle is skipped, as `session list` does
    return out


def _bundle_run(b: Dict[str, Any]) -> Optional[str]:
    rid = b.get("run_id") or (b.get("resume") or {}).get("run_id")
    return rid if isinstance(rid, str) and RUN_ID_RE.match(rid) else None


def team_runs(root: str) -> Dict[str, Any]:
    mode = team_mode(root)
    if not mode["configured"]:
        raise BadRequest("this repository is not team-connected — the team source is not "
                         "available (`mokata team connect`)")
    try:
        bundles = _team_bundles(root)
    except Exception as exc:  # noqa: BLE001 — ANY DB/driver/DSN fault is the explicit state
        return _unreachable(exc)
    rows: Dict[str, Dict[str, Any]] = {}
    for tag, b in bundles:
        rid = _bundle_run(b)
        if not rid:
            continue
        prov = b.get("provenance") or {}
        store = _BundleStore(b.get("state") or {})
        ap = _approach_view(store)
        from ..engine.spec_gate import read_emitted_spec
        spec, malformed = read_emitted_spec(store)
        row = {"id": rid, "short": rid[:8], "tag": tag, "author": _clip(prov.get("author")),
               "created": _clip(prov.get("created")),
               "title": _clip((spec.title if spec is not None else "") or ap.get("topic") or "")[:200],
               "approach": ap["state"], "spec": "malformed" if malformed else
               ("ok" if spec is not None else "absent"), "sessions": []}
        if rid not in rows or row["created"] > rows[rid]["created"]:
            rows[rid] = row
    ordered = sorted(rows.values(), key=lambda r: r["created"], reverse=True)
    return {"sample": False, "source": "team", "state": "ok", "runs": ordered,
            "default": ordered[0]["id"] if ordered else None, "team": mode}


# ----------------------------------------------------------------------------- the plan + spec
def plan(root: str, run_id: str, source: str = "local", version: int = 0) -> Dict[str, Any]:
    src = check_source(source)
    if src == "team":
        return _team_plan(root, check_run_id(run_id), version)
    rid = check_local_run(root, run_id)
    store = _run_store(root, rid)
    from ..engine import spec_history as SH
    from ..tdd_state import load as load_tdd
    try:
        tests = load_tdd(store, rid)
    except (OSError, ValueError, TypeError):
        tests = None
    versions = SH.spec_versions(store, rid)
    approach = _approach_view(store)
    return {"sample": False, "source": "local", "label": "local state", "run": rid,
            "approach": approach, "plan_files": plan_files_for(root, approach.get("topic", "")),
            "pending_spec": pending_specs(root).get(rid, []),
            "spec": _spec_view(store, rid, version, history=True, tests=tests),
            "versions": versions, "amendment": _amendment(store, rid)}


def _team_plan(root: str, rid: str, version: int) -> Dict[str, Any]:
    if not team_mode(root)["configured"]:
        raise BadRequest("this repository is not team-connected — the team source is not available")
    try:
        bundles = _team_bundles(root)
    except Exception as exc:  # noqa: BLE001 — ANY DB/driver/DSN fault is the explicit state
        return _unreachable(exc)
    mine = [(t, b) for t, b in bundles if _bundle_run(b) == rid]
    if not mine:
        raise BadRequest(f"no run {rid} in the team store")
    tag, b = max(mine, key=lambda tb: str((tb[1].get("provenance") or {}).get("created", "")))
    store = _BundleStore(b.get("state") or {})
    from ..engine import spec_history as SH
    cur = SH.current_version(store)
    prov = b.get("provenance") or {}
    return {"sample": False, "source": "team", "state": "ok",
            "label": f"team store · bundle {tag}", "run": rid, "tag": tag,
            "author": _clip(prov.get("author")), "created": _clip(prov.get("created")),
            "approach": _approach_view(store),
            "spec": _spec_view(store, rid, version, history=False, tests=None),
            "versions": [{"version": cur, "current": True, "present": cur > 0,
                          "malformed": False, "key": "bundle", "title": "", "ac_count": 0}]
            if cur else [],
            "versions_note": "the team bundle carries the current spec; earlier versions stay "
                             "in the author's local state",
            "amendment": {"open": False}}


# ----------------------------------------------------------------------------- the lifecycle
def _parse_ts(ts: Any) -> float:
    if not isinstance(ts, str) or not ts:
        return 0.0
    try:
        d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()


def _run_events(root: str, rid: str) -> List[Dict[str, Any]]:
    """This run's progress events, oldest first — read BACKWARD under the log's own byte cap and
    stopped at `MAX_EVENTS`, so a busy shared log never makes this read unbounded."""
    from ..progress_events import ProgressLog
    out = []
    for e in ProgressLog.from_state_dir(_state_dir(root)).read_events_backward():
        if e.get("run_id") == rid:
            out.append(e)
            if len(out) >= MAX_EVENTS:
                break
    out.reverse()
    return out


def _ledger_rows(root: str, rid: str) -> List[Dict[str, Any]]:
    """The audit ledger's rows for this run that mark its lifecycle (registration, amendments) —
    the ledger is JSONL, read backward under `LEDGER_BYTE_CAP` with the progress log's reader."""
    from .. import MOKATA_DIR
    from ..govern.ledger import AuditLedger
    from ..progress_events import ProgressLog
    path = AuditLedger.path_for(os.path.join(root, MOKATA_DIR))
    out = []
    for e in ProgressLog(path).read_events_backward(byte_cap=LEDGER_BYTE_CAP):
        if e.get("run") != rid:
            continue
        if e.get("kind") == "spec_amend" or (e.get("kind") == "checkpoint"
                                             and e.get("phase") == "registered"):
            out.append(e)
            if len(out) >= MAX_EVENTS:
                break
    out.reverse()
    return out


def _mark(ts: str, kind: str, stage: str, label: str, **extra: Any) -> Dict[str, Any]:
    return {"ts": ts or "", "t": _parse_ts(ts), "kind": kind, "stage": stage,
            "label": _clip(label)[:300], **extra}


def lifecycle(root: str, run_id: str) -> Dict[str, Any]:
    from ..govern.resume import CHECKPOINT_PREFIX, PipelineCheckpoint
    from ..progress_events import GATE_BLOCK, REVIEW_VERDICT, RUN_FINISHED, STAGE_ENTER
    from ..tdd_state import load as load_tdd, state_key as tdd_key
    rid = check_local_run(root, run_id)
    stages = lifecycle_stages()
    store = _run_store(root, rid)
    marks: List[Dict[str, Any]] = []
    entered: Dict[str, str] = {}
    finished_at = ""

    for e in _run_events(root, rid):
        typ, stage, ts, data = e.get("type"), str(e.get("stage") or ""), e.get("ts") or "", \
            e.get("data") if isinstance(e.get("data"), dict) else {}
        if typ == STAGE_ENTER and stage in stages:
            entered.setdefault(stage, ts)
            marks.append(_mark(ts, "stage_enter", stage, f"entered {stage}"))
        elif typ == GATE_BLOCK:
            marks.append(_mark(ts, "gate_block", stage, f"gate {data.get('gate', '?')} blocked "
                               f"{data.get('path', '')}".strip(), gate=_clip(data.get("gate"))))
        elif typ == REVIEW_VERDICT:
            ok = bool(data.get("passed"))
            entered.setdefault("review", ts)
            marks.append(_mark(ts, "review_verdict", "review",
                               "review passed" if ok else "review did not pass", passed=ok,
                               independent=bool(data.get("independent"))))
        elif typ == RUN_FINISHED:
            finished_at = finished_at or ts
            marks.append(_mark(ts, "run_finished", "ship",
                               f"run finished ({data.get('landing', '?')})"))

    registered = ""
    for e in _ledger_rows(root, rid):
        ts = e.get("at") or ""
        if e.get("kind") == "checkpoint":
            registered = registered or ts
            marks.append(_mark(ts, "registered", stages[0], "run registered"))
        elif e.get("decision") == "aborted":
            marks.append(_mark(ts, "spec_amend", "spec", f"amendment v{e.get('from_version')} "
                               "abandoned", reason=_clip(e.get("reason"))))
        else:
            marks.append(_mark(ts, "spec_amend", "spec", f"spec amended v{e.get('from_version')}"
                               f" → v{e.get('to_version')}", reason=_clip(e.get("reason"))))

    # Evidence the stage arc has from state (not events): an approved plan, an emitted spec, a
    # failing test on record, the checkpoint's completion stamp.
    ap = _approach_view(store)
    from ..engine.spec_gate import read_emitted_spec
    spec, _malformed = read_emitted_spec(store)
    if ap.get("state") == "ok":
        marks.append(_mark(ap.get("approved_at", ""), "approved", "brainstorm",
                           f"plan approved by {ap.get('approver') or 'the human'}"))
    try:
        red, green = load_tdd(store, rid)
    except (OSError, ValueError, TypeError):
        red, green = set(), set()
    reached: Dict[str, str] = dict(entered)
    if ap.get("state") == "ok":
        reached.setdefault("brainstorm", ap.get("approved_at", ""))
    if spec is not None:
        reached.setdefault("spec", "")
    if red or green:
        reached.setdefault("test", _mtime_iso(os.path.join(_state_dir(root),
                                                           tdd_key(rid) + ".json")))
    cp = PipelineCheckpoint(store, rid)
    if store.read(CHECKPOINT_PREFIX + rid) is not None and cp.completed_at:
        finished_at = finished_at or cp.completed_at
    if finished_at:
        reached.setdefault("ship", finished_at)

    amend = _amendment(store, rid)
    idx = max([stages.index(s) for s in reached if s in stages], default=-1)
    regressed = bool(amend.get("open")) and "spec" in stages
    if regressed:
        idx = stages.index("spec")
    rows = []
    for i, s in enumerate(stages):
        if finished_at and not regressed:
            status = "done"
        elif i < idx:
            status = "done"
        elif i == idx:
            status = "current"
        else:
            status = "pending"
        rows.append({"stage": s, "status": status, "entered": entered.get(s, ""),
                     "evidence": "recorded" if s in reached else
                     ("implied" if status == "done" else ""),
                     "blocks": sum(1 for m in marks if m["kind"] == "gate_block"
                                   and m["stage"] == s),
                     "amendments": sum(1 for m in marks if m["kind"] == "spec_amend"
                                       and m["stage"] == s)})
    marks.sort(key=lambda m: (m["t"] == 0, m["t"]))
    times = [m["t"] for m in marks if m["t"]]
    firsts = [t for t in [_parse_ts(registered)] + [_parse_ts(v) for v in entered.values()] if t]
    started = min(firsts) if firsts else (min(times) if times else 0.0)
    return {"sample": False, "run": rid, "stages": rows,
            "current": stages[idx] if 0 <= idx < len(stages) and not
            (finished_at and not regressed) else None,
            "finished": bool(finished_at) and not regressed, "finished_at": finished_at,
            "regressed": regressed, "amendment": amend,
            "started": started, "started_basis": "registration" if registered else
            ("first stage_enter" if entered else ("first mark" if times else "")),
            "marks": marks[-MAX_EVENTS:], "test_record": {"failing": len(red),
                                                           "passing": len(green)}}


__all__ = ["RUN_ID_RE", "SOURCES", "lifecycle_stages", "local_run_ids", "check_run_id",
           "check_local_run", "check_source", "team_mode", "local_runs", "team_runs", "plan",
           "lifecycle"]
