# Review what your agent wrote (`mokata ide`)

`mokata ide` opens the **review window**: a read-only page on your own machine that shows exactly
what changed, who changed it, and when. It is built for supervising agent work — checking a
session's writes before you approve or ship them — not for editing.

![The review window on its sample dataset](../assets/review-window.png)

## Open it

From inside a Claude Code session, type:

```text
/review-window
```

mokata's prompt hook opens the window in your browser and focuses it on **that session** — its
run, the files it wrote, its timeline. If a window is already open for the repository, it is
re-focused instead of a second one being started. Running several sessions in parallel? Each one
is its own row; type the command in a session to point the window at it.

From a terminal:

```bash
mokata ide                 # open (or re-focus) the window for this repository
mokata ide --session ID    # focus a particular session
mokata ide --demo          # a clearly labelled SAMPLE dataset, to look around first
```

The window needs the `[ide]` extra, which is normally already present with mokata's MCP SDK. If it
is missing, mokata prints the one line to install it: `pip install "mokata[ide]"`.

## What it shows

- **Live changes** — the working tree against `HEAD`, uncommitted and untracked files included,
  updating as sessions write.
- **What a session wrote** — for every file a session touched, its content before the session's
  first write against its latest one. This is "what is coded by this session", even when several
  sessions share the repository.
- **Timeline** — every write a session made, from mokata's own snapshot of the file before and
  after it. Pick one write to see it, or two to diff any two moments.
- **Compare branches** — `base...head`, the review a pull request shows.
- **Pull requests** — paste a GitHub PR URL. It is fetched with *your* git login; mokata stores no
  token, and it refuses a PR whose repository does not match your `origin`.

Every section says what will appear there when it is empty, so a fresh repository is still
readable.

## How it is laid out

Three tabs across the top bar, each a screen with its views as sub-tabs:

- **Review** — **Diff** (the changed files, one at a time; the inspector beside it shows a
  changed symbol's callers, tests and blast radius, with **Expand graph** to see it drawn — Back or
  Esc returns to the diff), **Proof** (acceptance criterion → test → code, full width) and
  **Explain**. Marking the last file seen ends the review and offers the next steps: Proof, then
  the worktree's gated merge.
- **Sessions** — every session, its card, and its **Timeline**, **Plan & spec** and **Worktree**.
- **Code** — the repository's files, search and outline, with references and history beside the
  code.

What waits on you (an approval, a blocked gate) is one bar under the top bar, on every tab. The
**health** chip opens what is missing or degraded; on a first run it says **Get started** and lists
what each capability needs. `g r` / `g s` / `g c` switch tabs and `?` lists every key; the
one-character shortcuts can be turned off from that list.

## What it never does

- **It never writes to your repository.** The only network call is a PR fetch you ask for.
- **It listens on `127.0.0.1` only.** A one-time pairing link becomes a cookie; requests with the
  wrong host or origin are refused, and nothing outside the repository is read — not through a
  symlink, a device file or a pipe.
- **It does not outlive its use.** Started from a session, it exits after 30 idle minutes;
  `mokata ide --foreground` serves until you press Ctrl-C.

The per-session features need Claude Code 2.1.198 or newer; `mokata doctor` tells you if yours is
older.

## Related

- [Watch a run (lanes + this window's Sessions screen)](watch-a-run.md) — the run's progress, as it happens.
- [CLI reference: `mokata ide`](../reference/cli.md) — every flag.
