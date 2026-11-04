mokata **0.0.27**. Set up or upgrade with one instruction, in your repo:
`uv tool install --upgrade "mokata[full]"` (or `pipx install --force "mokata[full]"`), then
`mokata setup claude --mode full`.
Requires **Python ≥ 3.12**, and for the Claude Code integration **Claude Code ≥ 2.1.198**. The plugin
route needs **[`uv`](https://docs.astral.sh/uv/) on your PATH**.

---

## 0.0.27 — "Read a change your way, see it proven, merge it with evidence."

**⚠ Before you upgrade.** Specs without a `tests[]` map must be re-emitted to be graded by the PR
check; there is no migration. `mokata ci-check` now exits **2** when it cannot resolve the base to
compare against (for example a shallow checkout with `fetch-depth: 1`) — a report-only job on a
shallow checkout fails until it fetches the base. Removed: the HTML `mokata watch` dashboard, Vim
navigation, the change treemap, the Replay scrubber, the rail and status footer, and Copy Ref (use
Copy with Context).

**🔒 Safer by default.** A visible write to `.git/hooks` or `.git/config` now asks for your approval
— this makes the write visible; it is not a wall (see below). Well-formed private keys are redacted
whole in the context the IDE sends to a session (0.0.26 could leak a whole key body). `.git/` and
`.mokata/` are refused on every IDE read route in any spelling of the name. `mokata ci-check --base`
refuses a value that starts with `-`.

**Read a change your way.** In `mokata ide`: split or unified diffs with the changed words marked;
image diffs (side by side, swipe, onion skin, pixel difference); CSV and TSV as a table, and a
comparison of two versions by row or by key; **Copy with Context** (secret-scanned) and **Find
Usages**; dark, light, two high-contrast themes or follow your OS; an explorer that copes with very
large repositories. `mokata explain live` prints the Claude Code command for a change explainer —
Claude writes it on your own login, and mokata keeps only the claims that cite a fact or a line range.

**Read-only code intelligence from a language server** — go-to-definition, references, hover and
document symbols — only after you trust the workspace (`mokata ide --trust`). This is part of what the
0.0.26 notes promised for 0.0.27; call hierarchy, the other part, is re-scheduled (see below).

**See that a change is proven.** The Proof screen shows each acceptance criterion, the tests that
prove it and the code they cover. `mokata suggest` names the recorded fact behind each hint. The PR
check can carry the run's gate evidence and the committed run record, says "not checked" when it
cannot grade a leg (never a pass), and works on a fresh repository.

**Merge with evidence.** `mokata worktree merge` merges a session's worktree only with full-suite
evidence at the head, your approval and a compare-and-set commit point; `--recover` restores a clean
state if it is interrupted. Needs git 2.40 or newer.

**Three tabs.** The window is now Review · Sessions · Code, with one "waiting on you" bar.
`mokata watch --open` opens the IDE on Sessions.

**Honest about the index.** `mokata index` and `index_status` now say which languages are indexed
and how many files were skipped. It indexes Python only.

## Re-scheduled

- **Call hierarchy from a language server** — published in the 0.0.26 notes as part of the read-only
  language server due in 0.0.27, and not in this release.
  It is re-scheduled to **0.0.28** (tracked as `ID-LSP-CALL-HIERARCHY`). Until then callers come from
  the code graph, which covers Python only.

## Known limitations
- **`mokata index` is Python only.** Impact and blast radius from it do not cover Go, TypeScript,
  Rust or Java; the command says so. The skipped count covers only javascript, go, rust and java, and `.pyi` / `.PY` files are counted as
  skipped.
- **Very large TypeScript repositories are slow to ripple:** about 74 seconds for a function with 391
  callers in a 15M-line tree (a visible job with Cancel); the first search hit takes about 2.2 s.
- **The `.git/hooks` and `.git/config` gate has limits.** It does not see a computed `cd` (`cd "$D"`, `cd -`, `popd`), Bash
  mokata cannot parse (variables, `eval`, heredoc-fed interpreters, `find -exec`, `xargs`), or
  commands that change config as a side effect (`git remote add`, `git push -u`, `git branch -u`);
  a hard link to `.git/config` is not seen on FAT, exFAT or some SMB shares. **An agent with a shell
  can approve its own proposal with `mokata approve <id> --yes`** — the gate makes the write visible;
  it does not stop an agent that has Bash. Writes to `.gitattributes`, `.git/info/attributes` and
  `.gitmodules` are not gated. The gate is not yet described in the product docs.
- **The PR check's spec leg is not part of the merge gate** (`mokata worktree merge` does not run it), and the merge commit is unsigned unless
  signing needs no repository-chosen code.
- **Some mangled private-key shapes are still not redacted** in session context (a marker with no
  trailing dashes, one or two dashes, a cut BEGIN line, a Unicode dash, or a key built by joining
  strings; a key split across lines with no marker).
- **Some printed commands still use the plugin form `/mokata:<name>`** (`mokata suggest`, hints, skill
  text, the IDE's empty Sessions screen). After `mokata init` type `/<name>` instead (or `/mokata-<name>` for review, init, doctor, resume
  and bug).
- **The image pixel difference feeds no gate.**
- **Language server:** pnpm and yarn global installs on Windows are unverified; trust is not bound to
  the server's command line.
- **CSV:** a single record over 64 MiB is refused; a keyed comparison of 1,000,000 rows in reverse
  order takes about 71 s; there is no case-insensitive ordering or regex filter.
- **A path-swap race can still serve a swapped file** (2 in 1.2 million attempts in a test); Windows
  8.3 short names and alternate streams are unverified on a real Windows machine. On a case-sensitive
  disk a directory named `.Git` is refused.
- **Not in this release, and not scheduled:** hunk-level stage and discard, PR review submit and
  other git hosts.
- **Carried over from 0.0.26, not re-verified:** this release has not yet run on the macOS and
  Windows CI legs, and the Windows IDE terminal and the `.cmd` hook launcher are not verified on a
  real Windows desktop; hook timings are measured
  on Linux only; on Windows a spec proposal can wait ~20 s while another mokata process writes the
  same run's state; follow mode is unproven against a real `claude` session; runs created before
  0.0.26 keep the previous re-entry behaviour until a new run is registered; a restarted MCP server
  is recognised only within two launcher processes of Claude's hooks; the clean-machine install
  workflow has not run on a release runner.
