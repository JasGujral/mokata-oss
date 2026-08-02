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
rem Same ladder, same contract:
rem   0 %MOKATA_HOOK%  ->  1 mokata-hook on PATH  ->  2/3 a Python 3 running the packaged
rem   module (`-m mokata.hook_cli`, this checkout's src\ on PYTHONPATH)  ->  4 exit 1, loud.
rem
rem Exit codes are the subcommand's own (`exit /b %ERRORLEVEL%`) — secret-guard's exit 2 still
rem BLOCKS. Exit 1 is the reserved MISCONFIGURATION code; this shim never invents an exit 2.
rem
rem Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.

setlocal EnableExtensions

if "%~1"=="" goto :fail

rem This shim lives at <plugin>\src\mokata\hooks\ — so ..\.. is the packaged source root.
set "MOKATA_SRC_DIR=%~dp0..\.."

rem 0 — an explicit override wins.
if not defined MOKATA_HOOK goto :try_path
if not exist "%MOKATA_HOOK%" goto :try_path
"%MOKATA_HOOK%" %*
exit /b %ERRORLEVEL%

:try_path
rem 1 — the console entry point on PATH (identical to the pre-HOOK-RESOLVE behaviour).
where mokata-hook >nul 2>&1
if errorlevel 1 goto :try_python
mokata-hook %*
exit /b %ERRORLEVEL%

:try_python
rem 2/3 — resolve an interpreter and run the packaged module.
if defined MOKATA_PYTHON goto :use_env_python
where python >nul 2>&1
if errorlevel 1 goto :try_python3
set "MOKATA_PY=python"
goto :run_python

:try_python3
where python3 >nul 2>&1
if errorlevel 1 goto :try_py_launcher
set "MOKATA_PY=python3"
goto :run_python

:try_py_launcher
where py >nul 2>&1
if errorlevel 1 goto :fail
set "MOKATA_PY=py -3"
goto :run_python

:use_env_python
set "MOKATA_PY=%MOKATA_PYTHON%"

:run_python
set "PYTHONPATH=%MOKATA_SRC_DIR%;%PYTHONPATH%"
%MOKATA_PY% -m mokata.hook_cli %*
exit /b %ERRORLEVEL%

:fail
rem 4 — nothing resolved. LOUD and non-zero: a dead gate must never look like a passing one.
echo mokata: hooks are NOT firing - could not resolve `mokata-hook` or a Python 3 to run it. Fix: run `mokata setup claude`. 1>&2
exit /b 1
