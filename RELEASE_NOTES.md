mokata **0.0.29**. Set up or upgrade with one instruction, in your repo:
`uv tool install --upgrade "mokata[full]"` (or `pipx install --force "mokata[full]"`), then
`mokata setup claude --mode full`.
Requires **Python ≥ 3.12**, and for the Claude Code integration **Claude Code ≥ 2.1.198**. The plugin
route needs **[`uv`](https://docs.astral.sh/uv/) on your PATH**.

---

## 0.0.29 — "Cancel means stopped, and long suites finish."

**⚠ Before you upgrade.** On Windows, a `statusLine`, `settings.baseline` or test-runner command
line that uses `%VAR%`/`!VAR!` expansion, `^`, a parenthesised group, `call`/`start`/`if`/`for`, or
a command host (`cmd`, `powershell`, `pwsh`, `wsl`, a script host) as the program is now refused
with the reason and the fix; every program in the line must resolve from PATH, and `mokata doctor`
lists every refused line. MCP `baseline` has no wall-clock limit: it ends on silence
(`settings.baseline.silence_seconds`, default 600), its output bound or cancel, and
`settings.mcp.test_timeout_seconds` is no longer read. `mokata doctor --fix` has no `--yes`. The
merge gate's spec checks have no age limit: they go stale when a decision they read changes.

**Cancel really stops.** Cancelling an MCP call, or Ctrl-C on the command line, ends the whole
process tree the call started on Linux, macOS and Windows; a second Ctrl-C escalates, and if the
MCP server is killed outright its processes are ended too. A cancelled write called again is
collected, never run twice. Helper commands (the self-updater, `claude --version`, the code graph's
command line, a domain gate's tool) run through the same held process tree.

**Long suites over MCP finish.** A long suite is no longer killed by the reply budget: the answer
says `running` with numeric progress, runaway output is bounded on disk, and the end is named
(silence, the output bound or cancel). `session_save` says what landed and never reports a save it
cannot read back as "nothing to save".

**Callers and Callees without a language server.** Callees now come from the code graph when no
trusted language server runs, as the 0.0.28 notes scheduled; with a trusted pyright, call hierarchy
works through mokata. pnpm and yarn global servers are trusted only from the directory that package
manager reports, owned by you.

**Faster at scale, lighter hooks.** TypeScript ripple's first page went from about 201 s to about
1.1 s (name-only matches shown as *pending*); the first code-search hit from about 1.64 s to about
0.58 s. On macOS the prompt hook no longer runs `ps` (about 43 ms less per prompt), and the resume
answer is served from the store (about 106 ms less on `/clear`).

**🔒 Security.** File access is decided by what is on disk, not by how a path is spelled: reads and
writes held open by descriptor cannot be redirected by a symlink, rename or `mv .git` made while
they run. `mokata doctor --fix` is a gated plan that rotates a weak hook token. Project tag names are
no longer stripped as secrets. The IDE pairing token's visibility to other accounts is disclosed in
`docs/security.md`.

**Full mode finishes (#86), and hints match your install.** `mokata setup` in full mode ends
"everything wired" or "wired — one command left" with that exact install line; the embedding model
is downloaded only with your consent. `code-review-graph` is pinned to `2.4.0` and `mcp` stays below
2 on every install route. Every command hint names `/mokata:<name>` on the plugin route and `/<name>`
on the command-line route.

## Re-scheduled

- **The IDE pairing token off the command line** — the token will stop travelling in the launcher's
  command line, where another account on the same machine can read it with `ps`.
  Scheduled for **0.0.30** (tracked as `IDE-PAIR-TOKEN-IN-ARGV`).
  Until then, on a shared machine close the IDE when you are not using it.

## Known limitations
- **Windows test runs.** A test command that resolves only to a batch file (`mvn.cmd`, `gradlew.bat`,
  an explicit `.cmd`/`.bat`, or one spelled with a trailing dot such as `run.cmd.`) or to a command
  host (`cmd.exe`, `powershell.exe`) cannot be run by mokata: it is refused before anything starts,
  the IDE re-run is disabled and the gated merge is not possible for that project, because the merge
  needs mokata's own suite result and merge-override does not substitute for it. Set the test command
  to a real executable (for example `node <runner.js>`), or merge that project from macOS, Linux or
  WSL.
- **Windows batch files found on PATH.** A `.bat` or `.cmd` on PATH that a status-line or baseline
  line names is run by its absolute path, but cmd reads the batch's own body with your repository as
  the current folder, so a command inside it can be found in the repository. A PATH entry inside the
  repository (a repo-local virtual environment, for example) is now resolved like any other.
- **Stopping a suite over MCP.** There is no separate stop operation: a told-running suite that keeps
  printing, or any suite with `silence_seconds` set to `0`, is stopped by cancelling the request (or
  re-calling and cancelling it) or by ending the server. A command that starts its own session
  (`setsid`) escapes the process-group end on POSIX, though ending the server still reaches it.
  An unattended `graph adopt --yes` has no time limit.
- **A changed working tree is detected by HEAD, status, size and modification time.** An edit to a
  gitignored file, or a same-size edit with its modification time restored, is not seen, so a
  re-call can be joined to the earlier run.
- **Spec checks in team mode.** Any teammate's decision write makes open spec-check results stale;
  re-run them before merging.
- **MCP.** The MCP server clears `GIT_ASKPASS`, so an https credential that comes only from an
  askpass helper (for example VS Code's) is not used by `vault_push` or `worktree_merge`. The
  `remember` tool reports a bad date in an intention (for example month 13) as "an intention needs
  a due condition" rather than as a bad date.
- **Plugin route:** for a short time during a release the marketplace can offer a version that PyPI
  does not serve yet. `mokata doctor --plugin` says so; wait and retry.
- **File privacy.** On a disk without access control (FAT, exFAT, some network mounts) a file the
  disk reports as yours is accepted, and the files are only as private as the disk. The IDE's
  pairing token is passed on the command line, where other processes, including those of another
  account on macOS and Linux, can see it (see Re-scheduled). Files under `.mokata/runs/` are written
  with your normal file permissions (your umask); they hold run state, not tokens. ReFS file ids,
  UNC and NFS spellings of a repository path, and Windows reparse points at file creation are not
  verified (no test machine).
- **Containment.** A linked worktree's or submodule's git directory outside the repository is
  judged a moment later than the repository's own stores, so a swap at exactly that moment can still
  be seen. Bind mounts inside the repository, and a hard link inside it to a file outside, are that
  file; mokata cannot tell them apart. Some IDE file reads do not yet go through the contained opener.
- **Hook timings.** The first hook after an install or upgrade pays a one-time compile. On a very
  large progress log (8 MiB), the session-start hook can run above its 150 ms target. Hook limits on
  Linux and Windows are the macOS measurements times 2, not measured on those systems.
- **Redaction.** In rare shapes a few bytes of a private key's last line can be left (a 1-3
  character last line, a key written with two different comment styles, a line split around a
  variable), and one line right after a key with no header can be hidden as well. PGP and PKCS#12
  bodies are caught by their marker or file suffix only. A project tag whose only lowercase piece is
  a short function word or a hex-looking word is not treated as a tag.
- **Proof.** On Windows, a symlink written with `/` and `..` never proves, and a test reached through
  a junction never proves at HEAD. Hard links and bind mounts are the same bytes under two names;
  mokata cannot tell them apart.
- **Callers and Callees.** Callees from the code graph miss calls made in a decorator or a default
  argument, and a recursive function does not list itself. Without a language server the first
  answer waits for one index of the repository (shared, with progress and Cancel); two such waits
  can hold up a third question until one is cancelled.
- **Language server:** trust is not bound to the server's command line, and the trust status says
  so. A pnpm `global-dir` set in `.npmrc`, or a yarn `global-folder` set only in `.yarnrc`, is not
  read, so that server is not found (mokata says so and runs nothing). Hover, definition and
  references have no silence limit; they wait behind their job's Cancel.
- **Very large TypeScript repositories:** the first ripple page is fast, but the fully resolved
  picture (Continue) still takes about 195 seconds on the measured tree.
- **Review window.** The image pixel difference is shown but feeds no gate. About 51 controls show
  the browser's default focus ring instead of mokata's, and the editor tabs have no arrow-key
  movement. A table count that fails with no reason prints `not counted: undefined`.
- **Setup.** `mokata unsetup` reformats a `settings.json` that uses an inline array or object
  in a layout Python's JSON writer cannot reproduce (the content is unchanged). `.code-review-graph/`
  stays after unwire (it is data, not wiring). A plugin installed before any Claude session ran is
  not detected, so the wizard asks as before. With the embedding model not yet downloaded, the
  notice says the engine could not be loaded.
- **Command hints.** One message (pgvector unavailable during a memory migration) still suggests
  `mokata team init --vector`; `mokata team init` has no `--vector` option. Some messages print the
  plugin form `/mokata:<name>` where a shorter form would also work; it is always valid.
- **`mokata index` is Python only.** Impact and blast radius from it do not cover Go, TypeScript,
  Rust or Java; the command says so.
- **The `.git/hooks` and `.git/config` gate has limits** (described in `docs/security.md`). It does
  not see a computed `cd` (`cd "$D"`, `cd -`, `popd`), Bash mokata cannot parse (variables, `eval`,
  heredoc-fed interpreters, `find -exec`, `xargs`), or commands that change config as a side effect
  (`git remote add`, `git push -u`, `git branch -u`); a hard link to `.git/config` is not seen on
  FAT, exFAT or some SMB shares. **An agent with a shell can approve its own proposal with
  `mokata approve <id> --yes`**: the gate makes the write visible, it does not stop an agent that has
  Bash. Writes to `.gitattributes`, `.git/info/attributes` and `.gitmodules` are not gated, nor are
  global git config keys other than `core.hooksPath` (for example `core.sshCommand`).
- **The gated merge commit is unsigned** (`mokata worktree merge` runs no signing program).
- **Team audit.** The shared team audit log carries absolute local file paths from `init`, so a
  teammate sees your home and folder names; on macOS a repository under `$TMPDIR` can make the
  publish refuse (a random folder name is scored as a token).
- **CSV:** a single record over 64 MiB is refused; a keyed comparison of 1,000,000 rows in reverse
  order takes about 71 seconds (measured for 0.0.27); a keyed comparison matches keys exactly, so
  case matters, and there is no regex filter.
- **On a case-sensitive disk a directory named `.Git` is refused.**
- **Not in this release, and not scheduled:** hunk-level stage and discard, PR review submit and
  other git hosts.
- **Carried over from earlier releases, not re-verified for this release:** the Windows IDE terminal
  and the `.cmd` hook launcher are not verified on a real Windows desktop; Windows 8.3 short names
  and alternate streams are not verified for the IDE's file reads on a real Windows desktop; on
  Windows a spec proposal can wait ~20 s while another mokata process writes the same run's state;
  follow mode is unproven against a real `claude` session; runs created before 0.0.26 keep the
  previous re-entry behaviour until a new run is registered; a restarted MCP server is recognised
  only within two launcher processes of Claude's hooks; the clean-machine install workflow has not
  run on a release runner.
