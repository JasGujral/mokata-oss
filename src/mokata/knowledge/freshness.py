"""GR.S4 — the graph FRESHNESS contract (read-time, never a daemon).

doc 85 binds this hard: NO watcher, NO daemon, NO background process. Freshness is a
READ-TIME contract — every query reconciles the graph against three cheap signals BEFORE it
answers, and rebuilds a KNOWN-stale index rather than serving it:

  1. a session DIRTY-SET the `PostToolUse` async observability hook appends to (in-harness
     edits) — an atomic O(1) append to a transient run-state file (GR.S1-cache precedent,
     ungated: P2 is about DURABLE writes, this is run-tracking);
  2. one cheap `.git/HEAD` change probe (zero-subprocess SHA read) — HEAD moved ⇒ ONE batched
     `git diff --name-only <last-indexed-SHA>` (catches branch switch / commit / pull, and
     editor/out-of-band changes);
  3. a cold-start index walk — ONCE per session — to seed the mtime/hash baseline;
  4. (H-6, 0.0.16) the CODE-ANCHOR tripwire — `about_code` anchors whose DURABLE recorded
     fingerprint has moved (`anchor_fingerprints`). The first three signals all die with the
     session; this one is the same knowledge arriving from a baseline that outlived it, which
     is why an out-of-band edit to an anchored file is a BEFORE-answer signal rather than
     something the post-answer recheck catches afterwards. Bounded by `ANCHOR_SCAN_CAP` with an
     honest costed note, and forced ONCE per state per session (see `FreshnessState`).

FRESHNESS-BEFORE-ANSWER INVARIANT (re-groom #5): a graph KNOWN stale never answers. A stale
index rebuilds first (AST incremental re-parse; CRG refresh via GR.S2(k)). Debounce only
COALESCES pending signals into ONE rebuild — it never permits a stale answer. A rebuild
FAILURE answers from the AST floor on CURRENT files with a loud classed note, never from stale
graph data.

PERF CONTRACT (option B): steady-state cost tracks CHURN, never repo size. The warm path
(nothing changed) is a dirty-set read + one HEAD stat + one small state read + TWO bounded git
calls — `git diff --name-only <last-indexed-sha>` and `git ls-files --others --exclude-standard`;
no walk. Reconcile is bounded by changed files; the dirty-set past a threshold collapses to ONE
batched git diff; the full mtime walk is cold-start only, once per session.

⚠ THE LETTER OF THIS CONTRACT WAS AMENDED AT 0.0.21 STAGE 14; THE INTENT WAS NOT. It read "no git
subprocess" on the warm path, and that clause was the only thing keeping signal 2 from delivering
the out-of-band detection this docstring claims for it two paragraphs above. Measured on a
2419-file checkout: that diff is 9.2 ms with output ∝ CHANGED FILES, while the mtime/hash walk it
stands in for is 1615 ms with cost ∝ REPO SIZE. "Cost tracks churn, never repo size" is better
served after the amendment than before it. A signal had been switched off to protect a sentence;
the sentence changed instead.

⚠ AMENDED A SECOND TIME, in the same pass and for the same reason, and the second one is recorded
separately because it was NOT part of the first decision. `git ls-files --others --exclude-standard`
is 30 ms on a 787-.py checkout. It buys the one case no signal in this module could see at all: a
source file git has never been told about. `--exclude-standard` is what keeps it bounded — build
trees, venvs and caches are gitignored and never reach the extension filter. 39 ms of warm-path git
against a 1615 ms walk, and an untracked file that used to be invisible until someone ran
`git add`. The numbers are in the two functions; nothing here is asserted without one.

Freshness CAP hit ⇒ an honest costed note on the answer, NEVER a block.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional
from ..repo_paths import name_of

# --- layout (transient run-state under temp_local — ungated, GR.S1-cache precedent) -------
DIRTY_DIRNAME = "knowledge_freshness"
# 🔴 REVIEW FINDING A3 — the content-hash sentinel for a path that EXISTS and CANNOT BE HASHED
# (a `*.py` symlink to a directory, a mode-000 file). It is deliberately not a hex digest, so no real
# `file_fingerprint` can ever equal it: the moment the path becomes readable, `is_stale` says True.
# "Known and unhashable" is a THIRD state beside "indexed" and "absent" (§7g), and collapsing it into
# either of those is a forever-rebuild loop or a missed change.
UNFINGERPRINTABLE = "unfingerprintable"

# 🔴 REVIEW FINDING 3-5 — the ABSENCE TOMBSTONE. `_advance_baseline` used to POP a path that no
# longer existed, so a reconciled DELETE followed by a byte-identical RECREATE had nothing to compare
# against and no signal said the file was back (finding A5, filed at stage 14 as unfixable). A
# tombstone makes "indexed, reconciled as gone" a THIRD state beside "indexed" and "unknown" (§7g) —
# the same idiom `UNFINGERPRINTABLE` already uses one line above — and "tombstoned but now on disk"
# becomes detectable at zero added cost.
ABSENT = "absent"

FRESHNESS_STATE_PREFIX = "graph_freshness__"          # StateStore, session-scoped
FRESHNESS_INDEX_PREFIX = "graph_freshness_index__"    # StateStore, session-scoped (cold baseline)

# --- caps (option-B perf + honest costed notes) -------------------------------------------
DIRTY_BATCH_THRESHOLD = 50        # dirty-set past this ⇒ ONE batched git diff, not per-file
FRESHNESS_CHANGE_CAP = 2000       # changed set past this ⇒ costed note attached, never a block
INDEX_SIZE_CAP_FILES = 20000      # cold walk past this ⇒ honest costed note, walk bounded

# 🔴 REVIEW FINDING 4-8 — SIGNAL 2b WAS THE ONLY SIGNAL WITH NO CAP AND NO NOTE. Every other signal
# in `_reconcile` is bounded and says so when the bound bites: the dirty-set collapses past
# DIRTY_BATCH_THRESHOLD, the cold walk stops at INDEX_SIZE_CAP_FILES, the anchor scan at
# ANCHOR_SCAN_CAP, the changed set at FRESHNESS_CHANGE_CAP. 2b hashed EVERY drifted candidate, so a
# branch switch, a `git stash pop` or a restore moving 8000 files paid 8000 hashes inside a read —
# precisely the ∝ repo-size cost the stat-only design was argued for avoiding, arriving by the back
# door, with nothing on the answer to say the read had just become expensive.
#
# ⭐ AND THE CAP FAILS LOUD (§7e). Over-cap candidates are NOT dropped — dropping them would make an
# unaffordable confirmation read as "clean", i.e. an instrument failing open, which is a false
# green. They are reported CHANGED without a hash, which costs a redundant rebuild and never an
# unreported edit, and the note says the confirmation was skipped rather than passed.
BASELINE_DRIFT_HASH_CAP = 500     # drifted candidates hashed per pass; the rest rebuild unconfirmed

# 🔴 REVIEW FINDING 4-8(b) — TOMBSTONES ACCUMULATED FOREVER. `_advance_baseline` records a
# reconciled deletion as an ABSENT entry so a byte-identical recreate is still noticed (finding A5),
# and NOTHING ever removed one. Every file ever deleted in the repo's life stayed in the persisted
# baseline and was `stat`ed on every 2b pass, so the cost of the signal grew with the repo's
# HISTORY rather than its contents — monotonically, in a file the user cannot see.
#
# The cap drops the OLDEST tombstones first (dict order is insertion order, and a tombstone is
# written when the deletion reconciles, so oldest-first is least-recently-deleted-first) and SAYS
# which paths lost their tombstone: an A5-shaped recreate of one of those is then a gap, and a
# disclosed gap is the only honest kind.
BASELINE_TOMBSTONE_CAP = 5000     # ABSENT entries kept; past this the oldest are dropped + noted


# --- how the freshness session was determined (Fix A, 0.0.21 stage 14) -------------------
# Three outcomes, three representations (doc 85 §7g). `unbound` is NOT "no change" — it is
# "the per-session signals cannot see anything", and a caller that cannot tell those apart is
# the defect this stage exists to close.
SESSION_GIVEN = "given"            # the caller handed us an id outright
SESSION_UNBOUND = "unbound"        # nothing resolved — a fresh per-process id that CANNOT
                                   # cross a process boundary, so signals 1 and 3 are blind


def resolve_freshness_session(root: str, session_id: Optional[str] = None):
    """`(key, basis)` — the id that scopes this repo's freshness run-state, and WHICH RUNG
    answered.

    ⭐ THE WHOLE POINT. The `PostToolUse` hook and the process that later asks a graph question
    are DIFFERENT PROCESSES. Signals 1 (the dirty set) and 3 (the cold baseline) are written by
    one and read by the other, so they only work when both land on the SAME key. Minting
    `uuid4()` per process — which is what `_sid(None)` does — guarantees they never do: the hook
    writes `dirty__<harness session>` and the reader drains `dirty__<its own uuid4>`.

    `run_resolver` already answers "which run is this caller in" ACROSS processes, with a
    nine-rung ladder and a `basis` naming the rung: the hook lands on BOUND (Claude Code's
    `session_id`, written by SessionStart — harness gap #25642), an MCP process that registered
    its run lands on OWN. Reusing it is the fix; a second resolution beside it was the bug.

    ⚠ AND REUSING IT ON THE READER ALONE WAS NOT ENOUGH — see `_dirty_path`. The first version of
    fix A rewired this side only, and the WRITER kept keying on `_sid`, so the two namespaces still
    never met and signal 1 was still dead. The independent review measured that; both sides resolve
    through the ladder now — the writer through `resolve_harness_session`, below, NOT through this
    function.

    Never raises and never writes — `resolve_run` guarantees both, and freshness is a read-time
    enhancement that must never cost a query its answer.

    🔴 REVIEW FINDING A7 (independent review, 0.0.21 stage 14) — AN EXPLICIT ID GOES IN AS `run_id`
    AS WELL. It used to be passed as `session_id` alone, which is the BOUND rung's INPUT rather than
    an answer, so an ambient `MOKATA_SESSION_ID` won at the PINNED rung and the caller's explicit id
    was SILENTLY DISCARDED. Measured: `resolve_freshness_session(root, "caller-gave-this-id")` with a
    pin set returned `("an-ambient-run", "pinned")`, and the controller then read and wrote state
    under a key its caller never asked for. It also made `SESSION_GIVEN` — *"the caller handed us an
    id outright"* — a DEAD representation whenever a pin existed (§7g). `run_id` is the ladder's own
    EXPLICIT rung, above PINNED, which is the ordering the ladder already documents.

    ⛔ AND THE TWO IDS ARE DIFFERENT FACTS, WHICH IS WHY THERE ARE TWO ENTRY POINTS BELOW. My first
    attempt at findings A1 and A7 put both through THIS function, and A7's change then BROKE A1's:
    passing the id as `run_id` makes the ladder answer at EXPLICIT and never reach BOUND — so the
    hook, whose id is Claude Code's `session_id` and NOT a run id, wrote under the raw harness id
    again and the two namespaces separated a second time. Caught by the A1 test, once that test had
    the right asymmetry. A caller's PIN and the harness's SESSION ID are not interchangeable (§7g),
    so each has its own named entry point over ONE body (§7f — one rule, one implementation)."""
    return _resolve_session(root, session_id=session_id, run_id=session_id)


def resolve_harness_session(root: str, claude_session_id: Optional[str] = None):
    """`(key, basis)` for a process that holds CLAUDE CODE'S `session_id` — the `PostToolUse` hook.

    ⭐ That id is the BOUND rung's INPUT, never a run id, so it is passed as `session_id` ONLY. The
    ladder translates it into the run it is bound to, which is what lets a query process — which
    cannot see it at all (harness gap #25642) — reach the same key from EVIDENCE/OWN/SINGLE/LIVE.

    ⛔ Do NOT "simplify" this into `resolve_freshness_session`. They differ in exactly one argument
    and that argument is the difference between signal 1 working and signal 1 being dead; review
    finding A1 is what that costs, twice."""
    return _resolve_session(root, session_id=claude_session_id, run_id=None)


def _resolve_session(root: str, *, session_id: Optional[str], run_id: Optional[str]):
    """THE body both entry points above share. One rule, one implementation (§7f)."""
    try:
        from ..run_resolver import resolve_run
        res = resolve_run(root, session_id=session_id, run_id=run_id)
        if res.resolved:
            return res.run_id, res.basis
    except Exception:  # noqa: BLE001
        pass
    if session_id:
        return session_id, SESSION_GIVEN
    return _sid(None), SESSION_UNBOUND


# ==========================================================================================
# session id (default when a caller doesn't pass one)
# ==========================================================================================
def _sid(session_id: Optional[str]) -> str:
    if session_id:
        return session_id
    try:
        from ..session import current_session_id
        return current_session_id()
    except Exception:  # noqa: BLE001 — freshness must never break on identity resolution
        return "default"


# ==========================================================================================
# 1 — the dirty-set (atomic O(1) append, session-scoped, ungated, never raises)
# ==========================================================================================
def _dirty_dir(root: str) -> str:
    from .. import MOKATA_DIR, TEMP_LOCAL_DIRNAME
    return os.path.join(root, MOKATA_DIR, TEMP_LOCAL_DIRNAME, DIRTY_DIRNAME)


def _dirty_path(root: str, session_id: Optional[str]) -> str:
    """The dirty-set file for the session the LADDER resolves — the same key the reader computes.

    🔴 REVIEW FINDING A1 (independent review, 0.0.21 stage 14). Fix A rewired the READER to the
    ladder and left this, the WRITER, on `_sid` — which returns Claude Code's `session_id` verbatim.
    So the two still keyed on different namespaces and signal 1 was STILL dead: the hook wrote
    `dirty__<harness session>.log` and the query process drained `dirty__<run id>.log`. Measured by
    the reviewer across three cases; the only one that met was a `MOKATA_SESSION_ID` pinned by hand
    to the Claude Code session id, which nothing in `src/` ever does.

    ⭐ THE LADDER IS WHAT TRANSLATES THE TWO, and its BOUND rung exists for exactly this: Claude
    Code's `session_id` → the run it is bound to. The hook is the one process that HAS that id (the
    MCP/query process cannot see it — harness gap #25642), so the hook resolves WITH it and the
    reader resolves WITHOUT it, and when a run exists they land on the same run id from different
    rungs.

    🔴 REVIEW FINDING 3-8 — AND THE SENTENCE THAT USED TO END THIS DOCSTRING WAS FALSE. It said
    *"when nothing resolves, both fall to the per-process id and the pair is honestly partitioned"*.
    They do not both fall there: with no pin and no registered run the WRITER returns the raw harness
    `session_id` at `SESSION_GIVEN`, while the reader — which has no harness id to give — mints a
    per-process uuid4 at `SESSION_UNBOUND`. Measured by the reviewer: `dirty__claude-code-session-abc`
    written, `dirty__9b184b17…` drained, MEET = False.

    ⛔ AND THEY CANNOT MEET IN THAT STATE, by construction rather than by oversight: the reader has no
    route to the harness session id at all (harness gap #25642), so there is nothing for it to resolve
    TO. **Signal 1 is therefore dead before a run is registered** — edits made before
    `/mokata:brainstorm` starts one, and the `AMBIGUOUS` case where two runs share the repo. That is a
    real residual gap, not a fixed one; what this module can do is SAY so, which `_reconcile` does
    whenever such a pass would otherwise report a silent fresh. Closing it needs the harness id to
    reach the query process, or a repo-scoped fallback key — and the latter reverses a stated design
    decision (§7h), so it is filed rather than taken here."""
    key, _basis = resolve_harness_session(root, session_id)
    safe = key.replace("/", "_")
    return os.path.join(_dirty_dir(root), f"dirty__{safe}.log")


def foreign_dirty_records(root: str, mine: str):
    """`(keys, record_count)` for dirty-set logs that are not `mine` — RECORDS, not files.

    🔴 REVIEW FINDING 3-6(b). The note said *"%d in-harness edit record(s)"* and passed it the number
    of KEYS. Two leftover logs holding six paths between them were reported as *"2 record(s)"*. A note
    written to stop a silent fresh must not itself be wrong about the quantity it cites.

    Bounded: one `scandir` plus a line count per foreign log, and the logs are one small file per
    session. Never raises — a log that cannot be read contributes its key and no records, which is
    the honest direction (the key IS evidence; the count is a detail)."""
    keys, records = [], 0
    want = mine.replace("/", "_")
    try:
        entries = sorted(os.scandir(_dirty_dir(root)), key=lambda e: e.name)
    except OSError:
        return [], 0
    for e in entries:
        n = e.name
        if not (n.startswith("dirty__") and n.endswith(".log")):
            continue
        key = n[len("dirty__"):-len(".log")]
        if key == want:
            continue
        keys.append(key)
        try:
            with open(e.path, encoding="utf-8") as fh:
                records += sum(1 for ln in fh if ln.strip())
        except OSError:
            pass
    return keys, records


def foreign_dirty_keys(root: str, mine: str) -> List[str]:
    """The dirty-set keys in this repo that are NOT `mine` — evidence that another process recorded
    in-harness edits this one cannot attribute.

    🔴 REVIEW FINDING A1's SECOND HALF, and the more important half. The blind-signal note was gated
    on `session_basis == SESSION_UNBOUND`, so a `pinned`, `bound` or `single` basis suppressed it —
    and the reviewer measured exactly that: a resolved session draining a key nobody wrote, emitting
    an empty note. A silent fresh from a signal that saw nothing is §7g at its purest, and the basis
    was the wrong thing to condition on: what makes signal 1 blind is not *"no run resolved"*, it is
    *"the edits were filed under a key I am not reading"*. That is OBSERVABLE, so it is measured
    rather than inferred.

    Bounded — one `scandir` of a directory holding one small file per session. Never raises: [] on
    any fault means no evidence of a partition, which is the honest default (a note that cannot be
    substantiated must not be printed)."""
    out: List[str] = []
    want = mine.replace("/", "_")
    try:
        with os.scandir(_dirty_dir(root)) as entries:
            for e in entries:
                n = e.name
                if not (n.startswith("dirty__") and n.endswith(".log")):
                    continue
                key = n[len("dirty__"):-len(".log")]
                if key != want:
                    out.append(key)
    except OSError:
        return []
    return sorted(out)


def mark_dirty(root: str, paths: List[str], *, session_id: Optional[str] = None) -> None:
    """Append touched paths to the session dirty-set. Atomic O(1) append (single append-mode
    write, no read-modify-write), ungated, and NEVER raises — this is the async observability
    lane, and the async lane never blocks or fails an action."""
    try:
        d = _dirty_dir(root)
        os.makedirs(d, exist_ok=True)
        payload = "".join(f"{p}\n" for p in paths if p)
        if not payload:
            return
        # O_APPEND makes each write atomic on POSIX; a small line write never interleaves.
        with open(_dirty_path(root, session_id), "a", encoding="utf-8") as fh:
            fh.write(payload)
    except Exception:  # noqa: BLE001 — observability must never break flow
        pass


def read_dirty(root: str, *, session_id: Optional[str] = None) -> List[str]:
    """Peek the dirty-set WITHOUT draining. Never raises."""
    try:
        with open(_dirty_path(root, session_id), encoding="utf-8") as fh:
            return [ln.strip() for ln in fh if ln.strip()]
    except OSError:
        return []


def drain_dirty(root: str, *, session_id: Optional[str] = None) -> List[str]:
    """Read AND clear the dirty-set atomically (rename-then-read, so appends racing the drain
    land in a fresh file and are never lost). Never raises."""
    src = _dirty_path(root, session_id)
    tmp = f"{src}.drain.{os.getpid()}"
    try:
        os.replace(src, tmp)           # atomic; new appends recreate `src`
    except OSError:
        return []
    lines: List[str] = []
    try:
        with open(tmp, encoding="utf-8") as fh:
            lines = [ln.strip() for ln in fh if ln.strip()]
    except OSError:
        lines = []
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return lines


# ==========================================================================================
# 2 — git probes (zero-subprocess SHA read; ONE batched diff on demand)
# ==========================================================================================
def _git_dir(root: str) -> Optional[str]:
    """The git dir for `root` (a `.git` directory, or the gitdir a `.git` FILE points at for a
    linked worktree). Walks up from root. Zero-subprocess. None when not in a repo."""
    cur = os.path.abspath(root)
    while True:
        cand = os.path.join(cur, ".git")
        if os.path.isdir(cand):
            return cand
        if os.path.isfile(cand):
            try:
                with open(cand, encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if line.startswith("gitdir:"):
                            gd = line[len("gitdir:"):].strip()
                            if not os.path.isabs(gd):
                                gd = os.path.normpath(os.path.join(cur, gd))
                            return gd
            except OSError:
                return None
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def git_head_sha(root: str) -> Optional[str]:
    """The current commit SHA — read straight from `.git/HEAD` (+ the loose/packed ref it names).
    Zero-subprocess, cheap (a couple of small file reads). None on a non-repo / unborn branch /
    any error (degrade-clean)."""
    try:
        gitdir = _git_dir(root)
        if gitdir is None:
            return None
        with open(os.path.join(gitdir, "HEAD"), encoding="utf-8") as fh:
            head = fh.read().strip()
        if not head.startswith("ref:"):
            return head or None                 # detached HEAD: a raw SHA
        ref = head[len("ref:"):].strip()
        loose = os.path.join(gitdir, ref)
        try:
            with open(loose, encoding="utf-8") as fh:
                return fh.read().strip() or None
        except OSError:
            pass
        packed = os.path.join(gitdir, "packed-refs")
        try:
            with open(packed, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or line.startswith("^"):
                        continue
                    parts = line.split()
                    if len(parts) == 2 and parts[1] == ref:
                        return parts[0]
        except OSError:
            pass
        return None                              # unborn branch (no commit yet)
    except OSError:
        return None


def _default_git_run(root: str, args: List[str]):
    p = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=10)
    return p.returncode, p.stdout


def _source_only(paths: List[str]) -> List[str]:
    """The diff narrowed to files the code graph actually indexes.

    🔴 FOUND BY THIS STAGE'S OWN TEST, and it is the more serious of the two things that test
    caught. Signal 2 hands `changed` to `_rebuild`, so ANY path git reports would rebuild the
    graph — including mokata's OWN transient state under `.mokata/temp_local/`, which
    `_reconcile` REWRITES on every pass. In a checkout where that directory is tracked (no
    `mokata init`, or a `git add -f`) the reconcile therefore sees its own writes as source
    changes and rebuilds forever, on every query, for the rest of the repo's life. A fix for a
    signal that never fired had introduced one that never stops.

    Narrowing to `languages.SOURCE_EXTENSIONS` closes that by construction rather than by
    depending on the user's `.gitignore` being right — and it is independently correct: a graph
    OF CODE has no reason to rebuild because a README, a lockfile or a JSON state file moved.
    The extension set is the one `grep_backend` already declares, so the two cannot drift.

    🔴 REVIEW FINDING A6 — THE SECOND, "DELIBERATELY REDUNDANT" `.mokata` PREFIX GUARD IS GONE, AND
    THE PARAGRAPH THAT DEFENDED IT WAS WRONG ON THE FACTS. It argued these were not two defences of
    one property — *"the extension filter is about RELEVANCE, the prefix guard is about
    SELF-REFERENCE"* — and claimed an exemption from §7f on that basis. The independent review
    measured it and the attribution was BACKWARDS:

      * the EXTENSION filter is what actually stops the self-reference loop (the offending path was
        `.mokata/temp_local/state/graph_freshness_index__*.json`, dropped on the extension test);
      * the PREFIX guard has NO REACHABLE OFFENDER: `find .mokata -type f` over ~17,700 files yields
        `lock/json/db/db-journal/log/marker/stamp/md/jsonl` and ZERO source extensions, and hooks are
        written to `repo_root()/hooks` → `.claude/`, never under `.mokata`.

    ⛔ And it was UNGRADABLE, which is §7f's whole point. Two mutants the reviewer wrote: removing
    the prefix guard → GREEN (survivor); removing the extension filter → GREEN (survivor); removing
    BOTH → RED. Neither half was gradable, only the pair — §7f's textbook signature. §7f's remedy is
    separate or delete, so the unreachable one is deleted and the one that does the work is the one
    that stays, with the mutant that grades it in this stage's batch.
    """
    from .. import languages
    return [rel for rel in paths if rel.lower().endswith(languages.SOURCE_EXTENSIONS)]


def stale_against(base: Any, root: str, rel: str) -> bool:
    """Is `rel` stale against baseline `base`? NEVER RAISES, and that is the whole reason it exists.

    🔴 REVIEW FINDING A3, SECOND ROUND — MY OWN FIRST FIX FOR A3 TRADED A FOREVER-LOOP FOR A
    FOREVER-CRASH, and the only reason the test passed is that `ensure_fresh` fails open to
    `fresh=True` on any exception (§7e, in the fix for A3).

    `KnowledgeIndex.is_stale` ends in `file_fingerprint(ab)[0] != entry.content_hash`, which OPENS
    the file. For an indexed path that cannot be read — a `*.py` symlink to a directory, a mode-000
    file — it raises `IsADirectoryError`/`PermissionError` straight out of `_reconcile`, so every
    pass after the first recorded a degrade and returned a silent `fresh=True`. Measured:
    `pass2 raw: False ['link.py']` then `IsADirectoryError` on pass 3.

    FOUR STATES, and `is_stale` is deliberately left alone because it is correct for its own callers:
      * indexed, hashable, hash moved        -> True
      * indexed, hashable, hash matches      -> False
      * indexed as UNFINGERPRINTABLE, still unhashable -> False (already reconciled; no loop)
      * indexed, now UNHASHABLE for the first time     -> True (it changed — it broke)
    An unindexed path is not this function's question; the caller decides that (§7g)."""
    entry = getattr(base, "entries", {}).get(rel)
    if entry is None:
        return False                      # unknown, not stale — the caller decides what that means
    recorded = getattr(entry, "content_hash", None)
    ab = os.path.join(root, rel)
    if not os.path.exists(ab):
        # 🔴 3-5 — a TOMBSTONED path that is still gone has already been reconciled. Without this
        # the tombstone would re-report the deletion on every pass forever, which is the loop the
        # pop was there to avoid — the tombstone has to answer BOTH halves to be an improvement.
        return recorded != ABSENT
    if recorded == ABSENT:
        return True                       # 🔴 3-5 — tombstoned and BACK. This is finding A5.
    try:
        from .index import file_fingerprint
        current = file_fingerprint(ab)[0]
    except Exception:  # noqa: BLE001 — ANY read fault means "cannot hash", not "unchanged"
        return recorded != UNFINGERPRINTABLE
    return current != recorded


def baseline_drift(base: Any, root: str) -> List[str]:
    """Indexed paths whose recorded `(mtime, size)` no longer match the filesystem. STAT ONLY.

    🔴 REVIEW FINDING 3-5, AND IT OVERTURNS THIS MODULE'S OWN FILED EXCUSE. Stage 14 filed findings
    A4 (an out-of-band REVERT to a previously-indexed state) and A5 (a reconciled DELETE then a
    byte-identical RECREATE) as unfixable, on the stated grounds that *"making the baseline an
    ADDITIVE source means asking `stale_against` about every indexed path, which hashes every indexed
    file — that IS the full mtime/hash walk, 1615 ms"*. **That reasoning was wrong twice.**

      1. `IndexEntry` has persisted `mtime` AND `size` since it was written, and NOTHING EVER
         COMPARED THEM — `KnowledgeIndex.is_stale` reads only `content_hash`. An additive pass needs
         a `stat`, not a read. Measured on the same 2423-file corpus the 1615 ms figure came from:
         full hash walk **843 ms**, stat-only pass **6.7 ms**. **127x.**
      2. There is no WALK either. The persisted baseline IS the path list, so the 245 ms
         `_iter_files` traversal the cold walk pays does not apply here at all.

    So the signal the module said it could not afford costs ~7 ms and closes A4. The hash is then paid
    only on the paths that drifted — ∝ churn, which is this module's whole perf contract.

    ⚠ A drifted path is a CANDIDATE, not a verdict: `touch` moves mtime without changing content, and
    reporting that as changed would rebuild the graph for nothing. The caller confirms with
    `stale_against`, which hashes — the candidates only.

    Never raises: a path that cannot be stat'ed is reported when it is not already tombstoned, which
    is the loud direction (`stale_against` then decides what it is)."""
    out: List[str] = []
    entries = getattr(base, "entries", None) or {}
    for rel, entry in entries.items():
        recorded = getattr(entry, "content_hash", None)
        ab = os.path.join(root, rel)
        try:
            st = os.stat(ab)
        except OSError:
            if recorded != ABSENT:
                out.append(rel)                     # indexed, and gone
            continue
        if recorded == ABSENT:
            out.append(rel)                         # tombstoned, and back (finding A5)
            continue
        if recorded == UNFINGERPRINTABLE:
            out.append(rel)                         # parked as unhashable — ask again now it stats
            continue
        try:
            # 🔴 REVIEW FINDING 4-1 — THREE FIELDS, AND THE THIRD IS THE ONLY UNFORGEABLE ONE.
            # `mtime` and `size` are both settable from userspace, so a same-size write that restores
            # the mtime left both unchanged — and git's own stat cache is blind to exactly the same
            # thing, so signal 2 reported clean too. The result was a REAL content edit to a tracked,
            # indexed, non-ignored `.py` reading `fresh=True` with an EMPTY note (§7g): the same
            # representation as a genuinely clean tree. Reproduced with `os.utime` AND with a real
            # `tar -xp` of two same-length versions — which is what every vendor drop, `cp -p`,
            # `rsync --times` and `touch -r` does.
            #
            # ⭐ `st_ctime` is the inode CHANGE time and POSIX gives userspace NO way to set it, so a
            # content write always moves it. Measured on both attacks: mtime False, size False,
            # ctime True.
            #
            # ⚠ AND IT IS NOT A WINDOWS FIX. There `st_ctime` is the CREATION time and does not move
            # on a content write, so this class survives on Windows and is DISCLOSED rather than
            # claimed closed. A recorded 0.0 means an index written before this field existed — no
            # opinion, which must not read as changed (§7g).
            recorded_ctime = float(getattr(entry, "ctime", 0) or 0)
            if st.st_mtime != float(getattr(entry, "mtime", 0)) \
                    or st.st_size != int(getattr(entry, "size", -1)) \
                    or (recorded_ctime and st.st_ctime != recorded_ctime):
                out.append(rel)
        except (TypeError, ValueError):
            out.append(rel)                         # an unreadable record is a candidate, not a pass
    return sorted(out)


def _git_paths(out: str) -> List[str]:
    r"""Split a NUL-delimited git path list.

    🔴 REVIEW FINDING 4-5 — `-c core.quotePath=false` CLOSED ONE OF FOUR QUOTING CLASSES, and the
    docstring that chose it over `-z` said `-z` gave *"the same result"*. It does not. `quotePath`
    only stops git quoting NON-ASCII; git still quotes a path containing a `"`, a `\` or a control
    character, measured:

        ['"back\\\\slash.py"', 'café.py', '"new\\nline.py"', '"quo\\"te.py"']
        _source_only kept only: ['café.py']

    So 3-2's exact symptom — an edit invisible because the name git handed over ends in `"` — was
    live for three legal POSIX filename classes. `-z` closes all four, and the format change the old
    docstring was avoiding is this one function."""
    return [p for p in (out or "").split("\0") if p]


# ⛔ `_no_quote_path` WAS DELETED AT THE FOURTH REVIEW PASS, AND THE MUTANT BATCH IS WHY (§7f, §7d).
#
# It prepended `-c core.quotePath=false` to both git readers, fixing review finding 3-2: with
# `quotePath` on — the default — git renders a path containing a non-ASCII byte as a quoted,
# backslash-escaped C string, `_source_only` dropped it because the name ended in a quote rather
# than `.py`, and an out-of-band edit to an accented filename reported `fresh=True` while the
# baseline knew it was stale.
#
# Finding 4-5 then showed that flag closed only ONE of four quoting classes — a path containing a
# `"`, a `\` or a control character was still handed over quoted — and added `-z` to both readers,
# which closes all four. The flag was KEPT alongside it, on the stated grounds that it "also makes
# any human-facing or logged output readable".
#
# 🔴 BOTH HALVES OF THAT WERE WRONG, and it took a mutant to say so. The mutant that deletes the
# flag entirely ran GREEN against a 70-test module, because with `-z` git does not quote AT ALL:
# there is no input for which the two differ, so nothing can grade the flag — the §7f shape exactly.
# And the justification was false on its own terms: neither reader's output is human-facing or
# logged; both are split on NUL and consumed. Pre-1.0 a redundant defence is deleted, not
# documented (§7d), and `-z` is now the single graded defence — see Q01/Q02 in
# `tests/_stage14_review3_mutants.sh`.


def git_changed_since(root: str, sha: str,
                      *, run: Optional[Callable[[str, List[str]], Any]] = None) -> List[str]:
    """ONE batched `git diff --name-only <sha>` — the paths that differ from `sha` (committed AND
    working-tree). Output is ∝ changed files (option-B). Degrade-clean: [] on any error."""
    if not sha:
        return []
    runner = run or _default_git_run
    try:
        rc, out = runner(root, ["diff", "--name-only", "-z", sha])
    except Exception:  # noqa: BLE001
        return []
    if rc != 0:
        return []
    return _git_paths(out)          # 4-5 — NUL-delimited: every quoting class, not just non-ASCII


def git_untracked(root: str, *, run: Optional[Callable[[str, List[str]], Any]] = None) -> List[str]:
    """ONE bounded `git ls-files --others --exclude-standard` — the paths git has never been told
    about, minus everything `.gitignore` excludes.

    ⭐ Fix C (0.0.21 stage 14) and MEASURED, like its sibling: 30 ms on a 787-.py checkout, against
    5 ms for the diff. It earns that because `git diff --name-only <sha>` CANNOT report a path that
    is not in the index — so before this call a brand-new source file was invisible to signal 2, and
    (this is the part the grounding got wrong) invisible to the cold walk as well: the walk only
    reports against a PRIOR persisted baseline, and a new session has none. A new file was therefore
    seen by NO signal until someone ran `git add`, which is a large piece of "the graph degrades".

    `--exclude-standard` is what keeps this bounded on a real checkout: build trees, venvs and
    caches are gitignored and never reach `_source_only`. Degrade-clean: [] on any error, which is
    also the answer in a non-git tree."""
    runner = run or _default_git_run
    try:
        rc, out = runner(root, ["ls-files", "--others", "--exclude-standard", "-z"])
    except Exception:  # noqa: BLE001
        return []
    if rc != 0:
        return []
    return _git_paths(out)          # 4-5 — see `_git_paths`


# ==========================================================================================
# persisted freshness state (transient, session-scoped, ungated)
# ==========================================================================================
@dataclass
class FreshnessState:
    head_sha: Optional[str] = None
    cold_done: bool = False
    # H-6 S2 — path → the anchor fingerprint this session has ALREADY forced a rebuild for.
    #
    # The other three signals self-clear: the dirty-set is DRAINED, HEAD ADVANCES into `head_sha`,
    # the cold walk sets `cold_done`. The anchor signal cannot, and deliberately so: the durable
    # record is not re-stamped by a rebuild (H-6 P7 — a re-stamp is a HUMAN's decision, and S3's
    # proposal and S4's refusal both read the un-restamped record as their evidence). Without this
    # ledger a single moved anchor would rebuild the graph on EVERY query for the rest of the
    # session, which is a loop rather than a signal. SESSION-scoped for the same reason the cold
    # walk is: a new session has not established what its graph reflects.
    forced_anchors: dict = field(default_factory=dict)

    def to_dict(self):
        return {"head_sha": self.head_sha, "cold_done": self.cold_done,
                "forced_anchors": dict(self.forced_anchors)}

    @classmethod
    def from_dict(cls, d):
        d = d or {}
        forced = d.get("forced_anchors")
        return cls(head_sha=d.get("head_sha"), cold_done=bool(d.get("cold_done", False)),
                   forced_anchors=dict(forced) if isinstance(forced, dict) else {})


# ==========================================================================================
# the freshness outcome (gate verdict — doc 85 §3 `*Outcome`)
# ==========================================================================================
@dataclass
class FreshnessOutcome:
    fresh: bool = True                 # nothing changed since the last index
    rebuilt: bool = False              # a rebuild ran this reconcile
    changed: List[str] = field(default_factory=list)
    answer_from_floor: bool = False    # graph rebuild FAILED ⇒ answer from the AST floor, loudly
    capped: bool = False               # a freshness cap was hit (costed note, never a block)
    note: str = ""                     # honest classed note to ride on the answer


def _join_note(existing: str, add: str) -> str:
    if not add:
        return existing
    return f"{existing} | {add}" if existing else add


# ==========================================================================================
# AST floor invalidation + graph refresh seams
# ==========================================================================================
def _ast_backends(layer: Any):
    """Every AST floor reachable from a layer (primary and/or fallback)."""
    from .ast_backend import AstBackend
    out = []
    for b in (getattr(layer, "primary", None), getattr(layer, "fallback", None)):
        if isinstance(b, AstBackend):
            out.append(b)
    return out


def _invalidate_ast(layer: Any) -> None:
    """Drop the in-memory AST edge index so the NEXT query re-parses. The on-disk cache is
    mtime/size keyed, so the re-parse touches only CHANGED files (∝ churn)."""
    for b in _ast_backends(layer):
        try:
            b.invalidate()
        except Exception:  # noqa: BLE001
            pass


def _refresh_graph(primary: Any) -> bool:
    """Proactively refresh the adopted graph's index (GR.S2(k)). True on success, False when the
    rebuild failed (⇒ the caller answers from the AST floor, never from stale graph data)."""
    fn = getattr(primary, "refresh_index", None)
    if callable(fn):
        try:
            return bool(fn())
        except Exception:  # noqa: BLE001
            return False
    rec = getattr(primary, "recover", None)      # older seam: health→refresh→reprobe
    if callable(rec):
        try:
            return bool(rec())
        except Exception:  # noqa: BLE001
            return False
    return True                                  # no refreshable index ⇒ nothing to fail


# ==========================================================================================
# the controller — the ONE read-time reconcile every query front-runs
# ==========================================================================================
class FreshnessController:
    """Reconciles a layer against the dirty-set + git HEAD + a cold baseline BEFORE it answers.

    Constructed per query in production (a fresh layer is built per MCP/CLI call), so all
    cross-instance state (the last-indexed SHA, the cold-done flag) is PERSISTED to transient
    run-state. Within a single long-lived layer, the seeded cold-start index also serves the
    post-answer out-of-band recheck."""

    def __init__(self, root: str, *, state: Any = None, session_id: Optional[str] = None,
                 git_run: Optional[Callable] = None) -> None:
        self.root = root
        # Fix A — the ladder, not a fresh uuid4. `session_basis` is kept so the reconcile can SAY
        # when the per-session signals are blind instead of reporting a silent "fresh".
        self.session_id, self.session_basis = resolve_freshness_session(root, session_id)
        self._state_store = state           # a StateStore, or None (lazily resolved)
        self._git_run = git_run
        self._index = None                  # in-memory KnowledgeIndex seeded at cold start

    # --- construction --------------------------------------------------------
    #
    # 🔴 Fix C (0.0.21 stage 14) — `for_root` was DELETED here, not deprecated (doc 85 §7d).
    # It was a second construction path with ZERO callers: none in `src/`, and none in `tests/`
    # either. The doc-84 row that filed it said "its only caller anywhere is a test" — that was
    # WRONG; the one test mention was `test_d5_sweep_register`'s register TABLE naming its
    # `except Exception`, which is a description of the handler, not a call of the method. So it
    # graded nothing (§7i) and was removed along with its register entry. `for_surface` is the
    # one construction path, and it passes no `session_id`, which is exactly what Fix A wants:
    # every production controller resolves its session through the ladder.
    @classmethod
    def for_surface(cls, surface: Any) -> Optional["FreshnessController"]:
        try:
            return cls(surface.root, state=surface.state)
        except Exception:  # noqa: BLE001
            return None

    # --- persisted state -----------------------------------------------------
    def _store(self):
        if self._state_store is not None:
            return self._state_store
        try:
            from ..state import StateStore
            from ..tdd_state import state_dir
            self._state_store = StateStore(state_dir(self.root))
        except Exception:  # noqa: BLE001
            self._state_store = None
        return self._state_store

    def _state_key(self) -> str:
        return FRESHNESS_STATE_PREFIX + self.session_id

    def _load_state(self) -> FreshnessState:
        store = self._store()
        if store is None:
            return FreshnessState()
        try:
            return FreshnessState.from_dict(store.read(self._state_key()))
        except Exception:  # noqa: BLE001
            return FreshnessState()

    def _save_state(self, st: FreshnessState) -> None:
        store = self._store()
        if store is None:
            return
        try:
            store.write(self._state_key(), st.to_dict())
        except Exception:  # noqa: BLE001
            pass

    # --- cold-start baseline walk (ONCE per session) -------------------------
    def _cold_walk(self):
        """Seed the mtime/hash baseline. Returns (changed_paths, capped). Full walk, cold-start
        only; bounded by INDEX_SIZE_CAP_FILES with an honest costed note past the cap.

        🔴 RE-SCOPED at Fix C (0.0.21 stage 14). The walk itself is UNCHANGED — what changed is
        the claim made for it, and the first re-scope written here was WRONG and is recorded as
        wrong rather than quietly replaced.

        ⛔ THIS WALK ALMOST NEVER REPORTS, AND THE FIRST VERSION OF THIS PARAGRAPH OVERSTATED THAT
        INTO A FALSEHOOD. It said the walk *"returns [] on every reachable path"* and that a non-None
        `prior` was *"a torn-write case, not a design case"*. 🔴 REVIEW FINDING A2 REFUTED BOTH, with
        two reproductions:

          * **A CANCELLED QUERY.** `_cold_walk` persists the index immediately; `_save_state(
            cold_done=True)` runs at the END of `_reconcile`, after `_rebuild` — which for an adopted
            graph runs `refresh_index()`, a subprocess. A `KeyboardInterrupt` in that window (Ctrl-C,
            a cancelled MCP call) is a `BaseException`, so `ensure_fresh`'s `except Exception` does
            NOT catch it, and the pass leaves index-persisted / cold-done-UNSET. The next pass's walk
            then reported `changed=['a.py']` with signals 1 and 2 contributing nothing.
          * **TWO PROCESSES RECONCILING CONCURRENTLY**, interleaved at a real yield point (one inside
            the `git ls-files` subprocess while the other completes a pass and an edit lands).

        So the honest statement is the narrow one: **the walk is a baseline SEEDER, and its reporting
        path is reachable only when a previous pass persisted the index without persisting the
        state.** That is not a torn write — it is a cancelled or concurrent query, both of which
        happen. Do not plan around signal 3 as a detector; do not claim it cannot detect.

        ⭐ MEASURED, and it is the measurement that killed the first draft of this docstring. That
        draft said the walk was "the only signal that sees an UNTRACKED file". Probe, 40-file git
        repo, a new brand_new·py written but not `git add`ed (⚠ the interpunct is deliberate: a
        BACKTICKED name.py asserts the file exists, and `test_stage14_neo4j_removal` grades that
        convention — this one was a throwaway probe fixture, so it is history, not a claim):

            proc1  cold walk runs      -> changed=[]
            proc2  warm, file created  -> changed=[]
            proc3  NEW session, cold walk again -> changed=[]      <- the draft predicted a catch
            proc4  after `git add`     -> changed=[the new file]

        So a new source file was seen by NO signal until it was tracked. That is not a case this
        walk covered; it was a hole, and Fix C closed it in signal 2 with `git_untracked`.

        What this walk IS still for, stated at the level the evidence supports:
          * it SEEDS the fingerprint baseline that signal 2's three-case filter and
            `recheck_after_answer` both read. Without it there is no baseline on pass 1, and
            signal 2 falls back to "absent ⇒ changed" for everything git reports.
          * it is filesystem-based, so the baseline it seeds includes untracked files that
            `git diff` would never name.

        ⛔ And it stays ONCE per session for the reason it always was: measured at 1615 ms on a
        2419-file checkout, i.e. 1.6 s added to every `mokata query` if it ran per-pass."""
        from .index import KnowledgeIndex
        idx = KnowledgeIndex()
        capped = False
        try:
            # count-bounded build (option-B: don't let a giant repo blow the cold walk unbounded)
            paths = []
            for ab in idx._iter_files(self.root, (".py",)):
                paths.append(ab)
                if len(paths) >= INDEX_SIZE_CAP_FILES:
                    capped = True
                    break
            for ab in paths:
                from .index import file_ctime, file_fingerprint, IndexEntry
                rel = name_of(ab, self.root)
                try:
                    h, m, s = file_fingerprint(ab)
                except OSError:
                    continue
                idx.entries[rel] = IndexEntry(rel, h, m, s, file_ctime(ab))
        except Exception:  # noqa: BLE001
            return [], capped
        changed = []
        prior = self._load_index()
        if prior is not None:
            # a persisted baseline from earlier THIS session ⇒ out-of-band changes since then.
            # REVIEW FINDING A3 — through `stale_against`, not `prior.stale_files`, because an
            # unhashable indexed path raises out of the latter and takes the whole reconcile with it.
            changed = [rel for rel in idx.entries if stale_against(prior, self.root, rel)]
        self._index = idx
        self._save_index(idx)
        return changed, capped

    def _index_key(self) -> str:
        return FRESHNESS_INDEX_PREFIX + self.session_id

    def _load_index(self):
        store = self._store()
        if store is None:
            return None
        try:
            from .index import KnowledgeIndex
            data = store.read(self._index_key())
            return KnowledgeIndex.from_dict(data) if data else None
        except Exception:  # noqa: BLE001
            return None

    def _save_index(self, idx) -> None:
        store = self._store()
        if store is None:
            return
        try:
            store.write(self._index_key(), idx.to_dict())
        except Exception:  # noqa: BLE001
            pass

    def _advance_baseline(self, paths) -> List[str]:
        """Record the CURRENT fingerprint of paths just reconciled (Fix B). Returns the tombstoned
        paths this call had to DROP to stay under `BASELINE_TOMBSTONE_CAP` — see finding 4-8(b);
        the caller discloses them, because each one is a recreate it can no longer notice.

        A path that no longer exists is REMOVED rather than left behind: `is_stale` answers True
        for an indexed-but-missing file, so keeping a deleted path would re-trigger a rebuild on
        every pass forever — the same loop the filter exists to prevent, arriving from the other
        side. Never raises: a baseline that cannot be written costs a redundant rebuild, never an
        answer."""
        if not paths:
            return []
        try:
            from .index import IndexEntry, KnowledgeIndex, file_ctime, file_fingerprint
        except Exception:  # noqa: BLE001
            return []
        base = self._load_index()
        if base is None:
            base = KnowledgeIndex()
        for rel in paths:
            ab = os.path.join(self.root, rel)
            if not os.path.exists(ab):
                # 🔴 REVIEW FINDING 3-5 — TOMBSTONE, not pop. Popping left a reconciled deletion
                # unknown, so a byte-identical RECREATE had nothing to compare against and no signal
                # said the file was back (finding A5). `stale_against` answers both halves: still
                # gone ⇒ not stale (no loop), back on disk ⇒ stale.
                base.entries[rel] = IndexEntry(rel, ABSENT, 0.0, -1)
                continue
            try:
                h, m, sz = file_fingerprint(ab)
            except Exception:  # noqa: BLE001
                # 🔴 REVIEW FINDING A3 — THE FOURTH CASE, and `continue` here was a FOREVER LOOP.
                # A path that EXISTS but cannot be fingerprinted was neither popped nor recorded, so
                # the filter's "absent from the baseline AND on disk ⇒ changed" arm re-fired on every
                # pass, for the life of the repo. The reviewer reproduced it over five passes with a
                # `*.py` symlink pointing at a DIRECTORY and with a mode-000 `*.py`; both rebuilt
                # every time. The §7g split enumerated THREE cases and this is a fourth one it did
                # not see: "on disk" and "readable" are not the same fact.
                #
                # A SENTINEL ENTRY is the fix, not a pop: popping would re-mark it changed next pass
                # (absent + exists), and recording a real fingerprint is impossible. An entry whose
                # content hash is a value no file can produce means "this path is KNOWN and cannot be
                # hashed" — it stops the loop, and `is_stale` keeps answering True for it the moment
                # it becomes readable, because any real hash differs from the sentinel.
                base.entries[rel] = IndexEntry(rel, UNFINGERPRINTABLE, 0.0, -1)
                continue
            base.entries[rel] = IndexEntry(rel, h, m, sz, file_ctime(ab))
        dropped = self._prune_tombstones(base)
        self._save_index(base)
        return dropped

    @staticmethod
    def _prune_tombstones(base: Any) -> List[str]:
        """Keep at most `BASELINE_TOMBSTONE_CAP` ABSENT entries, oldest dropped first.

        🔴 REVIEW FINDING 4-8(b). A tombstone's whole job is to make a RECREATE visible, and its
        value decays: a path deleted a thousand commits ago and never seen again is pure cost, paid
        as a `stat` on every single 2b pass, forever. Only the ABSENT entries are counted — a real
        record is the index doing its job and is not this cap's business.

        ⚠ WHAT IS LOST IS NAMED, not swallowed. The caller puts the dropped paths on the answer: a
        byte-identical recreate of one of them is now outside every signal, and that is exactly the
        A5 shape this module closed, so it is disclosed rather than quietly reopened."""
        entries = getattr(base, "entries", None)
        if not entries:
            return []
        tombs = [rel for rel, e in entries.items()
                 if getattr(e, "content_hash", None) == ABSENT]
        excess = len(tombs) - BASELINE_TOMBSTONE_CAP
        if excess <= 0:
            return []
        dropped = tombs[:excess]                # insertion order == deletion order == oldest first
        for rel in dropped:
            entries.pop(rel, None)
        return dropped

    # --- the reconcile -------------------------------------------------------
    def ensure_fresh(self, layer: Any) -> FreshnessOutcome:
        """The read-time reconcile every query front-runs. Rebuilds a KNOWN-stale index BEFORE
        the answer; NEVER raises (freshness must not break a query)."""
        try:
            return self._reconcile(layer)
        except Exception as exc:  # noqa: BLE001
            # D5 — LOUD, not silent: a broken freshness reconcile means the answer may not reflect
            # the latest edits, and that must never be a secret. The query STILL proceeds (freshness
            # is a read-time enhancement, never a reason a query can't run) — it just says so once.
            from ..degrade import FAILURE_UNREACHABLE, note_degraded
            note_degraded(
                "graph-freshness", FAILURE_UNREACHABLE,
                fallback="the answer may not reflect the latest edits",
                fix="run `mokata doctor`; the graph re-reconciles on the next query",
                detail=f"freshness reconcile failed: {exc}")
            return FreshnessOutcome(fresh=True)

    def _reconcile(self, layer: Any) -> FreshnessOutcome:
        changed = set()
        capped = False
        notes: List[str] = []

        st = self._load_state()
        # 1 — dirty-set (in-harness edits). Fast O(1) drain.
        dirty = drain_dirty(self.root, session_id=self.session_id)
        head = git_head_sha(self.root)

        if len(dirty) > DIRTY_BATCH_THRESHOLD and st.head_sha:
            # Option-B: collapse a big dirty-set into ONE batched git diff vs the last-indexed
            # SHA instead of per-file work.
            changed |= set(git_changed_since(self.root, st.head_sha, run=self._git_run))
            capped = True
            notes.append(f"freshness: {len(dirty)} files touched — batched git-diff reconcile")
        else:
            changed |= set(dirty)

        # 2 — the WORKING TREE **and** HEAD, together: ONE batched diff vs the last-INDEXED sha,
        #     on EVERY pass.
        #
        # 🔴 Fix B (0.0.21 stage 14). This used to be gated on `head != st.head_sha`, so it ran
        # ONLY when you committed — and `git_changed_since` reports the paths that differ from
        # `sha` COMMITTED AND WORKING-TREE (see its docstring). That gate was the one thing
        # preventing signal 2 from delivering the "editor/out-of-band changes" this module's own
        # docstring claims for it. The claim was in the docstring; the gate prevented it.
        #
        # ⭐ MEASURED, because the obvious alternative was to reach for signal 3 instead: on a
        # 2419-file checkout the mtime/hash walk costs **1615 ms** and this diff costs **9.2 ms**.
        # 175x cheaper, and its output is ∝ CHANGED FILES rather than repo size.
        #
        # ⚠ AND THE FILTER IS LOAD-BEARING, not tidiness. Without it this re-reports the same
        # uncommitted edit on every pass and rebuilds forever. `KnowledgeIndex.is_stale` answers
        # False for a path ABSENT from the baseline ("untracked -> not stale, just unknown") —
        # correct for its own caller and wrong here, where git has ALREADY established the path
        # differs from the indexed sha: a NEWLY CREATED file is absent from the baseline and must
        # count as CHANGED. So absence means changed at this call site (§7g), and `is_stale` is
        # left exactly as it is.
        #
        # 🔴 Fix C ALSO ungates this from `st.head_sha`, because the untracked half does not need
        # a sha to diff against — and the first pass of a session has no sha, which is exactly when
        # a new file is most likely to be missing from the graph.
        cand = _source_only(
            (git_changed_since(self.root, st.head_sha, run=self._git_run) if st.head_sha else [])
            + git_untracked(self.root, run=self._git_run))
        base = self._load_index()
        if cand:
            if base is None:
                changed |= set(cand)
            else:
                # ⚠ THREE cases, and "absent from the baseline" is TWO of them — found by this
                # stage's own test, which looped forever on a deletion (§7g, in the filter
                # written to fix a §7g):
                #   * in the baseline and fingerprint moved   -> CHANGED (`is_stale`)
                #   * absent AND on disk   -> a file we have never indexed -> CHANGED
                #     (this is also the case an UNTRACKED new file arrives by — Fix C)
                #   * absent AND gone      -> a deletion already reconciled -> NOT changed;
                #     git still reports it against the indexed sha forever, so treating
                #     absence alone as changed rebuilds on every query for the rest of time.
                changed |= {
                    rel for rel in cand
                    if stale_against(base, self.root, rel)
                    or (rel not in base.entries
                        and os.path.exists(os.path.join(self.root, rel)))
                }

        # 2b — 🔴 THE ADDITIVE BASELINE (review finding 3-5). Signals 1 and 2 are both SUBTRACTIVE:
        #      they take a set git names and remove what the baseline already knows. So a change git
        #      cannot name — a REVERT to a previously-indexed state, a reconciled DELETE then a
        #      byte-identical RECREATE — was invisible before the answer, while the baseline KNEW.
        #      Those were filed at stage 14 as unfixable; see `baseline_drift` for why that was wrong.
        #
        #      ⭐ STAT ONLY: ~7 ms over 2423 indexed paths, against the 843 ms a hash walk costs. The
        #      hash is paid on the drifted candidates alone, so the cost stays ∝ CHURN — this
        #      module's contract — rather than ∝ repo size.
        drift_settled: List[str] = []
        if base is not None:
            cands = baseline_drift(base, self.root)
            if cands:
                # 🔴 REVIEW FINDING 4-8 — BOUNDED, and bounded in the LOUD direction. See
                # BASELINE_DRIFT_HASH_CAP: the hash is what costs, so the hash is what is capped,
                # and a candidate that could not be afforded is rebuilt rather than assumed clean.
                confirmable = cands[:BASELINE_DRIFT_HASH_CAP]
                unaffordable = cands[BASELINE_DRIFT_HASH_CAP:]
                hits = {rel for rel in confirmable if stale_against(base, self.root, rel)}
                changed |= hits
                if unaffordable:
                    capped = True
                    changed |= set(unaffordable)
                    notes.append(
                        "freshness: baseline-drift cap — %d of %d drifted path(s) were rebuilt "
                        "WITHOUT the hash confirmation (bounded — costed, not blocked; a redundant "
                        "rebuild, never a missed edit)" % (len(unaffordable), len(cands)))
                # ⚠ A candidate that hashed CLEAN still has a stale (mtime, size) record — a `touch`
                # moves mtime without changing content — so its record is refreshed too. Without
                # this it would re-drift and be re-hashed on every pass, which turns an ∝ churn
                # signal into an ∝ touched-files one.
                #
                # ⛔ ONLY THE CONFIRMED ONES — AND THE REASON IS NAMING, NOT BEHAVIOUR. I first
                # wrote that including an unaffordable candidate here would "re-stamp a record from
                # a file nobody looked at", i.e. the cap's own fail-open. THAT WAS FALSE and the
                # mutant batch is what said so: `_advance_baseline` computes a fresh
                # `file_fingerprint` itself, and `unaffordable ⊆ changed` always holds, so the
                # rebuild arm advances exactly the same records with exactly the same values. The
                # mutant that widens this line back to `cands` is EQUIVALENT, and it is recorded as
                # equivalent in the batch rather than left in as a permanent GREEN (§7b).
                #
                # What the restriction buys is that `drift_settled` keeps meaning "confirmed clean",
                # which is what the arm below reads it as. Behaviour is identical either way.
                drift_settled = [rel for rel in confirmable if rel not in hits]

        # ⛔ WHAT REMAINS FILED, AND IT IS NO LONGER "THE 1615 ms WALK".
        #
        # A4 and A5 are CLOSED by signal 2b above. What stage 14 filed — and the excuse it filed it
        # with — was wrong on the facts, and the correction is recorded rather than quietly replaced:
        # the stated reason was that an additive baseline "hashes every indexed file", and
        # `IndexEntry` had persisted mtime and size all along with nothing comparing them.
        #
        # ⚠ WHAT IS GENUINELY STILL OUTSIDE EVERY SIGNAL: a change to a file that is NOT in the
        # baseline and NOT named by git — i.e. a path the cold walk never indexed (it walks `.py`
        # only) and `git ls-files --others` excludes (gitignored). A `.ts` file under an ignored
        # directory is the shape. That is a real gap and it is not cheap: closing it means either
        # indexing the full polyglot set at cold start, or walking ignored trees, and both are the
        # repo-size cost this module exists to avoid.
        #
        # 3 — cold start: ONE mtime/hash walk per session (baseline).
        if not st.cold_done:
            walked, walk_capped = self._cold_walk()
            changed |= set(walked)
            if walk_capped:
                capped = True
                notes.append("freshness: index size cap — cold walk bounded")

        # 4 — H-6: the CODE-ANCHOR tripwire. `about_code` anchors whose recorded fingerprint has
        #     moved are the same knowledge the three signals above carry — this graph is KNOWN
        #     stale for these files — arriving by a fourth route: a baseline that OUTLIVED the
        #     session. Section 9's pre-named `fingerprint_forces_refresh` is the comparison, owned
        #     once in `anchor_fingerprints` and consumed here rather than re-derived.
        forced = dict(st.forced_anchors)
        anchors = self._anchor_signal()
        for rel in anchors.paths:
            fp = self._anchor_fingerprint(rel)
            if fp and forced.get(rel) == fp:
                continue          # already forced a rebuild for THIS state (see FreshnessState)
            changed.add(rel)
            forced[rel] = fp
        if anchors.skipped:
            capped = True
            notes.append(
                f"freshness: anchor-scan cap — {anchors.skipped} recorded code anchor(s) not "
                "checked this pass (bounded — costed, not blocked)")

        # freshness change cap — costed note, NEVER a block.
        if len(changed) > FRESHNESS_CHANGE_CAP:
            capped = True
            notes.append(
                f"freshness cap: {len(changed)} files changed since last index "
                "(reconcile bounded — costed, not blocked)")

        # 🔴 REVIEW FINDING A1 — the note is conditioned on the MEASURED partition now, not on the
        # basis. It used to fire only for `SESSION_UNBOUND`, so a `pinned`/`bound`/`single` session
        # draining a key nobody wrote emitted NOTHING — a silent fresh from a signal that saw nothing
        # (§7g). What makes signal 1 blind is not "no run resolved", it is "the edits were filed
        # under a key I am not reading", and that is observable.
        # 🔴 REVIEW FINDING 3-6, three defects in one sentence, all three fixed here:
        #
        #   (a) IT WAS PERMANENT. Nothing in `src/` prunes this directory, so two leftover logs from
        #       finished sessions put the note on EVERY query for the life of the repo — including
        #       passes where signal 1 worked perfectly. That is the nagging this stage already
        #       argued against twice (§7i). The note exists for ONE case: a pass that would otherwise
        #       report a silent FRESH. When the reconcile found changes anyway, the note adds
        #       nothing, so it is scoped to `not changed`.
        #   (b) IT MISCOUNTED — `len(foreign)` is the number of KEYS; two logs holding six paths read
        #       as "2 record(s)". Now the records are counted.
        #   (c) ⛔ IT ASSERTED THE WRONG PROVENANCE. *"HEAD/working-tree and code-anchor signals …
        #       are what answered here"* is a claim this function cannot make and was measurably
        #       false: on a first pass `st.head_sha` is None, so signal 2 did not run at all. A note
        #       added to stop a silent fresh was itself stating something it had not measured, which
        #       is the §7g shape one level up. It now says only what it knows.
        if not changed:
            foreign, foreign_records = foreign_dirty_records(self.root, self.session_id)
            if foreign:
                notes.append(
                    "freshness: this pass found nothing, and %d in-harness edit record(s) under %d "
                    "other session key(s) (%s) are NOT drainable by this process — if an edit of "
                    "yours is missing from the answer, that is where it is. (A key may also be a "
                    "leftover from a finished session; nothing prunes them yet.)"
                    % (foreign_records, len(foreign),
                       ", ".join(foreign[:3]) + ("…" if len(foreign) > 3 else "")))
        # Fix A — when nothing resolved the session, signals 1 and 3 are BLIND, and that is not the
        # same fact as "nothing changed". Say so once rather than reporting a silent fresh (§7g).
        if self.session_basis == SESSION_UNBOUND:
            notes.append(
                "freshness: no run is bound to this process, so in-harness edit signals are not "
                "visible here — HEAD/working-tree and code-anchor signals still apply "
                "(pin MOKATA_SESSION_ID, or start a run, to enable them)")

        out = FreshnessOutcome(fresh=not changed, changed=sorted(changed), capped=capped)
        dropped_tombs: List[str] = []
        if changed:
            self._rebuild(layer, out, notes)
            # Fix B — advance the baseline PAST what we just reconciled, so the next pass does not
            # re-report an edit already in the index. This is what keeps an ungated signal 2 from
            # rebuilding on every query for as long as an uncommitted edit exists.
            dropped_tombs += self._advance_baseline(changed)
        if drift_settled:
            # 3-5 — see the note at signal 2b
            dropped_tombs += self._advance_baseline(drift_settled)
        if dropped_tombs:
            # 🔴 REVIEW FINDING 4-8(b) — the cap bit, and what it costs is SAID. Each of these paths
            # had a tombstone recording "the graph believes this file is gone"; without it, a
            # byte-identical recreate is invisible to every signal again (the A5 shape).
            out.capped = True               # `capped` itself is already spent — `out` is built above
            notes.append(
                "freshness: baseline tombstone cap — %d oldest deletion record(s) dropped (%s), so "
                "a byte-identical recreate of those paths would not be noticed (bounded — costed, "
                "not blocked)"
                % (len(dropped_tombs),
                   ", ".join(sorted(dropped_tombs)[:3])
                   + ("…" if len(dropped_tombs) > 3 else "")))

        # persist the advanced baseline (last-indexed SHA + cold-done + H-6's forced-anchor ledger)
        self._save_state(FreshnessState(head_sha=head, cold_done=True, forced_anchors=forced))
        out.note = " | ".join(n for n in notes if n)
        return out

    # --- H-6 S2: the code-anchor tripwire's inputs ---------------------------
    def _anchor_signal(self):
        """The moved `about_code` anchors, bounded. Imported LAZILY and on purpose:
        `anchor_fingerprints` imports this module for the tripwire, so a module-level import here
        would be a cycle. Never raises — a broken record must not cost a query its answer."""
        try:
            from .anchor_fingerprints import anchor_signal
            return anchor_signal(self.root)
        except Exception:  # noqa: BLE001
            from .anchor_fingerprints import AnchorSignal
            return AnchorSignal(paths=[])

    def _anchor_fingerprint(self, rel: str) -> str:
        """The CURRENT content hash of an anchored file — the ledger key for "already forced".
        Empty on any failure, which makes the entry non-matching and simply re-forces once."""
        try:
            from .index import file_fingerprint
            return file_fingerprint(os.path.join(self.root, rel))[0]
        except (OSError, ValueError):
            return ""

    def _rebuild(self, layer: Any, out: FreshnessOutcome, notes: List[str]) -> None:
        """Debounce COALESCES all pending signals into ONE rebuild (it never permits a stale
        answer). A graph rebuild failure routes the answer to the AST floor on CURRENT files."""
        out.rebuilt = True
        py = [p for p in out.changed if p.endswith(".py")]
        nonpy = [p for p in out.changed if not p.endswith(".py")]

        _invalidate_ast(layer)          # AST floor re-parses only changed .py on the next query
        primary = getattr(layer, "primary", None)
        if getattr(primary, "is_graph", False):
            # CRG is polyglot — the SAME signals (incl. non-.py) drive its incremental re-index.
            if not _refresh_graph(primary):
                out.answer_from_floor = True
                notes.append(
                    "code graph rebuild FAILED — answered from the AST floor on current files "
                    "(never from stale graph data); run `mokata doctor`")
        elif nonpy and not py:
            # AST-floor-only repo: the AST floor indexes .py only (honest tier note).
            notes.append(
                f"{len(nonpy)} non-Python file(s) changed; the AST floor indexes .py only — "
                "structural answers cover Python")

    # --- out-of-band recheck (post-answer; ∝ result size, not repo size) ------
    def recheck_after_answer(self, layer: Any, result: Any) -> bool:
        """Catch an out-of-band editor edit (no hook, HEAD unchanged) that touched a file the
        answer references: hash-staleness on the REFERENCED files (bounded by the result, never
        the repo). Returns True when a rebuild ran and the caller should re-query on fresh state."""
        if result is None:
            return False
        # 🔴 Fix C (0.0.21 stage 14) — MEASURED, and the measurement inverted the diagnosis.
        #
        # This was filed as "structurally dead: always returns False". It is not dead — it was
        # ALIVE ONLY BECAUSE THE SESSION WAS BROKEN, and Fix A is what killed it. `self._index`
        # is seeded by `_cold_walk` alone, and the cold walk runs once per SESSION KEY. Before
        # Fix A that key was a fresh uuid4 per process, so every `mokata query` was a cold start:
        # the walk ran every time (1.6 s of it) and this recheck therefore worked every time.
        # Once Fix A made the key stable across processes, process 1 seeds the index and
        # processes 2..n do not — so from the second query of a session onward `self._index` is
        # None and this returns False. Two defects were masking each other.
        #
        # Probe, three separate processes against a 40-file git repo (`fixc`):
        #   pinned  proc1 index_seeded=True  recheck=True    <- the cold walk ran here
        #   pinned  proc2 index_seeded=False recheck=False   <- dead
        #   pinned  proc3 index_seeded=False recheck=False   <- dead
        #   unbound proc1/2/3 all index_seeded=True recheck=True  (the PRE-Fix-A shape, 1.6 s each)
        #
        # The baseline is PERSISTED (`_save_index`), so the in-memory copy was never the only
        # one available — it was just the only one this method looked at. Load it. That makes the
        # recheck live in EVERY process at no walk cost, and it is why this is wired rather than
        # deleted: the delete-or-wire ruling was conditional on it being unable to fire, and it
        # fires. Fix B's `_advance_baseline` keeps the loaded baseline current, so a path this
        # pass already reconciled is NOT stale here — no double rebuild (there is a control).
        if self._index is None:
            self._index = self._load_index()
        if self._index is None:
            return False        # no baseline on disk yet ⇒ no opinion, not "nothing changed"
        try:
            paths = [r.path for r in getattr(result, "references", [])]
            # REVIEW FINDING A3 — same reason as the two sites above: `stale_files` opens files.
            stale = [rel for rel in paths if stale_against(self._index, self.root, rel)]
        except Exception:  # noqa: BLE001
            return False
        if not stale:
            return False
        _invalidate_ast(layer)
        primary = getattr(layer, "primary", None)
        if getattr(primary, "is_graph", False):
            _refresh_graph(primary)
        try:
            self._index.reindex(self.root, only=stale)
            self._save_index(self._index)
        except Exception:  # noqa: BLE001
            pass
        return True


# ==========================================================================================
# 9 — H-6 fingerprint tripwire: AWAKE since 0.0.16 (H-6 S2, 2026-08-01)
# ==========================================================================================
def fingerprint_forces_refresh(recorded: Optional[str], current: Optional[str]) -> bool:
    """The H-6 tripwire. A fingerprint MISMATCH is the SAME forced-refresh signal as a
    dirty-set/HEAD change — a KNOWN-stale graph must rebuild before it answers.

    **WOKEN AT H-6 S2 (0.0.16).** It was declared here at GR.S4 and left dormant with a note that
    0.0.16 would supply the recorded-vs-current fingerprints. It does: `anchor_fingerprints`
    (H-6 S1) holds a DURABLE anchor→fingerprint record — the piece GR.S4 could not supply, since
    its own baseline dies with the session — and `_reconcile` signal 4 consumes the verdict.

    The wake is NOT the literal `if fingerprint_forces_refresh(...)` line this docstring once
    predicted, and the difference is deliberate: the comparison is owned in ONE place
    (`anchor_fingerprints.evaluate_anchor`, this function's only other caller) and `_reconcile`
    reads its result. Inlining it here as well would put two fingerprint comparisons in the
    codebase, which is precisely what pre-naming ONE hook was for. A guard test enumerates the
    caller list (`test_db_s7c2_stale_ref.py`).

    Still a no-op when either side is absent (returns False) — no baseline is no opinion, the
    direction `memory.staleness.is_stale` takes for the memory-handle half."""
    if not recorded or not current:
        return False
    return recorded != current
