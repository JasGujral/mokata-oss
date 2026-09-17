#!/usr/bin/env python3
"""THE BATCH SWEEP — runs every mutant batch driver in the tree against the stand-in mutator.

`MUTANT-BATCHES-RUN-BY-NOBODY` (doc 84): the tree's batch drivers are graded for SHAPE by
`_mutant_driver_contract.py` and for RESULTS by nothing.  A mutant whose `old` string stops
occurring in its target makes `scripts/mutate.sh` exit 3; the driver then aborts and **every
mutant after it in the list is never graded again** — silently, with the project's record still
carrying the batch's kill count.  Four mutants died that way in one batch and it took five days to
notice.

This module is the cheap half of the answer.  Deciding *"does this `old` still occur exactly once
in this file"* is a string count and needs no test execution at all, so the rot that actually
happens gets graded on every push in seconds, while the expensive half (apply, run the suite,
restore) stays a nightly job.

⛔ **A SWEEP RUN IS NOT A MUTATION SCORE.**  Nothing here mutates a file or runs a test.  See
`_mutant_pattern_stub.py` for the divergence that makes a one-pass sweep possible, and for why the
`RED` the stub prints means nothing.

WHAT IS DERIVED AND WHAT IS TYPED
---------------------------------
Nothing about the corpus is typed.  The driver set is globbed, the mutant count is summed from the
records the drivers actually produce, and the offender set is whatever the sweep finds.  The one
constant in this file is the name of the environment variable the drivers already read.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

import _support
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

#: The seam every driver reads to find its mutator.  A driver without it cannot be swept at all,
#: which is itself a finding — see `drivers_without_the_seam`.
SEAM = "MUTATE_SH"

#: ⚠ THE SEAM IS DETECTED BY ITS **EXPANSION**, NOT BY THE NAME APPEARING SOMEWHERE IN THE FILE.
#: A substring test read `_stage18c_mutants.sh` as seamed because it exports an unrelated variable
#: called `MUTATE_SH_UNDER_TEST`, so the driver was reported swept while the sweep was in fact
#: running the REAL mutator against it — applying real mutations and running real tests, which is
#: how it showed up as a 120-second timeout rather than as a finding.  Measured 2026-08-26, and it
#: is this row's own defect committed by the instrument built to close it: a check that matches a
#: NAME where it means a MECHANISM.
SEAM_EXPANSION = re.compile(r"\$\{" + SEAM + r"[:}\-]")

#: Where the stub writes one JSON record per mutant.
LOG_VAR = "MUTANT_SWEEP_LOG"

STUB = "_mutant_pattern_stub.py"


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def discover_drivers(root: Path | None = None) -> list[Path]:
    """Every batch driver in the tree, DERIVED from the filesystem.

    A driver is a shell script under `tests/` that invokes the sanctioned mutator.  That is the
    definition the sweep uses rather than a name pattern, because `_run_mutants.sh` does not carry
    `_mutants.sh` in its name and a list keyed on the name would have missed it."""
    root = root or repo_root()
    out = []
    for path in sorted((root / "tests").glob("*.sh")):
        try:
            body = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "mutate.sh" in body and "mutant" in body.lower():
            out.append(path)
    return out


def drivers_without_the_seam(root: Path | None = None) -> list[Path]:
    """Drivers that hard-code the mutator and so can be neither swept nor dry-run."""
    return [p for p in discover_drivers(root)
            if not SEAM_EXPANSION.search(p.read_text(encoding="utf-8", errors="replace"))]


@dataclass
class DriverResult:
    driver: str
    returncode: int
    records: list = field(default_factory=list)
    stdout: str = ""

    @property
    def offenders(self):
        """Mutants whose `old` string no longer occurs exactly once IN A FILE THAT IS THERE.

        ⛔ AN UNREADABLE TARGET IS NOT A STALE PATTERN, and folding the two together is §7g inside
        the instrument built to find §7g. *"the pattern stopped matching"* asks somebody to repair
        a driver; *"the file is not in this tree"* asks whether the tree is supposed to have it —
        opposite remedies, and on the public mirror the second is the boundary WORKING. The stub
        has recorded `unreadable` all along; this property simply stopped ignoring it."""
        return [r for r in self.records if not r.get("applies") and not r.get("unreadable")]

    @property
    def unreadable_targets(self):
        """Records whose TARGET FILE could not be read at all. On the public mirror these are the
        internal subjects the mirror deliberately does not carry; in a dev checkout they are a
        driver pointed at a file that has been deleted, which is a real fault. The caller decides,
        because only the caller knows which tree it is in."""
        return [r for r in self.records if r.get("unreadable")]

    @property
    def no_op_mutations(self):
        return [r for r in self.records if r.get("is_a_no_op_mutation")]


def _stub_wrapper(tmpdir: str, stub: Path) -> str:
    """A tiny executable that hands the driver's argv to the stub under THIS interpreter.

    The drivers exec `"$M" "$@"`, so the seam has to point at something executable.  Routing
    through `sys.executable` rather than the stub's shebang keeps the sweep on the same interpreter
    the tests run under -- a sweep that silently used a different python would be measuring a
    different tree's idea of the source."""
    path = os.path.join(tmpdir, "mutate_stub")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\nexec %s %s \"$@\"\n" % (sys.executable, stub))
    os.chmod(path, 0o755)
    return path


def run_sweep(root: Path | None = None, drivers=None, timeout: int = 120,
              stub: Path | None = None) -> list[DriverResult]:
    """Run every driver against the stub and return one result per driver.

    `stub` is a seam of its own: `test_a9` points it at a LYING stub to prove that this sweep would
    notice a stub that reports a match for a string that is not there.  Without that, a broken stub
    greens every batch in the tree for free -- this row's own defect, one layer up."""
    root = root or repo_root()
    drivers = drivers if drivers is not None else discover_drivers(root)
    stub = stub or (root / "tests" / STUB)
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        wrapper = _stub_wrapper(tmp, stub)
        for driver in drivers:
            log = os.path.join(tmp, driver.name + ".jsonl")
            env = dict(os.environ)
            env[SEAM] = wrapper
            env[LOG_VAR] = log
            env["PYTHON"] = sys.executable
            try:
                # ⚠ `bash` RESOLVED THROUGH PATH, never handed bare to CreateProcess. On
                # Windows `bash.exe` in System32 is the WSL launcher, so a bare name
                # spawns a different program entirely — invisible on POSIX, which is why
                # the tree has one resolver and a sweep that reds on any bare spelling.
                # It caught this line the day it landed.
                # ⛔ `stdin=DEVNULL`, and it is not defensive: a driver runs a green-baseline
                # `unittest` pass, and a suite spawned with INHERITED stdin can reach a consent
                # gate and WAIT — the shape that wedged a Windows leg for 54 minutes, and the same
                # shape stage 10 measured three tests into this same release. A sweep that can
                # block is a gate nobody runs.
                proc = subprocess.run(_support.bash_argv(str(driver)), cwd=str(root),
                                      env=env, capture_output=True, text=True,
                                      stdin=subprocess.DEVNULL, timeout=timeout)
                rc, out = proc.returncode, proc.stdout + proc.stderr
            except subprocess.TimeoutExpired as exc:
                rc, out = -1, f"TIMEOUT after {timeout}s: {exc}"
            records = []
            if os.path.exists(log):
                with open(log, encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if line:
                            records.append(json.loads(line))
            results.append(DriverResult(driver=driver.name, returncode=rc,
                                        records=records, stdout=out))
    return results


def declared_total(driver: Path) -> int | None:
    """The `TOTAL=` a driver declares about itself, or None if it declares none.

    The declaration is the driver's OWN claim about the size of its list; the sweep counts what the
    list actually produced.  A gap between the two is a driver whose accounting never reaches the
    end of itself -- which is the `MUTANT-BATCHES-RUN-BY-NOBODY` shape at the level of the driver
    rather than the mutant, and no existing check compares the two numbers."""
    for line in driver.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if stripped.startswith("TOTAL="):
            digits = stripped[len("TOTAL="):].strip().strip('"').strip("'")
            if digits.isdigit():
                return int(digits)
    return None


#: A DRIVER'S OWN "my subject is not in this tree" EXIT — declared here so the sweep has a word for
#: it instead of a number typed inside one assertion.
#:
#: `_tag_guard_mutants.sh` checks for `scripts/release.sh` before its C group and, when the file is
#: absent, prints *"This is the PUBLIC subset, where the C group cannot run … this is not a pass"*
#: and exits 66. That is the driver telling the truth about a tree, and `reconcile` reported it as
#: *"never reached the end of its own mutant list"* — the same §7g collapse as `offenders` one
#: property up: **"not swept because the subject is not here" and "not swept because something
#: broke" are different facts with opposite remedies.**
#:
#: ⚠ IT IS NOT IN `mutate.sh`'s EXIT CONTRACT, and deliberately not added to it: 66 is the DRIVER's
#: verdict about its own corpus, not the mutator's about one mutation. The mutator never sees it.
SUBJECT_ABSENT_RC = 66


def reconcile(results, root: Path | None = None) -> list:
    """Per driver: (name, declared, swept, returncode).  Nothing is typed; both numbers are read."""
    root = root or repo_root()
    out = []
    for r in results:
        out.append((r.driver, declared_total(root / "tests" / r.driver),
                    len(r.records), r.returncode))
    return out


def verify_records(results, root: Path | None = None) -> list:
    """Re-count every swept pattern IN THIS PROCESS and return the records the stub got wrong.

    ⛔ **THE SWEEP MAY NOT TAKE THE STUB'S WORD FOR IT.** The stub is the only thing standing
    between 45 batches and a green tick, so a stub that answered *"applies"* for every string would
    green the entire corpus for free -- `MUTANT-BATCHES-RUN-BY-NOBODY` reproduced one layer up, by
    the instrument written to close it. The counting is therefore done twice, by two different
    programs, and `test_a9` proves the second one notices by running the sweep against a stub that
    lies on purpose.

    Returns a list of `(record, counted_here)` for every disagreement."""
    root = root or repo_root()
    cache: dict = {}
    wrong = []
    for result in results:
        for rec in result.records:
            if rec.get("old_text") is None or not in_repo(rec.get("target", ""), root):
                continue
            target = rec["target"]
            if target not in cache:
                try:
                    cache[target] = (root / target).read_text(encoding="utf-8", errors="replace")
                except OSError:
                    cache[target] = None
            body = cache[target]
            counted = -1 if body is None else body.count(rec["old_text"])
            if bool(rec.get("applies")) != (counted == 1):
                wrong.append((rec, counted))
    return wrong


def in_repo(target: str, root: Path | None = None) -> bool:
    """Is this mutant's target a file THIS repo owns?

    Two drivers grade `scripts/mutate.sh` itself and do it on a temporary COPY, so their targets are
    absolute paths under `$TMPDIR` that no longer exist once the driver's trap has fired.  Those
    records cannot be second-counted by anybody, and saying so is not the same as counting them --
    see `unverifiable_records`, which keeps the two facts apart rather than letting a skip read as
    a pass (doc 85 §7g)."""
    root = root or repo_root()
    path = Path(target)
    if path.is_absolute():
        try:
            path.relative_to(root)
        except ValueError:
            return False
    return True


def unverifiable_records(results, root: Path | None = None) -> list:
    """`(driver, record)` for every swept mutant whose target this process cannot re-read.

    The number belongs in the open, because it is exactly the hole a stub could hide in: a stub
    that reported an absolute `/tmp` target for every mutant would make `verify_records` return
    nothing at all and look immaculate."""
    root = root or repo_root()
    return [(r.driver, rec) for r in results for rec in r.records
            if not in_repo(rec.get("target", ""), root)]


def patterns_matching_no_test(results, root: Path | None = None) -> list:
    """`(driver, record)` for every mutant whose `<test-pattern>` matches no file under `tests/`.

    ⭐ FOUND BY THE FIRST SWEEP, and it is a whole class rather than one driver.
    `unittest discover -p 'test_stage17*.py'` against a tree that no longer contains such a file
    prints **`Ran 0 tests ... OK`** and exits 0 — a green.  So a mutant whose graders were deleted
    does not report *"I have no graders"*; it reports *"nothing caught me"*, which reads as a
    finding about a PIN when it is a finding about the LIST.  The pattern comes from the mutator's
    own argv rather than from parsing the driver, because a driver may build it at runtime."""
    root = root or repo_root()
    tests = root / "tests"
    cache: dict = {}
    out = []
    for result in results:
        for rec in result.records:
            pattern = rec.get("pattern") or ""
            if pattern not in cache:
                cache[pattern] = bool(list(tests.glob(pattern))) if pattern else False
            if not cache[pattern]:
                out.append((result.driver, rec))
    return out


#: A quoted glob that names test files, anywhere in a driver's text. Drivers assign these to
#: variables (`T12='test_stage12_*.py'`) and use them for the BASELINE step as well as for
#: individual mutants, and the baseline step is the one the sweep cannot see.
_TEST_GLOB = re.compile(r"""['"](test_[A-Za-z0-9_*]*\.py)['"]""")


def globs_matching_no_test(root: Path | None = None) -> list:
    """`(driver, glob)` for every test-file glob a driver names that matches nothing.

    ⚠ WIDER THAN `patterns_matching_no_test`, AND FOUND BY IT FAILING TO CATCH ONE. That function
    reads the mutator's argv, so it sees the pattern each MUTANT is graded by -- and misses the one
    a driver uses for its own green-baseline step. `_stage12_migrate_slice_mutants.sh` named
    `test_stage17_vault_rehome_gated.py` in its baseline loop long after that file was deleted, and
    its baseline PASSED, because `unittest discover` over a pattern matching nothing prints
    `Ran 0 tests ... OK`. A green baseline over an empty suite is the same lie one level up from
    the one this whole row is about."""
    root = root or repo_root()
    tests = root / "tests"
    out = []
    for driver in discover_drivers(root):
        body = driver.read_text(encoding="utf-8", errors="replace")
        for glob in sorted(set(_TEST_GLOB.findall(body))):
            if not list(tests.glob(glob)):
                out.append((driver.name, glob))
    return out


def grader_skip_census(results, root: Path | None = None) -> dict:
    """`{pattern: (skipped, total, mutants)}` — how many of each mutant's graders are ASLEEP here.

    🔴 `BATCHES-THAT-COULD-NOT-RUN-WERE-HIDING-UNGRADED-PINS` (doc 84) turned up two mutants whose
    grading run printed **`Ran 13 tests … OK (skipped=9)`** and came back GREEN — *"nothing caught
    me"* when the truth was *"most of the graders were asleep"*. ⛔ **A mutant graded by a skipped
    test is `Ran 0 tests … OK` wearing a number**, and the number is what makes it look graded.

    ⚠ **THIS REPORTS. IT DOES NOT GATE, AND THE REASON IS STATED RATHER THAN LEFT AS AN OMISSION.**
    Decorator skips are evaluated at LOAD time against THIS host, so the census legitimately differs
    between a laptop with PostgreSQL and a runner without — a pinned number would red everywhere
    except the machine it was recorded on. And the honest gate needs a fact this function does not
    have: **whether the mutant SURVIVED.** A survivor under a 69%-asleep pattern is a finding about
    the RUNNER; a kill under the same pattern is fine. Survival lives in the driver, so the gate
    belongs there — filed, not faked here.

    Measured on this host 2026-08-26: `test_sync_public_nested_checkout.py` is **9 of 13 skipped**
    and carries 10 mutants across two drivers — which is precisely where `_stage21`'s M17 and M18
    survived."""
    root = root or repo_root()
    import unittest as _ut
    per_pattern: dict = {}
    for result in results:
        for rec in result.records:
            pattern = rec.get("pattern") or ""
            if pattern:
                per_pattern[pattern] = per_pattern.get(pattern, 0) + 1
    census: dict = {}
    for pattern, mutants in sorted(per_pattern.items()):
        try:
            suite = _ut.TestLoader().discover(start_dir=str(root / "tests"),
                                              top_level_dir=str(root / "tests"), pattern=pattern)
        except Exception:                       # noqa: BLE001 — unloadable is not "skipped"
            census[pattern] = (0, 0, mutants)
            continue
        total = skipped = 0
        stack = [suite]
        while stack:
            node = stack.pop()
            if isinstance(node, _ut.TestSuite):
                stack.extend(list(node))
                continue
            total += 1
            method = getattr(node, node._testMethodName, None)
            if (getattr(method, "__unittest_skip__", False)
                    or getattr(node.__class__, "__unittest_skip__", False)):
                skipped += 1
        census[pattern] = (skipped, total, mutants)
    return census


def _report(results) -> str:
    swept = sum(len(r.records) for r in results)
    offenders = [(r.driver, o) for r in results for o in r.offenders]
    noops = [(r.driver, o) for r in results for o in r.no_op_mutations]
    lines = [
        f"drivers swept              {len(results)}",
        f"mutants swept              {swept}",
        f"patterns that DO NOT apply {len(offenders)}",
        f"no-op mutations (old==new) {len(noops)}",
        "",
    ]
    for driver, o in offenders:
        lines.append(f"  {driver:44s} {o['label'][:52]:52s} "
                     f"{o['occurrences']:>3} in {o['target']}")
    asleep = [(p, c) for p, c in grader_skip_census(results).items() if c[0]]
    if asleep:
        lines.append("")
        lines.append("graders ASLEEP on this host — a SURVIVOR under one of these is a finding")
        lines.append("about the runner, not about the pin (reported, never gated — see the census):")
        for pattern, (sk, tot, mut) in sorted(asleep, key=lambda x: -x[1][0] / (x[1][1] or 1)):
            lines.append(f"  {100 * sk / (tot or 1):5.1f}%  {sk:>4}/{tot:<6} graders asleep "
                         f"· {mut:>3} mutant(s) ride on  -p {pattern}")
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover
    res = run_sweep()
    print(_report(res))
    print("\n--- declared vs swept (a gap is a driver that never reached the end of itself) ---")
    for name, declared, swept, rc in reconcile(res):
        flag = "" if declared == swept else "   <-- GAP"
        if declared is None:
            flag = "   <-- DECLARES NO TOTAL"
        print(f"  {name:46s} declared={str(declared):>5s} swept={swept:>4d} rc={rc:>4d}{flag}")
    if len(sys.argv) > 1:
        with open(sys.argv[1], "w", encoding="utf-8") as fh:
            json.dump([{"driver": r.driver, "returncode": r.returncode,
                        "records": r.records, "stdout": r.stdout} for r in res], fh)
