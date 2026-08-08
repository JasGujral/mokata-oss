#!/usr/bin/env bash
# Drives the mutant list in `_writegraph_mutants.txt` through scripts/mutate.sh — the ONLY
# sanctioned mutator (doc 85 §7b). Never hand-edit a file to see whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python tests/_run_mutants.sh
#
# ---------------------------------------------------------------------------------------------
# WHY THIS FILE INSPECTS AN EXIT STATUS, AND WHY A HARNESS FAILURE STOPS THE BATCH DEAD
# (0.0.17 stage 18b — RUN-MUTANTS-IGNORES-EXIT-CODES)
#
# Until stage 18b this file ran `set -uo pipefail` — no `-e` — and invoked the mutator 26 times
# without ever looking at a status. Measured against a stub: for EVERY failure mode mutate.sh
# has (3, 4, 5, 6) the batch ran all 26 mutants and exited 0. Every guard in mutate.sh's header
# was, from this file's point of view, a comment.
#
# ON AN EXIT 4 THAT IS ACTIVELY DANGEROUS, not merely quiet. `mutate.sh` refuses to restore its
# snapshot precisely so that a third party's edit survives — which means it LEAVES THE TARGET
# MUTATED, on purpose. Carrying on past that grades every remaining mutant against a tree holding
# an uncontrolled edit: a compound mutant nobody designed, reported with a straight face. That is
# hole 3 in mutate.sh's header, arriving one layer up where hole 3's trap cannot see it.
#
# SO THE RULE HERE IS THE MIRROR OF THE MUTATOR'S CONTRACT:
#
#     a VERDICT (RED / GREEN) is a completed run  ->  count it, carry on.
#     a HARNESS FAILURE (non-zero)                ->  STOP. Nothing after this is trustworthy.
#
# A SURVIVOR IS NOT A FAILURE, and confusing the two would be a worse defect than the one this
# stage fixed. `GREEN ... <-- SURVIVOR` means the mutation was not caught — a real finding about
# a real pin, and the remaining mutants are still worth grading. It is reported, named again in
# the summary so it cannot be lost in 26 lines of log, and the batch continues.
#
# AND A BATCH THAT STOPS EARLY MUST NEVER READ AS A BATCH THAT PASSED. The abort names the mutant
# it died on and how many of the list never ran, and it prints to STDOUT rather than stderr on
# purpose: a batch log that captures only stdout would otherwise simply end, mid-list, looking
# indistinguishable from a run that covered everything.
#
# `set -e` is deliberately NOT used. Every status is checked explicitly and at exactly one place
# (`mutant`), which is what lets the failure be EXPLAINED rather than merely fatal — the same
# reasoning as hole 4 in mutate.sh's header.
# ---------------------------------------------------------------------------------------------
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
# The mutator is overridable for the SAME reason `PYTHON` is: so this driver's own self-tests can
# drive all 26 call sites against a stub returning a chosen exit code in milliseconds, instead of
# running 26 real mutation passes. `mutate.sh`'s tests already use this idiom (`MUTATE_LOCKFILE`,
# `MUTATE_TESTS_DIR`, `MUTATE_LOCK_IMPL`). Real runs get the default.
M="${MUTATE_SH:-scripts/mutate.sh}"
P=tests/test_si_6_writegate_side_doors.py
G=tests/_writegraph.py
T='test_si_6*.py'

# The number of `mutant` call sites below. Pinned against the real count by
# `test_mutant_batch_driver.TestTheAccountingCannotDrift`, because "19 of 26 never ran" is only
# true if 26 is true — a batch that overstates its own list is the silent-truncation shape doc 85
# warns about, wearing the costume of an honest abort.
TOTAL=26

ran=0
red=0
green=0
survivors=""

abort() {
    local rc="$1" label="$2" meaning remedy
    # Every code below is one scripts/mutate.sh's EXIT CONTRACT documents; that header is the
    # single source and this is its consumer. `test_mutant_batch_driver` fails if this dispatch
    # ever explains a code the contract does not define.
    case "$rc" in
        1) meaning="USAGE OR ENVIRONMENT — a bad argument, or a target that could not be copied."
           remedy="  Nothing was touched. Fix the invocation for this mutant and re-run." ;;
        2) meaning="LOCK MISCONFIGURED — MUTATE_LOCK_IMPL is not auto|flock|pidfile, or flock is absent."
           remedy="  Nothing was touched. Fix MUTATE_LOCK_IMPL and re-run." ;;
        3) meaning="THE MUTATION COULD NOT BE APPLIED — the pattern matched no site, or several."
           remedy="  The tree is RESTORED, so nothing is contaminated. This mutant's old/new strings
  have drifted from the source they describe; re-derive them and re-run." ;;
        4) meaning="REFUSED RESTORE — something wrote to the target while the run held it snapshotted."
           remedy="  ★ THE WORKING TREE IS STILL MUTATED, on purpose. mutate.sh refused to restore so
  that the other edit survived, and left BOTH copies: the target as they wrote it, and the
  pristine source beside it as <file>.REFUSED-RESTORE*.bak. Its message above names the file
  and both hashes.
  REMEDY: diff the target against its .REFUSED-RESTORE*.bak, keep whichever content you want,
  delete the .bak, check 'git diff' says what you expect, and only then re-run the batch." ;;
        5) meaning="LOCK BUSY — another mutate.sh run holds the mutation lock."
           remedy="  Nothing was touched. Two runs interleaving is exactly how one run's snapshot gets
  restored over the other's work, so this one refused to start rather than queue.
  REMEDY: wait for the other run to report, or kill it and remove its lockfile, then re-run." ;;
        6) meaning="NO VERDICT — the mutation applied but the test run produced nothing to grade."
           remedy="  The tree is RESTORED. Either the test pattern matched no files, or discovery never
  reported at all. Either way nothing was exercised, so this mutant is UNGRADED — it is not a
  survivor. Fix the pattern (or the tests dir) and re-run." ;;
        *) meaning="UNKNOWN STATUS $rc — not in scripts/mutate.sh's EXIT CONTRACT."
           remedy="  Treat the tree as SUSPECT: check 'git status' and 'git diff' before doing anything
  else, and reconcile this status with the contract in scripts/mutate.sh's header." ;;
    esac
    # stdout, not stderr — see the header. A batch log that captures only stdout must not be able
    # to end mid-list looking like a completed run.
    printf '\n'
    printf '================================================================================\n'
    printf 'BATCH ABORTED — mutate.sh exited %s on mutant %s of %s\n' "$rc" "$ran" "$TOTAL"
    printf '  mutant: %s\n' "$label"
    printf '  status: %s\n' "$meaning"
    printf '%s\n' "$remedy"
    printf '\n'
    printf '  %s of %s mutants NEVER RAN. THIS BATCH DID NOT PASS — it stopped here.\n' \
        "$((TOTAL - ran))" "$TOTAL"
    printf '  A harness failure means NO VERDICT was produced and the tree may not hold what this\n'
    printf '  run believes it holds, so every grade after this point would have been read off a\n'
    printf '  tree nothing can vouch for.\n'
    printf '================================================================================\n'
    exit "$rc"
}

mutant() {
    local label="$1" rc=0 out
    ran=$((ran + 1))
    # stdout is captured so the verdict can be COUNTED and so a status-0 run carrying no verdict
    # is noticed — then re-emitted immediately, so the batch log reads exactly as it always did.
    # stderr is deliberately NOT captured: mutate.sh's REFUSED-RESTORE and LOCK-BUSY blocks go
    # straight to the terminal, where they are the first thing the reader needs.
    out="$("$M" "$@")" || rc=$?
    if [ -n "$out" ]; then printf '%s\n' "$out"; fi

    if [ "$rc" -ne 0 ]; then abort "$rc" "$label"; fi

    case "$out" in
        RED*)   red=$((red + 1)) ;;
        GREEN*) green=$((green + 1)); survivors="$survivors  - $label"$'\n' ;;
        *)      # Status 0 is a CLAIM that a verdict exists. If the mutator ever drifts — a new
                # branch that prints something else and falls off the end at 0 — counting that
                # line as a graded mutant is the very laundering every hole in mutate.sh's header
                # turned out to be. 70 rather than a mutate.sh code: this is the driver's finding.
                printf '\n'
                printf 'BATCH ABORTED — mutate.sh exited 0 for mutant %s of %s but printed no verdict.\n' \
                    "$ran" "$TOTAL"
                printf '  mutant: %s\n' "$label"
                printf '  said  : %s\n' "${out:-<nothing at all>}"
                printf '  Exit 0 means "a RED or GREEN verdict was produced" (scripts/mutate.sh, EXIT\n'
                printf '  CONTRACT). This run claimed one and did not produce it, so it is UNGRADED and\n'
                printf '  the contract the rest of this batch relies on no longer holds.\n'
                printf '  %s of %s mutants NEVER RAN. THIS BATCH DID NOT PASS — it stopped here.\n' \
                    "$((TOTAL - ran))" "$TOTAL"
                exit 70 ;;
    esac
}

mutant "M01 sweep key -> bare innermost name" "$P" \
  'key = (self.rel, ".".join(self.stack) if self.stack else "<module>")' \
  'key = (self.rel, self.stack[-1] if self.stack else "<module>")' "$T"

mutant "M02 resolver _qual -> bare innermost name" "$G" \
  'return ".".join(n for n, _ in self.stack) if self.stack else "<module>"' \
  'return self.stack[-1][0] if self.stack else "<module>"' "$T"

mutant "M03 def_counts frozen at 1" "$G" \
  'facts.def_counts[qual] = facts.def_counts.get(qual, 0) + 1' \
  'facts.def_counts[qual] = 1' "$T"

mutant "M04 collision threshold n>1 -> n>0" "$G" \
  'for qual, n in f.def_counts.items() if n > 1}' \
  'for qual, n in f.def_counts.items() if n > 0}' "$T"

mutant "M05 rule (e) disabled" "$G" \
  'held = self._binding(base)                     # (e) a local holding a mokata instance' \
  'held = None                                    # (e) a local holding a mokata instance' "$T"

mutant "M06 rule (e) drops the defines-the-method guard" "$G" \
  'if fn.attr in self.facts[trel].classes.get(cls, set()):' \
  'if fn.attr in self.facts[trel].classes.get(cls, set()) | {fn.attr}:' "$T"

mutant "M07 _binding truthiness — a stale binding survives a rebind" "$G" \
  'if name in self.frames[j]:' \
  'if self.frames[j].get(name):' "$T"

mutant "M08 rule (a) climbs through class scopes" "$G" \
  'if i and self.stack[i - 1][1]:          # that candidate'"'"'s parent scope is a class' \
  'if False:                               # that candidate'"'"'s parent scope is a class' "$T"

mutant "M09 _enclosing_class ignores the is_class flag" "$G" \
  'if self.stack[i][1]:' 'if True:' "$T"

mutant "M10 blind-spot declaration renders no shapes" "$P" \
  'lines += [f"  - {shape}" for shape in _writegraph.UNCLOSED_SHAPES]' \
  'lines += []' "$T"

mutant "M11 collision count hard-coded to 0" "$P" \
  'lines.append(f"  KEY COLLISIONS still live in src/ ({len(collisions)}): "' \
  'lines.append(f"  KEY COLLISIONS still live in src/ (0): "' "$T"

mutant "M12 the audit report omits the declaration" "$P" \
  'lines.append(_declared_blind_spots(_graph.collisions))' \
  'lines.append("")' "$T"

# M13 was added after M07's first-pass survival, to pin the OTHER half of the same rule: M07 is
# "a lookup walks outwards past a not-a-constructor binding", M13 is "an assignment fails to
# record one". Same invariant — a binding must die where it is overwritten — two ways to break it.
mutant "M13 a rebind keeps the stale binding instead of clearing it" "$G" \
  'self.frames[-1][tgt.id] = bound' \
  'self.frames[-1][tgt.id] = bound or self.frames[-1].get(tgt.id)' "$T"

mutant "M14 rule (e) offers a class-BODY binding to a method body" "$G" \
  'if j and self.stack[j - 1][1]:          # that frame is a CLASS body' \
  'if False:                               # that frame is a CLASS body' "$T"

# ---------------------------------------------------------------------------------------------
# 0.0.17 STAGE 1a-FU — the NEW logic: derived caller lists (CALLER-LIST-UNPINNED), gate enclosure,
# and the coherence contract's second direction (COHERENCE-CONTRACT-ONE-DIRECTIONAL).
# ---------------------------------------------------------------------------------------------

mutant "M15 derive_callers returns an empty caller list for every site" "$G" \
  'return {site: tuple(sorted(rev.get(site, ()))) for site in sorted(set(sites))}' \
  'return {site: () for site in sorted(set(sites))}' "$T"

mutant "M16 is_gate_run answers TRUE for every call site" "$G" \
  'return owner is not None and (rel, owner) in self.gate_run' \
  'return True' "$T"

mutant "M17 gate_run: 'reached from a commit span at least ONCE' instead of ALWAYS" "$G" \
  'enclosure.gate_run = frozenset(t for t, seen in reached.items() if all(seen))' \
  'enclosure.gate_run = frozenset(t for t, seen in reached.items() if any(seen))' "$T"

mutant "M18 direction B never consults the enclosure" "$G" \
  'if not enclosure.is_gate_run(rel, lineno):' \
  'if False:' "$T"

mutant "M19 direction B excuses every declared site, not the declared exceptions" "$G" \
  'skip, out = set(excused), []' \
  'skip, out = set(declared), []' "$T"

mutant "M20 commit spans drop the positional-lambda runner half (tools_share.py:59)" "$G" \
  'if name in _RUNNER_NAMES:' \
  'if False:' "$T"

mutant "M21 commit spans drop the commit= keyword half" "$G" \
  "if kw.arg == \"commit\":" \
  'if False:' "$T"

mutant "M22 rule (e)'s edge LIST is not recorded, only its count" "$G" \
  'self.result.receiver_edges.append(record)' \
  'pass' "$T"

mutant "M23 the call-name index records nothing (declared-caller drift undetectable)" "$G" \
  'calls.setdefault(child.lineno, set()).add(name)' \
  'pass' "$T"

mutant "M24 a def OWNS only its own line, not its body" "$G" \
  'getattr(child, "end_lineno", child.lineno) + 1):' \
  'child.lineno + 1):' "$T"

mutant "M25 the declared DIRECTIONS render as nothing" "$P" \
  'lines += [f"  - {d}" for d in _writegraph.CONTRACT_DIRECTIONS]' \
  'lines += []' "$T"

mutant "M26 the declared UNSEEN-ENCLOSURE limits render as nothing" "$P" \
  'lines += [f"  - NOT ESTABLISHED: {u}" for u in _writegraph.UNSEEN_ENCLOSURE]' \
  'lines += []' "$T"

# A batch that quietly grades fewer mutants than it declares is the same defect as one that
# quietly carries on past a harness failure: a number that shrank looks identical to a number
# that was always that size.
if [ "$ran" -ne "$TOTAL" ]; then
    printf '\n'
    printf 'BATCH ABORTED — %s mutants ran but this driver declares TOTAL=%s.\n' "$ran" "$TOTAL"
    printf '  Every "N of %s never ran" line this file can print is wrong by the difference, and\n' "$TOTAL"
    printf '  a batch reporting more coverage than it has is exactly what stage 18b set out to fix.\n'
    exit 70
fi

printf '\n'
printf '================================================================================\n'
printf 'BATCH COMPLETE — %s/%s mutants ran, every one produced a verdict · %s RED · %s GREEN\n' \
    "$ran" "$TOTAL" "$red" "$green"
if [ -n "$survivors" ]; then
    printf '\n'
    printf '  %s SURVIVOR(S) — the mutation was NOT caught. Each is a real finding about a real\n' "$green"
    printf '  pin, and needs a look: either the pin is weak, or the mutated line is unreachable on\n'
    printf '  the path these tests drive (which wants an input that reaches it, not a deleted guard).\n'
    printf '%s' "$survivors"
fi
printf '================================================================================\n'
