#!/usr/bin/env bash
# Drives the stage-28 mutant list through scripts/mutate.sh — the ONLY sanctioned mutator
# (doc 85 §7b). Never hand-edit a file to see whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python tests/_stage28_mutants.sh
#
# Consumes mutate.sh's EXIT CONTRACT: a VERDICT (RED/GREEN) is a completed run; any harness
# failure STOPS THE BATCH DEAD, because on exit 4 the mutator deliberately leaves the target
# mutated. (Driver copied from _stage11_mutants.sh — MUTANT-DRIVER-CONTRACT-DUPLICATED, doc 84.)
#
# ⚠ §7i. After the fix the real tree is CLEAN, so every real-tree sweep is GREEN and grades
# nothing on its own. Three layers fix that:
#
#   A01-A04 ★ THE GUARD-SHAPE DISCRIMINATOR — the mutants this stage exists to hunt. A
#     `setUpClass` skip accepted as a guard is the survivor worth looking for, because that is the
#     shape a future author will reach for and the one that measurably diverges `Ran N`.
#
#   B01-B10 attack the derivation, and B01-B03 are §7g's load-bearing three: each makes an
#     UNDECIDABLE case render as a decided GREEN, which is the one prohibition here (an empty
#     exclude set makes the property VACUOUSLY true and byte-identical to "every read is guarded").
#
#   C01-C02 BREAK THE REAL TREE — strip the guard this stage just added back off test_s11 and
#     test_s12. These are what grade the SWEEP against the corpus rather than against fixtures:
#     under them the tree is exactly as it was when the cut aborted.
#
#   P01-P04 grade the stage-27 scope fix (`preflight_functions`) and the release.sh install that
#     depended on it. P04 mutates release.sh itself.
#
# ⚠⚠ THIS BATCH'S FIRST 27/27 WAS PARTLY A FALSE GREEN, AND THE MUTATOR CAUSED IT (stage 29 rider).
#
# `scripts/mutate.sh` creates `.mutate.lock` in the repo root for the duration of every run, and
# `.mutate.lock` is a literal `sync-public.sh --exclude` entry. Under the ORIGINAL disk-probe
# derivation (`os.path.exists`) it therefore entered the deriving set *while and only while this
# batch ran*, and `test_mutation_harness._InterlockFixture` reads it from `setUp` — an unguarded
# boundary read. So `test_no_shipped_test_reads_an_internal_file_without_a_guard` was RED for
# EVERY mutant here, and `FAILED (failures=1)` meant "the lock is present", not "the mutant died".
#
# MEASURED: B02, W3, W6 and W7 each report `Ran 44 ... failures=1` under this batch and each
# SURVIVES with no failure at all when the lock is absent; B10's RED was real but rested on
# developer-local exclude entries (`.venv`, `build`, `docs/marketing`) that no clone carries.
# Five arms, none of them graded. **The instrument was contaminating the corpus it was grading.**
#
# It cannot recur: the deriving set is now the GIT INDEX, and `.mutate.lock` is untracked, so no
# mutation run can put it back. The five arms are pinned by fixtures in
# `TestTheArmsTheContaminatedScoreHid`, which depend on no tree at all.
#
# ⚠ NOT MUTATED: the `absent ONLY because of the mirror boundary` companions. They assert a
# coupling that only has content when one of the two internal files is MISSING, and on a healthy
# private tree both are present, so every mutation of them survives by construction. That is a
# real §7i limit of this batch, stated rather than papered over with a mutant that grades nothing.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"
B=tests/_shipped_reads.py
P=tests/_preflight_parity.py
R=scripts/release.sh
S11=tests/test_s11_bookkeeping_derived.py
S12=tests/test_s12_release_publisher.py
T='test_s28_shipped_reads_guarded.py'
TP='test_s27_preflight_parity.py'

TOTAL=27
ran=0; red=0; green=0; survivors=""

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

# ==== A. ★ THE GUARD-SHAPE DISCRIMINATOR ======================================================

mutant "A01 a setUpClass skip is ACCEPTED as a guard (the survivor this stage went hunting)" "$B" \
  'ACCEPTED_GUARDS = frozenset([GUARD_DECORATOR])' \
  'ACCEPTED_GUARDS = frozenset([GUARD_DECORATOR, GUARD_SETUPCLASS_SKIP])' "$T"

mutant "A02 guard_of REPORTS a setUpClass skip as the decorator shape" "$B" \
  '                return GUARD_SETUPCLASS_SKIP
    return GUARD_NONE' \
  '                return GUARD_DECORATOR
    return GUARD_NONE' "$T"

mutant "A03 an OR of probes counts as a guard (runs when only one file is present)" "$B" \
  '    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.And):' \
  '    if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):' "$T"

mutant "A04 ANY skipUnless counts, not one that tests EXISTENCE" "$B" \
  '        if _is_existence_condition(dec.args[0]):' \
  '        if True:' "$T"

# ==== B. the derivation, and §7g's one prohibition =============================================

mutant "B01 ★ an empty exclude set derives GREEN — vacuity reads as a clean corpus" "$B" \
  '    BASIS_NO_EXCLUDES: UNDECIDABLE,' \
  '    BASIS_NO_EXCLUDES: GREEN,' "$T"

mutant "B02 ★ an unparseable source derives GREEN instead of UNDECIDABLE" "$B" \
  '    BASIS_SOURCE_UNPARSEABLE: UNDECIDABLE,' \
  '    BASIS_SOURCE_UNPARSEABLE: GREEN,' "$T"

mutant "B03 ★ an unreadable source derives GREEN instead of UNDECIDABLE" "$B" \
  '    BASIS_SOURCE_UNREADABLE: UNDECIDABLE,' \
  '    BASIS_SOURCE_UNREADABLE: GREEN,' "$T"

mutant "B04 an unguarded read stops being a defect" "$B" \
  '    BASIS_UNGUARDED_READS: RED,' \
  '    BASIS_UNGUARDED_READS: GREEN,' "$T"

mutant "B05 an UNDECIDABLE may be constructed with no reason (a shrug reads as green)" "$B" \
  '        if basis in UNDECIDABLE_BASES and not detail:' \
  '        if False:' "$T"

mutant "B06 an existence PROBE counts as a READ (the companions condemn themselves)" "$B" \
  '        if id(child) in probed:
            continue' \
  '        if False:
            continue' "$T"

mutant "B07 rsync GLOBS join the deciding set instead of being dropped" "$B" \
  '        if e.strip("/") and not any(c in e for c in _GLOB_CHARS))' \
  '        if e.strip("/"))' "$T"

mutant "B08 the exclude match becomes a SUBSTRING test (docs/buildings matches docs/build)" "$B" \
  '        if want == entry or want.startswith(entry + "/"):' \
  '        if entry in want:' "$T"

mutant "B09 a join under ANY head counts (a fixture tree becomes an offender)" "$B" \
  '    if tail is None or tail[0] not in ctx.roots:' \
  '    if tail is None:' "$T"

mutant "B10 an accessor call counts without a ROOT argument (a tmpdir becomes the repo)" "$B" \
  '        if isinstance(arg, ast.Name) and arg.id in ctx.roots:' \
  '        if isinstance(arg, ast.Name):' "$T"

# ==== C. the REAL tree loses the guard this stage added ========================================

mutant "C01 ★ test_s11 loses its guard — the corpus is back to the tree that aborted the cut" "$S11" \
  '@unittest.skipUnless(os.path.exists(SYNC_SH),
                     "sync-public.sh is dev-only, excluded from the public mirror")
class TestAgainstTheRealControls' \
  'class TestAgainstTheRealControls' "$T"

mutant "C02 ★ test_s12 loses its guard — same, for the eight release.sh assertions" "$S12" \
  '@unittest.skipUnless(os.path.exists(RELEASE_SH),
                     "release.sh is dev-only, excluded from the public mirror")
class TestExactlyOnePublisher' \
  'class TestExactlyOnePublisher' "$T"

# ==== P. the stage-27 scope fix, and the install that needed it ================================

mutant "P01 ★ only the FIRST suite-running function is graded (stage 27's actual defect)" "$P" \
  '        if _RUNS_SUITE in chunk:
            found.append(m.group(1))
    return tuple(found)' \
  '        if _RUNS_SUITE in chunk:
            return (m.group(1),)
    return tuple(found)' "$TP"

mutant "P02 ★ 'release.sh runs the suite nowhere' derives GREEN instead of UNDECIDABLE" "$P" \
  '    BASIS_NO_PREFLIGHT_FUNCTIONS: UNDECIDABLE,' \
  '    BASIS_NO_PREFLIGHT_FUNCTIONS: GREEN,' "$TP"

mutant "P03 the scope is read WITHOUT stripping comments (a commented preflight counts)" "$P" \
  '    body = _code_only(release_text)
    found = []' \
  '    body = release_text
    found = []' "$TP"

mutant "P04 ★ release.sh drops ci.txt from run_public_subset_preflight (the 37-failure defect)" "$R" \
  '  "$py" -m pip install -q --require-hashes -r "$sub/requirements/ci.txt" >/dev/null' \
  '  true  # ci.txt install removed' "$TP"

# ==== W. §7f — the SECOND wave. ===============================================================
# The first 20 came back 20/20, which is where a batch stops being evidence and starts being a
# number. So the question was asked the other way round: which lines of _shipped_reads.py did
# NOTHING above touch? These seven are that list — the presence filter, the corpus scope, the
# root-name rule, the definition/read split, the cross-module hop, the setUp arm of guard_of, and
# the accessor's parameter head. They were written AFTER the 100%, not before it.

# ⚠ W1 was `present_excludes stops filtering on presence`. The stage-29 rider replaced that
# function outright: presence on DISK was the defect (an untracked, gitignored `docs/marketing/`
# put the floor's number on one laptop). The mutant keeps its job — the filter stops filtering —
# against the reader that replaced it. The DISK-probe regression itself is graded in
# `_stage29_mutants.sh` R01, where it belongs with the fix.
mutant "W1 tracked_excludes stops filtering on trackedness (artifacts re-enter the deciding set)" "$B" \
  '    return frozenset(e for e in literal_excludes(excludes) if e in tracked)' \
  '    return literal_excludes(excludes)' "$T"

mutant "W2 SHIPPED_TEST_DIRS drops tests/integration from the corpus" "$B" \
  'SHIPPED_TEST_DIRS = ("tests", "tests/integration")' \
  'SHIPPED_TEST_DIRS = ("tests",)' "$T"

mutant "W3 _root_names accepts ANY module-level assignment, not one derived from __file__" "$B" \
  '        if not any(isinstance(n, ast.Name) and n.id == "__file__" for n in ast.walk(stmt.value)):
            continue' \
  '        if False:
            continue' "$T"

mutant "W4 a module-level path DEFINITION is counted as an import-time read" "$B" \
  '            if isinstance(stmt, (ast.Assign, ast.AnnAssign)) and value is not None \
                    and _anchored_path(value, ctx):
                continue' \
  '            if False:
                continue' "$T"

mutant "W5 resolve_all stops carrying cross-module taint (the accessor hop dies)" "$B" \
  '        resolve(f, s, excludes, module_taints, module_accessors)' \
  '        resolve(f, s, excludes, {}, {})' "$T"

mutant "W6 guard_of stops looking at setUp (only setUpClass counts as the wrong shape)" "$B" \
  '        if stmt.name not in ("setUpClass", "setUp"):' \
  '        if stmt.name not in ("setUpClass",):' "$T"

mutant "W7 an accessor is recognised on ANY join, not one headed by a PARAMETER" "$B" \
  '            if tail and tail[0] in params and excluded_match(tail[1], literals):' \
  '            if tail and excluded_match(tail[1], literals):' "$T"

# ==== verdict =================================================================================

printf '\n================================================================================\n'
printf 'STAGE 28 MUTANTS: %s ran of %s — %s RED, %s GREEN\n' "$ran" "$TOTAL" "$red" "$green"
if [ "$green" -ne 0 ]; then
    printf 'SURVIVORS (each is a pin that does not grade):\n%s' "$survivors"
    printf '================================================================================\n'
    exit 1
fi
printf 'Every mutant was caught.\n'
printf '================================================================================\n'
