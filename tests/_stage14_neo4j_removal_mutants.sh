#!/usr/bin/env bash
# Drives the 0.0.18 stage-14 (lane D, the Neo4j backend removal) mutant list through
# scripts/mutate.sh — the ONLY sanctioned mutator (doc 85 §7b). Never hand-edit a file to see
# whether a test catches it.
#
#   PYTHON=/path/to/venv/bin/python tests/_stage14_neo4j_removal_mutants.sh
#
# ⚠ THE NAME CARRIES ITS SUBJECT, NOT JUST ITS STAGE NUMBER. `tests/_stage14_mutants.sh` would
# collide with an earlier release's stage 14 — stage numbers repeat across releases in this repo.
# Stages 10–13 hit this first and answered it the same way.
#
# Consumes mutate.sh's EXIT CONTRACT: a VERDICT (RED/GREEN) is a completed run; any harness
# failure STOPS THE BATCH DEAD, because on exit 4 the mutator deliberately leaves the target
# mutated. Satisfies tests/_mutant_driver_contract.py, which is a live pin.
#
# ★ STEP 0 — THE GREEN BASELINE, AND IT IS NOT OPTIONAL (MUTANT-DRIVER-NO-GREEN-BASELINE, doc 84).
# Without it a batch cannot tell "the mutant was caught" from "these tests were already failing".
#
# ⚠ IT LIVES IN tests/, NOT IN docs/build/handoff/. `sync-public.sh` excludes docs/build/, so a
# driver filed beside its report is ABSENT from the public mirror and a contributor grading this
# repo would run an incomplete corpus that READS as complete.
#
# ⭐ WHAT THIS STAGE IS, AND WHY THE GROUPS ARE WEIGHTED THIS WAY. Every prior slice of the lane
# asserted the gate STILL CLOSED — an exact non-empty set, a fact about the tree. This stage
# asserts it OPEN, which is the absence of a fact, and an absence is also what a broken derivation
# produces. So group A does not just check the verdict; it attacks the DERIVATION behind it. And
# the deletion's own danger is not that something breaks — it is that everything keeps working
# while a user's wired graph quietly becomes the lexical floor under its own name (group B).
#   A. the gate OPENS           and the opening is the deletion's, not the probe's or the map's
#   B. no silent fallback       the announcement, the floor's NAME, and the by-TYPE filter
#   C. the record is TRUE       a third class, because both inherited shapes are false here
#   D. the remedy RUNS          the record names a command; the command has to do the job
#   E. what SURVIVES            the two graph tools, the floors, the exports, the backstops
#   F. the SELL surfaces        the wizard, the extra, the sweep that finds them
#   G. schema + do-NOT-build    the mechanism NOT reused, and the rule kept over an absence

set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
M="${MUTATE_SH:-scripts/mutate.sh}"
PY="${PYTHON:-python3}"

DEP=src/mokata/deprecation.py
LAY=src/mokata/knowledge/layer.py
ONB=src/mokata/onboarding.py
SEL=src/mokata/memory/selection.py
STX=src/mokata/session_transport.py
SCH=src/mokata/schema.py
EDG=src/mokata/memory/edges.py
KIN=src/mokata/knowledge/__init__.py
DR=tests/_deprecation_removal.py
T14F=tests/test_stage14_neo4j_removal.py

T14='test_stage14_neo4j_removal.py'
T9='test_stage9_removal_release.py'
TS2='test_simp_s2_deprecation.py'

TOTAL=28
ran=0; red=0; green=0; survivors=""

# ---- step 0: the green baseline ---------------------------------------------------------------
printf '================================================================================\n'
printf 'STEP 0 — GREEN BASELINE (no mutation applied). Nothing is graded until this passes.\n'
printf '================================================================================\n'
for pat in "$T14" "$T9" "$TS2"; do
    if ! PYTHONDONTWRITEBYTECODE=1 "$PY" -m unittest discover -s tests -t tests \
            -p "$pat" >/dev/null 2>&1; then
        printf '\nBATCH REFUSED — %s is not green before mutant 1.\n' "$pat"
        printf 'Every RED this batch could print would be unattributable. Fix the tree first.\n'
        exit 75
    fi
    printf 'baseline GREEN: %s\n' "$pat"
done
printf 'Grading %s mutants.\n\n' "$TOTAL"

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

# ==== A. the gate OPENS — and the opening is the DELETION's ====================================

mutant "A01 ***  THE MUTANT THIS STAGE EXISTS TO CATCH — the probe reports every target ABSENT, so
       the implemented set empties and the verdict reads 'landed' with the code still in the tree.
       An exact-empty assertion alone cannot see this; only the both-ways probe pin can" "$DR" \
  '    if spec is None:
        return False' \
  '    if True:
        return False' "$T14"

mutant "A02 ***  the removed channel is DROPPED from IMPLEMENTATIONS instead of kept absent —
       §7i in the file that quotes §7i: the map is what the probe ranges over, so a deleted entry
       stops being checked at the moment the check acquires a subject" "$DR" \
  '    "neo4j": "mokata.knowledge.neo4j_backend",' \
  '' "$T14"

mutant "A03 ***  the conservative direction of the probe is INVERTED — an import that ERRORS
       (a missing extra, a broken dependency) reads as a removal, which silently retires the whole
       guard the first time an optional dep goes missing" "$DR" \
  '    except (ImportError, AttributeError, ValueError):
        return True' \
  '    except (ImportError, AttributeError, ValueError):
        return False' "$T9"

mutant "A04 **   'landed' stops requiring an empty set — the verdict goes green while channels are
       still implemented, which is the arithmetic half of A01" "$DR" \
  '    if not present:
        return REMOVAL_LANDED' \
  '    if True:
        return REMOVAL_LANDED' "$T9"

# ==== B. NO SILENT FALLBACK — the whole danger of this deletion =================================

mutant "B01 ***  THE DELETION IS SILENT — the committed chain is never read, so a repo wired to a
       removed provider is told nothing at all and simply stops having a graph" "$LAY" \
  '    removed = _dep.removed_channels_in(chain, _dep.RemovedDerivedNotice)' \
  '    removed = ()' "$T14"

mutant "B02 ***  THE FLOOR WEARS THE REMOVED BACKEND'S NAME — the early return goes, so resolution
       falls through to GrepBackend(name=res.tool): the user sees \"floor 'neo4j'\", loses the AST
       floor on a Python repo, and nothing says a backend was removed" "$LAY" \
  '    if res is not None and res.tool in removed_here:' \
  '    if False:' "$T14"

mutant "B03 ***  the chain filter goes back to the MEMORY record type — the graph chain is asked
       the memory question, returns (), and the removal is silent again: the guard meant to catch
       the silent fallback produces it" "$LAY" \
  '_dep.removed_channels_in(chain, _dep.RemovedDerivedNotice)' \
  '_dep.removed_channels_in(chain, _dep.RemovedNotice)' "$T14"

mutant "B04 ***  the filter drops the TYPE and tests MEMBERSHIP — a \`memory_store\` chain naming a
       removed backend would now be announced by the code-graph path with the wrong remedy" "$DEP" \
  '    return tuple(tool for tool in (chain or ()) if isinstance(REMOVED.get(tool), kind))' \
  '    return tuple(tool for tool in (chain or ()) if tool in REMOVED)' "$T14"

mutant "B05 **   THE ANNOUNCEMENT NAGS — the O_EXCL marker stops suppressing the repeat, so every
       command re-prints the removal notice. A once-per-repo discipline that nags is one people
       learn to scroll past, which costs the notices that DO carry news" "$DEP" \
  '    except FileExistsError:
        return False
    except (OSError, ValueError):
        return False                              # degrade-clean, exactly as `warn_deprecated`' \
  '    except FileExistsError:
        pass
    except (OSError, ValueError):
        return False                              # degrade-clean, exactly as `warn_deprecated`' \
  "$T14"

mutant "B06 ***  the removed entry routes to the EMERGENCY lexical floor rather than the canonical
       one — the user keeps working and quietly loses every structural answer" "$LAY" \
  '        return _floor_backend(router, root), None' \
  '        return GrepBackend(root=root, name="grep"), None' "$T14"

mutant "B07 **   \`neo4j\` comes back into GRAPH_TOOLS, so the router treats a deleted backend as a
       real graph and tries to build a client for it" "$LAY" \
  'GRAPH_TOOLS = ("code-review-graph", "serena")' \
  'GRAPH_TOOLS = ("code-review-graph", "serena", "neo4j")' "$T14"

# ==== C. the RECORD is TRUE — a third class, because both inherited shapes are FALSE ============

mutant "C01 ***  THE FALSE REFUSAL, AND IT LOOKS CORRECT — the record becomes the BACKEND shape, so
       a user whose graph was never in mokata is told to install an older mokata and run
       \`mokata migrate neo4j\`, a command that has never existed for this channel. Every clause of
       it reads like a correct refusal, which is exactly slice 2's class" "$DEP" \
  '    "neo4j": RemovedDerivedNotice(
        channel="neo4j", what="Neo4j code-graph backend",' \
  '    "neo4j": RemovedNotice(
        data="mokata never held its data and has deleted nothing.",
        remedy=_one_last_migration("neo4j"),
        channel="neo4j", what="Neo4j code-graph backend", _dead=(' "$T14"

mutant "C02 ***  the dispatch loses its derived arm — every caller falls into \`removed_notice\`,
       which REFUSES this record by type, so the answer becomes a KeyError instead of a refusal" \
  "$DEP" \
  '    if isinstance(notice, RemovedDerivedNotice):
        return notice.render(ascii_only=ascii_only)' \
  '    if False:
        return notice.render(ascii_only=ascii_only)' "$T14"

mutant "C03 ***  the remedy names a command that does not exist — the notice sends the user to
       \`mokata migrate neo4j\` instead of the reconfigure this suite actually RUNS" "$DEP" \
  '        remedy=("Clear the dead wiring with `mokata reconfigure --remove neo4j` (reversible, "' \
  '        remedy=("Bring it across with `mokata migrate neo4j` (reversible, "' "$T14"

mutant "C04 **   P22 — the record leads with \"mokata has deleted nothing\", spending the notice on
       a tautology: mokata never held this data, so that clause tells the user nothing they need" \
  "$DEP" \
  '        return (f"{glyph}: the {self.what} was REMOVED in mokata {self.removed}, and this repo "
                f"still names `{self.channel}`. mokata has stopped querying it. {self.where}"' \
  '        return (f"{glyph}: mokata has deleted nothing. The {self.what} was REMOVED in mokata "
                f"{self.removed}, and this repo still names `{self.channel}`. {self.where}"' "$T14"

mutant "C05 ***  the frozen release goes back to tracking the live declaration — a statement about
       the PAST re-dates itself the day the next removal is promised (E7). ⚠ GRADED BY STAGE 13's
       SUITE, NOT THIS ONE, AND DELIBERATELY: the records are CONSTRUCTED at import, so today the
       mutated record RENDERS identically and every render-based pin passes — that was stage 13's
       F02 survivor. The pin that can see it is the AST one over the construction sites, which is
       a CLASS-level pin; stage 14 only added a member to that class, and writing a second copy
       here would be two defences covering for each other (§7f). What stage 14 DID change is that
       the pin now derives its record classes from the module instead of naming two of them" \
  "$DEP" \
  '        removed="0.0.18"),
}' \
  '        removed=REMOVAL_RELEASE),
}' test_stage13_vault_slice.py

# ==== D. the REMEDY RUNS — a record that names a command it cannot perform is a lie =============

mutant "D01 ***  THE REMEDY BECOMES A SILENT NO-OP — \`--remove\` goes back to asking which OFFERED
       integrations are wired, so removing a provider this release REMOVED reports 'no changes'
       and exits 0 to a user whose manifest plainly still names it" "$ONB" \
  '    removable = _removable_tools(root)' \
  '    removable = _wired_integrations(root)' "$T14"

mutant "D02 ***  the capability is read from the CATALOG again — which KeyErrors on exactly the
       tool a user most needs to unwire, the one this release stopped shipping" "$ONB" \
  '    need = ""
    if tid in TOOL_CATALOG:' \
  '    need = TOOL_CATALOG[tid]["provides"]
    if tid in TOOL_CATALOG:' "$T14"

mutant "D03 **   the unwire reports success without dropping the chain entry — 'no residue' becomes
       a claim rather than an outcome. ⚠ The one-line form of this target is now AMBIGUOUS (the
       manifest-derived capability lookup added a second \`if tid in chain\`), and mutate.sh
       refuses an ambiguous plan rather than guessing — exit 3, batch aborted, which is the
       contract working" "$ONB" \
  '    if tid in chain:
        new_chain = [t for t in chain if t != tid]' \
  '    if False:
        new_chain = [t for t in chain if t != tid]' "$T14"

# ==== E. what SURVIVES — deletion is graded by what stayed =====================================

mutant "E01 ***  a surviving public export is dropped alongside the three that went — the half a
       pure absence assertion cannot see, because it passes on an emptied module" "$KIN" \
  '    "CrgUnavailable",' \
  '' "$T14"

mutant "E02 ***  the honest floor collapses to bare grep, so the canonical AST answers the removal
       record points the user at stop being given" "$LAY" \
  '    if has_py:
        return AstBackend(root=root, grep=GrepBackend(root=root, name="grep"))' \
  '    if False:
        return AstBackend(root=root, grep=GrepBackend(root=root, name="grep"))' "$T14"

mutant "E03 ***  the memory backstop goes back to MEMBERSHIP — \`build_backend(\"neo4j\", …)\` then
       reaches \`removed_notice\`, which refuses a non-backend record, and a wrong call is answered
       with a KeyError instead of the typed refusal" "$SEL" \
  '    if isinstance(deprecation.REMOVED.get(tool), deprecation.RemovedNotice):' \
  '    if tool in deprecation.REMOVED:' "$T14"

mutant "E04 **   the transport backstop goes back to MEMBERSHIP — its own docstring claims it is
       'derived from the REMOVED registry, never a list of names', and a derived-record channel
       makes that claim false rather than merely incomplete" "$STX" \
  '    if not deprecation.is_removed_file_channel(kind):' \
  '    if kind not in deprecation.REMOVED:' "$T14"

# ==== F. the SELL surfaces — outside anything an import sweep reaches ===========================

mutant "F01 ***  THE WIZARD SELLS IT AGAIN — a brand-new user is offered the backend this release
       deleted, which is the state the tree was in on the day this stage started" "$ONB" \
  '    "code-review-graph", "serena",               # code_graph providers' \
  '    "code-review-graph", "serena", "neo4j",      # code_graph providers' "$T14"

mutant "F02 ***  the sweep strips comments from MARKDOWN too — a real install instruction in a
       published page is read as maintainer provenance and walks straight past" "$T14F" \
  '        code = path.endswith(stripped)' \
  '        code = True' "$T14"

# ==== G. schema + do-NOT-build =================================================================

mutant "G01 **   slice 1's detect-type mechanism is REUSED where it does not fit — a member nothing
       can ever read, added because the mechanism was nearby" "$SCH" \
  'REMOVED_DETECT_TYPES = ("obsidian",)' \
  'REMOVED_DETECT_TYPES = ("obsidian", "neo4j")' "$T14"

mutant "G02 ***  the do-NOT-build set drops the member the release just made true — retiring the
       rule in the release that SATISFIES it, which is §7i pointed at doc 85 §6 itself" \
  tests/test_db_s7a_edge_substrate.py \
  '        self.assertEqual(set(), imported & {"neo4j", "networkx", "igraph", "kuzu", "duckdb",' \
  '        self.assertEqual(set(), imported & {"networkx", "igraph", "kuzu", "duckdb",' \
  "$T14"

mutant "G03 ***  TRAP 4's CLASS — the prose goes back to CITING the deleted module as if it still
       existed, which is the exact sentence this stage had to rewrite and which the sweep found
       TWO live instances of (the second was stage 13's, in \`cli_commands/migrate.py\`)" "$EDG" \
  '(neo4j_backend.py, deliberately un-backticked, because a backticked name is a citation of' \
  '(`neo4j_backend.py`, backticked, because a backticked name is a citation of' "$T14"

# ---- the batch verdict ------------------------------------------------------------------------
printf '\n================================================================================\n'
printf 'STAGE 14 MUTANT BATCH — %s ran of %s declared: %s RED, %s GREEN\n' \
    "$ran" "$TOTAL" "$red" "$green"
if [ "$ran" -ne "$TOTAL" ]; then
    printf 'BATCH INCOMPLETE — %s declared, %s ran. Not a score.\n' "$TOTAL" "$ran"
    printf '================================================================================\n'
    exit 71
fi
if [ "$green" -ne 0 ]; then
    printf 'SURVIVORS (each is a hole in this stage\x27s tests, not a spare mutant):\n%s' "$survivors"
    printf '================================================================================\n'
    exit 1
fi
printf 'ALL %s MUTANTS RED.\n' "$TOTAL"
printf '================================================================================\n'
