@echo off
rem mokata plugin hook launcher (HOOK-RESOLVE) — the Windows half of the self-resolving shim.
rem
rem NOT ON THE HOOK PATH (HOOK-SHELL-AGNOSTIC). This file used to be documented as the Windows
rem half of the hook launcher, on the premise that cmd.exe completes `hooks.json`'s
rem extension-less path to this `.cmd` through the Windows executable-extension search. That
rem premise is FALSE: read out of Claude Code 2.1.220, a hook is spawned either directly (exec
rem form), or via PowerShell, or via bash/Git Bash — **cmd.exe is never a hook shell**, so no
rem such completion ever happens and this file is never selected by a hook.
rem
rem It still ships for anyone invoking it directly from cmd.exe, and it keeps the same ladder
rem and the same contract as the sh shim. The hook routes are: `hooks.json` pins
rem `"shell": "bash"` (Git Bash runs the sh shim; no Git Bash = a LOUD named error), and
rem `mokata setup claude` wires EXEC form, which no shell parses at all.
rem Same ladder, same contract (0.0.26 L8 — the plugin's OWN version only, never a PATH install):
rem   0 %MOKATA_HOOK%  ->  1 the pinned runtime recorded in %CLAUDE_PLUGIN_DATA%\runtime-<v>.txt
rem   ->  2 a Python 3.12+ running the packaged module (`-m mokata_emit`, this checkout's src\ on
rem   PYTHONPATH)  ->  3 uvx --from "mokata[full]==<v>" mokata-hook  ->  4 exit 1, loud.
rem
rem Exit codes are the subcommand's own (`exit /b %ERRORLEVEL%`) — secret-guard's exit 2 still
rem BLOCKS. Exit 1 is the reserved MISCONFIGURATION code; this shim never invents an exit 2.
rem
rem Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.

setlocal EnableExtensions

if "%~1"=="" goto :fail

rem This shim lives at <plugin>\src\mokata\hooks\ — so ..\.. is the packaged source root.
set "MOKATA_SRC_DIR=%~dp0..\.."

rem <v> = this checkout's __version__ (a guarded version field equal to plugin.json's).
set "MOKATA_VER="
for /f "tokens=2 delims==" %%v in ('findstr /b /c:"__version__" "%MOKATA_SRC_DIR%\mokata\__init__.py" 2^>nul') do set "MOKATA_VER=%%~v"
if defined MOKATA_VER set "MOKATA_VER=%MOKATA_VER: =%"
if defined MOKATA_VER set "MOKATA_VER=%MOKATA_VER:"=%"

rem 0 — an explicit override wins.
if not defined MOKATA_HOOK goto :try_pinned
if not exist "%MOKATA_HOOK%" goto :try_pinned
"%MOKATA_HOOK%" %*
exit /b %ERRORLEVEL%

:try_pinned
rem 1 — the pinned runtime the plugin's MCP server recorded for THIS version.
if not defined CLAUDE_PLUGIN_DATA goto :try_python
rem 2 — a Python at mokata's floor, 3.12+ (PY-FLOOR-3.12, = pyproject's requires-python), runs the
rem packaged module. Each candidate is ASKED its version; an older Python 3 is remembered in
rem MOKATA_OLD so the last line says why nothing ran instead of "no Python".
set "MOKATA_PY="
set "MOKATA_OLD="
if defined MOKATA_PYTHON call :probe "%MOKATA_PYTHON%"
if not defined MOKATA_PY call :probe python
if not defined MOKATA_PY call :probe python3
if not defined MOKATA_PY call :probe py -3
if not defined MOKATA_PY goto :try_uvx
set "PYTHONPATH=%MOKATA_SRC_DIR%;%PYTHONPATH%"
%MOKATA_PY% -m mokata_emit %*
exit /b %ERRORLEVEL%

:probe
rem 0 = at the floor (use it) · 7 = an older Python 3 (remember it) · anything else = not a Python.
%* -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 7 if sys.version_info[0] == 3 else 1)" >nul 2>&1
if "%ERRORLEVEL%"=="0" set "MOKATA_PY=%*"
if "%ERRORLEVEL%"=="7" if not defined MOKATA_OLD set "MOKATA_OLD=%*"
exit /b 0

:try_uvx
set "MOKATA_PY=py -3"
goto :run_python

:use_env_python
set "MOKATA_PY=%MOKATA_PYTHON%"

:run_python
set "PYTHONPATH=%MOKATA_SRC_DIR%;%PYTHONPATH%"
%MOKATA_PY% -m mokata_emit %*
exit /b %ERRORLEVEL%

:try_uvx
rem 3 — no Python, but uv: the pinned package (the MCP server's environment, one download).
if not defined MOKATA_VER goto :fail
where uvx >nul 2>&1
if errorlevel 1 goto :fail
uvx --python ">=3.12" --from "mokata[full]==%MOKATA_VER%" python -m mokata_emit %*
exit /b %ERRORLEVEL%

:fail
rem 4 — nothing resolved. LOUD and non-zero: a dead gate must never look like a passing one.
if defined MOKATA_OLD echo mokata: found %MOKATA_OLD% but it is older than Python 3.12 - mokata needs Python 3.12+. 1>&2
echo mokata: hooks are NOT firing - found neither a Python 3.12+ nor uv to run mokata. Fix: install uv (https://docs.astral.sh/uv/getting-started/installation/; it fetches Python 3.12 itself) or Python 3.12+, and restart Claude Code, or run `mokata setup claude`. 1>&2
exit /b 1
