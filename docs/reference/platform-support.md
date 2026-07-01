# Platform support (Windows · macOS · Linux)

mokata is a **first-class citizen on Windows, macOS, and Linux**. The core is pure-Python and
dependency-free, paths are built with `os.path`/`pathlib` (never a hard-coded `/`), temp files
go through `tempfile` (no `/tmp` literal), and file I/O is UTF-8 everywhere — so emojis and
non-ASCII content write correctly even under Windows' legacy code page.

## What's covered

| Area | Cross-platform behaviour |
|---|---|
| **Hooks** | Wired as the `mokata-hook` **console entry point** (Stage 53b) — a PATH-resolved executable, the same mechanism as the `mokata-mcp` server. No bare `python3`, **no `sh launch.sh`**. Resolves identically on Windows, GUI-launched macOS, and minimal-PATH shells. |
| **Statusline** | The Claude Code `statusLine` is the same `mokata-hook statusline` console entry — no shell dependency. |
| **Paths & separators** | All state, `temp_local/`, bundle, and dashboard paths use `os.path.join`; nothing assumes `/`. |
| **Portable bundles** | The machine-path-free invariant strips **Windows** absolute paths (`C:\…`, UNC `\\host\…`) and POSIX paths alike — a bundle built on one OS resumes cleanly on another. |
| **Usernames** | Provenance/author fields resolve via `getpass.getuser()`, which reads `%USERNAME%` on Windows (not just `$USER`). |
| **Encoding / line endings** | Files are read/written as UTF-8; line-oriented parsing uses `splitlines()`, tolerant of `LF` and `CRLF`. |

`launch.sh` remains a **POSIX last-resort fallback only** — used solely by a pure
plugin-without-pip install where the `mokata-hook` console script isn't present. The normal,
pip-installed path never touches it.

## CI coverage

Every push and PR runs the **full unit + integration suite on `ubuntu-latest`,
`windows-latest`, and `macos-latest`**, across each supported Python version and both the
`jsonschema`-present and `jsonschema`-absent legs. A regression on any OS fails the build.

## Manual-verification leg

Like the [live-DB integration leg](../how-to/configure-storage-backends.md), behaviour that
requires a *real* Windows process (e.g. an interactive Claude Code session launching a hook on
Windows) is **proven by the Windows CI matrix leg**, not on a contributor's local box. Local
test runs assert the same behaviour OS-agnostically (path joins, separator-agnostic basename,
the machine-path-free bundle on Windows-style paths, the `mokata-hook` command shape) so the
suite is green on whatever OS you develop on, and the matrix confirms the real Windows run.
