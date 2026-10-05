"""Does the interpreter running this suite have what the suite needs? — stage 05, row
`NO-COMMAND-PROVISIONS-THE-INTERPRETER-THE-SUITE-NEEDS`.

WHERE THIS CAME FROM, AND IT IS A MEASUREMENT RATHER THAN A WORRY. A full run on the dev machine
returned **7,616 tests · 30 failures · 116 errors**, and every one of the 20 affected modules
passed unchanged in a correctly-provisioned interpreter the same day (465 tests, all OK). Two
causes, neither of them a defect in the code under test:

  * **PyYAML absent → 116 errors.** Those tests hard-FAIL rather than skip, which is correct and
    deliberate — skipping would report OK while the property went unchecked — but it means one
    missing package presents as a catastrophically broken tree.
  * **mokata not installed in that interpreter**, so `mokata-hook` and `mokata-mcp` are off the
    environment's script path. The A2 gate tests then report `'not-wired' != 'enforcing'` and
    `mokata doctor` says *"the gates are NOT firing"*. ⚠ **A2's whole subject is a gate that says
    whether it is on; run unprovisioned it says OFF, and it is RIGHT, about the wrong thing.**

⭐ **THE FINDING IS NOT EITHER CAUSE. It is that nothing in the tree told a human what to run.**
`requirements/ci.txt` exists and `ci.yml` installs it; the docs named no provisioning step before
`python -m unittest discover`, so **the documented way to run the suite was the way that produced
146 red results on a clean checkout** — and the reader's reasonable conclusion was that the tree
was broken.

⛔ **REFUSE, DO NOT SKIP, AND THE DISTINCTION IS THE WHOLE POINT (doc 85 §7g).** An unprovisioned
run must not be able to report a NUMBER at all. A skip reports `OK (skipped=146)`, which is a green
with a footnote; a refusal reports nothing, because nothing was measured. Those are different facts
and must not share a representation.

🔴 **AND MY FIRST VERSION OF THIS WAS THE SAME DEFECT, MEASURED WITHIN THE HOUR.** It raised
`SystemExit`, on the stated reasoning that `unittest`'s loader converts an `Exception` into a
`_FailedTest` but leaves a `BaseException` alone. **That is wrong.** `TestLoader._find_test_path`
wraps the module import in a BARE `except:`, which catches `BaseException`, `SystemExit` included —
so the refusal came back as `_FailedTest`, and a discovery run over one module printed:

    Ran 1 test in 0.000s
    FAILED (errors=1)

⭐ **A number. Per module. Which is the 146-red shape this whole row is about, reproduced by its own
fix** — an instrument failing open (§7e), and it would have passed any test that merely asserted
"the refusal text was printed".

So the refusal calls **`os._exit(2)`** after an explicit flush. It is deliberately the one thing no
interpreter-level handler can intercept: no `except`, no `finally`, no `atexit`. The flush is not
decoration — `os._exit` skips buffer flushing, so without it the refusal would be silent as well as
abrupt. `test_a31` drives a real `unittest discover` subprocess and asserts there is **no
`Ran N tests` line in its output at all**, which is the only assertion that could have caught this.

⚠ **THE THIRD CAUSE IS DELIBERATELY NOT CHECKED HERE.** The original row named a Homebrew SQLite
class as cause ⑶, with a `PRAGMA synchronous == 2` check in the fix shape. That row is CLOSED, and
closed the RIGHT way: `test_ms_s4_sqlite_wal.test_the_durability_pragma_is_SET_BY_US_and_not_
INHERITED` hands the factory a connection already on NORMAL, so it grades the code and never the
box. ⭐ **A preflight check for it would be this module asserting a fact about the machine to
protect a test that no longer depends on the machine** — §7h, a pin encoding a false premise. The
fix shape was written before that test existed; it is not followed blindly.

SEAMS, AND WHY THERE ARE SEAMS. `resolve()` takes its three facts as arguments. A function that
reads the live interpreter can only ever be graded on the machine running it, which is the exact
failure class this module exists for (§7c: the observer is not the repo).

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import importlib.util
import os
import sys
import sysconfig

# ---- the verdicts ----------------------------------------------------------------------------
PROVISIONED = "provisioned"
MISSING = "missing"
UNDECIDABLE_NO_SCRIPT_DIR = "undecidable_no_script_dir"

# ---- what the suite needs, and WHY each one is here --------------------------------------------
# Each entry is (key, human name, the line explaining what breaks without it). The list is SHORT on
# purpose: a preflight that grows into an environment audit becomes the thing people disable.
NEED_YAML = "yaml"
NEED_CONSOLE_SCRIPTS = "console-scripts"

_WHY = {
    NEED_YAML: ("PyYAML (`import yaml`)",
                "the workflow-lint tests import it and hard-FAIL rather than skip, so its absence "
                "presents as ~116 broken tests rather than as a missing package"),
    NEED_CONSOLE_SCRIPTS: ("mokata's console scripts (`mokata`, `mokata-mcp`, `mokata-hook`)",
                           "the gate and hook-resolution tests ask the environment where its "
                           "`mokata-mcp` is; unprovisioned they report the gates as OFF, which is "
                           "true about the wrong thing"),
}

#: The command that fixes BOTH, together, because fixing one and re-running is the loop this
#: module exists to end. Never built from `sys.executable` alone: the point is a venv whose
#: `bin/` is the environment's script directory, which an interpreter path does not imply.
REMEDY = (
    "python3 -m venv .venv && .venv/bin/python -m pip install -e . "
    "-r requirements/ci.txt"
)

_SCRIPTS = ("mokata", "mokata-mcp", "mokata-hook")


class ProvisioningResolution(object):
    """What the interpreter is missing, and never a bare boolean.

    `missing` is the ANSWER; `basis` says how it was reached — `UNDECIDABLE_NO_SCRIPT_DIR` is not
    "nothing is missing", and the two must not share a representation (§7g)."""

    __slots__ = ("basis", "missing", "script_dir")

    def __init__(self, basis, missing=(), script_dir=None):
        object.__setattr__(self, "basis", basis)
        object.__setattr__(self, "missing", tuple(missing))
        object.__setattr__(self, "script_dir", script_dir)

    @property
    def ok(self):
        return self.basis == PROVISIONED

    def render(self):
        """The refusal, as the lines a human reads. One line per missing thing, then the remedy."""
        if self.ok:
            return ""
        out = ["", "=" * 86,
               "REFUSING TO RUN THE TEST SUITE — this interpreter is not provisioned for it.", ""]
        if self.basis == UNDECIDABLE_NO_SCRIPT_DIR:
            out.append("  * this interpreter reports no script directory, so whether mokata's "
                       "console scripts")
            out.append("    are installed CANNOT be determined — which is not the same as them "
                       "being present.")
        for key in self.missing:
            name, why = _WHY[key]
            out.append("  * MISSING: %s" % name)
            out.append("             %s" % why)
        out += [
            "",
            "Run this, then re-run the suite:",
            "",
            "    %s" % REMEDY,
            "",
            "Interpreter : %s" % sys.executable,
            "Script dir  : %s" % (self.script_dir or "<unknown>"),
            "",
            "This is a REFUSAL, not a skip: an unprovisioned run must not be able to report a",
            "number at all. Nothing was measured, so nothing is reported.",
            "=" * 86, ""]
        return "\n".join(out)


def script_dir():
    """The directory this interpreter installs console scripts into, or None.

    `sysconfig`, not `PATH`: the question is whether THIS interpreter has mokata, and a `mokata-mcp`
    somewhere else on `PATH` is a different installation — which is the whole subject of
    `THE-PREFLIGHT-VENV-IS-NOT-ON-ITS-OWN-PATH`."""
    try:
        return sysconfig.get_path("scripts")
    except Exception:                                  # noqa: BLE001
        return None


def resolve(has_yaml, scripts_dir, lister=os.listdir):
    """Derive the provisioning verdict from three supplied facts. NEVER returns ok by default.

    `lister` is a seam so a test can describe a script directory that does not exist on the box it
    is running on — which is the only way to grade the missing case without uninstalling mokata."""
    missing = []
    if not has_yaml:
        missing.append(NEED_YAML)
    if not scripts_dir:
        return ProvisioningResolution(UNDECIDABLE_NO_SCRIPT_DIR, missing, scripts_dir)
    try:
        present = set(lister(scripts_dir))
    except OSError:
        present = set()
    # Windows spells them `mokata.exe`; compare on the stem so the check is not POSIX-only.
    stems = {os.path.splitext(n)[0] for n in present}
    if not all(s in stems for s in _SCRIPTS):
        missing.append(NEED_CONSOLE_SCRIPTS)
    if missing:
        return ProvisioningResolution(MISSING, missing, scripts_dir)
    return ProvisioningResolution(PROVISIONED, (), scripts_dir)


def has_yaml():
    """Is PyYAML INSTALLED? — asked with `find_spec`, deliberately, and not with a guarded import.

    🔴 CAUGHT BY `test_pyyaml_skip_cluster` ON THIS FILE'S FIRST RUN, and the guard was right to
    fire even though this site is not the thing it hunts. That sweep convicts every
    `try: import yaml / except: <carry on>` in the test corpus, because the cluster it closed was
    17 tests that reported OK with their check unperformed. ⭐ **It has no exemption list ON
    PURPOSE** — *"do not add a skip, a substring fallback or a bare return"* — so the answer is not
    to exempt this site but to stop writing the shape at all.

    `importlib.util.find_spec` asks whether the module is IMPORTABLE without importing it. It is
    not a tolerated failure: nothing is swallowed, nothing continues with the check unperformed,
    and the caller REFUSES on a False. It is also strictly better here — the preflight runs before
    every suite and has no reason to execute PyYAML's module body to learn it exists.

    §7h is the lesson, pointed at my own first draft: the guard's premise ("a guarded yaml import
    is a tolerated one") was true of every site that existed when it was written, and I added the
    first counter-example. The repair is the shape the guard wants, not an argument that I am
    special."""
    try:
        return importlib.util.find_spec("yaml") is not None
    except (ImportError, ValueError):
        # A broken or shadowed `yaml` entry is NOT PyYAML being present — and this is the loud
        # direction: the preflight refuses and names it, rather than passing on a bad answer.
        return False


def live_resolution():
    """`resolve` against the interpreter actually running — the one impure function, kept tiny."""
    return resolve(has_yaml(), script_dir())


def refuse_unless_provisioned(resolution=None, stream=None, halt=None):
    """Print the refusal and HALT the process, or return None when the interpreter is fine.

    ⛔ `os._exit`, NOT `SystemExit`, AND THAT IS A MEASUREMENT. `unittest`'s
    `TestLoader._find_test_path` wraps the module import in a BARE `except:`, which catches
    `BaseException` — so a `SystemExit` raised here came back as a `_FailedTest` and the run printed
    `Ran 1 test … FAILED (errors=1)`. A number, per module: the exact shape this module exists to
    prevent, reproduced by its own first fix. `os._exit` cannot be intercepted by any `except`,
    `finally` or `atexit` handler, which is the property being bought.

    ⚠ THE FLUSH IS LOAD-BEARING. `os._exit` skips buffer flushing, so without it a refusal written
    to a redirected stream is lost and the run dies silently — abrupt AND mute, which is worse than
    what it replaced.

    ⚠ ESCAPE HATCH, narrow and loud by design: `MOKATA_SKIP_PREFLIGHT=1`. The callers that need it
    are the tests that drive the suite as a subprocess to grade this very behaviour. It is NOT
    documented as a way to run the suite — if it were, it would be how everyone ran it and this
    module would be decoration.

    `halt` is a seam so the refusal can be graded in-process; production passes nothing."""
    if os.environ.get("MOKATA_SKIP_PREFLIGHT") == "1":
        return None
    res = live_resolution() if resolution is None else resolution
    if res.ok:
        return None
    out = stream or sys.stderr
    out.write(res.render())
    try:
        out.flush()
    except Exception:                                  # noqa: BLE001
        pass
    (halt or os._exit)(2)
    return None                                        # unreachable in production; a seam may return
