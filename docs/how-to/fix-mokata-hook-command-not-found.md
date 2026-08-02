# `mokata-hook: command not found`

If your terminal, your agent's hook output, or `mokata doctor` shows any of these:

```
mokata-hook: command not found
/bin/sh: mokata-hook: command not found
sh: 1: mokata-hook: No such file or directory
```

…then **mokata's gates are not running.** This page is the fix.

## What it means

mokata's guardrails (secret-guard, gate-guard, the SessionStart briefing, dirty-track) are
[Claude Code hooks](use-without-plugin.md). Each is a line in `.claude/settings.json` naming a
command to launch.

Your settings file names **`mokata-hook`** as a bare command. Claude Code launches hooks with a
minimal environment — a GUI-launched app has no shell profile, so `PATH` does not include the
directory pip put `mokata-hook` in. The name does not resolve.

**And Claude Code drops a hook whose command does not resolve, silently.** No error dialog, no
warning banner, no red text. From the outside, a working seatbelt and no seatbelt at all look
exactly the same — which is why this is worth fixing the moment you see the string.

So: every write goes unscanned for secrets, and every run-state gate is off. Nothing is broken
in a way you can see. That is the problem.

## The fix

```bash
mokata setup claude
```

That is the whole remedy. `setup` rewrites the hook entries to the **absolute path** of the
`mokata-hook` executable next to the Python that has mokata installed, and wires them in the
shell-free *exec form* — a shape no shell parses, so no `PATH`, no quoting rule, and no
missing Git Bash can break it again.

It previews every change and asks before writing. Nothing is written if you say no.

Then **restart Claude Code** so it reloads `settings.json`.

## Verify it

```bash
mokata doctor --wiring
```

Expect:

```
✓ hooks: wired, launchable, and current.
```

`mokata doctor --wiring` checks exactly two things and nothing else: does every wired hook
command resolve, and is the wiring the wiring this version of mokata writes. It exits non-zero
if either answer is no, so it also works as a CI or post-upgrade check. It does not need an
initialized repo — you can run it the second after a `pip install`.

To prove the gate is really live rather than merely wired, ask your agent to write a file
containing something shaped like an AWS key. secret-guard blocks it outright.

## Why it happened

Almost always one of these:

- **You upgraded mokata without re-wiring.** `pip install -U mokata` replaces the package; it
  does not touch `.claude/settings.json`. If the old wiring pointed at a path that moved — a
  rebuilt virtualenv, a `pipx` reinstall, a new Python minor version — the command it names is
  now gone. Modern `mokata upgrade` finishes this for you (see
  [the upgrade runbook](which-setup-command.md#upgrading-mokata)); a hand-run `pip install -U`
  does not.
- **You hand-edited `settings.json`,** or copied a hook line from an older doc or blog post that
  used the bare name.
- **You installed mokata into one environment and run Claude Code against another** — for
  example `pip install --user` while Claude Code launches a different interpreter. Run
  `mokata version` to see which install you are actually on.
- **You moved or renamed the project directory** after wiring at project scope.

## If `mokata setup claude` itself is not found

Then mokata is not installed on the `PATH` of the shell you are typing in, which is a different
problem with a different fix:

```bash
python3 -m mokata setup claude     # run it through the interpreter that has mokata
```

If that also fails, mokata is not installed in that interpreter — see
[Install mokata](install-mokata.md).

## Related

- [Which setup command do I need?](which-setup-command.md) — `init` vs `setup` vs the plugin,
  and the upgrade runbook.
- [Use mokata without the plugin](use-without-plugin.md) — what `mokata setup claude` wires, in
  full.
- [Repairing the MCP server](../reference/cli.md) — if it is the **MCP tools** that are missing
  rather than the gates, that is `mokata mcp install` (or ask Claude: "mokata mcp isn't
  working").
