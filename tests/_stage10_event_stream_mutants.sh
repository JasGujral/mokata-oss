#!/usr/bin/env bash
# 0.0.21 stage 10 `event-stream` (R1.S1a-d), graded. Through scripts/mutate.sh.
#
#   PYTHON=/path/to/venv/bin/python tests/_stage10_event_stream_mutants.sh < /dev/null
#
#   S01-S08 ★★★ the schema and store: the closed vocabulary, the two Optional fields whose
#     ABSENCE-vs-ZERO is the §7g distinction (graded on BOTH live signatures, plus the dead
#     schema defaults that survived the first draft), the session anchor the clean-resume rule
#     rests on, and the bare `sqlite3.connect` that `test_ms_s4_sqlite_wal` convicted once.
#   P01-P06 ★★★ the register: an UNMAPPED kind projecting anyway, a kind leaving the register,
#     the free-text `reason` coming back (the privacy hole a test found), a MemoryOp carrying
#     the diff the ledger keeps, and the estimate/measurement pair becoming indistinguishable.
#   L01-L06 ★★★ the ledger projection: un-wired (§7i), able to fail the canonical append, a root
#     derivation that accepts anything, and the toggle's §7e decision — the one place doubt
#     resolves, a handler forbidden to re-decide it, and the public emit's own swallow.
#   M01-M03 ★★★ MCP dispatch: no duration, `ok` from "did it raise", and the one-exit property
#     that keeps `timed_out` evented.
#   G01-G02 ★★★ the seventh type: un-wired, and one row per finding instead of per kind.
#   R01-R04 ★★★ clean resume: the additive loop restored, the clear moved outside the commit
#     closure so a DECLINED pull wipes the session, the derived clear set narrowed back to one
#     declaration, and the report dropped.
#   C01-C04 ★★★ R1.S1c: the progress projection, the zero-spend suppression, the verdict's
#     gate-ness, and the memory-read event.
#   D01-D02 ★★★ R1.S1d's guard — and these two mutate `src/` to plant the thing the measurement
#     says is not there, because a register whose corpus finds nothing grades nothing (§7i): an
#     internal `print()` diagnostic, and an internal `logging` import.
#   F01-F05 ★★★ THE INDEPENDENT REVIEW's four blocking findings, none of which had a mutant:
#     the toggle honoured only for a `Surface` while all six producers pass a root STRING (F1),
#     an events DB created outside any repo (F2), the pre-MS.S2 singleton surviving the
#     clean-resume AND the report that denied it (F3, two mutants), and the unbounded retrieval
#     manifest in a savings label (F4).
#
# ⚠ THE FIRST DRAFT OF THIS HEADER PROMISED 29 ROWS AND THE FILE HELD 25. R04, C03, C04, D01 and
# D02 were described and never written, and `TOTAL=25` agreed with the file rather than with the
# claim — so the batch would have reported PASS over five properties it never touched (§7b: the
# mutant list IS the score, so a list that lies is a score that lies).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"

SC=src/mokata/events/schema.py
ST=src/mokata/events/store.py
PR=src/mokata/events/projection.py
LD=src/mokata/govern/ledger.py
MS=src/mokata/mcp/server.py
GT=src/mokata/govern/gate.py
SB=src/mokata/session_bundle.py
PE=src/mokata/progress_events.py
OR=src/mokata/execmode/orchestrator.py
MM=src/mokata/memory/store.py
SS=src/mokata/session_state.py
BG=src/mokata/govern/budget.py
T='test_a39_the_event_stream_is_typed_and_derived.py'

TOTAL=40

# ⚠ WHY THIS BATCH RUNS IN WINDOWS, AND WHY A WINDOW IS NOT A VERDICT. Each mutant re-runs the
# whole of test_a39, and the harness this is driven from caps one call at ~180s. So FROM/TO run
# an index window, windows accumulate into a LEDGER keyed by a FINGERPRINT of every file this
# batch mutates, and ONLY the ledger reports a PASS: all $TOTAL indices present and RED. (⛔ It
# said "all 30 indices" over a 35-row file — the SCORE was honest, because the check compares
# against $TOTAL, but a reader checking the batch by its own description checked the wrong thing.
# Same prose defect the CM.S5 batch carried, §7b.) A window
# that comes back all-RED says nothing about the batch (§7e).
#
#   MUTANT_LEDGER=$HOME/s10.ledger FROM=1  TO=10 tests/_stage10_event_stream_mutants.sh
#   MUTANT_LEDGER=$HOME/s10.ledger FROM=11 TO=20 tests/_stage10_event_stream_mutants.sh
#   MUTANT_LEDGER=$HOME/s10.ledger FROM=21 TO=30 tests/_stage10_event_stream_mutants.sh
#   MUTANT_LEDGER=$HOME/s10.ledger FROM=31 TO=40 tests/_stage10_event_stream_mutants.sh
FROM="${FROM:-1}"
TO="${TO:-$TOTAL}"
LEDGER="${MUTANT_LEDGER:-}"

_sha() { if command -v sha256sum >/dev/null 2>&1; then sha256sum | cut -d' ' -f1
         else shasum -a 256 | cut -d' ' -f1; fi; }
FINGERPRINT="$(cat "$SC" "$ST" "$PR" "$LD" "$MS" "$GT" "$SB" "$PE" "$OR" "$MM" \
                   "tests/$T" "$0" | _sha)"

if [ -n "$LEDGER" ]; then
    if [ ! -f "$LEDGER" ] || [ "$(head -1 "$LEDGER" 2>/dev/null)" != "fingerprint $FINGERPRINT" ]; then
        printf 'LEDGER RESET — the sources changed since the last window (or there was none).\n'
        printf 'fingerprint %s\n' "$FINGERPRINT" > "$LEDGER"
    fi
fi

# `seen` is the INDEX reached and `ran` is a COUNT of the mutants this window actually graded.
# They are separate because a window makes them different numbers, and the one the abort line
# needs ("how many never ran") is the count — see `_mutant_driver_contract.RUN_COUNTED`.
seen=0; ran=0; red=0; green=0; survivors=""

mutant() {
    local label="$1" rc=0 out idx
    idx=$((seen + 1))
    seen="$idx"
    if [ "$idx" -lt "$FROM" ] || [ "$idx" -gt "$TO" ]; then return 0; fi
    ran=$((ran + 1))
    out="$("$M" "$@")" || rc=$?
    if [ -n "$out" ]; then printf '%s\n' "$out"; fi
    if [ "$rc" -ne 0 ]; then
        printf '\n================================================================================\n'
        printf 'BATCH ABORTED — mutate.sh exited %s on mutant %s of %s\n' "$rc" "$idx" "$TOTAL"
        printf '  mutant: %s\n' "$label"
        printf '  %s of this window graded; THIS BATCH DID NOT PASS — it stopped at %s.\n' \
            "$((ran - 1))" "$idx"
        printf '================================================================================\n'
        exit "$rc"
    fi
    case "$out" in
        RED*)   red=$((red + 1)); _record "$idx" RED "$label" ;;
        GREEN*) green=$((green + 1)); survivors="$survivors  - $label"$'\n'
                _record "$idx" GREEN "$label" ;;
        *)      printf '\nBATCH ABORTED — exit 0 with no verdict on mutant %s of %s (%s): %s\n' \
                    "$idx" "$TOTAL" "$label" "${out:-<nothing>}"; exit 70 ;;
    esac
}

# One line per graded mutant; a re-run of the same index overwrites, so a GREEN that is fixed and
# re-run RED does not leave both claims in the file.
_record() {
    [ -n "$LEDGER" ] || return 0
    local tmp="$LEDGER.$$"
    grep -v "^$1	" "$LEDGER" > "$tmp" 2>/dev/null || true
    printf '%s\t%s\t%s\n' "$1" "$2" "$3" >> "$tmp"
    { head -1 "$tmp"; tail -n +2 "$tmp" | sort -n; } > "$LEDGER"
    rm -f "$tmp"
}

# ==== S · schema + store =======================================================================

mutant "S01 ★★★ the vocabulary opens — a new event type arrives by typing a string" "$ST" \
  '            if event_type not in EVENT_TYPES:' \
  '            if False:' "$T"

# ⚠ S02/S03's FIRST DRAFT MUTATED `Envelope`'S FIELD DEFAULTS AND BOTH SURVIVED — every
# construction of an Envelope passes both explicitly, so those `= None`s were dead text wearing
# the §7g guarantee (§7f). The defaults are DELETED from the schema and the two live signatures
# are graded instead: the method (S02/S03), the public function (S06/S07), and S08 puts the dead
# defaults back to prove the schema now refuses them.

mutant "S02 ★★★ duration_ms defaults to 0 — 'nobody timed it' becomes 'it took no time' (§7g)" "$ST" \
  '             duration_ms: Optional[int] = None, ledger_seq: Optional[int] = None,' \
  '             duration_ms: Optional[int] = 0, ledger_seq: Optional[int] = None,' "$T"

mutant "S03 ★★★ ledger_seq defaults to 0 — an unbacked event claims the hash chain (§7g)" "$ST" \
  '             duration_ms: Optional[int] = None, ledger_seq: Optional[int] = None,' \
  '             duration_ms: Optional[int] = None, ledger_seq: Optional[int] = 0,' "$T"

mutant "S06 ★★★ the PUBLIC emit times every untimed event at 0 — the method's guard is not enough" "$ST" \
  '         run_id: str = "", actor: str = "", duration_ms: Optional[int] = None,' \
  '         run_id: str = "", actor: str = "", duration_ms: Optional[int] = 0,' "$T"

mutant "S07 ★★★ the PUBLIC emit hands every event seq 0 — an unbacked event claims the chain" "$ST" \
  '         ledger_seq: Optional[int] = None) -> Optional[StoredEvent]:' \
  '         ledger_seq: Optional[int] = 0) -> Optional[StoredEvent]:' "$T"

mutant "S08 ★★★ the envelope may FORGET again — the dead defaults restored (§7f, found by S02)" "$SC" \
  '    duration_ms: Optional[int]
    ledger_seq: Optional[int]
    actor: str = ""' \
  '    actor: str = ""
    duration_ms: Optional[int] = None
    ledger_seq: Optional[int] = None' "$T"

mutant "S04 ★★★ the session anchor goes — a resumed session reads a prior one's history" "$ST" \
  '                where.append("session_id = ?")' \
  '                pass' "$T"

mutant "S05 ★★★ the bare connect returns — a SECOND WAL policy, and the quiet one (§7f)" "$ST" \
  '        from ..memory._sqlite import connect_sqlite
        return connect_sqlite(self.path)' \
  '        import sqlite3
        return sqlite3.connect(self.path, timeout=5.0)' "$T"

# ==== P · the register =========================================================================

mutant "P01 ★★★ an UNMAPPED kind projects anyway — a measurement filed as a decision" "$PR" \
  '    "impact": (UNMAPPED, "a blast-radius MEASUREMENT taken during a spec amend. A number, not a "' \
  '    "impact": (GATE_DECISION, "a blast-radius MEASUREMENT taken during a spec amend. A number, not a "' "$T"

mutant "P02 ★★★ a kind leaves the register — the totality claim must break" "$PR" \
  '    "write_approval": (APPROVAL_DECISION, "the minted approval itself"),' \
  '' "$T"

mutant "P03 ★★★ the free-text reason comes back — the privacy hole a test found" "$PR" \
  '        return GateDecision(gate=str(_first(entry, "gate", "hook", default=kind)),
                            decision=str(_first(entry, "decision", default="recorded")),
                            subject=str(_first(entry, "target", "subject", "item_id")))' \
  '        return GateDecision(gate=str(_first(entry, "gate", "hook", default=kind)),
                            decision=str(_first(entry, "reason", "decision", default="recorded")),
                            subject=str(_first(entry, "reason", "target", "subject")))' "$T"

mutant "P04 ★★★ the MemoryOp carries the DIFF the ledger keeps — content in the typed store" "$PR" \
  '                        item_id=str(_first(entry, "item_id", "batch_digest")),' \
  '                        item_id=str(_first(entry, "diff", "item_id", "batch_digest")),' "$T"

mutant "P05 ★★★ the estimate label stops being kind-qualified — guess and measurement look alike" "$PR" \
  '        label = _first(entry, "label", "context")
        if not label:
            mode = _first(entry, "mode")
            label = f"{kind}:{mode}" if mode else kind' \
  '        label = _first(entry, "label", "context", "mode", default=kind)' "$T"

mutant "P06 ★★★ every token row reads as an ESTIMATE — a real count marked a guess" "$PR" \
  '        real = kind == "token_calibration" and isinstance(entry.get("actual"), int)' \
  '        real = False' "$T"

# ==== L · the ledger projection ================================================================

mutant "L01 ★★★ the projection is un-wired from record — §7i, 90 call sites unreached" "$LD" \
  '        self._project(entry)' \
  '        pass' "$T"

mutant "L02 ★★★ the projection can FAIL THE CANONICAL APPEND — the worst trade in the tree" "$LD" \
  '        except Exception:  # noqa: BLE001 — the projection NEVER fails the canonical append
            return' \
  '        except Exception:
            raise' "$T"

mutant "L03 ★★★ root_of_path accepts any path — events written under someone else's root" "$LD" \
  '        if (os.path.basename(full) != LEDGER_FILENAME
                or os.path.basename(audit) != AUDIT_DIRNAME
                or os.path.basename(temp_local) != TEMP_LOCAL_DIRNAME
                or os.path.basename(mokata) != MOKATA_DIR_NAME):
            return None' \
  '        if False:
            return None' "$T"

# ⚠ L04's FIRST DRAFT MUTATED A HANDLER THAT COULD NOT DECIDE ANYTHING. `on = True` was written
# twice — once as the initializer and once in the except — so flipping the handler's copy changed
# no outcome and the mutant survived (§7f). The handler is now `pass`, L04 grades the ONE decision,
# and L05 puts the override back to prove the handler may not re-decide.

mutant "L04 ★★★ doubt resolves the toggle to OFF — a silent disable of an audit surface (§7e)" "$ST" \
  '    on = True
    try:
        from ..config import Surface' \
  '    on = False
    try:
        from ..config import Surface' "$T"

mutant "L05 ★★★ the handler re-decides and says OFF — the §7e answer overridden on the way out" "$ST" \
  '    except Exception:  # noqa: BLE001 — see the register entry; `on` keeps the ON set above
        pass' \
  '    except Exception:  # noqa: BLE001 — see the register entry; `on` keeps the ON set above
        on = False' "$T"

mutant "L06 ★★★ the PUBLIC emit stops swallowing — the observability lane fails its caller" "$ST" \
  '                                         ledger_seq=ledger_seq)
    except Exception:  # noqa: BLE001 — the observability lane never fails its caller
        return None' \
  '                                         ledger_seq=ledger_seq)
    except Exception:
        raise' "$T"

mutant "M01 ★★★ the duration is never measured — doc 99's criterion back at zero" "$MS" \
  '        _emit_tool_call(call_path, op, kind, out, elapsed_ms)' \
  '        _emit_tool_call(call_path, op, kind, out, 0)' "$T"

mutant "M02 ★★★ ok comes from 'did it raise' — a refusal recorded as a success" "$MS" \
  '        ok = status not in ("error", "timed_out", "refused")' \
  '        ok = True' "$T"

mutant "M03 ★★★ the one-exit property goes — timed_out stops being evented (§7e)" "$MS" \
  '        if worker.is_alive():                             # R1 — over budget, still running
            out = _timed_out(op, budget)' \
  '        if worker.is_alive():
            return _timed_out(op, budget)' "$T"

# ==== G · the seventh type =====================================================================

mutant "G01 ★★★ SecretScanHit un-wired — the one type the ledger cannot supply" "$GT" \
  '            self._emit_scan_hits(req, findings, workspace_root)' \
  '            pass' "$T"

mutant "G02 ★★★ one row per FINDING — a reader can count the secrets in a path" "$GT" \
  '            counts: Dict[str, int] = {}
            for f in findings or ():
                k = str(getattr(f, "kind", "") or "secret")
                counts[k] = counts.get(k, 0) + 1' \
  '            counts: Dict[str, int] = {}
            for i, f in enumerate(findings or ()):
                k = str(getattr(f, "kind", "") or "secret") + f":{i}"
                counts[k] = 1' "$T"

# ==== R · clean resume =========================================================================

mutant "R01 ★★★ the clear goes — the additive loop, and the old approval survives the pull" "$SB" \
  '        cleared.extend(clear_session_state(store))' \
  '        pass' "$T"

mutant "R02 ★★★ the clear leaves the commit closure — a DECLINED pull wipes the session" "$SB" \
  '    def _write_all() -> None:' \
  '    cleared.extend(clear_session_state(store))

    def _write_all() -> None:' "$T"

mutant "R03 ★★★ the clear set narrows to one declaration — approved_refinements survives (§7j)" "$SB" \
  '    declared = set(_SESSION_KEYS) | set(SESSION_SCOPED_KEYS)' \
  '    declared = set(_SESSION_KEYS)' "$T"

mutant "R04 ★★★ the clear is a SILENT side effect — right behaviour, and a report that denies it" "$SB" \
  '                      cleared=list(cleared))' \
  '                      cleared=[])' "$T"

# ==== C · R1.S1c ===============================================================================

mutant "C01 ★★★ the progress projection un-wired — the log stays a second store after all" "$PE" \
  '        _project_event(self.path, entry)' \
  '        pass' "$T"

mutant "C02 ★★★ the zero-spend suppression goes — a simulated run reports costing nothing (§7g)" "$OR" \
  '        if result.actual_total <= 0:' \
  '        if False:' "$T"

mutant "C03 ★★★ a review verdict files as a PHASE TRANSITION — the gate's decision stops being one" "$PE" \
  '            payload = GateDecision(gate="review", decision=str(verdict), subject=stage)' \
  '            payload = PhaseTransition(phase=stage, outcome=str(verdict))' "$T"

mutant "C04 ★★★ the memory READ stops eventing — the half the ledger cannot see goes dark again" "$MM" \
  '        self._emit_memory_read(n)' \
  '        pass' "$T"

# ==== D · R1.S1d's guard, graded by PLANTING what the measurement says is absent ================
# ⭐ These two are the only mutants in this batch that make the tree WORSE rather than undoing a
# fix, and that is the only way a corpus guard can be graded: R1.S1d's finding is "there are no
# internal print-diagnostics and no internal logging", so the guard is worth exactly what it
# catches when one appears (§7i — a guard whose corpus finds nothing grades nothing).

mutant "D01 ★★★ an internal print-diagnostic appears and the register does not notice" "$PR" \
  'def _as_int(value: Any) -> int:' \
  'def _as_int(value: Any) -> int:
    print("projection: coercing", value)' "$T"

mutant "D02 ★★★ an internal module starts importing logging and the guard sleeps through it" "$ST" \
  'import json
import os' \
  'import json
import logging
import os' "$T"

# ==== F01-F05 · what the INDEPENDENT REVIEW found, and what had nothing watching ===============
#
# ⚠ L04-L06 GRADE THE TOGGLE'S §7e DECISION — which arm doubt resolves to — and all three were
# RED while the toggle was being consulted for exactly ONE of the six producers. A mutant over
# *how* a question is answered cannot find that the question is never asked.

mutant "F01 ★★★ the toggle is honoured only for a Surface — five of six producers ignore it (F1)" "$ST" \
  '        elif not enabled_for_root(root):' \
  '        elif False:' "$T"

mutant "F02 ★★★ an events DB is created in a directory that is not a repo (F2)" "$ST" \
  '        if not Surface.is_initialized(root):' \
  '        if False:' "$T"

mutant "F03 ★★★ the clean resume stops reaching the pre-MS.S2 singleton — the APPROVAL survives (F3)" "$SB" \
  '    clear_one = getattr(store, "delete_session_state", None) or store.delete' \
  '    clear_one = store.delete' "$T"

mutant "F04 ★★★ the stronger clear stops reaching the legacy name — same defect, other end (F3)" "$SS" \
  '            gone = self._base.delete(name) or gone' \
  '            gone = gone' "$T"

mutant "F05 ★★★ the retrieval label is unbounded again — a 500-item manifest in an audit field (F4)" "$BG" \
  '        shown = ids[:RETRIEVAL_LABEL_ID_CAP]' \
  '        shown = ids' "$T"

printf '\n================================================================================\n'
printf 'STAGE 10 EVENT-STREAM MUTANTS — window %s..%s of %s: %s RED, %s GREEN\n' \
    "$FROM" "$TO" "$TOTAL" "$red" "$green"
if [ -n "$survivors" ]; then
    printf 'SURVIVORS IN THIS WINDOW (each is a row whose fix does not grade):\n%s' "$survivors"
fi

if [ -z "$LEDGER" ]; then
    if [ "$FROM" -eq 1 ] && [ "$TO" -ge "$TOTAL" ] && [ -z "$survivors" ]; then
        printf 'ALL %s MUTANTS RED IN ONE RUN.\n' "$TOTAL"
        printf '================================================================================\n'
        exit 0
    fi
    printf 'NO LEDGER AND NOT A WHOLE-BATCH RUN — THIS IS NOT A BATCH VERDICT.\n'
    printf '================================================================================\n'
    exit 70
fi

reds="$(tail -n +2 "$LEDGER" | awk -F'\t' '$2 == "RED" {print $1}' | sort -n -u | wc -l | tr -d ' ')"
greens="$(tail -n +2 "$LEDGER" | awk -F'\t' '$2 == "GREEN" {print $1}' | wc -l | tr -d ' ')"
printf 'LEDGER %s: %s of %s indices RED, %s GREEN\n' "$LEDGER" "$reds" "$TOTAL" "$greens"
if [ "$reds" -eq "$TOTAL" ] && [ "$greens" -eq 0 ]; then
    printf 'BATCH PASSED — every one of the %s mutants is RED against this exact source.\n' "$TOTAL"
    printf '================================================================================\n'
    exit 0
fi
printf 'BATCH INCOMPLETE — run the remaining windows. Missing indices:\n'
i=1
while [ "$i" -le "$TOTAL" ]; do
    grep -q "^$i	RED	" "$LEDGER" || printf '  %s\n' "$i"
    i=$((i + 1))
done
if [ "$greens" -ne 0 ]; then
    printf 'AND THERE ARE SURVIVORS IN THE LEDGER:\n'
    tail -n +2 "$LEDGER" | awk -F'\t' '$2 == "GREEN" {print "  - " $3}'
fi
printf '================================================================================\n'
exit 70
