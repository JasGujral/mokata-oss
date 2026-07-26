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

## Harness support — only Claude Code wires hooks

Cross-platform is not the same question as cross-*harness*. mokata's hooks are a **Claude Code**
capability: `claude` is the only harness that declares `hooks` in the
[capability matrix](cli.md#mokata-harness-name) (`mokata harness`).

| Harness | commands | hooks | context injection | subagents |
|---|:--:|:--:|:--:|:--:|
| `claude` | ✓ | **✓** | ✓ | ✓ |
| `cowork` | ✓ | — | ✓ | ✓ |
| `codex` · `cursor` · `copilot` · `windsurf` · `gemini` | ✓ | — | ✓ | — |
| `aider` | — | — | ✓ | — |

**What that means concretely:** on every harness *except* Claude Code the `gate-guard` is never
wired, so the **run-state gates** (`approach-approval`, `spec-persisted`,
`no-code-without-failing-test`, `spec-scope`) **enforce nothing** there — they degrade with a clear message rather than pretend. The `secret-guard`
is the same hook mechanism, so it too is only enforced where `hooks` is declared; mokata's other
secret layers (the gated CLI/MCP write path) still hard-block. The engine itself is
harness-agnostic: a missing capability degrades clearly, never a silent no-op of a gate.

## CI coverage

Every push and PR runs the **full unit + integration suite on `ubuntu-latest` and
`windows-latest`**, on Python 3.12, across both the `jsonschema`-present and
`jsonschema`-absent legs. (macOS runners bill at 10× and are dropped from the matrix; the
package floor stays Python ≥ 3.10.) A regression on either OS fails the build.

## Manual-verification leg

Like the [live-DB integration leg](../how-to/configure-storage-backends.md), behaviour that
requires a *real* Windows process (e.g. an interactive Claude Code session launching a hook on
Windows) is **proven by the Windows CI matrix leg**, not on a contributor's local box. Local
test runs assert the same behaviour OS-agnostically (path joins, separator-agnostic basename,
the machine-path-free bundle on Windows-style paths, the `mokata-hook` command shape) so the
suite is green on whatever OS you develop on, and the matrix confirms the real Windows run.
