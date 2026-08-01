"""HANDOFF.G1 — the MCP `spec_show` read tool + phase docs that point at it (0.0.16).

The bug (TRUST class, live): REVIEW was RE-SEARCHING for the spec instead of picking it up.
`review.md` told the coordinator to hand the reviewer a "SELF-CONTAINED brief — the emitted spec +
its acceptance criteria …" but named NO instrument to fetch it with, and no MCP tool returned the
spec's content at all: the registry carried `spec_emit`/`spec_amend`/`spec_check` (all writes) and
the only MCP reader that touched the spec — `decompose` — returned a subtask split, never the spec.
Meanwhile `develop.md`/`test.md` sent the agent to a FILE that does not exist: the real artifact is
`emitted_spec__<run_id>.json` (session-scoped, `session_state.SESSION_SCOPED_KEYS`), never a bare
`emitted_spec.json`. So the persisted, gate-passed spec was reachable from a terminal
(`mokata spec show`) and from nowhere inside the harness — and a phase that cannot READ the approved
spec re-derives it from conversation memory, which is exactly the P2 failure the emit gate exists to
prevent.

The fix under test:
  1. MCP read tool `spec_show` — one keyed read of the run's own spec (title / approach / domains /
     acceptance criteria / scope), NOT a corpus scan;
  2. it resolves the run through the SAME `cli_commands.spec._run_scoped_store` the CLI uses (no
     third resolution path), and takes an explicit `run`;
  3. degrade-clean: no tracked run · no spec · present-but-MALFORMED are three distinct answers,
     each naming its recovery, none an exception and none leaking spec content;
  4. the phase docs name it — review's brief fetches the spec with `spec_show`, and develop/test/
     ship stop pointing at the nonexistent `emitted_spec.json`;
  5. the parity matrix sees the read surface it was blind to;
  6. negatives: `mokata spec show` output byte-identical, `decompose` and `spec_check` untouched.

Business-level asserts: what a PHASE observes (the tool result a coordinator would hand a reviewer,
the CLI bytes a human reads, the shipped doc text an agent is given), never implementation poking.
"""

import contextlib
import io
import json
import os
import tempfile
import types
import unittest
from pathlib import Path

import _support  # noqa: F401  (puts src/ on the path)

from mokata import parity
from mokata.config import Surface
from mokata.engine.spec import AcceptanceCriterion, Spec
from mokata.engine.spec_gate import SPEC_STATE_KEY
from mokata.govern.resume import PipelineCheckpoint
from mokata.mcp import registry as REG
from mokata.mcp import tool_annotations as TA
from mokata.mcp.tools_read import decompose, spec_show
from mokata.state import StateStore
from mokata.tdd_state import state_dir

_SRC = Path(__file__).resolve().parents[1] / "src" / "mokata"
_TEMPLATES = _SRC / "templates" / "commands"
_SKILLS = _SRC / "skills"


# --------------------------------------------------------------------------- fixtures
def _repo(d):
    from mokata.init import init_repo
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
    return Surface.load(d)


def _persist_run(root, run_id):
    """A run with a checkpoint on disk — what `gate_hook._run_ids` enumerates as a candidate."""
    PipelineCheckpoint(Surface.load(root).state, run_id).ensure_registered()


def _write_spec(root, run_id, spec):
    """Persist `spec` at the run-scoped key the emit phase writes (`emitted_spec__<run_id>`).

    Deliberately written through the RAW `StateStore` with the physical name spelled out, rather
    than through a `SessionScopedStore`: the test then pins the on-disk contract the tool must read,
    not whatever scoping helper it happens to use."""
    StateStore(state_dir(root)).write(f"{SPEC_STATE_KEY}__{run_id}", spec.to_dict())


def _write_raw(root, run_id, payload):
    """Write arbitrary JSON at the spec's run-scoped path — the torn-write / hand-edit case."""
    path = os.path.join(state_dir(root), f"{SPEC_STATE_KEY}__{run_id}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


def _spec(title="Ship the widget", *, criteria=(("AC1", "the widget ships"),),
          approach="incremental", domains=("api",)):
    return Spec(title=title,
                criteria=[AcceptanceCriterion(id=i, text=t) for i, t in criteria],
                approach=approach, domains=list(domains))


def _phase_docs():
    """Every shipped phase doc an agent is actually handed: the `/` command templates and the
    rendered SKILL.md bodies. Enumerated from disk, so a doc added later is covered for free."""
    return sorted(_TEMPLATES.glob("*.md")) + sorted(_SKILLS.glob("*/SKILL.md"))


# ======================================================================================
# 1 · THE REGRESSION — before: no MCP surface returned the spec; after: `spec_show` does
# ======================================================================================

class TestHandoffG1Regression(unittest.TestCase):

    def test_handoff_g1_regression(self):
        """The old world and the new one, in one test.

        BEFORE — reconstructed as the registry gap it actually was: of the tools whose name mentions
        the spec, every one was a WRITE (`spec_emit`/`spec_amend`/`spec_check`), and the only READ
        tool that loads the spec — `decompose` — answers a DIFFERENT question. It returns a subtask
        split: it drops the approach, the domains and the declared scope (three of the things
        `review.md`'s brief asks for), and it has no `run` parameter at all, so a coordinator cannot
        ask it for a NAMED run's spec — it reads whatever run the calling PROCESS happens to be. A
        phase that needed the approved spec had nothing to call, which is why review re-searched.

        AFTER — `spec_show` returns the resolving run's own title + acceptance criteria."""
        pre_g1 = [t for t in REG.TOOLS if t.name != "spec_show"]

        # BEFORE (a): no read tool named for the spec existed at all.
        self.assertEqual(
            [t.name for t in pre_g1 if t.kind == "read" and "spec" in t.name], [],
            "the pre-G1 registry had no spec READ tool — that IS the gap G1 closes")
        # BEFORE (b): every spec-named tool was a propose-only write.
        self.assertEqual(
            sorted(t.name for t in pre_g1 if "spec" in t.name),
            ["spec_amend", "spec_check", "spec_emit"])

        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            run = "a" * 32
            _persist_run(d, run)
            _write_spec(d, run, _spec(criteria=(("AC1", "the widget ships"),
                                                ("AC2", "the widget is logged"))))

            # BEFORE (c): the sole MCP reader of the spec answers a different question. Pinned,
            # because `decompose` reads the PROCESS's own session store rather than the resolved
            # run — so even at its most favourable it answers with a split, not the spec.
            import inspect
            self.assertNotIn("run", inspect.signature(decompose).parameters,
                             "decompose cannot be asked for a NAMED run's spec")
            with _pin_session(run):
                split = decompose(path=d)
            self.assertTrue(split.get("available"))
            for missing in ("criteria", "approach", "domains", "scope"):
                self.assertNotIn(missing, split,
                                 f"the split carries no `{missing}` — it is not the spec")

            # AFTER: one keyed read returns the spec the gate passed.
            out = spec_show(path=d)
            self.assertTrue(out["available"])
            self.assertEqual(out["run"], run)
            self.assertEqual(out["title"], "Ship the widget")
            self.assertEqual([c["id"] for c in out["criteria"]], ["AC1", "AC2"])
            self.assertEqual([c["text"] for c in out["criteria"]],
                             ["the widget ships", "the widget is logged"])


# ======================================================================================
# 2 · The tool — the run's OWN spec, and no cross-run leak
# ======================================================================================

class TestSpecShowResolvesTheRightRun(unittest.TestCase):

    def test_returns_the_runs_own_spec(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            run = "b" * 32
            _persist_run(d, run)
            _write_spec(d, run, _spec(title="Only spec", approach="strangler",
                                      domains=("api", "security")))
            out = spec_show(path=d)
            self.assertEqual(out["title"], "Only spec")
            self.assertEqual(out["approach"], "strangler")
            self.assertEqual(out["domains"], ["api", "security"])

    def test_two_runs_on_disk_each_named_run_gets_its_own_spec(self):
        """The cross-run leak this stage must not create: with two runs' specs side by side, an
        EXPLICIT `run` returns that run's spec and never the other's."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            first, second = "c" * 32, "d" * 32
            for r in (first, second):
                _persist_run(d, r)
            _write_spec(d, first, _spec(title="FIRST run spec",
                                        criteria=(("AC1", "first only"),)))
            _write_spec(d, second, _spec(title="SECOND run spec",
                                         criteria=(("AC9", "second only"),)))

            a = spec_show(path=d, run=first)
            b = spec_show(path=d, run=second)
            self.assertEqual(a["title"], "FIRST run spec")
            self.assertEqual(b["title"], "SECOND run spec")
            self.assertEqual([c["id"] for c in a["criteria"]], ["AC1"])
            self.assertEqual([c["id"] for c in b["criteria"]], ["AC9"])
            # neither answer carries a byte of the other run's spec
            self.assertNotIn("SECOND", json.dumps(a))
            self.assertNotIn("second only", json.dumps(a))
            self.assertNotIn("FIRST", json.dumps(b))
            self.assertNotIn("first only", json.dumps(b))

    def test_two_undecidable_runs_refuse_rather_than_guess(self):
        """Un-narrowable ambiguity is the refuse-on-ambiguity precedent `mokata spec emit` set: a
        tool that PICKED one would hand a reviewer a foreign spec — the exact failure G1 closes."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            for r in ("e" * 32, "f" * 32):
                _persist_run(d, r)
                _write_spec(d, r, _spec(title=f"spec for {r[:1]}"))
            with _no_pinned_session():
                out = spec_show(path=d)
            self.assertFalse(out["available"])
            self.assertEqual(out["reason"], "ambiguous-run")
            self.assertIsNone(out["run"])
            self.assertIn("will not guess", out["note"])
            # the refusal names neither run's spec content
            self.assertNotIn("spec for", json.dumps(out))

    def test_run_resolution_is_the_cli_path_not_a_third_one(self):
        """`spec_show` and `mokata spec show` must resolve THE SAME run — proven by making the one
        shared seam raise: if the tool had its own resolver it would sail past this."""
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            run = "1" * 32
            _persist_run(d, run)
            _write_spec(d, run, _spec())
            sentinel = RuntimeError("the shared _run_scoped_store seam was called")
            with mock.patch("mokata.cli_commands.spec._run_scoped_store",
                            side_effect=sentinel):
                with self.assertRaises(RuntimeError) as ctx:
                    spec_show(path=d)
            self.assertIs(ctx.exception, sentinel)


# ======================================================================================
# 3 · Degrade-clean — absent vs malformed vs no-run, never an exception, never a leak
# ======================================================================================

class TestSpecShowDegradesClean(unittest.TestCase):

    def test_no_tracked_run_gives_the_cli_recovery_line(self):
        from mokata.cli_commands.spec import NO_TRACKED_RUN_RECOVERY
        with tempfile.TemporaryDirectory() as d:
            _repo(d)                                       # no run registered at all
            with _no_pinned_session():
                out = spec_show(path=d)
            self.assertFalse(out["available"])
            self.assertEqual(out["reason"], "no-run")
            self.assertEqual(out["note"], NO_TRACKED_RUN_RECOVERY)
            self.assertIn("/mokata:brainstorm", out["note"])   # how to START a tracked run
            self.assertEqual(out["criteria"], [])

    def test_run_with_no_spec_gives_the_cli_recovery_line(self):
        from mokata.cli_commands.spec import NO_SPEC_RECOVERY
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            run = "2" * 32
            _persist_run(d, run)                           # a run, but nothing emitted
            out = spec_show(path=d)
            self.assertFalse(out["available"])
            self.assertEqual(out["reason"], "no-spec")
            self.assertEqual(out["run"], run)
            self.assertEqual(out["note"], NO_SPEC_RECOVERY)
            self.assertIn("/mokata:spec", out["note"])        # how to EMIT one
            self.assertEqual(out["criteria"], [])

    def test_present_but_malformed_is_reported_as_such_not_as_absent(self):
        """D5's distinction on this surface: "no spec" sends the user to write one they already
        have, and leaves the real fault (a torn write) uninvestigated."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            run = "3" * 32
            _persist_run(d, run)
            _write_raw(d, run, {"title": "half-written", "criteria": [{"text": "no id"}]})
            out = spec_show(path=d)
            self.assertFalse(out["available"])
            self.assertEqual(out["reason"], "malformed")
            self.assertEqual(out["run"], run)
            self.assertIn("could not be read", out["note"])
            self.assertIn("NOT 'no spec'", out["note"])

    def test_no_degrade_path_raises(self):
        """Every empty/broken state answers; none throws. A reviewer's brief must degrade to "no
        spec — review on quality alone", never to a tool error the coordinator has to interpret."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            run = "4" * 32
            with _no_pinned_session():
                self.assertFalse(spec_show(path=d)["available"])          # no run
            _persist_run(d, run)
            self.assertFalse(spec_show(path=d)["available"])              # no spec
            _write_raw(d, run, ["not", "a", "dict"])
            self.assertFalse(spec_show(path=d)["available"])              # malformed
            _write_raw(d, run, {})
            self.assertFalse(spec_show(path=d)["available"])              # empty payload

    def test_degrade_answers_leak_no_spec_content(self):
        """Secret-safety: a spec can carry project content (paths, symbols, domain wording). The
        no-spec / malformed / ambiguous answers must be pure GUIDANCE — the only spec-derived field
        they may carry is the run id, which names nothing about the work."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            run = "5" * 32
            _persist_run(d, run)
            secret = "ACME-INTERNAL-PROJECT-CODENAME"
            _write_raw(d, run, {"title": secret, "criteria": [{"text": secret}]})
            out = spec_show(path=d)
            self.assertEqual(out["reason"], "malformed")
            self.assertNotIn(secret, json.dumps(out))


# ======================================================================================
# 4 · Payload + response_format — what a coordinator hands the reviewer
# ======================================================================================

class TestSpecShowPayload(unittest.TestCase):

    def test_payload_is_what_cmd_spec_show_renders(self):
        """The stage's contract: the tool's payload IS what `cmd_spec_show` prints — title,
        approach, domains, every AC — so the brief and the terminal agree."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            run = "6" * 32
            _persist_run(d, run)
            _write_spec(d, run, _spec(title="T", approach="A", domains=("api", "security"),
                                      criteria=(("AC1", "one"), ("AC2", "two"))))
            out = spec_show(path=d)
            self.assertEqual(
                {k: out[k] for k in ("title", "approach", "domains", "criteria")},
                {"title": "T", "approach": "A", "domains": ["api", "security"],
                 "criteria": [{"id": "AC1", "text": "one"}, {"id": "AC2", "text": "two"}]})

    def test_declared_scope_rides_along_when_set_and_is_absent_when_not(self):
        from mokata.spec_scope import DeferredItem, SpecScope
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            run = "7" * 32
            _persist_run(d, run)
            self.assertNotIn("scope", spec_show(path=d, run=run) or {})   # (no spec yet)
            spec = _spec()
            spec.scope = SpecScope(
                authorized=("src/api/*.py",),
                deferred=(DeferredItem(id="D1", item="batch delete",
                                       paths=("src/api/batch*.py",),
                                       markers=("bulk_delete",)),))
            _write_spec(d, run, spec)
            out = spec_show(path=d)
            self.assertIn("scope", out)
            self.assertEqual(out["scope"]["authorized"], ["src/api/*.py"])

            scopeless = _spec()
            _write_spec(d, run, scopeless)
            self.assertNotIn("scope", spec_show(path=d))

    def test_concise_drops_the_render_and_detailed_realizes_the_cli_lines(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            run = "8" * 32
            _persist_run(d, run)
            _write_spec(d, run, _spec(title="T", approach="A", domains=("api",),
                                      criteria=(("AC1", "one"),)))
            self.assertNotIn("block", spec_show(path=d))                    # concise default
            block = spec_show(path=d, response_format="detailed")["block"]
            self.assertEqual(block.splitlines(),
                             ["spec: T", "approach: A", "domains: api", "  AC1: one"])

    def test_detailed_block_matches_the_cli_render_line_for_line(self):
        """One spec, two surfaces: the `detailed` block is the same text `mokata spec show` prints
        (minus nothing) — so a human and a model read the identical spec."""
        from mokata.cli_commands.spec import cmd_spec_show
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            run = "9" * 32
            _persist_run(d, run)
            _write_spec(d, run, _spec(title="Two surfaces", approach="A",
                                      domains=("api", "git"),
                                      criteria=(("AC1", "one"), ("AC2", "two"))))
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                cmd_spec_show(types.SimpleNamespace(path=d))
            cli = buf.getvalue().rstrip("\n")
            self.assertEqual(spec_show(path=d, response_format="detailed")["block"], cli)


# ======================================================================================
# 5 · Registry, annotations, parity
# ======================================================================================

class TestSpecShowIsRegisteredCorrectly(unittest.TestCase):

    def test_registered_as_a_read_tool(self):
        self.assertIn("spec_show", REG.read_tool_names())
        self.assertNotIn("spec_show", REG.write_tool_names())

    def test_annotated_read_only_and_not_open_world(self):
        """Grounded: the body is a local state-dir resolve + one local JSON read. Unlike its
        neighbour `decompose` it builds no KnowledgeLayer, so the CRG subprocess never spawns."""
        ann = TA.annotations_for("read", "spec_show")
        self.assertIs(ann["readOnlyHint"], True)
        self.assertIs(ann["openWorldHint"], False)
        self.assertNotIn("spec_show", TA.OPEN_WORLD_TOOLS)

    def test_parity_matrix_includes_spec_show_on_the_spec_surface(self):
        surf = parity.SURFACE_MATRIX["spec"]
        self.assertIn("spec_show", surf.mcp_read)
        self.assertIn("spec_show", surf.mcp)
        # and the matrix's own rule holds: a declared read tool is registered read
        self.assertIn("spec_show", REG.read_tool_names())

    def test_no_consent_surface_on_this_read_tool(self):
        """doc 85 §3: `approve` is the MCP WRITE consent boolean. A read tool has no consent flow
        and must not grow one — pinned so a later edit can't quietly make the spec read gated."""
        import inspect
        params = inspect.signature(spec_show).parameters
        self.assertEqual(list(params), ["path", "run", "response_format"])
        for banned in ("approve", "confirm", "proposal_id"):
            self.assertNotIn(banned, params)


# ======================================================================================
# 6 · The phase docs point at it (and stop pointing at a file that does not exist)
# ======================================================================================

class TestPhaseDocsPointAtSpecShow(unittest.TestCase):

    def test_no_phase_doc_names_emitted_spec_json(self):
        """The fold-in bug: `emitted_spec.json` is not the artifact. The real one is
        `emitted_spec__<run_id>.json`, and no phase doc may send an agent looking for the bare
        name — grep-tested across every shipped command template and SKILL.md."""
        offenders = [str(p) for p in _phase_docs()
                     if "emitted_spec.json" in p.read_text(encoding="utf-8")]
        self.assertEqual(offenders, [])

    def test_review_brief_names_spec_show_as_the_fetch_instrument(self):
        text = (_TEMPLATES / "review.md").read_text(encoding="utf-8")
        self.assertIn("SELF-CONTAINED brief", text)
        self.assertIn("`spec_show`", text)
        self.assertIn("VERBATIM", text)
        self.assertIn("conversation memory", text)     # names the failure it replaces

    def test_develop_and_test_and_ship_name_the_instrument(self):
        for name in ("develop", "test", "ship"):
            with self.subTest(doc=name):
                text = (_TEMPLATES / f"{name}.md").read_text(encoding="utf-8")
                self.assertIn("spec_show", text)

    def test_g2_no_phase_doc_uses_spec_check_as_a_spec_fetch(self):
        """G2: `spec_check` is the regression guard over the SHARED corpus, not a spec fetch. Every
        surviving mention must sit in its regression-guard clause — and review.md, whose brief is
        the surface that was mis-served, says so explicitly."""
        for p in _phase_docs():
            text = p.read_text(encoding="utf-8")
            for line in text.splitlines():
                if "spec_check" not in line:
                    continue
                with self.subTest(doc=p.name):
                    self.assertTrue(
                        "regression guard" in line or "NOT a spec fetch" in line,
                        f"{p}: `spec_check` mentioned outside its regression-guard clause")
        review = (_TEMPLATES / "review.md").read_text(encoding="utf-8")
        self.assertIn("`spec_check` is NOT a spec fetch", review)

    def test_shipped_skill_md_mirrors_are_regenerated(self):
        """The single-source chain: skills.py -> templates/commands/<n>.md -> skills/<n>/SKILL.md.
        A stage that edits the source but ships a stale mirror hands the agent the OLD instruction."""
        from mokata.agent_skills import CURATED_SKILLS, skill_markdown
        from mokata.skills import SKILL_NAMES, command_markdown, get_skill
        for name in ("review", "develop", "test", "ship"):
            with self.subTest(skill=name):
                self.assertIn(name, SKILL_NAMES)
                self.assertEqual((_TEMPLATES / f"{name}.md").read_text(encoding="utf-8"),
                                 command_markdown(get_skill(name)))
        for name in CURATED_SKILLS:
            p = _SKILLS / name / "SKILL.md"
            if not p.is_file():
                continue
            with self.subTest(skill_md=name):
                self.assertEqual(p.read_text(encoding="utf-8"),
                                 skill_markdown(name, _TEMPLATES))


# ======================================================================================
# 7 · NEGATIVES — what this stage must not have moved
# ======================================================================================

class TestNegativesUnchanged(unittest.TestCase):

    def test_cli_spec_show_output_is_byte_identical(self):
        """`mokata spec show` is untouched: the three outputs a human can see, byte for byte
        against the strings the pre-stage code printed (re-spelled here, not imported, so a change
        to the constants cannot silently move the assertion with it)."""
        from mokata.cli_commands.spec import cmd_spec_show

        def run(d):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cmd_spec_show(types.SimpleNamespace(path=d))
            return rc, buf.getvalue()

        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            with _no_pinned_session():
                rc, out = run(d)
            self.assertEqual(rc, 0)
            self.assertEqual(out, (
                "no tracked run in this repo — mokata has nothing to attach a spec to yet. Start a "
                "tracked run with /mokata:brainstorm (it registers the run) or resume one with "
                "/mokata:resume, then emit the spec (/mokata:spec).\n"))

            run_id = "a1" * 16
            _persist_run(d, run_id)
            rc, out = run(d)
            self.assertEqual(rc, 0)
            self.assertEqual(out, (
                "no spec is emitted for this run — draft one and emit it (/mokata:spec, or "
                "`mokata spec emit --file <spec.json>`).\n"))

            _write_spec(d, run_id, _spec(title="T", approach="A", domains=("api", "git"),
                                         criteria=(("AC1", "one"), ("AC2", "two"))))
            rc, out = run(d)
            self.assertEqual(rc, 0)
            self.assertEqual(out, "spec: T\napproach: A\ndomains: api, git\n"
                                  "  AC1: one\n  AC2: two\n")

    def test_run_scoped_store_default_is_unchanged_for_every_cli_caller(self):
        """The seam gained an OPTIONAL `run_id`; no CLI surface passes it, and omitted it resolves
        exactly as before."""
        import inspect
        from mokata.cli_commands import spec as CLI
        sig = inspect.signature(CLI._run_scoped_store)
        self.assertEqual(list(sig.parameters), ["surface", "run_id"])
        self.assertIsNone(sig.parameters["run_id"].default)
        src = inspect.getsource(CLI)
        self.assertEqual(src.count("_run_scoped_store(surface)"), 3,
                         "a CLI caller started passing an explicit run — CLI keying moved")

    def test_decompose_is_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            run = "b1" * 16
            _persist_run(d, run)
            _write_spec(d, run, _spec(criteria=(("AC1", "one"), ("AC2", "two"))))
            with _pin_session(run):
                out = decompose(path=d)
            self.assertTrue(out["available"])
            self.assertEqual(len(out["subtasks"]), 2)      # one subtask per AC, as before
            for spec in REG.TOOLS:
                if spec.name == "decompose":
                    self.assertEqual(spec.kind, "read")

    def test_spec_check_is_still_the_corpus_regression_guard(self):
        """`spec_check` stays a WRITE-kind corpus scan with its deviation-gate consent surface —
        this stage did not repurpose it and did not weaken it."""
        import inspect
        from mokata.mcp.tools_spec import spec_check
        self.assertIn("spec_check", REG.write_tool_names())
        params = inspect.signature(spec_check).parameters
        for expected in ("symbols", "files", "text", "phase", "approve", "proposal_id"):
            self.assertIn(expected, params)
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            out = spec_check(path=d, symbols="nothing_here")
            self.assertEqual(out["status"], "skipped")     # empty corpus -> no false alarm

    def test_storage_key_and_prefix_are_untouched(self):
        """`emitted_spec__` is load-bearing: `gate_hook._run_ids` enumerates runs by this prefix, so
        moving it would make every write ambiguous and silently switch the gates off."""
        from mokata import gate_hook
        from mokata.session_state import SESSION_SCOPED_KEYS
        self.assertEqual(SPEC_STATE_KEY, "emitted_spec")
        self.assertIn("emitted_spec", SESSION_SCOPED_KEYS)
        self.assertEqual(gate_hook.SPEC_PREFIX, "emitted_spec__")

    def test_spec_show_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            run = "c1" * 16
            _persist_run(d, run)
            _write_spec(d, run, _spec())
            before = sorted(os.listdir(state_dir(d)))
            stamps = {n: os.stat(os.path.join(state_dir(d), n)).st_mtime for n in before}
            spec_show(path=d)
            spec_show(path=d, response_format="detailed")
            self.assertEqual(sorted(os.listdir(state_dir(d))), before)
            for n in before:
                self.assertEqual(os.stat(os.path.join(state_dir(d), n)).st_mtime, stamps[n])


# --------------------------------------------------------------------------- helpers
@contextlib.contextmanager
def _pin_session(run_id):
    """Run as if this PROCESS were `run_id` (the harness pin `MOKATA_SESSION_ID`).

    Needed for the `decompose` fixtures: `decompose` reads `surface.state`, which is scoped to the
    process's OWN `current_run_id()` — it does not use the gate-resolved run at all. Pinning is how
    a test makes that store address the run whose spec is on disk. (`spec_show` deliberately does
    NOT rely on this: it resolves the run the gates enforce.)"""
    from unittest import mock

    from mokata import session
    with mock.patch.dict(os.environ, {"MOKATA_SESSION_ID": run_id}, clear=False):
        session.reset_for_test()
        try:
            yield
        finally:
            session.reset_for_test()


@contextlib.contextmanager
def _no_pinned_session():
    """Run with `MOKATA_SESSION_ID` unset. `gate_hook.resolve_run` short-circuits on a pinned id,
    so a pinned env (a real harness, or another test) would mask the on-disk resolution these cases
    are about."""
    from unittest import mock
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("MOKATA_SESSION_ID", None)
        yield


if __name__ == "__main__":
    unittest.main()
