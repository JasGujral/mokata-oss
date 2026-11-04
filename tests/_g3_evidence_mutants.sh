#!/usr/bin/env bash
# Drives the 0.0.27 G3-EVIDENCE stage through scripts/mutate.sh — the ONLY sanctioned mutator
# (doc 85 §7b).
#
#   PYTHON=/path/to/venv/bin/python tests/_g3_evidence_mutants.sh
#
# WHAT THIS BATCH GRADES: the rule note v2 mutant list. Each mutant hands the PR check a way to
# overclaim — a committed record that grades, a cut section, a silent omission, a live link out of
# the repo, an exported field nobody approved — and the tests must red on every one.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"
PY="${PYTHON:-python3}"

RR=src/mokata/run_record.py
CE=src/mokata/ci_evidence.py
CC=src/mokata/ci_check.py
SG=src/mokata/engine/spec_gate.py
EN=src/mokata/govern/enforce.py
T='test_g3_evidence.py'
TS='test_si_6_writegate_side_doors.py'

TOTAL=32
ran=0; red=0; green=0; survivors=""

printf '================================================================================\n'
printf 'STEP 0 — GREEN BASELINE (no mutation applied). Nothing is graded until this passes.\n'
printf '================================================================================\n'
for t in "$T" "$TS"; do
    if ! PYTHONDONTWRITEBYTECODE=1 "$PY" -m unittest discover -s tests -t tests -p "$t" >/dev/null 2>&1
    then
        printf '\nBATCH REFUSED — %s is not green before mutant 1.\n' "$t"
        exit 75
    fi
done
printf 'Baseline GREEN. Grading %s mutants.\n\n' "$TOTAL"

mutant() {
    local label="$1" rc=0 out
    ran=$((ran + 1))
    out="$("$M" "$@")" || rc=$?
    if [ -n "$out" ]; then printf '%s\n' "$out"; fi
    if [ "$rc" -ne 0 ]; then
        printf '\nBATCH ABORTED — mutate.sh exited %s on mutant %s of %s\n' "$rc" "$ran" "$TOTAL"
        printf '  mutant: %s\n' "$label"
        printf '  %s of %s mutants NEVER RAN. THIS BATCH DID NOT PASS.\n' "$((TOTAL - ran))" "$TOTAL"
        exit "$rc"
    fi
    case "$out" in
        RED*)   red=$((red + 1)) ;;
        GREEN*) green=$((green + 1)); survivors="$survivors  - $label"$'\n' ;;
        *)      printf '\nBATCH ABORTED — exit 0 with no verdict on mutant %s of %s (%s): %s\n' \
                    "$ran" "$TOTAL" "$label" "${out:-<nothing>}"; exit 70 ;;
    esac
}

mutant "G01 the changed-files filter is dropped: every record in the repo is this PR's" "$RR" \
  '    touched = [n for n in _touched_dirs(changed_files) if n in existing]' \
  '    touched = list(existing)' "$T"
mutant "G02 a committed 'block' verdict feeds the PR's blocked state" "$CC" \
  '        return any(leg.blocked for leg in self.legs)' \
  '        return any(leg.blocked for leg in self.legs) or bool(self.records is not None and any(e.get("verdict") == "block" for r in self.records.records for e in r.evidence))' "$T"
mutant "G03 realpath containment dropped: a symlink out of the repo is linked" "$CE" \
  '    if not (full == real_root or full.startswith(real_root.rstrip(os.sep) + os.sep)):' \
  '    if False:' "$T"
mutant "G04 a fixed three-backtick fence" "$CE" \
  '    n = max(3, (max(len(r) for r in runs) if runs else 0) + 1)' \
  '    n = 3' "$T"
mutant "G05 the fence equals the longest run (off by one)" "$CE" \
  '    n = max(3, (max(len(r) for r in runs) if runs else 0) + 1)' \
  '    n = max(3, (max(len(r) for r in runs) if runs else 0) + 0)' "$T"
mutant "G06 scalars are not redacted on render" "$CE" \
  '    text, n = redact(text)' \
  '    n = 0' "$T"
mutant "G07 an over-budget section is trimmed partway instead of left out whole" "$CE" \
  '        if used + cost + reserve > limit:
            break' \
  '        if used + cost + reserve > limit:
            out += ["", s.md[: max(0, limit - used - reserve - 2)]]
            break' "$T"
mutant "G08 the disclosure block is dropped" "$CE" \
  '    if disclosure:' \
  '    if False:' "$T"
mutant "G09 the disclosure lists every omitted section (grows per item)" "$CE" \
  '    for (rank, kind), n in sorted(counts.items()):' \
  '    for (rank, kind), n in [((s.rank, s.kind), 1) for s in omitted]:' "$T"
mutant "G10 a symlinked .mokata/runs reads as NOT_OPTED_IN (merged with UNREADABLE)" "$RR" \
  '        return RunRecords(PRESENT, [RunRecord(dirname=RUNS_DIRNAME, state=UNREADABLE,
                                              reason=".mokata/runs is a symlink (not followed)")])' \
  '        return RunRecords(NOT_OPTED_IN)' "$T"
mutant "G11 NO_FILE_LIST merged into NONE_IN_PR" "$RR" \
  '        return RunRecords(NO_FILE_LIST)' \
  '        return RunRecords(NONE_IN_PR)' "$T"
mutant "G12 an UNREADABLE record does not set degraded" "$RR" \
  '        return any(r.state != OK for r in self.records)' \
  '        return any(r.state == INCONSISTENT for r in self.records)' "$T"
mutant "G13 run= removed from a record site (the spec gate)" "$SG" \
  'phase=phase, run=run_id,' \
  'phase=phase,' "$T"
mutant "G14 a constant replaces current_run_id at a record site (the advisory gate)" "$EN" \
  '    return current_run_id()' \
  '    return "run"' "$T"
mutant "G15 the untrusted label is rendered in <summary>" "$CE" \
  '<details><summary>Evidence: content #{counter[0]}</summary>' \
  '<details><summary>{_clean(label)}</summary>' "$T"
mutant "G16 the [committed, unverified] mark is removed" "$CE" \
  '    mark = f"{code_span(UNVERIFIED)} " if committed else ""' \
  '    mark = ""' "$T"
mutant "G17 sensitive paths are no longer refused by name" "$CE" \
  '    if path and sensitive(path):' \
  '    if False:' "$T"
mutant "G18 the #L line suffix is not validated" "$CE" \
  '        if not (line.isdigit() and len(line) <= _LINE_MAX_DIGITS) or not line.isascii():' \
  '        if False:' "$T"
mutant "G19 the order is reversed: failing sections are omitted first" "$CE" \
  '    sections.sort(key=lambda s: s.rank)' \
  '    sections.sort(key=lambda s: -s.rank)' "$T"
mutant "G20 the write-time field allowlist is removed" "$RR" \
  '        for name in FIELDS[entry["kind"]]:' \
  '        for name in entry:' "$T"
mutant "G21 the verdict section is dropped when over budget" "$CE" \
  '        verdict = verdict_counts' \
  '        verdict = ""' "$T"
mutant "G22 a symlinked record dir is followed" "$RR" \
  '    if name in (".", "..") or os.sep in name or _is_link(d):' \
  '    if name in (".", ".."):' "$T"
mutant "G23 the record dir name is not case-normalized" "$RR" \
  '    name = safe_run_dirname(run_id).lower()' \
  '    name = safe_run_dirname(run_id)' "$T"
mutant "G24 an ungated writer under .mokata/runs/ (meta.json before the gate)" "$RR" \
  '    emit(content)
' \
  '    emit(content)
    target_dir = os.path.join(runs_dir(root), plan.dirname)
    os.makedirs(target_dir, exist_ok=True)
    with open(os.path.join(target_dir, META_FILE), "w", encoding="utf-8") as _fh:
        _fh.write(plan.files[META_FILE])
' "$TS"
mutant "G25 a committed spec title in the verdict detail renders raw (pings @org/team)" "$CC" \
  "                lines.append(f\"  - {code_span(d[2:] if d.startswith('- ') else d)}\")" \
  "                lines.append(f\"  - {d[2:] if d.startswith('- ') else d}\")" "$T"

# ---- round 3 (verifier NOT GO: M1, M2, two survivors)
TE=src/mokata/mcp/tools_evidence.py
mutant "G26 M1 the MCP redeem re-renders (exported_at not frozen from the approved preview)" "$TE" \
  '            if frozen:' \
  '            if False:' "$T"
mutant "G27 M1 a source changed since the preview is not refused by name" "$TE" \
  '            and approved.preview != plan.preview():' \
  '            and False:' "$T"
mutant "G28 M2 the commit closure follows a link swapped in after the preview" "$RR" \
  '        d = _record_dir(root, plan.dirname, create=True)' \
  '        from .explain.safedir import open_chain as _oc
        os.makedirs(runs_dir(root), exist_ok=True)
        d = _oc(os.path.realpath(runs_dir(root)), [plan.dirname], create=True)' "$T"
mutant "G29 M2 no refusal before the preview (an unwritable path is shown for approval)" "$RR" \
  '        _record_dir(root, plan.dirname, create=False)' \
  '        pass' "$T"
mutant "G30 INCONSISTENT ignores a hand-deleted evidence record" "$RR" \
  '            or meta.get("evidence_exported") != len(rec.evidence):' \
  '            or False:' "$T"
mutant "G31 the Windows C:\\Users home is not folded" "$RR" \
  '[A-Za-z]:\\Users\\' \
  '[A-Za-z]:\\NoUsers\\' "$T"
mutant "G32 M2 a record FILE link swapped in after the preview is replaced, not refused" "$RR" \
  '                st = held._lstat(name)
                if st is not None and (_st_is_link(st) or not stat.S_ISREG(st.st_mode)):' \
  '                st = held._lstat(name)
                if False:' "$T"

printf '\n================================================================================\n'
printf 'G3-EVIDENCE MUTANTS: %s ran of %s — %s RED / %s GREEN\n' "$ran" "$TOTAL" "$red" "$green"
if [ "$ran" -ne "$TOTAL" ]; then
    printf 'BATCH INCOMPLETE — %s of %s never ran. Not a score.\n' "$((TOTAL - ran))" "$TOTAL"
    printf '================================================================================\n'
    exit 71
fi
if [ -n "$survivors" ]; then
    printf 'SURVIVORS (each is a pin that does not grade):\n%s' "$survivors"
    printf '================================================================================\n'
    exit 1
fi
printf 'Every mutant was caught.\n'
printf '================================================================================\n'
