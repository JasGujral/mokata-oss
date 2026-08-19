"""What a workflow's shell ACTUALLY RUNS — PURE FUNCTIONS over SUPPLIED shell text.

0.0.18 stage 8 (`PINNED-DEPS-PIP-EDITABLE` + `CLAUDE-MD-SHIPS-LIST-INCOMPLETE`'s second list,
doc 84 §1 and §9).

TWO QUESTIONS, ONE READER, AND THE READER IS THE POINT
------------------------------------------------------
Both rows this file serves were previously answered by GREPPING FOR A STRING, and both answers
were wrong in the same way — a grep cannot tell a command from a sentence about a command:

  * `.github/workflows/release.yml` contains the text `pip install` inside an `echo` that explains
    a wheel-packaging abort ("…would fail on a clean pip install."). A grep for `pip install`
    convicts it.
  * The same file contains `scripts/release.sh` and `scripts/sync-public.sh` inside the refusal
    job's `echo`ed instructions. A grep for `scripts/` "finds" two dev-only scripts being run by
    CI, which would make the mirror-boundary answer exactly backwards: those two must NEVER ship,
    and a helper that CI runs must ALWAYS ship.

That is `PIN-SUBSTRING-COMMENT-HOLE` (doc 85 §7h) with the prose inside a live line rather than a
comment, so comment-stripping alone does not save you. The fix is to read a COMMAND POSITION: the
first word of a fragment, after shell keywords and leading `VAR=value` assignments are removed.
`echo` is not an interpreter, so its arguments are never inspected.

THE PINNED-DEPENDENCIES PROPERTY, AND WHY IT IS A PROPERTY AND NOT A SCORE
--------------------------------------------------------------------------
The row's own instruction is `Do NOT chase the 6 as a score`, and stage 8 measured exactly why
that instruction is right rather than merely prudent:

    THERE ARE EIGHT `pip install -e` SITES IN `.github/workflows/` AND SEVEN OF THEM ALERT.

Derived 2026-08-13 by reading the live open alerts off the public mirror
(`gh api /repos/JasGujral/mokata-oss/code-scanning/alerts?state=open`), which returns exactly
EIGHT open `PinnedDependenciesID` alerts — seven editable installs plus the SBOM venv's
`pip install --upgrade pip`. The eighth editable site, `ci.yml`'s `hooks-execute` job, produces no
alert. The ONLY structural difference between it and the seven, and the control is in the same
file, is its `defaults: run: shell:`:

    ci.yml `test`           runs-on: ${{ matrix.os }}   shell: bash                    ALERTS
    ci.yml `hooks-execute`  runs-on: ${{ matrix.os }}   shell: ${{ matrix.runner_shell }}  SILENT

So the templated shell is what the instrument cannot resolve, and a job whose shell it cannot
resolve is a job whose `run:` steps it never reads. **The score under-counts, and nothing in the
score says so.** Driving 8 alerts to 0 would leave a site the instrument never counted — which is
the whole argument for grading the property here instead.

THE PROPERTY, in three classes, and the third must be EMPTY:

    HASH_PINNED    `--require-hashes -r requirements/*.txt`. Pinned and integrity-verified.
    LOCAL_SOURCE   installs THIS package from the checkout (`-e .`, `-e ".[extra]"`) or a locally
                   built artifact (`dist/*.whl`). Nothing is fetched by name.
    UNPINNED_FETCH anything else — a name resolved against an index with no hash, INCLUDING a
                   `-r requirements.txt` that does not require hashes.

⚠ THE HONEST RESIDUAL, STATED RATHER THAN WAVED THROUGH. `LOCAL_SOURCE` means the PACKAGE is not
fetched. Its DECLARED DEPENDENCIES still are, unpinned: `pyproject.toml` requires `mcp>=1.2,<2`,
whose resolved closure is THIRTY packages (measured 2026-08-13 against an installed 3.12 venv), on
top of the `setuptools>=61.0` build backend pip fetches under build isolation, plus each extra's
own tree at the `[postgres]` / `[embeddings]` sites. The backlog row calls that "the
real (small) exposure"; **it is not small, and it is not fixed here** — fixing it means a
constraint file at all eight sites, which is the seven the row forbids touching. It is named in
the stage-8 report as a proposed row, and `LOCAL_SOURCE` is deliberately not spelled "safe".

WHAT THIS FILE DOES NOT DO, declared rather than discovered:

  * **It is not Scorecard.** It does not model `pipCommand`, it does not know which sites alert,
    and it must not be edited to make a number move. The relationship runs the other way: the
    instrument's blind spot above is a fact ABOUT the instrument, recorded here so the next reader
    does not reconcile 8 sites against 7 alerts by assuming one of them moved.
  * **It reads shell text, not a shell.** Word splitting is naive; `eval`, a command hidden behind
    a variable, or a pip invoked from a python script are all invisible. Every construct in this
    repo's nine workflows is a plain command, and a new construct that this cannot read shows up
    as an absence — which is why `test_pinned_dependency_property` carries an anti-vacuity floor.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import re

__all__ = [
    "HASH_PINNED", "LOCAL_SOURCE", "UNPINNED_FETCH", "CLASSES",
    "command_positions", "pip_installs", "classify_pip_install", "unpinned_fetches",
    "executed_scripts",
]

# ---- the three classes ------------------------------------------------------------------------

HASH_PINNED = "hash-pinned"
LOCAL_SOURCE = "local-source"
UNPINNED_FETCH = "unpinned-fetch"

#: Ordered worst-last so a caller can print them in a stable order. Named as a tuple so a mutant
#: that drops one is visible rather than being absorbed by a dict literal.
CLASSES = (HASH_PINNED, LOCAL_SOURCE, UNPINNED_FETCH)

# ---- reading a command position ---------------------------------------------------------------

# `$(` opens a NEW command, so it is a separator like `;` and `|`. Without it the release job's
# `inputs="$(bash scripts/check-release-assets.sh dist --inputs)"` presents `inputs="$(bash` as its
# first word and the helper CI genuinely runs reads as never run — the exact inversion the module
# docstring warns about, arriving from the other side.
_SPLIT = re.compile(r"(?:\|\||&&|[;|&\n]|\$\()")

# Words that can precede a real command and are not the command. `sudo`/`env`/`command`/`exec` are
# here because they take the real command as their argument; the block keywords because splitting
# on `;` leaves them attached to what follows (`…; then python -m pip install …`).
_PREFIX_WORDS = frozenset({
    "then", "else", "elif", "do", "done", "fi", "!", "time",
    "sudo", "env", "command", "exec", "nohup",
})

_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# A redirection is not an argument. `pip install dist/*.whl >/dev/null` would otherwise present
# `>/dev/null` as a requirement specifier — and because it contains a `/` it would read as a LOCAL
# path, i.e. the right verdict reached through a wrong reason, which is the shape that survives a
# mutant. Both spellings are handled: glued (`>/dev/null`, `2>&1`) and detached (`> /dev/null`),
# where the target word is consumed too.
_REDIRECT_GLUED = re.compile(r"^\d?(?:>>|>|<<|<)\S")
_REDIRECT_BARE = re.compile(r"^\d?(?:>>|>|<<|<)$")
_PIP = re.compile(r"^(?:.*/)?pip[23]?(?:\.\d+)?$")
_PYTHON = re.compile(r"^(?:.*/)?python[23]?(?:\.\d+)?$")
_INTERPRETERS = re.compile(r"^(?:.*/)?(?:ba|da|z|k)?sh$|^(?:.*/)?python[23]?(?:\.\d+)?$")
_SCRIPT_TOKEN = re.compile(r"^\.?/?(scripts/[A-Za-z0-9_.\-/]+)$")


def _strip_comment(fragment):
    """Drop a trailing `#` comment, but only when the `#` starts a word outside quotes.

    Deliberately the same rule `tests/_release_assets.py` applies, and deliberately NOT enough on
    its own: this module's two false positives both live inside `echo` arguments on LIVE lines, so
    the command-position test below is the defence and this only removes the easy half.
    """
    out, quote = [], None
    for index, char in enumerate(fragment):
        if quote:
            out.append(char)
            if char == quote:
                quote = None
            continue
        if char in "'\"":
            quote = char
            out.append(char)
            continue
        if char == "#" and (index == 0 or fragment[index - 1] in " \t"):
            break
        out.append(char)
    return "".join(out)


def command_positions(shell_text):
    """`[(line_number, words)]` — one entry per command, with the command's own name first.

    Leading `VAR=value` assignments and shell keywords are dropped, so `words[0]` is the program
    being run. A fragment that reduces to nothing yields no entry.
    """
    found = []
    for number, line in enumerate(shell_text.splitlines(), start=1):
        for fragment in _SPLIT.split(_strip_comment(line)):
            words = fragment.split()
            while words and (words[0] in _PREFIX_WORDS or _ASSIGNMENT.match(words[0])):
                words = words[1:]
            kept, index = [], 0
            while index < len(words):
                word = words[index]
                if _REDIRECT_BARE.match(word):
                    index += 2                       # the operator AND its target
                    continue
                if not _REDIRECT_GLUED.match(word):
                    kept.append(word)
                index += 1
            if kept:
                found.append((number, kept))
    return tuple(found)


def _pip_arguments(words):
    """The arguments of a `pip install`, or None if this command is not one.

    Two spellings, both live in this repo: a bare `pip install …` and `<python> -m pip install …`
    (including the SBOM venv's absolute `/tmp/sbomenv/bin/python -m pip install …`).
    """
    if _PIP.match(words[0]):
        rest = words[1:]
    elif _PYTHON.match(words[0]) and words[1:3] and words[1] == "-m" and _PIP.match(words[2]):
        rest = words[3:]
    else:
        return None
    return rest[1:] if rest[:1] == ["install"] else None


def pip_installs(shell_text):
    """`[(line_number, arguments)]` for every `pip install` at a COMMAND POSITION."""
    found = []
    for number, words in command_positions(shell_text):
        arguments = _pip_arguments(words)
        if arguments is not None:
            found.append((number, tuple(arguments)))
    return tuple(found)


def _is_local(spec):
    """Whether one requirement specifier names something already on this disk.

    `.` and `".[postgres]"` are the checked-out source; `dist/*.whl` is what the build job just
    produced. A bare name — `pip`, `requests` — is not local, and that is the whole distinction.
    """
    bare = spec.strip("'\"")
    return bare.startswith((".", "/", "~")) or "/" in bare


def classify_pip_install(arguments):
    """Which of `CLASSES` one `pip install`'s argument list falls into.

    ⚠ THE `-r` RULE IS NOT A DETAIL. A requirements file is a LOCAL PATH holding REMOTE NAMES, so
    the path test alone would classify `pip install -r requirements/ci.txt` as `LOCAL_SOURCE` and
    quietly bless an un-hashed fetch of everything in it. Requirements installs are pinned by
    `--require-hashes` or they are not pinned at all.
    """
    flags = [a for a in arguments if a.startswith("-")]
    specs = [a for a in arguments if not a.startswith("-")]
    requirement = any(f in ("-r", "--requirement") or f.startswith("--requirement=")
                      for f in flags)
    if requirement:
        return HASH_PINNED if "--require-hashes" in flags else UNPINNED_FETCH
    # `-e` takes the NEXT word as its value; that word is a spec and is already in `specs`.
    if specs and all(_is_local(s) for s in specs):
        return LOCAL_SOURCE
    return UNPINNED_FETCH


def unpinned_fetches(shell_text):
    """`[(line_number, arguments)]` for the installs that resolve a NAME against an index.

    This is the set the property requires to be EMPTY. Everything else is either integrity-verified
    or already on the disk before pip is asked for it.
    """
    return tuple((number, arguments) for number, arguments in pip_installs(shell_text)
                 if classify_pip_install(arguments) == UNPINNED_FETCH)


def executed_scripts(shell_text):
    """Every `scripts/…` path this shell text RUNS, as a frozenset of repo-relative paths.

    Two shapes, both live: an interpreter with the script as an argument
    (`bash scripts/check-release-assets.sh`, `python scripts/normalize_sdist.py`) and the script
    invoked directly (`./scripts/foo.sh`). A `scripts/…` string anywhere else — an `echo`'s
    argument, a comment, a `--flag=scripts/x` value — is NOT a run, which is the distinction the
    mirror boundary turns on: a helper CI runs must ship, and the two dev-only scripts named in
    the refusal job's instructions must never.
    """
    found = set()
    for _number, words in command_positions(shell_text):
        direct = _SCRIPT_TOKEN.match(words[0])
        if direct:
            found.add(direct.group(1))
            continue
        if not _INTERPRETERS.match(words[0]):
            continue
        for word in words[1:]:
            if word.startswith("-"):
                continue
            candidate = _SCRIPT_TOKEN.match(word)
            if candidate:
                found.add(candidate.group(1))
            break            # the first non-flag word is the script; the rest are ITS arguments
    return frozenset(found)
