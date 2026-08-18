#!/usr/bin/env bash
# Drives the 0.0.18 stage-12 (lane D slice 3, `migrate`) mutant list through scripts/mutate.sh —
# the ONLY sanctioned mutator (doc 85 §7b). Never hand-edit a file to see whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python tests/_stage12_migrate_slice_mutants.sh
#
# ⚠ THE NAME CARRIES ITS SUBJECT, NOT JUST ITS STAGE NUMBER. `tests/_stage12_mutants.sh` would
# collide with 0.0.17's stage 12 — stage numbers repeat across releases in this repo. Stages 10
# and 11 hit this first and answered it the same way.
#
# Consumes mutate.sh's EXIT CONTRACT: a VERDICT (RED/GREEN) is a completed run; any harness
# failure STOPS THE BATCH DEAD, because on exit 4 the mutator deliberately leaves the target
# mutated. Satisfies tests/_mutant_driver_contract.py, which is a live pin.
#
# ★ STEP 0 — THE GREEN BASELINE, AND IT IS NOT OPTIONAL (MUTANT-DRIVER-NO-GREEN-BASELINE, doc 84).
# Without it a batch cannot tell "the mutant was caught" from "these tests were already failing".
#
# ⚠ IT LIVES IN tests/, NOT IN docs/build/handoff/. `sync-public.sh` excludes docs/build/, so a
# driver filed beside its report is ABSENT from the public mirror and a contributor grading this
# repo would run an incomplete corpus that READS as complete.
#
# ⭐ WHAT THIS SLICE IS, AND WHY GROUP E IS FIVE LINES. `migrate_channels.py` IS the vault migrator
# — every symbol in it serves `_migrate_vault`, which is slice 4's. So this slice deletes almost
# nothing and instead FIXES A SURVIVOR. Group A is therefore the biggest group, and it grades a
# bug fix rather than a removal. The row's largest file (`memory/migrate.py`, 430) is a FEATURE.
#   A. the both-handles leak   the row's subject — closes registered where handles are CREATED
#   B. `migrate_memory`        the SURVIVOR, on a real out-and-back round trip
#   C. the removal answer      generalises to a channel removed AFTER this stage, not just today's
#   D. the gate                still CLOSED — vault + neo4j remain, and vault is NOT this slice's
#   E. BACKCOMPAT-SWEEP        the dead channel dispatcher is gone, and what it fed still works

set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"
PY="${PYTHON:-python3}"

MM=src/mokata/memory/migrate.py
MC=src/mokata/migrate_channels.py
MIG=src/mokata/cli_commands/migrate.py
DEP=src/mokata/deprecation.py
DR=tests/_deprecation_removal.py

T12='test_stage12_migrate_slice.py'
T35C='test_stage35c_memory_migrate.py'
T17='test_stage17_vault_rehome_gated.py'

TOTAL=24
ran=0; red=0; green=0; survivors=""

# ---- step 0: the green baseline ---------------------------------------------------------------
printf '================================================================================\n'
printf 'STEP 0 — GREEN BASELINE (no mutation applied). Nothing is graded until this passes.\n'
printf '================================================================================\n'
for pat in "$T12" "$T35C" "$T17"; do
    if ! PYTHONDONTWRITEBYTECODE=1 "$PY" -m unittest discover -s tests -t tests \
            -p "$pat" >/dev/null 2>&1; then
        printf '\nBATCH REFUSED — %s is not green before mutant 1.\n' "$pat"
        printf 'Every RED this batch could print would be unattributable. Fix the tree first.\n'
        exit 75
    fi
    printf 'baseline GREEN: %s\n' "$pat"
done
printf 'Grading %s mutants.\n\n' "$TOTAL"

mutant() {
    local label="$1" rc=0 out
    ran=$((ran + 1))
    out="$("$M" "$@")" || rc=$?
    if [ -n "$out" ]; then printf '%s\n' "$out"; fi
    if [ "$rc" -ne 0 ]; then
        printf '\n================================================================================\n'
        printf 'BATCH ABORTED — mutate.sh exited %s on mutant %s of %s\n' "$rc" "$ran" "$TOTAL"
        printf '  mutant: %s\n' "$label"
        printf '  %s of %s mutants NEVER RAN. THIS BATCH DID NOT PASS — it stopped here.\n' \
            "$((TOTAL - ran))" "$TOTAL"
        printf '================================================================================\n'
        exit "$rc"
    fi
    case "$out" in
        RED*)   red=$((red + 1)) ;;
        GREEN*) green=$((green + 1)); survivors="$survivors  - $label"$'\n' ;;
        *)      printf '\nBATCH ABORTED — exit 0 with no verdict on mutant %s of %s (%s): %s\n' \
                    "$ran" "$TOTAL" "$label" "${out:-<nothing>}"; exit 70 ;;
    esac
}

# ==== A. the both-handles leak — MIGRATE-BACKENDS-UNCLOSED-ON-RAISE =============================

mutant "A01 ***  THE ROW ITSELF — the source handle is never registered, so every raise after the
       build leaks it, exactly as at HEAD" "$MM" \
  '        handles.callback(source.close)' \
  '        pass    # source close NOT registered' "$T12"

mutant "A02 ***  the destination handle is never registered — the other half of the pair" "$MM" \
  '        handles.callback(dest.close)' \
  '        pass    # dest close NOT registered' "$T12"

mutant "A03 ***  THE OLD SHAPE RESTORED, AND IT LOOKS CORRECT — a close on the exit path instead
       of at the handle. Catches MigrateError and nothing else, which is the leak" "$MM" \
  '        handles.callback(source.close)

        # Build the destination NON-degrading — a failure here writes NOTHING (degrade-clean).
        try:
            dest = build_named_backend(to_backend, root, manifest.tool_config(to_backend),
                                       project=project)
        except MigrateError as exc:' \
  '
        # Build the destination NON-degrading — a failure here writes NOTHING (degrade-clean).
        try:
            dest = build_named_backend(to_backend, root, manifest.tool_config(to_backend),
                                       project=project)
        except MigrateError as exc:
            source.close()' "$T12"

mutant "A04 **   a bare close is re-added before a return — the handle is closed TWICE on that
       path, which a boolean 'was it closed' pin could not see" "$MM" \
  '                                 aborted=True, message="declined at the human gate")' \
  '                                 aborted=True, message="declined at the human gate") if not \
                source.close() else None' "$T12"

mutant "A05 **   the stack is unwound BEFORE the migration runs — the handles close, then every
       write goes to a closed store" "$MM" \
  '        items: List[Any] = source.all()' \
  '        handles.close()
        items: List[Any] = source.all()' "$T12"

mutant "A06 **   A BARE CLOSE COMES BACK ON THE ONE PATH CI CANNOT RUN — inside the \`to_funnel\`
       (postgres) branch. Every behavioural pin above is blind to it because CI has no Postgres,
       so this is the offender only the SHAPE pin can see (doc 85 §7f, 2026-08-04 amendment)" "$MM" \
  '            except Exception as exc:                 # unreachable mid-migration → write NOTHING
                return MigrateResult(from_backend=src_tool, to_backend=to_backend, aborted=True,' \
  '            except Exception as exc:                 # unreachable mid-migration → write NOTHING
                source.close()
                return MigrateResult(from_backend=src_tool, to_backend=to_backend, aborted=True,' "$T12"

# ==== B. `migrate_memory` — THE SURVIVOR, and the largest file the row counted here =============

mutant "B01 ***  the migration writes NOTHING and still reports success — the round trip lands an
       empty destination" "$MM" \
  '            commit=((lambda it=it: _journal_it(it)) if to_funnel else (lambda it=it: dest.put(it))),' \
  '            commit=((lambda it=it: _journal_it(it)) if to_funnel else (lambda it=it: None)),' "$T12"

mutant "B02 ***  the migration becomes DESTRUCTIVE without --drop-source — the source is emptied
       by a plain migrate, which is the one thing this command promises never to do" "$MM" \
  '            if outcome.committed:
                migrated_items.append(it)' \
  '            if outcome.committed:
                source.delete(it.id)
                migrated_items.append(it)' "$T12"

mutant "B03 **   PROVENANCE is dropped on the way across — values survive, authorship does not,
       and a round trip that only compared values would never notice" "$MM" \
  '    return hashlib.sha256(json.dumps(' \
  '    item.provenance.pop("author", None)
    return hashlib.sha256(json.dumps(' "$T12"

mutant "B04 **   a destination this release supports is dropped from SUPPORTED — the survivor
       loses a leg" "$MM" \
  'SUPPORTED = ("sqlite", "postgres", "pgvector")' \
  'SUPPORTED = ("sqlite", "postgres")' "$T12"

mutant "B05 ***  THE BOUNDARY BREAKS — the channel migrator acquires a memory-store dependency,
       which is how a deletion slice takes a survivor with it by association" "$MC" \
  'CHANNELS = ("vault",)' \
  'from .memory.migrate import migrate_memory   # noqa: F401
CHANNELS = ("vault",)' "$T12"

# ==== C. the removal answer, GENERALISED to a channel removed AFTER this stage ==================

mutant "C01 ***  THE MUTANT THIS STAGE EXISTS TO CATCH — the dispatch stops reading the registry
       and hardcodes today's three channels. GREEN for every channel that is already removed,
       and a TYPO answer for every channel removed after this stage" "$MIG" \
  '    if channel in REMOVED_CHANNELS:' \
  '    if channel in ("obsidian", "native-memory", "memory-share"):' "$T12"

mutant "C02 ***  argparse REFUSES the word — 'invalid choice' (exit 2), which is what it says
       about a TYPO, on the command every 0.0.17 notice told the user to run" "$MIG" \
  '    p.add_argument("channel", choices=tuple(CHANNELS) + tuple(REMOVED_CHANNELS),' \
  '    p.add_argument("channel", choices=tuple(CHANNELS),' "$T12"

mutant "C03 ***  the RECORD CLASS stops being chosen by TYPE — a file channel gets the backend
       refusal, i.e. a downgrade charged for a file this release reads (slice 2's false refusal)" \
  "$DEP" \
  '    return isinstance(REMOVED.get(channel), RemovedFileNotice)' \
  '    return False' "$T12"

mutant "C04 **   --help ADVERTISES the removed channels — selling a channel that is gone, while
       the answer path exists to say it is gone" "$MIG" \
  '                   metavar="{%s}" % ",".join(CHANNELS),' \
  '                   metavar="{%s}" % ",".join(tuple(CHANNELS) + tuple(REMOVED_CHANNELS)),' "$T12"

mutant "C05 **   a removal record that names no release — the user is told a thing was removed
       and not which release did it" "$DEP" \
  '    remedy: str             # the one way to bring it into the canonical store
    removed: str = REMOVAL_RELEASE' \
  '    remedy: str             # the one way to bring it into the canonical store
    removed: str = "an earlier release"' "$T12"

# ==== D. the gate is STILL CLOSED, and vault is NOT this slice's ================================

mutant "D01 ***  THE SCOPE VIOLATION THIS STAGE MUST NOT COMMIT — vault leaves CHANNELS one slice
       early, taking the live migration path with it" "$MC" \
  'CHANNELS = ("vault",)' \
  'CHANNELS = ()' "$T12"

mutant "D02 ***  the gate's probe stops seeing vault, so the lane READS finished while two
       channels are still implemented" "$DR" \
  '    "vault": "mokata.vault",' \
  '    "vault": "mokata.vault_gone_module",' "$T12"

mutant "D03 **   vault stops being ANNOUNCED as deprecated — the lane goes quiet about a channel
       it has not removed" "$DEP" \
  'DEPRECATED_CHANNELS = tuple(CHANNELS)' \
  'CHANNELS.pop("vault", None)
DEPRECATED_CHANNELS = tuple(CHANNELS)' "$T12"

# ==== E. BACKCOMPAT-SWEEP, and what the swept dispatcher fed ====================================

mutant "E01 ***  THE SWEEP IS REVERTED — the dead channel dispatcher comes back, with the
       unreachable arm that made it dead" "$MC" \
  'def _vault_tags(surface: Any) -> List[str]:' \
  'def _source_subjects(surface: Any, channel: str) -> List[str]:
    if channel == "vault":
        return _vault_tags(surface)
    return []


def _vault_tags(surface: Any) -> List[str]:' "$T12"

mutant "E02 ***  THE ARM THE SWEEP HAD TO KEEP — the unknown-channel guard goes, so an unknown
       channel previews 'nothing to migrate' instead of raising. 'Empty' and 'not a channel'
       collapse into one answer (§7g), which is what the deleted [] fallback used to do" "$MC" \
  '    if channel not in CHANNELS:
        raise ValueError(f"unknown migration channel '"'"'{channel}'"'"' (one of {CHANNELS})")' \
  '    if False:
        raise ValueError(f"unknown migration channel '"'"'{channel}'"'"' (one of {CHANNELS})")' "$T12"

mutant "E03 **   the run-side unknown guard goes too — an unknown channel is dispatched straight
       into the vault migrator" "$MC" \
  '    if channel not in CHANNELS:
        return ChannelMigrateResult(channel=channel, aborted=True,' \
  '    if False:
        return ChannelMigrateResult(channel=channel, aborted=True,' "$T12"

mutant "E04 ***  the PREVIEW stops reading the vault and reports an empty one — the sweep removed
       the only wrapper around this call, so nothing else would notice" "$MC" \
  '    subjects = _vault_tags(surface)' \
  '    subjects = []' "$T12"

mutant "E05 **   the preview MINTS THE ONE-TIME MARKER — a read-only plan with a durable side
       effect, so the real migration afterwards says 'already migrated'" "$MC" \
  '    target = "the canonical transport"' \
  '    _write_marker(surface.mokata_dir, channel, 0)
    target = "the canonical transport"' "$T12"

# ==== verdict ==================================================================================

printf '\n================================================================================\n'
printf 'STAGE-12 MIGRATE-SLICE MUTANTS: %s ran of %s — %s RED, %s GREEN\n' \
    "$ran" "$TOTAL" "$red" "$green"
if [ "$green" -ne 0 ]; then
    printf 'SURVIVORS (each is a pin that does not grade):\n%s' "$survivors"
    printf '================================================================================\n'
    exit 1
fi
printf 'Every mutant was caught.\n'
printf '================================================================================\n'
