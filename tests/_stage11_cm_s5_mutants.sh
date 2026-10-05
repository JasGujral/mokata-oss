#!/usr/bin/env bash
# 0.0.21 stage 11 `cm-s5`, graded. Through scripts/mutate.sh.
#
#   PYTHON=/path/to/venv/bin/python tests/_stage11_cm_s5_mutants.sh < /dev/null
#
#   S01-S06 ★★★ the v6 schema: the version, the floor that must NOT move, the refusal on a
#     non-empty v5 table, the retirement of the trio, the idempotency index, and `local_seq`.
#   S07-S08 ★★★ the FEATURE floor (review F1): the number, and unreadable-is-not-permission.
#   N01-N05 ★★★ NOTIFY: the three-state verdict (the §7j bug my own junk-input test found), the
#     constant channel, the payload cap, identifiers-only, and the after-the-inserts ordering.
#   N06     ★★★ ...and the CONDITION (review F6): no doorbell when nothing landed.
#   P01-P05 ★★★ the publish: opt-in by default, the egress GATE's kind, the scan reading the real
#     bytes, the batch cap's resume, and the borrowed connection not being closed.
#   T01-T02 ★★★ the own-trail predicate: the shared prefix, and not keying on a substitutable tool.
#   A01-A02 ★★★ the resume tail: oldest-first, and keyed on seq rather than a coarse timestamp.
#   A03-A06 ★★★ the resume POINTER (review F2/F4/F7), which had NO mutant at all: the
#     pointer-ahead refusal, the pointer-stuck refusal, the three-way `max_seq`, and the exact
#     backlog count that used to be a 0/1 flag.
#   X01     ★★★ the MCP status word (review F5): a deduplicated success is not a refusal.
#   I01-I03 ★★★ the invariant guard: the derived owner set, the holder predicate itself, and the
#     attribute-qualified constructor the first version could not see (review F3).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"

TD=src/mokata/teamdb.py
TE=src/mokata/team_events.py
ST=src/mokata/events/store.py
CE=src/mokata/cli_commands/events.py
CL=src/mokata/cli.py
T='test_a40_the_shared_stream_and_the_per_call_store.py'

TOTAL=31

# ⚠ WHY THIS BATCH RUNS IN WINDOWS, AND WHY A WINDOW IS NOT A VERDICT. Each mutant re-runs the
# whole of the subject module named in $T, and the harness this is driven from caps one call at
# ~180s. So FROM/TO run an index window, windows accumulate into a LEDGER keyed by a FINGERPRINT
# of every file this batch mutates, and ONLY the ledger reports a PASS: all $TOTAL indices
# present and RED. A window that comes back all-RED says nothing about the batch (§7e).
#
# ⛔ BOTH SENTENCES ABOVE USED TO NAME A MODULE AND A NUMBER THAT WERE NOT THIS BATCH'S --
# "the whole of test_a39" (the subject is $T) and "all 30 indices" over a 22-row file. The SCORE
# was honest, because the ledger check compares against $TOTAL; the stated CONTRACT was not, and
# a reader checking this batch against its own description would have checked the wrong thing
# (review F9, §7b). Both now read off the variables, so neither can drift again.
#
#   MUTANT_LEDGER=$HOME/s11.ledger FROM=1  TO=8  tests/_stage11_cm_s5_mutants.sh
#   MUTANT_LEDGER=$HOME/s11.ledger FROM=9  TO=16 tests/_stage11_cm_s5_mutants.sh
#   MUTANT_LEDGER=$HOME/s11.ledger FROM=17 TO=24 tests/_stage11_cm_s5_mutants.sh
#   MUTANT_LEDGER=$HOME/s11.ledger FROM=25 TO=31 tests/_stage11_cm_s5_mutants.sh
FROM="${FROM:-1}"
TO="${TO:-$TOTAL}"
LEDGER="${MUTANT_LEDGER:-}"

_sha() { if command -v sha256sum >/dev/null 2>&1; then sha256sum | cut -d' ' -f1
         else shasum -a 256 | cut -d' ' -f1; fi; }
FINGERPRINT="$(cat "$TD" "$TE" "$ST" "$CE" "$CL" "tests/$T" "$0" | _sha)"

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


# ==== S · the v6 shared schema =================================================================

mutant "S01 ★★★ the version does not move — a v6 artifact stamps itself v5 and clients mis-read it" "$TD" \
  'TEAM_SCHEMA_VERSION = 6' \
  'TEAM_SCHEMA_VERSION = 5' "$T"

mutant "S02 ★★★ the FLOOR moves with it — every existing v3/v4 team fail-closes for an opt-in table" "$TD" \
  'TEAM_SCHEMA_MIN_SUPPORTED = 3' \
  'TEAM_SCHEMA_MIN_SUPPORTED = 6' "$T"

mutant "S03 ★★★ the refusal goes — three columns of SOMEBODY ELSE'S data dropped on a measurement" "$TD" \
  '        RAISE EXCEPTION ' \
  '        RAISE NOTICE ' "$T"

mutant "S04 ★★★ the v5 trio survives — two names for one thing, and \`kind\` collides with the ledger's" "$TD" \
  'f"ALTER TABLE {EVENTS_TABLE} DROP COLUMN IF EXISTS payload",' \
  'f"SELECT 1",' "$T"

mutant "S05 ★★★ the idempotency index goes — a re-publish duplicates every row it already sent" "$TD" \
  'f"CREATE UNIQUE INDEX IF NOT EXISTS {EVENTS_TABLE}_namespace_event"' \
  'f"CREATE INDEX IF NOT EXISTS {EVENTS_TABLE}_namespace_event"' "$T"

mutant "S06 ★★★ the envelope loses a column — the §7g pair has nowhere to land at the team boundary" "$TD" \
  '        "  duration_ms BIGINT, ledger_seq BIGINT, data TEXT, local_seq BIGINT)",' \
  '        "  data TEXT, local_seq BIGINT)",' "$T"

# ==== N · the doorbell =========================================================================

mutant "N01 ★★★ the §7j bug restored — one axis of a three-state answer, so junk reads 'deliverable'" "$TE" \
  '        verdict = inspect_dsn(dsn).verdict' \
  '        verdict = "pooled" if inspect_dsn(dsn).pooled else "direct"' "$T"

mutant "N02 ★★★ the channel stops being a legal identifier — a per-project name truncates to one" "$TE" \
  'NOTIFY_CHANNEL = "mokata_events"' \
  'NOTIFY_CHANNEL = "Mokata Events Channel"' "$T"

mutant "N03 ★★★ the payload cap goes — a pathological actor is sent to the server to be rejected" "$TE" \
  '        if len(payload.encode("utf-8")) > NOTIFY_PAYLOAD_CAP_BYTES:
            payload = json.dumps({"ns": ns, "n": int(landed), "to": int(high_water)},
                                 sort_keys=True)' \
  '        if False:
            payload = json.dumps({"ns": ns, "n": int(landed), "to": int(high_water)},
                                 sort_keys=True)' "$T"

mutant "N04 ★★★ the doorbell carries the delivery — the event's data rides the notification" "$TE" \
  '        payload = json.dumps({"ns": ns, "actor": who, "n": int(landed),
                              "to": int(high_water)}, sort_keys=True)' \
  '        payload = json.dumps({"ns": ns, "actor": who, "n": int(landed),
                              "to": int(high_water), "data": [str(landed)]}, sort_keys=True)' "$T"

mutant "N05 ★★★ the NOTIFY fires BEFORE the inserts — a doorbell for an empty doorstep" "$TE" \
  '            landed = log.append_all(ns, who, batch, project=ns)
            box["landed"] = landed' \
  '            box["sent"] = log.notify(ns, who, 0, int(batch[-1].seq))
            landed = log.append_all(ns, who, batch, project=ns)
            box["landed"] = landed' "$T"

# ⛔ N05's PATTERN WENT STALE the moment the F6 repair touched the line under it, and a stale
# pattern exits 3 and ABORTS THE REST OF THE BATCH — every mutant after it ungraded while the run
# looked busy. Caught by `test_a9_mutant_batches_are_swept`, which is the guard that exists for
# exactly this. Its anchor is now the two lines the repair does not touch.
mutant "N06 ★★★ the doorbell rings when NOTHING landed — review F6, the condition not the order" "$TE" \
  '            box["sent"] = (log.notify(ns, who, landed, int(batch[-1].seq)) if landed else "")' \
  '            box["sent"] = log.notify(ns, who, landed, int(batch[-1].seq))' "$T"

# ==== P · the publish is EGRESS ================================================================

mutant "P01 ★★★ sharing defaults ON — an observability lane becomes an egress channel by default" "$TE" \
  '    return bool(_settings(data).get("shared"))' \
  '    return bool(_settings(data).get("shared", True))' "$T"

mutant "P02 ★★★ the gate kind stops being \`send\` — the egress rule no longer applies to egress" "$TE" \
  '            WriteRequest(kind="send", target=f"{PUBLISH_TARGET_PREFIX}{env_name}/{ns}",' \
  '            WriteRequest(kind="code", target=f"{PUBLISH_TARGET_PREFIX}{env_name}/{ns}",' "$T"

mutant "P03 ★★★ the scan reads a SUMMARY instead of the bytes leaving — a secret walks out" "$TE" \
  '             "data": e.data}, sort_keys=True) for e in batch)' \
  '             "data": {}}, sort_keys=True) for e in batch)' "$T"

mutant "P04 ★★★ the batch cap goes — one publish sends the whole store in one statement" "$TE" \
  '        batch = fetched[:PUBLISH_BATCH_CAP]' \
  '        batch = fetched' "$T"

# Anchored on the RETURN above it, because `if client is None: log.close()` appears twice (the
# preview path has the same guard for the same reason) and a two-match pattern is exit 3.
mutant "P05 ★★★ a BORROWED connection is closed by the callee that borrowed it" "$TE" \
  '                             message=msg)
    finally:
        if client is None:
            log.close()' \
  '                             message=msg)
    finally:
        log.close()' "$T"

# ==== T · the publisher's own trail ============================================================

mutant "T01 ★★★ the trail predicate stops matching — \`only-own-trail\` never fires again" "$TE" \
  '    return str(data.get("subject", "")).startswith(PUBLISH_TARGET_PREFIX)' \
  '    return False' "$T"

mutant "T02 ★★★ it keys on the event TYPE alone — every gate decision reads as the publisher's own" "$TE" \
  '    if getattr(event, "type", "") != "gate_decision":
        return False' \
  '    if getattr(event, "type", "") != "gate_decision":
        return False
    return True' "$T"

# ==== A · the resume tail ======================================================================

mutant "A01 ★★★ the tail comes back NEWEST first — a backlog publishes its head and loses the middle" "$ST" \
  '                    "ORDER BY seq ASC LIMIT ?", (int(seq), max(1, int(limit)))).fetchall()' \
  '                    "ORDER BY seq DESC LIMIT ?", (int(seq), max(1, int(limit)))).fetchall()' "$T"

mutant "A02 ★★★ the resume keys on the coarse TIMESTAMP — same-second events re-send or vanish" "$ST" \
  '                    "duration_ms, ledger_seq, data FROM events WHERE seq > ? "' \
  '                    "duration_ms, ledger_seq, data FROM events WHERE ts > ? "' "$T"

# ==== I · the invariant guard itself ===========================================================
# ⭐ These mutate the TEST's own derivation, which is the only way to grade a guard: a sweep whose
# owner set collapses passes for the wrong reason, and so does a holder predicate that sees nothing.

mutant "I01 ★★★ the owner derivation collapses — the sweep grades an empty corpus (§7i)" "tests/$T" \
  '_CONNECTION_FACTORIES = ("connect_psycopg", "connect_sqlite")' \
  '_CONNECTION_FACTORIES = ("a_factory_that_does_not_exist",)' "$T"

mutant "I02 ★★★ the holder predicate stops seeing module-level state — the invariant is unguarded" "tests/$T" \
  '        for node in tree.body:                               # module level only
            if isinstance(node, (ast.Assign, ast.AnnAssign)):' \
  '        for node in []:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):' "$T"

mutant "I03 ★★★ the holder predicate goes blind to an ATTRIBUTE-QUALIFIED constructor — review F3" "tests/$T" \
  '    if isinstance(func, ast.Attribute) and func.attr in owners:
        return func.attr' \
  '    if False:
        return func.attr' "$T"

# ==== S07-S08 · the FEATURE FLOOR — review F1 =================================================
#
# ⚠ THE STAGE SHIPPED A MUTANT ON THE FLOOR CONSTANT (S02) AND NONE ON THE BEHAVIOUR THE CONSTANT
# IS NAMED FOR. That is how a pin came to encode a false premise (§7h): the global floor was
# justified by a "named degrade" that did not exist, and killing S02 proved only that nobody had
# retyped a number.

mutant "S07 ★★★ the FEATURE floor drops to the GLOBAL floor — a v5 store is served, and tracebacks" "$TD" \
  'EVENTS_SCHEMA_MIN = 6' \
  'EVENTS_SCHEMA_MIN = TEAM_SCHEMA_MIN_SUPPORTED' "$T"

mutant "S08 ★★★ an UNREADABLE schema version reads as PERMISSION — unknown is not permission" "$TE" \
  '        if not present or version is None:' \
  '        if False:' "$T"

# ==== A03-A06 · the RESUME POINTER — review F2, F4, F7 ========================================
#
# ⚠ A01/A02 mutate `after_seq`'s ORDERING and its KEY. Neither touches its ARGUMENT, which is
# where the defect was: the pointer that decides what gets published had no mutant and no
# behavioural test, and three separate report-success-over-a-skip paths ran through it.

mutant "A03 ★★★ the POINTER-AHEAD predicate always says no — a skipped store reports in_sync" "$TE" \
  '    return local_high > 0 and mark > local_high' \
  '    return False' "$T"

mutant "A04 ★★★ `max_seq` swallows into 0 — an unreadable store reads as an empty one (§7g)" "$ST" \
  '        except Exception:  # noqa: BLE001 — reported as None, NEVER as "empty"
            return None' \
  '        except Exception:  # noqa: BLE001 — reported as None, NEVER as "empty"
            return 0' "$T"

mutant "A05 ★★★ the POINTER-STUCK refusal is deleted — a stalled publisher reports committed" "$TE" \
  '        if landed == 0 and remaining_after:' \
  '        if False and remaining_after:' "$T"

mutant "A06 ★★★ `remaining` goes back to the CAPPED length — a count becomes a 0/1 flag" "$TE" \
  '        remaining_after = max(0, local_high - int(batch[-1].seq))' \
  '        remaining_after = max(0, len(fetched) - len(batch))' "$T"

# ==== X01 · the MCP status word — review F5 ====================================================

mutant "X01 ★★★ the status word stops recognising a COMMITTED publish — success reads as a refusal" \
  "src/mokata/mcp/tools_team.py" \
  '    if committed and reason == COMMITTED_REASON:' \
  '    if committed and reason == COMMITTED_REASON and False:' "$T"

printf '\n================================================================================\n'
printf 'STAGE 11 CM.S5 MUTANTS — window %s..%s of %s: %s RED, %s GREEN\n' \
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
