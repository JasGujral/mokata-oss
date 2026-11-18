# mokata plugin route — shared helpers for `mokata-hook-launch` and `mokata-launch` (0.0.26 L8).
#
# SOURCED (`. file`), never executed. Defines functions only. Like the shims that source it, it
# calls NO external binary: `dirname`, `cat`, `sed` live in /usr/bin, which a GUI-launched Claude
# Code's PATH may omit. Only shell builtins (`read`, `case`, `[`, `command -v`, parameter
# expansion, globbing) are used.
#
# The plugin route pins EVERYTHING to the plugin's own version (`plugin_runtime.py` has the why):
#   _mk_read_version   -> $_mk_version  : `__version__` of THIS checkout's src/mokata/__init__.py
#                                         (a guarded version field, equal to plugin.json's)
#   _mk_pinned_python  -> $_mk_py       : the interpreter of the pinned `uvx --from
#                                         "mokata[full]==$_mk_version"` environment, as recorded by
#                                         the plugin's MCP server in ${CLAUDE_PLUGIN_DATA}
#   _mk_find_uvx       -> $_mk_uvx      : `uvx`, on PATH or where the uv installers put it
#   _mk_py_ok CMD...   -> 0 when CMD is a Python at or above mokata's floor (3.12, PY-FLOOR-3.12,
#                         = `mokata.PYTHON_FLOOR` = pyproject's requires-python). An OLDER Python 3
#                         is remembered in $_mk_old ("3.10 (python3)") so the caller's last line can
#                         say WHY nothing ran, instead of "no Python found" on a box that has one.
#
# Callers must set $_src_dir (the checkout's `src/`) first.
#
# Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.

_mk_version=""
_mk_py=""
_mk_cr=$(printf '\r')          # printf is a builtin in every shell the hooks run under
_mk_uvx=""
_mk_old=""

_mk_py_ok() {
    # `$(...)` is a subshell, not an external binary; the version is printed BY the candidate.
    _mk_pv=$("$@" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null) || return 1
    case $_mk_pv in
        3.1[2-9]|3.[2-9][0-9]) return 0 ;;
        3.*) [ -n "$_mk_old" ] || _mk_old="$_mk_pv ($*)"; return 1 ;;
        *) return 1 ;;
    esac
}

# The tail every "nothing ran" line carries: the older Python it DID find, if any.
_mk_old_note() {
    if [ -n "$_mk_old" ]; then
        _mk_note=" — found Python $_mk_old, but mokata needs Python 3.12+"
    else
        _mk_note=""
    fi
}

_mk_read_version() {
    _mk_version=""
    [ -n "${_src_dir:-}" ] && [ -r "$_src_dir/mokata/__init__.py" ] || return 1
    while IFS= read -r _mk_l || [ -n "$_mk_l" ]; do
        case $_mk_l in
            __version__*)
                _mk_v=${_mk_l#*[\"\']}
                _mk_version=${_mk_v%%[\"\']*}
                break
                ;;
        esac
    done < "$_src_dir/mokata/__init__.py"
    [ -n "$_mk_version" ]
}

# ${CLAUDE_PLUGIN_DATA} is exported to hooks and MCP servers only. Run from the Bash tool (a slash
# command's launcher call) it is absent, so look where Claude Code keeps it:
# ${CLAUDE_CONFIG_DIR:-~/.claude}/plugins/data/mokata-<marketplace>/.
_mk_data_dir() {
    _mk_data=${CLAUDE_PLUGIN_DATA:-}
    [ -n "$_mk_data" ] && return 0
    _mk_base="${CLAUDE_CONFIG_DIR:-${HOME:-}/.claude}/plugins/data"
    for _mk_d in "$_mk_base"/mokata-*; do
        if [ -d "$_mk_d" ]; then
            _mk_data=$_mk_d
            return 0
        fi
    done
    return 1
}

_mk_pinned_python() {
    _mk_py=""
    [ -n "$_mk_version" ] || return 1
    _mk_data_dir || return 1
    _mk_rec="$_mk_data/runtime-$_mk_version.txt"
    [ -r "$_mk_rec" ] || return 1
    IFS= read -r _mk_py < "$_mk_rec" || [ -n "$_mk_py" ] || return 1
    # A record written with CRLF (Windows text mode, or an older mokata) ends in `\r`: drop it.
    _mk_py=${_mk_py%"$_mk_cr"}
    # uv may have pruned its cache since the record was written: a dead path is no runtime.
    [ -n "$_mk_py" ] && [ -x "$_mk_py" ]
}

# mokata's ONE user-cache rule (`mokata.pycache.user_cache_base`, 0.0.29 F) in builtins: is "$1" an
# ABSOLUTE path by this platform's own rule? POSIX: a leading `/` only. A drive with a root
# (`C:\x`, `C:/x`) or a UNC head (`\\srv\s`) only where the shell IS on Windows — Git Bash / MSYS /
# Cygwin, told by variables (`MSYSTEM`, `OSTYPE`), never by running a binary. Anything else is
# relative, and a relative cache base is ignored: taken as given it put the hooks' bytecode under
# whatever directory the hook ran in — the user's project (USER-CACHE-DIR-RELATIVE-XDG).
_mk_abs() {
    case $1 in
        /*) return 0 ;;
    esac
    _mk_win=""
    case ${MSYSTEM:-} in ?*) _mk_win=1 ;; esac
    case ${OSTYPE:-} in msys*|cygwin*) _mk_win=1 ;; esac
    [ -n "$_mk_win" ] || return 1
    case $1 in
        [A-Za-z]:[\\/]*) return 0 ;;
        [\\/][\\/][!\\/]*) return 0 ;;
    esac
    return 1
}

# BYTECODE (0.0.28 W5; one rule since 0.0.29 F). Claude Code exports PYTHONDONTWRITEBYTECODE=1 to
# every child, hooks included, and the pinned uv runtime is installed without .pyc: honouring it,
# every hook recompiled its ~60 modules. So the launched Python writes its bytecode into mokata's OWN
# per-user cache — `$XDG_CACHE_HOME` if absolute, else `$HOME/.cache` if absolute, then
# `mokata/pycache` — never beside the sources (a plugin checkout is not ours to dirty) and never
# under a relative path. No absolute base -> the variable stays and nothing is written. An
# unwritable cache costs the compile and nothing else: Python skips a write it cannot make.
_mk_bytecode_cache() {
    _mk_base=""
    if [ -n "${XDG_CACHE_HOME:-}" ] && _mk_abs "$XDG_CACHE_HOME"; then
        _mk_base=$XDG_CACHE_HOME
    elif [ -n "${HOME:-}" ] && _mk_abs "$HOME"; then
        _mk_base=$HOME/.cache
    fi
    if [ -n "$_mk_base" ]; then
        PYTHONPYCACHEPREFIX=$_mk_base/mokata/pycache
        export PYTHONPYCACHEPREFIX
        unset PYTHONDONTWRITEBYTECODE
    fi
}

_mk_find_uvx() {
    _mk_uvx=""
    if command -v uvx >/dev/null 2>&1; then
        _mk_uvx=$(command -v uvx)
        return 0
    fi
    _mk_dirs=${MOKATA_UV_DIRS-${HOME:-}/.local/bin:${HOME:-}/.cargo/bin:/opt/homebrew/bin:/usr/local/bin}
    _mk_oldifs=${IFS-}
    IFS=:
    for _mk_d in $_mk_dirs; do
        [ -n "$_mk_d" ] || continue
        for _mk_n in uvx uvx.exe; do
            if [ -x "$_mk_d/$_mk_n" ]; then
                IFS=$_mk_oldifs
                _mk_uvx="$_mk_d/$_mk_n"
                return 0
            fi
        done
    done
    IFS=$_mk_oldifs
    return 1
}
