#!/usr/bin/env bash
# floor-python.sh — provision, and grade, the DECLARED Python floor. One command, in the repo.
#
#   scripts/floor-python.sh                       provision the floor venv (jsonschema present)
#   scripts/floor-python.sh --jsonschema absent   provision the other CI floor leg
#   scripts/floor-python.sh --check               grade the provisioned interpreter
#   scripts/floor-python.sh --print-floor         print the floor pyproject.toml declares
#   scripts/floor-python.sh --grade X.Y.Z         grade a version against that floor
#   scripts/floor-python.sh --dry-run             print what provisioning would run
#   scripts/floor-python.sh --exec -m unittest discover -s tests -t tests
#
# WHY THIS FILE EXISTS. `pyproject.toml` promises `requires-python = ">=X.Y"`, and until 0.0.18 the
# only way anyone exercised that promise locally was an ad-hoc venv at `/Users/jas/jsvenv_mk310` —
# hand-built, outside the repo, on one laptop. It cannot be checked out and it cannot be reviewed,
# so "I ran it on the floor" was not a claim anyone else could re-run. It had also silently drifted
# once: doc 84 records it carrying `mcp 2.0.0` at 0.0.18 stage 3a, which means a run of stages
# reported the floor green with every MCP gate skipped. That is `CI-MATRIX-FLOOR-GAP`'s local half,
# and this script is its remedy.
#
# (No version appears in that paragraph, or anywhere else here, on purpose — see below.)
#
# THE FLOOR IS NEVER WRITTEN DOWN HERE. It is read out of `pyproject.toml` every time, and
# `tests/test_floor_provisioner.py` reds if a version literal ever appears in this file. A
# provisioner that keeps building the old floor after the project raises it is a gate verifying
# the wrong thing while reporting success — the shape `tests/test_pg_floor_drift.py` closed for
# the PostgreSQL floor at 0.0.18 stage 20.
#
# ---------------------------------------------------------------------------------------------
# EXIT CONTRACT — FOUR VERDICTS ABOUT AN INTERPRETER, NOT TWO (doc 85 §7g)
#
#   0  AT THE FLOOR. The interpreter is exactly the version pyproject.toml declares as its floor,
#      or the requested pure query answered.
#   1  USAGE. A bad flag or an argument that is not a version. Nothing was provisioned.
#   2  CANNOT PROVISION. No `uv` and no floor interpreter on PATH, so the venv was not built. The
#      message names both remedies. This is NOT a verdict about any interpreter.
#      ⚠ `--dry-run` ANSWERS 2 HERE TOO, and that is the 0.0.18 cut-halt repair. It used to print
#      `provision : <NO ROUTE>` and exit 0 — the same machine, the same fact, two statuses, and
#      the green one is the lie: `tests/test_floor_provisioner.py` read that line as a command and
#      asserted `--clear` was in it. A mode that reports "I cannot do this" must not exit 0.
#   3  BELOW THE FLOOR. The interpreter is older than the project supports. This is the ordinary
#      answer for a bare `python3` on a machine whose system Python predates the floor.
#   4  ABOVE THE FLOOR. ★ The status a `>=` check cannot produce, and the reason this grades for
#      EQUALITY. Running the suite on the newest interpreter and calling it a floor run is exactly
#      the false claim the row is about; it has to fail, and it has to fail DIFFERENTLY from 3,
#      because "install an older Python" and "you are not testing the floor" are opposite fixes.
#   5  NOT PROVISIONED. There is no interpreter at the venv path at all. Distinct from 3 on
#      purpose: an absent check and a failed check must never share a status.
# ---------------------------------------------------------------------------------------------
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

# THE DEFAULT VENV LIVES UNDER build/. Not `.venv-floor/`, and the reason is a control rather than
# a preference: `scripts/sync-public.sh` mirrors the WORKING TREE with rsync, which does not read
# `.gitignore` — so a new top-level directory would need a new entry in BOTH sync controls or it
# would ship several hundred megabytes of interpreter to the public mirror. `build/` is already
# excluded by both and already gitignored. Reusing a control beats adding a fourteenth one to a
# list whose completeness is itself a tracked defect class (CLAUDE-MD-SHIPS-LIST-INCOMPLETE).
VENV="build/floor-venv"
JSONSCHEMA="present"
MODE="provision"
GRADE_ARG=""
EXEC_ARGS=()

usage() {
    sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

die_usage() {
    printf 'floor-python.sh: %s\n\n' "$1" >&2
    usage >&2
    exit 1
}

# ---- THE DERIVATION -----------------------------------------------------------------------------
# The floor is a property of the manifest. Read with a grep rather than a TOML parser on purpose:
# this script runs BEFORE any interpreter is provisioned, so it may not assume a Python at all —
# and on this machine the only `python3` on PATH is below the floor it would be asked to read.
declared_floor() {
    local line
    line="$(grep -E '^[[:space:]]*requires-python[[:space:]]*=' pyproject.toml | head -n 1)"
    printf '%s' "$line" | sed -E 's/.*>=[[:space:]]*([0-9]+\.[0-9]+).*/\1/'
}

FLOOR="$(declared_floor)"
case "$FLOOR" in
    [0-9]*.[0-9]*) : ;;
    *) printf 'floor-python.sh: pyproject.toml declares no `requires-python = ">=X.Y"` floor\n' >&2
       exit 1 ;;
esac

# ---- THE GRADE ----------------------------------------------------------------------------------
# Equality on (major, minor), and each disagreement gets its own status and its own sentence.
grade_version() {
    local version="$1" where="$2" major minor fmajor fminor
    case "$version" in
        [0-9]*.[0-9]*) : ;;
        *) printf 'floor-python.sh: %r is not a version\n' "$version" >&2; exit 1 ;;
    esac
    major="${version%%.*}"
    minor="${version#*.}"; minor="${minor%%.*}"
    fmajor="${FLOOR%%.*}"
    fminor="${FLOOR#*.}"; fminor="${fminor%%.*}"
    case "$major$minor" in *[!0-9]*) die_usage "'$version' is not a version" ;; esac

    if [ "$major" -eq "$fmajor" ] && [ "$minor" -eq "$fminor" ]; then
        printf 'AT THE FLOOR — %s is Python %s, the floor pyproject.toml declares (%s)\n' \
            "$where" "$version" "$FLOOR"
        return 0
    fi
    if [ "$major" -lt "$fmajor" ] || { [ "$major" -eq "$fmajor" ] && [ "$minor" -lt "$fminor" ]; }; then
        printf 'BELOW THE FLOOR — %s is Python %s; the project declares %s and does not support it.\n' \
            "$where" "$version" "$FLOOR" >&2
        printf '  Provision the floor with: scripts/floor-python.sh\n' >&2
        return 3
    fi
    printf 'ABOVE THE FLOOR — %s is Python %s; the floor is %s.\n' "$where" "$version" "$FLOOR" >&2
    printf '  This interpreter is supported, but a run on it is NOT a floor run. Do not report it\n' >&2
    printf '  as one: the faults this leg exists to catch exist only BELOW the newest version.\n' >&2
    return 4
}

venv_python() {
    if [ -x "$VENV/bin/python" ]; then printf '%s' "$VENV/bin/python"
    elif [ -x "$VENV/Scripts/python.exe" ]; then printf '%s' "$VENV/Scripts/python.exe"
    fi
}

# ---- ARGUMENTS ----------------------------------------------------------------------------------
while [ "$#" -gt 0 ]; do
    case "$1" in
        --print-floor) MODE="print-floor"; shift ;;
        --dry-run)     MODE="dry-run"; shift ;;
        --check)       MODE="check"; shift ;;
        --grade)       MODE="grade"; GRADE_ARG="${2:?--grade needs a version}"; shift 2 ;;
        --venv)        VENV="${2:?--venv needs a path}"; shift 2 ;;
        --jsonschema)
            JSONSCHEMA="${2:?--jsonschema needs present|absent}"
            case "$JSONSCHEMA" in present|absent) : ;; *) die_usage "--jsonschema takes present|absent" ;; esac
            shift 2 ;;
        --exec)        MODE="exec"; shift; EXEC_ARGS=("$@"); break ;;
        -h|--help)     usage; exit 0 ;;
        *)             die_usage "unknown option '$1'" ;;
    esac
done

# ---- THE PROVISIONING COMMAND, derived ----------------------------------------------------------
# Two routes, both keyed on $FLOOR, tried in this order. `uv` first because it FETCHES a managed
# CPython at the requested version — which is what makes this reproducible on a machine that has
# no floor interpreter at all, and therefore what separates this from the home-directory venv it
# replaces. `--seed` puts pip in the venv so both routes install identically below.
#
# ⚠ `--clear` ON BOTH ROUTES, AND IT IS NOT A CONVENIENCE. `uv venv` REFUSES an existing directory
# outright, so without it this command cannot be run twice — and switching between the two CI
# floor legs (`--jsonschema present` / `absent`) is a second run. Of the two available fixes,
# REUSE is the wrong one: an inherited environment is precisely how `/Users/jas/jsvenv_mk310` came
# to be carrying a broken MCP SDK while every stage reported the floor green. A provisioner grades
# what it built, or it is grading someone else's leftovers.
#
# ⚠ EACH ROUTE IS A FUNCTION OF $FLOOR ALONE, SEPARATE FROM WHETHER THIS MACHINE HAS IT. That
# split is the 0.0.18 cut-halt repair: `provision_command` used to be the only way to see a route,
# so on a machine with neither tool it printed nothing and the one test that graded the derivation
# — "does the command REBUILD rather than inherit?" — had no command to grade. It passed on the
# maintainer's Mac, which happens to carry a hand-built floor interpreter, and reded on every CI
# leg that does not — CI-MATRIX-FLOOR-GAP's original defect surviving its own fix. `--dry-run` now
# prints BOTH routes with their availability, so the derivation is gradeable on any machine and
# the availability is a separate, separately-stated fact.
uv_route()     { printf 'uv venv --clear --seed --python %s %s' "$FLOOR" "$VENV"; }
python_route() { printf 'python%s -m venv --clear %s' "$FLOOR" "$VENV"; }

have_uv()     { command -v uv >/dev/null 2>&1; }
have_python() { command -v "python$FLOOR" >/dev/null 2>&1; }

provision_command() {
    if have_uv; then
        uv_route
    elif have_python; then
        python_route
    fi
}

case "$MODE" in
    print-floor)
        printf '%s\n' "$FLOOR"
        exit 0 ;;

    grade)
        grade_version "$GRADE_ARG" "the version given"
        exit $? ;;

    check)
        PY="$(venv_python)"
        if [ -z "$PY" ]; then
            printf 'NOT PROVISIONED — no interpreter at %s.\n' "$VENV" >&2
            printf '  Nothing was graded. This is an UN-RUN check, not a failed one; run\n' >&2
            printf '  `scripts/floor-python.sh` to build it.\n' >&2
            exit 5
        fi
        grade_version "$("$PY" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')" "$PY"
        exit $? ;;

    exec)
        PY="$(venv_python)"
        if [ -z "$PY" ]; then
            printf 'NOT PROVISIONED — no interpreter at %s. Run `scripts/floor-python.sh` first.\n' \
                "$VENV" >&2
            exit 5
        fi
        exec "$PY" "${EXEC_ARGS[@]}" ;;

    dry-run)
        cmd="$(provision_command)"
        printf 'floor        : %s   (from pyproject.toml requires-python)\n' "$FLOOR"
        printf 'venv         : %s\n' "$VENV"
        printf 'jsonschema   : %s\n' "$JSONSCHEMA"
        # BOTH routes, always — the command each WOULD run, and separately whether this machine
        # can. Printing only the chosen one made the derivation invisible exactly where it was
        # most in doubt (see the note above the route functions).
        if have_uv; then uv_avail="available"; else uv_avail="absent"; fi
        if have_python; then py_avail="available"; else py_avail="absent"; fi
        printf 'route uv     : %s   [uv: %s]\n' "$(uv_route)" "$uv_avail"
        printf 'route python : %s   [python%s: %s]\n' "$(python_route)" "$FLOOR" "$py_avail"
        if [ -z "$cmd" ]; then
            printf 'provision    : <NO ROUTE> — neither `uv` nor `python%s` is on PATH\n' "$FLOOR"
        else
            printf 'provision    : %s\n' "$cmd"
        fi
        printf 'then         : %s/bin/python -m pip install -e .\n' "$VENV"
        printf '               ... -m pip install --require-hashes -r requirements/ci.txt\n'
        if [ "$JSONSCHEMA" = "present" ]; then
            printf '               ... -m pip install --require-hashes -r requirements/jsonschema.txt\n'
        else
            printf '               ... -m pip uninstall -y jsonschema\n'
        fi
        # ⚠ THE VERDICT, NOT JUST THE LISTING. A dry run on a machine with no route has just
        # reported that provisioning is impossible here; exiting 0 would make "I cannot do this"
        # and "here is what I will do" the same status (doc 85 §7g), and it is what let a test
        # read the `<NO ROUTE>` line as if it were a command.
        if [ -z "$cmd" ]; then
            printf '\nCANNOT PROVISION — the floor is Python %s and this machine offers no route\n' \
                "$FLOOR" >&2
            printf '  to it. Install `uv` (it fetches the interpreter itself) or python%s.\n' \
                "$FLOOR" >&2
            exit 2
        fi
        exit 0 ;;
esac

# ---- PROVISION ----------------------------------------------------------------------------------
cmd="$(provision_command)"
if [ -z "$cmd" ]; then
    printf 'CANNOT PROVISION — the floor is Python %s and this machine offers no route to it.\n' \
        "$FLOOR" >&2
    printf '  Install either:\n' >&2
    printf '    * uv          (https://docs.astral.sh/uv/) — it fetches the interpreter itself\n' >&2
    printf '    * python%s    from python.org, pyenv, or your package manager\n' "$FLOOR" >&2
    printf '  Nothing was built and nothing was graded.\n' >&2
    exit 2
fi

printf '==> %s\n' "$cmd"
# Word-splitting is intended: the command is built above from $FLOOR and $VENV, not from input.
# shellcheck disable=SC2086
$cmd || { printf 'floor-python.sh: provisioning failed\n' >&2; exit 2; }

PY="$(venv_python)"
if [ -z "$PY" ]; then
    printf 'CANNOT PROVISION — %s ran but left no interpreter at %s\n' "$cmd" "$VENV" >&2
    exit 2
fi

printf '==> %s -m pip install -e .\n' "$PY"
"$PY" -m pip install -q -e . || exit 2
printf '==> %s -m pip install --require-hashes -r requirements/ci.txt\n' "$PY"
"$PY" -m pip install -q --require-hashes -r requirements/ci.txt || exit 2
if [ "$JSONSCHEMA" = "present" ]; then
    printf '==> %s -m pip install --require-hashes -r requirements/jsonschema.txt\n' "$PY"
    "$PY" -m pip install -q --require-hashes -r requirements/jsonschema.txt || exit 2
else
    # The `absent` CI leg, reproduced locally: jsonschema is REMOVED rather than never installed,
    # because `pip install -e .` may pull it in transitively and a leg that merely skipped the
    # install would be the `present` leg wearing the other name.
    printf '==> %s -m pip uninstall -y jsonschema\n' "$PY"
    "$PY" -m pip uninstall -q -y jsonschema >/dev/null 2>&1 || true
fi

grade_version "$("$PY" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')" "$PY"
rc=$?
printf '\nRun the suite on the floor with:\n  scripts/floor-python.sh --exec -m unittest discover -s tests -t tests\n'
exit "$rc"
