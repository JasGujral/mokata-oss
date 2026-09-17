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

TOTAL=31
ran=0; red=0; green=0; survivors=""; equiv=0; unexpected=""

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

# The verdict INVERTED — the shape 0.0.17 stage 19c introduced. A declared-equivalent mutant is one
# no test CAN kill, so GREEN is correct and a RED means the reasoning has stopped being true.
equivalent() {
    local label="$1" rc=0 out
    ran=$((ran + 1))
    shift
    out="$("$M" "$label" "$@")" || rc=$?
    if [ -n "$out" ]; then printf '%s\n' "$out"; fi
    if [ "$rc" -ne 0 ]; then
        printf '\nBATCH ABORTED — mutate.sh exited %s on mutant %s of %s\n' "$rc" "$ran" "$TOTAL"
        printf '  mutant: %s\n' "$label"
        exit "$rc"
    fi
    case "$out" in
        GREEN*) equiv=$((equiv + 1)); printf '        ^ EQUIVALENT — surviving is the expected, correct result.\n' ;;
        RED*)   unexpected="$unexpected  - $label"$'\n' ;;
        *)      printf '\nBATCH ABORTED — exit 0 with no verdict on mutant %s of %s\n' "$ran" "$TOTAL"
                exit 70 ;;
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

# ⚠ RE-AIMED at 0.0.20 stage 09. The intent is unchanged; the quotation is not. `cmd_migrate` was
# rewritten when the last live channel left — the `if channel in REMOVED_CHANNELS:` dispatch is
# gone because the command now answers unconditionally and always exits 1 — so this mutant's
# `old` had stopped occurring and the batch was aborting here.
# ⚠ DECLARED EQUIVALENT at 0.0.20 stage 09, ON EVIDENCE, not on reasoning alone: re-aimed and then
# REPLAYED through the real mutator, where it SURVIVED under this batch's test pattern. It is
# genuinely equivalent here — hardcoding the three names produces exactly today's registry, so no
# behavioural test driven by `$T11` can tell the two spellings apart. It is NOT equivalent in
# general, and it is not left ungraded: `_stage12_migrate_slice_mutants.sh` C01 applies the same
# mutation under a pattern whose tests read the DERIVATION rather than the behaviour, and killed it
# in the same replay. Two spellings, two vantages, one of which can see the difference (§7j).
equivalent "B01 [EQUIVALENT] ★★★ THE DEFECT ITSELF — a removed channel falls through to the migrator, so the
       command the 0.0.17 notice told the user to run answers like it never existed" "$MIG" \
  '    p.add_argument("channel", choices=_choices,' \
  '    p.add_argument("channel", choices=("obsidian", "native-memory", "memory-share"),' "$T11"

# ⚠ RE-AIMED at 0.0.20 stage 09. The intent is unchanged; the quotation is not. `cmd_migrate` was
# rewritten when the last live channel left — the `if channel in REMOVED_CHANNELS:` dispatch is
# gone because the command now answers unconditionally and always exits 1 — so this mutant's
# `old` had stopped occurring and the batch was aborting here.
# ⚠ RE-AIMED at 0.0.20 stage 11. Intent unchanged; the quotation follows the refactor that gave this
# surface and `collab.py` ONE shared helper (`deprecation.answerable_choices`) instead of two
# hand-written copies of the same rule. ⭐ The sweep found this pattern the moment the refactor
# landed, which is the whole reason the sweep exists.
mutant "B02 ★★★ argparse REFUSES the word — 'invalid choice', which is what it says about a
       TYPO. §7g on the one surface where the user is doing exactly what we asked" "$MIG" \
  '    p.add_argument("channel", choices=_choices,' \
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

# ⚠ RE-AIMED at 0.0.20 stage 09. The intent is unchanged; the quotation is not. `cmd_migrate` was
# rewritten when the last live channel left — the `if channel in REMOVED_CHANNELS:` dispatch is
# gone because the command now answers unconditionally and always exits 1 — so this mutant's
# `old` had stopped occurring and the batch was aborting here.
mutant "B05 ★★ the existence probe is INVERTED — every repo is told the opposite of the truth" "$MIG" \
  '    return os.path.exists(path)' \
  '    return not os.path.exists(path)' "$T11"

# ⚠ RE-AIMED at 0.0.20 stage 09. The intent is unchanged; the quotation is not. `cmd_migrate` was
# rewritten when the last live channel left — the `if channel in REMOVED_CHANNELS:` dispatch is
# gone because the command now answers unconditionally and always exits 1 — so this mutant's
# `old` had stopped occurring and the batch was aborting here.
mutant "B06 ★★ the refusal EXITS 0 — a migration that did not happen reports success" "$MIG" \
  '    print(deprecation.removal_answer(channel, surface.root, present), file=sys.stderr)
    return 1' \
  '    print(deprecation.removal_answer(channel, surface.root, present), file=sys.stderr)
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

# ⚠ RE-AIMED at 0.0.20 stage 09. Intent unchanged, quotation re-read from the file as it is now.
mutant "C01 ★★★ removed_channels_in reverts to a MEMBERSHIP test — a manifest chain naming the
       file channel would announce a removed BACKEND about a file we can read" "$DEP" \
  '    return tuple(tool for tool in (chain or ()) if isinstance(REMOVED.get(tool), kind))' \
  '    return tuple(tool for tool in (chain or ()) if tool in REMOVED)' "$T11"

mutant "C02 ★★ every removed channel becomes a FILE channel — obsidian loses its downgrade" "$DEP" \
  '    return isinstance(REMOVED.get(channel), RemovedFileNotice)' \
  '    return channel in REMOVED' "$T11"

mutant "C03 ★★ no removed channel is a file channel — memory-share gets the downgrade lie" "$DEP" \
  '    return isinstance(REMOVED.get(channel), RemovedFileNotice)' \
  '    return False' "$T11"

# ⚠ RE-AIMED at 0.0.20 stage 09. Intent unchanged, quotation re-read from the file as it is now.
mutant "C04 ★★ removed_notice stops REFUSING a file channel — the wrong shape renders happily" "$DEP" \
  '    notice = REMOVED[channel]
    if not isinstance(notice, RemovedNotice):
        raise KeyError("%r is a removed %s channel, not a removed backend — its answer is "
                       "`removal_answer`" % (channel, type(notice).__name__))
    return notice' \
  '    return REMOVED[channel]' "$T11"

# ⚠ RE-AIMED at 0.0.20 stage 09. Intent unchanged, quotation re-read from the file as it is now.
mutant "C05 ★★ removed_file_report stops REFUSING a backend — obsidian gets a file answer" "$DEP" \
  '    if not isinstance(notice, RemovedFileNotice):
        raise KeyError("%r is a removed %s channel, not a removed file channel — its answer is "
                       "`removal_answer`" % (channel, type(notice).__name__))' \
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

# ⛔ RETIRED — E04 ★★ a surviving channel quietly loses its deprecation notice — the lane goes silent about something it has NOT removed
#    RETIRED at 0.0.20 stage 09 — §7h, THE PIN ENCODED A FALSE PREMISE.
#    It graded "neo4j must NOT leave CHANNELS yet", i.e. that the channel was still deprecated-but-live
#    and its early removal would make the lane gate read LANDED before stage 14 had run. Stage 14 DID
#    run: neo4j left CHANNELS at 0.0.18 and now sits in REMOVED as a RemovedDerivedNotice. The premise
#    this mutant asserts about the tree is no longer true, so re-quoting it would have carried a false
#    premise across the refactor — the failure §7h names. What replaced the property is graded by TYPE,
#    not by membership: removed_channels_in filters on the record CLASS (_stage11 C01) and
#    _refuse_removed_kind on is_removed_file_channel (_stage13 A03).

# ==== F. BACKCOMPAT-SWEEP (E2 — one pass reads one file) ========================================

# ⚠ RE-AIMED at 0.0.20 stage 09. The intent is unchanged; the quotation is not. `cmd_migrate` was
# rewritten when the last live channel left — the `if channel in REMOVED_CHANNELS:` dispatch is
# gone because the command now answers unconditionally and always exits 1 — so this mutant's
# `old` had stopped occurring and the batch was aborting here.
# ⚠ RE-AIMED at 0.0.20 stage 11. Intent unchanged; the quotation follows the refactor that gave this
# surface and `collab.py` ONE shared helper (`deprecation.answerable_choices`) instead of two
# hand-written copies of the same rule. ⭐ The sweep found this pattern the moment the refactor
# landed, which is the whole reason the sweep exists.
mutant "F01 ★★ --file comes back — an option a user can pass that nothing can act on" "$MIG" \
  '    p.add_argument("channel", choices=_choices,' \
  '    p.add_argument("--file", default="", help="the file to read")
    p.add_argument("channel", choices=_choices,' "$T11"

# ⛔ RETIRED — F02 ★★ the stranded parameter comes back on the library surface
#    RETIRED at 0.0.20 stage 09. Its target, src/mokata/migrate_channels.py, was DELETED at
#    0.0.18 lane D slice 4 — the vault migrator was not a framework with channels plugged into it,
#    it WAS the vault migrator, and it left with the channel. So mutate.sh exited 3 here and ABORTED
#    THE BATCH, which means every mutant listed after it has been ungraded ever since, while this
#    batch's score stayed in the record. Nothing is repairable: the behaviour, not the spelling,
#    is gone.

# ⛔ RETIRED — F03 ★ migrate reads a MEMORY chain again — a resolver for a caller that no longer exists
#    RETIRED at 0.0.20 stage 09. Its target, src/mokata/migrate_channels.py, was DELETED at
#    0.0.18 lane D slice 4 — the vault migrator was not a framework with channels plugged into it,
#    it WAS the vault migrator, and it left with the channel. So mutate.sh exited 3 here and ABORTED
#    THE BATCH, which means every mutant listed after it has been ungraded ever since, while this
#    batch's score stayed in the record. Nothing is repairable: the behaviour, not the spelling,
#    is gone.

# ==== G. no hiding ==============================================================================

# ⚠ RE-AIMED at 0.0.20 stage 09. Intent unchanged, quotation re-read from the file as it is now.
mutant "G01 ★★★ THE RECORD BECOMES A LEGACY READER — the removal record starts OPENING the file
       it only ever named, which is the one thing slice 1 wrote 'must never happen'" "$DEP" \
  'def removed_file_path(channel: str, root: str) -> str:' \
  'def _peek(p):
    return open(p, encoding="utf-8").read()


def removed_file_path(channel: str, root: str) -> str:' "$T11"

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

# ⛔ RETIRED — G04 ★★ --help ADVERTISES the removed channels — help text selling a channel that is gone, while the answer path exists to say it is gone
#    RETIRED at 0.0.20 stage 09 — §7h, THE PIN ENCODED A FALSE PREMISE.
#    It graded "--help must not advertise the removed channels". That was right while a LIVE set
#    existed and the two lists differed. With the last live channel gone, `register()` derives BOTH
#    `choices` and `metavar` from REMOVED_CHANNELS on purpose (`migrate.py:88-94`): the command exists
#    precisely to answer for removed channels, so advertising them is now the CORRECT behaviour and
#    this mutant asked for the defect. Re-quoting it would have carried the false premise across the
#    refactor, which is the failure mode §7h names. The live property it leaves behind — metavar must
#    advertise exactly what choices accepts — is graded by G04a in _stage12_migrate_slice_mutants.sh.

# ==== verdict ==================================================================================

printf '\n================================================================================\n'
printf 'STAGE-11 MEMORY-SHARE-CHANNEL MUTANTS: %s ran of %s — %s RED, %s GREEN\n' \
    "$ran" "$TOTAL" "$red" "$green"
if [ -n "$unexpected" ]; then
    printf 'DECLARED-EQUIVALENT mutants that were CAUGHT — the reasoning is stale:\n%s' "$unexpected"
    exit 1
fi
if [ "$green" -ne 0 ]; then
    printf 'SURVIVORS (each is a pin that does not grade):\n%s' "$survivors"
    printf '================================================================================\n'
    exit 1
fi
printf 'Every mutant was caught.\n'
printf '================================================================================\n'
