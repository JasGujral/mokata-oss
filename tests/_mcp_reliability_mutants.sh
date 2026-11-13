#!/usr/bin/env bash
# 0.0.28 lane I (MCP reliability) mutants, driven through scripts/mutate.sh — the ONLY sanctioned
# mutator (doc 85 §7b). Each mutant breaks one fix this lane made; the named test module must RED.
#
#   PYTHON=/path/to/venv/bin/python tests/_mcp_reliability_mutants.sh
#
# IT CONSUMES THE EXIT CONTRACT (0.0.17 stage 18b): a VERDICT (RED / GREEN) is a completed run;
# any non-zero status, or a status 0 with no verdict, STOPS THE BATCH (the target may be left
# mutated). Moved here from docs/build/ in round 3 so it ships with the tests it grades.
# The release-script pin-gate mutant is not in this batch: scripts/release.sh is internal and absent
# from the public mirror, so a shipped driver cannot mutate it (its result is in the lane's
# internal evidence log).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"
S=src/mokata/mcp/server.py
V=src/mokata/mcp/validation.py
NL=$'\n'

TOTAL=22
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
        printf '  See scripts/mutate.sh'"'"'s EXIT CONTRACT for what %s means and what to do.\n' "$rc"
        printf '================================================================================\n'
        exit "$rc"
    fi
    case "$out" in
        RED*)   red=$((red + 1)) ;;
        GREEN*) green=$((green + 1)); survivors="$survivors  - $label"$'\n' ;;
        *)      printf '\nBATCH ABORTED — mutate.sh exited 0 for mutant %s of %s but printed no verdict.\n' \
                    "$ran" "$TOTAL"
                printf '  mutant: %s\n  said  : %s\n' "$label" "${out:-<nothing at all>}"
                printf '  %s of %s mutants NEVER RAN. THIS BATCH DID NOT PASS.\n' \
                    "$((TOTAL - ran))" "$TOTAL"
                exit 70 ;;
    esac
}

mutant "M01 drop the fd-0 park" $S \
  "    rpc_in = _park_stdin()${NL}    import argparse" "    rpc_in = None${NL}    import argparse" "test_i1_mcp_transport.py"
mutant "M02 a startup child before the park" $S \
  "    # 0.0.28 I1 — FIRST: the protocol input leaves fd 0 before anything else can spawn a child.${NL}    rpc_in = _park_stdin()" \
  "    _register_this_window(\".\")${NL}    rpc_in = _park_stdin()" "test_i1_mcp_transport.py"
mutant "M03 drop errors=replace on the protocol input" $S \
  "encoding=\"utf-8\",${NL}                                             errors=\"replace\"))" "encoding=\"utf-8\"))" "test_i1_mcp_transport.py"
mutant "M04 tools back on the default limiter" $S \
  "abandon_on_cancel=True, limiter=_tool_limiter())" "abandon_on_cancel=True)" "test_i1_mcp_transport.py"
mutant "M05 a finite tool limiter (64)" $S \
  "anyio.CapacityLimiter(math.inf)" "anyio.CapacityLimiter(64)" "test_i1_mcp_transport.py"
mutant "M06 log level back to INFO" $S \
  "log_level=\"WARNING\")" "log_level=\"INFO\")" "test_i1_mcp_transport.py"
mutant "M07 no not-initialized branch" $S \
  "if isinstance(exc, ConfigError) and getattr(exc, \"kind\", \"\") == NOT_INITIALIZED:" "if False:" "test_i1_mcp_not_initialized.py"
mutant "M08 skip validate_call" $S \
  "served = validate_call(name, tool.parameters, arguments)" "served = arguments" "test_i1_mcp_strict_front_door.py"
mutant "M09 strict JSON strings (no stringified arrays/objects)" $V \
  "        if isinstance(value, str):${NL}            try:${NL}                parsed = json.loads(value)" \
  "        if False:${NL}            try:${NL}                parsed = json.loads(value)" "test_i1_mcp_strict_front_door.py"
mutant "M10 lax bools (any truthy string)" $V \
  "        if isinstance(value, str) and value.strip().lower() in _BOOL_STRINGS:${NL}            return _BOOL_STRINGS[value.strip().lower()]${NL}        return _NO" \
  "        if isinstance(value, str) and value.strip().lower() in _BOOL_STRINGS:${NL}            return _BOOL_STRINGS[value.strip().lower()]${NL}        return bool(value)" "test_i1_mcp_strict_front_door.py"
mutant "M11 re-add the bare-prose remedy match" src/mokata/axi_out.py \
  "        return cmd or None${NL}    return None" \
  "        return cmd or None${NL}    m = re.search(r\"(?<![\\w/-])(mokata [a-z][a-z0-9-]*)\", text)${NL}    return m.group(1) if m else None" "test_i2_mcp_schema_contract.py"
mutant "M12 a default on remember.value" src/mokata/mcp/tools_memory.py \
  "description=\"The fact or decision text to store; must not be blank.\")]," \
  "description=\"The fact or decision text to store; must not be blank.\")] = \"\"," "test_i2_mcp_*.py"
mutant "M13 re-add the full preview echo" src/mokata/mcp/consent.py \
  "PREVIEW_INLINE_MAX = 4096" "PREVIEW_INLINE_MAX = 10 ** 9" "test_i1_mcp_transport.py"
mutant "M15 hand-edit the generated tool reference" docs/reference/mcp-tools.md \
  "# Reference: MCP tools" "# Reference: MCP tool" "test_i2_mcp_docs_derived.py"
mutant "M16 ConfigError loses its not-initialized kind" src/mokata/config.py \
  "kind=NOT_INITIALIZED" "kind=\"\"" "test_i1_mcp_not_initialized.py"
mutant "M17 the doctor pin line never fires" src/mokata/cli_commands/diagnostics.py \
  "    if rc != 0 and (\"No solution found\" in err or \"no version of\" in err):" \
  "    if False:" "test_i1_plugin_pin_gate.py"
# Round 2 — the probes go through B's door, and the handshake ends its tree before closing pipes.
A=src/mokata/mcp_admin.py
mutant "M18 handshake closes its pipes before ending the tree" $A \
  "    tree.close()${NL}    proc = tree.proc${NL}    for stream in (proc.stdin, proc.stdout, proc.stderr):" \
  "    proc = tree.proc${NL}    for stream in (proc.stdin, proc.stdout, proc.stderr):" "test_i1_mcp_admin_door.py"
mutant "M19 handshake spawns outside the door (a bare Popen, no group of its own)" $A \
  "        tree = quiet_spawn.spawn_jobbed(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,${NL}                                        stderr=subprocess.PIPE, text=True)" \
  "        tree = quiet_spawn.Jobbed(subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,${NL}                                        stderr=subprocess.PIPE, text=True), None, False)" "test_i1_mcp_admin_door.py"
mutant "M20 gh runner spawns outside the door" src/mokata/branch_protection.py \
  "    return quiet_spawn.run_jobbed([\"gh\", \"api\", path], timeout=GH_TIMEOUT_SECONDS)" \
  "    import subprocess as sp; p = sp.run([\"gh\", \"api\", path], capture_output=True, text=True, timeout=GH_TIMEOUT_SECONDS); return p.returncode, p.stdout, p.stderr" "test_i1_mcp_admin_door.py"
# Round 3 — the ruled spec_emit redeem (OSS #82 item 4 kept): the bare id commits the saved approved copy.
TS=src/mokata/mcp/tools_spec.py
mutant "M21 the bare proposal_id no longer reloads the saved copy" $TS \
  "    if proposal_id and len(absent) == len(EMIT_REQUIRED):" "    if False:" "test_patch_0024_oss_82_83.py"
mutant "M22 no strict refusal when fields are missing and there is no proposal_id" $TS \
  "        missing = absent${NL}        if missing:" "        missing = absent${NL}        if False:" "test_patch_0024_oss_82_83.py"
mutant "M23 the proposal saves no copy to redeem" $TS \
  "        _save_emit_args(surface, proposed.get(\"proposal_id\"), args)" "        pass" "test_patch_0024_oss_82_83.py"

printf 'LANE I MUTANTS: %s ran of %s — %s RED, %s GREEN\n' "$ran" "$TOTAL" "$red" "$green"
if [ "$ran" -ne "$TOTAL" ]; then
    printf 'BATCH INCOMPLETE — %s of %s ran\n' "$ran" "$TOTAL"
    exit 70
fi
if [ "$green" -ne 0 ]; then
    printf 'SURVIVORS:\n%s' "$survivors"
    exit 1
fi
