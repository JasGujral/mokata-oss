#!/bin/sh
# mokata cross-platform hook launcher (Stage 28).
#
# Claude Code's shipped, static plugin hooks.json can't bake in the user's Python
# interpreter (it has no access to sys.executable), and a bare `python3` is not
# found when:
#   * Windows uses `python` or the `py -3` launcher (no `python3` on PATH); or
#   * a GUI-launched macOS Claude Code runs hooks with a minimal PATH that omits
#     Homebrew (/opt/homebrew/bin), pyenv shims, or /usr/local/bin.
#
# This launcher resolves *a* Python 3 — mokata's hooks carry no third-party deps
# (they add ../src to sys.path), so any Python 3 works — and execs it with the
# target script plus any forwarded args. If no interpreter is found it prints one
# clear line to stderr and exits 0, so a missing Python never blocks the session.
#
# Usage:  sh launch.sh <script.py> [args...]
# Escape hatch: set MOKATA_PYTHON to an absolute interpreter to skip resolution.
# (MOKATA_PYTHON_DIRS overrides the common-location search list; colon-separated.)

set -u

target=${1:-}
if [ -z "$target" ]; then
    echo "mokata: launch.sh called without a target script; skipping hook." >&2
    exit 0
fi
shift

# Does the given command run as Python 3?  ("$@" lets us pass `py -3` as one cmd.)
_is_py3() {
    "$@" -c 'import sys; raise SystemExit(0 if sys.version_info[0] == 3 else 1)' \
        >/dev/null 2>&1
}

# 1. Explicit override wins (an exotic install the user pinned themselves).
if [ -n "${MOKATA_PYTHON:-}" ] && _is_py3 "$MOKATA_PYTHON"; then
    exec "$MOKATA_PYTHON" "$target" "$@"
fi

# 2. The usual names on PATH, in order. `py -3` is the Windows Python launcher.
for cand in python3 python; do
    if command -v "$cand" >/dev/null 2>&1 && _is_py3 "$cand"; then
        exec "$cand" "$target" "$@"
    fi
done
if command -v py >/dev/null 2>&1 && _is_py3 py -3; then
    exec py -3 "$target" "$@"
fi

# 3. Common install locations a minimal PATH (macOS GUI launch) may omit.
#    Overridable via MOKATA_PYTHON_DIRS for exotic setups / testing.
dirs=${MOKATA_PYTHON_DIRS-/opt/homebrew/bin:/usr/local/bin:/usr/bin:${HOME:-}/.pyenv/shims}
oldifs=${IFS-}
IFS=:
for dir in $dirs; do
    [ -n "$dir" ] || continue
    for name in python3 python; do
        if [ -x "$dir/$name" ] && _is_py3 "$dir/$name"; then
            IFS=$oldifs
            exec "$dir/$name" "$target" "$@"
        fi
    done
done
IFS=$oldifs

# 4. Nothing found — degrade clean. A missing interpreter never blocks a session.
echo "mokata: no Python 3 found; skipping hook (set MOKATA_PYTHON to point at one)." >&2
exit 0
