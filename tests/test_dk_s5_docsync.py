"""DK.S5 — docsync (docs↔code reconciliation) + brainstorm Lens 1 doc-freshness + count 15→16.

TDD over the audit + reconcile engine (planted discrepancies are caught; reconcile writes ONLY on
approval), plus the wiring guards: docsync is the 16th curated skill, the SK.S4 lints cover it, the
Contract/anatomy/activation surfaces render, parity is green, and no shipped skill leaks an internal
path. Pure-source (imports via `_support`, no installed package), so it runs on any interpreter.
"""

import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import docsync
from mokata.docsync import (
    BLOCKING, INFO, MINOR, NEW_NEEDED, STALE,
    CodeFacts, audit_text, gather_facts,
)

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _facts(**over):
    """A fixed CodeFacts so the audit tests don't depend on the live registry drifting."""
    base = dict(
        skill_count=16, total_skill_count=26,
        command_names=frozenset({"docsync", "setup", "review", "query"}),
        slash_commands=frozenset({"docsync", "review", "brainstorm"}),
        package="mokata", install_command="pip install mokata",
        setup_command="mokata setup claude", version="0.0.12",
        known_symbols=frozenset(), config_keys=frozenset())
    base.update(over)
    return CodeFacts(**base)


# --------------------------------------------------------------- the audit (output mode a)
class TestAuditCatchesPlantedDiscrepancies(unittest.TestCase):
    def test_stale_skill_count_is_blocking_and_fixable(self):
        text = "## Overview\n\nmokata ships 14 skills today.\n"
        findings = audit_text(text, facts=_facts())
        hit = [f for f in findings if f.checker == "skill-count"]
        self.assertEqual(len(hit), 1)
        self.assertEqual(hit[0].severity, BLOCKING)
        self.assertEqual(hit[0].section, "Overview")
        self.assertTrue(hit[0].fixable)
        self.assertIn("16", hit[0].suggestion)

    def test_correct_skill_count_is_not_flagged(self):
        for n in ("16 skills", "26 skills"):
            text = f"mokata ships {n}.\n"
            self.assertEqual(
                [f for f in audit_text(text, facts=_facts()) if f.checker == "skill-count"], [])

    def test_stale_command_name_is_blocking(self):
        text = "## Install\n\nRun `mokata reviewx` to review.\n"
        hit = [f for f in audit_text(text, facts=_facts()) if f.checker == "command-name"]
        self.assertEqual(len(hit), 1)
        self.assertEqual(hit[0].severity, BLOCKING)
        self.assertIn("reviewx", hit[0].message)
        self.assertFalse(hit[0].fixable)   # a rename can't be guessed

    def test_real_command_name_is_not_flagged(self):
        text = "Run `mokata review` then `mokata setup claude`.\n"
        self.assertEqual(
            [f for f in audit_text(text, facts=_facts()) if f.checker == "command-name"], [])

    def test_prose_mokata_is_not_a_command_reference(self):
        # "mokata governs" in prose (not code) must never be read as a `mokata <cmd>`.
        text = "mokata governs the write and mokata remembers the decision.\n"
        self.assertEqual(
            [f for f in audit_text(text, facts=_facts()) if f.checker == "command-name"], [])

    def test_stale_slash_skill_is_blocking(self):
        text = "Use `/mokata:reviewx` to review.\n"
        hit = [f for f in audit_text(text, facts=_facts()) if f.checker == "command-name"]
        self.assertEqual(len(hit), 1)
        self.assertIn("/mokata:reviewx", hit[0].message)

    def test_dead_install_path_is_blocking_and_fixable(self):
        text = "## Getting started\n\n```bash\npip install mokata-cli\n```\n"
        hit = [f for f in audit_text(text, facts=_facts()) if f.checker == "install-path"]
        self.assertEqual(len(hit), 1)
        self.assertEqual(hit[0].severity, BLOCKING)
        self.assertEqual(hit[0].section, "Getting started")
        self.assertTrue(hit[0].fixable)
        self.assertIn("pip install mokata", hit[0].suggestion)
        self.assertNotIn("mokata-cli", hit[0].suggestion)

    def test_canonical_install_is_not_flagged(self):
        text = "```bash\npip install mokata\n```\n"
        self.assertEqual(
            [f for f in audit_text(text, facts=_facts()) if f.checker == "install-path"], [])

    def test_unrelated_pip_install_is_not_flagged(self):
        text = "```bash\npip install pytest jsonschema\n```\n"
        self.assertEqual(
            [f for f in audit_text(text, facts=_facts()) if f.checker == "install-path"], [])

    def test_version_example_drift_is_info(self):
        text = "```bash\npip install mokata==0.0.9\n```\n"
        hit = [f for f in audit_text(text, facts=_facts()) if f.checker == "version-example"]
        self.assertEqual(len(hit), 1)
        self.assertEqual(hit[0].severity, INFO)
        self.assertIn("0.0.12", hit[0].suggestion)

    def test_all_three_planted_discrepancies_caught_together(self):
        # the validation fixture: a stale command name, a wrong skill count, AND a dead install path
        text = (
            "# Guide\n\n"
            "## Overview\n\nmokata ships 14 skills.\n\n"
            "## Getting started\n\n```bash\npip install mokata-cli\nmokata reviewx\n```\n")
        findings = audit_text(text, facts=_facts())
        kinds = {f.checker for f in findings}
        self.assertIn("skill-count", kinds)
        self.assertIn("command-name", kinds)
        self.assertIn("install-path", kinds)
        self.assertTrue(all(f.severity == BLOCKING
                            for f in findings if f.checker != "version-example"))
        self.assertEqual(set(docsync.stale_sections(findings)), {"Overview", "Getting started"})

    def test_symbol_ref_check_uses_injected_graph_resolver(self):
        text = "See `progress.active_banner` and `progress.gone_symbol`.\n"
        known = {"progress.active_banner"}
        findings = audit_text(text, facts=_facts(),
                              resolve=lambda s: s in known)
        hit = [f for f in findings if f.checker == "symbol-ref"]
        self.assertEqual(len(hit), 1)
        self.assertIn("progress.gone_symbol", hit[0].message)
        self.assertEqual(hit[0].severity, MINOR)

    def test_symbol_ref_check_is_noop_without_a_graph(self):
        text = "See `progress.whatever_symbol`.\n"
        self.assertEqual(
            [f for f in audit_text(text, facts=_facts()) if f.checker == "symbol-ref"], [])

    def test_clean_doc_has_no_findings(self):
        text = ("# Guide\n\nmokata ships 16 skills. Run `mokata review`.\n"
                "```bash\npip install mokata\n```\n")
        self.assertEqual(audit_text(text, facts=_facts()), [])

    def test_render_highlights_stale_sections(self):
        text = "## Overview\n\nmokata ships 14 skills.\n"
        report = docsync.render_findings("d.md", audit_text(text, facts=_facts()))
        self.assertIn("stale section", report)
        self.assertIn("Overview", report)


# --------------------------------------------------------------- reconcile (output mode b, gated)
class TestReconcileIsHumanGated(unittest.TestCase):
    def _doc(self, d):
        p = os.path.join(d, "getting-started.md")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("# Start\n\nmokata ships 14 skills.\n\n```bash\npip install mokata-cli\n```\n")
        return p

    def test_approve_writes_the_reconciled_doc(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._doc(d)
            res = docsync.reconcile_doc(p, facts=_facts(), confirm=lambda _t: True)
            self.assertTrue(res.written)
            self.assertGreaterEqual(res.edits, 2)
            with open(p, encoding="utf-8") as fh:
                after = fh.read()
            self.assertIn("16 skills", after)
            self.assertIn("pip install mokata\n", after)
            self.assertNotIn("14 skills", after)
            self.assertNotIn("mokata-cli", after)

    def test_decline_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._doc(d)
            with open(p, encoding="utf-8") as fh:
                before = fh.read()
            res = docsync.reconcile_doc(p, facts=_facts(), confirm=lambda _t: False)
            self.assertFalse(res.written)
            self.assertIn("declined", res.reason)
            with open(p, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), before)   # byte-identical — nothing written

    def test_preview_diff_is_shown_before_the_write(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._doc(d)
            captured = {}

            def confirm(text):
                captured["prompt"] = text
                return False

            docsync.reconcile_doc(p, facts=_facts(), confirm=confirm)
            self.assertIn("-mokata ships 14 skills.", captured["prompt"])
            self.assertIn("+mokata ships 16 skills.", captured["prompt"])

    def test_nothing_reconcilable_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "ok.md")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("mokata ships 16 skills.\n")
            res = docsync.reconcile_doc(p, facts=_facts(), confirm=lambda _t: True)
            self.assertFalse(res.written)
            self.assertIn("nothing to write", res.reason)

    def test_secret_in_new_text_hard_blocks_the_write(self):
        # the WriteGate secret scan still applies to a reconcile — a doc carrying a secret near a
        # fixable discrepancy is blocked even on approval.
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.md")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("mokata ships 14 skills.\n"
                         "aws_secret_access_key = AKIAIOSFODNN7EXAMPLEKEYDATA1234567890xx\n")
            res = docsync.reconcile_doc(p, facts=_facts(), assume_yes=True)
            self.assertFalse(res.written)


# --------------------------------------------------------------- targeting (ii): sweep + drift
class TestSweepAndDrift(unittest.TestCase):
    def _tree(self, d):
        os.makedirs(os.path.join(d, "docs", "build"))
        with open(os.path.join(d, "README.md"), "w", encoding="utf-8") as fh:
            fh.write("mokata ships 14 skills.\n")            # stale (public)
        with open(os.path.join(d, "docs", "clean.md"), "w", encoding="utf-8") as fh:
            fh.write("mokata ships 16 skills.\n")            # clean
        with open(os.path.join(d, "docs", "sig.md"), "w", encoding="utf-8") as fh:
            fh.write("The `resolve_precedence` engine wins.\n")  # references a symbol
        with open(os.path.join(d, "docs", "build", "internal.md"), "w", encoding="utf-8") as fh:
            fh.write("mokata ships 99 skills.\n")            # internal — excluded from sweep
        return d

    def test_sweep_no_target_audits_public_docs_and_skips_internal(self):
        with tempfile.TemporaryDirectory() as d:
            self._tree(d)
            results = docsync.sweep(root=d, facts=_facts())
            paths = {os.path.basename(p) for p in results}
            self.assertIn("README.md", paths)
            self.assertNotIn("clean.md", paths)             # clean → omitted
            self.assertNotIn("internal.md", paths)          # internal tree → never swept

    def test_find_docs_excludes_internal_tree_by_default(self):
        with tempfile.TemporaryDirectory() as d:
            self._tree(d)
            docs = docsync.find_docs(d)
            self.assertFalse(any("docs/build" in p.replace("\\", "/") for p in docs))
            self.assertTrue(docsync.find_docs(d, include_internal=True) != docs)

    def test_drift_docs_narrows_to_docs_that_reference_a_changed_symbol(self):
        with tempfile.TemporaryDirectory() as d:
            self._tree(d)
            drift = docsync.drift_docs(d, ["resolve_precedence"])
            self.assertEqual([os.path.basename(p) for p in drift], ["sig.md"])

    def test_drift_engagement_carries_banner_and_boundary_probe(self):
        with tempfile.TemporaryDirectory() as d:
            self._tree(d)
            eng = docsync.drift_engagement(d, ["resolve_precedence"])
            self.assertIsNotNone(eng)
            self.assertIn("⛭ mokata docsync", eng.banner)
            self.assertIn("gate:", eng.banner)
            self.assertIn("READ-ONLY", eng.boundary)
            self.assertIn("human gate", eng.boundary)
            self.assertEqual([os.path.basename(p) for p in eng.docs], ["sig.md"])

    def test_drift_engagement_is_none_when_no_doc_references_the_change(self):
        with tempfile.TemporaryDirectory() as d:
            self._tree(d)
            self.assertIsNone(docsync.drift_engagement(d, ["a_symbol_no_doc_mentions"]))

    def test_active_line_is_single_sourced(self):
        from mokata.progress import active_skill_line
        self.assertEqual(docsync.docsync_active_line(), active_skill_line("docsync"))
        self.assertIn("engaged", docsync.docsync_active_line("engaged"))


# --------------------------------------------------------------- brainstorm Lens 1 doc-freshness
class TestDocFreshnessLens(unittest.TestCase):
    def test_stale_doc_is_highlighted_and_returned(self):
        with tempfile.TemporaryDirectory() as d:
            stale = os.path.join(d, "stale.md")
            fresh = os.path.join(d, "fresh.md")
            with open(stale, "w", encoding="utf-8") as fh:
                fh.write("mokata ships 14 skills.\n")
            with open(fresh, "w", encoding="utf-8") as fh:
                fh.write("mokata ships 16 skills.\n")
            results = docsync.assess_doc_freshness([stale, fresh], root=d, facts=_facts())
            by = {os.path.basename(r.path): r for r in results}
            self.assertEqual(by["stale.md"].status, STALE)
            self.assertTrue(by["stale.md"].stale)
            self.assertEqual(by["fresh.md"].status, docsync.FRESH)
            self.assertEqual([os.path.basename(r.path) for r in docsync.stale_docs(results)],
                             ["stale.md"])

    def test_render_asks_the_user_to_update_stale_docs(self):
        with tempfile.TemporaryDirectory() as d:
            stale = os.path.join(d, "stale.md")
            with open(stale, "w", encoding="utf-8") as fh:
                fh.write("mokata ships 14 skills.\n")
            results = docsync.assess_doc_freshness([stale], root=d, facts=_facts())
            rendered = docsync.render_doc_freshness(results)
            self.assertIn("⚠", rendered)
            self.assertIn("STALE", rendered)
            self.assertRegex(rendered.lower(), r"ask the user to update")

    def test_touched_code_with_no_covering_doc_is_new_doc_needed(self):
        with tempfile.TemporaryDirectory() as d:
            results = docsync.assess_doc_freshness(
                ["src/mokata/newthing.py"], root=d, touched_symbols=["BrandNewSymbol"],
                facts=_facts())
            self.assertEqual([r.status for r in results], [NEW_NEEDED])

    def test_brainstorm_session_wires_the_lens_to_docsync(self):
        from mokata.brainstorm import BrainstormSession
        from mokata.brainstorm_impact import ApproachImpact
        with tempfile.TemporaryDirectory() as d:
            doc = os.path.join(d, "guide.md")
            with open(doc, "w", encoding="utf-8") as fh:
                fh.write("mokata ships 14 skills. See `changed_sym`.\n")
            s = BrainstormSession("anchor")
            s.record_impact("A", ApproachImpact(
                approach="A", impacted_files=[doc], impacted_symbols=["changed_sym"]))
            results = s.assess_doc_freshness("A", root=d, facts=_facts())
            self.assertTrue(any(r.stale for r in results))


# --------------------------------------------------------------- count bump 15→16 + lints
class TestCountBumpAndLints(unittest.TestCase):
    def test_docsync_is_the_16th_curated_skill(self):
        from mokata.agent_skills import CURATED_SKILLS
        self.assertIn("docsync", CURATED_SKILLS)
        self.assertEqual(len(CURATED_SKILLS), 16)

    def test_lints_expected_set_is_the_registry_not_a_hardcoded_count(self):
        from mokata.agent_skills import CURATED_SKILLS
        from mokata.skill_lints import expected_skill_names
        self.assertEqual(set(expected_skill_names()), set(CURATED_SKILLS))
        self.assertIn("docsync", expected_skill_names())

    def test_lints_pass_on_all_16_shipped_skills(self):
        from mokata.skill_lints import lint_report, run_lints
        findings = run_lints()
        self.assertEqual(findings, [], lint_report(findings))

    def test_lints_fail_on_a_planted_missing_anatomy(self):
        from mokata.skill_lints import LINT_ANATOMY, run_lints
        texts = {"docsync": "---\nname: docsync\nwhen_to_use: Engage when a, when b, when c. "
                             "Do NOT engage for x.\n---\n\n⛭ mokata docsync active — gate: x\n"
                             "## Contract\nbody\n"}   # NO ## Rationalizations / ## Verification
        findings = run_lints(texts=texts)
        self.assertTrue(any(f.lint == LINT_ANATOMY for f in findings))

    def test_docsync_has_contract_and_anatomy_single_sources(self):
        from mokata.skill_anatomy import ANATOMY
        from mokata.skill_contracts import CONTRACTS, render_contract_md
        from mokata.skill_anatomy import render_anatomy_md
        self.assertIn("docsync", CONTRACTS)
        self.assertIn("docsync", ANATOMY)
        self.assertIn("## Contract", render_contract_md("docsync"))
        self.assertIn("## Rationalizations", render_anatomy_md("docsync"))
        self.assertIn("## Verification", render_anatomy_md("docsync"))

    def test_docsync_headline_gate_is_backed_or_advisory_but_real(self):
        # the ⛭ line's gate one-liner must resolve (no invented gate)
        from mokata.progress import active_skill_line
        line = active_skill_line("docsync")
        self.assertIn("⛭ mokata docsync active — gate:", line)


# --------------------------------------------------------------- wiring guards
class TestWiringGuards(unittest.TestCase):
    def test_parity_declares_docsync_and_is_green(self):
        from mokata.parity import SURFACE_MATRIX, verify_parity
        self.assertIn("docsync", SURFACE_MATRIX)
        self.assertEqual(SURFACE_MATRIX["docsync"].slash, ("docsync",))
        report = verify_parity()
        self.assertTrue(report.ok, report.render())

    def test_docsync_cli_command_exists(self):
        from mokata.parity import cli_command_names
        self.assertIn("docsync", cli_command_names())

    def test_shipped_docsync_skill_matches_generated(self):
        from pathlib import Path
        from mokata.agent_skills import skill_markdown
        shipped = os.path.join(_REPO, "src", "mokata", "skills", "docsync", "SKILL.md")
        with open(shipped, encoding="utf-8") as fh:
            content = fh.read()
        built = skill_markdown("docsync",
                               Path(_REPO) / "src" / "mokata" / "templates" / "commands")
        self.assertEqual(content, built)

    def test_docsync_reference_file_ships(self):
        ref = os.path.join(_REPO, "src", "mokata", "skills", "docsync",
                           "references", "docsync-checks.md")
        self.assertTrue(os.path.isfile(ref))

    def test_no_shipped_skill_leaks_an_internal_doc_path(self):
        # the DK.S5 additions must keep the leak count at 0 (public-safe footer/body).
        skills_dir = os.path.join(_REPO, "src", "mokata", "skills")
        for name in os.listdir(skills_dir):
            sk = os.path.join(skills_dir, name, "SKILL.md")
            if not os.path.isfile(sk):
                continue
            with open(sk, encoding="utf-8") as fh:
                text = fh.read()
            for internal in ("docs/build", "docs/launch", "docs/marketing"):
                self.assertNotIn(internal, text, f"{name}/SKILL.md leaks '{internal}'")

    def test_brainstorm_surfaces_lint_wire_in_both_template_and_protocol(self):
        from mokata.brainstorm import BRAINSTORM_PROTOCOL
        self.assertIn("docsync", BRAINSTORM_PROTOCOL)
        self.assertIn("DOC-FRESHNESS", BRAINSTORM_PROTOCOL)
        tmpl = os.path.join(_REPO, "src", "mokata", "templates", "commands", "brainstorm.md")
        with open(tmpl, encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("docsync", body)
        self.assertIn("DOC-FRESHNESS", body)


class TestSlashCommandFactSetIsTheShippedSurface(unittest.TestCase):
    """DOCSYNC-P1 — the `/mokata:<name>` check must validate against the SHIPPED slash-command
    surface (the parity ``CommandSurface`` registry / the command templates), NOT the narrower set
    of agent-skills. Feeding only the ~26 agent-skills flagged the ~37 real slash commands that are
    NOT skills (`menu`, `docs`, `mode`, `init`, …) as "unknown" — 60 false-positives in the DOC.S1
    sweep. Widening must NOT weaken detection: a genuinely removed/renamed command still flags."""

    def test_parity_exposes_the_slash_surface_as_a_single_source(self):
        # The single source of the real slash-command set = the parity registry, never a hardcoded
        # list. It must equal the shipped command templates on disk exactly.
        from mokata.parity import slash_command_names
        surface = set(slash_command_names())
        tmpl_dir = os.path.join(_REPO, "src", "mokata", "templates", "commands")
        templates = {f[:-3] for f in os.listdir(tmpl_dir) if f.endswith(".md")}
        self.assertEqual(surface, templates,
                         "parity slash surface must match the shipped command templates")

    def test_gather_facts_populates_the_full_slash_surface(self):
        facts = gather_facts()
        from mokata.parity import slash_command_names
        self.assertEqual(set(facts.slash_commands), set(slash_command_names()))

    def test_valid_slash_command_that_is_not_a_skill_is_not_flagged(self):
        # `/mokata:menu`, `/mokata:docs`, `/mokata:mode`, `/mokata:init` are real slash commands but
        # NOT agent-skills. Under the live fact set they must audit CLEAN (the false-positive fix).
        facts = gather_facts()
        for cmd in ("menu", "docs", "mode", "init", "setup", "resume"):
            text = f"Run `/mokata:{cmd}` from inside Claude Code.\n"
            hits = [f for f in audit_text(text, facts=facts) if f.checker == "command-name"]
            self.assertEqual(hits, [], f"/mokata:{cmd} is a real slash command; must not flag")

    def test_genuinely_removed_slash_command_is_still_flagged(self):
        # detection is NOT weakened: a name that is neither a template nor a skill still flags.
        facts = gather_facts()
        text = "Use `/mokata:frobnicate` to do the thing.\n"
        hits = [f for f in audit_text(text, facts=facts) if f.checker == "command-name"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].severity, BLOCKING)
        self.assertIn("/mokata:frobnicate", hits[0].message)


# --------------------------------------------------- DG: the command checker scans INVOCATIONS only
def _cmd(text, facts=None):
    return [f for f in audit_text(text, facts=facts or _facts()) if f.checker == "command-name"]


class TestCommandCheckerDoesNotFabricateReferences(unittest.TestCase):
    """DG — `mokata <cmd>` is flagged only where the doc MEANS a command. The checker used to
    fabricate references three ways: it JOINED a line's separate inline code spans into one string
    (inventing adjacency that appears nowhere in the doc), it read OUTPUT/prose fences as commands,
    and it read a shell `#` comment as a command. Each fabrication gets its exact prose line here."""

    # (1) the join: two adjacent code spans are two separate fragments, never one command line.
    def test_adjacent_inline_code_spans_do_not_fabricate_a_command(self):
        # README.md L101 — the spans are `pip install mokata` and `mokata setup claude`; the old
        # checker joined them into "…install mokata mokata setup…" and flagged `mokata mokata`.
        self.assertEqual(_cmd("> `pip install mokata` → `mokata setup claude`\n"), [])

    def test_adjacent_spans_do_not_fabricate_a_command_across_prose(self):
        for line in (
            "Harness-agnostic: use the `mokata` CLI and `mokata-mcp` from any shell.\n",   # quickstart L36
            "there is **no npm package**, so `npx mokata` does not apply — use `uvx`/`pipx run`.\n",
            "2. in the project you want to use `mokata` on:\n",
        ):
            self.assertEqual(_cmd(line), [], line)

    def test_adjacent_spans_do_not_fabricate_a_dead_install_path(self):
        # use-without-plugin L180 — the spans are `pip install` and `mokata-hook`; joining them
        # invented "pip install mokata-hook" and the install checker called it a dead install path.
        text = "When you `pip install` mokata, `mokata-hook` lands on PATH.\n"
        self.assertEqual([f for f in audit_text(text, facts=_facts())
                          if f.checker == "install-path"], [])

    # (2) the fence language: a doc marks its output/data fences, and output is not an invocation.
    def test_a_text_fence_is_output_not_an_invocation_surface(self):
        # catches-a-bad-change L26 + differentiators L314 — CLI output, verbatim.
        self.assertEqual(_cmd("```text\nmokata initialized with profile 'standard'.\n```\n"), [])
        self.assertEqual(_cmd("```text\nmokata v1 playbook — profile 'full', mode 'sequential'\n```\n"), [])

    def test_a_text_fence_transcript_of_a_mistype_demo_is_not_flagged(self):
        # first-run L61-62 — the doc DEMONSTRATES a mistype and shows the CLI rejecting it. It
        # never tells the reader to run `mokata statuss`.
        text = ("```text\n"
                "$ mokata statuss\n"
                "mokata: 'statuss' is not a mokata command.\n"
                "Did you mean 'status'?\n"
                "```\n")
        self.assertEqual(_cmd(text), [])

    def test_a_non_shell_fence_is_not_an_invocation_surface(self):
        # mokata-as-a-pr-check L65 — a YAML step name, i.e. prose inside a data fence.
        self.assertEqual(_cmd("```yaml\n      - name: Post the mokata review comment\n```\n"), [])

    # (3) the shell comment: a `#` tail inside a shell fence is prose the author wrote for a human.
    def test_a_shell_comment_inside_a_bash_fence_is_prose(self):
        # complete-guide L195 / L202-203 / L230 and use-with-other-agents L47, verbatim.
        text = ("```bash\n"
                "rg --version        # mokata detects the `rg` executable\n"
                "# then confirm the command mokata looks for is resolvable:\n"
                "command -v code-review-graph    # must print a path for mokata to bind it\n"
                "# install the Obsidian app, or point mokata at an existing vault:\n"
                "# wire mokata into your agent (shows exactly what it will write, then asks)\n"
                "```\n")
        self.assertEqual(_cmd(text), [])

    # (4) command position: even in an undeclared fence, prose is not an invocation.
    def test_mokata_mid_sentence_in_a_bare_fence_is_not_a_command(self):
        # capture-project-rules L65 — CLI output inside an undeclared fence.
        text = ("```\n"
                "Proposed guardrails (recurring corrections mokata noticed — human-gated):\n"
                "```\n")
        self.assertEqual(_cmd(text), [])


class TestCommandCheckerStillCatchesRealDrift(unittest.TestCase):
    """DG negative controls — the scoping narrows WHERE the checker looks, never WHAT it catches.
    A genuinely stale command name in any real invocation surface is still Blocking."""

    def test_planted_stale_command_in_a_bash_fence_is_still_blocking(self):
        hits = _cmd("```bash\nmokata frobnicate --now\n```\n")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].severity, BLOCKING)
        self.assertIn("frobnicate", hits[0].message)

    def test_planted_stale_command_in_an_inline_code_span_is_still_blocking(self):
        hits = _cmd("Run `mokata frobnicate` to fix it.\n")
        self.assertEqual(len(hits), 1)
        self.assertIn("frobnicate", hits[0].message)

    def test_planted_stale_command_in_every_invocation_position_is_still_blocking(self):
        for line in (
            "$ mokata frobnicate",                    # a prompted transcript line
            "  mokata frobnicate --json",             # indented in a block
            "uvx mokata frobnicate",                  # behind a runner
            "pipx run mokata frobnicate",
            "python -m mokata frobnicate",
            "cd repo && mokata frobnicate",           # after a shell separator
            "mokata frobnicate | jq .",
            "mokata frobnicate   # with a trailing comment",
        ):
            hits = _cmd(f"```bash\n{line}\n```\n")
            self.assertEqual(len(hits), 1, f"missed real drift in: {line}")
            self.assertIn("frobnicate", hits[0].message)

    def test_a_bare_fence_invocation_is_still_scanned(self):
        # an undeclared fence still carries commands — only PROSE inside it is spared.
        hits = _cmd("```\nmokata frobnicate\n```\n")
        self.assertEqual(len(hits), 1)

    def test_a_real_command_in_a_bash_fence_is_not_flagged(self):
        self.assertEqual(_cmd("```bash\nmokata setup claude --yes\nmokata review\n```\n"), [])

    def test_the_slash_surface_keeps_its_full_scan_reach(self):
        # `/mokata:<name>` is self-delimiting — it cannot be prose — so it is checked in EVERY code
        # span, including the output fences the shell-invocation rule now skips.
        hits = _cmd("```text\n/mokata:frobnicate\n```\n")
        self.assertEqual(len(hits), 1)
        self.assertIn("/mokata:frobnicate", hits[0].message)


class TestCommandCheckerIsNotDisarmed(unittest.TestCase):
    """DG anti-disarm (the D5 sin, in reverse) — a checker that scans NOTHING also reports no false
    positives. These pin the checker's REACH over the real shipped docs, so a future 'precision'
    tweak that silently stops looking fails here instead of rendering as a clean bill of health."""

    def _public_docs(self):
        return docsync.find_docs(_REPO)

    def test_the_real_docs_no_longer_carry_command_name_false_positives(self):
        # the exact FP regression: the shipped public doc tree, audited with the LIVE fact set.
        facts = gather_facts()
        offenders = {}
        for d in self._public_docs():
            hits = [f for f in docsync.audit_doc(d, facts=facts) if f.checker == "command-name"]
            if hits:
                offenders[d] = [f.render() for f in hits]
        self.assertEqual(offenders, {}, f"command-name false positives remain: {offenders}")

    def test_the_checker_still_SEES_the_real_invocations_in_those_docs(self):
        # Arm the checker with a fact set in which NO real command exists: every genuine
        # `mokata <cmd>` invocation the docs carry must now light up. A scoping change that
        # quietly stopped scanning would drive this to zero.
        blind = _facts(command_names=frozenset({"__no_such_command__"}))
        seen = 0
        for d in self._public_docs():
            seen += len([f for f in docsync.audit_doc(d, facts=blind)
                         if f.checker == "command-name"])
        self.assertGreater(seen, 20, "the command checker has been disarmed, not made precise")


if __name__ == "__main__":
    unittest.main()
