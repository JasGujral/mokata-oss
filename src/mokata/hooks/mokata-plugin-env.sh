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
    # uv may have pruned its cache since the record was written: a dead path is no runtime.
    [ -n "$_mk_py" ] && [ -x "$_mk_py" ]
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
