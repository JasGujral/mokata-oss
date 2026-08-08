#!/usr/bin/env bash
# Drives the mutant list in `_stage21_mutants.txt` through scripts/mutate.sh — the ONLY sanctioned
# mutator (doc 85 §7b). Never hand-edit a file to see whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python tests/_stage21_mutants.sh
#
# IT CONSUMES THE EXIT CONTRACT (0.0.17 stage 18b): a VERDICT (RED / GREEN) is a completed run —
# count it and carry on; a HARNESS FAILURE (any non-zero, or a status 0 carrying no verdict) STOPS
# THE BATCH DEAD, because on an exit 4 the mutator deliberately LEAVES THE TARGET MUTATED and
# everything graded afterwards would be graded against a tree holding an uncontrolled edit.
#
# ⚠ THE FOURTH COPY OF THAT CONTRACT — MUTANT-DRIVER-CONTRACT-DUPLICATED (doc 84). `_run_mutants.sh`,
# `_stage16_mutants.sh` and `_stage17_mutants.sh` each carry their own. Recording the fourth
# instance is what stops "we'll unify it later" from being said a fourth time; unifying them
# mid-stage on files whose whole value is that they were verified is not this stage's change.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"

W=src/mokata/repo_walk.py                 # the shared rule
I=src/mokata/knowledge/index.py           # the walker that RECORDS + the renderer
C=src/mokata/cli_commands/index.py        # the surface the skip is declared on
S=scripts/sync-public.sh                  # the same rule on the distribution surface

T='test_nested_checkout*.py'              # stage 20's boundary pins + stage 21's walker pins
P='test_sync_public*.py'                  # the mirror, run for real

TOTAL=19
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
        printf '  See scripts/mutate.sh'"'"'s EXIT CONTRACT for what %s means and what to do.\n' "$rc"
        printf '================================================================================\n'
        exit "$rc"
    fi
    case "$out" in
        RED*)   red=$((red + 1)) ;;
        GREEN*) green=$((green + 1)); survivors="$survivors  - $label"$'\n' ;;
        *)      printf '\nBATCH ABORTED — mutate.sh exited 0 for mutant %s of %s but printed no verdict.\n' \
                    "$ran" "$TOTAL"
                printf '  mutant: %s\n  said  : %s\n' "$label" "${out:-<nothing at all>}"
                printf '  %s of %s mutants NEVER RAN. THIS BATCH DID NOT PASS.\n' \
                    "$((TOTAL - ran))" "$TOTAL"
                exit 70 ;;
    esac
}

# ---- the boundary predicate — both on-disk shapes, and the rule being real at all -------------

mutant "M01 the boundary sees only a .git DIRECTORY — a worktree's gitfile slips through" "$W" \
  '    return os.path.exists(os.path.join(path, CHECKOUT_MARKER))' \
  '    return os.path.isdir(os.path.join(path, CHECKOUT_MARKER))' "$T"

mutant "M02 the boundary sees only a .git FILE — a clone/submodule slips through" "$W" \
  '    return os.path.exists(os.path.join(path, CHECKOUT_MARKER))' \
  '    return os.path.isfile(os.path.join(path, CHECKOUT_MARKER))' "$T"

mutant "M03 the boundary rule goes inert — nothing is ever a checkout" "$W" \
  '    return os.path.exists(os.path.join(path, CHECKOUT_MARKER))' \
  '    return False' "$T"

mutant "M04 the boundary rule swallows the tree — everything is a checkout" "$W" \
  '    return os.path.exists(os.path.join(path, CHECKOUT_MARKER))' \
  '    return True' "$T"

mutant "M05 the rule reverts to a NAME — keyed on .claude, the thing this stage refuses" "$W" \
  'CHECKOUT_MARKER = ".git"' 'CHECKOUT_MARKER = ".claude"' "$T"

# ---- the walk policy: the two rules COMPOSE, and the prune is in place ------------------------

mutant "M06 the dot-prefix rule is dropped — '.venv' and '.mokata' get indexed" "$W" \
  '        if name.startswith("."):' '        if False:' "$T"

mutant "M07 the boundary rule is dropped from the walk — back to dot-prefix alone" "$W" \
  '        if is_checkout_boundary(full):' '        if False:' "$T"

mutant "M08 the prune is not in place — os.walk keeps descending into everything" "$W" \
  '    dirnames[:] = kept' '    dirnames = kept' "$T"

mutant "M09 the skip is SILENT — the checkout is pruned but never recorded" "$W" \
  '                skipped.append(full)' '                pass' "$T"

mutant "M10 the collector is never offered — nothing can be recorded upstream" "$W" \
  '                      skipped: Optional[List[str]] = None) -> None:' \
  '                      skipped: Optional[List[str]] = None) -> None:
    skipped = None' "$T"

# ---- the index: it asks to be told, remembers honestly, and forgets an abandoned walk ---------

mutant "M11 the index stops asking what was skipped" "$I" \
  '            prune_source_dirs(dirpath, dirnames, skipped=skipped)' \
  '            prune_source_dirs(dirpath, dirnames)' "$T"

mutant "M12 the record is never written back after the walk" "$I" \
  '        self.skipped_checkouts = sorted(os.path.relpath(p, root) for p in skipped)' \
  '        pass' "$T"

mutant "M13 an abandoned walk keeps the PREVIOUS walk's record — declares a skip it never made" "$I" \
  '        self.skipped_checkouts = []
        for dirpath, dirnames, filenames in os.walk(root):' \
  '        for dirpath, dirnames, filenames in os.walk(root):' "$T"

mutant "M14 the extra checkouts are truncated SILENTLY — '+N more' dropped" "$I" \
  '    if remainder:
        where += f", +{remainder} more"' \
  '    if False:
        where += f", +{remainder} more"' "$T"

mutant "M15 the declaration never reaches the user — mokata index goes quiet" "$C" \
  '    for line in skipped_checkout_lines(idx.skipped_checkouts):' \
  '    for line in []:' "$T"

# ---- the distribution surface: the same rule, graded by running the real script ---------------

mutant "M16 the mirror's exclusion reverts to a NAME — a vendored checkout is rsynced public" "$S" \
  'done < <(find "$SRC" -mindepth 2 -name .git -print 2>/dev/null || true)' \
  'done < <(find "$SRC" -mindepth 2 -name .claude -print 2>/dev/null || true)' "$P"

mutant "M17 the excludes are computed and then not passed to rsync" "$S" \
  '  ${NESTED_CHECKOUTS[@]+"${NESTED_CHECKOUTS[@]}"} \' \
  '  --exclude='"'"'/never-matches-anything-zz/'"'"' \' "$P"

mutant "M18 the DEST guard is removed — a checkout already in the mirror survives forever" "$S" \
  '  [ -n "$d" ] && [ -e "$d" ] && _check_internal "$d" "nested checkout"' \
  '  true' "$P"

# The mirror's own '.git' has exactly ONE defence (the -path prune) precisely so this mutant can
# be graded: two redundant defences cover for each other and both come back GREEN.
mutant "M19 the mirror's OWN .git enters the scan — the guard would rm -rf the public checkout" "$S" \
  'done < <(find . -path ./.git -prune -o -name .git -print 2>/dev/null || true)' \
  'done < <(find . -name .git -print 2>/dev/null || true)' "$P"

# ---- the record is UNCONDITIONAL -------------------------------------------------------------

printf '\n================================================================================\n'
printf 'STAGE 21 MUTANTS: %s RED / %s GREEN out of %s\n' "$red" "$green" "$TOTAL"
if [ -n "$survivors" ]; then
    printf 'SURVIVORS (a finding about a PIN, reported, never quietly dropped):\n%s' "$survivors"
    exit 1
fi
printf 'No survivors.\n'
printf '================================================================================\n'
