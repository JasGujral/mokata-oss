#!/usr/bin/env bash
# 0.0.21 stage 07 `hostile-arrangements-run-on-every-push`, graded. Through scripts/mutate.sh.
#
#   PYTHON=/path/to/venv/bin/python tests/_stage07_hostile_arrangements_mutants.sh < /dev/null
#
#   F01-F04 ★★★ the narrow-stdio repair: gone, un-wired from `main`, downgraded to `replace`
#     (which hides WHICH character went), and the condition narrowed back to `strict` only —
#     the last is the bug my own first version shipped, because `LC_ALL=C` gives
#     `errors="surrogateescape"`, which is not strict and still dies.
#   A01-A03 ★★★ the arrangements themselves: the cp1252 env dropped, the ascii-locale env dropped,
#     and `apply` mutating the real environment.
#   O01-O03 ★★★ the static `open()`-encoding rule: the encoding check, the binary-mode exemption
#     (which would convict every `open(p, "rb")` in the tree), and the narrowing that stops it
#     convicting `os.open` — the defect my first version shipped, 14 false positives.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"

C=src/mokata/cli.py
H=tests/_hostile_arrangements.py
T='test_a37_hostile_arrangements_run_on_every_push.py'
TF='test_floor_provisioner.py'
P=tests/test_floor_provisioner.py

TOTAL=11
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

# ==== F · the narrow-stdio repair ==============================================================

mutant "F01 ★★★ the repair never reconfigures — release-notes-check dies printing its verdict" "$C" \
  '                reconfigure(errors="backslashreplace")' \
  '                pass' "$T"

mutant "F02 ★★★ the repair is UN-WIRED from main — §7i, a perfect fix nothing calls" "$C" \
  'def main(argv: Optional[List[str]] = None) -> int:
    _survive_a_narrow_stdio()' \
  'def main(argv: Optional[List[str]] = None) -> int:
    pass' "$T"

mutant "F03 ★★★ backslashreplace becomes replace — the dropped character becomes unknowable" "$C" \
  '                reconfigure(errors="backslashreplace")' \
  '                reconfigure(errors="replace")' "$T"

mutant "F04 ★★★ the condition narrows back to strict-only — LC_ALL=C gives surrogateescape and dies" "$C" \
  '            if (getattr(stream, "errors", None) or "strict") not in replacing:' \
  '            if (getattr(stream, "errors", None) or "strict") == "strict":' "$T"

# ==== A · the arrangements =====================================================================

mutant "A01 ★★★ the cp1252 arrangement arranges nothing — a clean reading over a clean shell" "$H" \
  '        env={"PYTHONIOENCODING": "cp1252"},' \
  '        env={"MOKATA_NOT_AN_ARRANGEMENT": "1"},' "$T"

mutant "A02 ★★★ the ascii-locale arrangement stops narrowing the locale" "$H" \
  '        env={"LC_ALL": "C", "LANG": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0"},' \
  '        env={"LC_ALL": "C.UTF-8", "LANG": "C.UTF-8"},' "$T"

mutant "A03 ★★★ apply() mutates the REAL environment — every later test runs arranged" "$H" \
  '        out = dict(os.environ if base is None else base)
        out.update(self.env)
        return out' \
  '        os.environ.update(self.env)
        return dict(os.environ)' "$T"

# ⛔ A04 WAS DROPPED, and the reason is a rule rather than an oversight. It attacked the ASSERTION
# ("an arrangement must name its evidence") — which lives in the TEST file, not in the subject. A
# mutant that weakens the test it is graded by grades nothing: it is guaranteed GREEN by
# construction, because the only thing that could catch it is the line being mutated. §7b says the
# list IS the score, and a mutant whose verdict is decided in advance is not a score.

# ==== O · the static open()-encoding rule ======================================================

mutant "O01 ★★★ the encoding check goes — a locale-encoded read ships again" "$H" \
  '            if isinstance(node, ast.Call) and _is_text_open(node) \
                    and "encoding" not in {kw.arg for kw in node.keywords}:' \
  '            if False:' "$T"

mutant "O02 ★★★ binary mode stops being exempt — every open(p, \"rb\") in the tree is convicted" "$H" \
  '        if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and "b" in arg.value:
            return False' \
  '        if False:
            return False' "$T"

mutant "O03 ★★★ the rule matches any .open attribute — os.open and tarfile.open convicted (14 false positives)" "$H" \
  '    elif (isinstance(func, ast.Attribute) and func.attr == "open"
          and isinstance(func.value, ast.Name) and func.value.id in _OPENERS):' \
  '    elif isinstance(func, ast.Attribute) and func.attr == "open":' "$T"

# ==== N · the no-route fixture asks BASH =======================================================

# ⛔ N01 WAS WITHDRAWN AS AN EQUIVALENT MUTANT ON POSIX, and the equivalence IS the row.
#
#   N01 swapped `_resolve_through_bash` back for `shutil.which`. It ran GREEN, correctly: on POSIX
#   those two agree, so no machine this project owns can tell them apart. ⭐ *"On POSIX the broken
#   and the correct spelling behave identically"* is the sentence
#   `WINDOWS-HAS-NO-RUNNER-UNTIL-THE-CUT` is built on, and five cuts of invisible Windows failures
#   are what it costs. The asymmetry (Git Bash's own `usr/bin`, absent from the Windows PATH) is in
#   the list `tests/_hostile_arrangements.py` declares cannot be arranged on POSIX.
#
#   N01b grades what IS observable everywhere, and it is the half that actually broke: the ROUND
#   TRIP. Whatever the fixture resolved must be reachable FROM BASH inside the directory it hands
#   out — the old fixture passed its own exclusion probe on Windows and still broke, because
#   "neither route is here" and "the script's own utilities are here" are different facts and only
#   the first was checked (§7g).

mutant "N01b ★★★ the round-trip probe stops firing — a fixture bash cannot read is handed out" "$P" \
  '    return tuple(line.strip() for line in (proc.stdout or "").splitlines() if line.strip())' \
  '    return ()' "$TF"

printf '\n================================================================================\n'
printf 'STAGE 07 HOSTILE-ARRANGEMENT MUTANTS: %s ran of %s — %s RED, %s GREEN\n' \
    "$ran" "$TOTAL" "$red" "$green"
if [ -n "$survivors" ]; then
    printf 'SURVIVORS (each is a row whose fix does not grade):\n%s' "$survivors"
else
    printf 'Every mutant was caught.\n'
fi
printf '================================================================================\n'
[ "$ran" -eq "$TOTAL" ] || exit 70
