mokata **0.0.28**. Set up or upgrade with one instruction, in your repo:
`uv tool install --upgrade "mokata[full]"` (or `pipx install --force "mokata[full]"`), then
`mokata setup claude --mode full`.
Requires **Python ≥ 3.12**, and for the Claude Code integration **Claude Code ≥ 2.1.198**. The plugin
route needs **[`uv`](https://docs.astral.sh/uv/) on your PATH**.

---

## 0.0.28 — "Fewer hangs, fewer leaks, and Windows behaves."

**⚠ Before you upgrade.** MCP callers: the `approve`, `confirm` and `mtype` arguments are gone —
consent is the `proposal_id` alone, and `review_record` takes `outcome="passed"` or
`outcome="failed"`. MCP calls are now checked before they run: an unknown argument name, a missing
required argument or a value outside the allowed set is refused with a reply that names the fix
(before, an unknown name was silently dropped). Test proofs recorded before 0.0.28 must be re-run,
red then green. `mokata baseline` has a new state, **NOT RUN** (exit 1), for a suite that timed out,
could not start or could not be held. `mokata worktree merge` now also needs the spec checks at the
worktree's head. References moved from the Language panel to Find Usages.

**Windows: processes are held and ended.** Test re-runs, the gated merge's full suite,
`mokata baseline`, the status-line wrapper, the code-graph version probe and the language server
start through one place; on Windows each runs inside a job that is ended as a whole, so a leftover
grandchild can no longer keep a run going or hold a worktree open. If Windows will not let mokata
hold the tree, mokata refuses and says why — it never runs it unheld. A test runner inside your
repository is never picked from PATH by name, and `.cmd`, `.bat` and script runners are refused
before anything runs.

**MCP: no more stalls, and the first call works.** The server no longer hangs when a child reads its
input, when many slow calls are in flight, or when the client does not read its log. Every tool
parameter is described, typed and marked required where it is required; an uninitialised repository
gets one reply naming `mokata init`. `mokata mcp docs` renders a reference of every tool, and
`mokata doctor --plugin` names a plugin pin that does not resolve yet.

**🔒 Private files and redaction.** The files that hold tokens and trust are created private from the
first instant on Windows, macOS and Linux and checked again when read; `mokata doctor` reports the IDE server,
bridge and trust records and `settings.local.json`, and `mokata doctor --fix` re-protects your own weak ones. A PATH entry into your repository in
another letter case no longer lets a planted tool run on macOS. Private keys with a mangled `BEGIN`
line, or no header at all, are now redacted from session context, Copy with Context and grounded
explanations; `.ppk` files are treated as sensitive.

**Proof and gates.** A test proof is bound to the file that ran: re-pointing a symlink, even at a
byte-identical file, no longer keeps it. The gated merge needs the spec checks (spec awareness and
spec drift) at the worktree's head; "nothing to check" is allowed and recorded as NOT CHECKED.
`mokata worktree test` and the IDE's Run suite run the suite and the spec checks as one job you can
cancel. The "where you left off" line is the same next step `mokata suggest` and the IDE show.

**Callers and Callees.** Find Usages now has Usages, Callers and Callees tabs — an expandable tree
from the language server once you trust the workspace, with callers from the code graph without one.
"Find Callers" and "Find Callees" are in the palette and the Code right-click menu. This is the call
hierarchy the 0.0.27 notes moved to 0.0.28, now shipped.

**Fixed.** Hooks are faster inside Claude Code: compiled bytecode is now kept in your user cache
(`$XDG_CACHE_HOME/mokata/pycache`, or `~/.cache/mokata/pycache`), never beside the installed code.
In the review window, a table count that cannot finish says why, the Review panel says "not run" and
"cancelled" instead of FAILED, Split view copy and cite follow the selection exactly in Firefox and
Chromium, and the IDE no longer deadlocks on its first requests. On macOS with the system SQLite, a
freshly rebuilt code graph is no longer reported as incomplete.

## Re-scheduled

- **Callees without a language server** — the Callees tab will fall back to the code graph when no
  language server is running, as Callers already does.
  Scheduled for **0.0.29** (tracked as `LSP-CALLS-GRAPH-CALLEES`).
  Until then the Callees tab needs a trusted language server.

## Known limitations
- **Windows test runs.** A test command that resolves only to a batch file (`mvn.cmd`, `gradlew.bat`,
  an explicit `.cmd`/`.bat`, or one spelled with a trailing dot such as `run.cmd.`) cannot be run by
  mokata: it is refused before anything starts, the IDE re-run is disabled and the gated merge is not
  possible for that project, because the merge needs mokata's own suite result and merge-override
  does not substitute for it. Set the test command to a real executable (for example
  `node <runner.js>`), or merge that project from macOS, Linux or WSL. A test runner set explicitly
  to `cmd.exe` or `powershell.exe` is a real program and still runs, and a baseline command's first
  word can be found in the repository folder by `cmd.exe`.
- **Windows helper commands are not yet held.** The self-updater, `claude --version`,
  `mokata --version` during install, the code graph's command line and a domain gate's own tool do
  not yet run through the held process tree, so a timeout there can wait on a leftover child.
- **Ctrl-C in `mokata worktree test`.** Ctrl-C cancels at the next checkpoint, not at once: a second
  Ctrl-C does nothing until then, and one that arrives after the last checkpoint is ignored, so the
  command finishes and records its result. Cancel in the IDE stops it immediately.
- **Spec checks in the merge gate.** A decision recorded after the spec checks ran (within their
  24-hour window) is not part of their verdict; re-run them before merging. Setting
  `review.verdict_max_age_hours` to 0 or less turns the age limit off.
- **`mokata baseline`.** A test command containing a NUL byte stops with an error instead of
  reporting NOT RUN.
- **MCP.** Cancelling a long `ci_check` from an MCP client does not stop its work; Ctrl-C on the
  command line and Cancel in the IDE do. The MCP server clears `GIT_ASKPASS`, so an https credential
  that comes only from an askpass helper (for example VS Code's) is not used by `vault_push` or
  `worktree_merge`; on Windows the variable is unset rather than empty, so a helper may still be
  asked. The `remember` tool reports a bad date in an intention (for example month 13) as "an
  intention needs a due condition" rather than as a bad date.
- **Plugin route:** for a short time during a release the marketplace can offer a version that PyPI
  does not serve yet. `mokata doctor --plugin` says so; wait and retry.
- **File privacy.** `mokata doctor --fix` re-protects a token file that was readable but keeps the
  same token; anything that read it before still has it. On a disk without access control (FAT,
  exFAT, some network mounts) a file the disk reports as yours is accepted, and the files are only as
  private as the disk. The IDE's pairing token is passed on the command line, where other processes
  of the same user can see it. Files under `.mokata/runs/` are now written with your normal file
  permissions (your umask), not owner-only as before; they hold run state, not tokens.
- **Hook bytecode cache.** A relative `XDG_CACHE_HOME` makes the hooks write their compiled bytecode
  under the current folder, which is usually your project; set it to an absolute path or leave it
  unset. The first hook after an install or upgrade pays a one-time compile, and the caches of older
  versions are not cleaned up.
- **Redaction.** In rare shapes a few bytes of a private key's last line can be left (a 1-3
  character last line, a key written with two different comment styles, a line split around a
  variable), and one line right after a key with no header can be hidden as well. PGP and PKCS#12
  bodies are caught by their marker or file suffix only.
- **Proof.** On Windows, a symlink written with `/` and `..` never proves, and a test reached through
  a junction never proves at HEAD. Hard links and bind mounts are the same bytes under two names;
  mokata cannot tell them apart.
- **Callers and Callees.** Without a language server the Callees tab is empty. The first answer in a
  repository that holds a very large Python file can take a while as the code graph indexes it (it
  shows progress and can be cancelled).
- **Review window.** Citing a selection in Unified view walks the whole table, so it slows down on
  very large diffs. The image pixel difference is shown but feeds no gate.
- **Command hints.** Some messages from the command line suggest commands that do not exist
  (`mokata uninstall`, `mokata upgrade`, `mokata ship`, `mokata team init --vector`). Other
  commands that mokata prints still use the plugin form `/mokata:<name>` (`mokata suggest`, hints,
  skill text, the IDE's empty Sessions screen); after `mokata init` type `/<name>` instead (or
  `/mokata-<name>` for review, init, doctor, resume and bug).
- **`mokata index` is Python only.** Impact and blast radius from it do not cover Go, TypeScript,
  Rust or Java; the command says so. The skipped count covers only javascript, go, rust and java,
  and `.pyi` / `.PY` files are counted as skipped.
- **Very large TypeScript repositories are slow to ripple:** about 74 seconds for a function with
  391 callers in a 15M-line tree (a visible job with Cancel); the first search hit takes about 2.2
  seconds. Measured for 0.0.27; not re-measured for this release.
- **The `.git/hooks` and `.git/config` gate has limits.** It does not see a computed `cd`
  (`cd "$D"`, `cd -`, `popd`), Bash mokata cannot parse (variables, `eval`, heredoc-fed
  interpreters, `find -exec`, `xargs`), or commands that change config as a side effect
  (`git remote add`, `git push -u`, `git branch -u`); a hard link to `.git/config` is not seen on
  FAT, exFAT or some SMB shares. **An agent with a shell can approve its own proposal with
  `mokata approve <id> --yes`**: the gate makes the write visible, it does not stop an agent that has
  Bash. Writes to `.gitattributes`, `.git/info/attributes` and `.gitmodules` are not gated. The gate
  is not yet described in the product docs.
- **The gated merge commit is unsigned** (`mokata worktree merge` runs no signing program).
- **Language server:** pnpm and yarn global installs on Windows are unverified on a real install;
  trust is not bound to the server's command line, and the trust status says so.
- **CSV:** a single record over 64 MiB is refused; a keyed comparison of 1,000,000 rows in reverse
  order takes about 71 seconds (measured for 0.0.27); a keyed comparison matches keys exactly, so
  case matters, and there is no regex filter.
- **A path-swap race can still serve a swapped file** (2 in 1.2 million attempts in a test); Windows
  8.3 short names and alternate streams are unverified for the IDE's file reads on a real Windows
  machine. On a case-sensitive disk a directory named `.Git` is refused.
- **Not in this release, and not scheduled:** hunk-level stage and discard, PR review submit and
  other git hosts.
- **Carried over from earlier releases, not re-verified for this release:** the Windows IDE terminal
  and the `.cmd` hook launcher are not verified on a real Windows desktop; on Windows a spec proposal
  can wait ~20 s while another mokata process writes the same run's state; follow mode is unproven
  against a real `claude` session; runs created before 0.0.26 keep the previous re-entry behaviour
  until a new run is registered; a restarted MCP server is recognised only within two launcher
  processes of Claude's hooks; the clean-machine install workflow has not run on a release runner.
