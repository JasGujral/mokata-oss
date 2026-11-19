#!/usr/bin/env bash
# Drives the 0.0.29 lane F (hook budget) mutant list through scripts/mutate.sh — the ONLY
# sanctioned mutator (doc 85 §7b). Never hand-edit a file to see whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python tests/_f_hook_budget_mutants.sh
#
# Consumes mutate.sh's EXIT CONTRACT: a VERDICT (RED/GREEN) is a completed run; any harness
# failure STOPS THE BATCH DEAD (exit 4 leaves the target mutated on purpose; exit 7 means the
# baseline was not green, so nothing after it is attributable). (Driver shape copied from
# tests/_b5_mutants.sh — MUTANT-DRIVER-CONTRACT-DUPLICATED, doc 84.)
#
# WHAT THIS BATCH IS FOR. Each guard lane F added (note 0029-design/F-hook-budget.md §5) is undone
# one at a time: the in-process process reader (no `ps`), the one user-cache rule in Python and sh,
# the bytecode prune's narrowness, the measured ceilings, and the stored next-step answer's key
# (pid facts, event answers, the pin, the own row's projection).
#
# ⚠ THE LIMIT, STATED BEFORE THE SCORE (§7i). The note's M-F6b (prune decodes without the
# round-trip check) is NOT in this batch: on POSIX every name `_decode` accepts round-trips by
# construction (CPython's prefixed path is `prefix + abs source dir`), so the mutant is EQUIVALENT
# there; the check is defence in depth for a shape no POSIX input reaches. Stated in the F report.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"
SA=src/mokata/session_ancestry.py
PC=src/mokata/pycache.py
SI=src/mokata/session_index.py
BC=src/mokata/briefing_cache.py
BS=src/mokata/bootstrap.py
H=src/mokata/hints.py
RR=src/mokata/run_resolver.py
ENV=src/mokata/hooks/mokata-plugin-env.sh
L=src/mokata/hooks/mokata-launch
P=src/mokata/perf.py
HC=src/mokata/hook_cli.py

TS='test_f_hook_spawns_no_process_table.py'
TL='test_f_libproc_reader.py'
TR='test_f_resume_answer_at_start.py'
TC='test_f_hook_cache.py'
TK='test_f_hook_ceilings.py'
TW='test_w5_hook_launcher_bytecode.py'
TB='test_hb_hook_budget.py'
# the lane's prune cells AND the verifier's walk-swap matrix (tests/test_f_verify_r2_prune_walk.py)
TP='test_f_[hv]*.py'

TOTAL=41
ran=0; red=0; green=0; survivors=""

mutant() {
    local label="$1" rc=0 out
    ran=$((ran + 1))
    out="$("$M" "$@")" || rc=$?
    if [ -n "$out" ]; then printf '%s\n' "$out"; fi
    if [ "$rc" -ne 0 ]; then
        printf '\n================================================================================\n'
        printf 'BATCH ABORTED — mutate.sh exited %s on mutant %s of %s\n' "$rc" "$ran" "$TOTAL"
        printf '  mutant: %s\n' "$label"
        printf '  %s of %s mutants NEVER RAN. THIS BATCH DID NOT PASS — it stopped here.\n' \
            "$((TOTAL - ran))" "$TOTAL"
        printf '================================================================================\n'
        exit "$rc"
    fi
    case "$out" in
        RED*)   red=$((red + 1)) ;;
        GREEN*) green=$((green + 1)); survivors="$survivors  - $label"$'\n' ;;
        *)      printf '\nBATCH ABORTED — exit 0 with no verdict on mutant %s of %s (%s): %s\n' \
                    "$ran" "$TOTAL" "$label" "${out:-<nothing>}"; exit 70 ;;
    esac
}

# ==== the process reader ========================================================================
mutant "F01 darwin falls back to ps when libproc fails" "$SA" \
  'return _libproc_lookup()       # NEVER' 'return _libproc_lookup() or _ps_lookup()  # NEVER' "$TS"
mutant "F02 the prompt hook takes no shared snapshot" "$HC" \
  '_index_prompt(root, prompt=prompt, session_id=session_id, lookup=procs)' \
  '_index_prompt(root, prompt=prompt, session_id=session_id)' "$TS"
mutant "F03 a hop named by pbi_name (the resolved image)" "$SA" \
  'lstart_token(secs), float(secs), name_of(pid))' \
  'lstart_token(secs), float(secs), os.fsdecode(info.pbi_name) or None)' "$TL"
mutant "F04 the token built from epoch seconds" "$SA" \
  'lstart_token(secs), float(secs), name_of(pid))' 'str(secs), float(secs), name_of(pid))' "$TL"

# ==== the one cache rule =========================================================================
mutant "F05 a relative XDG_CACHE_HOME accepted" "$PC" \
  'if is_absolute(xdg, nt=nt):' 'if xdg:' "$TC"
mutant "F06 a drive path absolute on POSIX" "$PC" \
  'return posixpath.isabs(path)' 'return posixpath.isabs(path) or ntpath.isabs(path)' "$TC"
mutant "F07 the sh rule accepts any form" "$ENV" \
  '[ -n "$_mk_win" ] || return 1' '[ -n "$_mk_win" ] || return 0' "$TC"
mutant "F08 mokata-launch keeps PYTHONDONTWRITEBYTECODE" "$L" \
  '_mk_bytecode_cache

# Every fix line' ':

# Every fix line' "$TW"

# ==== the prune ==================================================================================
mutant "F09 prune ignores the mokata/pycache guard" "$PC" \
  '!= PYCACHE_TAIL or not os.path.isdir(root):' \
  '!= PYCACHE_TAIL and False or not os.path.isdir(root):' "$TC"
mutant "F10 prune runs on Windows" "$PC" \
  'if (os.name == "nt" if nt is None else nt) or not prefix:' 'if not prefix:' "$TC"
mutant "F11 prune follows links below the root" "$PC" \
  '                if e.is_symlink():' '                if False:' "$TC"

# Round 2 (verifier M1): the walk holds each directory by descriptor, opened O_NOFOLLOW.
mutant "F26 the prune opens directories without O_NOFOLLOW" "$PC" \
  'flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | nofollow' \
  'flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)' "$TC"

# Round 3 (verifier r2 H1): the root is reached from `/` by descriptor, then held only if it is
# the resolved directory. Each guard has a cell only it owns (PruneReachesItsRootByDescriptor).
mutant "F31 an ancestor of the root is followed" "$PC" \
  'child = os.open(name, flags, dir_fd=fd)' \
  'child = os.open(name, flags & ~os.O_NOFOLLOW, dir_fd=fd)' "$TC"
mutant "F32 the held root's identity is not checked" "$PC" \
  'if ((got.st_dev, got.st_ino) != (want.st_dev, want.st_ino) or not' 'if (False or not' "$TC"
# Close-out (verifier r3): the owner check, pinned by tests/test_f_verify_r3_prune_ancestors.py.
mutant "F37 the held root's owner is not checked" "$PC" \
  'or (hasattr(os, "geteuid") and got.st_uid != os.geteuid())):' 'or False):' "$TP"
# Round 3 (verifier r2 M1): the walk's own descriptor guards (the verifier's W2/W3/W4/W7).
mutant "F33 unlink by path, not dir_fd (W2)" "$PC" \
  'os.unlink(e.name, dir_fd=fd)' 'os.unlink(path)' "$TP"
mutant "F34 rmdir by path, not dir_fd (W3)" "$PC" \
  'os.rmdir(frame[4], dir_fd=stack[-1][0])' 'os.rmdir(frame[1])' "$TP"
mutant "F35 a child opened by path, not dir_fd (W4)" "$PC" \
  'child = os.open(e.name, flags, dir_fd=fd)' \
  'child = os.open(os.path.join(here, e.name), flags)' "$TP"
mutant "F36 O_DIRECTORY dropped (W7)" "$PC" \
  'flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | nofollow' \
  'flags = os.O_RDONLY | nofollow' "$TP"

# ==== the ceilings ===============================================================================
mutant "F12 a ceiling raised by hand" "$P" \
  '"gate-guard@repo": 95.0' '"gate-guard@repo": 3 * 95.0' "$TK"
mutant "F13 the CI relax comes back" "$P" \
  '    return 1.0

#: `python -c pass`' '    return relax_factor()

#: `python -c pass`' "$TK"

# A PLANTED 2x REGRESSION (brief: "a planted 2x regression reding on each OS"). gate-guard@repo's
# worst measured darwin p95 is 62.9 ms (the mirror's macOS runner; re-derived per OS at the wave-2
# bookend): this sleeps that long again inside the hook, through the real launcher, and the hb cell
# must red against the darwin ceiling (95). macOS only here; on linux / win32 the same plant is
# under the floor (darwin x 2) by design — a 3x one reds there (test_f_hook_ceilings states it).
mutant "F22 gate-guard planted 2x slower" "$HC" \
  '"""
    args = _parse_flags(argv, "mokata run-state gate (sync gate hook)", [' \
  '"""
    import time as _t; _t.sleep(0.0629)
    args = _parse_flags(argv, "mokata run-state gate (sync gate hook)", [' "$TB"

# Ruled by Jas 2026-11-16: off darwin no ceiling is below the darwin one x OFF_DARWIN_BUFFER.
mutant "F23 the off-darwin buffer removed" "$P" \
  'OFF_DARWIN_BUFFER = 2.0' 'OFF_DARWIN_BUFFER = 1.0' "$TK"
mutant "F24 the off-darwin buffer raised" "$P" \
  'OFF_DARWIN_BUFFER = 2.0' 'OFF_DARWIN_BUFFER = 10.0' "$TK"
# Re-derived per OS at the 0.0.29 wave-2 bookend (measured linux / win32 columns, darwin x buffer floor).
mutant "F38 the off-darwin floor dropped" "$P" \
  'return float(max(raw, darwin_ceiling_ms * OFF_DARWIN_BUFFER))' 'return float(raw)' "$TK"
mutant "F39 this OS's measured column ignored" "$P" \
  'ceiling = HOOK_BUDGET_CEILINGS_MS.get(sys.platform, {}).get(key,' 'ceiling = {}.get(key,' "$TK"
mutant "F40 an off-darwin ceiling raised by hand" "$P" \
  '"pre-compact@bound": 105.0' '"pre-compact@bound": 3 * 105.0' "$TK"
mutant "F41 an unmeasured platform graded at darwin" "$P" \
  '                                                                    darwin * OFF_DARWIN_BUFFER)' \
  '                                                                    darwin)' "$TK"
mutant "F25 a non-darwin skip reintroduced" "$P" \
  '    return p95_norm_ms <= hook_budget_ms(sub, fixture)[0] * margin_relax()' \
  '    if sys.platform != "darwin":
        return True
    return p95_norm_ms <= hook_budget_ms(sub, fixture)[0] * margin_relax()' "$TK"

# ==== the stored next-step answer's key ==========================================================
mutant "F14 heartbeat_live dropped from the projection" "$SI" \
  '{"has_end": has_end, "heartbeat_live": live, "proc_pid": pid}' \
  '{"has_end": has_end, "proc_pid": pid}' "$TR"
mutant "F15 has_end dropped from the projection" "$SI" \
  '{"has_end": has_end, "heartbeat_live": live, "proc_pid": pid}' \
  '{"heartbeat_live": live, "proc_pid": pid}' "$TR"
mutant "F16 proc_pid dropped from the projection" "$SI" \
  '{"has_end": has_end, "heartbeat_live": live, "proc_pid": pid}' \
  '{"has_end": has_end, "heartbeat_live": live}' "$TR"
mutant "F17 a hit never re-probes its pids" "$BC" \
  '    if entry["pids"]:' '    if False:' "$TR"
mutant "F18 the LIVE rung probes around the recorder" "$RR" \
  'not pid_alive(entry.get("pid"))' \
  'not getattr(pid_alive, "__wrapped__", pid_alive)(entry.get("pid"))' "$TR"
mutant "F19 the own-session probe goes around the recorder" "$H" \
  'if pid is not None and not pid_alive(pid):' \
  'if pid is not None and not getattr(pid_alive, "__wrapped__", pid_alive)(pid):' "$TR"
mutant "F20 a hit never re-asks the event store" "$BC" \
  'if entry["events"] and not _events_hold(entry, session):' 'if False:' "$TR"
mutant "F21 the run pin dropped from the key" "$H" \
  'KEYED_ENV = ("MOKATA_SESSION_ID", ' 'KEYED_ENV = (' "$TR"

# Round 2 (verifier M2, the verifier's V01/V01b/V01c/V19 re-run here): every event field and the
# refresher's post-derive fact recheck.
mutant "F27 a hit ignores the event store's fault" "$BC" \
  '"absent": bool(r.absent), "fault": bool(r.fault)}' \
  '"absent": bool(r.absent), "fault": bool(said.get("fault"))}' "$TR"
mutant "F28 a hit ignores truncation" "$BC" \
  '"truncated": bool(r.truncated),' '"truncated": bool(said.get("truncated")),' "$TR"
mutant "F29 a hit ignores absence" "$BC" \
  '"absent": bool(r.absent), "fault": bool(r.fault)}' \
  '"absent": bool(said.get("absent")), "fault": bool(r.fault)}' "$TR"
mutant "F30 the refresher stores without the fact recheck" "$BS" \
  '            or facts(ans) != first:' '            or False:' "$TR"

# ==== summary =================================================================================
printf '\n================================================================================\n'
printf 'F hook-budget mutants: %s ran of %s · RED %s · GREEN %s\n' \
    "$ran" "$TOTAL" "$red" "$green"
if [ -n "$survivors" ]; then
    printf 'SURVIVORS — each is a guard lane F added that no test holds:\n%s' "$survivors"
    printf '================================================================================\n'
    exit 1
fi
printf 'No survivors.\n'
printf '================================================================================\n'
