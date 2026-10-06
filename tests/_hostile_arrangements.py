"""HOSTILE ARRANGEMENTS — the Windows-class defects that are reproducible on POSIX, declared.

NOT A TEST. A declared registry plus two pure rules, for `WINDOWS-HAS-NO-RUNNER-UNTIL-THE-CUT`.

WHERE THIS COMES FROM, AND THE FINDING IS THE TIMING RATHER THAN THE SIZE
------------------------------------------------------------------------
Jas asked why hours go on failing Windows tests every build. The answer was measured rather than
guessed at, and it is CONSTRUCTION, not discipline:

  * `ci.yml` is `on: push` and DOES carry Windows legs — and the private repo's Actions minutes
    have been exhausted since 2026-08-17, so it never executes during the build;
  * the local preflight is macOS-only by declaration (`PREFLIGHT-PLATFORM-SCOPE-IS-THE-LAPTOP`);
  * there is no Windows host.

⭐ **So the first Windows execution of an entire release's code is AT THE CUT.** Five cuts of
evidence: 5 failures / 3 causes, then 20/3, 14/2, 10 + a 54-minute wedge, 11 across 2 modules.
**Every batch resolved to defects in the TESTS, none in mokata** — and each cause was invisible to
its author *by construction*, because on POSIX the broken and the correct spelling behave
identically. That is why *"review more carefully"* is not a remedy.

⛔ **THE BATCH IS NOT LARGE, IT IS LATE.** What closes that is not a Windows runner — it is making
the arrangement that exposes the class run on a machine this project already has, on every push.

WHAT IS HERE, AND WHY EACH ONE EARNS ITS PLACE
----------------------------------------------
An arrangement belongs here when it satisfies three things, and all three are stated per entry:

  1. it reproduces a MEASURED Windows failure (the run id is recorded);
  2. it is reproducible on POSIX — otherwise it is a wish, not a leg;
  3. it is cheap. A leg nobody can afford to run is the weekly workflow again.

⚠ WHAT IS DELIBERATELY ABSENT. A case-insensitive filesystem, `os.sep`, and the `CreateProcess`
System32 search order are all real Windows-class causes this project has paid for, and **none of
them can be arranged on POSIX.** They are covered by the static readers in
`tests/_windows_portability.py` instead, and the honest statement is that this leg is a SECOND
instrument rather than a replacement for a Windows runner (§7f: different corpora, both gradeable).

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import ast
import io
import os

import _support
from typing import Dict, List, Sequence, Tuple

# ---- the arrangements -------------------------------------------------------------------------


class Arrangement(object):
    """One hostile environment, with the Windows failure it stands in for.

    `evidence` is not decoration: an arrangement whose Windows instance nobody can name is a
    guess, and this project has already paid for one of those (the 0.0.20 line-ending theory,
    written from a plausible mechanism and wrong)."""

    __slots__ = ("key", "env", "reproduces", "evidence", "why_posix")

    def __init__(self, key, env, reproduces, evidence, why_posix):
        self.key = key
        self.env = dict(env)
        self.reproduces = reproduces
        self.evidence = evidence
        self.why_posix = why_posix

    def apply(self, base=None):
        """`base` overlaid with this arrangement. Never mutates `os.environ`."""
        out = dict(os.environ if base is None else base)
        out.update(self.env)
        return out


#: ⭐ TWO ARRANGEMENTS, AND BOTH ARE MEASURED RATHER THAN IMAGINED.
ARRANGEMENTS: Tuple[Arrangement, ...] = (
    Arrangement(
        key="cp1252-stdio",
        env={"PYTHONIOENCODING": "cp1252"},
        reproduces=(
            "a GitHub Windows runner hands Python a cp1252 stdout, where a label carrying a "
            "character like U+2605 cannot be encoded at all: the write raises UnicodeEncodeError "
            "and whatever was printing dies mid-line."
        ),
        evidence=(
            "0.0.20: the mutant-pattern stub died this way on both Windows 3.12 legs. "
            "`PYTHONIOENCODING=cp1252` on POSIX gave THE SAME THREE NUMBERS — see "
            "`tests/_mutant_pattern_stub.py` and `test_a9_mutant_batches_are_swept`."
        ),
        why_posix=(
            "`PYTHONIOENCODING` sets the stdio codec on every platform, so the refusal is the "
            "same refusal. This is the one arrangement this project has already proved transfers."
        ),
    ),
    Arrangement(
        key="ascii-locale",
        env={"LC_ALL": "C", "LANG": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0"},
        reproduces=(
            "an `open()` with no explicit `encoding=` uses the LOCALE encoding, which on Windows "
            "is cp1252 — so reading any file holding a non-ASCII byte raises UnicodeDecodeError "
            "on Windows and succeeds silently on a UTF-8 POSIX box."
        ),
        evidence=(
            "0.0.21 stage 14: seven `open()` calls in one stage were missing "
            "`encoding=\"utf-8\"`, every one of them invisible on the author's machine. The class "
            "is recurrent rather than historical, which is why it also has a static rule below."
        ),
        why_posix=(
            "`LC_ALL=C` with `PYTHONUTF8=0` makes the locale encoding ASCII, which is STRICTER "
            "than cp1252: anything that survives here survives there. ⚠ Strictly stronger is the "
            "right direction for a stand-in, and it is why a pass is meaningful while a failure "
            "needs reading — a byte legal in cp1252 and illegal in ASCII is a false alarm, so the "
            "arrangement is used on the product's own output rather than on arbitrary fixtures."
        ),
    ),
)

ARRANGEMENT_KEYS = tuple(a.key for a in ARRANGEMENTS)


def arrangement(key: str) -> Arrangement:
    for item in ARRANGEMENTS:
        if item.key == key:
            return item
    raise KeyError("no such hostile arrangement: %r" % (key,))


# ---- the static rule the `ascii-locale` arrangement cannot reach on its own --------------------

#: The two spellings of the SAME boundary. `codecs.open` is included because it is the one people
#: reach for when they remember encodings exist.
_OPENERS = ("io", "codecs")


def _is_text_open(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Name) and func.id == "open":
        pass
    elif (isinstance(func, ast.Attribute) and func.attr == "open"
          and isinstance(func.value, ast.Name) and func.value.id in _OPENERS):
        pass
    else:
        # ⚠ `os.open`, `webbrowser.open`, `tarfile.open`, `gzip.open`, `zipfile.ZipFile.open` all
        # end in `.open` and NONE of them takes an `encoding`. My first version matched on the
        # ATTRIBUTE NAME alone and reported 14 offenders, every single one of them one of those —
        # a detector whose whole output is false positives (§7g: a method named `open` and the
        # text-file builtin are different facts).
        return False
    for arg in node.args[1:2]:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and "b" in arg.value:
            return False
    for kw in node.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant) \
                and isinstance(kw.value.value, str) and "b" in kw.value.value:
            return False
    return True


def text_opens_without_encoding(sources: Dict[str, str]) -> Tuple[Tuple[str, int], ...]:
    """Every TEXT-mode `open()` in a SUPPLIED corpus that names no `encoding`.

    ⭐ A PURE FUNCTION OVER A SUPPLIED CORPUS (§7i). The tree holds ZERO offenders today — measured
    — so a reader that walked the tree and judged it in one step would have no offender to be
    graded against, and gutting it would be green.

    ⛔ WHY THIS IS A STATIC RULE AND NOT LEFT TO THE `ascii-locale` ARRANGEMENT: the arrangement
    only convicts a call that actually RUNS and actually reads a non-ASCII byte. Most of these
    read files that happen to be ASCII today, so the arrangement is silent and the defect waits
    for the first accented filename or an emoji in a commit message. The rule convicts the
    SPELLING, which is the thing the author can see."""
    found: List[Tuple[str, int]] = []
    for name in sorted(sources):
        text = sources[name]
        if text is None:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _is_text_open(node) \
                    and "encoding" not in {kw.arg for kw in node.keywords}:
                found.append((name, node.lineno))
    return tuple(found)


def python_sources(root: str, subdirs: Sequence[str] = ("src", "tests", "scripts")) -> Dict[str, str]:
    """`{repo-relative POSIX name: text}` for the tree's own python.

    CORPUS: THE WORKING TREE — an uncommitted file is exactly the one whose spelling nobody has
    reviewed yet, which is the whole point of catching this at write time rather than at the cut.
    """
    out: Dict[str, str] = {}
    for sub in subdirs:
        base = os.path.join(root, sub)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames
                           if d not in ("__pycache__", ".mokata", "vendor", ".venv")]
            for name in sorted(filenames):
                if not name.endswith(".py"):
                    continue
                path = os.path.join(dirpath, name)
                try:
                    with io.open(path, encoding="utf-8") as handle:
                        # ⚠ `_support.posix_rel`, NOT `os.path.relpath(...).replace(os.sep, "/")`.
                        # A repo-relative name is a NAME, not a filesystem path, and this tree has
                        # ONE place that conversion happens — `test_windows_shell_and_paths`
                        # convicted my hand-rolled version the first time it ran. Two spellings of
                        # the same conversion is the drift that detector exists for.
                        out[_support.posix_rel(path, root)] = handle.read()
                except (OSError, UnicodeDecodeError):
                    continue
    return out
