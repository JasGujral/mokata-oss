#!/usr/bin/env bash
# Drives the 0.0.18 stage-13 (lane D slice 4, `vault`) mutant list through scripts/mutate.sh —
# the ONLY sanctioned mutator (doc 85 §7b). Never hand-edit a file to see whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python tests/_stage13_vault_slice_mutants.sh
#
# ⚠ THE NAME CARRIES ITS SUBJECT, NOT JUST ITS STAGE NUMBER. `tests/_stage13_mutants.sh` would
# collide with 0.0.17's stage 13 — stage numbers repeat across releases in this repo. Stages 10,
# 11 and 12 hit this first and answered it the same way.
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
# ⭐ WHAT THIS SLICE IS. The row's two biggest named files are SURVIVORS: `vault.py` (425) is the
# design-artifact feature the gate's own map mistook for the channel, and `session_transport.py`
# (328) keeps three of its four transports. So the groups are weighted the way the slice is —
# most of them grade what stayed, or grade the honesty of what the user is told about what left.
#   A. the channel is gone      the transport kind, its factory arm, the migrator module
#   B. the FEATURE survives     the mistake the map invited — deleting a supported command
#   C. the user's data          artifacts untouched · bundles announced, not silently unlisted
#   D. the removal answer       a removed kind/channel gets a record, never a typo answer
#   E. the gate MOVES           open for vault, closed for neo4j, and the move is the deletion's
#   F. E7                       filed= keys the marker; frozen `removed=` on the record
#   G. BACKCOMPAT-SWEEP         the derived choices, both halves

set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"
PY="${PYTHON:-python3}"

DEP=src/mokata/deprecation.py
STX=src/mokata/session_transport.py
MIG=src/mokata/cli_commands/migrate.py
COL=src/mokata/cli_commands/collab.py
TR=src/mokata/mcp/tools_read.py
VLT=src/mokata/vault.py
DR=tests/_deprecation_removal.py

T13='test_stage13_vault_slice.py'
T55='test_stage55b_session_transport.py'
T9='test_stage9_removal_release.py'

TOTAL=28
ran=0; red=0; green=0; survivors=""

# ---- step 0: the green baseline ---------------------------------------------------------------
printf '================================================================================\n'
printf 'STEP 0 — GREEN BASELINE (no mutation applied). Nothing is graded until this passes.\n'
printf '================================================================================\n'
for pat in "$T13" "$T55" "$T9"; do
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

# ==== A. the channel is GONE ====================================================================

mutant "A01 ***  THE DELETION IS REVERTED — the transport kind comes back in TRANSPORT_KINDS, so
       the factory offers a channel this release removed" "$STX" \
  'TRANSPORT_KINDS = ("local", "postgres")' \
  'TRANSPORT_KINDS = ("local", "vault", "postgres")' "$T13"

mutant "A02 ***  the removed kind is HIDDEN rather than deleted — the refusal is skipped and the
       kind silently falls through to the unknown-transport degrade" "$STX" \
  '    _refuse_removed_kind(kind, root)' \
  '    pass    # no removal refusal' "$T13"

mutant "A03 ***  the removed-kind guard stops reading the registry and hardcodes today's names —
       GREEN for every channel removed so far, wrong for every one removed after this stage" \
  "$STX" \
  '    if kind not in deprecation.REMOVED:' \
  '    if kind not in ("obsidian", "native-memory", "memory-share"):' "$T13"

# ==== B. the FEATURE survives — the mistake the gate's own map invited ==========================

mutant "B01 ***  THE WHOLE TRAP IN ONE LINE — the gate's target is pointed back at the module, so
       the only way to satisfy it is to delete the design-artifact vault" "$DR" \
  '    "vault": "mokata.session_transport:VaultTransport",' \
  '    "vault": "mokata.vault",' "$T13"

mutant "B02 ***  the artifact vault's integrity refusal starts selling the migration again — a
       remedy that moves session bundles, shown to a user holding a corrupt SPEC" "$VLT" \
  '                f"serve it. Nothing was written and the stored file is untouched at "' \
  '                f"serve it. The vault is deprecated; migrate vault. Untouched at "' "$T13"

mutant "B03 ***  the artifact vault stops verifying its content hash — `team join --vault` then
       accepts a tampered artifact from an untrusted teammate's repo" "$VLT" \
  '        if actual != entry.content_hash:' \
  '        if False:' "$T13"

mutant "B04 **   the surviving base class loses its read — every FILE transport round trip breaks,
       which is the survivor half the deletion is graded by" "$STX" \
  '        with open(path, encoding="utf-8") as fh:
            return fh.read()' \
  '        return None' "$T13"

# ==== C. the user's data — BOTH kinds, and neither is destroyed =================================

mutant "C01 ***  THE DEFECT THIS SLICE WOULD HAVE SHIPPED — `session list` drops the vault leg and
       says nothing, so a user whose bundles are all in that store reads 'no shared bundles'" \
  "$COL" \
  '        _announce_removed_bundles(root)' \
  '        pass    # no announcement' "$T13"

mutant "C02 ***  the announcement is made CONDITIONAL on an empty listing — the mixed case (one
       local bundle, nine stranded) is non-empty and still short, and goes silent" "$COL" \
  '        infos = SB.list_session_bundles_across(root, transports)
        _announce_removed_bundles(root)' \
  '        infos = SB.list_session_bundles_across(root, transports)
        if not infos:
            _announce_removed_bundles(root)' "$T13"

mutant "C03 ***  the stranded-bundle probe reports nothing — the announcement never fires and the
       listing goes back to being cheerfully short" "$STX" \
  '        return sorted(fn[:-len(".json")] for fn in os.listdir(directory)
                      if fn.endswith(".json"))' \
  '        return []' "$T13"

mutant "C04 ***  the probe becomes DESTRUCTIVE — it removes what it finds. P2: mokata does not
       delete what a human owns, and a read-only probe is where that would hide" "$STX" \
  '    try:
        return sorted(fn[:-len(".json")] for fn in os.listdir(directory)' \
  '    try:
        import shutil as _sh; _sh.rmtree(directory, ignore_errors=True)
        return sorted(fn[:-len(".json")] for fn in os.listdir(directory)' "$T13"

mutant "C05 ***  the removed kind DEGRADES instead of refusing — the push silently lands in the
       LOCAL store, i.e. a different store than the caller named" "$STX" \
  '    _refuse_removed_kind(kind, root)
    if kind == "postgres":' \
  '    if kind in ("vault",):
        return LocalTransport(root)
    if kind == "postgres":' "$T13"

mutant "C06 **   the refusal stops naming WHERE the data is — the remedy becomes unrunnable
       because the user cannot find what they are being told to move" "$DEP" \
  '    detail = (f"Yours is still at {path}." if present' \
  '    detail = ("Yours is still there." if present' "$T13"

mutant "C07 ***  the artifact user is handed the SESSION channel's removal notice — nothing of
       theirs was removed, and this is the false refusal in the other direction" "$COL" \
  '    stranded = STX.removed_bundle_tags("vault", root)
    if not stranded:
        return' \
  '    stranded = STX.removed_bundle_tags("vault", root)
    if False:
        return' "$T13"

# ==== D. the removal ANSWER — never a typo answer ==============================================

mutant "D01 ***  `mokata migrate` stops accepting the removed channels — every one of them answers
       argparse's 'invalid choice', on the command a year of notices told users to run" "$MIG" \
  '    p.add_argument("channel", choices=tuple(REMOVED_CHANNELS),' \
  '    p.add_argument("channel", choices=(),' "$T13"

mutant "D02 ***  the help SELLS a schedule again — 'scheduled for removal in X' in the release
       that removed the last channel (MIGRATE-HELP-SELLS-REMOVED-CHANNELS)" "$MIG" \
  '        help=("what happened to a channel this release removed (it no longer migrates anything — ' \
  '        help=(f"one-time gated migration of a deprecated channel, scheduled for removal in "
              f"{deprecation.REMOVAL_RELEASE} (' "$T13"

mutant "D03 ***  the metavar advertises the LIVE registry instead of what the command ACCEPTS —
       it renders {neo4j}, a channel argparse then refuses. ⚠ NOT the empty-brace form the row
       named: that was one symptom, and the first draft of this slice only pinned the symptom" \
  "$MIG" \
  '                   metavar="{%s}" % ",".join(REMOVED_CHANNELS),' \
  '                   metavar="{%s}" % ",".join(deprecation.CHANNELS),' "$T13"

mutant "D04 ***  THE SECOND SURFACE, AND THE FIRST DRAFT OF THIS SLICE GOT IT WRONG — `--to/--from`
       derive from the LIVE registry only, so `--to vault` answers 'invalid choice'" "$COL" \
  'STX_KINDS = tuple(_LIVE_KINDS) + tuple(REMOVED_CHANNELS)' \
  'STX_KINDS = tuple(_LIVE_KINDS)' "$T13"

mutant "D05 **   …and the other half of the same property: the metavar starts ADVERTISING the
       removed kinds, so the help sells a transport that no longer exists" "$COL" \
  'STX_METAVAR = "{%s}" % ",".join(_LIVE_KINDS)' \
  'STX_METAVAR = "{%s}" % ",".join(tuple(_LIVE_KINDS) + tuple(REMOVED_CHANNELS))' "$T13"

mutant "D06 ***  the file channel's location is hardcoded to the FIRST file channel again — the
       vault user is told their session bundles are in a memory backup file (the stage-12 row)" \
  "$DEP" \
  '    return os.path.join(root, MOKATA_DIR, notice.path)' \
  '    return os.path.join(root, MOKATA_DIR, "memory-share.json")' "$T13"

mutant "D07 ***  the record class stops being chosen by TYPE — a file channel gets the backend
       refusal, charging a downgrade for bundles this release reads (slice 2's false refusal)" \
  "$DEP" \
  '    return isinstance(REMOVED.get(channel), RemovedFileNotice)' \
  '    return False' "$T13"

mutant "D08 **   a removed transport kind is reported as UNAVAILABLE on MCP — 'retry with a DSN'
       for a transport that will never come back (§7g)" "$TR" \
  '        except RemovedChannelError as exc:' \
  '        except (RemovedChannelError,) if False else () as exc:' "$T13"

# ==== E. the gate MOVES, and the move is the DELETION's ========================================

mutant "E01 ***  neo4j leaves CHANNELS one stage early — the gate would read LANDED with a channel
       still implemented, i.e. the lane reports finished while stage 14 has not run" "$DEP" \
  '    "neo4j": DeprecationNotice(' \
  '    "_neo4j_disabled": DeprecationNotice(' "$T13"

mutant "E02 ***  the removed channel is dropped from IMPLEMENTATIONS instead of being required to
       be ABSENT — the probe stops checking at the moment the check acquires a subject" "$DR" \
  '    "vault": "mokata.session_transport:VaultTransport",' \
  '    "_vault_unmapped": "mokata.session_transport:VaultTransport",' "$T13"

# ==== F. E7 — the ruling ========================================================================

mutant "F01 ***  THE MARKER GOES BACK TO THE RELEASE — a slipping `at=` then re-fires every repo
       to re-announce removals that did not change (E7's whole subject)" "$DEP" \
  '    marker = _marker_path(mokata_dir, "%s@removed-%s" % (channel, REMOVAL_FILED))' \
  '    marker = _marker_path(mokata_dir, "%s@removed-%s" % (channel, notice.removed))' "$T13"

mutant "F02 ***  the frozen release is defaulted off the live constant again — history starts
       tracking a mutable promise, and a moved `at=` re-dates removals that already happened" \
  "$DEP" \
  '        remedy=_one_last_migration("obsidian"), removed="0.0.18"),' \
  '        remedy=_one_last_migration("obsidian"), removed=REMOVAL_RELEASE),' "$T13"

mutant "F03 **   an unreadable `filed=` silently defaults instead of raising — which SILENCES a
       removal notice rather than printing a wrong one, the quieter half of the same defect" \
  "$DEP" \
  '    if not _FILED.match(filed):
        raise ValueError(' \
  '    if not _FILED.match(filed):
        return "1970-01-01"
    if False:
        raise ValueError(' "$T13"

mutant "F04 **   the ledger record drops the decision stamp — an audit cannot tell a
       re-announcement from the original notice" "$DEP" \
  '                          filed=REMOVAL_FILED, scope="repo")' \
  '                          scope="repo")' "$T13"

# ==== verdict ==================================================================================

printf '\n================================================================================\n'
printf 'STAGE-13 VAULT-SLICE MUTANTS: %s ran of %s — %s RED, %s GREEN\n' \
    "$ran" "$TOTAL" "$red" "$green"
if [ "$green" -ne 0 ]; then
    printf 'SURVIVORS (each is a pin that does not grade):\n%s' "$survivors"
    printf '================================================================================\n'
    exit 1
fi
printf 'Every mutant was caught.\n'
printf '================================================================================\n'
