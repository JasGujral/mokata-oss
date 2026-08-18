#!/usr/bin/env bash
# Drives the 0.0.18 stage-11 (lane D slice 2) mutant list through scripts/mutate.sh — the ONLY
# sanctioned mutator (doc 85 §7b). Never hand-edit a file to see whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python tests/_stage11_memory_share_channel_mutants.sh
#
# ⚠ THE NAME CARRIES ITS SUBJECT, NOT JUST ITS STAGE NUMBER, AND THAT IS DELIBERATE.
# `tests/_stage11_mutants.sh` ALREADY EXISTS — it is 0.0.17's stage 11. Stage numbers repeat
# across releases in this repo (0.0.17 had 10, 11, 12 and 13 too), so a bare `_stageN_mutants.sh`
# is a collision waiting to overwrite someone else's batch. Stage 10 hit this first and answered it
# the same way (`_stage10_removed_backends_mutants.sh`).
#
# Consumes mutate.sh's EXIT CONTRACT: a VERDICT (RED/GREEN) is a completed run; any harness
# failure STOPS THE BATCH DEAD, because on exit 4 the mutator deliberately leaves the target
# mutated. Driver shape from _stage10_removed_backends_mutants.sh — MUTANT-DRIVER-CONTRACT-
# DUPLICATED, doc 84 — and it satisfies tests/_mutant_driver_contract.py, which is a live pin.
#
# ★ STEP 0 — THE GREEN BASELINE, AND IT IS NOT OPTIONAL (MUTANT-DRIVER-NO-GREEN-BASELINE, doc 84).
# Without it a batch cannot tell "the mutant was caught" from "these tests were already failing",
# and every RED it prints is unattributable.
#
# ⚠ IT LIVES IN tests/, NOT IN docs/build/handoff/. `sync-public.sh` excludes docs/build/, so a
# driver filed beside its report is ABSENT from the public mirror and a contributor grading this
# repo would run an incomplete corpus that READS as complete.
#
# WHAT THE STAGE MUST NOT BE ABLE TO LOSE, one group per mechanism. Note the shape: A is the
# deletion and it is the SMALLEST group. This slice is not mostly a deletion — it is mostly the
# answer that stops the deletion from charging a user a downgrade for a file we can still read.
#   A. the removal itself      the channel's two symbols are gone, and the probe can SEE it
#   B. the answer at `migrate` the surface the 0.0.17 notice sent people to — §7g's whole point
#   C. the two record classes  a backend's remedy rendered over a file channel is a FALSE refusal
#   D. what SURVIVES           `memory export`/`import`, on a real file (slice 1's rule)
#   E. the gate                still CLOSED, and the probe target still points at the channel
#   F. BACKCOMPAT-SWEEP        the parameters the deletion stranded are deleted, not defaulted
#   G. no hiding               the record is not a reader, and no module regains the filename

set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"
PY="${PYTHON:-python3}"

DEP=src/mokata/deprecation.py
MIG=src/mokata/cli_commands/migrate.py
MC=src/mokata/migrate_channels.py
CLI=src/mokata/cli_commands/memory.py
SH=src/mokata/memory/share.py
DR=tests/_deprecation_removal.py

T11='test_stage11_memory_share_channel.py'
T10='test_stage10_removed_backends.py'
T35='test_35b_backup_surface.py'
TS2='test_simp_s2_deprecation.py'

TOTAL=35
ran=0; red=0; green=0; survivors=""

# ---- step 0: the green baseline ---------------------------------------------------------------
printf '================================================================================\n'
printf 'STEP 0 — GREEN BASELINE (no mutation applied). Nothing is graded until this passes.\n'
printf '================================================================================\n'
for pat in "$T11" "$T10" "$T35" "$TS2"; do
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

# ==== A. the removal itself =====================================================================

mutant "A01 ★★ the channel is ANNOUNCED as deprecated again — a notice promising a future
       removal for a thing this release already removed" "$DEP" \
  'DEPRECATED_CHANNELS = tuple(CHANNELS)' \
  'CHANNELS["memory-share"] = CHANNELS["neo4j"]
DEPRECATED_CHANNELS = tuple(CHANNELS)' "$T11"

mutant "A02 ★★ the removal RECORD is dropped — the channel is gone and nothing says so" "$DEP" \
  'REMOVED_CHANNELS = tuple(REMOVED)' \
  'REMOVED.pop("memory-share", None)
REMOVED_CHANNELS = tuple(REMOVED)' "$T11"

mutant "A03 ★★★ THE DETECTOR COMES BACK — the filename is a channel again, under its own name" "$SH" \
  'BACKUPS_DIRNAME = "backups"' \
  'BACKUPS_DIRNAME = "backups"
def is_legacy_share_dest(dest):
    return False' "$T11"

mutant "A04 ★ the deleted constant is re-exported through the package" "src/mokata/memory/__init__.py" \
  '    "load_memory_share",' \
  '    "load_memory_share",
    "MEMORY_SHARE_FILENAME",' "$T11"

# ==== B. the answer at `mokata migrate` =========================================================

mutant "B01 ★★★ THE DEFECT ITSELF — a removed channel falls through to the migrator, so the
       command the 0.0.17 notice told the user to run answers like it never existed" "$MIG" \
  '    if channel in REMOVED_CHANNELS:' \
  '    if False:' "$T11"

mutant "B02 ★★★ argparse REFUSES the word — 'invalid choice', which is what it says about a
       TYPO. §7g on the one surface where the user is doing exactly what we asked" "$MIG" \
  '    p.add_argument("channel", choices=tuple(CHANNELS) + tuple(REMOVED_CHANNELS),' \
  '    p.add_argument("channel", choices=tuple(CHANNELS),' "$T11"

mutant "B03 ★★ the present/absent split COLLAPSES — one sentence for two facts, so a repo with
       no file is invited to run a command that fails" "$DEP" \
  '    detail = (f"Yours is still at {path}." if present
              else f"This repo has none at {path}, so there is nothing here to bring across.")' \
  '    detail = f"Yours is still at {path}."' "$T11"

mutant "B04 ★★ the split collapses the OTHER way — a repo that HAS the file is never told where" \
  "$DEP" \
  '    detail = (f"Yours is still at {path}." if present
              else f"This repo has none at {path}, so there is nothing here to bring across.")' \
  '    detail = f"This repo has none at {path}, so there is nothing here to bring across."' "$T11"

mutant "B05 ★★ the existence probe is INVERTED — every repo is told the opposite of the truth" \
  "$MIG" \
  '        print(deprecation.removed_file_report(channel, path, os.path.exists(path)),' \
  '        print(deprecation.removed_file_report(channel, path, not os.path.exists(path)),' "$T11"

mutant "B06 ★★ the refusal EXITS 0 — a migration that did not happen reports success" "$MIG" \
  '        print(deprecation.removed_notice(channel).render(), file=sys.stderr)
    return 1' \
  '        print(deprecation.removed_notice(channel).render(), file=sys.stderr)
    return 0' "$T11"

mutant "B07 ★★ the answer loses the REMEDY — the user is told what broke and not what to do" "$DEP" \
  '        remedy=("This release still READS that file: `mokata memory import --file <path>` ' \
  '        remedy=("' "$T11"

mutant "B08 ★★★ THE FALSE REFUSAL — the file channel is given a BACKEND record, so a user whose
       file this release reads is told to pip-install an older mokata" "$DEP" \
  '    "memory-share": RemovedFileNotice(
        channel="memory-share", what="memory-share.json channel",' \
  '    "memory-share": RemovedNotice(
        channel="memory-share", what="memory-share.json channel",
        remedy=_one_last_migration("memory-share"),' "$T11"

mutant "B09 ★ the notice stops naming the release it happened in" "$DEP" \
  '        return (f"{glyph}: the {self.what} was REMOVED in mokata {self.removed}. "' \
  '        return (f"{glyph}: the {self.what} was REMOVED. "' "$T11"

# ==== C. the two record classes stay apart ======================================================

mutant "C01 ★★★ removed_channels_in reverts to a MEMBERSHIP test — a manifest chain naming the
       file channel would announce a removed BACKEND about a file we can read" "$DEP" \
  '    return tuple(tool for tool in (chain or ())
                 if isinstance(REMOVED.get(tool), RemovedNotice))' \
  '    return tuple(tool for tool in (chain or ()) if tool in REMOVED)' "$T11"

mutant "C02 ★★ every removed channel becomes a FILE channel — obsidian loses its downgrade" "$DEP" \
  '    return isinstance(REMOVED.get(channel), RemovedFileNotice)' \
  '    return channel in REMOVED' "$T11"

mutant "C03 ★★ no removed channel is a file channel — memory-share gets the downgrade lie" "$DEP" \
  '    return isinstance(REMOVED.get(channel), RemovedFileNotice)' \
  '    return False' "$T11"

mutant "C04 ★★ removed_notice stops REFUSING a file channel — the wrong shape renders happily" \
  "$DEP" \
  '    notice = REMOVED[channel]
    if not isinstance(notice, RemovedNotice):
        raise KeyError("%r is a removed FILE channel, not a removed backend — its answer is "
                       "`removed_file_report`" % (channel,))
    return notice' \
  '    return REMOVED[channel]' "$T11"

mutant "C05 ★★ removed_file_report stops REFUSING a backend — obsidian gets a file answer" "$DEP" \
  '    if not isinstance(notice, RemovedFileNotice):
        raise KeyError("%r is a removed BACKEND, not a removed file channel — its answer is "
                       "`removed_notice`" % (channel,))' \
  '    if False:
        raise KeyError("x")' "$T11"

# ==== D. what SURVIVES ==========================================================================

mutant "D01 ★★★ the backup DEFAULT becomes the removed channel path — the deletion hands every
       user the filename it just took away" "$SH" \
  '    return os.path.join(root, MOKATA_DIR, BACKUPS_DIRNAME, f"memory-{stamp}.json")' \
  '    return os.path.join(root, MOKATA_DIR, "memory-share.json")' "$T11"

mutant "D02 ★★ exporting to that path ANNOUNCES something again — a channel notice with no
       channel behind it" "$CLI" \
  '        dest = args.file or default_backup_path(args.path)' \
  '        dest = args.file or default_backup_path(args.path)
        if dest.endswith("memory-share.json"):
            from .. import deprecation as _d
            _d.warn_removed("memory-share", surface.mokata_dir)' "$T35"

mutant "D03 ★★★ SHARE_KIND is RENAMED — free for our code, and every backup already on a user's
       disk becomes unreadable. §7d's exception, inverted" "$SH" \
  'SHARE_KIND = "mokata-memory-share"' \
  'SHARE_KIND = "mokata-memory-backup"' "$T11"

mutant "D04 ★★ export stops writing the file it says it wrote" "$SH" \
  '    if dest is not None:
        parent = os.path.dirname(dest)' \
  '    if False:
        parent = os.path.dirname(dest)' "$T11"

mutant "D05 ★★ import silently restores NOTHING — the remedy the refusal names is a no-op" "$SH" \
  '    incoming = [MemoryItem.from_dict(d) for d in data["items"]]' \
  '    incoming = []' "$T11"

mutant "D06 ★★ the restore becomes DESTRUCTIVE on the source file it was handed" "$CLI" \
  '            data = load_memory_share(args.file)' \
  '            data = load_memory_share(args.file)
            os.remove(args.file)' "$T11"

# ==== E. the gate ===============================================================================

mutant "E01 ★★★ THE PROBE TARGET GOES BACK TO THE WHOLE MODULE — the only way to satisfy it is
       to delete memory export / import, which is what the row's 660 invited" "$DR" \
  '    "memory-share": "mokata.memory.share:is_legacy_share_dest",' \
  '    "memory-share": "mokata.memory.share",' "$T11"

mutant "E02 ★★ the map DROPS the removed channel — it stops being checked at the exact moment
       the check acquires a subject (§7i, in the file that quotes §7i)" "$DR" \
  '    "memory-share": "mokata.memory.share:is_legacy_share_dest",' \
  '' "$T11"

mutant "E03 ★★★ THE GATE OPENS EARLY — the verdict stops being overdue while two channels are
       still implemented, so the 0.0.18 cut would proceed with the lane unfinished" "$DR" \
  '    return REMOVAL_OVERDUE if now >= due else REMOVAL_PENDING' \
  '    return REMOVAL_PENDING' "$T10"

mutant "E04 ★★ a surviving channel quietly loses its deprecation notice — the lane goes silent
       about something it has NOT removed" "$DEP" \
  '    "neo4j": DeprecationNotice(' \
  '    "_neo4j_disabled": DeprecationNotice(' "$T11"

# ==== F. BACKCOMPAT-SWEEP (E2 — one pass reads one file) ========================================

mutant "F01 ★★ --file comes back — an option a user can pass that nothing can act on" "$MIG" \
  '    p.add_argument("--yes", action="store_true",
                   help="non-interactive (approve the gated migration)")' \
  '    p.add_argument("--file", default="", help="the file to read")
    p.add_argument("--yes", action="store_true",
                   help="non-interactive (approve the gated migration)")' "$T11"

mutant "F02 ★★ the stranded parameter comes back on the library surface" "$MC" \
  'def plan_channel_migration(surface: Any, channel: str) -> ChannelMigratePlan:' \
  'def plan_channel_migration(surface: Any, channel: str, *, file: str = "") -> ChannelMigratePlan:' \
  "$T11"

mutant "F03 ★ migrate reads a MEMORY chain again — a resolver for a caller that no longer exists" \
  "$MC" \
  '    target = "the canonical transport"' \
  '    target = "the canonical transport"
    _ = surface.router.manifest.fallback_order("memory_store")' "$T11"

# ==== G. no hiding ==============================================================================

mutant "G01 ★★★ THE RECORD BECOMES A LEGACY READER — the removal record starts OPENING the file
       it only ever named, which is the one thing slice 1 wrote 'must never happen'" "$DEP" \
  'def removed_share_path(root: str) -> str:' \
  'def _peek(p):
    return open(p, encoding="utf-8").read()


def removed_share_path(root: str) -> str:' "$T11"

mutant "G02 ★★ the once-per-repo marker stops being WRITE-ONLY — the guard that lets an
       os.open through can no longer tell a marker mint from a read" "$DEP" \
  '        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    except FileExistsError:
        return False
    except (OSError, ValueError):
        return False                              # degrade-clean, exactly as `warn_deprecated`
    (out or _stderr)(notice.render(detail=detail, ascii_only=ascii_only))' \
  '        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        os.close(fd)
    except FileExistsError:
        return False
    except (OSError, ValueError):
        return False                              # degrade-clean, exactly as `warn_deprecated`
    (out or _stderr)(notice.render(detail=detail, ascii_only=ascii_only))' "$T11"

mutant "G03 ★★★ A SECOND MODULE REGAINS THE FILENAME — the channel comes back somewhere the
       probe does not look, which is 'hidden, not deleted' (A3)" "$CLI" \
  '        dest = args.file or default_backup_path(args.path)' \
  '        _legacy = "memory-share.json"
        dest = args.file or default_backup_path(args.path)' "$T11"

mutant "G04 ★★ --help ADVERTISES the removed channels — help text selling a channel that is
       gone, while the answer path exists to say it is gone" "$MIG" \
  '                   metavar="{%s}" % ",".join(CHANNELS),' \
  '                   metavar="{%s}" % ",".join(tuple(CHANNELS) + tuple(REMOVED_CHANNELS)),' "$T11"

# ==== verdict ==================================================================================

printf '\n================================================================================\n'
printf 'STAGE-11 MEMORY-SHARE-CHANNEL MUTANTS: %s ran of %s — %s RED, %s GREEN\n' \
    "$ran" "$TOTAL" "$red" "$green"
if [ "$green" -ne 0 ]; then
    printf 'SURVIVORS (each is a pin that does not grade):\n%s' "$survivors"
    printf '================================================================================\n'
    exit 1
fi
printf 'Every mutant was caught.\n'
printf '================================================================================\n'
