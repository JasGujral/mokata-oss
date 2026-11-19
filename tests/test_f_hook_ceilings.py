"""0.0.29 F (ALLOWED-REDS-A12-A9-O12-O12B, HOOK-BUDGET-CI-RELAXED; matrix #10) — every hook-budget
ceiling is DERIVED from a committed measurement, never typed — per OS since the wave-2 bookend.

What must be true for the user:
  * Each wired hook, on each fixture, on each OS, has ONE ceiling derived from the worst normalised
    p95 measured for it ON THAT OS (`tests/data/hook_budget_measured.json`): darwin 1.5x rounded up
    to 5 ms, never above its kind's target unless an owned gap with a doc 84 row (so a 2x regression
    reds); linux / win32 the same 1.5x, never below the darwin ceiling x `perf.OFF_DARWIN_BUFFER`.
  * A ceiling raised by hand, a key or an OS with no measurement, a measurement with no ceiling, or
    a darwin gap above its target with no owner reds here.
  * No CI relax: `perf.margin_relax()` is 1.

Re-measure: run `test_hb_hook_budget` (each timed case prints an `[HB-MEASURE]` row with its OS) on
every OS, then `python tests/test_f_hook_ceilings.py --from <log> [<log> ...]` rebuilds the data
file from exactly those logs and prints the `HOOK_BUDGET_CEILINGS_MS` body to paste into `perf.py`.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""
from __future__ import annotations

import json
import os
import sys
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import perf

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "hook_budget_measured.json")
FIXTURES = ("outside", "repo", "bound")
EIGHT_MIB = tuple(f"{f}-8mib-{c}" for f in ("bound", "repo") for c in ("miss", "hit"))
#: Every OS the mirror matrix runs, each with its own measured column (none is borrowed).
OSES = ("darwin", "linux", "win32")
OFF = ("linux", "win32")


def wired_keys():
    keys = {f"{sub}@{fx}" for sub in perf.HOOK_KINDS for fx in FIXTURES}
    keys |= {f"session-start@{k}" for k in EIGHT_MIB}
    return keys


def _target(key):
    return perf.HOOK_BUDGET_TARGETS_MS[perf.HOOK_KINDS[key.split("@")[0]]]


def derive(measured):
    """`{os: {key: ceiling}}` from `{key: {os: p95}}` — the rule, in one place: darwin by
    `perf.ceiling_from` (capped at the target unless an owned gap), every other OS by
    `perf.ceiling_off_darwin` over that OS's own worst and the darwin ceiling."""
    darwin = {k: perf.ceiling_from(per_os["darwin"], _target(k),
                                   owned=k in perf.HOOK_BUDGET_OWNED_GAPS)
              for k, per_os in measured.items()}
    out = {"darwin": darwin}
    for osn in OFF:
        out[osn] = {k: perf.ceiling_off_darwin(per_os[osn], darwin[k])
                    for k, per_os in measured.items()}
    return out


def _load():
    with open(DATA, encoding="utf-8") as fh:
        return json.load(fh)


class EveryCeilingIsDerived(unittest.TestCase):

    def test_every_wired_key_is_measured_on_every_os_and_has_its_derived_ceiling(self):
        data = _load()
        measured = data["measured"]
        self.assertEqual(wired_keys(), set(measured), "a wired hook with no measurement, or a "
                                                      "measurement for no wired hook")
        for k, per_os in measured.items():
            self.assertEqual(set(OSES), set(per_os), f"{k}: an OS with no measurement")
        self.assertEqual(set(OSES), set(perf.HOOK_BUDGET_CEILINGS_MS))
        self.assertEqual(derive(measured), perf.HOOK_BUDGET_CEILINGS_MS,
                         "perf.HOOK_BUDGET_CEILINGS_MS is not what the measurements derive")

    def test_no_ceiling_exceeds_twice_its_worst_measurement_but_by_the_floor(self):
        """darwin: every ceiling <= 2x its worst p95. Off darwin the same, unless the excess IS
        the floor (darwin ceiling x OFF_DARWIN_BUFFER) — the ruling, named, never a typed raise."""
        measured = _load()["measured"]
        cols = perf.HOOK_BUDGET_CEILINGS_MS
        for osn in OSES:
            for k, ceiling in cols[osn].items():
                with self.subTest(os=osn, key=k):
                    if ceiling > 2 * measured[k][osn]:
                        self.assertNotEqual("darwin", osn)
                        self.assertEqual(cols["darwin"][k] * perf.OFF_DARWIN_BUFFER, ceiling)

    def test_a_darwin_ceiling_above_its_target_is_an_owned_gap(self):
        for k, ceiling in perf.HOOK_BUDGET_CEILINGS_MS["darwin"].items():
            if ceiling > _target(k):
                self.assertIn(k, perf.HOOK_BUDGET_OWNED_GAPS, k)
        for k in perf.HOOK_BUDGET_OWNED_GAPS:
            self.assertIn(k, perf.HOOK_BUDGET_CEILINGS_MS["darwin"],
                          f"an owned gap nobody measures: {k}")

    def test_hook_budget_ms_answers_this_os_column(self):
        from unittest import mock
        for osn in OSES:
            for k, ceiling in perf.HOOK_BUDGET_CEILINGS_MS[osn].items():
                sub, fx = k.split("@")
                with self.subTest(os=osn, key=k), mock.patch.object(sys, "platform", osn):
                    self.assertEqual((ceiling, ceiling == _target(k)), perf.hook_budget_ms(sub, fx))

    def test_no_ci_relax(self):
        from unittest import mock
        with mock.patch.dict(os.environ, {"CI": "true"}):
            self.assertEqual(1.0, perf.margin_relax())
        self.assertEqual(0.9, perf.MARGIN)
        self.assertEqual(1000.0, perf.FIRST_RUN_BUDGET_MS)


class TheCeilingsAreMeasuredPerOs(unittest.TestCase):
    """force — ruled by Jas 2026-11-16, applied at the 0.0.29 wave-2 bookend: the mirror's CI runs,
    so every OS is MEASURED (a Mac + the macOS runner; the Linux and Windows runners, both
    jsonschema legs) and graded by its own column. Off darwin no ceiling is below the darwin ceiling
    x `perf.OFF_DARWIN_BUFFER` (2.0). No skip, no relax.

    ⚠ WHAT EACH OS CATCHES, stated: darwin reds a 2x regression of its worst measured p95. Off
    darwin the floor keeps the gate at >= 2x the darwin CEILING, so there a regression reds past
    3x the larger of that OS's worst and darwin's worst (the floor's 2 x 1.5), plus 10 ms of the
    floor's 5 ms rounding doubled — not at 2x."""

    def test_every_os_was_measured_and_says_by_which_runs(self):
        data = _load()
        self.assertEqual(list(OSES), data["os_measured"])
        self.assertEqual([], data["os_not_measured"])
        for osn in OSES:
            self.assertTrue(data["sources"][osn], f"{osn}: no source run recorded")
        self.assertEqual(2.0, perf.OFF_DARWIN_BUFFER)

    def test_darwin_grades_at_its_ceiling_and_reds_2x_of_measured(self):
        from unittest import mock
        measured = _load()["measured"]
        for k, ceiling in perf.HOOK_BUDGET_CEILINGS_MS["darwin"].items():
            sub, fx = k.split("@")
            worst = measured[k]["darwin"]
            with self.subTest(key=k), mock.patch.object(sys, "platform", "darwin"):
                self.assertEqual(ceiling, perf.hook_budget_ms(sub, fx)[0])
                self.assertTrue(perf.hook_budget_holds(sub, fx, worst))
                self.assertFalse(perf.hook_budget_holds(sub, fx, 2 * worst),
                                 "a 2x regression passes the gate on darwin")

    def test_off_darwin_grades_at_its_own_column_never_below_the_floor_and_reds_3x(self):
        from unittest import mock
        measured = _load()["measured"]
        cols = perf.HOOK_BUDGET_CEILINGS_MS
        for osn in OFF:
            for k, ceiling in cols[osn].items():
                sub, fx = k.split("@")
                worst = measured[k][osn]
                with self.subTest(os=osn, key=k), mock.patch.object(sys, "platform", osn):
                    self.assertEqual(ceiling, perf.hook_budget_ms(sub, fx)[0])
                    self.assertGreaterEqual(ceiling, cols["darwin"][k] * 2.0, "below the floor")
                    self.assertTrue(perf.hook_budget_holds(sub, fx, worst))
                    self.assertFalse(perf.hook_budget_holds(
                        sub, fx, 3 * max(worst, measured[k]["darwin"]) + 2 * perf.CEILING_STEP_MS),
                        "a 3x regression (+ the floor's rounding) passes the gate on this OS")

    def test_an_unmeasured_platform_is_graded_at_darwin_times_the_buffer(self):
        from unittest import mock
        for k, ceiling in perf.HOOK_BUDGET_CEILINGS_MS["darwin"].items():
            sub, fx = k.split("@")
            with self.subTest(key=k), mock.patch.object(sys, "platform", "freebsd14"):
                self.assertEqual(ceiling * 2.0, perf.hook_budget_ms(sub, fx)[0])
                self.assertFalse(perf.hook_budget_holds(sub, fx, ceiling * 2.0 + 1))


def _from_logs(paths, default_os):
    """`({key: {os: worst p95_norm}}, {key: {os: raw p95 of that batch}}, {os: [source]})` from
    logs of `[HB-MEASURE]` rows. A row says its OS (`os`); a CI log's line names its leg before
    ` | `, and the source is recorded as `<log>` or `<log> :: <leg>`."""
    measured, raw, sources = {}, {}, {}
    for path in paths:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if "[HB-MEASURE] " not in line:
                    continue
                head, body = line.split("[HB-MEASURE] ", 1)
                row = json.loads(body)
                osn = row.get("os") or default_os
                key = row["key"].split(":", 1)[0]
                leg = head.split(" | ")[0].strip() if " | " in head else ""
                src = f"{os.path.basename(path)} :: {leg}" if leg else os.path.basename(path)
                if src not in sources.setdefault(osn, []):
                    sources[osn].append(src)
                v = float(row["p95_norm"])
                if v > measured.setdefault(key, {}).get(osn, 0.0):
                    measured[key][osn] = v
                    best = min(row["batches"])
                    raw.setdefault(key, {})[osn] = best[1]
    return measured, raw, sources


if __name__ == "__main__" and "--from" in sys.argv:
    # python tests/test_f_hook_ceilings.py --from <log> [<log> ...] [--os <platform>]
    # Rebuilds the data file from exactly these logs (a re-measure is reproducible from what is
    # named) and prints the HOOK_BUDGET_CEILINGS_MS body to paste into perf.py.
    args = sys.argv[sys.argv.index("--from") + 1:]
    osn = sys.platform
    if "--os" in args:
        osn = args[args.index("--os") + 1]
        args = args[:args.index("--os")]
    try:
        data = _load()
    except OSError:
        data = {}
    measured, raw, sources = _from_logs(args, osn)
    data.update(measured=measured, raw_p95_of_the_graded_batch=raw,
                sources={k: sources.get(k, []) for k in OSES},
                os_measured=[k for k in OSES if sources.get(k)],
                os_not_measured=[k for k in OSES if not sources.get(k)])
    with open(DATA, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
        fh.write("\n")
    for o, col in derive(measured).items():
        print(f'    "{o}": {{')
        for k, v in sorted(col.items()):
            print(f'        "{k}": {v},')
        print("    },")
elif __name__ == "__main__":
    unittest.main()
