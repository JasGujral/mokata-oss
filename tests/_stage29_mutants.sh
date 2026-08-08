#!/usr/bin/env bash
# Drives the stage-29 mutant list through scripts/mutate.sh — the ONLY sanctioned mutator
# (doc 85 §7b). Never hand-edit a file to see whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python tests/_stage29_mutants.sh
#
# Consumes mutate.sh's EXIT CONTRACT: a VERDICT (RED/GREEN) is a completed run; any harness
# failure STOPS THE BATCH DEAD, because on exit 4 the mutator deliberately leaves the target
# mutated. (Driver copied from _stage28_mutants.sh — MUTANT-DRIVER-CONTRACT-DUPLICATED, doc 84.)
#
# ⚠⚠ THE LIMIT THIS BATCH CANNOT ESCAPE, STATED BEFORE THE SCORE (§7i).
#
# Stage 29 fixed THREE Windows-only failures. Two of them — the cross-mount `relpath` and the
# tempdir file lock — are invisible to a POSIX mutation run BY CONSTRUCTION, not by omission:
#
#   * `posixpath.relpath` NEVER raises across mounts. It answers `../../..` and carries on. The
#     `ValueError` only exists in `ntpath.relpath`, which compares drive letters.
#   * POSIX unlinks a file that is still open. Only Windows refuses.
#
# So reverting either fix here comes back GREEN on this machine, and a GREEN would mean "this
# platform cannot see it", NOT "the pin is weak". Those two must not be graded by a number that
# cannot move. They are graded INSTEAD by direct measurement, recorded in the stage report:
#
#   * the `relpath` fix run through `ntpath` on the REAL CI paths (`D:\a\mokata-oss\...` vs
#     `C:\Users\RUNNER~1\...`) — old form raises the exact CI `ValueError`, new form does not,
#     and both spell the real sweep's keys `tests/<file>.py` identically;
#   * the handle count into the tempdir at rmtree with NO `gc.collect()` — 1 on CPython 3.12
#     before, 0 after, 0 on 3.10 either way.
#
# The mutants below therefore grade what IS gradeable here: that the round-trip pin still grades
# the INVERSE after its fixture was re-spelled (A), that `_rel` still names the tree the way the
# allow-list is written (B), that the si_6 test still measures the write it claims to (C), and
# that the tightened stub mode is load-bearing rather than decorative (D).
#
# The PR matrix on the mirror is the only thing that closes the Windows axis. Read its CONCLUSION.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"
TDD=src/mokata/tdd_state.py
SHIM=tests/test_shim_declaration.py
SQL=src/mokata/memory/_sqlite.py
BATCH=tests/test_mutant_batch_driver.py
HARN=tests/test_mutation_harness.py

TD='test_run_id_drift.py'
TS='test_shim_declaration.py'
TW='test_si_6_behavioural_write_freedom.py'
TB='test_mutant_batch_driver.py'
TH='test_mutation_harness.py'

SR=tests/_shipped_reads.py
TSR='test_s28_shipped_reads_guarded.py'

TOTAL=24
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

# ==== A. ★ the round-trip pin STILL GRADES THE INVERSE after the fixture was re-spelled ========
# This is the question the stage has to answer about itself. The fixture stopped feeding POSIX
# literals; if that turned the pin into a tautology, every mutant here comes back GREEN.

mutant "A01 ★ root_of_state_dir returns the directory itself — the inverse stops inverting" "$TDD" \
  '    return os.sep.join(parts[:-len(tail)]) or os.sep' \
  '    return os.path.normpath(directory)' "$TD"

mutant "A02 ★ off-by-one on the trim — one layout component survives into the repo root" "$TDD" \
  '    return os.sep.join(parts[:-len(tail)]) or os.sep' \
  '    return os.sep.join(parts[:-len(tail) + 1]) or os.sep' "$TD"

mutant "A03 ★ the filesystem-root arm is dropped — a store at / recovers '' instead of /" "$TDD" \
  '    return os.sep.join(parts[:-len(tail)]) or os.sep' \
  '    return os.sep.join(parts[:-len(tail)])' "$TD"

mutant "A04 ★ the length guard admits a directory that IS exactly the layout tail" "$TDD" \
  '    if len(parts) <= len(tail) or tuple(parts[-len(tail):]) != tail:' \
  '    if len(parts) < len(tail) or tuple(parts[-len(tail):]) != tail:' "$TD"

mutant "A05 the tail check is dropped — any directory recovers a 'root'" "$TDD" \
  '    if len(parts) <= len(tail) or tuple(parts[-len(tail):]) != tail:' \
  '    if False:' "$TD"

mutant "A06 state_dir loses a layout level — the pair moves apart" "$TDD" \
  '    return os.path.join(root, MOKATA_DIR, TEMP_LOCAL_DIRNAME, STATE_DIRNAME)' \
  '    return os.path.join(root, MOKATA_DIR, STATE_DIRNAME)' "$TD"

# ==== B. `_rel` still names the tree the way the allow-list is WRITTEN =========================
# The fix changed the base from a module constant to one derived from `root`. If that derivation
# is wrong the real sweeps stop producing `tests/<file>.py`, and the allow-list, the declared
# module map and MECHANISM_OWN_TESTS all stop matching — which must be RED, not a quiet re-key.

mutant "B01 ★ _rel bases on the walked root itself — keys lose their 'tests/' prefix" "$SHIM" \
  '    return os.path.relpath(full, os.path.dirname(os.path.normpath(root))).replace(os.sep, "/")' \
  '    return os.path.relpath(full, root).replace(os.sep, "/")' "$TS"

mutant "B02 _rel climbs one level too far — every key gains a directory" "$SHIM" \
  '    return os.path.relpath(full, os.path.dirname(os.path.normpath(root))).replace(os.sep, "/")' \
  '    return os.path.relpath(full, os.path.dirname(os.path.dirname(os.path.normpath(root)))).replace(os.sep, "/")' "$TS"

# ==== C. the si_6 test still MEASURES the write it claims ======================================
# Its subject is refusal (b): `_apply_pragmas` moves a durable byte. Wrapping the precondition
# read in `closing()` must not have cost it that. Mutate the product write away; it must fire.

mutant "C01 ★ _apply_pragmas never reaches the WAL switch — the measured write disappears" "$SQL" \
  '    mode, detail = _switch_to_wal(conn, busy_timeout_ms=busy_timeout_ms)' \
  '    return
    mode, detail = _switch_to_wal(conn, busy_timeout_ms=busy_timeout_ms)' "$TW"

# C02 is graded by the WAL suites, not by si_6 — si_6's subject is the durable-byte claim, and
# busy_timeout stores no byte. Pointed at its real grader rather than left as a false survivor.
mutant "C02 the busy_timeout pragma is dropped from _apply_pragmas" "$SQL" \
  '    conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")' \
  '    pass' "test_ms_s4_sqlite_wal.py"

# ==== D. the tightened stub mode is LOAD-BEARING, not decorative ===============================
# 0o755 -> 0o700 keeps the owner's execute bit, which is the only bit these stubs need. Prove the
# bit is actually required: drop it, and the tests that exec the stub must fail.

mutant "D01 ★ the batch-driver stub loses its execute bit entirely" "$BATCH" \
  '        os.chmod(self.stub, 0o700)' \
  '        os.chmod(self.stub, 0o600)' "$TB"

mutant "D02 ★ the window-holder stub loses its execute bit (harness site 1)" "$HARN" \
  '        holder = os.path.join(self.tmp, "python-window-holder")
        pathlib.Path(holder).write_text(WINDOW_HOLDER, encoding="utf-8")
        os.chmod(holder, 0o700)
        proc = subprocess.Popen(' \
  '        holder = os.path.join(self.tmp, "python-window-holder")
        pathlib.Path(holder).write_text(WINDOW_HOLDER, encoding="utf-8")
        os.chmod(holder, 0o600)
        proc = subprocess.Popen(' "$TH"

mutant "D03 ★ the flock shim loses its execute bit" "$HARN" \
  '        os.chmod(shim, 0o700)' \
  '        os.chmod(shim, 0o600)' "$TH"

# ==== R. THE RIDER — the anti-vacuity floor is derived from the INDEX, not from the disk =======
# ⚠ R01 is the defect itself, replayed. Unlike A-C above, this batch IS fully gradeable on POSIX:
# the defect was never platform-specific, it was machine-specific, and `git ls-files` answers the
# same on every machine. So a survivor here is a weak pin and nothing else.

mutant "R01 ★★ the deriving set asks the DISK again — the exact defect the rider fixed" "$SR" \
  '    return frozenset(e for e in literal_excludes(excludes) if e in tracked)' \
  '    return frozenset(e for e in literal_excludes(excludes)
                     if os.path.exists(os.path.join(root, *e.split("/"))))' "$TSR"

mutant "R02 the deriving set stops filtering at all (every literal re-enters)" "$SR" \
  '    return frozenset(e for e in literal_excludes(excludes) if e in tracked)' \
  '    return literal_excludes(excludes)' "$TSR"

mutant "R03 ★ an unreadable index collapses into an EMPTY set — §7g, three answers become two" "$SR" \
  '    if proc.returncode != 0:
        return None' \
  '    if proc.returncode != 0:
        return frozenset()' "$TSR"

mutant "R04 ★ a missing git binary collapses into an empty set instead of UNDECIDABLE" "$SR" \
  '    except OSError:
        return None' \
  '    except OSError:
        return frozenset()' "$TSR"

mutant "R05 ★ an EMPTY deriving set renders GREEN — the vacuous pass the floor exists to refuse" "$SR" \
  '    BASIS_DERIVING_SET_EMPTY: UNDECIDABLE,' \
  '    BASIS_DERIVING_SET_EMPTY: GREEN,' "$TSR"

mutant "R06 ★ an UNREADABLE index renders GREEN" "$SR" \
  '    BASIS_INDEX_UNREADABLE: UNDECIDABLE,' \
  '    BASIS_INDEX_UNREADABLE: GREEN,' "$TSR"

mutant "R07 ★ the empty arm is dropped — 'saw nothing' and 'saw the wrong thing' share a message" "$SR" \
  '    if not tracked:
        return DerivingSetResolution(
            BASIS_DERIVING_SET_EMPTY,' \
  '    if False:
        return DerivingSetResolution(
            BASIS_DERIVING_SET_EMPTY,' "$TSR"

mutant "R08 the floor only notices SHRINKAGE — a newly tracked internal path is absorbed" "$SR" \
  '    if gained or lost:' \
  '    if lost:' "$TSR"

mutant "R09 the floor only notices GROWTH — losing docs/build stops being reported" "$SR" \
  '    if gained or lost:' \
  '    if gained:' "$TSR"

mutant "R10 tracked_paths stops implying directories — every directory exclude leaves the set" "$SR" \
  '        for i in range(1, len(parts) + 1):' \
  '        for i in range(len(parts), len(parts) + 1):' "$TSR"

# The declaration is the floor's NUMBER. Nudging it must be as loud as breaking the reader —
# doc 84's "assert MEMBERSHIP, never a tally to be nudged until the test goes green".
mutant "R11 ★ the declaration quietly loses docs/build (the floor's number nudged down)" "$SR" \
  '    "docs/build",
    "docs/launch",' \
  '    "docs/launch",' "$TSR"

# ==== verdict =================================================================================

printf '\n================================================================================\n'
printf 'STAGE 29 MUTANTS: %s ran of %s — %s RED, %s GREEN\n' "$ran" "$TOTAL" "$red" "$green"
if [ "$green" -ne 0 ]; then
    printf 'SURVIVORS (each is a pin that does not grade):\n%s' "$survivors"
    printf '================================================================================\n'
    exit 1
fi
printf 'Every mutant was caught.\n'
printf '================================================================================\n'
